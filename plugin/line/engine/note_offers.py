"""At most one offer to take a note per call (round 16 addition b, live call 5: four offers in one call).

After Hermes has offered once, later turns carry a reminder, and a spoken sentence that offers a note again is not
said unless the caller's latest words were about a note or a message. Sentences are only held back (to be checked
whole) after the first offer, so ordinary turns keep their first-text latency.
"""
from __future__ import annotations

import re

_NOTE = re.compile(r"\b(?:take|leave|add|make|jot|write|pass)\b[^.?!]{0,40}\b(?:a note|the note|notes?|a message|"
                   r"the message|messages?)\b|\b(?:note|message) (?:for|to) \w+", re.I)
_OFFERING = re.compile(r"\b(?:i (?:can|could) (?:also |still |just )?(?:take|leave|add|make|jot|write|pass)|"
                       r"want me to|should i|shall i|would you like|do you want|did you want|happy to|"
                       r"if you(?:'d| would)? like|let me know if)\b", re.I)
_FOLLOW_UP = re.compile(r"^\s*(?:\w+\W*){1,6}\?\s*$")  # "Should I do that?" right after a dropped offer
_ASKED = re.compile(r"\b(?:notes?|message|messages|write (?:it|that|this) down|let (?:him|her|them) know|"
                    r"tell (?:him|her|them))\b", re.I)
_SENTENCE = re.compile(r".+?(?:[.!?]+(?=\s)|$)", re.S)

REMINDER = ("[system] You already offered to take a note on this call. Do not offer it again; the caller will ask "
            "if they want one.")


def is_offer(sentence: str) -> bool:
    return bool(_NOTE.search(sentence or "")) and bool(_OFFERING.search(sentence or ""))


def contains_offer(text: str) -> bool:
    return any(is_offer(s) for s in split(text))


def caller_asked(text: str) -> bool:
    return bool(_ASKED.search(text or ""))


def split(text: str) -> list:
    return [m.group(0) for m in _SENTENCE.finditer(text or "") if m.group(0).strip()]


class SentenceGate:
    """Streams text sentence by sentence and drops repeated note offers."""

    _END = re.compile(r"[.!?]+\s")

    def __init__(self) -> None:
        self.pending = ""
        self.kept: list = []
        self.dropped = 0
        self.last_dropped = False

    def _take(self, sentence: str) -> str:
        follow_up = self.last_dropped and _FOLLOW_UP.match(sentence)
        self.last_dropped = False
        if is_offer(sentence) or follow_up:
            self.dropped += 1
            self.last_dropped = True
            return ""
        self.kept.append(sentence)
        return sentence

    def feed(self, chunk: str) -> str:
        """The text that may be spoken now (whole sentences only)."""
        self.pending += chunk
        out = []
        while True:
            m = self._END.search(self.pending)
            if m is None:
                break
            sentence, self.pending = self.pending[:m.end()], self.pending[m.end():]
            out.append(self._take(sentence))
        return "".join(out)

    def flush(self) -> str:
        rest, self.pending = self.pending, ""
        return self._take(rest) if rest.strip() else ""

    def text(self) -> str:
        return "".join(self.kept).strip()
