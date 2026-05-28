"""Vision LLM client + offline stand-in (P4-T3; OD-7).

Extends the provider-agnostic LLM seam (ARCHITECTURE.md §8) with a *vision*
method: given a rendered page image and the page's OCR words (each tagged with
its index), the model returns extracted values that cite the **word indices**
backing them — never raw coordinates. The citation resolver (P4-T4) then unions
those words' real OCR boxes into a highlight rectangle, so a value can only point
at words that actually exist on the page (the no-hallucination guarantee, spec §3).

``OfflineVisionLLMClient`` is the default: it synthesizes ``word_indices`` with no
model and no network by **string-matching** each extracted value against the OCR
words (the deterministic analogue of a model emitting indices, spec §7). A real
vision provider drops in behind ``VisionLLMClient`` (OD-7); nothing else changes.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from backend.clients.llm import parse_json_or_raise
from backend.domain import WordBox

VISION_SYSTEM = (
    "Extract clinical-trial invoice header metadata and line items from the page "
    "image. You are given the page's OCR words, each with an integer index. For "
    "every value you extract, return its 'value', the 'word_indices' of the OCR "
    "words that back it, the 1-based 'page', and a 'status' (extracted | "
    "unreadable). Cite indices only — never invent coordinates."
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Lowercase and strip non-alphanumerics for tolerant token comparison.

    Makes ``"INV-1001"`` match an OCR token ``"INV-1001"`` and ``"$1,234.00"``
    match ``"1234.00"`` without depending on punctuation/casing fidelity.
    """
    return _NON_ALNUM.sub("", text.lower())


def _tokens(value: object) -> list[str]:
    return [tok for tok in (_norm(part) for part in str(value).split()) if tok]


def locate_value(value: object, words: list[WordBox]) -> tuple[int, list[int]] | None:
    """Find the OCR words backing ``value`` → ``(page_number, word_indices)``.

    Prefers a contiguous run of words (per page) matching the value's token
    sequence; falls back to the page with the best per-token coverage. Returns
    ``None`` when no OCR word backs the value (caller marks it unreadable — no
    box). ``word_indices`` are 0-based within the returned page.
    """
    tokens = _tokens(value)
    if not tokens:
        return None

    pages: dict[int, list[WordBox]] = {}
    for word in words:
        pages.setdefault(word.page_number, []).append(word)

    # Exact contiguous window match (handles single- and multi-token values).
    width = len(tokens)
    for page, pwords in pages.items():
        norms = [_norm(w.text) for w in pwords]
        for start in range(len(norms) - width + 1):
            if norms[start : start + width] == tokens:
                return page, [pwords[start + k].index for k in range(width)]

    # Fallback: the page covering the most tokens, first index per matched token.
    best: tuple[int, list[int]] | None = None
    for page, pwords in pages.items():
        norms = [_norm(w.text) for w in pwords]
        indices: list[int] = []
        for token in tokens:
            for position, norm in enumerate(norms):
                if norm == token:
                    indices.append(pwords[position].index)
                    break
        if indices and (best is None or len(indices) > len(best[1])):
            best = (page, indices)
    return best


@runtime_checkable
class VisionLLMClient(Protocol):
    def complete_vision_json(
        self, *, system: str, user: str, image: bytes, words: list[WordBox]
    ) -> dict:
        """Return extracted values annotated with ``word_indices`` + ``status``.

        Implementations must raise ``ValueError`` on non-JSON output so malformed
        responses never flow downstream (ARCHITECTURE.md §8).
        """
        ...


def _annotate(value: object, words: list[WordBox], base: dict | None = None) -> dict:
    """Build a metadata-field annotation ``{value, word_indices, page, status}``."""
    out = dict(base) if base else {}
    out["value"] = value
    out.update(_locate_annotation(value, words))
    return out


def _locate_annotation(value: object, words: list[WordBox]) -> dict:
    if value in (None, ""):
        return {"word_indices": [], "page": 1, "status": "missing"}
    located = locate_value(value, words)
    if located is None:
        return {"word_indices": [], "page": 1, "status": "unreadable"}
    page, indices = located
    return {"word_indices": indices, "page": page, "status": "extracted"}


class OfflineVisionLLMClient:
    """Offline vision stand-in: word-index citations with no model/network (spec §7).

    Reads the values embedded in the prompt (the parser renders the document as
    JSON, as the text path's ``PassthroughLLMClient`` does) and string-matches each
    against the OCR ``words`` to synthesize ``word_indices`` + ``status``. The page
    image is accepted for interface parity but unused. Returns ``{}`` when the
    prompt is not JSON.
    """

    def complete_vision_json(
        self, *, system: str, user: str, image: bytes, words: list[WordBox]
    ) -> dict:
        try:
            doc = parse_json_or_raise(user)
        except ValueError:
            return {}
        words = words or []

        metadata: dict[str, object] = {}
        for field, cell in (doc.get("metadata") or {}).items():
            base = cell if isinstance(cell, dict) else None
            value = cell.get("value") if isinstance(cell, dict) else cell
            metadata[field] = _annotate(value, words, base)

        line_items: list[dict] = []
        for item in doc.get("line_items") or []:
            out = dict(item)
            out["description_citation"] = _locate_annotation(item.get("raw_description"), words)
            line_items.append(out)

        return {"metadata": metadata, "line_items": line_items}


class StubVisionLLMClient:
    """Deterministic scripted stub for tests (mirrors ``StubLLMClient``).

    Pass ``responses`` to script successive ``complete_vision_json`` returns; once
    exhausted it falls back to ``default``. Every call is recorded in ``calls``.
    """

    def __init__(
        self,
        responses: list[dict] | None = None,
        default: dict | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._default = default if default is not None else {}
        self.calls: list[dict] = []

    def complete_vision_json(
        self, *, system: str, user: str, image: bytes, words: list[WordBox]
    ) -> dict:
        self.calls.append({"system": system, "user": user, "words": words})
        if self._responses:
            return self._responses.pop(0)
        return dict(self._default)
