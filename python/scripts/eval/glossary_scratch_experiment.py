#!/usr/bin/env python
"""#754 fix A offline experiment: does a per-document symbol glossary field bring
the target chunk into the BM25 top 50, without moving recall@10 elsewhere — and
how does it compare with a generic contextual-retrieval baseline?

BM25 arms, each a SCRATCH copy of one live index built through the real
``ElasticsearchTextIndex`` (so the mapping under test is the code's own):

* ``base``  — the live chunks verbatim (also a fidelity check against live ranks);
* ``rules`` — + ``symbol_glossary`` from the rules extractor;
* ``llm``   — + ``symbol_glossary`` from the cached LLM glossary where the cache
  has the document, rules otherwise (exactly what ingest does: "rules+LLM");
* ``union`` — rules and LLM definitions merged per document. Not what ingest
  does; measured to see whether a merge would add;
* ``ctx``   — (``--ctx-cache``) Anthropic "Contextual Retrieval" (2024): a
  per-chunk LLM context sentence PREPENDED to ``content``
  (``scripts/eval/contextual_chunks.py``). Evaluation only.

Glossary arms are queried at every ``--boosts`` value with the code's own
``_build_query`` (``most_fields``), plus a ``best_fields`` variant; boost 0 on a
glossary arm must reproduce ``base`` exactly.

Dense / hybrid (``--dense``): the base text and the contextualised text are
embedded with the collection's own embedder into scratch Qdrant collections
(``<prefix>base_<date>``, ``<prefix>ctx_<date>``); hybrid = RRF (k=60) of the
BM25 top 200 and the dense top 200. Glossary arms reuse the base vectors (the
glossary is never embedded).

Safety, enforced here rather than by discipline:

* the live index is read through :func:`ragstack.ingestion.reassembly.scroll_index`
  — PIT + search only, no write API reachable; the live Qdrant collection is
  never opened;
* every index/collection this script creates, writes or deletes must start with
  ``--scratch-prefix``, which must itself start with ``scratch754_``;
* there is no default ES/Qdrant/embedding URL;
* scratch indices AND collections are deleted at the end and the deletion
  VERIFIED by listing ``_cat/indices`` and Qdrant ``/collections``.

The eval set is private: the report holds pair ids, ranks and counts only —
never query text, quotes or context sentences.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ragstack.ingestion.reassembly import reassemble, scroll_index
from ragstack.ingestion.symbol_glossary import (
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
)
from ragstack.ingestion.symbol_glossary_llm import merged
from ragstack.models import Chunk
from ragstack.stores.elasticsearch import ElasticsearchTextIndex, _build_query

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextual_chunks as ctxmod  # noqa: E402

REQUIRED_PREFIX = "scratch754_"
SYMBOL_FIELDS = {"breathing_flow_m3_s", "deactivation_rate_s", "effective_radius_m"}
GO_ORIGINS = ("run_query", "keyword")
REGRESSION_ORIGINS = ("run_query", "keyword", "quote_terms")
DEPTH = 200
RRF_K = 60


def _check_scratch(name: str, prefix: str) -> str:
    if not prefix.startswith(REQUIRED_PREFIX) or not name.startswith(prefix):
        raise SystemExit(f"refusing to write {name!r}: not under {prefix!r} / {REQUIRED_PREFIX!r}")
    return name


async def dump_live(es_url: str, index: str, out: Path) -> list[dict[str, Any]]:
    """Read-only dump (PIT + search_after) of the live index's chunk sources."""
    from elasticsearch import AsyncElasticsearch

    es = AsyncElasticsearch(hosts=es_url, request_timeout=120)
    rows: list[dict[str, Any]] = []
    try:
        async for hit in scroll_index(es, index):
            rows.append(hit["_source"])
    finally:
        await es.close()
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return rows


def build_arms(
    rows: list[dict[str, Any]],
    llm_cache: Path | None,
    ctx_cache: Path | None,
    ctx_model: str,
    ctx_window: int,
) -> tuple[dict[str, list[Chunk]], dict[str, Any]]:
    """Group by doc, reassemble, attach. Returns chunks per arm + stats."""
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_doc[str(r["doc_id"])].append(r)
    rules_att = SymbolGlossaryAttacher()
    llm_att = SymbolGlossaryAttacher(cache=SymbolGlossaryCache(llm_cache)) if llm_cache else None
    contexts = ctxmod.load_cache(ctx_cache) if ctx_cache else None
    arms: dict[str, list[Chunk]] = {"base": [], "rules": [], "llm": [], "union": []}
    if contexts is not None:
        arms["ctx"] = []
    counts: Counter[str] = Counter()
    for doc_id in sorted(by_doc):
        doc_rows = sorted(by_doc[doc_id], key=lambda r: int(r["start_char"]))
        re_ = reassemble(doc_id, doc_rows)
        counts["reassembly_gaps"] += re_.gaps
        counts["reassembly_length_mismatches"] += re_.length_mismatches

        def chunks(rows_: list[dict[str, Any]] = doc_rows, did: str = doc_id) -> list[Chunk]:
            return [
                Chunk(id=str(r["chunk_id"]), doc_id=did, content=str(r["content"]),
                      start_char=int(r["start_char"]), end_char=int(r["end_char"]),
                      metadata=dict(r.get("metadata") or {}))
                for r in rows_
            ]

        arms["base"].extend(chunks())
        rules_g = rules_att.glossary_for(re_.text)
        llm_g = llm_att.glossary_for(re_.text) if llm_att else rules_g
        union_g = merged(rules_g, llm_g) if llm_g.version != rules_g.version else rules_g
        counts["llm_docs_from_cache"] += int(llm_g.version != rules_g.version)
        for arm, g in (("rules", rules_g), ("llm", llm_g), ("union", union_g)):
            cs = chunks()
            for c in cs:
                c.symbol_glossary = g.for_chunk(c.content, rules_att.config, start_char=c.start_char)
            counts[f"{arm}_entries"] += len(g.entries)
            counts[f"{arm}_ambiguous_symbols"] += len(g.ambiguous_symbols)
            counts[f"{arm}_docs_with_glossary"] += int(any(c.symbol_glossary for c in cs))
            counts[f"{arm}_chunks_with_glossary"] += sum(1 for c in cs if c.symbol_glossary)
            arms[arm].extend(cs)
        if contexts is not None:
            cs = chunks()
            for c in cs:
                win = ctxmod.window_for(re_.text, c.start_char, ctx_window)
                ctx = contexts.get(ctxmod.cache_key(win, c.content, ctx_model))
                if ctx:
                    c.content = f"{ctx}\n\n{c.content}"
                    counts["ctx_chunks_with_context"] += 1
                else:
                    counts["ctx_chunks_missing_context"] += 1
            arms["ctx"].extend(cs)
    stats: dict[str, Any] = {"docs": len(by_doc), "chunks": len(rows), **counts}
    return arms, stats


def load_pairs(pairs_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in pairs_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(text: str) -> str:
    """The eval set's own matching rule: NFKC, case- and whitespace-insensitive
    (README: 'Matching is NFKC-normalised, case- and whitespace-insensitive')."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def query_specs(pairs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per (pair, query) with openchia expected chunks — the same
    selection ``arms.py`` made for arms_results.json.

    The target set is the listed ``expected_chunks`` PLUS every chunk whose text
    contains the expected quote — the README's instruction ("score a hit on
    chunk text, not on doc_id"): openchia holds the reference paper in several
    copies, and an identical caption chunk of another copy is the same answer."""
    norm_rows = [(str(r["chunk_id"]), str(r["doc_id"]), _norm(str(r["content"]))) for r in rows]
    specs = []
    for p in pairs:
        exp = [c for c in (p.get("expected_chunks") or []) if c.get("collection") == "openchia"]
        if not exp:
            continue
        quote = _norm(str((p.get("expected") or {}).get("quote") or ""))
        if quote:
            extra = [{"chunk_id": cid, "doc_id": did} for cid, did, txt in norm_rows if quote in txt]
            exp = exp + extra
        for i, q in enumerate(p.get("queries") or []):
            if q.get("origin") not in REGRESSION_ORIGINS:
                continue
            specs.append({"pair": p["id"], "kind": p.get("kind"), "field": p.get("field"),
                          "origin": q["origin"], "qi": i, "query": q["query"],
                          "want": {c["chunk_id"] for c in exp}, "docs": {c["doc_id"] for c in exp}})
    return specs


async def bm25_lists(es: Any, index: str, specs: list[dict[str, Any]], tenants: list[str],
                     boost: float, mode: str) -> list[list[tuple[str, str]]]:
    out = []
    for s in specs:
        body = _build_query(s["query"], {"tenant_id": tenants}, boost)
        if mode == "best_fields" and boost > 0:
            body["bool"]["must"][0]["multi_match"]["type"] = "best_fields"
        resp = await es.search(index=index, query=body, size=DEPTH, _source=["chunk_id", "doc_id"])
        out.append([(h["_source"]["chunk_id"], h["_source"]["doc_id"]) for h in resp["hits"]["hits"]])
    return out


def rrf(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> list[tuple[str, str]]:
    score: dict[tuple[str, str], float] = defaultdict(float)
    for lst in (a, b):
        for i, item in enumerate(lst):
            score[item] += 1.0 / (RRF_K + i + 1)
    return [k for k, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))][:DEPTH]


def to_results(specs: list[dict[str, Any]], lists: list[list[tuple[str, str]]]) -> list[dict[str, Any]]:
    res = []
    for s, lst in zip(specs, lists, strict=True):
        ids = [c for c, _ in lst]
        docs = [d for _, d in lst]
        res.append({"rank": next((i + 1 for i, c in enumerate(ids) if c in s["want"]), None),
                    "top10_docs": len(set(docs[:10])),
                    "target_doc_slots_top10": sum(1 for d in docs[:10] if d in s["docs"]),
                    "top10": ids[:10]})
    return res


def summarize(specs: list[dict[str, Any]], base: list[dict[str, Any]], arm: list[dict[str, Any]],
              glossaried: set[str] | None = None) -> dict[str, Any]:
    def r10(res: list[dict[str, Any]], sel: list[int]) -> float:
        return round(sum(1 for i in sel if res[i]["rank"] and res[i]["rank"] <= 10) / max(1, len(sel)), 4)

    def in10(r: dict[str, Any]) -> bool:
        return bool(r["rank"] and r["rank"] <= 10)

    all_idx = list(range(len(specs)))
    b_idx = [i for i, s in enumerate(specs) if s["kind"] == "B"]
    pairs: dict[str, Any] = {}
    for i, s in enumerate(specs):
        if s["kind"] != "B":
            continue
        group = "symbol" if s["field"] in SYMBOL_FIELDS else s["field"]
        rec = pairs.setdefault(s["pair"], {"group": group, "queries": [], "entered_top50": False,
                                           "in_top50": False})
        br, ar = base[i]["rank"], arm[i]["rank"]
        rec["queries"].append({"origin": s["origin"], "qi": s["qi"], "base": br, "arm": ar})
        if s["origin"] in GO_ORIGINS and ar is not None and ar <= 50:
            rec["in_top50"] = True
            if br is None or br > 50:
                rec["entered_top50"] = True
    return {
        "recall10_all": r10(arm, all_idx), "recall10_all_base": r10(base, all_idx),
        "recall10_B": r10(arm, b_idx), "recall10_B_base": r10(base, b_idx),
        "records_all": len(all_idx), "records_B": len(b_idx),
        "lost_top10": [i for i in all_idx if in10(base[i]) and not in10(arm[i])],
        "gained_top10": [i for i in all_idx if in10(arm[i]) and not in10(base[i])],
        "top10_lists_changed": sum(1 for i in all_idx if base[i]["top10"] != arm[i]["top10"]),
        "glossary_chunks_in_top10_mean": round(statistics.mean(
            sum(1 for c in r["top10"] if c in glossaried) for r in arm), 3) if (arm and glossaried) else 0,
        "mean_distinct_docs_top10": round(statistics.mean(r["top10_docs"] for r in arm), 3) if arm else 0,
        "symbol_pairs_entered_top50": sum(1 for p in pairs.values() if p["group"] == "symbol" and p["entered_top50"]),
        "symbol_pairs_in_top50": sum(1 for p in pairs.values() if p["group"] == "symbol" and p["in_top50"]),
        "icc_pairs_entered_top50": sum(1 for p in pairs.values() if p["group"] == "index_case_count" and p["entered_top50"]),
        "icc_pairs_in_top50": sum(1 for p in pairs.values() if p["group"] == "index_case_count" and p["in_top50"]),
        "pairs": pairs,
    }


async def embed_all(texts: list[str], urls: list[str], model: str, batch: int = 32,
                    per_endpoint: int = 2) -> list[list[float]]:
    """Embed ``texts`` against the collection's embedder endpoints (stateless,
    read-only use), round-robin, bounded concurrency."""
    import httpx

    out: list[list[float] | None] = [None] * len(texts)
    sem = {u: asyncio.Semaphore(per_endpoint) for u in urls}
    async with httpx.AsyncClient(timeout=300) as http:
        async def one(i0: int, url: str) -> None:
            async with sem[url]:
                for attempt in range(3):
                    try:
                        r = await http.post(f"{url.rstrip('/')}/v1/embeddings",
                                            json={"model": model, "input": texts[i0 : i0 + batch]})
                        r.raise_for_status()
                        for j, d in enumerate(r.json()["data"]):
                            out[i0 + j] = d["embedding"]
                        return
                    except Exception:  # noqa: BLE001
                        await asyncio.sleep(2 * (attempt + 1))
                raise RuntimeError("embedding failed")

        await asyncio.gather(*(one(i, urls[(i // batch) % len(urls)]) for i in range(0, len(texts), batch)))
    return [v for v in out if v is not None]


async def main_async(args: argparse.Namespace) -> int:
    prefix = args.scratch_prefix
    date = _dt.date.today().strftime("%Y%m%d")
    arm_names = ["base", "rules", "llm", "union"] + (["ctx"] if args.ctx_cache else [])
    names = {arm: _check_scratch(f"{prefix}{arm}_{date}", prefix) for arm in arm_names}
    qnames = {arm: _check_scratch(f"{prefix}{arm}_{date}", prefix) for arm in ("base", "ctx")}

    dump = Path(args.dump)
    if dump.exists():
        rows = [json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = await dump_live(args.es_url, args.source_index, dump)
    tenants = sorted({str((r.get("metadata") or {}).get("tenant_id", "")) for r in rows} - {""})
    arms, stats = build_arms(rows, Path(args.llm_cache) if args.llm_cache else None,
                             Path(args.ctx_cache) if args.ctx_cache else None,
                             args.ctx_model, args.ctx_window)

    specs = query_specs(load_pairs(Path(args.pairs)), rows)
    have = {str(r["chunk_id"]) for r in rows}
    stats["query_records"] = len(specs)
    stats["query_records_with_target_present"] = sum(1 for s in specs if s["want"] & have)
    stats["distinct_queries"] = len({s["query"] for s in specs})
    stats["query_records_B"] = sum(1 for s in specs if s["kind"] == "B")
    stats["target_chunks_per_record_mean"] = round(statistics.mean(len(s["want"]) for s in specs), 2)

    from elasticsearch import AsyncElasticsearch

    report: dict[str, Any] = {"stats": stats, "tenants": len(tenants), "boosts": args.boosts,
                              "bm25": {}, "hybrid": {}, "dense": {}}
    es_admin = AsyncElasticsearch(hosts=args.es_url, request_timeout=120)
    stores = {arm: ElasticsearchTextIndex(url=args.es_url, index=names[arm], refresh_on_write=False)
              for arm in names}
    qc = None
    try:
        for arm, store in stores.items():
            await store.drop_index()
            await store.ensure_index()
            await store.index(arms[arm])
            await store.refresh()
            report.setdefault("index_counts", {})[arm] = (await es_admin.count(index=names[arm]))["count"]

        # Fidelity: the live index's own ranks vs the base copy (read-only query).
        live = to_results(specs, await bm25_lists(es_admin, args.source_index, specs, tenants, 0.0, "most_fields"))
        gl = {a: {c.id for c in arms[a] if c.symbol_glossary} for a in ("rules", "llm", "union")}
        bm = {"base": await bm25_lists(es_admin, names["base"], specs, tenants, 0.0, "most_fields")}
        base = to_results(specs, bm["base"])
        report["fidelity_rank_agreement"] = sum(1 for a, b in zip(live, base, strict=True)
                                                if a["rank"] == b["rank"]) / max(1, len(specs))
        report["bm25"]["base"] = summarize(specs, base, base)
        for arm in ("rules", "llm", "union"):
            for mode in ("most_fields", "best_fields"):
                for boost in [0.0, *args.boosts]:
                    if boost == 0.0 and mode == "best_fields":
                        continue
                    lists = await bm25_lists(es_admin, names[arm], specs, tenants, boost, mode)
                    bm[f"{arm}/{mode}/{boost:g}"] = lists
                    report["bm25"][f"{arm}/{mode}/{boost:g}"] = summarize(
                        specs, base, to_results(specs, lists), gl[arm])
        if "ctx" in names:
            bm["ctx"] = await bm25_lists(es_admin, names["ctx"], specs, tenants, 0.0, "most_fields")
            report["bm25"]["ctx"] = summarize(specs, base, to_results(specs, bm["ctx"]))

        if args.dense:
            from qdrant_client import AsyncQdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams

            qc = AsyncQdrantClient(url=args.qdrant_url, timeout=120)
            urls = [u for u in args.embed_urls.split(",") if u]
            dense: dict[str, list[list[tuple[str, str]]]] = {}
            t0 = time.monotonic()
            qvecs = await embed_all([s["query"] for s in specs], urls, args.embed_model)
            for arm in ("base", "ctx"):
                if arm not in arms:
                    continue
                cs = arms[arm]
                vecs = await embed_all([c.content for c in cs], urls, args.embed_model)
                name = _check_scratch(qnames[arm], prefix)
                if await qc.collection_exists(name):
                    await qc.delete_collection(name)
                await qc.create_collection(name, vectors_config=VectorParams(size=len(vecs[0]), distance=Distance.COSINE))
                for i in range(0, len(cs), 256):
                    await qc.upsert(name, points=[
                        PointStruct(id=j, vector=vecs[j], payload={"chunk_id": cs[j].id, "doc_id": cs[j].doc_id})
                        for j in range(i, min(i + 256, len(cs)))])
                lists = []
                for v in qvecs:
                    resp = await qc.query_points(name, query=v, limit=DEPTH, with_payload=True)
                    lists.append([(p.payload["chunk_id"], p.payload["doc_id"]) for p in resp.points])  # type: ignore[index]
                dense[arm] = lists
                report["dense"][arm] = summarize(specs, base, to_results(specs, lists))
            report["stats"]["embed_seconds"] = round(time.monotonic() - t0, 1)
            report["stats"]["embedded_texts"] = sum(len(arms[a]) for a in ("base", "ctx") if a in arms) + len(specs)
            hyb_base = to_results(specs, [rrf(a, b) for a, b in zip(bm["base"], dense["base"], strict=True)])
            report["hybrid"]["base"] = summarize(specs, hyb_base, hyb_base)
            for key, lists in bm.items():
                if key == "base" or "/best_fields/" in key:
                    continue
                dk = "ctx" if key == "ctx" else "base"
                fused = to_results(specs, [rrf(a, b) for a, b in zip(lists, dense[dk], strict=True)])
                report["hybrid"][key] = summarize(specs, hyb_base, fused, gl.get(key.split("/")[0]))
    finally:
        if not args.keep:
            for arm, store in stores.items():
                _check_scratch(names[arm], prefix)
                await store.drop_index()
        for store in stores.values():
            await store.close()
        listing = await es_admin.cat.indices(h="index", format="json")
        await es_admin.close()
        left = sorted(i["index"] for i in listing if i["index"].startswith(REQUIRED_PREFIX))
        if args.dense:
            from qdrant_client import AsyncQdrantClient

            qc = qc or AsyncQdrantClient(url=args.qdrant_url, timeout=120)
            if not args.keep:
                for name in qnames.values():
                    _check_scratch(name, prefix)
                    if await qc.collection_exists(name):
                        await qc.delete_collection(name)
            cols = (await qc.get_collections()).collections
            left += sorted(f"qdrant:{c.name}" for c in cols if c.name.startswith(REQUIRED_PREFIX))
            await qc.close()
        report["scratch_left_after_cleanup"] = left
        print(f"scratch indices/collections left after cleanup: {left}", file=sys.stderr)

    Path(args.out).write_text(json.dumps(report, indent=1, default=sorted), encoding="utf-8")
    return 1 if (report["scratch_left_after_cleanup"] and not args.keep) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", required=True, help="dev ES only; no default")
    p.add_argument("--source-index", required=True, help="live index, read-only")
    p.add_argument("--pairs", required=True)
    p.add_argument("--llm-cache", default="", help="LLM glossary cache (scripts/symbol_glossary_llm.py)")
    p.add_argument("--ctx-cache", default="", help="context cache (scripts/eval/contextual_chunks.py)")
    p.add_argument("--ctx-model", default="", help="the model id the context cache was built with")
    p.add_argument("--ctx-window", type=int, default=120_000)
    p.add_argument("--dump", required=True, help="chunk dump path (read if present, else written)")
    p.add_argument("--scratch-prefix", default=REQUIRED_PREFIX)
    p.add_argument("--boosts", default="0.3,0.5,1,2,3",
                   type=lambda s: [float(x) for x in s.split(",") if x])
    p.add_argument("--dense", action="store_true", help="also build scratch Qdrant arms + hybrid")
    p.add_argument("--qdrant-url", default="")
    p.add_argument("--embed-urls", default="", help="comma-separated OpenAI-compatible embedding URLs")
    p.add_argument("--embed-model", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--keep", action="store_true", help="do not delete the scratch indices")
    args = p.parse_args(argv)
    _check_scratch(args.scratch_prefix, args.scratch_prefix)
    if args.dense and not (args.qdrant_url and args.embed_urls and args.embed_model):
        p.error("--dense needs --qdrant-url, --embed-urls and --embed-model (no defaults)")
    if args.ctx_cache and not args.ctx_model:
        p.error("--ctx-cache needs --ctx-model")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
