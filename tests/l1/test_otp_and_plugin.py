"""Sealed OTP-to-call delivery, OTP approval through line.sms, and plugin registration."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import harness
from harness import Line, wait_for
from plugin.line.engine import sealed
from plugin.line.sms import FixtureSource, OtpService, SecretText, SmsMessage
from plugin.line.store.released_otps import ReleasedOtps

CODE = "739204"


def _reach_human(line):
    req, created = line.dial()
    relay = line.connect(req, created)
    relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
    relay.wait_text("live person?")
    relay.prompt("Yes, I am a real person")
    line.wait_state(req["call_id"], "agent_talking")
    return req, created, relay


def _allow_call(line, req, counterpart=harness.BUSINESS):
    line.svc.grants.put("otp_release", req["task_id"], "otp", {"x": 1}, 600, 600, renewable=False,
                        meta={"kind": "call", "call_id": req["call_id"], "counterpart": counterpart,
                              "approval_generation": 1})  # R1 delta N1: a send without one fails closed


class SealedCallTests(unittest.TestCase):
    def setUp(self):
        self.line = Line()

    def tearDown(self):
        self.line.close()

    def _no_code_anywhere(self, req):
        from plugin.line.server.desktop_api import snapshot
        blob = json.dumps([self.line.store.transcript(req["call_id"]), self.line.store.events(req["call_id"]),
                           snapshot(self.line.store), self.line.drivers[0].notes, self.line.drivers[0].turns,
                           self.line.drivers[0].history])
        self.assertNotIn(CODE, blob)
        self.assertNotIn(" ".join(CODE), blob)

    def test_dtmf_delivery_records_only_the_marker(self):
        req, created, relay = _reach_human(self.line)
        _allow_call(self.line, req)
        self.assertTrue(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf",
                                                   approval_generation=1))
        wait_for(lambda: relay.of_type("sendDigits"))
        self.assertEqual(relay.of_type("sendDigits")[-1]["digits"], CODE)
        texts = [t["text"] for t in self.line.store.transcript(req["call_id"])]
        self.assertIn("[verification code sent]", texts)
        wait_for(lambda: "[verification code sent]" in self.line.drivers[0].notes)
        self._no_code_anywhere(req)

    def test_speak_delivery_bypasses_the_model(self):
        req, created, relay = _reach_human(self.line)
        _allow_call(self.line, req)
        turns = len(self.line.drivers[0].turns)
        self.assertTrue(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "speak",
                                                   approval_generation=1))
        relay.wait_text(" ".join(CODE))
        self.assertEqual(len(self.line.drivers[0].turns), turns)
        self._no_code_anywhere(req)

    def test_refusals(self):
        req, created, relay = _reach_human(self.line)
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf"))  # not approved
        _allow_call(self.line, req, counterpart="+17755550177")
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf"))  # other party
        _allow_call(self.line, req)
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf"))  # no generation
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf",
                                                    approval_generation=2))                     # other generation
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], CODE, "dtmf"))              # plain str
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "sms"))   # bad mode
        self.assertFalse(sealed.send_secret_to_call("call_other", SecretText(CODE), "dtmf"))
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText("AB12CD"), "dtmf"))
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText("1; DROP"), "speak"))
        self.assertEqual(relay.of_type("sendDigits"), [])
        self.line.svc.stop(timeout=5)
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(CODE), "dtmf"))  # unbound


class OtpApprovalTests(unittest.TestCase):
    def test_owner_approval_binds_destination_and_release_persists(self):
        tmp = Path(tempfile.mkdtemp(prefix="hl-otp-"))
        line = Line(store_dir=tmp, start=False)
        now_ms = int(time.time() * 1000)
        msg = SmsMessage(id="m1", address="EXAMPLEBANK", date_ms=now_ms + 5_000,
                         body=f"Your EXAMPLEBANK verification code is {CODE}.", source="fixture")
        otp = OtpService(FixtureSource([msg]), released=ReleasedOtps(line.store))
        line.svc.otp = otp
        line.svc.start(port=0)
        try:
            req, created, relay = _reach_human(line)
            for bad in ("http://phish.example", "ftp://x.example", "https://user:pw@bank.example"):
                with self.assertRaises(Exception):
                    line.svc.otp_destination(req["task_id"], bad)
            self.assertEqual(line.svc.otp_destination(req["task_id"], "https://Bank.Example:443/").origin,
                             "https://bank.example")
            prompt, payload = line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
            self.assertIn("EXAMPLEBANK", prompt)
            self.assertIn(req["call_id"], prompt)
            self.assertIn(harness.BUSINESS, prompt)  # the owner sees who will receive the code
            tool = line.svc.submit("allow_code", source="tool", task_id=req["task_id"], payload=payload)
            self.assertFalse(tool["result"]["ok"])  # the model cannot approve a code
            self.assertIsNone(line.svc.otp_token(req["task_id"]))
            out = line.svc.submit("allow_code", source="slash", task_id=req["task_id"], payload=payload)
            self.assertTrue(out["result"]["ok"], out)
            self.assertEqual(out["result"]["prompt"], prompt)  # approved exactly what was shown
            self.assertTrue(line.svc.otp_call_allowed(req["call_id"]))
            from plugin.line.engine import sealed as sealed_mod
            from plugin.line.sms import CallSink, OtpDelivery
            delivery = OtpDelivery(otp, call_sink=CallSink(send=sealed_mod.send_secret_to_call),
                                   counterpart_of=line.svc.live_counterpart)
            token = line.svc.otp_token(req["task_id"])
            res = delivery.to_call(req["task_id"], "call_other", "dtmf", token, line.svc.consent)
            self.assertEqual(res.status, "wrong_destination")
            res = delivery.to_call(req["task_id"], req["call_id"], "dtmf", token, line.svc.consent)
            self.assertEqual(res.status, "sent", res)
            wait_for(lambda: relay.of_type("sendDigits"))
            self.assertEqual(relay.of_type("sendDigits")[-1]["digits"], CODE)
            again = otp.attempt(req["task_id"], token, line.svc.consent)
            self.assertIsNone(again.secret)  # single use
            self.assertIn("[verification code sent]", [t["text"] for t in line.store.transcript(req["call_id"])])
            from plugin.line.sms.matching import message_key
            self.assertTrue(ReleasedOtps(line.store).contains(message_key(msg)))
            self.assertNotIn(CODE, json.dumps(line.store.events()))
        finally:
            line.close()
        reopened = harness.Store(tmp / "line.db")
        try:
            from plugin.line.sms.matching import message_key
            self.assertTrue(ReleasedOtps(reopened).contains(message_key(msg)))  # survives restart
        finally:
            reopened.close()


class FakeCtx:
    def __init__(self, config=None):
        self.config = config or {}
        self.tools, self.hooks, self.commands, self.skills, self.unloads = {}, [], {}, [], []
        self.profile_name = "default"

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, cb):
        self.hooks.append(name)

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_skill(self, name, path, description=""):
        self.skills.append((name, Path(path)))

    def on_unload(self, cb):
        self.unloads.append(cb)

    def dispatch_tool(self, *a, **kw):
        return "{}"

    def post_message(self, text):
        return None

    def ask_choice(self, question, choices):
        return None

    def session_surface(self):
        return "cli"


class PluginRegistrationTests(unittest.TestCase):
    BROWSER_ONLY = set()  # round 8 (catalog G3): no browser code fill in this build

    def test_G3_browser_fill_is_not_in_this_build(self):
        import yaml
        import plugin
        manifest = yaml.safe_load((Path(harness.__file__).resolve().parents[2] / "plugin" / "plugin.yaml").read_text())
        self.assertNotIn("line_otp_fill", manifest["provides_tools"])
        self.assertNotIn("otp_browser_fill", manifest["config_schema"])
        self.assertIn("line_otp_to_call", manifest["provides_tools"])
        ctx = FakeCtx()
        plugin.register(ctx)
        self.assertNotIn("line_otp_fill", ctx.tools)
        self.assertIn("line_otp_to_call", ctx.tools)
        self.assertEqual(sorted(ctx.tools), sorted(manifest["provides_tools"]))
        host = ctx.unloads[0].__self__
        self.assertIsNone(host.otp_delivery.sink)            # nothing to type into
        self.assertFalse(host.otp_delivery.otp_browser_fill)
        harness.EngineConfig.load({"otp_browser_fill": False})  # an old false is accepted
        with self.assertRaises(ValueError):
            harness.EngineConfig.load({"otp_browser_fill": True})

    def test_registrations_match_manifest_and_touch_no_disk(self):
        import yaml  # available in the Hermes environment
        manifest = yaml.safe_load((Path(harness.__file__).resolve().parents[2] / "plugin" / "plugin.yaml").read_text())
        import plugin
        ctx = FakeCtx()
        with tempfile.TemporaryDirectory() as home:
            import os
            old = os.environ.get("HERMES_HOME")
            os.environ["HERMES_HOME"] = home
            try:
                plugin.register(ctx)
                self.assertEqual(list(Path(home).iterdir()), [])  # register() writes nothing
            finally:
                if old is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old
        self.assertEqual(sorted(ctx.tools), sorted(set(manifest["provides_tools"]) - self.BROWSER_ONLY))
        self.assertEqual(sorted(ctx.hooks), sorted(manifest["provides_hooks"]))
        self.assertNotIn("line_otp_read", ctx.tools)        # cleartext OTP stays unregistered
        self.assertNotIn("hermes_line_read_code", ctx.tools)
        self.assertIn("line", ctx.commands)
        name, path = ctx.skills[0]
        self.assertEqual(name, "hermes-line")
        self.assertTrue(path.is_file())
        for tool_name, (toolset, schema, _) in ctx.tools.items():
            self.assertEqual(schema["name"], tool_name)
            self.assertEqual(toolset, "hermes_line")
        self.assertEqual(len(ctx.unloads), 1)
        for key in manifest["config_schema"]:
            self.assertIn(key, harness.EngineConfig.__dataclass_fields__)

    def test_in_call_tools_refuse_outside_a_call(self):
        import plugin
        ctx = FakeCtx({"sms_source": "none"})
        plugin.register(ctx)
        from plugin.line.engine import hermes_glue
        host = ctx.unloads[0].__self__
        line = Line(start=False)
        try:
            host.service = line.svc
            handler = ctx.tools["hermes_line_call_press_digits"][2]
            out = json.loads(handler({"digits": "1"}, session_id="not-a-call"))
            self.assertFalse(out["ok"])
            handler = ctx.tools["hermes_line_request_call"][2]
            out = json.loads(handler({"destination": "911", "title": "x", "goal": "y", "mode": "agent"}))
            self.assertEqual(out["blocked_by"], "destination")
            ctx.post_message = lambda text: type("H", (), {"ok": True})()  # the approval message is posted (S7 RC-4)
            out = json.loads(handler({"destination": harness.BUSINESS, "title": "Book", "goal": "y",
                                      "mode": "agent"}, session_id="s1"))
            self.assertTrue(out["ok"])
            self.assertIn("cannot approve", out["note"])
            self.assertEqual(line.adapter.total_calls, 0)
            reply = ctx.commands["line"](f"allow-code {out['task_id']} EXAMPLEBANK https://bank.example")
            self.assertIn("codes can only go into the task's live call", reply)  # round 8: no web-page target
            self.assertIn(out["task_id"], ctx.commands["line"]("status"))
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
