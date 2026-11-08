# Adapted read-only reviewer probe; N18/N19 assertions inverted after the fix.
import sys,contextlib,io,threading,json,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=['.','tests/l6']
from test_hold import HoldTests,BUSINESS
from test_harness import trial,secret
from hold import recover,dispatch_window
from plugin.line.engine.contracts import Role
from plugin.line.engine.telephony import ProviderError

# Provider accepted a create but the HTTP response was lost; no real HTTP.
t=HoldTests();t.setUp()
try:
 a=t.adapter;a.real=True;a.journal.live=True;a.call_id="call_abcd"
 remote_row={}
 def remote(path,params):
  if path=='Balance.json':return {'balance':'20','currency':'USD'}
  if path=='Calls.json':
   a.accepted_without_reply=True
   import time
   remote_row.update(sid='CA'+'1'*32,to=BUSINESS,direction='outbound-api',status='in-progress',start_time=time.time(),date_created=time.time(),**{'from':a.line})
   raise ProviderError('Twilio unreachable: TimeoutError')
  if path.startswith('Calls.json?'):return {'calls':[remote_row]}
  if path.startswith('Calls/'):
   if params:remote_row['status']='completed'
   return remote_row
  if path.startswith('Conferences.json?'):return {'conferences':[]}
  raise AssertionError('unexpected boundary')
 with patch('hold.approval',return_value=t.row),patch.object(a,'_post',side_effect=remote):
  try:t.dispatch(Role.BUSINESS,BUSINESS)
  except ProviderError:pass
 a._post=remote
 assert a.accepted_without_reply and not a.sids
 assert (t.root/'hold-journal.json').exists()
 assert (t.root.parent/'hold-recovery.json').exists()
 assert any(f.get('status')=='unresolved_cleanup' for f in a.cleanup(None))
 with patch.object(trial,'ROOT',t.root.parent),patch.object(trial,'credentials',return_value=(secret,a.line)),patch('hold.HoldAdapter',return_value=a):
  assert recover(trial,coordinator=True) is True
 assert remote_row['status']=='completed'
 assert not (t.root.parent/'hold-recovery.json').exists()
 print('N18: unresolved intent survives cleanup; fresh recovery finds and ends the accepted call')
finally:t.doCleanups()

# A stop while the balance read is pending is not rechecked at dispatch.
t=HoldTests();t.setUp();entered=threading.Event();release=threading.Event();errors=[]
try:
 a=t.adapter
 def read():
  entered.set();assert release.wait(3);return {'usd':'20'}
 a.budget.read=read
 def dial():
  try:t.dispatch(Role.BUSINESS,BUSINESS)
  except BaseException as e:errors.append(type(e).__name__)
 capture=io.StringIO()
 with contextlib.redirect_stdout(capture):
  worker=threading.Thread(target=dial)
  with dispatch_window(a):
   worker.start();assert entered.wait(3)
  assert a.stopped
  # This is the actual cleanup method; there are no known SIDs yet.
  cleaned=[]
  cleaner=threading.Thread(target=lambda:cleaned.append(a.cleanup(None)))
  cleaner.start()
  release.set();worker.join(3);cleaner.join(3);assert not worker.is_alive() and not cleaner.is_alive()
  assert cleaned==[[]]
 assert errors==['ProviderError'],errors
 assert not a.forms and not a.sids
 print('N19: stop during balance read refuses dispatch; cleanup drains worker; no live fake leg')
finally:release.set();t.doCleanups()
