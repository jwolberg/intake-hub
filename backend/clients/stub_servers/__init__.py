"""Stand-in services for the reference API and ClinRun backend.

Run as separate Docker Compose services (ARCHITECTURE.md §17) using the backend
image with a different uvicorn target. They serve the same ``fixtures`` the
in-process stubs use, so HTTP and in-process paths behave identically.
"""
