// Invoice list view (PRD §10 Invoice List View). Surfaces status, decision,
// confidence, and exception count so the reviewer can triage attention to the
// flagged invoices.

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export default function InvoiceList({ invoices, onSelect }) {
  if (invoices.length === 0) {
    return (
      <div className="panel">
        <p className="empty">
          No invoices yet. Process one via <code>POST /api/invoices/process</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Invoices ({invoices.length})</h2>
      <table>
        <thead>
          <tr>
            <th>Invoice #</th>
            <th>Vendor</th>
            <th>Sponsor / Study</th>
            <th>Decision</th>
            <th>Status</th>
            <th className="right">Confidence</th>
            <th className="right">Exceptions</th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((inv) => (
            <tr key={inv.id} onClick={() => onSelect(inv.id)}>
              <td>{inv.invoice_number ?? inv.id}</td>
              <td>{inv.vendor_name ?? "—"}</td>
              <td className="muted">
                {inv.sponsor_id ?? "—"}
                {inv.study_id ? ` / ${inv.study_id}` : ""}
              </td>
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
    </div>
  );
}
