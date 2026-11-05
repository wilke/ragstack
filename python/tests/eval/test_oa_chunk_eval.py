"""OA chunking eval harness (scripts/eval/oa_chunk_eval, HARNESS-SPEC 2026-11-04).

The question checks and lowest-echo selection, coverage scoring with offsets and the
content-search fallback (incl. a quote split across two chunks), the paired bootstrap and
Holm, the decide rules on synthetic numbers, the ledger's resume and stop rules with a fake
writer, the retrieve request/response mapping with a fake API, the sampler, passages, the
gold audit, the noise margins, and score -> decide end to end on a tiny fixture.
No network: every HTTP call goes to an in-process fake."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from oa_chunk_eval import __main__ as cli  # noqa: E402
from oa_chunk_eval import (  # noqa: E402
    audit,
    decide,
    noise,
    passages,
    questions,
    retrieve,
    runner,
    sample,
    score,
    stats,
)
from oa_chunk_eval.common import (  # noqa: E402
    append_jsonl,
    corpus_rel_path,
    norm_ws_map,
    read_jsonl,
    sha256_text,
)
from oa_chunk_eval.config import ConfigError, load_config  # noqa: E402

CHECKS = {"question_min": 25, "question_max": 400, "quote_min": 10, "quote_max": 300, "max_copied_run": 3}

PASSAGE = ("Escherichia coli strain K-12 was grown in minimal medium at 37 degrees. "
           "The doubling time fell to 42 minutes when glucose was added at two grams per litre. "
           "Without glucose, growth stalled after six hours.")


# ------------------------------------------------------------------- fakes


class FakeResp:
    def __init__(self, status_code: int = 200, payload: Any = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise ValueError("bad json")
        return self._payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload)


VERSION = {"version": "1.6.22", "git_tag": "v1.6.22", "git_sha": "74ef9bb", "started_at": "2026-11-04T00:00:00Z",
           "python": "3.12.0", "impl": "python"}


class FakeHTTP:
    """Records every post; ``handler(url, json, headers) -> FakeResp``. ``get`` answers
    ``/v1/version`` with ``VERSION`` (or ``version_handler``)."""

    def __init__(self, handler, version_handler=None) -> None:
        self.handler = handler
        self.version_handler = version_handler
        self.calls: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get(self, url, headers=None, timeout=None):
        self.gets.append(url)
        if self.version_handler is not None:
            return self.version_handler(url)
        return FakeResp(200, dict(VERSION))

    async def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - httpx's name
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            res = self.handler(url, json, headers or {})
            if isinstance(res, Exception):
                raise res
            return res
        finally:
            self.in_flight -= 1


def chat(content: str) -> FakeResp:
    return FakeResp(200, {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 10}})


def good_reply(q: str = "How long did the doubling time of K-12 cells become once glucose was supplied?",
               a: str = "fell to 42 minutes") -> str:
    return json.dumps({"question": q, "answer_quote": a})


# ------------------------------------------------------------------- question checks


def test_quote_must_be_exact_substring():
    ok = questions.check_candidate({"question": "What doubling time did K-12 reach once glucose was supplied?",
                                    "answer_quote": "fell to 42 minutes"}, PASSAGE, CHECKS)
    assert ok["pass"], ok
    bad = questions.check_candidate({"question": "What doubling time did K-12 reach once glucose was supplied?",
                                     "answer_quote": "fell to forty-two minutes"}, PASSAGE, CHECKS)
    assert "quote_substring" in bad["failed"]
    case = questions.check_candidate({"question": "What doubling time did K-12 reach once glucose was supplied?",
                                      "answer_quote": "Fell to 42 minutes"}, PASSAGE, CHECKS)
    assert "quote_substring" in case["failed"]


def test_quote_surrounding_whitespace_is_stripped_before_the_substring_check():
    r = questions.check_candidate({"question": "What doubling time did K-12 reach once glucose was supplied?",
                                   "answer_quote": "  fell to 42 minutes \n"}, PASSAGE, CHECKS)
    assert r["pass"] and r["answer_quote"] == "fell to 42 minutes"


@pytest.mark.parametrize("q,ok", [("x" * 24, False), ("x" * 25, True), ("x" * 400, True), ("x" * 401, False)])
def test_question_length_bounds(q, ok):
    r = questions.check_candidate({"question": q, "answer_quote": "fell to 42 minutes"}, PASSAGE, CHECKS)
    assert ("question_len" not in r["failed"]) is ok


def test_quote_length_bounds():
    long_passage = "a" * 400
    for n, ok in ((9, False), (10, True), (300, True), (301, False)):
        r = questions.check_candidate({"question": "Which letter repeats throughout the sequence given?",
                                       "answer_quote": "a" * n}, long_passage, CHECKS)
        assert ("quote_len" not in r["failed"]) is ok, n


def test_copied_run_of_four_words_rejected_three_allowed():
    assert questions.copied_run("Why was strain K-12 grown in minimal medium?", PASSAGE)  # "grown in minimal medium"
    assert not questions.copied_run("Why was it grown in a minimal broth?", PASSAGE)  # "grown in" only
    # punctuation and case do not hide a copy
    assert questions.copied_run("WHEN GLUCOSE, WAS ADDED; what changed?", PASSAGE)
    r = questions.check_candidate({"question": "In which medium was it that growth stalled after six hours?",
                                   "answer_quote": "growth stalled after six hours"}, PASSAGE, CHECKS)
    assert "copied_run" in r["failed"]


@pytest.mark.parametrize("q", [
    "What did this study find about the doubling time of K-12?",
    "What did the authors measure for E. coli doubling time?",
    "Which medium does the passage say was used for the culture?",
    "According to the experiment, what was the doubling time?",
    "What does the paper report as the doubling time with sugar?",
    "In the text, which organism had its doubling time measured?",
])
def test_self_contained_regex_rejects(q):
    r = questions.check_candidate({"question": q, "answer_quote": "fell to 42 minutes"}, PASSAGE, CHECKS)
    assert "self_contained" in r["failed"]


def test_self_contained_regex_allows_unrelated_uses():
    for q in ("Which study design suits measuring bacterial doubling times best?",
              "What is the doubling time of K-12 when a sugar supplement is present?"):
        r = questions.check_candidate({"question": q, "answer_quote": "fell to 42 minutes"}, PASSAGE, CHECKS)
        assert "self_contained" not in r["failed"], q


def test_bad_json_fails_json_check():
    assert questions.check_candidate(None, PASSAGE, CHECKS)["failed"] == ["json"]
    assert questions.check_candidate({"question": 3, "answer_quote": "x"}, PASSAGE, CHECKS)["failed"] == ["json"]


def test_parse_reply_tolerates_fences_and_prose():
    assert questions.parse_reply('```json\n{"question": "q", "answer_quote": "a"}\n```')["question"] == "q"
    assert questions.parse_reply('Sure! {"question": "q", "answer_quote": "a"} hope it helps')["answer_quote"] == "a"
    assert questions.parse_reply("no json here") is None
    assert questions.parse_reply("") is None


def test_echo_and_lowest_echo_selection():
    assert questions.echo("glucose doubling time", PASSAGE) == 1.0
    assert questions.echo("zebra yak", PASSAGE) == 0.0
    cands = [
        {"i": 0, "pass": True, "echo": 0.5},
        {"i": 1, "pass": False, "echo": 0.1},  # lower echo but failing: never chosen
        {"i": 2, "pass": True, "echo": 0.3},
        {"i": 3, "pass": True, "echo": 0.3},  # tie: lower index wins
    ]
    assert questions.select(cands)["i"] == 2
    assert questions.select([{"i": 0, "pass": False, "echo": 0.0}]) is None


def test_request_body_always_sets_stream_false_and_max_tokens():
    w = {"model": "m", "max_tokens": 300, "temperature": 0.8, "top_p": 0.9,
         "extra_body": {"chat_template_kwargs": {"enable_thinking": False}, "stream": True, "max_tokens": 1}}
    b = questions.request_body(w, PASSAGE, {"system": "s", "user": "P: {passage}"})
    assert b["stream"] is False and b["max_tokens"] == 300
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert b["messages"][-1]["content"] == "P: " + PASSAGE
    assert b["temperature"] == 0.8 and b["top_p"] == 0.9


# ------------------------------------------------------------------- coverage scoring


RECORD = ("## Results\n\nAlpha beta gamma delta. The quick brown fox jumps over the lazy dog near the river bank. "
          "Epsilon zeta eta theta iota kappa.\n\n## Discussion\n\nLambda mu nu xi omicron pi.")
QUOTE = "quick brown fox jumps over the lazy dog"
QS = RECORD.index(QUOTE)
QE = QS + len(QUOTE)
Q = {"record_path": "PMC1", "answer_quote": QUOTE, "quote_start": QS, "quote_end": QE}


def _hit(content, start=None, end=None, path="PMC1", cid="c"):
    return {"source_path": path, "content": content, "start_char": start, "end_char": end, "chunk_id": cid}


def _score(hits, ks=(1, 5, 10), offsets="verify", rec=RECORD):
    return score.score_question(Q, hits, score.RecordText(rec), list(ks), 0.9, offsets, lambda h: len(h["content"]) // 4)


def test_coverage_union_and_threshold():
    assert score.coverage([(QS, QE)], (QS, QE)) == 1.0
    assert score.coverage([(QS, QS + 10), (QS + 5, QS + 20)], (QS, QS + 40)) == 0.5
    assert score.coverage([(0, QS)], (QS, QE)) == 0.0
    assert score.coverage([], (5, 5)) == 0.0


def test_hit_with_offsets():
    s, e = RECORD.index("The quick"), RECORD.index("Epsilon")
    r = _score([_hit(RECORD[s:e], s, e)])
    assert r["1"]["primary"] == 1 and r["1"]["strict"] == 1 and r["locate"] == {"offsets": 1}


def test_hit_with_content_search_fallback():
    s, e = RECORD.index("The quick"), RECORD.index("Epsilon")
    r = _score([_hit(RECORD[s:e])])  # no offsets
    assert r["1"]["primary"] == 1 and r["locate"] == {"exact": 1}
    # whitespace differs (chunker re-joined lines): normalised search still finds it
    r2 = _score([_hit("The  quick brown\nfox jumps over the lazy dog near the river bank.")])
    assert r2["1"]["primary"] == 1 and r2["locate"] == {"normalised": 1}


def test_wrong_offsets_are_verified_and_fall_back_to_search():
    s, e = RECORD.index("The quick"), RECORD.index("Epsilon")
    r = _score([_hit(RECORD[s:e], 0, 10)])  # offsets do not match the content
    assert r["1"]["primary"] == 1 and r["locate"] == {"exact": 1}
    trusted = _score([_hit(RECORD[s:e], 0, 10)], offsets="trust")
    assert trusted["1"]["primary"] == 0


def test_quote_split_across_two_chunks_counts_as_primary_hit_but_not_strict():
    cut = QS + len("quick brown fox ")
    a, b = RECORD[:cut], RECORD[cut:]
    for use_offsets in (True, False):
        h1 = _hit(a, 0, cut, cid="1") if use_offsets else _hit(a, cid="1")
        h2 = _hit(b, cut, len(RECORD), cid="2") if use_offsets else _hit(b, cid="2")
        r = _score([h1, _hit("unrelated", path="PMC9", cid="x"), h2])
        assert r["1"]["primary"] == 0  # one half only
        assert r["5"]["primary"] == 1 and r["5"]["coverage"] == 1.0
        assert r["5"]["strict"] == 0  # no single chunk holds the whole quote


def test_ninety_percent_threshold():
    keep = int(len(QUOTE) * 0.9) + 1
    r = _score([_hit(RECORD[:QS + keep])])
    assert r["1"]["primary"] == 1 and r["1"]["strict"] == 0
    r = _score([_hit(RECORD[:QS + int(len(QUOTE) * 0.8)])])
    assert r["1"]["primary"] == 0


def test_chunk_from_another_record_never_covers():
    s, e = RECORD.index("The quick"), RECORD.index("Epsilon")
    r = _score([_hit(RECORD[s:e], s, e, path="PMC1#table-1")])
    assert r["1"]["primary"] == 0 and r["1"]["strict"] == 0 and r["top1_article"] == "PMC1"


def test_context_tokens_sum_topk():
    hits = [_hit("x" * 40, cid=str(i)) for i in range(10)]
    r = _score(hits)
    assert r["1"]["ctx_tokens"] == 10 and r["5"]["ctx_tokens"] == 50 and r["10"]["ctx_tokens"] == 100


def test_duplicate_content_picks_occurrence_overlapping_quote():
    rec = "the quick brown fox. " * 3
    q = {"record_path": "PMC1", "answer_quote": "the quick brown fox", "quote_start": 21, "quote_end": 40}
    r = score.score_question(q, [_hit("the quick brown fox.")], score.RecordText(rec), [1], 0.9, "verify", lambda h: 1)
    assert r["1"]["primary"] == 1


def test_norm_ws_map_maps_back():
    s, idx = norm_ws_map("  a \n\n b  c ")
    assert s == "a b c" and [idx[i] for i in range(len(s))] == [2, 3, 7, 8, 10]


def test_tokenizer_fallback_is_flagged():
    count, name, est = score.make_tokenizer(None)
    assert est and name == "chars/4" and count("abcdefgh") == 2


# ------------------------------------------------------------------- stats


def test_paired_bootstrap_is_deterministic_with_seed():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 2, 500)
    b = rng.integers(0, 2, 500)
    r1 = stats.paired_diff(a, b, iters=2000, seed=7)
    r2 = stats.paired_diff(a, b, iters=2000, seed=7)
    r3 = stats.paired_diff(a, b, iters=2000, seed=8)
    assert (r1["lo"], r1["hi"]) == (r2["lo"], r2["hi"])
    assert np.array_equal(r1["_dist"], r2["_dist"])
    assert (r1["lo"], r1["hi"]) != (r3["lo"], r3["hi"])
    assert r1["point"] == pytest.approx((a.mean() - b.mean()) * 100)
    assert r1["lo"] < r1["point"] < r1["hi"]


def test_paired_bootstrap_identical_arms_degenerate_and_paired():
    a = [1, 0, 1, 1, 0] * 40
    r = stats.paired_diff(a, a, iters=500, seed=0)
    assert r["point"] == r["lo"] == r["hi"] == 0.0


def test_bootstrap_fast_path_matches_index_resampling_in_distribution():
    v = np.array([0.0] * 300 + [1.0] * 700)
    fast = stats.boot_means(v, 4000, 3)
    rng = np.random.default_rng(3)
    slow = v[rng.integers(0, len(v), size=(4000, len(v)))].mean(axis=1)
    assert fast.mean() == pytest.approx(slow.mean(), abs=0.003)
    assert fast.std() == pytest.approx(slow.std(), rel=0.1)


def test_p_greater():
    dist = np.array([-1.0, 0.0, 1.0, 2.0, 3.0])
    assert stats.p_greater(dist, 0.5) == pytest.approx(3 / 6)
    assert stats.p_greater(dist, 10) == pytest.approx(6 / 6)
    assert stats.p_greater(dist, -10) == pytest.approx(1 / 6)


def test_holm():
    out = stats.holm_bonferroni({"a": 0.01, "b": 0.04, "c": 0.03}, alpha=0.05)
    assert out["a"] == (pytest.approx(0.03), True)
    assert out["c"][0] == pytest.approx(0.06) and not out["c"][1]
    assert out["b"][0] == pytest.approx(0.06) and not out["b"][1]  # monotone; step-down stopped


# ------------------------------------------------------------------- decide rules


MARGINS = {"M_ni": 2.0, "M_mid": 2.0, "M_high": 4.0}
DCFG = {"alpha_one_sided": 0.025, "stratum_point": -2.0, "context_ratio": 1.5}


def _dist(center: float, sd: float = 0.5, n: int = 10000, seed: int = 0) -> dict[str, Any]:
    d = np.random.default_rng(seed).normal(center, sd, n)
    return {"n": 2000, "point": center, "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5)),
            "_dist": d}


def _arms(*specs):
    out = [{"name": "base", "cost_class": "cheap", "baseline": True}]
    for name, cls, *rest in specs:
        out.append({"name": name, "cost_class": cls, **(rest[0] if rest else {})})
    return out


def test_cheap_non_inferiority():
    arms = _arms(("c_ok", "cheap"), ("c_bad", "cheap"))
    diffs = {"c_ok": _dist(-0.5), "c_bad": _dist(-1.8)}  # lo -1.48 >= -2 ; lo -2.78 < -2
    r = decide.evaluate(arms, "base", diffs, {}, {"base": 100, "c_ok": 100, "c_bad": 100}, MARGINS, DCFG)
    assert r["arms"]["c_ok"]["pass"] and r["arms"]["c_ok"]["test"] == "non_inferiority"
    assert not r["arms"]["c_bad"]["pass"] and r["arms"]["c_bad"]["fail_reasons"] == ["class_test"]
    assert r["winner"] == "c_ok"


def test_mid_and_expensive_superiority_and_workflow():
    arms = _arms(("m", "mid"), ("e", "expensive", {"workflow_feasible": False}),
                 ("e2", "expensive", {"workflow_feasible": True}))
    diffs = {"m": _dist(3.5), "e": _dist(6.0), "e2": _dist(4.5)}  # m lo~2.5>=2; e2 lo~3.5<4
    r = decide.evaluate(arms, "base", diffs, {}, {"base": 100, "m": 100, "e": 100, "e2": 100}, MARGINS, DCFG)
    assert r["arms"]["m"]["pass"]
    assert r["arms"]["e"]["fail_reasons"] == ["workflow_feasibility"]
    assert r["arms"]["e2"]["fail_reasons"] == ["class_test"]
    assert r["winner"] == "m"


def test_nothing_passes_keeps_baseline():
    arms = _arms(("m", "mid"))
    r = decide.evaluate(arms, "base", {"m": _dist(1.0)}, {}, {"base": 1, "m": 1}, MARGINS, DCFG)
    assert r["kept_baseline"] and r["winner"] == "base"


def test_context_token_rule_bumps_to_next_class():
    arms = _arms(("c", "cheap"), ("m", "mid"))
    diffs = {"c": _dist(0.0), "m": _dist(3.5)}
    ctx = {"base": 1000, "c": 1600, "m": 1600}  # both > 1.5x
    r = decide.evaluate(arms, "base", diffs, {}, ctx, MARGINS, DCFG)
    assert r["arms"]["c"]["test_class"] == "mid" and r["arms"]["c"]["threshold"] == 2.0
    assert not r["arms"]["c"]["pass"]  # non-inferior but must now be superior by M_mid
    assert r["arms"]["m"]["test_class"] == "expensive" and r["arms"]["m"]["threshold"] == 4.0
    assert not r["arms"]["m"]["pass"]
    ctx_ok = {"base": 1000, "c": 1500, "m": 1500}  # exactly 1.5x is not "> 1.5x"
    r2 = decide.evaluate(arms, "base", diffs, {}, ctx_ok, MARGINS, DCFG)
    assert r2["arms"]["c"]["context_rule"] is None and r2["arms"]["c"]["pass"]


def test_stratum_guard_needs_both_point_and_upper_bound():
    strata = {"c": {"journal_family": {
        "fam_a": {"point": -2.5, "lo": -6.0, "hi": -0.2, "n": 200},  # fails: point < -2 and hi < 0
        "fam_b": {"point": -3.0, "lo": -8.0, "hi": 1.0, "n": 50},    # hi >= 0: ok
        "fam_c": {"point": -1.5, "lo": -3.0, "hi": -0.1, "n": 900},  # point >= -2: ok
    }}}
    r = decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1}, MARGINS, DCFG)
    assert r["arms"]["c"]["fail_reasons"] == ["stratum_guard"]
    assert [s["value"] for s in r["arms"]["c"]["stratum_failures"]] == ["fam_a"]


def test_holm_across_challengers_can_fail_arms_whose_ci_clears():
    # Both CI lower bounds clear -2.0; raw p ~ 0.02 each. Holm: smallest p x 2 ~ 0.04 > 0.025 -> both fail.
    arms = _arms(("c1", "cheap"), ("c2", "cheap"))
    d1 = _dist(-2.0 + 2.05 * 0.5, sd=0.5, seed=1)
    d2 = _dist(-2.0 + 2.05 * 0.5, sd=0.5, seed=2)
    r = decide.evaluate(arms, "base", {"c1": d1, "c2": d2}, {}, {"base": 1, "c1": 1, "c2": 1}, MARGINS, DCFG)
    for name in ("c1", "c2"):
        a = r["arms"][name]
        assert a["ci_ok"] and a["p_one_sided"] <= 0.025, a
        assert not a["holm_ok"] and not a["pass"] and a["fail_reasons"] == ["class_test"]
    # with one challenger the same arm passes: the correction is across challengers
    r1 = decide.evaluate(_arms(("c1", "cheap")), "base", {"c1": d1}, {}, {"base": 1, "c1": 1}, MARGINS, DCFG)
    assert r1["arms"]["c1"]["pass"]


def test_tie_break_cheaper_ingest_then_fewer_context_tokens():
    arms = _arms(("m", "mid"), ("c_big", "cheap"), ("c_small", "cheap"))
    diffs = {"m": _dist(5.0), "c_big": _dist(0.0), "c_small": _dist(0.0)}
    ctx = {"base": 1000, "m": 1000, "c_big": 1200, "c_small": 900}
    r = decide.evaluate(arms, "base", diffs, {}, ctx, MARGINS, DCFG)
    assert r["passing_order"] == ["c_small", "c_big", "m"] and r["winner"] == "c_small"
    arms2 = _arms(("c_big", "cheap", {"ingest_cost": 1.0}), ("c_small", "cheap", {"ingest_cost": 2.0}))
    r2 = decide.evaluate(arms2, "base", diffs, {}, ctx, MARGINS, DCFG)
    assert r2["winner"] == "c_big"


def test_decide_refuses_without_margins():
    with pytest.raises(ConfigError):
        decide.margins_from({"margins": None})


# ------------------------------------------------------------------- config fixture


PINNED_EXTRA = {"rerank": False, "use_graph": False, "retrieval_mode": "hybrid"}


def make_cfg(tmp_path: Path, **over) -> dict[str, Any]:
    raw = {
        "out_dir": str(tmp_path / "out"), "manifest": str(tmp_path / "manifest.jsonl"),
        "include_pmcids": str(tmp_path / "include.txt"), "corpus_root": str(tmp_path / "corpus"),
        "extracted": [str(tmp_path / "extracted.jsonl")],
        "sample": {"n_eval_articles": 2, "n_background": 1, "seed": 7},
        "writer": {"url": "http://writer.invalid", "model": "w", "n": 3, "max_tokens": 128,
                   "empty_retry_s": 0, "max_in_flight": 4},
        "api": {"base_url": "http://api.invalid", "api_key_file": str(tmp_path / "api.key"), "top_k": 10,
                "endpoint": "/v1/retrieve", "max_in_flight": 4,
                "request_extra": dict(PINNED_EXTRA)},
        "arms": [{"name": "base", "collection_id": "col-base", "cost_class": "cheap", "baseline": True},
                 {"name": "alt", "collection_id": "col-alt", "cost_class": "cheap", "baseline": False}],
        "stop": {"max_failures": 20, "max_consecutive": 5, "pause_file": "PAUSE"},
        "score": {"bootstrap_iters": 500},
        "margins": MARGINS,
        "noise": {"n_questions": 10, "planned_n": 100, "split_reps": 50},
    }
    for k, v in over.items():
        raw[k] = v
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw))
    (tmp_path / "api.key").write_text("SECRET-KEY-123\n")
    return load_config(p)


# ------------------------------------------------------------------- ledger / stop rules


def write_passages(cfg, n: int) -> list[dict[str, Any]]:
    rows = [{"passage_id": f"PMC{i}#p0", "pmcid": f"PMC{i}", "record_path": f"PMC{i}", "start": 0,
             "end": len(PASSAGE), "sha256": sha256_text(PASSAGE), "text": PASSAGE, "section_type": "results",
             "journal_family": "fa", "length_band": "short", "year_band": "2020+"} for i in range(n)]
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    with (out / "passages.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return rows


def test_questions_end_to_end_with_fake_writer(tmp_path):
    cfg = make_cfg(tmp_path)
    write_passages(cfg, 2)
    replies = {0: good_reply(), 1: good_reply(q="What did the authors find about growth?"),
               2: good_reply(q="What doubling time results once a sugar supplement is given to K-12?")}
    counter = {"n": 0}

    def handler(url, body, headers):
        assert url == "http://writer.invalid/v1/chat/completions"
        assert body["stream"] is False and body["max_tokens"] == 128
        i = counter["n"] % 3
        counter["n"] += 1
        return chat(replies[i])

    http = FakeHTTP(handler)
    code, _ = asyncio.run(questions.run_async(cfg, http=http))
    assert code == 0 and len(http.calls) == 6
    qs = read_jsonl(Path(cfg["out_dir"]) / "questions.jsonl")
    assert len(qs) == 2
    for q in qs:
        assert q["answer_quote"] == "fell to 42 minutes"
        assert PASSAGE[q["quote_start"]:q["quote_end"]] == q["answer_quote"]
        assert q["n_passing"] >= 1


def test_ledger_resume_skips_done_calls(tmp_path):
    cfg = make_cfg(tmp_path)
    write_passages(cfg, 3)
    http = FakeHTTP(lambda u, b, h: chat(good_reply()))
    (Path(cfg["out_dir"]) / "PAUSE").write_text("")
    code, ctl = asyncio.run(questions.run_async(cfg, http=http))
    assert code == 75 and ctl.reason == "pause_file" and len(http.calls) == 0
    (Path(cfg["out_dir"]) / "PAUSE").unlink()

    # first run: stop after 4 calls via a pause file created by the 4th reply
    def pausing(u, b, h):
        if len(http2.calls) == 4:
            (Path(cfg["out_dir"]) / "PAUSE").write_text("")
        return chat(good_reply())

    http2 = FakeHTTP(pausing)
    cfg["writer"]["max_in_flight"] = 1
    code, ctl = asyncio.run(questions.run_async(cfg, http=http2))
    assert code == 75 and len(http2.calls) == 4  # in-flight call finished, no new ones
    ledger = read_jsonl(Path(cfg["out_dir"]) / questions.LEDGER)
    assert len(ledger) == 4 and all(r["status"] == "ok" for r in ledger)
    summ = json.loads((Path(cfg["out_dir"]) / "questions.summary.json").read_text())
    assert summ["main"]["selected"] == 1 and summ["main"]["pending"] == 2

    (Path(cfg["out_dir"]) / "PAUSE").unlink()
    http3 = FakeHTTP(lambda u, b, h: chat(good_reply()))
    code, _ = asyncio.run(questions.run_async(cfg, http=http3))
    assert code == 0 and len(http3.calls) == 9 - 4
    done = {(r["passage_id"], r["i"]) for r in read_jsonl(Path(cfg["out_dir"]) / questions.LEDGER)}
    assert len(done) == 9
    assert len(read_jsonl(Path(cfg["out_dir"]) / "questions.jsonl")) == 3


def test_stop_after_consecutive_failures(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["writer"]["max_in_flight"] = 1
    write_passages(cfg, 4)
    http = FakeHTTP(lambda u, b, h: FakeResp(500, {}))
    code, ctl = asyncio.run(questions.run_async(cfg, http=http))
    assert code == 75 and ctl.reason == "failures_5_in_a_row" and len(http.calls) == 5


def test_stop_after_total_failures_including_previous_runs(tmp_path):
    cfg = make_cfg(tmp_path, stop={"max_failures": 3, "max_consecutive": 100, "pause_file": "PAUSE"})
    cfg["writer"]["max_in_flight"] = 1
    write_passages(cfg, 4)
    n = {"i": 0}

    def alternating(u, b, h):
        n["i"] += 1
        return FakeResp(500, {}) if n["i"] % 2 else chat(good_reply())

    http = FakeHTTP(alternating)
    code, ctl = asyncio.run(questions.run_async(cfg, http=http))
    assert code == 75 and ctl.reason == "failures_over_3" and ctl.fail_total == 4
    # a rerun counts the ledger's failures and refuses to start
    http2 = FakeHTTP(lambda u, b, h: chat(good_reply()))
    code, ctl = asyncio.run(questions.run_async(cfg, http=http2))
    assert code == 75 and len(http2.calls) == 0
    # an operator reset lets it continue
    runner.reset_failures(Path(cfg["out_dir"]) / questions.LEDGER)
    code, _ = asyncio.run(questions.run_async(cfg, http=http2))
    assert code == 0


def test_empty_reply_retried_once(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["writer"]["n"] = 1
    write_passages(cfg, 2)
    seen: dict[str, int] = {}

    def handler(u, b, h):
        k = json.dumps(b["messages"])
        seen[k] = seen.get(k, 0) + 1
        return chat("") if len(seen) and sum(seen.values()) in (1, 3, 4) else chat(good_reply())

    http = FakeHTTP(handler)
    cfg["writer"]["max_in_flight"] = 1
    code, _ = asyncio.run(questions.run_async(cfg, http=http))
    ledger = read_jsonl(Path(cfg["out_dir"]) / questions.LEDGER)
    assert [r["status"] for r in ledger] == ["ok", "empty"]
    assert [r["attempts"] for r in ledger] == [2, 2]
    assert len(http.calls) == 4


def test_in_flight_capped_at_four(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["writer"]["max_in_flight"] = 16
    write_passages(cfg, 5)

    class SlowHTTP(FakeHTTP):
        async def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            self.calls.append(1)
            return chat(good_reply())

    http = SlowHTTP(None)
    asyncio.run(questions.run_async(cfg, http=http))
    assert http.max_in_flight == 4 and len(http.calls) == 15


def test_skip_log_has_strata(tmp_path):
    cfg = make_cfg(tmp_path)
    write_passages(cfg, 1)
    http = FakeHTTP(lambda u, b, h: chat(good_reply(a="not in the passage at all")))
    asyncio.run(questions.run_async(cfg, http=http))
    skips = read_jsonl(Path(cfg["out_dir"]) / "skips.jsonl")
    assert len(skips) == 1 and skips[0]["journal_family"] == "fa" and skips[0]["section_type"] == "results"
    assert read_jsonl(Path(cfg["out_dir"]) / "questions.jsonl") == []


def test_writer_key_read_from_file_not_logged(tmp_path):
    cfg = make_cfg(tmp_path)
    (tmp_path / "w.key").write_text("WRITER-KEY\n")
    cfg["writer"]["api_key_file"] = str(tmp_path / "w.key")
    cfg["writer"]["n"] = 1
    write_passages(cfg, 1)
    http = FakeHTTP(lambda u, b, h: chat(good_reply()))
    asyncio.run(questions.run_async(cfg, http=http))
    assert http.calls[0]["headers"]["Authorization"] == "Bearer WRITER-KEY"
    for f in Path(cfg["out_dir"]).iterdir():
        assert "WRITER-KEY" not in f.read_text()


# ------------------------------------------------------------------- retrieve


def test_retrieve_request_and_response_mapping(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["api"]["request_extra"] = {**PINNED_EXTRA, "query": "nope", "collection": "nope"}
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True)
    with (out / "questions.jsonl").open("w") as fh:
        for i in range(3):
            fh.write(json.dumps({"question_id": f"q{i}", "question": f"question {i}"}) + "\n")

    def handler(url, body, headers):
        assert url == "http://api.invalid/v1/retrieve"
        assert headers == {"X-API-Key": "SECRET-KEY-123"}
        assert set(body) == {"query", "top_k", "collection", "retrieval_mode", "rerank", "use_graph"}
        assert body["top_k"] == 10 and body["collection"] in ("col-base", "col-alt")
        src = [{"doc_id": "d1", "chunk_id": "c1", "content": "text one", "score": 0.9,
                "metadata": {"source_path": "PMC1", "start_char": 5, "end_char": 13}},
               {"doc_id": "d2", "chunk_id": "c2", "content": "text two", "score": 0.5,
                "metadata": {"filename": "PMC2#table-1"}}]
        payload: dict[str, Any] = {"sources": src}
        if body["query"] == "question 2" and body["collection"] == "col-alt":
            payload["degraded"] = [{"component": "rerank", "reason": "Timeout"}]
        return FakeResp(200, payload, {"X-Request-Id": "rid"})

    http = FakeHTTP(handler)
    code, ctl = asyncio.run(retrieve.run_async(cfg, http=http))
    assert code == 0 and len(http.calls) == 6
    base = read_jsonl(out / "retrieval" / "base.jsonl")
    assert [r["status"] for r in base] == ["ok"] * 3
    h = base[0]["hits"]
    assert h[0] == {"rank": 1, "doc_id": "d1", "chunk_id": "c1", "source_path": "PMC1", "content": "text one",
                    "score": 0.9, "start_char": 5, "end_char": 13}
    assert h[1]["source_path"] == "PMC2#table-1" and h[1]["start_char"] is None
    assert base[0]["server_version"]["version"] == "1.6.22" and http.gets == ["http://api.invalid/v1/version"]
    assert base[0]["request"] == {"top_k": 10, "collection": "col-base", **PINNED_EXTRA}
    alt = read_jsonl(out / "retrieval" / "alt.jsonl")
    assert sorted(r["status"] for r in alt) == ["degraded", "ok", "ok"]
    for f in out.rglob("*"):
        if f.is_file():
            assert "SECRET-KEY-123" not in f.read_text()
    # resume: only the degraded pair is retried
    http2 = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}))
    code, _ = asyncio.run(retrieve.run_async(cfg, http=http2))
    assert code == 0 and len(http2.calls) == 1 and http2.calls[0]["json"]["collection"] == "col-alt"


def test_retrieve_stops_on_consecutive_failures(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["api"]["max_in_flight"] = 1
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True)
    with (out / "questions.jsonl").open("w") as fh:
        for i in range(10):
            fh.write(json.dumps({"question_id": f"q{i}", "question": f"question {i}"}) + "\n")
    http = FakeHTTP(lambda u, b, h: ConnectionError("down"))
    code, ctl = asyncio.run(retrieve.run_async(cfg, http=http))
    assert code == 75 and ctl.reason == "failures_5_in_a_row" and len(http.calls) == 5
    assert all(r["status"] == "error:ConnectionError" for r in read_jsonl(out / "retrieval" / "base.jsonl"))


# ------------------------------------------------------------------- sampler


def _manifest(n: int) -> list[dict[str, Any]]:
    journals = ["Journal of Example Bacteriology", "Example Virology Letters", "Unrelated Physics"]
    return [{"pmcid": f"PMC{i}", "journal_xml": journals[i % 3], "body_chars": 5000 + (i * 997) % 50000,
             "year": 2010 + i % 14} for i in range(n)]


SCFG = {"seed": 11, "n_eval_articles": 30, "n_background": 50, "strata": {
    "journal_family_map": {"bact": ["bacteriology"], "viro": ["virology"]},
    "length_bands": [{"name": "short", "max": 20000}, {"name": "long", "min": 20000}],
    "year_bands": [{"name": "old", "max": 2016}, {"name": "new", "min": 2016}]}}


def test_sampler_deterministic_disjoint_and_filtered():
    rows = _manifest(300)
    include = {f"PMC{i}" for i in range(300) if i % 5}
    s1, sum1 = sample.draw(rows, include, SCFG)
    s2, _ = sample.draw(list(reversed(rows)), set(include), SCFG)
    assert s1 == s2  # input order does not matter
    ev = {r["pmcid"] for r in s1 if r["role"] == "eval"}
    bg = {r["pmcid"] for r in s1 if r["role"] == "background"}
    assert len(ev) == 30 and len(bg) == 50 and not ev & bg
    assert (ev | bg) <= include
    s3, _ = sample.draw(rows, include, {**SCFG, "seed": 12})
    assert {r["pmcid"] for r in s3 if r["role"] == "eval"} != ev
    assert sum(c["eval"] for c in sum1["cells"].values()) == 30
    fams = {r["journal_family"] for r in s1}
    assert fams == {"bact", "viro", "other"}


def test_allocation_proportional_and_capped():
    alloc = sample.allocate({("a", "s"): 50, ("b", "s"): 30, ("c", "s"): 20}, 10)
    assert alloc == {("a", "s"): 5, ("b", "s"): 3, ("c", "s"): 2}
    alloc = sample.allocate({("a", "s"): 1, ("b", "s"): 99}, 50)
    assert sum(alloc.values()) == 50 and alloc[("a", "s")] <= 1
    with pytest.raises(ValueError):
        sample.allocate({("a", "s"): 1}, 2)


def test_journal_family_and_bands():
    m = {"asm": [r"\bjournal of bacteriology\b"]}
    assert sample.journal_family("J. Bacteriol.", m) == "other"
    assert sample.journal_family("Journal of  Bacteriology", m) == "asm"
    assert sample.journal_family(None, m) == "unknown"
    assert sample.band(None, [{"name": "x"}]) == "unknown"
    assert sample.band(5, [{"name": "lo", "max": 5}, {"name": "hi", "min": 5}]) == "hi"


def test_sample_run_writes_shard_paths(tmp_path):
    cfg = make_cfg(tmp_path, sample={**SCFG, "n_eval_articles": 5, "n_background": 3})
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in _manifest(40)) + "\n")
    (tmp_path / "include.txt").write_text("\n".join(f"PMC{i}" for i in range(40)) + "\n")
    assert sample.run(cfg) == 0
    shard = read_jsonl(Path(cfg["out_dir"]) / "shard.jsonl")
    assert len(shard) == 8
    import hashlib
    h = hashlib.sha1(b"PMC1").hexdigest()
    assert corpus_rel_path("PMC1") == f"clean/{h[:2]}/{h[2:4]}/PMC1.xml"
    assert all(s["xml_path"] == corpus_rel_path(s["pmcid"]) for s in shard)


# ------------------------------------------------------------------- passages


def _article_text() -> str:
    sent = "Bacterial strain number {i} showed a measurable change in growth under the tested condition."
    results = " ".join(sent.format(i=i) for i in range(30))
    return ("Short abstract sentence one. Short abstract sentence two.\n\n## Introduction\n\nIntro text here.\n\n"
            f"## Results\n\n### Growth\n\n{results}\n\n## References\n\nNone.")


def test_regions_and_sentence_aligned_cut():
    text = _article_text()
    regs = passages.regions(text, ["results", "introduction", "abstract"])
    types = [r["type"] for r in regs]
    assert types == ["abstract", "introduction", "results", "results"]  # ### Growth inherits Results
    rec = {"text": text, "path": "PMC5", "metadata": {"content_type": "article"}}
    ps, why = passages.pick_passages(rec, {"chars": 300, "per_article": 1, "section_targets": ["results"],
                                           "min_frac": 0.75, "max_frac": 1.25}, seed=1)
    assert why is None and len(ps) == 1
    p = ps[0]
    assert text[p["start"]:p["end"]] == p["text"] and p["sha256"] == sha256_text(p["text"])
    assert 225 <= len(p["text"]) <= 375
    assert p["text"].startswith("Bacterial") and p["text"].endswith(".")
    assert "#" not in p["text"] and p["section_type"] == "results"
    again, _ = passages.pick_passages(rec, {"chars": 300, "per_article": 1, "section_targets": ["results"],
                                            "min_frac": 0.75, "max_frac": 1.25}, seed=1)
    assert again == ps


def test_passages_skip_when_no_target_region():
    rec = {"text": "## Methods\n\nOnly methods.", "path": "PMC6", "metadata": {}}
    ps, why = passages.pick_passages(rec, {"chars": 1200, "per_article": 1, "section_targets": ["results"],
                                           "min_frac": 0.75, "max_frac": 1.25}, seed=1)
    assert ps == [] and why == "no_target_region"


# ------------------------------------------------------------------- audit


def test_audit_finds_quote_in_other_records_only():
    qs = [{"question_id": "q1", "record_path": "PMC1", "pmcid": "PMC1", "answer_quote": "the lazy  dog sleeps"},
          {"question_id": "q2", "record_path": "PMC2", "pmcid": "PMC2", "answer_quote": "unique words only here"},
          {"question_id": "q3", "record_path": "PMC3", "pmcid": "PMC3", "answer_quote": "ab cd"}]
    recs = [{"path": "PMC1", "text": "x the lazy dog sleeps y"},
            {"path": "PMC9", "text": "and the lazy\ndog sleeps too"},
            {"path": "PMC1#table-1", "text": "the lazy dog sleeps"},
            {"path": "PMC2", "text": "unique words only here"},
            {"path": "PMC3", "text": "ab cd"}, {"path": "PMC4", "text": "zz ab cd zz"}]
    hits = audit.find_other_occurrences(qs, iter(recs))
    assert sorted(hits["q1"]) == ["PMC1#table-1", "PMC9"]
    assert "q2" not in hits
    assert hits["q3"] == ["PMC4"]


# ------------------------------------------------------------------- noise


def test_noise_margins_follow_addendum_a():
    rng = np.random.default_rng(0)
    b = rng.integers(0, 2, 300)
    a = b.copy()
    flip = rng.choice(300, 30, replace=False)
    a[flip] = 1 - a[flip]
    ncfg = {"planned_n": 2000, "floor": 2.0, "split_reps": 200, "seed": 0, "target_half_width": 1.5}
    r = noise.compute(list(b), list(a), ncfg, iters=2000, seed=0)
    assert r["discordance"] == pytest.approx(0.1)
    hw = r["half_width_at_planned_n"]
    assert hw == pytest.approx(r["half_width_subset"] * (300 / 2000) ** 0.5, rel=1e-3)
    assert r["margins"]["M_ni"] == pytest.approx(max(2.0, hw), abs=1e-3)
    assert r["margins"]["M_high"] == pytest.approx(2 * r["margins"]["M_mid"], abs=1e-3)
    assert r["split_half_baseline"]["reps"] == 200 and r["split_half_baseline"]["sd"] > 0
    assert r["n_for_target"] > 0
    big = noise.compute([0, 1] * 5, [1, 0] * 5, {**ncfg, "planned_n": 10}, iters=500, seed=0)
    assert big["margins"]["M_ni"] > 2.0  # half-width above the floor wins


# ------------------------------------------------------------------- score -> decide end to end


def build_fixture(tmp_path: Path, n: int = 40) -> dict[str, Any]:
    cfg = make_cfg(tmp_path)
    out = Path(cfg["out_dir"])
    (out / "retrieval").mkdir(parents=True)
    text_tpl = "## Results\n\nPrefix sentence about strain {i}. The marker gene {i} was strongly induced by heat. Tail."
    recs, qs, samp, base_rows, alt_rows = [], [], [], [], []
    for i in range(n):
        pid = f"PMC{i}"
        text = text_tpl.format(i=i)
        quote = f"marker gene {i} was strongly induced"
        s = text.index(quote)
        recs.append({"text": text, "path": pid, "metadata": {"content_type": "article"}})
        qs.append({"question_id": f"{pid}#p0", "passage_id": f"{pid}#p0", "pmcid": pid, "record_path": pid,
                   "question": f"q{i}", "answer_quote": quote, "quote_start": s, "quote_end": s + len(quote),
                   "journal_family": "fa" if i % 2 else "fb", "length_band": "short", "year_band": "new",
                   "section_type": "results"})
        samp.append({"pmcid": pid, "role": "eval", "journal_family": "fa" if i % 2 else "fb"})
        good = {"source_path": pid, "content": text[s - 5:s + len(quote) + 5], "start_char": s - 5,
                "end_char": s + len(quote) + 5, "chunk_id": f"g{i}", "score": 1.0}
        wrong = {"source_path": f"PMC{(i + 2) % n}", "content": "x" * 400, "start_char": None, "end_char": None,
                 "chunk_id": f"w{i}", "score": 0.5}
        base_rows.append({"question_id": f"{pid}#p0", "status": "ok", "hits": [good, wrong]})
        alt_hits = [good, wrong] if i % 4 else [wrong, wrong]  # alt misses a quarter
        alt_rows.append({"question_id": f"{pid}#p0", "status": "ok", "hits": alt_hits})
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    for fname, rows in (("questions.jsonl", qs), ("sample.jsonl", samp), ("passages.jsonl", qs)):
        (out / fname).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (out / "skips.jsonl").write_text("")
    for r in base_rows:
        append_jsonl(out / "retrieval" / "base.jsonl", r)
    for r in alt_rows:
        append_jsonl(out / "retrieval" / "alt.jsonl", r)
    append_jsonl(out / "audit.jsonl", {"question_id": "PMC0#p0", "ambiguous_gold": True})
    return cfg


def write_noise(cfg, margins=None) -> None:
    (Path(cfg["out_dir"]) / "noise.json").write_text(json.dumps({"margins": margins or MARGINS}))


def test_score_and_decide_end_to_end(tmp_path):
    cfg = build_fixture(tmp_path)
    assert score.run(cfg) == 0
    out = Path(cfg["out_dir"])
    s = json.loads((out / "score.json").read_text())
    assert s["primary_set"] == "clean" and s["sets"]["clean"]["n"] == 39 and s["sets"]["ambiguous"]["n"] == 1
    k5 = s["sets"]["clean"]["primary"]["5"]["pooled"]
    assert k5["hit"]["base"]["point"] == 100.0
    assert k5["diff"]["alt"]["point"] == pytest.approx(-100 * 9 / 39, abs=1e-3)
    assert s["tokens_estimated"] is True
    assert set(s["sets"]["clean"]["primary"]["5"]["strata"]["journal_family"]) == {"fa", "fb"}
    assert "base|alt" in s["sets"]["clean"]["discordance"]["5"]
    jc = s["journal_confusion"]["alt"]
    assert jc["wrong_top1"] == 9 and jc["share_same_family"] == 1.0  # (i+2)%n keeps parity
    write_noise(cfg)
    assert decide.run(cfg) == 0
    dec = json.loads((out / "decision.json").read_text())
    assert dec["winner"] == "base" and dec["kept_baseline"]
    assert dec["forced_unchecked"] == [] and dec["margins_match_noise"] is True
    assert dec["arms"]["alt"]["fail_reasons"][0] == "class_test"
    md = (out / "decision.md").read_text()
    assert "Decision: `base`" in md
    for txt in (md, json.dumps(dec)):  # aggregates only: no question text, quotes or collection ids
        assert "marker gene" not in txt and "col-alt" not in txt and "q1" not in txt.split("Decision")[0]


def test_noise_run_on_fixture(tmp_path):
    cfg = build_fixture(tmp_path)
    assert noise.run(cfg) == 0
    res = json.loads((Path(cfg["out_dir"]) / "noise.json").read_text())
    assert res["arms"] == ["base", "alt"] and res["n_subset"] == 10
    assert res["margins"]["M_high"] == pytest.approx(2 * res["margins"]["M_mid"])


def test_cli_reports_config_errors(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"out_dir": str(tmp_path / "o"), "arms": []}))
    assert cli.main(["retrieve", "--config", str(p)]) == 2
    assert cli.main(["decide", "--config", str(p)]) == 2


def test_example_config_loads_and_has_only_placeholders():
    p = _EVAL / "examples" / "oa-chunk-eval.config.example.json"
    cfg = load_config(p)
    assert cfg["api"]["endpoint"] == "/v1/retrieve" and len(cfg["arms"]) == 6
    raw = p.read_text()
    assert ".invalid" in raw and "/home/" not in raw and "localhost" not in raw


# =================================================================== review regressions
# One or more tests per finding of the 2026-11-04 harness review (IDs in the test names'
# docstrings).


# ---- 1a: repeated quote inside the gold record

REPEAT_REC = ("## Methods\n\nCells were lysed in PBS buffer and spun down at low speed for ten minutes. "
              "Unrelated filler sentence about plates and incubators goes here. "
              "Pellets were then washed in PBS buffer twice before imaging.")
REPEAT_QUOTE = "in PBS buffer"


def test_audit_flags_within_record_repeat():
    """1a: a quote occurring twice in its own gold record is flagged within_record_repeat."""
    qs = [{"question_id": "q1", "record_path": "PMC1", "pmcid": "PMC1", "answer_quote": REPEAT_QUOTE},
          {"question_id": "q2", "record_path": "PMC2", "pmcid": "PMC2", "answer_quote": "unique words only here"}]
    recs = [{"path": "PMC1", "text": REPEAT_REC}, {"path": "PMC2", "text": "x unique words only here y"}]
    hits, gold_counts = audit.find_occurrences(qs, iter(recs))
    assert gold_counts == {"q1": 2, "q2": 1} and not hits


def test_audit_run_writes_within_record_repeat_and_score_treats_it_as_ambiguous(tmp_path):
    """1a: audit.run writes the flag; score puts the item in the ambiguous set, not clean."""
    cfg = build_fixture(tmp_path, n=4)
    out = Path(cfg["out_dir"])
    recs = read_jsonl(tmp_path / "extracted.jsonl")
    recs[1]["text"] += " The marker gene 1 was strongly induced again."  # repeat q1's quote
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    (out / "audit.jsonl").unlink()
    assert audit.run(cfg) == 0
    rows = {r["question_id"]: r for r in read_jsonl(out / "audit.jsonl")}
    assert rows["PMC1#p0"]["within_record_repeat"] and rows["PMC1#p0"]["n_in_gold_record"] == 2
    assert not rows["PMC1#p0"]["ambiguous_gold"] and not rows["PMC2#p0"]["within_record_repeat"]
    assert score.audit_flags(cfg) == {"PMC1#p0"}
    score.run(cfg)
    s = json.loads((out / "score.json").read_text())
    assert s["sets"]["ambiguous"]["n"] == 1 and s["sets"]["clean"]["n"] == 3


def test_primary_coverage_is_max_over_all_quote_occurrences():
    """1a: a chunk holding only the FIRST occurrence covers a quote recorded at the second."""
    second = REPEAT_REC.rindex(REPEAT_QUOTE)
    q = {"record_path": "PMC1", "answer_quote": REPEAT_QUOTE, "quote_start": second,
         "quote_end": second + len(REPEAT_QUOTE)}
    first_sent_end = REPEAT_REC.index("minutes.") + len("minutes.")
    chunk = REPEAT_REC[:first_sent_end]
    assert REPEAT_QUOTE in chunk and second >= first_sent_end
    r = score.score_question(q, [_hit(chunk, 0, first_sent_end)], score.RecordText(REPEAT_REC), [1], 0.9,
                             "verify", lambda h: 1)
    assert r["1"]["strict"] == 1 and r["1"]["primary"] == 1  # consistent with strict
    assert r["quote_occurrences"] == 2


# ---- 1b: offsets_unverified gives no coverage

def test_offsets_failing_verification_give_no_coverage():
    """1b: offsets that fail verification and content not found -> no coverage, counted."""
    s, e = RECORD.index("The quick"), RECORD.index("Epsilon")
    drifted = "The quick brown fox leaps over the lazy dog near the river bank."  # re-extract differs
    r = _score([_hit(drifted, s, e)])
    assert r["1"]["primary"] == 0 and r["1"]["coverage"] == 0.0
    assert r["locate"] == {"offsets_unverified": 1}
    assert score.locate_chunk(score.RecordText(RECORD), drifted, s, e, (QS, QE)) == (None, "offsets_unverified")


# ---- 1c: questions whose gold record text is missing

def test_questions_without_record_text_are_dropped_and_score_refuses(tmp_path):
    """1c: no gold text -> question dropped from the paired set; score exits 2; decide refuses."""
    cfg = build_fixture(tmp_path, n=8)
    recs = [r for r in read_jsonl(tmp_path / "extracted.jsonl") if r["path"] != "PMC3"]
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    rows, meta = score.build_rows(cfg)
    assert meta["records_without_text"] == 1 and meta["questions_without_record_text"] == 1
    assert "PMC3#p0" not in {r["question_id"] for r in rows} and len(rows) == 7
    assert any("records_without_text" in x for x in meta["refusals"])
    assert score.run(cfg) == 2
    write_noise(cfg)
    assert decide.run(cfg) == 2
    assert not (Path(cfg["out_dir"]) / "decision.json").exists()


# ---- 1d: empty source_path / unlocated share

def test_hit_quality_reports_empty_source_path_and_unlocated_per_arm(tmp_path):
    """1d: per-arm shares are reported and above max_unlocated_share score refuses."""
    cfg = build_fixture(tmp_path, n=8)
    out = Path(cfg["out_dir"])
    rows = read_jsonl(out / "retrieval" / "alt.jsonl")
    for r in rows:
        for h in r["hits"]:
            h["source_path"] = ""  # an ingest path that did not stamp source_path
    (out / "retrieval" / "alt.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rows_, meta = score.build_rows(cfg)
    q = meta["hit_quality"]
    assert q["alt"]["empty_source_path_share"] == 1.0 and q["base"]["empty_source_path_share"] == 0.0
    assert any(x.startswith("alt: empty_source_path_share") for x in meta["refusals"])
    assert score.run(cfg) == 2
    s = json.loads((out / "score.json").read_text())
    assert s["hit_quality"]["alt"]["empty_source_path"] == 16 and s["refusals"]


def test_unlocated_share_above_threshold_refuses(tmp_path):
    """1d: gold-record hits that cannot be located above the threshold make score refuse;
    a raised threshold lets it through."""
    cfg = build_fixture(tmp_path, n=8)
    out = Path(cfg["out_dir"])
    rows = read_jsonl(out / "retrieval" / "base.jsonl")
    rows[0]["hits"][0].update({"content": "text that is nowhere in the record", "start_char": None,
                               "end_char": None})
    (out / "retrieval" / "base.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _, meta = score.build_rows(cfg)
    assert meta["hit_quality"]["base"]["unlocated"] == 1
    assert meta["hit_quality"]["base"]["unlocated_share"] == pytest.approx(1 / 8)
    assert any("unlocated_share" in x for x in meta["refusals"])
    cfg["score"]["max_unlocated_share"] = 0.2
    _, meta = score.build_rows(cfg)
    assert meta["refusals"] == []


def test_noise_refuses_on_scoring_refusals(tmp_path):
    """1d: margins must not be measured on broken scoring."""
    cfg = build_fixture(tmp_path)
    recs = [r for r in read_jsonl(tmp_path / "extracted.jsonl") if r["path"] != "PMC3"]
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    cfg["noise"]["n_questions"] = 40
    assert noise.run(cfg) == 2
    nj = json.loads((Path(cfg["out_dir"]) / "noise.json").read_text())
    assert nj["refusals"]
    # decide will not take margins from a refused noise run either
    recs.append({"text": "## Results\n\nPrefix sentence about strain 3. The marker gene 3 was strongly induced "
                         "by heat. Tail.", "path": "PMC3", "metadata": {"content_type": "article"}})
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert score.run(cfg) == 0
    cfg["margins"] = nj["margins"]
    assert decide.run(cfg) == 2


# ---- 3a: copied-run tokenisation

def test_words_keep_hyphenated_names_and_unicode():
    """3a: whitespace split, edge punctuation stripped, internal - and . kept, casefold."""
    assert questions.words("How does the SARS-CoV-2 spike bind ACE2?") == \
        ["how", "does", "the", "sars-cov-2", "spike", "bind", "ace2"]
    assert questions.words("E. coli K-12, (β-Lactamase).") == ["e", "coli", "k-12", "β-lactamase"]
    assert questions.words("STRASSE Straße") == ["strasse", "strasse"]  # Unicode casefold
    assert questions.words("pH 7.4; 1.5-fold") == ["ph", "7.4", "1.5-fold"]
    assert questions.words("glucose/lactose and/or (IL-6)") == ["glucose", "lactose", "and", "or", "il-6"]


def test_copied_run_does_not_split_hyphenated_names():
    """3a: 'SARS-CoV-2 spike' is two words, so it is not a 4-word copy."""
    passage = "The SARS-CoV-2 spike protein binds ACE2 with high affinity in these assays."
    assert not questions.copied_run("How does the SARS-CoV-2 spike bind ACE2?", passage)
    r = questions.check_candidate({"question": "How does the SARS-CoV-2 spike bind its receptor?",
                                   "answer_quote": "binds ACE2 with high affinity"}, passage, CHECKS)
    assert "copied_run" not in r["failed"]
    assert questions.copied_run("Why does the SARS-CoV-2 spike protein binds matter?", passage)  # 4 real words
    assert not questions.copied_run("Is E. coli K-12 used?", "We used E. coli K-12 cells.")


# ---- 3b: ledger tied to passage sha256 and model

def test_ledger_rows_ignored_when_passage_text_or_model_changes(tmp_path):
    """3b: ok rows from a different passage text or writer model are not reused."""
    cfg = make_cfg(tmp_path)
    cfg["writer"]["n"] = 1
    write_passages(cfg, 1)
    http = FakeHTTP(lambda u, b, h: chat(good_reply()))
    assert asyncio.run(questions.run_async(cfg, http=http))[0] == 0 and len(http.calls) == 1
    row = read_jsonl(Path(cfg["out_dir"]) / questions.LEDGER)[0]
    assert row["passage_sha256"] == sha256_text(PASSAGE) and row["model"] == "w"
    # re-cut passage: different text, same passage_id -> the old row is ignored, the call is redone
    new_text = "Bacillus subtilis spores germinated within 30 minutes after nutrient addition at warm temperatures."
    out = Path(cfg["out_dir"])
    p = read_jsonl(out / "passages.jsonl")[0]
    p.update({"text": new_text, "sha256": sha256_text(new_text), "end": len(new_text)})
    (out / "passages.jsonl").write_text(json.dumps(p) + "\n")
    reply = json.dumps({"question": "How quickly do B. subtilis spores begin to germinate once fed?",
                        "answer_quote": "within 30 minutes"})
    http2 = FakeHTTP(lambda u, b, h: chat(reply))
    assert asyncio.run(questions.run_async(cfg, http=http2))[0] == 0 and len(http2.calls) == 1
    q = read_jsonl(out / "questions.jsonl")[0]
    assert q["answer_quote"] == "within 30 minutes" and new_text[q["quote_start"]:q["quote_end"]] == "within 30 minutes"
    # model change under the same writer name -> redone as well
    cfg["writer"]["model"] = "w2"
    http3 = FakeHTTP(lambda u, b, h: chat(reply))
    asyncio.run(questions.run_async(cfg, http=http3))
    assert len(http3.calls) == 1 and read_jsonl(out / "questions.jsonl")[0]["model"] == "w2"


# ---- 2a: minimum stratum n

def test_stratum_guard_skips_strata_below_min_n():
    """2a: an n=1 stratum with point -100 does not veto; it is listed as not tested."""
    strata = {"c": {"journal_family": {
        "unknown": {"point": -100.0, "lo": -100.0, "hi": -100.0, "n": 1},
        "big": {"point": -2.5, "lo": -6.0, "hi": -0.2, "n": 200},
    }}}
    dcfg = {**DCFG, "min_stratum_n": 30}
    r = decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1}, MARGINS, dcfg)
    a = r["arms"]["c"]
    assert [s["value"] for s in a["stratum_failures"]] == ["big"]
    assert a["strata_not_tested"] == [{"dim": "journal_family", "value": "unknown", "n": 1}]
    strata["c"]["journal_family"].pop("big")
    r = decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1}, MARGINS, dcfg)
    assert r["arms"]["c"]["pass"]


def test_min_stratum_n_default_and_listed_in_decision(tmp_path):
    """2a: default 30 from config; decision.md lists the untested strata."""
    cfg = build_fixture(tmp_path)  # strata fa/fb have 19-20 clean questions each
    assert cfg["decide"]["min_stratum_n"] == 30
    score.run(cfg)
    write_noise(cfg)
    assert decide.run(cfg) == 0
    out = Path(cfg["out_dir"])
    dec = json.loads((out / "decision.json").read_text())
    assert dec["min_stratum_n"] == 30
    assert {s["value"] for s in dec["arms"]["alt"]["strata_not_tested"]} >= {"fa", "fb"}
    assert "not tested by the stratum guard" in (out / "decision.md").read_text()


# ---- 2b: margins vs noise.json, audit required

def test_decide_refuses_margin_mismatch_unless_forced(tmp_path):
    """2b: margins differing from noise.json refuse; --force-unchecked records the override."""
    cfg = build_fixture(tmp_path)
    score.run(cfg)
    out = Path(cfg["out_dir"])
    write_noise(cfg, {"M_ni": 3.0, "M_mid": 3.0, "M_high": 6.0})
    assert decide.run(cfg) == 2 and not (out / "decision.json").exists()
    assert cli.main(["decide", "--config", cfg["_config_path"], "--force-unchecked"]) == 0
    dec = json.loads((out / "decision.json").read_text())
    assert dec["margins_match_noise"] is False
    assert any("differ from noise.json" in x for x in dec["forced_unchecked"])
    assert "FORCED UNCHECKED" in (out / "decision.md").read_text()


def test_decide_refuses_without_noise_json_or_audit(tmp_path):
    """2b: a missing noise.json or a missing audit refuses."""
    cfg = build_fixture(tmp_path)
    score.run(cfg)
    assert decide.run(cfg) == 2  # no noise.json
    write_noise(cfg)
    (Path(cfg["out_dir"]) / "audit.jsonl").unlink()
    score.run(cfg)
    assert json.loads((Path(cfg["out_dir"]) / "score.json").read_text())["audit_present"] is False
    assert decide.run(cfg) == 2
    assert decide.run(cfg, force_unchecked=True) == 0
    dec = json.loads((Path(cfg["out_dir"]) / "decision.json").read_text())
    assert any("audit.jsonl was missing" in x for x in dec["forced_unchecked"])


# ---- 3c: bounded attempts, pending and passage-stage skips

def test_exhausted_call_is_final_and_selection_uses_completed_calls(tmp_path):
    """3c: after max_attempts a failing call is final; the passage is selected from the rest."""
    cfg = make_cfg(tmp_path, questions={"max_attempts": 2}, stop={"max_failures": 100, "max_consecutive": 100,
                                                                  "pause_file": "PAUSE"})
    cfg["writer"]["max_in_flight"] = 1
    write_passages(cfg, 1)

    def handler(u, b, h):
        n = len(http.calls)
        return FakeResp(400, {}) if n >= 3 else chat(good_reply())  # call i=2 (and its retries) fail

    http = FakeHTTP(handler)
    asyncio.run(questions.run_async(cfg, http=http))
    out = Path(cfg["out_dir"])
    summ = json.loads((out / "questions.summary.json").read_text())["main"]
    assert summ["pending"] == 1 and len(read_jsonl(out / "pending.jsonl")) == 1  # one attempt left
    assert read_jsonl(out / "pending.jsonl")[0]["journal_family"] == "fa"
    asyncio.run(questions.run_async(cfg, http=http))  # second attempt fails too -> exhausted
    summ = json.loads((out / "questions.summary.json").read_text())["main"]
    assert summ["pending"] == 0 and summ["exhausted_calls"] == 1 and summ["selected"] == 1
    assert read_jsonl(out / "pending.jsonl") == []
    n_calls = len(http.calls)
    asyncio.run(questions.run_async(cfg, http=http))
    assert len(http.calls) == n_calls  # no third attempt


def test_skip_loss_counts_pending_and_passage_stage_by_stratum(tmp_path):
    """3c: pending passages and passage-stage skips appear in skip_loss and decision.md."""
    cfg = build_fixture(tmp_path)
    out = Path(cfg["out_dir"])
    (out / "skips.jsonl").write_text(json.dumps({"passage_id": "PMC0#p0"}) + "\n")
    (out / "pending.jsonl").write_text(json.dumps({"passage_id": "PMC1#p0"}) + "\n")
    (out / "passages.skipped.jsonl").write_text(
        json.dumps({"pmcid": "PMC99", "reason": "no_fitting_passage", "journal_family": "fb",
                    "length_band": "short", "year_band": "new"}) + "\n")
    loss = score.skip_loss(cfg, ["journal_family", "length_band", "section_type"])
    fa, fb = loss["journal_family"]["fa"], loss["journal_family"]["fb"]
    assert fb["skipped"] == 1 and fb["passage_stage"] == 1 and fb["loss"] == pytest.approx(2 / 21, abs=1e-4)
    assert fa["pending"] == 1 and fa["loss"] == pytest.approx(1 / 20, abs=1e-4)
    assert loss["length_band"]["short"]["passage_stage"] == 1
    assert loss["section_type"]["None"]["passage_stage"] == 1  # unknown before a passage exists
    score.run(cfg)
    write_noise(cfg)
    assert decide.run(cfg) == 0
    md = (out / "decision.md").read_text()
    assert "| journal_family | fb | 20 | 1 | 0 | 1 |" in md and "pending" in md


# ---- 3d: ledger tail repair

def test_append_after_truncated_tail_keeps_ledger_readable(tmp_path):
    """3d: a partial last line (crash mid-append) is truncated before the next append."""
    p = tmp_path / "l.jsonl"
    append_jsonl(p, {"a": 1})
    with p.open("a") as fh:
        fh.write('{"a": 2, "hits": [{"con')  # killed mid-write
    append_jsonl(p, {"a": 3})
    assert read_jsonl(p) == [{"a": 1}, {"a": 3}]
    # a complete row that only lost its newline is kept
    with p.open("a") as fh:
        fh.write('{"a": 4}')
    append_jsonl(p, {"a": 5})
    assert read_jsonl(p) == [{"a": 1}, {"a": 3}, {"a": 4}, {"a": 5}]


# ---- robustness: 503 + Retry-After, interleave, version, pinned settings, check-writer set

def _write_questions(cfg, n: int, fname: str = "questions.jsonl", suffix: str = "") -> None:
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    with (out / fname).open("a") as fh:
        for i in range(n):
            fh.write(json.dumps({"question_id": f"q{i}{suffix}", "question": f"question {i}{suffix}"}) + "\n")


def test_retrieve_503_with_retry_after_waits_and_retries(tmp_path, monkeypatch):
    """503 + Retry-After is a bounded wait-and-retry, not a failure."""
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(retrieve, "_sleep", fake_sleep)
    cfg = make_cfg(tmp_path)
    cfg["api"]["max_in_flight"] = 1
    _write_questions(cfg, 1)
    state = {"n": 0}

    def handler(u, b, h):
        state["n"] += 1
        if state["n"] <= 3:
            return FakeResp(503, {"detail": "dormant"}, {"Retry-After": "500" if state["n"] == 1 else "5"})
        return FakeResp(200, {"sources": []})

    http = FakeHTTP(handler)
    code, ctl = asyncio.run(retrieve.run_async(cfg, http=http, only_arms=["base"]))
    assert code == 0 and ctl.fail_total == 0
    row = read_jsonl(Path(cfg["out_dir"]) / "retrieval" / "base.jsonl")[0]
    assert row["status"] == "ok" and row["waits_503"] == 3
    assert slept == [120.0, 5.0, 5.0]  # capped at max_retry_after_s
    # bounded: after max_503_waits it is recorded as a failure
    cfg["api"]["max_503_waits"] = 2
    _write_questions(cfg, 1, suffix="b")
    http2 = FakeHTTP(lambda u, b, h: FakeResp(503, {}, {"Retry-After": "1"}))
    asyncio.run(retrieve.run_async(cfg, http=http2, only_arms=["base"]))
    assert len(http2.calls) == 3
    assert read_jsonl(Path(cfg["out_dir"]) / "retrieval" / "base.jsonl")[-1]["status"] == "http_503"
    # 503 without Retry-After stays an immediate failure
    http3 = FakeHTTP(lambda u, b, h: FakeResp(503, {}))
    asyncio.run(retrieve.run_async(cfg, http=http3, only_arms=["base"]))
    assert len(http3.calls) == 1


def test_retrieve_interleaves_arms_per_question(tmp_path):
    """Every arm is called for a question before the next question starts."""
    cfg = make_cfg(tmp_path)
    cfg["api"]["max_in_flight"] = 1
    _write_questions(cfg, 3)
    http = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}))
    asyncio.run(retrieve.run_async(cfg, http=http))
    order = [(c["json"]["query"], c["json"]["collection"]) for c in http.calls]
    assert order == [("question 0", "col-base"), ("question 0", "col-alt"), ("question 1", "col-base"),
                     ("question 1", "col-alt"), ("question 2", "col-base"), ("question 2", "col-alt")]
    for arm in ("base", "alt"):
        assert len(read_jsonl(Path(cfg["out_dir"]) / "retrieval" / f"{arm}.jsonl")) == 3


def test_retrieve_records_server_version_once_and_request_settings(tmp_path):
    """GET /v1/version once per run; version (or its error) and settings in every row."""
    cfg = make_cfg(tmp_path)
    _write_questions(cfg, 2)
    http = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}))
    asyncio.run(retrieve.run_async(cfg, http=http))
    assert http.gets == ["http://api.invalid/v1/version"]
    for arm in ("base", "alt"):
        for r in read_jsonl(Path(cfg["out_dir"]) / "retrieval" / f"{arm}.jsonl"):
            assert r["server_version"] == {k: VERSION[k] for k in retrieve._VERSION_FIELDS}
            assert r["request"]["rerank"] is False and r["request"]["use_graph"] is False
            assert r["request"]["retrieval_mode"] == "hybrid" and "query" not in r["request"]
    (tmp_path / "b").mkdir()
    cfg2 = make_cfg(tmp_path / "b")
    _write_questions(cfg2, 1)
    http2 = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}), version_handler=lambda u: FakeResp(401, {}))
    asyncio.run(retrieve.run_async(cfg2, http=http2, only_arms=["base"]))
    assert read_jsonl(Path(cfg2["out_dir"]) / "retrieval" / "base.jsonl")[0]["server_version"] == {"error": "http_401"}


@pytest.mark.parametrize("drop", list(retrieve.PINNED))
def test_retrieve_refuses_without_pinned_request_settings(tmp_path, drop):
    """rerank, use_graph and retrieval_mode must be explicit in api.request_extra."""
    cfg = make_cfg(tmp_path)
    cfg["api"]["request_extra"].pop(drop)
    _write_questions(cfg, 1)
    http = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}))
    with pytest.raises(ConfigError, match=drop):
        asyncio.run(retrieve.run_async(cfg, http=http))
    assert http.calls == []
    cfg_path = Path(cfg["_config_path"])
    raw = json.loads(cfg_path.read_text())
    raw["api"]["request_extra"].pop(drop)
    cfg_path.write_text(json.dumps(raw))
    assert cli.main(["retrieve", "--config", str(cfg_path)]) == 2


def test_pinned_names_are_retrieve_request_fields_in_openapi():
    """The pinned names exist as RetrieveRequest properties in contracts/openapi.yaml."""
    import yaml
    spec = yaml.safe_load((Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml").read_text())
    props = spec["components"]["schemas"]["RetrieveRequest"]["properties"]
    assert set(retrieve.PINNED) <= set(props)
    assert "/v1/version" in spec["paths"]
    assert set(retrieve._VERSION_FIELDS) <= set(spec["components"]["schemas"]["VersionResponse"]["properties"])


def test_check_writer_set_is_retrieved_and_scored_as_separate_report(tmp_path):
    """The check-writer questions are retrieved and reported in score.json['check_writer'],
    never in the main sets."""
    cfg = build_fixture(tmp_path, n=8)
    out = Path(cfg["out_dir"])
    main_q = read_jsonl(out / "questions.jsonl")
    check = [{**q, "question_id": q["question_id"] + ":check", "question": q["question"] + " (check)"}
             for q in main_q[:4]]
    (out / "questions.check.jsonl").write_text("\n".join(json.dumps(r) for r in check) + "\n")
    assert [q["question_id"] for q in retrieve.run_questions(cfg, None)][-4:] == [q["question_id"] for q in check]
    assert len(retrieve.run_questions(cfg, "noise")) <= len(main_q)  # noise subset: main questions only
    by_q = {r["question_id"]: r for r in read_jsonl(out / "retrieval" / "base.jsonl")}
    for arm in ("base", "alt"):
        for q in check:
            append_jsonl(out / "retrieval" / f"{arm}.jsonl",
                         {**by_q[q["question_id"][:-len(":check")]], "question_id": q["question_id"]})
    assert score.run(cfg) == 0
    s = json.loads((out / "score.json").read_text())
    assert s["n_questions"] == 8 and s["sets"]["all"]["n"] == 8
    cw = s["check_writer"]
    assert cw["n_questions"] == 4 and cw["sets"]["all"]["n"] == 4
    assert cw["sets"]["all"]["primary"]["5"]["pooled"]["hit"]["base"]["point"] == 100.0
    write_noise(cfg)
    assert decide.run(cfg) == 0
    assert "Check-writer set (secondary" in (out / "decision.md").read_text()


def test_retrieve_also_calls_check_writer_questions(tmp_path):
    """retrieve's job list includes questions.check.jsonl."""
    cfg = make_cfg(tmp_path)
    _write_questions(cfg, 1)
    _write_questions(cfg, 1, fname="questions.check.jsonl", suffix=":check")
    http = FakeHTTP(lambda u, b, h: FakeResp(200, {"sources": []}))
    asyncio.run(retrieve.run_async(cfg, http=http, only_arms=["base"]))
    assert [c["json"]["query"] for c in http.calls] == ["question 0", "question 0:check"]


def test_journal_names_fold_accents():
    """NFKD accent folding: 'Microbiología' matches a 'microbiologia' pattern."""
    assert sample.normalise_journal("Revista de Microbiología & Virología") == \
        "revista de microbiologia and virologia"
    m = {"micro": [r"\bmicrobiolog(y|ia)\b"]}
    assert sample.journal_family("Revista Argentina de Microbiología", m) == "micro"
    assert sample.journal_family("Archives Médicales", {"med": [r"\bmedicales\b"]}) == "med"


def test_tokenizer_label_is_explicit_or_basename(monkeypatch):
    """The tokenizer name in outputs is the explicit label, else the model basename (no path)."""
    assert score.tokenizer_label("/private/models/Qwen3-Embedding-8B/") == "Qwen3-Embedding-8B"
    assert score.tokenizer_label({"backend": "hf", "model": "/srv/x/tok"}) == "tok"
    assert score.tokenizer_label("org/model-name") == "model-name"
    assert score.tokenizer_label({"backend": "estimate"}) == "estimate"
    assert score.tokenizer_label("/private/path", "qwen3-tokenizer") == "qwen3-tokenizer"

    class Counter:
        def count(self, text):
            return len(text)

    import ragstack.ingestion.tokenization as tk
    monkeypatch.setattr(tk, "make_token_counter", lambda **kw: Counter())
    _, name, est = score.make_tokenizer({"backend": "hf", "model": "/home/someone/models/tok"})
    assert name == "tok" and not est
    _, name, _ = score.make_tokenizer("/home/someone/models/tok", "my-label")
    assert name == "my-label"


def _letter_text() -> str:
    s = ("In March a cluster of infections was reported in a rural district. "
         "Samples from twelve patients were sequenced at the reference laboratory. "
         "All isolates carried the same resistance gene on a conjugative plasmid. ")
    return s * 6


def test_passages_fallback_body_for_unheaded_letter():
    rec = {"text": _letter_text(), "path": "PMC7", "metadata": {"content_type": "article", "abstract": False}}
    cfg = {"chars": 300, "per_article": 1, "section_targets": ["results", "abstract"],
           "min_frac": 0.75, "max_frac": 1.25}
    ps, why = passages.pick_passages(rec, cfg, seed=1)
    assert ps == [] and why == "no_target_region"
    ps, why = passages.pick_passages(rec, {**cfg, "fallback_body": True}, seed=1)
    assert why is None and len(ps) == 1
    p = ps[0]
    assert p["fallback"] is True and p["section_type"] == "body"
    assert rec["text"][p["start"]:p["end"]] == p["text"] and 225 <= len(p["text"]) <= 375


def test_passages_fallback_not_used_when_targets_give_a_passage():
    text = _article_text()
    rec = {"text": text, "path": "PMC5", "metadata": {"content_type": "article"}}
    cfg = {"chars": 300, "per_article": 1, "section_targets": ["results"], "min_frac": 0.75, "max_frac": 1.25}
    plain, _ = passages.pick_passages(rec, cfg, seed=1)
    fb, _ = passages.pick_passages(rec, {**cfg, "fallback_body": True}, seed=1)
    assert plain == fb and all(p["fallback"] is False for p in fb)


def test_passages_fallback_keeps_reason_when_it_fails_too():
    rec = {"text": "## Methods\n\nToo short.", "path": "PMC8", "metadata": {}}
    ps, why = passages.pick_passages(rec, {"chars": 1200, "per_article": 1, "section_targets": ["results"],
                                           "min_frac": 0.75, "max_frac": 1.25, "fallback_body": True}, seed=1)
    assert ps == [] and why == "no_target_region"


def test_read_key_plain_and_envelope(tmp_path):
    from oa_chunk_eval.config import read_key
    plain = tmp_path / "k.txt"
    plain.write_text("rk-plain-123\n")
    assert read_key(str(plain)) == "rk-plain-123"
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"kind": "credentials", "secrets": [{"name": "svc", "value": "rk-env-456"}]}, indent=1))
    assert read_key(str(env)) == "rk-env-456"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"other": 1}))
    with pytest.raises(ConfigError):
        read_key(str(bad))
    two = tmp_path / "two.txt"
    two.write_text("line1\nline2\n")
    with pytest.raises(ConfigError):
        read_key(str(two))


# =================================================================== freeze changes (owner 2026-11-05)
# Addendum B: Bonferroni stratum guard, paired-ladder tie-break, noise-subset exclusion.


# ---- Bonferroni stratum guard


def _stratum(center: float, sd: float, n: int = 200, seed: int = 0) -> dict[str, Any]:
    d = np.random.default_rng(seed).normal(center, sd, 20000)
    return {"point": center, "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5)), "n": n,
            "_dist": d}


def _five_strata(min_small: bool = False) -> dict[str, Any]:
    # fam_a: 95% upper ~ -3 + 1.96 x 1.3 = -0.45 < 0 (fails uncorrected);
    # with k = 5 the 99% upper ~ -3 + 2.576 x 1.3 = +0.35 >= 0 (passes corrected)
    jf = {"fam_a": _stratum(-3.0, 1.3, seed=1), "fam_b": _stratum(0.0, 1.0, seed=2),
          "fam_c": _stratum(0.5, 1.0, seed=3)}
    lb = {"short": _stratum(-0.5, 1.0, seed=4), "long": _stratum(0.2, 1.0, seed=5)}
    if min_small:
        jf["fam_tiny"] = {**_stratum(-30.0, 1.0, seed=6), "n": 5}  # below min n: not tested, not counted
    return {"c": {"journal_family": jf, "length_band": lb}}


def test_bonferroni_k_counts_only_tested_strata_across_dims():
    detail: dict[str, Any] = {}
    untested: list[dict[str, Any]] = []
    decide.stratum_guard(_five_strata(min_small=True)["c"], -2.0, 30, untested, "bonferroni", detail)
    assert detail["k"] == 5 and detail["correction"] == "bonferroni"
    assert detail["level"] == pytest.approx(1 - 0.05 / 5)
    assert [s["value"] for s in untested] == ["fam_tiny"]
    assert {s["value"] for s in detail["strata"]} == {"fam_a", "fam_b", "fam_c", "short", "long"}


def test_bonferroni_stratum_passes_corrected_but_fails_uncorrected():
    strata = _five_strata()
    base_cfg = {**DCFG, "min_stratum_n": 30}
    r_none = decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1},
                             MARGINS, {**base_cfg, "stratum_correction": "none"})
    assert r_none["arms"]["c"]["fail_reasons"] == ["stratum_guard"]
    assert [s["value"] for s in r_none["arms"]["c"]["stratum_failures"]] == ["fam_a"]
    assert r_none["arms"]["c"]["stratum_guard_detail"]["level"] == pytest.approx(0.95)
    r_b = decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1},
                          MARGINS, {**base_cfg, "stratum_correction": "bonferroni"})
    c = r_b["arms"]["c"]
    assert c["pass"] and c["stratum_guard"] and c["stratum_failures"] == []
    fam_a = next(s for s in c["stratum_guard_detail"]["strata"] if s["value"] == "fam_a")
    assert fam_a["hi"] > 0 > strata["c"]["journal_family"]["fam_a"]["hi"]  # corrected bound is wider
    assert fam_a["point"] == -3.0 and not fam_a["fails"]


def test_bonferroni_without_bootstrap_dist_is_an_error_and_bad_value_refused():
    strata = {"c": {"journal_family": {"fam_a": {"point": -3.0, "lo": -5.0, "hi": -1.0, "n": 200}}}}
    with pytest.raises(ValueError):
        decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1},
                        MARGINS, {**DCFG, "stratum_correction": "bonferroni"})
    with pytest.raises(ConfigError):
        decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, strata, {"base": 1, "c": 1},
                        MARGINS, {**DCFG, "stratum_correction": "holm"})


def test_bonferroni_strata_from_rows_deterministic_and_match_score():
    rng = np.random.default_rng(3)
    rows = [{"strata": {"journal_family": "fa" if i % 3 else "fb", "length_band": "short" if i % 2 else "long"},
             "arms": {"base": {"5": {"primary": int(rng.random() < 0.8)}},
                      "alt": {"5": {"primary": int(rng.random() < 0.7)}}}} for i in range(300)]
    dims = ["journal_family", "length_band"]
    s1 = decide.strata_from_rows(rows, ["base", "alt"], "base", 5, dims, 2000, 0)
    s2 = decide.strata_from_rows(rows, ["base", "alt"], "base", 5, dims, 2000, 0)
    ref = score.by_stratum(rows, dims, lambda g: score.compare(g, ["base", "alt"], "base", 5, "primary", 2000, 0))
    for d in dims:
        for v, c in s1["alt"][d].items():
            assert np.array_equal(c["_dist"], s2["alt"][d][v]["_dist"])
            # the 95% bounds are the ones score.json reports (same rows, order and seed)
            assert round(c["lo"], 4) == ref[d][v]["diff"]["alt"]["lo"]
            assert round(c["hi"], 4) == ref[d][v]["diff"]["alt"]["hi"]
            assert c["n"] == ref[d][v]["diff"]["alt"]["n"]
    det1: dict[str, Any] = {}
    det2: dict[str, Any] = {}
    decide.stratum_guard(s1["alt"], -2.0, 30, None, "bonferroni", det1)
    decide.stratum_guard(s2["alt"], -2.0, 30, None, "bonferroni", det2)
    assert det1 == det2 and det1["k"] == 4


# ---- paired ladder tie-break


LN = 2000


def _base_vec(seed: int = 11) -> np.ndarray:
    return (np.random.default_rng(seed).random(LN) < 0.75).astype(int)


def _shift(v: np.ndarray, gains: int, losses: int, seed: int) -> np.ndarray:
    """``v`` with ``gains`` misses turned into hits and ``losses`` hits into misses."""
    rng = np.random.default_rng(seed)
    out = v.copy()
    zeros, ones = np.flatnonzero(out == 0), np.flatnonzero(out == 1)
    out[rng.choice(zeros, gains, replace=False)] = 1
    out[rng.choice(ones, losses, replace=False)] = 0
    return out


def _ladder_eval(arms, vec, **dcfg_over):
    diffs = {a["name"]: stats.paired_diff(vec[a["name"]], vec["base"], 2000, 0) for a in arms if not a.get("baseline")}
    ctx = {a["name"]: 100 for a in arms}
    return decide.evaluate(arms, "base", diffs, {}, ctx, MARGINS, {**DCFG, "tie_break": "paired_ladder", **dcfg_over},
                           vec, 2000, 0)


def test_ladder_cheap_kept_when_mid_not_directly_better():
    """(i) cheap passes, mid passes vs baseline but is not better than cheap directly."""
    b = _base_vec()
    c = _shift(b, 80, 0, 1)            # +4.0 vs base
    m = _shift(c, 10, 5, 2)            # +4.25 vs base, +0.25 vs cheap
    arms = _arms(("c", "cheap"), ("m", "mid"))
    r = _ladder_eval(arms, {"base": b, "c": c, "m": m})
    assert r["arms"]["c"]["pass"] and r["arms"]["m"]["pass"]
    lad = r["ladder"]
    assert lad["order"] == ["base", "c", "m"] and lad["level"] == pytest.approx(0.95)
    assert [(s["pair"], s["switched"]) for s in lad["steps"]] == [("c - base", True), ("m - c", False)]
    assert lad["steps"][1]["lo"] <= 0
    assert r["winner"] == "c" and not r["kept_baseline"]
    # the default tie-break would have picked the same cheap arm, without a ladder
    diffs = {a: stats.paired_diff(v, b, 2000, 0) for a, v in (("c", c), ("m", m))}
    r_old = decide.evaluate(arms, "base", diffs, {}, {"base": 100, "c": 100, "m": 100}, MARGINS, DCFG)
    assert r_old["winner"] == "c" and r_old["ladder"] is None and r_old["tie_break"] == "cheapest"


def test_ladder_mid_chosen_when_significantly_better_than_cheap():
    """(ii) with Addendum B's ranks (cheap 1 < baseline 2 < mid 3)."""
    b = _base_vec()
    c = _shift(b, 20, 20, 3)           # 0 vs base: non-inferior
    m = _shift(b, 100, 0, 4)           # +5 vs base
    arms = [{"name": "base", "cost_class": "cheap", "baseline": True, "cost_rank": 2},
            {"name": "c", "cost_class": "cheap", "cost_rank": 1}, {"name": "m", "cost_class": "mid", "cost_rank": 3}]
    r = _ladder_eval(arms, {"base": b, "c": c, "m": m})
    lad = r["ladder"]
    assert lad["order"] == ["c", "base", "m"] and lad["order_basis"] == "cost_rank"
    assert [(s["candidate"], s["incumbent"], s["switched"]) for s in lad["steps"]] == [
        ("base", "c", False), ("m", "c", True)]
    assert lad["steps"][1]["lo"] > 0 and r["winner"] == "m"


def test_ladder_three_steps():
    """(iii) cheap 1 < baseline 2 < mid 3 < expensive 4: two switches."""
    b = _base_vec()
    c = _shift(b, 20, 20, 5)
    m = _shift(b, 100, 0, 6)           # +5 vs base
    e = _shift(m, 120, 0, 7)           # +11 vs base, +6 vs mid
    arms = [{"name": "base", "cost_class": "cheap", "baseline": True, "cost_rank": 2},
            {"name": "c", "cost_class": "cheap", "cost_rank": 1}, {"name": "m", "cost_class": "mid", "cost_rank": 3},
            {"name": "e", "cost_class": "expensive", "cost_rank": 4, "workflow_feasible": True}]
    r = _ladder_eval(arms, {"base": b, "c": c, "m": m, "e": e})
    assert all(r["arms"][a]["pass"] for a in ("c", "m", "e"))
    steps = r["ladder"]["steps"]
    assert [(s["pair"], s["switched"]) for s in steps] == [("base - c", False), ("m - c", True), ("e - m", True)]
    assert all(set(s) >= {"pair", "point", "lo", "hi", "switched", "n"} for s in steps)
    assert steps[2]["point"] == pytest.approx(6.0) and r["winner"] == "e"


def test_ladder_cost_rank_fallback_is_class_then_config_order():
    """(iv) no cost_rank: cheap < mid < expensive, then config order; partial cost_rank refused."""
    arms = [{"name": "base", "cost_class": "cheap", "baseline": True}, {"name": "m", "cost_class": "mid"},
            {"name": "c2", "cost_class": "cheap"}, {"name": "c1", "cost_class": "cheap"}]
    order, basis = decide.ladder_order(arms, ["m", "c1", "base", "c2"])
    assert order == ["base", "c2", "c1", "m"] and basis == "cost_class_then_config_order"
    ranked = [{**a, "cost_rank": r} for a, r in zip(arms, (3, 1, 2, 2), strict=True)]
    assert decide.ladder_order(ranked, ["m", "c1", "base", "c2"])[0] == ["m", "c2", "c1", "base"]
    with pytest.raises(ConfigError):
        decide.ladder_order([{**arms[0], "cost_rank": 1}] + arms[1:], ["base", "m"])
    with pytest.raises(ConfigError):
        decide.ladder_order([{**a, "cost_rank": "1"} for a in arms], ["base", "m"])
    b = _base_vec()
    c1 = _shift(b, 20, 20, 8)
    m = _shift(b, 100, 0, 9)
    r = _ladder_eval([arms[0], arms[1], arms[3]], {"base": b, "m": m, "c1": c1})
    assert r["ladder"]["order"] == ["base", "c1", "m"] and r["winner"] == "m"


def test_ladder_baseline_chosen_when_nothing_passes():
    """(v)"""
    b = _base_vec()
    m = _shift(b, 10, 10, 10)          # 0 vs base: fails mid superiority
    r = _ladder_eval(_arms(("m", "mid")), {"base": b, "m": m})
    assert not r["arms"]["m"]["pass"] and r["no_arm_passed"]
    assert r["winner"] == "base" and r["kept_baseline"]
    assert r["ladder"]["order"] == ["base"] and r["ladder"]["steps"] == []


def test_ladder_needs_vectors_and_rejects_unknown_tie_break():
    with pytest.raises(ValueError):
        decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, {}, {"base": 1, "c": 1}, MARGINS,
                        {**DCFG, "tie_break": "paired_ladder"})
    with pytest.raises(ConfigError):
        decide.evaluate(_arms(("c", "cheap")), "base", {"c": _dist(0.0)}, {}, {"base": 1, "c": 1}, MARGINS,
                        {**DCFG, "tie_break": "highest_class"})


# ---- noise-subset exclusion + sensitivity, end to end


def test_noise_subset_excluded_from_primary_and_sensitivity_reported(tmp_path):
    cfg = build_fixture(tmp_path)
    cfg["score"]["exclude_noise_subset"] = True
    cfg["decide"].update({"stratum_correction": "bonferroni", "tie_break": "paired_ladder", "min_stratum_n": 10})
    out = Path(cfg["out_dir"])
    expected = {q["question_id"] for q in noise.noise_questions(cfg, read_jsonl(out / "questions.jsonl"))}
    assert len(expected) == 10
    assert score.run(cfg) == 0
    rows = read_jsonl(out / "per_question.jsonl")
    assert {r["question_id"] for r in rows if r["noise_subset"]} == expected
    s = json.loads((out / "score.json").read_text())
    assert s["primary_set"] == "clean_excl_noise" and s["sensitivity_set"] == "clean"
    assert "NOT the decision" in s["sensitivity_label"]
    ne = s["noise_exclusion"]
    assert ne["enabled"] and ne["n_noise_subset"] == 10 and ne["n_excluded_from_primary"] == 10
    assert s["sets"]["clean"]["n"] == 39 and s["sets"]["clean_excl_noise"]["n"] == 39 - 10 == ne["n_primary"]
    write_noise(cfg)
    assert decide.run(cfg) == 0
    dec = json.loads((out / "decision.json").read_text())
    assert dec["inputs"]["set"] == "clean_excl_noise" and dec["inputs"]["n"] == 29
    sens = dec["sensitivity"]
    assert sens["is_decision"] is False and "NOT the decision" in sens["label"]
    assert sens["set"] == "clean" and sens["n"] == 39 and set(sens["arms"]) == {"alt"}
    assert "pass" in sens["arms"]["alt"] and sens["winner"] in ("base", "alt")
    det = dec["arms"]["alt"]["stratum_guard_detail"]
    # journal_family fa, fb + length_band short (every fixture question is short)
    assert det["correction"] == "bonferroni" and det["k"] == 3 and det["level"] == pytest.approx(1 - 0.05 / 3)
    assert dec["ladder"]["order"][0] == "base" and dec["tie_break"] == "paired_ladder"
    md = (out / "decision.md").read_text()
    assert "## Sensitivity (SENSITIVITY" in md and "NOT the decision" in md
    assert "## Stratum guard (bonferroni)" in md and "## Tie-break: paired ladder" in md
    assert "marker gene" not in md and "col-alt" not in md
    # decide refuses a config that no longer matches how score was computed
    cfg["score"]["exclude_noise_subset"] = False
    assert decide.run(cfg) == 2


def test_default_score_has_no_noise_exclusion_and_no_sensitivity(tmp_path):
    cfg = build_fixture(tmp_path)
    assert score.run(cfg) == 0
    out = Path(cfg["out_dir"])
    s = json.loads((out / "score.json").read_text())
    assert s["noise_exclusion"] == {"enabled": False} and "sensitivity_set" not in s
    assert all("noise_subset" not in r for r in read_jsonl(out / "per_question.jsonl"))
    write_noise(cfg)
    assert decide.run(cfg) == 0
    dec = json.loads((out / "decision.json").read_text())
    assert dec["sensitivity"] is None and dec["ladder"] is None and dec["stratum_correction"] == "none"
