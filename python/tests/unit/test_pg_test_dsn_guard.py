"""``pg_test_dsn`` refuses a DSN naming a deployment database (#369).

The guard is pure — it parses the DSN and opens no connection — so most cases
here call it directly with fake DSNs. Two cases run the real fixture in a
subprocess against ``127.0.0.1:1`` (a dead port, nothing listens there): one
proves a denied DSN *fails* before any connection attempt, the other proves an
allowed one is selected and gets as far as connecting. Neither can reach a
real server.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _PG_DENY_BUILTIN, _pg_test_dsn_problem, _redact_dsn

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
PG_TARGET = "tests/unit/test_collection_store_versions.py::test_postgres_next_version"


@pytest.mark.parametrize(
    "dsn, env",
    [
        ("postgresql://ragstack:pw@db.example:5432/ragstack", {}),
        ("postgres://ragstack:pw@db.example/RAGSTACK", {}),                # case-insensitive
        ("postgresql://u:pw@db.example/postgres", {}),
        ("postgresql://u:pw@db.example/template1", {}),
        ("postgresql://u:pw@db.example/?dbname=ragstack", {}),             # query key
        ("postgresql://u:pw@db.example/", {"PGDATABASE": "ragstack"}),     # libpq env
        ("postgresql://ragstack:pw@db.example", {}),                       # defaults to user
        ("postgresql://db.example", {"PGUSER": "ragstack"}),
        ("postgresql://u:pw@db.example/acme", {"RAGSTACK_TEST_PG_DENY": "dev, Acme"}),
    ],
)
def test_a_deployment_database_is_refused(dsn, env):
    problem = _pg_test_dsn_problem(dsn, env)
    assert problem is not None and "denylist" in problem
    assert ":pw@" not in problem


@pytest.mark.parametrize(
    "dsn, env",
    [
        ("postgresql://ragstack:ragstack@localhost:5432/ragstack_test", {}),
        ("postgresql://u:pw@db.example/scratch", {"RAGSTACK_TEST_PG_DENY": "dev,acme"}),
        ("postgresql://u:pw@db.example/", {"PGDATABASE": "mine"}),
        # whole-name match, not substring: a name merely containing "ragstack"
        ("postgresql://u:pw@db.example/ragstack2", {}),
    ],
)
def test_a_scratch_database_is_allowed(dsn, env):
    assert _pg_test_dsn_problem(dsn, env) is None


def test_a_non_url_dsn_is_refused():
    problem = _pg_test_dsn_problem("host=db.example dbname=ragstack password=s3cret", {})
    assert problem is not None and "postgresql://" in problem
    assert "s3cret" not in problem


def test_the_refusal_never_prints_the_password():
    for dsn in (
        "postgresql://ragstack:s3cret@db.example:5432/ragstack",
        "postgresql://ragstack@db.example/ragstack?password=s3cret&sslmode=require",
    ):
        problem = _pg_test_dsn_problem(dsn, {})
        assert problem is not None
        assert "s3cret" not in problem
        assert "db.example" in problem and "'ragstack'" in problem  # still diagnosable
    assert _redact_dsn("postgresql://u:s3cret@h/d") == "postgresql://u:***@h/d"


def test_the_builtin_list_names_the_shipped_database():
    """``ragstack`` is the compose/apptainer ``POSTGRES_DB`` and the database in
    ``config.py``'s ``postgres_dsn`` default — the one #369 migrated."""
    from ragstack.config import Settings

    default = Settings.model_fields["postgres_dsn"].default
    assert default.rsplit("/", 1)[1] in _PG_DENY_BUILTIN


def test_the_pytest_process_is_pinned_off_the_local_postgres_default():
    """In-process ``settings`` must not resolve ``postgres_dsn`` to the shipped
    localhost default — on the dev host that is the shared instance."""
    from ragstack.config import settings

    assert "@127.0.0.1:1/" in settings.postgres_dsn


def _run_pg_target(dsn: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.pop("RAGSTACK_TEST_PG_DENY", None)
    env["RAGSTACK_TEST_PG_DSN"] = dsn
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(CHECKOUT_ROOT), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs", PG_TARGET],
        cwd=str(CHECKOUT_ROOT), env=env, capture_output=True, text=True, timeout=300,
    )


def test_the_fixture_fails_a_denied_dsn_before_connecting():
    """No asyncpg needed: the guard runs before the fixture imports it."""
    result = _run_pg_target("postgresql://ragstack:s3cret@127.0.0.1:1/ragstack")
    out = result.stdout + result.stderr
    assert result.returncode == 1, out          # a failure, not a skip (exit 0)
    assert "denylist" in out and "'ragstack'" in out, out
    assert "s3cret" not in out
    # refused by the guard, not by the dead port
    assert "Connect call failed" not in out and "ConnectionRefused" not in out, out


def test_the_fixture_selects_an_allowed_dsn():
    """The positive control: the same run with a scratch name gets past the
    guard and tries to connect — to a dead port, so it fails on the connect."""
    pytest.importorskip("asyncpg")
    result = _run_pg_target("postgresql://ragstack:s3cret@127.0.0.1:1/ragstack_test")
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "denylist" not in out, out
    assert "skipped" not in out.lower().split("short test summary")[-1], out
