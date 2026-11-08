"""Round 9: R1 delta 4 findings N4 (full tool scope), N5 (final gate after agent startup), N6 (auxiliary session)."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from harness import BUSINESS, OWNER, Line, wait_for
from plugin.line.policy import Settings, is_quiet

FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


class N5FinalGateAfterStartupTests(unittest.TestCase):
    """The reviewer's probe_gate_gap.py cases: policy changes while the call's agent starts."""

    def _line_with_startup_hook(self, hook, now):
        line = Line(policy={"quiet_hours_enabled": True}, now_utc=lambda: now[0])
        original = line.svc.driver_factory

        def factory(sid):
            driver = original(sid)
            start = driver.start

            def startup(briefing):
                hook(line)
                start(briefing)
            driver.start = startup
            return driver
        line.svc.driver_factory = factory
        return line

    def _assert_nothing_dialled(self, line, req, out, gate):
        self.assertFalse(out["result"]["ok"], out)
        self.assertIn(gate, out["result"]["error"])
        self.assertEqual(line.adapter.created, [])
        self.assertEqual(line.state(req["call_id"]), "failed")
        self.assertEqual(line.store.get_task(req["task_id"]).status.value, "failed")
        wait_for(lambda: req["call_id"] not in line.svc.sessions, what="provisional session cleaned up")
        self.assertEqual(line.svc.call_tools.active(), 0)
        wait_for(lambda: line.drivers and line.drivers[-1].closed, what="agent closed")
        self.assertIn("dial_rejected", [e["kind"] for e in line.store.events(req["call_id"])])

    def test_N5_quiet_hours_boundary_crossed_during_agent_startup_dials_nothing(self):
        now = [datetime(2026, 11, 6, 4, 59, 59, tzinfo=timezone.utc)]  # 20:59:59 Los Angeles

        def cross(line):
            now[0] = datetime(2026, 11, 6, 5, 0, 1, tzinfo=timezone.utc)  # 21:00:01

        line = self._line_with_startup_hook(cross, now)
        try:
            req = line.request()
            out = line.approve(req["task_id"])
            self.assertTrue(is_quiet(now[0], line.svc.gate.settings))
            self._assert_nothing_dialled(line, req, out, "quiet_hours")
        finally:
            line.close()

    def test_N5_destination_blocked_during_agent_startup_dials_nothing(self):
        now = [datetime(2026, 11, 6, 4, 59, 59, tzinfo=timezone.utc)]

        def block(line):
            line.svc.gate.settings = Settings.load({"allowed_destinations": [BUSINESS, OWNER],
                                                    "blocked_destinations": [BUSINESS],
                                                    "quiet_hours_enabled": False})

        line = self._line_with_startup_hook(block, now)
        try:
            req = line.request()
            out = line.approve(req["task_id"])
            self.assertFalse(line.svc.gate.preview(BUSINESS).allowed)
            self._assert_nothing_dialled(line, req, out, "destination")
        finally:
            line.close()

    def test_N5_consent_is_consumed_once_and_never_reminted(self):
        line = Line()
        try:
            consumed, issued = [], []
            consume, issue = line.svc.consent.consume, line.svc.consent.issue

            def counting_consume(token, action, *a, **kw):
                consumed.append(action)
                return consume(token, action, *a, **kw)

            def counting_issue(action, *a, **kw):
                issued.append(action)
                return issue(action, *a, **kw)
            line.svc.consent.consume, line.svc.consent.issue = counting_consume, counting_issue
            req, created = line.dial()
            self.assertEqual(consumed.count("dial"), 1)
            self.assertEqual(issued.count("dial"), 1)  # the approval's own token, nothing minted after startup
            self.assertEqual(created["to"], BUSINESS)
        finally:
            line.close()

    def test_N5_a_call_cancelled_during_startup_dials_nothing(self):
        now = [datetime(2026, 11, 6, 20, 0, 0, tzinfo=timezone.utc)]

        def cancel(line):
            calls = [c for c in line.store.list_calls()]
            line.store.update_task(calls[-1].task_id,
                                   status=__import__("plugin.line.engine.contracts",
                                                     fromlist=["TaskStatus"]).TaskStatus.CANCELED)

        line = self._line_with_startup_hook(cancel, now)
        try:
            req = line.request()
            out = line.approve(req["task_id"])
            self.assertFalse(out["result"]["ok"])
            self.assertEqual(line.adapter.created, [])
        finally:
            line.close()

    def test_N5_precheck_refuses_before_starting_any_agent(self):
        now = [datetime(2026, 11, 6, 4, 0, 0, tzinfo=timezone.utc)]  # 20:00 Los Angeles
        line = Line(policy={"quiet_hours_enabled": True}, now_utc=lambda: now[0])
        try:
            req = line.request()
            now[0] = datetime(2026, 11, 6, 5, 30, 0, tzinfo=timezone.utc)  # 21:30: quiet by the time it is approved
            out = line.approve(req["task_id"])
            self.assertFalse(out["result"]["ok"])
            self.assertEqual(line.adapter.created, [])
            self.assertEqual(line.drivers, [])  # no Hermes session was started for a call that cannot happen
        finally:
            line.close()


HERMES = "/Users/mac/.hermes/hermes-agent"


def _hermes_importable():
    try:
        if HERMES not in sys.path:
            sys.path.append(HERMES)
        import tools.tool_search  # noqa: F401
        return True
    except Exception:
        return False


def fake_driver(log, tools, sid="call-1"):
    from plugin.line.engine.acp_driver import AcpDriver
    return AcpDriver(sid, command=[sys.executable, "-B", FAKE, log, "0", tools], cwd=tempfile.gettempdir(),
                     start_timeout_s=10, turn_timeout_s=10)


class N4FullScopeTests(unittest.TestCase):
    @unittest.skipUnless(_hermes_importable(), "needs the local Hermes source for its real formatter")
    def test_N4_reviewer_probe_unrelated_tool_hidden_behind_the_bridge_is_refused(self):
        """probe_rule12.py: Hermes's own assemble_tool_defs + /tools formatter on our call tool plus an unrelated
        MCP tool gives only the three bridge names; the guard must refuse it."""
        import ast
        from functools import partial
        from tools.registry import registry
        from tools.tool_search import ToolSearchConfig, assemble_tool_defs, scoped_deferrable_names
        from plugin.line.engine.call_tools import disallowed_session_tools
        names = ["mcp__editor_hl_0123abcd_9e840205__hermes_line_call_finish", "mcp__other__send_message"]
        defs = [{"type": "function", "function": {"name": n, "description": "fixture only",
                                                  "parameters": {"type": "object", "properties": {}}}} for n in names]
        for n, td in zip(names, defs):
            registry.register(n, "mcp-fixture", td["function"], lambda **kw: "fixture")
        assembled = assemble_tool_defs(defs, context_length=32000, config=ToolSearchConfig.from_raw({"enabled": "on"}))
        src = Path(HERMES, "acp_adapter", "gateway_commands.py")
        tree = ast.parse(src.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tools_text")
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(src), "exec"), ns)
        reply = ns["_tools_text"]({"tools": {"other": [d["function"]["name"] for d in assembled.tool_defs]},
                                   "enabled_toolsets": ["mcp-editor_hl_0123abcd_9e840205", "mcp-other"]})
        self.assertEqual(reply.strip().splitlines()[0], "Available tools (3):")
        self.assertIn(names[1], scoped_deferrable_names(defs))  # the unrelated tool is still reachable
        problems = partial(disallowed_session_tools, server_names=["hl_0123abcd"])(reply)
        self.assertTrue(problems)  # round 8 returned [] here
        self.assertIn("deferred tools behind tool_search/tool_call cannot be verified", problems)

    def test_N4_hidden_or_extra_scope_dials_nobody(self):
        for tools in ("bridge_hidden", "extra_toolset"):
            with self.subTest(tools=tools):
                fd, log = tempfile.mkstemp(prefix="acp-r9-", suffix=".jsonl")
                os.close(fd)
                line = Line(driver_factory=lambda sid, log=log, tools=tools: fake_driver(log, tools, sid))
                try:
                    req = line.request()
                    out = line.approve(req["task_id"])
                    self.assertFalse(out["result"]["ok"])
                    self.assertEqual(line.adapter.created, [])
                    wait_for(lambda: any(e["kind"] == "agent_start_failed" for e in line.store.events(req["call_id"])))
                finally:
                    line.close()
                    os.unlink(log)

    def test_N4_each_call_gets_its_own_server_identity(self):
        fd, log = tempfile.mkstemp(prefix="acp-r9-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: fake_driver(log, "restricted", sid))
        try:
            names = []
            for _ in range(2):
                req, created = line.dial()
                names.append(line.session(req["call_id"]).mcp_server_name)
                line.svc.submit("hang_up", source="slash", call_id=req["call_id"])
                wait_for(lambda: req["call_id"] not in line.svc.sessions)
            self.assertNotEqual(names[0], names[1])
            for n in names:
                self.assertRegex(n, r"^hl_[0-9a-f]{8}$")
        finally:
            line.close()
            os.unlink(log)


def rows(log):
    with open(log) as fh:
        return [json.loads(line) for line in fh]


class N6AuxiliarySessionTests(unittest.TestCase):
    def test_N6_profile_drift_between_primary_and_detector_session_sends_no_caller_text(self):
        """Primary session restricted, the later detector session gets a full profile: no caller text reaches it."""
        fd, log = tempfile.mkstemp(prefix="acp-r9-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: fake_driver(log, "drift", sid))
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
            relay.wait_text("live person?", timeout=10)  # the detector found no model answer; the call goes on
            wait_for(lambda: any(e["kind"] == "agent_session_refused" for e in line.store.events(req["call_id"])),
                     timeout=10, what="ask session refused")
            refused = [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "agent_session_refused"]
            self.assertEqual(refused[0]["session"], "ask")
            self.assertIn("terminal", refused[0]["why"])
            self.assertEqual([r["session"] for r in rows(log) if "text" in r and r["session"] != "sess-1"], [])
            driver = line.session(req["call_id"])
            self.assertIsNotNone(driver)
        finally:
            line.close()
            os.unlink(log)

    def test_N6_detector_session_gets_its_own_empty_frozen_server_and_is_checked_first(self):
        fd, log = tempfile.mkstemp(prefix="acp-r9-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: fake_driver(log, "restricted", sid))
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
            relay.wait_text("live person?", timeout=10)
            session = line.session(req["call_id"])
            wait_for(lambda: session.driver.ask_tools_reply, timeout=10, what="ask session checked")
            new = [r["session_new"] for r in rows(log) if "session_new" in r]
            self.assertEqual(len(new), 2)
            self.assertEqual([s["name"] for s in new[0]], [session.mcp_server_name])
            self.assertEqual([s["name"] for s in new[1]], [session.ask_server_name])  # never the call tools
            self.assertNotEqual(session.mcp_server_name, session.ask_server_name)
            self.assertIn(session.ask_server_name, session.driver.ask_tools_reply)
            asked = [r for r in rows(log) if "text" in r and r["session"] == "sess-2"]
            self.assertTrue(asked)  # caller-derived text went only to the checked session
        finally:
            line.close()
            os.unlink(log)

    def test_N6_ask_bearer_reaches_no_call_tool(self):
        line = Line()
        try:
            req, created = line.dial()
            session = line.session(req["call_id"])
            spec = line.svc.ask_tool_servers(session)[0]
            import urllib.error
            import urllib.request

            def post(body):
                r = urllib.request.Request(spec["url"], data=json.dumps(body).encode(), method="POST", headers={
                    "Content-Type": "application/json", spec["headers"][0]["name"]: spec["headers"][0]["value"]})
                try:
                    with urllib.request.urlopen(r, timeout=5) as resp:
                        return resp.status, json.loads(resp.read() or b"null")
                except urllib.error.HTTPError as exc:
                    return exc.code, None
            self.assertEqual(post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1]["result"]["tools"], [])
            status, out = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "hermes_line_call_finish", "arguments": {"outcome": "done",
                                                                                            "summary": "x"}}})
            self.assertEqual(out["error"]["code"], -32602)
            self.assertIsNone(session.finish_outcome)
            self.assertIsNone(line.svc.call_tools.resolve(spec["headers"][0]["value"].split(" ", 1)[1]))
        finally:
            line.close()

    def test_N6_reviewer_protocol_fixture_checks_every_session_before_caller_text(self):
        """probe_rule12.py's second part: every session is checked before a question is sent to it."""
        from functools import partial
        from unittest.mock import patch
        from plugin.line.engine.acp_driver import AcpDriver
        from plugin.line.engine.call_tools import disallowed_session_tools

        class Conn:
            def __init__(self, *a):
                self.calls, self.n, self.names = [], 0, {}

            def request(self, m, p, **kw):
                self.calls.append((m, p))
                if m == "session/new":
                    self.n += 1
                    self.names[f"s{self.n}"] = [s["name"] for s in p.get("mcpServers", [])]
                    return {"sessionId": f"s{self.n}"}
                if m == "session/prompt":
                    text = p["prompt"][0]["text"]
                    own = ", ".join(f"mcp-editor_{n}_0123abcd" for n in self.names[p["sessionId"]])
                    reply = f"Enabled toolsets: {own}" if text == "/tools" else "fixture answer"
                    self.on_notification("session/update", {"sessionId": p["sessionId"], "update": {
                        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": reply}}})
                return {}

            def notify(self, *a):
                pass

            def close(self):
                pass

        with patch("plugin.line.engine.acp_driver._Connection", Conn):
            d = AcpDriver("c", command=["fixture"])
            d.set_mcp_servers([{"type": "http", "name": "hl_aaaaaaaa", "url": "http://127.0.0.1:9/mcp"}])
            d.set_ask_mcp_servers([{"type": "http", "name": "hl_bbbbbbbb", "url": "http://127.0.0.1:9/mcp"}])
            d.restrict_tools(partial(disallowed_session_tools, server_names=["hl_aaaaaaaa"]),
                             ask_check=partial(disallowed_session_tools, server_names=["hl_bbbbbbbb"],
                                               tools=frozenset()))
            d.start("b")
            self.assertEqual(d.ask("caller-controlled transcript"), "fixture answer")
            prompts = [(p["sessionId"], p["prompt"][0]["text"]) for m, p in d._conn.calls if m == "session/prompt"]
            self.assertEqual(prompts[:2], [("s1", "/tools"), ("s2", "/tools")])  # both checked first
            self.assertEqual(prompts[2][0], "s2")
            d.close()
        with patch("plugin.line.engine.acp_driver._Connection", Conn):
            d = AcpDriver("c", command=["fixture"])
            d.set_mcp_servers([{"type": "http", "name": "hl_aaaaaaaa", "url": "http://127.0.0.1:9/mcp"}])
            d.restrict_tools(partial(disallowed_session_tools, server_names=["hl_aaaaaaaa"]))
            d.start("b")
            self.assertEqual(d.ask("caller-controlled transcript"), "")  # no ask check: never sent
            self.assertEqual([m for m, p in d._conn.calls].count("session/new"), 1)


if __name__ == "__main__":
    unittest.main()
