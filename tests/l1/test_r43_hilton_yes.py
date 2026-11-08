"""Round 43 (MIT-018 STEER-3): Hilton Reservations live call 1, 2026-11-08 (Hilton call 1).

A real agent came on after the queue ("Thank you for calling Hilton. This is Chris. How can I help you?"), Hermes asked
"Sorry, am I speaking with a live person?" 1.2 s later and the agent said "Yes. Are you a live person too?". The engine
judged that unclear (clear_yes rejected any question mark), the re-ask was refused by the 20 s question spacing, the
next replies ("Hello? Are you a live person too?", "I'm a live person.") were also not a clear yes, Hermes said
nothing for 14 s and the agent hung up. Replayed here through the real engine and the real detector, with the call's
own masked business lines (fixtures/hold-sim/hilton-run1.json):

1. A first-clause clear yes followed by a question back about what we are rings the owner, and the connecting line
   (with the AI disclosure, since the person asked) is spoken within about 4 s.
2. If the owner does not answer, the fallback question goes to the person as before.
3. Round 43c: "Hello? Are you a live person too?" is human-like but not a clear yes: the one re-ask goes out at once
   (no 20 s silence), and the agent's next line, "I'm a live person.", rings. Two human-like unclear replies ring the
   owner after the second question (the owner judges); bare greetings or closings never ring on their own.
4. Bot-side replies starting with "yes", and the whole menu of Hilton call 2 attempt 1, never ring.
6. While the owner is rung the person never hears more than about 6 s of silence.
5. Round 43b, Hilton call 2 (fixtures/hold-sim/hilton-run2.json): the agents' replies "Yes. You are speaking with the
   live person. Yes. Indeed." and "You are speaking to a live person. How can I help you?" ring the owner."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import fallback
from plugin.line.engine.machine import CONNECTING_LINE, REASK_LINE

ROOT = Path(__file__).resolve().parents[2]
RUN = json.loads((ROOT / "fixtures/hold-sim/hilton-run1.json").read_text())["transcript"]
BUSINESS = [t["text"] for t in RUN if t["speaker"] == "business"]
CHRIS = "We look forward Thank you for calling Hilton. This is Chris. How can I help you?"
YES_BACK = "Yes. Are you a live person too?"
Q = "What is the usual nightly rate for a standard room at a Hilton in Reno, Nevada, next Friday?"
QUESTION = "live person?"
# The automated assistant, then the transfer and queue lines that came before Chris (masked transcript order).
BOT = BUSINESS[:BUSINESS.index("Why you're calling today.") + 1]
QUEUE = BUSINESS[BUSINESS.index("I'm connecting you with an agent."):BUSINESS.index(CHRIS)]
RUN2 = [t["text"] for t in json.loads((ROOT / "fixtures/hold-sim/hilton-run2.json").read_text())["transcript"]
        if t["speaker"] == "business"]
RUN2_AGENT = "Thank you for thank you for calling Hilton. How can I help you?"
MENU2 = [t["text"] for t in json.loads((ROOT / "fixtures/hold-sim/hilton-call2-menu.json").read_text())["transcript"]
         if t["speaker"] == "business"]


def make(quiet=1.2):
    line = Line(reply=lambda d, t: ["WAIT"], quiet_before_speak_s=quiet)
    req = line.request(mode="owner", title="Hilton Reservations (bridge-call test)", goal="Ask the nightly rate.",
                       fallback_question=Q, owner_ring_timeout_s=20, intro_names=["Hilton", "Hilton Honors"],
                       ivr_category="reservations")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def owner_rings(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def to_chris(line, relay):
    for text in BOT + QUEUE:
        relay.prompt(text)
        time.sleep(0.3)
    relay.prompt(CHRIS)
    relay.wait_text(QUESTION, timeout=6)
    asked = len(relay.texts())
    return asked


class HiltonReplayTests(unittest.TestCase):
    def test_R43_fixture_is_the_recorded_call(self):
        self.assertIn(CHRIS, BUSINESS)
        self.assertEqual(BUSINESS[BUSINESS.index(CHRIS) + 1], YES_BACK)
        self.assertIn("I'm a live person.", BUSINESS)

    def test_R43_yes_with_a_question_rings_and_speaks_within_4_s(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        to_chris(line, relay)
        self.assertEqual(owner_rings(line), [])
        relay.prompt(YES_BACK)
        t0 = time.monotonic()
        connecting = CONNECTING_LINE.format(owner=line.cfg.owner_name)
        relay.wait_text(connecting, timeout=4)
        self.assertLess(time.monotonic() - t0, 4.0)
        self.assertIn("AI assistant", connecting)
        wait_for(lambda: owner_rings(line), timeout=4)
        self.assertTrue(session.human_confirmed)
        self.assertLess(time.monotonic() - t0, 4.5)

    def test_R43_no_owner_answer_sends_the_fallback_question_to_the_person(self):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), \
                mock.patch.object(fallback, "QUIET_OTHERWISE_S", 0.2):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            session.options["owner_ring_timeout_s"] = 0.2
            to_chris(line, relay)
            relay.prompt(YES_BACK)
            wait_for(lambda: owner_rings(line), timeout=4)
            relay.wait_text(fallback.question_line(line.cfg.owner_name, Q), timeout=8)
            relay.prompt("It depends on the hotel, usually around one fifty a night.")
            relay.wait_text(fallback.THANKS_LINE, timeout=6)
            line.wait_state(req["call_id"], "ended")

    def test_R43_unclear_reply_gets_the_reask_then_i_m_a_live_person_rings(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        to_chris(line, relay)
        relay.prompt("Hello? Are you a live person too?")  # human-like, not a clear yes: the re-ask at once
        t0 = time.monotonic()
        relay.wait_text(REASK_LINE, timeout=4)
        self.assertLess(time.monotonic() - t0, 4.0)
        self.assertEqual(owner_rings(line), [])
        relay.prompt("I'm a live person.")  # the agent's next line on the real call
        t0 = time.monotonic()
        relay.wait_text(CONNECTING_LINE.format(owner=line.cfg.owner_name), timeout=4)
        self.assertLess(time.monotonic() - t0, 4.0)
        wait_for(lambda: owner_rings(line), timeout=4)
        self.assertTrue(session.human_confirmed)

    def test_R43b_call_2_replies_ring(self):
        first = RUN2[RUN2.index(RUN2_AGENT) + 1]
        self.assertEqual(first, "Yes. You are speaking with the live person. Yes. Indeed.")
        for reply in (first, "You are speaking to a live person. How can I help you?",
                      "Yes. You are speaking with a live person. How can I help?"):
            with self.subTest(reply=reply):
                line, req, session, relay = make()
                self.addCleanup(line.close)
                to_chris(line, relay)
                relay.prompt(reply)
                t0 = time.monotonic()
                relay.wait_text(CONNECTING_LINE.format(owner=line.cfg.owner_name), timeout=4)
                self.assertLess(time.monotonic() - t0, 4.0)
                wait_for(lambda: owner_rings(line), timeout=4)

    def test_R43c_two_human_like_unclear_replies_ring_after_the_second_question(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        to_chris(line, relay)
        relay.prompt("Hello? Are you a live person too?")
        relay.wait_text(REASK_LINE, timeout=4)
        relay.prompt("Hello? Who is this calling?")
        relay.wait_text(CONNECTING_LINE.format(owner=line.cfg.owner_name), timeout=4)
        wait_for(lambda: owner_rings(line), timeout=4)
        self.assertIn("live_person_assumed_after_two_questions",
                      [e["kind"] for e in line.store.events(req["call_id"])])
        self.assertEqual(sum("live person?" in t for t in relay.texts()), 2)  # never a third question to this party

    def test_R43c_greetings_or_closings_alone_never_ring(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        to_chris(line, relay)
        relay.prompt("How can I help?")
        relay.wait_text(REASK_LINE, timeout=4)
        relay.prompt("Goodbye.")
        time.sleep(1.5)
        self.assertEqual(owner_rings(line), [])
        self.assertFalse(session.human_confirmed)

    def test_R43c_the_call_2_menu_never_rings(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        for text in BOT:
            relay.prompt(text)
            time.sleep(0.3)
        relay.prompt(MENU2[2])  # "How can I help?" (the assistant): our question follows
        relay.wait_text(QUESTION, timeout=6)
        for text in MENU2[3:]:
            relay.prompt(text)
            time.sleep(0.2)
        time.sleep(1.0)
        self.assertEqual(owner_rings(line), [])
        self.assertFalse(session.human_confirmed)

    def test_R43c_no_long_silence_while_the_owner_rings(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        to_chris(line, relay)
        relay.prompt(YES_BACK)
        connecting = CONNECTING_LINE.format(owner=line.cfg.owner_name)
        relay.wait_text(connecting, timeout=4)
        wait_for(lambda: owner_rings(line), timeout=4)
        spoken = time.monotonic() + session.speech_wait(connecting)  # the connecting line has been said by then
        wait_for(lambda: "ring_filler" in [e["kind"] for e in line.store.events(req["call_id"])], timeout=9)
        self.assertLess(time.monotonic() - spoken, 6.5)

    def test_R43_bot_replies_that_start_with_yes_never_ring(self):
        for reply in ("Yes, I'm a virtual assistant.", "Yes? Are you there?", "Yes, please press one.",
                      "Yes, would you like to make a payment?", "Yes, no.", "Yes, enjoy our special offer.",
                      "You are speaking with a virtual assistant. How can I help?",
                      "Yes, all of our representatives are busy."):
            with self.subTest(reply=reply):
                line, req, session, relay = make(quiet=0.0)
                self.addCleanup(line.close)
                to_chris(line, relay)
                relay.prompt(reply)
                time.sleep(1.5)
                self.assertEqual(owner_rings(line), [], reply)
                self.assertFalse(session.human_confirmed)


if __name__ == "__main__":
    unittest.main()
