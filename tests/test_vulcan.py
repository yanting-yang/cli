import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from node_state.clusters import vulcan


class HardwareGroupingTests(unittest.TestCase):
    def test_identifies_gpu_nodes(self):
        self.assertTrue(vulcan.is_gpu_node({"cfg_gpus": {"gpu": 4, "l40s": 4}}))
        self.assertFalse(vulcan.is_gpu_node({"cfg_gpus": {}}))

    def test_groups_small_real_memory_differences_by_whole_gibibyte(self):
        base_node = {
            "cpu_tot": 64,
            "mem_tot": 515472,
            "cfg_gpus": {"gpu": 4, "l40s": 4},
        }
        nearly_identical_node = {**base_node, "mem_tot": 515478}

        key = vulcan.node_hw_key(base_node)

        self.assertEqual(key, vulcan.node_hw_key(nearly_identical_node))
        self.assertEqual(vulcan.hw_key_label(key), "64 CPUs / 503 GB / 4x gpu / 4x l40s")

    def test_keeps_different_whole_gibibyte_values_separate(self):
        smaller_node = {"cpu_tot": 64, "mem_tot": 515472, "cfg_gpus": {}}
        larger_node = {"cpu_tot": 64, "mem_tot": 516096, "cfg_gpus": {}}

        self.assertNotEqual(
            vulcan.node_hw_key(smaller_node), vulcan.node_hw_key(larger_node)
        )


class StateCountTests(unittest.TestCase):
    def test_formats_unique_states_with_counts(self):
        nodes = [
            {"state": "MIXED"},
            {"state": "IDLE"},
            {"state": "MIXED"},
        ]

        self.assertEqual(vulcan.format_state_counts(nodes), "IDLE=1, MIXED=2")

    def test_formats_no_states_as_an_empty_string(self):
        self.assertEqual(vulcan.format_state_counts([]), "")


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
        script = vulcan.build_sbatch_test_script("rack15-12")

        self.assertIn("#SBATCH --test-only", script)
        self.assertIn("#SBATCH --account=aip-xli135", script)
        self.assertIn("#SBATCH --gres=gpu:l40s:1", script)
        self.assertIn("#SBATCH --cpus-per-task=16", script)
        self.assertIn("#SBATCH --mem=128G", script)
        self.assertIn("#SBATCH --time=3:00:00", script)
        self.assertIn("#SBATCH --nodelist=rack15-12", script)

    @patch.object(vulcan.subprocess, "run")
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

    @patch.object(vulcan.subprocess, "run")
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
