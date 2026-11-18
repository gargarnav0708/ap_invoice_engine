"""
Data normalisation functions for financial values, vendor names, dates, currencies, and identifiers.
"""
import re
from typing import Optional, Any
from datetime import datetime
from dateutil import parser as date_parser

from app.core.schemas import ExtractedInvoice, NormalizedInvoice, NormalizedLineItem
from app.utils.logging import get_logger

logger = get_logger("normalizer")

LEGAL_SUFFIXES = [
    r"\binc(?:\.|\b)",
    r"\bllc(?:\.|\b)",
    r"\bltd(?:\.|\b)",
    r"\bcorp(?:\.|\b)",
    r"\bcorporation\b",
    r"\bco(?:\.|\b)",
    r"\bcompany\b",
    r"\bgmbh\b",
    r"\bag\b",
    r"\bplc\b",
    r"\bs\.?a\.?\b",
]


def normalize_vendor_name(name: Optional[str]) -> str:
    """Normalizes vendor name by removing corporate suffixes, special chars, and extra whitespace."""
    if not name:
        return ""
    cleaned = name.lower()
    for suffix_pat in LEGAL_SUFFIXES:
        cleaned = re.sub(suffix_pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_currency(curr: Optional[str]) -> Optional[str]:
    """Standardizes currency only when the source unambiguously identifies it."""
    if not curr:
        return None
    curr_upper = curr.strip().upper()
    mapping = {
        "US$": "USD", "USD": "USD",
        "€": "EUR", "EUR": "EUR",
        "£": "GBP", "GBP": "GBP",
        "HK$": "HKD", "HKD": "HKD", "CA$": "CAD", "CAD": "CAD",
        "A$": "AUD", "AUD": "AUD", "NZ$": "NZD", "NZD": "NZD",
        "JPY": "JPY", "CNY": "CNY", "INR": "INR", "₹": "INR",
    }
    if curr_upper in mapping:
        return mapping[curr_upper]
    return curr_upper if re.fullmatch(r"[A-Z]{3}", curr_upper) else None


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    """Parses date string into standard ISO 8601 (YYYY-MM-DD)."""
    if not date_str:
        return None
    for date_format in (
        "%B %d, %Y", "%B %d,%Y", "%b %d, %Y", "%b %d,%Y",
        "%B %d %Y", "%b %d %Y",
    ):
        try:
            return datetime.strptime(date_str.strip(), date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        dt = date_parser.parse(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        # Fallback regex if dateutil fails
        match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", date_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return date_str.strip()


def normalize_identifier(identifier: Optional[str]) -> str:
    """Normalizes invoice numbers, PO numbers, and tax IDs (removes prefixes, symbols, spaces)."""
    if not identifier:
        return ""
    # Strip common prefixes like 'PO#', 'INV-', 'NO.'
    cleaned = re.sub(r"^(?:po|inv|invoice|order|#|no\.?|ref[:#]?)+", "", identifier.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"[^A-Za-z0-9]", "", cleaned)
    return cleaned.upper()


def normalize_amount(amount_val: Any) -> Optional[float]:
    """Converts amount string or numeric to rounded float."""
    if amount_val is None:
        return None
    if isinstance(amount_val, (int, float)):
        return round(float(amount_val), 2)
    try:
        cleaned = re.sub(r"[^\d.-]", "", str(amount_val))
        return round(float(cleaned), 2)
    except Exception:
        return None


def normalize_invoice(extracted: ExtractedInvoice) -> NormalizedInvoice:
    """Normalizes all fields of an ExtractedInvoice."""
    norm_lines = []
    for item in extracted.line_items:
        desc = (item.description.value or "").strip()
        qty = normalize_amount(item.quantity.value) or 1.0
        price = normalize_amount(item.unit_price.value) or 0.0
        ltotal = normalize_amount(item.line_total.value) or round(qty * price, 2)
        tax = normalize_amount(item.tax_amount.value) or 0.0

        norm_lines.append(
            NormalizedLineItem(
                description=desc,
                quantity=qty,
                unit=(item.unit.value or "EA").strip().upper(),
                unit_price=price,
                tax_amount=tax,
                line_total=ltotal
            )
        )

    return NormalizedInvoice(
        vendor_name=extracted.vendor_name.value,
        normalized_vendor_name=normalize_vendor_name(extracted.vendor_name.value),
        vendor_tax_id=extracted.vendor_tax_id.value,
        invoice_number=extracted.invoice_number.value,
        normalized_invoice_number=normalize_identifier(extracted.invoice_number.value),
        invoice_date=normalize_date(extracted.invoice_date.value),
        due_date=normalize_date(extracted.due_date.value),
        paid_date=normalize_date(extracted.paid_date.value),
        po_reference=extracted.po_reference.value,
        normalized_po_reference=normalize_identifier(extracted.po_reference.value),
        currency=normalize_currency(extracted.currency.value),
        subtotal=normalize_amount(extracted.subtotal.value),
        tax_amount=normalize_amount(extracted.tax_amount.value) or 0.0,
        total_amount=normalize_amount(extracted.total_amount.value),
        line_items=norm_lines
    )
