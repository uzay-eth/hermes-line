"""Round 30: Delta run 2 false human (evidence/trial/call_2243b7f205194ab5).

1. No talking over the business: a fixed pre-human line (the live-person question, an assistant phrase) waits until
   the business turn has ended and ``quiet_before_speak_s`` (1.2 s) of quiet has passed; ConversationRelay sends
   partial prompts, so speech in progress counts as activity. A newer business utterance supersedes a pending line.
2. On restricted and information-only tasks the owner is rung only after our fixed question and a shared
   clear_yes() reply, in every call; a late or model human verdict alone never rings.
3. The fallback closing is neutral and the outcome honest: "unanswered" unless the reply has a digit or a currency
   word ("answered (unverified)"), and the summary says the answer is not verified."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import deps, fallback
from plugin.line.server import twiml
from test_r16c_fallback_question import Q

ROOT = Path(__file__).resolve().parents[2]
TRIAL = json.loads((ROOT / "evidence/trial/call_2243b7f205194ab5/trial.json").read_text())
BUSINESS = [t["text"] for t in TRIAL["call"]["transcript"] if t["speaker"] == "business"]
QUESTION = "live person?"


def make(quiet=1.2, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.", fallback_question=Q,
                       owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"], ivr_category="baggage")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def partial(relay, text):
    relay.send({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": False})


class ReplayTests(unittest.TestCase):
    """The run's business lines through the real engine and the real detector (L2 round 9)."""

    def test_R30_replay_never_rings_the_owner_and_asks_only_after_quiet(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        sent_at = []  # (time a business frame was sent)
        relay.prompt(BUSINESS[0])  # "Thanks for calling. I'm Del" (cut off: the bot was still talking)
        sent_at.append(time.monotonic())
        for word in ("ta's", "ta's virtual", "ta's virtual assistant"):  # it keeps talking for 0.9 s
            time.sleep(0.3)
            partial(relay, f"I'm Del{word}")
            sent_at.append(time.monotonic())
        self.assertFalse(any(QUESTION in t for t in relay.texts()))  # nothing over the business
        for text in BUSINESS[1:]:
            time.sleep(1.6)
            relay.prompt(text)
            sent_at.append(time.monotonic())
        time.sleep(1.6)
        owner = line.cfg.owner_number
        self.assertEqual([c for c in line.adapter.created if c["to"] == owner], [])  # never rung for a bot
        self.assertFalse(session.human_confirmed)
        said = relay.texts()
        self.assertNotIn("UNAUTHORIZED MODEL TEXT", said)
        self.assertFalse(any("connect" in t.lower() and "please hold" in t.lower() for t in said), said)
        self.assertNotIn(fallback.question_line("Sam", Q), said)  # no fallback question to a bot
        self.assertIn(line.state(req["call_id"]), ("dialing", "ivr", "on_hold"))

    def test_R30_the_question_waits_for_quiet_and_a_newer_line_supersedes_it(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt("Thanks for calling.")
        t0 = time.monotonic()
        for _ in range(3):
            time.sleep(0.3)
            partial(relay, "Thanks for calling, how")
        last = time.monotonic()
        relay.wait_text(QUESTION, timeout=6)
        asked = time.monotonic()
        self.assertGreaterEqual(asked - last, 1.1)  # 1.2 s of quiet after the last activity (frame jitter allowed)
        self.assertGreater(asked - t0, 1.9)


class LateHumanVerdictTests(unittest.TestCase):
    """A late (model) human verdict alone never rings on a restricted task, bot label or not."""

    def test_R30_a_late_human_verdict_asks_and_only_a_clear_yes_rings(self):
        verdicts = {"What can I help you with today regarding Delta Travel?": deps.Detection("human", "high", ("model",)),
                    "Yes, speaking.": deps.Detection("human", "high", ("model",))}

        class Scripted:
            def __init__(self, ask_model=None):
                pass

            def update(self, utt, context=None):
                return verdicts.get(utt.text, deps.Detection("unknown", "low", ("stub",)))
            feed = update
        with mock.patch.object(deps, "HoldTracker", Scripted):
            line, req, session, relay = make(quiet=0.0)
            self.addCleanup(line.close)
            relay.prompt("What can I help you with today regarding Delta Travel?")
            relay.wait_text(QUESTION)
            self.assertFalse(session.human_confirmed)
            self.assertEqual([c for c in line.adapter.created if c["to"] == line.cfg.owner_number], [])
            relay.prompt("Yes, speaking.")
            line.wait_state(req["call_id"], "ringing_owner")


class FallbackOutcomeTests(unittest.TestCase):
    def finish(self, reply):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), mock.patch.object(fallback, "QUIET_OTHERWISE_S",
                                                                                          0.2):
            line, req, session, relay = make(quiet=0.0)
            self.addCleanup(line.close)
            session.options["owner_ring_timeout_s"] = 0.2
            relay.prompt("Hi, this is Dana with Delta reservations, how can I help you?")
            relay.wait_text(QUESTION)
            relay.prompt("Yes, speaking.")
            relay.wait_text(fallback.question_line("Sam", Q), timeout=6)
            relay.prompt(reply)
            relay.wait_text(fallback.THANKS_LINE)
            line.wait_state(req["call_id"], "ended")
            summary = wait_for(lambda: line.store.get_task(req["task_id"]).summary)
            return line, req, relay, summary

    def test_R30_a_non_answer_gets_the_neutral_closing_and_is_unverified(self):
        # Round 31 (N28): no answered-heuristic. Anything said after the question is "unverified" (quoted, "answer not
        # verified"); only silence is "unanswered" (round 16c's no-answer test).
        line, req, relay, summary = self.finish("You're welcome. What can no problem.")
        self.assertEqual(fallback.THANKS_LINE, "Thank you for your time. Goodbye.")
        self.assertNotIn("very helpful", " ".join(relay.texts()))
        self.assertIn("You're welcome. What can no problem.", summary)
        self.assertIn("answer not verified", summary)
        self.assertEqual(line.store.get_task(req["task_id"]).outcome, "unverified")

    def test_R30_a_reply_with_an_amount_is_also_only_unverified(self):
        line, req, relay, summary = self.finish("The first checked bag is 35 dollars.")
        self.assertIn("answer not verified", summary)
        self.assertEqual(line.store.get_task(req["task_id"]).outcome, "unverified")
        self.assertFalse(hasattr(fallback, "looks_answered"))


class RelayTwimlTests(unittest.TestCase):
    def test_R30_partial_prompts_are_on(self):
        body = twiml.conversation_relay(ws_url="wss://x/relay", action_url="https://x/a", call_id="c", welcome=None)
        self.assertIn('partialPrompts="true"', body)


if __name__ == "__main__":
    unittest.main()
