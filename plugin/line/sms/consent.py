"""Consent interface for OTP release.

The store is `line.policy.consent.ConsentStore` (lane L2, docs/CONTRACT.md):

    issue(action, task_id, binding: dict, ttl_s) -> token
    consume(token, action, task_id, binding) -> bool      # single use; expired, reused or mismatched = False

`line.sms` imports nothing from `line.policy`: the engine passes the real store in, and this module only
declares the shape L4 relies on. Note that L2's store spends a token on ANY consume call that finds it
(a mismatch burns it too), which is why `service.OtpService` calls `consume` only at the moment a code
is actually about to be released.

The action name and the binding layout (`service.binding_for`) are part of the contract with the approval flow.
"""
from __future__ import annotations

from typing import Any, Mapping

try:  # typing.Protocol is 3.8+; the plugin targets 3.11+, tests also run on 3.9
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

OTP_ACTION = "otp_release"


class ConsentStoreLike(Protocol):
    def issue(self, action: str, task_id: str, binding: Mapping[str, Any], ttl_s: float) -> str: ...

    def consume(self, token: str, action: str, task_id: str, binding: Mapping[str, Any]) -> bool: ...
