"""split-answer PLAN §3.2 (c), ``split_answer_prep.py wall-read`` / ``wall-counts``: the split windows a
segment edge crosses (``split_feasibility.py``'s rule), the sheet with ``⟦SECTION WALL⟧`` marks and no
ids, the private key and the empty answer file, the refusal once the neighbour registry holds labels,
and the counts-only result. Synthetic world; no store, no network, no model."""
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
import alias_study_spot as asp  # noqa: E402
import split_answer_fixture as sfx  # noqa: E402
import split_answer_prep as sap  # noqa: E402

pytest.importorskip("nltk")
pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")

WALL_TAGS = ("a1", "a3", "t1")  # the split windows a segment edge crosses (b5 crosses none)


@pytest.fixture()
def words(monkeypatch: pytest.MonkeyPatch) -> sfx.WordCounter:
    from ragstack.ingestion import chunker_config

    wc = sfx.WordCounter()
    monkeypatch.setattr(chunker_config, "cached_token_counter", lambda *a, **k: wc)
    return wc


class Wall:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = w = sfx.World(tmp / "w")
        self.tmp = tmp
        monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, **w.pinned()})
        monkeypatch.setattr(sap, "WALL_N", len(WALL_TAGS))
        self.reg = {}
        for x in w.positive:
            win = w.windows[x.tag]["chunk_id"]
            pid = aj.pair_id(x.rid, win)
            self.reg[pid] = {"pair_id": pid, "record_id": x.rid, "chunk_id": win,
                             "question": f"What does the study of {x.tag} report about the measured effect?",
                             "term": f"term-{x.tag}", "stage": "A"}
        monkeypatch.setattr(sap, "load_judge_cfg", lambda p: {"chunk_exports": w.export})
        monkeypatch.setattr(asp, "committed_stage_a", lambda cfg: {"commit_sha256": sap.STAGE_A_COMMIT_SHA256,
                                                                   "final": [], "pairs": list(self.reg.values())})
        main = json.loads(w.p["main_config"].read_text())
        self.nbr_out = tmp / "nbr-judge"
        self.nbr_cfg = tmp / "neighbour-config.json"
        self.nbr_cfg.write_text(json.dumps({**main, "out_dir": str(self.nbr_out), "chunk_exports": {
            **main["chunk_exports"], aj.CONTROL_EXPORT: str(tmp / "items" / "neighbour-export.jsonl")}}))

    def read(self, out: str = "wall") -> dict[str, Any]:
        w = self.world
        return sap.run_wall_read(w.p["confirm"], w.p["label_table"], w.p["main_config"], self.nbr_cfg, self.tmp / out)


def test_segment_edges_and_marks_follow_the_feasibility_rule(words: sfx.WordCounter) -> None:
    assert sap.segment_edges("dA", sfx.DOC_A) == {2371, 7304}
    assert sap.segment_edges("dT", sfx.DOC_T) == {1345}
    assert sap.segment_edges("dB", sfx.DOC_B) == set()
    t = "abcdefghij"
    assert sap.mark_walls(t, 2, 8, [0, 4, 8, 6]) == f"cd{sap.WALL_MARK}ef{sap.WALL_MARK}gh"
    assert sap.mark_walls(t, 2, 8, [2, 8]) == "cdefgh"  # an edge at the window's own bound is not inside it


def test_wall_windows_are_the_split_windows_an_edge_crosses(words: sfx.WordCounter, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    w = Wall(tmp_path, monkeypatch).world
    pairs, _c, texts, _s = sap.confirm_pairs(w.p["confirm"], w.p["label_table"])
    found = sap.wall_windows(pairs, texts)
    assert sorted(p.window_id for p, _e in found) == sorted(w.windows[t]["chunk_id"] for t in WALL_TAGS)
    assert {p.window_id: e for p, e in found}[w.windows["a3"]["chunk_id"]] == [7304]


def test_wall_read_writes_an_id_free_sheet_a_key_and_an_empty_answer_file(words: sfx.WordCounter, tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    wl = Wall(tmp_path, monkeypatch)
    res = wl.read()
    out = tmp_path / "wall"
    sheet = json.loads((out / "wall-sheet.json").read_text())
    key = json.loads((out / "wall-key.json").read_text())
    ans = json.loads((out / "wall-answers.json").read_text())
    assert res["items"] == 3 and res["edges_inside"] == 3
    assert [it["item"] for it in sheet["items"]] == [1, 2, 3] == [k["item"] for k in key["items"]]
    # seeded order, by the stage-A pair id
    ids = [k["control_of"] for k in key["items"]]
    assert ids == sorted(ids, key=lambda i: asc.seeded_key(sap.WALL_ORDER_SEED, i))
    tag_of = {aj.pair_id(x.rid, wl.world.windows[x.tag]["chunk_id"]): x for x in wl.world.positive}
    for it, k in zip(sheet["items"], key["items"], strict=True):
        x = tag_of[k["control_of"]]
        text = sfx.DOCS[x.doc][1]
        assert it["window"].count(sap.WALL_MARK) == k["edges_inside"] == 1
        assert it["window"].replace(sap.WALL_MARK, "") == text[x.s:x.e]
        assert it["term"] == f"term-{x.tag}" and set(it) == {"item", "question", "term", "window"}
    # the sheet names no record, chunk, pair or document
    private_ids = {s for k in key["items"] for s in (k["control_of"], k["record_id"], k["window_id"])}
    assert not any(i in s for s in asc.string_values(sheet) for i in private_ids)
    assert key["sheet_sha256"] == asc.sha256_file(out / "wall-sheet.json")
    assert ans["answers"] == {"1": None, "2": None, "3": None} and ans["reader_role"] is None
    assert ans["key_sha256"] == asc.sha256_file(out / "wall-key.json")


def test_wall_read_refuses_another_count(words: sfx.WordCounter, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    wl = Wall(tmp_path, monkeypatch)
    monkeypatch.setattr(sap, "WALL_N", 10)
    with pytest.raises(SystemExit, match="3 split windows cross a segment edge; .* exactly 10"):
        wl.read()
    assert not (tmp_path / "wall").exists()


def test_wall_read_refuses_once_the_neighbour_registry_exists(words: sfx.WordCounter, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    wl = Wall(tmp_path, monkeypatch)
    wl.nbr_out.mkdir()
    for name in ("pairs.jsonl", "final.jsonl"):
        (wl.nbr_out / name).write_text("")
        with pytest.raises(SystemExit, match=f"already holds {name}"):
            wl.read()
        (wl.nbr_out / name).unlink()
    raw = json.loads(wl.nbr_cfg.read_text())
    wl.nbr_cfg.write_text(json.dumps({**raw, "batch_size": 7}))
    with pytest.raises(SystemExit, match="differs from the main judge config"):
        wl.read()
    assert not (tmp_path / "wall").exists()


def fill(path: Path, answers: dict[str, str | None], role: str | None = "owner", date: str | None = "2026-11-12"
         ) -> None:
    a = json.loads(path.read_text())
    path.write_text(json.dumps({**a, "answers": answers, "reader_role": role, "date": date}))


def test_wall_counts_counts_only(words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Wall(tmp_path, monkeypatch).read()
    out = tmp_path / "wall"
    fill(out / "wall-answers.json", {"1": "needs_both", "2": "one_side", "3": "needs_both"}, "reviewer-agent")
    rec = sap.run_wall_counts(out / "wall-sheet.json", out / "wall-key.json", out / "wall-answers.json",
                              tmp_path / "counts", tmp_path / "public" / "wall-read-counts.json")
    assert rec["counts"] == {"needs_both": 2, "one_side": 1, "unclear": 0} and rec["n"] == 3
    assert rec["reader_role"] == "reviewer-agent" and rec["date"] == "2026-11-12"
    assert rec["sha256"] == {"sheet": asc.sha256_file(out / "wall-sheet.json"),
                             "key": asc.sha256_file(out / "wall-key.json"),
                             "answers": asc.sha256_file(out / "wall-answers.json")}
    pub = json.loads((tmp_path / "public" / "wall-read-counts.json").read_text())
    assert pub == rec and sap.path_problems(pub) == []
    key = json.loads((out / "wall-key.json").read_text())
    sheet = json.loads((out / "wall-sheet.json").read_text())
    forbidden = {str(k[f]) for k in key["items"] for f in ("control_of", "record_id", "window_id")}
    assert asc.public_problems(pub, forbidden, [it["window"] for it in sheet["items"]]) == []
    assert not any(str(k["start_char"]) == s for k in key["items"] for s in asc.string_values(pub))


@pytest.mark.parametrize(("answers", "role", "date", "msg"), [
    ({"1": "needs_both", "2": None, "3": "unclear"}, "owner", "2026-11-12", "unanswered items remain"),
    ({"1": "needs_both", "2": "both", "3": "unclear"}, "owner", "2026-11-12", "must be one of"),
    ({"1": "needs_both", "2": "one_side"}, "owner", "2026-11-12", "keyed 1..3"),
    ({"1": "needs_both", "2": "one_side", "3": "unclear"}, "judge", "2026-11-12", "reader_role"),
    ({"1": "needs_both", "2": "one_side", "3": "unclear"}, "owner", "12/11/2026", "ISO date"),
])
def test_wall_counts_refuses_an_incomplete_or_malformed_answer_file(
        words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: dict[str, str | None],
        role: str, date: str, msg: str) -> None:
    Wall(tmp_path, monkeypatch).read()
    out = tmp_path / "wall"
    fill(out / "wall-answers.json", answers, role, date)
    with pytest.raises(SystemExit, match=msg):
        sap.run_wall_counts(out / "wall-sheet.json", out / "wall-key.json", out / "wall-answers.json",
                            tmp_path / "counts")
    assert not (tmp_path / "counts").exists()


def test_wall_counts_refuses_a_changed_sheet_and_files_inside_the_repository(
        words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Wall(tmp_path, monkeypatch).read()
    out = tmp_path / "wall"
    fill(out / "wall-answers.json", {"1": "needs_both", "2": "one_side", "3": "unclear"})
    (out / "wall-sheet.json").write_text((out / "wall-sheet.json").read_text() + " ")
    with pytest.raises(SystemExit, match="the key is not the sheet's"):
        sap.run_wall_counts(out / "wall-sheet.json", out / "wall-key.json", out / "wall-answers.json",
                            tmp_path / "counts")
    monkeypatch.setattr(asp, "REPO", tmp_path)
    with pytest.raises(SystemExit, match="inside the repository"):
        sap.run_wall_counts(out / "wall-sheet.json", out / "wall-key.json", out / "wall-answers.json",
                            tmp_path / "counts")
