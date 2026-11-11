"""split-answer review nit d, ``split_answer_prep.py report``: the counts-only result of the neighbour
registry's ``final.jsonl`` per group and context window, each positive group with and without its
document-edge items (§3.2 (b)9), the negative group's false-answers rate; refused on a final file
that is not the neighbour registry's or an items directory that is not the one ``items`` wrote."""
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
import alias_study_judge as aj  # noqa: E402
import split_answer_fixture as sfx  # noqa: E402
import split_answer_prep as sap  # noqa: E402

pytest.importorskip("nltk")
pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")


@pytest.fixture()
def items_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from ragstack.ingestion import chunker_config

    wc = sfx.WordCounter()
    monkeypatch.setattr(chunker_config, "cached_token_counter", lambda *a, **k: wc)
    w = sfx.World(tmp_path / "w")
    monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, **w.pinned()})
    monkeypatch.setattr(sap, "NEGATIVE_QUOTAS", {"same": 3, "other": 0})
    nd = sfx.negative_dir(tmp_path, sap.SCHEMA["negative"], [("alias-O-n1-g0", "dC", 1), ("alias-O-n2-g0", "dB", 1),
                                                             ("alias-O-n3-g0", "dC", 1)])
    sap.run_items(w.p["confirm"], w.p["label_table"], w.p["main_config"], nd, tmp_path / "items",
                  repo=tmp_path / "repo", allow_plan_mismatch=True)
    return tmp_path / "items"


def final(pid: str, word: str) -> dict[str, Any]:
    return {"k": f"pair|{pid}", "pair_id": pid, "label": None if word == "unjudged" else word == "answers",
            "source": "unjudged" if word == "unjudged" else "agree"}


def write_final(tmp: Path, items: Path, extra: tuple[dict[str, Any], ...] = ()) -> Path:
    """A synthetic final.jsonl: positive items answer unless at an edge; negatives n1 answers, n2 not,
    n3 unjudged; b2's item (kept_changed, edge after) gets no final row."""
    export = {r["chunk_id"]: r for r in asc.read_jsonl(items / "neighbour-export.jsonl")}
    rows = {}
    for s in asc.read_jsonl(items / "neighbour-seed.jsonl"):
        if s["record_id"] == "alias-O-b2-g0":
            continue
        pid = aj.pair_id(s["record_id"], s["chunk_id"])
        if s["group"] == "negative":
            word = {"alias-O-n1-g0": "answers", "alias-O-n2-g0": "not", "alias-O-n3-g0": "unjudged"}[s["record_id"]]
        else:
            word = "answers" if export[s["chunk_id"]]["metadata"]["edge"] == "none" else "not"
        rows[pid] = final(pid, word)
    return sfx.write_jsonl(tmp / "nbr-judge" / "final.jsonl", [{"k": "meta"}, *rows.values(), *extra])


def test_report_counts_with_and_without_edges(items_dir: Path, tmp_path: Path) -> None:
    fin = write_final(tmp_path, items_dir)
    rec = sap.run_report(items_dir, fin, tmp_path / "report", tmp_path / "public" / "neighbour-report.json")
    g = rec["by_group"]
    # split: w1 a1 (none), a3 (after), b5 (none), t1 (after); w2 a3 (after)
    s1 = g["split"]["context_window_1"]
    assert (s1["all"]["answers"], s1["all"]["not"]) == (2, 2)
    assert s1["without_edge"]["answers"] == 2 and s1["without_edge"]["not"] == 0
    assert s1["edge_only"]["not"] == 2 and s1["edge_only"]["answers"] == 0
    assert s1["all"]["answers_rate_unjudged_as_not"]["k"] == 2 and s1["all"]["answers_rate_unjudged_as_not"]["n"] == 4
    assert s1["all"]["answers_rate_unjudged_dropped"] == s1["all"]["answers_rate_unjudged_as_not"]  # no unjudged
    assert s1["without_edge"]["answers_rate_unjudged_dropped"]["rate"] == 1.0
    assert g["split"]["context_window_2"]["all"]["not"] == 1
    assert g["recovered"]["context_window_1"]["edge_only"]["not"] == 1  # a2: M is the first chunk
    # kept_changed: b1 (none), b2 (after, no final row), b3 (none)
    kc = g["kept_changed"]["context_window_1"]
    assert kc["all"]["no_final_row"] == 1 and kc["edge_only"]["no_final_row"] == 1
    assert kc["all"]["answers_rate_unjudged_as_not"]["n"] == 2 and kc["without_edge"]["answers"] == 2
    assert kc["all"]["answers_rate_unjudged_dropped"]["n"] == 2  # no final row: in neither denominator
    # the negative control: the false-answers rate under both unjudged policies (review A8 / Fable F5);
    # no edge split; the unjudged count is kept
    neg = g["negative"]["context_window_1"]
    assert set(neg) == {"all"}
    assert (neg["all"]["answers"], neg["all"]["not"], neg["all"]["unjudged"]) == (1, 1, 1)
    as_not, dropped = neg["all"]["answers_rate_unjudged_as_not"], neg["all"]["answers_rate_unjudged_dropped"]
    assert (as_not["k"], as_not["n"]) == (1, 3) and (dropped["k"], dropped["n"]) == (1, 2)
    assert dropped["rate"] == 0.5 > as_not["rate"]
    assert set(rec["rate_rule"]) == {"answers_rate_unjudged_as_not", "answers_rate_unjudged_dropped", "no_final_row"}
    assert rec["items"] == 13 and rec["items_without_final_row"] == 1 and rec["final_rows"] == 12
    # counts only
    pub = json.loads((tmp_path / "public" / "neighbour-report.json").read_text())
    export = {r["chunk_id"]: r for r in asc.read_jsonl(items_dir / "neighbour-export.jsonl")}
    seeds = asc.read_jsonl(items_dir / "neighbour-seed.jsonl")
    forbidden = set(export) | {s["record_id"] for s in seeds} | {s["control_of"] for s in seeds} | \
        {aj.pair_id(s["record_id"], s["chunk_id"]) for s in seeds} | \
        {r["metadata"]["max_overlap_chunk_id"] for r in export.values()}
    assert asc.public_problems(pub, forbidden, [r["content"] for r in export.values()]) == []
    assert sap.path_problems(pub) == [] and pub["inputs_sha256"]["neighbour_final"] == asc.sha256_file(fin)


def test_report_refuses_a_foreign_final_row(items_dir: Path, tmp_path: Path) -> None:
    fin = write_final(tmp_path, items_dir, (final(aj.pair_id("alias-O-b1-g0", "some-chunk"), "answers"),))
    with pytest.raises(SystemExit, match="1 pair.* no neighbour item"):
        sap.run_report(items_dir, fin, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_report_refuses_an_items_dir_that_does_not_match_its_summary(items_dir: Path, tmp_path: Path) -> None:
    fin = write_final(tmp_path, items_dir)
    p = items_dir / "neighbour-seed.jsonl"
    p.write_text(p.read_text() + "\n")
    with pytest.raises(SystemExit, match="not the ones its neighbour-items.json names"):
        sap.run_report(items_dir, fin, tmp_path / "report")


@pytest.mark.parametrize("cmd", ["items", "draw-negative", "wall-read", "wall-counts", "report"])
def test_cli_has_every_subcommand(cmd: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        sap.main([cmd, "--help"])
    assert e.value.code == 0 and "--out-dir" in capsys.readouterr().out
