"""Stage: decisioning.

Apply the data-driven submit/hold policy and produce a structured decision with
confidence, rationale, and risk flags. Runs before any human QC; low confidence
holds, never silently submits (PRD FR6, §9; ARCHITECTURE.md §10). Implemented in
Phase 1 (P1-T7) and deepened in Phase 2 (P2-B1).
"""
