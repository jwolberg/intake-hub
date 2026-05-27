"""Stage: line-item matching.

Baseline matcher (PRD FR5, §7 Step 6; ARCHITECTURE.md §9): normalise
descriptions, then match by exact/containment against the catalog and verify
unit price within tolerance. Unmatched items are flagged for exception review.
The full layered matcher (semantic + LLM adjudication) is P2-A4.
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain import CatalogItem, LineItem, MatchResult

_PRICE_TOLERANCE = Decimal("0.01")
_EXACT = 0.95
_CONTAINS = 0.8


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def match(line_items: list[LineItem], catalog: list[CatalogItem]) -> list[MatchResult]:
    normalized = [(c, _normalize(c.description)) for c in catalog]
    results: list[MatchResult] = []

    for li in line_items:
        desc = _normalize(li.raw_description)
        best: CatalogItem | None = None
        best_conf = 0.0
        for item, item_desc in normalized:
            if desc == item_desc:
                conf = _EXACT
            elif item_desc in desc or desc in item_desc:
                conf = _CONTAINS
            else:
                conf = 0.0
            if conf > best_conf:
                best, best_conf = item, conf

        if best is None or best_conf == 0.0:
            results.append(
                MatchResult(
                    line_item_id=li.id,
                    confidence=0.0,
                    rationale="no catalog item matched this description",
                    requires_exception_review=True,
                    exceptions=["unmatched_line_item"],
                )
            )
            continue

        amount_match: bool | None = None
        if best.unit_price is not None and li.unit_price is not None:
            amount_match = abs(best.unit_price - li.unit_price) <= _PRICE_TOLERANCE

        results.append(
            MatchResult(
                line_item_id=li.id,
                catalog_item_id=best.id,
                catalog_description=best.description,
                confidence=best_conf,
                amount_match=amount_match,
                rationale=f"matched to '{best.description}'",
                requires_exception_review=(amount_match is False),
                exceptions=[] if amount_match is not False else ["amount_mismatch"],
            )
        )

    return results
