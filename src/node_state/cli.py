"""Entry point for `node_state`: the dashboard, or a one-shot text report."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from . import hosts, probes, profiles, report, slurm, snapshot


def find_clusters():
    """This cluster, if Slurm runs here, then each cluster with an ssh host.

    The local cluster is detected from `scontrol show config`; a configured
    remote with the same name is skipped, since it is already shown.
    """
    local = hosts.Local()
    config = local.run(slurm.CONFIG_COMMAND)
    clusters = []
    local_name = None
    if config is not None:
        local_name = slurm.parse_config(config).get("ClusterName") or None
        profile = profiles.load_profile(local_name or "")
        clusters.append(snapshot.Cluster(local_name or "local", profile, local))
    for profile in profiles.remote_profiles():
        if profile.name != local_name:
            remote = hosts.Remote(profile.ssh)
            clusters.append(snapshot.Cluster(profile.name, profile, remote))
    return clusters


def connect(clusters):
    """Make sure each remote cluster in a text report answers ssh.

    Connections are checked in parallel. When one needs a password or MFA and
    there is a terminal, ssh asks for it here; the control master it opens is
    reused for every later command. (The dashboard logs in when a remote tab
    is opened instead.)
    """
    remotes = [cluster for cluster in clusters if cluster.host.ssh]
    if not remotes:
        return
    with ThreadPoolExecutor(len(remotes)) as pool:
        errors = list(pool.map(lambda cluster: cluster.host.check(), remotes))
    for cluster, error in zip(remotes, errors):
        if error is None or not sys.stdin.isatty():
            continue
        print(
            f"node_state: connecting to {cluster.name} ({cluster.host.ssh}); "
            "ssh may ask for your password or MFA.",
            file=sys.stderr,
        )
        if not cluster.host.login():
            print(f"node_state: could not connect to {cluster.name}.", file=sys.stderr)


def main(argv=None):
    # No options: clusters are detected and configured in clusters.toml, and
    # the probe request is edited in the dashboard. Parsing still rejects
    # stray arguments and serves --help.
    argparse.ArgumentParser(
        prog="node_state",
        description=(
            "Partitions, account/QOS limits and estimated start times for the "
            "Slurm cluster you are logged in to; clusters with an ssh host in "
            "clusters.toml get a tab that connects when opened. Prints a text "
            "report of this cluster when output is not a terminal."
        ),
    ).parse_args(argv)

    try:
        clusters = find_clusters()
    except ValueError as error:
        sys.exit(f"node_state: clusters.toml: {error}")
    if not clusters:
        sys.exit(
            "node_state: Slurm is not available here ('scontrol' failed), and no "
            "cluster in clusters.toml has an ssh host."
        )
    settings = probes.Settings()

    if not sys.stdout.isatty():
        # Like the dashboard at startup, the report covers the cluster this
        # runs on; remote ones only when Slurm is not available here.
        reported = [c for c in clusters if c.host.ssh is None] or clusters
        connect(reported)
        report.print_reports(reported, settings)
        return

    # Textual is only imported for the dashboard, keeping the report fast.
    from .tui import NodeStateApp

    NodeStateApp(clusters, settings).run()


if __name__ == "__main__":
    main()
