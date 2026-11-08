import sys,time
sys.path[:0]=['.','tests/l1']
from test_r16c_fallback_question import InfoOnlyMenuTests
from harness import wait_for
# Actual Relay/menu path, synthetic model replies; no provider.
t=InfoOnlyMenuTests();t.setUp()
try:
 l,req,r,results=t._menu('To get a call back when an agent is available, say yes. To stay on the line, say no.',phrase='yes')
 r.wait_text('yes')
 s=l.session(req['call_id'])
 assert 'callback' in s.restrictions
 print('forbidden callback offer produced spoken YES; detector evidence:',s.last_detection.evidence)
finally:t.doCleanups()
t=InfoOnlyMenuTests();t.setUp()
try:
 l,req,r,results=t._menu('To authorize a charge, press 1. For an agent, press 2.',presses=['1'])
 wait_for(lambda:results)
 assert results[0]['ok'] and any(f.get('digits')=='1' for f in r.of_type('sendDigits'))
 print('forbidden payment wording authorize a charge allowed key 1')
finally:t.doCleanups()
# A menu turn that completes after human detection can still speak to that human.
import threading
from harness import Line
from test_r16c_fallback_question import Q
release=threading.Event();entered=threading.Event()
def slow(driver,text):
 if '[phone menu]' in text:
  entered.set();release.wait(8);return ['yes']
 return ['WAIT']
l=Line(reply=slow)
try:
 req=l.request(mode='owner',fallback_question=Q,owner_ring_timeout_s=20)
 l.approve(req['task_id']);created=wait_for(lambda:l.adapter.created and l.adapter.created[0])
 r=l.connect(req,created)
 r.prompt('For baggage, press 2. For reservations, press 3.')
 assert entered.wait(3)
 from test_flows import reach_human
 reach_human(l,r)
 l.wait_state(req['call_id'],'ringing_owner')
 release.set();r.wait_text('yes')
 print('old menu model turn emitted YES after human detected and owner ringing')
finally:release.set();l.close()
