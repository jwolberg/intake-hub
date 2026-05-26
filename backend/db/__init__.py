"""Persistence: engine, schema bootstrap (ARCHITECTURE.md §12)."""

from .session import get_engine, init_schema

__all__ = ["get_engine", "init_schema"]
