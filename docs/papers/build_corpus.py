#!/usr/bin/env python3
"""Build a section-respecting, metadata-carrying corpus for the dev-tenant collection.

WHY THIS EXISTS
---------------
No chunker in this deployment is section-aware — `grep -i section` over
`ragstack/ingestion/chunkers.py` returns nothing, and the six methods (`fixed`,
`fixed_token`, `sentence`, `words`, `semantic`, `semantic_pooled`) all slide a window
over flat text. So "respect text sections" has to happen *before* ingest.

This splits each source document at its markdown headings and emits **one file per
section**, with the heading path carried into the text. That is deliberately the
`header512` arm of this project's own confirmation run — the one contrast in the
confirmatory family with adequate power (joint power 0.95 against 0.37–0.56 for the
rest) — so the collection is built the way our own evidence says to build it.

Each emitted file opens with a metadata block that survives chunking because it sits in
the text: source path, document kind, the area it belongs to, the heading path, and the
section's position in its parent. A `fixed_token` window at 512 tokens with **zero
overlap** then cuts inside a section rather than across two, and every chunk that lands
in the first window carries the headings that locate it.

Zero overlap is not a default here, it is a finding: on two independent legs, at four
chunk sizes and two metrics, overlap bought a recall@100 change of exactly 0.0000 for up
to 1.32x the vectors.

USAGE
-----
    /rag/envs/ragstack/bin/python3 docs/papers/build_corpus.py --out /tmp/corpus
    /rag/envs/ragstack/bin/python3 docs/papers/build_corpus.py --out /tmp/corpus --manifest-only

Writes only under --out. Reads only committed repository files.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

# (glob, kind, area) — kind and area become searchable metadata on every section.
SOURCES = [
    ("docs/papers/bib-inbox/*.md",                     "literature-survey", "related work"),
    ("docs/papers/bibliography.md",                    "bibliography",      "related work"),
    ("docs/papers/README.md",                          "process",           "publication track"),
    ("docs/papers/paper-a-evidence-localisation/OUTLINE.md", "paper-outline", "paper A"),
    ("docs/plans/results/stage0/RESULTS-*.md",         "results",           "confirmation run"),
    ("docs/plans/results/design/SPEC-confirmation-run-r3.md", "specification", "pre-registration"),
    ("docs/plans/results/design/SPEC-synthesis-stage.md",     "specification", "synthesis stage"),
    ("docs/plans/results/design/REVIEW-synthesis-stage.md",   "review",        "synthesis stage"),
    ("docs/plans/results/design/PLAN-hard-pointed-set.md",    "plan",          "pointed population"),
    ("docs/plans/results/design/RUBRIC-evidence.md",   "rubric",            "labeling protocol"),
    ("docs/plans/results/RUN-PACKAGE.md",              "run-package",       "reproducibility"),
    ("docs/plans/results/README.md",                   "index",             "confirmation run"),
    ("HANDOFF-2026-09-09.md",                          "status",            "project status"),
]

HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
MIN_CHARS = 200          # below this a section is folded into the next one
MAX_CHARS = 60_000       # above this a section is split on paragraph boundaries


def sections(text: str):
    """Split markdown into (heading_path, body) at headings, keeping the path."""
    lines = text.splitlines()
    path: list[str] = []
    cur_path: tuple[str, ...] = ()
    buf: list[str] = []
    out: list[tuple[tuple[str, ...], str]] = []
    for line in lines:
        m = HEADING.match(line)
        if m:
            if buf and "".join(buf).strip():
                out.append((cur_path, "\n".join(buf).strip()))
            buf = []
            level, title = len(m.group(1)), m.group(2).strip()
            path = path[: level - 1] + [title]
            cur_path = tuple(path)
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        out.append((cur_path, "\n".join(buf).strip()))
    return out


def coalesce(secs):
    """Fold sections shorter than MIN_CHARS into the following one, and split
    sections longer than MAX_CHARS at blank lines. Keeps every section a unit a
    reader would recognise while bounding the extremes."""
    merged = []
    carry_path, carry_body = None, ""
    for p, b in secs:
        if carry_body:
            b = carry_body + "\n\n" + b
            p = carry_path or p
            carry_path, carry_body = None, ""
        if len(b) < MIN_CHARS:
            carry_path, carry_body = p, b
            continue
        merged.append((p, b))
    if carry_body:
        if merged:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n\n" + carry_body)
        else:
            merged.append((carry_path or (), carry_body))
    out = []
    for p, b in merged:
        if len(b) <= MAX_CHARS:
            out.append((p, b)); continue
        piece, n = [], 0
        for para in b.split("\n\n"):
            if n + len(para) > MAX_CHARS and piece:
                out.append((p, "\n\n".join(piece))); piece, n = [], 0
            piece.append(para); n += len(para) + 2
        if piece:
            out.append((p, "\n\n".join(piece)))
    return out


def slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s[:n] or "section").strip("-")


def emit_jsonl(dest: pathlib.Path) -> int:
    """One record per source document: {"text", "path", "metadata"}.

    Why per-document and not per-section: `delete_prior` replaces per `doc_id`, so two
    records sharing a path would delete each other, and `link_neighbors_by_document`
    groups by `doc_id`, so one record per section restarts `chunk_index` at every
    heading and never links across one. Order was the stated requirement, so order wins
    here; sections wait for a section-aware chunker and a new collection (the chunk
    strategy is fixed at collection creation and cannot be edited later).

    Metadata uses only names declared in `contracts/schemas/chunk_metadata.json`, which
    is enforced at the ingest boundary (#603/#604). Nothing invented: an undeclared
    field is accepted but gets no ES mapping and is typed by whichever document lands
    first.
    """
    import json as _json
    n, total = 0, 0
    with dest.open("w", encoding="utf-8") as fh:
        for pattern, kind, area in SOURCES:
            for src in sorted(REPO.glob(pattern)):
                if not src.is_file():
                    continue
                rel = src.relative_to(REPO).as_posix()
                text = src.read_text(encoding="utf-8", errors="replace")
                rec = {
                    "text": text,
                    "path": rel,
                    "metadata": {
                        "title": src.stem,
                        "source": rel,
                        "source_path": rel,
                        "doc_type": kind,
                        "keywords": "; ".join([kind, area, "ragstack", "chunking-study"]),  # enrich.split_keywords
                        # expects a DELIMITED STRING, not a list — a list raises TypeError in re.split.
                        "publisher": "wilke/ragstack",
                        "content_type": "text/markdown",
                    },
                }
                fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1; total += len(text)
    print(f"{n} document records -> {dest} ({total/1e6:.2f} MB of text)")
    print("NOTE: section boundaries are NOT respected in this shape; see "
          "docs/plans/section-taxonomy.md for why and what would fix it.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/corpus")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--jsonl", metavar="FILE",
                    help="emit ONE RECORD PER DOCUMENT for scripts/ingest_jsonl.py instead of "
                         "one file per section. This is the shape that preserves document "
                         "ordering: ingest_jsonl gives each line its own doc_id and "
                         "link_neighbors_by_document groups by doc_id, so sections of one "
                         "source must share a record or their chunk_index restarts and the "
                         "prev/next links never cross a heading. Section boundaries are NOT "
                         "respected in this mode — that needs the unimplemented section-aware "
                         "chunker (docs/plans/section-taxonomy.md).")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

    if a.jsonl:
        return emit_jsonl(pathlib.Path(a.jsonl))
    manifest, n_files, n_sections = [], 0, 0
    for pattern, kind, area in SOURCES:
        for src in sorted(REPO.glob(pattern)):
            if not src.is_file():
                continue
            rel = src.relative_to(REPO).as_posix()
            text = src.read_text(encoding="utf-8", errors="replace")
            secs = coalesce(sections(text))
            n_files += 1
            for i, (path, body) in enumerate(secs, 1):
                heading_path = " > ".join(path) if path else "(preamble)"
                title = path[-1] if path else src.stem
                meta = {
                    "source_file": rel, "kind": kind, "area": area,
                    "heading_path": heading_path, "section_title": title,
                    "section_index": i, "sections_in_document": len(secs),
                    "document_title": src.stem, "repository": "wilke/ragstack",
                }
                # The metadata block and the heading path are part of the TEXT, so a
                # 512-token window with no overlap carries them into its first chunk.
                doc = (
                    f"<!-- ragstack-metadata\n{json.dumps(meta, indent=1)}\n-->\n\n"
                    f"# {src.stem}\n\n"
                    f"**Section:** {heading_path}\n"
                    f"**Source:** `{rel}` · section {i} of {len(secs)} · {kind} · {area}\n\n"
                    f"---\n\n{body}\n"
                )
                name = f"{slug(src.stem, 40)}__{i:03d}__{slug(title)}.md"
                if not a.manifest_only:
                    (out / name).write_text(doc, encoding="utf-8")
                manifest.append({"file": name, "bytes": len(doc.encode()), **meta})
                n_sections += 1

    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    total = sum(m["bytes"] for m in manifest)
    print(f"{n_files} source documents -> {n_sections} section files, {total/1e6:.2f} MB")
    by_kind: dict[str, int] = {}
    for m in manifest:
        by_kind[m["kind"]] = by_kind.get(m["kind"], 0) + 1
    for k, v in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"  {v:>4}  {k}")
    print(f"manifest: {out/'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
