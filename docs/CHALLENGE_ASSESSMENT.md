# Challenge Assessment

Assessment of the current implementation against the requirements in
[`challenge.md`](./challenge.md). Each requirement is rated **Done / Partial /
Not done** with the reason. Dated **2026-05-31** (updated after Phase 3); verified
against the code (stages, tests, samples, clients, hub, Cloud Run deploy).

**TL;DR:** the end-to-end AI pipeline + post-decision QC hub is complete and
tested across **all phases (0–5)**. Phase 3 closed the prior hardening gaps —
scale demos (large invoice + larger catalog), automatic retry/recovery, the
eight-scenario suite, and trace observability are now done. The only remaining
items are deliberate "letter vs intent" design choices: simulated email intake,
an HTTP stand-in for MCP, and rule-based (not LLM) entity resolution/decisioning.

## Functional Requirements

| # | Requirement | Rating | Reason |
|---|---|---|---|
| 1 | Ingest from email + process provided samples end-to-end | **Partial** | End-to-end processing of the provided samples is fully done (`/api/invoices/process` → all stages → terminal state). But "from email" is **simulated**: `intake.ingest` reads `source.channel`; there is no mailbox/IMAP poller. Samples are email-shaped (sender/subject/attachment) but there is no live email connection. |
| 2 | Extract metadata + line items via LLM | **Done** | `backend/extraction` validates LLM JSON into typed models with per-field confidence; real `AnthropicLLMClient` (key from Secret Manager) + offline stand-ins. Prompt pins exact schema keys. |
| 3 | Resolve sponsor/study/site via MCP-wrapped reference API | **Done\*** | `context.resolve` picks/clamps the best candidate via `MCPReferenceClient`; a dedicated `mcp-reference` service + client interface model the boundary. **\*Caveat:** that service is a plain **HTTP FastAPI stub**, not the literal MCP wire protocol — functionally correct and cleanly abstracted, but not "MCP" on the wire. |
| 4 | Fetch sponsor+study-scoped catalog and match line items | **Done** | `catalog.fetch` scopes by resolved sponsor+study; `matching.match` does normalize + exact/containment + price/qty checks, with **LLM adjudication** for ambiguous candidates; unmatched flagged. |
| 5 | AI decision: submit vs hold for exceptions | **Done** | `decision.decide` (severity policy + weakest-link confidence, 0.8 submit floor) → `submission.submit_invoice` to ClinRun or typed `exceptions`. |
| 6 | Hub shows extracted / matched / confidence-exceptions / submitted-withheld | **Done** | `InvoiceList` (vendor, sponsor/study, decision, status, confidence, exception count, filter chips) + `InvoiceDetail` (metadata value/confidence/evidence, line-item matches + rationale/flags, decision + risk flags, submission status). |
| 7 | Human QC after decisioning (review, corrections, rerun/escalation) | **Done** | Endpoints + hub for correct-metadata, correct-line-item, mark-reviewed, escalate, note, citation-confirm, and **rerun** (re-enters the pipeline with corrections as fixed inputs). All recorded as human audit events. |
| 8 | Demonstrate easy / ambiguous / mismatched scenarios | **Done** | Samples cover easy (`inv_clean_001`→submit), **ambiguous context** (`inv_ambiguous_008`→sibling-site tie hold), low-confidence (`inv_uncertain_006`→hold@60%), mismatched metadata (`inv_hold_mismatch_005`→hold), unmatched line (`inv_hold_unmatched_002`), and large (`inv_large_007`). The eight-scenario suite (`tests/scenarios/`) asserts each PRD §18 scenario end-to-end. |

## Performance Benchmarks

| # | Requirement | Rating | Reason |
|---|---|---|---|
| 1 | Stable on simple/medium/large/mismatched samples | **Done** | Now includes a **large** sample (`inv_large_007`, 15 line items) plus a stability-at-scale test (`tests/integration/test_performance.py`: 80-line invoice vs 250-item catalog → fully-matched submit, no failure). The eight-scenario suite (`tests/scenarios/`) covers simple/medium/large/mismatched explicitly. |
| 2 | Reliable handling of larger catalog matching | **Done** | Demo catalog grew to a generated 60-item set (sponsor_003/study_003) and the perf test matches against a 250-item catalog without workflow failure (P3-T5/T6). |
| 3 | Responsive reviewer interaction (read/review) | **Done** | Hub does instant client-side filtering over server-computed `filter_tags`; read/review are simple API calls. Not formally load-tested, but interaction is lightweight and responsive. |
| 4 | Predictable retry/recovery on stage failure / low-confidence | **Done** | Low-confidence→hold (0.8 floor, weakest-link confidence). Failures are isolated and **typed/retryable** (`is_retryable` taxonomy); transient catalog/submission errors get a **bounded in-process retry**; `recover()` + `POST /…/retry` (+ a "Retry failed stage" hub action) **resume a failed invoice from the failed stage** (reuse extraction, or re-extract on extraction failure). Tested: transient self-heal, recover-without-reextract, extraction-fail-then-recover (P3-T1). |

## Code Quality Expectations

| Item | Rating | Reason |
|---|---|---|
| Modular staged architecture | **Done** | Distinct stage modules (intake/parser/extraction/context/catalog/matching/decision/submission/exceptions/audit) behind an orchestrator that owns state transitions. |
| Separation of extraction/matching/decisioning | **Done** | Each is its own module with typed inputs/outputs; clients (LLM/MCP/ClinRun/OCR) isolated behind protocols. |
| Test coverage for core **and** exception paths | **Done** | 168 passed / 1 skipped, incl. a dedicated eight-scenario suite and scale + retry/recovery tests; exception paths covered (unmatched, mismatch, context-mismatch, hold reasons, failure isolation, extraction/catalog/submission failures). The skipped test is the Postgres round-trip (runs with a live DB). |
| Observable workflow state transitions | **Done** | Append-only `audit_events` record every transition + AI-vs-human attribution; explicit status machine; the hub's Processing timeline; `/api/metrics`; and `GET /api/invoices/:id/trace` reconstructing the ordered stage path with typed, `retryable` errors (P3-T4). |

## Technology / Dev Tools

| Item | Rating | Reason |
|---|---|---|
| Docker Compose | **Done** | Full stack (`db`, `api`, `hub`, `mcp-reference`, `mock-clinrun`) + dev override. |
| Git | **Done** | Per-ticket commit history on `main`. |
| Automated tests for core pipeline | **Done** | pytest suite as above; ruff lint clean. |
| LLM for extraction / entity resolution / matching / decisioning | **Partial** | LLM is genuinely used for **extraction** and **match adjudication**. **Entity resolution** and **decisioning** are deterministic (candidate ranking + a policy/confidence table), not LLM-driven — a reasonable design choice, but not literally "LLM for" all four. Cloud also runs offline (the lab project blocks egress to `api.anthropic.com`), so the real LLM is exercised locally, not in the deployment. |

## Closed by Phase 3

- ✅ **Scale demonstrated** (PB1/PB2) — `inv_large_007` + a 60-item demo catalog
  and a 250-item perf test.
- ✅ **Automatic retry / resume** (PB4) — bounded transient retry + `recover()` /
  `/retry` resume-from-failed-stage.
- ✅ **Scenario coverage** (FR8, §18) — eight-scenario suite incl. ambiguity,
  failed catalog, and failed submission.
- ✅ **Trace observability** — `GET /api/invoices/:id/trace`.

## Remaining (deliberate design choices, not gaps to "finish")

1. **Email ingestion is simulated** (FR1) — acceptable for the hackathon
   ("local-first acceptable"), but not a real mailbox connection.
2. **"MCP" is an HTTP stand-in** (FR3) and **entity resolution / decisioning are
   rule-based, not LLM** (tech list) — defensible choices behind clean interfaces;
   swapping in a real MCP server or LLM-driven resolution is a contained change.
3. **Cloud runs offline** — the lab project blocks egress to `api.anthropic.com`,
   so the deployed app uses the offline extractor; the key is in Secret Manager,
   ready to bind once egress (VPC connector + Cloud NAT) exists.

All build-plan phases (0–5) are complete — see [`BUILD_PLAN.md`](./BUILD_PLAN.md).
