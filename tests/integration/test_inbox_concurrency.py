"""Concurrent inbox fetches (#0005).

The poller and the hub's "fetch" button can hit ``/api/inbox/fetch`` at the
same time. ``is_seen`` → process → ``mark_seen`` isn't atomic, so two
overlapping fetches could each process the same message into two items (with
different ids, so the Sheet dedup — keyed on item id — wouldn't stop a second
row). Fetches are serialized with a database lock; an overlapping fetch gets
409 and the poller simply tries again next tick.

Runs against real Postgres (the lock is a Postgres advisory lock — the only
thing that serializes across Cloud Run instances).
"""

import threading
import time

from backend.api.main import app, get_inbox, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.inbox import MockInbox
from fastapi.testclient import TestClient


class SlowInbox(MockInbox):
    """MockInbox whose listing takes a moment, widening the race window."""

    def fetch_messages(self):
        messages = super().fetch_messages()
        time.sleep(0.5)
        return messages


def _run_concurrent_fetches(repo, n=2):
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    app.dependency_overrides[get_inbox] = lambda: SlowInbox(render_pdf=False)
    statuses: list[int] = []
    barrier = threading.Barrier(n)

    def worker():
        client = TestClient(app)
        barrier.wait()
        statuses.append(client.post("/api/inbox/fetch").status_code)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    finally:
        app.dependency_overrides.clear()
    return sorted(statuses)


def _assert_each_message_processed_once(repo):
    message_count = len(MockInbox(render_pdf=False).fetch_messages())
    invoices = repo.list_invoices()
    assert len(invoices) == message_count
    received = [
        e.details.get("message_id")
        for inv in invoices
        for e in repo.get_audit(inv.id)
        if e.action.value == "received"
    ]
    assert len(received) == len(set(received)) == message_count


def test_concurrent_fetches_never_double_process_postgres(pg_repo):
    statuses = _run_concurrent_fetches(pg_repo)

    assert statuses == [200, 409]
    _assert_each_message_processed_once(pg_repo)


def test_concurrent_fetches_never_double_process_in_memory():
    repo = InMemoryRepository()
    statuses = _run_concurrent_fetches(repo)

    assert statuses == [200, 409]
    _assert_each_message_processed_once(repo)


def _other_session_can_lock(pg_engine) -> bool:
    """Try the fetch lock from an *independent* connection. Session advisory locks
    are re-entrant, so probing from the pooled connection the repo used would
    succeed even if the lock had leaked."""
    from backend.db.repository import _INBOX_FETCH_LOCK_KEY
    from backend.db.session import make_engine
    from sqlalchemy import text

    other = make_engine(pg_engine.url.render_as_string(hide_password=False))
    try:
        with other.connect() as conn:
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _INBOX_FETCH_LOCK_KEY}
            ).scalar()
            if got:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INBOX_FETCH_LOCK_KEY})
            return bool(got)
    finally:
        other.dispose()


def test_lock_excludes_other_sessions_and_is_released_after(pg_repo, pg_engine):
    with pg_repo.inbox_fetch_lock() as acquired:
        assert acquired
        assert not _other_session_can_lock(pg_engine)
    assert _other_session_can_lock(pg_engine)


def test_lock_is_released_when_the_fetch_raises(pg_repo, pg_engine):
    try:
        with pg_repo.inbox_fetch_lock():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert _other_session_can_lock(pg_engine)
