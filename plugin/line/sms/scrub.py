"""Exact-value scrubbing for codes that were released.

`mask_codes` finds code-SHAPED tokens, so it cannot see a letter-only code and it only masks
what looks like a code. Once a code has been released we know its exact value, so we can do
better: `ValueScrubber` remembers released values (in memory only, bounded, expiring) and
`scrub()` replaces every appearance of them, including spaced, dashed or per-character
spelled-out forms ("7 3 9 2 0 4", "739-204"), with `[CODE]`.

Use it on any text that may carry a released code after release: the call transcript, a
summary, a log line. `line.sms` logging already runs it through `MaskingFilter`, and
`hooks.py` runs it over every tool result and the final reply (public `transform_tool_result`
and `transform_llm_output` hooks), which is what keeps a code out of Hermes's session store.

Never persisted, never logged. A value is dropped after `ttl_s` (default 1 h) or when more
than `max_values` newer ones are registered.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, Optional, Tuple, Union

from .secret import SecretText
from .textnorm import normalize_text

CODE_TOKEN = "[CODE]"
MIN_LEN = 4
MAX_LEN = 16
# separators a speaker or a formatter can put between characters of one code
_SEP = r"[\s\-._,/]{0,3}"


class ValueScrubber:
    def __init__(self, ttl_s: float = 3600.0, max_values: int = 32, clock: Callable[[], float] = time.time) -> None:
        self._ttl = ttl_s
        self._max = max_values
        self._clock = clock
        self._lock = threading.Lock()
        # normalized value -> (expiry, compiled pattern)
        self._items: "OrderedDict[str, Tuple[float, re.Pattern]]" = OrderedDict()

    def register(self, value: Union[SecretText, str]) -> bool:
        """Remember a released value. Returns False for values too short/long to scrub safely."""
        raw = value.reveal() if isinstance(value, SecretText) else value   # the registry is a sanctioned reader
        if not isinstance(raw, str):
            return False
        norm = normalize_text(raw).strip()
        compact = re.sub(r"[\s\-._]", "", norm)
        if not (MIN_LEN <= len(compact) <= MAX_LEN):
            return False
        pattern = re.compile(_SEP.join(re.escape(c) for c in compact), re.IGNORECASE)
        with self._lock:
            self._items.pop(compact.casefold(), None)
            self._items[compact.casefold()] = (self._clock() + self._ttl, pattern)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return True

    def scrub(self, text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        patterns = self._live_patterns()
        if not patterns:
            return text
        out = normalize_text(text)   # fullwidth digits, NBSP, zero-width characters -> plain ASCII
        hits = 0
        for p in patterns:
            out, n = p.subn(CODE_TOKEN, out)
            hits += n
        # Nothing matched: hand back the caller's text untouched. Normalisation alone must never
        # rewrite an unrelated tool result while a code happens to be registered.
        return out if hits else text

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._live_patterns())

    def __repr__(self) -> str:
        return "ValueScrubber(values=%d)" % len(self)

    def _live_patterns(self):
        now = self._clock()
        with self._lock:
            for k in [k for k, (exp, _) in self._items.items() if exp < now]:
                del self._items[k]
            # longest first: a short code never shadows a longer one that contains it
            return [p for _, (_, p) in sorted(self._items.items(), key=lambda kv: -len(kv[0]))]


DEFAULT = ValueScrubber()


def register_released(value: Union[SecretText, str]) -> bool:
    return DEFAULT.register(value)


def scrub_text(text: str) -> str:
    """Scrub every released value from `text` (module-level default registry)."""
    return DEFAULT.scrub(text)
