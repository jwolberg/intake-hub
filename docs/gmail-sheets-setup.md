# Gmail Intake + Google Sheets Ledger — Setup

This is the setup for the headline ingestion path (a connected Gmail mailbox) and
the ledger output (a user-owned Google Sheet). Gmail is read with **installed-app
user-OAuth** — a personal mailbox **cannot** be read by a service account (no
domain-wide delegation), so a one-time interactive consent mints a refresh token
the long-running process reuses headlessly. Sheets output uses a **service
account** (the same pattern as Drive intake — see
[`drive-intake-setup.md`](./drive-intake-setup.md)). Once configured, the Compose
poller (or a Cloud Scheduler job) watches the mailbox for you — see
[`RUNBOOK.md`](./RUNBOOK.md) Paths F & G and [`DEPLOY.md`](./DEPLOY.md).
(Design: `docs/plans/2026-07-06-001-feat-solopreneur-ledger-pivot-plan.md`.)

## How it works

`INBOX_PROVIDER=gmail` swaps the offline `MockInbox` for a `GmailInbox`. On each
`POST /api/inbox/fetch`:

1. **First connect** (no stored `historyId`): a full backfill of messages received
   since **Jan 1 of the configured tax year** (`GMAIL_TAX_YEAR`), paginated.
2. **Subsequent fetches**: incremental `history.list` deltas from the stored
   `historyId`; a stale id (Gmail retains history ~1 week) falls back to a full
   backfill rather than silently missing mail.
3. Each candidate is cheaply classified **is-this-a-receipt** *before* its body is
   fetched. Confident non-receipts (newsletters, personal mail) are **dropped**
   with only `id / sender / subject / verdict` retained — the body is never
   fetched, let alone stored (minimal retention). Receipts (and ambiguous items,
   which flow on to be held) are fetched in full and run through the pipeline:
   parse → extract → classify+categorize → decide → **append to the Sheet or
   hold**.

> **The mailbox is read-only.** The app requests only the `gmail.readonly` scope,
> so a processed message is **not** labeled or archived in Gmail — the hub is the
> source of truth for what was posted vs held. (Labeling would require the broader
> `gmail.modify` scope, deliberately not requested for an MVP.)

Filed items append one row to the Sheet (`Type, Category, Vendor, Date, Amount,
Source`). A Postgres `sheet_appends` table is the source-of-truth **dedup gate**
keyed by the item id, so a retry or a network blip never double-posts — the Sheet
is a durable *projection*, not the ledger of record.

## 1. Create the Gmail OAuth client (installed app)

In the GCP project:

1. **APIs & Services → Enable APIs** → enable the **Gmail API**.
2. **APIs & Services → OAuth consent screen**: set it to **"In production"** for a
   **single user** (yourself). Do **not** leave it in *Testing* mode — Testing
   refresh tokens expire after 7 days and the poller would stop. Keeping it private
   and single-user also avoids restricted-scope verification and the annual CASA
   security assessment.
3. Add the scope `https://www.googleapis.com/auth/gmail.readonly`.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download
   the client-secret JSON. Note the **client id** and **client secret**.

## 2. Mint a refresh token (one-time, interactive)

Run the setup script with the client-secret JSON; it opens a browser for consent
and prints a refresh token:

```bash
python -m backend.tools.gmail_oauth_setup /path/to/client_secret.json
# → complete consent in the browser; copy the printed refresh token
```

Store the three values as env vars (below). The refresh token is long-lived; the
runtime exchanges it for short-lived access tokens headlessly.

## 3. Encrypt the token at rest (recommended)

Generate a Fernet key so the refresh token is encrypted in the `oauth_tokens`
table rather than kept only in the environment:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → set the output as GMAIL_TOKEN_ENC_KEY
```

If `GMAIL_TOKEN_ENC_KEY` is unset (or `cryptography` isn't installed), the app
falls back to reading `GMAIL_REFRESH_TOKEN` from the environment on each use and
does **not** persist it — it is never written in cleartext either way.

## 4. Create the Sheets service account

Mirrors the Drive setup. In the GCP project:

1. Enable the **Google Sheets API** and **Google Drive API**.
2. **IAM & Admin → Service Accounts → Create service account**; **Keys → Add key →
   JSON** and download it — this is `GOOGLE_APPLICATION_CREDENTIALS` (inline JSON
   on one line, or a mounted key-file path). The `drive.file` scope means the
   service account owns the ledger sheet it creates/appends to.
3. Either let the app create a fresh spreadsheet on first append (it will log the
   new id) and then pin it via `SHEETS_SPREADSHEET_ID`, or create the sheet
   yourself, share it with the service-account email as **Editor**, and set
   `SHEETS_SPREADSHEET_ID` to its id from the URL.

With no service account / spreadsheet id configured, the app uses the offline
`StubSheetsClient` (network-free) — the folder-watcher validation path proves the
pipeline end-to-end without any Google credentials.

## 5. Configure the app

```bash
cp .env.example .env          # edit the values below
docker compose up -d --build  # add --profile drive/gmail poller per RUNBOOK
```

`.env` (auto-loaded by Compose; gitignored) sets:

| Variable | Value |
| --- | --- |
| `INBOX_PROVIDER` | `gmail` |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | from step 1 |
| `GMAIL_REFRESH_TOKEN` | from step 2 |
| `GMAIL_TOKEN_ENC_KEY` | from step 3 (optional but recommended) |
| `GMAIL_TAX_YEAR` | the tax year to backfill to Jan 1 of (defaults to the current year) |
| `GMAIL_LABEL` | optional; not applied yet (read-only scope) |
| `SHEETS_SPREADSHEET_ID` | the ledger sheet id from step 4 |
| `GOOGLE_APPLICATION_CREDENTIALS` | the Sheets service-account key (inline JSON or path) |
| `ANTHROPIC_API_KEY` | **required in practice** for real emails — the offline stand-in cannot read arbitrary receipt text; without it, live receipts extract to empty fields and hold |

Selecting `INBOX_PROVIDER=gmail` without the three `GMAIL_CLIENT_*` /
`GMAIL_REFRESH_TOKEN` values fails fast at startup rather than silently serving the
mock demo set.

## 6. Revoke access

To disconnect the mailbox, revoke the refresh token with Google and clear the
stored token + sync cursor:

```bash
python -m backend.tools.gmail_revoke
```

## What's out of scope (v1)

- Labeling/archiving processed mail in Gmail (read-only scope; the hub is the
  queue).
- A forward-to-address ingestion adapter, or surfacing the folder-watcher as a
  user-selectable source.
- Multi-tenant credential storage (single-tenant per deploy).
- Rich income-side handling and custom categories (fixed Schedule C set for v1).
