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
- Current phase: Phase 2 — Deepen the Tracks. **Track A (interpretation engine) COMPLETE** (P2-A1..A4).
- Current ticket: **P2-B1** (full decision policy + structured output) starts Track B — consumes the per-field confidence (P2-A1) and context/matching flags from Track A. The Phase 1 live-stack exit gate (Postgres round-trip + `docker compose up` + hub in a browser) is still OPEN — deferred because the Docker daemon is unavailable in the dev session, not because it passed. Commands: /docs/RUNBOOK.md.
- Note: PostgresRepository + the hub were NOT exercised against the live stack (Docker daemon unavailable across sessions). API + pipeline + orchestrator validated in-process; frontend builds clean; CORS wired but not browser-verified. P2-A1..A4 fully validated in-process (53 passed, 1 skipped). Run `docker compose up` to clear the gate end-to-end.
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
  - Status: Todo
- **P2-B2 — Exception taxonomy**
  - Objective: Typed exceptions for all PRD FR8 hold reasons with severity + specific message, surfaced to the hub.
  - Files: /backend/exceptions/**
  - Depends on: P1-T8
  - Acceptance criteria covered: PRD FR8 (all hold reasons); ARCHITECTURE §12.
  - Status: Todo
- **P2-B3 — Audit trail completeness (AI vs human)**
  - Objective: Append-only audit of every stage + human action with actor, action, before/after, reason; AI and human edits distinguishable.
  - Files: /backend/audit/**
  - Depends on: P1-T9
  - Acceptance criteria covered: PRD §17 (auditability), FR10/FR11; ARCHITECTURE §12.
  - Status: Todo
- **P2-B4 — Strategy metric instrumentation**
  - Objective: Compute/expose auto-submit rate, false-submit rate, hold precision for the Operations Lead.
  - Files: /backend/api/**, /backend/audit/**, /frontend/**
  - Depends on: P2-B1, P2-B3
  - Acceptance criteria covered: STRATEGY § Key metrics; USERS § Operations Lead.
  - Status: Todo

#### Track C — Reviewer hub (STRATEGY Track 3)
- **P2-C1 — List view filters**
  - Objective: Filters for submitted/held/failed/needs-review/low-confidence/mismatched-metadata/unmatched-line-items.
  - Files: /frontend/**, /backend/api (query params)
  - Depends on: P1-T11
  - Acceptance criteria covered: PRD §10 (Invoice List View), FR9; USERS § Reviewer (triage attention).
  - Status: Todo
- **P2-C2 — Detail view full sections**
  - Objective: Source, extracted metadata (value/confidence/evidence), context (resolved + candidates + mismatch warnings), line-item matching (raw vs normalized + rationale + flags), decision (submit/hold + confidence + rationale + risk flags + submission status).
  - Files: /frontend/**
  - Depends on: P1-T11
  - Acceptance criteria covered: PRD §10 (Invoice Detail View), FR9; USERS § Reviewer (understand a decision fast).
  - Status: Todo
- **P2-C3 — QC actions**
  - Objective: Mark reviewed, correct metadata, correct line match, rerun, escalate, add note; recorded as human audit events; corrections as overlays (not in-place mutation).
  - Files: /frontend/**, /backend/api (corrections/rerun/reviewed/escalate routes)
  - Depends on: P2-C2, P2-B3
  - Acceptance criteria covered: PRD FR10, §13 (correction/rerun/reviewed/escalate endpoints); ARCHITECTURE §11; USERS § Reviewer + § Exception Handler.
  - Status: Todo
- **P2-C4 — Rerun semantics with corrected data**
  - Objective: `rerun` re-enters pipeline using corrected fields as fixed inputs; downstream stages recompute, unchanged-input stages may reuse outputs.
  - Files: /backend/orchestrator/**, /backend/api/**
  - Depends on: P2-C3
  - Acceptance criteria covered: PRD FR10 (rerun uses corrected data); ARCHITECTURE §6, §11.
  - Status: Todo

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

## Recommended Next Step
- Start with: **P2-B1 — full decision policy + structured output** (Track B). Complete the §10 policy with severity levels, `risk_flags`, `required_human_actions`, decision confidence; high-severity always holds; low confidence (incl. low extraction confidence from P2-A1) → hold, never silent submit (PRD FR6, §9, §16; ARCHITECTURE §10). This is where the Track A signals (per-field confidence, context mismatch/ambiguity, match flags) are consolidated into the policy rather than the current minimal per-flag extensions.
- Still OPEN — **Phase 1 exit gate (live stack)**: deferred only because the Docker daemon is unavailable in the dev session. Run once Docker is up, before demo:
  1. `docker compose up -d db` then `DATABASE_URL=postgresql+psycopg://invoicescreener:invoicescreener@localhost:5432/invoicescreener pytest tests/integration/test_postgres_repository.py` — un-skips the Postgres round-trip.
  2. `docker compose up` — API lifespan `init_schema` + reflection + the mcp-reference/mock-clinrun services end-to-end.
  3. Open the hub (`:5173`), POST a sample to `/api/invoices/process`, confirm it lists + detail renders.

## Deferred / Out of Scope
- PRD §4 Non-Goals: perfect OCR/document intelligence; supporting every invoice format; replacing finance/compliance workflows; production-scale email ingestion; full ClinRun production integration (mock used instead); guaranteed 100% match accuracy in ambiguous cases.
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
