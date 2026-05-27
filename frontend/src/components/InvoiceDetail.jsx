// Invoice detail view (PRD §10 Invoice Detail View, FR9 §6). Shows what the AI
// extracted, what it matched, the submit/hold decision + rationale, the
// exceptions, and the processing timeline — the post-decision QC surface.

function pct(v) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function terminalEvent(audit) {
  const terminal = ["submitted", "held", "failed"];
  return [...audit].reverse().find((e) => terminal.includes(e.action));
}

export default function InvoiceDetail({ detail }) {
  const { invoice, line_items, context, matches, exceptions, audit } = detail;
  const meta = invoice.metadata ?? {};
  const matchByLine = Object.fromEntries(matches.map((m) => [m.line_item_id, m]));
  const decisionEvent = terminalEvent(audit);
  const rationale = decisionEvent?.details?.rationale ?? decisionEvent?.details?.error;

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
        <dl className="kv">
          <dt>Decision confidence</dt>
          <dd>{pct(invoice.decision_confidence)}</dd>
          <dt>Rationale</dt>
          <dd>{rationale ?? "—"}</dd>
        </dl>
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
                  <td>{e.type}</td>
                  <td><span className="flag">{e.severity}</span></td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel">
        <h2>Resolved context</h2>
        <dl className="kv">
          <dt>Sponsor</dt><dd>{context?.sponsor_id ?? "—"}</dd>
          <dt>Study</dt><dd>{context?.study_id ?? "—"}</dd>
          <dt>Site</dt><dd>{context?.site_id ?? "—"}</dd>
          <dt>Confidence</dt><dd>{pct(context?.confidence)}</dd>
          <dt>Warnings</dt>
          <dd>{context?.warnings?.length ? context.warnings.join(", ") : "none"}</dd>
        </dl>
      </div>

      <div className="panel">
        <h2>Extracted metadata</h2>
        <dl className="kv">
          <dt>Invoice date</dt><dd>{meta.invoice_date ?? "—"}</dd>
          <dt>Vendor</dt><dd>{meta.vendor_name ?? "—"}</dd>
          <dt>Sponsor (stated)</dt><dd>{meta.sponsor_name ?? "—"}</dd>
          <dt>Study / protocol</dt>
          <dd>{[meta.study_name, meta.protocol_number].filter(Boolean).join(" / ") || "—"}</dd>
          <dt>Currency</dt><dd>{meta.currency ?? "—"}</dd>
          <dt>Total amount</dt><dd>{meta.total_amount ?? "—"}</dd>
        </dl>
      </div>

      <div className="panel">
        <h2>Line items &amp; matches</h2>
        <table>
          <thead>
            <tr>
              <th>Description</th>
              <th className="right">Qty</th>
              <th className="right">Total</th>
              <th>Matched catalog item</th>
              <th className="right">Match</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {line_items.map((li) => {
              const m = matchByLine[li.id];
              const flags = [];
              if (!m || !m.catalog_item_id) flags.push("unmatched");
              if (m?.amount_match === false) flags.push("amount mismatch");
              return (
                <tr key={li.id}>
                  <td>{li.raw_description}</td>
                  <td className="right">{li.quantity ?? "—"}</td>
                  <td className="right">{li.total ?? "—"}</td>
                  <td className="muted">{m?.catalog_description ?? "—"}</td>
                  <td className="right">{pct(m?.confidence)}</td>
                  <td className="flag">{flags.join(", ")}</td>
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
              <span><strong>{e.action}</strong> <span className="muted">({e.actor})</span></span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
