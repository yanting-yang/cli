"""Reporter for the `tamia` cluster.

Tamia is an Alliance cluster like Killarney and reuses its partition, account
limit and probe-report output. Its probes differ: GPUs are only allocated by
whole node, walltime is capped at one day, and srun must name a program.
"""

import re

from . import common, killarney

CLUSTER = "tamia"

# Falls back to CPUTot where no cores are reserved, so this is safe either way.
CPU_KEY = "cpu_efctv"

# The b1/b2/b3 partition tiers; the submit filter rejects anything over a day.
PROBE_TIMES = ("0-03:00:00", "0-12:00:00", "1-00:00:00")
# The *_interac partitions allow up to 6 hours.
INTERACTIVE_TIME = "0-03:00:00"
# The submit filter only routes srun to *_interac when a program is named;
# without one the request fails with "No partition specified".
SRUN_COMMAND = ("bash",)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
MEMORY_NOTE = re.compile(r"NOTE: Your memory request .*? not 1000M\.\s*")


def build_requests(gpu_capacities):
    """One whole node per GPU type, since tamia rejects partial GPU nodes."""
    requests = [
        (gpu, capacity, f"{capacity}x {gpu} (whole node)")
        for gpu, capacity in sorted(gpu_capacities.items())
    ]
    requests.append((None, 1, "CPU only (no GPU)"))
    return requests


def run_probes(gpu_capacities, *, cpus_per_task, mem):
    requests = build_requests(gpu_capacities)
    probes = [
        ("sbatch", request, time_limit)
        for request in requests
        for time_limit in PROBE_TIMES
    ]
    probes += [("srun", request, INTERACTIVE_TIME) for request in requests]

    results = []
    for command, (gpu, count, label), time_limit in probes:
        directives = killarney.build_directives(
            gpu,
            count,
            time_limit,
            cpus_per_task=cpus_per_task,
            mem=mem,
        )
        if command == "sbatch":
            result = common.run_sbatch_test(directives)
        else:
            result = common.run_srun_test(directives, command=SRUN_COMMAND)
        results.append(
            {
                **result,
                "label": label,
                "time": time_limit,
                "command": command,
                "run_command": common.format_run_command(command, directives),
            }
        )
    return results


def clean_result(text):
    """Strip the submit filter's colour codes, memory-unit note and banners."""
    text = MEMORY_NOTE.sub("", ANSI_ESCAPE.sub("", text))
    text = re.sub(r"\b(?:sbatch|srun): (?:error: )?", "", text)
    text = re.sub(r"-{8,}", "", text)
    text = text.replace("allocation failure: Unspecified error", "")
    return " ".join(text.split()).strip(" .")


def main(args):
    nodes = common.fetch_nodes()
    if nodes is None:
        return

    # Only drops an untyped gres/gpu total when per-model counts are present.
    nodes = [common.normalize_gpu_rollups(node) for node in nodes]
    hardware = common.group_by_hardware(nodes, CPU_KEY)

    for key in sorted(hardware):
        _, _, gpu_frozenset = key
        common.print_summary(
            common.hw_key_label(key),
            hardware[key],
            len(hardware[key]),
            sorted(dict(gpu_frozenset)),
            cpu_key=CPU_KEY,
        )

    killarney.print_partition_table()
    killarney.print_account_limits(common.current_user(), CLUSTER)

    gpu_types = sorted({gpu for node in nodes for gpu in node["cfg_gpus"]})
    gpu_capacities = {
        gpu: max(node["cfg_gpus"].get(gpu, 0) for node in nodes) for gpu in gpu_types
    }
    results = run_probes(
        gpu_capacities,
        cpus_per_task=args.cpus_per_task,
        mem=args.mem,
    )
    killarney.print_probe_results(
        results, sort_by_start=args.sort_by_start, clean=clean_result
    )
