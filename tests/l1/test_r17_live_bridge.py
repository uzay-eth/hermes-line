"""Round 17: the live-leg owner bridge (the hook L6 round 9 names).

A PIN-verified owner on their own inbound call keys the confirmation of an owner-mode task; with
``owner_live_bridge`` on, that one child task is dialled while the owner stays on the line, and when a person
answers, the owner's existing inbound leg and the business leg join one conference (no new owner dial). The
owner hanging up first falls back to ring-back; no answer, expiry, cancel and teardown are covered."""
import time
import unittest
from unittest import mock

from harness import BUSINESS, LINE, OWNER, SECRETS, Line, wait_for
from plugin.line.engine import owner_line
from test_flows import CONFIRMED
from test_r13_owner_line import PIN, OwnerLineBase

STRANGER = "+17755550177"
PARENT_SID = "CA" + "1" * 32


def agent(driver, text):
    """Scripted parent-call Hermes: the verified owner asks for a call, with or without being put through."""
    if "put me through" in text:
        driver.last_tool = driver.tool("owner_new_task", BUSINESS, "Table at Sample Bistro", "Book a table", "yes")
        return ["Sure."]
    if "just book it" in text:
        driver.last_tool = driver.tool("owner_new_task", BUSINESS, "Table at Sample Bistro", "Book a table for 4", "")
        return ["Sure."]
    return ["Okay."]


def owners_rung(line):
    return [c for c in line.adapter.created if c["to"] == OWNER]


def kinds(line, call_id):
    return [e["kind"] for e in line.store.events(call_id)]


class LiveBridgeBase(OwnerLineBase):
    def make(self, *, live=True, **over):
        line = Line(owner_name="Can", secrets={**SECRETS, "HERMES_LINE_OWNER_PIN": PIN}, reply=agent,
                    owner_live_bridge=live, **over)
        self.addCleanup(line.close)
        return line

    def verified(self, line):
        body, call, relay, session = self.call_in(line, sid=PARENT_SID)
        self.verify(line, relay, session)
        return call, relay, session

    def confirm(self, line, relay, session, request="Please call Sample Bistro and put me through"):
        relay.prompt(request)
        proposal = wait_for(lambda: session.proposal, what="proposal")
        relay.wait_text("then pound.")
        for key in proposal["code"] + "#":
            relay.send({"type": "dtmf", "digit": key})
        return proposal

    def live_child(self, line):
        """The owner stays on the line; the child is dialled now. Returns parent pieces and the child record.
        Round 37 (B1): every step waits on the engine's own record (its events, the store, the adapter), with room
        for a loaded run, and names the step that did not happen; nothing is timed by sleeps."""
        call, relay, session = self.verified(line)
        proposal = self.confirm(line, relay, session)
        keyed = wait_for(lambda: [e for e in line.store.events(call.id) if e["kind"] == "owner_confirm_keyed"],
                         timeout=15, what="the keypad confirmation")
        self.assertTrue(keyed[-1]["data"].get("live_bridge"), kinds(line, call.id))  # the live path, not ring-back
        relay.wait_text("Confirmed. I'm calling now.", timeout=15)
        wait_for(lambda: "child_call_linked" in kinds(line, call.id), timeout=15, what="the child link")
        created = wait_for(lambda: [c for c in line.adapter.created if c["to"] == BUSINESS], timeout=15,
                           what="child dial")[0]
        child = wait_for(lambda: [c for c in line.store.calls_for_task(proposal["task_id"]) if c.provider_sid],
                         timeout=15, what="the child's provider sid")[0]
        self.assertEqual(child.provider_sid, created["sid"])
        return call, relay, session, child

    def answer_child(self, line, child):
        body = line.answer(child.id, child.provider_sid)
        crelay = line.relay(body)
        crelay.send({"type": "setup", "callSid": child.provider_sid})
        crelay.prompt("Thanks for calling Sample Bistro, this is Mike.")
        crelay.wait_text("live person?")
        crelay.prompt(CONFIRMED)
        return crelay

    def join(self, line, call_id, sid, relay):
        """Twilio's side of the hand-off: the relay ends, then the Connect action returns the conference."""
        wait_for(lambda: relay.of_type("end"), what=f"end frame for {call_id}")
        relay.close()
        wait_for(lambda: line.session(call_id) is None or line.session(call_id).relay is None)
        return line.post(f"/twilio/voice/connect/action/{call_id}?g=0",
                         {"CallSid": sid, "HandoffData": '{"reason":"takeover"}'})[1]


class LiveBridgeTests(LiveBridgeBase):
    def test_R17_child_dials_while_the_owner_stays_on_the_line(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        self.assertIn("connect you on this call when a person answers", relay.spoken())
        self.assertEqual(line.state(call.id), "agent_talking")  # the owner's call is still up
        self.assertEqual(owners_rung(line), [])
        self.assertIn("child_call_linked", kinds(line, call.id))
        linked = [e for e in line.store.events(child.id) if e["kind"] == "parent_call_linked"]
        self.assertEqual(linked[0]["data"]["parent_call_id"], call.id)
        self.assertIs(line.session(child.id).parent, session)
        self.assertIs(session.child, line.session(child.id))

    def test_R17_human_answer_moves_the_existing_owner_leg_into_the_childs_conference(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        crelay = self.answer_child(line, child)
        line.wait_state(child.id, "owner_connected")
        line.wait_state(call.id, "owner_connected")
        relay.wait_text("Someone answered about Table at Sample Bistro. Connecting you now.")
        self.assertEqual(owners_rung(line), [])  # no new owner dial, ever
        self.assertEqual(len(line.adapter.created), 1)
        record = line.store.get_call(child.id)
        self.assertEqual(record.owner_sid, PARENT_SID)  # the child's owner leg is the owner's own inbound leg
        conference = f"hl-{child.id}"
        self.assertEqual(record.conference, conference)
        self.assertEqual(line.store.get_call(call.id).conference, conference)
        owner_twiml = self.join(line, call.id, PARENT_SID, relay)
        business_twiml = self.join(line, child.id, child.provider_sid, crelay)
        for body in (owner_twiml, business_twiml):
            self.assertIn(f">{conference}</Conference>", body)
            self.assertIn(f"/twilio/voice/conference/status/{child.id}", body)  # one status URL for both legs
        bridged = [e for e in line.store.events(child.id) if e["kind"] == "owner_bridged"][0]["data"]
        self.assertEqual(bridged, {"parent_call_id": call.id, "conference": conference, "owner_dialled": False})
        self.assertIn("owner_bridged", kinds(line, call.id))
        # teardown: the conference ends (either leg left; endConferenceOnExit) and both calls end
        line.post(f"/twilio/voice/conference/status/{child.id}",
                  {"CallSid": child.provider_sid, "FriendlyName": conference, "StatusCallbackEvent": "conference-end"})
        line.wait_state(child.id, "ended")
        line.wait_state(call.id, "ended")
        wait_for(lambda: {PARENT_SID, child.provider_sid} <= set(line.adapter.ended), what="both legs ended")
        self.assertEqual(line.store.get_call(call.id).end_reason, "owner_finished")
        line.wait_task(child.task_id, "done")

    def test_R17_business_hangs_up_in_the_conference_ends_the_owner_leg(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        self.answer_child(line, child)
        line.wait_state(call.id, "owner_connected")
        line.status(child.id, child.provider_sid, "completed")
        line.wait_state(child.id, "ended")
        line.wait_state(call.id, "ended")
        self.assertIn(PARENT_SID, line.adapter.ended)
        ended = [e for e in line.store.events(call.id) if e["kind"] == "child_call_ended"][0]["data"]
        self.assertTrue(ended["bridged"])

    def test_R17_owner_hangs_up_first_falls_back_to_ring_back(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        relay.close()  # the owner hangs up while the child is still on hold
        line.wait_state(call.id, "failed", timeout=5)
        gone = wait_for(lambda: [e for e in line.store.events(child.id) if e["kind"] == "parent_call_ended"])
        self.assertEqual(gone[0]["data"]["next"], "ring_back")
        self.assertIn(line.state(child.id), ("dialing", "ivr", "on_hold"))  # the child keeps going
        self.answer_child(line, child)
        line.wait_state(child.id, "ringing_owner")
        self.assertEqual(len(owners_rung(line)), 1)  # ring-back: the owner's phone rings, whisper then press 1
        self.assertNotIn("owner_bridged", kinds(line, child.id))

    def test_R17_child_no_answer_tells_the_owner_and_frees_the_call(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        line.status(child.id, child.provider_sid, "no-answer")
        line.wait_state(child.id, "failed")
        relay.wait_text("An update on the call about Table at Sample Bistro: nobody answered.")
        self.assertEqual(line.state(call.id), "agent_talking")
        self.assertIsNone(session.child)
        self.assertEqual(owners_rung(line), [])
        out = line.svc.in_call(session.agent_session_id, "owner_new_task", BUSINESS, "Again", "Again", "yes")
        self.assertTrue(out["ok"], out)  # one child at a time, not one per call

    def test_R17_star_cancels_the_child_before_the_bridge(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        relay.send({"type": "dtmf", "digit": "*"})
        line.wait_state(child.id, "ended")
        line.wait_task(child.task_id, "canceled")
        self.assertIn(child.provider_sid, line.adapter.ended)
        relay.wait_text("you cancelled it")
        self.assertIn("child_cancel_keyed", kinds(line, call.id))
        self.assertEqual(line.state(call.id), "agent_talking")

    def test_R17_chat_cancel_of_the_child_is_reported_on_the_owner_call(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        out = line.svc.submit("cancel", source="slash", task_id=child.task_id)
        self.assertTrue(out["result"]["ok"], out)
        line.wait_state(child.id, "ended")
        relay.wait_text("you cancelled it")

    def test_R17_an_expired_live_approval_never_dials(self):
        line = self.make()
        with mock.patch.object(owner_line, "LIVE_DIAL_WINDOW_S", -1.0):
            call, relay, session = self.verified(line)
            proposal = self.confirm(line, relay, session)
            relay.wait_text("the approval expired before the line could dial")
        self.assertIn("owner_bridge_ticket_expired", kinds(line, call.id))
        self.assertEqual(line.adapter.created, [])
        line.svc.submit("hang_up", source="slash", call_id=call.id)  # and nothing is dialled after the call either
        wait_for(lambda: call.id not in line.svc.sessions)
        time.sleep(0.3)
        self.assertEqual(line.adapter.created, [])
        self.assertEqual(line.store.get_task(proposal["task_id"]).status.value, "awaiting_approval")


    def test_R17_a_dial_refused_by_the_gate_is_reported_on_the_owner_call(self):
        from plugin.line.engine import deps
        line = self.make()
        call, relay, session = self.verified(line)
        relay.prompt("Please call Sample Bistro and put me through")
        proposal = wait_for(lambda: session.proposal, what="proposal")
        relay.wait_text("then pound.")
        with mock.patch.object(deps, "is_quiet", return_value=True):  # quiet hours begin before the dial
            for key in proposal["code"] + "#":
                relay.send({"type": "dtmf", "digit": key})
            relay.wait_text("it could not be placed (quiet_hours")
        self.assertEqual(line.adapter.created, [])
        self.assertEqual(line.state(call.id), "agent_talking")


class BusyLineControlTests(LiveBridgeBase):
    def test_R17_one_child_only_and_unrelated_calls_stay_blocked(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        again = line.svc.in_call(session.agent_session_id, "owner_new_task", BUSINESS, "Second", "Second", "yes")
        self.assertFalse(again["ok"])
        self.assertIn("one call at a time", again["error"])
        other = line.request()
        out = line.approve(other["task_id"])
        self.assertFalse(out["result"]["ok"])
        self.assertIn("busy", out["result"]["error"])
        forged = line.svc.submit("approve", source="owner-voice", task_id=other["task_id"],
                                 payload={"destination": BUSINESS, "parent": call.id})
        self.assertFalse(forged["result"]["ok"])
        self.assertIn("no matching spoken approval", forged["result"]["error"])
        status, body = line.post("/twilio/voice/inbound", {"CallSid": "CA" + "7" * 32, "From": STRANGER, "To": LINE})
        self.assertIn('reason="busy"', body)
        self.assertEqual(len(line.adapter.created), 1)

    def test_R17_off_by_default_keeps_the_after_hang_up_flow(self):
        line = self.make(live=False)
        call, relay, session = self.verified(line)
        proposal = self.confirm(line, relay, session)
        relay.wait_text("Confirmed. I'll call as soon as you hang up")
        self.assertIn("after you hang up", relay.spoken())
        time.sleep(0.3)
        self.assertEqual(line.adapter.created, [])
        line.svc.submit("hang_up", source="slash", call_id=call.id)
        wait_for(lambda: [c for c in line.adapter.created if c["to"] == BUSINESS], what="dial after hang-up")

    def test_R17_an_agent_mode_task_is_not_live_bridged(self):
        line = self.make()
        call, relay, session = self.verified(line)
        self.confirm(line, relay, session, request="Please call Sample Bistro, just book it")
        relay.wait_text("Confirmed. I'll call as soon as you hang up")
        time.sleep(0.3)
        self.assertEqual(line.adapter.created, [])

    def test_R17_a_caller_id_that_is_not_the_owner_never_gets_a_live_child(self):
        line = self.make()
        body, call, relay, session = self.call_in(line, caller=STRANGER, sid="CA" + "5" * 32)
        session.owner_verified = True  # even with a (forged) verified flag, the caller ID must be the owner's
        task = line.store.get_task(line.request(mode="owner")["task_id"])
        self.assertFalse(line.svc.live_bridge_possible(session, task))


class BridgeGateTests(LiveBridgeBase):
    def test_R17_no_bridge_without_a_confirmed_person(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        csession = line.session(child.id)
        self.assertFalse(csession.human_confirmed)
        self.assertFalse(line.svc.run(line.svc.bridge_live_owner(csession)))
        self.assertIn("owner_bridge_unavailable", kinds(line, child.id))
        self.assertEqual(line.state(call.id), "agent_talking")

    def test_R17_the_bridge_needs_the_tasks_owner_approval(self):
        line = self.make()
        call, relay, session, child = self.live_child(line)
        line.svc.grants.drop_task(child.task_id)  # the approval's bridge grant is gone
        self.answer_child(line, child)
        wait_for(lambda: "owner_ring_rejected" in kinds(line, child.id), what="bridge refused")
        self.assertNotIn("owner_bridged", kinds(line, child.id))
        self.assertEqual(line.state(call.id), "agent_talking")
        self.assertEqual(owners_rung(line), [])


if __name__ == "__main__":
    unittest.main()
