"""All time handling in one place.

Rule of the project: **store UTC, display IST.**
Mixing naive and aware datetimes is the #1 source of bugs in monitoring bots,
so every datetime that enters the system goes through here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    """Timezone-aware 'now' in UTC. Never use datetime.utcnow() (it's naive)."""
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """Force any datetime to aware-UTC. Naive input is *assumed* UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    """Serialise for SQLite: '2026-05-01T12:00:00+00:00'."""
    return None if dt is None else to_utc(dt).isoformat()


def parse_iso_utc(value: str | None) -> datetime | None:
    """Deserialise from SQLite back to aware-UTC."""
    if not value:
        return None
    return to_utc(datetime.fromisoformat(value))


def to_display(dt: datetime | None, tz: ZoneInfo = IST) -> str:
    """Human-facing string, e.g. '01 May 2026, 05:30 PM IST'."""
    if dt is None:
        return "never"
    label = "IST" if tz is IST else str(tz)
    return to_utc(dt).astimezone(tz).strftime(f"%d %b %Y, %I:%M:%S %p {label}")


def parse_hhmm(value: str) -> time:
    """'19:30' -> time(19, 30). Raises ValueError on anything else."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return time(hour, minute)


def normalise_hhmm(value: str) -> str:
    """'7:5' -> '07:05'. Canonical form makes comparisons and hashes stable."""
    t = parse_hhmm(value)
    return f"{t.hour:02d}:{t.minute:02d}"


def parse_date(value: str) -> date:
    """Accepts YYYY-MM-DD or DD-MM-YYYY (both common in India)."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}; use YYYY-MM-DD")
