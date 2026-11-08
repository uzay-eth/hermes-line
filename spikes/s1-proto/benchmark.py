"""Offline real-Hermes loop benchmark. Fixture content is not a product claim."""
import asyncio
import json
import math
import platform
import statistics
import time
from pathlib import Path
from aiohttp import ClientSession, web
from server import RelayServer


async def main():
    from run_agent import AIAgent
    from tools.registry import registry
    calls = []
    seen_memory = []
    def calendar(args, **kwargs):
        calls.append(True)
        return json.dumps({"fixture": True, "available": True})
    registry.register(name="s1_fixture_calendar", toolset="s1_fixture",
                      schema={"name": "s1_fixture_calendar", "description": "Offline calendar fixture.",
                              "parameters": {"type": "object", "properties": {}}}, handler=calendar)

    async def model(request):
        if request.method == 'GET':
            return web.json_response({"data": [{"id": "s1-fixture", "context_length": 131072}]})
        body = await request.json()
        messages = body['messages']
        seen_memory.append('S1_MEMORY_FIXTURE' in json.dumps(messages))
        want_tool = 'calendar' in str(next((m['content'] for m in reversed(messages) if m['role']=='user'), ''))
        tool_done = messages[-1]['role'] == 'tool'
        if want_tool and tool_done:
            assert '"available": true' in str(messages[-1]['content']), messages[-1]['content']
        reply = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await reply.prepare(request)
        async def chunk(delta, finish=None):
            data = {"id":"s1", "object":"chat.completion.chunk", "created":0, "model":"s1-fixture",
                    "choices":[{"index":0,"delta":delta,"finish_reason":finish}]}
            await reply.write(('data: '+json.dumps(data)+'\n\n').encode())
        # Controlled model delays: 20 ms to first chunk, 5 ms between text chunks.
        await asyncio.sleep(.020)
        if want_tool and not tool_done:
            await chunk({"tool_calls":[{"index":0,"id":"s1-calendar", "type":"function",
                                        "function":{"name":"s1_fixture_calendar","arguments":"{}"}}]})
            await chunk({}, 'tool_calls')
        else:
            for token in ['The ', 'offline ', 'fixture ', 'is ', 'ready.']:
                await chunk({"content": token})
                await asyncio.sleep(.005)
            await chunk({}, 'stop')
        await reply.write(b'data: [DONE]\n\n')
        await reply.write_eof()
        return reply

    app = web.Application()
    app.router.add_route('*', '/v1/{tail:.*}', model)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    import socket
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    sock.listen(16)
    port = sock.getsockname()[1]
    await web.SockSite(runner,sock).start()
    started = time.perf_counter()
    agent = await asyncio.to_thread(AIAgent, model='s1-fixture', provider='custom',
        base_url=f'http://127.0.0.1:{port}/v1', api_key='offline-fixture', api_mode='chat_completions',
        enabled_toolsets=['s1_fixture','memory'], quiet_mode=True, skip_context_files=True,
        skip_memory=False, skip_background_review=True, max_iterations=4,
        session_id='s1-offline-fixture', cwd=str(Path(__file__).parent))
    init_ms = (time.perf_counter()-started)*1000
    history = []
    def turn(text, emit):
        nonlocal history
        result = agent.run_conversation(text, conversation_history=history, stream_callback=emit)
        assert result.get('final_response') == 'The offline fixture is ready.', result.get('error')
        history = result['messages']
    server = RelayServer(turn=turn)
    server.start()
    results = []
    try:
        async with ClientSession() as session:
            async with session.get(f'http://127.0.0.1:{server.port}/health') as health:
                assert (await health.json())['ok']
            async with session.post(f'http://127.0.0.1:{server.port}/webhook',json={}) as refused:
                assert refused.status == 403
            async with session.ws_connect(f'http://127.0.0.1:{server.port}/relay') as ws:
                await ws.send_json({'type':'setup','sessionId':'offline-fixture'})
                for i in range(21):
                    kind = 'cold' if i == 0 else ('tool' if i % 2 == 0 else 'text')
                    prompt = 'Check fixture calendar' if kind == 'tool' else 'Hello offline fixture'
                    before = time.perf_counter()
                    await ws.send_json({'type':'prompt','voicePrompt':prompt,'last':True})
                    first = None
                    output = ''
                    while True:
                        frame = await asyncio.wait_for(ws.receive_json(), timeout=30)
                        if frame.get('token'):
                            if frame['token'].strip():
                                first = first or time.perf_counter()
                            output += frame['token']
                        if frame.get('last'):
                            break
                    end = time.perf_counter()
                    assert output.strip() == 'The offline fixture is ready.', repr(output)
                    results.append({'kind':kind,'first_ms':round((first-before)*1000,3),
                                    'full_ms':round((end-before)*1000,3)})
    finally:
        await asyncio.to_thread(server.stop)
        await asyncio.to_thread(agent.close)
        await runner.cleanup()
    assert len(calls) == 10, len(calls)
    assert all(seen_memory), 'memory not present in every model request'
    summary = {'mode':'offline deterministic model; real Hermes AIAgent and tool dispatch',
               'machine':platform.machine(),'os':platform.mac_ver()[0], 'init_ms':round(init_ms,3),
               'calendar_calls':len(calls),'memory_in_every_request':all(seen_memory),'turns':results}
    for kind in ['text','tool']:
        group=[r for r in results if r['kind']==kind]
        summary[kind]={}
        for metric in ['first_ms','full_ms']:
            values=sorted(r[metric] for r in group)
            summary[kind][metric]={'median':round(statistics.median(values),3),
                                  'p95':values[math.ceil(.95*len(values))-1]}
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
