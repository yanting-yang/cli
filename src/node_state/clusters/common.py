"""Cluster-agnostic helpers shared by the per-cluster reporters."""

import getpass
import re
import subprocess
from collections import Counter, defaultdict

SBATCH_START_PATTERN = re.compile(
    r"\bto start at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\b"
)
SBATCH_PARTITION_PATTERN = re.compile(r"\bin partition (\S+)")
SINFO_PARTITION_FORMAT = "Partition,Gres,Nodes,Time"


def parse_tres_gpus(tres_str):
    gpus = {}
    for match in re.finditer(r"gres/gpu(?::([^=,]+))?=(\d+)", tres_str):
        label = match.group(1) or "gpu"
        gpus[label] = int(match.group(2))
    return gpus


def remove_untyped_gpu_rollup(gpu_counts):
    """Drop the untyped GPU total when per-model counts are available."""
    typed = {label: count for label, count in gpu_counts.items() if label != "gpu"}
    return typed if typed else gpu_counts


def normalize_gpu_rollups(node):
    node["cfg_gpus"] = remove_untyped_gpu_rollup(node["cfg_gpus"])
    node["alloc_gpus"] = remove_untyped_gpu_rollup(node["alloc_gpus"])
    return node


def parse_nodes(output):
    nodes = []
    for block in re.split(r"\n(?=NodeName=)", output.strip()):
        if not block.strip():
            continue
        node = {}
        for pattern, key, cast in [
            (r"NodeName=(\S+)", "name", str),
            (r"State=(\S+)", "state", str),
            (r"CPUTot=(\d+)", "cpu_tot", int),
            (r"CPUAlloc=(\d+)", "cpu_alloc", int),
            (r"RealMemory=(\d+)", "mem_tot", int),
            (r"AllocMem=(\d+)", "mem_alloc", int),
        ]:
            match = re.search(pattern, block)
            node[key] = (
                cast(match.group(1)) if match else ("?" if cast is str else cast())
            )

        # Cores held back by CoreSpecCount are counted in CPUTot but cannot be
        # allocated; CPUEfctv is what the scheduler actually offers to jobs.
        match = re.search(r"CPUEfctv=(\d+)", block)
        node["cpu_efctv"] = int(match.group(1)) if match else node["cpu_tot"]

        match = re.search(r"CfgTRES=(\S+)", block)
        node["cfg_gpus"] = parse_tres_gpus(match.group(1) if match else "")
        match = re.search(r"AllocTRES=(\S*)", block)
        node["alloc_gpus"] = parse_tres_gpus(match.group(1) if match else "")

        nodes.append(node)
    return nodes


def fetch_nodes():
    """Return parsed `scontrol show node` output, or None when it cannot run."""
    try:
        result = subprocess.run(
            ["scontrol", "show", "node"], capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        print("Error: 'scontrol' command not found. Are you on a Slurm login node?")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error executing Slurm command: {e}")
        return None

    return parse_nodes(result.stdout)


def fetch_partitions(sinfo_format=SINFO_PARTITION_FORMAT):
    """Return `sinfo --Format=...` output, or None when it cannot run."""
    try:
        result = subprocess.run(
            ["sinfo", f"--Format={sinfo_format}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    return result.stdout


def parse_sinfo_table(text):
    """Split fixed-width `sinfo --Format=...` output into columns and rows.

    Field boundaries come from the header line rather than from whitespace, so
    an empty value keeps its column instead of shifting the rest of the row.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []

    header = lines[0]
    starts = [match.start() for match in re.finditer(r"\S+", header)]
    bounds = list(zip(starts, [*starts[1:], None]))
    columns = [header[start:end].strip() for start, end in bounds]
    rows = [
        [line[start:end].strip() for start, end in bounds] for line in lines[1:]
    ]
    return columns, rows


def node_hw_key(node, cpu_key="cpu_tot"):
    return (
        node[cpu_key],
        node["mem_tot"] // 1024,
        frozenset(node["cfg_gpus"].items()),
    )


def group_by_hardware(nodes, cpu_key="cpu_tot"):
    groups = defaultdict(list)
    for node in nodes:
        groups[node_hw_key(node, cpu_key)].append(node)
    return groups


def is_gpu_node(node):
    return bool(node["cfg_gpus"])


def hw_key_label(key):
    cpu_tot, mem_gb, gpu_frozenset = key
    parts = [f"{cpu_tot} CPUs", f"{mem_gb} GB"]
    if gpu_frozenset:
        parts += [f"{count}x {gpu}" for gpu, count in sorted(gpu_frozenset)]
    return " / ".join(parts)


def format_state_counts(nodes):
    counts = Counter(node["state"] for node in nodes)
    return ", ".join(f"{state}={count}" for state, count in sorted(counts.items()))


def print_table(columns, rows):
    widths = [
        max([len(columns[index]), *(len(str(row[index])) for row in rows)])
        for index in range(len(columns))
    ]
    separator = " | "
    header = separator.join(
        column.ljust(width) for column, width in zip(columns, widths)
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            separator.join(str(value).ljust(width) for value, width in zip(row, widths))
        )


def print_summary(title, nodes, total, gpu_types, cpu_key="cpu_tot"):
    cpu_tot = sum(node[cpu_key] for node in nodes)
    cpu_alloc = sum(node["cpu_alloc"] for node in nodes)
    mem_tot = sum(node["mem_tot"] for node in nodes)
    mem_alloc = sum(node["mem_alloc"] for node in nodes)

    gpu_cfg_tot = {
        gpu: sum(node["cfg_gpus"].get(gpu, 0) for node in nodes) for gpu in gpu_types
    }
    gpu_alloc_tot = {
        gpu: sum(node["alloc_gpus"].get(gpu, 0) for node in nodes) for gpu in gpu_types
    }

    rows = [
        ("CPU (cores)", cpu_tot, cpu_alloc, cpu_tot - cpu_alloc),
        (
            "Memory (GB)",
            mem_tot // 1024,
            mem_alloc // 1024,
            (mem_tot - mem_alloc) // 1024,
        ),
    ]
    for gpu in gpu_types:
        total_gpus = gpu_cfg_tot[gpu]
        allocated_gpus = gpu_alloc_tot[gpu]
        rows.append((gpu, total_gpus, allocated_gpus, total_gpus - allocated_gpus))

    print(f"{title} ({len(nodes)}/{total}):")
    print(f"States: {format_state_counts(nodes) or 'none'}")
    print_table(["Resource", "Total", "Allocated", "Available"], rows)
    print()


def current_user():
    return getpass.getuser()


def parse_tres_values(tres_str):
    """Parse a plain `cpu=8,mem=65536,gres/gpu=1` TRES list into a dict."""
    values = {}
    for item in tres_str.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        # TRES names contain '=' only as the final separator, but do contain
        # ':' (gres/gpu:nvidia_b200), so split from the right.
        name, _, raw = item.rpartition("=")
        try:
            values[name] = int(raw)
        except ValueError:
            continue
    return values


def parse_tres_limits(tres_str):
    """Parse `cpu=64(8),mem=N(65536)` into separate limit and usage dicts."""
    limits = {}
    usage = {}
    for item in tres_str.split(","):
        name, separator, raw = item.strip().rpartition("=")
        if not name or not separator:
            continue
        match = re.fullmatch(r"(N|\d+)\((\d+)\)", raw)
        if match:
            limits[name] = None if match.group(1) == "N" else int(match.group(1))
            usage[name] = int(match.group(2))
    return limits, usage


def parse_limit(text):
    """Turn a `16(5)` / `N(0)` limit token into its numeric limit or None."""
    match = re.match(r"(\S+?)\((\d+)\)", text)
    if not match:
        return None
    return None if match.group(1) == "N" else int(match.group(1))


def parse_default_account(output, user):
    """Find `user`'s default account in `scontrol show assoc_mgr` output."""
    fallback = None
    for block in re.split(r"\n(?=ClusterName=)", output):
        if f"UserName={user}(" not in block:
            continue
        match = re.search(r"Account=(\S+)", block)
        if not match:
            continue
        if "DefAssoc=Yes" in block:
            return match.group(1)
        fallback = fallback or match.group(1)
    return fallback


def parse_qos_records(output):
    """Parse the `QOS Records` section of `scontrol show assoc_mgr` output.

    Each QOS record contains per-job `max_tres_pj`, `accounts`, and
    `user_limits` keyed by user with `max_jobs`, `max_submit_jobs`, and
    per-user `max_tres_pu` caps. A limit of None means no QOS cap is set.
    Resource usage is kept separately in `user_tres_usage`, keyed by user,
    so borrowing another user's limits never borrows their usage.
    """
    records = {}
    record = None
    subsection = None
    user = None
    in_qos_section = False

    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "QOS Records":
            in_qos_section = True
            continue
        if not in_qos_section:
            continue

        match = re.match(r"^QOS=(\S+?)\(\d+\)\s*$", line)
        if match:
            record = {
                "max_tres_pj": {},
                "accounts": set(),
                "user_limits": {},
                "user_tres_usage": {},
            }
            records[match.group(1)] = record
            subsection = None
            user = None
            continue
        if record is None:
            continue

        if stripped == "Account Limits":
            subsection = "accounts"
            continue
        if stripped == "User Limits":
            subsection = "users"
            continue
        if line.startswith("    MaxTRESPJ="):
            record["max_tres_pj"] = parse_tres_values(stripped.partition("=")[2])
            continue

        indent = len(line) - len(line.lstrip())
        if subsection == "accounts" and indent == 6:
            record["accounts"].add(stripped)
        elif subsection == "users" and indent == 6:
            user = re.sub(r"\(\d+\)$", "", stripped)
            record["user_limits"].setdefault(user, {})
        elif subsection == "users" and indent == 8 and user:
            for field, key in (
                ("MaxJobsPU", "max_jobs"),
                ("MaxSubmitJobsPU", "max_submit_jobs"),
            ):
                match = re.search(rf"\b{field}=(\S+)", line)
                if match:
                    record["user_limits"][user][key] = parse_limit(match.group(1))
            match = re.search(r"\bMaxTRESPU=(\S*)", line)
            if match:
                limits, usage = parse_tres_limits(match.group(1))
                record["user_limits"][user]["max_tres_pu"] = limits
                record["user_tres_usage"][user] = usage
    return records


def qos_names_for_account(qos_records, account):
    """Return every QOS listing `account`, sorted by name."""
    return sorted(
        name for name, record in qos_records.items() if account in record["accounts"]
    )


def qos_user_limits(record, user):
    """Per-user job and resource limits for `record`.

    QOS per-user limits are a single value applied to every user, so when the
    caller has no entry yet (they have never had a job tracked under this QOS)
    any other user's entry carries the same limits.
    """
    limits = record["user_limits"].get(user)
    if limits:
        return limits
    for other in record["user_limits"].values():
        if other:
            return other
    return {}


def fetch_assoc_mgr():
    """Return `scontrol show assoc_mgr` output, or None when unavailable."""
    try:
        result = subprocess.run(
            ["scontrol", "show", "assoc_mgr", "flags=assoc,qos"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout


def fetch_user_job_counts(user, qos=None):
    """Count the user's jobs, optionally within one QOS, or None if unavailable."""
    command = ["squeue", "-h", "-u", user, "-o", "%t"]
    if qos is not None:
        command.extend(["--qos", qos])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    states = result.stdout.split()
    return {
        "running": states.count("R"),
        "pending": states.count("PD"),
        "total": len(states),
    }


def run_sbatch_test(directives, timeout=30):
    """Submit an `sbatch --test-only` probe and report whether it could run.

    Returns a dict with `start_time` (None when the request was rejected),
    `partition` (as resolved by the scheduler) and the raw `result` text.
    """
    script = "#!/bin/bash\n" + "".join(f"#SBATCH {arg}\n" for arg in directives)
    try:
        completed = subprocess.run(
            ["sbatch"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"start_time": None, "partition": None, "result": "'sbatch' not found"}
    except subprocess.TimeoutExpired:
        return {
            "start_time": None,
            "partition": None,
            "result": f"Timed out after {timeout}s",
        }

    return parse_scheduling_test_result(completed, "sbatch")


def run_srun_test(directives, timeout=30):
    """Run an `srun --test-only` probe without starting an interactive job."""
    try:
        completed = subprocess.run(
            ["srun", *directives],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"start_time": None, "partition": None, "result": "'srun' not found"}
    except subprocess.TimeoutExpired:
        return {
            "start_time": None,
            "partition": None,
            "result": f"Timed out after {timeout}s",
        }

    return parse_scheduling_test_result(completed, "srun")


def parse_scheduling_test_result(completed, command):
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    start_match = SBATCH_START_PATTERN.search(output)
    partition_match = SBATCH_PARTITION_PATTERN.search(output)
    partition = partition_match.group(1) if partition_match else None

    if start_match:
        return {
            "start_time": start_match.group(1),
            "partition": partition,
            "result": "Runnable",
        }

    result = " ".join(output.splitlines())
    if not result:
        result = f"{command} exited with status {completed.returncode}"
    return {"start_time": None, "partition": partition, "result": result}
