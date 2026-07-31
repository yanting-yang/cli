from .clusters import fallback, killarney, rcl, vulcan


CLUSTER_RUNNERS = {
    "killarney": killarney.main,
    "rcl": rcl.main,
    "vulcan": vulcan.main,
}


def main(cluster_name=None, **runner_options):
    runner = (
        fallback.main
        if cluster_name is None
        else CLUSTER_RUNNERS[cluster_name.casefold()]
    )
    runner(**runner_options)
