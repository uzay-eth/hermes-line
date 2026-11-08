"""Round 25: speaking to automated assistants (Delta run 1, evidence/trial/call_b669b105d4774a49).

On restricted and information-only tasks, while L2's detection is ``automated_assistant`` (L2 round 5) and no person
has been detected, the engine says only fixed phrases chosen by rule (assistant.py); the model is never asked. The
DTMF/menu gate stays for keypad menus. A restricted hold task is not finished as failed while the call is up."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import assistant, fallback
from plugin.line.engine.contracts import CallState, GateRejected
from test_r16c_fallback_question import Q

ROOT = Path(__file__).resolve().parents[2]
TRIAL = json.loads((ROOT / "evidence/trial/call_b669b105d4774a49/trial.json").read_text())
BUSINESS_LINES = [t["text"] for t in TRIAL["call"]["transcript"] if t["speaker"] == "business"]
FIXED = set(assistant.PHRASES.values()) | {assistant.phrase_text("category", c) for c in assistant.CATEGORIES}
MODEL_TEXT = "I'd love to share my confirmation number ABC123 and say yes to a callback."
GUIDANCE = "Ask about the bag fee. Decline any callback offer. Do not pay anything. Do not change the booking."


def make(info_only=True, category=None, reply=None, **over):
    line = Line(reply=reply or (lambda d, t: [MODEL_TEXT]), **over)
    if info_only:
        req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.",
                           fallback_question=Q, owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"],
                           ivr_category=category)
    else:
        req = line.request(goal=GUIDANCE, intro_names=["Delta"], ivr_category=category)
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def actions(line, req):
    return [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action"]


def labels(line, req):
    return [e["data"]["label"] for e in line.store.events(req["call_id"]) if e["kind"] == "detect"]


class ReplayTests(unittest.TestCase):
    """The real call's business lines, replayed through the real engine (L2 round 5 detection)."""

    def replay(self, info_only, pause=0.35):
        line, req, session, relay = make(info_only)
        self.addCleanup(line.close)
        for text in BUSINESS_LINES:
            relay.prompt(text)
            time.sleep(pause)
        wait_for(lambda: actions(line, req), what="a fixed phrase")
        return line, req, session, relay

    def check(self, info_only):
        line, req, session, relay = self.replay(info_only)
        said = relay.texts()
        disclosure_lines = [t for t in said if "live person" in t]
        rest = [t for t in said if "live person" not in t]
        self.assertTrue(rest and all(t in FIXED for t in rest), rest)  # nothing but fixed phrases
        self.assertEqual(rest[0], "Representative.")
        self.assertNotIn(MODEL_TEXT, said)
        self.assertEqual(line.drivers[0].turns, [])  # the model was never asked
        # within two assistant turns: the first automated_assistant lines got "Representative."
        detections = labels(line, req)
        first_bot = detections.index("automated_assistant")
        events = [e for e in line.store.events(req["call_id"]) if e["kind"] in ("detect", "ivr_action")]
        bot_turns_before = 0
        for e in events:
            if e["kind"] == "detect" and e["data"]["label"] == "automated_assistant":
                bot_turns_before += 1
            if e["kind"] == "ivr_action":
                break
        self.assertLessEqual(bot_turns_before, 2)
        self.assertEqual(actions(line, req)[0], {"state": "automated_assistant", "phrase_id": "representative"})
        # no live-person question once the system said it is automated
        first_said_automated = BUSINESS_LINES.index("I'm not a live person.")
        self.assertLessEqual(len(disclosure_lines), first_said_automated)
        self.assertGreaterEqual(first_bot, 0)
        self.assertIn(session.state, (CallState.IVR, CallState.ON_HOLD))  # still up, still routing

    def test_R25_replay_information_only(self):
        self.check(True)

    def test_R25_replay_restricted(self):
        self.check(False)

    def test_R25_replay_without_spacing_says_agent_to_the_loop(self):
        with mock.patch.object(assistant, "MIN_SPACING_S", 0.0):
            line, req, session, relay = self.replay(True)
            wait_for(lambda: len(actions(line, req)) >= 3, what="the loop answered")
        ids = [a["phrase_id"] for a in actions(line, req)]
        self.assertEqual(ids[0], "representative")
        self.assertTrue(set(ids[1:]) <= {"agent"}, ids)  # data questions and patience lines get "Agent."


class PhraseRuleTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(assistant, "MIN_SPACING_S", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_lines(self, lines, **kw):
        line, req, session, relay = make(**kw)
        self.addCleanup(line.close)
        relay.prompt("I'm Delta's virtual assistant.")
        wait_for(lambda: actions(line, req), what="first phrase")
        for text in lines:
            n = len(actions(line, req)) + len([e for e in line.store.events(req["call_id"])
                                                if e["kind"] == "ivr_action_skipped"])
            relay.prompt(text)
            wait_for(lambda: len(actions(line, req)) + len([e for e in line.store.events(req["call_id"])
                                                            if e["kind"] == "ivr_action_skipped"]) > n,
                     what=f"handled: {text}")
        return line, req, session, relay

    def test_R25_data_requests_get_agent(self):
        # Round 27 (N22): "What is the card number?" is payment wording, so it now gets silence, not "Agent."
        requests = ["Can I get your confirmation number?", "What's your last name?", "Please tell me your date of "
                    "birth.", "Can you give me your SkyMiles number?", "Please say your phone number."]
        line, req, session, relay = self.run_lines(requests)
        self.assertEqual([a["phrase_id"] for a in actions(line, req)][1:], ["agent"] * len(requests))
        self.assertEqual(set(relay.texts()), {"Representative.", "Agent."})

    def test_R25_existing_trip_open_question_and_category(self):
        lines = ["Is this about an existing trip?", "How can I help you today?", "In a few words, what are you "
                 "calling about?", "How can I help?"]
        line, req, session, relay = self.run_lines(lines, category="baggage")
        self.assertEqual(relay.texts(), ["Representative.", "No, a general question.", "Baggage question.",
                                         "Speak to an agent.", "Agent."])

    def test_R25_callback_payment_and_change_offers_get_nothing_and_never_yes(self):
        lines = ["Would you like us to call you back when an agent is free?", "We can ring you back. Is that okay?",
                 "Would you like to pay the fee now?", "Do you want to change your flight?",
                 "Should I keep your place in line and text you?"]
        line, req, session, relay = self.run_lines(lines)
        self.assertEqual(relay.texts(), ["Representative."])
        skipped = [e["data"]["why"] for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action_skipped"]
        self.assertEqual(skipped, ["callback, payment or change wording"] * len(lines))
        for pid in assistant.PHRASES:
            self.assertNotIn("yes", assistant.PHRASES[pid].lower())

    def test_R25_loop_limit_twelve_per_call(self):
        line, req, session, relay = self.run_lines(["I can give you a moment if you need it."] * 14)
        self.assertEqual(len(actions(line, req)), assistant.MAX_PHRASES)
        self.assertEqual(len(relay.texts()), 12)
        skipped = [e["data"]["why"] for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action_skipped"]
        self.assertEqual(skipped, ["phrase limit reached"] * 3)


class SpacingTests(unittest.TestCase):
    def test_R25_at_most_one_phrase_every_eight_seconds(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt("I'm Delta's virtual assistant.")
        wait_for(lambda: actions(line, req))
        for _ in range(3):
            relay.prompt("I can give you a moment if you need it.")
            time.sleep(0.2)
        time.sleep(0.3)
        self.assertEqual(relay.texts(), ["Representative."])
        self.assertIn("spacing", [e["data"]["why"] for e in line.store.events(req["call_id"])
                                  if e["kind"] == "ivr_action_skipped"])


class HumanAndFinishTests(unittest.TestCase):
    def test_R25_human_detected_then_fixed_lines_only(self):
        with mock.patch.object(assistant, "MIN_SPACING_S", 0.0):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            for text in ("I'm not a live person.", "I'm Delta's automated assistant.",
                         "Please hold while I transfer you to an agent."):
                relay.prompt(text)
                time.sleep(0.2)
            n = len(actions(line, req))
            relay.prompt("Hi, this is Dana with Delta reservations, how can I help you?")
            relay.wait_text("live person?")  # round 29: after a bot label, our question first
            relay.prompt("Yes, speaking.")
            line.wait_state(req["call_id"], "ringing_owner")
            relay.prompt("Hello? Can I have your confirmation number?")
            relay.wait_text(fallback.holding_line("Sam"))
        self.assertEqual(len(actions(line, req)), n)  # no assistant phrase once a person is detected
        after = relay.texts()[relay.texts().index("Thank you. I'm an AI assistant calling for Sam. Please hold for just a moment while I connect Sam."):]
        self.assertTrue(all(t in (fallback.holding_line("Sam"),
                                  "Thank you. I'm an AI assistant calling for Sam. Please hold for just a moment while I connect Sam.") for t in after), after)
        self.assertNotIn(MODEL_TEXT, relay.texts())

    def test_R25_finish_failed_refused_while_the_call_is_up(self):
        for info_only in (True, False):
            line, req, session, relay = make(info_only)
            self.addCleanup(line.close)
            for outcome in ("failed", "callback_expected"):
                out = line.svc.run(session.request_finish(outcome, "Gave up."))
                self.assertFalse(out["ok"], (info_only, outcome))
                self.assertIn("keep routing", out["error"])
            done = line.svc.run(session.request_finish("done", "Got it."))
            self.assertEqual(done["ok"], not info_only)  # information-only: done comes from the fixed script only
            self.assertIn("finish_refused", [e["kind"] for e in line.store.events(req["call_id"])])
            self.assertIsNone(session.finish_outcome if info_only else None)

    def test_R25_finish_after_a_person_and_on_unrestricted_tasks_is_unchanged(self):
        line, req, session, relay = make(False)
        self.addCleanup(line.close)
        self.assertTrue(line.svc.run(session.set_state(CallState.AGENT_TALKING, "test: a person answered")))
        self.assertTrue(line.svc.run(session.request_finish("failed", "They could not help."))["ok"])
        plain = Line()
        self.addCleanup(plain.close)
        r, created = plain.dial(goal="Book a table for four.")
        self.assertTrue(plain.svc.run(plain.session(r["call_id"]).request_finish("failed", "Closed."))["ok"])


class ScopeTests(unittest.TestCase):
    def test_R25_unrestricted_tasks_keep_the_old_path(self):
        line = Line(reply=lambda d, t: ["Okay."])
        self.addCleanup(line.close)
        req, created = line.dial(goal="Book a table for four.")
        relay = line.connect(req, created)
        relay.prompt("How can I help you today?")  # automated_assistant under L2 round 5
        relay.wait_text("live person?")  # the live-person question decides, as before
        self.assertEqual(actions(line, req), [])

    def test_R25_keypad_menus_still_use_the_menu_gate(self):
        line, req, session, relay = make(True, reply=lambda d, t: ["WAIT"])
        self.addCleanup(line.close)
        relay.prompt("For baggage, press 2. For reservations, press 3.")
        wait_for(lambda: line.drivers[0].turns, what="menu turn")  # ivr: the model is asked, the gate decides
        self.assertEqual(actions(line, req), [])

    def test_R25_ivr_category_is_validated(self):
        line = Line()
        self.addCleanup(line.close)
        out = line.request(mode="owner", fallback_question=Q, ivr_category="Baggage")
        self.assertEqual(out["options"]["ivr_category"], "baggage")
        for bad in ("refunds", "change a flight", "baggage fees"):
            with self.assertRaises(GateRejected, msg=bad):
                line.request(mode="owner", ivr_category=bad)


if __name__ == "__main__":
    unittest.main()
