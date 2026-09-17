import argparse

from .clusters import fallback, killarney, rcl, tamia, vulcan

CLUSTER_RUNNERS = {
    "killarney": killarney.main,
    "rcl": rcl.main,
    "tamia": tamia.main,
    "vulcan": vulcan.main,
}

# Clusters whose reporters run the resizable feasibility probes.
PROBE_CLUSTERS = ("killarney", "rcl", "tamia")


def main(argv=None):
    parser = argparse.ArgumentParser()
    cluster_names = sorted(CLUSTER_RUNNERS)
    subparsers = parser.add_subparsers(dest="cluster")
    cluster_parsers = {
        cluster_name: subparsers.add_parser(cluster_name)
        for cluster_name in cluster_names
    }

    for cluster_name in PROBE_CLUSTERS:
        probe_parser = cluster_parsers[cluster_name]
        probe_parser.add_argument(
            "--cpus-per-task",
            type=int,
            default=4,
            metavar="4",
        )
        probe_parser.add_argument(
            "--mem",
            default="32G",
            metavar="32G",
        )
        probe_parser.add_argument(
            "--sort-by-start",
            action="store_true",
        )

    args = parser.parse_args(argv)

    runner = fallback.main if args.cluster is None else CLUSTER_RUNNERS[args.cluster]
    runner(args)


if __name__ == "__main__":
    main()
