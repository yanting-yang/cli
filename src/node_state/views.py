"""Plain-text cells shared by the TUI and the text report."""

import datetime

from . import probes, slurm, snapshot

NO_LIMIT = "∞"
UNKNOWN = "?"


def fmt_value(value, key=""):
    """Format a cap or usage: ∞ for no cap, ? when unknown, GB for memory."""
    if value is None:
        return NO_LIMIT
    if value == UNKNOWN:
        return UNKNOWN
    if key == "wall":
        return slurm.format_duration(value)
    if key == "tres:mem":
        return f"{value / 1024:g}G"
    return str(value)


def limit_rows(limits):
    """(Scope, Limit, Max, Used) rows; usage is "-" where it does not apply."""
    return [
        (
            limit.scope,
            limit.name,
            fmt_value(limit.max, limit.key),
            "-" if limit.used is None else fmt_value(limit.used, limit.key),
        )
        for limit in limits
    ]


def scope_summary(snap, scope, user):
    """One row of the accounts table: tightest job, GPU and wall caps."""
    limits = snap.limits(scope, user)
    counts = {} if snap.jobs is None else slurm.job_counts(snap.jobs, scope.qos)
    running = counts.get("running", UNKNOWN)
    pending = counts.get("pending", UNKNOWN)
    caps = [limit.max for limit in limits if limit.key == "jobs" and limit.per_user]
    numeric = [cap for cap in caps if isinstance(cap, int)]
    if numeric:
        max_jobs = min(numeric)
    else:
        max_jobs = UNKNOWN if UNKNOWN in caps else None
    share = snapshot.fairshare(snap.associations, scope.account, user)
    return {
        "account": f"{scope.account or '-'}{'*' if scope.default_account else ''}",
        "qos": f"{scope.qos or '-'}{'*' if scope.default_qos else ''}",
        "jobs": f"{running}/{fmt_value(max_jobs)}",
        "pending": str(pending),
        "gpus": fmt_value(snapshot.tightest(limits, "tres:gres/gpu")),
        "wall": fmt_value(snapshot.tightest(limits, "wall"), "wall"),
        "fairshare": "-" if share is None else f"{share:.2f}",
    }


def main_resource(summary):
    """The resource that defines a partition's load: its GPUs, else its CPUs."""
    if summary["gpus"]:
        allocated = sum(gpus["allocated"] for gpus in summary["gpus"].values())
        free = sum(gpus["free"] for gpus in summary["gpus"].values())
        total = sum(gpus["total"] for gpus in summary["gpus"].values())
        return allocated, free, total
    cpus = summary["cpus"]
    return cpus["allocated"], cpus["free"], cpus["total"]


def free_resources(summary, names=None):
    """Free/total GPUs per type (in total beyond two types), or CPUs."""
    names = names or {}
    gpus = summary["gpus"]
    if len(gpus) > 2:
        free = sum(counts["free"] for counts in gpus.values())
        total = sum(counts["total"] for counts in gpus.values())
        return f"{free}/{total} GPUs ({len(gpus)} types)"
    if gpus:
        return ", ".join(
            f"{counts['free']}/{counts['total']} {names.get(gpu, gpu)}"
            for gpu, counts in gpus.items()
        )
    return f"{summary['cpus']['free']}/{summary['cpus']['total']} CPUs"


def idle_nodes(summary):
    return f"{summary['states'].get('idle', 0)}/{summary['node_count']}"


def max_time(summary):
    return (
        NO_LIMIT
        if summary["max_time"] is None
        else slurm.format_duration(summary["max_time"])
    )


def load_percent(summary):
    allocated, _, total = main_resource(summary)
    return round(100 * allocated / total) if total else 0


def state_counts(states):
    order = ("idle", "mixed", "allocated", "unavailable")
    return ", ".join(f"{states[name]} {name}" for name in order if states.get(name))


def gb(megabytes):
    return f"{megabytes // 1024}"


SHORT_STATES = {
    "RUNNING": "R",
    "PENDING": "PD",
    "COMPLETING": "CG",
    "CONFIGURING": "CF",
    "SUSPENDED": "S",
    "REQUEUED": "RQ",
}


def short_state(state):
    """squeue's compact state code (R, PD, CG...) for a long state name."""
    return SHORT_STATES.get(state, state[:2])


def job_start(job, now):
    """When a job started or is expected to, relative to now where known."""
    start = parse_time(job["start"])
    if job["state"] == "RUNNING":
        return job["nodelist"] or "-"
    if start is None:
        return f"({job['reason']})"
    when = "now" if start <= now else f"in {probes.format_wait(start, now)}"
    return f"{when} ({job['reason']})"


def parse_time(text):
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def job_row(job, now):
    time = job["elapsed"] if job["state"] != "PENDING" else "0"
    return (
        job["id"],
        job["name"],
        job["state"],
        job["partition"],
        f"{slurm.normalize_time(time)}/{slurm.normalize_time(job['time_limit'])}",
        job_start(job, now),
    )


def cell(grid, row, column, now):
    """Text and kind for one probe cell.

    Kinds: pending (not probed yet), skipped (over the wall-time cap), blocked,
    or the wait bucket: now, hours (< 12h), days (< 3d), weeks.
    """
    if grid.skipped(column):
        return "-", "skipped"
    result = grid.results.get((row, column))
    if result is None:
        return "…", "pending"
    if not result.ok:
        return "no", "blocked"
    wait = probes.format_wait(result.start, now)
    seconds = (result.start - now).total_seconds()
    if wait == "now":
        return wait, "now"
    if seconds < 12 * 3600:
        return wait, "hours"
    if seconds < 3 * 86400:
        return wait, "days"
    return wait, "weeks"


def earliest_columns(grid, row):
    """Columns holding the row's earliest estimated start, if starts differ."""
    starts = {
        column: result.start
        for (result_row, column), result in grid.results.items()
        if result_row == row and result.ok
    }
    if len(set(starts.values())) < 2:
        return set()
    earliest = min(starts.values())
    return {column for column, start in starts.items() if start == earliest}
