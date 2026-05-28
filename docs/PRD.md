# PRD: AI-First Clinical Trial Invoice Intake, Matching, and Decisioning Workflow

## 1. Product Summary

Clinical trial site invoices arrive through email in inconsistent formats, terminology, and structures. This creates operational delays, manual triage burden, and avoidable errors when invoice context or billable line items are misidentified.

This product will provide an AI-first invoice processing workflow that ingests emailed invoices, extracts invoice metadata and line items, resolves sponsor/study/site context, matches billable items against the appropriate catalog, and decides whether the invoice should be submitted to ClinRun or held for exception handling.

The key product principle is:

> Humans are not a required gate before matching and decisioning. The system should process invoices end to end first, then provide clear post-decision QC visibility so reviewers can understand outcomes, validate risk cases, and intervene when needed.

The product should demonstrate reliable orchestration, explainable decision behavior, ambiguity handling, and operational review surfaces rather than perfect document intelligence.

## 2. Problem Statement

Manual invoice triage and line-item matching do not scale as clinical trial study volume increases. Invoices vary widely by site, sponsor, terminology, layout, and billing style. This causes:

- Slow invoice processing
- Repeated human review of routine invoices
- Mismatched sponsor, study, or site context
- Incorrect billable item matching
- Rework caused by unclear invoice interpretation
- Operational bottlenecks before submission to ClinRun

The opportunity is to automate invoice intake, interpretation, matching, and submit/hold decisioning while preserving operational control through transparent review and correction workflows.

## 3. Goals

### Primary Goal

Build an AI-first workflow that can process sample clinical trial site invoices end to end and decide whether each invoice should be submitted to ClinRun or held for exception handling.

### Secondary Goals

- Make AI decisions explainable to reviewers.
- Surface extracted metadata, matched line items, confidence, and exceptions.
- Allow human QC after AI processing, not before it.
- Support correction, rerun, escalation, and auditability.
- Demonstrate robustness across easy, ambiguous, large, and mismatched-metadata invoices.

## 4. Non-Goals

This project is not expected to:

- Guarantee perfect OCR or document intelligence on arbitrary documents.
  *(Narrowed 2026-05-27 to unblock Phase 4 — Visual Document Review,
  `/docs/specs/visual-document-review.md`. The system now performs OCR + vision
  extraction and overlays the page image with **human-verifiable,
  source-anchored highlights** (see §10), initially on controlled-format PDFs. It
  still makes **no accuracy guarantee on arbitrary or low-quality scans,
  handwriting, or multi-language documents** — imperfect extraction is surfaced
  for a reviewer to catch, never trusted blindly.)*
- Support every possible invoice format globally.
- Replace all finance or compliance workflows.
- Build a production-scale email ingestion system.
- Implement full ClinRun production integration unless a mock or provided backend is available.
- Guarantee 100% correct matching in ambiguous cases.

The expected solution should prioritize practical orchestration, explainability, and robust exception handling.

## 5. Users

### Primary User: Invoice Operations Reviewer

A reviewer responsible for validating invoice processing outcomes after AI decisioning.

They need to quickly answer:

- What invoice was received?
- What metadata did the AI extract?
- What sponsor, study, and site did the AI resolve?
- What line items were found?
- What catalog items were matched?
- What confidence or exceptions were identified?
- Was the invoice submitted or held?
- What action, if any, is needed?

### Secondary User: Operations Lead

A manager responsible for throughput, quality, and exception visibility.

They need to see:

- Processing status across invoices
- Submitted vs. held invoices
- Exception patterns
- Failure modes
- Audit trail of AI and human decisions

### System Actor: AI Workflow Orchestrator

The automated pipeline that performs:

- Email intake
- Attachment parsing
- LLM extraction
- Context resolution
- Catalog retrieval
- Line-item matching
- Submit/hold decisioning
- Workflow state tracking

## 6. Core Product Principle

The system should follow an AI-first, human-after workflow.

### Required Behavior

The AI pipeline must attempt to fully process an invoice before requiring human input.

Human review should occur after the AI has already:

- Extracted invoice data
- Resolved context
- Matched line items
- Made a submit/hold decision
- Logged rationale and confidence

Humans should review outcomes, not perform first-pass triage.

## 7. End-to-End Workflow

### Step 1: Email Intake

The system receives or simulates receipt of invoice emails.

Each email may include:

- PDF invoice attachment
- Image or scanned document
- Email body with invoice details
- Sender metadata
- Subject-line clues
- Inconsistent terminology

For hackathon/demo purposes, ingestion may be implemented as:

- Local sample invoice upload
- Seeded email-like records
- Mock inbox
- IMAP/Gmail integration if time allows

### Step 2: Document Parsing

The system extracts text and structural content from the invoice.

Expected output:

- Raw text
- Parsed document sections when available
- Attachment metadata
- Source file reference

### Step 3: LLM Extraction

The LLM extracts structured invoice information.

Required extracted metadata:

- Invoice number
- Invoice date
- Due date, if available
- Vendor/site name
- Sponsor name, if available
- Study name or protocol number
- Site identifier, if available
- Billing period, if available
- Currency
- Total amount
- Tax, if present
- Payment terms, if present

Required extracted line items:

- Description
- Quantity
- Unit price
- Total price
- Date or service period, if available
- Raw source text
- Normalized label or inferred billing category
- Extraction confidence

### Step 4: Context Resolution

The system resolves the correct business context using the MCP-wrapped reference API.

Required context entities:

- Sponsor
- Study
- Site

The system should use available clues from:

- Invoice metadata
- Site/vendor name
- Sponsor name
- Study/protocol number
- Email sender
- Invoice line-item language
- Known reference API records

Context resolution output should include:

- Resolved sponsor ID
- Resolved study ID
- Resolved site ID
- Resolution confidence
- Candidate alternatives, if ambiguous
- Reasoning summary
- Any unresolved or conflicting fields

### Step 5: Catalog Retrieval

Once sponsor and study context are resolved, the system fetches the sponsor+study-scoped billable item catalog.

Catalog data should be used to match invoice line items against approved billable items.

The catalog retrieval stage should handle:

- Large catalogs
- Missing catalogs
- Empty result sets
- API errors
- Sponsor/study mismatch
- Multiple possible catalog matches

### Step 6: Line-Item Matching

Each extracted invoice line item should be matched against the scoped catalog.

Matching may use:

- Semantic similarity
- Terminology normalization
- LLM reasoning
- Deterministic rules
- Amount checks
- Quantity/unit checks
- Fuzzy matching

Each match should produce:

- Extracted invoice item
- Best catalog match
- Match confidence
- Match rationale
- Amount comparison
- Quantity comparison
- Whether the item appears billable
- Whether the item requires exception review
- Alternate candidate matches, if relevant

### Step 7: AI Submit/Hold Decision

The system makes a final AI-driven decision:

- Submit to ClinRun
- Hold for exception handling

The decision should be based on:

- Context resolution confidence
- Line-item match confidence
- Missing required fields
- Amount mismatches
- Catalog mismatches
- Sponsor/study/site ambiguity
- Unsupported invoice format
- Duplicate invoice risk, if detectable
- Backend/API failures
- Low-confidence extraction

The system should not require human approval before making this decision.

### Step 8: Backend Submission or Hold

If decision is submit, the system sends structured invoice data to the ClinRun backend or mock backend.

If decision is hold, the system creates an exception record with clear reasons.

Each invoice should have a final processing status:

- Received
- Extracted
- Context resolved
- Catalog matched
- Submitted
- Held for exception
- Failed
- Rerun requested
- Corrected
- Escalated

### Step 9: Reviewer Hub

The hub provides post-decision visibility.

For each invoice, reviewers should see:

- Original invoice/email reference
- Extracted metadata
- Extracted line items
- Resolved sponsor/study/site
- Catalog matches
- Match confidence
- Exceptions
- AI rationale
- Submit/hold decision
- Submission status
- Human QC actions
- Processing timeline

## 8. Functional Requirements

### FR1: Invoice Intake

The system must ingest or simulate ingestion of provided sample invoices.

Acceptance criteria:

- User can process all provided sample invoices.
- Each invoice gets a unique workflow record.
- The system stores original source metadata and parsed document text.
- Failures are visible and do not crash the overall workflow.

### FR2: LLM-Based Metadata and Line-Item Extraction

The system must use an LLM to extract structured metadata and invoice line items.

Acceptance criteria:

- Extracted data is shown in structured form.
- Each line item preserves source text.
- Missing or uncertain fields are explicitly marked.
- Extraction confidence is captured.
- Extraction output is validated against a schema before downstream use.

### FR3: Sponsor/Study/Site Context Resolution

The system must resolve invoice context using the MCP-wrapped reference API.

Acceptance criteria:

- The system attempts sponsor, study, and site resolution for each invoice.
- Resolved IDs are stored with confidence scores.
- Ambiguous candidates are surfaced.
- Low-confidence or conflicting metadata creates an exception.
- Context resolution does not depend on human pre-review.

### FR4: Sponsor+Study-Scoped Catalog Fetching

The system must fetch the relevant billable catalog after context resolution.

Acceptance criteria:

- Catalog is scoped by resolved sponsor and study.
- Catalog fetch failures are handled gracefully.
- Large catalog responses do not break processing.
- Retrieved catalog items are available for matching and reviewer inspection.

### FR5: Line-Item Matching

The system must match extracted invoice line items to catalog items.

Acceptance criteria:

- Each extracted line item has a match result.
- Match result includes catalog item, confidence, and rationale.
- Mismatched or unmatched items are marked clearly.
- Amount/quantity discrepancies are flagged.
- Matching works across simple, medium, large, and ambiguous catalog cases.

### FR6: AI Submit/Hold Decisioning

The system must decide whether to submit the invoice or hold it.

Acceptance criteria:

- Every processed invoice receives a submit or hold decision unless there is a system failure.
- Decision includes rationale.
- Held invoices include explicit exception reasons.
- Submitted invoices include backend submission status.
- Decisioning runs before human QC.

### FR7: ClinRun Submission

The system must submit approved invoices to ClinRun or a mock ClinRun backend.

Acceptance criteria:

- Submitted invoice payload includes resolved context, metadata, and matched line items.
- Submission result is stored.
- Failed submission creates a retryable error state.
- Submitted status is visible in the hub.

### FR8: Exception Handling

The system must hold invoices that cannot be confidently submitted.

Hold reasons may include:

- Unresolved sponsor
- Unresolved study
- Unresolved site
- Multiple likely context candidates
- Missing invoice number
- Missing total
- Unmatched line items
- Amount mismatch
- Catalog unavailable
- Low extraction confidence
- Backend submission failure
- Mismatched metadata between invoice and reference data

Acceptance criteria:

- Exception reason is specific and visible.
- Reviewer can understand why the invoice was held.
- Held invoices are available for correction, rerun, or escalation.

### FR9: Reviewer Hub

The system must provide a UI hub for reviewing AI-processed invoices.

Acceptance criteria — the hub shows:

- Invoice list
- Processing status
- Submit/hold decision
- Extracted metadata
- Resolved sponsor/study/site
- Line-item matches
- Confidence scores
- Exception reasons
- AI rationale
- Submission result
- QC actions

### FR10: Human QC Actions

Reviewers must be able to take post-decision actions.

Required actions:

- Mark reviewed
- Correct extracted metadata
- Correct line-item match
- Rerun processing
- Escalate invoice
- Override hold/submit decision, if implemented
- Add reviewer note

Acceptance criteria:

- Human actions are recorded.
- Corrections are reflected in invoice state.
- Rerun uses corrected data where applicable.
- Audit trail distinguishes AI decisions from human edits.

### FR11: Workflow State and Observability

The system must expose workflow state transitions for debugging and demo clarity.

Acceptance criteria:

- Each stage has a visible status.
- Errors are captured with stage name and message.
- Retryable failures are distinguishable from terminal failures.
- Logs or traces make it clear how the invoice moved through the pipeline.

## 9. AI Decisioning Requirements

The AI decision should produce a structured output such as:

```json
{
  "decision": "submit" | "hold",
  "confidence": 0.0,
  "rationale": "Short explanation of why this invoice was submitted or held.",
  "risk_flags": [
    {
      "type": "context_ambiguity",
      "severity": "medium",
      "message": "Two possible sites matched the invoice metadata."
    }
  ],
  "required_human_actions": [
    "Confirm correct site before submission"
  ]
}
```

### Submit Criteria

An invoice may be submitted when:

- Sponsor, study, and site are confidently resolved.
- Required invoice metadata is present.
- All material line items are matched to catalog items.
- Amounts are consistent or within accepted tolerance.
- No high-severity exception is present.
- Backend submission succeeds.

### Hold Criteria

An invoice should be held when:

- Context resolution is missing or ambiguous.
- One or more material line items cannot be matched.
- Invoice total conflicts with line-item totals.
- Catalog lookup fails.
- Extracted metadata contradicts reference API data.
- The AI cannot explain the decision confidently.
- Any high-severity risk flag is present.

## 10. Reviewer Hub UX Requirements

### Invoice List View

The list should include:

- Invoice number
- Site/vendor
- Sponsor
- Study
- Total amount
- Current status
- Submit/hold decision
- Confidence
- Exception count
- Last updated timestamp

Suggested filters:

- Submitted
- Held
- Failed
- Needs review
- Low confidence
- Mismatched metadata
- Unmatched line items

### Invoice Detail View

The detail view should include sections for:

**Source**

- Email subject
- Sender
- Received timestamp
- Attachment name
- Original invoice preview or download link
- Rendered page image(s) of the document with **bounding-box highlights** for each
  extracted field/line item, anchored to where the value appears on the page
  (Phase 4 — Visual Document Review). Highlights are status-coded (extracted /
  uncertain / unreadable) and computed from real OCR geometry, so a field with no
  backing OCR text shows no box. *(See `/docs/specs/visual-document-review.md`.)*

**Extracted Metadata**

Display extracted fields with:

- Value
- Confidence
- Source evidence — both the text snippet and, when available, a **visual
  highlight** on the page image; hovering/selecting a field highlights its box and
  selecting a box focuses the field (Phase 4).
- Editable correction field — uncertain fields are visually distinct and must be
  confirmed or corrected (reusing the QC actions below) before a clean "reviewed".

**Context Resolution**

Display:

- Resolved sponsor
- Resolved study
- Resolved site
- Confidence
- Candidate alternatives
- Mismatch warnings

**Line-Item Matching**

Display each line item with:

- Raw invoice description
- Normalized description
- Amount
- Quantity
- Matched catalog item
- Match confidence
- Match rationale
- Exception flags

**Decision**

Display:

- Submit or hold
- Decision confidence
- Rationale
- Risk flags
- Backend submission status

**QC Actions**

Allow reviewer to:

- Mark reviewed
- Correct metadata
- Correct match
- Rerun
- Escalate
- Add note

## 11. Data Model

### Invoice

```json
{
  "id": "invoice_123",
  "source": "email",
  "status": "submitted | held | failed | needs_review",
  "decision": "submit | hold",
  "decisionConfidence": 0.92,
  "invoiceNumber": "INV-1001",
  "invoiceDate": "2026-05-20",
  "dueDate": "2026-06-20",
  "vendorName": "Site ABC",
  "currency": "USD",
  "totalAmount": 1200.00,
  "createdAt": "2026-05-26T10:00:00Z",
  "updatedAt": "2026-05-26T10:05:00Z"
}
```

### Resolved Context

```json
{
  "invoiceId": "invoice_123",
  "sponsorId": "sponsor_001",
  "studyId": "study_001",
  "siteId": "site_001",
  "confidence": 0.94,
  "candidates": [],
  "warnings": []
}
```

### Line Item

```json
{
  "id": "line_001",
  "invoiceId": "invoice_123",
  "rawDescription": "Patient screening visit",
  "normalizedDescription": "Screening Visit",
  "quantity": 2,
  "unitPrice": 300.00,
  "total": 600.00,
  "extractionConfidence": 0.96
}
```

### Match Result

```json
{
  "lineItemId": "line_001",
  "catalogItemId": "catalog_789",
  "catalogDescription": "Screening Visit",
  "confidence": 0.91,
  "amountMatch": true,
  "quantityMatch": true,
  "rationale": "Invoice description maps directly to catalog screening visit item.",
  "exceptions": []
}
```

### Exception

```json
{
  "invoiceId": "invoice_123",
  "type": "unmatched_line_item",
  "severity": "high",
  "message": "Line item could not be matched to sponsor-study catalog.",
  "createdAt": "2026-05-26T10:05:00Z"
}
```

### Audit Event

```json
{
  "invoiceId": "invoice_123",
  "actor": "ai | human",
  "action": "extracted | matched | submitted | held | corrected | rerun | escalated",
  "details": {},
  "timestamp": "2026-05-26T10:06:00Z"
}
```

## 12. System Architecture

### Recommended Architecture

Use a staged pipeline with clear boundaries:

1. Email/Invoice Intake Service
2. Document Parser
3. LLM Extraction Service
4. Context Resolver
5. Catalog Client
6. Line-Item Matcher
7. Decision Engine
8. Submission Client
9. Workflow State Store
10. Reviewer Hub

### Suggested Local-First Stack

Any modern framework is acceptable. A strong hackathon implementation could use:

- **Backend:** Python/FastAPI or TypeScript/Node
- **Frontend:** React/Vite
- **Database:** PostgreSQL
- **Queue/worker:** simple background job, Celery, BullMQ, or local async worker
- **LLM:** OpenAI or equivalent
- **Deployment:** Docker Compose
- **Tests:** pytest, vitest, or jest

### Required Separation

The code should clearly separate:

- Extraction
- Context resolution
- Catalog retrieval
- Matching
- Decisioning
- Submission
- UI/review actions

## 13. API Requirements

### Process Invoice

`POST /api/invoices/process`

Starts processing for a sample invoice.

### Get Invoices

`GET /api/invoices`

Returns invoice list with status, decision, confidence, and exception summary.

### Get Invoice Detail

`GET /api/invoices/:id`

Returns source, extraction, context, matches, decision, exceptions, and audit trail.

### Correct Metadata

`POST /api/invoices/:id/corrections/metadata`

Allows reviewer to correct extracted invoice metadata.

### Correct Line Match

`POST /api/invoices/:id/corrections/line-item`

Allows reviewer to correct a catalog match.

### Rerun Invoice

`POST /api/invoices/:id/rerun`

Reruns processing using original or corrected data.

### Mark Reviewed

`POST /api/invoices/:id/reviewed`

Marks invoice as reviewed after AI decisioning.

### Escalate Invoice

`POST /api/invoices/:id/escalate`

Escalates invoice for manual exception handling.

## 14. Performance and Reliability Requirements

### Performance Benchmarks

The system should demonstrate:

- Stable end-to-end processing on simple invoices.
- Stable processing on medium and large invoices.
- Stable handling of mismatched-metadata samples.
- Reliable matching against larger sponsor-study catalogs.
- Responsive reviewer interaction for core read/review actions.
- Predictable retry/recovery behavior on stage failure.

### Expected Behavior

- One failed invoice should not stop processing of other invoices.
- Failed stages should record clear error state.
- Low-confidence outcomes should result in hold decisions, not silent submission.
- Reviewer hub should remain usable even when some workflows fail.

## 15. Exception Handling Requirements

The system must handle the following scenarios:

### Easy Invoice

Expected behavior:

- Extract metadata
- Resolve context
- Match line items
- Submit invoice
- Show high-confidence decision

### Ambiguous Metadata Invoice

Expected behavior:

- Extract available fields
- Identify ambiguous sponsor/study/site candidates
- Hold invoice
- Show candidate conflicts and reason for hold

### Mismatched Metadata Invoice

Expected behavior:

- Detect mismatch between invoice content and reference API data
- Hold invoice
- Surface specific mismatch

Example:

> Invoice references Sponsor A but protocol number maps to Sponsor B.

### Large Invoice

Expected behavior:

- Process all line items
- Match against larger catalog
- Avoid workflow failure
- Hold only if material ambiguity or mismatch exists

### Catalog Failure

Expected behavior:

- Mark catalog stage as failed
- Hold invoice
- Provide retry option

### LLM Extraction Failure

Expected behavior:

- Mark extraction as failed
- Do not proceed to submission
- Provide retry option

## 16. Confidence and Risk Model

Each stage should produce a confidence score.

Suggested stage-level confidence:

- Extraction confidence
- Context resolution confidence
- Catalog match confidence
- Decision confidence

Suggested severity levels:

- **Low:** informational issue, does not block submission
- **Medium:** requires visibility, may still submit depending on policy
- **High:** blocks submission and creates hold decision

Suggested default decision policy:

| Condition | Decision |
| --- | --- |
| High-confidence context + all material items matched | Submit |
| Missing sponsor/study/site | Hold |
| Multiple plausible sites | Hold |
| Unmatched material line item | Hold |
| Total mismatch | Hold |
| Catalog unavailable | Hold |
| Backend submission failure | Hold or retryable failure |
| Low extraction confidence | Hold |

## 17. Auditability Requirements

The system must preserve an audit trail of:

- Source invoice intake
- Extracted values
- LLM extraction output
- Reference API lookup
- Catalog retrieval
- Match results
- AI decision
- Submission attempt
- Human corrections
- Reruns
- Escalations
- Final status

Each audit event should include:

- Timestamp
- Actor: AI, system, or human
- Action
- Before/after values when relevant
- Reason or note

## 18. Testing Requirements

### Unit Tests

Cover:

- Extraction schema validation
- Context resolution logic
- Catalog matching logic
- Decision policy
- Exception creation
- State transitions

### Integration Tests

Cover:

- End-to-end processing of sample invoices
- MCP reference API interaction
- Catalog retrieval
- Submit/hold behavior
- Retry handling

### Scenario Tests

Required sample scenarios:

- Simple invoice with clear metadata
- Medium invoice with multiple line items
- Large invoice with larger catalog matching
- Ambiguous sponsor/study/site metadata
- Mismatched metadata
- Unmatched line item
- Failed catalog fetch
- Failed backend submission

## 19. Demo Requirements

The demo should clearly show:

- Invoices entering the system.
- AI extraction output.
- Context resolution against the reference API.
- Catalog retrieval.
- Line-item matching.
- AI submit/hold decision.
- Reviewer hub visibility.
- Human correction or rerun flow.
- Exception handling for ambiguous or mismatched invoices.

The demo should include at least:

- One submitted invoice
- One held invoice due to ambiguity
- One held invoice due to mismatched metadata
- One larger invoice/catalog matching example
