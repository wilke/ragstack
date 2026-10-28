"""The ``glossary.jsonl`` file: one document glossary per row, kept with the
chunks and attached at load (#847 G1).

Today the per-document symbol glossary (#754) is computed inside every ingest
path and rendered onto the chunks before they are embedded and written. #847
makes the glossary a recorded artifact instead: a file produced once over a
collection version's documents, kept beside ``chunks.jsonl.gz``, and rendered
onto the chunks by the LOAD step. This module is that file's versioned
contract — the format, a strict reader, a writer that writes only what the
reader accepts, and the load-side checks. G2 adds the model step that produces
such files; G1's producer is ``scripts/glossary_file.py build``, which converts
what exists today (the rules, plus an optional offline LLM cache) into one.

Format — newline-delimited JSON, UTF-8, one object per line, keys sorted:

**Line 1, the header**::

    {"kind": "header", "schema": "ragstack.glossary_file/v1",
     "producer": "<free text: tool and version>",
     "created": "<ISO-8601 UTC, e.g. 2026-10-28T12:00:00Z>",
     "counts": {"documents": N, "with_llm": L, "degraded": D, "merged_entries": E},
     "config": {<GlossaryConfig fields>}}

``counts`` must equal what the rows hold (a truncated or spliced file is
refused). ``config`` is the :class:`~ragstack.ingestion.symbol_glossary.GlossaryConfig`
the producer extracted and validated under; the load renders with the SAME
config, so the file alone determines every chunk's field. A key the reader does
not know is refused (a newer config it cannot honour); a missing key takes the
default.

**Every further line, one document**::

    {"kind": "document", "doc_id": "...",
     "text_sha256": "<64 hex>",
     "glossary_version": "<= merged.version>",
     "rules":  GLOSSARY,
     "llm":    GLOSSARY | null,
     "merged": GLOSSARY,
     "drops": {"<reason>": <count>, ...},
     "provenance": {...},
     "degraded": null | "<reason>"}

    GLOSSARY = {"version": "rules/3" | "llm/1:<model>:<hash>:w..x..:t..",
                "entries": [{"symbol": "<canonical key>",
                             "definitions": [{"text": "...", "offset": <int>}, ...]},
                            ...]}

* ``text_sha256`` is :func:`~ragstack.ingestion.symbol_glossary.content_key` of
  the document text the glossary was built on — sha256 of the UTF-8 text with
  outer whitespace stripped — the same key the LLM cache uses. A load refuses a
  row whose ``text_sha256`` does not match the text its chunks were cut from.
* ``rules`` / ``llm`` / ``merged`` serialise a :class:`Glossary` losslessly:
  entries sorted by symbol (strictly ascending, so unique), each symbol's
  definitions in document order (offsets non-decreasing; the positional
  rendering bisects on them), every offset a raw character offset into the
  document text — the coordinates of ``Chunk.start_char``. ``merged`` is what
  the load renders. In G1 it is the glossary today's attacher would use: the
  re-validated LLM glossary when the cache holds the document, else the rules.
* ``glossary_version`` repeats ``merged.version`` so a reader can filter rows
  without parsing the glossaries; a disagreement is refused.
* ``drops``: entries dropped, by reason. ``{}`` where unknown (G1: only the
  re-validation of a cached LLM glossary is known, as ``revalidation``).
* ``provenance``: free-form, recorded and never interpreted by the reader.
* ``degraded``: ``null``, or why the document did not get its full glossary
  (G2: the endpoint failed and the rules stood in).

Symbols are stored as the canonical keys the extractors produce
(:func:`~ragstack.ingestion.symbol_glossary.canonical_symbol`). The reader does
not re-canonicalise them: a future canonicalisation change must not make old
files unreadable, and rendering compares the stored key with the chunk's
canonical text exactly as the in-memory glossary does.

**The version stamp.** A chunk rendered from a file gets
``metadata.symbol_glossary_version = "<glossary_version>@file/1"`` — e.g.
``rules/3@file/1`` or ``llm/1:<model>:<hash>:w80000x4:t2048@file/1``. The
suffix keeps the existing prefix semantics (``llm/…`` still means "an LLM
glossary", which the backfill's replace guard relies on) and says the field came
from a file rather than from an in-process attach.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ragstack.ingestion.reassembly import Reassembled
from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    VERSION_KEY,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    SymbolGlossaryAttacher,
    config_dict,
    content_key,
    extract_glossary,
    validate_entries,
)
from ragstack.models import Chunk

SCHEMA = "ragstack.glossary_file/v1"

#: Appended to ``glossary_version`` in ``metadata.symbol_glossary_version`` when a
#: chunk's field is rendered from a file. Bump with a change to how a file row
#: is rendered onto a chunk.
FILE_STAMP = "file/1"

_HEADER_KEYS = frozenset({"kind", "schema", "producer", "created", "counts", "config"})
_COUNT_KEYS = frozenset({"documents", "with_llm", "degraded", "merged_entries"})
_ROW_KEYS = frozenset({
    "kind", "doc_id", "text_sha256", "glossary_version", "rules", "llm", "merged",
    "drops", "provenance", "degraded",
})
_SHA256 = re.compile(r"[0-9a-f]{64}")


class GlossaryFileError(ValueError):
    """A malformed, inconsistent or unsupported glossary file — attributed to
    ``path:line``."""


def stamp_version(glossary_version: str) -> str:
    """The ``metadata.symbol_glossary_version`` of a chunk rendered from a file."""
    return f"{glossary_version}@{FILE_STAMP}"


@dataclass(frozen=True)
class GlossaryRow:
    """One document's row. ``merged`` is what the load renders."""

    doc_id: str
    text_sha256: str
    rules: Glossary
    llm: Glossary | None
    merged: Glossary
    drops: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    degraded: str | None = None

    @property
    def glossary_version(self) -> str:
        return self.merged.version

    def stamped(self) -> Glossary:
        """``merged`` re-labelled with the file stamp — what a chunk carries."""
        return Glossary(self.merged.entries, stamp_version(self.merged.version))


@dataclass
class GlossaryFile:
    """A file read by :func:`read_glossary_file`. ``rows`` may hold a subset of
    the file's documents (``doc_ids`` filter); ``header['counts']`` always
    describes the whole file, which was validated in full."""

    path: Path
    header: dict[str, Any]
    config: GlossaryConfig
    rows: dict[str, GlossaryRow]


# --- serialisation ------------------------------------------------------------


def glossary_to_obj(glossary: Glossary) -> dict[str, Any]:
    return {
        "version": glossary.version,
        "entries": [
            {"symbol": e.symbol,
             "definitions": [{"text": d.text, "offset": d.offset} for d in e.definitions]}
            for e in glossary.entries
        ],
    }


def row_to_obj(row: GlossaryRow) -> dict[str, Any]:
    return {
        "kind": "document",
        "doc_id": row.doc_id,
        "text_sha256": row.text_sha256,
        "glossary_version": row.glossary_version,
        "rules": glossary_to_obj(row.rules),
        "llm": glossary_to_obj(row.llm) if row.llm is not None else None,
        "merged": glossary_to_obj(row.merged),
        "drops": dict(row.drops),
        "provenance": dict(row.provenance),
        "degraded": row.degraded,
    }


def _dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# --- validation ---------------------------------------------------------------


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _need(cond: bool, where: str, msg: str) -> None:
    if not cond:
        raise GlossaryFileError(f"{where}: {msg}")


def _parse_glossary(obj: Any, where: str) -> Glossary:
    _need(isinstance(obj, dict) and set(obj) == {"version", "entries"}, where,
          "a glossary is an object with exactly 'version' and 'entries'")
    version, entries = obj["version"], obj["entries"]
    _need(isinstance(version, str) and bool(version), where, "'version' must be a non-empty string")
    _need(isinstance(entries, list), where, "'entries' must be a list")
    out: list[GlossaryEntry] = []
    prev: str | None = None
    for i, e in enumerate(entries):
        at = f"{where}.entries[{i}]"
        _need(isinstance(e, dict) and set(e) == {"symbol", "definitions"}, at,
              "an entry is an object with exactly 'symbol' and 'definitions'")
        sym, defs = e["symbol"], e["definitions"]
        _need(isinstance(sym, str) and bool(sym), at, "'symbol' must be a non-empty string")
        _need(prev is None or sym > prev, at,
              f"symbols must be strictly ascending (sorted, unique); {sym!r} after {prev!r}")
        prev = sym
        _need(isinstance(defs, list) and bool(defs), at, "'definitions' must be a non-empty list")
        parsed: list[Definition] = []
        last_offset = -1
        for j, d in enumerate(defs):
            dat = f"{at}.definitions[{j}]"
            _need(isinstance(d, dict) and set(d) == {"text", "offset"}, dat,
                  "a definition is an object with exactly 'text' and 'offset'")
            _need(isinstance(d["text"], str) and bool(d["text"]), dat,
                  "'text' must be a non-empty string")
            _need(_is_int(d["offset"]) and d["offset"] >= 0, dat,
                  "'offset' must be a non-negative integer")
            _need(d["offset"] >= last_offset, dat,
                  "definitions must be in document order (offsets non-decreasing)")
            last_offset = d["offset"]
            parsed.append(Definition(d["text"], d["offset"]))
        out.append(GlossaryEntry(sym, tuple(parsed)))
    return Glossary(tuple(out), version)


def parse_row(obj: Any, where: str) -> GlossaryRow:
    """Validate one document row (already JSON-decoded)."""
    _need(isinstance(obj, dict), where, "a row must be a JSON object")
    _need(obj.get("kind") == "document", where, f"expected kind 'document', got {obj.get('kind')!r}")
    keys = set(obj)
    _need(keys == _ROW_KEYS, where,
          f"row keys must be exactly {sorted(_ROW_KEYS)}; "
          f"missing {sorted(_ROW_KEYS - keys)}, unknown {sorted(keys - _ROW_KEYS)}")
    doc_id = obj["doc_id"]
    _need(isinstance(doc_id, str) and bool(doc_id), where, "'doc_id' must be a non-empty string")
    sha = obj["text_sha256"]
    _need(isinstance(sha, str) and _SHA256.fullmatch(sha) is not None, where,
          "'text_sha256' must be 64 lowercase hex characters")
    rules = _parse_glossary(obj["rules"], f"{where}.rules")
    llm = None if obj["llm"] is None else _parse_glossary(obj["llm"], f"{where}.llm")
    merged = _parse_glossary(obj["merged"], f"{where}.merged")
    _need(obj["glossary_version"] == merged.version, where,
          f"'glossary_version' {obj['glossary_version']!r} != merged.version {merged.version!r}")
    drops = obj["drops"]
    _need(isinstance(drops, dict) and all(isinstance(k, str) and _is_int(v) and v >= 0
                                          for k, v in drops.items()), where,
          "'drops' must map reasons to non-negative integers")
    _need(isinstance(obj["provenance"], dict), where, "'provenance' must be an object")
    degraded = obj["degraded"]
    _need(degraded is None or (isinstance(degraded, str) and bool(degraded)), where,
          "'degraded' must be null or a non-empty reason")
    return GlossaryRow(doc_id, sha, rules, llm, merged, dict(drops), dict(obj["provenance"]),
                       degraded)


def config_from_header(obj: Any, where: str) -> GlossaryConfig:
    """The header's ``config``, strictly: unknown keys and wrongly typed values
    are refused (unlike :func:`config_from_json`, which degrades to defaults —
    a file is a record, and rendering it under a config it does not state would
    not reproduce it)."""
    _need(isinstance(obj, dict), where, "'config' must be an object")
    types = {f.name: f.type for f in fields(GlossaryConfig)}
    unknown = sorted(set(obj) - set(types))
    _need(not unknown, where, f"unknown config key(s) {unknown}")
    for k, v in obj.items():
        ok = isinstance(v, bool) if types[k] == "bool" else (_is_int(v) and v >= 0)
        _need(ok, where, f"config {k!r}: {v!r} is not a valid {types[k]}")
    return GlossaryConfig(**obj)


def _parse_header(obj: Any, where: str) -> tuple[dict[str, Any], GlossaryConfig]:
    _need(isinstance(obj, dict), where, "the header must be a JSON object")
    _need(obj.get("schema") == SCHEMA, where,
          f"unknown schema {obj.get('schema')!r} (this reader knows {SCHEMA!r})")
    _need(obj.get("kind") == "header", where, f"expected kind 'header', got {obj.get('kind')!r}")
    keys = set(obj)
    _need(keys == _HEADER_KEYS, where,
          f"header keys must be exactly {sorted(_HEADER_KEYS)}; "
          f"missing {sorted(_HEADER_KEYS - keys)}, unknown {sorted(keys - _HEADER_KEYS)}")
    _need(isinstance(obj["producer"], str) and bool(obj["producer"]), where,
          "'producer' must be a non-empty string")
    created = obj["created"]
    try:
        ok = isinstance(created, str) and datetime.fromisoformat(created) is not None
    except ValueError:
        ok = False
    _need(ok, where, f"'created' must be an ISO-8601 time, got {created!r}")
    counts = obj["counts"]
    _need(isinstance(counts, dict) and set(counts) == _COUNT_KEYS
          and all(_is_int(v) and v >= 0 for v in counts.values()), where,
          f"'counts' must hold exactly {sorted(_COUNT_KEYS)} as non-negative integers")
    return obj, config_from_header(obj["config"], f"{where}.config")


def _counts(rows: Iterable[GlossaryRow]) -> dict[str, int]:
    c = dict.fromkeys(_COUNT_KEYS, 0)
    for r in rows:
        c["documents"] += 1
        c["with_llm"] += int(r.llm is not None)
        c["degraded"] += int(r.degraded is not None)
        c["merged_entries"] += len(r.merged.entries)
    return c


# --- reader and writer --------------------------------------------------------


def read_glossary_file(path: str | Path, *, doc_ids: Iterable[str] | None = None) -> GlossaryFile:
    """Read and validate EVERY line of ``path``; keep the rows of ``doc_ids``
    (all rows when ``None``). Raises :class:`GlossaryFileError` on an unknown
    schema version, a malformed header or row, a duplicate ``doc_id``, or
    header counts that disagree with the rows."""
    p = Path(path)
    keep = None if doc_ids is None else set(doc_ids)
    header: dict[str, Any] | None = None
    config = DEFAULT_CONFIG
    rows: dict[str, GlossaryRow] = {}
    seen: set[str] = set()
    tally = dict.fromkeys(_COUNT_KEYS, 0)
    with p.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            where = f"{p}:{lineno}"
            if not line.strip():
                raise GlossaryFileError(f"{where}: blank line")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise GlossaryFileError(f"{where}: bad JSON: {e}") from e
            if lineno == 1:
                header, config = _parse_header(obj, where)
                continue
            row = parse_row(obj, where)
            if row.doc_id in seen:
                raise GlossaryFileError(f"{where}: duplicate doc_id {row.doc_id!r}")
            seen.add(row.doc_id)
            for k, v in _counts([row]).items():
                tally[k] += v
            if keep is None or row.doc_id in keep:
                rows[row.doc_id] = row
    if header is None:
        raise GlossaryFileError(f"{p}: empty file (no header)")
    if tally != header["counts"]:
        raise GlossaryFileError(
            f"{p}: header counts {header['counts']} != rows {tally} "
            "(a truncated or spliced file)")
    return GlossaryFile(p, header, config, rows)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_glossary_file(
    path: str | Path,
    rows: Iterable[GlossaryRow],
    *,
    producer: str,
    config: GlossaryConfig = DEFAULT_CONFIG,
    created: str | None = None,
) -> dict[str, int]:
    """Write ``rows`` (streamed) to ``path`` atomically and return the counts.

    Every row is serialised and then validated by the reader's own
    :func:`parse_row`, so the writer cannot produce a row the reader refuses;
    a duplicate ``doc_id`` is refused. The body goes to a temporary file in the
    target directory first (the header's counts are known only at the end),
    then header + body are written to a second temporary and renamed over
    ``path`` — a crash leaves no half-written file under the final name."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    created = created or utc_now()
    seen: set[str] = set()
    tally = dict.fromkeys(_COUNT_KEYS, 0)
    body_fd, body_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".body", dir=p.parent)
    out_name = ""
    try:
        with os.fdopen(body_fd, "w", encoding="utf-8") as body:
            for n, row in enumerate(rows, start=1):
                obj = row_to_obj(row)
                parse_row(json.loads(_dumps(obj)), f"{p}: row {n}")
                if row.doc_id in seen:
                    raise GlossaryFileError(f"{p}: row {n}: duplicate doc_id {row.doc_id!r}")
                seen.add(row.doc_id)
                for k, v in _counts([row]).items():
                    tally[k] += v
                body.write(_dumps(obj) + "\n")
        header = {"kind": "header", "schema": SCHEMA, "producer": producer, "created": created,
                  "counts": tally, "config": config_dict(config)}
        _parse_header(json.loads(_dumps(header)), f"{p}: header")
        out_fd, out_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
        with os.fdopen(out_fd, "w", encoding="utf-8") as out, \
                open(body_name, encoding="utf-8") as src:
            out.write(_dumps(header) + "\n")
            for line in src:
                out.write(line)
        os.replace(out_name, p)
        out_name = ""
    finally:
        for name in (body_name, out_name):
            if name:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass
    return tally


# --- building a row from what exists today ------------------------------------


def build_row(doc_id: str, text: str, attacher: SymbolGlossaryAttacher) -> GlossaryRow:
    """One document's row from today's machinery: the rules glossary always,
    the cached LLM glossary when ``attacher.cache`` holds the text (re-validated
    against it, exactly as :meth:`SymbolGlossaryAttacher.glossary_for` does),
    and ``merged`` = the glossary that method would return — so a file built
    here renders exactly what today's in-process attach renders."""
    cfg = attacher.config
    key = content_key(text)
    rules = extract_glossary(text, cfg)
    llm: Glossary | None = None
    drops: dict[str, int] = {}
    provenance: dict[str, Any] = {"source": "rules", "config": "header"}
    hit = attacher.cache.get(key, attacher.cache_version) if attacher.cache is not None else None
    if attacher.cache is not None and hit is not None:
        version, entries = hit
        raw = [(s, d, o) for s, defs in entries for d, o in defs]
        llm, rejected = validate_entries(raw, text, version, cfg)
        if rejected:
            drops["revalidation"] = rejected
        provenance = {"source": "llm_cache", "config": "header",
                      "llm_cache_meta": attacher.cache.meta(key, version)}
    return GlossaryRow(doc_id, key, rules, llm, llm if llm is not None else rules,
                       drops, provenance, None)


# --- the load-side check and render -------------------------------------------

#: Why a document's row cannot be used. ``missing``: the file has no row for the
#: document. ``mismatch``: the text reassembled from the chunks is complete (no
#: gap, every chunk's length agrees with its offsets) and hashes differently —
#: the glossary was built on another text. ``unverifiable``: the reassembly is
#: incomplete (a gap, e.g. a chunk dropped by ``--boilerplate drop``, or a
#: length mismatch) and hashes differently, so whether the texts differ cannot
#: be told.
MISSING, MISMATCH, UNVERIFIABLE = "missing", "mismatch", "unverifiable"


@dataclass
class Resolution:
    """What :func:`resolve_documents` decided, per document."""

    #: doc_id -> the stamped glossary to render.
    glossaries: dict[str, Glossary] = field(default_factory=dict)
    #: (doc_id, kind) for every document the load must refuse on.
    problems: list[tuple[str, str]] = field(default_factory=list)
    #: doc_ids rendered although unverifiable (only with the override).
    unverified: list[str] = field(default_factory=list)

    def refusal(self, source: str, limit: int = 5) -> str:
        by: dict[str, list[str]] = {}
        for doc_id, kind in self.problems:
            by.setdefault(kind, []).append(doc_id)
        parts = [f"{kind}: {len(ids)} (e.g. {', '.join(repr(i) for i in ids[:limit])})"
                 for kind, ids in sorted(by.items())]
        return (f"refusing: glossary file {source} does not match the documents being "
                f"loaded — {'; '.join(parts)}. Nothing was written. A 'mismatch' row was "
                "built on a different text than the chunks were cut from; a 'missing' "
                "document has no row (the file was built over another document set); "
                "an 'unverifiable' document's chunks do not reassemble its full text "
                "(pass --glossary-allow-unverifiable to render those anyway).")


def check_document(row: GlossaryRow | None, doc: Reassembled) -> str | None:
    """The problem kind for one document, or ``None`` when its row verifies."""
    if row is None:
        return MISSING
    if content_key(doc.text) == row.text_sha256:
        return None
    return UNVERIFIABLE if (doc.gaps or doc.length_mismatches) else MISMATCH


def resolve_documents(
    gfile: GlossaryFile, docs: Iterable[Reassembled], *, allow_unverifiable: bool = False
) -> Resolution:
    """Check every document against the file. Pure: decides, writes nothing.

    The absent-document rule: a document with no row is a ``missing`` problem,
    never a silent rules fallback (see ``docs/plans/symbol-glossary-file.md``)."""
    res = Resolution()
    for doc in docs:
        row = gfile.rows.get(doc.doc_id)
        kind = check_document(row, doc)
        if kind == UNVERIFIABLE and allow_unverifiable:
            res.unverified.append(doc.doc_id)
            kind = None
        if kind is not None:
            res.problems.append((doc.doc_id, kind))
            continue
        assert row is not None
        res.glossaries[doc.doc_id] = row.stamped()
    return res


def render_chunks(
    chunks: Iterable[Chunk], glossaries: Mapping[str, Glossary], config: GlossaryConfig
) -> int:
    """Set each chunk's ``symbol_glossary`` and ``metadata.symbol_glossary_version``
    from its document's (stamped) glossary, exactly as
    :meth:`SymbolGlossaryAttacher.apply` renders. Returns the number of chunks
    that carry a non-empty field. A chunk whose document has no glossary here
    is an internal error (the caller resolved every document first)."""
    n = 0
    for chunk in chunks:
        glossary = glossaries.get(chunk.doc_id)
        if glossary is None:
            raise KeyError(f"no resolved glossary for document {chunk.doc_id!r}")
        chunk.symbol_glossary = glossary.for_chunk(chunk.content, config,
                                                   start_char=chunk.start_char)
        chunk.metadata[VERSION_KEY] = glossary.version
        n += int(bool(chunk.symbol_glossary))
    return n
