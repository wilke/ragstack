"""Conformance: GET/PUT/DELETE /v1/admin/log-level (#427), black-box over HTTP.

The endpoint changes the log level of a **running** server, so unlike every other
file here these tests mutate the thing they are pointed at. That is the point of
the feature and it is also the hazard: someone will eventually run this against a
real tenant, and the level it was left at would be the level it kept until the
next restart.

So the rule this file follows, and any test added here must follow:

    **Snapshot first, restore exactly, in a ``finally``.**

Restore means putting back what ``GET`` reported — not calling ``DELETE``.
``DELETE`` resets to the *configured* defaults, which would silently discard an
override an operator had deliberately set before the run. The
:func:`restored` fixture does this once so no individual test has to remember.

Python-only: the Go scaffold has no route here (404, not 501). When Go grows one,
delete the skip — the deletion belongs to that diff so it cannot be forgotten.
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

import httpx
import jsonschema
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

URL = "/v1/admin/log-level"


@pytest.fixture(autouse=True)
def _python_only(impl: str) -> None:
    if impl != "python":
        pytest.skip(f"{URL} is python-only; the Go scaffold serves no such route")


@pytest.fixture
def admin_headers() -> dict[str, str]:
    key = os.environ.get("RAGSTACK_API_KEY_ADMIN") or None
    if not key:
        pytest.skip("needs an admin key (RAGSTACK_API_KEY_ADMIN)")
    return {"X-API-Key": key}


@pytest_asyncio.fixture
async def restored(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield the server's state at entry, and put it back afterwards.

    Deliberately not ``DELETE``: that resets to the configured defaults and would
    throw away a runtime override the operator set on purpose. Restoring means
    re-asserting exactly what was observed — the root level, and the override set
    read back out of ``loggers``.
    """
    resp = await client.get(URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    before = resp.json()
    try:
        yield before
    finally:
        overrides = {
            entry["name"]: entry["level"]
            for entry in before["loggers"]
            if entry["source"] == "override"
        }
        if before["runtime_override"]:
            await client.put(
                URL,
                json={"level": before["effective_level"], "loggers": overrides},
                headers=admin_headers,
            )
        else:
            # No runtime root override was in force, so the configured level is
            # the honest destination — but the overrides still have to go back.
            await client.delete(URL, headers=admin_headers)
            if overrides:
                await client.put(URL, json={"loggers": overrides}, headers=admin_headers)


async def test_get_matches_the_schema(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    jsonschema.validate(instance=restored, schema=schemas["log_level_response"])
    assert restored["pid"] > 0
    assert restored["max_logger_overrides"] > 0


async def test_put_changes_the_level_and_reports_it(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    """The requirement in one assertion: the level changes without a restart, and
    a subsequent read confirms the server is still at the new level."""
    resp = await client.put(URL, json={"level": "DEBUG"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jsonschema.validate(instance=body, schema=schemas["log_level_response"])
    assert body["effective_level"] == "DEBUG"
    assert body["runtime_override"] is True
    # DEBUG releases the dampen set — the reason to turn it down in the first place.
    assert body["dampening_active"] is False

    again = (await client.get(URL, headers=admin_headers)).json()
    assert again["effective_level"] == "DEBUG"
    assert again["pid"] == body["pid"], "a different worker answered; state is process-local"


async def test_invalid_level_is_4xx_and_changes_nothing(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    before = (await client.get(URL, headers=admin_headers)).json()["effective_level"]
    resp = await client.put(URL, json={"level": "verbose"}, headers=admin_headers)
    assert 400 <= resp.status_code < 500, resp.status_code
    after = (await client.get(URL, headers=admin_headers)).json()["effective_level"]
    assert after == before


async def test_delete_resets_to_the_configured_level(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    await client.put(URL, json={"level": "ERROR"}, headers=admin_headers)
    body = (await client.delete(URL, headers=admin_headers)).json()
    assert body["runtime_override"] is False
    assert body["effective_level"] == body["configured_level_resolved"]
    assert body["logger_override_count"] == 0


async def test_non_admin_is_forbidden(client: httpx.AsyncClient) -> None:
    """The gate is the whole authorization story for this endpoint: raising the
    level to CRITICAL would blind the deployment, and reading it is already more
    than a user needs to know."""
    key = os.environ.get("RAGSTACK_API_KEY_NONADMIN") or None
    if not key:
        pytest.skip("needs a non-admin key (RAGSTACK_API_KEY_NONADMIN)")
    headers = {"X-API-Key": key}
    assert (await client.get(URL, headers=headers)).status_code == 403
    assert (await client.put(URL, json={"level": "DEBUG"}, headers=headers)).status_code == 403
    assert (await client.delete(URL, headers=headers)).status_code == 403


async def test_unauthenticated_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get(URL)
    assert resp.status_code in (401, 403), resp.status_code
