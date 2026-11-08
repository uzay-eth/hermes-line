"""Round 16 additions (a) and (b), from live calls 4 and 5 (evidence/trial/call_94909a575a8941db).

(a) A call forwarded by the owner's carrier line opened with the carrier's screening prompt; Hermes pressed 1 itself
after 13 s, after its greeting had been spoken over the prompt. The line now answers that prompt at once and greets
afterwards. (b) Hermes offered to "leave a note" four times in one call; it now offers at most once unless asked."""
import time
import unittest
from unittest import mock

from harness import LINE, OWNER, Line, wait_for
from plugin.line.engine import screening

CALLER = "+17755550177"
FORWARDING = "+17755550188"
FRAME1 = "In case of a fast pickup, we ask you to let us know that you are not a voice mail system."
FRAME2 = "So you need to press 1 to take this call."


def turns(line):
    return [t for d in line.drivers for t in d.turns]


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(screening, "GREET_AFTER_PRESS_S", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, **over):
        line = Line(**over)
        self.addCleanup(line.close)
        return line

    def call_in(self, line, sid="CA" + "3" * 32):
        status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": CALLER, "To": LINE})
        call = wait_for(lambda: [c for c in line.store.list_calls() if c.provider_sid == sid])[0]
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})
        session = wait_for(lambda: line.session(call.id))
        return body, call, relay, session

    def test_R16a_screening_prompt_is_answered_at_once_then_the_caller_is_greeted(self):
        line = self.make(forwarding_number=FORWARDING)
        body, call, relay, session = self.call_in(line)
        self.assertNotIn("welcomeGreeting", body)  # held back while a screening prompt may play
        relay.prompt(FRAME1)
        relay.wait_text(session.greeting)
        self.assertEqual([f["digits"] for f in relay.of_type("sendDigits")], ["1"])
        # Nothing was said over the prompt. Proven by order and by the engine's own times, not by reading the relay
        # at some moment after the key: the client reader can lag and get both frames in one burst (round 18 fix).
        with relay._lock:
            sent = [f["type"] for f in relay.frames if f["type"] in ("sendDigits", "text")]
        self.assertEqual(sent[0], "sendDigits")  # one ordered socket: arrival order is send order
        events = {e["kind"]: e for e in line.store.events(call.id)
                  if e["kind"] in ("carrier_screening_answered", "greeting")}
        self.assertEqual(events["greeting"]["data"]["why"], "after screening")
        self.assertGreaterEqual(events["greeting"]["ts"] - events["carrier_screening_answered"]["ts"],
                                screening.GREET_AFTER_PRESS_S * 0.8)
        relay.prompt(FRAME2)  # the rest of the prompt: no second key, no Hermes turn
        time.sleep(0.3)
        self.assertEqual(len(relay.of_type("sendDigits")), 1)
        self.assertEqual(turns(line), [])
        kinds = [e["kind"] for e in line.store.events(call.id)]
        self.assertIn("carrier_screening_answered", kinds)
        self.assertIn("carrier_screening_repeat", kinds)
        said = [t["speaker"] + ":" + t["text"] for t in line.store.transcript(call.id)]
        self.assertEqual(said[:3], ["business:" + FRAME1, "system:Pressed 1", "hermes:" + session.greeting])
        relay.prompt("Hi, this is Dana, can you take a message for Sam?")
        wait_for(lambda: turns(line), what="first real turn")

    def test_R16a_a_caller_who_speaks_first_is_greeted_at_once(self):
        line = self.make(forwarding_number=FORWARDING)
        body, call, relay, session = self.call_in(line)
        relay.prompt("Hello?")
        relay.wait_text(session.greeting)
        time.sleep(0.2)
        self.assertEqual(turns(line), [])  # the greeting answers a bare hello
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R16a_no_prompt_means_the_greeting_after_the_hold(self):
        line = self.make(forwarding_number=FORWARDING, inbound_screening_hold_s=0.2)
        body, call, relay, session = self.call_in(line)
        relay.wait_text(session.greeting)
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R16a_without_forwarding_the_greeting_plays_and_is_repeated_after_the_key(self):
        line = self.make()
        body, call, relay, session = self.call_in(line)
        self.assertIn("welcomeGreeting", body)  # unchanged for a line with no forwarding
        relay.prompt(FRAME2)
        wait_for(lambda: relay.of_type("sendDigits"), what="key 1")
        relay.wait_text(session.greeting)  # the caller was not connected for the first one

    def test_R16a_narrow_only_the_prompt_family_and_the_first_seconds(self):
        line = self.make(forwarding_number=FORWARDING, inbound_screening_hold_s=0.1)
        body, call, relay, session = self.call_in(line)
        relay.wait_text(session.greeting)
        relay.prompt("Please press 1 for sales.")  # not the screening family
        wait_for(lambda: turns(line), what="ordinary turn")
        with mock.patch.object(screening, "SCREENING_WINDOW_S", 0.0):  # after the window
            relay.prompt(FRAME2)
            wait_for(lambda: len(turns(line)) == 2, what="second ordinary turn")
        self.assertEqual(relay.of_type("sendDigits"), [])
        for text in ("Please press 1 for sales.", "I am not a voicemail", "Press 2 to take this call."):
            self.assertFalse(screening.is_carrier_screening(text), text)

    def test_R16a_outbound_calls_never_treat_speech_as_screening(self):
        line = self.make(forwarding_number=FORWARDING)
        req, created = line.dial()
        relay = line.connect(req, created)
        relay.prompt(FRAME2)
        time.sleep(0.3)
        self.assertEqual(relay.of_type("sendDigits"), [])
        self.assertNotIn("carrier_screening_answered", [e["kind"] for e in line.store.events(req["call_id"])])


def note_agent(driver, text):
    """The live call's pattern: the offer, then the same offer again and again."""
    if "usage limit" in text:
        return ["Did you mean your usage limit? ", "I can't check that from this call, ",
                "but I can leave a note so you get the answer in chat."]
    if "[business] Yeah" in text:
        return ["Got it. I can't see your usage from this call, ", "but I can leave a note so it gets checked. ",
                "Should I do that?"]
    if "Why not" in text:
        return ["I can leave a note so the answer shows up in your chat. ", "Want me to?"]
    if "take a note" in text:
        return ["Sure, I can take a note. ", "What should it say?"]
    return ["Okay."]


class NoteOfferTests(unittest.TestCase):
    def test_R16b_a_note_is_offered_once_unless_the_caller_asks(self):
        line = Line(reply=note_agent)
        self.addCleanup(line.close)
        sid = "CA" + "4" * 32
        status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": CALLER, "To": LINE})
        call = wait_for(lambda: [c for c in line.store.list_calls() if c.provider_sid == sid])[0]
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})

        def hermes():
            return [t["text"] for t in line.store.transcript(call.id) if t["speaker"] == "hermes"]
        relay.prompt("Can you read my usage limit right now?")
        relay.wait_text("but I can leave a note so you get the answer in chat.")  # the first offer is said
        first = len(relay.texts())
        self.assertIn("I can't check that from this call, ", relay.texts())  # streamed as before, not held
        relay.prompt("Yeah.")
        wait_for(lambda: len(hermes()) == 3, what="second reply")
        self.assertEqual(hermes()[-1], "Got it.")  # the repeated offer and its "Should I do that?" are not said
        self.assertNotIn("leave a note so it gets checked", relay.spoken())
        self.assertIn("You already offered to take a note", line.drivers[-1].turns[-1])
        relay.prompt("Why not?")
        wait_for(lambda: len(hermes()) == 4, what="third reply")
        self.assertEqual(hermes()[-1], "Okay.")  # everything was a repeat offer: never silence
        self.assertEqual(relay.spoken().count("leave a note"), 1)
        relay.prompt("Actually, can you take a note for me?")  # the caller asks: allowed
        relay.wait_text("Sure, I can take a note. ")
        kinds = [e["kind"] for e in line.store.events(call.id)]
        self.assertEqual(kinds.count("note_offered"), 1)
        self.assertEqual(kinds.count("note_offer_dropped"), 2)
        self.assertGreater(len(relay.texts()), first)

    def test_R16b_the_briefing_states_the_rule(self):
        line = Line()
        self.addCleanup(line.close)
        from plugin.line.engine.contracts import Direction
        self.assertIn("Offer to take a note at most once", line.svc.briefing(None, Direction.INBOUND))


if __name__ == "__main__":
    unittest.main()
