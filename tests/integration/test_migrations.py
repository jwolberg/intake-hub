"""Versioned schema migrations (#0013).

Each test gets its own throwaway database so "fresh", "pre-existing", and
"failed migration" states don't bleed into the shared test schema.
"""

import uuid

import pytest
from backend.db import migrate
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture
def fresh_engine(pg_engine):
    name = f"intakehub_mig_{uuid.uuid4().hex[:10]}"
    admin = pg_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(pg_engine.url.set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_fresh_database_gets_every_table_and_records_versions(fresh_engine):
    applied = migrate.apply(fresh_engine)

    assert applied == [m.version for m in migrate.discover()]
    assert {"invoices", "audit_events", "exceptions", "schema_migrations"} <= _tables(fresh_engine)
    assert migrate.pending(fresh_engine) == []


def test_reapplying_is_a_no_op(fresh_engine):
    migrate.apply(fresh_engine)
    assert migrate.apply(fresh_engine) == []


def test_existing_pre_migration_database_adopts_baseline_without_data_loss(fresh_engine):
    # A database created by the old startup init_schema: tables, no version table.
    baseline = migrate.discover()[0]
    with fresh_engine.begin() as conn:
        for stmt in migrate.split_statements(baseline.path.read_text()):
            conn.execute(text(stmt))
        conn.execute(text("INSERT INTO invoices (id) VALUES ('legacy-1')"))

    applied = migrate.apply(fresh_engine)

    assert applied[0] == baseline.version
    with fresh_engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM invoices")).scalars().all() == ["legacy-1"]


def test_failed_migration_rolls_back_and_is_not_recorded(fresh_engine, tmp_path):
    for m in migrate.discover():
        (tmp_path / m.path.name).write_text(m.path.read_text())
    (tmp_path / "9001_broken.sql").write_text(
        "CREATE TABLE half_done (id TEXT);\nSELECT this_function_does_not_exist();\n"
    )

    with pytest.raises(SQLAlchemyError):
        migrate.apply(fresh_engine, directory=tmp_path)

    assert "half_done" not in _tables(fresh_engine)
    assert [m.version for m in migrate.pending(fresh_engine, directory=tmp_path)] == ["9001"]


def test_migrations_apply_in_numeric_order(fresh_engine, tmp_path):
    for m in migrate.discover():
        (tmp_path / m.path.name).write_text(m.path.read_text())
    (tmp_path / "9010_second.sql").write_text("ALTER TABLE first_t ADD COLUMN b TEXT;")
    (tmp_path / "9002_first.sql").write_text("CREATE TABLE first_t (a TEXT);")
    (tmp_path / "README.md").write_text("not a migration")

    applied = migrate.apply(fresh_engine, directory=tmp_path)

    assert applied[-2:] == ["9002", "9010"]


def test_misnamed_sql_file_fails_loudly(tmp_path):
    (tmp_path / "add-column.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="add-column.sql"):
        migrate.discover(tmp_path)


def test_duplicate_versions_fail_loudly(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="0001"):
        migrate.discover(tmp_path)


def test_ready_is_503_while_migrations_are_pending(fresh_engine, monkeypatch):
    import backend.api.main as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "get_engine", lambda: fresh_engine)
    client = TestClient(api.app)

    pending = client.get("/ready")
    migrate.apply(fresh_engine)
    current = client.get("/ready")

    assert pending.status_code == 503
    assert pending.json()["schema"] == "pending"
    assert current.status_code == 200
    assert current.json()["schema"] == "current"
