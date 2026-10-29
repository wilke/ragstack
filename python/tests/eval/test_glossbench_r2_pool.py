"""Round-2 harness item 2c: eval papers, the arm-blind pool, strata, reach gate.

Synthetic corpus and glossary files only (``glossbench_r2_support``); no store,
no network, no model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.eval import glossbench_r2_support as sup

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_pool as pool  # noqa: E402

PROV = {"commit": "0" * 40, "dirty": False, "untracked": 0}


def _build(tmp: Path, **kw):
    path, cfg, texts = sup.make_config(tmp, **kw)
    out = g.out_dir(cfg)
    return pool.build(cfg, "O", out, provenance=lambda: PROV), out, cfg, texts


def _pool_rows(out: Path) -> tuple[dict, list[dict]]:
    rows = [json.loads(x) for x in (out / "pool-O.jsonl").read_text().splitlines()]
    return rows[0], rows[1:]


def test_value_statements_use_no_glossary_and_skip_units() -> None:
    assert g.value_statements("we set Qb = 0.5 m3/h here") == ["Qb"]
    assert g.value_statements("λv(r) = 0.3/h and λ_a ≈ .2") == ["λa", "λv"]
    assert g.value_statements("a volume of 500 mL = 3 doses") == []      # a unit is not a symbol
    assert g.value_statements("p < 0.05 and x = y") == []                 # comparison; no number
    assert g.value_statements("n = 12 samples; R2 = 0.93") == ["R2", "n"]
    assert g.value_statements("10 = 10") == []                            # a bare number


def test_build_counts_strata_and_gate(tmp_path: Path) -> None:
    m, out, _, _ = _build(tmp_path)
    assert m["reading"] == "final"
    assert m["counts"]["eval_papers"] == 4 and m["counts"]["papers"] == 6
    s = m["counts"]["strata"]
    assert s["S1"] > 0 and s["S2"] > 0 and s["S3"] > 0
    assert m["gate"]["s1"] == s["S1"] and m["gate"]["status"] == "EVALUABLE"
    assert m["manipulation"]["match"] is True
    # the rules-built arms reach the defined symbol; the empty frontier reaches nothing
    assert m["reach"]["rules"]["vs_chunks_reached"] == s["S1"]
    assert m["reach"]["frontierp"]["vs_chunks_reached"] == 0
    header, rows = _pool_rows(out)
    assert len(header["eval_papers"]) == 4
    for r in rows:
        assert r["paper"] in header["eval_papers"]
        assert r["stratum"] == ("S3" if not r["vs_tokens"] else "S1" if any(r["reach"].values()) else "S2")
    # per paper and stratum, the first k of the seeded order are sampled
    for p in header["eval_papers"]:
        for s_, cap in (("S1", 2), ("S2", 1), ("S3", 1)):
            got = [r for r in rows if r["paper"] == p and r["stratum"] == s_]
            assert sum(r["sampled"] for r in got) == min(cap, len(got))
            assert all(r["sampled"] == (r["sample_rank"] < cap) for r in got)


def test_paper_selection_is_arm_blind(tmp_path: Path) -> None:
    """The eval papers depend only on value statements: identical with the frontier
    absent (dry reading), and with every arm empty."""
    a, out_a, _, _ = _build(tmp_path / "a")
    b, out_b, _, _ = _build(tmp_path / "b", with_frontier=False)
    c, out_c, _, _ = _build(tmp_path / "c", arms_empty=True)
    assert b["reading"] == "dry"
    assert a["eval_papers_sha256"] == b["eval_papers_sha256"] == c["eval_papers_sha256"]
    assert _pool_rows(out_a)[0]["eval_papers"] == _pool_rows(out_c)[0]["eval_papers"]
    # every arm empty: nothing is reachable, the gate closes
    assert c["counts"]["strata"]["S1"] == 0
    assert c["gate"]["status"] == "GATE-NOT-EVALUABLE"


def test_pool_is_deterministic(tmp_path: Path) -> None:
    _, out1, _, _ = _build(tmp_path / "1")
    _, out2, _, _ = _build(tmp_path / "2")
    assert _pool_rows(out1)[1] == _pool_rows(out2)[1]


def test_exclusions_remove_papers_before_sampling(tmp_path: Path) -> None:
    path, cfg, _ = sup.make_config(tmp_path, pool={"n_papers": 10})
    excl = tmp_path / "exclude.txt"
    excl.write_text("PMC1\nPMC2\n")
    cfg["corpora"]["O"]["exclude_papers"] = str(excl)
    m = pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    header, _ = _pool_rows(g.out_dir(cfg))
    assert m["counts"]["excluded_papers"] == 2
    assert not {"PMC1", "PMC2"} & set(header["eval_papers"])
    assert m["counts"]["eval_papers"] == 4


def test_manifest_is_counts_only_and_pool_has_no_glossary_text(tmp_path: Path) -> None:
    m, out, cfg, texts = _build(tmp_path)
    header, rows = _pool_rows(out)
    ids = {*texts, *header["eval_papers"], *(r["chunk_id"] for r in rows)}
    words = {str(tmp_path), "boat", "drift", "Qb", "PMC"}
    for s in g.string_values(m):
        assert s not in ids and not any(w in s for w in words), s
    raw = (out / "pool-O.jsonl").read_text()
    assert "drift speed" not in raw                       # no definition text in the pool file
    assert "content" not in rows[0]                       # no chunk text either


def test_manipulation_checks_refuse(tmp_path: Path) -> None:
    path, cfg, texts = sup.make_config(tmp_path)
    cfg["corpora"]["O"]["expected_chunks"] += 1
    with pytest.raises(SystemExit, match="manipulation check"):
        pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    cfg["corpora"]["O"]["expected_chunks"] -= 1
    # an arm file built over other texts (a different document version) is refused
    other = {k: v + " changed" for k, v in texts.items()}
    cfg["corpora"]["O"]["arms"]["qwen36p"] = str(sup.write_arm(tmp_path / "bad.jsonl", other))
    with pytest.raises(SystemExit, match="does not resolve"):
        pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    # an arm file with no row for a document is refused, never a silent empty field
    cfg["corpora"]["O"]["arms"]["qwen36p"] = str(sup.write_arm(tmp_path / "part.jsonl", texts, only={"d1"}))
    with pytest.raises(SystemExit, match="does not resolve"):
        pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)


def test_outputs_refused_inside_the_repo(tmp_path: Path) -> None:
    _, cfg, _ = sup.make_config(tmp_path)
    cfg["out_dir"] = str(g.REPO / "python")
    with pytest.raises(SystemExit, match="inside the repository"):
        g.out_dir(cfg)


def test_paper_key_groups_float_units(tmp_path: Path) -> None:
    rows, _ = sup.corpus(2)
    c = g.build_corpus([g.parse_chunk(r, "t") for r in rows], "path_root")
    assert sorted(c.paper_docs) == ["PMC1", "PMC2"]
    assert c.paper_docs["PMC1"] == ["d1", "d1t"]           # the article first, then its units
    assert sorted(g.build_corpus([g.parse_chunk(r, "t") for r in rows], "doc_id").paper_docs) == \
        ["d1", "d1t", "d2", "d2t"]


def test_corpus_reach_reports_local_arms_only(tmp_path: Path) -> None:
    _, cfg, _ = sup.make_config(tmp_path)
    m = pool.build(cfg, "O", g.out_dir(cfg), corpus_reach=True, provenance=lambda: PROV)
    r = m["reach"]["rules"]["corpus"]
    assert r["vs_chunks"] == m["counts"]["vs_chunks_corpus"] and 0 < r["vs_chunks_reached"] < r["vs_chunks"]
    assert m["reach"]["frontierp"]["corpus"] == {"note": "eval papers only (frontier scope)"}
