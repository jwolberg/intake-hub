# Spec — Visual Document Review (Image + Bounding-Box Overlay Extraction)

Status: **Approved / unblocked** (2026-05-27) — the gating PRD §4/§10 +
ARCHITECTURE §4/§8/§12/§13 amendments are applied. Target: **Phase 4** in
`/docs/BUILD_PLAN.md` (start at P4-T1).

## 1. Problem & user value

Today the reviewer hub shows *what the AI extracted* as text, plus a collapsible
**text** preview of the parsed source (`source_text`). It cannot show the
**original document image**, and it cannot show **where on the page** each
extracted value came from. PRD §10 (Invoice Detail → Source) calls for an
"Original invoice preview or download link," and the Extracted-Metadata section
calls for "Source evidence" per field — currently a text string, not a visual
locus.

This feature lets a reviewer **see the original page image with the AI's
extracted fields and line items highlighted as rectangles**, linked both ways to
the detail view: hover/click a field → its box highlights on the page; click a
box → the field focuses. Uncertain fields are visually distinct and gate clean
approval. This turns post-decision QC (USERS § Reviewer, § Exception Handler)
from "trust the text" into "verify against the source in one glance," and makes
corrections (P2-C3) far faster and more confident.

Reference implementation studied (read-only, not vendored): the OpenEMR
`oe-module-clinical-copilot` module's OCR → vision-extraction → SVG-overlay
review pipeline. We adapt its **word-index citation** design (below) to
InvoiceScreener's Python/FastAPI + React stack and existing pipeline.

## 2. Scope & Doc Change (read this first)

This **expands scope** and **revises a stated PRD non-goal**, so per the build
plan's Update Rules ("Do NOT add new scope unless one of the REQUIRED input
documents changes") it must be recorded as a deliberate requirement change:

- **PRD §4 Non-Goals** currently includes *"Build perfect OCR or document
  intelligence."* This feature introduces **real OCR + vision extraction**. We do
  **not** repeal the non-goal wholesale; we narrow it: *we do not promise perfect
  OCR/document intelligence on arbitrary scans, but we do provide OCR + vision
  extraction with **human-verifiable, source-anchored highlights** so that
  imperfect extraction is caught by a reviewer rather than trusted blindly.* The
  controlled-format real-PDF demo path (RUNBOOK Path D) is the initial substrate.
- **Doc edits applied (2026-05-27):** PRD §4 narrowed to the wording above; PRD §10
  Source + Extracted-Metadata gained the page-image/overlay + visual-highlight
  elements; ARCHITECTURE §4 (the `ocr` module + parser/extraction notes), §8
  (vision extraction + word-index citations), §12 (the `Citation`/`BoundingBox`
  data model), and §13 (page-image endpoints) updated. Phase 4 is unblocked.

**In scope:** page rasterization + serving; OCR word boxes; vision extraction
that cites OCR word indices; server-side resolution to normalized bounding boxes;
persistence + API surfacing; the reviewer overlay UI; confirm/correct from the
overlay (reusing P2-C3 QC + P2-C4 rerun).

**Out of scope (still):** guaranteeing extraction accuracy on arbitrary/low-quality
scans; handwriting; multi-language OCR tuning; redaction/PHI; training custom
models. Offline tests must keep passing with no network and no model (§7).

## 3. The pattern we are adapting (word-index citations)

From the reference module, the robust-against-hallucination pipeline is:

1. **Rasterize** each page to a fixed-resolution image (normalized later to `[0,1]`).
2. **OCR** each page → a list of **word boxes**: `{index, text, bbox{x,y,w,h}}`
   in normalized `[0,1]` coords (Tesseract TSV → normalized).
3. **Vision extract**: the model receives the page image **and** the OCR word
   list (each word tagged with its integer `index`). For every field/line it
   emits a value, a `status`, and a `citation` carrying **`word_indices`** — the
   indices of the OCR words that back the value — **never raw coordinates**.
4. **Resolve**: the server unions the cited words' boxes into one `bbox`
   (`min x/y`, `max x+w / y+h`), dropping out-of-range indices defensively.
5. **Persist** per-field/per-line citations `{page, target_id, quote, bbox,
   status}`.
6. **Overlay**: render the page image and an SVG (`viewBox="0 0 1 1"`,
   `preserveAspectRatio="none"`) with one `<rect>` per citation. Because both the
   bboxes and the SVG are in normalized space, no scaling math is needed and the
   browser handles screen scaling. Hover/click links fields ↔ rects.

Why word indices instead of coordinates: the model cannot hallucinate a box — it
can only point at OCR words that actually exist, and the box is computed from real
OCR geometry. This is the crux of making the highlights trustworthy.

## 4. Architecture (InvoiceScreener adaptation)

Maps each reference component to an InvoiceScreener seam. We reuse existing
interfaces (`LLMClient`, the clients layer, the staged pipeline, the detail
payload, P2-C3 corrections, the audit trail) rather than inventing parallel ones.

### 4.1 Backend components

| Reference (PHP) | InvoiceScreener (Python) | Notes |
|---|---|---|
| `ImagickPageImageProducer` + `page-image.php` | `backend/parser/raster.py` (`render_pages`) + `GET /api/invoices/:id/pages/:n/image` | PyMuPDF/`pdf2image` for PDF; Pillow for images (OD-8). |
| `TesseractPageWordExtractor` + `TesseractTsvParser` | `backend/ocr/` (`OCRClient` protocol; `TesseractOCRClient`; `StubOCRClient`) | New clients-style seam; offline stub (OD-6). |
| `OpenAiLlmVisionClient` | extend `LLMClient` with `complete_vision_json(...)` or add `VisionLLMClient` | Behind the existing OD-2 interface; offline default (§7). |
| `LabExtractionPrompt` | `backend/extraction/prompts.py` (vision prompt + schema) | Embeds OCR word list; requests `word_indices`. |
| `CitationBboxAttacher` + `WordBoxResolver` | `backend/extraction/citations.py` (`resolve_citations`) | Union of cited word boxes; drop out-of-range. |
| `DbalCitationRepository` + citation table | `citations` rows (or JSONB on the extraction signals already stored on the `extracted` audit event) | Prefer extending the existing detail payload; persist via the Repository. |
| `ReviewService` approve/reject | reuse **P2-C3** correction/reviewed/escalate routes + **P2-C4** rerun | No new approval engine; confirm/reject ties to existing QC + audit. |

### 4.2 Domain model additions (`backend/domain/`)

```text
BoundingBox      { x: float, y: float, width: float, height: float }   # all [0,1]
WordBox          { page_number: int, index: int, text: str, bbox: BoundingBox }
Citation         { page_number: int, target_id: str, quote: str,
                   bbox: BoundingBox | None, status: CitationStatus }
CitationStatus   = extracted | uncertain | unreadable | missing
```

`target_id` keys a citation to an extracted thing: metadata fields use
`"metadata.<field>"` (e.g. `metadata.invoice_number`); line items use
`"line_item.<id>.<attr>"` (e.g. `line_item.line_abc.raw_description`). This reuses
the existing per-field signal shape from P2-A1/P2-C2 (`field_confidence`,
`field_evidence`) — citations are the *visual* counterpart of `field_evidence`.
`ExtractionResult` gains `citations: list[Citation]` and pages gain dimensions.

### 4.3 Data flow

```
intake → parser(render_pages + raster) → ocr(word boxes) →
extraction(vision: image + words → values + word_indices) →
citations(resolve word_indices → bbox) → [context → catalog → matching → decision …]
                                          ↑ unchanged downstream
detail payload adds: pages[], citations[]  →  hub Source section renders image + SVG overlay
```

OCR + vision-extraction slot **between parse and the rest of the pipeline**;
everything downstream (context, catalog, matching, decision, QC, rerun, metrics)
is unchanged. Citations are additive metadata on the extraction.

### 4.4 API additions (`backend/api/`)

- `GET /api/invoices/:id/pages` → `[{page_number, width, height}]` (page count + raster dims).
- `GET /api/invoices/:id/pages/:n/image` → the rendered page raster (PNG/JPEG bytes).
- `GET /api/invoices/:id` detail payload gains `pages` + `citations` (grouped by page).

### 4.5 Frontend (`frontend/src/components/InvoiceDetail.jsx` Source section)

- Render the page image inside a positioned container; overlay an
  `<svg viewBox="0 0 1 1" preserveAspectRatio="none">` with a `<rect>` per
  citation. Box color by `status` (extracted = teal, uncertain = amber, etc. —
  reuse the ClinRun palette tokens).
- Two-way linking: hovering/selecting a metadata row or line item adds an
  `is-highlight` class to the matching rect (`data-target-id`); clicking a rect
  scrolls/focuses the corresponding field.
- Multi-page: one image+overlay per page, citations grouped by `page_number`.

## 5. Acceptance criteria

- Opening an invoice with a rasterizable source shows the **page image(s)** in the
  Source section (PRD §10 "Original invoice preview"), with a download link.
- Each extracted metadata field and line item with a citation shows a **rectangle
  on the page** at the cited location; hovering the field highlights its box and
  vice versa (PRD §10 Source evidence; FR9).
- Boxes are computed from **real OCR word geometry** via cited indices — a field
  with no backing OCR words has **no** box (no hallucinated highlights).
- **Uncertain** fields are visually distinct and must be confirmed/rejected before
  the invoice can be cleanly marked reviewed; confirm/reject/correct reuse the
  P2-C3 QC routes and record **human** audit events (FR10; ARCHITECTURE §11/§17).
- Correcting a value from the overlay and **rerunning** (P2-C4) recomputes
  downstream stages, exactly as today.
- **Offline path intact**: with the stub OCR + offline extraction stand-in, the
  full suite runs with no network and citations are still produced for the
  controlled sample PDFs (§7).

## 6. Open decisions

- **OD-6 — OCR engine.** Provisional: **Tesseract** (local binary) behind an
  `OCRClient` interface, with a `StubOCRClient` for offline tests. Alternatives
  (cloud Textract/Vision) swap behind the interface.
- **OD-7 — Vision LLM provider.** Extends **OD-2**: a vision-capable provider
  behind `LLMClient`/`VisionLLMClient`. Default remains an **offline stand-in**.
- **OD-8 — Rasterization library.** Provisional: **PyMuPDF (`fitz`)** for PDF →
  image and Pillow for image inputs; `pdf2image`+poppler is the fallback.
- **OD-9 — Citation persistence.** Provisional: surface citations in the detail
  payload and persist alongside the existing per-field extraction signals (on the
  `extracted` audit event / Repository), avoiding a new table until Postgres is
  exercised; promote to a `citations` table if querying demands it.

New dependencies (justified by the feature; flagged per engineering standards):
Tesseract binary + a Python OCR wrapper, a raster library, and an optional vision
API client. All are isolated behind the OCR/LLM client interfaces and stubbed for
offline tests.

## 7. Offline-first constraint (critical)

The current dev/test path is fully offline (PassthroughLLMClient, stub MCP/ClinRun,
no network — 106 tests, in-process). This feature must preserve that:

- `StubOCRClient` returns canned word boxes for the controlled sample PDFs
  (RUNBOOK Path D, `samples/generate_pdfs.py`) — we render those PDFs ourselves, so
  their word geometry is known/deterministic.
- The **offline extraction stand-in** synthesizes citations *without a vision
  model* by string-matching each extracted value to OCR words (the deterministic
  analogue of the model emitting `word_indices`). This keeps tests deterministic
  and network-free; a real vision provider (OD-7) is a drop-in upgrade.

## 8. Risks / tradeoffs

- **Accuracy on arbitrary scans** — mitigated by scope (controlled PDFs first) +
  the human-verifiable overlay; the non-goal is narrowed, not erased (§2).
- **New heavy deps** (Tesseract, raster lib) — isolated behind interfaces; offline
  stubs keep CI light.
- **Coordinate drift** between OCR-resolution and display-resolution rasters —
  avoided by normalizing all boxes to `[0,1]` and using `viewBox="0 0 1 1"`.
- **Token cost / latency** of vision calls — the OCR-word-list grounding lets the
  model see a low-detail image (cheaper) while keeping word-level precision.
- **Postgres untested locally** — OD-9 keeps citations in the existing detail/
  audit path until the live-stack gate work lands a citations table.

## 9. Tickets (mirrors Phase 4 in BUILD_PLAN.md)

- **P4-T1** Page rasterization + image serving.
- **P4-T2** OCR word-box extraction (`OCRClient` + Tesseract + stub).
- **P4-T3** Vision extraction with word-index citations (LLM vision + offline stand-in).
- **P4-T4** Citation → bbox resolution + persistence + detail payload.
- **P4-T5** Reviewer overlay UI (image + SVG boxes, two-way linking).
- **P4-T6** Confirm/correct/approve from the overlay (reuse P2-C3 QC + P2-C4 rerun).
