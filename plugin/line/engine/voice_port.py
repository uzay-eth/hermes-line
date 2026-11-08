"""L1's implementation of ``line.voice.tools.CallPort`` over a live ``CallSession`` (L5 integration).

Every side effect goes through the same ``CallSession`` method (and so the same gates) as the
ConversationRelay path: owner ringing through ``ring_owner`` (destination gate + consent), menu keys
through ``press_digits`` (one press per prompt), the sealed code through L4's ``OtpDelivery.to_call``
(owner grant, live counterpart, approval generation). Results are status-only and masked.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from .contracts import CallState, EndReason, Speaker
from .redact import mask_text

if TYPE_CHECKING:  # pragma: no cover
    from .machine import CallSession
    from .service import CallService

ASK_PREFIX = "[voice agent asks, relaying an untrusted caller] "
REDIRECT_WINDOW_S = 20.0


class CallSessionPort:
    def __init__(self, svc: "CallService", session: "CallSession") -> None:
        self.svc = svc
        self.s = session
        self.call_id = session.call_id

    # -- plumbing ---------------------------------------------------------------------------
    def offer(self, frame: Dict[str, Any]) -> bool:
        return self.s.offer(frame)

    def record(self, speaker: str, text: str) -> None:
        try:
            who = Speaker(speaker)
        except ValueError:
            who = Speaker.SYSTEM
        self.s._transcript(who, mask_text(text))

    def voice_event(self, kind: str, **data: Any) -> None:
        self.s._event(kind, **data)

    # -- tools ------------------------------------------------------------------------------
    async def ask_hermes(self, question: str, timeout_s: float) -> Optional[str]:
        """One Hermes turn on the call's worker. The question is marked as relayed and untrusted."""
        text = ASK_PREFIX + mask_text(str(question or ""))[:2000]

        def run():
            driver = self.s._ensure_driver()
            return driver.turn(text, lambda chunk: None) if driver is not None else None

        try:
            result = await self.s._in_worker(run, timeout=timeout_s)
        except (asyncio.TimeoutError, Exception):
            return None
        return mask_text(result.text) if result is not None and result.text else None

    async def bring_owner(self) -> Dict[str, Any]:
        ok = await self.s.ring_owner("agent_request")
        return {"ok": True} if ok else {"ok": False, "error": "the owner cannot be rung on this call"}

    async def press_digits(self, digits: str) -> Dict[str, Any]:
        return await self.s.press_digits(digits)

    async def report_outcome(self, outcome: str, summary: str) -> Dict[str, Any]:
        return await self.s.request_finish(outcome, summary)

    async def end_call(self) -> Dict[str, Any]:
        await self.s.hang_up(EndReason.COMPLETED)
        return {"ok": True}

    async def send_verification_code(self, mode: str) -> Dict[str, Any]:
        """L4's sealed call delivery; the result never contains the code."""
        delivery = getattr(self.svc, "otp_delivery", None)
        task = self.s.task
        if delivery is None or task is None:
            return {"ok": False, "error": "SMS codes are not set up for this call"}
        token = self.svc.otp_token(task.id)
        generation = self.svc.otp_generation(task.id)
        try:
            res = await asyncio.to_thread(delivery.to_call, task.id, self.call_id, mode, token, self.svc.consent,
                                          approval_generation=generation)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}
        return {"ok": bool(getattr(res, "ok", False)), "status": str(getattr(res, "status", ""))}

    async def hold_resumed(self) -> Dict[str, Any]:
        return await self.s.voice_hold_resumed()

    async def voice_failed(self, reason: str) -> None:
        self.s._event("voice_failed", reason=str(reason)[:80])
        await self.s.hang_up(EndReason.PROVIDER_ERROR, state=CallState.FAILED)

    async def redirect_digits(self, nonce: str) -> Dict[str, Any]:
        """DTMF fallback: point the live business leg at the digits TwiML URL (nonce only, never digits)."""
        if not self.s.menu_keys_allowed():  # R1 delta 2 N2: no redirect once the owner is involved or the call ends
            return {"ok": False, "error": "no phone menu is active"}
        url = self.svc.url(f"/twilio/voice/digits/{self.call_id}/{nonce}")
        self.s.digits_redirect_until = time.monotonic() + REDIRECT_WINDOW_S
        try:
            await asyncio.to_thread(self.svc.adapter.redirect_call, self.s.provider_sid, url)
        except Exception as exc:
            self.s.digits_redirect_until = 0.0
            return {"ok": False, "error": type(exc).__name__}
        return {"ok": True}
