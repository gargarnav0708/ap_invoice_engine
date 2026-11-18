"""
Phase 2 test suite covering:
- Database-backed invoice-history & invoice-details pagination, filtering, counts,
  page boundaries, and deterministic ordering when timestamps collide.
- Live Processing View trace parsing, stage merging, and terminal vs running/stale
  polling behaviour.
- Document preview page clamping, zoom-to-DPI bounding, single-page PDF rasterisation,
  raster image support, and missing/invalid file explanations.
- Consistent decision/status presentation (including FAILED runs) and local-only test
  runner safeguards.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import ui as ui_config
from app.database.entities import Invoice, ProcessingRun, Vendor
from app.database.queries import (
    InvoiceFilters,
    count_invoices,
    list_recent_runs,
    paginate_invoices,
    resolve_page,
    status_counts,
)
from app.ui.document_preview import (
    MISSING_FILE_MESSAGE,
    clamp_page,
    image_html,
    inspect_document,
    normalise_zoom,
    render_image_png,
    render_pdf_page_png,
    zoom_to_dpi,
)
from app.ui.presentation import (
    Kpi,
    badge_html,
    invoice_display_status,
    kpi_grid_html,
    run_display_status,
    status_meta,
    table_html,
)
from app.ui.run_trace import (
    build_run_trace,
    format_duration,
    is_stale,
    is_terminal,
    parse_stages_log,
    should_poll,
)
from app.ui.settings_view import is_local_test_runner_allowed, truncate_test_output
from app.ui.theme import TOKENS, style_figure


def _seed_invoices(session):
    """Create a deterministic set of invoices with shared timestamps and varied statuses."""
    vendors = session.query(Vendor).order_by(Vendor.id).all()
    v_acme, v_apex = vendors[0], vendors[1]
    fixed_ts = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    older_ts = datetime(2026, 5, 1, 8, 30, 0, tzinfo=timezone.utc)

    rows = [
        # Three invoices with the exact same timestamp to test deterministic id tie-breaker
        Invoice(
            original_filename="alpha_clean.pdf",
            file_hash="h1",
            file_path="/tmp/nonexistent_1.pdf",
            vendor_id=v_acme.id,
            invoice_number="INV-2026-101",
            po_reference="PO-2026-001",
            currency="USD",
            total_amount=250.0,
            processing_status="COMPLETED",
            final_decision="APPROVED",
            created_at=fixed_ts,
        ),
        Invoice(
            original_filename="beta_review.pdf",
            file_hash="h2",
            file_path="/tmp/nonexistent_2.pdf",
            vendor_id=v_acme.id,
            invoice_number="INV-2026-102",
            po_reference="PO-2026-001",
            currency="USD",
            total_amount=500.0,
            processing_status="COMPLETED",
            final_decision="NEEDS_REVIEW",
            created_at=fixed_ts,
        ),
        Invoice(
            original_filename="gamma_blocked.pdf",
            file_hash="h3",
            file_path="/tmp/nonexistent_3.pdf",
            vendor_id=v_apex.id,
            invoice_number="INV-2026-103",
            po_reference="PO-2026-002",
            currency="USD",
            total_amount=1200.0,
            processing_status="COMPLETED",
            final_decision="BLOCKED",
            created_at=fixed_ts,
        ),
        # Failed run (processing_status == FAILED, final_decision fallback NEEDS_REVIEW)
        Invoice(
            original_filename="delta_failed.pdf",
            file_hash="h4",
            file_path="/tmp/nonexistent_4.pdf",
            vendor_id=v_apex.id,
            invoice_number="INV-2026-104",
            po_reference=None,
            currency="USD",
            total_amount=75.0,
            processing_status="FAILED",
            final_decision="NEEDS_REVIEW",
            created_at=older_ts,
        ),
        # Manual override from NEEDS_REVIEW -> APPROVED
        Invoice(
            original_filename="epsilon_overridden.pdf",
            file_hash="h5",
            file_path="/tmp/nonexistent_5.pdf",
            vendor_id=v_acme.id,
            invoice_number="INV-2026-105%",
            po_reference="PO-2026-001",
            currency="USD",
            total_amount=310.0,
            processing_status="COMPLETED",
            final_decision="NEEDS_REVIEW",
            manual_override_decision="APPROVED",
            manual_override_by="ANALYST_1",
            manual_override_reason="Verified with vendor",
            created_at=older_ts - timedelta(days=1),
        ),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def test_pagination_deterministic_order_and_boundaries(test_db_session):
    created = _seed_invoices(test_db_session)
    assert len(created) == 5

    # Page 1 of size 2 must return the 2 highest IDs among the newest timestamp
    page1, info1 = paginate_invoices(test_db_session, InvoiceFilters(), requested_page=1, page_size=2)
    assert info1.total == 5
    assert info1.total_pages == 3
    assert info1.page == 1
    assert info1.first_row == 1 and info1.last_row == 2
    assert info1.has_prev is False and info1.has_next is True
    assert [inv.invoice_number for inv in page1] == ["INV-2026-103", "INV-2026-102"]

    # Page 2 of size 2 must not overlap with page 1 even though 101, 102, 103 share created_at
    page2, info2 = paginate_invoices(test_db_session, InvoiceFilters(), requested_page=2, page_size=2)
    assert [inv.invoice_number for inv in page2] == ["INV-2026-101", "INV-2026-104"]

    # Requesting a page beyond total_pages clamps to the last page
    page_last, info_last = paginate_invoices(test_db_session, InvoiceFilters(), requested_page=99, page_size=2)
    assert info_last.page == 3
    assert info_last.has_next is False and info_last.has_prev is True
    assert [inv.invoice_number for inv in page_last] == ["INV-2026-105%"]

    # Requesting page <= 0 clamps to page 1
    info_zero = resolve_page(0, 10, 5)
    assert info_zero.page == 1


def test_pagination_filters_search_vendor_status_and_empty(test_db_session):
    _seed_invoices(test_db_session)
    vendors = test_db_session.query(Vendor).order_by(Vendor.id).all()
    v_acme, v_apex = vendors[0], vendors[1]

    # Status filter respects manual_override_decision and FAILED processing_status
    approved, info_app = paginate_invoices(
        test_db_session, InvoiceFilters(status="APPROVED"), requested_page=1, page_size=10
    )
    assert info_app.total == 2
    assert {inv.invoice_number for inv in approved} == {"INV-2026-101", "INV-2026-105%"}

    failed, info_fail = paginate_invoices(
        test_db_session, InvoiceFilters(status="FAILED"), requested_page=1, page_size=10
    )
    assert info_fail.total == 1
    assert failed[0].invoice_number == "INV-2026-104"

    # Needs review excludes the failed run and the overridden invoice
    review, info_rev = paginate_invoices(
        test_db_session, InvoiceFilters(status="NEEDS_REVIEW"), requested_page=1, page_size=10
    )
    assert info_rev.total == 1
    assert review[0].invoice_number == "INV-2026-102"

    # Vendor filter
    apex_rows, info_apex = paginate_invoices(
        test_db_session, InvoiceFilters(vendor_id=v_apex.id), requested_page=1, page_size=10
    )
    assert info_apex.total == 2
    assert {inv.invoice_number for inv in apex_rows} == {"INV-2026-103", "INV-2026-104"}

    # Search filter with SQL wildcard character '%' must match literally, not everything
    literal_pct, info_pct = paginate_invoices(
        test_db_session, InvoiceFilters(search="105%"), requested_page=1, page_size=10
    )
    assert info_pct.total == 1
    assert literal_pct[0].invoice_number == "INV-2026-105%"

    # Empty result set stays stable at page 1 of 1
    empty_rows, info_empty = paginate_invoices(
        test_db_session, InvoiceFilters(search="NO-SUCH-INVOICE"), requested_page=4, page_size=10
    )
    assert empty_rows == []
    assert info_empty.total == 0
    assert info_empty.page == 1
    assert info_empty.total_pages == 1
    assert info_empty.first_row == 0 and info_empty.last_row == 0
    assert count_invoices(test_db_session, InvoiceFilters(vendor_id=v_acme.id)) == 3

    # Grouped status_counts matches individual status filters
    grouped = status_counts(test_db_session)
    assert grouped == {"APPROVED": 2, "NEEDS_REVIEW": 1, "BLOCKED": 1, "FAILED": 1}


def test_list_recent_runs_is_bounded_and_includes_selected_run(test_db_session):
    invs = _seed_invoices(test_db_session)
    base_time = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    runs = []
    for idx in range(6):
        run = ProcessingRun(
            invoice_id=invs[0].id,
            start_time=base_time + timedelta(minutes=idx),
            current_stage="COMPLETED",
            execution_status="SUCCESS",
            final_decision="APPROVED",
        )
        test_db_session.add(run)
        runs.append(run)
    test_db_session.commit()

    recent_3 = list_recent_runs(test_db_session, limit=3)
    assert len(recent_3) == 3
    assert [r.id for r in recent_3] == [runs[-1].id, runs[-2].id, runs[-3].id]

    # Asking for an older run ID includes it at the top without fetching all runs
    with_oldest = list_recent_runs(test_db_session, limit=3, include_run_id=runs[0].id)
    assert len(with_oldest) == 4
    assert with_oldest[0].id == runs[0].id


def test_ui_modules_do_not_call_unbounded_all_on_invoices_or_runs():
    root = Path(__file__).resolve().parent.parent
    for rel in ("app/ui/invoice_history.py", "app/ui/invoice_details.py", "app/ui/processing_view.py"):
        source = (root / rel).read_text(encoding="utf-8")
        assert "query(Invoice)" not in source, f"{rel} still uses unbounded session.query(Invoice)"
        assert "query(ProcessingRun)" not in source, f"{rel} still uses session.query(ProcessingRun)"


def test_run_trace_parsing_and_polling_behaviour():
    # Valid trace while RUNNING fills remaining stages as PENDING up to 19
    sample_log = json.dumps(
        [
            {
                "stage_name": "1. Receive invoice",
                "status": "COMPLETED",
                "start_time": "2026-05-10T12:00:00+00:00",
                "completion_time": "2026-05-10T12:00:00+00:00",
                "duration_ms": 2.4,
                "details": "Received file",
            },
            {
                "stage_name": "2. Validate uploaded file",
                "status": "RUNNING",
                "start_time": "2026-05-10T12:00:01+00:00",
                "completion_time": None,
                "duration_ms": 0.0,
                "details": None,
            },
        ]
    )
    running_trace = build_run_trace(sample_log, "RUNNING")
    assert running_trace.trace_error is None
    assert len(running_trace.stages) == 19
    assert running_trace.completed == 1
    assert running_trace.stages[0].status == "COMPLETED"
    assert running_trace.stages[1].status == "RUNNING"
    assert running_trace.stages[2].status == "PENDING"

    # Failed trace marks unreached stages as NOT_RUN and identifies failed_stage
    failed_log = json.dumps(
        [
            {"stage_name": "1. Receive invoice", "status": "COMPLETED", "duration_ms": 1.0},
            {"stage_name": "2. Validate uploaded file", "status": "FAILED", "error": "Corrupt PDF"},
        ]
    )
    failed_trace = build_run_trace(failed_log, "FAILED")
    assert failed_trace.failed_stage is not None
    assert failed_trace.failed_stage.number == 2
    assert failed_trace.failed_stage.error == "Corrupt PDF"
    assert failed_trace.stages[2].status == "NOT_RUN"

    # Malformed JSON never raises
    records, err = parse_stages_log("{not valid json")
    assert records == []
    assert err is not None and "could not be read" in err

    # Non-list JSON never raises
    records_dict, err_dict = parse_stages_log(json.dumps({"unexpected": True}))
    assert records_dict == []
    assert err_dict is not None

    # Terminal vs running vs stale polling decision
    now = datetime(2026, 5, 10, 12, 5, 0, tzinfo=timezone.utc)
    recent_start = now - timedelta(seconds=20)
    old_start = now - timedelta(minutes=ui_config.STALE_RUN_MINUTES + 5)

    assert is_terminal("SUCCESS") is True
    assert is_terminal("FAILED") is True
    assert is_terminal("RUNNING") is False
    assert should_poll("RUNNING", recent_start, now, ui_config.STALE_RUN_MINUTES) is True
    assert should_poll("SUCCESS", recent_start, now, ui_config.STALE_RUN_MINUTES) is False
    assert should_poll("FAILED", recent_start, now, ui_config.STALE_RUN_MINUTES) is False
    assert is_stale(old_start, now, ui_config.STALE_RUN_MINUTES) is True
    assert should_poll("RUNNING", old_start, now, ui_config.STALE_RUN_MINUTES) is False
    assert format_duration(45.2) == "45 ms"
    assert format_duration(1520.0) == "1.52 s"


def test_document_preview_helpers_for_pdf_image_missing_and_invalid(tmp_path):
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    pdf_fixture = fixtures_dir / "multi_page_invoice.pdf"
    img_fixture = fixtures_dir / "fuzzy_invoice_low_res.png"

    assert clamp_page(0, 3) == 1
    assert clamp_page(2, 3) == 2
    assert clamp_page(9, 3) == 3
    assert normalise_zoom(150) == 150
    assert normalise_zoom(999) == ui_config.DEFAULT_ZOOM

    dpi_100 = zoom_to_dpi(100)
    dpi_200 = zoom_to_dpi(200)
    assert dpi_200 > dpi_100
    assert dpi_200 <= ui_config.PREVIEW_MAX_DPI

    # Multi-page PDF inspection and single-page rasterisation
    pdf_info = inspect_document(str(pdf_fixture))
    assert pdf_info.kind == "pdf"
    assert pdf_info.page_count >= 2
    page1_png = render_pdf_page_png(str(pdf_fixture), page=1, zoom=100)
    page2_png = render_pdf_page_png(str(pdf_fixture), page=2, zoom=125)
    assert page1_png.startswith(b"\x89PNG")
    assert page2_png.startswith(b"\x89PNG")
    assert page1_png != page2_png

    # Raster image inspection and rendering
    img_info = inspect_document(str(img_fixture))
    assert img_info.kind == "image"
    assert img_info.page_count == 1
    raster_png = render_image_png(str(img_fixture))
    assert raster_png.startswith(b"\x89PNG")
    markup = image_html(raster_png, 100, alt="Test preview")
    assert 'role="region"' in markup and "width:100%" in markup

    # Missing file explains temporary storage behaviour
    missing_info = inspect_document(str(tmp_path / "missing_file.pdf"))
    assert missing_info.kind == "missing"
    assert missing_info.message == MISSING_FILE_MESSAGE

    # Corrupt PDF is reported cleanly as invalid
    bad_pdf = tmp_path / "corrupt.pdf"
    bad_pdf.write_bytes(b"%PDF-1.4 broken content")
    bad_info = inspect_document(str(bad_pdf))
    assert bad_info.kind == "invalid"
    assert bad_info.message is not None


def test_status_presentation_and_local_runner_guards():
    # Failed run is always presented as FAILED (needs review), never IN_PROGRESS or APPROVED
    assert invoice_display_status("NEEDS_REVIEW", "FAILED", None) == "FAILED"
    assert invoice_display_status("NEEDS_REVIEW", "FAILED", "APPROVED") == "APPROVED"
    assert invoice_display_status("APPROVED", "COMPLETED", None) == "APPROVED"
    assert invoice_display_status("NEEDS_REVIEW", "PROCESSING", None) == "IN_PROGRESS"

    assert run_display_status("FAILED", "NEEDS_REVIEW") == "FAILED"
    assert run_display_status("RUNNING", None, is_stale=False) == "IN_PROGRESS"
    assert run_display_status("RUNNING", None, is_stale=True) == "STALLED"
    assert run_display_status("SUCCESS", "BLOCKED") == "BLOCKED"

    # Badges include both symbol and readable label (never colour alone)
    for key in ("APPROVED", "NEEDS_REVIEW", "BLOCKED", "FAILED"):
        meta = status_meta(key)
        html = badge_html(key)
        assert meta.symbol in html
        assert meta.label in html

    # HTML table escapes untrusted strings
    tbl = table_html(
        [{"Vendor": "<script>alert(1)</script>", "Decision": "APPROVED"}],
        ["Vendor", "Decision"],
        caption="Test table",
        column_kinds={"Decision": "status"},
    )
    assert "<script>" not in tbl
    assert "&lt;script&gt;" in tbl
    assert "Approved" in tbl

    kpis = kpi_grid_html([Kpi("Total", "10", "note", "info")])
    assert "ap-kpi-grid" in kpis

    # Theme tokens cover both light and dark
    assert set(TOKENS.keys()) == {"light", "dark"}
    import plotly.graph_objects as go

    fig = style_figure(go.Figure(), "dark")
    assert fig.layout.font.color == TOKENS["dark"]["text"]

    # Local test runner guards
    assert is_local_test_runner_allowed("development", {}) is True
    assert is_local_test_runner_allowed("test", {}) is True
    assert is_local_test_runner_allowed("production", {}) is False
    assert is_local_test_runner_allowed("development", {"RENDER": "true"}) is False
    assert is_local_test_runner_allowed("development", {"ENABLE_UI_TEST_RUNNER": "false"}) is False

    long_out = "A" * 500 + "B" * 500
    truncated = truncate_test_output(long_out, max_chars=500)
    assert "truncated" in truncated
    assert len(truncated) < len(long_out)


def test_multi_format_accounting_exporters(test_db_session):
    from app.database.entities import InvoiceLineItem
    from app.processing.exporter import (
        to_audit_json,
        to_generic_csv,
        to_quickbooks_bills_csv,
        to_xero_bills_csv,
    )

    invs = _seed_invoices(test_db_session)
    invs[0].line_items.append(
        InvoiceLineItem(
            invoice_id=invs[0].id,
            line_number=1,
            description="Industrial Safety Helmets",
            quantity=5.0,
            unit="EA",
            unit_price=50.0,
            line_total=250.0,
        )
    )
    test_db_session.commit()

    generic = to_generic_csv(invs[:2])
    assert "invoice_id,invoice_number,vendor" in generic
    assert "INV-2026-101" in generic

    xero = to_xero_bills_csv([invs[0]])
    assert "*ContactName,*InvoiceNumber" in xero
    assert "Industrial Safety Helmets" in xero
    assert "APPROVED" in xero

    qb = to_quickbooks_bills_csv([invs[0]])
    assert "Vendor,Invoice Number,Invoice Date" in qb
    assert "Accounts Payable" in qb

    audit_json = json.loads(to_audit_json([invs[0]]))
    assert audit_json["invoice_number"] == "INV-2026-101"
    assert audit_json["effective_decision"] == "APPROVED"
    assert len(audit_json["line_items"]) == 1


def test_batch_runner_isolates_per_document_errors(test_db_session, tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.workflow.batch_runner import run_invoice_batch
    from app.workflow.orchestrator import WorkflowOrchestrator

    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path)
    clean_pdf = (settings.SAMPLE_DATA_DIR / "scenario_1_clean_invoice.pdf").read_bytes()
    orchestrator = WorkflowOrchestrator(test_db_session)

    summary = run_invoice_batch(
        orchestrator,
        [
            ("scenario_1_clean_invoice.pdf", clean_pdf),
            ("corrupt_upload.pdf", b"not-a-valid-pdf"),
            ("scenario_2_duplicate_invoice.pdf", clean_pdf),
        ],
    )
    assert summary.total == 3
    assert summary.approved == 1
    assert summary.failed == 1
    assert summary.blocked == 1
    assert [r.status for r in summary.results] == ["APPROVED", "FAILED", "BLOCKED"]


def test_due_date_chronology_validation_and_pdf_annotations():
    from app.core.schemas import NormalizedInvoice, RuleStatus
    from app.processing.financial_validator import FinancialValidator
    from app.ui.document_preview import normalise_rotation, render_pdf_page_annotated

    validator = FinancialValidator()
    bad_dates = NormalizedInvoice(
        invoice_number="INV-DATE-1",
        invoice_date="2026-05-10",
        due_date="2026-04-01",
        currency="USD",
        total_amount=100.0,
    )
    res_bad = {r.rule_id: r for r in validator.validate(bad_dates)}
    assert "RULE_DUE_DATE_CHRONOLOGY" in res_bad
    assert res_bad["RULE_DUE_DATE_CHRONOLOGY"].result == RuleStatus.FAIL

    good_dates = NormalizedInvoice(
        invoice_number="INV-DATE-2",
        invoice_date="2026-05-10",
        due_date="2026-06-10",
        currency="USD",
        total_amount=100.0,
    )
    res_good = {r.rule_id: r for r in validator.validate(good_dates)}
    assert res_good["RULE_DUE_DATE_CHRONOLOGY"].result == RuleStatus.PASS

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    pdf_fixture = fixtures_dir / "multi_page_invoice.pdf"
    assert normalise_rotation(90) == 90
    assert normalise_rotation(45) == 0
    png_rot, hits = render_pdf_page_annotated(
        str(pdf_fixture), page=1, zoom=100, rotation=90, highlight_terms=("INV",)
    )
    assert png_rot.startswith(b"\x89PNG")
    assert hits >= 0


def test_in_app_vendor_and_purchase_order_creation(test_db_session):
    from app.core.exceptions import APException
    from app.database.procurement_service import (
        create_purchase_order,
        create_vendor,
        set_vendor_approval,
    )

    vendor = create_vendor(
        test_db_session,
        legal_name="Bharat Industrial Components Pvt. Ltd.",
        tax_id="27AABCU9603R1ZM",
        country="IND",
        address="Plot 42, MIDC Andheri East, Mumbai",
        bank_account="IN-HDFC-0001829",
        is_approved=True,
    )
    test_db_session.commit()
    assert vendor.id is not None
    assert vendor.normalized_name == "bharat industrial components"
    assert vendor.country == "IND"
    assert vendor.is_approved is True

    # Duplicate vendor name or tax ID must be rejected
    with pytest.raises(APException):
        create_vendor(
            test_db_session,
            legal_name="Bharat Industrial Components Pvt. Ltd.",
            country="IND",
        )
    with pytest.raises(APException):
        create_vendor(
            test_db_session,
            legal_name="Another Indian Vendor Ltd.",
            tax_id="27AABCU9603R1ZM",
            country="IND",
        )

    # Toggle approval status
    toggled = set_vendor_approval(test_db_session, vendor_id=vendor.id, is_approved=False)
    assert toggled.is_approved is False
    set_vendor_approval(test_db_session, vendor_id=vendor.id, is_approved=True)

    # Create an INR Purchase Order with line items
    po = create_purchase_order(
        test_db_session,
        vendor_id=vendor.id,
        po_number="PO-2026-IND-001",
        currency="INR",
        po_date="2026-05-01",
        expiry_date="2026-12-31",
        line_items=[
            {"description": "Precision CNC Bearings", "quantity": 10, "unit": "EA", "unit_price": 4200.0},
            {"description": "Hydraulic Valve Assemblies", "quantity": 5, "unit": "EA", "unit_price": 8400.0},
        ],
        total_amount_override=100000.0,
    )
    test_db_session.commit()
    assert po.id is not None
    assert po.currency == "INR"
    assert po.total_amount == 100000.0
    assert po.remaining_balance == 100000.0
    assert len(po.line_items) == 2
    assert po.line_items[0].line_total == 42000.0
    assert po.line_items[1].line_total == 42000.0


def test_inr_processing_bare_dollar_confirmation_and_usd_inr_conversion(test_db_session):
    from app.config.persistence import ConfigurationService
    from app.core.schemas import ExtractedField, ExtractedInvoice, NormalizedLineItem, RuleStatus
    from app.database.procurement_service import create_purchase_order, create_vendor
    from app.extraction.llm_provider import MockLLMProvider
    from app.processing.currency import (
        convert_amount,
        convert_to_inr,
        format_inr,
        format_native_and_inr,
        resolve_invoice_currency,
        set_usd_to_inr_rate,
    )
    from app.processing.financial_validator import FinancialValidator
    from app.processing.line_item_matcher import LineItemMatcher
    from app.processing.normalizer import normalize_currency, normalize_invoice

    # 1. INR recognition in normalizer and mock extractor
    assert normalize_currency("INR") == "INR"
    assert normalize_currency("₹") == "INR"
    assert normalize_currency("Rs.") == "INR"
    assert normalize_currency("Rupees") == "INR"

    inr_doc = (
        "Vendor: Bharat Industrial Components Pvt. Ltd.\n"
        "Invoice Number: INV-IND-2026-01\n"
        "Invoice Date: 2026-05-10\n"
        "Due Date: 2026-06-10\n"
        "PO Reference: PO-2026-IND-002\n"
        "ITEM: Precision CNC Bearings | Qty: 10 | Price: ₹4,200.00 | Total: ₹42,000.00\n"
        "Subtotal: ₹42,000.00\n"
        "GST (18%): ₹7,560.00\n"
        "Total Amount: ₹49,560.00\n"
    )
    extracted_inr = MockLLMProvider().extract_invoice(inr_doc)
    norm_inr = normalize_invoice(extracted_inr)
    assert norm_inr.currency == "INR"
    assert norm_inr.subtotal == 42000.0
    assert norm_inr.tax_amount == 7560.0
    assert norm_inr.total_amount == 49560.0
    assert len(norm_inr.line_items) == 1
    assert norm_inr.line_items[0].line_total == 42000.0

    # 2. Bare '$' on an invoice matching a US vendor or USD PO resolves to USD (no unconfirmed warning)
    us_vendor = test_db_session.query(Vendor).filter_by(legal_name="Acme Industrial Supplies Inc.").one()
    bare_dollar_ext = ExtractedInvoice(
        currency=ExtractedField(value=None, confidence=0.0, evidence="$"),
        total_amount=ExtractedField(value=250.0, confidence=0.95, evidence="$250.00"),
        raw_text="Total: $250.00",
    )
    bare_dollar_norm = normalize_invoice(bare_dollar_ext)
    assert bare_dollar_norm.currency is None
    resolved = resolve_invoice_currency(bare_dollar_ext, bare_dollar_norm, matched_vendor=us_vendor)
    assert resolved == "USD"
    assert bare_dollar_norm.currency == "USD"
    assert bare_dollar_ext.currency.value == "USD"

    # 3. Cross-currency conversion: USD invoice ($500.00 @ 1 USD = ₹84.00 -> ₹42,000.00) against an INR PO
    cfg = ConfigurationService(test_db_session)
    cfg.save_usd_to_inr_rate(84.0)
    test_db_session.commit()
    assert cfg.load_usd_to_inr_rate() == 84.0
    assert convert_amount(500.0, "USD", "INR") == 42000.0
    assert convert_to_inr(100.0, "USD") == 8400.0
    assert format_inr(8400.0) == "₹8,400.00"
    assert "₹8,400.00" in format_native_and_inr(100.0, "USD")

    ind_vendor = create_vendor(
        test_db_session,
        legal_name="Zenith Bharat Engineering Pvt. Ltd.",
        tax_id="29AAACZ1234A1Z5",
        country="IND",
    )
    inr_po = create_purchase_order(
        test_db_session,
        vendor_id=ind_vendor.id,
        po_number="PO-2026-INR-500",
        currency="INR",
        line_items=[
            {"description": "Precision CNC Bearings", "quantity": 10, "unit": "EA", "unit_price": 4200.0},
        ],
        total_amount_override=50000.0,
    )
    test_db_session.commit()

    # Invoice billed in USD ($50.00/unit * 10 = $500.00 == ₹4,200/unit * 10 = ₹42,000.00)
    usd_inv = normalize_invoice(ExtractedInvoice())
    usd_inv.currency = "USD"
    usd_inv.subtotal = 500.0
    usd_inv.tax_amount = 0.0
    usd_inv.total_amount = 500.0
    usd_inv.line_items = [
        NormalizedLineItem(
            description="Precision CNC Bearings",
            quantity=10.0,
            unit="EA",
            unit_price=50.0,
            line_total=500.0,
        )
    ]

    line_matches = LineItemMatcher().match_line_items(usd_inv, inr_po)
    assert len(line_matches) == 1
    assert line_matches[0].po_line_id == inr_po.line_items[0].id
    assert line_matches[0].price_diff == 0.0

    val_results = {r.rule_id: r for r in FinancialValidator().validate(usd_inv, inr_po, allow_fx_conversion=True)}
    assert "RULE_CURRENCY_MISMATCH" not in val_results
    assert val_results["RULE_CURRENCY_CONVERTED"].result == RuleStatus.PASS
    assert val_results["RULE_PO_BALANCE_CHECK"].result == RuleStatus.PASS
    set_usd_to_inr_rate(84.0)


def test_probabilistic_vendor_and_po_matching_across_different_formats(test_db_session):
    from app.core.exceptions import APException
    from app.core.schemas import NormalizedInvoice
    from app.database.procurement_service import create_purchase_order, create_vendor
    from app.processing.normalizer import normalize_identifier, normalize_vendor_name
    from app.processing.po_matcher import POMatcher
    from app.processing.vendor_matcher import VendorMatcher

    v_matcher = VendorMatcher(test_db_session)
    p_matcher = POMatcher(test_db_session)

    # 1. Abbreviations + honorifics ("M/s Acme Ind. Supply" -> "Acme Industrial Supplies Inc.")
    inv_abbrev = NormalizedInvoice(
        vendor_name="M/s Acme Ind. Supply",
        normalized_vendor_name=normalize_vendor_name("M/s Acme Ind. Supply"),
    )
    mv1, cands1, score1, status1 = v_matcher.match_vendor(inv_abbrev)
    assert mv1 is not None and mv1.legal_name == "Acme Industrial Supplies Inc."
    assert status1 == "CONFIDENT_MATCH"
    assert score1 >= 0.90

    # 2. Reordered words ("Supplies Industrial Acme" -> "Acme Industrial Supplies Inc.")
    inv_reordered = NormalizedInvoice(
        vendor_name="Supplies Industrial Acme",
        normalized_vendor_name=normalize_vendor_name("Supplies Industrial Acme"),
    )
    mv2, _, score2, status2 = v_matcher.match_vendor(inv_reordered)
    assert mv2 is not None and mv2.legal_name == "Acme Industrial Supplies Inc."
    assert status2 == "CONFIDENT_MATCH"
    assert score2 >= 0.95

    # 3. Differently formatted Tax ID ("TAXID: US TAX 1011" -> "US-TAX-1011")
    inv_tax = NormalizedInvoice(
        vendor_name="Completely Different DBA Name",
        normalized_vendor_name=normalize_vendor_name("Completely Different DBA Name"),
        vendor_tax_id="TAXID: US TAX 1011",
    )
    mv3, _, score3, status3 = v_matcher.match_vendor(inv_tax)
    assert mv3 is not None and mv3.legal_name == "Acme Industrial Supplies Inc."
    assert status3 == "CONFIDENT_MATCH"
    assert score3 == 1.0

    # 4. Differently formatted PO references ("P.O. #2026/001", "Order 2026-01", "PO-2O26-OO1")
    for raw_po_ref in ("P.O. #2026/001", "Order 2026-01", "PO-2O26-OO1", "PO 2026 001"):
        inv_po = NormalizedInvoice(
            po_reference=raw_po_ref,
            normalized_po_reference=normalize_identifier(raw_po_ref),
            currency="USD",
            total_amount=1350.0,
        )
        mp, pcands, pscore, pstatus = p_matcher.match_po(inv_po, mv1)
        assert mp is not None and mp.po_number == "PO-2026-001", f"Failed for {raw_po_ref}"
        assert pstatus == "CONFIDENT_MATCH", f"Expected CONFIDENT_MATCH for {raw_po_ref}, got {pstatus}"
        assert pscore >= 0.94

    # 5. Probabilistic similarity guard on Vendor & PO creation (warns by default, allows when confirmed)
    with pytest.raises(APException, match="Probabilistic similarity alert"):
        create_vendor(
            test_db_session,
            legal_name="Acme Ind. Supply Co.",
            country="USA",
            allow_similar=False,
        )

    distinct_v = create_vendor(
        test_db_session,
        legal_name="Acme Ind. Supply Co.",
        country="USA",
        allow_similar=True,
    )
    assert distinct_v.id is not None

    with pytest.raises(APException, match="Probabilistic PO similarity alert"):
        create_purchase_order(
            test_db_session,
            vendor_id=mv1.id,
            po_number="PO/2026/01",
            currency="USD",
            total_amount_override=1000.0,
            allow_similar=False,
        )



