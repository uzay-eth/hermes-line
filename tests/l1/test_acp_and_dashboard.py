"""ACP driver against a scripted ACP agent process, cross-process in-call tools, dashboard shim."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import harness
from harness import Line, wait_for
from plugin.line.engine.acp_driver import AcpDriver, AcpError

FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


def driver(log, delay=0.0, sid="call-1"):
    return AcpDriver(sid, command=[sys.executable, "-B", FAKE, log, str(delay)], cwd=tempfile.gettempdir(),
                     start_timeout_s=10, turn_timeout_s=10)


def prompts(log):
    if not os.path.exists(log):
        return []
    with open(log) as fh:
        return [row for row in map(json.loads, fh) if "text" in row]  # session_new rows are not prompts


class AcpDriverTests(unittest.TestCase):
    def setUp(self):
        fd, self.log = tempfile.mkstemp(prefix="acp-", suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.log)

    def test_streams_text_only_and_sends_briefing_once(self):
        d = driver(self.log)
        d.start("BRIEFING: be brief")
        self.assertEqual(d.hermes_session_id, "sess-1")
        got = []
        res = d.turn("[business] hello", got.append)
        self.assertEqual("".join(got), "Hello from the fake agent.")  # thought chunks never reach the caller
        self.assertFalse(res.interrupted)
        self.assertIsNotNone(res.first_text_ms)
        d.turn("[business] again", got.append)
        texts = [p["text"] for p in prompts(self.log)]
        self.assertIn("BRIEFING: be brief", texts[0])
        self.assertNotIn("BRIEFING", texts[1])
        d.close()
        self.assertIsNone(d._conn)

    def test_barge_in_cancels_and_tells_the_next_turn_what_was_heard(self):
        d = driver(self.log, delay=0.1)
        d.start("b")
        got = []
        out = []
        t = threading.Thread(target=lambda: out.append(d.turn("[business] hi", got.append)))
        t.start()
        wait_for(lambda: got, what="first chunk")
        d.interrupt("Hello")
        t.join(5)
        self.assertTrue(out[0].interrupted)
        self.assertLess(len(got), 5)
        d.turn("[business] what?", lambda c: None)
        last = prompts(self.log)[-1]["text"]
        self.assertIn('heard only: "Hello"', last)
        d.close()

    def test_permission_requests_are_declined(self):
        d = driver(self.log)
        d.start("b")
        got = []
        d.turn("PERMISSION please", got.append)
        self.assertIn("permission:deny", "".join(got))
        d.close()

    def test_ask_uses_its_own_session(self):
        d = driver(self.log)
        d.start("b")
        self.assertEqual(d.ask("classify this", 5), "Hello from the fake agent.")
        sessions = {p["session"] for p in prompts(self.log)}
        self.assertEqual(sessions, {"sess-2"})
        d.note("owner said X")
        d.turn("next", lambda c: None)
        self.assertIn("[call note] owner said X", prompts(self.log)[-1]["text"])
        self.assertEqual(prompts(self.log)[-1]["session"], "sess-1")
        d.close()

    def test_agent_process_death_is_a_failed_turn_not_a_hang(self):
        d = driver(self.log)
        d.start("b")
        start = time.monotonic()
        res = d.turn("DIE", lambda c: None)
        self.assertIsNotNone(res.error)
        self.assertLess(time.monotonic() - start, 5)
        with self.assertRaises(AcpError):
            d._conn.request("session/new", {"cwd": "/", "mcpServers": []}, timeout=1)
        d.close()

    def test_close_releases_the_process(self):
        d = driver(self.log)
        d.start("b")
        proc = d._conn.proc
        d.close()
        self.assertIsNotNone(proc.poll())
        self.assertTrue(proc.stdout.closed)

    def test_missing_cli_fails_cleanly(self):
        d = AcpDriver("x", command=["/nonexistent/hermes", "acp"])
        with self.assertRaises(OSError):
            d.start("b")


class AcpCallFlowTests(unittest.TestCase):
    def test_full_call_on_the_acp_driver(self):
        fd, log = tempfile.mkstemp(prefix="acp-flow-", suffix=".jsonl")
        os.close(fd)
        line = Line(driver_factory=lambda sid: driver(log, sid=sid))
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Thanks for calling, your verification code is 482913, this is Mike.")
            relay.wait_text("live person?", timeout=10)
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")
            relay.wait_text("Hello from the fake agent.", timeout=10)
            sent = json.dumps(prompts(log))
            self.assertNotIn("482913", sent)  # drivers only see masked speech
            # the gateway session id now maps to the call: an in-call tool works by that id
            out = line.svc.in_call("sess-1", "request_finish", "done", "Booked.")
            self.assertTrue(out["ok"], out)
        finally:
            line.close()
            os.unlink(log)


class CrossProcessInCallTests(unittest.TestCase):
    def test_in_call_tool_from_another_process_goes_through_the_store(self):
        tmp = Path(tempfile.mkdtemp(prefix="hl-xproc-"))
        owner = Line(store_dir=tmp, reply=lambda d, t: ["WAIT"])
        other = Line(store_dir=tmp, start=False)
        try:
            req, created = owner.dial()
            relay = owner.connect(req, created)
            relay.prompt("For English press 1.")
            owner.wait_state(req["call_id"], "ivr")
            owner.svc.register_agent_session("gateway-sess-42", owner.session(req["call_id"]))
            out = other.svc.in_call("gateway-sess-42", "press_digits", "1")
            self.assertTrue(out["ok"], out)
            wait_for(lambda: relay.of_type("sendDigits"))
            self.assertFalse(other.svc.in_call("unknown-sess", "press_digits", "1")["ok"])
            self.assertFalse(other.svc.in_call("gateway-sess-42", "hang_up")["ok"])
            forged = owner.svc.submit("approve", source="call-agent", task_id=req["task_id"])
            self.assertFalse(forged["result"]["ok"])
            hang = owner.svc.submit("hang_up", source="call-agent", call_id=req["call_id"])
            self.assertFalse(hang["result"]["ok"])
            mismatch = owner.svc.submit("in_call", source="call-agent", call_id=req["call_id"],
                                        payload={"method": "press_digits", "args": ["1"], "agent_session": "other"})
            self.assertFalse(mismatch["result"]["ok"])
            self.assertEqual(owner.state(req["call_id"]), "ivr")
            owner.svc.submit("hang_up", source="slash", call_id=req["call_id"])
            owner.wait_state(req["call_id"], "ended")
            self.assertIsNone(owner.store.get_agent_session("gateway-sess-42"))  # dead once the call ends
        finally:
            other.store.close()
            owner.close()


class DashboardShimTests(unittest.TestCase):
    def test_hermes_style_standalone_import_mounts_the_router(self):
        root = Path(harness.__file__).resolve().parents[2] / "plugin"
        manifest = json.loads((root / "dashboard" / "manifest.json").read_text())
        self.assertEqual(manifest["name"], "hermes-line")
        self.assertEqual(manifest["api"], "plugin_api.py")
        sys.modules.pop("hermes_line_backend", None)
        name = "hermes_dashboard_plugin_hermes-line"
        spec = importlib.util.spec_from_file_location(name, root / "dashboard" / manifest["api"])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        paths = sorted(r.path for r in module.router.routes)
        self.assertEqual(paths, ["/calls", "/calls/{call_id}/{action}", "/status", "/tasks/{task_id}/{action}"])
        hermes_root = Path("/Users/mac/.hermes/hermes-agent")
        if not (hermes_root / "plugins" / "plugin_storage.py").exists():
            self.skipTest("Hermes source not present: default store not exercised")
        sys.path.insert(0, str(hermes_root))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("HERMES_HOME")
            os.environ["HERMES_HOME"] = home
            try:
                app = FastAPI()
                app.include_router(module.router, prefix="/api/plugins/hermes-line")
                body = TestClient(app).get("/api/plugins/hermes-line/calls").json()
                self.assertEqual(body["calls"], [])
                self.assertTrue((Path(home) / "plugin-data" / "hermes-line" / "line.db").exists())
            finally:
                if old is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old



class DriverSelectionTests(unittest.TestCase):
    def test_shipped_package_runs_calls_over_acp_only(self):
        from plugin.line.engine.config import EngineConfig
        root = Path(harness.__file__).resolve().parents[2] / "plugin"
        self.assertFalse((root / "line" / "engine" / "embedded.py").exists())
        for key in ("driver", "reasoning_effort"):
            with self.assertRaises(ValueError):
                EngineConfig.load({key: "x"})
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text()
            for name in ("run_agent", "hermes_cli.runtime_provider"):
                if f"import {name}" in text or f"from {name}" in text:
                    offenders.append(f"{path.relative_to(root)}: {name}")
        self.assertEqual(offenders, [])

    def test_glue_builds_the_acp_driver(self):
        from unittest import mock
        from test_otp_and_plugin import FakeCtx
        from test_s7_call_profile import STOCK_SOUL, make_profile
        import plugin
        from plugin.line.engine import acp_driver, call_profile, hermes_glue
        from plugin.line.engine.acp_driver import AcpDriver, AcpError
        for profile in ("hermes-line-calls", ""):
            ctx = FakeCtx({"sms_source": "none", "call_profile": profile})
            plugin.register(ctx)
            host = ctx.unloads[0].__self__
            fake_store = mock.MagicMock()
            neutral = make_profile(Path(tempfile.mkdtemp(prefix="hl-glue-")), cloned=False)
            with mock.patch("plugins.plugin_storage.plugin_data_dir", create=True) as data_dir, \
                    mock.patch("plugin.line.store.Store", return_value=fake_store), \
                    mock.patch.object(hermes_glue, "_profile_dir", return_value=neutral), \
                    mock.patch.object(hermes_glue, "_neutral_souls", return_value=[STOCK_SOUL]), \
                    mock.patch.object(call_profile, "loaded_env_problems", create=True, return_value=[]), \
                    mock.patch.object(acp_driver.shutil, "which", lambda n: "/bin/hermes" if n == "hermes" else None):
                data_dir.return_value = Path(tempfile.mkdtemp(prefix="hl-glue-"))
                svc = host.get()
                if profile:
                    self.assertIsInstance(svc.driver_factory("s1"), AcpDriver)
                else:  # STEER-7: no call profile, no call session (README setup step 4)
                    with self.assertRaises(AcpError):
                        svc.driver_factory("s1")


if __name__ == "__main__":
    unittest.main()
