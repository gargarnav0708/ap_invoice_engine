"""
Purchase Order matching engine: Exact PO number, vendor-scoped fallback, and similarity scoring.
"""
from typing import List, Optional, Tuple
from difflib import SequenceMatcher
from sqlalchemy.orm import Session

from app.database.entities import PurchaseOrder, Vendor
from app.database.repositories import PORepository
from app.core.schemas import POMatchCandidate, NormalizedInvoice
from app.config.rules import rules_config
from app.utils.logging import get_logger

logger = get_logger("po_matcher")


class POMatcher:
    def __init__(self, session: Session):
        self.session = session
        self.repo = PORepository(session)

    def match_po(
        self,
        invoice: NormalizedInvoice,
        matched_vendor: Optional[Vendor] = None
    ) -> Tuple[Optional[PurchaseOrder], List[POMatchCandidate], float, str]:
        """
        Attempts to find the corresponding Purchase Order for an invoice.
        Returns:
            (best_po, candidates, score, match_status)
            match_status: 'CONFIDENT_MATCH', 'AMBIGUOUS_MATCH', 'NO_MATCH', 'VENDOR_MISMATCH'
        """
        candidates: List[POMatchCandidate] = []
        po_ref = invoice.po_reference

        # Priority 1: Exact PO Reference Number Match
        if po_ref:
            exact_po = self.repo.get_by_po_number(po_ref)
            if exact_po:
                # Check vendor consistency
                vendor_matches = (matched_vendor is not None and exact_po.vendor_id == matched_vendor.id)
                cand = POMatchCandidate(
                    po_id=exact_po.id,
                    po_number=exact_po.po_number,
                    vendor_id=exact_po.vendor_id,
                    score=1.0 if vendor_matches else 0.85,
                    explanation=(
                        f"Exact PO match '{exact_po.po_number}'" +
                        (" (Vendor verified)" if vendor_matches else " [WARNING: Vendor mismatch with PO owner!]")
                    ),
                    total_amount=exact_po.total_amount,
                    remaining_balance=exact_po.remaining_balance,
                    currency=exact_po.currency,
                    status=exact_po.status
                )
                candidates.append(cand)

                if matched_vendor and not vendor_matches:
                    logger.warning(
                        f"PO '{exact_po.po_number}' belongs to vendor {exact_po.vendor_id}, "
                        f"but invoice vendor is {matched_vendor.id}."
                    )
                    return exact_po, candidates, 0.50, "VENDOR_MISMATCH"

                return exact_po, candidates, 1.0, "CONFIDENT_MATCH"

        # Priority 2: When explicit PO is missing or not found, evaluate open POs for the vendor
        if matched_vendor:
            open_pos = self.repo.get_by_vendor_id(matched_vendor.id)
            for po in open_pos:
                score_factors = []
                # Currency check
                if invoice.currency and po.currency == invoice.currency:
                    score_factors.append(0.25)
                # Amount closeness (invoice total <= remaining balance and close to total or line items)
                if invoice.total_amount and po.total_amount > 0:
                    diff_pct = abs(po.total_amount - invoice.total_amount) / po.total_amount
                    if diff_pct < 0.05:
                        score_factors.append(0.50)
                    elif invoice.total_amount <= po.remaining_balance:
                        score_factors.append(0.30)
                # Status check
                if po.status == "OPEN":
                    score_factors.append(0.25)

                candidate_score = sum(score_factors)
                if candidate_score >= 0.50:
                    candidates.append(
                        POMatchCandidate(
                            po_id=po.id,
                            po_number=po.po_number,
                            vendor_id=po.vendor_id,
                            score=round(candidate_score, 3),
                            explanation=f"Inferred candidate from vendor '{matched_vendor.legal_name}' open POs (score={candidate_score:.2f})",
                            total_amount=po.total_amount,
                            remaining_balance=po.remaining_balance,
                            currency=po.currency,
                            status=po.status
                        )
                    )

        candidates.sort(key=lambda x: x.score, reverse=True)

        if not candidates:
            logger.info("No PO candidate could be identified.")
            return None, [], 0.0, "NO_MATCH"

        top_cand = candidates[0]
        top_po = self.repo.get_by_id(top_cand.po_id)

        # Ambiguity check: multiple viable candidate POs
        if len(candidates) > 1 and candidates[1].score >= 0.70:
            logger.warning("Multiple plausible PO candidates found. Flagging as ambiguous.")
            return top_po, candidates, top_cand.score, "AMBIGUOUS_MATCH"

        if top_cand.score >= rules_config.matching.PO_SIMILARITY_THRESHOLD and po_ref:
            return top_po, candidates, top_cand.score, "CONFIDENT_MATCH"

        # Without an explicit PO reference, even a high scoring inferred PO must be reviewed by a human!
        return top_po, candidates, top_cand.score, "AMBIGUOUS_MATCH"
