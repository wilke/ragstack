#!/usr/bin/env python
"""Shard **planner** for the JATS/OA ingest plane (#301, ADR-0001 offline plane).

Turns a hash-fanned harvest (``corpus/clean/xx/yy/PMC*.xml`` + ``manifest.jsonl``)
into the shard files ``cwl/jats-ingest.cwl`` scatters over. Each shard is a JSONL
list of articles that one worker will run through ``jats_extract.py`` →
``embed_shard.py``; ``load_embeddings.py`` gathers the result.

WHY A PLANNER AT ALL. The harvest is ~940k files and still downloading, so the
obvious answer — list the tree and cut it into N pieces — is wrong twice over.
``os.walk`` order is filesystem-dependent (not reproducible across hosts or even
across runs), and any cut derived from the *count* renumbers every article the
moment the count changes. This tool exists to make the assignment independent of
both.

**THE STABILITY PROPERTY.** An article's shard is a pure function of its
identity::

    shard(pmcid) = int(sha1(pmcid)[:16], 16) % n_shards

Nothing else is an input: not the corpus size, not the manifest order, not the
directory listing, not what has already been ingested. Re-planning after another
100k articles land moves *no* already-assigned article, because the modulus is an
explicit parameter the operator sets, never a value derived from the population.
(``index % ceil(count / target)`` — the natural-looking choice — reshuffles the
entire corpus on every growth step; that is exactly what this avoids.)

The cost of that guarantee is honest and bounded: shards grow with the corpus
rather than multiplying. ``n_shards`` is therefore sized for the *final* harvest,
not today's. When you do need finer shards, keep ``n_shards`` a **power of two**
and double it: with a power-of-two modulus the assignment is the low bits of the
hash, so ``n_shards -> 2*n_shards`` splits shard ``b`` into exactly ``b`` and
``b + n_shards`` and mixes nothing across shard boundaries. A doubling is a
refinement of the old plan, not a reshuffle of it. Non-powers of two are refused
for that reason.

**BALANCE IS BY WORK, NOT BY FILE COUNT.** Articles vary from 0 to 4.3M body
chars, so equal-count shards have wildly unequal wall-clock. The manifest carries
``body_chars`` + ``floats_chars``, which is what the extractor actually turns into
chunks, so the plan reports and budgets on *that*. Hash bucketing balances work
statistically rather than by explicit bin-packing — a packer would have to look at
the whole population, which is precisely the corpus-size dependence the stability
property forbids. Measured on the real 940,421-article harvest, that costs
surprisingly little::

    n_shards   articles/shard   work chars/shard   CV     worst/mean
       256          3674            142.1 M       1.9 %     1.06
       512          1837             71.1 M       2.7 %     1.08
      1024           918             35.5 M       3.8 %     1.15
      2048           459             17.8 M       5.3 %     1.23
      4096           230              8.9 M       7.5 %     1.49

**WHY 2048 IS THE DEFAULT.** Sized against the embedding file each shard produces,
which is the binding constraint — not the embed time. At ~26.9 chunks/article and
a measured 54.4 kB per 4096-d chunk in ``ragstack.embedding_file`` (JSON floats),
a 2048-shard plan of today's harvest gives ~459 articles → ~12.3k chunks → a
**~0.67 GB** intermediate file per shard, ~28 s of embed at 443 chunks/s. Losing
one worker loses half a minute of GPU and under a gigabyte of scratch. Going
coarser is tempting for the per-task overhead but 1024 already means a 1.3 GB file
that ``write_embedding_file`` materializes in memory; going finer buys balance you
can see degrading in the table above. 2048 shards over an 8-endpoint fleet is also
~256 waves, so the straggler tail is negligible.

**RESUMABILITY.** ``--exclude`` takes pmcids that are already planned or ingested
and are not to be planned again. It accepts, and auto-detects, three shapes, so
the caller supplies whatever artifact they already have:

  * a **previous plan's shard files** (or the whole plan directory — a directory
    argument reads its ``shard-*.jsonl``, and pointedly *not* its
    ``skipped.jsonl``, so an article skipped because its download had not landed
    yet gets planned once the harvest repairs it);
  * any **JSONL with a ``pmcid`` field** — ``jats_extract`` skip reports, receipts,
    a Qdrant/ES inventory dump;
  * a **plain text file**, one pmcid per line, ``#`` comments allowed.

The normal resume is round-based: plan round 2 into a *new* output directory while
excluding round 1's shard directory. Shard *numbers* still name the same buckets,
so ``shard-01337.jsonl`` in round 2 holds new arrivals of the same bucket as round
1's — which is the point. Writing into a directory that already holds shards is
refused unless ``--force``, so a resume cannot silently truncate an earlier round.

**NOTHING IS DROPPED SILENTLY.** An article in the manifest whose XML is missing,
zero-byte, listed in ``failures.jsonl``, or empty of body+float text is written to
``skipped.jsonl`` with a reason and counted in ``plan.json`` and on stdout. The
file paths are *computed* from the fanout convention (sha1 of the pmcid, two
levels of two hex chars) and every one is stat'ed — about 5 s for the full harvest
— so a corpus laid out differently shows up immediately as a wall of ``missing``
rather than as a plan of paths that do not exist. A run where most files are
missing aborts instead of emitting that plan.

Shard files are plain JSONL, one article per line, no header — the contract
``jats_extract.py --shard`` reads, and it keeps a shard splittable and
concatenable with ``head``/``cat``. Each line is the article's full manifest row
plus ``xml_path`` (relative to the corpus root, so it resolves inside the CWL
container mount), which makes a shard self-contained: a worker never opens the
940k-line ``manifest.jsonl``. Plan-level metadata — the modulus, the counts, the
distribution, the skip tally — goes in a sidecar ``plan.json``, not in the shard
files.

Usage::

    # what would this plan look like? (writes nothing)
    python scripts/plan_shards.py --corpus /rag/oa/corpus --out shards/ --dry-run

    # round 1
    python scripts/plan_shards.py --corpus /rag/oa/corpus --out /rag/ingest/oa/r1

    # round 2, after another 100k articles land: same buckets, only new work
    python scripts/plan_shards.py --corpus /rag/oa/corpus --out /rag/ingest/oa/r2 \
        --exclude /rag/ingest/oa/r1
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import statistics
import sys
import time
from collections.abc import Iterable, Iterator

SCHEMA = "ragstack.shard_plan/v1"

# Harvest fanout: clean/<sha1(pmcid)[0:2]>/<sha1(pmcid)[2:4]>/<pmcid>.xml. Verified
# against 20,000 random articles of the live harvest (20,000/20,000 hits).
FANOUT_LEVELS = 2
FANOUT_WIDTH = 2

# Corpus-derived constants used only for the *estimates* in the plan report; they
# never influence assignment. Chunks/article is #301's measured 512/64 figure, the
# embed rate is the historical fleet throughput, and the per-chunk file size is
# measured from a real 4096-d embedding file.
CHUNKS_PER_ARTICLE = 26.9
EMBED_CHUNKS_PER_SEC = 443.0
BYTES_PER_CHUNK = 54_400

SKIP_REASONS = ("failed_fetch", "missing", "empty_file", "no_work", "bad_manifest_line")


# --------------------------------------------------------------------------- #
# assignment
# --------------------------------------------------------------------------- #

def article_hash(pmcid: str) -> int:
    """The article's identity as an integer. The ONLY input to shard assignment.

    sha1 rather than ``hash()``: Python's string hash is salted per process, so it
    would give a different plan on every run.
    """
    return int(hashlib.sha1(pmcid.encode("utf-8")).hexdigest()[:16], 16)


def shard_index(pmcid: str, n_shards: int) -> int:
    """Which shard this article belongs to. Pure function of ``pmcid``.

    With ``n_shards`` a power of two this is the low ``log2(n_shards)`` bits of the
    hash, which is what makes a doubling a clean split (see the module docstring).
    """
    return article_hash(pmcid) % n_shards


def xml_relpath(pmcid: str, tree: str = "clean") -> str:
    """Corpus-relative path of an article's JATS XML, from the fanout convention.

    Relative on purpose: the CWL extract step mounts the corpus at a container path
    of its own choosing and passes ``--corpus``, so an absolute path baked at plan
    time would not resolve inside the worker.
    """
    h = hashlib.sha1(pmcid.encode("utf-8")).hexdigest()
    parts = [h[i * FANOUT_WIDTH:(i + 1) * FANOUT_WIDTH] for i in range(FANOUT_LEVELS)]
    return "/".join([tree, *parts, f"{pmcid}.xml"])


def work_chars(row: dict) -> int:
    """Work this article represents: body text + lifted tables/figures.

    ``back_chars`` is excluded — the harvest ran ``--strip-back``, so the reference
    list is not in the file the extractor reads. ``bytes`` is excluded too: it is
    XML markup weight, not text the chunker will see.
    """
    return int(row.get("body_chars") or 0) + int(row.get("floats_chars") or 0)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

def iter_manifest(path: str) -> Iterator[tuple[dict | None, str]]:
    """Stream ``manifest.jsonl`` as ``(row, raw_line)``; ``row`` is None if unusable.

    Streamed, not slurped: the live manifest is 570 MB and growing. A bad line is
    yielded with ``row=None`` so the caller can report it rather than lose it.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                yield None, line
                continue
            if not isinstance(row, dict) or not row.get("pmcid"):
                yield None, line
                continue
            yield row, line


def read_pmcid_set(path: str) -> set[str]:
    """pmcids out of a JSONL-with-``pmcid`` file or a plain one-per-line list.

    Detected per line rather than by suffix, so a mixed or unknown artifact still
    works: a JSON object contributes its ``pmcid``, anything else contributes its
    first whitespace-delimited token.
    """
    out: set[str] = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("{"):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pmcid = row.get("pmcid") if isinstance(row, dict) else None
                if pmcid:
                    out.add(str(pmcid))
                continue
            out.add(line.split()[0])
    return out


def read_exclusions(paths: Iterable[str]) -> set[str]:
    """Union of every exclusion source. A directory contributes its ``shard-*.jsonl``.

    The directory form is what makes round-based resume a one-liner: point at the
    previous plan's output directory and everything it *assigned* is excluded.

    Deliberately ``shard-*.jsonl`` and not ``*.jsonl``: a plan directory also holds
    ``skipped.jsonl``, and sweeping that in would mean an article skipped because
    its download had not finished is never planned again once the harvest repairs
    it. Skips are re-evaluated every round. Pass the skip file explicitly if you do
    want to hold a known-bad article out.
    """
    out: set[str] = set()
    for p in paths:
        if os.path.isdir(p):
            files = sorted(glob.glob(os.path.join(p, "shard-*.jsonl")))
            if not files:
                raise SystemExit(f"--exclude {p}: directory holds no shard-*.jsonl")
        else:
            files = [p]
        for f in files:
            if not os.path.exists(f):
                raise SystemExit(f"--exclude {f}: no such file")
            out |= read_pmcid_set(f)
    return out


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #

def plan(
    corpus: str,
    *,
    n_shards: int,
    tree: str = "clean",
    exclude: set[str] | None = None,
    verify_files: bool = True,
    min_work_chars: int = 1,
    limit: int | None = None,
) -> tuple[dict[int, list[dict]], list[dict], dict]:
    """Read the corpus, assign every eligible article, and report what was skipped.

    Returns ``(shards, skipped, stats)``. ``shards`` maps shard index -> the article
    rows assigned to it, each row sorted by hash so the file content is a function
    of the *set* of members and not of manifest order. Non-empty shards only: a
    resume round legitimately fills a fraction of the buckets, and emitting 2048
    files of which 40 have content would drown the scatter.
    """
    exclude = exclude or set()
    manifest_path = os.path.join(corpus, "manifest.jsonl")
    if not os.path.exists(manifest_path):
        raise SystemExit(f"{manifest_path}: no manifest.jsonl under --corpus")

    failed: set[str] = set()
    failures_path = os.path.join(corpus, "failures.jsonl")
    if os.path.exists(failures_path):
        failed = read_pmcid_set(failures_path)

    shards: dict[int, list[dict]] = {}
    skipped: list[dict] = []
    counts = dict.fromkeys(SKIP_REASONS, 0)
    n_rows = n_excluded = n_dup = 0
    seen: set[str] = set()

    for row, raw in iter_manifest(manifest_path):
        n_rows += 1
        if row is None:
            counts["bad_manifest_line"] += 1
            skipped.append({"pmcid": None, "reason": "bad_manifest_line",
                            "detail": raw[:200]})
            continue
        pmcid = str(row["pmcid"])
        if pmcid in seen:
            # A re-fetch appended a second row. Last-wins would make the plan depend
            # on manifest order; first-wins is order-free for an append-only log.
            n_dup += 1
            continue
        seen.add(pmcid)
        if pmcid in exclude:
            n_excluded += 1
            continue

        rel = xml_relpath(pmcid, tree)
        reason = None
        detail = ""
        if pmcid in failed:
            reason = "failed_fetch"
        elif verify_files:
            try:
                st = os.stat(os.path.join(corpus, rel))
            except OSError as e:
                reason, detail = "missing", str(e)
            else:
                if st.st_size == 0:
                    reason = "empty_file"
        if reason is None and (w := work_chars(row)) < min_work_chars:
            # Real in this harvest: ~2.3k articles are front-matter only (no <body>
            # element at all), so there is nothing for the chunker to see.
            reason, detail = "no_work", f"body+floats chars = {w}"
        if reason is not None:
            counts[reason] += 1
            skipped.append({"pmcid": pmcid, "reason": reason, "xml_path": rel,
                            "detail": detail})
            continue

        item = dict(row)
        item["xml_path"] = rel
        shards.setdefault(shard_index(pmcid, n_shards), []).append(item)

    # A wrong fanout convention (or a corpus mid-move) would otherwise produce a
    # confident plan of paths that do not exist. Refuse instead.
    n_planned = sum(len(v) for v in shards.values())
    n_candidates = n_planned + counts["missing"]
    if verify_files and n_candidates and counts["missing"] > n_candidates // 2:
        raise SystemExit(
            f"{counts['missing']}/{n_candidates} articles have no file under "
            f"{corpus}/{tree}/ — the fanout convention "
            f"({tree}/xx/yy/PMCnnn.xml by sha1(pmcid)) does not match this corpus. "
            "Refusing to write a plan of paths that do not exist."
        )

    for idx in shards:
        # Hash order, tie-broken by pmcid: deterministic, and independent of the
        # order the manifest happened to be written in.
        shards[idx].sort(key=lambda r: (article_hash(r["pmcid"]), r["pmcid"]))

    if limit is not None:
        shards, n_planned = _apply_limit(shards, limit)

    stats = {
        "n_manifest_rows": n_rows,
        "n_duplicate_pmcid_rows": n_dup,
        "n_excluded": n_excluded,
        "n_skipped": len(skipped),
        "n_planned": n_planned,
        "skips": counts,
        "n_failures_not_in_manifest": len(failed - seen),
    }
    return shards, skipped, stats


def _apply_limit(shards: dict[int, list[dict]], limit: int) -> tuple[dict[int, list[dict]], int]:
    """Keep the ``limit`` lowest-hash articles overall, for prototype runs.

    By hash rather than by manifest position, so the selection does not depend on
    the order the harvest happened to write. Be clear about the one thing it is
    *not*: this is the only knob here whose **membership** moves as the corpus
    grows — a newly arrived low-hash article displaces the previous last member.
    The stability property still holds for everything it selects (each article's
    shard is unchanged), and a prototype run is excluded from the full run by
    pmcid, so the shift is harmless — but do not read ``--limit N`` as "the same N
    articles forever".
    """
    flat = sorted(
        ((article_hash(r["pmcid"]), r["pmcid"], idx, r)
         for idx, rows in shards.items() for r in rows),
        key=lambda t: (t[0], t[1]),
    )[:limit]
    out: dict[int, list[dict]] = {}
    for _h, _p, idx, row in flat:
        out.setdefault(idx, []).append(row)
    return out, len(flat)


def shard_report(shards: dict[int, list[dict]], target_chars: int) -> dict:
    """Distribution of *work* (not file count) across shards, plus derived costs."""
    loads = sorted(sum(work_chars(r) for r in rows) for rows in shards.values())
    n = len(loads)
    if not n:
        return {"n_shards_nonempty": 0, "n_articles": 0, "total_work_chars": 0,
                "n_over_target": 0}
    total = sum(loads)
    mean = total / n
    articles = sum(len(v) for v in shards.values())
    est_chunks = articles * CHUNKS_PER_ARTICLE

    def pct(p: float) -> int:
        return loads[min(n - 1, int(p * n))]

    return {
        "n_shards_nonempty": n,
        "n_articles": articles,
        "total_work_chars": total,
        "work_chars_per_shard": {
            "mean": round(mean), "min": loads[0], "p50": pct(0.50),
            "p90": pct(0.90), "p99": pct(0.99), "max": loads[-1],
            "cv_pct": round(statistics.pstdev(loads) / mean * 100, 2) if mean else 0.0,
            "max_over_mean": round(loads[-1] / mean, 3) if mean else 0.0,
        },
        "n_over_target": sum(1 for x in loads if x > target_chars),
        "estimates": {
            "chunks_per_article": CHUNKS_PER_ARTICLE,
            "est_chunks_total": round(est_chunks),
            "est_chunks_per_shard_mean": round(est_chunks / n),
            "est_embed_sec_per_shard_mean": round(est_chunks / n / EMBED_CHUNKS_PER_SEC, 1),
            "est_embedding_bytes_per_shard_mean": round(est_chunks / n * BYTES_PER_CHUNK),
            "est_embedding_bytes_total": round(est_chunks * BYTES_PER_CHUNK),
        },
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def shard_filename(idx: int) -> str:
    return f"shard-{idx:05d}.jsonl"


def write_plan(out_dir: str, corpus: str, shards: dict[int, list[dict]],
               skipped: list[dict], stats: dict, report: dict, *,
               n_shards: int, tree: str, skip_report: str | None = None) -> str:
    """Write the shard files, ``skipped.jsonl`` and ``plan.json``. Returns plan path."""
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for idx in sorted(shards):
        rows = shards[idx]
        name = shard_filename(idx)
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        entries.append({"shard": idx, "file": name, "n_articles": len(rows),
                        "work_chars": sum(work_chars(r) for r in rows)})

    skip_path = skip_report or os.path.join(out_dir, "skipped.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(skip_path)), exist_ok=True)
    with open(skip_path, "w", encoding="utf-8") as fh:
        for row in skipped:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    plan_doc = {
        "schema": SCHEMA,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": os.path.abspath(corpus),
        "tree": tree,
        # The assignment rule travels with the plan: a later round MUST use the
        # same n_shards or the buckets mean something different.
        "assignment": {
            "hash": "sha1", "key": "pmcid", "bits": 64, "n_shards": n_shards,
            "formula": "int(sha1(pmcid).hexdigest()[:16], 16) % n_shards",
        },
        "counts": stats,
        "distribution": report,
        "skip_report": os.path.abspath(skip_path),
        "shards": entries,
    }
    plan_path = os.path.join(out_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan_doc, fh, indent=2, sort_keys=True)
    return plan_path


def print_summary(stats: dict, report: dict, n_shards: int, target_chars: int) -> None:
    c = stats["skips"]
    print(f"manifest rows      {stats['n_manifest_rows']:>10,}"
          f"   (duplicate pmcid rows: {stats['n_duplicate_pmcid_rows']:,})")
    print(f"excluded           {stats['n_excluded']:>10,}")
    print(f"skipped            {stats['n_skipped']:>10,}   "
          + "  ".join(f"{k}={c[k]:,}" for k in SKIP_REASONS))
    if stats["n_failures_not_in_manifest"]:
        print(f"  (+{stats['n_failures_not_in_manifest']:,} pmcids in failures.jsonl "
              "were never fetched, so are not manifest rows)")
    print(f"planned            {stats['n_planned']:>10,}")
    if not report["n_shards_nonempty"]:
        print("nothing to plan.")
        return
    w = report["work_chars_per_shard"]
    e = report["estimates"]
    print(f"shards             {report['n_shards_nonempty']:>10,} non-empty "
          f"of {n_shards} buckets")
    print(f"work chars         {report['total_work_chars'] / 1e9:>10.2f} GB total, "
          f"{w['mean'] / 1e6:.2f} M/shard")
    print(f"  spread           min {w['min'] / 1e6:.2f}M  p50 {w['p50'] / 1e6:.2f}M  "
          f"p90 {w['p90'] / 1e6:.2f}M  p99 {w['p99'] / 1e6:.2f}M  max {w['max'] / 1e6:.2f}M")
    print(f"  CV {w['cv_pct']}%   worst/mean {w['max_over_mean']}   "
          f"over --target-chars ({target_chars / 1e6:.1f}M): {report['n_over_target']}")
    print(f"est per shard      {e['est_chunks_per_shard_mean']:,} chunks, "
          f"{e['est_embed_sec_per_shard_mean']}s embed, "
          f"{e['est_embedding_bytes_per_shard_mean'] / 1e9:.2f} GB embedding file")
    print(f"est total          {e['est_chunks_total']:,} chunks, "
          f"{e['est_embedding_bytes_total'] / 1e12:.2f} TB of intermediate "
          "embedding files")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", required=True,
                   help="harvest root holding clean/ (or xml/) and manifest.jsonl")
    p.add_argument("--out", required=True,
                   help="directory for shard-NNNNN.jsonl + plan.json + skipped.jsonl")
    p.add_argument("--tree", default="clean",
                   help="subtree holding the XML (default clean/, the --strip-back tree)")
    p.add_argument("--shards", type=int, default=2048,
                   help="number of shard buckets — a POWER OF TWO, sized for the "
                        "FINAL harvest, never derived from today's count (see the "
                        "module docstring). Doubling it splits shards cleanly")
    p.add_argument("--target-chars", type=int, default=20_000_000,
                   help="advisory work-chars budget per shard; shards above it are "
                        "counted in the report. Does NOT affect assignment (default "
                        "20M ~= 14k chunks ~= 0.75 GB embedding file)")
    p.add_argument("--exclude", action="append", default=[], metavar="PATH",
                   help="already planned/ingested pmcids to leave out. A previous "
                        "plan DIRECTORY (its shard-*.jsonl, not its skipped.jsonl), "
                        "a JSONL with a pmcid field, or a plain one-per-line list. "
                        "Repeatable")
    p.add_argument("--min-work-chars", type=int, default=1,
                   help="skip articles with fewer body+float chars than this "
                        "(default 1: an article with no text at all is a skip, "
                        "reported, not a silently empty worker input)")
    p.add_argument("--limit", type=int, default=None,
                   help="plan only the N lowest-hash articles (prototype runs). "
                        "Hash-ordered, not manifest-ordered — but note this is the "
                        "one selection whose membership shifts as the corpus grows")
    p.add_argument("--skip-report", default=None,
                   help="where to write the skipped articles (default <out>/skipped.jsonl)")
    p.add_argument("--no-verify-files", dest="verify_files", action="store_false",
                   help="trust the manifest instead of stat-ing every article. Fast "
                        "but it disables the missing/zero-byte skip reasons — the "
                        "full harvest only costs ~5s, so prefer the default")
    p.add_argument("--force", action="store_true",
                   help="overwrite an --out that already holds shard files (a resume "
                        "should normally target a NEW directory instead)")
    p.add_argument("--dry-run", action="store_true",
                   help="report the plan (shard count, distribution, skips) and "
                        "write nothing")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.shards < 1 or args.shards & (args.shards - 1):
        raise SystemExit(
            f"--shards {args.shards} is not a power of two. Powers of two make a "
            "later doubling a clean split of each shard instead of a reshuffle of "
            "the whole corpus."
        )
    existing = glob.glob(os.path.join(args.out, "shard-*.jsonl"))
    if existing and not args.dry_run and not args.force:
        raise SystemExit(
            f"{args.out} already holds {len(existing)} shard file(s). Plan the next "
            "round into a NEW directory with --exclude pointing at this one, or pass "
            "--force to overwrite."
        )

    exclude = read_exclusions(args.exclude)
    if args.exclude:
        print(f"excluding {len(exclude):,} pmcid(s) from "
              f"{len(args.exclude)} source(s)", flush=True)

    shards, skipped, stats = plan(
        args.corpus, n_shards=args.shards, tree=args.tree, exclude=exclude,
        verify_files=args.verify_files, min_work_chars=args.min_work_chars,
        limit=args.limit,
    )
    report = shard_report(shards, args.target_chars)
    print_summary(stats, report, args.shards, args.target_chars)

    if args.dry_run:
        print("dry run — nothing written", flush=True)
        return 0
    plan_path = write_plan(args.out, args.corpus, shards, skipped, stats, report,
                           n_shards=args.shards, tree=args.tree,
                           skip_report=args.skip_report)
    print(f"wrote {report['n_shards_nonempty']} shard file(s) + "
          f"{stats['n_skipped']} skip record(s) → {plan_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
