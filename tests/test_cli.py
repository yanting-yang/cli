import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from node_state import cli
from node_state.clusters import killarney, rcl, tamia


class CliTests(unittest.TestCase):
    def assert_parse_error(self, argv, message):
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            cli.main(argv)

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(message, stderr.getvalue())

    def test_registers_killarney(self):
        self.assertIs(cli.CLUSTER_RUNNERS["killarney"], killarney.main)

    def test_registers_tamia(self):
        self.assertIs(cli.CLUSTER_RUNNERS["tamia"], tamia.main)

    def test_registers_rcl(self):
        self.assertIs(cli.CLUSTER_RUNNERS["rcl"], rcl.main)

    def test_forwards_rcl_probe_options_with_defaults(self):
        runner = Mock()
        with patch.dict(cli.CLUSTER_RUNNERS, {"rcl": runner}):
            cli.main(["rcl", "-c", "8", "--mem", "64G", "--sort-by-start"])
            cli.main(["rcl"])

        custom, default = (probe_call.args[0] for probe_call in runner.call_args_list)
        self.assertEqual(custom.cpus_per_task, 8)
        self.assertEqual(custom.mem, "64G")
        self.assertTrue(custom.sort_by_start)
        self.assertEqual(default.cpus_per_task, 4)
        self.assertEqual(default.mem, "32G")
        self.assertFalse(default.sort_by_start)

    def test_accepts_c_with_or_without_a_space(self):
        runner = Mock()
        with patch.dict(cli.CLUSTER_RUNNERS, {"rcl": runner}):
            cli.main(["rcl", "-c", "8"])
            cli.main(["rcl", "-c16"])

        self.assertEqual(
            [probe_call.args[0].cpus_per_task for probe_call in runner.call_args_list],
            [8, 16],
        )

    def test_forwards_tamia_probe_options(self):
        runner = Mock()
        with patch.dict(cli.CLUSTER_RUNNERS, {"tamia": runner}):
            cli.main(["tamia", "-c", "8", "--mem", "64G", "--sort-by-start"])

        args = runner.call_args.args[0]
        self.assertEqual(args.cpus_per_task, 8)
        self.assertEqual(args.mem, "64G")
        self.assertTrue(args.sort_by_start)

    def test_uses_fallback_when_cluster_is_omitted(self):
        runner = Mock()
        with patch.object(cli.fallback, "main", runner):
            cli.main([])

        args = runner.call_args.args[0]
        self.assertIsNone(args.cluster)

    def test_rejects_uppercase_cluster_name(self):
        self.assert_parse_error(["KILLARNEY"], "invalid choice: 'KILLARNEY'")

    def test_forwards_killarney_probe_options(self):
        runner = Mock()
        with patch.dict(cli.CLUSTER_RUNNERS, {"killarney": runner}):
            cli.main(
                [
                    "killarney",
                    "-c",
                    "12",
                    "--mem",
                    "96G",
                    "--sort-by-start",
                ]
            )

        args = runner.call_args.args[0]
        self.assertEqual(args.cpus_per_task, 12)
        self.assertEqual(args.mem, "96G")
        self.assertTrue(args.sort_by_start)

    def test_sets_default_killarney_probe_resources(self):
        runner = Mock()
        with patch.dict(cli.CLUSTER_RUNNERS, {"killarney": runner}):
            cli.main(["killarney"])

        args = runner.call_args.args[0]
        self.assertEqual(args.cpus_per_task, 4)
        self.assertEqual(args.mem, "32G")

    def test_rejects_the_long_cpus_per_task_option(self):
        self.assert_parse_error(
            ["rcl", "--cpus-per-task", "8"],
            "unrecognized arguments: --cpus-per-task 8",
        )

    def test_rejects_probe_options_for_other_clusters(self):
        self.assert_parse_error(
            ["vulcan", "--mem", "96G"],
            "unrecognized arguments: --mem 96G",
        )

    def test_scopes_probe_options_to_killarney_help(self):
        top_level_output = StringIO()
        with redirect_stdout(top_level_output), self.assertRaises(SystemExit) as raised:
            cli.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("{killarney,rcl,tamia,vulcan}", top_level_output.getvalue())
        self.assertNotIn("-c 4", top_level_output.getvalue())

        killarney_output = StringIO()
        with redirect_stdout(killarney_output), self.assertRaises(SystemExit) as raised:
            cli.main(["killarney", "--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("-c 4", killarney_output.getvalue())
        self.assertIn("--mem", killarney_output.getvalue())
        self.assertIn("--sort-by-start", killarney_output.getvalue())

    def test_rejects_an_unsupported_cluster(self):
        self.assert_parse_error(["other"], "invalid choice: 'other'")


if __name__ == "__main__":
    unittest.main()
