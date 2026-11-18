"""
Multi-format accounting and audit export helpers.

Inspired by clean in-memory accounting exporters (Generic Audit CSV, Xero Bills
CSV, QuickBooks Online Bills CSV, and structured JSON), enriched with our
3-way PO matching, per-field confidence, and human-review override metadata.
All outputs are generated in memory as UTF-8 strings with no temporary files.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Iterable, Sequence

from app.database.entities import Invoice
from app.ui.presentation import invoice_display_status
from app.utils.time import now_utc

GENERIC_CSV_HEADERS: tuple[str, ...] = (
    "invoice_id",
    "invoice_number",
    "vendor",
    "po_reference",
    "invoice_date",
    "due_date",
    "paid_date",
    "subtotal",
    "tax_amount",
    "total_amount",
    "currency",
    "extraction_confidence",
    "effective_decision",
    "automated_decision",
    "manual_override_by",
    "manual_override_reason",
    "original_filename",
    "exported_at_utc",
)

XERO_BILLS_CSV_HEADERS: tuple[str, ...] = (
    "*ContactName",
    "*InvoiceNumber",
    "*InvoiceDate",
    "*DueDate",
    "*Description",
    "*Quantity",
    "*UnitAmount",
    "*AccountCode",
    "*TaxType",
    "Currency",
    "POReference",
    "APDecision",
)

QUICKBOOKS_BILLS_CSV_HEADERS: tuple[str, ...] = (
    "Vendor",
    "Invoice Number",
    "Invoice Date",
    "Due Date",
    "Description",
    "Amount",
    "Currency",
    "Account",
    "PO Number",
    "AP Decision",
)


def _vendor_name(invoice: Invoice) -> str:
    return invoice.vendor.legal_name if invoice.vendor else "Unmatched Vendor"


def _effective_status(invoice: Invoice) -> str:
    return invoice_display_status(
        invoice.final_decision, invoice.processing_status, invoice.manual_override_decision
    )


def _fmt_num(val: float | None) -> str:
    return f"{val:.2f}" if val is not None else ""


def to_generic_csv(invoices: Iterable[Invoice]) -> str:
    """Serialise invoices to a flat audit CSV string with header row."""
    exported_at = now_utc().isoformat()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(GENERIC_CSV_HEADERS)
    for inv in invoices:
        writer.writerow(
            [
                inv.id,
                inv.invoice_number or "",
                _vendor_name(inv),
                inv.po_reference or "",
                inv.invoice_date or "",
                inv.due_date or "",
                inv.paid_date or "",
                _fmt_num(inv.subtotal),
                _fmt_num(inv.tax_amount),
                _fmt_num(inv.total_amount),
                inv.currency or "",
                f"{(inv.extraction_confidence or 0.0):.4f}",
                _effective_status(inv),
                inv.final_decision or "",
                inv.manual_override_by or "",
                inv.manual_override_reason or "",
                inv.original_filename or "",
                exported_at,
            ]
        )
    return buf.getvalue()


def to_xero_bills_csv(invoices: Iterable[Invoice], default_account_code: str = "200") -> str:
    """Format one or more invoices as a Xero Bills CSV import string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(XERO_BILLS_CSV_HEADERS)
    for inv in invoices:
        vendor = _vendor_name(inv)
        inv_num = inv.invoice_number or f"INV-{inv.id}"
        inv_date = inv.invoice_date or ""
        due_date = inv.due_date or ""
        currency = inv.currency or ""
        po_ref = inv.po_reference or (inv.matched_po.po_number if inv.matched_po else "")
        decision = _effective_status(inv)
        items = list(inv.line_items or [])
        if not items:
            writer.writerow(
                [
                    vendor,
                    inv_num,
                    inv_date,
                    due_date,
                    f"Invoice {inv_num}",
                    1,
                    _fmt_num(inv.total_amount),
                    default_account_code,
                    "NONE",
                    currency,
                    po_ref,
                    decision,
                ]
            )
        else:
            for item in items:
                writer.writerow(
                    [
                        vendor,
                        inv_num,
                        inv_date,
                        due_date,
                        item.description,
                        item.quantity if item.quantity is not None else 1,
                        _fmt_num(item.unit_price if item.unit_price is not None else item.line_total),
                        default_account_code,
                        "NONE",
                        currency,
                        po_ref,
                        decision,
                    ]
                )
    return buf.getvalue()


def to_quickbooks_bills_csv(
    invoices: Iterable[Invoice], default_account: str = "Accounts Payable"
) -> str:
    """Format one or more invoices as a QuickBooks Online Bills CSV import string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(QUICKBOOKS_BILLS_CSV_HEADERS)
    for inv in invoices:
        vendor = _vendor_name(inv)
        inv_num = inv.invoice_number or f"INV-{inv.id}"
        inv_date = inv.invoice_date or ""
        due_date = inv.due_date or ""
        currency = inv.currency or ""
        po_ref = inv.po_reference or (inv.matched_po.po_number if inv.matched_po else "")
        decision = _effective_status(inv)
        items = list(inv.line_items or [])
        if not items:
            writer.writerow(
                [
                    vendor,
                    inv_num,
                    inv_date,
                    due_date,
                    f"Invoice {inv_num}",
                    _fmt_num(inv.total_amount),
                    currency,
                    default_account,
                    po_ref,
                    decision,
                ]
            )
        else:
            for item in items:
                writer.writerow(
                    [
                        vendor,
                        inv_num,
                        inv_date,
                        due_date,
                        item.description,
                        _fmt_num(item.line_total),
                        currency,
                        default_account,
                        po_ref,
                        decision,
                    ]
                )
    return buf.getvalue()


def to_audit_json(invoices: Sequence[Invoice]) -> str:
    """Serialise one or more invoices (including line items and override metadata) to JSON."""
    exported_at = now_utc().isoformat()
    payload = []
    for inv in invoices:
        payload.append(
            {
                "invoice_id": inv.id,
                "original_filename": inv.original_filename,
                "file_hash": inv.file_hash,
                "invoice_number": inv.invoice_number,
                "vendor": _vendor_name(inv) if inv.vendor else None,
                "po_reference": inv.po_reference,
                "matched_po_number": inv.matched_po.po_number if inv.matched_po else None,
                "invoice_date": inv.invoice_date,
                "due_date": inv.due_date,
                "paid_date": inv.paid_date,
                "currency": inv.currency,
                "subtotal": inv.subtotal,
                "tax_amount": inv.tax_amount,
                "total_amount": inv.total_amount,
                "extraction_confidence": inv.extraction_confidence,
                "processing_status": inv.processing_status,
                "automated_decision": inv.final_decision,
                "effective_decision": _effective_status(inv),
                "manual_override": (
                    {
                        "decision": inv.manual_override_decision,
                        "by": inv.manual_override_by,
                        "reason": inv.manual_override_reason,
                        "at": inv.manual_override_at.isoformat() if inv.manual_override_at else None,
                    }
                    if inv.manual_override_decision
                    else None
                ),
                "line_items": [
                    {
                        "line_number": li.line_number,
                        "description": li.description,
                        "quantity": li.quantity,
                        "unit": li.unit,
                        "unit_price": li.unit_price,
                        "line_total": li.line_total,
                    }
                    for li in (inv.line_items or [])
                ],
                "exported_at_utc": exported_at,
            }
        )
    return json.dumps(payload if len(payload) != 1 else payload[0], indent=2)
