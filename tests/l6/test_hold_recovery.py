"""N18/N19: process death, ambiguous responses and concurrent stop at the HTTP boundary."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
import test_hold as fixture
from test_harness import trial, secret
from hold import HoldAdapter, Events, recover, dispatch_window
from plugin.line.engine.contracts import Role
from plugin.line.engine.telephony import ProviderError


class Remote:
    def __init__(self, path):self.path=path;self.queries=[];self.fail_role=None;self.block=None
    def rows(self):return json.loads(self.path.read_text()) if self.path.exists() else {}
    def save(self, rows):trial.save(self.path,rows)
    def post(self,path,params):
        rows=self.rows()
        if path=='Balance.json':return {'balance':'20','currency':'USD'}
        if path=='Calls.json':
            form=dict(params);sid='CA'+str(len(rows)+1).zfill(32)
            row=dict(sid=sid,to=form['To'],direction='outbound-api',status='in-progress',
                     start_time=time.time(),date_created=time.time(),**{'from':form['From']})
            rows[sid]=row;self.save(rows)
            if self.block:self.block[0].set();assert self.block[1].wait(5)
            if form['To']==self.fail_role:raise ProviderError('fixture response lost')
            return {'sid':sid}
        if path.startswith('Calls.json?'):
            query=parse_qs(path.split('?',1)[1]);self.queries.append(query)
            assert set(query)=={'To','From','PageSize'}
            return {'calls':[r for r in rows.values() if r['to']==query['To'][0] and r['from']==query['From'][0]]}
        if path.startswith('Calls/'):
            sid=path.split('/')[1].split('.')[0]
            if params:rows[sid]['status']='completed';self.save(rows)
            return rows[sid]
        if path.startswith('Conferences.json?'):return {'conferences':[]}
        raise AssertionError(path)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.HoldTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.remote=Remote(self.root.parent/'remote.json')
        self.a=HoldAdapter(trial,secret,self.f.row,self.f.adapter.line,self.root,self.f.events,real=True)
        self.a.call_id='call_abcd';self.a.task_id='task_fixture'
        self.a._post=self.remote.post
        self.f.adapter=self.a
        self.patch=patch('hold.approval',return_value=self.f.row);self.patch.start();self.addCleanup(self.patch.stop)

    def dispatch(self, role):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.f.dispatch(role,fixture.BUSINESS if role==Role.BUSINESS else trial.ALLOW[0])

    def prepare_owner(self):
        self.dispatch(Role.BUSINESS)
        with contextlib.redirect_stdout(io.StringIO()):self.a.events.emit('human_detected')

    def fresh(self, command=False):
        remote=self.remote
        def factory(api,getter,row,line,root,events,real):
            adapter=HoldAdapter(api,getter,row,line,root,events,real=real)
            adapter._post=remote.post
            return adapter
        capture=io.StringIO()
        with patch.object(trial,'ROOT',self.root.parent),patch.object(trial,'credentials',return_value=(secret,self.a.line)), \
             patch('hold.HoldAdapter',side_effect=factory),patch.dict(os.environ),contextlib.redirect_stdout(capture):
            try:
                if not command:return recover(trial)
                old=os.umask(0o077)
                try:
                    with patch.object(trial.sys,'argv',['run.py','reconcile','--i-am-the-coordinator']):
                        trial.main()
                    return True
                finally:os.umask(old)
            finally:self.reconcile_output=capture.getvalue()

    def test_response_loss_for_both_roles_survives_cleanup_and_fresh_start(self):
        for role in (Role.BUSINESS,Role.OWNER):
            with self.subTest(role=role):
                # Independent run per role to avoid reusing a consumed attempt.
                if role==Role.OWNER:
                    self.setUp();self.prepare_owner()
                self.remote.fail_role=fixture.BUSINESS if role==Role.BUSINESS else trial.ALLOW[0]
                with self.assertRaises(ProviderError):self.dispatch(role)
                pending=self.root.parent/'hold-recovery.json'
                before=json.loads(pending.read_text())
                entry=before['creates'][-1]
                self.assertIsNone(entry['sid'])
                for key in ('role','to','from','task_id','call_id','attempt_at','absolute_deadline'):
                    self.assertIn(key,entry)
                failures=self.a.cleanup(None)
                self.assertTrue(any(r.get('status')=='unresolved_cleanup' for r in failures))
                self.assertTrue(pending.exists())
                self.assertTrue(self.fresh(command=True))
                self.assertFalse(pending.exists())
                self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))
                self.assertEqual(len(self.remote.queries),1)

    def test_crash_before_sid_persistence_both_roles_then_fresh_start(self):
        for role in ('business','owner'):
            with self.subTest(role=role):
                if role=='owner':self.setUp()
                script=r'''
import sys,os,time,contextlib,io
from pathlib import Path
from unittest.mock import patch
from test_harness import trial,secret
from test_hold import ROW,BUSINESS
from test_hold_recovery import Remote
from hold import HoldAdapter,Events,approval
from plugin.line.engine.contracts import Role
from types import SimpleNamespace
root=Path(sys.argv[1]);remote=Remote(root.parent/'remote.json')
row=approval(trial,BUSINESS,root/'approved.json')
a=HoldAdapter(trial,secret,row,'+17755550166',root,Events(trial,root),real=True)
a.call_id='call_abcd';a.task_id='task_fixture';a._post=remote.post
normal=a.journal.known
wanted=sys.argv[2]
def known(record,sid):
 if record['role']==wanted:os._exit(42)
 return normal(record,sid)
a.journal.known=known
with patch('hold.approval',return_value=row),contextlib.redirect_stdout(io.StringIO()):
 for role in ((Role.BUSINESS,) if wanted=='business' else (Role.BUSINESS,Role.OWNER)):
  if role==Role.OWNER:a.events.emit('human_detected')
  a.context.permit=SimpleNamespace(role=role,task_id=a.task_id,call_id=a.call_id)
  a._create_call(to=BUSINESS if role==Role.BUSINESS else trial.ALLOW[0],from_number=a.line,
   answer_url='https://offline',status_url='https://offline',timeout_s=20,time_limit_s=2700)
'''
                env=dict(os.environ,PYTHONPATH=str(trial.REPO/'tests/l6')+os.pathsep+str(trial.REPO/'tools/trial'))
                result=subprocess.run([sys.executable,'-B','-c',script,str(self.root),role],env=env,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,42,result.stderr.decode())
                self.assertIsNone(json.loads((self.root.parent/'hold-recovery.json').read_text())['creates'][-1]['sid'])
                self.assertTrue(self.fresh(command=True))
                self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))

    def test_empty_or_foreign_calls_window_refuses_new_trials_with_check_instructions(self):
        self.remote.fail_role=fixture.BUSINESS
        with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
        pending=self.root.parent/'hold-recovery.json'
        saved=self.remote.rows();self.remote.save({})
        with self.assertRaises(trial.Stop) as err:self.fresh()
        self.assertIn('To/From/DateCreated',str(err.exception));self.assertTrue(pending.exists())
        row=next(iter(saved.values()));row['from']=trial.BORA;self.remote.save(saved)
        with self.assertRaises(trial.Stop):self.fresh()
        self.assertTrue(pending.exists())

    def test_stop_during_balance_never_dispatches_and_cleanup_drains(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def read():entered.set();assert release.wait(5);return {'usd':'20'}
        self.a.budget.read=read
        def dial():
            try:self.dispatch(Role.BUSINESS)
            except ProviderError:errors.append('refused')
        worker=threading.Thread(target=dial);worker.start();self.assertTrue(entered.wait(3))
        with dispatch_window(self.a):pass
        cleaned=[];cleaner=threading.Thread(target=lambda:cleaned.append(self.a.cleanup(None)))
        cleaner.start();time.sleep(.03);self.assertTrue(cleaner.is_alive())
        release.set();worker.join(3);cleaner.join(3)
        self.assertFalse(worker.is_alive() or cleaner.is_alive())
        self.assertEqual(errors,['refused']);self.assertEqual(self.remote.rows(),{})
        self.assertEqual(cleaned,[[]])

    def test_expiry_crossed_during_balance_refuses_dispatch(self):
        def read():self.f.row['expires']='2000-01-01T00:00:00Z';return {'usd':'20'}
        self.a.budget.read=read
        with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
        self.assertEqual(self.remote.rows(),{})

    def test_late_response_after_stop_is_ended_before_cleanup_success(self):
        self.late(False)

    def test_late_sid_hangup_failure_remains_recoverable(self):
        self.late(True)

    def late(self, fail_end):
        entered=threading.Event();release=threading.Event();self.remote.block=(entered,release)
        errors=[]
        def dial():
            try:self.dispatch(Role.BUSINESS)
            except ProviderError:errors.append('late')
        worker=threading.Thread(target=dial);worker.start();self.assertTrue(entered.wait(3))
        self.a.stopped=True
        original_end=self.a.end_call
        if fail_end:self.a.end_call=lambda sid:(_ for _ in ()).throw(ProviderError('fixture end failed'))
        cleaned=[];cleaner=threading.Thread(target=lambda:cleaned.append(self.a.cleanup(None)))
        cleaner.start();time.sleep(.03);self.assertTrue(cleaner.is_alive())
        release.set();worker.join(3);cleaner.join(3)
        self.assertFalse(worker.is_alive() or cleaner.is_alive());self.assertTrue(errors)
        pending=self.root.parent/'hold-recovery.json'
        if fail_end:
            self.assertTrue(cleaned[0]);self.assertTrue(pending.exists())
            self.a.end_call=original_end
            self.assertTrue(self.fresh(command=True))
        else:self.assertEqual(cleaned,[[]]);self.assertFalse(pending.exists())
        self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))

    def test_stop_during_durable_intent_prevents_http_commit(self):
        entered=threading.Event();release=threading.Event();errors=[]
        original=self.a.journal.intent
        def intent(*args):
            row=original(*args);entered.set();assert release.wait(5);return row
        self.a.journal.intent=intent
        def dial():
            try:self.dispatch(Role.BUSINESS)
            except ProviderError:errors.append('refused')
        worker=threading.Thread(target=dial);worker.start();self.assertTrue(entered.wait(3))
        stopper=threading.Thread(target=lambda:setattr(self.a,'stopped',True));stopper.start()
        self.assertTrue(self.a.stop_requested.wait(3))
        release.set();worker.join(3);stopper.join(3)
        self.assertFalse(worker.is_alive() or stopper.is_alive())
        self.assertEqual(errors,['refused']);self.assertEqual(self.remote.rows(),{})
        self.assertEqual(self.a.cleanup(None),[])
        self.assertEqual(self.a.journal.entries[0]['state'],'not_dispatched')

    def test_missing_sid_and_multiple_matching_calls_are_reconciled(self):
        original=self.remote.post
        def missing(path,params):
            result=original(path,params)
            return {} if path=='Calls.json' else result
        self.a._post=missing
        with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
        rows=self.remote.rows();extra=dict(next(iter(rows.values())),sid='CA'+'9'*32)
        rows[extra['sid']]=extra;self.remote.save(rows)
        self.assertTrue(self.fresh(command=True))
        self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))
        self.assertEqual(len(self.remote.rows()),2)

    def test_paginated_window_ends_visible_match_but_stays_unresolved(self):
        self.remote.fail_role=fixture.BUSINESS
        with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
        original=self.remote.post
        def partial(path,params):
            result=original(path,params)
            if path.startswith('Calls.json?'):result['next_page_uri']='https://never-follow.test/'
            return result
        with patch.object(self.remote,'post',side_effect=partial):
            with self.assertRaises(trial.Stop):self.fresh()
        self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))
        self.assertTrue((self.root.parent/'hold-recovery.json').exists())

    def test_new_start_cannot_clear_inflight_creator_after_ending_match(self):
        entered=threading.Event();release=threading.Event();self.remote.block=(entered,release)
        errors=[]
        def dial():
            try:self.dispatch(Role.BUSINESS)
            except ProviderError:errors.append('late')
        worker=threading.Thread(target=dial);worker.start();self.assertTrue(entered.wait(3))
        self.a.stopped=True
        try:
            # Model a recovery process observing the durable journal while the
            # original creator is still alive and awaiting its HTTP response.
            with self.assertRaises(trial.Stop):self.fresh()
            self.assertTrue((self.root.parent/'hold-recovery.json').exists())
            self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertTrue(errors)
        self.assertTrue(self.fresh(command=True))
        self.assertFalse((self.root.parent/'hold-recovery.json').exists())

    def test_drain_timeout_reports_unresolved_in_report_and_stream(self):
        from hold import finish
        entered=threading.Event();release=threading.Event();self.remote.block=(entered,release)
        errors=[]
        def dial():
            try:self.dispatch(Role.BUSINESS)
            except ProviderError:errors.append('late')
        worker=threading.Thread(target=dial);worker.start();self.assertTrue(entered.wait(3))
        try:
            with patch.object(self.a.drained,'wait_for',return_value=False),contextlib.redirect_stdout(io.StringIO()):
                report=finish(trial,self.a,None,None,self.root,'completed',False)
            self.assertEqual(report['outcome'],'unresolved_cleanup')
            self.assertEqual(self.a.events.records[-1]['outcome'],'unresolved_cleanup')
            self.assertTrue((self.root.parent/'hold-recovery.json').exists())
        finally:release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertTrue(errors)
        self.assertTrue(self.fresh(command=True))

    def test_late_writer_preserves_sids_discovered_by_a_reconciler(self):
        from hold_recovery import CreateJournal
        journal=self.a.journal
        row=journal.intent(Role.BUSINESS,fixture.BUSINESS,self.a.line,'task_fixture','call_abcd',time.time(),time.time()+100)
        other=CreateJournal(self.root,live=True)
        saved=json.loads(journal.pending.read_text())
        other.entries=saved['creates'];other.task_id='task_fixture';other.call_id='call_abcd'
        first,extra='CA'+'1'*32,'CA'+'2'*32
        other.sids=[first,extra];other.entries[0]['matches']=[first,extra];other.persist()
        journal.known(row,first)
        journal.ended(first);row.update(state='ended',in_flight=False);journal.persist()
        self.assertIn(extra,json.loads(journal.pending.read_text())['sids'])
        with self.assertRaises(ProviderError):journal.clear()
        journal.ended(extra);journal.clear()
        self.assertFalse(journal.pending.exists())

    def test_stale_reconciler_cannot_drop_a_new_owner_intent(self):
        from hold_recovery import CreateJournal
        journal=self.a.journal
        journal.intent(Role.BUSINESS,fixture.BUSINESS,self.a.line,'task_fixture','call_abcd',time.time(),time.time()+100)
        stale=CreateJournal(self.root,live=True)
        saved=json.loads(journal.pending.read_text())
        stale.entries=saved['creates'];stale.task_id='task_fixture';stale.call_id='call_abcd'
        journal.intent(Role.OWNER,trial.ALLOW[0],self.a.line,'task_fixture','call_abcd',time.time(),time.time()+100)
        stale.persist()
        self.assertEqual({r['role'] for r in json.loads(journal.pending.read_text())['creates']},{'business','owner'})
        with self.assertRaises(ProviderError):stale.clear()

    def collision(self):
        self.remote.fail_role=self.f.row['number']
        with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
        saved=json.loads((self.root.parent/'hold-recovery.json').read_text())
        attempt=saved['creates'][0]['attempt_at']
        rows=self.remote.rows();actual=next(iter(rows.values()))
        actual['start_time']=attempt+60
        old=dict(actual,sid='CA'+'8'*32,status='completed',start_time=attempt-2,date_created=attempt-3)
        rows[old['sid']]=old;self.remote.save(rows)
        return attempt,actual['sid'],old['sid']

    def test_review_collision_candidate_cleanup_blocks_until_coordinator(self):
        attempt,actual,old=self.collision()
        with patch('time.time',return_value=attempt+61):
            with self.assertRaises(trial.Stop):self.fresh()
        self.assertTrue((self.root.parent/'hold-recovery.json').exists())
        self.assertTrue(self.fresh(command=True))
        self.assertEqual(self.remote.rows()[actual]['status'],'completed')
        self.assertEqual(self.remote.rows()[old]['status'],'completed')
        self.assertFalse((self.root.parent/'hold-recovery.json').exists())
        self.assertNotIn('StartTime<=',self.remote.queries[-1])
        self.assertNotIn('StartTime>=',self.remote.queries[-1])
        report=json.loads((self.root/'hold-reconcile.json').read_text())[0]
        self.assertEqual(report['ignored_older'],1)
        self.assertEqual(len(report['candidates']),1)

    def test_older_call_alone_never_resolves_missing_create_even_for_coordinator(self):
        _,actual,old=self.collision()
        original=self.remote.post
        def partial(path,params):
            if path.startswith('Calls.json?'):return {'calls':[self.remote.rows()[old]]}
            return original(path,params)
        with patch.object(self.remote,'post',side_effect=partial):
            for command in (False,True):
                with self.assertRaises(trial.Stop):self.fresh(command=command)
                self.assertTrue((self.root.parent/'hold-recovery.json').exists())
        self.assertEqual(self.remote.rows()[actual]['status'],'in-progress')
        self.assertIn('"candidates": []',self.reconcile_output)

    def test_queued_call_past_old_window_with_no_start_time_is_ended(self):
        attempt,actual,old=self.collision()
        rows=self.remote.rows();rows[actual].update(start_time=None,date_created=attempt+4000,status='queued')
        self.remote.save(rows)
        self.assertTrue(self.fresh(command=True))
        self.assertEqual(self.remote.rows()[actual]['status'],'completed')
        self.assertFalse((self.root.parent/'hold-recovery.json').exists())

    def test_two_created_candidates_both_ended_and_masked_by_coordinator_command(self):
        attempt,actual,old=self.collision()
        rows=self.remote.rows();extra=dict(rows[actual],sid='CA'+'9'*32,date_created=attempt+90,status='ringing',start_time=None)
        rows[extra['sid']]=extra;self.remote.save(rows)
        self.assertTrue(self.fresh(command=True))
        self.assertTrue(all(r['status']=='completed' for r in self.remote.rows().values()))
        self.assertIn('reconcile_candidates',self.reconcile_output)
        self.assertIn('confirmed_terminal',self.reconcile_output)
        for raw in (actual,extra['sid'],self.f.row['number'],self.a.line):self.assertNotIn(raw,self.reconcile_output)
        report=json.loads((self.root/'hold-reconcile.json').read_text())[0]
        self.assertEqual(len(report['candidates']),2)
        self.assertTrue(all(c['confirmed_status']=='completed' for c in report['candidates']))

    def test_reconcile_flag_is_required_before_any_credentials_or_api(self):
        with patch.object(trial.sys,'argv',['run.py','reconcile']),patch.object(trial,'credentials',side_effect=AssertionError('credentials')):
            with self.assertRaises(trial.Stop):trial.main()

    def test_failure_ending_first_candidate_still_ends_second_and_keeps_block(self):
        attempt,actual,old=self.collision()
        rows=self.remote.rows();extra=dict(rows[actual],sid='CA'+'9'*32,date_created=attempt+1)
        rows[extra['sid']]=extra;self.remote.save(rows)
        original=self.remote.post
        def failing(path,params):
            if path=='Calls/'+actual+'.json' and params:raise ProviderError('fixture end refused')
            return original(path,params)
        with patch.object(self.remote,'post',side_effect=failing):
            with self.assertRaises(trial.Stop):self.fresh(command=True)
        self.assertEqual(self.remote.rows()[extra['sid']]['status'],'completed')
        self.assertEqual(self.remote.rows()[actual]['status'],'in-progress')
        self.assertTrue((self.root.parent/'hold-recovery.json').exists())

    def test_missing_or_invalid_date_created_cannot_use_start_time_as_identity(self):
        _,actual,old=self.collision()
        rows=self.remote.rows();rows.pop(old)
        for created in (None,float('inf')):
            rows[actual]['date_created']=created;self.remote.save(rows)
            with self.assertRaises(trial.Stop):self.fresh()
            self.assertTrue((self.root.parent/'hold-recovery.json').exists())
            self.assertEqual(self.remote.rows()[actual]['status'],'in-progress')

    def test_reconcile_without_pending_marker_does_not_load_credentials(self):
        capture=io.StringIO();old=os.umask(0o077)
        try:
            with patch.object(trial,'ROOT',self.root.parent),contextlib.redirect_stdout(capture), \
                 patch.object(trial.sys,'argv',['run.py','reconcile','--i-am-the-coordinator']), \
                 patch.object(trial,'credentials',side_effect=AssertionError('credentials')):
                trial.main()
        finally:os.umask(old)
        self.assertIn('no pending hold recovery',capture.getvalue())

    def test_inside_tolerance_candidate_never_automatically_clears_sidless_create(self):
        attempt,actual,other=self.collision()
        rows=self.remote.rows();rows[other]['date_created']=attempt-1;self.remote.save(rows)
        original=self.remote.post
        def partial(path,params):
            if path.startswith('Calls.json?'):return {'calls':[self.remote.rows()[other]],'next_page_uri':None}
            return original(path,params)
        with patch.object(self.remote,'post',side_effect=partial):
            with self.assertRaises(trial.Stop):self.fresh()
        self.assertTrue((self.root.parent/'hold-recovery.json').exists())
        self.assertEqual(self.remote.rows()[actual]['status'],'in-progress')
        self.assertIn('pending_coordinator', (self.root/'hold-reconcile.json').read_text())

    def test_recorded_sid_recovers_automatically_without_candidate_inference(self):
        sid=self.dispatch(Role.BUSINESS)
        original=self.remote.post
        def scoped(path,params):
            if path.startswith('Calls.json?'):raise AssertionError('known SID must not use candidate inference')
            return original(path,params)
        with patch.object(self.remote,'post',side_effect=scoped):self.assertTrue(self.fresh())
        self.assertEqual(self.remote.rows()[sid]['status'],'completed')
        self.assertFalse((self.root.parent/'hold-recovery.json').exists())

    def test_known_sid_failure_cannot_resolve_using_another_terminal_call(self):
        sid=self.dispatch(Role.BUSINESS)
        rows=self.remote.rows();rows['CA'+'8'*32]=dict(rows[sid],sid='CA'+'8'*32,status='completed');self.remote.save(rows)
        original=self.remote.post
        def scoped(path,params):
            if path.startswith('Calls.json?'):raise AssertionError('must resolve recorded SID only')
            if path=='Calls/'+sid+'.json' and params:raise ProviderError('fixture end failed')
            return original(path,params)
        with patch.object(self.remote,'post',side_effect=scoped):
            with self.assertRaises(trial.Stop):self.fresh()
        self.assertTrue((self.root.parent/'hold-recovery.json').exists())

    def test_journal_clear_itself_requires_coordinator_for_sidless_ended_row(self):
        journal=self.a.journal
        row=journal.intent(Role.BUSINESS,fixture.BUSINESS,self.a.line,'task_fixture','call_abcd',time.time(),time.time()+100)
        row.update(state='ended',in_flight=False)
        journal.persist()
        with self.assertRaises(ProviderError):journal.clear()
        self.assertTrue(journal.pending.exists())
        journal.clear(coordinator=True)
        self.assertFalse(journal.pending.exists())
