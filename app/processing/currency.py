"""
Multi-currency resolution and Foreign Exchange (FX) conversion engine.

Supports Indian Rupees (INR / ₹ / Rs.) as the primary dashboard reporting
currency and converts USD (and other major currencies) into INR or into a
matched Purchase Order's currency for 3-way matching and PO balance control.
"""
from __future__ import annotations

import re
from typing import Optional

from app.core.schemas import ExtractedInvoice, NormalizedInvoice
from app.database.entities import PurchaseOrder, Vendor

SUPPORTED_CURRENCIES: tuple[str, ...] = (
    "INR",
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "SGD",
    "AED",
    "HKD",
    "NZD",
    "JPY",
    "CNY",
)

CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "SGD": "S$",
    "AED": "AED ",
    "HKD": "HK$",
    "NZD": "NZ$",
    "JPY": "¥",
    "CNY": "¥",
}

# Default deterministic conversion rates to INR (1 unit of currency = X INR).
# USD->INR is runtime-configurable via Settings & Rules (persisted in AppConfig).
_DEFAULT_RATES_TO_INR: dict[str, float] = {
    "INR": 1.00,
    "USD": 84.00,
    "EUR": 91.00,
    "GBP": 106.00,
    "CAD": 61.00,
    "AUD": 55.00,
    "SGD": 62.50,
    "AED": 22.87,
    "HKD": 10.78,
    "NZD": 50.50,
    "JPY": 0.56,
    "CNY": 11.60,
}

_runtime_rates_to_inr: dict[str, float] = dict(_DEFAULT_RATES_TO_INR)


def get_usd_to_inr_rate() -> float:
    """Return the active USD -> INR exchange rate."""
    return float(_runtime_rates_to_inr.get("USD", 84.00))


def set_usd_to_inr_rate(rate: float) -> float:
    """Update the active runtime USD -> INR exchange rate."""
    val = round(float(rate), 4)
    if val <= 0:
        raise ValueError("USD to INR exchange rate must be greater than zero.")
    _runtime_rates_to_inr["USD"] = val
    return val


def get_rate_to_inr(currency: Optional[str]) -> float:
    """Return how many INR equal 1 unit of ``currency`` (defaults to 1.0 for INR)."""
    code = (currency or "INR").strip().upper()
    return float(_runtime_rates_to_inr.get(code, 1.0))


def all_rates_to_inr() -> dict[str, float]:
    """Snapshot of all active currency->INR conversion rates."""
    return dict(_runtime_rates_to_inr)


def exchange_rate(from_currency: Optional[str], to_currency: Optional[str] = "INR") -> float:
    """Return the multiplicative conversion rate from ``from_currency`` to ``to_currency``."""
    src = (from_currency or "INR").strip().upper()
    dst = (to_currency or "INR").strip().upper()
    if src == dst:
        return 1.0
    src_to_inr = get_rate_to_inr(src)
    dst_to_inr = get_rate_to_inr(dst)
    if dst_to_inr <= 0:
        return 1.0
    return src_to_inr / dst_to_inr


def convert_amount(
    amount: float,
    from_currency: Optional[str],
    to_currency: Optional[str] = "INR",
) -> float:
    """Convert a monetary amount from ``from_currency`` into ``to_currency``."""
    rate = exchange_rate(from_currency, to_currency)
    return round(float(amount) * rate, 2)


def convert_to_inr(amount: Optional[float], from_currency: Optional[str]) -> Optional[float]:
    """Convert an optional amount to INR (assuming USD if currency is unconfirmed '$')."""
    if amount is None:
        return None
    code = (from_currency or "INR").strip().upper()
    return convert_amount(amount, code, "INR")


def format_inr(amount: Optional[float]) -> str:
    """Format an INR amount with the ₹ symbol."""
    if amount is None:
        return "—"
    return f"₹{float(amount):,.2f}"


def format_native_and_inr(amount: Optional[float], currency: Optional[str]) -> str:
    """Format an amount in its native currency alongside its converted INR value."""
    if amount is None:
        return "N/A"
    code = (currency or "").strip().upper()
    if not code:
        return f"{amount:,.2f} (Currency unconfirmed)"
    if code == "INR":
        return f"₹{amount:,.2f} (INR)"
    inr_val = convert_amount(amount, code, "INR")
    return f"{code} {amount:,.2f} (≈ ₹{inr_val:,.2f})"


_COUNTRY_TO_CURRENCY: dict[str, str] = {
    "IND": "INR",
    "IN": "INR",
    "INDIA": "INR",
    "USA": "USD",
    "US": "USD",
    "UNITED STATES": "USD",
    "UK": "GBP",
    "GBR": "GBP",
    "GB": "GBP",
    "UNITED KINGDOM": "GBP",
    "CAN": "CAD",
    "CA": "CAD",
    "CANADA": "CAD",
    "AUS": "AUD",
    "AU": "AUD",
    "AUSTRALIA": "AUD",
    "SGP": "SGD",
    "SG": "SGD",
    "SINGAPORE": "SGD",
    "ARE": "AED",
    "UAE": "AED",
    "HKG": "HKD",
    "HK": "HKD",
    "NZL": "NZD",
    "NZ": "NZD",
    "DEU": "EUR",
    "FRA": "EUR",
    "EUR": "EUR",
    "JPN": "JPY",
    "CHN": "CNY",
}

_DOLLAR_CURRENCIES = {"USD", "CAD", "AUD", "NZD", "HKD", "SGD"}


def resolve_invoice_currency(
    extracted: ExtractedInvoice,
    normalized: NormalizedInvoice,
    matched_po: Optional[PurchaseOrder] = None,
    matched_vendor: Optional[Vendor] = None,
) -> Optional[str]:
    """Resolve an invoice's currency using document symbols, matched PO, and vendor context.

    Eliminates false 'bare $ is ambiguous' warnings when an invoice uses '$' or '₹'/'Rs.'
    and can be confirmed from the document, the matched PO, or the vendor record.
    """
    if normalized.currency:
        return normalized.currency

    raw_text = extracted.raw_text or ""
    ev = (extracted.currency.evidence or "") if extracted.currency else ""
    val = (extracted.currency.value or "") if extracted.currency else ""
    combined = f"{val} {ev} {raw_text}"

    # 1. Explicit Indian Rupee markers in evidence or text
    if "₹" in combined or re.search(r"\b(?:INR|Rs\.?|Rupees?)\b", combined, re.IGNORECASE):
        resolved = "INR"
    # 2. Dollar symbol present — confirm against matched PO or matched vendor
    elif "$" in ev or "$" in val or "$" in raw_text:
        if matched_po and (matched_po.currency or "").upper() in _DOLLAR_CURRENCIES:
            resolved = matched_po.currency.upper()
        elif matched_vendor and matched_vendor.country:
            v_curr = _COUNTRY_TO_CURRENCY.get(matched_vendor.country.strip().upper())
            resolved = v_curr if v_curr in _DOLLAR_CURRENCIES else "USD"
        elif matched_po is not None:
            resolved = "USD"
        else:
            resolved = None
    # 3. No conflicting symbol, fall back to matched PO currency or vendor country currency
    elif matched_po and matched_po.currency:
        resolved = matched_po.currency.strip().upper()
    elif matched_vendor and matched_vendor.country:
        resolved = _COUNTRY_TO_CURRENCY.get(matched_vendor.country.strip().upper())
    else:
        resolved = None

    if resolved:
        normalized.currency = resolved
        if extracted.currency and extracted.currency.value is None:
            extracted.currency.value = resolved
            if not extracted.currency.confidence or extracted.currency.confidence < 0.85:
                extracted.currency.confidence = 0.90
            if not extracted.currency.evidence:
                extracted.currency.evidence = f"{resolved} (confirmed from PO/vendor context)"
    return resolved
