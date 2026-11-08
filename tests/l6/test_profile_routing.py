"""Historical same-store routing checks (session MCP now supplies the cross-profile path).

No gateway, socket, credentials or provider request. Temporary stores model the
trial home and the separate gateway profile; they never open the owner's home.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugin.line.engine.acp_driver import AcpDriver
from plugin.line.engine.config import EngineConfig
from plugin.line.engine.contracts import CallState, Direction, Mode
from plugin.line.engine.service import CallService
from plugin.line.engine.telephony import TelephonyAdapter
from plugin.line.store import Store


class ProfileRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.trial = Store(root / "trial" / "line.db")
        self.gateway = Store(root / "gateway" / "line.db")
        self.addCleanup(self.trial.close)
        self.addCleanup(self.gateway.close)
        self.sid = "offline-acp-session"
        call = self.trial.create_call(
            task_id=None, direction=Direction.OUTBOUND,
            counterpart_e164="+18475550101", counterpart_masked="[NUMBER]",
            state=CallState.DIALING, mode=Mode.AGENT)
        self.call_id = call.id
        self.trial.put_agent_session(self.sid, call.id)
        self.root = root

    def service(self, store):
        return CallService(
            cfg=EngineConfig.load({"sms_source": "none", "otp_browser_fill": False,
                                   "voice": {"transport": "conversation_relay"}}),
            store=store, adapter=Mock(spec=TelephonyAdapter), driver_factory=Mock(),
            lock_dir=self.root / "locks", get_secret=lambda _: None)

    def test_separate_profile_cannot_route_any_in_call_action(self):
        svc = self.service(self.gateway)
        svc.submit = Mock(side_effect=AssertionError("must not queue an unknown call"))
        self.assertEqual(self.trial.get_agent_session(self.sid), self.call_id)
        self.assertIsNone(self.gateway.get_agent_session(self.sid))
        for method, args in (("press_digits", ("2",)),
                             ("request_finish", ("done", "Trial complete")),
                             ("request_owner", ())):
            with self.subTest(method=method):
                self.assertEqual(svc.in_call(self.sid, method, *args), {
                    "ok": False,
                    "error": "this tool only works inside a live Hermes Line call"})
        svc.submit.assert_not_called()

    def test_same_store_routes_to_call_agent_queue(self):
        svc = self.service(self.trial)
        svc.submit = Mock(return_value={"result": {"ok": True}})
        self.assertEqual(svc.in_call(self.sid, "press_digits", "2"), {"ok": True})
        svc.submit.assert_called_once_with(
            "in_call", source="call-agent", call_id=self.call_id, poll_remote=True,
            payload={"method": "press_digits", "args": ["2"], "agent_session": self.sid})

    def test_bare_unconfigured_driver_has_no_session_mcp_tools(self):
        connection = Mock()
        connection.request.return_value = {"sessionId": self.sid}
        with patch("plugin.line.engine.acp_driver._Connection", return_value=connection):
            driver = AcpDriver("offline-call", command=["never-executed"], cwd=str(self.root))
            try:
                driver.start("Offline briefing")
                new_session = connection.request.call_args_list[1]
                self.assertEqual(new_session.args[0], "session/new")
                self.assertEqual(new_session.args[1]["mcpServers"], [])
            finally:
                driver.close()


if __name__ == "__main__":
    unittest.main()
