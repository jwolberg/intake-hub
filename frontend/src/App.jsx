import { useCallback, useEffect, useState } from "react";

import { getHealth, getInvoice, listInvoices } from "./api.js";
import InvoiceDetail from "./components/InvoiceDetail.jsx";
import InvoiceList from "./components/InvoiceList.jsx";

// Reviewer hub shell: invoice list <-> detail. Read-only post-decision QC
// surface (PRD FR9; USERS § Invoice Operations Reviewer). QC actions
// (correct / rerun / escalate) arrive in Phase 2 (P2-C3).
export default function App() {
  const [invoices, setInvoices] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [health, setHealth] = useState("…");

  const refresh = useCallback(() => {
    listInvoices().then(setInvoices).catch((e) => setError(String(e)));
  }, []);

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
    getInvoice(selectedId).then(setDetail).catch((e) => setError(String(e)));
  }, [selectedId]);

  return (
    <div className="app">
      <header className="app-bar">
        <h1>InvoiceScreener</h1>
        <span className="api-status">{health}</span>
      </header>

      {error && <p className="error">{error}</p>}

      {selectedId && detail ? (
        <>
          <button className="link-btn" onClick={() => setSelectedId(null)}>
            ← Back to all invoices
          </button>
          <InvoiceDetail detail={detail} />
        </>
      ) : (
        <>
          <button className="link-btn" onClick={refresh}>Refresh</button>
          <InvoiceList invoices={invoices} onSelect={setSelectedId} />
        </>
      )}
    </div>
  );
}
