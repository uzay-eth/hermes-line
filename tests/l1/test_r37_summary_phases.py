"""Round 37 (docs/BACKLOG.md B8, B9; L7's note on Delta run 1).

B8: the call summary starts with one outcome sentence; the "Hermes Line: ..." log line follows it. The summary states
only the engine's own record: the outcome, the log line, the engine's fallback note and the lines actually exchanged,
quoted from the transcript. The model's finish note and any model summary are never used.

B9: every call record carries ``phases``: [{"phase", "since"}], oldest first, written by the store on every stored state
change (the engine names the finer ones), exposed by the desktop API as ISO 8601 times."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import fallback
from plugin.line.engine.contracts import PHASES, CallState, Direction, EndReason, Mode
from plugin.line.server.desktop_api import call_view
from plugin.line.store.db import Store
from test_r16c_fallback_question import Q

BOT = "I'm Delta's automated assistant."
GREETING = "Hi, this is Dana with Delta reservations, how can I help you?"


def make(quiet=0.0, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.", fallback_question=Q,
                       owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"], ivr_category="baggage")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay, created


def phases(line, call_id):
    return [p["phase"] for p in line.store.get_call(call_id).phases]


def summary_of(line, req):
    return wait_for(lambda: line.store.get_call(req["call_id"]).summary, timeout=8, what="summary")


class OutcomeFirstTests(unittest.TestCase):
    def test_B8_a_bot_that_hangs_up_reads_as_no_person_reached(self):
        line, req, session, relay, created = make()
        self.addCleanup(line.close)
        relay.prompt(BOT)
        relay.wait_text("Representative.")
        line.status(req["call_id"], created["sid"], "completed")  # the business side ends the call
        summary = summary_of(line, req)
        self.assertRegex(summary, r"^No person reached; Delta's automated assistant ended the call after \d+ s\. ")
        first, _, rest = summary.partition(". ")
        self.assertTrue(rest.startswith("Hermes Line: The other side hung up ("), summary)  # the log line second
        self.assertIn('Hermes: "Representative."', summary)
        self.assertIn(f'They: "{BOT}"', summary)
        self.assertNotIn("UNAUTHORIZED MODEL TEXT", summary)

    def test_B8_the_fallback_outcome_comes_first(self):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), \
                mock.patch.object(fallback, "QUIET_OTHERWISE_S", 0.2):
            line, req, session, relay, created = make()
            self.addCleanup(line.close)
            session.options["owner_ring_timeout_s"] = 0.3
            relay.prompt(GREETING)
            relay.wait_text("live person?")
            relay.prompt("Yes, speaking.")
            relay.wait_text(fallback.question_line("Sam", Q), timeout=6)
            relay.prompt("It depends on your fare.")
            relay.wait_text(fallback.THANKS_LINE, timeout=6)
            summary = summary_of(line, req)
        self.assertTrue(summary.startswith("Sam did not pick up, so Hermes asked the fallback question and got a reply "
                                           "(not verified); the call ended after"), summary)
        self.assertIn('Sam did not pick up within 0.3 s, so Hermes asked: "', summary)  # the fallback note too
        self.assertNotIn("the line asked", summary)
        self.assertIn("Hermes Line: Call completed (", summary)
        self.assertIn("answer not verified", summary)  # the engine's fallback note stays
        self.assertLess(summary.index("Sam did not pick up"), summary.index("Hermes Line:"))

    def test_L7_the_models_claims_are_never_summarised(self):
        line = Line(reply=lambda d, t: ["Thank you, goodbye!"])
        self.addCleanup(line.close)
        req, created = line.dial()
        relay = line.connect(req, created)
        relay.prompt("Thanks for calling Sample Bistro, this is Mike.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a live person")
        line.wait_state(req["call_id"], "agent_talking")
        session = line.session(req["call_id"])
        out = line.svc.run(session.request_finish("done", "I said agent and representative; the table is booked."))
        self.assertTrue(out["ok"], out)
        line.status(req["call_id"], created["sid"], "completed")
        summary = summary_of(line, req)
        self.assertNotIn("I said agent", summary)
        self.assertNotIn("table is booked", summary)
        sent = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
        for quoted in summary.split('Hermes: "')[1:]:
            self.assertTrue(any(s.startswith(quoted.split('"')[0][:40]) for s in sent), quoted)  # only sent lines
        self.assertFalse(any("phone call has ended" in p for d in line.drivers for p in getattr(d, "asks", [])))

    def test_B8_sentence_table(self):
        line, req, session, relay, created = make()
        self.addCleanup(line.close)

        def sentence(reason, phases=(), **state):
            for k, v in {"human_confirmed": False, "fallback_result": None, "had_bot_label": False,
                         "left_voicemail": False, "direction": Direction.OUTBOUND, **state}.items():
                setattr(session, k, v)
            return session._outcome_sentence(reason, 57, SimpleNamespace(phases=[{"phase": p} for p in phases]))
        self.assertEqual(sentence(EndReason.BUSINESS_HANGUP, had_bot_label=True),
                         "No person reached; Delta's automated assistant ended the call after 57 s.")
        self.assertEqual(sentence(EndReason.BUSINESS_HANGUP), "No person reached; the other side ended the call "
                                                              "after 57 s.")
        self.assertEqual(sentence(EndReason.OWNER_FINISHED, ("person", "bridged")),
                         "A person answered and Sam was connected; the call ended after 57 s.")
        self.assertEqual(sentence(EndReason.OWNER_UNAVAILABLE, ("person",), human_confirmed=True),
                         "A person answered but Sam could not be reached; the call ended after 57 s.")
        self.assertEqual(sentence(EndReason.COMPLETED, ("person",), human_confirmed=True, fallback_result="unanswered"),
                         "Sam did not pick up, so Hermes asked the fallback question and got no reply; the call ended "
                         "after 57 s.")
        self.assertEqual(sentence(EndReason.NO_ANSWER), "No answer; the call ended after 57 s.")
        self.assertEqual(sentence(EndReason.VOICEMAIL), "Reached voicemail; no message left. The call ended after 57 s.")
        self.assertEqual(sentence(EndReason.COMPLETED, direction=Direction.INBOUND),
                         "Hermes answered an incoming call; Hermes ended the call after 57 s.")
        sentence(EndReason.DROPPED)  # back to an outbound call
        self.assertEqual(session._outcome_sentence(EndReason.DROPPED, 125, SimpleNamespace(phases=[])),
                         "No person reached; the call dropped after 2 min 5 s.")


class PhaseListTests(unittest.TestCase):
    def test_B9_a_bot_call_is_dialing_then_assistant_then_ended(self):
        line, req, session, relay, created = make()
        self.addCleanup(line.close)
        relay.prompt(BOT)
        relay.wait_text("Representative.")
        relay.prompt("Please hold while I look that up.")
        line.status(req["call_id"], created["sid"], "completed")
        line.wait_state(req["call_id"], "failed")
        got = phases(line, req["call_id"])
        self.assertEqual(got[0], "dialing")
        self.assertEqual(got[1], "assistant")
        self.assertEqual(got[-1], "ended")
        self.assertTrue(set(got) <= set(PHASES))
        times = [p["since"] for p in line.store.get_call(req["call_id"]).phases]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(a != b for a, b in zip(got, got[1:])))  # consecutive repeats are not written

    def test_B9_person_owner_ringing_fallback_ended(self):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), \
                mock.patch.object(fallback, "QUIET_OTHERWISE_S", 0.2):
            line, req, session, relay, created = make()
            self.addCleanup(line.close)
            session.options["owner_ring_timeout_s"] = 0.3
            relay.prompt(GREETING)
            relay.wait_text("live person?")
            relay.prompt("Yes, speaking.")
            relay.wait_text(fallback.question_line("Sam", Q), timeout=6)
            relay.prompt("It depends on your fare.")
            line.wait_state(req["call_id"], "ended", timeout=8)
        self.assertEqual(phases(line, req["call_id"]), ["dialing", "person", "owner_ringing", "fallback", "ended"])

    def test_B9_the_desktop_api_exposes_phases_with_iso_times(self):
        line, req, session, relay, created = make()
        self.addCleanup(line.close)
        relay.prompt(BOT)
        relay.wait_text("Representative.")
        view = call_view(line.store, line.store.get_call(req["call_id"]))
        self.assertEqual([p["phase"] for p in view["phases"]], ["dialing", "assistant"])
        for p in view["phases"]:
            self.assertEqual(set(p), {"phase", "since"})
            self.assertRegex(p["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")


class StoreTests(unittest.TestCase):
    def test_B9_store_phase_rules_and_migration(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "line.db"
        clock = [1000.0]
        store = Store(path, clock=lambda: clock[0])
        call = store.create_call(task_id=None, direction=Direction.OUTBOUND, counterpart_e164="+17755550101",
                                 counterpart_masked="x", state=CallState.AWAITING_APPROVAL, mode=Mode.AGENT)
        self.assertEqual(store.get_call(call.id).phases, [])  # not yet a call
        for t, state, phase in ((1001, CallState.DIALING, None), (1002, CallState.IVR, "assistant"),
                                (1003, CallState.IVR, "assistant"), (1004, CallState.ON_HOLD, None),
                                (1005, CallState.ENDED, None)):
            clock[0] = t
            store.update_call(call.id, state=state, phase=phase)
        self.assertEqual(store.get_call(call.id).phases,
                         [{"phase": "dialing", "since": 1001}, {"phase": "assistant", "since": 1002},
                          {"phase": "hold", "since": 1004}, {"phase": "ended", "since": 1005}])
        with self.assertRaises(ValueError):
            store.update_call(call.id, state=CallState.ENDED, phase="lunch")
        store.update_call(call.id, summary="x")  # no state: no phase
        self.assertEqual(len(store.get_call(call.id).phases), 4)
        store.close()
        conn = sqlite3.connect(path)  # an older database without the column
        conn.execute("ALTER TABLE calls DROP COLUMN phases")
        conn.commit()
        conn.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.get_call(call.id).phases, [])


if __name__ == "__main__":
    unittest.main()
