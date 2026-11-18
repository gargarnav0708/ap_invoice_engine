# Five-Minute Demo Walkthrough

## Before the demo

- Use a fresh disposable database with the seeded vendors and POs.
- Confirm the sidebar reports the extraction mode, `OCR: Tesseract ready`, and the **Theme** selector (`☀️ Light` / `🌙 Dark`).
- Do not upload real invoices; the review label is not authenticated, and the free Render setup is temporary.
- For a near-duplicate demonstration, process Scenario 1 before Scenario 7.

## 0:00–0:40 — Frame the problem

Open **Overview Dashboard** (toggle between `☀️ Light` and `🌙 Dark` in the sidebar if desired). Explain that the engine extracts invoice facts, matches vendors and POs, applies deterministic financial controls, and routes uncertain cases to human review. The LLM extracts fields only; the rules engine makes the decision.

## 0:40–1:35 — Show a clean approval

Open **Upload Invoice → Quick Test Scenarios** and run **Scenario 1: Clean Invoice**. Point out the live 19-stage trace and final `APPROVED` decision. The audit records the PO reservation; the remaining balance decreases by the invoice total.

## 1:35–2:20 — Show a hard stop

Run **Scenario 3: Split PO Overbilling**. Point to the PO balance validation and `BLOCKED` result. Explain that the balance reservation is conditional at commit time, so an invoice cannot spend more than the remaining PO balance.

## 2:20–3:15 — Show OCR and human review

Run **Scenario 5: Low-Quality Scan**. Show that the raster invoice goes through OCR and the incomplete fields result in `NEEDS_REVIEW`, not an automatic approval.

## 3:15–4:35 — Resolve a review

Run **Scenario 6: Ambiguous PO**. Open **Invoice Details**, use the **Document Preview** controls (`Page`, `Zoom`, `Rotate`, and `Highlight extracted fields`) to inspect the invoice alongside the extracted evidence and candidate match, select the correct PO if known, enter a reviewer label and a mandatory reason, then save the review. Show the before/after correction and reason in the audit trail, and optionally export the audited record as **Xero Bills CSV**, **QuickBooks CSV**, or **Audit JSON** (also available across pages in **Invoice History**).

## 4:35–5:00 — Close with controls and limits

Summarize the three outcomes: approval, block, and human review. Note the other demo cases: Scenario 2 exact duplicate, Scenario 7 changed-file near duplicate (after Scenario 1), and Scenario 8 tolerance approval. The Render Blueprint is for a disposable case-study demo; authentication, durable file storage, backups, and long-term database retention are next-stage work.
