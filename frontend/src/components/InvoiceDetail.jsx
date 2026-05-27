// Invoice detail view (PRD §10 Invoice Detail View, FR9 §6). The post-decision
// QC surface: every section the reviewer needs to understand a decision fast —
// source, extracted metadata (value/confidence/evidence), context (resolved +
// candidates + mismatch warnings), line-item matching (raw vs normalized +
// rationale + flags), and the decision (submit/hold + confidence + rationale +
// risk flags + submission status). QC actions arrive in P2-C3.

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

function pct(v) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

// Humanize a taxonomy code (e.g. "context_mismatch" → "Context mismatch").
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

export default function InvoiceDetail({ detail }) {
  const { invoice, source, extraction, line_items, context, matches, exceptions, audit } =
    detail;
  const meta = invoice.metadata ?? {};
  const conf = extraction?.field_confidence ?? {};
  const evidence = extraction?.field_evidence ?? {};
  const missing = new Set(extraction?.missing_fields ?? []);
  const matchByLine = Object.fromEntries(matches.map((m) => [m.line_item_id, m]));
  const decisionEvent = terminalEvent(audit);
  const d = decisionEvent?.details ?? {};
  const rationale = d.reason ?? d.rationale ?? d.error;
  const riskFlags = d.risk_flags ?? [];

  return (
    <div>
      <div className="panel">
        <h2>
          {meta.invoice_number ?? invoice.id}{" "}
          <span className={`badge ${invoice.decision ?? "neutral"}`}>
            {invoice.decision ?? "no decision"}
          </span>{" "}
          <span className={`badge ${invoice.status}`}>{invoice.status}</span>
        </h2>
      </div>

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

      {/* Source (PRD §10). */}
      <div className="panel">
        <h2>Source</h2>
        <dl className="kv">
          <dt>Subject</dt><dd>{source?.subject ?? "—"}</dd>
          <dt>Sender</dt><dd>{source?.sender ?? "—"}</dd>
          <dt>Channel</dt><dd>{source?.channel ?? invoice.source ?? "—"}</dd>
          <dt>Attachment</dt><dd>{source?.attachment ?? "—"}</dd>
        </dl>
        {detail.source_text && (
          <details>
            <summary className="muted">Original invoice preview</summary>
            <pre className="preview">{detail.source_text}</pre>
          </details>
        )}
      </div>

      {/* Extracted Metadata (PRD §10) — value + confidence + source evidence. */}
      <div className="panel">
        <h2>Extracted metadata</h2>
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th className="right">Confidence</th>
              <th>Source evidence</th>
            </tr>
          </thead>
          <tbody>
            {META_FIELDS.map(([key, label]) => {
              const value = meta[key];
              const isMissing = missing.has(key) || value == null || value === "";
              return (
                <tr key={key}>
                  <td className="muted">{label}</td>
                  <td>
                    {isMissing
                      ? <span className="flag sev-high">missing</span>
                      : String(value)}
                  </td>
                  <td className={`right ${confidenceClass(conf[key])}`}>
                    {isMissing ? "—" : pct(conf[key])}
                  </td>
                  <td className="muted evidence">{evidence[key] ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
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

      {/* Line-Item Matching (PRD §10) — raw vs normalized + rationale + flags. */}
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
            </tr>
          </thead>
          <tbody>
            {line_items.map((li) => {
              const m = matchByLine[li.id];
              const flags = [...(m?.exceptions ?? [])];
              if (!m || !m.catalog_item_id) flags.push("unmatched");
              return (
                <tr key={li.id}>
                  <td>
                    {li.raw_description}
                    {li.normalized_description && (
                      <div className="muted small">→ {li.normalized_description}</div>
                    )}
                  </td>
                  <td className="right">{li.quantity ?? "—"}</td>
                  <td className="right">{li.total ?? "—"}</td>
                  <td className="muted">{m?.catalog_description ?? "—"}</td>
                  <td className={`right ${confidenceClass(m?.confidence)}`}>
                    {pct(m?.confidence)}
                  </td>
                  <td>
                    {m?.rationale && <div className="small">{m.rationale}</div>}
                    {flags.length > 0 && (
                      <span className="flag sev-high">{flags.map(humanize).join(", ")}</span>
                    )}
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
    </div>
  );
}
