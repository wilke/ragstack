"""Root-level shared fixtures, and the harness's own preconditions.

**The skip/fail doctrine this file implements** (#432): an absent opt-in is a
*skip*, loudly, naming the variable that would enable it; a precondition that
is claimed but false is a *failure*, naming both sides of the comparison. The
import-origin guard below and ``pg_test_dsn`` are two instances of it.

``pytest_configure`` — the import-origin guard
----------------------------------------------
A test run has to be able to prove which ``ragstack`` it imported. On the dev
host it could not: the conda envs carry an editable install resolving
``ragstack`` to ``/rag/repos/ragstack/python`` (a legacy *production*
checkout). ``sys.path`` puts the CWD first, so running from ``python/``
usually wins — but that is incidental. Any invocation whose CWD is elsewhere
(a worktree run from the repo root, an IDE runner, a plugin importing early)
silently tested the production checkout: a green run that proved nothing about
the branch, or a red one from code nobody wrote. ``make test-conformance-authz``
was doing exactly that, because its runner booted uvicorn before it ``cd``'d.

The guard checks the *outcome* rather than any one cause — it imports
``ragstack`` and asks where the module actually came from — so it holds no
matter who imported first or how ``sys.path`` was arranged. Rootdir is
``python/``, so this one file covers unit, api, ingestion, eval, integration
and perf. Escape hatch for a deliberate out-of-tree run (validating an
installed wheel): ``RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT=1`` downgrades it to a
warning that still prints both paths.

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
import sys
import uuid
import warnings
from pathlib import Path

import pytest
import pytest_asyncio

#: The tree under test: ``python/``, the directory holding ``ragstack/`` and
#: ``tests/``. Resolved, so worktrees and symlinked checkouts compare equal.
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]

#: Deliberately verbose and grep-able: nothing routine should set this.
ALLOW_FOREIGN_IMPORT_VAR = "RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT"

#: Stable marker so the meta-tests (``tests/unit/test_harness_guard.py``) can
#: assert the guard fired without matching on prose.
_GUARD_BANNER = "RAGSTACK TEST HARNESS: wrong `ragstack` import origin (#432)"


def _import_origin_problem() -> str | None:
    """Return a message describing the import-origin violation, or ``None``.

    Naming both paths is the contract: the acceptance criterion in #432 is that
    a run which would import code outside the tree under test fails *loudly*,
    saying which code it got and which it expected.
    """
    try:
        import ragstack
    except Exception as exc:  # pragma: no cover - exercised by the meta-tests
        return (
            f"{_GUARD_BANNER}\n"
            f"  imported from: <import failed: {exc!r}>\n"
            f"  expected under: {CHECKOUT_ROOT}\n"
            "This suite cannot run without importing the checkout under test. "
            f"Run pytest with PYTHONPATH={CHECKOUT_ROOT} (or from that directory)."
        )

    origin = getattr(ragstack, "__file__", None)
    if origin is None:  # namespace package / frozen import — origin unprovable
        return (
            f"{_GUARD_BANNER}\n"
            "  imported from: <no __file__; a namespace package shadows the checkout>\n"
            f"  expected under: {CHECKOUT_ROOT}\n"
            f"Run pytest with PYTHONPATH={CHECKOUT_ROOT} (or from that directory)."
        )

    imported = Path(origin).resolve()
    if imported.is_relative_to(CHECKOUT_ROOT):
        return None

    return (
        f"{_GUARD_BANNER}\n"
        f"  imported from: {imported}\n"
        f"  expected under: {CHECKOUT_ROOT}\n"
        "This run would prove nothing about this checkout: it exercises the code at "
        "the first path, not the code at the second. On the dev host the usual cause "
        "is the editable install in the conda env, which resolves `ragstack` to the "
        "legacy production checkout whenever the CWD is not the tree under test.\n"
        f"Fix: run pytest with PYTHONPATH={CHECKOUT_ROOT}, or from that directory "
        "(`make test-python` does both). To test an installed `ragstack` on purpose, "
        f"set {ALLOW_FOREIGN_IMPORT_VAR}=1 — the run then warns instead of failing."
    )


def pytest_configure(config: pytest.Config) -> None:
    """Fail the run before collection if ``ragstack`` came from another tree.

    Runs before ``tests/api/conftest.py`` imports any ``ragstack.*`` module, so
    the module this checks is the module every later test will get.
    """
    problem = _import_origin_problem()
    if problem is None:
        return

    if os.environ.get(ALLOW_FOREIGN_IMPORT_VAR) == "1":
        # Still loud, still names both paths — just not fatal.
        print(f"\n{problem}\n({ALLOW_FOREIGN_IMPORT_VAR}=1 — continuing anyway)\n",
              file=sys.stderr, flush=True)
        warnings.warn(problem, RuntimeWarning, stacklevel=2)
        config.issue_config_time_warning(pytest.PytestConfigWarning(problem), stacklevel=2)
        return

    raise pytest.UsageError(problem)


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
