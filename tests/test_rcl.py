import shlex
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, call, patch

from node_state.clusters import common, killarney, rcl

SCONTROL_NODE = """NodeName=rcl-nv2.ece.ubc.ca Arch=x86_64 CoresPerSocket=56
   CPUAlloc=156 CPUEfctv=224 CPUTot=224 CPULoad=21.17
   RealMemory=2060000 AllocMem=1214464 FreeMem=38056 Sockets=2 Boards=1
   State=MIXED+PLANNED ThreadsPerCore=2 TmpDisk=0 Weight=1
   CfgTRES=cpu=224,mem=2060000M,billing=448,gres/gpu=16,gres/gpu:nvidia_b200=4,gres/gpu:nvidia_b200_2g.45gb=8,gres/gpu:nvidia_b200_3g.90gb=4
   AllocTRES=cpu=156,mem=1186G,gres/gpu=13,gres/gpu:nvidia_b200=1,gres/gpu:nvidia_b200_2g.45gb=8,gres/gpu:nvidia_b200_3g.90gb=4
"""

CAPACITIES = {
    "nvidia_b200": 4,
    "nvidia_b200_2g.45gb": 8,
    "nvidia_b200_3g.90gb": 4,
}

RUNNABLE = {
    "start_time": "2026-09-24T10:57:15",
    "partition": "mig",
    "result": "Runnable",
}


class MaxGpuCountsTests(unittest.TestCase):
    def counts(self, assoc_mgr, qos_name):
        record = common.parse_qos_records(assoc_mgr)[qos_name]
        return rcl.max_gpu_counts(CAPACITIES, record, "me")

    def test_caps_each_model_at_its_per_user_limit(self):
        self.assertEqual(
            self.counts(AccountLimitsTests.NORMAL_ASSOC_MGR, "normal"),
            {"nvidia_b200": 1, "nvidia_b200_2g.45gb": 3, "nvidia_b200_3g.90gb": 1},
        )

    def test_caps_every_model_at_the_combined_per_user_gpu_limit(self):
        assoc_mgr = AccountLimitsTests.NORMAL_ASSOC_MGR.replace(
            "MaxTRESPU=cpu=64(8),mem=524288(65536),gres/gpu=4(1),"
            "gres/gpu:nvidia_b200=1(0),gres/gpu:nvidia_b200_2g.45gb=3(0),"
            "gres/gpu:nvidia_b200_3g.90gb=1(1)",
            "MaxTRESPU=cpu=64(8),gres/gpu=2(1)",
        )

        self.assertEqual(
            self.counts(assoc_mgr, "normal"),
            {"nvidia_b200": 2, "nvidia_b200_2g.45gb": 2, "nvidia_b200_3g.90gb": 2},
        )

    def test_applies_per_job_gpu_caps(self):
        self.assertEqual(
            self.counts(AccountLimitsTests.ASSOC_MGR, "limited"),
            {"nvidia_b200": 1, "nvidia_b200_2g.45gb": 1, "nvidia_b200_3g.90gb": 1},
        )

    def test_borrows_caps_when_the_caller_has_no_user_entry(self):
        assoc_mgr = AccountLimitsTests.NORMAL_ASSOC_MGR.replace(
            "      me(24918)", "      another(24919)"
        )

        self.assertEqual(
            self.counts(assoc_mgr, "normal"),
            {"nvidia_b200": 1, "nvidia_b200_2g.45gb": 3, "nvidia_b200_3g.90gb": 1},
        )

    def test_uses_node_capacity_when_the_qos_caps_no_gpus(self):
        self.assertEqual(
            self.counts(AccountLimitsTests.MULTI_QOS_ASSOC_MGR, "limited"), CAPACITIES
        )

    def test_ignores_unlimited_per_user_caps(self):
        # opportunistic's per-user gres/gpu is N; only its per-job cap of 2 applies.
        self.assertEqual(
            self.counts(AccountLimitsTests.MULTI_QOS_ASSOC_MGR, "opportunistic"),
            {"nvidia_b200": 2, "nvidia_b200_2g.45gb": 2, "nvidia_b200_3g.90gb": 2},
        )

    def test_applies_per_model_per_job_caps(self):
        assoc_mgr = AccountLimitsTests.NORMAL_ASSOC_MGR.replace(
            "    MaxTRESPJ=\n", "    MaxTRESPJ=gres/gpu:nvidia_b200_2g.45gb=2\n"
        )

        self.assertEqual(
            self.counts(assoc_mgr, "normal"),
            {"nvidia_b200": 1, "nvidia_b200_2g.45gb": 2, "nvidia_b200_3g.90gb": 1},
        )

    def test_never_exceeds_one_nodes_capacity(self):
        assoc_mgr = AccountLimitsTests.NORMAL_ASSOC_MGR.replace(
            "gres/gpu=4(1),gres/gpu:nvidia_b200=1(0)",
            "gres/gpu=N(1),gres/gpu:nvidia_b200=16(0)",
        )

        self.assertEqual(self.counts(assoc_mgr, "normal")["nvidia_b200"], 4)

    def test_still_probes_one_gpu_when_the_cap_is_zero(self):
        assoc_mgr = AccountLimitsTests.NORMAL_ASSOC_MGR.replace(
            "gres/gpu:nvidia_b200=1(0)", "gres/gpu:nvidia_b200=0(0)"
        )

        self.assertEqual(self.counts(assoc_mgr, "normal")["nvidia_b200"], 1)


class RunProbesTests(unittest.TestCase):
    def setUp(self):
        self.sbatch_mock = self.enterContext(
            patch.object(common, "run_sbatch_test", return_value=RUNNABLE)
        )
        self.srun_mock = self.enterContext(
            patch.object(common, "run_srun_test", return_value=RUNNABLE)
        )
        self.results = rcl.run_probes(
            {"nvidia_b200_2g.45gb": 3, "nvidia_b200": 1},
            account="rcl",
            qos="normal",
            cpus_per_task=8,
            mem="64G",
        )
        self.calls = self.sbatch_mock.call_args_list + self.srun_mock.call_args_list

    def test_probes_every_gpu_count_up_to_the_limit_with_sbatch_and_srun(self):
        labels = [
            "1x nvidia_b200",
            "1x nvidia_b200_2g.45gb",
            "2x nvidia_b200_2g.45gb",
            "3x nvidia_b200_2g.45gb",
            "CPU only (no GPU)",
        ]

        self.assertEqual(
            [(result["command"], result["label"]) for result in self.results],
            [("sbatch", label) for label in labels]
            + [("srun", label) for label in labels],
        )

    def test_probes_cpu_only_sbatch_first_and_only_once(self):
        self.assertEqual(self.sbatch_mock.call_count, 5)
        self.assertEqual(self.srun_mock.call_count, 5)
        self.assertEqual(
            self.sbatch_mock.call_args_list[0].args[0],
            ["--test-only", "--account=rcl", "--qos=normal", "--cpus-per-task=8",
             "--mem=64G", f"--time={rcl.PROBE_TIME}"],
        )

    def test_pins_account_and_qos_on_every_probe(self):
        for probe_call in self.calls:
            with self.subTest(directives=probe_call.args[0]):
                self.assertEqual(
                    probe_call.args[0][:3],
                    ["--test-only", "--account=rcl", "--qos=normal"],
                )
                self.assertIn("--cpus-per-task=8", probe_call.args[0])
                self.assertIn("--mem=64G", probe_call.args[0])
                self.assertIn(f"--time={rcl.PROBE_TIME}", probe_call.args[0])
                self.assertFalse(
                    any(
                        item.startswith(("--partition", "-p"))
                        for item in probe_call.args[0]
                    )
                )
        self.assertEqual({result["time"] for result in self.results}, {rcl.PROBE_TIME})

    def test_each_result_comes_from_the_command_it_names(self):
        self.sbatch_mock.return_value = {**RUNNABLE, "partition": "from-sbatch"}
        self.srun_mock.return_value = {**RUNNABLE, "partition": "from-srun"}

        results = rcl.run_probes({"nvidia_b200": 1}, cpus_per_task=4, mem="32G")

        self.assertEqual({result["command"] for result in results}, {"sbatch", "srun"})
        for result in results:
            with self.subTest(label=result["label"], command=result["command"]):
                self.assertEqual(result["partition"], f"from-{result['command']}")

    def test_srun_probes_need_no_program(self):
        for probe_call in self.srun_mock.call_args_list:
            with self.subTest(directives=probe_call.args[0]):
                self.assertEqual(probe_call.kwargs, {})

    def test_builds_copyable_run_commands_matching_each_probe(self):
        sbatch_calls = iter(self.sbatch_mock.call_args_list[1:])
        srun_calls = iter(self.srun_mock.call_args_list)
        for result in self.results:
            with self.subTest(label=result["label"], command=result["command"]):
                if result["label"] == "CPU only (no GPU)" and result["command"] == "sbatch":
                    probe_call = self.sbatch_mock.call_args_list[0]
                elif result["command"] == "sbatch":
                    probe_call = next(sbatch_calls)
                else:
                    probe_call = next(srun_calls)
                suffix = (
                    ["--pty", "bash"]
                    if result["command"] == "srun"
                    else ["--wrap=sleep infinity"]
                )
                self.assertEqual(
                    shlex.split(result["run_command"]),
                    [
                        result["command"],
                        *(arg for arg in probe_call.args[0] if arg != "--test-only"),
                        *suffix,
                    ],
                )
        run_commands = [result["run_command"] for result in self.results]
        self.assertIn(
            "sbatch --account=rcl --qos=normal --gres=gpu:nvidia_b200_2g.45gb:3 "
            '--cpus-per-task=8 --mem=64G --time=0-01:00:00 --wrap="sleep infinity"',
            run_commands,
        )
        self.assertIn(
            "srun --account=rcl --qos=normal --cpus-per-task=8 --mem=64G "
            "--time=0-01:00:00 --pty bash",
            run_commands,
        )

    def test_reports_only_the_scope_check_for_a_pair_slurm_rejects(self):
        self.sbatch_mock.reset_mock()
        self.srun_mock.reset_mock()
        self.sbatch_mock.return_value = {
            "start_time": None,
            "partition": None,
            "result": "allocation failure: Invalid qos specification",
        }

        results = rcl.run_probes(
            {"nvidia_b200": 4}, account="rcl", qos="large", cpus_per_task=4, mem="32G"
        )

        self.assertEqual(
            [(result["command"], result["label"]) for result in results],
            [("sbatch", "CPU only (no GPU)")],
        )
        self.assertEqual(self.sbatch_mock.call_count, 1)
        self.srun_mock.assert_not_called()

    def test_does_not_skip_a_pair_for_other_rejections(self):
        self.sbatch_mock.return_value = {
            "start_time": None,
            "partition": None,
            "result": "Limited account 'me': CPUs - max 8 per job",
        }

        results = rcl.run_probes(
            {"nvidia_b200": 1}, account="rcl", qos="normal", cpus_per_task=16, mem="32G"
        )

        self.assertEqual(len(results), 4)

    def test_runs_unscoped_probes_without_account_or_qos(self):
        self.sbatch_mock.reset_mock()
        self.srun_mock.reset_mock()
        self.sbatch_mock.return_value = {
            "start_time": None,
            "partition": None,
            "result": "Invalid account or account/partition combination specified",
        }

        results = rcl.run_probes({"nvidia_b200": 1}, cpus_per_task=4, mem="32G")

        self.assertEqual(len(results), 4)
        for probe_call in self.sbatch_mock.call_args_list + self.srun_mock.call_args_list:
            self.assertFalse(
                any(item.startswith(("--account", "--qos")) for item in probe_call.args[0])
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

    def test_strips_the_srun_prefix(self):
        raw = (
            "srun: error: Limited account 'you': GPUs - at most 1 MIG slice per job "
            "(you requested 2). allocation failure: Unspecified error"
        )

        self.assertEqual(
            rcl.clean_result(raw),
            "Limited account 'you': GPUs - at most 1 MIG slice per job (you requested 2)",
        )

    # Captured from `sbatch --test-only --qos=large ...` on rcl.
    BANNER_REJECTION = (
        "sbatch: error: " + "=" * 71 + "\n"
        "sbatch: error: STORAGE POLICY: Please ensure large job outputs are "
        "written to /scratch\n"
        "sbatch: error: Please delete files from /scratch after you are done "
        "with your work.   \n"
        "sbatch: error: " + "=" * 71 + "\n"
        "allocation failure: Invalid qos specification\n"
    )

    def banner_rejection(self):
        return common.parse_scheduling_test_result(
            Mock(stdout="", stderr=self.BANNER_REJECTION, returncode=1), "sbatch"
        )

    def test_strips_the_storage_policy_banner(self):
        self.assertEqual(
            rcl.clean_result(self.banner_rejection()["result"]),
            "allocation failure: Invalid qos specification",
        )

    def test_leaves_unprefixed_text_alone(self):
        self.assertEqual(rcl.clean_result("'sbatch' not found"), "'sbatch' not found")

    def test_cleans_the_blocked_reasons_in_the_probe_report(self):
        result = self.banner_rejection()
        output = StringIO()

        with redirect_stdout(output):
            killarney.print_probe_results(
                [
                    {
                        **result,
                        "label": "1x nvidia_b200_2g.45gb",
                        "time": rcl.PROBE_TIME,
                        "command": "sbatch",
                        "run_command": "sbatch --wrap",
                    }
                ],
                clean=rcl.clean_result,
            )
        text = output.getvalue()

        self.assertIn(
            "  sbatch 1x nvidia_b200_2g.45gb for 0-01:00:00: "
            "allocation failure: Invalid qos specification\n",
            text,
        )
        self.assertNotIn("STORAGE POLICY", text)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.object(
                common, "fetch_nodes", return_value=common.parse_nodes(SCONTROL_NODE)
            )
        )
        self.enterContext(patch.object(common, "current_user", return_value="me"))
        self.assoc_mock = self.enterContext(
            patch.object(
                common,
                "fetch_assoc_mgr",
                return_value=AccountLimitsTests.NORMAL_ASSOC_MGR,
            )
        )
        self.enterContext(
            patch.object(common, "fetch_user_job_counts", return_value=None)
        )
        self.probe_mock = self.enterContext(
            patch.object(rcl, "run_probes", return_value=[])
        )
        self.print_probe_mock = self.enterContext(
            patch.object(killarney, "print_probe_results")
        )

    def render(self):
        output = StringIO()
        with redirect_stdout(output):
            rcl.main(Namespace(cpus_per_task=12, mem="96G", sort_by_start=True))
        return output.getvalue()

    def test_reports_nodes_without_double_counting_gpus(self):
        text = self.render()

        self.assertIn(
            "224 CPUs / 2011 GB / 4x nvidia_b200 / 8x nvidia_b200_2g.45gb / "
            "4x nvidia_b200_3g.90gb (1/1):",
            text,
        )
        self.assertNotIn("x gpu", text)
        self.assertIn("account=rcl, QOS=normal", text)

    def test_probes_each_account_and_qos_up_to_its_gpu_limits(self):
        self.assoc_mock.return_value = AccountLimitsTests.MULTI_QOS_ASSOC_MGR

        self.render()

        self.assertEqual(
            self.probe_mock.call_args_list,
            [
                call(
                    {"nvidia_b200": 1, "nvidia_b200_2g.45gb": 3, "nvidia_b200_3g.90gb": 1},
                    account="rcl",
                    qos="normal",
                    cpus_per_task=12,
                    mem="96G",
                ),
                call(
                    {"nvidia_b200": 2, "nvidia_b200_2g.45gb": 2, "nvidia_b200_3g.90gb": 2},
                    account="rcl",
                    qos="opportunistic",
                    cpus_per_task=12,
                    mem="96G",
                ),
            ],
        )
        self.assertEqual(
            self.print_probe_mock.call_args_list,
            [
                call(
                    [],
                    title=f"Job feasibility (account=rcl, QOS={qos})",
                    sort_by_start=True,
                    clean=rcl.clean_result,
                    notes=False,
                )
                for qos in ("normal", "opportunistic")
            ],
        )

    def test_prints_each_feasibility_table_after_its_limits_table(self):
        self.assoc_mock.return_value = AccountLimitsTests.MULTI_QOS_ASSOC_MGR
        self.print_probe_mock.side_effect = lambda results, **kwargs: print(
            f"{kwargs['title']}:"
        )

        text = self.render()

        headings = [
            line
            for line in text.splitlines()
            if line.startswith(("Account limits (", "Job feasibility", "Run command:"))
        ]
        self.assertEqual(
            headings,
            [
                "Account limits (account=rcl, QOS=normal):",
                "Job feasibility (account=rcl, QOS=normal):",
                "Account limits (account=rcl, QOS=opportunistic):",
                "Job feasibility (account=rcl, QOS=opportunistic):",
                "Run command: sbatch holds the allocation with 'sleep infinity' until "
                "the time limit; srun opens a Bash shell.",
            ],
        )

    def test_probes_unscoped_up_to_node_capacity_when_limits_are_unavailable(self):
        self.assoc_mock.return_value = None

        text = self.render()

        self.probe_mock.assert_called_once_with(
            CAPACITIES, account=None, qos=None, cpus_per_task=12, mem="96G"
        )
        self.print_probe_mock.assert_called_once_with(
            [],
            title="Job feasibility",
            sort_by_start=True,
            clean=rcl.clean_result,
            notes=False,
        )
        self.assertEqual(text.count("Run command: sbatch holds"), 1)


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

    NORMAL_ASSOC_MGR = """Current Association Manager state

Association Records

ClusterName=rcl Account=rcl UserName=me(24918) Partition= Priority=0 ID=32
    ParentAccount= Lineage=/rcl/0-me/ DefAssoc=Yes

QOS Records

QOS=normal(1)
    MaxTRESPJ=
    Account Limits
      rcl
        MaxJobsPA=N(5) MaxSubmitJobsPA=N(5)
    User Limits
      someone(24958)
        MaxJobsPU=16(5) MaxSubmitJobsPU=100(5)
        MaxTRESPU=cpu=64(56),mem=524288(290816),gres/gpu=4(4),gres/gpu:nvidia_b200=1(1),gres/gpu:nvidia_b200_2g.45gb=3(2),gres/gpu:nvidia_b200_3g.90gb=1(1)
      me(24918)
        MaxJobsPU=16(1) MaxSubmitJobsPU=100(1)
        MaxTRESPU=cpu=64(8),mem=524288(65536),gres/gpu=4(1),gres/gpu:nvidia_b200=1(0),gres/gpu:nvidia_b200_2g.45gb=3(0),gres/gpu:nvidia_b200_3g.90gb=1(1)
"""

    MULTI_QOS_ASSOC_MGR = NORMAL_ASSOC_MGR + """
QOS=opportunistic(16)
    MaxTRESPJ=cpu=32,mem=131072,gres/gpu=2
    Account Limits
      rcl
        MaxJobsPA=N(2) MaxSubmitJobsPA=N(3)
    User Limits
      someone(24958)
        MaxJobsPU=N(1) MaxSubmitJobsPU=N(1)
        MaxTRESPU=cpu=N(32),mem=N(131072),gres/gpu=N(1)
      me(24918)
        MaxJobsPU=N(2) MaxSubmitJobsPU=N(3)
        MaxTRESPU=cpu=N(16),mem=N(65536),gres/gpu=N(1)
QOS=limited(2)
    MaxTRESPJ=cpu=8
    Account Limits
      guests
        MaxJobsPA=N(1) MaxSubmitJobsPA=N(1)
    User Limits
        No Users
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
    def test_reports_the_matching_account_and_qos(self, assoc_mock):
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
    def test_reports_per_user_resource_limits_and_the_callers_usage(self, assoc_mock):
        assoc_mock.return_value = self.NORMAL_ASSOC_MGR
        text = self.render()

        self.assertIn("account=rcl, QOS=normal", text)
        for label, limit, usage in [
            (r"CPUs per user", 64, 8),
            (r"Memory per user \(GB\)", 512, 64),
            (r"GPUs per user", 4, 1),
            (r"nvidia_b200 per user", 1, 0),
            (r"nvidia_b200_2g\.45gb per user", 3, 0),
            (r"nvidia_b200_3g\.90gb per user", 1, 1),
        ]:
            with self.subTest(label=label):
                self.assertRegex(text, rf"{label}\s+\|\s+{limit}\s+\|\s+{usage}")
        self.assertNotIn("per job", text)

    @patch.object(common, "fetch_assoc_mgr")
    def test_borrows_resource_caps_without_borrowing_another_users_usage(self, assoc_mock):
        assoc_mock.return_value = self.NORMAL_ASSOC_MGR.replace(
            "      me(24918)", "      another(24919)"
        )
        text = self.render()

        self.assertRegex(text, r"CPUs per user\s+\|\s+64\s+\|\s+\?")
        self.assertRegex(text, r"Memory per user \(GB\)\s+\|\s+512\s+\|\s+\?")
        self.assertRegex(text, r"GPUs per user\s+\|\s+4\s+\|\s+\?")

    @patch.object(common, "fetch_assoc_mgr")
    def test_keeps_per_job_and_per_user_caps_distinct(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR + (
            "        MaxTRESPU=cpu=N(8),mem=N(65536),gres/gpu=1(1)\n"
        )
        text = self.render()

        self.assertRegex(text, r"CPUs per job\s+\|\s+8\s+\|\s+-")
        self.assertRegex(text, r"Memory per job \(GB\)\s+\|\s+64\s+\|\s+-")
        self.assertRegex(text, r"GPUs per job\s+\|\s+1\s+\|\s+-")
        self.assertRegex(text, r"GPUs per user\s+\|\s+1\s+\|\s+\?")
        self.assertNotIn("CPUs per user", text)
        self.assertNotIn("Memory per user", text)

    @patch.object(common, "fetch_assoc_mgr")
    def test_preserves_zero_resource_caps(self, assoc_mock):
        assoc_mock.return_value = self.NORMAL_ASSOC_MGR.replace(
            "gres/gpu:nvidia_b200=1(", "gres/gpu:nvidia_b200=0("
        )

        self.assertRegex(self.render(), r"nvidia_b200 per user\s+\|\s+0\s+\|\s+0")

    @patch.object(common, "fetch_assoc_mgr")
    def test_marks_usage_unknown_when_squeue_is_unavailable(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR

        self.assertRegex(self.render(), r"Jobs running\s+\|\s+1\s+\|\s+\?")

    def test_notes_when_the_limits_cannot_be_read(self):
        with patch.object(common, "fetch_assoc_mgr", return_value=None):
            self.assertIn("unavailable", self.render())

    @patch.object(common, "fetch_assoc_mgr")
    def test_returns_the_reported_qos_records_for_probe_sizing(self, assoc_mock):
        assoc_mock.return_value = self.MULTI_QOS_ASSOC_MGR
        records = common.parse_qos_records(self.MULTI_QOS_ASSOC_MGR)

        with (
            patch.object(common, "fetch_user_job_counts", return_value=None),
            redirect_stdout(StringIO()),
        ):
            reported = rcl.print_account_limits("me")

        self.assertEqual(
            reported,
            {"rcl": {"normal": records["normal"], "opportunistic": records["opportunistic"]}},
        )

    @patch.object(common, "fetch_user_job_counts")
    @patch.object(common, "fetch_assoc_mgr")
    def test_reports_every_account_of_the_user(self, assoc_mock, counts_mock):
        assoc_mock.return_value = self.MULTI_QOS_ASSOC_MGR.replace(
            "    ParentAccount= Lineage=/rcl/0-me/ DefAssoc=Yes\n",
            "    ParentAccount= Lineage=/rcl/0-me/ DefAssoc=Yes\n"
            "ClusterName=rcl Account=guests UserName=me(24918) Partition= Priority=0 ID=33\n"
            "    ParentAccount= Lineage=/guests/0-me/ DefAssoc=No\n",
        )
        counts_mock.return_value = {"running": 0, "pending": 0, "total": 0}
        records = common.parse_qos_records(self.MULTI_QOS_ASSOC_MGR)
        output = StringIO()

        with redirect_stdout(output):
            reported = rcl.print_account_limits("me")

        self.assertEqual(
            reported,
            {
                "guests": {"limited": records["limited"]},
                "rcl": {"normal": records["normal"], "opportunistic": records["opportunistic"]},
            },
        )
        text = output.getvalue()
        self.assertLess(text.index("account=guests, QOS=limited"), text.index("account=rcl, QOS=normal"))
        self.assertEqual(
            counts_mock.call_args_list,
            [call("me", qos="limited"), call("me", qos="normal"), call("me", qos="opportunistic")],
        )

    def test_returns_no_records_when_nothing_is_reported(self):
        for assoc_mgr in (
            None,
            self.ASSOC_MGR.replace("UserName=me(24918)", "UserName="),
            self.ASSOC_MGR.replace("      guests", "      other"),
        ):
            with (
                self.subTest(assoc_mgr=assoc_mgr and assoc_mgr[-40:]),
                patch.object(common, "fetch_assoc_mgr", return_value=assoc_mgr),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(rcl.print_account_limits("me"), {})

    @patch.object(common, "fetch_assoc_mgr")
    def test_notes_when_no_qos_lists_the_account(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR.replace("      guests", "      other")

        self.assertIn("no QOS found", self.render())

    @patch.object(common, "fetch_user_job_counts")
    @patch.object(common, "fetch_assoc_mgr")
    def test_reports_each_matching_qos_with_its_own_limits_and_usage(
        self, assoc_mock, counts_mock
    ):
        assoc_mock.return_value = self.MULTI_QOS_ASSOC_MGR
        counts = {
            "normal": {"running": 1, "pending": 0, "total": 1},
            "opportunistic": {"running": 2, "pending": 1, "total": 3},
        }
        counts_mock.side_effect = lambda user, qos: counts[qos]
        output = StringIO()

        with redirect_stdout(output):
            rcl.print_account_limits("me")

        tables = output.getvalue().split("Account limits ")
        self.assertEqual(len(tables), 3)
        normal, opportunistic = tables[1:]
        self.assertIn("account=rcl, QOS=normal", normal)
        self.assertIn("account=rcl, QOS=opportunistic", opportunistic)
        self.assertRegex(normal, r"Jobs running\s+\|\s+16\s+\|\s+1")
        self.assertRegex(normal, r"Jobs submitted\s+\|\s+100\s+\|\s+1")
        self.assertRegex(normal, r"CPUs per user\s+\|\s+64\s+\|\s+8")
        self.assertNotIn("CPUs per job", normal)
        self.assertRegex(opportunistic, r"Jobs running\s+\|\s+no limit\s+\|\s+2")
        self.assertRegex(opportunistic, r"Jobs submitted\s+\|\s+no limit\s+\|\s+3")
        self.assertRegex(opportunistic, r"CPUs per job\s+\|\s+32\s+\|\s+-")
        self.assertNotIn("CPUs per user", opportunistic)
        self.assertNotIn("QOS=limited", output.getvalue())
        self.assertEqual(
            counts_mock.call_args_list,
            [call("me", qos="normal"), call("me", qos="opportunistic")],
        )

    @patch.object(common, "fetch_assoc_mgr")
    def test_reports_unknown_job_caps_when_a_qos_has_no_user_entries(self, assoc_mock):
        assoc_mock.return_value = self.ASSOC_MGR.partition("    User Limits")[0] + (
            "    User Limits\n        No Users\n"
        )

        text = self.render({"running": 0, "pending": 0, "total": 0})

        self.assertRegex(text, r"Jobs running\s+\|\s+\?\s+\|\s+0")
        self.assertRegex(text, r"Jobs submitted\s+\|\s+\?\s+\|\s+0")
        self.assertRegex(text, r"CPUs per job\s+\|\s+8\s+\|\s+-")

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
