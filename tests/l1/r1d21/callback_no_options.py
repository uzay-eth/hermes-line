import asyncio,json
from support import session,g
from plugin.line.detect import classify,Utterance
async def main():
 for menu,expected in [('We can ring you back. Select an option.',False),('We can call you back. Select an option.',False),('Please select an option.',True),('We can ring you back. For baggage, press 1.',False)]:
  for info in (True,False):
   for word in ('agent','representative'):
    s=session(menu,info,word)
    detection=classify([Utterance(menu)])
    assert detection.label=='ivr',detection
    await s._on_detection(detection,menu)
    if s.turn_task:await s.turn_task
    sent=any(f.get('token')==word for f in s.frames)
    print(json.dumps(dict(case='real detection and dispatch',menu=menu,info_only=info,value=word,expected=expected,sent=sent,pass_case=sent==expected,detector=detection.__dict__,options=g.clauses(menu,g.normalise_names(['Delta','Delta Air Lines'])),frames=s.frames,events=s.events)))
asyncio.run(main())
