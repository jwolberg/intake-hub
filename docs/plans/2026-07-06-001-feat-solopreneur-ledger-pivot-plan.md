---
title: "feat: Solopreneur income/expense ledger pivot"
type: feat
status: completed
date: 2026-07-06
origin: docs/brainstorms/solopreneur-ledger-requirements.md
---

# feat: Solopreneur income/expense ledger pivot

## Summary

Repoint the existing staged pipeline from clinical-trial invoice submission to a
solopreneur email→income/expense→Google-Sheet ledger by **swapping the middle
stages and the terminal destination while reusing the engine**. The clinical-trial
context/catalog/matching stages and ClinRun submission are removed; a
receipt-detection filter, an income/expense + Schedule C categorization stage
(shaped like today's candidate-ranking matcher), and a Google Sheets append target
(backed by a Postgres idempotency ledger) take their place. Gmail joins the inbox
seam as the headline source alongside the retained Drive folder-watcher (its
internal validation path); the confidence-based auto-file/hold decision, audit
trail, and overlay-correction review surface carry over intact.

---

## Problem Frame

A solopreneur's receipts pile up unsorted in their email inbox; at tax time they
scramble to reconstruct a year of deductible expenses. The product already solved
the hard part — reading messy documents, extracting with confidence, deciding
autonomously whether to file, and leaving an audit trail — but aimed it at ClinRun
and wrapped it in clinical-trial machinery. This plan retargets that engine. Full
product framing, actors, flows, and acceptance examples live in the origin doc
(see Sources & References).

---

## Requirements

**De-clinicalization**
- R1. Remove ClinRun submission + mock ClinRun as the terminal step.
- R2. Remove/generalize the clinical-trial stages (sponsor/study/site context,
  sponsor+study catalog matching); no requirement depends on clinical-trial concepts.

**Ingestion**
- R3. Ingest without manual per-document entry; v1 ships connected email as the
  user-facing source plus the folder-watcher as an internal validation path.
- R11. Sources are extensible behind one seam; no source is privileged.
- R13. Detect receipt-vs-noise before a message becomes a ledger candidate.
- R14. Extract from inline email bodies (HTML/text) and attachments.
- R17. Minimal retention for non-receipt content; disclose full-inbox scan at connect.
- R20. Backfill inbox mail to Jan 1 of the current tax year on first connect, then watch forward.

**Extraction & classification**
- R4. Extract payee/vendor, date, total, (when present) currency + line items.
- R5. Classify each document income vs. expense.
- R6. Assign a Schedule C-style category with per-field confidence.

**Decisioning (reuse engine)**
- R7. Reuse confidence-based auto-file-vs-hold.
- R9. Explainable rationale + append-only audit per item.
- R15. Periodic spot-check sampling of already-posted rows.
- R16. Auto-file threat model includes spoofed/adversarial email content.

**Ledger output**
- R8. Append each filed item as a row to a user-owned Google Sheet (type-labeled, with
  category/vendor/date/amount + source link).
- R12. The Sheet is user-owned and portable, not locked in the app.

**Review & correction**
- R10. Post-decision review surface (default oldest-first, grouped by reason); shows
  candidate categories; correct field/category, confirm, or reject a non-receipt.
- R18. Notify the user when items are held.
- R19. Hold suspected duplicate/same-transaction messages rather than double-posting.

**Origin actors:** A1 (solopreneur / owner-reviewer), A2 (processing agent / the AI), A3 (ingestion sources).
**Origin flows:** F1 (connect the inbox), F2 (confident item auto-files), F3 (uncertain item held & resolved).
**Origin acceptance examples:** AE1 (covers R7/R8), AE2 (R6/R7/R10), AE3 (R5/R10), AE4 (R10), AE5 (R13), AE6 (R14).

---

## Scope Boundaries

### Deferred for later

*(carried from origin — product sequencing)*
- Additional ingestion adapters beyond connected email — forward-to-address, and
  surfacing the folder-watcher as a user-selectable source.
- User-defined / custom categories (v1 ships the fixed Schedule C set).
- Rich income-side handling (multiple income categories, client-invoice tracking).
- Direct accounting-tool sync (QuickBooks, Xero) or export formats beyond the Sheet.
- Recurring-vendor rules / learned auto-categorization from corrections.
- Non-email expense capture (cash, card-only, mileage, home-office) — reconciled outside the tool.
- Full automatic duplicate dedup/merge (v1 only *holds* suspected duplicates).

### Outside this product's identity

*(carried from origin — positioning)*
- A full accounting application (double-entry, AP/AR, reconciliation, statements).
- Small-business features (multi-user roles, approval chains, org accounts).
- A personal-finance / budgeting app (spending goals, net-worth, bank aggregation).

### Deferred to Follow-Up Work

*(plan-local — split out of this effort)*
- Full rewrite of PRD.md / STRATEGY.md / USERS.md for the new product — separate docs PR;
  this plan rewrites ARCHITECTURE.md and the RUNBOOK only.
- Serverless VPC connector + Cloud NAT to unblock Cloud Run→Anthropic egress (known
  limitation) — deploy-infra follow-up; v1 stays fallback-safe.
- Multi-tenant credential storage / isolation — v1 is single-tenant per origin Key Decisions.

---

## Context & Research

### Relevant Code and Patterns
- **Inbox provider seam** — `backend/inbox/__init__.py` (`InboxClient` Protocol, `MockInbox`,
  `get_inbox_client()` provider switch, `_build_drive_inbox()` factory, `message_to_sample()`),
  `backend/inbox/drive.py` (`DriveInbox`, `on_processed()` reflect-to-source hook). Add a `"gmail"`
  branch + `_build_gmail_inbox()` + `backend/inbox/gmail.py` following this exact shape.
- **External-client triad** — `backend/clients/drive.py` (`DriveClient` Protocol / `StubDriveClient` /
  `HttpDriveClient` with injectable `httpx.Client` + `token_provider`, lazy `google-auth`, typed
  `DriveClientError`). Mirror for new `GmailClient` and `SheetsClient` in `backend/clients/`.
- **Orchestrator surgery point** — `backend/orchestrator/__init__.py` `_resolve_match_decide()`:
  currently `resolve()`→`fetch()`+`match()`→`decide()`→`_submit()`. This is the tail to rewrite.
  `_retry()` (3 attempts, retryable vs deliberate-hold errors) is the pattern for Sheets-append retry.
- **Decision engine** — `backend/decision/__init__.py` (weakest-link `min()` confidence, `_DECISION_FLOOR`,
  severity-graded holds) + `backend/domain/taxonomy.py` (`HoldReason` registry). Reuse the engine; add codes.
- **Categorization analog** — `backend/matching/__init__.py` (candidate generation + `_is_ambiguous()` gap +
  `_adjudicate()` LLM fallback, `MatchResult.alternates`). The template for candidate-category assignment.
- **LLM layer** — `backend/clients/llm.py` (`LLMClient.complete_json`, `StubLLMClient`, `PassthroughLLMClient`,
  `AnthropicLLMClient`, `FallbackLLMClient`), `backend/extraction/__init__.py` `extract()`.
- **Audit + corrections** — `backend/audit/__init__.py` (`record()`, append-only, actor ∈ ai/system/human),
  `backend/corrections/__init__.py` (overlay-not-mutate). Reuse for R10 category override + R19 resolution.
- **Persistence** — `backend/db/repository.py` (`Repository` Protocol, `InMemoryRepository`, `PostgresRepository`),
  `backend/db/schema.sql`, `is_seen`/`mark_seen` idempotency at the API route in `backend/api/main.py`.
- **Frontend** — `frontend/src/api.js` (whole API surface), `frontend/src/InvoiceList.jsx` (status chips).
  `correctLineItem`'s catalog body is the direct analog for category correction.

### Institutional Learnings
- `docs/implementation-notes.md` "feat: Google Drive folder intake" — the inbox-provider + client-triad +
  `token_provider` seam + `StubDriveClient` offline-test convention this plan mirrors; also the
  `is_seen → process → mark_seen → on_processed` idempotency ordering.
- `docs/implementation-notes.md` LLM entries — `get_llm_client()` gates on key presence; `FallbackLLMClient`
  degrades on connection error only. Reuse for classification/categorization LLM calls.
- `docs/DEPLOY.md` known limitation — **Cloud Run egress to api.anthropic.com is blocked**; binding the key
  without a VPC connector + Cloud NAT is a regression. Plan stays fallback-safe (offline path handles it).
- Repo convention — Protocol+Stub+Http for every external dep; lazy-import heavy SDKs; fail-fast provider
  selection (no silent fallback); registry-driven hold taxonomy; overlay-not-mutate corrections; per-item
  failure isolation; run a "4-angle simplify pass" before code review.

### External References
- Gmail API: `messages.list` (`q=after:` backfill, `pageToken`), `history.list` (`startHistoryId` incremental,
  **404 → full resync** fallback), `messages.get` (`format=metadata` for the noise filter, `full` for survivors),
  `messages.attachments.get`; recursive MIME part-walker; base64url decode needs padding re-added. Scope
  `gmail.readonly` (restricted). Per-user quota ~6,000 units/min — well clear for one mailbox.
- Gmail auth: personal Gmail **cannot** use a service account (no domain-wide delegation). Use installed-app
  user-OAuth (`google-auth-oauthlib` one-time setup → refresh token; runtime uses `google.oauth2.credentials`).
  Consent screen kept **"In production" single-user** to avoid Testing-mode's 7-day refresh-token expiry, and
  scoped/private to avoid restricted-scope verification + annual CASA.
- Sheets API v4: `spreadsheets.values.append` (`USER_ENTERED`, `INSERT_ROWS`) — **no native idempotency**;
  `spreadsheets.create`/`batchUpdate` for one-time tab/header setup. Scope `drive.file` (app owns the sheet it
  creates) via a service account, mirroring today's Drive auth.
- Prompt injection (OWASP #1, 2026): email content is untrusted input to the LLM — structural separation
  (email = data, not instructions), narrow task scope, **no tool access from the LLM step**, and a
  code-controlled Sheets write keyed off structured output, not model free-text.

---

## Key Technical Decisions

- **Gmail via installed-app user-OAuth; Sheets via service account.** Personal-Gmail read is impossible with a
  service account; a refresh-token user-OAuth flow is required. A one-time interactive setup script mints the
  refresh token; the long-running process refreshes access tokens headlessly, wrapped in the existing
  `token_provider` seam. Sheets reuses today's service-account pattern (`drive.file`, the SA owns the ledger it
  creates) — one fewer user-consent dependency. Consent screen stays single-user "production" to dodge both the
  7-day Testing expiry and CASA.
- **Categorization is a new stage modeled on `matching`**, not a field folded into extraction: candidate
  categories + ambiguity gap + LLM adjudication, so it can surface alternates to the reviewer (R10/AE2) and feed
  the weakest-link confidence the decision engine already consumes.
- **Receipt detection is a pre-extraction filter** (two-stage: cheap heuristic → LLM only on the ambiguous band).
  Non-receipts are dropped with minimal retention (id/sender/subject/verdict only). The LLM call has **no tool
  access** and treats email text as data (prompt-injection containment, R16).
- **Postgres is the ledger of record; the Google Sheet is a durable projection.** A `sheet_appends` dedup table
  keyed by an idempotency key (Gmail message id + line index) gates every `values.append`, so retries/network
  blips never double-post. Satisfies R12 ("user owns their data in a Sheet") without making the Sheet the source
  of truth. The terminal audit event (`POSTED`) records the Sheet row reference the way `_submit()` records a
  ClinRun reference id today.
- **Reuse the decision engine + audit + overlay corrections unchanged in spirit.** New `HoldReason` codes
  (`ambiguous_income_expense`, `low_category_confidence`, `suspected_duplicate`, `suspected_adversarial`,
  `sheet_write_failed`) replace the sponsor/catalog codes; HIGH/MEDIUM/LOW blocking semantics and weakest-link
  confidence carry over. Category corrections + duplicate resolutions are audit overlays, not in-place edits.
- **State machine repoint:** `InvoiceStatus`/`AuditAction` `context_resolved`/`catalog_matched` → `classified`/
  `categorized`; `SUBMITTED` → `POSTED`. Removed-stage tables (`resolved_context`, `match_results`,
  `catalog_cache`) dropped; `line_items` kept.
- **Fallback-safe by construction.** Given the known Cloud Run→Anthropic egress block, all new LLM calls go
  through `FallbackLLMClient`; the offline path must produce a *hold* (not a crash) when it can't classify.
- **Keep `DriveInbox`.** It ships as the internal validation path proving classify→categorize→decide→Sheets
  without OAuth/receipt-detection/HTML — it is not removed.

---

## Open Questions

### Resolved During Planning
- Gmail auth model: installed-app user-OAuth, consent screen single-user "production" (see Key Decisions).
- Sheet structure: single tab with a `type` column (income/expense), satisfying R8's "labeling."
- Idempotency location: Postgres `sheet_appends`, not the Sheet.
- Categorization shape: matching-analog stage (candidates + adjudication).
- Receipt-detection placement: pre-extraction filter, two-stage funnel.

### Deferred to Implementation
- Canonical Schedule C category list source and whether categorization is document- or line-item-level.
- Receipt-detection heuristics + where its confidence bar sits relative to the auto-file bar (tune against samples).
- Whether spot-check sampling (R15) needs a dedicated table or a query over `status = posted`.
- `historyId` storage location (a settings row vs. a small sync-state table) and exact MIME edge shapes.
- Refresh-token encryption mechanism (env-key symmetric vs. KMS) — single-tenant, so keep proportionate.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.
> The implementing agent should treat it as context, not code to reproduce.*

```
INGESTION (InboxClient seam)                 PIPELINE (orchestrator tail, rewritten)
┌───────────────┐                            parse ─▶ extract(LLM) ─▶ classify+categorize(LLM, matching-analog)
│ GmailInbox    │─┐   receipt-detect filter          │                      │
│ (backfill +   │ │   (heuristic → LLM,        ┌──────┴───────┐             ▼
│  history sync)│ ├─▶ non-receipts dropped) ─▶ │ decide()     │        weakest-link
├───────────────┤ │                            │ (reused)     │        confidence
│ DriveInbox    │─┘                            └──────┬───────┘
│ (validation)  │                                     │ auto-file?
└───────────────┘                        ┌────────────┴────────────┐
                                         ▼ yes                     ▼ no / duplicate / adversarial
                             Sheets append  ◀── Postgres            hold → review surface (R10)
                             (projection)       sheet_appends       + notify (R18)
                                                (dedup, source        │
                                                 of truth)            └─ correct/confirm/reject (overlay)
                            POSTED audit event (row ref)        spot-check sampling of POSTED rows (R15)
```

---

## Implementation Units

Grouped into phases (see Phased Delivery). Phase 1 makes the folder-watcher validation path work end to end
(no Gmail); Phase 2 adds Gmail; Phase 3 adds trust/review integrity; Phase 4 is docs/deploy.

### U1. Domain reshape — enums, hold taxonomy, category/type models

**Goal:** Repoint the domain types for the ledger without behavior change: rename states, add hold codes, add
income/expense + category cells.

**Requirements:** R2, R5, R6

**Dependencies:** None

**Files:**
- Modify: `backend/domain/enums.py` (InvoiceStatus/AuditAction: `context_resolved`/`catalog_matched` → `classified`/`categorized`; `SUBMITTED` → `POSTED`), `backend/domain/taxonomy.py` (new HoldReason codes; remove sponsor/catalog codes), `backend/domain/models.py` (`document_type` income/expense + `category` as `{value, confidence, evidence}` cells)
- Test: `tests/unit/test_taxonomy.py` (new or extend)

**Approach:**
- Mirror the existing per-field annotated-cell convention; keep monetary `Decimal`.
- Removed codes stay removed — grep callers (`decide()`, `exceptions.build()`, API filter logic) and update in later units.

**Patterns to follow:** `backend/domain/taxonomy.py` HoldReason registry; existing `{value, confidence, evidence}` cells.

**Test scenarios:**
- Happy path: each new HoldReason code resolves to its severity/title/retryable via the registry.
- Edge case: a removed code is no longer registered (lookup raises/absent as designed).

**Verification:** Types import cleanly; taxonomy unit test enumerates the new codes with correct severities.

---

### U2. Classification + categorization stage

**Goal:** New stage that classifies income vs. expense and assigns a Schedule C category with per-field
confidence and alternates, modeled on the matcher.

**Requirements:** R4, R5, R6

**Dependencies:** U1

**Files:**
- Create: `backend/categorize/__init__.py` (stage fn), `tests/unit/test_categorize.py`
- Modify: `backend/extraction/__init__.py` if income/expense signal is best read alongside extraction fields
- Reference: a fixed Schedule C category list (module constant for v1)

**Approach:**
- Candidate categories → ambiguity gap → LLM `complete_json` adjudication fallback (mirror `matching`'s
  `_is_ambiguous()`/`_adjudicate()`), returning chosen category + `alternates` + confidence.
- LLM call is structured, single-task, no tool access (prompt-injection posture).
- Offline/`Passthrough`/`Fallback` path yields low confidence → hold, never a crash.

**Execution note:** Implement the confidence/adjudication branch test-first — it drives the hold-vs-file boundary.

**Patterns to follow:** `backend/matching/__init__.py` (candidate + adjudication + verify), `backend/extraction` LLM call shape.

**Test scenarios:**
- Covers AE2. Happy path: clear office-software charge → `expense` + a single high-confidence category.
- Covers AE2. Edge case: ambiguous category (Office vs. Supplies) → alternates returned, category confidence below bar.
- Covers AE3. Edge case: can't tell income vs. expense → low `document_type` confidence.
- Error path: LLM connection error → Fallback offline path returns low confidence (hold), not an exception.
- Integration: stage output feeds the decision engine's weakest-link `min()` correctly.

**Verification:** Given fixture documents, the stage yields the expected category/type/confidence and alternates; low-confidence cases mark the item hold-eligible.

---

### U3. Google Sheets output client + Postgres idempotency ledger

**Goal:** Append filed items to a user-owned Sheet, gated by a Postgres dedup ledger; record a `POSTED` audit event.

**Requirements:** R8, R12, R9

**Dependencies:** U1

**Files:**
- Create: `backend/clients/sheets.py` (`SheetsClient` Protocol / `StubSheetsClient` / `HttpSheetsClient`), `tests/unit/test_sheets_client.py`
- Modify: `backend/db/schema.sql` (add `sheet_appends(idempotency_key, sheet_row_ref, status)`), `backend/clients/errors.py` (typed `SheetsClientError`, retryable), `backend/audit` (POSTED event with row ref)
- Test: `tests/unit/test_sheets_ledger.py`

**Approach:**
- `values.append` `USER_ENTERED`/`INSERT_ROWS`; one row per filed item (type-labeled).
- Idempotency key = message/source id (+ line index); check `sheet_appends` before append; on success record row ref.
- Service-account auth via the existing `token_provider` seam; `drive.file` scope; SA creates the ledger on first use (`spreadsheets.create` + header `batchUpdate`).
- A persistent append failure is a retryable `sheet_write_failed` hold (mirrors `SubmissionFailed`), not a silent drop.

**Patterns to follow:** `backend/clients/drive.py` triad + token seam; `_retry()`; `_submit()` recording a reference id.

**Test scenarios:**
- Covers AE1. Happy path: a filed expense appends exactly one row with type/category/vendor/date/amount/source link.
- Edge case: retry of an already-appended item is a no-op (dedup by idempotency key) — no duplicate row.
- Error path: transient 5xx → `_retry()` retries; persistent failure → `sheet_write_failed` hold, item not lost.
- Integration: StubSheetsClient records appended rows; ledger table row created before the append call returns.

**Verification:** Append is idempotent under retry; Postgres holds the source-of-truth dedup record; a POSTED audit event carries the row reference.

---

### U4. Orchestrator surgery — repoint the pipeline tail

**Goal:** Rewire `_resolve_match_decide()` to `extract → classify+categorize → decide → Sheets-append-or-hold`,
removing context/catalog/matching calls.

**Requirements:** R2, R7, R8, R9, R16

**Dependencies:** U2, U3

**Files:**
- Modify: `backend/orchestrator/__init__.py` (`_resolve_match_decide` tail, `rerun()`/`recover()` reuse), `backend/decision/__init__.py` (consume categorize output instead of ResolvedContext/MatchResult), `backend/api/main.py` (`mark_seen` ordering vs. Sheets-write failure)
- Test: `tests/integration/test_orchestrator_ledger.py`

**Approach:**
- Decision inputs become extraction + categorize confidences; `catalog_available`-style gates drop out.
- Preserve `is_seen → process → mark_seen → on_processed`; a `sheet_write_failed` hold is retried in-process before `mark_seen`, so a failed write never reprocesses the source (documented reconsideration of the Drive-era ordering).
- Adversarial/suspicious signals from U2's classifier force a hold (R16).

**Execution note:** Start with a failing integration test asserting the auto-file happy path end-to-end through the new tail.

**Patterns to follow:** existing `_resolve_match_decide` structure; `_retry()`; the decision engine's hold branch.

**Test scenarios:**
- Covers AE1. Happy path: clean receipt → classified/categorized → auto-filed → Sheet row + POSTED audit.
- Covers AE3. Error/hold path: low-confidence type/category → held, no Sheet write.
- Integration: `rerun()`/`recover()` re-drive the new tail without touching removed stages.
- Edge case: Sheets-write failure holds the item and does not cause reprocessing on the next fetch.

**Verification:** End-to-end (via the folder-watcher/Mock path) an invoice flows to a Sheet row or a hold with correct audit; no references to removed stages remain in the tail.

---

### U5. Remove clinical-trial stages, ClinRun, stubs, tables, config

**Goal:** Delete the now-unused clinical-trial machinery once the new tail is in place.

**Requirements:** R1, R2

**Dependencies:** U4

**Files:**
- Delete: `backend/submission/`, `backend/context/`, `backend/catalog/`, `backend/matching/`, `backend/clients/clinrun.py`, `backend/clients/mcp_reference.py`, `backend/clients/stub_servers/clinrun_app.py`, `backend/clients/stub_servers/mcp_app.py`
- Modify: `backend/db/schema.sql` (drop `resolved_context`, `match_results`, `catalog_cache`), `backend/config.py` (drop `clinrun_url`, `mcp_reference_url`), `docker-compose.yml` (remove `mock-clinrun` + `mcp-reference` services), delete `tests/**` for removed modules
- Keep: `line_items` table + model.

**Approach:** Pure subtraction; run the suite to surface dangling imports/filter references and clean them.

**Test scenarios:** Test expectation: none — subtractive; correctness is "full suite still green after removal" (covered by U4's and existing tests).

**Verification:** `ruff` + `pytest` green; grep shows no live imports of removed modules; Compose stack boots without the removed services.

---

### U6. Receipt-detection filter stage

**Goal:** Classify is-this-a-receipt before extraction; drop non-receipts with minimal retention; contain prompt injection.

**Requirements:** R13, R16, R17

**Dependencies:** U1

**Files:**
- Create: `backend/receipts/__init__.py` (filter fn), `tests/unit/test_receipt_filter.py`
- Modify: `backend/orchestrator/__init__.py` (invoke filter at intake, before extract)

**Approach:**
- Two-stage: heuristic pre-filter (sender/subject/attachment signals) → LLM classifier only on the ambiguous band; structured, no tool access.
- Non-receipts: persist id/sender/subject/verdict/confidence only; discard body.
- The folder-watcher validation path can treat all inputs as receipts (curated), so this stage is email-facing.

**Test scenarios:**
- Covers AE5. Happy path: newsletter/personal email → non-receipt, ignored (no candidate, no hold).
- Happy path: obvious receipt (billing@ + PDF) passes the heuristic without an LLM call.
- Edge case: ambiguous sender/subject → LLM classifies; low confidence → held for review, not silently dropped.
- Error path: an email body containing injection-style instructions is treated as data; verdict unaffected, no tool/side-effect invoked.

**Verification:** Non-receipts are excluded with only metadata retained; ambiguous items reach review; injection attempts don't alter control flow.

---

### U7. Gmail client + user-OAuth token seam

**Goal:** A `GmailClient` that backfills, incrementally syncs, and fetches message bodies/attachments, authed by a stored refresh token.

**Requirements:** R3, R14, R20

**Dependencies:** U1

**Files:**
- Create: `backend/clients/gmail.py` (`GmailClient` Protocol / `StubGmailClient` / `HttpGmailClient`), a one-time OAuth setup script under `backend/tools/`, `tests/unit/test_gmail_client.py`
- Modify: `backend/clients/errors.py` (typed `GmailClientError`), `backend/requirements.txt` (`google-auth-oauthlib` for the setup script only)

**Approach:**
- Operations: `list(q=after:)` backfill + pagination; `history_list(startHistoryId)` incremental with **404 → full-resync** fallback; `get_message(format=metadata|full)`; `get_attachment`; a recursive MIME part-walker + base64url-with-padding decode.
- Runtime token via `google.oauth2.credentials.Credentials(...).refresh()` behind the existing `token_provider` seam; lazy imports; `gmail.readonly` scope.
- `StubGmailClient` seeds in-memory messages (inline HTML + attachment fixtures) for network-free tests.

**Execution note:** Characterization-first on the MIME part-walker against a few real-shaped fixtures — it's the classic pain point.

**Patterns to follow:** `backend/clients/drive.py` triad + `token_provider`; lazy `google-auth`.

**Test scenarios:**
- Covers AE6. Happy path: inline HTML receipt (no attachment) → vendor/date/total extracted from the body.
- Happy path: PDF-attachment receipt → attachment bytes fetched + decoded.
- Edge case: `history.list` returns 404 (stale historyId) → falls back to a full `messages.list` resync.
- Edge case: multipart/mixed nesting + missing base64 padding decode correctly.
- Error path: transient 429 → backoff/retry; typed `GmailClientError` on hard failure.

**Verification:** Against `StubGmailClient`, backfill + incremental paths yield the expected messages; body/attachment extraction handles inline-HTML and attachment shapes; 404 triggers resync.

---

### U8. Gmail inbox provider + encrypted token storage + backfill orchestration

**Goal:** Wire Gmail into the inbox seam as a selectable provider with backfill, incremental watch, and secure token storage.

**Requirements:** R3, R11, R17, R20

**Dependencies:** U7, U6

**Files:**
- Create: `backend/inbox/gmail.py` (`GmailInbox` implementing `InboxClient`)
- Modify: `backend/inbox/__init__.py` (`"gmail"` branch + `_build_gmail_inbox()`), `backend/config.py` (client id/secret, encrypted-token ref, historyId/label settings), `backend/db/schema.sql` (encrypted `oauth_tokens` + sync-state), `backend/api/main.py` or `backend/tools/` (a revoke path)
- Test: `tests/unit/test_gmail_inbox.py`, `tests/integration/test_inbox_fetch.py` (extend)

**Approach:**
- `fetch_messages()` runs backfill on first connect (to Jan 1 current tax year) then history-based deltas; persists latest `historyId`.
- `on_processed()` labels/archives the message (Gmail analog of the Drive move).
- Refresh token encrypted at rest (single env-key symmetric for single-tenant), decrypt-on-use; explicit revoke calls Google's revoke endpoint.
- Fail-fast on missing Gmail config (mirror `_build_drive_inbox`).

**Test scenarios:**
- Happy path: first fetch backfills the date range; subsequent fetch uses historyId deltas only.
- Edge case: connect discloses the full-inbox scan (R17) and non-receipts are minimally retained end-to-end.
- Error path: missing credentials → fail-fast `ValueError` at startup (no silent mock fallback).
- Integration: a seeded Gmail message flows through fetch → receipt-filter → pipeline → Sheet/hold; re-fetch is idempotent (`is_seen`).

**Verification:** `INBOX_PROVIDER=gmail` selects the provider; backfill+incremental work against the stub; tokens are never stored in cleartext; revoke path invalidates access.

---

### U9. Review-integrity: duplicate holds, hold notifications, spot-check sampling

**Goal:** Protect ledger trust after auto-file — hold suspected duplicates, notify on holds, and periodically resurface posted rows.

**Requirements:** R15, R18, R19

**Dependencies:** U3, U4

**Files:**
- Create: `backend/ledger_integrity/__init__.py` (dup-detection + sampling helpers), `tests/unit/test_ledger_integrity.py`
- Modify: `backend/orchestrator/__init__.py` (duplicate check before auto-file), `backend/api/main.py` (held-count/notification endpoint; sampling query), `backend/audit` (sampling event)

**Approach:**
- Duplicate detection keys off the Postgres dedup ledger + heuristics (same vendor/amount/date window); a suspected dup → `suspected_duplicate` hold, resolved via overlay (confirm/reject).
- Notification is a queryable held-count/digest surface (mechanism kept minimal; digest/badge deferred to impl).
- Spot-check surfaces a random sample of `POSTED` rows for optional confirmation.

**Test scenarios:**
- Happy path: order-confirmation + separate receipt for the same charge → second is held `suspected_duplicate`, not double-posted.
- Edge case: legitimately-similar-but-distinct charges (same vendor, different day/amount) are not held.
- Happy path: a held item increments the notification/held-count surface.
- Integration: spot-check sampling returns a bounded random subset of posted rows and records the sampling event.

**Verification:** Duplicates are held rather than double-counted; the held-count surface reflects reality; sampling returns posted rows for review.

---

### U10. Reviewer hub + API updates

**Goal:** Update the review surface and API for the ledger taxonomy: candidate categories, default ordering, category correction, and status/filter vocabulary.

**Requirements:** R10, R6, R18

**Dependencies:** U4, U9

**Files:**
- Modify: `backend/api/main.py` (filter/status vocab from new taxonomy; held-item ordering oldest-first/by-reason; category-correction route analog to catalog correction), `frontend/src/api.js` (`correctCategory`), `frontend/src/InvoiceList.jsx` + detail view (status chips, candidate-category display, held ordering)
- Test: `tests/integration/test_api.py` (extend)

**Approach:**
- Category correction reuses the overlay/audit shape of `correctLineItem` (category in place of catalog item).
- Detail view shows the chosen category + alternates (from U2) for held items; list orders held items oldest-first, grouped by flagged reason.

**Test scenarios:**
- Covers AE2. Integration: a held ambiguous item shows candidate categories; correcting the category posts it (overlay recorded, AI original preserved).
- Covers AE4. Integration: rejecting a non-receipt removes it from the queue and never writes a Sheet row.
- Happy path: list filters/status chips reflect the new taxonomy (no sponsor/catalog tags remain).
- Edge case: multiple held items render oldest-first, grouped by reason.

**Verification:** Reviewer can see candidates, correct a category, confirm, or reject; corrections are overlays; no clinical-trial vocabulary remains in the UI/API.

---

### U11. Docs, config, and deploy updates

**Goal:** Bring the architecture doc, runbook, env template, and Compose in line with the pivot.

**Requirements:** R3, R8, R17

**Dependencies:** U5, U8, U3

**Files:**
- Modify: `docs/ARCHITECTURE.md` (rewrite pipeline/module sections for the ledger), `docs/RUNBOOK.md` (add Path F — Gmail intake dev/test, Path G — Sheets ledger dev/test, mirroring Path E's offline-stub + real-credential structure), `.env.example`, `docker-compose.yml`, `docs/DEPLOY.md` (Gmail/Sheets secrets + reiterate the egress limitation)
- Create: a Gmail/Sheets setup doc mirroring `docs/drive-intake-setup.md`

**Approach:** Offline-stub instructions vs. real-credential instructions per the existing RUNBOOK convention; document the one-time OAuth setup script and the single-user "production" consent-screen requirement.

**Test scenarios:** Test expectation: none — docs/config. Validation is that the documented offline test paths run green and `docker compose config` is valid.

**Verification:** A new contributor can run the offline Gmail/Sheets test suites and the real-credential dev path from the RUNBOOK alone; ARCHITECTURE.md no longer describes ClinRun/sponsor/study.

---

## System-Wide Impact

- **Interaction graph:** The orchestrator tail (`_resolve_match_decide`, reused by `rerun`/`recover`) is the hub;
  the inbox seam (`get_inbox_client`, `on_processed`) and the API fetch route (`is_seen`/`mark_seen`) are the
  entry points. Decision engine, audit, and corrections are downstream consumers of the new stage outputs.
- **Error propagation:** Per-item isolation preserved — a bad message/attachment holds or fails that item only.
  Sheets-write failure is a retryable hold; LLM connection failure degrades to a hold via `FallbackLLMClient`.
- **State lifecycle risks:** Sheets append is non-idempotent → Postgres dedup gates it. `mark_seen`-before-
  `on_processed` re-examined so a failed Sheet write doesn't reprocess. Backfill can produce a held-item flood →
  notification + ordering absorb it. `historyId` staleness → 404 full-resync.
- **API surface parity:** Category correction mirrors line-item/catalog correction; filter/status vocabulary must
  change in both API and frontend together.
- **Unchanged invariants:** The `InboxClient` Protocol, the confidence-based decide/hold contract, the append-only
  audit, the overlay-not-mutate correction model, and the Protocol+Stub+Http client shape are all preserved — the
  pivot swaps stage bodies and the terminal destination, not these seams.

---

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Personal-Gmail restricted-scope verification / Testing-mode 7-day token expiry breaks the poller | High | High | User-OAuth with consent screen in single-user "production" mode (no CASA, no 7-day expiry); document the setup; monitor "reauth needed". |
| Prompt injection via email content manipulates classification/extraction | Med | High | Structural separation (email = data), narrow single-task LLM calls, **no tool access** from LLM steps, code-controlled Sheets write keyed off structured output (R16). |
| Cloud Run egress to Anthropic blocked → live LLM fails in deploy | High (known) | Med | All LLM via `FallbackLLMClient`; offline path holds rather than crashes; VPC/NAT fix deferred to follow-up. |
| Sheets `values.append` double-posts on retry | Med | High | Postgres `sheet_appends` dedup gates every append; Sheet is a projection, not source of truth. |
| Removing stages breaks the pipeline if mis-sequenced | Med | Med | Build the new tail (U2–U4) before deleting old stages (U5); full suite gates the removal. |
| Backfill floods the review queue on first connect | Med | Med | Hold notification (R18) + oldest-first/by-reason ordering (R10) absorb the batch. |
| `historyId` expiry causes missed mail | Low | Med | 404 → full-resync fallback built in from day one (U7). |
| Refresh token leak (keys to the mailbox) | Low | High | Encrypted at rest, minimal `gmail.readonly` scope, explicit revoke path (U8). |

---

## Phased Delivery

### Phase 1 — Core engine repoint (validated via the folder-watcher path)
U1, U2, U3, U4, U5. Proves classify→categorize→decide→Sheets end to end with no Gmail dependency — the
brainstorm's "internal validation path." Ends with the clinical-trial machinery removed and the suite green.

### Phase 2 — Gmail ingestion (headline source)
U6, U7, U8. Adds receipt detection, the Gmail client, and the Gmail provider with backfill + secure tokens.

### Phase 3 — Trust & review integrity
U9, U10. Duplicate holds, notifications, spot-check sampling, and the reviewer hub/API updates.

### Phase 4 — Docs & deploy
U11. Architecture rewrite, runbook paths, env/Compose, setup doc.

---

## Documentation / Operational Notes
- One-time Gmail OAuth setup script must run interactively (browser consent) before the poller can run headless;
  document it in the new setup doc and RUNBOOK Path F.
- Keep the OAuth consent screen single-user "production" — not "Testing" (7-day tokens), not public (CASA).
- Deploy stays fallback-safe; do not bind the Anthropic key in Cloud Run until the VPC connector + Cloud NAT
  follow-up lands (binding it without egress is a regression — see `docs/DEPLOY.md`).
- Non-blocking validation flags carried from origin (not gating this plan): the solopreneur beachhead lacks cited
  evidence, and full-inbox OAuth is a possible activation barrier — worth a real-user check during/after Phase 2.

---

## Sources & References
- **Origin document:** [docs/brainstorms/solopreneur-ledger-requirements.md](docs/brainstorms/solopreneur-ledger-requirements.md)
- Related prior work: Drive folder intake (`backend/inbox/drive.py`, `backend/clients/drive.py`, PR #1), poller sidecar (branch `feat/drive-monitoring`).
- Deploy constraint: `docs/DEPLOY.md` — "Known limitation: outbound egress to api.anthropic.com is blocked".
- External: Gmail API (messages/history/attachments), Google Sheets API v4 (values.append), google-auth installed-app OAuth, OWASP LLM prompt-injection guidance (2026).
