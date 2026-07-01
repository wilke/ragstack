"""SegmentationCache: reproducible blocks computed once.

Guards that a re-ingest rebuilds identical chunk spans from the cache regardless
of embedding-backend jitter (the reproducible-blocks requirement), skips the
chunker on a hit, and recomputes cleanly when the segmentation config changes.
"""
from ragstack.ingestion.chunkers import _make_chunk
from ragstack.ingestion.segmentation_cache import SegmentationCache, config_fingerprint
from ragstack.models import Document


def _doc(content: str, doc_id: str = "d1") -> Document:
    return Document(id=doc_id, content=content, source="t")


def _chunker(spans):
    """A fake chunk_fn that emits chunks at the given (start,end) spans, and counts
    how many times it was invoked."""
    calls = {"n": 0}

    def fn(doc):
        calls["n"] += 1
        return [_make_chunk(doc, s, e) for s, e in spans]

    return fn, calls


def test_miss_computes_and_records_hit_rebuilds(tmp_path):
    doc = _doc("abcdefghij")
    fn, calls = _chunker([(0, 5), (5, 10)])
    cache = SegmentationCache(tmp_path / "seg.jsonl", "fp1")
    first = cache.get_or_compute(doc, fn)
    assert calls["n"] == 1 and cache.misses == 1 and cache.hits == 0

    # Second call for the same content: hit — chunker NOT invoked, identical ids.
    def _boom(_doc):
        raise AssertionError("chunk_fn must not run on a cache hit")

    second = cache.get_or_compute(doc, _boom)
    assert cache.hits == 1
    assert [c.id for c in second] == [c.id for c in first]
    assert [(c.start_char, c.end_char) for c in second] == [(0, 5), (5, 10)]


def test_hit_reproduces_blocks_despite_backend_jitter(tmp_path):
    """The point of the cache: a re-run whose (jittered) embeddings would place
    DIFFERENT boundaries still yields the original blocks from the cache."""
    path = tmp_path / "seg.jsonl"
    doc = _doc("abcdefghij")

    fn_v1, _ = _chunker([(0, 4), (4, 10)])
    ids_v1 = [c.id for c in SegmentationCache(path, "fp1").get_or_compute(doc, fn_v1)]

    # New process / instance, same cache; the chunker would now split differently.
    fn_v2, calls_v2 = _chunker([(0, 10)])
    cache2 = SegmentationCache(path, "fp1")
    ids_v2 = [c.id for c in cache2.get_or_compute(doc, fn_v2)]

    assert calls_v2["n"] == 0, "cached spans reused; recompute skipped"
    assert ids_v2 == ids_v1, "blocks reproduced from cache despite different segmentation"


def test_config_fingerprint_change_recomputes(tmp_path):
    path = tmp_path / "seg.jsonl"
    doc = _doc("abcdefghij")
    SegmentationCache(path, "fp1").get_or_compute(doc, _chunker([(0, 10)])[0])

    fn2, calls2 = _chunker([(0, 5), (5, 10)])
    cache = SegmentationCache(path, "fp2")  # different config → different key
    out = cache.get_or_compute(doc, fn2)
    assert calls2["n"] == 1 and cache.misses == 1
    assert [(c.start_char, c.end_char) for c in out] == [(0, 5), (5, 10)]


def test_persists_across_instances_and_skips_corrupt_lines(tmp_path):
    path = tmp_path / "seg.jsonl"
    doc = _doc("hello world")
    SegmentationCache(path, "fp1").get_or_compute(doc, _chunker([(0, 5), (5, 11)])[0])
    # inject a corrupt line; it must be skipped on reload, not crash
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("not json\n")
    cache = SegmentationCache(path, "fp1")
    out = cache.get_or_compute(doc, _chunker([(0, 11)])[0])
    assert cache.hits == 1
    assert [(c.start_char, c.end_char) for c in out] == [(0, 5), (5, 11)]


def test_config_fingerprint_is_order_independent():
    assert config_fingerprint(a=1, b=2) == config_fingerprint(b=2, a=1)
    assert config_fingerprint(a=1) != config_fingerprint(a=2)
