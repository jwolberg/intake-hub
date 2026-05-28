# Build Plan

## Project
- Name: InvoiceScreener — AI-First Clinical Trial Invoice Intake, Matching & Decisioning
- Summary: Clinical trial site invoices arrive in inconsistent formats and require per-invoice judgment (sponsor/study/site + catalog matching) that doesn't reduce to fixed rules. InvoiceScreener processes each invoice end to end and decides submit-to-ClinRun vs. hold-for-exception *before* any human touches it, then exposes explainable outcomes for post-decision QC.

## Source of Truth
- What must ship / requirements: /docs/PRD.md
- Approach / scope / metrics / tracks: /docs/STRATEGY.md
- Technical design / open decisions: /docs/ARCHITECTURE.md
- Personas / interfaces: /docs/USERS.md
- Originating brief: /docs/challenge.md
- UX clarifications (if used): /docs/ux.md — NOT PRESENT

## Planning Assumptions
- **Skill template vs. project domain.** The `plan` skill's body carries EntityIQ-domain examples (company registration, network-metadata capture, HQ map, IP intel, "<2h per company"). The skill's own first rule is "Plan ONLY from the REQUIRED input documents." Per that rule and the conflict-resolution order, this plan uses ledgerrun's documents as source of truth and applies only the skill's generic intent: deliver the narrowest end-to-end slice first, then deepen by STRATEGY Track. The template's hardcoded project name ("EntityIQ …") was replaced with the actual product name from STRATEGY/PRD.
- **No explicit Open Decisions table.** ARCHITECTURE.md does not contain an "Open Decisions" table; its §16 Technology Stack is stated as a *recommendation* and PRD §Technology says "any modern language/framework is acceptable." Per § Open decisions handling, backend-stack selection is treated as the blocking open decision (analogous to "Open Decision #1") and resolved in Phase 0. The following are planned **provisionally** against the ARCHITECTURE recommendation and listed as open:
  - **OD-1 Backend stack** — provisional: Python/FastAPI (ARCHITECTURE §16, listed first). Blocks all implementation tickets; resolved in P0-T1.
  - **OD-2 LLM provider** — provisional: OpenAI-or-equivalent behind `LLMClient` (ARCHITECTURE §8). Not blocking; interface isolates it.
  - **OD-3 Job execution model** — provisional: async worker/queue vs. synchronous (ARCHITECTURE §16/§17). MVP may run synchronously; revisited in Phase 3.
  - **OD-4 Reference API availability** — provisional: MCP reference API is stubbed for tests/dev; real MCP wired if provided (ARCHITECTURE §7).
  - **OD-5 ClinRun backend** — provisional: mock ClinRun by default; real backend if provided (ARCHITECTURE §2; PRD §Non-Goals excludes full prod integration unless a mock/backend is available).
- **Monorepo layout.** The skill mentions `frontend/ backend/ shared/ docs/ tests/`. ARCHITECTURE §19 specifies a more detailed layout (`/backend` with one package per stage, `/frontend`, `/tests/{unit,integration,scenarios}`, `/samples`). Per the skill rule "use ARCHITECTURE file paths verbatim," this plan uses ARCHITECTURE §19; `shared` maps to `/backend/domain` and `/backend/clients`.
- **No material document conflicts.** PRD and ARCHITECTURE align (ARCHITECTURE was derived from PRD). Where PRD §13 lists OpenAI specifically, it is treated as one provider option behind the `LLMClient` interface (ARCHITECTURE §8), not a hard requirement.

## Architecture Notes
- **Component layout** (ARCHITECTURE §3–§4, §19): staged pipeline, one package per stage under `/backend`: `intake`, `parser`, `extraction`, `context` (+ MCP reference client), `catalog`, `matching`, `decision`, `submission`, `exceptions`, `audit`, `orchestrator`, `api`; shared `domain` types and external `clients`; `/frontend` reviewer hub. Dependency rule: stages depend downward on shared types + orchestrator contract, never sideways.
- **Pipeline model** (ARCHITECTURE §5–§6): `received → parsed → extracted → context_resolved → catalog_matched → submitted | held`, with `failed` (retryable) distinguished from `held` (deliberate AI decision), plus `rerun_requested`, `corrected`, `escalated`. Each stage persists a typed output + appends an audit event before the next stage runs.
- **Data model** (ARCHITECTURE §12; PRD §11): `invoices`, `resolved_context`, `line_items`, `match_results`, `exceptions`, `audit_events` (append-only, source of truth for state + AI-vs-human), `catalog_cache` keyed by sponsor+study.
- **Integrations** (ARCHITECTURE §7–§8): MCP Reference Client (`resolveSponsorStudySite`, `getCatalog`) is the only module aware of MCP; `LLMClient` provider-agnostic with mandatory schema-validated structured output; `ClinRunClient` for submission (mock by default).
- **Auth model**: not specified in PRD/ARCHITECTURE for the hackathon scope; treated as out of scope beyond basic operator access (PRD does not list auth as a functional requirement). Recorded as a non-goal-adjacent gap, not planned.
- **Open Decisions handling**: OD-1 resolved in Phase 0 (blocks implementation); OD-2/OD-4/OD-5 isolated behind client interfaces and stubbed/mocked; OD-3 deferred to Phase 3.
- **Explicit non-goals affecting implementation** (PRD §4): no perfect OCR/document intelligence; not every invoice format; not a production email-ingestion system; no full ClinRun production integration unless a mock/backend is provided; no 100% match guarantee in ambiguous cases. These cap Phase 2 ambition (handle the provided sample set robustly, not the open world).

## Current Status
- Overall status: In Progress
- Current phase: **Phase 2 — Deepen the Tracks: COMPLETE** (Track A P2-A1..A4; Track B P2-B1..B4; Track C P2-C1..C4). Phase 3 (Hardening & Polish) next.
- Current ticket: **P3-T1** (failure isolation, retry & recovery). The Phase 1 live-stack exit gate is **CLEARED** (2026-05-27): Postgres round-trip test passes against the live DB, `docker compose up` brings up the full stack (`/health` → `db: up`), and the hub serves at `http://127.0.0.1:5173` with working CORS. Remaining: a human visual click-through of the hub UI. Commands: /docs/RUNBOOK.md.
- Note: All of Phase 2 (P2-A1..A4 + P2-B1..B4 + P2-C1..C4) validated in-process (106 passed, 1 skipped) **and** exercised end-to-end against the live Postgres stack (process → filters → detail → QC corrections → rerun → metrics). The skipped test is the Postgres round-trip, which passes when run with a live `DATABASE_URL`. Dev test also fixed a metric-bucketing bug (rates now key off the AI's first/autonomous decision, robust to rerun). Also: out-of-plan real-PDF demo path (RUNBOOK Path D).
- Blockers: None for in-process work (OD-1 resolved; OD-2..OD-5 provisional behind interfaces). Live-stack exit gate blocked on Docker availability only.
- Implementation log: /docs/implementation.md, /docs/implementation-notes.md
- Dev setup/run: /docs/RUNBOOK.md

---

## Phase Breakdown

### Phase 0 — Decisions & Scaffolding  ✅ Complete (2026-05-26)
**Goal**
- Resolve the blocking open decision (backend stack) and stand up the monorepo, data model, client interfaces, and lint/test/compose harness.

**Exit Criteria** — met
- Backend stack chosen (Python/FastAPI); ARCHITECTURE §19 layout in place; shared domain types + Postgres schema defined; clients (LLM, MCP, ClinRun) implemented with stubs + HTTP impls; `docker-compose` brings up db/api/hub/mcp-reference/mock-clinrun (worker deferred per OD-3); lint clean + 13 tests green.

**Tickets**
- **P0-T1 — Resolve backend stack & confirm provisional decisions**
  - Objective: Settle OD-1 (Python/FastAPI provisional) and confirm OD-2..OD-5 defaults; record outcomes in this plan + `docs/implementation-notes.md`.
  - Files likely involved: /docs/BUILD_PLAN.md, /docs/implementation-notes.md
  - Depends on: —
  - Acceptance criteria covered: ARCHITECTURE §16 (stack), § Open decisions handling (OD-1 prerequisite for implementation tickets); PRD §Technology.
  - Status: Complete — Python/FastAPI (see /docs/implementation-notes.md)
- **P0-T2 — Scaffold monorepo + tooling**
  - Objective: Create ARCHITECTURE §19 layout (`/backend/{intake,parser,extraction,context,catalog,matching,decision,submission,exceptions,audit,orchestrator,api,domain,clients}`, `/frontend`, `/tests/{unit,integration,scenarios}`, `/samples`), lint + test harness, `docker-compose.yml`.
  - Files likely involved: repo root, /backend/**, /frontend/**, /tests/**, docker-compose.yml, requirements/package manifests
  - Depends on: P0-T1
  - Acceptance criteria covered: ARCHITECTURE §17 (deployment topology), §19 (source layout); PRD §Dev Tools (Docker Compose, automated tests).
  - Status: Complete — layout + FastAPI skeleton + frontend shell + compose (db/api/hub) + ruff/pytest. Worker & mock-clinrun/mcp-reference services deferred (OD-3 / P0-T4).
- **P0-T3 — Shared domain types + persistence schema**
  - Objective: Define `Invoice`, `LineItem`, `ResolvedContext`, `CatalogItem`, `MatchResult`, `Exception`, `AuditEvent`, `Decision` and create Postgres tables/migrations incl. append-only `audit_events` and `catalog_cache`.
  - Files likely involved: /backend/domain/**, migrations
  - Depends on: P0-T2
  - Acceptance criteria covered: PRD §11 (Data Model); ARCHITECTURE §12 (Persistence Model).
  - Status: Complete — Pydantic domain models + Postgres schema + engine/bootstrap + unit tests (4 passing).
- **P0-T4 — External client interfaces + stubs**
  - Objective: Define `LLMClient`, `MCPReferenceClient` (`resolveSponsorStudySite`, `getCatalog`), `ClinRunClient`; ship stub/mock implementations + the `mock-clinrun` and `mcp-reference` compose services.
  - Files likely involved: /backend/clients/**, /backend/context (mcp client), docker-compose.yml
  - Depends on: P0-T2
  - Acceptance criteria covered: ARCHITECTURE §7 (MCP integration), §8 (LLM integration); PRD FR3, FR4, FR7 (dependencies isolated behind clients).
  - Status: Complete — LLM/MCP/ClinRun interfaces + in-process stubs + HTTP clients + mcp-reference/mock-clinrun compose services; 13 tests passing.

### Phase 1 — MVP Vertical Slice (Walking Skeleton)  ✅ Complete (2026-05-26)
**Goal**
- Thinnest end-to-end happy path: a seeded sample invoice flows through every stage to a stored, explainable submit/hold decision that an operator can view in the hub.

**Exit Criteria**
- Running `/process` on a provided sample invoice produces persisted extraction → resolved context → catalog match → decision, then either submits to mock ClinRun or creates a hold exception; the reviewer hub lists the invoice and shows what was extracted, matched, the decision, and exceptions (PRD FR6 §6 bullets).

**Tickets**
- **P1-T1 — Intake of sample invoices**
  - Objective: Process provided sample invoices; create a unique `Invoice` workflow record (status `received`); store source metadata + reference.
  - Files likely involved: /backend/intake/**, /samples/**, /backend/api (process route stub)
  - Depends on: P0-T3, P0-T4
  - Acceptance criteria covered: PRD FR1 (all acceptance bullets); ARCHITECTURE §4 (`intake`); PRD §7 Step 1.
  - Status: Complete — `intake.ingest` creates the Invoice record (status received); persistence wired in P1-T9.
- **P1-T2 — Document parsing**
  - Objective: Extract raw text + available structure from a sample invoice; persist parsed text + attachment metadata.
  - Files likely involved: /backend/parser/**
  - Depends on: P1-T1
  - Acceptance criteria covered: PRD §7 Step 2; ARCHITECTURE §4 (`parser`).
  - Status: Complete — `parser.parse` → `ParsedDocument` (MVP renders the sample document block; real parser swaps in later).
- **P1-T3 — LLM extraction (happy path)**
  - Objective: Extract metadata + line items via `LLMClient`, schema-validated, with per-field/item confidence; mark missing fields explicitly.
  - Files likely involved: /backend/extraction/**, /backend/clients (LLMClient)
  - Depends on: P1-T2
  - Acceptance criteria covered: PRD FR2 (all acceptance bullets); ARCHITECTURE §8.
  - Status: Complete — `extraction.extract` validates LLM JSON into `ExtractionResult`; offline `PassthroughLLMClient` default (real provider = OD-2 swap).
- **P1-T4 — Context resolution via MCP (single best candidate)**
  - Objective: Resolve sponsor/study/site using the stubbed `MCPReferenceClient`; persist resolved IDs + confidence + candidates.
  - Files likely involved: /backend/context/**
  - Depends on: P1-T3, P0-T4
  - Acceptance criteria covered: PRD FR3; ARCHITECTURE §7; PRD §7 Step 4.
  - Status: Complete — `context.resolve` picks top candidate, clamps confidence, flags near-tie ambiguity.
- **P1-T5 — Catalog fetch (scoped)**
  - Objective: Fetch sponsor+study-scoped catalog via client; cache by `(sponsorId, studyId)`.
  - Files likely involved: /backend/catalog/**
  - Depends on: P1-T4
  - Acceptance criteria covered: PRD FR4; ARCHITECTURE §7 (catalog caching), §12 (`catalog_cache`).
  - Status: Complete — `catalog.fetch` scopes by resolved sponsor/study; raises `CatalogNotFound` (caching in P2-A3).
- **P1-T6 — Line-item matching (baseline)**
  - Objective: Produce a `MatchResult` per line item (best catalog item, confidence, rationale, amount/qty comparison, unmatched flag).
  - Files likely involved: /backend/matching/**
  - Depends on: P1-T5
  - Acceptance criteria covered: PRD FR5; ARCHITECTURE §9.
  - Status: Complete — `matching.match` (normalize + exact/containment + price check); unmatched flagged (layered matcher in P2-A4).
- **P1-T7 — Decision engine (policy table)**
  - Objective: Apply the data-driven policy → `submit`|`hold` with confidence, rationale, risk_flags; no human gate.
  - Files likely involved: /backend/decision/**
  - Depends on: P1-T6
  - Acceptance criteria covered: PRD FR6 (decision before QC); ARCHITECTURE §10 (policy table); STRATEGY § Our approach.
  - Status: Complete — `decision.decide` applies the baseline policy; any HIGH flag → hold, else submit (full model in P2-B1).
- **P1-T8 — Submission to mock ClinRun + hold exception**
  - Objective: On `submit`, send structured payload to `mock-clinrun` and store result; on `hold`, create an `Exception` with a specific reason.
  - Files likely involved: /backend/submission/**, /backend/exceptions/**, /backend/clients (ClinRunClient)
  - Depends on: P1-T7
  - Acceptance criteria covered: PRD FR7, FR8 (baseline); ARCHITECTURE §2, §4.
  - Status: Complete — `submission.submit_invoice` (payload + ClinRun) and `exceptions.from_decision` (hold reasons → typed exceptions).
- **P1-T9 — Orchestrator + state machine + audit (baseline)**
  - Objective: Sequence stages, own state transitions, append audit events, isolate per-invoice failures so one failure doesn't stop others.
  - Files likely involved: /backend/orchestrator/**, /backend/audit/**
  - Depends on: P1-T1..P1-T8
  - Acceptance criteria covered: PRD FR11 (baseline), §14 (one failed invoice doesn't stop others); ARCHITECTURE §5–§6.
  - Status: Complete — `orchestrator.process`/`process_all` chain the stages, own state transitions, persist each output via a `Repository`, append audit events, and isolate per-invoice failures. `Repository` protocol + `InMemoryRepository` + `audit.record`. (PostgresRepository lands in P1-T10.)
- **P1-T10 — API: process / list / detail**
  - Objective: Implement `POST /api/invoices/process`, `GET /api/invoices`, `GET /api/invoices/:id` returning source/extraction/context/matches/decision/exceptions/audit.
  - Files likely involved: /backend/api/**
  - Depends on: P1-T9
  - Acceptance criteria covered: PRD §13 (API), FR9 (backend read surface).
  - Status: Complete — `POST /api/invoices/process`, `GET /api/invoices`, `GET /api/invoices/:id`; `PostgresRepository` + `get_repository`; repo/clients injected via FastAPI deps. API verified with InMemory + stubs; Postgres round-trip test skip-guarded (Docker unavailable in dev session).
- **P1-T11 — Reviewer hub (minimal list + detail)**
  - Objective: SPA list (status, decision, confidence, exception count) + detail (extracted, matched, decision + rationale, exceptions). Read-only.
  - Files likely involved: /frontend/**
  - Depends on: P1-T10
  - Acceptance criteria covered: PRD FR9 §6 bullets (what was extracted/matched, confidence/exceptions, submitted/withheld); USERS § Invoice Operations Reviewer.
  - Status: Complete — React/Vite hub: list (status/decision/confidence/exception count) + detail (metadata, context, line items↔matches, decision + rationale, exceptions, audit timeline). Builds clean; live browser check pending (Docker unavailable in dev session).

### Phase 2 — Deepen the Tracks
Tickets are grouped by STRATEGY § Tracks. Each traces to a PRD requirement.

#### Track A — Interpretation engine (STRATEGY Track 1)
- **P2-A1 — Robust multi-format extraction + evidence**
  - Objective: Handle PDF/image/body variants from the sample set; per-field confidence + source evidence; explicit uncertainty marking.
  - Files: /backend/extraction/**, /backend/parser/**
  - Depends on: P1-T3
  - Acceptance criteria covered: PRD FR2, §15 (easy/medium/large scenarios); STRATEGY Track "Interpretation engine".
  - Status: Complete — parser detects format (PDF/image/email-body) + locates payload; extraction captures per-field confidence + source evidence + explicit missing-field marking, preserves line-item source text, and accepts annotated `{value,confidence,evidence}` fields. Samples added for body + image variants. 31 passed, 1 skipped. (Persisting/surfacing per-field signals = P2-C2; feeding low-confidence→hold = P2-B1.)
- **P2-A2 — Context ranking, candidates & mismatch detection**
  - Objective: Return ranked candidates; flag close-second as ambiguity; detect invoice-vs-reference contradictions (e.g., sponsor A but protocol maps to sponsor B).
  - Files: /backend/context/**
  - Depends on: P1-T4
  - Acceptance criteria covered: PRD FR3, FR8 (ambiguous/mismatched metadata); ARCHITECTURE §7; PRD §15.
  - Status: Complete — candidates carry canonical names; context emits `ambiguous_context`/`multiple_site_candidates` (near-tie) and `sponsor_/study_/protocol_/site_mismatch` (invoice-vs-reference contradiction). Decision holds on ambiguity/mismatch (`context_ambiguity`/`context_mismatch` flags — minimal extension; full policy = P2-B1). Sample `inv_hold_mismatch_005.json` added. 37 passed, 1 skipped.
- **P2-A3 — Catalog robustness (large/missing/empty/error) + cache**
  - Objective: Graceful handling of large catalogs, missing/empty results, and API errors as typed failures.
  - Files: /backend/catalog/**
  - Depends on: P1-T5
  - Acceptance criteria covered: PRD FR4, §14 (reliable large-catalog matching); ARCHITECTURE §7.
  - Status: Complete — `CatalogCache` protocol + `InMemoryCatalogCache` keyed by (sponsor,study), shared per `process_all` batch (one fetch per scope); missing→hold, transport error→typed `catalog_fetch_failed` retryable failure, empty→[], large passes through. In-memory cache (Postgres `catalog_cache` table impl deferred behind the protocol pending Docker). 46 passed, 1 skipped.
- **P2-A4 — Hybrid matching (normalize → semantic → LLM → verify)**
  - Objective: Layered matcher with normalization, candidate generation, LLM adjudication, and deterministic amount/qty verification; alternates + unmatched-material detection.
  - Files: /backend/matching/**, /backend/clients (LLMClient)
  - Depends on: P1-T6, P2-A3
  - Acceptance criteria covered: PRD FR5 (simple→large→ambiguous); ARCHITECTURE §9.
  - Status: Complete — layered matcher: normalize (filler-word stripping) → token-set candidate ranking (exact/containment boost, floor) → LLM adjudication of ambiguous ties (advisory, deterministic fallback) → deterministic amount + quantity·price verification (downgrades confidence, raises flags). `MatchResult.quantity_match` + ranked `alternates`. Decision holds on `quantity_mismatch` (minimal extension; full policy P2-B1). 53 passed, 1 skipped.

#### Track B — Decisioning & explainability (STRATEGY Track 2)
- **P2-B1 — Full decision policy + structured output**
  - Objective: Complete §10 policy with severity levels, `risk_flags`, `required_human_actions`, decision confidence; high-severity always holds; low confidence → hold (never silent submit).
  - Files: /backend/decision/**
  - Depends on: P1-T7
  - Acceptance criteria covered: PRD FR6, §9 (AI decisioning), §16 (confidence/risk model); ARCHITECTURE §10.
  - Status: Complete — graded severity model (low/medium/high); any HIGH → hold, MEDIUM = visibility, LOW = informational. Consumes per-field extraction confidence (P2-A1) + context + match confidence; missing-critical-field holds; computed `decision_confidence` (submit = weakest-link, hold = 0.9); low-confidence floor → hold (never silent submit). `decide(extraction, ctx, matches, catalog_available)`. 75 passed, 1 skipped; new `tests/unit/test_decision.py` (10).
- **P2-B2 — Exception taxonomy**
  - Objective: Typed exceptions for all PRD FR8 hold reasons with severity + specific message, surfaced to the hub.
  - Files: /backend/exceptions/**
  - Depends on: P1-T8
  - Acceptance criteria covered: PRD FR8 (all hold reasons); ARCHITECTURE §12.
  - Status: Complete — canonical taxonomy in `backend/domain/taxonomy.py` (code → severity + title, FR8-flagged); decision severities sourced from it; FR8 unresolved sponsor/study/site split out; shared `exceptions.build` factory used by `from_decision` + orchestrator failures (also removed a duplicate `submission_failed`); hub humanises type codes. 82 passed, 1 skipped; new `tests/unit/test_exceptions.py` (7) guarantees FR8 coverage + code/severity consistency.
- **P2-B3 — Audit trail completeness (AI vs human)**
  - Objective: Append-only audit of every stage + human action with actor, action, before/after, reason; AI and human edits distinguishable.
  - Files: /backend/audit/**
  - Depends on: P1-T9
  - Acceptance criteria covered: PRD §17 (auditability), FR10/FR11; ARCHITECTURE §12.
  - Status: Complete — `record()` supports before/after/reason (in JSONB details, no schema change); every stage event enriched to §17 (intake/parse/extracted-values/reference-lookup/catalog-retrieval/match-results/decision); actor attribution system/ai/human; terminal events carry reason. Human-correction audit shape provided + tested (routes land P2-C3). 86 passed, 1 skipped; new `tests/unit/test_audit.py` (4) + orchestrator actor/reason assertions.
- **P2-B4 — Strategy metric instrumentation**
  - Objective: Compute/expose auto-submit rate, false-submit rate, hold precision for the Operations Lead.
  - Files: /backend/api/**, /backend/audit/**, /frontend/**
  - Depends on: P2-B1, P2-B3
  - Acceptance criteria covered: STRATEGY § Key metrics; USERS § Operations Lead.
  - Status: Complete — `backend/audit/metrics.py` computes auto-submit rate, false-submit rate (human-corrected/escalated submits), hold precision (human-confirmed holds) from invoices + audit; rates are `None` when no data. Surfaced via `GET /api/metrics` + a hub `MetricsBar`. **Refined in P2-C3/C4 + dev test:** rate buckets key off the AI's *first (autonomous) decision* in the audit trail (not current status/decision), so human QC + rerun (which flip an invoice hold→submit) don't inflate auto-submit rate or fabricate false submits. `tests/unit/test_metrics.py` (4) + API tests.

#### Track C — Reviewer hub (STRATEGY Track 3)
- **P2-C1 — List view filters**
  - Objective: Filters for submitted/held/failed/needs-review/low-confidence/mismatched-metadata/unmatched-line-items.
  - Files: /frontend/**, /backend/api (query params)
  - Depends on: P1-T11
  - Acceptance criteria covered: PRD §10 (Invoice List View), FR9; USERS § Reviewer (triage attention).
  - Status: Complete — `_filter_tags` derives PRD §10 triage tags (submitted/held/failed/needs_review/low_confidence/mismatched_metadata/unmatched_line_items) per invoice; surfaced in each list row + `GET /api/invoices?filter=` (unknown → 422). Hub renders filter chips (live counts) filtering client-side over the same tags. 93 passed, 1 skipped (+2 API tests).
- **P2-C2 — Detail view full sections**
  - Objective: Source, extracted metadata (value/confidence/evidence), context (resolved + candidates + mismatch warnings), line-item matching (raw vs normalized + rationale + flags), decision (submit/hold + confidence + rationale + risk flags + submission status).
  - Files: /frontend/**
  - Depends on: P1-T11
  - Acceptance criteria covered: PRD §10 (Invoice Detail View), FR9; USERS § Reviewer (understand a decision fast).
  - Status: Complete — per-field extraction confidence/evidence + missing fields persisted on the `extracted` audit event (no schema change); submit risk_flags recorded on `submitted` (symmetric with held); received event carries subject/sender; `GET /api/invoices/:id` projects `source` + `extraction` blocks via `audit.latest_details`. Hub detail rebuilt into all six PRD §10 sections (Source, Extracted Metadata value/confidence/evidence, Context + candidates + mismatch warnings, Line Items raw→normalized + rationale + flags, Decision + risk flags + submission status). 94 passed, 1 skipped (+1 API test).
- **P2-C3 — QC actions**
  - Objective: Mark reviewed, correct metadata, correct line match, rerun, escalate, add note; recorded as human audit events; corrections as overlays (not in-place mutation).
  - Files: /frontend/**, /backend/api (corrections/rerun/reviewed/escalate routes)
  - Depends on: P2-C2, P2-B3
  - Acceptance criteria covered: PRD FR10, §13 (correction/rerun/reviewed/escalate endpoints); ARCHITECTURE §11; USERS § Reviewer + § Exception Handler.
  - Status: Complete — routes for corrections/metadata, corrections/line-item, reviewed, escalate, note (rerun = P2-C4); each records a human-attributed audit event. New `backend/corrections` derives overlays from the append-only trail (latest-wins, type-coerced) so corrections are overlays, never in-place mutation (ARCHITECTURE §11); detail exposes AI value + overlay; status flips to corrected/escalated (FR10 reflected-in-state). Fixed `metrics.py` to bucket rates by immutable decision (status now mutated by QC). Hub: QC action panel + inline metadata/line-match correction inputs. 103 passed, 1 skipped (+5 API, +4 unit).
- **P2-C4 — Rerun semantics with corrected data**
  - Objective: `rerun` re-enters pipeline using corrected fields as fixed inputs; downstream stages recompute, unchanged-input stages may reuse outputs.
  - Files: /backend/orchestrator/**, /backend/api/**
  - Depends on: P2-C3
  - Acceptance criteria covered: PRD FR10 (rerun uses corrected data); ARCHITECTURE §6, §11.
  - Status: Complete — `orchestrator.rerun` reuses parse/extract outputs and re-enters from context: `_reconstruct_extraction` rebuilds the extraction from persisted line items + the per-field signals on the `extracted` event, applies the metadata overlay, and marks corrected fields reviewer-verified (confidence 1.0, no longer missing); corrected line matches pinned via `apply_match_overlay`; context→catalog→match→decision recompute. Shared `_resolve_match_decide` tail with `process`. `POST /api/invoices/:id/rerun` + hub "Rerun with corrections" button. A correction that resolves the hold reason now submits on rerun. 106 passed, 1 skipped (+3 rerun tests).

### Phase 3 — Hardening & Polish
**Goal**
- Reliability, test coverage, observability, performance, and demo readiness per PRD §14/§18/§19.

**Exit Criteria**
- Scenario suite green across simple/medium/large/ambiguous/mismatched/unmatched/failed-catalog/failed-submission; retryable vs terminal failures behave predictably; demo seed set ready.

**Tickets**
- **P3-T1 — Failure isolation, retry & recovery**
  - Objective: Retryable vs terminal failure states; resume from failed stage without redoing successful upstream; low-confidence → hold.
  - Files: /backend/orchestrator/**, /backend/clients/**
  - Depends on: P1-T9, P2-A3
  - Acceptance criteria covered: PRD §14 (predictable retry/recovery), FR11; ARCHITECTURE §15.
  - Status: Todo
- **P3-T2 — Scenario test suite**
  - Objective: End-to-end tests for the eight PRD §18 scenarios against stubbed MCP + mock ClinRun.
  - Files: /tests/scenarios/**, /samples/**
  - Depends on: Phase 2
  - Acceptance criteria covered: PRD §18 (Scenario Tests), §15.
  - Status: Todo
- **P3-T3 — Unit + integration coverage**
  - Objective: Unit tests for extraction schema, context ranking, matching layers, decision policy, exception creation, state transitions; integration for end-to-end + retries. Decision policy + matching carry heaviest coverage.
  - Files: /tests/unit/**, /tests/integration/**
  - Depends on: Phase 2
  - Acceptance criteria covered: PRD §18 (Unit/Integration), § Code Quality Expectations.
  - Status: Todo
- **P3-T4 — Observability of workflow state**
  - Objective: Visible per-stage status, typed stage errors, traces reconstructing an invoice's path.
  - Files: /backend/orchestrator/**, /backend/api/**, /frontend/**
  - Depends on: P1-T9
  - Acceptance criteria covered: PRD FR11; ARCHITECTURE §15.
  - Status: Todo
- **P3-T5 — Performance**
  - Objective: Stable processing on large invoices/catalogs; responsive hub read/review actions.
  - Files: /backend/**, /frontend/**
  - Depends on: P2-A3, P2-A4
  - Acceptance criteria covered: PRD §14 (Performance Benchmarks).
  - Status: Todo
- **P3-T6 — Demo seed set**
  - Objective: Seed/process at least one submitted invoice, one held-for-ambiguity, one held-for-mismatch, one large invoice/catalog example.
  - Files: /samples/**, seed scripts
  - Depends on: Phase 2
  - Acceptance criteria covered: PRD §19 (Demo Requirements).
  - Status: Todo
- **P3-T7 — Docs & run instructions**
  - Objective: Finalize `docs/implementation-notes.md`; add README/run steps (`docker-compose up`, seed, demo walkthrough).
  - Files: /README.md, /docs/implementation-notes.md
  - Depends on: P3-T6
  - Acceptance criteria covered: PRD § Code Quality Expectations (demo clarity); ARCHITECTURE §17.
  - Status: Todo

### Phase 4 — Visual Document Review (Image + Bounding-Box Overlay)
Full spec: **`/docs/specs/visual-document-review.md`**. Adapts the OCR →
vision-extraction → SVG-overlay pattern from the OpenEMR `oe-module-clinical-copilot`
reference module (word-index citations: the model cites OCR word indices, the
server resolves them to normalized boxes, so highlights are verifiable, never
hallucinated).

**Goal**
- A reviewer sees the original document page image with the AI's extracted fields
  and line items highlighted as rectangles tied to the detail view (hover/click a
  field ↔ its box), uncertain fields visually distinct and gating clean approval —
  turning post-decision QC from "trust the text" into "verify against the source."

**✅ Scope & doc change — APPLIED (2026-05-27)** (per Update Rules — new scope requires an input-doc change)
- This **narrows PRD §4's "no perfect OCR/document intelligence" non-goal**: we add
  real OCR + vision extraction but only with **human-verifiable, source-anchored
  highlights** (imperfect extraction is caught by a reviewer, not trusted blindly),
  initially on the controlled-format real PDFs (RUNBOOK Path D). The gating doc
  changes are **done**: PRD §4 (narrowed wording) + §10 (image/overlay elements)
  and ARCHITECTURE §4/§8/§12/§13 (OCR + vision components, citation data model,
  page endpoints). **Phase 4 is unblocked.** Still out of scope: accuracy
  guarantees on arbitrary scans, handwriting, multi-language OCR, redaction/PHI.
- **Open decisions:** OD-6 OCR engine (Tesseract, behind `OCRClient` + offline
  stub); OD-7 vision provider (extends OD-2, offline stand-in default); OD-8 raster
  lib (PyMuPDF provisional); OD-9 citation persistence (detail/audit payload first,
  table later). New deps (Tesseract, raster lib, vision client) isolated behind
  client interfaces + stubbed for offline tests.

**Exit Criteria**
- Opening a rasterizable invoice shows the page image(s) in the Source section with
  per-field/line bounding-box highlights computed from real OCR geometry; uncertain
  fields must be confirmed/rejected (reusing P2-C3 QC + P2-C4 rerun, recorded as
  human audit events) before a clean "reviewed"; the **offline suite still passes
  with no network/model** (stub OCR + offline extraction stand-in synthesize
  citations for the controlled sample PDFs).

**Tickets**
- **P4-T1 — Page rasterization + image serving**
  - Objective: Render each source page (PDF/image) to a normalized raster; serve via `GET /api/invoices/:id/pages/:n/image` + `GET /api/invoices/:id/pages` (count + dims).
  - Files: /backend/parser/** (raster), /backend/api/**, /backend/clients/** (render lib)
  - Depends on: P1-T2, P2-A1 (+ RUNBOOK Path D PDFs)
  - Acceptance criteria covered: PRD §10 (Original invoice preview/download); OD-8.
  - Status: Done — `backend/parser/raster.py` (`render_pages`, PyMuPDF/OD-8, one backend for PDF + image); `GET /api/invoices/:id/pages` + `/pages/:n/image`; source located via `attachment_path` on the RECEIVED audit event. 115 tests pass.
- **P4-T2 — OCR word-box extraction**
  - Objective: `OCRClient` protocol (Tesseract default, `StubOCRClient` for offline) producing per-word boxes with normalized [0,1] coords + indices per page; `WordBox`/`BoundingBox` domain types.
  - Files: /backend/ocr/**, /backend/clients/**, /backend/domain/**
  - Depends on: P4-T1
  - Acceptance criteria covered: PRD §7 Step 2 (extended); OD-6.
  - Status: Done — `backend/ocr/` (`OCRClient`, `StubOCRClient` offline default reading the PDF text layer, `TesseractOCRClient` + `parse_tesseract_tsv` for OD-6); `WordBox`/`BoundingBox` domain types; `get_ocr_client()` factory. 122 tests pass.
- **P4-T3 — Vision extraction with word-index citations**
  - Objective: Extend `LLMClient` (vision method / `VisionLLMClient`) to take page image + OCR word list and return fields/line items each with `word_indices` + `status`; schema-validated; keep an **offline deterministic stand-in** (string-match values → OCR words).
  - Files: /backend/clients/** (LLM vision), /backend/extraction/** (prompts/schema), /backend/domain/**
  - Depends on: P4-T2, P1-T3/P2-A1
  - Acceptance criteria covered: PRD FR2 (source-anchored evidence); §8; OD-7.
  - Status: Todo
- **P4-T4 — Citation → bounding-box resolution + persistence**
  - Objective: Resolve `word_indices` → union `bbox` via OCR boxes (drop out-of-range); persist per-field/line `Citation {page, target_id, quote, bbox, status}`; surface in the detail payload (`pages`, `citations`).
  - Files: /backend/extraction/** (resolver), /backend/db/**, /backend/api/**, /backend/domain/**
  - Depends on: P4-T3
  - Acceptance criteria covered: PRD §10 (Source evidence), FR9; ARCHITECTURE §12; OD-9.
  - Status: Todo
- **P4-T5 — Reviewer overlay UI (image + SVG boxes)**
  - Objective: In the detail Source section, render the page image + an SVG overlay (`viewBox="0 0 1 1"`, `preserveAspectRatio="none"`) with a status-coloured `<rect>` per citation; two-way linking (field ↔ box) and multi-page support.
  - Files: /frontend/**
  - Depends on: P4-T4
  - Acceptance criteria covered: PRD §10 (Invoice Detail View), FR9; USERS § Reviewer.
  - Status: Todo
- **P4-T6 — Confirm / correct / approve from the overlay**
  - Objective: Confirm/reject uncertain fields and correct values/line matches from the highlighted view, reusing the P2-C3 QC routes (+ P2-C4 rerun); uncertain fields gate a clean "reviewed"; all actions recorded as human audit events.
  - Files: /frontend/**, /backend/api/** (reuse P2-C3 routes; optional confirm endpoint)
  - Depends on: P4-T5, P2-C3, P2-C4
  - Acceptance criteria covered: PRD FR10, §10 (QC actions); ARCHITECTURE §11, §17; USERS § Reviewer + § Exception Handler.
  - Status: Todo

---

## Dependency Order
1. P0-T1
2. P0-T2
3. P0-T3
4. P0-T4
5. P1-T1
6. P1-T2
7. P1-T3
8. P1-T4
9. P1-T5
10. P1-T6
11. P1-T7
12. P1-T8
13. P1-T9
14. P1-T10
15. P1-T11  ← MVP vertical slice complete
16. Phase 2 (Track A: P2-A1→A4) ∥ (Track B: P2-B1→B4) ∥ (Track C: P2-C1→C4), respecting per-ticket deps
17. Phase 3: P3-T1, P3-T4 → P3-T2, P3-T3, P3-T5 → P3-T6 → P3-T7
18. Phase 4 (new scope, doc change applied — unblocked): P4-T1 → P4-T2 → P4-T3 → P4-T4 → P4-T5 → P4-T6 (P4-T6 also needs P2-C3/C4, done)

## Recommended Next Step
- **PICK UP HERE (next session) → P4-T3 — Vision extraction with word-index citations.** Phase 4 is in progress on branch `p2-track-c-reviewer-hub`: **P4-T1 (rasterization + image serving) and P4-T2 (OCR word boxes) are DONE.** T3 extends `LLMClient` with a vision method (or adds `VisionLLMClient`) that takes a page image + the OCR word list (from `get_ocr_client()`/`StubOCRClient`) and returns fields/line items each carrying `word_indices` + a `status`; schema-validated. **Critical:** keep an **offline deterministic stand-in** that synthesizes `word_indices` by string-matching each extracted value against the OCR words (the network/model-free analogue of the model emitting indices, spec §7), so the suite stays green with no provider. Then T4 resolves indices→bbox + persists, T5 renders the overlay, T6 wires confirm/correct. Depends on P4-T2 (done), P1-T3/P2-A1 (done). Files: `/backend/clients/**` (LLM vision), `/backend/extraction/**` (prompts/schema), `/backend/domain/**`. Read `docs/specs/visual-document-review.md` §3–§4 + §7 and the two newest `docs/implementation-notes.md` entries (P4-T1, P4-T2) first.
  - Useful context already in place: `WordBox`/`BoundingBox` domain types; `get_ocr_client()` (offline `StubOCRClient` reads the controlled PDFs' text layer); `render_pages()` + the `/pages` + `/pages/:n/image` endpoints; OCR `index` is **0-based per page** and citations are page-scoped.
- **P3 (failure isolation, retry & recovery) still open and independent** if priorities shift: **P3-T1** — retryable vs terminal failure states; resume from a failed stage without redoing upstream work; low-confidence → hold (PRD §14, FR11; ARCHITECTURE §15). Depends on P1-T9, P2-A3 (done). All of Phase 2 is complete.
- Still OPEN — **Phase 1 exit gate (live stack)**: deferred only because the Docker daemon is unavailable in the dev session. Run once Docker is up, before demo:
  1. `docker compose up -d db` then `DATABASE_URL=postgresql+psycopg://invoicescreener:invoicescreener@localhost:5432/invoicescreener pytest tests/integration/test_postgres_repository.py` — un-skips the Postgres round-trip.
  2. `docker compose up` — API lifespan `init_schema` + reflection + the mcp-reference/mock-clinrun services end-to-end.
  3. Open the hub (`:5173`), POST a sample to `/api/invoices/process`, confirm it lists + detail renders.

## Deferred / Out of Scope
- PRD §4 Non-Goals: perfect OCR/document intelligence; supporting every invoice format; replacing finance/compliance workflows; production-scale email ingestion; full ClinRun production integration (mock used instead); guaranteed 100% match accuracy in ambiguous cases.
  - **Out-of-plan addition (2026-05-27, user-requested):** a *controlled-format* real-PDF demo path (`samples/generate_pdfs.py` → `backend/parser/pdf.py` + `backend/tools/process_pdf.py`, RUNBOOK Path D). We render the PDFs ourselves, so this does not claim to parse arbitrary invoices — the OCR/arbitrary-document non-goal stands. See implementation-notes 2026-05-27 "EXTRA".
  - **Phase 4 (2026-05-27, user-requested) revisits the OCR non-goal:** the new Visual Document Review phase (`/docs/specs/visual-document-review.md`) **narrows** "no perfect OCR/document intelligence" — it adds real OCR + vision extraction but only with human-verifiable, source-anchored highlights, initially on the controlled PDFs above. The non-goal is not repealed (no accuracy guarantee on arbitrary scans); it is scoped down. PRD §4/§10 + ARCHITECTURE amended 2026-05-27 — Phase 4 is unblocked.
- Authentication/authorization beyond basic operator access — not a PRD functional requirement; not planned for this scope.
- Real MCP reference API and real ClinRun backend — wired only if provided (OD-4, OD-5); otherwise stub/mock.
- Async job orchestration (OD-3) — MVP may run synchronously; revisited only if Phase 3 performance requires it.
- IMAP/Gmail live ingestion — PRD §7 Step 1 allows seeded/sample/mock intake; live email deferred.

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status (phase, ticket, blockers)
- Record blockers briefly (including any Open Decision a ticket is blocked on)
- Set the next recommended ticket
- Do NOT add new scope unless one of the REQUIRED input documents changes

---

## Rules (carried from the plan skill)
- Plan ONLY from the REQUIRED input documents.
- Apply the conflict-resolution order (PRD > STRATEGY > ARCHITECTURE > USERS > challenge) when documents disagree.
- Honor Open-decisions handling — never silently assume an unresolved choice.
- Group tickets around STRATEGY Tracks; deliver the MVP vertical slice before deepening.
- Preserve traceability: every ticket's acceptance criteria cites a PRD/STRATEGY/ARCHITECTURE/USERS clause.
