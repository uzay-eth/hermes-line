import asyncio,json
from support import session,g
cases=[
 ('bare hold','Please hold.',True),
 ('wait digits','The estimated wait time is approximately 12 minutes.',True),
 ('wait words','Estimated wait time is about twenty five minutes.',True),
 ('wait combined','Welcome to Delta. Estimated wait time is 12 minutes. Please continue to hold.',True),
 ('wait money','Estimated wait time is 35USD minutes.',False),
 ('wait currency symbol','Estimated wait time is $35 minutes.',False),
 ('wait suffix','Estimated wait time is 12 minutes at your expense.',False),
 ('wait callback','Estimated wait time is 12 minutes. We will phone you instead.',False),
 ('wait no punctuation','Estimated wait time is 12 minutes we will phone you instead',False),
 ('hold unknown suffix','Please hold for my sister.',False),
 ('hold before safe choice','Please hold. For baggage, press 1.',True),
 ('hold after safe choice','For baggage, press 1. Please hold.',False),
 ('generic all safe','For baggage, press 1. For reservations, press 2.',True),
 ('generic unknown repeat','For baggage, press 1. To repeat, press 9.',False),
 ('no-choice unknown','Would you like us to phone you instead?',False),
]
async def main():
 for case,menu,expected in cases:
  for info in (True,False):
   for word in ('agent','representative'):
    s=session(menu,info,word);await s.start_turn('[phone menu] '+menu,spoken=False)
    sent=any(f.get('token')==word for f in s.frames)
    print(json.dumps(dict(case=case,menu=menu,info_only=info,via='speech',value=word,expected=expected,sent=sent,pass_case=expected==sent,events=s.events)))
asyncio.run(main())
