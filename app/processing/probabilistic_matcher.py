"""
Probabilistic similarity and confidence-scoring engine for Vendors, Purchase
Orders, and Line Items.

Vendors frequently format company names, tax IDs/GSTINs, PO references, and item
descriptions differently across invoices (e.g., "M/s Acme Ind. Supply" vs
"Acme Industrial Supplies Inc.", or "P.O. #2026/01" vs "PO-2026-001").
Instead of brittle exact-string constraints, this module computes calibrated
confidence scores in [0.0, 1.0] across multiple lexical, token-set, numeric,
and financial signals, returning human-readable signal breakdowns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Sequence

from app.core.schemas import NormalizedInvoice, NormalizedLineItem
from app.database.entities import POLineItem, PurchaseOrder, Vendor
from app.processing.currency import SUPPORTED_CURRENCIES, convert_amount
from app.processing.normalizer import normalize_identifier, normalize_vendor_name

# Common corporate/procurement abbreviations expanded during token matching
_ABBREVIATIONS: dict[str, str] = {
    "ind": "industrial",
    "indl": "industrial",
    "mfg": "manufacturing",
    "mfr": "manufacturer",
    "intl": "international",
    "int": "international",
    "svcs": "services",
    "svc": "services",
    "srv": "services",
    "tech": "technology",
    "solns": "solutions",
    "soln": "solutions",
    "sol": "solutions",
    "comp": "components",
    "comps": "components",
    "supp": "supplies",
    "sup": "supplies",
    "supply": "supplies",
    "log": "logistics",
    "off": "office",
    "sys": "systems",
    "engr": "engineering",
    "eng": "engineering",
    "elec": "electrical",
    "equip": "equipment",
    "dist": "distribution",
    "ent": "enterprises",
    "assoc": "associates",
    "bros": "brothers",
}

_STOP_PREFIXES = {"ms", "m", "s", "messrs", "shri", "sri", "the"}


@dataclass(frozen=True)
class SimilarityBreakdown:
    score: float            # calibrated confidence in [0.0, 1.0]
    match_type: str         # exact_tax_id | fuzzy_tax_id | exact_name | probabilistic_name | ...
    explanation: str


def expand_business_tokens(name: Optional[str]) -> list[str]:
    """Tokenize a vendor or item name, stripping honorifics and expanding abbreviations."""
    norm = normalize_vendor_name(name)
    if not norm:
        return []
    raw_tokens = [t for t in norm.split() if t and t not in _STOP_PREFIXES]
    return [_ABBREVIATIONS.get(t, t) for t in raw_tokens]


def _ocr_canonical_alnum(text: str) -> str:
    """Normalize common OCR look-alike characters inside identifiers (O->0, I/L->1, S->5)."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())
    return (
        cleaned.replace("O", "0")
        .replace("I", "1")
        .replace("L", "1")
        .replace("S", "5")
        .replace("B", "8")
    )


def score_tax_id_similarity(query_tax_id: Optional[str], vendor_tax_id: Optional[str]) -> float:
    """Compare two tax IDs / GSTINs after stripping formatting punctuation and prefixes."""
    if not query_tax_id or not vendor_tax_id:
        return 0.0
    q_clean = re.sub(r"^(?:GSTIN|TAXID|TAX|VAT|EIN|PAN)[:\-\s]*", "", query_tax_id.strip(), flags=re.IGNORECASE)
    v_clean = re.sub(r"^(?:GSTIN|TAXID|TAX|VAT|EIN|PAN)[:\-\s]*", "", vendor_tax_id.strip(), flags=re.IGNORECASE)
    q_alnum = re.sub(r"[^A-Za-z0-9]", "", q_clean).upper()
    v_alnum = re.sub(r"[^A-Za-z0-9]", "", v_clean).upper()
    if not q_alnum or not v_alnum:
        return 0.0
    if q_alnum == v_alnum:
        return 1.0
    if _ocr_canonical_alnum(q_alnum) == _ocr_canonical_alnum(v_alnum) and len(q_alnum) >= 5:
        return 0.96
    ratio = SequenceMatcher(None, q_alnum, v_alnum).ratio()
    return round(ratio, 3) if ratio >= 0.85 else 0.0


def score_vendor_similarity(
    query_name: Optional[str],
    vendor_legal_name: str,
    vendor_normalized_name: Optional[str] = None,
    query_tax_id: Optional[str] = None,
    vendor_tax_id: Optional[str] = None,
) -> SimilarityBreakdown:
    """Compute a calibrated probabilistic confidence score [0.0, 1.0] for a vendor match."""
    tax_sim = score_tax_id_similarity(query_tax_id, vendor_tax_id)
    if tax_sim >= 0.99:
        return SimilarityBreakdown(
            score=1.0,
            match_type="exact_tax_id",
            explanation=f"Normalized Tax ID / GSTIN match ({vendor_tax_id})",
        )

    q_norm = normalize_vendor_name(query_name)
    v_norm = vendor_normalized_name or normalize_vendor_name(vendor_legal_name)
    if not q_norm or not v_norm:
        if tax_sim >= 0.88:
            return SimilarityBreakdown(
                score=round(tax_sim, 3),
                match_type="fuzzy_tax_id",
                explanation=f"Near Tax ID match ({tax_sim * 100:.1f}% similarity to {vendor_tax_id})",
            )
        return SimilarityBreakdown(score=0.0, match_type="no_match", explanation="No vendor name or tax ID to compare")

    if q_norm == v_norm:
        return SimilarityBreakdown(
            score=1.0,
            match_type="exact_name",
            explanation="Exact match on normalized company name",
        )

    q_tokens = expand_business_tokens(query_name)
    v_tokens = expand_business_tokens(vendor_legal_name)
    q_expanded = " ".join(q_tokens) or q_norm
    v_expanded = " ".join(v_tokens) or v_norm

    if q_expanded == v_expanded:
        return SimilarityBreakdown(
            score=0.98,
            match_type="probabilistic_name",
            explanation="Normalized match after abbreviation expansion (98.0% confidence)",
        )

    # Signal 1: Character-level Gestalt ratio (on abbreviation-expanded strings)
    char_ratio = max(
        SequenceMatcher(None, q_norm, v_norm).ratio(),
        SequenceMatcher(None, q_expanded, v_expanded).ratio(),
    )

    # Signal 2: Token-set Dice coefficient (order-invariant word overlap)
    q_set, v_set = set(q_tokens), set(v_tokens)
    shared = q_set & v_set
    token_dice = (2.0 * len(shared) / (len(q_set) + len(v_set))) if (q_set and v_set) else 0.0

    # Signal 3: Token containment (handles shortened names like "Acme Industrial" vs "Acme Industrial Supplies")
    min_len = min(len(q_set), len(v_set))
    token_containment = (len(shared) / min_len) if min_len > 0 else 0.0

    # Signal 4: Mean best per-token fuzzy similarity (handles typos and stem variations)
    shorter, longer = (q_tokens, v_tokens) if len(q_tokens) <= len(v_tokens) else (v_tokens, q_tokens)
    if shorter and longer:
        per_token = [
            max(SequenceMatcher(None, s_tok, l_tok).ratio() for l_tok in longer)
            for s_tok in shorter
        ]
        fuzzy_token_mean = sum(per_token) / len(per_token)
    else:
        fuzzy_token_mean = char_ratio

    composite = (
        0.38 * char_ratio
        + 0.28 * token_dice
        + 0.17 * token_containment
        + 0.17 * fuzzy_token_mean
    )

    # Boost when all tokens of a 2+ word query are contained in the vendor name (or vice versa)
    if token_containment >= 0.99 and len(shared) >= 2:
        composite = max(composite, 0.91)
    # Boost when order-invariant tokens are identical
    if token_dice >= 0.99 and len(shared) >= 2:
        composite = max(composite, 0.97)
    # Boost when fuzzy Tax ID also aligns
    if tax_sim >= 0.88:
        composite = max(composite, round(0.65 * tax_sim + 0.35 * composite, 3))

    score = round(min(1.0, max(0.0, composite)), 3)
    return SimilarityBreakdown(
        score=score,
        match_type="probabilistic_name",
        explanation=(
            f"Probabilistic similarity {score * 100:.1f}% "
            f"(token overlap {token_dice * 100:.0f}%, char sim {char_ratio * 100:.0f}%"
            + (f", tax ID sim {tax_sim * 100:.0f}%" if tax_sim > 0 else "")
            + ")"
        ),
    )


def _extract_number_groups(text: str) -> tuple[int, ...]:
    """Extract integer groups so 'PO-2026-01' and 'PO/2026/001' both yield (2026, 1)."""
    return tuple(int(m) for m in re.findall(r"\d+", text or ""))


def score_po_reference_similarity(
    invoice_po_ref: Optional[str],
    db_po_number: Optional[str],
) -> SimilarityBreakdown:
    """Compare two PO references across formatting variations, leading zeros, and OCR typos."""
    if not invoice_po_ref or not db_po_number:
        return SimilarityBreakdown(0.0, "no_po_ref", "No PO reference on invoice")

    raw_q = invoice_po_ref.strip().upper()
    raw_db = db_po_number.strip().upper()
    if raw_q == raw_db:
        return SimilarityBreakdown(1.0, "exact_po_ref", f"Exact PO reference '{db_po_number}'")

    norm_q = normalize_identifier(raw_q)
    norm_db = normalize_identifier(raw_db)
    if norm_q and norm_q == norm_db:
        return SimilarityBreakdown(
            0.98,
            "normalized_po_ref",
            f"Normalized PO identifier match '{invoice_po_ref}' → '{db_po_number}' (98.0% confidence)",
        )

    # Numeric segment equality (e.g., "PO-2026-01" or "Order #2026/1" vs "PO-2026-001")
    nums_q = _extract_number_groups(raw_q)
    nums_db = _extract_number_groups(raw_db)
    if nums_q and nums_db and nums_q == nums_db:
        # Verify non-prefix alpha qualifiers match (so PO-2026-IND-001 is distinct from PO-2026-001)
        alpha_q = set(re.findall(r"[A-Z]{2,}", re.sub(r"^(?:PO|P\.O\.|ORDER|REF|NO)+", "", raw_q)))
        alpha_db = set(re.findall(r"[A-Z]{2,}", re.sub(r"^(?:PO|P\.O\.|ORDER|REF|NO)+", "", raw_db)))
        if alpha_q == alpha_db:
            return SimilarityBreakdown(
                0.95,
                "numeric_segment_po_ref",
                f"Numeric segment match '{invoice_po_ref}' → '{db_po_number}' (95.0% confidence)",
            )

    # OCR look-alike canonical match (e.g., "PO-2O26-OO1" vs "PO-2026-001")
    if norm_q and norm_db and _ocr_canonical_alnum(norm_q) == _ocr_canonical_alnum(norm_db):
        return SimilarityBreakdown(
            0.94,
            "ocr_canonical_po_ref",
            f"OCR-normalized PO reference match '{invoice_po_ref}' → '{db_po_number}' (94.0% confidence)",
        )

    # Trailing sequence match when invoice omits year prefix (e.g., "PO #001" vs "PO-2026-001")
    if len(nums_q) == 1 and len(nums_db) >= 2 and nums_q[0] == nums_db[-1]:
        return SimilarityBreakdown(
            0.84,
            "suffix_number_po_ref",
            f"Trailing PO sequence number match '{invoice_po_ref}' → '{db_po_number}' (84.0% confidence)",
        )

    # Character similarity on normalized identifiers
    if norm_q and norm_db:
        ratio = max(
            SequenceMatcher(None, norm_q, norm_db).ratio(),
            SequenceMatcher(None, _ocr_canonical_alnum(norm_q), _ocr_canonical_alnum(norm_db)).ratio(),
        )
        if ratio >= 0.72:
            sc = round(0.90 * ratio, 3)
            return SimilarityBreakdown(
                sc,
                "fuzzy_po_ref",
                f"Fuzzy PO reference similarity {sc * 100:.1f}% ('{invoice_po_ref}' vs '{db_po_number}')",
            )

    return SimilarityBreakdown(0.0, "no_po_ref_match", "PO reference does not match")


def score_description_similarity(desc_a: Optional[str], desc_b: Optional[str]) -> float:
    """Probabilistic similarity [0.0, 1.0] between two line-item descriptions."""
    toks_a = expand_business_tokens(desc_a)
    toks_b = expand_business_tokens(desc_b)
    if not toks_a or not toks_b:
        return 0.0
    str_a, str_b = " ".join(toks_a), " ".join(toks_b)
    if str_a == str_b:
        return 1.0
    char_sim = SequenceMatcher(None, str_a, str_b).ratio()
    set_a, set_b = set(toks_a), set(toks_b)
    shared = set_a & set_b
    dice = (2.0 * len(shared) / (len(set_a) + len(set_b))) if (set_a and set_b) else 0.0
    containment = len(shared) / min(len(set_a), len(set_b))
    score = 0.45 * char_sim + 0.35 * dice + 0.20 * containment
    if containment >= 0.99 and len(shared) >= 2:
        score = max(score, 0.88)
    return round(min(1.0, score), 3)


def score_po_candidate(
    invoice: NormalizedInvoice,
    po: PurchaseOrder,
    matched_vendor: Optional[Vendor] = None,
) -> SimilarityBreakdown:
    """Multi-signal probabilistic confidence score [0.0, 1.0] for matching an invoice to a PO."""
    ref_bd = score_po_reference_similarity(invoice.po_reference, po.po_number)
    vendor_matches = matched_vendor is not None and po.vendor_id == matched_vendor.id
    vendor_conflicts = matched_vendor is not None and po.vendor_id != matched_vendor.id

    # Amount & currency signal (evaluated in PO currency after FX conversion)
    amount_score = 0.0
    if invoice.total_amount and po.total_amount > 0:
        inv_total_po = (
            convert_amount(invoice.total_amount, invoice.currency, po.currency)
            if invoice.currency and po.currency
            else invoice.total_amount
        )
        inv_sub_po = (
            convert_amount(invoice.subtotal, invoice.currency, po.currency)
            if invoice.subtotal and invoice.currency and po.currency
            else (invoice.subtotal or inv_total_po)
        )
        # Compare both total and subtotal (since POs are often net of tax)
        diff_total_pct = abs(po.total_amount - inv_total_po) / po.total_amount
        diff_sub_pct = abs(po.total_amount - inv_sub_po) / po.total_amount
        best_diff_pct = min(diff_total_pct, diff_sub_pct)
        if best_diff_pct < 0.02:
            amount_score = 1.0
        elif best_diff_pct < 0.10:
            amount_score = 0.85
        elif inv_total_po <= po.remaining_balance:
            amount_score = 0.65
        elif inv_total_po <= po.total_amount * 1.15:
            amount_score = 0.40

    # Line-item description overlap signal
    line_score = 0.0
    if invoice.line_items and po.line_items:
        best_per_line = [
            max(score_description_similarity(inv_li.description, po_li.description) for po_li in po.line_items)
            for inv_li in invoice.line_items
        ]
        line_score = sum(best_per_line) / len(best_per_line)

    # Currency signal
    currency_score = 0.0
    if invoice.currency and po.currency:
        if invoice.currency.upper() == po.currency.upper():
            currency_score = 1.0
        elif invoice.currency.upper() in SUPPORTED_CURRENCIES and po.currency.upper() in SUPPORTED_CURRENCIES:
            currency_score = 0.80

    status_score = 1.0 if po.status == "OPEN" else 0.25

    # Case A: Strong PO reference match (>= 0.80)
    if ref_bd.score >= 0.80:
        if vendor_conflicts:
            return SimilarityBreakdown(
                score=round(0.50 * ref_bd.score, 3),
                match_type="vendor_mismatch",
                explanation=f"{ref_bd.explanation} [WARNING: PO belongs to a different vendor!]",
            )
        # Blend reference confidence with supporting vendor/amount/line signals
        support = 0.0
        if vendor_matches:
            support += 0.04
        if amount_score >= 0.65:
            support += 0.02
        if line_score >= 0.65:
            support += 0.02
        final_sc = round(min(1.0, ref_bd.score + support), 3)
        return SimilarityBreakdown(
            score=final_sc,
            match_type=ref_bd.match_type,
            explanation=(
                f"{ref_bd.explanation}"
                + (" · Vendor verified" if vendor_matches else "")
                + (f" · Line sim {line_score * 100:.0f}%" if line_score > 0 else "")
            ),
        )

    # Case B: No PO reference (or weak PO reference) — score open POs of the matched vendor
    if not vendor_matches:
        return SimilarityBreakdown(0.0, "no_match", "No PO reference or vendor match")

    # Weighted probabilistic blend across currency, amount, status, line items, and any partial ref
    inferred = (
        0.25 * currency_score
        + 0.45 * amount_score
        + 0.25 * status_score
        + 0.15 * line_score
        + 0.15 * ref_bd.score
    )
    final_inferred = round(min(0.95, inferred), 3)
    return SimilarityBreakdown(
        score=final_inferred,
        match_type="probabilistic_inferred",
        explanation=(
            f"Probabilistic PO match {final_inferred * 100:.1f}% "
            f"(amount fit {amount_score * 100:.0f}%, line-item sim {line_score * 100:.0f}%, "
            f"currency {po.currency}, status {po.status})"
        ),
    )
