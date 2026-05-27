// Thin client for the InvoiceScreener API (PRD §13). Reads list/inspect the
// invoices the AI has decided on; the POST helpers drive the post-decision human
// QC actions (PRD FR10): correct, review, escalate, note, rerun.

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function getJSON(path) {
  const resp = await fetch(`${API_URL}${path}`);
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  return resp.json();
}

async function postJSON(path, body) {
  const resp = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  return resp.json();
}

export function listInvoices() {
  return getJSON("/api/invoices");
}

export function getInvoice(id) {
  return getJSON(`/api/invoices/${id}`);
}

export function getMetrics() {
  return getJSON("/api/metrics");
}

export function getHealth() {
  return getJSON("/health");
}

// --- human QC actions (PRD FR10) -------------------------------------------

export function correctMetadata(id, updates, reason) {
  return postJSON(`/api/invoices/${id}/corrections/metadata`, { updates, reason });
}

export function correctLineItem(id, body) {
  return postJSON(`/api/invoices/${id}/corrections/line-item`, body);
}

export function markReviewed(id, note) {
  return postJSON(`/api/invoices/${id}/reviewed`, { note });
}

export function escalateInvoice(id, reason) {
  return postJSON(`/api/invoices/${id}/escalate`, { reason });
}

export function addNote(id, note) {
  return postJSON(`/api/invoices/${id}/note`, { note });
}
