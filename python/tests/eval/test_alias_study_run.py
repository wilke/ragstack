"""Alias study runner: the study's additions on top of round 3's runner (the admin config hash,
the per-leg failure stop, the noise repeat's seeded order, gold presence, the config shape), one
read-only collection per attachment with its load receipt and live census (owner, 2026-11-03),
the two stages with retrieval and judging stage per leg (§5.7), the placebo leg and its
derangement (§5.3), the modes (pilot, main, the frozen prereg) and that round 3's gates still
fire through it. A fake API and a fake census index, no store."""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_run as ar  # noqa: E402
import found10 as f10  # noqa: E402

ES = "http://es.invalid:9200"


def stamp(g: str) -> str:
    return f"t-{g}@file/1"


def collection(c: str, g: str) -> str:
    return f"bench-t-{c}-{g}"


class API:
    """A fake RAGStack API: ``/v1/retrieve`` returns the record's gold plus filler, stamped with
    the glossary arm of the collection asked for."""

    def __init__(self, world: fx.World) -> None:
        self.w = world
        self.config: Any = {"a": 1}
        self.config_status = 200
        self.config_reads = 0
        self.change_config_after: int | None = None
        self.bodies: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        self.fail: Any = None           # (body) -> bool
        self.same_rerank = False
        self.q2rec = {r["question"]: r for r in world.records}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        if req.url.path == "/v1/version":
            return httpx.Response(200, json={"v": 1})
        if req.url.path == "/v1/config":
            self.config_reads += 1
            if self.change_config_after is not None and self.config_reads > self.change_config_after:
                return httpx.Response(200, json={"a": 2})
            return httpx.Response(self.config_status, json=self.config)
        body = json.loads(req.content)
        self.bodies.append(body)
        if self.fail and self.fail(body):
            return httpx.Response(503, json={})
        rec = self.q2rec[body["query"]]
        g = body["collection"].split("-")[3]
        ids = [rec["gold_chunks"][0]] + [f"F{i}" for i in range(80)]
        if body["retrieval_mode"] == "hybrid" and body.get("rerank") and not self.same_rerank:
            ids = [ids[1], ids[0]] + ids[2:]
        return httpx.Response(200, json={"sources": [{"chunk_id": c, "metadata": {
            "symbol_glossary_version": stamp(g)}} for c in ids[: body["top_k"]]]})


class Census:
    """A fake index host: the stamp census of a collection (``_search`` terms aggregation)."""

    def __init__(self, stamps: dict[str, int] | None = None, total: int = 100) -> None:
        self.stamps, self.total = stamps, total
        self.requests: list[tuple[str, str]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        index = req.url.path.strip("/").split("/")[0]
        g = index.split("_")[2]
        st = self.stamps if self.stamps is not None else {stamp(g): self.total}
        return httpx.Response(200, json={"hits": {"total": {"value": self.total}}, "aggregations": {"v": {
            "buckets": [{"key": k, "doc_count": v} for k, v in st.items()]}}})


def arm_files(tmp: Path) -> dict[str, dict[str, Any]]:
    """One real glossary_file/v1 per glossary arm (none: empty rows), with the record
    ``load_embeddings.py`` writes about it (the real ``file_provenance``)."""
    d = tmp / "glossary-files"
    if not d.is_dir():
        d.mkdir()
        for g in ar.STUDY_GLOSSARY_ARMS:
            prov = dry.real_glossary_file(d / f"{g}.jsonl", f"t-{g}", entries=g != "none")
            (d / f"{g}.prov.json").write_text(json.dumps(prov))
    return {g: json.loads((d / f"{g}.prov.json").read_text()) for g in ar.STUDY_GLOSSARY_ARMS}


def receipt(tmp: Path, c: str, g: str, *, failed: bool = False, **over: Any) -> Path:
    """The load summary of ``load_embeddings.py`` for one collection (the real
    ``merge_summary`` and ``file_provenance``): every arm, none included, loaded from an explicit
    glossary file with its own stamp (the none file renders nothing)."""
    rec = dry.load_summary(100, arm_files(tmp)[g], failed=failed)
    rec.update(over)
    p = tmp / f"receipt-{c}-{g}.json"
    p.write_text(json.dumps(rec))
    return p


def setup(tmp: Path, **over: Any) -> tuple[fx.World, dict[str, Any]]:
    w = fx.World(tmp)
    gold = {r["gold_chunks"][0]: asc.sha_text(c["content"]) for r in w.records
            for cid, c in asc.read_chunk_exports([w.base_export]).items() if cid == r["gold_chunks"][0]}
    (tmp / "gold.json").write_text(json.dumps(gold))
    corpora = {}
    cols: dict[str, dict[str, Any]] = {}
    for a in asc.CORPUS_ARMS:
        exp = [str(w.base_export)] + ([str(w.exports[a])] if a != "base" else [])
        corpora[a] = {"expected_chunks": 100, "chunk_export": exp}
        for g in ar.STUDY_GLOSSARY_ARMS:
            if g == "placebo" and a != "base":
                continue
            cols.setdefault(a, {})[g] = {"collection": collection(a, g), "index": f"ix_{a}_{g}",
                                         "load_receipt": str(receipt(tmp, a, g))}
    files = arm_files(tmp)
    arms = {}
    for g in ("none", "merged", "placebo"):
        arms[g] = {"stamps": [f"t-{g}"], "glossary_file_sha256": dict.fromkeys(asc.CORPUS_ARMS, files[g]["sha256"])}
    cfg = {"api": {"base_url": "http://api.invalid", "retry_backoff_s": 0.0, "retries": 1}, "corpora": corpora,
           "collections": cols, "arms": arms, "records": str(w.records_path), "out_dir": str(tmp / "runs"),
           "gold_hashes": str(tmp / "gold.json"), "dry_run": True, **over}
    return w, cfg


def run(cfg: dict[str, Any], api: API, corpus: str = "base", glossary: str = "none",
        repeat: int | None = None, stage: str = "A", census: Census | None = None) -> int:
    c = ar.validate_config(cfg)
    recs, ident = ar.load_study_records(c)
    kw: dict[str, Any] = {}
    if census is not None:
        kw["census_client_factory"] = lambda: httpx.AsyncClient(transport=httpx.MockTransport(census.handler))
    r = ar.build_runner(c, recs, ident, corpus, glossary, repeat=repeat, boost_declared=1.0, stage=stage,
                        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(api.handler)), **kw)
    return asyncio.run(r.run())


def summary(cfg: dict[str, Any], corpus: str, label: str) -> dict[str, Any]:
    return json.loads((Path(cfg["out_dir"]) / corpus / f"{label}.summary.json").read_text())


def test_complete_leg_with_config_hash(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    assert run(cfg, api) == 0
    s = summary(cfg, "base", "none")
    assert s["status"] == "complete" and s["study"]["config_hash_identical"] is True
    # retrieval stage A: every retriever of a Layer-1 attachment, in one run (owner, 2026-11-03)
    assert s["study"]["retrievers"] == ["default", "bm25", "norerank"] and s["study"]["stage"] == "A"
    assert s["study"]["leg_stages"] == {"base|none|default": {"retrieval": "A", "judging": "A"},
                                        "base|none|bm25": {"retrieval": "A", "judging": "B"},
                                        "base|none|norerank": {"retrieval": "A", "judging": "A"}}
    assert s["study"]["gold_presence"]["present"] == len({r["gold_chunks"][0] for r in w.records})
    assert s["census"]["collection_build"]["collection"] == collection("base", "none")
    assert s["census"]["swap_record_sha256"] is None and s["census"]["source"] == "load_receipt"
    assert api.config_reads == 2 and len(api.bodies) == 3 * len(w.records)
    assert {b["collection"] for b in api.bodies} == {collection("base", "none")}
    assert all(b["use_graph"] is False and b["rewrite_strategies"] == ["passthrough"] for b in api.bodies)
    assert {b["top_k"] for b in api.bodies} == {50}


def test_config_change_during_leg_is_a_hard_stop(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    api.change_config_after = 1
    assert run(cfg, api) == f10.EXIT_REFUSED
    s = summary(cfg, "base", "none")
    assert s["status"] == "hard_stop" and "differs" in s["reason"]


def test_unreadable_config_refuses_before_measuring(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    api.config_status = 403
    with pytest.raises(f10.Refused, match="admin"):
        run(cfg, api)
    assert api.bodies == []


def test_failed_calls_over_2pc_in_one_leg_stop(tmp_path: Path) -> None:
    """1 of 38 bm25 calls fails: 2.6 % of that leg, 0.9 % of the run (round 3 would go on)."""
    w, cfg = setup(tmp_path)
    api = API(w)
    victim = w.records[0]["question"]
    api.fail = lambda b: b["retrieval_mode"] == "bm25" and b["query"] == victim
    assert run(cfg, api) == f10.EXIT_REFUSED
    s = summary(cfg, "base", "none")
    assert s["status"] == "hard_stop" and "bm25 leg" in s["reason"] and s["failed_share"] < 0.02


def test_noise_repeat_is_default_only_in_seeded_order(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    assert run(cfg, api, repeat=2) == 0
    s = summary(cfg, "base", "none#2")
    assert list(s["conditions"]) == ["base"] and s["study"]["seeds"] == {"noise": asc.SEEDS["noise"]}
    sent = [b["query"] for b in api.bodies]
    q2id = {r["question"]: r["record_id"] for r in w.records}
    want = sorted(q2id.values(), key=lambda rid: asc.seeded_key(asc.SEEDS["noise"], "base", rid, "base"))
    assert [q2id[q] for q in sent] == want and want != [r["record_id"] for r in w.records]
    with pytest.raises(f10.Refused, match="default x none"):
        run(cfg, api, glossary="merged", repeat=2)


def test_every_layer1_gate_is_readable_after_stage_a(tmp_path: Path) -> None:
    """Owner, 2026-11-03: the inactive-reranker gate (§9.1) is computed inside the stage-A run of
    each of the five Layer-1 attachments, from stage-A legs alone."""
    attach = {(c, g) for c, g, r, rep in ar.stage_legs("A") if rep == 1}
    assert attach == set(ar.LAYER1_ATTACHMENTS)
    for c, g in ar.LAYER1_ATTACHMENTS:
        assert ar.leg_stage(c, g, "default") == ar.leg_stage(c, g, "norerank") == "A"
        assert ar.judging_stage(c, g, "default") == ar.judging_stage(c, g, "norerank") == "A"
    for same in (True, False):
        tmp = tmp_path / str(same)
        tmp.mkdir()
        w, cfg = setup(tmp)
        api = API(w)
        api.same_rerank = same
        for c, g in ar.LAYER1_ATTACHMENTS:
            assert run(cfg, api, corpus=c, glossary=g) == 0
            base = summary(cfg, c, g)["conditions"]["base"]
            assert base["identical_to_norerank"]["records"] == len(w.records)
            assert ("reranker was not active" in (base["gate"] or "")) is same


# ------------------------------------------------------------------ collections (owner, 2026-11-03)

def test_live_census_refuses_another_arms_stamps(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path, census={"es_url": ES})
    api = API(w)
    assert run(cfg, api, census=Census()) == 0                               # its own stamps: measured
    s = summary(cfg, "base", "none")
    assert s["census"]["source"] == "load_receipt+live" and s["census"]["live_after"]["total"] == 100
    for bad in ({stamp("merged"): 100}, {stamp("none"): 60, stamp("merged"): 40}, {"<none>": 100},
                {stamp("none"): 99}):
        tmp = tmp_path / str(len(list(tmp_path.iterdir())))
        tmp.mkdir()
        w2, cfg2 = setup(tmp, census={"es_url": ES})
        api2 = API(w2)
        with pytest.raises(f10.Refused, match="live census"):
            run(cfg2, api2, census=Census(bad))
        assert api2.bodies == []


def test_merged_collection_must_hold_the_loaded_files_versions(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path, census={"es_url": ES})
    # a loose arm config (a prefix) admits the stamp; the loaded file's versions still refuse it
    cfg["arms"]["merged"]["stamps"] = []
    cfg["arms"]["merged"]["stamp_prefixes"] = ["t-mer"]
    with pytest.raises(f10.Refused, match="live census"):
        run(cfg, API(w), glossary="merged", census=Census({"t-merx@file/1": 100}))


def test_load_receipt_is_the_build_manifest(tmp_path: Path) -> None:
    def none_prov(tmp: Path, **counts: Any) -> dict[str, Any]:
        p = dict(arm_files(tmp)["none"])
        p["counts"] = {**p["counts"], **counts}
        return p

    cases = (("none", lambda t: {"failed": True}, "failed shards"),
             ("none", lambda t: {"n_chunks": 99}, "loaded 99 chunks"),
             ("none", lambda t: {"glossary_file": None}, "none collection was loaded without a glossary file"),
             ("none", lambda t: {"glossary_file": arm_files(t)["merged"]}, "another glossary file"),
             ("none", lambda t: {"glossary_file": dry.real_glossary_file(t / "none-with-entries.jsonl", "t-none",
                                                                         entries=True)}, "another glossary file"),
             ("none", lambda t: {"glossary_file": {"symbol_glossary": arm_files(t)["none"]}},
              "another glossary file"),                                  # the shape no loader writes
             ("merged", lambda t: {"glossary_file": None}, "without a glossary file"),
             ("merged", lambda t: {"glossary_file": arm_files(t)["placebo"]}, "another glossary file"))
    for g, over, msg in cases:
        tmp = tmp_path / str(len(list(tmp_path.iterdir())))
        tmp.mkdir()
        w, cfg = setup(tmp)
        o = over(tmp)
        failed = bool(o.pop("failed", False))
        cfg["collections"]["base"][g]["load_receipt"] = str(receipt(tmp, "base", g, failed=failed, **o))
        api = API(w)
        with pytest.raises(f10.Refused, match=msg):
            run(cfg, api, glossary=g)
        assert api.bodies == []


def test_a_none_file_that_renders_is_refused(tmp_path: Path) -> None:
    """The none arm's file holds empty rows: a file with entries, declared as the none arm's,
    is refused on its counts."""
    w, cfg = setup(tmp_path)
    prov = dry.real_glossary_file(tmp_path / "none-with-entries.jsonl", "t-none", entries=True)
    cfg["arms"]["none"]["glossary_file_sha256"]["base"] = prov["sha256"]
    cfg["collections"]["base"]["none"]["load_receipt"] = str(receipt(tmp_path, "base", "none", glossary_file=prov))
    with pytest.raises(f10.Refused, match="renders a field"):
        run(cfg, API(w))
    gone = {**prov, "counts": {k: v for k, v in prov["counts"].items() if k != "merged_entries"}}
    cfg["collections"]["base"]["none"]["load_receipt"] = str(receipt(tmp_path, "base", "none", glossary_file=gone))
    with pytest.raises(f10.Refused, match="renders a field"):
        run(cfg, API(w))


def real_load(tmp: Path, *, none: bool) -> tuple[dict[str, Any], Path]:
    """``load_embeddings.py`` run for real (memory stores, dead store URLs) on an embedded shard
    with a glossary file built and re-derived by ``glossary_file.py`` (none: ``--merge none``;
    both ``--attach mention``). Returns the summary it writes and the glossary file."""
    from ragstack.ingestion.chunkers import RecursiveCharacterChunker
    from ragstack.ingestion.embed_shard import run_embed_shard
    from ragstack.ingestion.loaders import JsonlLoader
    from ragstack.ingestion.pipeline import IngestionPipeline
    from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

    scripts = Path(ar.__file__).resolve().parents[1]
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import glossary_file as gf_cli
    import load_embeddings as load_cli

    class Emb:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(t)), 1.0, 2.0] for t in texts]

    docs = {"a": "Methods\n\nThe flux N = Qb Cq, where Qb is the breathing flow rate. " + "words " * 60,
            "b": "We use the rate λv, the viral deactivation rate, here. " + "more words " * 40}
    shard = tmp / "shard.jsonl"
    shard.write_text("".join(json.dumps({"text": t, "path": f"{k}.pdf", "metadata": {}}) + "\n"
                             for k, t in docs.items()))
    emb = tmp / "shard.emb.jsonl"
    pipe = IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(chunk_size=120, chunk_overlap=20),
                             embedder=Emb(), vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex())
    assert asyncio.run(run_embed_shard(pipe, str(shard), "public", "s0", emb)).status == "completed"
    g0, g1 = tmp / "g0.jsonl", tmp / "g1.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--out", str(g0)]) == 0
    assert gf_cli.main(["rederive", *(["--merge", "none"] if none else []), "--attach", "mention", str(g0),
                        "--out", str(g1)]) == 0
    out = tmp / "load-summary.json"
    rc = load_cli.main([str(emb), "--glossary-file", str(g1), "--out", str(out), "--collection-id", "bench-t-real",
                        "--vector-backend", "memory", "--text-backend", "memory",
                        "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1"])
    assert rc == 0
    return json.loads(out.read_text()), g1


def test_a_real_load_summary_is_read_as_written(tmp_path: Path) -> None:
    """The build manifest read from a summary ``load_embeddings.py`` actually wrote: the none
    arm from a re-derived empty file passes, its versions are the file's; a merged file declared
    as the none arm is refused on its counts and passes as the merged arm."""
    for none in (True, False):
        tmp = tmp_path / ("none" if none else "merged")
        tmp.mkdir()
        rec, gfile = real_load(tmp, none=none)
        w, cfg = setup(tmp)
        for g in ("none", "merged"):
            cfg["arms"][g]["glossary_file_sha256"]["base"] = asc.sha256_file(gfile)
            cfg["arms"][g]["stamp_prefixes"] = []
        cfg["corpora"]["base"]["expected_chunks"] = rec["n_chunks"]
        for g in ("none", "merged"):
            cfg["collections"]["base"][g]["load_receipt"] = str(tmp / "load-summary.json")
        c = ar.validate_config(cfg)
        if none:
            b = ar.check_load_receipt(c, "base", "none")
            assert b["glossary_file_sha256"] == asc.sha256_file(gfile) and b["n_chunks"] == rec["n_chunks"] > 0
            assert b["glossary_versions"] and all(v.endswith("+merge=none+attach=mention")
                                                  for v in b["glossary_versions"])
        else:
            with pytest.raises(f10.Refused, match="renders a field"):
                ar.check_load_receipt(c, "base", "none")
            assert ar.check_load_receipt(c, "base", "merged")["n_shards"] == 1


def test_none_from_an_explicit_empty_file_passes(tmp_path: Path) -> None:
    """Reviewer's probe (prereg §5.3, §9.2): the none collection loaded from an explicit file of
    empty rows with its own stamp is measured, census included."""
    w, cfg = setup(tmp_path, census={"es_url": ES})
    assert run(cfg, API(w), census=Census()) == 0
    b = summary(cfg, "base", "none")["census"]["collection_build"]
    assert b["glossary_file_sha256"] == arm_files(tmp_path)["none"]["sha256"] and b["glossary_versions"] == ["t-none"]


def test_none_without_a_file_is_refused(tmp_path: Path) -> None:
    """Reviewer's probe: a none collection loaded with glossary_file null (unstamped chunks)."""
    w, cfg = setup(tmp_path, census={"es_url": ES})
    cfg["collections"]["base"]["none"]["load_receipt"] = str(receipt(tmp_path, "base", "none", glossary_file=None))
    api = API(w)
    with pytest.raises(f10.Refused, match="without a glossary file"):
        run(cfg, api, census=Census({"<none>": 100}))
    assert api.bodies == []


def test_census_refusal_names_the_load_receipt(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path, census={"es_url": ES})
    with pytest.raises(f10.Refused, match="the load receipt's") as e:
        run(cfg, API(w), census=Census({stamp("merged"): 100}))
    assert "swap record" not in str(e.value)


def test_mention_stamps_need_the_attach_rule(tmp_path: Path) -> None:
    _, cfg = setup(tmp_path)
    c = json.loads(json.dumps(cfg))
    c["arms"]["merged"]["stamps"] = ["t-merged+attach=mention"]
    with pytest.raises(f10.Refused, match='attach: "mention"'):
        ar.validate_config(c)
    c["arms"]["merged"]["attach"] = "mention"
    ar.validate_config(c)


def test_no_write_path_is_reachable_from_the_runner(tmp_path: Path) -> None:
    """The runner reads: the API's version, config and retrieve, the index's census and counts.
    No backfill, swap or store-write code is imported by it."""
    w, cfg = setup(tmp_path, census={"es_url": ES})
    api, cen = API(w), Census()
    assert run(cfg, api, census=cen) == 0
    assert set(api.requests) <= {("GET", "/v1/version"), ("GET", "/v1/config"), ("POST", "/v1/retrieve")}
    assert all(m == "POST" and p.rsplit("/", 1)[-1] in ("_search", "_count") for m, p in cen.requests)
    tree = ast.parse(Path(ar.__file__).read_text(encoding="utf-8"))
    imported = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any("backfill" in m or "swap" in m or "load_embeddings" in m for m in imported)
    # in a fresh interpreter (order-independent): importing the runner, and the judge module its
    # stage-B gate imports, loads no write path
    import subprocess

    eval_dir = Path(ar.__file__).resolve().parent
    code = ("import sys; sys.path[:0] = [{e!r}, {g!r}, {p!r}]; import alias_study_run, alias_study_judge; "
            "print([m for m in sys.modules if 'backfill' in m or 'load_embeddings' in m or 'swap' in m])"
            ).format(e=str(eval_dir), g=str(eval_dir / "glossbench"), p=str(eval_dir.parents[1]))
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, timeout=120)
    assert out.stdout.strip() == "[]", out.stdout
    with pytest.raises(SystemExit):
        ar.main(["--config", str(tmp_path / "x.json"), "run", "--stage", "A", "--corpus-arm", "base",
                 "--glossary", "none", "--swap-record", "s.json"])


def test_gold_presence(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    hashes = json.loads(Path(cfg["gold_hashes"]).read_text())
    g = w.records[0]["gold_chunks"][0]
    with pytest.raises(f10.Refused, match="other content"):
        ar.check_gold_presence(w.records, [w.base_export], {**hashes, g: "0" * 64})
    with pytest.raises(f10.Refused, match="absent"):
        ar.check_gold_presence(w.records, [w.exports["neighbour"]], hashes)
    assert ar.check_gold_presence(w.records, [w.base_export], hashes)["present"] == len(hashes)


def test_config_shape(tmp_path: Path) -> None:
    _, cfg = setup(tmp_path)
    ar.validate_config(cfg)

    def bad(mut: Any, msg: str) -> None:
        c = json.loads(json.dumps(cfg))
        mut(c)
        with pytest.raises(f10.Refused, match=msg):
            ar.validate_config(c)

    bad(lambda c: c["corpora"].pop("random2"), "corpora")
    bad(lambda c: c["arms"].pop("merged"), "arms")
    bad(lambda c: c.update(conditions={"bm25": {"top_k": 10}}), "top_k")
    bad(lambda c: c.update(conditions={"base": {"rerank_candidates": 30}}), "rerank_candidates")
    bad(lambda c: c.update(server_assumed={"rrf_k": 61}), "rrf_k")
    bad(lambda c: c["collections"]["neighbour"].pop("merged"), "exactly the attachments")
    bad(lambda c: c["collections"]["neighbour"].update(placebo=c["collections"]["base"]["placebo"]),
        "exactly the attachments")
    bad(lambda c: c["collections"]["random1"]["none"].update(collection=collection("base", "none")), "shares")
    for cid in ("oa1000-live", "workbench-x", "benchmark-x", "x-bench-y"):
        bad(lambda c, cid=cid: c["collections"]["random1"]["none"].update(collection=cid), "bench collection")
    bad(lambda c: c["collections"]["random1"]["none"].pop("load_receipt"), "load_receipt")
    bad(lambda c: c["corpora"]["base"].update(collection="x"), "own collection")
    bad(lambda c: c["corpora"]["base"].update(restore_point="rp.json"), "own collection")
    bad(lambda c: c["arms"]["placebo"].update(stamps=["t-merged"]), "own version")
    bad(lambda c: c.update(dry_run=False, pilot=True), "census.es_url")


def test_real_config_checks_the_record_set(tmp_path: Path) -> None:
    _, cfg = setup(tmp_path, dry_run=False, pilot=True, census={"es_url": ES})
    with pytest.raises(SystemExit, match="not the frozen one"):
        ar.load_study_records(ar.validate_config(cfg))


def test_plan_lists_29_legs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w, cfg = setup(tmp_path)
    assert run(cfg, API(w)) == 0
    capsys.readouterr()
    st = ar.run_status(Path(cfg["out_dir"]))
    assert len(st) == 29 and sum(v == "complete" for v in st.values()) == 3
    assert st["base|none|bm25"] == "complete" and st["base|placebo|default"] == "missing"
    assert ar.main(["--config", str(write_cfg(tmp_path, cfg)), "plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["legs"] == 29 and plan["stage_a_committed"] is False
    assert sum(len(r["legs"]) for r in plan["runs"] if r["stage"] == "A") == 19
    assert sum(len(r["legs"]) for r in plan["runs"] if r["stage"] == "B") == 10


# ------------------------------------------------------------------ stages (§5.7)

def write_cfg(tmp: Path, cfg: dict[str, Any]) -> Path:
    p = tmp / "run-config.json"
    p.write_text(json.dumps(cfg))
    return p


def commit_stage_a(cfg: dict[str, Any], tmp: Path, files: list[tuple[str, Path, str]] = (),  # type: ignore[assignment]
                   mode: str = "dry_run") -> Path:
    """A stage-A commit as the judge's commit-stage-a writes it (only the files given), its
    public file and the runs dir's marker: enough for the runner's stage-B gate."""
    import alias_study_judge as aj

    bf = tmp / "batches-A.json"
    bf.write_text(json.dumps({"batches": []}))
    cp = tmp / "stage-a-commit.json"
    pub = tmp / "stage-a-commit.public.json"
    cp.write_text(json.dumps({"schema": aj.COMMIT_SCHEMA, "mode": mode, "batches_file": str(bf), "batches_A": [],
                              "batch_numbers_A": [], "public_file": str(pub),
                              "files": [aj.file_entry(n, p, m) for n, p, m in files]}))
    sha = asc.sha256_file(cp)
    pub.write_text(json.dumps({"commit_sha256": sha}))
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / ar.COMMIT_MARKER).write_text(json.dumps({"commit": str(cp), "commit_sha256": sha, "public": str(pub)}))
    return cp


def test_stage_legs_are_the_owner_s() -> None:
    a = {asc.leg_id(*lg) for lg in ar.stage_legs("A")}
    l1 = [("base", "none"), ("base", "merged"), ("neighbour", "none"), ("random1", "none"), ("random2", "none")]
    reps = {f"{c}|none|default#2" for c in asc.CORPUS_ARMS}
    assert a == {f"{c}|{g}|{r}" for c, g in l1 for r in ("default", "norerank", "bm25")} | reps
    assert {asc.leg_id(*lg) for lg in ar.judging_legs("A")} == \
        {f"{c}|{g}|{r}" for c, g in l1 for r in ("default", "norerank")} | reps
    b = ar.stage_legs("B")
    assert len(b) == 10 and ("base", "placebo", "default", 1) in b and len(ar.study_legs()) == 29
    assert len(ar.judging_legs("B")) == 15 and all(r == "bm25" for c, g, r, rep in ar.judging_legs("B")
                                                   if (c, g) in l1)


def test_stage_b_is_refused_before_stage_a_is_committed(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    with pytest.raises(f10.Refused, match="only after stage A is committed"):
        run(cfg, api, corpus="neighbour", glossary="merged", stage="B")
    with pytest.raises(f10.Refused, match="no stage-A leg"):
        run(cfg, api, corpus="neighbour", glossary="merged", stage="A")
    with pytest.raises(f10.Refused, match="no stage-B leg"):
        run(cfg, api, stage="B")
    with pytest.raises(f10.Refused, match="--stage"):
        run(cfg, api, stage=None)  # type: ignore[arg-type]
    assert api.bodies == []


def test_stage_a_is_closed_by_the_commit_and_the_commit_is_verified(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    api = API(w)
    assert run(cfg, api) == 0
    rows_a = Path(cfg["out_dir"]) / "base" / "none.jsonl"
    commit_stage_a(cfg, tmp_path, [("rows:base|none|default", rows_a, "exact")])
    with pytest.raises(f10.Refused, match="committed"):
        run(cfg, api, repeat=2)
    # a stage-A file rewritten after the commit: stage B refuses before measuring anything
    keep = rows_a.read_bytes()
    rows_a.write_bytes(keep.replace(b'"failed": false', b'"failed": true ', 1))
    n = len(api.bodies)
    with pytest.raises(f10.Refused, match="does not verify"):
        run(cfg, api, corpus="neighbour", glossary="merged", stage="B")
    assert len(api.bodies) == n
    rows_a.write_bytes(keep)
    assert run(cfg, api, corpus="neighbour", glossary="merged", stage="B") == 0
    s = summary(cfg, "neighbour", "merged")
    assert s["study"]["stage_a_commit"]["verified"] and len(s["study"]["legs"]) == 3
    assert all(v == {"retrieval": "B", "judging": "B"} for v in s["study"]["leg_stages"].values())


def test_the_runner_reads_the_marker_s_sha(tmp_path: Path) -> None:
    """The marker's commit_sha256 is always checked, and so is the public file's."""
    w, cfg = setup(tmp_path)
    cp = commit_stage_a(cfg, tmp_path)
    marker = Path(cfg["out_dir"]) / ar.COMMIT_MARKER
    m = json.loads(marker.read_text())
    marker.write_text(json.dumps({**m, "commit_sha256": "0" * 64}))
    with pytest.raises(f10.Refused, match="sha256 the marker"):
        run(cfg, API(w), corpus="neighbour", glossary="merged", stage="B")
    marker.write_text(json.dumps({k: v for k, v in m.items() if k != "commit_sha256"}))
    with pytest.raises(f10.Refused, match="no commit_sha256"):
        run(cfg, API(w), corpus="neighbour", glossary="merged", stage="B")
    marker.write_text(json.dumps(m))
    Path(m["public"]).write_text(json.dumps({"commit_sha256": "1" * 64}))
    with pytest.raises(f10.Refused, match="names another commit"):
        run(cfg, API(w), corpus="neighbour", glossary="merged", stage="B")
    assert cp.is_file()


def test_single_file_layout_is_read(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_runs()
    st = ar.run_status(w.runs)
    assert sum(v == "complete" for v in st.values()) == 29
    assert ar.leg_files(w.runs, "base", "none", "bm25")[0].name == "none.jsonl"


def test_a_stray_stage_b_file_shadows_nothing(tmp_path: Path) -> None:
    """Reviewer's probe: a ``<label>.stageB`` file left on disk is not a leg's file."""
    d = tmp_path / "runs" / "neighbour"
    d.mkdir(parents=True)
    (d / "merged.stageB.summary.json").write_text(json.dumps({"status": "complete", "conditions": {"bm25": {}}}))
    rows, sp = ar.leg_files(tmp_path / "runs", "neighbour", "merged", "bm25")
    assert sp.name == "merged.summary.json" and rows.name == "merged.jsonl"
    assert ar.run_status(tmp_path / "runs")["neighbour|merged|bm25"] == "missing"


# ------------------------------------------------------------------ the placebo leg (§5.3)

def test_placebo_leg_runs_base_default_only(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    commit_stage_a(cfg, tmp_path)
    api = API(w)
    assert run(cfg, api, glossary="placebo", stage="B") == 0
    s = summary(cfg, "base", "placebo")
    assert list(s["conditions"]) == ["base"] and s["study"]["placebo"] is True
    assert s["study"]["legs"] == ["base|placebo|default"]
    assert s["study"]["placebo_glossary"] is None                    # a dry run without a manifest
    assert {b["collection"] for b in api.bodies} == {collection("base", "placebo")}
    with pytest.raises(f10.Refused, match="base x default only"):
        run(cfg, api, corpus="neighbour", glossary="placebo", stage="B")
    with pytest.raises(f10.Refused, match="no stage-A leg"):
        run(cfg, api, glossary="placebo", stage="A")


def test_placebo_leg_summary_carries_the_drop_counts(tmp_path: Path) -> None:
    w, cfg = setup(tmp_path)
    pf = tmp_path / "out" / "placebo.jsonl"
    assert ar.main(["placebo-glossary", "--merged", str(w.glossary), "--base-export", str(w.base_export),
                    "--out", str(pf)]) == 0
    mf = pf.with_name(pf.name + ".manifest.json")
    commit_stage_a(cfg, tmp_path)
    cfg["arms"]["placebo"]["manifest"] = str(mf)
    with pytest.raises(f10.Refused, match="manifest"):            # not the arm's declared file
        run(cfg, API(w), glossary="placebo", stage="B")
    # the writer's manifest of the glossary_file/v1 the placebo collection was loaded from
    v1 = arm_files(tmp_path)["placebo"]["sha256"]
    man = json.loads(mf.read_text())
    mf.write_text(json.dumps({**man, "glossary_file_sha256": v1}))
    assert run(cfg, API(w), glossary="placebo", stage="B") == 0
    pg = summary(cfg, "base", "placebo")["study"]["placebo_glossary"]
    assert pg["glossary_file_sha256"] == v1
    assert set(pg["counts"]["placebo_dropped_own_entries"]) == {"entries", "papers", "papers_emptied",
                                                                "per_paper_by_rank"}


def rule_mapping(papers: list[str]) -> dict[str, str]:
    """The prereg's words, computed independently: sha256 of '261101|alias|placebo|<paper>'
    ascending, each paper receiving the next one's list, the last the first's."""
    import hashlib

    order = sorted(papers, key=lambda p: hashlib.sha256(f"261101|alias|placebo|{p}".encode()).hexdigest())
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


def test_placebo_mapping_is_the_prereg_derangement() -> None:
    for n in (2, 3, 7, 40, 300):
        papers = [f"W{i:04d}" for i in range(n)]
        m = ar.placebo_mapping(papers)
        assert m == rule_mapping(papers)
        assert sorted(m) == sorted(m.values()) == sorted(papers)       # a permutation
        assert all(r != s for r, s in m.items())                       # no fixed point
    with pytest.raises(SystemExit, match="at least 2"):
        ar.placebo_mapping(["only"])
    assert ar.SEED_PLACEBO == "261101|alias|placebo"


def test_build_placebo_rows() -> None:
    entries = [{"paper": "A", "alias": "PCR", "primary": "polymerase chain reaction", "kind": "abbreviation",
                "doc_id": "dA", "def_chunk_id": "A-c1", "def_span": [1, 5]},
               {"paper": "B", "alias": "x", "primary": "the mass", "kind": "symbol", "doc_id": "dB"},
               {"paper": "C", "alias": "QQ", "primary": "quality quotient", "kind": "abbreviation"}]
    rows, counts = ar.build_placebo(entries, {"A", "B", "C", "D"})
    m = ar.placebo_mapping(["A", "B", "C"])
    assert counts["papers"] == 3 and counts["entries"] == 3 and counts["fixed_points"] == 0
    for recv, src in m.items():
        got = [r for r in rows if r["paper"] == recv]
        assert [r["alias"] for r in got] == [e["alias"] for e in entries if e["paper"] == src]
        assert all(r["placebo"] is True for r in got)
    assert not any(k in r for r in rows for k in ("doc_id", "def_chunk_id", "def_span"))
    assert counts["placebo_dropped_own_entries"] == {"entries": 0, "papers": 0, "papers_emptied": 0,
                                                     "per_paper_by_rank": {}}
    with pytest.raises(SystemExit, match="not in the base export"):
        ar.build_placebo(entries, {"A", "B"})
    with pytest.raises(SystemExit, match="already a placebo"):
        ar.build_placebo(rows)


def test_placebo_drops_entries_equal_to_the_receiving_paper_s_own() -> None:
    """Owner, 2026-11-03. Two papers swap lists (a 2-cycle): A and B both define PCR as the
    polymerase chain reaction (B in the plural and capitalised: the same merged-glossary key,
    alias + ``alias_abbrev.words`` of the primary). Each drops the other's PCR; B is left with
    nothing and gets no placebo field."""
    entries = [{"paper": "A", "alias": "PCR", "primary": "polymerase chain reaction", "kind": "abbreviation"},
               {"paper": "B", "alias": "x", "primary": "the mass", "kind": "symbol"},
               {"paper": "B", "alias": "PCR", "primary": "Polymerase Chain Reactions", "kind": "abbreviation"},
               {"paper": "B", "alias": "pcr", "primary": "polymerase chain reaction", "kind": "abbreviation"}]
    rows, counts = ar.build_placebo(entries)
    assert ar.placebo_mapping(["A", "B"]) == {"A": "B", "B": "A"}
    a = [(r["alias"], r["primary"]) for r in rows if r["paper"] == "A"]
    assert a == [("x", "the mass"), ("pcr", "polymerase chain reaction")]   # another alias spelling is kept
    assert not [r for r in rows if r["paper"] == "B"]
    d = counts["placebo_dropped_own_entries"]
    rank = {p: str(i) for i, p in enumerate(ar.placebo_order(["A", "B"]), 1)}
    assert d["entries"] == 2 and d["papers"] == 2 and d["papers_emptied"] == 1
    assert d["per_paper_by_rank"] == {rank["A"]: 1, rank["B"]: 1}
    assert counts["entries_received"] == 4 and counts["entries"] == 2 and counts["papers_with_placebo_entries"] == 1


def test_placebo_command_writes_file_and_manifest(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    out = tmp_path / "out" / "placebo.jsonl"
    assert ar.main(["placebo-glossary", "--merged", str(w.glossary), "--base-export", str(w.base_export),
                    "--out", str(out)]) == 0
    man = json.loads((out.parent / "placebo.jsonl.manifest.json").read_text())
    assert man["placebo_sha256"] == asc.sha256_file(out) and man["seed"] == "261101|alias|placebo"
    assert man["counts"]["fixed_points"] == 0 and man["counts"]["papers"] == len(w.papers)
    with pytest.raises(SystemExit, match="inside the repository"):
        ar.main(["placebo-glossary", "--merged", str(w.glossary), "--base-export", str(w.base_export),
                 "--out", str(Path(__file__).resolve().parent / "placebo.jsonl")])


# ------------------------------------------------------------------ modes

def test_pilot_runs_the_191_only_and_keeps_its_own_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                                                            ) -> None:
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"accepted_sha256": "x", "records": len(recs)})
    w, cfg = setup(tmp_path, dry_run=False, pilot=True, census={"es_url": ES})
    api = API(w)
    assert run(cfg, api, census=Census()) == 0
    desc = {r["record_id"] for r in asc.split_records(w.records)[1]}
    assert {api.q2rec[b["query"]]["record_id"] for b in api.bodies} == desc and len(api.bodies) == 3 * len(desc)
    s = summary(cfg, "base", "none")
    assert s["study"]["pilot"] is True and s["study"]["mode"] == "pilot"
    assert s["study"]["record_set"]["pilot_records"] == len(desc)
    pr = tmp_path / "prereg.md"
    pr.write_text("frozen\n")
    main_cfg = {**cfg, "pilot": False, "prereg": {"file": str(pr), "sha256": asc.sha256_file(pr)}}
    with pytest.raises(f10.Refused, match="STUDY-MODE"):
        run(main_cfg, api, repeat=2, census=Census())


def test_pilot_leg_summary_carries_no_found10_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """R28 (owner, 2026-11-03): a pilot leg summary carries no per-condition found@10 level, and
    nothing the run prints does; the rows keep every rank. A dry-run summary keeps found10."""
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"accepted_sha256": "x", "records": len(recs)})
    (tmp_path / "p").mkdir()
    (tmp_path / "d").mkdir()
    w, cfg = setup(tmp_path / "p", dry_run=False, pilot=True, census={"es_url": ES})
    capsys.readouterr()
    assert run(cfg, API(w), census=Census()) == 0
    printed = capsys.readouterr().out
    s = summary(cfg, "base", "none")
    assert set(s["conditions"]) == {"base", "bm25", "norerank"}
    for c in s["conditions"].values():
        assert not set(ar.PILOT_WITHHELD_CONDITION_KEYS) & set(c) and c["outcomes"].startswith("withheld")
        assert "gate" in c and "rows" in c
    assert "found10" not in json.dumps(s["conditions"]) and "found10" not in printed
    rows = [json.loads(x) for x in (Path(cfg["out_dir"]) / "base" / "none.jsonl").read_text().splitlines() if x]
    assert rows and all("ranked_chunk_ids" in r for r in rows)
    w2, cfg2 = setup(tmp_path / "d")
    assert run(cfg2, API(w2)) == 0
    s2 = summary(cfg2, "base", "none")
    assert all("found10" in c and "outcomes" not in c for c in s2["conditions"].values())


def test_main_run_pins_the_frozen_prereg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"accepted_sha256": "x", "records": len(recs)})
    w, cfg = setup(tmp_path, dry_run=False, census={"es_url": ES})
    with pytest.raises(f10.Refused, match="prereg"):
        ar.validate_config(cfg)
    pr = tmp_path / "prereg.md"
    pr.write_text("frozen\n")
    good = {**cfg, "prereg": {"file": str(pr), "sha256": asc.sha256_file(pr)}}
    api = API(w)
    pr.write_text("edited after the freeze\n")
    with pytest.raises(f10.Refused, match="HARD STOP"):
        run(good, api, census=Census())
    assert api.bodies == []
    pr.write_text("frozen\n")
    assert run(good, api, census=Census()) == 0
    assert summary(good, "base", "none")["study"]["prereg"]["verified"] is True


def test_field_coverage(tmp_path: Path) -> None:
    rec = dry.load_summary(4, arm_files(tmp_path)["merged"])
    fc = ar.field_coverage([("a", "t", "AB: alpha beta"), ("b", "t", ""), ("c", "t", "CD: c"), ("d", "t", "")],
                           None, "chunk export + glossary file", rec)
    assert fc["available"] and fc["chunks"] == 4 and fc["chunks_with_field"] == 2 and fc["share_with_field"] == 0.5
    assert fc["mean_chars"] == (14 + 5) / 4 and fc["mean_chars_with_field"] == 9.5
    assert fc["glossary_file_counts"] == arm_files(tmp_path)["merged"]["counts"]
    w, cfg = setup(tmp_path)
    assert run(cfg, API(w)) == 0
    s = summary(cfg, "base", "none")["study"]["field_coverage"]
    assert s["available"] is False and s["glossary_file_counts"]["merged_entries"] == 0


def test_the_example_config_validates() -> None:
    """The committed template is the current shape: a main-run config (collections, no swap or
    restore point), the none arm declaring attach: mention."""
    ex = json.loads((Path(ar.__file__).resolve().parent / "examples" / "alias-study-run.example.json").read_text())
    cfg = ar.validate_config(ex)
    assert ar.config_mode(cfg) == "main" and cfg["arms"]["none"]["attach"] == "mention"
    assert {(c, g) for c, gs in cfg["collections"].items() for g in gs} == set(ar.required_attachments(cfg))


def test_a_kill_between_found10s_summary_and_the_study_block_leaves_the_leg_incomplete(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """found10's _finish writes ``status: complete`` first; AliasRunner._finish then adds the
    ``study`` block. A kill between the two must not read as complete (the judge's commit would
    pin the file): run_status says incomplete, the judge refuses the leg's rows, and a resume
    re-runs the leg from its rows (no new retrieval call) and completes it."""
    import alias_study_judge as aj

    w, cfg = setup(tmp_path)
    api = API(w)

    def killed(self: Any, *a: Any, **k: Any) -> int:
        f10.Runner._finish(self, *a, **k)
        raise KeyboardInterrupt("killed after found10 wrote its summary")
    monkeypatch.setattr(ar.AliasRunner, "_finish", killed)
    with pytest.raises(KeyboardInterrupt):
        run(cfg, api)
    s = summary(cfg, "base", "none")
    assert s["status"] == "complete" and "study" not in s       # the kill window
    legs = [lg for lg in ar.study_legs() if lg[:2] == ("base", "none") and lg[3] == 1]
    st = ar.run_status(Path(cfg["out_dir"]))
    assert {st[asc.leg_id(*lg)] for lg in legs} == {"incomplete"}
    with pytest.raises(SystemExit, match="not complete"):
        aj.leg_rows(Path(cfg["out_dir"]), legs)
    monkeypatch.undo()
    calls = len(api.bodies)
    assert run(cfg, api) == 0
    assert len(api.bodies) == calls                               # resumed from the rows
    s = summary(cfg, "base", "none")
    assert s["status"] == "complete" and s["study"]["mode"] == "dry_run" and s["study"]["stage"] == "A"
    st = ar.run_status(Path(cfg["out_dir"]))
    assert {st[asc.leg_id(*lg)] for lg in legs} == {"complete"}


@pytest.mark.parametrize("drop", ["study", "mode", "stage"])
def test_run_status_needs_the_study_block_with_mode_and_stage(tmp_path: Path, drop: str) -> None:
    w = fx.World(tmp_path)
    w.write_runs()
    sp = tmp_path / "runs" / "neighbour" / "none.summary.json"
    s = json.loads(sp.read_text())
    if drop == "study":
        del s["study"]
    else:
        s["study"].pop(drop)
    sp.write_text(json.dumps(s))
    st = ar.run_status(w.runs)
    assert {st[f"neighbour|none|{r}"] for r in asc.RETRIEVERS} == {"incomplete"}
    assert sum(v == "complete" for v in st.values()) == 29 - 3


# ------------------------------------------------------------------ the rechunk mode (exploratory)

def rechunk_exports(tmp: Path, w: fx.World, n: int = 100, desc: str | None = None) -> dict[str, list[str]]:
    """One rechunked export per corpus arm, as ``rechunk_prep.py export`` writes it: the arm's
    chunks (base plus its added papers), padded to ``n`` (the arm's expected_chunks), every row
    stamped with the confirmatory chunk descriptor (``desc`` overrides it)."""
    import rechunk_prep as rp

    out = {}
    for a in asc.CORPUS_ARMS:
        rows = [*asc.read_jsonl(w.base_export), *([] if a == "base" else asc.read_jsonl(w.exports[a]))]
        rows += [{**rows[0], "chunk_id": f"pad-{a}-{i}"} for i in range(n - len(rows))]
        for r in rows:
            r["metadata"] = {**r["metadata"], "chunk_descriptor": desc or rp.DESCRIPTOR}
        out[a] = [str(fx.write_jsonl(tmp / f"rechunked-{a}.jsonl", rows))]
    return out


def rechunk_setup(tmp: Path, monkeypatch: pytest.MonkeyPatch, missing: str | None = None
                  ) -> tuple[fx.World, dict[str, Any]]:
    """A rechunk config: the four none collections only, no gold hashes, each arm's rechunked
    export (100 chunks, the load receipt's and expected_chunks' count), and a label table whose
    pairs sit in base (two records), neighbour (one, also in base) and random1; none in random2.
    The random1 pair's max-overlap chunk is its record's gold, which the fake API serves at rank
    1 or 2: its M is True under every retriever."""
    import rechunk_fixture as rfx

    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"accepted_sha256": "x", "records": len(recs)})
    w, cfg = setup(tmp, dry_run=False, census={"es_url": ES})
    cfg.pop("gold_hashes")
    cfg["collections"] = {c: {"none": cfg["collections"][c]["none"]} for c in asc.CORPUS_ARMS}
    cfg["arms"] = {"none": cfg["arms"]["none"]}
    for a, paths in rechunk_exports(tmp, w).items():
        cfg["corpora"][a]["chunk_export"] = paths
    r = [x["record_id"] for x in w.records]
    gold4 = w.records[3]["gold_chunks"][0]
    pairs = [rfx.pair_row(r[0], "base", fx.chunk_id("P00", 1), tag="1", covers=[fx.chunk_id("P00", 1),
                                                                              fx.chunk_id("P00", 3)]),
             rfx.pair_row(r[1], "base", fx.chunk_id("P00", 2), tag="2"),
             rfx.pair_row(r[0], "neighbour", missing or fx.chunk_id("N00", 1), tag="3"),
             rfx.pair_row(r[3], "random1", gold4, tag="4")]
    labels = [rfx.label_row(r[0], fx.chunk_id("P00", 1), "answers"), rfx.label_row(r[3], gold4, "answers")]
    cfg["rechunk"] = {"label_table": str(rfx.write_label_table(tmp / "labels.jsonl", pairs, labels))}
    return w, cfg


def test_rechunk_runs_the_arm_s_pair_records_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, cfg = rechunk_setup(tmp_path, monkeypatch)
    api = API(w)
    assert run(cfg, api, census=Census()) == 0
    asked = {api.q2rec[b["query"]]["record_id"] for b in api.bodies}
    assert asked == {w.records[0]["record_id"], w.records[1]["record_id"]} and len(api.bodies) == 3 * 2
    assert {b["top_k"] for b in api.bodies} == {50} and {b["collection"] for b in api.bodies} == {
        collection("base", "none")}
    s = summary(cfg, "base", "none")
    assert s["status"] == "complete" and s["exploratory"] is True and s["mode"] == "rechunk"
    st = s["study"]
    assert st["mode"] == "rechunk" and st["exploratory"] is True and st["stage"] == "A"
    assert "gold_presence" not in st and st["record_set"]["rechunk_records"] == 3
    rp_ = st["rechunk_presence"]
    assert rp_["pairs"] == 2 and rp_["mapped_chunks"] == 3 and rp_["present"] == 3
    assert rp_["label_table_sha256"] == asc.sha256_file(cfg["rechunk"]["label_table"])
    assert set(s["conditions"]) == {"base", "bm25", "norerank"}
    for c in s["conditions"].values():
        assert not set(ar.PILOT_WITHHELD_CONDITION_KEYS) & set(c) and c["outcomes"] == ar.RECHUNK_OUTCOMES_NOTE
        assert "gate" in c
    assert (Path(cfg["out_dir"]) / ar.MODE_MARKER).read_text().strip() == "rechunk"
    rows = asc.read_jsonl(Path(cfg["out_dir"]) / "base" / "none.jsonl")
    assert len(rows) == 6 and all(len(x["ranked_chunk_ids"]) == 50 for x in rows)
    # neighbour: the record of the neighbour pair only; random2 has no pair
    api2 = API(w)
    assert run(cfg, api2, corpus="neighbour", census=Census()) == 0
    assert {api2.q2rec[b["query"]]["record_id"] for b in api2.bodies} == {w.records[0]["record_id"]}
    with pytest.raises(f10.Refused, match="no label-table pair"):
        run(cfg, API(w), corpus="random2", census=Census())


def test_rechunk_refuses_an_export_of_another_size_or_chunking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                                                               ) -> None:
    """The arm's export must hold corpora.<arm>.expected_chunks chunks and carry the confirmatory
    descriptor; both are refused before any API call."""
    import alias_control_chunks as acc

    w, cfg = rechunk_setup(tmp_path, monkeypatch)
    (tmp_path / "small").mkdir()
    cfg["corpora"]["base"]["chunk_export"] = rechunk_exports(tmp_path / "small", w, n=90)["base"]
    api = API(w)
    with pytest.raises(f10.Refused, match="holds 90 chunks, corpora.base.expected_chunks is 100"):
        run(cfg, api, census=Census())
    (tmp_path / "fixed").mkdir()
    cfg["corpora"]["base"]["chunk_export"] = rechunk_exports(tmp_path / "fixed", w,
                                                             desc=acc.STAGE_A_DESCRIPTOR)["base"]
    with pytest.raises(f10.Refused, match="another chunk_descriptor"):
        run(cfg, api, census=Census())
    assert api.bodies == [] and api.config_reads == 0


def test_rechunk_refuses_a_mapped_chunk_absent_from_the_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                                                               ) -> None:
    w, cfg = rechunk_setup(tmp_path, monkeypatch, missing="not-in-any-export-c9")
    api = API(w)
    with pytest.raises(f10.Refused, match="rechunk presence: 1 of the 1"):
        run(cfg, api, corpus="neighbour", census=Census())
    assert api.bodies == [] and api.config_reads == 0


def test_rechunk_config_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, cfg = rechunk_setup(tmp_path, monkeypatch)
    ar.validate_config(cfg)

    def bad(mut: Any, msg: str) -> None:
        c = json.loads(json.dumps(cfg))
        mut(c)
        with pytest.raises(f10.Refused, match=msg):
            ar.validate_config(c)

    bad(lambda c: c.update(pilot=True), "excludes dry_run and pilot")
    bad(lambda c: c.update(dry_run=True), "excludes dry_run and pilot")
    bad(lambda c: c.update(rechunk={"label_table": ""}), "label_table")
    bad(lambda c: c.update(rechunk="x.jsonl"), "label_table")
    bad(lambda c: c["arms"].pop("none"), "rechunk: arms")
    bad(lambda c: c["collections"]["base"].update(merged={"collection": "bench-t-base-merged", "index": "ix_m",
                                                          "load_receipt": "r.json"}), "exactly the attachments")
    bad(lambda c: c["collections"]["random2"].pop("none"), "exactly the attachments")
    # a merged arm configured for the collections it does not run is not a reason to run it
    c = json.loads(json.dumps(cfg))
    c["arms"]["merged"] = {"stamps": ["t-merged"]}
    cv = ar.validate_config(c)
    recs, ident = ar.load_study_records(cv)
    with pytest.raises(f10.Refused, match="none collections only"):
        ar.build_runner(cv, recs, ident, "base", "merged", stage="A")
    # a label-table record outside the accepted file
    t = Path(cfg["rechunk"]["label_table"])
    t.write_text(t.read_text().replace(w.records[3]["record_id"], "alias-O-ZZ99-g0"))
    with pytest.raises(f10.Refused, match="not in the accepted file"):
        ar.load_study_records(ar.validate_config(cfg))
    t.write_text("{}\n")
    with pytest.raises(f10.Refused, match="rechunk.label_table"):
        ar.load_study_records(ar.validate_config(cfg))


def test_rechunk_and_main_dirs_never_mix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, cfg = rechunk_setup(tmp_path, monkeypatch)
    assert ar.config_mode(cfg) == "rechunk"
    (Path(cfg["out_dir"])).mkdir(parents=True)
    (Path(cfg["out_dir"]) / ar.MODE_MARKER).write_text("main\n")
    with pytest.raises(f10.Refused, match="STUDY-MODE"):
        run(cfg, API(w), census=Census())


def test_rechunk_runs_feed_the_stage1_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The report reads what the runner writes in the rechunk mode (summaries and rows)."""
    import rechunk_stage1 as rs

    w, cfg = rechunk_setup(tmp_path, monkeypatch)
    for c in ("base", "neighbour", "random1"):
        assert run(cfg, API(w), corpus=c, census=Census()) == 0
    out = tmp_path / "stage1.json"
    rec = rs.run(Path(cfg["out_dir"]), Path(cfg["rechunk"]["label_table"]), out)
    assert rec["pairs"] == 4 and set(rec["runs"]) == {"base", "neighbour", "random1"}
    d = rec["q2_all_pairs"]["all_pairs"]["all"]["by_retriever"]["default"]
    assert d["n"] == 4 and d["failed"] == 0
    # M: only the random1 pair's max-overlap chunk is its record's gold, which the fake API serves
    # (rank 1, or 2 under the reranker); the other three max-overlap chunks are never served
    by = rec["q2_all_pairs"]["all_pairs"]["by_corpus_arm"]
    for r in ("default", "bm25", "norerank"):
        assert rec["q2_all_pairs"]["headline"]["M_all_pairs"][r] == {
            **rec["q2_all_pairs"]["headline"]["M_all_pairs"][r], "k": 1, "n": 4}
        assert by["random1"]["by_retriever"][r]["M"]["k"] == 1 and by["base"]["by_retriever"][r]["M"]["k"] == 0
    assert by["random1"]["by_retriever"]["default"]["M_rank"] == {"1": 0, "2-3": 1, "4-10": 0}
    assert by["random1"]["by_retriever"]["bm25"]["M_rank"] == {"1": 1, "2-3": 0, "4-10": 0}
    # A / A_own: the random1 pair's own chunk is labelled answers; nothing else answers in a top 10
    assert d["A"]["k"] == 1 and d["A_own"]["k"] == 1 and d["A_from_other_pairs_only"]["k"] == 0
    assert rec["runs"]["base"]["collection_build"]["n_chunks"] == 100
    assert rec["runs"]["base"]["gates"] == {"base": None, "bm25": None, "norerank": None}
