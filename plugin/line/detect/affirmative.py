"""The one clear-yes contract for the live-person question (R1b delta 26; shared by L1 and L2).

L2 owns this module; L1 added it in L1 round 29 at the coordinator's direction, so both layers decide "is this reply a
clear yes?" with the same function. After a call had a bot label, the owner is rung only when our fixed live-person
question was asked and the reply passes ``clear_yes``.

The reply is normalised (lower case; every punctuation mark a space, except that a question mark anywhere counts),
and it is a clear yes only when it has no question mark and its whole text equals one entry of ``CLEAR_YES``, or is
"yes this is <one-word name>". "Yes, no.", "Speaking? Is that correct?" and "Yes, would you like to make a payment?"
are not.

Round 43 (MIT-018 STEER-3, Hilton call 1, 2026-11-08): a real agent answered our question with "Yes. Are you a
live person too?" and was missed. As a REPLY to our pending question only (``clear_yes_reply``; callers check the
question is pending and the reply started after it): a reply whose first clause is a clear yes ("yes", "yeah", "yep",
"I am", "speaking", "this is <name>", a ``CLEAR_YES`` entry), not itself a question, followed ONLY by a question back
about who or what we are ("Are you a live person too?", "Are you a robot?", "Who is this?") or a one-word name
("This is Chris."), is a clear yes; so is "I'm a live person.". Any other words after the yes keep the old result: the
reviewed tables keep "Yes, how can I help?" (N30), "Yes, enjoy our special offer." (B2) and "Yes, I am a real person
and I can help" out, so the broader "yes + anything" rule was not adopted. Round 43b (Hilton call 2, where real
agents said "Yes. You are speaking with the live person. Yes. Indeed." and "You are speaking to a live person. How can
I help you?" eight times): an explicit live-person statement ("you are speaking with a live person") counts as the
yes or after it, plain affirmations ("Yes.", "Indeed.") may follow, and an offer of help ("How can I help?") may follow
only when such a statement is present.
"""
from __future__ import annotations

import re
from typing import Optional

BOT_LABELS = frozenset({"automated_assistant", "ivr", "hold_message", "hold_music", "voicemail"})

CLEAR_YES = frozenset({
    "yes", "yes i am", "yeah", "yep", "speaking", "yes speaking", "this is a real person", "i am a real person",
    "i m a real person", "yes i m a live person", "yes i am a live person", "i am a live agent",
    "yes you are speaking to a live agent", "yeah it s me",
})
# Round 43: whole replies that are a clear yes only as a reply to our pending question (Hilton's "I'm a live person.").
_REPLY_YES = frozenset({"i m a live person", "i am a live person", "i m a live agent", "i m live", "i am live"})
# Round 43c (MIT-018 STEER-4): the reply decision keys on what bots say, not on a list of human phrases.
# An explicit affirmation at the start of the reply; any words may follow.
_AFFIRM = re.compile(
    r"(?:(?:uh|um|oh|well|hi|hello|okay|ok) )*(?:"
    r"(?:yes|yeah|yep|yup|indeed)(?: [a-z ]*)?"
    r"|i am|i m here|speaking"
    r"|this is [a-z]+"
    r"|(?:yes )?(?:you are|you re|you have come through to) (?:speaking (?:with|to) )?(?:a|the) (?:live|real) "
    r"(?:person|agent|human|representative)(?: [a-z ]*)?"
    r"|(?:i am|i m|this is) (?:a |the )?(?:live|real|human)(?: [a-z ]*)?)")
# What bots, menus and recordings say (and negatives): any of it anywhere in the reply keeps it from being a yes.
_BOT = re.compile(
    r"\b(?:virtual|automated|automatic|digital|artificial|robot|bot|recorded|recording|machine|assistant|"
    r"press|pressing|dial|keypad|menu|option|options|select|or say|say yes|say no|say [a-z ]+ or|enter your|enter the|"
    r"your call is important|please hold|stay on the line|remain on the line|queue|wait time|next available|"
    r"transfer|transferring|offer|special|promotion|enjoy|visit|learn more|website|www|survey|monitored|privacy|"
    r"credit card|credit cards|earn|points|are you still there|say your|please say|account number|"
    r"confirmation number|pay|payment|payments|card|billing|busy|for waiting|patience|didn t get that|"
    r"didn t catch that|repeat that|com|slash|app|click|clicking|members|available|redeem|redeeming|certificate|"
    r"flights|wi fi|message us|need help|takeoff|on the go|add or update|known traveler|"
    r"not|no|nope)\b")
# Round 43c: an owner ring without a clear yes needs a conversational reply: a greeting, a first-person phrase, or a
# question to us (recorded tips and announcements rarely have one).
_CONVERSATIONAL = re.compile(r"\b(?:hello|hi|hey|i|i m|i am|me|my|we|we re|are you|who is|who s|can you hear|"
                             r"is this|speaking)\b")
# Bare greetings and closings: human-like enough for the re-ask, never enough to ring the owner on their own.
_GENERIC = re.compile(r"(?:(?:hi|hello|hey|goodbye|bye|thank you|thanks|okay|ok|alright)(?: [a-z]+)?"
                      r"|(?:thank you|thanks) for calling(?: [a-z ]+)?|how (?:can|may) i (?:help|assist)(?: you)?(?: today)?"
                      r"|how can i be of assistance|what can i do for you)")
# A question back about what WE are ("Are you a robot?") is a person's reply, not a bot cue: removed before the check.
_ASK_BACK_US = re.compile(r"(?:and |so )?are you (?:also )?(?:a |an )?(?:live |real |actual )?"
                          r"(?:person|human|robot|bot|machine|computer|recording|ai|automated)(?: too| as well)?")
_NAMED = re.compile(r"yes this is [a-z]+")


def normalise(text: str) -> str:
    """Lower case, punctuation as spaces, whitespace collapsed (question marks are checked separately)."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower().replace("’", "'")).split())


def clear_yes(text: str) -> bool:
    """True only for a whole-reply clear yes from the shared list, with no question mark."""
    if "?" in (text or ""):
        return False
    norm = normalise(text)
    return norm in CLEAR_YES or bool(_NAMED.fullmatch(norm))


def bot_cue(text: str) -> bool:
    """Round 43c: the reply contains what bots, menus and recordings say, or a negative (questions back about what we
    are, such as "Are you a robot?", do not count)."""
    parts = [normalise(p) for p in re.split(r"[.,!?;:]+", text or "")]
    return any(_BOT.search(p) for p in parts if p and not _ASK_BACK_US.fullmatch(p))


def clear_yes_reply(text: str) -> bool:
    """Round 43c: the clear-yes test for a reply to OUR pending live-person question (callers check that the question
    is pending and the reply started after it). The reply starts with an explicit affirmation ("yes", "yeah", "I am",
    "you are speaking with a live person", "this is <name>", "I'm a real person"; any words may follow), that first
    clause is not itself a question, and nothing in the reply is what bots, menus or recordings say. Without a pending
    question, ``clear_yes`` alone applies, unchanged."""
    text = (text or "").replace("\u2019", "'")
    if clear_yes(text):
        return True
    first = re.match(r"\s*([^.,!?;:]*)([.,!?;:]*)", text)
    lead, end = normalise(first.group(1)), first.group(2)
    if "?" in end and lead not in ("hi", "hello", "uh", "um", "oh", "well", "okay", "ok"):
        return False  # "Yes? Are you there?"
    if lead in ("hi", "hello", "uh", "um", "oh", "well", "okay", "ok"):
        rest = text[first.end():]
        second = re.match(r"\s*([^.,!?;:]*)([.,!?;:]*)", rest)
        lead, end = normalise(second.group(1)), second.group(2)
        if "?" in end:
            return False  # "Hello? Are you a live person too?"
    if not lead or not _AFFIRM.fullmatch(lead):
        return False
    return not bot_cue(text)


def reply_verdict(text: str, label: Optional[str] = None) -> str:
    """Round 43c: what a reply to our live-person question is. "yes" (``clear_yes_reply``), "bot" (a bot cue in the
    text, a negative, no words, or the detector's automated/menu/hold/voicemail label), else "unclear": a human-like
    reply that is not a clear yes (the engine asks at most twice per party, then lets the owner judge), or "generic"
    (only greetings, thanks or an offer of help, or no conversational word at all: a re-ask, but never a ring on its
    own)."""
    if clear_yes_reply(text):
        return "yes"
    if not normalise(text) or bot_cue(text) or label in BOT_LABELS:
        return "bot"
    parts = [normalise(p) for p in re.split(r"[.,!?;:]+", text) if normalise(p)]
    if all(_GENERIC.fullmatch(p) for p in parts) or not _CONVERSATIONAL.search(normalise(text)):
        return "generic"  # a re-ask at most; never an owner ring on its own
    return "unclear"
