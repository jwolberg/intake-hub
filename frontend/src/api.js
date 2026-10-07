// Thin client for the IntakeHub API (PRD §13). Reads list/inspect the
// invoices the AI has decided on; the POST helpers drive the post-decision human
// QC actions (PRD FR10): correct, review, escalate, note, rerun.

// In production the hub and API share one origin behind the IAP load balancer, so
// the build sets VITE_API_URL="" and calls are relative (the IAP cookie rides
// along). Local dev points at the separate uvicorn/compose API.
export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// An expired IAP session surfaces as 401 on an API call; a full reload sends the
// browser back through IAP's Google sign-in. Guarded: a 401 that persists after
// a reload (e.g. misconfigured audience) must not reload the page forever.
const RELOAD_KEY = "intakehub:auth-reload-at";
const RELOAD_GUARD_MS = 30_000;

function checkAuth(resp) {
  if (resp.status !== 401) return;
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0);
  } catch {
    // storage unavailable: fall through and allow one reload
  }
  if (Date.now() - last > RELOAD_GUARD_MS) {
    try {
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
    } catch {
      // ignore
    }
    window.location.reload();
  }
  throw new Error("Not signed in — reload the page to sign in again.");
}

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
  checkAuth(resp);
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  return resp.json();
}

async function postJSON(path, body) {
  const resp = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  checkAuth(resp);
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  return resp.json();
}

// Paginated list (#0012): resolves to { rows, total } — `total` comes from the
// API's X-Total-Count header (the row count across all pages).
export const PAGE_SIZE = 200;

export async function listInvoices({ offset = 0, limit = PAGE_SIZE } = {}) {
  const path = `/api/invoices?limit=${limit}&offset=${offset}`;
  const resp = await fetch(`${API_URL}${path}`);
  checkAuth(resp);
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`);
  const rows = await resp.json();
  const total = Number(resp.headers.get("X-Total-Count") ?? rows.length);
  return { rows, total };
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
