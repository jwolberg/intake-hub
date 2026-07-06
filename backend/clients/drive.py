"""Google Drive intake client (feat: Drive folder intake, KTD1).

Treats a watched Drive folder as the AI's inbox. The ``DriveClient`` protocol
exposes just the three operations the intake path needs — list the root PDFs,
download one, and move a processed file into a status subfolder — mirroring the
``Protocol`` + ``Http…``/``Stub…`` shape used by the other external clients
(e.g. ``backend/clients/sheets.py``).

``HttpDriveClient`` calls the Drive v3 REST API over the existing ``httpx``
dependency and mints a service-account bearer token with ``google-auth``. Both
the token provider and the ``httpx`` transport are injectable, so tests exercise
the client with no live credentials and no network, and ``google-auth`` is
imported lazily (only the real-provider path pulls it in — the same posture as
``anthropic`` in ``backend/clients/llm.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel

from .errors import DriveClientError

# Drive treats folders as files with this reserved mime type; PDFs report the
# standard PDF mime type. Both are used to scope ``files.list`` queries.
_FOLDER_MIME = "application/vnd.google-apps.folder"
_PDF_MIME = "application/pdf"


class DriveFile(BaseModel):
    """A file in the watched folder (only the fields intake needs)."""

    id: str
    name: str


class DriveClient(Protocol):
    """The three Drive operations the intake path depends on."""

    def list_pdfs(self, folder_id: str) -> list[DriveFile]:
        """Return root-level ``.pdf`` files in ``folder_id`` (subfolders excluded)."""
        ...

    def download(self, file_id: str) -> bytes:
        """Return the raw bytes of ``file_id``, or raise ``DriveClientError``."""
        ...

    def move(self, file_id: str, source_folder_id: str, dest: str) -> None:
        """Move ``file_id`` from ``source_folder_id`` into the ``dest`` status
        subfolder (created under the source folder if absent). The caller passes
        the source folder it already knows, so no extra lookup is needed."""
        ...


@dataclass
class _StubFile:
    name: str
    data: bytes
    parent: str


class StubDriveClient:
    """In-memory Drive fake for tests — no network, no credentials.

    Seed the watched folder with :meth:`add_file`; :meth:`move` reparents a file
    into a synthetic status subfolder so a subsequent :meth:`list_pdfs` on the
    root no longer returns it (mirrors the real root-only listing). Set the
    ``fail_*`` collections to make an operation raise a typed ``DriveClientError``,
    so callers can exercise the per-file error path.
    """

    def __init__(self) -> None:
        self._files: dict[str, _StubFile] = {}
        self.moves: list[tuple[str, str]] = []
        self.fail_list = False
        self.fail_download: set[str] = set()
        self.fail_move: set[str] = set()

    def add_file(self, file_id: str, name: str, data: bytes, *, parent: str) -> None:
        self._files[file_id] = _StubFile(name, data, parent)

    def list_pdfs(self, folder_id: str) -> list[DriveFile]:
        if self.fail_list:
            raise DriveClientError("stub: list failed")
        return [
            DriveFile(id=fid, name=f.name)
            for fid, f in self._files.items()
            if f.parent == folder_id and f.name.lower().endswith(".pdf")
        ]

    def download(self, file_id: str) -> bytes:
        if file_id in self.fail_download:
            raise DriveClientError(f"stub: download failed for {file_id}")
        try:
            return self._files[file_id].data
        except KeyError as exc:
            raise DriveClientError(f"stub: no such file {file_id}") from exc

    def move(self, file_id: str, source_folder_id: str, dest: str) -> None:
        if file_id in self.fail_move:
            raise DriveClientError(f"stub: move failed for {file_id}")
        try:
            self._files[file_id].parent = f"{source_folder_id}::{dest}"
        except KeyError as exc:
            raise DriveClientError(f"stub: no such file {file_id}") from exc
        self.moves.append((file_id, dest))


class HttpDriveClient:
    """Drive v3 REST client authenticated as a service account.

    ``token_provider`` is a ``Callable[[], str]`` returning a bearer token; it
    defaults to a lazily-built ``google-auth`` service-account provider so
    constructing the client never imports ``google-auth`` or touches credentials.
    ``client`` is an optional ``httpx.Client`` so tests can inject an in-process
    transport. Any transport/HTTP error is re-raised as ``DriveClientError``.
    """

    BASE_URL = "https://www.googleapis.com/drive/v3"
    _SCOPES = ("https://www.googleapis.com/auth/drive",)

    def __init__(
        self,
        *,
        credentials_file: str | None = None,
        credentials_info: dict | None = None,
        token_provider: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._credentials_file = credentials_file
        self._credentials_info = credentials_info
        self._token_provider = token_provider
        self._sa_credentials = None  # built lazily on first token mint
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)
        # Cache resolved status-subfolder ids per (parent, name) so repeated moves
        # don't re-query or re-create the subfolder.
        self._subfolder_ids: dict[tuple[str, str], str] = {}

    # --- auth ---------------------------------------------------------------

    def _token(self) -> str:
        if self._token_provider is not None:
            return self._token_provider()
        return self._mint_service_account_token()

    def _mint_service_account_token(self) -> str:
        # Lazy import: only the real service-account path needs google-auth.
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._sa_credentials is None:
            if self._credentials_info is not None:
                self._sa_credentials = service_account.Credentials.from_service_account_info(
                    self._credentials_info, scopes=list(self._SCOPES)
                )
            elif self._credentials_file is not None:
                self._sa_credentials = service_account.Credentials.from_service_account_file(
                    self._credentials_file, scopes=list(self._SCOPES)
                )
            else:
                raise DriveClientError(
                    "HttpDriveClient needs credentials_file or credentials_info "
                    "to mint a service-account token"
                )
        # Reuse the token for its ~1h lifetime; only refresh when it is missing or
        # expired, so one poll of N files does not mint N tokens.
        if not self._sa_credentials.valid:
            self._sa_credentials.refresh(Request())
        return self._sa_credentials.token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    # --- operations ---------------------------------------------------------

    def list_pdfs(self, folder_id: str) -> list[DriveFile]:
        q = (
            f"'{folder_id}' in parents and trashed=false "
            f"and mimeType='{_PDF_MIME}'"
        )
        try:
            resp = self._client.get(
                "/files",
                params={"q": q, "fields": "files(id,name)", "pageSize": 1000},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DriveClientError(f"list_pdfs failed: {exc}") from exc
        return [DriveFile(**f) for f in resp.json().get("files", [])]

    def download(self, file_id: str) -> bytes:
        try:
            resp = self._client.get(
                f"/files/{file_id}",
                params={"alt": "media"},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DriveClientError(f"download failed for {file_id}: {exc}") from exc
        return resp.content

    def move(self, file_id: str, source_folder_id: str, dest: str) -> None:
        # The caller passes the source folder it already listed the file from, so
        # there is no need to look up the file's current parent first.
        dest_id = self._resolve_subfolder(source_folder_id, dest)
        try:
            resp = self._client.patch(
                f"/files/{file_id}",
                params={
                    "addParents": dest_id,
                    "removeParents": source_folder_id,
                    "fields": "id,parents",
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DriveClientError(f"move failed for {file_id}: {exc}") from exc

    def _resolve_subfolder(self, parent: str, name: str) -> str:
        """Find (or create) the ``name`` subfolder under ``parent``, id cached per
        ``(parent, name)`` so repeated moves in one poll don't re-query."""
        cached = self._subfolder_ids.get((parent, name))
        if cached is not None:
            return cached
        q = (
            f"name='{name}' and mimeType='{_FOLDER_MIME}' "
            f"and '{parent}' in parents and trashed=false"
        )
        try:
            found = self._client.get(
                "/files",
                params={"q": q, "fields": "files(id)", "pageSize": 1},
                headers=self._auth_headers(),
            )
            found.raise_for_status()
            files = found.json().get("files", [])
            if files:
                folder_id = files[0]["id"]
            else:
                created = self._client.post(
                    "/files",
                    params={"fields": "id"},
                    json={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent]},
                    headers=self._auth_headers(),
                )
                created.raise_for_status()
                folder_id = created.json()["id"]
        except httpx.HTTPError as exc:
            raise DriveClientError(f"resolve subfolder '{name}' failed: {exc}") from exc
        self._subfolder_ids[(parent, name)] = folder_id
        return folder_id
