"""Interactive dashboard: partitions, account/QOS limits, start estimates, jobs.

Each cluster gets a tab holding a `ClusterView`. A cluster is contacted only
while its tab is shown: at startup that is the local one, and a remote cluster
is first reached over ssh (see `hosts`) when its tab is opened. The visible
view collects data on a background thread every `REFRESH_SECONDS`, and probes
run on their own worker, filling the estimates grid as replies arrive.
"""

import dataclasses
import datetime
import shlex

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)
from textual.worker import get_current_worker

from . import hosts, probes, slurm, snapshot, views
from .profiles import Profile

REFRESH_SECONDS = 60
# A tab shown again refreshes once its data is this old.
STALE_AFTER = datetime.timedelta(seconds=REFRESH_SECONDS)
# Re-probe on a refresh once the estimates are this old.
PROBE_MAX_AGE = datetime.timedelta(minutes=5)
# Below this width the 2x2 grid cannot fit D-HH:MM:SS columns, so panels stack.
NARROW_WIDTH = 135

CELL_STYLES = {
    "pending": "dim",
    "skipped": "dim",
    "blocked": "red",
    "now": "bold green",
    "hours": "green",
    "days": "yellow",
    "weeks": "dark_orange",
}
JOB_STATE_STYLES = {"RUNNING": "green", "PENDING": "yellow"}


def load_bar(summary, width=8):
    """`███▒▒░░` bar of allocated, unavailable and free capacity, with a %."""
    allocated, free, total = views.main_resource(summary)
    if not total:
        return Text("-", style="dim")
    unavailable = total - allocated - free
    cells = [round(width * value / total) for value in (allocated, unavailable)]
    cells.append(max(0, width - sum(cells)))
    percent = views.load_percent(summary)
    style = "red" if percent >= 90 else "yellow" if percent >= 70 else "green"
    bar = Text()
    bar.append("█" * cells[0], style=style)
    bar.append("▒" * cells[1], style="grey42")
    bar.append("░" * cells[2], style="grey35")
    bar.append(f" {percent:>3}%")
    return bar


class Dashboard(Grid):
    """The four panels: a 2x2 grid, or a scrolling stack when narrow."""

    def on_resize(self, event):
        self.set_class(event.size.width < NARROW_WIDTH, "narrow")


class RequestScreen(ModalScreen):
    """Edit the resources every probe asks for."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        settings = self.settings
        walltimes = ", ".join(slurm.format_duration(m) for m in settings.walltimes)
        with Vertical(id="request-dialog"):
            yield Label("Probe request", classes="dialog-title")
            yield Label("CPUs per task")
            yield Input(str(settings.cpus), id="cpus", type="integer")
            yield Label("Memory")
            yield Input(settings.mem, id="mem", placeholder="32G")
            yield Label("Walltimes (blank: one per partition time limit)")
            yield Input(walltimes, id="walltimes", placeholder="0-03:00:00, 1-00:00:00")
            yield Label("Extra options for every probe")
            yield Input(
                shlex.join(settings.extra),
                id="extra",
                placeholder="--partition=name --constraint=feature",
            )
            yield Label("", id="request-error")
            with Horizontal(id="request-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Probe", id="apply", variant="primary")

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#apply")
    def apply(self):
        try:
            cpus = int(self.query_one("#cpus", Input).value)
            if cpus < 1:
                raise ValueError("CPUs must be at least 1")
            settings = probes.Settings(
                command=self.settings.command,
                cpus=cpus,
                mem=self.query_one("#mem", Input).value.strip() or "32G",
                extra=tuple(shlex.split(self.query_one("#extra", Input).value)),
                walltimes=probes.parse_walltimes(
                    self.query_one("#walltimes", Input).value
                ),
            )
        except ValueError as error:
            self.query_one("#request-error", Label).update(Text(str(error), "red"))
            return
        self.dismiss(settings)


class ClusterView(Vertical):
    """One cluster's dashboard: identity line, four panels and details.

    The view owns its cluster's state (snapshot, probe request, grid), so
    each tab keeps its own selection and request. Its key bindings apply
    while focus is inside it.
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("p", "probe", "Re-probe"),
        Binding("a", "next_scope", "Account/QOS"),
        Binding("e", "edit_request", "Edit request"),
        Binding("m", "toggle_command", "sbatch/srun"),
        Binding("c", "copy", "Copy command"),
        Binding("n", "toggle_nodes", "Nodes"),
        Binding("l", "login", "Log in"),
        Binding("1", "focus_table('partitions')", show=False),
        Binding("2", "focus_table('scopes')", show=False),
        Binding("3", "focus_table('estimates')", show=False),
        Binding("4", "focus_table('jobs')", show=False),
    ]

    def __init__(self, cluster, settings, **kwargs):
        super().__init__(**kwargs)
        self.cluster = cluster
        self.host = cluster.host
        self.profile = cluster.profile
        self.settings = settings
        self.info = None
        self.collector = None
        self.snap = None
        self.scope = None
        self.grid = None
        self.probed_at = None
        self.error = None
        self.detail_from = "partitions"
        # Set when the tab is opened: if that first refresh cannot connect,
        # ssh gets the terminal to ask for a password or MFA.
        self.login_on_failure = False

    # --- Layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(self.identity(), id="identity")
            yield Static("loading…", id="status")
        with Dashboard(id="dashboard"):
            with Container(id="left-top"):
                yield self.table("partitions", "Partitions", "row")
                yield self.table("hardware", "Nodes by hardware", "row")
            yield self.table("scopes", "Accounts & QOS", "row")
            yield self.table("estimates", "Start estimates", "cell")
            yield self.table("jobs", "My jobs", "row")
        yield Static(id="detail")

    @staticmethod
    def table(table_id, title, cursor):
        table = DataTable(id=table_id, cursor_type=cursor, zebra_stripes=True)
        table.border_title = title
        return table

    def identity(self):
        text = Text()
        info = self.info
        if info is None:
            text.append(self.cluster.name, style="bold cyan")
        else:
            text.append(f"{info.user}@{info.hostname}", style="bold")
            text.append(f"  {info.name or self.cluster.name}", style="bold cyan")
            text.append(f"  Slurm {info.version}", style="dim")
        if self.host.ssh:
            text.append(f"  via ssh {self.host.ssh}", style="dim")
        if self.profile != Profile(name=self.profile.name, ssh=self.profile.ssh):
            text.append(f"  profile {self.profile.name}", style="dim")
        return text

    def on_mount(self):
        self.tables = {table.id: table for table in self.query(DataTable)}
        self.status = self.query_one("#status", Static)
        self.detail = self.query_one("#detail", Static)
        self.tables["hardware"].display = False
        self.detail.border_title = "Details"
        self.tables["partitions"].add_columns(
            "Partition", "Use", "Load", "Free", "Idle", "Pend", "Max"
        )
        self.tables["hardware"].add_columns(
            "Hardware", "Nodes", "States", "CPUs free", "Mem free (GB)", "GPUs free"
        )
        self.tables["scopes"].add_columns(
            "Account", "QOS", "Jobs", "GPUs", "Wall", "FS"
        )
        self.tables["jobs"].add_columns("Job", "Name", "St", "Time", "Start / where")
        # Nothing is fetched until the tab is opened (`activate`).
        if self.host.ssh:
            self.status.update("not checked yet")
            self.detail.update(
                Text(
                    f"{self.cluster.name} is checked over ssh ({self.host.ssh}) "
                    "when you open this tab.",
                    style="dim",
                )
            )

    def activate(self):
        """The tab was opened: fetch what is missing or old, then take focus."""
        # The pane is shown on the next refresh; a hidden table cannot take focus.
        self.app.call_after_refresh(self.focus_main)
        if self.stale or self.error is not None:
            self.login_on_failure = bool(self.host.ssh)
            self.refresh_snapshot()
        elif self.probes_stale:
            self.start_probes()

    def focus_main(self):
        """Focus the panel the details pane follows."""
        table = self.tables.get(self.detail_from)
        if table is not None and table.display:
            table.focus()

    def loaded(self):
        """Clear the loading state; tables that were loading could not take focus."""
        for table in self.tables.values():
            table.loading = False
        if self.app.view is self and not self.has_focus_within:
            self.focus_main()

    # --- Data ----------------------------------------------------------------

    @property
    def stale(self):
        return self.snap is None or (
            datetime.datetime.now() - self.snap.taken_at >= STALE_AFTER
        )

    @property
    def probes_stale(self):
        return self.probed_at is None or (
            datetime.datetime.now() - self.probed_at > PROBE_MAX_AGE
        )

    def refresh_snapshot(self):
        """Collect in the background, unless a collection is still running."""
        busy = any(
            worker.node is self
            and worker.group == "snapshot"
            and not worker.is_finished
            for worker in self.app.workers
        )
        if busy:
            return
        if self.snap is None:
            self.status.update("loading…")
            for table in self.tables.values():
                table.loading = True
        self.collect()

    @work(thread=True, group="snapshot")
    def collect(self):
        try:
            if self.collector is None:
                info = snapshot.ClusterInfo.detect(self.host)
                self.collector = snapshot.Collector(info, self.host)
                self.app.call_from_thread(self.detected, info)
            snap = self.collector.collect()
        except hosts.ConnectionLost as error:
            self.app.call_from_thread(self.disconnected, str(error))
            return
        except Exception as error:  # Keep the dashboard up; say what broke.
            self.app.call_from_thread(
                self.notify,
                f"{self.cluster.name}: refresh failed: {error!r}",
                severity="error",
            )
            return
        self.app.call_from_thread(self.apply_snapshot, snap)

    def detected(self, info):
        self.info = info
        self.query_one("#identity", Static).update(self.identity())

    def disconnected(self, message):
        """Show why ssh failed; the last data, if any, stays on screen.

        Right after the tab is opened, ssh then gets the terminal to log in.
        """
        self.error = message
        self.loaded()
        self.update_status()
        self.show_detail()
        if self.login_on_failure:
            self.login_on_failure = False
            self.call_after_refresh(self.action_login)

    def apply_snapshot(self, snap):
        first = self.snap is None
        self.snap = snap
        self.error = None
        self.login_on_failure = False
        self.fill_partitions()
        self.fill_hardware()
        self.fill_jobs()
        if self.scope not in snap.scopes:
            self.scope = snap.preferred_scope()
        self.fill_scopes()
        if self.probes_stale:
            self.start_probes()
        elif self.grid is not None:
            self.fill_estimates()
        self.loaded()
        if first:
            for note in snap.notes:
                self.notify(
                    f"{self.cluster.name}: {note}", severity="warning", timeout=10
                )
        self.show_detail()
        self.update_status()

    def update_status(self):
        if self.error is not None:
            status = Text("not connected", style="bold red")
            if self.snap is not None:
                status.append(f" · last data {self.snap.taken_at:%H:%M:%S}", "dim")
            self.status.update(status)
            return
        if self.snap is None:
            return
        age = int((datetime.datetime.now() - self.snap.taken_at).total_seconds())
        status = f"updated {self.snap.taken_at:%H:%M:%S} ({age}s ago)"
        if self.probed_at is not None:
            status += f" · probed {self.probed_at:%H:%M:%S}"
        self.status.update(status)

    # --- Tables --------------------------------------------------------------

    @staticmethod
    def refill(table, rows):
        """Replace the rows of `table`, keeping the cursor on the same row."""
        row = table.cursor_row
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1), scroll=False)

    def fill_partitions(self):
        rows = []
        for summary in self.snap.partitions:
            usable = Text("yes", "green") if summary["usable"] else Text("no", "red")
            pending = summary["pending"]
            rows.append(
                (
                    summary["name"],
                    (
                        Text(summary["name"] + ("*" if summary["default"] else "")),
                        usable,
                        load_bar(summary),
                        views.free_resources(summary, self.snap.gpu_names),
                        views.idle_nodes(summary),
                        "?" if pending is None else str(pending),
                        views.max_time(summary),
                    ),
                )
            )
        self.refill(self.tables["partitions"], rows)

    def fill_hardware(self):
        rows = []
        for index, group in enumerate(self.snap.hardware):
            names = self.snap.gpu_names
            gpus = ", ".join(
                f"{counts['free']}/{counts['total']} {names.get(gpu, gpu)}"
                for gpu, counts in group["gpus"].items()
            )
            mem = group["mem"]
            rows.append(
                (
                    str(index),
                    (
                        group["label"],
                        str(group["node_count"]),
                        views.state_counts(group["states"]),
                        f"{group['cpus']['free']}/{group['cpus']['total']}",
                        f"{views.gb(mem['free'])}/{views.gb(mem['total'])}",
                        gpus or "-",
                    ),
                )
            )
        self.refill(self.tables["hardware"], rows)

    def fill_scopes(self):
        rows = []
        for index, scope in enumerate(self.snap.scopes):
            summary = views.scope_summary(self.snap, scope, self.info.user)
            # The pair being probed stands out; the others are plain.
            style = "bold cyan" if scope == self.scope else ""
            rows.append(
                (
                    str(index),
                    (
                        Text(summary["account"], style),
                        Text(summary["qos"], style),
                        summary["jobs"],
                        summary["gpus"],
                        summary["wall"],
                        summary["fairshare"],
                    ),
                )
            )
        table = self.tables["scopes"]
        self.refill(table, rows)
        table.border_subtitle = "* left out of commands · enter: probe"

    def fill_jobs(self):
        table = self.tables["jobs"]
        jobs = self.snap.jobs
        now = datetime.datetime.now()
        if not jobs:
            message = "squeue failed" if jobs is None else "No jobs in the queue"
            self.refill(table, [("none", (Text(message, "dim"), "", "", "", ""))])
            return
        rows = []
        for job in jobs:
            state = job["state"]
            name = job["name"] if len(job["name"]) <= 16 else job["name"][:15] + "…"
            elapsed = (
                slurm.normalize_time(job["elapsed"]) if state != "PENDING" else "-"
            )
            rows.append(
                (
                    job["id"],
                    (
                        job["id"],
                        name,
                        Text(views.short_state(state), JOB_STATE_STYLES.get(state, "")),
                        elapsed,
                        views.job_start(job, now),
                    ),
                )
            )
        self.refill(table, rows)

    def fill_estimates(self):
        table = self.tables["estimates"]
        grid = self.grid
        cursor = table.cursor_coordinate
        table.clear(columns=True)
        table.fixed_columns = 1
        table.add_column("Request", key="request")
        for column, minutes in enumerate(grid.walltimes):
            # Wide enough for any wait ("365-08:37:00") before results arrive.
            table.add_column(slurm.format_duration(minutes), key=f"w{column}", width=12)
        now = datetime.datetime.now()
        for row, request in enumerate(grid.requests):
            cells = [
                self.estimate_cell(row, column, now)
                for column in range(len(grid.walltimes))
            ]
            table.add_row(request.label, *cells, key=f"r{row}")
        if table.row_count:
            table.move_cursor(
                row=min(cursor.row, table.row_count - 1),
                column=max(1, min(cursor.column, len(grid.walltimes))),
                scroll=False,
            )
        settings = grid.settings
        table.border_title = f"Start estimates · {grid.scope.label}"
        extra = f" {shlex.join(settings.extra)}" if settings.extra else ""
        table.border_subtitle = (
            f"{settings.command} -c{settings.cpus} --mem={settings.mem}{extra}"
        )

    def estimate_cell(self, row, column, now):
        text, kind = views.cell(self.grid, row, column, now)
        style = CELL_STYLES[kind]
        if column in views.earliest_columns(self.grid, row):
            style += " underline"
        return Text(text, style, justify="right")

    # --- Probes --------------------------------------------------------------

    def start_probes(self):
        if self.snap is None:
            return
        self.grid = probes.build_grid(
            self.snap, self.scope, self.settings, self.profile, self.info
        )
        self.probed_at = datetime.datetime.now()
        self.fill_estimates()
        self.fill_scopes()
        self.run_probes(self.grid)

    @work(thread=True, exclusive=True, group="probes")
    def run_probes(self, grid):
        worker = get_current_worker()

        def report(row, column, result):
            self.app.call_from_thread(self.probe_landed, grid, row, column)

        try:
            probes.run_grid(
                grid, self.profile, self.host, report, lambda: worker.is_cancelled
            )
        except hosts.ConnectionLost as error:
            self.app.call_from_thread(self.disconnected, str(error))
        except Exception as error:  # Keep the dashboard up; say what broke.
            self.app.call_from_thread(
                self.notify,
                f"{self.cluster.name}: probes failed: {error!r}",
                severity="error",
            )

    def probe_landed(self, grid, row, column):
        if grid is not self.grid:
            return
        table = self.tables["estimates"]
        now = datetime.datetime.now()
        # The earliest start in the row may move, so restyle the whole row.
        for index in range(len(grid.walltimes)):
            table.update_cell(
                f"r{row}", f"w{index}", self.estimate_cell(row, index, now)
            )
        selected = table.cursor_coordinate == (row, column + 1)
        if self.detail_from == "estimates" and selected:
            self.show_detail()

    # --- Details -------------------------------------------------------------

    def on_descendant_focus(self, event):
        if isinstance(event.widget, DataTable):
            self.detail_from = event.widget.id
            self.show_detail()

    @on(DataTable.RowHighlighted)
    @on(DataTable.CellHighlighted)
    def highlighted(self, event):
        if event.data_table.id == self.detail_from:
            self.show_detail()

    @on(DataTable.RowSelected, "#scopes")
    def scope_selected(self, event):
        index = int(event.row_key.value)
        self.select_scope(self.snap.scopes[index])

    def show_detail(self):
        detail = self.detail
        if self.error is not None:
            detail.update(self.connection_detail())
            return
        if self.snap is None:
            return
        source = self.detail_from
        table = self.tables[source]
        if not table.row_count:
            detail.update(self.notes_text())
            return
        row = table.cursor_row
        if source == "partitions":
            detail.update(self.partition_detail(self.snap.partitions[row]))
        elif source == "hardware":
            detail.update(self.hardware_detail(self.snap.hardware[row]))
        elif source == "scopes":
            detail.update(self.scope_detail(self.snap.scopes[row]))
        elif source == "estimates" and self.grid is not None:
            detail.update(self.estimate_detail(row, table.cursor_column - 1))
        elif source == "jobs" and self.snap.jobs:
            detail.update(self.job_detail(self.snap.jobs[row]))
        else:
            detail.update(self.notes_text())

    def connection_detail(self):
        title = Text(f"Cannot reach {self.cluster.name} over ssh ", style="bold red")
        title.append(f"({self.host.ssh})", style="red")
        return Group(
            title,
            Text(self.error),
            Text(
                "Press l to log in (ssh asks for your password or MFA in this "
                "terminal), then the connection is reused for every refresh.",
                style="dim",
            ),
        )

    def notes_text(self):
        notes = self.snap.notes if self.snap else []
        return Text("\n".join(notes) or "", style="yellow")

    def partition_detail(self, summary):
        cpus = summary["cpus"]
        mem = summary["mem"]

        def allow(names):
            return "ALL" if names is None else ", ".join(sorted(names))

        header = Text()
        header.append(summary["name"], style="bold")
        header.append(f"  {summary['state']}")
        if summary["usable"]:
            header.append("  usable", style="green")
        else:
            header.append(f"  not usable: {summary['access_note']}", style="red")
        header.append(f"  · max time {views.max_time(summary)}")
        if summary["default_time"]:
            default = slurm.format_duration(summary["default_time"])
            header.append(f" · default {default}")
        if summary["qos"]:
            header.append(f" · partition QOS {summary['qos']}")
        header.append(f" · priority tier {summary['priority_tier']}")
        if summary.get("preempt_mode", "OFF") != "OFF":
            header.append(f" · preemptible ({summary['preempt_mode']})", "yellow")
        pending = "?" if summary["pending"] is None else summary["pending"]
        lines = [
            header,
            Text(
                f"Nodes {summary['node_count']} "
                f"({views.state_counts(summary['states'])}) · pending jobs {pending}"
            ),
            Text(
                f"CPUs {cpus['free']} free / {cpus['allocated']} allocated / "
                f"{cpus.get('unavailable', 0)} unavailable of {cpus['total']} · "
                f"Memory {views.gb(mem['free'])} GB free of {views.gb(mem['total'])} GB"
            ),
        ]
        for gpu, counts in summary["gpus"].items():
            lines.append(
                Text(
                    f"GPUs {self.snap.gpu_names.get(gpu, gpu)}: {counts['free']} free / {counts['allocated']} "
                    f"allocated / {counts.get('unavailable', 0)} unavailable "
                    f"of {counts['total']}"
                )
            )
        lines.append(
            Text(
                f"Allowed accounts {allow(summary['allow_accounts'])} · QOS "
                f"{allow(summary['allow_qos'])} · groups "
                f"{allow(summary['allow_groups'])}",
                style="dim",
            )
        )
        return Group(*lines)

    def hardware_detail(self, group):
        names = ", ".join(group["names"][:40])
        if len(group["names"]) > 40:
            names += f", … ({len(group['names'])} nodes)"
        return Group(
            Text(group["label"], style="bold"),
            Text(views.state_counts(group["states"])),
            Text(names, style="dim"),
        )

    def scope_detail(self, scope):
        table = Table(box=None, pad_edge=False, expand=False, header_style="bold")
        for column in ("Scope", "Limit", "Max", "Used"):
            table.add_column(
                column, justify="right" if column in ("Max", "Used") else "left"
            )
        for row in views.limit_rows(self.snap.limits(scope, self.info.user)):
            table.add_row(*row)
        summary = views.scope_summary(self.snap, scope, self.info.user)
        caption = Text(f"{scope.label}", style="bold")
        caption.append(
            f"  your jobs: {summary['jobs']} running/cap, {summary['pending']} pending"
        )
        flags = " ".join(scope.directives()) or "no --account/--qos needed"
        caption.append(f" · {flags} · ∞ no cap · ? unknown", style="dim")
        if not table.row_count:
            return Group(caption, Text("No limits found for this QOS.", "dim"))
        return Group(caption, table)

    def estimate_detail(self, row, column):
        grid = self.grid
        if column < 0:
            return Text(
                "Rows are request sizes; columns are walltimes. Cells show how long "
                "until the scheduler expects the job to start.",
                style="dim",
            )
        request = grid.requests[row]
        walltime = slurm.format_duration(grid.walltimes[column])
        title = Text(f"{request.label} for {walltime}", style="bold")
        title.append(f"  {grid.scope.label}", style="dim")
        if grid.skipped(column):
            cap = slurm.format_duration(grid.max_wall)
            return Group(
                title,
                Text(
                    f"Not probed: over the {cap} wall-time cap of this account/QOS.",
                    "dim",
                ),
            )
        result = grid.results.get((row, column))
        if result is None:
            return Group(title, Text("Probing…", "dim"))
        lines = [title]
        if result.ok:
            now = datetime.datetime.now()
            line = Text("Estimated start ", style="green")
            line.append(f"{result.start:%Y-%m-%d %H:%M}", style="bold green")
            line.append(f" ({probes.format_wait(result.start, now)})")
            if result.partition:
                line.append(f" in partition {result.partition}")
            lines.append(line)
        else:
            lines.append(Text(f"Cannot run: {result.message}", style="red"))
        prompt = f"{self.host.ssh}$" if self.host.ssh else "$"
        lines.append(Text(f"{prompt} {result.command}", style="bold"))
        if grid.settings.command == "srun":
            usage = "drop --test-only to open an interactive shell on the allocation."
        else:
            usage = (
                "drop --test-only to submit. The job holds the allocation with "
                "'sleep infinity'; open a shell in it with "
                "'srun --jobid=<id> --overlap --pty $SHELL', release it with "
                "'scancel <id>'."
            )
        if self.host.ssh:
            usage = f"run it on {self.cluster.name}; {usage}"
        lines.append(Text(f"c copies it; {usage}", style="dim"))
        return Group(*lines)

    def job_detail(self, job):
        state = Text(job["state"], JOB_STATE_STYLES.get(job["state"], ""))
        header = Text.assemble(
            (f"{job['id']} ", "bold"),
            (job["name"], "bold"),
            "  ",
            state,
            f" ({job['reason']})" if job["reason"] not in ("None", "") else "",
        )
        return Group(
            header,
            Text(
                f"{job['account']}/{job['qos']} in {job['partition']} · "
                f"submitted {job['submit']} · start {job['start']}"
            ),
            Text(
                f"{job['cpus']} CPUs · {job['mem']} · {job['nodes']} node(s) · "
                f"{job['tres'] if job['tres'] != 'N/A' else 'no GRES'} · "
                f"elapsed {slurm.normalize_time(job['elapsed'])} of "
                f"{slurm.normalize_time(job['time_limit'])}"
            ),
            Text(job["nodelist"] or "", style="dim"),
        )

    # --- Actions -------------------------------------------------------------

    def check_action(self, action, parameters):
        """Logging in only applies to clusters reached over ssh."""
        if action == "login":
            return self.host.ssh is not None
        return True

    def action_refresh(self):
        self.probed_at = None
        self.refresh_snapshot()

    def action_probe(self):
        self.start_probes()

    def select_scope(self, scope):
        self.scope = scope
        self.start_probes()
        self.notify(f"Probing {self.cluster.name} as {scope.label}")

    def action_next_scope(self):
        scopes = self.snap.scopes if self.snap else []
        if len(scopes) < 2:
            self.notify("No other account/QOS to probe with.")
            return
        index = scopes.index(self.scope) if self.scope in scopes else -1
        self.select_scope(scopes[(index + 1) % len(scopes)])

    def action_toggle_command(self):
        command = "srun" if self.settings.command == "sbatch" else "sbatch"
        self.settings = dataclasses.replace(self.settings, command=command)
        self.start_probes()

    def action_edit_request(self):
        def done(settings):
            if settings is not None:
                self.settings = settings
                self.start_probes()

        self.app.push_screen(RequestScreen(self.settings), done)

    def action_copy(self):
        table = self.tables["estimates"]
        result = None
        if self.grid is not None and table.cursor_column > 0:
            result = self.grid.results.get((table.cursor_row, table.cursor_column - 1))
        if result is None:
            self.notify("Select a probed cell in Start estimates first.")
            return
        self.app.copy_to_clipboard(result.command)
        self.notify(result.command, title="Copied")

    def action_toggle_nodes(self):
        partitions = self.tables["partitions"]
        hardware = self.tables["hardware"]
        showing = partitions.display
        partitions.display = not showing
        hardware.display = showing
        (hardware if showing else partitions).focus()

    def action_focus_table(self, table_id):
        if table_id == "partitions" and not self.tables["partitions"].display:
            table_id = "hardware"
        self.tables[table_id].focus()

    def action_login(self):
        """Hand the terminal to ssh so it can ask for a password or MFA."""
        try:
            with self.app.suspend():
                print(
                    f"node_state: logging in to {self.cluster.name} "
                    f"({self.host.ssh}); the dashboard returns afterwards."
                )
                connected = self.host.login()
        except Exception as error:  # SuspendNotSupported, e.g. in a web terminal
            self.notify(f"Cannot log in from here: {error!r}", severity="error")
            return
        if not connected:
            self.notify(f"Could not connect to {self.cluster.name}.", severity="error")
            return
        self.action_refresh()


class NodeStateApp(App):
    TITLE = "node_state"
    CSS = """
    TabbedContent, ContentSwitcher, TabPane, ClusterView { height: 1fr; }
    TabPane { padding: 0; }
    #topbar { height: 1; background: $panel; padding: 0 1; }
    #identity { width: 1fr; }
    #status { width: auto; color: $text-muted; }
    #dashboard {
        grid-size: 2 2;
        grid-columns: 3fr 2fr;
        grid-rows: 1fr 1fr;
        height: 1fr;
    }
    #dashboard.narrow {
        grid-size: 1;
        grid-columns: 1fr;
        grid-rows: auto;
        overflow-y: auto;
    }
    #dashboard.narrow #left-top { height: auto; }
    #dashboard.narrow DataTable { height: auto; max-height: 16; }
    #left-top { height: 1fr; }
    DataTable {
        height: 1fr;
        border: round $border-blurred;
        border-title-color: $text-muted;
        scrollbar-size: 1 1;
    }
    DataTable:focus { border: round $border; border-title-color: $text; }
    #detail {
        height: 10;
        border: round $border-blurred;
        border-title-color: $text-muted;
        padding: 0 1;
        overflow-y: auto;
    }
    RequestScreen { align: center middle; }
    #request-dialog {
        width: 64;
        height: auto;
        border: round $border;
        background: $surface;
        padding: 1 2;
    }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    #request-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #request-buttons Button { margin-left: 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("right_square_bracket", "switch_cluster(1)", "Next cluster"),
        Binding("left_square_bracket", "switch_cluster(-1)", "Previous cluster"),
        Binding("question_mark", "toggle_help", "Keys"),
    ]

    def __init__(self, clusters, settings):
        super().__init__()
        self.clusters = clusters
        self.settings = settings
        self.theme = "tokyo-night"

    def compose(self) -> ComposeResult:
        with TabbedContent(id="clusters"):
            for index, cluster in enumerate(self.clusters):
                with TabPane(cluster.name, id=f"cluster-{index}"):
                    yield ClusterView(cluster, self.settings)
        yield Footer()

    def on_mount(self):
        # App.query_one searches the screen on top, which is the request dialog
        # while it is open, so keep direct references to the dashboard widgets.
        self.tabs = self.query_one(TabbedContent)
        self.views = list(self.query(ClusterView))
        self.set_interval(REFRESH_SECONDS, self.refresh_visible)
        self.set_interval(1, self.update_status)
        self.view.activate()

    @property
    def view(self):
        """The cluster view on the visible tab."""
        active = self.tabs.active or "cluster-0"
        return self.views[int(active.removeprefix("cluster-"))]

    def refresh_visible(self):
        """Only the visible cluster refreshes, probing when its estimates are old.

        Hidden tabs are left alone; opening one refreshes it if needed.
        """
        if self.view.snap is not None:
            self.view.refresh_snapshot()

    def update_status(self):
        self.view.update_status()

    @on(TabbedContent.TabActivated)
    def tab_activated(self, event):
        # The first tab is activated in on_mount, once the views are known.
        if getattr(self, "views", None):
            self.view.activate()

    def check_action(self, action, parameters):
        """While the request dialog is open, only quitting reaches the dashboard."""
        return action == "quit" or not isinstance(self.screen, RequestScreen)

    def action_switch_cluster(self, step):
        index = self.views.index(self.view)
        self.tabs.active = f"cluster-{(index + step) % len(self.views)}"

    def action_toggle_help(self):
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()
