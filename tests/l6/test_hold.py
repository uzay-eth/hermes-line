"""Hold admission, two-leg boundaries, budgets, real-engine offline fallback and live events."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_harness import trial, secret
from hold import approval, HoldAdapter, Budget, Events, FALLBACK, CAP, dry_run, recover, task_args, Events
from plugin.line.engine.contracts import Role
from plugin.line.engine.telephony import ProviderError

BUSINESS = '+18005550100'  # offline sample only
ROW = dict(number=BUSINESS,label='Offline airline',approved_by='fixture coordinator',
    expires='2099-01-01T00:00:00Z',max_hold_minutes=45,owner_ring_timeout_s=20, intro_names=['Sample Air'], ivr_category='baggage',
    fallback_question='checked-bag fee for a domestic economy ticket',forbid=['callback','payment','flight change'])


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'run';self.root.mkdir()
        self.path=self.root/'approved.json';self.path.write_text(json.dumps([ROW]));self.path.chmod(0o600)
        self.row=approval(trial,BUSINESS,self.path)
        self.events=Events(trial,self.root)
        self.now=[0.0]
        self.adapter=HoldAdapter(trial,secret,self.row,'+17755550166',self.root,self.events,real=False,clock=lambda:self.now[0])
        self.adapter.task_id='task_fixture';self.adapter.call_id='call_fixture'

    def dispatch(self,role,to):
        self.adapter.context.permit=SimpleNamespace(role=role, task_id=self.adapter.task_id,call_id=self.adapter.call_id)
        return self.adapter._create_call(to=to,from_number=self.adapter.line,answer_url='https://offline/answer',
            status_url='https://offline/status',timeout_s=30,time_limit_s=2700)

    def test_admission_exact_expiring_read_only_and_modes(self):
        before=self.path.read_bytes()
        self.assertTrue(self.row['fallback_question'].endswith('?'))
        self.assertEqual(self.path.read_bytes(),before)
        for number in (trial.BORA,trial.ALLOW[0],'911','+18005550101'):
            with self.assertRaises(trial.Stop):approval(trial,number,self.path)
        with self.assertRaises(trial.Stop):approval(trial,BUSINESS,self.path,datetime(2100,1,1,tzinfo=timezone.utc))
        self.path.chmod(0o644)
        with self.assertRaises(trial.Stop):approval(trial,BUSINESS,self.path)

    def test_roles_no_redial_time_remaining_and_human_before_owner_create(self):
        capture=io.StringIO()
        original=self.adapter._post
        def post(path,params):
            if path=='Calls.json' and dict(params)['To']==trial.ALLOW[0]:
                self.assertIn('human_detected',self.events.sent)
                self.assertIn('human_detected',(self.root/'live-events.jsonl').read_text())
            return original(path,params)
        with contextlib.redirect_stdout(capture),patch.object(self.adapter,'_post',side_effect=post):
            self.dispatch(Role.BUSINESS,BUSINESS)
            self.now[0]=600
            self.events.emit('human_detected')
            self.dispatch(Role.OWNER,trial.ALLOW[0])
        self.assertEqual(self.adapter.forms[0]['TimeLimit'],'2700')
        self.assertEqual(self.adapter.forms[1]['TimeLimit'],'2100')
        self.assertEqual(self.adapter.forms[1]['Timeout'],'20')
        self.assertEqual(capture.getvalue(),(self.root/'live-events.jsonl').read_text())
        with patch.object(self.adapter,'_post',side_effect=AssertionError('API')):
            for role,to in ((Role.BUSINESS,BUSINESS),(Role.OWNER,trial.ALLOW[0]),
                            (Role.BUSINESS,trial.BORA),(Role.OWNER,BUSINESS),(Role.BUSINESS,self.adapter.line)):
                with self.assertRaises(ProviderError):self.dispatch(role,to)

    def test_cap_and_insufficient_owner_window_refuse_before_create(self):
        with contextlib.redirect_stdout(io.StringIO()):self.dispatch(Role.BUSINESS,BUSINESS)
        self.now[0]=CAP-24
        self.events.emit=lambda *a,**k:None
        with self.assertRaises(ProviderError):self.dispatch(Role.OWNER,trial.ALLOW[0])
        self.now[0]=CAP
        with self.assertRaises(ProviderError):self.adapter.tick()
        self.assertEqual(len(self.adapter.forms),1)

    def test_budget_polls_60_seconds_and_delayed_billing_estimate_stops(self):
        clock=[0.0];read=Mock(return_value={'usd':'20'})
        budget=Budget(dict(FALLBACK),read,lambda:clock[0]);budget.start=0
        budget.check();clock[0]=59;budget.check();self.assertEqual(read.call_count,1)
        clock[0]=60;budget.check();self.assertEqual(read.call_count,2)
        clock[0]=61;read.return_value={'usd':'4.10'}
        with self.assertRaises(ProviderError):budget.check(force=True)
        with self.assertRaises(ProviderError):Budget(dict(FALLBACK),lambda:{'usd':'6'},lambda:0).check()
        clock[0]=62;read.side_effect=TimeoutError
        with self.assertRaises(TimeoutError):budget.check(force=True)

    def test_cleanup_ends_both_and_exact_conference_and_records_failures(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.dispatch(Role.BUSINESS,BUSINESS);self.events.emit('human_detected')
            self.dispatch(Role.OWNER,trial.ALLOW[0])
        self.assertEqual(self.adapter.cleanup('hl-call_fixture'),[])
        self.assertTrue(self.adapter.conference_ended)
        self.assertTrue(all(r['status']=='completed' for r in self.adapter.records.values()))
        with patch.object(self.adapter,'end_call',side_effect=TimeoutError):
            self.assertEqual(len(self.adapter.cleanup(None)),2)

    def test_pricing_permission_failure_keeps_s2_times_one_point_five(self):
        with patch.object(self.adapter,'_auth',return_value=('account','fixture')),patch.object(self.adapter,'_open',side_effect=PermissionError):
            self.adapter.pricing()
        self.assertEqual(self.adapter.rates,FALLBACK)
        self.assertIn('unavailable',self.adapter.rate_basis)

    def test_real_engine_dry_menu_hold_human_timeout_fallback_end_no_external_io(self):
        class Capture(io.StringIO):
            def __init__(self):super().__init__();self.flushed=[]
            def flush(self):
                lines=self.getvalue().splitlines()
                if lines:self.flushed.append(lines[-1])
                super().flush()
        capture=Capture()
        original=HoldAdapter._post
        def boundary(adapter,path,params):
            if path=='Calls.json' and dict(params)['To']==trial.ALLOW[0]:
                emitted=[json.loads(line)['event'] for line in (self.root/'live-events.jsonl').read_text().splitlines()]
                self.assertIn('human_detected',emitted)  # before the actual real-engine owner create
            return original(adapter,path,params)
        with contextlib.redirect_stdout(capture),patch.object(trial,'credentials',side_effect=AssertionError('credential read')), \
             patch.object(trial,'gateway_check',side_effect=AssertionError('gateway')),patch.object(HoldAdapter,'_post',new=boundary):
            report=dry_run(trial,self.row,self.root)
        self.assertEqual(report['outcome'],'fallback_question_answered')
        rows=[json.loads(line) for line in (self.root/'live-events.jsonl').read_text().splitlines()]
        self.assertEqual([r['event'] for r in rows],['business_dialing','new_party_question','human_detected','owner_ring','fallback_question','ended'])
        self.assertEqual([json.loads(line) for line in capture.flushed[:len(rows)]], rows)
        self.assertEqual(report['metrics']['false_human_count'],0)
        self.assertIsNotNone(report['metrics']['menu_s'])
        self.assertIsNotNone(report['metrics']['hold_s'])
        self.assertIsNotNone(report['metrics']['transcript_to_human_s'])

    def test_ambiguous_provider_create_is_recorded_and_never_retried(self):
        original=self.adapter._post
        def fail(path,params):
            if path=='Calls.json':raise TimeoutError('fixture')
            return original(path,params)
        with patch.object(self.adapter,'_post',side_effect=fail) as boundary:
            with self.assertRaises(TimeoutError):self.dispatch(Role.BUSINESS,BUSINESS)
            count=boundary.call_count
            with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS,BUSINESS)
            self.assertEqual(boundary.call_count,count)
        self.assertTrue((self.root/'hold-journal.json').exists())

    def test_cleanup_never_completes_foreign_conference(self):
        with patch.object(self.adapter,'_post',return_value={'conferences':[{'sid':'CF'+'0'*32,'friendly_name':'foreign'}]}) as boundary:
            failures=self.adapter.cleanup('hl-call_fixture')
        self.assertEqual(len(failures),1)
        self.assertEqual(boundary.call_count,1)
        self.assertIsNone(boundary.call_args.args[1])

    def test_recovery_uses_known_journal_legs_and_removes_on_success(self):
        sid='CA'+'0'*32
        run_dir=self.root/'hold-crashed';run_dir.mkdir()
        trial.save(self.root/'hold-recovery.json',{'run_dir':str(run_dir),'sids':[sid],'call_id':'call_abcd'})
        from hold import HoldAdapter as RealAdapter
        def fake(api,getter,row,line,root,events,real):
            adapter=RealAdapter(api,getter,{},line,root,events,real=False)
            adapter.records[sid]={'sid':sid,'status':'in-progress'}
            self.recovered=adapter
            return adapter
        with patch.object(trial,'ROOT',self.root),patch.object(trial,'credentials',return_value=(secret,'+17755550166')), \
             patch('hold.HoldAdapter',side_effect=fake),patch.dict(trial.os.environ),contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(recover(trial))
        self.assertEqual(self.recovered.records[sid]['status'],'completed')
        self.assertFalse((self.root/'hold-recovery.json').exists())

    def test_offline_dry_refuses_pending_provider_recovery_without_credentials(self):
        (self.root/'voice-restore.json').write_text('{}')
        old=trial.os.umask(0o077)
        try:
            with patch.object(trial,'ROOT',self.root),patch('hold.approval',return_value=self.row), \
                 patch.object(trial.sys,'argv',['run.py','hold','--business',BUSINESS,'--dry']), \
                 patch.object(trial,'credentials',side_effect=AssertionError('credential read')):
                with self.assertRaises(trial.Stop):trial.main()
        finally:trial.os.umask(old)


    def test_task_intro_names_use_approval_list_not_free_text_label(self):
        row = dict(self.row, label='Not an approved greeting name')
        self.assertEqual(task_args(trial,row)['intro_names'], ['Sample Air'])

    def test_missing_intro_names_means_empty_list_fail_closed(self):
        row = dict(self.row, label='Delta Air Lines')
        row.pop('intro_names',None)
        self.assertEqual(task_args(trial,row)['intro_names'], [])


    def test_ivr_category_passed_from_approval_and_omitted_when_missing(self):
        self.assertEqual(task_args(trial,self.row)['ivr_category'],'baggage')
        row=dict(self.row);row.pop('ivr_category')
        self.assertNotIn('ivr_category',task_args(trial,row))

    def test_every_ivr_action_streams_assistant_phrase_without_deduplication(self):
        events=Events(trial,self.root);events.call_id='call_fixture'
        store=Mock();original=store.log_event
        events.attach(store)
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):
            for _ in range(2):
                store.log_event('call_fixture','task_fixture','ivr_action',
                    {'phrase_id':'agent','state':'automated_assistant'})
            store.log_event('other_call','task_other','ivr_action',{'phrase_id':'category','state':'automated_assistant'})
        rows=[json.loads(line) for line in capture.getvalue().splitlines()]
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['event']=='assistant_phrase' and r['phrase_id']=='agent'
                            and r['detection_state']=='automated_assistant' for r in rows))
        self.assertEqual(capture.getvalue(),(self.root/'live-events.jsonl').read_text())
        self.assertEqual(original.call_count,3)


    def test_new_party_questions_stream_each_ask_with_engine_count(self):
        events=Events(trial,self.root);events.call_id='call_fixture'
        store=Mock();events.attach(store)
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):
            for count in (1,2):
                store.log_event('call_fixture','task_fixture','new_party_question',{'count':count})
        rows=[json.loads(line) for line in capture.getvalue().splitlines()]
        self.assertEqual([r['event'] for r in rows],['new_party_question','new_party_question'])
        self.assertEqual([r['count'] for r in rows],[1,2])
        self.assertEqual(capture.getvalue(),(self.root/'live-events.jsonl').read_text())
