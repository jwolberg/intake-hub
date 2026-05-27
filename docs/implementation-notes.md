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
