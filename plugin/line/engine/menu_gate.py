"""The final output gate for phone-menu actions of restricted tasks (R1 delta 13 N16; smaller in round 19 after R1
delta 15; whole-menu scope and positive forms in round 20 after R1b delta 17 N16-D, N16-E).

The gate does not try to understand a menu. It allows very little, and refuses everything else:

1. **Whole-menu scope.** The CURRENT menu text is normalised as one string (lower case, every punctuation mark and
   line break a single space, so a split expression such as "hang. Up." rejoins). If any blocked word or phrase, any
   phrase pointing back at an option ("this option", "the same key"), or any callback offer appears anywhere in it,
   nothing at all is sent for that menu, not even "agent": Hermes stays silent and waits for the next menu.
   Blocked words match whole words only: "reserve" blocks, the safe topic "reservations" does not.
2. **Nothing around an option.** Punctuation still marks where an option ends. A fragment after an option that is
   neither another option nor the lead-in of the next one ("Press 1 for baggage; authorize ...", "... press 1. The
   same key ...") makes the whole menu unusable. Text before the first option is ignored only when EVERY leading
   sentence is exactly a known harmless intro (round 21, R1b delta 19 N16-F; round 22, R1b delta 20 N16-G):
   "welcome to <name>", "thank you for calling" (with or without "<name>"), "your call is important to us", "please
   listen carefully", "our menu options have changed", "calls may be monitored or recorded", "para espanol oprima N"
   (ignored, and never pressed: "oprima" is not a key verb). A <name> is not free text: it must be exactly one of
   the task's approved business names (the ``intro_names`` task option, default none), the whole remainder of its
   sentence. Any other leading sentence, or anything else in an intro sentence, silences the menu. Sentences end at
   . ; ! ? or a line break.
4. **No amounts** (round 22, N16-H): any token containing a digit, other than the key right after press/key/dial/
   select/enter/push/say, silences the menu (35USD, USD35, 35p, 35c, $35, 1.50), as does a number word that is not
   such a key ("thirty five", "ten").
3. **Positive forms only.** An option qualifies only as "for <safe topic>", "to speak / talk to / with an agent or
   representative", or "<safe topic>, press N" (also "press N for <safe topic>"), where the safe topic is exactly
   reservations, baggage, general information, agent or representative, with an optional "a/an/the" and nothing
   else: any other word in the option ("to buy", "to bill", "to put down", "allowance") disqualifies it. Every
   announcement of the key or word in the menu must qualify.

**Information-only tasks** (the airline hold test) send only the spoken words "agent" or "representative" (if the
menu announces the word, every announcement must qualify; if it does not mention it, it is a generic request) or ONE
single key per menu from a qualifying option. Never "yes", no sequences. **Other restricted tasks** also may press
one key per menu from a qualifying option, a sequence only when a qualifying option announces exactly that sequence,
and say a word a qualifying option announces. A refusal sends nothing and logs ``restricted_action_blocked``.

A literal ``*`` or ``#`` is the star or pound key. Deterministic patterns only; fail closed. A candidate Jev
decision point.
"""
from __future__ import annotations

import re
from typing import Optional

BLOCKED = re.compile(
    r"\b(?:call|calls|called|calling|callback|callbacks|call back|phone call|later|place in line|hang up|hangs up|"
    r"hanging up|text|texts|texting|text message|charge|charges|charged|charging|pay|pays|paid|paying|payment|"
    r"payments|card|cards|authorize|authorise|authorizes|authorises|authorized|authorised|authorizing|authorising|"
    r"authorization|authorisation|purchase|purchases|purchased|purchasing|upgrade|upgrades|upgraded|upgrading|fee|"
    r"fees|change|changes|changed|changing|rebook|rebooks|rebooked|rebooking|cancel|cancels|cancelled|canceled|"
    r"cancelling|canceling|cancellation|modify|modifies|modified|modification|confirm|confirms|confirmed|"
    r"confirmation|accept|accepts|accepted|yes|proceed|continue|agree|approve|submit|complete|verify|offer|offers|"
    # round 20 (R1b delta 17 N16-E): payment and commitment verbs, whole words
    r"buy|buys|buying|bought|bill|bills|billed|billing|deposit|deposits|deposited|sell|sells|selling|sold|order|"
    r"orders|ordered|ordering|renew|renews|renewed|renewal|renewing|subscribe|subscribes|subscribed|subscription|"
    r"transfer|transfers|transferred|transferring|reserve|reserves|reserved|reserving|"
    # round 21 (R1b delta 19 N16-F): money words ("$" is read as "dollars"; spoken amounts: see _amount)
    r"debit|debits|debited|deduct|deducts|deducted|dollar|dollars|cent|cents|balance|balances|account|accounts|"
    r"price|prices|cost|costs|"
    # round 22 (R1b delta 20 N16-H): currency and surcharge words
    r"usd|eur|gbp|euro|euros|pounds|pound sterling|pounds sterling|sterling|surcharge|surcharges|incur|incurs|"
    r"incurred|incurring|(?:fee|fees|charge|charges|surcharge|surcharges|cost|costs) (?:will |may )?"
    r"(?:apply|applies|applied)|"
    # round 23 (R1b delta 21 N16-I): callback paraphrases
    r"ring you back|ring you|ring back|ringback|we will call|we'll call|will call you|call you|keep your place|"
    r"hold your place|save your place|your place|your spot|keep your spot|get back to you|contact you|reach you|"
    r"return your call|phone you|phones you|be contacted|contacted|contacts you)\b")
REFERENT = re.compile(r"\b(?:this|that|same|above|previous|following) (?:option|key|selection|choice|number)\b|"
                      r"\bthe same\b")
_CATEGORY = (
    ("callback", re.compile(r"\b(?:call\w*|callback\w*|phone call|later|place in line|hang\w* up|text\w*|"
                            r"reserv(?:e|es|ed|ing)|ring you|ring back|ringback|your place|your spot|"
                            r"get back to you|contact you|reach you)\b")),
    ("payment", re.compile(r"\b(?:charg\w*|pa(?:y|ys|id|ying|yment|yments)|cards?|authori[sz]\w*|purchas\w*|"
                           r"upgrad\w*|fees?|buy\w*|bought|bill\w*|deposit\w*|sell\w*|sold|order\w*|renew\w*|"
                           r"subscri\w*|transfer\w*|debit\w*|deduct\w*|dollars?|cents?|balances?|accounts?|"
                           r"prices?|costs?|usd|eur|gbp|euros?|pounds|sterling|surcharges?|incur\w*)\b")),
    ("change", re.compile(r"\b(?:chang\w*|rebook\w*|cancel\w*|modif\w*)\b")),
)
SPOKEN_WORDS = ("agent", "representative")
_VERBS = {"press", "key", "dial", "select", "enter", "push"}
_KEYWORD = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
            "eight": "8", "nine": "9", "star": "*", "pound": "#", "hash": "#"}
_ARTICLES = {"a", "an", "the"}
_TOPICS = (("reservations",), ("baggage",), ("general", "information"), ("agent",), ("representative",))
_PUNCT = re.compile(r"[.,;:!?\n\r]+")
_SENTENCE_END = re.compile(r"[.;!?\n\r]")


def _tokens(text: str) -> list:
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))  # "español" -> "espanol"
    text = re.sub(r"[$\u00a3\u20ac]", " dollars ", text)  # round 21: "$35" is money
    return re.sub(r"[^a-z0-9*#' ]+", " ", text.lower()).split()


_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                 "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
                 "thousand", "million", "half", "quarter"}


def _amount(tokens: list) -> Optional[str]:
    """Rounds 21-22: a spoken or written amount. Only the key right after press/key/dial/select/enter/push (or the
    reply right after "say") may be a number, and then only digits or a key word. Any other token containing a digit
    (35, 35usd, usd35, 35p, 35c, the 50 of 1.50) or any other number word ("thirty five", "ten") is an amount."""
    for i, tok in enumerate(tokens):
        key_place = i > 0 and (tokens[i - 1] in _VERBS or tokens[i - 1] == "say")
        if any(c.isdigit() for c in tok):
            if not (key_place and tok.isdigit()):
                return tok
        elif tok in _NUMBER_WORDS and not key_place:
            return tok
    return None


_FIXED_INTROS = (("your", "call", "is", "important", "to", "us"), ("please", "listen", "carefully"),
                 ("our", "menu", "options", "have", "changed"), ("calls", "may", "be", "monitored", "or", "recorded"))
_SPANISH_KEYS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "zero", "one", "two", "three", "four", "five",
                 "six", "seven", "eight", "nine"}


def normalise_names(names) -> tuple:
    """The approved intro names as token tuples ("Delta Air Lines" -> ("delta", "air", "lines"))."""
    return tuple(t for t in (tuple(_tokens(str(n))) for n in names or ()) if t)


_HOLD_LINES = (("please", "hold"), ("please", "stay", "on", "the", "line"), ("please", "continue", "to", "hold"),
               ("all", "agents", "are", "busy"), ("all", "agents", "are", "currently", "busy"),
               ("all", "representatives", "are", "busy"), ("all", "of", "our", "agents", "are", "busy"),
               ("your", "call", "will", "be", "answered", "in", "the", "order", "received"),
               ("your", "call", "will", "be", "answered", "in", "the", "order", "it", "was", "received"),
               ("thank", "you", "for", "holding"), ("thank", "you", "for", "your", "patience"))
_MINUTES = {"minute", "minutes"}


def _wait_line(tokens: list) -> int:
    """Round 23: "(the) estimated wait time is (about) <N> minute(s)" -> its length in tokens, else 0. N is a number
    in digits or one or two number words; this is the only place a number may appear outside a key."""
    i = 1 if tokens[:1] == ["the"] else 0
    if tokens[i:i + 4] != ["estimated", "wait", "time", "is"]:
        return 0
    i += 4
    if i < len(tokens) and tokens[i] in ("about", "approximately"):
        i += 1
    n = 0
    while i + n < len(tokens) and n < 2 and (tokens[i + n].isdigit() or tokens[i + n] in _NUMBER_WORDS):
        n += 1
    if n == 0 or i + n >= len(tokens) or tokens[i + n] not in _MINUTES:
        return 0
    return i + n + 1


def _intro_sentence(tokens: list, names: tuple) -> bool:
    """True if one sentence is exactly a sequence of known harmless intros and hold lines (round 23). A name must be
    one of ``names``."""
    if not tokens:
        return True
    for line in _HOLD_LINES:
        if tuple(tokens[:len(line)]) == line and _intro_sentence(tokens[len(line):], names):
            return True
    wait = _wait_line(tokens)
    if wait and _intro_sentence(tokens[wait:], names):
        return True
    for fixed in _FIXED_INTROS:
        if tuple(tokens[:len(fixed)]) == fixed and _intro_sentence(tokens[len(fixed):], names):
            return True
    if (tokens[:3] == ["para", "espanol", "oprima"] and len(tokens) > 3 and tokens[3] in _SPANISH_KEYS
            and _intro_sentence(tokens[4:], names)):
        return True  # ignored, never pressed
    for head, name_needed in ((("welcome", "to"), True), (("thank", "you", "for", "calling"), False)):
        if tuple(tokens[:len(head)]) != head:
            continue
        rest = tokens[len(head):]
        if not name_needed and _intro_sentence(rest, names):
            return True  # "Thank you for calling."
        for name in names:
            if tuple(rest[:len(name)]) == name and _intro_sentence(rest[len(name):], names):
                return True
    return False


def _intro_ok(pieces: list, names: tuple) -> bool:
    """Every leading sentence (pieces joined up to a sentence end) is exactly a known harmless intro."""
    sentence: list = []
    for tokens, ends in pieces:
        sentence += tokens
        if ends:
            if not _intro_sentence(sentence, names):
                return False
            sentence = []
    return _intro_sentence(sentence, names)


def _pieces(menu: Optional[str]) -> list:
    """(tokens, ends a sentence) for every fragment between punctuation marks."""
    parts = re.split(r"([.,;:!?\n\r]+)", menu or "")
    out = []
    for k in range(0, len(parts), 2):
        tokens = _tokens(parts[k])
        delim = parts[k + 1] if k + 1 < len(parts) else "."
        if tokens:
            out.append((tokens, bool(_SENTENCE_END.search(delim))))
        elif out and _SENTENCE_END.search(delim):
            out[-1] = (out[-1][0], True)
    return out


def joined(menu: Optional[str]) -> str:
    """The whole current menu as one string: lower case, punctuation and line breaks as single spaces."""
    return " ".join(_tokens(_PUNCT.sub(" ", menu or "")))


def fragments(menu: Optional[str]) -> list:
    return [t for t in (_tokens(f) for f in _PUNCT.split(menu or "")) if t]


def _key(token: str) -> Optional[str]:
    if re.fullmatch(r"\d+|[*#]", token):
        return token
    return _KEYWORD.get(token)


def announcements(tokens: list) -> list:
    """(index, kind, value) for every "press/key/dial/select/enter/push <key>" and "say <word>" in a fragment."""
    out = []
    for i, tok in enumerate(tokens[:-1]):
        if tok in _VERBS and _key(tokens[i + 1]) is not None:
            out.append((i, "key", _key(tokens[i + 1])))
        elif tok == "say":
            out.append((i, "say", tokens[i + 1]))
    return out


def _is_topic(tokens: list, spoken_only: bool = False) -> bool:
    tokens = tokens[1:] if tokens[:1] and tokens[0] in _ARTICLES else tokens
    topics = (("agent",), ("representative",)) if spoken_only else _TOPICS
    return tuple(tokens) in topics


def _is_option(tokens: list) -> bool:
    """Exactly "for <topic>", "to speak/talk to/with <agent|representative>" or "<topic>"."""
    if tokens[:1] == ["for"]:
        return _is_topic(tokens[1:])
    if len(tokens) >= 4 and tokens[0] == "to" and tokens[1] in ("speak", "talk") and tokens[2] in ("to", "with"):
        return _is_topic(tokens[3:], spoken_only=True)
    return _is_topic(tokens)


def _is_trailing_option(tokens: list) -> bool:
    """After the key: exactly "for <topic>" or "to speak/talk to/with <agent|representative>"."""
    return bool(tokens) and tokens[0] in ("for", "to") and _is_option(tokens)


def _parse(menu: Optional[str], names: tuple = ()) -> Optional[tuple]:
    """(options as (lead-in or None, fragment, announcements), the tokens after the leading intro), or None when the
    menu has text around an option that is neither another option, its lead-in, nor (before the first option) a
    leading sentence that is exactly a known harmless intro."""
    out, pending, seen, after = [], [], False, []
    for frag, ends in _pieces(menu):
        found = announcements(frag)
        if not found:
            pending.append((frag, ends))
            continue
        lead = None
        if pending and len(found) == 1 and found[0][0] == 0 and len(frag) == 2:
            lead = pending.pop()[0]  # "For baggage, press 2": the lead-in names the option
        if pending:
            if seen:
                return None  # text between options that belongs to neither
            if not _intro_ok(pending, names):
                return None  # N16-F/G: only known harmless intro sentences may come first
            pending = []
        out.append((lead, frag, found))
        after += (lead or []) + frag
        seen = True
    if pending and seen:
        return None  # text after the last option
    if not seen:
        # Round 23 (N16-I): a menu with no recognised option is valid only if every sentence is a known harmless
        # intro or hold line; "no options parsed" is not evidence that the text is safe.
        return ([], []) if _intro_ok(pending, names) else None
    return out, after


def clauses(menu: Optional[str], names: tuple = ()) -> Optional[list]:
    """The menu's options as (lead-in tokens or None, fragment tokens, announcements), or None (see ``_parse``)."""
    parsed = _parse(menu, names)
    return parsed[0] if parsed is not None else None


def checked_tokens(menu: Optional[str], names: tuple = ()) -> list:
    """What the whole-menu checks read: the menu without its leading intro and hold sentences (each exactly known).
    A menu that does not parse is read whole."""
    parsed = _parse(menu, names)
    if parsed is None:
        return _tokens(_PUNCT.sub(" ", menu or ""))
    return parsed[1]


def positive(lead: Optional[list], frag: list, found: list) -> bool:
    """The option is in one of the positive forms, with nothing else in it."""
    if len(found) != 1:
        return False
    i = found[0][0]
    head, tail = frag[:i], frag[i + 2:]
    if lead is not None:
        return not head and not tail and _is_option(lead)
    if not head and not tail:
        return found[0][1] == "say" and found[0][2] in SPOKEN_WORDS  # a bare "say agent"
    if not head:
        return _is_trailing_option(tail)  # "press 1 for baggage"
    return not tail and _is_option(head)  # "for baggage press 1"


def categories(text: str) -> list:
    return [name for name, rule in _CATEGORY if rule.search(text)] or ["acceptance"]


def menu_problem(menu: Optional[str], callback_offer: bool, names: tuple = ()) -> Optional[str]:
    """Why nothing at all may be sent for this menu, or None."""
    if callback_offer:
        return "this menu is a callback offer: nothing is sent for it"
    tokens = checked_tokens(menu, names)  # an unparseable menu is read whole
    text = " ".join(tokens)
    words = list(dict.fromkeys(m.group(0) for m in BLOCKED.finditer(text)))
    if words:
        kinds = list(dict.fromkeys(c for w in words for c in categories(w)))
        quoted = ", ".join(f"\"{w}\"" for w in words)
        return f"the menu has the blocked word(s) {quoted} ({', '.join(kinds)}): nothing is sent for it"
    ref = REFERENT.search(text)
    if ref:
        return f"the menu refers back to an option (\"{ref.group(0)}\"): nothing is sent for it"
    amount = _amount(tokens)
    if amount:
        return f"the menu mentions an amount (\"{amount}\", payment): nothing is sent for it"
    parsed = _parse(menu, names)
    if parsed is None:
        return ("the menu has text that is not a known harmless intro, hold line or option: nothing is sent "
                "for it")
    # Round 24 (R1b delta 22 N16-J): fully positive. Every option of the menu must itself be an allowed option
    # (positive form, allowed topic), not only the one being chosen; any other option silences the whole menu, for
    # keys and speech alike. The word lists above remain a second layer.
    for lead, frag, found in parsed[0]:
        if not positive(lead, frag, found):
            return (f"the menu has an option that is not an allowed one (\"{' '.join((lead or []) + frag)}\"): "
                    "nothing is sent for it")
    return None


CALLBACK_WORDING = re.compile(
    r"\b(?:call\w*|callback\w*|phone call|later|ring you|ring back|ringback|keep your place|hold your place|"
    r"save your place|your place|your spot|get back to you|contact you|reach you|return your call|phone you|"
    r"be contacted|contacted)\b")


def is_callback_offer(menu: Optional[str], detector_says: bool, names: tuple = ()) -> bool:
    """L2's evidence, or callback wording (round 23: with paraphrases such as "ring you back", "we will call you",
    "keep your place") outside a known intro ("thank you for calling ..." is not an offer)."""
    return bool(detector_says) or bool(CALLBACK_WORDING.search(" ".join(checked_tokens(menu, names))))


def _announcing(menu: Optional[str], kind: str, value: str, names: tuple = ()) -> list:
    return [(lead, frag, found) for lead, frag, found in clauses(menu, names) or []
            if any(k == kind and v == value for _, k, v in found)]


def check_keys(digits: str, menu: Optional[str], callback_offer: bool, *, info_only: bool,
               intro_names=()) -> Optional[str]:
    """None if ``digits`` may be sent to this menu (a restricted task), else why not. Whole-menu silence first."""
    names = normalise_names(intro_names)
    problem = menu_problem(menu, callback_offer, names)
    if problem:
        return problem
    keys = "".join(d for d in digits or "" if d != "w")
    if not keys:
        return "no key to press"
    if info_only and len(keys) != 1:
        return "an information-only task presses one single key per menu, never a sequence"
    found = _announcing(menu, "key", keys, names)
    if not found:
        what = "key" if len(keys) == 1 else "exact key sequence"
        return f"the menu did not announce the {what} {keys} as an option"
    for lead, frag, anns in found:
        if not positive(lead, frag, anns):
            text = " ".join((lead or []) + frag)
            return f"key {keys}: \"{text}\" is not \"for\" reservations, baggage, general information or an agent"
    return None


def check_speech(phrase: str, menu: Optional[str], callback_offer: bool, *, info_only: bool,
                 intro_names=()) -> Optional[str]:
    """None if ``phrase`` may be said to this menu (a restricted task), else why not. Whole-menu silence first."""
    names = normalise_names(intro_names)
    problem = menu_problem(menu, callback_offer, names)
    if problem:
        return problem
    word = " ".join(_tokens(phrase))
    if not word:
        return "nothing to say"
    if word == "yes" or BLOCKED.search(word):
        return f"\"{word}\" is never said to a menu by a restricted task"
    found = _announcing(menu, "say", word, names) if " " not in word else []
    for lead, frag, anns in found:
        if not positive(lead, frag, anns):
            return f"saying \"{word}\": \"{' '.join((lead or []) + frag)}\" is not a positive option"
    if word in SPOKEN_WORDS:
        if not found:  # round 23: a generic request only when every option of the menu is an allowed one
            for lead, frag, anns in clauses(menu, names) or []:
                if not positive(lead, frag, anns):
                    return (f"a generic \"{word}\" needs every option of the menu to be an allowed one; "
                            f"\"{' '.join((lead or []) + frag)}\" is not")
        return None
    if info_only:
        return "an information-only task says only agent or representative"
    if not found:
        return f"the menu did not announce \"{word}\" as an option"
    return None


def key_options(menu: Optional[str]) -> dict:
    """Report only (logs, review fixtures): key -> the option texts announcing it. The gate does not use it."""
    return _report(menu, "key")


def say_options(menu: Optional[str]) -> dict:
    """Report only: spoken word -> the option texts announcing it."""
    return _report(menu, "say")


def _report(menu: Optional[str], kind: str) -> dict:
    out: dict = {}
    for lead, frag, found in clauses(menu) or []:
        for _, k, v in found:
            if k == kind:
                out.setdefault(v, []).append(" ".join((lead or []) + frag))
    return out


def validate_intro_names(names) -> list:
    """Round 22 (N16-G): the ``intro_names`` task option. At most 4 names, each 1-5 words of letters (and ' & -), no
    digit, no number word, no blocked word. Returns them stripped; ValueError with the reason otherwise."""
    if names is None:
        return []
    if isinstance(names, str) or not isinstance(names, (list, tuple)):
        raise ValueError("intro_names must be a list of business names")
    if len(names) > 4:
        raise ValueError("intro_names takes at most 4 names")
    out = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z'&\- ]{0,60}", name.strip()):
            raise ValueError("an intro name is letters only (no digits or symbols)")
        tokens = _tokens(name)
        if not 1 <= len(tokens) <= 5:
            raise ValueError("an intro name is 1-5 words")
        if BLOCKED.search(" ".join(tokens)) or any(t in _NUMBER_WORDS for t in tokens):
            raise ValueError(f"the intro name \"{name.strip()}\" contains a blocked word")
        out.append(" ".join(name.split()))
    return out
