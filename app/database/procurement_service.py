"""
Procurement management service for creating and updating Vendors and Purchase Orders.

Uses the probabilistic similarity engine (``app/processing/probabilistic_matcher.py``)
to detect near-duplicate vendors and differently formatted PO numbers with
calibrated confidence scores, while allowing explicit operator confirmation when
registering distinct entities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import APException
from app.database.entities import AuditLog, POLineItem, PurchaseOrder, Vendor
from app.processing.currency import SUPPORTED_CURRENCIES
from app.processing.normalizer import normalize_vendor_name
from app.processing.probabilistic_matcher import (
    SimilarityBreakdown,
    score_po_reference_similarity,
    score_tax_id_similarity,
    score_vendor_similarity,
)
from app.utils.time import now_utc


@dataclass(frozen=True)
class NewPOLineInput:
    description: str
    quantity: float
    unit_price: float
    unit: str = "EA"


def find_similar_vendors(
    session: Session,
    legal_name: Optional[str],
    tax_id: Optional[str] = None,
    min_score: float = 0.60,
) -> list[tuple[Vendor, SimilarityBreakdown]]:
    """Return existing vendors ranked by probabilistic similarity confidence score."""
    all_vendors = session.execute(select(Vendor)).scalars().all()
    matches: list[tuple[Vendor, SimilarityBreakdown]] = []
    for v in all_vendors:
        bd = score_vendor_similarity(
            query_name=legal_name,
            vendor_legal_name=v.legal_name,
            vendor_normalized_name=v.normalized_name,
            query_tax_id=tax_id,
            vendor_tax_id=v.tax_id,
        )
        if bd.score >= min_score:
            matches.append((v, bd))
    matches.sort(key=lambda item: item[1].score, reverse=True)
    return matches


def find_similar_pos(
    session: Session,
    po_number: Optional[str],
    min_score: float = 0.72,
) -> list[tuple[PurchaseOrder, SimilarityBreakdown]]:
    """Return existing Purchase Orders ranked by probabilistic PO reference similarity."""
    all_pos = session.execute(select(PurchaseOrder)).scalars().all()
    matches: list[tuple[PurchaseOrder, SimilarityBreakdown]] = []
    for po in all_pos:
        bd = score_po_reference_similarity(po_number, po.po_number)
        if bd.score >= min_score:
            matches.append((po, bd))
    matches.sort(key=lambda item: item[1].score, reverse=True)
    return matches


def create_vendor(
    session: Session,
    *,
    legal_name: str,
    tax_id: Optional[str] = None,
    country: str = "IND",
    address: Optional[str] = None,
    bank_account: Optional[str] = None,
    is_approved: bool = True,
    allow_similar: bool = False,
    actor: str = "AP_ADMIN",
) -> Vendor:
    """Validate and create a new Vendor record with probabilistic near-duplicate detection."""
    clean_name = (legal_name or "").strip()
    if not clean_name:
        raise APException("Vendor legal company name is required.")

    norm_name = normalize_vendor_name(clean_name)
    if not norm_name:
        raise APException("Vendor legal name must contain alphanumeric characters.")

    existing_name = session.execute(
        select(Vendor).where(func.lower(Vendor.legal_name) == clean_name.lower())
    ).scalars().first()
    if existing_name is not None:
        raise APException(f"A vendor named '{existing_name.legal_name}' already exists (ID #{existing_name.id}).")

    clean_tax = (tax_id or "").strip().upper() or None
    if clean_tax:
        existing_tax = session.execute(
            select(Vendor).where(func.upper(Vendor.tax_id) == clean_tax)
        ).scalars().first()
        if existing_tax is not None:
            raise APException(
                f"Tax ID / GSTIN '{clean_tax}' is already registered to '{existing_tax.legal_name}'."
            )

    # Probabilistic similarity check across differently formatted vendor names and Tax IDs
    similar_vendors = find_similar_vendors(session, clean_name, clean_tax, min_score=0.82)
    if similar_vendors:
        top_v, top_bd = similar_vendors[0]
        # Exact normalized Tax ID conflict (e.g. "US-TAX-1011" vs "USTAX1011") is always blocked
        if clean_tax and top_v.tax_id and score_tax_id_similarity(clean_tax, top_v.tax_id) >= 0.99:
            raise APException(
                f"Normalized Tax ID / GSTIN '{clean_tax}' matches existing vendor "
                f"'{top_v.legal_name}' ({top_v.tax_id}, 100% confidence)."
            )
        if not allow_similar and top_bd.score >= 0.85:
            raise APException(
                f"Probabilistic similarity alert ({top_bd.score * 100:.1f}% confidence): "
                f"'{clean_name}' closely matches existing vendor '{top_v.legal_name}' "
                f"({top_bd.explanation}). Enable the override checkbox if this is a distinct vendor."
            )

    clean_country = (country or "IND").strip().upper() or "IND"
    vendor = Vendor(
        legal_name=clean_name,
        normalized_name=norm_name,
        tax_id=clean_tax,
        country=clean_country,
        address=(address or "").strip() or None,
        bank_account=(bank_account or "").strip() or None,
        is_approved=bool(is_approved),
    )
    session.add(vendor)
    session.flush()

    session.add(
        AuditLog(
            action="VENDOR_CREATED",
            details=(
                f"Created vendor '{vendor.legal_name}' (ID #{vendor.id}, country={vendor.country}, "
                f"tax_id={vendor.tax_id or 'N/A'}, approved={vendor.is_approved})."
            ),
            new_status="APPROVED" if vendor.is_approved else "UNAPPROVED",
            actor=(actor or "AP_ADMIN").strip() or "AP_ADMIN",
            timestamp=now_utc(),
        )
    )
    session.flush()
    return vendor


def set_vendor_approval(
    session: Session,
    *,
    vendor_id: int,
    is_approved: bool,
    actor: str = "AP_ADMIN",
) -> Vendor:
    """Update a vendor's approved/active status and record an audit log entry."""
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise APException(f"Vendor ID #{vendor_id} not found.")
    prev = "APPROVED" if vendor.is_approved else "UNAPPROVED"
    new_state = "APPROVED" if is_approved else "UNAPPROVED"
    vendor.is_approved = bool(is_approved)
    session.add(
        AuditLog(
            action="VENDOR_STATUS_UPDATED",
            details=f"Updated vendor '{vendor.legal_name}' (ID #{vendor.id}) approval: {prev} -> {new_state}.",
            previous_status=prev,
            new_status=new_state,
            actor=(actor or "AP_ADMIN").strip() or "AP_ADMIN",
            timestamp=now_utc(),
        )
    )
    session.flush()
    return vendor


def create_purchase_order(
    session: Session,
    *,
    vendor_id: int,
    po_number: str,
    currency: str = "INR",
    po_date: Optional[str] = None,
    expiry_date: Optional[str] = None,
    line_items: Sequence[NewPOLineInput | dict[str, Any]] = (),
    total_amount_override: Optional[float] = None,
    status: str = "OPEN",
    allow_similar: bool = False,
    actor: str = "AP_ADMIN",
) -> PurchaseOrder:
    """Validate and create a Purchase Order and its line items with probabilistic PO-number check."""
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise APException(f"Vendor ID #{vendor_id} not found.")

    clean_po_num = (po_number or "").strip().upper()
    if not clean_po_num:
        raise APException("Purchase Order reference number is required.")

    existing_po = session.execute(
        select(PurchaseOrder).where(func.upper(PurchaseOrder.po_number) == clean_po_num)
    ).scalars().first()
    if existing_po is not None:
        raise APException(f"Purchase Order '{clean_po_num}' already exists (ID #{existing_po.id}).")

    # Probabilistic check for differently formatted PO numbers (e.g., "PO/2026/001" vs "PO-2026-001")
    similar_pos = find_similar_pos(session, clean_po_num, min_score=0.90)
    if similar_pos and not allow_similar:
        top_po, top_bd = similar_pos[0]
        raise APException(
            f"Probabilistic PO similarity alert ({top_bd.score * 100:.1f}% confidence): "
            f"'{clean_po_num}' matches existing PO '{top_po.po_number}' ({top_bd.explanation}). "
            "Enable the override checkbox if this is an intentional distinct PO."
        )

    clean_currency = (currency or "INR").strip().upper()
    if clean_currency not in SUPPORTED_CURRENCIES:
        raise APException(
            f"Unsupported currency '{clean_currency}'. Choose from: {', '.join(SUPPORTED_CURRENCIES)}."
        )

    clean_status = (status or "OPEN").strip().upper()
    if clean_status not in {"OPEN", "CLOSED"}:
        raise APException("Purchase Order status must be OPEN or CLOSED.")

    parsed_lines: list[NewPOLineInput] = []
    for idx, raw in enumerate(line_items, start=1):
        if isinstance(raw, NewPOLineInput):
            item = raw
        else:
            desc = str(raw.get("description") or "").strip()
            if not desc:
                continue
            try:
                qty = float(raw.get("quantity", 1.0))
                price = float(raw.get("unit_price", 0.0))
            except (TypeError, ValueError) as exc:
                raise APException(f"Line item #{idx} has invalid quantity or unit price.") from exc
            unit = str(raw.get("unit") or "EA").strip().upper() or "EA"
            item = NewPOLineInput(description=desc, quantity=qty, unit_price=price, unit=unit)

        if not item.description.strip():
            continue
        if item.quantity <= 0:
            raise APException(f"Line item '{item.description}' quantity must be greater than zero.")
        if item.unit_price < 0:
            raise APException(f"Line item '{item.description}' unit price cannot be negative.")
        parsed_lines.append(item)

    lines_sum = round(sum(round(li.quantity * li.unit_price, 2) for li in parsed_lines), 2)
    override_val = round(float(total_amount_override), 2) if total_amount_override is not None else 0.0
    total_amount = max(lines_sum, override_val)
    if total_amount <= 0:
        raise APException(
            "Purchase Order total amount must be greater than zero (add line items or specify a positive PO budget)."
        )

    effective_po_date = (po_date or "").strip() or date.today().isoformat()
    effective_expiry = (expiry_date or "").strip() or None

    po = PurchaseOrder(
        po_number=clean_po_num,
        vendor_id=vendor.id,
        po_date=effective_po_date,
        expiry_date=effective_expiry,
        currency=clean_currency,
        total_amount=total_amount,
        billed_amount=0.0,
        remaining_balance=total_amount,
        status=clean_status,
    )
    session.add(po)
    session.flush()

    for line_no, li in enumerate(parsed_lines, start=1):
        line_total = round(li.quantity * li.unit_price, 2)
        session.add(
            POLineItem(
                po_id=po.id,
                line_number=line_no,
                description=li.description.strip(),
                quantity=round(li.quantity, 4),
                unit=li.unit.strip().upper() or "EA",
                unit_price=round(li.unit_price, 2),
                line_total=line_total,
            )
        )

    session.add(
        AuditLog(
            action="PO_CREATED",
            details=(
                f"Created PO '{po.po_number}' for vendor '{vendor.legal_name}' "
                f"({po.currency} {po.total_amount:,.2f}, {len(parsed_lines)} line item(s), status={po.status})."
            ),
            new_status=po.status,
            actor=(actor or "AP_ADMIN").strip() or "AP_ADMIN",
            timestamp=now_utc(),
        )
    )
    session.flush()
    return po
