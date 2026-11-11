"""``h2h_report.py reembed``: the 200-chunk re-embed sample of PREREG §2 / §6 (embedding drift).

Three steps; only ``fetch`` touches a service and it is run by the operator, never by tests.

``select``  — seeded draw of 200 chunk ids from the ft512o64s arm. The population is the
    distinct ``chunk_ids`` of that arm's shard receipts (the Addendum C receipts carry them
    per shard, so no export and no live call is needed): the ids are sorted, then
    ``random.Random(seed).sample`` draws 200. Output: a private ``ids.json`` with the ids and
    their list sha256 (sorted, one per line, LF); the sha256 is what ``frozen-inputs.json``
    pins. Deterministic for a given id population and seed.

``fetch``   — LIVE (operator, at the freeze and at the run): reads each sampled chunk's text
    and stored vector from the Elasticsearch index and the Qdrant collection named on the
    command line — looked up by their tenant-scoped ids (``<tenant>:<chunk_id>`` in the text
    index, ``ragstack.stores.qdrant._point_id(chunk_id, tenant)`` in the vector store;
    ``--tenant``, default ``public``) — embeds the text through the given OpenAI-compatible
    endpoint, and writes
    ``cosines.json``: ``[{"chunk_id", "cosine", "stored_dim", "model"}]``. Hosts and
    collection names are arguments, never defaults, and never written to the output beyond
    the model id.

``verify``  — ``cosines.json`` against the threshold (0.999) and n (200): the §6 gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[1]
_PYTHON = _EVAL.parents[1]  # python/, so `fetch` can import ragstack without PYTHONPATH
for _p in (_EVAL, _PYTHON):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from h2h import gates as G  # noqa: E402

DEFAULT_SEED = 20261111
N = 200


def select_ids(population: list[str], n: int = N, seed: int = DEFAULT_SEED) -> list[str]:
    """Seeded sample of ``n`` ids from the distinct ``population`` (sorted first, so the draw
    depends only on the set of ids and the seed)."""
    ids = sorted(set(population))
    if len(ids) < n:
        raise ValueError(f"population has {len(ids)} ids, fewer than {n}")
    return random.Random(seed).sample(ids, n)


def ids_from_receipts(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        r = json.loads(Path(p).read_text(encoding="utf-8"))
        ids = r.get("chunk_ids") or []
        if not ids:
            raise ValueError(f"{p}: receipt carries no chunk_ids")
        out.extend(ids)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector dimensions differ")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def cmd_select(args: argparse.Namespace) -> int:
    if args.receipts:
        pop = ids_from_receipts(args.receipts)
    else:
        pop = [line.strip() for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    sample = select_ids(pop, args.n, args.seed)
    out = {"schema": "h2h-reembed-sample/1", "arm": "ft512o64s", "n": len(sample), "seed": args.seed,
           "population_distinct": len(set(pop)), "population_sha256": G.ids_sha256(pop),
           "ids_sha256": G.ids_sha256(sample), "chunk_ids": sample}
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "chunk_ids"}, indent=1))
    return 0


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 120.0) -> Any:
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-supplied URL
        return json.loads(resp.read().decode("utf-8"))


def es_doc_id(chunk_id: str, tenant: str) -> str:
    """Elasticsearch stores a chunk under ``_id = "<tenant>:<chunk_id>"``."""
    return f"{tenant}:{chunk_id}"


def qdrant_point_id(chunk_id: str, tenant: str) -> str:
    """Qdrant point ids are ``ragstack.stores.qdrant._point_id(chunk_id, tenant)``; imported,
    never re-implemented."""
    from ragstack.stores.qdrant import _point_id

    return _point_id(chunk_id, tenant)


def fetch_cosines(ids: list[str], *, tenant: str, post, es_url: str, es_index: str, qdrant_url: str,
                  qdrant_collection: str, embed_url: str, model: str, headers: dict[str, str] | None = None,
                  batch: int = 16) -> list[dict[str, Any]]:
    """Texts from the text index and stored vectors from the vector store (both looked up by
    their tenant-scoped ids), re-embedded through ``embed_url``; the output rows are keyed by
    the ORIGINAL chunk id, so ``verify`` and the ids-hash gate are unchanged. ``post(url, body,
    headers)`` performs the HTTP call (injected so tests run on fakes)."""
    es_ids = {es_doc_id(c, tenant): c for c in ids}
    docs = post(f"{es_url.rstrip('/')}/{es_index}/_mget", {"ids": list(es_ids)}, None)
    texts: dict[str, str] = {}
    for d in docs.get("docs", []):
        if not d.get("found"):
            raise SystemExit(f"chunk {es_ids.get(d.get('_id'), d.get('_id'))} not in the text index")
        src = d["_source"]
        texts[es_ids[d["_id"]]] = src.get("content") or src.get("text") or ""
    missing_text = [c for c in ids if c not in texts]
    if missing_text:
        raise SystemExit(f"{len(missing_text)} sampled chunk(s) not returned by the text index")
    point_ids = {qdrant_point_id(c, tenant): c for c in ids}
    pts = post(f"{qdrant_url.rstrip('/')}/collections/{qdrant_collection}/points",
               {"ids": list(point_ids), "with_vector": True, "with_payload": False}, None)
    stored = {point_ids[str(pt["id"])]: pt["vector"] for pt in pts.get("result", []) if str(pt["id"]) in point_ids}
    missing = [c for c in ids if c not in stored]
    if missing:
        raise SystemExit(f"{len(missing)} sampled chunk(s) have no stored vector")
    out = []
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        resp = post(f"{embed_url.rstrip('/')}/v1/embeddings", {"model": model, "input": [texts[c] for c in chunk]}, headers)
        vecs = [d["embedding"] for d in sorted(resp["data"], key=lambda d: d["index"])]
        for c, v in zip(chunk, vecs, strict=True):
            out.append({"chunk_id": c, "cosine": round(cosine(v, stored[c]), 8), "stored_dim": len(stored[c]),
                        "model": resp.get("model") or model})
    return out


def cmd_fetch(args: argparse.Namespace) -> int:
    """LIVE (the HTTP calls); the id mapping and the cosine assembly are tested on fakes."""
    sample = json.loads(Path(args.ids).read_text(encoding="utf-8"))
    headers = {}
    if args.api_key_file:
        headers["Authorization"] = "Bearer " + Path(args.api_key_file).read_text(encoding="utf-8").strip()
    out = fetch_cosines(sample["chunk_ids"], tenant=args.tenant, post=_post_json, es_url=args.es_url,
                        es_index=args.es_index, qdrant_url=args.qdrant_url, qdrant_collection=args.qdrant_collection,
                        embed_url=args.embed_url, model=args.model, headers=headers, batch=args.batch)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"n": len(out), "min_cosine": min(o["cosine"] for o in out), "model": out[0]["model"] if out else None,
                      "tenant": args.tenant}))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cos = json.loads(Path(args.cosines).read_text(encoding="utf-8"))
    pinned = args.ids_sha256
    if args.ids:
        pinned = json.loads(Path(args.ids).read_text(encoding="utf-8")).get("ids_sha256")
    bad, desc = G.check_reembed(cos, args.threshold, args.n, pinned)
    print(json.dumps({"ok": not bad, "failures": bad, **desc}, indent=1))
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select", help="seeded 200-id draw from the ft512o64s receipts (no live call)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--receipts", nargs="+", help="the arm's shard receipts (chunk_ids)")
    g.add_argument("--ids-file", help="or a text file, one chunk id per line")
    s.add_argument("--n", type=int, default=N)
    s.add_argument("--seed", type=int, default=DEFAULT_SEED)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_select)
    f = sub.add_parser("fetch", help="LIVE: texts + stored vectors, re-embed, cosines (operator only)")
    f.add_argument("--ids", required=True)
    f.add_argument("--es-url", required=True)
    f.add_argument("--es-index", required=True)
    f.add_argument("--qdrant-url", required=True)
    f.add_argument("--qdrant-collection", required=True)
    f.add_argument("--tenant", default="public", help="the registry tenant: ES ids are <tenant>:<chunk_id>, Qdrant point ids _point_id(chunk_id, tenant)")
    f.add_argument("--embed-url", required=True)
    f.add_argument("--model", required=True)
    f.add_argument("--api-key-file")
    f.add_argument("--batch", type=int, default=16)
    f.add_argument("--out", required=True)
    f.set_defaults(fn=cmd_fetch)
    v = sub.add_parser("verify", help="cosines.json against the §6 threshold")
    v.add_argument("--cosines", required=True)
    v.add_argument("--ids", help="the sample file, to check the cosines cover exactly the pinned ids")
    v.add_argument("--ids-sha256", help="or the pinned ids sha256 itself (frozen-inputs.json reembed.ids_sha256)")
    v.add_argument("--threshold", type=float, default=0.999)
    v.add_argument("--n", type=int, default=N)
    v.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return int(args.fn(args))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
