"""Conformance: the authorization matrix, generated from the contract.

Every operation in ``contracts/ctl/openapi.yaml`` carries ``x-ctl-role``. This
file parametrizes over them (reading the contract is not importing the
implementation) and asserts three families:

* every non-anonymous operation, called with NO credential → 401;
* every ``operator`` operation, called with the VIEWER key → 403 ``forbidden``
  — for GETs and for every mutation alike, with a well-formed dry-run body so
  that authorization is the only possible reason for the refusal;
* every ``viewer`` operation, called with the viewer key → neither 401 nor 403.

Path parameters are substituted with values that are syntactically valid but
name nothing (a tenant that does not exist, a job id that does not exist):
the contract says authorization precedes lookup, so the answer must still be
the role answer, never a 404. That is also what stops a viewer from probing
for tenant existence. The one viewer-positive case that needs a real tenant
(``/v1/tenants/{name}``) takes it from the fleet.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from ctl.helpers import assert_error

pytestmark = pytest.mark.asyncio

CONTRACT_METHODS = {"get", "put", "post", "delete", "patch"}

#: Syntactically valid path values that name nothing.
SUBSTITUTIONS = {
    "{name}": "zz-conformance-authz",
    "{id}": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "{n}": "1",
    "{verb}": "start",
}

#: Required query parameters per path (the contract marks them required; a
#: missing one would be a 422 that masks the authorization answer).
REQUIRED_QUERY = {
    "/v1/tenants/{name}/logs": {"file": "api"},
}


def _operations(openapi: dict) -> list[tuple[str, str, str, str, str]]:
    """``(operationId, method, path, role, body_kind)`` for every operation."""
    out = []
    for path, item in openapi["paths"].items():
        for method, op in item.items():
            if method not in CONTRACT_METHODS:
                continue
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            kind = (body.get("$ref") or "").split("/")[-1]
            out.append((op["operationId"], method, path, op["x-ctl-role"], kind))
    return out


def _load_openapi() -> dict:
    # Parametrization happens at collection time, before fixtures exist.
    from ctl.conftest import CONTRACT_DIR
    import yaml

    with open(CONTRACT_DIR / "openapi.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_OPS = _operations(_load_openapi())
OPERATOR_OPS = [o for o in _OPS if o[3] == "operator"]
VIEWER_OPS = [o for o in _OPS if o[3] == "viewer"]
AUTHENTICATED_OPS = [o for o in _OPS if o[3] != "anonymous"]


def _concrete(path: str) -> str:
    for k, v in SUBSTITUTIONS.items():
        path = path.replace(k, v)
    return path


def _body(kind: str) -> dict | None:
    """A body that is VALID for the operation's envelope, so a 422 cannot be
    the answer; ``dry_run: true`` so that even a wrongly-authorized call
    could not mutate anything."""
    if kind == "OpRequest":
        return {"dry_run": True, "idempotency_key": f"conformance-authz-{uuid.uuid4()}", "args": {}}
    if kind == "CreateRequest":
        return {
            "dry_run": True,
            "idempotency_key": f"conformance-authz-{uuid.uuid4()}",
            "args": {"name": "zz-conformance-authz", "artifact_id": "conformance-none"},
        }
    return None


async def _call(c: httpx.AsyncClient, method: str, path: str, kind: str, headers: dict[str, str]) -> httpx.Response:
    body = _body(kind)
    return await c.request(
        method.upper(),
        _concrete(path),
        params=REQUIRED_QUERY.get(path),
        headers=headers,
        json=body,
    )


@pytest.mark.parametrize("opid,method,path,role,kind", AUTHENTICATED_OPS, ids=[o[0] for o in AUTHENTICATED_OPS])
async def test_no_credential_is_401(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict],
    opid: str, method: str, path: str, role: str, kind: str,
) -> None:
    resp = await _call(anon_client, method, path, kind, headers={})
    assert_error(resp, 401, "auth_required", schemas)


@pytest.mark.parametrize("opid,method,path,role,kind", OPERATOR_OPS, ids=[o[0] for o in OPERATOR_OPS])
async def test_viewer_is_403_on_every_operator_operation(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], ctl_viewer_key: str,
    opid: str, method: str, path: str, role: str, kind: str,
) -> None:
    """GET or mutation, existing target or not: a viewer gets 403 ``forbidden``
    and nothing else — not 404 (authorization precedes lookup), not 422 (the
    body is valid), not 200 with a reduced view."""
    resp = await _call(anon_client, method, path, kind, headers={"X-API-Key": ctl_viewer_key})
    assert_error(resp, 403, "forbidden", schemas)


@pytest.mark.parametrize("opid,method,path,role,kind", VIEWER_OPS, ids=[o[0] for o in VIEWER_OPS])
async def test_viewer_reaches_every_viewer_operation(
    anon_client: httpx.AsyncClient, ctl_viewer_key: str, some_tenant: str,
    opid: str, method: str, path: str, role: str, kind: str,
) -> None:
    """A viewer is never 401/403 on a viewer operation. The status may be a
    404 (the substituted target does not exist) — that is the lookup answer,
    which for a viewer-permitted route is allowed to come after authorization.
    ``/v1/tenants/{name}`` and its ``env`` use a real tenant so the positive
    200 is also seen."""
    concrete = path.replace("{name}", some_tenant)
    for k, v in SUBSTITUTIONS.items():
        concrete = concrete.replace(k, v)
    resp = await anon_client.request(
        method.upper(), concrete, params=REQUIRED_QUERY.get(path),
        headers={"X-API-Key": ctl_viewer_key}, json=_body(kind),
    )
    assert resp.status_code not in (401, 403), (
        f"{opid}: the viewer was refused with {resp.status_code}: {resp.text[:300]}"
    )
    if "{" not in path:
        assert resp.status_code < 400, f"{opid}: {resp.status_code}: {resp.text[:300]}"


async def test_matrix_and_x_ctl_role_agree(openapi: dict) -> None:
    """The extension table and the per-operation annotation are one fact."""
    rows = {r["operationId"]: r for r in openapi["x-ctl-authorization-matrix"]}
    for opid, method, path, role, _ in _OPS:
        assert rows[opid]["role"] == role, opid
        assert rows[opid]["method"].lower() == method, opid
        assert rows[opid]["path"] == path, opid
    assert set(rows) == {o[0] for o in _OPS}
