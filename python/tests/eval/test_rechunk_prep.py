"""Rechunk stage 1 (exploratory), the preparation: ``add-structure`` (the key added, nothing else
changed, refusals), ``export`` (``embed_shard``'s pipeline reproduces the control chunker's ids and
spans, no request sent, the refusals), ``gold-map`` (the label table of a synthetic confirmatory
sample: corpus arms, cover-any classes, combined labels, a public-clean summary) and the rechunk
run's presence check. Fixtures only: a word-count token counter stands in for the SFR tokenizer; no
model, no store, no network."""
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
import found10 as f10  # noqa: E402
import rechunk_fixture as rfx  # noqa: E402
import rechunk_prep as rp  # noqa: E402

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


#: an untitled lead, nested titled sections (one long), and one sentence over the 640 ceiling
DOC_A = (sents(3, 20, "lead") + "\n\n## Methods\n\n" + sents(10, 20, "m") + "\n\n### Sub A\n\n"
         + sents(60, 20, "a") + "\n\n### Sub B\n\n" + sents(2, 20, "b") + "\n\n## Results\n\n"
         + sent(700, "r") + "\n")
#: no heading: one untitled section
DOC_B = sents(45, 20, "u") + "\n"
#: a level-1 title over a titled section
DOC_C = "# Title one\n\n" + sents(8, 20, "c") + "\n\n## Two\n\n" + sents(30, 20, "d") + "\n"


def records(tmp: Path, name: str = "records.jsonl") -> Path:
    rows = [{"path": "PMC0001#body", "text": DOC_A, "metadata": {"title": "Paper one", "pmcid": "PMC0001"}},
            {"path": "PMC0002#body", "text": DOC_B, "metadata": {"title": "Paper two"}},
            {"path": "PMC0003#body", "text": DOC_C, "metadata": {"title": "Paper three"}}]
    return rfx.write_jsonl(tmp / name, rows)


# ------------------------------------------------------------------ add-structure

def test_add_structure_adds_the_key_and_changes_nothing_else(tmp_path: Path) -> None:
    src = records(tmp_path)
    dst = tmp_path / "out" / "structured.jsonl"
    rec = rp.run_add_structure(src, dst)
    a, b = rp.read_rows(src), rp.read_rows(dst)
    assert len(b) == 3 and all(set(y) == set(x) | {"structure"} for x, y in zip(a, b, strict=True))
    assert all({k: v for k, v in y.items() if k != "structure"} == x for x, y in zip(a, b, strict=True))
    assert rec["records"] == 3 and rec["sections"] > 0 and rec["exploratory"] is True
    assert rec["in_sha256"] == asc.sha256_file(src) and rec["out_sha256"] == asc.sha256_file(dst)
    assert rec["adapter"].startswith("markdown/") and "provenance" in rec
    on_disk = json.loads(dst.with_name(dst.name + ".summary.json").read_text())
    assert on_disk["out_sha256"] == rec["out_sha256"] and not dst.with_name(dst.name + ".unverified").exists()
    # the loader reads every structure back (none dropped as invalid)
    from ragstack.ingestion.loaders import JsonlLoader

    loader = JsonlLoader()
    docs = loader.load(str(dst))
    assert all(d.structure is not None for d in docs) and not loader.dropped.structure_invalid
    assert [d.content for d in docs] == [DOC_A, DOC_B, DOC_C]


def test_add_structure_refusals(tmp_path: Path) -> None:
    src = records(tmp_path)
    with pytest.raises(SystemExit, match="never rewritten"):
        rp.run_add_structure(src, src)
    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(src, dst)
    with pytest.raises(SystemExit, match="already carries a structure"):
        rp.run_add_structure(dst, tmp_path / "s2.jsonl")
    no_text = rfx.write_jsonl(tmp_path / "nt.jsonl", [{"path": "x", "metadata": {}}])
    with pytest.raises(SystemExit, match="no text"):
        rp.run_add_structure(no_text, tmp_path / "s3.jsonl")
    with pytest.raises(SystemExit, match="inside the repository"):
        rp.run_add_structure(src, Path(__file__).resolve().parent / "structured.jsonl")
    assert not (Path(__file__).resolve().parent / "structured.jsonl").exists()


def test_verify_structured_catches_a_changed_text_or_key(tmp_path: Path) -> None:
    src = records(tmp_path)
    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(src, dst)
    rows = rp.read_rows(dst)
    tampered = [dict(r) for r in rows]
    tampered[1]["text"] = tampered[1]["text"] + " "
    with pytest.raises(SystemExit, match="text sha changed"):
        rp.verify_structured(src, rfx.write_jsonl(tmp_path / "t1.jsonl", tampered))
    moved = [dict(r) for r in rows]
    moved[0]["path"] = "PMC9999#body"
    with pytest.raises(SystemExit, match="other than structure"):
        rp.verify_structured(src, rfx.write_jsonl(tmp_path / "t2.jsonl", moved))
    with pytest.raises(SystemExit, match="input records"):
        rp.verify_structured(src, rfx.write_jsonl(tmp_path / "t3.jsonl", rows[:2]))


# ------------------------------------------------------------------ export

def test_the_entry_is_the_plan_s() -> None:
    assert (rp.METHOD, rp.SIZE, rp.OVERLAP) == ("sentence", 512, 0)
    assert rp.PARAMS == {"size_unit": "tokens", "pack": "318", "structure_cuts": "sections/1",
                         "size_tolerance": 0.25, "min_piece": 128}
    spec = rp.registry_spec()
    assert spec.chunk_params == rp.PARAMS and spec.chunk_size == 512 and spec.chunk_overlap == 0
    args = rp.embed_args()
    assert args.embedding_url == [rp.NO_HOST] and args.chunk_max_tokens == acc.MODEL_WINDOW


def test_export_reproduces_the_control_chunker(words: WordCounter, tmp_path: Path) -> None:
    from ragstack.ingestion.loaders import deterministic_doc_id

    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(records(tmp_path), dst)
    out = tmp_path / "chunks.jsonl"
    rec = rp.run_export([dst], out)
    rows = rp.read_rows(out)
    cc = acc.control_chunker()
    want = []
    for path, text in (("PMC0001#body", DOC_A), ("PMC0002#body", DOC_B), ("PMC0003#body", DOC_C)):
        cd = acc.chunk_document(deterministic_doc_id(path), text, cc)
        want += [(c.id, c.doc_id, c.start_char, c.end_char, c.content) for c in cd.chunks]
    assert [(r["chunk_id"], r["doc_id"], r["start_char"], r["end_char"], r["content"]) for r in rows] == want
    assert len(want) > 3  # the section cuts and the size cap both split
    assert rec["chunks"] == len(rows) and rec["documents"] == 3 and rec["chunks_sha256"] == asc.sha256_file(out)
    assert all(r["metadata"]["chunk_descriptor"] == rp.DESCRIPTOR and r["metadata"].get("source_path") for r in rows)
    assert rec["chunker"]["params"] == rp.PARAMS and rec["files"][0]["records"] == 3
    # the export reads as a chunk export (the runner's reader)
    assert set(asc.read_chunk_exports([out])) == {w[0] for w in want}
    # the same input, the same bytes
    rp.run_export([dst], tmp_path / "again.jsonl")
    assert asc.sha256_file(tmp_path / "again.jsonl") == rec["chunks_sha256"]


def test_export_refuses_records_without_the_structure_key(words: WordCounter, tmp_path: Path) -> None:
    out = tmp_path / "chunks.jsonl"
    with pytest.raises(SystemExit, match="no structure key"):
        rp.run_export([records(tmp_path)], out)
    assert not out.exists()


def test_export_expect_chunks_and_duplicate_records(words: WordCounter, tmp_path: Path) -> None:
    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(records(tmp_path), dst)
    out = tmp_path / "chunks.jsonl"
    with pytest.raises(SystemExit, match="--expect-chunks 1"):
        rp.run_export([dst], out, expect_chunks=1)
    assert not out.exists()
    copy = tmp_path / "copy.jsonl"
    copy.write_bytes(dst.read_bytes())
    with pytest.raises(SystemExit, match="produced twice"):
        rp.run_export([dst, copy], out)
    with pytest.raises(SystemExit, match="one of --records"):
        rp.run_export([dst], dst)


def test_export_sends_no_request(words: WordCounter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline's embedder sits on the refusing transport (an embed call forced through it is
    refused there, before any socket), and the export itself sends nothing."""
    import asyncio

    import httpx

    refused: list[str] = []
    real_refuse = rp._refuse_request

    def counting_refuse(request: Any) -> Any:
        refused.append(str(request.url))
        return real_refuse(request)

    monkeypatch.setattr(rp, "_refuse_request", counting_refuse)
    opened: list[Any] = []
    real = httpx.AsyncClient.send

    async def spy(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        opened.append(str(request.url))
        return await real(self, request, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "send", spy)

    # the export's own pipeline, with an embed call forced through its embedder
    pipe, http = rp.build_pipeline(rp.embed_args(), rp.registry_spec())

    async def force() -> None:
        try:
            await pipe.embedder.embed(["a text the export never embeds"])
        finally:
            await http.aclose()

    with pytest.raises(Exception) as caught:
        asyncio.run(force())
    chain, e = [], caught.value
    while e is not None:
        chain.append(str(e))
        e = e.__cause__ or e.__context__
    assert any("no request is ever sent" in m for m in chain), chain
    assert refused and all(u.startswith(rp.NO_HOST) for u in refused)
    assert opened and all(u.startswith(rp.NO_HOST) for u in opened)

    # the export: nothing is sent at all
    refused.clear()
    opened.clear()
    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(records(tmp_path), dst)
    rp.run_export([dst], tmp_path / "chunks.jsonl")
    assert opened == [] and refused == []


def test_export_refuses_a_window_that_clamps_the_tolerance(words: WordCounter, tmp_path: Path) -> None:
    dst = tmp_path / "s.jsonl"
    rp.run_add_structure(records(tmp_path), dst)
    with pytest.raises(SystemExit, match="target/ceiling/min piece"):
        rp.run_export([dst], tmp_path / "chunks.jsonl", model_window=600)


# ------------------------------------------------------------------ gold-map

def gold(tmp: Path) -> tuple[rfx.GoldWorld, dict[str, Any], Path]:
    w = rfx.GoldWorld(tmp / "in")
    out = tmp / "private" / "labels.jsonl"
    rec = rp.run_gold_map(w.paths["sample"], w.paths["pairs_seed"], w.paths["pairs_seed_cover"],
                          w.paths["control_final"], w.paths["cover_final"], w.runs, out)
    return w, rec, out


def test_gold_map_counts(tmp_path: Path) -> None:
    _, rec, out = gold(tmp_path)
    assert rec["pairs"] == 8 and rec["records"] == 7
    assert rec["by_corpus_arm"] == {"base": 3, "neighbour": 2, "random1": 2, "random2": 1}
    assert rec["by_arm_group"] == {"base": 3, "neighbour": 2, "random": 3}
    assert rec["records_by_corpus_arm"] == {"base": 3, "neighbour": 2, "random1": 2, "random2": 1}
    assert rec["random_both_draws"] == 1 and rec["lost"] == 4 and rec["lost_without_cover"] == 1
    # pair 8: a base window stage A retrieved only in a neighbour leg, still run in base
    assert rec["base_windows_only_in_larger_arm_legs"] == 1
    assert rec["cover_class"] == {"other_only": 1, "both": 0, "primary_only": 1, "none": 1}
    assert rec["control_label"] == {"answers": 3, "not": 4, "unjudged": 1}
    assert rec["labels"] == 12 and rec["label"] == {"answers": 5, "not": 5, "unjudged": 2}
    assert rec["label_source"] == {"control": 5, "control+cover": 3, "cover": 4}
    assert rec["registry_conflicts"] == 1 and rec["cover_any_chunks"] == 7
    assert rec["coverage_band"] == {">=0.8": 4, "0.5-0.8": 3, "<0.5": 1}
    assert rec["table_sha256"] == asc.sha256_file(out) and out.stat().st_mode & 0o777 == 0o600


def test_gold_map_pair_and_label_rows(tmp_path: Path) -> None:
    w, _, out = gold(tmp_path)
    t = rp.read_label_table(out)
    by = {p["stage_a_chunk_id"]: p for p in t.pairs}
    p = {x.tag: x for x in w.pairs}

    def row(tag: str) -> dict[str, Any]:
        return by[p[tag].window]

    assert row("2")["cover_class"] == "other_only" and row("2")["lost"] is True
    assert row("2")["cover_any_chunk_ids"] == [rfx.cid("m2"), rfx.cid("o2a"), rfx.cid("o2b")]
    assert row("3")["cover_class"] == "primary_only" and row("3")["control_label"] == "not"
    assert row("4")["cover_class"] == "none" and row("4")["corpus_arm"] == "random1"
    assert row("5")["corpus_arm"] == "random2" and row("5")["coverage_band"] == "<0.5"
    assert row("6")["corpus_arm"] == "random1" and row("6")["random_both_draws"] is True
    assert row("6")["control_label"] == "unjudged" and row("6")["lost"] is False and row("6")["cover_class"] is None
    assert row("8")["lost"] is True and row("8")["cover_class"] is None
    assert row("8")["corpus_arm"] == "base" and row("8")["stage_a_legs"]
    assert not any(lg.startswith("base|") for lg in row("8")["stage_a_legs"]) and rp.base_only_in_larger_arm(row("8"))
    assert not rp.base_only_in_larger_arm(row("1")) and not rp.base_only_in_larger_arm(row("3"))
    # the sample's stratum is kept as the draw stratum (no stage-A route is claimed)
    assert {x["draw_stratum"] for x in t.pairs} == {"answers"} and all("stratum" not in x for x in t.pairs)
    assert row("1")["max_overlap_chunk_id"] == p["1"].control and row("1")["pair_id"] == p["1"].pair_id
    # the window's stage-A legs: every retrieval stage-A leg of its corpus arm, the failed bm25 row skipped
    assert "base|none|default" in row("1")["stage_a_legs"] and "base|merged|bm25" in row("1")["stage_a_legs"]
    assert "random2|none|bm25" not in row("5")["stage_a_legs"] and "random2|none|default" in row("5")["stage_a_legs"]
    # one record, two pairs in two corpus arms
    assert t.records("base") & t.records("neighbour") == {rfx.rid("P01")}
    # combined labels: any registry's answers wins; the conflict is kept per registry
    r3 = p["3"].record
    assert t.label(r3, p["3"].control) == "answers"
    assert t.labels[(r3, p["3"].control)]["registries"] == {"control": "not", "cover": "answers"}
    assert t.label(p["4"].record, rfx.cid("o4a")) == "unjudged" and t.label(p["6"].record, p["6"].control) == "unjudged"
    assert t.label(p["2"].record, rfx.cid("o2a")) == "answers" and t.label(p["2"].record, p["2"].control) == "not"
    assert t.label(p["1"].record, rfx.cid("o2a")) is None


def test_gold_map_summary_is_public_clean(tmp_path: Path) -> None:
    _, rec, out = gold(tmp_path)
    t = rp.read_label_table(out)
    summ = json.loads(out.with_name(out.name + ".summary.json").read_text())
    assert summ["pairs"] == 8 and asc.public_problems(summ, t.forbidden()) == []
    assert "host" not in summ["provenance"] and set(summ["inputs_sha256"]) == {
        "sample", "pairs_seed", "pairs_seed_cover", "control_final", "cover_final"}
    assert len(summ["stage_a_rows_sha256"]) == 9  # five attachments' files and four repeat files


def test_gold_map_cli_and_write_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = rfx.GoldWorld(tmp_path / "in")
    out = tmp_path / "labels.jsonl"
    assert rp.main(w.args(out)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["pairs"] == 8 and printed["lost"] == 4
    assert rp.main(w.args(out)) == 0  # the same table again: kept
    out.write_text(out.read_text() + "\n{}\n")
    with pytest.raises(SystemExit, match="exists with other content"):
        rp.main(w.args(out))
    with pytest.raises(SystemExit, match="inside the repository"):
        rp.main(w.args(Path(__file__).resolve().parent / "labels.jsonl"))


def test_gold_map_refusals(tmp_path: Path) -> None:
    w = rfx.GoldWorld(tmp_path)

    def gm(**over: Any) -> Any:
        a = {"sample": w.sample, "seeds": w.seeds, "cover_seeds": w.cover_seeds, "cfinal": w.cfinal,
             "vfinal": w.vfinal}
        a.update(over)
        hits, _ = rp.stage_a_hits(w.runs, {(s["record_id"], s["chunk_id"]) for s in a["sample"]})
        return rp.gold_map(a["sample"], a["seeds"], a["cover_seeds"], a["cfinal"], a["vfinal"], hits)

    rows, counts = gm()
    assert counts["pairs"] == 8 and len(rows) == 8 + 12
    bad_pid = [dict(s) for s in w.sample]
    bad_pid[0]["pair_id"] = "0" * 16
    with pytest.raises(SystemExit, match="sha256"):
        gm(sample=bad_pid)
    with pytest.raises(SystemExit, match="maps 7 of the 8"):
        gm(seeds=w.seeds[1:])
    other_window = [dict(s) for s in w.seeds]
    other_window[0]["stage_a_chunk_id"] = rfx.cid("elsewhere")
    with pytest.raises(SystemExit, match="stage-A window"):
        gm(seeds=other_window)
    with pytest.raises(SystemExit, match="one chunk twice"):
        gm(cover_seeds=[*w.cover_seeds, w.cover_seeds[0]])
    foreign = [dict(c) for c in w.cover_seeds]
    foreign[0]["record_id"] = rfx.rid("P99")
    with pytest.raises(SystemExit, match="pairs-seed-cover"):
        gm(cover_seeds=foreign)
    # a random pair no judged stage-A leg retrieved cannot be placed in a draw
    with pytest.raises(SystemExit, match="random pair"):
        rp.gold_map(w.sample, w.seeds, w.cover_seeds, w.cfinal, w.vfinal, {})


def test_gold_map_cover_split_cross_check_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The table's cover classes come from rechunk_prep.cover_class; the cross-check recounts them
    with alias_control_chunks.cover_split over the same labels. A local rule that drifts from
    cover_split (here: other_only read as none) is refused, nothing is returned."""
    w = rfx.GoldWorld(tmp_path)
    hits, _ = rp.stage_a_hits(w.runs, {(s["record_id"], s["chunk_id"]) for s in w.sample})
    real = rp.cover_class

    def drifted(other: Any, primary: Any) -> str:
        c = real(other, primary)
        return "none" if c == "other_only" else c

    monkeypatch.setattr(rp, "cover_class", drifted)
    with pytest.raises(SystemExit, match="disagree with alias_control_chunks.cover_split"):
        rp.gold_map(w.sample, w.seeds, w.cover_seeds, w.cfinal, w.vfinal, hits)


def test_final_rows_are_read_without_repair(tmp_path: Path) -> None:
    p = tmp_path / "final.jsonl"
    p.write_text('{"k": "batch|1"}\n{"k": "pair|a", "pair_id": "a", "label": 1}\n{"k": "pair|b", "pa\n')
    with pytest.raises(SystemExit, match="nothing is repaired"):
        rp.final_rows(p)
    p.write_text('{"k": "pair|a", "pair_id": "a", "label": 1}\n{"k": "pair|a", "pair_id": "a", "label": 0}\n')
    with pytest.raises(SystemExit, match="two different final rows"):
        rp.final_rows(p)


def test_label_word_and_combine() -> None:
    assert rp.label_word(None) is None and rp.label_word({"label": None, "source": "unjudged"}) == "unjudged"
    assert rp.label_word({"label": 1, "source": "agree"}) == "answers"
    assert rp.label_word({"label": 0, "source": "tiebreak"}) == "not"
    assert rp.combine({"control": "not", "cover": "answers"}) == "answers"
    assert rp.combine({"control": "unjudged", "cover": "not"}) == "not"
    assert rp.combine({"control": "unjudged"}) == "unjudged"
    assert [rp.cover_class(o, p) for o, p in (([1], 1), ([1, 0], 0), ([0], 1), ([], None))] == [
        "both", "other_only", "primary_only", "none"]


def test_read_label_table_refuses_another_file(tmp_path: Path) -> None:
    p = rfx.write_jsonl(tmp_path / "x.jsonl", [{"kind": "pair"}])
    with pytest.raises(SystemExit, match="not a rechunk label table"):
        rp.read_label_table(p)
    pr = rfx.pair_row(rfx.rid("P01"), "base", rfx.cid("m1"), tag="1")
    p2 = rfx.write_label_table(tmp_path / "t.jsonl", [pr], [])
    rows = rp.read_rows(p2)
    rows[0]["pairs"] = 2
    with pytest.raises(SystemExit, match="row counts"):
        rp.read_label_table(rfx.write_jsonl(tmp_path / "t2.jsonl", rows))


# ------------------------------------------------------------------ presence

def test_rechunk_presence(tmp_path: Path) -> None:
    r1, r2 = rfx.rid("P01"), rfx.rid("P02")
    pairs = [rfx.pair_row(r1, "base", rfx.cid("m1"), tag="1"),
             rfx.pair_row(r2, "base", rfx.cid("m2"), tag="2", covers=[rfx.cid("m2"), rfx.cid("o2")]),
             rfx.pair_row(r1, "neighbour", rfx.cid("m7"), tag="7")]
    table = rp.read_label_table(rfx.write_label_table(tmp_path / "t.jsonl", pairs, []))

    def export(name: str, ids: list[str], desc: str | None = rp.DESCRIPTOR) -> Path:
        md = {} if desc is None else {"chunk_descriptor": desc}
        return rfx.write_jsonl(tmp_path / name, [{"chunk_id": c, "doc_id": "d", "content": f"text {c}", "start_char": 0,
                                                 "end_char": 9, "metadata": md} for c in ids])

    def check(exports: list[Path], arm: str = "base", n: int = 4, receipt: Path | None = None) -> dict[str, Any]:
        return rp.check_rechunk_presence(table, exports, arm, expected_chunks=n, load_receipt=receipt)

    good = export("good.jsonl", [rfx.cid(t) for t in ("m1", "m2", "o2", "x")])
    got = check([good])
    assert got["pairs"] == 2 and got["records"] == 2 and got["mapped_chunks"] == 3 and got["present"] == 3
    assert got["label_table_sha256"] == table.sha256 and got["chunks_in_export"] == 4
    assert got["expected_chunks"] == 4 and got["chunk_descriptor"] == rp.DESCRIPTOR
    assert got["descriptor_checked_in"] == ["export_rows"]
    with pytest.raises(f10.Refused, match="1 of the 3 mapped"):
        check([export("bad.jsonl", [rfx.cid(t) for t in ("m1", "m2")])], n=2)
    with pytest.raises(f10.Refused, match="neighbour"):
        check([good], arm="neighbour")
    # the export holds another number of chunks than the run config expects
    with pytest.raises(f10.Refused, match="holds 4 chunks, corpora.base.expected_chunks is 5"):
        check([good], n=5)
    # an export of the stage-A chunking, or rows without the stamp
    with pytest.raises(f10.Refused, match="another chunk_descriptor"):
        check([export("stage-a.jsonl", [rfx.cid(t) for t in ("m1", "m2", "o2", "x")], acc.STAGE_A_DESCRIPTOR)])
    with pytest.raises(f10.Refused, match="<none>"):
        check([export("bare.jsonl", [rfx.cid(t) for t in ("m1", "m2", "o2", "x")], None)])
    # the load receipt: no descriptor recorded (load_embeddings.py today) passes on the rows' stamp;
    # a recorded one must be the confirmatory descriptor
    plain = tmp_path / "receipt.json"
    plain.write_text(json.dumps({"n_chunks": 4}))
    assert check([good], receipt=plain)["descriptor_checked_in"] == ["export_rows"]
    same = tmp_path / "receipt-same.json"
    same.write_text(json.dumps({"n_chunks": 4, "chunking": {"chunk_descriptor": rp.DESCRIPTOR}}))
    assert check([good], receipt=same)["descriptor_checked_in"] == ["export_rows", "load_receipt"]
    for name, rec in (("r1.json", {"chunk_descriptor": acc.STAGE_A_DESCRIPTOR}),
                      ("r2.json", {"chunking": {"chunk_descriptor": "sentence/512/0"}})):
        (tmp_path / name).write_text(json.dumps(rec))
        with pytest.raises(f10.Refused, match="load receipt records chunk_descriptor"):
            check([good], receipt=tmp_path / name)
