"""``exclude_boilerplate`` on ``/v1/query`` and ``/v1/retrieve`` (issue #597).

A third of a real collection can be the paper's own bibliography, and a
reference-list passage carries the SOURCE paper's numbered citations — which is
how an extraction run came back citing ``[70]`` when the prompt declared sources
``1..50``. This flag lets a caller keep those chunks out of retrieval.

The three things this file is really about, in order of how badly they would
hurt if they were wrong:

1. **The flag is a PRESENCE stamp, not a boolean.** ``BoilerplateFilter.apply``
   writes ``metadata["is_boilerplate"]`` only for a chunk it classified as
   non-body. Measured across three live collections — oa-dev 67 stamped of
   24,263, Dengue 133 of 382, asm-semantic 0 of 6,718,269 — **every stamp
   present was ``True`` and not one was ``False``**. So an equality filter on
   ``false`` matches nothing and would exclude the entire corpus; only "not
   stamped true" is correct. An unstamped chunk must be KEPT, or a collection
   that was never flagged goes dark.
2. **A negation must not be able to escape tenant scoping.** The condition is
   built server-side from a BOOLEAN, so there is no client expression to
   contain — that is the design, and the hostile-payload tests below are what
   prove it stays true.
3. **``top_k`` is honoured exactly.** The exclusion is a store-side ``must_not``
   (Qdrant and Elasticsearch both do this natively and agree exactly — see
   ragstack/stores/filters.py), so the store prunes before scoring rather than
   Python discarding afterwards, and no over-fetch heuristic is involved.
"""
from __future__ import annotations

from typing import Any

import pytest

from ragstack.api.main import app
from ragstack.ingestion.boilerplate import BOILERPLATE_KEY
from ragstack.models import Chunk
from ragstack.stores.filters import Not
from ragstack.tenancy import OWNER_FIELD, readable_tenants

pytestmark = pytest.mark.asyncio

#: The embedder double in conftest returns this for every text, so the vector
#: leg scores every chunk identically and ranking falls back to insertion
#: order — deterministic, and it lets a test put the boilerplate FIRST.
_VEC = [0.1, 0.2, 0.3, 0.4]

#: One token in every chunk below, so the BM25 leg matches the whole corpus too
#: and both legs are actually exercised rather than one silently returning [].
_QUERY = "dengue"


def _chunk(cid: str, text: str, **metadata: Any) -> Chunk:
    return Chunk(
        id=cid,
        doc_id=cid.rsplit("-", 1)[0],
        content=text,
        embedding=list(_VEC),
        metadata={"tenant_id": "public", **metadata},
    )


#: Seeded boilerplate-first on purpose: without the exclusion these occupy the
#: top of an insertion-ordered pool, so "the flagged chunks are gone" and "the
#: caller still got top_k" are two distinct observations, not one.
def _corpus() -> list[Chunk]:
    flagged = [
        _chunk(
            f"refs-{i}",
            f"dengue {i}. Halstead SB, et al. Dengue virus pathogenesis. "
            f"J Infect Dis. 2019;{200 + i}(4):1-9.",
            section="references",
            **{BOILERPLATE_KEY: True},
        )
        for i in range(3)
    ]
    # The case that must NOT be dropped: the ingester looked and said no. Only
    # reachable when BOILERPLATE_SECTIONS is narrowed, but if it ever occurs the
    # chunk is body text and belongs in the results.
    stamped_false = [
        _chunk(
            "ack-0",
            "dengue acknowledgements: we thank the study nurses.",
            section="acknowledgements",
            **{BOILERPLATE_KEY: False},
        )
    ]
    # The overwhelming majority in every real collection: no stamp at all.
    body = [
        _chunk(
            f"body-{i}",
            f"dengue NS1 protein alters endothelial permeability in model {i}.",
        )
        for i in range(6)
    ]
    return flagged + stamped_false + body


async def _seed() -> None:
    chunks = _corpus()
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


async def _ids(client, endpoint: str, **body: Any) -> list[str]:
    resp = await client.post(endpoint, json={"query": _QUERY, **body})
    assert resp.status_code == 200, resp.text
    return [s["chunk_id"] for s in resp.json()["sources"]]


ENDPOINTS = ["/v1/retrieve", "/v1/query"]


# --------------------------------------------------------------------------- #
# The stamp's three states
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_flagged_chunks_are_dropped(client, endpoint) -> None:
    await _seed()
    ids = await _ids(client, endpoint, top_k=10, exclude_boilerplate=True)
    assert [i for i in ids if i.startswith("refs-")] == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_an_unstamped_chunk_is_kept(client, endpoint) -> None:
    """Absence means "not boilerplate". This is the row that decides whether a
    collection carrying no flags at all (asm-semantic: 0 of 6.7M) keeps working
    — a ``{"is_boilerplate": false}`` equality filter would return NOTHING
    there, which is far worse than the disease."""
    await _seed()
    ids = await _ids(client, endpoint, top_k=10, exclude_boilerplate=True)
    assert {f"body-{i}" for i in range(6)} <= set(ids)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_a_chunk_stamped_false_is_kept(client, endpoint) -> None:
    """``False`` is the ingester having looked and said no — the opposite of a
    verdict to exclude on."""
    await _seed()
    ids = await _ids(client, endpoint, top_k=10, exclude_boilerplate=True)
    assert "ack-0" in ids


async def test_a_collection_with_no_stamps_at_all_is_unaffected(client) -> None:
    """The asm-semantic shape, end to end: with the flag on and nothing stamped,
    the results are IDENTICAL to the flag being off — not merely non-empty."""
    body = [_chunk(f"body-{i}", f"dengue finding number {i}.") for i in range(5)]
    await app.state.vector_store.upsert(body)
    await app.state.text_index.index(body)

    off = await _ids(client, "/v1/retrieve", top_k=5)
    on = await _ids(client, "/v1/retrieve", top_k=5, exclude_boilerplate=True)
    assert on == off == [f"body-{i}" for i in range(5)]


# --------------------------------------------------------------------------- #
# Default OFF, and top_k honoured exactly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_the_field_defaults_to_off(client, endpoint) -> None:
    """A request that does not mention the field gets the boilerplate it always
    got. A live event is running on these tenants; flipping the default is a
    product decision, not this issue's."""
    await _seed()
    absent = await _ids(client, endpoint, top_k=10)
    explicit_false = await _ids(client, endpoint, top_k=10, exclude_boilerplate=False)
    assert absent == explicit_false
    assert [i for i in absent if i.startswith("refs-")] != []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_top_k_is_filled_from_body_text_not_left_short(client, endpoint) -> None:
    """With the flag off, boilerplate really does occupy slots the caller wanted
    for evidence. With it on, the caller must still get a FULL ``top_k`` — every
    slot refilled from body text, none left short.

    No over-fetch is involved: the store's ``must_not`` prunes before scoring,
    so ``top_k`` is exact rather than "however many survived a post-filter" —
    which is precisely what a Python post-filter could not promise."""
    await _seed()
    off = await _ids(client, endpoint, top_k=5)
    assert len(off) == 5
    displaced = [i for i in off if i.startswith("refs-")]
    assert displaced, "the fixture stopped exercising the case it exists for"

    on = await _ids(client, endpoint, top_k=5, exclude_boilerplate=True)
    assert len(on) == 5, "top_k came back short — the store did not refill it"
    assert all(not i.startswith("refs-") for i in on)
    # Every displaced slot was refilled from further down the SAME pool, not
    # padded: the extra ids are real chunks that were below the cut before.
    assert set(on) - set(off) and set(on) <= {f"body-{i}" for i in range(6)} | {"ack-0"}


async def test_context_window_neighbours_are_filtered_too(client) -> None:
    """A body chunk whose next neighbour is the first reference-list chunk must
    not smuggle the bibliography in as ``context`` — that text would reach an
    extractor's prompt exactly as a retrieved source would."""
    body = _chunk("paper-0", "dengue NS1 alters endothelial permeability.")
    refs = _chunk(
        "paper-1",
        "dengue 85. Halstead SB. Pathogenesis. J Infect Dis. 2019;200(4):1-9.",
        section="references",
        **{BOILERPLATE_KEY: True},
    )
    body.metadata["next_chunk_id"] = refs.id
    refs.metadata["prev_chunk_id"] = body.id
    await app.state.vector_store.upsert([body, refs])
    await app.state.text_index.index([body, refs])

    resp = await client.post(
        "/v1/retrieve",
        json={"query": _QUERY, "top_k": 1, "context_window": 1, "exclude_boilerplate": True},
    )
    assert resp.status_code == 200, resp.text
    sources = resp.json()["sources"]
    assert [s["chunk_id"] for s in sources] == ["paper-0"]
    assert [c["chunk_id"] for c in sources[0].get("context", [])] == []


# --------------------------------------------------------------------------- #
# Tenant scoping — the one failure mode here that would be a data-isolation bug
# --------------------------------------------------------------------------- #
#: Every shape a caller could try if they were reaching for a negation, or for
#: the tenant key, through the one field that is caller-controlled.
HOSTILE_FILTERS: list[dict[str, Any]] = [
    {"tenant_id": "victim"},                       # plain override attempt
    {"tenant_id": ["victim", "public"]},           # widen by membership
    {"tenant_id": {"not": "public"}},              # negate the scope directly
    {"is_boilerplate": {"not": True}},             # ask for a Not by JSON shape
    {"is_boilerplate": False},                     # the filter that matches nothing
    {"tenant_id": {"$ne": "public"}},              # mongo-flavoured negation
]


@pytest.mark.parametrize("hostile", HOSTILE_FILTERS, ids=lambda f: str(f))
@pytest.mark.parametrize("exclude", [False, True])
async def test_a_hostile_filter_cannot_widen_tenant_scope(
    client, monkeypatch, hostile, exclude
) -> None:
    """Whatever the caller puts in ``filters``, the dict that reaches the store
    pins ``tenant_id`` to the tenants this caller may read — with the negation
    on as well as off.

    Captured at the store boundary rather than asserted on the response: an
    empty result set would "pass" a response-shaped test for the wrong reason
    (a leak that returned nothing because the victim tenant happens to be empty
    is still a leak). An object value is refused as a 400 before it ever gets
    there, which is itself the point — that is why there is no wire syntax for
    a negation."""
    await _seed()
    seen: list[dict[str, Any]] = []
    original = app.state.vector_store.search

    async def _spy(query_vector, top_k=5, filters=None):
        seen.append(dict(filters or {}))
        return await original(query_vector, top_k=top_k, filters=filters)

    monkeypatch.setattr(app.state.vector_store, "search", _spy)

    resp = await client.post(
        "/v1/retrieve",
        json={"query": _QUERY, "filters": hostile, "exclude_boilerplate": exclude},
    )
    # An object value never reaches a store at all — the grammar refuses it.
    if any(isinstance(v, dict) for v in hostile.values()):
        assert resp.status_code == 400, resp.text
        assert seen == []
        return

    assert resp.status_code == 200, resp.text
    assert seen, "the vector leg never ran, so nothing was proven"
    for filters in seen:
        assert filters[OWNER_FIELD] == readable_tenants("default"), (
            f"tenant scope was widened to {filters[OWNER_FIELD]!r}"
        )
        assert not isinstance(filters[OWNER_FIELD], Not)


@pytest.mark.parametrize("value", [{"not": True}, {"$ne": True}, {"gte": 1}, [True]])
async def test_no_wire_value_can_construct_a_negation(client, value) -> None:
    """There is deliberately NO JSON that becomes a :class:`Not`. Each of these
    is refused by the existing value grammar (400) rather than being
    interpreted — which is why adding server-side negation does not widen the
    request grammar at all."""
    await _seed()
    resp = await client.post(
        "/v1/retrieve", json={"query": _QUERY, "filters": {BOILERPLATE_KEY: value}}
    )
    assert resp.status_code == 400, resp.text


async def test_the_server_condition_wins_over_a_caller_supplied_one(
    client, monkeypatch
) -> None:
    """A caller who sends ``{"is_boilerplate": true}`` AND asks to exclude gets
    the exclusion: the server's condition is merged last, exactly as
    ``scope_filters`` pins the tenant key last. The alternative — honouring
    both — is an unsatisfiable filter and a silently empty result."""
    await _seed()
    seen: list[dict[str, Any]] = []
    original = app.state.vector_store.search

    async def _spy(query_vector, top_k=5, filters=None):
        seen.append(dict(filters or {}))
        return await original(query_vector, top_k=top_k, filters=filters)

    monkeypatch.setattr(app.state.vector_store, "search", _spy)
    resp = await client.post(
        "/v1/retrieve",
        json={
            "query": _QUERY,
            "filters": {BOILERPLATE_KEY: True},
            "exclude_boilerplate": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert seen and seen[0][BOILERPLATE_KEY] == Not(True)
    assert [s["chunk_id"] for s in resp.json()["sources"]] != []
