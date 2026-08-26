"""A bearer credential must never reach a log line.

``Principal.__repr__`` redacts ``token`` — but that guard covers ``repr()`` and
nothing else. It does nothing for ``log.info("%s", principal.token)``, and
``api_key`` is a live parameter on ``resolve_principal`` / ``resolve_tenant`` /
``_authenticate``, so a middleware that logged its own arguments would put a
usable credential in the log file. #427 W1 adds a request context that is
populated *from* the principal and a filter that copies it onto every record,
which is exactly the shape of change that leaks one by accident.

The defence is structural rather than a scrub on the way out: the request
context carries **tenant and role only**, and has no field a credential could be
assigned to. These tests pin that, because "we just won't add it" is not a
property a future change can be checked against.
"""
import dataclasses
import logging
import time

import pytest

from ragstack.api import security
from ragstack.api.security import Principal
from ragstack.observability.context import RequestContext, set_context
from ragstack.observability.logging_config import configure_logging

SECRET = "tok_LIVE_5f3a9c2b1e7d4a6f8c0b2d4e6f8a0c2e"


@pytest.fixture(autouse=True)
def _restore_root_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_request_context_has_no_field_that_can_hold_a_credential():
    """The structural guard. If someone adds ``token`` or ``api_key`` to the
    context — the natural next step when a log line needs "more about the
    caller" — this is what says no."""
    names = {f.name for f in dataclasses.fields(RequestContext)}
    forbidden = {"token", "api_key", "credential", "authorization", "secret", "password"}
    assert not (names & forbidden), f"the request context must carry no credential: {names}"


def test_principal_repr_still_redacts_the_token():
    """The pre-existing guard, pinned so this work item cannot regress it."""
    p = Principal(tenant="bvbrc:alice", role="user", token=SECRET, token_id="t1")
    assert SECRET not in repr(p)
    assert "'***'" in repr(p)


def test_the_context_populated_from_a_principal_carries_no_token(capsys):
    """Mirrors what ``resolve_principal`` does — tenant and role, nothing else —
    and then logs through the real handler to prove the token is not there."""
    configure_logging(level="DEBUG", log_format="logfmt")

    principal = Principal(tenant="bvbrc:alice", role="user", token=SECRET, token_id="t1")
    ctx = RequestContext(request_id="0123456789abcdef")
    ctx.tenant = principal.tenant
    ctx.role = principal.role
    set_context(ctx)

    logging.getLogger("ragstack.test").warning("something failed for %s", principal.tenant)

    err = capsys.readouterr().err
    assert SECRET not in err, "a bearer token reached a log line"
    assert "tenant=bvbrc:alice" in err
    assert "role=user" in err


@pytest.mark.asyncio
async def test_end_to_end_bearer_request_never_logs_the_token(capsys, monkeypatch):
    """The real path: a bearer credential authenticated through
    ``resolve_principal``, whose whole job in W1 is to copy the caller onto the
    request context. Everything the request emits at DEBUG is captured and the
    credential must not be anywhere in it."""
    from ragstack.identity import Identity, reset_identity_provider, set_identity_provider

    seen: list[str] = []

    class _Provider:
        async def authenticate(self, credential: str) -> Identity:
            seen.append(credential)
            return Identity(
                subject="alice@patricbrc.org",
                issuer="bvbrc",
                token_id="tok-1",
                expires_at=int(time.time()) + 3600,
            )

    monkeypatch.setattr(security.settings, "identity_provider", "bvbrc", raising=False)
    set_identity_provider(_Provider())
    configure_logging(level="DEBUG", log_format="logfmt")
    try:
        from httpx import ASGITransport, AsyncClient

        from ragstack.api.main import app

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/v1/config", headers={"Authorization": f"Bearer {SECRET}"})
    finally:
        reset_identity_provider()

    # Not vacuous: the credential really did travel through the auth path (and
    # therefore through resolve_principal, which writes to the request context).
    # /v1/config answers 403 for a non-admin bearer — that is after authentication,
    # which is the part under test.
    assert seen == [SECRET], "the bearer path never ran; this test would prove nothing"

    captured = capsys.readouterr()
    assert SECRET not in captured.err, "a bearer token reached a log line"
    assert SECRET not in captured.out
