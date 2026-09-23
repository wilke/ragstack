"""Conformance: an ingest job's status is readable by its submitter and an admin,
and by nobody else (#628; matrix row A4; the IDOR #130 closed).

``POST /v1/ingest/upload`` answers 202 with a ``job_id``, and the contract says
to poll ``GET /v1/ingest/{job_id}`` for it. That read is tenant-scoped: the
submitter and an admin get the job's real status; any other principal gets the
SAME 200 ``{"status": "unknown", ...}`` a missing id gets, so the endpoint never
confirms that a foreign id exists. Before this file, the cross-tenant half was
covered only by Python unit tests (``python/tests/api/
test_ingest_status_tenant_scope.py``): ``test_authz.py`` deferred it for want of
a second tenant, which ``run_authz_keyed.sh`` has provisioned since #405 — every
key there maps to its own subject, so P2 and B are both foreign to P1.

The flow, once per module: P1 (``RAGSTACK_API_KEY_NONADMIN``) creates a scratch
collection it owns, uploads one small text file into it (202 + ``job_id``), and
every principal polls that id. Completion is NOT awaited: accepted, running,
completed and failed are all a real status, and the assertion is only about
*who can see it*. The scratch collection is purged at module teardown and the
purge verified by listing.

**Why a feature probe and not a ``_python_only`` autouse fixture.** Nothing here
is Python-specific in the contract; what the Go phase-1 scaffold lacks is the
upload itself (``HandleIngestUpload`` answers 501, and it keeps no job store).
So the module probes ``POST /v1/ingest/upload`` first, in the style of
``test_grading.py``'s ``grading_present``: 404 or 501 means the surface is
absent, and the module skips saying so. The day Go implements upload, these
tests run against it unchanged, with no allowlist to edit. The probe runs BEFORE
the credential check, so on a server without upload the skip is for absence,
never a ``RAGSTACK_CREDENTIAL_SKIP`` — ``run_authz_keyed.sh`` fails any run
containing one of those. (The Go ``unknown`` shape itself is pinned by
``TestIngestStatusUnknownJob`` in ``go/internal/api/router_test.go`` and by
``test_ingest.py::test_ingest_status_endpoint``.)

The probe is a multipart POST with no ``files`` part. On Python that is a 422
before the handler runs — nothing is staged and no job is created. Admin is used
so the per-principal ingest rate bucket is not spent (admin is exempt).

Runs in ``make test-conformance-keyed``.
"""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import httpx
import jsonschema
import pytest

from conftest import key, skip_no_credential

NOT_IMPLEMENTED = (
    "file upload ingestion is not implemented on this server "
    "(POST /v1/ingest/upload answered {status}), so no job id can be minted to "
    "scope-check"
)

#: Prefix for the scratch collection P1 creates, so a leak is unmistakable.
SCRATCH_PREFIX = "conf-jobscope-"

SAMPLE = (
    Path(__file__).resolve().parent.parent
    / "contracts" / "fixtures" / "documents" / "sample_small.txt"
)

#: Statuses a readable job can carry. "unknown" is deliberately absent.
REAL_STATUSES = {"accepted", "running", "completed", "failed"}

PRINCIPALS = {
    "admin": "RAGSTACK_API_KEY_ADMIN",
    "p1": "RAGSTACK_API_KEY_NONADMIN",
    "p2": "RAGSTACK_API_KEY_P2",
    "b": "RAGSTACK_API_KEY_B",
}


def _validate(body: object, schemas: dict[str, dict]) -> None:
    jsonschema.validate(instance=body, schema=schemas["ingest_response"])


def _ids(c: httpx.Client, headers: dict[str, str]) -> list[str]:
    resp = c.get("/v1/collections", headers=headers)
    assert resp.status_code == 200, (
        f"PRECONDITION key_is_valid: GET /v1/collections answered "
        f"{resp.status_code}: {resp.text[:300]}"
    )
    return [entry["id"] for entry in resp.json()["collections"]]


@pytest.fixture(scope="module")
def upload_present(base_url: str, auth_headers: dict[str, str]) -> None:
    """Skip the module unless the server implements ``POST /v1/ingest/upload``."""
    with httpx.Client(base_url=base_url, timeout=30.0, headers=auth_headers) as c:
        # A multipart body with a form field but no `files` part: rejected
        # before any staging on an implementation that has the route.
        resp = c.post("/v1/ingest/upload", data={"collection": "___probe___"})
    if resp.status_code in (404, 405, 501):
        pytest.skip(NOT_IMPLEMENTED.format(status=resp.status_code))


@pytest.fixture(scope="module")
def job(upload_present: None, base_url: str) -> Iterator[SimpleNamespace]:
    """P1 uploads one file into a collection it owns; yields the job id and the
    four principals' headers. Purges the collection afterwards, verified."""
    keys = {name: key(var) for name, var in PRINCIPALS.items()}
    missing = [PRINCIPALS[n] for n, k in keys.items() if not k]
    if missing:
        skip_no_credential(
            "the cross-tenant job read needs four distinct principals; unset: "
            f"{', '.join(missing)}. `make test-conformance-keyed` provisions them."
        )
    headers = {name: {"X-API-Key": k} for name, k in keys.items() if k}

    cid = f"{SCRATCH_PREFIX}{secrets.token_hex(4)}"
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        created = c.post("/v1/collections", json={"id": cid}, headers=headers["p1"])
        assert created.status_code == 201, (
            f"P1 could not create its scratch collection {cid!r}: "
            f"{created.status_code} {created.text[:300]}"
        )
        try:
            up = c.post(
                "/v1/ingest/upload",
                headers=headers["p1"],
                data={"collection": cid},
                files={"files": (SAMPLE.name, SAMPLE.read_bytes(), "text/plain")},
            )
            if up.status_code in (404, 501):
                pytest.skip(NOT_IMPLEMENTED.format(status=up.status_code))
            assert up.status_code == 202, (
                f"P1's upload into its own collection answered {up.status_code}: "
                f"{up.text[:500]}"
            )
            job_id = up.json()["job_id"]
            assert job_id, f"the 202 carried no job_id: {up.text}"
            yield SimpleNamespace(job_id=job_id, headers=headers, collection=cid)
        finally:
            c.delete(f"/v1/collections/{cid}?purge=true", headers=headers["p1"])
            # Verify by LISTING, never by trusting the delete's status.
            remaining = _ids(c, headers["admin"])
            assert cid not in remaining, (
                f"teardown did not remove {cid!r}; delete it by hand"
            )


def _poll(base_url: str, job_id: str, headers: dict[str, str]) -> httpx.Response:
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        return c.get(f"/v1/ingest/{job_id}", headers=headers)


# --------------------------------------------------------------------------- #
# The submitter and an admin see the real status
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("who", ["p1", "admin"])
def test_submitter_and_admin_read_the_real_status(
    who: str, job: SimpleNamespace, base_url: str, schemas: dict[str, dict]
) -> None:
    resp = _poll(base_url, job.job_id, job.headers[who])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    assert body["job_id"] == job.job_id
    assert body["status"] in REAL_STATUSES, (
        f"{who} polled the job it can read and got status {body['status']!r}; "
        f"expected one of {sorted(REAL_STATUSES)} (never 'unknown')"
    )
    # The poll answers "where did this land?" with the collection the upload named.
    assert body["collection"] == job.collection


# --------------------------------------------------------------------------- #
# Everyone else gets exactly what a missing id gets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("who", ["p2", "b"])
def test_foreign_principal_sees_unknown_indistinguishable_from_missing(
    who: str, job: SimpleNamespace, base_url: str, schemas: dict[str, dict]
) -> None:
    resp = _poll(base_url, job.job_id, job.headers[who])
    assert resp.status_code == 200, (
        f"a foreign job id must answer 200 (no IDOR via 404); {who} got "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    _validate(body, schemas)
    assert body["job_id"] == job.job_id
    assert body["status"] == "unknown", (
        f"{who} can read another tenant's job status: {body}"
    )
    assert body["chunk_ids"] == []
    assert body["items"] is None
    assert body["collection"] is None, (
        f"{who} learned the foreign job's collection: {body['collection']!r}"
    )

    # Indistinguishable from a missing id: the same bytes, modulo the echoed id
    # (a uuid4 of the same length, so the substitution keeps offsets equal).
    missing_id = str(uuid.uuid4())
    missing = _poll(base_url, missing_id, job.headers[who])
    assert missing.status_code == resp.status_code
    _validate(missing.json(), schemas)
    assert len(missing_id) == len(job.job_id)
    assert resp.content == missing.content.replace(
        missing_id.encode(), job.job_id.encode()
    ), (
        "a foreign job id and a missing one answer differently, so the "
        f"endpoint is an existence oracle:\n foreign: {resp.content!r}\n "
        f"missing: {missing.content!r}"
    )
