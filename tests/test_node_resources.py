import unittest
from unittest.mock import Mock, patch

from node_state import node_resources
from node_state.clusters import killarney


class DispatchTests(unittest.TestCase):
    def test_registers_killarney(self):
        self.assertIs(node_resources.CLUSTER_RUNNERS["killarney"], killarney.main)

    def test_dispatches_to_named_cluster_with_options_case_insensitively(self):
        runner = Mock()

        with patch.dict(node_resources.CLUSTER_RUNNERS, {"vulcan": runner}, clear=True):
            node_resources.main("VULCAN", probe_cpus=12)

        runner.assert_called_once_with(probe_cpus=12)

    def test_uses_fallback_when_cluster_is_omitted(self):
        fallback_runner = Mock()

        with patch.object(node_resources.fallback, "main", fallback_runner):
            node_resources.main()

        fallback_runner.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
