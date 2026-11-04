"""Hand-checkable tests for the gold-labelling pure parts and the committed round-1 scripts."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse_gold as ag  # noqa: E402
import build_sample as bs  # noqa: E402
import gold_common as gc  # noqa: E402
import human_agree as ha  # noqa: E402
import make_sheet_html as mk  # noqa: E402


# ------------------------------------------------------------- majority / kappa / rates
def test_majority_rules() -> None:
    assert gc.majority([]) is None
    assert gc.majority(["correct"]) is None  # one vote is not labelled
    assert gc.majority(["correct", "correct"]) == "correct"
    assert gc.majority(["correct", "wrong"]) == gc.SPLIT
    assert gc.majority(["correct", "wrong", "correct"]) == "correct"
    assert gc.majority(["correct", "wrong", "partial"]) == gc.SPLIT
    # 2-2 is not a strict majority
    assert gc.majority(["correct", "correct", "wrong", "wrong"]) == gc.SPLIT


def test_kappa_hand_checked() -> None:
    # 4 items: agree on 3. x = c,c,w,w ; y = c,c,w,c.
    # po = 3/4; px = {c:2,w:2}, py = {c:3,w:1}; pe = (2*3 + 2*1)/16 = 0.5; kappa = (.75-.5)/.5 = .5
    x = {1: "correct", 2: "correct", 3: "wrong", 4: "wrong"}
    y = {1: "correct", 2: "correct", 3: "wrong", 4: "correct"}
    assert gc.kappa(x, y) == {"kappa": 0.5, "agree": 0.75, "n": 4}
    # unknown labels and missing keys are ignored
    assert gc.kappa({**x, 5: "maybe"}, {**y, 6: "correct"})["n"] == 4
    # all one label on both sides: pe = 1 -> kappa None
    assert gc.kappa({1: "correct"}, {1: "correct"})["kappa"] is None
    assert gc.kappa({}, {}) is None


def test_kappa_partial_merge() -> None:
    x = {1: "partial", 2: "wrong"}
    y = {1: "correct", 2: "wrong"}
    assert gc.kappa(x, y)["agree"] == 0.5
    assert gc.kappa(x, y, gc.MERGE_PARTIAL) == {"kappa": 1.0, "agree": 1.0, "n": 2}


ITEM_ARMS = {
    "O-01": {"items": {"i01": {"arms": ["rules", "q"]}, "i02": {"arms": ["q"]}, "i03": {"arms": ["rules"]}}},
    "O-02": {"items": {"i01": {"arms": ["q"]}, "i02": {"arms": ["q", "rules"]}}},
}
LABS: dict[Any, Any] = {("O-01", "i01"): "correct", ("O-01", "i02"): "wrong", ("O-01", "i03"): "partial",
                        ("O-02", "i01"): "not-a-symbol", ("O-02", "i02"): gc.SPLIT}


def test_counts_and_rates_hand_checked() -> None:
    c = gc.counts(LABS, ITEM_ARMS, ["O-01", "O-02"], ["rules", "q"])
    # rules: i01 correct, i03 partial (O-02 i02 is split -> left out)
    assert dict(c["rules"]) == {"correct": 1, "partial": 1}
    # q: correct, wrong, not-a-symbol
    assert dict(c["q"]) == {"correct": 1, "wrong": 1, "not-a-symbol": 1}
    r = gc.rates(c["rules"])
    assert r == {"n": 2, "strict": 0.5, "lenient": 1.0, "harm": 0.0, "not_a_symbol": 0.0}
    q = gc.rates(c["q"])
    assert q["harm"] == pytest.approx(1 / 3) and q["not_a_symbol"] == pytest.approx(1 / 3)
    assert gc.rates({}) is None
    # a resampled document counts twice
    assert gc.counts(LABS, ITEM_ARMS, ["O-01", "O-01"], ["rules"])["rules"]["correct"] == 2


def test_bootstrap_is_deterministic() -> None:
    a = gc.doc_bootstrap(LABS, ITEM_ARMS, ["O-01", "O-02"], ["rules", "q"], seed=261101, n_boot=200)
    b = gc.doc_bootstrap(LABS, ITEM_ARMS, ["O-01", "O-02"], ["rules", "q"], seed=261101, n_boot=200)
    assert a == b
    assert a["rules"]["strict"] is not None
    lo, hi = a["q"]["harm"]
    assert 0 <= lo <= hi <= 1
    d1 = gc.diff_bootstrap(LABS, ITEM_ARMS, ["O-01", "O-02"], ["rules", "q"], "q", "rules", "harm", 7, 200)
    d2 = gc.diff_bootstrap(LABS, ITEM_ARMS, ["O-01", "O-02"], ["rules", "q"], "q", "rules", "harm", 7, 200)
    assert d1 == d2


def test_seeded_streams() -> None:
    assert gc.seeded("261101|gold", "|", "pick", "O", 0).random() == random.Random("261101|gold|pick|O|0").random()
    assert gc.doc_alias("OA-1000-07") == "OA-1000" and gc.doc_alias("O-01") == "O"


def test_terciles_and_items() -> None:
    pop = [f"d{i}" for i in range(7)]
    size = {d: i // 2 for i, d in enumerate(pop)}  # 0,0,1,1,2,2,3
    t = gc.terciles(pop, size, random.Random(1))
    assert [len(g) for g in t] == [2, 3, 2]  # cuts at round(7/3)=2, round(14/3)=5
    assert sorted(sum(t, [])) == sorted(pop)
    assert max(size[d] for d in t[0]) <= min(size[d] for d in t[2])
    g = gc.group_items({"rules": [("x", "Speed  of A", 5)], "q": [("x", "speed of a", 9), ("y", "Y", 1)]})
    assert set(g) == {("x", "speed of a"), ("y", "y")}
    assert g[("x", "speed of a")]["arms"] == {"rules", "q"}
    assert g[("x", "speed of a")]["offsets"] == {5, 9}


def test_owner_sample_deterministic_and_blind() -> None:
    rows = [{"key": f"O-{k:02d}", "items": [{"id": f"i{i:02d}"} for i in range(1, 6)]} for k in range(1, 5)]
    a = bs.owner_sample(rows, 7, "261101|gold", "|")
    assert a == bs.owner_sample(rows, 7, "261101|gold", "|")
    assert len(a) == len(set(a)) == 7
    assert bs.owner_sample(rows, 100, "s", "|") == sorted((r["key"], i["id"]) for r in rows for i in r["items"])
    assert bs.owner_sample(rows, 0, "s", "|") == []


# ------------------------------------------------------------- analyse_gold / human_agree
def _judge_file(tmp: Path, name: str, labels: dict[tuple[str, str], str]) -> Path:
    by_doc: dict[str, list[dict[str, str]]] = {}
    for (d, i), lab in labels.items():
        by_doc.setdefault(d, []).append({"id": i, "label": lab, "reason": ""})
    p = tmp / f"{name}.jsonl"
    p.write_text("".join(json.dumps({"key": d, "ok": True, "labels": ls}) + "\n" for d, ls in by_doc.items())
                 + json.dumps({"key": "O-09", "ok": False}) + "\n")
    return p


def test_analyse_gold_majority_and_overlap(tmp_path: Path) -> None:
    keys = [("O-01", "i01"), ("O-01", "i02"), ("O-01", "i03"), ("O-02", "i01"), ("O-02", "i02")]
    j1 = dict(zip(keys, ["correct", "wrong", "partial", "not-a-symbol", "correct"], strict=True))
    j2 = dict(zip(keys, ["correct", "wrong", "partial", "not-a-symbol", "wrong"], strict=True))
    j3 = dict(zip(keys, ["correct", "correct", "partial", "correct", "partial"], strict=True))
    lab = {n: ag.read_judge(_judge_file(tmp_path, n, j)) for n, j in (("a", j1), ("b", j2), ("c", j3))}
    out = ag.analyse(ITEM_ARMS, lab, None, ["rules", "q"], [("q", "rules")], seed=5, n_boot=50, overlap_judge="c")
    # majority: i01 correct, i02 wrong, i03 partial, O-02 i01 not-a-symbol, O-02 i02 split (c/w/p)
    assert out["split_items"] == 1
    assert out["majority"]["per_arm"]["rules"] == {"n": 2, "strict": 0.5, "lenient": 1.0, "harm": 0.0,
                                                   "not_a_symbol": 0.0}
    # without judge c, O-02 i02 is correct vs wrong -> split again; the rest unchanged
    assert out["majority_without_overlap_judge"]["excluded_judge"] == "c"
    assert out["judge_kappa"]["a~b"]["4-label"]["agree"] == 0.8
    assert set(out["majority"]["contrasts"]) == {"q-rules"}


def test_human_agree(tmp_path: Path) -> None:
    owner = tmp_path / "owner.csv"
    owner.write_text("doc,item,symbol,definition,label,note\n"
                     "O-01,i01,x,d,correct,\nO-01,i02,y,d,wrong,\nO-01,i03,z,d,,\nO-01,missed,,,,a = b; c = d\n")
    own, missed, unl = ha.read_owner_csv(owner, set())
    assert own == {("O-01", "i01"): "correct", ("O-01", "i02"): "wrong"} and unl == 1 and missed == {"O-01": 2}
    lab = {"a": {("O-01", "i01"): "correct", ("O-01", "i02"): "correct"},
           "b": {("O-01", "i01"): "correct", ("O-01", "i02"): "wrong"}}
    ag_ = ha.agreement(own, lab)
    assert ag_["b"]["4-label"] == {"kappa": 1.0, "agree": 1.0, "n": 2}
    assert ag_["a"]["confusion_owner_to_judge"] == {"wrong->correct": 1}
    # majority: i01 correct (2 votes), i02 split -> only i01 compared
    assert ag_["majority"]["4-label"]["n"] == 1 and ag_["majority_split_items"] == 1
    own2, _, _ = ha.read_owner_csv(owner, {("O-01", "i02")})
    assert ("O-01", "i02") not in own2


# ------------------------------------------------------------- labelling page
def test_sheet_build_and_render() -> None:
    sample = [{"key": "O-01", "corpus": "O", "judged_text": "t1 </script>", "text_chars": 12,
               "items": [{"id": "i01", "symbol": "x", "definition": "d1"}, {"id": "i02", "symbol": "y", "definition": "d2"}]},
              {"key": "A-01", "corpus": "A", "judged_text": "t2", "items": [{"id": "i01", "symbol": "z", "definition": "d"}]}]
    labels = [["O-01", "i02", "y", "d2", "", ""], ["A-01", "i01", "z", "d", "", ""]]
    data = mk.build_data(sample, labels, "<p>rules</p>", "k-r2", False, "owner_labels.csv")
    assert [d["id"] for d in data["docs"]] == ["O-01", "A-01"]
    assert [i["id"] for i in data["docs"][0]["items"]] == ["i02"]  # blind subset only
    assert data["storage_key"] == "k-r2" and data["missed"] is False
    tpl = (Path(mk.__file__).parent / "sheet_template.html").read_text()
    html = mk.render(tpl, data)
    assert "</script>\"" not in html and "<\\/script>" in html
    assert "fonts.googleapis" not in html and "DATA.storage_key" in html
    with pytest.raises(SystemExit):
        mk.build_data(sample, [["X-01", "i01"]], "", "k", False, "x.csv")


# ------------------------------------------------------------- build_sample end to end (synthetic)
PAD = "Filler sentence about nothing in particular. " * 20
DOCS = {
    "a": "The exhaled flux N = Qb Cq, where Qb is the breathing flow rate, and Cq is the quanta "
         "concentration.\n" + PAD,
    "b": "We introduce the viral deactivation rate λv, a key constant.\n" + PAD
         + "\nMeasured: λv = 0.3 per hour, where r¯ is the effective infectious drop radius.\n",
    "c": "Here k is the decay constant of the tracer.\n" + PAD,
}


def test_build_sample_end_to_end(tmp_path: Path) -> None:
    from dataclasses import replace

    from ragstack.ingestion.glossary_file import build_row, write_glossary_file
    from ragstack.ingestion.loaders import JsonlLoader
    from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher

    shard = tmp_path / "shard.jsonl"
    shard.write_text("".join(json.dumps({"text": t, "path": f"{n}.pdf", "metadata": {}}) + "\n"
                             for n, t in DOCS.items()), encoding="utf-8")
    docs = JsonlLoader().load(str(shard))
    att = SymbolGlossaryAttacher()
    rules_rows = [build_row(d.id, d.content, att) for d in docs]
    write_glossary_file(tmp_path / "rules.jsonl", rules_rows, producer="t", created="2026-11-01T00:00:00Z")
    # a synthetic "pure" arm: its LLM half holds the rules entries
    write_glossary_file(tmp_path / "q.jsonl", [replace(r, llm=r.rules, merged=r.rules) for r in rules_rows],
                        producer="t", created="2026-11-01T00:00:00Z")
    out = tmp_path / "gold"
    argv = ["--out", str(out), "--corpus", f"O={shard}", "--glossary", f"O:rules={tmp_path / 'rules.jsonl'}",
            "--glossary", f"O:q={tmp_path / 'q.jsonl'}", "--per-tercile", "1", "--seed", "261101|gold",
            "--owner-items", "2"]
    assert bs.main(argv) == 0
    rows = [json.loads(x) for x in (out / "sample.jsonl").read_text().splitlines()]
    assert rows and all(r["key"].startswith("O-") and r["corpus"] == "O" for r in rows)
    ia = json.loads((out / "item_arms.json").read_text())
    some = next(iter(next(iter(ia.values()))["items"].values()))
    assert set(some["arms"]) == {"rules", "q"}  # identical proposals collapse to one item
    man = json.loads((out / "manifest.json").read_text())
    assert man["totals"]["owner_items"] == 2 and man["seed"] == "261101|gold"
    assert oct(out.stat().st_mode & 0o777) == "0o700"
    assert oct((out / "sample.jsonl").stat().st_mode & 0o777) == "0o600"
    # deterministic: a second build is byte-identical
    out2 = tmp_path / "gold2"
    assert bs.main([*argv[:1], str(out2), *argv[2:]]) == 0
    assert (out / "sample.jsonl").read_bytes() == (out2 / "sample.jsonl").read_bytes()
    assert (out / "owner_labels.csv").read_bytes() == (out2 / "owner_labels.csv").read_bytes()
