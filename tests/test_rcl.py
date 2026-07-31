import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from node_state.clusters import common, rcl


class BuildDirectivesTests(unittest.TestCase):
    def test_requests_a_single_gpu_of_the_given_type(self):
        directives = rcl.build_directives("nvidia_b200_3g.90gb")

        self.assertIn("--test-only", directives)
        self.assertIn("--gres=gpu:nvidia_b200_3g.90gb:1", directives)
        self.assertIn(f"--cpus-per-task={rcl.PROBE_CPUS}", directives)
        self.assertIn(f"--mem={rcl.PROBE_MEM}", directives)
        self.assertIn(f"--time={rcl.PROBE_TIME}", directives)

    def test_omits_the_gres_request_for_cpu_only_probes(self):
        self.assertFalse(
            any(directive.startswith("--gres") for directive in rcl.build_directives())
        )

    def test_never_pins_a_partition_because_the_cluster_routes_jobs(self):
        for gpu in (None, "nvidia_b200"):
            with self.subTest(gpu=gpu):
                self.assertFalse(
                    any(
                        directive.startswith(("--partition", "-p"))
                        for directive in rcl.build_directives(gpu)
                    )
                )


class CleanResultTests(unittest.TestCase):
    def test_strips_the_sbatch_prefix_and_trailing_boilerplate(self):
        raw = (
            "sbatch: error: Limited account 'yanting.yang': GPUs - only a MIG slice "
            "is allowed. allocation failure: Unspecified error"
        )

        self.assertEqual(
            rcl.clean_result(raw),
            "Limited account 'yanting.yang': GPUs - only a MIG slice is allowed",
        )

    def test_leaves_unprefixed_text_alone(self):
        self.assertEqual(rcl.clean_result("'sbatch' not found"), "'sbatch' not found")


class ProbeReportTests(unittest.TestCase):
    def setUp(self):
        self.results = [
            {
                "label": "1x nvidia_b200",
                "start_time": None,
                "partition": None,
                "result": "sbatch: error: only a MIG slice is allowed",
            },
            {
                "label": "1x nvidia_b200_3g.90gb",
                "start_time": "2026-07-26T14:30:52",
                "partition": "mig",
                "result": "Runnable",
            },
        ]

    def test_reports_each_request_and_its_runnability(self):
        output = StringIO()

        with redirect_stdout(output):
            rcl.print_probe_results(self.results)
        text = output.getvalue()

        self.assertIn("Runnable: 1/2", text)
        self.assertIn("1x nvidia_b200_3g.90gb", text)
        self.assertIn("2026-07-26T14:30:52", text)
        self.assertIn("mig", text)

    def test_explains_why_blocked_requests_failed(self):
        output = StringIO()

        with redirect_stdout(output):
            rcl.print_probe_results(self.results)
        text = output.getvalue()

        self.assertIn("Blocked requests:", text)
        self.assertIn("only a MIG slice is allowed", text)

    def test_omits_the_blocked_section_when_everything_runs(self):
        runnable = [result for result in self.results if result["start_time"]]
        output = StringIO()

        with redirect_stdout(output):
            rcl.print_probe_results(runnable)

        self.assertNotIn("Blocked requests:", output.getvalue())


class AccountLimitsTests(unittest.TestCase):
    ASSOC_MGR = """Current Association Manager state

Association Records

ClusterName=rcl Account=guests UserName=me(24918) Partition= Priority=0 ID=32
    ParentAccount= Lineage=/guests/0-me/ DefAssoc=Yes

QOS Records

QOS=limited(2)
    MaxTRESPJ=cpu=8,mem=65536,gres/gpu=1
    Account Limits
      guests
        MaxJobsPA=N(1) MaxSubmitJobsPA=N(1)
    User Limits
      someone(24958)
        MaxJobsPU=1(1) MaxJobsAccruePU=N(0) MaxSubmitJobsPU=N(1)
"""

    def render(self, job_counts=None):
        output = StringIO()
        with (
            patch.object(common, "fetch_user_job_counts", return_value=job_counts),
            redirect_stdout(output),
        ):
            rcl.print_account_limits("me")
        return output.getvalue()

    @patch.object(common, "fetch_assoc_mgr")
    def test_reports_the_governing_account_and_qos(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR

        self.assertIn("account=guests, QOS=limited", self.render())

    @patch.object(common, "fetch_assoc_mgr")
    def test_reports_job_limits_against_current_usage(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR
        text = self.render({"running": 0, "pending": 2, "total": 2})

        running = next(line for line in text.splitlines() if "Jobs running" in line)
        submitted = next(line for line in text.splitlines() if "Jobs submitted" in line)

        self.assertRegex(running, r"Jobs running\s+\|\s+1\s+\|\s+0")
        self.assertRegex(submitted, r"Jobs submitted\s+\|\s+no limit\s+\|\s+2")

    @patch.object(common, "fetch_assoc_mgr")
    def test_converts_the_memory_cap_to_gigabytes(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR
        text = self.render()

        memory = next(line for line in text.splitlines() if "Memory per job" in line)

        self.assertRegex(memory, r"Memory per job \(GB\)\s+\|\s+64")

    @patch.object(common, "fetch_assoc_mgr")
    def test_marks_usage_unknown_when_squeue_is_unavailable(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR

        self.assertRegex(self.render(), r"Jobs running\s+\|\s+1\s+\|\s+\?")

    def test_notes_when_the_limits_cannot_be_read(self):
        with patch.object(common, "fetch_assoc_mgr", return_value=None):
            self.assertIn("unavailable", self.render())

    @patch.object(common, "fetch_assoc_mgr")
    def test_notes_when_no_single_qos_governs_the_account(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR.replace("      guests", "      other")

        self.assertIn("no single QOS", self.render())

    @patch.object(common, "fetch_assoc_mgr")
    def test_notes_when_the_user_has_no_association(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR.replace(
            "UserName=me(24918)", "UserName="
        )

        self.assertIn("no Slurm association", self.render())


class SummaryTests(unittest.TestCase):
    def test_reports_effective_cpus_rather_than_reserved_ones(self):
        node = {
            "state": "MIXED",
            "cpu_tot": 224,
            "cpu_efctv": 192,
            "cpu_alloc": 88,
            "mem_tot": 2060000,
            "mem_alloc": 706560,
            "cfg_gpus": {},
            "alloc_gpus": {},
        }
        output = StringIO()

        with redirect_stdout(output):
            common.print_summary("node", [node], 1, [], cpu_key=rcl.CPU_KEY)
        cpu_row = next(
            line for line in output.getvalue().splitlines() if line.startswith("CPU")
        )

        self.assertIn("192", cpu_row)
        self.assertIn("104", cpu_row)
        self.assertNotIn("224", cpu_row)


if __name__ == "__main__":
    unittest.main()
