"""OTP extraction: pull candidate verification codes out of an SMS body.

Design: deterministic and explainable, no model. Precision beats recall here because a
wrong code only wastes a retry, but a code released for the wrong message is a leak.

Pipeline
1. Normalise (fullwidth/Arabic-Indic digits, NBSP, zero-width characters).
2. Find "forbidden" spans that are structurally not codes: phone numbers, dates, money,
   URLs, e-mail addresses. Any candidate overlapping one is dropped.
3. Find candidates: 4-8 digit runs, grouped digits ("482 915", "4829-1503"),
   letter-prefixed ("G-482915"), and uppercase letter+digit tokens ("X7K-9QP2").
4. Score each by shape plus context cues in the same sentence: verification words in
   many languages (+), order/reference/card-ending words and unit words (-).
5. Accept at ACCEPT_SCORE. If the top two accepted codes are close and different the
   message is `ambiguous` and no primary code is chosen: the caller must not release.

Known limits (documented, not hidden): codes made only of letters are not detected;
lower-case alphanumeric codes are not detected; a bare 4-8 digit number in a very short
message with no cue word is accepted (that is how many services send codes), so sender
and time-window matching (see matching.py) is the second gate, not an optional extra.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .secret import SecretText
from .textnorm import fold, normalize_text

ACCEPT_SCORE = 4.0
AMBIGUITY_MARGIN = 1.0
LONE_BODY_MAX = 100
# SMS bodies are attacker-controlled. OTP messages are short, so analysis stops at this many
# characters (a longer body yields no code) to keep worst-case time bounded.
MAX_ANALYSED_CHARS = 4000

# ------------------------------------------------------------------ cue vocabulary

_LATIN_POS = [
    "code", "kod", "código", "codigo", "codice", "kode", "verif", "vérif", "bestätig",
    "bestatig", "one-time", "one time", "onetime", "passcode", "password", "parola",
    "şifre", "sifre", "doğrulama", "dogrulama", "authenticat", "login", "log in",
    "sign in", "sign-in", "security", "güvenlik", "guvenlik", "sicherheits", "einmal",
    "contraseña", "contrasena", "clave", "mot de passe", "mfa",
]
_WORD_POS = ["pin", "otp", "2fa", "tan"]
_CJK_POS = [
    "验证码", "驗證碼", "校验码", "校驗碼", "动态码", "動態碼", "认证码", "認證碼", "验证", "驗證",
    "码", "碼", "コード", "認証", "ワンタイム", "인증", "확인번호", "код", "пароль", "подтвержд",
    "رمز", "كود", "التحقق", "קוד", "รหัส", "mã xác",
]
_LATIN_NEG = [
    "order", "ref", "invoice", "tracking", "track", "ticket", "case", "account", "acct",
    "ending in", "ending", "last 4", "last four", "flight", "zip", "postal", "apt", "suite",
    "sipariş", "siparis", "takip", "fatura", "pedido", "seguimiento", "bestellung",
    "rechnung", "commande", "facture",
]
_CJK_NEG = ["订单", "訂單", "单号", "單號", "注文", "주문", "заказ"]

_PROMO = ["promo", "coupon", "discount", "voucher", "sale", "save ", "% off", "kupon", "indirim", "descuento", "rabatt", "优惠", "折扣", "クーポン"]
_STRONG = ["verification", "verify", "verif", "otp", "one-time", "one time", "onetime", "security code", "sign in", "sign-in",
           "log in", "login", "passcode", "2fa", "doğrulama", "dogrulama", "bestätig", "验证码", "驗證碼", "認証", "인증", "код подтверждения", "رمز التحقق"]

_NOT_LETTER_BEFORE = r"(?<![^\W\d_])"
_NOT_LETTER_AFTER = r"(?![^\W\d_])"


PROMO_RE = None  # set below, after _alt is defined
STRONG_RE = None


def _alt(words: List[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


POS_RE = re.compile(
    _NOT_LETTER_BEFORE + "(?:" + _alt(_LATIN_POS) + ")"
    + "|" + _NOT_LETTER_BEFORE + "(?:" + _alt(_WORD_POS) + ")" + _NOT_LETTER_AFTER
    + "|" + _alt(_CJK_POS)
)
PROMO_RE = re.compile(_alt(_PROMO))
STRONG_RE = re.compile(_alt(_STRONG))
NEG_RE = re.compile(
    _NOT_LETTER_BEFORE + "(?:" + _alt(_LATIN_NEG) + ")" + "|" + _alt(_CJK_NEG)
)
UNIT_AFTER_RE = re.compile(
    r"^ ?(?:%|(?:min|mins|minute|minutes|dk|dakika|hour|hours|hrs|saat|sec|secs|seconds|sn|saniye"
    r"|days?|gün|pts|points|miles|km|kg|years|usd|eur|gbp|tl|try|dollars?|euros?)(?![^\W\d_])"
    r"|分钟|分鐘|分|時間|小时|秒|분|시간|₺)"
)
_SENTENCE_END = re.compile(r"[.!?。！？](?:\s|$)|\n")

# ------------------------------------------------------------------ forbidden spans

_FORBIDDEN = [
    # phone numbers with separators, national and international
    re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .\-])?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?!\d)"),
    re.compile(r"(?<![\w])\+\d[\d ()\-]{7,}\d"),
    re.compile(r"(?<!\d)0\d{3} \d{3} \d{2} \d{2}(?!\d)"),
    # dates
    re.compile(r"(?<!\d)\d{4}[\-/.]\d{1,2}[\-/.]\d{1,2}(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}(?!\d)"),
    # money
    re.compile(r"[$€£¥₺₹] ?\d[\d.,]*"),
    re.compile(r"(?<!\d)\d[\d.,]* ?(?:usd|eur|gbp|tl|try|dollars?|euros?)(?![^\W\d_])", re.I),
    # URLs and e-mail addresses
    re.compile(r"(?:https?://|www\.)\S+", re.I),
    # anchored at a token start and free of @ in the classes: linear on crafted input
    re.compile(r"(?<![^\s@])[^\s@]+@[^\s@]+\.[^\s@]+"),
]

# ------------------------------------------------------------------ candidate shapes

_RUN = re.compile(r"(?<![0-9A-Za-z])\d{4,8}(?![0-9A-Za-z])")
_GROUPED = [
    re.compile(r"(?<![0-9A-Za-z])(\d{3})[ \-](\d{3})(?![0-9A-Za-z])"),
    re.compile(r"(?<![0-9A-Za-z])(\d{4})[ \-](\d{4})(?![0-9A-Za-z])"),
    re.compile(r"(?<![0-9A-Za-z])(\d{2})[ \-](\d{2})[ \-](\d{2})(?![0-9A-Za-z])"),
]
_PREFIXED = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3})-(\d{4,8})(?![A-Za-z0-9])")
_ALNUM = re.compile(r"(?<![A-Za-z0-9_@/.\-])((?:[A-Z0-9]{2,5}-)?[A-Z0-9]{3,8})(?![A-Za-z0-9_@/\-])")

_BASE_BY_LEN = {4: 2.5, 5: 2.0, 6: 3.0, 7: 1.5, 8: 2.0}


@dataclass(frozen=True)
class OtpCandidate:
    value: SecretText            # canonical: no separators, no letter prefix on "G-482915"
    printed: SecretText          # as printed in the message
    kind: str                    # digits | grouped | prefixed | alnum
    span: Tuple[int, int]        # position in the normalised body
    score: float
    reasons: Tuple[str, ...] = field(default_factory=tuple)  # safe: no content

    @property
    def length(self) -> int:
        return len(self.value)


@dataclass(frozen=True)
class OtpResult:
    candidates: Tuple[OtpCandidate, ...]   # accepted, best first
    primary: Optional[OtpCandidate]        # None when empty or ambiguous
    ambiguous: bool

    @property
    def has_code(self) -> bool:
        return self.primary is not None


# ------------------------------------------------------------------ helpers

def _forbidden_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for rx in _FORBIDDEN:
        spans.extend((m.start(), m.end()) for m in rx.finditer(text))
    return spans


def _overlaps(s: int, e: int, spans: List[Tuple[int, int]]) -> bool:
    return any(s < b and a < e for a, b in spans)


def _structural_neighbour(text: str, s: int, e: int, grouped: bool) -> bool:
    """True if the digits are part of a bigger number: 1,234  1.2345  12:3456  5-1234."""
    n = len(text)
    # separator before: digit + [.,:/-] + our digits
    if s >= 2 and text[s - 1] in ".:/-" and text[s - 2].isdigit():
        return True
    if s >= 2 and text[s - 1] == "," and text[s - 2].isdigit() and _digits_after(text, s) == 3:
        return True   # "1,234": thousands grouping puts exactly three digits after the comma
    # separator after: our digits + [.,:/-] + digit
    if e + 1 < n and text[e] in ".:/-" and text[e + 1].isdigit():
        return True
    if e + 1 < n and text[e] == "," and text[e + 1].isdigit():
        if _digits_after(text, e + 1) == 3:
            return True
    if grouped:
        if s >= 2 and text[s - 1] in " -" and text[s - 2].isdigit():
            return True
        if e + 1 < n and text[e] in " -" and text[e + 1].isdigit():
            return True
    return False


def _digits_after(text: str, i: int) -> int:
    j = i
    while j < len(text) and text[j].isdigit():
        j += 1
    return j - i


def _sentence_start(folded: str, s: int, window: int = 60) -> int:
    lo = max(0, s - window)
    seg = folded[lo:s]
    last = 0
    for m in _SENTENCE_END.finditer(seg):
        last = m.end()
    return lo + last


def _nearest_cue_before(folded: str, s: int):
    """Polarity ("pos"/"neg"/None) of the cue governing the number at s.

    Only cues in the same sentence count. A negative cue ("card ending", "order") binds
    to the first number after it, so it does not poison a later number: in
    "OTP for card ending 4821 is 739104" the cue "ending" belongs to 4821 and 739104 is
    governed by "OTP". Positive cues reach over intervening numbers.
    """
    start = _sentence_start(folded, s)
    seg = folded[start:s]
    pos_end = -1
    for m in POS_RE.finditer(seg):
        pos_end = m.end()
    neg_end = -1
    for m in NEG_RE.finditer(seg):
        if not any(ch.isdigit() for ch in seg[m.end():]):
            neg_end = m.end()
    # "#12345" is a reference number, but the Retriever prefix "<#>" is not a marker
    tail = seg.rstrip(" ")
    if tail.endswith("#") and not tail.endswith("<#>"):
        neg_end = max(neg_end, len(tail))
    if neg_end > pos_end and neg_end >= 0:
        return "neg"
    if pos_end >= 0:
        return "pos"
    return None


def _cue_after(folded: str, e: int, window: int = 30) -> bool:
    seg = folded[e:e + window]
    m = _SENTENCE_END.search(seg)
    if m:
        seg = seg[: m.start()]
    return bool(POS_RE.search(seg))


def _is_date8(digits: str) -> bool:
    if len(digits) != 8:
        return False
    y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:])
    return 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31


def _score(kind, digits_len, norm, folded, s, e, value, lone, body_is_code, promo) -> Tuple[float, Tuple[str, ...]]:
    reasons: List[str] = []
    if kind == "alnum":
        score = 1.0
    else:
        score = _BASE_BY_LEN.get(digits_len, 1.0)
        if kind == "grouped":
            score -= 0.5
    pol = _nearest_cue_before(folded, s)
    after = _cue_after(folded, e)
    if pol == "pos":
        score += 4.0
        reasons.append("cue-before")
    elif pol == "neg":
        score -= 5.0
        reasons.append("neg-before")
    if after and pol != "pos":
        score += 3.0
        reasons.append("cue-after")
    if UNIT_AFTER_RE.search(folded[e:e + 16]):
        score -= 5.0
        reasons.append("unit-after")
    if kind in ("digits", "grouped") and digits_len == 4 and 1900 <= int(value) <= 2100 and pol != "pos" and not after:
        score -= 2.0
        reasons.append("year-like")
    if kind == "digits" and digits_len == 8 and _is_date8(value) and pol != "pos" and not after:
        score -= 3.0
        reasons.append("date-like")
    if promo:
        score -= 4.0
        reasons.append("promo-message")
    if body_is_code:
        score += 4.0
        reasons.append("code-only-body")
    elif lone and kind != "alnum":
        score += 1.0
        reasons.append("lone-number")
    return score, tuple(reasons)


# ------------------------------------------------------------------ public API

def extract_otp(body, accept_score: float = ACCEPT_SCORE) -> OtpResult:
    """Extract OTP candidates from an SMS body (str or SecretText)."""
    raw = body.reveal() if isinstance(body, SecretText) else (body or "")
    if len(raw) > MAX_ANALYSED_CHARS:
        return OtpResult((), None, False)
    norm = normalize_text(raw)
    folded = fold(norm)
    forbidden = _forbidden_spans(norm)

    raw_cands = []  # (kind, s, e, value, printed, digits_len)
    for m in _RUN.finditer(norm):
        raw_cands.append(("digits", m.start(), m.end(), m.group(), m.group(), len(m.group())))
    for rx in _GROUPED:
        for m in rx.finditer(norm):
            value = "".join(m.groups())
            raw_cands.append(("grouped", m.start(), m.end(), value, m.group(), len(value)))
    for m in _PREFIXED.finditer(norm):
        raw_cands.append(("prefixed", m.start(), m.end(), m.group(2), m.group(), len(m.group(2))))
    for m in _ALNUM.finditer(norm):
        tok = m.group(1)
        flat = tok.replace("-", "")
        if 5 <= len(flat) <= 8 and any(c.isdigit() for c in flat) and any(c.isalpha() for c in flat):
            raw_cands.append(("alnum", m.start(1), m.end(1), flat, tok, len(flat)))

    # a run inside a grouped match ("4829 1537") is part of it, not a code of its own
    grouped_spans = [(s, e) for kind, s, e, *_ in raw_cands if kind == "grouped"]
    raw_cands = [
        c for c in raw_cands
        if not (c[0] == "digits" and any(a <= c[1] and c[2] <= b for a, b in grouped_spans))
    ]

    # drop structurally impossible candidates
    kept = []
    for kind, s, e, value, printed, dl in raw_cands:
        if _overlaps(s, e, forbidden):
            continue
        if kind in ("digits", "grouped") and _structural_neighbour(norm, s, e, kind == "grouped"):
            continue
        if kind == "prefixed" and _structural_neighbour(norm, norm.index("-", s, e) + 1, e, False):
            continue
        kept.append((kind, s, e, value, printed, dl))

    # numeric spans count once even when a "digits" run sits inside a "prefixed" token
    numeric = _non_overlapping_spans(
        [(s, e) for kind, s, e, *_ in kept if kind in ("digits", "grouped", "prefixed")]
    )
    lone = len(numeric) == 1 and len(norm.strip()) <= LONE_BODY_MAX
    promo = bool(PROMO_RE.search(folded)) and not STRONG_RE.search(folded)
    stripped = norm.strip(" \t\r\n<>#.:-【】[]()")

    scored: List[OtpCandidate] = []
    for kind, s, e, value, printed, dl in kept:
        body_is_code = stripped == printed
        score, reasons = _score(kind, dl, norm, folded, s, e, value, lone, body_is_code, promo)
        if score >= accept_score:
            scored.append(OtpCandidate(SecretText(value), SecretText(printed), kind, (s, e), score, reasons))

    # greedy non-overlap: best score first, then longer span
    scored.sort(key=lambda c: (-c.score, -(c.span[1] - c.span[0]), c.span[0]))
    chosen: List[OtpCandidate] = []
    for c in scored:
        if not _overlaps(c.span[0], c.span[1], [x.span for x in chosen]):
            chosen.append(c)
    # same code printed twice ("Your code 482915 ... code 482915") is not ambiguity
    uniq: List[OtpCandidate] = []
    for c in chosen:
        if not any(c.value == u.value for u in uniq):
            uniq.append(c)

    ambiguous = len(uniq) >= 2 and (uniq[0].score - uniq[1].score) < AMBIGUITY_MARGIN
    primary = uniq[0] if uniq and not ambiguous else None
    return OtpResult(tuple(uniq), primary, ambiguous)


def _non_overlapping_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for a, b in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if not any(a < d and c < b for c, d in out):
            out.append((a, b))
    return out
