"""Conformance: a deployment must not mint a collection its ingest cannot populate.

#609. Under ``INGEST_BACKEND=gowe`` every ingest leaves the API process and a
GoWe scatter runs ``scripts/ingest_shard.py``, which cannot chunk semantically
yet. A hackathon user created a ``semantic`` collection through the documented
surface, uploaded 20 PDFs, and watched all 20 fail on all three attempts with an
error naming an internal tool. Nothing about the create told them, and no retry
could help.

**This file runs against every server, not only a gowe one.** The contract it
asserts is a disjunction, because which answer is correct depends on a deployment
setting conformance cannot see and must not change:

* **201** — this deployment ingests in-process and semantic works. The collection
  must then really be semantic, and must be deletable again.
* **422** — this deployment's ingest cannot chunk it. The body must then name the
  offending method AND at least one method that would work, and the collection
  must NOT exist afterwards. A refusal that leaves a half-created registry row,
  or that says only "unsupported", is a failure here.

Anything else — a 500, or a 201 followed by a collection that is not semantic —
is a bug either way, which is what makes the disjunction worth asserting rather
than skipping.

Set ``RAGSTACK_CONFORMANCE_INGEST_BACKEND=gowe`` when the server under test is
configured that way and the 422 branch becomes mandatory instead of merely
well-formed. ``make test-conformance-*`` does not set it: the in-repo servers
boot with the default local backend, so the 201 branch is the one they exercise.
"""

from __future__ import annotations

import os

import httpx
import pytest

from conftest import skip_no_credential

pytestmark = pytest.mark.asyncio

SEMANTIC = {"method": "semantic", "size": 512, "overlap": 0}

#: Methods the shard ingest can do — mirrors chunker_config.shard_supported_methods().
#: Spelled out rather than imported: conformance is black-box and may not import
#: from ``python/``. If this list and the server's ever disagree, the assertion
#: below ("names at least one supported method") is what still holds.
KNOWN_SUPPORTED = ("fixed", "fixed_token", "sentence", "words")


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


async def _register_embedding(client: httpx.AsyncClient, model_id: str) -> int:
    resp = await client.post(
        "/v1/admin/models/registry",
        json={
            "id": model_id, "task": "embedding", "provider": "vllm",
            "base_urls": ["http://localhost:9100"], "model": "conformance/emb", "dim": 8,
        },
        headers=_headers(),
    )
    return resp.status_code


async def _ids(client: httpx.AsyncClient) -> set[str]:
    listed = await client.get("/v1/collections", headers=_headers())
    assert listed.status_code == 200, listed.text
    return {c["id"] for c in listed.json()["collections"]}


async def test_semantic_create_is_either_honoured_or_refused_with_an_alternative(
    client: httpx.AsyncClient, impl: str
) -> None:
    if impl != "python":
        pytest.skip("build-spec resolution is python-authoritative in phase 3")
    mid = "conf-emb-semantic"
    if await _register_embedding(client, mid) in (401, 403):
        skip_no_credential(
            "the configured RAGSTACK_API_KEY lacks admin access to register a model "
            "/ create a collection with an explicit build spec"
        )

    before = await _ids(client)
    created = await client.post(
        "/v1/collections", json={"embedding": mid, "chunk": SEMANTIC}, headers=_headers()
    )
    assert created.status_code in (201, 422), created.text

    if created.status_code == 422:
        detail = created.json().get("detail")
        assert isinstance(detail, str) and detail, "422 must carry a string detail"
        assert "semantic" in detail, "the refusal must name the method it refused"
        assert any(m in detail for m in KNOWN_SUPPORTED), (
            "a refusal that names no working alternative leaves the caller with "
            f"nothing to do: {detail!r}"
        )
        # Refused means refused: no registry row, no id to clean up.
        assert await _ids(client) == before
        return

    info = created.json()
    assert info["chunk_method"] == "semantic", (
        "a 201 must honour the requested method — silently substituting a "
        "different chunker re-chunks the whole corpus without saying so"
    )
    cid = info["id"]
    assert cid in await _ids(client)
    deleted = await client.delete(f"/v1/collections/{cid}?purge=true", headers=_headers())
    assert deleted.status_code == 200, deleted.text


@pytest.mark.skipif(
    os.environ.get("RAGSTACK_CONFORMANCE_INGEST_BACKEND") != "gowe",
    reason=(
        "server under test is not declared INGEST_BACKEND=gowe; set "
        "RAGSTACK_CONFORMANCE_INGEST_BACKEND=gowe when it is. Not a credential "
        "skip — the deployment simply is not configured this way, and conformance "
        "does not flip a live server's backend to find out."
    ),
)
async def test_gowe_deployment_must_refuse_semantic(
    client: httpx.AsyncClient, impl: str
) -> None:
    """On a declared gowe server the 422 branch is the only correct answer."""
    if impl != "python":
        pytest.skip("build-spec resolution is python-authoritative in phase 3")
    mid = "conf-emb-semantic-gowe"
    if await _register_embedding(client, mid) in (401, 403):
        skip_no_credential("no admin credential for an explicit build-spec create")
    created = await client.post(
        "/v1/collections", json={"embedding": mid, "chunk": SEMANTIC}, headers=_headers()
    )
    assert created.status_code == 422, created.text
