#!/usr/bin/env python
"""Turn a committed study package into a ``GradingBatchCreateRequest`` JSON body.

This writes a FILE. Posting it is the admin's job::

    curl -sS -X POST "$BASE/v1/grading/batches" \\
         -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \\
         --data-binary @batch.json

The API deliberately reads no files (``contracts/openapi.yaml``: "the importer
builds the body from the committed study package; the API reads no files"), so
the study's inputs never have to be mounted into a server, and the body that
created a read is a reviewable artifact.

Two subcommands, one per package:

``pilot``
    ``docs/plans/results/stage0/artifacts/rdev-pilot-read/pilot_data_r3.json``
    → 10 tasks. That file already carries the segmented units, the topic and
    the judges' evidence sets, so this is a transcription with no lookups.

``rdev``
    the full R-dev draw: ``artifacts/rdev_sample.json`` (100 pairs) × the r3.1
    label files × the segmented corpus (``docs.jsonl`` + ``units.jsonl``, the
    same ``segment`` the labeling harness used) × the TREC CDS topics. The
    claims are the UNION of both judges' evidence sets, deduplicated by
    ``SPEC-confirmation-run.md`` D3 rule 1 (character-span union Jaccard ≥ 0.5,
    the smaller set retained as canonical), with ``sources`` naming every judge
    that produced the merged set.

Both stamp ``rubric_sha256`` from the frozen rubric file and default
``order_seed`` to ``SEED_RDEV`` (20260915) — the R-dev draw's seed — so the
per-reader orders the UI computes are the orders
``RDEV-readsheet-A/B.html`` already show, and a read begun on those sheets
continues in the UI at the same position.

Examples
--------
The pilot, for two keyed readers on the dev tenant::

    python python/scripts/grading_import.py pilot \\
      --readers @service:reader-a @service:reader-b \\
      --name 'R-dev pilot r3' -o /tmp/pilot-batch.json

The full draw, presentation 0 of each judge::

    python python/scripts/grading_import.py rdev \\
      --topics ~/Development/worktrees/phase0-rescue/phase0/cds/topics_merged.json \\
      --readers @service:reader-a @service:reader-b \\
      --name 'R-dev' -o /tmp/rdev-batch.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

# ``python/scripts/`` → ``python/`` → the repo root.
REPO = pathlib.Path(__file__).resolve().parents[2]
STAGE0 = REPO / "docs" / "plans" / "results" / "stage0"
DESIGN = REPO / "docs" / "plans" / "results" / "design"

DEFAULT_PILOT = STAGE0 / "artifacts" / "rdev-pilot-read" / "pilot_data_r3.json"
DEFAULT_SAMPLE = STAGE0 / "artifacts" / "rdev_sample.json"
DEFAULT_LABELS = [
    STAGE0 / "artifacts" / "r31" / "labels-r31-scout.jsonl",
    STAGE0 / "artifacts" / "r31" / "labels-r31-qwen.jsonl",
]
DEFAULT_RUBRIC = DESIGN / "RUBRIC-evidence.md"
#: Large artifacts live off the NFS home (MEMORY: /home is space-constrained);
#: this mirrors ``s0_common.BIG``/``WORK``.
DEFAULT_WORK = pathlib.Path("/rag/tmp/stage0-conf/work")

#: ``s0_common.SEED_RDEV`` — the R-dev stratified human-read draw's seed, and the
#: seed ``s0_rdev.py`` shuffled the two readsheets with (``+1`` for A, ``+2`` for
#: B). ``GradingBatch``'s order rule is ``order_seed + k + 1``, so passing this
#: value reproduces those two orders exactly.
SEED_RDEV = 20260915

#: D3 rule 1's threshold (``SPEC-confirmation-run.md`` §6.2.1), the same number
#: the §6.4 rule-4 self-consistency check uses. One number governs both.
D3_JACCARD = 0.5


# --------------------------------------------------------------------------- #
# Segmentation — copied, with provenance
# --------------------------------------------------------------------------- #
def segment(text: str, units: list[dict]) -> list[tuple[int, str, list[tuple]]]:
    """``[(unit_i, unit_title, [(sent_idx, start, end, sent_text)])]``.

    Copied verbatim from ``docs/plans/results/stage0/s0_label.py::segment`` (the
    function the labeling harness and ``s0_rdev.py`` both use) rather than
    imported: ``s0_label`` imports ``s0_common`` at module scope, which creates
    directories under ``/rag/tmp`` on import and pins a host layout this script
    must not require. The body is five lines and must not drift — the sentence
    numbering here IS the numbering the labels' ``first_sentence`` /
    ``last_sentence`` refer to.
    """
    from ragstack.ingestion.chunkers import sentence_spans

    out = []
    for u in units:
        seg = text[u["start_char"]:u["end_char"]]
        sp = sentence_spans(seg)
        sents = [
            (k, u["start_char"] + a, u["start_char"] + b, seg[a:b].strip())
            for k, (a, b) in enumerate(sp)
            if seg[a:b].strip()
        ]
        out.append((u["i"], u["title"] or u["cls"], sents))
    return out


def _document_json(
    docno: str, title: str, seg: list[tuple[int, str, list[tuple]]]
) -> tuple[dict, dict[int, int], list[dict[int, int]]]:
    """A ``GradingDocument`` plus the index maps that make the labels addressable.

    ``GradingDocument`` requires a unit's ``index`` and a sentence's ``i`` to
    EQUAL their position in the array. ``segment`` does not produce that: its
    sentence key is ``enumerate``'s ``k`` over *all* candidate spans, and blank
    ones are dropped afterwards, so a unit containing a blank has a GAP in its
    numbering — and ``s0_rdev.py``'s readsheet prints that same gapped ``k``
    (``[{si}] …``), which is the space the r3.1 labels' ``first_sentence`` /
    ``last_sentence`` live in.

    So the document is renumbered by position and the caller is handed the maps
    to translate the labels with. Renumbering silently, without translating,
    would slide every span in a gapped unit by one or more sentences — a
    highlight that lands on the wrong text, which is the single worst thing this
    importer could do to a read.

    Returns ``(document, unit_map, sentence_maps)``: ``unit_map`` takes a
    label's unit id to its position, ``sentence_maps[position]`` takes a
    label's sentence key to its position within that unit.
    """
    units = []
    unit_map: dict[int, int] = {}
    sentence_maps: list[dict[int, int]] = []
    for pos, (ui, unit_title, sents) in enumerate(seg):
        unit_map[ui] = pos
        sentence_maps.append({k: j for j, (k, _a, _b, _s) in enumerate(sents)})
        units.append(
            {
                "index": pos,
                "title": unit_title or "",
                "sentences": [{"i": j, "text": s} for j, (_k, _a, _b, s) in enumerate(sents)],
            }
        )
    return {"doc_id": docno, "title": title, "units": units}, unit_map, sentence_maps


# --------------------------------------------------------------------------- #
# D3 rule 1 — within-document merge
# --------------------------------------------------------------------------- #
def _char_len(spans: list[dict]) -> int:
    return sum(max(0, s["end"] - s["start"]) for s in spans)


def _overlap(a: list[dict], b: list[dict]) -> int:
    """Total character overlap between two span lists, treated as interval sets."""
    total = 0
    for x in a:
        for y in b:
            total += max(0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
    return total


def _jaccard(a: list[dict], b: list[dict]) -> float:
    """Character-span union Jaccard. Spans within one set are assumed disjoint —
    they are, by D1 (a span never crosses a ``<sec>`` boundary and the labeler
    emits them in reading order) — so |A∩B| is the pairwise overlap sum."""
    inter = _overlap(a, b)
    union = _char_len(a) + _char_len(b) - inter
    return inter / union if union else 0.0


def _merge_sets(candidates: list[tuple[str, list[dict]]]) -> list[tuple[list[str], list[dict]]]:
    """D3 rule 1, applied to ``(judge, spans)`` candidates for ONE document.

    Two sets are one unit iff their character-span union Jaccard ≥ 0.5; the
    merged unit keeps the **smaller** set's spans as its canonical list, so
    merging can never make a unit easier to cover. ``sources`` is every judge
    that contributed to the group.

    Greedy single-pass agglomeration against each group's current canonical
    list. The spec fixes the threshold and the canonical-list rule, not a
    clustering algorithm; this is the reading ``s0_labelgates`` uses and it is
    deterministic in candidate order, which the caller fixes (judge file order,
    then presentation, then set index).
    """
    groups: list[dict[str, Any]] = []
    for judge, spans in candidates:
        if not spans:
            continue
        for g in groups:
            if _jaccard(g["spans"], spans) >= D3_JACCARD:
                if judge not in g["sources"]:
                    g["sources"].append(judge)
                if _char_len(spans) < _char_len(g["spans"]):
                    g["spans"] = spans  # intersection-preserving: keep the smaller
                break
        else:
            groups.append({"sources": [judge], "spans": spans})
    return [(g["sources"], g["spans"]) for g in groups]


# --------------------------------------------------------------------------- #
# Shared assembly
# --------------------------------------------------------------------------- #
def _unique(values: list[str]) -> list[str]:
    """Order-preserving deduplication — ``sources`` is ``uniqueItems``."""
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _span_text(doc: dict, unit: int, first: int, last: int) -> str:
    """The span as the reader will see it: the sentences it covers, joined.

    The contract keeps ``text`` so the claimed answer can be shown even where it
    disagrees with the segmentation; the UI highlights by the sentence range,
    not by this string. Building it from the same sentences the range names
    keeps the two consistent by construction.
    """
    sentences = doc["units"][unit]["sentences"]
    return " ".join(s["text"] for s in sentences[first : last + 1])


def _translate(
    span: dict, unit_map: dict[int, int], sentence_maps: list[dict[int, int]], where: str
) -> tuple[int, int, int]:
    """A label's (unit, first, last) in ``segment``'s key space → array positions."""
    unit = unit_map.get(span["unit"])
    if unit is None:
        raise SystemExit(
            f"{where}: span names unit {span['unit']}, which this segmentation does "
            f"not produce (it has {sorted(unit_map)}). The labels and this "
            "segmentation disagree — do not import a read the reader cannot see."
        )
    smap = sentence_maps[unit]
    out = []
    for which in ("first_sentence", "last_sentence"):
        pos = smap.get(span[which])
        if pos is None:
            raise SystemExit(
                f"{where}: span's {which} {span[which]} is not a sentence of unit "
                f"{span['unit']} in this segmentation (it has {sorted(smap)})."
            )
        out.append(pos)
    return unit, out[0], out[1]


def _check_span(doc: dict, unit: int, first: int, last: int, where: str) -> None:
    if unit >= len(doc["units"]):
        raise SystemExit(
            f"{where}: span names unit {unit}, but the segmented document has "
            f"{len(doc['units'])} unit(s). The labels and this segmentation "
            "disagree — do not import a read the reader cannot see."
        )
    n = len(doc["units"][unit]["sentences"])
    if not 0 <= first <= last < n:
        raise SystemExit(
            f"{where}: span names sentences {first}..{last} of unit {unit}, which "
            f"has {n}. The labels and this segmentation disagree."
        )


def _batch(args: argparse.Namespace, tasks: list[dict]) -> dict:
    rubric = pathlib.Path(args.rubric)
    if not rubric.exists():
        raise SystemExit(f"rubric not found: {rubric}")
    return {
        "name": args.name,
        "kind": args.kind,
        "rubric_sha256": hashlib.sha256(rubric.read_bytes()).hexdigest(),
        "order_seed": args.order_seed,
        "readers": list(args.readers),
        "tasks": tasks,
    }


def _write(args: argparse.Namespace, body: dict) -> None:
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    n_claims = sum(len(t["claims"]) for t in body["tasks"])
    print(
        f"[grading-import] {len(body['tasks'])} task(s), {n_claims} evidence set(s), "
        f"readers={body['readers']}, order_seed={body['order_seed']}, "
        f"rubric_sha256={body['rubric_sha256'][:16]}… -> {args.out}",
        file=sys.stderr,
    )


# --------------------------------------------------------------------------- #
# pilot
# --------------------------------------------------------------------------- #
def build_pilot(args: argparse.Namespace) -> dict:
    """The ten-pair pilot. ``pilot_data_r3.json`` already carries the segmented
    units, the topic and each judge's sets, so nothing is looked up."""
    data = json.loads(pathlib.Path(args.pilot).read_text(encoding="utf-8"))
    titles = _doc_titles(args.docs) if args.docs else {}

    tasks = []
    for pair in data["pairs"]:
        docno = pair["pair_id"].split("__", 1)[-1]
        doc = {
            "doc_id": docno,
            # The pilot package carries no title (the sheet showed none). With
            # --docs it comes from the corpus; otherwise the UI shows the units.
            "title": titles.get(docno, ""),
            "units": [
                {
                    "index": u["index"],
                    "title": u["title"] or "",
                    "sentences": [{"i": s["i"], "text": s["text"]} for s in u["sentences"]],
                }
                for u in pair["units"]
            ],
        }
        claims = []
        for i, st in enumerate(pair["sets"], 1):
            spans = []
            for j, sp in enumerate(st["spans"], 1):
                where = f"{pair['pair_id']} set {i} span {j}"
                _check_span(doc, sp["unit"], sp["first"], sp["last"], where)
                spans.append(
                    {
                        "unit": sp["unit"],
                        "first_sentence": sp["first"],
                        "last_sentence": sp["last"],
                        # The labeler's own quote, verbatim — this package has it.
                        "text": sp["text"],
                    }
                )
            # `sources` is uniqueItems in the contract, and the pilot package
            # does carry a set tagged ['scout', 'scout'] (one judge, two
            # presentations that produced it). Deduplicate, keeping tag order.
            claims.append(
                {"set_index": i, "spans": spans, "sources": _unique(st["judges"])}
            )
        topic = pair["topic"]
        tasks.append(
            {
                "pair_id": pair["pair_id"],
                "stratum": pair["stratum"],
                "question": {
                    "id": topic["id"],
                    "type": topic["type"],
                    "summary": topic["summary"],
                    "description": topic["description"],
                },
                "document": doc,
                "claims": claims,
                "extra_questions": [],
            }
        )
    return _batch(args, tasks)


# --------------------------------------------------------------------------- #
# rdev
# --------------------------------------------------------------------------- #
def _doc_titles(path: str | pathlib.Path) -> dict[str, str]:
    titles = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                titles[r["docno"]] = r.get("title", "")
    return titles


def _load_corpus(
    docs_path: pathlib.Path, units_path: pathlib.Path, wanted: set[str]
) -> tuple[dict[str, str], dict[str, str], dict[str, list[dict]]]:
    """Text, title and units for just the documents the draw names.

    ``docs.jsonl`` is ~700 MB; loading it whole would cost minutes and gigabytes
    for a 100-pair read, so this filters as it streams.
    """
    text: dict[str, str] = {}
    title: dict[str, str] = {}
    units: dict[str, list[dict]] = {}
    with open(docs_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r["docno"] in wanted:
                text[r["docno"]] = r["text"]
                title[r["docno"]] = r.get("title", "")
    with open(units_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r["docno"] in wanted:
                units[r["docno"]] = r["units"]
    return text, title, units


def build_rdev(args: argparse.Namespace) -> dict:
    sample = json.loads(pathlib.Path(args.sample).read_text(encoding="utf-8"))
    topics = json.loads(pathlib.Path(args.topics).read_text(encoding="utf-8"))
    pairs = sample["pairs"]
    wanted = {p["docno"] for p in pairs}

    # (topic, docno) -> [(judge, presentation, set_index, spans)], in a fixed
    # order: label-file order, then presentation, then the judge's own set order.
    labels: dict[tuple[str, str], list[tuple[str, list[dict]]]] = {}
    for path in args.labels:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
        rows.sort(key=lambda r: (r["topic"], r["docno"], r.get("presentation", 0)))
        for r in rows:
            if r.get("dropped"):
                continue
            if not args.all_presentations and r.get("presentation", 0) != args.presentation:
                continue
            judge = r.get("judge") or pathlib.Path(path).stem
            for st in r.get("sets", []):
                labels.setdefault((r["topic"], r["docno"]), []).append((judge, st["spans"]))

    docs_text, docs_title, docs_units = _load_corpus(
        pathlib.Path(args.docs), pathlib.Path(args.units), wanted
    )

    tasks = []
    for p in pairs:
        topic_id, docno = p["topic"], p["docno"]
        pair_id = f"{topic_id}__{docno}"
        if docno not in docs_text or docno not in docs_units:
            raise SystemExit(
                f"{pair_id}: document {docno} is not in {args.docs} / {args.units}. "
                "Point --docs/--units at the corpus the labels were produced against."
            )
        seg = segment(docs_text[docno], docs_units[docno])
        doc, unit_map, sentence_maps = _document_json(
            docno, docs_title.get(docno, ""), seg
        )

        claims = []
        for i, (sources, spans) in enumerate(
            _merge_sets(labels.get((topic_id, docno), [])), 1
        ):
            out_spans = []
            for j, sp in enumerate(spans, 1):
                where = f"{pair_id} set {i} span {j}"
                unit, first, last = _translate(
                    sp, unit_map, sentence_maps, where
                )
                _check_span(doc, unit, first, last, where)
                out_spans.append(
                    {
                        "unit": unit,
                        "first_sentence": first,
                        "last_sentence": last,
                        "text": _span_text(doc, unit, first, last),
                    }
                )
            claims.append({"set_index": i, "spans": out_spans, "sources": sorted(sources)})

        top = topics.get(topic_id)
        if top is None:
            raise SystemExit(f"{pair_id}: topic {topic_id!r} is not in {args.topics}")
        fields = top.get("fields", {})
        tasks.append(
            {
                "pair_id": pair_id,
                "stratum": p["stratum"],
                "question": {
                    "id": topic_id,
                    "type": top.get("type", ""),
                    "summary": fields.get("summary", ""),
                    "description": fields.get("description", ""),
                },
                "document": doc,
                "claims": claims,
                "extra_questions": [],
            }
        )
    return _batch(args, tasks)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _common(sp: argparse.ArgumentParser, default_name: str) -> None:
    sp.add_argument(
        "--readers", nargs="+", required=True,
        help="Readers in LABEL order (first = A). Same vocabulary as a share "
             "grantee minus the group forms: '@service:<subject>' for a keyed "
             "principal (keeps the subject colon-free), a full 'issuer:subject', "
             "or a bare BV-BRC username (qualified to 'bvbrc:<name>').",
    )
    sp.add_argument("--name", default=default_name, help="Batch name shown in the UI.")
    sp.add_argument(
        "--kind", default="evidence-read",
        choices=["evidence-read", "pointed-read", "citation-feedback"],
    )
    sp.add_argument(
        "--order-seed", type=int, default=SEED_RDEV,
        help=f"Seed the per-reader orders derive from (default {SEED_RDEV}, "
             "SEED_RDEV — the seed the RDEV readsheets were shuffled with).",
    )
    sp.add_argument("--rubric", default=str(DEFAULT_RUBRIC), help="Frozen rubric to hash.")
    sp.add_argument("-o", "--out", default="-", help="Output path, or '-' for stdout.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pilot = sub.add_parser("pilot", help="the ten-pair R-dev pilot")
    pilot.add_argument("--pilot", default=str(DEFAULT_PILOT))
    pilot.add_argument(
        "--docs", default=None,
        help="Optional docs.jsonl, only to fill in document titles (the pilot "
             "package carries none).",
    )
    _common(pilot, "R-dev pilot r3")

    rdev = sub.add_parser("rdev", help="the full R-dev draw")
    rdev.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    rdev.add_argument("--labels", nargs="+", default=[str(p) for p in DEFAULT_LABELS])
    rdev.add_argument("--docs", default=str(DEFAULT_WORK / "docs.jsonl"))
    rdev.add_argument("--units", default=str(DEFAULT_WORK / "units.jsonl"))
    rdev.add_argument(
        "--topics", required=True,
        help="topics_merged.json (the TREC CDS topics; s0_common.CDS). Not "
             "committed — pass the path on the study host.",
    )
    rdev.add_argument(
        "--presentation", type=int, default=0,
        help="Which presentation of each judge's r3.1 labels to take (default 0).",
    )
    rdev.add_argument(
        "--all-presentations", action="store_true",
        help="Take every presentation instead, deduplicated by D3 rule 1 "
             "(character-span union Jaccard >= 0.5). The union is larger and "
             "saturated; presentation 0 is the single-shot reading.",
    )
    _common(rdev, "R-dev")

    args = ap.parse_args(argv)
    body = build_pilot(args) if args.cmd == "pilot" else build_rdev(args)
    _write(args, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
