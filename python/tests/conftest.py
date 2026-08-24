"""Root-level shared fixtures.

``pg_test_dsn`` is the ONLY sanctioned way any test in this tree may open a
Postgres connection (#130 follow-up). A prior version of the Postgres job
store tests defaulted to ``postgresql://ragstack:ragstack@localhost/ragstack``
when no DSN was configured — on this host that default happens to BE the
shared infra Postgres a production API instance points
``JOB_STORE_BACKEND=postgres`` at, and an unattended test run applied the
#130 ``tenant_id`` migration to its live ``jobs`` table. No test may ever
touch a real database by default again:

- Opt-in only, via ``RAGSTACK_TEST_PG_DSN`` — deliberately a different name
  than the old ``TEST_PG_DSN``, so a stale env var from before this fix
  cannot silently re-enable a real-database run. Unset -> skip, never a
  fallback DSN.
- Even when opted in, every test gets its own throwaway schema
  (``test_<8 hex chars>``) and never touches ``public`` (or any other
  pre-existing schema) on whatever server ``RAGSTACK_TEST_PG_DSN`` names.
  The schema is dropped ``CASCADE`` on teardown.
- The DSN handed to a test is scoped to that schema via a ``search_path``
  query parameter. asyncpg treats any DSN query key it doesn't recognize as a
  ``server_settings`` entry (see ``asyncpg.connect_utils``) and sends it as a
  startup-packet runtime parameter on EVERY connection opened from that DSN —
  including every connection ``PostgresJobStore``'s internal pool opens over
  its lifetime — so this requires no change to ``ragstack/jobstore.py``
  itself: ``jobstore.py``'s DDL and queries use unqualified table names
  (``jobs``, ``job_items``), which resolve against whatever ``search_path``
  is in effect.
- ``RAGSTACK_TEST_PG_DSN`` must still name a SCRATCH server/database you own
  — the schema isolation here is defence in depth, not a substitute for
  pointing this at something other than shared/production infra.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def pg_test_dsn():
    """Yield a Postgres DSN scoped to a fresh, per-test throwaway schema.

    Skips (does not fail, does not fall back to any default) unless
    ``RAGSTACK_TEST_PG_DSN`` is set.
    """
    base_dsn = os.environ.get("RAGSTACK_TEST_PG_DSN")
    if not base_dsn:
        pytest.skip("set RAGSTACK_TEST_PG_DSN to a SCRATCH database to run Postgres tests")

    asyncpg = pytest.importorskip("asyncpg")

    schema = f"test_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(base_dsn)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await conn.close()

    sep = "&" if "?" in base_dsn else "?"
    scoped_dsn = f"{base_dsn}{sep}search_path={schema}"

    try:
        yield scoped_dsn
    finally:
        conn = await asyncpg.connect(base_dsn)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()
