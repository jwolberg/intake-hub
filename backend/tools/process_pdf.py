"""CLI: run a real PDF invoice through the full pipeline and print the trace.

    python -m backend.tools.process_pdf samples/pdf/inv_clean_001.pdf

Uses the in-memory repository + a stub Sheets client and the offline
``LayoutLLMClient`` extraction stand-in, so it needs no database, network, or
API key. A real deployment would point at Postgres + a real LLM provider.
"""

from __future__ import annotations

import pathlib
import sys

from backend.clients import StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.orchestrator import process
from backend.parser.pdf import LayoutLLMClient


def run(pdf_path: str) -> int:
    path = pathlib.Path(pdf_path)
    if not path.is_file():
        print(f"error: no such file: {pdf_path}", file=sys.stderr)
        return 2

    repo = InMemoryRepository()
    sheets = StubSheetsClient()
    sample = {"source": {"channel": "upload", "attachment": path.name,
                         "attachment_path": str(path)}}
    invoice = process(
        sample, repo,
        llm=LayoutLLMClient(), sheets=sheets,
    )

    line_items = repo.get_line_items(invoice.id)
    exceptions = repo.get_exceptions(invoice.id)

    print(f"\n=== {path.name} ===")
    print(f"status   : {invoice.status.value}")
    print(f"decision : {invoice.decision.value if invoice.decision else '-'}"
          f"  (confidence {invoice.decision_confidence})")
    print(f"vendor   : {invoice.metadata.vendor_name}")
    print(f"total    : {invoice.metadata.total_amount} {invoice.metadata.currency or ''}".rstrip())

    print("line items:")
    for li in line_items:
        print(f"  - {li.raw_description:32} qty={li.quantity} total={li.total}")

    if sheets.rows:
        print("ledger row(s):")
        for row in sheets.rows:
            print(f"  - {row}")

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
