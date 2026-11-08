"""Voice path settings and the transport choice.

``voice.transport`` (plugin config, read by L1):
* ``auto`` (default): Media Streams + Grok Voice when ``XAI_API_KEY`` is set, otherwise
  ConversationRelay, exactly as before this lane.
* ``media_streams``: require the realtime path; without a key the call is refused before
  dialing (never silently placed without a voice).
* ``conversation_relay``: always ConversationRelay.

The key is only probed for presence through ``get_secret``; its value is never returned,
stored or logged here.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable, Dict, Optional

from .grok import DEFAULT_MODEL, KEY_NAME

TRANSPORTS = ("auto", "media_streams", "conversation_relay")
CONVERSATION = ("grok", "hermes_text")


@dataclass(frozen=True)
class VoiceSettings:
    transport: str = "auto"
    provider: str = "grok"
    model: str = DEFAULT_MODEL
    voice: str = "eve"
    conversation: str = "grok"          # grok: Grok talks, Hermes via tools; hermes_text: Grok reads Hermes text
    transcriber: str = "xai_stt"
    stt_endpointing_ms: int = 700
    language: str = "en"
    ask_timeout_s: float = 10.0         # hard cap for one ask_hermes turn
    filler_after_s: float = 3.0         # say "still checking" if Hermes is slower than this
    max_tool_calls_per_minute: int = 20
    max_reconnects: int = 2
    reconnect_backoff_s: float = 0.5
    speech_timeout_s: float = 15.0      # a fixed line must be rendered within this
    idle_hold_s: float = 60.0           # no one spoke for this long in a conversation -> treat as hold
    prewarm_s: float = 0.0              # optional pre-warm after the live-person question (0 = off)
    end_drain_s: float = 10.0           # wait this long for the goodbye to play before hanging up
    transcript_settle_s: float = 1.2    # a caller line is recorded at the next response, or after this quiet
    dtmf_react_window_s: float = 8.0    # after menu tones end, the menu's next utterance within this decides
    reasoning_effort: Optional[str] = "none"

    @classmethod
    def load(cls, data: Optional[Dict[str, Any]]) -> "VoiceSettings":
        data = dict(data or {})
        known = {f.name: f for f in fields(cls)}
        unknown = set(data) - set(known)
        if unknown:
            raise ValueError(f"unknown voice settings: {sorted(unknown)}")
        out = cls(**data)
        if out.transport not in TRANSPORTS:
            raise ValueError("voice.transport must be auto, media_streams or conversation_relay")
        if out.provider != "grok":
            raise ValueError("voice.provider must be grok")
        if out.conversation not in CONVERSATION:
            raise ValueError("voice.conversation must be grok or hermes_text")
        if out.transcriber != "xai_stt":
            raise ValueError("voice.transcriber must be xai_stt")
        for name in ("ask_timeout_s", "filler_after_s", "speech_timeout_s", "idle_hold_s", "end_drain_s",
                     "dtmf_react_window_s", "transcript_settle_s"):
            if not 0 < float(getattr(out, name)) <= 600:
                raise ValueError(f"voice.{name} must be in (0, 600]")
        if not 0 <= out.max_reconnects <= 5 or not 1 <= out.max_tool_calls_per_minute <= 120:
            raise ValueError("voice.max_reconnects 0-5, voice.max_tool_calls_per_minute 1-120")
        if out.reasoning_effort not in (None, "none", "high"):
            raise ValueError("voice.reasoning_effort must be none or high")
        return out


@dataclass(frozen=True)
class TransportChoice:
    transport: str      # "media_streams" | "conversation_relay" | "refused"
    reason: str


def select_transport(settings: VoiceSettings, get_secret: Callable[[str], Optional[str]]) -> TransportChoice:
    if settings.transport == "conversation_relay":
        return TransportChoice("conversation_relay", "configured")
    try:
        has_key = bool((get_secret(KEY_NAME) or "").strip())
    except Exception:
        has_key = False
    if has_key:
        return TransportChoice("media_streams", f"{KEY_NAME} is set")
    if settings.transport == "media_streams":
        return TransportChoice("refused", f"voice.transport is media_streams but {KEY_NAME} is not set")
    return TransportChoice("conversation_relay", f"{KEY_NAME} is not set")
