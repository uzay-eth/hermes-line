import asyncio,json
from types import SimpleNamespace as NS
from support import session,g
from plugin.line.detect import classify,Utterance
NAMES=['Delta','Delta Air Lines']
intros=[
 ('short approved','Welcome to Delta.',True),('long approved','Thank you for calling Delta Air Lines.',True),
 ('uppercase','WELCOME TO DELTA AIR LINES.',True),('comma fixed','Welcome to Delta, your call is important to us.',True),
 ('colon fixed','Welcome to Delta: please listen carefully.',True),('bare thanks','Thank you for calling.',True),
 ('prefix insufficient','Welcome to Delta Air.',False),('suffix unknown','Welcome to Delta Air Lines today.',False),
 ('name split sentence','Welcome to Delta Air. Lines.',False),('name split comma','Welcome to Delta Air, Lines.',True),
 ('callback extra sentence',"Welcome to Delta. We'll ring you back.",False),
 ('callback no punctuation',"Thank you for calling Delta we'll ring you back",False),
 ('callback before fixed',"Thank you for calling we'll ring you back your call is important to us",False),
 ('surcharge no punctuation','Welcome to Delta Air Lines surcharges apply',False),
 ('other business','Welcome to Sample Air.',False),('missing name','Welcome to.',False),
 ('literal amount in name','Welcome to Delta35.',False),('word amount in name','Welcome to Delta ten.',False),
 ('callback after option','',None),
]
async def probe(name,menu,via,value,expected,names=NAMES,evidence=False):
 for info in (True,False):
  s=session(menu,info,value);s.options={'intro_names':names}
  if evidence:s.last_detection=NS(label='ivr',evidence=('callback offer requires task authorization',))
  else:s.last_detection=classify([Utterance(menu)])
  result=await s.press_digits(value) if via=='dtmf' else None
  if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
  sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
  print(json.dumps(dict(case=name,menu=menu,via=via,value=value,info_only=info,intro_names=names,detector=s.last_detection.__dict__,expected=expected,sent=sent,pass_case=sent==expected,frames=s.frames,events=s.events,result=result)))
async def main():
 for name,intro,expected in intros:
  if expected is None:continue
  for via in ('dtmf','speech'):
   await probe(name,intro+(' For baggage, press 1.' if via=='dtmf' else ' For baggage, say agent.'),via,'1' if via=='dtmf' else 'agent',expected)
 for names in ([],['Delta'],['Delta Air Lines']):
  for business in ('Delta','Delta Air Lines'):
   for via in ('dtmf','speech'):
    await probe('exact authority '+business,'Welcome to '+business+('. For baggage, press 1.' if via=='dtmf' else '. For baggage, say agent.'),via,'1' if via=='dtmf' else 'agent',business in names,names)
 for text in ("Would you like us to ring you back?", "We can ring you back. Select an option.","For a ring back, tell us which department you need."):
  for word in ('agent','representative'):
   await probe('callback without announced choice',text,'speech',word,False)
   await probe('callback detected control',text,'speech',word,False,evidence=True)
 await probe('generic agent clean control','Please select an option.','speech','agent',False)
 await probe('known callback words control','We can call you back. Select an option.','speech','agent',False)
asyncio.run(main())
