# Testing

How to run the skene test suite, including unit tests and live PostgreSQL integration tests.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management and test execution
- A live PostgreSQL database (optional — required only for integration tests)

## Quick start

Run all unit tests:

```bash
uv run pytest tests/ -v
```

Run unit tests with a live PostgreSQL database (integration tests):

```bash
uv run pytest tests/ --db-url "postgresql://postgres:postgres@localhost/postgres" -v
```

## Test categories

### Unit tests (no database required)

These tests run against mocks and local fixtures. They cover:

| Category | Location | What it tests |
|----------|----------|---------------|
| Validators | `tests/test_validators/` | TS parser, loop validator, engine validator |
| Strategies | `tests/test_strategies/` | Results processing, context handling |
| Schema parsers | `tests/test_schema_parsers/` | PostgreSQL schema introspection (mocked) |
| CLI | `tests/test_cli/` | Config resolution, bundle resolution, analyse-journey helpers |
| Journey | `tests/test_journey/` | Pipeline, models, merge, classify, assemble, fs tools |
| Planner | `tests/test_planner/` | Steps, planner logic |
| Codebase | `tests/test_codebase/` | Explorer, filters, tree walking |
| LLM | `tests/test_llm/` | Agent loop, skene provider |
| Growth loops | `tests/test_growth_loops/` | Upstream sync, storage, schema init |
| Objectives | `tests/test_objectives/` | Objective generation |
| Engine | `tests/test_engine_*.py` | Engine generation, storage |
| Config | `tests/test_config.py` | Configuration loading |
| Output paths | `tests/test_output_paths.py` | Path resolution |
| Progress | `tests/test_progress.py` | Progress tracking |
| Feature registry | `tests/test_feature_registry.py` | Feature registry management |

### Integration tests (require `--db-url`)

These tests connect to a real PostgreSQL database. They are marked with `@pytest.mark.db` and are **skipped** when `--db-url` is not provided.

| Test file | What it tests |
|-----------|---------------|
| `tests/test_schema_parsers/test_postgres_live.py` | Live `introspect_db` — empty DB, single table, foreign keys, composite FKs, multi-schema, cross-schema FKs |

## Running with a live PostgreSQL database

The `--db-url` flag tells pytest to connect to a real PostgreSQL server. When provided:

1. The `pg_conn` fixture (in `tests/conftest.py`) creates a live `psycopg` connection.
2. Tests decorated with `@pytest.mark.db` use this connection to run real queries.
3. Without the flag, all `@pytest.mark.db` tests are silently skipped.

### Connection string format

The `--db-url` value must be a valid PostgreSQL connection string:

```
postgresql://<user>:<password>@<host>:<port>/<database>
```

Examples:

```bash
# Local database
uv run pytest tests/ --db-url "postgresql://postgres:password@localhost:5432/mydb" -v

# Remote database
uv run pytest tests/ --db-url "postgresql://postgres:postgres@localhost/postgres" -v

# With SSL and query parameters
uv run pytest tests/ --db-url "postgresql://user:pass@host/db?sslmode=require" -v
```

### Running only integration tests

Run only the database-marked tests:

```bash
uv run pytest tests/ -m db --db-url "postgresql://postgres:password@localhost:5432/mydb" -v
```

Run only the live postgres tests:

```bash
uv run pytest tests/test_schema_parsers/test_postgres_live.py -m db --db-url "postgresql://postgres:password@localhost:5432/mydb" -v
```

### What live tests do

The integration tests in `test_postgres_live.py` are **self-contained** and **safe to run against any database**:

- They create their own tables with unique prefixes (`live_test_*`, `live_schema_*`)
- They clean up after themselves using `try/finally` blocks
- They drop all created tables and schemas when done
- They do not modify or depend on existing data

## Running specific tests

### By file

```bash
uv run pytest tests/test_config.py -v
uv run pytest tests/test_cli/test_resolve_cli_config.py -v
```

### By directory

```bash
uv run pytest tests/test_journey/ -v
uv run pytest tests/test_validators/ -v
```

### By keyword expression

```bash
# Run only tests with "redact" in the name
uv run pytest tests/ -k "redact" -v

# Run only tests with "foreign" in the name
uv run pytest tests/ -k "foreign" -v

# Exclude integration tests
uv run pytest tests/ -k "not db" -v
```

### By marker

```bash
# Run only database tests (requires --db-url)
uv run pytest tests/ -m db --db-url "postgresql://postgres:password@localhost:5432/mydb" -v

# Run only async tests
uv run pytest tests/ -m "asyncio" -v
```

## Fixtures

The shared fixtures live in `tests/conftest.py`:

| Fixture | Type | Description |
|---------|------|-------------|
| `fixtures_path` | `Path` | Path to the `tests/fixtures/` directory |
| `sample_repo_path` | `Path` | Path to `tests/fixtures/sample_repo/` |
| `codebase_explorer` | `CodebaseExplorer` | Pre-instantiated explorer for the sample repo |
| `pg_conn` | `psycopg.Connection \| None` | Live DB connection when `--db-url` is provided, else `None` |

### Using the `pg_conn` fixture

Tests that need a live database check for `None` and skip if no connection is available:

```python
@pytest.mark.db
def test_something(self, pg_conn):
    if pg_conn is None:
        pytest.skip("no --db-url provided")
    
    # Use pg_conn for queries...
    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1")
    
    # The original URL is available for helper functions:
    index = introspect_db(pg_conn._db_url)
```

## Pytest configuration

Settings are in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "db: database integration tests (require --db-url)",
]
```

- **`asyncio_mode = "auto"`** — async test functions are automatically handled by pytest-asyncio. No `@pytest.mark.asyncio` decorator needed.
- **`testpaths = ["tests"]`** — pytest discovers tests under the `tests/` directory by default.

## Development dependencies

Test dependencies are defined in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "ruff (>=0.14.10,<0.15.0)",
    "pytest (>=9.0.3,<10.0.0)",
    "pytest-asyncio (>=1.3.0,<2.0.0)",
]
```

Install them with:

```bash
uv sync --group dev
```

## Test fixtures

Sample data lives in `tests/fixtures/`:

```
tests/fixtures/
└── sample_repo/
    ├── src/
    │   ├── main.py
    │   └── utils.py
```

Use the `sample_repo_path` fixture to reference this directory in tests.

## CI/CD

In CI environments where no database is available, run unit tests only (the default):

```bash
uv run pytest tests/ -v
```

The `@pytest.mark.db` tests will be automatically skipped.

When a database is available in CI, pass `--db-url` via an environment variable:

```bash
uv run pytest tests/ --db-url "$DATABASE_URL" -v
```

## Troubleshooting

### "no --db-url provided" — all tests skipped

This means the `--db-url` flag was not passed. The integration tests are designed to skip gracefully when no database connection is available. Pass the flag to run them:

```bash
uv run pytest tests/ -m db --db-url "postgresql://postgres:password@localhost:5432/mydb" -v
```

### Connection refused / authentication failed

Verify the connection string is correct:

```bash
# Test the connection independently
psql "postgresql://postgres:postgres@localhost/postgres" -c "SELECT 1"
```

### Password leaking in error messages

The codebase includes password redaction (`_redact_db_url`) to prevent passwords from appearing in exception messages. The live tests verify this behavior (`TestDuplicateConstraintNames.test_duplicate_constraint_names_same_table_name` and `TestIntrospectDb.test_password_not_in_exception`).

## Next steps

- [Configuration](configuration.md) — Project and user config setup
- [CLI reference](../reference/cli.md) — All commands and flags
- [Troubleshooting](../troubleshooting.md) — Common issues and solutions
