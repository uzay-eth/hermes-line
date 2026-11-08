"""Masking for everything the engine stores, logs or shows.

Transcripts and summaries are masked before they are written anywhere: one-time
codes (a 4-8 digit run near a code keyword), card-like runs (13-19 digits),
SSN-shaped runs and secret-looking tokens. Masked spans are the bracketed
uppercase tokens of docs/CONTRACT.md: ``[CODE]``, ``[CARD]``, ``[NUMBER]``
(plus ``[SECRET]``). When ``line.sms`` is installed its exact-value scrubber
(``scrub_text``) runs first, so a released code is hidden in any spelling. Phone
numbers shown in the UI keep only the area code and last four digits.
"""
from __future__ import annotations

import re
from typing import Callable

CODE_MASK = "[CODE]"
CARD_MASK = "[CARD]"
ID_MASK = "[NUMBER]"
SECRET_MASK = "[SECRET]"
_SCRUBBERS: list[Callable[[str], str]] = []


def register_scrubber(fn: Callable[[str], str]) -> None:
    """Add an extra masking pass (``line.sms.scrub_text``) that runs first."""
    if fn not in _SCRUBBERS:
        _SCRUBBERS.append(fn)

_DIGIT_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SEP = r"[\s\-.]?"
_CARD = re.compile(r"(?<!\d)(?:\d" + _SEP + r"){12,18}\d(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_KEYWORD = re.compile(r"(code|verification|verify|passcode|pass code|pin|otp|one[\s-]?time|security)", re.I)
_DIGIT_RUN = re.compile(r"(?<![\d])(?:\d[\s\-]?){3,7}\d(?![\d])")
_WORD_RUN = re.compile(r"\b(?:" + _DIGIT_WORDS + r"[\s,\-]+){3,7}" + _DIGIT_WORDS + r"\b", re.I)
_SECRETISH = re.compile(r"\b(?:AC|SK)[0-9a-fA-F]{32}\b|\b[A-Za-z0-9_\-]{40,}\b")
_WINDOW = 48


def _near_keyword(text: str, start: int, end: int) -> bool:
    lo, hi = max(0, start - _WINDOW), min(len(text), end + _WINDOW)
    return bool(_KEYWORD.search(text[lo:start]) or _KEYWORD.search(text[end:hi]))


def mask_text(text: str) -> str:
    if not text:
        return ""
    out = str(text)
    for scrub in _SCRUBBERS:
        try:
            out = scrub(out)
        except Exception:
            out = CODE_MASK if any(ch.isdigit() for ch in out) else out
    out = _SECRETISH.sub(SECRET_MASK, out)
    out = _CARD.sub(CARD_MASK, out)
    out = _SSN.sub(ID_MASK, out)

    def _code(m: re.Match) -> str:
        return CODE_MASK if _near_keyword(m.string, m.start(), m.end()) else m.group(0)

    out = _DIGIT_RUN.sub(_code, out)
    out = _WORD_RUN.sub(_code, out)
    return out


def mask_number(e164: str) -> str:
    """'+17755550123' -> '+1 775-•••-0123'. Unknown shapes keep only the last 2 digits."""
    digits = re.sub(r"\D", "", e164 or "")
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]}-•••-{digits[7:]}"
    if len(digits) >= 4:
        return "•" * (len(digits) - 2) + digits[-2:]
    return "•••"


def mask_digits(digits: str) -> str:
    """DTMF sent to an IVR: keep short menu choices, hide anything longer."""
    if len(digits or "") <= 2:
        return digits or ""
    return f"[{len(digits)} digits]"
