import { useCallback, useEffect, useState } from "react";

import { getHealth, getInvoice, getMetrics, getNotifications, listInvoices } from "./api.js";
import InvoiceDetail from "./components/InvoiceDetail.jsx";
import InvoiceList from "./components/InvoiceList.jsx";
import MetricsBar from "./components/MetricsBar.jsx";

// Reviewer hub shell: invoice list <-> detail. The detail view is the
// post-decision QC surface (PRD FR9/FR10; USERS § Invoice Operations Reviewer)
// where reviewers correct, review, escalate, note, and rerun invoices.
export default function App() {
  const [invoices, setInvoices] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [health, setHealth] = useState("…");
  const [heldCount, setHeldCount] = useState(0);

  const refresh = useCallback(() => {
    listInvoices().then(setInvoices).catch((e) => setError(String(e)));
    getMetrics().then(setMetrics).catch((e) => setError(String(e)));
    // Held-item notification digest (R18) — badge in the app bar.
    getNotifications()
      .then((n) => setHeldCount(n.held_count ?? 0))
      .catch((e) => setError(String(e)));
  }, []);

  const loadDetail = useCallback((id) => {
    getInvoice(id).then(setDetail).catch((e) => setError(String(e)));
  }, []);

  // After a QC action: refresh the open detail and the list/metrics behind it.
  const onAction = useCallback(
    (updated) => {
      if (updated) setDetail(updated);
      else if (selectedId) loadDetail(selectedId);
      refresh();
    },
    [selectedId, loadDetail, refresh],
  );

  useEffect(() => {
    refresh();
    getHealth()
      .then((h) => setHealth(`API ${h.status} · db ${h.db}`))
      .catch(() => setHealth("API unreachable"));
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  return (
    <div className="app">
      <header className="app-bar">
        <h1>IntakeHub</h1>
        <span className="api-status">
          {heldCount > 0 && <span className="badge held">{heldCount} held</span>}{" "}
          {health}
        </span>
      </header>

      {error && <p className="error">{error}</p>}

      {selectedId && detail ? (
        <>
          <button className="link-btn" onClick={() => setSelectedId(null)}>
            ← Back to all invoices
          </button>
          <InvoiceDetail detail={detail} onAction={onAction} setError={setError} />
        </>
      ) : (
        <>
          <button className="link-btn" onClick={refresh}>Refresh</button>
          <MetricsBar metrics={metrics} />
          <InvoiceList invoices={invoices} onSelect={setSelectedId} />
        </>
      )}
    </div>
  );
}
