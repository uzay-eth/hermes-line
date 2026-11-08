"""Validated engine configuration (``plugins.entries.hermes-line.settings``).

Safety policy lives in ``policy`` and is validated by ``line.policy.Settings``;
this module holds the wiring: URLs, numbers and timings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Optional
from urllib.parse import urlsplit

DEFAULT_DISCLOSURE = ("Hi, this is Hermes, an AI assistant calling on behalf of {owner}. "
                      "This call is not recorded.")
CONFIRM_QUESTION = "Hi, this is Hermes, an AI assistant calling for {owner}. Am I speaking with a live person?"


@dataclass
class EngineConfig:
    enabled: bool = False                 # start the call service in this process
    public_base_url: str = ""             # https origin Twilio reaches (tunnel)
    listen_host: str = "127.0.0.1"
    listen_port: int = 8788
    line_key: str = "default"
    from_number: str = ""                 # Twilio-verified caller ID (E.164)
    line_number: str = ""                 # Twilio number that receives callbacks (E.164)
    owner_number: str = ""                # owner's own phone for bridging (E.164)
    owner_name: str = "the owner"
    disclosure_text: str = DEFAULT_DISCLOSURE
    ring_timeout_s: int = 30
    owner_ring_timeout_s: int = 25
    max_call_minutes: int = 90
    callback_window_hours: int = 72
    relay_token_ttl_s: int = 120
    relay_reconnect_grace_s: float = 20.0
    relay_max_reconnects: int = 2
    confirm_cooldown_s: float = 45.0      # unprompted re-asks of the live-person question (recordings)
    ask_timeout_s: float = 20.0
    reply_budget_s: float = 2.5           # round 7: most silence a person may hear before Hermes speaks
    quiet_before_speak_s: float = 1.2     # round 30: before a fixed pre-human line, this much business quiet
    confirm_reply_window_s: float = 15.0  # a reply this soon after the live-person question always gets an answer
    summary_timeout_s: float = 10.0       # unused since round 37 (summaries are engine-only); kept for configs
    call_profile: str = ""                # round 8: Hermes profile the call sessions run in (toolsets: call tools only)
    forwarding_number: str = ""           # round 7: the owner's line (e.g. Fi) that forwards calls to line_number
    inbound_ring_owner: bool = True       # an unknown inbound caller may ask for the owner (owner presses 1)
    inbound_screening_hold_s: float = 6.0  # round 16 (a): with forwarding_number, hold the greeting this long
    owner_live_bridge: bool = False       # round 17: the PIN-verified owner stays on the line; joined live
    turn_timeout_s: float = 60.0
    queue_max: int = 64
    speech_chars_per_s: float = 14.0      # used to let a goodbye finish before hanging up
    ingress_auth: str = "auto"            # "auto" (signatures if the Auth Token is set) or "signature"
    sms_source: str = "adb"               # "adb" (phone over USB or Wi-Fi wireless debugging) or "none"
    sms_adb_serial: str = ""              # USB serial, wireless-debugging mDNS name or ip:port; "" = the one USB phone
    policy: dict = field(default_factory=dict)
    voice: dict = field(default_factory=dict)  # line.voice.VoiceSettings (L5): transport, model, timings

    @property
    def otp_browser_fill(self) -> bool:
        """Round 8 (catalog G3): always off; kept read-only for older callers (the trial harness)."""
        return False

    @classmethod
    def load(cls, data: Optional[dict]) -> "EngineConfig":
        data = dict(data or {})
        # Round 8 (catalog G3): typing codes into web pages is not part of this build. An old ``false`` is
        # accepted and ignored; ``true`` is refused instead of silently doing nothing.
        if data.pop("otp_browser_fill", False) is not False:
            raise ValueError("otp_browser_fill: typing SMS codes into web pages is not part of this build; "
                             "approved codes go only into a live call (line_otp_to_call)")
        known = {f.name: f for f in fields(cls)}
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise ValueError(f"unknown hermes-line settings: {', '.join(unknown)}")
        cfg = cls()
        for key, value in data.items():
            default = getattr(cfg, key)
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, int):
                ok = isinstance(value, int) and not isinstance(value, bool)
            elif isinstance(default, float):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = float(value) if ok else value
            elif isinstance(default, dict):
                ok = isinstance(value, dict)
            else:
                ok = isinstance(value, str)
            if not ok:
                raise ValueError(f"setting {key} has the wrong type")
            setattr(cfg, key, value)
        if cfg.public_base_url:
            parts = urlsplit(cfg.public_base_url)
            if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
                raise ValueError("public_base_url must be an https origin")
            cfg.public_base_url = cfg.public_base_url.rstrip("/")
        for key in ("from_number", "line_number", "owner_number", "forwarding_number"):
            value = getattr(cfg, key)
            if value and not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
                raise ValueError(f"{key} must be canonical E.164")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", cfg.line_key):
            raise ValueError("line_key must be short and alphanumeric")
        if not (1 <= cfg.max_call_minutes <= 240):
            raise ValueError("max_call_minutes must be 1-240")
        if not (1 <= cfg.queue_max <= 1024):
            raise ValueError("queue_max must be 1-1024")
        try:  # round 8: the safety policy (allowed_destinations, blocked_destinations, ...) fails at config time
            from ..policy import Settings as PolicySettings
            PolicySettings.load(dict(cfg.policy))
        except ImportError:  # pragma: no cover - policy package absent
            pass
        except (TypeError, ValueError) as exc:
            raise ValueError(f"policy: {exc}") from None
        try:
            from ..voice import VoiceSettings
            VoiceSettings.load(cfg.voice)
        except ImportError:  # pragma: no cover - voice package absent
            pass
        if cfg.ingress_auth not in ("auto", "signature"):
            raise ValueError("ingress_auth must be auto or signature")
        if cfg.sms_source not in ("adb", "none"):
            raise ValueError("sms_source must be adb or none")
        if cfg.call_profile and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", cfg.call_profile):
            raise ValueError("call_profile must be a Hermes profile name (lowercase letters, digits, - and _)")
        if not (0.0 <= cfg.quiet_before_speak_s <= 5.0):
            raise ValueError("quiet_before_speak_s must be 0-5")
        if not (0.0 <= cfg.inbound_screening_hold_s <= 10.0):
            raise ValueError("inbound_screening_hold_s must be 0-10")
        if not (0.5 <= cfg.reply_budget_s <= 3.0):
            raise ValueError("reply_budget_s must be 0.5-3.0 (a live person never waits longer)")
        if not (1.0 <= cfg.summary_timeout_s <= 30.0):
            raise ValueError("summary_timeout_s must be 1-30")
        if not (1 <= len(cfg.owner_name) <= 60):
            raise ValueError("owner_name must be 1-60 characters")
        return cfg

    def missing_for_live(self) -> list[str]:
        """Settings a live (Twilio) service cannot run without."""
        return [k for k in ("public_base_url", "from_number") if not getattr(self, k)]

    def disclosure(self) -> str:
        return self.disclosure_text.replace("{owner}", self.owner_name)

    def confirm_question(self) -> str:
        return CONFIRM_QUESTION.replace("{owner}", self.owner_name)
