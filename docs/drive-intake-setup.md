# Google Drive Folder Intake — Setup

This is the org-side setup that turns a shared Google Drive folder into the AI's
inbox. The app **does not** build the Gmail→Drive glue or an OAuth connect flow;
it reads a folder it has been granted access to. Follow these steps to go from an
empty folder to processed invoices. (Design: `docs/plans/2026-07-01-001-feat-drive-folder-intake-plan.md`.)

## How it works

`INBOX_PROVIDER=drive` swaps the offline `MockInbox` for a `DriveInbox`. On each
`POST /api/inbox/fetch` (driven by `backend/tools/inbox_poller.py` or a scheduler)
the app:

1. Lists the **root** of the watched folder for `.pdf` files.
2. Downloads each new file and runs it through the normal pipeline (parse →
   extract → resolve → match → decide).
3. Moves the file into a status subfolder reflecting the **decision at processing
   time**:

   | Outcome | Subfolder |
   | --- | --- |
   | Auto-submitted | `submitted` |
   | Held for review | `needs-review` |
   | Unreadable / failed | `failed` |

Only the folder root is listed, so moved files are never re-scanned. Each file is
keyed by its stable Drive fileId, recorded as *seen* **before** the move — so a
crash or move failure can never cause a double-submit (re-fetch skips it).

> **The subfolders are a decision-time snapshot, not a live queue.** Reviewers
> work held invoices **in the hub**, which stays the source of truth. Resolving a
> hold in the hub does **not** move the Drive file back out of `needs-review`.

## 1. Create a service account

In the GCP project:

1. **IAM & Admin → Service Accounts → Create service account** (e.g.
   `intake-drive@<project>.iam.gserviceaccount.com`).
2. **Keys → Add key → JSON** and download the key file. This is the value you
   provide to the app as `GOOGLE_APPLICATION_CREDENTIALS`.

No project-level roles are needed — access is granted per-folder in the next step.

## 2. Share the Drive folder with the service account (Editor)

1. Create the watched folder in Drive (or pick an existing one).
2. **Share** it with the service account's email address, granting **Editor**.
   Editor (not Viewer) is required because the app **moves** processed files into
   the `submitted` / `needs-review` / `failed` subfolders.
3. Copy the folder id from its URL —
   `https://drive.google.com/drive/folders/<THIS_IS_THE_FOLDER_ID>` — for
   `DRIVE_FOLDER_ID`.

The app creates the three status subfolders automatically on first use.

## 3. Configure the app

Set these environment variables (see also `docs/RUNBOOK.md` § Environment
variables and `docs/DEPLOY.md` for the Cloud Run injection):

| Variable | Value |
| --- | --- |
| `INBOX_PROVIDER` | `drive` |
| `DRIVE_FOLDER_ID` | the folder id from step 2 |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to the JSON key file, **or** the key's inline JSON |
| `ANTHROPIC_API_KEY` | **required in practice** — see the caveat below |

Selecting `INBOX_PROVIDER=drive` without `DRIVE_FOLDER_ID` or
`GOOGLE_APPLICATION_CREDENTIALS` fails fast at startup rather than silently
serving the mock demo set.

> **Real PDFs need the LLM key.** The offline extraction stand-in only echoes a
> structured JSON document; it cannot read an arbitrary PDF's text layer. Live
> Drive invoices arrive as real PDFs with no structured block, so without
> `ANTHROPIC_API_KEY` set they extract to empty metadata (blank Vendor / Sponsor
> / Study) and hold. Set the key so extraction is genuinely model-derived. (The
> automated tests use controlled sample PDFs that the offline `LayoutLLMClient`
> can parse, which is why the suite stays network-free.)

Trigger a poll:

```bash
python -m backend.tools.inbox_poller http://127.0.0.1:8000
```

Re-running is idempotent — already-processed files were moved out of root and are
never reprocessed.

## 4. Org-side: auto-save invoice attachments into the folder

This glue lives **outside** the app — it is illustrative and not maintained here.
The simplest option is a Gmail filter plus a small Apps Script that saves matching
attachments into the watched folder:

```javascript
// Apps Script (script.google.com), run on a time-driven trigger.
// Illustrative only — tune the query and label to your mailbox.
function saveInvoiceAttachmentsToDrive() {
  const folder = DriveApp.getFolderById('DRIVE_FOLDER_ID');
  const threads = GmailApp.search('label:invoices has:attachment -label:filed');
  for (const thread of threads) {
    for (const message of thread.getMessages()) {
      for (const att of message.getAttachments()) {
        if (att.getContentType() === 'application/pdf') {
          folder.createFile(att.copyBlob());
        }
      }
    }
    thread.addLabel(GmailApp.getUserLabelByName('filed'));
  }
}
```

Alternatively, drop PDFs into the folder by hand, or use any tool that can write
to Drive. The app treats **any** new root-level `.pdf` as an invoice to process.

## What's out of scope (v1)

- Live Drive↔hub sync (a hold resolved in the hub does not move the Drive file).
- Drive push notifications / watch channels — polling only.
- Multiple invoices per PDF (one PDF = one invoice).
- Non-PDF attachments (ignored, not processed).
- An end-user OAuth connect flow (service-account + shared folder only).
