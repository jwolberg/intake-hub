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
