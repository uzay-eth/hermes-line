"""Menu keys on the Media Streams transport: in-band tones first, a REST redirect fallback.

Twilio does not accept outbound DTMF over a Media Stream ("Sending DTMF outbound from your
media server toward Twilio is not supported", twilio.com/docs/voice/media-streams,
accessed 2026-11-05). So keys are first played as locally generated in-band tones
(``mulaw.dtmf_tones``). If the menu does not react, the call briefly leaves the stream:

1. ``DtmfFallback.prepare(digits, origin="menu")`` stores the digits under a one-time nonce
   (refusing anything that is or contains a verification code);
2. L1 updates the live call over REST (``TelephonyAdapter.redirect_call``) to a plugin URL
   that carries only the nonce;
3. that URL's handler ``take``s the digits once and returns
   ``<Play digits="..."/>`` + ``<Connect><Stream>`` with a fresh one-time stream token
   (``twiml.play_digits_then_stream``); Twilio plays the digits on the call and opens a new
   stream, which ``VoiceCall`` re-attaches to.

The digits travel only in that one TwiML response, never in a URL. Verification codes never
take this path: they would end up in a TwiML document Twilio keeps in its request logs.

"Did the menu react?" signal (``menu_did_not_react``): after the tones have finished
playing (all marks acknowledged), the first final utterance from the menu within
``react_window_s`` is compared with the prompt that preceded the key press. It counts as
"no reaction" when it is an explicit retry/error phrase ("invalid entry", "I didn't get
that", "please try again", ...) or a repeat of the same prompt (word similarity >= 0.9, or a reply of 4+ words that all appear
in order inside the prompt, because STT may split a long menu into sentences). The rule is
deliberately strict: a false retry presses a key in the wrong (sub-)menu, a missed retry
only leaves the repeated prompt to Hermes. The "prompt" is
everything the menu said since the previous key press.
Any other utterance means the menu moved on. Silence is not a signal (a menu that
transfers to hold music is silent too), so it never triggers the fallback.
"""
from __future__ import annotations

import difflib
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

MENU_DIGITS = re.compile(r"^[0-9*#w]{1,32}$")
ERROR_PHRASES = (
    "invalid", "not a valid", "didn't get", "did not get", "didn't catch", "did not catch", "didn't understand",
    "did not understand", "try again", "please re-enter", "please reenter", "not recognized", "no entry",
    "i'm sorry, i didn't", "sorry, i didn't", "sorry, that", "we did not receive", "we didn't receive",
)


class DigitsRefused(Exception):
    """The digits may not use the fallback (verification code or bad shape)."""


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def menu_did_not_react(prompt: str, reply: str, *, threshold: float = 0.9) -> bool:
    """True when ``reply`` shows the menu ignored the key press (see module docstring)."""
    low = (reply or "").lower().replace("\u2019", "'")
    if not _words(low):
        return False
    if any(p in low for p in ERROR_PHRASES):
        return True
    a, b = _words(prompt), _words(reply)
    if not a or not b:
        return False
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() >= threshold:
        return True
    # STT splits a long menu into sentences: a reply of 4+ words that ALL appear, in order,
    # inside the prompt is the menu starting over. Any new word ("For NEW reservations, press 1")
    # means a different (sub-)menu: a false retry would press a key in the wrong menu, while a
    # missed retry is harmless (Hermes still hears the repeated prompt).
    contained = sum(block.size for block in matcher.get_matching_blocks())
    return len(b) >= 4 and contained == len(b)


@dataclass
class _Plan:
    call_id: str
    digits: str
    expires_at: float


class SealedGuard:
    """Remembers released codes for one call as keyed hashes, never as text."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._codes: List[Tuple[int, bytes]] = []

    def _mac(self, text: str) -> bytes:
        return hmac.new(self._key, text.encode(), hashlib.sha256).digest()

    def add(self, value: str) -> None:
        digits = re.sub(r"\D", "", value or "")
        if len(digits) >= 3:
            entry = (len(digits), self._mac(digits))
            if entry not in self._codes:
                self._codes.append(entry)

    def matches(self, digits: str) -> bool:
        """True if ``digits`` (pauses ignored) equal or contain a remembered code."""
        plain = re.sub(r"[^0-9]", "", digits or "")
        for length, mac in self._codes:
            for i in range(0, len(plain) - length + 1):
                if hmac.compare_digest(self._mac(plain[i:i + length]), mac):
                    return True
        return False


class DtmfFallback:
    """One-time digit plans for the redirect fallback, plus the sealed-code guard."""

    def __init__(self, clock: Callable[[], float], *, ttl_s: float = 60.0) -> None:
        self._clock = clock
        self._ttl = ttl_s
        self._plans: Dict[str, _Plan] = {}
        self.guard = SealedGuard()

    def prepare(self, call_id: str, digits: str, *, origin: str) -> str:
        """Store ``digits`` for one redirect and return the nonce for the TwiML URL.

        Only ``origin="menu"`` (a menu key press from L1's ``press_digits``) is accepted."""
        if origin != "menu":
            raise DigitsRefused("only menu keys may use the redirect fallback; codes go through the sealed path")
        if not isinstance(digits, str) or not MENU_DIGITS.match(digits):
            raise DigitsRefused("digits must be 0-9, *, # or w (max 32)")
        if self.guard.matches(digits):
            raise DigitsRefused("these digits contain a verification code; codes go only through the sealed path")
        self._prune()
        nonce = secrets.token_urlsafe(18)
        self._plans[nonce] = _Plan(call_id, digits, self._clock() + self._ttl)
        return nonce

    def take(self, call_id: str, nonce: str) -> Optional[str]:
        """The digits for ``nonce``, once; None if unknown, used, expired or for another call."""
        plan = self._plans.pop(nonce or "", None) if isinstance(nonce, str) and len(nonce) <= 64 else None
        if plan is None or plan.call_id != call_id or self._clock() > plan.expires_at:
            return None
        if self.guard.matches(plan.digits):   # a code released after the plan was made
            return None
        return plan.digits

    def discard(self, nonce: str) -> None:
        self._plans.pop(nonce, None)

    def _prune(self) -> None:
        now = self._clock()
        for k in [k for k, p in self._plans.items() if now > p.expires_at]:
            del self._plans[k]
