"""Owner PIN staging and digit-free exports; all providers are offline fakes."""
import contextlib
import io
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_harness import trial, LINE


class OwnerPinTests(unittest.TestCase):
    def stage(self, home):
        folder = home/'.hermes-line'; folder.mkdir()
        (folder/'twilio_account_sid').write_text('AC'+'0'*32)
        (folder/'twilio_api_key.json').write_text(json.dumps({'api_key_sid':'SK'+'0'*32,'api_key_secret':'x'*32}))
        (folder/'twilio_line_number').write_text(LINE)
        return folder

    def test_absent_and_default_do_not_enable_owner_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); folder = self.stage(home)
            with patch.object(Path, 'home', return_value=home), patch.dict(trial.os.environ, HERMES_HOME=str(home/'trial'), HERMES_LINE_OWNER_PIN='ambient-fixture'):
                self.assertEqual(trial.owner_pin_status(), 'absent')
                getter, _ = trial.credentials(owner_pin=True)
                self.assertFalse(getter('HERMES_LINE_OWNER_PIN'))
                (folder/'owner_pin').write_text('bad shape')
                getter, _ = trial.credentials()  # non-listen never reads PIN
                self.assertFalse(getter('HERMES_LINE_OWNER_PIN'))
                self.assertEqual(trial.owner_pin_status(), 'present')

    def test_bad_shape_and_permissions_are_refused_without_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pinfile = folder/'owner_pin'
            for raw in ('123', '123456789', '12a4', '１２３４', '1234\n5678\n', ' 1234', ''):
                pinfile.write_text(raw); pinfile.chmod(0o600)
                with self.assertRaises(trial.Stop) as caught:trial.load_owner_pin(folder)
                self.assertEqual(str(caught.exception), 'owner PIN file must contain one line of 4-8 digits')
            pinfile.write_text('1234'); pinfile.chmod(0o644)
            with self.assertRaises(trial.Stop):trial.load_owner_pin(folder)

    def test_pin_once_through_real_scope_and_plugin_then_grep_every_export(self):
        sys.path.insert(0, str(trial.REPO/'tests/l1'))
        from harness import Line, OWNER, SECRETS, wait_for
        # Synthetic test fixture only, never an owner credential.
        pin = '693728'
        failed = '314159'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); home = root/'staging'; home.mkdir()
            folder = self.stage(home)
            pinfile = folder/'owner_pin'; pinfile.write_text(pin+'\n'); pinfile.chmod(0o600)
            output = io.StringIO()
            handler = logging.StreamHandler(output)
            logging.getLogger().addHandler(handler)
            try:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), patch.object(Path, 'home', return_value=home), patch.dict(trial.os.environ, HERMES_HOME=str(home/'trial'), HERMES_LINE_OWNER_PIN='ambient-fixture'):
                    getter, _ = trial.credentials(owner_pin=True)
                    pinfile.unlink()  # secret queries and evidence cannot reread
                    self.assertEqual(getter('HERMES_LINE_OWNER_PIN'), pin)
                    self.assertEqual(getter('HERMES_LINE_OWNER_PIN'), pin)
                    secrets = dict(SECRETS, HERMES_LINE_OWNER_PIN=getter('HERMES_LINE_OWNER_PIN'))
                    line = Line(owner_name='Can', secrets=secrets)
                    try:
                        sid = 'CA'+'1'*32
                        _, body = line.post('/twilio/voice/inbound', {'CallSid':sid,'From':OWNER,'To':LINE})
                        call = wait_for(lambda: [c for c in line.store.list_calls() if c.provider_sid == sid])[0]
                        relay = line.relay(body); relay.send({'type':'setup','callSid':sid})
                        session = wait_for(lambda: line.session(call.id))
                        for digit in failed+'#':relay.send({'type':'dtmf','digit':digit})
                        wait_for(lambda: session.pin_failures == 1)
                        for digit in pin+'#':relay.send({'type':'dtmf','digit':digit})
                        wait_for(lambda: session.owner_verified)
                        line.store.log_event(call.id, None, 'owner_request', {'task_id':'task_fixture'})
                        line.store.update_call(call.id, owner_sid='CA'+'2'*32)
                        proxy = Mock(wraps=line.store); proxy.close = Mock()
                        with patch.object(trial, 'Store', return_value=proxy), patch.object(trial, 'REPO', root):
                            path = trial.evidence(call.id, root/'run')
                        data = json.loads((root/path/'trial.json').read_text())
                        self.assertEqual(data['owner_pin_attempts'], {'count':2,'results':['fail','pass']})
                        self.assertTrue(data['owner_mode_entered'])
                        self.assertEqual(data['tasks_created_from_call'], ['task_fixture'])
                        self.assertEqual(len(data['bridge_legs']), 1)
                        trial.emit({'evidence':path})
                    finally:line.close()
                (root/'captured.log').write_text(output.getvalue())
                exports = list((root/'evidence').rglob('*')) + [root/'captured.log']
                for path in exports:
                    if path.is_file():
                        for secret in (pin, failed, ' '.join(pin)):
                            self.assertNotIn(secret, path.read_text(), str(path))
            finally:logging.getLogger().removeHandler(handler)
