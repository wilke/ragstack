"""Rechunk stage 1 (exploratory), the report: per retriever and pair A / M (and its rank) / U and
the upper bound, failed calls left out, Q1 (the lost pairs by cover-any class) and Q2 (all pairs by
coverage band and corpus arm), the run-summary checks, and a public file with no id in it. A label
table and run files written from rows; no store, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench",
           _HERE.parents[1] / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import rechunk_fixture as rfx  # noqa: E402
import rechunk_prep as rp  # noqa: E402
import rechunk_stage1 as rs  # noqa: E402

cid, rid = rfx.cid, rfx.rid
R1, R2, R3, R4 = rid("P01"), rid("P02"), rid("P03"), rid("P04")
K = [cid(f"k{i}") for i in range(8)]


def table_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """B1 base answers; B2 base lost other_only; B3 base lost none (ten labelled chunks for its
    record, one unjudged; stage A retrieved its window only in a neighbour leg); N1 neighbour lost
    primary_only (the record of B1); Q1 random1 answers. Every pair's draw stratum is the
    confirmatory sample's (answers)."""
    pairs = [
        rfx.pair_row(R1, "base", cid("m1"), tag="1", stage_a_legs=["base|none|default", "base|none|norerank"]),
        rfx.pair_row(R2, "base", cid("m2"), tag="2", covers=[cid("m2"), cid("o2")], control_label="not", lost=True,
                     cover_class="other_only", coverage_band="0.5-0.8", stage_a_legs=["base|none|bm25"]),
        rfx.pair_row(R3, "base", cid("m3"), tag="3", covers=[cid("m3"), cid("o3")], control_label="not", lost=True,
                     cover_class="none", coverage_band="0.5-0.8", stage_a_legs=["neighbour|none|default"]),
        rfx.pair_row(R1, "neighbour", cid("n1"), tag="4", covers=[cid("n1")], control_label="not", lost=True,
                     cover_class="primary_only", stage_a_legs=["neighbour|none|default"]),
        rfx.pair_row(R4, "random1", cid("q1"), tag="5", coverage_band="<0.5",
                     stage_a_legs=["random1|none|default", "random1|none|norerank"]),
    ]
    labels = [rfx.label_row(R1, cid("m1"), "answers"), rfx.label_row(R2, cid("m2"), "not", "control+cover"),
              rfx.label_row(R2, cid("o2"), "answers", "cover"), rfx.label_row(R3, cid("m3"), "not", "control+cover"),
              rfx.label_row(R3, cid("o3"), "not", "cover"), rfx.label_row(R3, cid("u3"), "unjudged", "cover"),
              *(rfx.label_row(R3, k, "not", "cover") for k in K), rfx.label_row(R1, cid("n1"), "answers", "control+cover"),
              rfx.label_row(R4, cid("q1"), "answers")]
    return pairs, labels


def top(at: dict[int, str], n: int = 50, tag: str = "") -> list[str]:
    """A ranked list of ``n`` ids: ``at`` (rank -> id), unlabelled fillers elsewhere."""
    return [at.get(i, cid(f"fill{tag}{i}")) for i in range(1, n + 1)]


#: (corpus arm, record) -> retriever -> ranked list (None: the call failed)
RANKED: dict[tuple[str, str], dict[str, list[str] | None]] = {
    ("base", R1): {"default": top({1: cid("m1")}), "bm25": top({3: cid("o2")}), "norerank": top({2: cid("m1")})},
    ("base", R2): {"default": top({2: cid("o2")}), "bm25": top({1: cid("m2")}), "norerank": top({})},
    ("base", R3): {"default": top({5: cid("m3")}),
                   "bm25": top({**{i + 1: k for i, k in enumerate(K)}, 9: cid("o3"), 10: cid("u3"), 11: cid("m3")}),
                   "norerank": top({1: cid("o3"), **{i + 2: k for i, k in enumerate(K)}, 10: cid("m3")})},
    # bm25: B1's chunk m1 (answers for the record, not one of N1's own chunks) at rank 5
    ("neighbour", R1): {"default": top({2: cid("n1")}), "bm25": top({5: cid("m1")}), "norerank": top({4: cid("n1")})},
    ("random1", R4): {"default": top({1: cid("q1")}), "bm25": None, "norerank": top({7: cid("q1")})},
}


def write_world(tmp: Path, *, summary_over: dict[str, Any] | None = None, drop_row: tuple[str, str] | None = None
                ) -> tuple[Path, Path]:
    pairs, labels = table_rows()
    table = rfx.write_label_table(tmp / "labels.jsonl", pairs, labels)
    sha = asc.sha256_file(table)
    runs = tmp / "runs"
    for arm in ("base", "neighbour", "random1"):
        rows = []
        for (a, rec), by in RANKED.items():
            if a != arm:
                continue
            for r, ids in by.items():
                if drop_row == (arm, r):
                    continue
                rows.append({"record_id": rec, "condition": asc.RETRIEVERS[r], "failed": ids is None,
                             "ranked_chunk_ids": ids})
        rfx.write_jsonl(runs / arm / "none.jsonl", rows)
        summ: dict[str, Any] = {"status": "complete", "citable": True, "exploratory": True, "mode": "rechunk",
                "conditions": {c: {"gate": None, "outcomes": "note"} for c in asc.RETRIEVERS.values()},
                "provenance": {"commit": "a" * 40, "dirty": False, "host": "somewhere"},
                "study": {"mode": "rechunk", "exploratory": True, "stage": "A",
                          "collection_build": {"collection": "bench-x", "n_chunks": 100, "load_receipt_sha256": "b" * 64,
                                               "glossary_file_sha256": "c" * 64},
                          "rechunk_presence": {"label_table_sha256": sha, "export_sha256": ["d" * 64],
                                               "mapped_chunks": 3}}}
        for k, v in (summary_over or {}).get(arm, {}).items():
            if k == "study":
                summ["study"].update(v)
            else:
                summ[k] = v
        (runs / arm / "none.summary.json").write_text(json.dumps(summ))
    return runs, table


def test_score_pair_measures() -> None:
    pairs, labels = table_rows()
    t = rp.LabelTable(pairs, {(x["record_id"], x["chunk_id"]): x for x in labels}, "0" * 64, {})
    by = {p["stage_a_chunk_id"]: p for p in pairs}

    def s(tag: str, arm: str, rec: str, r: str) -> dict[str, Any]:
        ids = RANKED[(arm, rec)][r]
        return rs.score_pair(by[cid(f"w{tag}")], {"failed": ids is None, "ranked_chunk_ids": ids}, t)

    assert s("1", "base", R1, "default") == {"failed": False, "A": True, "A_own": True, "M": True, "M_rank": 1,
                                             "U": 9, "A_or_unlabelled": True}
    # o2 answers for R2, not for R1: unlabelled there
    assert s("1", "base", R1, "bm25") == {"failed": False, "A": False, "A_own": False, "M": False, "M_rank": None,
                                          "U": 10, "A_or_unlabelled": True}
    # o2 is one of B2's own cover-any chunks
    got = s("2", "base", R2, "default")
    assert got["A"] is True and got["A_own"] is True and got["M"] is False
    assert s("3", "base", R3, "default") == {"failed": False, "A": False, "A_own": False, "M": True, "M_rank": 5,
                                             "U": 9, "A_or_unlabelled": True}
    # rank 11 is outside the top 10; an unjudged label counts as unlabelled
    assert s("3", "base", R3, "bm25") == {"failed": False, "A": False, "A_own": False, "M": False, "M_rank": None,
                                          "U": 1, "A_or_unlabelled": True}
    # ten chunks, all labelled not: the upper bound is no
    assert s("3", "base", R3, "norerank") == {"failed": False, "A": False, "A_own": False, "M": True, "M_rank": 10,
                                              "U": 0, "A_or_unlabelled": False}
    # m1 answers for the record but was mapped for B1, not N1: A from another pair's chunk only
    assert s("4", "neighbour", R1, "bm25") == {"failed": False, "A": True, "A_own": False, "M": False,
                                               "M_rank": None, "U": 9, "A_or_unlabelled": True}
    assert s("4", "neighbour", R1, "default")["A_own"] is True
    assert s("5", "random1", R4, "bm25") == {"failed": True}
    with pytest.raises(SystemExit, match="no row"):
        rs.score_pair(by[cid("w1")], None, t)


def test_report_q1_q2_and_reference(tmp_path: Path) -> None:
    runs, table = write_world(tmp_path)
    out = tmp_path / "public" / "stage1.json"
    rec = rs.run(runs, table, out)
    assert rec["pairs"] == 5 and rec["records"] == 4 and rec["exploratory"] is True
    assert rec["pairs_by_corpus_arm"] == {"base": 3, "neighbour": 1, "random1": 1}
    assert rec["base_windows_only_in_larger_arm_legs"] == 1  # B3
    q1 = rec["q1_lost_pairs"]
    assert q1["lost_pairs"] == 3 and q1["cover_class"] == {"other_only": 1, "both": 0, "primary_only": 1, "none": 1}
    # the plan's groups: recovered (other_only + both), others (none only), primary_only alone, all lost
    assert {k: q1["definitions"][k] for k in ("recovered", "others", "primary_only")} == {
        "recovered": ["other_only", "both"], "others": ["none"], "primary_only": ["primary_only"]}
    assert "conflicting re-read" in q1["definitions"]["primary_only_note"]
    assert "base collection" in q1["definitions"]["base_windows"]
    assert "none" not in q1
    d = {k: q1[k]["by_retriever"]["default"] for k in ("recovered", "others", "primary_only", "all_lost")}
    assert d["recovered"]["n"] == 1 and d["recovered"]["A"]["k"] == 1 and d["recovered"]["M"]["k"] == 0
    assert d["recovered"]["A_own"]["k"] == 1
    assert d["others"]["n"] == 1 and d["others"]["A"]["k"] == 0 and d["others"]["M"]["k"] == 1
    assert d["others"]["M_rank"] == {"1": 0, "2-3": 0, "4-10": 1} and d["others"]["A_or_unlabelled"]["k"] == 1
    assert d["primary_only"]["n"] == 1 and d["primary_only"]["A"]["k"] == 1 and d["primary_only"]["M"]["k"] == 1
    assert d["all_lost"]["n"] == 3 and d["all_lost"]["A"]["k"] == 2 and d["all_lost"]["A_own"]["k"] == 2
    assert d["all_lost"]["M"]["k"] == 2 and d["all_lost"]["M_rank"] == {"1": 0, "2-3": 1, "4-10": 1}
    assert d["all_lost"]["records"] == 3 and d["all_lost"]["top10_lists"] == 3 and q1["all_lost"]["pairs"] == 3
    assert "wilson95" in d["all_lost"]["A_own"]
    # bm25: N1's A comes only from B1's chunk, counted
    pb = q1["primary_only"]["by_retriever"]["bm25"]
    assert pb["A"]["k"] == 1 and pb["A_own"]["k"] == 0 and pb["A_from_other_pairs_only"]["k"] == 1
    assert q1["others"]["by_retriever"]["norerank"]["A_or_unlabelled"] == {
        **q1["others"]["by_retriever"]["norerank"]["A_or_unlabelled"], "k": 0, "n": 1}
    q2 = rec["q2_all_pairs"]
    assert q2["headline"]["M_all_pairs"]["default"]["k"] == 4 and q2["headline"]["M_all_pairs"]["default"]["n"] == 5
    assert q2["headline"]["A_control_answers_pairs"]["default"] == {
        **q2["headline"]["A_control_answers_pairs"]["default"], "k": 2, "n": 2}
    assert q2["headline"]["A_own_control_answers_pairs"]["default"]["k"] == 2
    # M among the pairs whose window was in the same retriever's same-arm stage-A top 10:
    # default B1, N1, Q1 (all M); bm25 B2 (M at 1); norerank B1, Q1
    wr = q2["headline"]["M_window_retained"]
    assert [(wr[r]["k"], wr[r]["n"]) for r in ("default", "bm25", "norerank")] == [(3, 3), (1, 1), (2, 2)]
    assert "wilson95" in wr["default"]
    # one record (R1) in two arms: 4 records, 5 top-10 lists
    allp = q2["all_pairs"]["all"]
    assert allp["records"] == 4 and allp["top10_lists"] == 5
    assert allp["by_retriever"]["default"]["records"] == 4 and allp["by_retriever"]["default"]["top10_lists"] == 5
    # the failed bm25 call is left out and counted
    bm = q2["control_answers_pairs"]["all"]["by_retriever"]["bm25"]
    assert bm["pairs"] == 2 and bm["failed"] == 1 and bm["n"] == 1
    assert q2["control_label"] == {"answers": 2, "not": 3}
    bands = q2["all_pairs"]["by_coverage_band"]
    assert [bands[b]["by_retriever"]["default"]["pairs"] for b in ("<0.5", "0.5-0.8", ">=0.8")] == [1, 2, 2]
    arms = q2["all_pairs"]["by_corpus_arm"]
    assert [arms[a]["by_retriever"]["default"]["pairs"] for a in asc.CORPUS_ARMS] == [3, 1, 1, 0]
    assert arms["random2"]["by_retriever"]["default"]["M"]["rate"] is None
    assert q2["control_answers_pairs"]["by_corpus_arm"]["neighbour"]["by_retriever"]["default"]["pairs"] == 0
    ref = q2["all_pairs"]["all"]["reference"]
    assert ref["draw_stratum"] == {"answers": 5} and "stage_a_status" not in ref
    assert ref["base_windows_only_in_larger_arm_legs"] == 1
    # judged stage-A legs only (bm25 is judged in stage B)
    assert ref["stage_a_judged_legs"] == {"base|none|default": 1, "base|none|norerank": 1, "neighbour|none|default": 2,
                                          "random1|none|default": 1, "random1|none|norerank": 1}
    same = ref["window_in_stage_a_top10_same_arm"]
    assert same["default"]["k"] == 3 and same["bm25"]["k"] == 1 and same["norerank"]["k"] == 2
    assert set(rec["runs"]) == {"base", "neighbour", "random1"}
    assert rec["runs"]["base"]["gates"] == {"base": None, "bm25": None, "norerank": None}
    assert rec["runs"]["base"]["collection_build"] == {"n_chunks": 100, "load_receipt_sha256": "b" * 64,
                                                       "glossary_file_sha256": "c" * 64}


def test_public_file_holds_no_id(tmp_path: Path) -> None:
    runs, table = write_world(tmp_path)
    out = tmp_path / "stage1.json"
    rs.run(runs, table, out)
    text = out.read_text()
    t = rp.read_label_table(table)
    assert asc.public_problems(json.loads(text), t.forbidden()) == []
    for x in (R1, cid("m1"), cid("fill7"), "somewhere", "bench-x"):
        assert x not in text
    assert "host" not in json.loads(text)["provenance"]


def test_a_leaked_id_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs, table = write_world(tmp_path)
    real = rs.report

    def leaky(*a: Any) -> dict[str, Any]:
        return {**real(*a), "oops": R2}

    monkeypatch.setattr(rs, "report", leaky)
    out = tmp_path / "stage1.json"
    with pytest.raises(SystemExit):
        rs.run(runs, table, out)
    assert not out.exists()


@pytest.mark.parametrize(("over", "msg"), [
    ({"base": {"status": "running"}}, "status"),
    ({"base": {"status": "hard_stop", "reason": "config differs"}}, "hard stop \\(config differs\\)"),
    ({"neighbour": {"conditions": {"base": {"gate": None}, "bm25": {"gate": "GATE: served depth differs"},
                                   "norerank": {"gate": None}}}}, "gated condition\\(s\\): bm25: GATE: served depth"),
    ({"neighbour": {"study": {"mode": "main"}}}, "not a rechunk-mode run"),
    ({"neighbour": {"study": {"exploratory": False}}}, "not a rechunk-mode run"),
    ({"random1": {"conditions": {"base": {}, "bm25": {}}}}, "retriever is missing"),
    ({"base": {"study": {"rechunk_presence": {"label_table_sha256": "e" * 64}}}}, "another label table"),
])
def test_run_summary_checks(tmp_path: Path, over: dict[str, Any], msg: str) -> None:
    runs, table = write_world(tmp_path, summary_over=over)
    with pytest.raises(SystemExit, match=msg):
        rs.run(runs, table, tmp_path / "stage1.json")


def test_missing_run_or_row_is_refused(tmp_path: Path) -> None:
    runs, table = write_world(tmp_path / "a", drop_row=("neighbour", "norerank"))
    with pytest.raises(SystemExit, match="no row"):
        rs.run(runs, table, tmp_path / "a" / "stage1.json")
    runs, table = write_world(tmp_path / "b")
    (runs / "random1" / "none.jsonl").unlink()
    with pytest.raises(SystemExit, match="no rechunk run for random1"):
        rs.run(runs, table, tmp_path / "b" / "stage1.json")


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs, table = write_world(tmp_path)
    out = tmp_path / "stage1.json"
    assert rs.main(["--runs", str(runs), "--label-table", str(table), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["pairs"] == 5 and printed["lost_pairs"] == 3 and out.is_file()
