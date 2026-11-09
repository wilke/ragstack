"""Rechunk stage 1b (exploratory): ``rechunk_prep.py stage1b-seed`` (the unlabelled top-10 chunks of
the lost pairs' questions as ``build-control`` seed rows and matching export rows, a counts-only
summary), ``gold-map --extra-final`` (the stage-1b labels merged as registry ``stage1b``; byte-identical
without the flag) and ``rechunk_stage1.py`` on a merged table (U = 0 for the lost pairs under the
default retriever). Made-up ids and files only; no store, no network, no model."""
from __future__ import annotations

import hashlib
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
import alias_study_judge as aj  # noqa: E402
import rechunk_fixture as rfx  # noqa: E402
import rechunk_prep as rp  # noqa: E402
import rechunk_stage1 as rs  # noqa: E402

cid, rid = rfx.cid, rfx.rid
R1, R2, R4 = rid("P01"), rid("P02"), rid("P04")


@pytest.fixture(autouse=True)
def _no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("QDRANT_URL", "ELASTICSEARCH_URL"):
        monkeypatch.setenv(k, "http://127.0.0.1:1")


def top(at: dict[int, str], tag: str, n: int = 50) -> list[str]:
    return [at.get(i, cid(f"fill{tag}{i}")) for i in range(1, n + 1)]


def write_runs(runs: Path, table: Path, ranked: dict[tuple[str, str], dict[str, list[str] | None]]) -> Path:
    """The rechunk run's per-arm rows and summaries (``rechunk_stage1.read_runs``' format)."""
    sha = asc.sha256_file(table)
    for arm in sorted({a for a, _ in ranked}):
        rows = [{"record_id": rec, "condition": asc.RETRIEVERS[r], "failed": ids is None, "ranked_chunk_ids": ids}
                for (a, rec), by in ranked.items() if a == arm for r, ids in by.items()]
        rfx.write_jsonl(runs / arm / "none.jsonl", rows)
        summ = {"status": "complete", "citable": True, "exploratory": True, "mode": "rechunk",
                "conditions": {c: {"gate": None} for c in asc.RETRIEVERS.values()},
                "provenance": {"commit": "a" * 40, "dirty": False},
                "study": {"mode": "rechunk", "exploratory": True,
                          "rechunk_presence": {"label_table_sha256": sha, "export_sha256": ["d" * 64],
                                               "mapped_chunks": 3}}}
        (runs / arm / "none.summary.json").write_text(json.dumps(summ))
    return runs


def export_row(chunk: str, paper: str, content: str | None = None) -> dict[str, Any]:
    return {"chunk_id": chunk, "doc_id": f"doc-{paper}-0000", "content": content or f"text of {chunk}",
            "start_char": 0, "end_char": 10, "metadata": {"source_path": f"{paper}#body", "title": "t",
                                                          "chunk_descriptor": rp.DESCRIPTOR}}


# ------------------------------------------------------------------ a direct world (rows written straight)

def table_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """P1 base not lost; P2 base lost (window paper PMC2); P3 the same record lost in neighbour
    (window paper PMC3); P4 random1 lost, its default call failed."""
    pairs = [
        rfx.pair_row(R1, "base", cid("m1"), tag="1"),
        rfx.pair_row(R2, "base", cid("m2"), tag="2", covers=[cid("m2"), cid("o2")], control_label="not", lost=True,
                     cover_class="other_only"),
        rfx.pair_row(R2, "neighbour", cid("m3"), tag="3", covers=[cid("m3")], control_label="not", lost=True,
                     cover_class="none"),
        rfx.pair_row(R4, "random1", cid("m4"), tag="4", control_label="not", lost=True, cover_class="none"),
    ]
    labels = [rfx.label_row(R1, cid("m1"), "answers"), rfx.label_row(R2, cid("m2"), "not", "control+cover"),
              rfx.label_row(R2, cid("o2"), "answers", "cover"), rfx.label_row(R2, cid("u2"), "unjudged", "cover"),
              rfx.label_row(R2, cid("m3"), "not", "control+cover"), rfx.label_row(R4, cid("m4"), "not")]
    return pairs, labels


RANKED: dict[tuple[str, str], dict[str, list[str] | None]] = {
    ("base", R1): {r: top({1: cid("m1")}, "a") for r in asc.RETRIEVERS},
    ("base", R2): {"default": top({1: cid("x2"), 2: cid("o2"), 3: cid("m2"), 4: cid("u2"), 5: cid("y")}, "b"),
                   "bm25": top({}, "bx"), "norerank": top({}, "bn")},
    ("neighbour", R2): {"default": top({1: cid("y"), 2: cid("z3"), 5: cid("x2"), 6: cid("m3")}, "c"),
                        "bm25": top({}, "cx"), "norerank": top({}, "cn")},
    ("random1", R4): {"default": None, "bm25": top({}, "dx"), "norerank": top({}, "dn")},
}
SEEDS_BASE = [cid("x2"), cid("y"), *(cid(f"fillb{i}") for i in range(6, 11))]
SEEDS_NB = [cid("z3"), *(cid(f"fillc{i}") for i in (3, 4, 7, 8, 9, 10))]


def world(tmp: Path) -> dict[str, Path]:
    pairs, labels = table_rows()
    table = rfx.write_label_table(tmp / "in" / "labels.jsonl", pairs, labels)
    runs = write_runs(tmp / "in" / "runs", table, RANKED)
    base_rows = [export_row(cid("x2"), "PMC2"), export_row(cid("y"), "PMC9"), export_row(cid("m2"), "PMC2"),
                 export_row(cid("o2"), "PMC2"), export_row(cid("m1"), "PMC1"),
                 *(export_row(cid(f"fillb{i}"), "PMC2") for i in range(1, 51)),
                 *(export_row(cid(f"fillbx{i}"), "PMC5") for i in range(1, 51))]
    nb_rows = [export_row(cid("y"), "PMC9"), export_row(cid("z3"), "PMC3"), export_row(cid("m3"), "PMC3"),
               export_row(cid("x2"), "PMC2"), export_row(cid("m4"), "PMC4"),
               *(export_row(cid(f"fillc{i}"), "PMC7") for i in range(1, 51) if i != 3)]
    e1 = rfx.write_jsonl(tmp / "in" / "export-base.jsonl", base_rows)
    e2 = rfx.write_jsonl(tmp / "in" / "export-nb.jsonl", nb_rows)
    # one non-ASCII line (re-serialized ASCII-escaped in the output) and one blank line
    with e2.open("a", encoding="utf-8") as fh:
        fh.write("\n" + json.dumps(export_row(cid("fillc3"), "PMC7", "naïve text"), ensure_ascii=False) + "\n")
    out = tmp / "out"
    return {"table": table, "runs": runs, "e1": e1, "e2": e2, "seed": out / "seed.jsonl",
            "export": out / "export.jsonl", "summary": out / "summary.json"}


def seed_args(w: dict[str, Path]) -> list[str]:
    return ["stage1b-seed", "--label-table", str(w["table"]), "--runs", str(w["runs"]), "--retriever", "base",
            "--export", str(w["e1"]), "--export", str(w["e2"]), "--out-seed", str(w["seed"]),
            "--out-export", str(w["export"]), "--summary", str(w["summary"])]


def run_seed(w: dict[str, Path], **over: Any) -> dict[str, Any]:
    a = {"table_path": w["table"], "runs": w["runs"], "exports": [w["e1"], w["e2"]], "out_seed": w["seed"],
         "out_export": w["export"], "summary": w["summary"], "retriever": "base"}
    a.update(over)
    return rp.run_stage1b_seed(**a)


def test_stage1b_seed_rows(tmp_path: Path) -> None:
    w = world(tmp_path)
    rec = run_seed(w)
    seeds = asc.read_jsonl(w["seed"])
    by = {s["chunk_id"]: s for s in seeds}
    # every unlabelled top-10 chunk of the lost pairs, deduplicated; labelled (answers / not /
    # unjudged), beyond-rank-10, other-retriever and not-lost chunks are never seeded
    assert sorted(by) == sorted(SEEDS_BASE + SEEDS_NB) and len(seeds) == 14
    assert {s["record_id"] for s in seeds} == {R2}
    assert not {cid("o2"), cid("m2"), cid("u2"), cid("m3"), cid("fillb11"), cid("fillbx1"), cid("filla2")} & set(by)
    p2, p3 = aj.pair_id(R2, cid("w2")), aj.pair_id(R2, cid("w3"))
    # the window-paper rule: x2 (both lists) goes to P2 (its window's paper); z3 to P3; y (PMC9, in
    # neither window's paper) to the first lost pair by pair id
    assert by[cid("x2")]["control_of"] == p2 and by[cid("x2")]["same_paper_as_window"] is True
    assert by[cid("x2")]["lost_pairs_with_chunk"] == sorted([p2, p3]) and by[cid("x2")]["best_rank"] == 1
    assert by[cid("z3")]["control_of"] == p3 and by[cid("z3")]["stage_a_chunk_id"] == cid("w3")
    assert by[cid("y")]["control_of"] == min(p2, p3) and by[cid("y")]["same_paper_as_window"] is False
    assert by[cid("fillc3")]["control_of"] == p3 and by[cid("fillc3")]["corpus_arm"] == "neighbour"
    for s in seeds:
        assert s["pair_id"] == aj.pair_id(s["record_id"], s["chunk_id"])
        assert s["stage_a_chunk_id"] == (cid("w2") if s["control_of"] == p2 else cid("w3"))
        assert s["stage"] == "stage1b" and s["arm"] == asc.arm_group(s["corpus_arm"])
    assert rec["lost_pairs"] == 3 and rec["lost_pairs_read"] == 2 and rec["lost_pairs_failed"] == 1
    assert rec["questions"] == 2 and rec["top10_lists"] == 2 and rec["top10_slots"] == 20
    assert rec["top10_slots_by_label"] == {"answers": 1, "not": 2, "unjudged": 1, "no_label_row": 16}
    assert rec["seeds"] == 14 and rec["seed_records"] == 1 and rec["seeds_in_several_lost_pairs"] == 2
    assert rec["seeds_same_paper_as_window"] == 7 and rec["seeds_other_paper"] == 7
    # y's seed takes the corpus arm of the lost pair it was assigned to (the first by pair id)
    y_base = int(by[cid("y")]["control_of"] == p2)
    assert rec["seeds_by_corpus_arm"] == {"base": 6 + y_base, "neighbour": 8 - y_base}


def test_stage1b_export_rows_unchanged(tmp_path: Path) -> None:
    w = world(tmp_path)
    rec = run_seed(w)
    src = {}
    for e in (w["e1"], w["e2"]):
        for line in e.read_text(encoding="utf-8").splitlines():
            if line.strip():
                src.setdefault(json.loads(line)["chunk_id"], line)
    got = w["export"].read_text(encoding="utf-8").splitlines()
    ids = [json.loads(x)["chunk_id"] for x in got]
    assert ids == sorted(SEEDS_BASE + SEEDS_NB) and rec["export_rows"] == 14
    for line, c in zip(got, ids, strict=True):
        assert line.isascii()
        if c == cid("fillc3"):
            assert json.loads(line) == json.loads(src[c]) and line != src[c]
        else:
            assert line == src[c]
    assert rec["export_rows_reescaped"] == 1 and rec["export_duplicate_rows_same_content"] == 2
    # the export reads as the control export build-control expects
    assert set(asc.read_chunk_exports([w["export"]])) == set(ids)


def test_stage1b_summary_is_counts_only(tmp_path: Path) -> None:
    w = world(tmp_path)
    run_seed(w)
    summ = json.loads(w["summary"].read_text())
    seeds = asc.read_jsonl(w["seed"])
    forbidden = rp.read_label_table(w["table"]).forbidden() | {s[k] for s in seeds for k in ("chunk_id", "pair_id",
                                                                                             "doc_id", "control_of")}
    assert asc.public_problems(summ, forbidden) == [] and "host" not in summ["provenance"]
    assert summ["outputs_sha256"] == {"seed": asc.sha256_file(w["seed"]), "export": asc.sha256_file(w["export"])}
    assert summ["inputs_sha256"]["label_table"] == asc.sha256_file(w["table"])
    assert summ["inputs_sha256"]["exports"] == [asc.sha256_file(w["e1"]), asc.sha256_file(w["e2"])]
    assert set(summ["inputs_sha256"]["runs"]) == {"base", "neighbour", "random1"}
    assert summ["schema"] == rp.SCHEMA["stage1b_seed"] and summ["retriever_condition"] == "base"
    raw = w["summary"].read_text()
    assert "PMC" not in raw and "-0000" not in raw and "alias-O-" not in raw


def test_stage1b_seeds_and_build_control(tmp_path: Path) -> None:
    """The seed rows go through ``alias_study_judge.build_control_pairs`` as they are, except a row
    whose chunk lies in another paper than its stage-A pair's window (``c_paper``): build-control
    refuses those, and the summary counts them (``seeds_other_paper``)."""
    w = world(tmp_path)
    run_seed(w)
    seeds = asc.read_jsonl(w["seed"])
    chunks = asc.read_chunk_exports([w["export"]])
    main_pairs = [{"pair_id": aj.pair_id(R2, cid(f"w{t}")), "record_id": R2, "chunk_id": cid(f"w{t}"),
                   "question": "q", "term": "x", "q_paper": "PMC0", "c_paper": paper, "record_kind": "O",
                   "arm": arm, "stage": "A", "need": "found10"}
                  for t, paper, arm in (("2", "PMC2", "base"), ("3", "PMC3", "neighbour"))]
    same = [s for s in seeds if s["same_paper_as_window"]]
    pairs, counts = aj.build_control_pairs(same, main_pairs, chunks, set(chunks))
    assert counts["pairs"] == len(same) == 7 and {p["chunk_id"] for p in pairs} == {s["chunk_id"] for s in same}
    assert all(p["pair_id"] == aj.pair_id(R2, p["chunk_id"]) for p in pairs)
    with pytest.raises(SystemExit, match="paper is not the stage-A passage's"):
        aj.build_control_pairs(seeds, main_pairs, chunks, set(chunks))


def test_stage1b_refusals(tmp_path: Path) -> None:
    w = world(tmp_path)
    with pytest.raises(SystemExit, match="not a condition"):
        run_seed(w, retriever="default")
    with pytest.raises(SystemExit, match="in no --export"):
        run_seed(w, exports=[w["e1"]])
    bad = rfx.write_jsonl(tmp_path / "in" / "bad.jsonl", [export_row(cid("y"), "PMC9", "other text")])
    with pytest.raises(SystemExit, match="conflicting content"):
        run_seed(w, exports=[w["e1"], w["e2"], bad])
    with pytest.raises(SystemExit, match="inside the repository"):
        run_seed(w, summary=_HERE / "summary.json")
    with pytest.raises(SystemExit, match="three distinct files"):
        run_seed(w, out_export=w["seed"])
    assert not w["seed"].exists() and not w["export"].exists() and not w["summary"].exists()
    # a lost pair's record without a row in its arm's run
    rows = [r for r in rp.read_rows(w["runs"] / "neighbour" / "none.jsonl") if r["condition"] != "base"]
    rfx.write_jsonl(w["runs"] / "neighbour" / "none.jsonl", rows)
    with pytest.raises(SystemExit, match="no 'base' row"):
        run_seed(w)


def test_stage1b_run_summary_checked(tmp_path: Path) -> None:
    w = world(tmp_path)
    s = w["runs"] / "base" / "none.summary.json"
    summ = json.loads(s.read_text())
    summ["conditions"]["base"]["gate"] = "GATE-NOT-EVALUABLE"
    s.write_text(json.dumps(summ))
    with pytest.raises(SystemExit, match="gated"):
        run_seed(w)


def test_stage1b_cli_and_write_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = world(tmp_path)
    assert rp.main(seed_args(w)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"lost_pairs": 3, "lost_pairs_failed": 1, "seeds": 14, "seeds_other_paper": 7, "export_rows": 14}
    assert rp.main(seed_args(w)) == 0  # the same seeds again: kept
    w["seed"].write_text(w["seed"].read_text() + "{}\n")
    with pytest.raises(SystemExit, match="exists with other content"):
        rp.main(seed_args(w))


# ------------------------------------------------------------------ gold-map --extra-final

def gold(tmp: Path, extra: list[Path] | None = None, name: str = "labels.jsonl") -> tuple[dict[str, Any], Path]:
    w = rfx.GoldWorld(tmp / "in")
    out = tmp / "private" / name
    rec = rp.run_gold_map(w.paths["sample"], w.paths["pairs_seed"], w.paths["pairs_seed_cover"],
                          w.paths["control_final"], w.paths["cover_final"], w.runs, out, extra_final=extra)
    return rec, out


#: the table ``gold-map`` wrote on this fixture before ``--extra-final`` existed (commit f758b064),
#: and the sha of its summary without the provenance block (canonical JSON, sorted keys)
GOLDEN_TABLE_SHA256 = "38b1bab0c66032674ec8678a5052a9b197d4a5adf62af5046d282f007e37b235"
GOLDEN_SUMMARY_SHA256 = "7a5a3f35deea0c09b676e35d9bb696ffb150d5c82eaa80d900eece4c31d2dcb0"


def summary_sha(path: Path) -> str:
    s = json.loads(path.read_text())
    s.pop("provenance")
    return hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()


def test_gold_map_without_extra_final_is_byte_identical(tmp_path: Path) -> None:
    rec, out = gold(tmp_path)
    assert asc.sha256_file(out) == GOLDEN_TABLE_SHA256 == rec["table_sha256"]
    assert summary_sha(out.with_name(out.name + ".summary.json")) == GOLDEN_SUMMARY_SHA256
    assert "stage1b" not in rec and "extra_registries" not in rp.read_rows(out)[0]
    # an empty list is no extra registry either
    rec2, out2 = gold(tmp_path / "b", [])
    assert out2.read_bytes() == out.read_bytes() and "stage1b" not in rec2


def test_gold_map_extra_final_merges(tmp_path: Path) -> None:
    w = rfx.GoldWorld(tmp_path / "w")
    p = {x.tag: x for x in w.pairs}
    r2, r3, r4 = p["2"].record, p["3"].record, p["4"].record

    def fin(rec: str, chunk: str, label: int | None) -> dict[str, Any]:
        return {**rfx.final(aj.pair_id(rec, chunk), label), "record_id": rec, "chunk_id": chunk}

    f1 = rfx.write_jsonl(tmp_path / "f1.jsonl", [
        {"k": "batch|1", "n": 4},
        fin(r2, cid("new2"), 1),            # new, answers
        fin(r2, p["2"].control, 1),         # control said not: a conflict with the confirm registries
        fin(r4, cid("o4a"), 0),             # cover said unjudged: now judged not (no conflict)
        fin(r3, cid("new3"), None)])        # new, unjudged
    f2 = rfx.write_jsonl(tmp_path / "f2.jsonl", [
        fin(r2, cid("new2"), 0),            # disagrees with f1 within stage 1b: answers wins
        fin(r3, cid("new3b"), 0)])
    rec, out = gold(tmp_path, [f1, f2])
    t = rp.read_label_table(out)
    assert t.meta["extra_registries"] == ["stage1b"]
    assert t.label(r2, cid("new2")) == "answers" and t.labels[(r2, cid("new2"))]["registries"] == {"stage1b": "answers"}
    assert t.labels[(r2, cid("new2"))]["source"] == "stage1b"
    assert t.label(r2, p["2"].control) == "answers"
    assert t.labels[(r2, p["2"].control)]["registries"] == {"control": "not", "cover": "not", "stage1b": "answers"}
    assert t.labels[(r2, p["2"].control)]["source"] == "control+cover+stage1b"
    assert t.label(r4, cid("o4a")) == "not" and t.label(r3, cid("new3")) == "unjudged"
    assert t.label(r3, cid("new3b")) == "not"
    assert rec["labels"] == 12 + 3 and rec["label"] == {"answers": 7, "not": 6, "unjudged": 2}
    assert rec["stage1b"] == {"rows": 6, "rows_skipped_without_ids": 1, "labels": 5, "conflicts_within": 1,
                              "labels_new": 3, "labels_already_judged": 2, "conflicts_with_confirm_registries": 1,
                              "files": 2}
    assert rec["registry_conflicts"] == 2  # the confirm conflict of pair 3, and pair 2's control chunk
    summ = json.loads(out.with_name(out.name + ".summary.json").read_text())
    assert summ["inputs_sha256"]["extra_final"] == [asc.sha256_file(f1), asc.sha256_file(f2)]
    assert asc.public_problems(summ, t.forbidden()) == []
    # pair rows are untouched by the merge
    _, base = gold(tmp_path / "plain")
    assert rp.read_label_table(base).pairs == t.pairs


def test_gold_map_extra_final_refusals(tmp_path: Path) -> None:
    r2 = rid("P02")
    bad_pid = rfx.write_jsonl(tmp_path / "a.jsonl", [{"pair_id": "0" * 16, "record_id": r2, "chunk_id": cid("q"),
                                                      "label": 1, "source": "agree"}])
    with pytest.raises(SystemExit, match="pair_id"):
        gold(tmp_path / "x", [bad_pid])
    foreign = rfx.write_jsonl(tmp_path / "b.jsonl", [{"record_id": rid("P99"), "chunk_id": cid("q"), "label": 1,
                                                      "source": "agree"}])
    with pytest.raises(SystemExit, match="not in the sample"):
        gold(tmp_path / "y", [foreign])
    torn = tmp_path / "c.jsonl"
    torn.write_text('{"record_id": "r", "chunk_id": "c", "label": 1}\n{"record_id": "r", "chu\n')
    with pytest.raises(SystemExit, match="nothing is repaired"):
        gold(tmp_path / "z", [torn])


def test_gold_map_cli_extra_final(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = rfx.GoldWorld(tmp_path / "in")
    r2 = w.pairs[1].record
    f = rfx.write_jsonl(tmp_path / "f.jsonl", [{"record_id": r2, "chunk_id": cid("n"), "label": 1, "source": "agree"}])
    g = rfx.write_jsonl(tmp_path / "g.jsonl", [{"record_id": r2, "chunk_id": cid("n2"), "label": 0,
                                                "source": "agree"}])
    out = tmp_path / "labels.jsonl"
    assert rp.main([*w.args(out), "--extra-final", str(f), "--extra-final", str(g)]) == 0
    capsys.readouterr()
    t = rp.read_label_table(out)
    assert t.label(r2, cid("n")) == "answers" and t.label(r2, cid("n2")) == "not"


# ------------------------------------------------------------------ the report on a merged table

def test_stage1_on_the_merged_table(tmp_path: Path) -> None:
    """gold-map -> stage1b-seed -> (judged) -> gold-map --extra-final -> rechunk_stage1: after
    stage 1b every top-10 chunk of a lost pair is labelled under the default retriever (U = 0).
    The run summaries name the run's table, so the merged table is read with --run-label-table;
    without it the report refuses (the table's sha differs)."""
    w = rfx.GoldWorld(tmp_path / "in")
    t0_path = tmp_path / "private" / "labels.jsonl"
    rp.run_gold_map(w.paths["sample"], w.paths["pairs_seed"], w.paths["pairs_seed_cover"],
                    w.paths["control_final"], w.paths["cover_final"], w.runs, t0_path)
    t0 = rp.read_label_table(t0_path)
    ranked: dict[tuple[str, str], dict[str, list[str] | None]] = {}
    exports: list[dict[str, Any]] = []
    for i, p in enumerate(sorted(t0.pairs, key=lambda x: x["pair_id"])):
        key = (p["corpus_arm"], p["record_id"])
        if key in ranked:
            continue
        own = [p["max_overlap_chunk_id"], *p["cover_any_chunk_ids"]]
        ranked[key] = {r: top({1: own[0], 3: cid(f"s{i}")}, f"r{i}{r}") for r in asc.RETRIEVERS}
        exports += [export_row(c, f"PMC{i}") for c in {*own, *ranked[key]["default"]}]
    seen: set[str] = set()
    ex = rfx.write_jsonl(tmp_path / "in" / "export.jsonl",
                         [r for r in exports if not (r["chunk_id"] in seen or seen.add(r["chunk_id"]))])
    runs = write_runs(tmp_path / "in" / "rechunk-runs", t0_path, ranked)
    out = tmp_path / "private" / "1b"
    before = rs.report(t0, rs.read_runs(runs, t0)[0])["q1_lost_pairs"]["all_lost"]["by_retriever"]["default"]
    assert before["n"] == 4 and before["U_any"]["k"] == 4
    rec = rp.run_stage1b_seed(t0_path, runs, [ex], out / "seed.jsonl", out / "export.jsonl", out / "seed.json")
    assert rec["lost_pairs"] == 4 and rec["seeds"] > 0
    seeds = asc.read_jsonl(out / "seed.jsonl")
    final = [{"k": f"pair|{s['pair_id']}", "pair_id": s["pair_id"], "record_id": s["record_id"],
              "chunk_id": s["chunk_id"], "label": int(s["best_rank"] == 3), "source": "agree"} for s in seeds]
    fin = rfx.write_jsonl(tmp_path / "private" / "1b-final.jsonl", [{"k": "batch|1"}, *final])
    t1_path = tmp_path / "private" / "labels-1b.jsonl"
    rp.run_gold_map(w.paths["sample"], w.paths["pairs_seed"], w.paths["pairs_seed_cover"],
                    w.paths["control_final"], w.paths["cover_final"], w.runs, t1_path, extra_final=[fin])
    # unchanged rechunk_stage1 code refuses a table the run was not made against
    with pytest.raises(SystemExit, match="another label table"):
        rs.run(runs, t1_path, tmp_path / "public.json")
    got = rs.run(runs, t1_path, tmp_path / "public.json", run_table_path=t0_path)
    d = got["q1_lost_pairs"]["all_lost"]["by_retriever"]["default"]
    assert d["n"] == 4 and d["U_any"]["k"] == 0 and d["U_mean"] == 0
    # the stage-1b answers at rank 3 now count in A (not A_own: those read the pair's own chunks only)
    assert d["A"]["k"] == 4 and d["A_own"]["k"] == before["A_own"]["k"]
    assert d["A_or_unlabelled"]["k"] == d["A"]["k"]
    assert got["merged_label_table"]["run_label_table_sha256"] == t0.sha256
    assert got["merged_label_table"]["labels_added"] == len(seeds)
    assert got["label_table_sha256"] == asc.sha256_file(t1_path)
    # other retrievers were not judged: their unlabelled chunks remain
    assert got["q1_lost_pairs"]["all_lost"]["by_retriever"]["bm25"]["U_any"]["k"] == 4


def test_check_merged_refuses_a_table_that_is_not_an_extension(tmp_path: Path) -> None:
    pairs, labels = table_rows()
    t0 = rp.read_label_table(rfx.write_label_table(tmp_path / "t0.jsonl", pairs, labels))
    changed = [dict(x) for x in labels]
    changed[1] = rfx.label_row(R2, cid("m2"), "answers", "control+cover")
    t1 = rp.read_label_table(rfx.write_label_table(tmp_path / "t1.jsonl", pairs, changed))
    with pytest.raises(SystemExit, match="missing or changed"):
        rs.check_merged(t0, t1)
    t2 = rp.read_label_table(rfx.write_label_table(tmp_path / "t2.jsonl", pairs[:-1], labels))
    with pytest.raises(SystemExit, match="other pair rows"):
        rs.check_merged(t0, t2)
    t3 = rp.read_label_table(rfx.write_label_table(tmp_path / "t3.jsonl", pairs,
                                                   [*labels, rfx.label_row(R2, cid("new"), "not", "stage1b")]))
    assert rs.check_merged(t0, t3)["labels_added"] == 1
