"""Read-only smoke checks against a RUNNING tenant. Skipped unless opted into.

Tier 3 of the docs-backed test plan. Everything here is a ``GET``. There is no
write path in this file on purpose: a live tenant is somebody's data, and the
write-side assertions (the upload content-type gate) are pinned hermetically in
``tests/api/test_upload_hardening.py`` where they cost nothing and cannot touch
a real deployment.

Run it against the **dev** tenant, never a tenant serving users::

    RAGSTACK_LIVE_BASE_URL=http://127.0.0.1:24040 \\
    RAGSTACK_LIVE_TOKEN="$(cat ~/.patric_token)" \\
    pytest python/tests/live -q

``RAGSTACK_LIVE_BASE_URL`` has no default, deliberately: this repository has
been bitten by a default that resolved to a live production API (see the
conformance note in ``CLAUDE.md``). Without it every test here skips.

The token is optional — without one only the unauthenticated checks run.
Nothing here asserts a corpus exists, a document count, or an answer's text:
those are properties of whatever happens to be loaded, and a test that fails
because someone ingested is a test people learn to ignore.
"""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("RAGSTACK_LIVE_BASE_URL")
TOKEN = os.environ.get("RAGSTACK_LIVE_TOKEN")

pytestmark = pytest.mark.skipif(
    not BASE, reason="set RAGSTACK_LIVE_BASE_URL to run live smoke checks"
)


def _get(path: str, *, auth: bool = False) -> httpx.Response:
    headers = {"Authorization": TOKEN} if (auth and TOKEN) else {}
    return httpx.get(f"{BASE.rstrip('/')}{path}", headers=headers, timeout=15.0)


def test_health_is_open_and_answers():
    """/health is the one unauthenticated route; it is how you know the API is up."""
    r = _get("/health")
    assert r.status_code == 200, r.text


def test_version_reports_what_is_deployed():
    """Present since v1.6.0. A 404 here means the tenant predates it."""
    r = _get("/v1/version", auth=True)
    if r.status_code == 401 and not TOKEN:
        pytest.skip("set RAGSTACK_LIVE_TOKEN to check /v1/version")
    # A 401 WITH a token set is a bad credential, not an absent one. Skipping
    # there would report "set RAGSTACK_LIVE_TOKEN" to someone who did set it,
    # and hide a whole run behind a plausible message -- the claimed-but-false
    # precondition that tests/conftest.py (#432) exists to turn into a failure.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("version"), body
    # git_tag/git_sha are stamped by the supervisor; one of them must be usable
    # or nobody can tell what is running (see docs/runbooks/tenant-upgrade.md).
    assert body.get("git_tag") or body.get("git_sha"), body


def test_core_endpoints_require_a_credential():
    """The contract does not declare security on these (issue #561); the server does.

    If this ever passes without a credential, the deployment is open and the
    docs telling attendees their collections are private are wrong.
    """
    for path in ("/v1/collections", "/v1/documents"):
        r = _get(path)
        assert r.status_code in (401, 403), f"{path} answered {r.status_code}: {r.text[:200]}"


def test_admin_only_config_is_refused_or_allowed_but_never_errors():
    """``GET /v1/config`` answers 200 for an admin and 403 for anyone else.

    **This cannot prove the refusal**, and the name says so deliberately. The
    test has no way to learn the caller's role — the API exposes no who-am-I
    route (``contracts/openapi.yaml``) — so a normal user wrongly granted 200,
    which is the defect worth catching, is indistinguishable here from an admin
    correctly getting 200, and would skip rather than fail.

    What it does pin is that the route is reachable and answers one of the two
    documented statuses: a 401, 404 or 5xx fails. Proving the refusal needs a
    known-non-admin credential, which belongs in the keyed conformance suite
    (``make test-conformance-keyed``, four distinct principals) rather than in a
    smoke test pointed at whatever tenant the operator chose.
    """
    if not TOKEN:
        pytest.skip("set RAGSTACK_LIVE_TOKEN")
    r = _get("/v1/config", auth=True)
    assert r.status_code in (200, 403), r.text


def test_collections_listing_is_a_list_for_an_authenticated_caller():
    """Shape only. Never a count: that is whatever happens to be loaded."""
    if not TOKEN:
        pytest.skip("set RAGSTACK_LIVE_TOKEN")
    r = _get("/v1/collections", auth=True)
    assert r.status_code == 200, r.text
    assert isinstance(r.json().get("collections"), list), r.text
