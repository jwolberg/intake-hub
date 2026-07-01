---
date: 2026-07-01
topic: drive-folder-intake
---

# Google Drive Folder Intake

## Summary

Add a real invoice source: a Google Drive folder that acts as the AI's inbox.
An org points a Gmail filter at the folder so invoice attachments auto-land
there; our app polls the folder, downloads each new PDF, runs it through the
existing pipeline, and moves the file into a `/submitted`, `/needs-review`, or
`/failed` subfolder so Drive is self-documenting. It ships as a new
`InboxClient` implementation behind the existing seam — no pipeline changes.

---

## Problem Frame

Today the only invoice source is `MockInbox` (`backend/inbox/__init__.py`),
which replays curated sample JSON. Each mock message carries a structured
`document` block that feeds the *offline* extractor, so the system has never
ingested a real, un-curated invoice end-to-end from a live source. The
`CHALLENGE_ASSESSMENT.md` "ingest from email" requirement is therefore only
partially met: samples are email-shaped, but there is no live connection and no
real "obtain the PDF" step.

An org running this can't actually feed it work without a defined mechanism.
The cost is that "obtained and processed" is hand-waved — the very first step
of the workflow (getting an invoice into the system) has no real path, so the
tool can't be dropped into an operation as-is.

---

## Actors

- A1. **Ops / AP sender (org side):** Sets up a Gmail filter (or Apps Script)
  that auto-saves invoice attachments into the shared Drive folder. Does not
  touch the folder after setup.
- A2. **DriveInbox provider (our app):** Polls the folder, downloads new PDFs,
  hands each to the pipeline, and moves the file to a status subfolder.
- A3. **Invoice Operations Reviewer:** The existing primary persona — works
  held invoices in the reviewer hub. The hub stays the source of truth for
  ongoing review; Drive is a snapshot, not their workspace.

---

## Key Flows

- F1. **Invoice arrives and is processed from Drive**
  - **Trigger:** A new `.pdf` appears in the watched folder root (auto-forwarded
    from email), and the poller runs.
  - **Actors:** A2 (DriveInbox), then the existing pipeline.
  - **Steps:**
    1. DriveInbox lists the folder root for `.pdf` files not already seen.
    2. For each, downloads bytes to a local `attachment_path` and emits an
       `InboxMessage` with **no** `document` block.
    3. The orchestrator's real-PDF path parses text (`pypdf`), runs LLM
       extraction, and OCRs the original for evidence overlay.
    4. Pipeline reaches a terminal decision (auto-submit or hold), or errors.
    5. DriveInbox records the Drive fileId as seen, then moves the file into
       `/submitted`, `/needs-review`, or `/failed`.
  - **Outcome:** The invoice exists in the hub with a decision; the Drive folder
    reflects where it landed at decision time.
  - **Covered by:** R1, R2, R3, R4, R5, R7

- F2. **Reviewer resolves a held invoice**
  - **Trigger:** A3 opens a held invoice in the hub and corrects / reruns /
    escalates it.
  - **Actors:** A3, hub.
  - **Steps:** Reviewer acts entirely in the hub; the Drive file stays in
    `/needs-review` (known drift — see Assumptions).
  - **Outcome:** Hub state advances; Drive is not chased in v1.
  - **Covered by:** R6

---

## Requirements

**Ingestion**
- R1. Provide a `DriveInbox` implementing the existing `InboxClient` protocol
  (`fetch_messages() -> list[InboxMessage]`), selectable via `get_inbox_client()`
  by config; `MockInbox` remains the default.
- R2. For each new `.pdf` in the folder root, download it to a local
  `attachment_path` and emit an `InboxMessage` with the attachment path set and
  **no** `document` block, so the real OCR+LLM extraction path runs.
- R3. Only `.pdf` files in the folder **root** are pulled. Subfolders
  (`/submitted`, `/needs-review`, `/failed`) are never re-scanned. Non-PDF files
  in the root are ignored (left in place), not processed.

**Idempotency & lifecycle**
- R4. Record each Drive fileId via the existing `is_seen` / `mark_seen`
  mechanism as the authoritative dedup guard — so a crash or failure *between*
  processing and the file move cannot cause a second submission.
- R5. After the pipeline reaches a terminal state, move the file into a status
  subfolder: `/submitted` (auto-submitted), `/needs-review` (held for
  exceptions), or `/failed` (could not be processed — parse/extract/pipeline
  error). Create the subfolders on first use if absent.
- R6. The move reflects the AI's decision **at processing time only**. The app
  does not re-move a file when a reviewer later resolves it in the hub (v1).

**Access & trigger**
- R7. Authenticate with a Google service account that has editor access to the
  folder (edit is required to move files). Do not require an interactive
  end-user OAuth flow.
- R8. Trigger ingestion by polling via the existing `POST /api/inbox/fetch` +
  `backend/tools/inbox_poller`. No push/webhook infrastructure in v1.

**Operability**
- R9. Provide a short runbook for the org-side Gmail→Drive auto-forward setup
  and the service-account/folder-sharing steps. The auto-forward glue is **not**
  built by our app.

---

## Acceptance Examples

- AE1. **Covers R2, R5.** Given a clean, machine-readable invoice PDF in the
  folder root, when the poller runs, then it is extracted, auto-submitted, and
  moved to `/submitted`; the hub shows the invoice with a submit decision.
- AE2. **Covers R2, R5.** Given a valid invoice PDF whose context is ambiguous,
  when processed, then the pipeline holds it with exceptions and the file moves
  to `/needs-review`; the reviewer works it in the hub.
- AE3. **Covers R5.** Given an unreadable/corrupt PDF (no text layer, OCR
  fails), when processed, then it moves to `/failed` and does not create a
  bogus submitted invoice.
- AE4. **Covers R4.** Given a file that was processed but the subsequent move
  failed (or the app crashed before moving), when the poller runs again, then
  the file is skipped by fileId and is **not** submitted a second time.
- AE5. **Covers R3.** Given files already sitting in `/submitted` or `/failed`,
  when the poller runs, then they are not re-pulled or reprocessed.

---

## Success Criteria

- An org can wire a Gmail filter to a Drive folder and, with no further manual
  steps, see invoices arrive, get decided, and land in the correct status
  subfolder — the "obtain + process" step is now real, not simulated.
- A person glancing at the Drive folder can tell backlog (root) from handled
  (`/submitted`, `/needs-review`) from broken (`/failed`) without opening the hub.
- Re-running the poller never double-submits and never reprocesses a moved file.
- A downstream implementer can build this without inventing product behavior:
  the source, auth model, unit of work, lifecycle, and idempotency guard are all
  pinned here.

---

## Scope Boundaries

- **Live Drive↔hub sync (deferred):** the folder is a decision-time snapshot; a
  hold resolved in the hub does not move the Drive file. A cheap follow-on
  ("move on rerun/resolve") is possible later but out of v1.
- **Drive push notifications (deferred):** polling only; no watch channels /
  webhooks.
- **Multiple invoices per PDF (out):** one PDF = one invoice in v1.
- **Non-PDF attachments (out):** images/other formats in the root are ignored,
  not processed.
- **End-user OAuth connect flow (out):** service-account + shared folder only;
  no per-user Drive authorization UI.
- **Building the Gmail→Drive auto-forward (out):** that glue is org
  configuration, documented in the runbook, not implemented by our app.

---

## Key Decisions

- **Drive folder = the AI's inbox; humans never touch the root.** The
  auto-forward path chosen over "ops drops files manually" — the win is a truly
  hands-off inbox, at the cost of moving parts (Gmail filter) outside our app.
- **Three-way business split** (`/submitted` · `/needs-review` · `/failed`) over
  a technical handled-vs-couldn't split. Accepted tradeoff: review-relevant
  state is now visible in both Drive and the hub and can drift; the hub remains
  the single source of truth for ongoing review.
- **Moving files is the visible dedup, but fileId `is_seen` is authoritative**
  so the process→move gap can't double-submit.
- **Reuse over new:** slots into the existing `InboxClient` seam and the
  existing real-PDF orchestrator path — no changes downstream of intake.

---

## Dependencies / Assumptions

- Assumes the existing real-PDF path (`backend/parser/pdf.py`, `backend/ocr`,
  `backend/extraction`, orchestrator `attachment_path` branch) handles arbitrary
  forwarded PDFs. Verified present in code; **robustness on messy real-world
  scans is unproven** and is the top demo risk (see below).
- Assumes a Google Cloud service account is available and the target folder is
  shared to it with editor rights (consistent with the existing Cloud Run /
  `docs/DEPLOY.md` GCP footprint).
- Assumes org-side ability to configure a Gmail filter or Apps Script to route
  attachments into the folder.

---

## Outstanding Questions / Risks

- **Messy real PDFs:** scanned invoices with no text layer fall to OCR-only, and
  unusual layouts lower extraction confidence. Expected behavior is that low
  confidence → hold (caught by `/needs-review`), but this is the most likely
  live-demo surprise. Worth a deliberate "hard PDF" test before any demo.
- **Drift acknowledgement:** confirm ops are told the Drive subfolders are a
  decision-time snapshot, not a live queue, to avoid people "working" files in
  `/needs-review` on Drive instead of the hub.
