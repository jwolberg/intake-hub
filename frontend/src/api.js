// Thin client for the InvoiceScreener API (PRD §13). The hub is read-only
// (PRD FR9 / USERS § Invoice Operations Reviewer): it lists and inspects
// invoices the AI has already decided on.

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function getJSON(path) {
  const resp = await fetch(`${API_URL}${path}`);
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  return resp.json();
}

export function listInvoices() {
  return getJSON("/api/invoices");
}

export function getInvoice(id) {
  return getJSON(`/api/invoices/${id}`);
}

export function getHealth() {
  return getJSON("/health");
}
