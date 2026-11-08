"""Twilio Media Streams, bidirectional (``<Connect><Stream>``), server side.

Protocol facts (twilio.com/docs/voice/media-streams/websocket-messages, accessed 2026-11-05):
- Twilio sends ``connected``, ``start`` (``streamSid``, ``callSid``, ``customParameters``,
  ``mediaFormat`` always ``audio/x-mulaw`` / 8000 / 1), ``media`` (base64 ``payload``,
  ``timestamp`` ms, ``track``), ``dtmf`` (bidirectional only), ``mark`` and ``stop``.
- We send ``media`` (base64 µ-law 8 kHz, no file header), ``mark`` and ``clear``. ``clear``
  "empties all buffered audio and causes any mark messages to be sent back".
- Outbound DTMF over the socket is not supported (see ``mulaw.dtmf_tones``).
- When our server closes the socket, Twilio requests the ``<Connect action>`` URL.

Audio is passed through untouched. The bridge does not know about calls, models or
policy; it hands every Twilio event to a ``StreamHandler`` (the ``VoiceCall``).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Set

from . import mulaw

MAX_FRAME = 64 * 1024
MAX_PAYLOAD = 48 * 1024
OUT_QUEUE = 4096
LOCAL_CHUNK = mulaw.FRAME_BYTES * 10        # local playback in 200 ms messages
_SID = re.compile(r"^[A-Z]{2}[0-9a-f]{32}$")
_DIGIT = re.compile(r"^[0-9*#]$")


@dataclass
class StreamInfo:
    stream_sid: str
    call_sid: str
    custom: Dict[str, str] = field(default_factory=dict)


class StreamSocket(Protocol):
    """The server end of the Twilio WebSocket (Starlette's ``WebSocket`` fits)."""

    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class StreamHandler(Protocol):
    async def on_stream_start(self, bridge: "MediaStreamBridge", info: StreamInfo) -> None: ...
    async def on_media(self, payload: str, timestamp_ms: int) -> None: ...
    async def on_dtmf(self, digit: str) -> None: ...
    async def on_mark(self, name: str, cleared: bool) -> None: ...
    async def on_stream_end(self, bridge: "MediaStreamBridge", reason: str) -> None: ...


class MediaStreamBridge:
    """One Twilio media stream. ``run`` returns when the stream ends."""

    def __init__(self, handler: StreamHandler, *, expected_call_sid: Optional[str] = None) -> None:
        self.handler = handler
        self.expected_call_sid = expected_call_sid
        self.info: Optional[StreamInfo] = None
        self.pending_marks: Set[str] = set()
        self._cleared: Set[str] = set()
        self._out: "asyncio.Queue[Optional[str]]" = asyncio.Queue(maxsize=OUT_QUEUE)
        self._ws: Optional[StreamSocket] = None
        self.closed = False
        self.dropped_out = 0
        self.media_in = 0
        self.media_out_bytes = 0
        self.clears = 0

    # -- inbound ---------------------------------------------------------------------
    async def run(self, ws: StreamSocket) -> str:
        self._ws = ws
        writer = asyncio.get_running_loop().create_task(self._writer(ws))
        reason = "closed"
        try:
            while not self.closed:
                try:
                    text = await ws.receive_text()
                except Exception:
                    reason = "disconnected"
                    break
                if not isinstance(text, str) or len(text) > MAX_FRAME:
                    reason = "bad_frame"
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                verdict = await self._dispatch(msg)
                if verdict:
                    reason = verdict
                    break
        finally:
            self.closed = True
            try:
                self._out.put_nowait(None)
            except asyncio.QueueFull:
                writer.cancel()
            try:
                await asyncio.wait_for(writer, 2)
            except BaseException:
                pass
            if reason in ("bad_format", "wrong_call", "bad_frame"):
                try:
                    await ws.close(1008)
                except Exception:
                    pass
            await self.handler.on_stream_end(self, reason)
        return reason

    async def _dispatch(self, msg: Dict[str, Any]) -> Optional[str]:
        event = msg.get("event")
        if event == "start":
            if self.info is not None:
                return None
            start = msg.get("start") or {}
            fmt = start.get("mediaFormat") or {}
            if (fmt.get("encoding") != "audio/x-mulaw" or int(fmt.get("sampleRate") or 0) != 8000
                    or int(fmt.get("channels") or 0) != 1):
                return "bad_format"
            stream_sid = str(msg.get("streamSid") or start.get("streamSid") or "")
            call_sid = str(start.get("callSid") or "")
            if not _SID.match(stream_sid) or not _SID.match(call_sid):
                return "bad_format"
            if self.expected_call_sid and call_sid != self.expected_call_sid:
                return "wrong_call"
            custom = start.get("customParameters") or {}
            custom = {str(k)[:64]: str(v)[:256] for k, v in custom.items()} if isinstance(custom, dict) else {}
            self.info = StreamInfo(stream_sid, call_sid, custom)
            await self.handler.on_stream_start(self, self.info)
            return None
        if self.info is None:
            return None  # "connected" and anything before "start"
        if event == "media":
            media = msg.get("media") or {}
            payload = media.get("payload")
            if not isinstance(payload, str) or not payload or len(payload) > MAX_PAYLOAD:
                return None
            if media.get("track", "inbound") != "inbound":
                return None
            self.media_in += 1
            try:
                ts = int(media.get("timestamp") or 0)
            except (TypeError, ValueError):
                ts = 0
            await self.handler.on_media(payload, ts)
        elif event == "dtmf":
            digit = str((msg.get("dtmf") or {}).get("digit", ""))
            if _DIGIT.match(digit):
                await self.handler.on_dtmf(digit)
        elif event == "mark":
            name = str((msg.get("mark") or {}).get("name", ""))
            if name in self.pending_marks:
                self.pending_marks.discard(name)
                await self.handler.on_mark(name, False)
            elif name in self._cleared:
                self._cleared.discard(name)
                await self.handler.on_mark(name, True)
        elif event == "stop":
            return "stop"
        return None

    # -- outbound --------------------------------------------------------------------
    def _put(self, message: Dict[str, Any]) -> bool:
        if self.closed or self.info is None:
            return False
        message["streamSid"] = self.info.stream_sid
        try:
            self._out.put_nowait(json.dumps(message, separators=(",", ":")))
            return True
        except asyncio.QueueFull:
            self.dropped_out += 1
            return False

    def send_media(self, payload_b64: str) -> bool:
        ok = self._put({"event": "media", "media": {"payload": payload_b64}})
        if ok:
            self.media_out_bytes += (len(payload_b64) * 3) // 4
        return ok

    def play(self, audio: bytes) -> int:
        """Queue local µ-law audio (a cached phrase, DTMF tones). Returns bytes queued."""
        sent = 0
        for chunk in mulaw.frames(audio, LOCAL_CHUNK):
            if not self.send_media(mulaw.b64(chunk)):
                break
            sent += len(chunk)
        return sent

    def send_mark(self, name: str) -> bool:
        if self._put({"event": "mark", "mark": {"name": name}}):
            self.pending_marks.add(name)
            return True
        return False

    def clear(self) -> Set[str]:
        """Barge-in: drop everything queued here and everything Twilio has buffered.

        Returns the marks that will now come back without having been played."""
        if self.closed or self.info is None:
            return set()
        while True:  # unsent messages never reach Twilio
            try:
                item = self._out.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                self._out.put_nowait(None)
                break
        cleared = set(self.pending_marks)
        self._cleared |= cleared
        self.pending_marks.clear()
        self.clears += 1
        self._put({"event": "clear"})
        return cleared

    @property
    def playing(self) -> bool:
        return bool(self.pending_marks)

    async def close(self, code: int = 1000) -> None:
        """End the stream from our side (Twilio then requests the ``<Connect action>`` URL)."""
        if self.closed:
            return
        self.closed = True
        try:
            self._out.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._ws is not None:
            try:
                await self._ws.close(code)
            except Exception:
                pass

    async def _writer(self, ws: StreamSocket) -> None:
        while True:
            item = await self._out.get()
            if item is None:
                return
            try:
                await ws.send_text(item)
            except Exception:
                return
