"""The engine's view of the L2 (policy, detect) and L4 (sms) interfaces.

L2 is merged and imported directly. ``line.sms`` (L4) is resolved at import
time: only a *missing* module falls back to the disabled stub, an error raised
inside a real module propagates, so a broken SMS package never silently
degrades to the stub.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

from ..detect import Detection, HoldTracker, Utterance, classify
from ..policy import ConsentStore, Settings, check_destination, is_quiet
from .stubs import sms as _stub_sms

_ROOT = __name__.rsplit(".line.engine", 1)[0]


def _optional(relative: str):
    name = f"{_ROOT}.line.{relative}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (name == missing or name.startswith(missing + ".")):
            return None
        raise


def _load_sms() -> SimpleNamespace:
    mod = _optional("sms")
    if mod is not None and hasattr(mod, "release_otp"):
        return SimpleNamespace(release_otp=mod.release_otp, SecretText=getattr(mod, "SecretText", None),
                               is_stub=False)
    return SimpleNamespace(release_otp=_stub_sms.release_otp, SecretText=_stub_sms.SecretText, is_stub=True)


sms = _load_sms()
CALL_TRANSCRIPT_MARKER = "[verification code sent]"
if not sms.is_stub:
    CALL_TRANSCRIPT_MARKER = getattr(importlib.import_module(f"{_ROOT}.line.sms"), "CALL_TRANSCRIPT_MARKER",
                                     CALL_TRANSCRIPT_MARKER)
    from .redact import register_scrubber
    # Exact-value scrub of released codes. L4's shape masker (mask_codes) is not used for call
    # transcripts: it hides dates, confirmation and phone numbers the owner needs in a summary.
    _mask = getattr(importlib.import_module(f"{_ROOT}.line.sms"), "scrub_text", None)
    if callable(_mask):
        register_scrubber(_mask)

__all__ = ["ConsentStore", "Detection", "HoldTracker", "Settings", "Utterance", "check_destination",
           "classify", "is_quiet", "sms", "status"]


def status() -> dict:
    return {"policy": "installed", "detect": "installed", "sms": "stub" if sms.is_stub else "installed"}
