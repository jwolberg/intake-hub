// Thin client for the IntakeHub API (PRD §13). Reads list/inspect the
// invoices the AI has decided on; the POST helpers drive the post-decision human
// QC actions (PRD FR10): correct, review, escalate, note, rerun.

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// URL of a rendered page raster (1-based) for the Source overlay (P4-T5).
export function pageImageUrl(id, pageNumber) {
  return `${API_URL}/api/invoices/${id}/pages/${pageNumber}/image`;
}

// URL of the original source PDF (PRD §10 download; P5-T2). Served only when the
// invoice has a PDF source (detail.source.has_pdf).
export function sourcePdfUrl(id) {
  return `${API_URL}/api/invoices/${id}/source.pdf`;
}

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

// Held items for review, default oldest-first grouped by hold reason (R10).
export function getReviewQueue() {
  return getJSON("/api/review-queue");
}

// Held-item notification digest: total count + breakdown by reason (R18).
export function getNotifications() {
  return getJSON("/api/notifications");
}

// A bounded random sample of already-posted items to spot-check (R15).
export function spotCheck(k = 5) {
  return postJSON("/api/spot-check", { k });
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

// Overlay a Schedule C category correction on a held item; rerun files it (AE2).
export function correctCategory(id, category, reason) {
  return postJSON(`/api/invoices/${id}/corrections/category`, { category, reason });
}

// Reject a non-receipt: remove it from the queue, never file it (AE4).
export function rejectInvoice(id, note) {
  return postJSON(`/api/invoices/${id}/reject`, { note });
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

export function rerunInvoice(id) {
  return postJSON(`/api/invoices/${id}/rerun`, {});
}

// Resume a FAILED invoice from the failed stage (P3-T1).
export function retryInvoice(id) {
  return postJSON(`/api/invoices/${id}/retry`, {});
}

// Confirm an uncertain source-anchored field against the page image (P4-T6).
export function confirmCitation(id, targetId, reason) {
  return postJSON(`/api/invoices/${id}/citations/confirm`, {
    target_id: targetId,
    reason,
  });
}
