"""QdrantVectorStore batches upserts so one request never carries an oversized
payload (the #144 A/B benchmark showed a single large-shard upsert raises
ResponseHandlingException), and pipelines the batches when upsert_concurrency > 1."""
import asyncio

import pytest

from ragstack.models import Chunk
from ragstack.stores.qdrant import QdrantVectorStore


class _FakeClient:
    """Records each upsert's point count and tracks peak concurrency."""

    def __init__(self):
        self.batch_sizes = []
        self.all_ids = []
        self._inflight = 0
        self.peak_inflight = 0

    async def upsert(self, collection_name, points):
        self._inflight += 1
        self.peak_inflight = max(self.peak_inflight, self._inflight)
        await asyncio.sleep(0)  # yield so concurrent batches can overlap
        self.batch_sizes.append(len(points))
        self.all_ids.extend(p.id for p in points)
        await asyncio.sleep(0)
        self._inflight -= 1


def _store(**kw):
    s = QdrantVectorStore(url="http://x", collection="c", vector_size=3, **kw)
    s._client = _FakeClient()
    return s


def _chunks(n):
    return [Chunk(id=f"c{i}", doc_id="d", content="x", embedding=[1.0, 2.0, 3.0])
            for i in range(n)]


@pytest.mark.asyncio
async def test_upsert_splits_into_batches():
    s = _store(upsert_batch_size=256, upsert_concurrency=1)
    await s.upsert(_chunks(600))
    assert s._client.batch_sizes == [256, 256, 88]     # no single 600-point call
    assert len(s._client.all_ids) == 600               # nothing dropped


@pytest.mark.asyncio
async def test_small_upsert_is_one_batch():
    s = _store(upsert_batch_size=256)
    await s.upsert(_chunks(10))
    assert s._client.batch_sizes == [10]


@pytest.mark.asyncio
async def test_empty_upsert_makes_no_call():
    s = _store()
    await s.upsert([])
    assert s._client.batch_sizes == []


@pytest.mark.asyncio
async def test_concurrency_bounds_inflight_batches():
    s = _store(upsert_batch_size=100, upsert_concurrency=3)
    await s.upsert(_chunks(1000))               # 10 batches
    assert sum(s._client.batch_sizes) == 1000
    assert len(s._client.batch_sizes) == 10
    assert s._client.peak_inflight <= 3          # semaphore-bounded


@pytest.mark.asyncio
async def test_serial_default_never_overlaps():
    s = _store(upsert_batch_size=100, upsert_concurrency=1)
    await s.upsert(_chunks(500))
    assert s._client.peak_inflight == 1          # serial by default


# --- #177: batches are bounded by request BYTES as well as by count ----------
#
# Qdrant rejects a request body over service.max_request_size_mb (32 MiB by
# default) and rejects the WHOLE request. A few chunks carrying ~1 MB of
# mis-extracted metadata (a reference list copied into title/citation) used to
# push a 256-point batch over the limit, and every point in it was lost.

import httpx  # noqa: E402
from qdrant_client.http.api.points_api import jsonable_encoder  # noqa: E402
from qdrant_client.http.exceptions import (  # noqa: E402
    ResponseHandlingException,
    UnexpectedResponse,
)
from qdrant_client.models import PointsList, PointStruct  # noqa: E402

from ragstack.config import Settings  # noqa: E402
from ragstack.ingestion.retry import (  # noqa: E402
    _NON_TRANSIENT_ERROR_NAMES,
    is_transient_error,
)
from ragstack.stores.qdrant import (  # noqa: E402
    UPSERT_MAX_BYTES_DEFAULT,
    OversizedPointError,
    _point_bytes_estimate,
)

_QDRANT_DEFAULT_LIMIT = 32 * 1024 * 1024


def _wire_bytes(points):
    return len(jsonable_encoder(PointsList(points=points)).encode("utf-8"))


class _SizeLimitedClient(_FakeClient):
    """Rejects a request whose REST body exceeds ``limit``, the way Qdrant does:
    the whole request fails (a 4xx ``UnexpectedResponse``) and nothing in it is
    written. The body is measured exactly as qdrant-client's REST path builds
    it (``points_api._build_for_upsert_points``: ``jsonable_encoder`` over a
    ``PointsList``), UTF-8 encoded — the real wire bytes."""

    def __init__(self, limit=_QDRANT_DEFAULT_LIMIT):
        super().__init__()
        self.limit = limit
        self.body_bytes = []
        self.rejected = 0

    async def upsert(self, collection_name, points):
        size = _wire_bytes(points)
        if size > self.limit:
            self.rejected += 1
            raise UnexpectedResponse(
                status_code=400, reason_phrase="Bad Request",
                content=f"Payload error: request {size} > {self.limit}".encode(),
                headers=httpx.Headers())
        self.body_bytes.append(size)
        await super().upsert(collection_name, points)


def _poisoned_chunks(n, poison_every, poison_bytes, dim=3):
    out = []
    for i in range(n):
        meta = {"title": "A normal title", "authors": "Doe J"}
        if i % poison_every == 0:
            meta["citation"] = "r" * poison_bytes   # the mis-extracted ref list
        out.append(Chunk(id=f"c{i}", doc_id="d", content="x" * 1000,
                         embedding=[0.123456789] * dim, metadata=meta))
    return out


@pytest.mark.asyncio
async def test_metadata_heavy_points_no_longer_sink_their_batch():
    """The #177 failure at its real scale: 256 chunks, 40 of them carrying ~1 MB
    of metadata (40 MB total) against Qdrant's default 32 MiB limit. Count-only
    batching sends one 256-point request, which the server rejects whole."""
    s = _store()          # all defaults: batch 256, default byte cap
    s._client = _SizeLimitedClient()
    chunks = _poisoned_chunks(256, poison_every=6, poison_bytes=1_000_000)
    await s.upsert(chunks)
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 256                 # every point sent
    assert len(set(s._client.all_ids)) == 256            # none twice
    assert max(s._client.body_bytes) <= _QDRANT_DEFAULT_LIMIT
    assert len(s._client.batch_sizes) > 1


@pytest.mark.asyncio
async def test_byte_cap_bounds_every_request_and_drops_nothing():
    """A small configured cap: no request's real body exceeds it."""
    cap = 1024 * 1024
    s = _store(upsert_max_bytes=cap)
    s._client = _SizeLimitedClient(limit=cap)
    await s.upsert(_poisoned_chunks(256, poison_every=50, poison_bytes=300_000))
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 256
    assert len(set(s._client.all_ids)) == 256
    assert max(s._client.body_bytes) <= cap


@pytest.mark.asyncio
async def test_byte_cap_applies_on_the_concurrent_path_too():
    cap = 1024 * 1024
    s = _store(upsert_max_bytes=cap, upsert_concurrency=4)
    s._client = _SizeLimitedClient(limit=cap)
    await s.upsert(_poisoned_chunks(600, poison_every=40, poison_bytes=300_000))
    assert s._client.rejected == 0
    assert len(s._client.all_ids) == 600
    assert max(s._client.body_bytes) <= cap


@pytest.mark.asyncio
async def test_normal_4096d_data_keeps_the_same_request_count():
    """Ordinary chunks (2 KB content, small metadata, 4096-d vectors) must batch
    exactly as before: the byte cap only bites on outliers. The real body of a
    full 256-point batch also stays under Qdrant's default limit."""
    import random
    rng = random.Random(0)
    chunks = [
        Chunk(id=f"c{i}", doc_id=f"d{i // 40}", content="w" * 2000,
              embedding=[rng.uniform(-0.05, 0.05) for _ in range(4096)],
              metadata={"title": "A normal title", "authors": "Doe J, Roe R",
                        "year": 2021, "source": "pmc"})
        for i in range(600)
    ]
    s = QdrantVectorStore(url="http://x", collection="c", vector_size=4096)
    s._client = _SizeLimitedClient()
    await s.upsert(chunks)
    assert s._client.batch_sizes == [256, 256, 88]
    assert s._client.rejected == 0


@pytest.mark.asyncio
async def test_a_point_over_the_cap_is_sent_alone():
    cap = 64 * 1024
    s = _store(upsert_max_bytes=cap)
    chunks = _chunks(10)
    chunks[4].metadata["citation"] = "r" * (cap * 2)
    await s.upsert(chunks)
    # The giant point travels by itself; its neighbours are not held hostage.
    assert s._client.batch_sizes == [4, 1, 5]
    assert len(s._client.all_ids) == 10


@pytest.mark.asyncio
async def test_rejected_oversized_point_error_names_the_chunk():
    cap = 64 * 1024
    s = _store(upsert_max_bytes=cap)
    s._client = _SizeLimitedClient(limit=cap)   # the server rejects it too
    chunks = [Chunk(id=f"c{i}", doc_id="paper-7", content="x",
                    embedding=[1.0, 2.0, 3.0]) for i in range(3)]
    chunks[1].metadata["citation"] = "r" * (cap * 2)
    with pytest.raises(OversizedPointError) as ei:
        await s.upsert(chunks)
    msg = str(ei.value)
    assert "chunk_id='c1'" in msg and "doc_id='paper-7'" in msg
    assert "'citation'" in msg and "estimated_bytes=" in msg
    assert isinstance(ei.value.__cause__, UnexpectedResponse)   # store error chained
    assert len(msg) < 2000                                 # payload not echoed
    assert s._client.all_ids and len(s._client.all_ids) == 1   # c0 was sent


@pytest.mark.asyncio
async def test_ordinary_batch_failure_is_not_rewrapped():
    """Only a lone oversized point gets the attributing error; any other
    failure propagates as the store raised it (retry classification unchanged)."""

    class _Boom(_FakeClient):
        async def upsert(self, collection_name, points):
            raise ConnectionError("down")

    s = _store()
    s._client = _Boom()
    with pytest.raises(ConnectionError):
        await s.upsert(_chunks(3))


def test_setting_default_matches_store_default():
    # The field default, not Settings(): an operator env var must not decide this.
    assert Settings.model_fields["qdrant_upsert_max_bytes"].default == UPSERT_MAX_BYTES_DEFAULT


# --- review round: worst-case floats, non-ASCII, error classification -------


def test_estimate_bounds_worst_case_floats_and_non_ascii_payloads():
    """The per-point estimate must never undercount the real wire bytes.
    pydantic-core writes 1e-5 <= |x| < 1e16 in plain decimal, so a float64
    just above 1e-5 is the longest form: ``-0.000012345678901234568`` (24
    chars + comma). A 24-byte/dim bound undercounted exactly these."""
    import random
    rng = random.Random(1)
    worst = [-(1e-5 + rng.random() * 1e-6) for _ in range(4096)]
    payloads = [
        {"chunk_id": "c", "doc_id": "d", "content": "w" * 2000},
        {"chunk_id": "c", "doc_id": "d", "title": "漢字のタイトル" * 20_000},
        {"chunk_id": "c", "doc_id": "d", "title": "😀🧬" * 20_000},
        {"chunk_id": "c", "doc_id": "d", "scores": worst},
    ]
    for payload in payloads:
        p = PointStruct(id="00000000-0000-0000-0000-000000000001",
                        vector=worst, payload=payload)
        assert _point_bytes_estimate(p) >= _wire_bytes([p]), list(payload)


@pytest.mark.asyncio
async def test_worst_case_floats_with_non_ascii_payloads_never_exceed_the_cap():
    """End to end at a tight cap: 4096-d vectors of float64 values near 1e-5
    and CJK/emoji metadata. The server enforces the cap on the real UTF-8
    body; no request may be rejected and every point must arrive."""
    import random
    rng = random.Random(2)
    cap = 1024 * 1024
    chunks = [
        Chunk(id=f"c{i}", doc_id="d", content="内容" * 200,
              embedding=[-(1e-5 + rng.random() * 1e-6) for _ in range(4096)],
              metadata={"title": ("漢字" if i % 2 else "😀") * 3000})
        for i in range(60)
    ]
    s = _store(upsert_max_bytes=cap)
    s._client = _SizeLimitedClient(limit=cap)
    await s.upsert(chunks)
    assert s._client.rejected == 0
    assert len(set(s._client.all_ids)) == 60
    assert max(s._client.body_bytes) <= cap


def test_full_normal_4096d_batch_estimate_fits_the_default_cap():
    """256 ordinary 4096-d points with 2 KB content, at the worst float form:
    the estimate stays under 28 MiB (one request, as before #177) and the real
    body under Qdrant's 32 MiB."""
    worst = [-0.000012345678901234568] * 4096
    pts = [PointStruct(id=f"00000000-0000-0000-0000-{i:012d}", vector=worst,
                       payload={"chunk_id": f"c{i}", "doc_id": "d", "content": "w" * 2000,
                                "start_char": 0, "end_char": 2000, "title": "t"})
           for i in range(256)]
    est = sum(_point_bytes_estimate(p) for p in pts)
    real = _wire_bytes(pts)
    assert real <= est <= UPSERT_MAX_BYTES_DEFAULT
    assert real <= _QDRANT_DEFAULT_LIMIT


@pytest.mark.parametrize("exc", [
    ResponseHandlingException(httpx.ReadTimeout("timed out")),
    UnexpectedResponse(status_code=503, reason_phrase="Service Unavailable",
                       content=b"", headers=httpx.Headers()),
], ids=["timeout", "5xx"])
@pytest.mark.asyncio
async def test_lone_oversized_point_non_4xx_failure_is_reraised_unchanged(exc):
    """Only a 4xx means the store rejected the point. A timeout or 5xx on a big
    point is not evidence against it and must keep its transient type."""

    class _Fails(_FakeClient):
        async def upsert(self, collection_name, points):
            raise exc

    cap = 64 * 1024
    s = _store(upsert_max_bytes=cap)
    s._client = _Fails()
    chunks = _chunks(1)
    chunks[0].metadata["citation"] = "r" * (cap * 2)
    with pytest.raises(type(exc)) as ei:
        await s.upsert(chunks)
    assert ei.value is exc


def _oversized_error(digits="503"):
    cause = UnexpectedResponse(status_code=400, reason_phrase="Bad Request",
                               content=b"Payload error", headers=httpx.Headers())
    try:
        raise OversizedPointError(
            f"qdrant rejected an oversized point sent alone: chunk_id='c1' "
            f"estimated_bytes=1{digits}000 (cap 29360128)") from cause
    except OversizedPointError as e:
        return e


def test_oversized_point_error_is_not_transient():
    """Retrying sends the same body and gets the same 4xx. Without the guard the
    chained UnexpectedResponse (a transient name) and the digits 502/503/504 in
    the byte counts would each make it look transient."""
    for digits in ("502", "503", "504", "000"):
        assert not is_transient_error(_oversized_error(digits))


def test_oversized_point_error_wrapped_by_a_caller_is_still_not_transient():
    inner = _oversized_error()
    try:
        raise RuntimeError("ingest batch failed: timeout budget 503") from inner
    except RuntimeError as outer:
        assert not is_transient_error(outer)


def test_retry_names_track_the_error_class():
    # retry.py matches by NAME to stay free of the stores import; pin the pair.
    assert OversizedPointError.__name__ in _NON_TRANSIENT_ERROR_NAMES


def test_ordinary_5xx_unexpected_response_is_still_transient():
    """The guard is specific: a bare 5xx UnexpectedResponse stays transient."""
    e = UnexpectedResponse(status_code=503, reason_phrase="Service Unavailable",
                           content=b"", headers=httpx.Headers())
    assert is_transient_error(e)


def test_oversized_point_error_only_in_context_is_still_not_transient():
    """``raise X from other`` while handling the poison error: the poison sits
    only in __context__ (__cause__ is ``other``), and must still be found."""
    poison = _oversized_error()
    try:
        try:
            raise poison
        except OversizedPointError:
            raise RuntimeError("batch failed") from ConnectionError("503 reset")
    except RuntimeError as outer:
        assert outer.__cause__ is not None and outer.__context__ is poison
        assert not is_transient_error(outer)


def test_settings_refuse_a_non_positive_byte_cap():
    from pydantic import ValidationError
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            Settings(qdrant_upsert_max_bytes=bad)
