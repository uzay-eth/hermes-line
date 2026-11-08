"""Stand-in for `line.engine.sealed` until L1 merges (docs/CONTRACT.md, "OTP delivery").

The contract names exactly:

    line.engine.sealed.send_secret_to_call(call_id: str, secret: SecretText, mode: "dtmf"|"speak") -> bool

(R1-delta N1: a trailing keyword `approval_generation`, which L4 always passes and the sink must accept; a sink without it is refused.)

`resolve_send_secret_to_call()` returns L1's function when `line.engine.sealed` is importable and
this stub otherwise. The stub never sends anything: it returns False, so `line_otp_to_call` reports
`sink_failed` ("the call could not take the code") and the sealed slot is kept for a retry.
"""
from __future__ import annotations

from typing import Callable, Optional

from .log import LOG
from .secret import SecretText


def send_secret_to_call(call_id: str, secret: SecretText, mode: str, approval_generation: Optional[int] = None) -> bool:
    LOG.warning("line.engine.sealed.send_secret_to_call is not available yet; nothing was sent")
    return False


def resolve_send_secret_to_call() -> Callable[..., bool]:
    try:
        from ..engine.sealed import send_secret_to_call as real   # type: ignore[import-not-found]

        return real
    except ImportError:
        return send_secret_to_call
