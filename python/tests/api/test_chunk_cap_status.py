"""The chunk cap (#291) as seen through the API: a refused job's STATUS.

Both ingest paths, over the in-process app with in-memory doubles and fakes:

* **local** — a user-created collection (owner row, non-admin owner) ingesting
  more chunks than ``max_chunks_per_collection`` allows: the job ends
  ``failed`` with the ``chunk_cap_exceeded`` label on the job row, every item
  ``failed`` under the formatted refusal (the four numbers), nothing written to
  either store, and the poll response shape unchanged. An admin-owned
  (curated) collection with the same payload is exempt; a registry override
  wins both ways.
* **gowe** — the worker's receipt carries the label (fake receipts from the
  fake Workspace) and the API lifts it onto the job; the submission's inputs
  carry the derived ``max_chunks``, and omit it when no cap applies.

The is-user-created seam (``api/access.py::is_user_created``) is pinned on its
own: owner row + owner not admin, by every role source, failing toward the cap.
"""
from __future__ import annotations

import asyncio

import pytest

from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, get_acl_store
from ragstack.api import security
from ragstack.api.access import is_user_created
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.collection_store import CollectionSpec, InMemoryCollectionStore
from ragstack.ingestion.chunk_cap import CHUNK_CAP_EXCEEDED, format_refusal, is_cap_refusal
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt
from tests.api.conftest import SHARED_ID
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    TENANT,
    _upload,
    gowe,
)

pytestmark = pytest.mark.asyncio

KEYS = {"owner": "k-owner", "admin": "k-admin"}


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_chunks_per_collection", 3)


@pytest.fixture
def principals(monkeypatch):
    """Auth ON with two keyed callers: ``owner`` (user) and ``admin``."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(security.settings, "api_key_tenants",
                        {"k-owner": "owner", "k-admin": "admin"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


def _entry(cid: str, owner: str) -> CollectionEntry:
    """A non-default collection over the conftest's in-memory stores, chunked
    small (``fixed`` 40/0) so a short text file produces > 3 chunks."""
    return CollectionEntry(
        id=cid, label=cid, collection=f"{cid}_phys", model="test-model", dim=4,
        chunk_method="fixed", chunk_size=40, chunk_overlap=0, chunk_params={},
        is_shared_surface=False, retriever=object(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
        embedder=app.state.embedder, owner=owner,
    )


async def _install(cid: str, owner: str, **spec_over) -> None:
    """Register ``cid`` in the live registry, the durable registry and the ACL
    store (owner row), the way POST /v1/collections would have left it."""
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [*app.state.collections.entries(), _entry(cid, owner)], default_id=SHARED_ID,
    )
    spec = CollectionSpec(id=cid, owner=owner, collection=f"{cid}_phys", embedding_model="test-model",
                          embedding_model_dim=4, chunk_method="fixed", chunk_size=40,
                          chunk_overlap=0, **spec_over)
    store = getattr(app.state, "collection_store", None)
    if not isinstance(store, InMemoryCollectionStore):
        store = InMemoryCollectionStore()
        app.state.collection_store = store
    await store.put(spec)
    await get_acl_store().grant(cid, GRANTEE_USER, owner, PERM_OWNER, granted_by="system:test")


@pytest.fixture
def _cleanup():
    yield
    if hasattr(app.state, "collection_store"):
        delattr(app.state, "collection_store")


async def _poll(client, job_id: str, headers: dict) -> dict:
    body: dict = {}
    for _ in range(200):
        body = (await client.get(f"/v1/ingest/{job_id}", headers=headers)).json()
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)
    return body


def _doc(tmp_path, words: int = 40):
    f = tmp_path / "doc.txt"
    f.write_text(" ".join(f"word{i}" for i in range(words)), encoding="utf-8")  # ~280 chars
    return f


# --------------------------------------------------------------------------- #
# local path
# --------------------------------------------------------------------------- #


async def test_local_refusal_surfaces_on_the_job_status(client, principals, _cleanup, tmp_path):
    await _install("lib", owner="owner")
    r = await client.post("/v1/ingest", json={"source": str(_doc(tmp_path)), "collection": "lib"},
                          headers=_h("owner"))
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id, _h("owner"))
    # The poll response keeps its contract shape; the reason is on the job.
    assert set(body) == {"job_id", "status", "chunk_ids", "items"}
    assert body["status"] == "failed" and body["chunk_ids"] == []
    assert body["items"] == {"total": 1, "completed": 0, "failed": 1, "pending": 0}
    job = await app.state.job_store.get(job_id)
    assert job.error == CHUNK_CAP_EXCEEDED
    item = next(iter(app.state.job_store._items[job_id].values()))
    assert is_cap_refusal(item.error)
    live, incoming, cap, would_fit = (
        int(kv.split("=")[1]) for kv in item.error.split(": ", 1)[1].split()
    )
    assert (live, cap, would_fit) == (0, 3, 3) and incoming > 3
    assert item.error == format_refusal(0, incoming, 3)
    # nothing landed in either leg
    assert app.state.vector_store._chunks == [] and app.state.text_index._chunks == []
    # ...and the admin jobs listing shows the label
    jobs = (await client.get("/v1/jobs", headers=_h("admin"))).json()["jobs"]
    assert {j["job_id"]: j["error"] for j in jobs}[job_id] == CHUNK_CAP_EXCEEDED


async def test_admin_owned_collection_is_exempt(client, principals, _cleanup, tmp_path):
    await _install("curated", owner="admin")
    assert not await is_user_created("curated")
    r = await client.post("/v1/ingest", json={"source": str(_doc(tmp_path)),
                                              "collection": "curated"}, headers=_h("admin"))
    assert r.status_code == 200, r.text
    body = await _poll(client, r.json()["job_id"], _h("admin"))
    assert body["status"] == "completed" and len(body["chunk_ids"]) > 3


async def test_override_wins_on_the_api_path(client, principals, _cleanup, tmp_path):
    # exempt by override, although user-created and over the default
    await _install("lib0", owner="owner", max_chunks=0)
    r = await client.post("/v1/ingest", json={"source": str(_doc(tmp_path)), "collection": "lib0"},
                          headers=_h("owner"))
    assert (await _poll(client, r.json()["job_id"], _h("owner")))["status"] == "completed"
    # capped by override, although admin-owned
    await _install("cur2", owner="admin", max_chunks=2)
    r = await client.post("/v1/ingest", json={"source": str(_doc(tmp_path)), "collection": "cur2"},
                          headers=_h("admin"))
    assert (await _poll(client, r.json()["job_id"], _h("admin")))["status"] == "failed"
    assert (await app.state.job_store.get(r.json()["job_id"])).error == CHUNK_CAP_EXCEEDED


async def test_the_shared_default_surface_is_never_capped(client, principals, tmp_path):
    r = await client.post("/v1/ingest", json={"source": str(_doc(tmp_path))}, headers=_h("owner"))
    assert r.status_code == 200
    assert (await _poll(client, r.json()["job_id"], _h("owner")))["status"] == "completed"


# --------------------------------------------------------------------------- #
# is_user_created
# --------------------------------------------------------------------------- #


async def test_is_user_created_by_every_role_source(principals, monkeypatch):
    from ragstack.config import settings

    acl = get_acl_store()
    assert not await is_user_created("nobody-owns-this")            # no owner row
    await acl.grant("legacy", GRANTEE_USER, "legacy:admin", PERM_OWNER, granted_by="s")
    assert not await is_user_created("legacy")                      # the backfill owner
    await acl.grant("byuser", GRANTEE_USER, "owner", PERM_OWNER, granted_by="s")
    assert await is_user_created("byuser")                          # a keyed user tenant
    await acl.grant("bykey", GRANTEE_USER, "admin", PERM_OWNER, granted_by="s")
    assert not await is_user_created("bykey")                       # an admin API key's tenant
    await acl.grant("bylist", GRANTEE_USER, "bvbrc:root@x", PERM_OWNER, granted_by="s")
    monkeypatch.setattr(settings, "admin_subjects", ["bvbrc:root@x"])
    assert not await is_user_created("bylist")                      # ADMIN_SUBJECTS
    # an ACL store that cannot answer → the cap applies (fail toward the cap)
    class _Down:
        async def owner_of(self, cid):
            raise RuntimeError("acl down")
    assert await is_user_created("anything", store=_Down())


# --------------------------------------------------------------------------- #
# gowe path (fake engine + fake Workspace; the receipt carries the label)
# --------------------------------------------------------------------------- #


def _refusing_receipts(engine):
    """Make the fake worker refuse every shard at the cap: a failed receipt
    with the labelled four numbers, in input order (what ``run_shard`` emits)."""
    real = engine.receipts_for

    def refused(version):
        return [
            {**r, "status": FAILED, "n_chunks": 0, "chunk_ids": [],
             "error": format_refusal(2, 2, 3)}
            for r in real(version)
        ]
    engine.receipts_for = refused


async def test_gowe_receipt_label_is_lifted_onto_the_job(client, gowe):  # noqa: F811
    _refusing_receipts(gowe["engine"])
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id, AUTH)
    assert set(body) == {"job_id", "status", "chunk_ids", "items"}
    assert body["status"] == "failed"
    assert body["items"] == {"total": 2, "completed": 0, "failed": 2, "pending": 0}
    job = await app.state.job_store.get(job_id)
    assert job.error == CHUNK_CAP_EXCEEDED
    items = app.state.job_store._items[job_id]
    assert {i.error for i in items.values()} == {"chunk_cap_exceeded: live=2 incoming=2 cap=3 would_fit=1"}
    # The derived cap travelled to the worker as the workflow input (#291):
    # ``lib1`` is owned by a BV-BRC user, so the default applies.
    assert gowe["engine"].submissions[0]["inputs"]["max_chunks"] == 3


async def test_gowe_cap_input_follows_the_override_and_the_exemption(client, gowe, monkeypatch):  # noqa: F811
    from ragstack.config import settings

    inner = gowe["store"]._inner
    # override on the registry row → that value goes to the worker
    await inner.put(gowe["spec"].model_copy(update={"max_chunks": 7}))
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202
    assert gowe["engine"].submissions[-1]["inputs"]["max_chunks"] == 7
    # exempt (override 0) → no input at all (the workflow's default 0 = unlimited)
    await inner.put(gowe["spec"].model_copy(update={"max_chunks": 0}))
    r = await _upload(client, "b.pdf")
    assert r.status_code == 202
    assert "max_chunks" not in gowe["engine"].submissions[-1]["inputs"]
    # default switched off deployment-wide → no input either
    await inner.put(gowe["spec"])
    monkeypatch.setattr(settings, "max_chunks_per_collection", 0)
    r = await _upload(client, "c.pdf")
    assert r.status_code == 202
    assert "max_chunks" not in gowe["engine"].submissions[-1]["inputs"]
    # a mixed run (one shard refused, one completed) is NOT relabelled: the job
    # completes with a failed item that carries the reason.
    engine = gowe["engine"]
    real = engine.receipts_for

    def one_refused(version):
        rs = real(version)
        rs[0] = {**rs[0], "status": FAILED, "chunk_ids": [], "error": format_refusal(2, 2, 3)}
        return rs
    engine.receipts_for = one_refused
    r = await _upload(client, "d.pdf", "e.pdf")
    body = await _poll(client, r.json()["job_id"], AUTH)
    assert body["status"] == COMPLETED
    assert body["items"] == {"total": 2, "completed": 1, "failed": 1, "pending": 0}
    assert (await app.state.job_store.get(r.json()["job_id"])).error == ""


async def test_receipt_round_trips_the_label():
    r = ShardReceipt("s0", TENANT, FAILED, n_docs=1, error=format_refusal(10, 5, 12))
    back = ShardReceipt.from_dict(__import__("json").loads(r.to_json()))
    assert is_cap_refusal(back.error) and back.error.endswith("would_fit=2")


async def test_gowe_real_engine_failure_exit_4_reaches_the_job(client, gowe):  # noqa: F811
    """On a real engine a cap-refused task exits 4 and the submission FAILS
    before any receipt is delivered: the API classifies the engine's error
    record (``error.context.exit_code == 4``, the stderr line for the numbers)
    and the job carries the label — no receipt read, no archive."""
    line = format_refusal(49_990, 34, 50_000)
    backend = gowe["backend"]

    async def failed_wait(sub_id, **kw):
        return {"id": sub_id, "state": "FAILED", "output_state": "",
                "error": {"code": "TASK_FAILED", "message": "task ingest failed",
                          "context": {"stderr": "INFO: warm cache\n" + line + "\n",
                                      "exit_code": 4}}}

    backend.client.wait = failed_wait  # type: ignore[method-assign]
    r = await _upload(client, "a.pdf", "b.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id, AUTH)
    assert body["status"] == "failed"
    assert body["items"] == {"total": 2, "completed": 0, "failed": 2, "pending": 0}
    job = await app.state.job_store.get(job_id)
    assert job.error == CHUNK_CAP_EXCEEDED and job.archive_ref == ""
    assert {i.error for i in app.state.job_store._items[job_id].values()} == {line}
    assert gowe["workspace"].reads == []  # nothing delivered, nothing read
