"""TwiML builders. Every attribute and text node is XML-escaped."""
from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape, quoteattr


def _attrs(**kw) -> str:
    parts = []
    for key, value in kw.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f" {key}={quoteattr(str(value))}")
    return "".join(parts)


def response(*children: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(children) + "</Response>"


def say(text: str) -> str:
    return f"<Say>{escape(text)}</Say>"


def hangup() -> str:
    return "<Hangup/>"


def reject(reason: str = "busy") -> str:
    return f"<Reject{_attrs(reason=reason)}/>"


def conversation_relay(*, ws_url: str, action_url: str, call_id: str, welcome: Optional[str],
                       hints: Optional[list] = None) -> str:
    """Connect the leg to ConversationRelay. Recording is never enabled here. ``hints`` (round 7) are names the
    speech recognizer should expect (the owner's name, the business), so "Can" is not heard as "Ken"."""
    hint_text = ",".join(hints) if hints else None
    relay = (f"<ConversationRelay{_attrs(url=ws_url, welcomeGreeting=welcome or None, welcomeGreetingInterruptible='speech' if welcome else None, interruptible='speech', dtmfDetection=True, partialPrompts=True, hints=hint_text)}>"
             f"<Parameter{_attrs(name='call', value=call_id)}/></ConversationRelay>")
    return f"<Connect{_attrs(action=action_url, method='POST')}>{relay}</Connect>"


def owner_whisper(*, prompt: str, accept_url: str, timeout_s: int = 8) -> str:
    gather = f"<Gather{_attrs(numDigits=1, timeout=timeout_s, action=accept_url, method='POST')}>{say(prompt)}</Gather>"
    return gather + say("No answer received. Goodbye.") + hangup()


def conference(*, name: str, status_url: str, end_on_exit: bool = True) -> str:
    conf = (f"<Conference{_attrs(beep=False, startConferenceOnEnter=True, endConferenceOnExit=end_on_exit, waitUrl='', record='do-not-record', statusCallback=status_url, statusCallbackMethod='POST', statusCallbackEvent='start end join leave')}>"
            f"{escape(name)}</Conference>")
    return f"<Dial>{conf}</Dial>"
