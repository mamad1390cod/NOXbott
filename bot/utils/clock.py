"""The project's time convention.

* **The database stores UTC.** SQLite hands datetimes back *without* a timezone
  (``DateTime(timezone=True)`` is not honoured by SQLite), so every datetime read
  from a row is a naive UTC value. ``as_utc()`` restores that meaning.
* **Humans see local time.** Admins type and read times in the server's own
  timezone (``datetime.astimezone()``), which is what the notification messages in
  this bot already print.

Mixing the two — comparing a naive local ``datetime.now()`` against a naive UTC
value — silently shifts every deadline by the server's UTC offset. Always go
through this module instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_FORMAT = "%Y-%m-%d %H:%M"


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC (the storage convention) and return it aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_local(value: datetime) -> datetime:
    """Convert a stored (or aware) datetime into the server's local timezone."""
    return as_utc(value).astimezone()


def format_local(value: datetime, fmt: str = DEFAULT_FORMAT) -> str:
    """Render a stored datetime for a human."""
    return to_local(value).strftime(fmt)


def local_now_str(fmt: str = DEFAULT_FORMAT) -> str:
    """The current local time, for prompts that ask the admin to type a time."""
    return utc_now().astimezone().strftime(fmt)


def parse_local(text: str, fmt: str = DEFAULT_FORMAT) -> datetime:
    """Parse a human-typed local time into an aware UTC datetime.

    Raises ``ValueError`` when the text does not match ``fmt``.
    """
    return datetime.strptime(text.strip(), fmt).astimezone(timezone.utc)
