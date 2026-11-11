"""STEER-24: the no-cost setup check (``/line check``, ``hermes hermes-line check``), with fakes for every probe.

No network: Twilio, the tunnel, the gateway and the port are fakes. The checks below also prove the output
never carries a credential, a SID or a whole phone number, and that every Twilio request is a GET.
"""
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

import harness  # noqa: F401  (repo root on sys.path)

from plugin.line.engine import hermes_glue, setup_check as sc
from plugin.line.engine.config import EngineConfig
from test_otp_and_plugin import FakeCtx

ACCOUNT = "AC" + "a" * 32
KEY = "SK" + "b" * 32
SECRET = "s" * 32
LINE = "+17755550100"       # sample Twilio number
OWNER = "+17755550199"      # sample owner phone
CALLER = "+17755550142"     # sample verified caller ID
BASE = "https://line.example.com"
SECRETS = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT, "HERMES_LINE_TWILIO_API_KEY_SID": KEY,
           "HERMES_LINE_TWILIO_API_KEY_SECRET": SECRET}
SETTINGS = {"enabled": True, "public_base_url": BASE, "from_number": LINE, "line_number": LINE,
            "owner_number": OWNER, "call_profile": "hermes-line-calls",
            "policy": {"allowed_destinations": [OWNER]}}
VOICE_ROW = {"phone_number": LINE, "capabilities": {"voice": True, "sms": True}}


class FakeNet:
    """Twilio list resources and the tunnel's /health, by URL. Records every request."""

    def __init__(self):
        self.requests = []
        self.numbers = {LINE: VOICE_ROW}
        self.caller_ids = set()
        self.auth_status = 200
        self.health = (200, {"ok": True})
        self.down = set()  # "twilio", "tunnel"

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers)))
        if url.startswith(sc.TWILIO_API):
            if "twilio" in self.down:
                raise ConnectionRefusedError("down")
            if headers.get("Authorization") != "Basic " + __import__("base64").b64encode(
                    f"{KEY}:{SECRET}".encode()).decode():
                return 401, b'{"code": 20003}'
            if self.auth_status != 200:
                return self.auth_status, b"{}"
            from urllib.parse import parse_qs, urlsplit
            parts = urlsplit(url)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            if parts.path.endswith("/IncomingPhoneNumbers.json"):
                rows = [r for n, r in self.numbers.items() if query.get("PhoneNumber", n) == n]
                return 200, json.dumps({"incoming_phone_numbers": rows}).encode()
            if parts.path.endswith("/OutgoingCallerIds.json"):
                rows = [{"phone_number": n} for n in self.caller_ids if query.get("PhoneNumber") == n]
                return 200, json.dumps({"outgoing_caller_ids": rows}).encode()
            return 404, b"{}"
        if url == f"{BASE}/health":
            if "tunnel" in self.down:
                raise TimeoutError("no answer")
            status, body = self.health
            return status, (json.dumps(body).encode() if isinstance(body, dict) else body)
        raise AssertionError(f"unexpected request {url}")


class Rig:
    def __init__(self):
        self.settings = json.loads(json.dumps(SETTINGS))
        self.secrets = dict(SECRETS)
        self.net = FakeNet()
        self.version = "0.23.0"
        self.gateway = True
        self.listening = True
        self.profile = []

    def config(self):
        return EngineConfig.load(self.settings)

    def secret(self, name):
        value = self.secrets.get(name)
        if value == "RAISE":
            raise RuntimeError(f"{name} could not be read")
        return value

    def probes(self):
        from plugin.line.engine import deps
        return sc.Probes(
            hermes_version=lambda: self.version, gateway_running=lambda: self.gateway, get_secret=self.secret,
            http_get=self.net, port_open=lambda host, port, timeout: self.listening,
            profile_problems=lambda name: list(self.profile) if name else [
                "call_profile not set (README, setup step 4)"],
            destination_allowed=lambda e164, policy: bool(
                deps.check_destination(e164, deps.Settings.load(policy)).allowed))

    def run(self):
        items = sc.run(self.config, self.probes())
        return {i.name: i for i in items}, sc.report(items), sc.exit_code(items)


class SetupCheckTests(unittest.TestCase):
    def assertClean(self, text):
        for secret in (ACCOUNT, KEY, SECRET, LINE, OWNER, CALLER, LINE[2:], OWNER[2:], CALLER[2:]):
            self.assertNotIn(secret, text)
        self.assertNotIn("+1 775", text)

    def test_all_pass_exits_zero_and_prints_one_line_per_item(self):
        rig = Rig()
        items, text, code = rig.run()
        self.assertEqual(code, 0, text)
        self.assertEqual(list(items), ["Hermes version", "Hermes gateway", "Settings", "Twilio credentials",
                                       "Twilio accepts the credentials", "Line number on the account", "Caller ID",
                                       "Owner phone", "Call profile", "Tunnel", "Local port"])
        lines = text.splitlines()
        self.assertEqual(len(lines), len(items) + 2)
        self.assertTrue(all(line.startswith("OK ") for line in lines[1:-1]))
        self.assertIn("All checks passed. No call was made.", text)
        self.assertIn("number ending in 00", text)
        self.assertClean(text)

    def test_every_request_is_read_only(self):
        rig = Rig()
        rig.settings["from_number"] = CALLER
        rig.net.caller_ids.add(CALLER)
        rig.run()
        urls = [u for u, _ in rig.net.requests]
        self.assertTrue(urls)
        for url in urls:
            if url.startswith(sc.TWILIO_API):
                path = url.split("?")[0]
                self.assertTrue(path.startswith(f"{sc.TWILIO_API}/Accounts/{ACCOUNT}/"), path)
                self.assertTrue(path.endswith(("/IncomingPhoneNumbers.json", "/OutgoingCallerIds.json")), path)
            else:
                self.assertEqual(url, f"{BASE}/health")
        self.assertFalse(any("Calls" in u or "Messages" in u for u in urls))

    def test_real_http_get_sends_a_get_without_a_body(self):
        seen = []

        class Resp:
            status = 200

            def read(self, n=-1):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen.append((req.get_method(), req.data, timeout))
            return Resp()
        with mock.patch.object(sc.urllib.request, "urlopen", opener):
            self.assertEqual(sc.http_get(f"{BASE}/health", {}, 3.0), (200, b"{}"))
        self.assertEqual(seen, [("GET", None, 3.0)])

        def http_error(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"code": 20003}'))
        with mock.patch.object(sc.urllib.request, "urlopen", http_error):
            self.assertEqual(sc.http_get(f"{BASE}/health", {}, 3.0)[0], 401)

        def unreachable(req, timeout):
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        with mock.patch.object(sc.urllib.request, "urlopen", unreachable):
            with self.assertRaises(OSError):
                sc.http_get(f"{BASE}/health", {}, 3.0)

    def test_hermes_version(self):
        for version, ok in (("0.22.5", True), ("0.23.0", True), ("0.22.4", False), ("0.9.9", False),
                            ("", False), ("unknown", False)):
            with self.subTest(version=version):
                rig = Rig()
                rig.version = version
                items, _, code = rig.run()
                self.assertEqual(items["Hermes version"].ok, ok)
                self.assertEqual(code == 0, ok)
                if not ok:
                    self.assertIn("0.22.5", items["Hermes version"].text)

    def test_real_version_probe_reads_the_public_cli(self):
        import subprocess
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "Hermes Agent v0.23.0 (2026.9.24) \u00b7 stable\n", "")
        with mock.patch("subprocess.run", fake_run), mock.patch("shutil.which", lambda name: None), \
                mock.patch("pathlib.Path.is_file", lambda self: True), \
                mock.patch.dict("os.environ", {"XAI_API_KEY": "x" * 20, "PATH": "/usr/bin"}):
            self.assertEqual(sc.hermes_version(), "0.23.0")
        argv, kw = calls[0]
        self.assertEqual(argv[1:], ["--version"])
        self.assertTrue(argv[0].endswith("hermes"))
        self.assertNotIn("XAI_API_KEY", kw["env"])
        self.assertEqual(kw["env"]["PATH"], "/usr/bin")
        with mock.patch("subprocess.run", side_effect=OSError("no")), mock.patch("shutil.which", lambda n: None):
            self.assertEqual(sc.hermes_version(), "")

    def test_gateway_not_running(self):
        rig = Rig()
        rig.gateway = False
        items, _, code = rig.run()
        self.assertFalse(items["Hermes gateway"].ok)
        self.assertIn("hermes gateway start", items["Hermes gateway"].text)
        self.assertEqual(code, 1)

    def test_settings(self):
        rig = Rig()
        rig.settings["enabled"] = False
        items, _, code = rig.run()
        self.assertFalse(items["Settings"].ok)
        self.assertIn("enabled: true", items["Settings"].text)
        self.assertEqual(code, 1)
        rig = Rig()
        del rig.settings["line_number"]
        items, _, _ = rig.run()
        self.assertIn("line_number", items["Settings"].text)
        rig = Rig()
        rig.settings = {}
        items, _, _ = rig.run()
        self.assertIn("set `enabled: true`, public_base_url, from_number, line_number, owner_number",
                      items["Settings"].text)
        rig = Rig()
        rig.settings["from_number"] = "7755550100"
        items, text, code = rig.run()
        self.assertFalse(items["Settings"].ok)
        self.assertIn("E.164", items["Settings"].text)
        self.assertFalse(items["Owner phone"].ok)
        self.assertEqual(code, 1)
        self.assertEqual(len(items), 11)

    def test_credentials_missing_unreadable_or_malformed_are_not_printed(self):
        cases = (("HERMES_LINE_TWILIO_API_KEY_SECRET", None, "set HERMES_LINE_TWILIO_API_KEY_SECRET"),
                 ("HERMES_LINE_TWILIO_ACCOUNT_SID", "RAISE", "could not be read"),
                 ("HERMES_LINE_TWILIO_API_KEY_SID", "AK" + "b" * 32, "wrong shape"))
        for name, value, expect in cases:
            with self.subTest(name=name):
                rig = Rig()
                if value is None:
                    rig.secrets.pop(name)
                else:
                    rig.secrets[name] = value
                items, text, code = rig.run()
                self.assertFalse(items["Twilio credentials"].ok)
                self.assertIn(expect, items["Twilio credentials"].text)
                self.assertIn("skipped", items["Twilio accepts the credentials"].text)
                self.assertFalse(items["Line number on the account"].ok)
                self.assertFalse(any(u.startswith(sc.TWILIO_API) for u, _ in rig.net.requests))
                self.assertEqual(code, 1)
                self.assertClean(text)
                self.assertNotIn("b" * 32, text)

    def test_twilio_refuses_or_is_unreachable(self):
        rig = Rig()
        rig.secrets["HERMES_LINE_TWILIO_API_KEY_SECRET"] = "wrong"
        items, text, code = rig.run()
        self.assertFalse(items["Twilio accepts the credentials"].ok)
        self.assertIn("HTTP 401", items["Twilio accepts the credentials"].text)
        self.assertIn("skipped", items["Line number on the account"].text)
        self.assertNotIn("wrong", text)
        self.assertEqual(code, 1)
        rig = Rig()
        rig.net.down.add("twilio")
        items, _, code = rig.run()
        self.assertIn("could not be reached", items["Twilio accepts the credentials"].text)
        self.assertEqual(code, 1)
        rig = Rig()
        rig.net.auth_status = 404
        items, _, _ = rig.run()
        self.assertIn("Account SID", items["Twilio accepts the credentials"].text)

    def test_line_number_not_on_account_or_without_voice(self):
        rig = Rig()
        rig.net.numbers = {}
        items, text, code = rig.run()
        self.assertFalse(items["Line number on the account"].ok)
        self.assertIn("not a number on this Twilio account", items["Line number on the account"].text)
        self.assertEqual(code, 1)
        self.assertClean(text)
        rig = Rig()
        rig.net.numbers = {LINE: {"phone_number": LINE, "capabilities": {"voice": False, "sms": True}}}
        items, _, _ = rig.run()
        self.assertIn("no voice capability", items["Line number on the account"].text)
        self.assertFalse(items["Caller ID"].ok)

    def test_caller_id_verified_or_unknown(self):
        rig = Rig()
        rig.settings["from_number"] = CALLER
        rig.net.caller_ids.add(CALLER)
        items, text, code = rig.run()
        self.assertTrue(items["Caller ID"].ok, items["Caller ID"].text)
        self.assertIn("verified caller ID", items["Caller ID"].text)
        self.assertEqual(code, 0)
        self.assertClean(text)
        rig = Rig()
        rig.settings["from_number"] = CALLER
        items, _, code = rig.run()
        self.assertFalse(items["Caller ID"].ok)
        self.assertIn("Verified Caller IDs", items["Caller ID"].text)
        self.assertEqual(code, 1)

    def test_owner_phone_missing_or_not_allowed(self):
        rig = Rig()
        del rig.settings["owner_number"]
        items, _, code = rig.run()
        self.assertFalse(items["Owner phone"].ok)
        self.assertIn("owner_number", items["Owner phone"].text)
        self.assertEqual(code, 1)
        rig = Rig()
        rig.settings["policy"] = {"allowed_destinations": []}
        items, text, code = rig.run()
        self.assertFalse(items["Owner phone"].ok)
        self.assertIn("policy.allowed_destinations", items["Owner phone"].text)
        self.assertEqual(code, 1)
        self.assertClean(text)

    def test_call_profile_refused(self):
        rig = Rig()
        rig.profile = ["memory is on (memory.memory_enabled)"]
        items, _, code = rig.run()
        self.assertFalse(items["Call profile"].ok)
        self.assertIn("memory is on", items["Call profile"].text)
        self.assertEqual(code, 1)
        rig = Rig()
        del rig.settings["call_profile"]
        items, _, _ = rig.run()
        self.assertIn("call_profile not set", items["Call profile"].text)
        self.assertEqual(items["Call profile"].text.count("setup step"), 1)

    def test_tunnel(self):
        cases = ((None, "set public_base_url"), ("down", "no HTTPS answer"), ((502, b"bad gateway"), "HTTP 502"),
                 ((200, b"<html>"), "not from Hermes Line"), ((200, {"ok": False}), "not ready"))
        for case, expect in cases:
            with self.subTest(case=case):
                rig = Rig()
                if case is None:
                    del rig.settings["public_base_url"]
                elif case == "down":
                    rig.net.down.add("tunnel")
                else:
                    rig.net.health = case
                items, _, code = rig.run()
                self.assertFalse(items["Tunnel"].ok)
                self.assertIn(expect, items["Tunnel"].text)
                self.assertEqual(code, 1)

    def test_local_port(self):
        rig = Rig()
        rig.listening = False
        items, _, code = rig.run()
        self.assertFalse(items["Local port"].ok)
        self.assertIn("127.0.0.1:8788", items["Local port"].text)
        self.assertEqual(code, 1)
        seen = []
        rig = Rig()
        rig.settings["listen_host"] = "0.0.0.0"
        probes = rig.probes()
        probes.port_open = lambda host, port, timeout: seen.append((host, port)) or True
        sc.run(rig.config, probes)
        self.assertEqual(seen, [("127.0.0.1", 8788)])

    def test_real_port_probe(self):
        import socket
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.assertTrue(sc.port_open("127.0.0.1", port, 1.0))
        self.assertFalse(sc.port_open("127.0.0.1", port, 1.0))


class SetupCheckCommandTests(unittest.TestCase):
    def _ctx(self):
        class Ctx(FakeCtx):
            def __init__(self, config=None):
                super().__init__(config)
                self.cli = {}

            def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
                self.cli[name] = (setup_fn, handler_fn)
        rig = Rig()
        ctx = Ctx(dict(rig.settings, enabled=False))  # enabled stays off here: register() starts nothing
        return ctx, rig

    def _patched(self, rig):
        return mock.patch.object(hermes_glue, "setup_probes", lambda host: rig.probes())

    def test_slash_line_check(self):
        import plugin
        ctx, rig = self._ctx()
        plugin.register(ctx)
        with self._patched(rig):
            text = ctx.commands["line"]("check")
        self.assertIn("Hermes Line setup check", text)
        self.assertIn("FIX  Settings: set `enabled: true`", text)  # the ctx config has enabled: false
        self.assertIn("No call was made.", text)
        host = ctx.unloads[0].__self__
        self.assertIsNone(host.service)  # the check builds no service and opens no store

    def test_cli_line_check_exit_status(self):
        import argparse
        import plugin
        ctx, rig = self._ctx()
        plugin.register(ctx)
        setup_fn, handler = ctx.cli["hermes-line"]
        parser = argparse.ArgumentParser()
        setup_fn(parser)
        ctx.config["enabled"] = True
        for listening, code in ((True, 0), (False, 1)):
            with self.subTest(listening=listening):
                rig.listening = listening
                out = io.StringIO()
                with self._patched(rig), redirect_stdout(out), self.assertRaises(SystemExit) as stop:
                    handler(parser.parse_args(["check"]))
                self.assertEqual(stop.exception.code, code, out.getvalue())
                self.assertIn("Local port", out.getvalue())
        host = ctx.unloads[0].__self__
        self.assertTrue(host.stopped)  # the CLI process never takes the line
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stop:
            handler(parser.parse_args([]))
        self.assertEqual(stop.exception.code, 2)

    def test_older_hermes_without_cli_commands(self):
        import plugin
        ctx = FakeCtx({})
        plugin.register(ctx)  # FakeCtx has no register_cli_command: /line check still works
        self.assertIn("line", ctx.commands)


if __name__ == "__main__":
    unittest.main()
