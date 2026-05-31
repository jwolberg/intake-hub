# Runbook — Dev Setup & Run

Practical setup and run instructions for InvoiceScreener (the AI-first clinical
trial invoice intake/matching/decisioning workflow). For *what* it does see
[`PRD.md`](./PRD.md); for *how* it's structured see [`ARCHITECTURE.md`](./ARCHITECTURE.md);
for status see [`BUILD_PLAN.md`](./BUILD_PLAN.md).

## TL;DR

```bash
# Full stack (DB + API + hub + stub services) + a hub full of PDF-backed invoices
docker compose up -d --build                          # → API :8000, Hub :5173
python -m backend.tools.seed_hub http://localhost:8000 # one-time: seed the hub
# Open http://localhost:5173 — invoices show with Vendor / Sponsor / Study filled.
# Process a single sample by hand instead:
curl -X POST http://localhost:8000/api/invoices/process \
  -H 'Content-Type: application/json' --data @samples/inv_clean_001.json
```

`seed_hub` renders the samples to real PDFs and posts them. The dev-only
`docker-compose.override.yml` (auto-loaded) mounts `samples/` into the `api`
container at the host path so the container can read those PDFs — run both
`docker compose` and `seed_hub` from the repo root so `$PWD` matches. Seeded
data persists in the `db_data` volume, so after a reboot just `docker compose
up -d` (no re-seed needed). This path is offline (no key); export
`ANTHROPIC_API_KEY` before `up` to use the real provider instead.

No LLM API key is required: by default the pipeline uses an offline extraction
stand-in (`PassthroughLLMClient` for JSON samples, `LayoutLLMClient` for PDFs).
To use a **real LLM provider** (OD-2) so extraction — including per-field
confidence — is model-derived, set `ANTHROPIC_API_KEY` (and optionally
`LLM_MODEL`, default `claude-opus-4-7`) before starting the API; see
§Environment variables. Restart the API after setting it (config is read once at
startup).

## Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Docker + Compose | recent | the one-command full stack (Path A) |
| Python | 3.11+ (3.10 works for tests) | backend, tests (Path B/C) |
| Node.js | 18+ (22 recommended) | frontend dev / build |

Everything is local-first; no cloud accounts are required.

## Repository layout

```
backend/    FastAPI app + pipeline stages + clients + persistence (package: backend)
  api/        HTTP routes (PRD §13)
  domain/     Pydantic domain types
  db/         schema.sql, engine/bootstrap, Repository (in-memory + Postgres)
  clients/    LLM / MCP-reference / ClinRun clients + stub servers
  intake parser extraction context catalog matching decision submission exceptions audit orchestrator
frontend/   React/Vite reviewer hub
samples/    sample invoices (inv_clean_001.json, inv_hold_unmatched_002.json)
tests/      unit / integration / scenarios
docker-compose.yml   db + api + hub + mcp-reference + mock-clinrun
pyproject.toml       ruff + pytest config
```

## Services & ports

| Service | Port | What it is |
| --- | --- | --- |
| `api` | 8000 | FastAPI app (`/health`, `/api/invoices...`, `/docs`) |
| `hub` | 5173 | Vite dev server (reviewer hub) |
| `db` | 5432 | PostgreSQL 16 |
| `mcp-reference` | 8100 | stub reference API (sponsor/study/site + catalog) |
| `mock-clinrun` | 8200 | stub ClinRun submission backend |

The async worker is intentionally not a service yet (Open Decision OD-3 — the MVP
runs synchronously).

## Environment variables

Defaults (in `backend/config.py`) target the Compose hostnames; override for local
runs.

| Variable | Default (Compose) | Local override example |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://invoicescreener:invoicescreener@db:5432/invoicescreener` | `...@localhost:5432/...` |
| `MCP_REFERENCE_URL` | `http://mcp-reference:8100` | `http://localhost:8100` |
| `CLINRUN_URL` | `http://mock-clinrun:8200` | `http://localhost:8200` |
| `VITE_API_URL` (frontend) | `http://localhost:8000` | — |
| `ANTHROPIC_API_KEY` | _(unset → offline stand-in)_ | `sk-ant-...` (enables the real LLM provider, OD-2) |
| `LLM_MODEL` | `claude-opus-4-7` | e.g. `claude-haiku-4-5` |

When `ANTHROPIC_API_KEY` is set, `get_llm_client()` returns `AnthropicLLMClient`
(official `anthropic` SDK, imported lazily) and extraction confidence comes from
the model. Unset → fully offline, network-free. After setting it, restart the API
and reprocess (e.g. `python -m backend.tools.seed_hub`) to see model-derived
confidences.

> **PDF-backed invoices need the real key.** The offline stand-in only echoes
> JSON; it cannot read a PDF text layer, so PDF samples (e.g. those seeded by
> `seed_hub`) come back with empty metadata (blank Vendor / Sponsor / Study)
> unless `ANTHROPIC_API_KEY` is set. JSON `document` samples work offline.

### Local secret: `ANTHROPIC_API_KEY` in macOS Keychain

For local dev the key is stored in the login Keychain under the service name
**`ledgerrun-ANTHROPIC_API_KEY`** — so it never lives in shell history, a
committed `.env`, or this repo.

```bash
# Store / update (prompts for the value, hidden; asks twice to confirm)
security add-generic-password -a "$USER" -s "ledgerrun-ANTHROPIC_API_KEY" -U -w

# Read it back into the environment when launching the API
ANTHROPIC_API_KEY="$(security find-generic-password -s ledgerrun-ANTHROPIC_API_KEY -w)" \
  uvicorn backend.api.main:app --reload --port 8000
```

In the cloud the same value lives in GCP Secret Manager and is injected as the
`ANTHROPIC_API_KEY` env var — see [`DEPLOY.md`](./DEPLOY.md).

---

## Path A — Full stack with Docker (recommended)

```bash
docker compose up --build          # all services; Ctrl-C to stop
docker compose up -d --build       # detached
docker compose logs -f api         # tail a service
docker compose down                # stop
docker compose down -v             # stop + wipe the DB volume
```

The API applies the DB schema on startup (`init_schema`), so no migration step is
needed. Then:

```bash
# Health (also reports DB connectivity)
curl http://localhost:8000/health

# Process the provided samples
curl -X POST http://localhost:8000/api/invoices/process \
  -H 'Content-Type: application/json' --data @samples/inv_clean_001.json
curl -X POST http://localhost:8000/api/invoices/process \
  -H 'Content-Type: application/json' --data @samples/inv_hold_unmatched_002.json

# List + inspect
curl http://localhost:8000/api/invoices
curl http://localhost:8000/api/invoices/<invoice_id>
```

Open the hub at <http://localhost:5173> — the list shows the processed invoices;
click one for the full extraction / context / matches / decision / audit detail.

---

## Path B — Local backend dev (hot reload)

Run the app from a venv while using Docker only for the dependencies (DB + stub
services). Good for fast iteration on backend code.

```bash
# 1. dependencies via Docker
docker compose up -d db mcp-reference mock-clinrun

# 2. Python env (from repo root)
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt

# 3. run the API against the local-mapped services
export DATABASE_URL=postgresql+psycopg://invoicescreener:invoicescreener@localhost:5432/invoicescreener
export MCP_REFERENCE_URL=http://localhost:8100
export CLINRUN_URL=http://localhost:8200
uvicorn backend.api.main:app --reload --port 8000
```

Run `uvicorn` from the repo root so the `backend` package is importable.

If you don't have Docker at all, you can run the two stub servers directly in
separate shells (each needs the venv) and point at your own Postgres:

```bash
uvicorn backend.clients.stub_servers.mcp_app:app --port 8100
uvicorn backend.clients.stub_servers.clinrun_app:app --port 8200
```

### Frontend dev

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (proxies to VITE_API_URL, default :8000)
npm run build          # production build into frontend/dist
```

---

## Path C — Tests & lint (no services required)

The unit/integration tests use in-process stubs and an in-memory repository, so
they need no DB or network.

```bash
source .venv/bin/activate           # (create per Path B if needed)
pip install -r backend/requirements-dev.txt

ruff check .                        # lint
ruff check --fix .                  # lint + autofix
pytest -q                           # full suite
pytest tests/unit -q                # just unit tests
```

Expected: lint clean; suite passes with **one skipped** test — the Postgres
round-trip (`tests/integration/test_postgres_repository.py`), which is
skip-guarded and only runs when a real Postgres is reachable.

### Validating the Postgres path

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg://invoicescreener:invoicescreener@localhost:5432/invoicescreener \
  pytest tests/integration/test_postgres_repository.py -q
```

This un-skips and exercises every JSONB/NUMERIC column round-trip through
`PostgresRepository`.

---

## Path D — Process a real PDF (demo)

The pipeline normally reads a structured `document` block (an OCR/parse stand-in).
For a tangible demo there is also a **real PDF** path: render the JSON samples to
PDFs, then run one through the full pipeline. No DB, network, or API key needed.

```bash
source .venv/bin/activate
pip install -r backend/requirements-dev.txt   # pulls reportlab (generator) + pypdf (parser)

# 1. generate sample PDFs → samples/pdf/*.pdf  (also committed, so this is optional)
python -m samples.generate_pdfs

# 2. watch one flow: parse PDF → extract → resolve → match → decide
python -m backend.tools.process_pdf samples/pdf/inv_clean_001.pdf
python -m backend.tools.process_pdf samples/pdf/inv_hold_mismatch_005.pdf
```

The CLI prints status, decision + confidence, resolved context, the line-item
matches, any exceptions, and the stage trace. How it works: `backend/parser/pdf.py`
extracts the PDF text layer (`pypdf`); the offline `LayoutLLMClient` parses that
text back into `{metadata, line_items}` (the extraction stand-in for the PDF
format — a real LLM provider, OD-2, replaces it for arbitrary invoices).

Provided PDFs: `inv_clean_001` (→ submit), `inv_hold_unmatched_002` (→ hold,
unmatched line item), `inv_body_003` (→ submit), `inv_hold_mismatch_005`
(→ hold, sponsor mismatch).

## Common tasks

- **Reset the database:** `docker compose down -v && docker compose up -d db`
  (schema is recreated by the API on next startup).
- **Add a sample invoice:** drop a JSON file in `samples/` shaped like the existing
  ones (`source` + `document.metadata` + `document.line_items`) and POST it to
  `/api/invoices/process`. To make a matching PDF, add its stem to `_RENDERABLE`
  in `samples/generate_pdfs.py` and re-run the generator.
- **Process a real PDF:** see Path D.
- **Browse the API:** interactive docs at <http://localhost:8000/docs>.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `/health` returns `"db": "down"` | Postgres not up / wrong `DATABASE_URL`. Start `db`, check the port. |
| `cannot connect to the Docker daemon` | Docker Desktop/colima not running — start it, or use Path C (no Docker). |
| `ModuleNotFoundError: backend` | Run `uvicorn`/`pytest` from the repo root. |
| API 500 on `/api/invoices` | App can't reach Postgres (it uses `PostgresRepository`). Bring up `db` and set `DATABASE_URL`. |
| Hub shows "API unreachable" | API not running on `VITE_API_URL` (default `:8000`), or CORS/host mismatch. |
| Postgres round-trip test always skips | Expected without a DB; see "Validating the Postgres path". |
