import re
import subprocess

from .clusters import rcl, vulcan


CLUSTER_RUNNERS = {
    "rcl": rcl.main,
    "vulcan": vulcan.main,
}


def parse_cluster_name(output):
    match = re.search(r"^ClusterName\s*=\s*(\S+)", output, re.MULTILINE)
    return match.group(1) if match else None


def detect_cluster_name():
    try:
        result = subprocess.run(
            ["scontrol", "show", "config"], capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        print("Error: 'scontrol' command not found. Are you on a Slurm login node?")
        return
    except subprocess.CalledProcessError as e:
        print(f"Error detecting Slurm cluster: {e}")
        return

    cluster_name = parse_cluster_name(result.stdout)
    if cluster_name is None:
        print("Error: could not find ClusterName in 'scontrol show config' output.")
    return cluster_name


def main(exclude_states=None):
    cluster_name = detect_cluster_name()
    if cluster_name is None:
        return

    runner = CLUSTER_RUNNERS.get(cluster_name.casefold())
    if runner is None:
        print(f"Error: unsupported Slurm cluster '{cluster_name}'.")
        return

    runner(exclude_states=exclude_states)
