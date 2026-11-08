import asyncio,json
from support import session
from plugin.line.detect import classify,Utterance
async def main():
 for phrase in ['To have an agent phone you instead','To be contacted when an agent is free']:
  for case,menu,via,value in [
   ('callback choice refused',f'For reservations, press 1. {phrase}, press 2.','dtmf','2'),
   ('shared key refused',f'For reservations, press 1. {phrase}, press 1.','dtmf','1'),
   ('shared spoken word refused',f'For reservations, say agent. {phrase}, say agent.','speech','agent')]:
   for info in (True,False):
    s=session(menu,info,value);s.last_detection=classify([Utterance(menu)])
    out=await s.press_digits(value) if via=='dtmf' else None
    if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
    sent=bool(s.frames);assert not sent,(menu,s.frames)
    print(json.dumps(dict(case=case,menu=menu,info_only=info,via=via,value=value,expected=False,sent=sent,pass_case=True,result=out,events=s.events)))
asyncio.run(main())
