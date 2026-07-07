// Strategy metrics for the Operations Lead (STRATEGY § Key metrics;
// USERS § Operations Lead). Read-only aggregate over all processed invoices.

function rate(v) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export default function MetricsBar({ metrics }) {
  if (!metrics) return null;
  const stats = [
    { label: "Processed", value: metrics.total },
    { label: "Posted", value: metrics.submitted },
    { label: "Held", value: metrics.held },
    { label: "Failed", value: metrics.failed },
    { label: "Auto-submit rate", value: rate(metrics.auto_submit_rate) },
    { label: "False-submit rate", value: rate(metrics.false_submit_rate) },
    { label: "Hold precision", value: rate(metrics.hold_precision) },
  ];
  return (
    <div className="panel metrics">
      {stats.map((s) => (
        <div className="metric" key={s.label}>
          <span className="metric-value">{s.value}</span>
          <span className="metric-label">{s.label}</span>
        </div>
      ))}
    </div>
  );
}
