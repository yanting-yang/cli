"""Plain-text report: the TUI's panels printed once, for pipes and scripts."""

import datetime
import shlex

from . import hosts, probes, slurm, snapshot, views
from .snapshot import Scope


def print_table(columns, rows):
    widths = [
        max([len(columns[index]), *(len(str(row[index])) for row in rows)])
        for index in range(len(columns))
    ]
    separator = " | "
    header = separator.join(
        column.ljust(width) for column, width in zip(columns, widths)
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            separator.join(str(value).ljust(width) for value, width in zip(row, widths))
        )
    print()


def print_partitions(snap):
    rows = [
        (
            summary["name"] + ("*" if summary["default"] else ""),
            "yes" if summary["usable"] else f"no ({summary['access_note']})",
            f"{views.load_percent(summary)}%",
            views.free_resources(summary, snap.gpu_names),
            views.idle_nodes(summary),
            "?" if summary["pending"] is None else summary["pending"],
            views.max_time(summary),
        )
        for summary in snap.partitions
    ]
    print("Partitions (* default):")
    if not rows:
        print("none reported\n")
        return
    print_table(
        ["Partition", "Usable", "Load", "Free", "Idle nodes", "Pending", "Max time"],
        rows,
    )


def print_hardware(snap):
    rows = [
        (
            group["label"],
            group["node_count"],
            views.state_counts(group["states"]),
            f"{group['cpus']['free']}/{group['cpus']['total']}",
            f"{views.gb(group['mem']['free'])}/{views.gb(group['mem']['total'])}",
            ", ".join(
                f"{counts['free']}/{counts['total']} {snap.gpu_names.get(gpu, gpu)}"
                for gpu, counts in group["gpus"].items()
            )
            or "-",
        )
        for group in snap.hardware
    ]
    print("Nodes by hardware (free/total; CPUs exclude reserved cores):")
    print_table(
        ["Hardware", "Nodes", "States", "CPUs free", "Mem free (GB)", "GPUs free"],
        rows,
    )


def print_scopes(snap, user):
    print("Accounts & QOS (* used when omitted; ∞ no cap, ? unknown):")
    if not snap.scopes:
        print("none found\n")
        return
    rows = []
    for scope in snap.scopes:
        summary = views.scope_summary(snap, scope, user)
        rows.append(
            (
                summary["account"],
                summary["qos"],
                summary["jobs"],
                summary["pending"],
                summary["gpus"],
                summary["wall"],
                summary["fairshare"],
            )
        )
    print_table(
        ["Account", "QOS", "Running/cap", "Pending", "GPU cap", "Wall cap", "FS"], rows
    )
    for scope in snap.scopes:
        rows = views.limit_rows(snap.limits(scope, user))
        print(f"Limits for {scope.label}:")
        if rows:
            print_table(["Scope", "Limit", "Max", "Used"], rows)
        else:
            print("none found\n")


def print_grid(grid, now):
    settings = grid.settings
    extra = f" {shlex.join(settings.extra)}" if settings.extra else ""
    print(
        f"Start estimates for {grid.scope.label} "
        f"({settings.command} -c{settings.cpus} --mem={settings.mem}{extra}), "
        f"waits from {now:%Y-%m-%d %H:%M}:"
    )
    rows = [
        (
            request.label,
            *(
                views.cell(grid, row, column, now)[0]
                for column in range(len(grid.walltimes))
            ),
        )
        for row, request in enumerate(grid.requests)
    ]
    print_table(["Request", *(slurm.format_duration(m) for m in grid.walltimes)], rows)
    if grid.max_wall is not None and any(
        grid.skipped(column) for column in range(len(grid.walltimes))
    ):
        print(
            f"'-' is over the {slurm.format_duration(grid.max_wall)} wall-time cap; "
            "not probed."
        )

    print_reasons(grid)

    example = next(
        (result for _, result in sorted(grid.results.items()) if result.ok), None
    )
    if example is not None:
        print(f"Example: {example.command}")
        print("Drop --test-only to submit; other cells differ only in --gres and -t.")
    print()


def print_reasons(grid):
    """List each rejection reason once, with the requests it applies to.

    A request rejected at every probed walltime is named alone; otherwise
    the walltimes follow it.
    """
    probed = [c for c in range(len(grid.walltimes)) if not grid.skipped(c)]
    reasons = {}
    for (row, column), result in sorted(grid.results.items()):
        if not result.ok:
            reasons.setdefault(result.message, {}).setdefault(row, []).append(column)
    if not reasons:
        return
    print("Cannot run:")
    for message, rows in reasons.items():
        labels = []
        for row, columns in rows.items():
            label = grid.requests[row].label
            if columns != probed:
                walltimes = ", ".join(
                    slurm.format_duration(grid.walltimes[c]) for c in columns
                )
                label += f" at {walltimes}"
            labels.append(label)
        print(f"  {'; '.join(labels)}: {message}")


def print_jobs(snap):
    print("My jobs:")
    if snap.jobs is None:
        print("squeue failed\n")
        return
    if not snap.jobs:
        print("none\n")
        return
    now = datetime.datetime.now()
    print_table(
        ["Job", "Name", "State", "Partition", "Time", "Start / where"],
        [views.job_row(job, now) for job in snap.jobs],
    )


def print_report(info, snap, profile, settings, host):
    where = f" (via ssh {info.ssh})" if info.ssh else ""
    print(
        f"{info.user}@{info.hostname} · {info.name or 'unknown cluster'}{where} · "
        f"Slurm {info.version} · {snap.taken_at:%Y-%m-%d %H:%M:%S}"
    )
    for note in snap.notes:
        print(f"Note: {note}")
    print()
    print_partitions(snap)
    print_hardware(snap)
    print_scopes(snap, info.user)
    for scope in snap.scopes or [Scope()]:
        grid = probes.build_grid(snap, scope, settings, profile, info)
        probes.run_grid(grid, profile, host)
        print_grid(grid, datetime.datetime.now())
    print_jobs(snap)


def print_reports(clusters, settings):
    """One report per cluster, in order; an unreachable cluster says why."""
    for index, cluster in enumerate(clusters):
        if index:
            print("=" * 80)
            print()
        try:
            info = snapshot.ClusterInfo.detect(cluster.host)
            snap = snapshot.Collector(info, cluster.host).collect()
            print_report(info, snap, cluster.profile, settings, cluster.host)
        except hosts.ConnectionLost as error:
            print(
                f"{cluster.name}: cannot connect over ssh ({cluster.host.ssh}): {error}"
            )
            print()
