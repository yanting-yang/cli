import subprocess
import re
from collections import defaultdict


def parse_tres_gpus(tres_str):
    gpus = {}
    for m in re.finditer(r"gres/gpu(?::([^=,]+))?=(\d+)", tres_str):
        label = m.group(1) or "gpu"
        gpus[label] = int(m.group(2))
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

        # Parse GPU dicts once; reused by all downstream functions
        match = re.search(r"CfgTRES=(\S+)", block)
        node["cfg_gpus"] = parse_tres_gpus(match.group(1) if match else "")
        match = re.search(r"AllocTRES=(\S*)", block)
        node["alloc_gpus"] = parse_tres_gpus(match.group(1) if match else "")

        nodes.append(node)
    return nodes


def node_hw_key(node):
    return (node["cpu_tot"], node["mem_tot"], frozenset(node["cfg_gpus"].items()))


def hw_key_label(key):
    cpu_tot, mem_tot, gpu_frozenset = key
    parts = [f"{cpu_tot} CPUs", f"{mem_tot // 1024} GB"]
    if gpu_frozenset:
        parts += [f"{v}x {g}" for g, v in sorted(gpu_frozenset)]
    return " / ".join(parts)


def print_summary(title, active_nodes, total, gpu_types):
    cpu_tot   = sum(n["cpu_tot"]   for n in active_nodes)
    cpu_alloc = sum(n["cpu_alloc"] for n in active_nodes)
    mem_tot   = sum(n["mem_tot"]   for n in active_nodes)
    mem_alloc = sum(n["mem_alloc"] for n in active_nodes)

    gpu_cfg_tot   = {g: sum(n["cfg_gpus"].get(g, 0)   for n in active_nodes) for g in gpu_types}
    gpu_alloc_tot = {g: sum(n["alloc_gpus"].get(g, 0) for n in active_nodes) for g in gpu_types}

    rows = [
        ("CPU (cores)", cpu_tot,           cpu_alloc,           cpu_tot   - cpu_alloc),
        ("Memory (GB)", mem_tot // 1024,   mem_alloc // 1024,   (mem_tot  - mem_alloc) // 1024),
    ]
    for g in gpu_types:
        tot   = gpu_cfg_tot[g]
        alloc = gpu_alloc_tot[g]
        rows.append((g, tot, alloc, tot - alloc))

    cols = ["Resource", "Total", "Allocated", "Available"]
    widths = [max(len(cols[i]), max(len(str(r[i])) for r in rows)) for i in range(4)]
    sep = " | "
    header = sep.join(c.ljust(w) for c, w in zip(cols, widths))
    print(f"{title} ({len(active_nodes)}/{total}):")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(sep.join(str(v).ljust(w) for v, w in zip(row, widths)))
    print()


EXCLUDE_STATES = ["PLANNED", "DRAIN", "MAINTENANCE", "RESERVED", "ALLOCATED", "DOWN"]


def main():
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

    exclude_pattern = "|".join(EXCLUDE_STATES)
    active_nodes = [n for n in nodes if not re.search(exclude_pattern, n["state"], re.IGNORECASE)]

    all_hw    = defaultdict(list)
    active_hw = defaultdict(list)
    for n in nodes:
        all_hw[node_hw_key(n)].append(n)
    for n in active_nodes:
        active_hw[node_hw_key(n)].append(n)

    for key in sorted(all_hw.keys()):
        _, _, gpu_frozenset = key
        print_summary(
            hw_key_label(key),
            active_hw.get(key, []),
            len(all_hw[key]),
            sorted(dict(gpu_frozenset).keys()),
        )


if __name__ == "__main__":
    main()
