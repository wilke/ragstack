"""Tests for the notice scanner (#301 — retraction/EoC exclusion for the OA plane).

The dangerous mistakes are all classification mistakes: matching the attribute
of a *related* article instead of the root tag (which brands corrected
research articles as notices), excluding an EoC original as if it were
retracted, or silently defaulting an unreadable file. Each is pinned here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import scan_notices as sn  # noqa: E402


def _article(pmcid: str, atype: str, body: str = "<body><p>text</p></body>",
             related: str = "") -> str:
    return f"""<article xmlns:ns1="http://www.w3.org/1999/xlink" article-type="{atype}">
<front><article-meta>
<article-id pub-id-type="pmcid">{pmcid}</article-id>
<title-group><article-title>T {pmcid}</article-title></title-group>
{related}
</article-meta></front>{body}</article>"""


def _related(ra_type: str, target: str) -> str:
    return (f'<related-article related-article-type="{ra_type}" '
            f'ext-link-type="pmc" ns1:href="{target}"/>')


def _corpus(tmp_path: Path, articles: dict[str, str]) -> Path:
    root = tmp_path / "corpus" / "clean" / "aa" / "bb"
    root.mkdir(parents=True)
    for pmcid, xml in articles.items():
        (root / f"{pmcid}.xml").write_text(xml)
    return tmp_path / "corpus"


def _run(tmp_path: Path, corpus: Path, extra: list[str] | None = None) -> tuple[list[dict], dict]:
    out = tmp_path / "scan"
    rc = sn.main(["--corpus", str(corpus), "--out", str(out),
                  "--workers", "1", *(extra or [])])
    assert rc == 0
    exclusions = [json.loads(x) for x in
                  (out / "exclusions.jsonl").read_text().splitlines()]
    report = json.loads((out / "report.json").read_text())
    return exclusions, report


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def test_root_tag_only_never_the_related_article_attribute(tmp_path):
    """The substring trap: a corrected RESEARCH article carries
    related-article-type="correction-forward". A scan keyed on anything but the
    root tag classifies it as a correction notice — the exact mistake that
    produced a wrong type distribution once already."""
    corpus = _corpus(tmp_path, {
        "PMC1": _article("PMC1", "research-article",
                         related=_related("correction-forward", "PMC2")),
    })
    path = str(corpus / "clean" / "aa" / "bb" / "PMC1.xml")
    assert sn.root_article_type(path) == "research-article"
    exclusions, report = _run(tmp_path, corpus)
    assert exclusions == []
    assert report["article_types"] == {"research-article": 1}


def test_retraction_notice_and_its_target_are_both_excluded(tmp_path):
    corpus = _corpus(tmp_path, {
        "PMC10": _article("PMC10", "research-article"),          # the retracted one
        "PMC11": _article("PMC11", "retraction",
                          related=_related("retracted-article", "PMC10")),
        "PMC12": _article("PMC12", "research-article"),          # innocent bystander
    })
    exclusions, report = _run(tmp_path, corpus)
    got = {(r["pmcid"], r["reason"]) for r in exclusions}
    assert got == {("PMC11", "notice:retraction"), ("PMC10", "retracted-original")}
    assert next(r for r in exclusions if r["pmcid"] == "PMC10")["via"] == "PMC11"
    assert report["excluded_by_reason"] == {"notice:retraction": 1,
                                            "retracted-original": 1}


def test_eoc_notice_dropped_but_its_original_is_kept_and_listed(tmp_path):
    """An expression of concern is a warning, not a retraction — the original
    stays in the corpus and is listed in the report for visibility."""
    corpus = _corpus(tmp_path, {
        "PMC20": _article("PMC20", "research-article"),
        "PMC21": _article("PMC21", "expression-of-concern",
                          related=_related("object-of-concern", "PMC20")),
    })
    exclusions, report = _run(tmp_path, corpus)
    assert [r["pmcid"] for r in exclusions] == ["PMC21"]
    assert report["eoc_originals_kept"] == [{"pmcid": "PMC20", "via": "PMC21"}]


def test_corrections_are_kept_both_halves(tmp_path):
    """Corrected original AND correction notice stay: the original is valid, the
    notice carries the fixed values."""
    corpus = _corpus(tmp_path, {
        "PMC30": _article("PMC30", "research-article",
                          related=_related("correction-forward", "PMC31")),
        "PMC31": _article("PMC31", "correction",
                          related=_related("corrected-article", "PMC30")),
    })
    exclusions, _ = _run(tmp_path, corpus)
    assert exclusions == []


def test_editorial_matter_is_excluded_by_default(tmp_path):
    corpus = _corpus(tmp_path, {
        "PMC40": _article("PMC40", "editorial"),
        "PMC41": _article("PMC41", "news"),
        "PMC42": _article("PMC42", "book-review"),
        "PMC43": _article("PMC43", "review-article"),
    })
    exclusions, _ = _run(tmp_path, corpus)
    assert {r["pmcid"] for r in exclusions} == {"PMC40", "PMC41", "PMC42"}


def test_drop_types_flag_overrides_the_default(tmp_path):
    corpus = _corpus(tmp_path, {
        "PMC50": _article("PMC50", "editorial"),
        "PMC51": _article("PMC51", "retraction",
                          related=_related("retracted-article", "PMC50")),
    })
    exclusions, _ = _run(tmp_path, corpus, ["--drop-types", "retraction"])
    got = {(r["pmcid"], r["reason"]) for r in exclusions}
    # editorial kept (not in the override), PMC50 still excluded — but as the
    # retraction's target, which the override cannot disable.
    assert got == {("PMC51", "notice:retraction"), ("PMC50", "retracted-original")}


def test_target_outside_the_corpus_is_reported_not_excluded(tmp_path):
    corpus = _corpus(tmp_path, {
        "PMC60": _article("PMC60", "retraction",
                          related=_related("retracted-article", "PMC99999")),
    })
    exclusions, report = _run(tmp_path, corpus)
    assert [r["pmcid"] for r in exclusions] == ["PMC60"]
    assert report["retraction_targets_not_in_corpus"] == [
        {"pmcid": "PMC99999", "via": "PMC60"}]


def test_unreadable_file_is_reported_never_defaulted(tmp_path):
    corpus = _corpus(tmp_path, {"PMC70": _article("PMC70", "research-article")})
    (corpus / "clean" / "aa" / "bb" / "PMC71.xml").write_text("<article no closing")
    exclusions, report = _run(tmp_path, corpus)
    assert exclusions == []
    assert report["n_unreadable"] == 1
    assert report["unreadable"][0].endswith("PMC71.xml")


def test_root_type_found_even_when_head_window_misses(tmp_path):
    """A root tag pushed past the fast-path window falls back to a real parse."""
    padding = "<?xml version='1.0'?><!-- " + "x" * 10000 + " -->\n"
    xml = padding + _article("PMC80", "retraction")
    corpus = _corpus(tmp_path, {})
    (corpus / "clean" / "aa" / "bb" / "PMC80.xml").write_text(xml)
    path = str(corpus / "clean" / "aa" / "bb" / "PMC80.xml")
    assert sn.root_article_type(path) == "retraction"


def test_exclusions_file_is_planner_compatible(tmp_path):
    """plan_shards --exclude reads any JSONL with a pmcid field — every row must
    carry one, and ONLY excludable rows may be in the file (the planner excludes
    everything it finds there)."""
    corpus = _corpus(tmp_path, {
        "PMC90": _article("PMC90", "research-article"),
        "PMC91": _article("PMC91", "retraction",
                          related=_related("retracted-article", "PMC90")),
        "PMC92": _article("PMC92", "expression-of-concern",
                          related=_related("object-of-concern", "PMC93")),
        "PMC93": _article("PMC93", "research-article"),
    })
    exclusions, _ = _run(tmp_path, corpus)
    assert all("pmcid" in r for r in exclusions)
    # PMC93 (EoC original) must NOT be in the exclusions file, or the planner
    # would drop a kept article.
    assert "PMC93" not in {r["pmcid"] for r in exclusions}

    import plan_shards
    got = plan_shards.read_pmcid_set(str(tmp_path / "scan" / "exclusions.jsonl"))
    assert got == {"PMC90", "PMC91", "PMC92"}
