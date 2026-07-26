import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from node_state.clusters import common, vulcan


class SbatchTestTests(unittest.TestCase):
    def test_result_table_includes_all_gpu_node_results(self):
        results = [
            {"node": "rack15-12", "start_time": "2026-07-25T19:06:59", "result": "Runnable"},
            {"node": "rack15-05", "start_time": None, "result": "allocation failure"},
        ]
        output = StringIO()

        with redirect_stdout(output):
            vulcan.print_sbatch_test_results(results)

        self.assertIn("rack15-12", output.getvalue())
        self.assertIn("rack15-05", output.getvalue())
        self.assertIn("Can run", output.getvalue())
        self.assertIn("Result", output.getvalue())

    def test_builds_cluster_request_for_node(self):
        directives = vulcan.build_directives("rack15-12")

        self.assertIn("--test-only", directives)
        self.assertIn("--account=aip-xli135", directives)
        self.assertIn("--gres=gpu:l40s:1", directives)
        self.assertIn("--cpus-per-task=16", directives)
        self.assertIn("--mem=128G", directives)
        self.assertIn("--time=3:00:00", directives)
        self.assertIn("--nodelist=rack15-12", directives)

    @patch.object(common.subprocess, "run")
    def test_extracts_estimated_start_time(self, run_mock):
        run_mock.return_value = Mock(
            stdout=(
                "sbatch: Job 5484 to start at 2026-07-25T19:06:59 a using "
                "16 processors on nodes rack15-12\n"
            ),
            stderr="",
            returncode=0,
        )

        result = vulcan.run_sbatch_test("rack15-12")

        self.assertEqual(result["start_time"], "2026-07-25T19:06:59")
        self.assertEqual(result["result"], "Runnable")
        self.assertEqual(result["node"], "rack15-12")

    @patch.object(common.subprocess, "run")
    def test_reports_infeasible_node(self, run_mock):
        run_mock.return_value = Mock(
            stdout="allocation failure: Requested node configuration is not available\n",
            stderr="",
            returncode=1,
        )

        result = vulcan.run_sbatch_test("vulcan1")

        self.assertIsNone(result["start_time"])
        self.assertEqual(
            result["result"],
            "allocation failure: Requested node configuration is not available",
        )


if __name__ == "__main__":
    unittest.main()
