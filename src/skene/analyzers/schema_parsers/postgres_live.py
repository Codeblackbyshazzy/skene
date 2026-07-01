"""Introspect a live PostgreSQL database and return a SchemaIndex.

Uses psycopg3 (sync API) to query the PostgreSQL catalog in a small number
of set-returning queries, then assembles the results into the same
:class:`SchemaIndex` that the SQL-file parser produces.

Never raises the raw connection string or password in an exception message
or log line.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from skene.analyzers.schema_parsers.models import (
    ColumnInfo,
    ForeignKey,
    IndexInfo,
    SchemaIndex,
    TableInfo,
)
from skene.output import debug

# relkind filter: ordinary tables, views, materialized views.
_RELKIND_FILTER = "('r', 'v', 'm')"


def introspect_db(db_url: str, *, connect_timeout: int = 10) -> SchemaIndex:
    """Connect to *db_url*, introspect the schema, return a :class:`SchemaIndex`.

    Parameters
    ----------
    db_url:
        A complete PostgreSQL connection string (libpq URL or keyword DSN).
    connect_timeout:
        Seconds to wait for the TCP connection to be established.
    """
    index = SchemaIndex()

    with psycopg.connect(db_url, connect_timeout=connect_timeout, row_factory=dict_row) as conn:
        tables_by_file: dict[str, dict[str, TableInfo]] = {}

        # --- 1. Collect tables (names + primary keys) ---
        tables_query = """\
            SELECT c.relname AS table_name,
                   i.indisprimary AS is_pk,
                   array_agg(a.attname ORDER BY array_position(i.indkey, a.attnum)) AS pk_columns
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_index i ON i.indrelid = c.oid
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
            WHERE c.relkind IN _RELKIND_PLACEHOLDER
              AND n.nspname = 'public'
            GROUP BY c.relname, i.indisprimary, a.attname
        """
        # We need a different approach because array_agg with ORDER BY
        # inside a GROUP BY on indisprimary creates ambiguity.
        # Use a simpler query and handle PK columns separately.

        tables_query = """\
            SELECT DISTINCT c.relname AS table_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN _RELKIND_PLACEHOLDER
              AND n.nspname = 'public'
            ORDER BY c.relname
        """
        tables_query = tables_query.replace("_RELKIND_PLACEHOLDER", _RELKIND_FILTER)

        with conn.cursor() as cur:
            cur.execute(tables_query)
            table_names: list[str] = [row["table_name"] for row in cur.fetchall()]

        if not table_names:
            return index

        # --- 2. Collect all data in parallel-ish queries (single connection, sequential) ---

        # 2a. Primary keys per table
        pk_query = """\
            SELECT c.relname AS table_name,
                   array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS pk_columns
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_index ix ON ix.indrelid = c.oid AND ix.indisprimary
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(ix.indkey)
            WHERE c.relname = ANY(%s)
              AND n.nspname = 'public'
            GROUP BY c.relname
        """
        with conn.cursor() as cur:
            cur.execute(pk_query, (table_names,))
            pk_map: dict[str, list[str]] = {row["table_name"]: row["pk_columns"] for row in cur.fetchall()}

        # 2b. Columns per table
        col_query = """\
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = ANY(%s)
              AND table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """
        with conn.cursor() as cur:
            cur.execute(col_query, (table_names,))
            # Group by table
            cols_by_table: dict[str, list[dict[str, Any]]] = {}
            for row in cur.fetchall():
                cols_by_table.setdefault(row["table_name"], []).append(row)

        # 2c. Foreign keys
        fk_query = """\
            SELECT tc.table_name,
                   array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS columns,
                   ccu.table_name AS references_table,
                   array_agg(ccu.column_name ORDER BY kcu.ordinal_position) AS references_columns
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = ANY(%s)
              AND tc.table_schema = 'public'
            GROUP BY tc.table_name, ccu.table_name
        """
        with conn.cursor() as cur:
            cur.execute(fk_query, (table_names,))
            fk_map: dict[str, list[dict[str, Any]]] = {}
            for row in cur.fetchall():
                fk_map.setdefault(row["table_name"], []).append(row)

        # 2d. Indexes
        idx_query = """\
            SELECT t.relname AS table_name,
                   i.relname AS index_name,
                   array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns,
                   ix.indisunique AS is_unique
            FROM pg_class t
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_index ix ON ix.indexrelid = i.oid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE t.relkind IN _IDXRELKIND_PLACEHOLDER
              AND t.relname = ANY(%s)
              AND n.nspname = 'public'
            GROUP BY t.relname, i.relname, ix.indisunique
        """
        idx_query = idx_query.replace("_IDXRELKIND_PLACEHOLDER", "('r', 'v', 'm')")
        with conn.cursor() as cur:
            cur.execute(idx_query, (table_names,))
            idx_map: dict[str, list[dict[str, Any]]] = {}
            for row in cur.fetchall():
                idx_map.setdefault(row["table_name"], []).append(row)

        # --- 3. Assemble TableInfo objects ---
        for tname in table_names:
            columns = [
                ColumnInfo(
                    name=r["column_name"],
                    type=r["data_type"],
                    nullable=r["is_nullable"] == "YES",
                    default=r["column_default"],
                )
                for r in cols_by_table.get(tname, [])
            ]

            primary_key = pk_map.get(tname, [])

            foreign_keys = [
                ForeignKey(
                    columns=r["columns"],
                    references_table=r["references_table"],
                    references_columns=r["references_columns"],
                )
                for r in fk_map.get(tname, [])
            ]

            indexes = [
                IndexInfo(
                    name=r["index_name"],
                    columns=r["columns"],
                    unique=r["is_unique"],
                )
                for r in idx_map.get(tname, [])
            ]

            # Use a synthetic schema file name: "<table_name>.sql" to mirror
            # the file parser's per-file structure.
            schema_file = f"{tname}.sql"
            table_info = TableInfo(
                name=tname,
                schema_file=schema_file,
                columns=columns,
                primary_key=primary_key,
                foreign_keys=foreign_keys,
                indexes=indexes,
            )

            tables_by_file.setdefault(schema_file, {})[tname] = table_info

        # Flatten into SchemaIndex.files dict (key = schema_file)
        for schema_file, tables_dict in tables_by_file.items():
            index.files[schema_file] = sorted(tables_dict.values(), key=lambda t: t.name)

    debug(f"Introspected {len(index.files)} schema files, {sum(len(t) for t in index.files.values())} tables")
    return index
