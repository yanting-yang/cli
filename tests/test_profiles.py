import os
import tempfile
import unittest
from pathlib import Path

from node_state import profiles

REPO_CONFIG = Path(__file__).resolve().parents[1] / "clusters.toml"


class ProfileTests(unittest.TestCase):
    def config(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_reads_the_repository_config_through_the_package_link(self):
        self.assertEqual(profiles.CONFIG_PATH.name, "clusters.toml")
        self.assertEqual(profiles.CONFIG_PATH.resolve(), REPO_CONFIG)

    def test_loads_the_site_rules_in_the_repository_config(self):
        tamia = profiles.load_profile("tamia")
        rcl = profiles.load_profile("rcl")

        self.assertEqual(tamia.walltimes, (180, 720, 1440))
        self.assertEqual(tamia.gpu_counts, "whole_node")
        self.assertEqual(tamia.srun_program, ("bash",))
        self.assertEqual(rcl.walltimes, (60,))
        self.assertEqual(len(rcl.message_noise), 1)

    def test_gives_an_unlisted_cluster_the_generic_settings(self):
        self.assertEqual(
            profiles.load_profile("newcluster"), profiles.Profile(name="newcluster")
        )
        self.assertEqual(
            profiles.load_profile("tamia", path="/nonexistent"),
            profiles.Profile(name="tamia"),
        )

    def test_reads_every_setting(self):
        path = self.config(
            """
[clusters.mycluster]
walltimes = ["1-00:00:00", "0-03:00:00", "3:00:00"]
gpu_counts = "all"
srun_program = ["bash"]
message_noise = ["Banner: .*?\\\\."]
"""
        )

        mine = profiles.load_profile("mycluster", path=path)

        self.assertEqual(mine.walltimes, (180, 1440))
        self.assertEqual(mine.gpu_counts, "all")
        self.assertEqual(mine.srun_program, ("bash",))
        self.assertEqual(mine.message_noise, ("Banner: .*?\\.",))

    def test_rejects_unknown_or_invalid_settings(self):
        for text in (
            '[clusters.x]\nwaltimes = ["1h"]\n',
            '[clusters.x]\nwalltimes = ["soon"]\n',
            '[clusters.x]\ngpu_counts = "some"\n',
            '[clusters.x]\nmessage_noise = ["("]\n',
            'clusters = "x"\n',
            "[clusters.x\n",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                profiles.load_profile("x", path=self.config(text))


if __name__ == "__main__":
    unittest.main()
