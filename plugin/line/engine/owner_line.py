"""Owner line mode (round 13): the owner calls the line, proves it with a PIN, and has Hermes place calls.

Caller ID is not authentication. A call from ``owner_number`` is only offered the PIN; until it passes, the caller is
an unknown caller (take a message only). The PIN comes from the plugin's secret scope (``HERMES_LINE_OWNER_PIN``,
4-8 digits), is keyed on the phone's keypad only (R1 delta 10 N14: spoken digits never verify), is compared in
constant time, and never reaches Hermes, the transcript, logs or events: key presses are intercepted before anything
records them, and the configured PIN is scrubbed from any speech as a backstop (``scrub_pin``). Failures count
against a persistent per-line budget (N13).

After the PIN, the owner may ask Hermes to call a business. Hermes creates a normal outbound task through the
destination gate; the line reads the request back (a fixed line, spoken by the plugin, not the model), ending with
"To confirm, key the last four digits of the number, then pound." Only those keyed digits, for that task and
generation, within 60 s, approve it (R1 delta 11 N12: playback timing cannot prove the read-back was heard, so speech
never approves on the owner line). Wrong digits or the timeout cancel; chat approval still works.
"""
from __future__ import annotations

import hmac
import re
from typing import Optional

PIN_SECRET = "HERMES_LINE_OWNER_PIN"
PIN_RE = re.compile(r"[0-9]{4,8}")
MAX_PIN_FAILURES = 3        # per call, and in a row per line: then a cooldown (R1 delta 10 N13)
LOCK_COOLDOWN_S = 15 * 60
DAY_FAILURE_LIMIT = 10      # in 24 h per line: locked until the owner runs /line unlock-owner
DTMF_PAUSE_S = 2.5
CONFIRM_WINDOW_S = 60.0     # the keypad confirmation of a read-back expires (R1 delta 11 N12)
HOLD_DIGITS_S = 1.5         # a caller frame ending in a possible PIN start waits this long (N14)
LIVE_DIAL_WINDOW_S = 30.0   # round 17: a live-bridge approval must start dialling within this long

_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "won": "1", "two": "2", "to": "2", "too": "2",
          "three": "3", "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8",
          "nine": "9"}
_STRICT_WORDS = {"zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}


def valid_pin(value) -> Optional[str]:
    """The configured PIN if it is 4-8 digits, else None (owner mode off)."""
    return value if isinstance(value, str) and PIN_RE.fullmatch(value) else None


def spoken_digits(text: str) -> str:
    """The digits in an utterance: numerals and spoken digit words ("one two three four", "1 2 3 4", "1234").
    Homophones (to, for, ate) count only next to other digits, so "I'm calling for Dana" yields nothing."""
    tokens = re.findall(r"[0-9]+|[A-Za-z']+", (text or "").lower())
    out, run = [], []

    def flush():
        digits = "".join(d for d, _ in run)
        strong = any(s for _, s in run)
        if digits and (strong or len(run) >= 2):
            out.append(digits)
        run.clear()

    for tok in tokens:
        if tok.isdigit():
            run.append((tok, True))
        elif tok in _WORDS:
            run.append((_WORDS[tok], tok in _STRICT_WORDS))
        else:
            flush()
    flush()
    return "".join(out)


_TOKEN = re.compile(r"[0-9]+|[A-Za-z']+")


def _tokens(frames: list) -> list:
    """Every word/numeral token of ``frames`` as (frame, start, end, digits-or-None, strong)."""
    out = []
    for i, text in enumerate(frames):
        for m in _TOKEN.finditer(text or ""):
            tok = m.group(0).lower()
            if tok.isdigit():
                out.append((i, m.start(), m.end(), tok, True))
            elif tok in _WORDS:
                out.append((i, m.start(), m.end(), _WORDS[tok], tok in _STRICT_WORDS))
            else:
                out.append((i, m.start(), m.end(), None, False))
    return out


def _runs(tokens: list) -> list:
    """Maximal runs of digit tokens. Punctuation and frame boundaries never break a run (R1 delta 11: "four eight;
    two one", or "four eight" then "two one"); a word that is not a digit does. Homophones (to, for, ate) count
    only inside a run with another digit."""
    runs, cur = [], []
    for tok in tokens:
        if tok[3] is not None:
            cur.append(tok)
            continue
        if cur:
            runs.append(cur)
        cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if any(t[4] for t in r) or len(r) >= 2]


def _near(pin: str) -> set:
    """The PIN with any one digit missing (a partial transcription), for PINs of 4 or more digits."""
    return {pin[:i] + pin[i + 1:] for i in range(len(pin))} if len(pin) >= 4 else set()


def scrub_window(frames: list, pin: Optional[str]) -> list:
    """N14 (R1 delta 10/11): replace the configured PIN with ``[PIN]`` across a window of consecutive caller frames.
    A run of digits (numerals or digit words, any separators, across frames) is replaced in every frame it touches
    when it contains the PIN, or, if the run is at most one digit longer than the PIN, the PIN with one digit
    missing. Long runs such as phone numbers are only scrubbed for the full PIN. Returns the frames rewritten."""
    if not pin or not frames:
        return list(frames)
    near = _near(pin)
    hits = []
    for run in _runs(_tokens(frames)):
        digits = "".join(t[3] for t in run)
        if pin in digits or (len(digits) <= len(pin) + 1 and any(n in digits for n in near)):
            hits.append(run)
    if not hits:
        return list(frames)
    spans: dict = {}
    for run in hits:
        for tok in run:
            spans.setdefault(tok[0], []).append((tok[1], tok[2]))
    out = []
    for i, text in enumerate(frames):
        regions = []
        for start, end in sorted(spans.get(i, [])):  # merge tokens of one run inside one frame
            if regions and re.fullmatch(r"[^A-Za-z0-9]*", text[regions[-1][1]:start]):
                regions[-1][1] = end
            else:
                regions.append([start, end])
        pieces, last = [], 0
        for start, end in regions:
            pieces += [text[last:start], "[PIN]"]
            last = end
        out.append("".join(pieces) + text[last:])
    return out


def scrub_pin(text: str, pin: Optional[str]) -> str:
    """One frame of :func:`scrub_window` (every boundary that records or forwards text)."""
    return scrub_window([text], pin)[0] if text and pin else text


def pin_may_continue(frames: list, pin: Optional[str]) -> bool:
    """True when the last frame ends in a digit run whose tail could be the start of the PIN: hold it briefly, the
    rest of the PIN may come in the next frame (R1 delta 11 N14)."""
    if not pin or not frames:
        return False
    tokens = _tokens(frames)
    if not tokens or tokens[-1][3] is None or tokens[-1][0] != len(frames) - 1:
        return False
    tail = []
    for tok in reversed(tokens):
        if tok[3] is None:
            break
        tail.insert(0, tok)
    digits = "".join(t[3] for t in tail)
    return any(digits.endswith(pin[:k]) for k in range(1, len(pin)))


def last_digits(e164: str, count: int) -> str:
    """The confirmation challenge for a destination: its last ``count`` digits (R1 delta 11 N12: four for the owner's
    own number or an earlier approved destination; R1 delta 12: seven for any other, against suffix collisions)."""
    return re.sub(r"\D", "", e164 or "")[-count:]


def last_four(e164: str) -> str:
    return last_digits(e164, 4)


def pin_matches(entered: str, pin: str) -> bool:
    """Constant-time comparison; the length is not revealed by an early exit either."""
    return hmac.compare_digest((entered or "").encode(), (pin or "").encode())


_YES = re.compile(r"(yes|yeah|yep|yup|sure|correct|right|go ahead|do it|please do|ok|okay|affirmative|confirm(ed)?)"
                  r"( please| go ahead| do it| that's right| that is right| call them| thanks| thank you)*[.!]*")
_NO = re.compile(r"(no|nope|don't|do not|cancel|stop|wait|not now|never ?mind)\b.*")


def reply_is_yes(text: str) -> bool:
    """A plain yes, nothing else (a "yes but call a different number" is not one)."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower().replace(",", ""))
    return bool(_YES.fullmatch(t))


def reply_is_no(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(_NO.match(t))


def spoken_number(e164: str) -> str:
    """+17755550199 -> "7 7 5, 5 5 5, 0 1 9 9" (NANP), so text-to-speech reads every digit back."""
    digits = re.sub(r"\D", "", e164 or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{' '.join(digits[:3])}, {' '.join(digits[3:6])}, {' '.join(digits[6:])}"
    return " ".join(digits)
