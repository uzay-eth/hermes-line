"""Sealed delivery of a secret (an SMS verification code) into a live call.

``send_secret_to_call(call_id, secret, mode, *, approval_generation)`` is the call sink for L4's
``line_otp_to_call`` tool (docs/CONTRACT.md, "OTP delivery"). The code goes
straight from the ``SecretText`` to the ConversationRelay socket, as DTMF
(``sendDigits``) or as text-to-speech, without passing through the Hermes model.
The transcript, the event log and the Desktop snapshot only ever record
``[verification code sent]``; the Hermes call session is told the same.

It only works in the process that owns the line, for a live call on which
Hermes holds the line, and only when the owner approved a code for that very
call and the party on the line is still the approved counterpart
(``CallService.otp_call_allowed``).

R1 delta N1:

* The send carries the approval generation it was validated under (L4 passes
  it). A send without one is refused (R1 delta 2, N1 residual): adopting the
  generation current at entry would let a code released under an older
  approval go out under its replacement. ``send_secret`` re-checks generation
  and grant on the service loop, right before the frame is queued.
* The sink never runs on, or waits from, the service loop (it would block it).
* A send whose caller timed out is abandoned and cancelled: a ticket decides
  atomically whether the loop committed the frame first (then the caller
  reports success) or the caller gave up first (then the loop never sends).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Optional

_SERVICE: Optional[Any] = None
TIMEOUT_S = 10.0
COMMITTED_GRACE_S = 5.0


def bind(service: Any) -> None:
    global _SERVICE
    _SERVICE = service


def unbind(service: Any) -> None:
    global _SERVICE
    if _SERVICE is service:
        _SERVICE = None


class SendTicket:
    """Exactly one of ``commit`` (the loop, right before queuing the frame) and ``abandon`` (the timed-out
    caller) wins. The lock is held only for the state change, never across a wait."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "pending"

    def _move(self, to: str) -> bool:
        with self._lock:
            if self.state != "pending":
                return False
            self.state = to
            return True

    def commit(self) -> bool:
        return self._move("committed")

    def abandon(self) -> bool:
        return self._move("abandoned")


def _on_loop(loop: Any) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def send_secret_to_call(call_id: str, secret: Any, mode: str, *, approval_generation: Optional[int] = None,
                        timeout: float = TIMEOUT_S) -> bool:
    svc = _SERVICE
    if svc is None or svc.loop is None or mode not in ("dtmf", "speak") or not isinstance(call_id, str):
        return False
    if _on_loop(svc.loop) or getattr(svc.loop, "_thread_id", None) == threading.get_ident():
        return False  # waiting here would block the loop that has to send the frame
    reveal = getattr(secret, "reveal", None)
    if not callable(reveal):
        return False  # only a SecretText is accepted, never a plain string
    session = svc.sessions.get(call_id)
    if session is None or session.task is None or not svc.otp_call_allowed(call_id):
        return False
    if not isinstance(approval_generation, int) or isinstance(approval_generation, bool):
        return False  # mandatory: never adopt the current generation for a code released under another
    ticket = SendTicket()
    try:
        fut = asyncio.run_coroutine_threadsafe(
            session.send_secret(secret, mode, approval_generation=approval_generation, ticket=ticket), svc.loop)
    except Exception:
        return False
    try:
        return bool(fut.result(timeout))
    except concurrent.futures.TimeoutError:
        if ticket.abandon():
            fut.cancel()
            return False  # the loop can no longer send this code
        try:  # the loop committed the frame before we gave up: it is out, or about to be
            return bool(fut.result(COMMITTED_GRACE_S))
        except Exception:
            return True
    except Exception:
        if ticket.abandon():
            return False
        return ticket.state == "committed"
