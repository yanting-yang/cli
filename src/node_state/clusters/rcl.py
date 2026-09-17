"""Reporter for the RCL servers (UBC ECE, GPU nodes with MIG slices).

Probe results reuse Killarney's report, including its copyable run commands.
"""

import re

from . import common, killarney

# rcl routes jobs by GPU request rather than walltime, so one time limit is enough.
PROBE_TIME = "0-01:00:00"

# rcl reserves cores via CoreSpecCount, so CPUTot overstates what jobs can get.
CPU_KEY = "cpu_efctv"

CPU_ONLY_LABEL = "CPU only (no GPU)"

# The controller's reply when the caller may not use an account or QOS.
INVALID_SCOPE = re.compile(r"Invalid (?:account|qos)", re.IGNORECASE)

# Printed before sbatch/srun output once a request passes the submit filter,
# including controller rejections such as an invalid QOS.
STORAGE_BANNER = re.compile(
    r"={8,}|STORAGE POLICY: .*? after you are done with your work\."
)


def max_gpu_counts(gpu_capacities, record, user):
    """Return the largest count of each GPU type worth probing in one job.

    The tightest per-user (`MaxTRESPU`) or per-job (`MaxTRESPJ`) cap in the QOS
    `record`, on the model or on all GPUs combined, bounds the count along with
    one node's capacity. A zero cap still probes one GPU to surface the reason.
    """
    counts = {}
    for gpu, capacity in gpu_capacities.items():
        caps = [
            limits[tres]
            for limits in (
                common.qos_user_limits(record, user).get("max_tres_pu", {}),
                record["max_tres_pj"],
            )
            for tres in (f"gres/gpu:{gpu}", "gres/gpu")
            if limits.get(tres) is not None
        ]
        counts[gpu] = max(1, min([capacity, *caps]))
    return counts


def run_probes(gpu_counts, *, account=None, qos=None, cpus_per_task, mem):
    """Probe 1..N GPUs of each type, plus CPU only, with both sbatch and srun.

    Every probe pins `--account` and `--qos` when given. If Slurm rejects that
    pair as invalid, only the CPU-only sbatch row is returned, since every other
    request would fail the same way. Deliberately omits --partition: rcl routes
    jobs to `mig`, `full` or `cpu` based on the GPU request, and passing -p only
    triggers a rerouting notice.
    """
    scope = [f"--account={account}"] if account else []
    scope += [f"--qos={qos}"] if qos else []

    def probe(command, gpu, count, label):
        directives = killarney.build_directives(
            gpu,
            count,
            PROBE_TIME,
            cpus_per_task=cpus_per_task,
            mem=mem,
        )
        directives[1:1] = scope
        if command == "sbatch":
            result = common.run_sbatch_test(directives)
        else:
            result = common.run_srun_test(directives)
        return {
            **result,
            "label": label,
            "time": PROBE_TIME,
            "command": command,
            "run_command": common.format_run_command(command, directives),
        }

    cpu_only = probe("sbatch", None, 1, CPU_ONLY_LABEL)
    if scope and INVALID_SCOPE.search(cpu_only["result"]):
        return [cpu_only]

    requests = [
        (gpu, count, f"{count}x {gpu}")
        for gpu, max_count in sorted(gpu_counts.items())
        for count in range(1, max_count + 1)
    ]
    requests.append((None, 1, CPU_ONLY_LABEL))

    results = []
    for command in ("sbatch", "srun"):
        for gpu, count, label in requests:
            if (command, gpu) == ("sbatch", None):
                results.append(cpu_only)
            else:
                results.append(probe(command, gpu, count, label))
    return results


def clean_result(text):
    """Strip command prefixes, the storage-policy banner and boilerplate."""
    text = re.sub(r"\b(?:sbatch|srun): (?:error: )?", "", text)
    text = STORAGE_BANNER.sub("", text)
    text = text.replace("allocation failure: Unspecified error", "")
    return " ".join(text.split()).strip(" .")


def format_limit(value):
    return "no limit" if value is None else value


def print_account_limits(user, after_each=None):
    """Report caps for each QOS listing each of the user's accounts.

    The controller cache can list an account under several QOS records; each
    gets its own table because their limits and usage are independent.
    `after_each(account, qos_name, record)` runs right after each table.
    Returns the reported records as `{account: {qos_name: record}}`, empty when
    none could be read.
    """
    output = common.fetch_assoc_mgr()
    if output is None:
        print("Account limits: unavailable ('scontrol show assoc_mgr' failed).\n")
        return {}

    accounts = common.parse_user_accounts(output, user)
    if not accounts:
        print(f"Account limits: no Slurm association found for '{user}'.\n")
        return {}

    qos_records = common.parse_qos_records(output)
    counts_by_qos = {}
    reported = {}
    for account in accounts:
        qos_names = common.qos_names_for_account(qos_records, account)
        if not qos_names:
            print(f"Account limits: no QOS found for account '{account}'.\n")
            continue
        for qos_name in qos_names:
            if qos_name not in counts_by_qos:
                counts_by_qos[qos_name] = (
                    common.fetch_user_job_counts(user, qos=qos_name) or {}
                )
            print_qos_limits(
                account, qos_name, qos_records[qos_name], user, counts_by_qos[qos_name]
            )
            if after_each is not None:
                after_each(account, qos_name, qos_records[qos_name])
        reported[account] = {name: qos_records[name] for name in qos_names}
    return reported


def print_qos_limits(account, qos_name, record, user, counts):
    """Print one QOS's caps and the caller's usage within that QOS."""
    limits = common.qos_user_limits(record, user)

    rows = [
        (
            "Jobs running",
            format_limit(limits["max_jobs"]) if "max_jobs" in limits else "?",
            counts.get("running", "?"),
        ),
        (
            "Jobs submitted",
            format_limit(limits["max_submit_jobs"])
            if "max_submit_jobs" in limits
            else "?",
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

    user = common.current_user()
    gpu_types = sorted({gpu for node in nodes for gpu in node["cfg_gpus"]})
    gpu_capacities = {
        gpu: max(node["cfg_gpus"].get(gpu, 0) for node in nodes) for gpu in gpu_types
    }

    def print_feasibility(title, gpu_counts, account=None, qos=None):
        results = run_probes(
            gpu_counts,
            account=account,
            qos=qos,
            cpus_per_task=args.cpus_per_task,
            mem=args.mem,
        )
        killarney.print_probe_results(
            results,
            title=title,
            sort_by_start=args.sort_by_start,
            clean=clean_result,
            notes=False,
        )

    def print_qos_feasibility(account, qos_name, record):
        print_feasibility(
            f"Job feasibility (account={account}, QOS={qos_name})",
            max_gpu_counts(gpu_capacities, record, user),
            account,
            qos_name,
        )

    # Each account limits table is followed by the probes for that pair; without
    # any pair, one unscoped table probes up to node capacity.
    if not print_account_limits(user, after_each=print_qos_feasibility):
        print_feasibility("Job feasibility", gpu_capacities)
    killarney.print_run_command_notes()
