"""Hermetic tests for found10's BM25 exhaustion verifier (:mod:`bm25_exhaustion`): a fake
API and a fake index behind ``httpx.MockTransport``; no network, no store."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bm25_exhaustion as bx  # noqa: E402
import found10 as f10  # noqa: E402
import test_found10 as t  # noqa: E402

from ragstack.stores.elasticsearch import _build_query  # noqa: E402
from ragstack.stores.filters import Not  # noqa: E402

TENANT = "tenant-synthetic"


class CountingAPI(t.AuthFakeAPI):
    """The fake API plus an index that answers ``_count`` with ``count`` (an int, or a
    status code via ``count_status``) and records every count body."""

    def __init__(self, *a: Any, count: int | None = None, count_status: int = 200, **k: Any) -> None:
        super().__init__(*a, **k)
        self.count, self.count_status = count, count_status
        self.count_bodies: list[dict[str, Any]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "es.invalid" and req.url.path.endswith("/_count"):
            self.seen.append(dict(req.headers))
            self.trace.append((str(req.url.host), req.url.path, dict(req.headers)))
            self.count_bodies.append(json.loads(req.content))
            if self.count_status != 200:
                return httpx.Response(self.count_status, json={"error": "x"})
            return httpx.Response(200, json={"count": self.count})
        return super().__call__(req)


def conf(scope: bool = True, es: bool = True, **over: Any) -> dict[str, Any]:
    corpora = {"O": {"collection": "coll-o", "index": t.INDEX, "expected_chunks": 100}}
    if scope:
        corpora["O"]["bm25_scope"] = {"tenant": TENANT}
    return t.cfg(corpora=corpora, **({"census": {"es_url": t.ES}} if es else {}), **over)


def summary(tmp: Path) -> dict[str, Any]:
    return json.loads((tmp / "out/O/armx.summary.json").read_text())


def rows(tmp: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp / "out/O/armx.jsonl").read_text().splitlines()]


# ------------------------------------------------------------- the shared query builder
def test_count_query_is_the_es_stores_own_query() -> None:
    body = f10.request_body(f10.DEFAULT_CONDITIONS["bm25"], "a question", "coll")
    q, scope = bx.count_query("a question", body, 1.0, TENANT)
    assert scope == bx.SCOPE_EXACT
    assert q == _build_query("a question", {"tenant_id": [TENANT, "public"]}, 1.0)
    assert q["bool"]["must"][0]["multi_match"]["type"] == "most_fields"
    # boost <= 0: content alone, as the store does
    q0, _ = bx.count_query("a question", body, 0.0, TENANT)
    assert q0["bool"]["must"] == [{"match": {"content": "a question"}}]
    # exclude_boilerplate and request filters, tenant scope set last (a client cannot widen it)
    q2, _ = bx.count_query("a question", {**body, "exclude_boilerplate": True,
                                          "filters": {"year": 2020, "tenant_id": "other"}},
                           1.0, TENANT, ["owner-t"])
    assert q2 == _build_query("a question", {"year": 2020, "is_boilerplate": Not(True),
                                             "tenant_id": [TENANT, "public", "owner-t"]}, 1.0)
    # no tenant: the unscoped upper bound, the text clause alone
    qb, sb = bx.count_query("a question", body, 1.0, None)
    assert sb == bx.SCOPE_BOUND and "filter" not in qb["bool"]


def test_verdicts_and_eligibility() -> None:
    assert bx.verdict(10, 10, bx.SCOPE_EXACT) == bx.EXHAUSTED
    assert bx.verdict(10, 10, bx.SCOPE_BOUND) == bx.EXHAUSTED
    assert bx.verdict(10, 15, bx.SCOPE_EXACT) == bx.CLAMP
    assert bx.verdict(10, 15, bx.SCOPE_BOUND) == bx.UNDER
    assert bx.verdict(10, 9, bx.SCOPE_EXACT) == bx.MISMATCH
    assert bx.verdict(10, 9, bx.SCOPE_BOUND) == bx.MISMATCH
    assert bx.verdict(10, None, bx.SCOPE_EXACT) == bx.UNDER
    base = f10.request_body(f10.DEFAULT_CONDITIONS["bm25"], "q", "c")
    assert bx.ineligible(base) is None
    assert bx.ineligible(f10.request_body(f10.DEFAULT_CONDITIONS["norerank"], "q", "c"))
    assert bx.ineligible({**base, "use_graph": True})
    assert bx.ineligible({**base, "rewrite_strategies": ["hyde"]})
    assert bx.ineligible({**base, "collections": ["c", "d"]})
    assert bx.ineligible({**base, "retrieval_mode": "hybrid"})


# ------------------------------------------------------------- found10 runs
def test_exhausted_bm25_list_is_complete_and_not_gated(tmp_path: Path) -> None:
    api = CountingAPI(t.RANKS, depth=10, count=10)  # 20 requested, the query matches 10
    assert t.run(tmp_path, api, conf=conf()) == f10.EXIT_OK
    s = summary(tmp_path)
    bm = s["conditions"]["bm25"]
    assert bm["gate"] is None and bm["depth_short"] == 0 and bm["exhausted_verified"] == 6
    assert bm["exhaustion_verdicts"] == {bx.EXHAUSTED: 6} and bm["served_max"] == 10
    # hybrid lists cannot be verified this way: still gated, never counted
    assert f10.fa.GATE in s["conditions"]["base"]["gate"] and f10.fa.GATE in s["conditions"]["norerank"]["gate"]
    assert len(api.count_bodies) == 6
    rs = rows(tmp_path)
    for r in rs:
        if r["condition"] == "bm25":
            assert r["exhaustion"] == {"check": bx.CHECK, "requested": 20, "served": 10, "es_count": 10,
                                       "verdict": bx.EXHAUSTED, "scope": bx.SCOPE_EXACT,
                                       "clause": "content+symbol_glossary"}
        else:
            assert "exhaustion" not in r
    # each count is the server's query for that record's question, on the arm's index
    assert sorted(b["query"]["bool"]["must"][0]["multi_match"]["query"] for b in api.count_bodies) == \
        sorted(q["question"] for q in t.QUESTIONS)
    assert all(p == f"/{t.INDEX}/_count" for h, p, _ in api.trace if p.endswith("_count"))
    # never the question text in the outputs
    for f in (tmp_path / "out").rglob("*"):
        if f.is_file():
            txt = f.read_text()
            assert not any(f'"{q["question"]}"' in txt for q in t.QUESTIONS), f
    # the analysis treats the verified list as complete
    _, gated, _ = f10.collect(tmp_path / "out", [dict(q) for q in t.QUESTIONS])
    assert "armx|bm25" not in gated and "armx|base" in gated


def test_clamp_is_a_hard_stop(tmp_path: Path) -> None:
    api = CountingAPI(t.RANKS, depth=10, count=15)  # the index matches 15, 10 served
    assert t.run(tmp_path, api, conf=conf()) == f10.EXIT_REFUSED
    s = summary(tmp_path)
    assert s["status"] == "hard_stop" and "clamp" in s["reason"]
    assert any((r.get("exhaustion") or {}).get("verdict") == bx.CLAMP for r in rows(tmp_path))


def test_query_mismatch_is_a_hard_stop(tmp_path: Path) -> None:
    """The index matching FEWER chunks than the server served for the same query
    means the rebuilt query is not the server's: never evidence, a hard stop."""
    api = CountingAPI(t.RANKS, depth=10, count=7)  # 10 served, the index matches 7
    assert t.run(tmp_path, api, conf=conf()) == f10.EXIT_REFUSED
    s = summary(tmp_path)
    assert s["status"] == "hard_stop" and "does not match the server" in s["reason"]
    assert any((r.get("exhaustion") or {}).get("verdict") == bx.MISMATCH for r in rows(tmp_path))


def test_unscoped_bound_above_served_stays_under_served(tmp_path: Path) -> None:
    api = CountingAPI(t.RANKS, depth=10, count=15)
    assert t.run(tmp_path, api, conf=conf(scope=False)) == f10.EXIT_OK  # a bound proves no clamp
    bm = summary(tmp_path)["conditions"]["bm25"]
    assert f10.fa.GATE in bm["gate"] and bm["depth_short"] == 6 and bm["exhausted_verified"] == 0
    assert all("filter" not in b["query"]["bool"] for b in api.count_bodies)


def test_unscoped_bound_equal_to_served_verifies(tmp_path: Path) -> None:
    api = CountingAPI(t.RANKS, depth=10, count=10)
    assert t.run(tmp_path, api, conf=conf(scope=False)) == f10.EXIT_OK
    bm = summary(tmp_path)["conditions"]["bm25"]
    assert bm["gate"] is None and bm["exhaustion_verdicts"] == {bx.EXHAUSTED: 6}
    assert {r["exhaustion"]["scope"] for r in rows(tmp_path) if r["condition"] == "bm25"} == {bx.SCOPE_BOUND}


@pytest.mark.parametrize("case", ["es_error", "boost_unknown", "no_es_url"])
def test_unverifiable_stays_under_served(tmp_path: Path, case: str) -> None:
    api = CountingAPI(t.RANKS, depth=10, count=10, count_status=500 if case == "es_error" else 200)
    kw: dict[str, Any] = {}
    if case == "boost_unknown":  # too few probe candidates and nothing declared
        kw = {"probe_chunks": t.PROBE_CHUNKS[:2], "boost_declared": None}
    assert t.run(tmp_path, api, conf=conf(es=case != "no_es_url"), **kw) == f10.EXIT_OK
    bm = summary(tmp_path)["conditions"]["bm25"]
    assert f10.fa.GATE in bm["gate"] and bm["exhausted_verified"] == 0
    ex = [r.get("exhaustion") for r in rows(tmp_path) if r["condition"] == "bm25"]
    if case == "no_es_url":
        assert ex == [None] * 6 and api.count_bodies == []
    elif case == "es_error":
        assert all(e["verdict"] == bx.UNDER and e["error"] == "HTTP 500" and e["es_count"] is None for e in ex)
    else:
        assert all(e["verdict"] == bx.UNDER and "boost" in e["reason"] for e in ex) and api.count_bodies == []


def test_nothing_short_means_no_count(tmp_path: Path) -> None:
    api = CountingAPI(t.RANKS, count=10)  # depth 20 = requested
    assert t.run(tmp_path, api, conf=conf()) == f10.EXIT_OK
    assert api.count_bodies == []
    assert all("exhaustion" not in r for r in rows(tmp_path))
    s = summary(tmp_path)
    assert all(c["gate"] is None and c["exhausted_verified"] == 0 for c in s["conditions"].values())


@pytest.mark.parametrize("choice", ["authorization", "x-api-key"])
def test_credential_never_reaches_the_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str) -> None:
    monkeypatch.setenv("F10_TEST_TOKEN", t.TOKEN)
    api = CountingAPI(t.RANKS, depth=10, count=10)
    c = conf(api={"base_url": t.API, "retries": 1, "retry_backoff_s": 0, "api_key_env": "F10_TEST_TOKEN",
                  "auth_header": choice})
    assert t.run_default_client(tmp_path, api, c) == f10.EXIT_OK
    counts = [hd for h, p, hd in api.trace if h == "es.invalid" and p.endswith("/_count")]
    assert len(counts) == 6
    assert all(not (t.CREDENTIAL_HEADERS & {k.lower() for k in hd}) for _, _, hd in
               [x for x in api.trace if x[0] == "es.invalid"])
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert t.TOKEN not in f.read_text(errors="replace")


def test_a_count_client_with_a_credential_is_refused(tmp_path: Path) -> None:
    """Defence in depth: whoever builds the index client, the count refuses one that
    carries an API credential before any request is made."""
    import asyncio

    api = CountingAPI(t.RANKS, depth=10, count=10)
    r = f10.Runner(conf(), t.QUESTIONS, "O", "armx", tmp_path / "out", tmp_path / "swap.json", boost_declared=1.0)
    r.boost_check = {"status": "declared", "declared": 1.0}
    res = {"failed": False, "depth_short": True, "requested": 20, "served": 10}
    for header in f10.AUTH_HEADERS.values():
        client = httpx.AsyncClient(headers={header: t.TOKEN}, transport=httpx.MockTransport(api))
        with pytest.raises(bx.CredentialRefused):
            asyncio.run(r._verify_exhaustion(client, t.QUESTIONS[0], "bm25", dict(res)))
    assert api.trace == [] and api.count_bodies == []
    clean = httpx.AsyncClient(transport=httpx.MockTransport(api))
    out = dict(res)
    asyncio.run(r._verify_exhaustion(clean, t.QUESTIONS[0], "bm25", out))
    assert out["exhausted_verified"] is True and len(api.count_bodies) == 1


def test_bm25_scope_config_is_validated() -> None:
    with pytest.raises(f10.Refused, match="bm25_scope"):
        t.cfg(corpora={"O": {"collection": "c", "index": t.INDEX, "bm25_scope": {"tenant": ""}}})


def test_bm25_scope_must_see_the_indexs_tenant() -> None:
    """A scope that cannot read the tenant that stamped the index (``es_id_prefix``)
    undercounts, and an undercount equal to the served length would pass as
    exhausted: refused at config time. The stamping tenant may be the caller's or a
    shared collection's owner (an extra tenant)."""
    base = {"collection": "c", "index": t.INDEX}
    with pytest.raises(f10.Refused, match="es_id_prefix"):
        t.cfg(corpora={"O": {**base, "es_id_prefix": "owner-t:", "bm25_scope": {"tenant": TENANT}}})
    for scope in ({"tenant": "owner-t"}, {"tenant": TENANT, "extra_tenants": ["owner-t"]}):
        c = t.cfg(corpora={"O": {**base, "es_id_prefix": "owner-t:", "bm25_scope": scope}})
        assert c["corpora"]["O"]["bm25_scope"] == scope
    # without a prefix nothing can be cross-checked; the scope is taken as declared
    t.cfg(corpora={"O": {**base, "bm25_scope": {"tenant": TENANT}}})
