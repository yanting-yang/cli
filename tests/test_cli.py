import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from node_state import cli


class CliTests(unittest.TestCase):
    @patch.object(cli.node_resources, "main")
    def test_uses_fallback_when_cluster_is_omitted(self, main_mock):
        with patch.object(sys, "argv", ["node_state"]):
            cli.main()

        main_mock.assert_called_once_with(None)

    @patch.object(cli.node_resources, "main")
    def test_forwards_cluster_name_case_insensitively(self, main_mock):
        with patch.object(sys, "argv", ["node_state", "KILLARNEY"]):
            cli.main()

        main_mock.assert_called_once_with("killarney")

    @patch.object(cli.node_resources, "main")
    def test_forwards_killarney_probe_options(self, main_mock):
        with patch.object(
            sys,
            "argv",
            [
                "node_state",
                "killarney",
                "--cpus-per-task",
                "12",
                "--mem",
                "96G",
                "--sort-by-start",
            ],
        ):
            cli.main()

        main_mock.assert_called_once_with(
            "killarney",
            probe_cpus=12,
            probe_ram="96G",
            sort_by_start=True,
        )

    @patch.object(cli.node_resources, "main")
    def test_rejects_non_positive_probe_cpu_count(self, main_mock):
        stderr = StringIO()
        with patch.object(
            sys,
            "argv",
            ["node_state", "killarney", "--cpus-per-task", "0"],
        ):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("must be at least 1", stderr.getvalue())
        main_mock.assert_not_called()

    @patch.object(cli.node_resources, "main")
    def test_rejects_probe_options_for_other_clusters(self, main_mock):
        stderr = StringIO()
        with patch.object(sys, "argv", ["node_state", "vulcan", "--mem", "96G"]):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --mem 96G", stderr.getvalue())
        main_mock.assert_not_called()

    @patch.object(cli.node_resources, "main")
    def test_rejects_legacy_probe_option_names(self, main_mock):
        for option in ("--cpu", "--cpus", "--ram"):
            with self.subTest(option=option):
                stderr = StringIO()
                with patch.object(
                    sys,
                    "argv",
                    ["node_state", "killarney", option, "12"],
                ):
                    with redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as raised:
                            cli.main()

                self.assertEqual(raised.exception.code, 2)
                self.assertIn("unrecognized arguments", stderr.getvalue())

        main_mock.assert_not_called()

    def test_scopes_probe_options_to_killarney_help(self):
        top_level_output = StringIO()
        with patch.object(sys, "argv", ["node_state", "--help"]):
            with redirect_stdout(top_level_output):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("{killarney,rcl,vulcan}", top_level_output.getvalue())
        self.assertNotIn("--cpus-per-task", top_level_output.getvalue())

        killarney_output = StringIO()
        with patch.object(sys, "argv", ["node_state", "killarney", "--help"]):
            with redirect_stdout(killarney_output):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--cpus-per-task", killarney_output.getvalue())
        self.assertIn("--mem", killarney_output.getvalue())
        self.assertIn("--sort-by-start", killarney_output.getvalue())

    @patch.object(cli.node_resources, "main")
    def test_rejects_an_unsupported_cluster(self, main_mock):
        stderr = StringIO()
        with patch.object(sys, "argv", ["node_state", "other"]):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'other'", stderr.getvalue())
        main_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
