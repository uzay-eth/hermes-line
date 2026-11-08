"""Round 27: R1b delta 24 N20, N21, N22 (engine side), on the L2 round 7 contract.

L2 is the single source of bot and transfer recognition: a possible new party after a bot is ``unknown`` with
evidence ``possible_new_party``; a transfer line is ``hold_message`` with evidence ``transfer``. L1 no longer reads
transfers, self-declarations or personal introductions from the text. On ``possible_new_party`` the engine asks the
fixed live-person question (at most one every 20 s, 4 per call; then the assistant path continues); a clear yes goes
to the human path (owner ring as before); a bot answer keeps the assistant path. N22: an unsafe offer is checked
before data words.

Until L2 round 7 is on main, ``ContractL2`` emits exactly those labels for the reviewer's sequences; the same tables
then run through the real detector (``RealDetectorTests``, skipped until L2 implements the contract)."""
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import assistant, deps
from plugin.line.engine.machine import CallSession
from test_r16c_fallback_question import Q


async def _round43c_reply_rule_off(self, *args, **kwargs):
    """Round 43c (MIT-018 STEER-4): the reply rule (ask twice per party, then the owner judges) replaces the 20 s
    no-re-ask rule for replies to our question. This budget test checks the global question budget for everything
    the reply rule does not decide, so it runs with that rule switched off."""
    return False

BOT = "I'm Delta's automated assistant."
QUESTION = "Am I speaking with a live person?"
D = deps.Detection
CONTRACT = {  # the L2 round 7 contract labels for the reviewer's lines
    BOT: D("automated_assistant", "high", ("explicit automation",)),
    "I'm not a live person.": D("automated_assistant", "high", ("denial of personhood",)),
    "I'm a virtual assistant.": D("automated_assistant", "high", ("explicit automation",)),
    "My name is Alex, your virtual assistant. How can I help?": D("automated_assistant", "high", ("explicit",)),
    "My name is Alex. How can I help you?": D("unknown", "low", ("possible_new_party",)),  # N20: no boundary
    "Hello, how can I help you?": D("automated_assistant", "high", ("continuing bot",)),  # no boundary
    "Can I have your confirmation number?": D("automated_assistant", "high", ("continuing bot",)),
    "Hello? Are you there?": D("unknown", "low", ("possible_new_party",)),  # human-only cue, no boundary
    "[music]": D("hold_music", "high", ("music marker",)),
    "Hello, how can I help?": D("unknown", "low", ("possible_new_party",)),  # after a boundary, unnamed
    "May I have your confirmation number?": D("unknown", "low", ("possible_new_party",)),  # after a boundary
    "Let me connect you to an agent.": D("hold_message", "high", ("transfer",)),
    "Please wait while I connect you.": D("hold_message", "high", ("transfer",)),
    "Hello, this is Dana. How can I help?": D("human", "high", ("named greeting after transfer",)),
    "Yes, I'm a live person.": D("unknown", "low", ("possible_new_party",)),
}


NEW_PARTY = ["May I have your confirmation number?", "Hello, how can I help?", "Hello? Are you there?",
             "Sorry, can you hear me?", "Hello, anyone there?", "Hi, can you hear me okay?", "Hello, who is calling?"]
for _line in NEW_PARTY:
    CONTRACT.setdefault(_line, D("unknown", "low", ("possible_new_party",)))


class ContractL2:
    """A scripted HoldTracker that returns the L2 round 7 contract label for each business line."""

    def __init__(self, ask_model=None):
        pass

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") != "business":
            return D("unknown", "low", ("waiting for business response",))
        return CONTRACT.get(utt.text, D("unknown", "low", ("rules inconclusive",)))

    feed = update


def make(info_only=True, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], **over)
    if info_only:
        req = line.request(mode="owner", title="Delta", goal="Ask about the bag fee.", fallback_question=Q,
                           owner_ring_timeout_s=20, intro_names=["Delta"], ivr_category="baggage")
    else:
        req = line.request(goal="Ask about the bag fee. Decline any callback offer. Do not pay anything.",
                           intro_names=["Delta"], ivr_category="baggage")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


class ContractBase(unittest.TestCase):
    real_detector = False

    def setUp(self):
        if not self.real_detector:
            p = mock.patch.object(deps, "HoldTracker", ContractL2)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(assistant, "MIN_SPACING_S", 0.0)  # phrase spacing is tested in round 25
        p.start()
        self.addCleanup(p.stop)

    def run_seq(self, lines, info_only=True, **over):
        line, req, session, relay = make(info_only, **over)
        self.addCleanup(line.close)
        rows = []
        for text in lines:
            seen = len(relay.frames)
            relay.prompt(text)
            time.sleep(0.35)
            rows.append([f.get("token") for f in relay.frames[seen:] if f.get("type") == "text"])
        return line, req, session, relay, rows

    def owner_rung(self, line):
        return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]

    def assert_never_model_text(self, relay):
        self.assertNotIn("UNAUTHORIZED MODEL TEXT", relay.texts())


class N20Tests(ContractBase):
    def test_N20_a_name_after_a_bot_without_a_boundary_is_asked_not_rung(self):
        for mode in (True, False):
            line, req, session, relay, rows = self.run_seq([BOT, "My name is Alex. How can I help you?"], mode)
            self.assertEqual(rows[0], ["Representative."])
            self.assertTrue(any(QUESTION in t for t in rows[1]), rows)  # the fixed question, not a ring
            self.assertFalse(session.human_confirmed)
            self.assertEqual(self.owner_rung(line), [])
            self.assert_never_model_text(relay)

    def test_N20_a_bot_answer_to_the_question_keeps_the_assistant_path(self):
        line, req, session, relay, rows = self.run_seq([BOT, "My name is Alex. How can I help you?",
                                                        "I'm not a live person."])
        self.assertEqual(rows[2], ["Agent."])
        self.assertEqual(self.owner_rung(line), [])
        self.assertFalse(session.human_confirmed)

    def test_N20_explicit_bot_name_in_one_line_stays_a_bot(self):
        line, req, session, relay, rows = self.run_seq([BOT, "My name is Alex, your virtual assistant. How can I help?"])
        self.assertEqual(rows[1], ["Agent."])  # the reviewer's control: still a bot ("name" is a data word)
        self.assertEqual(self.owner_rung(line), [])


class N21Tests(ContractBase):
    def confirm_and_ring(self, lines, info_only=True):
        line, req, session, relay, rows = self.run_seq(lines + ["Yes, I'm a live person."], info_only)
        self.assertTrue(any(QUESTION in t for r in rows[:-1] for t in r), rows)
        self.assertTrue(session.human_confirmed)
        self.assertNotIn("UNAUTHORIZED MODEL TEXT", [t for r in rows[:-1] for t in r])  # nothing before the person
        if info_only:
            line.wait_state(req["call_id"], "ringing_owner")  # owner ring as before (press 1 still required)
            self.assertEqual(len(self.owner_rung(line)), 1)
            self.assert_never_model_text(relay)  # information-only: fixed lines after the person too
        return rows

    def test_N21_unnamed_human_after_a_human_only_cue(self):
        rows = self.confirm_and_ring([BOT, "Hello, how can I help you?", "Can I have your confirmation number?",
                                      "Hello? Are you there?"])
        self.assertEqual(rows[2], ["Agent."])  # still a bot then: a plain identifier request

    def test_N21_unnamed_human_after_hold_music(self):
        rows = self.confirm_and_ring([BOT, "[music]", "Hello, how can I help?"])
        self.assertEqual(rows[1], [])  # music: silent hold

    def test_N21_unnamed_human_after_a_transfer(self):
        for transfer in ("Let me connect you to an agent.", "Please wait while I connect you."):
            rows = self.confirm_and_ring([BOT, transfer, "Hello, how can I help?"])
            self.assertEqual(rows[1], [])  # the transfer line itself is a silent hold, never routing speech

    def test_N21_restricted_task_also_confirms(self):
        self.confirm_and_ring([BOT, "Please wait while I connect you.", "Hello, how can I help?"], info_only=False)

    def test_N21_a_named_agent_after_a_transfer_rings_directly(self):
        # Round 29 (N24): after a bot label even a named agent after a transfer gets our question first; only a
        # clear yes rings.
        line, req, session, relay, rows = self.run_seq([BOT, "Let me connect you to an agent.",
                                                        "Hello, this is Dana. How can I help?", "Yes, speaking."])
        self.assertTrue(any(QUESTION in t for t in rows[2]), rows)
        line.wait_state(req["call_id"], "ringing_owner")

    @mock.patch.object(CallSession, "_question_reply", _round43c_reply_rule_off)
    def test_N21_question_budget_one_per_20_seconds_four_per_call(self):
        line, req, session, relay, rows = self.run_seq([BOT, "Hello? Are you there?", "Hello? Are you there?",
                                                        "Hello? Are you there?"])
        # the later lines are unclear replies inside 20 s: no re-ask, no second question
        self.assertEqual(sum(QUESTION in t for r in rows for t in r), 1)
        self.assertEqual(rows[2:], [[], []])
        with mock.patch.object(CallSession, "NEW_PARTY_QUESTION_EVERY_S", 0.0), \
                mock.patch.object(CallSession, "NEW_PARTY_QUESTIONS_MAX", 4):
            # each line after the reply window: a fresh possible new party, not a reply to the last question
            # after a transfer (a boundary), distinct unnamed lines are each a possible new party (L2 round 7)
            line, req, session, relay, rows = self.run_seq([BOT, "Let me connect you to an agent."] + NEW_PARTY,
                                                           confirm_reply_window_s=0.1)
            asked = [e for e in line.store.events(req["call_id"]) if e["kind"] == "new_party_question"]
            self.assertEqual(len(asked), 4)
            skipped = [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "new_party_question_skipped"]
            self.assertTrue(skipped and all(s["why"] == "budget" for s in skipped))
            self.assertEqual(rows[-1], [])  # round 39: after the budget, silence (never a phrase on possible_new_party)
            self.assertEqual(self.owner_rung(line), [])  # never rung without a clear yes or a named human


class N22Tests(ContractBase):
    PROMPTS = [("What phone number should we call you back on?", []), ("Would you like to make a payment?", []),
               ("Do you want to change the date?", []), ("Would you like us to call you back?", []),
               ("Can I get your confirmation number?", ["Agent."]), ("Is this about an existing trip?",
                                                                     ["No, a general question."])]

    def test_N22_unsafe_offers_before_data_words(self):
        for mode in (True, False):
            for prompt, expected in self.PROMPTS:
                CONTRACT[prompt] = D("automated_assistant", "high", ("continuing bot",))
                line, req, session, relay, rows = self.run_seq([BOT, prompt], mode)
                self.assertEqual(rows[1], expected, (prompt, mode))
        self.assertIsNone(assistant.choose("What phone number should we call you back on?", ["representative"], None))
        self.assertEqual(assistant.choose("Can I get your confirmation number?", ["representative"], None), "agent")


class NoL1TextRulesTests(unittest.TestCase):
    def test_R27_l1_reads_no_transfer_or_declaration_from_text(self):
        for name in ("_track_assistant",):
            self.assertFalse(hasattr(CallSession, name), name)
        for name in ("declared", "transfer_cue", "personal_intro"):
            self.assertFalse(hasattr(assistant, name), name)


def _l2_round7_on_main() -> bool:
    from plugin.line.detect import HoldTracker, Utterance
    t = HoldTracker()
    t.update(Utterance(BOT, 0, turn_id="a"))
    d = t.update(Utterance("My name is Alex. How can I help you?", 1000, turn_id="b"))
    return d.label == "unknown" and "possible_new_party" in d.evidence


@unittest.skipUnless(_l2_round7_on_main(), "L2 round 7 (possible_new_party) is not on main yet")
class RealDetectorN20Tests(N20Tests):
    real_detector = True


@unittest.skipUnless(_l2_round7_on_main(), "L2 round 7 (possible_new_party) is not on main yet")
class RealDetectorN21Tests(N21Tests):
    real_detector = True

    @mock.patch.object(CallSession, "_question_reply", _round43c_reply_rule_off)
    def test_N21_question_budget_one_per_20_seconds_four_per_call(self):
        # Round 28 (N23): on a call with a bot label, a question back to our pending question is not a person,
        # whatever L2 says; only a clear yes is. The budget itself is tested with the scripted contract above.
        line, req, session, relay, rows = self.run_seq([BOT, "Let me connect you to an agent.",
                                                        "May I have your confirmation number?",
                                                        "Hello, how can I help?", "Yes, I'm a live person."])
        self.assertEqual(sum(QUESTION in t for r in rows for t in r), 1)
        self.assertEqual(rows[3], [])  # the question back: silence
        line.wait_state(req["call_id"], "ringing_owner")  # the clear yes: the owner ring as before


if __name__ == "__main__":
    unittest.main()
