"""Shared helpers for the L5 voice tests (offline; no sockets, no keys, no network)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugin.line.voice import VoiceCall, VoiceSettings                              # noqa: E402
from plugin.line.voice import mulaw                                                 # noqa: E402
from plugin.line.voice.testing import (FakeMediaStreamsClient, FakeProviderHub,     # noqa: E402
                                       FakeTranscriberHub, VirtualClock)

HOLD_MESSAGES = (
    "Thank you for calling. All of our agents are currently assisting other customers. Please stay on the line.",
    "Your call is important to us. The current wait time is more than thirty minutes.",
)
HUMAN_GREETING = "Hi, thanks for holding, this is Dana. How can I help you today?"


class FakePort:
    """Stands in for L1's call session. Every method that would act on the call is recorded
    in ``side_effects``; the rule switches decide whether L1 would have allowed it."""

    def __init__(self) -> None:
        self.call_id = "call_test"
        self.offers: List[Dict[str, Any]] = []
        self.records: List[tuple] = []
        self.events: List[tuple] = []
        self.side_effects: List[Any] = []
        self.asks: List[str] = []
        self.ask_answer: Optional[str] = "Yes, the owner is free on Friday at 7 pm."
        self.ask_delay = 0.0
        self.owner_allowed = True
        self.code_approved = False
        self.failed: List[str] = []
        self.voice: Optional[VoiceCall] = None
        self.outcome: Optional[tuple] = None

    def offer(self, frame: Dict[str, Any]) -> bool:
        self.offers.append(dict(frame))
        return True

    def record(self, speaker: str, text: str) -> None:
        self.records.append((speaker, text))

    def voice_event(self, kind: str, **data: Any) -> None:
        self.events.append((kind, data))

    def prompts(self) -> List[str]:
        return [f["voicePrompt"] for f in self.offers if f.get("type") == "prompt"]

    def event_kinds(self) -> List[str]:
        return [k for k, _ in self.events]

    async def ask_hermes(self, question: str, timeout_s: float) -> Optional[str]:
        self.asks.append(question)
        await asyncio.sleep(self.ask_delay)
        return self.ask_answer

    async def bring_owner(self) -> Dict[str, Any]:
        if not self.owner_allowed:
            return {"ok": False, "error": "the owner has not allowed a bridge on this call"}
        self.side_effects.append("ring_owner")
        return {"ok": True}

    async def press_digits(self, digits: str) -> Dict[str, Any]:
        self.side_effects.append(("press", digits))
        if self.voice is not None:
            return await self.voice.press_menu(digits)
        return {"ok": True}

    async def report_outcome(self, outcome: str, summary: str) -> Dict[str, Any]:
        self.outcome = (outcome, summary)
        return {"ok": True}

    async def end_call(self) -> Dict[str, Any]:
        self.side_effects.append("end_call")
        if self.voice is not None:
            self.voice.on_state("ended")
        return {"ok": True}

    async def send_verification_code(self, mode: str) -> Dict[str, Any]:
        if not self.code_approved:
            return {"ok": False, "error": "no code was approved for this call"}
        self.side_effects.append(("code", mode))
        return {"ok": True}

    async def hold_resumed(self) -> Dict[str, Any]:
        self.side_effects.append("hold_resumed")
        if self.voice is not None:
            self.voice.on_state("on_hold")
        return {"ok": True}

    async def voice_failed(self, reason: str) -> None:
        self.failed.append(reason)
        if self.voice is not None:
            self.voice.on_state("failed")


class Rig:
    """One VoiceCall wired to fakes: provider hub, transcriber hub, Media Streams client, port."""

    def __init__(self, *, settings: Optional[Dict[str, Any]] = None, welcome: Optional[str] = None,
                 auto_ack: bool = False, phrases=None) -> None:
        self.clock = VirtualClock()
        self.hub = FakeProviderHub()
        self.stt = FakeTranscriberHub()
        self.port = FakePort()
        base = {"idle_hold_s": 600.0, "filler_after_s": 5.0, "reconnect_backoff_s": 0.01}
        base.update(settings or {})
        kwargs = {} if phrases is None else {"phrases": phrases}
        self.voice = VoiceCall(call_id="call_test", port=self.port, provider_factory=self.hub,
                               transcriber_factory=self.stt, settings=VoiceSettings.load(base), clock=self.clock,
                               welcome=welcome, instructions="Task: book a table for four at 7 pm Friday.",
                               **kwargs)
        self.port.voice = self.voice
        self.client = FakeMediaStreamsClient(auto_ack=auto_ack)
        self.serve_task: Optional[asyncio.Task] = None

    async def start(self, state: str = "dialing") -> None:
        self.voice.on_state(state)
        self.serve_task = asyncio.get_running_loop().create_task(self.voice.serve(self.client.socket))
        self.client.start({"call": "call_test"})
        await self.flush()

    async def flush(self, rounds: int = 6) -> None:
        for _ in range(rounds):
            await asyncio.sleep(0.002)
            await self.voice.settle()

    async def state(self, state: str) -> None:
        self.voice.on_state(state)
        await self.flush()

    async def hold_for(self, seconds: float, *, every_s: float = 60.0, frame_ms: int = 20, batch: int = 500,
                       audio: Optional[bytes] = None) -> None:
        """Feed ``seconds`` of hold audio in real-size frames, a hold message every ``every_s``."""
        frame = audio or mulaw.tone([440, 554], frame_ms, amplitude=0.1)
        frames_total = int(seconds * 1000 / frame_ms)
        next_message = every_s
        elapsed = 0.0
        i = 0
        while i < frames_total:
            n = min(batch, frames_total - i)
            for k in range(n):
                self.client.media(frame, int((elapsed + k * frame_ms / 1000) * 1000))
            i += n
            elapsed += n * frame_ms / 1000
            self.clock.advance(n * frame_ms / 1000)
            await self.flush(2)
            while elapsed >= next_message:
                await self.stt.utter(HOLD_MESSAGES[int(next_message // every_s) % 2])
                next_message += every_s
                await self.flush(2)

    async def close(self) -> None:
        await self.voice.close()
        self.client.hang_up()
        if self.serve_task is not None:
            try:
                await asyncio.wait_for(self.serve_task, 2)
            except Exception:
                pass
        await self.flush()


def run(coro):
    return asyncio.run(coro)
