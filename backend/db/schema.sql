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

CREATE TABLE IF NOT EXISTS resolved_context (
    invoice_id  TEXT PRIMARY KEY REFERENCES invoices(id) ON DELETE CASCADE,
    sponsor_id  TEXT,
    study_id    TEXT,
    site_id     TEXT,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 0,
    candidates  JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings    JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS match_results (
    line_item_id              TEXT PRIMARY KEY REFERENCES line_items(id) ON DELETE CASCADE,
    invoice_id                TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    catalog_item_id           TEXT,
    catalog_description       TEXT,
    confidence                DOUBLE PRECISION NOT NULL DEFAULT 0,
    amount_match              BOOLEAN,
    quantity_match            BOOLEAN,
    rationale                 TEXT,
    requires_exception_review BOOLEAN NOT NULL DEFAULT FALSE,
    alternates                JSONB NOT NULL DEFAULT '[]'::jsonb,
    exceptions                JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_match_results_invoice ON match_results(invoice_id);

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

CREATE TABLE IF NOT EXISTS catalog_cache (
    sponsor_id  TEXT NOT NULL,
    study_id    TEXT NOT NULL,
    items       JSONB NOT NULL DEFAULT '[]'::jsonb,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sponsor_id, study_id)
);

-- Inbox idempotency (P6-T3): message ids already ingested, so re-fetching a mock
-- inbox never double-processes the same email (PRD §14, §7 Step 1 "Mock inbox").
CREATE TABLE IF NOT EXISTS seen_messages (
    message_id  TEXT PRIMARY KEY,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
