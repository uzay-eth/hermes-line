"""TwiML for the Media Streams transport (L1 serves it; every value is XML-escaped).

``<Stream url>`` does not support query strings (twilio.com/docs/voice/twiml/stream,
accessed 2026-11-05), so the one-time stream token travels in the URL path, and the call
id as a ``<Parameter>`` (name+value under 500 characters).
"""
from __future__ import annotations

import re
from typing import Optional
from xml.sax.saxutils import quoteattr


def _attrs(**kw) -> str:
    return "".join(f" {k}={quoteattr(str(v))}" for k, v in kw.items() if v is not None)


def connect_stream(*, ws_url: str, action_url: str, call_id: str, status_url: Optional[str] = None) -> str:
    if "?" in ws_url:
        raise ValueError("a <Stream> url cannot carry a query string; put the token in the path")
    if not ws_url.startswith("wss://"):
        raise ValueError("the stream url must be wss://")
    stream = (f"<Stream{_attrs(url=ws_url, statusCallback=status_url, statusCallbackMethod='POST' if status_url else None)}>"
              f"<Parameter{_attrs(name='call', value=call_id)}/></Stream>")
    return f"<Connect{_attrs(action=action_url, method='POST')}>{stream}</Connect>"


def play_digits_then_stream(*, digits: str, ws_url: str, action_url: str, call_id: str,
                            status_url: Optional[str] = None) -> str:
    """The DTMF fallback document: Twilio plays ``digits`` on the call, then opens a new stream.

    ``ws_url`` must carry a fresh one-time stream token (in the path)."""
    if not isinstance(digits, str) or not re.fullmatch(r"[0-9*#w]{1,32}", digits):
        raise ValueError("digits must be 0-9, *, # or w (max 32)")
    return f"<Play{_attrs(digits=digits)}/>" + connect_stream(ws_url=ws_url, action_url=action_url, call_id=call_id,
                                                               status_url=status_url)


def response(*children: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(children) + "</Response>"
