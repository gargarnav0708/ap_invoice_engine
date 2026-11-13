"""
SQLAlchemy relational database entities.
Defines models for Vendors, Purchase Orders, Line Items, Invoices, Validation Results, and Audit Logs.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    ForeignKey, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship

def get_utc_now():
    return datetime.now(timezone.utc)

Base = declarative_base()


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    legal_name = Column(String(255), nullable=False, unique=True)
    normalized_name = Column(String(255), nullable=False, index=True)
    address = Column(String(500), nullable=True)
    country = Column(String(100), default="USA")
    tax_id = Column(String(100), nullable=True, unique=True, index=True)
    is_approved = Column(Boolean, default=True, nullable=False)
    bank_account = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    # Relationships
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="vendor")

    def __repr__(self):
        return f"<Vendor(id={self.id}, name='{self.legal_name}', approved={self.is_approved})>"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_number = Column(String(100), nullable=False, unique=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    po_date = Column(String(50), nullable=False)
    expiry_date = Column(String(50), nullable=True)
    currency = Column(String(10), default="USD", nullable=False)
    total_amount = Column(Float, nullable=False)
    billed_amount = Column(Float, default=0.0, nullable=False)
    remaining_balance = Column(Float, nullable=False)
    status = Column(String(50), default="OPEN", nullable=False)  # OPEN, CLOSED, CANCELLED
    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    # Relationships
    vendor = relationship("Vendor", back_populates="purchase_orders")
    line_items = relationship("POLineItem", back_populates="purchase_order", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<PurchaseOrder(id={self.id}, po_number='{self.po_number}', balance={self.remaining_balance})>"


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    description = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(50), default="EA", nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="line_items")

    def __repr__(self):
        return f"<POLineItem(po_id={self.po_id}, line={self.line_number}, total={self.line_total})>"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_filename = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    file_path = Column(String(500), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    matched_po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    invoice_number = Column(String(100), nullable=True, index=True)
    invoice_date = Column(String(50), nullable=True)
    due_date = Column(String(50), nullable=True)
    po_reference = Column(String(100), nullable=True, index=True)
    currency = Column(String(10), default="USD", nullable=True)
    subtotal = Column(Float, nullable=True)
    tax_amount = Column(Float, nullable=True)
    total_amount = Column(Float, nullable=True)

    # Raw extracted JSON and evaluation results
    extracted_data = Column(Text, nullable=True)
    extraction_confidence = Column(Float, default=0.0)
    processing_status = Column(String(50), default="PENDING")  # PENDING, PROCESSING, COMPLETED, FAILED
    final_decision = Column(String(50), default="NEEDS_REVIEW")  # APPROVED, NEEDS_REVIEW, BLOCKED

    # Human Override tracking
    manual_override_decision = Column(String(50), nullable=True)
    manual_override_reason = Column(Text, nullable=True)
    manual_override_by = Column(String(100), nullable=True)
    manual_override_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=get_utc_now, nullable=False)

    # Relationships
    vendor = relationship("Vendor", back_populates="invoices")
    matched_po = relationship("PurchaseOrder")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")
    runs = relationship("ProcessingRun", back_populates="invoice", cascade="all, delete-orphan")
    validations = relationship("ValidationResultEntity", back_populates="invoice", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_invoice_vendor_num", "vendor_id", "invoice_number"),
    )

    def __repr__(self):
        return f"<Invoice(id={self.id}, number='{self.invoice_number}', decision='{self.final_decision}')>"


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    description = Column(String(500), nullable=False)
    quantity = Column(Float, default=1.0)
    unit = Column(String(50), default="EA")
    unit_price = Column(Float, default=0.0)
    tax_amount = Column(Float, default=0.0)
    line_total = Column(Float, default=0.0)
    matched_po_line_id = Column(Integer, ForeignKey("po_line_items.id"), nullable=True)

    # Relationships
    invoice = relationship("Invoice", back_populates="line_items")
    matched_po_line = relationship("POLineItem")


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    start_time = Column(DateTime, default=get_utc_now, nullable=False)
    completion_time = Column(DateTime, nullable=True)
    current_stage = Column(String(100), default="INITIALIZING")
    execution_status = Column(String(50), default="RUNNING")  # RUNNING, SUCCESS, FAILED
    final_decision = Column(String(50), nullable=True)
    duration_ms = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    stages_log = Column(Text, nullable=True)  # JSON serialized list of StageExecutionRecord

    # Relationships
    invoice = relationship("Invoice", back_populates="runs")
    audit_logs = relationship("AuditLog", back_populates="run", cascade="all, delete-orphan")


class ValidationResultEntity(Base):
    __tablename__ = "validation_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("processing_runs.id"), nullable=True)
    rule_id = Column(String(100), nullable=False)
    rule_name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)
    result = Column(String(20), nullable=False)  # PASS, FAIL, WARN
    severity = Column(String(20), nullable=False)  # HARD, SOFT, INFO
    expected_value = Column(String(255), nullable=True)
    actual_value = Column(String(255), nullable=True)
    difference = Column(String(255), nullable=True)
    explanation = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)

    # Relationships
    invoice = relationship("Invoice", back_populates="validations")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id"), nullable=True)
    timestamp = Column(DateTime, default=get_utc_now, nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    previous_status = Column(String(100), nullable=True)
    new_status = Column(String(100), nullable=True)
    actor = Column(String(100), default="SYSTEM", nullable=False)

    # Relationships
    invoice = relationship("Invoice", back_populates="audit_logs")
    run = relationship("ProcessingRun", back_populates="audit_logs")
