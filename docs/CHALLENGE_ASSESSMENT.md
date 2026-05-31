# Challenge Assessment

Assessment of the current implementation against the requirements in
[`challenge.md`](./challenge.md). Each requirement is rated **Done / Partial /
Not done** with the reason. Dated **2026-05-31**; verified against the code
(stages, tests, samples, clients, hub, Cloud Run deploy).

**TL;DR:** the end-to-end AI pipeline + post-decision QC hub is complete and
tested (Phases 0–2, 4, 5). The open gaps are the hardening items (scale demos,
automatic retry/resume) that map to the unbuilt Phase 3, plus a few "letter vs
intent" notes (simulated email intake, HTTP stand-in for MCP, rule-based
resolution/decisioning).

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
| 8 | Demonstrate easy / ambiguous / mismatched scenarios | **Done** | Samples cover easy (`inv_clean_001`→submit), ambiguous/low-confidence (`inv_uncertain_006`→hold@60%), mismatched metadata (`inv_hold_mismatch_005`→sponsor mismatch hold), unmatched line (`inv_hold_unmatched_002`); integration tests assert each. |

## Performance Benchmarks

| # | Requirement | Rating | Reason |
|---|---|---|---|
| 1 | Stable on simple/medium/large/mismatched samples | **Partial** | Robust on the provided set incl. mismatched; failures are isolated per-invoice. But there is **no "large" sample** — all samples are small (≤4 line items), so "large" is not actually demonstrated. |
| 2 | Reliable handling of larger catalog matching | **Partial** | Matching is an O(catalog) linear scan + optional LLM tie-break, so it *should* scale, but fixture catalogs are only 4–6 items and there is **no large-catalog test**. Not demonstrated at scale. |
| 3 | Responsive reviewer interaction (read/review) | **Done** | Hub does instant client-side filtering over server-computed `filter_tags`; read/review are simple API calls. Not formally load-tested, but interaction is lightweight and responsive. |
| 4 | Predictable retry/recovery on stage failure / low-confidence | **Partial** | Low-confidence→hold is solid (0.8 floor, weakest-link confidence, `low_confidence` flag). Stage failures are isolated, classified retryable (`catalog_fetch_failed`/`submission_failed`) vs terminal, never propagate, and a manual **rerun** re-enters. But there is **no automatic bounded retry/backoff or resume-from-failed-stage** (planned P3-T1 is unbuilt). |

## Code Quality Expectations

| Item | Rating | Reason |
|---|---|---|
| Modular staged architecture | **Done** | Distinct stage modules (intake/parser/extraction/context/catalog/matching/decision/submission/exceptions/audit) behind an orchestrator that owns state transitions. |
| Separation of extraction/matching/decisioning | **Done** | Each is its own module with typed inputs/outputs; clients (LLM/MCP/ClinRun/OCR) isolated behind protocols. |
| Test coverage for core **and** exception paths | **Done** | 154 passed / 1 skipped; exception paths covered (unmatched, mismatch, context-mismatch, hold reasons, failure isolation). The skipped test is the Postgres round-trip (runs with a live DB). |
| Observable workflow state transitions | **Done** | Append-only `audit_events` record every transition + AI-vs-human attribution; explicit status machine; `/api/metrics`; stage trace in the `process_pdf` CLI. |

## Technology / Dev Tools

| Item | Rating | Reason |
|---|---|---|
| Docker Compose | **Done** | Full stack (`db`, `api`, `hub`, `mcp-reference`, `mock-clinrun`) + dev override. |
| Git | **Done** | Per-ticket commit history on `main`. |
| Automated tests for core pipeline | **Done** | pytest suite as above; ruff lint clean. |
| LLM for extraction / entity resolution / matching / decisioning | **Partial** | LLM is genuinely used for **extraction** and **match adjudication**. **Entity resolution** and **decisioning** are deterministic (candidate ranking + a policy/confidence table), not LLM-driven — a reasonable design choice, but not literally "LLM for" all four. Cloud also runs offline (the lab project blocks egress to `api.anthropic.com`), so the real LLM is exercised locally, not in the deployment. |

## Gaps to close (everything else is solid)

1. **Scale isn't demonstrated** (PB1/PB2) — no large invoice or large catalog
   sample/test. Cheapest fix: a generated large-catalog + many-line-item sample
   plus a test asserting it processes without failure.
2. **No automatic retry/resume** (PB4) — failure isolation + manual rerun exist;
   bounded retries/resume-from-stage (planned P3-T1) do not.
3. **Email ingestion is simulated** (FR1) — acceptable for the hackathon
   ("local-first acceptable"), but not a real mailbox connection.
4. **"MCP" is an HTTP stand-in** (FR3) and **resolution/decisioning are
   rule-based, not LLM** (tech list) — defensible design choices; worth stating
   explicitly in the writeup.

These map cleanly to the **unbuilt Phase 3** (hardening/scale/retry) in
[`BUILD_PLAN.md`](./BUILD_PLAN.md); Phases 0–2, 4, and 5 are complete.
