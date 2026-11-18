"""Guarded database maintenance operations."""

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.entities import (
    AuditLog,
    Invoice,
    InvoiceLineItem,
    POLineItem,
    ProcessingRun,
    PurchaseOrder,
    ValidationResultEntity,
    Vendor,
)


class UnsafeDatabaseResetError(RuntimeError):
    """Raised when a reset is requested outside a local/test environment."""


def reset_transactional_data(session: Session) -> dict[str, int]:
    """Delete demo transaction data in foreign-key-safe order.

    Persistent application configuration is deliberately retained. The caller
    owns the transaction and may seed replacement data after this function
    succeeds.
    """
    if settings.APP_ENV.strip().lower() not in {"development", "test"}:
        raise UnsafeDatabaseResetError(
            "Database reset is disabled outside development and test environments."
        )

    deleted: dict[str, int] = {}
    for model in (
        AuditLog,
        ValidationResultEntity,
        InvoiceLineItem,
        ProcessingRun,
        Invoice,
        POLineItem,
        PurchaseOrder,
        Vendor,
    ):
        count = session.query(model).delete(synchronize_session=False)
        deleted[model.__tablename__] = count
    session.flush()
    session.expire_all()
    return deleted
