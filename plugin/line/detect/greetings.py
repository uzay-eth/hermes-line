"""Personal-name greeting shapes; input is normalized lowercase transcript text.

These lexical cues do not prove acoustic liveness or verify an identity.
"""
import re

_NON_NAMES = frozenset('our your the a an automated virtual digital ai assistant bot customer support service recording recorded name agent representative front desk not no'.split())
_PREFIX = r'(?:(?:hi|hello|good morning|good afternoon) |(?:thanks|thank you) for (?:holding|waiting|calling(?: [a-z]+){0,6}) )?'
_IDENTITY = re.compile(r'^' + _PREFIX + r'(?:this is|my name is|i m) (?P<name>[a-z]+)\b')
_SPEAKING = re.compile(r'^(?:(?:hi|hello) )?(?P<name>[a-z]+)(?: [a-z]+)? speaking\b')


def named_greeting(text):
    match = _IDENTITY.match(text) or _SPEAKING.match(text)
    return match is not None and match['name'] not in _NON_NAMES


def strong_named_greeting(text):
    return named_greeting(text) and re.search(
        r'\b(?:help|assist|with|from|agent|representative|specialist|support|care|services)\b', text) is not None
