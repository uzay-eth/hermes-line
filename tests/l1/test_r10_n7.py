"""Round 10: R1 delta 5 N7. An owner stop while the call's agent starts takes effect before dispatch."""
import threading
import time
import unittest

from harness import BUSINESS, Line, wait_for
from test_otp_and_plugin import FakeCtx

STOPPED = "Stopped before dialling"


class N7StopDuringStartupTests(unittest.TestCase):
    def setUp(self):
        import plugin
        self.line = Line(sms_source="none")
        self.entered, self.resume = threading.Event(), threading.Event()
        original = self.line.svc.driver_factory

        def factory(sid):  # the reviewer's probe_cancel.py: pause inside the agent's startup
            driver = original(sid)
            start = driver.start

            def startup(briefing):
                self.entered.set()
                assert self.resume.wait(10)
                start(briefing)
            driver.start = startup
            return driver
        self.line.svc.driver_factory = factory
        self.ctx = FakeCtx({"sms_source": "none"})
        plugin.register(self.ctx)
        self.ctx.unloads[0].__self__.service = self.line.svc  # the /line command talks to this service

    def tearDown(self):
        self.resume.set()
        self.line.close()

    def slash(self, raw):
        return self.ctx.commands["line"](raw)

    def _approve_in_background(self, req):
        replies = []
        t = threading.Thread(target=lambda: replies.append(self.slash(f"approve {req['task_id']}")))
        t.start()
        self.assertTrue(self.entered.wait(5), "agent startup reached")
        self.assertEqual(self.line.adapter.created, [])
        return t, replies

    def _assert_stopped(self, req, t, replies, reason):
        self.resume.set()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.line.adapter.created, [])  # zero provider calls
        self.assertIn(STOPPED, replies[0])                # the approve command's own answer
        call = self.line.store.get_call(req["call_id"])
        self.assertEqual((call.state.value, call.end_reason), ("ended", reason))
        task = self.line.store.get_task(req["task_id"])
        self.assertEqual((task.status.value, task.outcome), ("canceled", "stopped before dialling"))
        self.assertIn("stopped_before_dialling", [e["kind"] for e in self.line.store.events(req["call_id"])])
        wait_for(lambda: req["call_id"] not in self.line.svc.sessions, what="provisional session cleaned up")
        self.assertEqual(self.line.svc.call_tools.active(), 0)
        wait_for(lambda: self.line.drivers and self.line.drivers[-1].closed, what="agent closed")
        self.assertEqual(self.line.svc._starting, {})

    def test_N7_line_hangup_during_startup_dials_nothing_and_says_so_at_once(self):
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        started = time.monotonic()
        reply = self.slash(f"hangup {req['call_id']}")
        self.assertLess(time.monotonic() - started, 1.0)  # not queued behind the startup
        self.assertIn(STOPPED, reply)
        self._assert_stopped(req, t, replies, "hung_up_by_owner")
        cmd = [c for c in self._commands() if c["action"] == "hang_up"][0]
        wait_for(lambda: self.line.store.get_command(cmd["id"])["status"] == "done", what="stop command done")
        self.assertTrue(self.line.store.get_command(cmd["id"])["result"]["stopped_before_dialling"])

    def test_N7_line_deny_during_startup_is_a_cancel_before_dialling(self):
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        reply = self.slash(f"deny {req['task_id']}")
        self.assertIn(STOPPED, reply)
        self._assert_stopped(req, t, replies, "canceled")

    def test_N7_desktop_cancel_through_the_queue_during_startup(self):
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        out = self.line.svc.submit("cancel", source="desktop", task_id=req["task_id"], call_id=req["call_id"])
        self.assertTrue(out["result"]["stopped_before_dialling"])
        self._assert_stopped(req, t, replies, "canceled")

    def test_N7_stop_from_another_process_is_read_from_the_store(self):
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        # another process of this profile only writes the command row (no in-memory intent here)
        self.line.store.enqueue_command(idempotency_key="other-process:1", action="hang_up", source="desktop",
                                        call_id=req["call_id"], task_id=req["task_id"], payload=None)
        self.assertEqual(self.line.svc._stop_intent, {})
        self._assert_stopped(req, t, replies, "hung_up_by_owner")

    def test_N7_a_tool_cannot_deny_on_the_owners_behalf(self):
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        out = self.line.svc.submit("deny", source="tool", task_id=req["task_id"], wait_s=0.1)
        self.assertFalse((out["result"] or {}).get("stopped_before_dialling", False))
        self.resume.set()
        t.join(10)
        self.assertEqual(len(self.line.adapter.created), 1)  # deny needs the owner; the call goes ahead

    def _commands(self):
        rows = self.line.store._conn.execute("SELECT id, action FROM commands ORDER BY created_at").fetchall()
        return [{"id": r[0], "action": r[1]} for r in rows]


class N8RejectedStopsLeaveNoIntentTests(unittest.TestCase):
    """R1 delta 6 N8: a stop the executor would reject, or the queue refused, has no stop side effect."""
    setUp = N7StopDuringStartupTests.setUp
    tearDown = N7StopDuringStartupTests.tearDown
    slash = N7StopDuringStartupTests.slash
    _approve_in_background = N7StopDuringStartupTests._approve_in_background

    def _dials_anyway(self, req, t):
        self.assertEqual(self.line.svc._stop_intent, {})
        self.resume.set()
        t.join(10)
        self.assertEqual(len(self.line.adapter.created), 1)  # the call went ahead
        self.assertNotIn("stopped_before_dialling", [e["kind"] for e in self.line.store.events(req["call_id"])])

    def _call_agent_stop(self, action):
        """probe_rejected_source.py: the call's own agent cannot stop a call it is not yet on."""
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        out = self.line.svc.submit(action, source="call-agent", task_id=req["task_id"], call_id=req["call_id"],
                                   wait_s=0.1)
        self.assertFalse((out["result"] or {}).get("stopped_before_dialling", False))
        self._dials_anyway(req, t)
        done = wait_for(lambda: (r if (r := self.line.store.get_command(out["command_id"]))["status"] == "done"
                                 else None))
        self.assertEqual(done["result"], {"ok": False, "error": "not an in-call action"})

    def test_N8_call_agent_hang_up_during_startup_leaves_no_intent(self):
        self._call_agent_stop("hang_up")

    def test_N8_call_agent_cancel_during_startup_leaves_no_intent(self):
        self._call_agent_stop("cancel")

    def test_N8_stale_revision_hang_up_leaves_no_intent(self):
        """probe_stale.py: the queue refuses a stale hang-up; nothing of it remains."""
        from plugin.line.store.db import StaleRevision
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        with self.assertRaises(StaleRevision):
            self.line.svc.submit("hang_up", source="slash", task_id=req["task_id"], call_id=req["call_id"],
                                 expected_revision=0, wait_s=0.1)
        self._dials_anyway(req, t)

    def test_N8_reused_idempotency_key_adds_no_new_intent(self):
        first = self.line.request()
        self.line.svc.submit("hang_up", source="slash", call_id=first["call_id"], idempotency_key="owner:k1")
        self.line.wait_state(first["call_id"], "failed")  # hang-up while awaiting approval cancels it
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        out = self.line.svc.submit("hang_up", source="slash", call_id=req["call_id"], idempotency_key="owner:k1")
        self.assertTrue(out["duplicate"])
        self._dials_anyway(req, t)

    def test_N8_store_rows_the_executor_would_reject_do_not_stop(self):
        """pending_stop mirrors the executor for rows written by another process."""
        req = self.line.request()
        t, replies = self._approve_in_background(req)
        store = self.line.store
        store.enqueue_command(idempotency_key="other:agent", action="hang_up", source="call-agent",
                              call_id=req["call_id"], task_id=req["task_id"])
        store.enqueue_command(idempotency_key="other:tool-deny", action="deny", source="tool",
                              task_id=req["task_id"])
        row, _ = store.enqueue_command(idempotency_key="other:stale", action="hang_up", source="desktop",
                                       call_id=req["call_id"], task_id=req["task_id"])
        store._conn.execute("UPDATE commands SET expected_revision = 0 WHERE id = ?", (row["id"],))
        self.assertIsNone(store.pending_stop(req["call_id"], req["task_id"], self.line.svc.stop_acceptable))
        self._dials_anyway(req, t)


class N7AfterDispatchTests(unittest.TestCase):
    def test_N7_hang_up_after_dispatch_keeps_todays_behaviour(self):
        import plugin
        line = Line(sms_source="none")
        try:
            ctx = FakeCtx({"sms_source": "none"})
            plugin.register(ctx)
            ctx.unloads[0].__self__.service = line.svc
            req, created = line.dial()
            self.assertEqual(created["to"], BUSINESS)
            reply = ctx.commands["line"](f"hangup {req['call_id']}")
            self.assertEqual(reply, "Done.")
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "hung_up_by_owner")
            self.assertIn(created["sid"], line.adapter.ended)
            self.assertEqual(len(line.adapter.created), 1)
        finally:
            line.close()

    def test_N7_stop_arriving_while_the_dial_is_in_progress_is_not_promised(self):
        """Past the final check the call is being dispatched: the stop is not promised, it ends the call after the
        dial (today's behaviour), and nothing claims "stopped before dialling"."""
        line = Line(sms_source="none")
        try:
            dialling, release = threading.Event(), threading.Event()
            create = line.adapter._create_call

            def slow_create(**kw):
                dialling.set()
                release.wait(5)
                return create(**kw)
            line.adapter._create_call = slow_create
            req = line.request()
            t = threading.Thread(target=lambda: line.approve(req["task_id"]))
            t.start()
            self.assertTrue(dialling.wait(5))
            out = line.svc.submit("hang_up", source="slash", call_id=req["call_id"], wait_s=0.1)
            self.assertFalse((out["result"] or {}).get("stopped_before_dialling", False))
            release.set()
            t.join(10)
            line.wait_state(req["call_id"], "ended", timeout=10)
            self.assertEqual(len(line.adapter.created), 1)
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "hung_up_by_owner")
            self.assertNotIn("stopped_before_dialling", [e["kind"] for e in line.store.events(req["call_id"])])
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
