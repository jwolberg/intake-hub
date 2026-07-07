-- IntakeHub persistence schema (PRD §11, ARCHITECTURE.md §12).
--
-- Stage outputs are persisted, not just final state, so the hub can render an
-- invoice at its latest completed stage and reruns can diff against prior
-- outputs. audit_events is append-only and is the source of truth for state
-- and for the AI-vs-human distinction.

CREATE TABLE IF NOT EXISTS invoices (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL DEFAULT 'email',
    status              TEXT NOT NULL DEFAULT 'received',
    decision            TEXT,
    decision_confidence DOUBLE PRECISION,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_text         TEXT,
    source_pdf          BYTEA,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Original source PDF bytes (P5-T1) so the hub can serve the actual document
-- (PRD §10 download), independent of any local file path. Idempotent add for
-- databases created before this column existed.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS source_pdf BYTEA;

CREATE TABLE IF NOT EXISTS line_items (
    id                     TEXT PRIMARY KEY,
    invoice_id             TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    raw_description        TEXT NOT NULL,
    normalized_description TEXT,
    quantity               NUMERIC,
    unit_price             NUMERIC,
    total                  NUMERIC,
    service_period         TEXT,
    raw_source_text        TEXT,
    extraction_confidence  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_line_items_invoice ON line_items(invoice_id);

CREATE TABLE IF NOT EXISTS exceptions (
    id          TEXT PRIMARY KEY,
    invoice_id  TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exceptions_invoice ON exceptions(invoice_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    invoice_id  TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_events_invoice ON audit_events(invoice_id, timestamp);

-- Inbox idempotency (P6-T3): message ids already ingested, so re-fetching a mock
-- inbox never double-processes the same email (PRD §14, §7 Step 1 "Mock inbox").
CREATE TABLE IF NOT EXISTS seen_messages (
    message_id  TEXT PRIMARY KEY,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sheet-append idempotency (feat: solopreneur-ledger pivot, U3): source-of-truth
-- dedup gate for appends to the user's Google Sheet ledger. The Sheet is a
-- projection of this table, not the other way around — a retry after a
-- crashed/partial append checks this table first, so the same filed item is
-- never posted to the Sheet twice.
CREATE TABLE IF NOT EXISTS sheet_appends (
    idempotency_key TEXT PRIMARY KEY,
    sheet_row_ref   TEXT,
    status          TEXT NOT NULL DEFAULT 'posted',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OAuth refresh tokens (feat: solopreneur-ledger pivot, U8), encrypted at rest.
-- ``provider`` is a fixed key (e.g. 'gmail'); ``encrypted_token`` is Fernet
-- ciphertext (backend/inbox/_crypto.py) keyed by GMAIL_TOKEN_ENC_KEY — the
-- refresh token is the key to the mailbox (R20), so it is never written here in
-- cleartext. Cleared by backend/tools/gmail_revoke.py on revoke.
CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider        TEXT PRIMARY KEY,
    encrypted_token TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gmail incremental sync position (feat: solopreneur-ledger pivot, U8). A
-- single row (id='gmail'): the last historyId GmailInbox successfully synced
-- through, so the next fetch can request only the delta (history.list) instead
-- of re-scanning the whole mailbox. NULL means "no cursor yet" -> the next
-- fetch does a full backfill (first connect, or after a historyId expired and
-- the sentinel-bootstrap also failed).
CREATE TABLE IF NOT EXISTS gmail_sync_state (
    id          TEXT PRIMARY KEY DEFAULT 'gmail',
    history_id  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
