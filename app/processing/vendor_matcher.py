"""
Vendor matching engine: probabilistic multi-signal matching across Tax IDs/GSTINs,
normalized company names, token sets, abbreviations, and fuzzy similarity.
"""
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.config.rules import rules_config
from app.core.schemas import NormalizedInvoice, VendorMatchCandidate
from app.database.entities import Vendor
from app.database.repositories import VendorRepository
from app.processing.probabilistic_matcher import score_vendor_similarity
from app.utils.logging import get_logger

logger = get_logger("vendor_matcher")


class VendorMatcher:
    def __init__(self, session: Session):
        self.repo = VendorRepository(session)

    def match_vendor(
        self, invoice: NormalizedInvoice
    ) -> Tuple[Optional[Vendor], List[VendorMatchCandidate], float, str]:
        """
        Matches vendor based on normalized invoice using probabilistic confidence scoring.
        Returns:
            (best_matched_vendor, all_candidates, match_score, match_status)
            match_status: 'CONFIDENT_MATCH', 'AMBIGUOUS_MATCH', 'NO_MATCH'
        """
        all_vendors = self.repo.get_all()
        if not all_vendors:
            logger.warning("No vendors registered in database.")
            return None, [], 0.0, "NO_MATCH"

        candidates: List[VendorMatchCandidate] = []
        query_name = invoice.vendor_name or invoice.normalized_vendor_name
        query_tax = invoice.vendor_tax_id

        for v in all_vendors:
            bd = score_vendor_similarity(
                query_name=query_name,
                vendor_legal_name=v.legal_name,
                vendor_normalized_name=v.normalized_name,
                query_tax_id=query_tax,
                vendor_tax_id=v.tax_id,
            )
            if bd.score >= rules_config.matching.VENDOR_REVIEW_THRESHOLD:
                candidates.append(
                    VendorMatchCandidate(
                        vendor_id=v.id,
                        legal_name=v.legal_name,
                        match_type=bd.match_type,
                        score=bd.score,
                        explanation=bd.explanation,
                        is_approved=v.is_approved,
                    )
                )

        candidates.sort(key=lambda x: x.score, reverse=True)

        if not candidates:
            logger.info(f"No vendor matched for name='{invoice.vendor_name}', tax_id='{query_tax}'")
            return None, [], 0.0, "NO_MATCH"

        top_cand = candidates[0]
        top_vendor = self.repo.get_by_id(top_cand.vendor_id)

        # Deterministic fast-path when Tax ID or normalized name is an exact 100% match
        if top_cand.match_type in {"exact_tax_id", "exact_name"} and top_cand.score >= 0.999:
            logger.info(f"Vendor matched with 100% confidence ({top_cand.match_type}): {top_cand.legal_name}")
            return top_vendor, candidates, top_cand.score, "CONFIDENT_MATCH"

        # Ambiguity check: two different vendors have very close probabilistic scores
        if len(candidates) > 1:
            second_cand = candidates[1]
            if (top_cand.score - second_cand.score) < 0.08 and top_cand.score < 0.96:
                logger.warning(
                    f"Ambiguous probabilistic vendor match: '{top_cand.legal_name}' ({top_cand.score:.2f}) "
                    f"vs '{second_cand.legal_name}' ({second_cand.score:.2f})"
                )
                return top_vendor, candidates, top_cand.score, "AMBIGUOUS_MATCH"

        if top_cand.score >= rules_config.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD:
            logger.info(
                f"Vendor matched probabilistically ({top_cand.score * 100:.1f}%): {top_cand.legal_name}"
            )
            return top_vendor, candidates, top_cand.score, "CONFIDENT_MATCH"

        return top_vendor, candidates, top_cand.score, "AMBIGUOUS_MATCH"
