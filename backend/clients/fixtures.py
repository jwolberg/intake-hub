"""Canned reference data for local dev and tests.

Shared by the in-process ``StubMCPReferenceClient`` and the ``mcp-reference``
stub server so both serve identical data. This is *not* production reference
data; it is a small, deterministic fixture set covering a clean sponsor/study
and a second sponsor used to exercise mismatch/ambiguity scenarios later.
"""

from __future__ import annotations

# Sponsors / studies / sites the reference API "knows about".
SPONSORS = {
    "sponsor_001": {"name": "Northwind Therapeutics"},
    "sponsor_002": {"name": "Acme Biosciences"},
    "sponsor_003": {"name": "Globex Pharma"},
}

STUDIES = {
    "study_001": {"sponsor_id": "sponsor_001", "name": "NW-CARDIO-1", "protocol": "NWT-101"},
    "study_002": {"sponsor_id": "sponsor_002", "name": "AB-ONCO-2", "protocol": "ACM-202"},
    "study_003": {"sponsor_id": "sponsor_003", "name": "GX-NEURO-3", "protocol": "GLX-303"},
}

SITES = {
    "site_001": {"study_id": "study_001", "name": "Riverside Clinical Research"},
    "site_002": {"study_id": "study_001", "name": "Riverside Clinical Research - West"},
    "site_003": {"study_id": "study_002", "name": "Summit Trials Group"},
    "site_004": {"study_id": "study_003", "name": "Globex Neuro Center"},
}

# A large sponsor+study catalog for the large-invoice / larger-catalog scenario
# (P3-T6; PRD §18 "Large invoice with larger catalog matching"). Generated so the
# fixture stays readable; line items in inv_large_007 reference these descriptions.
_LARGE_CATALOG = [
    {"id": f"cat_2{n:02d}", "description": f"Procedure {n:02d}", "unit_price": f"{100 + n}.00"}
    for n in range(1, 61)
]

# Sponsor+study-scoped billable catalogs, keyed by (sponsor_id, study_id).
CATALOGS = {
    ("sponsor_001", "study_001"): [
        {"id": "cat_001", "description": "Screening Visit", "unit_price": "300.00"},
        {"id": "cat_002", "description": "Randomization Visit", "unit_price": "450.00"},
        {"id": "cat_003", "description": "ECG", "unit_price": "120.00"},
        {"id": "cat_004", "description": "Pharmacy Dispensing Fee", "unit_price": "75.00"},
    ],
    ("sponsor_002", "study_002"): [
        {"id": "cat_101", "description": "Baseline Imaging", "unit_price": "900.00"},
        {"id": "cat_102", "description": "Infusion Visit", "unit_price": "650.00"},
    ],
    ("sponsor_003", "study_003"): _LARGE_CATALOG,
}
