"""Masking: the one place that decides what may leave the process.

Three levels, strictest first:

* `redact_all(text)`   - structural shape only ("Aa a a 9."). Safe for ANY log line,
  probe output or debug dump: no word, name or digit of the original survives.
* `mask_codes(text)`   - readable text with every code-like token replaced by
  `[CODE]`. For owner-facing transcripts. Best effort: it cannot see a code made only
  of letters ("your code is KJHDW"), so logs never use it for message bodies.
* `mask_sender(addr)`  - keeps the first character and last two, short codes stay.

`MaskingFilter` is the logging defence in depth: attach it to the `smsline` logger
(`install_log_masking()`) and a code that slips into a log message is masked anyway.

Policy, enforced by tests: nothing in smsline logs a message body. Log lines carry
`SmsMessage.log_summary()` (sender masked, length, code count) and never the text.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable, Optional

from .textnorm import normalize_text

CODE_TOKEN = "[CODE]"

# Any run of 4+ digits, optionally broken up by single spaces or hyphens
# ("482 915", "4829-1503", "2026-11-04", "+1 555 0100"). Over-masks dates, phone
# numbers and amounts on purpose: those are masked in a transcript, never leaked.
_DIGIT_RUN = re.compile(r"\d(?:[ \-]?\d){3,}")
# Alphanumeric tokens (hyphen groups allowed). Tokenise first, test each token in Python:
# a single regex with look-aheads over the token is quadratic on long crafted runs.
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def mask_codes(text: str) -> str:
    """Replace every code-like token with a fixed `[CODE]` (length is hidden too)."""
    if not text:
        return ""
    norm = normalize_text(text)
    out = _DIGIT_RUN.sub(CODE_TOKEN, norm)

    # Mixed letter+digit tokens with 4+ alphanumerics ("AB12CD", "X7K-9QP2"); "A1", "3D" survive.
    def _mixed(m: "re.Match[str]") -> str:
        tok = m.group(0)
        alnum = [c for c in tok if c.isalnum()]
        if len(alnum) >= 4 and any(c.isdigit() for c in alnum) and any(c.isalpha() for c in alnum):
            return CODE_TOKEN
        return tok

    return _TOKEN.sub(_mixed, out)


def redact_all(text: str, max_len: int = 48) -> str:
    """Shape of the text and nothing else. Letter runs -> a/A, digit runs -> 9."""
    if not text:
        return ""
    out = []
    prev = ""
    for c in normalize_text(text):
        cat = unicodedata.category(c)
        if cat[0] == "L":
            if prev in ("a", "A"):
                continue
            sym = "A" if c.isupper() else "a"
        elif cat[0] == "N":
            sym = "9"
            if prev == "9":
                continue
        elif c in "\n\r\t":
            sym = " "
            if prev == " ":
                continue
        elif c == " ":
            sym = " "
            if prev == " ":
                continue
        elif c.isascii() and not c.isalnum() and c.isprintable():
            sym = c  # ASCII punctuation carries no secret and helps read the shape
        else:
            sym = "."
        out.append(sym)
        prev = sym
    s = "".join(out)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def mask_sender(address: str) -> str:
    """Phone numbers and sender IDs are PII too. Keep first char and last two.

    All-digit short codes (<= 8 digits, e.g. 32665) are public service numbers and
    stay readable; they are what the task matcher is configured with.
    """
    if not address:
        return ""
    a = re.sub(r"[^\w+.\- ]", "", normalize_text(address))[:32]
    digits = re.sub(r"\D", "", a)
    if digits and digits == a and len(digits) <= 8:
        return digits
    if len(a) <= 3:
        return a[:1] + "***"
    return a[0] + "***" + a[-2:]


class MaskingFilter(logging.Filter):
    """Mask code-like tokens in whatever a log call produced (defence in depth)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # bad format args must not crash logging
            msg = str(record.msg)
        record.msg = mask_codes(msg)
        record.args = None
        return True


def install_log_masking(logger_names: Optional[Iterable[str]] = None) -> None:
    """Attach MaskingFilter to the given loggers (default: the shared `smsline` logger).

    A logger-level filter only sees records logged on that exact logger, not on its
    children. Every smsline module therefore logs through the single `smsline` logger
    (`smsline.log.LOG`). A host app that routes records elsewhere should also call
    `mask_handler(handler)` on its own handlers.
    """
    for name in logger_names or ("smsline",):
        lg = logging.getLogger(name)
        if not any(isinstance(f, MaskingFilter) for f in lg.filters):
            lg.addFilter(MaskingFilter())


def mask_handler(handler: logging.Handler) -> logging.Handler:
    """Attach MaskingFilter to a handler, covering every logger that reaches it."""
    if not any(isinstance(f, MaskingFilter) for f in handler.filters):
        handler.addFilter(MaskingFilter())
    return handler
