"""Integration tests: HTTP clients against the stub servers (P0-T4).

Exercises the HTTP client <-> stub-server contract in-process using FastAPI's
``TestClient`` (an ``httpx.Client`` subclass that runs the ASGI app
synchronously), so the wire format the Compose topology relies on is verified
without standing up containers.
"""

import pytest
from backend.clients.clinrun import HttpClinRunClient
from backend.clients.errors import CatalogNotFound
from backend.clients.mcp_reference import HttpMCPReferenceClient
from backend.clients.stub_servers.clinrun_app import app as clinrun_app
from backend.clients.stub_servers.mcp_app import app as mcp_app
from fastapi.testclient import TestClient


def test_http_reference_resolve_and_catalog():
    ref = HttpMCPReferenceClient("http://test", client=TestClient(mcp_app))
    candidates = ref.resolve_sponsor_study_site(
        {"sponsor_name": "Northwind Therapeutics", "protocol_number": "NWT-101"}
    )
    assert candidates[0].sponsor_id == "sponsor_001"

    items = ref.get_catalog("sponsor_001", "study_001")
    assert any(i.description == "Screening Visit" for i in items)


def test_http_reference_missing_catalog_maps_to_not_found():
    ref = HttpMCPReferenceClient("http://test", client=TestClient(mcp_app))
    with pytest.raises(CatalogNotFound):
        ref.get_catalog("sponsor_001", "study_999")


def test_http_clinrun_submit():
    clinrun = HttpClinRunClient("http://test", client=TestClient(clinrun_app))
    result = clinrun.submit({"invoice_id": "invoice_xyz"})
    assert result.accepted is True
    assert result.reference_id == "clinrun_invoice_xyz"
