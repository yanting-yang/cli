import sys
import unittest
from contextlib import redirect_stderr
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
