"""
Multi-level duplicate detection engine:
Level 1: Exact file SHA-256 hash
Level 2: Same vendor + exact invoice number
Level 3: Normalized invoice identifier matching
Level 4: Near-duplicate (same vendor + similar amount within date window)
"""
from dataclasses import dataclass
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.database.entities import Invoice
from app.database.repositories import InvoiceRepository
from app.core.schemas import NormalizedInvoice
from app.config.rules import rules_config
from app.utils.logging import get_logger

logger = get_logger("duplicate_detector")


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    duplicate_type: str  # 'EXACT_HASH', 'VENDOR_INVOICE_NUM', 'NORMALIZED_NUM', 'NEAR_DUPLICATE', 'NONE'
    severity: str        # 'CONFIRMED' (blocks), 'POTENTIAL' (review), 'NONE'
    matched_invoice_id: Optional[int] = None
    matched_invoice_number: Optional[str] = None
    explanation: str = "No duplicate detected."


class DuplicateDetector:
    def __init__(self, session: Session):
        self.session = session
        self.repo = InvoiceRepository(session)

    def check_duplicate(
        self,
        file_hash: str,
        invoice: NormalizedInvoice,
        vendor_id: Optional[int],
        current_invoice_id: Optional[int] = None
    ) -> DuplicateCheckResult:
        """
        Executes multi-level duplicate detection checks.
        """
        # Level 1: Exact File Hash Match
        if file_hash:
            existing = self.repo.get_by_file_hash(file_hash)
            if existing and (current_invoice_id is None or existing.id != current_invoice_id):
                logger.warning(f"Duplicate Level 1: Identical file hash found (Invoice ID={existing.id})")
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_type="EXACT_HASH",
                    severity="CONFIRMED",
                    matched_invoice_id=existing.id,
                    matched_invoice_number=existing.invoice_number,
                    explanation=f"Exact binary file duplicate already processed (Invoice ID: {existing.id}, Number: '{existing.invoice_number}')."
                )

        # Level 2 & 3: Same Vendor + Normalized Invoice Number Match
        if vendor_id and invoice.invoice_number:
            existing = self.repo.find_duplicate_by_vendor_and_number(
                vendor_id=vendor_id,
                invoice_number=invoice.invoice_number,
                exclude_id=current_invoice_id
            )
            if existing:
                logger.warning(
                    f"Duplicate Level 2: Same vendor ({vendor_id}) and invoice number '{invoice.invoice_number}' (Invoice ID={existing.id})"
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_type="VENDOR_INVOICE_NUM",
                    severity="CONFIRMED",
                    matched_invoice_id=existing.id,
                    matched_invoice_number=existing.invoice_number,
                    explanation=f"Confirmed duplicate: Invoice '{invoice.invoice_number}' from this vendor has already been recorded (Invoice ID: {existing.id})."
                )

        # Level 4: Near-duplicate detection (same vendor + similar amount within date window)
        if vendor_id and invoice.total_amount:
            potential_matches = self.repo.find_potential_near_duplicates(
                vendor_id=vendor_id,
                total_amount=invoice.total_amount,
                tolerance_abs=rules_config.duplicates.NEAR_DUPLICATE_AMOUNT_TOLERANCE_ABS,
                exclude_id=current_invoice_id
            )

            for cand in potential_matches:
                # Check date proximity if invoice_date is available
                if invoice.invoice_date and cand.invoice_date:
                    try:
                        d1 = datetime.strptime(invoice.invoice_date, "%Y-%m-%d")
                        d2 = datetime.strptime(cand.invoice_date, "%Y-%m-%d")
                        delta_days = abs((d1 - d2).days)
                        if delta_days <= rules_config.duplicates.NEAR_DUPLICATE_DAYS_WINDOW:
                            logger.info(
                                f"Duplicate Level 4: Potential near-duplicate with Invoice ID={cand.id} "
                                f"(same vendor, amount diff <= $1, {delta_days} days apart)"
                            )
                            return DuplicateCheckResult(
                                is_duplicate=True,
                                duplicate_type="NEAR_DUPLICATE",
                                severity="POTENTIAL",
                                matched_invoice_id=cand.id,
                                matched_invoice_number=cand.invoice_number,
                                explanation=(
                                    f"Potential near-duplicate: An invoice with amount ${cand.total_amount:.2f} "
                                    f"was billed on {cand.invoice_date} ({delta_days} days difference). Requires human inspection."
                                )
                            )
                    except Exception:
                        pass

        return DuplicateCheckResult(
            is_duplicate=False,
            duplicate_type="NONE",
            severity="NONE",
            explanation="No duplicate matches found."
        )
