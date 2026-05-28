# Architecture: InvoiceScreener

This document describes the technical architecture for the AI-first clinical trial
invoice intake, matching, and decisioning workflow. It is derived from
[`challenge.md`](./challenge.md) and [`PRD.md`](./PRD.md), and it assumes the
product principle established there: **the AI processes each invoice end to end and
decides submit-vs-hold before any human touches it; reviewers validate outcomes
after the fact.**

It is a design reference, not a status report. Where the PRD states *what* must be
true, this document states *how* the system is structured to make it true.

## 1. Guiding Architectural Principles

1. **Staged pipeline with hard boundaries.** Extraction, context resolution,
   catalog retrieval, matching, decisioning, and submission are independent stages.
   Each consumes a typed input and produces a typed, persisted output. No stage
   reaches into another's internals.
2. **AI-first, human-after.** The pipeline runs to a terminal decision (`submit` or
   `hold`) with no human gate. Human QC operates on the *result*, never as a
   precondition.
3. **Everything is explainable and persisted.** Every stage records its output,
   confidence, and rationale. The reviewer hub and the audit trail are reads over
   this persisted state — they never recompute.
4. **Fail isolated, fail visible.** One invoice failing a stage never blocks
   others. A failed stage records a typed error and either holds the invoice or
   marks a retryable failure; it never crashes the pipeline.
5. **Low confidence holds; it never silently submits.** Ambiguity and low
   confidence resolve toward `hold`, which is safe and visible, rather than toward
   a silent submission, which is not.

## 2. System Context

```
                ┌────────────────────────────────────────────┐
   Email /      │              InvoiceScreener                │
   Sample   ───▶│                                             │
   Invoices     │   Intake → Pipeline → Decision → Hub        │
                │                  │            │             │
                └──────────────────┼────────────┼─────────────┘
                                   │            │
                      MCP-wrapped  │            │  ClinRun backend
                      Reference API│            │  (or mock)
                      (sponsor/    ▼            ▼
                       study/site,    Submission of approved
                       catalog)        invoice payloads
```

External dependencies:

- **MCP-wrapped Reference API** — authoritative source for sponsor / study / site
  records and the sponsor+study-scoped billable catalog. Accessed through an MCP
  client. Treated as a remote dependency that can be slow, return ambiguous
  matches, or fail.
- **ClinRun backend (or mock)** — the destination for submitted invoices. The
  system targets a mock by default and a real backend if one is provided.
- **LLM provider** — used for extraction, entity-resolution assistance, line-item
  matching assistance, and decision rationale. Accessed behind a provider-agnostic
  interface.

## 3. Logical Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Reviewer Hub (SPA)                            │
│   Invoice list · Detail view · QC actions (correct / rerun / escalate)     │
└───────────────────────────────┬────────────────────────────────────────────┘
                                 │  REST/JSON
┌───────────────────────────────▼────────────────────────────────────────────┐
│                                 API Layer                                    │
│   /invoices  /invoices/:id  /process  /corrections  /rerun  /reviewed  ...   │
└───────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────────┐
│                          Workflow Orchestrator                               │
│   Drives stages, owns state transitions, writes audit events                 │
└──┬──────────┬───────────┬────────────┬────────────┬───────────┬─────────────┘
   │          │           │            │            │           │
┌──▼───┐  ┌───▼────┐  ┌───▼─────┐  ┌───▼─────┐  ┌───▼─────┐  ┌──▼────────┐
│Intake│  │ Doc    │  │  LLM    │  │ Context │  │ Catalog │  │ Line-Item │
│      │  │ Parser │  │Extractor│  │Resolver │  │ Client  │  │ Matcher   │
└──────┘  └────────┘  └─────────┘  └────┬────┘  └────┬────┘  └─────┬─────┘
                                        │            │             │
                                  ┌─────▼────────────▼─────┐  ┌────▼─────────┐
                                  │   MCP Reference Client  │  │  Decision    │
                                  └─────────────────────────┘  │  Engine      │
                                                                └────┬─────────┘
                                                                     │
                                                              ┌──────▼───────┐
                                                              │ Submission   │
                                                              │ Client       │
                                                              └──────────────┘

         ┌──────────────────────────────────────────────────────────┐
         │     Persistence (invoices, stage outputs, exceptions,      │
         │                  audit events, catalog cache)              │
         └──────────────────────────────────────────────────────────┘
```

## 4. Module Boundaries

The codebase enforces the PRD's required separation. Each module is independently
testable and depends only on shared domain types, not on sibling modules.

| Module | Responsibility | Key inputs | Key outputs |
| --- | --- | --- | --- |
| `intake` | Receive/simulate emails, register a workflow record, store source metadata | Email / sample file | `Invoice` record (status `received`) |
| `parser` | Extract raw text + structure from PDF/image/body; **(Phase 4)** rasterize pages to normalized images | Attachment, body | Parsed document text + sections; **page rasters + dims** |
| `ocr` *(Phase 4)* | Per-word boxes with normalized `[0,1]` coords + indices per page, behind an `OCRClient` (Tesseract default, offline stub) | Page raster | `WordBox[]` |
| `extraction` | LLM-driven structured extraction of metadata + line items; **(Phase 4)** vision extraction citing OCR word indices | Parsed text; **page image + `WordBox[]`** | `InvoiceMetadata`, `LineItem[]`, confidences; **`Citation[]`** |
| `context` | Resolve sponsor/study/site via MCP reference API | Extracted metadata, sender | `ResolvedContext` + candidates + warnings |
| `catalog` | Fetch sponsor+study-scoped catalog; cache it | Resolved sponsor/study | `CatalogItem[]` |
| `matching` | Match line items to catalog items | `LineItem[]`, `CatalogItem[]` | `MatchResult[]` |
| `decision` | Apply policy → `submit` or `hold` with rationale | All stage outputs + confidences | `Decision` + `risk_flags` |
| `submission` | Send approved payload to ClinRun (or mock) | `Decision=submit`, full invoice | submission result |
| `exceptions` | Create typed exception records on hold/failure | stage signals | `Exception[]` |
| `audit` | Append immutable audit events | every stage + human action | `AuditEvent[]` |
| `orchestrator` | Sequence stages, own state machine, isolate failures | invoice id | state transitions |
| `api` | HTTP surface for the hub | requests | JSON responses |
| `hub` (frontend) | Post-decision review + QC actions | API | rendered views, user actions |

**Dependency rule:** modules depend downward on shared domain types and on the
orchestrator's contract, never sideways. `matching` does not import `context`; it
receives `ResolvedContext` and `CatalogItem[]` as inputs. This keeps each stage
unit-testable in isolation and mockable for integration tests.

**Phase 4 — Visual Document Review** (`/docs/specs/visual-document-review.md`) adds
the `ocr` module and extends `parser` (rasterization) and `extraction` (vision)
without disturbing the dependency rule: OCR runs on the parser's page rasters,
vision extraction consumes the image + `WordBox[]`, and everything downstream of
extraction (`context` … `decision`, QC, rerun, metrics) is unchanged — `Citation[]`
is additive metadata on the extraction output.

## 5. Pipeline Data Flow

For a single invoice, the orchestrator drives this sequence. Each arrow persists
the stage output and appends an audit event before the next stage starts, so the
invoice is always inspectable at its latest completed stage.

```
received
  └▶ parse ─────────────▶ parsed text
       └▶ extract ───────▶ metadata + line items (+confidence)
            └▶ resolve ──▶ sponsor/study/site (+candidates, +warnings)
                 └▶ fetch catalog ─▶ scoped catalog items
                      └▶ match ────▶ per-line match results (+confidence)
                           └▶ decide ─▶ submit | hold (+rationale, +risk flags)
                                └▶ submit  → ClinRun (if submit)
                                   or
                                └▶ exception → hold record (if hold)
```

Each stage can short-circuit to a terminal state:

- A **hard failure** (e.g., extraction throws, catalog API down) → mark stage
  `failed`, create an exception, set the invoice to `held` or a retryable `failed`
  state. Downstream stages are skipped.
- A **low-confidence / ambiguous** result is *not* a failure — the stage succeeds,
  the pipeline continues, and the accumulated risk surfaces in the decision stage,
  which holds the invoice.

## 6. Workflow State Machine

The orchestrator owns invoice state. States and legal transitions:

```
received → parsed → extracted → context_resolved → catalog_matched
                                                        │
                                                        ├─▶ submitted        (terminal-success)
                                                        └─▶ held             (terminal-hold)

any stage ─▶ failed            (retryable; → rerun_requested)
held / failed / submitted ─▶ rerun_requested ─▶ (re-enter pipeline)
held / failed ─▶ escalated     (manual handling)
any state + human edit ─▶ corrected (then typically → rerun_requested)
```

Rules:

- Transitions are append-only audit events; the current state is the latest event.
- `failed` is distinguished from `held`: `failed` means a stage errored and is
  retryable; `held` means the AI made a deliberate decision not to submit.
- `rerun_requested` re-enters the pipeline using corrected data where present
  (see §11), reusing prior stage outputs only when their inputs are unchanged.

## 7. MCP Reference API Integration

Context resolution and catalog retrieval are the two stages that depend on the
MCP-wrapped reference API. They share a single **MCP Reference Client** that
encapsulates the MCP transport and exposes a narrow domain interface:

- `resolveSponsorStudySite(clues) → candidates[]` — given extracted metadata,
  vendor/site name, sponsor name, protocol number, and email sender, returns
  ranked candidate (sponsor, study, site) tuples with scores.
- `getCatalog(sponsorId, studyId) → catalogItems[]` — returns the scoped billable
  catalog.

Design choices:

- **Resolution is a ranking, not a lookup.** The client returns candidates with
  scores; the `context` module decides confidence and whether the top candidate is
  unambiguous. A close second candidate is a risk flag, not a silent pick.
- **The client is the only place that knows MCP exists.** Stages depend on the
  domain interface, so the reference source can be swapped for a fixture in tests
  without touching stage logic.
- **Catalog responses are cached** by `(sponsorId, studyId)` for the run, so large
  catalogs are fetched once and reused across line items and reviewer inspection.
- **Failures are typed.** Timeout, empty result, sponsor/study mismatch, and
  transport error are distinct, because they map to different decisions
  (retryable failure vs. hold-with-reason).

## 8. LLM Integration

LLM calls happen in three stages — extraction, matching assistance, and decision
rationale — all behind a single provider-agnostic `LLMClient` interface so the
provider (OpenAI or equivalent) is a configuration detail.

- **Structured output is mandatory.** Extraction and decision calls request JSON
  conforming to a schema and are validated (e.g., via a schema validator) before
  use. Invalid output is a retryable extraction/decision failure, never silently
  passed downstream.
- **Confidence is captured per field/item**, not just per call, so the reviewer
  hub can highlight exactly which extracted value or match is uncertain.
- **Determinism for tests.** Prompts and parsing are pure functions of their
  inputs; tests inject a recorded/stub `LLMClient` so pipeline logic is verified
  without live model calls.
- **Matching is hybrid, not pure LLM.** See §9 — the LLM assists semantic mapping,
  but deterministic amount/quantity checks gate the final match decision.
- **Vision extraction (Phase 4).** The `LLMClient` gains a vision path (or a
  `VisionLLMClient`) that receives the rendered page image **plus the OCR word
  list** (each word tagged with an integer index). For each field/line it returns a
  value, a `status` (`extracted`/`uncertain`/`unreadable`/`missing`), and a
  `citation` carrying **`word_indices`** — indices into the OCR word list, **never
  raw coordinates**. The server resolves those indices to a bounding box from real
  OCR geometry (§12), so the model cannot hallucinate a highlight; it can only
  point at words that exist. The offline default stays deterministic: a stub
  matches extracted values to OCR words to synthesize citations without a live
  vision model (testing strategy §18). See `/docs/specs/visual-document-review.md`.

## 9. Matching Strategy

Each extracted line item is matched against the scoped catalog using a layered
approach so results are both flexible and explainable:

1. **Normalization** — canonicalize descriptions, units, and terminology.
2. **Candidate generation** — semantic similarity + fuzzy match against catalog
   descriptions to produce ranked candidates.
3. **LLM adjudication** — for ambiguous candidates, the LLM selects the best
   catalog mapping and produces a rationale.
4. **Deterministic verification** — amount and quantity are compared against the
   chosen catalog item with explicit tolerances; mismatches downgrade confidence
   and raise flags regardless of semantic certainty.

Each `MatchResult` records the chosen catalog item, confidence, rationale, amount
and quantity comparison, alternate candidates, and whether the item requires
exception review. An item with no acceptable candidate is an **unmatched material
line item** — a high-severity signal for the decision stage.

## 10. Decision Engine

The decision engine consumes every prior stage output and applies an explicit,
testable policy to produce a structured `Decision` (`submit` | `hold`,
confidence, rationale, `risk_flags[]`, `required_human_actions[]`).

Policy is data-driven so it is auditable and unit-testable in isolation:

| Condition | Decision |
| --- | --- |
| High-confidence context + all material items matched, amounts consistent | Submit |
| Missing sponsor/study/site, or multiple plausible candidates | Hold |
| Unmatched material line item | Hold |
| Invoice total conflicts with line-item totals | Hold |
| Catalog unavailable | Hold (retryable) |
| Extracted metadata contradicts reference data | Hold |
| Low extraction/match confidence | Hold |
| Any high-severity risk flag present | Hold |
| Backend submission fails | Retryable failure |

The engine never requires human approval to reach a decision. Severity levels
(low/medium/high) map to whether a risk flag is informational, visibility-only, or
blocking — high-severity always forces a hold. This stage directly produces the
two strategy guardrail metrics: **false-submit rate** (a wrong `submit`) and
**hold precision** (a `hold` that didn't actually need a human).

## 11. Human QC & Correction Loop

QC is strictly post-decision. The hub exposes actions that all produce audit events
attributed to a human actor, keeping AI decisions and human edits distinguishable:

- **Mark reviewed** — acknowledges an outcome without changing it.
- **Correct metadata / correct line match** — writes a correction record that
  overlays the AI's extracted value or chosen match.
- **Rerun** — re-enters the pipeline. Corrected fields are treated as fixed inputs;
  stages whose inputs are unchanged may reuse prior outputs, stages downstream of a
  correction recompute.
- **Escalate** — routes the invoice to manual exception handling.
- **Override decision** (if implemented) — explicit human submit/hold over the AI
  decision, recorded as a human action.

Corrections never mutate the original AI output in place; they are overlays, so the
audit trail always shows what the AI produced versus what a human changed.

## 12. Persistence Model

A relational store (PostgreSQL) holds the workflow state. Core tables map to the
PRD data model: `invoices`, `resolved_context`, `line_items`, `match_results`,
`exceptions`, `audit_events`, plus a `catalog_cache` keyed by sponsor+study.

Design notes:

- **Stage outputs are persisted, not just final state**, so the hub renders any
  invoice at its latest completed stage and reruns can diff against prior outputs.
- **`audit_events` is append-only** and is the source of truth for current state
  and for the AI-vs-human distinction.
- **`exceptions` carry type + severity + message**, so the hub can filter by
  exception kind and the decision engine can reason over them.
- **Citations (Phase 4).** Each extracted field/line item may carry a `Citation`
  `{page_number, target_id, quote, bbox, status}`, where `bbox` is a
  `BoundingBox {x, y, width, height}` in normalized `[0,1]` page space (resolved
  server-side from the union of cited OCR `WordBox` geometry). `target_id` keys the
  citation to its extracted thing (`metadata.<field>` or `line_item.<id>.<attr>`),
  reusing the per-field signal shape from the extraction output. Provisional
  persistence (OD-9): surface citations + page dims in the detail payload alongside
  the existing extraction signals; promote to a `citations` table only if querying
  demands it. See `/docs/specs/visual-document-review.md`.

## 13. API Surface

The API layer is a thin translation of orchestrator/state into JSON. Endpoints
(from the PRD) and their roles:

| Endpoint | Method | Role |
| --- | --- | --- |
| `/api/invoices/process` | POST | Start processing for a sample invoice |
| `/api/invoices` | GET | List with status, decision, confidence, exception summary |
| `/api/invoices/:id` | GET | Full detail: source, extraction, context, matches, decision, exceptions, audit |
| `/api/invoices/:id/corrections/metadata` | POST | Reviewer corrects extracted metadata |
| `/api/invoices/:id/corrections/line-item` | POST | Reviewer corrects a catalog match |
| `/api/invoices/:id/rerun` | POST | Re-run with original or corrected data |
| `/api/invoices/:id/reviewed` | POST | Mark reviewed post-decision |
| `/api/invoices/:id/escalate` | POST | Escalate for manual handling |
| `/api/invoices/:id/pages` | GET | *(Phase 4)* Page count + raster dimensions |
| `/api/invoices/:id/pages/:n/image` | GET | *(Phase 4)* Rendered page raster (PNG/JPEG) for the overlay |

The hub reads list/detail and triggers QC actions; it does no business logic of its
own — all decisioning lives server-side and is merely surfaced.

## 14. Reviewer Hub (Frontend)

A single-page app (React/Vite) with two primary surfaces:

- **List view** — invoice number, site/vendor, sponsor, study, total, status,
  decision, confidence, exception count, last-updated; filterable by submitted /
  held / failed / needs-review / low-confidence / mismatched-metadata /
  unmatched-line-items.
- **Detail view** — sectioned per the PRD: source, extracted metadata (with
  confidence + editable correction), context resolution (with candidates +
  mismatch warnings), line-item matching (raw vs normalized, match + rationale +
  flags), decision (submit/hold + confidence + rationale + risk flags + submission
  status), and QC actions.

The hub's only job is post-decision visibility and QC — it embodies the
"human-after" principle in UI form.

## 15. Failure Handling & Observability

- **Isolation:** the orchestrator processes invoices independently; an exception in
  one never aborts the batch.
- **Typed errors:** each stage failure records stage name, message, and whether it
  is retryable vs terminal.
- **Retry/recovery:** retryable failures (catalog/API/submission) are re-runnable
  from the failed stage without re-doing successful upstream stages.
- **Low-confidence path:** ambiguity routes to `hold`, never silent submission.
- **Traceability:** the audit event stream per invoice makes the full path through
  the pipeline reconstructable for debugging and demo clarity.

## 16. Technology Stack

Local-first, per the PRD. Recommended concrete stack (any modern equivalent is
acceptable):

| Concern | Choice |
| --- | --- |
| Backend | Python / FastAPI (or TypeScript / Node) |
| Frontend | React / Vite |
| Database | PostgreSQL |
| Background work | Async worker / job queue (Celery, BullMQ, or local async) |
| LLM | OpenAI or equivalent, behind `LLMClient` |
| Reference data | MCP-wrapped reference API, behind `MCP Reference Client` |
| Orchestration | Docker Compose |
| Tests | pytest / vitest / jest |

## 17. Deployment Topology

`docker-compose` brings up the full local environment:

```
services:
  api        # FastAPI/Node backend + orchestrator
  worker     # pipeline execution (if async)
  hub        # React/Vite frontend
  db         # PostgreSQL
  mock-clinrun   # stand-in submission backend
  mcp-reference  # MCP-wrapped reference API (or stub)
```

Sample invoices are seeded into `intake` on startup so the pipeline can be
demonstrated end to end without configuring real email.

## 18. Testing Strategy

Aligned with the PRD's testing requirements and the staged boundaries:

- **Unit tests** per module: extraction schema validation, context resolution
  ranking, matching layers, decision policy table, exception creation, state
  transitions.
- **Integration tests**: end-to-end processing of sample invoices against a stubbed
  MCP reference client and mock ClinRun, plus retry handling.
- **Scenario tests** (the demo cases): simple invoice, medium multi-line invoice,
  large invoice against a large catalog, ambiguous sponsor/study/site, mismatched
  metadata, unmatched line item, failed catalog fetch, failed submission.

The decision policy and matching layers carry the heaviest unit coverage because
they directly govern the strategy guardrails (false-submit rate, hold precision).

## 19. Suggested Source Layout

```
/backend
  /intake
  /parser
  /extraction
  /context        # + mcp reference client
  /catalog
  /matching
  /decision
  /submission
  /exceptions
  /audit
  /orchestrator
  /api
  /domain         # shared types: Invoice, LineItem, MatchResult, Decision, ...
  /clients        # LLMClient, MCPReferenceClient, ClinRunClient
/frontend         # reviewer hub (React/Vite)
/tests
  /unit
  /integration
  /scenarios
/samples          # provided sample invoices for seeding
docker-compose.yml
```

The layout makes the staged separation physical: each pipeline stage is its own
package, shared types live in `domain`, and all external I/O is isolated in
`clients`, so the pipeline logic stays pure and testable.
