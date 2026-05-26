"""Stage: line-item matching.

Match extracted line items to catalog items via a layered approach
(normalize -> semantic candidates -> LLM adjudication -> deterministic
amount/quantity verification) (PRD FR5, §7 Step 6; ARCHITECTURE.md §9).
Implemented in Phase 1 (P1-T6) and deepened in Phase 2 (P2-A4).
"""
