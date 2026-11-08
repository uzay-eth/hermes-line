"""Read-only access to coordinator-owned number files. Never log their values."""
import json
import re
from pathlib import Path


def load_personal_numbers(path=None):
    path = Path(path) if path is not None else Path.home() / '.hermes-line/personal_numbers.json'
    try:
        if path.stat().st_mode & 0o777 != 0o600:
            raise ValueError('personal_numbers.json must have mode 0600')
        values = json.loads(path.read_text())
        if (not isinstance(values, dict) or set(values) != {'owner', 'fi'}
                or any(not isinstance(v, str) or not re.fullmatch(r'\+1[0-9]{10}', v)
                       for v in values.values()) or values['owner'] == values['fi']):
            raise ValueError('personal_numbers.json requires distinct owner and fi E.164 numbers')
        return values
    except FileNotFoundError:
        raise ValueError('Missing ~/.hermes-line/personal_numbers.json; coordinator must stage owner and fi') from None
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ValueError('Cannot read valid personal_numbers.json') from None


def load_line_number(path=None):
    path = Path(path) if path is not None else Path.home() / '.hermes-line/twilio_line_number'
    try:
        value = path.read_text().strip()
    except (OSError, UnicodeError):
        raise ValueError('Missing or unreadable ~/.hermes-line/twilio_line_number') from None
    if not re.fullmatch(r'\+1[0-9]{10}', value):
        raise ValueError('Invalid twilio_line_number')
    return value
