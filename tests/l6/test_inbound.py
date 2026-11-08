"""Offline inbound trial: fake provider, loopback ingress, no gateway/tunnel/key."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode
import urllib.request
import urllib.error
from test_harness import trial, secret, ACCOUNT
from inbound import VoiceSettings, VOICE_FIELDS, InboundTrial

LINE = '+17755550166'  # offline fixture; never sent to a provider
OTHER = '+17755550177'  # another well-formed offline number, never configured
import inbound  # noqa: E402  (the same module object run.py imports)
_LINE_FIXTURE = tempfile.TemporaryDirectory()
inbound.LINE_NUMBER_PATH = Path(_LINE_FIXTURE.name) / 'twilio_line_number'
inbound.LINE_NUMBER_PATH.write_text(LINE + '\n')
PN = 'PN' + 'a' * 32


class FakeNumber(trial.TrialAdapter):
    def __init__(self):
        super().__init__(secret)
        self.operations = []
        self.row = dict(sid=PN, phone_number=LINE, voice_url='https://old.test/voice',
            voice_method='GET', status_callback='https://old.test/status', status_callback_method='POST',
            voice_application_sid='AP' + 'b' * 32, sms_url='https://untouched.test/sms')
        self.fail_write = False
        self.after_write = None

    def _post(self, path, params):
        self.operations.append((path, params))
        if path == 'IncomingPhoneNumbers.json?' + urlencode({'PhoneNumber': LINE}):
            return {'incoming_phone_numbers': [dict(self.row)]}
        if path == 'IncomingPhoneNumbers/' + PN + '.json':
            if self.fail_write:
                raise TimeoutError('offline restore failure')
            for key, val in params:
                if key not in VOICE_FIELDS:
                    raise AssertionError('non-voice mutation')
                self.row[VOICE_FIELDS[key]] = val
            if self.after_write:
                self.after_write()
            return dict(self.row)
        if path.startswith('IncomingPhoneNumbers'):
            raise AssertionError('another number was accessed')
        return super()._post(path, params)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'listen-offline'
        self.fake = FakeNumber()
        self.original = dict(self.fake.row)
        self.settings = VoiceSettings(self.fake, LINE, self.root)

    def install(self):
        self.settings.install('https://new.test/t/test/voice', 'https://new.test/t/test/status', self.run)

    def test_snapshot_is_durable_before_install_and_sms_is_never_touched(self):
        def check():
            self.assertTrue(self.settings.pending.exists())
            self.assertTrue((self.run / 'voice-before.json').exists())
        self.fake.after_write = check
        self.install()
        self.fake.after_write = None
        self.assertEqual(self.fake.row['voice_application_sid'], '')
        self.assertEqual(self.fake.row['sms_url'], self.original['sms_url'])
        self.assertTrue(self.settings.restore())
        self.assertEqual(self.fake.row, self.original)
        self.assertFalse(self.settings.pending.exists())
        self.assertEqual((self.run / 'voice-before.json').stat().st_mode & 0o777, 0o600)

    def test_restore_runs_after_a_crash_at_next_start(self):
        self.install()
        # Simulate process death: no finally/restore; a fresh startup discovers the journal.
        with patch.object(trial, 'ROOT', self.root), \
             patch.object(trial, 'credentials', return_value=(secret, LINE)), \
             patch.object(trial, 'TwilioRestAdapter', return_value=self.fake), \
             patch.dict(trial.os.environ), patch.object(trial, 'emit'):
            self.assertTrue(trial.recover_voice())
            self.assertFalse(trial.recover_voice())
        self.assertEqual(self.fake.row, self.original)

    def test_down_recovers_a_crashed_listener_without_starting_a_runtime(self):
        self.install()
        previous_umask = os.umask(0o077)
        try:
            with patch.object(trial, 'ROOT', self.root), \
                 patch.object(trial, 'credentials', return_value=(secret, LINE)), \
                 patch.object(trial, 'TwilioRestAdapter', return_value=self.fake), \
                 patch.dict(trial.os.environ), patch.object(trial, 'emit'), \
                 patch.object(trial.sys, 'argv', ['run.py', 'down']), \
                 patch.object(trial, 'Runtime', side_effect=AssertionError('must not start')):
                trial.main()
        finally:
            os.umask(previous_umask)
        self.assertEqual(self.fake.row, self.original)
        self.assertFalse(self.settings.pending.exists())

    def test_interrupted_wait_restores_before_runtime_exit(self):
        runtime = Mock()
        runtime.__enter__ = Mock(return_value=runtime)
        def exit_runtime(*args):
            self.assertEqual(self.fake.row, self.original)
            self.assertFalse(self.settings.pending.exists())
        runtime.__exit__ = Mock(side_effect=exit_runtime)
        runtime.svc.url.side_effect = lambda path: 'https://offline.test/t/test' + path
        guard = Mock(call_id=None)
        args = SimpleNamespace(minutes=5, i_am_the_coordinator=True)
        self.fake.deadline = trial.time.monotonic() + 300
        with patch.object(trial, 'ROOT', self.root), \
             patch.object(trial, 'RingbackAdapter', return_value=self.fake), \
             patch.object(trial, 'gateway_check'), patch.object(trial, 'balance', return_value={'usd': '12'}), \
             patch.object(trial, 'monitor', return_value=contextlib.nullcontext()), \
             patch.object(trial, 'Runtime', return_value=runtime) as make_runtime, \
             patch.object(trial, 'InboundTrial', return_value=guard), patch.object(trial, 'emit'), \
             patch.object(trial.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                trial.run_listen(args, self.run, secret, LINE)
        cfg = make_runtime.call_args.args[1]
        self.assertEqual(cfg.forwarding_number, trial.ALLOW[1])
        self.assertFalse(cfg.inbound_ring_owner)
        self.assertEqual(cfg.owner_number, trial.ALLOW[0])
        self.assertEqual(cfg.policy['blocked_destinations'], [trial.BORA])
        self.assertFalse(guard.accepting)
        runtime.__exit__.assert_called_once()

    def test_ambiguous_install_response_still_restores(self):
        def lose_response():
            raise TimeoutError('offline lost response after remote commit')
        self.fake.after_write = lose_response
        with self.assertRaises(TimeoutError):
            self.install()
        self.fake.after_write = None
        self.assertTrue(self.settings.restore())
        self.assertEqual(self.fake.row, self.original)

    def test_restore_failure_retains_journal_for_retry(self):
        self.install()
        self.fake.fail_write = True
        with self.assertRaises(TimeoutError):
            self.settings.restore()
        self.assertTrue(self.settings.pending.exists())
        self.fake.fail_write = False
        self.assertTrue(VoiceSettings(self.fake, LINE, self.root).restore())
        self.assertEqual(self.fake.row, self.original)

    def test_never_touches_another_number_even_on_tampered_journal(self):
        for bad in (trial.BORA, trial.ALLOW[0], trial.ALLOW[1], '+17755550100'):
            with self.assertRaises(ValueError):
                VoiceSettings(self.fake, bad, self.root)
        self.assertEqual(self.fake.operations, [])
        self.install()
        data = json.loads(self.settings.pending.read_text())
        data['sid'] = 'PN' + 'c' * 32
        self.settings.pending.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):
            self.settings.restore()
        for path, params in self.fake.operations:
            self.assertIn(path, ('IncomingPhoneNumbers.json?' + urlencode({'PhoneNumber': LINE}),
                                 'IncomingPhoneNumbers/' + PN + '.json'))
            if params:
                self.assertEqual(set(dict(params)), set(VOICE_FIELDS))

    def test_configured_line_passes_and_follows_the_configuration(self):
        self.assertEqual(VoiceSettings(self.fake, LINE, self.root).line, LINE)
        other = self.root / 'other_line_number'
        other.write_text(OTHER + '\n')
        with patch.object(inbound, 'LINE_NUMBER_PATH', other):
            self.assertEqual(VoiceSettings(self.fake, OTHER, self.root).line, OTHER)
            with self.assertRaisesRegex(ValueError, '^only the configured plugin line may be configured$'):
                VoiceSettings(self.fake, LINE, self.root)
        self.assertEqual(self.fake.operations, [])

    def test_another_number_refuses(self):
        for bad in (OTHER, trial.BORA, trial.ALLOW[0], trial.ALLOW[1], LINE[1:], LINE + '0', ' ' + LINE, '', None):
            with self.assertRaisesRegex(ValueError, '^only the configured plugin line may be configured$', msg=repr(bad)):
                VoiceSettings(self.fake, bad, self.root)
        self.assertEqual(self.fake.operations, [])

    def test_missing_unreadable_or_invalid_configured_line_refuses(self):
        unreadable = self.root / 'a_directory'
        unreadable.mkdir()
        invalid = self.root / 'invalid_line_number'
        invalid.write_text('not-a-number\n')
        for path in (self.root / 'missing', unreadable, invalid):
            with patch.object(inbound, 'LINE_NUMBER_PATH', path), \
                 self.assertRaisesRegex(ValueError, '^the configured plugin line is missing or unreadable$', msg=path.name):
                VoiceSettings(self.fake, LINE, self.root)
        self.assertEqual(self.fake.operations, [])

    def test_crash_recovery_refuses_without_a_configured_line(self):
        self.install()
        with patch.object(inbound, 'LINE_NUMBER_PATH', self.root / 'missing'), \
             patch.object(trial, 'ROOT', self.root), \
             patch.object(trial, 'credentials', return_value=(secret, LINE)), \
             patch.object(trial, 'TwilioRestAdapter', return_value=self.fake), \
             patch.dict(trial.os.environ), patch.object(trial, 'emit'):
            before = list(self.fake.operations)
            with self.assertRaises(ValueError):
                trial.recover_voice()
            self.assertEqual(self.fake.operations, before)
        self.assertTrue(self.settings.pending.exists())

    def test_foreign_lookup_response_is_never_followed(self):
        self.fake.row['phone_number'] = trial.BORA
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(len(self.fake.operations), 1)
        self.assertIsNone(self.fake.operations[0][1])

    def test_listen_flags_and_duration(self):
        for value in (0, 11, True, -1):
            with self.assertRaises(trial.Stop):
                trial.validate_listen(value, True)
        with self.assertRaises(trial.Stop):
            trial.validate_listen(5, False)
        for value in (1, 10):
            trial.validate_listen(value, True)


class IngressTests(unittest.TestCase):
    def test_one_allowlisted_inbound_retries_status_auth_and_owner_ring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = trial.config(LINE, 5)
            cfg.forwarding_number, cfg.inbound_ring_owner = trial.ALLOW[1], True
            adapter = FakeNumber()
            adapter.minutes, adapter.to = 5, trial.ALLOW[0]
            store = trial.Store(root / 'line.db')
            svc = trial.CallService(cfg=cfg, store=store, adapter=adapter,
                driver_factory=lambda _: trial.QuietDriver(), lock_dir=root / 'locks', get_secret=secret,
                now_utc=lambda: datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
            guard = InboundTrial(svc, trial.ALLOW)
            try:
                svc.start(port=0)
                guard.mount_status()
                base = 'http://127.0.0.1:' + str(svc.server.port)
                def post(path, values):
                    req = urllib.request.Request(base + path, data=urlencode(values).encode(),
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
                    try:
                        with urllib.request.urlopen(req, timeout=5) as response:
                            return response.status, response.read().decode()
                    except urllib.error.HTTPError as exc:
                        return exc.code, ''
                sid = 'CA' + 'a' * 32
                def frame(sid, caller):
                    adapter.records[sid] = dict(sid=sid, account_sid=ACCOUNT, direction='inbound',
                        to=LINE, **{'from': caller}, status='in-progress')
                    return dict(AccountSid=ACCOUNT, CallSid=sid, To=LINE, From=caller,
                                ForwardedFrom=trial.ALLOW[1])
                path = svc.auth.prefix() + '/twilio/voice/inbound'
                rejected = post(path, frame('CA' + 'b' * 32, '+17755550123'))
                self.assertIn('Reject', rejected[1])
                accepted = post(path, frame(sid, trial.ALLOW[0]))
                self.assertIn('ConversationRelay', accepted[1])
                self.assertEqual(post(path, frame(sid, trial.ALLOW[0])), accepted)
                call_id = guard.call_id
                session = svc.sessions[call_id]
                self.assertTrue(svc.run(session.ring_owner('offline-trial')))
                self.assertEqual(adapter.forms[0]['To'], trial.ALLOW[0])
                self.assertEqual(adapter.forms[0]['TimeLimit'], '300')
                # Terminal business status must be REST-confirmed by the production handler.
                status = svc.auth.prefix() + '/trial/status'
                fields = dict(AccountSid=ACCOUNT, CallSid=sid, CallStatus='completed')
                self.assertEqual(post(status, fields)[0], 204)
                self.assertFalse(store.get_call(call_id).state.terminal)
                adapter.records[sid]['status'] = 'completed'
                self.assertEqual(post(status, fields)[0], 204)
                self.assertTrue(store.get_call(call_id).state.terminal)
                self.assertIn('Reject', post(path, frame('CA' + 'c' * 32, trial.ALLOW[1]))[1])
                self.assertEqual(len(store.list_calls()), 1)
                fields['AccountSid'] = 'AC' + 'f' * 32
                self.assertEqual(post(status, fields)[0], 403)
                self.assertEqual(post('/t/wrong/trial/status', fields)[0], 404)
            finally:
                svc.stop()
                store.close()
