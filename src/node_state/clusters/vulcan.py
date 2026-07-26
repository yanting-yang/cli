"""Reporter for the `vulcan` cluster."""

from . import common


SBATCH_ACCOUNT = "aip-xli135"


def build_directives(node_name):
    return [
        "--test-only",
        f"--account={SBATCH_ACCOUNT}",
        "--gres=gpu:l40s:1",
        "--cpus-per-task=16",
        "--mem=128G",
        "--time=3:00:00",
        f"--nodelist={node_name}",
    ]


def run_sbatch_test(node_name):
    result = common.run_sbatch_test(build_directives(node_name))
    result["node"] = node_name
    return result


def print_sbatch_test_results(results):
    runnable = sum(result["start_time"] is not None for result in results)
    start_times = [result["start_time"] for result in results if result["start_time"]]
    print(
        f"SBATCH test-only request: account={SBATCH_ACCOUNT}, 1x l40s, "
        "16 CPUs, 128 GB, 3:00:00"
    )
    print(f"Runnable: {runnable}/{len(results)}")
    print(f"Unavailable: {len(results) - runnable}/{len(results)}")
    if start_times:
        print(f"Estimated start range: {min(start_times)} to {max(start_times)}")

    rows = [
        (
            result["node"],
            "yes" if result["start_time"] else "no",
            result["start_time"] or "-",
            result["result"],
        )
        for result in results
    ]
    common.print_table(["Node", "Can run", "Estimated start", "Result"], rows)
    print()


def main(exclude_states=None):
    nodes = common.fetch_nodes()
    if nodes is None:
        return

    active_nodes = common.filter_active_nodes(nodes, exclude_states)

    all_hardware = common.group_by_hardware(nodes)
    active_hardware = common.group_by_hardware(active_nodes)

    for key in sorted(all_hardware):
        _, _, gpu_frozenset = key
        common.print_summary(
            common.hw_key_label(key),
            active_hardware.get(key, []),
            len(all_hardware[key]),
            sorted(dict(gpu_frozenset)),
        )

    gpu_nodes = [node for node in nodes if common.is_gpu_node(node)]
    print(f"Testing job feasibility on {len(gpu_nodes)} GPU nodes...")
    test_results = [run_sbatch_test(node["name"]) for node in gpu_nodes]
    print_sbatch_test_results(test_results)

    runnable_names = {
        result["node"] for result in test_results if result["start_time"] is not None
    }
    runnable_nodes = [node for node in gpu_nodes if node["name"] in runnable_names]
    gpu_types = sorted({gpu for node in gpu_nodes for gpu in node["cfg_gpus"]})
    common.print_summary("Runnable GPU nodes", runnable_nodes, len(gpu_nodes), gpu_types)
