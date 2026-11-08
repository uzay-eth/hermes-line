"""Owner-timeout fallback question (round 16 addition c, the approved hold-line trial, decision A).

A task option pair, set when the task is created and shown with the approval request:

* ``owner_ring_timeout_s``: how long the owner has to accept (press 1) once a person answers and the owner is rung.
  The engine ends the owner leg at that moment, whatever Twilio's ring timeout does.
* ``fallback_question``: if the owner did not accept in time, the line asks the person this one question, listens to
  the answer, thanks them and ends the call. Every line is fixed text spoken by the plugin; Hermes takes no turn
  once a person has answered, so it cannot invent a booking, confirmation number or identity. The summary is the
  question and the person's own words, not a model's.

A task with a fallback question is an information-only task. On top of its guidance's restrictions:

* callbacks, payments and changes are always forbidden (task_rules);
* phone-menu keys are single keys only (there is no number, booking or account to enter);
* a spoken menu reply must use a small fixed vocabulary ("agent", "representative", "baggage", ...), so no name or
  number can be said.
"""
from __future__ import annotations

import re
from typing import Optional

RING_TIMEOUT_RANGE = (10, 60)
QUESTION_MAX = 200
ANSWER_MAX_S = 45.0        # longest the line waits for an answer after asking
QUIET_AFTER_ANSWER_S = 2.5  # silence after an utterance that sounds like an answer (a price, "free", ...)
QUIET_OTHERWISE_S = 6.0     # silence after "let me check" and similar
HOLDING_LINE_EVERY_S = 10.0

_FORBIDDEN_IN_QUESTION = re.compile(
    r"\b(?:my name|i am|i'm|this is|we are|confirmation|booking (?:number|reference|code)|record locator|"
    r"reservation (?:number|code)|account|card|ticket number|pnr|password|pin|call (?:me|us) back|callback|"
    r"pay|payment|change my|rebook|cancel my)\b", re.I)
_ANSWERISH = re.compile(r"\d|\$|\b(?:dollars?|free|no (?:fee|charge|cost)|fee|fees|charge|charges|costs?|price|"
                        r"included|complimentary|first bag|second bag)\b", re.I)
MENU_WORDS = frozenset("""
agent representative operator person live human customer service help support main menu other something else
reservations reservation baggage bag bags checked fees fee general information question questions yes no
speak talk to with a an the new
""".split())


def validate(question: Optional[str], timeout_s, *, owner_mode: bool, owner_number: str) -> dict:
    """The options to store, or ValueError with the reason. Deterministic; no model involved."""
    options: dict = {}
    if timeout_s is not None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or int(timeout_s) != timeout_s:
            raise ValueError("owner_ring_timeout_s must be a whole number of seconds")
        if not RING_TIMEOUT_RANGE[0] <= int(timeout_s) <= RING_TIMEOUT_RANGE[1]:
            raise ValueError(f"owner_ring_timeout_s must be {RING_TIMEOUT_RANGE[0]}-{RING_TIMEOUT_RANGE[1]}")
        options["owner_ring_timeout_s"] = int(timeout_s)
    if question is not None:
        q = " ".join(str(question).split())
        if not 10 <= len(q) <= QUESTION_MAX:
            raise ValueError(f"fallback_question must be 10-{QUESTION_MAX} characters")
        if not q.endswith("?") or q.count("?") != 1 or re.search(r"[.!;]", q[:-1]):
            raise ValueError("fallback_question must be exactly one question")
        if re.search(r"\d", q):
            raise ValueError("fallback_question must not contain digits (no numbers, bookings or codes)")
        if _FORBIDDEN_IN_QUESTION.search(q):
            raise ValueError("fallback_question must be a general question: no identity, booking, account, "
                             "callback, payment or change")
        options["fallback_question"] = q
    if options and not owner_mode:
        raise ValueError("owner_ring_timeout_s and fallback_question need mode owner")
    if options and not owner_number:
        raise ValueError("owner_ring_timeout_s and fallback_question need owner_number in the settings")
    return options


def answerish(text: str) -> bool:
    return bool(_ANSWERISH.search(text or ""))


def menu_phrase_ok(phrase: str) -> bool:
    words = re.findall(r"[A-Za-z']+|\d", phrase or "")
    return bool(words) and len(words) <= 5 and all(w.lower() in MENU_WORDS for w in words)


def single_keys_only(digits: str) -> bool:
    return len([d for d in digits or "" if d != "w"]) <= 1


def question_line(owner: str, question: str) -> str:
    return f"Sorry, {owner} can't come to the phone right now. I have just one quick question: {question}"


def clarify_line(question: str) -> str:
    return f"I don't have a booking or an account to look up; it's a general question. {question}"


def holding_line(owner: str) -> str:
    return f"Thank you for your patience, I'm connecting {owner} now."


# Round 30 (Delta run 2): one neutral closing line, whatever was said; the line cannot know it got an answer.
THANKS_LINE = "Thank you for your time. Goodbye."
NO_ANSWER_LINE = THANKS_LINE
INFO_ONLY_RULE = ("This is an information-only task: you have no booking, confirmation number, account or name to "
                  "give and must never make one up. To a menu you may only say agent or representative, or press "
                  "one single key per menu for reservations, baggage, general information or an agent. Never say "
                  "yes. A menu that mentions a callback, a payment or a change gets nothing at all: reply WAIT. "
                  "The line refuses anything else.")
