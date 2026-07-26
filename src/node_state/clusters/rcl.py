"""Reporter for the `rcl` cluster (UBC ECE, single B200 node with MIG slices)."""

from . import common


# Probe size for the feasibility check. Kept small on purpose: the point is to
# test GPU/partition availability, not to bump into per-account job caps.
PROBE_CPUS = 4
PROBE_MEM = "32G"
PROBE_TIME = "1:00:00"

# rcl reserves cores via CoreSpecCount, so CPUTot overstates what jobs can get.
CPU_KEY = "cpu_efctv"


def typed_gpus(gpu_counts):
    """Drop the untyped `gres/gpu` rollup when per-model entries are present.

    rcl advertises both `gres/gpu=16` and the per-model counts that sum to it,
    so keeping the rollup would double-count every GPU.
    """
    typed = {label: count for label, count in gpu_counts.items() if label != "gpu"}
    return typed if typed else gpu_counts


def normalize_node(node):
    node["cfg_gpus"] = typed_gpus(node["cfg_gpus"])
    node["alloc_gpus"] = typed_gpus(node["alloc_gpus"])
    return node


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
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.replace("allocation failure: Unspecified error", "").strip(" .")


def run_probes(gpu_types):
    requests = [(gpu, f"1x {gpu}") for gpu in gpu_types]
    requests.append((None, "CPU only (no GPU)"))

    results = []
    for gpu, label in requests:
        result = common.run_sbatch_test(build_directives(gpu))
        result["label"] = label
        results.append(result)
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
    per_job = record["max_tres_pj"]
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
    for label, tres, scale in [
        ("CPUs per job", "cpu", 1),
        ("Memory per job (GB)", "mem", 1024),
        ("GPUs per job", "gres/gpu", 1),
    ]:
        if tres in per_job:
            rows.append((label, per_job[tres] // scale, "-"))

    print(f"Account limits (account={account}, QOS={qos_name}):")
    common.print_table(["Limit", "Value", "In use"], rows)
    print()


def main(exclude_states=None):
    nodes = common.fetch_nodes()
    if nodes is None:
        return

    nodes = [normalize_node(node) for node in nodes]
    active_nodes = common.filter_active_nodes(nodes, exclude_states)

    all_hardware = common.group_by_hardware(nodes, CPU_KEY)
    active_hardware = common.group_by_hardware(active_nodes, CPU_KEY)

    for key in sorted(all_hardware):
        _, _, gpu_frozenset = key
        common.print_summary(
            common.hw_key_label(key),
            active_hardware.get(key, []),
            len(all_hardware[key]),
            sorted(dict(gpu_frozenset)),
            cpu_key=CPU_KEY,
        )

    print_account_limits(common.current_user())

    gpu_types = sorted({gpu for node in nodes for gpu in node["cfg_gpus"]})
    print_probe_results(run_probes(gpu_types))
