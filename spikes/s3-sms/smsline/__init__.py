"""smsline: read SMS verification codes for an owner-approved task, masked everywhere else.

Spike S3 code for the Hermes Line plugin. Stdlib only, Python 3.9+.
"""
from .masking import (
    MaskingFilter,
    install_log_masking,
    mask_codes,
    mask_handler,
    mask_sender,
    redact_all,
)
from .matching import ApprovedTask, Decision, MatchOutcome, TaskMatcher, wait_for_code
from .model import SmsMessage, normalize_sender
from .otp import OtpCandidate, OtpResult, extract_otp
from .secret import SecretText
from .sources import AdbSource, FixtureSource, GatewaySource, SmsSource

__all__ = [
    "AdbSource", "ApprovedTask", "Decision", "FixtureSource", "GatewaySource", "MaskingFilter",
    "MatchOutcome", "OtpCandidate", "OtpResult", "SecretText", "SmsMessage", "SmsSource",
    "TaskMatcher", "extract_otp", "install_log_masking", "mask_codes", "mask_handler",
    "mask_sender", "normalize_sender", "redact_all", "wait_for_code",
]
