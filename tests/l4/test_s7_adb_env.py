"""STEER-7 (Nous review of d92bc1fb, recommended): adb runs with a minimal environment, not the gateway's own."""
import os
import unittest
from unittest import mock

from line.sms.sources import adb

SYNTHETIC = "SYNTHETIC-not-a-secret"


class AdbEnvTests(unittest.TestCase):
    def test_S7_adb_runs_with_only_path_home_lang_and_android_adb_vars(self):
        environ = {"PATH": "/usr/bin:/bin", "HOME": "/home/sample", "LANG": "en_US.UTF-8",
                   "ANDROID_SERIAL": "sample-serial", "ANDROID_ADB_SERVER_PORT": "5037", "ADB_VENDOR_KEYS": "/k",
                   "OPENAI_API_KEY": SYNTHETIC, "XAI_API_KEY": SYNTHETIC, "HERMES_LINE_TWILIO_API_KEY_SECRET": SYNTHETIC,
                   "HERMES_HOME": "/home/sample/.hermes", "PYTHONPATH": "/x", "SSH_AUTH_SOCK": "/tmp/agent"}
        seen = {}

        def run(argv, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0, stdout="Android Debug Bridge version 1.0.41\n", stderr="")

        with mock.patch.dict(os.environ, environ, clear=True), mock.patch.object(adb.subprocess, "run", run):
            out = adb.default_runner(["adb", "version"], 5.0)
        self.assertEqual(out.returncode, 0)
        self.assertIn("env", seen)  # never the inherited environment
        self.assertEqual(seen["env"], {"PATH": "/usr/bin:/bin", "HOME": "/home/sample", "LANG": "en_US.UTF-8",
                                       "ANDROID_SERIAL": "sample-serial", "ANDROID_ADB_SERVER_PORT": "5037",
                                       "ADB_VENDOR_KEYS": "/k"})
        self.assertNotIn(SYNTHETIC, seen["env"].values())

    def test_S7_every_adb_call_of_a_fetch_gets_the_minimal_env(self):
        envs = []

        def run(argv, **kwargs):
            envs.append(kwargs.get("env"))
            return mock.Mock(returncode=0, stdout="No result found.\n", stderr="")

        with mock.patch.dict(os.environ, {"PATH": "/bin", "XAI_API_KEY": SYNTHETIC}, clear=True), \
                mock.patch.object(adb.subprocess, "run", run):
            adb.AdbSource(serial="sample-serial").fetch(since_ms=0)
        self.assertTrue(envs)
        self.assertEqual(envs, [{"PATH": "/bin"}] * len(envs))


if __name__ == "__main__":
    unittest.main()
