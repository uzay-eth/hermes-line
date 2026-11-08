"""Text normalisation shared by masking and OTP extraction.

Everything here is a 1:1 character mapping except `strip_invisible`, so spans found
on the normalised text still line up with it. Real SMS bodies are untrusted input:
fullwidth digits, Arabic-Indic digits, NBSP and zero-width characters all show up in
verification messages (sometimes on purpose, to dodge filters).
"""
from __future__ import annotations

import unicodedata

# Zero-width and bidi control characters that carry no visible content.
_INVISIBLE = {
    "​", "‌", "‍", "‎", "‏", "⁠", "﻿",
    "‪", "‫", "‬", "‭", "‮", "⁦", "⁧",
    "⁨", "⁩", "­",
}


def strip_invisible(text: str) -> str:
    return "".join(c for c in text if c not in _INVISIBLE)


def _map_char(c: str) -> str:
    if c in ("\n", "\r", "\t"):
        return c
    cat = unicodedata.category(c)
    if cat == "Zs":
        return " "
    dec = unicodedata.decimal(c, None)
    if dec is not None:
        return str(dec)
    if ord(c) > 127 and cat[0] == "L":
        # Fullwidth/styled Latin letters fold to ASCII. Punctuation is NOT folded: a
        # fullwidth comma next to a digit ("482915，5分钟") must not become "482915,5",
        # which would read as a thousands separator. Length-changing folds are skipped
        # so the mapping stays 1:1.
        n = unicodedata.normalize("NFKC", c)
        if len(n) == 1 and n.isascii():
            return n
    return c


def normalize_text(text: str) -> str:
    """Strip invisible characters, then map digits/spaces/fullwidth forms to ASCII, 1:1."""
    return "".join(_map_char(c) for c in strip_invisible(text))


def fold(text: str) -> str:
    """Length-preserving lower-casing (str.lower() can change length, e.g. U+0130)."""
    out = []
    for c in text:
        lc = c.lower()
        out.append(lc if len(lc) == 1 else c)
    return "".join(out)
