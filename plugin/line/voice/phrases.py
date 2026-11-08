"""Cache of rendered fixed lines, so they can be played without opening a voice session.

The plugin's own lines (the AI disclosure, "am I speaking with a live person?", "please
hold while I connect ...") are the same on every call. The first time one is spoken the
voice session renders it (``force_message``) and the µ-law audio is kept here; after
that it is played straight into the Media Stream and costs no model minutes.

Only text without digits is cached (no phone numbers, dates or codes from a task end up
in audio at rest), and the cache is in memory, bounded by bytes. The ten digit words are
the exception that makes sealed speech possible: a verification code is read aloud by
concatenating cached "zero" ... "nine" clips locally, so the code never reaches the
voice model.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from . import mulaw

DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
MAX_TEXT = 240


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def cacheable(text: str) -> bool:
    t = normalize(text)
    return bool(t) and len(t) <= MAX_TEXT and not re.search(r"\d", t)


class PhraseCache:
    def __init__(self, max_bytes: int = 8 * 1024 * 1024) -> None:
        self._max = max_bytes
        self._items: "OrderedDict[Tuple[str, str], bytes]" = OrderedDict()
        self._size = 0
        self.hits = 0
        self.misses = 0

    def get(self, voice: str, text: str) -> Optional[bytes]:
        key = (voice, normalize(text).lower())
        audio = self._items.get(key)
        if audio is None:
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return audio

    def put(self, voice: str, text: str, audio: bytes) -> bool:
        if not cacheable(text) or not audio or len(audio) > self._max // 4:
            return False
        key = (voice, normalize(text).lower())
        old = self._items.pop(key, None)
        if old is not None:
            self._size -= len(old)
        self._items[key] = bytes(audio)
        self._size += len(audio)
        while self._size > self._max and self._items:
            _, dropped = self._items.popitem(last=False)
            self._size -= len(dropped)
        return True

    def has(self, voice: str, text: str) -> bool:
        return (voice, normalize(text).lower()) in self._items

    def digit_clips(self, voice: str) -> Optional[Dict[str, bytes]]:
        clips = {}
        for d, word in enumerate(DIGIT_WORDS):
            audio = self._items.get((voice, word))
            if audio is None:
                return None
            clips[str(d)] = audio
        return clips

    def missing_digits(self, voice: str) -> Tuple[str, ...]:
        return tuple(w for w in DIGIT_WORDS if (voice, w) not in self._items)


def spoken_digits(value: str, clips: Dict[str, bytes], gap_ms: int = 250) -> Optional[bytes]:
    """µ-law audio reading ``value`` digit by digit from ``clips``; None if it is not all digits."""
    if not value or not value.isdigit() or not clips:
        return None
    gap = mulaw.silence(gap_ms)
    out = bytearray()
    for ch in value:
        out += clips[ch] + gap
    return bytes(out)
