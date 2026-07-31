import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from node_state.clusters import common, killarney


class TypedGpusTests(unittest.TestCase):
    def test_drops_the_rollup_when_a_model_count_exists(self):
        self.assertEqual(
            killarney.typed_gpus({"gpu": 4, "l40s": 4}),
            {"l40s": 4},
        )

    def test_keeps_the_rollup_when_it_is_the_only_gpu_information(self):
        self.assertEqual(killarney.typed_gpus({"gpu": 4}), {"gpu": 4})

    def test_normalizes_configured_and_allocated_gpus(self):
        node = killarney.normalize_node(
            {
                "cfg_gpus": {"gpu": 8, "h100": 8},
                "alloc_gpus": {"gpu": 3, "h100": 3},
            }
        )

        self.assertEqual(node["cfg_gpus"], {"h100": 8})
        self.assertEqual(node["alloc_gpus"], {"h100": 3})


class BuildDirectivesTests(unittest.TestCase):
    def test_builds_a_gpu_count_and_time_probe(self):
        directives = killarney.build_directives("h100", 8, "7-00:00:00")

        self.assertIn("--test-only", directives)
        self.assertIn("--gres=gpu:h100:8", directives)
        self.assertIn(f"--cpus-per-task={killarney.PROBE_CPUS}", directives)
        self.assertIn(f"--mem={killarney.PROBE_MEM}", directives)
        self.assertIn("--time=7-00:00:00", directives)

    def test_omits_gpu_and_partition_for_a_cpu_only_probe(self):
        directives = killarney.build_directives()

        self.assertFalse(any(item.startswith("--gres") for item in directives))
        self.assertFalse(
            any(item.startswith(("--partition", "-p")) for item in directives)
        )

    def test_uses_custom_cpu_and_ram(self):
        directives = killarney.build_directives(
            probe_cpus=12,
            probe_ram="96G",
        )

        self.assertIn("--cpus-per-task=12", directives)
        self.assertIn("--mem=96G", directives)


class RunProbesTests(unittest.TestCase):
    @patch.object(common, "run_srun_test")
    @patch.object(common, "run_sbatch_test")
    def test_applies_custom_cpu_and_ram_to_every_probe(
        self, sbatch_mock, srun_mock
    ):
        result = {
            "start_time": "2026-07-30T20:00:00",
            "partition": "test",
            "result": "Runnable",
        }
        sbatch_mock.return_value = result
        srun_mock.return_value = result

        killarney.run_probes({}, probe_cpus=12, probe_ram="96G")

        for call in sbatch_mock.call_args_list + srun_mock.call_args_list:
            self.assertIn("--cpus-per-task=12", call.args[0])
            self.assertIn("--mem=96G", call.args[0])

    @patch.object(common, "run_srun_test")
    @patch.object(common, "run_sbatch_test")
    def test_runs_every_gpu_count_and_time_in_one_matrix(
        self, sbatch_mock, srun_mock
    ):
        result = lambda _directives: {
            "start_time": "2026-07-30T20:00:00",
            "partition": "test",
            "result": "Runnable",
        }
        sbatch_mock.side_effect = result
        srun_mock.side_effect = result

        results = killarney.run_probes({"h100": 8, "l40s": 4})

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
                    next(
                        item for item in call.args[0] if item.startswith("--gres=")
                    ),
                    next(
                        item for item in call.args[0] if item.startswith("--time=")
                    ),
                )
                for call in srun_mock.call_args_list
                if any(item.startswith("--gres=") for item in call.args[0])
            },
            {
                (f"--gres=gpu:l40s:{count}", "--time=3:00:00")
                for count in range(1, 5)
            },
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
        ):
            killarney.main(
                probe_cpus=12,
                probe_ram="96G",
                sort_by_start=True,
            )

        run_mock.assert_called_once_with(
            {},
            probe_cpus=12,
            probe_ram="96G",
        )
        print_mock.assert_called_once_with(
            [],
            probe_cpus=12,
            probe_ram="96G",
            sort_by_start=True,
        )


class ProbeReportTests(unittest.TestCase):
    def test_sorts_by_estimated_start_with_blocked_requests_last(self):
        results = [
            {
                "label": "late request",
                "time": "3:00:00",
                "command": "sbatch",
                "start_time": "2026-08-02T12:00:00",
                "partition": "late",
                "result": "Runnable",
            },
            {
                "label": "unrunnable request",
                "time": "3:00:00",
                "command": "sbatch",
                "start_time": None,
                "partition": None,
                "result": "Unavailable",
            },
            {
                "label": "early request",
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
                probe_cpus=12,
                probe_ram="96G",
                sort_by_start=True,
            )

        text = output.getvalue()
        self.assertIn("12 CPUs, 96G", text)
        self.assertLess(text.index("early request"), text.index("late request"))
        self.assertLess(text.index("late request"), text.index("unrunnable request"))

    def test_prints_all_results_in_one_table_with_a_time_column(self):
        results = [
            {
                "label": "8x h100",
                "time": "7-00:00:00",
                "command": "sbatch",
                "start_time": "2026-08-01T12:00:00",
                "partition": "gpubase_h100_b5",
                "result": "Runnable",
            }
        ]
        output = StringIO()

        with redirect_stdout(output):
            killarney.print_probe_results(results)

        text = output.getvalue()
        self.assertRegex(text, r"Request\s+\| Time\s+\| Command\s+\| Can run")
        self.assertIn("sbatch", text)
        self.assertIn("8x h100", text)
        self.assertIn("7-00:00:00", text)
        self.assertIn("gpubase_h100_b5", text)


if __name__ == "__main__":
    unittest.main()
