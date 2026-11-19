# End-to-End Demo Walkthrough Script (5–7 Minutes)

This script gives you a **click-by-click path** and **spoken talking points** to demonstrate the **Zamp AI-Powered Accounts Payable (AP) Automation & Decision Engine** in an interview or live presentation.

---

## Pre-Demo Checklist (30 Seconds Before Starting)

1. Start the application from VS Code or terminal:
   ```bash
   source .venv/bin/activate
   streamlit run app/main.py
   ```
2. Open **Settings & Rules → Database Operations**, check *"I understand this deletes local demo transaction data"*, and click **🔄 Reset & Reseed Database** so PO balances start fresh (`PO-2026-001` at `$2,500.00`, `PO-2026-002` at `$500.00`).
3. In the sidebar, confirm:
   - **Theme:** `☀️ Light` (you will toggle `🌙 Dark` during Step 1)
   - **AI Extraction:** `Live` (Groq `openai/gpt-oss-20b`) or `Demo` (deterministic rule engine)
   - **Footer:** `DB: Sqlite | OCR: Tesseract ready`

---

## 0:00–0:45 — 1. Frame the Problem & Core Architecture (`Overview Dashboard`)

**Where to click:**
- Start on **Overview Dashboard**.
- Toggle the sidebar **Theme** from `☀️ Light` to `🌙 Dark` and back to show session-scoped theming.

**What to say:**
> *"In enterprise Accounts Payable, finance teams manually review hundreds of vendor PDFs and scans every week—extracting fields, hunting down Purchase Orders, checking vendor tax IDs, converting currencies, verifying line-item math, and watching for duplicate submissions. It’s slow and error-prone.*
>
> *We built an end-to-end AP Decision Engine around one strict architectural principle: **AI interprets unstructured documents, but deterministic code makes financial decisions.** The LLM extracts structured fields with per-field confidence and verbatim evidence snippets, our probabilistic matching engine links Vendors and POs even when formatted differently, and a deterministic rules engine decides whether to auto-approve, block, or route to human review.*
>
> *Notice the dashboard reports all approved spend in **Indian Rupees (INR ₹)**, automatically converting USD and multi-currency invoices at the configured FX rate."*

---

## 0:45–1:45 — 2. Onboard a Vendor, Issue a PO & Show the Probabilistic Matcher (`Vendors & Purchase Orders`)

**Where to click:**
1. Click **Vendors & Purchase Orders** in the sidebar.
2. On **🏢 Vendors Directory & Onboarding**, point out the registration form (Legal Name, Tax ID / GSTIN, Country `IND — India (₹ INR)`, Approved status) and the registered vendors table below.
3. Switch to **📑 Purchase Orders & Line Items** and point out the multi-row Line Items builder and the PO registry showing both **native currency** and **INR (₹) equivalent remaining balances**.
4. Switch to the 3rd tab: **🎯 Probabilistic Matcher & Confidence Simulator**.
   - Leave the default test inputs (`M/s Acme Ind. Supply`, `P.O. #2026/01`, `Ind. Safety Helmets`) or edit them live.

**What to say:**
> *"Procurement teams can onboard vendors and issue Purchase Orders in INR, USD, or 10 other currencies directly inside the app.*
>
> *In the real world, vendors rarely format their names or PO references the same way twice. Instead of brittle exact-string constraints, we built a **multi-signal probabilistic matching model**. Here in the live simulator, even when an invoice says `'M/s Acme Ind. Supply'` instead of `'Acme Industrial Supplies Inc.'` and formats the PO as `'P.O. #2026/01'` instead of `'PO-2026-001'`, our engine expands abbreviations, computes token-set Dice and containment scores, normalizes numeric segments and OCR look-alikes, and matches both the Vendor and the PO with **95%+ confidence**—while also guarding against near-duplicate vendor or PO creation."*

---

## 1:45–2:40 — 3. Clean 3-Way Match & Automatic PO Reservation (`Upload Invoice → Scenario 1`)

**Where to click:**
1. Click **Upload Invoice** in the sidebar.
2. Point out that **Upload Document** supports both **single-file live streaming** and **multi-file batch upload** with per-document error isolation.
3. Open the **Quick Test Scenarios (1-Click)** tab and click **▶ Run Scenario 1** (*Clean Invoice*).

**What to say:**
> *"Let’s run Scenario 1: a clean invoice from Acme Industrial Supplies billing `$1,350.00` against `PO-2026-001`. Watch the **19 pipeline stages** stream live—file validation, SHA-256 hashing, duplicate check, text extraction, structured extraction, normalization, probabilistic vendor and PO matching, line-item comparison, financial math, and atomic PO balance reservation.*
>
> *Every check passes, so the invoice is **AUTOMATICALLY APPROVED**. The decision card displays both the native `$1,350.00` total and its converted **`₹113,400.00` INR equivalent**, and `PO-2026-001`’s remaining balance is atomically reduced from `$2,500.00` to `$1,150.00`."*

---

## 2:40–3:30 — 4. Hard Financial Controls: Duplicate & Split-PO Overbilling (`Scenarios 2 & 3`)

**Where to click:**
1. On **Upload Invoice → Quick Test Scenarios (1-Click)**, click **▶ Run Scenario 3** (*Split PO Overbilling*).
2. Scroll to the red **✕ Blocked** decision card and point to the `Why` section (`RULE_PO_BALANCE_EXCEEDED`).
3. (Optional) Click **▶ Run Scenario 7** (*Changed-PDF Near Duplicate*, which compares against Scenario 1) or **▶ Run Scenario 2** (*Exact Duplicate*).

**What to say:**
> *"Now let’s test a hard stop. In Scenario 3, `PO-2026-002` has `$5,000.00` total budget, `$4,500.00` already billed, and only **`$500.00` remaining**. This invoice bills **`$1,100.00`**.*
>
> *Even though the vendor is approved and the invoice arithmetic is valid, Stage 15 catches the `$600.00` overage and immediately **BLOCKS** the invoice with a hard failure. Because our database reservation in Stage 18 uses an atomic conditional SQL update in cents, two concurrent invoices can never overdraw a PO."*

---

## 3:30–4:15 — 5. OCR Fallback & Safe Routing on Low-Quality Scans (`Scenario 5` & `Live Processing View`)

**Where to click:**
1. On **Upload Invoice → Quick Test Scenarios (1-Click)**, click **▶ Run Scenario 5** (*Low-Quality Scan*).
2. Once it finishes with **! Needs review**, click **Live Processing View** in the sidebar.
3. Expand Stage 6 (*Run OCR when necessary*) and Stage 7 (*Extract structured invoice information*).

**What to say:**
> *"Scenario 5 is a blurred raster scan with missing fields. The document classifier detects that it’s an image, engages Tesseract OCR with word-level confidence scoring, and routes the invoice to **`NEEDS_REVIEW`**—never guessing missing PO numbers or tax amounts.*
>
> *In **Live Processing View**, every stage’s status, duration, and evidence is persisted to the database as it executes, and active runs auto-refresh via `st.fragment` polling without blocking the UI."*

---

## 4:15–5:30 — 6. Deep Audit Workbench, PDF Highlighting, Manual Override & Accounting Export (`Scenario 6` → `Invoice Details` → `Invoice History`)

**Where to click:**
1. Go to **Upload Invoice → Quick Test Scenarios (1-Click)** and click **▶ Run Scenario 6** (*Ambiguous PO*).
2. Click **Invoice Details** in the sidebar (it automatically selects the newly processed invoice).
3. In **📄 Document Preview**:
   - Change **Zoom** to `125%`, test **Rotate** (`↻ 90°` and back to `↻ 0°`), and check **Highlight extracted fields** (or type `TechNova` in *Find text on page*) to show translucent bounding boxes drawn directly on the PDF page.
4. Point to **Extracted Structured Fields** (per-field confidence, OCR confidence, verbatim evidence snippet, and converted `INR ₹` amounts) and **3-Way Procurement Comparison** (showing the vendor’s **Probabilistic Confidence %**).
5. Scroll to **✍️ Human-in-the-Loop Review & Decision Override**:
   - Open **Optional reviewer corrections**, select `PO-2026-004 — TechNova Solutions Inc.`, set **Select Override Decision** to `✓ Approved (APPROVED)`, enter a justification reason (`"Confirmed against statement of work; linked to PO-2026-004"`), and click **💾 Save Manual Override & Update Audit Trail**.
6. Scroll down to **📜 Complete Audit Trail** and expand **Reviewer corrections — before and after** to show the immutable diff (`Before → After`) and PO balance reservation.
7. Click **⬇️ Export Xero Bills CSV**, **⬇️ Export QuickBooks CSV**, or **⬇️ Export Audit JSON** at the top of the page (or navigate to **Invoice History** to show SQL-backed `LIMIT/OFFSET` pagination and batch page exports).

**What to say:**
> *"Finally, let’s resolve an exception as an AP analyst. Scenario 6 is from TechNova Solutions with no PO number printed on the invoice, and TechNova has two open POs—so the engine safely routes it to **`NEEDS_REVIEW`**.*
>
> *In **Invoice Details**, the analyst can navigate multi-page PDFs, zoom, rotate sideways scans, and toggle **Highlight extracted fields** to visually spotlight the extracted values right on the PDF page. Alongside it, we see per-field extraction confidence, OCR word confidence, verbatim evidence, and the probabilistic vendor match score.*
>
> *Using the **Human-in-the-Loop Override** form, I link the correct PO (`PO-2026-004`), approve the invoice, and enter a mandatory justification. The engine atomically reserves the PO balance, preserves the original AI extraction payload untouched, and records a complete **before-and-after diff** in the immutable audit trail. From here—or from our SQL-paginated **Invoice History**—we can export ready-to-import **Xero Bills CSV**, **QuickBooks Online CSV**, or **Audit JSON** in one click."*

---

## 5:30–6:00 — 7. Closing: Policy Governance & Local Test Suite (`Settings & Rules`)

**Where to click:**
- Click **Settings & Rules** in the sidebar.
- Point out **Extraction Mode** (`Demo` vs `Live LLM`), **Financial Validation & Matching Tolerances**, **`USD → INR Exchange Rate`**, and **Developer Diagnostics** (`🧪 Run Automated Test Suite (pytest -q)`).

**What to say:**
> *"Under **Settings & Rules**, finance admins can tune arithmetic tolerances, near-duplicate windows, fuzzy match thresholds, and the **`USD → INR` exchange rate**—all validated by Pydantic and persisted in the database across restarts. And in local development, engineers can run the entire **78-test automated pytest suite** right from the diagnostics panel."*
