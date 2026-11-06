#!/usr/bin/env python
"""JATS XML -> JSONL extraction tool for the CWL/GoWe OA ingest plane (#301).

The XML counterpart of ``scripts/pdf_extract.py``: it runs the real
:mod:`ragstack.ingestion.jats` parser over a set of PubMed-Central JATS articles
and emits **one JSONL shard** in the exact ``{"text", "path", "metadata"}`` shape
that ``scripts/embed_shard.py`` / ``scripts/ingest_shard.py`` consume via
:class:`ragstack.ingestion.loaders.JsonlLoader`, plus a sidecar JSON report of
everything that was skipped. This is the bulk route (the CWL scatter, path C
of #828 §8): since #828 PR 5a the API process also loads a single ``.xml`` in
process through :class:`ragstack.ingestion.loaders.JatsLoader`, which builds its
documents from the same parser, the same structure helper and the same
record-to-``Document`` code ``JsonlLoader`` applies to this tool's shard.

Ported from the validated out-of-tree converter ``/rag/oa/scripts/jats_to_jsonl.py``
(800 articles -> 6,538 records, 0 failures; inline-table chunk contamination
17.78% -> 0.27% after lifting floats out of the prose). The parsing rules — two
record kinds, floats lifted at any depth, ``<floats-group>`` captured,
row-splitting of oversized tables with the caption+header repeated — live in
``ragstack/ingestion/jats.py`` and are documented there; the structure adapter
(``jats/1``) lives in ``ragstack/ingestion/jats_structure.py``, and so does the
one helper that builds and validates a record's structure
(``record_structure``). This file is I/O plus attaching that structure and
counting its drops.

INPUT CONTRACT — two modes, ``--shard`` (the CWL scatter) or ``--corpus`` (ad hoc)

``--shard FILE``
    A **JSONL** file, one article per line, as produced by
    ``scripts/plan_shards.py``. Each line is an object with at least::

        {"pmcid": "PMC123", "xml_path": "clean/ab/cd/PMC123.xml"}

    * ``xml_path`` (required) — path to the article's JATS XML. A relative path
      is resolved against ``--corpus`` when given, else against the directory
      holding the shard file.
    * ``pmcid`` (optional) — the article id; it becomes the record ``path`` and
      hence the document id. Defaults to ``jats.record_key``: the filename stem
      up to the first ``.`` when it is a PMC id (``PMC123.1.xml`` -> ``PMC123``),
      else the article's own ``<article-id pub-id-type="pmc">``, else the
      file's RESOLVED path (``/corpus/clean/journal.pone.0012345.xml``) — the
      key ``JatsLoader`` gives the same file in process, and the one a PDF at
      that path gets. Step 1 is literal (``pmc4242.xml`` -> ``pmc4242``).
      An explicit ``pmcid`` is taken as given (never normalised).
      **Re-runs of a non-PMC corpus:** a path key follows the STAGED path,
      and cwltool stages ``corpus`` under a fresh ``stg<uuid>`` directory on
      every run, so such a file gets new doc ids each run. jats-ingest.cwl
      runs no duplicate guard and ``ingest_shard.py --duplicate-policy``
      defaults to ``off``, so a re-run indexes a second copy. Give such lines
      a ``pmcid`` (``plan_shards.py``/manifest ids), or load the shards with
      ``ingest_shard.py --duplicate-policy refuse``.
    * **Any other keys on the line are treated as that article's manifest row**
      (``sha256``, ``source_url``, ``doi_xml``, ``licence``, ...), so a shard is
      self-contained and a worker never has to read the ~1M-line corpus
      ``manifest.jsonl``. ``--manifest`` can still be passed to enrich bare shards.
    * A line that is not JSON is taken as a bare ``xml_path`` (convenience for
      ``find ... > shard.txt``). Blank lines are ignored.

``--corpus DIR``
    Walk ``DIR/<--tree>/**/*.xml`` and ``*.nxml``, any case (``clean/`` is the ``<back>``-stripped tree —
    that is the one you ingest) and read ``DIR/manifest.jsonl`` for provenance.
    ``--limit N`` truncates the sorted walk, for previews.

OUTPUT CONTRACT

* ``--out FILE`` — one JSONL shard: ``{"text", "path", "metadata", "structure"}``
  per line. ``path`` is unique per record (``PMC123``, ``PMC123#table-2``,
  ``PMC123#table-2-part-3``) because ``JsonlLoader`` derives the document id from
  it and colliding paths overwrite each other. ``structure`` (#828 §4) is the
  record's ``jats/1`` document structure, ``DocumentStructure.model_dump()``,
  as the LAST top-level key and never inside ``metadata``: built from the
  article's element spans (article records) or the unit's layout (table/figure
  records) and validated against ``text`` before it is written. A record whose
  structure fails is written WITHOUT the key — byte-identical to a record from
  before the key existed — with the drop counted in the report and one stderr
  line; the record itself is never dropped. The shard is the only carrier of
  the structure from this step to the ingest step, which re-validates it.
* ``--spans-out FILE`` (optional, off by default) — a debugging side file: one
  ``{"pmcid", "spans"}`` JSONL row per article record, every element's
  ``(start, end)`` in that record's ``text``
  (:class:`ragstack.ingestion.jats.Span`). It duplicates what the shard's
  ``structure`` key was built from; the ``--out`` shard is byte-identical with
  or without it.
* ``--report FILE`` (alias ``--skip-report``, the flag ``jats-ingest.cwl`` binds)
  — sidecar JSON: per-kind record counts, ``n_structured`` (records with a
  ``structure`` key) and ``structure_dropped`` (``{reason: n}``), and the full
  list of skipped items (unparseable articles, articles with no prose, units
  under ``--min-unit-chars``). Nothing is dropped silently.

Exit code is 0 on success, 1 if no XML matched, if every article failed to parse,
or if the parse-failure rate exceeds ``--max-fail-rate`` (default 1%) — under
``set -e`` an unconditional ``exit 0`` would let a totally failed convert flow
through to a green, empty ingest.

Usage::

    python scripts/jats_extract.py --shard shards/s0007.jsonl \
        --corpus /rag/oa/corpus --out s0007.jsonl --report s0007.report.json

    python scripts/jats_extract.py --corpus /rag/oa/corpus --out oa.jsonl --limit 20
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from ragstack.ingestion.jats import (
    CONTENT_TYPES,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MIN_UNIT_CHARS,
    JATS_SUFFIXES,
    convert_file,
    file_record_key,
    pmc_stem,
)
from ragstack.ingestion.jats_structure import record_structure
from ragstack.provenance import add_provenance_arguments, provenance_from_args


def load_manifest(path: str | Path) -> dict[str, dict]:
    """``manifest.jsonl`` -> ``{pmcid: row}``. Bad lines are skipped, not fatal."""
    manifest: dict[str, dict] = {}
    p = Path(path)
    if not p.exists():
        return manifest
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("pmcid"):
            manifest[row["pmcid"]] = row
    return manifest


def read_shard(shard: str | Path, corpus: str | Path | None = None) -> list[dict]:
    """Shard file -> ``[{"pmcid", "xml_path", **manifest_fields}]`` in file order.

    See the module docstring for the line contract. Relative ``xml_path`` is
    resolved against ``corpus`` when given, else against the shard's directory.
    """
    shard_path = Path(shard)
    base = Path(corpus) if corpus else shard_path.parent
    items: list[dict] = []
    for line in shard_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = {"xml_path": line}  # bare path line
        if not isinstance(row, dict):
            row = {"xml_path": str(row)}
        raw = row.get("xml_path") or row.get("path") or ""
        if not raw:
            continue
        p = Path(raw)
        row["xml_path"] = str(p if p.is_absolute() else base / p)
        # A PMC-named file's key is its name (no parse); any other file's is
        # left to extract_articles, which reads the article's own PMC id
        # (jats.record_key). An explicit pmcid on the line always wins.
        if not row.get("pmcid") and pmc_stem(row["xml_path"]):
            row["pmcid"] = pmc_stem(row["xml_path"])
        items.append(row)
    return items


def walk_corpus(corpus: str | Path, tree: str = "clean") -> list[dict]:
    """Walk ``corpus/tree/**/*.xml`` and ``**/*.nxml`` (the PMC OA bulk
    package's suffix, which the in-process route loads too) -> shard items,
    sorted for determinism.

    A PMC-named file carries its key (``pmcid``) from the name; any other
    file's key is resolved by :func:`extract_articles` (``jats.record_key``)."""
    root = Path(corpus) / tree
    items: list[dict] = []
    # Suffixes case-insensitively (``PMC1.XML``), as manifest.py and the
    # loader registry match them (#975 round 3 nit 10).
    files = {f for f in root.rglob("*")
             if f.name.lower().endswith(JATS_SUFFIXES)}
    for f in sorted(files):
        item = {"pmcid": pmc_stem(f), "xml_path": str(f)}
        if not item["pmcid"]:
            del item["pmcid"]
        items.append(item)
    return items


def _attach_structure(record: dict, spans: list, units: dict,
                      structure_dropped: Counter[str]) -> None:
    """Attach one record's ``jats/1`` structure (#828 §4), or count its drop.

    The structure is built and validated by
    :func:`ragstack.ingestion.jats_structure.record_structure` — the same
    helper the in-process ``JatsLoader`` uses, so the two cannot disagree. On
    success it is the record's LAST top-level key, ``structure`` (never inside
    ``metadata``). On failure no key is written, the drop is counted under the
    reason the helper gave (the validator's reason, or the exception class
    name), one stderr line says so, and the record is kept exactly as it was."""
    dumped, reason = record_structure(record, spans, units)
    if dumped is not None:
        record["structure"] = dumped
        return
    assert reason is not None
    structure_dropped[reason] += 1
    print(f"jats_extract: {record['path']}: structure dropped ({reason})", file=sys.stderr)


def extract_articles(
    items: list[dict],
    manifest: dict[str, dict] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_unit_chars: int = DEFAULT_MIN_UNIT_CHARS,
    kinds: set[str] | None = None,
    count_tokens=None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    spans: list[dict] | None = None,
    structure_dropped: Counter[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Convert ``items`` -> ``(records, skipped)``. Pure — no filesystem writes.

    Each item's own keys are its manifest row; ``manifest[pmcid]`` (when given)
    fills in underneath, so a self-contained shard needs no corpus manifest.
    ``kinds`` filters emitted records by ``content_type`` (default: all).

    Every record carries its ``jats/1`` document structure as the top-level
    ``structure`` key (#828 §4), validated against its ``text`` here; a record
    whose structure cannot be built or is invalid is emitted without the key
    and the drop is counted in ``structure_dropped`` (by reason), when given.

    ``spans``, when a list, receives one ``{"pmcid", "spans"}`` row per article
    that produced an article record (independent of ``kinds``): the element
    spans of :func:`ragstack.ingestion.jats.article_spans`, offsets into that
    record's ``text`` — a debugging duplicate of what the structure was built
    from. The records are the same either way.
    """
    manifest = manifest or {}
    kinds = kinds or set(CONTENT_TYPES)
    dropped = structure_dropped if structure_dropped is not None else Counter()
    records: list[dict] = []
    skipped: list[dict] = []
    for item in items:
        # The record key (jats.record_key): the item's explicit pmcid, else a
        # PMC-named file's stem, else the article's own PMC id, else the
        # file's resolved path — the rule JatsLoader keys the in-process
        # records on.
        # A key that cannot be computed (a symlink loop: ``Path.resolve()``
        # raises RuntimeError) skips its file, never the shard (#975 round 3
        # nit 5).
        try:
            pmcid = item.get("pmcid") or file_record_key(item["xml_path"])
        except (OSError, RuntimeError, ValueError) as e:
            skipped.append({"path": str(item["xml_path"]),
                            "pmcid": Path(item["xml_path"]).name, "kind": "article",
                            "reason": f"record key: {type(e).__name__}: {e}"})
            continue
        row = dict(manifest.get(pmcid, {}))
        row.update({k: v for k, v in item.items()
                    if k not in ("xml_path", "pmcid") and v not in (None, "")})
        recs, skips, art_spans, units = convert_file(item["xml_path"], pmcid, row,
                                                     max_chars, min_unit_chars,
                                                     count_tokens, max_tokens,
                                                     with_spans=True)
        if spans is not None and art_spans:
            spans.append({"pmcid": pmcid,
                          "spans": [s._asdict() for s in art_spans]})
        skipped.extend(skips)
        for r in recs:
            if r["metadata"].get("content_type", "article") in kinds:
                _attach_structure(r, art_spans, units, dropped)
                records.append(r)
    return records, skipped


def write_jsonl(records: list[dict], out: str) -> None:
    with open(out, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False))
            fh.write("\n")


def build_report(records: list[dict], skipped: list[dict], n_input: int,
                 out: str, structure_dropped: Counter[str] | None = None) -> dict:
    """Sidecar report: per-kind counts + every skipped article/unit, and how
    many records carry a ``structure`` key (``n_structured``) and why the
    others' were dropped (``structure_dropped``, ``{reason: n}``)."""
    by_kind = dict.fromkeys(CONTENT_TYPES, 0)
    for rec in records:
        ct = rec["metadata"].get("content_type", "article")
        by_kind[ct] = by_kind.get(ct, 0) + 1
    failed = sum(1 for s in skipped if s.get("kind") == "article")
    return {
        "out": out,
        "n_input": n_input,                       # articles attempted
        "n_articles": n_input - failed,           # articles converted
        "n_failed": failed,                       # unparseable / unreadable
        "n_extracted": len(records),              # records written
        "n_skipped": len(skipped),
        "by_content_type": by_kind,
        "n_units_too_short": sum(1 for s in skipped if s.get("kind") == "unit"),
        "n_articles_without_prose": sum(1 for s in skipped if s.get("kind") == "prose"),
        "n_chars": sum(len(r["text"]) for r in records),
        "n_structured": sum(1 for r in records if "structure" in r),
        "structure_dropped": dict(sorted((structure_dropped or Counter()).items())),
        "skipped": skipped,
    }


def _unit_token_counter(args):
    """A real tokenizer for the unit-splitting budget, or None (char budgets).

    Char budgets alone under-split tables badly: measured on 1,727 real units,
    token density spans 1.61-4.30 chars/token, and 32.2% of units came out over
    one 512-token window -- whereupon the stock chunker split them with NO
    caption/header context, the exact contamination the lift-out prevents. The
    HF tokenizer is the same one embed_shard's chunker uses (it is in the worker
    image); when it cannot load, fall back to char budgets LOUDLY, not silently.
    """
    if args.no_token_budget:
        return None
    try:
        from ragstack.ingestion.tokenization import HFTokenCounter

        counter = HFTokenCounter(model=args.unit_tokenizer)
        counter.count("probe")  # force the lazy load; fail here, not mid-shard
        return counter.count
    except Exception as e:  # noqa: BLE001 - degraded, not fatal; the report shows it
        print(f"jats_extract: HF tokenizer {args.unit_tokenizer!r} unavailable "
              f"({type(e).__name__}); falling back to CHAR budgets -- expect "
              f"~30% of table units to exceed one 512-token chunk", file=sys.stderr)
        return None


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.shard and not args.corpus:
        print("jats_extract: one of --shard or --corpus is required", file=sys.stderr)
        return 2

    if args.shard:
        items = read_shard(args.shard, args.corpus)
    else:
        tree = Path(args.corpus) / args.tree
        if not tree.is_dir():
            print(f"jats_extract: no {args.tree}/ under {args.corpus}", file=sys.stderr)
            return 2
        items = walk_corpus(args.corpus, args.tree)
    if args.limit:
        items = items[: args.limit]

    # A shard carries its own provenance, so the ~1M-line corpus manifest is read
    # only when walking the corpus or when explicitly asked for.
    manifest_path = args.manifest or (
        None if args.shard else str(Path(args.corpus) / "manifest.jsonl"))
    manifest = load_manifest(manifest_path) if manifest_path else {}

    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    count_tokens = _unit_token_counter(args)
    span_rows: list[dict] | None = [] if args.spans_out else None
    structure_dropped: Counter[str] = Counter()
    records, skipped = extract_articles(items, manifest, args.max_table_chars,
                                        args.min_unit_chars, kinds,
                                        count_tokens, args.unit_token_budget,
                                        spans=span_rows,
                                        structure_dropped=structure_dropped)
    write_jsonl(records, args.out)
    if span_rows is not None:
        write_jsonl(span_rows, args.spans_out)
    report = build_report(records, skipped, len(items), args.out, structure_dropped)
    # ADR-0010 decision 8, #821: which workflow and which tools image
    # extracted this shard (never all-null: segmenter_version is always known).
    report["provenance"] = provenance_from_args(args)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)

    by = report["by_content_type"]
    print(f"jats_extract: articles={report['n_articles']} failed={report['n_failed']} "
          f"records={report['n_extracted']} "
          f"(article {by.get('article', 0)}, table {by.get('table', 0)}, "
          f"figure {by.get('figure', 0)}) "
          f"units_too_short={report['n_units_too_short']} "
          f"structured={report['n_structured']} "
          + (f"structure_dropped={report['structure_dropped']} " if structure_dropped else "")
          + f"chars={report['n_chars']} -> {args.out}"
          + (f" (report: {args.report})" if args.report else ""), flush=True)
    for s in skipped:
        if s.get("kind") == "article":
            print(f"  ! {s['pmcid']}: {s['reason']}", file=sys.stderr)

    # Unlike pdf_extract (where an all-skipped shard is a valid empty result),
    # a JATS shard that mostly failed to PARSE is a broken input, not an empty
    # one: fail loudly so `set -e` does not carry it into a green, empty ingest.
    if not items:
        print("jats_extract: FAIL — no XML files matched "
              "(wrong --shard/--corpus/--tree?)", file=sys.stderr)
        return 1
    if report["n_articles"] == 0:
        print(f"jats_extract: FAIL — every one of {len(items)} articles failed to parse",
              file=sys.stderr)
        return 1
    rate = report["n_failed"] / len(items)
    if args.max_fail_rate > 0 and rate > args.max_fail_rate:
        print(f"jats_extract: FAIL — parse failure rate {rate * 100:.2f}% exceeds "
              f"--max-fail-rate {args.max_fail_rate * 100:.2f}%", file=sys.stderr)
        return 1
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--shard", default=None,
                   help="JSONL shard file listing the articles to convert "
                        "(one {'pmcid','xml_path',...} object per line)")
    p.add_argument("--corpus", default=None,
                   help="harvest dir containing clean/ (or xml/) and manifest.jsonl; "
                        "walked when --shard is absent, otherwise used to resolve "
                        "relative xml_path")
    p.add_argument("--out", default="shard.jsonl", help="output JSONL shard path")
    # --report is pdf_extract.py's name for the same side output; --skip-report is
    # the name jats-ingest.cwl binds. Both write the same file.
    p.add_argument("--report", "--skip-report", dest="report", default=None,
                   help="output sidecar JSON report path (skipped items + counts)")
    p.add_argument("--manifest", default=None,
                   help="manifest.jsonl to enrich records with (default: "
                        "<corpus>/manifest.jsonl in --corpus mode, none in --shard mode)")
    p.add_argument("--tree", default="clean", choices=("clean", "xml"),
                   help="corpus subtree to walk; clean/ is <back>-stripped, "
                        "that is what you ingest")
    p.add_argument("--unit-token-budget", type=int, default=DEFAULT_MAX_TOKENS,
                   help="per-unit token cap for table/figure splitting (default "
                        f"{DEFAULT_MAX_TOKENS}; headroom under the 512-token chunk window)")
    p.add_argument("--unit-tokenizer", default="Salesforce/SFR-Embedding-Mistral",
                   help="HF tokenizer that defines the token budget (must match "
                        "the embedding model)")
    p.add_argument("--no-token-budget", action="store_true",
                   help="split by characters only (pre-token behaviour; leaves "
                        "~30%% of table units over one chunk window)")
    p.add_argument("--max-table-chars", type=int, default=DEFAULT_MAX_CHARS,
                   help="split table/figure units longer than this (~450 tokens)")
    p.add_argument("--min-unit-chars", type=int, default=DEFAULT_MIN_UNIT_CHARS,
                   help="report (and do not emit) table/figure units shorter than "
                        "this. Captions like 'Figure 1 Flow chart.' carry no "
                        "retrievable information and embed as near-noise "
                        "(0.5%% of units at the default)")
    p.add_argument("--limit", type=int, default=0,
                   help="convert at most N articles (preview)")
    p.add_argument("--kinds", default=",".join(CONTENT_TYPES),
                   help="comma-separated content_type filter")
    p.add_argument("--spans-out", default=None,
                   help="debugging side file: also write the element spans, one "
                        "{pmcid, spans} JSONL row per article record (offsets into "
                        "its text; #318). The shard record already carries the "
                        "structure built from them (its top-level 'structure' key, "
                        "#828 §4); the --out shard is byte-identical with or "
                        "without this flag")
    p.add_argument("--max-fail-rate", type=float, default=0.01,
                   help="exit non-zero if more than this fraction of articles fail "
                        "to parse (0 disables)")
    add_provenance_arguments(p)
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
