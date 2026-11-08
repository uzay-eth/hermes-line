"""Task restrictions the call tools enforce (round 16), not only the briefing text.

A task's guidance ("decline any callback offer", "do not make a payment", "do not change the flight") becomes a
per-task deny list. The in-call tools check it:

* ``press_digits`` refuses a key the current phone menu attaches to a forbidden option (callback, payment). When L2
  labelled the menu a callback offer and the task forbids callbacks, a key that cannot be told apart from the
  callback option is refused too (fail closed; the stay-on-the-line option is allowed when the menu names it).
* ``request_finish`` cannot report ``done`` with a summary that claims a forbidden result (a change made, a payment).

Reaching an agent through a menu option named "change a flight" stays allowed: the task may need that department,
it only must not *make* the change. The parsing is deterministic and narrow; it is a candidate Jev decision point.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

CALLBACK, PAYMENT, CHANGE = "callback", "payment", "change"

_NEG = r"(?:decline|refuse|reject|do not|don't|dont|never|no|not|without|avoid)"
_RULES = {
    CALLBACK: re.compile(_NEG + r"\b[^.;\n]{0,40}\b(?:call ?backs?|call you back)\b", re.I),
    PAYMENT: re.compile(_NEG + r"\b[^.;\n]{0,40}\b(?:pay|payments?|paying|charges?|card|fees?|authori[sz]e|"
                       r"authori[sz]ation|purchase|purchases|buy|upgrade|upgrades|fee payment)\b", re.I),
    CHANGE: re.compile(_NEG + r"\b[^.;\n]{0,40}\b(?:change|changes|changing|rebook|rebooking|modify|switch|cancel|"
                      r"cancels|cancellation)\b", re.I),
}


def restrictions_from(goal: str) -> frozenset:
    """The deny list a task's guidance states (``Restrictions: no callback, no payment, no change`` also works)."""
    text = " ".join((goal or "").split())  # guidance is often wrapped over lines
    return frozenset(name for name, rule in _RULES.items() if rule.search(text))


_OPTION_A = re.compile(r"(?:press|key|dial|select)\s+(\d|star|pound)\s*(?:to|for|if)\s+([^.;,]{2,60})", re.I)
_OPTION_B = re.compile(r"(?:to|for|if)\s+([^.;,]{2,60}?)[,\s]+(?:please\s+)?(?:press|key|dial|select)\s+(\d|star|pound)",
                       re.I)
_KEYS = {"star": "*", "pound": "#"}
_CATEGORY = {
    CALLBACK: re.compile(r"\b(?:call ?back|call you back|return call)\b", re.I),
    PAYMENT: re.compile(r"\b(?:pay|payment|billing|card)\b", re.I),
}
_STAY = re.compile(r"\b(?:stay on the line|remain on the line|continue (?:to )?hold|keep (?:your place|holding)|"
                   r"decline|no thanks)\b", re.I)


def menu_options(menu: str) -> list:
    """(key, description) pairs announced by a menu, in order."""
    out = []
    for m in _OPTION_A.finditer(menu or ""):
        out.append((_KEYS.get(m.group(1).lower(), m.group(1)), m.group(2).strip()))
    for m in _OPTION_B.finditer(menu or ""):
        out.append((_KEYS.get(m.group(2).lower(), m.group(2)), m.group(1).strip()))
    return out


def blocked_keys(menu: str, restrictions: Iterable[str]) -> dict:
    """key -> restriction for every announced option the task forbids."""
    blocked = {}
    for key, desc in menu_options(menu):
        for name, rule in _CATEGORY.items():
            if name in restrictions and rule.search(desc) and not _STAY.search(desc):
                blocked[key] = name
    return blocked


def check_press(digits: str, menu: Optional[str], restrictions: Iterable[str], callback_offer: bool) -> Optional[str]:
    """None if the keys may be pressed, else the reason they may not."""
    restrictions = set(restrictions)
    if not restrictions or not menu:
        return None
    keys = [d for d in digits if d != "w"]
    blocked = blocked_keys(menu, restrictions)
    for k in keys:
        if k in blocked:
            return f"this task forbids the {blocked[k]} option this key selects"
    if callback_offer and CALLBACK in restrictions:
        stay = {key for key, desc in menu_options(menu) if _STAY.search(desc)}
        if not keys or not all(k in stay for k in keys):
            return ("this is a callback offer and this task declines callbacks: press only an announced "
                    "stay-on-the-line option, or wait")
    return None


_CLAIMS = {
    CHANGE: re.compile(r"\b(?:changed|rebooked|moved (?:you|the|your)|switched|new flight|change (?:is|was) "
                       r"(?:made|done|confirmed)|confirmed the change)\b", re.I),
    PAYMENT: re.compile(r"\b(?:paid|charged|payment (?:was|is) (?:made|taken|processed)|processed (?:the|a) payment)\b",
                        re.I),
    CALLBACK: re.compile(r"\b(?:accepted (?:a|the) call ?back|scheduled (?:a|the) call ?back|they will call back)\b",
                         re.I),
}


def check_finish(outcome: str, summary: str, restrictions: Iterable[str]) -> Optional[str]:
    """None if the outcome may be reported, else why not (a forbidden result reported as done)."""
    if outcome != "done":
        return None
    for name in restrictions:
        rule = _CLAIMS.get(name)
        if rule and rule.search(summary or ""):
            return (f"this task forbids a {name}; it cannot be reported as done. Report outcome failed or "
                    "callback_expected and say what was offered")
    return None


def describe(restrictions: Iterable[str]) -> str:
    names = {CALLBACK: "accepting a callback", PAYMENT: "making or agreeing to a payment",
             CHANGE: "making a change to the booking (asking about options is fine)"}
    items = [names[r] for r in (CALLBACK, PAYMENT, CHANGE) if r in set(restrictions)]
    return ("This task forbids: " + "; ".join(items) + ". The phone line enforces this: forbidden menu keys and "
            "forbidden outcomes are refused.") if items else ""
