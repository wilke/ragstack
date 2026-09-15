#!/usr/bin/env python
"""**Store-to-store collection copy**: Qdrant -> Qdrant, plus the matching
Elasticsearch index -> Elasticsearch index.

The capability the plan
``/rag/documents/plans/plan-hackathon-asm-semantic-copy-2026-09-15.md`` needs and
that neither ragstack nor ragstack-ctl had: move one collection's *live store
contents* from one instance to another, without re-embedding and without the API
running. ``load_embeddings.py`` needs header-style embedding files (the semantic
shards are an older header-less format and at least one is truncated), a
collection archive replay needs an archive (``versions: []``), and
``ragstack-ctl restore --as`` moves a whole tenant. So: read the stores, write
the stores.

What it does NOT do, on purpose:

* It never deletes or recreates anything. ``--create-dst`` creates a destination
  collection only when it is *absent*; an existing destination is verified
  (vector size + distance) and then written into. There is no ``--recreate-dst``.
  Rollback is ``DELETE /v1/collections/<id>`` through the API, which purges only
  the destination tenant's physical stores.
* It never writes to the source. The source legs are scroll/search only.
* It does not touch ``tenant_id`` (or any other payload field). Point ids,
  payloads, ES ``_id`` and ``_source`` cross verbatim, which is what makes the
  copy idempotent and re-runnable.

**Destination geometry.** ``--create-dst`` builds the destination from the
SOURCE collection's size/distance, but at a smaller resolution:
``datatype: float16`` originals ``on_disk``, plus an ``int8`` scalar-quantized
copy (quantile 0.99, ``always_ram``) for search, and ``on_disk_payload``. Qdrant
rescores from the float16 originals, so no ragstack code change is needed. For a
4096-d/3M-point collection that is roughly 24 GB on disk + 12 GB resident rather
than 53 GB. Keyword payload indexes on ``tenant_id`` and ``doc_id`` are created
the way ``QdrantVectorStore._ensure_payload_indexes`` expects them (``doc_id`` is
not optional at scale: every delete-prior filters on it).

**Resumability.** A checkpoint (``--checkpoint``) holds the Qdrant scroll offset,
the ES ``search_after`` sort key and running counts, written atomically after
every batch. A re-run resumes from it by default; ``--restart`` ignores it. The
checkpoint records the source/destination identities and refuses to resume a
*different* copy into the same file. Both legs are idempotent — Qdrant upserts by
point id, ES bulk uses ``op_type: index`` — so a resumed batch that was already
written is overwritten, never duplicated, and a version conflict cannot happen.

  *Caveat, by design:* ES ``search_after`` here sorts on ``_shard_doc``, whose
  values are only stable for the life of a point-in-time. A resume opens a NEW
  PIT and reuses the stored key, which is sound because the source index is read
  only for the duration of the copy (the plan's premise). If the source is being
  written while you copy, use ``--restart`` rather than a resume — and in either
  case the final verification, not the checkpoint, is what says the copy is
  complete.

**Verification** runs after a copy, or alone with ``--verify-only``: live counts
on both legs, then N random source point ids spot-checked against the
destination for payload equality and vector cosine >= 0.99 (float16 rounds, so
this is not an equality test), and the same chunk ids spot-checked in ES for
``_source`` equality.

Exit codes: ``0`` ok, ``1`` error, ``3`` verification mismatch.

Usage::

    # read-only: counts, destination state, and the plan
    python scripts/copy_collection.py --dry-run \
        --src-qdrant http://127.0.0.1:6333 --src-collection ragstack_sfr_semantic \
        --dst-qdrant http://127.0.0.1:24081 --dst-collection <physical-dst-name> \
        --src-es http://127.0.0.1:9200 --src-index ragstack_sfr_semantic \
        --dst-es http://127.0.0.1:24083 --dst-index <physical-dst-name>

    # the copy itself (same arguments, plus a checkpoint and the destination geometry)
    python scripts/copy_collection.py --create-dst \
        --checkpoint /rag/data/tenants/hackathon/copy-asm-semantic.ckpt \
        --out /rag/data/tenants/hackathon/copy-asm-semantic.json \
        ... (as above)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Datatype,
    Distance,
    PayloadSchemaType,
    PointStruct,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

log = logging.getLogger("copy_collection")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISMATCH = 3

#: Points between progress lines.
PROGRESS_EVERY = 50_000
#: Key standing for an unnamed (single, default) dense vector — the spelling
#: Qdrant itself uses for the default vector internally, and JSON-safe.
UNNAMED = ""
#: Payload fields keyword-indexed on the destination, mirroring
#: ``QdrantVectorStore._ensure_payload_indexes``.
PAYLOAD_INDEX_FIELDS = ("tenant_id", "doc_id")
#: Minimum cosine similarity between a source and destination vector for the
#: spot check to pass. float16 keeps ~3 decimal digits, so an exact comparison
#: would be a false alarm generator; 0.99 catches a genuinely wrong vector.
DEFAULT_MIN_COSINE = 0.99


class CopyError(RuntimeError):
    """Anything that should end the run with exit code 1."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute or key access — the Qdrant client returns pydantic models, the
    raw HTTP API (and the tests' fakes) return dicts, and this tool reads both."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _thousands(n: int | None) -> str:
    return "unknown" if n is None else f"{n:,}"


def _distance_name(value: Any) -> str:
    """Normalise a distance to Qdrant's own spelling (``Cosine``, ``Dot``, ...)."""
    raw = str(_get(value, "value", value) if not isinstance(value, str) else value)
    for member in Distance:
        if member.value.lower() == raw.lower():
            return member.value
    raise CopyError(f"unknown Qdrant distance {raw!r}")


def _distance_enum(value: Any) -> Distance:
    return Distance(_distance_name(value))


def _jsonable(value: Any) -> Any:
    """Qdrant point ids are int or str (uuid); anything else is stringified so
    the checkpoint stays plain JSON."""
    if value is None or isinstance(value, (int, str)):
        return value
    return str(value)


# --------------------------------------------------------------------------- #
# source geometry / destination configuration
# --------------------------------------------------------------------------- #


def source_vector_spec(info: Any) -> dict[str, tuple[int, str]]:
    """``{vector_name: (size, distance)}`` for a collection.

    An unnamed (single) dense vector comes back under the key ``UNNAMED`` (``""``);
    named vectors come back under their names. The source of this copy is unnamed,
    but a collection built by another tool may not be, and a copy that silently
    dropped the names would produce an unsearchable destination.
    """
    params = _get(_get(info, "config"), "params")
    vectors = _get(params, "vectors")
    if vectors is None:
        raise CopyError("source collection has no dense vector configuration")
    if isinstance(vectors, dict) and "size" not in vectors:
        named = vectors
    elif isinstance(vectors, dict):
        named = {UNNAMED: vectors}
    else:
        named = {UNNAMED: vectors}
    spec: dict[str, tuple[int, str]] = {}
    for name, params_ in named.items():
        size = _get(params_, "size")
        distance = _get(params_, "distance")
        if not size or distance is None:
            raise CopyError(f"vector {name or '(unnamed)'} has no size/distance")
        spec[name] = (int(size), _distance_name(distance))
    if not spec:
        raise CopyError("source collection has no dense vector configuration")
    return spec


@dataclass(frozen=True)
class DestinationConfig:
    """Exactly the arguments ``create_collection`` is called with."""

    vectors_config: Any
    quantization_config: Any
    on_disk_payload: bool


def build_destination_config(
    spec: dict[str, tuple[int, str]],
    *,
    datatype: str = "float16",
    quantization: str = "int8",
    on_disk: bool = True,
) -> DestinationConfig:
    """Destination geometry from the source's size/distance.

    float16 originals (optionally on disk) + an int8 scalar-quantized copy held in
    RAM for search. Quantization is set at the collection level, so it covers every
    named vector, which is how the plan writes it.
    """
    params = {
        name: VectorParams(
            size=size,
            distance=_distance_enum(distance),
            datatype=Datatype(datatype),
            on_disk=on_disk,
        )
        for name, (size, distance) in spec.items()
    }
    vectors_config: Any = params[UNNAMED] if set(params) == {UNNAMED} else params
    quantization_config: Any = None
    if quantization == "int8":
        quantization_config = ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8, quantile=0.99, always_ram=True
            )
        )
    elif quantization != "none":
        raise CopyError(f"unsupported quantization {quantization!r}")
    return DestinationConfig(
        vectors_config=vectors_config,
        quantization_config=quantization_config,
        on_disk_payload=True,
    )


def config_as_json(cfg: DestinationConfig) -> dict[str, Any]:
    """The destination configuration as the JSON Qdrant would be sent — what the
    dry run prints, so an operator can compare it against the plan by eye."""

    def dump(value: Any) -> Any:
        if hasattr(value, "model_dump_json"):
            return json.loads(value.model_dump_json(exclude_none=True))
        return value

    vectors = (
        {name: dump(v) for name, v in cfg.vectors_config.items()}
        if isinstance(cfg.vectors_config, dict)
        else dump(cfg.vectors_config)
    )
    return {
        "vectors": vectors,
        "quantization_config": dump(cfg.quantization_config),
        "on_disk_payload": cfg.on_disk_payload,
        "payload_indexes": list(PAYLOAD_INDEX_FIELDS),
    }


def spec_mismatch(src: dict[str, tuple[int, str]], dst: dict[str, tuple[int, str]]) -> list[str]:
    """Why an existing destination cannot receive this source. Empty list = ok.

    Only the geometry that makes vectors *meaningful* is compared: names, size,
    distance. Datatype and quantization are deliberately NOT compared — an
    existing destination may legitimately be float32 (e.g. created by the API's
    register step) and copying into it is fine.
    """
    problems = []
    if set(src) != set(dst):
        problems.append(
            f"vector names differ: source {sorted(src) or ['(unnamed)']} vs "
            f"destination {sorted(dst) or ['(unnamed)']}"
        )
        return problems
    for name in sorted(src):
        s_size, s_dist = src[name]
        d_size, d_dist = dst[name]
        label = name or "(unnamed)"
        if s_size != d_size:
            problems.append(f"vector {label}: size {s_size} (source) != {d_size} (destination)")
        if s_dist != d_dist:
            problems.append(f"vector {label}: distance {s_dist} (source) != {d_dist} (destination)")
    return problems


# --------------------------------------------------------------------------- #
# checkpoint
# --------------------------------------------------------------------------- #


@dataclass
class Checkpoint:
    """Resume state. ``key`` identifies the copy; the counts are for reporting
    only — verification always reads live counts."""

    key: dict[str, Any] = field(default_factory=dict)
    qdrant_offset: Any = None
    qdrant_done: bool = False
    qdrant_copied: int = 0
    es_search_after: list[Any] | None = None
    es_done: bool = False
    es_copied: int = 0
    updated: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "qdrant_offset": self.qdrant_offset,
            "qdrant_done": self.qdrant_done,
            "qdrant_copied": self.qdrant_copied,
            "es_search_after": self.es_search_after,
            "es_done": self.es_done,
            "es_copied": self.es_copied,
            "updated": self.updated,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Checkpoint:
        return cls(
            key=raw.get("key") or {},
            qdrant_offset=raw.get("qdrant_offset"),
            qdrant_done=bool(raw.get("qdrant_done")),
            qdrant_copied=int(raw.get("qdrant_copied") or 0),
            es_search_after=raw.get("es_search_after"),
            es_done=bool(raw.get("es_done")),
            es_copied=int(raw.get("es_copied") or 0),
            updated=float(raw.get("updated") or 0.0),
        )


def save_checkpoint(path: str | None, cp: Checkpoint) -> None:
    """Atomic: write a sibling temp file, then rename over the target. A crash
    (or a kill -9) mid-write therefore leaves the previous checkpoint intact
    rather than a truncated one that would restart the copy from zero."""
    if not path:
        return
    cp.updated = time.time()
    tmp = f"{path}.tmp"
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cp.to_json(), fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_checkpoint(path: str | None, key: dict[str, Any], *, restart: bool = False) -> Checkpoint:
    """Resume by default when the file is present and describes THIS copy.

    A checkpoint whose key names a different source/destination is refused rather
    than ignored: silently starting from zero would be survivable, but silently
    resuming another copy's offset would skip most of the collection and the
    counts would only disagree hours later.
    """
    fresh = Checkpoint(key=dict(key))
    if not path or restart or not os.path.exists(path):
        return fresh
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        raise CopyError(f"checkpoint {path} is unreadable: {e}; use --restart to start over") from e
    cp = Checkpoint.from_json(raw)
    if cp.key and cp.key != key:
        raise CopyError(
            f"checkpoint {path} belongs to a different copy "
            f"({cp.key} != {key}); use --restart or a different --checkpoint path"
        )
    cp.key = dict(key)
    log.info(
        "resuming from %s: qdrant %s points (done=%s), es %s docs (done=%s)",
        path,
        _thousands(cp.qdrant_copied),
        cp.qdrant_done,
        _thousands(cp.es_copied),
        cp.es_done,
    )
    return cp


# --------------------------------------------------------------------------- #
# Qdrant leg
# --------------------------------------------------------------------------- #


def vector_of(point: Any) -> Any:
    """The point's vector(s), unnamed (list) or named (dict), rejecting a point
    that was scrolled without vectors — copying those would build a destination
    that answers every query with nothing."""
    vector = _get(point, "vector")
    if vector is None:
        raise CopyError(
            f"point {_get(point, 'id')!r} came back without a vector; "
            "the source must be scrolled with with_vectors=True"
        )
    return vector


def _progress(copied: int, total: int | None, started_at: float, start_count: int) -> str:
    elapsed = max(time.monotonic() - started_at, 1e-6)
    rate = (copied - start_count) / elapsed
    eta = ""
    if total and rate > 0 and total > copied:
        eta = f", eta {(total - copied) / rate / 60:.1f} min"
    return f"{_thousands(copied)}/{_thousands(total)} points, {rate:,.0f} points/s{eta}"


def copy_vectors(
    src: Any,
    dst: Any,
    src_collection: str,
    dst_collection: str,
    *,
    batch: int = 256,
    pause: float = 0.05,
    cp: Checkpoint,
    checkpoint_path: str | None = None,
    total: int | None = None,
    sleep: Any = time.sleep,
) -> int:
    """Scroll the source ordered by point id, upsert into the destination.

    ``wait=False`` on every batch but the last: the destination indexes in the
    background while the next batch is already in flight, and the final
    ``wait=True`` is what makes the count check straight afterwards meaningful.
    """
    if cp.qdrant_done:
        log.info("vector leg already complete per checkpoint (%s points)", _thousands(cp.qdrant_copied))
        return cp.qdrant_copied
    offset = cp.qdrant_offset
    copied = cp.qdrant_copied
    started_at = time.monotonic()
    start_count = copied
    next_log = copied + PROGRESS_EVERY
    while True:
        points, next_offset = src.scroll(
            collection_name=src_collection,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        last = next_offset is None
        if points:
            structs = [
                PointStruct(
                    id=_get(p, "id"),
                    vector=vector_of(p),
                    payload=_get(p, "payload") or {},
                )
                for p in points
            ]
            dst.upsert(collection_name=dst_collection, points=structs, wait=last)
            copied += len(structs)
        cp.qdrant_offset = _jsonable(next_offset)
        cp.qdrant_copied = copied
        cp.qdrant_done = last
        save_checkpoint(checkpoint_path, cp)
        if copied >= next_log:
            log.info("qdrant: %s", _progress(copied, total, started_at, start_count))
            next_log = copied + PROGRESS_EVERY
        if last:
            break
        offset = next_offset
        if pause:
            sleep(pause)
    log.info("qdrant leg done: %s", _progress(copied, total, started_at, start_count))
    return copied


# --------------------------------------------------------------------------- #
# Elasticsearch leg
# --------------------------------------------------------------------------- #


def _default_bulk(client: Any, actions: list[dict[str, Any]], **kw: Any) -> tuple[int, Any]:
    from elasticsearch import helpers

    return helpers.bulk(client, actions, **kw)


def copy_text(
    src: Any,
    dst: Any,
    src_index: str,
    dst_index: str,
    *,
    size: int = 1024,
    pause: float = 0.05,
    cp: Checkpoint,
    checkpoint_path: str | None = None,
    bulk_fn: Any = None,
    keep_alive: str = "5m",
    total: int | None = None,
    sleep: Any = time.sleep,
) -> int:
    """``search_after`` on ``_shard_doc`` under a point-in-time, bulk-indexed into
    the destination with the same ``_id`` and ``_source``.

    ``_op_type: index`` (not ``create``) is what makes a resume — or a re-run over
    an already-populated destination — overwrite instead of raising
    ``version_conflict_engine_exception``. ``_type`` is never sent: it has been
    gone since ES 8. Refresh is off for the duration and forced once at the end,
    because a per-bulk refresh dominates the wall clock on a large index.
    """
    if cp.es_done:
        log.info("text leg already complete per checkpoint (%s docs)", _thousands(cp.es_copied))
        return cp.es_copied
    bulk = bulk_fn or _default_bulk
    pit = src.open_point_in_time(index=src_index, keep_alive=keep_alive)
    pit_id = _get(pit, "id")
    if not pit_id:
        raise CopyError(f"could not open a point-in-time on {src_index}")
    search_after = cp.es_search_after
    copied = cp.es_copied
    started_at = time.monotonic()
    start_count = copied
    next_log = copied + PROGRESS_EVERY
    try:
        while True:
            kwargs: dict[str, Any] = {
                "size": size,
                "sort": [{"_shard_doc": "asc"}],
                "pit": {"id": pit_id, "keep_alive": keep_alive},
                "track_total_hits": False,
            }
            if search_after:
                kwargs["search_after"] = search_after
            resp = src.search(**kwargs)
            hits = resp["hits"]["hits"]
            pit_id = resp.get("pit_id") or pit_id
            if hits:
                actions = [
                    {
                        "_op_type": "index",
                        "_index": dst_index,
                        "_id": h["_id"],
                        "_source": h["_source"],
                    }
                    for h in hits
                ]
                bulk(dst, actions, refresh=False, raise_on_error=True)
                copied += len(actions)
                search_after = list(hits[-1]["sort"])
            cp.es_search_after = search_after
            cp.es_copied = copied
            cp.es_done = len(hits) < size
            save_checkpoint(checkpoint_path, cp)
            if copied >= next_log:
                log.info("es: %s", _progress(copied, total, started_at, start_count))
                next_log = copied + PROGRESS_EVERY
            if cp.es_done:
                break
            if pause:
                sleep(pause)
    finally:
        try:
            src.close_point_in_time(id=pit_id)
        except Exception as e:  # noqa: BLE001 — the PIT expires on its own
            log.debug("closing the point-in-time failed (it will expire): %s", e)
    dst.indices.refresh(index=dst_index)
    log.info("es leg done: %s", _progress(copied, total, started_at, start_count))
    return copied


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #


def cosine(a: Any, b: Any) -> float:
    """Cosine similarity, 0.0 when either side is empty or zero-length."""
    if a is None or b is None or len(a) != len(b) or not len(a):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def canon(value: Any) -> Any:
    """JSON-shaped normalisation: tuples become lists (a payload that made a
    round trip through JSON cannot keep a tuple), so equality compares content
    rather than the container type the client happened to build."""
    if isinstance(value, dict):
        return {k: canon(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canon(v) for v in value]
    return value


def payloads_equal(a: Any, b: Any) -> bool:
    return canon(a or {}) == canon(b or {})


def compare_point(
    point_id: Any,
    src_point: Any,
    dst_point: Any,
    *,
    min_cosine: float = DEFAULT_MIN_COSINE,
) -> list[str]:
    """Problems with one copied point; empty list = it matches.

    Payload must be equal exactly (it crosses verbatim). Vectors are compared by
    cosine, because a float16 destination rounds every component.
    """
    if dst_point is None:
        return [f"point {point_id!r}: missing from the destination"]
    problems = []
    if not payloads_equal(_get(src_point, "payload"), _get(dst_point, "payload")):
        problems.append(f"point {point_id!r}: payload differs")
    src_vec = _get(src_point, "vector")
    dst_vec = _get(dst_point, "vector")
    if isinstance(src_vec, dict) or isinstance(dst_vec, dict):
        src_map = src_vec if isinstance(src_vec, dict) else {UNNAMED: src_vec}
        dst_map = dst_vec if isinstance(dst_vec, dict) else {UNNAMED: dst_vec}
        if set(src_map) != set(dst_map):
            problems.append(
                f"point {point_id!r}: vector names differ "
                f"({sorted(src_map)} != {sorted(dst_map)})"
            )
            return problems
        for name in sorted(src_map):
            sim = cosine(src_map[name], dst_map[name])
            if sim < min_cosine:
                problems.append(
                    f"point {point_id!r}: vector {name or '(unnamed)'} cosine {sim:.6f} < {min_cosine}"
                )
        return problems
    sim = cosine(src_vec, dst_vec)
    if sim < min_cosine:
        problems.append(f"point {point_id!r}: vector cosine {sim:.6f} < {min_cosine}")
    return problems


def sample_point_ids(
    client: Any,
    collection: str,
    n: int,
    *,
    points_count: int | None = None,
    rng: random.Random | None = None,
) -> list[Any]:
    """N (or fewer) point ids drawn from random positions in the id order.

    Qdrant has no "random point" verb, and enumerating 3M points to pick 20 of
    them is absurd, so this scrolls one point from a random offset: a random uuid
    for uuid-keyed collections, a random integer below the point count for
    integer-keyed ones. Best effort by construction — an offset past the end
    simply yields nothing and is retried from the start.
    """
    rnd = rng or random.Random()
    probe, _ = client.scroll(
        collection_name=collection, limit=1, with_payload=False, with_vectors=False
    )
    if not probe:
        return []
    numeric = isinstance(_get(probe[0], "id"), int)
    ids: list[Any] = []
    seen: set[Any] = set()
    for _ in range(n * 4):
        if len(ids) >= n:
            break
        if numeric:
            ceiling = max(int(points_count or 0), 1)
            offset: Any = rnd.randrange(0, ceiling)
        else:
            offset = str(uuid.UUID(int=rnd.getrandbits(128), version=4))
        found, _ = client.scroll(
            collection_name=collection,
            limit=1,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        if not found:
            found, _ = client.scroll(
                collection_name=collection, limit=1, with_payload=False, with_vectors=False
            )
        if not found:
            continue
        pid = _get(found[0], "id")
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def verify_vectors(
    src: Any,
    dst: Any,
    src_collection: str,
    dst_collection: str,
    *,
    spot_check: int = 20,
    min_cosine: float = DEFAULT_MIN_COSINE,
    rng: random.Random | None = None,
) -> tuple[dict[str, Any], list[str]]:
    src_info = src.get_collection(src_collection)
    dst_info = dst.get_collection(dst_collection)
    src_count = int(_get(src_info, "points_count") or 0)
    dst_count = int(_get(dst_info, "points_count") or 0)
    problems: list[str] = []
    if src_count != dst_count:
        problems.append(
            f"qdrant points_count: source {src_count:,} != destination {dst_count:,} "
            f"(difference {src_count - dst_count:+,})"
        )
    ids = sample_point_ids(src, src_collection, spot_check, points_count=src_count, rng=rng)
    checked = 0
    chunk_ids: list[str] = []
    if ids:
        src_points = src.retrieve(
            collection_name=src_collection, ids=ids, with_payload=True, with_vectors=True
        )
        dst_points = dst.retrieve(
            collection_name=dst_collection, ids=ids, with_payload=True, with_vectors=True
        )
        by_id = {_get(p, "id"): p for p in dst_points}
        for sp in src_points:
            pid = _get(sp, "id")
            problems.extend(
                compare_point(pid, sp, by_id.get(pid), min_cosine=min_cosine)
            )
            checked += 1
            cid = (_get(sp, "payload") or {}).get("chunk_id")
            if isinstance(cid, str):
                chunk_ids.append(cid)
    report = {
        "source_points": src_count,
        "destination_points": dst_count,
        "spot_checked": checked,
        "destination_status": str(_get(dst_info, "status") or ""),
        "sample_chunk_ids": chunk_ids,
    }
    return report, problems


def verify_text(
    src: Any,
    dst: Any,
    src_index: str,
    dst_index: str,
    *,
    chunk_ids: list[str] | None = None,
    spot_check: int = 20,
) -> tuple[dict[str, Any], list[str]]:
    src_count = int(src.count(index=src_index)["count"])
    dst_count = int(dst.count(index=dst_index)["count"])
    problems: list[str] = []
    if src_count != dst_count:
        problems.append(
            f"es count: source {src_count:,} != destination {dst_count:,} "
            f"(difference {src_count - dst_count:+,})"
        )
    ids = list(chunk_ids or [])[:spot_check]
    if not ids:
        # No chunk ids to hand (vector spot check found none, or --verify-only on
        # a text-only copy): take whatever the first page of the source holds.
        hits = src.search(index=src_index, size=spot_check, sort=["_doc"])["hits"]["hits"]
        ids = [h["_id"] for h in hits]
    checked = 0
    if ids:
        src_docs = {
            d["_id"]: d.get("_source")
            for d in src.mget(index=src_index, ids=ids)["docs"]
            if d.get("found")
        }
        dst_docs = {
            d["_id"]: d.get("_source")
            for d in dst.mget(index=dst_index, ids=ids)["docs"]
            if d.get("found")
        }
        for doc_id, source in src_docs.items():
            checked += 1
            if doc_id not in dst_docs:
                problems.append(f"es doc {doc_id!r}: missing from the destination")
            elif canon(source) != canon(dst_docs[doc_id]):
                problems.append(f"es doc {doc_id!r}: _source differs")
    return {
        "source_docs": src_count,
        "destination_docs": dst_count,
        "spot_checked": checked,
    }, problems


# --------------------------------------------------------------------------- #
# destination setup
# --------------------------------------------------------------------------- #


def collection_exists(client: Any, name: str) -> bool:
    collections = client.get_collections()
    return any(_get(c, "name") == name for c in _get(collections, "collections") or [])


def ensure_payload_indexes(client: Any, collection: str) -> None:
    """Keyword indexes on ``tenant_id`` and ``doc_id``, idempotent and
    best-effort — exactly ``QdrantVectorStore._ensure_payload_indexes``. An
    already-existing index raises, and that is not an error."""
    for field_name in PAYLOAD_INDEX_FIELDS:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            log.info("destination: payload index on %s ensured", field_name)
        except Exception as e:  # noqa: BLE001 — already-exists / transient; non-fatal
            log.debug("%s index on %r not (re)created: %s", field_name, collection, e)


def ensure_destination(
    client: Any,
    collection: str,
    src_spec: dict[str, tuple[int, str]],
    *,
    create: bool,
    datatype: str,
    quantization: str,
    on_disk: bool,
) -> str:
    """Make sure the destination can receive this source. Never deletes.

    Returns ``"created"`` or ``"existing"``. A destination whose geometry
    disagrees with the source is a hard error: recreating it is not this tool's
    decision to make (the collection may be registered and shared), and writing
    4096-d vectors into a 768-d collection is how an index gets silently ruined.
    """
    if collection_exists(client, collection):
        dst_spec = source_vector_spec(client.get_collection(collection))
        problems = spec_mismatch(src_spec, dst_spec)
        if problems:
            raise CopyError(
                f"destination collection {collection!r} does not match the source: "
                + "; ".join(problems)
                + ". It is NOT recreated — delete it through the API "
                "(DELETE /v1/collections/<id>) if that is really what you want."
            )
        log.info("destination collection %r already exists and matches the source", collection)
        if create:
            ensure_payload_indexes(client, collection)
        return "existing"
    if not create:
        raise CopyError(
            f"destination collection {collection!r} does not exist; "
            "pass --create-dst to create it from the source geometry"
        )
    cfg = build_destination_config(
        src_spec, datatype=datatype, quantization=quantization, on_disk=on_disk
    )
    client.create_collection(
        collection_name=collection,
        vectors_config=cfg.vectors_config,
        quantization_config=cfg.quantization_config,
        on_disk_payload=cfg.on_disk_payload,
    )
    log.info(
        "created destination collection %r (datatype=%s, quantization=%s, on_disk=%s)",
        collection,
        datatype,
        quantization,
        on_disk,
    )
    ensure_payload_indexes(client, collection)
    return "created"


# --------------------------------------------------------------------------- #
# reads used by --dry-run
# --------------------------------------------------------------------------- #


def describe_collection(client: Any, name: str) -> dict[str, Any]:
    if not collection_exists(client, name):
        return {"exists": False}
    info = client.get_collection(name)
    spec = source_vector_spec(info)
    return {
        "exists": True,
        "points_count": int(_get(info, "points_count") or 0),
        "status": str(_get(info, "status") or ""),
        "vectors": {
            (name_ or "(unnamed)"): {"size": size, "distance": distance}
            for name_, (size, distance) in spec.items()
        },
        "spec": spec,
    }


def describe_index(client: Any, name: str) -> dict[str, Any]:
    if not client.indices.exists(index=name):
        return {"exists": False}
    return {"exists": True, "count": int(client.count(index=name)["count"])}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--src-qdrant", default="http://127.0.0.1:6333", help="source Qdrant URL")
    p.add_argument("--src-collection", required=True, help="source Qdrant collection (physical name)")
    p.add_argument("--dst-qdrant", required=True, help="destination Qdrant URL")
    p.add_argument(
        "--dst-collection", required=True, help="destination Qdrant collection (physical name)"
    )
    p.add_argument("--src-es", default=None, help="source Elasticsearch URL (optional leg)")
    p.add_argument("--src-index", default=None, help="source Elasticsearch index")
    p.add_argument("--dst-es", default=None, help="destination Elasticsearch URL")
    p.add_argument("--dst-index", default=None, help="destination Elasticsearch index")
    p.add_argument(
        "--batch",
        type=int,
        default=256,
        help="points per Qdrant scroll/upsert (default 256). The ES leg reads "
             "4x this per search page",
    )
    p.add_argument(
        "--pause",
        type=float,
        default=0.05,
        help="seconds to sleep between source batches (default 0.05) so a shared "
             "production Qdrant keeps serving its tenants during the copy",
    )
    p.add_argument(
        "--checkpoint",
        default=None,
        help="JSON resume file: last Qdrant scroll offset, last ES search_after "
             "key, running counts. Resumed by default when present",
    )
    p.add_argument(
        "--restart",
        action="store_true",
        help="ignore an existing --checkpoint and copy from the beginning "
             "(both legs are idempotent, so this re-writes rather than duplicates)",
    )
    p.add_argument(
        "--create-dst",
        action="store_true",
        help="create the destination collection from the SOURCE size/distance if "
             "it is absent, and ensure the tenant_id/doc_id payload indexes. An "
             "existing destination is verified and kept, never recreated",
    )
    p.add_argument(
        "--vector-datatype",
        choices=["float32", "float16"],
        default="float16",
        help="stored precision of the destination's original vectors (default float16)",
    )
    p.add_argument(
        "--quantization",
        choices=["none", "int8"],
        default="int8",
        help="destination scalar quantization (default int8, quantile 0.99, always_ram)",
    )
    p.add_argument(
        "--on-disk",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep the destination's original vectors on disk (default on)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="read only: print source counts, the destination's current state and "
             "the plan, then exit 0 without creating or writing anything",
    )
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="skip the copy: compare live counts and spot-check random points/docs",
    )
    p.add_argument(
        "--spot-check",
        type=int,
        default=20,
        help="random source point ids compared against the destination (default 20)",
    )
    p.add_argument(
        "--min-cosine",
        type=float,
        default=DEFAULT_MIN_COSINE,
        help="minimum source/destination vector cosine in the spot check "
             "(default 0.99; float16 rounds, so this is not an equality test)",
    )
    p.add_argument("--qdrant-timeout", type=int, default=120, help="Qdrant client timeout, seconds")
    p.add_argument("--es-timeout", type=int, default=120, help="ES client request timeout, seconds")
    p.add_argument("--pit-keep-alive", default="5m", help="ES point-in-time keep-alive (default 5m)")
    p.add_argument("--out", default=None, help="write the JSON summary here as well as to stdout")
    p.add_argument("--seed", type=int, default=None, help="seed for the spot-check sampling")
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return p.parse_args(argv)


def _es_client(url: str, timeout: int) -> Any:
    from elasticsearch import Elasticsearch

    return Elasticsearch(url, request_timeout=timeout)


def _checkpoint_key(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "src_qdrant": args.src_qdrant,
        "src_collection": args.src_collection,
        "dst_qdrant": args.dst_qdrant,
        "dst_collection": args.dst_collection,
        "src_es": args.src_es,
        "src_index": args.src_index,
        "dst_es": args.dst_es,
        "dst_index": args.dst_index,
    }


def _emit(summary: dict[str, Any], out: str | None) -> None:
    text = json.dumps(summary, indent=2, sort_keys=True, default=str)
    print(text)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        log.info("summary written to %s", out)


def run(args: argparse.Namespace) -> int:
    es_parts = [args.src_es, args.src_index, args.dst_es, args.dst_index]
    if any(es_parts) and not all(es_parts):
        raise CopyError(
            "the Elasticsearch leg needs all four of --src-es/--src-index/"
            "--dst-es/--dst-index, or none of them (vectors only)"
        )
    with_es = all(es_parts)
    if args.dry_run and args.verify_only:
        raise CopyError("--dry-run and --verify-only are mutually exclusive")
    if args.batch < 1:
        raise CopyError("--batch must be >= 1")

    rng = random.Random(args.seed)
    src_q = QdrantClient(url=args.src_qdrant, timeout=args.qdrant_timeout)
    dst_q = QdrantClient(url=args.dst_qdrant, timeout=args.qdrant_timeout)
    src_es: Any = _es_client(args.src_es, args.es_timeout) if with_es else None
    dst_es: Any = _es_client(args.dst_es, args.es_timeout) if with_es else None

    started = time.time()
    summary: dict[str, Any] = {
        "tool": "copy_collection.py",
        "mode": "dry-run" if args.dry_run else ("verify-only" if args.verify_only else "copy"),
        "source": {
            "qdrant": args.src_qdrant,
            "collection": args.src_collection,
            "es": args.src_es,
            "index": args.src_index,
        },
        "destination": {
            "qdrant": args.dst_qdrant,
            "collection": args.dst_collection,
            "es": args.dst_es,
            "index": args.dst_index,
        },
        "started": started,
    }

    src_desc = describe_collection(src_q, args.src_collection)
    if not src_desc["exists"]:
        raise CopyError(
            f"source collection {args.src_collection!r} does not exist on {args.src_qdrant}"
        )
    src_spec: dict[str, tuple[int, str]] = src_desc["spec"]
    dst_desc = describe_collection(dst_q, args.dst_collection)

    # ---------------------------------------------------------------- dry run
    if args.dry_run:
        src_index_desc = describe_index(src_es, args.src_index) if with_es else {"exists": False}
        dst_index_desc = describe_index(dst_es, args.dst_index) if with_es else {"exists": False}
        cfg = build_destination_config(
            src_spec,
            datatype=args.vector_datatype,
            quantization=args.quantization,
            on_disk=args.on_disk,
        )
        print(f"source qdrant      {args.src_qdrant} {args.src_collection}")
        print(
            f"  points_count     {_thousands(src_desc['points_count'])}  "
            f"status={src_desc['status']}  vectors={src_desc['vectors']}"
        )
        print(f"destination qdrant {args.dst_qdrant} {args.dst_collection}")
        if dst_desc["exists"]:
            problems = spec_mismatch(src_spec, dst_desc["spec"])
            print(
                f"  EXISTS           points_count={_thousands(dst_desc['points_count'])}  "
                f"status={dst_desc['status']}  vectors={dst_desc['vectors']}"
            )
            print(f"  geometry         {'MISMATCH: ' + '; '.join(problems) if problems else 'matches the source'}")
        else:
            print("  DOES NOT EXIST   nothing was created by this dry run")
            print(
                "  would create     "
                + json.dumps(config_as_json(cfg), sort_keys=True)
                + (
                    ""
                    if args.create_dst
                    else "\n                   (only with --create-dst; without it the copy would fail)"
                )
            )
        if with_es:
            print(f"source es          {args.src_es} {args.src_index}")
            print(f"  docs             {_thousands(src_index_desc.get('count'))}")
            print(f"destination es     {args.dst_es} {args.dst_index}")
            if dst_index_desc["exists"]:
                print(f"  EXISTS           docs={_thousands(dst_index_desc.get('count'))}")
            else:
                print("  DOES NOT EXIST   nothing was created by this dry run; the ES "
                      "index must be created by the API's register step (it owns the mapping)")
        else:
            print("es leg             skipped (no --src-es/--src-index/--dst-es/--dst-index)")
        print(
            f"plan               scroll {args.src_collection} in batches of {args.batch} "
            f"(pause {args.pause}s), upsert into {args.dst_collection}"
            + (f"; ES search_after pages of {args.batch * 4}" if with_es else "")
            + f"; then verify counts and spot-check {args.spot_check} ids "
              f"(cosine >= {args.min_cosine})"
        )
        summary["source"]["points_count"] = src_desc["points_count"]
        summary["source"]["vectors"] = src_desc["vectors"]
        summary["source"]["docs"] = src_index_desc.get("count")
        summary["destination"]["exists"] = dst_desc["exists"]
        summary["destination"]["points_count"] = dst_desc.get("points_count")
        summary["destination"]["index_exists"] = dst_index_desc["exists"] if with_es else None
        summary["destination"]["docs"] = dst_index_desc.get("count")
        summary["would_create"] = config_as_json(cfg)
        summary["ok"] = True
        _emit(summary, args.out)
        return EXIT_OK

    # ------------------------------------------------------------------- copy
    if not args.verify_only:
        state = ensure_destination(
            dst_q,
            args.dst_collection,
            src_spec,
            create=args.create_dst,
            datatype=args.vector_datatype,
            quantization=args.quantization,
            on_disk=args.on_disk,
        )
        summary["destination"]["state"] = state
        cp = load_checkpoint(args.checkpoint, _checkpoint_key(args), restart=args.restart)
        src_docs = int(src_es.count(index=args.src_index)["count"]) if with_es else None
        copied_points = copy_vectors(
            src_q,
            dst_q,
            args.src_collection,
            args.dst_collection,
            batch=args.batch,
            pause=args.pause,
            cp=cp,
            checkpoint_path=args.checkpoint,
            total=src_desc["points_count"],
        )
        copied_docs = None
        if with_es:
            if not dst_es.indices.exists(index=args.dst_index):
                raise CopyError(
                    f"destination index {args.dst_index!r} does not exist on {args.dst_es}. "
                    "Create it through the API's register step so it gets ragstack's "
                    "mapping (metadata.* keyword with ignore_above); this tool will not "
                    "guess a mapping"
                )
            copied_docs = copy_text(
                src_es,
                dst_es,
                args.src_index,
                args.dst_index,
                size=args.batch * 4,
                pause=args.pause,
                cp=cp,
                checkpoint_path=args.checkpoint,
                keep_alive=args.pit_keep_alive,
                total=src_docs,
            )
        summary["copied"] = {"points": copied_points, "docs": copied_docs}

    # ----------------------------------------------------------------- verify
    vec_report, problems = verify_vectors(
        src_q,
        dst_q,
        args.src_collection,
        args.dst_collection,
        spot_check=args.spot_check,
        min_cosine=args.min_cosine,
        rng=rng,
    )
    summary["verification"] = {"qdrant": vec_report}
    if with_es:
        text_report, text_problems = verify_text(
            src_es,
            dst_es,
            args.src_index,
            args.dst_index,
            chunk_ids=vec_report.get("sample_chunk_ids"),
            spot_check=args.spot_check,
        )
        summary["verification"]["es"] = text_report
        problems.extend(text_problems)
    summary["problems"] = problems
    summary["ok"] = not problems
    summary["elapsed_s"] = round(time.time() - started, 3)
    _emit(summary, args.out)
    if problems:
        for problem in problems:
            log.error("verification: %s", problem)
        return EXIT_MISMATCH
    log.info("verified: source and destination agree")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    try:
        return run(args)
    except CopyError as e:
        log.error("%s", e)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("interrupted; re-run with the same --checkpoint to resume")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
