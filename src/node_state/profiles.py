"""Per-cluster settings, kept as data so a new cluster needs no code.

Everything else (hardware, partitions, time tiers, accounts, QOS limits) is
read from Slurm. A profile only records what Slurm cannot tell us: which
walltimes are worth probing when partition limits do not reveal them, site
rules about GPU request sizes, and submit-filter noise to strip from replies.

Profiles live in `clusters.toml` at the repository root, which documents each
setting.
"""

import dataclasses
import re
import tomllib
from pathlib import Path

from . import probes

# A symlink to the repository's clusters.toml, so the build copies the file
# into the package and installed copies read the same settings as a checkout.
CONFIG_PATH = Path(__file__).with_name("clusters.toml")

GPU_COUNT_MODES = ("pow2", "all", "whole_node")


@dataclasses.dataclass(frozen=True)
class Profile:
    name: str = "generic"
    # Walltimes to probe in minutes; empty derives them from partition limits.
    walltimes: tuple[int, ...] = ()
    # Which GPU counts to probe for each type, up to the per-job ceiling.
    gpu_counts: str = "pow2"
    # Program for srun probes, for submit filters that require one.
    srun_program: tuple[str, ...] = ()
    # Regexes removed from scheduler replies before they are shown.
    message_noise: tuple[str, ...] = ()
    # ssh destination of a login node, to show this cluster from elsewhere.
    ssh: str | None = None


def load_profiles(path=CONFIG_PATH):
    """Read the `[clusters.<name>]` tables, or none if the file is missing."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    clusters = data.get("clusters", {})
    if not isinstance(clusters, dict):
        raise ValueError("[clusters] must be a table")
    return clusters


def build_profile(name, settings):
    """Validate raw settings and return a `Profile`."""
    known = {field.name for field in dataclasses.fields(Profile)} - {"name"}
    unknown = set(settings) - known
    if unknown:
        raise ValueError(f"cluster {name!r}: unknown settings {sorted(unknown)}")

    walltimes = []
    for text in settings.get("walltimes", ()):
        minutes = probes.parse_walltime(str(text))
        if not minutes:
            raise ValueError(f"cluster {name!r}: invalid walltime {text!r}")
        walltimes.append(minutes)

    gpu_counts = settings.get("gpu_counts", "pow2")
    if gpu_counts not in GPU_COUNT_MODES:
        raise ValueError(
            f"cluster {name!r}: gpu_counts must be one of {', '.join(GPU_COUNT_MODES)}"
        )

    noise = tuple(settings.get("message_noise", ()))
    for pattern in noise:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(
                f"cluster {name!r}: invalid message_noise {pattern!r}: {error}"
            ) from None

    ssh = settings.get("ssh")
    if ssh is not None and (not isinstance(ssh, str) or not ssh.strip()):
        raise ValueError(f"cluster {name!r}: ssh must be a host name or alias")

    return Profile(
        name=name,
        walltimes=tuple(sorted(set(walltimes))),
        gpu_counts=gpu_counts,
        srun_program=tuple(settings.get("srun_program", ())),
        message_noise=noise,
        ssh=ssh,
    )


def load_profile(cluster, path=CONFIG_PATH):
    """Return the profile for `cluster`, or the generic one under its name."""
    settings = load_profiles(path).get(cluster, {})
    return build_profile(cluster or "generic", settings)


def remote_profiles(path=CONFIG_PATH):
    """Profiles of the clusters reachable over ssh, in file order."""
    profiles = [
        build_profile(name, settings) for name, settings in load_profiles(path).items()
    ]
    return [profile for profile in profiles if profile.ssh]
