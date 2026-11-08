"""Offline spending, key scoping and audio-free Grok protocol checks."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from test_harness import trial, LINE
from grok_budget import GrokRun, BudgetError, summary, dry_connect, CEILING


class BudgetTests(unittest.TestCase):
    def test_reservation_persists_across_crash_and_blocks_over_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = GrokRun(root, root/'first', 1200)
            self.assertEqual(summary(root)['pending_runs'], 1)
            with self.assertRaises(BudgetError):
                GrokRun(root, root/'second', 1200)
            # No finish = crash; a fresh reader still counts the whole reservation.
            self.assertGreater(float(summary(root)['accounted_usd']), 1.6)
            a.finish()
            self.assertEqual(summary(root)['pending_runs'], 0)

    def test_measured_time_and_text_settle_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = GrokRun(root, root/'run', 60)
            run.text_input()
            run.grok_seconds, run.stt_seconds = 30, 60
            run.finish(); before = summary(root)
            run.finish()
            self.assertEqual(summary(root), before)
            self.assertAlmostEqual(float(before['accounted_usd']), .04+.20/60+.004, places=7)

    def test_text_cannot_spend_reserved_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = GrokRun(root, root/'run', 1295)
            with self.assertRaises(BudgetError):
                run.text_input()

    def test_transport_defaults_and_grok_pinning(self):
        self.assertEqual(trial.config(LINE).voice['transport'], 'conversation_relay')
        cfg = trial.config(LINE, transport='grok')
        self.assertEqual(cfg.voice['transport'], 'media_streams')
        self.assertEqual(cfg.voice['reasoning_effort'], 'none')

    def test_key_read_once_and_only_in_secret_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            folder = home/'.hermes-line'; folder.mkdir()
            (folder/'twilio_account_sid').write_text('AC'+'0'*32)
            (folder/'twilio_api_key.json').write_text(json.dumps({'api_key_sid':'SK'+'0'*32,'api_key_secret':'x'*32}))
            (folder/'twilio_line_number').write_text(LINE)
            key = folder/'xai_api_key'; key.write_text('offline-key\n'); key.chmod(0o600)
            with patch.object(Path, 'home', return_value=home), patch.dict(trial.os.environ, HERMES_HOME=str(home/'trial')):
                getter, _ = trial.credentials(grok=True)
                key.unlink()  # repeated secret requests cannot re-read the staging file
                self.assertEqual(getter('XAI_API_KEY'), 'offline-key')
                self.assertEqual(getter('XAI_API_KEY'), 'offline-key')
                self.assertIsNone(getter('HERMES_LINE_TWILIO_AUTH_TOKEN'))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_socket_cutoff_closes_and_audio_is_prohibited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = GrokRun(root, root/'dry', .05, dry=True)
            ws = Mock()
            from unittest.mock import AsyncMock
            ws.close = AsyncMock()
            async def connect(*args):return ws
            with patch('plugin.line.voice.provider.websockets_connector', side_effect=connect):
                sock = await run.connector('wss://api.x.ai/v1/realtime?model=fixture', {})
                with self.assertRaises(BudgetError):
                    await sock.send(json.dumps({'type': 'input_audio_buffer.append', 'audio': 'fixture'}))
                with self.assertRaises(BudgetError):
                    await sock.send(b'audio')
                await asyncio.sleep(.08)
                self.assertTrue(sock.closed)
                self.assertEqual(ws.close.await_count, 1)
            run.finish()

    async def test_one_audio_free_connect_uses_production_session_config_and_records_only_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = GrokRun(root, root/'dry', 5, dry=True)
            class Socket:
                def __init__(self):
                    self.sent = []
                    self.frames = [json.dumps({'type':'session.created','session':{'id':'private-session','model':'model'}}),
                                   json.dumps({'type':'session.updated','session':{}})]
                async def send(self, data):self.sent.append(json.loads(data))
                async def recv(self):return self.frames.pop(0)
                async def close(self):pass
            ws = Socket()
            async def connect(*args):return ws
            with patch('plugin.line.voice.provider.websockets_connector', side_effect=connect) as connector:
                result = await dry_connect(run, lambda _: 'offline-key')
                self.assertTrue(result['session_update_accepted'])
                self.assertEqual(connector.call_count, 1)
                self.assertEqual([e['type'] for e in ws.sent], ['session.update'])
                self.assertEqual(ws.sent[0]['session']['audio']['input']['format']['type'], 'audio/pcmu')
                with self.assertRaises(BudgetError):
                    await run.connector('wss://api.x.ai/v1/stt?encoding=mulaw', {})
            run.finish()
            data = (root/'dry/grok-evidence.json').read_text()
            self.assertNotIn('private-session', data)
            self.assertIn('session.id', data)
            self.assertGreater(float(summary(root)['accounted_usd']), 0)
