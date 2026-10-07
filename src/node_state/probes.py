"""Start-time estimates from `sbatch`/`srun --test-only` probes.

A `Grid` holds one probe per request size (CPU only, then 1..N GPUs of each
type) and walltime. Walltimes default to the distinct partition time limits,
which is where routing tiers change, and requests stop at the tightest GPU cap
in the account/QOS limits. No probe ever starts a job.
"""

import dataclasses
import datetime
import re

from . import slurm, snapshot

# Probed when no partition sets a time limit and the profile names none.
DEFAULT_WALLTIMES = (60, 1440)
MAX_DERIVED_WALLTIMES = 6

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# Noise every site's replies share; profiles add their own submit-filter text.
GENERIC_NOISE = (
    r"\b(?:sbatch|srun): (?:error: )?",
    r"[-=]{8,}",
    r"allocation failure: Unspecified error",
)


@dataclasses.dataclass(frozen=True)
class Request:
    gpu: str | None = None
    count: int = 0
    # How to show the GPU type; the full type still goes into --gres.
    name: str | None = dataclasses.field(default=None, compare=False)

    @property
    def label(self):
        return f"{self.count}x {self.name or self.gpu}" if self.gpu else "CPU only"

    def directives(self):
        return [f"--gres=gpu:{self.gpu}:{self.count}"] if self.gpu else []


@dataclasses.dataclass(frozen=True)
class Settings:
    """What every probe asks for besides its GPUs, walltime and scope."""

    command: str = "sbatch"
    cpus: int = 4
    mem: str = "32G"
    # Extra options for every probe, e.g. ("--partition=x", "--constraint=y").
    extra: tuple[str, ...] = ()
    # Overrides the profile's or derived walltimes, in minutes.
    walltimes: tuple[int, ...] = ()


@dataclasses.dataclass(frozen=True)
class Result:
    start: datetime.datetime | None
    partition: str | None
    message: str
    command: str

    @property
    def ok(self):
        return self.start is not None


@dataclasses.dataclass
class Grid:
    scope: snapshot.Scope
    settings: Settings
    requests: list
    walltimes: list
    # Walltimes above this cap (minutes) are not probed.
    max_wall: int | None = None
    # The cluster user's login shell, for `srun --pty` run commands.
    shell: str = "bash"
    results: dict = dataclasses.field(default_factory=dict)

    def skipped(self, column):
        return self.max_wall is not None and self.walltimes[column] > self.max_wall

    def cells(self):
        return [
            (row, column)
            for row in range(len(self.requests))
            for column in range(len(self.walltimes))
            if not self.skipped(column)
        ]

    def directives(self, row, column):
        settings = self.settings
        return [
            "--test-only",
            *self.scope.directives(),
            *self.requests[row].directives(),
            # -c/-t are --cpus-per-task/--time, kept short for copying.
            f"-c{settings.cpus}",
            f"--mem={settings.mem}",
            f"-t{slurm.format_duration(self.walltimes[column])}",
            *settings.extra,
        ]


def parse_walltime(text):
    """Minutes in a compact (`90m`, `3h`, `1d12h`) or Slurm time string."""
    text = text.strip().lower()
    match = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", text)
    if text and match:
        days, hours, minutes = (int(group or 0) for group in match.groups())
        return days * 1440 + hours * 60 + minutes or None
    return slurm.parse_duration(text)


def parse_walltimes(text):
    """Sorted distinct minutes from a comma- or space-separated list."""
    values = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        minutes = parse_walltime(part)
        if not minutes:
            raise ValueError(f"invalid walltime {part!r}")
        values.add(minutes)
    return tuple(sorted(values))


def format_wait(start, now):
    """How long until `start`: "now" within a minute, else `D-HH:MM:SS`.

    Rounded up to the minute, so the wait never reads shorter than it is.
    """
    seconds = (start - now).total_seconds()
    if seconds <= 60:
        return "now"
    return slurm.format_duration(int(-(-seconds // 60)))


def counts_for(ceiling, capacity, mode):
    """GPU counts worth probing: powers of two to the ceiling, all, or whole node."""
    if mode == "whole_node":
        return [capacity]
    if mode == "all":
        return list(range(1, ceiling + 1))
    counts = []
    count = 1
    while count < ceiling:
        counts.append(count)
        count *= 2
    return [*counts, ceiling]


def max_gpu_counts(capacities, limits):
    """Return the largest count of each GPU type worth probing in one job.

    The tightest cap on the model or on all GPUs combined, at any scope (per
    job, user, account or node), bounds the count along with one node's
    capacity. A zero cap still probes one GPU to surface the reason.
    """
    counts = {}
    for gpu, capacity in capacities.items():
        caps = [
            cap
            for key in (f"tres:gres/gpu:{gpu}", "tres:gres/gpu")
            if (cap := snapshot.tightest(limits, key)) is not None
        ]
        counts[gpu] = max(1, min([capacity, *caps]))
    return counts


def plan_requests(capacities, ceilings, mode="pow2", names=None):
    """Requests for each GPU type up to its ceiling, then CPU only."""
    names = names or {}
    requests = [
        Request(gpu, count, names.get(gpu))
        for gpu, capacity in capacities.items()
        for count in counts_for(ceilings.get(gpu, capacity), capacity, mode)
    ]
    requests.append(Request())
    return requests


def plan_walltimes(partitions, profile, override=()):
    """Walltimes to probe: the override, the profile's, or partition tiers.

    Distinct time limits of the partitions the caller can use mark where
    routing changes; more than `MAX_DERIVED_WALLTIMES` are thinned evenly,
    keeping the shortest and longest. Preemptible partitions (such as Fir's
    122-day `cpupreempt`) are not routing tiers, so they are left out.
    """
    if override:
        return sorted(set(override))
    if profile.walltimes:
        return list(profile.walltimes)
    tiers = sorted(
        {
            partition["max_time"]
            for partition in partitions
            if partition["max_time"]
            and partition.get("usable", True)
            and partition.get("preempt_mode", "OFF") == "OFF"
        }
    )
    if not tiers:
        return list(DEFAULT_WALLTIMES)
    if len(tiers) <= MAX_DERIVED_WALLTIMES:
        return tiers
    last = len(tiers) - 1
    picks = {
        round(index * last / (MAX_DERIVED_WALLTIMES - 1))
        for index in range(MAX_DERIVED_WALLTIMES)
    }
    return [tiers[index] for index in sorted(picks)]


def usable_nodes(snap):
    """Nodes in partitions the caller can use, or every node if none are known."""
    usable = {partition["name"] for partition in snap.partitions if partition["usable"]}
    nodes = [node for node in snap.nodes if usable.intersection(node["partitions"])]
    return nodes or snap.nodes


def build_grid(snap, scope, settings, profile, info):
    """Size the grid for `scope` on the cluster `info` describes."""
    limits = snap.limits(scope, info.user)
    capacities = snapshot.gpu_capacities(usable_nodes(snap))
    usable = [partition for partition in snap.partitions if partition["usable"]]
    return Grid(
        scope=scope,
        settings=settings,
        requests=plan_requests(
            capacities,
            max_gpu_counts(capacities, limits),
            profile.gpu_counts,
            snap.gpu_names,
        ),
        walltimes=plan_walltimes(
            usable or snap.partitions, profile, settings.walltimes
        ),
        max_wall=snapshot.tightest(limits, "wall"),
        shell=info.shell,
    )


def clean_message(text, noise=()):
    """Strip colour codes, command prefixes, banners and site noise."""
    text = ANSI_ESCAPE.sub("", text)
    for pattern in (*noise, *GENERIC_NOISE):
        text = re.sub(pattern, " ", text)
    return " ".join(text.split()).strip(" .") or "rejected without a reason"


def make_result(grid, row, column, returncode, output, profile):
    """Turn one probe's reply into a `Result` with its copyable command."""
    command = grid.settings.command
    raw = slurm.parse_test_output(output, returncode, command)
    start = raw["start_time"]
    return Result(
        start=datetime.datetime.fromisoformat(start) if start else None,
        partition=raw["partition"],
        message="" if start else clean_message(raw["result"], profile.message_noise),
        command=slurm.format_run_command(
            command, grid.directives(row, column), grid.shell
        ),
    )


def run_grid(grid, profile, host, on_result=None, should_stop=lambda: False):
    """Run every probe in `grid` on `host`, reporting each result as it lands.

    `should_stop` is polled between results; once it returns True, probes not
    yet started are dropped. Raises `hosts.ConnectionLost` if ssh fails.
    """
    cells = grid.cells()
    commands = [
        slurm.test_command(
            grid.settings.command, grid.directives(row, column), profile.srun_program
        )
        for row, column in cells
    ]

    def landed(index, returncode, output):
        row, column = cells[index]
        result = make_result(grid, row, column, returncode, output, profile)
        grid.results[row, column] = result
        if on_result is not None:
            on_result(row, column, result)

    host.run_stream(commands, landed, should_stop)
    return grid
