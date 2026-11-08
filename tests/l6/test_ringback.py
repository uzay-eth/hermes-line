"""Ring-back listener scope using real engine/ingress and fake Twilio only."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from test_harness import trial, secret
sys.path.insert(0, str(trial.REPO/'tests/l1'))
from harness import Line, OWNER, LINE, SECRETS, wait_for
from plugin.line.engine.contracts import CallState, Role
from plugin.line.engine.telephony import ProviderError

PIN = '693728'  # synthetic fixture, never a staged owner secret
BUSINESS = trial.ALLOW[1]


def agent(driver, text):
    if 'fixture business' in text:
        driver.last_tool = driver.tool('owner_new_task', BUSINESS, 'Offline business', 'Fixture only', 'yes')
    return ['Okay.']


class RingbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.line = Line(start=False, owner_number=trial.ALLOW[0], owner_name='Can', reply=agent,
            policy=dict(trial.config(LINE).policy),
            quiet_before_speak_s=trial.config(LINE).quiet_before_speak_s,
            call_profile=trial.CALL_PROFILE,
            now_utc=lambda: datetime(2026,11,5,20,tzinfo=timezone.utc),
            secrets={**SECRETS, 'HERMES_LINE_OWNER_PIN': PIN})
        self.adapter = trial.RingbackAdapter(secret, line=LINE, minutes=3, run_dir=self.root, real=False)
        self.adapter.store = self.line.store
        self.parent = None
        self.adapter.parent = lambda: self.parent
        self.line.svc.adapter = self.adapter
        self.line.svc.start(port=0)
        self.addCleanup(self.line.close)

    def call_in(self, caller=None):
        sid = 'CA'+'1'*32
        _, body = self.line.post('/twilio/voice/inbound', {'CallSid':sid,'From':caller or trial.ALLOW[0], 'To':LINE})
        call = wait_for(lambda: [c for c in self.line.store.list_calls() if c.provider_sid == sid])[0]
        self.parent = call.id
        relay = self.line.relay(body); relay.send({'type':'setup','callSid':sid})
        session = wait_for(lambda: self.line.session(call.id))
        return call, relay, session

    def dial_child(self):
        call, relay, session = self.call_in()
        for digit in PIN+'#':relay.send({'type':'dtmf','digit':digit})
        wait_for(lambda: session.owner_verified)
        relay.prompt('Please call the fixture business')
        proposal = wait_for(lambda: session.proposal, what='owner task proposal')
        relay.wait_text('then pound.')
        for digit in proposal['code']+'#':relay.send({'type':'dtmf','digit':digit})
        wait_for(lambda: trial.approved_tasks(self.line.store, call.id))
        self.assertFalse(self.adapter.forms)
        self.assertFalse(trial.ringback_done(self.line.store, call.id))
        self.line.svc.submit('hang_up', source='slash', call_id=call.id)
        wait_for(lambda: self.adapter.forms)
        tid = proposal['task_id']
        child = wait_for(lambda: [c for c in self.line.store.calls_for_task(tid) if c.provider_sid])[0]
        self.assertFalse(trial.ringback_done(self.line.store, call.id))
        return call, child

    def answer(self, child):
        _, body = self.line.post('/twilio/voice/answer/'+child.id, {'CallSid':child.provider_sid})
        relay = self.line.relay(body); relay.send({'type':'setup', 'callSid':child.provider_sid})
        relay.prompt('Thanks for calling Sample Bistro, this is Mike.')
        relay.wait_text('live person?')
        self.assertEqual(len(self.adapter.forms),1)
        relay.prompt('Yes, speaking.')
        # A form is visible before the adapter returns and the engine publishes
        # the SID/state. Wait on the authoritative call record, not that boundary.
        def ringing():
            record = self.line.store.get_call(child.id)
            return record if record.owner_sid and record.state is CallState.RINGING_OWNER else None
        record = wait_for(ringing, timeout=15, what='engine owner SID and ringing_owner state')
        self.assertEqual(len(self.adapter.forms),2)
        return relay, record.owner_sid

    def test_keypad_hangup_child_human_owner_accept_conference_and_evidence(self):
        parent, child = self.dial_child()
        relay, owner_sid = self.answer(child)
        _, whisper = self.line.post('/twilio/voice/owner/answer/'+child.id, {'CallSid':owner_sid})
        self.assertIn('Press 1', whisper)
        _, body = self.line.post('/twilio/voice/owner/accept/'+child.id, {'CallSid':owner_sid,'Digits':'1'})
        self.assertIn('Conference', body)
        self.line.wait_state(child.id, 'owner_connected')
        session = self.line.session(child.id)
        self.line.post('/twilio/voice/conference/status/'+child.id,
            {'CallSid':owner_sid,'FriendlyName':session.conference,'StatusCallbackEvent':'participant-join'})
        wait_for(lambda: relay.of_type('end'))
        relay.close()
        wait_for(lambda: self.line.session(child.id).relay is None)
        _, business_join = self.line.post('/twilio/voice/connect/action/'+child.id+'?g=0',
            {'CallSid':child.provider_sid,'HandoffData':'{"reason":"takeover"}'})
        self.assertIn('Conference', business_join)
        self.line.post('/twilio/voice/conference/status/'+child.id,
            {'CallSid':owner_sid,'FriendlyName':session.conference,'StatusCallbackEvent':'conference-end'})
        wait_for(lambda: trial.ringback_done(self.line.store, parent.id))
        wait_for(lambda: all(self.adapter.records[sid]['status'] == 'completed' for sid in (child.provider_sid, owner_sid)))
        self.assertEqual([f['To'] for f in self.adapter.forms], [BUSINESS, trial.ALLOW[0]])
        self.assertTrue(all(0 < int(f['TimeLimit']) <= 180 for f in self.adapter.forms))
        proxy = Mock(wraps=self.line.store); proxy.close = Mock()
        with patch.object(trial, 'Store', return_value=proxy), patch.object(trial, 'REPO', self.root):
            path = trial.evidence(parent.id, self.root/'run')
        import json
        data = json.loads((self.root/path/'trial.json').read_text())
        self.assertEqual(len(data['related_calls']), 2)
        self.assertEqual(data['owner_pin_attempts']['results'], ['pass'])
        self.assertNotIn(PIN, json.dumps(data))
        self.assertTrue(any(e['kind'] == 'conference' for c in data['related_calls'] for e in c['events']))

    def test_owner_declines_no_retry(self):
        parent, child = self.dial_child()
        relay, sid = self.answer(child)
        self.line.post('/twilio/voice/owner/accept/'+child.id, {'CallSid':sid,'Digits':'2'})
        wait_for(lambda: any(e['kind']=='owner_declined' for e in self.line.store.events(child.id)))
        self.line.svc.submit('hang_up', source='slash', call_id=child.id)
        wait_for(lambda: trial.ringback_done(self.line.store, parent.id))
        self.assertEqual(len(self.adapter.forms), 2)

    def test_child_unanswered_finishes_without_owner_ring(self):
        parent, child = self.dial_child()
        self.line.post('/twilio/voice/status/'+child.id, {'CallSid':child.provider_sid, 'CallStatus':'no-answer'})
        wait_for(lambda: trial.ringback_done(self.line.store, parent.id))
        self.assertEqual(len(self.adapter.forms), 1)

    def test_stranger_cannot_create_task(self):
        call, relay, session = self.call_in(BUSINESS)
        out = self.line.svc.in_call(session.agent_session_id, 'owner_new_task', BUSINESS, 'x','y','yes')
        self.assertFalse(out['ok'])
        self.assertFalse(trial.approved_tasks(self.line.store, call.id))
        self.assertFalse(self.adapter.forms)

    def test_unexpected_direct_create_before_balance_or_api(self):
        with patch.object(trial, 'balance', side_effect=AssertionError('balance read')), patch.object(self.adapter, '_post', side_effect=AssertionError('API')):
            for to in (LINE, trial.BORA, BUSINESS, '+12025550123'):
                with self.assertRaises(ProviderError):self.adapter._create_call(to=to)

    def mint(self, child, destination, role):
        gate = self.line.svc.gate
        binding = gate.binding(e164=destination, role=role, call_id=child.id, line_key=self.line.cfg.line_key)
        token = self.line.svc.consent.issue('fixture', child.task_id, binding, 60)
        return gate.authorize(destination, role=role, action='fixture', task_id=child.task_id,
                              call_id=child.id, line_key=self.line.cfg.line_key, token=token)

    def dispatch(self, permit):
        return self.adapter.place_call(permit, from_number=LINE, answer_url='https://fixture.test/answer',
            status_url='https://fixture.test/status', timeout_s=30, time_limit_s=180)

    def test_retry_and_changed_business_refused_before_any_api(self):
        _, child = self.dial_child()
        with patch.object(self.adapter, '_post', side_effect=AssertionError('API')):
            for destination in (BUSINESS, trial.ALLOW[0]):
                with self.assertRaises(ProviderError):
                    self.dispatch(self.mint(child, destination, Role.BUSINESS))

    def test_owner_balance_stop_cancels_run_before_create_and_never_retries(self):
        _, child = self.dial_child()
        self.adapter.real = True
        with patch.object(trial, 'balance', return_value={'usd':'4.00'}) as funds, \
             patch.object(self.adapter, '_post', side_effect=AssertionError('API')):
            with self.assertRaises(ProviderError):self.dispatch(self.mint(child, trial.ALLOW[0], Role.OWNER))
            self.assertTrue(self.adapter.cancelled.is_set())
            self.assertEqual(funds.call_count, 1)
            with self.assertRaises(ProviderError):self.dispatch(self.mint(child, trial.ALLOW[0], Role.OWNER))
            self.assertEqual(funds.call_count, 1)
        self.adapter.real = False  # local service cleanup stays on the fake boundary

    def test_global_deadline_refuses_owner_create_before_balance(self):
        _, child = self.dial_child()
        self.adapter.deadline = 0
        with patch.object(trial, 'balance', side_effect=AssertionError('balance read')):
            with self.assertRaises(ProviderError):self.dispatch(self.mint(child, trial.ALLOW[0], Role.OWNER))


    def test_owner_scope_cannot_reach_delta_even_with_hold_approval(self):
        from plugin.line.engine.contracts import GateRejected
        self.assertEqual(self.line.cfg.policy['allowed_destinations'],list(trial.ALLOW))
        self.assertTrue(self.line.cfg.policy['allowlist_only'])
        self.assertTrue(self.line.cfg.policy['quiet_hours_enabled'])
        call, relay, session=self.call_in()
        for digit in PIN+'#':relay.send({'type':'dtmf','digit':digit})
        wait_for(lambda: session.owner_verified)
        # A coordinator-owned business approval belongs only to hold; owner-line
        # requests must never consult it or make a provider/balance request for Delta.
        with patch('hold.approval',side_effect=AssertionError('hold approval consulted')), \
             patch.object(self.adapter,'_post',side_effect=AssertionError('provider API')):
            out=self.line.svc.in_call(session.agent_session_id,'owner_new_task','+18002211212','Delta','Ask a question','yes')
        self.assertFalse(out['ok']);self.assertFalse(self.adapter.forms)
        self.assertFalse(trial.approved_tasks(self.line.store,call.id))

    def test_owner_child_request_keeps_quiet_hours_gate(self):
        from plugin.line.engine.contracts import GateRejected
        self.line.svc.now_utc=lambda:datetime(2026,11,5,12,tzinfo=timezone.utc)
        with patch.object(self.adapter,'_post',side_effect=AssertionError('provider API')):
            with self.assertRaises(GateRejected) as caught:
                self.line.svc.request_call(destination=BUSINESS,title='Fixture',goal='Fixture')
        self.assertEqual(caught.exception.gate,'quiet_hours')
