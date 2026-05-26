"""Stage: submission.

Send approved invoice payloads to ClinRun (or the mock) and store the result;
failed submission becomes a retryable error state (PRD FR7, §7 Step 8;
ARCHITECTURE.md §4). Implemented in Phase 1 (P1-T8).
"""
