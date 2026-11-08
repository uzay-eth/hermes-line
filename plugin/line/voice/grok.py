"""xAI Grok Voice (Speech to Speech) adapter.

Wire facts, all from docs.x.ai, accessed 2026-11-05 (see docs/adr/0004-realtime-voice.md):
- endpoint ``wss://api.x.ai/v1/realtime?model=grok-voice-latest``, ``Authorization: Bearer <key>``,
  server-side only;
- OpenAI-Realtime-compatible events; audio deltas arrive as ``response.output_audio.delta``
  (``response.audio.delta`` is also accepted);
- ``session.update`` with ``audio.input.format.type`` / ``audio.output.format.type`` =
  ``"audio/pcmu"`` (G.711 µ-law, 8 kHz), ``turn_detection.type`` ``"server_vad"`` or ``null``;
- function tools ``{type:"function", name, description, parameters}``; the call arrives as
  ``response.function_call_arguments.done``; the result goes back as ``conversation.item.create``
  ``function_call_output`` followed by ``response.create`` (only after all outputs are in);
- ``force_message`` (an xAI extension) speaks a fixed line through TTS without the model, and
  is its own turn (no ``response.create``).
UNCONFIRMED: the exact ``conversation.item.truncate`` fields (documented by name only; the
OpenAI field names are used), session length limits, and whether billing counts connected
or audio minutes.

The key is read through the injected ``get_secret`` (``agent.secret_scope.get_secret``) at
connect time, used for the one handshake header and not kept on the instance.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Set

from .provider import (AudioOut, ProviderClosed, ProviderError, ProviderUnavailable, RealtimeVoiceProvider,
                       ResponseDone, ResponseStarted, SessionConfig, SessionReady, SpeechStarted, SpeechStopped,
                       ToolCall, Transcript, VoiceEvent, WsConnection, WsConnector, connect_owned,
                       websockets_connector)

XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime"
DEFAULT_MODEL = "grok-voice-latest"
KEY_NAME = "XAI_API_KEY"
PCMU = {"type": "audio/pcmu"}


def session_payload(config: SessionConfig) -> Dict[str, Any]:
    """The ``session`` object of a ``session.update`` for ``config``."""
    conversation = config.purpose == "conversation"
    session: Dict[str, Any] = {
        "voice": config.voice,
        "instructions": config.instructions,
        "audio": {"input": {"format": dict(PCMU)}, "output": {"format": dict(PCMU)}},
        "turn_detection": {"type": "server_vad"} if conversation else None,
        "tools": [t.as_function() for t in config.tools] if conversation else [],
    }
    if conversation and config.idle_timeout_ms:
        session["turn_detection"]["idle_timeout_ms"] = int(config.idle_timeout_ms)
    if conversation and config.transcribe_input:
        session["audio"]["input"]["transcription"] = {"model": "grok-transcribe"}
    if config.reasoning_effort:
        session["reasoning"] = {"effort": config.reasoning_effort}
    return session


class GrokRealtimeProvider(RealtimeVoiceProvider):
    name = "grok"

    def __init__(self, get_secret: Callable[[str], Optional[str]], *, model: str = DEFAULT_MODEL,
                 connector: Optional[WsConnector] = None, url: str = XAI_REALTIME_URL,
                 ready_timeout_s: float = 8.0) -> None:
        self._get_secret = get_secret
        self._model = model
        self._connector = connector or websockets_connector
        self._url = url
        self._ready_timeout = ready_timeout_s
        self._ws: Optional[WsConnection] = None
        self._connected = False
        self._closing = False
        self._closed_sent = False
        self._pending: Deque[VoiceEvent] = deque()
        self._seen_tool_calls: Set[str] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    # -- lifecycle -------------------------------------------------------------------
    async def connect(self, config: SessionConfig) -> None:
        if self._ws is not None:
            raise ProviderUnavailable("session already used")
        key = self._get_secret(KEY_NAME) or ""
        if not key.strip():
            raise ProviderUnavailable(f"{KEY_NAME} is not set")
        url = f"{self._url}?{urllib.parse.urlencode({'model': self._model})}"
        try:
            self._ws = await connect_owned(self._connector, url, {"Authorization": f"Bearer {key.strip()}"},
                                           self._ready_timeout)
        except Exception as exc:
            # Only the exception type: a handshake error can quote the request (review N10).
            raise ProviderUnavailable(f"connect failed: {type(exc).__name__}") from None
        finally:
            key = ""  # noqa: F841  (drop the local reference early)
        # From here the socket is ours: any unsuccessful exit, cancellation included, closes it (review N9).
        try:
            await self._send({"type": "session.update", "session": session_payload(config)})
            await asyncio.wait_for(self._await_ready(), self._ready_timeout)
        except Exception:
            await self.close()
            raise ProviderUnavailable("session was not accepted") from None
        except BaseException:   # cancellation: close, then let it propagate
            await self.close()
            raise
        self._connected = True

    async def _await_ready(self) -> None:
        while True:
            raw = await self._ws.recv()
            if raw is None:
                raise ProviderUnavailable("closed during setup")
            for event in self._map(raw):
                if isinstance(event, SessionReady):
                    return
                if isinstance(event, ProviderError) and event.code in ("invalid_api_key", "unauthorized"):
                    raise ProviderUnavailable("rejected")
                self._pending.append(event)

    async def update(self, config: SessionConfig) -> None:
        await self._send({"type": "session.update", "session": session_payload(config)})

    async def close(self) -> None:
        self._closing = True
        self._connected = False
        if self._ws is not None:
            await self._ws.close()  # recv() then reports ProviderClosed(expected=True)

    # -- client events -----------------------------------------------------------------
    async def _send(self, event: Dict[str, Any]) -> None:
        if self._ws is None or self._closing:
            return
        await self._ws.send(json.dumps(event, separators=(",", ":")))

    async def send_audio(self, payload_b64: str) -> None:
        await self._send({"type": "input_audio_buffer.append", "audio": payload_b64})

    async def add_context(self, text: str) -> None:
        await self._send({"type": "conversation.item.create",
                          "item": {"type": "message", "role": "user",
                                   "content": [{"type": "input_text", "text": text}]}})

    async def respond(self, instructions: Optional[str] = None) -> None:
        event: Dict[str, Any] = {"type": "response.create"}
        if instructions:
            event["response"] = {"instructions": instructions}
        await self._send(event)

    async def say_exact(self, text: str, *, interruptible: bool = False) -> None:
        await self._send({"type": "conversation.item.create",
                          "item": {"type": "force_message", "role": "assistant", "interruptible": interruptible,
                                   "content": [{"type": "output_text", "text": text}]}})

    async def send_tool_result(self, call_id: str, output: str, *, respond: bool = True) -> None:
        await self._send({"type": "conversation.item.create",
                          "item": {"type": "function_call_output", "call_id": call_id, "output": output}})
        if respond:
            await self.respond()

    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        if item_id:
            await self._send({"type": "conversation.item.truncate", "item_id": item_id, "content_index": 0,
                              "audio_end_ms": max(0, int(audio_end_ms))})

    async def cancel(self) -> None:
        await self._send({"type": "response.cancel"})

    # -- server events -------------------------------------------------------------------
    async def recv(self) -> Optional[VoiceEvent]:
        while True:
            if self._pending:
                return self._pending.popleft()
            if self._closed_sent:
                return None
            raw = await self._ws.recv() if self._ws is not None else None
            if raw is None:
                self._connected = False
                self._closed_sent = True
                return ProviderClosed(expected=self._closing, reason="closed" if self._closing else "dropped")
            self._pending.extend(self._map(raw))

    def _map(self, raw: Any) -> list:
        if isinstance(raw, bytes):
            return []  # binary transport is not requested
        try:
            ev = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(ev, dict):
            return []
        kind = ev.get("type")
        if kind in ("response.output_audio.delta", "response.audio.delta"):
            delta = ev.get("delta")
            return [AudioOut(delta, str(ev.get("item_id", "")), str(ev.get("response_id", "")))] \
                if isinstance(delta, str) and delta else []
        if kind == "session.updated":
            return [SessionReady()]
        if kind == "input_audio_buffer.speech_started":
            return [SpeechStarted(str(ev.get("item_id", "")))]
        if kind == "input_audio_buffer.speech_stopped":
            return [SpeechStopped(str(ev.get("item_id", "")))]
        if kind == "response.created":
            return [ResponseStarted(str((ev.get("response") or {}).get("id", "")))]
        if kind == "response.done":
            resp = ev.get("response") or {}
            return [ResponseDone(str(resp.get("id", "")), str(resp.get("status", "completed")))]
        if kind == "response.function_call_arguments.done":
            return self._tool(ev.get("call_id"), ev.get("name"), ev.get("arguments"))
        if kind == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":  # some SDK paths only emit this one
                return self._tool(item.get("call_id"), item.get("name"), item.get("arguments"))
            return []
        if kind in ("conversation.item.input_audio_transcription.completed",
                    "conversation.item.input_audio_transcription.updated"):
            # xAI: ``updated`` carries the cumulative text so far (a partial); ``completed`` can also arrive
            # more than once per item with growing text (first live call, 2026-11-05). The controller keeps
            # one line per utterance; partials never become transcript lines.
            text = ev.get("transcript")
            final = kind.endswith(".completed")
            return [Transcript("business", text, final, str(ev.get("item_id", "")))] \
                if isinstance(text, str) and text.strip() else []
        if kind in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            text = ev.get("transcript")
            return [Transcript("agent", text, True)] if isinstance(text, str) and text.strip() else []
        if kind == "error":
            err = ev.get("error") or {}
            return [ProviderError(str(err.get("code") or err.get("type") or ""), str(err.get("message", ""))[:200])]
        return []

    def _tool(self, call_id: Any, name: Any, arguments: Any) -> list:
        if not isinstance(call_id, str) or not call_id or call_id in self._seen_tool_calls:
            return []
        self._seen_tool_calls.add(call_id)
        return [ToolCall(call_id, str(name or ""), arguments if isinstance(arguments, str) else "{}")]
