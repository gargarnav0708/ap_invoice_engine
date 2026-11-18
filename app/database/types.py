"""Database column types used by the AP engine."""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.utils.time import as_utc


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC datetimes while returning timezone-aware values.

    The physical column intentionally remains timezone-naive for compatibility
    with existing SQLite and PostgreSQL tables. Values are stored as naive UTC
    and legacy naive rows are interpreted as UTC when loaded.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        return as_utc(value).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        return as_utc(value)

    @property
    def python_type(self) -> type[datetime]:
        return datetime
