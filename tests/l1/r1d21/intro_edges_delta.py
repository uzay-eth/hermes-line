import asyncio,json
from support import session,g
# Synthetic menus; expected False means whole-menu refusal.
intros=[
 ('ordinary welcome','Welcome to Delta Air Lines.',True),
 ('ordinary thanks','Thank you for calling Delta Air Lines.',True),
 ('all fixed intros','Your call is important to us. Please listen carefully. Our menu options have changed. Calls may be monitored or recorded. Para español, oprima nine.',True),
 ('name callback blocked','Welcome to Callback Air.',False),
 ('name payment blocked','Welcome to Payment Air.',False),
 ('name split blocked','Welcome to Delta Air Lines hang. Up.',False),
 ('no-name callback promise',"Thank you for calling. We'll ring you back.",False),
 ('no-name callback promise punctuated',"Thank you for calling; we'll ring you back!",False),
 ('normal-name callback promise',"Welcome to Delta. We'll ring back.",False),
 ('no-name payment statement','Thank you for calling. Baggage incurs surcharges.',False),
 ('normal-name payment statement','Welcome to Delta Air Lines. Surcharges apply.',False),
 ('payment non-intro control','Baggage incurs surcharges.',False),
 ('callback non-intro control',"We'll ring you back.",False),
 ('callback blocked verb control',"Thank you for calling. We'll call you back.",False),
 ('amount name ten','Thank you for calling Delta ten.',False),
 ('amount intro dollar','Welcome to Delta Air Lines $35.',False),
 ('unknown greeting','Hello there.',False),
 ('oversized name','Welcome to One Very Long Delta Air Linesline Name.',False),
]
amounts=['$35','£35','€35','35','thirty five','half','quarter','1.50','35USD','USD35','35p','35c','ten dollars']
async def main():
 for name,intro,expected in intros:
  for via in ('dtmf','speech'):
   menu=intro+(' For baggage, press 1.' if via=='dtmf' else ' For baggage, say agent.')
   await probe(name,menu,via,expected)
 for amount in amounts:
  for via in ('dtmf','speech'):
   menu=('For baggage, press 1.' if via=='dtmf' else 'For baggage, say agent.')+f' For {amount}, press 2.'
   await probe('amount '+amount,menu,via,False)
 for name,menu,via,value in [('word key','For baggage, press one.','dtmf','1'),('literal star','For baggage, press *.','dtmf','*'),('word star','For baggage, press star.','dtmf','*'),('generic agent','Welcome to Delta Air Lines. For baggage, press 1.','speech','agent')]:
  await probe(name,menu,via,True,value)
async def probe(name,menu,via,expected,value=None):
 value=value or ('1' if via=='dtmf' else 'agent')
 for info in (True,False):
  s=session(menu,info,value)
  result=await s.press_digits(value) if via=='dtmf' else None
  if via=='speech': await s.start_turn('[phone menu] '+menu,spoken=False)
  sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
  print(json.dumps(dict(case=name,menu=menu,via=via,value=value,info_only=info,expected=expected,sent=sent,pass_case=sent==expected,checked=g.checked_tokens(menu,g.normalise_names(["Delta","Delta Air Lines"])),callback=g.is_callback_offer(menu,False,g.normalise_names(["Delta","Delta Air Lines"])),frames=s.frames,events=s.events,result=result)))
asyncio.run(main())
