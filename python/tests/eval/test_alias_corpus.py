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


def test_snapshot_rows_get_pmcid_from_the_pmc_ids_map():
    import alias_corpus_run as run

    run._PMC_MAP.clear()
    run._PMC_MAP.update({"pmid": {"111": "PMC9"}, "doi": {"10.5/q": "PMC8"}})
    by_pmid = run._with_pmcid({"ids": [["pmid", "https://pubmed.ncbi.nlm.nih.gov/111"]], "doi": None})
    by_doi = run._with_pmcid({"ids": [], "doi": "https://doi.org/10.5/Q"})
    none = run._with_pmcid({"ids": [["pmid", "https://pubmed.ncbi.nlm.nih.gov/222"]], "doi": None})
    assert ac.work_from_snapshot(by_pmid).pmcid == "PMC9" and ac.work_from_snapshot(by_doi).pmcid == "PMC8"
    assert ac.work_from_snapshot(none).pmcid is None


def test_memoised_pdf_outcome_is_stale_once_a_pmcid_is_known():
    import alias_corpus_run as run

    pdf_only = {"ok": True, "route": "pdf", "attempts": [{"route": "pdf"}]}
    assert run.Builder._stale(ac.Work(wid="W1", pmcid="PMC1"), pdf_only)
    assert not run.Builder._stale(ac.Work(wid="W1"), pdf_only)
    tried = {"ok": True, "route": "pdf", "attempts": [{"route": "jats", "reason": "pmc_not_in_oa_subset"}]}
    assert not run.Builder._stale(ac.Work(wid="W1", pmcid="PMC1"), tried)


# ------------------------------------------------------------------ v2: walk-down, type filter, matched draws


def test_type_filter_keeps_only_articles_and_reviews():
    assert ac.type_class("article") == "article" and ac.type_class("review") == "review"
    for t in ("preprint", "letter", "editorial", "erratum", "retraction", "paratext", "peer-review", None, ""):
        assert ac.type_class(t) is None


def test_rank_lists_extend_the_v1_picks():
    excl = ac.Exclusion()
    refs = [W(f"W{i}", cited_by_count=100 - i, type="article") for i in range(12)]
    assert [w.wid for w in ac.rank_references("PMC1", refs, excl)[:5]] == \
        [w.wid for w in ac.pick_references("PMC1", refs, excl)[0]]
    cits = [W(f"W{i}", pub_date=f"2020-01-{i + 1:02d}", type="article") for i in range(12)]
    assert [w.wid for w in ac.rank_citers("PMC1", cits, excl)[:5]] == \
        [w.wid for w in ac.pick_citers("PMC1", cits, excl)[0]]


def test_walk_down_skips_type_failures_and_c0_until_cap():
    ranked = [W("W1", type="article"), W("W2", type="erratum"), W("W3", type="article"), W("W4", type="review"),
              W("W5", type="article"), W("W6", type="article"), W("W7", type="article"), W("W8", type="article")]
    outs = {"W1": ac.Outcome(ok=True, source="jats"), "W3": ac.Outcome(ok=False, reason="robots_disallow"),
            "W4": ac.Outcome(ok=False, excluded_c0=True), "W5": ac.Outcome(ok=True), "W6": ac.Outcome(ok=True),
            "W7": ac.Outcome(ok=True), "W8": ac.Outcome(ok=True)}
    calls = []

    def resolve(wid):
        calls.append(wid)
        return outs[wid]

    r = ac.walk_down(ranked, 3, resolve)
    assert r["picked"] == ["W1", "W5", "W6"] and not r["exhausted"]
    assert r["skipped"] == [("W2", "type:erratum"), ("W3", "robots_disallow"), ("W4", "c0_screen")]
    assert "W2" not in calls and "W7" not in calls
    r = ac.walk_down(ranked[:4], 3, resolve)
    assert r["exhausted"] and r["picked"] == ["W1"]


def test_next_to_resolve_never_over_fetches():
    ranked = [W(f"W{i}", type="article") for i in range(10)]
    known = {"W0": ac.Outcome(ok=True), "W1": ac.Outcome(ok=False)}
    assert ac.next_to_resolve(ranked, 3, known.get) == ["W2", "W3"]
    known.update({"W2": ac.Outcome(ok=True), "W3": ac.Outcome(ok=True)})
    assert ac.next_to_resolve(ranked, 3, known.get) == []


def test_length_bands():
    cuts = ac.length_cuts(list(range(1, 101)), 5)
    assert cuts == [21.0, 41.0, 61.0, 81.0]
    assert [ac.band_of(n, cuts) for n in (1, 20, 21, 60, 81, 500)] == [0, 0, 1, 2, 4, 4]


def _matched(order, quota, info, max_resolves=None, taken=None):
    """info: wid -> (type, klass, Outcome)."""
    return ac.draw_matched(order, quota, taken if taken is not None else set(),
                           type_of=lambda w: info[w][0], klass=lambda w: info[w][1],
                           resolve=lambda w: info[w][2], band=lambda n: 0 if n < 10 else 1,
                           max_resolves=max_resolves)


def test_draw_matched_fills_cells_strictly_and_rejects_for_band():
    info = {"A": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=50)),   # band 1, cell full
            "B": ("letter", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5)),
            "C": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5)),
            "D": ("review", "pdf", ac.Outcome(ok=True, source="pdf", n_chunks=30)),
            "E": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=40))}
    quota = {("article", 0, "jats"): 1, ("article", 1, "jats"): 1, ("review", 1, "pdf"): 1}
    r = _matched(["A", "B", "C", "D", "E"], quota, info)
    assert [t["wid"] for t in r["took"]] == ["A", "C", "D"] and r["shortfall"] == 0
    assert all(t["level"] == "strict" for t in r["took"]) and r["skipped"]["type"] == 1
    quota = {("article", 0, "jats"): 2}
    r = _matched(["A", "C", "E"], quota, info)
    # A, E are band 1: set aside, then the relaxation (source -> source+type -> any) takes one of them at "source+type"? no:
    # band differs, so only level "any" matches
    assert r["skipped"]["rejected_for_band"] == 2
    assert [(t["wid"], t["level"]) for t in r["took"]] == [("C", "strict"), ("A", "any")]


def test_draw_matched_relaxes_source_before_type_and_respects_budget():
    info = {"P": ("article", "pdf", ac.Outcome(ok=True, source="pdf", n_chunks=5)),
            "J": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5)),
            "R": ("review", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5))}
    quota = {("review", 0, "pdf"): 1, ("article", 0, "jats"): 1}
    r = _matched(["J", "R", "P"], quota, info)
    assert [(t["wid"], t["level"]) for t in r["took"]] == [("J", "strict"), ("R", "source")]
    taken = set()
    r = _matched(["J", "R", "P"], {("article", 0, "pdf"): 1, ("article", 0, "jats"): 1}, info, max_resolves=1,
                 taken=taken)
    # the strict pass stops at its budget; the relaxation (own budget) fetches P, which fits exactly
    assert r["budget_hit"] and [(t["wid"], t["level"]) for t in r["took"]] == [("J", "strict"), ("P", "strict")]
    assert taken == {"J", "P"} and r["resolves_by_phase"] == {"strict": 1, "relax": 1}
    r = _matched(["J", "R", "P"], {("article", 0, "pdf"): 1, ("article", 0, "jats"): 1}, info, max_resolves=0)
    assert r["got"] == 0 and r["shortfall"] == 2


def test_random_order_v2_seeds():
    assert ac.SEED_RANDOM_V2 == {1: "261101|alias|random-1-v2", 2: "261101|alias|random-2-v2"}
    ws = [f"W{i}" for i in range(20)]
    assert ac.random_order(1, "PMC1", ws, seeds=ac.SEED_RANDOM_V2) != ac.random_order(1, "PMC1", ws)


def test_v3_cuts_split_the_top_band_at_80():
    assert ac.v3_cuts([20.0, 27.0, 34.0, 46.0]) == [20.0, 27.0, 34.0, 46.0, 80.0]
    assert [ac.band_of(n, ac.v3_cuts([20, 27, 34, 46])) for n in (45, 46, 79, 80, 300)] == [3, 4, 4, 5, 5]
    with pytest.raises(ValueError):
        ac.v3_cuts([20.0, 90.0])


def _take(wid, t, b, s, level, fc):
    return {"wid": wid, "type": t, "band": b, "source": s, "level": level, "for_cell": list(fc)}


def test_carry_over_keeps_unmoved_takes_and_releases_moved_and_band_relaxed():
    cuts3 = [20.0, 27.0, 34.0, 46.0, 80.0]
    nch = {"A": 10, "B": 60, "C": 90, "D": 95, "E": 50, "F": 12, "G": 30}
    took = [_take("A", "article", 0, "jats", "strict", ("article", 0, "jats")),
            _take("B", "article", 4, "jats", "strict", ("article", 4, "jats")),     # 60: stays band 4
            _take("C", "article", 4, "jats", "strict", ("article", 4, "jats")),     # 90: -> band 5
            _take("D", "article", 4, "jats", "strict", ("article", 4, "jats")),     # 95: band 5, no room left
            _take("E", "review", 4, "jats", "source", ("review", 4, "pdf")),        # source-relaxed, stays band 4
            _take("F", "article", 0, "pdf", "any", ("article", 3, "pdf")),          # band relaxed: re-drawn
            _take("G", "article", 2, "jats", "source+type", ("review", 2, "pdf"))]
    # neighbours: band-4 article/jats cell split 1 (46-79) + 2 (>=80) ... but only one >=80 slot here
    quota = {("article", 0, "jats"): 1, ("article", 4, "jats"): 2, ("article", 5, "jats"): 1,
             ("review", 4, "pdf"): 1, ("article", 3, "pdf"): 1, ("review", 2, "pdf"): 1}
    r = ac.carry_over(took, quota, lambda n: ac.band_of(n, cuts3), nch.__getitem__)
    kept = {t["wid"]: (t["level"], tuple(t["for_cell"])) for t in r["kept"]}
    assert kept == {"A": ("strict", ("article", 0, "jats")), "B": ("strict", ("article", 4, "jats")),
                    "C": ("strict", ("article", 5, "jats")), "E": ("source", ("review", 4, "pdf")),
                    "G": ("source+type", ("review", 2, "pdf"))}
    assert {t["wid"]: t["why"] for t in r["released"]} == {"D": "band_assignment_changed", "F": "relaxed_on_band"}
    # one band-4 slot (D's 95 chunks cannot fill it) and F's slot stay open for the re-draw
    assert r["open"] == {("article", 3, "pdf"): 1, ("article", 4, "jats"): 1}
    assert sum(r["open"].values()) == len(r["released"])


def test_carry_over_refuses_an_inconsistent_take():
    bad = [_take("A", "article", 1, "jats", "strict", ("article", 0, "jats"))]
    with pytest.raises(ValueError):
        ac.carry_over(bad, {("article", 0, "jats"): 1}, lambda n: 0, lambda w: 5)


def test_strict_shortfall_counts_non_strict_slots_per_cell():
    quota = {("article", 0, "jats"): 2, ("review", 5, "pdf"): 1}
    took = [_take("A", "article", 0, "jats", "strict", ("article", 0, "jats")),
            _take("B", "article", 0, "pdf", "source", ("article", 0, "jats")),
            _take("C", "review", 5, "pdf", "strict", ("review", 5, "pdf"))]
    assert ac.strict_shortfall(quota, took) == {("article", 0, "jats"): 1}
    assert ac.RELAX_ORDER == ("strict", "source", "source+type", "any") and ac.BAND_RELAXED == {"any"}


# ------------------------------------------------------------------ v4: nearest-band fill, repetition screen


def test_nearest_band_fill_orders_by_band_distance_then_type_then_source_then_rank():
    slot = ("article", 3, "jats")
    fetched = {"far": ("article", 0, "jats"),       # distance 3
               "near_rev_pdf": ("review", 2, "pdf"),  # distance 1, type and source differ
               "near_art_pdf": ("article", 4, "pdf"),  # distance 1, type matches
               "near_art_jats_late": ("article", 2, "jats"),  # distance 1, type and source match, later rank
               "near_art_jats": ("article", 4, "jats")}       # distance 1, type and source match, earlier rank
    rank = {"far": 0, "near_rev_pdf": 1, "near_art_pdf": 2, "near_art_jats": 3, "near_art_jats_late": 4}
    taken: set[str] = set()
    out = ac.nearest_band_fill({slot: 5}, fetched, taken, rank)
    assert [w for w, *_ in out] == ["near_art_jats", "near_art_jats_late", "near_art_pdf", "near_rev_pdf", "far"]
    assert [d for *_, d in out] == [1, 1, 1, 1, 3] and taken == set(fetched)
    # taken and duplicate candidates are never picked; a slot with nothing left stays open
    taken = {"near_art_jats"}
    picked: list[str] = []
    out = ac.nearest_band_fill({slot: 1, ("review", 0, "pdf"): 1}, fetched, taken, rank,
                               is_dup=lambda w: w == "near_art_jats_late", on_pick=lambda w, *_: picked.append(w))
    # global order: far -> review|0|pdf (distance 0) first, then near_art_pdf (type matches) -> slot
    assert [(w, s) for w, _, s, _ in out] == [("far", ("review", 0, "pdf")), ("near_art_pdf", slot)]
    assert picked == ["far", "near_art_pdf"]
    assert ac.nearest_band_fill({slot: 1}, {}, set(), {}) == []


def test_nearest_band_fill_is_smallest_distance_first_over_all_pairs():
    # slots in bands 5 and 3, candidates in bands 4 and 0. Pairs: (5, b4) = 1, (3, b4) = 1, (3, b0) = 3,
    # (5, b0) = 5. Smallest distance first over all pairs: the two distance-1 pairs tie on distance, type
    # and source, so the slot earlier in the sorted cell order (band 3) gets b4; b0 then fills band 5.
    # The result is the same whatever order the slots are listed in. (Total 6: the rule is greedy, not
    # the minimum-total assignment, which would be 5 <- b4, 3 <- b0 = 4.)
    fetched = {"b4": ("article", 4, "jats"), "b0": ("article", 0, "jats")}
    rank = {"b4": 0, "b0": 1}
    for quota in ({("article", 5, "jats"): 1, ("article", 3, "jats"): 1},
                  {("article", 3, "jats"): 1, ("article", 5, "jats"): 1}):
        out = ac.nearest_band_fill(quota, fetched, set(), rank)
        assert [(w, s[1], d) for w, _, s, d in out] == [("b4", 3, 1), ("b0", 5, 5)]
    # a smaller distance beats the slot order: a band-5 candidate goes to the band-5 slot first
    out = ac.nearest_band_fill({("article", 5, "jats"): 1, ("article", 3, "jats"): 1},
                               {"b4": ("article", 4, "jats"), "b5": ("article", 5, "jats")}, set(), {"b4": 0, "b5": 1})
    assert [(w, s[1], d) for w, _, s, d in out] == [("b5", 5, 0), ("b4", 3, 1)]
    # full ties are taken in slot order before seeded order (this only orders the takes: tied pairs that
    # share a candidate differ only in slot, tied pairs that share a slot only in candidate)
    out = ac.nearest_band_fill({("article", 3, "jats"): 1, ("article", 5, "jats"): 1},
                               {"x6": ("article", 6, "jats"), "y2": ("article", 2, "jats")}, set(), {"x6": 0, "y2": 1})
    assert [(w, s[1]) for w, _, s, _ in out] == [("y2", 3), ("x6", 5)]
    # type match beats slot order: a review candidate at the same distance goes to the review slot
    out = ac.nearest_band_fill({("article", 3, "jats"): 1, ("review", 5, "jats"): 1},
                               {"r4": ("review", 4, "jats"), "a0": ("article", 0, "jats")}, set(), {"r4": 0, "a0": 1})
    assert [(w, s) for w, _, s, _ in out] == [("r4", ("review", 5, "jats")), ("a0", ("article", 3, "jats"))]


def _info_matched(order, quota, info, any_rule, calls, max_resolves=None):
    def resolve(w):
        calls.append(w)
        return info[w][2]
    return ac.draw_matched(order, quota, set(), type_of=lambda w: info[w][0], klass=lambda w: info[w][1],
                           resolve=resolve, band=lambda n: n // 10, max_resolves=max_resolves, any_rule=any_rule)


def test_draw_matched_nearest_any_takes_nearest_fetched_band_without_fetching():
    # slot article|5|jats; A (band 0) and B (band 4) are fetched in the strict pass and passed over
    # (rejected_for_band); C (review/pdf, band 3) only in the relaxation. Only level "any" can fill the slot.
    info = {"A": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5)),
            "B": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=45)),
            "C": ("review", "pdf", ac.Outcome(ok=True, source="pdf", n_chunks=35)),
            "D": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=41))}
    quota = {("article", 5, "jats"): 1}
    calls: list[str] = []
    first = _info_matched(["A", "C", "B"], quota, info, "first", calls)
    assert [(t["wid"], t["level"]) for t in first["took"]] == [("A", "any")]   # v3: first passed over
    first_calls = list(calls)
    calls.clear()
    near = _info_matched(["A", "C", "B"], quota, info, "nearest", calls)
    assert [(t["wid"], t["level"], t["band_distance"]) for t in near["took"]] == [("B", "any", 1)]
    assert calls == first_calls and near["shortfall"] == 0   # no fetch beyond the walk's
    # ties at the same distance: type match beats source match, then seeded order
    info2 = {"X": ("review", "pdf", ac.Outcome(ok=True, source="pdf", n_chunks=45)),     # type differs
             "Y": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=41)),  # source differs
             "Z": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=49))}  # same as Y, later
    near = _info_matched(["X", "Y", "Z"], {("article", 5, "pdf"): 1}, info2, "nearest", [])
    assert [(t["wid"], t["band_distance"]) for t in near["took"]] == [("Y", 1)]
    near = _info_matched(["X", "Z", "Y"], {("article", 5, "pdf"): 1}, info2, "nearest", [])
    assert [t["wid"] for t in near["took"]] == ["Z"]
    with pytest.raises(ValueError):
        _info_matched(["A"], quota, info, "closest", calls)


def test_draw_matched_reports_fetched_candidates_with_their_level():
    info = {"A": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=5)),
            "B": ("article", "jats", ac.Outcome(ok=False, reason="http_404")),
            "C": ("review", "pdf", ac.Outcome(ok=True, source="pdf", n_chunks=50))}
    out: dict = {}
    ac.draw_matched(["A", "B", "C"], {("article", 5, "jats"): 1}, set(), type_of=lambda w: info[w][0],
                    klass=lambda w: info[w][1], resolve=lambda w: info[w][2], band=lambda n: n // 10,
                    fetched_out=out)
    assert out == {"A": (("article", 0, "jats"), "strict"), "C": (("review", 5, "pdf"), "source+type")}


def test_repetition_screen_counts_distinct_texts_after_nfc_and_whitespace_collapse():
    assert ac.chunk_text_key(" cafe\u0301  au\n\tlait ") == ac.chunk_text_key("caf\u00e9 au lait")
    base = [f"chunk {i}" for i in range(9)]
    # 10 chunks, one repeated: 9 distinct = 0.90, not excluded (below 0.90 is)
    s = ac.repetition_screen(base + ["chunk 0"])
    assert (s["n_chunks"], s["distinct"], s["ratio"], s["excluded"]) == (10, 9, 0.9, False)
    # 10 chunks, three spellings of one text (precomposed vs decomposed, whitespace): 8 distinct = 0.80, excluded
    s = ac.repetition_screen(base[:7] + ["caf\u00e9 x", "cafe\u0301 x", "cafe\u0301\n  x"])
    assert (s["n_chunks"], s["distinct"], s["excluded"]) == (10, 8, True)
    assert ac.repetition_screen(base[:7] + ["caf\u00e9 x", "caf\u00e9 y", "cafe\u0301\n  x"])["distinct"] == 9
    assert ac.repetition_screen([])["excluded"] is False
    assert ac.REPETITION_THRESHOLD == 0.90


def test_screen_outcome_rejects_only_accepted_repetitive_papers():
    ok = ac.Outcome(ok=True, source="jats", n_chunks=100)
    out = ac.screen_outcome(ok, 0.54)
    assert not out.ok and out.reason == "repetition_screen" and out.source == "jats" and out.n_chunks == 100
    assert ac.screen_outcome(ok, 0.90) is ok and ac.screen_outcome(ok, None) is ok
    c0 = ac.Outcome(ok=False, source="pdf", excluded_c0=True, reason="c0_screen")
    assert ac.screen_outcome(c0, 0.1) is c0   # the C0 screen comes first


def test_repetition_screen_walks_the_neighbour_list_down_and_the_random_order_on():
    ratio = {"W1": 0.99, "W2": 0.61, "W3": 0.97, "W4": 0.95}
    raw = {w: ac.Outcome(ok=True, source="pdf", n_chunks=5) for w in ratio}

    def screened(w):
        return ac.screen_outcome(raw[w], ratio[w])

    ranked = [W(w, type="article") for w in ratio]
    r = ac.walk_down(ranked, 2, screened)
    assert r["picked"] == ["W1", "W3"] and ("W2", "repetition_screen") in r["skipped"]
    assert ac.walk_down(ranked, 2, lambda w: raw[w])["picked"] == ["W1", "W2"]   # unscreened: W2 stays
    info = dict.fromkeys(ratio, ("article", "pdf", None))
    m = ac.draw_matched(["W2", "W4", "W1"], {("article", 0, "pdf"): 1}, set(), type_of=lambda w: info[w][0],
                        klass=lambda w: info[w][1], resolve=screened, band=lambda n: 0)
    assert [t["wid"] for t in m["took"]] == ["W4"] and m["skipped"] == {"repetition_screen": 1}


def test_carry_over_v4_releases_screened_any_and_quota_changed_and_keeps_the_rest_exactly():
    took = [_take("A", "article", 0, "jats", "strict", ("article", 0, "jats")),
            _take("B", "article", 5, "pdf", "source", ("article", 5, "jats")),        # repetitive: released
            _take("C", "review", 4, "jats", "any", ("article", 5, "jats")),           # band relaxed: released
            _take("D", "article", 2, "pdf", "strict", ("article", 2, "pdf")),         # its slot left the quota
            _take("E", "review", 1, "jats", "source+type", ("article", 1, "pdf"))]
    quota = {("article", 0, "jats"): 1, ("article", 5, "jats"): 2, ("article", 1, "pdf"): 1, ("review", 3, "jats"): 1}
    r = ac.carry_over_v4(took, quota, excluded=lambda w: w == "B")
    assert r["kept"] == [took[0], took[4]]
    assert {t["wid"]: t["why"] for t in r["released"]} == {"B": "repetition_screen", "C": "any_nearest_band",
                                                          "D": "quota_changed"}
    assert r["open"] == {("article", 5, "jats"): 2, ("review", 3, "jats"): 1}
    # nothing touched: every take kept, nothing open
    same = ac.carry_over_v4(took[:1] + took[3:], {("article", 0, "jats"): 1, ("article", 2, "pdf"): 1,
                                                  ("article", 1, "pdf"): 1}, excluded=lambda w: False)
    assert len(same["kept"]) == 3 and not same["released"] and not same["open"]
    assert ac.V4_RELEASE == ("repetition_screen", "any_nearest_band", "quota_changed")


def test_study_seed_table_names_the_corpus_random_seeds():
    import alias_study_common as asc

    assert (asc.SEEDS["random1"], asc.SEEDS["random2"]) == (ac.SEED_RANDOM_V2[1], ac.SEED_RANDOM_V2[2])


# ------------------------------------------------------------------ v4 runner: cache writes, Repetition, re-draw step


def _run():
    import alias_corpus_run as run
    return run


def test_openalex_cache_is_written_only_when_changed_and_never_read_only(tmp_path):
    run = _run()
    lay = run.Layout(str(tmp_path / "root"))
    api = run.OpenAlex(lay)
    assert api.save_if_dirty() is False and not api.cache_path.exists()      # nothing added: no write
    api.cache["u"], api.dirty = {"results": []}, 1
    api.read_only = True
    assert api.save_if_dirty() is False and not api.cache_path.exists()      # dry run: never
    api.save()
    assert not api.cache_path.exists()
    api.read_only = False
    assert api.save_if_dirty() is True and api.cache_path.exists() and api.dirty == 0


def test_neighbour_inputs_does_not_rewrite_an_unchanged_cache(tmp_path, monkeypatch):
    run = _run()
    lay = run.Layout(str(tmp_path / "root"))
    (lay.state / "study.json").write_text('{"study": []}')
    monkeypatch.setattr(run, "load_oa1000", lambda: (["PMC1"], {"PMC1": "t"}, {}))

    class CachedOnly(run.OpenAlex):
        def get(self, params):
            self.hits += 1
            return {"results": [], "meta": {"count": 0}}

    api = CachedOnly(lay)
    writes = []
    monkeypatch.setattr(api, "save", lambda: writes.append(1))
    run.neighbour_inputs(lay, api)
    assert api.hits >= 1 and writes == []


class _FakeBuilder:
    def __init__(self, done):
        self.done = done


def _chunk_file(lay, name, texts):
    import json as _json
    body = "".join(_json.dumps({"content": t}, ensure_ascii=False) + "\n" for t in texts).encode()
    (lay.root / "chunks").mkdir(exist_ok=True)
    (lay.root / "chunks" / name).write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_repetition_helper_reads_chunk_files_checks_the_sha_and_screens(tmp_path):
    run = _run()
    lay = run.Layout(str(tmp_path / "root"))
    rep_texts = [f"t{i}" for i in range(7)] + ["dup", "dup", "dup  "]          # 8 / 10 = 0.80
    ok_texts = [f"u{i}" for i in range(10)] + ["u0 "]                      # U+2028 inside a line
    done = {"R": {"ok": True, "source": "jats", "n_chunks": 10, "chunk_file": "R.jsonl",
                  "chunks_sha256": _chunk_file(lay, "R.jsonl", rep_texts)},
            "K": {"ok": True, "source": "pdf", "n_chunks": 11, "chunk_file": "K.jsonl",
                  "chunks_sha256": _chunk_file(lay, "K.jsonl", ok_texts)},
            "F": {"ok": False, "reason": "http_404"}}
    rep = run.Repetition(lay, _FakeBuilder(done))
    assert rep.ratio("R") == 0.8 and rep.excluded("R")
    assert rep.ratio("K") == 10 / 11 and not rep.excluded("K")          # the U+2028 line is not split
    assert rep.ratio("F") is None and not rep.excluded("F") and rep.ratio("missing") is None
    o = rep.outcome(done["R"])
    assert not o.ok and o.reason == "repetition_screen"
    assert rep.outcome(done["K"]).ok and rep.outcome(done["F"]).reason == "http_404"
    off = run.Repetition(lay, _FakeBuilder(done), enabled=False)
    assert not off.excluded("R") and off.outcome(done["R"]).ok
    done["K"]["chunks_sha256"] = "0" * 64
    with pytest.raises(SystemExit):
        run.Repetition(lay, _FakeBuilder(done)).ratio("K")


def _rel(wid, why, cell, level="strict"):
    return {**_take(wid, cell[0], cell[1], cell[2], level, cell), "why": why}


def test_v4_redraw_mode_refills_from_the_v3_walk_only_for_pure_any_releases():
    run = _run()
    cell = ("article", 5, "jats")
    anyrel = {"open": {cell: 1}, "released": [_rel("A", "any_nearest_band", cell, "any")], "kept": []}
    assert run.v4_redraw_mode(anyrel, True) == "nearest_from_v3_walk"
    assert run.v4_redraw_mode(anyrel, False) == "walk"                      # quota changed: walk
    mixed = {"open": {cell: 2}, "released": anyrel["released"] + [_rel("B", "repetition_screen", cell)], "kept": []}
    assert run.v4_redraw_mode(mixed, True) == "walk"
    for why in ("repetition_screen", "quota_changed"):
        assert run.v4_redraw_mode({"open": {cell: 1}, "released": [_rel("B", why, cell)], "kept": []}, True) == "walk"
    # released but nothing open (the slot's cell left the quota): nothing to do
    assert run.v4_redraw_mode({"open": {}, "released": [_rel("B", "quota_changed", cell)], "kept": []}, False) is None
    assert run.v4_redraw_mode({"open": {}, "released": [], "kept": []}, True) is None


def test_v4_carry_refuses_a_take_that_left_the_candidate_pool():
    run = _run()
    took = [_take("A", "article", 0, "jats", "strict", ("article", 0, "jats"))]
    assert run.v4_carry(took, {"A"}, {("article", 0, "jats"): 1}, lambda w: False)["kept"] == took
    with pytest.raises(SystemExit):
        run.v4_carry(took, {"B"}, {("article", 0, "jats"): 1}, lambda w: False)


def _step(carry, quota3, quota4, order_p, fetched3, taken, excluded=lambda w: False, info=None):
    run = _run()
    calls: list[str] = []
    took_ids: list[str] = []
    info = info or {}

    def resolve(w):
        calls.append(w)
        return info[w][2]
    rec = run.v4_study_redraw(carry, quota3, quota4, order_p, fetched3, taken, excluded=excluded,
                              is_dup=lambda w: False, on_take=took_ids.append,
                              type_of=lambda w: info[w][0], klass=lambda w: info[w][1], resolve=resolve,
                              band=lambda n: n // 10)
    return rec, calls, took_ids


def test_v4_study_redraw_refills_from_the_v3_walk_without_fetching():
    slot = ("article", 5, "jats")
    kept = [_take("K", "article", 1, "jats", "strict", ("article", 1, "jats"))]
    carry = {"kept": kept, "released": [_rel("A", "any_nearest_band", slot, "any")], "open": {slot: 1}}
    quota = {("article", 1, "jats"): 1, slot: 1}
    fetched3 = {"near_rep": ("article", 4, "jats"), "near_taken": ("article", 4, "jats"),
                "near_ok": ("article", 4, "pdf"), "far": ("article", 0, "jats")}
    order_p = ["near_rep", "near_taken", "far", "near_ok", "K"]
    taken = {"K", "near_taken"}
    rec, calls, took_ids = _step(carry, quota, quota, order_p, fetched3, taken, excluded=lambda w: w == "near_rep")
    assert rec["mode"] == "nearest_from_v3_walk" and calls == []          # no walk, no fetch
    assert [(t["wid"], t["redrawn"]) for t in rec["took"]] == [("K", False), ("near_ok", True)]
    assert rec["took"][1]["band_distance"] == 1 and rec["took"][1]["level"] == "any"
    assert took_ids == ["near_ok"] and "near_ok" in taken and rec["shortfall"] == 0 and rec["nearest_pool"] == 3
    # nothing left to refill from: the slot stays open and is reported
    rec, _, _ = _step(carry, quota, quota, order_p, {"near_rep": ("article", 4, "jats")}, {"K"},
                      excluded=lambda w: w == "near_rep")
    assert rec["unfilled"] == {"article|5|jats": 1} and rec["shortfall"] == 1


def test_v4_study_redraw_walks_again_when_the_screen_or_the_quota_released_a_slot():
    slot = ("article", 2, "jats")
    info = {"S": ("article", "jats", ac.Outcome(ok=False, reason="repetition_screen")),
            "T": ("article", "jats", ac.Outcome(ok=True, source="jats", n_chunks=25))}
    carry = {"kept": [], "released": [_rel("R", "repetition_screen", slot)], "open": {slot: 1}}
    rec, calls, took_ids = _step(carry, {slot: 1}, {slot: 1}, ["S", "T"], {"X": ("article", 2, "jats")}, set(),
                                 info=info)
    assert rec["mode"] == "walk" and calls == ["S", "T"] and took_ids == ["T"]
    assert [(t["wid"], t["level"], t["redrawn"]) for t in rec["took"]] == [("T", "strict", True)]
    assert rec["skipped"] == {"repetition_screen": 1} and rec["redraw_budget"] == 40
    # an any release whose study paper's quota changed walks too (its v3 walk was for the old quota)
    carry = {"kept": [], "released": [_rel("A", "any_nearest_band", ("article", 5, "pdf"), "any")], "open": {slot: 1}}
    rec, calls, _ = _step(carry, {("article", 5, "pdf"): 1}, {slot: 1}, ["S", "T"], {"X": ("article", 2, "jats")},
                          set(), info=info)
    assert rec["mode"] == "walk" and rec["quota_changed"] and [t["wid"] for t in rec["took"]] == ["T"]
    # nothing released, nothing open: the kept takes are the record
    kept = [_take("K", "article", 2, "jats", "strict", slot)]
    rec, calls, _ = _step({"kept": kept, "released": [], "open": {}}, {slot: 1}, {slot: 1}, ["S"], {}, {"K"}, info=info)
    assert rec["mode"] is None and calls == [] and [t["wid"] for t in rec["took"]] == ["K"]
