"""Glossary benchmark round 3: ``glossary_file.py rederive --attach value|mention``.

The attachment rule becomes the arm: a re-configured copy of a glossary file
whose header sets ``attach_only_with_value`` and whose every version is marked
``+attach=<rule>``; the backfill renders under the file's config. Round 2's
``rederive --merge`` must stay byte-identical (its frozen record is reproduced
from it): the output hashes below were computed with ``main`` at f408b7a,
before this change.

Offline: synthetic documents and glossaries, a fake Elasticsearch.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ragstack.ingestion.glossary_file import (
    ATTACH_MARK,
    ATTACH_RULES,
    MERGE_MODES,
    GlossaryFileError,
    attach_mark_problems,
    attach_rule_of_config,
    config_dict,
    file_sha256,
    read_glossary_file,
    rederive_file,
    rederive_row,
    stamp_version,
    version_attach,
    write_glossary_file,
)
from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    EXTRACTOR_VERSION,
    VERSION_KEY,
    Definition,
    Glossary,
    GlossaryEntry,
)
from tests.unit.test_glossary_file import (  # noqa: F401 - the fixture is used by name
    FakeES,
    _bf_args,
    _es_rows,
    bf,
    corpus,
    gf_cli,
)
from tests.unit.test_glossary_round2_arms import (  # noqa: F401 - fixture by name
    LLM_V,
    _row,
    _rows,
    src,
)

#: rederive_file(src, out, mode) output sha256 per mode on the ``src`` fixture,
#: computed with the code on main at f408b7a (before ``--attach`` existed)
ROUND2_REDERIVE_SHA256 = {
    "src": "ee83f1002294fa14794c5583f6cd9884d2f848452149f0a38888a74a426719f2",
    "llm-only": "6ec55bb5e017f10f54ee031a467f3d4125778342ad00c4bb22cd433bf908161e",
    "rules-only": "55ecc9be72f6cbf697f158e8df920d14fd162c5c39f36ab84ae1e3168a1c868a",
    "merged": "de0afeef8037f8765b78e46443f1a1721b6ddf7f9be3eab8d60480cd39169665",
    "none": "b1dd2b83a4788a084c9294986c37becf1d6232b1117a216024d96729888fb58a",
}
ROUND2_RESULT_KEYS = ["census", "counts", "glossary_versions", "merge", "out", "source", "source_sha256"]

#: chunk texts against the ``full`` row (LLM half: Wm, Zq; rules half: Qb)
VALUE_CHUNK = "In the table, Zq = 3 and Wm is the window width."     # Zq states a value, Wm is mentioned
MENTION_CHUNK = "Zq and Wm vary with depth (Table 2, 14 rows)."        # both mentioned, no value
PLAIN_CHUNK = "Nothing here names a symbol."


# --- round 2 unchanged -----------------------------------------------------------------


def test_round2_rederive_outputs_are_byte_identical_to_main(src: Path, tmp_path: Path) -> None:  # noqa: F811
    assert file_sha256(src) == ROUND2_REDERIVE_SHA256["src"]
    for mode in MERGE_MODES:
        out = rederive_file(src, tmp_path / f"{mode}.jsonl", mode)
        assert file_sha256(tmp_path / f"{mode}.jsonl") == ROUND2_REDERIVE_SHA256[mode], mode
        assert sorted(out) == ROUND2_RESULT_KEYS and "attach" not in out
        rows = read_glossary_file(tmp_path / f"{mode}.jsonl").rows.values()
        assert all(ATTACH_MARK not in r.glossary_version for r in rows)
        assert all(r.provenance["rederived"].keys() == {"merge", "source_glossary_version"} for r in rows)


# --- --attach -------------------------------------------------------------------------


@pytest.fixture
def pure(src: Path, tmp_path: Path) -> Path:  # noqa: F811
    """Round 2's pure arm (``--merge llm-only``): the source of the round-3 arms."""
    p = tmp_path / "pure.jsonl"
    rederive_file(src, p, "llm-only")
    return p


def test_attach_mention_changes_only_the_rule_and_the_mark(pure: Path, tmp_path: Path) -> None:
    out = tmp_path / "mention.jsonl"
    res = rederive_file(pure, out, attach="mention")
    a, b = read_glossary_file(pure), read_glossary_file(out)
    assert a.config.attach_only_with_value is True and b.config.attach_only_with_value is False
    assert config_dict(b.config) == {**config_dict(a.config), "attach_only_with_value": False}
    assert attach_rule_of_config(b.config) == "mention" and res["config"] == config_dict(b.config)
    assert set(a.rows) == set(b.rows)
    for d, r in a.rows.items():
        n = b.rows[d]
        assert (n.rules, n.llm, n.merged.entries, n.drops, n.degraded) == \
            (r.rules, r.llm, r.merged.entries, r.drops, r.degraded)
        assert (n.text_sha256, n.text_chars, n.text_ws_sha256) == (r.text_sha256, r.text_chars, r.text_ws_sha256)
        assert n.glossary_version == f"{r.glossary_version}+attach=mention"
        assert n.provenance["rederived"] == {"merge": None, "attach": "mention",
                                             "source_glossary_version": r.glossary_version}
    assert b.rows["full"].stamped().version == f"{LLM_V}+merge=llm-only+attach=mention@file/1"
    assert b.rows["bare"].stamped().version == "none+merge=llm-only+attach=mention@file/1"
    assert res["census"]["rows_entries_changed"] == 0
    assert res["attach"] == "mention" and res["merge"] is None
    assert b.header["created"] == a.header["created"]
    assert "--attach mention" in b.header["producer"] and file_sha256(pure) in b.header["producer"]
    assert attach_mark_problems(b) == [] and attach_mark_problems(a) == []
    # deterministic
    rederive_file(pure, tmp_path / "again.jsonl", attach="mention")
    assert (tmp_path / "again.jsonl").read_bytes() == out.read_bytes()


def test_render_parity_with_for_chunk_mention(pure: Path, tmp_path: Path) -> None:
    """The re-configured file renders, under its own config, exactly what the source's
    glossary renders with ``attach_only_with_value=False``; the value file what it
    renders with True. ``mention`` is a superset of ``value`` on every chunk."""
    rederive_file(pure, tmp_path / "m.jsonl", attach="mention")
    a, b = read_glossary_file(pure), read_glossary_file(tmp_path / "m.jsonl")
    off = replace(a.config, attach_only_with_value=False)
    seen_gain = 0
    for d, r in a.rows.items():
        for text in (VALUE_CHUNK, MENTION_CHUNK, PLAIN_CHUNK):
            got_m = b.rows[d].stamped().for_chunk(text, b.config, start_char=0)
            assert got_m == r.merged.for_chunk(text, off, start_char=0)
            got_v = r.stamped().for_chunk(text, a.config, start_char=0)
            assert set(filter(None, got_v.split("\n"))) <= set(filter(None, got_m.split("\n")))
            seen_gain += int(got_m != got_v)
    full_m = b.rows["full"].stamped()
    # hand-checked on the `full` row (pure: Wm, Zq)
    assert full_m.for_chunk(VALUE_CHUNK, b.config) == "Wm — window width\nZq — fade rate"
    assert a.rows["full"].merged.for_chunk(VALUE_CHUNK, a.config) == "Zq — fade rate"
    assert a.rows["full"].merged.for_chunk(MENTION_CHUNK, a.config) == ""
    assert full_m.for_chunk(MENTION_CHUNK, b.config) == "Wm — window width\nZq — fade rate"
    assert full_m.for_chunk(PLAIN_CHUNK, b.config) == ""
    assert seen_gain >= 2


def test_attach_composes_with_merge_and_is_kept_by_a_later_merge(src: Path, pure: Path,  # noqa: F811
                                                                  tmp_path: Path) -> None:
    both = tmp_path / "both.jsonl"
    rederive_file(src, both, "llm-only", attach="mention")
    two = tmp_path / "two.jsonl"
    rederive_file(pure, two, attach="mention")
    rb, rt = read_glossary_file(both), read_glossary_file(two)
    assert rb.config == rt.config
    assert {d: (r.merged, r.glossary_version) for d, r in rb.rows.items()} == \
        {d: (r.merged, r.glossary_version) for d, r in rt.rows.items()}
    assert "--merge llm-only --attach mention" in rb.header["producer"]
    # a later --merge on a mention file keeps the file's rule and its mark
    remerged = tmp_path / "remerged.jsonl"
    rederive_file(two, remerged, "merged")
    rr = read_glossary_file(remerged)
    assert rr.config.attach_only_with_value is False
    assert rr.rows["full"].glossary_version == f"{LLM_V}+merge=merged+attach=mention"
    assert attach_mark_problems(rr) == []
    # back to value: the mark is replaced, never doubled; the rule follows
    back = tmp_path / "back.jsonl"
    rederive_file(two, back, attach="value")
    rv = read_glossary_file(back)
    assert rv.config.attach_only_with_value is True
    assert rv.rows["full"].glossary_version == f"{LLM_V}+merge=llm-only+attach=value"
    again = tmp_path / "again.jsonl"
    rederive_file(two, again, attach="mention")
    assert read_glossary_file(again).rows["full"].glossary_version.count(ATTACH_MARK) == 1


def test_every_attach_arm_stamps_differently(pure: Path) -> None:
    row = read_glossary_file(pure).rows["full"]
    stamps = {"value (as is)": row.stamped().version,
              "value (marked)": rederive_row(row, None, attach="value").stamped().version,
              "mention": rederive_row(row, None, attach="mention").stamped().version}
    assert len(set(stamps.values())) == 3, stamps
    assert all(s.startswith("llm/") for s in stamps.values())     # the backfill's replace guard key


def test_version_attach_and_mark_problems(tmp_path: Path) -> None:
    assert version_attach("rules/3") == ("rules/3", None)
    assert version_attach("rules/3+attach=mention") == ("rules/3", "mention")
    assert version_attach("x+merge=llm-only+attach=value@file/1") == ("x+merge=llm-only@file/1", "value")
    assert version_attach("llm/2:a+attach=b:c") == ("llm/2:a+attach=b:c", None)   # not the last part
    # a header saying mention over unmarked rows, or a mark that disagrees, is reported
    p = tmp_path / "f.jsonl"
    write_glossary_file(p, [_row("x", Glossary((GlossaryEntry("Qb", (Definition("rate", 0),)),),
                                               EXTRACTOR_VERSION), None)],
                        producer="t", config=replace(DEFAULT_CONFIG, attach_only_with_value=False))
    assert attach_mark_problems(read_glossary_file(p)) == ["'x': unmarked, header rule mention"]


def test_attach_refusals(src: Path, tmp_path: Path) -> None:  # noqa: F811
    with pytest.raises(GlossaryFileError, match="name a merge mode"):
        rederive_file(src, tmp_path / "o.jsonl")
    with pytest.raises(GlossaryFileError, match="unknown attach rule"):
        rederive_file(src, tmp_path / "o.jsonl", attach="document")
    with pytest.raises(GlossaryFileError, match="is the input"):
        rederive_file(src, src, attach="mention")
    with pytest.raises(GlossaryFileError, match="name a merge mode"):
        rederive_row(_rows()[0], None)
    assert not (tmp_path / "o.jsonl").exists()
    assert tuple(ATTACH_RULES) == ("value", "mention")


def test_attach_cli(pure: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m.jsonl"
    assert gf_cli.main(["rederive", "--attach", "mention", str(pure), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["attach"] == "mention" and printed["config"]["attach_only_with_value"] is False
    assert printed["out_sha256"] == file_sha256(out)
    assert printed["glossary_versions"] == [f"{LLM_V}+merge=llm-only+attach=mention",
                                            "none+merge=llm-only+attach=mention"]
    assert gf_cli.main(["verify", str(out)]) == 0
    with pytest.raises(SystemExit):
        gf_cli.main(["rederive", str(pure), "--out", str(out)])              # neither flag
    with pytest.raises(SystemExit):
        gf_cli.main(["rederive", "--attach", "doc", str(pure), "--out", str(out)])


# --- the backfill honours the file's rule and writes only the field and the stamp --------


def test_backfill_renders_a_mention_file_and_writes_only_two_fields(corpus: dict[str, Path]) -> None:  # noqa: F811
    mention = corpus["dir"] / "rules-mention.jsonl"
    rederive_file(corpus["gfile"], mention, attach="mention")
    gm = read_glossary_file(mention)

    es_v = FakeES(_es_rows(corpus["emb"]))
    out_v = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=es_v))
    es_m = FakeES(_es_rows(corpus["emb"]))
    before = {r["_id"]: {k: v for k, v in r.items() if k not in ("symbol_glossary", "metadata")}
              for r in es_m.rows}
    meta_before = {r["_id"]: {k: v for k, v in r["metadata"].items() if k != VERSION_KEY} for r in es_m.rows}
    out_m = asyncio.run(bf.run(_bf_args(mention, apply=True), es=es_m))

    # only symbol_glossary and metadata.symbol_glossary_version are written
    for ops in es_m.bulks:
        for doc in ops[1::2]:
            assert set(doc["doc"]) == {"symbol_glossary", "metadata"}
            assert set(doc["doc"]["metadata"]) == {VERSION_KEY}
    assert {r["_id"]: {k: v for k, v in r.items() if k not in ("symbol_glossary", "metadata")}
            for r in es_m.rows} == before
    assert {r["_id"]: {k: v for k, v in r["metadata"].items() if k != VERSION_KEY} for r in es_m.rows} \
        == meta_before
    # every chunk is rendered under mention, stamped with the attach mark
    want_stamp = stamp_version(f"{EXTRACTOR_VERSION}+attach=mention")
    for r in es_m.rows:
        g = gm.rows[r["doc_id"]].stamped()
        assert (r["symbol_glossary"] or "") == g.for_chunk(r["content"], gm.config, start_char=r["start_char"])
        assert r["metadata"][VERSION_KEY] == want_stamp
    # mention reaches more chunks than value, and covers every value chunk
    val = {r["_id"]: r.get("symbol_glossary") or "" for r in es_v.rows}
    men = {r["_id"]: r.get("symbol_glossary") or "" for r in es_m.rows}
    assert out_m["chunks_with_glossary"] > out_v["chunks_with_glossary"]
    assert all(men[k] for k, v in val.items() if v)
    assert out_m["glossary_file"]["glossary_versions"] == [f"{EXTRACTOR_VERSION}+attach=mention"]


def test_backfill_shuffle_composes_with_the_attach_mark(corpus: dict[str, Path]) -> None:  # noqa: F811
    mention = corpus["dir"] / "rules-mention.jsonl"
    rederive_file(corpus["gfile"], mention, attach="mention")
    es = FakeES(_es_rows(corpus["emb"]))
    out = asyncio.run(bf.run(_bf_args(mention, apply=True, shuffle_rendered="261103|shuf"), es=es))
    suffix = out["shuffle"]["stamp_suffix"]
    assert suffix.startswith("+shuf/1:261103|shuf:")
    assert {r["metadata"][VERSION_KEY] for r in es.rows} == \
        {f"{EXTRACTOR_VERSION}+attach=mention@file/1{suffix}"}
