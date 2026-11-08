"""Speaking to automated assistants (round 25, Delta run 1: evidence/trial/call_b669b105d4774a49).

Some businesses answer with a conversational AI assistant instead of a keypad menu ("I'm not a live person. I'm
Delta's automated assistant." then open questions, "I can give you a moment if you need it."). The round-24 menu
gate rightly sends nothing to such text, so the call never reached a person. This module is the one speech path for
automated systems on restricted and information-only tasks:

* It applies only before a person is detected, and only while L2's detection is ``automated_assistant`` (L2 is the
  single source of bot and transfer recognition; round 27).
* Only fixed phrases chosen by the engine are sent, never model text: "Representative.", "Agent.", "Speak to an
  agent.", "<Category> question." (the task option ``ivr_category``: baggage, reservations, general information),
  and "No, a general question." only as the answer to a question whether this is about an existing trip,
  reservation or booking.
* The rule (``choose``): a callback, payment or change offer gets nothing, even when it also asks for data (round 27,
  N22); a plain request for data (a name, number, code, date or identity) gets "Agent."; an existing-trip question gets "No, a general question."; the
  first turn gets "Representative."; an open routing question gets the category question, then "Speak to an agent.",
  once each; anything else (loops, repeats, patience lines) gets "Agent.".
* At most one phrase every ``MIN_SPACING_S`` and ``MAX_PHRASES`` per call; after that, silence.

Never yes, never a name, number, code, date, payment or identity data, never a callback, change or payment request:
none of the phrases can carry any of that. Deterministic patterns; a candidate Jev decision point.
"""
from __future__ import annotations

import re
from typing import Optional

MIN_SPACING_S = 8.0
MAX_PHRASES = 12
CATEGORIES = ("baggage", "reservations", "general information")
PHRASES = {
    "representative": "Representative.",
    "agent": "Agent.",
    "speak_agent": "Speak to an agent.",
    "category": "{category} question.",
    "no_general": "No, a general question.",
}

_EXISTING_TRIP = re.compile(
    r"\b(?:(?:about|regarding|calling about|for) (?:an? |your |the )?(?:existing |current |upcoming |recent )?"
    r"(?:trip|reservation|booking)|(?:have|got) (?:an? )?(?:existing |current |upcoming )?(?:trip|reservation|"
    r"booking)|existing (?:trip|reservation|booking))\b")
_DATA = re.compile(r"\b(?:name|number|numbers|confirmation|code|record locator|skymiles|frequent flyer|account|date|"
                   r"birth|birthday|card|payment|zip|postal|ticket|traveler|email|address|pin|password|passport|"
                   r"last four|digits|phone|identity|verify|verification|security question)\b")
_OPEN = re.compile(r"\b(?:how can i help|how may i help|what can i help|what can i do for you|what are you calling|"
                   r"reason for your call|tell me (?:what|why|how|in a few words|briefly)|what would you like|"
                   r"briefly describe|in a few words|what brings you)\b")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'")).split())


_CALLBACK_OFFER = re.compile(
    r"\b(?:call (?:you|me|us)(?: back)?|calls? back|callback|call later|ring (?:you|me)|ring back|ringback|"
    r"phone (?:you|me)|text (?:you|me)|contact (?:you|me)|be contacted|reach (?:you|me)|get back to (?:you|me)|"
    r"return (?:your|the) call|(?:keep|hold|save) (?:your|my) (?:place|spot)|place in line|your (?:place|spot) in "
    r"line|later)\b")


# Round 39 (Delta run 3): an offer to switch channel or send something to the caller, and any prompt asking to say yes
# or no (its other half, "Or you can say, no.", is the same offer): no fixed phrase answers either.
_CHANNEL_OFFER = re.compile(
    r"\b(?:say (?:yes|no)|send (?:you |a |an |the )*(?:text|message|link|sms|email)|(?:message|text|chat) (?:with|to) "
    r"(?:a |an |our )?(?:representative|agent)|to your (?:phone|mobile|cell)|instead of (?:waiting|holding)|"
    # round 40 (B17): switching channel ("Would you like to switch channels?", to chat/text/the app; continue by text)
    r"switch(?:ing)? (?:to )?(?:a |another |the |our )?(?:[a-z]+ ){0,2}?(?:channels?|chat|text|texting|messaging|sms|"
    r"app|web)|"
    r"(?:continue|carry on|finish|keep going) (?:this )?(?:by|via|over|in|on|with) (?:a |the |our )?"
    r"(?:text|texting|chat|messaging|message|sms|app)|(?:move|transfer|take) (?:this |the |our )?(?:conversation|call|"
    r"chat) (?:to|over to) (?:a |the |our )?(?:text|chat|messaging|sms|app))\b")


def unsafe_offer(text: str) -> bool:
    """A callback, payment or change offer: nothing is said (whatever the bot asks). Narrower than the keypad gate's
    callback wording, so an ordinary routing question ("what are you calling about?") still gets its phrase."""
    from . import menu_gate
    norm = " ".join(menu_gate._tokens(text))
    return bool(_CALLBACK_OFFER.search(norm)) or bool(_CHANNEL_OFFER.search(norm)) or any(
        name in ("payment", "change") and rule.search(norm) for name, rule in menu_gate._CATEGORY)


def existing_trip_question(text: str) -> bool:
    norm = _norm(text)
    return bool(_EXISTING_TRIP.search(norm)) and ("?" in (text or "") or re.match(r"(?:are|is|do|does|did|was|will)\b",
                                                                                   norm) is not None)


def data_request(text: str) -> bool:
    return bool(_DATA.search(_norm(text)))


def open_question(text: str) -> bool:
    return bool(_OPEN.search(_norm(text)))


def choose(text: str, said: list, category: Optional[str]) -> Optional[str]:
    """The phrase id for this assistant line, or None for silence. ``said``: the phrase ids already said."""
    if unsafe_offer(text):
        return None  # a callback, payment or change offer: nothing, even when it also asks for data (N22)
    if data_request(text):
        return "agent"  # a plain identifier request (a name, number, code, date): "Agent.", never the data
    if existing_trip_question(text):
        return "no_general"
    if not said:
        return "representative"
    if open_question(text):
        if category and "category" not in said:
            return "category"
        if "speak_agent" not in said:
            return "speak_agent"
    return "agent"


def phrase_text(phrase_id: str, category: Optional[str]) -> str:
    text = PHRASES[phrase_id]
    if phrase_id == "category":
        if category not in CATEGORIES:
            raise ValueError("no approved ivr_category")
        text = text.format(category=category[:1].upper() + category[1:])
    return text


def validate_category(value) -> Optional[str]:
    """The ``ivr_category`` task option: one of baggage, reservations, general information, or none."""
    if value in (None, ""):
        return None
    norm = " ".join(str(value).lower().split())
    if norm not in CATEGORIES:
        raise ValueError("ivr_category must be baggage, reservations or general information")
    return norm
