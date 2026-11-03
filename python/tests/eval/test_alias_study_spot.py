"""Alias study spot-check sheets (prereg §13.4, §13.5, §5.5): the seeded draws, the stratum and
arm allocation with its remainder and shortfall rules, blindness, ingest into the statistics'
formats, and every refusal."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import alias_study_run as ar  # noqa: E402
import alias_study_spot as spot  # noqa: E402
import alias_study_stats as ast  # noqa: E402

ARMS = asc.ARM_GROUPS


# ------------------------------------------------------------------ records (§13.4)

@pytest.fixture()
def rec_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> fx.World:
    (tmp_path / "in").mkdir()
    w = fx.World(tmp_path / "in", n_papers=20, set_="O")
    w.records = [{**r, "answer": f"the answer about {r['primary']}", "quote": f"the {r['alias']} value was given"}
                 for r in w.records]
    fx.write_jsonl(w.records_path, w.records)
    bal, _ = asc.split_records(w.records)
    assert len(bal) == 60
    monkeypatch.setattr(asc, "ACCEPTED_SHA256", asc.sha256_file(w.records_path))
    monkeypatch.setattr(asc, "BALANCED_SHA256", asc.balanced_ids_sha(r["record_id"] for r in bal))
    return w


def draw_rec(w: fx.World, out: Path) -> dict[str, Any]:
    return spot.draw_records(w.records_path, [w.base_export], out)


def test_record_draw_is_seeded_and_the_sheet_sha_is_its_bytes(rec_world: fx.World, tmp_path: Path) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    b = draw_rec(rec_world, tmp_path / "b")
    sa, sb = Path(a["sheet"]).read_bytes(), Path(b["sheet"]).read_bytes()
    assert sa == sb and a["sheet_sha256"] == asc.sha256_file(a["sheet"]) and a["n"] == 40
    key = json.loads(Path(a["key"]).read_text())
    bal = [r["record_id"] for r in asc.split_records(rec_world.records)[0]]
    want = sorted(bal, key=lambda i: asc.sha_text(f"261101|alias|spot|records|{i}"))[:40]
    assert list(key["items"].values()) == want and key["sheet_sha256"] == a["sheet_sha256"]
    assert draw_rec(rec_world, tmp_path / "a")["sheet_sha256"] == a["sheet_sha256"]   # a re-draw is identical


def test_record_sheet_is_blind_to_kind_arm_and_ids(rec_world: fx.World, tmp_path: Path) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    sheet = json.loads(Path(a["sheet"]).read_text())
    assert all(set(it) == set(spot.RECORD_ITEM_FIELDS)
               for it in sheet["items"])
    text = Path(a["sheet"]).read_text()
    for r in rec_world.records:
        assert r["record_id"] not in text and f'"{r["paper"]}"' not in text and r["gold_chunks"][0] not in text
    for k in ('"kind": "G"', '"kind": "C"', '"stratum"', '"arm"', '"record_id"', '"paper"', '"verifier"'):
        assert k not in text
    # an item is the same whatever the record's kind
    chunks = asc.read_chunk_exports([rec_world.base_export])
    r = asc.split_records(rec_world.records)[0][0]
    flip = {**r, "kind": "C" if r["kind"] == "G" else "G", "stratum": "x", "verifier": "codex"}
    assert spot.record_item(1, r, chunks) == spot.record_item(1, flip, chunks)


def test_record_draw_refusals(rec_world: fx.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="inside the repository"):
        draw_rec(rec_world, _HERE / "spot-out")
    with pytest.raises(SystemExit, match="draws 40"):
        spot.draw_record_ids([f"r{i}" for i in range(39)])
    a = draw_rec(rec_world, tmp_path / "a")
    Path(a["sheet"]).write_text("{}\n")
    with pytest.raises(SystemExit, match="drawn once"):
        draw_rec(rec_world, tmp_path / "a")
    chunks = asc.read_chunk_exports([rec_world.base_export])
    r = asc.split_records(rec_world.records)[0][0]
    with pytest.raises(SystemExit, match="no 'quote'"):
        spot.record_item(1, {**r, "quote": " "}, chunks)
    with pytest.raises(SystemExit, match="gold chunk is in no chunk export"):
        spot.record_item(1, {**r, "gold_chunks": ["nope"]}, chunks)
    monkeypatch.setattr(asc, "BALANCED_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="record set is not the frozen one"):
        draw_rec(rec_world, tmp_path / "c")


def fill(sheet_path: Path, out: Path, labels: list[str] | None = None, **edit: Any) -> Path:
    s = json.loads(sheet_path.read_text())
    vals = spot.LABELS[s["kind"]]
    first = list(vals)
    for i, it in enumerate(s["items"]):
        it["label"] = labels[i] if labels else first[i % 3 == 2]
        it["note"] = "checked" if i == 0 else ""
    for v in edit.values():
        v(s)
    out.write_text(json.dumps(s, indent=4))          # an editor's own formatting is accepted
    return out


def test_ingest_records_writes_what_the_statistics_read(rec_world: fx.World, tmp_path: Path) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    f = fill(Path(a["sheet"]), tmp_path / "filled.json")
    res = spot.ingest(Path(a["key"]), f, tmp_path / "records.json")
    labels = [it["label"] for it in json.loads(f.read_text())["items"]]
    assert res == {"valid": labels.count("valid"), "n": 40, "sheet_sha256": a["sheet_sha256"]}
    assert json.loads((tmp_path / "records.json").read_text()) == res
    got = ast.spot_flags({"spot_checks": {"records": str(tmp_path / "records.json")}}, None)  # type: ignore[arg-type]
    assert got["record_validity"]["n"] == 40 and got["record_validity"]["sheet_sha256"] == a["sheet_sha256"]


@pytest.mark.parametrize("edit,match", [
    (lambda s: s["items"][3].update(question=s["items"][3]["question"] + " "), "not the drawn sheet"),
    (lambda s: s["items"].reverse(), "not the drawn sheet"),
    (lambda s: s["items"].pop(), "not the drawn sheet"),
    (lambda s: s["items"][0].update(denotes=True), "only label and note"),
    (lambda s: s["items"][5].update(label="maybe"), "carry no label"),
    (lambda s: s["items"][5].update(label=None), "carry no label"),
    (lambda s: s.update(schema=spot.SHEET_SCHEMA["judge"]), "not a records sheet"),
])
def test_ingest_records_refusals(rec_world: fx.World, tmp_path: Path, edit: Any, match: str) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    f = fill(Path(a["sheet"]), tmp_path / "filled.json", e=edit)
    with pytest.raises(SystemExit, match=match):
        spot.ingest(Path(a["key"]), f, tmp_path / "records.json")
    assert not (tmp_path / "records.json").exists()


def test_ingest_refuses_a_key_whose_items_differ(rec_world: fx.World, tmp_path: Path) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    key = json.loads(Path(a["key"]).read_text())
    items = list(key["items"].items())
    items[0], items[1] = items[1], items[0]
    key["items"] = dict(items)
    (tmp_path / "k.json").write_text(json.dumps(key))
    f = fill(Path(a["sheet"]), tmp_path / "filled.json")
    with pytest.raises(SystemExit, match="item ids are not the key's"):
        spot.ingest(tmp_path / "k.json", f, tmp_path / "records.json")


def test_ingest_records_enforces_forty(rec_world: fx.World, tmp_path: Path) -> None:
    a = draw_rec(rec_world, tmp_path / "a")
    sheet = json.loads(Path(a["sheet"]).read_text())
    key = json.loads(Path(a["key"]).read_text())
    sheet["items"].pop()
    sheet["n"] = 39
    key["items"].pop(sheet_last := "r40")
    assert sheet_last not in key["items"]
    key["sheet_sha256"] = asc.sha_text(spot.canonical(sheet).decode())
    Path(a["sheet"]).write_text(spot.canonical(sheet).decode())
    Path(a["key"]).write_text(json.dumps(key))
    f = fill(Path(a["sheet"]), tmp_path / "filled.json")
    with pytest.raises(SystemExit, match="n = 40"):
        spot.ingest(Path(a["key"]), f, tmp_path / "records.json")
    with pytest.raises(SystemExit, match="inside the repository"):
        spot.ingest(Path(a["key"]), f, _HERE / "records.json")


# ------------------------------------------------------------------ the allocation (§13.5, R15)

def big(n: int = 1000) -> dict[str, int]:
    return dict.fromkeys(ARMS, n)


def test_allocation_nominal_split_and_remainder_order() -> None:
    a = spot.allocate({s: big() for s in spot.SPOT_STRATA})
    assert a == {"answers": {"base": 17, "neighbour": 17, "random": 16},
                 "not": {"base": 11, "neighbour": 11, "random": 11},
                 "tiebroken": {"base": 6, "neighbour": 6, "random": 5}}
    assert sum(spot.NOMINAL.values()) == spot.SPOT_JUDGE_N == 100


def test_allocation_short_cell_goes_to_the_other_arms_of_its_stratum() -> None:
    a = spot.allocate({"answers": big(), "not": big(), "tiebroken": {"base": 50, "neighbour": 50, "random": 2}})
    assert a["tiebroken"] == {"base": 8, "neighbour": 7, "random": 2} and a["answers"]["base"] == 17
    a = spot.allocate({"answers": {"base": 1000, "neighbour": 3, "random": 1000}, "not": big(), "tiebroken": big()})
    assert a["answers"] == {"base": 24, "neighbour": 3, "random": 23}


def test_allocation_short_stratum_goes_to_answers_then_not() -> None:
    a = spot.allocate({"answers": big(), "not": big(), "tiebroken": {"base": 3, "neighbour": 2, "random": 1}})
    assert a["tiebroken"] == {"base": 3, "neighbour": 2, "random": 1}
    assert sum(a["answers"].values()) == 61 and a["answers"] == {"base": 21, "neighbour": 20, "random": 20}
    assert sum(a["not"].values()) == 33
    # answers cannot take tiebroken's shortfall: the rest goes to not
    a = spot.allocate({"answers": dict.fromkeys(ARMS, 18), "not": big(), "tiebroken": dict.fromkeys(ARMS, 1)})
    assert {s: sum(v.values()) for s, v in a.items()} == {"answers": 54, "not": 43, "tiebroken": 3}
    # not's shortfall goes to answers; answers's to not
    assert spot.stratum_totals({"answers": 1000, "not": 20, "tiebroken": 1000}) == {"answers": 63, "not": 20,
                                                                                   "tiebroken": 17}
    assert spot.stratum_totals({"answers": 30, "not": 1000, "tiebroken": 1000}) == {"answers": 30, "not": 53,
                                                                                   "tiebroken": 17}
    # fewer than 100 pairs in all: every pair is taken
    assert spot.stratum_totals({"answers": 10, "not": 20, "tiebroken": 5}) == {"answers": 10, "not": 20,
                                                                              "tiebroken": 5}


def frows(n: int, stratum: str, arm: str, stage: str = "A") -> list[dict[str, Any]]:
    src, label = {"answers": ("agree", 1), "not": ("agree", 0), "tiebroken": ("tiebreak", 0)}[stratum]
    return [{"pair_id": f"{stratum[:2]}{arm[:2]}{i:04d}", "source": src, "label": label, "arm": arm, "stage": stage}
            for i in range(n)]


def test_draw_within_a_cell_is_the_seeded_order() -> None:
    rows = [r for s in spot.SPOT_STRATA for a in ARMS for r in frows(40, s, a)]
    rows += [{**frows(1, "not", "base")[0], "pair_id": "unj", "source": "unjudged", "label": None}]
    picked, counts = spot.draw_pairs_from(rows)
    assert counts["n"] == 100 and counts["unjudged_excluded"] == 1
    cell = [r["pair_id"] for r in picked if r["stratum"] == "answers" and r["arm"] == "neighbour"]
    pool = [r["pair_id"] for r in rows if r["source"] == "agree" and r["label"] == 1 and r["arm"] == "neighbour"]
    assert cell == sorted(pool, key=lambda p: asc.sha_text(f"261101|alias|spot|judge|answers|neighbour|{p}"))[:17]
    assert spot.draw_pairs_from(list(reversed(rows)))[0] == picked
    with pytest.raises(SystemExit, match="not from stage A"):
        spot.draw_pairs_from(rows + frows(1, "not", "base", stage="B"))


def test_sheet_order_is_blind_to_the_stratum() -> None:
    ids = [f"x{i}" for i in range(30)]
    assert spot.sheet_order(ids) == sorted(ids, key=lambda p: asc.sha_text(f"261101|alias|spot|judge|sheet|{p}"))
    assert spot.sheet_order(list(reversed(ids))) == spot.sheet_order(ids)


# ------------------------------------------------------------------ judged pairs on the dry run's stage A

@pytest.fixture(scope="module")
def dry_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("spot")
    (root / "in").mkdir()
    w = fx.World(root / "in", n_papers=20, set_="V")
    dry.dry_run(w.records_path, w.base_export, w.glossary, root / "work", added_per_arm=4, batch_size=200,
                n_perm=100, n_boot=50)
    return root


@pytest.fixture()
def judged(dry_built: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "w"
    shutil.copytree(dry_built, dst)
    for p in [*dst.rglob("*.json"), dst / "work" / "runs" / ar.COMMIT_MARKER]:
        t = p.read_text()
        if str(dry_built) in t:
            p.write_text(t.replace(str(dry_built), str(dst)))
    # the commit file names its own paths: re-seal its sha in the marker and the public file
    runs = dst / "work" / "runs"
    marker = json.loads((runs / ar.COMMIT_MARKER).read_text())
    csha = asc.sha256_file(marker["commit"])
    pub = json.loads(Path(marker["public"]).read_text())
    pub["commit_sha256"] = marker["commit_sha256"] = csha
    Path(marker["public"]).write_text(json.dumps(pub))
    (runs / ar.COMMIT_MARKER).write_text(json.dumps(marker))
    return dst


def jcfg(w: Path) -> Path:
    return w / "work" / "judge-config.json"


def stage_a_final(w: Path) -> dict[str, dict[str, Any]]:
    return {x["pair_id"]: x for x in asc.read_jsonl(w / "work" / "judge" / "final.jsonl")
            if x["k"].startswith("pair|") and x["stage"] == "A"}


def test_draw_pairs_from_committed_stage_a(judged: Path, tmp_path: Path) -> None:
    r = spot.draw_pairs(jcfg(judged), tmp_path / "s")
    key = json.loads(Path(r["key"]).read_text())
    fa = stage_a_final(judged)
    strat = {p: spot.spot_stratum(f) for p, f in fa.items()}
    sizes = {s: {a: sum(strat[p] == s and fa[p]["arm"] == a for p in fa) for a in ARMS} for s in spot.SPOT_STRATA}
    assert key["counts"]["available"] == sizes and key["counts"]["drawn"] == spot.allocate(sizes)
    assert r["n"] == min(100, sum(v for s in sizes.values() for v in s.values()))
    for it in key["items"].values():
        assert it["pair_id"] in fa and strat[it["pair_id"]] == it["stratum"] and fa[it["pair_id"]]["arm"] == it["arm"]
    assert list(key["items"]) == [f"p{i:03d}" for i in range(1, r["n"] + 1)]
    assert [v["pair_id"] for v in key["items"].values()] == spot.sheet_order([v["pair_id"] for v in key["items"].values()])
    assert key["stage_a_commit_sha256"] == json.loads((judged / "work" / "runs" / ar.COMMIT_MARKER).read_text())[
        "commit_sha256"]
    assert spot.draw_pairs(jcfg(judged), tmp_path / "t")["sheet_sha256"] == r["sheet_sha256"]


def test_pair_sheet_shows_what_the_judge_saw_and_nothing_else(judged: Path, tmp_path: Path) -> None:
    r = spot.draw_pairs(jcfg(judged), tmp_path / "s")
    sheet = json.loads(Path(r["sheet"]).read_text())
    key = json.loads(Path(r["key"]).read_text())
    text = Path(r["sheet"]).read_text()
    reg = {p["pair_id"]: p for p in asc.read_jsonl(judged / "work" / "judge" / "pairs.jsonl")}
    for it in sheet["items"]:
        assert set(it) == set(spot.PAIR_ITEM_FIELDS) and it["label"] is None
    for k in ('"arm"', '"stratum"', '"stage"', '"need"', '"source"', '"pair_id"', '"record_id"', '"chunk_id"',
              "neighbour", "random1", "tiebreak", '"leg"', "default", "bm25", "norerank", "merged", "placebo"):
        assert k not in text, k
    for v in key["items"].values():
        p = reg[v["pair_id"]]
        assert v["pair_id"] not in text and p["record_id"] not in text and p["chunk_id"] not in text
    # each item is the judge's prompt, field by field
    cfg = aj.load_config(jcfg(judged))
    chunks, _ = aj.chunk_arms(cfg)
    world = aj.StudyWorld(cfg, chunks, aj.read_glossaries(aj.glossary_paths(cfg)), list(reg.values()))
    for it, v in zip(sheet["items"], key["items"].values(), strict=True):
        prompt, _ = world.prompt(reg[v["pair_id"]])
        assert prompt == aj.jd.judge_prompt(it["question"], it["term"], it["passage"], it["title"],
                                            [tuple(d) for d in it["definitions"]])
    # an item depends on the registry text only, never on arm, stage, need or kind
    some = [reg[v["pair_id"]] for v in list(key["items"].values())[:5]]
    blur = [{**p, "arm": "base", "stage": "B", "need": "rr10", "record_kind": "G"} for p in some]
    out = str(cfg["out_dir"])
    assert spot.pair_items(some, chunks, {}, out) == spot.pair_items(blur, chunks, {}, out)


def test_ingest_pairs_writes_what_the_statistics_read(judged: Path, tmp_path: Path) -> None:
    r = spot.draw_pairs(jcfg(judged), tmp_path / "s")
    f = fill(Path(r["sheet"]), tmp_path / "filled.json")
    res = spot.ingest(Path(r["key"]), f, tmp_path / "judge.jsonl", jcfg(judged))
    rows = asc.read_jsonl(tmp_path / "judge.jsonl")
    assert len(rows) == res["n"] == r["n"] and all(set(x) == {"pair_id", "owner_label"} for x in rows)
    assert all(x["owner_label"] in (0, 1) for x in rows)
    fin = {(x["record_id"], x["chunk_id"]): x for x in asc.read_jsonl(judged / "work" / "judge" / "final.jsonl")
           if x["k"].startswith("pair|")}
    got = ast.spot_flags({"spot_checks": {"judge": str(tmp_path / "judge.jsonl")}}, SimpleNamespace(final=fin))  # type: ignore[arg-type]
    jv = got["judge_validity"]
    assert jv["n"] == r["n"] and jv["without_final_label"] == 0 and jv["unjudged"] == 0
    assert {k: v["n"] for k, v in jv["strata"].items()} == {
        s: sum(v.values()) for s, v in json.loads(Path(r["key"]).read_text())["counts"]["drawn"].items()}


def test_draw_pairs_refuses_without_a_committed_stage_a(judged: Path, tmp_path: Path) -> None:
    (judged / "work" / "runs" / ar.COMMIT_MARKER).unlink()
    with pytest.raises(SystemExit, match="drawn from stage A's committed"):
        spot.draw_pairs(jcfg(judged), tmp_path / "s")


def test_draw_pairs_refuses_a_commit_that_no_longer_verifies(judged: Path, tmp_path: Path) -> None:
    p = judged / "work" / "runs" / "base" / "none.jsonl"
    p.write_text(p.read_text() + "\n")
    with pytest.raises(SystemExit, match="stage-A commit"):
        spot.draw_pairs(jcfg(judged), tmp_path / "s")


def test_draw_pairs_reads_only_the_committed_bytes(judged: Path, tmp_path: Path) -> None:
    sa = spot.committed_stage_a(aj.load_config(jcfg(judged)))
    assert sa["final"] and all(x["stage"] == "A" for x in sa["final"])
    assert {x["pair_id"] for x in sa["final"]} == set(stage_a_final(judged))
    # the full file holds stage B's labels after the committed prefix
    assert any(x.get("stage") == "B" for x in asc.read_jsonl(judged / "work" / "judge" / "final.jsonl"))


def test_draw_pairs_refuses_a_pilot_or_mismatched_config(judged: Path) -> None:
    cfg = aj.load_config(jcfg(judged))
    with pytest.raises(SystemExit, match="pilot judge directory"):
        spot.committed_stage_a({**cfg, "dry_run": False, "pilot": True})
    with pytest.raises(SystemExit, match="config is 'main'"):
        spot.committed_stage_a({**cfg, "dry_run": False})
    with pytest.raises(SystemExit, match="not in the judge config's out_dir"):
        spot.committed_stage_a({**cfg, "out_dir": str(judged / "elsewhere")})


def test_ingest_pairs_refusals(judged: Path, tmp_path: Path) -> None:
    r = spot.draw_pairs(jcfg(judged), tmp_path / "s")
    f = fill(Path(r["sheet"]), tmp_path / "filled.json")
    with pytest.raises(SystemExit, match="needs --judge-config"):
        spot.ingest(Path(r["key"]), f, tmp_path / "j.jsonl")
    key = json.loads(Path(r["key"]).read_text())
    # a pair whose label is not a stage-A label is refused
    stage_b = next(x["pair_id"] for x in asc.read_jsonl(judged / "work" / "judge" / "final.jsonl")
                   if x["k"].startswith("pair|") and x["stage"] == "B")
    bad = json.loads(json.dumps(key))
    bad["items"]["p001"]["pair_id"] = stage_b
    (tmp_path / "bad.key.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="not stage-A labelled pairs"):
        spot.ingest(tmp_path / "bad.key.json", f, tmp_path / "j.jsonl", jcfg(judged))
    other = {**key, "stage_a_commit_sha256": "0" * 64}
    (tmp_path / "other.key.json").write_text(json.dumps(other))
    with pytest.raises(SystemExit, match="not the one the sheet was drawn from"):
        spot.ingest(tmp_path / "other.key.json", f, tmp_path / "j.jsonl", jcfg(judged))
    g = fill(Path(r["sheet"]), tmp_path / "g.json", e=lambda s: s["items"][0].update(term="something else"))
    with pytest.raises(SystemExit, match="not the drawn sheet"):
        spot.ingest(Path(r["key"]), g, tmp_path / "j.jsonl", jcfg(judged))
    assert not (tmp_path / "j.jsonl").exists()
