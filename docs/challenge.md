# Challenge

Clinical trial site invoices arrive through email with inconsistent formatting, terminology, and structure, which creates operational delays and avoidable matching errors. This challenge asks participants to design an AI-first workflow that can interpret these invoices, determine context, match billable items, and decide whether an invoice should be submitted to ClinRun or held for exceptions.

The core design principle is that humans are not a required gate before matching and decisioning. Instead, the system should complete AI-driven processing first, then provide clear post-decision QC visibility so reviewers can understand outcomes, validate risk cases, and intervene when needed.

## Problem & Context

### Business Context

Manual invoice triage and matching do not scale with growing study volume and lead to repeated corrective work when context is misidentified or line items are mismatched. The opportunity is to improve throughput by automating intake and decisioning while still preserving operational control through transparent review surfaces.

This challenge is intended to test practical orchestration and explainable decision behavior, not perfect document intelligence. Strong solutions show reliable end-to-end flow, robust handling of ambiguity, and clear communication of why the AI submitted or held an invoice.

### Impact Metrics

- Time saved

## Requirements & Success Criteria

### Functional Requirements

1. Ingest invoices from email and process provided sample invoices end to end
2. Extract metadata and line items using an LLM
3. Resolve sponsor/study/site context via the MCP-wrapped reference API
4. Fetch sponsor+study-scoped catalog and match extracted line items
5. Make an AI decision to submit to backend or hold for exception handling
6. Show in the hub:
   - what was extracted
   - what was matched
   - confidence/exceptions
   - whether the invoice was submitted or withheld
7. Support human QC actions after AI decisioning (review, corrections, rerun/escalation)
8. Demonstrate behavior across easy, ambiguous, and mismatched metadata scenarios from the provided sample invoices

### Performance Benchmarks

1. Stable end-to-end processing on simple, medium, large, and mismatched-metadata samples
2. Reliable handling of larger catalog matching scenarios without workflow failure
3. Responsive reviewer interaction in the hub for core read/review actions
4. Predictable retry/recovery behavior when a stage fails or returns low-confidence outcomes

### Code Quality Expectations

Modular staged architecture, clear separation of extraction/matching/decisioning, test coverage for core and exception paths, and observable workflow state transitions for debugging and demo clarity. Submission quality should make it obvious how decisions are reached and how failures are handled.

### Time Constraints

2-3 days

### Technical Contact

ryan.washburn@ledgerrun.com

## Technology

### Required Languages

Any modern language/framework is acceptable (e.g., TypeScript, Python, Java)

### AI / ML Frameworks

LLM integration for:

- invoice extraction
- metadata/entity resolution
- line-item matching
- submit vs hold decisioning

### Dev Tools

Docker Compose, Git, automated tests for core pipeline logic

### Cloud Platforms

Cloud optional; local-first implementation is acceptable for hackathon delivery
