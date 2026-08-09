#!/usr/bin/env python
"""Scan a JATS harvest for retraction/correction/EoC notices → an exclusion list.

Why this exists. The harvest was discovered by TITLE only (the pubType filter
never ran), so it contains editorial matter and — worse — retraction notices.
In JATS a retraction is **not a flag on the article**: it is a *separate* small
article (its own PMCID, ``article-type="retraction"``) that names its target via

    <related-article related-article-type="retracted-article"
                     ext-link-type="pmc" xlink:href="PMC8910543"/>

while the retracted ORIGINAL's own XML is typically untouched in the OA bulk
copy. So dropping notice article-types alone leaves every retracted paper in the
corpus, surfacing with full confidence at query time. This tool makes the
two-hop exclusion: one pass over the corpus roots finds the notices, and the
notices' ``related-article`` links yield the originals.

Corrections are deliberately the OTHER case: a corrected original stays
scientifically valid (it even links forward to its correction via
``related-article-type="correction-forward"``), and the correction notice
carries the actual fixed values — both are KEPT by default.

Outputs, kept separate on purpose:

* ``exclusions.jsonl`` — one ``{"pmcid", "reason", ...}`` row per article to
  exclude. **Feed this straight to ``plan_shards.py --exclude``** (which accepts
  any JSONL with a ``pmcid`` field). Only rows that should be excluded go here,
  because the planner excludes every pmcid it finds in the file.
* ``report.json`` — everything else: per-type counts, per-reason counts,
  expression-of-concern ORIGINALS (kept, listed for visibility — an EoC is not a
  retraction), retraction targets that are not in this corpus, and parse
  failures.

The article-type is matched on the ``<article>`` ROOT TAG only. This matters:
a naive grep for ``article-type="correction"`` also matches
``related-**article-type**="correction-forward"`` — an attribute that appears on
*corrected originals* — and would misclassify valid research articles as
notices. (That exact mistake produced a wrong distribution once already; the
regression test pins it.)

Usage::

    python scripts/scan_notices.py --corpus /rag/oa/corpus \
        --out /rag/ingest/oa/notice-scan
    python scripts/plan_shards.py --corpus /rag/oa/corpus --out ... \
        --exclude /rag/ingest/oa/notice-scan/exclusions.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

#: Notice types excluded by default. Corrections are NOT here: the corrected
#: original stays valid and the notice carries the fix. expression-of-concern
#: notices are dropped but their ORIGINALS are kept (an EoC is a warning, not a
#: retraction) — they are listed in the report instead.
DEFAULT_DROP = (
    "retraction",
    "retraction-forward",
    "expression-of-concern",
    "editorial",
    "news",
    "book-review",
)

#: Notice types whose related-article TARGETS are also excluded.
TARGETED_DROP = ("retraction", "retraction-forward")

#: related-article-type values that point a notice at its subject.
_TARGET_RA_TYPES = frozenset({"retracted-article", "object-of-concern"})

#: The root tag's article-type, and nothing else's. The negative lookbehind is
#: the whole point: ``related-article-type="…"`` must not match (see docstring).
_ROOT_TYPE = re.compile(rb'<article\s[^>]*?(?<![-\w])article-type="([^"]+)"')

#: How much of a file the fast path reads. clean/ files carry no DOCTYPE, so the
#: root tag is in the first kilobyte; 8 KiB gives margin for fat processing-meta.
_HEAD_BYTES = 8192


def root_article_type(path: str) -> str | None:
    """The root ``<article>`` element's article-type, or None if unreadable.

    Fast path reads the head; a miss falls back to a real parse rather than
    assuming, so an unusual file is classified correctly or reported, never
    silently defaulted.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return None
    m = _ROOT_TYPE.search(head)
    if m:
        return m.group(1).decode("utf-8", "replace")
    try:
        return ET.parse(path).getroot().get("article-type")
    except Exception:  # noqa: BLE001 - one bad file is a report row, not a crash
        return None


def notice_targets(path: str) -> list[str]:
    """PMCIDs a notice points at (``related-article`` with a pmc href)."""
    try:
        root = ET.parse(path).getroot()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ra in root.iter("related-article"):
        if ra.get("related-article-type") not in _TARGET_RA_TYPES:
            continue
        href = (ra.get(XLINK_HREF) or "").strip()
        if ra.get("ext-link-type") == "pmc" and href:
            out.append(href if href.startswith("PMC") else f"PMC{href}")
    return out


def _scan_chunk(paths: list[str]) -> list[tuple[str, str, str]]:
    """``[(path, pmcid, article_type)]`` for one worker chunk. '' = unreadable."""
    out = []
    for p in paths:
        pmcid = Path(p).name.split(".")[0]
        out.append((p, pmcid, root_article_type(p) or ""))
    return out


def scan(corpus: str, tree: str = "clean", workers: int = 8,
         limit: int = 0) -> tuple[dict[str, list[tuple[str, str]]], list[str], int]:
    """Walk the tree once → ``{article_type: [(path, pmcid)]}``, bad files, total."""
    root = Path(corpus) / tree
    files = sorted(str(p) for p in root.rglob("*.xml"))
    if limit:
        files = files[:limit]
    by_type: dict[str, list[tuple[str, str]]] = {}
    bad: list[str] = []
    chunks = [files[i:i + 2000] for i in range(0, len(files), 2000)]
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(_scan_chunk, chunks):
            for path, pmcid, atype in rows:
                if not atype:
                    bad.append(path)
                    continue
                by_type.setdefault(atype, []).append((path, pmcid))
    return by_type, bad, len(files)


def build_exclusions(
    by_type: dict[str, list[tuple[str, str]]],
    drop_types: tuple[str, ...],
    *,
    known_pmcids: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """Exclusion rows + the report extras.

    ``known_pmcids`` (when given) marks which retraction targets exist in this
    corpus; targets outside it are reported, not excluded — excluding a pmcid
    that is not in the plan is harmless, but reporting keeps the accounting
    honest.
    """
    exclusions: list[dict] = []
    seen: set[str] = set()
    eoc_originals: list[dict] = []
    targets_outside: list[dict] = []

    def _add(pmcid: str, reason: str, **extra) -> None:
        if pmcid in seen:
            return
        seen.add(pmcid)
        exclusions.append({"pmcid": pmcid, "reason": reason, **extra})

    for atype in drop_types:
        for path, pmcid in by_type.get(atype, []):
            _add(pmcid, f"notice:{atype}")
            if atype not in TARGETED_DROP and atype != "expression-of-concern":
                continue
            for target in notice_targets(path):
                if atype in TARGETED_DROP:
                    if known_pmcids is not None and target not in known_pmcids:
                        targets_outside.append({"pmcid": target, "via": pmcid})
                        continue
                    _add(target, "retracted-original", via=pmcid)
                else:  # expression-of-concern: original KEPT, listed
                    eoc_originals.append({"pmcid": target, "via": pmcid})

    report_extra = {
        "eoc_originals_kept": eoc_originals,
        "retraction_targets_not_in_corpus": targets_outside,
    }
    return exclusions, report_extra


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", required=True)
    p.add_argument("--tree", default="clean", choices=("clean", "xml"))
    p.add_argument("--out", required=True, help="output dir (exclusions.jsonl + report.json)")
    p.add_argument("--drop-types", default=",".join(DEFAULT_DROP),
                   help=f"root article-types to exclude (default: {','.join(DEFAULT_DROP)}). "
                        "Retraction targets are always excluded alongside their notices.")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0, help="scan only the first N files (testing)")
    args = p.parse_args(argv)

    drop = tuple(t.strip() for t in args.drop_types.split(",") if t.strip())
    t0 = time.time()
    by_type, bad, total = scan(args.corpus, args.tree, args.workers, args.limit)
    known = {pmcid for rows in by_type.values() for _, pmcid in rows}
    exclusions, extra = build_exclusions(by_type, drop, known_pmcids=known)

    os.makedirs(args.out, exist_ok=True)
    excl_path = os.path.join(args.out, "exclusions.jsonl")
    with open(excl_path, "w", encoding="utf-8") as fh:
        for row in exclusions:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    reasons: dict[str, int] = {}
    for row in exclusions:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    report = {
        "schema": "ragstack.notice_scan/v1",
        "n_files": total,
        "n_unreadable": len(bad),
        "unreadable": bad[:200],
        "article_types": {k: len(v) for k, v in sorted(by_type.items())},
        "drop_types": list(drop),
        "n_excluded": len(exclusions),
        "excluded_by_reason": reasons,
        "wall_s": round(time.time() - t0, 1),
        **extra,
    }
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    print(f"scanned {total:,} files in {report['wall_s']}s "
          f"({len(bad)} unreadable)", file=sys.stderr)
    for reason, n in sorted(reasons.items()):
        print(f"  {reason:<28} {n:>7,}", file=sys.stderr)
    print(f"  {'eoc originals kept':<28} {len(extra['eoc_originals_kept']):>7,}",
          file=sys.stderr)
    print(f"  {'targets outside corpus':<28} "
          f"{len(extra['retraction_targets_not_in_corpus']):>7,}", file=sys.stderr)
    print(f"→ {excl_path}  (feed to plan_shards.py --exclude)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
