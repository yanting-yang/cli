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
            directives = build_directives(
                gpu,
                count,
                time_limit,
                cpus_per_task=cpus_per_task,
                mem=mem,
            )
            result = common.run_sbatch_test(directives)
            results.append(
                {
                    **result,
                    "label": label,
                    "time": time_limit,
                    "command": "sbatch",
                    "run_command": common.format_run_command("sbatch", directives),
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
        directives = build_directives(
            gpu,
            count,
            INTERACTIVE_TIME,
            cpus_per_task=cpus_per_task,
            mem=mem,
        )
        result = common.run_srun_test(directives)
        results.append(
            {
                **result,
                "label": label,
                "time": INTERACTIVE_TIME,
                "command": "srun",
                "run_command": common.format_run_command("srun", directives),
            }
        )
    return results


def print_partition_table():
    """Print `sinfo`'s partition, GRES, node-count and time-limit table."""
    output = common.fetch_partitions()
    if output is None:
        print("Partitions: 'sinfo' unavailable")
        print()
        return

    columns, rows = common.parse_sinfo_table(output)
    if not rows:
        print("Partitions: none reported")
        print()
        return

    print("Partitions:")
    common.print_table(columns, rows)
    print()


def print_account_limits(user):
    """Report each of the caller's accounts and its QOS caps and usage."""
    output = common.fetch_assoc_mgr()
    if output is None:
        print("Account limits: unavailable ('scontrol show assoc_mgr' failed).\n")
        return

    records = common.parse_qos_records(output)
    account_qos = common.fetch_user_account_qos(user, "killarney")
    if account_qos is None:
        account_qos = {
            account: common.qos_names_for_account(records, account)
            for account in common.parse_user_accounts(output, user)
        }
        print(
            "Account limits: using cached QOS matches; "
            "these may be incomplete and do not establish QOS permissions."
        )
    if not account_qos:
        print(f"Account limits: no Slurm association found for '{user}'.\n")
        return

    print(f"QOS limits for {user}:")
    print("Account usage covers all users; user usage covers all accounts in the QOS.")
    print("'no limit' means no cap at this QOS scope; other Slurm limits may apply.")
    print("'?' means the limit or usage is unavailable.\n")
    counts_by_qos = {}
    for account, qos_names in sorted(account_qos.items()):
        if not qos_names:
            print(f"Account limits: no QOS found for account '{account}'.\n")
        for qos_name in sorted(qos_names):
            if qos_name not in records:
                print(
                    f"Account limits (account={account}, QOS={qos_name}): "
                    "unavailable in the controller cache.\n"
                )
                continue
            if qos_name not in counts_by_qos:
                counts_by_qos[qos_name] = (
                    common.fetch_user_job_counts(user, qos=qos_name) or {}
                )
            common.print_account_qos_limits(
                account, qos_name, records[qos_name], user, counts_by_qos[qos_name]
            )


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
            result["run_command"],
        )
        for result in displayed_results
    ]
    common.print_table(
        [
            "Request", "Time", "Command", "Can run", "Estimated start", "Partition",
            "Run command",
        ],
        rows,
    )
    print()

    print("Run command: replace job.sh with your batch script; srun opens a Bash shell.")
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

    print_partition_table()
    print_account_limits(common.current_user())

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
