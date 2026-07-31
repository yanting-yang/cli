import argparse
import sys

from . import node_resources


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main():
    parser = argparse.ArgumentParser(
        prog="node_state",
        description="Summarize Slurm node resources (CPU, memory, GPUs) grouped by hardware type.",
        allow_abbrev=False,
    )
    cluster_names = sorted(node_resources.CLUSTER_RUNNERS)
    subparsers = parser.add_subparsers(
        dest="cluster",
        metavar="{" + ",".join(cluster_names) + "}",
        help="Run a cluster-specific reporter; omit to use the generic fallback.",
    )
    cluster_parsers = {
        cluster_name: subparsers.add_parser(
            cluster_name,
            help=f"Run the {cluster_name} cluster reporter.",
            allow_abbrev=False,
        )
        for cluster_name in cluster_names
    }

    killarney_parser = cluster_parsers["killarney"]
    killarney_parser.add_argument(
        "--cpus-per-task",
        dest="probe_cpus",
        type=positive_int,
        metavar="N",
        help="Set CPUs per task for Killarney job-feasibility probes.",
    )
    killarney_parser.add_argument(
        "--mem",
        dest="probe_ram",
        metavar="SIZE",
        help="Set memory for Killarney job-feasibility probes (for example, 64G).",
    )
    killarney_parser.add_argument(
        "--sort-by-start",
        action="store_true",
        help="Sort Killarney job-feasibility rows by estimated start.",
    )

    arguments = sys.argv[1:]
    if arguments and arguments[0].casefold() in node_resources.CLUSTER_RUNNERS:
        arguments[0] = arguments[0].casefold()
    args = parser.parse_args(arguments)

    runner_options = {}
    if args.cluster == "killarney":
        if args.probe_cpus is not None:
            runner_options["probe_cpus"] = args.probe_cpus
        if args.probe_ram is not None:
            runner_options["probe_ram"] = args.probe_ram
        if args.sort_by_start:
            runner_options["sort_by_start"] = True

    node_resources.main(args.cluster, **runner_options)


if __name__ == "__main__":
    main()
