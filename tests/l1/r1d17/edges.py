import asyncio,json,threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from support import session,CallSession,TurnResult,g
from plugin.line.engine.service import CallService
rows=[]
def out(**r):rows.append(r);print(json.dumps(r),flush=True)
cases=[
 ('comma charge','Press 1 for baggage, authorize a charge.','dtmf','1',False),
 ('colon charge','Press 1 for baggage: authorize a charge.','dtmf','1',False),
 ('semicolon charge','Press 1 for baggage; authorize a charge.','dtmf','1',False),
 ('period charge','Press 1 for baggage. This option authorizes a charge.','dtmf','1',False),
 ('newline charge','Press 1 for baggage\nThis option authorizes a charge.','dtmf','1',False),
 ('speech semicolon','Say agent for baggage; authorize a charge.','speech','agent',False),
 ('speech period','Say agent for baggage. This option authorizes a charge.','speech','agent',False),
 ('shared key explicit','For baggage, press 1. To authorize a charge, press 1.','dtmf','1',False),
 ('shared key shorthand','For baggage, press 1. Key 1 authorizes a charge.','dtmf','1',False),
 ('shared key referent','For baggage, press 1. The same key authorizes a charge.','dtmf','1',False),
 ('shared key safe','For baggage, press 1. For reservations, press 1.','dtmf','1',True),
 ('shared speech unsafe','For baggage, say agent. To authorize a charge, say agent.','speech','agent',False),
 ('callback separate context','We can call you back instead of waiting. To reserve the next available agent, press 1.','dtmf','1',False),
 ('callback shared speech','We can call you back instead of waiting. To reserve the next available agent, say agent.','speech','agent',False),
 ('hang up intact','For baggage, press 1 to hang up.','dtmf','1',False),
 ('hang up split','For baggage, press 1 to hang. Up.','dtmf','1',False),
 ('place in line split','For baggage, press 1 to keep your place in. Line.','dtmf','1',False),
 ('buy baggage','To buy baggage allowance, press 1.','dtmf','1',False),
 ('deposit reservations','To put down a deposit for reservations, press 1.','dtmf','1',False),
 ('bill baggage','To bill baggage to your account, press 1.','dtmf','1',False),
 ('standalone charge key','To authorize a charge. Press 1.','dtmf','1',False),
 ('standalone charge speech','To authorize a charge. Say agent.','speech','agent',False),
 ('clean pound','For an agent, press #.','dtmf','#',True),
]
async def main():
 for name,menu,via,value,expected in cases:
  for info in (True,False):
   s=session(menu,info,value)
   result=await s.press_digits(value) if via=='dtmf' else None
   if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
   sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
   out(case=name,mode='information-only' if info else 'restricted',menu=menu,via=via,value=value,expected=expected,sent=sent,pass_case=sent==expected,result=result,events=s.events)
 # Key-budget check across turns, then a new menu.
 for info in (True,False):
  s=session('For baggage, press 1. For reservations, press 2.',info)
  first=await s.press_digits('1');s.gen+=1
  second=await s.press_digits('2');s.menu_seq+=1;s.gen+=1
  third=await s.press_digits('2')
  out(case='one key per menu across turns',mode=str(info),pass_case=[x['ok'] for x in (first,second,third)]==[True,False,True],results=[first,second,third])
 # A real worker with an in-flight tool queued before the phase change.
 # Fake RPC latency holds dispatch; actual CallService.call_tool performs dispatch.
 for timeout in (False,True):
  for accept in (False,True):
   s=session('For baggage, press 1.');s.cfg.turn_timeout_s=.03 if timeout else 3
   service=CallService.__new__(CallService);service.sessions={s.call_id:s}
   entered=asyncio.Event();release=asyncio.Event();results=[]
   async def rpc():
    entered.set();await release.wait()
    return await service.call_tool(s,'request_finish',('done','Information received.'))
   class Driver:
    def turn(self,text,emit):
     result=asyncio.run_coroutine_threadsafe(rpc(),s.loop).result(timeout=3)
     results.append(result);emit('agent');return TurnResult(text='agent')
    def interrupt(self,*args):pass
    def note(self,*args):pass
   s.driver=Driver();s.executor=ThreadPoolExecutor(max_workers=1)
   s._in_worker=CallSession._in_worker.__get__(s,CallSession)
   task=s.start_turn('[phone menu] For baggage, press 1.',spoken=False)
   await entered.wait()
   if timeout:await task;await asyncio.sleep(0)
   await s._on_human('human',confirmed=True)
   if accept:assert await s.owner_accept('1')
   release.set()
   if not timeout:await task
   for _ in range(100):
    if results:break
    await asyncio.sleep(.005)
   await asyncio.sleep(.01)
   out(case='tool in flight timeout=%s accept=%s'%(timeout,accept),mode='information-only',pass_case=bool(results) and not results[0]['ok'],result=results,finish_outcome=s.finish_outcome,state=s.state.value,frames=s.frames,events=s.events)
   s.executor.shutdown(wait=True)
 print('TOTAL',len(rows),'PASS',sum(r['pass_case'] for r in rows),'FAIL',sum(not r['pass_case'] for r in rows))
asyncio.run(main())
