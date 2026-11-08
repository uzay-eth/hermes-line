"""SmsMessage, sender normalisation and time helpers."""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Union

from .masking import mask_sender, redact_all  # noqa: F401  (re-exported for callers)
from .secret import SecretText
from .textnorm import normalize_text


@dataclass(frozen=True, repr=False)
class SmsMessage:
    """One received SMS.

    `body` is a SecretText: repr/str/format print a placeholder. `date_ms` is the time
    the phone recorded for the message (ms since epoch, UTC). `source` names the
    SmsSource that produced it ("adb", "gateway", "fixture").
    """

    id: str
    address: str
    date_ms: int
    body: SecretText
    source: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.body, SecretText):
            object.__setattr__(self, "body", SecretText(self.body))
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "date_ms", int(self.date_ms))

    def __repr__(self) -> str:
        # The generated repr would print the raw sender; use the masked summary instead.
        return "SmsMessage(%s)" % self.log_summary()

    def log_summary(self) -> str:
        """The only description of a message that belongs in a log: no body text."""
        return "sms id=%s from=%s at=%s len=%d src=%s" % (
            self.id,
            mask_sender(self.address),
            iso_utc(self.date_ms),
            len(self.body),
            self.source,
        )


def iso_utc(ms: int) -> str:
    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_ms(value: Union[str, int, float]) -> int:
    """ISO-8601 (with Z or offset) or epoch seconds/ms -> epoch ms."""
    if isinstance(value, (int, float)):
        v = int(value)
        return v if v > 10_000_000_000 else v * 1000
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    # Python 3.9's fromisoformat rejects fractional seconds that are not 3 or 6 digits.
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s)
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return int(d.timestamp() * 1000)


# ---------------------------------------------------------------- sender matching

_SENDER_OK = re.compile(r"^[A-Za-z0-9+._ \-]{1,32}$")


def is_safe_address(address: str) -> bool:
    """Addresses are interpolated into an on-device SQL filter; accept a tight alphabet."""
    return bool(_SENDER_OK.match(address or ""))


def normalize_sender(address: str, default_cc: str = "1") -> str:
    """Canonical form for comparing senders.

    * phone numbers   -> "+<digits>" (10 digits get `default_cc`; 11 starting with it too)
    * short codes     -> digits only, 8 or fewer
    * alphanumeric IDs-> case-folded, whitespace removed ("Example Bank" == "examplebank")
    """
    a = normalize_text(address or "").strip()
    if not a:
        return ""
    compact = re.sub(r"[\s().\-]", "", a)
    if re.fullmatch(r"\+?\d+", compact):
        digits = compact.lstrip("+")
        if compact.startswith("+"):
            return "+" + digits
        if len(digits) <= 8:
            return digits
        if len(digits) == 10:
            return "+" + default_cc + digits
        return "+" + digits
    return re.sub(r"\s", "", a).casefold()
