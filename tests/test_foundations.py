"""Regression tests for Phase 1 stabilization foundations."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config.persistence import ConfigurationService, RULE_THRESHOLDS_KEY
from app.config.rules import EditableRuleThresholds, rules_config
from app.config.settings import settings
from app.database.entities import (
    AppConfig,
    AuditLog,
    Base,
    Invoice,
    InvoiceLineItem,
    POLineItem,
    ProcessingRun,
    PurchaseOrder,
    ValidationResultEntity,
    Vendor,
)
from app.database.maintenance import (
    UnsafeDatabaseResetError,
    reset_transactional_data,
)
from app.utils.time import now_utc


def test_now_utc_is_timezone_aware_utc() -> None:
    current = now_utc()
    assert current.tzinfo is not None
    assert current.utcoffset() == timezone.utc.utcoffset(current)


def test_legacy_naive_database_timestamp_is_loaded_as_utc() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    legacy_timestamp = datetime(2024, 1, 2, 3, 4, 5)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO vendors "
                "(legal_name, normalized_name, country, is_approved, created_at) "
                "VALUES (:name, :normalized, 'USA', 1, :created_at)"
            ),
            {
                "name": "Legacy Vendor",
                "normalized": "legacy vendor",
                "created_at": legacy_timestamp.isoformat(" "),
            },
        )

    with Session(engine) as session:
        vendor = session.query(Vendor).one()
        assert vendor.created_at == legacy_timestamp.replace(tzinfo=timezone.utc)
        assert vendor.created_at.tzinfo is timezone.utc


def test_reset_seeded_database_respects_foreign_keys_and_keeps_config(
    test_db_session: Session,
) -> None:
    vendor = test_db_session.query(Vendor).first()
    purchase_order = test_db_session.query(PurchaseOrder).first()
    po_line = test_db_session.query(POLineItem).first()
    invoice = Invoice(
        original_filename="seeded.pdf",
        file_hash="a" * 64,
        file_path="/tmp/seeded.pdf",
        vendor_id=vendor.id,
        matched_po_id=purchase_order.id,
    )
    test_db_session.add(invoice)
    test_db_session.flush()
    run = ProcessingRun(invoice_id=invoice.id)
    test_db_session.add(run)
    test_db_session.flush()
    test_db_session.add_all(
        [
            InvoiceLineItem(
                invoice_id=invoice.id,
                line_number=1,
                description="Seeded line",
                matched_po_line_id=po_line.id,
            ),
            ValidationResultEntity(
                invoice_id=invoice.id,
                run_id=run.id,
                rule_id="TEST",
                rule_name="Test rule",
                category="TEST",
                result="PASS",
                severity="INFO",
                explanation="Seeded validation",
            ),
            AuditLog(
                invoice_id=invoice.id,
                run_id=run.id,
                action="SEEDED",
            ),
        ]
    )
    ConfigurationService(test_db_session).save_rule_thresholds(
        EditableRuleThresholds(arithmetic_tolerance_abs=0.08)
    )
    test_db_session.commit()

    deleted = reset_transactional_data(test_db_session)
    test_db_session.commit()

    assert deleted["invoices"] == 1
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
        assert test_db_session.query(model).count() == 0
    assert test_db_session.get(AppConfig, RULE_THRESHOLDS_KEY) is not None


def test_reset_is_blocked_in_production(
    test_db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(UnsafeDatabaseResetError, match="disabled"):
        reset_transactional_data(test_db_session)
    assert test_db_session.query(Vendor).count() > 0


def test_rule_threshold_validation_rejects_unsafe_values() -> None:
    with pytest.raises(ValidationError):
        EditableRuleThresholds(arithmetic_tolerance_abs=-0.01)
    with pytest.raises(ValidationError):
        EditableRuleThresholds(near_duplicate_days_window=0)
    with pytest.raises(ValidationError):
        EditableRuleThresholds(vendor_high_confidence_threshold=0.59)


def test_rule_thresholds_persist_across_sessions(
    test_db_session: Session,
) -> None:
    service = ConfigurationService(test_db_session)
    saved = service.save_rule_thresholds(
        {
            "arithmetic_tolerance_abs": 0.12,
            "near_duplicate_days_window": 14,
            "vendor_high_confidence_threshold": 0.91,
            "min_overall_confidence_threshold": 0.82,
        }
    )
    test_db_session.commit()
    rules_config.financial.ARITHMETIC_TOLERANCE_ABS = 0.05

    with Session(test_db_session.get_bind()) as restarted_session:
        loaded = ConfigurationService(restarted_session).load_rule_thresholds()

    assert loaded == saved
    assert rules_config.financial.ARITHMETIC_TOLERANCE_ABS == 0.12


def test_invalid_stored_thresholds_fall_back_to_safe_defaults(
    test_db_session: Session,
) -> None:
    test_db_session.add(
        AppConfig(
            key=RULE_THRESHOLDS_KEY,
            value='{"arithmetic_tolerance_abs": -5}',
        )
    )
    test_db_session.commit()

    loaded = ConfigurationService(test_db_session).load_rule_thresholds()

    assert loaded == EditableRuleThresholds()
    assert rules_config.financial.ARITHMETIC_TOLERANCE_ABS == 0.05


def test_live_mode_requires_runtime_credentials(
    test_db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    service = ConfigurationService(test_db_session)

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        service.save_extraction_preferences("live")

    preferences = service.save_extraction_preferences("demo")
    test_db_session.commit()
    assert preferences.mode == "demo"
    stored = test_db_session.get(AppConfig, "extraction_preferences")
    assert stored is not None
    assert "LLM_API_KEY" not in stored.value


def test_stored_live_mode_falls_back_when_credentials_disappear(
    test_db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "configured-only-in-runtime")
    service = ConfigurationService(test_db_session)
    service.save_extraction_preferences("live")
    test_db_session.commit()

    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    assert service.load_extraction_preferences().mode == "demo"
