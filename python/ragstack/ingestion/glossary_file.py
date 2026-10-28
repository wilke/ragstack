"""The ``glossary.jsonl`` file: one document glossary per row, kept with the
chunks and attached at load (#847 G1).

Today the per-document symbol glossary (#754) is computed inside every ingest
path and rendered onto the chunks before they are embedded and written. #847
makes the glossary a recorded artifact instead: a file produced once over a
collection version's documents, kept beside ``chunks.jsonl.gz``, and rendered
onto the chunks by the LOAD step. This module is that file's versioned
contract — the format, a strict reader, a writer that writes only what the
reader accepts, and the load-side checks. Producers: ``scripts/glossary_file.py
build`` (G1), which converts what exists today (the rules, plus an optional
offline LLM cache) into one, and the workflow step ``scripts/glossary_step.py``
(G2), which runs the model itself.

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
reader's current DEFAULT for that field — so a file that omits a key renders
under whatever that default is when it is read. Writers write every key.
``created`` must be UTC (``Z`` or ``+00:00``).

**Every further line, one document**::

    {"kind": "document", "doc_id": "...",
     "text_sha256": "<64 hex>",
     "text_chars": <int>,
     "text_ws_sha256": "<64 hex>",
     "glossary_version": "<= merged.version>",
     "rules":  GLOSSARY,
     "llm":    GLOSSARY | null,
     "merged": GLOSSARY,
     "drops": {"<reason>": <count>, ...},
     "provenance": {...},
     "degraded": null | "<reason>"}

    GLOSSARY = {"version": "rules/3" | "llm/<n>:<model>:<hash>:w..x..:t..[:k..:r..]",
                "entries": [{"symbol": "<canonical key>",
                             "definitions": [{"text": "...", "offset": <int>}, ...]},
                            ...]}

* ``text_sha256`` is :func:`~ragstack.ingestion.symbol_glossary.content_key` of
  the document text the glossary was built on — sha256 of the UTF-8 text with
  outer whitespace stripped — the same key the LLM cache uses. A load refuses a
  row whose ``text_sha256`` does not match the text its chunks were cut from.
* ``text_chars`` and ``text_ws_sha256`` are the second, gap-tolerant identity
  (:func:`masked_key`): the text's length in code points (before
  ``content_key``'s strip) and the sha256 of the text with every character of
  :data:`MASK_CLASS` (Unicode White_Space, Default_Ignorable_Code_Point, the
  Cc and Cf controls, U+FFFD) REPLACED by a space — replaced, not removed, so
  the hash also pins where every other character sits. It verifies a document
  whose chunks do not tile its text: ``fixed_token`` under a WordPiece
  tokenizer leaves whitespace and normaliser-stripped characters at window
  boundaries in no chunk (see :func:`check_document`).
* ``rules`` / ``llm`` / ``merged`` serialise a :class:`Glossary` losslessly:
  entries sorted by symbol (strictly ascending, so unique), each symbol's
  definitions in document order (offsets non-decreasing; the positional
  rendering bisects on them), every offset a raw character offset into the
  document text — the coordinates of ``Chunk.start_char``. ``merged`` is what
  the load renders: :func:`choose_merged` — the LLM glossary when the row has
  one, else the rules (today's attacher choice; both builders use it).
* ``glossary_version`` repeats ``merged.version`` so a reader can filter rows
  without parsing the glossaries; a disagreement is refused.
* ``drops``: entries dropped, by reason. ``{}`` where unknown. G1's builder
  knows only the re-validation of a cached LLM glossary (``revalidation``);
  the G2 step records the gate's reasons
  (:data:`~ragstack.ingestion.symbol_glossary.GATE_REASONS`).
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
from a file rather than from an in-process attach. Whatever G2 makes ``merged``
(a union of the two halves), its version string must still BEGIN with ``llm/``
whenever it holds any LLM entry, or the backfill's ``llm/`` guards misfire.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
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
    "kind", "doc_id", "text_sha256", "text_chars", "text_ws_sha256", "glossary_version",
    "rules", "llm", "merged", "drops", "provenance", "degraded",
})
_SHA256 = re.compile(r"[0-9a-f]{64}")


#: The characters :func:`masked_key` replaces by a space. Fixed here, code point
#: by code point, rather than read from ``unicodedata`` at run time: the hash is
#: durable, and must not change with the Python build's Unicode version.
#:
#: * Unicode White_Space (PropList.txt): U+0009-000D, 0020, 0085, 00A0, 1680,
#:   2000-200A, 2028, 2029, 202F, 205F, 3000;
#: * Default_Ignorable_Code_Point (DerivedCoreProperties.txt, Unicode 15.1):
#:   U+00AD, 034F, 061C, 115F-1160, 17B4-17B5, 180B-180F, 200B-200F, 202A-202E,
#:   2060-206F, 3164, FE00-FE0F, FEFF, FFA0, FFF0-FFF8, 1BCA0-1BCA3,
#:   1D173-1D17A, E0000-E0FFF;
#: * the C0/C1 controls (Cc): U+0000-001F, 007F-009F;
#: * the format characters (Cf, Unicode 15.1) not already above: U+0600-0605,
#:   06DD, 070F, 0890-0891, 08E2, 110BD, 110CD, 13430-1343F, FFF9-FFFB;
#: * U+FFFD, the replacement character.
#:
#: These are what a tokenizer's normaliser drops or a pre-tokenizer splits on
#: (BERT's ``clean_text`` removes Cc/Cf and U+FFFD; whitespace falls between
#: tokens), so they are what a WordPiece offset mapping leaves uncovered. Bump
#: :data:`MASK_VERSION` (and the schema) on any change.
#:
#: Deliberately NOT masked: combining marks (Mn) and private-use characters
#: (Co). BGE/WordPiece leaves those uncovered too; at a document's edge or an
#: overlap-0 window boundary such a document is UNVERIFIABLE, so the override
#: applies, and SFR (byte-level, the default stack) verifies it exactly.
#: Masking them would make ``r̄`` equal ``r `` at an uncovered boundary.
MASK_CLASS = (
    "\u0000-\u0020\u007f-\u00a0\u00ad\u034f\u061c\u115f\u1160\u1680"
    "\u17b4\u17b5\u180b-\u180f\u2000-\u200f\u2028-\u202f\u205f-\u206f"
    "\u3000\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufffb\ufffd"
    "\u0600-\u0605\u06dd\u070f\u0890\u0891\u08e2"
    "\U000110bd\U000110cd\U00013430-\U0001343f"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
MASK_VERSION = "mask/1"
_MASK = re.compile(f"[{MASK_CLASS}]")


def masked_key(text: str, length: int | None = None) -> str:
    """sha256 of ``text`` with every :data:`MASK_CLASS` character replaced by a
    space, right-padded with spaces to ``length`` code points when given.
    Position-preserving: equal keys mean every other character is at the same
    offset. Encoded as UTF-8 with ``surrogatepass`` so a lone surrogate hashes
    instead of raising."""
    masked = _MASK.sub(" ", text)
    if length is not None and len(masked) < length:
        masked += " " * (length - len(masked))
    return hashlib.sha256(masked.encode("utf-8", "surrogatepass")).hexdigest()


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
    text_chars: int
    text_ws_sha256: str
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
        "text_chars": row.text_chars,
        "text_ws_sha256": row.text_ws_sha256,
        "glossary_version": row.glossary_version,
        "rules": glossary_to_obj(row.rules),
        "llm": glossary_to_obj(row.llm) if row.llm is not None else None,
        "merged": glossary_to_obj(row.merged),
        "drops": dict(row.drops),
        "provenance": dict(row.provenance),
        "degraded": row.degraded,
    }


def _dumps(obj: dict[str, Any]) -> str:
    out = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    try:
        out.encode("utf-8")
    except UnicodeEncodeError as e:  # a lone surrogate: not representable in UTF-8
        raise GlossaryFileError(f"not representable as UTF-8 (lone surrogate?): {e}") from e
    return out


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
    chars, ws = obj["text_chars"], obj["text_ws_sha256"]
    _need(_is_int(chars) and chars >= 0, where, "'text_chars' must be a non-negative integer")
    _need(isinstance(ws, str) and _SHA256.fullmatch(ws) is not None, where,
          "'text_ws_sha256' must be 64 lowercase hex characters")
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
    return GlossaryRow(doc_id, sha, chars, ws, rules, llm, merged, dict(drops),
                       dict(obj["provenance"]), degraded)


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
        parsed = datetime.fromisoformat(created) if isinstance(created, str) else None
    except ValueError:
        parsed = None
    _need(parsed is not None and parsed.utcoffset() == timedelta(0), where,
          f"'created' must be an ISO-8601 UTC time (Z or +00:00), got {created!r}")
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
                try:
                    line = _dumps(obj)
                except GlossaryFileError as e:
                    raise GlossaryFileError(f"{p}: row {n} ({row.doc_id!r}): {e}") from e
                parse_row(json.loads(line), f"{p}: row {n}")
                if row.doc_id in seen:
                    raise GlossaryFileError(f"{p}: row {n}: duplicate doc_id {row.doc_id!r}")
                seen.add(row.doc_id)
                for k, v in _counts([row]).items():
                    tally[k] += v
                body.write(line + "\n")
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
    return GlossaryRow(doc_id, key, len(text), masked_key(text), rules, llm,
                       choose_merged(rules, llm), drops, provenance, None)


def choose_merged(rules: Glossary, llm: Glossary | None) -> Glossary:
    """The glossary a row renders: the LLM glossary when there is one (even an
    empty one: the model found nothing the gate kept), else the rules — today's
    attacher choice (:meth:`SymbolGlossaryAttacher.glossary_for`), kept by
    owner decision for #847 G2. One function, so the builder and the workflow
    step cannot drift.

    The file keeps BOTH halves, so the choice can be re-decided later without
    re-running the model. TODO(#847): a union of the two
    (``symbol_glossary_llm.merged``) is a possible later change; if it lands,
    its version string must still START with ``llm/`` whenever it holds an LLM
    entry — the backfill's replace guards key on that prefix."""
    return llm if llm is not None else rules


# --- combining files -----------------------------------------------------------


def merge_files(paths: Iterable[str | Path], out: str | Path) -> dict[str, int]:
    """Concatenate glossary files (e.g. one per batch of a workflow run) into
    one, written atomically at ``out``. Every input is read strictly. Refused
    (:class:`GlossaryFileError`): no input, a ``doc_id`` in two inputs, inputs
    whose ``config`` differs (one file renders under one config).

    Deterministic: rows in input order, ``producer`` = ``merge(<the inputs'
    distinct producers, sorted>)``, ``created`` = the latest input's — so the
    same inputs give a byte-identical file (the archive's rule)."""
    files = [read_glossary_file(p) for p in paths]
    if not files:
        raise GlossaryFileError("merge: no input files")
    config = files[0].config
    for f in files[1:]:
        if f.config != config:
            raise GlossaryFileError(
                f"merge: {f.path} was built under a different config than {files[0].path} "
                f"({config_dict(f.config)} != {config_dict(config)})")
    seen: dict[str, Path] = {}
    for f in files:
        for doc_id in f.rows:
            if doc_id in seen:
                raise GlossaryFileError(
                    f"merge: doc_id {doc_id!r} is in both {seen[doc_id]} and {f.path}")
            seen[doc_id] = f.path
    producers = sorted({f.header["producer"] for f in files})
    created = max((f.header["created"] for f in files),
                  key=lambda c: datetime.fromisoformat(c))
    rows = (row for f in files for row in f.rows.values())
    return write_glossary_file(out, rows, producer=f"merge({', '.join(producers)})",
                               config=config, created=created)


# --- the load-side check and render -------------------------------------------

#: Why a document's row cannot be used. ``missing``: the file has no row for the
#: document. ``mismatch``: the text reassembled from the chunks is complete (no
#: gap, every chunk's length agrees with its offsets) and hashes differently —
#: the glossary was built on another text. ``unverifiable``: the reassembly is
#: incomplete (a gap, e.g. a chunk dropped by ``--boilerplate drop``, or a
#: length mismatch) and hashes differently, so whether the texts differ cannot
#: be told.
MISSING, MISMATCH, UNVERIFIABLE = "missing", "mismatch", "unverifiable"
#: Verified outcomes: ``verified`` — the exact ``text_sha256`` matched;
#: ``verified_ws`` — it did not, but the masked identity did (see
#: :func:`check_document`).
VERIFIED, VERIFIED_WS = "verified", "verified_ws"


@dataclass
class Resolution:
    """What :func:`resolve_documents` decided, per document."""

    #: doc_id -> the stamped glossary to render.
    glossaries: dict[str, Glossary] = field(default_factory=dict)
    #: (doc_id, kind) for every document the load must refuse on.
    problems: list[tuple[str, str]] = field(default_factory=list)
    #: doc_ids rendered although unverifiable (only with the override).
    unverified: list[str] = field(default_factory=list)
    #: How each rendered document was verified: VERIFIED / VERIFIED_WS counts.
    verified: dict[str, int] = field(default_factory=lambda: {VERIFIED: 0, VERIFIED_WS: 0})

    def counts(self) -> dict[str, int]:
        return {**self.verified, "unverified": len(self.unverified)}

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


def check_document(row: GlossaryRow | None, doc: Reassembled) -> str:
    """How one document's row checks against the text reassembled from its
    chunks: VERIFIED, VERIFIED_WS, or a problem kind.

    1. ``content_key(reassembled) == text_sha256``: VERIFIED. This exact check
       holds whenever the chunks TILE the text — every chunker whose spans cover
       it (recursive, sentence, word, semantic), and ``fixed_token`` only when
       its tokenizer's offset mapping covers every character (a byte-level
       tokenizer does; a WordPiece one does not).
    2. Otherwise, when no chunk reaches past ``text_chars`` and
       :func:`masked_key` of the reassembly (gaps are filled with spaces;
       padded with spaces to ``text_chars``) equals ``text_ws_sha256``:
       VERIFIED_WS. Every character outside :data:`MASK_CLASS` is then at the
       same offset in both, so the glossary's offsets are sound; only
       whitespace and ignorable characters the chunks never covered are
       unchecked. This is what verifies ``fixed_token`` under WordPiece: a
       window boundary on ``\n``/``\t``/NBSP, a leading BOM, a trailing ZWSP.
    3. Otherwise UNVERIFIABLE when the reassembly is incomplete — a gap, a
       length mismatch, or SHORTER than ``text_chars`` (a dropped last chunk,
       or a trailing character no token covers that the mask does not list,
       e.g. a private-use U+F0B7 bullet or a combining accent under
       WordPiece) — so the override may render it; else MISMATCH (always
       refused). A reassembly LONGER than ``text_chars`` is never verified by
       step 2 (the ``<=`` guard short-circuits it, and the length-preserving
       masked hash could not match anyway)."""
    if row is None:
        return MISSING
    if content_key(doc.text) == row.text_sha256:
        return VERIFIED
    if len(doc.text) <= row.text_chars and \
            masked_key(doc.text, row.text_chars) == row.text_ws_sha256:
        return VERIFIED_WS
    incomplete = doc.gaps or doc.length_mismatches or len(doc.text) < row.text_chars
    return UNVERIFIABLE if incomplete else MISMATCH


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
        elif kind in (VERIFIED, VERIFIED_WS):
            res.verified[kind] += 1
        else:
            res.problems.append((doc.doc_id, kind))
            continue
        assert row is not None
        res.glossaries[doc.doc_id] = row.stamped()
    return res


def resolve_from_documents(gfile: GlossaryFile, docs: Iterable[Any]) -> Resolution:
    """The DIRECT check, for a step that holds the documents themselves (the
    coupled ``ingest_shard --glossary-file``): ``content_key(doc.content) ==
    row.text_sha256`` or the document is a problem — ``missing`` (no row) or
    ``mismatch``. Stronger than the reassembly check: nothing is
    unverifiable. ``docs`` are :class:`~ragstack.models.Document` (``id`` +
    ``content``). Pure: decides, writes nothing."""
    res = Resolution()
    for doc in docs:
        row = gfile.rows.get(doc.id)
        if row is None:
            res.problems.append((doc.id, MISSING))
        elif content_key(doc.content) != row.text_sha256:
            res.problems.append((doc.id, MISMATCH))
        else:
            res.verified[VERIFIED] += 1
            res.glossaries[doc.id] = row.stamped()
    return res


#: The receipt / batch error label when ``ingest_shard --glossary-file`` refuses
#: a batch: the file has no row, or a mismatched row, for a loaded document.
#: That can only be a tool or wiring bug (the step reads the same shard), so it
#: is a batch-level error with its own exit code.
GLOSSARY_FILE_MISMATCH = "glossary_file_mismatch"
GLOSSARY_REFUSED_EXIT_CODE = 6


def is_glossary_refusal(error: str | None) -> bool:
    return bool(error) and str(error).startswith(GLOSSARY_FILE_MISMATCH)


def format_glossary_refusal(res: Resolution, limit: int = 5) -> str:
    by: dict[str, list[str]] = {}
    for doc_id, kind in res.problems:
        by.setdefault(kind, []).append(doc_id)
    parts = [f"{kind}={len(ids)} (e.g. {', '.join(repr(i) for i in ids[:limit])})"
             for kind, ids in sorted(by.items())]
    return f"{GLOSSARY_FILE_MISMATCH}: {'; '.join(parts)}"


class FileGlossaryAttacher:
    """Renders chunks from a glossary file — the attacher the coupled ingest
    (``ingest_shard --glossary-file``) hands its pipeline in place of a
    :class:`SymbolGlossaryAttacher`. Same ``apply`` / ``provenance`` surface,
    so ``IngestionPipeline._attach_glossary`` runs it after the boilerplate
    filter, before the embed — the embedding file and the archive carry the
    rendered field.

    The caller checked every document against the file first
    (:func:`resolve_from_documents`); ``apply`` checks the text identity again
    on the documents it renders, and raises on a difference (the pipeline then
    degrades the batch to no glossary and records it, never renders a
    glossary at the wrong offsets)."""

    def __init__(self, gfile: GlossaryFile) -> None:
        self.gfile = gfile
        self.config = gfile.config

    def apply(self, documents: Iterable[Any], chunks: Iterable[Chunk]) -> Any:
        from ragstack.ingestion.symbol_glossary import AttachResult

        result = AttachResult()
        by_doc: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_doc.setdefault(chunk.doc_id, []).append(chunk)
        glossaries: dict[str, Glossary] = {}
        for doc in documents:
            if doc.id not in by_doc:
                continue
            row = self.gfile.rows.get(doc.id)
            if row is None or content_key(doc.content) != row.text_sha256:
                raise GlossaryFileError(
                    f"document {doc.id!r}: no matching row in {self.gfile.path}")
            glossaries[doc.id] = row.stamped()
            if row.llm is None:
                result.docs_rules += 1
            else:
                result.docs_cached += 1
            result.versions.add(row.glossary_version)
            result.ambiguous_symbols += len(row.merged.ambiguous_symbols)
        rendered = [c for doc_id in glossaries for c in by_doc[doc_id]]
        result.chunks_with_glossary = render_chunks(rendered, glossaries, self.config)
        return result

    def provenance(self) -> dict[str, Any]:
        h = self.gfile.header
        return {"symbol_glossary": {
            "source": "glossary_file", "path": str(self.gfile.path),
            "sha256": file_sha256(self.gfile.path), "schema": h["schema"],
            "producer": h["producer"], "created": h["created"], "counts": dict(h["counts"]),
            "file_stamp": FILE_STAMP, "config": config_dict(self.config),
        }}


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_provenance(gfile: GlossaryFile, resolution: Resolution) -> dict[str, Any]:
    """What a load or backfill records about the file it rendered from."""
    return {
        "path": str(gfile.path),
        "sha256": file_sha256(gfile.path),
        "schema": gfile.header["schema"],
        "producer": gfile.header["producer"],
        "created": gfile.header["created"],
        "counts": dict(gfile.header["counts"]),
        "file_stamp": FILE_STAMP,
        "mask": MASK_VERSION,
        "documents": resolution.counts(),
    }


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
