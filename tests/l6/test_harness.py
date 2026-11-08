"""Offline L6 guards and production integration; no tunnel, owner gateway or Twilio."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

SPEC = importlib.util.spec_from_file_location('trial_run', Path(__file__).resolve().parents[2] / 'tools/trial/run.py')
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)
_NUMBER_FIXTURE = tempfile.TemporaryDirectory()
_NUMBER_PATH = Path(_NUMBER_FIXTURE.name) / 'personal_numbers.json'
_NUMBER_PATH.write_text(json.dumps({'owner': '+18475550101', 'fi': '+17755550102'}))
_NUMBER_PATH.chmod(0o600)
trial.PERSONAL_NUMBERS_PATH = _NUMBER_PATH
trial.initialize_numbers()
from plugin.line.engine.telephony import ProviderError

ACCOUNT = 'AC' + '0' * 32
LINE = '+17755550100'  # offline sample, never dialed


def secret(name):
    return ACCOUNT if name == 'HERMES_LINE_TWILIO_ACCOUNT_SID' else None


class HarnessTests(unittest.TestCase):
    def test_bad_destinations_and_missing_coordinator_refused_before_any_io(self):
        with patch.object(trial, 'credentials', side_effect=AssertionError('credential access')):
            for destination in (trial.BORA, '911', '+1911', '+12025550123', '18475550101',
                                '+1 847 555 0101', '', 'https://example.test'):
                with self.subTest(destination=destination), self.assertRaises(trial.Stop):
                    trial.validate_call(destination, True, 3)
            with self.assertRaises(trial.Stop):
                trial.validate_call(trial.ALLOW[0], False, 3)
            for minutes in (0, 4, -1, True):
                with self.assertRaises(trial.Stop):
                    trial.validate_call(trial.ALLOW[0], True, minutes)
            trial.validate_call(trial.ALLOW[1], True, 3)

    def test_balance_fails_closed_and_accepts_exact_floor(self):
        adapter = Mock()
        for response in ({'currency': 'USD', 'balance': '4.999'},
                         {'currency': 'CAD', 'balance': '100'},
                         {'currency': 'USD', 'balance': 'NaN'},
                         {'currency': 'USD', 'balance': 'Infinity'}):
            adapter._post.return_value = response
            with self.assertRaises(trial.Stop):
                trial.balance(adapter)
        adapter._post.return_value = {'currency': 'USD', 'balance': '5.00'}
        self.assertEqual(trial.balance(adapter)['usd'], '5.00')
        adapter._post.assert_called_with('Balance.json', None)

    def test_exact_safety_configuration(self):
        cfg = trial.config(LINE)
        self.assertEqual(cfg.policy['allowed_destinations'], list(trial.ALLOW))
        self.assertEqual(cfg.owner_number, trial.ALLOW[0])
        self.assertEqual(cfg.voice, {'transport': 'conversation_relay'})
        self.assertEqual(cfg.sms_source, 'none')
        self.assertFalse(cfg.otp_browser_fill)
        self.assertTrue(cfg.policy['disclosure_enabled'])
        self.assertTrue(cfg.policy['quiet_hours_enabled'])
        self.assertFalse(cfg.policy['recording_enabled'])

    def test_production_approval_sets_both_duration_caps_and_blocks_second_leg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = trial.config(LINE)
            store = trial.Store(root / 'line.db')
            adapter = trial.TrialAdapter(secret)
            svc = trial.CallService(cfg=cfg, store=store, adapter=adapter,
                driver_factory=lambda _: trial.QuietDriver(), lock_dir=root / 'locks',
                get_secret=secret, now_utc=lambda: datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
            try:
                svc.start(port=0)
                req = svc.request_call(destination=trial.ALLOW[0], title='Offline test', goal='Offline only')
                self.assertFalse(adapter.forms)
                result = svc.submit('approve', source='slash', task_id=req['task_id'])
                self.assertTrue(result['result']['ok'])
                self.assertEqual(svc.cfg.max_call_minutes, 3)
                self.assertEqual(adapter.forms[0]['TimeLimit'], '180')
                self.assertEqual(adapter.forms[0]['Record'], 'false')
                self.assertTrue('/twilio/voice/answer/' in adapter.forms[0]['Url'])
                kw = dict(to=trial.ALLOW[1], from_number=LINE, answer_url='https://example.test',
                          status_url='https://example.test', timeout_s=30, time_limit_s=180)
                with self.assertRaises(ProviderError):
                    adapter._create_call(**kw)
                self.assertEqual(len(adapter.forms), 1)
            finally:
                svc.stop()
                store.close()

    def test_ambiguous_real_create_is_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = trial.TrialAdapter(secret, real=True, to=trial.ALLOW[0], run_dir=Path(tmp))
            kw = dict(to=trial.ALLOW[0], from_number=LINE, answer_url='https://example.test',
                      status_url='https://example.test', timeout_s=30, time_limit_s=180)
            with patch.object(trial, 'balance', return_value={'usd': '10'}), \
                 patch.object(trial.TwilioRestAdapter, '_create_call', side_effect=TimeoutError) as create:
                with self.assertRaises(TimeoutError):
                    adapter._create_call(**kw)
                with self.assertRaises(ProviderError):
                    adapter._create_call(**kw)
                self.assertEqual(create.call_count, 1)
                self.assertTrue((Path(tmp) / 'dial-attempt.json').exists())

    def test_failed_tunnel_start_cleans_service_and_tunnel(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = trial.Runtime(Path(tmp), trial.config(LINE), trial.TrialAdapter(secret), secret)
            runtime.svc = Mock()
            runtime.svc.server.port = 1234
            tunnel = Mock()
            tunnel.start.side_effect = trial.Stop('tunnel failed')
            with patch.object(trial, 'Tunnel', return_value=tunnel), self.assertRaises(trial.Stop):
                with runtime:
                    pass
            runtime.svc.stop.assert_called_once()
            tunnel.stop.assert_called_once()

    def test_report_redacts_url_secrets_phone_codes_and_cards(self):
        value = trial.masked({'xml': '<Relay url="wss://host/t/secretABC/relay?t=tokenDEF"/>',
                              'text': 'phone +18475550101 or +1 847-555-0101 verification code 482193'})
        text = json.dumps(value)
        for raw in ('secretABC', 'tokenDEF', '+18475550101', '482193'):
            self.assertNotIn(raw, text)
        self.assertNotIn('847-555-0101', text)

    def test_shutdown_blocks_even_the_first_create(self):
        adapter = trial.TrialAdapter(secret)
        adapter.cancelled.set()
        with self.assertRaises(ProviderError):
            adapter._create_call(to=trial.ALLOW[0])
        self.assertFalse(adapter.forms)

    def test_real_request_retains_quiet_hours_before_provider_io(self):
        from plugin.line.engine.contracts import GateRejected
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = trial.Store(root / 'line.db')
            adapter = Mock()
            svc = trial.CallService(cfg=trial.config(LINE), store=store, adapter=adapter,
                driver_factory=Mock(), lock_dir=root / 'locks', get_secret=secret,
                now_utc=lambda: datetime(2026, 11, 5, 10, tzinfo=timezone.utc))
            try:
                with self.assertRaises(GateRejected) as refused:
                    svc.request_call(destination=trial.ALLOW[0], title='Offline test', goal='Offline only')
                self.assertEqual(refused.exception.gate, 'quiet_hours')
                adapter.place_call.assert_not_called()
            finally:
                store.close()

    def test_dns_override_is_exact_host_scoped_and_restored(self):
        with patch.object(trial.socket, 'getaddrinfo') as resolve:
            resolve.side_effect = [trial.socket.gaierror(), [('resolved',)], [('other',)]]
            with patch.object(trial.subprocess, 'run', return_value=Mock(stdout='104.16.230.132\n')):
                with trial.TunnelDNS('https://offline.trycloudflare.com') as dns:
                    self.assertIn('1.1.1.1', dns.mode)
                    trial.socket.getaddrinfo('offline.trycloudflare.com', 443)
                    trial.socket.getaddrinfo('other.test', 443)
                self.assertIs(trial.socket.getaddrinfo, resolve)
                self.assertEqual(resolve.call_args_list[1].args, ('104.16.230.132', 443))
                self.assertEqual(resolve.call_args_list[2].args, ('other.test', 443))

    def test_evidence_export_keeps_sid_price_pending_and_masks_transcript(self):
        from plugin.line.engine.contracts import CallState, Direction, Mode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / 'run'
            store = trial.Store(run_dir / 'home/plugin-data/hermes-line/line.db')
            sid = 'CA' + '0' * 32
            call = store.create_call(task_id=None, direction=Direction.OUTBOUND,
                counterpart_e164=trial.ALLOW[0], counterpart_masked='[NUMBER]',
                state=CallState.ENDED, mode=Mode.AGENT, provider_sid=sid)
            store.append_transcript(call.id, 'business', 'phone +18475550101 verification code 482193')
            store.close()
            trial.save(run_dir / 'balance-before.json', {'usd': '13'})
            trial.save(run_dir / 'balance-after.json', {'usd': '12.9'})
            trial.save(run_dir / 'provider.json', {'sid': sid, 'price': None, 'status': 'completed'})
            with patch.object(trial, 'REPO', root), \
                 patch.object(trial.TwilioRestAdapter, '_post', side_effect=AssertionError('network')):
                trial.evidence(call.id, run_dir)
            data = json.loads((root / 'evidence/trial' / call.id / 'trial.json').read_text())
            self.assertEqual(data['CallSID'], sid)
            self.assertIsNone(data['provider']['price'])
            self.assertEqual(data['balance_after']['usd'], '12.9')
            self.assertNotIn('482193', json.dumps(data))
            self.assertNotIn(trial.ALLOW[0], json.dumps(data))

    def test_driver_forwards_session_mcp_instead_of_empty_list(self):
        from plugin.line.engine.acp_driver import AcpDriver
        connection = Mock()
        connection.request.return_value = {'sessionId': 'offline-session'}
        servers = [{'type': 'http', 'name': 'hermes_line', 'url': 'http://127.0.0.1:1234/mcp',
                    'headers': [{'name': 'Authorization', 'value': 'Bearer offline-token'}]}]
        with patch('plugin.line.engine.acp_driver._Connection', return_value=connection):
            driver = AcpDriver('offline', command=['never-executed'])
            driver.set_mcp_servers(servers)
            driver.start('Offline briefing')
            self.assertEqual(connection.request.call_args_list[1].args[1]['mcpServers'], servers)
            driver.close()


if __name__ == '__main__':
    unittest.main()


class CallerIdTests(unittest.TestCase):
    LINE = '+17755550120'

    def test_fi_caller_id_changes_only_the_business_leg_from(self):
        cfg = trial.config(self.LINE, 3, trial.caller_id('fi', self.LINE))
        self.assertEqual(cfg.from_number, trial.ALLOW[1])
        self.assertEqual(cfg.line_number, self.LINE)

    def test_default_caller_id_is_the_plugin_line(self):
        cfg = trial.config(self.LINE, 3, trial.caller_id('line', self.LINE))
        self.assertEqual((cfg.from_number, cfg.line_number), (self.LINE, self.LINE))

    def test_unknown_caller_id_is_refused(self):
        with self.assertRaises(trial.Stop):
            trial.caller_id(trial.BORA, self.LINE)


class BlockedDestinationTests(unittest.TestCase):
    def test_trial_config_blocks_the_bora_number(self):
        from plugin.line.policy.settings import Settings
        cfg = trial.config('+17755550120', 3)
        self.assertIn(trial.BORA, Settings(**cfg.policy).blocked_destinations if isinstance(cfg.policy, dict)
                      else cfg.policy.blocked_destinations)
