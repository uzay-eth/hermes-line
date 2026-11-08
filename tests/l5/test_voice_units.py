"""Unit tests: µ-law/DTMF, the Grok adapter's wire format, xAI STT, the Media Streams
bridge, the gate table, settings, TwiML and the phrase cache."""
from __future__ import annotations

import asyncio
import json
import unittest

from helpers import run

from plugin.line.voice import mulaw
from plugin.line.voice.gate import ConnectionMeter, SessionGate
from plugin.line.voice.grok import GrokRealtimeProvider, session_payload
from plugin.line.voice.mediastreams import MediaStreamBridge
from plugin.line.voice.phrases import PhraseCache, cacheable
from plugin.line.voice.provider import (AudioOut, ProviderClosed, ProviderError, ProviderUnavailable, ResponseDone,
                                        SessionConfig, SpeechStarted, ToolCall, Transcript)
from plugin.line.voice.settings import VoiceSettings, select_transport
from plugin.line.voice.stt import XaiStreamingTranscriber, parse_final
from plugin.line.voice.testing import FakeMediaStreamsClient, FakeWs, VirtualClock, fake_connector
from plugin.line.voice.tools import TOOL_SPECS
from plugin.line.voice.twiml import connect_stream, response

FAKE_KEY = "xai-test-not-a-real-key"


def ready_on_update(msg):
    return [{"type": "session.updated", "session": {}}] if msg.get("type") == "session.update" else []


class MulawTests(unittest.TestCase):
    def test_roundtrip_is_close(self):
        for sample in (-32000, -1000, -10, 0, 10, 1000, 32000):
            back = mulaw.decode(bytes([mulaw.encode_sample(sample)]))[0]
            self.assertLessEqual(abs(back - sample), max(16, abs(sample) // 16), sample)
        self.assertEqual(mulaw.rms(mulaw.silence(100)), 0.0)

    def test_dtmf_tones_hit_the_right_frequencies(self):
        for key, (low, high) in mulaw.DTMF_FREQS.items():
            if key in "ABCD":
                continue
            audio = mulaw.dtmf_tones(key)
            tone = mulaw.decode(audio[:120 * 8])
            powers = {f: mulaw.goertzel(tone, f) for f in (697, 770, 852, 941, 1209, 1336, 1477, 1633)}
            top = sorted(powers, key=powers.get, reverse=True)[:2]
            self.assertEqual(sorted(top), sorted([low, high]), key)
        self.assertEqual(len(mulaw.dtmf_tones("1w2")), (120 + 80) * 8 * 2 + 500 * 8)
        for bad in ("A", "1,2", "x", "+1"):
            with self.assertRaises(ValueError):
                mulaw.dtmf_tones(bad)


class GrokAdapterTests(unittest.TestCase):
    def test_connect_sends_pcmu_session_and_waits_for_ready(self):
        async def go():
            ws = FakeWs(ready_on_update)
            got = []
            p = GrokRealtimeProvider(lambda name: got.append(name) or FAKE_KEY, connector=fake_connector(ws))
            cfg = SessionConfig("conversation", "be brief", "eve", list(TOOL_SPECS), True, reasoning_effort="none")
            await p.connect(cfg)
            self.assertTrue(p.connected)
            self.assertEqual(got, ["XAI_API_KEY"])
            self.assertEqual(ws.url, "wss://api.x.ai/v1/realtime?model=grok-voice-latest")
            self.assertEqual(ws.headers, {"Authorization": f"Bearer {FAKE_KEY}"})
            self.assertNotIn(FAKE_KEY, json.dumps(vars(p), default=str))   # key not kept on the instance
            update = ws.sent_json()[0]
            self.assertEqual(update["type"], "session.update")
            sess = update["session"]
            self.assertEqual(sess["audio"]["input"]["format"], {"type": "audio/pcmu"})
            self.assertEqual(sess["audio"]["output"]["format"], {"type": "audio/pcmu"})
            self.assertEqual(sess["turn_detection"], {"type": "server_vad"})
            self.assertEqual(sess["audio"]["input"]["transcription"], {"model": "grok-transcribe"})
            self.assertEqual(sess["reasoning"], {"effort": "none"})
            self.assertEqual(sess["tools"][0], {"type": "function", "name": "ask_hermes",
                                                "description": TOOL_SPECS[0].description,
                                                "parameters": TOOL_SPECS[0].parameters})
            await p.close()
        run(go())

    def test_speech_session_has_no_vad_and_no_tools(self):
        sess = session_payload(SessionConfig("speech", "tts", "eve", [], False))
        self.assertIsNone(sess["turn_detection"])
        self.assertEqual(sess["tools"], [])
        self.assertNotIn("transcription", sess["audio"]["input"])

    def test_missing_key_or_refused_session(self):
        async def go():
            ws = FakeWs(ready_on_update)
            with self.assertRaises(ProviderUnavailable):
                await GrokRealtimeProvider(lambda n: None, connector=fake_connector(ws)).connect(SessionConfig())
            self.assertEqual(ws.sent, [])

            async def boom(url, headers):
                raise OSError("no route")
            with self.assertRaises(ProviderUnavailable) as ctx:
                await GrokRealtimeProvider(lambda n: FAKE_KEY, connector=boom).connect(SessionConfig())
            self.assertNotIn(FAKE_KEY, str(ctx.exception))

            ws = FakeWs(lambda m: [{"type": "error", "error": {"code": "invalid_api_key", "message": "x"}}])
            with self.assertRaises(ProviderUnavailable):
                await GrokRealtimeProvider(lambda n: FAKE_KEY, connector=fake_connector(ws),
                                           ready_timeout_s=0.5).connect(SessionConfig())
        run(go())

    def test_client_events_match_the_documented_shapes(self):
        async def go():
            ws = FakeWs(ready_on_update)
            p = GrokRealtimeProvider(lambda n: FAKE_KEY, connector=fake_connector(ws))
            await p.connect(SessionConfig())
            await p.send_audio("AAAA")
            await p.say_exact("This call is being handled by an AI assistant.")
            await p.add_context("[context] hi")
            await p.send_tool_result("call_1", '{"ok":true}', respond=True)
            await p.truncate("item_1", 640)
            await p.respond("Apologize briefly.")
            sent = ws.sent_json()[1:]
            self.assertEqual(sent[0], {"type": "input_audio_buffer.append", "audio": "AAAA"})
            self.assertEqual(sent[1], {"type": "conversation.item.create", "item": {
                "type": "force_message", "role": "assistant", "interruptible": False,
                "content": [{"type": "output_text", "text": "This call is being handled by an AI assistant."}]}})
            self.assertEqual(sent[2]["item"], {"type": "message", "role": "user",
                                               "content": [{"type": "input_text", "text": "[context] hi"}]})
            self.assertEqual(sent[3], {"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}})
            self.assertEqual(sent[4], {"type": "response.create"})
            self.assertEqual(sent[5], {"type": "conversation.item.truncate", "item_id": "item_1",
                                       "content_index": 0, "audio_end_ms": 640})
            self.assertEqual(sent[6], {"type": "response.create", "response": {"instructions": "Apologize briefly."}})
            await p.close()
        run(go())

    def test_server_events_are_mapped_and_tool_calls_deduplicated(self):
        async def go():
            ws = FakeWs(ready_on_update)
            p = GrokRealtimeProvider(lambda n: FAKE_KEY, connector=fake_connector(ws))
            await p.connect(SessionConfig())
            for ev in (
                {"type": "response.output_audio.delta", "delta": "AQID", "item_id": "i1", "response_id": "r1"},
                {"type": "response.audio.delta", "delta": "BAUG", "item_id": "i1", "response_id": "r1"},
                {"type": "input_audio_buffer.speech_started", "item_id": "u1"},
                {"type": "response.function_call_arguments.done", "call_id": "c1", "name": "ask_hermes",
                 "arguments": "{\"question\":\"q\"}"},
                {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c1",
                                                               "name": "ask_hermes", "arguments": "{}"}},
                {"type": "conversation.item.input_audio_transcription.completed", "transcript": "hello"},
                {"type": "response.output_audio_transcript.done", "transcript": "hi there"},
                {"type": "response.done", "response": {"id": "r1", "status": "cancelled"}},
                {"type": "error", "error": {"code": "rate_limit", "message": "slow down"}},
                {"type": "rate_limits.updated"},
            ):
                ws.push(ev)
            got = [await p.recv() for _ in range(8)]
            self.assertEqual(got[0], AudioOut("AQID", "i1", "r1"))
            self.assertEqual(got[1], AudioOut("BAUG", "i1", "r1"))
            self.assertEqual(got[2], SpeechStarted("u1"))
            self.assertEqual(got[3], ToolCall("c1", "ask_hermes", "{\"question\":\"q\"}"))
            self.assertEqual(got[4], Transcript("business", "hello"))
            self.assertEqual(got[5], Transcript("agent", "hi there"))
            self.assertEqual(got[6], ResponseDone("r1", "cancelled"))
            self.assertEqual(got[7], ProviderError("rate_limit", "slow down"))
            await ws.close()
            self.assertEqual(await p.recv(), ProviderClosed(expected=False, reason="dropped"))
            self.assertIsNone(await p.recv())
        run(go())


class SttTests(unittest.TestCase):
    def test_streams_binary_mulaw_and_emits_final_utterances(self):
        async def go():
            ws = FakeWs()
            t = XaiStreamingTranscriber(lambda n: FAKE_KEY, connector=fake_connector(ws), endpointing_ms=700)
            heard = []

            async def sink(text):
                heard.append(text)
            await t.start(sink)
            self.assertTrue(ws.url.startswith("wss://api.x.ai/v1/stt?"))
            for part in ("encoding=mulaw", "sample_rate=8000", "endpointing=700", "interim_results=false"):
                self.assertIn(part, ws.url)
            await t.feed(mulaw.b64(b"\x01\x02\x03"))
            self.assertEqual(ws.sent, [b"\x01\x02\x03"])           # raw bytes, no base64, no JSON
            ws.push({"type": "transcript.partial", "text": "please", "is_final": False, "speech_final": False})
            ws.push({"type": "transcript.partial", "text": "please hold", "is_final": True, "speech_final": False})
            ws.push({"type": "transcript.partial", "text": "Please hold.", "is_final": True, "speech_final": True})
            ws.push({"type": "transcript.done", "text": "Please hold."})
            await asyncio.sleep(0.01)
            self.assertEqual(heard, ["Please hold."])
            await t.stop()
            self.assertEqual(json.loads(ws.sent[-1]), {"type": "audio.done"})
            self.assertFalse(t.running)
        run(go())

    def test_parse_final_rejects_noise(self):
        self.assertIsNone(parse_final(b"\x00"))
        self.assertIsNone(parse_final("not json"))
        self.assertIsNone(parse_final(json.dumps({"type": "transcript.partial", "text": " ", "is_final": True,
                                                  "speech_final": True})))
        self.assertIsNone(parse_final(json.dumps({"type": "error", "message": "x"})))

    def test_missing_key(self):
        async def go():
            with self.assertRaises(ProviderUnavailable):
                await XaiStreamingTranscriber(lambda n: "", connector=fake_connector(FakeWs())).start(None)
        run(go())


class Recorder:
    def __init__(self):
        self.starts, self.media, self.dtmf, self.marks, self.ends = [], [], [], [], []

    async def on_stream_start(self, bridge, info):
        self.starts.append(info)

    async def on_media(self, payload, ts):
        self.media.append((payload, ts))

    async def on_dtmf(self, digit):
        self.dtmf.append(digit)

    async def on_mark(self, name, cleared):
        self.marks.append((name, cleared))

    async def on_stream_end(self, bridge, reason):
        self.ends.append(reason)


class BridgeTests(unittest.TestCase):
    def test_events_marks_clear_and_validation(self):
        async def go():
            rec = Recorder()
            client = FakeMediaStreamsClient()
            bridge = MediaStreamBridge(rec, expected_call_sid=client.call_sid)
            task = asyncio.get_running_loop().create_task(bridge.run(client.socket))
            client.media(b"\xff" * 160, 0)                         # before start: ignored
            client.start({"call": "c1"})
            client.media(b"\x10" * 160, 20)
            client.send({"event": "media", "streamSid": client.stream_sid,
                         "media": {"track": "outbound", "payload": "AAAA"}})
            client.dtmf("5")
            client.dtmf("55")                                      # malformed digit
            await asyncio.sleep(0.01)
            self.assertEqual(rec.starts[0].custom, {"call": "c1"})
            self.assertEqual(len(rec.media), 1)
            self.assertEqual(rec.media[0][1], 20)
            self.assertEqual(rec.dtmf, ["5"])
            bridge.play(b"\x00" * 3200)
            bridge.send_mark("m1")
            bridge.send_mark("m2")
            await asyncio.sleep(0.01)
            self.assertEqual(len(client.of("media")), 2)            # 400 ms in 200 ms messages
            self.assertTrue(all(m["streamSid"] == client.stream_sid for m in client.received))
            client.send({"event": "mark", "streamSid": client.stream_sid, "mark": {"name": "m1"}})
            await asyncio.sleep(0.01)
            self.assertEqual(rec.marks, [("m1", False)])
            cleared = bridge.clear()
            await asyncio.sleep(0.01)
            self.assertEqual(cleared, {"m2"})
            self.assertEqual(rec.marks[-1], ("m2", True))           # echoed back by "Twilio" on clear
            self.assertFalse(bridge.playing)
            client.stop()
            self.assertEqual(await asyncio.wait_for(task, 1), "stop")
            self.assertEqual(rec.ends, ["stop"])
        run(go())

    def test_wrong_format_or_call_is_refused(self):
        async def go():
            for kwargs, expected in (({"encoding": "audio/l16"}, "bad_format"),
                                     ({"call_sid": "CA" + "9" * 32}, "wrong_call")):
                rec = Recorder()
                client = FakeMediaStreamsClient()
                bridge = MediaStreamBridge(rec, expected_call_sid=client.call_sid)
                task = asyncio.get_running_loop().create_task(bridge.run(client.socket))
                client.start(**kwargs)
                self.assertEqual(await asyncio.wait_for(task, 1), expected)
                self.assertEqual(rec.starts, [])
                self.assertEqual(client.closed_code, 1008)
        run(go())

    def test_oversized_frame_ends_the_stream(self):
        async def go():
            rec = Recorder()
            client = FakeMediaStreamsClient()
            bridge = MediaStreamBridge(rec)
            task = asyncio.get_running_loop().create_task(bridge.run(client.socket))
            client.start()
            client._in.put_nowait("x" * (70 * 1024))
            self.assertEqual(await asyncio.wait_for(task, 1), "bad_frame")
        run(go())


class GateTests(unittest.TestCase):
    def test_decision_table(self):
        clock = VirtualClock()
        g = SessionGate(clock)
        for state in ("dialing", "ivr", "on_hold", "owner_connected", "awaiting_approval", "ended", "failed"):
            g.on_state(state)
            self.assertFalse(g.decide()[0], state)
        g.on_state("ringing_owner")                     # owner rung from hold (take-over): stays closed
        self.assertFalse(g.decide()[0])
        g.on_state("on_hold")
        g.on_state("human_detected")
        self.assertEqual(g.decide(), (True, "human"))
        g.on_state("ringing_owner")
        self.assertEqual(g.decide(), (True, "ringing_owner"))
        g.on_state("on_hold")
        self.assertFalse(g.decide()[0])
        g.speech_pending = 1
        self.assertEqual(g.decide(), (True, "speech"))
        g.on_state("ended")
        self.assertEqual(g.speech_pending, 0)
        g2 = SessionGate(clock)
        g2.on_state("on_hold")
        g2.prewarm(5)
        self.assertEqual(g2.decide(), (True, "prewarm"))
        clock.advance(6)
        self.assertFalse(g2.decide()[0])
        g2.on_state("bogus")
        self.assertEqual(g2.state, "on_hold")

    def test_meter(self):
        clock = VirtualClock()
        m = ConnectionMeter(clock)
        m.open("a")
        clock.advance(10)
        m.close()
        clock.advance(100)
        m.open("b")
        clock.advance(5)
        self.assertAlmostEqual(m.seconds(), 15)
        self.assertAlmostEqual(m.seconds_between(clock() - 50, clock()), 5)


class SettingsTests(unittest.TestCase):
    def test_transport_choice(self):
        s = VoiceSettings()
        self.assertEqual(select_transport(s, lambda n: FAKE_KEY).transport, "media_streams")
        self.assertEqual(select_transport(s, lambda n: None).transport, "conversation_relay")
        self.assertEqual(select_transport(s, lambda n: "  ").transport, "conversation_relay")
        forced = VoiceSettings.load({"transport": "media_streams"})
        self.assertEqual(select_transport(forced, lambda n: None).transport, "refused")
        relay = VoiceSettings.load({"transport": "conversation_relay"})
        self.assertEqual(select_transport(relay, lambda n: FAKE_KEY).transport, "conversation_relay")

        def boom(n):
            raise RuntimeError("scope")
        self.assertEqual(select_transport(s, boom).transport, "conversation_relay")
        for choice in (select_transport(s, lambda n: FAKE_KEY), select_transport(forced, lambda n: None)):
            self.assertNotIn(FAKE_KEY, choice.reason)

    def test_validation(self):
        for bad in ({"transport": "sip"}, {"provider": "other"}, {"ask_timeout_s": 0}, {"nope": 1},
                    {"max_reconnects": 9}, {"conversation": "x"}, {"reasoning_effort": "low"}):
            with self.assertRaises((ValueError, TypeError)):
                VoiceSettings.load(bad)


class TwimlAndCacheTests(unittest.TestCase):
    def test_connect_stream(self):
        xml = response(connect_stream(ws_url="wss://line.test/twilio/stream/c1/tok", call_id="c1",
                                      action_url="https://line.test/twilio/voice/connect/action/c1"))
        self.assertEqual(xml, '<?xml version="1.0" encoding="UTF-8"?><Response><Connect '
                              'action="https://line.test/twilio/voice/connect/action/c1" method="POST"><Stream '
                              'url="wss://line.test/twilio/stream/c1/tok"><Parameter name="call" value="c1"/>'
                              '</Stream></Connect></Response>')
        with self.assertRaises(ValueError):
            connect_stream(ws_url="wss://x/s?t=1", call_id="c", action_url="https://x/a")
        with self.assertRaises(ValueError):
            connect_stream(ws_url="ws://x/s", call_id="c", action_url="https://x/a")
        injected = connect_stream(ws_url="wss://x/s", call_id='c"><Hangup/>', action_url="https://x/a")
        self.assertNotIn("<Hangup/>", injected)
        self.assertEqual(injected.count("<Parameter"), 1)

    def test_phrase_cache(self):
        c = PhraseCache(max_bytes=1000)
        self.assertFalse(cacheable("Call me at 775 555 0101"))
        self.assertFalse(c.put("eve", "Code 1234", b"\x01" * 10))
        self.assertTrue(c.put("eve", "Hello  there", b"\x01" * 200))
        self.assertEqual(c.get("eve", "hello there"), b"\x01" * 200)
        self.assertIsNone(c.get("ara", "hello there"))
        for i in range(10):
            c.put("eve", f"line {'x' * i}", b"\x02" * 200)
        self.assertLessEqual(sum(len(v) for v in c._items.values()), 1000)
        self.assertIsNone(c.digit_clips("eve"))


if __name__ == "__main__":
    unittest.main()
