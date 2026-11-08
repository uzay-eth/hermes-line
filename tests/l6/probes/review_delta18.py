# Adapted reviewer case: round-13 policy tests automatic recovery; explicit reconcile is separately tested.
import sys,unittest,json
from unittest.mock import patch
sys.path[:0]=['.','tests/l6']
from test_hold_recovery import SafetyTests
from test_harness import trial
class Edge(SafetyTests):
 def test_pre_attempt_candidate_inside_tolerance(self):
  attempt,actual,old=self.collision()
  rows=self.remote.rows();rows[old]['date_created']=attempt-1;self.remote.save(rows)
  original=self.remote.post
  def partial(path,params):
   if path.startswith('Calls.json?'):return {'calls':[self.remote.rows()[old]],'next_page_uri':None}
   return original(path,params)
  # Same list-visibility fault as upstream older-only test, but T-1 rather than T-3.
  with patch.object(self.remote,'post',side_effect=partial):
   with self.assertRaises(trial.Stop):self.fresh()
  result=False
  marker=(self.root.parent/'hold-recovery.json').exists()
  print(json.dumps(dict(recovery=result,marker=marker,actual_status=self.remote.rows()[actual]['status'],different_call_created_delta=-1)))
  self.assertFalse(result);self.assertTrue(marker)
  self.assertEqual(self.remote.rows()[actual]['status'],'in-progress')
r=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Edge('test_pre_attempt_candidate_inside_tolerance')]))
sys.exit(not r.wasSuccessful())
