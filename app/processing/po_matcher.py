"""
Purchase Order matching engine: probabilistic multi-signal matching across
differently formatted PO references, vendor ownership, FX-converted amounts,
and line-item descriptions.
"""
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.config.rules import rules_config
from app.core.schemas import NormalizedInvoice, POMatchCandidate
from app.database.entities import PurchaseOrder, Vendor
from app.database.repositories import PORepository
from app.processing.probabilistic_matcher import (
    score_po_candidate,
    score_po_reference_similarity,
)
from app.utils.logging import get_logger

logger = get_logger("po_matcher")


class POMatcher:
    def __init__(self, session: Session):
        self.session = session
        self.repo = PORepository(session)

    def match_po(
        self,
        invoice: NormalizedInvoice,
        matched_vendor: Optional[Vendor] = None,
    ) -> Tuple[Optional[PurchaseOrder], List[POMatchCandidate], float, str]:
        """
        Finds the corresponding Purchase Order for an invoice using probabilistic scoring.
        Returns:
            (best_po, candidates, score, match_status)
            match_status: 'CONFIDENT_MATCH', 'AMBIGUOUS_MATCH', 'NO_MATCH', 'VENDOR_MISMATCH'
        """
        all_pos = self.session.query(PurchaseOrder).all()
        if not all_pos:
            return None, [], 0.0, "NO_MATCH"

        candidates: List[POMatchCandidate] = []
        po_ref = (invoice.po_reference or "").strip()

        # Priority 1: Evaluate PO reference similarity across all POs (handles exact,
        # normalized, leading-zero differences like PO-2026-01 vs PO-2026-001, and OCR typos)
        if po_ref:
            ref_matches: list[tuple[PurchaseOrder, float, str]] = []
            for po in all_pos:
                ref_bd = score_po_reference_similarity(po_ref, po.po_number)
                if ref_bd.score >= 0.75:
                    ref_matches.append((po, ref_bd.score, ref_bd.explanation))

            ref_matches.sort(key=lambda item: item[1], reverse=True)
            if ref_matches:
                best_po, best_ref_score, best_ref_exp = ref_matches[0]
                vendor_matches = matched_vendor is not None and best_po.vendor_id == matched_vendor.id
                full_bd = score_po_candidate(invoice, best_po, matched_vendor)

                for po, r_score, r_exp in ref_matches:
                    v_ok = matched_vendor is not None and po.vendor_id == matched_vendor.id
                    cand_score = round(min(1.0, r_score + (0.02 if v_ok else 0.0)), 3) if v_ok or not matched_vendor else round(0.50 * r_score, 3)
                    candidates.append(
                        POMatchCandidate(
                            po_id=po.id,
                            po_number=po.po_number,
                            vendor_id=po.vendor_id,
                            score=cand_score,
                            explanation=r_exp
                            + (" (Vendor verified)" if v_ok else (" [WARNING: Vendor mismatch with PO owner!]" if matched_vendor else "")),
                            total_amount=po.total_amount,
                            remaining_balance=po.remaining_balance,
                            currency=po.currency,
                            status=po.status,
                        )
                    )

                if matched_vendor and not vendor_matches and best_ref_score >= 0.85:
                    logger.warning(
                        f"PO '{best_po.po_number}' matched reference '{po_ref}' ({best_ref_score:.2f}) "
                        f"but belongs to vendor {best_po.vendor_id}, not {matched_vendor.id}."
                    )
                    return best_po, candidates, 0.50, "VENDOR_MISMATCH"

                # Check if two distinct POs matched the reference almost equally well
                if len(ref_matches) > 1 and (ref_matches[0][1] - ref_matches[1][1]) < 0.06 and best_ref_score < 0.98:
                    return best_po, candidates, candidates[0].score, "AMBIGUOUS_MATCH"

                if best_ref_score >= rules_config.matching.PO_SIMILARITY_THRESHOLD:
                    return best_po, candidates, max(candidates[0].score, full_bd.score), "CONFIDENT_MATCH"

        # Priority 2: When explicit PO reference is missing or did not match strongly,
        # score all POs belonging to the matched vendor probabilistically.
        if matched_vendor:
            vendor_pos = self.repo.get_by_vendor_id(matched_vendor.id)
            for po in vendor_pos:
                if any(c.po_id == po.id for c in candidates):
                    continue
                bd = score_po_candidate(invoice, po, matched_vendor)
                if bd.score >= 0.50:
                    candidates.append(
                        POMatchCandidate(
                            po_id=po.id,
                            po_number=po.po_number,
                            vendor_id=po.vendor_id,
                            score=bd.score,
                            explanation=bd.explanation,
                            total_amount=po.total_amount,
                            remaining_balance=po.remaining_balance,
                            currency=po.currency,
                            status=po.status,
                        )
                    )

        candidates.sort(key=lambda x: x.score, reverse=True)

        if not candidates:
            logger.info("No PO candidate could be identified.")
            return None, [], 0.0, "NO_MATCH"

        top_cand = candidates[0]
        top_po = self.repo.get_by_id(top_cand.po_id)

        # Multiple plausible candidate POs -> route to human review with ranked candidates
        if len(candidates) > 1 and candidates[1].score >= 0.70:
            logger.warning("Multiple plausible PO candidates found. Flagging as ambiguous.")
            return top_po, candidates, top_cand.score, "AMBIGUOUS_MATCH"

        if top_cand.score >= rules_config.matching.PO_SIMILARITY_THRESHOLD and po_ref:
            return top_po, candidates, top_cand.score, "CONFIDENT_MATCH"

        # Without an explicit PO reference on the invoice, even a high-scoring inferred PO
        # is surfaced as a top candidate for human confirmation rather than silently assumed.
        return top_po, candidates, top_cand.score, "AMBIGUOUS_MATCH"
