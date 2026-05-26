"""Stage: orchestrator.

Sequence the pipeline stages, own workflow state transitions, append audit
events, and isolate per-invoice failures so one failure never blocks others
(PRD FR11, §14; ARCHITECTURE.md §5-§6). Implemented in Phase 1 (P1-T9).
"""
