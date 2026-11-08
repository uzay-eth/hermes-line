"""The realtime voice provider boundary.

A ``RealtimeVoiceProvider`` is one speech-to-speech session with a voice model. The
call controller (``controller.VoiceCall``) owns it and decides when it exists: the hold
gate keeps it closed during phone menus and hold, so it is opened per human
conversation (or per uncached fixed line) and closed again.

Audio crosses this boundary as base64 G.711 µ-law at 8 kHz in both directions, the
format Twilio Media Streams uses, so nothing is transcoded.

Every event a provider emits is one of the small dataclasses below; provider-specific
wire names stay inside the adapter (``grok.py``).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union


class ProviderUnavailable(Exception):
    """The provider cannot be used (no key, connect failed). Never carries a secret."""


# -- events -------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionReady:
    """The provider accepted the session configuration (``session.updated``)."""


@dataclass(frozen=True)
class AudioOut:
    payload: str                 # base64 µ-law
    item_id: str = ""
    response_id: str = ""


@dataclass(frozen=True)
class ResponseStarted:
    response_id: str = ""


@dataclass(frozen=True)
class ResponseDone:
    response_id: str = ""
    status: str = "completed"


@dataclass(frozen=True)
class SpeechStarted:
    """The provider's VAD heard the other party start speaking (barge-in signal)."""
    item_id: str = ""


@dataclass(frozen=True)
class SpeechStopped:
    item_id: str = ""


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str               # raw JSON text from the model; untrusted


@dataclass(frozen=True)
class Transcript:
    role: str                    # "business" (input) or "agent" (what the voice said)
    text: str
    final: bool = True
    item_id: str = ""            # the conversation item the text belongs to (input transcription)


@dataclass(frozen=True)
class ProviderError:
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class ProviderClosed:
    """The session ended. ``expected`` is True when we closed it ourselves."""
    expected: bool
    reason: str = ""


VoiceEvent = Union[SessionReady, AudioOut, ResponseStarted, ResponseDone, SpeechStarted, SpeechStopped, ToolCall,
                   Transcript, ProviderError, ProviderClosed]


# -- configuration ------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]

    def as_function(self) -> Dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters}


@dataclass(frozen=True)
class SessionConfig:
    """What a session is for.

    ``purpose="speech"``: the model only renders fixed lines (``say_exact``); no tools,
    no turn detection, no input audio is sent.
    ``purpose="conversation"``: server VAD, tools, instructions; the other party's audio
    is streamed in.
    """
    purpose: str = "conversation"
    instructions: str = ""
    voice: str = "eve"
    tools: List[ToolSpec] = field(default_factory=list)
    transcribe_input: bool = True
    idle_timeout_ms: Optional[int] = None
    reasoning_effort: Optional[str] = None      # xAI: "high" (default) | "none"


# -- transport ----------------------------------------------------------------------
class WsConnection(Protocol):
    async def send(self, data: Union[str, bytes]) -> None: ...
    async def recv(self) -> Union[str, bytes, None]: ...   # None once closed
    async def close(self) -> None: ...


WsConnector = Callable[[str, Dict[str, str]], Awaitable[WsConnection]]


class _WebsocketsConnection:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send(self, data):
        await self._ws.send(data)

    async def recv(self):
        try:
            return await self._ws.recv()
        except Exception:
            return None

    async def close(self):
        try:
            await self._ws.close()
        except Exception:
            pass


_SECRETISH = re.compile(r"(?i)(authorization|bearer|api[-_ ]?key|x-api-key)")


class _RedactingFilter(logging.Filter):
    """Last line of defence on the transport logger: any record that mentions an
    Authorization header or a bearer token has its arguments replaced."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            text = str(record.msg)
        if _SECRETISH.search(text) or _SECRETISH.search(str(record.msg)):
            record.msg = "[transport log record redacted]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


def transport_logger() -> logging.Logger:
    """The private logger handed to the ``websockets`` client (review N10).

    ``websockets`` logs every outgoing handshake header, Authorization included, whenever
    its logger is enabled for DEBUG. This logger has its own level (INFO), so root or
    ``websockets`` DEBUG settings never turn that wire logging on, and a redacting filter
    covers anyone who lowers its level by hand."""
    log = logging.getLogger("hermes_line.voice.transport")
    if not any(isinstance(f, _RedactingFilter) for f in log.filters):
        log.addFilter(_RedactingFilter())
    if log.level == logging.NOTSET or log.level < logging.INFO:
        log.setLevel(logging.INFO)
    return log


async def _close_quietly(conn: Any) -> None:
    try:
        await conn.close()
    except BaseException:
        pass


async def connect_owned(connector: "WsConnector", url: str, headers: Dict[str, str], timeout: float) -> WsConnection:
    """Run a handshake and own its socket from the moment it exists (review N9).

    If the caller is cancelled or times out while the handshake is finishing, a socket that
    was created anyway is closed instead of being lost (``asyncio.wait_for`` can drop a result
    that completes during cancellation)."""
    task = asyncio.ensure_future(connector(url, headers))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except BaseException:
        if task.done():
            if not task.cancelled() and task.exception() is None:
                await _close_quietly(task.result())
        else:
            task.cancel()

            def _late(t: "asyncio.Future") -> None:
                if not t.cancelled() and t.exception() is None:
                    asyncio.ensure_future(_close_quietly(t.result()))
            task.add_done_callback(_late)
        raise


async def websockets_connector(url: str, headers: Dict[str, str]) -> WsConnection:
    """Default client transport: the ``websockets`` package Hermes core already ships, with a
    private transport logger so handshake headers never reach any log (review N10)."""
    from websockets.asyncio.client import connect  # imported lazily: tests never need it
    ws = await connect(url, additional_headers=headers, max_size=4 * 1024 * 1024, open_timeout=10,
                       ping_interval=20, ping_timeout=20, close_timeout=3, logger=transport_logger())
    return _WebsocketsConnection(ws)


# -- the interface ------------------------------------------------------------------
class RealtimeVoiceProvider(abc.ABC):
    """One realtime speech session. Not reusable after ``close``."""

    name = "abstract"

    @property
    @abc.abstractmethod
    def connected(self) -> bool: ...

    @abc.abstractmethod
    async def connect(self, config: SessionConfig) -> None:
        """Open the session and apply ``config``; raises ``ProviderUnavailable``."""

    @abc.abstractmethod
    async def update(self, config: SessionConfig) -> None:
        """Re-configure an open session (e.g. speech-only -> conversation)."""

    @abc.abstractmethod
    async def send_audio(self, payload_b64: str) -> None:
        """Append caller audio (base64 µ-law 8 kHz)."""

    @abc.abstractmethod
    async def recv(self) -> Optional[VoiceEvent]:
        """Next event; a ``ProviderClosed`` is delivered once, then ``None``."""

    @abc.abstractmethod
    async def add_context(self, text: str) -> None:
        """Add a text message to the conversation without asking for a reply."""

    @abc.abstractmethod
    async def respond(self, instructions: Optional[str] = None) -> None:
        """Ask the model to speak now."""

    @abc.abstractmethod
    async def say_exact(self, text: str, *, interruptible: bool = False) -> None:
        """Speak ``text`` verbatim, without the model choosing the words."""

    @abc.abstractmethod
    async def send_tool_result(self, call_id: str, output: str, *, respond: bool = True) -> None: ...

    @abc.abstractmethod
    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        """Tell the model how much of an answer the caller actually heard (barge-in)."""

    @abc.abstractmethod
    async def cancel(self) -> None:
        """Stop the current response."""

    @abc.abstractmethod
    async def close(self) -> None: ...
