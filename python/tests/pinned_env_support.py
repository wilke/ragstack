"""Dead-port environment for tests that hand work to a child process (#432).

**Why a shared module.** ``ragstack.config`` gives every outbound dependency a
``localhost`` default — ``crossencoder_sidecar_url`` is ``http://localhost:50052``,
``embedding_sidecar_url`` is ``http://localhost:50053``, and so on. Inside the
test process that is harmless: the fixtures inject in-memory doubles. In a
*child* process it is not — the child builds real settings from its inherited
environment, and on this host every one of those ports has a live service
listening on it. A scratch run of the lifespan probe in
``tests/api/test_latency_rollup.py`` with only the stores pinned reached the
**live cross-encoder sidecar on :50052**; that incident is why the dict below
covers the model backends and not just Qdrant/ES.

So: any test that spawns a child which can import ``ragstack.config`` — or
otherwise fall back to a config default — builds the child's environment from
:func:`pinned_env`, and the child then hits ``127.0.0.1:1``, where a leak fails
loudly instead of silently reaching production.

Tests whose child touches no ``ragstack`` code at all (a bash provisioning
script, a node harness, a standalone script imported by path) do **not** need
this and deliberately don't use it.
"""
from __future__ import annotations

import os

#: Nothing listens here, and nothing ever will. A connection attempt to port 1
#: fails immediately with ECONNREFUSED rather than hanging.
DEAD_URL = "http://127.0.0.1:1"

#: Every outbound endpoint ``ragstack.config`` defaults to a live local port.
#: Keep this in sync with ``ragstack/config.py`` when a new backend is added —
#: an unpinned key here is a hole straight to whatever runs on that port.
#:
#: ``FAISS_SIDECAR_URL`` has no corresponding field in ``config.py`` today. It
#: is kept because the sidecar is documented in ``.env.example`` and pinning a
#: name nothing reads costs nothing, while removing it invites re-adding a live
#: default later. ``conformance/boot_env.sh`` pins the identical set, and
#: ``test_the_conformance_runners_pin_the_same_set_as_the_python_tests``
#: enforces that rather than trusting this comment.
#:
#: Scope note for ``test_pinned_env_leaves_no_live_local_default``: it flags
#: settings that resolve to a **local** address, which is the shape that reaches
#: a service on this host. A default pointing at a live *remote* service —
#: ``workspace_url`` has one — is outside what that test can see, so dropping
#: ``WORKSPACE_URL`` from this dict would not fail it.
PINNED_ENV: dict[str, str] = {
    "QDRANT_URL": DEAD_URL,
    "ELASTICSEARCH_URL": DEAD_URL,
    "EMBEDDING_SIDECAR_URL": DEAD_URL,
    "CROSSENCODER_SIDECAR_URL": DEAD_URL,
    "FAISS_SIDECAR_URL": DEAD_URL,
    "GOWE_URL": DEAD_URL,
    "WORKSPACE_URL": DEAD_URL,
    "NEO4J_URI": "bolt://127.0.0.1:1",
    "REDIS_URL": "redis://127.0.0.1:1",
    # Not in the original per-incident dict this was promoted from, and found by
    # ``test_pinned_env_leaves_no_live_local_default`` below: `postgres_dsn`
    # defaults to `postgresql+asyncpg://ragstack:ragstack@localhost/ragstack`,
    # which is verbatim the DSN that applied a migration to a production `jobs`
    # table in #369. `pg_test_dsn` gates the *test* path; this gates the child
    # processes, which build settings from the environment and never see it.
    "POSTGRES_DSN": "postgresql+asyncpg://ragstack:ragstack@127.0.0.1:1/ragstack",
}


def pinned_env(base: dict[str, str] | None = None, **overrides: str) -> dict[str, str]:
    """Build a child-process environment with every store/model URL pinned dead.

    ``base`` defaults to a copy of ``os.environ`` (so ``PATH``, ``HOME`` and the
    interpreter's own variables survive); pass an explicit dict to start from a
    minimal environment instead. ``overrides`` are applied **after** the pins, so
    a test that genuinely needs one endpoint pointed somewhere else can say so
    explicitly — which is the point: reaching a live service should require a
    line of code naming it.
    """
    env = dict(os.environ if base is None else base)
    env.update(PINNED_ENV)
    env.update(overrides)
    return env
