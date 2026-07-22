import unittest
from unittest.mock import Mock, patch

from node_state import node_resources


class ParseClusterNameTests(unittest.TestCase):
    def test_parses_cluster_name(self):
        output = "Configuration data\nClusterName             = vulcan\nSlurmUser = slurm\n"

        self.assertEqual(node_resources.parse_cluster_name(output), "vulcan")

    def test_returns_none_when_cluster_name_is_missing(self):
        self.assertIsNone(node_resources.parse_cluster_name("SlurmUser = slurm\n"))


class DispatchTests(unittest.TestCase):
    @patch.object(node_resources, "detect_cluster_name", return_value="VULCAN")
    def test_dispatches_to_vulcan_case_insensitively(self, _detect_cluster_name):
        runner = Mock()

        with patch.dict(node_resources.CLUSTER_RUNNERS, {"vulcan": runner}, clear=True):
            node_resources.main(exclude_states=["DOWN"])

        runner.assert_called_once_with(exclude_states=["DOWN"])

    @patch.object(node_resources, "detect_cluster_name", return_value="other")
    def test_reports_unsupported_cluster(self, _detect_cluster_name):
        with patch("builtins.print") as print_mock:
            node_resources.main()

        print_mock.assert_called_once_with("Error: unsupported Slurm cluster 'other'.")


if __name__ == "__main__":
    unittest.main()
