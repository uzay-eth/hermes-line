"""Round 6: the call's in-call tools as a session-scoped MCP server on loopback, handed to ACP in session/new."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from harness import Line, wait_for
from plugin.line.engine import hermes_glue
from plugin.line.engine.acp_driver import AcpDriver
from plugin.line.engine.call_tools import IN_CALL_TOOLS, CallToolTokens, require_loopback
from plugin.line.server.runner import ServerThread

FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


def rows(log):
    with open(log) as fh:
        return [json.loads(line) for line in fh]


class Mcp:
    """A minimal MCP client (what the Hermes gateway does over Streamable HTTP)."""

    def __init__(self, spec: dict):
        self.url = spec["url"]
        self.headers = {h["name"]: h["value"] for h in spec["headers"]}
        self.ids = 0

    def post(self, body, *, headers=None, raw=None, method="POST"):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if headers is not None:
            h = {**h, **headers}
            h = {k: v for k, v in h.items() if v is not None}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read()
                return resp.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def rpc(self, method, params=None, **kw):
        self.ids += 1
        return self.post({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params or {}}, **kw)

    def call(self, name, arguments, **kw):
        status, body = self.rpc("tools/call", {"name": name, "arguments": arguments}, **kw)
        if status != 200:
            return status, None
        return status, json.loads(body["result"]["content"][0]["text"])


class TokenTests(unittest.TestCase):
    def test_tokens_are_per_call_single_holder_expiring_and_revocable(self):
        now = [1000.0]
        book = CallToolTokens(ttl_s=60, clock=lambda: now[0])
        a, b = book.issue("call_a"), book.issue("call_b")
        self.assertGreaterEqual(len(a), 43)  # 256 bits
        self.assertEqual((book.resolve(a), book.resolve(b)), ("call_a", "call_b"))
        for bad in (None, "", "x", a + "x", a[:-1], "Bearer " + a, "y" * 500):
            self.assertIsNone(book.resolve(bad))
        a2 = book.issue("call_a")
        self.assertIsNone(book.resolve(a))  # a re-issue retires the old token
        self.assertEqual(book.resolve(a2), "call_a")
        book.revoke("call_a")
        self.assertIsNone(book.resolve(a2))
        self.assertEqual(book.resolve(b), "call_b")
        now[0] += 61
        self.assertIsNone(book.resolve(b))  # expired
        self.assertEqual(book.active(), 0)
        self.assertNotIn(b, repr(book.__dict__))  # only digests are held

    def test_non_loopback_bind_is_refused(self):
        for host in ("0.0.0.0", "::", "192.168.1.5", "line.test", ""):
            with self.assertRaises(ValueError):
                require_loopback(host)
            with self.assertRaises(ValueError):
                ServerThread(None, host="127.0.0.1", port=0, tools_host=host)
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertEqual(require_loopback(host), host)


class SessionMcpTests(unittest.TestCase):
    def setUp(self):
        self.line = Line()

    def tearDown(self):
        self.line.close()

    def _talking(self, line=None):
        line = line or self.line
        req, created = line.dial()
        relay = line.connect(req, created)
        relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a real person")
        line.wait_state(req["call_id"], "agent_talking")
        driver = next(d for d in line.drivers if d.session_id == line.session(req["call_id"]).agent_session_id)
        wait_for(lambda: getattr(driver, "mcp_servers", None), what="session mcp servers")
        return req, relay, driver

    def _other_line(self):
        other = Line()
        self.addCleanup(other.close)
        return other

    def test_R6_spec_is_loopback_http_with_a_bearer_header_and_noted_in_the_briefing(self):
        req, relay, driver = self._talking()
        (spec,) = driver.mcp_servers
        self.assertEqual(spec["type"], "http")
        self.assertRegex(spec["name"], r"^hl_[0-9a-f]{8}$")  # round 9: a per-call name
        self.assertTrue(spec["url"].startswith("http://127.0.0.1:"))
        self.assertNotEqual(spec["url"].split(":")[2].split("/")[0], str(self.line.svc.server.port))  # not ingress
        self.assertEqual([h["name"] for h in spec["headers"]], ["Authorization"])
        self.assertIn("MCP prefix", driver.briefing)
        token = spec["headers"][0]["value"].split(" ", 1)[1]
        self.assertNotIn(token, json.dumps(self.line.store.events()))
        self.assertNotIn(token, driver.briefing)

    def test_R6_tool_list_matches_the_in_process_tools(self):
        req, relay, driver = self._talking()
        mcp = Mcp(driver.mcp_servers[0])
        status, init = mcp.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(status, 200)
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertEqual(mcp.post({"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        status, listed = mcp.rpc("tools/list")
        tools = {t["name"]: t for t in listed["result"]["tools"]}
        self.assertEqual(set(tools), {"hermes_line_call_press_digits", "hermes_line_call_finish",
                                      "hermes_line_call_bring_owner",
                                      "hermes_line_call_new_task"})  # round 13: owner-only, refused before the PIN
        for name, tool in tools.items():  # one definition for both routes
            self.assertEqual(tool["inputSchema"], hermes_glue.TOOLS[name]["parameters"])
            self.assertEqual(tool["description"], hermes_glue.TOOLS[name]["description"])
            self.assertIs(hermes_glue.TOOLS[name], IN_CALL_TOOLS[name])
        self.assertEqual(mcp.rpc("ping")[1]["result"], {})
        self.assertEqual(mcp.rpc("resources/list")[1]["error"]["code"], -32601)
        for other in ("hermes_line_hang_up", "hermes_line_request_call", "hermes_line_take_over"):  # owner tools
            self.assertEqual(mcp.rpc("tools/call", {"name": other, "arguments": {}})[1]["error"]["code"], -32602)

    def test_R6_tool_parity_with_the_in_process_handlers(self):
        other = self._other_line()  # one live call per line: the in-process call runs on a second, equal line
        a_req, a_relay, a_driver = self._talking(other)
        b_req, b_relay, b_driver = self._talking()
        a_sid = other.session(a_req["call_id"]).agent_session_id
        mcp = Mcp(b_driver.mcp_servers[0])
        cases = [("press_digits", "hermes_line_call_press_digits", ("2",), {"digits": "2"}),
                 ("press_digits", "hermes_line_call_press_digits", ("3",), {"digits": "3"}),  # same prompt: refused
                 ("press_digits", "hermes_line_call_press_digits", ("9x",), {"digits": "9x"}),
                 ("request_finish", "hermes_line_call_finish", ("maybe", "x"), {"outcome": "maybe", "summary": "x"}),
                 ("request_owner", "hermes_line_call_bring_owner", (), {}),
                 ("request_finish", "hermes_line_call_finish", ("done", "Booked."), {"outcome": "done",
                                                                                     "summary": "Booked."})]
        for method, tool, args, arguments in cases:
            with self.subTest(tool=tool, args=args):
                local = other.svc.in_call(a_sid, method, *args)
                status, remote = mcp.call(tool, arguments)
                self.assertEqual(status, 200)
                self.assertEqual(remote, local)
        a, b = other.session(a_req["call_id"]), self.line.session(b_req["call_id"])
        self.assertEqual((a.finish_outcome, a.owner_requested), (b.finish_outcome, b.owner_requested))
        self.assertEqual([t["text"] for t in other.store.transcript(a_req["call_id"]) if t["text"].startswith("Pressed")],
                         [t["text"] for t in self.line.store.transcript(b_req["call_id"])
                          if t["text"].startswith("Pressed")])

    def test_R6_token_of_another_call_acts_only_on_its_own_call(self):
        # an earlier call's token on this service: refused, and the live call is untouched
        first, _, first_driver = self._talking()
        old = Mcp(first_driver.mcp_servers[0])
        self.line.svc.submit("hang_up", source="slash", call_id=first["call_id"])
        wait_for(lambda: first["call_id"] not in self.line.svc.sessions, what="first call finished")
        b_req, b_relay, b_driver = self._talking()
        self.assertEqual(old.call("hermes_line_call_finish", {"outcome": "done", "summary": "x"})[0], 401)
        self.assertIsNone(self.line.session(b_req["call_id"]).finish_outcome)
        # a live call's token on another service (another call's server): refused there
        other = self._other_line()
        a_req, a_relay, a_driver = self._talking(other)
        wrong = Mcp({"url": a_driver.mcp_servers[0]["url"], "headers": b_driver.mcp_servers[0]["headers"]})
        self.assertEqual(wrong.call("hermes_line_call_finish", {"outcome": "done", "summary": "x"})[0], 401)
        self.assertIsNone(other.session(a_req["call_id"]).finish_outcome)
        # the right token acts on its own call only; a call_id argument changes nothing
        status, out = Mcp(b_driver.mcp_servers[0]).call(
            "hermes_line_call_finish", {"outcome": "done", "summary": "B", "call_id": a_req["call_id"]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.line.session(b_req["call_id"]).finish_outcome, "done")
        self.assertIsNone(other.session(a_req["call_id"]).finish_outcome)

    def test_R6_wrong_missing_expired_and_ended_tokens_are_refused(self):
        req, relay, driver = self._talking()
        spec = driver.mcp_servers[0]
        mcp = Mcp(spec)
        good = {"outcome": "done", "summary": "x"}
        for headers in ({"Authorization": None}, {"Authorization": "Bearer " + "A" * 43},
                        {"Authorization": spec["headers"][0]["value"].replace("Bearer ", "Basic ")},
                        {"Authorization": spec["headers"][0]["value"] + "x"}):
            with self.subTest(headers=str(headers)[:30]):
                self.assertEqual(mcp.call("hermes_line_call_finish", good, headers=headers)[0], 401)
        self.assertIsNone(self.line.session(req["call_id"]).finish_outcome)
        self.assertEqual(mcp.post(None, method="GET")[0], 405)
        self.assertEqual(mcp.rpc("ping", headers={"Host": "evil.example"})[0], 421)  # DNS rebinding
        self.assertEqual(mcp.post(None, raw=b"x" * (65 * 1024))[0], 413)
        self.assertEqual(mcp.post(None, raw=b"{not json")[1]["error"]["code"], -32700)
        self.assertEqual(mcp.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])[1]["error"]["code"], -32600)
        clock = self.line.svc.call_tools._clock
        self.line.svc.call_tools._clock = lambda: clock() + 10 ** 6  # past the token's time limit
        try:
            self.assertEqual(mcp.rpc("ping")[0], 401)
        finally:
            self.line.svc.call_tools._clock = clock
        # expiry pruned it: even with the real clock back, that token is gone
        self.assertEqual(mcp.rpc("ping")[0], 401)

    def test_R6_token_is_revoked_when_the_call_ends(self):
        req, relay, driver = self._talking()
        mcp = Mcp(driver.mcp_servers[0])
        self.assertEqual(mcp.rpc("ping")[0], 200)
        self.line.svc.submit("hang_up", source="slash", call_id=req["call_id"])
        self.line.wait_state(req["call_id"], "ended")
        wait_for(lambda: req["call_id"] not in self.line.svc.sessions, what="session finished")
        self.assertEqual(mcp.call("hermes_line_call_finish", {"outcome": "done", "summary": "x"})[0], 401)
        self.assertEqual(self.line.svc.call_tools.active(), 0)
        reasons = [e["data"]["reason"] for e in self.line.store.events() if e["kind"] == "call_tools_rejected"]
        self.assertIn("token", reasons)

    def test_R6_listener_is_loopback_and_closes_on_stop(self):
        import socket
        line = Line()
        port = line.svc.server.tools_port
        self.assertIsNotNone(port)
        self.assertNotEqual(port, line.svc.server.port)
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        line.close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()

    def test_R6_no_server_no_spec(self):
        line = Line(start=False)
        try:
            session = type("S", (), {"terminal": False, "call_id": "c"})()
            self.assertEqual(line.svc.call_tool_servers(session), [])  # not serving: no spec, no token
            self.assertEqual(line.svc.call_tools.active(), 0)
        finally:
            line.close()


class AcpSessionMcpTests(unittest.TestCase):
    def setUp(self):
        fd, self.log = tempfile.mkstemp(prefix="acp-r6-", suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.log)

    def _driver(self, sid):
        return AcpDriver(sid, command=[sys.executable, "-B", FAKE, self.log], cwd=tempfile.gettempdir(),
                         start_timeout_s=10, turn_timeout_s=10)

    def test_R6_driver_passes_the_servers_in_session_new(self):
        d = self._driver("call-1")
        d.set_mcp_servers([{"type": "http", "name": "hermes_line", "url": "http://127.0.0.1:9/mcp",
                            "headers": [{"name": "Authorization", "value": "Bearer sample-not-a-token"}]}])
        d.start("b")
        d.close()
        new = [r["session_new"] for r in rows(self.log) if "session_new" in r]
        self.assertEqual(new, [[{"name": "hermes_line", "type": "http", "url": "http://127.0.0.1:9/mcp",
                                 "headers": ["Authorization"]}]])
        d2 = self._driver("call-2")  # without servers: an empty list, as before
        d2.start("b")
        d2.close()
        self.assertEqual([r["session_new"] for r in rows(self.log) if "session_new" in r][-1], [])

    def test_R6_cross_profile_call_finishes_through_the_session_mcp(self):
        """The agent process shares nothing with the service but the MCP spec it got in session/new:
        no store, no plugin, no in-process tool. Its model calls hermes_line_call_finish through it."""
        line = Line(driver_factory=self._driver)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
            relay.wait_text("live person?", timeout=10)
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")
            relay.wait_text("Hello from the fake agent.", timeout=10)
            relay.prompt("MCPFINISH: we have Friday at 7 for four.")
            relay.wait_text('finish:{"ok": true', timeout=10)
            line.wait_state(req["call_id"], "ended", timeout=15)
            wait_for(lambda: req["call_id"] not in line.svc.sessions, what="session finished")
            call = line.store.get_call(req["call_id"])
            self.assertEqual(call.end_reason, "completed")
            self.assertEqual(line.store.get_task(req["task_id"]).outcome, "done")
            self.assertEqual(line.svc.call_tools.active(), 0)
            self.assertNotIn("Bearer", json.dumps(line.store.events()))
            self.assertNotIn("Bearer", Path(self.log).read_text())  # the agent log keeps header names only
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
