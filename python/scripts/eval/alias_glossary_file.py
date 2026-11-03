#!/usr/bin/env python3
"""Alias study (PREREG-alias-study.md §5.3, §15 item 7): the glossary files the bench loads render.

Each bench collection is built once by ``load-embeddings.cwl`` from the corpus's embedding shards
with a ``glossary_file`` (``ragstack.glossary_file/v1``, :mod:`ragstack.ingestion.glossary_file`).
A load takes one ``glossary_file``, so there is **one file per collection**, covering every
document the load writes. For an expanded arm that is the base shards plus the arm's added-paper
shards: neighbour × merged is the base merged rows plus the added papers' merged rows, and
neighbour × none is empty rows for all of them. This script writes that file with the module's
own writer, from the shard list, the chunk export(s) and the merged glossary file(s), which
are the base file plus, later, the arm's added-paper file. A manifest with a census goes beside
it. The load renders it with
``Glossary.for_chunk`` under the file's header config (``render_chunks``); nothing here renders
into a store.

**Variants.**

* ``none``: every document gets a row with no entry. The collection is stamped like the
  others, so the stamp census is uniform, and nothing depends on what the embedding shards
  carry in ``symbol_glossary``.
* ``merged``: each document gets its paper's merged glossary (``alias_abbrev.py``'s
  ``merged_glossary.jsonl`` rows, keyed (paper, alias, primary words), ``kind`` symbol /
  abbreviation / both). Every document of a paper carries the whole paper's glossary, because
  the glossary is per paper and a paper spans several documents.
* ``placebo`` (base only): each document gets the entries its paper received in the seeded
  derangement. That derangement is ``alias_study_run.build_placebo``, imported rather than
  copied: seed ``261101|alias|placebo``, and an entry equal to one of the receiver's own
  (alias + ``alias_abbrev.words(primary)``) is dropped (owner rule R21). A paper left with no
  entry gets an empty row, so it gets no field.

**The rendered field, stated exactly** (owner and coordinator, 2026-11-03). This is what
``render_chunks`` produces from these files and what :func:`spec_field` re-derives
independently. The parity tests hold the two equal byte for byte.

1. *Forms.* Each entry contributes its alias. An ``abbreviation`` or ``both`` entry also
   contributes the plural form ``alias + "s"``, always. A ``symbol`` entry contributes only its
   alias. These are ``alias_abbrev.alias_tokens``, so a form occurs in a chunk exactly when
   ``alias_abbrev.alias_occurs`` matches. "Always" rather than "only where the plural occurs in
   the paper" keeps the rule independent of the corpus: a form that does not occur never
   renders, and a placebo receiver's text is not the source paper's.
2. *Mention rule.* A form renders on a chunk when it is a whole token (``[\\w<bars>]+``) of the
   chunk's canonical text, case-sensitively. The header config is ``attach_only_with_value:
   false``: no value is needed, and abbreviations state none.
3. *One line per form.* The line is ``<form> — <def1>; <def2>…``. Its definitions are the
   primaries of every entry contributing that form, in merged-file order, exact duplicates
   dropped. A plural form that equals another entry's alias is one form holding both entries'
   primaries (counted in the census). ``positional: false``: all definitions render. The
   merged entries carry no document offsets, and a paper spans several documents.
4. *Order.* Forms in codepoint order (``sorted``), definitions as in (3).
5. *Caps.* At most 12 lines, so the cap counts lines (forms), not merged entries. The number of
   chunks where counting merged entries would give a different field is in the census.
   At most 1,200 characters, counting each line plus one ``\\n`` between lines. The first line
   that would pass a cap ends the field: no later line is tried, and no line is truncated.
6. *Text.* Each line is NFC (``display_form``). The primaries are stored as the merged file has
   them.

**Versions.** Each row's ``merged.version`` is ``alias-<variant>/1[:<inputs12>]+attach=mention``.
``<inputs12>`` is the first 12 hex characters of sha256 over the sorted sha256s of the merged
input files, so a census tells every variant, and every input set, apart. No version starts with
``llm/``. A chunk carries ``<version>@file/1``.

**Identity.** ``text_sha256`` / ``text_chars`` / ``text_ws_sha256`` come from the shards that
are actually submitted to embed-bulk, read with the ``JsonlLoader`` the embed step uses
(:func:`~ragstack.ingestion.glossary_file.masked_key`, ``content_key``). The script then
reassembles every document of the chunk export and runs ``check_document`` against its row, the
check the load makes. A document that does not verify refuses the file. Every shard document
gets a row: a document with no row would refuse the load.

**Census** (manifest, counts only). It counts documents and papers, with and without entries;
entries by kind; forms, including plural forms added and multi-definition forms; and the
identity verdicts. It counts chunks with a field and mean rendered characters. Those are
rendered offline over the chunk export by the load path itself: ``read_glossary_file`` →
``resolve_documents`` → ``render_chunks``. It also counts chunks cut by each cap, and the
disclosed difference from counting merged entries against the 12 cap. The placebo's manifest
carries ``build_placebo``'s counts, including ``placebo_dropped_own_entries``, plus
``glossary_file_sha256``, which is what the harness reads (``arms.placebo.manifest``).

Inputs are read-only. Outputs are private (mode 0600 in a 0700 directory, refused inside the
repository). No model, gateway, store or API call is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _glossbench_r2 as g  # noqa: E402
import alias_abbrev as aa  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    VERIFIED,
    VERIFIED_WS,
    GlossaryRow,
    check_document,
    file_sha256,
    masked_key,
    read_glossary_file,
    resolve_documents,
    stamp_version,
    write_glossary_file,
)
from ragstack.ingestion.reassembly import Reassembled, reassemble  # noqa: E402
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    DEFAULT_CONFIG,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    canonical_symbol,
    canonical_text,
    content_key,
)
from ragstack.models import Chunk  # noqa: E402

SCHEMA = "alias-glossary-file/1"
WRITER = "alias_glossary_file/1"
VARIANTS = ("none", "merged", "placebo")
CORPUS_ARMS = ("base", "neighbour", "random1", "random2")
#: the header config every alias file is written (and so rendered) under (§5.3; owner 2026-11-03)
CONFIG = replace(DEFAULT_CONFIG, attach_only_with_value=False, positional=False,
                 max_entries_per_chunk=12, max_chars_per_chunk=1200)
ATTACH_SUFFIX = "+attach=mention"
#: no-entry rows still need a ``rules`` half; this is its version (never rendered)
EMPTY_RULES_VERSION = "none"


class Refused(SystemExit):
    """A refusal: the run stops before (or instead of) writing, with the reason."""

    def __init__(self, msg: str) -> None:
        super().__init__(f"REFUSED: {msg}")


# ------------------------------------------------------------------ the rule (forms, glossary)

def forms_of(alias: str, kind: str, *, plural: bool = True) -> list[str]:
    """The forms one entry contributes (rule 1): ``alias_abbrev.alias_tokens``, the alias first."""
    toks = aa.alias_tokens(alias, kind if plural else "symbol")
    return [alias] + sorted(t for t in toks if t != alias)


def check_alias(alias: str) -> None:
    """Every alias must be a canonical whole token, or the served regex match and the study's
    token match could differ (they agree exactly on ``[\\w<bars>]+`` tokens)."""
    if not alias or canonical_symbol(alias) != alias or not aa.TOK.fullmatch(alias):
        raise Refused(f"an alias is not one canonical [\\w<bars>]+ token ({len(alias)} chars)")


@dataclass
class FormStats:
    forms: int = 0
    plural_forms_added: int = 0
    plural_form_collisions: int = 0
    multi_definition_forms: int = 0


def paper_glossary(entries: Sequence[Mapping[str, Any]], version: str, *, plural: bool = True
                   ) -> tuple[Glossary, FormStats]:
    """One paper's glossary (rules 1, 3, 4): one :class:`GlossaryEntry` per form, definitions =
    the primaries of the entries contributing it, merged-file order, exact duplicates dropped;
    every offset 0 (``positional: false`` never reads them)."""
    per: dict[str, list[str]] = {}
    owners: dict[str, set[str]] = defaultdict(set)
    st = FormStats()
    aliases = {str(e["alias"]) for e in entries}
    for e in entries:
        alias, kind, primary = str(e["alias"]), str(e.get("kind") or "abbreviation"), str(e["primary"])
        check_alias(alias)
        if not primary.strip():
            raise Refused("an entry has an empty primary")
        for f in forms_of(alias, kind, plural=plural):
            owners[f].add(alias)
            lst = per.setdefault(f, [])
            if primary not in lst:
                lst.append(primary)
    for f in per:
        if f not in aliases:
            st.plural_forms_added += 1
        elif len(owners[f]) > 1:
            st.plural_form_collisions += 1
    st.forms = len(per)
    st.multi_definition_forms = sum(len(v) > 1 for v in per.values())
    gl = Glossary(tuple(GlossaryEntry(f, tuple(Definition(p, 0) for p in per[f])) for f in sorted(per)),
                  version)
    return gl, st


def spec_field(entries: Sequence[Mapping[str, Any]], chunk_text: str, *, max_lines: int = 12,
               max_chars: int = 1200, plural: bool = True, reverse: bool = False) -> str:
    """The INDEPENDENT §5.3 renderer the parity tests hold ``render_chunks`` to. It works from
    the merged rows and ``alias_abbrev``'s matcher (``TOK`` tokens of the canonical text,
    ``alias_tokens``), not from a glossary file, the reader or ``Glossary.for_chunk``.
    ``plural=False`` and ``reverse=True`` are the parity tests' mutations."""
    toks = set(aa.TOK.findall(canonical_text(chunk_text)))
    defs: dict[str, list[str]] = {}
    for e in entries:
        kind = str(e.get("kind") or "abbreviation") if plural else "symbol"
        for f in aa.alias_tokens(str(e["alias"]), kind):
            lst = defs.setdefault(f, [])
            if str(e["primary"]) not in lst:
                lst.append(str(e["primary"]))
    lines: list[str] = []
    size = 0
    for f in sorted((f for f in defs if f in toks), reverse=reverse):
        if len(lines) >= max_lines:
            break
        line = unicodedata.normalize("NFC", f"{f} — {'; '.join(defs[f])}")
        extra = len(line) + (1 if lines else 0)
        if size + extra > max_chars:
            break
        lines.append(line)
        size += extra
    return "\n".join(lines)


def version_for(variant: str, merged_shas: Sequence[str]) -> str:
    if variant not in VARIANTS:
        raise Refused(f"unknown variant {variant!r} (one of {', '.join(VARIANTS)})")
    if variant == "none":
        return f"alias-none/1{ATTACH_SUFFIX}"
    tag = hashlib.sha256("".join(s + "\n" for s in sorted(merged_shas)).encode()).hexdigest()[:12]
    return f"alias-{variant}/1:{tag}{ATTACH_SUFFIX}"


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class Doc:
    doc_id: str
    text: str
    paper: str


def paper_root(source_path: str, doc_id: str) -> str:
    """The study's paper key: ``source_path`` up to ``#`` (``_glossbench_r2.paper_of`` path_root)."""
    return source_path.split("#", 1)[0] if source_path else doc_id


def read_shard_documents(paths: Sequence[str | Path]) -> dict[str, Doc]:
    """Every document of the shards as embed-bulk's loader yields it (``JsonlLoader``)."""
    from ragstack.ingestion.loaders import JsonlLoader

    docs: dict[str, Doc] = {}
    for p in paths:
        for d in JsonlLoader().load(str(p)):
            if d.id in docs:
                raise Refused(f"a document id is in two shard records ({Path(p).name})")
            sp = str((d.metadata or {}).get("source_path") or d.source or "")
            docs[d.id] = Doc(d.id, d.content, paper_root(sp, d.id))
    if not docs:
        raise Refused("the shards hold no document")
    return docs


def read_merged(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    if "alias" in e:
                        out.append(e)
    return out


def chunks_by_doc(rows: Sequence[g.ChunkRow]) -> dict[str, list[g.ChunkRow]]:
    by: dict[str, list[g.ChunkRow]] = defaultdict(list)
    for r in rows:
        by[r.doc_id].append(r)
    return by


def reassembled(by_doc: Mapping[str, Sequence[g.ChunkRow]]) -> list[Reassembled]:
    return [reassemble(d, [{"content": r.content, "start_char": r.start_char, "end_char": r.end_char}
                           for r in rs]) for d, rs in sorted(by_doc.items())]


# ------------------------------------------------------------------ building a file

@dataclass
class Built:
    rows: list[GlossaryRow]
    version: str
    entries_by_paper: dict[str, list[dict[str, Any]]]
    stats: dict[str, Any]
    placebo_counts: dict[str, Any] | None


def entries_for_variant(variant: str, corpus_arm: str, entries: Sequence[Mapping[str, Any]],
                        papers: set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None]:
    """paper → its entry list under ``variant`` (merged-file order), and the placebo counts."""
    if variant == "none":
        return {}, None
    foreign = {str(e["paper"]) for e in entries} - papers
    if foreign:
        raise Refused(f"{len(foreign)} glossary paper(s) are not in the corpus (wrong corpus arm or export?)")
    if variant == "placebo":
        if corpus_arm != "base":
            raise Refused("the placebo glossary is base only (§5.3)")
        import alias_study_run as ar

        rows, counts = ar.build_placebo(entries, papers)
        counts = {**counts, "seed": ar.SEED_PLACEBO}
    else:
        rows, counts = [dict(e) for e in entries], None
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in rows:
        by[str(e["paper"])].append(dict(e))
    return dict(by), counts


def build(variant: str, corpus_arm: str, docs: Mapping[str, Doc], entries: Sequence[Mapping[str, Any]],
          merged_shas: Sequence[str], *, plural: bool = True) -> Built:
    """Every document's row (shard order), the version, and the counts. Pure."""
    if corpus_arm not in CORPUS_ARMS:
        raise Refused(f"unknown corpus arm {corpus_arm!r} (one of {', '.join(CORPUS_ARMS)})")
    if variant != "none" and not entries:
        raise Refused(f"the {variant} variant needs merged glossary entries")
    papers = {d.paper for d in docs.values()}
    version = version_for(variant, merged_shas)
    by_paper, pcounts = entries_for_variant(variant, corpus_arm, entries, papers)
    glossaries: dict[str, tuple[Glossary, FormStats]] = {
        p: paper_glossary(es, version, plural=plural) for p, es in by_paper.items()}
    empty = Glossary((), version)
    rows: list[GlossaryRow] = []
    for d in docs.values():
        gl, fst = glossaries.get(d.paper, (empty, FormStats()))
        prov = {"writer": WRITER, "variant": variant, "paper_entries": len(by_paper.get(d.paper, ())),
                "forms": fst.forms}
        rows.append(GlossaryRow(d.doc_id, content_key(d.text), len(d.text), masked_key(d.text),
                                Glossary((), EMPTY_RULES_VERSION), None, gl, {}, prov, None))
    st = Counter()
    for _gl, fst in glossaries.values():
        st["forms"] += fst.forms
        st["plural_forms_added"] += fst.plural_forms_added
        st["plural_form_collisions"] += fst.plural_form_collisions
        st["multi_definition_forms"] += fst.multi_definition_forms
    stats = {"documents": len(docs), "papers": len(papers),
             "documents_with_entries": sum(1 for r in rows if r.merged.entries),
             "papers_with_entries": sum(1 for p in papers if glossaries.get(p, (empty,))[0].entries),
             "entries": sum(len(v) for v in by_paper.values()),
             "entries_by_kind": dict(sorted(Counter(str(e.get("kind")) for v in by_paper.values() for e in v).items())),
             **{k: st[k] for k in ("forms", "plural_forms_added", "plural_form_collisions", "multi_definition_forms")}}
    return Built(rows, version, by_paper, stats, pcounts)


# ------------------------------------------------------------------ the load path (census, parity)

def to_chunks(rows: Sequence[g.ChunkRow]) -> list[Chunk]:
    return [Chunk(id=r.chunk_id, doc_id=r.doc_id, content=r.content, start_char=r.start_char,
                  end_char=r.end_char, metadata={}) for r in rows]


def load_render(gfile_path: str | Path, export_rows: Sequence[g.ChunkRow]) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Render the export's chunks exactly as ``load_embeddings`` does: read the file, resolve
    every document from its reassembled chunks, ``render_chunks``. Returns chunk id → field,
    chunk id → stamp, and the identity verdicts. Refuses when any document does not resolve."""
    from ragstack.ingestion.glossary_file import render_chunks

    gf = read_glossary_file(gfile_path)
    by = chunks_by_doc(export_rows)
    res = resolve_documents(gf, reassembled(by))
    if res.problems:
        kinds = Counter(k for _, k in res.problems)
        raise Refused(f"the load would refuse this file against the export: {dict(kinds)}")
    chunks = to_chunks(export_rows)
    render_chunks(chunks, res.glossaries, gf.config)
    return ({c.id: c.symbol_glossary for c in chunks},
            {c.id: str(c.metadata.get("symbol_glossary_version")) for c in chunks}, res.counts())


def identity_check(rows: Sequence[GlossaryRow], export_rows: Sequence[g.ChunkRow]) -> dict[str, int]:
    """Every export document against its row (``check_document``); refuses on anything but a
    verification, or on an export document the shards do not hold."""
    by_id = {r.doc_id: r for r in rows}
    out: Counter[str] = Counter()
    for ra in reassembled(chunks_by_doc(export_rows)):
        out[check_document(by_id.get(ra.doc_id), ra)] += 1
    bad = {k: v for k, v in out.items() if k not in (VERIFIED, VERIFIED_WS)}
    if bad:
        raise Refused(f"export documents do not verify against the shards: {bad} (were these the shards submitted?)")
    return dict(sorted(out.items()))


def census(fields: Mapping[str, str], export_rows: Sequence[g.ChunkRow],
           by_paper: Mapping[str, Sequence[Mapping[str, Any]]], *, max_lines: int = 12) -> dict[str, Any]:
    """Field coverage over the export, rendered by the load path (``fields``), and the caps."""
    n = len(export_rows)
    with_field = [f for f in fields.values() if f]
    chars = sum(len(f) for f in with_field)
    cut_lines = cut_chars = gt12_entries = gt12_entries_not_cut = 0
    for r in export_rows:
        es = by_paper.get(paper_root(r.source_path, r.doc_id), ())
        if not es:
            continue
        toks = set(aa.TOK.findall(canonical_text(r.content)))
        eligible = {f for e in es for f in aa.alias_tokens(str(e["alias"]), str(e.get("kind") or "abbreviation"))
                    if f in toks}
        matched = sum(1 for e in es if aa.alias_occurs(str(e["alias"]), str(e.get("kind") or "abbreviation"), r.content))
        got = len(fields[r.chunk_id].split("\n")) if fields[r.chunk_id] else 0
        if got < len(eligible):
            if got >= max_lines:
                cut_lines += 1
            else:
                cut_chars += 1
        if matched > 12:
            gt12_entries += 1
            if got == len(eligible):
                gt12_entries_not_cut += 1
    return {"chunks": n, "chunks_with_field": len(with_field),
            "share_with_field": round(len(with_field) / n, 6) if n else None,
            "mean_field_chars_all": round(chars / n, 3) if n else None,
            "mean_field_chars_with_field": round(chars / len(with_field), 3) if with_field else None,
            "chunks_cut_by_line_cap": cut_lines, "chunks_cut_by_char_cap": cut_chars,
            # owner, 2026-11-03: the 12 cap counts lines (forms); disclosed: chunks where more than
            # 12 merged entries match (alias_occurs) yet every eligible line rendered
            "chunks_gt12_matched_entries": gt12_entries,
            "chunks_gt12_matched_entries_all_lines_rendered": gt12_entries_not_cut}


# ------------------------------------------------------------------ writing one variant

def _private_dir(path: Path) -> None:
    g.refuse_in_repo(path, "alias glossary files")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def write_variant(variant: str, corpus_arm: str, *, shards: Sequence[str | Path],
                  chunk_exports: Sequence[str | Path], merged: Sequence[str | Path], out: str | Path,
                  created: str | None = None, plural: bool = True, config: GlossaryConfig = CONFIG,
                  provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build, write and census one glossary file; write ``<out>.manifest.json``; return it.
    ``plural`` and ``config`` other than the defaults are for the parity tests' mutations."""
    out = Path(out)
    _private_dir(out.parent)
    old_umask = os.umask(0o077)
    try:
        return _write_variant(variant, corpus_arm, shards=shards, chunk_exports=chunk_exports, merged=merged,
                              out=out, created=created, plural=plural, config=config, provenance=provenance)
    finally:
        os.umask(old_umask)


def _write_variant(variant: str, corpus_arm: str, *, shards: Sequence[str | Path],
                   chunk_exports: Sequence[str | Path], merged: Sequence[str | Path], out: Path,
                   created: str | None, plural: bool, config: GlossaryConfig,
                   provenance: Mapping[str, Any] | None) -> dict[str, Any]:
    if variant == "none" and merged:
        raise Refused("the none variant takes no merged glossary")
    docs = read_shard_documents(shards)
    export_rows = g.read_chunks(list(chunk_exports))
    for r in export_rows:
        d = docs.get(r.doc_id)
        if d is None:
            raise Refused("an export document is not in the shards (were these the shards submitted?)")
        if d.paper != paper_root(r.source_path, r.doc_id):
            raise Refused("a shard document's paper key differs from the export's source_path root")
    entries = read_merged(merged)
    merged_shas = [file_sha256(p) for p in merged]
    built = build(variant, corpus_arm, docs, entries, merged_shas, plural=plural)
    ident = identity_check(built.rows, export_rows)
    shard_shas = [file_sha256(p) for p in shards]
    prov = dict(provenance) if provenance is not None else g.experiment_provenance()
    producer = (f"scripts/eval/alias_glossary_file.py {variant} --corpus-arm {corpus_arm} "
                f"({WRITER}; commit {prov.get('commit')}, describe {prov.get('describe')}, dirty {prov.get('dirty')}; "
                f"merged sha256 {','.join(merged_shas) or '-'}; "
                f"{len(shards)} shard(s), sha256 list digest "
                f"{hashlib.sha256(''.join(s + chr(10) for s in shard_shas).encode()).hexdigest()})")
    counts = write_glossary_file(out, built.rows, producer=producer, config=config, created=created)
    os.chmod(out, 0o600)
    fields, stamps, verdicts = load_render(out, export_rows)
    cen = census(fields, export_rows, built.entries_by_paper, max_lines=config.max_entries_per_chunk)
    man: dict[str, Any] = {
        "schema": SCHEMA, "variant": variant, "corpus_arm": corpus_arm,
        "glossary_file": str(out), "glossary_file_sha256": file_sha256(out),
        "version": built.version, "stamp": stamp_version(built.version),
        "stamps_rendered": dict(sorted(Counter(stamps.values()).items())),
        "config": {k: getattr(config, k) for k in config.__dataclass_fields__},
        "file_counts": counts, "glossary": built.stats, "census": cen,
        "identity": {"export_check": ident, "load_resolution": verdicts},
        "rules": {"forms": "alias; plus alias+'s' for abbreviation/both, always (alias_abbrev.alias_tokens)",
                  "mention": "whole [\\w<bars>]+ token of the canonical chunk text, case-sensitive",
                  "line": "<form> — <defs joined '; '>, merged-file order, exact duplicates dropped",
                  "order": "forms by codepoint", "caps": "12 lines, 1200 chars incl. newlines; first overflow ends",
                  "text": "NFC per line"},
        "inputs": {"merged": [{"path": str(p), "sha256": s} for p, s in zip(merged, merged_shas, strict=True)],
                   "shards": [{"path": str(p), "sha256": s} for p, s in zip(shards, shard_shas, strict=True)],
                   "shards_count": len(shards),
                   "shards_digest": hashlib.sha256("".join(s + "\n" for s in shard_shas).encode()).hexdigest(),
                   "chunk_exports": [{"path": str(p), "sha256": file_sha256(p)} for p in chunk_exports]},
        "producer": producer, "provenance": prov, "citable": g.citable(prov),
    }
    if built.placebo_counts is not None:
        man["seed"] = built.placebo_counts.get("seed")
        man["counts"] = built.placebo_counts
    g.write_json_atomic(out.with_name(out.name + ".manifest.json"), man)
    os.chmod(out.with_name(out.name + ".manifest.json"), 0o600)
    return man


# ------------------------------------------------------------------ parity and reach (offline)

def spec_fields(export_rows: Sequence[g.ChunkRow], by_paper: Mapping[str, Sequence[Mapping[str, Any]]],
                **kw: Any) -> dict[str, str]:
    """:func:`spec_field` for every export chunk (its paper's entries; ``kw`` = the mutations)."""
    return {r.chunk_id: spec_field(by_paper.get(paper_root(r.source_path, r.doc_id), ()), r.content, **kw)
            for r in export_rows}


def compare(load: Mapping[str, str], spec: Mapping[str, str]) -> dict[str, int]:
    """Byte-for-byte comparison of two chunk id → field maps over the same chunks."""
    if set(load) != set(spec):
        raise Refused("parity: the two renderings cover different chunks")
    bad = sum(1 for k in load if load[k] != spec[k])
    return {"chunks": len(load), "mismatches": bad, "chunks_with_field_load": sum(1 for v in load.values() if v),
            "chunks_with_field_spec": sum(1 for v in spec.values() if v)}


def entries_by_paper_for(variant: str, corpus_arm: str, export_rows: Sequence[g.ChunkRow],
                         merged: Sequence[str | Path]) -> dict[str, list[dict[str, Any]]]:
    papers = {paper_root(r.source_path, r.doc_id) for r in export_rows}
    return entries_for_variant(variant, corpus_arm, read_merged(merged), papers)[0]


def balanced_records(recs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The balanced set (prereg §4.1): the records of papers whose accepted G cover >= 2 distinct
    alias keys (``glossbench_alias_checks.alias_key``) and that have >= 1 accepted C."""
    import glossbench_alias_checks as gac

    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in recs:
        by[str(r["paper"])].append(r)
    keep = [p for p, rs in by.items()
            if len({gac.alias_key(r["alias"], r.get("alias_kind")) for r in rs if r["kind"] == "G"}) >= 2
            and any(r["kind"] == "C" for r in rs)]
    return [r for p in keep for r in by[p]]


def renders_entry(field: str, alias: str, kind: str, primary: str) -> bool:
    """Does ``field`` hold a line for one of the entry's forms whose definitions include ``primary``?"""
    forms = aa.alias_tokens(alias, kind or "abbreviation")
    for line in field.split("\n") if field else ():
        form, _, defs = line.partition(" — ")
        if form in forms and unicodedata.normalize("NFC", primary) in defs.split("; "):
            return True
    return False


def reach(fields: Mapping[str, str], recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """§9.1's reach count, offline: G records whose gold chunk renders the record's own entry."""
    def count(rs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        gs = [r for r in rs if r["kind"] == "G"]
        hit = [r for r in gs if renders_entry(fields.get(r["gold_chunks"][0], ""), str(r["alias"]),
                                              str(r.get("alias_kind") or "abbreviation"), str(r["primary"]))]
        miss = Counter(str(r.get("alias_kind")) for r in gs if r not in hit)
        return {"G": len(gs), "renders_own_entry": len(hit), "misses_by_alias_kind": dict(sorted(miss.items()))}
    bal = balanced_records(recs)
    ids = "".join(x + "\n" for x in sorted(str(r["record_id"]) for r in bal))
    return {"all_accepted": count(recs), "balanced": count(bal),
            "balanced_ids_sha256": hashlib.sha256(ids.encode()).hexdigest(),
            "gate": "480 of the 600 balanced G records (prereg §9.1)"}


def cmd_parity(a: argparse.Namespace) -> int:
    rows = g.read_chunks(list(a.chunk_export))
    load, _, verdicts = load_render(a.glossary_file, rows)
    by_paper = entries_by_paper_for(a.variant, a.corpus_arm, rows, a.merged)
    out: dict[str, Any] = {"glossary_file_sha256": file_sha256(a.glossary_file), "identity": verdicts,
                           "parity": compare(load, spec_fields(rows, by_paper))}
    if a.records:
        with open(a.records, encoding="utf-8") as fh:
            out["reach"] = reach(load, [json.loads(x) for x in fh if x.strip()])
    print(json.dumps(out, indent=1))
    return 0 if out["parity"]["mismatches"] == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="write one glossary file and its manifest")
    w.add_argument("variant", choices=VARIANTS)
    w.add_argument("--corpus-arm", required=True, choices=CORPUS_ARMS)
    w.add_argument("--shard", action="append", default=[], help="a shard submitted to embed-bulk (repeat)")
    w.add_argument("--shard-list", type=Path, default=None,
                   help="a file naming one shard path per line (with or instead of --shard)")
    w.add_argument("--chunk-export", action="append", required=True, help="the corpus arm's chunk export(s)")
    w.add_argument("--merged", action="append", default=[], help="merged glossary file(s) (merged / placebo)")
    w.add_argument("--out", required=True, type=Path)
    w.add_argument("--created", default=None, help="the header's created time (default: now, UTC)")
    w.add_argument("--allow-dirty", action="store_true", help="development only: outputs are not citable")
    p = sub.add_parser("parity", help="load-path rendering vs the independent §5.3 renderer, and the reach count")
    p.add_argument("variant", choices=VARIANTS)
    p.add_argument("--corpus-arm", required=True, choices=CORPUS_ARMS)
    p.add_argument("--glossary-file", required=True, type=Path)
    p.add_argument("--chunk-export", action="append", required=True)
    p.add_argument("--merged", action="append", default=[])
    p.add_argument("--records", default=None, help="accepted-record file: also print §9.1's reach count")
    a = ap.parse_args(argv)
    if a.cmd == "parity":
        return cmd_parity(a)
    prov = g.experiment_provenance()
    if not g.citable(prov) and not a.allow_dirty:
        raise SystemExit("tree is dirty/untracked or commit unknown; commit first (or --allow-dirty)")
    shards = list(a.shard)
    if a.shard_list:
        shards += [ln.strip() for ln in a.shard_list.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not shards:
        raise SystemExit("name the shards submitted to embed-bulk (--shard / --shard-list)")
    man = write_variant(a.variant, a.corpus_arm, shards=shards, chunk_exports=a.chunk_export,
                        merged=a.merged, out=a.out, created=a.created, provenance=prov)
    print(json.dumps({k: man[k] for k in ("variant", "corpus_arm", "glossary_file_sha256", "version",
                                          "glossary", "census", "identity", "citable")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
