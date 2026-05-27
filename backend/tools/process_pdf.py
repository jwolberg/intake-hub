"""CLI: run a real PDF invoice through the full pipeline and print the trace.

    python -m backend.tools.process_pdf samples/pdf/inv_clean_001.pdf

Uses the in-memory repository + stub reference/ClinRun clients and the offline
``LayoutLLMClient`` extraction stand-in, so it needs no database, network, or
API key. A real deployment would point at Postgres + a real LLM provider.
"""

from __future__ import annotations

import pathlib
import sys

from backend.clients import StubClinRunClient, StubMCPReferenceClient
from backend.db.repository import InMemoryRepository
from backend.orchestrator import process
from backend.parser.pdf import LayoutLLMClient


def run(pdf_path: str) -> int:
    path = pathlib.Path(pdf_path)
    if not path.is_file():
        print(f"error: no such file: {pdf_path}", file=sys.stderr)
        return 2

    repo = InMemoryRepository()
    sample = {"source": {"channel": "upload", "attachment": path.name,
                         "attachment_path": str(path)}}
    invoice = process(
        sample, repo,
        llm=LayoutLLMClient(), ref=StubMCPReferenceClient(), clinrun=StubClinRunClient(),
    )

    ctx = repo.get_context(invoice.id)
    matches = repo.get_matches(invoice.id)
    line_items = {li.id: li for li in repo.get_line_items(invoice.id)}
    exceptions = repo.get_exceptions(invoice.id)

    print(f"\n=== {path.name} ===")
    print(f"status   : {invoice.status.value}")
    print(f"decision : {invoice.decision.value if invoice.decision else '-'}"
          f"  (confidence {invoice.decision_confidence})")
    print(f"vendor   : {invoice.metadata.vendor_name}")
    print(f"sponsor  : {invoice.metadata.sponsor_name} / {invoice.metadata.study_name}")
    if ctx:
        print(f"resolved : sponsor={ctx.sponsor_id} study={ctx.study_id} site={ctx.site_id}"
              f"  (confidence {round(ctx.confidence, 2)})")
        if ctx.warnings:
            print(f"warnings : {', '.join(ctx.warnings)}")

    print("line items:")
    for m in matches:
        li = line_items.get(m.line_item_id)
        desc = li.raw_description if li else m.line_item_id
        target = m.catalog_description or "UNMATCHED"
        flags = f"  [{', '.join(m.exceptions)}]" if m.exceptions else ""
        print(f"  - {desc:32} -> {target:24} ({m.confidence}){flags}")

    if exceptions:
        print("exceptions:")
        for e in exceptions:
            print(f"  - {e.type} ({e.severity.value}): {e.message}")

    print("trace    : " + " -> ".join(ev.action.value for ev in repo.get_audit(invoice.id)))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m backend.tools.process_pdf <invoice.pdf>", file=sys.stderr)
        return 2
    return run(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
