"""Alias study glossary files (prereg §5.3, §15 item 7): the writer, the rendered field as
specified, and render parity. ``load_embeddings``' render path (``read_glossary_file`` ->
``resolve_documents`` -> ``render_chunks``) must equal the independent §5.3 renderer
(:func:`alias_glossary_file.spec_field`, built on ``alias_abbrev.alias_occurs``'s tokens) byte for
byte, and each mutation (cap 11, no plural entries, order flipped) must break that.

The synthetic corpus below is made up. The real-data parity test reads the private inputs
read-only, and only when ``RAGSTACK_ALIAS_PRIVATE_PARITY=1``. It prints counts only."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import _glossbench_r2 as g  # noqa: E402
import alias_glossary_file as agf  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    attach_mark_problems,
    read_glossary_file,
    write_glossary_file,
)
from ragstack.ingestion.loaders import JsonlLoader  # noqa: E402

PROV = {"commit": "0" * 40, "describe": "test", "dirty": False, "untracked": 0}
LONG = " ".join(["word"] * 60)  # a ~300-character primary: four such lines pass 1,200 chars

# paper -> document texts (synthetic)
TEXTS = {
    "PA": ["We grew MSCs in culture. The Qb was measured.\n\nMSC and MSCs both appear here.",
           "Each PC and the PCs were kept. " + " ".join(f"K{c}" for c in "ABCDEFGHIJKLM") + " end."],
    "PB": ["Only Qb here, and the MSC once. Also VL1 VL2 VL3 VL4 VL5."],
    "PC": ["No glossary for this paper at all."],
    "PD": ["A paper of its own: MSC appears, and PCs."],
}


def entry(paper: str, alias: str, primary: str, kind: str) -> dict[str, Any]:
    return {"paper": paper, "doc_id": "x", "alias": alias, "primary": primary, "kind": kind,
            "extractor": None, "used_elsewhere": None}


MERGED = [
    entry("PA", "MSC", "mesenchymal stem cell", "abbreviation"),
    entry("PA", "Qb", "breathing flow rate", "symbol"),
    entry("PA", "Qb", "breathing rate", "symbol"),
    entry("PA", "PC", "principal component", "both"),
    *[entry("PA", f"K{c}", f"kinase {c.lower()} complex", "abbreviation") for c in "ABCDEFGHIJKLM"],
    entry("PB", "Qb", "breathing flow rate", "symbol"),          # equal to one of PA's own (R21)
    entry("PB", "MSC", "marrow stromal cell", "abbreviation"),
    *[entry("PB", f"VL{i}", f"{LONG} {i}", "abbreviation") for i in range(1, 6)],
    entry("PD", "PC", "plasma cell", "abbreviation"),
]


@pytest.fixture()
def corpus(tmp_path: Path) -> dict[str, Path]:
    """A shard (``JsonlLoader`` records) and its chunk export, chunks tiling each text."""
    shard = tmp_path / "in" / "shard.jsonl"
    shard.parent.mkdir()
    recs = [{"text": t, "path": f"{p}#sec{i}", "metadata": {"title": p}}
            for p, ts in TEXTS.items() for i, t in enumerate(ts)]
    shard.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    export = tmp_path / "in" / "chunks.jsonl"
    rows = []
    for d in JsonlLoader().load(str(shard)):
        text = d.content
        cut = text.find("\n\n")
        cuts = [0, cut + 2, len(text)] if cut > 0 else [0, len(text)]
        for k in range(len(cuts) - 1):
            a, b = cuts[k], cuts[k + 1]
            rows.append({"chunk_id": f"{d.id}:{k}", "doc_id": d.id, "content": text[a:b], "start_char": a,
                         "end_char": b, "metadata": {"source_path": d.source, "title": "t"}})
    export.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    merged = tmp_path / "in" / "merged.jsonl"
    merged.write_text("".join(json.dumps(e) + "\n" for e in MERGED), encoding="utf-8")
    return {"shard": shard, "export": export, "merged": merged, "out": tmp_path / "out"}


def write(c: dict[str, Path], variant: str, **kw: Any) -> dict[str, Any]:
    merged = [c["merged"]] if variant != "none" else []
    return agf.write_variant(variant, "base", shards=[c["shard"]], chunk_exports=[c["export"]], merged=merged,
                             out=c["out"] / f"base.{variant}.jsonl", created="2026-11-03T00:00:00Z",
                             provenance=PROV, **kw)


def parity(c: dict[str, Path], variant: str, gfile: Path | None = None, **spec_kw: Any) -> dict[str, int]:
    rows = g.read_chunks([c["export"]])
    load, _, _ = agf.load_render(gfile or c["out"] / f"base.{variant}.jsonl", rows)
    merged = [c["merged"]] if variant != "none" else []
    by_paper = agf.entries_by_paper_for(variant, "base", rows, merged)
    return agf.compare(load, agf.spec_fields(rows, by_paper, **spec_kw))


# ------------------------------------------------------------------ the rule

def test_forms_follow_alias_tokens() -> None:
    assert agf.forms_of("MSC", "abbreviation") == ["MSC", "MSCs"]
    assert agf.forms_of("PC", "both") == ["PC", "PCs"]
    assert agf.forms_of("Qb", "symbol") == ["Qb"]
    assert agf.forms_of("MSC", "abbreviation", plural=False) == ["MSC"]


def test_paper_glossary_one_line_per_form_codepoint_order_file_order_definitions() -> None:
    gl, st = agf.paper_glossary([e for e in MERGED if e["paper"] == "PA"], "v")
    syms = [e.symbol for e in gl.entries]
    assert syms == sorted(syms) and "MSCs" in syms and "PCs" in syms and "Qbs" not in syms
    qb = next(e for e in gl.entries if e.symbol == "Qb")
    assert [d.text for d in qb.definitions] == ["breathing flow rate", "breathing rate"]
    assert st.plural_forms_added == 2 + 13 and st.multi_definition_forms == 1


def test_spec_field_plural_only_and_both_forms() -> None:
    pa = [e for e in MERGED if e["paper"] == "PA"]
    assert agf.spec_field(pa, "We grew MSCs here.") == "MSCs — mesenchymal stem cell"
    assert agf.spec_field(pa, "MSC and MSCs.") == "MSC — mesenchymal stem cell\nMSCs — mesenchymal stem cell"
    assert agf.spec_field(pa, "The Qb rose.") == "Qb — breathing flow rate; breathing rate"
    assert agf.spec_field(pa, "QB and Qbs and MSC-derived") == "MSC — mesenchymal stem cell"


def test_caps_lines_then_chars() -> None:
    pa = [e for e in MERGED if e["paper"] == "PA"]
    field = agf.spec_field(pa, " ".join(f"K{c}" for c in "ABCDEFGHIJKLM"))
    assert len(field.split("\n")) == 12 and "KM" not in field
    pb = [e for e in MERGED if e["paper"] == "PB"]
    field = agf.spec_field(pb, "VL1 VL2 VL3 VL4 VL5")
    assert len(field.split("\n")) == 3 and len(field) <= 1200


def test_versions_and_config() -> None:
    for v in agf.VARIANTS:
        ver = agf.version_for(v, ["a" * 64])
        assert ver.endswith("+attach=mention") and not ver.startswith("llm/")
    assert len({agf.version_for(v, ["a" * 64]) for v in agf.VARIANTS}) == 3
    assert agf.version_for("merged", ["a" * 64]) != agf.version_for("merged", ["b" * 64])
    assert agf.CONFIG.attach_only_with_value is False and agf.CONFIG.positional is False
    assert (agf.CONFIG.max_entries_per_chunk, agf.CONFIG.max_chars_per_chunk) == (12, 1200)


# ------------------------------------------------------------------ files, identity, census

def test_every_document_gets_a_row_and_the_file_is_private(corpus: dict[str, Path]) -> None:
    man = write(corpus, "merged")
    gf = read_glossary_file(man["glossary_file"])
    docs = {d.id for d in JsonlLoader().load(str(corpus["shard"]))}
    assert set(gf.rows) == docs
    empty = [r for r in gf.rows.values() if not r.merged.entries]
    assert len(empty) == 1                                   # paper PC
    assert not attach_mark_problems(gf)
    assert {r.glossary_version for r in gf.rows.values()} == {man["version"]}
    assert gf.config == agf.CONFIG
    assert oct(os.stat(man["glossary_file"]).st_mode & 0o777) == "0o600"
    assert oct(os.stat(corpus["out"]).st_mode & 0o777) == "0o700"
    assert man["identity"]["export_check"] == {"verified": len(docs)}
    n_chunks = len(corpus["export"].read_text(encoding="utf-8").splitlines())
    assert man["stamps_rendered"] == {man["stamp"]: n_chunks}
    assert man["census"]["chunks_cut_by_line_cap"] == 1
    assert man["census"]["chunks_with_field"] > 0


def test_none_file_has_empty_rows_and_its_own_stamp(corpus: dict[str, Path]) -> None:
    man = write(corpus, "none")
    gf = read_glossary_file(man["glossary_file"])
    assert gf.header["counts"]["merged_entries"] == 0 and len(gf.rows) == 5
    assert man["census"]["chunks_with_field"] == 0
    assert man["stamp"] == "alias-none/1+attach=mention@file/1"


@pytest.mark.parametrize("variant", agf.VARIANTS)
def test_render_parity_synthetic(corpus: dict[str, Path], variant: str) -> None:
    write(corpus, variant)
    res = parity(corpus, variant)
    assert res["mismatches"] == 0, res
    if variant == "merged":
        assert res["chunks_with_field_load"] == 5      # every chunk but paper PC's


def test_mutation_cap_11_fails_parity(corpus: dict[str, Path]) -> None:
    write(corpus, "merged", config=replace(agf.CONFIG, max_entries_per_chunk=11))
    assert parity(corpus, "merged")["mismatches"] > 0


def test_mutation_no_plural_entries_fails_parity(corpus: dict[str, Path]) -> None:
    write(corpus, "merged", plural=False)
    assert parity(corpus, "merged")["mismatches"] > 0


def test_mutation_order_flipped_fails_parity(corpus: dict[str, Path]) -> None:
    write(corpus, "merged")
    assert parity(corpus, "merged", reverse=True)["mismatches"] > 0


def test_placebo_uses_the_harness_derangement_and_its_manifest_is_accepted(corpus: dict[str, Path]) -> None:
    import alias_study_run as ar

    man = write(corpus, "placebo")
    rows, counts = ar.build_placebo(MERGED)
    assert man["counts"]["placebo_dropped_own_entries"] == counts["placebo_dropped_own_entries"]
    assert man["counts"]["mapping_sha256"] == counts["mapping_sha256"]
    assert man["seed"] == "261101|alias|placebo"
    mapping = ar.placebo_mapping(["PA", "PB", "PD"])
    gf = read_glossary_file(man["glossary_file"])
    docs = {d.id: d.source.split("#")[0] for d in JsonlLoader().load(str(corpus["shard"]))}
    for doc_id, row in gf.rows.items():
        recv = docs[doc_id]
        if recv == "PC":
            assert not row.merged.entries
            continue
        src_primaries = {e["primary"] for e in MERGED if e["paper"] == mapping[recv]}
        own = {e["primary"] for e in MERGED if e["paper"] == recv}
        got = {d.text for e in row.merged.entries for d in e.definitions}
        assert got and got <= src_primaries and not (got & own - src_primaries)
    # what the harness reads (arms.placebo.manifest)
    mpath = Path(man["glossary_file"] + ".manifest.json")
    cfg = {"arms": {"placebo": {"manifest": str(mpath),
                                "glossary_file_sha256": {"base": man["glossary_file_sha256"]}}}}
    rec = ar.placebo_manifest_record(cfg)
    assert rec is not None and rec["glossary_file_sha256"] == man["glossary_file_sha256"]


def test_placebo_drops_receivers_own_entries() -> None:
    import alias_study_run as ar

    rows, counts = ar.build_placebo(MERGED)
    mapping = ar.placebo_mapping(["PA", "PB", "PD"])
    own = {(e["paper"], e["alias"], e["primary"]) for e in MERGED}
    assert not any((r["paper"], r["alias"], r["primary"]) in own for r in rows)
    assert counts["placebo_dropped_own_entries"]["entries"] == int(mapping["PA"] == "PB")


# ------------------------------------------------------------------ refusals

def test_refuses_an_export_document_missing_from_the_shards(corpus: dict[str, Path], tmp_path: Path) -> None:
    lines = corpus["shard"].read_text(encoding="utf-8").splitlines()
    short = tmp_path / "in" / "short.jsonl"
    short.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(agf.Refused, match="not in the shards"):
        agf.write_variant("merged", "base", shards=[short], chunk_exports=[corpus["export"]],
                          merged=[corpus["merged"]], out=corpus["out"] / "x.jsonl", provenance=PROV)


def test_refuses_a_shard_text_the_chunks_were_not_cut_from(corpus: dict[str, Path], tmp_path: Path) -> None:
    recs = [json.loads(x) for x in corpus["shard"].read_text(encoding="utf-8").splitlines()]
    recs[0]["text"] = recs[0]["text"].replace("culture", "cultures")
    alt = tmp_path / "in" / "alt.jsonl"
    alt.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    with pytest.raises(agf.Refused, match="do not verify"):
        agf.write_variant("merged", "base", shards=[alt], chunk_exports=[corpus["export"]],
                          merged=[corpus["merged"]], out=corpus["out"] / "x.jsonl", provenance=PROV)


def test_refusals_variant_arm_and_entries(corpus: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(agf.Refused, match="base only"):
        agf.write_variant("placebo", "neighbour", shards=[corpus["shard"]], chunk_exports=[corpus["export"]],
                          merged=[corpus["merged"]], out=corpus["out"] / "x.jsonl", provenance=PROV)
    with pytest.raises(agf.Refused, match="takes no merged"):
        agf.write_variant("none", "base", shards=[corpus["shard"]], chunk_exports=[corpus["export"]],
                          merged=[corpus["merged"]], out=corpus["out"] / "x.jsonl", provenance=PROV)
    foreign = tmp_path / "in" / "foreign.jsonl"
    foreign.write_text(json.dumps(entry("PZ", "MSC", "x y", "abbreviation")) + "\n", encoding="utf-8")
    with pytest.raises(agf.Refused, match="not in the corpus"):
        agf.write_variant("merged", "base", shards=[corpus["shard"]], chunk_exports=[corpus["export"]],
                          merged=[foreign], out=corpus["out"] / "x.jsonl", provenance=PROV)
    with pytest.raises(agf.Refused, match="canonical"):
        agf.paper_glossary([entry("PA", "IL-6", "interleukin 6", "abbreviation")], "v")
    with pytest.raises(SystemExit, match="inside the repository"):
        agf.write_variant("none", "base", shards=[corpus["shard"]], chunk_exports=[corpus["export"]], merged=[],
                          out=_HERE / "x.jsonl", provenance=PROV)


def test_reach_counts_the_records_own_entry() -> None:
    field = "MSCs — mesenchymal stem cell\nQb — breathing flow rate; breathing rate"
    assert agf.renders_entry(field, "MSC", "abbreviation", "mesenchymal stem cell")
    assert agf.renders_entry(field, "Qb", "symbol", "breathing rate")
    assert not agf.renders_entry(field, "Qb", "symbol", "breathing")
    assert not agf.renders_entry(field, "MSC", "symbol", "mesenchymal stem cell")


# ------------------------------------------------------------------ real data (opt-in, read-only)

PRIVATE = {
    "shard": os.environ.get("ALIAS_BASE_SHARD", "/rag/tmp/glossbench-261028/oa.jsonl"),
    "export": os.environ.get("ALIAS_BASE_EXPORT", "/rag/tmp/glossbench-round2-2026-11/exports/chunks-oa.jsonl"),
    "merged": os.environ.get("ALIAS_MERGED", "/rag/tmp/alias-study-2026-11/glossary/merged_glossary.jsonl"),
    "records": os.environ.get("ALIAS_RECORDS", "/rag/tmp/alias-study-2026-11/out/main/accepted-alias-O.jsonl"),
}
real = pytest.mark.skipif(
    os.environ.get("RAGSTACK_ALIAS_PRIVATE_PARITY") != "1" or not all(Path(p).is_file() for p in PRIVATE.values()),
    reason="private alias-study inputs: set RAGSTACK_ALIAS_PRIVATE_PARITY=1 on the study host")


@real
@pytest.mark.parametrize("variant", ["merged", "placebo"])
def test_render_parity_real_chunks(tmp_path: Path, variant: str) -> None:
    """Every base chunk: the load path vs the §5.3 renderer, then each mutation. Counts only."""
    old = os.umask(0o077)
    try:
        docs = agf.read_shard_documents([PRIVATE["shard"]])
        rows = g.read_chunks([PRIVATE["export"]])
        entries = agf.read_merged([PRIVATE["merged"]])
        by_paper = agf.entries_by_paper_for(variant, "base", rows, [PRIVATE["merged"]])
        spec = agf.spec_fields(rows, by_paper)

        def load(built: agf.Built, config: Any = agf.CONFIG) -> dict[str, str]:
            path = tmp_path / f"{variant}.{len(list(tmp_path.iterdir()))}.jsonl"
            write_glossary_file(path, built.rows, producer="test", config=config)
            return agf.load_render(path, rows)[0]

        built = agf.build(variant, "base", docs, entries, ["0" * 64])
        res = agf.compare(load(built), spec)
        print(variant, json.dumps(res))
        assert res["mismatches"] == 0 and res["chunks"] == len(rows)
        cap11 = agf.compare(load(built, replace(agf.CONFIG, max_entries_per_chunk=11)), spec)
        noplural = agf.compare(load(agf.build(variant, "base", docs, entries, ["0" * 64], plural=False)), spec)
        loaded = load(built)
        flipped = agf.compare(loaded, agf.spec_fields(rows, by_paper, reverse=True))
        print(variant, "mutations", json.dumps({"cap11": cap11["mismatches"], "no_plural": noplural["mismatches"],
                                                "order_flipped": flipped["mismatches"]}))
        assert flipped["mismatches"] > 0 and noplural["mismatches"] > 0
        if variant == "merged":
            assert cap11["mismatches"] > 0
            with open(PRIVATE["records"], encoding="utf-8") as fh:
                r = agf.reach(loaded, [json.loads(x) for x in fh if x.strip()])
            print("reach", json.dumps(r))
            assert r["balanced"]["G"] == 600 and r["balanced"]["renders_own_entry"] >= 480
    finally:
        os.umask(old)
