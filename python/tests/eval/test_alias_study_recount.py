"""Alias study record recount (prereg §4.1, §4.3; §15 items 2, 3): the verdict-source split, the
activation order and extension coverage, the overlaps, the prereg comparison, and an output that
carries no id. A synthetic study directory; no /rag path."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_study_common as asc  # noqa: E402
import alias_study_recount as rc  # noqa: E402


def pid(i: int) -> str:
    return f"PMCSYN{i:06d}"


def rid(paper: str, k: str) -> str:
    return f"alias-O-{paper.lower()}-{k}"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def rec(paper: str, k: str, kind: str, alias: str, verifier: str = "claude") -> dict[str, Any]:
    return {"record_id": rid(paper, k), "paper": paper, "kind": kind, "stratum": kind,
            "question": f"What does the synthetic study {paper} report about the measure {alias} in its cohort?",
            "primary": f"primary {alias}", "alias": alias, "alias_kind": "abbreviation" if k != "g1" else "symbol",
            "gold_chunks": [f"chunk-{paper}-{k}-0000"], "pilot": False, "prompt_version": "alias-draft/2",
            "verifier": verifier, "verifier_model": "claude-opus-5-5" if verifier == "claude" else "gpt-6-astra",
            "verifier_prompt_version": "alias-verify/3", "model": "Gemini 2.5 Pro"}


@pytest.fixture()
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Papers 0-5 satisfied (2 G + 1 C), 6-7 held out (1 G + 1 C; 1 C). Papers 0, 1 carry a Codex
    verdict (a safeguard fallback; an API error), paper 6 one more fallback."""
    d = tmp_path / "study"
    recs: list[dict[str, Any]] = []
    for i in range(6):
        p = pid(i)
        recs += [rec(p, "g0", "G", f"A{i}"), rec(p, "g1", "G", f"B{i}"), rec(p, "c0", "C", f"C{i}")]
    recs[0]["verifier"], recs[0]["verifier_model"] = "codex", "gpt-6-astra"
    recs[3]["verifier"], recs[3]["verifier_model"] = "codex", "gpt-6-astra"
    recs += [rec(pid(6), "g0", "G", "A6", "codex"), rec(pid(6), "c0", "C", "C6"), rec(pid(7), "c0", "C", "C7")]
    write_jsonl(d / "out/main/accepted-alias-O.jsonl", recs)
    verify = []
    for r in recs:
        if r["verifier"] == "codex":
            text = ("API Error: Opus 5's safeguards flagged this message." if r["paper"] == pid(1)
                    else "safety_stop_fallback: answered by claude-opus-5 (safety_stops 2)")
            verify.append({"record_id": r["record_id"], "verifier": "codex", "refusal_text": text})
        else:
            verify.append({"record_id": r["record_id"], "verifier": "claude", "refusal_text": None})
    verify.append({"record_id": "alias-O-rejected-x", "verifier": "codex",
                   "refusal_text": "safety_stop_fallback: answered by ? (safety_stops 1)"})
    write_jsonl(d / "out/main/verify-alias-O.jsonl", verify)
    acc_log = [{"layer": "l2", "verifier": r["verifier"], "verdict": "pass", "record_id": r["record_id"]} for r in recs]
    acc_log.append({"layer": "l2", "verifier": "codex", "verdict": "reject", "record_id": "alias-O-rejected-x"})
    write_jsonl(d / "out/main/acceptance-alias-O.jsonl", acc_log)
    drafts = [{"record_id": r["record_id"], "paper": r["paper"], "model": "Gemini 2.5 Pro"} for r in recs]
    drafts.append({"record_id": "alias-O-calib-shared-0001", "paper": pid(9), "model": "Gemini 2.5 Pro"})
    write_jsonl(d / "out/main/draft-alias-O.jsonl", drafts)
    calls = [{"status": "ok", "pt": 10, "ct": 20} for _ in drafts] + [{"status": "error:http_502"}]
    write_jsonl(d / "out/main/draft-calls-alias-O.jsonl", calls)
    # pool: 10 papers, each reachable (2 distinct G aliases, 1 C)
    papers = [pid(i) for i in range(10)]
    header = {"kind": "header", "seed": "syn|alias|main|O", "design": {"goal": {"G": 2, "C": 1}}, "eval_papers": papers}
    jobs = []
    for p in papers:
        jobs += [{"kind": "job", "paper": p, "stratum": "G", "rank": 0, "job_id": f"G-{p}-0", "alias": "X1",
                  "alias_kind": "abbreviation"},
                 {"kind": "job", "paper": p, "stratum": "G", "rank": 1, "job_id": f"G-{p}-1", "alias": "Y1",
                  "alias_kind": "abbreviation"},
                 {"kind": "job", "paper": p, "stratum": "C", "rank": 0, "job_id": f"C-{p}-0", "alias": "Z1",
                  "alias_kind": "abbreviation"}]
    write_jsonl(d / "out/main/pool-alias-O.jsonl", [header] + jobs)
    order = random.Random("syn|alias|main|O|waves").sample(sorted(papers), len(papers))
    state = {"waves": {"n_active": 10, "wave": 3, "stop": "target_met",
                       "history": [{"wave": 1, "accepted": 6, "papers_active": 4, "papers_satisfied": 1, "t": "T1"},
                                   {"wave": 2, "accepted": 5, "papers_active": 6, "papers_satisfied": 2, "t": "T3"},
                                   {"wave": 3, "accepted": 10, "papers_active": 10, "papers_satisfied": 6, "t": "T5"}]},
             "segments": [{"end": "hard_stop", "ended": "T0"}, {"end": "complete", "ended": "T1"},
                          {"end": "complete", "ended": "T5"}],
             "reseals": [{"t": "T4", "diff": {"waves.target_papers": {"old": 200, "new": 300}}}]}
    (d / "out/main/waves-state-alias-O.json").write_text(json.dumps(state))
    (d / "out/main/acceptance-summary-alias-O.json").write_text(
        json.dumps({"accepted_sha256": asc.sha256_file(d / "out/main/accepted-alias-O.jsonl")}))
    write_jsonl(d / "out/draft-alias-O-pilot.jsonl", [{"paper": pid(0)}, {"paper": pid(8)}])
    write_jsonl(d / "out/pilot2/draft-alias-O.jsonl", [{"paper": pid(6)}])
    write_jsonl(d / "calib/pairs.jsonl", [
        {"pair_id": "cal-pair-000001", "record_id": rid(pid(2), "c0"), "q_paper": pid(2), "c_paper": pid(9),
         "chunk_id": "chunk-cal-000001", "question": "a calibration question that is long enough to be checked"},
        {"pair_id": "cal-pair-000002", "record_id": "alias-O-calib-shared-0001", "q_paper": pid(9), "c_paper": pid(9),
         "chunk_id": "chunk-cal-000002", "question": "another calibration question, also long enough to check"}])
    # validation: drafts on paper 3 (satisfied) and 9; pairs touching satisfied papers in each way
    gold3 = f"chunk-{pid(3)}-g0-0000"
    write_jsonl(d / "out/validation/draft-alias-V.jsonl", [{"paper": pid(3), "record_id": "alias-V-a"},
                                                          {"paper": pid(3), "record_id": "alias-V-b"},
                                                          {"paper": pid(9), "record_id": "alias-V-c"}])
    write_jsonl(d / "out/validation/acceptance-alias-V.jsonl", [
        {"layer": "l1", "verdict": "pass", "paper": pid(3)}, {"layer": "l1", "verdict": "reject", "paper": pid(3)},
        {"layer": "l2", "verdict": "pass", "paper": pid(3)}])
    write_jsonl(d / "out/validation/accepted-alias-V.jsonl", [{"paper": pid(3), "record_id": "alias-V-a"}])
    vq = "a validation question with plenty of words to pass the forty character window"
    write_jsonl(d / "calib/validation/pairs.jsonl", [
        {"pair_id": "val-pair-000001", "record_id": "alias-V-a", "q_paper": pid(3), "c_paper": pid(3),
         "chunk_id": gold3, "question": vq, "stratum": "gold"},
        {"pair_id": "val-pair-000002", "record_id": "alias-V-a", "q_paper": pid(3), "c_paper": pid(9),
         "chunk_id": "chunk-other-0001", "question": vq, "stratum": "coll_diff"},
        {"pair_id": "val-pair-000003", "record_id": "alias-V-c", "q_paper": pid(9), "c_paper": pid(4),
         "chunk_id": "chunk-other-0002", "question": vq, "stratum": "lex_other"},
        {"pair_id": "val-pair-000004", "record_id": "alias-V-c", "q_paper": pid(9), "c_paper": pid(9),
         "chunk_id": "chunk-other-0003", "question": vq, "stratum": "gold"}])
    # the exclusion list covers the first 6 activated papers (the "200-paper run") only
    (d / "calib/validation/exclude-papers.txt").write_text("# synthetic\n" + "\n".join(order[:6]) + "\n")
    bal, _ = asc.split_records(asc.load_records(d / "out/main/accepted-alias-O.jsonl"))
    monkeypatch.setattr(asc, "ACCEPTED_SHA256", asc.sha256_file(d / "out/main/accepted-alias-O.jsonl"))
    monkeypatch.setattr(asc, "BALANCED_SHA256", asc.balanced_ids_sha(r["record_id"] for r in bal))
    return {"dir": d, "records": recs, "order": order}


def test_refusal_reason() -> None:
    assert rc.refusal_reason("safety_stop_fallback: answered by claude-opus-5 (safety_stops 2)") == "cli_safeguard_fallback"
    assert rc.refusal_reason("API Error: Opus 5's safeguards flagged this message (x).") == "safeguard_api_error"
    assert rc.refusal_reason(None) == "other"
    assert rc.fallback_answered_by("safety_stop_fallback: answered by claude-opus-5 (safety_stops 2)") == "claude-opus-5"
    assert rc.fallback_answered_by("API Error") is None


def test_verdict_sources_split_by_set(study: dict[str, Any]) -> None:
    res, _, _ = rc.compute(rc.resolve(study["dir"]))
    va, vb = res["verdict_source"]["all"], res["verdict_source"]["balanced"]
    assert va["verifier_model"] == {"claude-opus-5-5": 18, "gpt-6-astra": 3}
    assert va["codex_reason"] == {"cli_safeguard_fallback": 2, "safeguard_api_error": 1}
    assert vb["verifier_model"] == {"claude-opus-5-5": 16, "gpt-6-astra": 2}
    assert vb["codex_reason"] == {"cli_safeguard_fallback": 1, "safeguard_api_error": 1}
    # the verify log's own codex rows include one on a rejected record
    assert res["verifier"]["codex_rows"] == 4
    assert res["verifier"]["l2_verdicts"] == {"claude.pass": 18, "codex.pass": 3, "codex.reject": 1}


def test_record_set_counts_and_layout(study: dict[str, Any]) -> None:
    res, _, _ = rc.compute(rc.resolve(study["dir"]))
    rs = res["record_set"]
    idn = rs["identity"]
    assert (idn["records"], idn["balanced"], idn["descriptive"]) == (21, 18, 3)
    assert idn["accepted_sha256"] == asc.ACCEPTED_SHA256 and idn["balanced_sha256"] == asc.BALANCED_SHA256
    assert rs["accepted_sha256_equals_summary"] is True
    assert rs["balanced"]["layout"] == {"2G+1C": 6} and rs["balanced"]["by_kind"] == {"C": 6, "G": 12}
    assert rs["held_out"]["layout"] == {"0G+1C": 1, "1G+1C": 1}
    assert rs["accepted"]["papers"] == 8
    assert res["checks"]["held_out.papers_1C_only"]["recomputed"] == 1


def test_overlaps(study: dict[str, Any]) -> None:
    res, _, _ = rc.compute(rc.resolve(study["dir"]))
    pil = res["pilot_overlap"]
    assert (pil["papers_with_accepted"], pil["satisfied_papers"]) == (2, 1)
    cal = res["calibration_overlap"]
    assert cal["record_ids_in_main_drafts"] == 2 and cal["record_ids_in_accepted"] == 1
    assert cal["record_ids_in_balanced_by_kind"] == {"C": 1}
    assert cal["question_papers_in_satisfied"] == 1
    v = res["validation_overlap"]
    assert v["satisfied_with_validation_drafts"] == 1 and v["drafts_on_those_papers"] == 2
    assert v["l1_passed_on_those_papers"] == 1 and v["accepted_on_those_papers"] == 1
    assert v["balanced_records_on_those_papers"] == 3
    assert v["balanced_records_on_those_papers_by_kind"] == {"C": 1, "G": 2}
    assert v["question_papers_in_satisfied"] == 1 and v["pairs_with_question_paper_in_satisfied"] == 2
    assert v["pairs_with_balanced_gold_passage"] == 1 and v["distinct_balanced_gold_passages"] == 1
    assert v["pairs_with_passage_paper_in_satisfied"] == 2
    assert v["pairs_touching_satisfied"] == 3


def test_activation_order_and_extension(study: dict[str, Any]) -> None:
    res, _, _ = rc.compute(rc.resolve(study["dir"]))
    w = res["waves"]
    assert w["activated_at_extension"] == 6 and w["accepted_at_extension"] == 11
    assert w["accepted_first_segment_close"] == 6  # the hard stop and its resume are one run
    ext = res["validation_exclusion_vs_extension"]
    assert ext["activated_before_extension_outside_exclusion"] == 0
    assert ext["activated_by_extension"] == 4 and ext["activated_by_extension_outside_exclusion"] == 4
    assert ext["exclusion_covers_extension"] is False
    sat = {pid(i) for i in range(6)}
    assert ext["satisfied_outside_exclusion"] == len(sat - set(study["order"][:6]))


def test_extension_coverage_covered() -> None:
    order = ["a", "b", "c", "d"]
    out = rc.extension_coverage(order, 2, 4, {"a", "c"}, {"a", "c", "d"}, {"a", "b", "c", "d"})
    assert out["exclusion_covers_extension"] is True and out["satisfied_outside_exclusion"] == 0


def test_refusal_reason_precedence() -> None:
    # a text matching both reasons is a CLI safeguard fallback (the prefix is checked first)
    both = "safety_stop_fallback: answered by claude-opus-5 (the safeguards flagged this message)"
    assert rc.refusal_reason(both) == "cli_safeguard_fallback"
    assert rc.refusal_reason("API Error: safeguards flagged it") == "safeguard_api_error"
    assert rc.refusal_reason("a safety_stop_fallback: mid-text is not a prefix") == "other"
    assert rc.refusal_reason("") == "other"


def test_verdict_source_takes_the_last_verify_row_of_a_record() -> None:
    r = rec(pid(0), "g0", "G", "A0", "codex")
    fb = "safety_stop_fallback: answered by claude-opus-5 (safety_stops 2)"
    api = "API Error: Opus 5's safeguards flagged this message."
    rows = [{"record_id": r["record_id"], "verifier": "codex", "refusal_text": fb},
            {"record_id": r["record_id"], "verifier": "codex", "refusal_text": api}]
    out = rc.verdict_sources([r], rows)
    assert out["codex_reason"] == {"safeguard_api_error": 1}
    assert out["cli_safeguard_fallback_answered_by"] == {}
    # and in the other order the earlier API error is superseded by the later fallback
    out = rc.verdict_sources([r], rows[::-1])
    assert out["codex_reason"] == {"cli_safeguard_fallback": 1}
    assert out["cli_safeguard_fallback_answered_by"] == {"claude-opus-5": 1}
    # a later non-Codex row supersedes an earlier Codex one
    late = rows[:1] + [{"record_id": r["record_id"], "verifier": "claude", "refusal_text": None}]
    assert rc.verdict_sources([r], late)["codex_reason"] == {"no_codex_verify_row": 1}


def test_activation_order_keeps_only_reachable_papers() -> None:
    """Reachable: at least goal G distinct alias keys and goal C jobs."""
    header = {"kind": "header", "seed": "syn|u", "design": {"goal": {"G": 2, "C": 1}}}

    def jobs(p: str, g_aliases: list[str], n_c: int) -> list[dict[str, Any]]:
        out = [{"kind": "job", "paper": p, "stratum": "G", "rank": i, "job_id": f"G-{p}-{i}", "alias": a,
                "alias_kind": "abbreviation"} for i, a in enumerate(g_aliases)]
        return out + [{"kind": "job", "paper": p, "stratum": "C", "rank": i, "job_id": f"C-{p}-{i}", "alias": "Z",
                       "alias_kind": "abbreviation"} for i in range(n_c)]

    pool = [header] + jobs("p1", ["X1", "Y1"], 1) + jobs("p2", ["X1", "X1"], 1) + jobs("p3", ["X1", "Y1"], 0) \
        + jobs("p4", ["X1", "Y1", "W1"], 2) + [{"kind": "other", "paper": "p9"}]
    order, info = rc.activation_order(pool)
    assert sorted(order) == ["p1", "p4"]
    assert info == {"papers": 4, "reachable": 2}
    assert order == random.Random("syn|u|waves").sample(["p1", "p4"], 2)


def test_extension_coverage_slices_the_activation_order() -> None:
    # the order is longer than what the run activated: positions n_before..n_after only
    order = ["a", "b", "c", "d", "e", "f"]
    out = rc.extension_coverage(order, 2, 4, {"a", "c", "e", "f"}, {"a", "c", "e", "f"}, {"a", "b", "c"})
    assert out["activated_before_extension"] == 2
    assert out["activated_before_extension_outside_exclusion"] == 0
    assert out["activated_by_extension"] == 2  # c, d (not e, f)
    assert out["activated_by_extension_in_exclusion"] == 1  # c
    assert out["activated_by_extension_outside_exclusion"] == 1  # d
    assert out["satisfied_activated_by_extension"] == 1  # c
    assert out["satisfied_activated_by_extension_outside_exclusion"] == 0
    assert out["satisfied_outside_activation"] == 2  # e, f
    assert out["exclusion_covers_extension"] is False


def test_waves_at_extension_boundary_is_strict() -> None:
    """A wave stamped exactly at the reseal's time is not counted as before it. (The code's
    behaviour, pinned; the prereg does not say which side a same-instant wave belongs to.)"""
    hist = [{"wave": 1, "accepted": 6, "papers_active": 4, "papers_satisfied": 1, "t": "T1"},
            {"wave": 2, "accepted": 5, "papers_active": 6, "papers_satisfied": 2, "t": "T4"},
            {"wave": 3, "accepted": 9, "papers_active": 9, "papers_satisfied": 5, "t": "T5"}]
    state = {"waves": {"n_active": 9, "wave": 3, "stop": "target_met", "history": hist},
             "reseals": [{"t": "T4", "diff": {"waves.target_papers": {"old": 200, "new": 300}}}]}
    w = rc.waves_at_extension(state)
    assert (w["activated_at_extension"], w["satisfied_at_extension"], w["accepted_at_extension"]) == (4, 1, 6)


def test_compare_prefix_and_mismatch() -> None:
    out = rc.compare({"x": "ab" * 32, "y": 3, "z": None}, {"x": "abababab", "y": 4, "z": 1, "w": 2})
    assert out["x"]["status"] == "match" and out["y"]["status"] == "MISMATCH"
    assert out["z"]["status"] == "not_checked" and out["w"]["status"] == "not_checked"


def test_main_writes_counts_only(study: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "provenance", lambda: {"commit": "c" * 40, "dirty": False, "untracked": 0,
                                                   "host": "secret-host", "warnings": ["w"]})
    out = tmp_path / "public" / "recount.json"
    assert rc.main(["--study-dir", str(study["dir"]), "--out", str(out)]) == 0
    text = out.read_text()
    data = json.loads(text)
    assert "host" not in data["provenance"] and "warnings" not in data["provenance"]
    assert data["citable"] is True
    for i in range(10):
        assert pid(i) not in text
    for r in study["records"]:
        assert r["record_id"] not in text and r["gold_chunks"][0] not in text
    for s in ("val-pair-", "cal-pair-", "alias-V-", "chunk-"):
        assert s not in text
    assert all(v["sha256"] for v in data["inputs"].values())
    # the synthetic world is not the prereg's: the comparison reports mismatches, never raises
    assert data["checks"]["balanced.records"]["status"] == "MISMATCH"


def test_main_refuses_a_leak(study: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "provenance", lambda: {"leak": pid(3)})
    out = tmp_path / "recount.json"
    assert rc.main(["--study-dir", str(study["dir"]), "--out", str(out)]) == 1
    assert not out.exists()


def test_wrong_record_set_raises(study: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asc, "ACCEPTED_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="not the frozen one"):
        rc.compute(rc.resolve(study["dir"]))


def test_missing_required_input(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        rc.resolve(tmp_path)
