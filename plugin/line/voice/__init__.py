"""Realtime voice transport: Twilio Media Streams + a realtime voice model (Grok Voice).

Design: docs/adr/0004-realtime-voice.md. L1 wiring: docs/lanes/L5.md.
"""
from __future__ import annotations

from .controller import EngineLink, VoiceCall
from .gate import ConnectionMeter, SessionGate
from .grok import GrokRealtimeProvider
from .mediastreams import MediaStreamBridge, StreamInfo
from .phrases import PhraseCache
from .provider import ProviderUnavailable, RealtimeVoiceProvider, SessionConfig, ToolSpec
from .settings import TransportChoice, VoiceSettings, select_transport
from .stt import Transcriber, XaiStreamingTranscriber
from .tools import TOOL_SPECS, CallPort, ToolRouter

KEY_NAME = "XAI_API_KEY"

__all__ = ["CallPort", "ConnectionMeter", "EngineLink", "GrokRealtimeProvider", "KEY_NAME", "MediaStreamBridge",
           "PhraseCache", "ProviderUnavailable", "RealtimeVoiceProvider", "SessionConfig", "SessionGate",
           "StreamInfo", "TOOL_SPECS", "ToolRouter", "ToolSpec", "Transcriber", "TransportChoice", "VoiceCall",
           "VoiceSettings", "XaiStreamingTranscriber", "select_transport"]
