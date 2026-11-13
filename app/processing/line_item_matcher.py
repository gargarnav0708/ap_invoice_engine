"""
Line item matching engine comparing invoice line items against PO line items.
"""
from typing import List, Optional
from difflib import SequenceMatcher

from app.database.entities import PurchaseOrder, POLineItem
from app.core.schemas import NormalizedInvoice, LineItemMatch
from app.config.rules import rules_config
from app.utils.logging import get_logger

logger = get_logger("line_item_matcher")


class LineItemMatcher:
    def match_line_items(
        self,
        invoice: NormalizedInvoice,
        po: Optional[PurchaseOrder]
    ) -> List[LineItemMatch]:
        """
        Matches each line item from the normalized invoice to the best matching PO line item.
        """
        matches: List[LineItemMatch] = []

        if not invoice.line_items:
            return matches

        if not po or not po.line_items:
            # No PO to match against; record unmatched
            for idx, item in enumerate(invoice.line_items):
                matches.append(
                    LineItemMatch(
                        invoice_line_idx=idx,
                        po_line_id=None,
                        score=0.0,
                        explanation="No PO line items available for comparison."
                    )
                )
            return matches

        po_lines: List[POLineItem] = po.line_items

        for idx, inv_item in enumerate(invoice.line_items):
            best_score = 0.0
            best_po_line: Optional[POLineItem] = None
            explanation = "No matching PO line item."

            inv_desc = inv_item.description.lower().strip()

            for po_line in po_lines:
                po_desc = po_line.description.lower().strip()

                # Description similarity
                desc_sim = SequenceMatcher(None, inv_desc, po_desc).ratio()

                # Unit price closeness
                price_match = 0.0
                if po_line.unit_price > 0:
                    diff_pct = abs(inv_item.unit_price - po_line.unit_price) / po_line.unit_price
                    if diff_pct <= rules_config.matching.LINE_ITEM_PRICE_TOLERANCE_PCT:
                        price_match = 0.30
                    elif diff_pct <= 0.10:
                        price_match = 0.15

                # Total composite score
                composite = (desc_sim * 0.70) + price_match

                if composite > best_score:
                    best_score = composite
                    best_po_line = po_line

            if best_po_line and best_score >= rules_config.matching.LINE_ITEM_DESCRIPTION_SIMILARITY_THRESHOLD:
                price_diff = round(inv_item.unit_price - best_po_line.unit_price, 2)
                qty_diff = round(inv_item.quantity - best_po_line.quantity, 2)

                notes = []
                if abs(price_diff) > 0.01:
                    notes.append(f"Price diff: ${price_diff:+.2f}")
                if qty_diff > 0:
                    notes.append(f"Qty exceeds PO by {qty_diff}")

                explanation = f"Matched to PO Line {best_po_line.line_number} ('{best_po_line.description}')"
                if notes:
                    explanation += f" with discrepancies: {', '.join(notes)}"

                matches.append(
                    LineItemMatch(
                        invoice_line_idx=idx,
                        po_line_id=best_po_line.id,
                        po_line_number=best_po_line.line_number,
                        score=round(best_score, 3),
                        matched_description=best_po_line.description,
                        price_diff=price_diff,
                        qty_diff=qty_diff,
                        explanation=explanation
                    )
                )
            else:
                matches.append(
                    LineItemMatch(
                        invoice_line_idx=idx,
                        po_line_id=None,
                        score=round(best_score, 3),
                        explanation=f"No matching PO line item found (best similarity {best_score*100:.1f}%)."
                    )
                )

        return matches
