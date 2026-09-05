"""Reporter for the `rcl` cluster (UBC ECE, single B200 node with MIG slices)."""

from . import common

# Probe size for the feasibility check. Kept small on purpose: the point is to
# test GPU/partition availability, not to bump into per-account job caps.
PROBE_CPUS = 4
PROBE_MEM = "32G"
PROBE_TIME = "1:00:00"

# rcl reserves cores via CoreSpecCount, so CPUTot overstates what jobs can get.
CPU_KEY = "cpu_efctv"


def build_directives(gpu=None):
    """Directives for one probe.

    Deliberately omits --partition: rcl routes jobs to `mig`, `full` or `cpu`
    based on the GPU request, and passing -p only triggers a rerouting notice.
    """
    directives = ["--test-only"]
    if gpu:
        directives.append(f"--gres=gpu:{gpu}:1")
    directives += [
        f"--cpus-per-task={PROBE_CPUS}",
        f"--mem={PROBE_MEM}",
        f"--time={PROBE_TIME}",
    ]
    return directives


def clean_result(text):
    """Trim sbatch's boilerplate so the reason fits on one line."""
    for prefix in ("sbatch: error: ", "sbatch: "):
        text = text.removeprefix(prefix)
    return text.replace("allocation failure: Unspecified error", "").strip(" .")


def run_probes(gpu_types):
    requests = [(gpu, f"1x {gpu}") for gpu in gpu_types]
    requests.append((None, "CPU only (no GPU)"))

    results = []
    for gpu, label in requests:
        result = common.run_sbatch_test(build_directives(gpu))
        results.append({**result, "label": label})
    return results


def print_probe_results(results):
    runnable = sum(result["start_time"] is not None for result in results)
    print(
        f"Job feasibility (sbatch --test-only, {PROBE_CPUS} CPUs, "
        f"{PROBE_MEM}, {PROBE_TIME}):"
    )
    print(f"Runnable: {runnable}/{len(results)}")

    rows = [
        (
            result["label"],
            "yes" if result["start_time"] else "no",
            result["start_time"] or "-",
            result["partition"] or "-",
        )
        for result in results
    ]
    common.print_table(["Request", "Can run", "Estimated start", "Partition"], rows)
    print()

    blocked = [result for result in results if result["start_time"] is None]
    if blocked:
        print("Blocked requests:")
        for result in blocked:
            print(f"  {result['label']}: {clean_result(result['result'])}")
        print()


def format_limit(value):
    return "no limit" if value is None else value


def print_account_limits(user):
    """Report the QOS caps that govern what this user can actually submit.

    rcl enforces per-account caps through a QOS rather than through anything
    visible in `scontrol show node`, so the resource tables above can show
    capacity this account is not allowed to ask for.
    """
    output = common.fetch_assoc_mgr()
    if output is None:
        print("Account limits: unavailable ('scontrol show assoc_mgr' failed).\n")
        return

    account = common.parse_default_account(output, user)
    if account is None:
        print(f"Account limits: no Slurm association found for '{user}'.\n")
        return

    qos_records = common.parse_qos_records(output)
    qos_name = common.qos_for_account(qos_records, account)
    if qos_name is None:
        print(f"Account limits: no single QOS found for account '{account}'.\n")
        return

    record = qos_records[qos_name]
    limits = common.qos_user_limits(record, user)
    counts = common.fetch_user_job_counts(user) or {}

    rows = [
        (
            "Jobs running",
            format_limit(limits.get("max_jobs")),
            counts.get("running", "?"),
        ),
        (
            "Jobs submitted",
            format_limit(limits.get("max_submit_jobs")),
            counts.get("total", "?"),
        ),
    ]
    for scope, resource_limits, usage in [
        ("job", record["max_tres_pj"], None),
        (
            "user",
            limits.get("max_tres_pu", {}),
            record["user_tres_usage"].get(user, {}),
        ),
    ]:
        resources = [
            (f"CPUs per {scope}", "cpu", 1),
            (f"Memory per {scope} (GB)", "mem", 1024),
            (f"GPUs per {scope}", "gres/gpu", 1),
        ]
        resources += [
            (f"{tres.removeprefix('gres/gpu:')} per {scope}", tres, 1)
            for tres in sorted(resource_limits)
            if tres.startswith("gres/gpu:")
        ]
        for label, tres, scale in resources:
            value = resource_limits.get(tres)
            if value is None:
                continue
            in_use = "-" if usage is None else usage.get(tres, "?")
            if isinstance(in_use, int):
                in_use = f"{in_use / scale:g}"
            rows.append((label, f"{value / scale:g}", in_use))

    print(f"Account limits (account={account}, QOS={qos_name}):")
    common.print_table(["Limit", "Value", "In use"], rows)
    print()


def main(args):
    del args

    nodes = common.fetch_nodes()
    if nodes is None:
        return

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

    print_account_limits(common.current_user())

    gpu_types = sorted({gpu for node in nodes for gpu in node["cfg_gpus"]})
    print_probe_results(run_probes(gpu_types))
