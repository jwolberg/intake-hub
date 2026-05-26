"""``mock-clinrun`` stub service.

A minimal FastAPI app standing in for the ClinRun backend (PRD §Non-Goals allows
a mock). Accepts invoice submissions and echoes an acceptance with a reference
id. Run with: ``uvicorn backend.clients.stub_servers.clinrun_app:app``.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="mock-clinrun", version="0.0.1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mock-clinrun"}


@app.post("/invoices")
def submit(payload: dict) -> dict:
    invoice_id = payload.get("invoice_id", "unknown")
    return {
        "accepted": True,
        "reference_id": f"clinrun_{invoice_id}",
        "message": "accepted by mock ClinRun",
    }
