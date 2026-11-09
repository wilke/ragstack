"""Alias study chunk-boundary control (exploratory): the environment refusals, the control chunker
as the markers resolve it, P2's invariants on titled / nested / untitled sections and an
over-budget sentence, the mapping and its tie rule, the draw (x1 reproduces the spot draw byte for
byte; the spot sheet is the head of every x3 cell), build-control, and the public report's
refusals. Fixtures only: a word-count token counter stands in for the SFR tokenizer; no model, no
store, no network."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_control_chunks as ctl  # noqa: E402
import alias_study_common as asc  # noqa: E402
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import alias_study_run as ar  # noqa: E402
import alias_study_spot as spot  # noqa: E402
import chunktrial_stats as cs  # noqa: E402

pytest.importorskip("nltk")
pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")


class WordCounter:
    """One token per whitespace-separated word: a deterministic stand-in for the SFR tokenizer."""

    def count(self, text: str) -> int:
        return len(text.split())


@pytest.fixture()
def words(monkeypatch: pytest.MonkeyPatch) -> WordCounter:
    from ragstack.ingestion import chunker_config

    wc = WordCounter()
    monkeypatch.setattr(chunker_config, "cached_token_counter", lambda *a, **k: wc)
    return wc


def sent(n: int, tag: str) -> str:
    return " ".join([tag.capitalize()] + [f"{tag}w{j}" for j in range(n - 1)]) + "."


def sents(k: int, n: int, tag: str) -> str:
    return " ".join(sent(n, f"{tag}{i}") for i in range(k))


#: an untitled lead, a titled parent with nested children (one long, one short), and a titled
#: section holding one sentence over the 640 ceiling
DOC_A = (sents(3, 20, "lead") + "\n\n## Methods\n\n" + sents(10, 20, "m") + "\n\n### Sub A\n\n"
         + sents(60, 20, "a") + "\n\n### Sub B\n\n" + sents(2, 20, "b") + "\n\n## Results\n\n"
         + sent(700, "r") + "\n")
#: no heading at all: one untitled section
DOC_B = sents(45, 20, "u") + "\n"


# ------------------------------------------------------------------ identity and environment

def test_markers_chunk_descriptor_is_the_plan_string() -> None:
    assert ctl.control_descriptor() == ctl.CONTROL_DESCRIPTOR == (
        'sentence/512/0/{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens",'
        '"structure_cuts":"sections/1"}')
    assert ctl.control_markers().applied() == {"structure_cuts": "sections/1"}
    assert ctl.control_descriptor() != ctl.STAGE_A_DESCRIPTOR


def test_environment_passes_here() -> None:
    env = ctl.require_env()
    assert env["segmenter_version"] == "punkt-abbrev-v1" and env["adapter"] == "markdown/1"


def test_refuses_a_ragstack_outside_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import ragstack

    monkeypatch.setattr(ragstack, "__file__", str(tmp_path / "stale" / "ragstack" / "__init__.py"))
    with pytest.raises(SystemExit, match="outside this script's checkout"):
        ctl.require_env()


def test_refuses_the_regex_segmenter(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragstack.ingestion import chunkers

    monkeypatch.setattr(chunkers, "segmenter_version", lambda: "regex-abbrev-v1")
    with pytest.raises(SystemExit, match="punkt-abbrev-v1"):
        ctl.require_env()


def test_refuses_a_missing_markdown_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragstack.ingestion import markdown_structure as md

    def missing() -> None:
        raise md.MarkdownBackendMissing("no markdown-it here")

    monkeypatch.setattr(md, "require", missing)
    with pytest.raises(SystemExit, match=r"REFUSED: .*\[markdown\]"):
        ctl.require_env()


def test_control_chunker_is_the_section_2_one(words: WordCounter) -> None:
    cc = ctl.control_chunker()
    assert (cc.chunker.target_tokens, cc.chunker.ceiling_tokens, cc.chunker.min_piece_tokens) == (512, 640, 128)
    assert cc.chunker.structure_cuts == "sections/1" and cc.chunker.size_unit == "tokens"
    assert cc.applied()["tolerance"]["ceiling"] == 640


def test_a_window_that_clamps_the_tolerance_is_refused(words: WordCounter) -> None:
    with pytest.raises(SystemExit, match="clamps the #831 tolerance"):
        ctl.control_chunker(model_window=600)


# ------------------------------------------------------------------ P2 on the fixture

def test_p2_invariants_titled_nested_untitled_and_an_over_budget_sentence(words: WordCounter) -> None:
    cc = ctl.control_chunker()
    a = ctl.chunk_document("doc-a", DOC_A, cc)
    c, toks, piece = ctl.measure_document(a, cc)
    assert c["fragment_violations"] == 0 and c["boundary_violations"] == 0
    assert c["over_ceiling"] == 0 and max(toks) <= 640 and c["tiling_violation_docs"] == 0
    assert c["content_mismatch"] == 0
    # the 700-word sentence is the one cut inside a sentence
    assert c["split_sentences"] == 1 and c["split_sentence_boundaries"] >= 1 and any(piece)
    assert c["structured_docs"] == 1 and c["stamped"] == len(a.chunks)
    paths = [tuple(ch.metadata.get("section_path") or ()) for ch in a.chunks]
    assert ("Methods", "Sub A") in paths and ("Methods", "Sub B") in paths and ("Results",) in paths
    # no chunk holds text of two titled sections unless it is a union of whole segments
    segs = ctl.doc_segments(a.doc)
    for ch in a.chunks:
        inside = [g for g in segs if g.start < ch.end_char and ch.start_char < g.end]
        assert len(inside) == 1 or (ch.start_char == inside[0].start and ch.end_char == inside[-1].end)
    b = ctl.chunk_document("doc-b", DOC_B, cc)
    cb, tb, _ = ctl.measure_document(b, cc)
    assert ctl.section_info(b.doc) == {"structure": "markdown/1", "n_sections": 1, "untitled_only": True}
    assert cb["one_untitled_section_docs"] == 1 and cb["boundary_violations"] == 0 and len(b.chunks) >= 2
    assert all(t <= 640 for t in tb)
    assert all(ctl.p2_gate(c + cb, True).values())


def test_forged_chunks_trip_the_fragment_and_boundary_counters(words: WordCounter) -> None:
    from ragstack.ingestion.chunkers import _make_chunk, sentence_spans

    cc = ctl.control_chunker()
    a = ctl.chunk_document("doc-a", DOC_A, cc)
    segs = ctl.doc_segments(a.doc)
    # a chunk spanning the last part of one segment and the first part of the next: a fragment
    g = next(g for g in segs if g.end - g.start > 400)
    nxt = next(h for h in segs if h.start == g.end)
    cut = [(g.start, g.end - 50), (g.end - 50, nxt.start + 30), (nxt.start + 30, len(DOC_A))]
    forged = [_make_chunk(a.doc, x, y) for x, y in ([(0, g.start)] if g.start else []) + cut]
    c, _, _ = ctl.measure_document(ctl.ChunkedDoc(a.doc, forged), cc)
    assert c["fragment_violations"] >= 1 and c["tiling_violation_docs"] == 0
    # a boundary moved into the middle of an ordinary sentence
    s0, e0 = next((s, e) for s, e in sentence_spans(DOC_B) if e - s > 40)
    mid = (s0 + e0) // 2
    forged = [_make_chunk(b := ctl.chunk_document("doc-b", DOC_B, cc).doc, 0, mid), _make_chunk(b, mid, len(DOC_B))]
    c, _, _ = ctl.measure_document(ctl.ChunkedDoc(b, forged), cc)
    assert c["boundary_violations"] == 1 and c["split_sentence_boundaries"] == 0
    assert not ctl.p2_gate(c, True)["ii_boundaries_are_sentence_or_segment"]


def test_refuses_a_chunker_with_another_min_piece_or_budget_mode(words: WordCounter,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "MIN_PIECE", 100)
    with pytest.raises(SystemExit, match="min_piece / budget_mode"):
        ctl.control_chunker()
    monkeypatch.setattr(ctl, "MIN_PIECE", 128)
    from ragstack.ingestion import chunker_config

    real = chunker_config.build_chunker

    def summed(*a: Any, **k: Any) -> Any:
        ch, counter, budget = real(*a, **k)
        ch.budget_mode = "summed"
        return ch, counter, budget

    monkeypatch.setattr(chunker_config, "build_chunker", summed)
    with pytest.raises(SystemExit, match="min_piece / budget_mode"):
        ctl.control_chunker()


def test_chunking_is_deterministic(words: WordCounter) -> None:
    cc = ctl.control_chunker()
    one = [(c.id, c.start_char, c.end_char) for c in ctl.chunk_document("doc-a", DOC_A, cc).chunks]
    two = [(c.id, c.start_char, c.end_char) for c in ctl.chunk_document("doc-a", DOC_A, ctl.control_chunker()).chunks]
    assert one == two


def test_latex_preamble_stratum() -> None:
    assert ctl.has_latex_preamble("x \\documentclass[12pt]{minimal} \\usepackage{a} \\begin{document} $x$")
    assert not ctl.has_latex_preamble("plain text with \\alpha only")


# ------------------------------------------------------------------ the mapping (§3)

def test_primary_map_max_overlap_and_ties_to_the_earlier() -> None:
    spans = [(0, 100, "k1"), (100, 200, "k2"), (200, 300, "k3")]
    m = ctl.primary_map(50, 150, spans)  # 50 chars in k1, 50 in k2: a tie
    assert m["chunk_id"] == "k1" and m["overlap_chars"] == 50 and m["coverage"] == 0.5 and m["purity"] == 0.5
    assert m["n_overlapping"] == 2
    assert ctl.primary_map(50, 150, list(reversed(spans))) == m  # order of the input does not matter
    m = ctl.primary_map(90, 260, spans)
    assert m["chunk_id"] == "k2" and m["coverage"] == round(100 / 170, 6) and m["purity"] == 1.0
    assert ctl.primary_map(300, 400, spans) is None


def test_cover_any_is_capped_at_six_by_overlap_ties_to_the_earlier() -> None:
    spans = [(i * 10, i * 10 + 10, f"k{i}") for i in range(10)]
    hits, capped = ctl.cover_any(5, 95, spans)  # 10 chunks overlap; k0 and k9 have 5 chars, the rest 10
    assert capped and len(hits) == 6 == ctl.COVER_ANY_CAP
    assert [h["chunk_id"] for h in hits] == [f"k{i}" for i in range(1, 7)]
    hits, capped = ctl.cover_any(0, 30, spans)
    assert not capped and [h["chunk_id"] for h in hits] == ["k0", "k1", "k2"]


# ------------------------------------------------------------------ chunk -> map -> check on files

def stage_a_rows(doc_id: str, text: str, paper: str, width: int) -> list[dict[str, Any]]:
    """Stand-in stage-A windows: fixed character windows that tile the text."""
    return [{"chunk_id": f"{doc_id}-w{i:03d}", "doc_id": doc_id, "content": text[s:s + width], "start_char": s,
             "end_char": min(len(text), s + width), "metadata": {"source_path": paper, "title": f"Title {paper}"}}
            for i, s in enumerate(range(0, len(text), width))]


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Any]:
    rows = stage_a_rows("da", DOC_A, "PMC1", 1500) + stage_a_rows("db", DOC_B, "PMC2", 1500)
    bad = stage_a_rows("dc", DOC_B, "PMC3", 1500)
    bad[1] = {**bad[1], "content": "x" + bad[1]["content"][1:]}  # its record text cannot reproduce it
    exp = fx.write_jsonl(tmp_path / "export.jsonl", rows + bad)
    texts = tmp_path / "texts"
    texts.mkdir()
    fx.write_jsonl(texts / "PMC1.jsonl", [{"path": "PMC1", "text": DOC_A}, {"path": "PMC1#table-1", "text": "T"}])
    fx.write_jsonl(texts / "PMC2.jsonl", [{"path": "PMC2", "text": DOC_B}])
    fx.write_jsonl(texts / "PMC3.jsonl", [{"path": "PMC3", "text": DOC_B}])
    sample = [{"pair_id": f"p{i}", "record_id": f"r{i}", "chunk_id": r["chunk_id"], "stratum": "answers",
               "arm": "base", "in_spot_sheet": i % 2 == 0}
              for i, r in enumerate([rows[1], rows[3], rows[-1], bad[0]])]
    (tmp_path / "out").mkdir()
    sp = fx.write_jsonl(tmp_path / "out" / "sample.jsonl", sample)
    return {"exports": {"base": str(exp)}, "texts": [texts], "sample": sp, "out": tmp_path / "out", "rows": rows}


def test_chunk_map_check_end_to_end(words: WordCounter, files: dict[str, Any], tmp_path: Path) -> None:
    r = ctl.run_chunk(files["exports"], files["texts"], files["sample"], files["out"])
    assert r["documents"] == 3 and r["resolution"] == {"ok": 2, "no_match": 1}
    chunks = asc.read_jsonl(files["out"] / "chunks-control.jsonl")
    assert {c["doc_id"] for c in chunks} == {"da", "db"}
    assert all(c["metadata"]["chunk_descriptor"] == ctl.CONTROL_DESCRIPTOR for c in chunks)
    summ = json.loads((files["out"] / "chunk.json").read_text())
    kinds = {s["kind"] for s in summ["provenance"]["segmentation"]}
    assert kinds == {"sentences", "sections", "chunks"} and "host" not in summ["provenance"]
    assert all(summ["p2_gate"].values())
    m = ctl.run_map(files["exports"], files["sample"], files["out"])
    assert m["pairs_in"] == 4 and m["pairs_mapped"] == 3 and m["excluded"] == {"document_not_chunked": 1}
    seed = asc.read_jsonl(files["out"] / "pairs-seed.jsonl")
    by_id = {c["chunk_id"]: c for c in chunks}
    for s in seed:
        w = next(x for x in files["rows"] if x["chunk_id"] == s["stage_a_chunk_id"])
        k = by_id[s["chunk_id"]]
        assert k["doc_id"] == w["doc_id"] == s["doc_id"]
        best = max(ctl.overlap(w["start_char"], w["end_char"], c["start_char"], c["end_char"])
                   for c in chunks if c["doc_id"] == w["doc_id"])
        assert s["overlap_chars"] == best and 0 < s["coverage"] <= 1
    # deterministic: a second run writes the same seed bytes (write-once accepts them)
    assert ctl.run_map(files["exports"], files["sample"], files["out"])["seed_sha256"] == m["seed_sha256"]
    # cover-any on one pair
    (tmp_path / "flipped.txt").write_text("p0\n")
    cv = ctl.run_map(files["exports"], files["sample"], files["out"], cover=True, pairs=tmp_path / "flipped.txt")
    assert cv["pairs_mapped"] == 1 and cv["seed_rows"] >= 1
    chk = ctl.run_check(files["exports"], files["texts"], tmp_path / "check.json", files["sample"], per_arm=2)
    assert chk["gate_passes"] and chk["resolution"]["no_match"] == 1 and chk["counts"]["docs"] == 2
    text = (tmp_path / "check.json").read_text()
    assert "PMC1" not in text and "da" not in json.loads(text)["groups"]


def test_map_excludes_a_window_whose_offsets_fail(words: WordCounter, files: dict[str, Any]) -> None:
    ctl.run_chunk(files["exports"], files["texts"], files["sample"], files["out"])
    exp = Path(files["exports"]["base"])
    rows = asc.read_jsonl(exp)
    rows = [{**r, "start_char": r["start_char"] + 1, "end_char": r["end_char"] + 1}
            if r["chunk_id"] == "da-w001" else r for r in rows]
    fx.write_jsonl(exp, rows)
    m = ctl.run_map(files["exports"], files["sample"], files["out"])
    assert m["excluded"].get("stage_a_offsets") == 1


def test_private_outputs_are_refused_inside_the_repository(words: WordCounter, files: dict[str, Any],
                                                           monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spot, "REPO", tmp_path)
    with pytest.raises(SystemExit, match="inside the repository"):
        ctl.run_chunk(files["exports"], files["texts"], files["sample"], files["out"])


# ------------------------------------------------------------------ the draw (§4)

def frows(n: int, stratum: str, arm: str) -> list[dict[str, Any]]:
    src, label = {"answers": ("agree", 1), "not": ("agree", 0), "tiebroken": ("tiebreak", 0)}[stratum]
    return [{"pair_id": f"{stratum[:2]}{arm[:2]}{i:04d}", "record_id": f"r{i}", "chunk_id": f"c{i}",
             "source": src, "label": label, "arm": arm, "stage": "A"} for i in range(n)]


def test_draw_x1_reproduces_the_spot_draw_byte_for_byte() -> None:
    for n in (40, 7, 3):
        rows = [r for s in spot.SPOT_STRATA for a in asc.ARM_GROUPS for r in frows(n, s, a)]
        rows += frows(2, "answers", "base")[:1]  # duplicates in a cell change nothing about the rule
        mine, mc = ctl.draw(rows, 1)
        theirs, tc = spot.draw_pairs_from(rows)
        assert spot.canonical(mine) == spot.canonical(theirs) and mc == tc
    assert ctl.control_nominal() == {"answers": 150, "not": 99, "tiebroken": 51}


def test_draw_x3_sizes_and_the_spot_cells_are_its_head() -> None:
    rows = [r for s in spot.SPOT_STRATA for a in asc.ARM_GROUPS for r in frows(80, s, a)]
    picked, counts = ctl.draw(rows)
    assert counts["n"] == 300 and {s: sum(v.values()) for s, v in counts["drawn"].items()} == ctl.control_nominal()
    one, _ = spot.draw_pairs_from(rows)
    key = {"items": {f"p{i:03d}": {"pair_id": f["pair_id"], "stratum": f["stratum"], "arm": f["arm"]}
                     for i, f in enumerate(one)}}
    pre = ctl.verify_spot_prefix(rows, picked, key)
    assert pre["answers|base"] == {"spot": 17, "control": 50} and pre["tiebroken|random"] == {"spot": 5, "control": 17}
    swapped = json.loads(json.dumps(key))
    swapped["items"]["p000"]["pair_id"] = "nope"
    with pytest.raises(SystemExit, match="not the x1 draw"):
        ctl.verify_spot_prefix(rows, picked, swapped)


def _cell(picked: list[dict[str, Any]], s: str, a: str) -> list[int]:
    return [i for i, f in enumerate(picked) if f["stratum"] == s and f["arm"] == a]


def test_spot_prefix_refuses_a_reordered_truncated_or_swapped_cell() -> None:
    rows = [r for s in spot.SPOT_STRATA for a in asc.ARM_GROUPS for r in frows(80, s, a)]
    picked, _ = ctl.draw(rows)
    one, _ = spot.draw_pairs_from(rows)
    key = {"items": {f"p{i:03d}": {"pair_id": f["pair_id"], "stratum": f["stratum"], "arm": f["arm"]}
                     for i, f in enumerate(one)}}
    idx = _cell(picked, "not", "random")  # 11 spot pairs at the head of 33
    # a spot pair moved behind a non-spot pair inside the cell
    reordered = list(picked)
    reordered[idx[0]], reordered[idx[20]] = reordered[idx[20]], reordered[idx[0]]
    # the cell truncated below the spot cell
    truncated = [f for i, f in enumerate(picked) if i not in idx[5:]]
    # a spot pair swapped out of the head for a pair the draw did not take
    taken = {f["pair_id"] for f in picked}
    spare = next(r for r in rows if r["pair_id"] not in taken and spot.spot_stratum(r) == "not"
                 and r["arm"] == "random")
    swapped = list(picked)
    swapped[idx[3]] = {**spare, "stratum": "not"}
    for bad in (reordered, truncated, swapped):
        with pytest.raises(SystemExit, match="not the head"):
            ctl.verify_spot_prefix(rows, bad, key)
    # a control cell no larger than the spot cell while the cell has more pairs: not a x3 draw
    same = [f for i, f in enumerate(picked) if i not in idx[11:]]
    with pytest.raises(SystemExit, match="no larger than the spot sheet's"):
        ctl.verify_spot_prefix(rows, same, key)
    # an exhausted cell may be equal: 5 pairs in it, all drawn at x1 and at x3
    small = [r for r in rows if not (r["arm"] == "random" and spot.spot_stratum(r) == "tiebroken")]
    small += frows(5, "tiebroken", "random")
    p3, _ = ctl.draw(small)
    o1, _ = spot.draw_pairs_from(small)
    k1 = {"items": {f"p{i:03d}": {"pair_id": f["pair_id"], "stratum": f["stratum"], "arm": f["arm"]}
                    for i, f in enumerate(o1)}}
    assert ctl.verify_spot_prefix(small, p3, k1)["tiebroken|random"] == {"spot": 5, "control": 5}


@pytest.fixture(scope="module")
def dry_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("ctl")
    (root / "in").mkdir()
    w = fx.World(root / "in", n_papers=20, set_="V")
    dry.dry_run(w.records_path, w.base_export, w.glossary, root / "work", added_per_arm=4, batch_size=200,
                n_perm=100, n_boot=50)
    return root


@pytest.fixture()
def judged(dry_built: Path, tmp_path: Path) -> Path:
    """The dry run's committed stage A, relocated (the spot tests' recipe)."""
    dst = tmp_path / "w"
    shutil.copytree(dry_built, dst)
    for p in [*dst.rglob("*.json"), dst / "work" / "runs" / ar.COMMIT_MARKER]:
        t = p.read_text()
        if str(dry_built) in t:
            p.write_text(t.replace(str(dry_built), str(dst)))
    runs = dst / "work" / "runs"
    marker = json.loads((runs / ar.COMMIT_MARKER).read_text())
    csha = asc.sha256_file(marker["commit"])
    pub = json.loads(Path(marker["public"]).read_text())
    pub["commit_sha256"] = marker["commit_sha256"] = csha
    Path(marker["public"]).write_text(json.dumps(pub))
    (runs / ar.COMMIT_MARKER).write_text(json.dumps(marker))
    return dst


def test_run_draw_on_a_committed_stage_a(judged: Path, tmp_path: Path) -> None:
    cfgp = judged / "work" / "judge-config.json"
    s = spot.draw_pairs(cfgp, tmp_path / "spot")
    r = ctl.run_draw(cfgp, Path(s["key"]), Path(s["sheet"]), tmp_path / "ctl")
    sample = asc.read_jsonl(tmp_path / "ctl" / "sample.jsonl")
    key = json.loads(Path(s["key"]).read_text())
    assert sum(x["in_spot_sheet"] for x in sample) == s["n"] == len(key["items"])
    assert {x["pair_id"] for x in sample} >= {v["pair_id"] for v in key["items"].values()}
    assert r["n"] == len(sample) and r["sample_sha256"] == asc.sha256_file(tmp_path / "ctl" / "sample.jsonl")
    draw_json = json.loads((tmp_path / "ctl" / "draw.json").read_text())
    assert "host" not in draw_json["provenance"] and draw_json["factor"] == 3
    sheet = Path(s["sheet"])
    sheet.write_text(sheet.read_text().replace('"label": null', '"label": "answers"', 1))
    with pytest.raises(SystemExit, match="sheet's sha256 is not the spot key's"):
        ctl.run_draw(cfgp, Path(s["key"]), sheet, tmp_path / "ctl2")


# ------------------------------------------------------------------ build-control

def control_world(judged: Path, tmp_path: Path, n: int = 12) -> tuple[Path, Path, Path, list[dict[str, Any]]]:
    main = judged / "work" / "judge-config.json"
    raw = json.loads(main.read_text())
    stage_a = [p for p in asc.read_jsonl(Path(raw["out_dir"]) / "pairs.jsonl") if p.get("stage", "A") == "A"][:n]
    chunks = asc.read_chunk_exports(list(raw["chunk_exports"].values()))
    rows, seed = [], []
    for i, p in enumerate(stage_a):
        src = chunks[p["chunk_id"]]
        cid = f"ctl-{i:04d}-{p['chunk_id'][:8]}"
        rows.append({"chunk_id": cid, "doc_id": src["doc_id"], "content": src["content"] + " (control)",
                     "start_char": 0, "end_char": len(src["content"]) + 10,
                     "metadata": {"source_path": src["paper"], "title": src["title"]}})
        seed.append({"control_of": p["pair_id"], "record_id": p["record_id"], "chunk_id": cid})
    # two stage-A windows of one record mapped to one control chunk
    seed.append({**seed[0], "control_of": seed[0]["control_of"]})
    exp = fx.write_jsonl(tmp_path / "chunks-control.jsonl", rows)
    seed_p = fx.write_jsonl(tmp_path / "pairs-seed.jsonl", seed)
    ctl_raw = {**raw, "out_dir": str(tmp_path / "control-judge"),
               "chunk_exports": {**raw["chunk_exports"], aj.CONTROL_EXPORT: str(exp)}}
    cp = tmp_path / "control-config.json"
    cp.write_text(json.dumps(ctl_raw))
    return main, cp, seed_p, stage_a


def tree_sha(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*"))
            if p.is_file()}


def test_build_control_writes_the_registry_schema_into_a_new_out_dir(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, stage_a = control_world(judged, tmp_path)
    mdir = Path(json.loads(main.read_text())["out_dir"])
    before = tree_sha(mdir)
    assert aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                   prov={"commit": "c" * 40}) == aj.EXIT_OK
    assert tree_sha(mdir) == before  # the main run's judge dir is never touched
    out = tmp_path / "control-judge"
    reg = asc.read_jsonl(out / "pairs.jsonl")
    man = json.loads((out / "pairs-manifest.json").read_text())
    bdoc = json.loads((out / "batches.json").read_text())
    main_row_keys = set(asc.read_jsonl(mdir / "pairs.jsonl")[0])
    assert len(reg) == len(stage_a) and all(main_row_keys <= set(r) for r in reg)
    assert man["schema"] == aj.PAIRS_SCHEMA and man["pairs_sha256"] == asc.sha256_file(out / "pairs.jsonl")
    assert set(man["inputs_sha256"]) >= {"records", "glossaries", "chunks_base", "chunks_control", "seed_pairs"}
    assert bdoc["stages"] == {"control": [1]} and sorted(bdoc["batches"][0]) == sorted(r["pair_id"] for r in reg)
    by_a = {p["pair_id"]: p for p in stage_a}
    for r in reg:
        (a_id,) = r["control_of"]
        a = by_a[a_id]
        assert r["pair_id"] == aj.pair_id(a["record_id"], r["chunk_id"]) and r["stage"] == "control"
        assert (r["question"], r["term"], r["arm"], r["c_paper"]) == (a["question"], a["term"], a["arm"], a["c_paper"])
    assert [r["order"] for r in reg] == sorted(r["order"] for r in reg)
    # the labelling world reads the control export, and the registry's text sha holds
    pl = aj.Pipeline(aj.load_config(cp), provenance={})
    world = aj.StudyWorld({"out_dir": str(out)}, pl.chunks(), {}, pl.selection(1, "C"))
    assert len(world.pairs()) == len(reg)
    with pytest.raises(SystemExit, match="frozen once built"):
        aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})
    with pytest.raises(SystemExit, match="use build-control"):
        aj.main(["--config", str(cp), "build-pairs", "--stage", "A"], prov={})
    with pytest.raises(SystemExit, match="main run's judge dir only"):
        pl.commit_stage_a()


@pytest.mark.parametrize("edit, match", [
    (lambda c, m: c.update(batch_size=7), "'batch_size' differs"),
    (lambda c, m: c.update(out_dir=m["out_dir"]), "out_dir must be a new directory"),
    (lambda c, m: c.update(out_dir=str(Path(m["out_dir"]) / "sub")), "out_dir must be a new directory"),
    (lambda c, m: c["chunk_exports"].update(base=c["chunk_exports"]["control"]), "chunk_exports must be"),
])
def test_build_control_refuses_a_config_that_is_not_the_main_one(judged: Path, tmp_path: Path, edit: Any,
                                                                 match: str) -> None:
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    c, m = json.loads(cp.read_text()), json.loads(main.read_text())
    edit(c, m)
    cp.write_text(json.dumps(c))
    with pytest.raises(SystemExit, match=match):
        aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})


def test_build_control_refuses_a_main_config_and_foreign_seed_rows(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    with pytest.raises(SystemExit, match="needs a control config"):
        aj.main(["--config", str(main), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})
    rows = asc.read_jsonl(seed_p)
    fx.write_jsonl(seed_p, [{**rows[0], "control_of": "0" * 16}])
    with pytest.raises(SystemExit, match="not a pair of the main run's registry"):
        aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})


def test_build_control_pairs_guards(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, stage_a = control_world(judged, tmp_path)
    cfg = aj.load_config(cp)
    chunks, _ = aj.chunk_arms(cfg)
    control_ids = set(asc.read_chunk_exports([cfg["chunk_exports"][aj.CONTROL_EXPORT]]))
    seed = asc.read_jsonl(seed_p)
    pairs, counts = aj.build_control_pairs(seed, stage_a, chunks, control_ids)
    assert counts["pairs"] == len(stage_a)
    # a chunk that is not in the control export (here: a stage-A chunk) is refused
    with pytest.raises(SystemExit, match="not in the control export"):
        aj.build_control_pairs([{**seed[0], "chunk_id": stage_a[0]["chunk_id"]}], stage_a, chunks, control_ids)
    # a control chunk of another paper than the stage-A passage is refused
    other = next(s for s, a in zip(seed, stage_a, strict=False) if a["c_paper"] != stage_a[0]["c_paper"])
    with pytest.raises(SystemExit, match="paper is not the stage-A passage's"):
        aj.build_control_pairs([{**seed[0], "chunk_id": other["chunk_id"]}], stage_a, chunks, control_ids)
    with pytest.raises(SystemExit, match="record_id is not"):
        aj.build_control_pairs([{**seed[0], "record_id": "elsewhere"}], stage_a, chunks, control_ids)


def test_build_control_hard_stops_on_a_main_registry_that_differs_from_its_manifest(judged: Path,
                                                                                   tmp_path: Path) -> None:
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    mdir = Path(json.loads(main.read_text())["out_dir"])
    with (mdir / "pairs.jsonl").open("a") as fh:
        fh.write("\n")
    with pytest.raises(aj.jd.p4.HardStop, match="differs from its manifest"):
        aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})
    assert not (tmp_path / "control-judge" / "pairs.jsonl").exists()


def test_control_config_still_enforces_the_prereg_sha(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    pre = tmp_path / "PREREG.md"
    pre.write_text("frozen\n")
    for p in (main, cp):
        c = json.loads(p.read_text())
        c["prereg"] = {"file": str(pre), "sha256": asc.sha256_file(pre)}
        p.write_text(json.dumps(c))
    pre.write_text("edited\n")
    with pytest.raises(SystemExit, match="HARD STOP: the prereg file's sha256"):
        aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main)],
                prov={})


# ------------------------------------------------------------------ build-control --any-paper (stage 1b)

#: the last commit whose alias_study_judge.py predates ``--any-paper``: the default build is compared
#: byte for byte against it
PRE_STAGE1B_COMMIT = "26088496"
FIXED_T = "2026-11-09T00:00:00+00:00"


def build_cmd(cp: Path, seed_p: Path, main: Path, *extra: str) -> list[str]:
    return ["--config", str(cp), "build-control", "--seed-pairs", str(seed_p), "--main-config", str(main), *extra]


def second_control_config(cp: Path, name: str) -> Path:
    """The same control config with another (new) out_dir."""
    c = json.loads(cp.read_text())
    c["out_dir"] = str(cp.parent / name)
    p = cp.parent / f"{name}.json"
    p.write_text(json.dumps(c))
    return p


def stage1b_world(judged: Path, tmp_path: Path) -> dict[str, Any]:
    """:func:`control_world` plus seeds whose chunks lie in other papers than their stage-A pair's
    window: one in another base paper with glossary entries, one in a neighbour paper. Each passage
    names an alias of its own paper and one of the window's paper, so the definitions block shows
    whose glossary it reads."""
    main, cp, seed_p, stage_a = control_world(judged, tmp_path)
    cfg = aj.load_config(cp)
    chunks, arm_of = aj.chunk_arms(cfg)
    p_arm = aj.paper_arms(chunks, arm_of)
    gl = aj.read_glossaries(aj.glossary_paths(cfg))
    a = next(p for p in stage_a if gl.get(str(p["c_paper"])))
    win = str(a["c_paper"])
    q = next(p for p in sorted(gl) if p_arm.get(p) == "base" and p not in (win, str(a["q_paper"])))
    n = next(p for p in sorted(p_arm) if p_arm[p] == "neighbour")
    titles = {str(c["paper"]): c["title"] for c in chunks.values() if c.get("title")}
    exp = Path(cfg["chunk_exports"][aj.CONTROL_EXPORT])
    rows = [json.loads(x) for x in exp.read_text().splitlines() if x.strip()]
    seed = asc.read_jsonl(seed_p)
    win_alias = gl[win][0]["alias"]
    other: dict[str, str] = {}
    for i, paper in enumerate((q, n)):
        own = (gl.get(paper) or [{"alias": "NOALIAS"}])[0]["alias"]
        cid = f"s1b-{i}-{paper}"
        text = f"Other-paper passage {i} of {paper}: the {own} and the {win_alias} values were reported."
        rows.append({"chunk_id": cid, "doc_id": f"d-{paper}", "content": text, "start_char": 0,
                     "end_char": len(text), "metadata": {"source_path": paper, "title": titles.get(paper, "")}})
        seed.append({"control_of": a["pair_id"], "record_id": a["record_id"], "chunk_id": cid, "stage": "stage1b"})
        other[paper] = cid
    fx.write_jsonl(exp, rows)
    fx.write_jsonl(seed_p, seed)
    return {"main": main, "cp": cp, "seed_p": seed_p, "stage_a": stage_a, "a": a, "win": win, "q": q, "n": n,
            "other": other, "titles": titles, "gl": gl, "win_alias": win_alias}


def world_for(cp: Path) -> tuple[aj.Pipeline, aj.StudyWorld]:
    pl = aj.Pipeline(aj.load_config(cp), provenance={})
    out = Path(pl.cfg["out_dir"])
    return pl, aj.StudyWorld({"out_dir": str(out)}, pl.chunks(), pl.glossary(), pl.selection(1, "C"))


def test_any_paper_seed_is_refused_without_the_flag_and_accepted_with_it(judged: Path, tmp_path: Path) -> None:
    w = stage1b_world(judged, tmp_path)
    out = Path(json.loads(w["cp"].read_text())["out_dir"])
    with pytest.raises(SystemExit, match="paper is not the stage-A passage's"):
        aj.main(build_cmd(w["cp"], w["seed_p"], w["main"]), prov={})
    # refused before the registry is written
    assert not any((out / f).exists() for f in ("pairs.jsonl", "pairs-manifest.json", "batches.json"))
    assert aj.main(build_cmd(w["cp"], w["seed_p"], w["main"], "--any-paper"), prov={}) == aj.EXIT_OK
    man = json.loads((out / "pairs-manifest.json").read_text())
    assert man["build"] == man["control"]["build"] == aj.STAGE1B_BUILD == "stage1b-any-paper"
    reg = {r["chunk_id"]: r for r in asc.read_jsonl(out / "pairs.jsonl")}
    a = w["a"]
    for paper, arm in ((w["q"], "base"), (w["n"], "neighbour")):
        r = reg[w["other"][paper]]
        # the passage side is the chunk's own paper and arm, the record side the stage-A pair's
        assert (r["c_paper"], r["arm"]) == (paper, arm) != (a["c_paper"], a["arm"])
        assert (r["question"], r["term"], r["q_paper"], r["record_kind"], r["record_id"]) == \
            (a["question"], a["term"], a["q_paper"], a["record_kind"], a["record_id"])
        assert r["control_of"] == [a["pair_id"]] and r["stage"] == aj.CONTROL_STAGE
        assert r["pair_id"] == aj.pair_id(a["record_id"], r["chunk_id"])
    c = man["counts"]
    assert c["pairs"] == len(reg) == len(w["stage_a"]) + 2
    assert c["passage_paper_not_stage_a_window_paper"] == 2
    assert c["same_paper"] == sum(r["c_paper"] == r["q_paper"] for r in reg.values())
    assert c["per_arm"] == dict(Counter(r["arm"] for r in reg.values()))
    assert c["definitions_coverage"] == aj.definitions_coverage(list(reg.values()), w["gl"])
    # the registry runs through the unchanged selection (C/E and the drift sample's arm strata)
    pl = aj.Pipeline(aj.load_config(w["cp"]), provenance={})
    assert {p["pair_id"] for p in pl.selection(1, "C")} == {r["pair_id"] for r in reg.values()}
    drift = pl.selection(1, "opus")
    assert drift and {p["arm"] for p in drift} <= set(asc.ARM_GROUPS)
    with pytest.raises(SystemExit, match="frozen once built"):
        aj.main(build_cmd(w["cp"], w["seed_p"], w["main"], "--any-paper"), prov={})


def test_any_paper_refuses_a_chunk_whose_paper_is_in_no_corpus_arm(judged: Path, tmp_path: Path) -> None:
    w = stage1b_world(judged, tmp_path)
    cfg = aj.load_config(w["cp"])
    chunks, arm_of = aj.chunk_arms(cfg)
    control_ids = set(asc.read_chunk_exports([cfg["chunk_exports"][aj.CONTROL_EXPORT]]))
    cid = w["other"][w["q"]]
    chunks = {**chunks, cid: {**chunks[cid], "paper": "nowhere"}}
    seed = [{"control_of": w["a"]["pair_id"], "record_id": w["a"]["record_id"], "chunk_id": cid}]
    with pytest.raises(SystemExit, match="no chunk in any corpus arm's export"):
        aj.build_control_pairs(seed, w["stage_a"], chunks, control_ids, any_paper=True, arm_of=arm_of)
    with pytest.raises(ValueError, match="needs arm_of"):
        aj.build_control_pairs(seed, w["stage_a"], chunks, control_ids, any_paper=True)


def test_any_paper_prompt_title_and_definitions_come_from_the_chunks_own_paper(judged: Path,
                                                                               tmp_path: Path) -> None:
    w = stage1b_world(judged, tmp_path)
    assert aj.main(build_cmd(w["cp"], w["seed_p"], w["main"], "--any-paper"), prov={}) == aj.EXIT_OK
    pl, world = world_for(w["cp"])
    reg = {p["chunk_id"]: p for p in world.pairs()}
    chunks = pl.chunks()
    for paper in (w["q"], w["n"]):
        row = reg[w["other"][paper]]
        text = chunks[row["chunk_id"]]["content"]
        prompt, cut = world.prompt(row)
        own_defs, own_cut = aj.jd.definitions_for(text, w["gl"].get(paper, ()))
        assert (prompt, cut) == (aj.jd.judge_prompt(row["question"], row["term"], text, w["titles"][paper], own_defs),
                                 own_cut)
        assert f"PASSAGE'S PAPER TITLE: {w['titles'][paper]}\n" in prompt
        assert w["titles"][paper] != w["titles"][w["win"]]
        # the window paper's alias is in the passage, but the window paper's glossary is not read
        assert f'"{w["win_alias"]}" stands for' not in prompt
        win_defs, _ = aj.jd.definitions_for(text, w["gl"][w["win"]])
        assert win_defs and prompt != aj.jd.judge_prompt(row["question"], row["term"], text,
                                                         w["titles"][w["win"]], win_defs)
    q_defs, _ = aj.jd.definitions_for(chunks[w["other"][w["q"]]]["content"], w["gl"][w["q"]])
    assert q_defs, "the base paper's own alias must be listed (its merged-glossary entry)"
    n_defs, _ = aj.jd.definitions_for(chunks[w["other"][w["n"]]]["content"], w["gl"].get(w["n"], ()))
    if not n_defs:
        assert "(none listed)" in world.prompt(reg[w["other"][w["n"]]])[0]


def test_definitions_never_depend_on_the_glossary_arm(judged: Path, tmp_path: Path) -> None:
    """The premise 'none-arm pairs had empty definitions' does not hold in the main study: a
    registry row carries no glossary arm, and :class:`StudyWorld` reads the merged glossary for
    every pair (§5.3, §6.2). A pair demanded only by a ``none`` leg gets its passage paper's
    definitions; a stage-1b row with the same record, chunk and paper gets the same prompt."""
    main = judged / "work" / "judge-config.json"
    cfg = aj.load_config(main)
    chunks, arm_of = aj.chunk_arms(cfg)
    gl = aj.read_glossaries(aj.glossary_paths(cfg))
    recs = asc.load_records(cfg["records"])
    rec = next(r for r in recs if gl.get(r["paper"]))
    paper = rec["paper"]
    cid = next(c for c, ch in chunks.items() if ch["paper"] == paper and c != rec["gold_chunks"][0])
    alias = gl[paper][0]["alias"]
    chunks = {**chunks, cid: {**chunks[cid], "content": chunks[cid]["content"] + f" The {alias} rose."}}
    none_leg = asc.leg_id("base", "none", "default")
    row = {"record_id": rec["record_id"], "condition": "base", "failed": False, "ranked_chunk_ids": [cid]}
    (pair,), _ = aj.build_pairs(recs, [(none_leg, row)], chunks, arm_of)
    assert "glossary" not in json.dumps(sorted(pair))  # no glossary arm on the row
    world = aj.StudyWorld({"out_dir": str(tmp_path)}, chunks, gl, [pair])
    prompt, _ = world.prompt(pair)
    assert f'- "{alias}" stands for' in prompt and "(none listed)" not in prompt
    s1b = {**pair, "stage": aj.CONTROL_STAGE, "control_of": ["x"]}
    assert world.prompt(s1b) == (prompt, 0)


def test_same_paper_seeds_give_identical_rows_and_prompts_in_both_modes(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    cp_1b = second_control_config(cp, "control-judge-1b")
    assert aj.main(build_cmd(cp, seed_p, main), prov={}) == aj.EXIT_OK
    assert aj.main(build_cmd(cp_1b, seed_p, main, "--any-paper"), prov={}) == aj.EXIT_OK
    out, out_1b = (Path(json.loads(p.read_text())["out_dir"]) for p in (cp, cp_1b))
    assert (out / "pairs.jsonl").read_bytes() == (out_1b / "pairs.jsonl").read_bytes()
    assert (out / "batches.json").read_bytes() == (out_1b / "batches.json").read_bytes()
    (_, w0), (_, w1) = world_for(cp), world_for(cp_1b)
    p0, p1 = w0.pairs(), w1.pairs()
    assert p0 == p1 and len(p0) > 0
    for x, y in zip(p0, p1, strict=True):
        assert w0.prompt(x) == w1.prompt(y)
    m0, m1 = (json.loads((d / "pairs-manifest.json").read_text()) for d in (out, out_1b))
    assert "build" not in m0 and "build" not in m0["control"]
    assert m1["counts"]["passage_paper_not_stage_a_window_paper"] == 0
    strip = ("build", "t", "passage_paper_not_stage_a_window_paper")

    def drop(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: drop(v) for k, v in x.items() if k not in strip}
        return x
    assert drop(m0) == drop(m1)


def test_build_control_pairs_default_ignores_arm_of(judged: Path, tmp_path: Path) -> None:
    main, cp, seed_p, stage_a = control_world(judged, tmp_path)
    cfg = aj.load_config(cp)
    chunks, arm_of = aj.chunk_arms(cfg)
    control_ids = set(asc.read_chunk_exports([cfg["chunk_exports"][aj.CONTROL_EXPORT]]))
    seed = asc.read_jsonl(seed_p)
    ref = aj.build_control_pairs(seed, stage_a, chunks, control_ids)
    assert aj.build_control_pairs(seed, stage_a, chunks, control_ids, any_paper=False, arm_of=arm_of) == ref
    assert "passage_paper_not_stage_a_window_paper" not in ref[1]


def _pre_stage1b_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib.util
    import subprocess

    repo = _HERE.parents[2]
    try:
        src = subprocess.run(["git", "-C", str(repo), "show",
                              f"{PRE_STAGE1B_COMMIT}:python/scripts/eval/alias_study_judge.py"],
                             check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git or commit {PRE_STAGE1B_COMMIT} unavailable")
    assert b"any_paper" not in src
    # deep enough that the module's out_dir-inside-the-repository check (its directory's
    # parents[2]) is pre1b/, which holds no out_dir
    d = tmp_path / "pre1b" / "a" / "b" / "c"
    d.mkdir(parents=True)
    (d / "alias_study_judge_pre1b.py").write_bytes(src)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("alias_study_judge_pre1b", d / "alias_study_judge_pre1b.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_build_control_is_byte_identical_to_the_pre_stage1b_code(judged: Path, tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    old = _pre_stage1b_module(tmp_path, monkeypatch)
    assert old.jd is aj.jd  # one harness module: the frozen clock below holds for both
    monkeypatch.setattr(aj.jd.g, "now_iso", lambda: FIXED_T)
    main, cp, seed_p, _ = control_world(judged, tmp_path)
    cp_old = second_control_config(cp, "control-judge-old")
    prov = {"commit": "c" * 40}
    assert old.main(build_cmd(cp_old, seed_p, main), prov=prov) == old.EXIT_OK
    assert aj.main(build_cmd(cp, seed_p, main), prov=prov) == aj.EXIT_OK
    out, out_old = (Path(json.loads(p.read_text())["out_dir"]) for p in (cp, cp_old))
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(p.name for p in out_old.iterdir())
    assert {"batches.json", "pairs-manifest.json", "pairs.jsonl"} <= set(names)
    for name in names:
        assert (out / name).read_bytes() == (out_old / name).read_bytes(), name


# ------------------------------------------------------------------ the report (§5)

def final(label: int | None, source: str = "agree", c: int | None = None, e: int | None = None) -> dict[str, Any]:
    return {"label": label, "source": source, "c_label": label if c is None else c, "e_label": label if e is None else e}


def report_rows() -> list[dict[str, Any]]:
    rows = []
    for i in range(20):
        a = i % 2
        ctl_label = 1 - a if i in (0, 3) else a  # one answers->not (i=3) and one not->answers (i=0)
        rows.append({"stage_a": final(a), "control": final(ctl_label, "tiebreak" if i == 5 else "agree"),
                     "stratum": "answers" if a else "not", "arm": asc.ARM_GROUPS[i % 3],
                     "coverage": [0.3, 0.6, 0.95][i % 3], "control_tokens": [100, 400, 600][i % 3],
                     "latex_preamble": i == 7, "in_spot_sheet": i < 7, "cover": [1] if i == 3 else [0] if i == 0 else None,
                     "control_pair": f"cp{i}",
                     "window": f"window text number {i} ends here.", "control_text": f"## Head\ncontrol {i}",
                     "window_tokens": 512, "section_crossing": i == 1, "split_sentence": False,
                     "one_untitled_section": i == 2})
    rows.append({**rows[1], "control": None})
    return rows


def test_compute_report_counts() -> None:
    m = ctl.compute_report(report_rows())
    assert m["n_mapped"] == 21 and m["n_judged_both"] == 20 and m["n_without_control_label"] == 1
    assert m["flip"]["k"] == 2 and m["flip"]["n"] == 20 and m["flip"]["wilson95"] is not None
    assert m["by_direction"] == {"not_to_answers": 1, "answers_to_not": 1}
    assert m["agreement_control_vs_stage_a"]["table"] == {"tp": 9, "fp": 1, "fn": 1, "tn": 9}
    assert m["cover_any"]["answers_to_not"] == {"with_cover_any": 1, "recovered": ctl.wilson(1, 1)}
    assert m["cover_any"]["not_to_answers"]["with_cover_any"] == 1
    # a not->answers flip whose cover set holds no answering chunk is a consistency miss, never a recovery
    assert m["cover_any"]["not_to_answers"]["cover_any_answers"]["k"] == 0
    assert m["cover_any"]["flipped_with_cover_any"] == 2
    assert m["by_section_structure"]["one_untitled_section"]["n"] == 1
    assert m["by_section_structure"]["sectioned"]["n"] == 19
    assert m["boundary"]["one_untitled_section_pairs"]["k"] == 1
    assert m["control_pairs_shared"] == {"control_pairs": 20, "shared_by_two_or_more_windows": 1,
                                         "windows_on_shared_control_pairs": 2}
    assert m["judge_stage"]["control"]["escalated"]["k"] == 1
    assert m["by_latex_preamble"]["latex_preamble"]["n"] == 1
    assert m["boundary"]["windows"]["ends_in_sentence_punctuation"]["k"] == 21
    assert m["boundary"]["control"]["holds_a_heading_line"]["k"] == 21
    assert "owner" not in json.dumps(m)  # §5.4 is dropped


def test_report_writer_refuses_text_ids_and_host_fields(tmp_path: Path) -> None:
    rows = report_rows()
    rec = {"schema": ctl.SCHEMA["report"], "metrics": ctl.compute_report(rows),
           "provenance": ctl.public_provenance({"commit": "c" * 40, "host": "somehost", "warnings": ["w"]})}
    passages = [r["window"] for r in rows] + ["a private passage that is long enough to be windowed, surely."]
    assert ctl.write_public(tmp_path / "ok.json", rec, {"pair-id-0001"}, passages)
    assert "host" not in json.loads((tmp_path / "ok.json").read_text())["provenance"]
    with pytest.raises(SystemExit, match="REFUSED"):
        ctl.write_public(tmp_path / "h.json", {**rec, "provenance": {"host": "somehost"}})
    with pytest.raises(SystemExit, match="REFUSED"):
        ctl.write_public(tmp_path / "t.json", {**rec, "note": passages[-1]}, (), passages)
    with pytest.raises(SystemExit, match="REFUSED"):
        ctl.write_public(tmp_path / "i.json", {**rec, "worst_pair": "pair-id-0001"}, {"pair-id-0001"})
    assert not any((tmp_path / n).exists() for n in ("h.json", "t.json", "i.json"))


def test_jsonl_rows_survive_unicode_line_separators(tmp_path: Path) -> None:
    """Record text with U+2028/U+2029/U+0085/U+001C must round-trip through the shared reader."""
    rows = [{"content": "a b c\x85d\x1ce", "i": 0}, {"content": "plain", "i": 1}]
    p = tmp_path / "rows.jsonl"
    p.write_bytes(ctl.jsonl_bytes(rows))
    assert p.read_bytes().count(b"\n") == 2
    assert asc.read_jsonl(p) == rows


# ------------------------------------------------------------------ cover-any split and the retest null

def test_cover_labels_tell_the_primary_chunk_from_the_others() -> None:
    # one cover pair controls two stage-A pairs: primary for s1, a non-primary chunk for s2
    vreg = [{"pair_id": "v1", "chunk_id": "k1", "control_of": ["s1", "s2"]},
            {"pair_id": "v2", "chunk_id": "k2", "control_of": ["s2"]},
            {"pair_id": "v3", "chunk_id": "k3", "control_of": ["s1"]},
            {"pair_id": "v4", "chunk_id": "k4", "control_of": ["s3"]}]  # no final row: ignored
    vfin = {"v1": {"label": 1}, "v2": {"label": None}, "v3": {"label": 0}}
    every, prim, other = ctl.cover_labels(vreg, vfin, {"s1": "k1", "s2": "k2", "s3": "k4"})
    assert every == {"s1": [1, 0], "s2": [1, 0]}
    assert prim == {"s1": 1, "s2": 0}  # unjudged = not
    assert other == {"s1": [0], "s2": [1]}


def split_rows() -> list[dict[str, Any]]:
    """Flipped pairs with a cover split: answers->not (stage A 1, control 0) in all four cells, and
    two not->answers (stage A 0, control 1); plus one unflipped pair, never counted."""
    rows = report_rows()[:1]  # i=0 is a not->answers flip; reuse its shape
    base = {k: v for k, v in rows[0].items() if k not in ("stage_a", "control", "cover")}

    def r(sa: int, cl: int, prim: int | None, other: list[int], i: int) -> dict[str, Any]:
        return {**base, "stage_a": final(sa), "control": final(cl), "control_pair": f"x{i}",
                "cover": ([prim] if prim is not None else []) + other, "cover_primary": prim, "cover_other": other}

    return [r(1, 0, 0, [1], 1), r(1, 0, 0, [1, 0], 2),  # other_only x2 (primary re-judged 0 = control: agree)
            r(1, 0, 1, [1], 3),                          # both (primary disagrees)
            r(1, 0, 1, [0], 4),                          # primary_only (disagrees)
            r(1, 0, 0, [], 5), r(1, 0, None, [0], 6),    # none x2 (one primary without a cover label)
            r(0, 1, 1, [0], 7), r(0, 1, 0, [], 8),       # not->answers: primary_only (agree), none (disagree)
            r(1, 1, 1, [1], 9)]                          # not flipped


def test_cover_split_four_ways_and_the_primary_rejudge() -> None:
    m = ctl.compute_report(split_rows())["cover_any"]
    a2n, n2a = m["split"]["answers_to_not"], m["split"]["not_to_answers"]
    assert {k: a2n[k] for k in (*ctl.COVER_SPLIT, "n")} == {"other_only": 2, "both": 1, "primary_only": 1,
                                                            "none": 2, "n": 6}
    assert a2n["primary_without_cover_label"] == 1
    assert a2n["recovered_by_other_chunk"] == ctl.wilson(3, 6)
    assert {k: n2a[k] for k in (*ctl.COVER_SPLIT, "n")} == {"other_only": 0, "both": 0, "primary_only": 1,
                                                            "none": 1, "n": 2}
    assert "recovered_by_other_chunk" not in n2a  # not->answers is never a recovery
    # same-bytes re-judge of the primary against the control label, pairs with a cover primary only
    assert a2n["primary_rejudge_agreement_selected"] == ctl.wilson(3, 5)
    assert n2a["primary_rejudge_agreement_selected"] == ctl.wilson(1, 2)
    assert m["split"]["primary_rejudge_agreement_selected"] == ctl.wilson(4, 7)
    assert set(m["split"]) == {"answers_to_not", "not_to_answers", "primary_rejudge_agreement_selected",
                               "by_route"}
    # the existing cover-any fields are unchanged
    assert m["answers_to_not"] == {"with_cover_any": 6, "recovered": ctl.wilson(4, 6)}  # any cover chunk
    assert m["not_to_answers"]["cover_any_answers"] == ctl.wilson(1, 2)


def test_cover_split_by_stage_a_route() -> None:
    rows = split_rows()
    for i in (0, 2, 6):  # other_only, both (answers->not) and primary_only (not->answers) go to tiebreak
        rows[i] = {**rows[i], "stage_a": final(label_of_row(rows[i]), "tiebreak")}
    br = ctl.compute_report(rows)["cover_any"]["split"]["by_route"]
    ag, tb = br["agree"], br["tiebreak_or_other"]
    assert {k: ag["answers_to_not"][k] for k in (*ctl.COVER_SPLIT, "n")} == {
        "other_only": 1, "both": 0, "primary_only": 1, "none": 2, "n": 4}
    assert ag["answers_to_not"]["recovered_by_other_chunk"] == ctl.wilson(1, 4)
    assert {k: tb["answers_to_not"][k] for k in (*ctl.COVER_SPLIT, "n")} == {
        "other_only": 1, "both": 1, "primary_only": 0, "none": 0, "n": 2}
    assert tb["answers_to_not"]["recovered_by_other_chunk"] == ctl.wilson(2, 2)
    assert ag["not_to_answers"]["n"] == 1 and ag["not_to_answers"]["none"] == 1
    assert tb["not_to_answers"]["n"] == 1 and tb["not_to_answers"]["primary_only"] == 1
    assert tb["not_to_answers"]["other_chunk_also_answers"] == ctl.wilson(0, 1)
    assert "recovered_by_other_chunk" not in tb["not_to_answers"]


def label_of_row(r: dict[str, Any]) -> int:
    return int(r["stage_a"]["label"])


REPORT_KEYS = {"n_mapped", "n_judged_both", "n_without_control_label", "flip", "by_direction", "direction_rates",
               "by_stratum", "by_arm", "by_route", "by_stratum_direction", "by_latex_preamble", "by_coverage_band",
               "by_control_token_band", "by_spot_sheet", "by_section_structure", "control_pairs_shared",
               "agreement_control_vs_stage_a", "judge_stage", "cover_any", "boundary"}


def test_report_keys_unchanged_without_the_new_inputs() -> None:
    m = ctl.compute_report(report_rows())
    assert set(m) == REPORT_KEYS
    assert set(m["cover_any"]) == {"flipped", "flipped_with_cover_any", "answers_to_not", "not_to_answers"}


def test_retest_metrics_flip_direction_and_route() -> None:
    def row(sa: int, rt: int | None, sa_src: str = "agree", rt_src: str = "agree", stratum: str = "answers"
            ) -> dict[str, Any]:
        return {"stage_a": final(sa, sa_src), "retest": None if rt is None else final(rt, rt_src),
                "stratum": stratum, "arm": "base"}

    rows = [row(1, 1), row(1, 1), row(1, 0, "tiebreak", "tiebreak"), row(0, 0, stratum="not"),
            row(0, 1, "tiebreak", stratum="not"), row(0, 0, "tiebreak", stratum="tiebroken"),
            row(1, None)]
    m = ctl.compute_retest(rows)
    assert m["n_seed"] == 7 and m["n_judged_both"] == 6 and m["n_without_retest_label"] == 1
    assert m["flip"] == ctl.wilson(2, 6)
    assert m["direction_rates"] == {"answers_to_not": ctl.wilson(1, 3), "not_to_answers": ctl.wilson(1, 3)}
    assert m["by_direction"] == {"answers_to_not": 1, "not_to_answers": 1}
    assert m["by_route"] == {"agree": ctl.wilson(0, 3), "tiebreak_or_other": ctl.wilson(2, 3)}
    assert m["by_route_retest"] == {"agree": ctl.wilson(1, 5), "tiebreak_or_other": ctl.wilson(1, 1)}
    assert m["by_stratum"]["not"] == ctl.wilson(1, 2)
    assert m["agreement_retest_vs_stage_a"]["table"] == {"tp": 2, "fp": 1, "fn": 1, "tn": 2}
    # unjudged = not: an unjudged retest of an answers pair is a flip
    assert ctl.compute_retest([row(1, None) | {"retest": final(None, "unjudged")}])["flip"]["k"] == 1


def test_retest_escalation_overlap_counts_and_jaccard() -> None:
    def row(sa_src: str | None, rt_src: str | None) -> dict[str, Any]:
        return {"stage_a": None if sa_src is None else final(1, sa_src),
                "retest": None if rt_src is None else final(1, rt_src), "stratum": "answers", "arm": "base"}

    rows = [row("tiebreak", "tiebreak"), row("tiebreak", "tiebreak"),      # both x2
            row("tiebreak", "agree"), row("unjudged", "agree"),           # stage A only x2 (unjudged != agree)
            row("agree", "tiebreak"),                                     # retest only
            row("agree", "agree"), row("agree", "agree"),                 # neither
            row("tiebreak", None), row(None, "tiebreak")]                 # not judged both: not counted
    m = ctl.compute_retest(rows)["escalation_overlap"]
    assert m == {"n": 7, "stage_a_escalated": 4, "retest_escalated": 3, "both": 2, "stage_a_only": 2, "retest_only": 1,
                 "jaccard": round(2 / 5, 4)}
    assert ctl.compute_retest([row("agree", "agree")])["escalation_overlap"]["jaccard"] is None


def test_retest_tiebreak_replicate_counts_null_verdicts_and_sha_mismatches() -> None:
    def lab(v: Any, prompt: str = "p", system: str = "s") -> dict[str, Any]:
        return {"verdict": v, "prompt_sha256": prompt, "system_sha256": system}

    sa = {"a": lab(True), "b": lab(False), "c": lab(True, prompt="p-old"), "d": lab(None), "e": lab(True),
          "x": lab(True)}
    rt = {"a": lab(True), "b": lab(True), "c": lab(True), "d": lab(False), "e": lab(True, system="s2"),
          "f": lab(True), "x": lab(False)}
    m = ctl.tiebreak_replicate(["a", "b", "c", "d", "e", "f", "g"], sa, rt)  # x: not a registry pair
    assert m["n"] == 5 and m["n_null_verdict"] == 1
    assert m["same_prompt_sha256"] == 4 and m["same_system_sha256"] == 4
    assert m["verdict_agreement"] == ctl.wilson(3, 4)  # a, c, e agree; b disagrees; d has a null verdict
    assert "length" not in json.dumps(m)


def test_control_flip_by_stage_a_route() -> None:
    base = {k: v for k, v in report_rows()[0].items() if k not in ("stage_a", "control")}
    rows = [{**base, "stage_a": final(1, "agree"), "control": final(0)},
            {**base, "stage_a": final(1, "agree"), "control": final(1)},
            {**base, "stage_a": final(0, "agree"), "control": final(0)},
            {**base, "stage_a": final(0, "tiebreak"), "control": final(1)},
            {**base, "stage_a": final(1, "unjudged"), "control": final(1)},
            {**base, "stage_a": final(1, "tiebreak"), "control": None}]  # not judged both
    assert ctl.compute_report(rows)["by_route"] == {"agree": ctl.wilson(1, 3),
                                                    "tiebreak_or_other": ctl.wilson(1, 2)}


def test_retest_fields_absent_without_the_retest() -> None:
    m = ctl.compute_report(report_rows())
    assert "retest" not in m and "escalation_overlap" not in m and "tiebreak_replicate" not in m


def test_retest_rows_refuse_a_seed_that_is_not_its_stage_a_pair() -> None:
    pid = aj.pair_id("r1", "c1")
    sa = {pid: final(1)}
    rows = ctl.retest_rows([{"control_of": pid, "record_id": "r1", "chunk_id": "c1", "stratum": "answers",
                             "arm": "base"}], sa, {pid: final(0)})
    assert ctl.compute_retest(rows)["flip"]["k"] == 1
    with pytest.raises(SystemExit, match="not the stage-A pair it retests"):
        ctl.retest_rows([{"control_of": pid, "record_id": "r1", "chunk_id": "c2", "stratum": "answers",
                          "arm": "base"}], sa, {})


def retest_world(judged: Path, tmp_path: Path, n: int = 10) -> tuple[Path, Path, list[dict[str, Any]]]:
    main = judged / "work" / "judge-config.json"
    raw = json.loads(main.read_text())
    stage_a = [p for p in asc.read_jsonl(Path(raw["out_dir"]) / "pairs.jsonl") if p.get("stage", "A") == "A"][:n]
    cdir = tmp_path / "control-run"
    cdir.mkdir()
    seed = [{"control_of": p["pair_id"], "record_id": p["record_id"], "stage_a_chunk_id": p["chunk_id"],
             "chunk_id": f"ctl-{i}", "doc_id": "d", "stratum": "answers", "arm": p["arm"], "in_spot_sheet": False}
            for i, p in enumerate(stage_a)]
    fx.write_jsonl(cdir / "pairs-seed.jsonl", seed)
    return main, cdir, stage_a


def test_retest_seed_writes_the_stage_a_windows_and_their_pair_ids(judged: Path, tmp_path: Path) -> None:
    main, cdir, stage_a = retest_world(judged, tmp_path)
    res = ctl.run_retest_seed(main, cdir, tmp_path / "rt")
    out = tmp_path / "rt"
    windows = {p["chunk_id"] for p in stage_a}
    assert res["seed_rows"] == len(stage_a) and res["windows"] == len(windows)
    raw = json.loads(main.read_text())
    orig = asc.read_chunk_exports(list(raw["chunk_exports"].values()))
    got = asc.read_chunk_exports([out / "chunks-retest.jsonl"])
    assert set(got) == windows and all(got[c] == orig[c] for c in got)
    # the control-shaped config: build_control_pairs gives the stage-A pair ids themselves
    ccfg = {**aj.load_config(main), "chunk_exports": {**raw["chunk_exports"],
                                                       aj.CONTROL_EXPORT: str(out / "chunks-retest.jsonl")}}
    chunks, _ = aj.chunk_arms(ccfg)
    pairs, _ = aj.build_control_pairs(asc.read_jsonl(out / "pairs-seed-retest.jsonl"), stage_a, chunks, set(got))
    assert sorted(p["pair_id"] for p in pairs) == sorted(p["pair_id"] for p in stage_a)
    summary = json.loads((out / "retest-seed.json").read_text())
    assert summary["chunks_sha256"] == asc.sha256_file(out / "chunks-retest.jsonl")
    assert not asc.public_problems(summary, windows | {p["pair_id"] for p in stage_a}, [])


def test_retest_seed_refuses_a_mismatched_chunk_and_a_missing_window(judged: Path, tmp_path: Path) -> None:
    main, cdir, stage_a = retest_world(judged, tmp_path)
    seed = asc.read_jsonl(cdir / "pairs-seed.jsonl")
    fx.write_jsonl(cdir / "pairs-seed.jsonl", [{**seed[0], "stage_a_chunk_id": stage_a[1]["chunk_id"]}, *seed[1:]])
    with pytest.raises(SystemExit, match="not that stage-A pair's"):
        ctl.run_retest_seed(main, cdir, tmp_path / "rt1")
    fx.write_jsonl(cdir / "pairs-seed.jsonl", seed)
    # every export without the first window
    raw = json.loads(main.read_text())
    gone = stage_a[0]["chunk_id"]
    for arm, p in list(raw["chunk_exports"].items()):
        lines = [x for x in Path(p).read_text(encoding="utf-8").splitlines(keepends=True)
                 if not x.strip() or json.loads(x).get("chunk_id", json.loads(x).get("id")) != gone]
        dst = tmp_path / f"export-{arm}.jsonl"
        dst.write_text("".join(lines), encoding="utf-8")
        raw["chunk_exports"][arm] = str(dst)
    cut = tmp_path / "judge-config-cut.json"
    cut.write_text(json.dumps(raw))
    with pytest.raises(SystemExit, match="1 stage-A window"):
        ctl.run_retest_seed(cut, cdir, tmp_path / "rt2")
    assert not (tmp_path / "rt2" / "chunks-retest.jsonl").exists()


def test_argon_labels_plain_read_refuses_a_duplicate_pair(tmp_path: Path) -> None:
    import alias_study_judge as aj

    f = tmp_path / aj.LABEL_FILES["argon"]
    f.write_text(json.dumps({"pair_id": "p1", "verdict": True}) + "\n" + json.dumps({"pair_id": "p2", "verdict": False}) + "\n")
    assert set(ctl.argon_labels(tmp_path)) == {"p1", "p2"}
    f.write_text(f.read_text() + json.dumps({"pair_id": "p1", "verdict": False}) + "\n")
    with pytest.raises(SystemExit):
        ctl.argon_labels(tmp_path)


# ================================================================== the confirmatory test (PREREG §11)
# alias-boundary-confirm-2026-11: synthetic fixtures only; no real pool, no real label.


PSHA = "ab" * 32


def cfin(pid: str, rec: str, win: str, arm: str, *, stage: str = "A", source: str = "agree",
         label: int | None = 1) -> dict[str, Any]:
    return {"pair_id": pid, "record_id": rec, "chunk_id": win, "arm": arm, "stage": stage, "source": source,
            "label": label}


def pool_fixture() -> list[dict[str, Any]]:
    """Per arm: records with several pairs; windows shared between records inside an arm; rows
    that are not in the pool (not agreed, agreed not, stage B, a control pair, a control window)."""
    rows: list[dict[str, Any]] = []
    for a in asc.ARM_GROUPS:
        for r in range(8):
            for k in range(3):
                rows.append(cfin(f"{a}-r{r}-k{k}", f"rec{r}", f"{a}-w{r}-{k}", a))
        # two records sharing one window
        rows.append(cfin(f"{a}-s1", "rec20", f"{a}-shared", a))
        rows.append(cfin(f"{a}-s2", "rec21", f"{a}-shared", a))
        rows.append(cfin(f"{a}-tb", "rec30", f"{a}-wtb", a, source="tiebreak"))
        rows.append(cfin(f"{a}-no", "rec31", f"{a}-wno", a, label=0))
        rows.append(cfin(f"{a}-B", "rec32", f"{a}-wB", a, stage="B"))
        rows.append(cfin(f"{a}-uj", "rec33", f"{a}-wuj", a, source="unjudged", label=None))
        rows.append(cfin(f"{a}-ctl", "rec34", f"{a}-wctl", a))          # a control pair
        rows.append(cfin(f"{a}-cw", "rec35", f"{a}-ctlwin", a))          # on a control window
    return rows


EXCL_P = {f"{a}-ctl" for a in asc.ARM_GROUPS}
EXCL_W = {f"{a}-ctlwin" for a in asc.ARM_GROUPS} | {"elsewhere"}


def test_confirm_constants_and_schemas() -> None:
    assert ctl.CONFIRM_SEED == "261105|alias|confirm|draw" and ctl.CONFIRM_BOOT_SEED == "261105|alias|confirm|boot"
    assert ctl.SCHEMA["draw-confirm"] == "alias-confirm-draw/1"
    assert ctl.SCHEMA["confirm-report"] == "alias-confirm-report/1"
    assert ctl.CONFIRM_STAGE_A_COMMIT_SHA256.startswith("f53ca9d0") and ctl.CONFIRM_CONTROL_SAMPLE_SHA256.startswith(
        "64a8bbc1")
    assert ctl.CONFIRM_N_FLOOR == 316 == math.ceil(0.90 * 351)
    committed = json.loads((ctl.REPO / "docs/plans/results/alias-study-2026-11/control/artifacts/control-draw.json")
                           .read_text())
    assert committed["sample_sha256"] == ctl.CONFIRM_CONTROL_SAMPLE_SHA256
    assert committed["stage_a_commit_sha256"] == ctl.CONFIRM_STAGE_A_COMMIT_SHA256
    assert ctl.load_power()["n_floor"] == 316


def test_draw_confirm_pool_exclusions_caps_and_windows() -> None:
    rows = pool_fixture()
    picked, c = ctl.draw_confirm(rows, exclude_pairs=EXCL_P, exclude_windows=EXCL_W, n=12)
    ids = {f["pair_id"] for f in picked}
    assert not ids & EXCL_P and not {f["chunk_id"] for f in picked} & EXCL_W
    assert all(f["source"] == "agree" and f["label"] == 1 and f["stage"] == "A" for f in picked)
    assert all(f["stratum"] == "answers" for f in picked)
    assert len({(f["record_id"], f["arm"]) for f in picked}) == len(picked)        # one per (record, arm)
    assert len({f["chunk_id"] for f in picked}) == len(picked)                      # one per window
    assert c["agreed_answers"]["total"] == 3 * (24 + 2 + 2) and c["in_control_sample"]["total"] == 3
    assert c["shared_window_excluded"]["total"] == 3 and c["pool"]["total"] == 3 * 26
    assert c["availability"]["cells"] == {"base": 10, "neighbour": 10, "random": 10}
    assert c["quotas"] == {"base": 4, "neighbour": 4, "random": 4} and c["n_drawn"] == 12
    assert c["pool_windows_multi_record"] == 3 and c["pool_pairs_on_multi_record_windows"] == 6
    assert c["pool_records_by_arms"] == {"1": 0, "2": 0, "3": 10} and c["pool_max_pairs_per_record"] == 9
    assert c["drawn_control_pairs"] == c["drawn_shared_windows"] == c["drawn_record_arm_repeats"] == 0
    # the walk takes, per arm, the first free pairs in sha256(seed|arm|pair id) order: re-derived here
    for a in asc.ARM_GROUPS:
        cand = sorted((f for f in rows if f["arm"] == a and f["stage"] == "A" and f["source"] == "agree"
                       and f["label"] == 1 and f["pair_id"] not in EXCL_P and f["chunk_id"] not in EXCL_W),
                      key=lambda f: hashlib.sha256(f"261105|alias|confirm|draw|{a}|{f['pair_id']}".encode())
                      .hexdigest())
        want, recs, wins = [], set(), set()
        for f in cand:
            if f["record_id"] in recs or f["chunk_id"] in wins:
                continue
            recs.add(f["record_id"])
            wins.add(f["chunk_id"])
            want.append(f["pair_id"])
        assert [f["pair_id"] for f in picked if f["arm"] == a] == want[:4]


def test_draw_confirm_is_deterministic_and_input_order_free() -> None:
    rows = pool_fixture()
    one = ctl.draw_confirm(rows, exclude_pairs=EXCL_P, exclude_windows=EXCL_W, n=20)
    two = ctl.draw_confirm(list(reversed(rows)), exclude_pairs=EXCL_P, exclude_windows=EXCL_W, n=20)
    assert one == two
    other = ctl.draw_confirm(rows, exclude_pairs=EXCL_P, exclude_windows=EXCL_W, n=20, seed="another|seed")
    assert [f["pair_id"] for f in other[0]] != [f["pair_id"] for f in one[0]]


def test_draw_confirm_window_rule_costs_an_arm_cells_and_the_shortfall_moves() -> None:
    """random: 4 records whose every pair sits on one of 2 shared windows (4 cells, 2 achievable);
    base 6 cells, neighbour 6 cells. n = 12: quotas 4/4/4 by cells; random achieves 2; the
    shortfall of 2 goes to base and neighbour (1 each), whose walks continue."""
    rows = []
    for a in ("base", "neighbour"):
        rows += [cfin(f"{a}{r}-{k}", f"r{r}", f"{a}w{r}-{k}", a) for r in range(6) for k in range(2)]
    rows += [cfin(f"rnd{r}-{k}", f"r{r}", f"rndw{k}", "random") for r in range(4) for k in range(2)]
    picked, c = ctl.draw_confirm(rows, exclude_pairs=(), exclude_windows=(), n=12)
    assert c["availability"]["cells"] == {"base": 6, "neighbour": 6, "random": 4}
    assert c["quotas"] == {"base": 4, "neighbour": 4, "random": 4}
    assert c["achieved_first_pass"] == {"base": 4, "neighbour": 4, "random": 2}
    assert c["availability"]["achieved"] == {"base": 5, "neighbour": 5, "random": 2} and c["n_drawn"] == 12
    assert c["reallocation_rounds"] == 1 and c["exhausted"]["random"]
    # a shortfall of 3: the remainder goes to base first
    picked, c = ctl.draw_confirm(rows, exclude_pairs=(), exclude_windows=(), n=13)
    assert c["quotas"] == {"base": 5, "neighbour": 4, "random": 4}
    assert c["availability"]["achieved"] == {"base": 6, "neighbour": 5, "random": 2}
    # an odd second-pass shortfall: random reaches 1 of its 4 (one window for all), the 3 left go
    # base 2, neighbour 1 (remainder to base)
    one_win = [r for r in rows if r["arm"] != "random"] + [cfin(f"rx{r}", f"r{r}", "rx-w", "random") for r in range(4)]
    _, c1 = ctl.draw_confirm(one_win, exclude_pairs=(), exclude_windows=(), n=12)
    assert c1["availability"]["achieved"] == {"base": 6, "neighbour": 5, "random": 1}
    # more than the pool can give: every walk exhausted, no error
    picked, c = ctl.draw_confirm(rows, exclude_pairs=(), exclude_windows=(), n=50)
    assert c["availability"]["achieved"] == {"base": 6, "neighbour": 6, "random": 2} and c["n_drawn"] == 14
    assert all(c["exhausted"].values())


def test_draw_confirm_refuses_an_unknown_arm_and_a_duplicate_pair() -> None:
    with pytest.raises(SystemExit, match="carries arm"):
        ctl.draw_confirm([cfin("p", "r", "w", "random1")], exclude_pairs=(), exclude_windows=(), n=1)
    with pytest.raises(SystemExit, match="twice"):
        ctl.draw_confirm([cfin("p", "r", "w", "base"), cfin("p", "r", "w", "base")], exclude_pairs=(),
                         exclude_windows=(), n=1)


# ---- run_draw_confirm on the dry run's committed stage A

def confirm_inputs(judged: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n_ctl: int = 4, n_draw: int = 9
                   ) -> tuple[Path, Path, Path, dict[str, Any]]:
    """A forged exploratory control sample (four stage-A pairs) with its control-draw.json, and the
    §4.1 constants pointed at this fixture's stage A and sample."""
    cfgp = judged / "work" / "judge-config.json"
    sa = spot.committed_stage_a(aj.load_config(cfgp))
    ans = [f for f in sa["final"] if f["source"] == "agree" and f["label"] == 1]
    ctl_rows = [{"pair_id": f["pair_id"], "record_id": f["record_id"], "chunk_id": f["chunk_id"],
                 "stratum": "answers", "arm": f["arm"], "in_spot_sheet": False} for f in ans[:n_ctl]]
    priv = tmp_path / "priv"
    priv.mkdir()
    cs_p = fx.write_jsonl(priv / "control-sample.jsonl", ctl_rows)
    cd_p = priv / "control-draw.json"
    cd_p.write_text(json.dumps({"sample_sha256": asc.sha256_file(cs_p), "stage_a_commit_sha256": sa["commit_sha256"]}))
    monkeypatch.setattr(ctl, "CONFIRM_STAGE_A_COMMIT_SHA256", sa["commit_sha256"])
    monkeypatch.setattr(ctl, "CONFIRM_CONTROL_SAMPLE_SHA256", asc.sha256_file(cs_p))
    # the draw size and the §4.1 counts pinned to this fixture (the real ones are asserted elsewhere)
    monkeypatch.setattr(ctl, "CONFIRM_N_DRAW", n_draw)
    monkeypatch.setattr(ctl, "CONFIRM_POOL_COUNTS", fixture_pool_counts(sa, ctl_rows, n_draw))
    pre = tmp_path / "PREREG.md"
    pre.write_text("frozen text\n")
    monkeypatch.setattr(ctl, "CONFIRM_PREREG_FILE", pre)      # the "committed" prereg of this fixture
    sa = {**sa, "prereg": pre, "psha": asc.sha256_file(pre)}
    return cfgp, cd_p, cs_p, sa


def fixture_pool_counts(sa: dict[str, Any], ctl_rows: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """The fixture's own recount, in the shape of :data:`ctl.CONFIRM_POOL_COUNTS`."""
    _, c = ctl.draw_confirm(sa["final"], exclude_pairs={r["pair_id"] for r in ctl_rows},
                            exclude_windows={r["chunk_id"] for r in ctl_rows}, n=n,
                            paper_of={p["pair_id"]: str(p["c_paper"]) for p in sa["pairs"]})
    exp = {k: c[k] for k in ctl.CONFIRM_POOL_COUNTS if k != "cells"}
    exp["cells"] = {**c["availability"]["cells"], "total": sum(c["availability"]["cells"].values())}
    return exp


def test_run_draw_confirm_writes_the_control_row_shape_and_a_public_draw(judged: Path, tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    cfgp, cd_p, cs_p, sa = confirm_inputs(judged, tmp_path, monkeypatch)
    r = ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "c", sa["psha"], sa["prereg"])
    sample = asc.read_jsonl(tmp_path / "c" / "sample.jsonl")
    assert r["n"] == len(sample) == 9
    assert all(set(x) == {"pair_id", "record_id", "chunk_id", "stratum", "arm", "in_spot_sheet"} for x in sample)
    assert all(x["stratum"] == "answers" and x["in_spot_sheet"] is False for x in sample)
    ctl_ids = {x["pair_id"] for x in asc.read_jsonl(cs_p)}
    ctl_wins = {x["chunk_id"] for x in asc.read_jsonl(cs_p)}
    assert not {x["pair_id"] for x in sample} & ctl_ids and not {x["chunk_id"] for x in sample} & ctl_wins
    by_id = {f["pair_id"]: f for f in sa["final"]}
    assert all(by_id[x["pair_id"]]["source"] == "agree" and by_id[x["pair_id"]]["label"] == 1 for x in sample)
    d = json.loads((tmp_path / "c" / "draw.json").read_text())
    assert d["schema"] == "alias-confirm-draw/1" and d["exploratory"] is False
    assert d["confirmatory"] == {"prereg": "alias-boundary-confirm-2026-11", "prereg_sha256": sa["psha"]}
    assert d["sample_sha256"] == asc.sha256_file(tmp_path / "c" / "sample.jsonl") == r["sample_sha256"]
    assert d["control_sample_sha256"] == asc.sha256_file(cs_p) and d["seed"] == ctl.CONFIRM_SEED
    assert "host" not in d["provenance"] and d["counts"]["availability"]["cells"]
    text = json.dumps(d)
    assert not any(f["pair_id"] in text or f["chunk_id"] in text for f in sa["final"])
    # a second run writes the same sample (write-once accepts identical bytes)
    assert ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "c", sa["psha"], sa["prereg"])["sample_sha256"] == r["sample_sha256"]


def test_run_draw_confirm_refuses_sha_mismatches(judged: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    cfgp, cd_p, cs_p, sa = confirm_inputs(judged, tmp_path, monkeypatch)
    good = cs_p.read_text()
    # a control sample whose sha differs from control-draw.json's
    cs_p.write_text(good + "\n")
    with pytest.raises(SystemExit, match="not control-draw.json's sample_sha256"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x1", sa["psha"], sa["prereg"])
    cs_p.write_text(good)
    # control-draw.json and the sample agree, but neither is §4.1's
    monkeypatch.setattr(ctl, "CONFIRM_CONTROL_SAMPLE_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="not the one PREREG §4.1 names"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x2", sa["psha"], sa["prereg"])
    monkeypatch.setattr(ctl, "CONFIRM_CONTROL_SAMPLE_SHA256", asc.sha256_file(cs_p))
    # the committed stage A is not §4.1's
    monkeypatch.setattr(ctl, "CONFIRM_STAGE_A_COMMIT_SHA256", "1" * 64)
    with pytest.raises(SystemExit, match="stage-A commit is not the one PREREG"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x3", sa["psha"], sa["prereg"])
    monkeypatch.setattr(ctl, "CONFIRM_STAGE_A_COMMIT_SHA256", sa["commit_sha256"])
    # control-draw.json names another stage-A commit
    cd = json.loads(cd_p.read_text())
    cd_p.write_text(json.dumps({**cd, "stage_a_commit_sha256": "2" * 64}))
    with pytest.raises(SystemExit, match="names another stage-A commit"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x4", sa["psha"], sa["prereg"])
    cd_p.write_text(json.dumps(cd))
    with pytest.raises(SystemExit, match="64 lower-case hex"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x5", "not-a-sha", sa["prereg"])
    # the prereg file must be the sha's (a wrong sha stops step 1), and it is required
    with pytest.raises(SystemExit, match="prereg file's sha256 is not --confirmatory"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x6", PSHA, sa["prereg"])
    with pytest.raises(SystemExit, match="needs --prereg"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x7", sa["psha"], None)
    # the draw size is pinned
    with pytest.raises(SystemExit, match="fixes the draw at 9"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 10, tmp_path / "x8", sa["psha"], sa["prereg"])
    # a recount that does not reproduce §4.1 is refused, naming the figure
    pinned = json.loads(json.dumps(ctl.CONFIRM_POOL_COUNTS))
    for edit, name in ((lambda c: c["shared_window_excluded"].update(total=c["shared_window_excluded"]["total"] + 1),
                        "shared_window_excluded.total"),
                       (lambda c: c["cells"].update(random=c["cells"]["random"] - 1), "cells.random"),
                       (lambda c: c.update(pool_windows_cross_arm=1), "pool_windows_cross_arm"),
                       (lambda c: c.update(stage_a_pairs=c["stage_a_pairs"] + 1), "stage_a_pairs")):
        bad = json.loads(json.dumps(pinned))
        edit(bad)
        monkeypatch.setattr(ctl, "CONFIRM_POOL_COUNTS", bad)
        with pytest.raises(SystemExit, match=f"does not reproduce PREREG §4.1.*{name}"):
            ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "x9", sa["psha"], sa["prereg"])
    monkeypatch.setattr(ctl, "CONFIRM_POOL_COUNTS", pinned)
    assert not any((tmp_path / f"x{i}" / "sample.jsonl").exists() for i in range(1, 10))


# ---- confirm_table on hand-checkable tables

def fr(label: int | None, source: str | None = None, **kw: Any) -> dict[str, Any]:
    src = source or ("unjudged" if label is None else "agree")
    return {"label": label, "source": src, "c_label": label, "e_label": label, **kw}


def crow(i: int, rec: str, x: int | None, y: int | None, arm: str = "base", *, cu: bool = False,
         ru: bool = False) -> dict[str, Any]:
    """One pair: X / Y as the flip events (X = 1 -> control label 0); None = no final row;
    ``cu`` / ``ru`` = a real unjudged final row in that arm."""
    c = fr(None) if cu else None if x is None else fr(1 - x)
    r = fr(None) if ru else None if y is None else fr(1 - y)
    return {"control_of": f"pair{i:04d}", "record_id": rec, "arm": arm, "control": c, "retest": r}


def singletons(b: int, c: int, both: int = 0, neither: int = 0) -> list[dict[str, Any]]:
    cells = [(1, 0)] * b + [(0, 1)] * c + [(1, 1)] * both + [(0, 0)] * neither
    return [crow(i, f"rec{i:04d}", x, y) for i, (x, y) in enumerate(cells)]


def test_confirm_table_singletons_b10_c2_sign_flip_equals_mcnemar_79_over_4096() -> None:
    t = ctl.confirm_table(singletons(10, 2, both=3, neither=5), n_boot=2000)
    assert (t["b"], t["c"], t["n"], t["records"]) == (10, 2, 20, 20)
    assert t["table"] == {"both_not": 3, "control_only": 10, "retest_only": 2, "neither": 5}
    assert t["primary"]["p"] == pytest.approx(79 / 4096) == t["mcnemar_sensitivity"]["p"]
    assert t["primary"]["p_method"] == "enumeration" and t["d"] == pytest.approx(8 / 20)
    assert t["rates"]["control_not"]["k"] == 13 and t["rates"]["retest_not"]["k"] == 5
    assert t["clusters"]["score_distribution"] == {"-1": 2, "0": 8, "1": 10}


def test_confirm_table_a_two_pair_record_is_one_cluster() -> None:
    # R1: two pairs, both control-only (S = +2); R2: control-only (+1); R3: retest-only (-1)
    rows = [crow(0, "R1", 1, 0, "base"), crow(1, "R1", 1, 0, "neighbour"), crow(2, "R2", 1, 0), crow(3, "R3", 0, 1)]
    t = ctl.confirm_table(rows, n_boot=500)
    # T = 2; of the 8 sign vectors of (2, 1, -1), T* >= 2 for (+,+,-), (+,+,+), (+,-,+): p = 3/8
    assert t["primary"]["p"] == pytest.approx(3 / 8) and t["primary"]["statistic"] == 2
    # McNemar treats the four pairs as independent: P(Bin(4, 1/2) >= 3) = 5/16
    assert t["mcnemar_sensitivity"]["p"] == pytest.approx(5 / 16)
    assert t["records"] == 3 and t["clusters"]["records_by_arms"] == {"1": 2, "2": 1, "3": 0}
    assert t["clusters"]["score_distribution"] == {"-1": 1, "1": 1, "2": 1}


def test_confirm_table_b_equals_c_is_no_evidence() -> None:
    t = ctl.confirm_table(singletons(6, 6, neither=10), n_boot=500)
    assert t["primary"]["p"] > 0.5 and t["mcnemar_sensitivity"]["p"] > 0.5 and t["d"] == 0


def test_tango_score_interval_fixtures_from_the_prereg_formula() -> None:
    def r4(x: dict[str, Any]) -> list[float]:
        return [round(v, 4) for v in x["ci"]]

    assert r4(ctl.tango_score_ci(10, 2, 100, 0.90)) == [0.0260, 0.1437]
    assert r4(ctl.tango_score_ci(10, 2, 100, 0.95)) == [0.0143, 0.1580]
    assert r4(ctl.tango_score_ci(5, 5, 100, 0.90)) == [-0.0571, 0.0571]
    lo, hi = ctl.tango_score_ci(5, 5, 100)["ci"]
    assert lo == pytest.approx(-hi, abs=1e-9)                     # symmetric about 0
    assert r4(ctl.tango_score_ci(0, 0, 50, 0.90)) == [-0.0513, 0.0513]
    # b = c = 0: the end solves n δ² = z² δ (1 - δ), δ = z² / (n + z²)
    z = ctl.tango_score_ci(0, 0, 50)["z"]
    assert ctl.tango_score_ci(0, 0, 50)["ci"][1] == pytest.approx(z * z / (50 + z * z), abs=1e-9)
    for b, c, n in ((10, 2, 100), (5, 5, 100), (0, 3, 40), (7, 0, 33)):
        assert ctl.tango_p21(b, c, n, (b - c) / n) == pytest.approx(c / n)   # p̃21(δ̂) = c / n
    assert ctl.tango_score_ci(3, 0, 0)["ci"] is None
    # the score statistic is exactly z at both ends
    t = ctl.tango_score_ci(10, 2, 100)
    for d in t["ci"]:
        stat = abs(10 - 2 - 100 * d) / math.sqrt(100 * (2 * ctl.tango_p21(10, 2, 100, d) + d * (1 - d)))
        assert stat == pytest.approx(t["z"], abs=1e-6)


def test_confirm_bootstrap_is_page_bootstrap_ci_with_records_as_pages() -> None:
    rows = [crow(0, "R1", 1, 0), crow(1, "R1", 1, 1), crow(2, "R2", 0, 1), crow(3, "R3", 1, 0),
            crow(4, "R3", 1, 0, "random"), crow(5, "R4", 0, 0), crow(6, "R5", 1, 0)]
    t = ctl.confirm_table(rows, n_boot=3000)
    seed = asc.seed_int("261105|alias|confirm|boot")
    pages = ["R1", "R1", "R2", "R3", "R3", "R4", "R5"]
    xs, ys = [1, 1, 0, 1, 1, 0, 1], [0, 1, 1, 0, 0, 0, 0]
    ref = cs.page_bootstrap_ci(pages, xs, ys, n_boot=3000, seed=seed, level=0.90)
    assert t["bootstrap"]["ci"] == ref["ci"] and t["bootstrap"]["seed"] == seed
    assert t["bootstrap"]["upper_one_sided_95"] == ref["ci"][1]
    # independently: d* = Σ S_j / Σ n_j over records drawn with replacement, 5th / 95th percentiles
    s, nn = np.array([1, -1, 2, 0, 1.0]), np.array([2, 1, 2, 1, 1.0])
    idx = np.random.default_rng(seed).integers(0, 5, size=(3000, 5))
    want = np.percentile(s[idx].sum(axis=1) / nn[idx].sum(axis=1), [5, 95])
    assert t["bootstrap"]["ci"] == pytest.approx(list(want))


def test_confirm_table_drops_a_real_unjudged_final_row_pairwise_and_counts_it() -> None:
    rows = singletons(10, 2, neither=8)
    # a real final row of finalize: label None, source "unjudged" (not a missing row)
    rows.append(crow(100, "recU1", None, 1, cu=True))          # control unjudged, retest not  (Y = 1)
    rows.append(crow(101, "recU2", 1, None, ru=True))          # control not (X = 1), retest unjudged
    rows.append(crow(102, "recU3", 0, None, ru=True))          # control answers, retest unjudged
    t = ctl.confirm_table(rows, n_boot=500)
    assert t["n_mapped"] == 23 and t["n"] == 20 and (t["b"], t["c"]) == (10, 2)
    assert t["arms"]["control"]["unjudged"] == 1 and t["arms"]["retest"]["unjudged"] == 2
    assert t["arms"]["control"]["without_final_row"] == 0
    s = t["unjudged_sensitivity"]
    # (a) unjudged = not: U1 (1, 1); U2 (1, 1); U3 (0, 1)
    assert (s["unjudged_not"]["n"], s["unjudged_not"]["b"], s["unjudged_not"]["c"]) == (23, 10, 3)
    # (b) adverse: control unjudged -> X 0, retest unjudged -> Y 1: U1 (0, 1); U2 (1, 1); U3 (0, 1)
    assert (s["adverse"]["b"], s["adverse"]["c"]) == (10, 4)
    # (c) favourable: control unjudged -> X 1, retest unjudged -> Y 0: U1 (1, 1); U2 (1, 0); U3 (0, 0)
    assert (s["favourable"]["b"], s["favourable"]["c"]) == (11, 2)
    assert s["adverse"]["p_sign_flip"] > t["primary"]["p"] > s["favourable"]["p_sign_flip"]
    # unjudged = not goes through label_of: an unjudged row is label 0
    assert ctl.label_of(rows[-3]["control"]) == 0 and ctl.confirm_xy(rows[-3], "unjudged_not") == (1, 1)
    assert ctl.confirm_xy(rows[-3], "primary") is None
    # a missing final row is dropped under every policy and counted apart
    miss = crow(103, "recM", 1, None)
    assert all(ctl.confirm_xy(miss, p) is None for p in ctl.UNJUDGED_POLICIES)
    assert ctl.confirm_table([*rows, miss], n_boot=200)["arms"]["retest"]["without_final_row"] == 1


def all_pass_checks() -> dict[str, dict[str, Any]]:
    return {g: {"pass": True} for g in ("g3_draw_and_registries", "g4_chunker_and_environment", "g6_input_shas")}


def test_confirm_table_gates_and_outcomes() -> None:
    rows = singletons(10, 2, neither=8)
    ok = ctl.confirm_table(rows, checks=all_pass_checks(), n_floor=20, n_boot=200)
    assert ok["outcome"] == "Confirmed" and ok["gates_failed"] == []
    null = ctl.confirm_table(singletons(3, 3, neither=14), checks=all_pass_checks(), n_floor=20, n_boot=200)
    assert null["outcome"] == "Not confirmed (powered null)"
    # g1: one pair below the floor
    t = ctl.confirm_table(rows, checks=all_pass_checks(), n_floor=21, n_boot=200)
    assert t["outcome"] == "GATE-NOT-EVALUABLE" and t["gates_failed"] == ["g1_realised_n"]
    assert t["gates"]["g1_realised_n"] == {"pass": False, "realised_n": 20, "floor": 21}
    assert t["primary"]["p"] == ok["primary"]["p"]                     # computed, deciding nothing
    assert ctl.confirm_table(rows, n_boot=10)["gates"]["g1_realised_n"]["floor"] == 316
    # g2: 2 unjudged of 22 mapped (9.1 %) > 5 %; 1 of 21 (4.8 %) passes but is judge incomplete
    two = rows + [crow(200, "u1", 0, 0, cu=True), crow(201, "u2", 0, 0, cu=True)]
    t = ctl.confirm_table(two, checks=all_pass_checks(), n_floor=20, n_boot=200)
    assert t["gates_failed"] == ["g2_unjudged"] and t["gates"]["g2_unjudged"]["control_unjudged"] == 2
    one = ctl.confirm_table(rows + [crow(200, "u1", 0, 0, ru=True)], checks=all_pass_checks(), n_floor=20,
                            n_boot=200)
    assert one["gates_failed"] == [] and one["caveats"]["judge_incomplete"] is True
    assert ok["caveats"]["judge_incomplete"] is False
    # g5: a pair without a final row in the retest arm
    t = ctl.confirm_table(rows + [crow(300, "m", 1, None)], checks=all_pass_checks(), n_floor=20, n_boot=200)
    assert t["gates_failed"] == ["g5_arms_complete"] and t["gates"]["g5_arms_complete"]["retest_without_final_row"] == 1
    # g3 / g4 / g6: a failing check, and an absent one
    for g in ("g3_draw_and_registries", "g4_chunker_and_environment", "g6_input_shas"):
        bad = {**all_pass_checks(), g: {"pass": False, "count": 1}}
        assert ctl.confirm_table(rows, checks=bad, n_floor=20, n_boot=200)["gates_failed"] == [g]
        gone = {k: v for k, v in all_pass_checks().items() if k != g}
        t = ctl.confirm_table(rows, checks=gone, n_floor=20, n_boot=200)
        assert t["gates_failed"] == [g] and t["gates"][g] == {"status": "absent", "pass": False}
    assert list(ok["gates"]) == list(ctl.CONFIRM_GATES)
    # the cache flag is carried as a caveat
    flagged = ctl.confirm_table(rows, checks=all_pass_checks(), n_floor=20, n_boot=200, cache={"flag": True})
    assert flagged["caveats"]["cache_suspected"] is True and ok["caveats"]["cache_suspected"] == "absent"


def test_power_at_realised_n_interpolates_and_flags() -> None:
    power = ctl.load_power()
    curve = {int(k): v["power_sign_flip"] for k, v in power["power_by_realised_n"].items()}
    assert ctl.power_at_realised_n(power, 316)["power"] == curve[316]
    p = ctl.power_at_realised_n(power, 317)
    assert p["grid_points"] == [316, 320] and p["power"] == round(curve[316] + (curve[320] - curve[316]) / 4, 4)
    assert ctl.power_at_realised_n(power, 100) == {"n": 100, "power": curve[290], "grid_points": [290],
                                                   "flag": "below_grid"}
    assert ctl.power_at_realised_n(power, 400)["flag"] == "above_grid"
    assert ctl.power_at_realised_n(None, 300) == {"status": "absent"}
    t = ctl.confirm_table(singletons(10, 2), power=power, n_boot=10)
    assert t["design_mde_points"] == 7.5 and t["power_at_realised_n"]["flag"] == "below_grid"


# ---- the Qwen cache check (§6.9)

def qrow(reasoning: Any = None, texts: Any = None, ct: int | None = 100, verdicts: Any = (1,),
         prompt: str = "P", system: str = "S") -> dict[str, Any]:
    r: dict[str, Any] = {"texts_sample": list(texts) if texts is not None else ["{\"answers\": true}"], "ct": ct,
                         "verdicts": list(verdicts), "prompt_sha256": prompt, "system_sha256": system}
    if reasoning is not None:
        r["reasoning_sample"] = reasoning
    return r


def test_cache_check_rests_on_nonempty_reasoning_identity_only() -> None:
    long_a, long_b = "a" * 200, "b" * 250
    short = "c" * 199
    sa = {"C": {"p1": qrow([""]), "p2": qrow([long_a]), "p3": qrow([short]), "p4": qrow([long_a]),
                "p5": qrow([long_b], ct=7), "p6": qrow(None)},
          "E": {"p1": qrow([""]), "p2": qrow([long_b])}}
    rt = {"C": {"p1": qrow([""]), "p2": qrow([long_b]), "p3": qrow([short]), "p4": qrow([""]),
                "p5": qrow([long_a], ct=7), "p6": qrow([long_a])},
          "E": {"p1": qrow([""]), "p2": qrow([long_a])}}
    ids = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
    m = ctl.cache_check(ids, sa, rt)
    # identical but empty (p1), identical but under 200 chars (p3), one side empty (p4): never counted
    assert m["flag"] is False and m["C"]["reasoning_identical"] == 0
    assert m["C"]["reasoning_eligible"] == 2 and m["C"]["reasoning_field_missing"] == 1
    assert m["C"]["texts_sample_identical"] == 6 and m["C"]["ct_equal"] == 6        # descriptives, no flag
    assert m["C"]["compared"] == 6 and m["C"]["without_retest_row"] == 1 and m["C"]["without_stage_a_row"] == 1
    assert m["response_cache_status"] == "absent"
    # one identical non-empty reasoning text on one backend is the threshold
    rt["E"]["p2"] = qrow([long_b])
    m = ctl.cache_check(ids, sa, rt)
    assert m["flag"] is True and m["E"]["reasoning_identical"] == 1 and m["C"]["reasoning_identical"] == 0
    # exactly 200 characters counts; 199 does not
    m = ctl.cache_check(["p"], {"C": {"p": qrow(["x" * 200])}}, {"C": {"p": qrow(["x" * 200])}})
    assert m["flag"] is True
    m = ctl.cache_check(["p"], {"C": {"p": qrow(["x" * 199])}}, {"C": {"p": qrow(["x" * 199])}})
    # no eligible pair on a backend with compared rows: not evaluable, never "clean"
    assert m["flag"] == "not_evaluable" and m["C"]["reasoning_eligible"] == 0 and m["C"]["evaluable"] is False
    # a backend with no compared rows at all (E here) does not make the check unevaluable
    m = ctl.cache_check(["p"], {"C": {"p": qrow(["x" * 300])}}, {"C": {"p": qrow(["y" * 300])}})
    assert m["flag"] is False and m["E"]["compared"] == 0 and m["E"]["evaluable"] is True
    # an identical pair still flags when the other backend is not evaluable
    m = ctl.cache_check(["p"], {"C": {"p": qrow(["x" * 300])}, "E": {"p": qrow([""])}},
                        {"C": {"p": qrow(["x" * 300])}, "E": {"p": qrow([""])}})
    assert m["flag"] is True


def test_cache_check_descriptives_verdicts_and_prompt_shas() -> None:
    sa = {"C": {"p1": qrow(verdicts=(1, 1, 0, None, 1)), "p2": qrow(verdicts=(0, 0, 0, 1, 1), prompt="X")},
          "E": {"p1": qrow(verdicts=(1,), system="S2"), "p2": qrow(verdicts=(None,))}}
    rt = {"C": {"p1": qrow(verdicts=(1, 0, 1, 1, 0)), "p2": qrow(verdicts=(1, 1, 1, 0, 0))},
          "E": {"p1": qrow(verdicts=(0,)), "p2": qrow(verdicts=(1,))}}
    m = ctl.cache_check(["p1", "p2"], sa, rt)
    assert m["C"]["verdict_agreement"] == ctl.wilson(1, 2)          # p1 1 = 1; p2 0 != 1
    assert m["E"]["verdict_agreement"] == ctl.wilson(0, 1)          # p2: a null verdict is left out
    assert m["C"]["prompt_sha256_differs"] == 1 and m["E"]["system_sha256_differs"] == 1


# ---- the file-derived gates

def checks_world() -> dict[str, Any]:
    good = {"sample": "s" * 64, "seed": "e" * 64, "chunks": "k" * 64, "rseed": "r" * 64, "rchunks": "q" * 64,
            "control_export": "k" * 64, "retest_export": "q" * 64}
    block = {"prereg": ctl.CONFIRM_PREREG, "prereg_sha256": PSHA}
    arts = {"draw": {"exploratory": False, "confirmatory": block, "sample_sha256": good["sample"],
                     "stage_a_commit_sha256": ctl.CONFIRM_STAGE_A_COMMIT_SHA256,
                     "control_sample_sha256": ctl.CONFIRM_CONTROL_SAMPLE_SHA256,
                     "counts": {"drawn_control_pairs": 0, "n_draw": 351,
                                "quotas": {"base": 117, "neighbour": 117, "random": 117}}},
            "chunk": {"exploratory": False, "confirmatory": block, "sample_sha256": good["sample"],
                      "chunks_sha256": good["chunks"], "p2_gate": {"i": True, "ii": True},
                      "environment": {"segmenter_version": "punkt-abbrev-v1", "adapter": "markdown/1",
                                      "markdown_backend": "markdown-it-py 3",
                                      "token_counter": {"class": "HFTokenCounter",
                                                        "model": "Salesforce/SFR-Embedding-Mistral"}}},
            "map": {"exploratory": False, "confirmatory": block, "sample_sha256": good["sample"],
                    "seed_sha256": good["seed"], "chunks_sha256": good["chunks"]},
            "retest_seed": {"exploratory": False, "confirmatory": block, "control_seed_sha256": good["seed"],
                            "seed_sha256": good["rseed"], "chunks_sha256": good["rchunks"]}}
    man = {"control": {"inputs_sha256": {"seed_pairs": good["seed"], "chunks_control": good["chunks"]}},
           "retest": {"inputs_sha256": {"seed_pairs": good["rseed"], "chunks_control": good["rchunks"]}}}
    return {"prereg_sha256": PSHA, "stage_a_commit_sha256": ctl.CONFIRM_STAGE_A_COMMIT_SHA256, "artifacts": arts,
            "sample_rows": [{"chunk_id": "w1"}, {"chunk_id": "w2"}],
            "control_registry": [{"control_of": ["a"]}, {"control_of": ["b"]}],
            "retest_registry": [{"control_of": ["a"]}, {"control_of": ["b"]}], "manifests": man, "files": good,
            "committed": {k: {**v, "provenance": {"commit": "other"}} for k, v in arts.items()}}


def test_confirm_checks_pass_on_consistent_inputs_and_trip_on_each_forgery() -> None:
    w = checks_world()
    ok = ctl.confirm_checks(**w)
    assert all(v["pass"] for v in ok.values()) and ok["g6_input_shas"]["checked"] == 27

    def forged(edit: Any) -> dict[str, Any]:
        x = json.loads(json.dumps(w))
        edit(x)
        return ctl.confirm_checks(**x)

    g3 = "g3_draw_and_registries"
    assert not forged(lambda x: x["retest_registry"].append({"control_of": ["c"]}))[g3]["pass"]
    r = forged(lambda x: x["retest_registry"].pop())[g3]
    assert not r["pass"] and r["control_of_only_in_control_registry"] == 1
    assert not forged(lambda x: x.update(control_registry=[], retest_registry=[]))[g3]["pass"]
    assert not forged(lambda x: x["artifacts"]["draw"]["counts"].update(drawn_control_pairs=1))[g3]["pass"]
    r = forged(lambda x: x["sample_rows"].append({"chunk_id": "w1"}))[g3]
    assert not r["pass"] and r["drawn_shared_windows"] == 1
    assert not forged(lambda x: x.update(stage_a_commit_sha256="0" * 64))[g3]["pass"]
    assert not forged(lambda x: x["artifacts"]["draw"].update(stage_a_commit_sha256="0" * 64))[g3]["pass"]
    assert not forged(lambda x: x["artifacts"]["draw"].update(control_sample_sha256="0" * 64))[g3]["pass"]
    g4 = "g4_chunker_and_environment"
    assert not forged(lambda x: x["artifacts"]["chunk"]["p2_gate"].update(ii=False))[g4]["pass"]
    assert not forged(lambda x: x["artifacts"]["chunk"]["environment"].update(
        segmenter_version="regex-abbrev-v1"))[g4]["pass"]
    assert not forged(lambda x: x["artifacts"]["chunk"]["environment"].update(adapter="markdown/0"))[g4]["pass"]
    assert not forged(lambda x: x["artifacts"]["chunk"]["environment"].pop("markdown_backend"))[g4]["pass"]
    g6 = "g6_input_shas"
    for edit, name in [
        (lambda x: x["artifacts"]["map"]["confirmatory"].update(prereg_sha256="cd" * 32),
         "map.json confirmatory.prereg_sha256 = the frozen sha"),
        (lambda x: x["artifacts"]["chunk"].update(exploratory=True), "chunk.json exploratory is false"),
        (lambda x: x["artifacts"]["retest_seed"].pop("confirmatory"),
         "retest-seed.json confirmatory.prereg_sha256 = the frozen sha"),
        (lambda x: x["files"].update(seed="z" * 64), "pairs-seed.jsonl = map.json seed_sha256"),
        (lambda x: x["manifests"]["retest"]["inputs_sha256"].update(seed_pairs="z" * 64),
         "retest registry inputs seed_pairs = pairs-seed-retest.jsonl"),
        (lambda x: x["files"].update(control_export=None), "control config control export = chunks-control.jsonl"),
        (lambda x: x["artifacts"].update(draw=None), "sample.jsonl = draw.json sample_sha256"),
    ]:
        r = forged(edit)[g6]
        assert not r["pass"] and name in r["mismatches"], name


# ---- --confirmatory on chunk / map / retest-seed

def test_confirmatory_switch_sets_the_flag_and_block(words: WordCounter, files: dict[str, Any], judged: Path,
                                                     tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = files["out"]
    pre = tmp_path / "PREREG.md"
    pre.write_text("frozen\n")
    psha = asc.sha256_file(pre)
    monkeypatch.setattr(ctl, "CONFIRM_PREREG_FILE", pre)
    with pytest.raises(SystemExit, match="64 lower-case hex"):
        ctl.run_chunk(files["exports"], files["texts"], files["sample"], out, confirmatory="ABC", prereg=pre)
    with pytest.raises(SystemExit, match="prereg file's sha256 is not --confirmatory"):
        ctl.run_chunk(files["exports"], files["texts"], files["sample"], out, confirmatory=PSHA, prereg=pre)
    with pytest.raises(SystemExit, match="needs --prereg"):
        ctl.run_map(files["exports"], files["sample"], out, confirmatory=psha)
    assert not (out / "chunks-control.jsonl").exists()      # refused before any file is written
    ctl.run_chunk(files["exports"], files["texts"], files["sample"], out, confirmatory=psha, prereg=pre)
    ctl.run_map(files["exports"], files["sample"], out, confirmatory=psha, prereg=pre)
    block = {"prereg": "alias-boundary-confirm-2026-11", "prereg_sha256": psha}
    env = json.loads((out / "chunk.json").read_text())["environment"]
    assert env["token_counter"] == {"class": "WordCounter", "model": None}   # the counter the chunker loaded
    for name, schema in (("chunk.json", "alias-control-chunk/1"), ("map.json", "alias-control-map/1")):
        d = json.loads((out / name).read_text())
        assert d["exploratory"] is False and d["confirmatory"] == block and d["schema"] == schema
    main, cdir, _ = retest_world(judged, tmp_path)
    with pytest.raises(SystemExit, match="prereg file's sha256 is not --confirmatory"):
        ctl.run_retest_seed(main, cdir, tmp_path / "rtx", confirmatory=PSHA, prereg=pre)
    assert not (tmp_path / "rtx" / "chunks-retest.jsonl").exists()
    ctl.run_retest_seed(main, cdir, tmp_path / "rt", confirmatory=psha, prereg=pre)
    d = json.loads((tmp_path / "rt" / "retest-seed.json").read_text())
    assert d["exploratory"] is False and d["confirmatory"] == block
    # the default is unchanged
    ctl.run_retest_seed(main, cdir, tmp_path / "rt0")
    d0 = json.loads((tmp_path / "rt0" / "retest-seed.json").read_text())
    assert d0["exploratory"] is True and "confirmatory" not in d0


def test_main_wires_the_confirmatory_subcommands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(ctl, "run_draw_confirm", lambda *a: seen.setdefault("draw", a) and {"n": 0})
    monkeypatch.setattr(ctl, "run_retest_seed", lambda *a, **k: seen.setdefault("retest", (a, k)) and {})
    p = tmp_path
    assert ctl.main(["draw-confirm", "--judge-config", str(p / "j"), "--control-draw", str(p / "d"),
                     "--control-sample", str(p / "s"), "--n", "351", "--out-dir", str(p / "o"),
                     "--confirmatory", PSHA, "--prereg", str(p / "P.md")]) == 0
    assert seen["draw"] == (p / "j", p / "d", p / "s", 351, p / "o", PSHA, p / "P.md")
    assert ctl.main(["retest-seed", "--judge-config", str(p / "j"), "--control-dir", str(p / "c"), "--out-dir",
                     str(p / "o"), "--confirmatory", PSHA, "--prereg", str(p / "P.md")]) == 0
    assert seen["retest"][1] == {"confirmatory": PSHA, "prereg": p / "P.md"}
    with pytest.raises(SystemExit):                                       # --prereg is required
        ctl.main(["draw-confirm", "--judge-config", "j", "--control-draw", "d", "--control-sample", "s", "--n", "3",
                  "--out-dir", "o", "--confirmatory", PSHA])
    with pytest.raises(SystemExit):
        ctl.main(["draw-confirm", "--judge-config", "j", "--control-draw", "d", "--control-sample", "s", "--n", "3",
                  "--out-dir", "o"])                                      # --confirmatory is required


# ---- times

def test_times_from_label_rows_and_calls_logs(tmp_path: Path) -> None:
    rows = [{"t": "2026-11-06T10:00:05Z", "duration_s": 5.0}, {"t": "2026-11-06T10:30:00Z", "duration_s": 2.5}]
    q = ctl.qwen_times(rows)
    assert q["first_utc"] == "2026-11-06T10:00:00Z" and q["last_utc"] == "2026-11-06T10:30:00Z" and q["rows"] == 2
    assert ctl.qwen_times([])["first_utc"] == "absent"
    f = fx.write_jsonl(tmp_path / "calls.jsonl", [{"t": 1793959200.0, "step": "judge", "status": "ok"},
                                                  {"t": 1793959260.5, "step": "judge", "status": "error:429"}])
    g = ctl.gateway_times(f)
    assert g["by_step_status"] == {"judge|ok": 1, "judge|error:429": 1} and g["rows"] == 2
    assert g["first_utc"] == "2026-11-06T10:00:00Z" and g["last_utc"] == "2026-11-06T10:01:00Z"
    assert ctl.gateway_times(tmp_path / "none.jsonl")["first_utc"] == "absent"
    gap = ctl.arm_gap({"first_epoch": 100.0, "last_epoch": 200.0}, {"first_epoch": 250.0, "last_epoch": 300.0})
    assert gap == {"first_arm": "control", "gap_s": 50.0}
    assert ctl.halves(["a", "b", "c"]) == {"a": "first", "b": "first", "c": "second"}


# ---- confirm-report end to end on the dry run (forged labels; the judge harness is not exercised)

def _final_row(pid: str, label: int | None, source: str | None = None, **kw: Any) -> dict[str, Any]:
    src = source or ("unjudged" if label is None else "agree")
    return {"k": f"pair|{pid}", "pair_id": pid, "label": label, "source": src, "c_label": label, "e_label": label,
            "tiebreak_label": None, "drift_sampled": False, "opus": None, "opus_status": None, **kw}


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(ctl.jsonl_bytes(rows))


def confirm_world(judged: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n: int = 9) -> dict[str, Any]:
    cfgp, cd_p, cs_p, sa = confirm_inputs(judged, tmp_path, monkeypatch, n_draw=n)
    cdir, rdir = tmp_path / "c", tmp_path / "rt"
    prereg, psha = sa["prereg"], sa["psha"]
    ctl.run_draw_confirm(cfgp, cd_p, cs_p, n, cdir, psha, prereg)
    sample = asc.read_jsonl(cdir / "sample.jsonl")
    raw = json.loads(cfgp.read_text())
    src = asc.read_chunk_exports(list(raw["chunk_exports"].values()))
    # one control chunk per document (they then tile its text), each window mapped to it
    cchunks: dict[str, dict[str, Any]] = {}
    seeds = []
    for i, s in enumerate(sample):
        w = src[s["chunk_id"]]
        d = w["doc_id"]
        if d not in cchunks:
            text = f"Control chunk {len(cchunks)} of a document: the boundary-respecting text sits here."
            cchunks[d] = {"chunk_id": f"ctlchunk-{len(cchunks):04d}", "doc_id": d, "content": text, "start_char": 0,
                          "end_char": len(text), "metadata": {"source_path": w["paper"], "title": w["title"],
                                                              "section_crossing": i == 0, "split_sentence": False,
                                                              "tokens": 12}}
        seeds.append({"control_of": s["pair_id"], "record_id": s["record_id"], "stage_a_chunk_id": s["chunk_id"],
                      "chunk_id": cchunks[d]["chunk_id"], "doc_id": d, "stratum": "answers", "arm": s["arm"],
                      "in_spot_sheet": False, "coverage": [0.3, 0.6, 0.95][i % 3],
                      "control_tokens": [100, 400, 600][i % 3], "latex_preamble": False, "overlap_chars": 10,
                      "purity": 1.0, "n_overlapping": 1, "window_chars": 50})
    _write_rows(cdir / "chunks-control.jsonl", list(cchunks.values()))
    _write_rows(cdir / "docs.jsonl", [{"doc_id": d, "status": "ok", "n_sections": 1, "untitled_only": True,
                                       "latex_preamble": False} for d in cchunks])
    _write_rows(cdir / "pairs-seed.jsonl", seeds)
    block = {"prereg": ctl.CONFIRM_PREREG, "prereg_sha256": psha}
    sha = asc.sha256_file
    (cdir / "chunk.json").write_text(json.dumps({
        "schema": ctl.SCHEMA["chunk"], "exploratory": False, "confirmatory": block,
        "environment": {**ctl.require_env(), "token_counter": {"class": "HFTokenCounter", "model": ctl.TOKENIZER}},
        "p2_gate": {"i": True, "ii": True}, "chunks_sha256": sha(cdir / "chunks-control.jsonl"),
        "sample_sha256": sha(cdir / "sample.jsonl")}))
    (cdir / "map.json").write_text(json.dumps({
        "schema": ctl.SCHEMA["map"], "exploratory": False, "confirmatory": block,
        "seed_sha256": sha(cdir / "pairs-seed.jsonl"), "sample_sha256": sha(cdir / "sample.jsonl"),
        "chunks_sha256": sha(cdir / "chunks-control.jsonl")}))
    ctl.run_retest_seed(cfgp, cdir, rdir, confirmatory=psha, prereg=prereg)
    cfgs = {}
    for side, out, export, seed in (("control", "cj", cdir / "chunks-control.jsonl", cdir / "pairs-seed.jsonl"),
                                    ("retest", "rj", rdir / "chunks-retest.jsonl", rdir / "pairs-seed-retest.jsonl")):
        c = {**raw, "out_dir": str(tmp_path / out), "chunk_exports": {**raw["chunk_exports"], aj.CONTROL_EXPORT:
                                                                       str(export)}}
        cp = tmp_path / f"{side}-config.json"
        cp.write_text(json.dumps(c))
        assert aj.main(["--config", str(cp), "build-control", "--seed-pairs", str(seed), "--main-config", str(cfgp)],
                       prov={"commit": "c" * 40}) == aj.EXIT_OK
        cfgs[side] = cp
    monkeypatch.setattr(ctl, "CONFIRM_N_FLOOR", n)
    monkeypatch.setattr(ctl, "CONFIRM_QUOTAS", json.loads((cdir / "draw.json").read_text())["counts"]["quotas"])
    # the committed copies of the four public files (§8 gate 6 compares them with the run copies)
    arts = tmp_path / "artifacts"
    arts.mkdir()
    for src_ in (cdir / "draw.json", cdir / "chunk.json", cdir / "map.json", rdir / "retest-seed.json"):
        shutil.copy(src_, arts / src_.name)
    return {"main": cfgp, "cdir": cdir, "rdir": rdir, "cfgs": cfgs, "seeds": seeds, "sample": sample,
            "prereg": prereg, "psha": psha, "sa": sa, "cout": tmp_path / "cj",
            "rout": tmp_path / "rj", "artifacts": arts}


def label_world(w: dict[str, Any], xy: dict[str, tuple[int | None, int | None]]) -> None:
    """Final rows and Qwen label rows for both arms; (X, Y) per stage-A pair id, None = unjudged."""
    creg = asc.read_jsonl(w["cout"] / "pairs.jsonl")
    rreg = asc.read_jsonl(w["rout"] / "pairs.jsonl")
    sa_c, _, _ = ctl.label_rows(Path(json.loads(w["main"].read_text())["out_dir"]), "C")
    cf, rf, cl, rl = [], [], [], []
    for i, p in enumerate(creg):
        (s,) = p["control_of"]
        x = xy[s][0]
        cf.append(_final_row(p["pair_id"], None if x is None else 1 - x))
        cl.append({"k": f"qwen|{p['pair_id']}", "pair_id": p["pair_id"], "t": f"2026-11-06T10:{i:02d}:00Z",
                   "duration_s": 2.0, "verdicts": [1, 1, 1, 1, 1], "reasoning_sample": [f"control {i} " * 30],
                   "texts_sample": ["x"], "ct": 10, "prompt_sha256": "cp", "system_sha256": "s"})
    for i, p in enumerate(rreg):
        y = xy[p["pair_id"]][1]
        rf.append(_final_row(p["pair_id"], None if y is None else 1 - y))
        stage = sa_c.get(p["pair_id"], {})
        rl.append({"k": f"qwen|{p['pair_id']}", "pair_id": p["pair_id"], "t": f"2026-11-06T11:{i:02d}:00Z",
                   "duration_s": 2.0, "verdicts": [1, 1, 1, 1, 1], "reasoning_sample": [f"retest {i} " * 30],
                   "texts_sample": stage.get("texts_sample"), "ct": 999, "prompt_sha256": stage.get("prompt_sha256"),
                   "system_sha256": stage.get("system_sha256")})
    for out, f, lab in ((w["cout"], cf, cl), (w["rout"], rf, rl)):
        _write_rows(out / "final.jsonl", f)
        for be in ("C", "E"):
            _write_rows(out / aj.LABEL_FILES[be], lab)
    _write_rows(w["cout"] / "calls-gw-argon.jsonl", [{"t": 1793959200.0, "step": "judge", "status": "ok"}])


def run_cr(w: dict[str, Any], out: Path, **kw: Any) -> dict[str, Any]:
    return ctl.run_confirm_report(w["main"], w["cfgs"]["control"], w["cfgs"]["retest"], w["cdir"], w["rdir"],
                                  w["prereg"], kw.pop("psha", w["psha"]), out,
                                  artifacts_dir=kw.pop("artifacts_dir", w["artifacts"]), **kw)


def exact_sign_flip(sums: list[int]) -> float:
    import itertools

    t = sum(sums)
    hits = [sum(s * g for s, g in zip(sums, signs, strict=True)) >= t
            for signs in itertools.product((1, -1), repeat=len(sums))]
    return sum(hits) / len(hits)


def test_confirm_report_end_to_end(words: WordCounter, judged: Path, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    w = confirm_world(judged, tmp_path, monkeypatch)
    ids = [s["control_of"] for s in w["seeds"]]
    cells = [(1, 0)] * 6 + [(0, 0)] * 2 + [(1, 1)]
    xy = dict(zip(ids, cells, strict=True))
    label_world(w, xy)
    pub = tmp_path / "pub" / "confirm-report.json"
    flipped = tmp_path / "priv" / "flipped.txt"
    rep = run_cr(w, pub, write_flipped=flipped)
    t = rep["primary"]
    assert rep["schema"] == "alias-confirm-report/1" and rep["exploratory"] is False
    assert rep["confirmatory"] == {"prereg": ctl.CONFIRM_PREREG, "prereg_sha256": w["psha"]}
    assert (t["n"], t["b"], t["c"], t["n_mapped"]) == (9, 6, 0, 9)
    rec_of = {s["control_of"]: s["record_id"] for s in w["seeds"]}
    sums: dict[str, int] = defaultdict(int)
    for pid, (x, y) in xy.items():
        sums[rec_of[pid]] += x - y
    assert t["primary"]["p"] == pytest.approx(exact_sign_flip(list(sums.values())))
    assert t["gates"]["g6_input_shas"]["mismatches"] == []
    assert t["gates_failed"] == [] and t["outcome"] == "Confirmed" == rep["outcome"]
    assert t["clusters"]["passage_papers"] >= 1 and t["clusters"]["documents"] >= 1
    # stage A's dry-run rows carry empty reasoning: the check cannot evaluate, and says so
    assert rep["cache_check"]["flag"] == "not_evaluable" and rep["cache_check"]["C"]["compared"] == 9
    assert t["caveats"]["cache_suspected"] == "not_evaluable"
    assert rep["cache_check"]["C"]["prompt_sha256_differs"] == 0       # joined by the stage-A pair id
    assert rep["times"]["C"]["first_arm"] == "control" and rep["times"]["argon"]["control"]["rows"] == 1
    assert rep["times"]["argon"]["retest"]["first_utc"] == "absent"
    assert rep["descriptives"]["escalation"]["overlap"]["n"] == 9
    tb = rep["descriptives"]["time_blocks"]["by_control_labelling_order"]
    assert sum(v["n"] for v in tb.values()) == 9 and sum(v["b"] for v in tb.values()) == 6
    assert "host" not in rep["provenance"]
    assert sorted(flipped.read_text().split()) == sorted(p for p, (x, _) in xy.items() if x == 1)
    text = pub.read_text()
    for s in w["seeds"]:
        assert s["control_of"] not in text and s["record_id"] not in text and s["chunk_id"] not in text
    # run once
    with pytest.raises(SystemExit, match="runs once"):
        run_cr(w, pub)
    # the prereg's sha256 must be the frozen one
    with pytest.raises(SystemExit, match="not --prereg-sha256"):
        run_cr(w, tmp_path / "pub" / "x.json", psha="cd" * 32)
    # a public artifact stamped with another prereg sha trips gate 6
    mp = w["cdir"] / "map.json"
    good = mp.read_text()
    mp.write_text(good.replace(w["psha"], "cd" * 32))
    bad = run_cr(w, tmp_path / "pub" / "g6.json")["primary"]
    assert bad["outcome"] == "GATE-NOT-EVALUABLE" and bad["gates_failed"] == ["g6_input_shas"]
    assert "map.json confirmatory.prereg_sha256 = the frozen sha" in bad["gates"]["g6_input_shas"]["mismatches"]
    mp.write_text(good)
    # committed copies absent: gate 6 reads absent and fails
    (tmp_path / "empty").mkdir()
    gone = run_cr(w, tmp_path / "pub" / "noart.json", artifacts_dir=tmp_path / "empty")["primary"]
    assert gone["gates_failed"] == ["g6_input_shas"] and gone["gates"]["g6_input_shas"]["mismatched"] == 4


def test_confirm_report_unjudged_row_and_the_cover_rerun(words: WordCounter, judged: Path, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    w = confirm_world(judged, tmp_path, monkeypatch)
    ids = [s["control_of"] for s in w["seeds"]]
    cells: list[tuple[int | None, int | None]] = [(1, 0)] * 5 + [(0, 0)] * 3 + [(1, None)]
    xy = dict(zip(ids, cells, strict=True))
    label_world(w, xy)
    pub = tmp_path / "pub" / "r.json"
    flipped = tmp_path / "priv" / "flipped.txt"
    t = run_cr(w, pub, write_flipped=flipped)["primary"]
    # the real unjudged retest row: dropped pairwise, counted, n under the (patched) floor of 9
    assert (t["n"], t["n_mapped"], t["arms"]["retest"]["unjudged"]) == (8, 9, 1)
    assert t["gates_failed"] == ["g1_realised_n", "g2_unjudged"] and t["outcome"] == "GATE-NOT-EVALUABLE"
    assert t["unjudged_sensitivity"]["adverse"]["n"] == 9 and t["unjudged_sensitivity"]["adverse"]["b"] == 5
    assert t["unjudged_sensitivity"]["favourable"]["b"] == 6
    flipped_ids = flipped.read_text().split()
    assert len(flipped_ids) == 6
    # cover-any: the flipped pairs' primary chunks re-judged in a third registry, all answering
    seeds = [s for s in w["seeds"] if s["control_of"] in set(flipped_ids)]
    cover_seed = fx.write_jsonl(tmp_path / "priv" / "pairs-seed-cover.jsonl", seeds)
    raw = json.loads(w["cfgs"]["control"].read_text())
    vp = tmp_path / "cover-config.json"
    vp.write_text(json.dumps({**raw, "out_dir": str(tmp_path / "vj")}))
    assert aj.main(["--config", str(vp), "build-control", "--seed-pairs", str(cover_seed), "--main-config",
                    str(w["main"])], prov={"commit": "c" * 40}) == aj.EXIT_OK
    _write_rows(tmp_path / "vj" / "final.jsonl", [_final_row(p["pair_id"], 1)
                                                  for p in asc.read_jsonl(tmp_path / "vj" / "pairs.jsonl")])
    with pytest.raises(SystemExit, match="does not exist yet"):
        run_cr(w, tmp_path / "pub" / "none.json", cover_config=vp)
    rep = run_cr(w, pub, cover_config=vp)
    split = rep["cover_any"]["split"]["answers_to_not"]
    assert rep["cover_any"]["control_not_pairs"] == 6 and split["n"] == 6 and split["primary_only"] == 6
    assert rep["primary"] == t                                      # the primary numbers are not changed
    old = json.loads(pub.read_text())
    old["primary"]["b"] = 99
    pub.write_text(json.dumps(old))
    with pytest.raises(SystemExit, match="never changes a primary number"):
        run_cr(w, pub, cover_config=vp)


def test_confirm_report_writer_refuses_ids_text_and_host() -> None:
    t = ctl.confirm_table(singletons(3, 1), n_boot=10)
    rec = {"schema": ctl.SCHEMA["confirm-report"], "primary": t}
    assert not asc.public_problems(rec, {"pair0000", "rec0000"}, ["a private passage that is long enough to matter."])
    for bad, forbidden, texts in (({**rec, "worst": "pair0000"}, {"pair0000"}, ()),
                                  ({**rec, "note": "a private passage that is long enough to matter."}, (),
                                   ["a private passage that is long enough to matter."]),
                                  ({**rec, "provenance": {"host": "x"}}, (), ())):
        with pytest.raises(SystemExit, match="REFUSED"):
            ctl.write_public(Path("/nonexistent/never.json"), bad, forbidden, texts)


# ---- the review fixes (PR #969)

def test_confirm_pins_match_power_json_and_the_prereg_table() -> None:
    power = ctl.load_power()
    assert ctl.CONFIRM_N_DRAW == power["n_draw_owner"] == 351
    assert ctl.CONFIRM_QUOTAS == power["quotas_at_owner_draw"]
    pc = ctl.CONFIRM_POOL_COUNTS
    for k in ("agreed_answers", "eligible", "shared_window_excluded", "pool", "cells"):
        assert sum(pc[k][a] for a in asc.ARM_GROUPS) == pc[k]["total"], k
    for a in asc.ARM_GROUPS:   # 50 per arm in the control's answers stratum; exclusion 2 the rest
        assert pc["agreed_answers"][a] - pc["eligible"][a] == 50
        assert pc["eligible"][a] - pc["pool"][a] == pc["shared_window_excluded"][a]
    assert pc["in_control_sample"]["total"] == 150 and pc["shared_window_excluded"]["total"] == 43
    assert sum(pc["pool_records_by_arms"].values()) == pc["pool_records"] == 262
    assert sum(int(k) * v for k, v in pc["pool_records_by_arms"].items()) == pc["cells"]["total"] == 470
    assert aj.drift_allocation(351, {a: pc["cells"][a] for a in asc.ARM_GROUPS}) == ctl.CONFIRM_QUOTAS


def test_load_power_refuses_a_changed_file(tmp_path: Path) -> None:
    p = tmp_path / "power.json"
    p.write_bytes(ctl.CONFIRM_POWER_JSON.read_bytes().replace(b'"n_floor": 316', b'"n_floor": 300'))
    with pytest.raises(SystemExit, match="not the frozen file"):
        ctl.load_power(p)


def test_draw_reports_walk_capacity() -> None:
    rows = []
    for a in ("base", "neighbour"):
        rows += [cfin(f"{a}{r}-{k}", f"r{r}", f"{a}w{r}-{k}", a) for r in range(6) for k in range(2)]
    rows += [cfin(f"rnd{r}-{k}", f"r{r}", f"rndw{k}", "random") for r in range(4) for k in range(2)]
    _, c = ctl.draw_confirm(rows, exclude_pairs=(), exclude_windows=(), n=3)
    # capacity is the walk run to exhaustion, whatever n asks for: random's 4 cells reach 2
    assert c["walk_capacity"] == {"base": 6, "neighbour": 6, "random": 2} and c["n_drawn"] == 3


def test_run_draw_confirm_quotas_are_by_cells_not_by_pool_pairs(judged: Path, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    cfgp, cd_p, cs_p, sa = confirm_inputs(judged, tmp_path, monkeypatch)
    pc = ctl.CONFIRM_POOL_COUNTS
    cells = {a: pc["cells"][a] for a in asc.ARM_GROUPS}
    pairs = {a: pc["pool"][a] for a in asc.ARM_GROUPS}
    n = sum(cells.values())
    by_cells, by_pairs = aj.drift_allocation(n, cells), aj.drift_allocation(n, pairs)
    assert by_cells != by_pairs                                       # the fixture tells the two apart
    monkeypatch.setattr(ctl, "CONFIRM_N_DRAW", n)
    ctl.run_draw_confirm(cfgp, cd_p, cs_p, n, tmp_path / "q", sa["psha"], sa["prereg"])
    c = json.loads((tmp_path / "q" / "draw.json").read_text())["counts"]
    assert c["quotas"] == by_cells == cells
    assert c["achieved_first_pass"] == {a: min(by_cells[a], c["walk_capacity"][a]) for a in asc.ARM_GROUPS}


def test_confirm_checks_n_draw_quotas_token_counter_and_committed_copies() -> None:
    w = checks_world()

    def forged(edit: Any) -> dict[str, Any]:
        x = json.loads(json.dumps(w))
        edit(x)
        return ctl.confirm_checks(**x)

    g3, g4, g6 = "g3_draw_and_registries", "g4_chunker_and_environment", "g6_input_shas"
    r = forged(lambda x: x["artifacts"]["draw"]["counts"].update(n_draw=350))[g3]
    assert not r["pass"] and r["n_draw_is_prereg"] is False
    r = forged(lambda x: x["artifacts"]["draw"]["counts"].update(quotas={"base": 118, "neighbour": 117,
                                                                         "random": 116}))[g3]
    assert not r["pass"] and r["quotas_are_prereg"] is False
    for tc in ({"class": "WordCounter", "model": None}, {"class": "HFTokenCounter", "model": "other/model"}, None):
        r = forged(lambda x, tc=tc: x["artifacts"]["chunk"]["environment"].update(token_counter=tc))[g4]
        assert not r["pass"] and r["token_counter_is_sfr"] is False
    # a committed copy that differs from its run copy, or is absent
    r = forged(lambda x: x["committed"]["map"].update(seed_rows=1))[g6]
    assert not r["pass"] and r["mismatches"] == ["map.json = its committed copy in artifacts/ (provenance aside)"]
    r = forged(lambda x: x["committed"].update(retest_seed=None))[g6]
    assert r["mismatches"] == ["retest-seed.json = its committed copy in artifacts/ (provenance aside)"]
    x = json.loads(json.dumps(w))
    x.pop("committed")
    assert ctl.confirm_checks(**x)[g6]["mismatched"] == 4
    # only provenance may differ
    assert forged(lambda x: x["committed"]["draw"].update(provenance={"commit": "x" * 40}))[g6]["pass"]


def test_gate_and_caveat_boundaries() -> None:
    base = singletons(10, 2, neither=7)                      # 19 judged pairs
    # g2 at exactly 5 %: 1 unjudged of 20 mapped passes
    t = ctl.confirm_table(base + [crow(400, "u", 0, 0, cu=True)], checks=all_pass_checks(), n_floor=19, n_boot=10)
    assert t["arms"]["control"]["unjudged_share"] == 0.05 and t["gates"]["g2_unjudged"]["pass"] is True
    # judge incomplete at exactly 1 %: 1 unjudged of 100 mapped is not incomplete; 2 of 100 is
    big = singletons(10, 2, neither=87)
    one = ctl.confirm_table(big + [crow(500, "u", 0, 0, ru=True)], n_boot=10)
    assert one["arms"]["retest"]["unjudged_share"] == 0.01 and one["caveats"]["judge_incomplete"] is False
    two = ctl.confirm_table(big[:-1] + [crow(500, "u", 0, 0, ru=True), crow(501, "v", 0, 0, ru=True)], n_boot=10)
    assert two["caveats"]["judge_incomplete"] is True
    # the outcome at p == alpha is Confirmed (p <= alpha)
    t = ctl.confirm_table(singletons(10, 2), checks=all_pass_checks(), n_floor=12, n_boot=10, alpha=79 / 4096)
    assert t["primary"]["p"] == 79 / 4096 and t["outcome"] == "Confirmed"
    t = ctl.confirm_table(singletons(10, 2), checks=all_pass_checks(), n_floor=12, n_boot=10, alpha=78 / 4096)
    assert t["outcome"] == "Not confirmed (powered null)"


def test_halves_with_even_and_odd_n() -> None:
    assert ctl.halves(["a", "b", "c", "d"]) == {"a": "first", "b": "first", "c": "second", "d": "second"}
    assert ctl.halves(["a"]) == {"a": "first"} and ctl.halves([]) == {}


def bootstrap_rows(n_records: int, seed: int = 5) -> list[dict[str, Any]]:
    """Records of 1-3 pairs with mixed outcomes."""
    import random

    rnd = random.Random(seed)
    rows, i = [], 0
    for j in range(n_records):
        for k in range(rnd.randint(1, 3)):
            x, y = rnd.choice([(1, 0), (0, 1), (0, 0), (1, 1), (0, 0)])
            rows.append(crow(i, f"rec{j:04d}", x, y, asc.ARM_GROUPS[k]))
            i += 1
    return rows


def test_bootstrap_and_test_are_invariant_to_the_input_order() -> None:
    import random

    rows = bootstrap_rows(40)
    a = ctl.confirm_table(rows, n_boot=2000)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    b = ctl.confirm_table(shuffled, n_boot=2000)
    assert shuffled != rows and a["bootstrap"]["ci"] == b["bootstrap"]["ci"] and a["primary"] == b["primary"]


def test_bootstrap_at_210_records_equals_a_single_block_numpy_reproduction() -> None:
    rows = bootstrap_rows(210, seed=11)
    t = ctl.confirm_table(rows, n_boot=10_000)
    recs = sorted({r["record_id"] for r in rows})
    s = np.array([sum(ctl.confirm_xy(r)[0] - ctl.confirm_xy(r)[1] for r in rows if r["record_id"] == k)
                  for k in recs], dtype=float)
    nn = np.array([sum(r["record_id"] == k for r in rows) for k in recs], dtype=float)
    idx = np.random.default_rng(asc.seed_int("261105|alias|confirm|boot")).integers(0, 210, size=(10_000, 210))
    want = np.percentile(s[idx].sum(axis=1) / nn[idx].sum(axis=1), [5, 95])
    assert 2_000_000 // 210 < 10_000                    # page_bootstrap_ci draws this in two blocks
    assert t["bootstrap"]["ci"] == pytest.approx(list(want), abs=0)


def test_confirm_report_sets_the_cache_flag_end_to_end(words: WordCounter, judged: Path, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    w = confirm_world(judged, tmp_path, monkeypatch)
    ids = [s["control_of"] for s in w["seeds"]]
    label_world(w, dict(zip(ids, [(1, 0)] * 6 + [(0, 0)] * 3, strict=True)))
    # the committed stage A cannot be edited or appended to (the commit pins its label files), so
    # its E rows are given long reasoning as they are read: every retest pair eligible, and on one
    # of them the retest row's own text
    main_out = Path(json.loads(w["main"].read_text())["out_dir"]).resolve()
    rrows = {r["pair_id"]: r for r in asc.read_jsonl(w["rout"] / aj.LABEL_FILES["E"])}
    real = ctl.label_rows
    first = sorted(rrows)[0]

    def patched(out_dir: Path, backend: str) -> tuple[dict[str, dict[str, Any]], int, list[str]]:
        rows, dup, order = real(out_dir, backend)
        if Path(out_dir).resolve() == main_out and backend == "E":
            rows = {p: {**r, "reasoning_sample": rrows[p]["reasoning_sample"] if p == first
                        else [f"stage A reasoning {p} " * 20]} if p in rrows else r for p, r in rows.items()}
        return rows, dup, order

    monkeypatch.setattr(ctl, "label_rows", patched)
    rep = run_cr(w, tmp_path / "pub" / "cache.json")
    cc = rep["cache_check"]
    assert cc["flag"] is True and cc["E"]["reasoning_identical"] == 1 and cc["E"]["reasoning_eligible"] == 9
    assert cc["C"]["reasoning_identical"] == 0 and cc["C"]["evaluable"] is False
    assert rep["primary"]["caveats"]["cache_suspected"] is True


def test_label_rows_reads_as_the_harness_does(tmp_path: Path) -> None:
    f = tmp_path / aj.LABEL_FILES["C"]
    f.write_text("".join(json.dumps(r) + "\n" for r in ({"pair_id": "p1", "v": 1}, {"pair_id": "p2", "v": 2},
                                                        {"pair_id": "p1", "v": 3})))
    rows, dup, order = ctl.label_rows(tmp_path, "C")
    assert rows["p1"]["v"] == 3 and dup == 1 and order == ["p1", "p2"]       # the last row wins, counted
    assert ctl.label_rows(tmp_path, "E") == ({}, 0, [])


def test_the_frozen_sha_must_be_the_committed_prereg(words: WordCounter, judged: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    assert ctl.CONFIRM_PREREG_FILE == ctl.REPO / "docs/plans/results/alias-boundary-confirm-2026-11/PREREG.md"
    cfgp, cd_p, cs_p, sa = confirm_inputs(judged, tmp_path, monkeypatch)
    other = tmp_path / "other" / "PREREG.md"
    other.parent.mkdir()
    other.write_text("another text, with its own sha\n")
    osha = asc.sha256_file(other)
    # the file and the sha agree, but it is not the committed prereg: refused at step 1
    with pytest.raises(SystemExit, match="not the committed PREREG.md's"):
        ctl.check_prereg(osha, other)
    with pytest.raises(SystemExit, match="not the committed PREREG.md's"):
        ctl.run_draw_confirm(cfgp, cd_p, cs_p, 9, tmp_path / "o1", osha, other)
    assert not (tmp_path / "o1" / "sample.jsonl").exists()
    assert ctl.check_prereg(sa["psha"], sa["prereg"])["prereg_sha256"] == sa["psha"]
    # and at step 6
    with pytest.raises(SystemExit, match="not the committed PREREG.md's"):
        ctl.run_confirm_report(cfgp, cfgp, cfgp, tmp_path, tmp_path, other, osha, tmp_path / "r.json",
                               artifacts_dir=tmp_path)
    # with the real constant, a tmp file is refused even when its own sha is given
    monkeypatch.setattr(ctl, "CONFIRM_PREREG_FILE", ctl.CONFIRM_DIR / "PREREG.md")
    with pytest.raises(SystemExit, match="not the committed PREREG.md's"):
        ctl.check_prereg(sa["psha"], sa["prereg"])
    real = ctl.CONFIRM_DIR / "PREREG.md"
    assert ctl.check_prereg(asc.sha256_file(real), real)["prereg_sha256"] == asc.sha256_file(real)
