# Runbook — Dev Setup & Run

Practical setup and run instructions for InvoiceScreener (the AI-first clinical
trial invoice intake/matching/decisioning workflow). For *what* it does see
[`PRD.md`](./PRD.md); for *how* it's structured see [`ARCHITECTURE.md`](./ARCHITECTURE.md);
for status see [`BUILD_PLAN.md`](./BUILD_PLAN.md).

## TL;DR

```bash
# Full stack (DB + API + hub + stub services)
docker compose up --build
# → API   http://localhost:8000  (docs at /docs)
# → Hub   http://localhost:5173
# Process a sample invoice:
curl -X POST http://localhost:8000/api/invoices/process \
  -H 'Content-Type: application/json' --data @samples/inv_clean_001.json
```

No LLM API key is required: the MVP uses an offline extraction stand-in
(`PassthroughLLMClient`). A real provider is wired later (Open Decision OD-2).

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

## Common tasks

- **Reset the database:** `docker compose down -v && docker compose up -d db`
  (schema is recreated by the API on next startup).
- **Add a sample invoice:** drop a JSON file in `samples/` shaped like the existing
  ones (`source` + `document.metadata` + `document.line_items`) and POST it to
  `/api/invoices/process`.
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
