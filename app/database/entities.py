"""
SQLAlchemy relational database entities.
Defines models for Vendors, Purchase Orders, Line Items, Invoices, Validation Results, and Audit Logs.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Integer, String, Float, Boolean,
    ForeignKey, Text, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.types import UTCDateTime
from app.utils.time import now_utc

class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="USA")
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bank_account: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now_utc, nullable=False)

    # Relationships
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="vendor")

    def __repr__(self):
        return f"<Vendor(id={self.id}, name='{self.legal_name}', approved={self.is_approved})>"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    po_date: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    billed_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    remaining_balance: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now_utc, nullable=False)

    # Relationships
    vendor: Mapped[Vendor] = relationship(back_populates="purchase_orders")
    line_items: Mapped[list["POLineItem"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<PurchaseOrder(id={self.id}, po_number='{self.po_number}', balance={self.remaining_balance})>"


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(50), default="EA", nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    line_total: Mapped[float] = mapped_column(Float, nullable=False)

    # Relationships
    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="line_items")

    def __repr__(self):
        return f"<POLineItem(po_id={self.po_id}, line={self.line_number}, total={self.line_total})>"


class Invoice(Base):
    __tablename__ = "invoices"
    __allow_unmapped__ = True
    manual_override_noop: bool = False

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    matched_po_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True
    )
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    invoice_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    paid_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    po_reference: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Raw extracted JSON and evaluation results
    extracted_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    processing_status: Mapped[str] = mapped_column(String(50), default="PENDING")
    final_decision: Mapped[str] = mapped_column(String(50), default="NEEDS_REVIEW")

    # Human Override tracking
    manual_override_decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manual_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_override_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manual_override_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now_utc, nullable=False)

    # Relationships
    vendor: Mapped[Vendor | None] = relationship(back_populates="invoices")
    matched_po: Mapped[PurchaseOrder | None] = relationship()
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ProcessingRun"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    validations: Mapped[list["ValidationResultEntity"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_invoice_vendor_num", "vendor_id", "invoice_number"),
    )

    def __repr__(self):
        return f"<Invoice(id={self.id}, number='{self.invoice_number}', decision='{self.final_decision}')>"


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String(50), default="EA")
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    line_total: Mapped[float] = mapped_column(Float, default=0.0)
    matched_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("po_line_items.id"), nullable=True
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
    matched_po_line: Mapped[POLineItem | None] = relationship()


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __allow_unmapped__ = True
    decision_result: Any = None

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=now_utc, nullable=False)
    completion_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    current_stage: Mapped[str] = mapped_column(String(100), default="INITIALIZING")
    execution_status: Mapped[str] = mapped_column(String(50), default="RUNNING")
    final_decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stages_log: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    invoice: Mapped[Invoice] = relationship(back_populates="runs")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ValidationResultEntity(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("processing_runs.id"), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    invoice: Mapped[Invoice] = relationship(back_populates="validations")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("processing_runs.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=now_utc, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="SYSTEM", nullable=False)

    # Relationships
    invoice: Mapped[Invoice | None] = relationship(back_populates="audit_logs")
    run: Mapped[ProcessingRun | None] = relationship(back_populates="audit_logs")


class AppConfig(Base):
    """Durable application configuration stored independently of demo data."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=now_utc, onupdate=now_utc, nullable=False
    )
