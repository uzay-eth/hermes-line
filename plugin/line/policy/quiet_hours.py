from datetime import datetime
from zoneinfo import ZoneInfo


def is_quiet(now: datetime, settings) -> bool:
    """Start inclusive/end exclusive, using wall time across DST transitions."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('an aware datetime is required')
    if not settings.quiet_hours_enabled:
        return False
    local = now.astimezone(ZoneInfo(settings.owner_timezone))
    minute = local.hour * 60 + local.minute
    def minutes(value):
        h, m = map(int, value.split(':'))
        return h * 60 + m
    start, end = map(minutes, (settings.quiet_hours_start, settings.quiet_hours_end))
    return (minute >= start or minute < end) if start > end else start <= minute < end
