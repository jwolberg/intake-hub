# Implementation

## Scope Implemented
- Requested scope: "as software-engineer use implement skill" — no ticket named, so per the skill the build plan's recommended next scope was taken: **Phase 0 — Decisions & Scaffolding**.
- Related phase: Phase 0
- Related ticket(s): P0-T1 (resolve backend stack), P0-T2 (scaffold monorepo + tooling), P0-T3 (domain types + persistence schema). **P0-T4 deferred** to the next pass.

## Approach
- **High-level strategy:** stand up the ARCHITECTURE.md §19 layout as an importable `backend` package with one package per pipeline stage, a typed Pydantic domain layer, a Postgres schema, a bootable FastAPI app, a minimal React/Vite hub shell, and a Docker Compose topology — then validate with lint + tests before stopping.
- **Key decisions:** Python/FastAPI (resolves OD-1); `backend` as a package to avoid generic top-level module names; SQLAlchemy Core over ORM; `Decimal` for money; best-effort schema bootstrap so the API boots without a DB.
- **Assumptions:** no `/docs/spec.md` exists — PRD/STRATEGY/ARCHITECTURE/USERS were used as the distributed spec (per the `plan` skill). Worker service and the mock-clinrun/mcp-reference services are deferred (OD-3 / P0-T4).

---

## Implementation Plan
1. Resolve OD-1 (backend stack) and record it.
2. Define shared domain enums + Pydantic models (the cross-stage contract).
3. Write the Postgres schema + a SQLAlchemy engine/bootstrap.
4. Create the FastAPI app with `/health` and a resilient schema-init lifespan.
5. Create empty-but-documented packages for each pipeline stage.
6. Add the React/Vite hub shell and Dockerfiles.
7. Add `docker-compose.yml` (db/api/hub) and repo dev-tool config (ruff + pytest).
8. Add a real domain unit test; run ruff + pytest + an API smoke test.

Files created/modified: see Code Changes.

---

## Code Changes

### File: backend/domain/enums.py
- Change summary: workflow/decision/severity/actor/audit enums mirroring ARCHITECTURE.md §6 and §10.

### File: backend/domain/models.py
- Change summary: Pydantic models — `Invoice`, `InvoiceMetadata`, `LineItem`, `ResolvedContext`, `ContextCandidate`, `CatalogItem`, `MatchResult`, `RiskFlag`, `DecisionResult`, `ExceptionRecord`, `AuditEvent` (PRD §11).

### File: backend/domain/__init__.py
- Change summary: re-exports the domain types/enums as the package's public surface.

### File: backend/config.py
- Change summary: dependency-free `Settings` dataclass read from env, defaulting to the Compose topology.

### File: backend/db/schema.sql
- Change summary: idempotent DDL for `invoices`, `line_items`, `resolved_context`, `match_results`, `exceptions`, `audit_events` (append-only), `catalog_cache` (ARCHITECTURE.md §12).

### File: backend/db/session.py
- Change summary: cached SQLAlchemy engine + `init_schema()` that applies `schema.sql` statement-by-statement.

### File: backend/db/__init__.py
- Change summary: exposes `get_engine`, `init_schema`.

### File: backend/api/main.py
- Change summary: FastAPI app; `/health` with best-effort DB check; lifespan bootstraps schema, tolerating an absent DB.

### File: backend/api/__init__.py · backend/__init__.py
- Change summary: package markers / module docstrings.

### Files: backend/{intake,parser,extraction,context,catalog,matching,decision,submission,exceptions,audit,orchestrator,clients}/__init__.py
- Change summary: documented stage package boundaries (logic lands in Phase 1 / P0-T4); each docstring cites the PRD/ARCHITECTURE clause and the ticket that fills it.

### File: backend/requirements.txt · backend/requirements-dev.txt · backend/Dockerfile
- Change summary: runtime deps (fastapi, uvicorn, pydantic, SQLAlchemy, psycopg), dev deps (ruff, pytest, httpx), and the backend image (build context = repo root to preserve `backend.` imports).

### File: frontend/{package.json,vite.config.js,index.html,src/main.jsx,src/App.jsx,Dockerfile}
- Change summary: minimal React/Vite hub shell that pings `/health`; dev-server image.

### File: docker-compose.yml
- Change summary: db (postgres:16) + api + hub, with a DB healthcheck and dependency ordering.

### File: pyproject.toml
- Change summary: repo-level ruff + pytest config (`pythonpath=["."]`, `testpaths=["tests"]`).

### File: tests/unit/test_domain.py
- Change summary: unit tests pinning invoice defaults, decimal amounts, decision structure, and the state-machine value set.

### Files: tests/integration/.gitkeep · tests/scenarios/.gitkeep · samples/.gitkeep
- Change summary: tracked placeholders for the ARCHITECTURE.md §19 structure.

---

## Acceptance Criteria Mapping
(Spec = PRD.md + ARCHITECTURE.md, since `/docs/spec.md` is distributed.)

- Criterion: OD-1 backend stack must be resolved before implementation tickets (BUILD_PLAN § Open decisions handling).
  - Implementation: Python/FastAPI selected and recorded.
  - File(s): docs/implementation-notes.md, docs/BUILD_PLAN.md

- Criterion: ARCHITECTURE.md §19 source layout (`/backend` per-stage packages, `/frontend`, `/tests/{unit,integration,scenarios}`, `/samples`, `docker-compose.yml`).
  - Implementation: layout created verbatim.
  - File(s): backend/**, frontend/**, tests/**, samples/, docker-compose.yml

- Criterion: PRD §11 data model / ARCHITECTURE.md §12 persistence.
  - Implementation: Pydantic domain models + Postgres schema for all seven entities.
  - File(s): backend/domain/models.py, backend/db/schema.sql

- Criterion: PRD §Dev Tools (Docker Compose, automated tests) + §Code Quality (observable, tested).
  - Implementation: Compose topology; ruff + pytest harness; 4 passing tests.
  - File(s): docker-compose.yml, pyproject.toml, tests/unit/test_domain.py

- Criterion: ARCHITECTURE.md §15 — one unavailable dependency should be visible, not fatal.
  - Implementation: `/health` + lifespan tolerate an absent DB (`db: down`).
  - File(s): backend/api/main.py

---

## Build Plan Mapping
- Ticket: P0-T1 — Resolve backend stack
  - Status: Complete
  - What was completed: Python/FastAPI chosen; OD-2..OD-5 confirmed provisional.
  - Remaining work: none.
- Ticket: P0-T2 — Scaffold monorepo + tooling
  - Status: Complete
  - What was completed: ARCHITECTURE §19 layout, FastAPI skeleton, frontend shell, Compose (db/api/hub), ruff+pytest harness.
  - Remaining work: worker + mock-clinrun + mcp-reference compose services (deferred to OD-3 / P0-T4).
- Ticket: P0-T3 — Shared domain types + persistence schema
  - Status: Complete
  - What was completed: domain enums/models, Postgres schema, engine/bootstrap, unit tests.
  - Remaining work: none.
- Ticket: P0-T4 — External client interfaces + stubs
  - Status: Todo (next)

---

## Validation
- How tested: `ruff check .` (lint), `pytest -q` (unit), and a FastAPI `TestClient` smoke check of `/health`.
- Lint/test results: ruff — all checks passed; pytest — 4 passed; `GET /health` → 200 `{"status":"ok","service":"invoicescreener-api","db":"down"}` with no DB running.
- Manual verification: `docker compose up` (db + api + hub); API at `:8000/health`, hub at `:5173` (requires Docker; not exercised in this environment).
- Visible user outcome: a bootable API + hub shell; the hub displays live API status. No invoice behavior yet (Phase 1).

---

## Open Issues
- Known limitations: pipeline stages are documented-but-empty packages; no invoice processing until Phase 1. No `worker`/`mock-clinrun`/`mcp-reference` services yet (P0-T4).
- Unresolved edge cases: none for this scope.
- Blockers: none. OD-1 resolved; OD-2..OD-5 remain provisional behind interfaces.

---

## BUILD_PLAN Update
- Current phase: Phase 0 — Decisions & Scaffolding (P0-T1..T3 Complete)
- Current ticket: P0-T4 — External client interfaces + stubs
- Updated ticket status: P0-T1 Complete, P0-T2 Complete, P0-T3 Complete, P0-T4 Todo
- Blockers: None
- Recommended next ticket: P0-T4

---

# Implementation — P0-T4 (External client interfaces + stubs)

## Scope Implemented
- Requested scope: continue Phase 0 — implement P0-T4.
- Related phase: Phase 0 (completes it).
- Related ticket(s): P0-T4.

## Approach
- For each external dependency, define a `Protocol` interface plus two
  implementations: an in-process stub (tests/offline) and an HTTP client (Compose
  topology). Back the stubs and the stub servers with one shared `fixtures`
  module so both paths return identical data. Make failures typed.

## Code Changes
### File: backend/clients/errors.py
- Typed errors: `ReferenceUnavailable` / `CatalogNotFound` (reference) and
  `SubmissionFailed` (ClinRun) — distinct because they drive retry vs. hold.

### File: backend/clients/fixtures.py
- Shared canned sponsors/studies/sites + sponsor+study-scoped catalogs.

### File: backend/clients/llm.py
- `LLMClient` protocol (`complete_json`), `StubLLMClient` (scripted + records
  calls), `parse_json_or_raise` helper.

### File: backend/clients/mcp_reference.py
- `MCPReferenceClient` protocol; `StubMCPReferenceClient` (ranks candidates,
  scopes catalog); `HttpMCPReferenceClient` (calls `mcp-reference`, maps errors).

### File: backend/clients/clinrun.py
- `ClinRunClient` protocol + `SubmissionResult`; `StubClinRunClient` and
  `HttpClinRunClient`.

### File: backend/clients/stub_servers/{mcp_app.py,clinrun_app.py}
- FastAPI stub services for `mcp-reference` and `mock-clinrun`, reusing fixtures.

### File: backend/clients/__init__.py
- Public surface + `get_reference_client()` / `get_clinrun_client()` /
  `get_llm_client()` factories (HTTP by default; LLM stub until OD-2 wired).

### File: backend/requirements.txt · backend/requirements-dev.txt
- `httpx` promoted to runtime.

### File: docker-compose.yml
- Added `mcp-reference` (:8100) and `mock-clinrun` (:8200) services; wired their
  URLs + dependency ordering into `api`.

### File: tests/unit/test_clients.py · tests/integration/test_stub_servers.py
- Unit tests for the in-process stubs; HTTP-client↔stub-server integration tests
  via FastAPI `TestClient`.

## Acceptance Criteria Mapping
- Criterion: ARCHITECTURE.md §7 (MCP integration: ranking, typed failures,
  catalog) → `MCPReferenceClient` + stubs + errors. File(s): mcp_reference.py, errors.py.
- Criterion: ARCHITECTURE.md §8 (provider-agnostic LLM, schema-validated JSON) →
  `LLMClient` + `parse_json_or_raise`. File(s): llm.py.
- Criterion: PRD FR3/FR4/FR7 dependencies isolated behind clients → factories +
  HTTP clients + Compose services. File(s): __init__.py, clinrun.py, docker-compose.yml.

## Build Plan Mapping
- Ticket: P0-T4 — External client interfaces + stubs. Status: Complete. Phase 0 done.

## Validation
- `ruff check .` clean; `pytest -q` → 13 passed (4 domain, 6 client unit, 3 integration).
- Visible outcome: `docker compose up` now starts db/api/hub/mcp-reference/mock-clinrun; clients reach the stubs.

## Open Issues
- `get_llm_client()` returns a stub until a real provider is wired in P1-T3 (OD-2).

## Next
- Phase 1, P1-T1 — intake of sample invoices (start of the MVP vertical slice).
