"""The reranker-test harness against synthetic data and in-process mock servers only.

Every endpoint (the retrieval API, the cross-encoder sidecar) is a mock bound to
127.0.0.1 on an ephemeral port; the dense leg uses fixed vectors; the tokenizer is
a whitespace fake. Chunks, questions and quotes are synthetic. Store URLs, if
anything were to read them, point at a dead port.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import pytest

os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("ELASTICSEARCH_URL", "http://127.0.0.1:1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chunktrial_stats as cstats  # noqa: E402
import reranker_test as rt  # noqa: E402
from section_aware_trial import C  # noqa: E402

# ------------------------------------------------------------------ mocks


class Mock:
    """A JSON HTTP server on 127.0.0.1:<ephemeral>; ``handler(method, path, body) -> (status, obj)``."""

    def __init__(self, handler: Any) -> None:
        self.inflight = 0
        self.max_inflight = 0
        self.calls: list[tuple[str, str, Any]] = []
        lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._go("GET", None)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self._go("POST", json.loads(self.rfile.read(n) or b"null"))

            def _go(self, method: str, body: Any) -> None:
                with lock:
                    outer.inflight += 1
                    outer.max_inflight = max(outer.max_inflight, outer.inflight)
                    outer.calls.append((method, self.path, body))
                try:
                    status, obj = handler(method, self.path, body)
                finally:
                    with lock:
                        outer.inflight -= 1
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture(autouse=True)
def _no_provenance_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "provenance", lambda: {"commit": "0" * 40, "dirty": False, "untracked": 0,
                                                   "citable": True})


# ------------------------------------------------------------------ synthetic data

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
         "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango"]


def make_rows(n_docs: int = 10, per_doc: int = 4) -> list[dict[str, Any]]:
    rows = []
    for d in range(n_docs):
        for i in range(per_doc):
            cid = f"d{d}c{i}"
            w = [WORDS[(d * 3 + i * 7 + j) % len(WORDS)] for j in range(12)]
            rows.append({"chunk_id": cid, "doc_id": f"doc{d}",
                         "content": f"chunk {cid} speaks of {' '.join(w)} token{d}x{i} end.",
                         "symbol_glossary": (f"k{d}{i} — rate constant {d}\nT{d} — temperature {i}" if i % 2 == 0 else ""),
                         "metadata": {"prev_chunk_id": f"d{d}c{i - 1}" if i else None,
                                      "next_chunk_id": f"d{d}c{i + 1}" if i + 1 < per_doc else None}})
    return rows


def make_questions(n_docs: int = 10) -> list[dict[str, Any]]:
    qs = []
    for d in range(n_docs):
        qs.append({"record_id": f"q{d}", "question": f"what does token{d}x1 say about the matter at hand?",
                   "gold": [f"token{d}x1 end."], "doc_id": f"doc{d}"})
    return qs


@pytest.fixture
def data(tmp_path: Path) -> dict[str, Any]:
    rows, qs = make_rows(), make_questions()
    ch, qf = tmp_path / "chunks.jsonl", tmp_path / "questions.jsonl"
    ch.write_text("".join(json.dumps(r) + "\n" for r in rows))
    qf.write_text("".join(json.dumps(q) + "\n" for q in qs))
    return {"rows": rows, "qs": qs, "chunks": ch, "questions": qf, "dir": tmp_path}


def corpus(rows: list[dict[str, Any]] | None = None) -> rt.Corpus:
    return rt.corpus_from_rows(rows or make_rows(), {"source": "test"})


# ------------------------------------------------------------------ renderings and the shuffle


def test_content_gloss_is_the_live_rerank_passage() -> None:
    from ragstack.models import Chunk
    from ragstack.scoring.scorers import rerank_passage

    for g in ("", "k1 — rate"):
        c = C("x", "d", "body text", "", g)
        live = rerank_passage(Chunk(id="x", doc_id="d", content="body text", symbol_glossary=g), True)
        assert rt.render(c, "content+gloss") == live
    c = C("x", "d", "body", "", "G")
    assert rt.render(c, "content") == "body"
    assert rt.render(c, "gloss+content") == "G\nbody"
    assert rt.render(c, "gloss-only") == "G"
    assert rt.render(c, "content+shuffled", {"x": "H"}) == "body\nH"
    assert rt.render(C("y", "d", "body", "", ""), "content+shuffled", {}) == "body"


def test_shuffle_is_seeded_and_keeps_population_and_strings() -> None:
    cp = corpus()
    m1, meta1 = rt.shuffle_glossaries(cp.chunks, 261029, "string")
    m2, _ = rt.shuffle_glossaries(list(reversed(cp.chunks)), 261029, "string")
    assert m1 == m2  # order of the dump does not matter
    bearers = {c.id: c.glossary for c in cp.chunks if c.glossary}
    assert set(m1) == set(bearers)
    assert sorted(m1.values()) == sorted(bearers.values())
    assert meta1["bearers"] == len(bearers) and meta1["unchanged"] < len(bearers)
    m3, _ = rt.shuffle_glossaries(cp.chunks, 1, "string")
    assert m3 != m1
    me, meta_e = rt.shuffle_glossaries(cp.chunks, 261029, "entry")
    assert all(len(me[k].split("\n")) == len(v.split("\n")) for k, v in bearers.items())
    assert sorted(x for v in me.values() for x in v.split("\n")) == sorted(
        x for v in bearers.values() for x in v.split("\n"))
    assert meta_e["unit"] == "entry"


# ------------------------------------------------------------------ gold


def test_gold_rule_partial_quote_and_exclusions(data: dict[str, Any]) -> None:
    qs = make_questions(3)
    qs.append({"record_id": "qx", "question": "nothing", "gold": ["no such text anywhere at all"], "doc_id": "doc0"})
    qs.append({"record_id": "qv", "question": "invalid", "gold": ["token1x1 end."], "doc_id": "doc1", "valid": False})
    # a quote cut by a chunk boundary: its first 60% is in d2c1, the rest is not
    qs.append({"record_id": "qp", "question": "partial", "gold": ["token2x1   END. extra"], "doc_id": "doc2"})
    qf = data["dir"] / "q2.jsonl"
    qf.write_text("".join(json.dumps(q) + "\n" for q in qs))
    questions, meta = rt.load_questions(qf, {"id": None, "query": None, "quotes": None, "cluster": None})
    g = rt.build_gold(questions, corpus())
    assert g["records"]["q0"]["quotes"] == [["d0c1"]]
    assert g["records"]["qx"]["excluded"] == "no_gold_chunk_in_index"
    assert g["records"]["qv"]["excluded"] == "invalid_at_build"
    assert g["records"]["qp"]["quotes"] == [["d2c1"]] and g["records"]["qp"]["excluded"] is None
    assert g["excluded"] == {"no_gold_chunk_in_index": 1, "invalid_at_build": 1} and g["n_included"] == 4
    assert meta["records"] == 6 and meta["clusters"] == 3


def test_gold_partial_60_percent() -> None:
    cp = rt.corpus_from_rows([{"chunk_id": "a", "doc_id": "d", "content": "xxxx The quick brown fox"},
                              {"chunk_id": "b", "doc_id": "d", "content": "jumps over the lazy dog yyyy"}], {})
    q = [{"id": "r", "query": "q", "cluster": "d", "quotes": ["The quick brown fox jumps over"],
          "gold_chunk_ids": [], "valid": True}]
    g = rt.build_gold(q, cp)
    assert g["records"]["r"]["quotes"] == [["a"]]  # first 60% in a; last 60% straddles


def test_rank_rules() -> None:
    rec = {"quotes": [["a", "b"], ["c"]]}
    assert rt.rank_of_gold(["x", "b", "y", "c"], rec, "any") == 2
    assert rt.rank_of_gold(["x", "b", "y", "c"], rec, "all") == 4
    assert rt.rank_of_gold(["x", "b"], rec, "all") is None
    assert rt.rank_of_gold(["x"], rec, "any") is None


# ------------------------------------------------------------------ in-process pools


def fixed_vectors(cp: rt.Corpus, questions: list[dict[str, Any]], dim: int = 16) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    cm = rng.normal(size=(len(cp.chunks), dim)).astype(np.float32)
    cm /= np.linalg.norm(cm, axis=1, keepdims=True)
    qm = np.stack([cm[[c.id for c in cp.chunks].index(f"d{int(q['id'][1:])}c2")] for q in questions])
    return qm, cm


def test_inprocess_hybrid_is_rrf_of_multiplied_legs(data: dict[str, Any]) -> None:
    cp = corpus(data["rows"])
    questions, _ = rt.load_questions(data["questions"], {"id": None, "query": None, "quotes": None, "cluster": None})
    qm, cm = fixed_vectors(cp, questions)
    ip = rt.InProcess(cp, 1.0, qm, cm)
    lists, depth = rt.capture_inprocess(ip, questions, [5, 10, 20], ["bm25", "vector", "hybrid"], 2)
    ids = [c.id for c in cp.chunks]
    for qi, q in enumerate(questions):
        b, d = ip.bm25(q["query"], 40), ip.dense(qi, 40)
        for D in (5, 10, 20):
            # hand-rolled RRF k=60 over the 2D prefixes, vector leg first
            sc: dict[int, float] = {}
            for lst in (d[:2 * D], b[:2 * D]):
                for r, i in enumerate(lst):
                    sc[i] = sc.get(i, 0) + 1 / (60 + r + 1)
            want = [ids[i] for i in sorted(sc, key=lambda i: -sc[i])][:D]
            assert lists[f"hybrid@{D}"][q["id"]] == want
    assert set(lists) == {"bm25@20", "vector@20", "hybrid@5", "hybrid@10", "hybrid@20"}
    assert depth["vector@20"]["status"] == "ok" and depth["hybrid@20"]["requested"] == 20
    # BM25 can legitimately return fewer (only chunks sharing a term score): exhausted, not under-served
    assert depth["bm25@20"]["status"] in ("ok", "exhausted")


def test_inprocess_glossary_boost_moves_bm25() -> None:
    rows = [{"chunk_id": "a", "doc_id": "d", "content": "plain words here", "symbol_glossary": "zeta — rate"},
            {"chunk_id": "b", "doc_id": "d", "content": "zeta appears in this text", "symbol_glossary": ""}]
    cp = rt.corpus_from_rows(rows, {})
    z = np.zeros((1, 2), dtype=np.float32)
    assert rt.InProcess(cp, 0.0, z, np.zeros((2, 2), np.float32)).bm25("zeta", 5) == [1]
    assert 0 in rt.InProcess(cp, 1.0, z, np.zeros((2, 2), np.float32)).bm25("zeta", 5)


def test_pool_prefix_for_single_legs_only() -> None:
    lists = {"bm25@100": {"r": [str(i) for i in range(100)]}, "hybrid@100": {"r": ["x"] * 100}}
    assert rt.pool_list(lists, "bm25", 10) == {"r": [str(i) for i in range(10)]}
    assert rt.pool_list(lists, "hybrid", 50) is None  # a fused pool is not a prefix of a deeper one
    assert rt.pool_source_key(lists, "bm25", 50) == "bm25@100"


# ------------------------------------------------------------------ API pools: depth read-back


def api_server(clamp: int | None = None, degraded: bool = False, config: dict[str, Any] | None = None) -> Mock:
    ids = [r["chunk_id"] for r in make_rows()]

    def h(method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/v1/config":
            return 200, config if config is not None else {"rerank_candidates": 50,
                                                            "reranker_model": rt.RERANKER_MODEL}
        if path == "/v1/retrieve":
            k = body["top_k"] if clamp is None else min(body["top_k"], clamp)
            seed = sum(map(ord, body["query"])) + (7 if body["retrieval_mode"] == "bm25" else 0)
            order = sorted(ids, key=lambda x: (hash((seed, x)) % 997, x))
            resp: dict[str, Any] = {"sources": [{"chunk_id": x, "doc_id": "d", "content": "", "score": 0,
                                                 "metadata": {}} for x in order[:k]]}
            if degraded and body.get("rerank"):
                resp["degraded"] = [{"component": "rerank", "reason": "down"}]
            return 200, resp
        return 404, {}

    return Mock(h)


def server_settings(tmp: Path, **over: Any) -> str:
    s = {k: v for k, v in rt.PREREG_SETTINGS.items() if k != "rerank_candidates"}
    s.update(over)
    p = tmp / "server.json"
    p.write_text(json.dumps(s))
    return str(p)


def pools_argv(data: dict[str, Any], url: str, *extra: str) -> list[str]:
    return ["pools", "--backend", "api", "--base-url", url, "--questions", str(data["questions"]),
            "--chunks", str(data["chunks"]), "--depths", "10,20,40", "--server-settings",
            server_settings(data["dir"]), "--out", str(data["dir"] / "pools.json"),
            "--gold-out", str(data["dir"] / "gold.json"), *extra]


def test_api_pools_record_requested_and_served(data: dict[str, Any]) -> None:
    m = api_server()
    try:
        assert rt.main(pools_argv(data, m.url, "--live", "hybrid@20")) == 0
    finally:
        m.close()
    out = json.loads((data["dir"] / "pools.json").read_text())
    assert out["depth"]["hybrid@40"] == {**out["depth"]["hybrid@40"], "requested": 40, "served_min": 40,
                                         "status": "ok"}
    assert out["depth"]["live:hybrid@20"]["requested"] == 10
    assert set(out["lists"]) == {"bm25@40", "vector@40", "hybrid@10", "hybrid@20", "hybrid@40", "live:hybrid@20"}
    assert out["manipulation"]["ok"] and m.max_inflight <= rt.MAX_INFLIGHT
    live_bodies = [b for meth, p, b in m.calls if p == "/v1/retrieve" and b.get("rerank")]
    assert live_bodies and all(b["rerank_candidates"] == 20 and b["top_k"] == 10 for b in live_bodies)
    assert "queries" not in out  # --omit-text is the default


def test_api_clamp_fails_the_run(data: dict[str, Any]) -> None:
    """A gateway that silently clamps top_k to 10: requested 40, served 10 -> exit 1, marked."""
    m = api_server(clamp=10)
    try:
        assert rt.main(pools_argv(data, m.url)) == 1
    finally:
        m.close()
    out = json.loads((data["dir"] / "pools.json").read_text())
    d = out["depth"]["hybrid@40"]
    assert d["requested"] == 40 and d["served_max"] == 10 and d["status"] == "under-served"
    assert d["n_under_served"] == 10
    assert out["depth"]["hybrid@10"]["status"] == "ok"
    assert out["status"] == "under_served"


def test_api_clamp_allow_short_marks_gate_and_stats_inherits(data: dict[str, Any]) -> None:
    m = api_server(clamp=20)
    try:
        assert rt.main(pools_argv(data, m.url, "--allow-short")) == 0
    finally:
        m.close()
    out = json.loads((data["dir"] / "pools.json").read_text())
    assert out["depth"]["hybrid@40"]["status"] == rt.GNE and out["depth"]["bm25@40"]["status"] == rt.GNE
    assert rt.main(["stats", "--lists", str(data["dir"] / "pools.json"), "--gold", str(data["dir"] / "gold.json"),
                    "--set-label", "synthetic", "--depths", "10,20,40", "--n-perm", "200", "--n-boot", "200",
                    "--out", str(data["dir"] / "res.json")]) == 0
    res = json.loads((data["dir"] / "res.json").read_text())
    assert res["arms"]["A0"]["gate"] and "under-served" in res["arms"]["A0"]["gate"][0]
    assert res["depth_read_back"]["hybrid@40"]["served_max"] == 20


def test_api_degraded_rerank_is_under_served(data: dict[str, Any]) -> None:
    m = api_server(degraded=True)
    try:
        assert rt.main(pools_argv(data, m.url, "--live", "hybrid@20")) == 1
    finally:
        m.close()
    out = json.loads((data["dir"] / "pools.json").read_text())
    assert out["depth"]["live:hybrid@20"]["degraded_responses"] == 10
    assert out["depth"]["live:hybrid@20"]["status"] == "under-served"


def test_api_refuses_depth_over_max_top_k(data: dict[str, Any]) -> None:
    m = api_server()
    try:
        argv = pools_argv(data, m.url)
        argv[argv.index("10,20,40")] = "50,200"
        with pytest.raises(SystemExit, match="MAX_TOP_K"):
            rt.main(argv)
        assert not [c for c in m.calls if c[1] == "/v1/retrieve"]
    finally:
        m.close()


def test_manipulation_mismatch_stops_before_retrieval(data: dict[str, Any]) -> None:
    m = api_server(config={"rerank_candidates": 30, "reranker_model": rt.RERANKER_MODEL})
    try:
        assert rt.main(pools_argv(data, m.url)) == 1
        assert not [c for c in m.calls if c[1] == "/v1/retrieve"]
    finally:
        m.close()
    out = json.loads((data["dir"] / "pools.json").read_text())
    assert out["status"] == "manipulation_check_mismatch"
    assert out["manipulation"]["values"]["rerank_candidates"] == {"expected": 50, "observed": 30,
                                                                   "source": "GET /v1/config", "ok": False}


def test_manipulation_missing_value_is_a_mismatch() -> None:
    r = rt.manipulation_check({"rrf_k": 60}, {"rrf_k": 60, "retrieval_max_per_doc": 0}, {"rrf_k": "x"})
    assert not r["ok"] and r["values"]["retrieval_max_per_doc"]["source"] == "missing"


def test_base_url_has_no_default(data: dict[str, Any]) -> None:
    argv = pools_argv(data, "x")
    i = argv.index("--base-url")
    del argv[i:i + 2]
    with pytest.raises(SystemExit, match="no default"):
        rt.main(argv)


def test_omit_text_refuses_a_leak(tmp_path: Path) -> None:
    q = [{"id": "r", "query": "a private question text", "quotes": ["a private quote text"]}]
    with pytest.raises(SystemExit, match="query/quote"):
        rt.write_out(tmp_path / "x.json", {"oops": "the a private quote text"}, q, True)
    rt.write_out(tmp_path / "y.json", {"oops": "the a private quote text"}, q, False)


# ------------------------------------------------------------------ rerank via a mock sidecar


def sidecar_server(model: str = rt.RERANKER_MODEL, delay: float = 0.0) -> Mock:
    def h(method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/ready":
            return 200, {"status": "ok", "model": model, "device": "cuda:0", "loaded": True}
        if path == "/rerank":
            time.sleep(delay)
            q = set(body["query"].split())
            sc = [float(len(q & set(d.split()))) + 1e-3 * len(d) for d in body["documents"]]
            idx = sorted(range(len(sc)), key=lambda i: -sc[i])[: body["top_k"]]
            return 200, {"indices": idx, "scores": [sc[i] for i in idx]}
        return 404, {}

    return Mock(h)


def offline_order(cp: rt.Corpus, query: str, ids: list[str], rendering: str = "content+gloss") -> list[str]:
    q = set(query.split())
    docs = [rt.render(cp.by_id[x], rendering) for x in ids]
    sc = [float(len(q & set(d.split()))) + 1e-3 * len(d) for d in docs]
    return [ids[i] for i in sorted(range(len(sc)), key=lambda i: -sc[i])]


def write_pools(data: dict[str, Any], live_ok: bool = True) -> Path:
    cp = corpus(data["rows"])
    questions, _ = rt.load_questions(data["questions"], {"id": None, "query": None, "quotes": None, "cluster": None})
    qm, cm = fixed_vectors(cp, questions)
    lists, depth = rt.capture_inprocess(rt.InProcess(cp, 1.0, qm, cm), questions, [10, 20, 50], LEGS3, 2)
    live = {}
    for q in questions:
        o = offline_order(cp, q["query"], lists["hybrid@50"][q["id"]])[:10]
        live[q["id"]] = o if live_ok else list(reversed(o))
    lists["live:hybrid@50"] = live
    depth["live:hybrid@50"] = {"requested": 10, "status": "ok"}
    out = {**rt.new_lists("pools"), "lists": lists, "depth": depth, "depths": [10, 20, 50],
           "manipulation": {"ok": True}}
    p = data["dir"] / "pools.json"
    p.write_text(json.dumps(out))
    g = rt.build_gold(questions, cp)
    (data["dir"] / "gold.json").write_text(json.dumps({"schema": rt.GOLD_SCHEMA, **g}))
    return p


LEGS3 = ["bm25", "vector", "hybrid"]


def rerank_argv(data: dict[str, Any], url: str, *extra: str) -> list[str]:
    return ["rerank", "--questions", str(data["questions"]), "--chunks", str(data["chunks"]),
            "--pools", str(data["dir"] / "pools.json"), "--rerank-url", url,
            "--cache", str(data["dir"] / "cache.jsonl"), "--out", str(data["dir"] / "rr.json"), *extra]


def test_rerank_identity_gate_pass_and_renderings(data: dict[str, Any]) -> None:
    write_pools(data)
    m = sidecar_server(delay=0.01)
    try:
        assert rt.main(rerank_argv(data, m.url)) == 0
    finally:
        m.close()
    out = json.loads((data["dir"] / "rr.json").read_text())
    assert out["identity_gate"]["status"] == "pass" and out["identity_gate"]["n_probes"] == 3
    assert out["identity_all"] == {"n": 10, "equal_top10": 10}
    keys = set(out["lists"])
    assert {f"rr:hybrid@50:{r}" for r in rt.RENDERINGS} <= keys
    assert {"rr:hybrid@10:content+gloss", "rr:bm25@20:content+gloss", "rr:vector@50:content+gloss"} <= keys
    assert m.max_inflight <= rt.MAX_INFLIGHT
    # one request per (query, pool), the whole pool, top_k = pool size
    reqs = [b for _, p, b in m.calls if p == "/rerank"]
    assert all(b["top_k"] == len(b["documents"]) for b in reqs)
    assert out["sidecar"]["model"] == rt.RERANKER_MODEL and out["sidecar"]["fp16"] is True
    # rerun: every score comes from the cache
    m2 = sidecar_server()
    try:
        assert rt.main(rerank_argv(data, m2.url)) == 0
        assert not [c for c in m2.calls if c[1] == "/rerank"]
    finally:
        m2.close()


def test_rerank_identity_gate_failure_stops_everything(data: dict[str, Any]) -> None:
    """Remove the gate and this test fails: a live list the offline path cannot
    reproduce must stop the run after the 3 probe calls."""
    write_pools(data, live_ok=False)
    m = sidecar_server()
    try:
        assert rt.main(rerank_argv(data, m.url)) == 1
        assert len([c for c in m.calls if c[1] == "/rerank"]) == 3
    finally:
        m.close()
    out = json.loads((data["dir"] / "rr.json").read_text())
    assert out["identity_gate"]["status"] == "fail" and out["status"] == "identity_gate_failed"
    assert out["lists"] == {}


def test_rerank_refuses_a_different_model(data: dict[str, Any]) -> None:
    write_pools(data)
    m = sidecar_server(model="some/other-reranker")
    try:
        assert rt.main(rerank_argv(data, m.url)) == 1
        assert not [c for c in m.calls if c[1] == "/rerank"]
    finally:
        m.close()


def test_rerank_rejects_a_bad_sidecar_response() -> None:
    m = Mock(lambda meth, p, b: (200, {"indices": [0, 0], "scores": [1.0, 0.5]}))
    try:
        with pytest.raises(ValueError, match="permutation"):
            rt.Sidecar(m.url, 4).rerank("q", ["a", "b"])
    finally:
        m.close()


def test_inflight_cannot_be_raised() -> None:
    assert rt.Sidecar("http://127.0.0.1:1", 64).inflight == rt.MAX_INFLIGHT
    assert rt.Api("http://127.0.0.1:1", None, None, 64).inflight == rt.MAX_INFLIGHT


def test_sidecar_versions_and_pin_drift(tmp_path: Path) -> None:
    for d in ("sentence_transformers-5.6.0.dist-info", "transformers-5.12.1.dist-info", "torch-2.12.1.dist-info"):
        (tmp_path / d).mkdir()
    v = rt.dist_versions(tmp_path)
    assert v == {"sentence_transformers": "5.6.0", "transformers": "5.12.1", "torch": "2.12.1"}
    assert rt.requirement_pin() == ">=3.0,<4"
    assert rt.pin_drift("5.6.0", ">=3.0,<4") is True and rt.pin_drift("3.4.1", ">=3.0,<4") is False
    snap = tmp_path / "models--BAAI--bge-reranker-v2-m3" / "refs"
    snap.mkdir(parents=True)
    (snap / "main").write_text(rt.RERANKER_SNAPSHOT + "\n")
    assert rt.snapshot_from_cache(tmp_path) == rt.RERANKER_SNAPSHOT


# ------------------------------------------------------------------ fuse


def test_rrf_ids_hand_checked() -> None:
    # a: x1 y2 z3 ; b: z1 x2 -> x: 1/61+1/62, z: 1/63+1/61, y: 1/62
    assert rt.rrf_ids(["x", "y", "z"], ["z", "x"]) == ["x", "z", "y"]


def test_fuse_cmd(data: dict[str, Any]) -> None:
    p = data["dir"] / "l.json"
    p.write_text(json.dumps({**rt.new_lists("x"), "lists": {
        "rr:hybrid@50:content+gloss": {"r": ["x", "y", "z"]}, "bm25@100": {"r": ["z", "x"]},
        "hybrid@50": {"r": ["y", "z", "x"]}}, "depth": {}}))
    assert rt.main(["fuse", "--lists", str(p), "--out", str(data["dir"] / "f.json")]) == 0
    out = json.loads((data["dir"] / "f.json").read_text())
    assert out["lists"]["fuse:A9-bm25"]["r"] == ["x", "z", "y"]
    assert out["lists"]["fuse:A9-fused"]["r"][0] in ("y", "x")


# ------------------------------------------------------------------ census


def test_census_lengths_and_neighbours(data: dict[str, Any]) -> None:
    cp = corpus(data["rows"])
    questions, _ = rt.load_questions(data["questions"], {"id": None, "query": None, "quotes": None, "cluster": None})
    gold = rt.build_gold(questions, cp)

    def count(a: list[str], b: list[str] | None) -> list[int]:
        return [len(x.split()) + (len(y.split()) + 3 if b is not None else 0)
                for x, y in zip(a, b if b is not None else a, strict=True)]

    lists = {"hybrid@50": {"q0": ["d0c0", "d0c1"]}, "live:hybrid@50": {"q0": ["d0c2", "d9c0"], "q1": ["d1c1"]}}
    shuffled, _ = rt.shuffle_glossaries(cp.chunks)
    res = rt.census(cp, questions, lists, gold, count, 18, ["hybrid@50"], ["content", "content+gloss"], shuffled)
    assert res["passage_tokens"]["content"]["n"] == 40
    # content is 17 words; +gloss adds 8 on even chunks -> over 18
    assert res["passage_tokens"]["content"]["over_max_length"] == 0
    assert res["passage_tokens"]["content+gloss"]["over_max_length"] == 20
    assert res["pair_tokens"]["hybrid@50|content"]["n"] == 2
    nh = res["neighbour_hits"]
    assert nh["live:hybrid@50"] == {"records_with_neighbour_in_top10": 1, "of_which_not_found10": 1}
    assert nh["hybrid@50"]["records_with_neighbour_in_top10"] == 1 and nh["hybrid@50"]["of_which_not_found10"] == 0


# ------------------------------------------------------------------ stats: hand-checkable tables


def hand_case(n_clusters: int = 10) -> tuple[dict[str, Any], dict[str, Any]]:
    """Per cluster c (one record r<c>): gold chunk g<c>.

    * bm25@100: gold at rank 1 for every record -> A0 found 10/10, ceiling@50(bm25) 10.
    * hybrid@50: gold at rank 30 for even c, absent for odd -> ceiling@50(hybrid) = 5.
    * live:hybrid@50 (A4-50): gold at rank 2 for c in {0, 2}, rank 15 for c in {4, 6, 8}, absent odd
      -> found@10 = 2, ordering loss 3, not in pool 5.
    * live:bm25@50 (A5-50): gold at rank 1 except c = 0 (rank 12) -> found 9.
    """
    recs = {f"r{c}": {"cluster": f"p{c}", "quotes": [[f"g{c}"]], "excluded": None} for c in range(n_clusters)}
    recs["rx"] = {"cluster": "p0", "quotes": [[]], "excluded": "no_gold_chunk_in_index"}
    fill = [f"f{i}" for i in range(200)]

    def at(rank: int | None, c: int, n: int) -> list[str]:
        lst = list(fill[:n])
        if rank is not None:
            lst[rank - 1] = f"g{c}"
        return lst

    L: dict[str, dict[str, list[str]]] = {"bm25@100": {}, "hybrid@50": {}, "hybrid@10": {}, "live:hybrid@50": {},
                                          "live:bm25@50": {}}
    for c in range(n_clusters):
        r = f"r{c}"
        L["bm25@100"][r] = at(1, c, 100)
        L["hybrid@50"][r] = at(30 if c % 2 == 0 else None, c, 50)
        L["hybrid@10"][r] = at(None, c, 10)
        L["live:hybrid@50"][r] = at(2 if c in (0, 2) else (15 if c % 2 == 0 else None), c, 20)
        L["live:bm25@50"][r] = at(12 if c == 0 else 1, c, 20)
    return L, {"records": recs, "excluded": {"no_gold_chunk_in_index": 1}}


AN = {"n_perm": 2000, "n_boot": 500, "ci_level": 0.90, "delta": 0.05, "alpha": 0.05}


def test_stats_decomposition_and_hypotheses_hand_checked() -> None:
    L, gold = hand_case()
    depth = {k: {"status": "ok"} for k in L}
    res = rt.analyse(L, depth, gold, depths=[10, 50, 100], an=AN, seed=1, identity={"status": "pass"})
    assert res["n_included"] == 10 and res["excluded"] == {"no_gold_chunk_in_index": 1}
    assert res["arms"]["A0"]["summary"]["found@10"] == 10
    assert res["arms"]["A4-50"]["route"] == "api" and res["arms"]["A4-50"]["summary"]["found@10"] == 2
    d = res["decomposition"]["A4-50"]
    assert (d["n"], d["ceiling"], d["not_in_pool"], d["ordering_loss"], d["found@10"]) == (10, 5, 5, 3, 2)
    assert d["identity"] is True
    assert res["ceilings"]["bm25@50"]["ceiling"] == 10 and res["ceilings"]["hybrid@50"]["ceiling"] == 5
    s = res["arms"]["A4-50"]["summary"]
    assert s["median_rank"] == 15.0 and s["iqr"] == [2.0, 15.0] and s["share_rank_11_50"] == pytest.approx(0.3)
    assert s["mrr50"] == pytest.approx((2 * 0.5 + 3 / 15) / 10)
    # H1: A0 - A4-50 : gained 8, lost 0, over 8 clusters -> exact p = 2^-8
    h1 = res["hypotheses"]["H1"]
    assert (h1["gained"], h1["lost"]) == (8, 0)
    ref = cstats.contrast([f"p{c}" for c in range(10)], [1] * 10, [1 if c in (0, 2) else 0 for c in range(10)],
                          n_flips=2000, n_boot=500, seed=1)
    assert h1["p"] == pytest.approx(ref["p"]) == pytest.approx(2 ** -8)
    assert h1["per_cluster"]["p1"] == {"gained": 1, "lost": 0, "n": 1}
    # H2: bm25 ceiling 10 vs hybrid 5 -> 5 non-zero clusters -> 2^-5 = .031 > .05/3: not attainable
    h2 = res["hypotheses"]["H2"]
    assert (h2["gained"], h2["lost"]) == (5, 0) and h2["status"] == rt.GNE
    assert "not attainable" in h2["gate"][0]
    # H3: A5-50 (9) vs A0 (10), NI at .05: tested, Holm over the two testable hypotheses
    h3 = res["hypotheses"]["H3"]
    assert (h3["gained"], h3["lost"]) == (0, 1) and h3["type"] == "noninferiority"
    assert res["family"]["tested"] == ["H1", "H3"]
    holm = cstats.holm([h1["p"], h3["p"]], 0.05)
    assert [h1["p_holm"], h3["p_holm"]] == pytest.approx(holm["p_adj"])
    assert h1["status"] == "confirmed"
    assert h3["status"] in ("confirmed", "not shown non-inferior")
    assert res["per_record"]["r0"]["A0"] == 1 and res["per_record"]["r1"]["A4-50"] is None
    assert all(e["bonferroni_m"] == len(res["exploratory"]) for e in res["exploratory"].values())


def test_stats_fewer_than_8_clusters_is_not_evaluable() -> None:
    L, gold = hand_case(6)
    res = rt.analyse(L, {k: {"status": "ok"} for k in L}, gold, depths=[10, 50], an=AN, seed=1)
    assert all(h["status"] == rt.GNE for h in res["hypotheses"].values())
    assert any("fewer than 8 clusters" in g for g in res["hypotheses"]["H1"]["gate"])
    assert res["family"]["tested"] == []


def test_stats_absent_arm_and_offline_route() -> None:
    L, gold = hand_case()
    del L["live:bm25@50"]
    L["rr:hybrid@50:content+gloss"] = L.pop("live:hybrid@50")
    res = rt.analyse(L, {k: {"status": "ok"} for k in L}, gold, depths=[10, 50, 100], an=AN, seed=1,
                     identity={"status": "fail"})
    assert res["hypotheses"]["H3"]["status"] == "absent"
    assert res["arms"]["A4-50"]["route"] == "offline"
    assert res["hypotheses"]["H1"]["status"] == rt.GNE
    assert "identity gate failed" in res["hypotheses"]["H1"]["gate"]


def test_attainable_floor() -> None:
    assert not rt.attainable(4, 0.05 / 3) and rt.attainable(6, 0.05 / 3) and rt.attainable(30, 1e-9)


def test_end_to_end_stats_cli_and_repeat(data: dict[str, Any]) -> None:
    write_pools(data)
    m = sidecar_server()
    try:
        assert rt.main(rerank_argv(data, m.url, "--jobs", "hybrid@50:content+gloss,bm25@50:content+gloss")) == 0
    finally:
        m.close()
    lists = [str(data["dir"] / "pools.json"), str(data["dir"] / "rr.json")]
    assert rt.main(["stats", "--lists", *lists, "--gold", str(data["dir"] / "gold.json"), "--set-label", "synthetic",
                    "--depths", "10,20,50", "--n-perm", "200", "--n-boot", "200", "--repeat", lists[0],
                    "--out", str(data["dir"] / "res.json")]) == 0
    res = json.loads((data["dir"] / "res.json").read_text())
    assert res["schema"] == rt.RESULTS_SCHEMA and res["provenance"]["citable"] is True
    assert res["identity_gate"]["status"] == "pass"
    assert res["arms"]["A5-50"]["route"] == "offline"
    assert res["repeat"]["bm25@50"] == {"n": 10, "found10_differs": 0, "top10_differs": 0}
    blob = (data["dir"] / "res.json").read_text() + (data["dir"] / "pools.json").read_text() + \
        (data["dir"] / "rr.json").read_text() + (data["dir"] / "gold.json").read_text()
    for q in data["qs"]:
        assert q["question"] not in blob and q["gold"][0] not in blob


# ------------------------------------------------------------------ power table


def test_power_table_shape() -> None:
    t = rt.power_table(n_pages=12, n_questions=40, n_sims=50, n_flips=50, discordances=(0.2,))
    assert t["schema"] == rt.POWER_SCHEMA and t["n_family"] == 3 and len(t["rows"]) == 2 * 6
    assert all(r["alpha_test"] == pytest.approx(0.05 / 3) for r in t["rows"])
    assert {r["hypothesis"] for r in t["rows"]} == {"H1/H2", "H3"}
