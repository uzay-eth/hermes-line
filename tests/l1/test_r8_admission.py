"""Round 8: catalog admission fixes (G5 rule 12, G3, G7, blocked_destinations plumbing, N3, G2)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import BUSINESS, Line, wait_for
from plugin.line.engine import acp_driver
from plugin.line.engine.acp_driver import AcpDriver, AcpError, child_env, default_command
from plugin.line.engine.call_tools import disallowed_session_tools, session_tool_names
from plugin.line.engine.config import EngineConfig

FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))
# Hermes v0.22.5's `/tools` reply for a default profile's ACP session (scratch probe, round 8).
DEFAULT_PROFILE = ("Available tools (19):\n  browser-use: browser_exec\n  code_execution: execute_code\n"
                   "  connections: manage_connections\n  delegation: delegate_task\n"
                   "  file: patch, read_file, search_files, write_file\n  memory: memory\n"
                   "  other: tool_call, tool_describe, tool_search\n  skills: skill_manage, skill_view, skills_list\n"
                   "  terminal: terminal\n  vision: vision_analyze\n  web: web_extract, web_search\n")
NAME = "hl_0123abcd"  # a per-call session MCP server name (round 9)
TOOLSET_ONLY = "Enabled toolsets: mcp-editor_hl_0123abcd_9e840205\n"  # live v0.22.5 form, all deferred


def check(reply, names=(NAME,)):
    return disallowed_session_tools(reply, list(names))


class ToolListTests(unittest.TestCase):
    """Round 8 G5 as corrected in round 9 (R1 delta 4 N4): the bridge names never pass."""

    def test_G5_only_this_calls_own_session_mcp_passes(self):
        self.assertEqual(check(TOOLSET_ONLY), [])
        own = ("Available tools (3):\n  mcp-editor_hl_0123abcd_9e840205: "
               "mcp__editor_hl_0123abcd_9e840205__hermes_line_call_bring_owner, "
               "mcp__editor_hl_0123abcd_9e840205__hermes_line_call_finish, "
               "mcp__editor_hl_0123abcd_9e840205__hermes_line_call_press_digits\n")
        self.assertEqual(check(own), [])  # shown, nothing deferred: the list is the scope
        extra = check(DEFAULT_PROFILE)
        for name in ("terminal", "read_file", "write_file", "browser_exec", "web_search", "delegate_task", "memory"):
            self.assertIn(name, extra)

    def test_N4_bridge_names_never_pass(self):
        bridge_only = "Available tools (3):\n  other: tool_call, tool_describe, tool_search\n"
        self.assertTrue(check(bridge_only))  # the reviewer's probe_rule12.py reply: an unrelated tool is behind it
        self.assertIn("deferred tools behind tool_search/tool_call cannot be verified", check(bridge_only))
        with_ours = ("Available tools (4):\n  mcp-editor_hl_0123abcd_9e840205: "
                     "mcp__editor_hl_0123abcd_9e840205__hermes_line_call_finish\n"
                     "  other: tool_call, tool_describe, tool_search\n")
        self.assertTrue(check(with_ours))

    def test_N4_identity_is_this_calls_server(self):
        other_call = "Enabled toolsets: mcp-editor_hl_ffffffff_9e840205\n"
        self.assertEqual(check(other_call), ["mcp-editor_hl_ffffffff_9e840205",
                                             "missing session MCP server hl_0123abcd"])
        self.assertTrue(check("Enabled toolsets: mcp-editor_hl_0123abcd_9e840205, mcp-other\n"))
        self.assertTrue(check("Enabled toolsets: mcp-editor_hl_0123abcd_9e840205, "
                              "mcp-editor_hl_0123abcd_11111111\n"))  # two servers under our name
        self.assertTrue(check("Enabled toolsets: hermes-acp\n"))
        self.assertTrue(check("No tools available.\n"))  # our server missing
        self.assertEqual(check(TOOLSET_ONLY, names=("hermes_line",)), ["no session MCP identity to bind"])
        for name in ("mcp__editor_github_0123abcd__create_issue", "mcp__cua__click",
                     "mcp__editor_hl_0123abcd_9e840205__hermes_line_hang_up",
                     "mcp__editor_hl_ffffffff_9e840205__hermes_line_call_finish", "hermes_line_call_finish"):
            self.assertEqual(check(f"Available tools (1):\n  other: {name}\n"), [name], name)

    def test_G5_unreadable_lists_fail_closed(self):
        for reply in ("", "Tools: lots", "Available tools (3):\n  other: tool_call\n", "Enabled toolsets: ",
                      "Available tools (1):\nterminal\n"):
            self.assertIsNone(check(reply), reply)
        self.assertEqual(session_tool_names("Available tools (1):\n  terminal: terminal\n"), ["terminal"])


class DriverGuardTests(unittest.TestCase):
    def setUp(self):
        fd, self.log = tempfile.mkstemp(prefix="acp-r8-", suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.log)

    def _driver(self, tools):
        from functools import partial
        d = AcpDriver("call-1", command=[sys.executable, "-B", FAKE, self.log, "0", tools], cwd=tempfile.gettempdir(),
                      start_timeout_s=10, turn_timeout_s=10)
        d.set_mcp_servers([{"type": "http", "name": NAME, "url": "http://127.0.0.1:9/mcp", "headers": []}])
        d.restrict_tools(partial(disallowed_session_tools, server_names=[NAME]))
        return d

    def test_G5_driver_refuses_an_unrestricted_or_unreadable_session(self):
        for tools in ("full", "garbled", "bridge_hidden", "extra_toolset"):
            with self.subTest(tools=tools):
                d = self._driver(tools)
                with self.assertRaises(AcpError) as caught:
                    d.start("b")
                self.assertIn("not restricted", str(caught.exception))
                self.assertIsNone(d._conn)  # the viewer is closed
        d = self._driver("restricted")
        d.start("b")
        self.assertIn("mcp-editor_hl_0123abcd", d.session_tools_reply)
        d.close()

    def test_G5_viewer_never_inherits_yolo_or_other_launch_flags(self):
        env = {"PATH": "/usr/bin", "HERMES_YOLO_MODE": "1", "HERMES_IGNORE_RULES": "1", "HERMES_ACCEPT_HOOKS": "1",
               "HERMES_SAFE_MODE": "1", "HERMES_IGNORE_USER_CONFIG": "1", "HERMES_HOME": "/x"}
        self.assertEqual(child_env(env), {"PATH": "/usr/bin", "HERMES_HOME": "/x"})
        with mock.patch.dict(os.environ, {"HERMES_YOLO_MODE": "true"}):
            self.assertNotIn("HERMES_YOLO_MODE", child_env(None))
        with mock.patch.object(acp_driver.subprocess, "Popen") as popen:
            popen.side_effect = OSError("stop")
            with mock.patch.dict(os.environ, {"HERMES_YOLO_MODE": "1"}):
                with self.assertRaises(OSError):
                    AcpDriver("c", command=["hermes", "acp"]).start("b")
            self.assertNotIn("HERMES_YOLO_MODE", popen.call_args.kwargs["env"])

    def test_G5_call_profile_selects_the_viewer_profile(self):
        with mock.patch.object(acp_driver.shutil, "which", lambda name: "/bin/hermes" if name == "hermes" else None):
            self.assertEqual(default_command(), ["/bin/hermes", "acp"])
            self.assertEqual(default_command("hermes-line-calls"), ["/bin/hermes", "--profile", "hermes-line-calls",
                                                                    "acp"])
        with mock.patch.object(acp_driver.shutil, "which", lambda name: "/bin/hermes-acp" if name == "hermes-acp" else None):
            with self.assertRaises(AcpError):
                default_command("hermes-line-calls")  # hermes-acp cannot select a profile: refuse, never ignore
        self.assertEqual(EngineConfig.load({"call_profile": "hermes-line-calls"}).call_profile, "hermes-line-calls")
        for bad in ("../x", "a b", "-p", "x" * 65):
            with self.assertRaises(ValueError):
                EngineConfig.load({"call_profile": bad})


class DialRefusalTests(unittest.TestCase):
    def test_G5_unrestricted_session_dials_nobody(self):
        fd, log = tempfile.mkstemp(prefix="acp-r8-dial-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: AcpDriver(sid, command=[sys.executable, "-B", FAKE, log, "0", "full"],
                                                         cwd=tempfile.gettempdir(), start_timeout_s=10))
        try:
            req = line.request()
            out = line.approve(req["task_id"])
            self.assertFalse(out["result"]["ok"])
            self.assertIn("nothing was dialled", out["result"]["error"])
            self.assertEqual(line.adapter.created, [])  # zero provider calls
            self.assertEqual(line.state(req["call_id"]), "failed")
            kinds = {e["kind"]: e["data"] for e in line.store.events(req["call_id"])}
            self.assertIn("dial_refused", kinds)
            wait_for(lambda: "agent_start_failed" in [e["kind"] for e in line.store.events(req["call_id"])])
            why = [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "agent_start_failed"][0]
            self.assertIn("terminal", why.get("why", ""))
        finally:
            line.close()
            os.unlink(log)

    def test_G5_restricted_session_dials(self):
        fd, log = tempfile.mkstemp(prefix="acp-r8-dial-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: AcpDriver(sid, command=[sys.executable, "-B", FAKE, log, "0",
                                                                       "restricted"],
                                                         cwd=tempfile.gettempdir(), start_timeout_s=10))
        try:
            req, created = line.dial()
            self.assertEqual(created["to"], BUSINESS)
        finally:
            line.close()
            os.unlink(log)


class N3PendingRequestTests(unittest.TestCase):
    """R1 delta 3 N3: a request authenticated before a token change must not act after it."""

    def _talking(self, line):
        req, created = line.dial()
        relay = line.connect(req, created)
        relay.prompt("Thanks for calling Sample Clinic, this is Ana.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a real person")
        line.wait_state(req["call_id"], "agent_talking")
        driver = next(d for d in line.drivers if d.session_id == line.session(req["call_id"]).agent_session_id)
        wait_for(lambda: getattr(driver, "mcp_servers", None), what="session mcp")
        return req, driver.mcp_servers[0]

    def _split_post(self, spec, body, between):
        """Send the headers, run ``between`` once the server has authenticated them, then send the body."""
        import json as _json
        import socket
        host_port = spec["url"].split("/")[2]
        port = int(host_port.split(":")[1])
        data = _json.dumps(body).encode()
        auth = spec["headers"][0]["value"]
        head = (f"POST /mcp HTTP/1.1\r\nHost: {host_port}\r\nAuthorization: {auth}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n")
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            sock.sendall(head.encode())
            time.sleep(0.3)  # the server has read and authenticated the headers and waits for the body
            between()
            sock.sendall(data)
            reply = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                reply += chunk
        finally:
            sock.close()
        return int(reply.split(b" ")[1])

    def test_N3_rotation_revocation_or_expiry_during_the_body_stops_the_request(self):
        finish = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "hermes_line_call_finish", "arguments": {"outcome": "done", "summary": "x"}}}
        for case in ("reissue", "revoke", "expiry"):
            with self.subTest(case=case):
                line = Line()
                try:
                    req, spec = self._talking(line)
                    book = line.svc.call_tools

                    def between():
                        if case == "reissue":
                            book.issue(req["call_id"])
                        elif case == "revoke":
                            book.revoke(req["call_id"])
                        else:
                            clock = book._clock
                            book._clock = lambda: clock() + 10 ** 6

                    status = self._split_post(spec, finish, between)
                    self.assertEqual(status, 401)
                    self.assertIsNone(line.session(req["call_id"]).finish_outcome)
                    self.assertEqual(line.state(req["call_id"]), "agent_talking")
                finally:
                    line.close()

    def test_N3_unchanged_token_with_a_slow_body_still_works_and_the_cap_is_streamed(self):
        line = Line()
        try:
            req, spec = self._talking(line)
            finish = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "hermes_line_call_finish", "arguments": {"outcome": "done", "summary": "x"}}}
            self.assertEqual(self._split_post(spec, finish, lambda: None), 200)
            self.assertEqual(line.session(req["call_id"]).finish_outcome, "done")
            big = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {"pad": "x" * (70 * 1024)}}
            self.assertEqual(self._split_post(spec, big, lambda: None), 413)
        finally:
            line.close()


class TextsMatchWhatExistsTests(unittest.TestCase):
    def test_G7_no_approve_in_the_pane_and_the_skill_documents_line_otp_to_call(self):
        from plugin.line.engine import hermes_glue
        skill = (Path(hermes_glue.__file__).resolve().parents[2] / "skills" / "hermes-line" / "SKILL.md").read_text()
        pane = (Path(hermes_glue.__file__).resolve().parents[2] / "desktop" / "plugin.js").read_text()
        self.assertNotIn("Desktop pane or", hermes_glue.TOOLS["hermes_line_request_call"]["description"])
        self.assertIn("/line approve", hermes_glue.TOOLS["hermes_line_request_call"]["description"])
        self.assertNotIn("approval in the Desktop pane", skill)
        self.assertIn("no approve", skill)
        self.assertIn("line_otp_to_call", skill)
        self.assertNotIn("line_otp_fill", skill)
        self.assertIn("run('take_over')", pane)  # the take-over text does name a real pane button
        self.assertIn("Take over in the pane", hermes_glue.TOOLS["hermes_line_take_over"]["description"])
        glue = Path(hermes_glue.__file__).read_text()
        self.assertNotIn("Approve in the Desktop pane", glue)


class BlockedDestinationsPlumbingTests(unittest.TestCase):
    BLOCKED = "+17755550123"  # sample number

    def test_4_blocked_destinations_pass_through_config_and_refuse_before_the_adapter(self):
        import yaml
        cfg = EngineConfig.load({"policy": {"allowed_destinations": [BUSINESS], "blocked_destinations": [self.BLOCKED]}})
        self.assertEqual(cfg.policy["blocked_destinations"], [self.BLOCKED])
        with self.assertRaises(ValueError):
            EngineConfig.load({"policy": {"blocked_destinations": ["not a number"]}})
        with self.assertRaises(ValueError):
            EngineConfig.load({"policy": {"blocked_destinations": "+17755550123"}})
        line = Line(policy={"allowed_destinations": [BUSINESS, self.BLOCKED], "blocked_destinations": [self.BLOCKED]})
        try:
            self.assertIn(self.BLOCKED, line.svc.settings.blocked_destinations)
            with self.assertRaises(Exception):
                line.request(destination=self.BLOCKED)
            self.assertEqual(line.adapter.total_calls, 0)
        finally:
            line.close()
        manifest = yaml.safe_load((Path(__file__).resolve().parents[2] / "plugin" / "plugin.yaml").read_text())
        self.assertIn("blocked_destinations", manifest["config_schema"]["policy"]["description"])
        self.assertEqual(manifest["config_schema"]["policy"]["type"], "object")  # the Desktop settings form passes it


if __name__ == "__main__":
    unittest.main()
