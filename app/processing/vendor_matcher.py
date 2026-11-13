"""
Vendor matching engine: Exact tax ID, exact normalized name, and fuzzy matching.
"""
from typing import List, Optional, Tuple
from difflib import SequenceMatcher
from sqlalchemy.orm import Session

from app.database.entities import Vendor
from app.database.repositories import VendorRepository
from app.core.schemas import VendorMatchCandidate, NormalizedInvoice
from app.config.rules import rules_config
from app.processing.normalizer import normalize_vendor_name
from app.utils.logging import get_logger

logger = get_logger("vendor_matcher")


class VendorMatcher:
    def __init__(self, session: Session):
        self.repo = VendorRepository(session)

    def match_vendor(self, invoice: NormalizedInvoice) -> Tuple[Optional[Vendor], List[VendorMatchCandidate], float, str]:
        """
        Matches vendor based on normalized invoice.
        Returns:
            (best_matched_vendor, all_candidates, match_score, match_status)
            match_status: 'CONFIDENT_MATCH', 'AMBIGUOUS_MATCH', 'NO_MATCH'
        """
        candidates: List[VendorMatchCandidate] = []
        all_vendors = self.repo.get_all()

        if not all_vendors:
            logger.warning("No vendors registered in database.")
            return None, [], 0.0, "NO_MATCH"

        # 1. Exact Tax ID Match
        if invoice.vendor_tax_id:
            tax_match = self.repo.get_by_tax_id(invoice.vendor_tax_id)
            if tax_match:
                cand = VendorMatchCandidate(
                    vendor_id=tax_match.id,
                    legal_name=tax_match.legal_name,
                    match_type="exact_tax_id",
                    score=1.0,
                    explanation=f"Exact match on Tax ID: {tax_match.tax_id}",
                    is_approved=tax_match.is_approved
                )
                logger.info(f"Vendor matched on exact Tax ID: {tax_match.legal_name}")
                return tax_match, [cand], 1.0, "CONFIDENT_MATCH"

        # 2. Exact Normalized Name Match
        norm_name = invoice.normalized_vendor_name
        if norm_name:
            for v in all_vendors:
                if v.normalized_name == norm_name:
                    cand = VendorMatchCandidate(
                        vendor_id=v.id,
                        legal_name=v.legal_name,
                        match_type="exact_name",
                        score=1.0,
                        explanation="Exact match on normalized company name",
                        is_approved=v.is_approved
                    )
                    logger.info(f"Vendor matched on exact normalized name: {v.legal_name}")
                    return v, [cand], 1.0, "CONFIDENT_MATCH"

        # 3. Fuzzy Name Matching across all vendors
        if norm_name:
            for v in all_vendors:
                ratio = SequenceMatcher(None, norm_name, v.normalized_name).ratio()
                if ratio >= rules_config.matching.VENDOR_REVIEW_THRESHOLD:
                    candidates.append(
                        VendorMatchCandidate(
                            vendor_id=v.id,
                            legal_name=v.legal_name,
                            match_type="fuzzy_name",
                            score=round(ratio, 3),
                            explanation=f"Fuzzy name similarity of {ratio*100:.1f}%",
                            is_approved=v.is_approved
                        )
                    )

        # Sort candidates descending by score
        candidates.sort(key=lambda x: x.score, reverse=True)

        if not candidates:
            logger.info(f"No vendor matched for name: '{invoice.vendor_name}'")
            return None, [], 0.0, "NO_MATCH"

        top_cand = candidates[0]
        top_vendor = self.repo.get_by_id(top_cand.vendor_id)

        # Check for ambiguity: multiple close candidates
        if len(candidates) > 1:
            second_cand = candidates[1]
            if (top_cand.score - second_cand.score) < 0.08 and top_cand.score < 0.95:
                logger.warning(f"Ambiguous vendor match: '{top_cand.legal_name}' vs '{second_cand.legal_name}'")
                return top_vendor, candidates, top_cand.score, "AMBIGUOUS_MATCH"

        if top_cand.score >= rules_config.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD:
            return top_vendor, candidates, top_cand.score, "CONFIDENT_MATCH"

        return top_vendor, candidates, top_cand.score, "AMBIGUOUS_MATCH"
