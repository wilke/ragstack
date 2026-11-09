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
    # the main judge's corpus-arm exports (the stage-A chunking: other chunk ids): every paper a seed
    # chunk lies in has a chunk there, so build-control --any-paper can place each seed's arm
    cb, cn = corpus_exports(tmp / "in", CORPUS_PAPERS)
    out = tmp / "out"
    return {"table": table, "runs": runs, "e1": e1, "e2": e2, "seed": out / "seed.jsonl",
            "export": out / "export.jsonl", "summary": out / "summary.json", "cb": cb, "cn": cn}


#: paper -> corpus arm group of the main judge's exports in :func:`world`
CORPUS_PAPERS = {"PMC1": "base", "PMC2": "base", "PMC5": "base", "PMC9": "base",
                 "PMC3": "neighbour", "PMC4": "neighbour", "PMC7": "neighbour"}


def corpus_exports(d: Path, papers: dict[str, str]) -> tuple[Path, Path]:
    """The base and neighbour exports of the main judge: one stage-A-chunking chunk per paper."""
    rows: dict[str, list[dict[str, Any]]] = {"base": [], "neighbour": []}
    for paper, arm in sorted(papers.items()):
        rows[arm].append(export_row(f"old-{paper}", paper))
    return (rfx.write_jsonl(d / "corpus-base.jsonl", rows["base"]),
            rfx.write_jsonl(d / "corpus-neighbour.jsonl", rows["neighbour"]))


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
        # the window's arm is named window_arm: no seed row carries an "arm" a reader could take for
        # the chunk's (build-control --any-paper derives that from the chunk's own paper)
        assert s["stage"] == "stage1b" and s["window_arm"] == asc.arm_group(s["corpus_arm"]) and "arm" not in s
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


#: the main run's stage-A pairs of the lost pairs P2 and P3 (:func:`table_rows`)
MAIN_PAIRS = [{"pair_id": aj.pair_id(R2, cid(f"w{t}")), "record_id": R2, "chunk_id": cid(f"w{t}"),
               "question": f"q{t}", "term": f"x{t}", "q_paper": "PMC0", "c_paper": paper, "record_kind": "O",
               "arm": arm, "stage": "A", "need": "found10"}
              for t, paper, arm in (("2", "PMC2", "base"), ("3", "PMC3", "neighbour"))]


def build_control_inputs(w: dict[str, Path]) -> tuple[dict[str, Any], dict[str, str], set[str]]:
    """What ``Pipeline.build_control`` hands ``build_control_pairs``: ``chunk_arms`` over the main
    judge's corpus-arm exports plus the stage-1b export as the control export, and the control ids."""
    cfg = {"chunk_exports": {"base": str(w["cb"]), "neighbour": str(w["cn"]), aj.CONTROL_EXPORT: str(w["export"])}}
    chunks, arm_of = aj.chunk_arms(cfg)
    return chunks, arm_of, set(asc.read_chunk_exports([w["export"]]))


def test_stage1b_seeds_and_build_control(tmp_path: Path) -> None:
    """Without ``--any-paper`` the same-paper seed rows go through ``build_control_pairs`` as they
    are, and a row whose chunk lies in another paper than its stage-A pair's window is refused (the
    summary counts those, ``seeds_other_paper``)."""
    w = world(tmp_path)
    run_seed(w)
    seeds = asc.read_jsonl(w["seed"])
    chunks, _, control_ids = build_control_inputs(w)
    same = [s for s in seeds if s["same_paper_as_window"]]
    pairs, counts = aj.build_control_pairs(same, MAIN_PAIRS, chunks, control_ids)
    assert counts["pairs"] == len(same) == 7 and {p["chunk_id"] for p in pairs} == {s["chunk_id"] for s in same}
    assert all(p["pair_id"] == aj.pair_id(R2, p["chunk_id"]) for p in pairs)
    with pytest.raises(SystemExit, match="paper is not the stage-A passage's"):
        aj.build_control_pairs(seeds, MAIN_PAIRS, chunks, control_ids)


def test_stage1b_seed_output_through_build_control_any_paper(tmp_path: Path) -> None:
    """End to end: every ``stage1b-seed`` row (other-paper ones included) goes through
    ``build_control_pairs(any_paper=True)`` unchanged. The passage side is the chunk's own paper and
    that paper's corpus arm (never the seed's ``window_arm``), the record side the stage-A pair's."""
    w = world(tmp_path)
    rec = run_seed(w)
    seeds = asc.read_jsonl(w["seed"])
    chunks, arm_of, control_ids = build_control_inputs(w)
    assert not control_ids & set(arm_of)  # the stage-1b chunks are control-export only: arm via their paper
    pairs, counts = aj.build_control_pairs(seeds, MAIN_PAIRS, chunks, control_ids, any_paper=True, arm_of=arm_of)
    by = {p["chunk_id"]: p for p in pairs}
    main = {p["pair_id"]: p for p in MAIN_PAIRS}
    assert sorted(by) == sorted(s["chunk_id"] for s in seeds) and counts["pairs"] == rec["seeds"] == 14
    for s in seeds:
        p, a = by[s["chunk_id"]], main[s["control_of"]]
        paper = chunks[s["chunk_id"]]["paper"]
        assert p["pair_id"] == s["pair_id"] and p["control_of"] == [s["control_of"]]
        assert (p["c_paper"], p["arm"]) == (paper, CORPUS_PAPERS[paper])
        assert (p["c_paper"] == a["c_paper"]) is s["same_paper_as_window"]
        assert (p["record_id"], p["question"], p["term"], p["q_paper"], p["record_kind"]) == \
            (a["record_id"], a["question"], a["term"], a["q_paper"], a["record_kind"])
        assert p["content_sha256"] == asc.sha_text(chunks[s["chunk_id"]]["content"])
    assert counts["passage_paper_not_stage_a_window_paper"] == rec["seeds_other_paper"] == 7
    # y (PMC9, a base paper) takes base whatever lost pair (and window arm) it was assigned to
    y = next(s for s in seeds if s["chunk_id"] == cid("y"))
    assert by[cid("y")]["arm"] == "base" and y["window_arm"] in ("base", "neighbour")
    assert counts["per_arm"] == {"base": 7, "neighbour": 7}


def test_stage1b_any_paper_refuses_a_seed_paper_without_a_corpus_arm(tmp_path: Path) -> None:
    """A seed chunk whose paper has no chunk in any corpus arm's export has no arm: refused."""
    w = world(tmp_path)
    run_seed(w)
    seeds = asc.read_jsonl(w["seed"])
    corpus_exports(tmp_path / "in", {k: v for k, v in CORPUS_PAPERS.items() if k != "PMC9"})
    chunks, arm_of, control_ids = build_control_inputs(w)
    with pytest.raises(SystemExit, match="no chunk in any corpus arm's export"):
        aj.build_control_pairs(seeds, MAIN_PAIRS, chunks, control_ids, any_paper=True, arm_of=arm_of)
    # the seeds of the papers that do have one still go through
    ok = [s for s in seeds if chunks[s["chunk_id"]]["paper"] != "PMC9"]
    pairs, _ = aj.build_control_pairs(ok, MAIN_PAIRS, chunks, control_ids, any_paper=True, arm_of=arm_of)
    assert len(pairs) == len(ok) == 13


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

def gold(tmp: Path, extra: list[Path] | None = None, name: str = "labels.jsonl",
         allow_relabel: bool = False) -> tuple[dict[str, Any], Path]:
    w = rfx.GoldWorld(tmp / "in")
    out = tmp / "private" / name
    rec = rp.run_gold_map(w.paths["sample"], w.paths["pairs_seed"], w.paths["pairs_seed_cover"],
                          w.paths["control_final"], w.paths["cover_final"], w.runs, out, extra_final=extra,
                          allow_relabel=allow_relabel)
    return rec, out


def fin(rec: str, chunk: str, label: int | None) -> dict[str, Any]:
    """A stage-1b judge run's final ``pair|`` row."""
    return {**rfx.final(aj.pair_id(rec, chunk), label), "record_id": rec, "chunk_id": chunk}


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
    f1 = rfx.write_jsonl(tmp_path / "f1.jsonl", [
        {"k": "batch|1", "n": 4},
        fin(r2, cid("new2"), 1),            # new, answers
        fin(r2, p["2"].control, 1),         # control said not: a conflict with the confirm registries
        fin(r4, cid("o4a"), 0),             # cover said unjudged: now judged not (no conflict)
        fin(r3, cid("new3"), None)])        # new, unjudged
    f2 = rfx.write_jsonl(tmp_path / "f2.jsonl", [
        fin(r2, cid("new2"), 0),            # disagrees with f1 within stage 1b: answers wins
        fin(r3, cid("new3b"), 0)])
    # two of these keys already have a label row: refused without --allow-relabel, nothing written
    with pytest.raises(SystemExit, match=r"2 \(record, chunk\) key\(s\) that already have a label row"):
        gold(tmp_path, [f1, f2])
    assert not (tmp_path / "private" / "labels.jsonl").exists()
    rec, out = gold(tmp_path, [f1, f2], allow_relabel=True)
    t = rp.read_label_table(out)
    assert t.meta["extra_registries"] == ["stage1b"] and t.meta["allow_relabel"] is True
    assert t.label(r2, cid("new2")) == "answers" and t.labels[(r2, cid("new2"))]["registries"] == {"stage1b": "answers"}
    assert t.labels[(r2, cid("new2"))]["source"] == "stage1b"
    assert t.label(r2, p["2"].control) == "answers"
    assert t.labels[(r2, p["2"].control)]["registries"] == {"control": "not", "cover": "not", "stage1b": "answers"}
    assert t.labels[(r2, p["2"].control)]["source"] == "control+cover+stage1b"
    assert t.label(r4, cid("o4a")) == "not" and t.label(r3, cid("new3")) == "unjudged"
    assert t.label(r3, cid("new3b")) == "not"
    assert rec["labels"] == 12 + 3 and rec["label"] == {"answers": 7, "not": 6, "unjudged": 2}
    assert rec["stage1b"] == {"rows": 6, "batch_rows_skipped": 1, "labels": 5, "conflicts_within": 1,
                              "labels_new": 3, "labels_already_judged": 2, "conflicts_with_confirm_registries": 1,
                              "allow_relabel": True, "files": 2}
    assert rec["registry_conflicts"] == 2  # the confirm conflict of pair 3, and pair 2's control chunk
    summ = json.loads(out.with_name(out.name + ".summary.json").read_text())
    assert summ["inputs_sha256"]["extra_final"] == [asc.sha256_file(f1), asc.sha256_file(f2)]
    assert asc.public_problems(summ, t.forbidden()) == []
    assert summ["stage1b"]["allow_relabel"] is True
    # pair rows are untouched by the merge
    _, base = gold(tmp_path / "plain")
    t0 = rp.read_label_table(base)
    assert t0.pairs == t.pairs
    # the relabel flipped pair 2's control chunk from not to answers: the report refuses the table
    with pytest.raises(SystemExit, match="combined label"):
        rs.check_merged(t0, t)


def test_gold_map_extra_final_new_keys_only_need_no_flag(tmp_path: Path) -> None:
    w = rfx.GoldWorld(tmp_path / "w")
    r2 = w.pairs[1].record
    f = rfx.write_jsonl(tmp_path / "f.jsonl", [{"k": "batch|1"}, fin(r2, cid("new2"), 1)])
    rec, out = gold(tmp_path, [f])
    assert rec["stage1b"]["labels_already_judged"] == 0 and rec["stage1b"]["allow_relabel"] is False
    assert "allow_relabel" not in rp.read_label_table(out).meta


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
    good = fin(r2, cid("new"), 1)
    # only batch| rows are skipped: a pair| row without its ids is refused, as is any other id-less row
    no_ids = rfx.write_jsonl(tmp_path / "d.jsonl", [good, {k: v for k, v in good.items() if k != "chunk_id"}])
    with pytest.raises(SystemExit, match="a pair\\| row without record_id/chunk_id"):
        gold(tmp_path / "d", [no_ids])
    no_k = rfx.write_jsonl(tmp_path / "e.jsonl", [good, {"label": 1, "source": "agree"}])
    with pytest.raises(SystemExit, match="not a batch\\| row without record_id/chunk_id"):
        gold(tmp_path / "e", [no_k])
    # a file that contributes no label: batch rows only, or empty
    batch_only = rfx.write_jsonl(tmp_path / "f.jsonl", [{"k": "batch|1", "n": 0}])
    empty = tmp_path / "g.jsonl"
    empty.write_text("")
    for i, f in enumerate((batch_only, empty)):
        with pytest.raises(SystemExit, match="#2: the file contributes no label"):
            gold(tmp_path / f"h{i}", [rfx.write_jsonl(tmp_path / f"ok{i}.jsonl", [good]), f])
    # an already-labelled key (pair 2's control chunk) without --allow-relabel
    p2 = {x.tag: x for x in rfx.GoldWorld(tmp_path / "w").pairs}["2"]
    relabel = rfx.write_jsonl(tmp_path / "i.jsonl", [fin(p2.record, p2.control, 0)])
    with pytest.raises(SystemExit, match="--allow-relabel"):
        gold(tmp_path / "i", [relabel])
    with pytest.raises(SystemExit, match="--allow-relabel needs --extra-final"):
        gold(tmp_path / "j", None, allow_relabel=True)
    assert not list(tmp_path.glob("*/private/labels.jsonl"))


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
    p2 = {x.tag: x for x in w.pairs}["2"]
    h = rfx.write_jsonl(tmp_path / "h.jsonl", [fin(p2.record, p2.control, 0)])
    out2 = tmp_path / "labels-relabel.jsonl"
    with pytest.raises(SystemExit, match="--allow-relabel"):
        rp.main([*w.args(out2), "--extra-final", str(h)])
    assert rp.main([*w.args(out2), "--extra-final", str(h), "--allow-relabel"]) == 0
    capsys.readouterr()
    summ = json.loads(out2.with_name(out2.name + ".summary.json").read_text())
    assert summ["stage1b"]["allow_relabel"] is True and summ["stage1b"]["labels_already_judged"] == 1


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
    # only the combined label flips: every registry of the run table's row is kept unchanged and one
    # is added, but the row's label goes from not to answers
    flipped = [dict(x) for x in labels]
    m2 = flipped[1]
    assert (m2["chunk_id"], m2["label"]) == (cid("m2"), "not")
    flipped[1] = {**m2, "label": "answers", "registries": {**m2["registries"], "stage1b": "answers"},
                  "source": m2["source"] + "+stage1b"}
    t4 = rp.read_label_table(rfx.write_label_table(tmp_path / "t4.jsonl", pairs, flipped))
    with pytest.raises(SystemExit, match="combined label of a --run-label-table row differs"):
        rs.check_merged(t0, t4)
    # an added registry that keeps the combined label is accepted
    kept = [dict(x) for x in labels]
    kept[1] = {**m2, "registries": {**m2["registries"], "stage1b": "not"}}
    t5 = rp.read_label_table(rfx.write_label_table(tmp_path / "t5.jsonl", pairs, kept))
    assert rs.check_merged(t0, t5)["labels_added"] == 0
