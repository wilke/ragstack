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
wins — and under pytest specifically, prepend importmode re-inserts the rootdir
(``tests/`` is a package, so the basedir is ``python/``), which rescues even the
wrong-CWD case. That is *incidental protection from another tool*, not a
property of this suite: it does not hold for a plugin that imports ``ragstack``
during startup, for a pre-seeded ``sys.modules``, or — the case that actually
bit — for anything booting the app outside pytest. ``make test-conformance-authz``
booted uvicorn before it ``cd``'d and so contract-tested the production checkout
on every run of its life; that path is covered by ``conformance/boot_env.sh``,
not by this guard.

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
- A second line of defence refuses (``pytest.fail``, not skip — the opt-in is
  *claimed*, and it names a database no test may touch) a DSN that is not
  provably a test database. The rule, exactly, checked in this order:

  1. The DSN must be a ``postgresql://`` URL, and must not name a connection
     service (a ``service`` query key, or ``PGSERVICE`` set): in either case
     the database comes from somewhere the guard cannot read.
  2. The effective database name — the URL path; else a ``dbname``/``database``
     query key; else ``PGDATABASE``; else the user name, which is what libpq
     and asyncpg fall back to — must not be on the denylist:
     ``_PG_DENY_BUILTIN`` plus the comma-separated ``RAGSTACK_TEST_PG_DENY``
     (case-insensitive, whole name).
  3. It must contain ``test`` or ``scratch`` (case-insensitive substring), e.g.
     ``ragstack_test``. This is what keeps per-tenant databases out:
     ``apptainer/new-tenant.sh --postgres`` names each after its tenant, so no
     built-in denylist can enumerate them.

  The refusal names the database and a password-redacted DSN, never the
  password.
"""
from __future__ import annotations

import os
import sys
import uuid
import warnings
from pathlib import Path

import pytest
import pytest_asyncio

# DOI enrichment ships ON (#634) and is the one part of ingest that reaches the
# public internet (Crossref, DataCite, the NCBI ID Converter). No test may make
# those requests by default, so the whole suite runs with it OFF: set in the
# environment here — before ``ragstack.config`` builds ``settings``, and so it
# is inherited by every child process a test spawns — and re-asserted per test
# by ``_doi_enrichment_off`` below in case something imported settings first.
# Tests that exercise the ON path monkeypatch it back on explicitly; the
# shipped default is asserted against ``Settings.model_fields``, not the env.
os.environ["DOI_ENRICHMENT_ENABLED"] = "false"

# ``require_durable_backends`` ships TRUE (#651): the memory vector/text/graph
# backends are test-only, and a deployment that forgets the setting must refuse
# to boot on them. The suite boots on exactly those backends, so it opts out
# explicitly — here, before ``settings`` is built, so child processes inherit it
# too. Tests of the durable path monkeypatch it back on; the shipped default is
# asserted against ``Settings.model_fields`` (tests/unit/test_deps.py).
os.environ["REQUIRE_DURABLE_BACKENDS"] = "false"

# ``postgres_dsn`` defaults to ``postgresql+asyncpg://ragstack:ragstack@localhost/ragstack``
# — verbatim the DSN that migrated a production ``jobs`` table in #369. Child
# processes are pinned off it by ``tests/pinned_env_support.py``; this pins the
# pytest process itself, so an in-process ``settings`` with a postgres backend
# (an ambient ``JOB_STORE_BACKEND=postgres``, a future test that forgets its
# DSN) reaches a dead port instead of the shared instance. Unconditional, so an
# exported deployment ``POSTGRES_DSN`` is overridden too. The only sanctioned
# Postgres a test may open is ``pg_test_dsn`` below.
os.environ["POSTGRES_DSN"] = "postgresql+asyncpg://ragstack:ragstack@127.0.0.1:1/ragstack"

#: The tree under test:``python/``, the directory holding ``ragstack/`` and
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

    Deliberately checks the *outcome* — which module object is in
    ``sys.modules`` — rather than trying to run before whoever imported it.
    Ordering is not something this can promise (initial conftests load before
    ``pytest_configure`` for a direct-path invocation, and plugins load earlier
    still), and it does not need to: whoever imported first, this reports the
    truth about the module every later test will get.
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


@pytest.fixture(autouse=True)
def _doi_enrichment_off(monkeypatch):
    """Keep DOI enrichment OFF for every test (#634) — see the module-level
    ``DOI_ENRICHMENT_ENABLED`` pin. A test that wants the ON path sets it with
    its own ``monkeypatch.setattr``, which runs after this and is undone with it.

    Guarded by ``hasattr`` because the import-origin escape hatch deliberately
    runs this suite against a foreign ``ragstack`` that may predate the setting.
    """
    from ragstack.config import settings

    if hasattr(settings, "doi_enrichment_enabled"):
        monkeypatch.setattr(settings, "doi_enrichment_enabled", False)


#: Database names a deployment uses, which ``pg_test_dsn`` refuses outright:
#: ``ragstack`` is ``POSTGRES_DB`` in ``deploy/docker-compose.infra.yml`` and
#: ``apptainer/up.sh`` and the database in ``config.py``'s ``postgres_dsn``
#: default — the one #369's migration landed in; ``postgres`` is the server's
#: maintenance database (and ``new-tenant.sh``'s example admin DSN); the
#: templates are cloned into every new database. Per-tenant databases are named
#: after the tenant and so cannot be listed here; ``_PG_TEST_MARKERS`` keeps
#: them out instead.
_PG_DENY_BUILTIN = frozenset({"ragstack", "postgres", "template0", "template1"})

#: A test database's name must contain one of these (case-insensitive).
_PG_TEST_MARKERS = ("test", "scratch")


def _redact_dsn(dsn: str) -> str:
    """``dsn`` with any password replaced by ``***`` — safe to print."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "<unparseable DSN>"
    userinfo, at, hostinfo = parts.netloc.rpartition("@")
    if at and ":" in userinfo:
        userinfo = userinfo.split(":", 1)[0] + ":***"
    netloc = f"{userinfo}{at}{hostinfo}"
    pairs = []
    for kv in filter(None, parts.query.split("&")):
        key = kv.split("=", 1)[0]
        pairs.append(f"{key}=***" if key.lower() == "password" else kv)
    query = "&".join(pairs)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _pg_effective_database(dsn: str, env) -> str:
    """The database a connection from ``dsn`` would open, resolved the way
    asyncpg does: URL path, else ``dbname``/``database`` query key, else
    ``PGDATABASE``, else the user (URL, then ``PGUSER``, then the OS user)."""
    import getpass
    from urllib.parse import parse_qs, unquote, urlsplit

    parts = urlsplit(dsn)
    name = unquote(parts.path.lstrip("/"))
    if not name:
        query = parse_qs(parts.query)
        name = (query.get("dbname") or query.get("database") or [""])[0]
    if not name:
        name = env.get("PGDATABASE", "")
    if not name:
        userinfo, at, _ = parts.netloc.rpartition("@")
        name = unquote(userinfo.split(":", 1)[0]) if at else ""
    if not name:
        name = env.get("PGUSER", "") or getpass.getuser()
    return name


def _pg_test_dsn_problem(dsn: str, env) -> str | None:
    """Why ``dsn`` must not be used as ``RAGSTACK_TEST_PG_DSN``, or ``None``.

    Pure — opens no connection — so ``tests/unit/test_pg_test_dsn_guard.py``
    exercises it with fake DSNs. See the module docstring for the rule.
    """
    from urllib.parse import parse_qs, urlsplit

    try:
        parts = urlsplit(dsn)
        scheme = parts.scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in {"postgres", "postgresql"}:
        # Not echoed at all: a key=value DSN has no URL shape to redact.
        return (
            "RAGSTACK_TEST_PG_DSN is not a postgresql:// URL; the test-database "
            "guard cannot read its database name, so it refuses (#369)."
        )
    redacted = _redact_dsn(dsn)
    if "service" in parse_qs(parts.query) or env.get("PGSERVICE"):
        return (
            f"RAGSTACK_TEST_PG_DSN ({redacted}) resolves through a connection service "
            "(a 'service' query key or PGSERVICE), so its database comes from a "
            "service file the test-database guard cannot see; it refuses (#369). "
            "Name the database in the URL and unset PGSERVICE."
        )
    database = _pg_effective_database(dsn, env)
    extra = {
        n.strip().lower() for n in env.get("RAGSTACK_TEST_PG_DENY", "").split(",") if n.strip()
    }
    denied = _PG_DENY_BUILTIN | extra
    if database.lower() in denied:
        source = "built-in" if database.lower() in _PG_DENY_BUILTIN else "RAGSTACK_TEST_PG_DENY"
        return (
            f"RAGSTACK_TEST_PG_DSN names database {database!r} ({redacted}), which is "
            f"on the {source} denylist of deployment databases (#369). Postgres tests "
            "create and drop schemas there. Point RAGSTACK_TEST_PG_DSN at a scratch "
            "database whose name contains 'test' or 'scratch', e.g. 'ragstack_test'. "
            f"Denied: {', '.join(sorted(denied))}."
        )
    if not any(m in database.lower() for m in _PG_TEST_MARKERS):
        return (
            f"RAGSTACK_TEST_PG_DSN names database {database!r} ({redacted}), whose name "
            "does not mark it as a test database (#369). Postgres tests create and drop "
            "schemas there, and deployment databases — per-tenant ones are named after "
            "their tenant — cannot all be listed. Use a scratch database whose name "
            "contains 'test' or 'scratch', e.g. 'ragstack_test'."
        )
    return None


@pytest_asyncio.fixture
async def pg_test_dsn():
    """Yield a Postgres DSN scoped to a fresh, per-test throwaway schema.

    Skips (does not fail, does not fall back to any default) unless
    ``RAGSTACK_TEST_PG_DSN`` is set.
    """
    base_dsn = os.environ.get("RAGSTACK_TEST_PG_DSN")
    if not base_dsn:
        pytest.skip("set RAGSTACK_TEST_PG_DSN to a SCRATCH database to run Postgres tests")
    problem = _pg_test_dsn_problem(base_dsn, os.environ)
    if problem is not None:
        pytest.fail(problem, pytrace=False)

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


@pytest.fixture(autouse=True)
def _reset_fallback_state():
    """The fallback counter, the rate-limited warning windows and the
    dependency-verdict cache (#751 PR-3) are process-wide by design — one fact
    for the whole server. Between tests that is leakage: a rerank failure in
    one test would show as a cached ``down`` verdict in the next test's deep
    health. Reset all three on both sides of every test."""
    from ragstack.observability.dependencies import dependency_health
    from ragstack.observability.fallbacks import rate_limited, reset_fallbacks

    dependency_health.reset()
    reset_fallbacks()
    rate_limited.reset()
    yield
    dependency_health.reset()
    reset_fallbacks()
    rate_limited.reset()
