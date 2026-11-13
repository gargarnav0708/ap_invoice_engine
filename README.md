# AI-Powered Invoice Processing & AP Decision Engine

> Production-minded accounts payable (AP) automation system built for the **Zamp AI Solutions Analyst** case study.

---

## 1. Executive Summary & Core Proposition

In typical enterprise Accounts Payable (AP) workflows, staff manually process hundreds of vendor invoices: inspecting PDF scans, cross-referencing Purchase Orders (POs), checking vendor tax IDs, comparing line-item prices, verifying math and tax calculations, detecting duplicates, and assessing PO remaining balances. The process is prone to human error, expensive, and difficult to scale.

### Core Philosophy
1. **AI Interprets Unstructured Information; Deterministic Code Enforces Rules:** The Large Language Model extracts unstructured document data into a strict schema. The LLM is **never** permitted to independently approve, reject, or invent financial values.
2. **Explainable Decisions & Audit Trails:** Every validation rule emits explicit expected vs. actual values, variances, explanations, and evidence snippets.
3. **Traceable 3-Way Matching:** Links invoices against vendor registries and PO procurement balances with atomic balance tracking.
4. **Human-in-the-Loop Oversight:** Low-confidence extraction, ambiguous matches, or missing fields trigger human review (`NEEDS_REVIEW`) rather than hazardous assumptions.
5. **Multi-level Fraud & Duplicate Prevention:** Detects exact binary file duplicates, duplicate vendor invoice numbers, and near-duplicates within configurable date and amount windows.

---

## 2. System Architecture & Project Organization

The repository adheres to clean architecture principles with complete separation of concerns:

```
ap_invoice_engine/
├── .env.example               # Environment template for LLM and paths
├── requirements.txt           # Python dependency specifications
├── README.md                  # Comprehensive architectural and run documentation
├── app/
│   ├── main.py                # Streamlit application entry point & page router
│   ├── config/
│   │   ├── settings.py        # Environment variables & system directories
│   │   └── rules.py           # Configurable thresholds, tolerances & rule policies
│   ├── core/
│   │   ├── schemas.py         # Pydantic schemas (ExtractedInvoice, RuleResult, etc.)
│   │   └── exceptions.py      # Business exceptions vs Technical failures
│   ├── ingestion/
│   │   ├── pdf_loader.py      # PyMuPDF + pdfplumber text & table extraction
│   │   ├── document_classifier.py # Classifies MACHINE_READABLE, SCANNED, HYBRID
│   │   └── ocr_engine.py      # Tesseract OCR abstraction with graceful fallback
│   ├── extraction/
│   │   ├── extractor.py       # Extraction pipeline coordinator
│   │   ├── llm_provider.py    # OpenAI-compatible provider & deterministic demo engine
│   │   ├── extraction_prompts.py # Strict Pydantic JSON extraction prompts
│   │   └── confidence.py      # Multi-factor confidence scoring
│   ├── processing/
│   │   ├── normalizer.py      # Normalizes company names, currencies, dates, IDs
│   │   ├── vendor_matcher.py  # Tax ID exact match, normalized match, fuzzy scoring
│   │   ├── po_matcher.py      # Exact PO reference, vendor-scoped candidate scoring
│   │   ├── duplicate_detector.py # 4-level duplicate detection engine
│   │   ├── financial_validator.py # Arithmetic, tax, line sum, and PO balance validation
│   │   └── line_item_matcher.py # Item description fuzzy match, price & qty variances
│   ├── decision/
│   │   ├── rules_engine.py    # Aggregates and runs all AP validation rules
│   │   └── decision_service.py # Deterministic classification (APPROVED, NEEDS_REVIEW, BLOCKED)
│   ├── database/
│   │   ├── db.py              # SQLAlchemy engine, session management, SQLite pragmas
│   │   ├── entities.py        # Relational models (Vendor, PO, Line Items, Invoices, Audits)
│   │   └── repositories.py    # Transactional repositories with atomic PO balance updates
│   ├── workflow/
│   │   ├── orchestrator.py    # 19-stage pipeline coordinator with rerun idempotency
│   │   └── execution_logger.py # Records execution trace, durations, and statuses
│   ├── ui/
│   │   ├── dashboard.py       # Live dynamic KPIs and Plotly charts
│   │   ├── upload.py          # Drag-and-drop uploader & 1-click scenario runners
│   │   ├── processing_view.py # 19-stage live execution trace inspector
│   │   ├── invoice_history.py # Searchable and filterable invoice registry
│   │   ├── invoice_details.py # Audit workbench, PDF preview, and manual override form
│   │   └── settings_view.py   # Configurable tolerances and database maintenance
│   └── utils/
│       ├── file_storage.py    # Secure PDF storage and path sanitization
│       ├── hashing.py         # SHA-256 calculation utilities
│       └── logging.py         # Standardized application logging
├── data/
│   ├── uploads/               # Secure uploaded file storage
│   └── sample_data/           # Synthetic test PDF fixtures
├── scripts/
│   ├── seed_database.py       # Seeds 5 vendors, 10 POs, and line items
│   └── generate_test_invoices.py # Generates 4 scenario test invoice PDFs
└── tests/
    ├── conftest.py            # Isolated in-memory SQLite fixtures
    ├── test_ingestion.py      # PDF classification, text extraction, OCR abstraction
    ├── test_extraction.py     # Schema validation, missing fields, confidence scoring
    ├── test_matching.py       # Vendor, PO, and line-item matching
    ├── test_duplicates.py     # 4-level duplicate detection
    ├── test_financial_validation.py # Math, tax, currency, and PO balance overbilling
    ├── test_decisions.py      # Decision classification & explainability
    └── test_workflow.py       # End-to-end orchestration, atomic updates, human overrides
```

---

## 3. The 19-Stage Workflow Pipeline

The central `WorkflowOrchestrator` coordinates the end-to-end lifecycle of every invoice:

| Stage # | Stage Name | Description |
|---|---|---|
| **1** | **Receive invoice** | Validates presence of file payload or filesystem reference. |
| **2** | **Validate uploaded file** | Inspects magic `%PDF` header bytes, file extension, and enforces size limit (<= 20MB). |
| **3** | **Calculate file hash** | Computes SHA-256 binary hash for persistent identification. |
| **4** | **Check for exact duplicate** | Level 1 duplicate check: blocks if an identical file hash has already been processed. |
| **5** | **Extract PDF text** | Extracts digital text streams using PyMuPDF. |
| **6** | **Run OCR when necessary** | Evaluates document type. Engages OCR only when text density is insufficient (scanned/hybrid). |
| **7** | **Extract structured info** | Invokes LLM provider with structured schema (or deterministic demo engine in offline mode). |
| **8** | **Validate extracted schema** | Enforces strict Pydantic models. Missing values remain `null`; never invented. |
| **9** | **Normalise extracted fields** | Standardizes vendor corporate suffixes, currencies (ISO 4217), dates (ISO 8601), and IDs. |
| **10** | **Match vendor** | Hierarchical matching: Exact Tax ID → Exact Normalized Name → Fuzzy similarity scoring. |
| **11** | **Match purchase order** | Exact PO reference verification → Vendor cross-check → Candidate similarity ranking. |
| **12** | **Detect duplicate invoices** | Level 2–4 checks: Vendor + Invoice Number match, and Near-Duplicates (similar amount within 7 days). |
| **13** | **Match line items** | Compares invoice line items against PO lines for description similarity, price variances, and quantities. |
| **14** | **Run financial validation** | Verifies line item math (`qty * price`), subtotal sum, tax calculation, and currency alignment. |
| **15** | **Run PO balance validation** | Calculates `remaining_balance = total_amount - billed_amount`. Flags or blocks overbilling. |
| **16** | **Generate final decision** | Decision engine deterministically maps rule results to `APPROVED`, `NEEDS_REVIEW`, or `BLOCKED`. |
| **17** | **Save all results** | Persists Invoice, Line Items, and Validation Result entities to SQLite. |
| **18** | **Update invoice & PO records** | If `APPROVED`, atomically updates cumulative PO billed amount and remaining balance. |
| **19** | **Generate final audit report** | Emits an immutable `AuditLog` entry documenting the run, decision, actor, and timestamp. |

---

## 4. Installation & Environment Setup

### Prerequisites
- Python 3.10+ (tested on Python 3.14)
- (Optional) Tesseract OCR installed on the host system (`brew install tesseract` on macOS). If not installed, the engine gracefully utilizes the built-in OCR fallback without crashing.

### Step 1: Clone or Navigate to Directory
```bash
cd /Users/herr_ag98/.gemini/antigravity/scratch/ap_invoice_engine
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy the template configuration:
```bash
cp .env.example .env
```
Inside `.env`:
```env
# Optional: Provide OpenAI-compatible credentials for live LLM extraction.
# If left blank, the application automatically runs in Demo / Offline Mode using
# the deterministic rule engine (does not hallucinate or make network requests).
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Database Configuration
DATABASE_URL=sqlite:///data/ap_invoices.db

# Storage Directories
STORAGE_DIR=data/uploads
SAMPLE_DATA_DIR=data/sample_data

# OCR Engine
OCR_ENABLED=true
TESSERACT_CMD=
```

---

## 5. Database Initialization & Synthetic Fixtures

Initialize and seed the database with 5 vendors, 10 POs, and generate the 4 test scenario PDFs:

```bash
# 1. Initialize tables and seed procurement records
PYTHONPATH=. python3 scripts/seed_database.py

# 2. Generate the 4 benchmark test invoice PDFs
PYTHONPATH=. python3 scripts/generate_test_invoices.py
```

---

## 6. Running the Application

Launch the Streamlit web dashboard:

```bash
PYTHONPATH=. streamlit run app/main.py
```
Open your browser to: **`http://localhost:8501`**

---

## 7. Automated Test Suite

Run the full automated test suite containing 25 unit and integration tests:

```bash
PYTHONPATH=. pytest -v
```

All 25 tests cover:
- PDF document classification & OCR abstraction
- Schema validation & missing-field null handling
- Exact and fuzzy vendor matching
- PO matching & vendor cross-checks
- 4-level duplicate detection
- Financial arithmetic, tax checks, rounding tolerances, and PO balance overbilling
- Deterministic decision classification
- End-to-end workflow execution, atomic PO deductions, and human reviewer overrides

---

## 8. Demonstrating the 4 Core Scenarios

The system can be tested immediately using the 1-click scenario buttons in the UI under **Upload Invoice > Quick Test Scenarios (1-Click)** or programmatically via the command line:

### Scenario 1: Clean Invoice
- **Document:** `scenario_1_clean_invoice.pdf`
- **Vendor:** Acme Industrial Supplies Inc. (Approved, Tax ID: `US-TAX-1011`)
- **PO:** `PO-2026-001` (Active, Remaining Balance: $2,500.00)
- **Line Items:** 20 Helmets @ $50.00 ($1,000.00) + 10 Gloves @ $25.00 ($250.00). Subtotal: $1,250.00, Tax: $100.00, Total: $1,350.00.
- **Outcome:** `APPROVED`
- **Audit Action:** PO remaining balance atomically decremented from $2,500.00 to $1,150.00.

### Scenario 2: Duplicate Invoice
- **Document:** `scenario_2_duplicate_invoice.pdf` (exact re-submission of Invoice 1)
- **Outcome:** `BLOCKED`
- **Evidence:** Level 1 binary SHA-256 file duplicate detected pointing to original Invoice ID #1.

### Scenario 3: Split PO Overbilling
- **Document:** `scenario_3_split_overbilling.pdf`
- **Vendor:** Apex Cloud Services LLC
- **PO:** `PO-2026-002` (Total: $5,000.00, Previously Billed: $4,500.00, Remaining Balance: $500.00)
- **Invoice Amount:** $1,100.00 ($1,000.00 + $100.00 tax)
- **Outcome:** `BLOCKED`
- **Evidence:** Invoice total ($1,100.00) exceeds available PO balance ($500.00) by $600.00.

### Scenario 4: Poor-Quality / Incomplete Invoice
- **Document:** `scenario_4_incomplete_invoice.pdf`
- **Issue:** Missing formal invoice number, missing PO reference, illegible tax.
- **Outcome:** `NEEDS_REVIEW`
- **Evidence:** Soft validation failure flagged for missing critical fields and unlinked PO. Sent to human reviewer workbench for inspection and override.

---

## 9. Human Review & Manual Overrides

When an invoice receives `NEEDS_REVIEW` or `BLOCKED`, authorized AP personnel can audit the invoice:
1. Navigate to **Invoice Details**.
2. Inspect the PDF page preview, extracted fields, matched vendor, matched PO, and rule check logs.
3. Submit an override decision (`APPROVED`, `NEEDS_REVIEW`, `BLOCKED`) with a **mandatory justification reason**.
4. The system preserves the original automated decision, records the reviewer's identity and timestamp in `AuditLog`, and atomically adjusts the PO balance if approved.

---

## 10. Limitations & Future Roadmap

### Current Scope & Local Dev Boundaries
- **Single-tenant Local Database:** Uses thread-safe SQLite with PRAGMA foreign keys enabled.
- **Payment Execution:** Intentionally stops at the approval/escalation decision; does not execute direct banking payments.

### Planned Enhancements (PS-2 & Beyond)
1. **Multi-Currency FX Rates:** Integration with European Central Bank or OpenExchangeRates API for dynamic cross-currency conversion against PO base currency.
2. **Advanced Multi-Page Table Stitching:** Enhancing line item extraction across multi-page purchase invoices with repeated headers.
3. **ERP Integrations:** Webhook connectors for SAP, NetSuite, and QuickBooks for automatic bill creation post-approval.
4. **Role-Based Access Control (RBAC):** SAML/OAuth2 authentication distinguishing AP Clerks, Senior Analysts, and Financial Controllers.
