"""Validated immutable settings; no implicit destination approvals."""
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .destinations import _hard_reason, _normalize_blocked

@dataclass(frozen=True)
class Settings:
    disclosure_enabled: bool = True
    recording_enabled: bool = False
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = '21:00'
    quiet_hours_end: str = '08:00'
    owner_timezone: str = 'America/Los_Angeles'
    max_calls_per_line: int = 1
    allowlist_only: bool = True
    require_approval: bool = True
    allowed_destinations: tuple[str, ...] = ()
    blocked_destinations: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ('disclosure_enabled', 'recording_enabled', 'quiet_hours_enabled',
                     'allowlist_only', 'require_approval'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} must be boolean')
        if self.require_approval is not True:
            raise ValueError('approval is mandatory')
        if type(self.max_calls_per_line) is not int or self.max_calls_per_line != 1:
            raise ValueError('exactly one call per line is mandatory')
        import re
        for value in (self.quiet_hours_start, self.quiet_hours_end):
            if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
                raise ValueError('quiet hours must be HH:MM')
        if self.quiet_hours_start == self.quiet_hours_end:
            raise ValueError('quiet hours must have distinct boundaries')
        try:
            ZoneInfo(self.owner_timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ValueError('invalid owner timezone') from None
        if not isinstance(self.allowed_destinations, (list, tuple)):
            raise ValueError('allowed_destinations must be a list or tuple')
        if any(_hard_reason(value) for value in self.allowed_destinations):
            raise ValueError('allowlist contains an unsafe destination')
        object.__setattr__(self, 'allowed_destinations', tuple(self.allowed_destinations))
        if not isinstance(self.blocked_destinations, (list, tuple)):
            raise ValueError('blocked_destinations must be a list or tuple')
        blocked = tuple(dict.fromkeys(_normalize_blocked(value) for value in self.blocked_destinations))
        object.__setattr__(self, 'blocked_destinations', blocked)

    @classmethod
    def load(cls, values: dict):
        if not isinstance(values, dict) or set(values) - set(cls.__dataclass_fields__):
            raise ValueError('unknown or invalid settings')
        return cls(**values)
