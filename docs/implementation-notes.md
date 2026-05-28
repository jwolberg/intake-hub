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

## 2026-05-26 — Phase 1 (P1-T10): API + PostgresRepository

**FastAPI routes** `POST /api/invoices/process`, `GET /api/invoices`,
`GET /api/invoices/:id` (PRD §13). The repository and the pipeline clients are
injected via FastAPI dependencies (`get_repo`, `get_pipeline_clients`), so tests
override them with `InMemoryRepository` + stubs — the routes are fully verified
with no DB and no network.

**Decision: `Annotated[...]` dependency style** (`RepoDep`, `ClientsDep`) instead
of `= Depends(...)` defaults — the latter trips ruff B008, and Annotated is
FastAPI's current idiom.

**`PostgresRepository`** reflects the schema created by `init_schema` (no second
copy of the DDL to drift). Handles JSONB (metadata/candidates/warnings/alternates/
exceptions/audit details) and NUMERIC via reflected column types, upserts
invoices/context, and replaces line-items/matches transactionally. `save_invoice`
upserts without touching `source_text`, so `set_source_text` isn't clobbered.

**⚠️ Validation gap — Postgres not exercised here.** The Docker daemon was
unavailable in this session (`docker compose up -d db` → cannot connect to the
daemon socket), so `PostgresRepository` was NOT run against a real database. The
round-trip test (`tests/integration/test_postgres_repository.py`) is
**skip-guarded** and skips when no DB is reachable. To validate:
`docker compose up` (full stack), or `docker compose up -d db` then
`DATABASE_URL=postgresql+psycopg://invoicescreener:invoicescreener@localhost:5432/invoicescreener pytest tests/integration/test_postgres_repository.py`.
Everything else (API + pipeline + orchestrator) is validated in-process.

**Validation:** `ruff` clean; `pytest -q` → **21 passed, 1 skipped** (the Postgres
round-trip). Manual API demo confirms `POST /process` on the clean sample returns
`status=submitted, decision=submit, confidence=1.0`.

**Follow-up (P1-T11):** the reviewer hub list + detail views on top of these
routes — completes the MVP vertical slice.

## 2026-05-26 — Phase 1 (P1-T11): reviewer hub — MVP slice complete

**React/Vite hub** (read-only, per the ticket and the human-after principle):
- **List view** — invoice #, vendor, sponsor/study, decision badge, status badge,
  confidence, exception count; row click → detail (PRD §10 list view).
- **Detail view** — decision + rationale (pulled from the terminal audit event),
  exceptions table, resolved context (+ warnings), extracted metadata, a line
  items ↔ matches table (raw description, qty, total, matched catalog item, match
  %, flags), and the processing timeline (PRD §10 detail view, FR9 §6).

Split into `api.js`, `App.jsx` (list↔detail nav + API status), and
`components/InvoiceList.jsx` / `InvoiceDetail.jsx`, with a small `styles.css`.
QC actions (correct/rerun/escalate) are intentionally out of scope here — Phase 2
(P2-C3).

**Validation:** `npm install` + `npm run build` → clean production build (34
modules). Python suite unchanged (21 passed, 1 skipped).

**⚠️ Not browser-validated here.** A live hub check needs the API running, which
needs Postgres + the stub services — i.e. `docker compose up` — and the Docker
daemon was unavailable this session. The build proves it compiles; rendering
against real data is part of the Phase 1 exit gate.

**Phase 1 (MVP vertical slice) is feature-complete:** intake → parse → extract →
resolve → catalog → match → decide → submit/hold, persisted + audited, exposed
over the API, and surfaced in the hub. Remaining before Phase 2: the live-stack
validation gate (Postgres round-trip, `docker compose up`, hub in a browser).

## 2026-05-26 — dev runbook + CORS fix

Added [`RUNBOOK.md`](./RUNBOOK.md) (dev setup/run: Docker full stack, local backend
dev, tests, ports, env vars, troubleshooting).

**Fix while writing it:** the hub (Vite :5173) calls the API (:8000) cross-origin,
but the FastAPI app had no CORS — the browser would block every hub request. Added
`CORSMiddleware` with origins from a new `CORS_ORIGINS` setting (default
`http://localhost:5173,http://127.0.0.1:5173`). Caught by documenting the flow,
not by a test (no browser in the dev session); worth a smoke check during the
live-stack validation gate.

## 2026-05-27 — Phase 2 Track A (P2-A1): robust multi-format extraction + evidence

**Live-stack exit gate still pending — Docker unavailable again this session**
(`docker info` → daemon down). Rather than block the build on an environment I
can't fix, proceeded to the next substantive ticket (P2-A1), which is fully
validated in-process. The Postgres round-trip + `docker compose up` + browser hub
check remain the open Phase 1 exit gate; re-run per `RUNBOOK.md` when Docker is up.

**Parser now detects format + locates the payload.** `parse` looks for the
invoice content as a parsed attachment (`document`) or inline (`body`), records
the `format` (`pdf` / `image` / `email_body` / `attachment` / `unknown`) on a new
`ParsedDocument.format` field, and lists which `sections` were present. A
structured payload is still serialised to JSON for the offline extractor; a
free-text body passes through verbatim (offline extraction then marks its fields
uncertain — honest, per PRD §4 non-goal).

**Extraction now captures per-field confidence + source evidence + missing
marking** (ARCHITECTURE.md §8: "confidence captured per field/item … so the hub
can highlight exactly which value is uncertain"). New `ExtractionResult` fields:
`field_confidence`, `field_evidence`, `missing_fields`.
- **Two field shapes accepted.** A field may be flat (`"vendor_name": "Acme"`) or
  annotated (`{"value": ..., "confidence": ..., "evidence": ...}`). The annotated
  shape lets a real provider (OD-2) supply its own confidence; the offline path
  derives it — present **and** corroborated in the parsed source text → 0.95,
  merely present → 0.9, missing/empty → 0.0 and listed in `missing_fields`.
- **Evidence cites the format**, e.g. `email body: "Riverside Clinical Research"`,
  so the hub (P2-C2) can show where each value came from.
- **Line items preserve source text** (`raw_source_text`, FR2) and get a
  confidence: complete (description + total) → 0.9, partial → 0.6.

**Decision: per-field signals are produced here but not yet persisted or surfaced.**
P2-A1's scope is `parser`/`extraction` (per the build plan). Threading
`field_confidence`/`missing_fields` into the decision policy (low extraction
confidence → hold, PRD §16) is **P2-B1**; persisting + showing value/confidence/
evidence in the hub is **P2-C2**. The orchestrator passes the richer
`ExtractionResult` through unchanged for now.

**Samples added:** `inv_body_003.json` (email-body delivery, no attachment →
SUBMIT) and `inv_image_004.json` (scanned-image attachment → SUBMIT) — exercise
the PDF/image/body variants the ticket calls for.

**Validation:** `ruff` clean; `pytest -q` → **31 passed, 1 skipped** (the Postgres
round-trip). Added `tests/unit/test_extraction.py` (7 tests: format detection,
per-field confidence/evidence, missing marking, line-item source text, annotated
confidence, partial-item confidence) + 2 pipeline tests (body + image → submit).

**Follow-up:** P2-A2 (context ranking, candidates & mismatch detection) is the
next Track A ticket; or run the live-stack gate once Docker is available.

## 2026-05-27 — Phase 2 Track A (P2-A2): context ranking, candidates & mismatch detection

**Candidates now carry canonical names.** `ContextCandidate` gained
`sponsor_name`/`study_name`/`protocol_number`/`site_name`, populated by the stub
reference client from the fixtures. This is what makes invoice-vs-reference
mismatch detection possible and gives the hub (P2-C2) meaningful candidate rows.
Backward-compatible (new optional fields); the HTTP client + stub server pick
them up for free via `model_dump`.

**Two failure modes are now surfaced as warning codes** (PRD FR3, FR8, §15):
- **Ambiguity** — a near-tied runner-up (within 0.1). `ambiguous_context` when the
  runner-up is a *different sponsor/study* (the clues disagree about who the
  invoice is for); `multiple_site_candidates` when it's a sibling *site* under the
  same study (the existing code, now reached only for true site ties).
- **Mismatch** — the invoice's stated sponsor/study/protocol/site contradicts the
  reference data for the resolved candidate. Detected by comparing invoice values
  against the top candidate's canonical names with a containment test
  (`_conflicts`), which tolerates harmless variation ("Riverside Clinical
  Research" vs "…- West") but catches real contradictions ("Acme Biosciences" vs
  "Northwind Therapeutics").

**Decision: warnings stay `list[str]` of stable codes**, not a structured model.
The decision stage matches them by membership, the orchestrator audits them, and
the hub `.join()`s them — all keep working unchanged. The specific conflicting
*values* are visible in `ctx.candidates` (now named). A structured/typed warning
+ richer hub display is deferred to the exception-taxonomy (P2-B2) and detail-view
(P2-C2) tickets — keeping P2-A2 to its `/backend/context` scope.

**Minimal decision-stage extension (acknowledged cross-ticket touch).** To keep
the §15 ambiguous/mismatched scenarios actually *holding* (never a silent submit),
the decision stage now raises `context_ambiguity` on any ambiguity warning and a
new `context_mismatch` flag on any mismatch warning. This generalises the
pre-existing single-code (`multiple_site_candidates`) check rather than building
the full P2-B1 severity model, which still owns the complete policy + tolerances.

**Sample added:** `inv_hold_mismatch_005.json` — the canonical §15 case: invoice
names sponsor "Acme Biosciences" but protocol NWT-101 / study NW-CARDIO-1 / site
Riverside map to Northwind. Resolves to Northwind (score 0.9) and flags
`sponsor_mismatch` → HOLD with a `context_mismatch` exception.

**Validation:** `ruff` clean; `pytest -q` → **37 passed, 1 skipped**. Added
`tests/unit/test_context.py` (5: ranked candidates, sponsor mismatch, sibling-site
ambiguity, no-match, lone-sponsor-clue) + 1 pipeline test (mismatch → hold).
Manual run: clean/body/image → no warnings; mismatch → `sponsor_mismatch`.

**Follow-up:** Track A continues with **P2-A3** (catalog robustness: large/missing/
empty/error + cache). The Phase 1 live-stack gate remains open (Docker unavailable).

## 2026-05-27 — Phase 2 Track A (P2-A3): catalog robustness + cache

**The typed errors already existed** (`CatalogNotFound` = hold reason,
`ReferenceUnavailable` = retryable transport failure — defined in P0-T4). P2-A3
makes the catalog stage *use* the distinction and adds caching.

**Catalog cache by `(sponsor_id, study_id)`** (the item deferred from P1-T5;
ARCHITECTURE.md §7, §12). Added a `CatalogCache` protocol + `InMemoryCatalogCache`
in the catalog package; `fetch(ctx, ref, cache=None)` returns a cache hit before
calling the reference. The orchestrator shares **one cache per `process_all`
batch**, so a batch of same-study invoices fetches the catalog once (verified:
3 Northwind invoices → 1 `get_catalog` call). Only successful fetches are cached —
a transient `ReferenceUnavailable` is never cached, so a retry re-hits the source.

**Decision: in-memory cache, not the `catalog_cache` table, for now.** A persistent
repo-backed cache would need Postgres validation, which is blocked by the Docker
gap. The in-memory cache lives behind the `CatalogCache` protocol, so a
Postgres-backed implementation drops in later without touching callers. Noted as
a follow-up; the `catalog_cache` table already exists (P0-T3) and stays unused
until then.

**Robustness, per PRD FR4/§14/§15 catalog-failure:**
- **Missing catalog** (`CatalogNotFound`) → orchestrator holds (`catalog_unavailable`),
  unchanged.
- **Transport/API error** (`ReferenceUnavailable`) → orchestrator now catches it
  *specifically* and marks the invoice `failed` with a typed `catalog_fetch_failed`
  exception (retryable), instead of falling through to the generic `stage_failure`.
  This realises §15 "mark catalog stage as failed; provide retry option" with a
  specific reason. (General retry/recovery across all stages is still P3-T1.)
- **Empty catalog** (scope known, zero items) returns `[]` — matching flags every
  line unmatched; not conflated with a missing catalog.
- **Large catalog** passes straight through (verified with a 2000-item fixture).

**Minimal orchestrator touch (acknowledged).** Threading the cache + the explicit
`ReferenceUnavailable` branch are small orchestrator edits, in service of P2-A3's
"API errors as typed failures" objective. P3-T1 owns the full retry/recovery model.

**Validation:** `ruff` clean; `pytest -q` → **46 passed, 1 skipped**. Added
`tests/unit/test_catalog.py` (7: unresolved, missing, transport-error, empty,
large, cache-hit, error-not-cached) + 2 orchestrator tests (catalog transport
error → failed not held; batch shares one cache).

**Follow-up:** Track A finishes with **P2-A4** (hybrid matching: normalize →
semantic → LLM adjudication → deterministic verify). Phase 1 live-stack gate still
open (Docker unavailable).

## 2026-05-27 — Phase 2 Track A (P2-A4): hybrid matching — Track A complete

**Replaced the baseline first-match matcher with the layered matcher**
(ARCHITECTURE.md §9), all four layers:
1. **Normalization** — lowercase, strip punctuation, collapse whitespace, drop
   filler words (`fee`, `visit`, `patient`, …) before comparison.
2. **Candidate generation** — every catalog item scored by token-set (Jaccard)
   similarity with an exact (1.0) / subset-containment (≥0.8) boost; ranked, kept
   above a 0.3 floor. No candidate above the floor → **unmatched material line
   item** (high-severity).
3. **LLM adjudication** — only for *ambiguous* cases (no confident exact match
   AND top two within 0.15). The injected `LLMClient` picks the best mapping +
   rationale. **Advisory**: an empty/unusable response falls back to the
   deterministic top, so the offline path (Passthrough) stays fully deterministic.
4. **Deterministic verification** — unit price *and* quantity·catalog_price→total
   compared with a 0.01 tolerance; a mismatch downgrades confidence (×0.5) and
   raises `amount_mismatch` / `quantity_mismatch` *regardless* of semantic score.

`MatchResult` now carries `quantity_match` and ranked `alternates` (the losing
candidates, chosen-item excluded — fixed a bug where a post-adjudication best
left the chosen item in its own alternates list).

**`match(line_items, catalog, llm=None)`** — `llm` is optional (default → pure
deterministic). The orchestrator passes the pipeline `llm`; the standalone
pipeline test omits it. Verified the five samples are unchanged end-to-end:
clean/body/image → submit, unmatched → held (unmatched_line_item), mismatch →
held (context_mismatch).

**Minimal decision-stage touch (acknowledged).** Added a `quantity_mismatch` HIGH
flag mirroring the existing `amount_mismatch` handling, so a quantity discrepancy
holds rather than silently submits. Full policy/tolerances remain P2-B1.

**Validation:** `ruff` clean; `pytest -q` → **53 passed, 1 skipped**. Added
`tests/unit/test_matching.py` (7: exact/containment, unmatched, amount mismatch,
quantity mismatch, LLM adjudication of an ambiguous tie, adjudication fallback,
large 2000-item catalog).

**Track A (interpretation engine) is complete** (P2-A1..A4). Next: Track B
(decisioning & explainability), starting **P2-B1** — full decision policy +
structured output, which will *consume* the per-field confidence (P2-A1) and the
context/matching flags built across Track A. Phase 1 live-stack gate still open
(Docker unavailable).

## 2026-05-27 — EXTRA (user-requested): real PDF ingestion demo path

**Scope note: this is outside the BUILD_PLAN tickets** — added on direct user
request ("is there any example pdf files I can use to test?" → chose to generate
real PDFs + a parser). PRD §4 lists real document intelligence as a non-goal, so
the build plan deliberately used a structured `document` stand-in. This adds a
*controlled-format* real-PDF path for demos without contradicting that non-goal:
we render the PDFs ourselves, so parsing our own layout is reliable, not a claim
to parse arbitrary invoices.

**What was added:**
- `backend/parser/pdf.py` — `extract_text` (pypdf text layer), `parse_invoice_text`
  (reverses the rendered layout → `{metadata, line_items}`), and `LayoutLLMClient`
  (that parse behind the `LLMClient` protocol = the offline extraction stand-in
  for the PDF format, mirroring `PassthroughLLMClient` for JSON). The shared label
  map + table markers live here so generator and parser never drift.
- `backend/parser/__init__.py` — `parse()` now reads a real PDF when
  `source.attachment_path` ends in `.pdf` (lazy import; JSON path untouched).
- `samples/generate_pdfs.py` — renders the JSON samples to `samples/pdf/*.pdf`
  via reportlab (labelled headers + monospaced pipe-delimited line-item table,
  chosen because it round-trips cleanly through pypdf — verified empirically).
- `backend/tools/process_pdf.py` — `python -m backend.tools.process_pdf <pdf>`
  CLI: runs the full pipeline and prints status/decision/context/matches/
  exceptions/trace. No DB, network, or API key.
- Committed PDFs: clean→submit, unmatched→hold, body→submit, mismatch→hold.

**Decision: deterministic `LayoutLLMClient`, not a real LLM.** The parser yields
real PDF text, which the offline Passthrough client can't turn into structure (it
echoes JSON). Rather than require an API key, the LayoutLLMClient deterministically
parses our known layout — same boundary a real provider (OD-2) would occupy. Keeps
the demo offline + reproducible.

**Decision: pypdf = runtime dep, reportlab = dev dep.** Parsing a PDF is a runtime
capability; rendering fixtures is dev tooling. Added to `backend/requirements.txt`
and `requirements-dev.txt` respectively. Two new deps, justified by the feature.

**Validation:** `ruff` clean; `pytest -q` → **65 passed, 1 skipped** (added
`tests/unit/test_pdf_parser.py` (4) + `tests/integration/test_pdf_pipeline.py` (8:
each sample reaches the expected decision, and PDF-path == JSON-path)). Manual:
all four PDFs flow end-to-end to the correct outcome via the CLI. RUNBOOK Path D
documents it.

**Follow-ups if this graduates from demo to feature:** a PDF *upload* API endpoint
(multipart) so the hub can ingest files; a real OCR/LLM extractor for arbitrary
(non-self-rendered) PDFs. Both isolated behind the existing parser/LLM boundaries.

## 2026-05-27 — Phase 2 Track B (P2-B1): full decision policy + structured output

**Replaced the all-HIGH baseline policy with the full §16 severity model.** The
decision stage now grades flags **low / medium / high** (PRD §16; ARCHITECTURE §10):
high blocks (→ hold), medium is visibility-only (rides along on a submit), low is
informational. It consumes every upstream confidence and the new signals from
Track A.

**Signature change: `decide(extraction, ctx, matches, catalog_available)`** — now
takes the full `ExtractionResult` (was `metadata, ctx, line_items, matches, …`) so
it can read P2-A1's per-field `field_confidence` / `missing_fields`. Two callers
updated (orchestrator, pipeline test).

**New policy behaviour:**
- **Missing critical fields** (`invoice_number`, `total_amount`) → HIGH
  `missing_invoice_number` / `missing_total` (PRD FR8, §16). Other missing fields →
  a single LOW `missing_optional_fields` (informational; still submits).
- **Extraction confidence** (weakest-link over populated fields + line items):
  `< 0.5` → HIGH `low_extraction_confidence` (hold); `0.5–0.8` → MEDIUM
  `moderate_extraction_confidence` (submit + visibility). Realises PRD §16 "Low
  extraction confidence → Hold".
- **Match confidence** (weakest matched item): `< 0.5` → HIGH `low_match_confidence`;
  `0.5–0.85` → MEDIUM `weak_match`.
- **`decision_confidence`** is now computed: submit = the weakest-link of context /
  extraction / match confidence; hold = a fixed 0.9 (confidence that deferring to a
  human is right). The clean samples now submit at 0.9 (honest — the offline
  extraction stand-in is a 0.9-confidence source) rather than the old 1.0.
- **Never a silent submit:** if no HIGH flag fires but `decision_confidence` is
  below the 0.5 floor, a HIGH `low_confidence` flag is raised → hold.

**Consolidation, not just addition.** The minimal per-flag holds added during
Track A (`context_mismatch`, `quantity_mismatch`, generalised ambiguity) are now
part of the single graded policy with `required_human_actions` per blocking flag.

**Sample outcomes unchanged:** clean/body/image → submit (conf 0.9),
unmatched/mismatch → hold. Existing exception-creation behaviour is unaffected
(`from_decision` still reports MEDIUM+HIGH; LOW informational flags create no
exception).

**Validation:** `ruff` clean; `pytest -q` → **75 passed, 1 skipped**. Added
`tests/unit/test_decision.py` (10: submit, high-flag hold, missing critical → hold,
missing optional → low+submit, low/moderate extraction confidence, weak match,
catalog unavailable, total mismatch, weakest-link confidence) — the decision-policy
unit coverage PRD §18 calls for.

**Follow-up:** Track B continues with **P2-B2** (exception taxonomy — typed
exceptions for all FR8 hold reasons with severity + specific message, surfaced to
the hub). Phase 1 live-stack gate still open (Docker unavailable).

## 2026-05-27 — Phase 2 Track B (P2-B2): exception taxonomy

**Canonical hold-reason taxonomy in the domain layer** (`backend/domain/taxonomy.py`):
a registry mapping every risk-flag / exception `code` → default `severity` + human
`title`, flagged `fr8=True` for the reasons PRD FR8 lists explicitly. Lives in
domain so the decision stage, exceptions stage, and orchestrator all depend on it
*downward* (never sideways on each other). Covers all 12 FR8 reasons plus the
operational failures (`submission_failed`, `catalog_fetch_failed`, `stage_failure`)
and confidence-band signals.

**One source of truth for severity.** The decision stage's `flag()` helper no
longer hardcodes severities — it looks them up via `severity_of(code)`. So a
reason's severity is defined once, in the taxonomy.

**Context specificity (FR8).** FR8 lists unresolved sponsor / study / site as
*separate* reasons; the decision stage now emits `unresolved_sponsor` /
`unresolved_study` / `unresolved_site` based on which id is missing (and
`context_unresolved` for the low-confidence-but-resolved case), instead of one
generic code.

**Shared factory `exceptions.build(invoice_id, code, message?, severity?)`** —
defaults severity + message from the taxonomy when not supplied. `from_decision`
uses it (reportable = medium/high; LOW stays informational, no exception). The
orchestrator's failure paths (`_fail`, submission failure) now go through it too —
which also removed a pre-existing **duplicate** `submission_failed` exception (the
old code added it once explicitly and again inside `_fail`).

**Hub:** exception `type` codes are humanised for display (`context_mismatch` →
"Context mismatch", raw code kept in a tooltip). Small, non-duplicating tweak; the
authoritative titles live in the backend taxonomy.

**Validation:** `ruff` clean; `pytest -q` → **82 passed, 1 skipped**; frontend
builds clean. Added `tests/unit/test_exceptions.py` (7): all FR8 reasons + all
orchestrator failure codes are registered; `build` defaults/overrides; unknown
codes fail safe to HIGH; the decision stage only emits *registered* codes with
taxonomy-consistent severities; `from_decision` reports medium/high only. Spot-
checked: mismatch → `context_mismatch`; empty doc → `unresolved_sponsor`,
`missing_invoice_number`, `missing_total`, `low_extraction_confidence`,
`catalog_unavailable`.

**Follow-up:** Track B continues with **P2-B3** (audit trail completeness — every
stage + human action with actor/action/before-after/reason; AI vs human
distinguishable). Phase 1 live-stack gate still open (Docker unavailable).

## 2026-05-27 — Phase 2 Track B (P2-B3): audit trail completeness

**`record()` gains `before` / `after` / `reason`** (PRD §17 event fields). They
fold into the event's JSONB `details` under standard keys — **no schema change**
(Docker-blocked), `details` is already JSONB. `before`/`after` capture a value
change (human corrections); `reason` is the rationale/note. `record` stays the
single audit writer.

**Stage events enriched to §17 completeness.** Every §17 item is now captured with
specifics: intake (channel/attachment), parse (format/sections), **extracted
values** (invoice number, field count, missing fields), reference lookup
(ids/confidence/warnings/#candidates), catalog retrieval (`catalog_size`), match
results (matched/total), AI decision (reason + risk_flags). Terminal events
(submitted/held/failed) carry the rationale via `reason=`.

**AI vs human is first-class.** Actor attribution: intake/parse/fail = `system`,
the model-driven stages (extract/context/match/decide) = `ai`, and corrections
land as `human`. P2-B3 provides + tests the human-correction audit shape
(actor=human, before→after, reason); the actual correction/rerun/escalate *routes*
arrive with P2-C3 and will call `record(actor=Actor.HUMAN, before=…, after=…,
reason=…)`.

**Hub:** the decision rationale now reads `details.reason` (falls back to the old
`rationale`/`error` keys for safety).

**Validation:** `ruff` clean; `pytest -q` → **86 passed, 1 skipped**; frontend
builds clean. Added `tests/unit/test_audit.py` (4: reason folding, clean details
when no optionals, human before/after/reason, AI-vs-human distinguishability) +
an orchestrator assertion on actor attribution + terminal reason. Verified the
full enriched trail end-to-end on the mismatch sample.

**Follow-up:** Track B finishes with **P2-B4** (strategy metric instrumentation —
auto-submit rate, false-submit rate, hold precision). Phase 1 live-stack gate
still open (Docker unavailable).

## 2026-05-27 — Phase 2 Track B (P2-B4): strategy metric instrumentation — Track B complete

**`backend/audit/metrics.py`** computes the three STRATEGY guardrail metrics from
the persisted invoices + audit trail, returned as a typed `WorkflowMetrics`:
- **auto_submit_rate** = submitted / decided (submitted + held).
- **false_submit_rate** = submits a human later *corrected/escalated* / submitted.
- **hold_precision** = holds a human confirmed (corrected/escalated) / holds a
  human dispositioned (any human event).

**Honest denominators.** A rate is `None` when its denominator is zero, so the
Operations Lead never sees a fabricated number. `false_submit_rate` and
`hold_precision` depend on **human outcomes** in the audit trail — they read as
0.0 / `None` until reviewers act. The correction/escalation routes that emit those
human events land with **P2-C3**; P2-B3 already defined the human-event audit
shape, so the metric just reads it. This is why the metric design slots cleanly
onto the existing trail rather than needing new storage.

**Surfaced two ways** (USERS § Operations Lead watches in aggregate):
- API: `GET /api/metrics` → `WorkflowMetrics`.
- Hub: a `MetricsBar` panel on the list view (throughput counts + the three rates,
  `—` when a rate has no data yet).

**Validation:** `ruff` clean; `pytest -q` → **91 passed, 1 skipped**; frontend
builds clean. Added `tests/unit/test_metrics.py` (4: throughput + auto-submit rate;
false-submit + hold-precision from human corrections/escalations; a reviewed-but-
not-corrected hold lowering precision; empty repo → all rates `None`) + an API test.

**Track B (decisioning & explainability) is complete** (P2-B1..B4). Next: Track C
(reviewer hub), starting **P2-C1** — list-view filters (submitted/held/failed/
needs-review/low-confidence/mismatched/unmatched). Phase 1 live-stack gate still
open (Docker unavailable).

## 2026-05-27 — Phase 2 Track C (P2-C1): list-view filters

**Backend (`backend/api/main.py`).** Added `_filter_tags(invoice, exceptions,
matches, ctx)` — one source of truth deriving the PRD §10 triage tags per invoice:
`submitted/held/failed` (status), `needs_review` (held|failed), `low_confidence`
(any low-confidence exception code, or a *submit* below the 0.75 watch line),
`mismatched_metadata` (context warning ending in `_mismatch`, or a
`context_mismatch` exception), `unmatched_line_items` (an unmatched match or the
`unmatched_line_item` exception). Tags ship in every list row (`filter_tags`) and
`GET /api/invoices?filter=<key>` narrows by tag membership; an unknown key → 422.

**Decision — low_confidence semantics.** Held invoices store
`decision_confidence = 0.9` (confidence in *deferring* to a human), so filtering
held invoices by a low confidence number would be wrong. Instead `low_confidence`
keys off the *exceptions* the decision stage already emits (low_extraction /
low_match / context_unresolved / low_confidence / moderate / weak), plus submits
whose `decision_confidence` cleared the floor but sits under 0.75. This reads the
real signal rather than a single misleading scalar.

**Frontend.** `InvoiceList` renders filter chips (with live counts + disabled when
empty) and an "All" chip, filtering **client-side over `filter_tags`** for instant
response and free counts. The backend `?filter=` param is the authoritative
server-side filter (same tags, tested) for API consumers; the hub reuses the tags
rather than refetching per chip. Also added Total to the list columns (PRD §10).

**Tradeoff.** Computing tags means `_summary` now also reads matches per row (it
already read context + exceptions). Fine at demo scale; if the list ever paginates
server-side, tags should be precomputed/persisted. Noted, not done (YAGNI).

**Validation:** `ruff` clean; `pytest -q` → **93 passed, 1 skipped** (+2 API tests:
filters narrow correctly + tags present; unknown filter → 422); frontend builds
clean. Next: **P2-C2** — detail-view full sections.

## 2026-05-27 — Phase 2 Track C (P2-C2): detail-view full sections

**Persisting per-field signals (the P2-A1 follow-up).** P2-A1 captured per-field
confidence/evidence but they were dropped after decisioning. P2-C2 persists them
on the **`extracted` audit event** (`field_confidence`, `field_evidence`,
`missing_fields`) — no schema change, works identically for InMemory + Postgres,
and keeps the audit trail the source of truth (PRD §17). Symmetrically, a *submit*
now records its `risk_flags` on the `submitted` event (held already did), and the
`received` event gained `subject`/`sender`. New `audit.latest_details(audit,
action)` reads the most recent event of a kind; the `GET /api/invoices/:id` route
projects `source` + `extraction` blocks onto the detail payload from it.

**Why audit, not new columns.** Postgres is untestable here (Docker unavailable),
so a column add would be the one change exercised only on an unrun path. Storing
on the audit event reuses the JSONB `details` both repos already round-trip, so
the tested in-memory path and the prod path share one mechanism. (If these signals
ever need indexed querying, promote them to columns — not now.)

**Frontend.** `InvoiceDetail` rebuilt into the six PRD §10 sections: Decision
(submit/hold + confidence + rationale + risk-flag table + submission status),
Source (subject/sender/channel/attachment + collapsible original preview),
Extracted Metadata (per-field value/confidence/evidence table, missing fields
flagged), Context Resolution (resolved + confidence + mismatch-warning badges +
candidate-alternatives table), Line Items & Matches (raw→normalized, qty/total,
matched item, confidence, rationale + exception flags), and the timeline (now with
inline reasons). Added severity badges (high/medium/low) + confidence colour
coding. Editable correction fields + QC action controls are P2-C3.

**Validation:** `ruff` clean; `pytest -q` → **94 passed, 1 skipped** (+1 API test:
source block, per-field confidence/evidence, submit risk_flags persisted); frontend
builds clean. Next: **P2-C3** — QC actions.

## 2026-05-27 — Phase 2 Track C (P2-C3): human QC actions

**Routes (PRD §13, FR10).** `POST /api/invoices/:id/` → `corrections/metadata`,
`corrections/line-item`, `reviewed`, `escalate`, `note`. Each records a
**human-attributed** audit event (so AI vs. human stays distinguishable, PRD §17)
and returns the refreshed detail. Added a `note` action to `AuditAction` (the
`action` column is free-text, so no migration). Escalate sets status `escalated`;
corrections set status `corrected`; reviewed/note leave status unchanged.

**Corrections as overlays (ARCHITECTURE.md §11), not in-place mutation.** New
`backend/corrections` module replays the append-only audit trail to derive the
current overlay: `metadata_overlay`/`match_overlay` (latest-wins), `effective_metadata`
(applies + re-validates so a human's `"1320.00"` becomes a `Decimal`), and
`apply_match_overlay` (pins a corrected line to the human's catalog choice,
confidence 1.0, flags cleared). The AI's original `invoices.metadata` /
`match_results` are never overwritten — the correction lives as a `corrected`
event with `before`/`after`, and the detail payload exposes both AI value and the
overlay. The list summary reflects corrected fields via `effective_metadata`, and
status flips to `corrected`, so the change is "reflected in invoice state" (FR10)
without losing what the AI produced.

**Decision (interpretation) — overlay vs. in-place.** PRD FR10 requires only
(a) corrections reflected in state and (b) audit distinguishing AI from human.
ARCHITECTURE §11 additionally requires overlays, not in-place edits. We satisfy
both with the audit-replayed overlay; no parallel correction table is needed
(works identically for InMemory + Postgres).

**Fix — metrics now bucket by decision, not status (P2-B4 ↔ P2-C3).** Escalating a
held invoice (or correcting a submitted one) moves its *status*, which would drop
it from `metrics.py`'s status-based buckets — exactly the invoices `false_submit_rate`
/ `hold_precision` measure. Reworked `compute_metrics` so the rate buckets key off
the immutable AI `decision` (submit/hold); throughput counts stay status-based.
This is why `hold_precision` finally populates once reviewers act, as P2-B4's note
anticipated.

**Frontend.** `InvoiceDetail` gains a QC Actions panel (Mark reviewed / Escalate /
Add note + a shared reason/note box), inline editable correction inputs per
metadata field (showing corrected value + AI original), and per-line catalog-match
correction inputs. Actions post and refresh detail + list + metrics via an
`onAction` callback threaded from `App`.

**Validation:** `ruff` clean; `pytest -q` → **103 passed, 1 skipped** (+5 API tests
for the routes/overlay/state + metrics integration; +4 unit tests for the overlay
helpers); frontend builds clean. Next: **P2-C4** — rerun with corrected data.

## 2026-05-27 — Phase 2 Track C (P2-C4): rerun with corrected data — Phase 2 complete

**`orchestrator.rerun(invoice_id, repo, ...)`.** Re-enters the pipeline for an
existing invoice using corrected data as fixed inputs (PRD FR10; ARCHITECTURE §6,
§11). Parsing + extraction are **not** re-run — their persisted outputs (source
text, metadata, line items) are reused via `_reconstruct_extraction`, which:
- rebuilds the `ExtractionResult` from `repo.get_line_items` + the per-field
  signals stored on the `extracted` audit event (P2-C2 synergy);
- applies the metadata correction overlay (effective, type-coerced) and marks each
  corrected field **reviewer-verified** (confidence 1.0, removed from
  `missing_fields`) so the decision stage no longer holds on it.

Context → catalog → matching → decision then recompute from those inputs; corrected
line matches are pinned via `apply_match_overlay`. Result: a correction that
resolves the original hold reason lets the invoice **submit on rerun** — the
correction loop closes end to end.

**Refactor (kept safe).** Extracted the shared "context → match → decide →
submit/hold" tail of `process` into `_resolve_match_decide`, now called by both
`process` and `rerun` (rerun passes the audit so corrected matches are pinned;
first-pass `process` passes none, so matching is unmodified). The full orchestrator
+ API suite confirms no regression.

**Frontend.** Added a "Rerun with corrections" button to the QC panel.

**Validation:** `ruff` clean; `pytest -q` → **106 passed, 1 skipped** (+3 rerun
tests: metadata-correction rerun clears a context-mismatch hold and submits;
line-match-correction rerun clears an unmatched-line hold and submits; unknown
invoice → 404); frontend builds clean.

**Phase 2 (Deepen the Tracks) is COMPLETE** — Track A (P2-A1..A4), Track B
(P2-B1..B4), Track C (P2-C1..C4). Next: Phase 3 (Hardening & Polish), starting
**P3-T1** (failure isolation, retry & recovery). Phase 1 live-stack exit gate
remains OPEN (Docker daemon unavailable in the dev session) — run `docker compose
up` per RUNBOOK to clear it before the demo.

## 2026-05-27 — Dev test on the live stack (Docker available): metrics fix + Phase 1 gate cleared

First run of the full `docker compose up` stack (Docker finally available). Drove
Track C end-to-end against the **real Postgres-backed** API + stub services:
process → filters (`filter_tags` + `?filter=`) → detail blocks (source/extraction/
corrections) → QC corrections → **rerun** → metrics. All worked; the headline
correction→rerun loop submitted both a context-mismatch hold (after a sponsor
correction) and an unmatched-line hold (after a line-match correction).

**Fix found in dev — metric bucketing (`backend/audit/metrics.py`).** With the
rerun flow exercised, `auto_submit_rate`/`false_submit_rate` came out wrong (0.75 /
0.667): three invoices the AI *held*, a human corrected and reran to submit, were
being counted as autonomous submits and as false submits. Root cause: P2-C3 had
switched the rate buckets to the invoice's *current* decision, which rerun flips
from hold→submit. Fixed to bucket on the AI's **first (autonomous) decision** from
the audit trail (`_first_decision` = first `submitted`/`held` event). Now the same
data reads honestly: auto_submit_rate 0.25 (only the clean invoice was autonomous),
false_submit_rate 0.0 (no autonomous submit needed fixing), hold_precision 1.0 (all
three holds were confirmed as genuinely needing a human). `pytest -q` → 106 passed,
1 skipped; existing metric unit tests unchanged and still green.

**Phase 1 live-stack exit gate — CLEARED.** (1) The skip-guarded Postgres
round-trip test passes against the live DB
(`DATABASE_URL=...@localhost:5432/... pytest tests/integration/test_postgres_repository.py`
→ 1 passed). (2) `docker compose up` brings up db/api/hub/mcp-reference/
mock-clinrun; `/health` reports `db: up`; schema bootstrap runs on lifespan.
(3) The hub serves at `http://127.0.0.1:5173` (`<title>InvoiceScreener</title>`,
`main.jsx`) and CORS preflight from that origin succeeds for GET and the POST QC
actions. Remaining: a human visual click-through of the hub UI in a browser.

**Env note (not a code issue).** A stray host `voicerace` Vite dev server is bound
to `[::1]:5173`; since macOS resolves `localhost`→IPv6 first, `http://localhost:5173`
hits *that* app, not ours. Use `http://127.0.0.1:5173` (or stop the other server)
to reach the InvoiceScreener hub.

## 2026-05-27 — Reviewer hub restyle to ClinRun look (user-requested)

Adopted the **colour, font, and sizing** of the ClinRun dashboard (per a provided
screenshot); **layout unchanged** (no JSX/structure edits). All changes in
`frontend/src/styles.css` + `frontend/index.html`:
- **Colour:** teal brand/action (`--primary #159a9c`) replaces the old blue
  (links, active filter chip, QC buttons); green "Active"-style positive
  (`--submit #3f9d54`) for submit/submitted badges; amber hold, red failed, soft
  slate text on light-gray panels — all via the `:root` token set.
- **Font:** `Open Sans` (400/600/700) loaded from Google Fonts in `index.html`,
  with `system-ui` fallback if the CDN is unreachable.
- **Sizing:** base `font-size: 13px` for the compact enterprise density; the
  existing rem-based type/spacing scales down proportionally, so layout structure
  is preserved.

**Tradeoff / assumption.** Open Sans is a close match to the screenshot's
neutral humanist sans (the exact face wasn't specified); the system-ui fallback
keeps the hub usable offline. Pulls one external CDN dependency (fonts only).
Frontend builds clean; verified in-browser against the live stack (list + detail
both pick up the teal/green palette + Open Sans).

## 2026-05-27 — P4-T1 Page rasterization + image serving (Phase 4)

First Visual Document Review ticket. Renders an invoice's original source
(PDF/image) to per-page PNGs and serves them, so later tickets can overlay
extracted-field highlights.

**What landed**
- `backend/parser/raster.py` — `render_pages(source, dpi=150) -> list[RenderedPage]`
  (`{page_number, width, height, image_png}`) + `is_rasterizable()`. Results
  cached per (resolved path, mtime, dpi) so serving `/pages` then `/pages/:n/image`
  rasterizes once.
- `backend/api/main.py` — `GET /api/invoices/:id/pages` (count + dims) and
  `GET /api/invoices/:id/pages/:n/image` (PNG, 1-based page). Source located via
  the path on the RECEIVED audit event.
- `backend/orchestrator/__init__.py` — RECEIVED audit details now also carry
  `attachment_path` (additive, JSONB; no schema change) so the endpoints can find
  the source file.
- `backend/requirements.txt` — added `PyMuPDF` (OD-8).

**Decisions / deviations (not pre-specified)**
- **One render backend (PyMuPDF), not two.** Spec §4.1 implied PyMuPDF for PDFs +
  Pillow for images. MuPDF opens an image as a one-page document, so a single
  `fitz.open()` path covers both — avoids adding Pillow as a *runtime* dep (it is
  only present transitively via the `reportlab` dev dep today). Swapping backends
  (`pdf2image`+poppler fallback) touches only `raster.py`.
- **Render logic lives in `parser/raster.py`, not a `clients/` seam.** The build
  plan listed `clients/** (render lib)`; ARCHITECTURE §4.1 maps it to
  `parser/raster.py`. Kept it in the parser with a *lazy* `import fitz` (mirroring
  `parser/pdf.py`'s lazy `pypdf`), so the 24 MB dep stays off the offline import
  path and the existing offline suite is unaffected.
- **Source located via `attachment_path` on the RECEIVED event.** Smallest
  additive change; the `process_pdf` CLI and PDF pipeline tests already supply it.
  Invoices without a rasterizable source (email-body, or JSON-only samples) return
  `[]` from `/pages` and 404 from the image route — correct: they have no original
  document image. Detail-payload `pages`/`citations` integration is deferred to
  P4-T4/T5 per the spec (T1 is rasterization + serving only).
- **On-demand rendering (+ small LRU), not persisted rasters.** Stateless and
  simplest at demo scale (single-page PDFs); a stored/cached blob can come later
  if needed.

**Risks / follow-ups**
- **Path handling:** `attachment_path` is operator-supplied at intake (CLI/sample),
  not an end-user upload, so the current demo trusts it. If invoice upload becomes
  user-facing, constrain rasterization to an allowlisted uploads root to avoid
  path traversal. Flagged, not addressed (out of T1 scope).
- PyMuPDF/SWIG emits harmless `DeprecationWarning`s under pytest.

**Validation:** `ruff check` clean on changed files; full suite **115 passed, 1
skipped** (was 106/1; +6 raster unit + 3 page-endpoint tests). No network/model.

## 2026-05-27 — P4-T2 OCR word-box extraction (Phase 4)

Adds the `OCRClient` seam + `WordBox`/`BoundingBox` domain types. OCR turns an
invoice's pages into per-word boxes (0-based per-page `index`, normalized [0,1]
bbox) that the vision extractor (T3) cites by index.

**What landed**
- `backend/domain/models.py` — `BoundingBox {x,y,width,height}` (all [0,1]) and
  `WordBox {page_number, index, text, bbox}`; exported from `backend.domain`.
- `backend/ocr/__init__.py` — `OCRClient` protocol; `StubOCRClient` (offline
  default); `TesseractOCRClient` (OD-6, lazy deps); `parse_tesseract_tsv` (pure).
- `backend/clients/__init__.py` — `get_ocr_client()` → `StubOCRClient()`, plus
  re-exports (mirrors the `get_llm_client()` seam).

**Decisions / deviations (not pre-specified)**
- **Offline stub reads the PDF text layer (PyMuPDF `get_text("words")`), not
  canned boxes.** Spec §7 says "canned word boxes ... geometry is known"; rather
  than hardcode per-sample fixtures, the stub derives exact geometry from the
  text layer of the controlled PDFs we render ourselves. Deterministic, no
  Tesseract/network, and generalizes to any PDF with a text layer. Image-only /
  text-less sources return `[]` (they'd need the real engine) — acceptable since
  the demo substrate is the controlled PDFs.
- **`extract_words(source, pages)` takes both the source path and rendered
  pages.** Honest minimal interface: the stub uses `source` (text layer), the
  real Tesseract client uses `pages` (page images). Both are products of the
  parser/raster stage. Documented asymmetry rather than a new wrapper type.
- **pytesseract/Pillow NOT added to requirements.** Following the OD-2 precedent
  (real LLM provider isn't a dep until wired), `TesseractOCRClient` imports them
  lazily; the default path needs neither. The TSV parser is split out so it's
  unit-tested with a canned string — no Tesseract binary required.
- **No orchestrator wiring yet.** OCR output is consumed by vision extraction
  (T3) and surfaced/persisted by T4; T2 is the client + types only (smallest
  change).

**Index semantics:** `index` is 0-based *per page* (objective says "indices per
page"); citations are page-scoped, so `word_indices` index into that page's list.

**Validation:** `ruff` clean; full suite **122 passed, 1 skipped** (+7 OCR tests).
No network/model/Tesseract.

## 2026-05-28 — P4-T3 Vision extraction with word-index citations (Phase 4)

Extends the LLM seam with a *vision* method and produces source-anchored
citations: each extracted value carries the OCR `word_indices` that back it
(never coordinates), so P4-T4 can union real OCR geometry into a highlight box
that cannot be hallucinated (spec §3).

**What landed**
- `backend/domain/` — `Citation {page_number, target_id, quote, word_indices,
  bbox|None, status}` + `CitationStatus` enum (extracted/uncertain/unreadable/
  missing); `ExtractionResult.citations`. `bbox` stays `None` until P4-T4.
- `backend/clients/vision.py` — `VisionLLMClient` protocol
  (`complete_vision_json(system, user, image, words)`); `OfflineVisionLLMClient`
  (offline default, OD-7); `StubVisionLLMClient` (scripted tests);
  `locate_value()` (pure word matcher). `get_vision_llm_client()` factory +
  re-exports in `clients/__init__.py`.
- `backend/extraction/` — `extract_vision(invoice_id, parsed, words, image,
  vision)`; factored the shared header loop into `_build_metadata`; citations
  built + schema-validated by `_build_citations`/`_citation_from_cell`.

**Decisions / deviations (not pre-specified)**
- **Offline stand-in synthesizes indices by string-matching, not a model
  (spec §7).** `OfflineVisionLLMClient` reads the values embedded in the prompt
  (the parser renders the doc as JSON, like the text path's
  `PassthroughLLMClient`) and matches each against the OCR words via
  `locate_value` — a contiguous-window match per page, falling back to best
  per-token coverage. Normalization strips case/punctuation so `INV-1001` and
  `$1,234.00` anchor cleanly. A real vision provider drops in behind
  `VisionLLMClient` with no other change (OD-7).
- **Client emits `page` per annotation.** OCR `index` is 0-based *per page*
  (P4-T2), so an index alone is ambiguous across pages. The matcher returns
  `(page, indices)` and citations carry both. (Sample PDFs are single-page, so
  page is always 1 today, but the contract is multipage-correct.)
- **Status = anchoring (client) refined by confidence (extraction).** The client
  sets `extracted`/`unreadable`/`missing` from whether OCR words back the value;
  `extract_vision` downgrades anchored-but-low-confidence (<0.7) to `uncertain`
  (which will gate a clean review in T5/T6). No backing words → `unreadable`
  (→ no box), enforced regardless of what the client claimed.
- **One citation per line item (`raw_description`).** Enough to demonstrate
  line-item highlighting; more attributes (qty/total) can be cited later without
  schema change. Line-item annotation rides under a `description_citation` key so
  it doesn't pollute the `LineItem` fields.
- **No orchestrator wiring / persistence yet.** Per the spec, T3 produces
  citations-with-indices on `ExtractionResult`; resolving indices→`bbox`,
  persisting, and surfacing in the detail payload is **P4-T4**. The text path
  (`extract`) is unchanged, so non-rasterizable invoices and all prior tests are
  unaffected. Repeated/duplicated values (e.g. vendor == site text) anchor to the
  first occurrence — acceptable for the offline analogue; a real model
  disambiguates by position.
- **Schema gate:** malformed `status` or non-integer `word_indices` raise
  `ValueError` (invalid model output is a failure, never passed downstream —
  ARCHITECTURE.md §8).

**Validation:** `ruff` clean; full suite **136 passed, 1 skipped** (+14 vision
tests: word matcher, offline client, citations + status edge cases). No
network/model.

## 2026-05-28 — P4-T4 Citation → bbox resolution + persistence + detail (Phase 4)

Resolves each citation's OCR `word_indices` into a highlight `bbox`, wires it into
the pipeline, persists it, and surfaces `pages` + `citations` in the detail
payload — so the reviewer overlay (P4-T5) has real boxes to draw.

**What landed**
- `backend/extraction/citations.py` — `synthesize_citations(extraction, words)`
  (offline analogue: string-match each extracted value to OCR words via
  `locate_value`) + `resolve_citations(citations, words)` (union cited word boxes
  → one `bbox`, drop out-of-range, `None` when nothing resolves) + `_union`.
  `UNCERTAIN_BELOW` moved here (single source of truth; `extraction/__init__`
  imports it).
- `backend/orchestrator/__init__.py` — `_attach_citations` runs after `extract`
  when the source is rasterizable (render → OCR → synthesize → resolve);
  citations persisted on the EXTRACTED audit event (`citations` key, OD-9, no
  schema change). New `ocr` param (defaults to `get_ocr_client()`).
- `backend/api/main.py` — `_build_detail` now returns `pages` (raster dims) +
  `citations` (each with page_number + normalized bbox), grouped client-side by
  page (spec §4.4).

**Decisions / deviations (not pre-specified)**
- **Citations synthesized from the extraction *result*, not the vision-client
  prompt.** T3 built `extract_vision`/`OfflineVisionLLMClient`, whose offline
  stand-in reads JSON from the prompt. But the controlled-PDF path parses to
  *layout text* (not JSON), so that stand-in can't drive it. Deriving citations
  from the final `ExtractionResult` + OCR words works uniformly for *both* the
  JSON/Passthrough and PDF/Layout extraction paths, and is the truer offline
  analogue (spec §7). `extract_vision` + `resolve_citations` remain the seam for a
  *real* vision provider (OD-7) that emits indices directly — both funnel through
  `resolve_citations`.
- **Orchestrator picks the offline extraction stand-in by source
  (`_extraction_llm`).** A real PDF (`attachment_path` ends `.pdf`) is parsed to
  layout text, which `PassthroughLLMClient` (offline JSON stand-in) cannot read —
  it returned empty extraction (a latent P4-T1 gap: the page endpoints worked but
  extraction didn't). For real PDFs we now substitute `LayoutLLMClient` (the
  offline PDF stand-in, as the `process_pdf` CLI does) so the controlled PDFs
  extract *and* get citations through the normal `process()`/API entry — making
  the T5 hub overlay demoable without manual client injection. **Keyed off
  `attachment_path`, not `parsed.format`** — `_detect_format` returns "pdf" for a
  JSON sample whose attachment is merely *named* `.pdf`, so a format check wrongly
  swapped the client on the JSON path (caught by the metrics/pdf-pipeline tests).
  An injected real provider (OD-2) is untouched.
- **Citations are additive + non-fatal.** Any render/OCR failure (or a source
  with no text/words) leaves the extraction untouched rather than failing the
  invoice; non-rasterizable invoices (email-body) get `pages: []`, `citations: []`.
- **Rerun does not re-synthesize citations** (kept in scope): the persisted
  citations reflect the AI's original read of the page, which is exactly what the
  overlay verifies against; corrections are already shown via the corrections
  overlay. Re-anchoring corrected values is a possible later refinement.

**Risks / follow-ups**
- Detail GET renders pages on every call (PyMuPDF; `render_pages` is LRU-cached).
  Fine at demo scale (single-page PDFs); revisit if hub latency matters (P3-T5).
- Repeated identical text on a page (e.g. vendor == site) anchors to the first
  occurrence — acceptable for the offline analogue; a real vision model
  disambiguates by position.

**Validation:** `ruff` clean; full suite **146 passed, 1 skipped** (+6 citation
unit + 2 detail-payload API; fixed the JSON-path regression the format check
introduced). No network/model.

## 2026-05-28 — P4-T5 Reviewer overlay UI (image + SVG boxes) (Phase 4)

Frontend-only. The detail Source section now renders the page image(s) with the
AI's source-anchored highlight boxes drawn over them, two-way linked to the
fields/line items.

**What landed**
- `frontend/src/api.js` — exported `API_URL` + `pageImageUrl(id, n)`.
- `frontend/src/components/InvoiceDetail.jsx` — `SourceOverlay` renders each page
  raster inside a positioned frame with an `<svg viewBox="0 0 1 1"
  preserveAspectRatio="none">` overlay; one `<rect>` per citation that has a
  resolved `bbox` (citations with `bbox === null` are skipped — no hallucinated
  box), coloured by `status` (extracted = teal, uncertain = dashed amber).
  Citations grouped by `page_number` for multi-page. Two-way linking via a shared
  `hovered` target_id: metadata rows + line-item rows carry `data-row` + hover
  handlers (a `rowLink` helper), the matching rect gets `is-highlight`, and
  clicking a rect scrolls its field into view.
- `frontend/src/styles.css` — `.page-frame`/`.page-image`/`.page-overlay` +
  `.cite-*` rect styles (`vector-effect: non-scaling-stroke` so stroke width is
  px-constant regardless of the unit-square viewBox) + `.row-linked` row
  highlight, all from the existing ClinRun palette tokens.

**Decisions (not pre-specified)**
- **No scaling math** — boxes are normalized `[0,1]` and the SVG uses
  `viewBox="0 0 1 1"` + `preserveAspectRatio="none"`, so a `<rect>` takes the bbox
  verbatim and the browser maps the unit square onto the rendered image at any
  size (spec §3/§4.5).
- **Overlay is pointer-transparent except the rects** (`pointer-events` only on
  `.cite-rect`) so the page image stays selectable and only the boxes are
  interactive.
- **Reused the existing detail payload** (`pages` + `citations` from P4-T4); no
  new endpoint. Page image bytes come from the existing `/pages/:n/image` route.

**Browser verification (offline, no Docker).** Ran the app with an in-memory repo
+ stub clients (throwaway runner, not committed) seeding the controlled PDF, and
the Vite dev server, then drove it with agent-browser: the INV-1001 detail shows
the page image with **13 teal highlight boxes** over the header/line text;
hovering a box highlights its field row (and fills the box) and vice versa.
(Default API/hub ports 8000/`localhost:5173` collide with a stray local server on
this machine — used 8011 + `127.0.0.1`; see the earlier 2026-05-27 env note.)

**Follow-up (P4-T6):** uncertain boxes are already visually distinct (dashed
amber); gating a clean "reviewed" on confirming them + confirm/correct from the
overlay is T6.

**Validation:** `npm run build` clean; backend suite unaffected (146 passed, 1
skipped). Verified live in a browser per the above.

## 2026-05-28 — P4-T6 Confirm / correct / approve from the overlay (Phase 4 DONE)

Closes Phase 4: uncertain source-anchored fields must be verified against the
page image (confirm) or corrected before a clean "reviewed", and confirmations
are recorded as human audit events.

**What landed**
- `backend/domain/enums.py` — new `AuditAction.CONFIRMED` (the `action` column is
  free-form TEXT, so no migration; metrics treat it like any human review action).
- `backend/api/main.py` — `POST /api/invoices/:id/citations/confirm`
  `{target_id, reason?}` records a human CONFIRMED event keyed to the citation's
  `target_id` (FR10, §17), returns the refreshed detail.
- `frontend/src/api.js` — `confirmCitation(id, targetId, reason)`.
- `frontend/src/components/InvoiceDetail.jsx` — a "Uncertain fields to verify"
  panel lists every `uncertain` citation (label + extracted value + state +
  Confirm button); the top-level **Mark reviewed** button is **gated** while any
  uncertain citation is neither confirmed nor corrected. A confirmed/corrected
  uncertain box renders green (`cite-confirmed`) in the overlay.
- `frontend/src/styles.css` — `.cite-confirmed` (solid teal).

**Decisions (not pre-specified)**
- **Reject == correct (no separate reject action).** Rejecting an uncertain value
  means it is wrong, so the reviewer uses the existing P2-C3 correction inputs —
  a correction (CORRECTED overlay) already resolves the gate. So T6 only adds a
  *confirm* path; correction is reused, not duplicated.
- **Gating is client-side** over data already in the detail payload: uncertain =
  `citations[].status === "uncertain"`; resolved = a CONFIRMED audit event for the
  `target_id` **or** a metadata/line correction overlay for it. No server-side
  "reviewed" guard was added — the backend stays a thin recorder; the hub enforces
  the workflow (consistent with the rest of the QC surface).
- **CONFIRMED is metrics-neutral.** On a held invoice any human action already
  counts as "reviewed" (only correct/escalate counts as "hold confirmed needed"),
  so a confirm behaves like REVIEWED/NOTE — it does not distort hold precision or
  false-submit rate (checked against `audit/metrics.py`).

**Browser verification (offline, no Docker).** Seeded the controlled clean PDF
(all citations high-confidence) plus a variant with a line-item total removed so
that line extracts at 0.6 → an **uncertain** citation. In the hub: the held
invoice showed the ECG box dashed-amber, the verify panel "1 pending", and **Mark
reviewed disabled**; clicking **Confirm** recorded a human `confirmed` event (seen
in the timeline), turned the box green, flipped the panel to "all verified", and
**enabled Mark reviewed**. (Ports: stray local servers held 5173/5174/8000, so the
dev server landed on 5175 and the API on 8011 — needed `CORS_ORIGINS` to include
the actual hub origin; see the 2026-05-27 env note.)

**Validation:** `ruff` clean; backend suite **148 passed, 1 skipped** (+2 confirm
endpoint tests); `npm run build` clean; full confirm→gate flow verified live.

## 2026-05-28 — "View source" side panel (UI refinement, user-requested)

Moved the original-document view out of the inline Source panel into a dismissible
right-hand **drawer** opened by a **View source ↗** button.

- `InvoiceDetail.jsx` — `SourceDrawer` (a fixed right `aside`) hosts the page
  image + highlight overlay (or the text preview / an empty-state when there is no
  rasterizable source); the Source panel keeps the metadata + the button (disabled
  when there is nothing to show). `styles.css` — `.source-drawer` etc.
- **Non-modal on purpose.** No backdrop, so the reviewer can still hover the
  metadata/line rows while the source sits alongside — the box ↔ field
  two-way highlighting (shared `hovered` state) stays live across the drawer.
  Reuses the existing `SourceOverlay` verbatim, so all P4-T5/T6 behaviour
  (status colours, click-to-scroll, uncertain gating) is unchanged, just relocated.

**Browser-verified** (offline runner): View source opens the drawer with the
page image + 13 boxes for the controlled PDF; hovering the Vendor row highlights
its box inside the drawer; a body-only invoice shows the text preview instead of
an image. `npm run build` clean; backend untouched.
