"""Tests for the store-to-store collection copy (scripts/copy_collection.py).

Entirely offline: the stores are fakes, so what is pinned here is the logic that
decides *what* gets written — not that Qdrant or ES work.

The properties that matter operationally, and the failure each one guards:

* the destination geometry builder — a wrong ``datatype``/``quantization``
  structure is only discovered after 53 GB has been copied at the wrong size;
* ``wait=False`` on every batch but the last — a count check taken straight after
  a copy that never waited is a lie;
* checkpoint/resume — a resume that starts from zero wastes a day, and a resume
  that adopts *another* copy's offset skips most of the collection and looks
  finished;
* named vs unnamed vectors — dropping the name produces a destination that
  answers every query with nothing;
* the comparator's cosine tolerance — float16 rounds, so an equality test would
  fail every honest copy while a missing point must still be caught.
"""
from __future__ import annotations

import json
import random
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from qdrant_client.models import Datatype, Distance, ScalarType, VectorParams

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import copy_collection as cc  # noqa: E402

# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


@dataclass
class FakePoint:
    id: Any
    vector: Any = None
    payload: dict[str, Any] | None = None


def _order(value: Any) -> tuple[int, int, str]:
    """Qdrant's own scroll order: integer ids sort numerically, uuid ids sort
    lexically, and the two kinds never mix in one collection."""
    return (0, value, "") if isinstance(value, int) else (1, 0, str(value))


@dataclass
class FakeQdrant:
    """Scroll/upsert/retrieve over an in-memory list, ordered by id."""

    points: list[FakePoint] = field(default_factory=list)
    collections: dict[str, dict[str, Any]] = field(default_factory=dict)
    upserts: list[tuple[str, list[Any], bool]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)
    indexes: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    scroll_calls: int = 0

    # --- reads ---------------------------------------------------------- #
    def scroll(self, collection_name, limit, offset=None, with_payload=True, with_vectors=True):
        self.scroll_calls += 1
        ordered = sorted(self.points, key=lambda p: _order(p.id))
        start = 0
        if offset is not None:
            start = next(
                (i for i, p in enumerate(ordered) if _order(p.id) >= _order(offset)), len(ordered)
            )
        page = ordered[start : start + limit]
        nxt = ordered[start + limit].id if start + limit < len(ordered) else None
        out = [
            FakePoint(
                id=p.id,
                vector=p.vector if with_vectors else None,
                payload=p.payload if with_payload else None,
            )
            for p in page
        ]
        return out, nxt

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=True):
        wanted = set(ids)
        return [p for p in self.points if p.id in wanted]

    def get_collections(self):
        return {"collections": [{"name": n} for n in self.collections]}

    def get_collection(self, name):
        return self.collections[name]

    # --- writes --------------------------------------------------------- #
    def upsert(self, collection_name, points, wait=False):
        self.upserts.append((collection_name, list(points), wait))
        self.points.extend(FakePoint(p.id, p.vector, p.payload) for p in points)

    def create_collection(self, collection_name, **kw):
        self.created.append({"collection_name": collection_name, **kw})
        self.collections[collection_name] = _info(4096, "Cosine", points=0)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes.append((collection_name, field_name))

    def delete_collection(self, collection_name):  # pragma: no cover — must never be called
        self.deleted.append(collection_name)


def _info(size, distance, points=0, named=None, status="green"):
    """A collection info in the raw-JSON shape (``_get`` reads dicts and models alike)."""
    vectors: Any = (
        {"size": size, "distance": distance}
        if named is None
        else {n: {"size": s, "distance": d} for n, (s, d) in named.items()}
    )
    return {
        "status": status,
        "points_count": points,
        "config": {"params": {"vectors": vectors}},
    }


class FakeIndices:
    def __init__(self, existing: set[str]):
        self.existing = existing
        self.refreshed: list[str] = []

    def exists(self, index):
        return index in self.existing

    def refresh(self, index):
        self.refreshed.append(index)


class FakeEs:
    """Just enough of the ES client for the text leg: PIT + search_after + count."""

    def __init__(self, docs=None, existing=("src", "dst")):
        self.docs = list(docs or [])
        self.indices = FakeIndices(set(existing))
        self.pits: list[str] = []
        self.closed: list[str] = []
        self.searches: list[dict[str, Any]] = []

    def open_point_in_time(self, index, keep_alive):
        self.pits.append(index)
        return {"id": f"pit-{len(self.pits)}"}

    def close_point_in_time(self, id):  # noqa: A002 — the client's own parameter name
        self.closed.append(id)

    def search(self, **kw):
        self.searches.append(kw)
        size = kw["size"]
        after = kw.get("search_after")
        start = 0
        if after:
            start = next(
                (i + 1 for i, d in enumerate(self.docs) if d["sort"] == list(after)), len(self.docs)
            )
        page = self.docs[start : start + size]
        return {"hits": {"hits": page}, "pit_id": kw["pit"]["id"]}

    def count(self, index):
        return {"count": len(self.docs)}

    def mget(self, index, ids):
        by_id = {d["_id"]: d for d in self.docs}
        return {
            "docs": [
                {"_id": i, "found": i in by_id, "_source": by_id.get(i, {}).get("_source")}
                for i in ids
            ]
        }


def _doc(i: int, body: str = "body") -> dict[str, Any]:
    return {"_id": f"c{i}", "_source": {"content": f"{body} {i}"}, "sort": [i]}


# --------------------------------------------------------------------------- #
# destination configuration
# --------------------------------------------------------------------------- #


def test_float16_int8_config_is_exactly_the_planned_structure():
    cfg = cc.build_destination_config({cc.UNNAMED: (4096, "Cosine")})
    assert cfg.vectors_config == VectorParams(
        size=4096, distance=Distance.COSINE, datatype=Datatype.FLOAT16, on_disk=True
    )
    # Down to the field values: this is the structure the plan specifies, and a
    # wrong quantile or a quantization held on disk is not visibly different
    # until search latency or recall is measured on a 3M-point collection.
    quant = cfg.quantization_config.scalar
    assert quant.type == ScalarType.INT8
    assert quant.quantile == 0.99
    assert quant.always_ram is True
    assert cfg.on_disk_payload is True
    # and it serialises to the JSON Qdrant documents for datatype/quantization
    assert json.loads(cfg.vectors_config.model_dump_json())["datatype"] == "float16"
    assert json.loads(cfg.quantization_config.model_dump_json())["scalar"] == {
        "type": "int8",
        "quantile": 0.99,
        "always_ram": True,
    }


def test_config_honours_float32_no_quantization_and_no_on_disk():
    cfg = cc.build_destination_config(
        {cc.UNNAMED: (768, "Dot")}, datatype="float32", quantization="none", on_disk=False
    )
    assert cfg.vectors_config == VectorParams(
        size=768, distance=Distance.DOT, datatype=Datatype.FLOAT32, on_disk=False
    )
    assert cfg.quantization_config is None


def test_named_vectors_produce_a_mapping_not_a_single_params():
    cfg = cc.build_destination_config({"dense": (1024, "Cosine"), "sparse_ish": (64, "Euclid")})
    assert isinstance(cfg.vectors_config, dict)
    assert set(cfg.vectors_config) == {"dense", "sparse_ish"}
    assert cfg.vectors_config["dense"].size == 1024
    assert cfg.vectors_config["sparse_ish"].distance == Distance.EUCLID
    # every named vector carries the reduced datatype too
    assert all(v.datatype == Datatype.FLOAT16 for v in cfg.vectors_config.values())


def test_config_as_json_is_what_the_dry_run_shows_an_operator():
    cfg = cc.build_destination_config({cc.UNNAMED: (4096, "Cosine")})
    assert cc.config_as_json(cfg) == {
        "vectors": {
            "size": 4096,
            "distance": "Cosine",
            "datatype": "float16",
            "on_disk": True,
        },
        "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
        "on_disk_payload": True,
        "payload_indexes": ["tenant_id", "doc_id"],
    }
    named = cc.build_destination_config({"dense": (8, "Cosine")})
    assert set(cc.config_as_json(named)["vectors"]) == {"dense"}


def test_unsupported_quantization_is_refused():
    with pytest.raises(cc.CopyError):
        cc.build_destination_config({cc.UNNAMED: (4, "Cosine")}, quantization="binary")


# --------------------------------------------------------------------------- #
# reading the source geometry
# --------------------------------------------------------------------------- #


def test_source_vector_spec_reads_unnamed_and_named():
    assert cc.source_vector_spec(_info(4096, "Cosine")) == {cc.UNNAMED: (4096, "Cosine")}
    named = _info(0, "", named={"a": (128, "Dot"), "b": (256, "Cosine")})
    assert cc.source_vector_spec(named) == {"a": (128, "Dot"), "b": (256, "Cosine")}


def test_source_vector_spec_accepts_model_objects_too():
    """The real client returns pydantic models, not dicts."""

    class _P:
        vectors = VectorParams(size=4096, distance=Distance.COSINE)

    class _C:
        params = _P()

    class _Info:
        config = _C()

    assert cc.source_vector_spec(_Info()) == {cc.UNNAMED: (4096, "Cosine")}


def test_source_vector_spec_rejects_a_collection_without_vectors():
    with pytest.raises(cc.CopyError):
        cc.source_vector_spec({"config": {"params": {}}})


@pytest.mark.parametrize(
    "dst,expect",
    [
        ({cc.UNNAMED: (4096, "Cosine")}, 0),
        ({cc.UNNAMED: (768, "Cosine")}, 1),
        ({cc.UNNAMED: (4096, "Dot")}, 1),
        ({"dense": (4096, "Cosine")}, 1),
    ],
)
def test_spec_mismatch_flags_only_real_disagreement(dst, expect):
    assert len(cc.spec_mismatch({cc.UNNAMED: (4096, "Cosine")}, dst)) == expect


# --------------------------------------------------------------------------- #
# destination setup — never destructive
# --------------------------------------------------------------------------- #


def _ensure(client, **kw):
    return cc.ensure_destination(
        client,
        "dst",
        {cc.UNNAMED: (4096, "Cosine")},
        create=kw.pop("create", True),
        datatype=kw.pop("datatype", "float16"),
        quantization=kw.pop("quantization", "int8"),
        on_disk=kw.pop("on_disk", True),
        **kw,
    )


def test_absent_destination_is_created_with_payload_indexes():
    client = FakeQdrant()
    assert _ensure(client) == "created"
    assert client.created[0]["collection_name"] == "dst"
    assert client.created[0]["on_disk_payload"] is True
    assert [f for _, f in client.indexes] == ["tenant_id", "doc_id"]
    assert client.deleted == []


def test_existing_matching_destination_is_kept_never_recreated():
    client = FakeQdrant(collections={"dst": _info(4096, "Cosine", points=10)})
    assert _ensure(client) == "existing"
    assert client.created == []
    assert client.deleted == []
    assert [f for _, f in client.indexes] == ["tenant_id", "doc_id"]


def test_existing_mismatched_destination_is_a_hard_error():
    client = FakeQdrant(collections={"dst": _info(768, "Cosine", points=10)})
    with pytest.raises(cc.CopyError, match="does not match the source"):
        _ensure(client)
    assert client.deleted == []
    assert client.created == []


def test_absent_destination_without_create_dst_is_an_error():
    client = FakeQdrant()
    with pytest.raises(cc.CopyError, match="--create-dst"):
        _ensure(client, create=False)


def test_payload_index_creation_is_best_effort():
    class _Raises(FakeQdrant):
        def create_payload_index(self, collection_name, field_name, field_schema):
            raise RuntimeError("already exists")

    client = _Raises(collections={"dst": _info(4096, "Cosine")})
    assert _ensure(client) == "existing"  # no exception escapes


# --------------------------------------------------------------------------- #
# the vector leg: batching, wait, checkpointing
# --------------------------------------------------------------------------- #


def _source(n, vector=None, ids=None):
    """A source of n points. Integer ids by default (readable offsets); ``ids``
    supplies uuid ids where the uuid sampling path is what is under test."""
    keys = list(ids) if ids is not None else list(range(n))
    return FakeQdrant(
        points=[
            FakePoint(
                id=key,
                vector=vector(i) if vector else [float(i), 1.0],
                payload={"chunk_id": f"c{i}", "tenant_id": "public", "doc_id": f"d{i}"},
            )
            for i, key in enumerate(keys)
        ]
    )


def _uuid_ids(n, seed=99):
    rnd = random.Random(seed)
    return [str(uuid.UUID(int=rnd.getrandbits(128), version=4)) for _ in range(n)]


def test_copy_vectors_batches_and_only_the_last_batch_waits(tmp_path):
    src, dst = _source(10), FakeQdrant()
    cp = cc.Checkpoint()
    slept: list[float] = []
    copied = cc.copy_vectors(
        src, dst, "src", "dst", batch=4, pause=0.5, cp=cp, sleep=slept.append
    )
    assert copied == 10
    assert [len(points) for _, points, _ in dst.upserts] == [4, 4, 2]
    assert [wait for _, _, wait in dst.upserts] == [False, False, True]
    # one pause per non-final batch, so the source is not hammered
    assert slept == [0.5, 0.5]
    assert cp.qdrant_done is True and cp.qdrant_copied == 10


def test_copy_vectors_copies_ids_and_payload_verbatim():
    src, dst = _source(3), FakeQdrant()
    cc.copy_vectors(src, dst, "src", "dst", batch=2, pause=0, cp=cc.Checkpoint())
    written = [p for _, points, _ in dst.upserts for p in points]
    assert [p.id for p in written] == [0, 1, 2]
    # tenant_id crosses untouched — the copy must not re-stamp ownership
    assert all(p.payload["tenant_id"] == "public" for p in written)
    assert written[1].payload == {"chunk_id": "c1", "tenant_id": "public", "doc_id": "d1"}


def test_copy_vectors_handles_named_vectors():
    src = _source(2, vector=lambda i: {"dense": [float(i)], "title": [1.0]})
    dst = FakeQdrant()
    cc.copy_vectors(src, dst, "src", "dst", batch=2, pause=0, cp=cc.Checkpoint())
    written = [p for _, points, _ in dst.upserts for p in points]
    assert written[0].vector == {"dense": [0.0], "title": [1.0]}


def test_copy_vectors_refuses_a_point_without_a_vector():
    src = FakeQdrant(points=[FakePoint(id="a", vector=None, payload={})])
    with pytest.raises(cc.CopyError, match="without a vector"):
        cc.copy_vectors(src, FakeQdrant(), "src", "dst", batch=2, pause=0, cp=cc.Checkpoint())


def test_checkpoint_is_written_after_every_batch(tmp_path, monkeypatch):
    path = str(tmp_path / "ckpt.json")
    src, dst = _source(10), FakeQdrant()
    seen: list[int] = []
    real_save = cc.save_checkpoint

    def spy(p, cp):
        real_save(p, cp)
        seen.append(json.loads(Path(path).read_text())["qdrant_copied"])

    monkeypatch.setattr(cc, "save_checkpoint", spy)
    cc.copy_vectors(
        src, dst, "src", "dst", batch=4, pause=0, cp=cc.Checkpoint(), checkpoint_path=path
    )
    assert seen == [4, 8, 10]
    assert not (tmp_path / "ckpt.json.tmp").exists()  # the temp file is renamed, not left behind


def test_resume_starts_from_the_checkpoint_offset_and_keeps_the_count():
    src, dst = _source(10), FakeQdrant()
    cp = cc.Checkpoint(qdrant_offset=4, qdrant_copied=4)
    copied = cc.copy_vectors(src, dst, "src", "dst", batch=4, pause=0, cp=cp)
    assert copied == 10
    written = [p.id for _, points, _ in dst.upserts for p in points]
    assert written == [4, 5, 6, 7, 8, 9]


def test_a_completed_leg_is_not_recopied():
    src, dst = _source(10), FakeQdrant()
    cp = cc.Checkpoint(qdrant_done=True, qdrant_copied=10)
    assert cc.copy_vectors(src, dst, "src", "dst", batch=4, pause=0, cp=cp) == 10
    assert dst.upserts == []
    assert src.scroll_calls == 0


# --------------------------------------------------------------------------- #
# checkpoint file handling
# --------------------------------------------------------------------------- #


def test_checkpoint_round_trips(tmp_path):
    path = str(tmp_path / "c.json")
    key = {"src_collection": "a", "dst_collection": "b"}
    cp = cc.Checkpoint(key=key, qdrant_offset="0100", qdrant_copied=100, es_search_after=[7])
    cc.save_checkpoint(path, cp)
    back = cc.load_checkpoint(path, key)
    assert back.qdrant_offset == "0100"
    assert back.qdrant_copied == 100
    assert back.es_search_after == [7]


def test_missing_checkpoint_starts_clean(tmp_path):
    cp = cc.load_checkpoint(str(tmp_path / "nope.json"), {"a": 1})
    assert cp.qdrant_copied == 0 and cp.qdrant_offset is None


def test_restart_ignores_an_existing_checkpoint(tmp_path):
    path = str(tmp_path / "c.json")
    key = {"src_collection": "a"}
    cc.save_checkpoint(path, cc.Checkpoint(key=key, qdrant_copied=999, qdrant_offset="x"))
    cp = cc.load_checkpoint(path, key, restart=True)
    assert cp.qdrant_copied == 0 and cp.qdrant_offset is None


def test_a_checkpoint_from_another_copy_is_refused_not_adopted(tmp_path):
    path = str(tmp_path / "c.json")
    cc.save_checkpoint(path, cc.Checkpoint(key={"dst_collection": "other"}, qdrant_copied=5))
    with pytest.raises(cc.CopyError, match="different copy"):
        cc.load_checkpoint(path, {"dst_collection": "mine"})


def test_a_corrupt_checkpoint_is_refused(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    with pytest.raises(cc.CopyError, match="unreadable"):
        cc.load_checkpoint(str(path), {"a": 1})


# --------------------------------------------------------------------------- #
# the text leg
# --------------------------------------------------------------------------- #


def test_copy_text_pages_with_search_after_and_indexes_verbatim():
    src = FakeEs(docs=[_doc(i) for i in range(5)])
    dst = FakeEs(docs=[])
    sent: list[list[dict[str, Any]]] = []
    bulk_kw: list[dict[str, Any]] = []

    def bulk(client, actions, **kw):
        sent.append(actions)
        bulk_kw.append(kw)
        return len(actions), []

    cp = cc.Checkpoint()
    copied = cc.copy_text(
        src, dst, "src", "dst", size=2, pause=0, cp=cp, bulk_fn=bulk, sleep=lambda _s: None
    )
    assert copied == 5
    assert [len(a) for a in sent] == [2, 2, 1]
    flat = [a for batch in sent for a in batch]
    assert [a["_id"] for a in flat] == ["c0", "c1", "c2", "c3", "c4"]
    assert flat[0]["_source"] == {"content": "body 0"}
    # op_type index, so a resume overwrites instead of raising version_conflict
    assert {a["_op_type"] for a in flat} == {"index"}
    # _type has been gone since ES 8; sending it is a 400
    assert all("_type" not in a for a in flat)
    assert all(kw["refresh"] is False for kw in bulk_kw)
    # sorted on _shard_doc under a PIT, and the PIT is closed again
    assert src.searches[0]["sort"] == [{"_shard_doc": "asc"}]
    assert "index" not in src.searches[0]
    assert src.closed == ["pit-1"]
    # exactly one refresh, at the end
    assert dst.indices.refreshed == ["dst"]
    assert cp.es_done is True and cp.es_search_after == [4]


def test_copy_text_resumes_from_the_stored_sort_key():
    src = FakeEs(docs=[_doc(i) for i in range(5)])
    sent: list[dict[str, Any]] = []
    cp = cc.Checkpoint(es_search_after=[2], es_copied=3)
    copied = cc.copy_text(
        src,
        FakeEs(docs=[]),
        "src",
        "dst",
        size=2,
        pause=0,
        cp=cp,
        bulk_fn=lambda c, actions, **kw: (sent.extend(actions), (len(actions), []))[1],
        sleep=lambda _s: None,
    )
    assert [a["_id"] for a in sent] == ["c3", "c4"]
    assert copied == 5


def test_completed_text_leg_is_not_recopied():
    src = FakeEs(docs=[_doc(i) for i in range(5)])
    cp = cc.Checkpoint(es_done=True, es_copied=5)
    assert (
        cc.copy_text(src, FakeEs(), "src", "dst", size=2, pause=0, cp=cp, bulk_fn=None) == 5
    )
    assert src.pits == []


# --------------------------------------------------------------------------- #
# the verification comparator
# --------------------------------------------------------------------------- #


def test_cosine_basics():
    assert cc.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cc.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cc.cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)  # scale-free
    assert cc.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cc.cosine([1.0], [1.0, 2.0]) == 0.0  # length disagreement is not a match
    assert cc.cosine(None, [1.0]) == 0.0


def test_float16_rounding_passes_but_a_wrong_vector_fails():
    import struct

    def f16(x):
        return struct.unpack("e", struct.pack("e", x))[0]

    src_vec = [0.123456, -0.987654, 0.5, 0.0009]
    rounded = [f16(x) for x in src_vec]
    assert rounded != src_vec  # the destination really does store something else
    src = FakePoint("p", src_vec, {"a": 1})
    assert cc.compare_point("p", src, FakePoint("p", rounded, {"a": 1})) == []
    wrong = cc.compare_point("p", src, FakePoint("p", [0.9, 0.1, -0.2, 0.3], {"a": 1}))
    assert len(wrong) == 1 and "cosine" in wrong[0]


def test_comparator_catches_payload_difference_and_a_missing_point():
    src = FakePoint("p", [1.0, 0.0], {"tenant_id": "public", "n": 1})
    assert "payload differs" in cc.compare_point(
        "p", src, FakePoint("p", [1.0, 0.0], {"tenant_id": "other", "n": 1})
    )[0]
    assert "missing from the destination" in cc.compare_point("p", src, None)[0]


def test_payload_equality_is_json_shaped_not_type_pedantic():
    assert cc.payloads_equal({"a": [1, 2]}, {"a": (1, 2)})
    assert cc.payloads_equal({}, None)
    assert not cc.payloads_equal({"a": 1}, {"a": "1"})


def test_comparator_compares_named_vectors_by_name():
    src = FakePoint("p", {"dense": [1.0, 0.0], "title": [0.0, 1.0]}, {})
    assert cc.compare_point("p", src, FakePoint("p", {"dense": [1.0, 0.0], "title": [0.0, 1.0]}, {})) == []
    swapped = cc.compare_point(
        "p", src, FakePoint("p", {"dense": [0.0, 1.0], "title": [0.0, 1.0]}, {})
    )
    assert len(swapped) == 1 and "dense" in swapped[0]
    renamed = cc.compare_point("p", src, FakePoint("p", {"dense": [1.0, 0.0]}, {}))
    assert "vector names differ" in renamed[0]


# --------------------------------------------------------------------------- #
# verification end to end (still offline)
# --------------------------------------------------------------------------- #


def test_verify_vectors_reports_a_count_gap_and_collects_chunk_ids():
    src = _source(6)
    src.collections = {"src": _info(4096, "Cosine", points=6)}
    dst = FakeQdrant(points=list(src.points), collections={"dst": _info(4096, "Cosine", points=5)})
    report, problems = cc.verify_vectors(
        src, dst, "src", "dst", spot_check=3, rng=random.Random(7)
    )
    assert report["source_points"] == 6 and report["destination_points"] == 5
    assert any("points_count" in p for p in problems)
    assert report["spot_checked"] == 3
    assert all(c.startswith("c") for c in report["sample_chunk_ids"])


def test_verify_vectors_is_clean_when_the_stores_agree():
    src = _source(6)
    src.collections = {"src": _info(4096, "Cosine", points=6)}
    dst = FakeQdrant(points=list(src.points), collections={"dst": _info(4096, "Cosine", points=6)})
    _, problems = cc.verify_vectors(src, dst, "src", "dst", spot_check=3, rng=random.Random(7))
    assert problems == []


def test_verify_text_compares_counts_and_sources():
    src = FakeEs(docs=[_doc(i) for i in range(4)])
    dst = FakeEs(docs=[_doc(i) for i in range(3)] + [_doc(3, body="tampered")])
    report, problems = cc.verify_text(src, dst, "src", "dst", chunk_ids=["c0", "c3"])
    assert report["source_docs"] == 4 and report["destination_docs"] == 4
    assert len(problems) == 1 and "_source differs" in problems[0]
    assert report["spot_checked"] == 2


def test_sample_point_ids_is_bounded_and_deterministic_under_a_seed():
    src = _source(50)
    a = cc.sample_point_ids(src, "src", 5, points_count=50, rng=random.Random(1))
    b = cc.sample_point_ids(src, "src", 5, points_count=50, rng=random.Random(1))
    assert a == b
    assert len(a) == 5 and len(set(a)) == 5
    assert len(set(a) - {p.id for p in src.points}) == 0
    assert cc.sample_point_ids(FakeQdrant(), "src", 5, rng=random.Random(1)) == []


def test_sample_point_ids_works_on_a_uuid_keyed_collection():
    """The real source is uuid-keyed (``_point_id`` is a uuid5), where a random
    position means a random uuid offset rather than a random integer."""
    src = _source(50, ids=_uuid_ids(50))
    sampled = cc.sample_point_ids(src, "src", 5, points_count=50, rng=random.Random(3))
    assert len(sampled) == 5 and len(set(sampled)) == 5
    assert set(sampled) <= {p.id for p in src.points}


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_help_renders(capsys):
    with pytest.raises(SystemExit) as exc:
        cc.parse_args(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_defaults_match_the_documented_contract():
    args = cc.parse_args(
        ["--src-collection", "a", "--dst-qdrant", "http://x:6333", "--dst-collection", "b"]
    )
    assert args.batch == 256
    assert args.pause == 0.05
    assert args.vector_datatype == "float16"
    assert args.quantization == "int8"
    assert args.on_disk is True
    assert args.spot_check == 20
    assert args.min_cosine == 0.99
    assert cc.parse_args(
        ["--src-collection", "a", "--dst-qdrant", "u", "--dst-collection", "b", "--no-on-disk"]
    ).on_disk is False


def test_a_half_specified_es_leg_is_refused():
    args = cc.parse_args(
        [
            "--src-collection", "a",
            "--dst-qdrant", "u",
            "--dst-collection", "b",
            "--src-es", "http://es:9200",
        ]
    )
    with pytest.raises(cc.CopyError, match="all four"):
        cc.run(args)


def test_dry_run_and_verify_only_are_mutually_exclusive():
    args = cc.parse_args(
        [
            "--src-collection", "a",
            "--dst-qdrant", "u",
            "--dst-collection", "b",
            "--dry-run",
            "--verify-only",
        ]
    )
    with pytest.raises(cc.CopyError, match="mutually exclusive"):
        cc.run(args)
