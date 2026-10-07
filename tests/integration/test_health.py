"""Liveness vs readiness, and batch error logging (#0015)."""

import logging

import backend.api.main as api
from backend.db.repository import InMemoryRepository
from backend.orchestrator import process_all
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


class _DownEngine:
    def connect(self):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_is_liveness_only_even_when_db_is_down(monkeypatch):
    monkeypatch.setattr(api, "get_engine", lambda: _DownEngine())
    resp = TestClient(api.app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["db"] == "down"


def test_ready_is_503_when_db_is_down(monkeypatch):
    monkeypatch.setattr(api, "get_engine", lambda: _DownEngine())
    resp = TestClient(api.app).get("/ready")
    assert resp.status_code == 503
    assert resp.json()["db"] == "down"


def test_ready_is_200_when_db_is_up(pg_engine):
    resp = TestClient(api.app).get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "db": "up", "schema": "current"}


def test_process_all_logs_swallowed_errors(monkeypatch, caplog):
    import backend.orchestrator as orch

    def boom(sample, repo, **clients):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(orch, "process", boom)
    with caplog.at_level(logging.ERROR, logger="intakehub.orchestrator"):
        results = process_all([{"source": {"message_id": "m-1"}}], InMemoryRepository())

    assert results == []
    assert "m-1" in caplog.text
    assert caplog.records[0].exc_info is not None


# --- CORS (#0014) -------------------------------------------------------------

ORIGIN = "http://localhost:5173"  # a default CORS_ORIGINS entry


def _preflight(method, headers="content-type"):
    return TestClient(api.app).options(
        "/api/invoices",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_cors_allows_the_methods_and_headers_the_hub_uses():
    resp = _preflight("POST", "content-type")
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_cors_rejects_other_methods():
    assert _preflight("DELETE").status_code == 400


def test_cors_rejects_arbitrary_headers():
    assert _preflight("POST", "x-evil-header").status_code == 400
