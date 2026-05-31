// Invoice detail view (PRD §10 Invoice Detail View, FR9/FR10 §6). The
// post-decision QC surface: every section the reviewer needs to understand a
// decision fast — source, extracted metadata (value/confidence/evidence +
// editable correction), context (resolved + candidates + mismatch warnings),
// line-item matching (raw vs normalized + rationale + flags + match correction),
// and the decision (submit/hold + confidence + rationale + risk flags +
// submission status) — plus the human QC actions (correct/review/escalate/note).

import { useRef, useState } from "react";

import {
  addNote,
  confirmCitation,
  correctLineItem,
  correctMetadata,
  escalateInvoice,
  markReviewed,
  pageImageUrl,
  rerunInvoice,
  sourcePdfUrl,
} from "../api.js";

// Header fields shown in the Extracted Metadata section (PRD §10), in order.
const META_FIELDS = [
  ["invoice_number", "Invoice number"],
  ["invoice_date", "Invoice date"],
  ["due_date", "Due date"],
  ["vendor_name", "Vendor"],
  ["sponsor_name", "Sponsor (stated)"],
  ["study_name", "Study"],
  ["protocol_number", "Protocol"],
  ["site_identifier", "Site"],
  ["billing_period", "Billing period"],
  ["currency", "Currency"],
  ["total_amount", "Total amount"],
  ["tax", "Tax"],
  ["payment_terms", "Payment terms"],
];

const META_LABELS = Object.fromEntries(META_FIELDS);

// Human label for a citation target_id (e.g. "metadata.vendor_name" → "Vendor",
// "line_item.<id>.raw_description" → "Line item").
function targetLabel(targetId, lineItems) {
  if (targetId.startsWith("metadata.")) {
    return META_LABELS[targetId.slice("metadata.".length)] ?? targetId;
  }
  const m = targetId.match(/^line_item\.(.+)\.raw_description$/);
  if (m) {
    const li = lineItems.find((l) => l.id === m[1]);
    return li ? `Line: ${li.raw_description}` : "Line item";
  }
  return targetId;
}

function pct(v) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function humanize(code) {
  if (!code) return "";
  const spaced = code.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function confidenceClass(v) {
  if (v == null) return "muted";
  if (v < 0.6) return "conf-low";
  if (v < 0.85) return "conf-mid";
  return "conf-high";
}

function terminalEvent(audit) {
  const terminal = ["submitted", "held", "failed"];
  return [...audit].reverse().find((e) => terminal.includes(e.action));
}

function submissionStatus(invoice, event) {
  const d = event?.details ?? {};
  if (invoice.status === "submitted") {
    return d.reference_id
      ? `Submitted to ClinRun (ref ${d.reference_id})`
      : "Submitted to ClinRun";
  }
  if (invoice.status === "failed") return `Failed: ${d.reason ?? "unknown error"}`;
  if (invoice.status === "held") return "Held — not submitted";
  return "Pending";
}

// Page image(s) + an SVG overlay of source-anchored highlight boxes (P4-T5).
// Boxes are normalized to [0,1] so the SVG uses viewBox="0 0 1 1" with
// preserveAspectRatio="none" — no scaling math, the browser maps the unit square
// onto the rendered image. A citation with no resolved bbox is not drawn (no
// hallucinated highlight, spec §3). Hovering a box ↔ its field is two-way.
function SourceOverlay({ invoiceId, pages, citations, resolved, hovered, setHovered, scrollToRow }) {
  if (!pages?.length) return null;
  const byPage = {};
  for (const c of citations ?? []) {
    if (!c.bbox) continue; // unanchored: no box
    (byPage[c.page_number] ??= []).push(c);
  }
  // An uncertain box the reviewer has confirmed/corrected reads as resolved.
  const statusClass = (c) =>
    c.status === "uncertain" && resolved?.has(c.target_id) ? "confirmed" : c.status;

  return (
    <div className="pages">
      {pages.map((page) => (
        <figure className="page" key={page.page_number}>
          <div className="page-frame">
            <img
              className="page-image"
              src={pageImageUrl(invoiceId, page.page_number)}
              alt={`Invoice page ${page.page_number}`}
            />
            <svg
              className="page-overlay"
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {(byPage[page.page_number] ?? []).map((c) => (
                <rect
                  key={c.target_id}
                  data-target-id={c.target_id}
                  className={`cite-rect cite-${statusClass(c)}${
                    hovered === c.target_id ? " is-highlight" : ""
                  }`}
                  x={c.bbox.x}
                  y={c.bbox.y}
                  width={c.bbox.width}
                  height={c.bbox.height}
                  onMouseEnter={() => setHovered(c.target_id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => {
                    setHovered(c.target_id);
                    scrollToRow(c.target_id);
                  }}
                >
                  <title>{`${c.quote} (${c.status})`}</title>
                </rect>
              ))}
            </svg>
          </div>
          <figcaption className="muted small">Page {page.page_number}</figcaption>
        </figure>
      ))}
    </div>
  );
}

// "View source" side panel: the original document (page image + highlight
// overlay, or text preview) docked on the right. Non-modal so the reviewer can
// still hover the fields/line items — the box ↔ field highlighting stays live
// while the source sits alongside.
function SourceDrawer({ open, onClose, invoiceId, source, pages, citations, resolved,
                        sourceText, hovered, setHovered, scrollToRow }) {
  if (!open) return null;
  return (
    <aside className="source-drawer" aria-label="Original source">
      <div className="drawer-head">
        <strong>Original source{source?.attachment ? ` — ${source.attachment}` : ""}</strong>
        <div className="drawer-actions">
          {source?.has_pdf && (
            <a
              className="small-btn"
              href={sourcePdfUrl(invoiceId)}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open original PDF ↗
            </a>
          )}
          <button className="small-btn" onClick={onClose}>Close ✕</button>
        </div>
      </div>
      <div className="drawer-body">
        {pages.length > 0 ? (
          <>
            <p className="muted small">
              Hover an extracted field or line item to locate it; click a box to jump
              to its field.
            </p>
            <SourceOverlay
              invoiceId={invoiceId}
              pages={pages}
              citations={citations}
              resolved={resolved}
              hovered={hovered}
              setHovered={setHovered}
              scrollToRow={scrollToRow}
            />
          </>
        ) : sourceText ? (
          <pre className="preview">{sourceText}</pre>
        ) : (
          <p className="muted">No original document image for this invoice.</p>
        )}
      </div>
    </aside>
  );
}

export default function InvoiceDetail({ detail, onAction, setError }) {
  const { invoice, source, extraction, line_items, context, matches, exceptions, audit } =
    detail;
  const meta = invoice.metadata ?? {};
  const overlay = detail.corrections?.metadata ?? {};
  const matchOverlay = detail.corrections?.line_items ?? {};
  const conf = extraction?.field_confidence ?? {};
  const evidence = extraction?.field_evidence ?? {};
  const missing = new Set(extraction?.missing_fields ?? []);
  const matchByLine = Object.fromEntries(matches.map((m) => [m.line_item_id, m]));
  const decisionEvent = terminalEvent(audit);
  const d = decisionEvent?.details ?? {};
  const rationale = d.reason ?? d.rationale ?? d.error;
  const riskFlags = d.risk_flags ?? [];

  const [metaEdits, setMetaEdits] = useState({});
  const [metaReason, setMetaReason] = useState("");
  const [lineEdits, setLineEdits] = useState({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  // Source-overlay ↔ field two-way linking (P4-T5): the target_id currently
  // highlighted, shared between the SVG boxes and the metadata / line-item rows.
  const [hovered, setHovered] = useState(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const rootRef = useRef(null);
  const pages = detail.pages ?? [];
  const citations = detail.citations ?? [];
  const hasSource = pages.length > 0 || Boolean(detail.source_text);

  // Uncertain citations gate a clean review (P4-T6): each must be confirmed
  // against the page image or corrected (a correction supplies a verified value).
  const confirmedTargets = new Set(
    audit.filter((e) => e.action === "confirmed").map((e) => e.details?.target_id),
  );
  const correctedTargets = new Set([
    ...Object.keys(overlay).map((k) => `metadata.${k}`),
    ...Object.keys(matchOverlay).map((id) => `line_item.${id}.raw_description`),
  ]);
  const resolvedTargets = new Set([...confirmedTargets, ...correctedTargets]);
  const uncertain = citations.filter((c) => c.status === "uncertain");
  const pendingUncertain = uncertain.filter((c) => !resolvedTargets.has(c.target_id));
  const reviewBlocked = pendingUncertain.length > 0;

  function scrollToRow(targetId) {
    rootRef.current
      ?.querySelector(`[data-row="${targetId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Props that make a row light up in sync with its highlight box.
  function rowLink(targetId) {
    return {
      "data-row": targetId,
      className: hovered === targetId ? "row-linked" : undefined,
      onMouseEnter: () => setHovered(targetId),
      onMouseLeave: () => setHovered(null),
    };
  }

  // Run a QC action; the route returns the refreshed detail, which onAction applies.
  function run(promise) {
    setBusy(true);
    promise
      .then(onAction)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  }

  function effective(key) {
    const o = overlay[key];
    return o != null && o !== "" ? o : meta[key];
  }

  function saveMetadata() {
    const updates = {};
    for (const [key, val] of Object.entries(metaEdits)) {
      const current = effective(key);
      if (val !== "" && val !== (current == null ? "" : String(current))) {
        updates[key] = val;
      }
    }
    if (Object.keys(updates).length === 0) return;
    run(correctMetadata(invoice.id, updates, metaReason || null));
    setMetaEdits({});
    setMetaReason("");
  }

  function confirmField(targetId) {
    run(confirmCitation(invoice.id, targetId, note || null));
  }

  function saveLine(lineId) {
    const edit = lineEdits[lineId] ?? {};
    run(
      correctLineItem(invoice.id, {
        line_item_id: lineId,
        catalog_item_id: edit.catalog_item_id || null,
        catalog_description: edit.catalog_description || null,
      }),
    );
    setLineEdits((prev) => ({ ...prev, [lineId]: {} }));
  }

  return (
    <div ref={rootRef}>
      <div className="panel detail-head">
        <h2>
          {effective("invoice_number") ?? invoice.id}{" "}
          <span className={`badge ${invoice.decision ?? "neutral"}`}>
            {invoice.decision ?? "no decision"}
          </span>{" "}
          <span className={`badge ${invoice.status}`}>{invoice.status}</span>
        </h2>
        <button
          className="view-source-btn"
          disabled={!hasSource}
          title={hasSource ? undefined : "No original document for this invoice"}
          onClick={() => setSourceOpen(true)}
        >
          View source ↗
        </button>
      </div>

      {/* QC Actions (PRD §10/FR10) — every action records a human audit event. */}
      <div className="panel">
        <h2>QC actions</h2>
        <div className="qc-actions">
          <button
            disabled={busy || reviewBlocked}
            title={
              reviewBlocked
                ? "Confirm or correct the uncertain fields below first"
                : undefined
            }
            onClick={() => run(markReviewed(invoice.id, note || null))}
          >
            Mark reviewed
          </button>
          <button disabled={busy} onClick={() => run(rerunInvoice(invoice.id))}>
            Rerun with corrections
          </button>
          <button
            disabled={busy}
            className="danger"
            onClick={() => run(escalateInvoice(invoice.id, note || null))}
          >
            Escalate
          </button>
          <button
            disabled={busy || !note.trim()}
            onClick={() => {
              run(addNote(invoice.id, note));
              setNote("");
            }}
          >
            Add note
          </button>
        </div>
        <textarea
          className="note-input"
          placeholder="Reviewer note / reason (used by note, and attached to reviewed / escalate)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>

      {/* Verify uncertain fields (P4-T6) — confirm against the page image or
          correct; gates a clean "reviewed". */}
      {uncertain.length > 0 && (
        <div className="panel">
          <h2>
            Uncertain fields to verify{" "}
            {reviewBlocked ? (
              <span className="flag sev-medium">{pendingUncertain.length} pending</span>
            ) : (
              <span className="flag sev-low">all verified</span>
            )}
          </h2>
          <p className="muted small">
            These values were extracted with low confidence. Confirm each against its
            highlighted box on the page, or correct it below — required before a clean
            review.
          </p>
          <table>
            <thead>
              <tr><th>Field</th><th>Extracted value</th><th>State</th><th /></tr>
            </thead>
            <tbody>
              {uncertain.map((c) => {
                const corrected = correctedTargets.has(c.target_id);
                const confirmed = confirmedTargets.has(c.target_id);
                const done = corrected || confirmed;
                return (
                  <tr key={c.target_id} {...rowLink(c.target_id)}>
                    <td className="muted">{targetLabel(c.target_id, line_items)}</td>
                    <td>{c.quote}</td>
                    <td>
                      {done ? (
                        <span className="flag sev-low">
                          {corrected ? "corrected" : "confirmed"}
                        </span>
                      ) : (
                        <span className="flag sev-medium">needs review</span>
                      )}
                    </td>
                    <td>
                      <button
                        className="small-btn"
                        disabled={busy || done}
                        onClick={() => confirmField(c.target_id)}
                      >
                        Confirm
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {exceptions.length > 0 && (
        <div className="panel">
          <h2>Exceptions ({exceptions.length})</h2>
          <table>
            <thead>
              <tr><th>Type</th><th>Severity</th><th>Message</th></tr>
            </thead>
            <tbody>
              {exceptions.map((e) => (
                <tr key={e.id}>
                  <td title={e.type}>{humanize(e.type)}</td>
                  <td><span className={`flag sev-${e.severity}`}>{e.severity}</span></td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Decision (PRD §10) — submit/hold + confidence + rationale + risk flags. */}
      <div className="panel">
        <h2>Decision</h2>
        <dl className="kv">
          <dt>Decision</dt>
          <dd>
            <span className={`badge ${invoice.decision ?? "neutral"}`}>
              {invoice.decision ?? "—"}
            </span>
          </dd>
          <dt>Decision confidence</dt>
          <dd className={confidenceClass(invoice.decision_confidence)}>
            {pct(invoice.decision_confidence)}
          </dd>
          <dt>Rationale</dt>
          <dd>{rationale ?? "—"}</dd>
          <dt>Submission status</dt>
          <dd>{submissionStatus(invoice, decisionEvent)}</dd>
        </dl>
        {riskFlags.length > 0 && (
          <table>
            <thead>
              <tr><th>Risk flag</th><th>Severity</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {riskFlags.map((f, i) => (
                <tr key={`${f.type}-${i}`}>
                  <td title={f.type}>{humanize(f.type)}</td>
                  <td><span className={`flag sev-${f.severity}`}>{f.severity}</span></td>
                  <td>{f.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Source (PRD §10) — metadata inline; the original document opens in a
          side panel via "View source" so it can sit alongside the fields. */}
      <div className="panel">
        <h2>
          Source{" "}
          <button
            className="small-btn"
            disabled={!hasSource}
            title={hasSource ? undefined : "No original document for this invoice"}
            onClick={() => setSourceOpen(true)}
          >
            View source ↗
          </button>
        </h2>
        <dl className="kv">
          <dt>Subject</dt><dd>{source?.subject ?? "—"}</dd>
          <dt>Sender</dt><dd>{source?.sender ?? "—"}</dd>
          <dt>Channel</dt><dd>{source?.channel ?? invoice.source ?? "—"}</dd>
          <dt>Attachment</dt><dd>{source?.attachment ?? "—"}</dd>
        </dl>
        {!hasSource && (
          <p className="muted small">No original document image for this invoice.</p>
        )}
      </div>

      {/* Extracted Metadata (PRD §10) — value + confidence + evidence + correction. */}
      <div className="panel">
        <h2>Extracted metadata</h2>
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th className="right">Confidence</th>
              <th>Source evidence</th>
              <th>Correction</th>
            </tr>
          </thead>
          <tbody>
            {META_FIELDS.map(([key, label]) => {
              const value = meta[key];
              const isMissing = missing.has(key) || value == null || value === "";
              const corrected = key in overlay;
              return (
                <tr key={key} {...rowLink(`metadata.${key}`)}>
                  <td className="muted">{label}</td>
                  <td>
                    {corrected ? (
                      <span>
                        <strong>{String(overlay[key])}</strong>{" "}
                        <span className="muted small">
                          (AI: {isMissing ? "missing" : String(value)})
                        </span>
                      </span>
                    ) : isMissing ? (
                      <span className="flag sev-high">missing</span>
                    ) : (
                      String(value)
                    )}
                  </td>
                  <td className={`right ${confidenceClass(conf[key])}`}>
                    {isMissing ? "—" : pct(conf[key])}
                  </td>
                  <td className="muted evidence">{evidence[key] ?? "—"}</td>
                  <td>
                    <input
                      className="cell-input"
                      placeholder="correct…"
                      value={metaEdits[key] ?? ""}
                      onChange={(e) =>
                        setMetaEdits((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="qc-actions">
          <input
            className="reason-input"
            placeholder="reason for correction (optional)"
            value={metaReason}
            onChange={(e) => setMetaReason(e.target.value)}
          />
          <button disabled={busy} onClick={saveMetadata}>Save metadata corrections</button>
        </div>
      </div>

      {/* Context Resolution (PRD §10) — resolved + candidates + mismatch warnings. */}
      <div className="panel">
        <h2>Context resolution</h2>
        <dl className="kv">
          <dt>Sponsor</dt><dd>{context?.sponsor_id ?? "—"}</dd>
          <dt>Study</dt><dd>{context?.study_id ?? "—"}</dd>
          <dt>Site</dt><dd>{context?.site_id ?? "—"}</dd>
          <dt>Confidence</dt>
          <dd className={confidenceClass(context?.confidence)}>{pct(context?.confidence)}</dd>
          <dt>Mismatch warnings</dt>
          <dd>
            {context?.warnings?.length
              ? context.warnings.map((w) => (
                  <span key={w} className="flag sev-medium" title={w}>{humanize(w)}</span>
                ))
              : "none"}
          </dd>
        </dl>
        {context?.candidates?.length > 1 && (
          <>
            <h3 className="subhead">Candidate alternatives</h3>
            <table>
              <thead>
                <tr>
                  <th>Sponsor</th><th>Study</th><th>Site</th><th className="right">Score</th>
                </tr>
              </thead>
              <tbody>
                {context.candidates.map((c, i) => (
                  <tr key={`${c.site_id}-${i}`}>
                    <td>{c.sponsor_name ?? c.sponsor_id ?? "—"}</td>
                    <td>{c.study_name ?? c.study_id ?? "—"}</td>
                    <td>{c.site_name ?? c.site_id ?? "—"}</td>
                    <td className="right">{pct(c.score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      {/* Line-Item Matching (PRD §10) — raw vs normalized + rationale + flags + correction. */}
      <div className="panel">
        <h2>Line items &amp; matches</h2>
        <table>
          <thead>
            <tr>
              <th>Raw / normalized</th>
              <th className="right">Qty</th>
              <th className="right">Total</th>
              <th>Matched catalog item</th>
              <th className="right">Match</th>
              <th>Rationale / flags</th>
              <th>Correct match</th>
            </tr>
          </thead>
          <tbody>
            {line_items.map((li) => {
              const m = matchByLine[li.id];
              const corrected = li.id in matchOverlay;
              const flags = [...(m?.exceptions ?? [])];
              if (!corrected && (!m || !m.catalog_item_id)) flags.push("unmatched");
              const edit = lineEdits[li.id] ?? {};
              const display = corrected
                ? matchOverlay[li.id].catalog_description ?? matchOverlay[li.id].catalog_item_id
                : m?.catalog_description;
              return (
                <tr key={li.id} {...rowLink(`line_item.${li.id}.raw_description`)}>
                  <td>
                    {li.raw_description}
                    {li.normalized_description && (
                      <div className="muted small">→ {li.normalized_description}</div>
                    )}
                  </td>
                  <td className="right">{li.quantity ?? "—"}</td>
                  <td className="right">{li.total ?? "—"}</td>
                  <td className="muted">
                    {display ?? "—"}
                    {corrected && <span className="flag sev-low">corrected</span>}
                  </td>
                  <td className={`right ${confidenceClass(corrected ? 1 : m?.confidence)}`}>
                    {corrected ? "100%" : pct(m?.confidence)}
                  </td>
                  <td>
                    {m?.rationale && <div className="small">{m.rationale}</div>}
                    {flags.length > 0 && (
                      <span className="flag sev-high">{flags.map(humanize).join(", ")}</span>
                    )}
                  </td>
                  <td>
                    <input
                      className="cell-input"
                      placeholder="catalog id"
                      value={edit.catalog_item_id ?? ""}
                      onChange={(e) =>
                        setLineEdits((prev) => ({
                          ...prev,
                          [li.id]: { ...edit, catalog_item_id: e.target.value },
                        }))
                      }
                    />
                    <input
                      className="cell-input"
                      placeholder="description"
                      value={edit.catalog_description ?? ""}
                      onChange={(e) =>
                        setLineEdits((prev) => ({
                          ...prev,
                          [li.id]: { ...edit, catalog_description: e.target.value },
                        }))
                      }
                    />
                    <button
                      className="small-btn"
                      disabled={busy || !(edit.catalog_item_id || edit.catalog_description)}
                      onClick={() => saveLine(li.id)}
                    >
                      Save
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Processing timeline</h2>
        <ul className="timeline">
          {audit.map((e) => (
            <li key={e.id}>
              <span className="ts">{e.timestamp}</span>
              <span>
                <strong>{e.action}</strong> <span className="muted">({e.actor})</span>
                {e.details?.reason && <span className="muted"> — {e.details.reason}</span>}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <SourceDrawer
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        invoiceId={invoice.id}
        source={source}
        pages={pages}
        citations={citations}
        resolved={resolvedTargets}
        sourceText={detail.source_text}
        hovered={hovered}
        setHovered={setHovered}
        scrollToRow={scrollToRow}
      />
    </div>
  );
}
