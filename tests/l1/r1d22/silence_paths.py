import sys,asyncio,json
sys.path[:0]=['.','tests/l1']
from test_r23_silence_first import SILENCED,EveryPathOnASilencedMenuTests
from support import session,NS
async def main():
 for reason,menu,evidence in SILENCED:
  for info in (True,False):
   for via,value in EveryPathOnASilencedMenuTests.PATHS:
    s=session(menu,info,value)
    if evidence:s.last_detection=NS(label='ivr',evidence=('callback offer requires task authorization',))
    why=s._menu_output_refused(via,value)
    result=await s.press_digits(value) if via=='dtmf' else None
    if via=='speech':await s.start_turn('[phone menu] '+menu,spoken=False)
    assert why and 'nothing is sent' in why and not s.frames,(reason,info,via,value,why,s.frames)
    print(json.dumps(dict(case=reason,menu=menu,info_only=info,via=via,value=value,expected=False,sent=bool(s.frames),pass_case=True,reason=why,result=result)))
asyncio.run(main())
