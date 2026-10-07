import datetime
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from fakes import FakeHost, scheduler_reply

from node_state import probes, profiles, report, slurm, snapshot
from node_state.snapshot import Scope

from test_snapshot import ASSOC_MGR, node, partition

NOW = datetime.datetime(2026, 10, 7, 12, 0)


def make_snapshot(jobs=()):
    nodes = [node("a"), node("b", state="IDLE+DRAIN")]
    scopes = [
        Scope("guests", "limited", False, True),
        Scope("lab", "normal", True, True, user_default=True),
    ]
    return snapshot.Snapshot(
        taken_at=NOW,
        nodes=nodes,
        partitions=snapshot.summarize_partitions(
            [partition("gpu", default=True), partition("long", max_time=1440)],
            nodes,
            {"gpu": 12},
            scopes,
            None,
        ),
        hardware=snapshot.summarize_hardware(nodes),
        scopes=scopes,
        qos_records=slurm.parse_qos_records(ASSOC_MGR),
        associations=slurm.parse_associations(ASSOC_MGR),
        jobs=list(jobs),
        notes=["sacctmgr unavailable"],
    )


def fake_probe(args):
    """4 L40S never fits; 3-hour jobs start at NOW, longer ones 30 hours later."""
    if "--gres=gpu:l40s:4" in args:
        return scheduler_reply(error="error: too big.")
    hours = 0 if "-t0-03:00:00" in args else 30
    return scheduler_reply((NOW + datetime.timedelta(hours=hours)).isoformat())


class ReportTests(unittest.TestCase):
    def render(self, snap):
        info = snapshot.ClusterInfo("c", "25.05", "me", "login", None)
        output = StringIO()
        with (
            patch.object(report.datetime, "datetime", wraps=datetime.datetime) as clock,
            redirect_stdout(output),
        ):
            clock.now.return_value = NOW
            report.print_report(
                info,
                snap,
                profiles.Profile("c"),
                probes.Settings(),
                FakeHost(probe=fake_probe),
            )
        return output.getvalue()

    def test_prints_every_section(self):
        text = self.render(make_snapshot())

        self.assertIn("me@login · c · Slurm 25.05", text)
        self.assertIn("Note: sacctmgr unavailable", text)
        for heading in (
            "Partitions (* default):",
            "Nodes by hardware",
            "Accounts & QOS",
            "Limits for guests/limited:",
            "Start estimates for guests/limited",
            "Start estimates for lab/normal",
            "My jobs:",
        ):
            self.assertIn(heading, text)

    def test_lists_partition_usage(self):
        text = self.render(make_snapshot())

        self.assertRegex(
            text,
            r"gpu\*\s+\| yes\s+\| \d+%\s+\| 3/8 l40s\s+\| 0/2\s+\| 12\s+\| 0-03:00:00",
        )

    def test_prints_the_estimate_grid_with_reasons_and_skipped_cells(self):
        text = self.render(make_snapshot())
        limited = text[text.index("Start estimates for guests/limited") :]
        limited = limited[: limited.index("Start estimates for lab/normal")]

        # limited caps GPUs at 1 and wall time at 3 hours.
        self.assertRegex(limited, r"1x l40s\s+\| now\s+\| -")
        self.assertNotIn("2x l40s", limited)
        self.assertIn("Request  | 0-03:00:00 | 1-00:00:00", limited)
        self.assertIn("'-' is over the 0-03:00:00 wall-time cap", limited)
        self.assertIn("--account=guests", limited)

        normal = text[text.index("Start estimates for lab/normal") :]
        self.assertRegex(normal, r"4x l40s\s+\| no\s+\| no")
        self.assertRegex(normal, r"CPU only\s+\| now\s+\| 1-06:00:00")
        # Rejected at every walltime, so the request is named once.
        self.assertIn("  4x l40s: too big", normal)

    def test_lists_jobs(self):
        job = {
            "id": "7",
            "name": "train",
            "state": "PENDING",
            "partition": "gpu",
            "account": "lab",
            "qos": "normal",
            "elapsed": "0:00",
            "time_limit": "3:00:00",
            "start": "2026-10-07T15:00:00",
            "submit": "",
            "reason": "Priority",
            "cpus": "4",
            "mem": "32G",
            "nodes": "1",
            "tres": "N/A",
            "nodelist": "",
        }

        text = self.render(make_snapshot([job]))

        self.assertRegex(
            text, r"7\s+\| train\s+\| PENDING\s+\| gpu\s+\| 0-00:00:00/0-03:00:00\s+\|"
        )
        self.assertIn("(Priority)", text)


class ReportsTests(unittest.TestCase):
    def test_reports_each_cluster_and_why_one_is_unreachable(self):
        clusters = [
            snapshot.Cluster("here", profiles.Profile("here"), FakeHost()),
            snapshot.Cluster(
                "fir",
                profiles.Profile("fir"),
                FakeHost(ssh="fir", lost="Permission denied"),
            ),
        ]
        snap = make_snapshot()
        output = StringIO()
        with (
            patch.object(
                snapshot.ClusterInfo,
                "detect",
                return_value=snapshot.ClusterInfo("here", "25", "me", "login", None),
            ),
            patch.object(snapshot.Collector, "collect", return_value=snap),
            redirect_stdout(output),
        ):
            report.print_reports(clusters, probes.Settings())

        text = output.getvalue()
        self.assertIn("me@login · here · Slurm 25", text)
        self.assertIn("=" * 80, text)
        self.assertIn("fir: cannot connect over ssh (fir): Permission denied", text)


if __name__ == "__main__":
    unittest.main()
