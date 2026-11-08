import asyncio,json
from support import session
cases=[
 ('prelude debit key','Selecting baggage debits your account. For baggage, press 1.','dtmf','1',False),
 ('prelude debit speech','Selecting baggage debits your account. For baggage, say agent.','speech','agent',False),
 ('debit trailing control','For baggage, press 1. Selecting baggage debits your account.','dtmf','1',False),
 ('ordinary greeting','Welcome to Delta Air Lines. For baggage, press 1.','dtmf','1',True),
 ('prelude deduction','Selecting baggage deducts thirty five dollars from your balance. For baggage, press 1.','dtmf','1',False),
]
async def main():
 for name,menu,via,value,expected in cases:
  for info in (True,False):
   s=session(menu,info,value)
   result=await s.press_digits(value) if via=='dtmf' else None
   if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
   sent=any(f.get('digits')==value if via=='dtmf' else f.get('token')==value for f in s.frames)
   print(json.dumps(dict(case=name,menu=menu,via=via,value=value,info_only=info,expected=expected,sent=sent,pass_case=sent==expected,result=result)))
asyncio.run(main())
