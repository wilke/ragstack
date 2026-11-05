"""Alias study chunk-boundary control (exploratory): the environment refusals, the control chunker
as the markers resolve it, P2's invariants on titled / nested / untitled sections and an
over-budget sentence, the mapping and its tie rule, the draw (x1 reproduces the spot draw byte for
byte; the spot sheet is the head of every x3 cell), build-control, and the public report's
refusals. Fixtures only: a word-count token counter stands in for the SFR tokenizer; no model, no
store, no network."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

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
    assert tb["not_to_answers"]["recovered_by_other_chunk"] == ctl.wilson(0, 1)


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
    assert m == {"stage_a": 4, "retest": 3, "both": 2, "stage_a_only": 2, "retest_only": 1,
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
