# IntakeHub

An **AI-first** workflow that turns a solopreneur's emailed receipts into a
categorized **income/expense ledger** in a Google Sheet. It reads a receipt from
your inbox, extracts the vendor / date / amount with per-field confidence,
classifies it **income vs. expense** and assigns a **Schedule C category**, and
then **decides on its own to file it to the Sheet or hold it for review — with no
human gate before decisioning**. Reviewers get a clear *post-decision* QC hub to
confirm outcomes, correct a category, resolve holds, or reject a non-receipt.

The hard part was already solved — reading messy documents, extracting with
confidence, deciding autonomously whether to file, and leaving an audit trail.
This is that engine, pointed at a solopreneur's tax-time paperwork.

> **Note on docs:** the product-facing docs (this README, ARCHITECTURE) describe
> the ledger product. `PRD.md` / `STRATEGY.md` / `USERS.md` are being refreshed
> from the prior clinical-trial framing in a follow-up docs pass.

## What it does

- **Ingests receipts** from a pluggable inbox: a connected **Gmail mailbox**
  (`INBOX_PROVIDER=gmail`), a watched **Google Drive folder** (`INBOX_PROVIDER=drive`),
  or the offline demo set (default). No source is privileged.
- **Filters receipts from noise** — newsletters and personal mail are dropped
  before they ever become ledger candidates (minimal retention; email content is
  treated as untrusted data, not instructions).
- **Extracts + classifies + categorizes** each receipt with per-field confidence.
- **Files or holds** by confidence: a confident receipt appends one row to your
  Google Sheet (type, category, vendor, date, amount, source); an uncertain one is
  held with a typed reason. Low confidence never silently files.
- **Protects the ledger after the fact** — suspected duplicates are held (never
  double-posted), holds surface in a notification digest, and posted rows can be
  spot-checked.
- **Keeps you in control** — a reviewer hub to correct a category, resolve a
  hold, reject a non-receipt, or rerun, all as append-only audit overlays that
  never mutate the AI's original output.

Your Sheet is **user-owned and portable** — the app appends to it, but Postgres is
the ledger of record, so the Sheet is a durable projection you can take anywhere.

## Quickstart (Docker, ~1 command)

```bash
docker compose up -d --build                           # db + api + hub
python -m backend.tools.seed_hub http://localhost:8000 # seed the demo documents
# → API  http://localhost:8000  (/docs)
# → Hub  http://localhost:5173
```

Open the hub, click an item, and use **View source** (page image + the AI's
highlight boxes, plus **Open original PDF**). No API key or network is required —
the offline extractor handles the controlled sample PDFs and the ledger append
uses an in-memory stub. To use the real LLM provider (model-derived confidence),
export `ANTHROPIC_API_KEY` before `up` (see [`docs/RUNBOOK.md`](docs/RUNBOOK.md)).
Full local-dev paths (hot reload, tests, real-PDF CLI) are in the RUNBOOK; cloud
deploy is in [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Connect a real source

**Gmail mailbox** — the headline source. A one-time interactive OAuth setup mints
a read-only refresh token; the poller then backfills the tax year and watches
forward, filing confident receipts to your Sheet. Personal Gmail uses installed-app
user-OAuth (a service account can't read a personal mailbox). Step-by-step —
OAuth client, single-user "production" consent, the setup script, the Sheets
service account, and at-rest token encryption — is in
[`docs/gmail-sheets-setup.md`](docs/gmail-sheets-setup.md) (dev & test wiring:
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) Paths F & G).

**Google Drive folder** — point IntakeHub at a folder and it continuously watches
it: every new PDF is pulled, run through the pipeline, and moved into a
`posted` / `needs-review` / `failed` subfolder by its decision, while the hub stays
the source of truth for review. Setup is in
[`docs/drive-intake-setup.md`](docs/drive-intake-setup.md) (dev & test:
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) Path E). Start it with the poller sidecar:

```bash
docker compose --profile drive up -d --build   # adds the folder poller
```

Selecting `gmail` or `drive` without its credentials **fails fast at startup** — it
never silently falls back to the demo set.

## How it works

A receipt → a staged pipeline, each stage a focused module behind the
orchestrator, which owns state transitions and writes an append-only audit trail:

```
intake → receipt filter → parser → extraction → classify + categorize → decision → Sheet append | hold
        (drop non-receipts)          (LLM)      (income/expense + Sched. C)   (file → row | hold → review)
```

- **Intake** (`backend/inbox`) — receipts arrive from a pluggable inbox behind one
  client seam: `MockInbox` (offline demo), a watched **Google Drive folder**, or a
  connected **Gmail mailbox** (backfill + incremental `history.list` sync).
- **Receipt filter** (`backend/receipts`) — a two-stage funnel (cheap heuristic →
  LLM only on the ambiguous band) drops non-receipts with minimal retention and
  contains prompt injection (email content is data, never instructions).
- **Extraction** (`backend/extraction`) — LLM pulls vendor / date / amount / line
  items with per-field confidence (real Anthropic provider or an offline stand-in).
- **Classify + categorize** (`backend/categorize`) — determines income vs. expense
  and assigns a Schedule C category with candidate alternates, so the reviewer can
  pick among candidates on a hold.
- **Decision** (`backend/decision`) — weakest-link confidence over extraction +
  income/expense + category; files only when confident (≥0.8), else holds with a
  typed reason. Adversarial-looking content is always held.
- **Ledger output** (`backend/clients/sheets.py`) — appends one row to a user-owned
  Google Sheet, gated by a Postgres `sheet_appends` dedup ledger so retries never
  double-post. The Sheet is a projection; Postgres is the source of truth.
- **Ledger integrity** (`backend/ledger_integrity`) — holds suspected duplicates,
  surfaces a held-count notification digest, and samples posted rows for spot-checks.
- **Reliability** (`backend/orchestrator`) — per-item failure isolation, bounded
  retry on transient Sheet-append errors, and `recover()` to resume a failed item.

The **reviewer hub** (`frontend/`) shows the list (status / decision / confidence /
exceptions + filters) and a detail view (extracted fields, classification &
category with candidate alternates, decision + risk flags, source overlay,
processing timeline) with QC actions: correct a category, resolve a hold, reject a
non-receipt, rerun, or retry. Observability: append-only audit events,
`/api/metrics`, `/api/notifications`, and `GET /api/invoices/:id/trace`.

## Demo walkthrough

`seed_hub` runs the sample documents through the ledger. The pipeline demonstrates
each terminal outcome:

| Scenario | Outcome |
|---|---|
| Clean, categorizable expense (vendor + amount + a recognizable line) | **Posted** — one row appended to the Sheet |
| Ambiguous category (e.g. office vs. supplies) | Held (low category confidence) — candidate categories shown |
| Can't tell income vs. expense | Held (ambiguous income/expense) |
| A second receipt for an already-filed charge | Held (suspected duplicate) — not double-posted |
| Injection-style content in the document | Held (suspected adversarial) |
| A newsletter / non-receipt (Gmail) | Dropped before extraction (minimal retention) |

Try the QC flow: open a held item → correct its category (or supply a missing
amount) → **Rerun** to re-decide and file; a failed item exposes **Retry failed
stage**; reject a non-receipt to drop it from the queue.

## Testing

```bash
pip install -r backend/requirements-dev.txt
ruff check .          # lint
pytest -q             # 248 passed, 1 skipped (Postgres round-trip; runs with a live DB)
```

Coverage spans every stage (unit), the orchestrator's state transitions +
retry/recovery, end-to-end ledger flows (auto-file, hold, sheet-write recovery,
duplicate holds, rerun idempotency), the receipt filter, the Gmail client/inbox,
and the Sheets client + dedup ledger.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — structure & decisions (ledger pipeline)
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup & run (all paths, incl. Gmail F / Sheets G)
- [`docs/gmail-sheets-setup.md`](docs/gmail-sheets-setup.md) — Gmail intake + Sheets ledger setup
- [`docs/drive-intake-setup.md`](docs/drive-intake-setup.md) — Google Drive folder intake setup
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — Google Cloud Run deployment
- [`docs/implementation-notes.md`](docs/implementation-notes.md) — running decisions log
- [`docs/PRD.md`](docs/PRD.md) · [`docs/STRATEGY.md`](docs/STRATEGY.md) · [`docs/USERS.md`](docs/USERS.md) — _being refreshed from the prior clinical-trial framing_
