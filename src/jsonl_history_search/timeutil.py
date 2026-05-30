from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 on this host commonly lacks zoneinfo.
    ZoneInfo = None


def parse_timezone_offset(value: str | None) -> timezone:
    if not value:
        return timezone.utc
    raw = value.strip()
    if raw.upper() == "Z":
        return timezone.utc
    sign = 1
    if raw.startswith("-"):
        sign = -1
        raw = raw[1:]
    elif raw.startswith("+"):
        raw = raw[1:]
    hours, _, minutes = raw.partition(":")
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes or "0")))


def timezone_from_args(name: str | None, offset: str | None):
    if name:
        if ZoneInfo is None:
            raise SystemExit("--timezone requires Python 3.9+ zoneinfo; use --timezone-offset on this interpreter")
        return ZoneInfo(name)
    return parse_timezone_offset(offset or "+00:00")


def parse_datetime(value: str | None, default_tz) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        numeric = float(raw)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def parse_now(value: str | None, default_tz) -> datetime:
    parsed = parse_datetime(value, default_tz) if value else None
    if parsed is None:
        return datetime.now(default_tz)
    return parsed.astimezone(default_tz)


def local_day_window(day, tz) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
