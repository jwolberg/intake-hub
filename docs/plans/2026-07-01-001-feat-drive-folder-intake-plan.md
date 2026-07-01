---
date: 2026-07-01
type: feat
status: active
title: "feat: Google Drive folder intake"
origin: docs/brainstorms/drive-folder-intake-requirements.md
depth: standard
---

# feat: Google Drive Folder Intake

## Summary

Add a real invoice source that treats a Google Drive folder as the AI's inbox.
A new `DriveInbox` implements the existing `InboxClient` protocol: it lists a
watched folder, downloads each new PDF to a local path, and emits an
`InboxMessage` with `attachment_path` set and **no** `document` block — so the
orchestrator's existing real-PDF path (text extract → LLM → OCR evidence) runs
unchanged. After each invoice reaches a decision, the file is moved into a
`submitted` / `needs-review` / `failed` subfolder so the folder is
self-documenting. Provider selection is env-driven; `MockInbox` stays the
default. (See origin: `docs/brainstorms/drive-folder-intake-requirements.md`.)

---

## Problem Frame

Today the only invoice source is `MockInbox` (`backend/inbox/__init__.py`), which
replays curated sample JSON carrying a structured `document` block that feeds the
*offline* extractor. The system has never ingested a real, un-curated invoice
from a live source — the `docs/CHALLENGE_ASSESSMENT.md` "ingest from email"
requirement is only partially met. An org cannot feed the tool real work without
a defined "obtain the PDF" mechanism. This plan supplies that mechanism using the
seam that was always reserved for it (OD-10 in `docs/BUILD_PLAN.md`).

The pipeline itself does not change: `backend/orchestrator/__init__.py` already
branches on `attachment_path` being a real `.pdf` (no `document` block) to run
`backend/parser/pdf.py` → `backend/extraction` → `backend/ocr` evidence
matching. The work is confined to a new source client and a small extension to
the inbox seam.

---

## Actors

- A1. **Ops / AP sender (org side):** Configures a Gmail filter / Apps Script that
  auto-saves invoice attachments into the shared Drive folder. Does not touch the
  folder afterward. Out of our code; covered by the runbook (U6).
- A2. **`DriveInbox` provider (our app):** Lists the folder, downloads new PDFs,
  hands each to the pipeline, and moves the file to a status subfolder.
- A3. **Invoice Operations Reviewer:** Existing primary persona — works held
  invoices in the hub. The hub stays the source of truth for ongoing review;
  Drive is a decision-time snapshot.

---

## Key Technical Decisions

- **KTD1 — Drive access via `google-auth` + httpx REST, not `google-api-python-client`.**
  Mint a service-account bearer token with `google-auth` and call the Drive v3
  REST API over the existing `httpx` dependency. This adds a single small,
  justified dependency, mirrors the existing `Protocol` + `Http…`/`Stub…` client
  pattern (`backend/clients/clinrun.py`, `backend/clients/mcp_reference.py`), and
  is imported lazily so the offline/default path never pulls it in — the same
  posture as `anthropic` (OD-2). (Confirmed with user.)
- **KTD2 — Post-decision move via an inbox-seam hook.** Extend the `InboxClient`
  protocol with an optional `on_processed(message, invoice)` hook, called by the
  fetch route *after* `mark_seen`. `MockInbox` implements it as a no-op;
  `DriveInbox` uses it to move the file. The move must not live inside
  `fetch_messages()` because the destination depends on the *decision*, which is
  only known after `process()`. (Confirmed with user.)
- **KTD3 — Idempotency ordering: `mark_seen` before move.** The fetch route
  already calls `repo.mark_seen()` immediately after `process()`; the new
  `on_processed` move runs after that. So a crash or move failure *after*
  processing cannot cause reprocessing — the fileId is already recorded as seen
  (satisfies R4 / AE4). Moving the file out of root is the visible dedup; the
  `seen_messages` record is the authoritative backstop.
- **KTD4 — Status→subfolder mapping.** `Decision.SUBMIT` → `submitted`;
  `InvoiceStatus.HELD` → `needs-review`; `InvoiceStatus.FAILED` (or an unexpected
  processing error) → `failed`. The move reflects the decision **at processing
  time only**; the app does not chase later hub actions (R6).
- **KTD5 — Local temp download works in cloud too.** Downloading each PDF to a
  local temp path and setting `attachment_path` works on Cloud Run (ephemeral
  fs); no need for the inline `attachment_b64` cloud path. Keeps `DriveInbox`
  uniform across dev and deploy.

---

## High-Level Technical Design

*This illustrates the intended approach and is directional guidance for review,
not implementation specification. The implementing agent should treat it as
context, not code to reproduce.*

```
POST /api/inbox/fetch
        │
        ▼
  inbox.fetch_messages()                      DriveInbox
        │  lists folder ROOT for *.pdf ─────► DriveClient.list_pdfs(folder_id)
        │  downloads each new file ─────────► DriveClient.download(file_id) → temp .pdf
        │  → InboxMessage(message_id=fileId, attachment_path=temp, document=None)
        ▼
  for each message:
     is_seen(fileId)?  ── yes ─► skip
        │ no
        ▼
     process(message_to_sample(message))      ← existing real-PDF pipeline, unchanged
        │
        ▼
     repo.mark_seen(fileId)                    ← idempotency committed BEFORE move (KTD3)
        │
        ▼
     inbox.on_processed(message, invoice)      MockInbox: no-op
                                               DriveInbox: DriveClient.move(fileId,
                                                 submitted | needs-review | failed)
```

Only the folder **root** is listed; the three status subfolders are never
re-scanned (they hold moved-out files).

---

## Requirements Traceability

Origin requirements (`docs/brainstorms/drive-folder-intake-requirements.md`) →
implementation units:

- R1 (`DriveInbox` behind `InboxClient`, env-selectable, mock default) → U2, U4
- R2 (download to `attachment_path`, no `document` block) → U2
- R3 (only root `.pdf` pulled; subfolders/non-PDF ignored) → U2
- R4 (fileId `is_seen` as authoritative dedup guard) → U3
- R5 (move to `submitted`/`needs-review`/`failed`) → U1, U3
- R6 (move = decision-time snapshot, no live sync) → U3 (documented behavior)
- R7 (service-account auth, editor rights, no OAuth flow) → U1, U4
- R8 (poll trigger via existing `/api/inbox/fetch` + poller) → U3 (reused as-is)
- R9 (runbook for Gmail→Drive + SA setup) → U6

---

## Implementation Units

### U1. Drive REST client (`google-auth` + httpx)

**Goal:** A `DriveClient` that lists, downloads, and moves files in a folder,
authenticated as a service account.

**Requirements:** R5, R7 · **Dependencies:** none

**Files:**
- `backend/clients/drive.py` (new) — `DriveClient` Protocol, `HttpDriveClient`
  (real), `StubDriveClient` (in-memory fake for tests)
- `backend/requirements.txt` (modify) — add `google-auth` with a lazy-import note
- `tests/unit/test_drive_client.py` (new)

**Approach:**
- Follow the `backend/clients/clinrun.py` pattern: a `Protocol` plus a real
  httpx-backed implementation and an in-memory stub. Methods:
  `list_pdfs(folder_id) -> list[DriveFile]` (id + name; root only, `.pdf` only,
  `mimeType != folder`), `download(file_id) -> bytes`, and
  `move(file_id, dest: str)` where `dest` is a status subfolder name.
- `HttpDriveClient` mints a token from a service-account credentials file/JSON via
  `google-auth` (lazy import), then calls Drive v3 REST endpoints over httpx:
  `files.list` (scoped `'<folder>' in parents and trashed=false`, PDF filter),
  `files.get?alt=media` for download, and `files.update` with
  `addParents`/`removeParents` for the move. Resolve/create the destination
  subfolder id once (create-if-absent).
- `StubDriveClient` holds an in-memory map of folder→files and records moves, so
  provider and route tests run with no network and no credentials.
- On Drive API errors, raise a typed error (mirror `backend/clients/errors.py`);
  do not crash the whole fetch loop (handled at U3).

**Patterns to follow:** `backend/clients/clinrun.py` (Protocol + Stub/Http +
typed error), `backend/clients/errors.py`, lazy-import posture of `anthropic` in
`backend/clients/llm.py`.

**Test scenarios:**
- `list_pdfs` returns only root-level `.pdf` files; excludes folders, non-PDF
  files, and files in subfolders. *Covers AE5 (indirectly — moved files live in
  subfolders and are not listed).*
- `download` returns the stored bytes for a known file id.
- `move` relocates a file to the named subfolder and creates the subfolder when
  it does not yet exist; a subsequent `list_pdfs` on root no longer returns it.
- `move`/`list`/`download` surface a typed error (not a raw exception) when the
  underlying client reports failure.
- Token minting is invoked lazily — constructing `HttpDriveClient` without
  exercising a call does not require live credentials (assert via injected
  auth/transport seam).

**Verification:** Unit tests pass against `StubDriveClient` and a mocked httpx
transport for `HttpDriveClient`; no live network or real credentials required.

---

### U2. `DriveInbox` provider (`fetch_messages`)

**Goal:** Pull new root PDFs from Drive and emit pipeline-ready `InboxMessage`s
that route through the real-PDF path.

**Requirements:** R1, R2, R3 · **Dependencies:** U1

**Files:**
- `backend/inbox/drive.py` (new) — `DriveInbox` implementing `InboxClient`
- `tests/unit/test_drive_inbox.py` (new)

**Approach:**
- `DriveInbox(client: DriveClient, folder_id: str, download_dir: Path)`.
- `fetch_messages()`: `client.list_pdfs(folder_id)`; for each file, download bytes
  and write to a temp `.pdf` under `download_dir`; build
  `InboxMessage(message_id=<Drive fileId>, subject=<file name>, sender=None,
  attachment=<file name>, attachment_path=<temp path>, document=None, body=None)`.
- `message_id` is the stable Drive fileId (idempotency key). No `document` block
  is set, which is what makes the orchestrator take the real-PDF branch
  (`backend/orchestrator/__init__.py`) rather than the offline document path.
- Do not filter `is_seen` here — that stays in the route (U3), consistent with
  `MockInbox` returning all messages and the route deduping.

**Patterns to follow:** `MockInbox` in `backend/inbox/__init__.py` (same
`fetch_messages` shape and `InboxMessage` construction), minus PDF *rendering*
(here we *download* a real PDF instead).

**Test scenarios:**
- `fetch_messages` returns one `InboxMessage` per root PDF, each with
  `message_id` == the Drive fileId, `attachment_path` pointing at a written temp
  `.pdf`, and `document is None`. *Covers AE1/AE2 setup (real-PDF branch).*
- The emitted message maps through `message_to_sample` to a sample with a
  `source` block and **no** `document` key (so the parser takes the PDF path).
- Empty folder → empty list (no error).
- A file that fails to download surfaces the typed error from U1 and does not
  yield a half-built message (the loop skips or raises per U3 handling).

**Verification:** With `StubDriveClient` seeded with sample PDF bytes,
`DriveInbox.fetch_messages()` produces messages that `message_to_sample` accepts
and that carry a readable `attachment_path`.

---

### U3. Post-decision move hook + fetch-route wiring

**Goal:** Move each processed file into the correct status subfolder after its
decision, without breaking `MockInbox` or the idempotency guarantee.

**Requirements:** R4, R5, R6, R8 · **Dependencies:** U1, U2

**Files:**
- `backend/inbox/__init__.py` (modify) — add `on_processed` to the `InboxClient`
  Protocol; give `MockInbox` a no-op implementation
- `backend/inbox/drive.py` (modify) — implement `DriveInbox.on_processed`
- `backend/api/main.py` (modify) — call `inbox.on_processed(message, invoice)`
  after `repo.mark_seen(...)` in `fetch_inbox`; harden the per-message loop so an
  unexpected processing error routes the file to `failed` and continues
- `tests/integration/test_inbox_fetch.py` (modify) — add DriveInbox-path coverage

**Approach:**
- Protocol gains `on_processed(self, message: InboxMessage, invoice: Invoice) ->
  None`. `MockInbox.on_processed` does nothing (no folder to reflect state in).
- `DriveInbox.on_processed` maps outcome → subfolder per KTD4
  (`Decision.SUBMIT` → `submitted`; `InvoiceStatus.HELD` → `needs-review`;
  `InvoiceStatus.FAILED` → `failed`) and calls `client.move(fileId, dest)`.
- Route change (`fetch_inbox`): keep the existing order — `is_seen` check →
  `process` → `mark_seen` → **new** `on_processed`. Wrap the per-message body so a
  raised pipeline error is caught, the file is moved to `failed` (best-effort),
  and the loop continues instead of aborting the whole fetch. Exact catch
  boundary and whether `process()` already returns a `FAILED` invoice vs. raises
  is an execution-time detail (see Deferred).

**Execution note:** Start from a failing integration test asserting the
submit/hold/failed files land in the right subfolder via `StubDriveClient`, then
wire the hook to make it pass.

**Patterns to follow:** existing `fetch_inbox` loop in `backend/api/main.py`
(is_seen/mark_seen ordering); dependency-injection seam `get_inbox()`.

**Test scenarios:**
- *Covers AE1.* A clean invoice PDF → auto-submitted → file moved to `submitted`;
  response row shows a submit decision.
- *Covers AE2.* An ambiguous-context invoice → held → file moved to
  `needs-review`.
- *Covers AE3.* An unreadable/corrupt PDF → not submitted; file moved to
  `failed`; the fetch loop still completes for other files.
- *Covers AE4.* A message whose `on_processed` move raises after `mark_seen`:
  re-running `fetch` skips it by fileId (`is_seen`) and does **not** submit twice.
- `MockInbox.on_processed` is a no-op — the existing mock fetch path is
  unchanged (regression guard on `tests/integration/test_inbox_fetch.py`).
- A file already moved to a status subfolder is not re-listed on the next fetch
  (*covers AE5*, via U1's root-only `list_pdfs`).

**Verification:** Integration test drives `/api/inbox/fetch` with a `DriveInbox`
built on `StubDriveClient`; asserts subfolder destinations, no double-submit on
re-fetch, and an unchanged MockInbox path.

---

### U4. Config + provider selection

**Goal:** Select the inbox provider and Drive settings from the environment;
default stays `MockInbox`.

**Requirements:** R1, R7 · **Dependencies:** U1, U2

**Files:**
- `backend/config.py` (modify) — add `inbox_provider`, `drive_folder_id`,
  `google_application_credentials` (path or inline JSON) to `Settings`
- `backend/inbox/__init__.py` (modify) — `get_inbox_client()` reads settings:
  `mock` (default) → `MockInbox`; `drive` → build `DriveInbox` + `HttpDriveClient`
- `tests/unit/test_inbox.py` (modify) — provider-selection coverage

**Approach:**
- Mirror the `Settings.from_env` pattern (plain `os.environ`, frozen dataclass,
  local-friendly defaults). New vars: `INBOX_PROVIDER=mock|drive` (default
  `mock`), `DRIVE_FOLDER_ID`, `GOOGLE_APPLICATION_CREDENTIALS`.
- `get_inbox_client()` branches on `settings.inbox_provider`. For `drive`,
  construct `HttpDriveClient` from the credentials + `DriveInbox` from the folder
  id; raise a clear config error if `drive` is selected but folder/credentials
  are missing (fail fast, don't silently fall back).

**Patterns to follow:** `Settings.from_env` in `backend/config.py`; the existing
`get_inbox_client()` default in `backend/inbox/__init__.py`.

**Test scenarios:**
- Unset / `INBOX_PROVIDER=mock` → `get_inbox_client()` returns a `MockInbox`
  (default preserved).
- `INBOX_PROVIDER=drive` with folder id + credentials set → returns a
  `DriveInbox` wired to an `HttpDriveClient` (assert type; do not hit network).
- `INBOX_PROVIDER=drive` with missing folder id or credentials → raises a clear
  configuration error, not a silent mock fallback.

**Verification:** Settings parse from a controlled env dict; provider selection
returns the expected type for each combination.

---

### U5. Sample real PDFs for the DriveInbox path

**Goal:** Exercisable, checked-in fixtures so the Drive path can be demoed and
tested end-to-end without a live Drive.

**Requirements:** supports R2, success criteria · **Dependencies:** U2

**Files:**
- `samples/pdf/` (reuse existing rendered PDFs) and/or a small fixture loader for
  `StubDriveClient`
- `backend/tools/inbox_poller.py` (verify only — no change expected)

**Approach:**
- Reuse the existing `samples/generate_pdfs.py` output as the byte source for
  `StubDriveClient` in tests and for a local demo (seed the stub folder with a
  clean, a hold, and an unreadable/garbage PDF to exercise all three
  destinations).
- Confirm the existing `backend/tools/inbox_poller.py` drives the Drive provider
  unchanged (it only calls `POST /api/inbox/fetch`).

**Test expectation:** none beyond what U2/U3 cover — this unit is fixtures/wiring.
If a garbage-PDF fixture does not already exist, add one so AE3 has real bytes to
run against.

**Verification:** Local run with `INBOX_PROVIDER=drive` against a stub-backed or
test folder pulls, processes, and files the three fixtures into their subfolders.

---

### U6. Setup runbook (Gmail→Drive + service account)

**Goal:** Document the org-side setup our app does *not* build, so the workflow is
reproducible.

**Requirements:** R9 · **Dependencies:** U4

**Files:**
- `docs/drive-intake-setup.md` (new), linked from `docs/RUNBOOK.md`

**Approach:**
- Cover: (1) create a GCP service account, share the Drive folder to its email
  with **Editor** (needed for moves); (2) provide credentials to the app
  (`GOOGLE_APPLICATION_CREDENTIALS`); (3) set `INBOX_PROVIDER=drive` +
  `DRIVE_FOLDER_ID`; (4) org-side Gmail filter / Apps Script to auto-save invoice
  attachments into the folder (example snippet, framed as illustrative — not
  maintained by this app); (5) note the `submitted`/`needs-review`/`failed`
  subfolders are a **decision-time snapshot**, not a live queue — reviewers work
  holds in the hub, not in Drive (R6, and the origin drift note).

**Test expectation:** none — documentation.

**Verification:** A reader can go from an empty folder to a processed invoice by
following the steps; the drift caveat is explicit.

---

## Scope Boundaries

### Carried from origin — Deferred for later
- Live Drive↔hub sync (a hold resolved in the hub does not chase the Drive file);
  optional "move on rerun" is a follow-on.
- Drive push notifications / watch channels — polling only.
- Multiple invoices per PDF (one PDF = one invoice).
- Non-PDF attachments (ignored, not processed).
- End-user OAuth connect flow (service-account + shared folder only).
- Building the Gmail→Drive auto-forward glue (org config; documented in U6).

### Deferred to Follow-Up Work (plan-local)
- Reconciliation of files that are `is_seen` but stranded in root (marked seen,
  move failed) — acceptable in v1 (no double-submit); a sweeper could re-move
  them later.
- Retry/backoff policy for Drive API transient failures beyond the existing
  per-message isolation.

---

## System-Wide Impact

- **Inbox seam:** `InboxClient` gains one optional method (`on_processed`);
  `MockInbox` and the `fetch_inbox` route are the only existing implementors and
  both are updated in-plan. No other pipeline stage changes.
- **Config surface:** three new env vars (`INBOX_PROVIDER`, `DRIVE_FOLDER_ID`,
  `GOOGLE_APPLICATION_CREDENTIALS`); all default to today's mock behavior.
- **Dependencies:** one new lazily-imported dep (`google-auth`); offline/default
  path unaffected.
- **Deploy:** Cloud Run needs the service-account credentials and folder id set
  (extends `docs/DEPLOY.md`); no schema change (`seen_messages` already exists).

---

## Risks & Mitigations

- **Messy real PDFs** (scans without a text layer, odd layouts) extract with lower
  confidence. *Mitigation:* expected behavior — low confidence holds → filed to
  `needs-review`; AE3 covers the unreadable case → `failed`. Add a deliberate
  "hard PDF" fixture (U5) and test before any live demo. *(Top demo risk per
  origin.)*
- **Move-after-`mark_seen` stranding** (seen but not moved). *Mitigation:*
  accepted in v1 — guarantees no double-submit (KTD3); reconciliation deferred.
- **Credential/config misconfiguration.** *Mitigation:* fail fast in
  `get_inbox_client()` when `drive` is selected without folder/credentials (U4).

---

## Deferred to Implementation (execution-time unknowns)

- Whether `process()` returns a `FAILED` invoice or raises on an unreadable PDF —
  determines the exact catch boundary in `fetch_inbox` (U3). Inspect at
  implementation time and handle both.
- Exact Drive v3 request/response field selection (`fields=` masks, page-size
  handling if a folder ever exceeds one page).
- Final helper/method names in `DriveClient`.
- Whether the garbage-PDF fixture already exists in `samples/` or must be added
  (U5).

---

## Verification Strategy

- `pytest` unit + integration green, including the new Drive client, provider,
  and route tests; the existing `MockInbox` fetch path unchanged.
- `ruff` clean on changed files (per project validation rules).
- Manual: `INBOX_PROVIDER=drive` local run against a stub/test folder pulls,
  processes, and files a clean / hold / unreadable fixture into
  `submitted` / `needs-review` / `failed`; a second fetch double-processes
  nothing.
