"""STEER-24: the no-cost setup check (``/line check``, ``hermes hermes-line check``), with fakes for every probe.

No network: Twilio, the tunnel, the gateway and the port are fakes. The checks below also prove the output
never carries a credential, a SID or a whole phone number, and that every Twilio request is a GET.
"""
import email.message
import http.client
import io
import json
import unittest
import urllib.error
import urllib.request
import urllib.response
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
        self.gateway = "alive"
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
            hermes_version=lambda: self.version, gateway_state=lambda: self.gateway, get_secret=self.secret,
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
        handlers = lambda routes: (FakeHTTPS(routes, seen), FakeHTTP(seen))  # noqa: E731
        self.assertEqual(sc.http_get(f"{BASE}/health", {}, 3.0, _handlers=handlers(lambda u: (200, {}, b"{}"))),
                         (200, b"{}"))
        self.assertEqual(seen, [(f"{BASE}/health", "GET", None, None)])
        status, _ = sc.http_get(f"{BASE}/health", {}, 3.0,
                                _handlers=handlers(lambda u: (401, {}, b'{"code": 20003}')))
        self.assertEqual(status, 401)

        class Down(urllib.request.HTTPSHandler):
            def https_open(self, req):
                raise urllib.error.URLError(ConnectionRefusedError("refused"))
        with self.assertRaises(OSError):
            sc.http_get(f"{BASE}/health", {}, 3.0, _handlers=(Down(),))

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

    def test_gateway_states(self):
        for state, ok, expect in (("alive", True, "answered Hermes's liveness probe"),
                                  ("wedged", False, "does not answer"),
                                  ("unknown", False, "could not confirm"),
                                  ("absent", False, "update Hermes"),
                                  ("not running", False, "hermes gateway start"),
                                  ("", False, "could not confirm")):
            with self.subTest(state=state):
                rig = Rig()
                rig.gateway = state
                items, _, code = rig.run()
                self.assertEqual(items["Hermes gateway"].ok, ok)
                self.assertIn(expect, items["Hermes gateway"].text)
                self.assertEqual(code == 0, ok)

    def test_real_gateway_probe(self):
        import sys
        import types
        status = types.ModuleType("gateway.status")
        liveness = types.ModuleType("hermes_cli.gateway")
        calls = []
        status.get_running_pid = lambda cleanup_stale=True: calls.append(("pid", cleanup_stale)) or 4242
        for verdict in ("alive", "wedged", "unknown"):
            with self.subTest(verdict=verdict):
                liveness.probe_gateway_loop_liveness = lambda pid, verdict=verdict: calls.append(("probe", pid)) \
                    or verdict
                with mock.patch.dict(sys.modules, {"gateway.status": status, "hermes_cli.gateway": liveness}):
                    self.assertEqual(sc.gateway_state(), verdict)
        self.assertIn(("pid", False), calls)
        self.assertIn(("probe", 4242), calls)
        with mock.patch.dict(sys.modules, {"gateway.status": status, "hermes_cli.gateway": None}):
            self.assertEqual(sc.gateway_state(), "absent")  # a Hermes without the probe: never OK from a PID
        status.get_running_pid = lambda cleanup_stale=True: None
        with mock.patch.dict(sys.modules, {"gateway.status": status, "hermes_cli.gateway": liveness}):
            self.assertEqual(sc.gateway_state(), "not running")

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
        self.assertIn("no usable answer (ConnectionRefusedError)", items["Twilio accepts the credentials"].text)
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
        items, _, _ = Rig().run()
        self.assertEqual(items["Tunnel"].text, "public_base_url answers over HTTPS and /health returns ok")

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

    def test_port_zero_is_said_plainly(self):
        rig = Rig()
        rig.settings["listen_port"] = 0
        items, _, code = rig.run()
        self.assertFalse(items["Local port"].ok)
        self.assertIn("not a fixed port", items["Local port"].text)
        self.assertNotIn(":0", items["Local port"].text)
        self.assertEqual(code, 1)

    def test_real_port_probe_with_a_fake_socket(self):
        seen = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def connect(addr, timeout):
            seen.append((addr, timeout))
            if addr[1] == 8788:
                return Conn()
            raise ConnectionRefusedError("refused")
        with mock.patch.object(sc.socket, "create_connection", connect):
            self.assertTrue(sc.port_open("127.0.0.1", 8788, 1.0))
            self.assertFalse(sc.port_open("127.0.0.1", 8789, 1.0))
        self.assertEqual(seen, [(("127.0.0.1", 8788), 1.0), (("127.0.0.1", 8789), 1.0)])


class FakeHTTPS(urllib.request.HTTPSHandler):
    """Serves https requests from ``routes(url) -> (status, headers, body or exception)``; no socket."""

    def __init__(self, routes, seen):
        super().__init__()
        self.routes, self.seen = routes, seen

    def https_open(self, req):
        self.seen.append((req.full_url, req.get_method(), req.get_header("Authorization"), req.data))
        status, headers, body = self.routes(req.full_url)
        msg = email.message.Message()
        for k, v in headers.items():
            msg[k] = v
        fp = body if hasattr(body, "read") else io.BytesIO(body)
        resp = urllib.response.addinfourl(fp, msg, req.full_url, status)
        resp.msg = "fake"
        return resp


class FakeHTTP(urllib.request.HTTPHandler):
    def __init__(self, seen):
        super().__init__()
        self.seen = seen

    def http_open(self, req):
        self.seen.append((req.full_url, "plain http", req.get_header("Authorization"), None))
        raise AssertionError("plain http must never be requested")


class Truncated(io.BytesIO):
    def read(self, *a):
        raise http.client.IncompleteRead(b'{"incoming_phone')


class HttpHelperTests(unittest.TestCase):
    """The real ``http_get`` with fake urllib handlers: redirects, downgrades, truncation, error privacy."""

    def _get(self, routes, url, headers=None):
        seen = []
        handlers = (FakeHTTPS(routes, seen), FakeHTTP(seen))
        try:
            return sc.http_get(url, headers or {}, 3.0, _handlers=handlers), seen
        except sc.ProbeError as exc:
            return exc, seen

    def test_redirects_are_not_followed_and_authorization_never_moves(self):
        auth = {"Authorization": "Basic " + "Q" * 40}
        for target in ("http://evil.example/health", "https://other.example/x", f"{BASE}/elsewhere"):
            with self.subTest(target=target):
                out, seen = self._get(lambda url: (302, {"Location": target}, b'{"ok": true}'),
                                      f"{sc.TWILIO_API}/Accounts/{ACCOUNT}/IncomingPhoneNumbers.json", auth)
                self.assertEqual(out[0], 302)
                self.assertEqual(len(seen), 1)  # the redirect target was never requested
                self.assertTrue(seen[0][0].startswith(sc.TWILIO_API))

    def test_tunnel_redirect_to_http_is_a_fix_not_a_pass(self):
        rig = Rig()
        seen = []

        def routes(url):
            if url == f"{BASE}/health":
                return 301, {"Location": "http://plain.example/health"}, b""
            return rig.net(url, {}, 3.0)[0], {}, rig.net(url, {}, 3.0)[1]
        probes = rig.probes()
        probes.http_get = lambda url, headers, timeout: (
            rig.net(url, headers, timeout) if url.startswith(sc.TWILIO_API)
            else sc.http_get(url, headers, timeout, _handlers=(FakeHTTPS(routes, seen), FakeHTTP(seen))))
        items = {i.name: i for i in sc.run(rig.config, probes)}
        self.assertFalse(items["Tunnel"].ok)
        self.assertIn("redirected (HTTP 301)", items["Tunnel"].text)
        self.assertEqual([u for u, *_ in seen], [f"{BASE}/health"])

    def test_plain_http_url_is_refused_before_any_request(self):
        out, seen = self._get(lambda url: (200, {}, b"{}"), "http://line.example.com/health")
        self.assertIsInstance(out, sc.ProbeError)
        self.assertEqual(seen, [])

    def test_truncated_body_becomes_a_probe_error(self):
        out, _ = self._get(lambda url: (200, {}, Truncated()), f"{BASE}/health")
        self.assertIsInstance(out, sc.ProbeError)
        self.assertEqual(str(out), "IncompleteRead")

    def test_unreachable_errors_keep_no_message(self):
        secret_url = f"{sc.TWILIO_API}/Accounts/{ACCOUNT}/IncomingPhoneNumbers.json?PhoneNumber={LINE}"

        class Down(urllib.request.HTTPSHandler):
            def https_open(self, req):
                raise urllib.error.URLError(ConnectionRefusedError(f"refused {secret_url} {SECRET}"))
        with self.assertRaises(sc.ProbeError) as ctx:
            sc.http_get(secret_url, {}, 3.0, _handlers=(Down(),))
        for text in (str(ctx.exception), repr(ctx.exception), str(ctx.exception.args)):
            for value in (ACCOUNT, LINE, LINE[2:], SECRET):
                self.assertNotIn(value, text)
        self.assertEqual(ctx.exception.kind, "ConnectionRefusedError")
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


class PrivacyAndShapeTests(unittest.TestCase):
    MARKERS = (ACCOUNT, KEY, SECRET, LINE, OWNER, LINE[2:], OWNER[2:], "CA" + "c" * 32)

    def assertClean(self, text):
        for value in self.MARKERS:
            self.assertNotIn(value, text)

    def test_settings_errors_show_only_allowlisted_messages(self):
        rig = Rig()
        rig.settings["public_base_url"] = "https://" + OWNER + "\uff20example.invalid"
        items, text, code = rig.run()
        self.assertFalse(items["Settings"].ok)
        self.assertIn("do not load", items["Settings"].text)
        self.assertClean(text)
        self.assertEqual(code, 1)
        for exc in (RuntimeError(f"provider failed for {ACCOUNT} {SECRET}"), ValueError(f"from_number {OWNER} bad"),
                    ValueError(f"unknown hermes-line settings: {LINE[1:]}"), KeyError(LINE),
                    ValueError("public_base_url https://user:" + SECRET + "@x.example")):
            with self.subTest(exc=type(exc).__name__):
                def boom():
                    raise exc
                items = {i.name: i for i in sc.run(boom, rig.probes())}
                self.assertIn("do not load", items["Settings"].text)
                self.assertClean(sc.report(list(items.values())))
        rig = Rig()
        rig.settings["from_number"] = "7755550100"
        items, _, _ = rig.run()
        self.assertIn("from_number must be canonical E.164", items["Settings"].text)  # allowlisted, shown

    def test_formatted_number_in_listen_host_is_redacted(self):
        for host in ("(775) 555-0199", "775.555.0199", "+1 775 555 0199"):
            with self.subTest(host=host):
                rig = Rig()
                rig.settings["listen_host"] = host
                rig.listening = False
                items, text, code = rig.run()
                self.assertFalse(items["Local port"].ok)
                self.assertIn("ending in 99", text)
                self.assertNotIn("555", text)
                self.assertEqual(code, 1)

    def test_secret_shaped_profile_name_is_not_printed_on_success(self):
        for name, shown in (("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", False), ("hermes-line-calls", True),
                            ("calls-0123456789abcdef", False)):
            with self.subTest(name=name):
                rig = Rig()
                rig.settings["call_profile"] = name
                items, text, code = rig.run()
                self.assertEqual(code, 0, text)
                self.assertTrue(items["Call profile"].ok)
                self.assertEqual(name in text, shown)

    def test_render_boundary_redaction(self):
        self.assertEqual(sc.redact("x AC" + "a" * 32 + " y"), "x [SID hidden] y")
        self.assertEqual(sc.redact("+1 (775) 555-0100"), "[number ending in 00]")
        self.assertEqual(sc.redact("key " + "aB3" * 10), "key [hidden]")
        for kept in ("0.23.0 (needs 0.22.5 or newer)", "127.0.0.1:8788", "HERMES_LINE_TWILIO_API_KEY_SECRET",
                     "HTTP 401", "3 of 11 checks", "plugins.entries.hermes-line.settings"):
            self.assertEqual(sc.redact(kept), kept)
        text = sc.report([sc.Item("Tunnel", False, f"oops {LINE} {ACCOUNT} {SECRET}x1Y")])
        self.assertClean(text)

    def test_probe_exceptions_with_secrets_inside_show_only_class_names(self):
        rig = Rig()
        leak = f"{sc.TWILIO_API}/Accounts/{ACCOUNT} {LINE} {SECRET} {KEY}"

        def get(url, headers, timeout):
            raise RuntimeError(leak)
        probes = rig.probes()
        probes.http_get = get
        probes.hermes_version = lambda: f"0.23.0 {leak}"
        probes.gateway_state = lambda: (_ for _ in ()).throw(RuntimeError(leak))
        probes.port_open = lambda *a: (_ for _ in ()).throw(RuntimeError(leak))
        probes.destination_allowed = lambda *a: (_ for _ in ()).throw(RuntimeError(leak))
        items = sc.run(rig.config, probes)
        text = sc.report(items)
        self.assertClean(text)
        self.assertIn("(RuntimeError)", text)
        self.assertIn("0.23.0 (needs", text)
        probes.profile_problems = lambda name: [f"profile at {leak}"]
        text = sc.report(sc.run(rig.config, probes))
        self.assertIn("could not show safely", text)
        self.assertClean(text)

    def test_truncated_and_malformed_twilio_answers_are_fix_lines(self):
        cases = {
            "truncated": lambda url: (_ for _ in ()).throw(http.client.IncompleteRead(b"x")),
            "not json": lambda url: (200, b"<html>"),
            "json list": lambda url: (200, b"[]"),
            "rows as object": lambda url: (200, json.dumps({"incoming_phone_numbers": {"a": 1},
                                                            "outgoing_caller_ids": {}}).encode()),
            "row not object": lambda url: (200, json.dumps({"incoming_phone_numbers": [LINE]}).encode()),
            "capabilities not object": lambda url: (200, json.dumps({"incoming_phone_numbers": [
                {"phone_number": LINE, "capabilities": [True]}]}).encode()),
            "status not int": lambda url: ("200", b"{}"),
        }
        for name, answer in cases.items():
            with self.subTest(case=name):
                rig = Rig()
                probes = rig.probes()
                first = []

                def get(url, headers, timeout, answer=answer):
                    if url.startswith(sc.TWILIO_API) and "PageSize" in url and not first:
                        first.append(url)
                        if name in ("truncated", "not json", "json list", "status not int"):
                            return answer(url)
                        return 200, b'{"incoming_phone_numbers": []}'
                    if url.startswith(sc.TWILIO_API):
                        return answer(url)
                    return rig.net(url, headers, timeout)
                probes.http_get = get
                items = {i.name: i for i in sc.run(rig.config, probes)}
                self.assertEqual(len(items), 11)
                twilio = [items[n] for n in ("Twilio accepts the credentials", "Line number on the account",
                                             "Caller ID")]
                self.assertFalse(all(i.ok for i in twilio))
                self.assertTrue(any("no usable answer" in i.text for i in twilio), [i.text for i in twilio])
                self.assertClean(sc.report(list(items.values())))

    def test_tunnel_truncated_or_odd_answers(self):
        for answer, expect in (((200, b"[1, 2]"), "not from Hermes Line"), ((200, b"\xff\xfe"), "not from Hermes Line"),
                               (http.client.IncompleteRead(b"{"), "IncompleteRead")):
            with self.subTest(answer=repr(answer)[:30]):
                rig = Rig()
                probes = rig.probes()

                def get(url, headers, timeout, answer=answer):
                    if url.endswith("/health"):
                        if isinstance(answer, Exception):
                            raise answer
                        return answer
                    return rig.net(url, headers, timeout)
                probes.http_get = get
                items = {i.name: i for i in sc.run(rig.config, probes)}
                self.assertFalse(items["Tunnel"].ok)
                self.assertIn(expect, items["Tunnel"].text)


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

    def _forbidding(self, events, argv):
        from plugin.line.engine import service as service_mod, telephony
        from plugin.line import store as store_mod

        def forbid(name):
            def f(*a, **kw):
                events.append(name)
                raise AssertionError(name)
            return f

        class EagerThread:
            def __init__(self, target=None, **kw):
                self.target = target

            def start(self):
                events.append("thread")
                self.target()
        return [mock.patch.object(hermes_glue.threading, "Thread", EagerThread),
                mock.patch.object(hermes_glue._Host, "get", forbid("service_build")),
                mock.patch.object(hermes_glue._Host, "start", forbid("service_start")),
                mock.patch.object(service_mod.CallService, "__init__", forbid("CallService")),
                mock.patch.object(service_mod.CallService, "start", forbid("CallService.start")),
                mock.patch.object(service_mod.CallService, "stop", forbid("CallService.stop")),
                mock.patch.object(store_mod.Store, "__init__", forbid("Store")),
                mock.patch.object(telephony.TwilioRestAdapter, "_post", forbid("provider")),
                mock.patch("sys.argv", argv)]

    def test_cli_process_with_enabled_true_never_builds_or_starts_the_service(self):
        """S24 review findings 1 and re-review 1: in any process whose arguments hold `hermes-line` (the check, its
        help, a missing or wrong subcommand, global flags first), registering with `enabled: true` creates no
        service, store, start thread or provider request, even when the start thread would run at once."""
        import argparse
        import plugin
        for argv in (["hermes", "hermes-line", "check"], ["hermes", "--model", "x", "hermes-line", "--help"],
                     ["hermes", "hermes-line"], ["hermes", "-p", "p", "hermes-line", "check"],
                     ["hermes", "hermes-line", "bogus"], ["hermes", "--resume", "hermes-line"]):
            with self.subTest(argv=argv):
                events = []
                ctx, rig = self._ctx()
                ctx.config["enabled"] = True
                patches = self._forbidding(events, argv) + [self._patched(rig)]
                for p in patches:
                    p.start()
                try:
                    plugin.register(ctx)
                    if argv[-1] == "check":
                        setup_fn, handler = ctx.cli["hermes-line"]
                        parser = argparse.ArgumentParser()
                        setup_fn(parser)
                        with redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as stop:
                            handler(parser.parse_args(["check"]))
                        self.assertEqual(stop.exception.code, 0, out.getvalue())
                finally:
                    for p in reversed(patches):
                        p.stop()
                self.assertEqual(events, [])
                self.assertIsNone(ctx.unloads[0].__self__.service)

    def test_other_processes_with_enabled_true_still_start_the_service(self):
        import plugin
        for argv in (["hermes", "gateway", "run"], ["hermes", "chat", "-q", "hello"], ["hermes"]):
            with self.subTest(argv=argv):
                started = []

                class RecordingThread:
                    def __init__(self, target=None, **kw):
                        self.target = target

                    def start(self):
                        started.append(getattr(self.target, "__name__", ""))
                ctx, _ = self._ctx()
                ctx.config["enabled"] = True
                with mock.patch.object(hermes_glue.threading, "Thread", RecordingThread), \
                        mock.patch("sys.argv", argv):
                    plugin.register(ctx)
                self.assertEqual(started, ["start"])

    def test_cli_invocation(self):
        for argv, expect in ((["hermes-line", "check"], True), (["-p", "work", "hermes-line", "check"], True),
                             (["--model", "x", "hermes-line", "--help"], True), (["hermes-line"], True),
                             (["--resume", "hermes-line"], True), (["gateway", "run"], False),
                             (["chat", "-q", "hermes line"], False), ([], False)):
            with self.subTest(argv=argv):
                self.assertEqual(hermes_glue.cli_invocation(argv), expect)

    def test_older_hermes_without_cli_commands(self):
        import plugin
        ctx = FakeCtx({})
        plugin.register(ctx)  # FakeCtx has no register_cli_command: /line check still works
        self.assertIn("line", ctx.commands)


if __name__ == "__main__":
    unittest.main()
