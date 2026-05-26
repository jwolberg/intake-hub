---
name: plan
description: Convert Ledgerruns's multi-doc design set (challenge + PRD + STRATEGY + USERS + ARCHITECTURE) into an execution-ready build plan with phases, tickets, dependencies, and status tracking
---

Plan this product strictly from the design documents already in `/docs/`.

This project does not have a single `/docs/spec.md`. The role of "spec" is
distributed across several documents, each authoritative for a specific dimension.
The plan skill reads all of them and reconciles them into one execution contract.

---

## Input documents and their authority

Read each in full. Each is the source of truth for a different dimension; treat it
as authoritative within its dimension and defer to it when documents disagree.

- **/docs/PRD.md** (REQUIRED) — Source of truth for *what must ship*. Functional
  requirements (FE / BE / API), inputs and outputs, verification sources, risk
  scoring, success criteria, performance expectations, and non-goals. Plan tickets
  must trace to PRD requirements.
- **/docs/STRATEGY.md** (REQUIRED) — Source of truth for *approach, scope
  boundaries, prioritization, and metrics*. The target problem, the guiding
  approach ("triage, human decides"), the key metrics, and the **Tracks** —
  which are the investment areas the plan groups work around.
- **/docs/ARCHITECTURE.md** (REQUIRED) — Source of truth for *technical design*.
  Components, the agentic pipeline stages, the data model, integration/adapter
  strategy, auth, and the **Open Decisions** table (unresolved choices that the
  plan must not silently assume — see § Open decisions handling).
- **/docs/USERS.md** (REQUIRED) — Source of truth for *who we are building for*.
  Personas, the interface each uses, and access/audit rules. Tickets that affect a
  user surface must trace to a persona's interface.
- **/docs/challenge.md** (REQUIRED) — Originating brief and business context: the
  problem, impact metrics, and the high-level FE/BE/quality expectations. Defer to
  PRD where they overlap (PRD refines the challenge).
- **/docs/ux.md** (OPTIONAL, clarification only) — If present, use only for
  clarifying interaction details; do not expand scope based on it.

If any REQUIRED document is missing, stop and report what is missing rather than
guessing.

---

## Conflict resolution between documents

When two documents disagree, apply this order of authority:

1. **PRD** wins on what must ship, acceptance/success criteria, and non-goals.
2. **STRATEGY** wins on approach, scope boundaries, prioritization, and metrics.
3. **ARCHITECTURE** wins on technical decisions (components, pipeline, data model,
   file layout, integrations, auth) — except where a decision is still listed under
   its Open Decisions table, which is treated as unresolved.
4. **USERS** wins on persona workflows and which interface serves which user.
5. **challenge.md** is background; defer to PRD wherever they overlap.

When all would inform the same decision, prefer the **narrowest MVP path** that
still satisfies the PRD's functional requirements and STRATEGY's metrics.

---

## Open decisions handling

`ARCHITECTURE.md` carries an **Open Decisions** table (e.g. backend stack, job
orchestration, data-source licensing, PII retention). These are not yet settled.

- Do **not** silently assume a resolution. If a ticket depends on an open decision,
  either (a) make resolving that decision its own early ticket, or (b) mark the
  dependent ticket `Blocked` with the decision id it waits on.
- Where ARCHITECTURE states a *recommendation* for an open decision, you may plan
  against it, but record it in § Planning Assumptions and note it is provisional.
- Resolving the backend-stack decision (Open Decision #1) is a prerequisite for any
  implementation ticket; surface it in Phase 0.

---

## Update output in:
- /docs/BUILD_PLAN.md

---

## Goal

Translate the design-document set into a durable, execution-ready build plan that:

- Delivers the **narrowest end-to-end working path first**, then deepens each track
- Groups work around STRATEGY's Tracks and traces every ticket to a PRD requirement
- Respects ARCHITECTURE's components/pipeline/data model and its Open Decisions
- Survives session resets
- Guides implementation one phase / one ticket at a time

---

## Task

1. Read all REQUIRED input documents above. Skim /docs/ux.md if present.
2. Extract from the document set:
   - Core problem (STRATEGY § Target problem + challenge § Problem Statement)
   - Scoped solution / approach (STRATEGY § Our approach + ARCHITECTURE § System
     context & components)
   - Acceptance criteria (PRD § Success Criteria + the FE / BE / API functional
     requirements)
   - Non-goals (PRD § Non-Goals)
   - Architecture constraints (ARCHITECTURE § components, § agentic pipeline,
     § data model, § integrations, § auth) and Open Decisions
   - "Definition of working" metrics (STRATEGY § Key metrics + PRD § Performance
     Expectations, e.g. < 2h per company, viewable partial results)
   - Track grouping (STRATEGY § Tracks)
3. Derive phases as an **MVP-first vertical slice, then track-deepening**:
   - **Phase 0 — Decisions & scaffolding** — resolve blocking Open Decisions
     (backend stack first), set up the monorepo layout PRD mandates
     (`frontend/ backend/ shared/ docs/ tests/`), and the lint/test harness.
   - **Phase 1 — MVP vertical slice (walking skeleton)** — the thinnest end-to-end
     path: submission endpoint (with network-metadata capture) → minimal pipeline
     (normalize + ≥1 authoritative source + domain/infra signals) → data model +
     store → a basic risk assessment + stored report → minimal operator app (auth,
     audited, company list, detail with submitted-vs-discovered, mark reviewed).
   - **Phase 2 — Deepen the tracks** — expand each STRATEGY Track to full PRD scope:
     full evidence pipeline (all tiers, adapters, caching, graceful degradation, IP
     intel), four-layer scoring + explainability + triage tiers, full operator
     workbench (corrections + re-run, HQ map, filters, export), full report /
     integration API (re-analysis, workflow endpoints, partial results).
   - **Phase 3 — Hardening & polish** — performance (< 2h, partial results), audit
     completeness, PII retention, optional domain-ownership verification, BE + FE
     tests per PRD code-quality expectations, docs.
4. Within each phase, order tickets by dependency. Mark the recommended starting
   ticket.
5. For every ticket, populate Objective / Files / Dependencies / Acceptance criteria
   covered / Status.
6. Acceptance criteria for each ticket must cite a specific clause from PRD
   § Success Criteria or a functional requirement, a STRATEGY metric/track, or an
   ARCHITECTURE section.
7. Write a durable status-tracking plan.

---

## Required Output Format (/docs/BUILD_PLAN.md)

# Build Plan

## Project
- Name: EntityIQ — Enterprise Business Verification & Risk Intelligence Platform
- Summary: <1-2 sentences from STRATEGY § Target problem + § Our approach>

## Source of Truth
- What must ship / requirements: /docs/PRD.md
- Approach / scope / metrics / tracks: /docs/STRATEGY.md
- Technical design / open decisions: /docs/ARCHITECTURE.md
- Personas / interfaces: /docs/USERS.md
- Originating brief: /docs/challenge.md
- UX clarifications (if used): /docs/ux.md

## Planning Assumptions
- Any place a document was ambiguous and a minimal assumption was made
- Any place two documents disagreed and the conflict-resolution rule was applied
- Any Open Decision planned against provisionally (state which, and the recommendation used)

## Architecture Notes
- Component layout from ARCHITECTURE § System context & components
- Pipeline model from ARCHITECTURE § Agentic verification pipeline
- Data model from ARCHITECTURE § Data model
- Auth model from ARCHITECTURE § Authentication & authorization
- Open Decisions and how each is handled (resolved in Phase 0 / planned provisionally / blocks a ticket)
- Explicit non-goals that affect implementation (PRD § Non-Goals)

## Current Status
- Overall status: Not Started
- Current phase:
- Current ticket:
- Blockers: None

---

## Phase Breakdown

### Phase 0 — Decisions & Scaffolding
**Goal**
- Resolve blocking Open Decisions and stand up the monorepo + tooling

**Exit Criteria**
- Backend stack chosen; monorepo (`frontend/ backend/ shared/ docs/ tests/`) and lint/test harness in place

**Tickets**
- P0-T1 — <ticket name>
  - Objective:
  - Files likely involved:
  - Depends on:
  - Acceptance criteria covered: (cite PRD / STRATEGY / ARCHITECTURE clause or Open Decision id)
  - Status: Todo

(continue with P0-T2 …)

### Phase 1 — MVP Vertical Slice
**Goal**
- Thinnest end-to-end path through both co-primary surfaces (API + operator app)

**Exit Criteria**
- A submitted registration produces a stored, viewable report an operator can review

(tickets, same structure)

### Phase 2 — Deepen the Tracks
(same structure; tickets grouped by STRATEGY Track, each tracing to a PRD requirement)

### Phase 3 — Hardening & Polish
(performance, audit, PII retention, tests, docs)

---

## Dependency Order
1. P0-T1
2. P0-T2
3. P1-T1
...

## Recommended Next Step
- Start with: <ticket id + name>
- Why this is first:

## Deferred / Out of Scope
- Items explicitly marked non-goal in PRD § Non-Goals
- Open Decisions not yet needed for the current phase
- Nice-to-haves not required for the MVP vertical slice

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status
- Record blockers briefly (including any Open Decision a ticket is blocked on)
- Set the next recommended ticket
- Do NOT add new scope unless one of the REQUIRED input documents changes

---

## Rules

- Plan ONLY from the REQUIRED input documents listed above
- Apply the conflict-resolution order when documents disagree
- Honor § Open decisions handling — never silently assume an unresolved choice
- Do NOT expand product scope beyond what the input documents authorize
- Group tickets around STRATEGY Tracks; deliver the MVP vertical slice before deepening
- Keep phases incremental and independently testable
- Keep tickets small and implementation-ready
- Prefer the smallest viable sequence to a working product
- Do NOT rely on prior conversation
- Be explicit about dependencies
- Preserve traceability: every ticket's acceptance criteria must cite a specific
  clause from PRD, STRATEGY, ARCHITECTURE, or USERS

---

## Behavior

- If a /docs/BUILD_PLAN.md already exists, update it only if explicitly asked;
  otherwise create from scratch.
- If a REQUIRED input document is missing, stop and list what is missing.
- If an input document is unclear or two documents disagree, apply the
  conflict-resolution order, state the assumption in § Planning Assumptions, and
  proceed. Do not silently choose.
- If ARCHITECTURE specifies a component, file path, data entity, or framework, use
  it verbatim in ticket "Files likely involved" — do not guess alternatives.
- If a ticket depends on an Open Decision, either schedule the decision as an early
  ticket or mark the dependent ticket Blocked with the decision id.

---

After writing:
- Confirm file created: /docs/BUILD_PLAN.md
- STOP
