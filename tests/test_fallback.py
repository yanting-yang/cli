import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from node_state.clusters import common, fallback


class FallbackReporterTests(unittest.TestCase):
    @patch.object(common, "fetch_nodes")
    def test_prints_a_generic_summary_for_all_nodes(self, fetch_mock):
        fetch_mock.return_value = [
            {
                "name": "node1",
                "state": "IDLE",
                "cpu_tot": 10,
                "cpu_efctv": 8,
                "cpu_alloc": 2,
                "mem_tot": 16384,
                "mem_alloc": 4096,
                "cfg_gpus": {"a100": 1},
                "alloc_gpus": {"a100": 1},
            },
            {
                "name": "node2",
                "state": "DOWN",
                "cpu_tot": 10,
                "cpu_efctv": 8,
                "cpu_alloc": 0,
                "mem_tot": 16384,
                "mem_alloc": 0,
                "cfg_gpus": {"a100": 1},
                "alloc_gpus": {},
            },
        ]
        output = StringIO()

        with redirect_stdout(output):
            fallback.main(Namespace(cluster=None))

        text = output.getvalue()
        self.assertIn("8 CPUs / 16 GB / 1x a100 (2/2)", text)
        self.assertIn("States: DOWN=1, IDLE=1", text)
        self.assertRegex(text, r"CPU \(cores\)\s+\| 16\s+\| 2\s+\| 14")


if __name__ == "__main__":
    unittest.main()
