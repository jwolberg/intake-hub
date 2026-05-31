"""Seed a *remote* hub (Cloud Run) with PDF-backed sample invoices (P5-T3).

Unlike ``seed_hub`` — which sets ``source.attachment_path`` to a host file the
API reads off local disk — this carries the PDF **in the request** as base64
(``source.attachment_b64``), because Cloud Run cannot read host paths. The API
persists the bytes (``invoices.source_pdf``), so the deployed hub gets real
source documents: page-image preview *and* the "Open original PDF" download.

    python -m backend.tools.seed_cloud https://invoicescreener-api-...run.app

Offline-friendly: the controlled sample PDFs extract via the offline
``LayoutLLMClient`` (no API key needed), exactly as the local PDF path does.
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
import urllib.error
import urllib.request

from samples.generate_pdfs import render_invoice_pdf

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
PDF_DIR = SAMPLES / "pdf"

# Same demo set as seed_hub: a clean submit, two distinct holds, a low-confidence line.
STEMS = ["inv_clean_001", "inv_hold_unmatched_002", "inv_hold_mismatch_005",
         "inv_uncertain_006"]


def _post(api: str, sample: dict) -> dict:
    req = urllib.request.Request(
        f"{api}/api/invoices/process",
        data=json.dumps(sample).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def run(api: str) -> int:
    api = api.rstrip("/")
    PDF_DIR.mkdir(exist_ok=True)
    seeded = 0
    for stem in STEMS:
        sample = json.loads((SAMPLES / f"{stem}.json").read_text())
        pdf = render_invoice_pdf(sample, PDF_DIR / f"{stem}.pdf")
        source = sample.setdefault("source", {})
        # Carry the PDF in the payload (no host path — the cloud can't read one).
        source["attachment"] = f"{stem}.pdf"
        source["attachment_b64"] = base64.b64encode(pdf.read_bytes()).decode()
        source.pop("attachment_path", None)
        try:
            out = _post(api, sample)
        except urllib.error.URLError as exc:
            print(f"  {stem}: POST failed ({exc}) — is the API at {api}?", file=sys.stderr)
            continue
        print(f"  {stem}: {out['id']}  status={out['status']}")
        seeded += 1
    print(f"\nSeeded {seeded}/{len(STEMS)} PDF-backed invoices to {api}.")
    print("Open the hub, click an invoice → 'View source' → 'Open original PDF'.")
    return 0 if seeded else 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m backend.tools.seed_cloud <API_URL>", file=sys.stderr)
        return 2
    return run(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
