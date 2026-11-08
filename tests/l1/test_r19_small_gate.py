"""Round 19: R1 delta 15 (N16-A, N16-B, N16-C, N17-R, C1). The gate is smaller, not smarter (menu_gate.py).

``conformance_r1d15.py`` is the reviewer's socket-free fixture, copied unchanged; ``MATRIX`` below is its case list.
Every case runs here through the real engine and Relay, in both task modes, with the expectation the coordinator's
decisions give. Where that differs from the reviewer's expectation it is marked: the findings (unsafe sends) are
inverted, and four safe inputs are now refused on purpose (fail closed, coordinator decision)."""
import threading
import time
import unittest

from harness import Line, wait_for
from plugin.line.engine import menu_gate
from plugin.line.engine.task_rules import CHANGE, PAYMENT, restrictions_from
from test_flows import reach_human
from test_r16c_fallback_question import Q, owner_legs

GUIDANCE = "Ask about the bag fee. Decline any callback offer. Do not pay anything. Do not change the booking."
SEND, BLOCK = True, False

# (case, menu, via, value, reviewer expected, information-only now, restricted now)
MATRIX = [
    ("baggage", "For baggage, press 2.", "dtmf", "2", SEND, SEND, SEND),
    ("reservations", "For reservations, press three.", "dtmf", "3", SEND, SEND, SEND),
    ("general info", "Press zero for general information.", "dtmf", "0", SEND, SEND, SEND),
    ("decline", "To decline, press 2.", "dtmf", "2", SEND, BLOCK, BLOCK),  # round 20: not a positive form
    ("unknown", "Press 9 to proceed.", "dtmf", "9", BLOCK, BLOCK, BLOCK),
    ("callback yes", "We can call you back. Say yes to accept.", "speech", "yes", BLOCK, BLOCK, BLOCK),
    ("callback key", "We can call you back. Press 1 to accept, press 2 to keep holding.", "dtmf", "1",
     BLOCK, BLOCK, BLOCK),
    ("callback stay", "We can call you back. Press 1 to accept, press 2 to keep holding.", "dtmf", "2",
     SEND, BLOCK, BLOCK),  # two options and "accept" in its sentence: refused on purpose
    ("callback euphemism", "To keep your place in line and receive a phone call from an agent later, press 1.",
     "dtmf", "1", BLOCK, BLOCK, BLOCK),  # N16-B, inverted
    ("known callback keep place", "We can call you back. To keep your place in line and hang up, press 1.",
     "dtmf", "1", BLOCK, BLOCK, BLOCK),  # N16-B, inverted
    ("callback speech OR", "We can call you back. To keep your place in line and hang up, say agent or press 1.",
     "speech", "agent", BLOCK, BLOCK, BLOCK),  # N16-B, inverted
    ("charge", "To authorize a charge, press one.", "dtmf", "1", BLOCK, BLOCK, BLOCK),
    ("change", "To confirm the change, press 4.", "dtmf", "4", BLOCK, BLOCK, BLOCK),
    ("mixed same clause", "Press 1 for baggage and to authorize a charge.", "dtmf", "1", BLOCK, BLOCK, BLOCK),
    ("mixed comma key", "Press 1 for baggage, and to authorize a charge.", "dtmf", "1",
     BLOCK, BLOCK, BLOCK),  # N16-A, inverted
    ("mixed comma speech", "Say agent for baggage, and to authorize a charge.", "speech", "agent",
     BLOCK, BLOCK, BLOCK),  # N16-A, inverted
    ("mixed repeated key", "For baggage, press 1. To authorize a charge, press 1.", "dtmf", "1", BLOCK, BLOCK, BLOCK),
    ("mixed repeated speech", "For baggage, say agent. To authorize a charge, say agent.", "speech", "agent",
     BLOCK, BLOCK, BLOCK),
    ("unsafe speech OR", "To authorize a charge, say agent or press 1.", "speech", "agent",
     BLOCK, BLOCK, BLOCK),  # N16-A, inverted
    ("unsafe speech then", "To authorize a charge, say agent then wait.", "speech", "agent",
     BLOCK, BLOCK, BLOCK),  # N16-A, inverted
    ("safe star word", "Press star for baggage.", "dtmf", "*", SEND, SEND, SEND),
    ("safe star literal", "For baggage, press *.", "dtmf", "*", SEND, SEND, SEND),  # C1, fixed
    ("unsafe star", "Press star to authorize a charge.", "dtmf", "*", BLOCK, BLOCK, BLOCK),
    ("spoken numeral safe", "For baggage, say one.", "speech", "one", SEND, BLOCK, SEND),  # info-only: words only
    ("spoken numeral unsafe", "To authorize a charge, say one.", "speech", "one", BLOCK, BLOCK, BLOCK),
    ("safe agent", "For an agent, say agent.", "speech", "agent", SEND, SEND, SEND),
    ("no", "We can call you back. To stay on the line, say no.", "speech", "no", SEND, BLOCK, BLOCK),  # round 20
    ("seq 12", "For baggage, press 1. For reservations, press 2.", "dtmf", "12", BLOCK, BLOCK, BLOCK),  # N16-C
    ("seq 1w2", "For baggage, press 1. For reservations, press 2.", "dtmf", "1w2", BLOCK, BLOCK, BLOCK),  # N16-C
    ("seq 11", "For baggage, press 1.", "dtmf", "11", BLOCK, BLOCK, BLOCK),  # N16-C
    ("one paused key", "For baggage, press 1.", "dtmf", "w1w", SEND, SEND, SEND),
]
DELIBERATE = {("decline", True), ("callback stay", True), ("callback stay", False), ("spoken numeral safe", True),
              # round 20 (R1b delta 17): positive forms for every restricted task; any callback offer silences the menu
              ("decline", False), ("no", True), ("no", False)}


class CaseMatrixTests(unittest.TestCase):
    """Each reviewer case, end to end: the menu is the session's current menu, Hermes answers with the case's key
    (the press tool) or phrase (the menu reply), and the Relay shows what really went out."""

    def run_mode(self, info_only, matrix=None, intro_names=None):
        holder = {"phrase": "WAIT"}
        line = Line(reply=lambda driver, text: [holder["phrase"]] if "[phone menu]" in text else ["Okay."])
        self.addCleanup(line.close)
        if info_only:
            req = line.request(mode="owner", title="Bag fee", goal="Ask about the bag fee.", fallback_question=Q,
                               intro_names=intro_names)
        else:
            req = line.request(goal=GUIDANCE, intro_names=intro_names)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        relay = line.connect(req, created)
        wait_for(lambda: session.setup_seen)
        self.assertEqual(session.info_only, info_only)
        self.assertTrue(session.restrictions)
        outcomes = {}
        for name, menu, via, value, reviewer, info_now, restricted_now in matrix or MATRIX:
            expected = info_now if info_only else restricted_now
            session.last_menu, session.last_detection = menu, None
            session.menu_seq += 1  # a new menu prompt
            session.digits_gen = -1
            seen = len(relay.frames)
            blocked_before = len([e for e in line.store.events(req["call_id"]) if e["kind"] == "restricted_action_blocked"])
            if via == "dtmf":
                result = line.svc.run(session.press_digits(value))
                self.assertEqual(result["ok"], expected, (name, info_only, result))
            else:
                holder["phrase"] = value

                async def menu_turn():
                    await session.start_turn(f"[phone menu] {menu}", spoken=False)
                line.svc.run(menu_turn())
            if expected:
                wait_for(lambda: any((f.get("digits") == value) if via == "dtmf" else (f.get("token") == value)
                                     for f in relay.frames[seen:]), what=f"{name} sent")
            else:
                wait_for(lambda: len([e for e in line.store.events(req["call_id"])
                                      if e["kind"] == "restricted_action_blocked"]) > blocked_before,
                         what=f"{name} blocked")
                time.sleep(0.05)
                sent = [f for f in relay.frames[seen:] if f.get("type") in ("sendDigits", "text")]
                self.assertEqual(sent, [], (name, info_only))
            outcomes[name] = expected
            if (name, info_only) not in DELIBERATE:
                self.assertEqual(expected, reviewer, name)  # elsewhere the reviewer's expectation holds
        return outcomes

    def test_R19_matrix_information_only(self):
        self.run_mode(True)

    def test_R19_matrix_restricted(self):
        self.run_mode(False)

    def test_R19_matrix_differs_from_the_reviewer_only_where_decided(self):
        for name, menu, via, value, reviewer, info_now, restricted_now in MATRIX:
            for info_only, now in ((True, info_now), (False, restricted_now)):
                if now != reviewer:
                    self.assertIn((name, info_only), DELIBERATE, name)
                    self.assertTrue(reviewer and not now, "a deviation may only refuse, never send")


class QueuedTurnTests(unittest.TestCase):
    """N17-R: menu turn A in flight, menu turn B queued behind it, then a person answers."""

    def run_case(self, accept):
        release, entered, calls, results = threading.Event(), threading.Event(), [], []

        def reply(driver, text):
            if "[phone menu]" in text:
                calls.append(text)
                if len(calls) == 1:
                    entered.set()
                    release.wait(8)
                results.append(driver.tool("press_digits", "2"))
                return ["agent"]
            return ["WAIT"]
        line = Line(reply=reply)
        self.addCleanup(line.close)
        self.addCleanup(release.set)
        req = line.request(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[0])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt("For baggage, press 2.")
        self.assertTrue(entered.wait(3))
        relay.prompt("For reservations, press 3. For baggage, press 2.")  # turn B, queued behind A
        wait_for(lambda: session.turn_task is not None and session.current_turn is not None
                 and session.turn_task is not session.current_turn.task, what="turn B queued")
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        if accept:
            leg = owner_legs(line)[0]
            line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": leg["sid"], "Digits": "1"})
            line.wait_state(req["call_id"], "owner_connected")
        release.set()
        wait_for(lambda: [e for e in line.store.events(req["call_id"])
                          if e["kind"] == "stale_turn_dropped" and e["data"].get("queued")] or accept,
                 what="queued turn B dropped")
        time.sleep(0.4)
        self.assertEqual(len(calls), 1, "turn B never reached Hermes")
        self.assertTrue(all(not r["ok"] for r in results))
        self.assertEqual(relay.of_type("sendDigits"), [])
        self.assertNotIn("agent", [t.strip().lower() for t in relay.texts()])

    def test_R19_queued_turn_B_never_speaks_while_the_owner_is_rung(self):
        self.run_case(accept=False)

    def test_R19_queued_turn_B_never_speaks_after_acceptance(self):
        self.run_case(accept=True)


class SmallGateRuleTests(unittest.TestCase):
    def keys(self, digits, menu, info_only, detector=False):
        return menu_gate.check_keys(digits, menu, menu_gate.is_callback_offer(menu, detector), info_only=info_only)

    def speech(self, phrase, menu, info_only, detector=False):
        return menu_gate.check_speech(phrase, menu, menu_gate.is_callback_offer(menu, detector),
                                      info_only=info_only)

    def test_R19_every_blocked_word_blocks_the_sentence(self):
        words = ["call", "phone call", "call back", "callback", "later", "place in line", "hang up", "text", "charge",
                 "pay", "payment", "card", "authorize", "purchase", "upgrade", "fee", "change", "rebook", "cancel",
                 "modify", "confirm", "accept", "yes", "proceed", "continue"]
        for word in words:
            for mode in (True, False):
                self.assertIsNotNone(self.keys("2", f"For baggage {word}, press 2.", mode), word)
        self.assertIsNone(self.keys("2", "For baggage, press 2.", True))

    def test_R19_a_second_clause_blocks_the_sentence(self):
        for joined in ("For baggage and lost items, press 2.", "For baggage or reservations, press 2.",
                       "For baggage, press 2, then wait."):
            self.assertIsNotNone(self.keys("2", joined, False), joined)

    def test_R19_information_only_says_only_agent_or_representative(self):
        for word in ("agent", "Representative."):
            self.assertIsNone(self.speech(word, None, True), word)
        for word in ("yes", "reservations", "baggage", "operator", "one", "my name is Dana"):
            self.assertIsNotNone(self.speech(word, None, True), word)
        # Round 20: any callback offer silences the whole menu, the staying "no" included (coordinator decision).
        for menu in ("For baggage, press 2.", "We can call you back. To stay on the line, say no."):
            self.assertIsNotNone(self.speech("no", menu, True), menu)
        self.assertIsNotNone(self.speech("no", "To remain on the line, say no.", True, detector=True))

    def test_R19_never_yes_even_when_tied_to_a_safe_option(self):
        for mode in (True, False):
            self.assertIsNotNone(self.speech("yes", "For baggage, say yes.", mode))

    def test_R19_sequences_only_when_announced_whole_and_never_for_information_only(self):
        menu = "For reservations, dial 123."  # round 20: the sequence's option must be a positive form too
        self.assertIsNone(self.keys("123", menu, False))
        self.assertIsNone(self.keys("1w2w3", menu, False))
        self.assertIsNotNone(self.keys("12", menu, False))
        self.assertIsNotNone(self.keys("123", menu, True))
        self.assertIsNotNone(self.keys("12", "For baggage, press 1. For reservations, press 2.", False))

    def test_R19_literal_star_and_pound(self):
        self.assertIsNone(self.keys("*", "For baggage, press *.", True))
        self.assertIsNone(self.keys("#", "For an agent, press #.", True))
        self.assertIsNotNone(self.keys("*", "To authorize a charge, press *.", True))

    def test_R19_information_only_positive_list(self):
        for ok in ("For reservations, press 1.", "For baggage, press 1.", "For general information, press 1.",
                   "To speak to an agent, press 1.", "For a representative, press 1."):
            self.assertIsNone(self.keys("1", ok, True), ok)
        for other in ("For flight status, press 1.", "To decline, press 1.", "To stay on the line, press 1."):
            self.assertIsNotNone(self.keys("1", other, True), other)
            self.assertIsNotNone(self.keys("1", other, False), other)  # round 20: positive forms for both modes

    def test_R19_one_key_per_menu(self):
        line = Line()
        self.addCleanup(line.close)
        req, created = line.dial(goal=GUIDANCE)
        session = line.session(req["call_id"])
        session.last_menu, session.menu_seq = "For baggage, press 2. For reservations, press 3.", 5
        self.assertTrue(line.svc.run(session.press_digits("2"))["ok"])
        session.digits_gen = -1
        second = line.svc.run(session.press_digits("3"))
        self.assertFalse(second["ok"])
        self.assertIn("already sent for this menu", second["error"])
        session.menu_seq += 1  # the next menu prompt
        session.digits_gen = -1
        self.assertTrue(line.svc.run(session.press_digits("3"))["ok"])

    def test_R19_guidance_reads_the_wider_wording(self):
        self.assertEqual(restrictions_from("Do not authorize anything. Never rebook. Don't cancel."),
                         frozenset({PAYMENT, CHANGE}))
        self.assertEqual(restrictions_from("Do not purchase an upgrade."), frozenset({PAYMENT}))

    def test_R19_an_unrestricted_task_is_not_gated(self):
        line = Line()
        self.addCleanup(line.close)
        req, created = line.dial(goal="Book a table for four.")
        session = line.session(req["call_id"])
        self.assertEqual(session.restrictions, frozenset())
        self.assertIsNone(session._menu_output_refused("dtmf", "1234"))


if __name__ == "__main__":
    unittest.main()
