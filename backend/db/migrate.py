"""Versioned schema migrations (#0013).

Migrations are plain SQL files in ``backend/db/migrations/`` named
``NNNN_description.sql``, applied in version order. Each file runs in its own
transaction and is recorded in ``schema_migrations`` in that same transaction, so
a failing migration leaves neither partial DDL nor a version row behind (Postgres
DDL is transactional). A transaction-scoped advisory lock serializes concurrent
runners (e.g. two deploys).

Migrations are an explicit deploy step — the API no longer touches the schema on
startup; ``/ready`` reports 503 while any are pending.

    python -m backend.db.migrate            # apply pending migrations
    python -m backend.db.migrate --status   # list applied / pending, exit 1 if pending
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, inspect, text

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Arbitrary, stable advisory-lock id for "a migration run is in progress".
_LOCK_KEY = 0x1A7E_0013

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """All migrations in ``directory``, in version order.

    Non-``.sql`` files are ignored; a ``.sql`` file that doesn't match
    ``NNNN_name.sql`` or reuses a version is an error (never silently skipped).
    """
    found: dict[str, Migration] = {}
    for path in sorted(Path(directory).glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise ValueError(f"migration file must be named NNNN_name.sql: {path.name}")
        version = match.group(1)
        if version in found:
            raise ValueError(
                f"duplicate migration version {version}: {found[version].path.name}, {path.name}"
            )
        found[version] = Migration(version=version, name=path.stem, path=path)
    return [found[v] for v in sorted(found)]


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements on ``;`` boundaries.

    ``--`` comment lines are stripped *before* splitting, so a ``;`` inside a
    comment can't cut a statement in half; empty fragments are dropped. (Not a SQL
    parser: keep ``;`` out of string literals and function bodies.)
    """
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    statements: list[str] = []
    for chunk in "\n".join(lines).split(";"):
        cleaned = chunk.strip()
        if cleaned:
            statements.append(cleaned)
    return statements


def applied_versions(engine: Engine) -> set[str]:
    if "schema_migrations" not in inspect(engine).get_table_names():
        return set()
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT version FROM schema_migrations")).scalars())


def pending(engine: Engine, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    done = applied_versions(engine)
    return [m for m in discover(directory) if m.version not in done]


def apply(engine: Engine, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration in order; returns the versions applied."""
    migrations = discover(directory)
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        conn.execute(text(_VERSION_TABLE))
    for migration in migrations:
        with engine.begin() as conn:
            # Re-check under the lock, so a concurrent runner can't double-apply.
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
            done = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :v"),
                {"v": migration.version},
            ).first()
            if done:
                continue
            for statement in split_statements(migration.path.read_text(encoding="utf-8")):
                conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                {"v": migration.version, "n": migration.name},
            )
        applied.append(migration.version)
    return applied


def main(argv: list[str]) -> int:
    from backend.db.session import get_engine

    engine = get_engine()
    if "--status" in argv:
        done = applied_versions(engine)
        todo = pending(engine)
        for m in discover():
            print(f"  {'applied' if m.version in done else 'PENDING'}  {m.name}")
        return 1 if todo else 0
    applied = apply(engine)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none pending'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
