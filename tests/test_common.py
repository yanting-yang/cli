import subprocess
import shlex
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
        self.assertEqual(common.qos_names_for_account(self.records, "guests"), ["limited"])

    def test_returns_empty_list_for_an_unknown_account(self):
        self.assertEqual(common.qos_names_for_account(self.records, "other"), [])

    def test_returns_sorted_names_when_several_qos_share_an_account(self):
        self.records["normal"]["accounts"].add("guests")

        self.assertEqual(
            common.qos_names_for_account(self.records, "guests"), ["limited", "normal"]
        )

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


class ParseUserAccountsTests(unittest.TestCase):
    def test_returns_every_current_user_account(self):
        self.assertEqual(
            common.parse_user_accounts(ASSOC_MGR, "yanting.yang"), ["guests", "staff"]
        )

    def test_matches_exact_user_and_deduplicates_partition_associations(self):
        output = """Association Records
ClusterName=killarney Account=project UserName=me(123) Partition= ID=1
ClusterName=killarney Account=project UserName=me(123) Partition=gpu ID=2
ClusterName=killarney Account=other UserName=someone(124) Partition= ID=3
ClusterName=killarney Account=prefix UserName=me.extra(125) Partition= ID=4
ClusterName=killarney Account=parent UserName= Partition= ID=5
QOS Records
ClusterName=killarney Account=outside UserName=me(123) Partition= ID=6
"""

        self.assertEqual(common.parse_user_accounts(output, "me"), ["project"])
        self.assertEqual(common.parse_user_accounts(output, "missing"), [])
        self.assertEqual(common.parse_user_accounts("", "me"), [])


class ParseAccountQosTests(unittest.TestCase):
    def test_combines_duplicate_associations_and_sorts_qos_names(self):
        output = (
            "project|normal,interac|\n"
            "project|normal,extra|\n"
            "other|normal|\n"
            "project|normal|\n"
        )

        self.assertEqual(
            common.parse_account_qos(output),
            {"other": ["normal"], "project": ["extra", "interac", "normal"]},
        )

    def test_preserves_accounts_with_no_qos_and_ignores_invalid_rows(self):
        self.assertEqual(
            common.parse_account_qos("empty||\n spaced | normal, interac |\n|normal|\ninvalid\n"),
            {"empty": [], "spaced": ["interac", "normal"]},
        )
        self.assertEqual(common.parse_account_qos(""), {})


MULTI_ACCOUNT_QOS = """QOS Records
QOS=normal(1)
    MaxWallPJ=
    MaxTRESPJ=cpu=64
    MaxTRESPN=gres/gpu=4,gres/gpu:h100=2
    Account Limits
      project-a
        MaxJobsPA=16(3) MaxJobsAccruePA=N(0) MaxSubmitJobsPA=N(6)
        MaxTRESPA=cpu=128(24),mem=N(65536),gres/gpu=0(0),gres/gpu:h100=2(1)
      project-b
        MaxJobsPA=16(1) MaxSubmitJobsPA=N(2)
        MaxTRESPA=cpu=128(8),mem=N(16384),gres/gpu=0(0),gres/gpu:h100=2(0)
    User Limits
      other(456)
        MaxJobsPU=4(1) MaxSubmitJobsPU=8(2)
        MaxTRESPU=cpu=64(8),gres/gpu=2(1)
QOS=interac(2)
    MaxWallPJ=180
    MaxTRESPJ=gres/gpu=1
    Account Limits
      project-a
        MaxJobsPA=0(0) MaxSubmitJobsPA=bad(9)
        MaxTRESPA=cpu=bad(4),mem=0(0)
      project-c
    User Limits
"""


class QosAccountLimitTests(unittest.TestCase):
    def setUp(self):
        self.records = common.parse_qos_records(MULTI_ACCOUNT_QOS)

    def test_parses_per_account_caps_separately_from_account_usage(self):
        record = self.records["normal"]
        self.assertEqual(
            record["account_limits"]["project-a"],
            {
                "max_jobs": 16,
                "max_submit_jobs": None,
                "max_tres_pa": {
                    "cpu": 128, "mem": None, "gres/gpu": 0, "gres/gpu:h100": 2,
                },
            },
        )
        self.assertEqual(record["accounts"], {"project-a", "project-b"})
        self.assertEqual(
            record["account_job_usage"],
            {"project-a": {"running": 3, "total": 6}, "project-b": {"running": 1, "total": 2}},
        )
        self.assertEqual(
            record["account_tres_usage"],
            {
                "project-a": {"cpu": 24, "mem": 65536, "gres/gpu": 0, "gres/gpu:h100": 1},
                "project-b": {"cpu": 8, "mem": 16384, "gres/gpu": 0, "gres/gpu:h100": 0},
            },
        )
        self.assertEqual(record["user_tres_usage"], {"other": {"cpu": 8, "gres/gpu": 1}})

    def test_keeps_qos_records_separate_and_preserves_zero_and_missing_caps(self):
        record = self.records["interac"]
        self.assertEqual(
            record["account_limits"],
            {"project-a": {"max_jobs": 0, "max_tres_pa": {"mem": 0}}, "project-c": {}},
        )
        self.assertEqual(record["account_job_usage"], {"project-a": {"running": 0}})
        self.assertEqual(record["account_tres_usage"], {"project-a": {"mem": 0}})

    def test_reads_per_node_caps_and_per_job_time_in_minutes(self):
        self.assertEqual(
            self.records["normal"]["max_tres_pn"], {"gres/gpu": 4, "gres/gpu:h100": 2}
        )
        self.assertIsNone(self.records["normal"]["max_wall_pj"])
        self.assertEqual(self.records["interac"]["max_tres_pn"], {})
        self.assertEqual(self.records["interac"]["max_wall_pj"], 180)
        self.assertEqual(
            common.parse_qos_records(MULTI_ACCOUNT_QOS.replace("MaxWallPJ=180", "MaxWallPJ=0"))[
                "interac"
            ]["max_wall_pj"],
            0,
        )

    def test_distinguishes_missing_or_malformed_time_from_unset_time(self):
        output = """QOS Records
QOS=missing(1)
    MaxTRESPJ=
QOS=malformed(2)
    MaxWallPJ=invalid
QOS=unset(3)
    MaxWallPJ=N
"""
        records = common.parse_qos_records(output)

        self.assertNotIn("max_wall_pj", records["missing"])
        self.assertNotIn("max_wall_pj", records["malformed"])
        self.assertIsNone(records["unset"]["max_wall_pj"])

    def test_prefers_requested_accounts_limits(self):
        record = self.records["normal"]
        record["account_limits"]["project-b"]["max_jobs"] = 7

        self.assertEqual(common.qos_account_limits(record, "project-b")["max_jobs"], 7)

    def test_borrows_only_caps_when_the_account_has_no_cached_usage(self):
        record = self.records["normal"]
        self.assertEqual(
            common.qos_account_limits(record, "inactive"), record["account_limits"]["project-a"]
        )
        self.assertNotIn("inactive", record["account_job_usage"])
        self.assertNotIn("inactive", record["account_tres_usage"])
        self.assertEqual(record["account_job_usage"]["project-a"], {"running": 3, "total": 6})

    def test_returns_unknown_caps_if_no_account_entry_has_limits(self):
        self.assertEqual(common.qos_account_limits({"account_limits": {}}, "inactive"), {})
        self.assertEqual(
            common.qos_account_limits({"account_limits": {"other": {}}}, "inactive"), {}
        )


class FetchUserAccountQosTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_fetches_full_qos_names_for_the_current_user_and_cluster(self, run_mock):
        run_mock.return_value = Mock(stdout="project|normal,interac|\n", returncode=0)

        self.assertEqual(
            common.fetch_user_account_qos("me", "killarney"),
            {"project": ["interac", "normal"]},
        )
        run_mock.assert_called_once_with(
            [
                "sacctmgr", "-nP", "show", "assoc", "where", "user=me",
                "cluster=killarney", "format=Account,QOS%1000",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    @patch.object(common.subprocess, "run")
    def test_distinguishes_no_associations_from_a_failed_query(self, run_mock):
        run_mock.return_value = Mock(stdout="", returncode=0)
        self.assertEqual(common.fetch_user_account_qos("me", "killarney"), {})

    def test_reports_unavailable_when_command_is_missing_fails_or_times_out(self):
        for error in (
            FileNotFoundError(),
            subprocess.CalledProcessError(1, "sacctmgr"),
            subprocess.TimeoutExpired("sacctmgr", 10),
        ):
            with self.subTest(error=type(error).__name__), patch.object(
                common.subprocess, "run", side_effect=error
            ):
                self.assertIsNone(common.fetch_user_account_qos("me", "killarney"))


class UserJobCountTests(unittest.TestCase):
    @patch.object(common.subprocess, "run")
    def test_counts_running_and_pending_jobs(self, run_mock):
        run_mock.return_value = Mock(stdout="R\nPD\nR\nCG\n", stderr="", returncode=0)

        self.assertEqual(
            common.fetch_user_job_counts("me"),
            {"running": 2, "pending": 1, "total": 4},
        )
        run_mock.assert_called_once_with(
            ["squeue", "-h", "-u", "me", "-o", "%t"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch.object(common.subprocess, "run")
    def test_filters_jobs_by_qos_without_restricting_account(self, run_mock):
        run_mock.return_value = Mock(stdout="R\nPD\n", stderr="", returncode=0)

        self.assertEqual(
            common.fetch_user_job_counts("me", qos="opportunistic"),
            {"running": 1, "pending": 1, "total": 2},
        )
        run_mock.assert_called_once_with(
            ["squeue", "-h", "-u", "me", "-o", "%t", "--qos", "opportunistic"],
            capture_output=True,
            text=True,
            check=True,
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

    def test_reports_none_when_qos_filtered_query_fails(self):
        with patch.object(
            common.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "squeue"),
        ):
            self.assertIsNone(common.fetch_user_job_counts("me", qos="opportunistic"))


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


class FormatRunCommandTests(unittest.TestCase):
    def test_quotes_shell_metacharacters_and_preserves_original_probe(self):
        directives = ["--test-only", "--comment=two words; echo $HOME", "--time=3:00:00"]
        original = directives.copy()

        command = common.format_run_command("sbatch", directives)

        self.assertEqual(
            shlex.split(command),
            ["sbatch", "--comment=two words; echo $HOME", "--time=3:00:00", "job.sh"],
        )
        self.assertIn("'--comment=two words; echo $HOME'", command)
        self.assertEqual(directives, original)


if __name__ == "__main__":
    unittest.main()
