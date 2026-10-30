"""Round 3 pool: the arm-blind populations V and M_num, eligibility by density and
glossary, per-stratum quotas, the gold-differs gate (prereg round 3 §3.1).

A hand-built synthetic corpus whose every count can be checked by reading the
segments below; glossary files are built by the library's own rules builder and
``rederive --attach mention``. No store, no network, no model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_pool as r2pool  # noqa: E402
import glossbench_r3_pool as pool  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    build_row,
    rederive_file,
    write_glossary_file,
)
from ragstack.ingestion.symbol_glossary import DEFAULT_CONFIG, SymbolGlossaryAttacher  # noqa: E402

PROV = {"commit": "0" * 40, "dirty": False, "untracked": 0}

#: A dense paper: Qb is defined (the rules glossary holds it), Kz is not.
#: V  = s1 (Qb = …: value and mention both render Qb), s2 (Kz = … while Qb is mentioned:
#:      value renders "", mention renders Qb -> GOLD DIFFERS), s5 (Qb = …: same under both)
#: M_num = s3, s6, s8, s10 (Qb + a number: mention renders Qb, value "" -> GOLD DIFFERS)
#:         s4, s7, s9 (Kz + a number: no entry, "" under both)
#: outside: s0 (the definition: no number), s11 (no symbol)
DENSE = [
    "where Qb is the boat drift speed of the boats. ",
    "We measured Qb = 0.{i}5 m/s in calm water. ",
    "Later Kz = 3{i} was set while Qb was steady. ",
    "Qb rose in 12 of the trials. ",
    "Kz stayed near 4 units. ",
    "At dusk Qb = 0.7{i} again. ",
    "Qb fell in 3 runs. ",
    "In 5 cases Kz was higher. ",
    "Qb doubled 2 times. ",
    "Kz changed 7 times. ",
    "Qb was low on day 9. ",
    "The pond keeper wrote it all down. ",
]
V_IDX, M_IDX = {1, 2, 5}, {3, 4, 6, 7, 8, 9, 10}
GD_IDX = {2, 3, 6, 8, 10}            # gold differs: V {2}, M_num {3, 6, 8, 10}


def segs(kind: str, i: int) -> list[str]:
    if kind == "dense":
        return [s.format(i=i) for s in DENSE]
    if kind == "noglossary":             # the same densities, no definition: an empty glossary
        return [s.format(i=i) for s in DENSE[1:]] + ["The boats were blue. "]
    if kind == "sparse":                 # V = 1 (< min_v)
        return [DENSE[0], DENSE[1].format(i=i), DENSE[3], DENSE[4], DENSE[6]]
    raise ValueError(kind)


def chunk_rows(doc: str, parts: list[str]) -> tuple[list[dict[str, Any]], str]:
    rows, s = [], 0
    for n, p in enumerate(parts):
        rows.append({"id": f"{doc}-c{n}", "doc_id": doc, "content": p, "start_char": s, "end_char": s + len(p),
                     "metadata": {"source_path": doc, "title": f"Boat paper {doc}"}})
        s += len(p)
    return rows, "".join(parts)


LAYOUT = {"d1": "dense", "d2": "dense", "d3": "dense", "d4": "noglossary", "d5": "sparse"}


def world(tmp: Path, pool_r3: dict[str, Any] | None = None, **corpus_over: Any) -> tuple[dict[str, Any], Path]:
    rows: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for i, (doc, kind) in enumerate(LAYOUT.items(), 1):
        r, t = chunk_rows(doc, segs(kind, i))
        rows += r
        texts[doc] = t
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    chunks = data / "chunks.jsonl"
    chunks.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    att = SymbolGlossaryAttacher(DEFAULT_CONFIG)
    value = data / "value.jsonl"
    write_glossary_file(value, [build_row(d, texts[d], att) for d in sorted(texts)], producer="synthetic",
                        created="2026-10-30T00:00:00Z")
    mention = data / "mention.jsonl"
    rederive_file(value, mention, attach="mention")
    cfg = {"out_dir": str(tmp / "out"),
           "corpora": {"O": {"chunks": [str(chunks)], "expected_chunks": len(rows), "paper_key": "doc_id",
                             "arms": {"value": str(value), "mention": str(mention)}, **corpus_over}},
           "pool_r3": {"n_papers": 3, "v_per_paper": 3, "m_per_paper": 7, "gold_differs_gate": 15,
                       "gold_differs_gate_per_stratum": 3, **(pool_r3 or {})}}
    return cfg, data


def build(tmp: Path, **kw: Any) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    dry = kw.pop("dry", False)
    reach = kw.pop("corpus_reach", False)
    cfg, _ = world(tmp, **kw)
    out = g.out_dir(cfg)
    m = pool.build(cfg, "O", out, dry=dry, corpus_reach=reach, provenance=lambda: PROV)
    rows = [] if dry else [json.loads(x) for x in (out / "pool-O.jsonl").read_text().splitlines()]
    return m, out, rows


# --------------------------------------------------------------------------- populations
def test_populations_are_hand_checked() -> None:
    rows = []
    for i, (doc, kind) in enumerate(LAYOUT.items(), 1):
        rows += chunk_rows(doc, segs(kind, i))[0]
    crs = [g.parse_chunk(r, "t") for r in rows]
    corpus = g.build_corpus(crs, "doc_id")
    stratum, vs, mentioned, sigma = pool.populations(crs, corpus, sorted(g.STAT_TOKENS))
    assert sigma["d1"] == {"Qb", "Kz"} and sigma["d5"] == {"Qb"}
    got_v = {int(c.split("-c")[1]) for c, s in stratum.items() if c.startswith("d1-") and s == "V"}
    got_m = {int(c.split("-c")[1]) for c, s in stratum.items() if c.startswith("d1-") and s == "M_num"}
    assert (got_v, got_m) == (V_IDX, M_IDX)
    assert vs["d1-c2"] == ["Kz"] and mentioned["d1-c3"] == ["Qb"] and mentioned["d1-c4"] == ["Kz"]
    # sparse: V = 1; Sigma = {Qb}, so the Kz chunk is outside the population
    assert sum(s == "V" for c, s in stratum.items() if c.startswith("d5-")) == 1
    assert stratum.get("d5-c3") is None and stratum["d5-c2"] == "M_num"


def test_build_eligibility_reach_and_gate(tmp_path: Path) -> None:
    m, _, rows = build(tmp_path)
    c = m["counts"]
    # dense d1-d3 and the glossary-less d4 meet the densities; d4 has no glossary; d5 is sparse
    assert (c["papers_dense"], c["papers_eligible"], c["papers_dense_without_glossary"]) == (4, 3, 1)
    assert c["eval_papers"] == 3 and {r["paper"] for r in rows[1:]} == {"d1", "d2", "d3"}
    bs = c["by_stratum"]
    assert bs["V"]["sampled"]["chunks"] == 9 and bs["M_num"]["sampled"]["chunks"] == 21
    assert bs["V"]["sampled"]["gold_differs"] == 3 and bs["M_num"]["sampled"]["gold_differs"] == 12
    # reach: value renders on s1, s5 (V); mention on s1, s2, s5 (V) and s3, s6, s8, s10 (M_num)
    assert bs["V"]["sampled"]["reach"] == {"value": 6, "mention": 9}
    assert bs["M_num"]["sampled"]["reach"] == {"value": 0, "mention": 12}
    gate = m["gate"]
    assert gate["gold_differs_sampled"] == 15 and gate["status"] == "EVALUABLE" and gate["reason"] is None
    # every pool row: the documented gold-differs chunks, and value reach implies mention reach
    for r in rows[1:]:
        idx = int(r["chunk_id"].split("-c")[1])
        assert r["gold_differs"] is (idx in GD_IDX)
        assert not r["reach"]["value"] or r["reach"]["mention"]
        assert r["stratum"] == ("V" if idx in V_IDX else "M_num")
    header = rows[0]
    assert header["schema"] == pool.POOL_SCHEMA and header["attachment"] == {"value": "value", "mention": "mention"}
    assert header["strata"] == ["V", "M_num"] and header["seed"] == "261103|O"
    # the eligible papers' distractor load: a mention field on s0, s1, s2, s3, s5, s6, s8, s10 -> 8 of 12 each
    assert c["eligible_paper_chunks_with_mention_field"] == {"chunks": 36, "with_field": 24}


@pytest.mark.parametrize("over,status,why", [
    ({"gold_differs_gate": 16}, pool.GATE, "total 15 < 16"),
    ({"gold_differs_gate_per_stratum": 4}, pool.GATE, "V 3 < 4"),
    ({"gold_differs_gate": 15, "gold_differs_gate_per_stratum": 3}, "EVALUABLE", None),
])
def test_gate_thresholds(tmp_path: Path, over: dict[str, Any], status: str, why: str | None) -> None:
    m, _, _ = build(tmp_path, pool_r3=over)
    assert m["gate"]["status"] == status and m["gate"]["reason"] == why


def test_quotas_and_seeded_sample(tmp_path: Path) -> None:
    m, _, rows = build(tmp_path / "a", pool_r3={"v_per_paper": 2, "m_per_paper": 3})
    body = rows[1:]
    for p in ("d1", "d2", "d3"):
        assert sum(r["sampled"] for r in body if r["paper"] == p and r["stratum"] == "V") == 2
        assert sum(r["sampled"] for r in body if r["paper"] == p and r["stratum"] == "M_num") == 3
    assert m["gate"]["gold_differs_sampled"] == sum(r["gold_differs"] for r in body if r["sampled"])
    assert m["counts"]["by_stratum"]["V"]["pool"]["chunks"] == 9          # the pool keeps every chunk
    # the sample order is round 2's seeded order
    ids = sorted(r["chunk_id"] for r in body if r["paper"] == "d1" and r["stratum"] == "M_num")
    order = r2pool.seeded_order(ids, "261103|O|chunks|M_num|d1")
    assert [r["chunk_id"] for r in sorted((r for r in body if r["chunk_id"] in ids),
                                          key=lambda r: r["sample_rank"])] == order
    # deterministic
    m2, _, rows2 = build(tmp_path / "b", pool_r3={"v_per_paper": 2, "m_per_paper": 3})
    assert rows2[1:] == body and m2["gate"] == m["gate"]


def test_paper_sample_is_seeded(tmp_path: Path) -> None:
    m, _, rows = build(tmp_path, pool_r3={"n_papers": 2})
    import random
    want = sorted(random.Random("261103|O").sample(["d1", "d2", "d3"], 2))
    assert rows[0]["eval_papers"] == want and m["counts"]["eval_papers"] == 2


def test_require_glossary_off_admits_the_glossaryless_paper(tmp_path: Path) -> None:
    m, _, rows = build(tmp_path, pool_r3={"require_glossary": False, "n_papers": 10})
    assert m["counts"]["papers_eligible"] == 4 and "d4" in rows[0]["eval_papers"]


# --------------------------------------------------------------------------- config
def test_pool_config_defaults_and_per_set() -> None:
    p = pool.pool_config({}, "O")
    assert (p["n_papers"], p["v_per_paper"], p["m_per_paper"], p["gold_differs_gate"],
            p["gold_differs_gate_per_stratum"], p["seed_base"]) == (50, 6, 6, 100, 40, "261103")
    assert (p["min_v"], p["min_m"], p["min_pop"], p["require_glossary"]) == (2, 2, 10, True)
    a = pool.pool_config({}, "A")
    assert (a["n_papers"], a["v_per_paper"], a["m_per_paper"], a["gold_differs_gate"]) == (25, 2, 6, 40)
    pp = pool.pool_config({}, "P")
    assert (pp["n_papers"], pp["gold_differs_gate"], pp["gold_differs_gate_per_stratum"]) == (25, 60, 0)
    # a config override merges into the pre-registered per-set values, key by key
    o = pool.pool_config({"pool_r3": {"per_set": {"A": {"n_papers": 28}}}}, "A")
    assert (o["n_papers"], o["v_per_paper"], o["gold_differs_gate"]) == (28, 2, 40)
    with pytest.raises(SystemExit, match="unknown keys"):
        pool.pool_config({"pool_r3": {"s1_per_paper": 3}})
    with pytest.raises(SystemExit, match="per_set"):
        pool.pool_config({"pool_r3": {"per_set": {"O": {"min_v": 3}}}})
    with pytest.raises(SystemExit, match="different arms"):
        pool.pool_config({"pool_r3": {"mention_arm": "value"}})


def test_per_set_override_is_recorded(tmp_path: Path) -> None:
    m, _, _ = build(tmp_path, pool_r3={"per_set": {"O": {"v_per_paper": 1}}})
    assert m["design"]["quotas_per_paper"]["v_per_paper"] == 1 and m["design"]["quotas_overridden"] == ["v_per_paper"]
    assert m["counts"]["by_stratum"]["V"]["sampled"]["chunks"] == 3


# --------------------------------------------------------------------------- manipulation checks
def test_arm_pair_identity_is_enforced(tmp_path: Path) -> None:
    cfg, data = world(tmp_path)
    # the value file declared as both arms: the mention arm's header says value
    bad = json.loads(json.dumps(cfg))
    bad["corpora"]["O"]["arms"]["mention"] = str(data / "value.jsonl")
    with pytest.raises(SystemExit, match="mention arm's header rule is 'value'"):
        pool.build(bad, "O", g.out_dir(cfg), provenance=lambda: PROV)
    # a mention file whose entries differ (another glossary) is not the same glossary
    rows = [json.loads(x) for x in (data / "mention.jsonl").read_text().splitlines()]
    body = rows[1]
    body["merged"]["entries"] = []
    body["rules"]["entries"] = []
    other = data / "other.jsonl"
    rows[0]["counts"]["merged_entries"] -= 1
    other.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))
    bad["corpora"]["O"]["arms"]["mention"] = str(other)
    with pytest.raises(SystemExit, match="differ in more than the attach mark"):
        pool.build(bad, "O", g.out_dir(cfg), provenance=lambda: PROV)
    # round 2's pool still refuses a mention file (unchanged)
    corpus = g.build_corpus(g.read_chunks(cfg["corpora"]["O"]["chunks"]), "doc_id")
    with pytest.raises(SystemExit, match="not value-only"):
        r2pool.load_arm(str(data / "mention.jsonl"), {"d1"}, corpus, "mention")


def test_chunk_count_check(tmp_path: Path) -> None:
    cfg, _ = world(tmp_path)
    cfg["corpora"]["O"]["expected_chunks"] = 1
    with pytest.raises(SystemExit, match="expected 1"):
        pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)


def test_exclusions_from_several_files(tmp_path: Path) -> None:
    (tmp_path / "x1.txt").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "x1.txt").write_text("d1\n")
    (tmp_path / "x2.txt").write_text('["d2"]')
    m, _, rows = build(tmp_path, exclude_papers=[str(tmp_path / "x1.txt"), str(tmp_path / "x2.txt")])
    assert rows[0]["eval_papers"] == ["d3"] and m["counts"]["excluded_papers"] == 2
    assert len(rows[0]["inputs_sha256"]["exclude_papers"]) == 2


# --------------------------------------------------------------------------- outputs
def test_dry_writes_the_manifest_only_and_fields_mode_needs_dry(tmp_path: Path) -> None:
    m, out, _ = build(tmp_path / "a", dry=True)
    assert m["reading"] == "dry" and m["pool_sha256"] is None and not (out / "pool-O.jsonl").exists()
    assert m["gate"]["gold_differs_sampled"] == 15
    cfg, _ = world(tmp_path / "b", draft_mode="fields")
    with pytest.raises(SystemExit, match="fields mode"):
        pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    assert pool.build(cfg, "O", g.out_dir(cfg), dry=True, provenance=lambda: PROV)["design"]["fields_mode"]


def test_manifest_is_counts_only_and_ranked(tmp_path: Path) -> None:
    m, out, rows = build(tmp_path, corpus_reach=True)
    strings = set(g.string_values(m))
    words = {w for s in strings for w in s.replace("/", " ").replace("|", " ").split()}
    for paper in ("d1", "d2", "d3", "d4", "d5"):
        assert paper not in strings and paper not in words, paper
    assert not any("Qb" in s or "boat" in s for s in strings)
    pp = m["per_paper_by_rank"]
    assert [p["rank"] for p in pp] == [1, 2, 3]
    assert all((p["V"], p["M_num"], p["chunks"], p["mention_glossary_chunks"]) == (3, 7, 12, 8) for p in pp)
    assert m["eligible_density"]["V"]["median"] == 3.0
    # corpus reach: value renders on s1, s5 of the 3 dense papers (6 chunks); mention on 8 per dense paper
    # (24) plus the sparse paper's Qb chunks (s0 def, s1 V, s3 and s6 M_num: 4)
    cr = m["corpus_reach"]
    assert cr["value"]["chunks_with_field"] == 6 + 1 and cr["mention"]["chunks_with_field"] == 24 + 4
    assert m["citable"] is True and m["provenance"] == PROV
    assert m["pool_sha256"] == g.file_sha(out / "pool-O.jsonl")


def test_round2_pool_module_is_unchanged() -> None:
    assert r2pool.DEFAULTS["seed_base"] == "261101" and r2pool.STRATA == ("S1", "S2", "S3")
    assert r2pool.POOL_SCHEMA == "glossbench-r2-pool/1"
