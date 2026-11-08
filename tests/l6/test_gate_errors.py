"""B2: actual quiet-hours rejection through call/hold CLI paths, without network."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from test_harness import trial, secret, LINE
from test_hold import ROW
from plugin.line.engine.contracts import GateRejected


class OfflineRuntime:
    def __init__(self, root, cfg, adapter, getter, **kwargs):
        self.store=trial.Store(root/'home/plugin-data/hermes-line/line.db')
        self.svc=trial.CallService(cfg=cfg,store=self.store,adapter=adapter,
            driver_factory=Mock(side_effect=AssertionError('no model')), lock_dir=root/'locks',get_secret=getter,
            now_utc=lambda:datetime(2026,11,5,12,tzinfo=timezone.utc))
    def __enter__(self):return self
    def __exit__(self,*args):self.store.close()


class GateErrorTests(unittest.TestCase):
    def test_call_and_hold_gate_reason_to_stdout_and_run_directory(self):
        for command in ('call','hold'):
            with self.subTest(command=command),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);capture=io.StringIO()
                (root/(command+'-recovery.lock')).touch()  # matching file must never be selected
                argv=['run.py',command,'--i-am-the-coordinator']
                argv += ['--to',trial.ALLOW[1],'--task','Fixture'] if command=='call' else ['--business',ROW['number']]
                old=trial.os.umask(0o077)
                try:
                    with patch.object(trial,'ROOT',root),patch.object(trial.sys,'argv',argv),patch.dict(trial.os.environ), \
                         patch.object(trial,'credentials',return_value=(secret,LINE)),patch.object(trial,'gateway_check'), \
                         patch.object(trial,'Runtime',OfflineRuntime),patch.object(trial,'balance',return_value={'usd':'20'}), \
                         patch('hold.approval',return_value=dict(ROW)),patch('hold.HoldAdapter.pricing'), \
                         patch.object(trial.TwilioRestAdapter,'_post',side_effect=AssertionError('no Twilio API')), \
                         contextlib.redirect_stdout(capture):
                        with self.assertRaises(GateRejected) as caught:trial.main()
                    self.assertTrue(caught.exception._trial_error_reported)
                finally:trial.os.umask(old)
                rows=[json.loads(s) for s in capture.getvalue().splitlines()]
                gate=rows[-1]
                self.assertEqual(gate['gate'],'quiet_hours')
                self.assertEqual(gate['error'],'quiet_hours: '+gate['reason'])
                self.assertIn('outbound calls are blocked during quiet hours',gate['reason'])
                runs=[p for p in root.glob(command+'-*') if p.is_dir()]
                self.assertEqual(len(runs),1)
                run=runs[0]
                self.assertEqual(json.loads((run/'error.json').read_text()),gate)

    def test_gate_details_are_masked_and_unknown_exceptions_never_stringified(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()) as output:
            error=GateRejected('destination','phone +18475550101 verification code 482193 '+
                'wss://host/t/privatePath/relay?t=privateToken')
            payload=trial.report_error(error,Path(tmp))
            blob=output.getvalue()+(Path(tmp)/'error.json').read_text()
            for raw in ('+18475550101','482193','privatePath','privateToken'):self.assertNotIn(raw,blob)
            unknown=trial.error_payload(ValueError('Bearer credential-never-log'))
            self.assertEqual(unknown,{'ok':False,'error':'ValueError'})
