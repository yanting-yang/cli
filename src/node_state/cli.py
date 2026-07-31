import argparse

from .clusters import fallback, killarney, rcl, vulcan

CLUSTER_RUNNERS = {
    "killarney": killarney.main,
    "rcl": rcl.main,
    "vulcan": vulcan.main,
}


def main(argv=None):
    parser = argparse.ArgumentParser()
    cluster_names = sorted(CLUSTER_RUNNERS)
    subparsers = parser.add_subparsers(dest="cluster")
    cluster_parsers = {
        cluster_name: subparsers.add_parser(cluster_name)
        for cluster_name in cluster_names
    }

    killarney_parser = cluster_parsers["killarney"]
    killarney_parser.add_argument(
        "--cpus-per-task",
        type=int,
        default=4,
        metavar="4",
    )
    killarney_parser.add_argument(
        "--mem",
        default="32G",
        metavar="32G",
    )
    killarney_parser.add_argument(
        "--sort-by-start",
        action="store_true",
    )

    args = parser.parse_args(argv)

    runner = fallback.main if args.cluster is None else CLUSTER_RUNNERS[args.cluster]
    runner(args)


if __name__ == "__main__":
    main()
