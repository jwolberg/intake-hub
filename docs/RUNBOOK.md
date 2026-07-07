# Runbook — Dev Setup & Run

Practical setup and run instructions for IntakeHub (the AI-first solopreneur
tax-ledger intake/classification/filing workflow). For *what* it does see
[`PRD.md`](./PRD.md) and the pivot's origin document
[`docs/brainstorms/solopreneur-ledger-requirements.md`](./brainstorms/solopreneur-ledger-requirements.md);
for *how* it's structured see [`ARCHITECTURE.md`](./ARCHITECTURE.md);
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
  clients/    LLM / Drive / Gmail / Sheets clients
  inbox receipts intake parser extraction categorize decision ledger_integrity exceptions audit corrections orchestrator
frontend/   React/Vite reviewer hub
samples/    sample invoices (inv_clean_001.json, inv_hold_unmatched_002.json)
tests/      unit / integration / scenarios
docker-compose.yml   db + api + hub + poller (drive/gmail profiles)
pyproject.toml       ruff + pytest config
```

## Services & ports

| Service | Port | What it is |
| --- | --- | --- |
| `api` | 8000 | FastAPI app (`/health`, `/api/invoices...`, `/docs`) |
| `hub` | 5173 | Vite dev server (reviewer hub) |
| `db` | 5432 | PostgreSQL 16 |
| `poller` | — | inbox-fetch loop sidecar (`--profile drive` or `--profile gmail`; no port, calls `api` over the Compose network) |

The async worker is intentionally not a service yet (Open Decision OD-3 — the MVP
runs synchronously).

## Environment variables

Defaults (in `backend/config.py`) target the Compose hostnames; override for local
runs.

| Variable | Default (Compose) | Local override example |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://intakehub:intakehub@db:5432/intakehub` | `...@localhost:5432/...` |
| `VITE_API_URL` (frontend) | `http://localhost:8000` | — |
| `ANTHROPIC_API_KEY` | _(unset → offline stand-in)_ | `sk-ant-...` (enables the real LLM provider, OD-2) |
| `LLM_MODEL` | `claude-opus-4-7` | e.g. `claude-haiku-4-5` |
| `INBOX_PROVIDER` | `mock` | `drive` (watched Google Drive folder) or `gmail` (connected Gmail mailbox) |
| `DRIVE_FOLDER_ID` | _(unset)_ | the watched folder's id (required when `INBOX_PROVIDER=drive`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | _(unset)_ | service-account key path or inline JSON (required when `INBOX_PROVIDER=drive`, or for the Sheets service account) |
| `SHEETS_SPREADSHEET_ID` | _(unset → offline `StubSheetsClient`)_ | id of the ledger spreadsheet to append to |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | _(unset)_ | the OAuth client (installed app) id/secret (required when `INBOX_PROVIDER=gmail`) |
| `GMAIL_REFRESH_TOKEN` | _(unset)_ | minted once by `backend/tools/gmail_oauth_setup.py` (required when `INBOX_PROVIDER=gmail`) |
| `GMAIL_TOKEN_ENC_KEY` | _(unset → env-only fallback)_ | a Fernet key encrypting the refresh token at rest |
| `GMAIL_LABEL` | _(unset)_ | the label `GmailInbox.on_processed` would apply if the integration ever gains Gmail *modify* scope — not yet actionable (read-only today, see Path F) |
| `GMAIL_TAX_YEAR` | current year | anchors the first-connect backfill (`after:<year>/01/01`) |

For the Google Drive folder intake source (`INBOX_PROVIDER=drive`), see
[`drive-intake-setup.md`](./drive-intake-setup.md) — service-account creation,
folder sharing, and the org-side Gmail→Drive glue — and **Path E** below for
running it in dev (setting the credentials) and exercising it in tests. For
the Gmail mailbox source and the Sheets ledger output, see
[`gmail-sheets-setup.md`](./gmail-sheets-setup.md) and **Path F** / **Path G**
below.

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
click one for the full extraction / classification / category / decision / audit
detail.

---

## Path B — Local backend dev (hot reload)

Run the app from a venv while using Docker only for the DB. Good for fast
iteration on backend code — there is no internal reference/submission service
to stand up (unlike the tool's clinical-trial predecessor); the Sheets/Gmail
clients talk directly to Google's APIs (or their offline stubs).

```bash
# 1. dependency via Docker
docker compose up -d db

# 2. Python env (from repo root)
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt

# 3. run the API against the local-mapped DB
export DATABASE_URL=postgresql+psycopg://intakehub:intakehub@localhost:5432/intakehub
uvicorn backend.api.main:app --reload --port 8000
```

Run `uvicorn` from the repo root so the `backend` package is importable.

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

Expected: lint clean; suite passes with **two skipped** tests — the Postgres
round-trip (`tests/integration/test_postgres_repository.py`), which is
skip-guarded and only runs when a real Postgres is reachable, and one Gmail
token-encryption test (`tests/unit/test_gmail_inbox.py`) that is skip-guarded
on the `cryptography` package (already listed in `backend/requirements.txt`;
it only skips if your venv predates that addition — reinstall
`backend/requirements-dev.txt` to pick it up. See Path F).

### Validating the Postgres path

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg://intakehub:intakehub@localhost:5432/intakehub \
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

## Path E — Google Drive folder intake (dev & test)

Run the pipeline against a **watched Google Drive folder** instead of the offline
mock inbox (`INBOX_PROVIDER=drive`). This path needs a service account and a
shared folder — that org-side setup (create SA, share folder as Editor, get the
folder id) lives in [`drive-intake-setup.md`](./drive-intake-setup.md) and is not
repeated here. Below is how to wire it up **locally**, plus how to exercise the
Drive code **without any credentials** in tests.

### Test — offline, no credentials

The Drive client, provider, and fetch-route are fully covered by an in-memory
`StubDriveClient` (seeded with PDF bytes) — no Google account, network, or key is
needed. Run just those suites:

```bash
source .venv/bin/activate                       # (create per Path B/C if needed)
pytest tests/unit/test_drive_client.py \
       tests/unit/test_drive_inbox.py \
       tests/integration/test_inbox_fetch.py -q
```

These verify list/download/move + typed errors, that each PDF files into
`submitted` / `needs-review` / `failed` by decision, and that a re-fetch is
idempotent (moved files are never re-listed). `HttpDriveClient` never imports
`google-auth` unless it actually mints a token, so the suite stays network-free.

### Dev — against a real Drive folder

You need two things from [`drive-intake-setup.md`](./drive-intake-setup.md): the
**service-account JSON key** and the watched **folder id**. Then set:

| Variable | Value |
| --- | --- |
| `INBOX_PROVIDER` | `drive` |
| `DRIVE_FOLDER_ID` | the watched folder's id |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to the SA JSON key **or** the key's inline JSON |
| `ANTHROPIC_API_KEY` | needed in practice — real Drive PDFs have no structured block, so without it extraction is blank and everything holds |

`GOOGLE_APPLICATION_CREDENTIALS` is treated as **inline JSON** when the value
starts with `{`, otherwise as a **file path**. Selecting `drive` without a folder
id or credentials fails fast at startup (no silent fall-back to the mock).

**Supplying the credentials — two options:**

```bash
# Option 1 — file path. Keep the key OUTSIDE the repo tree so it can't be
# committed (the repo only gitignores .env and /logs). e.g. ~/.config/:
mkdir -p ~/.config/intakehub
mv ~/Downloads/intake-drive-*.json ~/.config/intakehub/drive-sa.json
chmod 600 ~/.config/intakehub/drive-sa.json
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/intakehub/drive-sa.json"

# Option 2 — inline JSON from the macOS Keychain (mirrors the ANTHROPIC_API_KEY
# pattern below; keeps the key out of disk, shell history, and any .env). Store
# the whole JSON once, read it back at launch:
security add-generic-password -a "$USER" -s "ledgerrun-DRIVE_SA_JSON" -U -w < ~/.config/intakehub/drive-sa.json
export GOOGLE_APPLICATION_CREDENTIALS="$(security find-generic-password -s ledgerrun-DRIVE_SA_JSON -w)"
```

Then run the API with the Drive vars set and trigger a poll:

```bash
export INBOX_PROVIDER=drive
export DRIVE_FOLDER_ID="<folder-id-from-the-drive-url>"
export ANTHROPIC_API_KEY="$(security find-generic-password -s ledgerrun-ANTHROPIC_API_KEY -w)"
uvicorn backend.api.main:app --reload --port 8000     # + Path B's DB/service vars

# In another shell: poll the folder once (idempotent — safe to re-run)
python -m backend.tools.inbox_poller http://127.0.0.1:8000

# …or monitor continuously — poll every 60s until Ctrl-C
python -m backend.tools.inbox_poller --interval 60 http://127.0.0.1:8000
```

Each poll lists the folder root for new `.pdf`s, processes them, and moves each
into its status subfolder (the three subfolders are auto-created on first use).
Re-running never double-processes — files are recorded *seen* by Drive fileId
before the move. Config is read once at startup, so restart `uvicorn` after
changing any of these vars.

> **Turnkey monitoring (Compose).** Instead of running `uvicorn` + the poller by
> hand, set the Drive vars in `.env` (`cp .env.example .env`) and start the whole
> stack with the polling sidecar: `docker compose --profile drive up -d --build`.
> The `poller` service loops the fetch route on `INBOX_POLL_INTERVAL` (default
> 60s) so the folder is watched with no manual step. See the README's *Monitor
> your own Google Drive folder* section.

---

## Path F — Gmail intake (dev & test)

Run the pipeline against a **connected Gmail mailbox** instead of the offline
mock inbox or a Drive folder (`INBOX_PROVIDER=gmail`). Personal Gmail cannot
use a service account (no domain-wide delegation), so this path needs a
one-time interactive OAuth consent — that setup (create the OAuth client,
consent screen, mint the refresh token) lives in
[`gmail-sheets-setup.md`](./gmail-sheets-setup.md) and is not repeated here.
Below is how to wire it up **locally**, plus how to exercise the Gmail code
**without any credentials** in tests.

### Test — offline, no credentials

The Gmail client, inbox provider, and fetch-route wiring are fully covered by
an in-memory `StubGmailClient` (seeded with messages/attachments) — no Google
account, network, or key is needed. Run just those suites:

```bash
source .venv/bin/activate                       # (create per Path B/C if needed)
pytest tests/unit/test_gmail_client.py \
       tests/unit/test_gmail_inbox.py \
       tests/integration/test_inbox_fetch.py \
       tests/integration/test_ledger_integrity.py -q
```

These verify MIME part-walking + base64url decoding, backfill pagination and
`history.list` incremental sync (including a stale-`historyId` → full-resync
fallback), the receipt filter dropping non-receipts before a full fetch (with
only id/sender/subject retained for a drop — R17), and the duplicate-hold /
spot-check guards over posted items. `HttpGmailClient` never imports
`google-auth` unless it actually mints a token, so the suite stays
network-free. The Gmail token-encryption path
(`tests/unit/test_gmail_inbox.py`) additionally needs the `cryptography`
package (see Path C) to run un-skipped.

### Dev — against a real Gmail mailbox

You need three things from [`gmail-sheets-setup.md`](./gmail-sheets-setup.md):
the OAuth client's **id** and **secret**, and the **refresh token** minted by
`backend/tools/gmail_oauth_setup.py`. Then set:

| Variable | Value |
| --- | --- |
| `INBOX_PROVIDER` | `gmail` |
| `GMAIL_CLIENT_ID` | the OAuth client id |
| `GMAIL_CLIENT_SECRET` | the OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | the refresh token from `gmail_oauth_setup.py` |
| `GMAIL_TOKEN_ENC_KEY` | (recommended) a Fernet key so the refresh token is encrypted at rest instead of only living in the environment |
| `GMAIL_TAX_YEAR` | the tax year to backfill from (defaults to the current year) |
| `ANTHROPIC_API_KEY` | needed in practice — real Gmail receipts have no structured block, so without it extraction is blank and everything holds |

Selecting `gmail` without `GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`/
`GMAIL_REFRESH_TOKEN` fails fast at startup (no silent fall-back to the mock).

```bash
# One-time, interactive (opens a browser for consent) — prints a
# GMAIL_REFRESH_TOKEN=... line to copy from:
python -m backend.tools.gmail_oauth_setup /path/to/client_secret.json

export INBOX_PROVIDER=gmail
export GMAIL_CLIENT_ID="<from the Cloud Console OAuth client>"
export GMAIL_CLIENT_SECRET="<from the Cloud Console OAuth client>"
export GMAIL_REFRESH_TOKEN="<the token the setup script printed>"
export ANTHROPIC_API_KEY="$(security find-generic-password -s ledgerrun-ANTHROPIC_API_KEY -w)"
uvicorn backend.api.main:app --reload --port 8000     # + Path B's DB vars

# In another shell: poll the mailbox once (idempotent — safe to re-run)
python -m backend.tools.inbox_poller http://127.0.0.1:8000

# …or monitor continuously — poll every 60s until Ctrl-C
python -m backend.tools.inbox_poller --interval 60 http://127.0.0.1:8000
```

The first fetch backfills to `after:<GMAIL_TAX_YEAR>/01/01`; every fetch after
that uses `history.list` deltas, so re-running is cheap and idempotent (each
message is recorded *seen* before the receipt filter or the pipeline ever
touches it). Config is read once at startup, so restart `uvicorn` after
changing any of these vars.

> **Turnkey monitoring (Compose).** As with Drive, set the Gmail vars in
> `.env` (`cp .env.example .env`) and start the stack with the polling
> sidecar: `docker compose --profile gmail up -d --build`.

> **No modify scope, by design.** `GmailInbox.on_processed` is a documented
> no-op — this integration requests only `gmail.readonly` (least privilege,
> R20), so a processed message is never labeled or archived in the mailbox.
> The hub, not the mailbox, is the source of truth for review state — the same
> posture as the Drive path's status subfolders being a decision-time
> snapshot, not a live queue.

To revoke access (rotate/retire the connection):

```bash
python -m backend.tools.gmail_revoke
```

This revokes the refresh token at Google and clears the app's own encrypted
copy + sync cursor, so a subsequent connect starts clean and requires re-running
`gmail_oauth_setup.py`.

---

## Path G — Sheets ledger (dev & test)

Filed (`submit`-decided) items are appended as rows to a **user-owned Google
Sheet** instead of a submission backend. Auth is a **service account**
(`drive.file` scope — the service account owns the sheet it creates), the same
pattern as the Drive folder-intake source.

### Test — offline, no credentials

`StubSheetsClient` is an in-memory fake — no network, no credentials — and is
the default when `SHEETS_SPREADSHEET_ID`/`GOOGLE_APPLICATION_CREDENTIALS` are
unset. Run just those suites:

```bash
source .venv/bin/activate                       # (create per Path B/C if needed)
pytest tests/unit/test_sheets_client.py \
       tests/unit/test_sheets_ledger.py \
       tests/integration/test_ledger_integrity.py -q
```

These verify `append_row`/header-bootstrap behavior, and — the important
part — that `append_filed_row` is idempotent against the Postgres
`sheet_appends` dedup ledger: the same idempotency key (the invoice id) never
produces a second row, and a transient client failure propagates so the
caller's retry/hold logic (`sheet_write_failed`) can act on it.
`HttpSheetsClient` never imports `google-auth` unless it actually mints a
token, so the suite stays network-free.

### Dev — against a real Sheet

You need the **service-account JSON key** from
[`gmail-sheets-setup.md`](./gmail-sheets-setup.md) (a *different* service
account from the Drive one is fine, or the same one with both scopes granted).
Then set:

| Variable | Value |
| --- | --- |
| `SHEETS_SPREADSHEET_ID` | id of an existing spreadsheet to append to, or leave unset to have the first append **create** one titled "Solopreneur Ledger" |
| `GOOGLE_APPLICATION_CREDENTIALS` | the service-account key path or inline JSON |

```bash
export SHEETS_SPREADSHEET_ID="<spreadsheet-id-from-the-sheets-url>"   # or leave unset
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/intakehub/sheets-sa.json"
uvicorn backend.api.main:app --reload --port 8000     # + Path B's DB vars + an inbox provider
```

Process or fetch an invoice that decides `submit`; its ledger row
(`Type, Category, Vendor, Date, Amount, Source`) appears in the sheet's
`Ledger` tab. Re-processing the same item (rerun/recover) never double-posts —
the Postgres `sheet_appends` table, not the Sheet, is the dedup source of
truth.

---

## Common tasks

- **Reset the database:** `docker compose down -v && docker compose up -d db`
  (schema is recreated by the API on next startup).
- **Add a sample invoice:** drop a JSON file in `samples/` shaped like the existing
  ones (`source` + `document.metadata` + `document.line_items`) and POST it to
  `/api/invoices/process`. To make a matching PDF, add its stem to `_RENDERABLE`
  in `samples/generate_pdfs.py` and re-run the generator.
- **Process a real PDF:** see Path D.
- **Read invoices from a Google Drive folder:** see Path E (dev credentials + the
  credential-free test suite).
- **Read invoices from a connected Gmail mailbox:** see Path F.
- **File to a Google Sheet ledger:** see Path G.
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
