import contextlib
import dataclasses
import io
import smtplib
import unittest
from unittest.mock import patch

from rich.console import Console
from textual.widgets import Input

from fakes import FakeHost
from node_state import hosts, probes, profiles, snapshot, tui

from test_report import fake_probe, make_snapshot


class FakeCollector:
    def __init__(self, info, host):
        self.host = host
        self.snap = make_snapshot()
        self.fail = None

    def collect(self):
        if self.host.lost:
            raise hosts.ConnectionLost(self.host.lost)
        if self.fail:
            raise self.fail
        return self.snap


def detect(host):
    if host.lost:
        raise hosts.ConnectionLost(host.lost)
    name = "fir" if host.ssh else "here"
    return snapshot.ClusterInfo(name, "25.05", "me", "login", None, ssh=host.ssh)


class TuiTestCase(unittest.IsolatedAsyncioTestCase):
    """A dashboard with this cluster ("here") and a remote one ("fir")."""

    async def asyncSetUp(self):
        self.contacted = []

        def contact(host):
            self.contacted.append(host.ssh)
            return detect(host)

        for target, name, kwargs in [
            (snapshot.ClusterInfo, "detect", {"side_effect": contact}),
            (snapshot, "Collector", {"side_effect": FakeCollector}),
        ]:
            patcher = patch.object(target, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.here = FakeHost(probe=fake_probe)
        self.fir = FakeHost(probe=fake_probe, ssh="fir.example.org")
        self.app = tui.NodeStateApp(
            [
                snapshot.Cluster("here", profiles.Profile("here"), self.here),
                snapshot.Cluster("fir", profiles.Profile("fir"), self.fir),
            ],
            probes.Settings(),
        )

    @property
    def view(self):
        return self.app.view

    def table(self, table_id):
        return self.view.tables[table_id]

    def detail(self):
        console = Console(width=200, record=True, file=io.StringIO())
        console.print(self.view.detail.content)
        return console.export_text()

    def detail_rows(self, label):
        """The detail lines from the one starting with `label` to the next label."""
        lines = [line.rstrip() for line in self.detail().splitlines()]
        start = next(i for i, line in enumerate(lines) if line.startswith(label))
        rows = [lines[start][len(label) :].strip()]
        for line in lines[start + 1 :]:
            if not line.startswith(" ") or not line.strip():
                break
            rows.append(line.strip())
        return rows

    async def open_tab(self, pilot, index):
        """Click a cluster's tab."""
        await pilot.click(f"#--content-tab-cluster-{index}")

    async def click_detail(self, pilot, text, nth=0):
        """Click the `nth` place the Details panel shows `text`."""
        width = self.view.detail.content_size.width
        console = Console(width=width, record=True, file=io.StringIO())
        console.print(self.view.detail.content)
        hits = [
            (row, line.index(text))
            for row, line in enumerate(console.export_text().splitlines())
            if text in line
        ]
        row, column = hits[nth]
        # Inside the border and one column of padding, on the text's second cell.
        await pilot.click(self.view.detail, offset=(column + 3, row + 1))
        await pilot.pause()

    def last_probe(self):
        return self.here.streamed[-1][0]

    async def settle(self, pilot):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


class CommandLineTests(unittest.TestCase):
    def test_wraps_between_arguments_never_inside_quotes(self):
        command = tui.CommandLine('$ sbatch -c4 --mem=32G --wrap="sleep infinity"')
        console = Console(width=30, record=True, file=io.StringIO())
        console.print(command)

        self.assertEqual(
            console.export_text().splitlines(),
            ["$ sbatch -c4 --mem=32G", '--wrap="sleep infinity"'],
        )


class DashboardTests(TuiTestCase):
    async def test_fills_every_panel(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)

            partitions = self.table("partitions")
            self.assertEqual(partitions.row_count, 2)
            self.assertEqual(
                [str(column.label) for column in partitions.columns.values()][:5],
                ["Partition", "Use", "CPU/node", "Mem/node", "GPU/node"],
            )
            self.assertEqual(
                [str(cell) for cell in partitions.get_row_at(0)[2:5]],
                ["60", "512G", "4x l40s"],
            )
            self.assertEqual(self.table("hardware").row_count, 1)
            self.assertEqual(self.table("scopes").row_count, 2)
            self.assertEqual(self.table("jobs").row_count, 1)
            self.assertIn("gpu", self.detail())

    async def test_probes_the_default_scope_first(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)

            self.assertEqual(self.view.scope.label, "lab/normal")
            estimates = self.table("estimates")
            self.assertEqual(
                [str(column.label) for column in estimates.columns.values()],
                ["Request", "0-03:00:00", "1-00:00:00"],
            )
            self.assertEqual(estimates.row_count, 4)
            # Waits run from the real clock, so only the format is fixed.
            self.assertRegex(
                str(estimates.get_cell("r3", "w1")), r"^\d+-\d\d:\d\d:\d\d$"
            )
            self.assertEqual(str(estimates.get_cell("r3", "w0")), "now")
            self.assertEqual(str(estimates.get_cell("r2", "w0")), "no")

    async def test_lists_jobs_with_start_time_and_nodes(self):
        base = {
            "name": "train",
            "partition": "gpu",
            "account": "lab",
            "qos": "normal",
            "time_limit": "3:00:00",
            "submit": "2026-10-14T08:00:00",
            "cpus": "4",
            "mem": "32G",
            "nodes": "1",
            "tres": "gres/gpu:l40s:1",
        }
        jobs = [
            {
                **base,
                "id": "101",
                "state": "RUNNING",
                "elapsed": "1:02:03",
                "start": "2026-10-14T08:42:05",
                "reason": "None",
                "nodelist": "kn001",
                "sched_nodes": "(null)",
                "time_left": "5:37",
            },
            {
                **base,
                "id": "102",
                "state": "PENDING",
                "elapsed": "0:00",
                "start": "2026-10-15T06:00:00",
                "reason": "Priority",
                "nodelist": "",
                "sched_nodes": "kn134",
                "time_left": "3:00:00",
                "partition": "gpu_a,gpu_b,gpu_c",
            },
        ]
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.view.collector.snap.jobs = jobs
            await pilot.press("r")
            await self.settle(pilot)

            table = self.table("jobs")
            self.assertEqual(
                [str(column.label) for column in table.columns.values()],
                [
                    "Job",
                    "Name",
                    "St",
                    "Partition",
                    "Start",
                    "Time",
                    "Left",
                    "Nodes",
                    "Reason",
                ],
            )
            self.assertEqual(
                [str(cell) for cell in table.get_row("101")],
                [
                    "101",
                    "train",
                    "R",
                    "gpu",
                    "10-14 08:42",
                    "0-01:02:03",
                    "0-00:05:37",
                    "kn001",
                    "-",
                ],
            )
            self.assertEqual(
                [str(cell) for cell in table.get_row("102")],
                [
                    "102",
                    "train",
                    "PD",
                    "gpu_a,gpu_b,gpu…",
                    "10-15 06:00",
                    "-",
                    "0-03:00:00",
                    "kn134",
                    "Priority",
                ],
            )

            await pilot.press("4", "down")
            await pilot.pause()
            self.assertIn("starts 2026-10-15 06:00 planned on kn134", self.detail())
            self.assertEqual(
                self.detail_rows("Attach"),
                ["$ srun --jobid=102 --overlap --pty bash  (once it starts)  Copy"],
            )
            self.assertEqual(self.detail_rows("Release"), ["$ scancel 102  Copy"])
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(
                self.detail_rows("Attach"),
                ["$ srun --jobid=101 --overlap --pty bash  Copy"],
            )
            self.assertEqual(self.detail_rows("Release"), ["$ scancel 101  Copy"])
            # Under 10 minutes left stands out; a pending job's limit is dimmed.
            self.assertEqual(table.get_row("101")[6].style, "bold red")
            self.assertEqual(table.get_row("102")[6].style, "dim")

    async def test_shows_the_probe_its_reply_and_the_run_command(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("3")
            await pilot.pause()

            command, output = self.detail_rows("Probe")
            self.assertTrue(
                command.startswith("$ sbatch --test-only --gres=gpu:l40s:1 ")
            )
            self.assertTrue(command.endswith('--wrap="sleep infinity"  Copy'))
            self.assertIn("sbatch: Job 1 to start at", output)
            (command,) = self.detail_rows("Submit")
            self.assertTrue(command.startswith("$ sbatch --gres=gpu:l40s:1 "))
            self.assertTrue(command.endswith("  Copy"))
            self.assertIn("Estimated start", self.detail())

    async def test_switches_account_qos_and_reprobes(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            table = self.table("estimates")
            self.assertEqual(table._border_title.plain, "lab/normal")

            # The title text starts after "╭─ ".
            await pilot.click(table, offset=(4, 0))
            await self.settle(pilot)

            self.assertEqual(table._border_title.plain, "guests/limited")
            self.assertEqual(self.view.scope.label, "guests/limited")
            self.assertIn("--account=guests", self.last_probe())
            self.assertEqual(self.table("estimates").row_count, 2)

    async def test_selecting_a_scope_row_probes_it(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("2", "home", "enter")
            await self.settle(pilot)

            self.assertEqual(self.view.scope.label, "guests/limited")
            self.assertIn("running jobs", self.detail())

    async def test_edits_the_request(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("e")
            await pilot.pause()
            self.app.screen.query_one("#cpus", Input).value = "8"
            self.app.screen.query_one("#walltimes", Input).value = "2h"
            self.app.screen.query_one("#extra", Input).value = "--partition=gpu"
            await pilot.click("#apply")
            await self.settle(pilot)

            self.assertEqual(self.view.settings.cpus, 8)
            self.assertEqual(self.view.grid.walltimes, [120])
            self.assertIn("-c8", self.last_probe())
            self.assertEqual(self.last_probe()[-2], "--partition=gpu")
            # Each cluster keeps its own request.
            self.assertEqual(self.app.views[1].settings.cpus, 4)

    async def test_keeps_updating_while_the_request_dialog_is_open(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("e")
            await pilot.pause()
            self.app.update_status()
            self.app.refresh_visible()
            self.view.start_probes()
            await self.settle(pilot)
            self.assertIsInstance(self.app.screen, tui.RequestScreen)
            self.assertFalse(self.app.check_action("submit", ()))

            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, tui.RequestScreen)
            self.assertTrue(self.app.check_action("switch_cluster", (1,)))
            self.assertEqual(self.table("estimates").row_count, 4)

    async def test_adds_email_to_commands_and_lets_the_request_drop_it(self):
        self.app = tui.NodeStateApp(
            self.app.clusters, probes.Settings(mail_user="me@example.com")
        )
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            # Probes leave email out; only the command to submit has it.
            self.assertNotIn("--mail-user=me@example.com", self.last_probe())
            self.assertNotIn("email", self.table("estimates").border_subtitle)
            await pilot.press("3")
            await pilot.pause()
            self.assertIn("--mail-user=me@example.com", self.detail_rows("Submit")[0])

            await pilot.press("e")
            await pilot.pause()
            mail = self.app.screen.query_one("#mail", Input)
            self.assertEqual(mail.value, "me@example.com")
            mail.value = ""
            await pilot.click("#apply")
            await self.settle(pilot)

            self.assertIsNone(self.view.settings.mail_user)
            self.assertNotIn(
                "--mail-user=me@example.com", self.view.grid.results[0, 0].command
            )

    async def test_moves_through_the_dialog_with_the_keyboard(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("e")
            await pilot.pause()
            self.assertEqual(self.app.focused.id, "whole-node")

            # Whole node disables CPUs and memory, so Tab skips them.
            await pilot.press("space", "tab")
            self.assertEqual(self.app.focused.id, "walltimes")
            await pilot.press("tab", "tab", "tab", "tab", "enter")
            await self.settle(pilot)

            self.assertNotIsInstance(self.app.screen, tui.RequestScreen)
            self.assertTrue(self.view.settings.whole_node)

    async def test_requests_a_whole_node_from_the_dialog(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("e")
            await pilot.pause()
            await pilot.click("#whole-node")
            await pilot.pause()
            self.assertTrue(self.app.screen.query_one("#cpus", Input).disabled)
            self.assertTrue(self.app.screen.query_one("#mem", Input).disabled)
            await pilot.click("#apply")
            await self.settle(pilot)

            self.assertTrue(self.view.settings.whole_node)
            probe = self.last_probe()
            self.assertIn("--exclusive", probe)
            self.assertIn("--mem=0", probe)
            self.assertFalse(any(arg.startswith("-c") for arg in probe))
            # Only each GPU type's full node count, then CPU only.
            self.assertEqual(
                [request.label for request in self.view.grid.requests],
                ["4x l40s", "CPU only"],
            )
            self.assertIn("whole node", self.table("estimates").border_subtitle)

            # The dialog remembers the choice and can switch it back off.
            await pilot.press("e")
            await pilot.pause()
            self.assertTrue(self.app.screen.query_one("#whole-node").value)
            await pilot.click("#whole-node")
            await pilot.click("#apply")
            await self.settle(pilot)
            self.assertFalse(self.view.settings.whole_node)
            self.assertIn("-c4", self.last_probe())

    async def test_asks_for_whole_gpu_nodes_where_gpus_go_by_node(self):
        whole = profiles.Profile("here", gpu_counts="whole_node")
        self.app = tui.NodeStateApp(
            [snapshot.Cluster("here", whole, self.here)], probes.Settings()
        )
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("3")
            await pilot.pause()

            self.assertIn("GPUs by whole node", self.table("estimates").border_subtitle)
            self.assertIn(
                "--gres=gpu:l40s:4 --exclusive --mem=0 ", self.detail_rows("Submit")[0]
            )

    async def test_rejects_an_invalid_request(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("e")
            await pilot.pause()
            self.app.screen.query_one("#walltimes", Input).value = "soon"
            await pilot.click("#apply")
            await pilot.pause()

            self.assertIsInstance(self.app.screen, tui.RequestScreen)
            error = self.app.screen.query_one("#request-error")
            self.assertIn("invalid walltime", str(error.content))

    async def test_toggles_srun_and_the_nodes_view(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.app.action_toggle_command()  # a click on the command
            await self.settle(pilot)
            self.assertEqual(self.view.settings.command, "srun")
            self.assertEqual(self.last_probe()[0], "srun")

            # The title text starts after "╭─ ".
            await pilot.click(self.table("partitions"), offset=(4, 0))
            await pilot.pause()
            self.assertFalse(self.table("partitions").display)
            self.assertTrue(self.table("hardware").has_focus)

            await pilot.click(self.table("hardware"), offset=(4, 0))
            await pilot.pause()
            self.assertTrue(self.table("partitions").display)
            self.assertFalse(self.table("hardware").display)

    async def test_switches_sbatch_and_srun_from_the_estimates_border(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            table = self.table("estimates")
            plain = "sbatch -c4 --mem=32G"
            self.assertEqual(table._border_subtitle.plain, plain)

            # The subtitle ends two cells before the corner: "… ─╯".
            width, height = table.outer_size
            x = width - 3 - len(plain)
            # Only the command is a link, not the options after it.
            await pilot.click(table, offset=(x + len("sbatch -c"), height - 1))
            await self.settle(pilot)
            self.assertEqual(self.view.settings.command, "sbatch")

            await pilot.click(table, offset=(x, height - 1))
            await self.settle(pilot)

            self.assertEqual(self.view.settings.command, "srun")
            self.assertEqual(self.last_probe()[0], "srun")
            self.assertTrue(table._border_subtitle.plain.startswith("srun "))

    async def test_copies_the_selected_command(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("3")
            await pilot.pause()
            result = self.view.grid.results[0, 0]
            with patch.object(self.app, "copy_to_clipboard") as copy:
                await self.click_detail(pilot, "Copy", 0)
                await self.click_detail(pilot, "Copy", 1)

            self.assertEqual(
                [call.args[0] for call in copy.call_args_list],
                [result.probe, result.command],
            )
            self.assertTrue(result.probe.startswith("sbatch --test-only "))
            self.assertNotIn("--test-only", result.command)

    async def test_copies_and_releases_from_a_jobs_details(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.view.collector.snap.jobs = [
                {
                    **dict.fromkeys(("account", "qos", "submit", "cpus", "mem"), "x"),
                    "id": "101",
                    "name": "train",
                    "state": "RUNNING",
                    "partition": "gpu",
                    "elapsed": "1:00",
                    "time_limit": "3:00:00",
                    "time_left": "2:59:00",
                    "start": "2026-10-14T08:42:05",
                    "reason": "None",
                    "nodelist": "kn001",
                    "sched_nodes": "(null)",
                    "nodes": "1",
                    "tres": "N/A",
                }
            ]
            await pilot.press("r")
            await self.settle(pilot)
            await pilot.press("4")
            await pilot.pause()

            with patch.object(self.app, "copy_to_clipboard") as copy:
                await self.click_detail(pilot, "Copy", 0)
                await self.click_detail(pilot, "Copy", 1)
            self.assertEqual(
                [call.args[0] for call in copy.call_args_list],
                ["srun --jobid=101 --overlap --pty bash", "scancel 101"],
            )

            await self.click_detail(pilot, "Release")
            dialog = self.app.screen
            self.assertIsInstance(dialog, tui.ConfirmScreen)
            self.assertEqual(dialog.question, "Release job 101 (train) on here?")
            self.assertEqual(dialog.command, "$ scancel 101")
            self.assertEqual(self.app.focused.id, "confirm-cancel")
            await pilot.press("escape")
            await self.settle(pilot)
            self.assertEqual(self.here.acted, [])

            self.here.act_reply = (0, "")
            await self.click_detail(pilot, "Release")
            with patch.object(self.app, "notify") as notify:
                await pilot.click("#confirm-ok")
                await self.settle(pilot)
            self.assertEqual(self.here.acted, [["scancel", "101"]])
            self.assertEqual(notify.call_args.args[0], "Released job 101 on here.")

            self.here.act_reply = (1, "scancel: error: Invalid job id specified")
            await self.click_detail(pilot, "Release")
            with patch.object(self.app, "notify") as notify:
                await pilot.click("#confirm-ok")
                await self.settle(pilot)
            self.assertIn(
                "scancel failed: scancel: error: Invalid job id",
                notify.call_args.args[0],
            )

    async def test_survives_a_failed_refresh(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.view.collector.fail = ZeroDivisionError()
            with patch.object(self.view, "notify") as notify:
                await pilot.press("r")
                await self.settle(pilot)

            self.assertTrue(self.app.is_running)
            self.assertIn("refresh failed", notify.call_args.args[0])

    async def test_stacks_the_panels_when_narrow(self):
        async with self.app.run_test(size=(90, 40)) as pilot:
            await self.settle(pilot)
            dashboard = self.view.query_one("#dashboard")
            self.assertTrue(dashboard.has_class("narrow"))

            await pilot.resize_terminal(150, 40)
            await pilot.pause()
            self.assertFalse(dashboard.has_class("narrow"))


class ClusterTabTests(TuiTestCase):
    async def test_shows_each_cluster_in_its_own_tab(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.assertEqual(self.view.cluster.name, "here")

            await self.open_tab(pilot, 1)
            await self.settle(pilot)

            self.assertEqual(self.view.cluster.name, "fir")
            self.assertTrue(self.table("partitions").has_focus)
            self.assertEqual(self.table("partitions").row_count, 2)
            identity = self.view.query_one("#identity").content
            self.assertIn("via ssh fir.example.org", str(identity))
            self.assertTrue(self.fir.streamed)

            await self.open_tab(pilot, 0)
            self.assertEqual(self.view.cluster.name, "here")

    async def test_contacts_a_remote_cluster_only_when_its_tab_opens(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            fir = self.app.views[1]
            self.assertEqual(self.contacted, [None])
            self.assertIsNone(fir.snap)
            self.assertEqual(str(fir.status.content), "not checked yet")
            self.assertIn("checked over ssh (fir.example.org)", str(fir.detail.content))

            self.app.refresh_visible()
            await self.settle(pilot)
            self.assertEqual(self.contacted, [None])

            await self.open_tab(pilot, 1)
            await self.settle(pilot)
            self.assertEqual(self.contacted, [None, "fir.example.org"])

    async def test_logs_in_when_an_opened_remote_tab_needs_it(self):
        self.fir.lost = "Permission denied."
        logins = []

        def login():
            logins.append(True)
            self.fir.lost = None
            return True

        self.fir.login = login
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            with (
                patch.object(self.app, "suspend", contextlib.nullcontext),
                contextlib.redirect_stdout(io.StringIO()) as terminal,
            ):
                await self.open_tab(pilot, 1)
                await self.settle(pilot)
                await self.settle(pilot)

            self.assertEqual(logins, [True])
            self.assertIn("logging in to fir", terminal.getvalue())
            self.assertIsNone(self.view.error)
            self.assertEqual(self.table("partitions").row_count, 2)
            self.assertTrue(self.table("partitions").has_focus)

    async def test_explains_a_failed_login_and_offers_another(self):
        self.fir.lost = "Permission denied (keyboard-interactive)."
        logins = []
        self.fir.login = lambda: logins.append(True) or False
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            with (
                patch.object(self.app, "suspend", contextlib.nullcontext),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                await self.open_tab(pilot, 1)
                await self.settle(pilot)
                await self.settle(pilot)

                self.assertEqual(logins, [True])
                self.assertIn("Cannot reach fir over ssh", self.detail())
                self.assertIn("Permission denied", self.detail())
                self.assertIn("not connected", str(self.view.status.content))
                self.assertTrue(self.view.check_action("login", ()))
                self.assertFalse(self.app.views[0].check_action("login", ()))

                # A refresh that fails again does not prompt by itself...
                self.view.refresh_snapshot()
                await self.settle(pilot)
                self.assertEqual(logins, [True])
                # ...but l does.
                await pilot.press("l")
                await self.settle(pilot)
                self.assertEqual(logins, [True, True])


class SubmitTests(TuiTestCase):
    async def click_submit(self, pilot):
        """Click the Submit label in Details."""
        await self.click_detail(pilot, "Submit")

    async def open_dialog(self, pilot, *keys):
        await self.settle(pilot)
        await pilot.press("3", *keys)
        await pilot.pause()
        await self.click_submit(pilot)

    async def test_submits_a_batch_job_once_confirmed(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.open_dialog(pilot)
            dialog = self.app.screen
            self.assertIsInstance(dialog, tui.ConfirmScreen)
            self.assertTrue(dialog.command.startswith("$ sbatch --gres=gpu:l40s:1 "))
            # Enter on a fresh dialog cancels; submitting is a deliberate click.
            self.assertEqual(self.app.focused.id, "confirm-cancel")

            with patch.object(self.app, "notify") as notify:
                await pilot.click("#confirm-ok")
                await self.settle(pilot)

            result = self.view.grid.results[0, 0]
            self.assertEqual(self.here.acted, [list(result.run)])
            self.assertNotIn("--test-only", result.run)
            self.assertIn("Batch job 77 on here", notify.call_args.args[0])

    async def test_submits_nothing_when_cancelled(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.open_dialog(pilot)
            await pilot.press("enter")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, tui.ConfirmScreen)

            await self.click_submit(pilot)
            self.assertIsInstance(self.app.screen, tui.ConfirmScreen)
            await pilot.press("escape")
            await self.settle(pilot)

            self.assertNotIsInstance(self.app.screen, tui.ConfirmScreen)
            self.assertEqual(self.here.acted, [])

    async def test_refuses_a_request_slurm_rejected(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            with patch.object(self.app, "notify") as notify:
                await self.open_dialog(pilot, "down", "down")

            self.assertNotIsInstance(self.app.screen, tui.ConfirmScreen)
            self.assertIn(
                "would refuse this request: too big", notify.call_args.args[0]
            )

    async def test_says_why_sbatch_failed(self):
        self.here.act_reply = (1, "sbatch: error: QOSMaxSubmitJobPerUserLimit")
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.open_dialog(pilot)
            with patch.object(self.app, "notify") as notify:
                await pilot.click("#confirm-ok")
                await self.settle(pilot)

            self.assertEqual(
                notify.call_args.args[0], "sbatch failed: QOSMaxSubmitJobPerUserLimit"
            )

    async def test_offers_no_submit_for_srun(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            self.app.action_toggle_command()
            await self.settle(pilot)
            await pilot.press("3")
            await pilot.pause()

            self.assertIn("--pty bash", self.detail_rows("Submit")[0])
            self.assertIn("run it in a terminal", self.detail_rows("Submit")[-1])
            await self.click_submit(pilot)
            self.assertNotIsInstance(self.app.screen, tui.ConfirmScreen)
            with patch.object(self.app, "notify") as notify:
                self.app.action_submit()
            self.assertIn("Only sbatch jobs", notify.call_args.args[0])
            self.assertEqual(self.here.acted, [])

    async def test_has_no_keys_for_submitting_or_switching_commands(self):
        keys = {binding.key for binding in tui.ClusterView.BINDINGS}
        self.assertFalse(keys & {"s", "m", "n", "c", "a"})
        app_keys = {binding.key for binding in tui.NodeStateApp.BINDINGS}
        self.assertFalse(app_keys & {"right_square_bracket", "left_square_bracket"})


class MailTests(TuiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.smtp = profiles.Smtp(
            "mail.example.org", 587, "me@example.org", "you@example.org", "secret"
        )
        self.sent = []

        def send_report(targets, smtp):
            self.sent.append(
                [(cluster.name, settings.cpus) for cluster, settings in targets]
            )

        for name, kwargs in [
            ("load_smtp", {"return_value": self.smtp}),
            ("send_report", {"side_effect": send_report}),
        ]:
            target = profiles if name == "load_smtp" else tui.mailer
            patcher = patch.object(target, name, **kwargs)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    @staticmethod
    def messages(notify):
        return [call.args[0] for call in notify.call_args_list]

    async def test_emails_now_and_hourly_until_turned_off(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            with patch.object(self.app, "notify") as notify:
                await pilot.press("M")
                await self.settle(pilot)

                self.assertEqual(self.sent, [[("here", 4)]])
                self.assertIn("Emailed you@example.org.", self.messages(notify))
                self.assertIn("next email", str(self.view.status.content))

                self.app.send_mail()  # what the hourly timer calls
                await self.settle(pilot)
                self.assertEqual(len(self.sent), 2)

                await pilot.press("M")
                await self.settle(pilot)

            self.assertIsNone(self.app.mail_timer)
            self.assertIn("Hourly email off.", self.messages(notify))
            self.assertNotIn("next email", str(self.view.status.content))
            self.load_smtp.assert_called_once_with()

    async def test_adds_remote_tabs_once_opened_with_their_request(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("M")
            await self.settle(pilot)
            await self.open_tab(pilot, 1)
            await self.settle(pilot)
            self.view.settings = dataclasses.replace(self.view.settings, cpus=8)

            self.app.send_mail()
            await self.settle(pilot)

            self.assertEqual(self.sent, [[("here", 4)], [("here", 4), ("fir", 8)]])

    async def test_explains_how_to_set_it_up(self):
        self.load_smtp.return_value = None
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            with patch.object(self.app, "notify") as notify:
                await pilot.press("M")
                await self.settle(pilot)

            self.assertIn("Add an [smtp] table", self.messages(notify)[0])
            self.assertIsNone(self.app.mail_timer)
            self.assertEqual(self.sent, [])

            self.load_smtp.side_effect = ValueError("[smtp]: port must be a number")
            with patch.object(self.app, "notify") as notify:
                await pilot.press("M")
            self.assertIn("port must be a number", self.messages(notify)[0])
            self.assertIsNone(self.app.mail_timer)

    async def test_says_when_an_email_fails_and_keeps_the_schedule(self):
        self.send_report.side_effect = smtplib.SMTPServerDisconnected("closed")
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            with patch.object(self.app, "notify") as notify:
                await pilot.press("M")
                await self.settle(pilot)

            self.assertIn(
                "Could not email you@example.org: closed", self.messages(notify)
            )
            self.assertIn("(last failed)", str(self.view.status.content))
            self.assertIsNotNone(self.app.mail_timer)


if __name__ == "__main__":
    unittest.main()
