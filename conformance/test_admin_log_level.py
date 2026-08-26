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

One thing it deliberately does **not** put back: a pending ``ttl_seconds``
auto-revert that happened to be armed when the run started. There is no way to
re-arm one at its original deadline (a TTL is a duration from now, not a
timestamp), and re-arming an approximation would be worse than leaving it off —
the operator would be told a level reverts at a time it does not. The rule for
tests here is the other half of that: **never leave a TTL armed**. Every test
below that arms one either lets it expire or cancels it before it returns, so
the server this ran against is not left with a timer nobody knows about.

Python-only: the Go scaffold has no route here (404, not 501). When Go grows one,
delete the skip — the deletion belongs to that diff so it cannot be forgotten.
"""

from __future__ import annotations

import asyncio
import os
import time
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


# --------------------------------------------------------------------------- #
# TTL / auto-revert (#427 follow-on)
# --------------------------------------------------------------------------- #


async def test_a_ttl_is_reported_and_then_reverts_the_server(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    """The feature against a real server: the level goes back on its own.

    A one-second TTL and a bounded poll, not a fixed sleep — the assertion is
    that the revert happens and that a subsequent GET sees it, not how promptly.
    The test cannot finish while a timer is still armed, which is also what keeps
    it from leaving one behind.
    """
    resp = await client.put(
        URL, json={"level": "DEBUG", "ttl_seconds": 1}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jsonschema.validate(instance=body, schema=schemas["log_level_response"])
    assert body["effective_level"] == "DEBUG"
    assert body["auto_revert_pending"] is True
    assert body["ttl_seconds"] == 1
    assert body["expires_in_seconds"] == 1
    assert body["expires_at"] != ""
    assert body["max_ttl_seconds"] >= 1

    deadline = time.monotonic() + 15.0
    state = body
    while state["auto_revert_pending"] and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        state = (await client.get(URL, headers=admin_headers)).json()

    jsonschema.validate(instance=state, schema=schemas["log_level_response"])
    assert state["auto_revert_pending"] is False, "the TTL never fired"
    assert state["runtime_override"] is False
    assert state["effective_level"] == state["configured_level_resolved"]
    assert state["expires_in_seconds"] is None
    assert state["changed_by"] == "", "an auto-revert is attributable to no principal"


async def test_a_second_put_supersedes_the_first_ttl(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    """Two overlapping TTLs must never fight. The first is given a deadline well
    inside this test's runtime; if it were still armed it would fire and take
    ERROR away, and the final GET would catch it."""
    await client.put(URL, json={"level": "DEBUG", "ttl_seconds": 1}, headers=admin_headers)
    second = await client.put(
        URL, json={"level": "ERROR", "ttl_seconds": 3600}, headers=admin_headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["expires_in_seconds"] == 3600

    # Well past the FIRST deadline. The second change must be untouched.
    await asyncio.sleep(2.0)
    state = (await client.get(URL, headers=admin_headers)).json()
    assert state["effective_level"] == "ERROR", "the superseded timer fired anyway"
    assert state["ttl_seconds"] == 3600
    assert state["auto_revert_pending"] is True

    # Never leave a TTL armed — this is also the DELETE-cancels assertion.
    after = (await client.delete(URL, headers=admin_headers)).json()
    assert after["auto_revert_pending"] is False


async def test_no_ttl_arms_no_expiry(
    client: httpx.AsyncClient,
    schemas: dict[str, dict],
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    """The behaviour this endpoint shipped with, unchanged."""
    body = (await client.put(URL, json={"level": "DEBUG"}, headers=admin_headers)).json()
    jsonschema.validate(instance=body, schema=schemas["log_level_response"])
    assert body["auto_revert_pending"] is False
    assert body["ttl_seconds"] is None
    assert body["expires_at"] == ""
    assert body["expires_in_seconds"] is None


async def test_an_out_of_range_ttl_is_4xx_and_changes_nothing(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    restored: dict[str, Any],
) -> None:
    before = (await client.get(URL, headers=admin_headers)).json()
    for ttl in (0, 86_401):
        resp = await client.put(
            URL, json={"level": "DEBUG", "ttl_seconds": ttl}, headers=admin_headers
        )
        assert 400 <= resp.status_code < 500, (ttl, resp.status_code)

    after = (await client.get(URL, headers=admin_headers)).json()
    assert after["effective_level"] == before["effective_level"]
    assert after["auto_revert_pending"] is False
