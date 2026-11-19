# Demo Walkthrough Script (Focused on What Was Built & Why)

**Pre-Demo Reset (10s):** Open **Settings & Rules → 🔄 Reset & Reseed Database** so PO balances start clean.

---

### 1. Intro & Core Architecture (`Overview Dashboard` — 20s)
* **What was built:** An end-to-end AP engine where **AI only extracts unstructured fields**, a **probabilistic matcher** links Vendors/POs, and **deterministic code** enforces financial rules and reports all spend in **INR (₹)** (auto-converting USD/foreign currencies).
* **Why it helps:** Eliminates manual PDF data entry while guaranteeing an LLM can never hallucinate a PO or approve an invalid payment.
* **Say:**
  > *"We built an AP automation engine that separates AI extraction from deterministic financial rules: the LLM extracts invoice fields with verbatim evidence, a probabilistic engine links Vendors and POs, and deterministic code approves, blocks, or routes to review—with all spend converted and reported in **INR (₹)**."*

---

### 2. In-App Vendor/PO Onboarding & Probabilistic Matcher (`Vendors & Purchase Orders` — 60s)
* **Click:** **Vendors & Purchase Orders** → show **Vendors** tab → **Purchase Orders** tab → **🎯 Probabilistic Matcher & Confidence Simulator** tab.
* **What was built:**
  1. In-app onboarding for Vendors (Tax ID/GSTIN, Country, approval toggle) and multi-line POs in `INR` or `USD`.
  2. A **4-signal probabilistic matching engine** (abbreviation expansion like `Ind.` → `Industrial`, order-invariant token Dice, token containment, numeric-segment & OCR look-alike normalization for PO numbers, and FX-converted amount/line-item fit).
* **Why it helps:** Vendors rarely format their name (`"M/s Acme Ind. Supply"` vs `"Acme Industrial Supplies Inc."`) or PO number (`"P.O. #2026/01"` vs `"PO-2026-001"`) identically to internal records. Exact-match systems fail on valid invoices; our probabilistic model matches them at **95%+ confidence** and prevents duplicate vendor/PO creation.
* **Say:**
  > *"Here we can onboard vendors and issue POs in INR or USD. Because vendors format names and POs inconsistently on invoices, we built a multi-signal probabilistic matcher instead of exact string matching. In the simulator, `'M/s Acme Ind. Supply'` and `'P.O. #2026/01'` match `'Acme Industrial Supplies Inc.'` and `'PO-2026-001'` at **95%+ confidence** across abbreviation expansion, token overlap, and numeric-segment normalization."*

---

### 3. 19-Stage Live Pipeline & Atomic PO Balance Control (`Upload Invoice → Scenarios 1 & 3` — 60s)
* **Click:** **Upload Invoice → Quick Test Scenarios** → run **▶ Scenario 1 (Clean Invoice)**, then run **▶ Scenario 3 (Split PO Overbilling)**.
* **What was built:**
  1. Single-file live 19-stage execution stream + multi-file batch uploader with per-document error isolation.
  2. Deterministic 3-way validation (line math, subtotal/tax, chronology, 4-tier duplicate detection) and **atomic cent-exact SQL PO balance reservation**.
* **Why it helps:** Approves clean invoices in milliseconds (converting `$1,350.00` → `₹113,400.00` and deducting the PO balance), while hard-blocking split invoices that exceed remaining PO budget (`$1,100` billed against `$500` remaining on `PO-2026-002`) so a PO can never be over-paid.
* **Say:**
  > *"Scenario 1 runs all 19 stages live, passes every math and 3-way match rule, auto-approves `$1,350` (`₹113,400`), and atomically deducts the PO balance. Scenario 3 bills `$1,100` against a PO with only `$500` remaining—Stage 15 catches the `$600` overage and hard-blocks payment."*

---

### 4. OCR Confidence & Auto-Polling Trace (`Scenario 5` & `Live Processing View` — 40s)
* **Click:** Run **▶ Scenario 5 (Low-Quality Scan)** → open **Live Processing View**.
* **What was built:** Automatic document classification (digital vs. scanned), Tesseract OCR fallback with word-level confidence per evidence snippet, and a persisted stage timeline that auto-polls active runs via `st.fragment`.
* **Why it helps:** Degraded scans or missing fields never get guessed by AI—low OCR confidence deterministically routes the invoice to `NEEDS_REVIEW`, and every stage trace is saved to SQL even if a run fails mid-way.
* **Say:**
  > *"Scenario 5 is a blurry raster image. The engine auto-engages OCR, scores word-level confidence on each extracted snippet, and routes missing fields to `NEEDS_REVIEW` rather than guessing. Live Processing View persists and auto-polls every stage."*

---

### 5. Audit Workbench, PDF Field Highlighting, Override & ERP Export (`Scenario 6` → `Invoice Details` → `Invoice History` — 75s)
* **Click:**
  1. Run **▶ Scenario 6 (Ambiguous PO)** → open **Invoice Details**.
  2. In **Document Preview**, toggle **Highlight extracted fields** (and `Zoom 125%` / `Rotate`).
  3. In **Human-in-the-Loop Override**, link `PO-2026-004`, set decision to `APPROVED`, enter a reason, and click **💾 Save Manual Override**.
  4. Expand **Reviewer corrections — before and after** in the audit trail, then click **⬇️ Export Xero Bills CSV** / **QuickBooks CSV** (and show SQL-paginated **Invoice History**).
* **What was built:**
  1. Bounded single-page PDF viewer with page navigation, zoom, rotation, and **on-page bounding-box highlighting** of extracted fields.
  2. Human override workflow that preserves the raw AI extraction, atomically reconciles PO balances, logs a **before/after field diff**, and exports to **Xero CSV, QuickBooks CSV, Generic CSV, and Audit JSON**.
  3. SQL `LIMIT/OFFSET` pagination in **Invoice History** with deterministic `created_at DESC, id DESC` ordering.
* **Why it helps:** Gives AP analysts instant visual proof on the PDF, full audit traceability for compliance, scalable history queries, and 1-click ERP import files without re-typing data.
* **Say:**
  > *"On Scenario 6, the vendor omitted the PO number and has two open POs, so it routes to review. In Invoice Details, checking **Highlight extracted fields** draws bounding boxes directly around the extracted values on the PDF. I can link `PO-2026-004` and approve with a mandatory reason—which atomically updates the PO balance, logs a before/after diff in the immutable audit trail, and lets us export directly to Xero or QuickBooks CSV."*

---

### 6. Live Policy & FX Controls (`Settings & Rules` — 20s)
* **Click:** **Settings & Rules**.
* **What was built:** Database-persisted rule thresholds, Demo/Live LLM switch, configurable **`USD → INR` exchange rate**, and a local-only `pytest` runner.
* **Why it helps:** Finance teams can adjust tolerances and FX rates at runtime without code changes or redeployments.
