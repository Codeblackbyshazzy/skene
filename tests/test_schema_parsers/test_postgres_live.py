"""Tests for skene.analyzers.schema_parsers.postgres_live."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from skene.analyzers.schema_parsers.models import (
    ForeignKey,
    TableInfo,
)
from skene.analyzers.schema_parsers.postgres_live import introspect_db


def _build_mock_conn(
    tables: list[str],
    pk_rows: list[dict],
    col_rows: list[dict],
    fk_rows: list[dict],
    idx_rows: list[dict],
) -> MagicMock:
    """Build a fully wired mock psycopg connection.

    psycopg.connect() returns a connection that acts as a context manager,
    so the mock must support __enter__/__exit__ and cursor() must return
    cursors that are also context managers.
    """

    def _make_cursor(rows: list[dict]) -> MagicMock:
        cur = MagicMock()
        cur.__enter__ = lambda s: cur
        cur.__exit__ = lambda s, *a: None
        cur.fetchall.return_value = rows
        return cur

    cursors = [
        _make_cursor([{"table_name": t} for t in tables]),
        _make_cursor(pk_rows),
        _make_cursor(col_rows),
        _make_cursor(fk_rows),
        _make_cursor(idx_rows),
    ]
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    conn.cursor.side_effect = cursors
    return conn


class TestIntrospectDb:
    """Test introspect_db produces correct SchemaIndex."""

    def test_empty_db_returns_empty_index(self):
        """An empty database returns a SchemaIndex with no files."""
        mock_conn = _build_mock_conn(tables=[], pk_rows=[], col_rows=[], fk_rows=[], idx_rows=[])
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")
        assert index.files == {}

    def test_single_table_basic(self):
        """A single table with columns, PK, FK, and index."""
        mock_conn = _build_mock_conn(
            tables=["users"],
            pk_rows=[{"table_name": "users", "pk_columns": ["id"]}],
            col_rows=[
                {
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "users",
                    "column_name": "email",
                    "data_type": "text",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "users",
                    "column_name": "name",
                    "data_type": "text",
                    "is_nullable": "YES",
                    "column_default": None,
                },
            ],
            fk_rows=[],
            idx_rows=[
                {
                    "table_name": "users",
                    "index_name": "users_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
            ],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        assert len(index.files) == 1
        tables = list(index.files.values())[0]
        assert len(tables) == 1
        t = tables[0]
        assert isinstance(t, TableInfo)
        assert t.name == "users"
        assert len(t.columns) == 3
        assert t.columns[0].name == "id"
        assert t.columns[0].type == "uuid"
        assert t.columns[0].nullable is False
        assert t.columns[1].name == "email"
        assert t.columns[1].nullable is False
        assert t.columns[2].name == "name"
        assert t.columns[2].nullable is True
        assert t.primary_key == ["id"]
        assert len(t.foreign_keys) == 0
        assert len(t.indexes) == 1
        assert t.indexes[0].name == "users_pkey"
        assert t.indexes[0].unique is True

    def test_table_with_foreign_keys(self):
        """Tables with FK relationships are captured."""
        mock_conn = _build_mock_conn(
            tables=["orders", "users"],
            pk_rows=[
                {"table_name": "orders", "pk_columns": ["id"]},
                {"table_name": "users", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "table_name": "orders",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "orders",
                    "column_name": "user_id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
            ],
            fk_rows=[
                {
                    "table_name": "orders",
                    "columns": ["user_id"],
                    "references_table": "users",
                    "references_columns": ["id"],
                },
            ],
            idx_rows=[
                {
                    "table_name": "orders",
                    "index_name": "orders_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
                {
                    "table_name": "users",
                    "index_name": "users_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
            ],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        assert len(index.files) == 2
        for tables in index.files.values():
            for t in tables:
                if t.name == "orders":
                    assert len(t.foreign_keys) == 1
                    fk = t.foreign_keys[0]
                    assert isinstance(fk, ForeignKey)
                    assert fk.columns == ["user_id"]
                    assert fk.references_table == "users"
                    assert fk.references_columns == ["id"]

    def test_views_included(self):
        """Views are included in the schema index."""
        mock_conn = _build_mock_conn(
            tables=["user_summary"],
            pk_rows=[],
            col_rows=[
                {
                    "table_name": "user_summary",
                    "column_name": "user_id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "user_summary",
                    "column_name": "total_orders",
                    "data_type": "integer",
                    "is_nullable": "NO",
                    "column_default": None,
                },
            ],
            fk_rows=[],
            idx_rows=[],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        assert len(index.files) == 1
        for tables in index.files.values():
            assert len(tables) == 1
            assert tables[0].name == "user_summary"

    def test_connection_error_propagates(self):
        """Connection errors are not swallowed."""
        import psycopg

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = psycopg.OperationalError("connection refused")
        mock_cursor.__enter__ = lambda s: mock_cursor
        mock_cursor.__exit__ = lambda s, *a: None

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = lambda s, *a: None
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg.connect", return_value=mock_conn):
            with pytest.raises(psycopg.OperationalError, match="connection refused"):
                introspect_db("postgresql://user:badpass@localhost/nonexistent")

    def test_password_not_in_exception(self):
        """Password is not leaked in exception messages."""
        import psycopg

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = psycopg.OperationalError("password authentication failed")
        mock_cursor.__enter__ = lambda s: mock_cursor
        mock_cursor.__exit__ = lambda s, *a: None

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = lambda s, *a: None
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg.connect", return_value=mock_conn):
            with pytest.raises(psycopg.OperationalError) as exc_info:
                introspect_db("postgresql://testuser:supersecret@localhost/testdb")
        assert "supersecret" not in str(exc_info.value)

    def test_multiple_tables_different_files(self):
        """Each table gets its own schema file entry."""
        mock_conn = _build_mock_conn(
            tables=["users", "posts"],
            pk_rows=[
                {"table_name": "users", "pk_columns": ["id"]},
                {"table_name": "posts", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_name": "posts",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
            ],
            fk_rows=[],
            idx_rows=[],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        assert len(index.files) == 2
        file_keys = sorted(index.files.keys())
        assert "posts.sql" in file_keys
        assert "users.sql" in file_keys
