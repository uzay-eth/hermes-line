# Adapted from the read-only delta-16 reviewer fixture: collision assertions require safe cleanup.
import sys,unittest,json,contextlib,io,time,os
from urllib.parse import parse_qs
from unittest.mock import patch
sys.path[:0]=['.','tests/l6']
from test_hold_recovery import SafetyTests
from test_harness import trial
from plugin.line.engine.contracts import Role
from plugin.line.engine.telephony import ProviderError
from hold_recovery import stamp

class Review(SafetyTests):
 def test_nonempty_window_does_not_identify_the_missing_create(self):
  self.remote.fail_role=self.f.row['number']
  with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
  pending=self.root.parent/'hold-recovery.json'
  journal=json.loads(pending.read_text());attempt=journal['creates'][0]['attempt_at']
  rows=self.remote.rows();actual=next(iter(rows.values()));actual_sid=actual['sid']
  # The accepted request was queued and starts beyond the narrow window.
  actual['start_time']=attempt+60
  old_sid='CA'+'8'*32
  old=dict(actual,sid=old_sid,status='completed',start_time=attempt-2,date_created=attempt-3)
  rows[old_sid]=old;self.remote.save(rows)
  original=self.remote.post
  def filtered(path,params):
   if path.startswith('Calls.json?'):
    q=parse_qs(path.split('?',1)[1]);self.remote.queries.append(q)
    return {'calls':[r for r in self.remote.rows().values() if r['to']==q['To'][0]
      and r['from']==q['From'][0] and ('StartTime>=' not in q or stamp(q['StartTime>='][0])<=r['start_time']<=stamp(q['StartTime<='][0]))],
      'next_page_uri':None}
   return original(path,params)
  # Recovery happens after the queued call starts; no sleep or provider involved.
  with patch.object(self.remote,'post',side_effect=filtered),patch('time.time',return_value=attempt+61):
   recovered=self.fresh(command=True)
  observed=dict(case='nonempty partial window',recovery_success=recovered,marker_exists=pending.exists(),
    missing_create_status=self.remote.rows()[actual_sid]['status'],matched_status=self.remote.rows()[old_sid]['status'])
  print(json.dumps(observed))
  # Conformance: the queued create is identified by DateCreated and ended.
  self.assertTrue(recovered);self.assertFalse(pending.exists())
  self.assertEqual(observed['missing_create_status'],'completed')

 def test_main_blocks_execute_when_recovery_window_is_empty(self):
  self.remote.fail_role=self.f.row['number']
  with self.assertRaises(ProviderError):self.dispatch(Role.BUSINESS)
  self.remote.save({})
  import hold
  original=hold.recover
  def replay(api):return self.fresh()
  prior=os.umask(0o077)
  try:
   with patch.object(trial,'ROOT',self.root.parent),patch.object(trial.sys,'argv',['run.py','hold','--business',self.f.row['number'],'--i-am-the-coordinator']),patch('hold.recover',side_effect=replay),patch.object(trial,'execute',side_effect=AssertionError('new trial executed')) as execute,contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaises(trial.Stop):trial.main()
    self.assertEqual(execute.call_count,0)
   print('startup empty window: execute not called, marker retained')
  finally:os.umask(prior)

suite=unittest.TestSuite([
 SafetyTests('test_crash_before_sid_persistence_both_roles_then_fresh_start'),
 Review('test_nonempty_window_does_not_identify_the_missing_create'),
 Review('test_main_blocks_execute_when_recovery_window_is_empty')])
r=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not r.wasSuccessful())
