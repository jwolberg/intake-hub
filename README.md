# InvoiceScreener

An **AI-first** workflow for clinical-trial site invoices: it interprets an
emailed invoice, resolves its sponsor/study/site context, matches line items to
the sponsor+study catalog, and **decides to submit to ClinRun or hold for
exceptions — without a human gate before decisioning**. Reviewers then get a
clear *post-decision* QC hub to understand outcomes, validate risk cases, and
intervene (correct / rerun / escalate / retry).

See [`docs/challenge.md`](docs/challenge.md) for the brief and
[`docs/CHALLENGE_ASSESSMENT.md`](docs/CHALLENGE_ASSESSMENT.md) for a
requirement-by-requirement status.

## Quickstart (Docker, ~1 command)

```bash
docker compose up -d --build                           # db + api + hub + stubs
python -m backend.tools.seed_hub http://localhost:8000 # seed the demo invoices
# → API  http://localhost:8000  (/docs)
# → Hub  http://localhost:5173
```

Open the hub, click an invoice, and use **View source** (page image + the AI's
highlight boxes, plus **Open original PDF**). No API key or network is required —
the offline extractor handles the controlled sample PDFs. To use the real LLM
provider (model-derived confidence), export `ANTHROPIC_API_KEY` before `up` (see
[`docs/RUNBOOK.md`](docs/RUNBOOK.md)). Full local-dev paths (hot reload, tests,
real-PDF CLI) are in the RUNBOOK; cloud deploy is in [`docs/DEPLOY.md`](docs/DEPLOY.md).

## How it works

Email-shaped invoice → a staged pipeline, each stage a focused module behind the
orchestrator, which owns state transitions and writes an append-only audit trail:

```
intake → parser → extraction → context → catalog → matching → decision → submission
                     (LLM)      (MCP ref)  (scoped)   (+LLM tie-break)   (submit | hold→exceptions)
```

- **Extraction** (`backend/extraction`) — LLM pulls metadata + line items with
  per-field confidence (real Anthropic provider or an offline stand-in).
- **Context** (`backend/context`) — ranks sponsor/study/site candidates from the
  MCP-wrapped reference service; flags ambiguity and metadata mismatch.
- **Catalog + matching** (`backend/catalog`, `backend/matching`) — fetches the
  sponsor+study-scoped catalog and matches line items (exact/containment + price/
  qty checks, LLM adjudication for ambiguous candidates).
- **Decision** (`backend/decision`) — weakest-link confidence + severity policy;
  submits only when confident (≥0.8), else holds with typed exceptions. Low
  confidence never silently submits.
- **Reliability** (`backend/orchestrator`) — per-invoice failure isolation,
  bounded retry on transient catalog/submission errors, and `recover()` to resume
  a failed invoice from the failed stage.

The **reviewer hub** (`frontend/`) shows the list (status/decision/confidence/
exceptions + filters) and a detail view (extracted metadata, line-item matches,
decision + risk flags, submission status, source overlay, processing timeline)
with QC actions. Observability: append-only audit events, `/api/metrics`, and
`GET /api/invoices/:id/trace`.

## Demo walkthrough

`seed_hub` loads invoices spanning the key scenarios:

| Invoice | Scenario | Outcome |
|---|---|---|
| INV-1001 | clean, clear metadata | **Submitted** |
| INV-1002 | unmatched line item | Held (unmatched) |
| INV-1005 | sponsor vs protocol mismatch | Held (context mismatch) |
| INV-1006 | low-confidence line | Held (low confidence) |
| INV-1008 | ambiguous site (sibling tie) | Held (context ambiguity) |
| INV-1007 | large invoice vs larger catalog | **Submitted**, all matched |

Try the QC flow: open a held invoice → correct a field → **Rerun** to re-decide;
a failed invoice exposes **Retry failed stage**.

## Testing

```bash
pip install -r backend/requirements-dev.txt
ruff check .          # lint
pytest -q             # 168 passed, 1 skipped (Postgres round-trip; runs with a live DB)
```

Coverage spans every stage (unit), the orchestrator's state transitions +
retry/recovery, the eight PRD §18 scenarios (`tests/scenarios/`), and
stability-at-scale (`tests/integration/test_performance.py`).

## Documentation

- [`docs/PRD.md`](docs/PRD.md) — product requirements
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — structure & decisions
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — setup & run (all paths)
- [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) — phases, tickets, status
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — Google Cloud Run deployment
- [`docs/CHALLENGE_ASSESSMENT.md`](docs/CHALLENGE_ASSESSMENT.md) — requirement status
- [`docs/implementation-notes.md`](docs/implementation-notes.md) — running decisions log
