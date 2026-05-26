# Users: InvoiceScreener

This document describes who InvoiceScreener is for and the jobs they hire it to do.
It is derived from [`STRATEGY.md`](../STRATEGY.md) and [`PRD.md`](./PRD.md), and it
reflects the product's core principle: **the AI processes and decides on each
invoice first; people review and intervene afterward.** That principle shapes every
persona below — humans here are reviewers of outcomes, not first-pass triagers.

## How to read this

There is one **primary** persona whose needs drive product decisions, two
**secondary** human personas who benefit, and one **system actor** that does the
first-pass work. When a tradeoff arises, the primary persona wins.

---

## Primary persona — Invoice Operations Reviewer

> *"Let me focus only on the invoices the AI flagged, and trust the rest were
> handled."*

### Snapshot

The person responsible for validating invoice processing outcomes after the AI has
extracted, matched, and decided. They are the reason the post-decision QC surface
exists. Before InvoiceScreener, this person *was* the pipeline — opening each
emailed invoice, figuring out which sponsor/study/site it belonged to, matching
line items by hand, and deciding whether it was clean enough to submit.

### The situation they're in

Invoices arrive in inconsistent formats and terminology. Volume rises as study
count grows. Manual triage doesn't scale, and the cost of a *wrong* match (wrong
sponsor, wrong catalog item, wrong amount) is real downstream rework. Their
attention is the scarce resource, and today it's spent on routine invoices that
didn't need it.

### Jobs to be done

- **Triage their own attention.** See immediately which invoices the AI held or
  flagged, and ignore the ones it submitted cleanly.
- **Understand a decision fast.** For any held invoice, learn *why* it was held —
  which field was ambiguous, which line item didn't match, which amount conflicted
  — without re-deriving it from the raw document.
- **Trust the submitted ones.** Be confident that auto-submitted invoices were
  right, so they don't have to re-check them.
- **Fix and move on.** Correct a bad extraction or match, rerun, or escalate, with
  the correction reflected in the outcome.

### What they need from InvoiceScreener

- A list view that surfaces status, decision, confidence, and exception count, with
  filters for *held / failed / needs-review / low-confidence / mismatched-metadata /
  unmatched-line-items*.
- A detail view that shows, per invoice: what was extracted (with confidence and
  source evidence), what context was resolved (with candidate alternatives and
  mismatch warnings), what was matched (with rationale and flags), and the
  submit/hold decision with its rationale and risk flags.
- QC actions that work *after* the decision: mark reviewed, correct metadata,
  correct a line match, rerun, escalate, add a note — all recorded in an audit trail
  that distinguishes AI actions from theirs.

### What success looks like for them

They open the hub, the held/flagged invoices are the only ones demanding attention,
each one explains itself, and the corrections they make stick. The submitted pile
is trustworthy enough to leave alone. Their day is spent on judgment calls, not
data entry.

### Anti-needs (what they explicitly do *not* want)

- To be a gate the AI waits on before it can act.
- To re-triage invoices the AI already handled well.
- To reconstruct the AI's reasoning from scratch because the decision wasn't
  explained.
- A flood of low-value holds (the AI crying wolf) that drowns the real exceptions.

---

## Secondary persona — Operations Lead

> *"Are we keeping up, and where is the AI getting it wrong?"*

### Snapshot

A manager accountable for throughput, quality, and exception visibility across the
invoice operation. They don't process individual invoices; they watch the system
and the queue.

### Jobs to be done

- **Watch throughput.** Track how many invoices flow through and how many are
  auto-submitted vs. held.
- **Spot patterns.** Notice recurring exception types, failure modes, or sponsors/
  sites that consistently produce holds.
- **Trust the guardrails.** Confirm the AI isn't submitting wrong invoices
  (false-submit rate) and isn't over-holding clean ones (hold precision).
- **Stand behind decisions.** Use the audit trail of AI and human actions when
  questioned about an outcome.

### What they need from InvoiceScreener

- Aggregate visibility into processing status, submitted-vs-held counts, exception
  patterns, and failure modes.
- The strategy metrics made legible: **auto-submit rate**, **false-submit rate**,
  **hold precision** (see [`STRATEGY.md`](../STRATEGY.md)).
- A complete audit trail showing what the AI decided and what humans changed.

### What success looks like for them

Volume is handled without the team growing linearly with study count, the
guardrail metrics stay healthy, and exception patterns are visible early enough to
act on.

---

## Secondary persona — Exception Handler

> *"This one got escalated to me — what's actually wrong with it?"*

### Snapshot

The person an invoice reaches when a Reviewer escalates it — a deeper specialist
(billing/finance or a senior operations person) who resolves the hard cases the
first-line Reviewer can't. This persona is derived from the PRD's `escalate` action;
in a small team it may be the same person wearing a second hat.

### Jobs to be done

- **Pick up escalations** with full context already attached (extraction, resolved
  context, matches, decision rationale, exception reasons, and reviewer notes).
- **Resolve genuine ambiguity or mismatch** — e.g., an invoice whose stated sponsor
  conflicts with its protocol number — and decide the correct disposition.
- **Close the loop** so the invoice reaches a terminal, audited state.

### What they need from InvoiceScreener

- Escalated invoices clearly distinguished in the queue.
- The same explainable detail view the Reviewer used, plus the reviewer's note and
  the full audit trail.
- The ability to correct, rerun, or finalize disposition.

---

## System actor — AI Workflow Orchestrator

> *Not a human. The actor that does the first-pass work humans used to do.*

### Snapshot

The automated pipeline that processes every invoice end to end before any human is
involved: intake, parsing, LLM extraction, context resolution via the MCP reference
API, catalog retrieval, line-item matching, submit/hold decisioning, submission, and
workflow state tracking. It is listed here as a first-class user because, under the
AI-first principle, it is the system's primary worker.

### "Jobs to be done"

- Carry the per-invoice judgment that doesn't reduce to fixed rules — to a terminal
  decision, with no human gate.
- Attach confidence and a legible rationale to every decision so a human can audit
  it afterward.
- Resolve ambiguity and low confidence toward a safe, visible **hold** rather than a
  silent submission.
- Fail isolated and visible: one invoice's failure never blocks others, and every
  step is recorded as an audit event.

### What it needs from the system

- A typed contract per stage (input → persisted output) so its work is inspectable
  at any point.
- Stable access to the MCP reference API and the ClinRun backend (or mocks), with
  typed failures it can reason over.
- A schema-validated LLM interface so its extractions and decisions are structured
  and trustworthy downstream.

> Its outputs are consumed by every human persona above. See
> [`ARCHITECTURE.md`](./ARCHITECTURE.md) for how it is structured.

---

## Persona summary

| Persona | Type | Core job | When they engage |
| --- | --- | --- | --- |
| Invoice Operations Reviewer | Primary (human) | Focus only on flagged invoices; trust the rest | After every AI decision |
| Operations Lead | Secondary (human) | Watch throughput, quality, and exception patterns | Continuously, in aggregate |
| Exception Handler | Secondary (human) | Resolve escalated hard cases | On escalation only |
| AI Workflow Orchestrator | System actor | Process and decide on every invoice first | Always, first |

When a product decision trades one persona's convenience against another's, the
**Invoice Operations Reviewer** is the tiebreaker — the entire human-after surface
is built for them.
