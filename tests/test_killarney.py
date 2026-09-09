import shlex
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import call, patch

from node_state.clusters import common, killarney


class BuildDirectivesTests(unittest.TestCase):
    def test_builds_a_gpu_count_and_time_probe(self):
        directives = killarney.build_directives(
            "h100",
            8,
            "7-00:00:00",
            cpus_per_task=4,
            mem="32G",
        )

        self.assertIn("--test-only", directives)
        self.assertIn("--gres=gpu:h100:8", directives)
        self.assertIn("--cpus-per-task=4", directives)
        self.assertIn("--mem=32G", directives)
        self.assertIn("--time=7-00:00:00", directives)

    def test_omits_gpu_and_partition_for_a_cpu_only_probe(self):
        directives = killarney.build_directives(cpus_per_task=4, mem="32G")

        self.assertFalse(any(item.startswith("--gres") for item in directives))
        self.assertFalse(
            any(item.startswith(("--partition", "-p")) for item in directives)
        )

    def test_uses_custom_cpu_and_ram(self):
        directives = killarney.build_directives(
            cpus_per_task=12,
            mem="96G",
        )

        self.assertIn("--cpus-per-task=12", directives)
        self.assertIn("--mem=96G", directives)


class RunProbesTests(unittest.TestCase):
    @patch.object(common, "run_srun_test")
    @patch.object(common, "run_sbatch_test")
    def test_applies_custom_cpu_and_ram_to_every_probe(self, sbatch_mock, srun_mock):
        result = {
            "start_time": "2026-07-30T20:00:00",
            "partition": "test",
            "result": "Runnable",
        }
        sbatch_mock.return_value = result
        srun_mock.return_value = result

        results = killarney.run_probes({}, cpus_per_task=12, mem="96G")

        for call in sbatch_mock.call_args_list + srun_mock.call_args_list:
            self.assertIn("--cpus-per-task=12", call.args[0])
            self.assertIn("--mem=96G", call.args[0])
        self.assertNotIn("label", result)
        for probe in results:
            arguments = shlex.split(probe["run_command"])
            self.assertIn("--cpus-per-task=12", arguments)
            self.assertIn("--mem=96G", arguments)
        self.assertEqual(
            [probe["command"] for probe in results],
            ["sbatch"] * len(killarney.PROBE_TIMES) + ["srun"],
        )

    @patch.object(common, "run_srun_test")
    @patch.object(common, "run_sbatch_test")
    def test_runs_every_gpu_count_and_time_in_one_matrix(self, sbatch_mock, srun_mock):
        runnable_result = {
            "start_time": "2026-07-30T20:00:00",
            "partition": "test",
            "result": "Runnable",
        }
        sbatch_mock.return_value = runnable_result
        srun_mock.return_value = runnable_result

        results = killarney.run_probes(
            {"h100": 8, "l40s": 4},
            cpus_per_task=4,
            mem="32G",
        )

        expected_gpu_requests = {
            (f"{count}x {gpu}", time_limit)
            for gpu, capacity in (("h100", 8), ("l40s", 4))
            for count in range(1, capacity + 1)
            for time_limit in killarney.PROBE_TIMES
        }
        actual_requests = {
            (result["label"], result["time"])
            for result in results
            if result["label"] != "CPU only (no GPU)"
        }

        self.assertEqual(actual_requests, expected_gpu_requests)
        self.assertEqual(len(results), 70)
        for result, probe_call in zip(
            results, sbatch_mock.call_args_list + srun_mock.call_args_list, strict=True
        ):
            with self.subTest(request=result["label"], time=result["time"],
                              command=result["command"]):
                arguments = shlex.split(result["run_command"])
                self.assertEqual(arguments[0], result["command"])
                suffix = ["--pty", "bash"] if result["command"] == "srun" else ["job.sh"]
                self.assertEqual(arguments[-len(suffix):], suffix)
                self.assertEqual(
                    arguments[1:-len(suffix)],
                    [arg for arg in probe_call.args[0] if arg != "--test-only"],
                )
                self.assertIn("--test-only", probe_call.args[0])
                self.assertNotIn("--test-only", arguments)
        self.assertEqual(sbatch_mock.call_count, 65)
        self.assertEqual(srun_mock.call_count, 5)

        actual_gpu_directives = {
            (
                next(item for item in call.args[0] if item.startswith("--gres=")),
                next(item for item in call.args[0] if item.startswith("--time=")),
            )
            for call in sbatch_mock.call_args_list
            if any(item.startswith("--gres=") for item in call.args[0])
        }
        expected_gpu_directives = {
            (f"--gres=gpu:{gpu}:{count}", f"--time={time_limit}")
            for gpu, capacity in (("h100", 8), ("l40s", 4))
            for count in range(1, capacity + 1)
            for time_limit in killarney.PROBE_TIMES
        }
        self.assertEqual(actual_gpu_directives, expected_gpu_directives)

        self.assertEqual(
            {
                (
                    next(item for item in call.args[0] if item.startswith("--gres=")),
                    next(item for item in call.args[0] if item.startswith("--time=")),
                )
                for call in srun_mock.call_args_list
                if any(item.startswith("--gres=") for item in call.args[0])
            },
            {(f"--gres=gpu:l40s:{count}", "--time=3:00:00") for count in range(1, 5)},
        )
        cpu_srun_directives = [
            call.args[0]
            for call in srun_mock.call_args_list
            if not any(item.startswith("--gres=") for item in call.args[0])
        ]
        self.assertEqual(len(cpu_srun_directives), 1)
        self.assertIn("--time=3:00:00", cpu_srun_directives[0])
        self.assertEqual(
            [
                (result["label"], result["time"], result["command"])
                for result in results
                if result["label"] == "CPU only (no GPU)"
                and result["command"] == "srun"
            ],
            [("CPU only (no GPU)", "3:00:00", "srun")],
        )


class MainTests(unittest.TestCase):
    def test_propagates_feasibility_options(self):
        with (
            patch.object(common, "fetch_nodes", return_value=[]),
            patch.object(killarney, "run_probes", return_value=[]) as run_mock,
            patch.object(killarney, "print_probe_results") as print_mock,
            patch.object(killarney, "print_partition_table") as partition_mock,
            patch.object(common, "current_user", return_value="me"),
            patch.object(killarney, "print_account_limits") as account_mock,
        ):
            killarney.main(
                Namespace(
                    cpus_per_task=12,
                    mem="96G",
                    sort_by_start=True,
                )
            )

        partition_mock.assert_called_once_with()
        account_mock.assert_called_once_with("me")
        run_mock.assert_called_once_with(
            {},
            cpus_per_task=12,
            mem="96G",
        )
        print_mock.assert_called_once_with(
            [],
            cpus_per_task=12,
            mem="96G",
            sort_by_start=True,
        )


class PartitionTableTests(unittest.TestCase):
    SINFO_OUTPUT = (
        "PARTITION           GRES                NODES               TIMELIMIT       \n"
        "gpubase_interac     gpu:l40s:4          25                  3:00:00         \n"
    )

    def test_prints_the_sinfo_columns_and_rows(self):
        output = StringIO()

        with (
            patch.object(common, "fetch_partitions", return_value=self.SINFO_OUTPUT),
            redirect_stdout(output),
        ):
            killarney.print_partition_table()

        text = output.getvalue()
        self.assertIn("Partitions:", text)
        self.assertIn("PARTITION", text)
        self.assertIn("TIMELIMIT", text)
        self.assertIn("gpubase_interac", text)
        self.assertIn("gpu:l40s:4", text)

    def test_reports_when_sinfo_is_unavailable(self):
        output = StringIO()

        with (
            patch.object(common, "fetch_partitions", return_value=None),
            redirect_stdout(output),
        ):
            killarney.print_partition_table()

        self.assertIn("'sinfo' unavailable", output.getvalue())

    def test_reports_when_no_partitions_come_back(self):
        output = StringIO()

        with (
            patch.object(common, "fetch_partitions", return_value=""),
            redirect_stdout(output),
        ):
            killarney.print_partition_table()

        self.assertIn("none reported", output.getvalue())


class AccountLimitsTests(unittest.TestCase):
    ASSOC_MGR = """Current Association Manager state

Association Records

ClusterName=killarney Account=beta UserName=me(100) Partition= ID=1
    ParentAccount=root DefAssoc=Yes
ClusterName=killarney Account=alpha UserName=me(100) Partition= ID=2
    ParentAccount=root DefAssoc=No
ClusterName=killarney Account=unrelated UserName=someone(101) Partition= ID=3
    ParentAccount=root DefAssoc=Yes

QOS Records

QOS=normal(1)
    MaxWallPJ=1440
    MaxTRESPJ=cpu=32,mem=65536,node=2,gres/gpu=4,gres/gpu:h100=2
    MaxTRESPN=cpu=16,mem=32768,node=1,gres/gpu=2,gres/gpu:h100=1
    Account Limits
      alpha
        MaxJobsPA=20(4) MaxSubmitJobsPA=100(7)
        MaxTRESPA=cpu=128(48),mem=524288(98304),node=8(3),gres/gpu=16(6),gres/gpu:h100=8(2)
      beta
        MaxJobsPA=20(9) MaxSubmitJobsPA=100(11)
        MaxTRESPA=cpu=128(80),mem=524288(196608),node=8(5),gres/gpu=16(10),gres/gpu:h100=8(7)
    User Limits
      someone(101)
        MaxJobsPU=8(7) MaxSubmitJobsPU=40(9)
        MaxTRESPU=cpu=64(56),mem=262144(131072),node=4(3),gres/gpu=8(7),gres/gpu:h100=4(3)
      me(100)
        MaxJobsPU=8(1) MaxSubmitJobsPU=40(2)
        MaxTRESPU=cpu=64(16),mem=262144(32768),node=4(1),gres/gpu=8(2),gres/gpu:h100=4(1)
QOS=burst(2)
    MaxWallPJ=180
    MaxTRESPJ=cpu=8,mem=16384,gres/gpu:h100=0
    MaxTRESPN=
    Account Limits
      alpha
        MaxJobsPA=N(0) MaxSubmitJobsPA=N(1)
        MaxTRESPA=cpu=N(0),mem=N(0),node=N(0),gres/gpu=0(0),gres/gpu:h100=0(0)
    User Limits
      someone(101)
        MaxJobsPU=N(3) MaxSubmitJobsPU=N(4)
        MaxTRESPU=cpu=N(24),mem=N(49152),node=N(3),gres/gpu=0(0),gres/gpu:h100=0(0)
QOS=unused(3)
    MaxWallPJ=60
    MaxTRESPJ=cpu=2
    MaxTRESPN=
    Account Limits
        No Accounts
    User Limits
        No Users
QOS=other(4)
    MaxTRESPJ=cpu=1
    Account Limits
      unrelated
        MaxJobsPA=1(1) MaxSubmitJobsPA=1(1)
    User Limits
        No Users
"""

    def setUp(self):
        self.assoc_mock = self.enterContext(
            patch.object(common, "fetch_assoc_mgr", return_value=self.ASSOC_MGR)
        )
        self.accounts_mock = self.enterContext(
            patch.object(
                common,
                "fetch_user_account_qos",
                return_value={"beta": ["normal"], "alpha": ["normal", "burst"]},
            )
        )
        counts = {
            "normal": {"running": 2, "pending": 3, "total": 5},
            "burst": {"running": 0, "pending": 1, "total": 1},
            "unused": {"running": 0, "pending": 0, "total": 0},
        }
        self.counts_mock = self.enterContext(
            patch.object(
                common,
                "fetch_user_job_counts",
                side_effect=lambda user, qos: counts[qos],
            )
        )

    def render(self):
        output = StringIO()
        with redirect_stdout(output):
            killarney.print_account_limits("me")
        return output.getvalue()

    @staticmethod
    def table(text, account, qos):
        title = f"Account limits (account={account}, QOS={qos}):"
        section = text.split(title, 1)[1].split("Account limits", 1)[0]
        rows = {}
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) == 3:
                rows[cells[0]] = tuple(cells[1:])
        return rows

    def test_reports_every_allowed_account_and_qos_in_sorted_order(self):
        text = self.render()

        titles = [line for line in text.splitlines() if line.startswith("Account limits (")]
        self.assertEqual(titles, [
            "Account limits (account=alpha, QOS=burst):",
            "Account limits (account=alpha, QOS=normal):",
            "Account limits (account=beta, QOS=normal):",
        ])
        self.assoc_mock.assert_called_once_with()
        self.accounts_mock.assert_called_once_with("me", "killarney")
        self.assertCountEqual(
            self.counts_mock.call_args_list,
            [call("me", qos="burst"), call("me", qos="normal")],
        )
        self.assertNotIn("account=unrelated", text)
        self.assertNotIn("QOS=unused", text)

    def test_keeps_account_usage_separate_and_user_usage_across_accounts(self):
        text = self.render()
        alpha = self.table(text, "alpha", "normal")
        beta = self.table(text, "beta", "normal")

        self.assertEqual(alpha["Jobs running per account"], ("20", "4"))
        self.assertEqual(alpha["Jobs submitted per account"], ("100", "7"))
        self.assertEqual(beta["Jobs running per account"], ("20", "9"))
        self.assertEqual(beta["Jobs submitted per account"], ("100", "11"))
        self.assertEqual(alpha["CPUs per account"], ("128", "48"))
        self.assertEqual(beta["CPUs per account"], ("128", "80"))
        for rows in (alpha, beta):
            self.assertEqual(rows["Jobs running per user"], ("8", "2"))
            self.assertEqual(rows["Jobs submitted per user"], ("40", "5"))
            self.assertEqual(rows["CPUs per user"], ("64", "16"))

    def test_reports_memory_nodes_gpus_and_wall_time_for_each_scope(self):
        rows = self.table(self.render(), "alpha", "normal")

        expected = {
            "Memory per account (GB)": ("512", "96"),
            "Memory per user (GB)": ("256", "32"),
            "Memory per job (GB)": ("64", "-"),
            "Memory per node (GB)": ("32", "-"),
            "CPUs per job": ("32", "-"),
            "CPUs per node": ("16", "-"),
            "Nodes per account": ("8", "3"),
            "Nodes per user": ("4", "1"),
            "Nodes per job": ("2", "-"),
            "Nodes per node": ("1", "-"),
            "GPUs per account": ("16", "6"),
            "GPUs per user": ("8", "2"),
            "GPUs per job": ("4", "-"),
            "GPUs per node": ("2", "-"),
            "h100 per account": ("8", "2"),
            "h100 per user": ("4", "1"),
            "h100 per job": ("2", "-"),
            "h100 per node": ("1", "-"),
            "Wall time per job (minutes)": ("1440", "-"),
        }
        for label, values in expected.items():
            with self.subTest(label=label):
                self.assertEqual(rows[label], values)

    def test_displays_unlimited_and_zero_caps_without_borrowing_usage(self):
        rows = self.table(self.render(), "alpha", "burst")

        self.assertEqual(rows["Jobs running per account"], ("no limit", "0"))
        self.assertEqual(rows["Jobs submitted per account"], ("no limit", "1"))
        self.assertEqual(rows["Jobs running per user"], ("no limit", "0"))
        self.assertEqual(rows["Jobs submitted per user"], ("no limit", "1"))
        self.assertEqual(rows["CPUs per account"], ("no limit", "0"))
        self.assertEqual(rows["Memory per account (GB)"], ("no limit", "0"))
        self.assertEqual(rows["CPUs per user"], ("no limit", "?"))
        self.assertEqual(rows["Memory per user (GB)"], ("no limit", "?"))
        self.assertEqual(rows["GPUs per account"], ("0", "0"))
        self.assertEqual(rows["h100 per user"], ("0", "?"))
        self.assertEqual(rows["h100 per job"], ("0", "-"))
        self.assertNotIn("CPUs per node", rows)

    def test_allowed_account_without_cached_usage_borrows_only_caps(self):
        self.accounts_mock.return_value = {"inactive": ["normal"]}
        rows = self.table(self.render(), "inactive", "normal")

        self.assertEqual(rows["Jobs running per account"], ("20", "?"))
        self.assertEqual(rows["Jobs submitted per account"], ("100", "?"))
        self.assertEqual(rows["CPUs per account"], ("128", "?"))
        self.assertEqual(rows["Memory per account (GB)"], ("512", "?"))
        self.assertEqual(rows["h100 per account"], ("8", "?"))
        self.assertEqual(rows["Jobs running per user"], ("8", "2"))

    def test_lists_allowed_qos_even_when_no_account_or_user_is_tracked(self):
        self.accounts_mock.return_value = {"alpha": ["unused"]}
        rows = self.table(self.render(), "alpha", "unused")

        self.assertEqual(rows["Jobs running per account"], ("?", "?"))
        self.assertEqual(rows["Jobs running per user"], ("?", "0"))
        self.assertEqual(rows["CPUs per account"], ("?", "?"))
        self.assertEqual(rows["Memory per user (GB)"], ("?", "?"))
        self.assertEqual(rows["CPUs per job"], ("2", "-"))
        self.assertEqual(rows["Wall time per job (minutes)"], ("60", "-"))

    def test_falls_back_to_all_user_accounts_and_labels_cached_qos_matches(self):
        self.accounts_mock.return_value = None
        text = self.render()

        self.assertIn("account=alpha, QOS=burst", text)
        self.assertIn("account=alpha, QOS=normal", text)
        self.assertIn("account=beta, QOS=normal", text)
        self.assertNotIn("account=unrelated", text)
        self.assertIn("cache", text.lower())
        self.assertIn("permission", text.lower())

    def test_marks_only_user_job_usage_unknown_when_squeue_fails(self):
        self.counts_mock.side_effect = None
        self.counts_mock.return_value = None
        rows = self.table(self.render(), "alpha", "normal")

        self.assertEqual(rows["Jobs running per user"], ("8", "?"))
        self.assertEqual(rows["Jobs submitted per user"], ("40", "?"))
        self.assertEqual(rows["Jobs running per account"], ("20", "4"))
        self.assertEqual(rows["CPUs per user"], ("64", "16"))

    def test_reports_unavailable_controller_cache(self):
        self.assoc_mock.return_value = None

        self.assertIn("unavailable", self.render())
        self.counts_mock.assert_not_called()

    def test_reports_no_association_when_authoritative_mapping_is_empty(self):
        self.accounts_mock.return_value = {}
        text = self.render()

        self.assertIn("no Slurm association", text)
        self.assertNotIn("account=alpha", text)
        self.counts_mock.assert_not_called()

    def test_reports_missing_qos_cache_record_and_continues_other_qos(self):
        self.accounts_mock.return_value = {"alpha": ["missing", "normal"]}
        text = self.render()

        self.assertIn("QOS=missing", text)
        self.assertIn("unavailable", text)
        rows = self.table(text, "alpha", "normal")
        self.assertEqual(rows["Jobs running per account"], ("20", "4"))
        self.counts_mock.assert_called_once_with("me", qos="normal")

    def test_fallback_reports_when_the_user_has_no_association(self):
        self.accounts_mock.return_value = None
        self.assoc_mock.return_value = self.ASSOC_MGR.replace(
            "UserName=me(100)", "UserName=elsewhere(102)"
        )

        self.assertIn("no Slurm association", self.render())
        self.counts_mock.assert_not_called()

    def test_reports_account_without_any_qos(self):
        self.accounts_mock.return_value = {"alpha": []}
        text = self.render()

        self.assertIn("alpha", text)
        self.assertIn("no QOS", text)
        self.counts_mock.assert_not_called()


class ProbeReportTests(unittest.TestCase):
    def test_sorts_by_estimated_start_with_blocked_requests_last(self):
        results = [
            {
                "label": "late request",
                "run_command": "sbatch --cpus-per-task=12 --mem=96G --time=3:00:00 late.sh",
                "time": "3:00:00",
                "command": "sbatch",
                "start_time": "2026-08-02T12:00:00",
                "partition": "late",
                "result": "Runnable",
            },
            {
                "label": "unrunnable request",
                "run_command": "sbatch --cpus-per-task=12 --mem=96G --time=3:00:00 blocked.sh",
                "time": "3:00:00",
                "command": "sbatch",
                "start_time": None,
                "partition": None,
                "result": "Unavailable",
            },
            {
                "label": "early request",
                "run_command": "sbatch --cpus-per-task=12 --mem=96G --time=3:00:00 early.sh",
                "time": "3:00:00",
                "command": "sbatch",
                "start_time": "2026-08-01T12:00:00",
                "partition": "early",
                "result": "Runnable",
            },
        ]
        output = StringIO()

        with redirect_stdout(output):
            killarney.print_probe_results(
                results,
                cpus_per_task=12,
                mem="96G",
                sort_by_start=True,
            )

        text = output.getvalue()
        self.assertIn("12 CPUs, 96G", text)
        self.assertLess(text.index("early request"), text.index("late request"))
        self.assertLess(text.index("late request"), text.index("unrunnable request"))
        self.assertLess(text.index("early.sh"), text.index("late.sh"))
        self.assertLess(text.index("late.sh"), text.index("blocked.sh"))

    def test_prints_all_results_in_one_table_with_a_time_column(self):
        results = [
            {
                "label": "8x h100",
                "run_command": "sbatch --gres=gpu:h100:8 --cpus-per-task=4 --mem=32G --time=7-00:00:00 job.sh",
                "time": "7-00:00:00",
                "command": "sbatch",
                "start_time": "2026-08-01T12:00:00",
                "partition": "gpubase_h100_b5",
                "result": "Runnable",
            }
        ]
        output = StringIO()

        with redirect_stdout(output):
            killarney.print_probe_results(
                results,
                cpus_per_task=4,
                mem="32G",
            )

        text = output.getvalue()
        self.assertRegex(text, r"Request\s+\| Time\s+\| Command\s+\| Can run")
        self.assertIn("sbatch", text)
        self.assertIn("8x h100", text)
        self.assertIn("7-00:00:00", text)
        self.assertIn("gpubase_h100_b5", text)
        self.assertIn("Run command", text)
        self.assertIn(results[0]["run_command"], text)
        self.assertIn("replace job.sh", text)


if __name__ == "__main__":
    unittest.main()
