"""Performance / scale tests (P3-T5; PRD §14 benchmarks, §18 large scenario).

Not wall-clock benchmarks (those are flaky in CI) — these assert the workflow
stays *stable and correct* at scale: a large invoice matched against a large
catalog processes to a terminal state with every line matched and no failure.
"""

from backend.clients import PassthroughLLMClient, StubClinRunClient
from backend.db.repository import InMemoryRepository
from backend.domain import CatalogItem, ContextCandidate, Decision, InvoiceStatus
from backend.orchestrator import process

_CATALOG_SIZE = 250
_LINE_ITEMS = 80


class _BigCatalogRef:
    """Reference stub with a large catalog for one sponsor/study."""

    def resolve_sponsor_study_site(self, clues):
        return [ContextCandidate(
            sponsor_id="sponsor_x", study_id="study_x", site_id="site_x", score=1.0,
            sponsor_name="Globex Pharma", study_name="GX-NEURO-3",
            protocol_number="GLX-303", site_name="Globex Neuro Center",
        )]

    def get_catalog(self, sponsor_id, study_id):
        return [
            CatalogItem(id=f"cat_{n:04d}", sponsor_id=sponsor_id, study_id=study_id,
                        description=f"Procedure {n:04d}", unit_price=f"{100 + n}.00")
            for n in range(_CATALOG_SIZE)
        ]


def _big_invoice_sample() -> dict:
    items = [
        {"raw_description": f"Procedure {n:04d}", "quantity": "1",
         "unit_price": f"{100 + n}.00", "total": f"{100 + n}.00"}
        for n in range(_LINE_ITEMS)
    ]
    return {
        "source": {"channel": "email", "subject": "Big invoice", "attachment": "big.pdf"},
        "document": {
            "metadata": {
                "invoice_number": "INV-BIG",
                "sponsor_name": "Globex Pharma",
                "protocol_number": "GLX-303",
                "study_name": "GX-NEURO-3",
                "currency": "USD",
                "total_amount": str(sum(100 + n for n in range(_LINE_ITEMS))) + ".00",
            },
            "line_items": items,
        },
    }


def test_large_invoice_against_large_catalog_is_stable_and_fully_matched():
    repo = InMemoryRepository()
    invoice = process(
        _big_invoice_sample(), repo,
        llm=PassthroughLLMClient(), ref=_BigCatalogRef(), clinrun=StubClinRunClient(),
    )

    assert invoice.status is InvoiceStatus.SUBMITTED
    assert invoice.decision is Decision.SUBMIT
    matches = repo.get_matches(invoice.id)
    assert len(matches) == _LINE_ITEMS
    assert all(m.catalog_item_id for m in matches)  # every line matched, no failure
    assert repo.get_exceptions(invoice.id) == []
