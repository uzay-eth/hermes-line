"""N11 cancellation, delayed settlement and process-death regressions (offline)."""
import asyncio
import contextlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from test_harness import trial
from grok_budget import GrokRun, BudgetError, summary, reconcile_crashes


class Wire:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
    async def close(self):
        self.entered.set()
        await self.release.wait()


class CloseTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, root, kind='realtime'):
        run = GrokRun(root, root/'run', 180)
        wire = Wire()
        async def connect(*args):return wire
        with patch('plugin.line.voice.provider.websockets_connector', side_effect=connect):
            sock = await run.connector('wss://api.x.ai/v1/' + kind + '?fixture', {})
        sock.start = time.monotonic() - 120
        return run, wire, sock

    async def test_close_all_joins_accounting_and_finish_refuses_inflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, wire, sock = await self.make(root)
            first = asyncio.create_task(sock.close())
            await wire.entered.wait()
            self.assertGreaterEqual(run.grok_seconds, 120)
            waiter = asyncio.create_task(run.close_all())
            await asyncio.sleep(.01)
            self.assertFalse(waiter.done())
            with self.assertRaises(BudgetError):run.finish()
            self.assertEqual(summary(root)['pending_runs'], 1)
            wire.release.set()
            await asyncio.gather(first, waiter)
            run.finish()
            self.assertGreaterEqual(float(summary(root)['accounted_usd']), .16)
            seconds = run.grok_seconds
            await run.close_all(); run.finish()
            self.assertEqual(run.grok_seconds, seconds)  # no double booking

    async def test_cancelled_close_fallback_and_late_real_close_only_raise_charge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, wire, sock = await self.make(root)
            closer = asyncio.create_task(sock.close())
            await wire.entered.wait()
            closer.cancel()
            with contextlib.suppress(asyncio.CancelledError):await closer
            self.assertFalse(sock.closing)
            self.assertFalse(sock.close_complete)
            run.finish()
            before = float(summary(root)['accounted_usd'])
            self.assertGreaterEqual(before, .16)
            # A different invocation reserved funds since fallback settlement.
            next_run = GrokRun(root, root/'next', 60)
            wire.release.set()
            await asyncio.sleep(.01)
            await sock.close()
            self.assertGreaterEqual(float(run.data['runs']['run']['usd']), before)
            self.assertIn('next', json.loads((root/'xai-spend.json').read_text())['runs'])
            after = float(run.data['runs']['run']['usd'])
            run.finish()
            self.assertEqual(float(run.data['runs']['run']['usd']), after)
            next_run.finish()

    async def test_stt_cancelled_close_is_charged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, wire, sock = await self.make(root, 'stt')
            closer = asyncio.create_task(sock.close())
            await wire.entered.wait(); closer.cancel()
            with contextlib.suppress(asyncio.CancelledError):await closer
            run.finish()
            self.assertGreaterEqual(run.stt_seconds, 120)
            self.assertGreaterEqual(float(summary(root)['accounted_usd']), .20/30)

    async def test_setup_in_flight_cannot_release_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = GrokRun(root, root/'run', 180)
            run.connecting.add('grok')
            with self.assertRaises(BudgetError):run.finish()
            self.assertEqual(summary(root)['pending_runs'], 1)


class ShutdownTests(unittest.TestCase):
    def test_service_loop_teardown_with_never_started_close_books_wall_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = GrokRun(root, root/'run', 180)
            def service():
                loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                async def setup():
                    async def connect(*args):return Wire()
                    with patch('plugin.line.voice.provider.websockets_connector', side_effect=connect):
                        sock = await run.connector('wss://api.x.ai/v1/realtime?fixture', {})
                    sock.start = time.monotonic() - 120
                    return sock
                sock = loop.run_until_complete(setup())
                closer = loop.create_task(sock.close())
                # Model runner.py cancellation before close gets its first turn.
                for task in asyncio.all_tasks(loop):task.cancel()
                loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
                loop.close()
            thread = threading.Thread(target=service); thread.start(); thread.join(5)
            self.assertFalse(thread.is_alive())
            runtime = trial.Runtime(root/'runtime', trial.config('+17755550100'), trial.TrialAdapter(lambda _: None), lambda _: None, credit=run)
            runtime.svc = Mock(); runtime.svc.loop = None
            runtime.__exit__(None, None, None)
            runtime.svc.stop.assert_called_once()
            self.assertGreaterEqual(float(summary(root)['accounted_usd']), .16)

    def test_crash_mid_call_next_preflight_charges_worst_case_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = str(Path(trial.__file__).parent)
            script = """
import os, asyncio
from pathlib import Path
from grok_budget import GrokRun
from unittest.mock import patch
async def main():
 r=GrokRun(Path(ROOT),Path(ROOT)/'crashed',180)
 r.text_input()
 async def connect(*a):return object()
 with patch('plugin.line.voice.provider.websockets_connector',side_effect=connect):
  await r.connector('wss://api.x.ai/v1/realtime?fixture',{})
 os._exit(0)
asyncio.run(main())
"""
            proc = subprocess.run([sys.executable, '-B', '-c', 'import sys; sys.path.insert(0,' + repr(source) + '); ROOT=' + repr(str(root)) + '\n' + script], capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 0)
            previous_umask = trial.os.umask(0o077)
            try:
                with patch.object(trial, 'ROOT', root), patch.object(trial.sys, 'argv', ['run.py', 'preflight']), patch.dict(trial.os.environ):
                    def before_any_provider(*args):
                        report = summary(root)
                        self.assertEqual(report['pending_runs'], 0)
                        self.assertEqual(float(report['accounted_usd']), .254)
                        self.assertEqual(json.loads((root/'xai-spend.json').read_text())['runs']['crashed']['state'], 'reconciled_crash')
                    with patch.object(trial, 'execute', side_effect=before_any_provider):trial.main()
            finally:trial.os.umask(previous_umask)
            self.assertEqual(reconcile_crashes(root), [])
