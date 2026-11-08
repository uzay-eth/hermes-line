"""line.sms: read SMS verification codes for an owner-approved task, masked everywhere else.

Lane L4 of Hermes Line (docs/CONTRACT.md). Standard library only, Python 3.9+ (Hermes ships 3.11+).

Public surface:
    sources        AdbSource, GatewaySource, FixtureSource (SmsSource interface)
    OtpMatcher     sender AND window AND one unambiguous code AND single use
    SecretText     a string that refuses to print itself
    release_otp    the contract entry point (service.py): SecretText or None, single-use consent
    OtpDelivery    how a released code reaches its destination (delivery.py): sealed fill or sealed call
    hooks.register wires the tool(s) and the scrubbing hooks onto a Hermes PluginContext (hooks.py)
"""
from .log import LOG
from .masking import (
    MaskingFilter,
    install_log_masking,
    mask_codes,
    mask_for_transcript,
    mask_handler,
    mask_sender,
    redact_all,
)
from .matching import ApprovedTask, Decision, MatchOutcome, OtpMatcher, wait_for_code
from .model import SmsMessage, normalize_sender
from .otp import OtpCandidate, OtpResult, extract_otp
from .secret import SecretText
from .sources import AdbSource, FixtureSource, GatewaySource, SmsSource
from .consent import OTP_ACTION, ConsentStoreLike
from .destination import Destination, normalize_origin
from .released import MemoryReleasedStore, ReleasedStore, default_store
from .service import (
    OtpService,
    OtpTaskRegistry,
    ReleaseResult,
    ReleaseStatus,
    approval_prompt,
    binding_for,
    configure,
    get_service,
    release_otp,
)
from .delivery import (
    CALL_TRANSCRIPT_MARKER,
    BrowserOriginProbe,
    BrowserTypeSink,
    CallSink,
    OtpDelivery,
    Sink,
)
from .scrub import ValueScrubber, register_released, scrub_text

# Defence in depth: whatever any line.sms module logs is masked before a handler sees it.
install_log_masking()

__all__ = [
    "BrowserOriginProbe", "BrowserTypeSink", "CALL_TRANSCRIPT_MARKER", "CallSink", "ConsentStoreLike",
    "Destination", "MemoryReleasedStore", "OTP_ACTION", "OtpDelivery", "OtpService", "OtpTaskRegistry",
    "ReleaseResult", "ReleaseStatus", "ReleasedStore", "Sink", "ValueScrubber", "approval_prompt",
    "binding_for", "default_store", "normalize_origin", "configure", "get_service", "register_released", "release_otp", "scrub_text",
    "AdbSource", "ApprovedTask", "Decision", "FixtureSource", "GatewaySource", "LOG", "MaskingFilter",
    "MatchOutcome", "OtpCandidate", "OtpMatcher", "OtpResult", "SecretText", "SmsMessage", "SmsSource",
    "extract_otp", "install_log_masking", "mask_codes", "mask_for_transcript", "mask_handler", "mask_sender",
    "normalize_sender", "redact_all", "wait_for_code",
]
