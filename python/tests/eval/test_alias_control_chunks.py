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
