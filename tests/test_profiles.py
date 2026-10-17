import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from node_state import profiles

REPO = Path(__file__).resolve().parents[1]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        # Read the shared template, never your own config.toml.
        patcher = patch.object(profiles, "CONFIG_PATH", REPO / "missing.toml")
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_reads_the_template_through_the_package_link(self):
        self.assertEqual(profiles.EXAMPLE_PATH.name, "config.example.toml")
        self.assertEqual(profiles.EXAMPLE_PATH.resolve(), REPO / "config.example.toml")
        self.assertEqual(profiles.config_path(), profiles.EXAMPLE_PATH)

    def test_prefers_your_config_beside_the_template(self):
        self.assertEqual(
            profiles.EXAMPLE_PATH.resolve().parent / "config.toml",
            REPO / "config.toml",
        )
        mine = Path(self.config('[mail]\nuser = "me@example.com"\n'))
        with patch.object(profiles, "CONFIG_PATH", mine):
            self.assertEqual(profiles.config_path(), mine)
            self.assertEqual(profiles.load_mail()[0], "me@example.com")
            # A cluster your file leaves out gets the generic profile.
            self.assertEqual(profiles.load_profile("tamia").gpu_counts, "pow2")

    def test_loads_the_site_rules_in_the_repository_config(self):
        tamia = profiles.load_profile("tamia")
        rcl = profiles.load_profile("rcl")

        self.assertEqual(tamia.walltimes, (180, 720, 1440))
        self.assertEqual(tamia.gpu_counts, "whole_node")
        self.assertEqual(rcl.walltimes, (60,))

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
"""
        )

        mine = profiles.load_profile("mycluster", path=path)

        self.assertEqual(mine.walltimes, (180, 1440))
        self.assertEqual(mine.gpu_counts, "all")

    def test_reads_the_mail_table(self):
        self.assertEqual(
            profiles.load_mail(self.config('[mail]\nuser = "me@example.com"\n')),
            ("me@example.com", "BEGIN,END,FAIL"),
        )
        self.assertEqual(
            profiles.load_mail(
                self.config('[mail]\nuser = "me@example.com"\ntype = "end, fail"\n')
            ),
            ("me@example.com", "END,FAIL"),
        )
        self.assertIsNone(profiles.load_mail(self.config("[clusters.x]\n")))
        self.assertIsNone(profiles.load_mail("/nonexistent"))

    def test_the_template_holds_no_email_settings(self):
        self.assertIsNone(profiles.load_mail())
        self.assertIsNone(profiles.load_smtp())

    def test_reads_the_smtp_table(self):
        path = self.config(
            '[mail]\nuser = "me@example.com"\n'
            '[smtp]\nserver = "mail.example.org"\nsender = "me@example.org"\n'
            'password = "secret"\n'
        )

        smtp = profiles.load_smtp(path)

        self.assertEqual(
            smtp,
            profiles.Smtp(
                "mail.example.org", 587, "me@example.org", "me@example.com", "secret"
            ),
        )
        self.assertNotIn("secret", repr(smtp))
        path = self.config(
            '[smtp]\nserver = "mail.example.org"\nport = 465\n'
            'sender = "me@example.org"\nrecipient = "you@example.org"\n'
        )
        self.assertEqual(
            profiles.load_smtp(path),
            profiles.Smtp("mail.example.org", 465, "me@example.org", "you@example.org"),
        )
        self.assertIsNone(profiles.load_smtp(self.config("[mail]\n")))

    def test_rejects_a_broken_smtp_table(self):
        good = 'server = "mail.example.org"\nsender = "me@example.org"\n'
        for text in (
            'smtp = "mail.example.org"\n',
            '[smtp]\nsender = "me@example.org"\nrecipient = "you@example.org"\n',
            f"[smtp]\n{good}",
            f'[smtp]\n{good}recipient = "you@example.org"\nport = "587"\n',
            f'[smtp]\n{good}recipient = "you@example.org"\nuser = "me"\n',
            f'[smtp]\n{good}recipient = "you@example.org"\npassword = 1\n',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                profiles.load_smtp(self.config(text))

    def test_rejects_a_broken_mail_table(self):
        for text in (
            'mail = "me@example.com"\n',
            "[mail]\n",
            '[mail]\nuser = "me"\n',
            '[mail]\nuser = "me@example.com"\ntype = "SOMETIMES"\n',
            '[mail]\nuser = "me@example.com"\nto = "x"\n',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                profiles.load_mail(self.config(text))

    def test_rejects_unknown_or_invalid_settings(self):
        for text in (
            '[clusters.x]\nwaltimes = ["1h"]\n',
            '[clusters.x]\nwalltimes = ["soon"]\n',
            '[clusters.x]\ngpu_counts = "some"\n',
            'clusters = "x"\n',
            "[clusters.x\n",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                profiles.load_profile("x", path=self.config(text))


if __name__ == "__main__":
    unittest.main()
