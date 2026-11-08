# Adapted read-only R1 delta8 probe: assertions inverted for the N11 fix.
import asyncio, tempfile, sys, time
from pathlib import Path
sys.path[:0]=['.','tools/trial']
from grok_budget import GrokRun, summary
from websockets.asyncio.server import serve

async def main():
 async def handler(ws):await ws.wait_closed()
 async with serve(handler,'127.0.0.1',0) as server:
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);r=GrokRun(root,root/'run',180)
   sock=await r.connector('ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1])+'/v1/realtime?model=fixture',{})
   sock.start=time.monotonic()-120  # synthetic duration, actual transport
   closer=asyncio.create_task(sock.close())
   await asyncio.sleep(0)   # close has aborted transport; accounting is after a suspending wait_for
   assert sock.closed
   # ServerRunner teardown cancels all pending tasks, including nested close.
   inners=[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and t is not closer and getattr(t.get_coro(), "__qualname__", "")=="_WebsocketsConnection.close"]
   assert len(inners)==1
   closer.cancel()
   for t in inners:t.cancel()
   try:await closer
   except asyncio.CancelledError:pass
   await r.close_all();r.finish()
   print('real_loopback_transport_closing:',sock.ws._ws.transport.is_closing())
   print('simulated_120_second_connection_persisted_usd:',summary(root)['accounted_usd'])
   assert float(summary(root)['accounted_usd'])>=0.16
asyncio.run(main())
