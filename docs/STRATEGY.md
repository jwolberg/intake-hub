---
name: IntakeHub
last_updated: 2026-05-26
---

# IntakeHub Strategy

## Target problem

Clinical trial site invoices arrive in inconsistent formats, terminology, and
structure, and each one requires interpreting which sponsor/study/site it belongs
to and which catalog items it maps to before it can be matched. That judgment
doesn't reduce to fixed rules, so manual triage is the bottleneck — and it breaks
down as study volume grows.

## Our approach

AI processes every invoice end-to-end and decides submit-vs-hold *before* any
human touches it; reviewers validate outcomes after the fact rather than gating
them up front. What makes "human-after" safe is that every decision carries a
transparent, explainable rationale a reviewer can audit.

## Who it's for

**Primary:** Invoice Operations Reviewer — they're hiring IntakeHub to
*focus only on the invoices the AI flagged, and trust the rest were handled.*

## Key metrics

- **Auto-submit rate** — % of invoices submitted with no human touch. The
  throughput win; leading indicator that the AI is carrying the judgment.
- **False-submit rate** — % of auto-submitted invoices later found wrong. The
  trust guardrail; regresses hard if the AI gets confidently wrong.
- **Hold precision** — % of held invoices that genuinely needed a human. Catches
  the AI crying wolf and dumping noise back on the reviewer.

## Tracks

### Interpretation engine

Extraction, sponsor/study/site context resolution, and catalog matching — the AI
judgment that replaces manual triage.

_Why it serves the approach:_ This is the end-to-end interpretation that has to
work before any decision can be trusted.

### Decisioning & explainability

The submit/hold decision engine plus the confidence, rationale, and risk flags
attached to every outcome.

_Why it serves the approach:_ "Human-after" is only safe if each decision is
legible enough for a reviewer to audit after the fact.

### Reviewer hub

The post-decision QC surface: review, correction, rerun, and escalation.
Pipeline reliability (state, retries, graceful failure) is a quality bar inside
this and the other tracks rather than a track of its own.

_Why it serves the approach:_ It's where the primary persona lives and the only
place humans enter the loop.
