"""Round 13: owner line mode. The owner calls the line, passes the PIN, and has Hermes call a business."""
import json
import time
import unittest

from harness import BUSINESS, LINE, OWNER, SECRETS, Line, wait_for
from plugin.line.engine.owner_line import pin_matches, reply_is_no, reply_is_yes, spoken_digits, spoken_number

PIN = "4821"
STRANGER = "+17755550177"


def agent(driver, text):
    """Scripted Hermes: asks the line to call a business when the verified owner says so."""
    if "call Sample Bistro" in text:
        out = driver.tool("owner_new_task", BUSINESS, "Table at Sample Bistro", "Book a table for 4 at 7", "")
        driver.last_tool = out
        return ["Sure."] if out.get("ok") else ["I can't do that on this call."]
    if "call the clinic" in text:
        out = driver.tool("owner_new_task", OWNER, "Clinic appointment", "Book a check-up", "")
        driver.last_tool = out
        return ["Sure."]
    return ["Okay."]


class OwnerLineBase(unittest.TestCase):
    def make(self, pin=PIN):
        secrets = dict(SECRETS)
        if pin is not None:
            secrets["HERMES_LINE_OWNER_PIN"] = pin
        line = Line(owner_name="Can", secrets=secrets, reply=agent)
        self.addCleanup(line.close)
        return line

    def call_in(self, line, caller=OWNER, sid="CA" + "1" * 32):
        status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": caller, "To": LINE})
        call = wait_for(lambda: [c for c in line.store.list_calls() if c.provider_sid == sid])[0]
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})
        session = wait_for(lambda: line.session(call.id))
        return body, call, relay, session

    def everything(self, line, call_id):
        """Every place a PIN must never appear: transcript text, event kinds and data, Hermes's inputs, the summary.
        Timestamps and generated ids are left out: a float timestamp such as 1793917101.2794821 can contain the test
        PIN's digits by chance (a false positive seen in the round-21 full run)."""
        def scrub_ids(value):
            if isinstance(value, dict):
                return {k: scrub_ids(v) for k, v in value.items() if k not in ("ts", "call_id", "task_id")
                        and not k.endswith("_call_id")}
            if isinstance(value, list):
                return [scrub_ids(v) for v in value]
            return value
        drivers = [{"turns": d.turns, "notes": d.notes, "asks": d.asks, "briefing": d.briefing} for d in line.drivers]
        transcript = [{"speaker": t["speaker"], "text": t["text"]} for t in line.store.transcript(call_id, limit=500)]
        events = [{"kind": e["kind"], "data": scrub_ids(e["data"])} for e in line.store.events()]
        return json.dumps([transcript, events, drivers, line.store.get_call(call_id).summary])

    def verify(self, line, relay, session, by="dtmf"):
        for key in PIN + "#":  # keypad only (R1 delta 10 N14)
            relay.send({"type": "dtmf", "digit": key})
        wait_for(lambda: session.owner_verified, what="owner verified")
        relay.wait_text("Thanks, Can. How can I help?")


class PinTests(OwnerLineBase):
    def test_R13_owner_number_is_offered_the_pin_and_verifies_by_keypad(self):
        line = self.make()
        body, call, relay, session = self.call_in(line)
        self.assertIn("If you are Can, key your PIN, then press pound", body)
        self.assertTrue(session.pin_phase)
        self.verify(line, relay, session, by="dtmf")
        self.assertIn("owner_verified", [e["kind"] for e in line.store.events(call.id)])
        wait_for(lambda: any("verified with the PIN" in n for d in line.drivers for n in d.notes), what="owner note")
        self.assertNotIn(PIN, self.everything(line, call.id))
        self.assertNotIn(" ".join(PIN), self.everything(line, call.id))

    def test_N14_a_spoken_pin_never_verifies_and_is_scrubbed(self):
        """R1 delta 10 N14 (probe_owner.py, second part, inverted): keypad only; speech carrying the PIN is scrubbed."""
        line = self.make()
        body, call, relay, session = self.call_in(line)
        relay.prompt("Hello")
        relay.wait_text("Okay.")
        self.assertTrue(session.pin_phase)  # a greeting does not leave secure entry
        relay.prompt("four eight two one")
        wait_for(lambda: len(line.drivers[-1].turns) >= 2, what="second utterance handled")
        self.assertFalse(session.owner_verified)
        for form in ("my pin is 4 8 2 1", "it's 4-8-2-1", "four eight two", "48 21"):  # repeats, partials, formats
            relay.prompt(form)
        wait_for(lambda: len(line.drivers[-1].turns) >= 6, what="all handled")
        blob = self.everything(line, call.id)
        for leak in (PIN, "four eight two one", "four eight two", "4 8 2 1", "4-8-2-1", "48 21"):
            self.assertNotIn(leak, blob)
        self.assertIn("[PIN]", blob)
        self.verify(line, relay, session, by="dtmf")  # the keypad still works afterwards
        relay.prompt("Just to check, my PIN is four eight two one.")  # repeated after verification
        wait_for(lambda: any("my PIN is [PIN]" in t for t in line.drivers[-1].turns), what="scrubbed repeat")
        self.assertNotIn("four eight two one", self.everything(line, call.id))

    def test_R13_three_wrong_pins_end_the_call_without_a_hint(self):
        line = self.make()
        body, call, relay, session = self.call_in(line)
        for attempt, wrong in enumerate(("1111", "4822", "48219999"), start=1):
            for key in wrong + ("" if len(wrong) == 8 else "#"):
                relay.send({"type": "dtmf", "digit": key})
            wait_for(lambda: session.pin_failures == attempt, what=f"attempt {attempt}")
        relay.wait_text("Sorry, I can't verify that. Goodbye.")
        said = relay.spoken()
        self.assertEqual(said.count("Sorry, that didn't work. Please try again."), 2)
        for hint in ("digit", "length", "too short", "too long", "almost", "first"):
            self.assertNotIn(hint, said.lower().replace("didn't work", ""))
        line.wait_state(call.id, "ended", timeout=10)
        kinds = [e["kind"] for e in line.store.events(call.id)]
        self.assertEqual(kinds.count("owner_pin_failed"), 3)
        self.assertIn("owner_pin_locked", kinds)
        self.assertNotIn("owner_verified", kinds)
        blob = self.everything(line, call.id)
        for wrong in ("1111", "4822", "48219999", PIN):
            self.assertNotIn(wrong, blob)
        self.assertEqual(line.drivers[-1].turns, [])

    def test_R13_owner_number_without_the_pin_is_an_unknown_caller(self):
        line = self.make()
        body, call, relay, session = self.call_in(line)
        relay.prompt("Hi, this is Dana, can you take a message for Can?")
        relay.wait_text("Okay.")
        self.assertFalse(session.owner_verified)
        out = line.svc.in_call(session.agent_session_id, "owner_new_task", BUSINESS, "x", "y", "")
        self.assertFalse(out["ok"])
        self.assertIn("after their PIN", out["error"])
        self.assertIn("take a short message", line.drivers[-1].briefing)
        self.assertNotIn("most likely Can", line.drivers[-1].briefing)  # caller ID alone is not the owner here

    def test_R13_a_stranger_cannot_reach_owner_mode(self):
        line = self.make()
        body, call, relay, session = self.call_in(line, caller=STRANGER)
        self.assertNotIn("PIN", body)
        self.assertFalse(session.pin_phase)
        relay.prompt("four eight two one")
        relay.wait_text("Okay.")
        self.assertFalse(session.owner_verified)
        for d in "4821#":
            relay.send({"type": "dtmf", "digit": d})
        time.sleep(0.3)
        self.assertFalse(session.owner_verified)
        out = line.svc.in_call(session.agent_session_id, "owner_new_task", BUSINESS, "x", "y", "")
        self.assertFalse(out["ok"])
        self.assertNotIn("owner_verified", [e["kind"] for e in line.store.events(call.id)])

    def test_R13_no_pin_configured_means_no_pin_step(self):
        line = self.make(pin=None)
        body, call, relay, session = self.call_in(line)
        self.assertNotIn("PIN", body)
        self.assertFalse(session.pin_phase)
        for bad in ("123", "123456789", "12a4", ""):
            line2 = Line(owner_name="Can", secrets={**SECRETS, "HERMES_LINE_OWNER_PIN": bad})
            try:
                self.assertIsNone(line2.svc.owner_pin())
            finally:
                line2.close()


def key(relay, digits):
    for d in digits:
        relay.send({"type": "dtmf", "digit": d})


class OwnerRequestTests(OwnerLineBase):
    """Round 13 requests, with the R1 delta 11 N12 keypad confirmation (speech never approves)."""

    def _verified(self, **kw):
        line = Line(owner_name="Can", secrets={**SECRETS, "HERMES_LINE_OWNER_PIN": PIN}, reply=agent, **kw)
        self.addCleanup(line.close)
        body, call, relay, session = self.call_in(line)
        self.verify(line, relay, session)
        return line, call, relay, session

    def _hang_up(self, line, call):
        line.svc.submit("hang_up", source="slash", call_id=call.id)
        wait_for(lambda: call.id not in line.svc.sessions, what="owner call finished")

    def _propose(self, relay, session, text="Please call Sample Bistro for me."):
        before = len(relay.frames)
        relay.prompt(text)
        wait_for(lambda: "digits of the number, then pound" in
                 "".join(f.get("token", "") for f in relay.frames[before:]), what="read-back with the challenge")
        return session.proposal

    def test_R13_keyed_last_four_approve_only_the_read_back_destination(self):
        line, call, relay, session = self._verified()
        proposal = self._propose(relay, session)
        relay.wait_text(f"I'll call {spoken_number(BUSINESS)} about Table at Sample Bistro")
        self.assertEqual(proposal["code"], BUSINESS[-7:])  # a new destination: seven digits (R1 delta 12)
        key(relay, proposal["code"] + "#")
        relay.wait_text("Confirmed. I'll call as soon as you hang up")
        self.assertIsNone(session.proposal)
        self.assertEqual(line.adapter.created, [])  # one call at a time: dialled after this call ends
        self._hang_up(line, call)
        created = wait_for(lambda: line.adapter.created, timeout=10, what="keypad-approved dial")
        self.assertEqual([c["to"] for c in created], [BUSINESS])
        self.assertEqual(line.svc._voice_tickets, [])
        out = line.svc.submit("approve", source="owner-voice", task_id=proposal["task_id"],
                              payload={"destination": BUSINESS})
        self.assertFalse(out["result"]["ok"])  # one approval, used

    def test_R13_speech_never_approves_and_wrong_digits_cancel(self):
        line, call, relay, session = self._verified()
        proposal = self._propose(relay, session)
        for said in ("Yes.", "Yes, go ahead.", "Confirmed, call them."):
            relay.prompt(said)
        wait_for(lambda: len(line.drivers[-1].turns) >= 4, what="all heard as turns")
        self.assertEqual(line.svc._voice_tickets, [])
        self.assertIsNotNone(session.proposal)  # speech neither approves nor cancels
        key(relay, "9999#")
        relay.wait_text("Those digits don't match, so I won't make that call")
        self.assertIsNone(session.proposal)
        key(relay, proposal["code"] + "#")  # too late: the confirmation is gone
        time.sleep(0.3)
        self.assertEqual(line.svc._voice_tickets, [])
        self._hang_up(line, call)
        time.sleep(0.3)
        self.assertEqual(line.adapter.created, [])
        self.assertEqual(line.store.get_task(proposal["task_id"]).status.value, "awaiting_approval")
        out = line.approve(proposal["task_id"])  # chat approval still works
        self.assertTrue(out["result"]["ok"], out)
        self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS])

    def test_R13_a_second_task_needs_its_own_confirmation(self):
        line, call, relay, session = self._verified()
        first = self._propose(relay, session)
        key(relay, first["code"] + "#")
        relay.wait_text("Confirmed.")
        second = self._propose(relay, session, "And call the clinic too.")
        self.assertNotEqual(first["task_id"], second["task_id"])
        self._hang_up(line, call)  # no digits for the second task
        wait_for(lambda: line.adapter.created, timeout=10, what="first task dialled")
        time.sleep(0.5)
        self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS])
        self.assertEqual(line.store.get_task(second["task_id"]).status.value, "awaiting_approval")

    def test_R13_a_newer_request_replaces_an_unanswered_one(self):
        line, call, relay, session = self._verified()
        first = self._propose(relay, session)
        second = self._propose(relay, session, "Actually, call the clinic instead.")
        key(relay, first["code"] + "#")  # the first request's digits no longer count
        relay.wait_text("Those digits don't match")
        self.assertEqual(line.svc._voice_tickets, [])
        self.assertEqual(line.store.get_task(first["task_id"]).status.value, "awaiting_approval")


class SummaryRouteTests(unittest.TestCase):
    def test_R13_a_phone_requested_task_summary_goes_to_the_owners_chat(self):
        import plugin
        from test_otp_and_plugin import FakeCtx
        posted = []

        class Ctx(FakeCtx):
            def post_message(self, text, **kw):
                posted.append(text)
                return type("H", (), {"ok": True})()

        ctx = Ctx({"sms_source": "none"})
        plugin.register(ctx)
        host = ctx.unloads[0].__self__
        line = Line(start=False)
        try:
            req = line.request()  # no chat origin, like a task the owner asked for by phone
            task = line.store.get_task(req["task_id"])
            ok, err = host.deliver(task, {"text": "Hermes Line: Call completed (Sample Bistro)."})
            self.assertTrue(ok, err)
            self.assertEqual(posted, ["[Hermes Line call summary] Hermes Line: Call completed (Sample Bistro)."])
        finally:
            line.close()


class HelperTests(unittest.TestCase):
    def test_R13_spoken_digits_yes_no_and_pin_compare(self):
        from plugin.line.engine.owner_line import scrub_pin
        self.assertEqual(scrub_pin("my pin is four eight two one ok", PIN), "my pin is [PIN] ok")
        self.assertEqual(scrub_pin("call 7 7 5 5 5 5 0 1 9 9", PIN), "call 7 7 5 5 5 5 0 1 9 9")  # a phone number
        self.assertEqual(scrub_pin("I'm calling for Dana at 4", PIN), "I'm calling for Dana at 4")
        self.assertEqual(scrub_pin("it is 4821.", PIN), "it is [PIN].")
        self.assertEqual(spoken_digits("four eight two one"), "4821")
        self.assertEqual(spoken_digits("my pin is 4 8 2 1"), "4821")
        self.assertEqual(spoken_digits("It's 4821."), "4821")
        self.assertEqual(spoken_digits("I'm calling for Dana"), "")
        self.assertEqual(spoken_digits("Hi, this is Dana"), "")
        self.assertTrue(pin_matches("4821", "4821"))
        self.assertFalse(pin_matches("482", "4821"))
        for yes in ("Yes.", "yeah", "Yes, go ahead.", "okay", "Sure, thanks"):
            self.assertTrue(reply_is_yes(yes), yes)
        for not_yes in ("Yes but call a different number", "yes, call 555 0100 instead", "what?", "no", "maybe"):
            self.assertFalse(reply_is_yes(not_yes), not_yes)
        self.assertTrue(reply_is_no("No, don't."))
        self.assertEqual(spoken_number("+17755550199"), "7 7 5, 5 5 5, 0 1 9 9")


if __name__ == "__main__":
    unittest.main()


class N15PausePinTests(OwnerLineBase):
    """R1 delta 10 N15 (probe_owner.py, third part, inverted): pause-submitted failures still end the call."""

    def test_N15_three_pause_triggered_failures_hang_up(self):
        import asyncio
        line = self.make()
        body, call, relay, session = self.call_in(line)

        async def attempts():
            for _ in range(3):
                session._pin_keys = list("9999")
                session._pin_timer = session._spawn(session._pin_pause(0.01))
                await asyncio.sleep(0.05)
        line.svc.run(attempts())
        self.assertEqual(session.pin_failures, 3)
        line.wait_state(call.id, "ended", timeout=10)
        self.assertIn("owner_pin_locked", [e["kind"] for e in line.store.events(call.id)])

    def test_N15_pause_branch_submits_keyed_digits(self):
        import plugin.line.engine.owner_line as owner_line
        line = self.make()
        body, call, relay, session = self.call_in(line)
        saved, owner_line.DTMF_PAUSE_S = owner_line.DTMF_PAUSE_S, 0.2
        try:
            for key in PIN:  # no pound: the pause submits it
                relay.send({"type": "dtmf", "digit": key})
            wait_for(lambda: session.owner_verified, timeout=5, what="verified after the pause")
        finally:
            owner_line.DTMF_PAUSE_S = saved


class N13PinBudgetTests(OwnerLineBase):
    """R1 delta 10 N13 (probe_owner.py, first part, inverted): the PIN budget is per line, across calls and restarts."""

    def make_at(self, clock, store_dir=None):
        line = Line(owner_name="Can", secrets={**SECRETS, "HERMES_LINE_OWNER_PIN": PIN}, reply=agent,
                    store_dir=store_dir)
        line.svc.clock = lambda: clock[0]
        return line

    def _fail(self, line, session, n, wrong="9999"):
        for _ in range(n):
            before = session.pin_failures
            for key in wrong + "#":
                session.offer({"type": "dtmf", "digit": key})
            wait_for(lambda: session.pin_failures == before + 1 or session.terminal, what="failure counted")

    def _call(self, line, sid):
        body, call, relay, session = self.call_in(line, sid=sid)
        return body, call, relay, session

    def _end(self, line, call):
        if call.id in line.svc.sessions:
            line.svc.submit("hang_up", source="slash", call_id=call.id)
        wait_for(lambda: call.id not in line.svc.sessions, what="call finished")

    def test_N13_redial_after_three_failures_is_locked_for_15_minutes(self):
        clock = [1_800_000_000.0]
        line = self.make_at(clock)
        self.addCleanup(line.close)
        body, call, relay, session = self._call(line, "CA" + "1" * 32)
        self._fail(line, session, 3)
        line.wait_state(call.id, "ended", timeout=10)
        self._end(line, call)
        body2, call2, relay2, session2 = self._call(line, "CA" + "2" * 32)  # the probe's redial
        self.assertNotIn("PIN", body2)
        self.assertFalse(session2.pin_phase)
        for key in PIN + "#":
            relay2.send({"type": "dtmf", "digit": key})
        time.sleep(0.4)
        self.assertFalse(session2.owner_verified)  # a correct PIN on a locked line verifies nobody
        self._end(line, call2)
        notices = [t for _, t in line.delivered if "locked for 15 minutes" in t]
        wait_for(lambda: [t for _, t in line.delivered if "locked for 15 minutes" in t], what="one lockout notice")
        self.assertEqual(len([t for _, t in line.delivered if "locked" in t]), 1)
        clock[0] += 16 * 60
        body3, call3, relay3, session3 = self._call(line, "CA" + "3" * 32)
        self.assertTrue(session3.pin_phase)
        self.verify(line, relay3, session3)

    def test_N13_the_lock_survives_a_restart(self):
        import tempfile
        from pathlib import Path
        clock = [1_800_000_000.0]
        tmp = Path(tempfile.mkdtemp(prefix="hl-n13-"))
        line = self.make_at(clock, store_dir=tmp)
        try:
            body, call, relay, session = self._call(line, "CA" + "4" * 32)
            self._fail(line, session, 3)
            line.wait_state(call.id, "ended", timeout=10)
        finally:
            line.close()
        again = self.make_at(clock, store_dir=tmp)
        self.addCleanup(again.close)
        body, call, relay, session = self._call(again, "CA" + "5" * 32)
        self.assertFalse(session.pin_phase)
        self.assertEqual(again.svc.owner_mode_lock(), "cooldown")

    def test_N13_ten_failures_in_a_day_lock_until_the_owner_unlocks_from_chat(self):
        import plugin
        from test_otp_and_plugin import FakeCtx
        clock = [1_800_000_000.0]
        line = self.make_at(clock)
        self.addCleanup(line.close)
        ctx = FakeCtx({"sms_source": "none"})
        plugin.register(ctx)
        ctx.unloads[0].__self__.service = line.svc
        for i in range(4):  # 3 + 3 + 3 + 1 = 10 failures, each streak followed by the cooldown passing
            body, call, relay, session = self._call(line, f"CA{i:032d}")
            self._fail(line, session, 3 if i < 3 else 1)
            self._end(line, call)
            if i < 3:
                self.assertEqual(line.svc.owner_mode_lock(), "cooldown")
                clock[0] += 16 * 60
        self.assertEqual(line.svc.owner_mode_lock(), "hard")
        clock[0] += 6 * 3600
        self.assertEqual(line.svc.owner_mode_lock(), "hard")  # time alone does not lift it
        wait_for(lambda: any("/line unlock-owner" in t for _, t in line.delivered), what="hard-lock notice")
        self.assertEqual(len([t for _, t in line.delivered if "owner line mode is locked" in t]), 4)  # one per lockout
        self.assertEqual(ctx.commands["line"]("unlock-owner"), "Done.")
        self.assertIsNone(line.svc.owner_mode_lock())
        body, call, relay, session = self._call(line, "CA" + "9" * 32)
        self.verify(line, relay, session)

    def test_N13_only_the_owner_can_unlock(self):
        clock = [1_800_000_000.0]
        line = self.make_at(clock)
        self.addCleanup(line.close)
        for source in ("tool", "call-agent", "owner-voice"):
            out = line.svc.submit("unlock_owner", source=source)
            self.assertFalse(out["result"]["ok"], source)

    def test_N13_a_success_resets_the_streak_but_not_the_day_count(self):
        clock = [1_800_000_000.0]
        line = self.make_at(clock)
        self.addCleanup(line.close)
        body, call, relay, session = self._call(line, "CA" + "6" * 32)
        self._fail(line, session, 2)
        self.verify(line, relay, session)
        self._end(line, call)
        body, call, relay, session = self._call(line, "CA" + "7" * 32)
        self._fail(line, session, 2)
        # Round 42 (B23): _fail waits on the session's counter, which the engine bumps just before it stores the
        # failure; wait on the stored count itself instead of reading it once.
        wait_for(lambda: self._stored_failures(line) == 4, what="four failures stored")
        self.assertIsNone(line.svc.owner_mode_lock())  # 2 + 2 with a success between: no cooldown
        self.assertEqual(self._stored_failures(line), 4)

    @staticmethod
    def _stored_failures(line):
        with line.store._lock:
            return line.store._conn.execute("SELECT COUNT(*) FROM owner_auth_failures").fetchone()[0]


class N12KeypadConfirmationTests(OwnerLineBase):
    """R1 delta 11 N12: the reviewer's residual (an early timer, held delivery, an interruption, then "Yes.") cannot
    approve anything, because only keyed digits approve."""
    _verified = OwnerRequestTests._verified
    _propose = OwnerRequestTests._propose

    def test_N12_probe_residual_held_read_back_interrupt_then_yes_approves_nothing(self):
        line, call, relay, session = self._verified()
        sent = []
        session._send = lambda frame: sent.append(frame)  # the read-back never reaches the owner
        relay.prompt("Please call Sample Bistro for me.")
        wait_for(lambda: session.proposal is not None, what="proposed")
        time.sleep(0.5)
        relay.send({"type": "interrupt", "utteranceUntilInterrupt": ""})
        relay.prompt("Yes.")
        time.sleep(0.5)
        self.assertEqual(line.svc._voice_tickets, [])

    def test_N12_digits_keyed_before_the_proposal_do_nothing(self):
        line, call, relay, session = self._verified()
        key(relay, BUSINESS[-4:] + "#")  # no proposal: not a confirmation
        time.sleep(0.2)
        proposal = self._propose(relay, session)
        self.assertEqual(session._confirm_keys, [])
        self.assertEqual(line.svc._voice_tickets, [])
        self.assertIsNotNone(proposal)

    def test_N12_the_confirmation_times_out(self):
        import plugin.line.engine.owner_line as owner_line
        saved, owner_line.CONFIRM_WINDOW_S = owner_line.CONFIRM_WINDOW_S, 0.4
        try:
            line, call, relay, session = self._verified()
            proposal = self._propose(relay, session)
            relay.wait_text("That confirmation timed out", timeout=5)
            key(relay, BUSINESS[-4:] + "#")
            time.sleep(0.3)
            self.assertEqual(line.svc._voice_tickets, [])
            self.assertEqual(line.store.get_task(proposal["task_id"]).status.value, "awaiting_approval")
        finally:
            owner_line.CONFIRM_WINDOW_S = saved

    def test_N12_the_challenge_is_the_destinations_last_four_digits(self):
        from plugin.line.engine.owner_line import last_four
        self.assertEqual(last_four("+18005551234"), "1234")  # toll-free: same rule
        self.assertEqual(last_four("+17755550101"), "0101")
        self.assertEqual(last_four("611"), "611")  # short numbers are refused by the destination gate anyway


class N14RollingWindowTests(OwnerLineBase):
    """R1 delta 11 N14: the PIN split across frames or punctuation never reaches the transcript or Hermes."""

    def _call(self):
        line = self.make()
        body, call, relay, session = self.call_in(line)
        return line, call, relay, session

    def test_N14_probe_split_pin_across_two_frames_is_scrubbed(self):
        line, call, relay, session = self._call()
        relay.prompt("four eight")
        relay.prompt("two one")
        wait_for(lambda: len(line.drivers[-1].turns) >= 2, what="both fragments handled")
        blob = self.everything(line, call.id)
        for leak in ("four eight", "two one", PIN):
            self.assertNotIn(leak, blob)
        self.assertEqual(line.drivers[-1].turns.count("[business] [PIN]"), 2)

    def test_N14_semicolon_and_three_frame_splits(self):
        line, call, relay, session = self._call()
        relay.prompt("four eight; two one")
        relay.prompt("four")
        relay.prompt("eight two")
        relay.prompt("one, thanks")
        wait_for(lambda: len(line.drivers[-1].turns) >= 4, what="all handled")
        blob = self.everything(line, call.id)
        for leak in ("four eight", "two one", "eight two", "four"):
            self.assertNotIn(leak, blob)
        self.assertIn("[PIN], thanks", blob)

    def test_N14_a_late_fragment_is_redacted_in_the_store(self):
        """Past the 1.5 s hold the first fragment is sent; when the rest comes, its stored row is redacted (Hermes
        text already sent cannot be recalled; the hold makes this the rare case)."""
        line, call, relay, session = self._call()
        relay.prompt("four eight")
        wait_for(lambda: len(line.drivers[-1].turns) >= 1, timeout=5, what="held fragment sent after 1.5 s")
        relay.prompt("two one")
        wait_for(lambda: "pin_redacted_late" in [e["kind"] for e in line.store.events(call.id)], what="late redaction")
        texts = [t["text"] for t in line.store.transcript(call.id) if t["speaker"] == "business"]
        self.assertEqual(texts, ["[PIN]", "[PIN]"])

    def test_N14_ordinary_speech_is_not_held(self):
        line, call, relay, session = self._call()
        t0 = time.monotonic()
        relay.prompt("Hello, can you take a message?")
        wait_for(lambda: line.drivers[-1].turns, what="turn")
        self.assertLess(time.monotonic() - t0, 1.0)
        relay.prompt("My number is 775 555 0123")  # a phone number survives
        wait_for(lambda: any("775 555 0123" in t for t in line.drivers[-1].turns), timeout=5, what="number kept")
