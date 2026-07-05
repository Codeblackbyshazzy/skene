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
    schemas: list[str],
    tables: list[tuple[str, str]],
    pk_rows: list[dict],
    col_rows: list[dict],
    fk_rows: list[dict],
    idx_rows: list[dict],
) -> MagicMock:
    """Build a fully wired mock psycopg connection.

    psycopg.connect() returns a connection that acts as a context manager,
    so the mock must support __enter__/__exit__ and cursor() must return
    cursors that are also context managers.

    *tables* is a list of ``(schema_name, table_name)`` tuples.

    Cursor call order:
    1. Schema discovery (_discover_user_schemas)
    2. Tables query
    3. Primary keys query
    4. Columns query
    5. Foreign keys query
    6. Indexes query
    """

    def _make_cursor(rows: list[dict]) -> MagicMock:
        cur = MagicMock()
        cur.__enter__ = lambda s: cur
        cur.__exit__ = lambda s, *a: None
        cur.fetchall.return_value = rows
        return cur

    cursors = [
        _make_cursor([{"nspname": s} for s in schemas]),  # schema discovery
        _make_cursor([{"schema_name": s, "table_name": t} for s, t in tables]),  # tables
        _make_cursor(pk_rows),  # primary keys
        _make_cursor(col_rows),  # columns
        _make_cursor(fk_rows),  # foreign keys
        _make_cursor(idx_rows),  # indexes
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
        mock_conn = _build_mock_conn(
            schemas=["public"],
            tables=[],
            pk_rows=[],
            col_rows=[],
            fk_rows=[],
            idx_rows=[],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")
        assert index.files == {}

    def test_single_table_basic(self):
        """A single table with columns, PK, FK, and index."""
        mock_conn = _build_mock_conn(
            schemas=["public"],
            tables=[("public", "users")],
            pk_rows=[{"schema_name": "public", "table_name": "users", "pk_columns": ["id"]}],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "email",
                    "data_type": "text",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
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
                    "schema_name": "public",
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
        assert "public.users.sql" in index.files
        tables = index.files["public.users.sql"]
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
            schemas=["public"],
            tables=[("public", "orders"), ("public", "users")],
            pk_rows=[
                {"schema_name": "public", "table_name": "orders", "pk_columns": ["id"]},
                {"schema_name": "public", "table_name": "users", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "orders",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
                    "table_name": "orders",
                    "column_name": "user_id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
            ],
            fk_rows=[
                {
                    "schema_name": "public",
                    "table_name": "orders",
                    "columns": ["user_id"],
                    "references_schema": "public",
                    "references_table": "users",
                    "references_columns": ["id"],
                },
            ],
            idx_rows=[
                {
                    "schema_name": "public",
                    "table_name": "orders",
                    "index_name": "orders_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
                {
                    "schema_name": "public",
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
        orders_tables = index.files["public.orders.sql"]
        for t in orders_tables:
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
            schemas=["public"],
            tables=[("public", "user_summary")],
            pk_rows=[],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "user_summary",
                    "column_name": "user_id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
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
        assert "public.user_summary.sql" in index.files
        tables = index.files["public.user_summary.sql"]
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
            schemas=["public"],
            tables=[("public", "users"), ("public", "posts")],
            pk_rows=[
                {"schema_name": "public", "table_name": "users", "pk_columns": ["id"]},
                {"schema_name": "public", "table_name": "posts", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
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
        assert "public.posts.sql" in file_keys
        assert "public.users.sql" in file_keys


class TestSchemaDiscovery:
    """Test that system/extension schemas are excluded from introspection."""

    def test_system_schemas_excluded(self):
        """pg_catalog, information_schema, and pg_toast are not returned as user schemas."""
        # The mock returns only 'public' and 'app' as user schemas;
        # pg_catalog, information_schema, and a fake extension schema
        # are implicitly excluded by the _discover_user_schemas query.
        mock_conn = _build_mock_conn(
            schemas=["public", "app"],  # only user schemas returned
            tables=[("public", "users")],
            pk_rows=[{"schema_name": "public", "table_name": "users", "pk_columns": ["id"]}],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "users",
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

        # Should find the table in public schema
        assert len(index.files) == 1
        assert "public.users.sql" in index.files
        tables = index.files["public.users.sql"]
        assert len(tables) == 1
        assert tables[0].name == "users"

    def test_custom_schema_tables_included(self):
        """Tables in non-public user schemas are included."""
        mock_conn = _build_mock_conn(
            schemas=["public", "tenant_a"],
            tables=[("tenant_a", "customers")],
            pk_rows=[{"schema_name": "tenant_a", "table_name": "customers", "pk_columns": ["id"]}],
            col_rows=[
                {
                    "schema_name": "tenant_a",
                    "table_name": "customers",
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

        assert len(index.files) == 1
        assert "tenant_a.customers.sql" in index.files
        tables = index.files["tenant_a.customers.sql"]
        assert len(tables) == 1
        assert tables[0].name == "customers"


class TestMultiSchema:
    """Test that same-named tables in different schemas stay distinct."""

    def test_same_table_name_different_schemas(self):
        """public.users and auth.users produce separate files, no collision."""
        mock_conn = _build_mock_conn(
            schemas=["public", "auth"],
            tables=[("public", "users"), ("auth", "users")],
            pk_rows=[
                {"schema_name": "public", "table_name": "users", "pk_columns": ["id"]},
                {"schema_name": "auth", "table_name": "users", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "email",
                    "data_type": "text",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "auth",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "auth",
                    "table_name": "users",
                    "column_name": "email",
                    "data_type": "text",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "auth",
                    "table_name": "users",
                    "column_name": "encrypted_password",
                    "data_type": "text",
                    "is_nullable": "YES",
                    "column_default": None,
                },
            ],
            fk_rows=[],
            idx_rows=[
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "index_name": "users_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
                {
                    "schema_name": "auth",
                    "table_name": "users",
                    "index_name": "users_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
            ],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        # Two separate files, no collision
        assert len(index.files) == 2
        assert "public.users.sql" in index.files
        assert "auth.users.sql" in index.files

        # public.users has 2 columns (id, email)
        pub_tables = index.files["public.users.sql"]
        assert len(pub_tables) == 1
        pub_user = pub_tables[0]
        assert pub_user.name == "users"
        assert len(pub_user.columns) == 2
        assert {c.name for c in pub_user.columns} == {"id", "email"}

        # auth.users has 3 columns (id, email, encrypted_password)
        auth_tables = index.files["auth.users.sql"]
        assert len(auth_tables) == 1
        auth_user = auth_tables[0]
        assert auth_user.name == "users"
        assert len(auth_user.columns) == 3
        assert {c.name for c in auth_user.columns} == {
            "id",
            "email",
            "encrypted_password",
        }

        # Both have the same index name but in different files
        assert pub_user.indexes[0].name == "users_pkey"
        assert auth_user.indexes[0].name == "users_pkey"

    def test_cross_schema_foreign_key(self):
        """app.orders.user_id → public.users.id is captured, not dropped."""
        mock_conn = _build_mock_conn(
            schemas=["app", "public"],
            tables=[("app", "orders"), ("public", "users")],
            pk_rows=[
                {"schema_name": "app", "table_name": "orders", "pk_columns": ["id"]},
                {"schema_name": "public", "table_name": "users", "pk_columns": ["id"]},
            ],
            col_rows=[
                {
                    "schema_name": "app",
                    "table_name": "orders",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "app",
                    "table_name": "orders",
                    "column_name": "user_id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "schema_name": "public",
                    "table_name": "users",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
            ],
            fk_rows=[
                {
                    "schema_name": "app",
                    "table_name": "orders",
                    "columns": ["user_id"],
                    "references_schema": "public",
                    "references_table": "users",
                    "references_columns": ["id"],
                },
            ],
            idx_rows=[
                {
                    "schema_name": "app",
                    "table_name": "orders",
                    "index_name": "orders_pkey",
                    "columns": ["id"],
                    "is_unique": True,
                },
            ],
        )
        with patch("psycopg.connect", return_value=mock_conn):
            index = introspect_db("postgresql://user:pass@localhost/db")

        assert len(index.files) == 2
        assert "app.orders.sql" in index.files
        assert "public.users.sql" in index.files

        orders = index.files["app.orders.sql"][0]
        assert len(orders.foreign_keys) == 1
        fk = orders.foreign_keys[0]
        assert fk.columns == ["user_id"]
        assert fk.references_table == "users"
        assert fk.references_columns == ["id"]
