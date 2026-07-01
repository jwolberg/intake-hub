"""Unit tests for the Google Drive intake client (feat: Drive folder intake, U1).

Covers ``StubDriveClient`` behavior (list/download/move + typed errors) and the
``HttpDriveClient`` REST path against an injected ``httpx`` transport, asserting
the service-account token is minted lazily so constructing the client needs no
live credentials or network.
"""

import httpx
import pytest
from backend.clients import DriveFile, HttpDriveClient, StubDriveClient
from backend.clients.errors import DriveClientError

ROOT = "root-folder"


# --- StubDriveClient --------------------------------------------------------


def _seeded_stub() -> StubDriveClient:
    stub = StubDriveClient()
    stub.add_file("f1", "invoice-a.pdf", b"%PDF-a", parent=ROOT)
    stub.add_file("f2", "invoice-b.pdf", b"%PDF-b", parent=ROOT)
    stub.add_file("f3", "notes.txt", b"text", parent=ROOT)  # non-PDF
    stub.add_file("f4", "old.pdf", b"%PDF-old", parent="sub::submitted")  # subfolder
    return stub


def test_list_pdfs_returns_only_root_level_pdfs():
    files = _seeded_stub().list_pdfs(ROOT)
    assert {f.id for f in files} == {"f1", "f2"}
    assert all(isinstance(f, DriveFile) for f in files)
    # non-PDF (f3) and subfolder file (f4) are excluded — covers AE5 indirectly.


def test_download_returns_stored_bytes():
    assert _seeded_stub().download("f1") == b"%PDF-a"


def test_move_relocates_file_and_removes_it_from_root_listing():
    stub = _seeded_stub()
    stub.move("f1", "submitted")
    assert stub.moves == [("f1", "submitted")]
    # A subsequent root listing no longer returns the moved file.
    assert {f.id for f in stub.list_pdfs(ROOT)} == {"f2"}


def test_stub_surfaces_typed_errors():
    stub = _seeded_stub()
    stub.fail_list = True
    with pytest.raises(DriveClientError):
        stub.list_pdfs(ROOT)

    stub2 = _seeded_stub()
    stub2.fail_download.add("f1")
    with pytest.raises(DriveClientError):
        stub2.download("f1")

    stub3 = _seeded_stub()
    stub3.fail_move.add("f1")
    with pytest.raises(DriveClientError):
        stub3.move("f1", "submitted")


def test_stub_download_unknown_file_raises_typed_error():
    with pytest.raises(DriveClientError):
        StubDriveClient().download("missing")


# --- HttpDriveClient (injected transport + token seam) ----------------------


class _TokenSpy:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


def test_token_minting_is_lazy_and_needs_no_credentials():
    # Constructing the client must not import google-auth, hit the network, or
    # mint a token — the injected provider is untouched until a call is made.
    spy = _TokenSpy()
    HttpDriveClient(token_provider=spy)
    assert spy.calls == 0

    # Without credentials and without a provider, construction still succeeds;
    # only an actual REST call would try to mint (asserted separately).
    HttpDriveClient()


def test_http_list_pdfs_parses_response_and_sends_bearer_token():
    spy = _TokenSpy()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["q"] = request.url.params.get("q")
        return httpx.Response(200, json={"files": [{"id": "f1", "name": "a.pdf"}]})

    transport = httpx.MockTransport(handler)
    client = HttpDriveClient(
        token_provider=spy,
        client=httpx.Client(base_url=HttpDriveClient.BASE_URL, transport=transport),
    )
    files = client.list_pdfs(ROOT)

    assert [f.id for f in files] == ["f1"]
    assert spy.calls == 1
    assert seen["auth"] == "Bearer test-token"
    assert f"'{ROOT}' in parents" in seen["q"]
    assert "application/pdf" in seen["q"]


def test_http_download_returns_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-bytes")

    client = HttpDriveClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpDriveClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    assert client.download("f1") == b"%PDF-bytes"


def test_http_move_creates_subfolder_when_absent_and_reparents():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.params.get("fields") == "parents":
            return httpx.Response(200, json={"parents": [ROOT]})
        if request.method == "GET":  # subfolder lookup — none exists yet
            return httpx.Response(200, json={"files": []})
        if request.method == "POST":  # create subfolder
            return httpx.Response(200, json={"id": "sub-submitted"})
        if request.method == "PATCH":  # reparent
            return httpx.Response(200, json={"id": "f1", "parents": ["sub-submitted"]})
        return httpx.Response(400)

    client = HttpDriveClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpDriveClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    client.move("f1", "submitted")
    methods = [m for m, _ in calls]
    assert "POST" in methods  # subfolder was created
    assert "PATCH" in methods  # file was reparented


def test_http_errors_are_wrapped_as_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpDriveClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpDriveClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(DriveClientError):
        client.list_pdfs(ROOT)
