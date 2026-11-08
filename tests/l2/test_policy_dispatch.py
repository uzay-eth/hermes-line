"""Actual L1 dial-gate boundary with a stdlib-only in-memory provider fake.

No server, socket, credential lookup, registration or provider I/O is executed.
"""
import unittest
from plugin.line.engine.telephony import DialGate, TelephonyAdapter
from plugin.line.engine.contracts import GateRejected, Role
from plugin.line.policy import Settings, ConsentStore

HOTLINE = '+18005550123'  # Synthetic fixture only; never dialed.


class MemoryProvider(TelephonyAdapter):
    def __init__(self):
        super().__init__()
        self.created = []

    def _create_call(self, **kwargs):
        self.created.append(kwargs['to'])
        return 'fixture-call'

    def end_call(self, sid):
        pass

    def redirect_call(self, sid, url):
        pass


class PolicyDispatchTests(unittest.TestCase):
    def test_toll_free_gate_still_requires_consent(self):
        for mode in (False, True):
            settings = Settings(allowlist_only=mode, allowed_destinations=[HOTLINE], quiet_hours_enabled=False)
            store = ConsentStore()
            gate = DialGate(settings, store)
            args = dict(role=Role.BUSINESS, action='dial', task_id='task', call_id='call', line_key='line')
            with self.assertRaises(GateRejected):
                gate.authorize(HOTLINE, token=None, **args)
            binding = gate.binding(e164=HOTLINE, role=Role.BUSINESS, call_id='call', line_key='line')
            token = store.issue('dial', 'task', binding, 30)
            permit = gate.authorize(HOTLINE, token=token, **args)
            provider = MemoryProvider()
            provider.place_call(permit, from_number='+17755550100', answer_url='fixture', status_url='fixture', timeout_s=10, time_limit_s=30)
            self.assertEqual(provider.created, [HOTLINE])

    def test_config_block_is_rechecked_after_preview_and_approval(self):
        for mode in (False, True):
            store = ConsentStore()
            gate = DialGate(Settings(allowlist_only=mode, allowed_destinations=[HOTLINE], quiet_hours_enabled=False), store)
            self.assertTrue(gate.preview(HOTLINE).allowed)
            binding = gate.binding(e164=HOTLINE, role=Role.BUSINESS, call_id='call', line_key='line')
            token = store.issue('dial', 'task', binding, 30)
            gate.settings = Settings(allowlist_only=mode, allowed_destinations=[HOTLINE],
                                     blocked_destinations=['1 800-555-0123'], quiet_hours_enabled=False)
            provider = MemoryProvider()
            with self.assertRaises(GateRejected):
                permit = gate.authorize(HOTLINE, role=Role.BUSINESS, action='dial', task_id='task', call_id='call', line_key='line', token=token)
                provider.place_call(permit, from_number='+17755550100', answer_url='fixture', status_url='fixture', timeout_s=10, time_limit_s=30)
            self.assertEqual(provider.created, [])
