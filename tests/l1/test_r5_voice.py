"""Round 5: the realtime voice path (L5) wired into the real engine and the real ingress.

A fake Twilio Media Streams client speaks the real WebSocket protocol to L1's authenticated
``/twilio/media/{call_id}/{token}`` route on loopback. Grok and the xAI transcriber are L5's
fakes, injected through ``CallService(voice_factories=...)``. No network, no key, no phone.
"""
import asyncio
import json
import re
import threading
import time
import unittest

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

from harness import ACCOUNT, AUTH_TOKEN, BUSINESS, OWNER, SECRETS, Line, wait_for
from plugin.line.engine.voice_port import ASK_PREFIX
from plugin.line.server import twilio_signature
from plugin.line.voice import mulaw
from plugin.line.voice.testing import FakeProviderHub, FakeTranscriberHub

XAI = {**SECRETS, "XAI_API_KEY": "sample-key-not-a-secret"}
XAI_NO_TOKEN = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT, "XAI_API_KEY": "sample-key-not-a-secret"}
HOLD = "Please hold; all representatives are assisting other callers."
GREETING = "Hello, this is Dana speaking. How can I help you today?"
CONFIRM = "Yes, I'm a real person. How can I help?"
VOICE = {"reconnect_backoff_s": 0.01, "idle_hold_s": 600.0}


def stream_url(twiml_body: str) -> str:
    m = re.search(r'<Stream url="([^"]+)"', twiml_body)
    assert m, twiml_body
    return m.group(1).replace("&amp;", "&")


class MediaClient:
    """Twilio's side of a bidirectional Media Stream over a real WebSocket. Marks are echoed back at once
    (audio "played"), and every outstanding mark on ``clear``, like Twilio."""

    def __init__(self, line: Line, public_url: str, *, sign: bool = True, signature=None):
        local = public_url.replace("wss://line.test", f"ws://127.0.0.1:{line.svc.server.port}")
        headers = {}
        if sign:
            headers["X-Twilio-Signature"] = signature or twilio_signature.compute(AUTH_TOKEN, public_url, [])
        self.ws = ws_connect(local, additional_headers=headers, open_timeout=5, close_timeout=2)
        self.received, self.closed_code = [], None
        self.stream_sid = "MZ" + "2" * 32
        self._lock = threading.Lock()
        self._chunk = 0
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for message in self.ws:
                msg = json.loads(message)
                with self._lock:
                    self.received.append(msg)
                if msg.get("event") == "mark":
                    self.send({"event": "mark", "streamSid": self.stream_sid, "mark": msg["mark"]})
        except Exception:
            pass
        finally:
            self.closed_code = getattr(self.ws, "close_code", None) or -1

    def send(self, msg):
        try:
            self.ws.send(json.dumps(msg))
        except Exception:
            pass

    def start(self, call_sid: str, custom=None):
        self.send({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        self.send({"event": "start", "sequenceNumber": "1", "streamSid": self.stream_sid,
                   "start": {"streamSid": self.stream_sid, "accountSid": ACCOUNT, "callSid": call_sid,
                             "tracks": ["inbound"], "customParameters": custom or {},
                             "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})

    def media(self, audio: bytes):
        self._chunk += 1
        self.send({"event": "media", "streamSid": self.stream_sid,
                   "media": {"track": "inbound", "chunk": str(self._chunk), "timestamp": str(self._chunk * 20),
                             "payload": mulaw.b64(audio)}})

    def of(self, event):
        with self._lock:
            return [m for m in self.received if m.get("event") == event]

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class VoiceLine:
    def __init__(self, testcase, *, secrets=XAI, voice=None, **kw):
        self.hub, self.stt = FakeProviderHub(), FakeTranscriberHub()
        self.line = Line(secrets=secrets, voice_factories={"provider": self.hub, "transcriber": self.stt},
                         voice=dict(VOICE if voice is None else voice), **kw)
        testcase.addCleanup(self.line.close)

    def say(self, text: str, settle_s: float = 0.3):
        async def go():
            ok = await self.stt.utter(text)
            await asyncio.sleep(settle_s)
            return ok
        return self.line.svc.run(go(), timeout=30)

    def tool(self, name: str, args: dict, timeout: float = 10.0) -> dict:
        async def go():
            provider = self.hub.last
            n = len(provider.tool_results)
            provider.tool_call(name, args)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(0.01)
                if len(provider.tool_results) > n:
                    return provider.tool_results[-1]["output"]
            raise AssertionError(f"no result for {name}")
        return self.line.svc.run(go(), timeout=timeout + 5)


class EndToEndMediaStreamsTests(unittest.TestCase):
    def test_R5_fake_call_over_media_streams_through_the_real_ingress(self):
        v = VoiceLine(self)
        line = v.line
        req, created = line.dial(mode="agent")
        cid, sid = req["call_id"], created["sid"]
        self.assertEqual(line.store.get_call(cid).transport, "media_streams")
        body = line.answer(cid, sid)
        self.assertIn("<Stream", body)
        self.assertNotIn("ConversationRelay", body)
        self.assertIn(f"/twilio/voice/connect/action/{cid}?g=0", body)
        self.assertIn(f"/twilio/media/{cid}/", stream_url(body))
        session = line.session(cid)
        self.assertIsNotNone(session.voice)

        client = MediaClient(line, stream_url(body))
        client.start(sid)
        wait_for(lambda: session.relay is not None and session.voice.bridge is not None, what="stream attached")
        # 1. hold: the transcriber listens, the voice provider stays closed
        self.assertTrue(v.say(HOLD))
        line.wait_state(cid, "on_hold")
        wait_for(lambda: v.hub.connected_now == 0, what="provider closed on hold")
        hold_start = session.voice.clock()
        frames_before = v.stt.frames
        frame = mulaw.tone([440, 554], 20, amplitude=0.1)
        for _ in range(50):  # one second of hold music
            client.media(frame)
            time.sleep(0.02)
        wait_for(lambda: v.stt.frames >= frames_before + 50, what="hold audio to the transcriber")
        self.assertTrue(v.say(HOLD))
        hold_end = session.voice.clock()
        self.assertEqual(session.voice.meter.seconds_between(hold_start, hold_end), 0.0)
        self.assertEqual(line.state(cid), "on_hold")
        # 2. a human: L2 round 6 takes a named greeting after hold as a human verdict (high), so Grok holds the
        # conversation without the live-person question
        v.say(GREETING)
        line.wait_state(cid, "agent_talking")
        wait_for(lambda: session.voice.purpose == "conversation", what="grok conversation")
        self.assertIn("human", [e["data"].get("label") for e in line.store.events(cid) if e["kind"] == "detect"])
        said = [t for p in v.hub.instances for t in p.said]
        self.assertNotIn(line.cfg.confirm_question(), said)
        v.say(CONFIRM)
        self.assertTrue(v.hub.last.connected)
        # 3. a Grok tool call into Hermes: one turn on the call's worker, marked as relayed and untrusted
        turns_before = len(line.drivers[0].turns)
        out = v.tool("ask_hermes", {"question": "Which day did we want?"})
        self.assertTrue(out["ok"], out)
        new_turns = line.drivers[0].turns[turns_before:]
        self.assertTrue(any(str(t).startswith(ASK_PREFIX) for t in new_turns), new_turns)
        self.assertIn("book a table", json.dumps(out))
        # 4. owner bridge: Grok asks for the owner, the owner presses 1, the stream hands off, the conference
        before = list(line.adapter.created)
        out = v.tool("bring_owner", {})
        self.assertTrue(out["ok"], out)
        line.wait_state(cid, "ringing_owner")
        owner_legs = [c for c in line.adapter.created[len(before):] if c["to"] == OWNER]
        self.assertEqual(len(owner_legs), 1)
        status, _ = line.post(f"/twilio/voice/owner/accept/{cid}", {"CallSid": owner_legs[0]["sid"], "Digits": "1"})
        self.assertEqual(status, 200)
        line.wait_state(cid, "owner_connected")
        wait_for(lambda: client.closed_code is not None, what="stream closed for the hand-off")
        wait_for(lambda: v.hub.connected_now == 0, what="provider closed after the hand-off")
        status, action = line.post(f"/twilio/voice/connect/action/{cid}?g=0", {"CallSid": sid})
        self.assertEqual(status, 200)
        self.assertIn("<Conference", action)
        self.assertEqual(session.reconnects, 0)  # a hand-off is not a recovery
        # 5. end: the conference ends
        status, _ = line.post(f"/twilio/voice/conference/status/{cid}",
                              {"FriendlyName": session.conference, "StatusCallbackEvent": "conference-end"})
        self.assertEqual(status, 204)
        line.wait_state(cid, "ended")
        self.assertIsNone(v.stt.active)
        self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS, OWNER])
        kinds = [e["kind"] for e in line.store.events(cid)]
        self.assertNotIn("ingress_rejected", kinds)
        self.assertNotIn("voice_failed", kinds)
        client.close()


class DigitsFallbackRouteTests(unittest.TestCase):
    def test_R5_digits_route_plays_once_then_opens_the_next_generation(self):
        v = VoiceLine(self)
        line = v.line
        req, created = line.dial(mode="agent")
        cid, sid = req["call_id"], created["sid"]
        body = line.answer(cid, sid)
        client = MediaClient(line, stream_url(body))
        client.start(sid)
        session = line.session(cid)
        wait_for(lambda: session.relay is not None and session.voice.bridge is not None, what="stream attached")
        v.say(HOLD)
        line.wait_state(cid, "on_hold")
        session.voice._redirect_mode = True  # the menu ignored tones earlier: L5 now presses through the fallback

        async def press():
            return await session.press_digits("2")
        out = line.svc.run(press())
        self.assertEqual(out, {"ok": True, "via": "redirect"})
        self.assertEqual(len(line.adapter.redirected), 1)
        redirected_sid, url = line.adapter.redirected[0]
        self.assertEqual(redirected_sid, sid)
        path = url.replace("https://line.test", "")
        self.assertIn(f"/twilio/voice/digits/{cid}/", path)
        client.close()  # Twilio leaves the stream for the redirected document
        wait_for(lambda: session.relay is None, what="stream left")
        status, doc = line.post(path.replace(line.svc.auth.prefix(), ""), {"CallSid": sid})
        self.assertEqual(status, 200)
        self.assertIn('<Play digits="2"', doc)
        self.assertIn("<Stream", doc)
        self.assertIn(f"?g=1", doc)
        self.assertEqual(session.relay_gen, 1)
        self.assertEqual(session.reconnects, 0)  # a redirect is not a recovery
        status, again = line.post(path.replace(line.svc.auth.prefix(), ""), {"CallSid": sid})  # nonce is single use
        self.assertNotIn("<Play", again)
        self.assertIn(session.gen_twiml[1], again)
        self.assertIn("digits_nonce", [e["data"].get("reason") for e in line.store.events(cid)
                                       if e["kind"] == "ingress_rejected"])
        status, stale = line.post(f"/twilio/voice/connect/action/{cid}?g=0", {"CallSid": sid})
        self.assertNotIn("<Hangup", stale)  # the old generation's completion changes nothing
        self.assertEqual((session.relay_gen, session.reconnects), (1, 0))
        nxt = MediaClient(line, stream_url(doc))
        nxt.start(sid)
        wait_for(lambda: session.relay is not None, what="generation 1 attached")
        self.assertEqual(line.state(cid), "on_hold")
        self.assertNotIn("dropped", [e["kind"] for e in line.store.events(cid)])
        nxt.close()


class N2DelayedDigitsAfterHandoffTests(unittest.TestCase):
    """R1 delta 2 N2: a valid digits nonce issued on hold, arriving after the owner took over."""
    MODES = (("signature", XAI), ("path_secret", XAI_NO_TOKEN))

    def test_N2_delayed_digits_webhook_after_owner_takeover_changes_nothing(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                v = VoiceLine(self, secrets=secrets)
                line, sign = v.line, "HERMES_LINE_TWILIO_AUTH_TOKEN" in secrets
                req, created = line.dial(mode="agent")
                cid, sid = req["call_id"], created["sid"]
                body = line.post(f"/twilio/voice/answer/{cid}", {"CallSid": sid}, sign=sign)[1]
                client = MediaClient(line, stream_url(body), sign=sign)
                client.start(sid)
                session = line.session(cid)
                wait_for(lambda: session.relay is not None and session.voice.bridge is not None, what="stream")
                v.say(HOLD)
                line.wait_state(cid, "on_hold")
                session.voice._redirect_mode = True

                async def press():
                    return await session.press_digits("2")
                self.assertEqual(line.svc.run(press()), {"ok": True, "via": "redirect"})
                url = line.adapter.redirected[-1][1]
                nonce = url.rsplit("/", 1)[1]
                digits_path = url.replace("https://line.test", "").replace(line.svc.auth.prefix(), "")
                # the owner takes over before Twilio fetches the redirected document
                out = line.svc.submit("take_over", source="slash", call_id=cid)
                self.assertTrue(out["result"]["ok"], out)
                line.wait_state(cid, "ringing_owner")
                owner = line.adapter.created[-1]
                self.assertEqual(owner["to"], OWNER)
                line.post(f"/twilio/voice/owner/answer/{cid}", {"CallSid": owner["sid"]}, sign=sign)
                status, joined = line.post(f"/twilio/voice/owner/accept/{cid}",
                                           {"CallSid": owner["sid"], "Digits": "1"}, sign=sign)
                self.assertEqual(status, 200)
                line.wait_state(cid, "owner_connected")
                gen_before, tokens_before = session.relay_gen, sorted(session.gen_twiml)
                redirects_before = len(line.adapter.redirected)
                # the delayed digits webhook
                status, doc = line.post(digits_path, {"CallSid": sid}, sign=sign)
                self.assertEqual(status, 200)
                self.assertNotIn("<Play", doc)
                self.assertNotIn("<Stream", doc)
                self.assertIn("<Conference", doc)
                self.assertEqual((session.relay_gen, sorted(session.gen_twiml)), (gen_before, tokens_before))
                self.assertIsNone(session.voice.take_redirect_digits(nonce))  # the nonce was consumed
                self.assertEqual(line.state(cid), "owner_connected")
                refused = [e["data"] for e in line.store.events(cid) if e["kind"] == "digits_refused"]
                self.assertEqual(refused, [{"state": "owner_connected", "route": "voice/digits"}])
                status, again = line.post(digits_path, {"CallSid": sid}, sign=sign)  # replay: still the conference
                self.assertIn("<Conference", again)
                self.assertNotIn("<Play", again)
                # the voice port no longer redirects, and the menu gate refuses presses
                self.assertFalse(line.svc.run(session.voice.port.redirect_digits("n" * 22))["ok"])
                self.assertFalse(line.svc.run(press())["ok"])
                self.assertEqual(len(line.adapter.redirected), redirects_before)
                client.close()

    def test_N2_digits_refused_while_the_owner_is_ringing(self):
        v = VoiceLine(self)
        line = v.line
        req, created = line.dial(mode="agent")
        cid, sid = req["call_id"], created["sid"]
        client = MediaClient(line, stream_url(line.answer(cid, sid)))
        client.start(sid)
        session = line.session(cid)
        wait_for(lambda: session.relay is not None and session.voice.bridge is not None, what="stream")
        v.say(HOLD)
        line.wait_state(cid, "on_hold")
        session.voice._redirect_mode = True

        async def press():
            return await session.press_digits("3")
        self.assertTrue(line.svc.run(press())["ok"])
        digits_path = line.adapter.redirected[-1][1].replace("https://line.test", "").replace(
            line.svc.auth.prefix(), "")
        self.assertTrue(line.svc.submit("take_over", source="slash", call_id=cid)["result"]["ok"])
        line.wait_state(cid, "ringing_owner")
        gen = session.relay_gen
        status, doc = line.post(digits_path, {"CallSid": sid})
        self.assertNotIn("<Play", doc)
        self.assertEqual(session.relay_gen, gen)
        self.assertIn(session.gen_twiml[gen], doc)  # the current generation again; nothing new minted
        self.assertEqual(line.state(cid), "ringing_owner")
        client.close()


class TransportChoiceTests(unittest.TestCase):
    def test_R5_without_xai_key_every_call_stays_conversation_relay(self):
        hub, stt = FakeProviderHub(), FakeTranscriberHub()
        line = Line(voice_factories={"provider": hub, "transcriber": stt})
        self.addCleanup(line.close)
        req, created = line.dial()
        self.assertEqual(line.store.get_call(req["call_id"]).transport, "conversation_relay")
        body = line.answer(req["call_id"], created["sid"])
        self.assertIn("<ConversationRelay", body)
        self.assertNotIn("<Stream", body)
        self.assertIsNone(line.session(req["call_id"]).voice)
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": created["sid"]})
        relay.prompt("Please hold.")
        line.wait_state(req["call_id"], "on_hold")
        self.assertEqual((hub.connect_attempts, len(hub.instances), stt.starts), (0, 0, 0))

    def test_R5_configured_conversation_relay_wins_over_the_key(self):
        line = Line(secrets=XAI, voice={"transport": "conversation_relay"})
        self.addCleanup(line.close)
        req, created = line.dial()
        self.assertIn("<ConversationRelay", line.answer(req["call_id"], created["sid"]))

    def test_R5_media_streams_required_without_key_refuses_before_the_adapter(self):
        line = Line(voice={"transport": "media_streams"})
        self.addCleanup(line.close)
        with self.assertRaises(Exception):
            line.dial()
        self.assertEqual(line.adapter.created, [])


class MediaRouteAuthTests(unittest.TestCase):
    MODES = (("signature", XAI), ("path_secret", XAI_NO_TOKEN))

    def _answered(self, testcase_secrets):
        v = VoiceLine(self, secrets=testcase_secrets)
        req, created = v.line.dial()
        sign = "HERMES_LINE_TWILIO_AUTH_TOKEN" in testcase_secrets
        body = v.line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]}, sign=sign)[1]
        return v, req, created, body, sign

    def _refused(self, line, url, sign=True, **kw):
        with self.assertRaises(InvalidStatus):
            MediaClient(line, url, sign=sign, **kw)

    def test_R5_media_route_rejects_bad_secret_token_and_kind(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                v, req, created, body, sign = self._answered(secrets)
                url = stream_url(body)
                prefix = v.line.svc.auth.prefix()
                if prefix:
                    self._refused(v.line, url.replace(prefix, "/t/" + "A" * 43), sign=sign)
                self._refused(v.line, url.rsplit("/", 1)[0] + "/" + "x" * 32, sign=sign)        # unknown token
                relay_token = v.line.svc.issue_stream_token(req["call_id"], kind="relay-g0")
                self._refused(v.line, url.rsplit("/", 1)[0] + "/" + relay_token, sign=sign)    # wrong kind
                if sign:
                    self._refused(v.line, url, sign=True, signature="bad")
                    self._refused(v.line, url, sign=False)
                client = MediaClient(v.line, url, sign=sign)  # the real one still works
                client.start(created["sid"])
                wait_for(lambda: v.line.session(req["call_id"]).relay is not None, what="stream attached")
                client.close()

    def test_R5_media_token_is_single_use_and_bound_to_its_generation(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                v, req, created, body, sign = self._answered(secrets)
                line, cid = v.line, req["call_id"]
                session = line.session(cid)
                gen0 = stream_url(body)
                # generation 0 never connected; its completion becomes a recovery to generation 1
                status, again = line.post(f"/twilio/voice/connect/action/{cid}?g=0", {"CallSid": created["sid"]},
                                          sign=sign)
                self.assertIn("<Stream", again)
                self.assertEqual(session.relay_gen, 1)
                self._refused(line, gen0, sign=sign)                    # a previous generation's unused token
                gen1 = stream_url(again)
                client = MediaClient(line, gen1, sign=sign)
                client.start(created["sid"])
                wait_for(lambda: session.relay is not None, what="generation 1 attached")
                client.close()
                wait_for(lambda: session.relay is None, what="generation 1 closed")
                self._refused(line, gen1, sign=sign)                    # single use
                kinds = [e["data"].get("reason") for e in line.store.events() if e["kind"] == "ingress_rejected"]
                self.assertIn("media_token", kinds)

    def test_R5_relay_route_refuses_a_media_token(self):
        v, req, created, body, sign = self._answered(XAI)
        token = stream_url(body).rsplit("/", 1)[1]
        url = re.sub(r"/twilio/media/[^/]+/[^/]+$", f"/twilio/relay/{req['call_id']}?t={token}", stream_url(body))
        with self.assertRaises(InvalidStatus):
            MediaClient(v.line, url)


if __name__ == "__main__":
    unittest.main()
