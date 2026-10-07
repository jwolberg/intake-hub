"""List endpoints stay O(1) in queries, paginate, and render pages lazily (#0012)."""

import json
import pathlib

import pytest
from backend.api import main as api
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.orchestrator import process
from backend.parser import raster
from fastapi.testclient import TestClient
from sqlalchemy import event

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
STEMS = ["inv_clean_001", "inv_body_003", "inv_ambiguous_008"]


def _seed(repo, copies: int) -> None:
    for i in range(copies):
        for stem in STEMS:
            sample = json.loads((SAMPLES / f"{stem}.json").read_text())
            sample["source"]["message_id"] = f"{stem}-{i}"
            process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())


def _client(repo):
    api.app.dependency_overrides[api.get_repo] = lambda: repo
    api.app.dependency_overrides[api.get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    api.app.dependency_overrides.clear()


def _query_count(engine, fn) -> int:
    count = {"n": 0}

    def on_execute(*_args, **_kwargs):
        count["n"] += 1

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    return count["n"]


@pytest.mark.parametrize("path", ["/api/invoices", "/api/review-queue", "/api/notifications"])
def test_list_queries_do_not_grow_with_row_count(pg_repo, pg_engine, path):
    client = _client(pg_repo)
    _seed(pg_repo, 1)
    small = _query_count(pg_engine, lambda: client.get(path).raise_for_status())
    _seed(pg_repo, 3)
    large = _query_count(pg_engine, lambda: client.get(path).raise_for_status())
    assert large == small, f"{path}: {small} queries for 3 items, {large} for 12"


def test_metrics_queries_do_not_grow_with_row_count(pg_repo, pg_engine):
    client = _client(pg_repo)
    _seed(pg_repo, 1)
    small = _query_count(pg_engine, lambda: client.get("/api/metrics").raise_for_status())
    _seed(pg_repo, 3)
    large = _query_count(pg_engine, lambda: client.get("/api/metrics").raise_for_status())
    assert large == small


def test_list_does_not_load_pdf_blobs(pg_repo, pg_engine):
    client = _client(pg_repo)
    _seed(pg_repo, 1)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(pg_engine, "before_cursor_execute", capture)
    try:
        client.get("/api/invoices").raise_for_status()
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)
    assert not [s for s in statements if "source_pdf" in s or "source_text" in s]


# --- pagination -----------------------------------------------------------------


def test_list_paginates_with_total_count_header():
    repo = InMemoryRepository()
    client = _client(repo)
    _seed(repo, 2)  # 6 items

    first = client.get("/api/invoices", params={"limit": 4})
    second = client.get("/api/invoices", params={"limit": 4, "offset": 4})

    assert first.headers["x-total-count"] == "6"
    assert len(first.json()) == 4
    assert len(second.json()) == 2
    ids = [r["id"] for r in first.json() + second.json()]
    assert len(set(ids)) == 6


def test_pagination_applies_after_the_filter():
    repo = InMemoryRepository()
    client = _client(repo)
    _seed(repo, 2)
    held = client.get("/api/invoices", params={"filter": "held"})
    page = client.get("/api/invoices", params={"filter": "held", "limit": 1})
    assert page.headers["x-total-count"] == str(len(held.json()))
    assert page.json()[0]["id"] == held.json()[0]["id"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 5000}, {"offset": -1}])
def test_bad_pagination_params_are_422(params):
    client = _client(InMemoryRepository())
    assert client.get("/api/invoices", params=params).status_code == 422


# --- lazy page rendering --------------------------------------------------------


def _three_page_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    for n in range(3):
        doc.new_page().insert_text((72, 72), f"page {n + 1}")
    return doc.tobytes()


def test_page_dims_match_a_real_render_without_rasterizing(monkeypatch):
    data = _three_page_pdf()
    rendered = raster.render_pdf_bytes(data)
    calls = {"n": 0}
    real = raster._rasterize

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(raster, "_rasterize", counting)
    dims = raster.pdf_page_dims(data)

    assert calls["n"] == 0
    assert [(d.page_number, d.width, d.height) for d in dims] == [
        (p.page_number, p.width, p.height) for p in rendered
    ]


def test_detail_and_page_image_render_only_what_is_asked(monkeypatch):
    import base64

    repo = InMemoryRepository()
    client = _client(repo)
    sample = json.loads((SAMPLES / "inv_clean_001.json").read_text())
    sample["source"]["attachment"] = "multi.pdf"
    sample["source"]["attachment_b64"] = base64.b64encode(_three_page_pdf()).decode()
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    calls = {"n": 0}
    real = raster._rasterize

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(raster, "_rasterize", counting)

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert len(detail["pages"]) == 3
    assert calls["n"] == 0  # dims only

    img = client.get(f"/api/invoices/{invoice_id}/pages/2/image")
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    assert calls["n"] == 1  # just page 2

    client.get(f"/api/invoices/{invoice_id}/pages/2/image")
    assert calls["n"] == 1  # cached
