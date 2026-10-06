"""Schema script splitting (backend/db/session.py).

A ``;`` inside a ``--`` comment must not split a statement — the shipped
schema.sql has one, and splitting on it sent a comment fragment to Postgres as
SQL, so ``init_schema`` failed on every fresh database.
"""

from backend.db.session import _SCHEMA_PATH, _split_statements


def test_semicolon_inside_comment_does_not_split():
    sql = "-- note (e.g. 'x'); more note\nCREATE TABLE a (id TEXT);\nCREATE TABLE b (id TEXT);"
    assert _split_statements(sql) == ["CREATE TABLE a (id TEXT)", "CREATE TABLE b (id TEXT)"]


def test_shipped_schema_statements_all_start_with_sql_keywords():
    statements = _split_statements(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert statements
    for stmt in statements:
        assert stmt.split()[0].upper() in {"CREATE", "ALTER"}, stmt[:60]
