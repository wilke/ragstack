"""Hermetic tests for the round-2 found@10 runner: a fake API and a fake index census
behind ``httpx.MockTransport``; no network, no store."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import found10 as f10  # noqa: E402
import found10_analysis as fa  # noqa: E402

API = "http://api.invalid"
ES = "http://es.invalid"
INDEX = "ragstack_lib_bench_synthetic"
GOOD_STAMP = "llm/2:ModelX:abc+merge=llm-only@file/1"      # a pure (re-derived) arm, as the backfill stamps it
BARE_STAMP = "none+merge=llm-only@file/1"                  # a document with no LLM version at all, same arm
SEED = "261101|shuf"
PERM = "1aef9aca491b3025740ecf294964a0a6c87132757234c74ba600884769da7ba3"
SHUF_SUFFIX = f"+shuf/1:{SEED}:{PERM[:12]}"


def cfg(**over: Any) -> dict[str, Any]:
    c = {
        "api": {"base_url": API, "retries": 1, "retry_backoff_s": 0},
        "corpora": {"O": {"collection": "coll-o", "index": INDEX, "expected_chunks": 100}},
        "arms": {"armx": {"stamp_prefixes": ["llm/2:ModelX:"], "stamps": ["none"], "merge": "llm-only",
                          "glossary_file_sha256": {"O": "f" * 64}, "shuffle_seed": None},
                 "rules": {"stamps": ["rules/3@file/1"], "glossary_file_sha256": {"O": "e" * 64},
                           "shuffle_seed": None}},
        "conditions": {c: {"top_k": 20} for c in ("base", "bm25", "norerank")},
    }
    c.update(over)
    return f10.normalise_config(c)


def swap(**over: Any) -> dict[str, Any]:
    s = {"schema": f10.SWAP_SCHEMA, "arm": "armx", "index": INDEX, "shuffle_seed": None,
         "utc_start": "2026-11-01T00:00:00Z", "utc_end": "2026-11-01T00:01:00Z",
         "glossary_file": {"path": "g.jsonl", "sha256": "f" * 64},
         "apply": {"dry_run": False, "chunks_to_update": 100, "chunks_written": 100,
                   "glossary_file": {"sha256": "f" * 64}},
         "census_after": {"total": 100, "stamps": {GOOD_STAMP: 90, BARE_STAMP: 10}}}
    s.update(over)
    return s


QUESTIONS = [
    {"record_id": f"r{i}", "set": "O", "corpus": "O", "paper": f"p{i // 2}", "stratum": "S1", "question": f"q{i}",
     "gold_chunks": [f"g{i}"]} for i in range(6)
]


class FakeAPI:
    """Serves /v1/retrieve: record r<i> puts its gold at rank ``rank[i]`` (None = absent).
    ``depth`` sources per call; knobs to inject faults."""

    def __init__(self, ranks: dict[str, int | None], depth: int = 20, stamp: str = GOOD_STAMP) -> None:
        self.ranks, self.depth, self.stamp = ranks, depth, stamp
        self.calls: list[dict[str, Any]] = []
        self.status = 200
        self.degraded_times = 0
        self.inactive_reranker = False
        self.census = {"total": 100, "stamps": {GOOD_STAMP: 90, BARE_STAMP: 10}}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "es.invalid":
            body = {"hits": {"total": {"value": self.census["total"]}},
                    "aggregations": {"v": {"buckets": [{"key": k, "doc_count": v}
                                                       for k, v in self.census["stamps"].items()]}}}
            return httpx.Response(200, json=body)
        if req.method == "GET":
            return httpx.Response(200, json={"version": "test"}) if req.url.path == "/v1/version" \
                else httpx.Response(403, json={})
        body = json.loads(req.content)
        self.calls.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "x"}, headers={"X-Request-Id": "rid"})
        if self.degraded_times:
            self.degraded_times -= 1
            return httpx.Response(200, json={"sources": [], "degraded": [{"component": "rerank", "reason": "x"}]})
        rid = "r" + body["query"][1:]
        ids = [f"x{k}" for k in range(self.depth)]
        if body.get("rerank") and not self.inactive_reranker:
            ids[-1], ids[-2] = ids[-2], ids[-1]  # the reranker moves something: base != norerank
        rk = self.ranks.get(rid)
        if rk is not None and rk <= self.depth:
            ids[rk - 1] = "g" + rid[1:]
        src = [{"doc_id": "d", "chunk_id": c, "content": "", "score": 0.0,
                "metadata": {"symbol_glossary_version": self.stamp}} for c in ids]
        return httpx.Response(200, json={"sources": src}, headers={"X-Request-Id": "rid"})


def run(tmp: Path, api: FakeAPI, conf: dict[str, Any] | None = None, sw: dict[str, Any] | None = None,
        arm: str = "armx", repeat: int | None = None) -> int:
    conf = conf or cfg()
    tmp.mkdir(parents=True, exist_ok=True)
    sp = tmp / f"swap-{arm}.json"
    sp.write_text(json.dumps(sw or swap()))

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(api))

    async def nosleep(_: float) -> None:
        return None

    r = f10.Runner(conf, QUESTIONS, "O", arm, tmp / "out", sp, repeat=repeat, client_factory=factory, sleep=nosleep)
    return asyncio.run(r.run())


# ------------------------------------------------------------- census refusal
def test_verify_swap_record_accepts_a_good_record() -> None:
    v = f10.verify_swap_record(swap(), cfg(), "O", "armx")
    assert v["total"] == 100


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(arm="rules"), "for arm"),
    (lambda s: s.update(index="ragstack_lib_other"), "index"),
    (lambda s: s["census_after"].update(total=99), "expected chunks"),
    (lambda s: s["census_after"]["stamps"].update({"rules/3@file/1": 0}), "does not allow"),
    (lambda s: s["census_after"].update(stamps={GOOD_STAMP: 90, "<none>": 10}), "does not allow"),
    (lambda s: s["apply"]["glossary_file"].update(sha256="0" * 64), "sha256"),
    (lambda s: s.update(shuffle_seed="261101|shuf"), "shuffle seed"),
    (lambda s: s["apply"].update(errors=3), "errors"),
    (lambda s: s["apply"].update(chunks_written=99), "wrote 99 of 100"),
    (lambda s: s["apply"].update(shuffle={"seed": SEED, "stamp_suffix": SHUF_SUFFIX}), "true \\(unshuffled\\) arm"),
    (lambda s: s["apply"]["glossary_file"].update(sha256="f" * 63 + "0"), "other than the one swap.sh hashed"),
    # the same file loaded WITHOUT the re-derive marker is not the pure arm
    (lambda s: s["census_after"].update(stamps={"llm/2:ModelX:abc@file/1": 100}), "does not allow"),
    # a merged (today's rule) file passed off as pure
    (lambda s: s["census_after"].update(stamps={"llm/2:ModelX:abc+merge=merged@file/1": 100}), "does not allow"),
    # a shuffled index passed off as the true arm
    (lambda s: s["census_after"].update(stamps={GOOD_STAMP + SHUF_SUFFIX: 100}), "does not allow"),
    (lambda s: s["apply"].update(dry_run=True), "dry_run"),
    (lambda s: s.update(schema="other/1"), "schema"),
])
def test_verify_swap_record_refuses(mutate: Any, needle: str) -> None:
    s = copy.deepcopy(swap())
    mutate(s)
    with pytest.raises(f10.Refused, match=needle):
        f10.verify_swap_record(s, cfg(), "O", "armx")


def shuffled_swap(**over: Any) -> dict[str, Any]:
    """The swap record of the shuffled control: the backfill's own shuffle record and
    every census stamp carrying its suffix."""
    s = swap(arm="armx-shuf", shuffle_seed=SEED,
             census_after={"total": 100, "stamps": {GOOD_STAMP + SHUF_SUFFIX: 90, BARE_STAMP + SHUF_SUFFIX: 10}})
    s["apply"] = {**s["apply"], "shuffle": {"scheme": "shuf/1", "seed": SEED, "populated": 100, "empty": 0,
                                            "moved": 100, "fixed_points": 0, "fixed_points_before_repair": 3,
                                            "permutation_sha256": PERM, "multiset_sha256": "m" * 64,
                                            "stamp_suffix": SHUF_SUFFIX}}
    s.update(over)
    return s


def test_shuffled_arm_is_told_apart_by_seed_record_and_stamp() -> None:
    conf = cfg()
    conf["arms"]["armx-shuf"] = {**conf["arms"]["armx"], "shuffle_seed": SEED}
    v = f10.verify_swap_record(shuffled_swap(), conf, "O", "armx-shuf")
    assert v["shuffle_suffix"] == SHUF_SUFFIX
    # the true arm's record (same file, unshuffled stamps) does not pass as the shuffled arm
    with pytest.raises(f10.Refused, match="shuffle seed"):
        f10.verify_swap_record(swap(arm="armx-shuf"), conf, "O", "armx-shuf")
    # the seed on the record alone is not enough: the backfill's shuffle record must name it ...
    bad = shuffled_swap()
    bad["apply"]["shuffle"]["seed"] = "261101|other"
    with pytest.raises(f10.Refused, match="shuffle record names seed"):
        f10.verify_swap_record(bad, conf, "O", "armx-shuf")
    # ... and the stamps must carry exactly its suffix (another permutation is another index)
    bad = shuffled_swap(census_after={"total": 100, "stamps": {GOOD_STAMP + f"+shuf/1:{SEED}:000000000000": 100}})
    with pytest.raises(f10.Refused, match="does not allow"):
        f10.verify_swap_record(bad, conf, "O", "armx-shuf")
    # and a shuffled index does not pass as the true arm
    with pytest.raises(f10.Refused, match="true \\(unshuffled\\) arm"):
        f10.verify_swap_record(shuffled_swap(arm="armx", shuffle_seed=None), conf, "O", "armx")


def test_split_stamp_grammar() -> None:
    assert f10.split_stamp("rules/3@file/1") == ("rules/3", None, None)
    assert f10.split_stamp("llm/2:M:x:w80000x4:t4096:k0:r0+merge=llm-only@file/1") == \
        ("llm/2:M:x:w80000x4:t4096:k0:r0", "llm-only", None)
    assert f10.split_stamp("none+merge=none@file/1") == ("none", "none", None)
    assert f10.split_stamp(f"rules/3@file/1{SHUF_SUFFIX}") == ("rules/3", None, (SEED, PERM[:12]))
    for bad in ("rules/3", "@file/1", "rules/3@file/1#b", "rules/3+merge=odd@file/1", "rules/3@file/1+shuf/1:s",
                "rules/3@file/1+shuf/2:s:0123456789ab"):
        assert f10.split_stamp(bad) is None, bad


def test_stamp_allowed() -> None:
    a = {"stamps": ["rules/3@file/1"], "stamp_prefixes": ["llm/2:M:"]}       # no merge key: marker unconstrained
    assert f10.stamp_allowed("rules/3@file/1", a)
    assert f10.stamp_allowed("llm/2:M:x@file/1", a)
    assert f10.stamp_allowed("llm/2:M:x+merge=llm-only@file/1", a)
    assert not f10.stamp_allowed("rules/3", a)
    assert not f10.stamp_allowed("<none>", a) and not f10.stamp_allowed(None, a)
    assert not f10.stamp_allowed("rules/3@file/1" + SHUF_SUFFIX, a)         # a true arm: no suffix
    pure = {"stamp_prefixes": ["llm/2:M:"], "stamps": ["none"], "merge": "llm-only", "shuffle_seed": None}
    assert f10.stamp_allowed("llm/2:M:x+merge=llm-only@file/1", pure)
    assert f10.stamp_allowed("none+merge=llm-only@file/1", pure)
    assert not f10.stamp_allowed("llm/2:M:x@file/1", pure)                  # unmarked = not the pure file
    assert not f10.stamp_allowed("llm/2:M:x+merge=merged@file/1", pure)
    assert not f10.stamp_allowed("none+merge=none@file/1", pure)
    rules = {"stamps": ["rules/3"], "merge": None, "shuffle_seed": None}
    assert f10.stamp_allowed("rules/3@file/1", rules)
    assert not f10.stamp_allowed("rules/3+merge=rules-only@file/1", rules)
    shuf = {**pure, "shuffle_seed": SEED}
    st = "llm/2:M:x+merge=llm-only@file/1" + SHUF_SUFFIX
    assert f10.stamp_allowed(st, shuf) and f10.stamp_allowed(st, shuf, SHUF_SUFFIX)
    assert not f10.stamp_allowed(st, shuf, f"+shuf/1:{SEED}:000000000000")
    assert not f10.stamp_allowed("llm/2:M:x+merge=llm-only@file/1", shuf)
    assert not f10.stamp_allowed("llm/2:M:x+merge=llm-only@file/1+shuf/1:other:0123456789ab", shuf)


def test_load_questions_takes_the_drafters_records(tmp_path: Path) -> None:
    q = tmp_path / "accepted-O.jsonl"
    q.write_text(json.dumps({"record_id": "O-1", "set": "O", "paper": "P", "stratum": "S1", "question": "q",
                             "gold_chunks": ["c1"], "binding_terms": ["x"], "quantity": "x"}) + "\n")
    r = f10.load_questions([q])[0]
    assert r["corpus"] == "O" and r["gold_chunks"] == ["c1"] and r["binding_terms"] == ["x"]
    q.write_text(json.dumps({"record_id": "O-1", "set": "O", "paper": "P", "stratum": "S1", "question": "q",
                             "gold_chunk_ids": ["c1"]}) + "\n")
    with pytest.raises(f10.Refused, match="gold_chunks"):
        f10.load_questions([q])
    q.write_text(json.dumps({"record_id": "O-1", "paper": "P", "stratum": "S1", "question": "q",
                             "gold_chunks": ["c1"]}) + "\n")
    with pytest.raises(f10.Refused, match="'set'"):
        f10.load_questions([q])


def test_load_config_requires_base_url(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"api": {}, "corpora": {}, "arms": {}}))
    with pytest.raises(f10.Refused, match="base_url"):
        f10.load_config(p)


# ------------------------------------------------------------- scoring
def test_score_response_ranks_and_found10() -> None:
    src = [{"chunk_id": f"c{i}", "metadata": {}} for i in range(1, 21)]
    s = f10.score_response(src, ["c10", "c15", "zz"], 20)
    assert s["gold_ranks"] == {"c10": 10, "c15": 15, "zz": None}
    assert s["best_rank"] == 10 and s["found10"] == 1
    s = f10.score_response(src, ["c11"], 20)
    assert s["found10"] == 0 and s["best_rank"] == 11
    s = f10.score_response(src[:12], ["c11"], 20)
    assert s["depth_short"] and s["served"] == 12


# ------------------------------------------------------------- end to end
RANKS = {"r0": 1, "r1": 10, "r2": 11, "r3": None, "r4": 3, "r5": 20}


def test_run_complete_and_analyse(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    assert run(tmp_path, api) == f10.EXIT_OK
    s = json.loads((tmp_path / "out/O/armx.summary.json").read_text())
    assert s["status"] == "complete" and s["calls"] == 18 and s["failed_calls"] == 0
    assert s["conditions"]["base"]["found10"] == 3  # ranks 1, 10, 3
    assert s["conditions"]["base"]["served_min"] == 20 and s["conditions"]["base"]["gate"] is None
    assert "provenance" in s and "citable" in s
    # every request names the collection and pins the condition
    base = [c for c in api.calls if c["retrieval_mode"] == "hybrid" and c["rerank"]]
    assert base and all(c["collection"] == "coll-o" and c["use_graph"] is False for c in base)
    assert all(c["top_k"] == 20 for c in api.calls)
    rows = [json.loads(x) for x in (tmp_path / "out/O/armx.jsonl").read_text().splitlines()]
    r1 = next(r for r in rows if r["key"] == "r1|base")
    assert r1["gold_ranks"] == {"g1": 10} and r1["found10"] == 1
    # rerun: nothing left to do, no new call
    n = len(api.calls)
    assert run(tmp_path, api) == f10.EXIT_OK and len(api.calls) == n
    # analyse reads it back
    per, gated, runs = f10.collect(tmp_path / "out", [dict(q) for q in QUESTIONS])
    assert runs["O/armx"]["status"] == "complete" and not gated
    assert per["r1"]["armx"]["found"] == {"base": 1, "bm25": 1, "norerank": 1}
    # the wording proxy's results JSON is the proxy outcome (its per_record[rid][arm]["pass"])
    px = tmp_path / "proxy.json"
    px.write_text(json.dumps({"schema": "wording-proxy/1", "per_record": {
        "r0": {"armx": {"pass": True, "match": {"chunk_id": "g0", "symbol": "x", "term": "t"}}, "rules": {"pass": False}},
        "r1": {"armx": {"pass": False}}, "zz": {"armx": {"pass": True}}}}))
    per, _, _ = f10.collect(tmp_path / "out", [dict(q) for q in QUESTIONS], px)
    assert per["r0"]["armx"]["proxy"] == 1 and per["r0"]["rules"]["proxy"] == 0 and per["r1"]["armx"]["proxy"] == 0
    assert per["r2"]["armx"]["proxy"] is None and "zz" not in per
    px.write_text("".join(json.dumps({"record_id": "r0", "arm": "armx", "pass": True}) + "\n"))  # the old JSONL guess
    with pytest.raises(f10.Refused, match="wording-proxy"):
        f10.collect(tmp_path / "out", [dict(q) for q in QUESTIONS], px)


def test_inactive_reranker_gates_base(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    api.inactive_reranker = True  # `rerank: true` answered with the RRF order on every record
    assert run(tmp_path, api) == f10.EXIT_OK
    s = json.loads((tmp_path / "out/O/armx.summary.json").read_text())
    assert s["conditions"]["base"]["gate"] and "reranker was not active" in s["conditions"]["base"]["gate"]
    assert s["conditions"]["base"]["identical_to_norerank"] == {"records": 6, "identical": 6}
    assert s["conditions"]["norerank"]["gate"] is None
    assert s["server_assumed"]["retrieval_candidate_multiplier"] == 2 and s["server_assumed"]["rrf_k"] == 60


def test_depth_short_gates_the_condition(tmp_path: Path) -> None:
    api = FakeAPI(RANKS, depth=10)  # the server clamps 20 -> 10
    assert run(tmp_path, api) == f10.EXIT_OK
    s = json.loads((tmp_path / "out/O/armx.summary.json").read_text())
    assert all(fa.GATE in s["conditions"][c]["gate"] for c in ("base", "bm25", "norerank"))
    assert s["conditions"]["base"]["requested"] == 20 and s["conditions"]["base"]["served_max"] == 10
    _, gated, _ = f10.collect(tmp_path / "out", [dict(q) for q in QUESTIONS])
    assert "armx|base" in gated


def test_wrong_census_refuses_before_any_call(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    with pytest.raises(f10.Refused):
        run(tmp_path, api, sw=swap(arm="rules"))
    assert api.calls == []


def test_live_census_mismatch_refuses(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    api.census = {"total": 100, "stamps": {"rules/3@file/1": 100}}
    with pytest.raises(f10.Refused, match="live census"):
        run(tmp_path, api, conf=cfg(census={"es_url": ES}))
    assert api.calls == []


def test_live_census_match_is_recorded(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    assert run(tmp_path, api, conf=cfg(census={"es_url": ES})) == f10.EXIT_OK
    s = json.loads((tmp_path / "out/O/armx.summary.json").read_text())
    assert s["census"]["source"] == "swap_record+live" and s["census"]["live_after"]["total"] == 100


def test_served_hit_with_foreign_stamp_hard_stops(tmp_path: Path) -> None:
    api = FakeAPI(RANKS, stamp="rules/3@file/1")  # the index serves another arm
    assert run(tmp_path, api) == f10.EXIT_REFUSED
    s = json.loads((tmp_path / "out/O/armx.summary.json").read_text())
    assert s["status"] == "hard_stop" and "does not allow" in s["reason"]


def test_degraded_response_is_retried_then_failed(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    api.degraded_times = 1  # first call degraded, the retry is clean
    conf = cfg()
    conf["api"]["inflight"] = 1
    assert run(tmp_path, api, conf=conf) == f10.EXIT_OK
    rows = [json.loads(x) for x in (tmp_path / "out/O/armx.jsonl").read_text().splitlines()]
    assert sum(r["attempts"] == 2 for r in rows) == 1 and not any(r["failed"] for r in rows)
    api2 = FakeAPI(RANKS)
    api2.degraded_times = 10_000  # always degraded: every call fails -> > 2% -> hard stop
    assert run(tmp_path / "b", api2) == f10.EXIT_REFUSED
    s = json.loads((tmp_path / "b/out/O/armx.summary.json").read_text())
    assert s["failed_calls"] == 18 and s["status"] == "hard_stop"


def test_4xx_is_a_configuration_error(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    api.status = 404
    with pytest.raises(f10.Refused, match="HTTP 404"):
        run(tmp_path, api)


def test_pause_file_then_resume(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out/PAUSE").write_text("")
    api = FakeAPI(RANKS)
    assert run(tmp_path, api) == f10.EXIT_PAUSED
    assert api.calls == []
    (tmp_path / "out/PAUSE").unlink()
    assert run(tmp_path, api) == f10.EXIT_OK
    assert len(api.calls) == 18


def test_repeat_label(tmp_path: Path) -> None:
    api = FakeAPI(RANKS)
    conf = cfg()
    assert run(tmp_path, api, conf=conf, repeat=2) == f10.EXIT_OK
    assert (tmp_path / "out/O/armx#2.summary.json").exists()


def test_inflight_hard_cap() -> None:
    conf = cfg()
    conf["api"]["inflight"] = 64
    r = f10.Runner(conf, QUESTIONS, "O", "armx", Path("/nonexistent"), Path("/nonexistent"))
    assert r.inflight == 4


def test_cmd_analyse_end_to_end(tmp_path: Path) -> None:
    conf = cfg()
    conf["arms"]["armx"]["glossary_file_sha256"]["O"] = "f" * 64
    for arm, ranks in (("armx", RANKS), ("rules", dict.fromkeys(RANKS))):
        api = FakeAPI(ranks, stamp=GOOD_STAMP if arm == "armx" else "rules/3@file/1")
        sw = swap(arm=arm) if arm == "armx" else swap(
            arm="rules", glossary_file={"path": "r.jsonl", "sha256": "e" * 64},
            apply={"dry_run": False, "glossary_file": {"sha256": "e" * 64}},
            census_after={"total": 100, "stamps": {"rules/3@file/1": 100}})
        assert run(tmp_path, api, conf=conf, sw=sw, arm=arm) == f10.EXIT_OK
    q = tmp_path / "q.jsonl"
    q.write_text("".join(json.dumps(x) + "\n" for x in QUESTIONS))
    pc = tmp_path / "cfg.json"
    pc.write_text(json.dumps({**conf, "analysis": {
        "n_perm": 200, "n_boot": 200, "family_corpora": ["O"],
        "hypotheses": {"H1": ["armx", "rules", "superiority", "found"]}, "secondary": {}}}))
    assert f10.main(["analyse", "--private-config", str(pc), "--questions", str(q),
                     "--out", str(tmp_path / "out"), "--no-power"]) == 0
    res = json.loads((tmp_path / "out/results-found10.json").read_text())
    h1 = res["family"]["hypotheses"]["H1"]
    # gains on r0, r1, r4 -> papers p0 (+2), p2 (+1): two non-zero papers, gated
    assert (h1["gained"], h1["lost"]) == (3, 0)
    assert h1["n_pages_nonzero"] == 2 and h1["status"] == fa.GATE
    assert set(res["runs"]) == {"O/armx", "O/rules"} and "provenance" in res
