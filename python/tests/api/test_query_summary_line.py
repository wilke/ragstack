"""The per-request summary line — #427's acceptance criterion, as a test file.

The criterion, in one sentence: *a repeat of the incident must produce one
greppable line saying which request, whose, which leg, how much of the bound it
consumed, and how many other requests the process was serving.* Before W3 a
successful request logged **nothing at all**, and the one that 503'd logged a
sentence that could not be tied to a user, a query or a duration.

``test_the_incident_replays_as_one_line_naming_the_leg`` is the assertion this
whole PR exists for. If exactly one test here survives, it is that one.
"""
import asyncio
import logging

import pytest

from ragstack.api.main import app
from ragstack.observability.context import RequestContextFilter
from ragstack.observability.middleware import RequestContextMiddleware
from ragstack.observability.stages import STAGE_NAMES, query_sha
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores.errors import KIND_TIMEOUT, StoreUnavailable

pytestmark = pytest.mark.asyncio

#: The logger the line comes from. Everything here filters on it, because the
#: request path emits plenty of other records and a test that matched on the
#: message alone would eventually match one of them.
SUMMARY_LOGGER = "ragstack.observability.middleware"

#: What a ``/v1/query`` against the default in-memory fixture must time. Derived
#: from the fixture's wiring, not from a wish list: no reranker (``rerank`` never
#: runs), no LLM (``generate`` never runs), no graph store on the retriever
#: (``graph`` never runs), one variant (so the router's own ``fuse`` is skipped
#: and only the retriever's fires).
#:
#: This is the A5 assertion. Its value is not that these seven names are right —
#: it is that adding an eighth external call to the query path without timing it
#: leaves this set unchanged and this test green, while adding one *with* a timer
#: fails it loudly and forces the author to say so. A silent untimed store round
#: trip is what inflates ADR-0006's residual and could justify a Go port the
#: measurement does not support.
EXPECTED_QUERY_STAGES = {"authz", "rewrite", "embed", "vector", "text", "fuse", "expand"}

#: ``/v1/retrieve`` is the same minus query rewriting, which it does not do.
EXPECTED_RETRIEVE_STAGES = EXPECTED_QUERY_STAGES - {"rewrite"}


def _summaries(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == SUMMARY_LOGGER and r.getMessage() == "request complete"
    ]


def _one_summary(caplog) -> logging.LogRecord:
    found = _summaries(caplog)
    assert len(found) == 1, f"expected exactly one summary line, got {len(found)}"
    return found[0]


def _stage_names(record: logging.LogRecord) -> set[str]:
    return {
        key[: -len("_ms")]
        for key in record.__dict__
        if key.endswith("_ms") and key not in ("wall_ms", "self_ms")
    }


class _ExplodingVectorStore:
    """The incident: a vector search that consumes its whole bound and then
    raises ``ReadTimeout``, surfaced as ``StoreUnavailable(kind="timeout")``."""

    async def search(self, *_args, **_kwargs):
        raise StoreUnavailable(
            "qdrant",
            "qdrant search on 'ragstack_lib_open_access' at <store> failed — "
            "ReadTimeout; per-request timeout is 30s (QDRANT_TIMEOUT)",
            kind=KIND_TIMEOUT,
            elapsed_s=30.0,
        )


# --------------------------------------------------------------------------- #
# The acceptance criterion
# --------------------------------------------------------------------------- #


async def test_the_incident_replays_as_one_line_naming_the_leg(client, caplog):
    """**The assertion this PR exists for.**

    A ``/v1/query`` whose vector store times out must still produce the summary
    line — from the middleware's ``finally``, so failure is not a path that skips
    it — and that line must say *which leg* burned the time. Specifically:

    * ``embed_ms`` is **present**: the query embedding succeeded, so the LLM
      fleet is exonerated by the same line that accuses the store;
    * ``vector_ms`` is **present**, because ``stage.__exit__`` records whether or
      not the body raised — a timer that skipped on failure would have had
      nothing to say about the only request anyone cared about;
    * ``text_ms`` is **absent**, because the BM25 leg never ran. An absent field
      is a fact, and a zero would have been a lie;
    * the status is the observed ``503`` and the outcome is ``server_error``,
      at WARNING, so it survives ``LOG_LEVEL=WARNING``.
    """
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(RequestContextFilter())
    app.state.retriever = HybridRetriever(
        _ExplodingVectorStore(), app.state.text_index, app.state.embedder
    )

    r = await client.post("/v1/query", json={"query": "why was this slow", "top_k": 3})
    assert r.status_code == 503, r.text

    line = _one_summary(caplog)
    assert line.status == 503
    assert line.outcome == "server_error"
    assert line.levelno == logging.WARNING, "a 503 must survive LOG_LEVEL=WARNING"

    assert hasattr(line, "embed_ms"), "no embed timing — the LLM fleet is not exonerated"
    assert hasattr(line, "vector_ms"), (
        "the failing leg recorded no time — a timer that skips on failure is "
        "useless for exactly the request that matters"
    )
    assert not hasattr(line, "text_ms"), "the BM25 leg never ran; it must not be reported"

    # …and the line is tied to a request, a caller and a query.
    assert line.rid != "-"
    assert line.route == "POST /v1/query"
    assert line.qsha == query_sha("why was this slow")
    assert float(line.wall_ms) >= 0.0
    assert line.inflight >= 1


# --------------------------------------------------------------------------- #
# The happy path — a successful request logged nothing at all before W3
# --------------------------------------------------------------------------- #


async def test_a_successful_query_emits_exactly_one_line_at_info(client, caplog):
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(RequestContextFilter())

    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text

    line = _one_summary(caplog)
    assert line.levelno == logging.INFO
    assert line.status == 200
    assert line.outcome == "ok"
    assert line.route == "POST /v1/query"
    assert line.rid == r.headers["x-request-id"], (
        "the line's id and the header's id must be the same value, or a user's "
        "screenshot cannot be turned into a log lookup"
    )
    assert line.tenant != "-"
    assert line.coll  # the registry collection this ran against
    assert float(line.self_ms) >= 0.0
    assert line.embed_ms.endswith("/1")


async def test_every_request_gets_a_line_even_without_a_query_path(client, caplog):
    """``/health`` too. That is what makes the uvicorn access log replaceable
    rather than merely duplicated: the summary line covers every route."""
    caplog.set_level(logging.INFO)
    await client.get("/health")
    line = _one_summary(caplog)
    assert line.route == "GET /health"
    assert line.status == 200
    # No query ran, so there is nothing to time and nothing is invented.
    assert _stage_names(line) == set()


# --------------------------------------------------------------------------- #
# A5 — the stage-name set, pinned so an untimed external call is loud
# --------------------------------------------------------------------------- #


async def test_the_query_path_times_exactly_the_expected_stages(client, caplog):
    caplog.set_level(logging.INFO)
    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text

    emitted = _stage_names(_one_summary(caplog))
    assert emitted == EXPECTED_QUERY_STAGES, (
        "the timed-stage set changed. If you added an external call to the query "
        "path, time it and add it here; if you added an in-process one, add it to "
        "STAGE_NAMES too. Silence here is how ADR-0006's residual gets inflated."
    )
    assert emitted <= STAGE_NAMES, f"{emitted - STAGE_NAMES} is not a declared stage name"


async def test_the_retrieve_path_times_exactly_the_expected_stages(client, caplog):
    caplog.set_level(logging.INFO)
    r = await client.post("/v1/retrieve", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text

    emitted = _stage_names(_one_summary(caplog))
    assert emitted == EXPECTED_RETRIEVE_STAGES
    assert "rewrite" not in emitted, "/v1/retrieve does not rewrite queries"


async def test_a_fully_wired_query_times_the_model_stages_and_tags_the_collection(
    client, caplog
):
    """The three stages the default fixture cannot exercise — ``graph``,
    ``rerank`` and ``generate`` — plus the per-leg ``by_coll`` attribution that a
    tagged (collection-scoped) retriever produces.

    Without this, the set assertion above would be pinning a *subset* and the
    three most expensive external calls on the query path would be untested.
    """

    class _Reranker:
        async def score(self, _query, chunks, top_k=None):
            from ragstack.models import ScoredChunk

            return [ScoredChunk(chunk=c, score=1.0, retrieval_method="rerank") for c in chunks]

    class _Generator:
        async def generate(self, _query, _sources):
            return "an answer"

    from ragstack.models import Chunk

    caplog.set_level(logging.INFO)
    # A seeded chunk, because `_maybe_rerank` returns before touching the
    # cross-encoder when the fused pool is empty — an empty store would have made
    # this test pass vacuously on the very stage it is here to cover.
    await app.state.vector_store.upsert(
        [
            Chunk(
                id="c1",
                doc_id="d1",
                content="hello world",
                embedding=[0.1, 0.2, 0.3, 0.4],
                metadata={"tenant_id": "public"},
            )
        ]
    )
    app.state.retriever = HybridRetriever(
        app.state.vector_store,
        app.state.text_index,
        app.state.embedder,
        graph_store=app.state.graph_store,
        collection="phys_collection_a",
    )
    app.state.reranker = _Reranker()
    app.state.generator = _Generator()

    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text

    line = _one_summary(caplog)
    emitted = _stage_names(line)
    assert {"graph", "rerank", "generate"} <= emitted, f"missing from {emitted}"
    assert emitted <= STAGE_NAMES
    # The tag is the PHYSICAL collection name — the registry id is `coll`.
    assert "vector@phys_collection_a=" in line.by_coll
    assert "text@phys_collection_a=" in line.by_coll


# --------------------------------------------------------------------------- #
# #114 — the query text is never logged
# --------------------------------------------------------------------------- #


async def test_no_log_line_contains_the_query_text(client, caplog):
    """#114 mandates redaction by default. The fingerprint goes on the line
    instead, so two occurrences of the same query are still correlatable.

    Asserted over the FORMATTED output of every record, not over the summary
    line's fields, because the risk is a stray ``log.info("query %s", q)``
    anywhere on the path — not just here.
    """
    secret = "zzunmistakablequerytextzz"
    caplog.set_level(logging.DEBUG)

    r = await client.post("/v1/query", json={"query": secret, "top_k": 1})
    assert r.status_code == 200, r.text

    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__}"
        assert secret not in rendered, f"query text leaked into {record.name}: {rendered[:200]}"

    assert _one_summary(caplog).qsha == query_sha(secret)


# --------------------------------------------------------------------------- #
# The level table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raised", "expected_outcome", "expected_level"),
    [
        (asyncio.CancelledError, "client_disconnected", logging.INFO),
        (RuntimeError, "unhandled", logging.WARNING),
    ],
)
async def test_the_level_matches_the_outcome(caplog, raised, expected_outcome, expected_level):
    """``client_disconnected`` at **INFO**, and this is a decision rather than an
    oversight. A user closing a tab is not a server fault: it must never page
    anyone and must never enter W4's error rate, so it is not WARNING. But a
    disconnect during a 30-second query is evidence in exactly the scenario #427
    exists for, so it is not DEBUG either.

    ``unhandled`` at WARNING with the status the middleware stamps, because an
    exception escaping user middleware IS a 500.

    Driven at the ASGI layer directly: httpx cannot produce a mid-request client
    disconnect against ``ASGITransport``.
    """
    caplog.set_level(logging.INFO)

    async def _app(scope, receive, send):
        raise raised()

    mw = RequestContextMiddleware(_app)
    scope = {"type": "http", "method": "GET", "path": "/v1/query", "headers": []}

    async def _send(message):  # pragma: no cover - never reached
        raise AssertionError("no response should be sent")

    with pytest.raises(raised):
        await mw(scope, None, _send)

    line = _one_summary(caplog)
    assert line.outcome == expected_outcome
    assert line.levelno == expected_level


async def test_a_disconnect_before_any_response_reports_no_status_rather_than_a_guess(caplog):
    """``status=-``, never a number. The whole point of this line is that the
    least-explained failures stop being described with invented facts; a
    plausible ``200`` here would be exactly that.
    """
    caplog.set_level(logging.INFO)

    async def _app(scope, receive, send):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await RequestContextMiddleware(_app)(
            {"type": "http", "method": "GET", "path": "/v1/query", "headers": []},
            None,
            None,
        )

    assert _one_summary(caplog).status == "-"


# --------------------------------------------------------------------------- #
# inflight, and the per-request isolation the whole line depends on
# --------------------------------------------------------------------------- #


async def test_inflight_counts_the_requests_the_process_is_serving(client, caplog):
    """"How many other requests was this process serving?" is one of the incident
    questions no record could answer. Three concurrent queries must report more
    than one, and a lone request must report exactly one."""
    caplog.set_level(logging.INFO)
    await asyncio.gather(
        *(client.post("/v1/query", json={"query": f"q{i}", "top_k": 1}) for i in range(3))
    )

    counts = sorted(int(r.inflight) for r in _summaries(caplog))
    assert len(counts) == 3
    assert max(counts) > 1, f"concurrency was invisible: {counts}"

    caplog.clear()
    await client.post("/v1/query", json={"query": "alone", "top_k": 1})
    assert _one_summary(caplog).inflight == 1


async def test_the_counter_returns_to_zero_after_a_failing_request(client, caplog):
    """A request that raises must still decrement. Otherwise ``inflight`` drifts
    upward for the life of the process and eventually describes nothing."""
    caplog.set_level(logging.INFO)
    app.state.retriever = HybridRetriever(
        _ExplodingVectorStore(), app.state.text_index, app.state.embedder
    )
    for _ in range(3):
        assert (await client.post("/v1/query", json={"query": "x"})).status_code == 503

    caplog.clear()
    app.state.retriever = HybridRetriever(
        app.state.vector_store, app.state.text_index, app.state.embedder
    )
    await client.get("/health")
    assert _one_summary(caplog).inflight == 1, "the in-flight counter leaked"


async def test_two_sequential_requests_do_not_share_timings(client, caplog):
    """The leak regression at the level a user would see it: request N+1's line
    must not carry request N's numbers. ``/health`` times nothing, so if the
    query before it leaked, this line would carry stage fields.

    The first line's fields are asserted **present** before the second's are
    asserted absent, deliberately. An "is absent" assertion alone would pass just
    as happily against a build where stage timing does not work at all — which is
    a test satisfied by an absent thing rather than by the behaviour it names.
    """
    caplog.set_level(logging.INFO)
    await client.post("/v1/query", json={"query": "first", "top_k": 1})
    first = _one_summary(caplog)
    assert _stage_names(first) == EXPECTED_QUERY_STAGES
    assert first.qsha == query_sha("first")
    caplog.clear()

    await client.get("/health")
    line = _one_summary(caplog)
    assert _stage_names(line) == set(), "the previous request's stages leaked forward"
    assert not hasattr(line, "qsha"), "the previous request's query fingerprint leaked forward"


# --------------------------------------------------------------------------- #
# The multi-collection path — where the counts and the tags earn their keep
# --------------------------------------------------------------------------- #


@pytest.fixture
async def two_collections(client):
    """Two registry entries, each with its own in-memory stores and a retriever
    that KNOWS ITS PHYSICAL COLLECTION.

    Built here rather than borrowed from ``test_query_collections.py`` for one
    reason that matters: that fixture constructs its retrievers without
    ``collection=``, so every tag would be ``None`` and the ``by_coll``
    assertions below would pass vacuously against a build with no tagging at all.
    ``deps.py:293-306`` passes the physical name in production, so this fixture
    mirrors production and the test is about the wiring that actually ships.
    """
    from ragstack.api.collections import CollectionEntry
    from ragstack.models import Chunk
    from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
    from tests.api.conftest import _FakeEmbedder

    added = []
    for cid in ("col_a", "col_b"):
        vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
        chunks = [
            Chunk(
                id=f"{cid}-c0",
                doc_id=f"{cid}-d",
                content=f"how does BM25 work, in {cid}",
                embedding=[0.1, 0.2, 0.3, 0.4],
                metadata={"tenant_id": "public"},
            )
        ]
        await vs.upsert(chunks)
        await ti.index(chunks)
        app.state.collections.add(
            CollectionEntry(
                id=cid, label=cid, collection=f"ragstack_{cid}", model="test-model", dim=4,
                chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
                is_shared_surface=False,
                retriever=HybridRetriever(
                    vs, ti, _FakeEmbedder(), collection=f"ragstack_{cid}"
                ),
                vector_store=vs, text_index=ti, embedder=_FakeEmbedder(),
            )
        )
        added.append(cid)
    try:
        yield added
    finally:
        for cid in added:
            app.state.collections.remove(cid)


async def test_a_multi_collection_query_reports_the_fan_out_and_attributes_each_leg(
    client, two_collections, caplog
):
    """``colls=N`` plus a per-leg ``by_coll`` breakdown.

    This is the shape that makes mechanic 5 non-negotiable. Two legs run
    concurrently, so ``vector_ms`` is a *sum over two searches* and would be read
    as one slow search without the ``/2``; and "which collection was slow" is
    unanswerable without the tags. ``coll`` names the last member resolved — the
    documented last-wins — and ``colls`` is what says it is one of two.

    The tag is the PHYSICAL collection name (``ragstack_col_a``), not the
    registry id, because that is what ``HybridRetriever.collection`` holds and
    what an operator matches against a Qdrant collection listing.
    """
    caplog.set_level(logging.INFO)

    r = await client.post(
        "/v1/query",
        json={"query": "How does BM25 work?", "top_k": 5, "collections": ["col_a", "col_b"]},
    )
    assert r.status_code == 200, r.text

    line = _one_summary(caplog)
    assert line.colls == 2, "the fan-out width is not on the line"
    assert line.coll == "col_b", "coll should name the LAST member resolved"

    # Both legs attributed, and the aggregate says how many observations it is
    # the sum of — the guard against reading N legs as one call.
    assert "vector@ragstack_col_a=" in line.by_coll, line.by_coll
    assert "vector@ragstack_col_b=" in line.by_coll, line.by_coll
    assert line.vector_ms.endswith("/2"), line.vector_ms
    assert line.text_ms.endswith("/2"), line.text_ms


async def test_a_single_collection_query_reports_no_fan_out(client, caplog):
    """``colls`` is omitted, not printed as ``0``. A field that is always present
    and almost always zero is a column an operator learns to skip.

    Paired with the test above so neither can pass by the feature being absent:
    that one asserts 2, this one asserts the field is not there at all.
    """
    caplog.set_level(logging.INFO)
    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text
    assert not hasattr(_one_summary(caplog), "colls")
