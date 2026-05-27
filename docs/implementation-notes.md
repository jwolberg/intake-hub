# Implementation Notes

Running log of decisions, deviations, and tradeoffs made during implementation.
Written for human review (per `.claude/CLAUDE.md` § Running implementation notes).

## 2026-05-26 — Phase 0 (P0-T1, P0-T2, P0-T3)

**Decision (P0-T1 / OD-1): backend stack = Python 3.11 + FastAPI.**
- Why: ARCHITECTURE.md §16 lists it first; Pydantic gives the schema-validated
  structured output that extraction (FR2) and decisioning (PRD §9) require.
- This resolves the only implementation-blocking open decision in BUILD_PLAN.md.

**Decision: `backend` is an importable package (`import backend.domain`).**
- Why: the stage directories (`domain`, `api`, `matching`, `context`, ...) would
  be generic top-level module names if `/backend` were the source root, risking
  collisions. Making `backend` the package keeps imports unambiguous. The on-disk
  layout still matches ARCHITECTURE.md §19 exactly.

**Deviation: the `implement` skill lists `/docs/spec.md` as REQUIRED; it does not
exist.** Per the `plan` skill, "spec" is distributed across PRD / STRATEGY /
ARCHITECTURE / USERS. Used those as the authoritative spec rather than stopping.

**Scope: docker-compose Phase 0 = db + api + hub only.**
- The async `worker` service is deferred (OD-3 — MVP may run synchronously).
- `mock-clinrun` and `mcp-reference` services are deferred to P0-T4, where their
  client interfaces are built.

**Tradeoff: SQLAlchemy Core (engine + `text`) instead of the ORM.** The domain
layer is already Pydantic and stages persist explicit typed rows; an ORM would
duplicate the model layer for no benefit at this stage.

**Tradeoff: monetary fields use `Decimal`, not `float`,** to avoid rounding drift
on amounts (matters for the amount-match check in FR5).

**Behavior: `/health` is best-effort.** Schema bootstrap and the DB check are
wrapped so the API boots even when Postgres is unavailable, reporting `db: down`
rather than crashing (ARCHITECTURE.md §15 — fail visible, not fatal).

**Validation:** `ruff check .` clean; `pytest -q` → 4 passed; FastAPI `TestClient`
`GET /health` → 200 `{"status":"ok","db":"down"}` with no database running.

**Follow-up:** Next ticket is **P0-T4** — external client interfaces + stubs
(`LLMClient`, `MCPReferenceClient`, `ClinRunClient`) and the `mock-clinrun` /
`mcp-reference` compose services.

## 2026-05-26 — Phase 0 (P0-T4) + Phase 0 complete

**Built the three external client interfaces, each with an in-process stub and an
HTTP implementation** (ARCHITECTURE.md §7-§8): `LLMClient` (provider-agnostic,
`complete_json`), `MCPReferenceClient` (`resolve_sponsor_study_site` returning a
*ranking*, `get_catalog`), `ClinRunClient` (`submit`). The `mcp-reference` and
`mock-clinrun` stub servers (FastAPI apps under `backend/clients/stub_servers/`)
reuse the same `fixtures` as the in-process stubs, so HTTP and in-process paths
return identical data.

**Decision: `httpx` promoted to a runtime dependency.** The HTTP clients use it
for service-to-service calls; it was previously dev-only.

**Decision: stub servers live in `backend/clients/stub_servers/` and reuse the
backend image** (different uvicorn target in compose), rather than a new
top-level dir — keeps the ARCHITECTURE.md §19 layout intact.

**Tradeoff: typed client errors** (`ReferenceUnavailable`, `CatalogNotFound`,
`SubmissionFailed`) so downstream stages can distinguish a *retryable* transport
failure from a *hold* reason (missing catalog), per ARCHITECTURE.md §7.

**Bug found + fixed during validation:** the in-process integration test first
used `httpx.Client(transport=ASGITransport(...))`, which fails — `ASGITransport`
is async-only and a sync `httpx.Client` cannot drive it. Switched to FastAPI's
`TestClient` (a sync `httpx.Client` subclass), injected straight into the HTTP
clients. This verifies the real wire format without containers.

**Resolution policy: `get_llm_client()` returns a stub for now.** A real provider
(OD-2) is wired by the first stage that needs it (extraction, P1-T3); the swap is
one edit in `backend/clients/__init__.py`.

**Validation:** `ruff check .` clean; `pytest -q` → **13 passed** (4 domain, 6
client unit, 3 HTTP-integration via TestClient).

**Phase 0 is complete.** Next: **Phase 1 (P1-T1)** — intake of sample invoices,
the start of the MVP vertical slice.

## 2026-05-26 — Phase 1 (P1-T1..T8): pipeline stages

**Built the eight pipeline stages as pure functions** (intake, parser,
extraction, context, catalog, matching, decision, submission/exceptions), each
in its package. Per ARCHITECTURE.md §4 stages are pure transformations; the
orchestrator (P1-T9) owns persistence + audit, so these stages take/return
domain objects and touch no DB.

**Decision: MVP extraction runs offline via `PassthroughLLMClient`.** The parser
renders the sample's `document` block to JSON; the passthrough LLM echoes it; the
extraction stage validates it into the domain models. This proves the pipeline
shape and the schema-validation gate without a provider key. A real provider
(OD-2) drops in at the `complete_json` boundary in P2-A1 — nothing else changes.
This is a deliberate stand-in, consistent with PRD §4 (no real document
intelligence) for the walking skeleton.

**Decision: removed the 1.0 cap from the reference stub's `_score`.** Study-level
clues (sponsor/protocol/study) are shared by every site under a study, so only
the site-name match can break a tie between sibling sites; clamping erased that
and made the clean sample look ambiguous. Confidence is still clamped to 1.0 in
`ResolvedContext`, but ranking uses the raw score.

**Baseline policy (P1-T7):** any HIGH-severity risk flag → hold; otherwise submit.
HIGH flags: unresolved/ambiguous context, catalog unavailable, unmatched line
item, amount mismatch, total mismatch. Full severity model + tolerances in P2-B1.

**Samples added:** `inv_clean_001.json` (all items match, totals reconcile →
SUBMIT) and `inv_hold_unmatched_002.json` (an item absent from the catalog →
HOLD). These cover the two terminal outcomes the MVP must show.

**Validation:** `ruff check .` clean; `pytest -q` → **15 passed** (added a chained
end-to-end pipeline test). Manual run confirms: clean → SUBMIT (conf 1.0, 4/4
matched), unmatched → HOLD (conf 0.9, rationale "1 line item(s) could not be
matched to the catalog").

**Follow-up (P1-T9):** add a `Repository` (in-memory for tests + Postgres for the
app) and the orchestrator that chains these stages, persists each output, and
writes audit events — then API (P1-T10) and hub (P1-T11).

## 2026-05-26 — Phase 1 (P1-T9): orchestrator + repository + audit

**Added a `Repository` protocol + `InMemoryRepository`.** Stages stay pure; the
orchestrator is the only writer. The interface lets the orchestrator/API depend
on the contract, not the engine. `PostgresRepository` is deliberately deferred to
P1-T10, where it'll be validated against a real Docker Postgres rather than
shipped untested.

**Orchestrator (`process` / `process_all`)** chains the eight stages, owns state
transitions (received → parsed → extracted → context_resolved → catalog_matched →
submitted | held | failed), persists each stage output, and appends an audit
event per step. Key behaviours:
- **Failure isolation:** a stage that raises is caught, the invoice is marked
  `failed` (retryable) with a `stage_failure` exception, and the error never
  propagates; `process_all` keeps going so one bad invoice can't halt the batch
  (PRD §14).
- **Catalog miss is not a failure:** `CatalogNotFound` flows through as
  `catalog_available=False` and the decision stage holds — no crash.
- **Submission failure** is a retryable `failed` state with a `submission_failed`
  exception, distinct from a deliberate `held`.

**`audit.record(repo, ...)`** builds + appends events with an `actor`
(system/ai/human), so the AI-vs-human distinction is in place from the start.

**Validation:** `ruff` clean; `pytest -q` → **18 passed** (+3 orchestrator tests:
clean→submitted with full audit trail, unmatched→held, and stage-failure
isolation across a batch). All without a DB.

**Follow-up (P1-T10):** `PostgresRepository` + the FastAPI routes
(`/process`, `/invoices`, `/invoices/:id`), validated end-to-end against the
`db` compose service.
