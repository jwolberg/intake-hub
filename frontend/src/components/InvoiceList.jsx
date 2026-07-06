// Invoice list view (PRD §10 Invoice List View). Surfaces status, decision,
// confidence, and exception count so the reviewer can triage attention to the
// flagged invoices, plus filter chips for the PRD §10 suggested filters.
//
// Filtering is done client-side over the `filter_tags` the API computes per row,
// so chips are instant and can show live counts. The same tags back the API's
// `?filter=` query param, so server- and client-side filtering stay consistent.

import { useState } from "react";

// PRD §10 Suggested filters, in display order. Keys match the API's FILTER_KEYS.
// (The clinical-trial mismatched_metadata/unmatched_line_items keys still exist
// server-side but never match post-pivot, so they're dropped here.)
const FILTERS = [
  ["posted", "Posted"],
  ["held", "Held"],
  ["failed", "Failed"],
  ["needs_review", "Needs review"],
  ["low_confidence", "Low confidence"],
];

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export default function InvoiceList({ invoices, onSelect }) {
  const [active, setActive] = useState(null);

  if (invoices.length === 0) {
    return (
      <div className="panel">
        <p className="empty">
          No invoices yet. Process one via <code>POST /api/invoices/process</code>.
        </p>
      </div>
    );
  }

  const counts = Object.fromEntries(FILTERS.map(([key]) => [key, 0]));
  for (const inv of invoices) {
    for (const tag of inv.filter_tags ?? []) {
      if (tag in counts) counts[tag] += 1;
    }
  }

  const shown = active
    ? invoices.filter((inv) => (inv.filter_tags ?? []).includes(active))
    : invoices;

  return (
    <div className="panel">
      <div className="list-head">
        <h2>Invoices ({shown.length})</h2>
        <div className="chips">
          <button
            className={`chip ${active === null ? "active" : ""}`}
            onClick={() => setActive(null)}
          >
            All <span className="chip-count">{invoices.length}</span>
          </button>
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              className={`chip ${active === key ? "active" : ""}`}
              disabled={counts[key] === 0}
              onClick={() => setActive(active === key ? null : key)}
            >
              {label} <span className="chip-count">{counts[key]}</span>
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="empty">No invoices match this filter.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Invoice #</th>
              <th>Vendor</th>
              <th className="right">Amount</th>
              <th>Decision</th>
              <th>Status</th>
              <th className="right">Confidence</th>
              <th className="right">Exceptions</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((inv) => (
              <tr key={inv.id} onClick={() => onSelect(inv.id)}>
                <td>{inv.invoice_number ?? inv.id}</td>
                <td>{inv.vendor_name ?? "—"}</td>
                <td className="right">{inv.total_amount ?? "—"}</td>
                <td>
                  <span className={`badge ${inv.decision ?? "neutral"}`}>
                    {inv.decision ?? "—"}
                  </span>
                </td>
                <td>
                  <span className={`badge ${inv.status}`}>{inv.status}</span>
                </td>
                <td className="right">{pct(inv.decision_confidence)}</td>
                <td className="right">{inv.exception_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
