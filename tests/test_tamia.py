import shlex
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from node_state.clusters import common, killarney, tamia

SCONTROL_NODES = """NodeName=tg10101 Arch=x86_64 CoresPerSocket=24
   CPUAlloc=16 CPUEfctv=40 CPUTot=48 CPULoad=1.00
   RealMemory=500000 AllocMem=128000 FreeMem=1000 Sockets=2 Boards=1
   State=MIXED ThreadsPerCore=1
   CfgTRES=cpu=48,mem=500000M,billing=146784,gres/gpu=4,gres/gpu:h100=4
   AllocTRES=cpu=16,mem=128000M,gres/gpu=2,gres/gpu:h100=2
NodeName=tc10701 Arch=x86_64 CoresPerSocket=32
   CPUAlloc=0 CPUEfctv=64 CPUTot=64 CPULoad=0.00
   RealMemory=500000 AllocMem=0 FreeMem=492000 Sockets=2 Boards=1
   State=IDLE ThreadsPerCore=1
   CfgTRES=cpu=64,mem=500000M,billing=30814
   AllocTRES=
"""

RUNNABLE = {
    "start_time": "2026-09-14T23:22:11",
    "partition": "gpubase_bynode_b1",
    "result": "Runnable",
}


class MainTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.object(
                common, "fetch_nodes", return_value=common.parse_nodes(SCONTROL_NODES)
            )
        )
        self.enterContext(patch.object(common, "current_user", return_value="me"))
        self.partition_mock = self.enterContext(
            patch.object(killarney, "print_partition_table")
        )
        self.account_mock = self.enterContext(
            patch.object(killarney, "print_account_limits")
        )
        self.probe_mock = self.enterContext(
            patch.object(tamia, "run_probes", return_value=[])
        )
        self.print_probe_mock = self.enterContext(
            patch.object(killarney, "print_probe_results")
        )

    def render(self):
        output = StringIO()
        with redirect_stdout(output):
            tamia.main(Namespace(cpus_per_task=12, mem="96G", sort_by_start=True))
        return output.getvalue()

    def test_reports_effective_cpus_without_double_counting_gpus(self):
        text = self.render()

        self.assertIn("40 CPUs / 488 GB / 4x h100 (1/1):", text)
        self.assertIn("64 CPUs / 488 GB (1/1):", text)
        self.assertNotIn("x gpu", text)
        self.assertRegex(text, r"CPU \(cores\)\s+\|\s+40\s+\|\s+16\s+\|\s+24")
        self.assertRegex(text, r"h100\s+\|\s+4\s+\|\s+2\s+\|\s+2")

    def test_reports_partitions_and_tamia_account_limits(self):
        self.render()

        self.partition_mock.assert_called_once_with()
        self.account_mock.assert_called_once_with("me", "tamia")

    def test_probes_whole_gpu_nodes_and_cleans_blocked_reasons(self):
        self.render()

        self.probe_mock.assert_called_once_with({"h100": 4}, cpus_per_task=12, mem="96G")
        self.print_probe_mock.assert_called_once_with(
            [],
            sort_by_start=True,
            clean=tamia.clean_result,
        )


class RunProbesTests(unittest.TestCase):
    def setUp(self):
        self.sbatch_mock = self.enterContext(
            patch.object(common, "run_sbatch_test", return_value=RUNNABLE)
        )
        self.srun_mock = self.enterContext(
            patch.object(common, "run_srun_test", return_value=RUNNABLE)
        )
        self.results = tamia.run_probes(
            {"h100": 4, "h200": 8}, cpus_per_task=4, mem="32G"
        )

    def test_requests_only_whole_gpu_nodes_within_the_one_day_cap(self):
        self.assertEqual(
            {(result["label"], result["time"]) for result in self.results
             if result["command"] == "sbatch"},
            {
                (label, time_limit)
                for label in (
                    "4x h100 (whole node)", "8x h200 (whole node)", "CPU only (no GPU)"
                )
                for time_limit in ("0-03:00:00", "0-12:00:00", "1-00:00:00")
            },
        )
        gres = {
            item
            for probe_call in self.sbatch_mock.call_args_list + self.srun_mock.call_args_list
            for item in probe_call.args[0]
            if item.startswith("--gres=")
        }
        self.assertEqual(gres, {"--gres=gpu:h100:4", "--gres=gpu:h200:8"})

    def test_names_a_program_for_every_srun_probe(self):
        self.assertEqual(self.srun_mock.call_count, 3)
        for probe_call in self.srun_mock.call_args_list:
            with self.subTest(directives=probe_call.args[0]):
                self.assertEqual(probe_call.kwargs["command"], ("bash",))
                self.assertIn("--test-only", probe_call.args[0])
                self.assertIn("--time=0-03:00:00", probe_call.args[0])

    def test_builds_copyable_run_commands_without_test_only(self):
        for result in self.results:
            with self.subTest(label=result["label"], command=result["command"]):
                arguments = shlex.split(result["run_command"])
                self.assertEqual(arguments[0], result["command"])
                self.assertNotIn("--test-only", arguments)
        self.assertEqual(
            [result["run_command"] for result in self.results
             if result["command"] == "srun"][0],
            "srun --gres=gpu:h100:4 --cpus-per-task=4 --mem=32G --time=0-03:00:00 --pty bash",
        )
        self.assertEqual(
            [result["run_command"] for result in self.results
             if result["command"] == "sbatch"][0],
            "sbatch --gres=gpu:h100:4 --cpus-per-task=4 --mem=32G --time=0-03:00:00 "
            '--wrap="sleep infinity"',
        )


class CleanResultTests(unittest.TestCase):
    NOTE = (
        "NOTE: Your memory request of 32768M was likely submitted as 32G. Please "
        "note that Slurm interprets memory requests denominated in G as multiples "
        "of 1024M, not 1000M. "
    )

    def test_strips_colour_codes_memory_note_and_banners(self):
        raw = (
            self.NOTE
            + "sbatch: error: \x1b[00;31m---------------------------------------- "
            "sbatch: error: The h100 GPUs are only allocated by node. "
            "sbatch: error: Please request a whole node using --gpus-per-node=h100:4 "
            "sbatch: error: ----------------------------------------\x1b[00m "
            "allocation failure: Requested node configuration is not available"
        )

        self.assertEqual(
            tamia.clean_result(raw),
            "The h100 GPUs are only allocated by node. Please request a whole node "
            "using --gpus-per-node=h100:4 allocation failure: Requested node "
            "configuration is not available",
        )

    def test_strips_srun_prefixes_and_unspecified_error_boilerplate(self):
        raw = (
            "srun: " + self.NOTE
            + "srun: error: This job exceeds the maximum walltime of 1 days on tamia. "
            "allocation failure: Unspecified error"
        )

        self.assertEqual(
            tamia.clean_result(raw),
            "This job exceeds the maximum walltime of 1 days on tamia",
        )

    def test_leaves_local_errors_alone(self):
        self.assertEqual(tamia.clean_result("'srun' not found"), "'srun' not found")


if __name__ == "__main__":
    unittest.main()
