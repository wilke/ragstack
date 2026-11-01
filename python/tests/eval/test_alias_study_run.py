"""Alias study runner: the study's additions on top of round 3's runner (the admin config hash,
the per-leg failure stop, the noise repeat's seeded order, gold presence, restore points, the
config shape) and that round 3's gates still fire through it. A fake API, no store."""
from __future__ import annotations

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
import alias_study_fixture as fx  # noqa: E402
import alias_study_run as ar  # noqa: E402
import found10 as f10  # noqa: E402


def stamp(g: str) -> str:
    return f"t-{g}@file/1"


class API:
    """A fake RAGStack API: ``/v1/retrieve`` returns the record's gold plus filler."""

    def __init__(self, world: fx.World) -> None:
        self.w = world
        self.config: Any = {"a": 1}
        self.config_status = 200
        self.config_reads = 0
        self.change_config_after: int | None = None
        self.glossary = "none"
        self.bodies: list[dict[str, Any]] = []
        self.fail: Any = None           # (body) -> bool
        self.same_rerank = False
        self.q2rec = {r["question"]: r for r in world.records}

    def handler(self, req: httpx.Request) -> httpx.Response:
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
        ids = [rec["gold_chunks"][0]] + [f"F{i}" for i in range(80)]
        if body["retrieval_mode"] == "hybrid" and body.get("rerank") and not self.same_rerank:
            ids = [ids[1], ids[0]] + ids[2:]
        return httpx.Response(200, json={"sources": [{"chunk_id": c, "metadata": {
            "symbol_glossary_version": stamp(self.glossary)}} for c in ids[: body["top_k"]]]})


def setup(tmp: Path, **over: Any) -> tuple[fx.World, dict[str, Any], dict[str, Path]]:
    w = fx.World(tmp)
    gold = {r["gold_chunks"][0]: asc.sha_text(c["content"]) for r in w.records
            for cid, c in asc.read_chunk_exports([w.base_export]).items() if cid == r["gold_chunks"][0]}
    (tmp / "gold.json").write_text(json.dumps(gold))
    corpora = {}
    swaps = {}
    for a in asc.CORPUS_ARMS:
        rp = tmp / f"rp-{a}.json"
        rp.write_text(json.dumps({"schema": ar.RESTORE_SCHEMA, "index": f"ix_{a}", "snapshot_id": "s1", "total": 100}))
        exp = [str(w.base_export)] + ([str(w.exports[a])] if a != "base" else [])
        corpora[a] = {"collection": f"col_{a}", "index": f"ix_{a}", "expected_chunks": 100, "chunk_export": exp,
                      "restore_point": str(rp)}
        for g in asc.GLOSSARY_ARMS:
            sp = tmp / f"swap-{a}-{g}.json"
            sp.write_text(json.dumps({"schema": "glossbench-swap/1", "arm": g, "index": f"ix_{a}",
                                      "glossary_file": {"sha256": f"sha-{g}"}, "shuffle_seed": None,
                                      "apply": {"dry_run": False, "chunks_to_update": 5, "chunks_written": 5},
                                      "census_after": {"total": 100, "stamps": {stamp(g): 100}}}))
            swaps[f"{a}-{g}"] = sp
    cfg = {"api": {"base_url": "http://api.invalid", "retry_backoff_s": 0.0, "retries": 1}, "corpora": corpora,
           "arms": {g: {"stamps": [f"t-{g}"], "glossary_file_sha256": dict.fromkeys(asc.CORPUS_ARMS, f"sha-{g}")}
                    for g in asc.GLOSSARY_ARMS},
           "records": str(w.records_path), "out_dir": str(tmp / "runs"), "gold_hashes": str(tmp / "gold.json"),
           "dry_run": True, **over}
    return w, cfg, swaps


def run(cfg: dict[str, Any], swaps: dict[str, Path], api: API, corpus: str = "base", glossary: str = "none",
        repeat: int | None = None) -> int:
    c = ar.validate_config(cfg)
    recs, ident = ar.load_study_records(c)
    api.glossary = glossary
    r = ar.build_runner(c, recs, ident, corpus, glossary, swaps[f"{corpus}-{glossary}"], repeat=repeat,
                        boost_declared=1.0,
                        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(api.handler)))
    import asyncio
    return asyncio.run(r.run())


def summary(cfg: dict[str, Any], corpus: str, label: str) -> dict[str, Any]:
    return json.loads((Path(cfg["out_dir"]) / corpus / f"{label}.summary.json").read_text())


def test_complete_leg_with_config_hash(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    assert run(cfg, swaps, api) == 0
    s = summary(cfg, "base", "none")
    assert s["status"] == "complete" and s["study"]["config_hash_identical"] is True
    assert s["study"]["retrievers"] == ["default", "bm25", "norerank"]
    assert s["study"]["gold_presence"]["present"] == len({r["gold_chunks"][0] for r in w.records})
    assert api.config_reads == 2
    assert all(b["use_graph"] is False and b["rewrite_strategies"] == ["passthrough"] for b in api.bodies)
    assert {b["top_k"] for b in api.bodies} == {50}


def test_config_change_during_leg_is_a_hard_stop(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    api.change_config_after = 1
    assert run(cfg, swaps, api) == f10.EXIT_REFUSED
    s = summary(cfg, "base", "none")
    assert s["status"] == "hard_stop" and "differs" in s["reason"]


def test_unreadable_config_refuses_before_measuring(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    api.config_status = 403
    with pytest.raises(f10.Refused, match="admin"):
        run(cfg, swaps, api)
    assert api.bodies == []


def test_failed_calls_over_2pc_in_one_leg_stop(tmp_path: Path) -> None:
    """1 of 38 bm25 calls fails: 2.6 % of that leg, 0.9 % of the run (round 3 would go on)."""
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    victim = w.records[0]["question"]
    api.fail = lambda b: b["retrieval_mode"] == "bm25" and b["query"] == victim
    assert run(cfg, swaps, api) == f10.EXIT_REFUSED
    s = summary(cfg, "base", "none")
    assert s["status"] == "hard_stop" and "bm25 leg" in s["reason"] and s["failed_share"] < 0.02


def test_noise_repeat_is_default_only_in_seeded_order(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    assert run(cfg, swaps, api, repeat=2) == 0
    s = summary(cfg, "base", "none#2")
    assert list(s["conditions"]) == ["base"] and s["study"]["seeds"] == {"noise": asc.SEEDS["noise"]}
    sent = [b["query"] for b in api.bodies]
    q2id = {r["question"]: r["record_id"] for r in w.records}
    want = sorted(q2id.values(), key=lambda rid: asc.seeded_key(asc.SEEDS["noise"], "base", rid, "base"))
    assert [q2id[q] for q in sent] == want and want != [r["record_id"] for r in w.records]
    with pytest.raises(f10.Refused, match="default x none"):
        run(cfg, swaps, api, glossary="merged", repeat=2)


def test_inactive_reranker_gate_still_fires(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    api.same_rerank = True
    assert run(cfg, swaps, api) == 0
    assert "reranker was not active" in summary(cfg, "base", "none")["conditions"]["base"]["gate"]


def test_census_stamp_mismatch_refused(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    with pytest.raises(f10.Refused, match="census"):
        c = ar.validate_config(cfg)
        recs, ident = ar.load_study_records(c)
        r = ar.build_runner(c, recs, ident, "base", "merged", swaps["base-none"], boost_declared=1.0,
                            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(API(w).handler)))
        import asyncio
        asyncio.run(r.run())


def test_gold_presence(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    hashes = json.loads(Path(cfg["gold_hashes"]).read_text())
    g = w.records[0]["gold_chunks"][0]
    with pytest.raises(f10.Refused, match="other content"):
        ar.check_gold_presence(w.records, [w.base_export], {**hashes, g: "0" * 64})
    with pytest.raises(f10.Refused, match="absent"):
        ar.check_gold_presence(w.records, [w.exports["neighbour"]], hashes)
    assert ar.check_gold_presence(w.records, [w.base_export], hashes)["present"] == len(hashes)


def test_restore_point(tmp_path: Path) -> None:
    w, cfg, _ = setup(tmp_path)
    ccfg = cfg["corpora"]["base"]
    p = Path(ccfg["restore_point"])
    assert ar.check_restore_point(p, ccfg)["sha256"]
    for bad in ({"snapshot_id": ""}, {"index": "other"}, {"total": 99}, {"schema": "x"}):
        q = tmp_path / "rp-bad.json"
        q.write_text(json.dumps({**json.loads(p.read_text()), **bad}))
        with pytest.raises(f10.Refused, match="restore point"):
            ar.check_restore_point(q, ccfg)


def test_config_shape(tmp_path: Path) -> None:
    _, cfg, _ = setup(tmp_path)
    ar.validate_config(cfg)
    for mut in ({"corpora": {k: v for k, v in cfg["corpora"].items() if k != "random2"}},
                {"arms": {"none": cfg["arms"]["none"]}},
                {"conditions": {"bm25": {"top_k": 10}}},
                {"conditions": {"base": {"rerank_candidates": 30}}},
                {"server_assumed": {"rrf_k": 61}}):
        with pytest.raises(f10.Refused):
            ar.validate_config({**cfg, **mut})
    real = {**cfg, "dry_run": False}
    real["corpora"] = {k: {kk: vv for kk, vv in v.items() if kk != "restore_point"} for k, v in cfg["corpora"].items()}
    with pytest.raises(f10.Refused, match="restore_point"):
        ar.validate_config(real)


def test_real_config_checks_the_record_set(tmp_path: Path) -> None:
    _, cfg, _ = setup(tmp_path, dry_run=False)
    with pytest.raises(SystemExit, match="not the frozen one"):
        ar.load_study_records(ar.validate_config(cfg))


def test_plan_lists_28_legs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w, cfg, swaps = setup(tmp_path)
    assert run(cfg, swaps, API(w)) == 0
    st = ar.run_status(Path(cfg["out_dir"]))
    assert len(st) == 28 and sum(v == "complete" for v in st.values()) == 3
