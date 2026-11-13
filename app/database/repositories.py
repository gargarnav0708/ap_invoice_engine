"""
Database repositories with atomic transactions and domain query methods.
"""
from typing import List, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

from app.database.entities import (
    Vendor, PurchaseOrder, POLineItem, Invoice,
    InvoiceLineItem, ProcessingRun, ValidationResultEntity, AuditLog
)
from app.core.schemas import DecisionStatus, RuleStatus


class VendorRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, vendor_id: int) -> Optional[Vendor]:
        return self.session.query(Vendor).filter(Vendor.id == vendor_id).first()

    def get_by_tax_id(self, tax_id: str) -> Optional[Vendor]:
        if not tax_id:
            return None
        return self.session.query(Vendor).filter(
            func.lower(Vendor.tax_id) == tax_id.strip().lower()
        ).first()

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Vendor]:
        if not normalized_name:
            return None
        return self.session.query(Vendor).filter(
            Vendor.normalized_name == normalized_name.strip().lower()
        ).first()

    def get_all_active(self) -> List[Vendor]:
        return self.session.query(Vendor).filter(Vendor.is_approved == True).all()

    def get_all(self) -> List[Vendor]:
        return self.session.query(Vendor).all()

    def add(self, vendor: Vendor) -> Vendor:
        self.session.add(vendor)
        self.session.flush()
        return vendor


class PORepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, po_id: int) -> Optional[PurchaseOrder]:
        return self.session.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()

    def get_by_po_number(self, po_number: str) -> Optional[PurchaseOrder]:
        if not po_number:
            return None
        clean_po = po_number.strip().upper()
        return self.session.query(PurchaseOrder).filter(
            func.upper(PurchaseOrder.po_number) == clean_po
        ).first()

    def get_by_vendor_id(self, vendor_id: int) -> List[PurchaseOrder]:
        return self.session.query(PurchaseOrder).filter(
            PurchaseOrder.vendor_id == vendor_id
        ).all()

    def get_open_pos_for_vendor(self, vendor_id: int) -> List[PurchaseOrder]:
        return self.session.query(PurchaseOrder).filter(
            PurchaseOrder.vendor_id == vendor_id,
            PurchaseOrder.status == "OPEN"
        ).all()

    def update_billed_balance_atomic(
        self, po_id: int, invoice_amount: float, action: str = "ADD"
    ) -> Tuple[bool, Optional[str]]:
        """
        Atomically update billed amount and remaining balance on a PO.
        action='ADD' increases billed_amount and decreases remaining_balance.
        action='REVERT' decreases billed_amount and increases remaining_balance.
        """
        po = self.session.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).with_for_update().first()
        if not po:
            return False, f"Purchase Order ID {po_id} not found."

        if action == "ADD":
            po.billed_amount = round(po.billed_amount + invoice_amount, 2)
            po.remaining_balance = round(po.total_amount - po.billed_amount, 2)
            if po.remaining_balance <= 0.00:
                po.remaining_balance = max(0.0, po.remaining_balance)
        elif action == "REVERT":
            po.billed_amount = max(0.0, round(po.billed_amount - invoice_amount, 2))
            po.remaining_balance = round(po.total_amount - po.billed_amount, 2)

        self.session.flush()
        return True, None


class InvoiceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, invoice_id: int) -> Optional[Invoice]:
        return self.session.query(Invoice).filter(Invoice.id == invoice_id).first()

    def get_by_file_hash(self, file_hash: str) -> Optional[Invoice]:
        if not file_hash:
            return None
        return self.session.query(Invoice).filter(Invoice.file_hash == file_hash).first()

    def find_duplicate_by_vendor_and_number(
        self, vendor_id: int, invoice_number: str, exclude_id: Optional[int] = None
    ) -> Optional[Invoice]:
        if not vendor_id or not invoice_number:
            return None
        clean_num = invoice_number.strip().upper()
        query = self.session.query(Invoice).filter(
            Invoice.vendor_id == vendor_id,
            func.upper(Invoice.invoice_number) == clean_num
        )
        if exclude_id:
            query = query.filter(Invoice.id != exclude_id)
        return query.first()

    def find_potential_near_duplicates(
        self,
        vendor_id: int,
        total_amount: float,
        tolerance_abs: float = 1.0,
        exclude_id: Optional[int] = None
    ) -> List[Invoice]:
        if not vendor_id or total_amount is None:
            return []
        query = self.session.query(Invoice).filter(
            Invoice.vendor_id == vendor_id,
            func.abs(Invoice.total_amount - total_amount) <= tolerance_abs
        )
        if exclude_id:
            query = query.filter(Invoice.id != exclude_id)
        return query.all()

    def get_all(self, limit: int = 100, offset: int = 0) -> List[Invoice]:
        return self.session.query(Invoice).order_by(Invoice.created_at.desc()).offset(offset).limit(limit).all()

    def get_count_by_decision(self) -> dict:
        results = self.session.query(
            Invoice.final_decision, func.count(Invoice.id)
        ).group_by(Invoice.final_decision).all()
        counts = {status.value: 0 for status in DecisionStatus}
        for status, count in results:
            if status in counts:
                counts[status] = count
        return counts

    def get_total_spend_processed(self) -> float:
        total = self.session.query(func.sum(Invoice.total_amount)).filter(
            Invoice.final_decision == DecisionStatus.APPROVED.value
        ).scalar()
        return float(total or 0.0)

    def add(self, invoice: Invoice) -> Invoice:
        self.session.add(invoice)
        self.session.flush()
        return invoice


class AuditRepository:
    def __init__(self, session: Session):
        self.session = session

    def log(
        self,
        action: str,
        invoice_id: Optional[int] = None,
        run_id: Optional[int] = None,
        details: Optional[str] = None,
        previous_status: Optional[str] = None,
        new_status: Optional[str] = None,
        actor: str = "SYSTEM"
    ) -> AuditLog:
        entry = AuditLog(
            invoice_id=invoice_id,
            run_id=run_id,
            action=action,
            details=details,
            previous_status=previous_status,
            new_status=new_status,
            actor=actor,
            timestamp=datetime.now(timezone.utc)
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def get_logs_for_invoice(self, invoice_id: int) -> List[AuditLog]:
        return self.session.query(AuditLog).filter(
            AuditLog.invoice_id == invoice_id
        ).order_by(AuditLog.timestamp.asc()).all()
