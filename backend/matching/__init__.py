"""Stage: line-item matching.

Layered, explainable matcher (PRD FR5, §7 Step 6; ARCHITECTURE.md §9):

1. **Normalization** — canonicalise descriptions (lowercase, strip punctuation,
   collapse whitespace, drop filler words).
2. **Candidate generation** — score every catalog item by token-set similarity
   (with an exact/containment boost) and keep the ranked candidates above a
   floor. No candidate → an **unmatched material line item** (high-severity).
3. **LLM adjudication** — when the top candidates are close (ambiguous) and an
   ``LLMClient`` is supplied, the model picks the best mapping + rationale. It is
   advisory: an unusable/empty response falls back to the deterministic top, so
   the offline path is fully deterministic.
4. **Deterministic verification** — unit price and quantity·price→total are
   compared against the chosen catalog item with explicit tolerances; a mismatch
   downgrades confidence and raises a flag *regardless* of semantic certainty.

Each ``MatchResult`` records the chosen item, confidence, rationale, amount and
quantity comparison, alternate candidates, and whether it needs exception review.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from backend.clients.llm import LLMClient
from backend.domain import CatalogItem, LineItem, MatchResult

_PRICE_TOLERANCE = Decimal("0.01")
_CANDIDATE_FLOOR = 0.3          # below this, not a plausible candidate
_AMBIGUITY_GAP = 0.15           # top two within this → adjudicate
_EXACT = 1.0
_CONTAINMENT = 0.8
_MAX_ALTERNATES = 3
_FILLER = {"fee", "visit", "patient", "the", "a", "of", "for", "and", "per"}

ADJUDICATION_SYSTEM = (
    "You map one clinical-trial invoice line to the single best catalog item. "
    "Reply with JSON {\"catalog_item_id\": <id or null>, \"rationale\": <text>}."
)

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _normalize(text: str) -> str:
    cleaned = _PUNCT.sub(" ", text.lower())
    return " ".join(cleaned.split())


def _content_tokens(norm: str) -> set[str]:
    tokens = {t for t in norm.split() if t not in _FILLER}
    return tokens or set(norm.split())  # never empty if there was any text


def _similarity(line_norm: str, cat_norm: str) -> float:
    """Token-set similarity in [0, 1] with an exact/containment boost."""
    if line_norm == cat_norm:
        return _EXACT
    lt, ct = _content_tokens(line_norm), _content_tokens(cat_norm)
    if not lt or not ct:
        return 0.0
    jaccard = len(lt & ct) / len(lt | ct)
    if lt <= ct or ct <= lt:  # one is a subset of the other
        return max(jaccard, _CONTAINMENT)
    return round(jaccard, 3)


def match(
    line_items: list[LineItem],
    catalog: list[CatalogItem],
    llm: LLMClient | None = None,
) -> list[MatchResult]:
    scored_catalog = [(item, _normalize(item.description)) for item in catalog]
    return [_match_one(li, scored_catalog, llm) for li in line_items]


def _match_one(li: LineItem, scored_catalog, llm: LLMClient | None) -> MatchResult:
    line_norm = _normalize(li.raw_description)

    # Layer 2: ranked candidates above the floor.
    candidates = sorted(
        (
            (item, _similarity(line_norm, cat_norm))
            for item, cat_norm in scored_catalog
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    candidates = [(item, score) for item, score in candidates if score >= _CANDIDATE_FLOOR]

    if not candidates:
        return MatchResult(
            line_item_id=li.id,
            confidence=0.0,
            rationale="no catalog item matched this description",
            requires_exception_review=True,
            exceptions=["unmatched_line_item"],
        )

    best, best_score = candidates[0]
    rationale = f"matched to '{best.description}' (similarity {best_score:.2f})"

    # Layer 3: adjudicate genuinely ambiguous cases with the LLM (advisory).
    if llm is not None and _is_ambiguous(candidates):
        chosen, llm_rationale = _adjudicate(llm, li, candidates)
        if chosen is not None:
            best, best_score = chosen
            rationale = f"LLM adjudicated to '{best.description}': {llm_rationale}"

    # Layer 4: deterministic verification (gates regardless of semantic score).
    amount_match = _verify_amount(li, best)
    quantity_match = _verify_quantity(li, best)
    exceptions: list[str] = []
    confidence = best_score
    if amount_match is False:
        exceptions.append("amount_mismatch")
    if quantity_match is False:
        exceptions.append("quantity_mismatch")
    if exceptions:
        confidence = round(best_score * 0.5, 3)
        rationale += "; " + ", ".join(exceptions).replace("_", " ")

    alternates = [
        item.description for item, _ in candidates if item.id != best.id
    ][:_MAX_ALTERNATES]

    return MatchResult(
        line_item_id=li.id,
        catalog_item_id=best.id,
        catalog_description=best.description,
        confidence=confidence,
        amount_match=amount_match,
        quantity_match=quantity_match,
        rationale=rationale,
        requires_exception_review=bool(exceptions),
        alternates=alternates,
        exceptions=exceptions,
    )


def _is_ambiguous(candidates: list[tuple[CatalogItem, float]]) -> bool:
    """True when no confident exact match and the top two are close."""
    top_score = candidates[0][1]
    if top_score >= _EXACT:
        return False
    if len(candidates) < 2:
        return False
    return candidates[1][1] >= top_score - _AMBIGUITY_GAP


def _adjudicate(
    llm: LLMClient, li: LineItem, candidates: list[tuple[CatalogItem, float]]
) -> tuple[tuple[CatalogItem, float] | None, str]:
    """Ask the LLM to choose among candidates. Falls back to the top on any
    unusable response (returns ``(None, "")``)."""
    options = [
        {"catalog_item_id": item.id, "description": item.description}
        for item, _ in candidates
    ]
    user = json.dumps({"line_item": li.raw_description, "candidates": options})
    try:
        result = llm.complete_json(system=ADJUDICATION_SYSTEM, user=user)
    except ValueError:
        return None, ""
    chosen_id = result.get("catalog_item_id")
    for pair in candidates:
        if pair[0].id == chosen_id:
            return pair, str(result.get("rationale", "")).strip()
    return None, ""


def _verify_amount(li: LineItem, item: CatalogItem) -> bool | None:
    if item.unit_price is None or li.unit_price is None:
        return None
    return abs(item.unit_price - li.unit_price) <= _PRICE_TOLERANCE


def _verify_quantity(li: LineItem, item: CatalogItem) -> bool | None:
    """Check quantity·catalog_price reconciles with the line total (ARCHITECTURE §9)."""
    if item.unit_price is None or li.quantity is None or li.total is None:
        return None
    try:
        expected = Decimal(li.quantity) * item.unit_price
    except (InvalidOperation, TypeError):
        return None
    return abs(expected - li.total) <= _PRICE_TOLERANCE
