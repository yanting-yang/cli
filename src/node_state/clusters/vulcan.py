import re
import subprocess
from collections import Counter, defaultdict


SBATCH_ACCOUNT = "aip-xli135"
SBATCH_START_PATTERN = re.compile(
    r"\bto start at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\b"
)


def parse_tres_gpus(tres_str):
    gpus = {}
    for match in re.finditer(r"gres/gpu(?::([^=,]+))?=(\d+)", tres_str):
        label = match.group(1) or "gpu"
        gpus[label] = int(match.group(2))
    return gpus


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
            node[key] = cast(match.group(1)) if match else ("?" if cast is str else cast())

        match = re.search(r"CfgTRES=(\S+)", block)
        node["cfg_gpus"] = parse_tres_gpus(match.group(1) if match else "")
        match = re.search(r"AllocTRES=(\S*)", block)
        node["alloc_gpus"] = parse_tres_gpus(match.group(1) if match else "")

        nodes.append(node)
    return nodes


def node_hw_key(node):
    return (
        node["cpu_tot"],
        node["mem_tot"] // 1024,
        frozenset(node["cfg_gpus"].items()),
    )


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


def build_sbatch_test_script(node_name):
    return f"""#!/bin/bash
#SBATCH --test-only
#SBATCH --account={SBATCH_ACCOUNT}
#SBATCH --gres=gpu:l40s:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=3:00:00
#SBATCH --nodelist={node_name}
"""


def run_sbatch_test(node_name):
    try:
        completed = subprocess.run(
            ["sbatch"],
            input=build_sbatch_test_script(node_name),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return {"node": node_name, "start_time": None, "result": "'sbatch' not found"}
    except subprocess.TimeoutExpired:
        return {"node": node_name, "start_time": None, "result": "Timed out after 30s"}

    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    match = SBATCH_START_PATTERN.search(output)
    if match:
        return {"node": node_name, "start_time": match.group(1), "result": "Runnable"}

    result = " ".join(output.splitlines())
    if not result:
        result = f"sbatch exited with status {completed.returncode}"
    return {"node": node_name, "start_time": None, "result": result}


def print_summary(title, active_nodes, total, gpu_types):
    cpu_tot = sum(node["cpu_tot"] for node in active_nodes)
    cpu_alloc = sum(node["cpu_alloc"] for node in active_nodes)
    mem_tot = sum(node["mem_tot"] for node in active_nodes)
    mem_alloc = sum(node["mem_alloc"] for node in active_nodes)

    gpu_cfg_tot = {
        gpu: sum(node["cfg_gpus"].get(gpu, 0) for node in active_nodes)
        for gpu in gpu_types
    }
    gpu_alloc_tot = {
        gpu: sum(node["alloc_gpus"].get(gpu, 0) for node in active_nodes)
        for gpu in gpu_types
    }

    rows = [
        ("CPU (cores)", cpu_tot, cpu_alloc, cpu_tot - cpu_alloc),
        ("Memory (GB)", mem_tot // 1024, mem_alloc // 1024, (mem_tot - mem_alloc) // 1024),
    ]
    for gpu in gpu_types:
        total_gpus = gpu_cfg_tot[gpu]
        allocated_gpus = gpu_alloc_tot[gpu]
        rows.append((gpu, total_gpus, allocated_gpus, total_gpus - allocated_gpus))

    columns = ["Resource", "Total", "Allocated", "Available"]
    widths = [
        max(len(columns[index]), max(len(str(row[index])) for row in rows))
        for index in range(4)
    ]
    separator = " | "
    header = separator.join(column.ljust(width) for column, width in zip(columns, widths))
    print(f"{title} ({len(active_nodes)}/{total}):")
    print(f"States: {format_state_counts(active_nodes) or 'none'}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(separator.join(str(value).ljust(width) for value, width in zip(row, widths)))
    print()


def print_sbatch_test_results(results):
    runnable = sum(result["start_time"] is not None for result in results)
    start_times = [result["start_time"] for result in results if result["start_time"]]
    print(f"SBATCH test-only request: account={SBATCH_ACCOUNT}, 1x l40s, 16 CPUs, 128 GB, 3:00:00")
    print(f"Runnable: {runnable}/{len(results)}")
    print(f"Unavailable: {len(results) - runnable}/{len(results)}")
    if start_times:
        print(f"Estimated start range: {min(start_times)} to {max(start_times)}")

    columns = ["Node", "Can run", "Estimated start", "Result"]
    rows = [
        (
            result["node"],
            "yes" if result["start_time"] else "no",
            result["start_time"] or "-",
            result["result"],
        )
        for result in results
    ]
    widths = [
        max([len(columns[index]), *(len(str(row[index])) for row in rows)])
        for index in range(len(columns))
    ]
    separator = " | "
    header = separator.join(column.ljust(width) for column, width in zip(columns, widths))
    print(header)
    print("-" * len(header))
    for row in rows:
        print(separator.join(str(value).ljust(width) for value, width in zip(row, widths)))
    print()


def main(exclude_states=None):
    try:
        result = subprocess.run(
            ["scontrol", "show", "node"], capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        print("Error: 'scontrol' command not found. Are you on a Slurm login node?")
        return
    except subprocess.CalledProcessError as e:
        print(f"Error executing Slurm command: {e}")
        return

    nodes = parse_nodes(result.stdout)

    if exclude_states:
        exclude_pattern = "|".join(exclude_states)
        active_nodes = [
            node
            for node in nodes
            if not re.search(exclude_pattern, node["state"], re.IGNORECASE)
        ]
    else:
        active_nodes = list(nodes)

    all_hardware = defaultdict(list)
    active_hardware = defaultdict(list)
    for node in nodes:
        all_hardware[node_hw_key(node)].append(node)
    for node in active_nodes:
        active_hardware[node_hw_key(node)].append(node)

    for key in sorted(all_hardware):
        _, _, gpu_frozenset = key
        print_summary(
            hw_key_label(key),
            active_hardware.get(key, []),
            len(all_hardware[key]),
            sorted(dict(gpu_frozenset)),
        )

    gpu_nodes = [node for node in nodes if is_gpu_node(node)]
    print(f"Testing job feasibility on {len(gpu_nodes)} GPU nodes...")
    test_results = [run_sbatch_test(node["name"]) for node in gpu_nodes]
    print_sbatch_test_results(test_results)

    runnable_names = {
        result["node"] for result in test_results if result["start_time"] is not None
    }
    runnable_nodes = [node for node in gpu_nodes if node["name"] in runnable_names]
    gpu_types = sorted({gpu for node in gpu_nodes for gpu in node["cfg_gpus"]})
    print_summary("Runnable GPU nodes", runnable_nodes, len(gpu_nodes), gpu_types)
