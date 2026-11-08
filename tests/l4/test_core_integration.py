"""line.sms against the real Hermes core (read-only), via a subprocess; skipped when Hermes is absent.

Verifies the facts the delivery design depends on, with Hermes's own code rather than a model of it:
  * `ctx.dispatch_tool` is registry-level: it fires no `post_tool_call` (so no hook sees the code);
  * `transform_tool_result` registered by `hooks.register` really replaces the string that
    `model_tools.handle_function_call` returns (and so what core appends and stores);
  * the real `SessionDB` stores the sealed tools' results (status JSON) and the scrubbed snapshot without the code.
Point `HERMES_AGENT_DIR` elsewhere to test another checkout.
"""
import json
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin")
HERMES = os.environ.get("HERMES_AGENT_DIR", os.path.expanduser("~/.hermes/hermes-agent"))
PY = os.path.join(HERMES, "venv", "bin", "python")


@unittest.skipUnless(os.path.exists(PY) and os.path.isdir(os.path.join(HERMES, "agent")), "Hermes checkout not found")
class RealCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, HERMES_AGENT_DIR=HERMES)
        p = subprocess.run([PY, os.path.join(HERE, "core_probe.py"), PLUGIN], capture_output=True, text=True,
                           env=env, cwd="/tmp", timeout=240)
        if p.returncode != 0:
            raise AssertionError("core probe failed (exit %d):\n%s" % (p.returncode, p.stderr[-2000:]))
        cls.out = json.loads(p.stdout.strip().splitlines()[-1])

    def test_fill(self):
        o = self.out["fill"]
        self.assertEqual(["line_otp_fill", "line_otp_to_call"], o["tools_registered"], "no cleartext tool is registered")
        self.assertNotIn("739204", o["first_result"])
        self.assertEqual({"@e7": "739204"}, o["pages"], "the code reached the page")
        self.assertEqual(["line_otp_fill", "l4_snapshot"], o["post_tool_call_tools"],
                         "ctx.dispatch_tool (browser_type, browser_console) must fire no tool hooks")
        self.assertEqual("{\"page\": \"textbox value [CODE]\"}", o["snapshot_result"])
        self.assertEqual([False, False], o["db_rows_with_code"])

    def test_call(self):
        o = self.out["call"]
        self.assertEqual([["call-1", "dtmf", "739204"]], o["sent"], "the code reached the call sink")
        self.assertEqual({}, o["pages"])
        self.assertEqual(["line_otp_to_call", "l4_snapshot"], o["post_tool_call_tools"])
        self.assertNotIn("739204", o["first_result"])
        self.assertEqual("{\"page\": \"textbox value [CODE]\"}", o["snapshot_result"])
        self.assertEqual([False, False], o["db_rows_with_code"])


if __name__ == "__main__":
    unittest.main()
