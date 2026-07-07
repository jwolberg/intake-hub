# Architecture: IntakeHub

This document describes the technical architecture for the AI-first solopreneur
tax-ledger intake, classification, and filing workflow. It is derived from
[`challenge.md`](./challenge.md) and the original [`PRD.md`](./PRD.md) (both
written for the tool's clinical-trial-invoice predecessor; see the pivot's
origin document, [`docs/brainstorms/solopreneur-ledger-requirements.md`](./brainstorms/solopreneur-ledger-requirements.md)),
and it keeps the product principle established there: **the AI processes each
inbound receipt/invoice end to end and decides file-vs-hold before any human
touches it; reviewers validate outcomes after the fact.**

It is a design reference, not a status report. Where the requirements state
*what* must be true, this document states *how* the system is structured to
make it true.

## 1. Guiding Architectural Principles

1. **Staged pipeline with hard boundaries.** Intake, parsing, extraction,
   classification/categorization, decisioning, and filing are independent
   stages. Each consumes a typed input and produces a typed, persisted output.
   No stage reaches into another's internals.
2. **AI-first, human-after.** The pipeline runs to a terminal decision (`file`
   or `hold`) with no human gate. Human QC operates on the *result*, never as a
   precondition.
3. **Everything is explainable and persisted.** Every stage records its output,
   confidence, and rationale. The reviewer hub and the audit trail are reads
   over this persisted state — they never recompute.
4. **Fail isolated, fail visible.** One item failing a stage never blocks
   others. A failed stage records a typed error and either holds the item or
   marks a retryable failure; it never crashes the pipeline.
5. **Low confidence holds; it never silently files.** Ambiguity and low
   confidence resolve toward `hold`, which is safe and visible, rather than
   toward a silent auto-file, which is not.

## 2. System Context

```
                ┌────────────────────────────────────────────┐
   Gmail /      │                 IntakeHub                   │
   Drive   ───▶ │                                              │
   receipts     │   Intake → Pipeline → Decision → Hub         │
                │                  │            │              │
                └──────────────────┼────────────┼──────────────┘
                                   │            │
                            LLM    │            │  Google Sheets
                       (extract/   │            ▼  (ledger projection)
                        classify)  ▼      Append filed row
                            (advisory,       (gated by the
                        heuristics-first)   sheet_appends
                                              dedup ledger)
```

External dependencies:

- **Inbox source** — one of three pluggable providers: the offline `mock` demo
  set, a watched **Google Drive folder**, or a connected **Gmail mailbox**
  (§7). Treated as a remote dependency that can be slow, empty, or unreachable.
- **Google Sheets** — the destination for filed (auto-approved) items: a
  user-owned spreadsheet that IntakeHub appends ledger rows to. The system
  targets an offline stub by default and the real Sheets API when a service
  account + spreadsheet id are configured (§8).
- **LLM provider** — used for extraction, receipt/non-receipt filtering,
  income/expense classification, Schedule C categorization, and decision
  rationale. Accessed behind a provider-agnostic interface; every LLM call is
  heuristics-first and advisory-only (§9).

## 3. Logical Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Reviewer Hub (SPA)                            │
│   Item list · Detail view · QC actions (correct / rerun / reject)         │
└───────────────────────────────┬────────────────────────────────────────────┘
                                 │  REST/JSON
┌───────────────────────────────▼────────────────────────────────────────────┐
│                                 API Layer                                    │
│  /invoices  /invoices/:id  /inbox/fetch  /corrections  /rerun  /reviewed …  │
│  /notifications  /spot-check                                                │
└───────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────────┐
│                          Workflow Orchestrator                               │
│   Drives stages, owns state transitions, writes audit events                 │
└──┬──────────┬───────────┬────────────┬────────────┬───────────┬─────────────┘
   │          │           │            │            │           │
┌──▼───┐  ┌───▼────┐  ┌───▼─────┐  ┌───▼─────┐  ┌───▼─────┐  ┌──▼────────┐
│Inbox │  │ Doc    │  │  LLM    │  │Categorize│  │ Decision│  │  Sheets   │
│(Mock/│  │ Parser │  │Extractor│  │(type+cat)│  │ Engine  │  │  Client   │
│Drive/│  └────────┘  └─────────┘  └────┬─────┘  └────┬────┘  └─────┬─────┘
│Gmail)│                                │             │             │
└──┬───┘                          ┌─────▼─────┐  ┌────▼─────┐       │
   │                              │  Receipt  │  │  Ledger  │       │
   └─────────────────────────────▶│  Filter   │  │Integrity │◀──────┘
                                  └───────────┘  │(dup/spot-│
                                                  │  check)  │
                                                  └──────────┘

         ┌──────────────────────────────────────────────────────────┐
         │     Persistence (invoices, line items, exceptions,          │
         │     audit events, sheet_appends, oauth_tokens,               │
         │     gmail_sync_state)                                        │
         └──────────────────────────────────────────────────────────┘
```

## 4. Module Boundaries

The codebase enforces a required separation carried over from the tool's
original design. Each module is independently testable and depends only on
shared domain types, not on sibling modules.

| Module | Responsibility | Key inputs | Key outputs |
| --- | --- | --- | --- |
| `inbox` | Pluggable inbox providers (`MockInbox`, `DriveInbox`, `GmailInbox`) behind one `InboxClient` protocol; map a provider message to the orchestrator's intake sample | Provider-specific message | `InboxMessage` |
| `receipts` | Pre-extraction receipt-detection filter — is this message a receipt at all? | Sender/subject/attachment/body | `ReceiptVerdict` |
| `intake` | Receive a sample, register a workflow record, store source metadata | Email / sample file | `Invoice` record (status `received`) |
| `parser` | Extract raw text + structure from PDF/image/body; **(Phase 4)** rasterize pages to normalized images | Attachment, body | Parsed document text + sections; **page rasters + dims** |
| `ocr` *(Phase 4)* | Per-word boxes with normalized `[0,1]` coords + indices per page, behind an `OCRClient` (Tesseract default, offline stub) | Page raster | `WordBox[]` |
| `extraction` | LLM-driven structured extraction of metadata + line items; **(Phase 4)** vision extraction citing OCR word indices | Parsed text; **page image + `WordBox[]`** | `InvoiceMetadata`, `LineItem[]`, confidences; **`Citation[]`** |
| `categorize` | Classify income vs expense and assign a Schedule C category (matching-analog: heuristic candidates + advisory LLM adjudication on ambiguity) | `ExtractionResult`, source | `CategorizationResult` (type + category + confidences + alternates) |
| `decision` | Apply policy → `submit` (file) or `hold` with rationale, over a weakest-link confidence | All stage outputs + confidences | `DecisionResult` + `risk_flags` |
| `ledger_integrity` | Duplicate-post detection (R19) and spot-check sampling (R15) over already-posted items | `Invoice` list | duplicate match / sampled subset |
| `clients.sheets` | Append a filed row to the user's Google Sheet, gated by the Postgres `sheet_appends` dedup ledger | `Decision=submit`, ledger row | append outcome (ref, deduped) |
| `exceptions` | Create typed exception records on hold/failure | stage signals | `Exception[]` |
| `audit` | Append immutable audit events | every stage + human action | `AuditEvent[]` |
| `corrections` | Replay the audit trail into effective metadata/category/line-item overlays (never mutates AI output) | audit events | overlaid values for detail view + rerun |
| `orchestrator` | Sequence stages, own state machine, isolate failures | invoice id | state transitions |
| `api` | HTTP surface for the hub | requests | JSON responses |
| `hub` (frontend) | Post-decision review + QC actions | API | rendered views, user actions |

**Dependency rule:** modules depend downward on shared domain types and on the
orchestrator's contract, never sideways. `categorize` does not import
`decision`; it receives an `ExtractionResult` and returns a
`CategorizationResult` as an input to `decision`. This keeps each stage
unit-testable in isolation and mockable for integration tests.

**Phase 4 — Visual Document Review** (`/docs/specs/visual-document-review.md`)
adds the `ocr` module and extends `parser` (rasterization) and `extraction`
(vision) without disturbing the dependency rule: OCR runs on the parser's page
rasters, vision extraction consumes the image + `WordBox[]`, and everything
downstream of extraction (categorize … decision, QC, rerun, metrics) is
unchanged — `Citation[]` is additive metadata on the extraction output.

**Removed by the ledger pivot** (see the plan's Unit U5): the `context`
(sponsor/study/site resolution), `catalog`, and `matching` modules; the
`submission` module and its `ClinRunClient`; the MCP-wrapped reference client;
and the `resolved_context` / `match_results` / `catalog_cache` tables. Their
role is replaced end-to-end by `categorize` → `decision` → `clients.sheets`, as
described below.

## 5. Pipeline Data Flow

For a single item, the orchestrator drives this sequence. Each arrow persists
the stage output and appends an audit event before the next stage starts, so
the item is always inspectable at its latest completed stage.

```
received
  └▶ parse ───────────▶ parsed text
       └▶ extract ─────▶ metadata + line items (+confidence)
            └▶ classify ─▶ income | expense (+confidence)
                 └▶ categorize ─▶ Schedule C category (+confidence, +alternates)
                      └▶ decide ─▶ submit | hold (+rationale, +risk flags)
                           └▶ file to Sheet  → posted (if submit, no duplicate)
                              or
                           └▶ hold as suspected duplicate (if submit, but a
                              matching posted item exists — R19)
                              or
                           └▶ exception → hold record (if hold)
```

Receipt filtering happens *before* this sequence, at the inbox boundary (§7):
`GmailInbox` runs `classify_receipt` on each candidate message and drops clear
non-receipts before parsing/extraction ever run, so newsletters and personal
mail never become ledger candidates.

Each stage can short-circuit to a terminal state:

- A **hard failure** (e.g., extraction throws, the Sheet append persistently
  fails) → mark stage `failed`, create an exception, set the item to `held` or
  a retryable `failed` state. Downstream stages are skipped.
- A **low-confidence / ambiguous** result is *not* a failure — the stage
  succeeds, the pipeline continues, and the accumulated risk surfaces in the
  decision stage, which holds the item.

## 6. Workflow State Machine

The orchestrator owns invoice state. States and legal transitions:

```
received → parsed → extracted → classified → categorized
                                                  │
                                                  ├─▶ posted            (terminal-success)
                                                  └─▶ held              (terminal-hold)

any stage ─▶ failed            (retryable; → rerun_requested)
held / failed / posted ─▶ rerun_requested ─▶ (re-enter pipeline)
held / failed ─▶ escalated     (manual handling)
held ─▶ rejected               (reviewer marks a hold as not ledger-worthy)
any state + human edit ─▶ corrected (then typically → rerun_requested)
```

Rules:

- Transitions are append-only audit events; the current state is the latest
  event.
- `failed` is distinguished from `held`: `failed` means a stage errored (or the
  Sheet append could not complete) and is retryable; `held` means the AI made a
  deliberate decision not to file.
- `rerun_requested` re-enters the pipeline using corrected data where present
  (see §11); parsing and extraction are not re-run (their outputs are reused
  with corrections overlaid), and categorization + decision always recompute so
  a correction that resolves the original hold reason lets the item post on
  rerun.
- On a *first* pass, a `submit` decision that matches an already-posted item on
  vendor + amount within a short date window is held as a
  `suspected_duplicate` rather than posted (R19, `ledger_integrity`); a
  `rerun`/`recover` skips this check, so a reviewer who confirms the item is
  genuinely distinct can force the post.

## 7. Inbox Providers

Every item enters the pipeline through one of three interchangeable
`InboxClient` implementations, selected by `INBOX_PROVIDER`:

- **`mock` (default)** — `MockInbox` replays a curated offline demo set with no
  network dependency; the pipeline's out-of-the-box, credential-free path.
- **`drive`** — `DriveInbox` reads a Google Drive folder shared with a service
  account. It lists the folder root for new PDFs, downloads and processes each,
  then moves it into a `submitted` / `needs-review` / `failed` subfolder
  reflecting the decision at processing time. Setup:
  [`drive-intake-setup.md`](./drive-intake-setup.md).
- **`gmail`** — `GmailInbox` reads a connected Gmail mailbox via user-OAuth
  (personal Gmail cannot use a service account — no domain-wide delegation).
  Setup: [`gmail-sheets-setup.md`](./gmail-sheets-setup.md).

Design choices shared across providers:

- **The client is the only place that knows the transport exists.** Stages
  depend on the `InboxClient` domain interface (`fetch_messages`,
  `on_processed`), so any provider can be swapped for a fixture in tests
  without touching pipeline logic.
- **Idempotency is provider-owned.** Each provider records a message as *seen*
  before reflecting any state externally (a Drive file move; nothing, for
  Gmail — see below), so a crash or a failed reflection can never cause
  reprocessing.
- **`on_processed` is a best-effort reflection hook, not a requirement.**
  `DriveInbox` moves the processed file into its status subfolder.
  `GmailInbox.on_processed` is a documented no-op: the integration
  deliberately requests only the `gmail.readonly` scope (least privilege,
  R20), which has no modify/label capability, so a processed message is never
  labeled or archived — the hub, not the mailbox, is the source of truth for
  review state.

### Gmail sync strategy

`GmailInbox` runs a **receipt filter** (`backend.receipts.classify_receipt`) on
each candidate message's cheap metadata *before* fetching its full body. A
confident non-receipt is dropped — its id/sender/subject are marked seen but
its body is never fetched, let alone persisted (R17 minimal retention); an
ambiguous verdict defaults to "needs review" rather than a silent drop, so the
item still flows on to be held for a human.

Sync is incremental:

- **First connect** (no stored `historyId`) — a full `list_messages` backfill
  to `after:<GMAIL_TAX_YEAR>/01/01`, paginated to exhaustion, then a
  best-effort bootstrap of the incremental cursor.
- **Subsequent fetches** — `history.list` deltas from the stored `historyId`.
  A stale/expired id (Gmail 404s it) falls back to a full backfill rather than
  raising, so a missed sync window degrades to "re-scan" rather than "silently
  drop mail."

The refresh token that authenticates the mailbox is encrypted at rest
(`GMAIL_TOKEN_ENC_KEY`, Fernet) in the `oauth_tokens` table when a key is
configured; without one it falls back to reading `GMAIL_REFRESH_TOKEN` from the
environment on every use rather than ever persisting it in cleartext. An
explicit revoke path (`backend/tools/gmail_revoke.py`) invalidates the token at
Google and clears the local encrypted copy + sync cursor.

## 8. Google Sheets Ledger Output

Filed (`submit`) items are appended as rows to a **user-owned Google Sheet** —
the ledger's durable, human-facing projection. `SheetsClient` is a narrow
protocol (one operation, `append_row`) mirroring the `Protocol` +
`Http…`/`Stub…` shape used by the inbox clients:

- **`StubSheetsClient`** — an in-memory fake; the offline default when no
  spreadsheet id / credentials are configured.
- **`HttpSheetsClient`** — calls the Sheets v4 REST API, authenticated as a
  **service account** (`drive.file` scope — the service account owns the sheet
  it creates, mirroring the Drive folder-intake auth pattern). If no
  `SHEETS_SPREADSHEET_ID` is configured, the first append creates a new
  spreadsheet and writes the header row before appending data.

**The Sheet is never the source of truth for "was this already posted."** That
role belongs to the Postgres `sheet_appends` table: `append_filed_row` checks
it first (keyed by the invoice id, stable across retries/rerun/recover) and
short-circuits to a no-op if the key is already recorded, only calling
`append_row` — and recording the ref — for a genuinely new post. A row can
always be recreated from the ledger database; the ledger cannot be
reconstructed from the Sheet. A persistent append failure marks the item
`failed` with a retryable `sheet_write_failed` exception rather than silently
losing it (a few in-process retries absorb a transient error first).

Each ledger row is `[Type, Category, Vendor, Date, Amount, Source]` — the
income/expense determination, the Schedule C category, the vendor name, the
invoice date, the total amount, and a human-facing source reference (a Gmail
message id, an attachment name, or a subject).

## 9. LLM Integration

LLM calls happen behind a single provider-agnostic `LLMClient` interface so the
provider (Anthropic or equivalent) is a configuration detail. In the ledger
pivot, every LLM call is **heuristics-first and advisory**: a heuristic pass
decides confident/clear cases without a model call at all, and the LLM is only
consulted for the genuinely ambiguous band, exactly like the matching-analog
`categorize` stage (§9.1) and the `receipts` filter.

- **Structured output is mandatory.** Extraction, categorization, and
  receipt-classification calls request JSON and are validated before use.
  Invalid or unusable output (including the offline stand-in's echo, or a
  connection error via `FallbackLLMClient`) falls back to the heuristic
  result — which for a genuinely ambiguous item is low-confidence, so the
  decision engine *holds* rather than mis-filing, never crashes.
- **Confidence is captured per field/item**, not just per call, so the
  reviewer hub can highlight exactly which extracted value, classification, or
  category is uncertain.
- **Determinism for tests.** Prompts and parsing are pure functions of their
  inputs; tests inject a recorded/stub `LLMClient` so pipeline logic is
  verified without live model calls.
- **The document is data, never instructions (R16).** `categorize` and
  `receipts` treat inbound message/document content as data to classify, and
  explicitly instruct the model to ignore any embedded directives — the
  `categorize` stage additionally runs a pattern-based adversarial-content
  check (prompt-injection tells) independent of the LLM call, which forces a
  `suspected_adversarial` hold regardless of confidence.
- **Vision extraction (Phase 4)** is unchanged by the pivot: the `LLMClient`
  gains a vision path that receives the rendered page image plus the OCR word
  list and returns per-field/line values with `word_indices` citations,
  resolved server-side to bounding boxes from real OCR geometry so the model
  cannot hallucinate a highlight. See `/docs/specs/visual-document-review.md`.

### 9.1 Classification + Categorization Strategy

`categorize` determines **income vs. expense** and assigns a **Schedule C
category**, structured as the matching-analog of the pre-pivot `matching`
stage so its output slots into the decision engine's weakest-link `min()` the
same way match/context confidence did before:

1. **Heuristic candidates** — score every Schedule C category by keyword
   overlap against the vendor name, line-item descriptions, and message
   subject. A clear winner (confident, well-separated from the runner-up)
   needs no model call, so the offline demo/folder-watcher validation path
   files clean receipts deterministically.
2. **LLM adjudication (advisory)** — only when the top categories are close,
   or income-vs-expense is unclear from vendor/total signals, an `LLMClient`
   picks the best mapping. An unusable response falls back to the heuristic
   result.

Each `CategorizationResult` carries the type + category as annotated cells
(value, confidence, evidence), ranked `alternates` for the reviewer, and an
`adversarial` flag. An unresolved category race (top two candidates within a
small gap) is capped below the auto-file confidence bar, so an ambiguous
category is *held*, never guessed and filed.

## 10. Decision Engine

The decision engine consumes every prior stage output and applies an explicit,
testable policy to produce a structured `DecisionResult` (`submit` | `hold`,
confidence, rationale, `risk_flags[]`, `required_human_actions[]`).

Policy is data-driven so it is auditable and unit-testable in isolation, and
runs over a **weakest-link confidence**: `min()` of per-field extraction
confidence, the income/expense determination, and the Schedule C category
confidence. Low confidence anywhere resolves toward a hold.

| Condition | Decision |
| --- | --- |
| High confidence across extraction, income/expense, and category; no blocking flag | Submit (file) |
| Content looks spoofed / prompt-injection-style (`suspected_adversarial`) | Hold |
| Income vs. expense could not be confidently classified (`ambiguous_income_expense`) | Hold |
| Category could not be assigned with confidence (`low_category_confidence`) | Hold |
| Required field missing (e.g. total amount) | Hold |
| Low extraction confidence | Hold |
| Line items don't sum to the stated total | Hold |
| Weakest-link confidence below the file threshold (0.8) | Hold |
| Matches an already-posted item (vendor + amount + date window) | Hold (`suspected_duplicate`) |
| Sheet append persistently fails | Retryable failure (`sheet_write_failed`) |

The engine never requires human approval to reach a decision. Severity levels
(low/medium/high) map to whether a risk flag is informational, visibility-only,
or blocking — a high-severity flag always forces a hold. `decision_confidence`
is the same combined weakest-link value for both submit and hold, so a low
value *explains* a hold (held because uncertain) rather than being a separate,
disconnected signal.

## 11. Ledger Integrity (Duplicate Holds, Notifications, Spot-Checks)

Auto-filing is only trustworthy if the ledger it produces stays trustworthy
*after* the fact. Three guards close that loop:

- **Duplicate holds (R19, `ledger_integrity.find_duplicate`)** — an
  order-confirmation email and a separate receipt for the same charge must not
  both post. A `submit`-decided candidate that matches an already-posted item
  on vendor + exact amount within a short date window is held as
  `suspected_duplicate` instead of double-posted. v1 only *holds* suspected
  duplicates; it never auto-merges. A reviewer who confirms two similar items
  are genuinely distinct resolves it via `rerun` (which skips the duplicate
  check on the second pass).
- **Held-item notifications (R18, `GET /api/notifications`)** — a queryable
  digest of the total held count plus a breakdown by hold reason, so a flood
  of one kind (e.g. a Gmail backfill surfacing many low-category-confidence
  items at once) is visible at a glance rather than requiring the reviewer to
  page through the list.
- **Spot-check sampling (R15, `POST /api/spot-check`, `ledger_integrity.sample_posted`)**
  — a bounded random subset of already-posted items, so a reviewer can
  periodically confirm the AI's autonomous posts were correct. Sampling an
  item records a system-attributed audit event, so the spot-check itself is on
  the audit trail.

## 12. Human QC & Correction Loop

QC is strictly post-decision. The hub exposes actions that all produce audit
events attributed to a human actor, keeping AI decisions and human edits
distinguishable:

- **Mark reviewed** — acknowledges an outcome without changing it.
- **Correct metadata / correct category** — writes a correction record that
  overlays the AI's extracted value or assigned Schedule C category.
- **Reject** — marks a held item as not ledger-worthy (e.g. a false-positive
  receipt classification) without posting it.
- **Rerun** — re-enters the pipeline. Corrected fields are treated as fixed
  inputs; extraction is not re-run, categorization and the decision always
  recompute, so a correction that resolves the original hold reason lets the
  item file on rerun. The duplicate check is skipped on rerun (§6, §11).
- **Escalate** — routes the item to manual handling.

Corrections never mutate the original AI output in place; they are overlays
(`backend/corrections`, replayed from the append-only audit trail), so the
audit trail always shows what the AI produced versus what a human changed.

## 13. Persistence Model

A relational store (PostgreSQL) holds the workflow state. Core tables:
`invoices`, `line_items`, `exceptions`, `audit_events` (append-only), plus
three tables added by the pivot: `sheet_appends` (the dedup gate for Sheet
posts, §8), `oauth_tokens` (encrypted Gmail refresh token), and
`gmail_sync_state` (the incremental `historyId` cursor). `seen_messages` (inbox
idempotency) is unchanged from before the pivot.

Design notes:

- **Stage outputs are persisted, not just final state**, so the hub renders
  any item at its latest completed stage and reruns can diff against prior
  outputs.
- **`audit_events` is append-only** and is the source of truth for current
  state and for the AI-vs-human distinction.
- **`exceptions` carry type + severity + message**, so the hub can filter by
  exception kind and the decision engine can reason over them.
- **`sheet_appends` is the ledger's dedup source of truth**, not the Sheet
  itself (§8) — a row can always be recreated from this table; the reverse is
  not true.
- **Citations (Phase 4)** are unchanged by the pivot — see the pre-pivot
  design in `/docs/specs/visual-document-review.md`.

## 14. API Surface

The API layer is a thin translation of orchestrator/state into JSON.
Endpoints and their roles:

| Endpoint | Method | Role |
| --- | --- | --- |
| `/api/invoices/process` | POST | Process one sample directly (bypasses the inbox) |
| `/api/inbox/fetch` | POST | Pull new messages from the configured `InboxClient`, process each, skip already-seen |
| `/api/invoices` | GET | List with status, decision, confidence, exception summary, filter tags |
| `/api/invoices/:id` | GET | Full detail: source, extraction, categorization, decision, exceptions, audit |
| `/api/invoices/:id/trace` | GET | The stage-by-stage state trace for one item |
| `/api/review-queue` | GET | Held/failed items ordered for reviewer triage |
| `/api/metrics` | GET | Aggregate strategy metrics |
| `/api/notifications` | GET | Held-item digest by hold reason (R18, §11) |
| `/api/spot-check` | POST | Sample already-posted items for reviewer confirmation (R15, §11) |
| `/api/invoices/:id/corrections/metadata` | POST | Reviewer corrects extracted metadata |
| `/api/invoices/:id/corrections/category` | POST | Reviewer corrects the Schedule C category |
| `/api/invoices/:id/rerun` | POST | Re-run with original or corrected data |
| `/api/invoices/:id/retry` | POST | Retry a failed stage without resetting to the start |
| `/api/invoices/:id/reject` | POST | Mark a held item as not ledger-worthy |
| `/api/invoices/:id/reviewed` | POST | Mark reviewed post-decision |
| `/api/invoices/:id/escalate` | POST | Escalate for manual handling |
| `/api/invoices/:id/pages` | GET | *(Phase 4)* Page count + raster dimensions |
| `/api/invoices/:id/pages/:n/image` | GET | *(Phase 4)* Rendered page raster (PNG/JPEG) for the overlay |
| `/api/invoices/:id/source.pdf` | GET | The original source PDF, when stored |

The hub reads list/detail and triggers QC actions; it does no business logic of
its own — all decisioning lives server-side and is merely surfaced.

## 15. Reviewer Hub (Frontend)

A single-page app (React/Vite) with two primary surfaces:

- **List view** — invoice number, vendor, total, status, decision, confidence,
  exception count, last-updated; filterable by posted / held / failed /
  needs-review / low-confidence.
- **Detail view** — sectioned: source, extracted metadata (with confidence +
  editable correction), classification + category (with confidence + editable
  correction + alternates), decision (submit/hold + confidence + rationale +
  risk flags + Sheet-post status), and QC actions.

The hub's only job is post-decision visibility and QC — it embodies the
"human-after" principle in UI form.

## 16. Failure Handling & Observability

- **Isolation:** the orchestrator processes items independently; an exception
  in one never aborts a batch fetch.
- **Typed errors:** each stage failure records stage name, message, and
  whether it is retryable vs terminal.
- **Retry/recovery:** retryable failures (e.g. `sheet_write_failed`) are
  re-runnable from the failed stage without re-doing successful upstream
  stages.
- **Low-confidence path:** ambiguity routes to `hold`, never silent filing.
- **Fallback-safe LLM path:** `FallbackLLMClient` degrades a live-provider
  connection error to the offline stand-in rather than failing the item — this
  matters concretely in the current Cloud Run deployment, whose egress to
  `api.anthropic.com` is blocked (§18, [`DEPLOY.md`](./DEPLOY.md)).
- **Traceability:** the audit event stream per item makes the full path
  through the pipeline reconstructable for debugging and demo clarity.

## 17. Technology Stack

| Concern | Choice |
| --- | --- |
| Backend | Python / FastAPI |
| Frontend | React / Vite |
| Database | PostgreSQL |
| LLM | Anthropic (or equivalent), behind `LLMClient`, wrapped in `FallbackLLMClient` |
| Inbox | `InboxClient`: `MockInbox` / `DriveInbox` (Google Drive, service account) / `GmailInbox` (Gmail, user-OAuth) |
| Ledger output | `SheetsClient`: `StubSheetsClient` / `HttpSheetsClient` (Google Sheets, service account) |
| Orchestration | Docker Compose |
| Tests | pytest |

## 18. Deployment Topology

`docker-compose` brings up the full local environment:

```
services:
  api      # FastAPI backend + orchestrator
  hub      # React/Vite frontend
  db       # PostgreSQL
  poller   # inbox-fetch loop sidecar (profiles: drive, gmail)
```

The demo mock inbox needs no external service. `INBOX_PROVIDER=drive` and
`INBOX_PROVIDER=gmail` add credentials via env vars (`.env.example`) rather
than additional containers — there is no internal reference/submission
microservice to stand up, unlike the tool's clinical-trial predecessor.

**Known limitation:** the current Cloud Run deployment's egress to
`api.anthropic.com` is blocked, so the API runs with no `ANTHROPIC_API_KEY`
bound and all LLM calls go through `FallbackLLMClient`'s offline path — the
pipeline holds rather than crashes. See
[`DEPLOY.md`](./DEPLOY.md#known-limitation-outbound-egress-to-apianthropiccom-is-blocked)
for the full diagnosis and the VPC-connector + Cloud-NAT follow-up.

## 19. Testing Strategy

Aligned with the staged boundaries:

- **Unit tests** per module: extraction schema validation, the receipt filter,
  categorization heuristics + ambiguity handling, decision policy table,
  exception creation, state transitions, the Drive/Gmail/Sheets clients and
  inbox providers (heuristics + protocol behavior, all against in-memory
  stubs — no network).
- **Integration tests**: end-to-end processing of sample invoices, the
  inbox-fetch route against each provider, ledger-integrity duplicate/spot-check
  behavior, and retry handling.
- **Scenario tests** (the demo cases): clean submit, unmatched/low-confidence
  holds, ambiguous classification, a large multi-line invoice, failed Sheet
  append.

The decision policy and categorization heuristics carry the heaviest unit
coverage because they directly govern whether an item is safe to auto-file.

## 20. Suggested Source Layout

```
/backend
  /inbox          # MockInbox, DriveInbox, GmailInbox + InboxClient protocol
  /receipts       # receipt-detection filter
  /intake
  /parser
  /extraction
  /categorize     # income/expense + Schedule C category (matching-analog)
  /decision
  /ledger_integrity   # duplicate holds, spot-check sampling
  /exceptions
  /audit
  /corrections
  /orchestrator
  /api
  /domain         # shared types: Invoice, LineItem, CategorizationResult, Decision, ...
  /clients        # LLMClient, DriveClient, GmailClient, SheetsClient
/frontend         # reviewer hub (React/Vite)
/tests
  /unit
  /integration
  /scenarios
/samples          # provided sample invoices for the offline demo
docker-compose.yml
```

The layout makes the staged separation physical: each pipeline stage is its
own package, shared types live in `domain`, and all external I/O is isolated
in `clients`, so the pipeline logic stays pure and testable.
