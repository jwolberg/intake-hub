"""Unit tests for the PDF text parser (user-requested demo path).

``parse_invoice_text`` reverses the rendered layout into the extraction shape;
``LayoutLLMClient`` exposes it behind the LLMClient protocol. No reportlab/pypdf
needed here — we feed the text layout directly.
"""

from backend.parser.pdf import LayoutLLMClient, parse_invoice_text

SAMPLE_TEXT = """INVOICE
Invoice Number: INV-1001
Vendor: Riverside Clinical Research
Sponsor: Northwind Therapeutics
Protocol: NWT-101
Currency: USD
Total Amount: 720.00
Line Items
Description | Qty | Unit Price | Line Total
Patient screening visit | 2 | 300.00 | 600.00
ECG | 1 | 120.00 | 120.00
"""


def test_parses_header_fields():
    result = parse_invoice_text(SAMPLE_TEXT)
    md = result["metadata"]
    assert md["invoice_number"] == "INV-1001"
    assert md["sponsor_name"] == "Northwind Therapeutics"
    assert md["protocol_number"] == "NWT-101"
    assert md["total_amount"] == "720.00"


def test_parses_line_items_and_skips_header_row():
    result = parse_invoice_text(SAMPLE_TEXT)
    items = result["line_items"]
    assert len(items) == 2  # header row excluded
    assert items[0] == {
        "raw_description": "Patient screening visit",
        "quantity": "2", "unit_price": "300.00", "total": "600.00",
    }


def test_ignores_unknown_and_blank_lines():
    text = "Random preamble\n\nSponsor: Acme Biosciences\nFooter noise"
    result = parse_invoice_text(text)
    assert result["metadata"] == {"sponsor_name": "Acme Biosciences"}
    assert result["line_items"] == []


def test_layout_client_implements_complete_json():
    client = LayoutLLMClient()
    out = client.complete_json(system="x", user=SAMPLE_TEXT)
    assert out["metadata"]["invoice_number"] == "INV-1001"
    assert len(out["line_items"]) == 2
