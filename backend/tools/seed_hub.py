"""Seed the running hub with PDF-backed sample invoices (demo / P3-T6).

Renders each sample's invoice content to a real PDF (via ``samples/generate_pdfs``)
and POSTs the sample to the API with ``source.attachment_path`` pointing at that
PDF. Because the invoice then has a rasterizable source, the reviewer hub's
"View source" panel shows the original **page image** with the AI's highlight
boxes — not just the text preview.

    python -m backend.tools.seed_hub                      # -> http://127.0.0.1:8000
    python -m backend.tools.seed_hub http://127.0.0.1:8000

Point it at the API that actually has the Visual Document Review code and can
read these host paths (a local ``uvicorn``, not a stale container). Note that on
macOS ``localhost`` may resolve to IPv6 first — use ``127.0.0.1`` to force the
local uvicorn if a Docker container is also publishing the port.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

from samples.generate_pdfs import render_invoice_pdf

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
PDF_DIR = SAMPLES / "pdf"

# Document-style samples worth showing as PDFs (1 submit + 2 distinct holds).
STEMS = ["inv_clean_001", "inv_hold_unmatched_002", "inv_hold_mismatch_005"]


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
        source["attachment_path"] = str(pdf.resolve())
        source.setdefault("attachment", f"{stem}.pdf")
        try:
            out = _post(api, sample)
        except urllib.error.URLError as exc:
            print(f"  {stem}: POST failed ({exc}) — is the API at {api}?", file=sys.stderr)
            continue
        print(f"  {stem}: {out['id']}  status={out['status']}  (View source → {pdf.name})")
        seeded += 1
    print(f"\nSeeded {seeded}/{len(STEMS)} PDF-backed invoices to {api}.")
    print("Open the hub, click an invoice, and use 'View source'.")
    return 0 if seeded else 1


def main(argv: list[str]) -> int:
    api = argv[1] if len(argv) > 1 else "http://127.0.0.1:8000"
    return run(api)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
