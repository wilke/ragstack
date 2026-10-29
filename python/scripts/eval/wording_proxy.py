#!/usr/bin/env python
"""The wording proxy (glossary benchmark round 2, prereg §5 M2): does a glossary
arm's rendered field on a record's gold chunk carry the words the paper uses
for the quantity asked?

Reimplemented from the written spec (no external code was consulted):

* **Lines.** The rendered ``symbol_glossary`` field is one line per symbol,
  joined by ``"\\n"``, each ``"<symbol> — <definition>"`` — U+2014 EM DASH with
  one space on each side, exactly as
  :meth:`ragstack.ingestion.symbol_glossary.Glossary.for_chunk` writes it
  (``f"{entry.symbol} — {defs}"``, several definitions of one symbol joined by
  ``"; "``). A line is split at the FIRST ``" — "``: the symbol is left, the
  definition is everything to the right (a definition may itself contain the
  dash; a symbol cannot). A line without the separator is skipped and counted
  (``malformed_lines``) — the renderer never writes one.
* **Normalisation.** Symbol: NFKC, casefold, all whitespace removed.
  Definition: NFKC, casefold, whitespace runs collapsed to one space.
* **Words.** Tokens are the maximal ``[a-z0-9]+`` runs of the normalised text
  of length > 1 (so a Greek letter, a sub/superscript or punctuation is not a
  word). Each token is stemmed by stripping the FIRST suffix, in the order
  ``ations, ation, ated, ions, ion, ing, ed, es, s`` (longest first), that the
  token ends with AND that leaves at least 4 characters; a token no suffix
  satisfies is kept as is. So ``rates`` -> ``rate`` (``es`` would leave 3, ``s``
  leaves 4), ``ventilation`` -> ``ventil``, ``was`` -> ``was``.
* **Binding terms** (1-3 short noun phrases per record, naming the quantity as
  the paper words it) are tokenised the same way. A term with no word (e.g.
  only a symbol) can never pass — the empty set would otherwise be contained
  in everything — and is counted (``empty_terms``).
* **Pass.** A (record, arm) pair passes if ANY entry of ANY gold chunk's field
  has a definition whose word SET contains every word of at least ONE binding
  term. Set containment: not substring (``rate`` is not in ``separated``), not
  order (``ventilation rate`` is in ``rate of ventilation``).

Inputs (all private; nothing is fetched):

* ``--records`` JSONL, one record per line: ``record_id``, ``gold_chunks``
  (chunk ids), ``binding_terms`` (strings), ``quantity`` (the quantity asked,
  in words: the per-field grouping), optional ``stratum``.
* per arm, either ``--fields NAME=PATH`` — JSONL rows ``{"chunk_id",
  "symbol_glossary"}`` of every chunk of the index (an export of the rendered
  fields) — or ``--glossary-file NAME=PATH`` with ``--chunks PATH`` (JSONL
  rows ``chunk_id, doc_id, content, start_char, end_char`` of every chunk of
  the index): each document is reassembled from its chunks, checked against
  the file (:func:`~ragstack.ingestion.glossary_file.resolve_documents`; a
  mismatch, a missing row or, without ``--glossary-allow-unverifiable``, an
  unverifiable document refuses the run), and every chunk is rendered with the
  file's own config, as the backfill renders.
* ``--shuffle-rendered NAME=SEED``: arm NAME's fields permuted among the chunks
  that carry one (:mod:`ragstack.ingestion.glossary_shuffle`) before scoring —
  the chance level. It needs the whole index's chunks, which both sources give.

Output (``--out``, refused inside the repository): per record and arm the pass
and the matching (chunk, symbol, term); per arm the totals, per quantity and
per stratum; the rules above; ``experiment_provenance()``, the sha256 of every
input and each glossary file's provenance and shuffle record.

Exit codes: 0 done; 2 bad input (an unknown gold chunk, a malformed record,
a glossary file refused).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "wording-proxy/1"
#: The renderer's separator: U+2014 EM DASH with a space each side.
SEPARATOR = " — "
SUFFIXES = ("ations", "ation", "ated", "ions", "ion", "ing", "ed", "es", "s")
MIN_STEM = 4
_TOKEN = re.compile(r"[a-z0-9]+")
EXIT_OK, EXIT_INPUT = 0, 2

RULES = {
    "line_split": "first ' — ' (U+2014 with a space each side); symbol left, definition right",
    "symbol_norm": "NFKC, casefold, all whitespace removed",
    "definition_norm": "NFKC, casefold, whitespace collapsed",
    "tokens": "[a-z0-9]+ runs of length > 1",
    "stemming": "strip the first of " + ",".join(SUFFIXES)
                + f" that the token ends with and that leaves >= {MIN_STEM} characters",
    "pass": "some entry of some gold chunk has a definition word set containing every word of "
            "at least one binding term (set containment; a term with no word never passes)",
}


class ProxyInputError(ValueError):
    pass


# --- the measure --------------------------------------------------------------


def norm_symbol(s: str) -> str:
    return "".join(unicodedata.normalize("NFKC", s).casefold().split())


def norm_definition(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def stem(token: str) -> str:
    for suf in SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= MIN_STEM:
            return token[: -len(suf)]
    return token


def words(text: str) -> frozenset[str]:
    """The stemmed word set of ``text`` (normalised as a definition)."""
    return frozenset(stem(t) for t in _TOKEN.findall(norm_definition(text)) if len(t) > 1)


@dataclass(frozen=True)
class Entry:
    symbol: str
    definition: str
    words: frozenset[str]


def parse_field(field: str) -> tuple[list[Entry], int]:
    """The entries of one rendered field, and how many lines had no separator."""
    entries: list[Entry] = []
    malformed = 0
    for line in (field or "").split("\n"):
        if not line.strip():
            continue
        sym, sep, definition = line.partition(SEPARATOR)
        if not sep:
            malformed += 1
            continue
        d = norm_definition(definition)
        entries.append(Entry(norm_symbol(sym), d, words(d)))
    return entries, malformed


def judge(fields: Iterable[tuple[str, str]], terms: Iterable[str]) -> dict[str, Any]:
    """One (record, arm): ``fields`` = (gold chunk id, rendered field) pairs."""
    term_sets = [(t, words(t)) for t in terms]
    usable = [(t, w) for t, w in term_sets if w]
    malformed = 0
    nonempty = 0
    for cid, field in fields:
        entries, bad = parse_field(field)
        malformed += bad
        nonempty += int(bool(entries))
        for e in entries:
            for t, w in usable:
                if w <= e.words:
                    return {"pass": True, "match": {"chunk_id": cid, "symbol": e.symbol, "term": t},
                            "gold_fields_nonempty": nonempty, "malformed_lines": malformed,
                            "empty_terms": len(term_sets) - len(usable)}
    return {"pass": False, "match": None, "gold_fields_nonempty": nonempty,
            "malformed_lines": malformed, "empty_terms": len(term_sets) - len(usable)}


def score(records: list[dict[str, Any]], arms: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """Per record, per arm, the judgement; per arm the totals, by quantity and by stratum."""
    per_record: dict[str, dict[str, Any]] = {}
    totals: dict[str, Any] = {}
    by_quantity: dict[str, dict[str, Counter[str]]] = {}
    by_stratum: dict[str, dict[str, Counter[str]]] = {}
    for arm, fields in arms.items():
        tot: Counter[str] = Counter()
        bq: dict[str, Counter[str]] = {}
        bs: dict[str, Counter[str]] = {}
        for r in records:
            j = judge([(c, fields.get(c, "")) for c in r["gold_chunks"]], r["binding_terms"])
            per_record.setdefault(r["record_id"], {})[arm] = j
            for c in (tot, bq.setdefault(norm_definition(r["quantity"]), Counter()),
                      bs.setdefault(str(r.get("stratum") or "-"), Counter())):
                c["n"] += 1
                c["pass"] += int(j["pass"])
                c["records_with_field"] += int(j["gold_fields_nonempty"] > 0)
            tot["malformed_lines"] += j["malformed_lines"]
            tot["records_with_empty_terms"] += int(j["empty_terms"] > 0)
            tot["records_without_usable_term"] += int(j["empty_terms"] == len(r["binding_terms"]))
        totals[arm] = {**dict(sorted(tot.items())),
                       "rate": round(tot["pass"] / tot["n"], 4) if tot["n"] else None}
        by_quantity[arm] = {k: dict(sorted(v.items())) for k, v in sorted(bq.items())}
        by_stratum[arm] = {k: dict(sorted(v.items())) for k, v in sorted(bs.items())}
    return {"per_record": per_record, "totals": totals, "by_quantity": by_quantity,
            "by_stratum": by_stratum}


# --- inputs -----------------------------------------------------------------------


def _jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ProxyInputError(f"{path}:{n}: bad JSON: {e}") from e
    return out


def read_records(path: Path) -> list[dict[str, Any]]:
    recs = _jsonl(path)
    seen: set[str] = set()
    for n, r in enumerate(recs, start=1):
        ok = (isinstance(r.get("record_id"), str) and r["record_id"]
              and isinstance(r.get("gold_chunks"), list) and r["gold_chunks"]
              and all(isinstance(c, str) and c for c in r["gold_chunks"])
              and isinstance(r.get("binding_terms"), list) and r["binding_terms"]
              and all(isinstance(t, str) for t in r["binding_terms"])
              and isinstance(r.get("quantity"), str))
        if not ok:
            raise ProxyInputError(
                f"{path}: record {n}: needs record_id, gold_chunks (non-empty list of ids), "
                "binding_terms (non-empty list of strings), quantity (string)")
        if r["record_id"] in seen:
            raise ProxyInputError(f"{path}: duplicate record_id {r['record_id']!r}")
        seen.add(r["record_id"])
    return recs


def fields_from_export(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, row in enumerate(_jsonl(path), start=1):
        cid = row.get("chunk_id")
        if not isinstance(cid, str) or not cid or cid in out:
            raise ProxyInputError(f"{path}: row {n}: missing or duplicate chunk_id")
        out[cid] = str(row.get("symbol_glossary") or "")
    return out


def fields_from_glossary_file(gpath: Path, chunks: list[dict[str, Any]],
                              allow_unverifiable: bool) -> tuple[dict[str, str], dict[str, Any]]:
    """Every chunk rendered from ``gpath`` after the document check (the backfill's rendering)."""
    from ragstack.ingestion.glossary_file import (
        GlossaryFileError,
        file_provenance,
        read_glossary_file,
        resolve_documents,
    )
    from ragstack.ingestion.reassembly import reassemble

    by_doc: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        by_doc.setdefault(str(c["doc_id"]), []).append(c)
    try:
        gfile = read_glossary_file(gpath, doc_ids=by_doc)
    except (GlossaryFileError, OSError) as e:
        raise ProxyInputError(f"glossary file {gpath}: {e}") from e
    res = resolve_documents(gfile, [reassemble(d, rows) for d, rows in sorted(by_doc.items())],
                            allow_unverifiable=allow_unverifiable)
    if res.problems:
        raise ProxyInputError(res.refusal(repr(str(gpath))))
    out: dict[str, str] = {}
    for d, rows in by_doc.items():
        g = res.glossaries[d]
        for c in rows:
            out[str(c["chunk_id"])] = g.for_chunk(str(c.get("content") or ""), gfile.config,
                                                  start_char=int(c.get("start_char") or 0))
    return out, file_provenance(gfile, res)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pairs(values: list[str] | None, flag: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for v in values or []:
        name, sep, rest = v.partition("=")
        if not sep or not name or not rest or any(n == name for n, _ in out):
            raise ProxyInputError(f"{flag} wants NAME=VALUE with unique names, got {v!r}")
        out.append((name, rest))
    return out


def run(args: argparse.Namespace) -> dict[str, Any]:
    from ragstack.ingestion.glossary_shuffle import ShuffleError, shuffle_rendered

    records = read_records(Path(args.records))
    sources = [(n, "fields", Path(p)) for n, p in _pairs(args.fields, "--fields")] + \
              [(n, "glossary_file", Path(p)) for n, p in _pairs(args.glossary_file, "--glossary-file")]
    names = [n for n, _, _ in sources]
    if not sources or len(set(names)) != len(names):
        raise ProxyInputError("give at least one arm (--fields / --glossary-file), names unique")
    chunks: list[dict[str, Any]] | None = None
    if any(kind == "glossary_file" for _, kind, _ in sources):
        if not args.chunks:
            raise ProxyInputError("--glossary-file needs --chunks (every chunk of the index)")
        chunks = _jsonl(Path(args.chunks))
    shuffles = dict(_pairs(args.shuffle_rendered, "--shuffle-rendered"))
    unknown = sorted(set(shuffles) - set(names))
    if unknown:
        raise ProxyInputError(f"--shuffle-rendered names unknown arms {unknown}")
    arms: dict[str, dict[str, str]] = {}
    arm_records: dict[str, dict[str, Any]] = {}
    for name, kind, path in sources:
        if kind == "fields":
            fields = fields_from_export(path)
            rec: dict[str, Any] = {"source": "fields", "path": str(path), "sha256": _sha(path)}
        else:
            assert chunks is not None
            fields, prov = fields_from_glossary_file(path, chunks, args.glossary_allow_unverifiable)
            rec = {"source": "glossary_file", **prov}
        missing = sorted({c for r in records for c in r["gold_chunks"]} - set(fields))
        if missing:
            raise ProxyInputError(f"arm {name}: {len(missing)} gold chunk(s) not in its chunks "
                                  f"(e.g. {missing[:5]})")
        rec["chunks"] = len(fields)
        rec["chunks_with_field"] = sum(1 for v in fields.values() if v)
        if name in shuffles:
            try:
                res = shuffle_rendered(fields, shuffles[name])
            except ShuffleError as e:
                raise ProxyInputError(f"--shuffle-rendered {name}: {e}") from e
            fields = {k: res.assignment.get(k, "") for k in fields}
            rec["shuffle"] = res.record()
        arms[name] = fields
        arm_records[name] = rec
    from ragstack.provenance import experiment_provenance

    prov = experiment_provenance(repo=str(Path(__file__).resolve().parents[3]))
    inputs = {"records": {"path": str(args.records), "sha256": _sha(Path(args.records)),
                          "n": len(records)}}
    if args.chunks:
        inputs["chunks"] = {"path": str(args.chunks), "sha256": _sha(Path(args.chunks))}
    return {"schema": SCHEMA, "rules": RULES, "arms": arm_records, "inputs": inputs,
            "provenance": prov,
            "citable": bool(prov.get("commit")) and prov.get("dirty") is False
            and prov.get("untracked") == 0,
            **score(records, arms)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--records", required=True, help="records JSONL (private)")
    p.add_argument("--fields", action="append", metavar="NAME=PATH",
                   help="an arm from an export of rendered fields {chunk_id, symbol_glossary}")
    p.add_argument("--glossary-file", action="append", metavar="NAME=PATH",
                   help="an arm rendered from a glossary.jsonl over --chunks")
    p.add_argument("--chunks", default=None,
                   help="every chunk of the index: {chunk_id, doc_id, content, start_char, end_char}")
    p.add_argument("--glossary-allow-unverifiable", action="store_true")
    p.add_argument("--shuffle-rendered", action="append", metavar="NAME=SEED",
                   help="score arm NAME with its fields permuted among the populated chunks")
    p.add_argument("--out", required=True, help="results JSON (refused inside the repository)")
    args = p.parse_args(argv)
    out = Path(args.out).resolve()
    repo = Path(__file__).resolve().parents[3]
    if out == repo or repo in out.parents:
        print("error: refusing to write the results inside the repository", file=sys.stderr)
        return EXIT_INPUT
    try:
        result = run(args)
    except ProxyInputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"out": str(out), "totals": result["totals"]}, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
