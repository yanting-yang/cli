import contextlib
import io
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
        for target, name, kwargs in [
            (snapshot.ClusterInfo, "detect", {"side_effect": detect}),
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

    def last_probe(self):
        return self.here.streamed[-1][0]

    async def settle(self, pilot):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


class DashboardTests(TuiTestCase):
    async def test_fills_every_panel(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)

            self.assertEqual(self.table("partitions").row_count, 2)
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

    async def test_shows_the_run_command_for_the_selected_cell(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("3")
            await pilot.pause()

            self.assertIn("$ sbatch --test-only --gres=gpu:l40s:1", self.detail())
            self.assertIn("Estimated start", self.detail())

    async def test_switches_account_qos_and_reprobes(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("a")
            await self.settle(pilot)

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
            self.assertFalse(self.app.check_action("switch_cluster", (1,)))

            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, tui.RequestScreen)
            self.assertTrue(self.app.check_action("switch_cluster", (1,)))
            self.assertEqual(self.table("estimates").row_count, 4)

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
            await pilot.press("m")
            await self.settle(pilot)
            await pilot.press("n")
            await pilot.pause()

            self.assertEqual(self.view.settings.command, "srun")
            self.assertEqual(self.last_probe()[0], "srun")
            self.assertFalse(self.table("partitions").display)
            self.assertTrue(self.table("hardware").has_focus)

    async def test_copies_the_selected_command(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("3")
            with patch.object(self.app, "copy_to_clipboard") as copy:
                await pilot.press("c")

            self.assertTrue(copy.call_args.args[0].startswith("sbatch --test-only"))

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

            await pilot.press("right_square_bracket")
            await self.settle(pilot)

            self.assertEqual(self.view.cluster.name, "fir")
            self.assertTrue(self.table("partitions").has_focus)
            self.assertEqual(self.table("partitions").row_count, 2)
            identity = self.view.query_one("#identity").content
            self.assertIn("via ssh fir.example.org", str(identity))
            self.assertTrue(self.fir.streamed)

            await pilot.press("left_square_bracket")
            self.assertEqual(self.view.cluster.name, "here")

    async def test_explains_an_unreachable_cluster_and_offers_a_login(self):
        self.fir.lost = "Permission denied (keyboard-interactive)."
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("right_square_bracket")
            await self.settle(pilot)

            self.assertIn("Cannot reach fir over ssh", self.detail())
            self.assertIn("Permission denied", self.detail())
            self.assertIn("not connected", str(self.view.status.content))
            self.assertTrue(self.view.check_action("login", ()))
            self.assertFalse(self.app.views[0].check_action("login", ()))

    async def test_logs_in_through_the_terminal_and_loads(self):
        self.fir.lost = "Permission denied."

        def login():
            self.fir.lost = None
            return True

        self.fir.login = login
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            await pilot.press("right_square_bracket")
            await self.settle(pilot)
            with (
                patch.object(self.app, "suspend", contextlib.nullcontext),
                contextlib.redirect_stdout(io.StringIO()) as terminal,
            ):
                await pilot.press("l")
                await self.settle(pilot)

            self.assertIn("logging in to fir", terminal.getvalue())

            self.assertIsNone(self.view.error)
            self.assertEqual(self.table("partitions").row_count, 2)


class BackgroundRefreshTests(TuiTestCase):
    async def test_refreshes_hidden_tabs_without_probing(self):
        async with self.app.run_test(size=(150, 44)) as pilot:
            await self.settle(pilot)
            fir = self.app.views[1]
            probed = len(self.fir.streamed)
            fir.probed_at -= tui.PROBE_MAX_AGE * 2
            fir.snap.taken_at -= tui.STALE_AFTER  # make the old data visibly older

            self.app.refresh_hidden()
            await self.settle(pilot)

            self.assertEqual(len(self.fir.streamed), probed)
            self.assertIsNotNone(fir.snap)

            # Showing the tab probes again, since its estimates are old.
            await pilot.press("right_square_bracket")
            await self.settle(pilot)
            self.assertEqual(len(self.fir.streamed), probed + 1)


if __name__ == "__main__":
    unittest.main()
