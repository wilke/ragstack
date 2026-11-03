"""Alias study runner: the study's additions on top of round 3's runner (the admin config hash,
the per-leg failure stop, the noise repeat's seeded order, gold presence, restore points, the
config shape), the two stages (§5.7), the placebo leg and its derangement (§5.3), the modes
(pilot, main, the frozen prereg) and that round 3's gates still fire through it. A fake API,
no store."""
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
        for g in ar.STUDY_GLOSSARY_ARMS:
            sp = tmp / f"swap-{a}-{g}.json"
            sp.write_text(json.dumps({"schema": "glossbench-swap/1", "arm": g, "index": f"ix_{a}",
                                      "glossary_file": {"sha256": f"sha-{g}"}, "shuffle_seed": None,
                                      "apply": {"dry_run": False, "chunks_to_update": 5, "chunks_written": 5},
                                      "census_after": {"total": 100, "stamps": {stamp(g): 100}}}))
            swaps[f"{a}-{g}"] = sp
    cfg = {"api": {"base_url": "http://api.invalid", "retry_backoff_s": 0.0, "retries": 1}, "corpora": corpora,
           "arms": {g: {"stamps": [f"t-{g}"], "glossary_file_sha256": dict.fromkeys(asc.CORPUS_ARMS, f"sha-{g}")}
                    for g in ar.STUDY_GLOSSARY_ARMS},
           "records": str(w.records_path), "out_dir": str(tmp / "runs"), "gold_hashes": str(tmp / "gold.json"),
           "dry_run": True, **over}
    return w, cfg, swaps


def run(cfg: dict[str, Any], swaps: dict[str, Path], api: API, corpus: str = "base", glossary: str = "none",
        repeat: int | None = None, stage: str = "A") -> int:
    c = ar.validate_config(cfg)
    recs, ident = ar.load_study_records(c)
    api.glossary = glossary
    r = ar.build_runner(c, recs, ident, corpus, glossary, swaps[f"{corpus}-{glossary}"], repeat=repeat,
                        boost_declared=1.0, stage=stage,
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
    # stage A measures the attachment's stage-A leg only (§5.7)
    assert s["study"]["retrievers"] == ["default"] and s["study"]["legs"] == ["base|none|default"]
    assert s["study"]["stage"] == "A" and s["study"]["mode"] == "dry_run" and s["study"]["placebo"] is False
    assert s["study"]["gold_presence"]["present"] == len({r["gold_chunks"][0] for r in w.records})
    assert api.config_reads == 2
    assert all(b["use_graph"] is False and b["rewrite_strategies"] == ["passthrough"] for b in api.bodies)
    assert {b["top_k"] for b in api.bodies} == {50}
    assert len(api.bodies) == len(w.records)


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
    """1 of 38 bm25 calls fails: 2.6 % of that leg, 0.9 % of the stage-B run of neighbour x
    merged (round 3 would go on)."""
    w, cfg, swaps = setup(tmp_path)
    commit_stage_a(cfg, tmp_path)
    api = API(w)
    victim = w.records[0]["question"]
    api.fail = lambda b: b["retrieval_mode"] == "bm25" and b["query"] == victim
    assert run(cfg, swaps, api, corpus="neighbour", glossary="merged", stage="B") == f10.EXIT_REFUSED
    s = summary(cfg, "neighbour", "merged")
    assert s["status"] == "hard_stop" and "bm25 leg" in s["reason"] and s["failed_share"] < 0.02
    assert s["study"]["retrievers"] == ["default", "bm25", "norerank"]


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
    """Within one run (neighbour x merged, all stage B): found10's own gate."""
    w, cfg, swaps = setup(tmp_path)
    commit_stage_a(cfg, tmp_path)
    api = API(w)
    api.same_rerank = True
    assert run(cfg, swaps, api, corpus="neighbour", glossary="merged", stage="B") == 0
    assert "reranker was not active" in summary(cfg, "neighbour", "merged")["conditions"]["base"]["gate"]


def test_inactive_reranker_gate_across_the_stages(tmp_path: Path) -> None:
    """base x none: default is stage A, norerank stage B; the stage-B run compares them."""
    for same in (True, False):
        tmp = tmp_path / str(same)
        tmp.mkdir()
        w, cfg, swaps = setup(tmp)
        api = API(w)
        api.same_rerank = same
        assert run(cfg, swaps, api) == 0
        commit_stage_a(cfg, tmp)
        assert run(cfg, swaps, api, stage="B") == 0
        s = summary(cfg, "base", "none.stageB")
        xr = s["study"]["inactive_reranker"]
        assert xr["default_leg"] == "base|none|default" and xr["records"] == len(w.records)
        assert (xr["gate"] is not None) is same and ("reranker was not active" in (xr["gate"] or "")) is same


def test_census_stamp_mismatch_refused(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    with pytest.raises(f10.Refused, match="census"):
        c = ar.validate_config(cfg)
        recs, ident = ar.load_study_records(c)
        r = ar.build_runner(c, recs, ident, "base", "merged", swaps["base-none"], boost_declared=1.0, stage="A",
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
    _, cfg, _ = setup(tmp_path, dry_run=False, pilot=True)
    with pytest.raises(SystemExit, match="not the frozen one"):
        ar.load_study_records(ar.validate_config(cfg))


def test_plan_lists_29_legs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w, cfg, swaps = setup(tmp_path)
    assert run(cfg, swaps, API(w)) == 0
    capsys.readouterr()
    st = ar.run_status(Path(cfg["out_dir"]))
    assert len(st) == 29 and sum(v == "complete" for v in st.values()) == 1
    assert st["base|none|default"] == "complete" and st["base|placebo|default"] == "missing"
    assert ar.main(["--config", str(write_cfg(tmp_path, cfg)), "plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["legs"] == 29 and plan["stage_a_committed"] is False
    assert sum(len(r["legs"]) for r in plan["runs"] if r["stage"] == "A") == 9
    assert sum(len(r["legs"]) for r in plan["runs"] if r["stage"] == "B") == 20


# ------------------------------------------------------------------ stages (§5.7)

def write_cfg(tmp: Path, cfg: dict[str, Any]) -> Path:
    p = tmp / "run-config.json"
    p.write_text(json.dumps(cfg))
    return p


def commit_stage_a(cfg: dict[str, Any], tmp: Path, files: list[tuple[str, Path, str]] = ()) -> Path:  # type: ignore[assignment]
    """A stage-A commit as the judge's commit-stage-a writes it (only the files given), and the
    runs dir's marker: enough for the runner's stage-B gate."""
    import alias_study_judge as aj

    bf = tmp / "batches-A.json"
    bf.write_text(json.dumps({"batches": []}))
    cp = tmp / "stage-a-commit.json"
    cp.write_text(json.dumps({"schema": aj.COMMIT_SCHEMA, "batches_file": str(bf), "batches_A": [],
                              "files": [aj.file_entry(n, p, m) for n, p, m in files]}))
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / ar.COMMIT_MARKER).write_text(json.dumps({"commit": str(cp), "commit_sha256": asc.sha256_file(cp)}))
    return cp


def test_stage_legs_are_the_prereg_s() -> None:
    a = {asc.leg_id(*lg) for lg in ar.stage_legs("A")}
    assert a == {"base|none|default", "base|merged|default", "neighbour|none|default", "random1|none|default",
                 "random2|none|default", "base|none|default#2", "neighbour|none|default#2",
                 "random1|none|default#2", "random2|none|default#2"}
    b = ar.stage_legs("B")
    assert len(b) == 20 and ("base", "placebo", "default", 1) in b and len(ar.study_legs()) == 29


def test_stage_b_is_refused_before_stage_a_is_committed(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    with pytest.raises(f10.Refused, match="only after stage A is committed"):
        run(cfg, swaps, api, stage="B")
    with pytest.raises(f10.Refused, match="no stage-A leg"):
        run(cfg, swaps, api, corpus="neighbour", glossary="merged", stage="A")
    with pytest.raises(f10.Refused, match="--stage"):
        run(cfg, swaps, api, stage=None)  # type: ignore[arg-type]
    assert api.bodies == []


def test_stage_a_is_closed_by_the_commit_and_the_commit_is_verified(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    assert run(cfg, swaps, api) == 0
    rows_a = Path(cfg["out_dir"]) / "base" / "none.jsonl"
    commit_stage_a(cfg, tmp_path, [("rows:base|none|default", rows_a, "prefix")])
    with pytest.raises(f10.Refused, match="committed"):
        run(cfg, swaps, api, repeat=2)
    # a stage-A file rewritten after the commit: stage B refuses before measuring anything
    keep = rows_a.read_bytes()
    rows_a.write_bytes(keep.replace(b'"failed": false', b'"failed": true ', 1))
    n = len(api.bodies)
    with pytest.raises(f10.Refused, match="does not verify"):
        run(cfg, swaps, api, stage="B")
    assert len(api.bodies) == n
    rows_a.write_bytes(keep + b"")
    assert run(cfg, swaps, api, stage="B") == 0


def test_stage_b_writes_its_own_files(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    api = API(w)
    assert run(cfg, swaps, api) == 0
    out = Path(cfg["out_dir"])
    before = {p.name: asc.sha256_file(p) for p in (out / "base").iterdir()}
    commit_stage_a(cfg, tmp_path)
    api.bodies.clear()
    assert run(cfg, swaps, api, stage="B") == 0
    # no default call in stage B, and no stage-A file touched
    assert {b["retrieval_mode"] for b in api.bodies} == {"bm25", "hybrid"} and not any(b.get("rerank") for b in api.bodies)
    assert all(asc.sha256_file(out / "base" / n) == sha for n, sha in before.items())
    s = summary(cfg, "base", "none.stageB")
    assert s["study"]["legs"] == ["base|none|bm25", "base|none|norerank"] and s["study"]["stage_a_commit"]["verified"]
    assert ar.leg_files(out, "base", "none", "bm25")[1].name == "none.stageB.summary.json"
    assert ar.leg_files(out, "base", "none", "default")[1].name == "none.summary.json"
    st = ar.run_status(out)
    assert [st[k] for k in ("base|none|default", "base|none|bm25", "base|none|norerank")] == ["complete"] * 3


def test_single_file_layout_is_still_read(tmp_path: Path) -> None:
    """Legs written before the stages (one file per attachment) resolve to that file."""
    w = fx.World(tmp_path)
    w.write_runs()
    st = ar.run_status(w.runs)
    assert sum(v == "complete" for v in st.values()) == 29
    assert ar.leg_files(w.runs, "base", "none", "bm25")[0].name == "none.jsonl"


# ------------------------------------------------------------------ the placebo leg (§5.3)

def test_placebo_leg_runs_base_default_only(tmp_path: Path) -> None:
    w, cfg, swaps = setup(tmp_path)
    commit_stage_a(cfg, tmp_path)
    api = API(w)
    assert run(cfg, swaps, api, glossary="placebo", stage="B") == 0
    s = summary(cfg, "base", "placebo")
    assert list(s["conditions"]) == ["base"] and s["study"]["placebo"] is True
    assert s["study"]["legs"] == ["base|placebo|default"]
    with pytest.raises(f10.Refused, match="base x default only"):
        run(cfg, swaps, api, corpus="neighbour", glossary="placebo", stage="B")
    with pytest.raises(f10.Refused, match="no stage-A leg"):
        run(cfg, swaps, api, glossary="placebo", stage="A")


def test_placebo_needs_its_own_stamp(tmp_path: Path) -> None:
    _, cfg, _ = setup(tmp_path)
    bad = json.loads(json.dumps(cfg))
    bad["arms"]["placebo"]["stamps"] = ["t-merged"]
    with pytest.raises(f10.Refused, match="own version"):
        ar.validate_config(bad)
    no_placebo = json.loads(json.dumps(cfg))
    del no_placebo["arms"]["placebo"]
    ar.validate_config(no_placebo)


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
               {"paper": "B", "alias": "PCR", "primary": "polymerase chain reactions", "kind": "abbreviation"},
               {"paper": "C", "alias": "QQ", "primary": "quality quotient", "kind": "abbreviation"}]
    rows, counts = ar.build_placebo(entries, {"A", "B", "C", "D"})
    m = ar.placebo_mapping(["A", "B", "C"])
    by_src = {"A": 1, "B": 2, "C": 1}
    assert counts["papers"] == 3 and counts["entries"] == 4 and counts["fixed_points"] == 0
    for recv, src in m.items():
        got = [r for r in rows if r["paper"] == recv]
        assert len(got) == by_src[src] and all(r["placebo"] is True for r in got)
        assert {r["alias"] for r in got} == {e["alias"] for e in entries if e["paper"] == src}
    assert not any(k in r for r in rows for k in ("doc_id", "def_chunk_id", "def_span"))
    # A and B share PCR -> polymerase chain reaction(s): whichever receives the other's list keeps a
    # coincident entry, which the rule does not remove; it is counted
    coincide = int(m["A"] == "B") + int(m["B"] == "A")
    assert counts["own_entry_coincidences"]["entries"] == coincide
    with pytest.raises(SystemExit, match="not in the base export"):
        ar.build_placebo(entries, {"A", "B"})
    with pytest.raises(SystemExit, match="already a placebo"):
        ar.build_placebo(rows)


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
    w, cfg, swaps = setup(tmp_path, dry_run=False, pilot=True)
    api = API(w)
    assert run(cfg, swaps, api) == 0
    desc = {r["record_id"] for r in asc.split_records(w.records)[1]}
    assert {api.q2rec[b["query"]]["record_id"] for b in api.bodies} == desc and len(api.bodies) == len(desc)
    s = summary(cfg, "base", "none")
    assert s["study"]["pilot"] is True and s["study"]["mode"] == "pilot"
    assert s["study"]["record_set"]["pilot_records"] == len(desc)
    pr = tmp_path / "prereg.md"
    pr.write_text("frozen\n")
    main_cfg = {**cfg, "pilot": False, "prereg": {"file": str(pr), "sha256": asc.sha256_file(pr)}}
    with pytest.raises(f10.Refused, match="STUDY-MODE"):
        run(main_cfg, swaps, api, repeat=2)


def test_main_run_pins_the_frozen_prereg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: {"accepted_sha256": "x", "records": len(recs)})
    w, cfg, swaps = setup(tmp_path, dry_run=False)
    with pytest.raises(f10.Refused, match="prereg"):
        ar.validate_config(cfg)
    pr = tmp_path / "prereg.md"
    pr.write_text("frozen\n")
    good = {**cfg, "prereg": {"file": str(pr), "sha256": asc.sha256_file(pr)}}
    api = API(w)
    pr.write_text("edited after the freeze\n")
    with pytest.raises(f10.Refused, match="HARD STOP"):
        run(good, swaps, api)
    assert api.bodies == []
    pr.write_text("frozen\n")
    assert run(good, swaps, api) == 0
    assert summary(good, "base", "none")["study"]["prereg"]["verified"] is True


def test_field_coverage(tmp_path: Path) -> None:
    fc = ar.field_coverage([("a", "t", "AB: alpha beta"), ("b", "t", ""), ("c", "t", "CD: c"), ("d", "t", "")],
                           None, "chunk export + glossary file", {"apply": {"chunks": 4, "chunks_with_glossary": 2}})
    assert fc["available"] and fc["chunks"] == 4 and fc["chunks_with_field"] == 2 and fc["share_with_field"] == 0.5
    assert fc["mean_chars"] == (14 + 5) / 4 and fc["mean_chars_with_field"] == 9.5
    assert fc["backfill"] == {"chunks": 4, "chunks_with_glossary": 2}
    w, cfg, swaps = setup(tmp_path)
    assert run(cfg, swaps, API(w)) == 0
    s = summary(cfg, "base", "none")["study"]["field_coverage"]
    assert s["available"] is False and s["backfill"] == {}
