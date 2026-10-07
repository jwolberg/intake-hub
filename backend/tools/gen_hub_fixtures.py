"""Regenerate the hub's component-test fixtures from the real API (#0010).

The hub tests render real ``/api/invoices`` and ``/api/invoices/{id}`` payloads,
so they break when the API shape drifts instead of silently testing a stale
hand-written mock. Runs fully offline (in-memory repo, stub LLM/Sheets).

    python -m backend.tools.gen_hub_fixtures
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys

os.environ.setdefault("AUTH_MODE", "disabled")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
OUT = ROOT / "frontend" / "src" / "test" / "fixtures"

# fixture name -> sample stem
DETAILS = {
    "detail_posted": "inv_clean_001",
    "detail_held_uncertain": "inv_uncertain_006",
}
LIST_STEMS = ["inv_clean_001", "inv_body_003", "inv_uncertain_006", "inv_ambiguous_008"]


def _sample(stem: str) -> dict:
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())
    pdf = SAMPLES / "pdf" / f"{stem}.pdf"
    if pdf.exists():
        sample["source"]["attachment"] = pdf.name
        sample["source"]["attachment_b64"] = base64.b64encode(pdf.read_bytes()).decode()
    return sample


def main() -> int:
    from fastapi.testclient import TestClient

    from backend.api.main import app, get_pipeline_clients, get_repo
    from backend.clients import PassthroughLLMClient, StubSheetsClient
    from backend.db.repository import InMemoryRepository

    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    client = TestClient(app)
    ids = {
        stem: client.post("/api/invoices/process", json=_sample(stem)).json()["id"]
        for stem in LIST_STEMS
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, stem in DETAILS.items():
        detail = client.get(f"/api/invoices/{ids[stem]}").json()
        (OUT / f"{name}.json").write_text(json.dumps(detail, indent=2) + "\n")
    rows = client.get("/api/invoices").json()
    (OUT / "list.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"wrote {len(DETAILS) + 1} fixtures to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
