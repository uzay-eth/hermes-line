import asyncio,json
from support import session,g
from plugin.line.detect import classify,Utterance
NAMES=['Delta','Delta Air Lines']
async def main():
 for clause in ['To have an agent phone you instead, press 2.','To be contacted when an agent is free, press 2.','To have an agent call you back, press 2.','To repeat, press 9.']:
  for via in ('dtmf','speech','generic'):
   menu=('For reservations, say agent. ' if via=='speech' else 'For reservations, press 1. ')+clause
   expected=clause=='To repeat, press 9.' and via!='generic'
   for info in (True,False):
    value='1' if via=='dtmf' else 'agent';s=session(menu,info,value)
    det=classify([Utterance(menu)]);assert det.label=='ivr',det
    if via=='dtmf':
     # Use real detection/dispatch with a silent fake reply, then invoke the real keypad tool.
     s.driver.phrase='WAIT'
    await s._on_detection(det,menu)
    if s.turn_task:await s.turn_task
    result=await s.press_digits(value) if via=='dtmf' else None
    sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
    print(json.dumps(dict(case=clause,menu=menu,info_only=info,via=via,value=value,expected=expected,sent=sent,pass_case=sent==expected,detector=det.__dict__,callback=g.is_callback_offer(menu,False,g.normalise_names(NAMES)),frames=s.frames,events=s.events,result=result)))
asyncio.run(main())
