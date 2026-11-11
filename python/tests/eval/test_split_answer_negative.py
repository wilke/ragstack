"""split-answer PLAN §3.2 (b), ``split_answer_prep.py draw-negative``: the seed recorded before the
draw (clean tree, committed bytes), the pool and its exclusions, the paper x arm quotas, the seeded
walk and its 3 x quota prefix counted after the (record, arm) / window filter (review nit c),
geometric eligibility (edge, one-chunk document, unresolved, collision with a positive item), the
gate on shared documents, a short cell refused, counts-only public output, and the draw's output
read by ``items``. Synthetic stage-A labels; the real ``chunk`` and ``map`` cut the fixture texts."""
from __future__ import annotations

import json
import subprocess
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

SEED = "test|split|negative|draw"


@pytest.fixture()
def words(monkeypatch: pytest.MonkeyPatch) -> sfx.WordCounter:
    from ragstack.ingestion import chunker_config

    wc = sfx.WordCounter()
    monkeypatch.setattr(chunker_config, "cached_token_counter", lambda *a, **k: wc)
    return wc


# ------------------------------------------------------------------ the seed (§3.2 (b)6)

def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def seed_repo(tmp: Path, seed: str = SEED, commit: bool = True) -> tuple[Path, Path]:
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "README").write_text("x\n")
    f = repo / "negative-draw-seed.json"
    f.write_text(json.dumps({"seed": seed}) + "\n")
    git(repo, "add", "README", *(["negative-draw-seed.json"] if commit else []))
    git(repo, "commit", "-q", "-m", "init")
    return repo, f


def test_seed_committed_on_a_clean_tree_is_read(tmp_path: Path) -> None:
    repo, f = seed_repo(tmp_path)
    assert sap.seed_file_problems(f, repo) == []
    assert sap.read_seed(f, repo) == SEED


def test_seed_not_committed_is_refused(tmp_path: Path) -> None:
    repo, f = seed_repo(tmp_path, commit=False)
    assert "no file tracked at HEAD has the seed file's bytes (commit it first)" in sap.seed_file_problems(f, repo)
    assert "the working tree is not clean" in sap.seed_file_problems(f, repo)  # the seed file is untracked
    with pytest.raises(SystemExit, match="not recorded before the draw"):
        sap.read_seed(f, repo)


def test_seed_changed_after_commit_or_a_dirty_tree_is_refused(tmp_path: Path) -> None:
    repo, f = seed_repo(tmp_path)
    (repo / "scratch.txt").write_text("untracked\n")
    assert sap.seed_file_problems(f, repo) == ["the working tree is not clean"]
    (repo / "scratch.txt").unlink()
    f.write_text(json.dumps({"seed": "another"}) + "\n")
    assert len(sap.seed_file_problems(f, repo)) == 2
    with pytest.raises(SystemExit, match="REFUSED"):
        sap.read_seed(f, repo)


def test_empty_seed_and_missing_seed_file_are_refused(tmp_path: Path) -> None:
    repo, f = seed_repo(tmp_path, seed=" ")
    with pytest.raises(SystemExit, match="no non-empty 'seed'"):
        sap.read_seed(f, repo)
    assert sap.seed_file_problems(tmp_path / "nope.json", repo) == ["the seed file does not exist"]


def test_draw_refuses_before_reading_anything_without_the_committed_seed(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    repo, f = seed_repo(tmp_path, commit=False)
    touched: list[str] = []
    monkeypatch.setattr(sap, "load_judge_cfg", lambda p: touched.append("cfg") or {})
    monkeypatch.setattr(sap.acc, "require_env", lambda: touched.append("env") or {})
    with pytest.raises(SystemExit, match="seed is not recorded before the draw"):
        sap.run_draw_negative(tmp_path / "none.json", f, tmp_path / "c", tmp_path / "s", tmp_path / "t", [],
                              tmp_path / "out", repo=repo)
    assert touched == [] and not (tmp_path / "out").exists()


# ------------------------------------------------------------------ the walk (§3.2 (b)2-5, nit c)

def final_row(rec: str, win: str, arm: str, *, label: bool = False, source: str = "agree", stage: str = "A"
              ) -> dict[str, Any]:
    return {"k": f"pair|{aj.pair_id(rec, win)}", "pair_id": aj.pair_id(rec, win), "record_id": rec, "chunk_id": win,
            "arm": arm, "stage": stage, "source": source, "label": None if source == "unjudged" else label}


def reg_row(f: dict[str, Any], paper: str, same: bool) -> dict[str, Any]:
    return {"pair_id": f["pair_id"], "record_id": f["record_id"], "chunk_id": f["chunk_id"], "arm": f["arm"],
            "c_paper": paper, "q_paper": paper if same else "PMC99", "stage": f["stage"]}


def test_walk_pool_quotas_and_the_prefix_counted_after_the_filter() -> None:
    final, reg = [], {}
    # same:base: 12 pairs on 6 records (two each) and 10 windows; other:neighbour: 4 pairs; noise rows
    for i in range(12):
        f = final_row(f"r{i // 2}", f"w{i % 10}", "base")
        final.append(f)
        reg[f["pair_id"]] = reg_row(f, "P1", True)
    for i in range(4):
        f = final_row(f"r{i}", f"v{i}", "neighbour")
        final.append(f)
        reg[f["pair_id"]] = reg_row(f, "P1", False)
    for f in (final_row("x1", "y1", "base", label=True), final_row("x2", "y2", "base", source="unjudged"),
              final_row("x3", "y3", "base", stage="B"), final_row("x4", "y4", "base", source="tiebreak")):
        final.append(f)
        reg[f["pair_id"]] = reg_row(f, "P1", True)
    excl_pair = final[0]["pair_id"]  # r0|w0, by pair id
    cands, quotas, c = sap.negative_walk(final, reg, exclude_pairs={excl_pair}, exclude_windows={"w9"}, seed=SEED,
                                         quotas_by_paper={"same": 2, "other": 2})
    # pool: the agreed not rows only, minus the excluded pair and the pairs on window w9 (r4|w9)
    assert c["agreed_not"]["same:base"] == 12 and c["agreed_not"]["other:neighbour"] == 4
    assert c["in_sample_excluded"]["same:base"] == 1 and c["shared_window_excluded"]["same:base"] == 1
    assert c["pool"]["same:base"] == 10 and c["pool_records"]["same:base"] == 6
    # quotas: drift_allocation over the arm cells' records; an empty arm's share goes to the others
    assert quotas == {"same:base": 2, "same:neighbour": 0, "same:random": 0, "other:base": 0,
                      "other:neighbour": 2, "other:random": 0}
    # the prefix: 3 x quota candidates, counted after the filter (each record has two pool pairs)
    by = {k: [f for f in cands if f["cell"] == k] for k in quotas}
    assert len(by["same:base"]) == 6 and len(by["other:neighbour"]) == 4  # capped by the pool
    assert c["prefix_targets"]["same:base"] == 6 and c["prefix_reached"]["same:base"]
    assert c["walked"]["same:base"] > 6 and sum(c["walk_skips"]["same:base"].values()) == c["walked"]["same:base"] - 6
    assert not c["prefix_reached"]["other:neighbour"]
    # one per (record, arm) and per window across all cells; each cell in seeded order
    assert len({(f["record_id"], f["arm"]) for f in cands}) == len(cands) == len({f["chunk_id"] for f in cands})
    for k, lst in by.items():
        p, a = k.split(":")
        keys = [asc.seeded_key(SEED, p, a, f["pair_id"]) for f in lst]
        assert keys == sorted(keys)
    # every pool pair the walk passed over before its last candidate was claimed by an earlier one
    pool_sb = sorted((f for f in final if f["arm"] == "base" and f["stage"] == "A" and f["source"] == "agree"
                      and not f["label"] and f["pair_id"] != excl_pair and f["chunk_id"] != "w9"),
                     key=lambda f: (asc.seeded_key(SEED, "same", "base", f["pair_id"]), f["pair_id"]))
    taken = {f["pair_id"] for f in by["same:base"]}
    last = max(i for i, f in enumerate(pool_sb) if f["pair_id"] in taken)
    for i, f in enumerate(pool_sb[:last]):
        if f["pair_id"] not in taken:
            earlier = [g for g in pool_sb[:i] if g["pair_id"] in taken]
            assert any(g["record_id"] == f["record_id"] or g["chunk_id"] == f["chunk_id"] for g in earlier)
    # the seed changes the order (and so which windows collide), not the rule
    other, _q, _c = sap.negative_walk(final, reg, exclude_pairs={excl_pair}, exclude_windows={"w9"}, seed="x",
                                      quotas_by_paper={"same": 2, "other": 2})
    assert [f["pair_id"] for f in other] != [f["pair_id"] for f in cands]
    assert len({(f["record_id"], f["arm"]) for f in other}) == len(other) == len({f["chunk_id"] for f in other})


def test_walk_refuses_a_final_row_without_its_registry_row_or_with_another_arm() -> None:
    f = final_row("r", "w", "base")
    with pytest.raises(SystemExit, match="no matching stage-A registry row"):
        sap.negative_walk([f], {}, exclude_pairs=(), exclude_windows=(), seed=SEED)
    with pytest.raises(SystemExit, match="arm is not its registry row's"):
        sap.negative_walk([f], {f["pair_id"]: {**reg_row(f, "P", True), "arm": "random"}}, exclude_pairs=(),
                          exclude_windows=(), seed=SEED)


# ------------------------------------------------------------------ draw-negative end to end

#: (tag, record, doc, start, end, arm, paper same?, expected outcome)
NOTS = [
    ("nb1", "alias-N-1-g0", "dB", 3100, 3500, "base", True, "drawn"),          # M=B1, both sides
    ("nb2", "alias-N-2-g0", "dB", 100, 900, "base", True, "edge"),             # M=B0
    ("nb3", "alias-N-3-g0", "dE", 0, 500, "base", True, "one_chunk_document"),
    ("nn1", "alias-N-5-g0", "dX", 0, 40, "neighbour", True, "unresolved"),     # no record text
    ("nn2", "alias-O-b1-g0", "dB", 3200, 3600, "neighbour", True, "collision_positive"),  # b1's (record, M=B1)
    ("nn3", "alias-N-6-g0", "dA", 2500, 3000, "neighbour", True, "drawn"),     # M=A1: a document both runs chunk
    ("no1", "alias-N-7-g0", "dC", 3500, 4000, "base", False, "drawn"),         # M=C1
    ("no2", "alias-N-1-g0", "dC", 4100, 4500, "base", False, "record_arm_taken"),  # nb1 holds (N-1, base)
    ("nx1", "alias-N-8-g0", "dB", 6500, 7000, "base", True, "in_sample"),      # in the control sample
    ("nx2", "alias-N-9-g0", "dB", 6500, 7000, "base", True, "shared_window"),  # its window is nx1's
]


def neg_window(tag: str, doc: str, s: int, e: int) -> dict[str, Any]:
    if doc == "dX":
        return {"chunk_id": f"win-{tag}-0000", "doc_id": "dX", "content": "No record text resolves for this one.",
                "start_char": 0, "end_char": 37, "metadata": {"source_path": "PMC9", "title": "Title of PMC9"}}
    return sfx.window(doc, s, e, tag)


class Draw:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch, quotas: dict[str, int] | None = None,
                 nots: list[tuple[Any, ...]] | None = None) -> None:
        self.nots = NOTS if nots is None else nots
        wins = {t: neg_window(t, d, s, e) for t, _r, d, s, e, *_x in self.nots}
        wins["nx2"] = wins["nx1"]
        self.world = sfx.World(tmp / "w", extra_windows=[w for t, w in wins.items() if t != "nx2"])
        self.final, self.reg = [], {}
        for t, r, d, _s, _e, arm, same, _o in self.nots:
            f = final_row(r, wins[t]["chunk_id"], arm)
            self.final.append(f)
            self.reg[f["pair_id"]] = reg_row(f, sfx.DOCS.get(d, ("PMC9", ""))[0], same)
        self.pid = {t: aj.pair_id(r, wins[t]["chunk_id"]) for t, r, *_x in self.nots}
        nx1 = next(f for f in self.final if f["pair_id"] == self.pid["nx1"])
        self.control = sfx.write_jsonl(tmp / "control" / "sample.jsonl", [
            {"pair_id": nx1["pair_id"], "record_id": nx1["record_id"], "chunk_id": nx1["chunk_id"],
             "stratum": "not", "arm": "base", "in_spot_sheet": False}])
        self.repo, self.seed = seed_repo(tmp)
        monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, **self.world.pinned(),
                                                   "control_sample": asc.sha256_file(self.control)})
        monkeypatch.setattr(sap, "NEGATIVE_QUOTAS", quotas or {"same": 2, "other": 1})
        monkeypatch.setattr(sap, "load_judge_cfg", lambda p: {"chunk_exports": self.world.export})
        monkeypatch.setattr(asp, "committed_stage_a", lambda cfg: {"commit_sha256": sap.STAGE_A_COMMIT_SHA256,
                                                                   "final": self.final, "pairs": list(self.reg.values())})
        self.tmp = tmp

    def run(self, out: str = "neg", public: bool = True) -> dict[str, Any]:
        return sap.run_draw_negative(self.world.p["main_config"], self.seed, self.world.p["confirm"],
                                     self.world.sample, self.control, [self.world.p["texts"]], self.tmp / out,
                                     self.tmp / "public" / "negative-draw.json" if public else None, repo=self.repo)


def test_draw_negative_end_to_end(words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = Draw(tmp_path, monkeypatch)
    rec = d.run()
    out = tmp_path / "neg"
    drawn = asc.read_jsonl(out / "sample-negative.jsonl")
    assert {r["pair_id"] for r in drawn} == {d.pid[t] for t, *_x, o in NOTS if o == "drawn"}
    assert rec["quotas"] == {"same:base": 1, "same:neighbour": 1, "same:random": 0, "other:base": 1,
                             "other:neighbour": 0, "other:random": 0}
    assert rec["drawn"] == rec["quotas"] and rec["n_drawn"] == 3
    ex = rec["exclusions"]
    assert ex["same:base"] == {"unresolved": 0, "one_chunk_document": 1, "edge": 1, "trim": 0,
                               "collision_positive": 0, "collision_negative": 0, "eligible_beyond_quota": 0}
    assert ex["same:neighbour"]["unresolved"] == 1 and ex["same:neighbour"]["collision_positive"] == 1
    assert rec["walk_skips"]["other:base"] == {"record_arm_taken": 1}
    assert rec["in_sample_excluded"]["same:base"] == 1 and rec["shared_window_excluded"]["same:base"] == 1
    assert rec["shared_documents_checked"] == 2  # dA and dB are in both chunk runs
    # each drawn M has a neighbour on both sides of the draw's own chunk run
    ndocs = sap.acc.control_docs(out / "chunks" / "chunks-control.jsonl")
    for r in drawn:
        ids = [x["chunk_id"] for x in ndocs[r["doc_id"]]]
        assert 0 < ids.index(r["max_overlap_chunk_id"]) < len(ids) - 1
        assert r["paper"] == ("other" if r["pair_id"] == d.pid["no1"] else "same")
    # the candidate sample is the control row shape, stratum not
    samp = asc.read_jsonl(out / "chunks" / "sample.jsonl")
    assert {tuple(sorted(r)) for r in samp} == {("arm", "chunk_id", "in_spot_sheet", "pair_id", "record_id",
                                                 "stratum")}
    assert {r["stratum"] for r in samp} == {"not"} and len(samp) == 7
    # counts only, and the file items reads
    pub = json.loads((tmp_path / "public" / "negative-draw.json").read_text())
    forbidden = {f["pair_id"] for f in d.final} | {f["record_id"] for f in d.final} | \
        {f["chunk_id"] for f in d.final} | {x["chunk_id"] for rs in ndocs.values() for x in rs} | set(ndocs)
    assert asc.public_problems(pub, forbidden, [t for _p, t in sfx.DOCS.values()]) == []
    assert sap.path_problems(pub) == []
    assert pub["seed_file_sha256"] == asc.sha256_file(d.seed) and pub["seed_is_the_proposed_one"] is False
    assert pub["passage_tokens"]["n"] == 3
    items = sap.run_items(d.world.p["confirm"], d.world.p["label_table"], d.world.p["main_config"], out,
                          tmp_path / "items")
    assert items["items"]["negative"] == {"context_window_1": 3} and items["items_per_set"]["negative"] == 3


def test_draw_negative_excludes_a_second_candidate_on_one_record_and_m(
        words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review A1: nc1 is nb1's record in another arm (so the walk's (record, arm) filter passes it)
    with another window on the same M = B1; it would merge with nb1 into one registry pair."""
    nots = [*NOTS, ("nc1", "alias-N-1-g0", "dB", 3300, 3700, "neighbour", False, "collision_negative"),
            ("nc2", "alias-N-10-g0", "dC", 5000, 5500, "neighbour", False, "drawn")]
    d = Draw(tmp_path, monkeypatch, quotas={"same": 2, "other": 2}, nots=nots)
    rec = d.run()
    drawn = asc.read_jsonl(tmp_path / "neg" / "sample-negative.jsonl")
    assert {r["pair_id"] for r in drawn} == {d.pid[t] for t, *_x, o in nots if o == "drawn"}
    assert rec["candidates"]["other:neighbour"] == 2 and rec["drawn"]["other:neighbour"] == 1
    assert rec["exclusions"]["other:neighbour"]["collision_negative"] == 1
    assert sum(v["collision_negative"] for v in rec["exclusions"].values()) == 1
    assert len({(r["record_id"], r["max_overlap_chunk_id"]) for r in drawn}) == len(drawn) == 4


def test_negative_eligibility_collision_is_claimed_by_eligible_candidates_only() -> None:
    rows = sfx.chunk_rows("dB")
    ndocs = {"dB": rows}
    seeds = {"p0": {"doc_id": "dB", "chunk_id": rows[0]["chunk_id"]},   # edge: claims nothing
             "p1": {"doc_id": "dB", "chunk_id": rows[0]["chunk_id"]},   # edge again, not a collision
             "p2": {"doc_id": "dB", "chunk_id": rows[1]["chunk_id"]},   # eligible: claims (r, B1)
             "p3": {"doc_id": "dB", "chunk_id": rows[1]["chunk_id"]},   # (r, B1) again
             "p4": {"doc_id": "dB", "chunk_id": rows[1]["chunk_id"]}}   # another record: eligible
    cands = [{"pair_id": "p0", "record_id": "r"}, {"pair_id": "p1", "record_id": "r"},
             {"pair_id": "p2", "record_id": "r"}, {"pair_id": "p3", "record_id": "r"},
             {"pair_id": "p4", "record_id": "s"}]
    got = [why for why, _q in sap.negative_eligibility(cands, seeds, ndocs, set(), budget=8000)]
    assert got == ["edge", "edge", None, "collision_negative", None]


def test_draw_negative_refuses_a_short_cell(words: sfx.WordCounter, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    d = Draw(tmp_path, monkeypatch, quotas={"same": 2, "other": 2})  # other:base has one eligible candidate
    with pytest.raises(SystemExit, match=r"ran short.*other:base 1/2"):
        d.run()
    assert not (tmp_path / "neg" / "sample-negative.jsonl").exists()


def test_draw_negative_refuses_another_stage_a_commit_or_an_unpinned_sample(
        words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = Draw(tmp_path, monkeypatch)
    monkeypatch.setattr(asp, "committed_stage_a", lambda cfg: {"commit_sha256": "0" * 64, "final": [], "pairs": []})
    with pytest.raises(SystemExit, match="stage-A commit is not the one"):
        d.run()
    monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, "control_sample": "1" * 64})
    with pytest.raises(SystemExit, match="control_sample .* is not the file"):
        d.run()
    assert not (tmp_path / "neg").exists()


def test_shared_document_gate() -> None:
    a = {"d": [{"chunk_id": "c0", "start_char": 0, "end_char": 5}, {"chunk_id": "c1", "start_char": 5,
                                                                   "end_char": 9}]}
    assert sap.shared_doc_problems(a, a) == 0 and sap.shared_doc_problems(a, {"e": []}) == 0
    b = {"d": [{"chunk_id": "c0", "start_char": 0, "end_char": 6}, {"chunk_id": "c1", "start_char": 6,
                                                                   "end_char": 9}]}
    assert sap.shared_doc_problems(a, b) == 1
