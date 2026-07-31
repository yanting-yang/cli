import argparse

from . import node_resources


def main():
    parser = argparse.ArgumentParser(
        prog="node_state",
        description="Summarize Slurm node resources (CPU, memory, GPUs) grouped by hardware type.",
    )
    parser.add_argument(
        "cluster",
        nargs="?",
        type=str.casefold,
        choices=sorted(node_resources.CLUSTER_RUNNERS),
        help="Run a cluster-specific reporter; omit to use the generic fallback.",
    )
    args = parser.parse_args()
    node_resources.main(args.cluster)


if __name__ == "__main__":
    main()
