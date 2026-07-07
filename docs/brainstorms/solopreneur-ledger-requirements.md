---
date: 2026-07-06
topic: solopreneur-income-expense-ledger
---

# Solopreneur Income/Expense Ledger — Product Pivot

## Summary

Repoint the existing "AI decides before a human touches it; human audits after"
engine from clinical-trial invoice submission to a **self-maintaining income/expense
ledger for solopreneurs**. v1 connects directly to the user's **email inbox** —
where the receipts already pile up — finds the receipts amid the noise, extracts
each one, classifies income vs. expense, assigns a **tax-ready (Schedule C) category**,
and writes it to a Google Sheet the user owns — auto-filing the confident ones and
holding only the uncertain for review. The intake layer stays plural (a watched
folder and forward-to-address are additional sources), and ClinRun and the
clinical-trial machinery are removed.

---

## Problem Frame

A solopreneur's receipts are **scattered across their email inbox** — order
confirmations, SaaS charges, contractor bills, the occasional client payment —
arriving continuously and never collected anywhere. Nothing categorizes them,
nothing totals them, and the inbox is not a ledger. The cost lands all at once:
at tax time (or when a bookkeeper asks), they scramble to reconstruct a year of
deductible expenses from a search-and-forward hunt through email, or they pay
someone to do it, or they simply under-claim and overpay. Tools exist, but they
demand per-receipt forwarding discipline, lock the data inside an app, or are
full accounting suites that a one-person business won't adopt.

The clinical-trial product already solved the hard, general part of this — reading
messy documents, extracting structured line items with confidence, deciding
autonomously whether each is clean enough to file, and leaving a legible audit
trail. That capability was pointed at the wrong outcome (submit to ClinRun) and
wrapped in domain machinery (sponsor/study/site resolution, catalog matching) that
a solopreneur doesn't need.

---

## Actors

- A1. Solopreneur / freelancer (owner-reviewer): points the product at their
  receipt sources, reviews only the items the AI held, and owns the resulting
  Google Sheet. One person wearing both the "owner" and "reviewer" hats.
- A2. Processing agent (the AI): harvests documents, extracts fields, classifies
  income vs. expense, assigns a category, decides auto-file vs. hold, writes rows
  to the ledger, and records a rationale for each.
- A3. Ingestion sources (plural, swappable): the places receipts come from — a
  watched folder, a forwarding address, a connected inbox. Interchangeable
  adapters feeding the same engine; the product is not defined by any one of them.

---

## Key Flows

- F1. Connect the inbox
  - **Trigger:** User sets up the product and connects their email inbox (v1's
    source only; a watched folder and forward-to-address are deferred and not
    user-selectable in v1).
  - **Actors:** A1, A3
  - **Steps:** User grants inbox access → names/points at the destination Google
    Sheet → on first connect the product **backfills** inbox mail back to Jan 1 of
    the current tax year → then watches for new mail.
  - **Outcome:** The current tax year's back-receipts are captured on connect, and
    new mail is scanned automatically thereafter; receipts are picked up, the rest
    ignored.
  - **Covered by:** R11, R13, R20

- F2. Confident item auto-files
  - **Trigger:** A new message arrives at a connected source.
  - **Actors:** A2
  - **Steps:** Detect whether the message is a receipt/bill/payment (skip the rest)
    → extract fields from the attachment or inline body → classify income vs.
    expense → assign a category → decision engine judges confidence high → append a
    row to the Google Sheet with its rationale/audit entry.
  - **Outcome:** The ledger gains a categorized row with no human action; the user
    trusts it was handled.
  - **Covered by:** R3, R4, R5, R6, R7, R8, R9, R13, R14

- F3. Uncertain item is held and resolved
  - **Trigger:** Extraction/classification/categorization confidence is below the
    auto-file bar (ambiguous vendor, unreadable total, unclear category, or
    can't-tell income vs. expense).
  - **Actors:** A2, A1
  - **Steps:** Item is held in the review surface with the flagged reason → user opens
    it, sees the source document + the AI's read → corrects the field/category (or
    confirms) → the item posts to the ledger.
  - **Outcome:** Only genuinely-uncertain items reach the human; the ledger stays
    trustworthy.
  - **Escape path:** User can reject/delete a non-receipt (e.g., marketing email
    misfiled as a receipt) so it never reaches the ledger.
  - **Covered by:** R8, R9, R10

---

## Requirements

**De-clinicalization (the pivot)**
- R1. Remove ClinRun submission and the mock ClinRun backend as the pipeline's terminal step.
- R2. Remove or generalize the clinical-trial-specific stages — sponsor/study/site
  context resolution and sponsor+study catalog matching — replacing them with the
  general classification/categorization described below. No requirement may depend
  on clinical-trial concepts.

**Ingestion (plural sources, email-first)**
- R3. Ingest documents from a source without manual per-document entry. **v1 ships
  connected email** as the user-facing source, and also runs the already-built
  **folder-watcher as an internal validation path** (proving the
  classify → categorize → decide → ledger loop without OAuth / receipt-detection /
  HTML extraction); the intake layer stays source-agnostic so additional sources can
  be added without changing the engine.
- R11. The set of sources is extensible: a watched folder (already built) and
  forward-to-an-address are additional adapters, and the architecture must not
  privilege any single source.
- R13. Because an inbox is noisy, detect whether a message is a receipt/bill/payment
  before it becomes a ledger candidate; non-receipt mail is ignored rather than
  mis-posted or dumped on the reviewer.
- R14. Extract from both **inline email bodies** (HTML/text receipts, e.g. SaaS or
  ride-share) and **attachments** (PDF/image), not attachments alone.
- R17. Because receipt detection (R13) must read every incoming message — including
  personal, medical, or legal mail unrelated to receipts — non-receipt content is
  processed with minimal retention (not persisted or logged once classified as
  non-receipt), and the user is told at connect time that the full inbox is scanned
  to find receipts.
- R20. On first connect, backfill inbox mail back to Jan 1 of the current tax year so
  the first filing is complete, then watch forward. The initial batch of held items
  this produces is absorbed by the hold notification (R18) and default ordering (R10)
  rather than dumped unbounded on the user.

**Extraction & classification**
- R4. Extract the fields a ledger row needs from each document: payee/vendor, date,
  total amount, and (when present) currency and line items.
- R5. Classify each document as **income** or **expense**.
- R6. Assign each item a category from a tax-ready taxonomy (Schedule C-style
  expense categories; a minimal income category set), with per-field confidence.

**Decisioning (reuse the existing engine)**
- R7. Reuse the confidence-based auto-file-vs-hold decision: high-confidence items
  post to the ledger with no human action; anything below the bar is held.
- R9. Every posted or held item carries an explainable rationale and an append-only
  audit entry (what was extracted, how it was classified/categorized, why filed or
  held).
- R15. Because auto-filed rows are never otherwise re-seen, periodically surface a
  sample of already-posted rows for optional confirmation so a rising false-file
  rate is detectable before tax time, not only at intake.
- R16. The auto-file confidence engine's threat model includes spoofed/adversarial
  sender content (a crafted invoice-looking or LLM-manipulating email), not only
  honest-but-ambiguous documents; suspicious signals force a hold rather than an
  auto-post.

**Ledger output (the product's spine)**
- R8. Write each filed item as a row in a user-owned **Google Sheet** ledger,
  separating (or labeling) income vs. expense and including category, vendor, date,
  amount, and a link back to the source document.
- R12. The Google Sheet is the system of record the user owns and can take with
  them — not a view locked inside the app.

**Review & correction**
- R10. Provide a post-decision review surface where the user works only the held
  items, presented in a default order (oldest-first, grouped by flagged reason):
  see the source document + the AI's read — including its chosen category alongside
  its top alternative candidates — correct any field or category, confirm to post,
  or reject a non-receipt so it never lands in the ledger.
- R18. Notify the user when items are held for review (mechanism — digest, badge, or
  similar — deferred to planning), so held items don't sit unseen and silently break
  the "connect and walk away" promise.
- R19. Hold suspected duplicate/same-transaction messages for review (e.g. an order
  confirmation plus a separate payment receipt, or a forwarded copy) rather than
  auto-posting both, so the ledger's totals aren't silently double-counted.

---

## Acceptance Examples

- AE1. **Covers R7, R8.** Given a clearly-legible SaaS invoice PDF with an
  unambiguous vendor and total, when it arrives at a connected source, then it is
  categorized as an expense and appended to the Google Sheet with no human action.
- AE2. **Covers R6, R7, R10.** Given a receipt whose category is ambiguous (could
  be "Office expense" or "Supplies"), when confidence for the category falls below
  the bar, then the item is held for review rather than posted, and surfaces the
  candidate categories to the user.
- AE3. **Covers R5, R10.** Given a document the AI can't confidently tell is income
  vs. expense, when it is processed, then it is held (not guessed) with that
  specific reason flagged.
- AE4. **Covers R10.** Given a marketing email misidentified as a receipt, when the
  user opens the held item, then they can reject it so it never appears in the ledger.
- AE5. **Covers R13.** Given a newsletter or personal email in the inbox, when it is
  scanned, then it is recognized as a non-receipt and ignored — it never becomes a
  ledger candidate or a held item.
- AE6. **Covers R14.** Given a SaaS charge that arrives as an inline HTML receipt
  with no attachment, when it is processed, then its vendor/date/total are extracted
  from the email body and it posts (or holds) like any attachment-based receipt.

---

## Success Criteria

- A solopreneur can connect one source, walk away, and later find a categorized
  **email-sourced** income/expense Google Sheet that is trustworthy enough to hand to
  a bookkeeper or use as the email-derived portion of tax prep — having only touched
  the items the AI flagged. (v1 captures what arrives as email; non-email expenses —
  cash, card-only, mileage, home-office — are out of scope and reconciled separately.)
- Auto-file rate is high enough that review is a small, bounded task, while
  false-file rate (wrongly categorized items that posted silently) stays low —
  the same throughput-vs-trust guardrail as the original strategy, retargeted.
- ce-plan can start without inventing product behavior: the source(s) for v1, the
  category taxonomy, the ledger's contents, and the auto-file-vs-hold contract are
  all specified here.

---

## Scope Boundaries

### Deferred for later

- Additional ingestion adapters beyond v1's connected email — forward-to-an-address,
  and surfacing the already-built folder-watcher as a selectable source.
- User-defined / custom categories; v1 ships the fixed Schedule C-style set only.
- Rich income-side handling (multiple income categories, client-invoice tracking);
  v1 classifies income but keeps its category set minimal.
- Direct sync/integration with accounting tools (QuickBooks, Xero) or exporting
  formats beyond the Google Sheet.
- Recurring-vendor rules / learned auto-categorization from user corrections.
- Non-email expense capture — cash, card-only, mileage, home-office allocation. v1
  is email-sourced only; these are reconciled outside the tool (a known coverage gap
  for the tax-time use case).
- Full automatic duplicate dedup across messages; v1 holds suspected duplicates for
  review (R19), but automatic same-transaction merging is deferred.

### Outside this product's identity

- A full accounting application — double-entry, accounts payable/receivable,
  bank reconciliation, financial statements. This feeds a ledger; it is not QuickBooks.
- Small-business features — multi-user roles, approval chains, org accounts. The
  beachhead is a single solopreneur.
- A personal-finance / budgeting app — spending goals, net-worth tracking, bank
  aggregation. The wedge is tax-shaped income/expense capture, not budgeting.

---

## Key Decisions

- Product shape = **ledger-as-product with plural, swappable ingestion** (approach
  C), chosen over email-native (B) and folder-only (A): bets on durability and
  "own your data" positioning over the fastest single demo.
- **Beachhead = solopreneur/freelancer**, chosen over small-business and personal
  finance: a sharp, tax-time-shaped wedge with a spreadsheet-native user.
- **v1 ships both email and the folder-watcher.** Native connected email is the
  user-facing headline source — it attacks the real pain ("receipts scattered in
  email") and gives the "connect your inbox, get your books" demo (accepting the
  added surface: OAuth, receipt-vs-noise detection (R13), inline HTML/text extraction
  (R14)). In parallel, the already-built folder-watcher runs as an **internal
  validation path** that proves the classify → categorize → decide → ledger loop
  cheaply, independent of the three new email surfaces. This directly hedges the
  schedule risk the doc review flagged (product-lens + scope-guardian): the core
  engine can be validated even if an email surface slips. Forward-to-address follows.
- **First connect backfills the current tax year** (R20), not forward-only: the
  tax-time pitch assumes the year's back-receipts, so a forward-only start would ship
  a guaranteed first-year gap. The one-time onboarding batch is absorbed by the hold
  notification (R18) and default review ordering (R10).
- **Category taxonomy = tax-ready Schedule C-style, fixed set with override in
  review.** The differentiated replacement for the old catalog-matching engine and
  the reason a solopreneur picks this over a generic receipt scanner. Custom/user-
  defined categories are deferred; v1 ships the canonical set with no per-user setup.
- **v1 classifies both income and expense, but is expense-category-rich and
  income-category-minimal** — the surfaced pain (receipts in email) is expense-side,
  and expense volume dominates.
- **Preserve the human-after engine and audit trail** — the pivot reinterprets the
  domain stages, it does not rebuild the decision/review/observability spine.
- **v1 is single-tenant** — one deployment / credential set per solopreneur, matching
  today's single-`Settings` architecture. F1's "user grants access" is a per-deploy
  setup step, not a multi-user SaaS onboarding flow (multi-tenant credential storage
  and isolation are a separate, later bet — see Outside this product's identity).

---

## Dependencies / Assumptions

- Assumes the existing pluggable intake abstraction and the confidence-based
  decision + review + audit spine carry over unchanged conceptually — the pivot
  swaps the domain-specific stages and the terminal destination, not the engine.
  (Verified: intake is already a source-agnostic seam with multiple providers, and
  a submit/hold decision + reviewer hub + append-only audit already exist.)
- Assumes the product can obtain read access to the user's inbox (OAuth/read scope
  for the chosen email provider). The specific provider (Gmail first?) and auth flow
  are planning concerns.
- Assumes an inbox is noisy, so a receipt-vs-non-receipt detection step (R13) is a
  required part of v1, not an enhancement.
- Assumes writing to a user-owned Google Sheet is an acceptable v1 system of record
  (mirrors the "spreadsheet is the solopreneur's tool" premise). Auth/permission
  model for that Sheet is a planning concern.
- Assumes a tax-ready category taxonomy can be sourced/defined without per-user
  configuration for v1 (a fixed Schedule C-style set), with override in review.
- Inbox-read and Sheet-write OAuth tokens are **sensitive credentials** (effectively
  keys to the user's mailbox and financial ledger): they require secure storage,
  minimally-scoped grants (read-only inbox), and a revocation path. Mechanism is a
  planning concern; the security posture is not optional.
- The audit trail (R9) and held-item store (R10) persist extracted financial data
  outside the user-owned Sheet. Assumes a retention/deletion expectation (kept only
  as long as needed for review/audit) and owner-only access; specifics deferred to
  planning.

---

## Outstanding Questions

_The scope forks are decided (v1 = email + folder-watcher validation path;
backfill = current tax year; categories = Schedule C — see Key Decisions). Two
risk flags from the 2026-07-06 review remain worth validating but do not block
planning._

### Validate (not blocking)

- [Affects Problem Frame][Needs research] **Beachhead is asserted, not evidenced** —
  no cited user research/churn/complaints back the solopreneur-email pain as the
  highest-leverage wedge. Validate before over-investing. (Product-lens.)
- [Affects Key Decisions][Risk] **Full-inbox OAuth as an activation barrier** —
  granting an unproven tool read access to an entire inbox (incl. personal/client
  mail) is a known trust hurdle that may block activation before value is shown;
  worth weighing against the forwarding-friction it removes, and a reason the
  folder-watcher validation path is a useful hedge. (Product-lens.)

### Deferred to Planning

- [Affects R3, R11][Technical] Email provider for v1 (Gmail first?), the OAuth/read
  scope, and the polling/watch mechanism for new mail.
- [Affects R13][Needs research] How receipt-vs-non-receipt detection works and where
  its confidence bar sits relative to the auto-file bar.
- [Affects R14][Technical] Extracting from inline HTML/text email bodies vs.
  attachments — one path or two.
- [Affects R8][Technical] Google Sheet write mechanism, auth model, and how income
  vs. expense are laid out (two tabs vs. one ledger with a type column).
- [Affects R6][Needs research] Source of a canonical Schedule C-style category list
  and how line-item-level vs. document-level categorization is handled.
