"""Offline tests for the alias study's added-paper rules (``scripts/eval/alias_corpus.py``)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import alias_corpus as ac  # noqa: E402


def W(wid, **kw):
    kw.setdefault("pmcid", "PMC" + wid[1:])
    return ac.Work(wid=wid, **kw)


def test_seeded_hash_is_sha256_of_pipe_joined_parts():
    want = hashlib.sha256(b"261101|alias|neighbours|order|PMC1").hexdigest()
    assert ac.seeded_hash(ac.SEED_NEIGHBOURS, "order", "PMC1") == want
    assert ac.SEED_RANDOM == {1: "261101|alias|random|1", 2: "261101|alias|random|2"}


def test_normalisers():
    assert ac.norm_pmcid("https://www.ncbi.nlm.nih.gov/pmc/articles/123") == "PMC123"
    assert ac.norm_pmcid("pmc77") == "PMC77" and ac.norm_pmcid("") is None
    assert ac.norm_doi("https://doi.org/10.1/AbC") == "10.1/abc"
    assert ac.norm_title("Short") is None
    assert ac.norm_title("A Long Enough Title: about Things, (2020)!") == ac.norm_title("a long enough title about things 2020")


def test_oa_fulltext_needs_pmcid_or_licensed_oa_pdf():
    api = {"id": "https://openalex.org/W1", "ids": {}, "open_access": {"is_oa": True},
           "best_oa_location": {"pdf_url": "https://x/a.pdf", "license": None, "is_oa": True},
           "locations": [{"is_oa": True, "pdf_url": None, "landing_page_url": "https://x/a", "license": "cc-by"},
                         {"is_oa": False, "pdf_url": "https://y/b.pdf", "license": "cc-by"}]}
    w = ac.work_from_api(api)
    assert not w.oa_fulltext and w.predicted_class == "unavailable"  # no licence / landing only / not OA
    api["locations"].append({"is_oa": True, "pdf_url": "https://z/c.pdf", "license": "cc-by"})
    w = ac.work_from_api(api)
    assert w.oa_fulltext and [loc.url for loc in w.pdf_locations] == ["https://z/c.pdf"]
    api["ids"] = {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9"}
    assert ac.work_from_api(api).predicted_class == "pmc"


def test_work_from_snapshot_row():
    row = {"id": "https://openalex.org/W5", "doi": "https://doi.org/10.2/x", "title": "t", "type": "article",
           "publication_year": 2020, "primary_topic": {"id": "https://openalex.org/T7"},
           "open_access": {"is_oa": True}, "ids": [["openalex", "W5"], ["pmcid", "https://x/PMC5"]],
           "locations": [{"is_oa": True, "pdf_url": "https://p/x.pdf", "license": "cc-by"}],
           "best_oa_location": {"pdf_url": None, "license": None}}
    w = ac.work_from_snapshot(row)
    assert (w.wid, w.pmcid, w.doi, w.topic, w.year) == ("W5", "PMC5", "10.2/x", "T7", 2020)
    assert len(w.pdf_locations) == 1


def test_exclusion_by_every_identity():
    ex = ac.Exclusion()
    ex.add(ac.Work(wid="W1", pmcid="PMC1", doi="10.1/a", title="An excluded paper with a long title here"))
    assert ex.reason(ac.Work(wid="W1")) == "wid"
    assert ex.reason(ac.Work(wid="W2", pmcid="PMC1")) == "pmcid"
    assert ex.reason(ac.Work(wid="W3", doi="10.1/a")) == "doi"
    assert ex.reason(ac.Work(wid="W4", title="An Excluded Paper, with a long title here.")) == "title"
    assert ex.reason(ac.Work(wid="W5", pmcid="PMC5")) is None


def test_pick_references_most_cited_then_seeded_tie():
    refs = [W("W1", cited_by_count=10), W("W2", cited_by_count=50), W("W3", cited_by_count=10),
            W("W4", cited_by_count=99, pmcid=None), W("W5", cited_by_count=7), W("W6", cited_by_count=10),
            W("W7", cited_by_count=1000)]
    ex = ac.Exclusion(wids={"W7"})
    picked, stat = ac.pick_references("PMCs", refs, ex, cap=4)
    ties = sorted(["W1", "W3", "W6"], key=lambda w: ac.seeded_hash(ac.SEED_NEIGHBOURS, "tie", "refs", "PMCs", w))
    assert [w.wid for w in picked] == ["W2"] + ties
    assert stat == {"found": 7, "not_excluded": 6, "eligible": 5, "excluded": 1}


def test_pick_citers_most_recent_then_seeded_tie_ascending():
    cs = [W("W1", pub_date="2024-01-01"), W("W2", pub_date="2025-05-05"), W("W3", pub_date="2024-01-01"),
          W("W4", pub_date="2023-01-01"), W("W5", pub_date=None)]
    picked, _ = ac.pick_citers("PMCs", cs, ac.Exclusion(), cap=3)
    ties = sorted(["W1", "W3"], key=lambda w: ac.seeded_hash(ac.SEED_NEIGHBOURS, "tie", "citers", "PMCs", w))
    assert [w.wid for w in picked] == ["W2"] + ties


def test_attribute_shared_neighbour_goes_to_first_in_seeded_order():
    picks = {"PMCa": [("W1", "ref"), ("W2", "citer")], "PMCb": [("W1", "citer"), ("W3", "ref")]}
    first, second = ac.attribution_order(picks)
    out, dropped = ac.attribute(picks)
    assert dropped == 1
    assert ("W1", picks[first][[w for w, _ in picks[first]].index("W1")][1]) in out[first]
    assert "W1" not in [w for w, _ in out[second]]
    assert sum(len(v) for v in out.values()) == 3


def test_eligible_random_rules():
    ex = ac.Exclusion(wids={"Wx"})
    base = {"topic": "T1", "year": 2020}
    assert ac.eligible_random(W("W1", **base), "T1", 2020, ex, set(), set()) is None
    assert ac.eligible_random(W("W1", topic="T2", year=2020), "T1", 2020, ex, set(), set()) == "topic"
    assert ac.eligible_random(W("W1", topic="T1", year=2024), "T1", 2020, ex, set(), set()) == "year"
    assert ac.eligible_random(W("W1", topic="T1", year=2017), "T1", 2020, ex, set(), set()) is None
    assert ac.eligible_random(W("W1", pmcid=None, **base), "T1", 2020, ex, set(), set()) == "no_oa_fulltext"
    assert ac.eligible_random(W("Wx", **base), "T1", 2020, ex, set(), set()) == "oa1000"
    assert ac.eligible_random(W("W1", **base), "T1", 2020, ex, set(), {"W1"}) == "neighbour"
    assert ac.eligible_random(W("W1", **base), "T1", 2020, ex, {"W1"}, set()) == "citation_link"


def test_random_order_is_seeded_and_draw_specific():
    wids = [f"W{i}" for i in range(20)]
    o1, o2 = ac.random_order(1, "PMCs", wids), ac.random_order(2, "PMCs", wids)
    assert o1 == sorted(wids, key=lambda w: ac.seeded_hash("261101|alias|random|1", "PMCs", w))
    assert o1 != o2 and sorted(o1) == sorted(wids)


def _resolver(table):
    def resolve(wid):
        return table.get(wid, ac.Outcome(ok=True, source=None))
    return resolve


def test_draw_matches_class_quotas_defers_and_skips_failures():
    order = ["W1", "W2", "W3", "W4", "W5", "W6", "W7"]
    kl = {"W1": "jats", "W2": "jats", "W3": "pdf", "W4": "jats", "W5": "pdf", "W6": None, "W7": "pdf"}
    res = {"W1": ac.Outcome(ok=True, source="jats"), "W2": ac.Outcome(ok=True, source="jats"),
           "W3": ac.Outcome(ok=False, source="pdf", excluded_c0=True),
           "W5": ac.Outcome(ok=False, reason="http_404"), "W7": ac.Outcome(ok=True, source="pdf")}
    taken = set()
    r = ac.draw_for_study(order, need_jats=1, need_pdf=1, taken=taken, klass=kl.get, resolve=_resolver(res))
    assert r["jats"] == ["W1"] and r["pdf"] == ["W7"] and r["shortfall"] == 0
    assert dict(r["skipped"]) == {"W3": "c0_screen", "W5": "http_404", "W6": "no_oa_fulltext"}
    assert taken == {"W1", "W7"}
    assert r["deferred"] == 2  # W2, W4: jats quota already full — left for later study papers


def test_draw_fills_short_class_from_the_other_and_reports_shortfall():
    kl = {"W1": "jats", "W2": "jats", "W3": "jats"}
    res = {w: ac.Outcome(ok=True, source="jats") for w in kl}
    r = ac.draw_for_study(["W1", "W2", "W3"], need_jats=1, need_pdf=3, taken=set(), klass=kl.get,
                          resolve=_resolver(res))
    assert r["jats"] == ["W1", "W2", "W3"] and r["pdf"] == []
    assert r["filled_from_other_class"] == 2 and r["shortfall"] == 1 and r["got"] == 3


def test_draw_respects_taken_and_duplicates():
    kl = {"W1": "jats", "W2": "jats", "W3": "jats"}
    seen = {"PMC2"}
    took = []
    r = ac.draw_for_study(["W1", "W2", "W3"], need_jats=1, need_pdf=0, taken={"W1"}, klass=kl.get,
                          resolve=_resolver({}), is_dup=lambda w: "PMC" + w[1:] in seen, on_take=took.append)
    assert r["jats"] == ["W3"] and took == ["W3"] and ("W2", "duplicate") in r["skipped"]


def test_c0_screen_excludes_pdf_only_above_half_a_percent():
    clean = ["plain text\twith tab\nand newline\r"] * 199
    one = clean + ["lost glyph \x02 here"]
    assert not ac.c0_screen(one, "pdf")["excluded"]  # 1 of 200 = 0.5 %: not > 0.5 %
    two = clean[:198] + ["\x02", "\x0c page"]
    s = ac.c0_screen(two, "pdf")
    assert s["excluded"] and s["c0_chunks"] == 2 and s["formfeed_chunks"] == 1
    assert not ac.c0_screen(two, "jats")["excluded"]  # JATS is counted, never excluded
    assert ac.C0_CHARS == {chr(c) for c in list(range(0, 9)) + [0x0B, 0x0C] + list(range(0x0E, 0x20))}


def test_export_row_shape_matches_the_oa_export():
    class C:
        id, doc_id, content, start_char, end_char = "c", "d", "x", 0, 1
        metadata = {"source_path": "PMC1", "title": "T", "content_type": "article"}
    assert list(ac.export_row(C())) == ["chunk_id", "doc_id", "content", "start_char", "end_char", "metadata"]
    assert ac.export_row(C())["metadata"] == {"source_path": "PMC1", "title": "T"}


def test_strip_back_drops_back_keeps_floats_group():
    pytest.importorskip("xml.etree.ElementTree")
    import alias_corpus_run as run

    raw = (b"<article><front><article-meta><title-group><article-title>T</article-title></title-group>"
           b"</article-meta></front><body><p>x</p></body><back><ref-list><ref>r</ref></ref-list></back>"
           b"<floats-group><fig><caption>c</caption></fig></floats-group></article>")
    out = run.strip_back(raw)
    assert b"<back>" not in out and b"<floats-group>" in out and b"<body>" in out
