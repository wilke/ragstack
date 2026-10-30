"""``reranker_test.py part4-rescore``: D3's pre-committed re-score of a
``part4-glossary/1`` result (tooling blocker B3; a post-freeze tooling
addition, no analysis change).

Offline. The first fixture is written by the real writer
(:func:`part4_glossary.rescore`, with the fakes of ``test_part4_glossary.py``:
synthetic pages, no store, no endpoint); the second is a hand-built object of
the writer's shape with more records, for the H1 contrast.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import part4_glossary as p4g  # noqa: E402
import reranker_test as rt  # noqa: E402
import test_part4_glossary as t4  # noqa: E402

AN = {"n_perm": 199, "n_boot": 50, "ci_level": 0.9, "delta": 0.05, "alpha": 0.05}


@pytest.fixture
def writer_result(tmp_path: Path) -> Path:
    """A results file from the real writer: two glossary conditions, arms L and
    T512o0, budget conditions k1 and B60."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "PREREG-synthetic.md").write_text("synthetic\n")
    (out / "part4-questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in t4.QUESTIONS))
    pg = t4.pages()
    g = p4g.resolve_condition("r", t4.rules_file(tmp_path / "count.jsonl"), pg)
    arms = p4g.make_build(tmp_path, [])(pg, ["L", "T512o0"], g.glossaries, g.config)
    cfg = t4.write_config(tmp_path / "config.json", {a: len(cs) for a, cs in arms.items()},
                          {"k1": {"top": 1}, "B60": {"budget": 60}})
    fa, fb = t4.rules_file(tmp_path / "rules.jsonl"), t4.reworded_file(tmp_path / "reworded.jsonl")
    rc = p4g.rescore(cfg, out, t4.make_env(t4.World(), t4.es_rows(), tmp_path), [("rules", fa), ("reworded", fb)])
    assert rc == p4g.EXIT_OK
    return out / p4g.DEFAULT_RESULTS


def argv(result: Path, out: Path, *extra: str) -> list[str]:
    return ["part4-rescore", "--part4-result", str(result), "--part4-arm", "T512o0", "--set-label", "docs-A/T512o0",
            "--n-perm", "199", "--n-boot", "50", "--out", str(out), *extra]


def test_reads_the_real_writers_output(writer_result: Path, tmp_path: Path) -> None:
    obj = json.loads(writer_result.read_text())
    assert obj["schema"] == rt.PART4_SCHEMA == p4g.RESULTS_SCHEMA
    res = rt.part4_rescore(obj, condition=None, part4_arm="T512o0", mapping={"A4-50": "k1"},
                           clusters=None, an=AN, seed=1)
    want = {q: int(obj["per_item"][q]["rules"]["T512o0"]["k1"]["found"]) for q in obj["per_item"]}
    assert res["per_record"] == {q: {"A4-50": v} for q, v in want.items()}
    assert res["arms"]["A4-50"] == {"cell": "k1", "n": 2, "found@10": sum(want.values())}
    assert res["source"]["glossary_condition"] == "rules"  # the reference by default
    assert res["source"]["cells_available"] == ["B60", "k1"]
    assert res["source"]["commit"] == "f" * 40 and res["source"]["citable"] is True
    assert res["d3_coverage"] == {"A0": "absent", "A2": "absent", "A4-50": "mapped: cell 'k1'"}
    assert res["d3_complete"] is False and res["hypotheses"]["H1"]["status"] == "not computed"
    other = rt.part4_rescore(obj, condition="reworded", part4_arm="T512o0", mapping={"A4-50": "k1"},
                             clusters=None, an=AN, seed=1)
    assert other["source"]["glossary_condition"] == "reworded"


def test_the_writer_holds_no_first_stage_cells(writer_result: Path) -> None:
    """The writer stores only its budget-condition cells of the reranked list: an
    A0 or A2 cell cannot be read from it, and the refusal names what is there."""
    obj = json.loads(writer_result.read_text())
    for cell in ("bm25", "hybrid", "rr_list"):
        with pytest.raises(SystemExit, match=r"cells this result holds: \['B60', 'k1'\]"):
            rt.part4_rescore(obj, condition=None, part4_arm="T512o0", mapping={"A0": cell},
                             clusters=None, an=AN, seed=1)


def test_cli_partial_coverage_fails_unless_allowed(writer_result: Path, tmp_path: Path) -> None:
    out = tmp_path / "rescore.json"
    assert rt.main(argv(writer_result, out, "--map", "A4-50=k1")) == rt.EXIT_FAIL
    rec = json.loads(out.read_text())  # written either way, with the gap stated
    assert rec["schema"] == rt.PART4_RESCORE_SCHEMA and rec["d3_complete"] is False
    assert rec["input"]["sha256"] == rt.sha256_file(writer_result) and "provenance" in rec
    assert rt.main(argv(writer_result, out, "--map", "A4-50=k1", "--allow-partial")) == rt.EXIT_OK


@pytest.mark.parametrize("bad", ["A9=k1", "A4-50", "A4-50=", "k1"])
def test_map_is_explicit_and_restricted_to_the_d3_arms(bad: str) -> None:
    with pytest.raises(SystemExit, match="ARM=CELL"):
        rt.parse_part4_map([bad])
    with pytest.raises(SystemExit, match="twice"):
        rt.parse_part4_map(["A0=a", "A0=b"])


def test_refuses_other_schemas_and_counts_only_copies(writer_result: Path) -> None:
    obj = json.loads(writer_result.read_text())
    m = {"A4-50": "k1"}
    with pytest.raises(SystemExit, match="not a part4-glossary/1"):
        rt.part4_rescore({**obj, "schema": "reranker-lists/1"}, condition=None, part4_arm="T512o0", mapping=m,
                         clusters=None, an=AN, seed=1)
    with pytest.raises(SystemExit, match="counts-only"):
        rt.part4_rescore({k: v for k, v in obj.items() if k != "per_item"}, condition=None, part4_arm="T512o0",
                         mapping=m, clusters=None, an=AN, seed=1)
    with pytest.raises(SystemExit, match="not in the result"):
        rt.part4_rescore(obj, condition="qwen36", part4_arm="T512o0", mapping=m, clusters=None, an=AN, seed=1)
    with pytest.raises(SystemExit, match="no rules/X1"):
        rt.part4_rescore(obj, condition=None, part4_arm="X1", mapping=m, clusters=None, an=AN, seed=1)


# ------------------------------------------------------------ H1 on a writer-shaped object

def shaped(n_pages: int = 12, per_page: int = 3) -> tuple[dict[str, Any], dict[str, str]]:
    """The writer's shape (``per_item[q][condition][arm][cell]``) with three cells,
    as a file holding first-stage cells would carry them."""
    per_item, clusters = {}, {}
    for p in range(n_pages):
        for k in range(per_page):
            q = f"q{p:02d}{k}"
            clusters[q] = f"page{p:02d}"
            a0 = (p + k) % 4 != 0          # found by A0 3/4 of the time
            a4 = (p * 3 + k) % 2 == 0      # by A4-50 about half
            cell = {"k10": {"found": a4, "chunks": 10, "ctx_tokens": 900, "chunks_with_glossary": 0},
                    "bm25@10": {"found": a0}, "hy@10": {"found": not a4}}
            per_item[q] = {"rules": {"T512o0": cell}}
    obj = {"schema": "part4-glossary/1", "reference_condition": "rules",
           "glossary_conditions": [{"name": "rules"}], "citable": True,
           "provenance": {"commit": "a" * 40, "dirty": False, "untracked": 0}, "per_item": per_item}
    return obj, clusters


def test_h1_is_the_stats_test_on_the_mapped_outcomes() -> None:
    obj, clusters = shaped()
    m = {"A0": "bm25@10", "A2": "hy@10", "A4-50": "k10"}
    res = rt.part4_rescore(obj, condition=None, part4_arm="T512o0", mapping=m, clusters=clusters, an=AN, seed=7)
    assert res["d3_complete"] is True
    rids = sorted(obj["per_item"])
    a = [int(obj["per_item"][r]["rules"]["T512o0"]["bm25@10"]["found"]) for r in rids]
    b = [int(obj["per_item"][r]["rules"]["T512o0"]["k10"]["found"]) for r in rids]
    want = rt.run_contrast([clusters[r] for r in rids], a, b, "superiority", AN, 7)
    h1 = res["hypotheses"]["H1"]
    assert h1["statement"] == rt.HYPOTHESES["H1"]["statement"]
    assert {k: h1[k] for k in want} == want
    assert res["arms"]["A0"]["found@10"] == sum(a) and res["arms"]["A4-50"]["found@10"] == sum(b)
    assert res["arms"]["A2"]["found@10"] == len(rids) - sum(b)
    assert set(res["hypotheses"]) == {"H1"}  # no other contrast is computed


def test_h1_refuses_records_without_a_cluster() -> None:
    obj, clusters = shaped()
    clusters.pop(sorted(clusters)[0])
    with pytest.raises(SystemExit, match="not in the questions file"):
        rt.part4_rescore(obj, condition=None, part4_arm="T512o0", mapping={"A0": "bm25@10", "A4-50": "k10"},
                         clusters=clusters, an=AN, seed=7)


def test_cli_with_questions_complete_and_text_free(tmp_path: Path) -> None:
    obj, clusters = shaped()
    src = tmp_path / "step7.json"
    src.write_text(json.dumps(obj))
    qs = tmp_path / "q.jsonl"
    qs.write_text("".join(json.dumps({"record_id": q, "doc_id": c, "question": f"a long private question {q}",
                                      "gold": [f"a long private gold quote {q}"]}) + "\n"
                          for q, c in clusters.items()))
    out = tmp_path / "rescore.json"
    rc = rt.main(argv(src, out, "--questions", str(qs), "--map", "A0=bm25@10", "--map", "A2=hy@10",
                      "--map", "A4-50=k10"))
    assert rc == rt.EXIT_OK
    rec = json.loads(out.read_text())
    assert rec["d3_complete"] is True and rec["hypotheses"]["H1"]["type"] == "superiority"
    assert rec["questions"]["clusters"] == 12
    assert "private" not in out.read_text()
