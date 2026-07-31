"""Reporter for the `killarney` cluster."""

from . import common

PROBE_TIMES = (
    "3:00:00",
    "12:00:00",
    "1-00:00:00",
    "3-00:00:00",
    "7-00:00:00",
)
INTERACTIVE_GPU = "l40s"
INTERACTIVE_GPU_COUNTS = range(1, 5)
INTERACTIVE_TIME = "3:00:00"


def build_directives(
    gpu=None,
    gpu_count=1,
    time_limit=PROBE_TIMES[0],
    *,
    cpus_per_task,
    mem,
):
    directives = ["--test-only"]
    if gpu:
        directives.append(f"--gres=gpu:{gpu}:{gpu_count}")
    directives += [
        f"--cpus-per-task={cpus_per_task}",
        f"--mem={mem}",
        f"--time={time_limit}",
    ]
    return directives


def clean_result(text):
    for prefix in ("sbatch: error: ", "srun: error: ", "sbatch: ", "srun: "):
        text = text.removeprefix(prefix)
    return text.strip(" .")


def run_probes(
    gpu_capacities,
    *,
    cpus_per_task,
    mem,
):
    requests = [
        (gpu, count, f"{count}x {gpu}")
        for gpu, capacity in sorted(gpu_capacities.items())
        for count in range(1, capacity + 1)
    ]
    requests.append((None, 1, "CPU only (no GPU)"))

    results = []
    for gpu, count, label in requests:
        for time_limit in PROBE_TIMES:
            result = common.run_sbatch_test(
                build_directives(
                    gpu,
                    count,
                    time_limit,
                    cpus_per_task=cpus_per_task,
                    mem=mem,
                )
            )
            results.append(
                {
                    **result,
                    "label": label,
                    "time": time_limit,
                    "command": "sbatch",
                }
            )

    interactive_requests = []
    if INTERACTIVE_GPU in gpu_capacities:
        interactive_requests = [
            (INTERACTIVE_GPU, count, f"{count}x {INTERACTIVE_GPU}")
            for count in INTERACTIVE_GPU_COUNTS
        ]
    interactive_requests.append((None, 1, "CPU only (no GPU)"))

    for gpu, count, label in interactive_requests:
        result = common.run_srun_test(
            build_directives(
                gpu,
                count,
                INTERACTIVE_TIME,
                cpus_per_task=cpus_per_task,
                mem=mem,
            )
        )
        results.append(
            {
                **result,
                "label": label,
                "time": INTERACTIVE_TIME,
                "command": "srun",
            }
        )
    return results


def print_probe_results(
    results,
    *,
    cpus_per_task,
    mem,
    sort_by_start=False,
):
    runnable = sum(result["start_time"] is not None for result in results)
    print(f"Job feasibility (--test-only, {cpus_per_task} CPUs, " f"{mem}):")
    print(f"Runnable: {runnable}/{len(results)}")

    displayed_results = results
    if sort_by_start:
        displayed_results = sorted(
            results,
            key=lambda result: (
                result["start_time"] is None,
                result["start_time"] or "",
            ),
        )

    rows = [
        (
            result["label"],
            result["time"],
            result["command"],
            "yes" if result["start_time"] else "no",
            result["start_time"] or "-",
            result["partition"] or "-",
        )
        for result in displayed_results
    ]
    common.print_table(
        ["Request", "Time", "Command", "Can run", "Estimated start", "Partition"],
        rows,
    )
    print()

    blocked = [result for result in results if result["start_time"] is None]
    if blocked:
        print("Blocked requests:")
        for result in blocked:
            print(
                f"  {result['command']} {result['label']} for {result['time']}: "
                f"{clean_result(result['result'])}"
            )
        print()


def main(args):
    nodes = common.fetch_nodes()
    if nodes is None:
        return

    nodes = [common.normalize_gpu_rollups(node) for node in nodes]
    hardware = common.group_by_hardware(nodes)

    for key in sorted(hardware):
        _, _, gpu_frozenset = key
        common.print_summary(
            common.hw_key_label(key),
            hardware[key],
            len(hardware[key]),
            sorted(dict(gpu_frozenset)),
        )

    gpu_types = sorted({gpu for node in nodes for gpu in node["cfg_gpus"]})
    gpu_capacities = {
        gpu: max(node["cfg_gpus"].get(gpu, 0) for node in nodes) for gpu in gpu_types
    }
    results = run_probes(
        gpu_capacities,
        cpus_per_task=args.cpus_per_task,
        mem=args.mem,
    )
    print_probe_results(
        results,
        cpus_per_task=args.cpus_per_task,
        mem=args.mem,
        sort_by_start=args.sort_by_start,
    )
