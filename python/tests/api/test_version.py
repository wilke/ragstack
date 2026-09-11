"""GET /v1/version — which code this process runs (ADR-0007, PR-A).

The endpoint exists so the control plane can show *configured vs running*
without reading a tenant's worktree. Its two properties that matter:

* **the supervisor's word wins** — ``RAGSTACK_GIT_TAG`` / ``RAGSTACK_GIT_SHA``
  (rendered into the unit from the registry) beat whatever ``git`` says about
  the checkout, because the artifact a tenant was launched from is the fact an
  operator is comparing against;
* **it is "any credential"** — the same gate as the data routes, never the
  admin group, and never open like ``/health``.

The response is checked against the published contract read from
``contracts/`` rather than restated here — the same pattern as
``test_admin_log_level.py`` — so drift fails here before conformance does.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import jsonschema
import pytest
import yaml

from ragstack import version as version_mod
from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_USER

URL = "/v1/version"

_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
SCHEMA = json.loads((_CONTRACTS / "schemas" / "version_response.json").read_text())


@pytest.fixture(autouse=True)
def _fresh_git_cache(monkeypatch):
    """The git lookups are cached for the life of the process; each test starts
    with an empty cache and no env override so the fallback order is observable."""
    monkeypatch.delenv("RAGSTACK_GIT_TAG", raising=False)
    monkeypatch.delenv("RAGSTACK_GIT_SHA", raising=False)
    version_mod.cache_clear()
    yield
    version_mod.cache_clear()


def _configure(monkeypatch, roles: dict[str, str]) -> None:
    monkeypatch.setattr(security.settings, "api_keys", list(roles))
    monkeypatch.setattr(security.settings, "api_key_roles", dict(roles))
    monkeypatch.setattr(security.settings, "api_key_tenants", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _validate(body: dict) -> None:
    jsonschema.validate(instance=body, schema=SCHEMA)


# --------------------------------------------------------------------------- #
# Authorization: any credential, not none, not admin
# --------------------------------------------------------------------------- #


async def test_unauthenticated_is_401_when_keys_configured(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    assert (await client.get(URL)).status_code == 401


async def test_any_valid_key_is_200(client, monkeypatch):
    """A plain user key reaches it — the route must not be admin-gated."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN, "usr": ROLE_USER})
    for key in ("usr", "adm"):
        resp = await client.get(URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, (key, resp.text)
        _validate(resp.json())


# --------------------------------------------------------------------------- #
# The body
# --------------------------------------------------------------------------- #


async def test_body_matches_contract_and_names_this_impl(client):
    resp = await client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body)
    assert body["impl"] == "python"
    assert body["version"] == version_mod.package_version()


async def test_started_at_is_rfc3339_z_and_stable(client):
    body = (await client.get(URL)).json()
    # RFC 3339 with a trailing Z, the spelling the ctl renders every timestamp
    # in: `+00:00` is the same instant but a second spelling of one field.
    assert body["started_at"].endswith("Z"), body["started_at"]
    started = datetime.fromisoformat(body["started_at"])
    assert started.tzinfo is not None and started.utcoffset().total_seconds() == 0
    # Captured once at import: two calls report the same start, not "now".
    assert (await client.get(URL)).json()["started_at"] == body["started_at"]
    assert body["started_at"] == version_mod.STARTED_AT


async def test_env_override_wins_over_git(client, monkeypatch):
    """The supervisor's RAGSTACK_GIT_TAG/SHA describe the launched artifact and
    beat the checkout's own git identity."""
    monkeypatch.setenv("RAGSTACK_GIT_TAG", "v9.9.9-unit")
    monkeypatch.setenv("RAGSTACK_GIT_SHA", "deadbeef")
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(version_mod.subprocess, "run", spy)
    body = (await client.get(URL)).json()
    _validate(body)
    assert body["git_tag"] == "v9.9.9-unit"
    assert body["git_sha"] == "deadbeef"
    assert calls == [], "env override set — git must not be consulted at all"


async def test_empty_env_override_falls_back_to_git(client, monkeypatch):
    """An empty ``RAGSTACK_GIT_TAG=`` (a unit with the key present but unset) is
    "not set", not "the tag is the empty string"."""
    monkeypatch.setenv("RAGSTACK_GIT_TAG", "")
    body = (await client.get(URL)).json()
    _validate(body)
    assert body["git_tag"] != ""


def _fake_git(stdout: str = "v1.2.3-4-gabc", *, toplevel: str | None = None, seen=None):
    """A ``subprocess.run`` stand-in that answers ``rev-parse --show-toplevel``
    with *toplevel* (the real checkout by default, so the identity check passes)
    and everything else with *stdout*."""
    top = version_mod._CHECKOUT if toplevel is None else toplevel

    def fake_run(argv, *args, **kwargs):
        if seen is not None:
            seen.append((list(argv), dict(kwargs)))
        out = str(top) if "--show-toplevel" in argv else stdout
        return subprocess.CompletedProcess(argv, 0, stdout=out + "\n", stderr="")

    return fake_run


def test_git_fallback_is_argv_only_never_shell(monkeypatch):
    seen: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(version_mod.subprocess, "run", _fake_git(seen=seen))
    assert version_mod.git_tag() == "v1.2.3-4-gabc"
    assert version_mod.git_sha() == "v1.2.3-4-gabc"
    assert seen, "git was never consulted"
    for argv, kwargs in seen:
        # The binary is the one resolved at import, not whatever `git` PATH
        # happens to mean when the request arrives.
        assert argv[0] == version_mod._GIT and all(isinstance(a, str) for a in argv)
        assert not kwargs.get("shell"), "never a shell"
        assert kwargs.get("timeout") == version_mod.GIT_TIMEOUT_S
        assert kwargs.get("cwd") == version_mod._CHECKOUT


def test_git_lookups_are_cached_after_first_call(monkeypatch):
    """A subprocess per request would be an amplification lever on an endpoint
    any credential can reach: one ``git`` call per command, ever."""
    count = 0
    inner = _fake_git("abc1234")

    def fake_run(argv, *args, **kwargs):
        nonlocal count
        count += 1
        return inner(argv, *args, **kwargs)

    monkeypatch.setattr(version_mod.subprocess, "run", fake_run)
    for _ in range(3):
        version_mod.version_info()
    assert count == 3  # show-toplevel + describe + rev-parse, once each


# --------------------------------------------------------------------------- #
# Which git, and which repository (S33)
# --------------------------------------------------------------------------- #


def test_a_foreign_toplevel_is_not_trusted(monkeypatch):
    """``_CHECKOUT`` can resolve into a conda env — ``site-packages/ragstack``
    has a ``parents[2]`` that is no repository — and git then walks UPWARD and
    answers for whatever repo encloses the env. The checkout must be proven."""
    monkeypatch.setattr(
        version_mod.subprocess, "run", _fake_git("v9.9.9", toplevel="/opt/somebody-elses-repo")
    )
    info = version_mod.version_info()
    assert info["git_tag"] is None and info["git_sha"] is None
    _validate(info)


def test_no_git_binary_spawns_nothing(monkeypatch):
    """``shutil.which`` found no git: nulls, and not one subprocess attempt."""
    monkeypatch.setattr(version_mod, "_GIT", None)
    calls: list = []
    monkeypatch.setattr(
        version_mod.subprocess, "run", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError)
    )
    info = version_mod.version_info()
    assert info["git_tag"] is None and info["git_sha"] is None
    assert calls == []
    _validate(info)


# --------------------------------------------------------------------------- #
# The cold path: one subprocess, and failures are not permanent (S29/S30)
# --------------------------------------------------------------------------- #


def test_concurrent_cold_calls_spawn_one_git_per_command(monkeypatch):
    """N requests arriving together on a cold cache — the state of every
    process for the first moments after a restart — must fork ONE git per
    command, not N. The threadpool makes that concurrency real."""
    threads = 8
    started = threading.Barrier(threads)
    count = 0
    count_lock = threading.Lock()
    inner = _fake_git("abc1234")

    def fake_run(argv, *args, **kwargs):
        nonlocal count
        with count_lock:
            count += 1
        time.sleep(0.02)  # hold the cold path open so a racer could join in
        return inner(argv, *args, **kwargs)

    monkeypatch.setattr(version_mod.subprocess, "run", fake_run)

    results: list[dict] = []
    def worker():
        started.wait(timeout=10)
        results.append(version_mod.version_info())

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=20)

    assert len(results) == threads and all(r["git_tag"] == "abc1234" for r in results)
    assert count == 3, f"{threads} concurrent cold callers spawned {count} git processes"


def _advance(monkeypatch, seconds: float) -> None:
    """Move the module's clock forward past a back-off window (the real
    `time.monotonic` is captured first: patching it with a lambda that calls
    the patched name would recurse)."""
    now = version_mod.time.monotonic()
    monkeypatch.setattr(version_mod.time, "monotonic", lambda: now + seconds)


def test_a_failure_is_retried_not_memoised(monkeypatch):
    """A transient git timeout must not turn into a permanent null for the life
    of the process — but a dead git must not be re-spawned per request either."""
    calls: list[list[str]] = []
    ok = _fake_git("v2.0.0")
    failing = [True]

    def flaky(argv, *args, **kwargs):
        calls.append(list(argv))
        if failing[0]:
            raise subprocess.TimeoutExpired(argv, version_mod.GIT_TIMEOUT_S)
        return ok(argv, *args, **kwargs)

    monkeypatch.setattr(version_mod.subprocess, "run", flaky)

    assert version_mod.version_info()["git_tag"] is None
    # Inside the back-off window: no new attempt, still null.
    before = len(calls)
    assert version_mod.version_info()["git_tag"] is None
    assert len(calls) == before, "a dead git was re-spawned inside the back-off"

    # After it, the same process recovers — the None was never cached.
    failing[0] = False
    _advance(monkeypatch, version_mod.RETRY_INTERVAL_S + 1)
    info = version_mod.version_info()
    assert info["git_tag"] == "v2.0.0" and info["git_sha"] == "v2.0.0"
    _validate(info)


def test_success_is_cached_across_the_backoff_window(monkeypatch):
    monkeypatch.setattr(version_mod.subprocess, "run", _fake_git("v3.0.0"))
    assert version_mod.git_tag() == "v3.0.0"
    monkeypatch.setattr(
        version_mod.subprocess,
        "run",
        _fake_git("SHOULD-NOT-BE-ASKED-AGAIN"),
    )
    _advance(monkeypatch, 10_000)
    assert version_mod.git_tag() == "v3.0.0"


def test_handler_is_sync_so_git_never_runs_on_the_event_loop():
    """An ``async def`` handler would run the (bounded, but multi-second)
    subprocess on the event loop, stalling every other request on the process."""
    from ragstack.api.routers import version as version_router

    assert not inspect.iscoroutinefunction(version_router.version)
    assert inspect.isfunction(version_router.version)


async def test_lifespan_warms_the_cache(monkeypatch):
    """The control plane polls /v1/version right after a restart — precisely
    when the cache is cold. The lifespan pays that cost once, in a thread."""
    from fastapi import FastAPI

    from ragstack.api import deps
    from ragstack.config import settings

    # Nothing in this test may reach a real store: the autouse _isolate_qdrant
    # fixture already pins Qdrant at a dead port, and the rest is in-memory.
    monkeypatch.setattr(settings, "vector_backend", "memory")
    monkeypatch.setattr(settings, "text_backend", "memory")
    monkeypatch.setattr(settings, "graph_backend", "memory")
    monkeypatch.setattr(settings, "rerank_enabled", False)
    monkeypatch.setattr(settings, "require_durable_backends", False)

    version_mod.cache_clear()
    monkeypatch.setattr(version_mod.subprocess, "run", _fake_git("v4.0.0"))
    warmed: list[str] = []
    loop_thread = threading.get_ident()

    real = version_mod.version_info

    def spy():
        assert threading.get_ident() != loop_thread, "warm-up ran ON the event loop"
        out = real()
        warmed.append(out["git_tag"])
        return out

    monkeypatch.setattr(version_mod, "version_info", spy)
    # A no-op lifespan body would hide the point, so drive the real one and let
    # it fail on missing infra only AFTER the warm-up step.
    app = FastAPI()
    try:
        async with deps.lifespan(app):
            pass
    except Exception:
        pass
    assert warmed == ["v4.0.0"], "the lifespan did not warm the version cache"


def _raises(exc_factory):
    def run(argv, **kwargs):
        raise exc_factory(argv)

    return run


def _returns(returncode: int, stdout: str):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return run


@pytest.mark.parametrize(
    "failure",
    [
        _raises(lambda argv: FileNotFoundError("no git")),
        _raises(lambda argv: subprocess.TimeoutExpired(argv, 2.0)),
        _returns(128, ""),
        _returns(0, "\n"),
    ],
    ids=["no-git-binary", "timeout", "nonzero-exit", "empty-output"],
)
def test_git_failure_is_null_never_an_error(monkeypatch, failure):
    monkeypatch.setattr(version_mod.subprocess, "run", failure)
    info = version_mod.version_info()
    assert info["git_tag"] is None and info["git_sha"] is None
    _validate(info)


# --------------------------------------------------------------------------- #
# Contract parity: the OpenAPI component and the JSON schema say the same thing
# --------------------------------------------------------------------------- #


def test_openapi_component_matches_version_response_json():
    openapi = yaml.safe_load((_CONTRACTS / "openapi.yaml").read_text())
    component = openapi["components"]["schemas"]["VersionResponse"]
    assert set(component["required"]) == set(SCHEMA["required"])
    assert set(component["properties"]) == set(SCHEMA["properties"])
    assert component["additionalProperties"] is False
    for name, prop in SCHEMA["properties"].items():
        assert component["properties"][name]["type"] == prop["type"], name
    assert component["properties"]["impl"]["enum"] == SCHEMA["properties"]["impl"]["enum"]
    op = openapi["paths"]["/v1/version"]["get"]
    assert op["operationId"] == "versionInfo"
    assert {"ApiKeyAuth": []} in op["security"] and {"BearerIdentity": []} in op["security"]
    assert "401" in op["responses"] and "403" not in op["responses"]
