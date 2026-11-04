"""Glossary benchmark round 2, harness items 2a and 2b.

* 2a — ``glossary_file.py rederive --merge llm-only|rules-only|merged|none``:
  re-decides ``merged`` from the halves a file already holds, marks the mode in
  the version, keeps both halves;
* 2b — ``--shuffle-rendered SEED`` in ``backfill_symbol_glossary.py`` (this
  file) and in the in-memory ``part4-glossary`` harness
  (``scripts/eval/test_part4_glossary.py``), on the shared
  :mod:`ragstack.ingestion.glossary_shuffle`.

Offline: synthetic documents and glossaries, a fake Elasticsearch, no store,
no endpoint.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ragstack.ingestion.glossary_file import (
    MERGE_MODES,
    GlossaryFileError,
    GlossaryRow,
    choose_merged,
    file_sha256,
    masked_key,
    read_glossary_file,
    rederive_file,
    rederive_row,
    stamp_version,
    write_glossary_file,
)
from ragstack.ingestion.glossary_shuffle import (
    SHUFFLE_SCHEME,
    ShuffleError,
    check_seed,
    multiset_sha256,
    shuffle_rendered,
)
from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    EXTRACTOR_VERSION,
    VERSION_KEY,
    Definition,
    Glossary,
    GlossaryEntry,
    content_key,
)
from tests.unit.test_glossary_file import (  # noqa: F401 - the fixture is used by name
    FakeES,
    _bf_args,
    _es_rows,
    bf,
    corpus,
    gf_cli,
)

LLM_V = "llm/2:Model-X:0123456789ab:w80000x4:t4096:k0:r0"
CHUNK = "In the table, Qb = 0.5 and Zq = 3 and Wm = 7 per unit."


def _g(version: str, *pairs: tuple[str, str]) -> Glossary:
    return Glossary(tuple(GlossaryEntry(s, (Definition(d, 0),)) for s, d in sorted(pairs)), version)


RULES = _g(EXTRACTOR_VERSION, ("Qb", "breathing rate"))
LLM = _g(LLM_V, ("Wm", "window width"), ("Zq", "fade rate"))


def _row(doc_id: str, rules: Glossary, llm: Glossary | None, *, prov: dict[str, Any] | None = None,
         degraded: str | None = None) -> GlossaryRow:
    return GlossaryRow(doc_id, content_key(doc_id), len(doc_id), masked_key(doc_id), rules, llm,
                       choose_merged(rules, llm), {}, prov or {}, degraded)


def _rows() -> list[GlossaryRow]:
    return [
        _row("full", RULES, LLM),                                  # the LLM half has entries
        _row("empty", RULES, _g(LLM_V)),                            # the LLM kept nothing
        _row("skipped", RULES, None, prov={"llm": {"version": LLM_V, "skipped": "short"}}),
        _row("degraded", RULES, None, prov={"llm": {"version": LLM_V}}, degraded="cut_off"),
        _row("bare", _g(EXTRACTOR_VERSION), None),                   # no LLM version recorded at all
    ]


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "round1.jsonl"
    write_glossary_file(p, _rows(), producer="round-1 step", created="2026-10-28T12:00:00Z")
    return p


# --- 2a: rederive ---------------------------------------------------------------


def test_llm_only_is_the_llm_half_alone_with_no_rules_fallback(src: Path, tmp_path: Path) -> None:
    out = rederive_file(src, tmp_path / "pure.jsonl", "llm-only")
    f = read_glossary_file(tmp_path / "pure.jsonl")
    pure = f"{LLM_V}+merge=llm-only"
    assert f.rows["full"].merged.entries == LLM.entries
    for d in ("empty", "skipped", "degraded"):
        # under the old rule these fell back to the rules (Qb); pure renders ""
        assert f.rows[d].merged.entries == ()
        assert f.rows[d].merged.for_chunk(CHUNK, DEFAULT_CONFIG) == ""
        assert f.rows[d].glossary_version == pure
    assert f.rows["full"].glossary_version == pure
    assert f.rows["bare"].glossary_version == "none+merge=llm-only"
    assert "Zq — fade rate" in f.rows["full"].merged.for_chunk(CHUNK, DEFAULT_CONFIG)
    assert "Qb" not in f.rows["full"].merged.for_chunk(CHUNK, DEFAULT_CONFIG)
    # both halves, identity and degraded are carried unchanged
    for r in _rows():
        n = f.rows[r.doc_id]
        assert (n.rules, n.llm, n.text_sha256, n.text_chars, n.text_ws_sha256, n.degraded) == \
               (r.rules, r.llm, r.text_sha256, r.text_chars, r.text_ws_sha256, r.degraded)
        assert n.provenance["rederived"] == {"merge": "llm-only",
                                             "source_glossary_version": r.glossary_version}
    assert out["census"] == {"degraded": 1, "llm_absent": 3, "llm_empty": 1,
                             "rows_empty": 4, "rows_with_entries": 1}


def test_other_modes(src: Path, tmp_path: Path) -> None:
    got = {}
    for mode in MERGE_MODES:
        rederive_file(src, tmp_path / f"{mode}.jsonl", mode)
        got[mode] = read_glossary_file(tmp_path / f"{mode}.jsonl").rows
    for r in _rows():
        assert got["rules-only"][r.doc_id].merged.entries == r.rules.entries
        assert got["merged"][r.doc_id].merged.entries == r.merged.entries  # today's rule
        assert got["none"][r.doc_id].merged.entries == ()
    assert got["merged"]["full"].glossary_version == f"{LLM_V}+merge=merged"
    assert got["merged"]["empty"].glossary_version == f"{EXTRACTOR_VERSION}+merge=merged"
    assert got["rules-only"]["full"].glossary_version == f"{EXTRACTOR_VERSION}+merge=rules-only"
    assert got["none"]["full"].glossary_version == "none+merge=none"


def test_every_mode_stamps_differently_so_a_census_tells_them_apart(src: Path,
                                                                    tmp_path: Path) -> None:
    original = read_glossary_file(src).rows["full"]
    stamps = {"source": original.stamped().version}
    for mode in MERGE_MODES:
        stamps[mode] = rederive_row(original, mode).stamped().version
    assert len(set(stamps.values())) == len(stamps), stamps
    # the LLM prefix the backfill's replace guard keys on is kept
    assert stamps["llm-only"].startswith("llm/") and stamps["merged"].startswith("llm/")
    assert stamps["llm-only"].endswith("+merge=llm-only@file/1")


def test_rederive_is_deterministic_and_records_its_source(src: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    rederive_file(src, a, "llm-only")
    rederive_file(src, b, "llm-only")
    assert a.read_bytes() == b.read_bytes()
    h = read_glossary_file(a).header
    assert h["created"] == "2026-10-28T12:00:00Z"
    assert "--merge llm-only" in h["producer"] and file_sha256(src) in h["producer"]
    assert "round-1 step" in h["producer"]
    # rederiving a rederived file starts from the halves, not from the marked version
    c = tmp_path / "c.jsonl"
    rederive_file(a, c, "llm-only")
    assert read_glossary_file(c).rows["full"].glossary_version == f"{LLM_V}+merge=llm-only"


def test_rederive_refusals(src: Path, tmp_path: Path) -> None:
    rules_only = tmp_path / "rules.jsonl"
    write_glossary_file(rules_only, [_row("x", RULES, None)], producer="t")
    with pytest.raises(GlossaryFileError, match="no LLM half"):
        rederive_file(rules_only, tmp_path / "o.jsonl", "llm-only")
    with pytest.raises(GlossaryFileError, match="unknown merge mode"):
        rederive_file(src, tmp_path / "o.jsonl", "union")
    with pytest.raises(GlossaryFileError, match="is the input"):
        rederive_file(src, src, "none")
    assert not (tmp_path / "o.jsonl").exists()


def test_rederive_cli(src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "pure.jsonl"
    assert gf_cli.main(["rederive", "--merge", "llm-only", str(src), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["merge"] == "llm-only" and printed["counts"]["documents"] == 5
    assert printed["glossary_versions"] == ["llm/2:Model-X:0123456789ab:w80000x4:t4096:k0:r0"
                                            "+merge=llm-only", "none+merge=llm-only"]
    assert gf_cli.main(["verify", str(out)]) == 0
    with pytest.raises(SystemExit):
        gf_cli.main(["rederive", "--merge", "pure", str(src), "--out", str(out)])
    assert gf_cli.main(["rederive", "--merge", "none", str(src), "--out", str(src)]) == 2


# --- 2b: the shuffle core ---------------------------------------------------------


def _fields(n: int, empty_every: int = 3) -> dict[str, str]:
    return {f"c{i:03d}": ("" if i % empty_every == 0 else f"S{i} — def {'x' * i}")
            for i in range(n)}


def test_shuffle_keeps_population_and_multiset_and_moves_every_string() -> None:
    fields = _fields(60)
    res = shuffle_rendered(fields, "261101|shuf")
    populated = {k for k, v in fields.items() if v}
    assert set(res.assignment) == populated
    assert Counter(res.assignment.values()) == Counter(v for v in fields.values() if v)
    assert sorted(len(v) for v in res.assignment.values()) == \
           sorted(len(v) for v in fields.values() if v)
    # all strings distinct: a derangement is possible, so no chunk keeps its own
    assert res.fixed_points == 0
    assert all(res.assignment[k] != fields[k] for k in populated)
    rec = res.record()
    assert rec["seed"] == "261101|shuf" and rec["populated"] == len(populated)
    assert rec["empty"] == 60 - len(populated)
    assert rec["multiset_sha256"] == multiset_sha256(v for v in fields.values() if v)
    assert res.stamp_suffix() == f"+{SHUFFLE_SCHEME}:261101|shuf:{res.permutation_sha256[:12]}"


def test_shuffle_is_deterministic_and_seed_dependent() -> None:
    fields = _fields(40)
    a, b = shuffle_rendered(fields, "s1"), shuffle_rendered(dict(reversed(fields.items())), "s1")
    assert a.assignment == b.assignment and a.permutation_sha256 == b.permutation_sha256
    c = shuffle_rendered(fields, "s2")
    assert c.permutation_sha256 != a.permutation_sha256 and c.assignment != a.assignment


def test_pinned_permutation() -> None:
    """shuf/1 is a durable contract: the stream is SHA-256, not :mod:`random`'s,
    so the same seed means the same permutation on any Python. These literals
    were produced by this implementation; a change to them is a scheme bump."""
    r = shuffle_rendered({"a": "1", "b": "2", "c": "3", "d": "4"}, "pin")
    assert r.source == {"a": "c", "b": "d", "c": "b", "d": "a"}
    assert r.permutation_sha256 == \
        "1aef9aca491b3025740ecf294964a0a6c87132757234c74ba600884769da7ba3"


def test_fixed_points_by_value_are_repaired_where_possible_and_counted_where_not() -> None:
    # duplicates: two chunks of one document render the same line
    dup = {"a": "X", "b": "X", "c": "Y", "d": "Z", "e": "W"}
    res = shuffle_rendered(dup, "seed")
    assert res.fixed_points == 0
    assert all(res.assignment[k] != dup[k] for k in dup)
    # one string on more than half the chunks: some must keep an equal string
    major = {"a": "X", "b": "X", "c": "X", "d": "Y"}
    res = shuffle_rendered(major, "seed")
    assert res.fixed_points == 2 and Counter(res.assignment.values()) == Counter(major.values())
    # a single populated chunk cannot move
    one = shuffle_rendered({"a": "X", "b": ""}, "seed")
    assert one.fixed_points == 1 and one.assignment == {"a": "X"}
    assert shuffle_rendered({}, "seed").populated == 0


def test_shuffle_detects_a_changed_index() -> None:
    res = shuffle_rendered({"a": "X", "b": "Y"}, "s")
    assert res.rendered("a", "X") == "Y" and res.rendered("z", "") == ""
    with pytest.raises(ShuffleError, match="changed between passes"):
        res.rendered("a", "X2")
    with pytest.raises(ShuffleError, match="changed between passes"):
        res.rendered("z", "new field")


@pytest.mark.parametrize("seed", ["", "a:b", "x@y", "a b", "z" * 65])
def test_bad_seeds_are_refused(seed: str) -> None:
    with pytest.raises(ShuffleError):
        check_seed(seed)


# --- 2b: the backfill ----------------------------------------------------------------


def _llm_file(corpus: dict[str, Path]) -> Path:  # noqa: F811
    """The corpus's rules file re-labelled as an LLM file (every row's merged
    stamped llm/…), so the shuffled stamp's prefix and the guard are visible."""
    rows = [json.loads(x) for x in corpus["gfile"].read_text(encoding="utf-8").splitlines()]
    header, body = rows[0], rows[1:]
    for r in body:
        r["llm"] = {"version": LLM_V, "entries": r["rules"]["entries"]}
        r["merged"] = {"version": LLM_V, "entries": r["rules"]["entries"]}
        r["glossary_version"] = LLM_V
    header["counts"]["with_llm"] = len(body)
    p = corpus["dir"] / "llm.jsonl"
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in [header, *body]),
                 encoding="utf-8")
    return p


def _state(es: FakeES) -> dict[str, tuple[str, str]]:
    return {r["_id"]: (r.get("symbol_glossary") or "", r["metadata"].get(VERSION_KEY))
            for r in es.rows}


def test_backfill_shuffle_permutes_the_populated_chunks(corpus: dict[str, Path]) -> None:  # noqa: F811
    true_es = FakeES(_es_rows(corpus["emb"]))
    asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=true_es))
    true = _state(true_es)
    populated = {k for k, (f, _) in true.items() if f}
    assert len(populated) >= 3, "the fixture must render several fields"

    es = FakeES(_es_rows(corpus["emb"]))
    dry = asyncio.run(bf.run(_bf_args(corpus["gfile"], shuffle_rendered="261101|shuf"), es=es))
    assert es.bulks == [] and es.mappings == []          # the dry run writes nothing
    out = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True,
                                      shuffle_rendered="261101|shuf"), es=es))
    shuf = _state(es)
    # field population and the multiset of strings are unchanged; the dry run's
    # census counts are the true run's
    assert {k for k, (f, _) in shuf.items() if f} == populated
    assert Counter(f for f, _ in shuf.values() if f) == Counter(f for f, _ in true.values() if f)
    assert dry["chunks_with_glossary"] == out["chunks_with_glossary"] == len(populated)
    # the assignment changed, and every chunk carries the shuffle stamp
    rec = out["shuffle"]
    moved = sum(1 for k in populated if shuf[k][0] != true[k][0])
    assert moved == len(populated) - rec["fixed_points"] and moved > 0
    want = stamp_version(EXTRACTOR_VERSION) + f"+shuf/1:261101|shuf:{rec['permutation_sha256'][:12]}"
    assert {v for _, v in shuf.values()} == {want}
    assert want != stamp_version(EXTRACTOR_VERSION)
    assert rec["seed"] == "261101|shuf" and dry["shuffle"] == rec
    # resumable: the same shuffle again writes nothing
    again = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True,
                                        shuffle_rendered="261101|shuf"), es=es))
    assert again["chunks_to_update"] == 0
    # deterministic across runs on a fresh index
    es2 = FakeES(_es_rows(corpus["emb"]))
    asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True, shuffle_rendered="261101|shuf"),
                       es=es2))
    assert _state(es2) == shuf


def test_backfill_shuffle_keeps_the_llm_replace_guard(corpus: dict[str, Path]) -> None:  # noqa: F811
    rows = _es_rows(corpus["emb"])
    for r in rows:
        r["metadata"][VERSION_KEY] = "llm/1:m:abc"
    es = FakeES(rows)
    # a rules file, shuffled, would replace LLM glossaries: refused without --replace-llm
    with pytest.raises(SystemExit, match="--replace-llm"):
        asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True, shuffle_rendered="s"), es=es))
    assert es.bulks == [] and es.mappings == []
    # an LLM file shuffled keeps the llm/ prefix: no replacement, no refusal
    llm = _llm_file(corpus)
    out = asyncio.run(bf.run(_bf_args(llm, apply=True, shuffle_rendered="s"), es=es))
    assert out.get("chunks_llm_replaced", 0) == 0
    assert all(r["metadata"][VERSION_KEY].startswith(f"{LLM_V}@file/1+shuf/1:s:") for r in es.rows)


def test_backfill_shuffle_cli_guards(corpus: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    base = ["--es-url", "http://127.0.0.1:1", "--index", "i"]
    with pytest.raises(SystemExit):                      # needs --glossary-file
        bf.main([*base, "--shuffle-rendered", "s"])
    with pytest.raises(SystemExit):                      # a bad seed
        bf.main([*base, "--glossary-file", str(corpus["gfile"]), "--shuffle-rendered", "a:b"])
    with pytest.raises(SystemExit):                      # --apply still needs --confirm-index
        bf.main([*base, "--glossary-file", str(corpus["gfile"]), "--shuffle-rendered", "s",
                 "--apply"])
    seen: dict[str, Any] = {}

    async def fake_run(args: Any, store: Any = None) -> dict[str, Any]:
        seen.update(vars(args))
        return {}

    monkeypatch.setattr(bf, "run", fake_run)
    assert bf.main([*base, "--glossary-file", str(corpus["gfile"]),
                    "--shuffle-rendered", "261101|shuf"]) == 0
    assert seen["shuffle_rendered"] == "261101|shuf" and seen["apply"] is False


def test_plan_updates_refuses_a_drifted_chunk(corpus: dict[str, Path]) -> None:  # noqa: F811
    from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher

    g = Glossary((GlossaryEntry("Qb", (Definition("rate", 0),)),), "rules/3@file/1")
    rows = [{"_id": "x", "content": "Qb = 1", "start_char": 0, "metadata": {}}]
    res = shuffle_rendered({"x": "", "y": "Qb — other"}, "s")   # x was planned empty
    with pytest.raises(ShuffleError):
        bf.plan_updates(SymbolGlossaryAttacher(), "", rows, g, res)
