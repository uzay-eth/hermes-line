# Adapted read-only R1 delta8 probe: assertions inverted for the N11 fix.
import asyncio, io, logging, sys, json, tempfile, time
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = ['.', 'tests/l5', 'tools/trial']
from helpers import Rig
from plugin.line.voice.grok import GrokRealtimeProvider
from plugin.line.voice.provider import transport_logger
from grok_budget import GrokRun, summary

class Wire:
    def __init__(self):
        self.closed=False; self.waiting=asyncio.Event()
    async def send(self,v): pass
    async def recv(self):
        self.waiting.set(); await asyncio.Future()
    async def close(self): self.closed=True

async def cancelled_setup():
    w=Wire()
    async def connect(url,headers): return w
    r=Rig(); r.voice._provider_factory=lambda:GrokRealtimeProvider(lambda n:'synthetic-fixture',connector=connect)
    task=asyncio.create_task(r.voice._open('conversation','fixture'))
    await asyncio.wait_for(w.waiting.wait(),2)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    await r.voice.close()
    assert w.closed and r.voice.provider is None
    print('N9 cancelled_setup_socket_closed_after_call_close:',w.closed)

def private_logger():
    from websockets.client import ClientProtocol
    from websockets.uri import parse_uri
    root=logging.getLogger(); saved=root.level; root.setLevel(logging.DEBUG)
    log=transport_logger(); level=log.level
    out=io.StringIO(); handler=logging.StreamHandler(out); root.addHandler(handler)
    try:
        for force in (False,True):
            if force: log.setLevel(logging.DEBUG)
            p=ClientProtocol(parse_uri('wss://api.x.ai/v1/realtime'),logger=log)
            request=p.connect(); request.headers['Authorization']='Bearer synthetic-fixture'
            p.send_request(request)
        assert 'synthetic-fixture' not in out.getvalue()
        assert 'redacted' in out.getvalue()
        print('N10 private logger authorization absent, including forced DEBUG: True')
    finally:
        root.removeHandler(handler);root.setLevel(saved);log.setLevel(level)

async def settlement_race():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);run=GrokRun(root,root/'run',180)
        entered=asyncio.Event(); release=asyncio.Event()
        class SlowClose(Wire):
            async def close(self):
                entered.set();await release.wait();self.closed=True
        wire=SlowClose()
        async def connect(*args):return wire
        with patch('plugin.line.voice.provider.websockets_connector',side_effect=connect):
            sock=await run.connector('wss://api.x.ai/v1/realtime?model=fixture',{})
        # Model 120 seconds of connected time; no real provider or time charge.
        sock.start=time.monotonic()-120
        first=asyncio.create_task(sock.close())
        await entered.wait()
        await run.close_all()    # returns although the first close has not booked its seconds
        run.finish()
        before=summary(root)['accounted_usd']
        release.set();await first
        run.finish()
        after=summary(root)['accounted_usd']
        assert float(before)>=0.16 and float(after)>=float(before) and run.grok_seconds>=120
        print('N11 concurrent close/finish: persisted USD=',after,'; actual simulated connected seconds>=120:',run.grok_seconds>=120)

async def canceled_close():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);run=GrokRun(root,root/'run',180)
        entered=asyncio.Event()
        class SlowClose(Wire):
            async def close(self):entered.set();await asyncio.Future()
        wire=SlowClose()
        async def connect(*args):return wire
        with patch('plugin.line.voice.provider.websockets_connector',side_effect=connect):
            sock=await run.connector('wss://api.x.ai/v1/realtime?model=fixture',{})
        sock.start=time.monotonic()-120
        task=asyncio.create_task(sock.close());await entered.wait();task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        await run.close_all();run.finish()
        assert float(summary(root)['accounted_usd'])>=0.16
        print('N11 cancelled close: persisted USD>=0.16 for simulated 120-second connection')

async def main():
    await cancelled_setup();private_logger();await settlement_race();await canceled_close()
asyncio.run(main())
