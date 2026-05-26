import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Phase 0 shell for the reviewer hub. It only confirms API connectivity; the
// invoice list and detail views are built in Phase 1 (P1-T11) and Phase 2.
export default function App() {
  const [health, setHealth] = useState("checking...");

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then((d) => setHealth(`${d.status} (db: ${d.db})`))
      .catch(() => setHealth("unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>InvoiceScreener</h1>
      <p>Reviewer hub — scaffold.</p>
      <p>
        API status: <strong>{health}</strong>
      </p>
    </main>
  );
}
