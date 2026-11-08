"""Disabled stand-in for ``line.sms`` (lane L4) until it merges.

ADR 0002 item 8: OTP release stays disabled until the persistence audit is done,
so ``release_otp`` always returns ``None`` here.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

IS_STUB = True

sources: dict = {}


class SecretText:
    """Same safety surface as the S3 spike: never formats its value."""

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SecretText wraps str only")
        object.__setattr__(self, "_v", value)

    def __setattr__(self, name, value):
        raise AttributeError("SecretText is immutable")

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "SecretText(<redacted>)"

    __str__ = lambda self: "<redacted>"  # noqa: E731
    __format__ = lambda self, spec: "<redacted>"  # noqa: E731

    def __eq__(self, other) -> bool:
        if isinstance(other, SecretText):
            return hmac.compare_digest(self._v.encode(), other._v.encode())
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("SecretText", self._v))

    def __reduce__(self):
        raise TypeError("SecretText cannot be pickled")


class OtpMatcher:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("line.sms is not installed (lane L4)")


def release_otp(task_id: str, consent_token: str, consent_store: Any) -> Optional[SecretText]:
    return None
