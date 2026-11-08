"""Carrier call screening on forwarded inbound calls (live calls 4 and 5, round 16 addition a).

A call forwarded by the owner's carrier line (Google Fi) can open with the carrier's own prompt before the caller is
connected: "In case of a fast pickup, we ask you to let us know that you are not a voice mail system. So you need to
press 1 to take this call." The line answers it itself, at once, and only then greets the caller, who was not
connected while the prompt played.

Narrow on purpose: inbound calls only, the first ``SCREENING_WINDOW_S`` seconds of the call, and only this prompt
family (a "press 1 to take this call" instruction, or the "fast pickup ... not a voice mail" lead-in).
"""
from __future__ import annotations

import re

SCREENING_WINDOW_S = 15.0
GREET_AFTER_PRESS_S = 1.0   # the carrier connects the caller after the key; greet once they can hear it
SCREENING_KEY = "1"

_PATTERNS = (
    re.compile(r"\bpress (?:1|one) to (?:take|accept|answer) (?:this|the) call\b"),
    re.compile(r"\bfast pick ?up\b.*\bnot an? voice ?mail\b"),
)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split())


def is_carrier_screening(text: str) -> bool:
    """True for the carrier screening prompt family, nothing else."""
    norm = _norm(text)
    return any(p.search(norm) for p in _PATTERNS)
