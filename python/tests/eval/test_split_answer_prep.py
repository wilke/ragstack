"""split-answer PLAN §3.2 (exploratory), ``split_answer_prep.py``: the neighbour item id scheme and
its collision guard, the §3.1 passage as production's formatter renders it (both sides, document
edges, a one-chunk document, the rule-3 trim check), and ``items`` end to end on a synthetic world
(groups, M ± 2 for the uncontained split windows, edges, merges, counts-only public output, rows
``build-control`` accepts). A word-count token counter stands in for the SFR tokenizer; no store,
no network, no model."""
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
import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import rechunk_prep as rp  # noqa: E402
import split_answer_fixture as sfx  # noqa: E402
import split_answer_prep as sap  # noqa: E402

pytest.importorskip("nltk")
pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")

REPO = Path(__file__).resolve().parents[3]
FEASIBILITY = REPO / "docs" / "plans" / "results" / "split-answer-2026-11" / "artifacts" / "feasibility.json"
CONFIRM_DRAW = REPO / "docs" / "plans" / "results" / "alias-boundary-confirm-2026-11" / "artifacts" / "draw.json"


@pytest.fixture()
def words(monkeypatch: pytest.MonkeyPatch) -> sfx.WordCounter:
    from ragstack.ingestion import chunker_config

    wc = sfx.WordCounter()
    monkeypatch.setattr(chunker_config, "cached_token_counter", lambda *a, **k: wc)
    return wc


def rows(*contents: str, doc: str = "d1") -> list[dict[str, Any]]:
    """Tiling chunk rows of one document."""
    out, pos = [], 0
    for i, c in enumerate(contents):
        out.append({"chunk_id": f"{doc}-c{i}", "doc_id": doc, "content": c, "start_char": pos,
                    "end_char": pos + len(c), "metadata": {"source_path": "PMCX", "title": "T"}})
        pos += len(c)
    return out


# ------------------------------------------------------------------ id scheme

def test_nbr_id_scheme_round_trip_and_refusals() -> None:
    m = "0b9d6c2e-1f3a-5b7c-9d1e-2f3a4b5c6d7e"
    assert sap.nbr_id(m, 1) == f"nbr-w1:{m}" and sap.nbr_id(m, 2) == f"nbr-w2:{m}"
    assert sap.parse_nbr_id(sap.nbr_id(m, 2)) == (2, m)
    assert sap.parse_nbr_id(m) is None and sap.parse_nbr_id("nbr-w3:x") is None
    assert sap.parse_nbr_id("nbr-w1:nbr-w1:x") is None
    with pytest.raises(ValueError):
        sap.nbr_id(m, 3)
    with pytest.raises(ValueError):
        sap.nbr_id(sap.nbr_id(m, 1), 1)
    # the pair id is the judge's, unchanged: it depends on (record, item id) only
    assert aj.pair_id("r1", sap.nbr_id(m, 1)) == asc.sha_text(f"r1|nbr-w1:{m}")[:16]
    assert aj.pair_id("r1", sap.nbr_id(m, 1)) != aj.pair_id("r1", sap.nbr_id(m, 2)) != aj.pair_id("r1", m)
    # the prefix is rechunk_prep's guard
    assert sap.NBR_PREFIX == rp.NEIGHBOUR_ID_PREFIX == "nbr-"


def test_refuse_nbr_ids_is_the_collision_guard() -> None:
    assert sap.refuse_nbr_ids(["a", "b-nbr-c"], "x") == 2
    with pytest.raises(SystemExit, match="starting 'nbr-'"):
        sap.refuse_nbr_ids(["a", "nbr-w1:a"], "the main config's chunk exports")


def test_pinned_inputs_are_the_committed_artifacts_shas() -> None:
    feas = json.loads(FEASIBILITY.read_text())["inputs_sha256"]
    for k in ("pairs_seed", "chunks_control", "chunks_retest", "label_table"):
        assert sap.PINNED_INPUTS[k] == feas[k]
    assert sap.PINNED_INPUTS["confirm_sample"] == json.loads(CONFIRM_DRAW.read_text())["sample_sha256"]
    assert sap.PINNED_INPUTS["control_sample"] == acc.CONFIRM_CONTROL_SAMPLE_SHA256
    assert sap.STAGE_A_COMMIT_SHA256 == acc.CONFIRM_STAGE_A_COMMIT_SHA256


# ------------------------------------------------------------------ the passage (§3.1)

def via_format_context(r: list[dict[str, Any]], m: int, before: list[int], after: list[int]) -> str:
    """Production's formatter called directly on a hand-built Source (no linker, no walk)."""
    from ragstack.llm import RagGenerator
    from ragstack.models import ContextChunk, Source

    ctx = [ContextChunk(chunk_id=r[i]["chunk_id"], position=i - m, content=r[i]["content"]) for i in before + after]
    src = Source(doc_id="d1", chunk_id=r[m]["chunk_id"], content=r[m]["content"], score=0.5, context=ctx or None)
    out = RagGenerator(None, max_context_chars=8000).format_context([src])  # type: ignore[arg-type]
    assert out.startswith("[1] ")
    return out[4:]


def test_passage_w1_both_sides_is_production_text_literally() -> None:
    r = rows("Alpha one.", "Beta two.", "Gamma three.", "Delta four.")
    p = sap.neighbour_passage(r, "d1-c1", 1)
    assert p is not None and not p.trimmed
    assert p.text == "(context before)\nAlpha one.\n(passage)\nBeta two.\n(context after)\nGamma three."
    assert p.text == via_format_context(r, 1, [0], [2])
    assert p.edge == "none" and p.neighbour_chunk_ids == ["d1-c0", "d1-c2"] and p.chunk_ids == ["d1-c0", "d1-c1",
                                                                                                 "d1-c2"]
    assert (p.start_char, p.end_char) == (0, r[2]["end_char"]) and (p.n_before, p.n_after) == (1, 1)
    assert p.store_calls == 1  # expand_context fetched both neighbours in one hop


def test_passage_w2_puts_two_neighbours_under_one_marker() -> None:
    r = rows("A0.", "A1.", "A2.", "A3.", "A4.")
    p = sap.neighbour_passage(r, "d1-c2", 2)
    assert p is not None
    assert p.text == "(context before)\nA0.\nA1.\n(passage)\nA2.\n(context after)\nA3.\nA4."
    assert p.text == via_format_context(r, 2, [0, 1], [3, 4])
    assert p.neighbour_chunk_ids == ["d1-c0", "d1-c1", "d1-c3", "d1-c4"] and p.store_calls == 2
    # one side cut short by the document edge at w=2 is not an edge
    q = sap.neighbour_passage(r, "d1-c1", 2)
    assert q is not None and q.edge == "none" and (q.n_before, q.n_after) == (1, 2)
    assert q.text == "(context before)\nA0.\n(passage)\nA1.\n(context after)\nA2.\nA3."


def test_passage_at_document_edges_drops_the_missing_side_with_its_marker() -> None:
    r = rows("First.", "Second.", "Third.")
    first = sap.neighbour_passage(r, "d1-c0", 1)
    last = sap.neighbour_passage(r, "d1-c2", 1)
    assert first is not None and last is not None
    assert first.text == "(passage)\nFirst.\n(context after)\nSecond." == via_format_context(r, 0, [], [1])
    assert last.text == "(context before)\nSecond.\n(passage)\nThird." == via_format_context(r, 2, [1], [])
    assert (first.edge, last.edge) == ("before", "after")
    assert (first.start_char, first.end_char) == (0, r[1]["end_char"])
    two = rows("Only one.", "Only two.")
    assert sap.neighbour_passage(two, "d1-c0", 2).edge == "before"  # type: ignore[union-attr]


def test_one_chunk_document_has_no_passage_and_production_agrees() -> None:
    r = rows("Alone here.")
    assert sap.neighbour_passage(r, "d1-c0", 1) is None
    # production: a context-free source renders as its bare content (§3.1, llm.py:208-209)
    assert via_format_context(r, 0, [], []) == "Alone here."


def test_neighbour_passage_refuses_an_m_outside_the_document() -> None:
    with pytest.raises(SystemExit, match="M is not a chunk"):
        sap.neighbour_passage(rows("a.", "b."), "nope", 1)
    with pytest.raises(ValueError):
        sap.neighbour_passage(rows("a.", "b."), "d1-c0", 3)


def test_trim_check_flags_a_budget_trim_and_ignores_an_ellipsis_in_the_text() -> None:
    long = "x" * 300
    r = rows(long, "Mid.", long)
    p = sap.neighbour_passage(r, "d1-c1", 1, budget_chars=100)  # budget 300 at w=1: the context gives way
    assert p is not None and p.trimmed and "…" in p.text
    ok = sap.neighbour_passage(rows("Wait… then.", "Mid.", "After."), "d1-c1", 1)
    assert ok is not None and not ok.trimmed
    assert sap.literal_passage("M", ["B"], ["A"]) == "(context before)\nB\n(passage)\nM\n(context after)\nA"


def test_the_production_default_budget_is_read_without_building_settings() -> None:
    from ragstack.config import Settings

    assert sap.max_context_chars() == Settings.model_fields["llm_max_context_chars"].default == 8000


def test_a_change_to_productions_markers_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragstack.llm import RagGenerator

    monkeypatch.setattr(RagGenerator, "_BEFORE", "[before]\n")
    with pytest.raises(SystemExit, match="markers"):
        sap.neighbour_passage(rows("a.", "b.", "c."), "d1-c1", 1)


# ------------------------------------------------------------------ items (§3.2 (a))

@pytest.fixture()
def world(words: sfx.WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sfx.World:
    w = sfx.World(tmp_path / "w")
    monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, **w.pinned()})
    w.mp = monkeypatch  # type: ignore[attr-defined]
    return w


def test_fixture_geometry_is_what_the_tests_assume(world: sfx.World) -> None:
    assert [len(world.cdocs[d]) for d in ("dA", "dB", "dS", "dT", "dS2")] == [4, 3, 1, 2, 1]
    assert {t: world.m_index(t) for t in ("a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4", "b5", "t1")} == {
        "a1": 1, "a2": 0, "a3": 3, "a4": 2, "b1": 1, "b2": 2, "b3": 1, "b4": 1, "b5": 1, "t1": 1}


def neg_picks() -> list[tuple[str, str, int]]:
    return [("alias-O-n1-g0", "dC", 1), ("alias-O-n2-g0", "dB", 1), ("alias-O-n3-g0", "dC", 1)]


def run_items(world: sfx.World, tmp: Path, picks: list[tuple[str, str, int]] | None = None,
              public: bool = True) -> dict[str, Any]:
    picks = neg_picks() if picks is None else picks
    world.mp.setattr(sap, "NEGATIVE_QUOTAS", {"same": len(picks), "other": 0})  # type: ignore[attr-defined]
    nd = sfx.negative_dir(tmp, sap.SCHEMA["negative"], picks)
    return sap.run_items(world.p["confirm"], world.p["label_table"], world.p["main_config"], nd, tmp / "items",
                         tmp / "public" / "neighbour-items.json" if public else None, repo=tmp / "repo")


def test_items_end_to_end(world: sfx.World, tmp_path: Path) -> None:
    rec = run_items(world, tmp_path)
    out = tmp_path / "items"
    export = {r["chunk_id"]: r for r in asc.read_jsonl(out / "neighbour-export.jsonl")}
    seeds = asc.read_jsonl(out / "neighbour-seed.jsonl")
    m = world.m_of
    # groups and windows: split a1/a3/b5/t1 at w=1, a3 also at w=2 (M±1 does not contain it)
    assert rec["pairs"] == {"split": 4, "recovered": 1, "primary_only": 1, "kept_identical": 1, "kept_changed": 5,
                            "negative": 3}
    assert rec["items"]["split"] == {"context_window_1": 4, "context_window_2": 1}
    assert rec["items"]["recovered"] == {"context_window_1": 1} and rec["items"]["primary_only"] == {
        "context_window_1": 1}
    # kept_changed: b1, b2, b3 (b4 merges into b1's item; s2 is a one-chunk document)
    assert rec["items"]["kept_changed"] == {"context_window_1": 3}
    assert rec["items"]["negative"] == {"context_window_1": 3}
    assert rec["one_chunk_documents_skipped"] == {"split": 0, "recovered": 0, "primary_only": 0,
                                                  "kept_identical": 1, "kept_changed": 1}
    assert rec["split_windows_contained"] == {"context_window_1": 3, "context_window_2": 1}
    assert rec["seed_rows"] == len(seeds) == 4 + 1 + 1 + 1 + 4 + 3 and rec["seed_rows_merged_on_a_shared_item"] == 1
    assert rec["items_total"] == len({(s["record_id"], s["chunk_id"]) for s in seeds}) == 13
    # b1, b3, b5 and n2 share M=B1 (four records), n1 and n3 share M=C1: one export row each
    assert rec["export_rows_shared_by_several_records"] == 2 and len(export) == rec["export_rows"]
    assert sap.nbr_id(m("b1"), 1) in export and sap.nbr_id(m("a3"), 2) in export
    assert rec["edge_items"]["recovered"] == {"context_window_1:before": 1}
    assert rec["edge_items"]["split"] == {"context_window_1:after": 2, "context_window_1:none": 2,
                                          "context_window_2:after": 1}
    assert rec["edge_items"]["negative"] == {"context_window_1:none": 3}
    assert rec["trims_found"] == 0 and rec["plan_counts"]["matches"] is False
    assert rec["items_per_set"] == {"negative": 3, "positive": 10}
    # every export row: §3.2 (a)2's shape, its content production's passage of M's document
    for cid, r in export.items():
        w, mid = sap.parse_nbr_id(cid)  # type: ignore[misc]
        md = r["metadata"]
        assert md["item_type"] == "neighbour" and md["context_window"] == w and md["max_overlap_chunk_id"] == mid
        assert md["content_sha256"] == asc.sha_text(r["content"]) and md["formatter"] == sap.FORMATTER
        assert md["max_context_chars"] == 8000 and md["edge"] in sap.EDGES
        docs = world.cdocs.get(r["doc_id"]) or sfx.chunk_rows(r["doc_id"])
        p = sap.neighbour_passage(docs, mid, w)
        assert p is not None and r["content"] == p.text and md["neighbour_chunk_ids"] == p.neighbour_chunk_ids
        assert (r["start_char"], r["end_char"]) == (p.start_char, p.end_char)
        m_row = next(x for x in docs if x["chunk_id"] == mid)
        assert md["source_path"] == m_row["metadata"]["source_path"] and md["title"] == m_row["metadata"]["title"]
    a3 = export[sap.nbr_id(m("a3"), 2)]
    assert a3["metadata"]["neighbour_chunk_ids"] == [r["chunk_id"] for r in world.cdocs["dA"][1:3]]
    assert a3["start_char"] == 2371 and a3["end_char"] == len(sfx.DOC_A)
    # seed rows: §3.2 (a)4
    s_by = {(s["record_id"], s["chunk_id"], s["control_of"]): s for s in seeds}
    b1 = world.windows["b1"]["chunk_id"]
    assert s_by[("alias-O-b1-g0", sap.nbr_id(m("b1"), 1), aj.pair_id("alias-O-b1-g0", b1))]["group"] == "kept_changed"
    assert all(s["item_set"] == ("negative" if s["group"] == "negative" else "positive") for s in seeds)
    assert {s["context_window"] for s in seeds if s["group"] != "split"} == {1}
    # the export reads as a chunk export; build-control accepts every seed row (same paper, stage A).
    # The stage-A pair's c_paper is its window's paper (review A5): the positive's stage-A window row,
    # the negative's window document, never the neighbour export's own paper
    chunks = asc.read_chunk_exports([out / "neighbour-export.jsonl"])
    window_paper = {aj.pair_id(x.rid, world.windows[x.tag]["chunk_id"]): world.windows[x.tag]["metadata"]["source_path"]
                    for x in world.positive}
    window_paper.update({r["pair_id"]: sfx.DOCS[r["doc_id"]][0]
                         for r in asc.read_jsonl(tmp_path / "negative" / "sample-negative.jsonl")})
    main_pairs = {}
    for s in seeds:
        paper = window_paper[s["control_of"]]
        main_pairs[s["control_of"]] = {"pair_id": s["control_of"], "record_id": s["record_id"], "stage": "A",
                                       "c_paper": paper, "q_paper": paper, "arm": "base", "question": "q?",
                                       "term": "t", "record_kind": "G"}
    reg, counts = aj.build_control_pairs(seeds, list(main_pairs.values()), chunks, set(chunks))
    assert counts["pairs"] == rec["items_total"] and len(reg) == 13 and counts["same_paper"] == 13
    assert all(r["content_sha256"] == asc.sha_text(chunks[r["chunk_id"]]["content"]) for r in reg)
    # the check can fail: an export row naming another paper is refused by build-control
    bad = dict(chunks)
    some = seeds[0]["chunk_id"]
    bad[some] = {**chunks[some], "paper": "PMC-other"}
    with pytest.raises(SystemExit, match="paper is not the stage-A passage's"):
        aj.build_control_pairs(seeds, list(main_pairs.values()), bad, set(bad))
    # the public file: counts only (no id, no text, no path) and the same as the private summary
    pub = json.loads((tmp_path / "public" / "neighbour-items.json").read_text())
    assert pub == json.loads((out / "neighbour-items.json").read_text())
    forbidden = set(export) | {r["chunk_id"] for rs in world.cdocs.values() for r in rs} | \
        {s["control_of"] for s in seeds} | {s["record_id"] for s in seeds} | set(world.cdocs) | \
        {w["chunk_id"] for w in world.windows.values()}
    texts = [t for _p, t in sfx.DOCS.values()]
    assert asc.public_problems(pub, forbidden, texts) == [] and sap.path_problems(pub) == []
    assert pub["outputs_sha256"] == {"neighbour_export": asc.sha256_file(out / "neighbour-export.jsonl"),
                                     "neighbour_seed": asc.sha256_file(out / "neighbour-seed.jsonl")}
    assert pub["calls"] == {"store": 0, "retrieval": 0, "llm": 0, "gateway": 0}
    assert "host" not in pub["provenance"]
    # passage tokens: one count per new item, on the passage as judged
    assert pub["passage_tokens"]["split"]["context_window_2"]["n"] == 1
    assert pub["passage_tokens"]["kept_changed"]["context_window_1"]["n"] == 3
    # deterministic: a second run writes the same bytes (write-once accepts them)
    rec2 = run_items(world, tmp_path)
    assert rec2["outputs_sha256"] == rec["outputs_sha256"]


def test_items_refuses_unpinned_inputs(world: sfx.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, "label_table": "0" * 64})
    with pytest.raises(SystemExit, match="label_table .* is not the file"):
        run_items(world, tmp_path)
    assert not (tmp_path / "items").exists()


def test_items_refuses_a_positive_negative_collision(world: sfx.World, tmp_path: Path) -> None:
    # n1 takes b1's record and M=B1: the same (record, M) as a positive item
    with pytest.raises(SystemExit, match="disjoint"):
        run_items(world, tmp_path, [("alias-O-b1-g0", "dB", 1)])
    assert not (tmp_path / "items" / "neighbour-export.jsonl").exists()


def test_items_refuses_two_negatives_on_one_record_and_m(world: sfx.World, tmp_path: Path) -> None:
    # review A1: two drawn pairs (two windows) of one record on one M are one neighbour item
    with pytest.raises(SystemExit, match=r"share one \(record, neighbour item id\)"):
        run_items(world, tmp_path, [("alias-O-n1-g0", "dC", 1), ("alias-O-n1-g0", "dC", 1)])
    assert not (tmp_path / "items" / "neighbour-export.jsonl").exists()


def test_items_refuses_an_edge_negative_and_a_tampered_draw(world: sfx.World, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="both sides"):
        run_items(world, tmp_path, [("alias-O-n1-g0", "dC", 0)])
    nd = sfx.negative_dir(tmp_path / "t", sap.SCHEMA["negative"], neg_picks())
    (nd / "sample-negative.jsonl").write_text((nd / "sample-negative.jsonl").read_text() + "\n")
    with pytest.raises(SystemExit, match="not the ones its negative-draw.json names"):
        sap.run_items(world.p["confirm"], world.p["label_table"], world.p["main_config"], nd, tmp_path / "i2",
                      repo=tmp_path / "t" / "repo")


def test_items_refuses_a_shared_document_with_another_text_in_the_negative_run(world: sfx.World,
                                                                              tmp_path: Path) -> None:
    """Review A4: dB is in both chunk runs; one letter of a dB chunk changes in the draw's run (ids
    and spans equal), and the summary names the changed file."""
    world.mp.setattr(sap, "NEGATIVE_QUOTAS", {"same": 3, "other": 0})  # type: ignore[attr-defined]
    nd = sfx.negative_dir(tmp_path, sap.SCHEMA["negative"], neg_picks())
    cp = nd / "chunks" / "chunks-control.jsonl"
    rows = asc.read_jsonl(cp)
    k = next(i for i, r in enumerate(rows) if r["doc_id"] == "dB" and r["start_char"] > 0)
    c = rows[k]["content"]
    j = c.index("w")
    rows[k] = {**rows[k], "content": c[:j] + "v" + c[j + 1:]}
    sfx.write_jsonl(cp, rows)
    summ = json.loads((nd / "negative-draw.json").read_text())
    sfx.rewrite_summary(nd, outputs_sha256={**summ["outputs_sha256"], "chunks_control": asc.sha256_file(cp)})
    with pytest.raises(SystemExit, match=r"other chunk ids, spans or texts in the negative draw"):
        sap.run_items(world.p["confirm"], world.p["label_table"], world.p["main_config"], nd, tmp_path / "items",
                      repo=tmp_path / "repo")
    assert not (tmp_path / "items").exists()


NEG_PICKS_N = 3


@pytest.mark.parametrize(("change", "why"), [
    ({"seed_file_sha256": "0" * 64}, "seed_file_sha256 is not the sha256 of"),
    ({"seed_file_sha256": None}, "seed_file_sha256 is not the sha256 of"),
    ({"stage_a_commit_sha256": "0" * 64}, "stage_a_commit_sha256 is not"),
    ({"inputs_sha256": {"confirm_sample": "0" * 64}}, "inputs_sha256.confirm_sample is not the pinned"),
    ({"quotas": None}, "quotas are not"),
    ({"drawn": {"same:base": 2}}, "drawn counts are not its quotas"),
    ({"n_drawn": NEG_PICKS_N - 1}, f"did not draw {NEG_PICKS_N} pairs"),
])
def test_items_refuses_a_negative_dir_the_draw_did_not_write(world: sfx.World, tmp_path: Path,
                                                             change: dict[str, Any], why: str) -> None:
    """Review A2: a hand-written sample with a summary that names its files is not a draw."""
    world.mp.setattr(sap, "NEGATIVE_QUOTAS", {"same": NEG_PICKS_N, "other": 0})  # type: ignore[attr-defined]
    nd = sfx.negative_dir(tmp_path, sap.SCHEMA["negative"], neg_picks())
    if "inputs_sha256" in change:
        summ = json.loads((nd / "negative-draw.json").read_text())
        change = {"inputs_sha256": {**summ["inputs_sha256"], **change["inputs_sha256"]}}
    sfx.rewrite_summary(nd, **change)
    with pytest.raises(SystemExit, match=f"not a draw made after the committed seed.*{why}"):
        sap.run_items(world.p["confirm"], world.p["label_table"], world.p["main_config"], nd, tmp_path / "items",
                      repo=tmp_path / "repo")
    assert not (tmp_path / "items").exists()


def test_items_refuses_a_negative_dir_without_the_committed_seed_or_with_other_quotas(
        world: sfx.World, tmp_path: Path) -> None:
    world.mp.setattr(sap, "NEGATIVE_QUOTAS", {"same": NEG_PICKS_N, "other": 0})  # type: ignore[attr-defined]
    nd = sfx.negative_dir(tmp_path, sap.SCHEMA["negative"], neg_picks())
    args = (world.p["confirm"], world.p["label_table"], world.p["main_config"], nd, tmp_path / "items")
    # the summary's seed is the file's bytes, but the file was never committed: a repo without it
    other, _f = sfx.seed_repo(tmp_path / "bare", commit=False)
    with pytest.raises(SystemExit, match="seed_file_sha256 is not the sha256 of"):
        sap.run_items(*args, repo=other)
    # the old hand-written shape: only the files' sha256s
    summ = json.loads((nd / "negative-draw.json").read_text())
    (nd / "negative-draw.json").write_text(json.dumps({"schema": summ["schema"],
                                                       "outputs_sha256": summ["outputs_sha256"]}))
    with pytest.raises(SystemExit, match="not a draw made after the committed seed"):
        sap.run_items(*args, repo=tmp_path / "repo")
    (nd / "negative-draw.json").write_text(json.dumps(summ))
    # the plan's quotas: 150 = 100 same + 50 other; this summary's 3 same are not those
    world.mp.setattr(sap, "NEGATIVE_QUOTAS", {"same": 100, "other": 50})  # type: ignore[attr-defined]
    with pytest.raises(SystemExit, match=r"quotas are not .*did not draw 150 pairs"):
        sap.run_items(*args, repo=tmp_path / "repo")
    assert not (tmp_path / "items").exists()


def test_items_refuses_an_export_chunk_id_with_the_nbr_prefix(world: sfx.World, tmp_path: Path) -> None:
    raw = json.loads(world.p["main_config"].read_text())
    extra = sfx.write_jsonl(tmp_path / "extra.jsonl", [{**sfx.window("dB", 0, 10, "x"), "chunk_id": "nbr-w1:abc"}])
    raw["chunk_exports"]["neighbour"] = str(extra)
    world.p["main_config"].write_text(json.dumps(raw))
    with pytest.raises(SystemExit, match="main config's chunk exports holds a chunk id starting 'nbr-'"):
        run_items(world, tmp_path)


def test_items_refuses_a_trimmed_passage(world: sfx.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sap, "max_context_chars", lambda: 1500)
    with pytest.raises(SystemExit, match=r"trimmed by the production budget \(§3.1 rule 3\)"):
        run_items(world, tmp_path)
    assert not (tmp_path / "items" / "neighbour-export.jsonl").exists()


def test_items_refuses_a_kept_identical_pair_in_a_multi_chunk_document(words: sfx.WordCounter, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # a window equal to the first adopted chunk of B: kept_identical, but B has three chunks
    pos = [*sfx.POSITIVE, sfx.Pos("bx", "dB", 0, 3000, False, None)]
    w = sfx.World(tmp_path / "w", positive=pos)
    monkeypatch.setattr(sap, "PINNED_INPUTS", {**sap.PINNED_INPUTS, **w.pinned()})
    w.mp = monkeypatch  # type: ignore[attr-defined]
    with pytest.raises(SystemExit, match="kept_identical pair sits in a document of several chunks"):
        run_items(w, tmp_path)


def test_public_output_refuses_an_id_or_a_path(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="private string"):
        sap.publish({"n": 1, "x": "chunk-abcdefgh"}, tmp_path / "p.json", None, "x", {"chunk-abcdefgh"})
    with pytest.raises(SystemExit, match="a path"):
        sap.publish({"n": 1, "x": "/home/someone/file"}, tmp_path / "p.json", tmp_path / "pub.json", "x", set())
    assert not (tmp_path / "p.json").exists() and not (tmp_path / "pub.json").exists()


def test_private_outputs_are_refused_inside_the_repository(world: sfx.World, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    import alias_study_spot as spot

    monkeypatch.setattr(spot, "REPO", tmp_path)
    with pytest.raises(SystemExit, match="inside the repository"):
        run_items(world, tmp_path)
