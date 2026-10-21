#!/usr/bin/env python
"""#754 review re-run: value-only symbol glossary, norms on/off, per-document
cap, and the RERANKED leg (the actual /v1/retrieve shape).

Arms (scratch copies of one live index; same safety rules as
``glossary_scratch_experiment.py`` — read-only source, ``scratch754_`` writes
only, deleted and verified on ES and Qdrant):

* ``base`` — the live chunks verbatim;
* ``mention`` — rules/2 + cached LLM glossary, attached to every mention
  (``attach_only_with_value=False``) — the first experiment's attachment, for
  reference;
* ``val`` — rules/2 + cached LLM glossary, attached only where the chunk states
  a value (``attach_only_with_value=True``, the new default), with the field
  mapped as the store maps it (norms on);
* ``valnn`` — the same chunks, ``symbol_glossary`` mapped with
  ``"norms": false`` (no BM25 length normalisation on the short field).

Each glossary arm is queried at ``--boosts``; every arm is also reported with a
per-document cap (``retrieval_max_per_doc``, stable demotion, applied last as
the retriever does).

Legs, matching /v1/retrieve as deployed: per-leg depth ``--leg-depth`` (100 =
top_k 50 x retrieval_candidate_multiplier 2). ``bm25``; ``hybrid`` = RRF(k=60) of
the BM25 and dense lists, the dense list from the BASE vectors (the glossary is
never embedded, so the vectors do not change); ``rerank`` = the hybrid list,
capped (``shape()`` inside ``retrieve()``), cut to 50, rescored by the live
cross-encoder sidecar on chunk CONTENT (stateless, read-only use, at most 4
requests in flight), capped again (the router's second ``shape()``), top 10.

The report holds pair ids, ranks and counts only.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as _dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ragstack.ingestion.reassembly import reassemble
from ragstack.ingestion.symbol_glossary import (
    GlossaryConfig,
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
)
from ragstack.models import Chunk
from ragstack.stores.elasticsearch import _MAPPINGS, ElasticsearchTextIndex

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossary_scratch_experiment as gx  # noqa: E402

RERANK_POOL = 50
MAX_RERANK_INFLIGHT = 4


def cap_per_doc(lst: list[tuple[str, str]], k: int) -> list[tuple[str, str]]:
    """The retriever's ``_cap_per_doc``: a stable demotion, never a deletion."""
    if k <= 0:
        return lst
    seen: dict[str, int] = defaultdict(int)
    keep, overflow = [], []
    for cid, did in lst:
        seen[did] += 1
        (keep if seen[did] <= k else overflow).append((cid, did))
    return keep + overflow


def build(rows: list[dict[str, Any]], llm_cache: Path) -> tuple[dict[str, list[Chunk]], dict[str, int]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_doc[str(r["doc_id"])].append(r)
    cache = SymbolGlossaryCache(llm_cache)
    att = {
        "mention": SymbolGlossaryAttacher(GlossaryConfig(attach_only_with_value=False), cache),
        "val": SymbolGlossaryAttacher(GlossaryConfig(), cache),
    }
    arms: dict[str, list[Chunk]] = {"base": [], "mention": [], "val": []}
    for did in sorted(by_doc):
        drows = sorted(by_doc[did], key=lambda r: int(r["start_char"]))
        doc_text = reassemble(did, drows).text
        glossaries = {a: att[a].glossary_for(doc_text) for a in att}
        for arm in arms:
            for r in drows:
                c = Chunk(id=str(r["chunk_id"]), doc_id=did, content=str(r["content"]),
                          start_char=int(r["start_char"]), end_char=int(r["end_char"]),
                          metadata=dict(r.get("metadata") or {}))
                if arm != "base":
                    c.symbol_glossary = glossaries[arm].for_chunk(
                        c.content, att[arm].config, start_char=c.start_char)
                arms[arm].append(c)
    arms["valnn"] = arms["val"]
    counts = {f"{a}_chunks_with_glossary": sum(1 for c in arms[a] if c.symbol_glossary)
              for a in ("mention", "val")}
    return arms, counts


async def rerank_all(url: str, specs: list[dict[str, Any]], pools: list[list[tuple[str, str]]],
                     content: dict[str, str], memo: dict[Any, list[int]]) -> list[list[tuple[str, str]]]:
    import httpx

    sem = asyncio.Semaphore(MAX_RERANK_INFLIGHT)
    out: list[list[tuple[str, str]]] = [[] for _ in pools]
    async with httpx.AsyncClient(timeout=120) as http:
        async def one(i: int) -> None:
            pool = pools[i][:RERANK_POOL]
            key = (specs[i]["query"], tuple(c for c, _ in pool))
            if key not in memo:
                async with sem:
                    r = await http.post(f"{url.rstrip('/')}/rerank", json={
                        "query": specs[i]["query"], "documents": [content[c] for c, _ in pool],
                        "top_k": len(pool)})
                    r.raise_for_status()
                    memo[key] = list(r.json()["indices"])
            out[i] = [pool[j] for j in memo[key]]

        await asyncio.gather(*(one(i) for i in range(len(pools))))
    return out


def pair_view(specs: list[dict[str, Any]], res: list[dict[str, Any]]) -> dict[str, Any]:
    """Per B pair: best rank over run/keyword queries, and #queries in top 10 / 50."""
    out: dict[str, Any] = {}
    for s, r in zip(specs, res, strict=True):
        if s["kind"] != "B" or s["origin"] not in gx.GO_ORIGINS:
            continue
        rec = out.setdefault(s["pair"], {"group": "symbol" if s["field"] in gx.SYMBOL_FIELDS else s["field"],
                                         "best": None, "in10": 0, "in50": 0, "n": 0})
        rec["n"] += 1
        rank = r["rank"]
        if rank:
            rec["best"] = rank if rec["best"] is None else min(rec["best"], rank)
            rec["in10"] += rank <= 10
            rec["in50"] += rank <= 50
    return out


def row(specs: list[dict[str, Any]], ref: list[dict[str, Any]], res: list[dict[str, Any]],
        gl: set[str] | None) -> dict[str, Any]:
    s = gx.summarize(specs, ref, res, gl)
    pv = pair_view(specs, res)
    return {
        "r10_all": s["recall10_all"], "r10_B": s["recall10_B"],
        "sym_in10": sum(1 for p in pv.values() if p["group"] == "symbol" and p["in10"]),
        "sym_in50": sum(1 for p in pv.values() if p["group"] == "symbol" and p["in50"]),
        "icc_in10": sum(1 for p in pv.values() if p["group"] == "index_case_count" and p["in10"]),
        "lost": len(s["lost_top10"]), "gained": len(s["gained_top10"]),
        "gloss10": s["glossary_chunks_in_top10_mean"], "docs10": s["mean_distinct_docs_top10"],
        "pairs": pv,
    }


async def main_async(args: argparse.Namespace) -> int:
    prefix = args.scratch_prefix
    date = _dt.date.today().strftime("%Y%m%d")
    es_arms = ["base", "mention", "val", "valnn"]
    names = {a: gx._check_scratch(f"{prefix}r{a}_{date}", prefix) for a in es_arms}
    qname = gx._check_scratch(f"{prefix}rbase_{date}", prefix)

    # Per-leg depth as deployed: top_k (the rerank pool, 50) ×
    # retrieval_candidate_multiplier (2) = 100 per leg before RRF.
    gx.DEPTH = args.leg_depth
    report_depth = args.leg_depth
    rows = [json.loads(line) for line in Path(args.dump).read_text(encoding="utf-8").splitlines() if line.strip()]
    tenants = sorted({str((r.get("metadata") or {}).get("tenant_id", "")) for r in rows} - {""})
    content = {str(r["chunk_id"]): str(r["content"]) for r in rows}
    arms, counts = build(rows, Path(args.llm_cache))
    specs = gx.query_specs(gx.load_pairs(Path(args.pairs)), rows)
    gl = {a: {c.id for c in arms[a] if c.symbol_glossary} for a in ("mention", "val", "valnn")}

    from elasticsearch import AsyncElasticsearch
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    es = AsyncElasticsearch(hosts=args.es_url, request_timeout=120)
    qc = AsyncQdrantClient(url=args.qdrant_url, timeout=120)
    stores = {a: ElasticsearchTextIndex(url=args.es_url, index=names[a], refresh_on_write=False) for a in es_arms}
    report: dict[str, Any] = {"stats": {**counts, "records": len(specs),
                                        "records_B": sum(s["kind"] == "B" for s in specs)},
                              "bm25": {}, "hybrid": {}, "rerank": {}}
    report["stats"]["leg_depth"] = report_depth
    try:
        for a, st in stores.items():
            await st.drop_index()
            # Each arm's glossary mapping is EXPLICIT: the store's own mapping
            # is now norms:false, so "norms on" must be stated, not inherited.
            mapping = copy.deepcopy(_MAPPINGS)
            mapping["properties"]["symbol_glossary"] = (
                {"type": "text", "norms": False} if a == "valnn" else {"type": "text", "norms": True}
            )
            await es.indices.create(index=names[a], mappings=mapping)
            await st.index(arms[a])
            await st.refresh()
        for a in ("mention", "val", "valnn"):
            report["stats"][f"{a}_mapping"] = (await es.indices.get_mapping(index=names[a]))[
                names[a]]["mappings"]["properties"]["symbol_glossary"]

        # Dense: base vectors only (the glossary is never embedded).
        urls = [u for u in args.embed_urls.split(",") if u]
        qvecs = await gx.embed_all([s["query"] for s in specs], urls, args.embed_model)
        vecs = await gx.embed_all([c.content for c in arms["base"]], urls, args.embed_model)
        if await qc.collection_exists(qname):
            await qc.delete_collection(qname)
        await qc.create_collection(qname, vectors_config=VectorParams(size=len(vecs[0]), distance=Distance.COSINE))
        base_chunks = arms["base"]
        for i in range(0, len(base_chunks), 256):
            await qc.upsert(qname, points=[
                PointStruct(id=j, vector=vecs[j], payload={"chunk_id": base_chunks[j].id, "doc_id": base_chunks[j].doc_id})
                for j in range(i, min(i + 256, len(base_chunks)))])
        dense = []
        for v in qvecs:
            resp = await qc.query_points(qname, query=v, limit=gx.DEPTH, with_payload=True)
            dense.append([(p.payload["chunk_id"], p.payload["doc_id"]) for p in resp.points])  # type: ignore[index]

        configs = [("base", 0.0)] + [(a, b) for a in ("mention", "val", "valnn") for b in args.boosts]
        memo: dict[Any, list[int]] = {}
        refs: dict[str, list[dict[str, Any]]] = {}
        for arm, boost in configs:
            bm = await gx.bm25_lists(es, names[arm], specs, tenants, boost, "most_fields")
            hy = [gx.rrf(a, b) for a, b in zip(bm, dense, strict=True)]
            for cap in (0, args.cap):
                label = f"{arm}" + (f"@{boost:g}" if arm != "base" else "") + (f" cap{cap}" if cap else "")
                # As deployed: retriever.retrieve() applies shape() (the cap)
                # to the fused list BEFORE the cut to the rerank pool, and the
                # query router applies it again AFTER the rerank.
                bm_c = [cap_per_doc(lst, cap) for lst in bm]
                hy_c = [cap_per_doc(lst, cap) for lst in hy]
                rr = await rerank_all(args.rerank_url, specs, hy_c, content, memo)
                rr_c = [cap_per_doc(lst, cap) for lst in rr]
                for leg, lists in (("bm25", bm_c), ("hybrid", hy_c), ("rerank", rr_c)):
                    res = gx.to_results(specs, lists)
                    if label == "base":
                        refs[leg] = res
                    report[leg][label] = row(specs, refs[leg], res, gl.get(arm))
        report["stats"]["rerank_calls"] = len(memo)
    finally:
        for a, st in stores.items():
            gx._check_scratch(names[a], prefix)
            await st.drop_index()
            await st.close()
        gx._check_scratch(qname, prefix)
        if await qc.collection_exists(qname):
            await qc.delete_collection(qname)
        left = [i["index"] for i in await es.cat.indices(h="index", format="json")
                if i["index"].startswith(gx.REQUIRED_PREFIX)]
        left += [f"qdrant:{c.name}" for c in (await qc.get_collections()).collections
                 if c.name.startswith(gx.REQUIRED_PREFIX)]
        await es.close()
        await qc.close()
        report["scratch_left_after_cleanup"] = left
        print(f"scratch indices/collections left after cleanup: {left}", file=sys.stderr)
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 1 if left else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", required=True)
    p.add_argument("--qdrant-url", required=True)
    p.add_argument("--embed-urls", required=True)
    p.add_argument("--embed-model", required=True)
    p.add_argument("--rerank-url", required=True, help="cross-encoder sidecar, read-only use")
    p.add_argument("--pairs", required=True)
    p.add_argument("--llm-cache", required=True)
    p.add_argument("--dump", required=True)
    p.add_argument("--scratch-prefix", default=gx.REQUIRED_PREFIX)
    p.add_argument("--boosts", default="0.5,1,2", type=lambda s: [float(x) for x in s.split(",") if x])
    p.add_argument("--cap", type=int, default=2, help="retrieval_max_per_doc for the capped rows")
    p.add_argument("--leg-depth", type=int, default=100,
                   help="per-leg fetch before RRF (live: top_k 50 x candidate_multiplier 2)")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    gx._check_scratch(args.scratch_prefix, args.scratch_prefix)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
