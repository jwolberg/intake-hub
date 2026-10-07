"""Migration script splitting (backend/db/migrate.py).

A ``;`` inside a ``--`` comment must not split a statement — the baseline
migration has one, and splitting on it once sent a comment fragment to
Postgres as SQL, so schema setup failed on every fresh database (#0007).
"""

from backend.db.migrate import discover, split_statements


def test_semicolon_inside_comment_does_not_split():
    sql = "-- note (e.g. 'x'); more note\nCREATE TABLE a (id TEXT);\nCREATE TABLE b (id TEXT);"
    assert split_statements(sql) == ["CREATE TABLE a (id TEXT)", "CREATE TABLE b (id TEXT)"]


def test_shipped_migration_statements_all_start_with_sql_keywords():
    for migration in discover():
        statements = split_statements(migration.path.read_text(encoding="utf-8"))
        assert statements, migration.name
        for stmt in statements:
            assert stmt.split()[0].upper() in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE"}, (
                migration.name,
                stmt[:60],
            )
