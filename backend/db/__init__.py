"""Persistence: engine + versioned migrations (ARCHITECTURE.md §12)."""

from .session import get_engine

__all__ = ["get_engine"]
