"""Offline fakes for the voice path: a fake realtime provider, a fake Twilio Media Streams
client, a fake transcriber and a virtual clock. Nothing here opens a socket.

They are part of the package (not only of tests/l5) so L1 can drive its own integration
tests through the same doubles.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from . import mulaw
from .provider import (AudioOut, ProviderClosed, ProviderUnavailable, RealtimeVoiceProvider, ResponseDone,
                       ResponseStarted, SessionConfig, SessionReady, SpeechStarted, ToolCall, Transcript)
from .stt import Transcriber


class VirtualClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def synthetic_speech(text: str, ms_per_char: int = 20) -> bytes:
    """Deterministic stand-in for TTS audio: a tone whose pitch depends on the text."""
    pitch = 300 + int(hashlib.sha256(text.encode()).hexdigest()[:2], 16)
    return mulaw.tone([pitch], max(40, len(text) * ms_per_char), amplitude=0.2)


class FakeRealtimeProvider(RealtimeVoiceProvider):
    """Speaks ``say_exact`` lines as synthetic audio; everything else is scripted by the test."""

    name = "fake"

    def __init__(self, hub: "FakeProviderHub") -> None:
        self.hub = hub
        self.config: Optional[SessionConfig] = None
        self.configs: List[SessionConfig] = []
        self.audio_in = 0
        self.said: List[str] = []
        self.contexts: List[str] = []
        self.responds: List[Optional[str]] = []
        self.tool_results: List[Dict[str, Any]] = []
        self.truncates: List[tuple] = []
        self.cancels = 0
        self._events: "asyncio.Queue[Any]" = asyncio.Queue()
        self._connected = False
        self._closed = False
        self._resp = 0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, config: SessionConfig) -> None:
        self.hub.connect_attempts += 1
        if self.hub.fail_connects > 0:
            self.hub.fail_connects -= 1
            raise ProviderUnavailable("fake connect failure")
        self.config = config
        self.configs.append(config)
        self._connected = True
        self.hub.connected_now += 1
        self._events.put_nowait(SessionReady())

    async def update(self, config: SessionConfig) -> None:
        self.config = config
        self.configs.append(config)

    async def send_audio(self, payload_b64: str) -> None:
        if self._connected:
            self.audio_in += 1
            self.hub.audio_in += 1

    async def recv(self):
        if self._closed and self._events.empty():
            return None
        ev = await self._events.get()
        if isinstance(ev, ProviderClosed):
            self._closed = True
        return ev

    async def add_context(self, text: str) -> None:
        self.contexts.append(text)
        self.hub.all_text_sent.append(text)

    async def respond(self, instructions: Optional[str] = None) -> None:
        self.responds.append(instructions)

    async def say_exact(self, text: str, *, interruptible: bool = False) -> None:
        self.said.append(text)
        self.hub.all_text_sent.append(text)
        if self.hub.drop_on_say > 0:   # the session dies before the line is rendered
            self.hub.drop_on_say -= 1
            self.drop()
            return
        self._resp += 1
        rid = f"resp_{id(self)}_{self._resp}"
        item = f"item_{id(self)}_{self._resp}"
        self._events.put_nowait(ResponseStarted(rid))
        for chunk in mulaw.frames(synthetic_speech(text), mulaw.FRAME_BYTES * 5):
            self._events.put_nowait(AudioOut(mulaw.b64(chunk), item, rid))
        self._events.put_nowait(ResponseDone(rid, "completed"))

    async def send_tool_result(self, call_id: str, output: str, *, respond: bool = True) -> None:
        self.tool_results.append({"call_id": call_id, "output": json.loads(output), "respond": respond})
        self.hub.all_text_sent.append(output)
        if respond:
            self.responds.append(None)

    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        self.truncates.append((item_id, audio_end_ms))

    async def cancel(self) -> None:
        self.cancels += 1

    async def close(self) -> None:
        if self._connected:
            self.hub.connected_now -= 1
        self._connected = False
        if not self._closed:
            self._events.put_nowait(ProviderClosed(expected=True, reason="closed"))

    # -- test controls ----------------------------------------------------------------
    def emit(self, ev: Any) -> None:
        self._events.put_nowait(ev)

    def model_speaks(self, text: str, item: str = "item_model") -> None:
        """The model answers on its own (conversation mode)."""
        self._resp += 1
        rid = f"resp_m{self._resp}"
        self.emit(ResponseStarted(rid))
        for chunk in mulaw.frames(synthetic_speech(text), mulaw.FRAME_BYTES * 5):
            self.emit(AudioOut(mulaw.b64(chunk), item, rid))
        self.emit(Transcript("agent", text))
        self.emit(ResponseDone(rid, "completed"))

    def tool_call(self, name: str, args: Dict[str, Any], call_id: str = "") -> str:
        call_id = call_id or f"call_{len(self.tool_results)}_{name}"
        self.emit(ToolCall(call_id, name, json.dumps(args)))
        return call_id

    def drop(self) -> None:
        """The provider goes away by itself (network, server restart)."""
        if self._connected:
            self.hub.connected_now -= 1
        self._connected = False
        self.emit(ProviderClosed(expected=False, reason="dropped"))


class FakeProviderHub:
    """Factory + ledger for every fake provider session of one call."""

    def __init__(self) -> None:
        self.instances: List[FakeRealtimeProvider] = []
        self.connect_attempts = 0
        self.connected_now = 0
        self.fail_connects = 0
        self.drop_on_say = 0
        self.audio_in = 0
        self.all_text_sent: List[str] = []

    def __call__(self) -> FakeRealtimeProvider:
        p = FakeRealtimeProvider(self)
        self.instances.append(p)
        return p

    @property
    def last(self) -> FakeRealtimeProvider:
        return self.instances[-1]


class FakeTranscriber(Transcriber):
    name = "fake_stt"

    def __init__(self, hub: "FakeTranscriberHub") -> None:
        self.hub = hub
        self._sink = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, on_utterance) -> None:
        if self.hub.unavailable:
            raise ProviderUnavailable("fake stt unavailable")
        self._sink = on_utterance
        self._running = True
        self.hub.active = self

    async def feed(self, payload_b64: str) -> None:
        if self._running:
            self.hub.frames += 1

    async def stop(self) -> None:
        self._running = False
        if self.hub.active is self:
            self.hub.active = None


class FakeTranscriberHub:
    def __init__(self) -> None:
        self.active: Optional[FakeTranscriber] = None
        self.frames = 0
        self.starts = 0
        self.unavailable = False

    def __call__(self) -> FakeTranscriber:
        self.starts += 1
        return FakeTranscriber(self)

    async def utter(self, text: str) -> bool:
        """The other side said ``text`` (a final utterance). False if nobody is listening."""
        if self.active is None or self.active._sink is None:
            return False
        await self.active._sink(text)
        return True


class FakeMediaStreamsClient:
    """Plays Twilio's side of a bidirectional Media Stream against ``VoiceCall.serve``.

    ``socket`` is what the server reads from and writes to. Outbound ``mark`` messages are
    echoed back when ``ack_marks()`` is called (audio "played"), or at once if
    ``auto_ack``; on ``clear`` every outstanding mark is echoed immediately, like Twilio.
    """

    def __init__(self, *, call_sid: str = "CA" + "1" * 32, stream_sid: str = "MZ" + "2" * 32,
                 auto_ack: bool = False) -> None:
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.auto_ack = auto_ack
        self._in: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self.received: List[Dict[str, Any]] = []
        self.outstanding: List[str] = []
        self.closed_code: Optional[int] = None
        self.socket = _ServerSocket(self)
        self._chunk = 0

    # what Twilio sends
    def send(self, msg: Dict[str, Any]) -> None:
        self._in.put_nowait(json.dumps(msg))

    def connected(self) -> None:
        self.send({"event": "connected", "protocol": "Call", "version": "1.0.0"})

    def start(self, custom: Optional[Dict[str, str]] = None, *, encoding: str = "audio/x-mulaw",
              call_sid: Optional[str] = None) -> None:
        self.connected()
        self.send({"event": "start", "sequenceNumber": "1", "streamSid": self.stream_sid,
                   "start": {"streamSid": self.stream_sid, "accountSid": "AC" + "0" * 32,
                             "callSid": call_sid or self.call_sid, "tracks": ["inbound"],
                             "customParameters": custom or {},
                             "mediaFormat": {"encoding": encoding, "sampleRate": 8000, "channels": 1}}})

    def media(self, audio: bytes, timestamp_ms: int = 0) -> None:
        self._chunk += 1
        self.send({"event": "media", "streamSid": self.stream_sid,
                   "media": {"track": "inbound", "chunk": str(self._chunk), "timestamp": str(timestamp_ms),
                             "payload": mulaw.b64(audio)}})

    def dtmf(self, digit: str) -> None:
        self.send({"event": "dtmf", "streamSid": self.stream_sid, "dtmf": {"track": "inbound_track", "digit": digit}})

    def stop(self) -> None:
        self.send({"event": "stop", "streamSid": self.stream_sid, "stop": {"callSid": self.call_sid}})

    def hang_up(self) -> None:
        self._in.put_nowait(None)

    def ack_marks(self) -> int:
        n = len(self.outstanding)
        for name in self.outstanding:
            self.send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}})
        self.outstanding = []
        return n

    # what the server sent
    def _on_server(self, msg: Dict[str, Any]) -> None:
        self.received.append(msg)
        event = msg.get("event")
        if event == "mark":
            self.outstanding.append(msg["mark"]["name"])
            if self.auto_ack:
                self.ack_marks()
        elif event == "clear":
            self.ack_marks()

    def of(self, event: str) -> List[Dict[str, Any]]:
        return [m for m in self.received if m.get("event") == event]

    def audio_out(self) -> bytes:
        return b"".join(mulaw.unb64(m["media"]["payload"]) for m in self.of("media"))


class _ServerSocket:
    def __init__(self, client: FakeMediaStreamsClient) -> None:
        self._c = client

    async def receive_text(self) -> str:
        item = await self._c._in.get()
        if item is None:
            raise ConnectionError("client went away")
        return item

    async def send_text(self, data: str) -> None:
        if self._c.closed_code is not None:
            raise ConnectionError("closed")
        self._c._on_server(json.loads(data))

    async def close(self, code: int = 1000) -> None:
        if self._c.closed_code is None:
            self._c.closed_code = code
            self._c._in.put_nowait(None)


class FakeWs:
    """A scripted provider-side WebSocket for adapter tests (``WsConnector`` target)."""

    def __init__(self, replies: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None) -> None:
        self.sent: List[Any] = []
        self._in: "asyncio.Queue[Any]" = asyncio.Queue()
        self.replies = replies
        self.closed = False
        self.url = ""
        self.headers: Dict[str, str] = {}

    async def send(self, data) -> None:
        self.sent.append(data)
        if isinstance(data, str) and self.replies is not None:
            for reply in self.replies(json.loads(data)):
                self._in.put_nowait(json.dumps(reply))

    async def recv(self):
        if self.closed and self._in.empty():
            return None
        item = await self._in.get()
        return item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._in.put_nowait(None)

    def push(self, event: Dict[str, Any]) -> None:
        self._in.put_nowait(json.dumps(event))

    def sent_json(self) -> List[Dict[str, Any]]:
        return [json.loads(s) for s in self.sent if isinstance(s, str)]


def fake_connector(ws: FakeWs):
    async def connect(url: str, headers: Dict[str, str]):
        ws.url = url
        ws.headers = dict(headers)
        return ws
    return connect


__all__ = ["FakeMediaStreamsClient", "FakeProviderHub", "FakeRealtimeProvider", "FakeTranscriberHub", "FakeWs",
           "SpeechStarted", "VirtualClock", "fake_connector", "synthetic_speech"]
