import subprocess
import unittest
from unittest.mock import Mock, patch

from node_state.clusters import common

NODE_BLOCK = """NodeName=rcl-nv2.ece.ubc.ca Arch=x86_64 CoresPerSocket=56
   CPUAlloc=88 CPUEfctv=192 CPUTot=224 CPULoad=18.41
   RealMemory=2060000 AllocMem=706560 FreeMem=224124 Sockets=2 Boards=1
   CoreSpecCount=16 CPUSpecList=96-111,208-223 MemSpecLimit=65536
   State=MIXED ThreadsPerCore=2 TmpDisk=0 Weight=1
   CfgTRES=cpu=192,mem=2060000M,billing=448,gres/gpu=16,gres/gpu:nvidia_b200=4
   AllocTRES=cpu=88,mem=690G,gres/gpu=8,gres/gpu:nvidia_b200=3
"""


class ParseTresGpusTests(unittest.TestCase):
    def test_parses_typed_and_untyped_entries(self):
        tres = "cpu=192,mem=2060000M,gres/gpu=16,gres/gpu:nvidia_b200=4"

        self.assertEqual(common.parse_tres_gpus(tres), {"gpu": 16, "nvidia_b200": 4})

    def test_returns_empty_mapping_without_gpus(self):
        self.assertEqual(common.parse_tres_gpus("cpu=48,mem=257000M"), {})


class GpuRollupTests(unittest.TestCase):
    def test_drops_the_rollup_when_per_model_counts_exist(self):
        gpus = {
            "gpu": 16,
            "nvidia_b200": 4,
            "nvidia_b200_2g.45gb": 8,
            "nvidia_b200_3g.90gb": 4,
        }

        self.assertEqual(
            common.remove_untyped_gpu_rollup(gpus),
            {"nvidia_b200": 4, "nvidia_b200_2g.45gb": 8, "nvidia_b200_3g.90gb": 4},
        )

    def test_keeps_the_rollup_when_it_is_the_only_information(self):
        self.assertEqual(common.remove_untyped_gpu_rollup({"gpu": 4}), {"gpu": 4})

    def test_leaves_cpu_only_nodes_empty(self):
        self.assertEqual(common.remove_untyped_gpu_rollup({}), {})

    def test_normalizes_both_configured_and_allocated_gpus(self):
        node = common.normalize_gpu_rollups(
            {
                "cfg_gpus": {"gpu": 16, "nvidia_b200": 4},
                "alloc_gpus": {"gpu": 8, "nvidia_b200": 3},
            }
        )

        self.assertEqual(node["cfg_gpus"], {"nvidia_b200": 4})
        self.assertEqual(node["alloc_gpus"], {"nvidia_b200": 3})


class ParseNodesTests(unittest.TestCase):
    def test_parses_core_fields(self):
        node = common.parse_nodes(NODE_BLOCK)[0]

        self.assertEqual(node["name"], "rcl-nv2.ece.ubc.ca")
        self.assertEqual(node["state"], "MIXED")
        self.assertEqual(node["cpu_tot"], 224)
        self.assertEqual(node["cpu_alloc"], 88)
        self.assertEqual(node["mem_tot"], 2060000)
        self.assertEqual(node["mem_alloc"], 706560)
        self.assertEqual(node["cfg_gpus"], {"gpu": 16, "nvidia_b200": 4})
        self.assertEqual(node["alloc_gpus"], {"gpu": 8, "nvidia_b200": 3})

    def test_records_effective_cpus_separately_from_total(self):
        node = common.parse_nodes(NODE_BLOCK)[0]

        self.assertEqual(node["cpu_efctv"], 192)

    def test_falls_back_to_cpu_tot_when_effective_count_is_absent(self):
        block = NODE_BLOCK.replace("CPUEfctv=192 ", "")

        self.assertEqual(common.parse_nodes(block)[0]["cpu_efctv"], 224)


class HardwareGroupingTests(unittest.TestCase):
    def test_identifies_gpu_nodes(self):
        self.assertTrue(common.is_gpu_node({"cfg_gpus": {"gpu": 4, "l40s": 4}}))
        self.assertFalse(common.is_gpu_node({"cfg_gpus": {}}))

    def test_groups_small_real_memory_differences_by_whole_gibibyte(self):
        base_node = {
            "cpu_tot": 64,
            "mem_tot": 515472,
            "cfg_gpus": {"gpu": 4, "l40s": 4},
        }
        nearly_identical_node = {**base_node, "mem_tot": 515478}

        key = common.node_hw_key(base_node)

        self.assertEqual(key, common.node_hw_key(nearly_identical_node))
        self.assertEqual(
            common.hw_key_label(key), "64 CPUs / 503 GB / 4x gpu / 4x l40s"
        )

    def test_keeps_different_whole_gibibyte_values_separate(self):
        smaller_node = {"cpu_tot": 64, "mem_tot": 515472, "cfg_gpus": {}}
        larger_node = {"cpu_tot": 64, "mem_tot": 516096, "cfg_gpus": {}}

        self.assertNotEqual(
            common.node_hw_key(smaller_node), common.node_hw_key(larger_node)
        )

    def test_keys_on_the_requested_cpu_field(self):
        node = {"cpu_tot": 224, "cpu_efctv": 192, "mem_tot": 515472, "cfg_gpus": {}}

        self.assertEqual(common.node_hw_key(node, "cpu_efctv")[0], 192)
        self.assertEqual(common.node_hw_key(node)[0], 224)


class StateCountTests(unittest.TestCase):
    def test_formats_unique_states_with_counts(self):
        nodes = [
            {"state": "MIXED"},
            {"state": "IDLE"},
            {"state": "MIXED"},
        ]

        self.assertEqual(common.format_state_counts(nodes), "IDLE=1, MIXED=2")

    def test_formats_no_states_as_an_empty_string(self):
        self.assertEqual(common.format_state_counts([]), "")


ASSOC_MGR = """Current Association Manager state

Association Records

ClusterName=rcl Account=root UserName= Partition= Priority=0 ID=1
    ParentAccount= Lineage=/ DefAssoc=No
    MaxJobs= MaxJobsAccrue= MaxSubmitJobs= MaxWallPJ=
ClusterName=rcl Account=staff UserName=yanting.yang(24918) Partition= Priority=0 ID=9
    ParentAccount= Lineage=/staff/0-yanting.yang/ DefAssoc=No
    MaxJobs= MaxJobsAccrue= MaxSubmitJobs= MaxWallPJ=
ClusterName=rcl Account=guests UserName=yanting.yang(24918) Partition= Priority=0 ID=32
    ParentAccount= Lineage=/guests/0-yanting.yang/ DefAssoc=Yes
    MaxJobs= MaxJobsAccrue= MaxSubmitJobs= MaxWallPJ=

QOS Records

QOS=normal(1)
    UsageRaw=311045818.638041
    MaxWallPJ=
    MaxTRESPJ=
    PreemptMode=OFF
    Account Limits
      rcl
        MaxJobsPA=N(8) MaxJobsAccruePA=N(0) MaxSubmitJobsPA=N(8)
    User Limits
      mahdik(18821)
        MaxJobsPU=16(5) MaxJobsAccruePU=4(0) MaxSubmitJobsPU=100(5)
        MaxTRESPU=cpu=64(56),mem=524288(290816),gres/gpu=4(4)
QOS=limited(2)
    UsageRaw=55306116.454286
    MaxWallPJ=
    MaxTRESPJ=cpu=8,mem=65536,gres/gpu=1,gres/gpu:nvidia_b200=1,gres/gpu:nvidia_b200_3g.90gb=1
    PreemptMode=OFF
    Account Limits
      guests
        MaxJobsPA=N(1) MaxJobsAccruePA=N(0) MaxSubmitJobsPA=N(1)
    User Limits
      yuxiang.fu(24958)
        MaxJobsPU=1(1) MaxJobsAccruePU=N(0) MaxSubmitJobsPU=N(1)
        MaxTRESPU=cpu=N(8),mem=N(65536),gres/gpu=1(1)
"""


class ParseTresValuesTests(unittest.TestCase):
    def test_keeps_tres_names_containing_colons_intact(self):
        values = common.parse_tres_values("cpu=8,mem=65536,gres/gpu:nvidia_b200=1")

        self.assertEqual(values, {"cpu": 8, "mem": 65536, "gres/gpu:nvidia_b200": 1})

    def test_ignores_empty_and_non_numeric_entries(self):
        self.assertEqual(common.parse_tres_values(""), {})
        self.assertEqual(common.parse_tres_values("cpu=unlimited,mem=64"), {"mem": 64})


class ParseTresLimitsTests(unittest.TestCase):
    def test_separates_resource_limits_from_current_usage(self):
        limits, usage = common.parse_tres_limits(
            "cpu=64(56),mem=524288(290816),gres/gpu=4(4)"
        )

        self.assertEqual(limits, {"cpu": 64, "mem": 524288, "gres/gpu": 4})
        self.assertEqual(usage, {"cpu": 56, "mem": 290816, "gres/gpu": 4})

    def test_keeps_unlimited_resources_and_zero_caps_distinct(self):
        limits, usage = common.parse_tres_limits("cpu=N(8),gres/gpu=0(0)")

        self.assertEqual(limits, {"cpu": None, "gres/gpu": 0})
        self.assertEqual(usage, {"cpu": 8, "gres/gpu": 0})

    def test_preserves_model_gpu_names_alongside_the_total_gpu_limit(self):
        limits, usage = common.parse_tres_limits(
            "gres/gpu=4(2),gres/gpu:nvidia_b200=1(0),"
            "gres/gpu:nvidia_b200_2g.45gb=3(2)"
        )

        self.assertEqual(
            limits,
            {
                "gres/gpu": 4,
                "gres/gpu:nvidia_b200": 1,
                "gres/gpu:nvidia_b200_2g.45gb": 3,
            },
        )
        self.assertEqual(
            usage,
            {
                "gres/gpu": 2,
                "gres/gpu:nvidia_b200": 0,
                "gres/gpu:nvidia_b200_2g.45gb": 2,
            },
        )

    def test_ignores_empty_and_malformed_entries_without_losing_valid_ones(self):
        self.assertEqual(common.parse_tres_limits(""), ({}, {}))
        limits, usage = common.parse_tres_limits(
            "garbage,cpu=bad(3),mem=64,gres/gpu=4(bad),"
            "node=3(1)trailing,,cpu=64(0)"
        )

        self.assertEqual(limits, {"cpu": 64})
        self.assertEqual(usage, {"cpu": 0})


class ParseLimitTests(unittest.TestCase):
    def test_reads_a_numeric_limit(self):
        self.assertEqual(common.parse_limit("16(5)"), 16)

    def test_reads_an_absent_limit_as_none(self):
        self.assertIsNone(common.parse_limit("N(0)"))

    def test_returns_none_for_unparseable_text(self):
        self.assertIsNone(common.parse_limit("junk"))


class ParseDefaultAccountTests(unittest.TestCase):
    def test_prefers_the_default_association(self):
        self.assertEqual(
            common.parse_default_account(ASSOC_MGR, "yanting.yang"), "guests"
        )

    def test_returns_none_for_an_unknown_user(self):
        self.assertIsNone(common.parse_default_account(ASSOC_MGR, "nobody"))


class ParseQosRecordsTests(unittest.TestCase):
    def setUp(self):
        self.records = common.parse_qos_records(ASSOC_MGR)

    def test_finds_every_qos(self):
        self.assertEqual(sorted(self.records), ["limited", "normal"])

    def test_reads_per_job_tres_caps(self):
        self.assertEqual(
            self.records["limited"]["max_tres_pj"],
            {
                "cpu": 8,
                "mem": 65536,
                "gres/gpu": 1,
                "gres/gpu:nvidia_b200": 1,
                "gres/gpu:nvidia_b200_3g.90gb": 1,
            },
        )

    def test_leaves_per_job_caps_empty_when_unset(self):
        self.assertEqual(self.records["normal"]["max_tres_pj"], {})

    def test_records_the_accounts_each_qos_governs(self):
        self.assertEqual(self.records["limited"]["accounts"], {"guests"})
        self.assertEqual(self.records["normal"]["accounts"], {"rcl"})

    def test_reads_per_user_job_and_resource_limits(self):
        self.assertEqual(
            self.records["limited"]["user_limits"]["yuxiang.fu"],
            {
                "max_jobs": 1,
                "max_submit_jobs": None,
                "max_tres_pu": {"cpu": None, "mem": None, "gres/gpu": 1},
            },
        )
        self.assertEqual(
            self.records["normal"]["user_limits"]["mahdik"],
            {
                "max_jobs": 16,
                "max_submit_jobs": 100,
                "max_tres_pu": {"cpu": 64, "mem": 524288, "gres/gpu": 4},
            },
        )

    def test_records_resource_usage_separately_from_borrowable_limits(self):
        self.assertEqual(
            self.records["normal"]["user_tres_usage"],
            {"mahdik": {"cpu": 56, "mem": 290816, "gres/gpu": 4}},
        )
        self.assertEqual(
            self.records["limited"]["user_tres_usage"],
            {"yuxiang.fu": {"cpu": 8, "mem": 65536, "gres/gpu": 1}},
        )

    def test_keeps_each_users_resource_usage_separate(self):
        output = ASSOC_MGR.replace(
            "QOS=limited(2)",
            "      me(12345)\n"
            "        MaxJobsPU=16(1) MaxSubmitJobsPU=100(1)\n"
            "        MaxTRESPU=cpu=64(8),mem=524288(65536),gres/gpu=4(0)\n"
            "QOS=limited(2)",
        )
        record = common.parse_qos_records(output)["normal"]

        self.assertEqual(record["user_limits"]["me"], record["user_limits"]["mahdik"])
        self.assertEqual(
            record["user_tres_usage"],
            {
                "mahdik": {"cpu": 56, "mem": 290816, "gres/gpu": 4},
                "me": {"cpu": 8, "mem": 65536, "gres/gpu": 0},
            },
        )

    def test_ignores_association_records(self):
        # Association blocks also contain MaxJobs=/MaxSubmitJobs= fields.
        self.assertNotIn("yanting.yang", self.records["limited"]["user_limits"])


class QosLookupTests(unittest.TestCase):
    def setUp(self):
        self.records = common.parse_qos_records(ASSOC_MGR)

    def test_maps_an_account_to_its_qos(self):
        self.assertEqual(common.qos_for_account(self.records, "guests"), "limited")

    def test_returns_none_for_an_unknown_account(self):
        self.assertIsNone(common.qos_for_account(self.records, "other"))

    def test_returns_none_when_several_qos_share_an_account(self):
        self.records["normal"]["accounts"].add("guests")

        self.assertIsNone(common.qos_for_account(self.records, "guests"))

    def test_prefers_the_users_own_limits(self):
        record = self.records["limited"]
        record["user_limits"]["me"] = {"max_jobs": 3, "max_submit_jobs": 9}

        self.assertEqual(
            common.qos_user_limits(record, "me"), {"max_jobs": 3, "max_submit_jobs": 9}
        )

    def test_borrows_limits_when_the_user_has_no_entry_yet(self):
        # QOS per-user limits are one value applied to every user, so another
        # user's entry carries the same numbers.
        self.assertEqual(
            common.qos_user_limits(self.records["limited"], "yanting.yang"),
            {
                "max_jobs": 1,
                "max_submit_jobs": None,
                "max_tres_pu": {"cpu": None, "mem": None, "gres/gpu": 1},
            },
        )

    def test_borrowing_resource_limits_does_not_create_usage_for_the_user(self):
        record = self.records["normal"]

        limits = common.qos_user_limits(record, "yanting.yang")

        self.assertEqual(
            limits["max_tres_pu"], {"cpu": 64, "mem": 524288, "gres/gpu": 4}
        )
        self.assertNotIn("yanting.yang", record["user_tres_usage"])
        self.assertEqual(
            record["user_tres_usage"]["mahdik"],
            {"cpu": 56, "mem": 290816, "gres/gpu": 4},
        )

    def test_returns_empty_limits_when_no_user_entries_exist(self):
        self.assertEqual(common.qos_user_limits({"user_limits": {}}, "me"), {})


class UserJobCountTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_counts_running_and_pending_jobs(self, run_mock):
        run_mock.return_value = Mock(stdout="R\nPD\nR\nCG\n", stderr="", returncode=0)

        self.assertEqual(
            common.fetch_user_job_counts("me"),
            {"running": 2, "pending": 1, "total": 4},
        )

    @patch.object(common.subprocess, "run")
    def test_counts_an_empty_queue_as_zero(self, run_mock):
        run_mock.return_value = Mock(stdout="", stderr="", returncode=0)

        self.assertEqual(
            common.fetch_user_job_counts("me"),
            {"running": 0, "pending": 0, "total": 0},
        )

    def test_reports_none_when_squeue_is_missing(self):
        with patch.object(common.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(common.fetch_user_job_counts("me"))


SINFO_PARTITIONS = (
    "PARTITION           GRES                NODES               TIMELIMIT           \n"
    "gpubase_h100_b1     gpu:h100:8          10                  3:00:00             \n"
    "gpubase_interac     gpu:l40s:4          25                  3:00:00             \n"
    "cpubase_bycore_b1                       4                   3:00:00             \n"
)


class ParseSinfoTableTests(unittest.TestCase):
    def test_parses_the_header_and_rows(self):
        columns, rows = common.parse_sinfo_table(SINFO_PARTITIONS)

        self.assertEqual(columns, ["PARTITION", "GRES", "NODES", "TIMELIMIT"])
        self.assertEqual(
            rows[:2],
            [
                ["gpubase_h100_b1", "gpu:h100:8", "10", "3:00:00"],
                ["gpubase_interac", "gpu:l40s:4", "25", "3:00:00"],
            ],
        )

    def test_keeps_an_empty_field_in_its_own_column(self):
        _, rows = common.parse_sinfo_table(SINFO_PARTITIONS)

        self.assertEqual(rows[-1], ["cpubase_bycore_b1", "", "4", "3:00:00"])

    def test_returns_nothing_for_empty_output(self):
        self.assertEqual(common.parse_sinfo_table(""), ([], []))

    def test_returns_no_rows_for_a_header_only_table(self):
        columns, rows = common.parse_sinfo_table("PARTITION           GRES\n")

        self.assertEqual(columns, ["PARTITION", "GRES"])
        self.assertEqual(rows, [])


class FetchPartitionsTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_requests_the_partition_format(self, run_mock):
        run_mock.return_value = Mock(stdout=SINFO_PARTITIONS, stderr="", returncode=0)

        self.assertEqual(common.fetch_partitions(), SINFO_PARTITIONS)
        self.assertEqual(
            run_mock.call_args.args[0],
            ["sinfo", "--Format=Partition,Gres,Nodes,Time"],
        )

    def test_reports_none_when_sinfo_is_missing(self):
        with patch.object(common.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(common.fetch_partitions())

    def test_reports_none_when_the_command_fails(self):
        with patch.object(
            common.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "sinfo"),
        ):
            self.assertIsNone(common.fetch_partitions())


class FetchAssocMgrTests(unittest.TestCase):
    def test_reports_none_when_scontrol_is_missing(self):
        with patch.object(common.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(common.fetch_assoc_mgr())

    def test_reports_none_when_the_command_fails(self):
        with patch.object(
            common.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "scontrol"),
        ):
            self.assertIsNone(common.fetch_assoc_mgr())


class RunSbatchTestTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_extracts_estimated_start_time_and_partition(self, run_mock):
        run_mock.return_value = Mock(
            stdout=(
                "sbatch: Job 5484 to start at 2026-07-25T19:06:59 a using "
                "16 processors on nodes rack15-12 in partition mig\n"
            ),
            stderr="",
            returncode=0,
        )

        result = common.run_sbatch_test(["--test-only"])

        self.assertEqual(result["start_time"], "2026-07-25T19:06:59")
        self.assertEqual(result["partition"], "mig")
        self.assertEqual(result["result"], "Runnable")

    @patch.object(common.subprocess, "run")
    def test_reports_rejected_request(self, run_mock):
        run_mock.return_value = Mock(
            stdout="allocation failure: Requested node configuration is not available\n",
            stderr="",
            returncode=1,
        )

        result = common.run_sbatch_test(["--test-only"])

        self.assertIsNone(result["start_time"])
        self.assertIsNone(result["partition"])
        self.assertEqual(
            result["result"],
            "allocation failure: Requested node configuration is not available",
        )

    @patch.object(common.subprocess, "run")
    def test_builds_a_script_from_the_given_directives(self, run_mock):
        run_mock.return_value = Mock(stdout="", stderr="", returncode=0)

        common.run_sbatch_test(["--test-only", "--mem=32G"])

        self.assertEqual(
            run_mock.call_args.kwargs["input"],
            "#!/bin/bash\n#SBATCH --test-only\n#SBATCH --mem=32G\n",
        )

    def test_reports_missing_sbatch(self):
        with patch.object(common.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(common.run_sbatch_test([])["result"], "'sbatch' not found")


class RunSrunTestTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_runs_directives_and_extracts_the_schedule(self, run_mock):
        run_mock.return_value = Mock(
            stdout=(
                "srun: Job 4490849 to start at 2026-07-30T19:56:05 a using "
                "4 processors on nodes kn117 in partition gpubase_interac\n"
            ),
            stderr="",
            returncode=0,
        )

        result = common.run_srun_test(
            ["--test-only", "--gres=gpu:l40s:1", "--time=3:00:00"]
        )

        self.assertEqual(result["start_time"], "2026-07-30T19:56:05")
        self.assertEqual(result["partition"], "gpubase_interac")
        self.assertEqual(result["result"], "Runnable")
        self.assertEqual(
            run_mock.call_args.args[0],
            ["srun", "--test-only", "--gres=gpu:l40s:1", "--time=3:00:00"],
        )


if __name__ == "__main__":
    unittest.main()
