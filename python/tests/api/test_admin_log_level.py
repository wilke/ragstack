"""GET/PUT/DELETE /v1/admin/log-level — the runtime log-level control (#427).

The endpoint exists because of one requirement: *"make it set-able on demand via
api call so we don't have to reload the service."* So the load-bearing assertion
here is :func:`test_admin_can_set_the_level_and_it_takes_effect_immediately` —
the level changes and the very next log call honours it, in the same process,
with no restart.

The rest is the surface: it must be admin-only (the level of a production
process is not a knob for any authenticated caller), a bad request must be a 4xx
that changed **nothing**, and the response must distinguish what is *configured*
from what is *in effect* — the gap W1's review found in ``GET /v1/config``,
which echoes the raw ``LOG_LEVEL`` even when the server rejected it.

Process-global logging state is restored by the autouse fixture; see the note in
``tests/unit/test_log_control.py``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import jsonschema
import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.observability import log_control
from ragstack.observability.logging_config import configured_dampen_loggers

pytestmark = pytest.mark.asyncio

URL = "/v1/admin/log-level"

#: The published contract, read from ``contracts/`` rather than restated here —
#: the same pattern as ``test_evict_on_create.py``. If the response drifts from
#: what the OpenAPI document promises, these tests fail before conformance does.
SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "log_level_response.json"
    ).read_text()
)


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    watched = set(configured_dampen_loggers()) | {log_control.AUDIT_LOGGER, "ragstack"}
    before = {name: logging.getLogger(name).level for name in watched}
    before_root = root.level
    try:
        yield
    finally:
        log_control._reset_for_tests()
        for name, level in before.items():
            logging.getLogger(name).setLevel(level)
        root.setLevel(before_root)


def _configure(monkeypatch, roles: dict[str, str], tenants: dict[str, str] | None = None) -> None:
    monkeypatch.setattr(security.settings, "api_keys", list(roles))
    monkeypatch.setattr(security.settings, "api_key_roles", dict(roles))
    monkeypatch.setattr(security.settings, "api_key_tenants", dict(tenants or {}))
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


#: Tenant the admin key maps to. Named explicitly rather than left to fall back
#: to DEFAULT_TENANT, because ``changed_by`` and the audit line are asserted
#: against it — and because mapping a key to a tenant is what production does.
ADMIN_TENANT = "acme"


def _admin(monkeypatch) -> dict[str, str]:
    _configure(
        monkeypatch,
        {"adm": ROLE_ADMIN, "usr": ROLE_USER},
        {"adm": ADMIN_TENANT, "usr": "other"},
    )
    return {"X-API-Key": "adm"}


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


async def test_unauthenticated_is_401(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(URL, json={"level": "DEBUG"})).status_code == 401
    assert (await client.delete(URL)).status_code == 401


async def test_non_admin_is_403_on_every_verb(client, monkeypatch):
    """A user must not be able to read the level, let alone change it: raising it
    to CRITICAL is a denial-of-observability on the whole process."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN, "usr": ROLE_USER})
    headers = {"X-API-Key": "usr"}
    assert (await client.get(URL, headers=headers)).status_code == 403
    assert (await client.put(URL, json={"level": "DEBUG"}, headers=headers)).status_code == 403
    assert (await client.delete(URL, headers=headers)).status_code == 403


async def test_a_refused_caller_changes_nothing(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN, "usr": ROLE_USER})
    log_control.set_level(level="INFO")
    await client.put(URL, json={"level": "CRITICAL"}, headers={"X-API-Key": "usr"})
    assert logging.getLogger().level == logging.INFO


# --------------------------------------------------------------------------- #
# The requirement: no restart
# --------------------------------------------------------------------------- #


async def test_admin_can_set_the_level_and_it_takes_effect_immediately(
    client, monkeypatch, caplog
):
    """The reason this endpoint exists. One PUT, and the next log call in this
    same process honours the new level — nothing is reloaded."""
    caplog.set_level(logging.DEBUG, logger="")
    headers = _admin(monkeypatch)
    log = logging.getLogger("ragstack.test.no_restart")

    assert (await client.put(URL, json={"level": "WARNING"}, headers=headers)).status_code == 200
    caplog.clear()
    log.info("before")
    assert not [r for r in caplog.records if r.getMessage() == "before"]

    resp = await client.put(URL, json={"level": "DEBUG"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["effective_level"] == "DEBUG"
    caplog.clear()
    log.debug("after")
    assert [r for r in caplog.records if r.getMessage() == "after"]


async def test_lowercase_and_warn_are_accepted(client, monkeypatch):
    """Matching W1's start-up parsing, which accepts both — the deployed dev and
    demo tenants carry ``LOG_LEVEL=info`` and ``.env.example`` documents ``warn``,
    so refusing them here would contradict the documentation an operator reads."""
    headers = _admin(monkeypatch)
    resp = await client.put(URL, json={"level": "debug"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["effective_level"] == "DEBUG"
    resp = await client.put(URL, json={"level": "warn"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["effective_level"] == "WARNING"


async def test_get_reports_configured_and_effective_separately(client, monkeypatch):
    """The gap W1's review found: ``GET /v1/config`` echoes the RAW ``LOG_LEVEL``,
    so a value the server rejected is reported there while INFO is in force. This
    response keeps the three facts apart — the raw string, what it resolves to,
    and what is live."""
    from ragstack.config import settings

    headers = _admin(monkeypatch)
    monkeypatch.setattr(settings, "log_level", "verbose")  # a value the server rejects
    await client.delete(URL, headers=headers)

    body = (await client.get(URL, headers=headers)).json()
    assert body["configured_level"] == "verbose"
    assert body["configured_level_resolved"] == "INFO"  # W1's documented fallback
    assert body["effective_level"] == "INFO"
    assert body["runtime_override"] is False

    await client.put(URL, json={"level": "DEBUG"}, headers=headers)
    body = (await client.get(URL, headers=headers)).json()
    assert body["effective_level"] == "DEBUG"
    assert body["runtime_override"] is True
    # The configured half is what a restart returns to, and is untouched by a PUT.
    assert body["configured_level"] == "verbose"
    assert body["configured_level_resolved"] == "INFO"
    assert body["changed_by"] == ADMIN_TENANT
    assert body["changed_at"].endswith("Z")
    assert body["pid"] > 0


@pytest.mark.parametrize("verb", ["get", "put", "delete"])
async def test_every_verb_answers_the_published_schema(client, monkeypatch, verb):
    """The contract is the source of truth (CLAUDE.md, ADR-0006), and all three
    verbs return the same shape — so all three are validated against the same
    published schema, not just the one conformance happens to exercise."""
    headers = _admin(monkeypatch)
    if verb == "put":
        resp = await client.put(URL, json={"level": "INFO"}, headers=headers)
    else:
        resp = await getattr(client, verb)(URL, headers=headers)
    assert resp.status_code == 200, resp.text
    jsonschema.validate(instance=resp.json(), schema=SCHEMA)


# --------------------------------------------------------------------------- #
# Damping, through the HTTP surface
# --------------------------------------------------------------------------- #


async def test_debug_releases_the_dampened_loggers_and_info_re_damps(client, monkeypatch):
    """Turning the level down has to actually turn the HTTP transports on — a
    single /v1/query makes 5 outbound calls minimum, and seeing them is usually
    why someone reached for DEBUG."""
    headers = _admin(monkeypatch)
    dampened = list(configured_dampen_loggers())

    body = (await client.put(URL, json={"level": "DEBUG"}, headers=headers)).json()
    assert body["dampening_active"] is False
    assert all(logging.getLogger(n).level == logging.NOTSET for n in dampened)

    body = (await client.put(URL, json={"level": "INFO"}, headers=headers)).json()
    assert body["dampening_active"] is True
    assert all(logging.getLogger(n).level == logging.WARNING for n in dampened)
    assert {e["source"] for e in body["loggers"]} == {"dampen"}


async def test_a_per_logger_override_is_reported_as_such(client, monkeypatch):
    headers = _admin(monkeypatch)
    body = (
        await client.put(URL, json={"level": "INFO", "loggers": {"httpx": "DEBUG"}}, headers=headers)
    ).json()
    assert body["logger_override_count"] == 1
    entry = next(e for e in body["loggers"] if e["name"] == "httpx")
    assert entry["source"] == "override" and entry["level"] == "DEBUG"


# --------------------------------------------------------------------------- #
# Refusals are 4xx and change nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"level": "verbose"},
        {"level": "NOTSET"},
        {},
        {"loggers": {"no.such.logger.427": "DEBUG"}},
        {"loggers": {"bad name": "DEBUG"}},
        {"level": "DEBUG", "loggers": {"httpx": "nonsense"}},
        {"loggers": {"ragstack.audit": "CRITICAL"}},
        {"level": "DEBUG", "unknown_field": 1},
    ],
    ids=[
        "unknown-level",
        "notset",
        "empty-body",
        "missing-logger",
        "bad-logger-name",
        "half-valid",
        "audit-logger",
        "extra-field",
    ],
)
async def test_invalid_requests_are_422_and_leave_the_level_unchanged(
    client, monkeypatch, payload
):
    """Both halves matter. A 4xx alone would still be a bug if the request had
    partially applied — an operator who mistyped one logger name must not
    discover later that the root level moved anyway."""
    headers = _admin(monkeypatch)
    await client.put(URL, json={"level": "WARNING"}, headers=headers)
    before = logging.getLogger().level
    before_httpx = logging.getLogger("httpx").level

    resp = await client.put(URL, json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert logging.getLogger().level == before
    assert logging.getLogger("httpx").level == before_httpx


async def test_a_refused_logger_name_is_not_created(client, monkeypatch):
    """``logging.getLogger`` creates a logger permanently, so a refusal that
    created one anyway would make the validation decorative."""
    headers = _admin(monkeypatch)
    name = "not.a.logger.in.this.process.427"
    await client.put(URL, json={"loggers": {name: "DEBUG"}}, headers=headers)
    assert name not in logging.Logger.manager.loggerDict


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #


async def test_delete_restores_the_configured_defaults(client, monkeypatch):
    """The caller gets back to the configured state without knowing what it was."""
    headers = _admin(monkeypatch)
    await client.put(URL, json={"level": "CRITICAL", "loggers": {"httpx": "DEBUG"}}, headers=headers)

    body = (await client.delete(URL, headers=headers)).json()
    assert body["runtime_override"] is False
    assert body["logger_override_count"] == 0
    assert body["effective_level"] == body["configured_level_resolved"]
    assert logging.getLogger().level == logging.getLevelName(body["effective_level"])
    # The override is gone, not merely unlisted.
    assert logging.getLogger("httpx").level == logging.WARNING


async def test_delete_is_idempotent(client, monkeypatch):
    headers = _admin(monkeypatch)
    first = (await client.delete(URL, headers=headers)).json()
    second = (await client.delete(URL, headers=headers)).json()
    assert first["effective_level"] == second["effective_level"]
    assert second["runtime_override"] is False


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


async def test_the_change_is_audited_at_warning_with_the_principal(client, monkeypatch, caplog):
    """Someone who finds DEBUG on in production needs to know who turned it on.
    At WARNING so it survives a raised threshold — including the one this very
    call sets."""
    caplog.set_level(logging.DEBUG, logger="")
    headers = _admin(monkeypatch)
    caplog.clear()

    await client.put(URL, json={"level": "CRITICAL"}, headers=headers)
    audit = [r for r in caplog.records if r.name == log_control.AUDIT_LOGGER]
    assert audit, "the change to CRITICAL suppressed its own audit line"
    assert audit[-1].levelno == logging.WARNING
    assert audit[-1].principal == ADMIN_TENANT
    assert audit[-1].level_after == "CRITICAL"


async def test_the_audit_line_carries_no_credential(client, monkeypatch, caplog):
    """``Principal.__repr__`` redacts ``token``, but that guard covers ``repr()``
    alone — it does nothing for someone interpolating an attribute. The audit
    line is built from ``tenant`` and ``tenant`` only, so the presented
    credential must appear nowhere in the captured output."""
    caplog.set_level(logging.DEBUG, logger="")
    secret = "sup3rs3cr3t-key-427"
    _configure(monkeypatch, {secret: ROLE_ADMIN}, {secret: ADMIN_TENANT})
    caplog.clear()

    resp = await client.put(URL, json={"level": "DEBUG"}, headers={"X-API-Key": secret})
    assert resp.status_code == 200
    assert caplog.records, "nothing was captured, so this would pass vacuously"
    for record in caplog.records:
        assert secret not in str(record.__dict__), record.name
        assert secret not in record.getMessage()
    assert secret not in resp.text
