import sys, asyncio, json
from types import SimpleNamespace as NS
sys.path[:0]=['.','plugin']
from plugin.line.engine.machine import CallSession
from plugin.line.engine.contracts import CallState as S, Mode
from plugin.line.engine.driver import TurnResult
from plugin.line.engine import menu_gate as g

rows=[]
def record(**r): rows.append(r); print(json.dumps(r),flush=True)
class FakeDriver:
 def __init__(self,phrase): self.phrase=phrase; self.interrupts=0
 def turn(self,text,emit): emit(self.phrase); return TurnResult(text=self.phrase)
 def interrupt(self,*a): self.interrupts+=1 # deliberately ignore cancellation, test late return
 def note(self,*a): pass

def session(menu,info=True,phrase='WAIT'):
 s=CallSession.__new__(CallSession)
 s.loop=asyncio.get_running_loop();s.state=S.IVR;s._finalizing=False;s.handoff_pending=False
 s.gen=0;s.digits_gen=-1;s.current_turn=None;s.turn_task=None;s._bg=set();s.note_offered=False
 s.info_only=info;s.restrictions={'callback','payment','change'};s.last_menu=menu;s.last_detection=None
 s.end_request=None;s.owner_sid=None;s.voice=None;s.driver=FakeDriver(phrase);s.driver_error=None
 s.cfg=NS(turn_timeout_s=3,owner_name='Fixture owner',speech_chars_per_s=1000)
 s.frames=[];s.events=[];s.transcripts=[];s._send=s.frames.append
 s._event=lambda kind,**data:s.events.append({'kind':kind,**data})
 s._transcript=lambda who,text:s.transcripts.append((str(who),text))
 s._ensure_driver=lambda:s.driver
 s._readback=None;s.proposal=None;s.finish_outcome=None;s.owner_requested=False
 s.call_id='fixture';s.mode=Mode.OWNER;s.parent=None;s.human_confirmed=False
 s.store=NS(update_call=lambda *a,**k:0);s.svc=NS(notify=lambda *a:None)
 async def worker(fn,*args,**kw):return fn(*args)
 s._in_worker=worker
 async def ring(*args):return await s.set_state(S.RINGING_OWNER,'fake owner ringing')
 s.ring_owner=ring
 async def move():pass
 s._move_business_to_conference=move
 return s

# Expected True means navigation/stay/decline only. Each is exercised in both task modes.
cases=[
('baggage','For baggage, press 2.','dtmf','2',True),
('reservations','For reservations, press three.','dtmf','3',True),
('general info','Press zero for general information.','dtmf','0',True),
('decline','To decline, press 2.','dtmf','2',True),
('unknown','Press 9 to proceed.','dtmf','9',False),
('callback yes','We can call you back. Say yes to accept.','speech','yes',False),
('callback key','We can call you back. Press 1 to accept, press 2 to keep holding.','dtmf','1',False),
('callback stay','We can call you back. Press 1 to accept, press 2 to keep holding.','dtmf','2',True),
('callback euphemism','To keep your place in line and receive a phone call from an agent later, press 1.','dtmf','1',False),
('known callback keep place','We can call you back. To keep your place in line and hang up, press 1.','dtmf','1',False),
('callback speech OR','We can call you back. To keep your place in line and hang up, say agent or press 1.','speech','agent',False),
('charge','To authorize a charge, press one.','dtmf','1',False),
('change','To confirm the change, press 4.','dtmf','4',False),
('mixed same clause','Press 1 for baggage and to authorize a charge.','dtmf','1',False),
('mixed comma key','Press 1 for baggage, and to authorize a charge.','dtmf','1',False),
('mixed comma speech','Say agent for baggage, and to authorize a charge.','speech','agent',False),
('mixed repeated key','For baggage, press 1. To authorize a charge, press 1.','dtmf','1',False),
('mixed repeated speech','For baggage, say agent. To authorize a charge, say agent.','speech','agent',False),
('unsafe speech OR','To authorize a charge, say agent or press 1.','speech','agent',False),
('unsafe speech then','To authorize a charge, say agent then wait.','speech','agent',False),
('safe star word','Press star for baggage.','dtmf','*',True),
('safe star literal','For baggage, press *.','dtmf','*',True),
('unsafe star','Press star to authorize a charge.','dtmf','*',False),
('spoken numeral safe','For baggage, say one.','speech','one',True),
('spoken numeral unsafe','To authorize a charge, say one.','speech','one',False),
('safe agent','For an agent, say agent.','speech','agent',True),
('no','We can call you back. To stay on the line, say no.','speech','no',True),
('seq 12','For baggage, press 1. For reservations, press 2.','dtmf','12',False),
('seq 1w2','For baggage, press 1. For reservations, press 2.','dtmf','1w2',False),
('seq 11','For baggage, press 1.','dtmf','11',False),
('one paused key','For baggage, press 1.','dtmf','w1w',True),
]
async def main():
 for name,menu,via,value,expected in cases:
  for info in (True,False):
   s=session(menu,info,value)
   result=await s.press_digits(value) if via=='dtmf' else None
   if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
   sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
   record(case=name,mode='information-only' if info else 'restricted',menu=menu,via=via,value=value,expected=expected,sent=sent,pass_case=sent==expected,result=result,blocked=[e for e in s.events if e['kind']=='restricted_action_blocked'],options=g.key_options(menu) if via=='dtmf' else g.say_options(menu))
 # Actual state transitions, worker scheduled before human, cancellation ignored by fake driver.
 for queued,accept in [(False,False),(False,True),(True,False),(True,True)]:
  s=session('For baggage, press 2.',phrase='agent');entered=asyncio.Event();release=asyncio.Event();calls=0
  async def worker(fn,*a,**kw):
   nonlocal calls
   if fn.__name__=='run':
    calls+=1
    if calls==1: entered.set();await release.wait()
   return fn(*a)
  s._in_worker=worker
  first=s.start_turn('[phone menu] first',spoken=False);await entered.wait()
  second=s.start_turn('[phone menu] queued',spoken=False) if queued else None
  await asyncio.sleep(0)
  await s._on_human('A live person answered',confirmed=True)
  if accept:
   # Release the ready response and accept in the same event-loop slice.
   release.set();assert await s.owner_accept('1')
  else:release.set()
  await first
  if second:await second
  await asyncio.sleep(0)
  sent=any(f.get('token')=='agent' for f in s.frames)
  record(case='queued=%s accept=%s'%(queued,accept),mode='information-only',expected=False,sent=sent,pass_case=not sent,state=s.state.value,worker_turns=calls,frames=s.frames,events=s.events)
 print('TOTAL',len(rows),'PASS',sum(r['pass_case'] for r in rows),'FAIL',sum(not r['pass_case'] for r in rows))
