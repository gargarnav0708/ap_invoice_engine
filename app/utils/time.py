"""UTC datetime helpers shared by persistence and workflow code."""

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, treating legacy naive values as UTC.

    Older database rows were stored without timezone metadata. The application
    has always displayed those values as UTC, so attaching UTC preserves their
    established meaning without shifting the wall-clock value.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
