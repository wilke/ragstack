"""Unit tests for scripts/eval/alias_abbrev.py (alias study, abbreviation glossary).

Synthetic text only: no export, no arm file, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import _glossbench_r2 as g  # noqa: E402
import alias_abbrev as aa  # noqa: E402

COMMON = frozenset({"aids", "cat", "can", "set"})


def defs(text: str) -> dict[str, tuple[str, str]]:
    return {d.alias: (d.lf, d.pattern) for d in aa.find_definitions(text)}


# ---------------------------------------------------------------- definition patterns
def test_p1_long_form_then_abbreviation() -> None:
    assert defs("We ran the polymerase chain reaction (PCR) twice.") == {
        "PCR": ("polymerase chain reaction", "P1")}


def test_p1_takes_the_shortest_aligned_long_form() -> None:
    d = defs("Cells were grown in medium with 10% fetal bovine serum (FBS; Sigma).")
    assert d["FBS"] == ("fetal bovine serum", "P1")


def test_p1_does_not_cross_a_clause_boundary() -> None:
    # 'cancer, tumour' — the comma cuts the window, so no long form for CTR exists
    assert "CTR" not in defs("in cancer, tumour response (CTR) was measured")


def test_p1_plural_abbreviation_is_normalised() -> None:
    assert defs("Mesenchymal stem cells (MSCs) were isolated.")["MSC"][0] == "Mesenchymal stem cells"


def test_p1_needs_space_before_parenthesis() -> None:
    assert defs("polymerase chain reaction(PCR) run") == {}


def test_p2_abbreviation_then_long_form() -> None:
    assert defs("treated with DPP4 (dipeptidyl peptidase 4) inhibitors") == {
        "DPP4": ("dipeptidyl peptidase 4", "P2")}


def test_p2_rejects_non_expansions() -> None:
    assert defs("PCR (Bio-Rad, Hercules) was used") == {}
    assert defs("PCR (e.g. qPCR assays) was used") == {}


def test_long_form_over_eight_words_is_rejected() -> None:
    lf = "alpha beta gamma delta epsilon zeta eta theta iota"
    assert not aa.lf_ok("ABGDEZETI", lf)
    assert aa.lf_ok("ABGDEZET", "alpha beta gamma delta epsilon zeta eta theta")


# ---------------------------------------------------------------- letter alignment
@pytest.mark.parametrize("sf,lf", [
    ("PCR", "polymerase chain reaction"),
    ("DNA", "deoxyribonucleic acid"),
    ("mRNA", "messenger RNA"),
    ("ROI", "region of interest"),
    ("MRSA", "methicillin-resistant Staphylococcus aureus"),
    ("IL6", "interleukin 6"),
    ("LPS", "lipopolysaccharide"),
])
def test_alignment_accepts(sf: str, lf: str) -> None:
    assert aa.aligns(sf, lf)


@pytest.mark.parametrize("sf,lf", [
    ("ROS", "reactive species"),          # no 'o' to match
    ("PCR", "chain reaction"),            # first letter does not start the long form
    ("BMI", "body mass index score"),     # 'score' contributes no letter
    ("TC", "of total cholesterol"),       # long form starts with a function word
])
def test_alignment_rejects(sf: str, lf: str) -> None:
    assert not aa.aligns(sf, lf)


def test_one_word_long_form_must_be_long() -> None:
    assert not aa.lf_ok("HB", "heme")
    assert aa.lf_ok("LPS", "lipopolysaccharide")


# ---------------------------------------------------------------- exclusions
@pytest.mark.parametrize("sf,why", [
    ("A1", "single_letter"),
    ("123", "number"),
    ("Fig", "casing"),
    ("mM", "unit"),
    ("kDa", "unit"),
    ("II", "roman_numeral"),
    ("AIDS", "common_word"),
    ("CAT", "common_word"),
    ("OR", "common_word"),
    ("TNFα", "charset"),
])
def test_exclusions(sf: str, why: str) -> None:
    assert aa.sf_exclusion(sf, COMMON) == why


@pytest.mark.parametrize("sf", ["PCR", "mAb", "IL6", "CT", "HbA1C"])
def test_kept(sf: str) -> None:
    assert aa.sf_exclusion(sf, COMMON) is None


# ---------------------------------------------------------------- entries, use rule, recount
def _row(cid: str, paper: str, text: str) -> g.ChunkRow:
    return g.ChunkRow(cid, f"{paper}-{cid}", text, 0, len(text), f"{paper}#sec", "")


def _corpus(rows: list[g.ChunkRow]) -> aa.Corpus:
    return aa.build_corpus(rows, lambda r: g.paper_of(r, "path_root"))


def test_used_elsewhere_is_recorded_not_required() -> None:
    rows = [
        _row("c1", "P1", "We measured the body mass index (BMI) of all subjects."),
        _row("c2", "P1", "Subjects with a high BMI were excluded from the cohort."),
        _row("c3", "P1", "Fluorescence was imaged by scanning electron microscopy (SEM) here."),
    ]
    ents, stats = aa.build_abbrev_entries(_corpus(rows), COMMON)
    by = {e["alias"]: e for e in ents}
    assert by["BMI"]["def_chunk_id"] == "c1"
    assert by["BMI"]["use_chunk_ids"] == ["c2"] and by["BMI"]["used_elsewhere"] is True
    # defined but never used in another chunk: still an entry, flagged
    assert by["SEM"]["use_chunk_ids"] == [] and by["SEM"]["used_elsewhere"] is False
    assert stats["entries"] == 2 and stats["entries_used_elsewhere"] == 1


def test_use_rule_skips_every_definition_chunk() -> None:
    rows = [
        _row("c1", "P1", "The body mass index (BMI) was high."),
        _row("c2", "P1", "Again, body mass index (BMI) is defined here."),
    ]
    (e,), _ = aa.build_abbrev_entries(_corpus(rows), COMMON)
    assert e["def_chunk_ids"] == ["c1", "c2"] and e["used_elsewhere"] is False


def test_conflicting_long_forms_drop_the_alias() -> None:
    rows = [
        _row("c1", "P1", "We used principal component analysis (PCA) first."),
        _row("c2", "P1", "Then the posterior cerebral artery (PCA) was imaged."),
    ]
    ents, stats = aa.build_abbrev_entries(_corpus(rows), COMMON)
    assert ents == [] and stats["dropped_conflicting_long_forms"] == 1


def test_excluded_abbreviation_never_enters() -> None:
    rows = [_row("c1", "P1", "Patients with acquired immune deficiency syndrome (AIDS) were seen.")]
    ents, stats = aa.build_abbrev_entries(_corpus(rows), COMMON)
    assert ents == [] and stats["excluded_instances"] == {"common_word": 1}


def test_g_candidate_strict_rule_and_c_candidate() -> None:
    rows = [
        _row("c1", "P1", "We measured the body mass index (BMI) of all subjects."),
        _row("c2", "P1", "Subjects with a high BMI were excluded."),              # G: alias, no primary
        _row("c3", "P1", "A high BMI and a low index were both rare."),           # not G: 'index' (>= 5 chars)
        _row("c4", "P1", "The body mass index was computed from the height."),    # C: primary literal
    ]
    corpus = _corpus(rows)
    ents, _ = aa.build_abbrev_entries(corpus, COMMON)
    G, loose = aa.g_candidates(corpus, ents)
    assert [x["chunks"] for x in G["P1"]] == [["c2"]]
    assert loose["P1"] == {"c2", "c3"}
    assert aa.c_candidates(corpus, ents, loose) == {"P1": 2}   # c1 and c4


def test_merge_records_kind() -> None:
    sym = [{"paper": "P", "alias": "BMI", "primary": "body mass index", "kind": "symbol", "extractor": "llm"},
           {"paper": "P", "alias": "σ", "primary": "stress", "kind": "symbol", "extractor": "rules"}]
    abb = [{"paper": "P", "alias": "BMI", "primary": "body mass index", "kind": "abbreviation"},
           {"paper": "P", "alias": "SEM", "primary": "scanning electron microscopy", "kind": "abbreviation"}]
    kinds = {e["alias"]: e["kind"] for e in aa.merge(sym, abb)}
    assert kinds == {"BMI": "both", "σ": "symbol", "SEM": "abbreviation"}


def test_collision_counts_different_long_forms_only() -> None:
    abb = [{"paper": "A", "alias": "PCA", "primary": "principal component analysis"},
           {"paper": "B", "alias": "PCA", "primary": "posterior cerebral artery"},
           {"paper": "A", "alias": "BMI", "primary": "body mass index"},
           {"paper": "B", "alias": "BMI", "primary": "Body Mass Index"}]
    c = aa.collision(abb)
    assert c["aliases_in_ge2_papers"] == 2
    assert c["aliases_in_ge2_papers_with_different_long_form"] == 1
    assert c["entries_whose_alias_collides"] == 2


# ---------------------------------------------------------------- yield
def test_paper_yield_edges() -> None:
    assert aa.paper_yield(2, 1.0) == (1.0, 2.0)
    assert aa.paper_yield(1, 1.0)[0] == 0.0
    s, e = aa.paper_yield(10, 0.5)
    assert e <= 6 and 0 < s < 1


def test_yield_cost_unreachable_and_reachable() -> None:
    assert aa.yield_cost([2] * 10, 0.2)["reachable"] is False
    r = aa.yield_cost([6] * 1000, 0.5, target=100)
    assert r["reachable"] and r["papers_to_attempt"] * r["usable_rate_per_paper"] >= 100
    assert r["calls"] == round(r["jobs"] * aa.CALLS_PER_JOB, -0) or abs(r["calls"] - r["jobs"] * aa.CALLS_PER_JOB) < 2
