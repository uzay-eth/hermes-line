"""Round 36: R1b delta 33 (N32-R). The 10 s abandonment rule is removed. The start stamp of a final is the arrival time
of the first business frame (partial or final) since the previous business final; only a final clears it, with no
timer and no reset. Cost accepted: after an abandoned partial the next final cannot confirm (fails closed); the one
after it can. Real time, quiet 1.2 s, real detector."""
import inspect
import time
import unittest

from harness import wait_for
from plugin.line.engine.machine import CallSession
from test_r35_no_promotion import make, owner_calls, partial, questions, spy


class ReviewerProbeTests(unittest.TestCase):
    """N32-R inverted: partial "Yes" stalls and gets the question; another partial "Yes" more than 10 s later (10.01 s
    and 11 s in the reviewer's table), then the real final "Yes.". The final keeps the first partial's stamp and never
    rings."""

    def test_N32R_a_partial_after_more_than_ten_seconds_never_moves_the_stamp(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        seen = spy(session)
        t0 = time.monotonic()
        partial(relay, "Yes")
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4, what="the stall question")
        wait_for(lambda: time.monotonic() - t0 > 11.0, timeout=12)
        partial(relay, "Yes")  # 11 s after the first: no reset
        time.sleep(0.1)
        relay.prompt("Yes.")
        wait_for(lambda: seen, timeout=3)
        self.assertLess(seen[0].started_at - t0, 0.2)  # the first partial's arrival
        time.sleep(2.0)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertEqual(len(questions(relay)), 1)  # the same pending utterance: no second stall question


class AbandonedPartialControlTests(unittest.TestCase):
    def test_R36_after_an_abandoned_partial_the_first_reply_cannot_confirm_the_second_can(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        seen = spy(session)
        partial(relay, "Hello")  # abandoned: never finalised
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4, what="the stall question")
        time.sleep(0.3)
        relay.prompt("Yes.")  # a real reply, but it carries the abandoned partial's stamp
        time.sleep(2.0)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertLess(seen[0].started_at, session.last_question_sent)
        relay.prompt("Yes.")  # the next final: its first frame came after the question
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertGreater(seen[1].started_at, session.last_question_sent)
        self.assertEqual(len(owner_calls(line)), 1)

    def test_R36_a_final_with_no_pending_partial_is_stamped_on_arrival(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        seen = spy(session)
        relay.prompt("Thanks for calling.")
        wait_for(lambda: seen)
        before = time.monotonic()
        relay.prompt("Hello?")
        wait_for(lambda: len(seen) == 2)
        self.assertGreater(seen[1].started_at, before)

    def test_R36_no_timer_and_no_reset_in_the_source(self):
        src = inspect.getsource(CallSession)
        self.assertNotIn("UTT_ABANDONED_S", src)
        offer = inspect.getsource(CallSession.offer)
        self.assertEqual(offer.count("self.utt_first_partial_at = "), 2)  # set on the first partial, cleared by a final


if __name__ == "__main__":
    unittest.main()
