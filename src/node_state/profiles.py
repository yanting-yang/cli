"""Per-cluster settings, kept as data so a new cluster needs no code.

Everything else (hardware, partitions, time tiers, accounts, QOS limits) is
read from Slurm. A profile only records what Slurm cannot tell us: which
walltimes are worth probing when partition limits do not reveal them, site
rules about GPU request sizes, and an ssh host to reach the cluster from
elsewhere.

Profiles live in the config file with the email settings (`[mail]` for
Slurm's job emails, `[smtp]` for the dashboard's hourly email):
`config.toml` at the repository root, or the shared `config.example.toml`
beside it, which documents each setting.
"""

import dataclasses
import tomllib
from pathlib import Path

from . import probes

# Job states `--mail-type` accepts (sbatch(1)).
MAIL_TYPES = frozenset(
    {
        "NONE",
        "BEGIN",
        "END",
        "FAIL",
        "REQUEUE",
        "ALL",
        "INVALID_DEPEND",
        "STAGE_OUT",
        "TIME_LIMIT",
        "TIME_LIMIT_90",
        "TIME_LIMIT_80",
        "TIME_LIMIT_50",
        "ARRAY_TASKS",
    }
)

# A symlink to the repository's config.example.toml, so the build copies the
# template into the package and installed copies get the same profiles.
EXAMPLE_PATH = Path(__file__).with_name("config.example.toml")
# Your own settings, beside the template in a checkout. Git-ignored, since
# they include the SMTP password; read instead of the template when present.
CONFIG_PATH = EXAMPLE_PATH.resolve().parent / "config.toml"
SMTP_PORT = 587

GPU_COUNT_MODES = ("pow2", "all", "whole_node")


@dataclasses.dataclass(frozen=True)
class Profile:
    name: str = "generic"
    # Walltimes to probe in minutes; empty derives them from partition limits.
    walltimes: tuple[int, ...] = ()
    # Which GPU counts to probe for each type, up to the per-job ceiling.
    gpu_counts: str = "pow2"
    # ssh destination of a login node, to show this cluster from elsewhere.
    ssh: str | None = None


def config_path():
    """The config file in use: config.toml if you have one, else the template."""
    return CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH


def load_config(path=None):
    """The whole config file (`config_path()` by default), or {} if missing."""
    if path is None:
        path = config_path()
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}


def load_mail(path=None):
    """Return (user, type) from the `[mail]` table, or None without one.

    Every cluster's commands then ask Slurm to email `user` on the job
    states listed in `type` (default BEGIN,END,FAIL).
    """
    mail = load_config(path).get("mail")
    if mail is None:
        return None
    if not isinstance(mail, dict):
        raise ValueError("[mail] must be a table")
    unknown = set(mail) - {"user", "type"}
    if unknown:
        raise ValueError(f"[mail]: unknown settings {sorted(unknown)}")
    user = mail.get("user")
    if not isinstance(user, str) or "@" not in user or " " in user.strip():
        raise ValueError("[mail]: user must be an email address")
    kind = str(mail.get("type", "BEGIN,END,FAIL")).upper().replace(" ", "")
    invalid = set(kind.split(",")) - MAIL_TYPES
    if invalid:
        raise ValueError(f"[mail]: unknown type {sorted(invalid)}")
    return user.strip(), kind


@dataclasses.dataclass(frozen=True)
class Smtp:
    """The server the dashboard's hourly email goes through, and its reader."""

    server: str
    port: int
    sender: str
    recipient: str
    # Kept out of repr, so it never lands in a log or traceback.
    password: str | None = dataclasses.field(default=None, repr=False)


def load_smtp(path=None):
    """Return the `[smtp]` table as `Smtp`, or None without one.

    `port` defaults to 587 (STARTTLS; 465 means SSL), `sender` is also the
    login, and `recipient` defaults to the `[mail]` address.
    """
    smtp = load_config(path).get("smtp")
    if smtp is None:
        return None
    if not isinstance(smtp, dict):
        raise ValueError("[smtp] must be a table")
    unknown = set(smtp) - {field.name for field in dataclasses.fields(Smtp)}
    if unknown:
        raise ValueError(f"[smtp]: unknown settings {sorted(unknown)}")
    server, sender = smtp.get("server"), smtp.get("sender")
    if not isinstance(server, str) or not server.strip():
        raise ValueError("[smtp]: server must be a host name")
    if not isinstance(sender, str) or "@" not in sender:
        raise ValueError("[smtp]: sender must be an email address")
    recipient = smtp.get("recipient")
    if recipient is None:
        mail = load_mail(path)
        recipient = mail and mail[0]
    if not isinstance(recipient, str) or "@" not in recipient:
        raise ValueError("[smtp]: set recipient, or user in [mail]")
    port = smtp.get("port", SMTP_PORT)
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError("[smtp]: port must be a port number")
    password = smtp.get("password")
    if password is not None and not isinstance(password, str):
        raise ValueError("[smtp]: password must be a string")
    return Smtp(
        server.strip(), port, sender.strip(), recipient.strip(), password or None
    )


def load_profiles(path=None):
    """Read the `[clusters.<name>]` tables, or none if the file is missing."""
    clusters = load_config(path).get("clusters", {})
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

    ssh = settings.get("ssh")
    if ssh is not None and (not isinstance(ssh, str) or not ssh.strip()):
        raise ValueError(f"cluster {name!r}: ssh must be a host name or alias")

    return Profile(
        name=name,
        walltimes=tuple(sorted(set(walltimes))),
        gpu_counts=gpu_counts,
        ssh=ssh,
    )


def load_profile(cluster, path=None):
    """Return the profile for `cluster`, or the generic one under its name."""
    settings = load_profiles(path).get(cluster, {})
    return build_profile(cluster or "generic", settings)


def remote_profiles(path=None):
    """Profiles of the clusters reachable over ssh, in file order."""
    profiles = [
        build_profile(name, settings) for name, settings in load_profiles(path).items()
    ]
    return [profile for profile in profiles if profile.ssh]
