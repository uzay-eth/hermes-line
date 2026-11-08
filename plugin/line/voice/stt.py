"""The cheap ears: transcripts of the other side while no voice session is open.

During phone menus and hold the plugin needs text, not a voice, for L2's detector
(``line.detect.HoldTracker``). A ``Transcriber`` turns the call's inbound µ-law into
final utterances. The default is xAI streaming Speech to Text, the same ``XAI_API_KEY``:

    wss://api.x.ai/v1/stt?encoding=mulaw&sample_rate=8000&endpointing=...
    raw binary audio frames in; ``transcript.partial`` messages out, an utterance is final
    when ``is_final`` and ``speech_final`` are both true; ``{"type": "audio.done"}`` ends it.

Price listed on docs.x.ai/developers/pricing (accessed 2026-11-05): Speech to Text
$0.20/h streaming, $0.10/h REST, against $4.80/h for the voice model.

Twilio's own real-time transcription (``<Start><Transcription>``) would also work and is
billed by Twilio, but its documentation says use of it is subject to Twilio's Predictive and
Generative AI/ML Features Addendum, which the owner has not accepted; it is not used.
"""
from __future__ import annotations

import abc
import asyncio
import json
import urllib.parse
from typing import Awaitable, Callable, Dict, Optional

from .mulaw import unb64
from .provider import ProviderUnavailable, WsConnection, WsConnector, connect_owned, websockets_connector

XAI_STT_URL = "wss://api.x.ai/v1/stt"
KEY_NAME = "XAI_API_KEY"

UtteranceSink = Callable[[str], Awaitable[None]]


class Transcriber(abc.ABC):
    """Streaming transcription of one call's inbound audio."""

    name = "abstract"

    @abc.abstractmethod
    async def start(self, on_utterance: UtteranceSink) -> None:
        """Open the stream; ``on_utterance(text)`` is awaited for each final utterance."""

    @abc.abstractmethod
    async def feed(self, payload_b64: str) -> None:
        """Inbound audio, base64 µ-law 8 kHz, exactly as Twilio sent it."""

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @property
    @abc.abstractmethod
    def running(self) -> bool: ...


class XaiStreamingTranscriber(Transcriber):
    name = "xai_stt"

    def __init__(self, get_secret: Callable[[str], Optional[str]], *, connector: Optional[WsConnector] = None,
                 url: str = XAI_STT_URL, endpointing_ms: int = 700, language: str = "en",
                 connect_timeout_s: float = 8.0) -> None:
        self._get_secret = get_secret
        self._connector = connector or websockets_connector
        self._url = url
        self._params: Dict[str, str] = {"encoding": "mulaw", "sample_rate": "8000",
                                        "endpointing": str(max(0, min(int(endpointing_ms), 5000))),
                                        "interim_results": "false"}
        if language:
            self._params["language"] = language
        self._timeout = connect_timeout_s
        self._ws: Optional[WsConnection] = None
        self._reader: Optional[asyncio.Task] = None
        self._sink: Optional[UtteranceSink] = None
        self.errors = 0

    @property
    def running(self) -> bool:
        return self._ws is not None

    async def start(self, on_utterance: UtteranceSink) -> None:
        if self._ws is not None:
            return
        key = (self._get_secret(KEY_NAME) or "").strip()
        if not key:
            raise ProviderUnavailable(f"{KEY_NAME} is not set")
        url = f"{self._url}?{urllib.parse.urlencode(self._params)}"
        try:
            ws = await connect_owned(self._connector, url, {"Authorization": f"Bearer {key}"}, self._timeout)
        except Exception as exc:
            raise ProviderUnavailable(f"stt connect failed: {type(exc).__name__}") from None   # type only (N10)
        finally:
            key = ""  # noqa: F841
        # No await between the handshake and adoption: the socket is tracked from here on (N9).
        self._ws = ws
        self._sink = on_utterance
        self._reader = asyncio.get_running_loop().create_task(self._read(ws))

    async def feed(self, payload_b64: str) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(unb64(payload_b64))
        except Exception:
            self.errors += 1

    async def stop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "audio.done"}))
            except Exception:
                pass
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None

    async def _read(self, ws: WsConnection) -> None:
        while True:
            raw = await ws.recv()
            if raw is None:
                if self._ws is ws:
                    self._ws = None
                return
            text = parse_final(raw)
            if text and self._sink is not None:
                try:
                    await self._sink(text)
                except Exception:
                    self.errors += 1


def parse_final(raw) -> Optional[str]:
    """Text of a final utterance from one xAI STT message, else None."""
    if isinstance(raw, bytes):
        return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(msg, dict):
        return None
    if msg.get("type") == "transcript.partial" and msg.get("is_final") is True and msg.get("speech_final") is True:
        text = msg.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None
    # ``transcript.done`` (after ``audio.done``) repeats the whole stream; it is ignored so no
    # utterance reaches the detector twice.
    return None
