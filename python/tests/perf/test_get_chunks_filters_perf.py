"""Perf budget for get_chunks' shared filter predicate (#197, #355 convention).

50 ids x 2 tenants must stay ONE ``retrieve`` round trip (asserted on the fake
client's call count) and the ``payload_matches`` filtering pass over the
returned records must stay well under budget. The fake client answers
in-process with no network, so timing the whole ``get_chunks()`` call is a
clean proxy for the Python-side filtering cost the budget is actually about.
"""
import pytest

pytest.importorskip("qdrant_client")

from ragstack.stores.qdrant import QdrantVectorStore  # noqa: E402
from tests.perf._budget import assert_budget_async
from tests.unit.test_get_chunks_filters import _FakeRetrieveClient, _record  # noqa: E402

_N_IDS = 50
_TENANTS = ["alice", "public"]


def _seed_store() -> tuple[QdrantVectorStore, _FakeRetrieveClient]:
    records = {}
    for i in range(_N_IDS):
        cid = f"c{i}"
        for tenant in _TENANTS:
            pid, rec = _record(cid, tenant, source=f"doc-{i}.pdf")
            records[pid] = rec
    store = QdrantVectorStore(collection="c", vector_size=4)
    client = _FakeRetrieveClient(records)
    store._client = client
    return store, client


@pytest.mark.perf
@pytest.mark.asyncio
async def test_get_chunks_one_round_trip_and_filtering_budget():
    store, client = _seed_store()
    ids = [f"c{i}" for i in range(_N_IDS)]
    filters = {"tenant_id": _TENANTS, "metadata.source": "doc-0.pdf"}

    async def _once() -> None:
        await store.get_chunks(ids, filters=filters)

    await assert_budget_async(
        "get_chunks_filtering_50ids_2tenants", _once, budget_s=0.002, n=20,
    )
    # One retrieve call per get_chunks() invocation regardless of filter
    # complexity — the O(ids) point-id batch stays a single round trip. 20
    # reps above => 20 total calls here.
    assert client.retrieve_calls == 20
