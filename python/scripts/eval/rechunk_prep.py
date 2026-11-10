#!/usr/bin/env python
"""Rechunk stage 1 (exploratory): the offline preparation of the alias corpora under the chunker of
the confirmatory boundary test, and the private label table the stage-1 report reads.

    rechunk_prep.py add-structure --in RECORDS.jsonl --out RECORDS.structured.jsonl [--summary S.json]
    rechunk_prep.py export --records R1.jsonl [R2.jsonl ...] --out CHUNKS.jsonl [--summary S.json] \\
                           [--expect-chunks N]
    rechunk_prep.py gold-map --sample sample.jsonl --pairs-seed pairs-seed.jsonl \\
                             --pairs-seed-cover pairs-seed-cover.jsonl --control-final judge-control/final.jsonl \\
                             --cover-final judge-cover/final.jsonl --stage-a-runs RUNS_DIR --out TABLE.jsonl \\
                             [--summary S.json] [--extra-final F.jsonl ... [--allow-relabel]]
    rechunk_prep.py stage1b-seed --label-table TABLE.jsonl --runs RUNS_DIR [--retriever base] \\
                                 --export E.jsonl [--export E2.jsonl ...] --out-seed SEED.jsonl \\
                                 --out-export X.jsonl --summary S.json

**The chunker.** ``sentence`` / 512 / 0 with the confirmatory control's params
(:data:`alias_control_chunks.CONTROL_PARAMS`: tokens, pack 318, ``structure_cuts`` sections/1,
tolerance 0.25, min piece 128). The section cuts need each document's structure, and the alias
records' ``path`` (``PMC…#…``) has no ``.md`` suffix, so ``attach_structure`` picks no adapter for
them. Changing the path would change every doc and chunk id, so ``add-structure`` gives each record
a top-level ``structure`` key instead, built by the ``.md`` ingest adapter (``markdown/1``) over the
record's own text, in the form ``structure.structure_from_record`` reads. Path, doc id and text are
never touched; a text sha that changes between input and output is refused, and nothing is written.

**export.** The new chunks of the records, produced as the GoWe embed step produces them:
``embed_shard._build_pipeline`` with a registry-like entry carrying the params (the markers and the
#831 tolerance are read from it by the same readers), then ``IngestionPipeline.prepare_source`` per
records file. No embedding call is made: the embedder is built on a client whose transport refuses
every request, and the model window is passed (``--chunk-max-tokens``) so nothing is
auto-detected. The rows are the alias chunk-export rows (``chunk_id``, ``doc_id``, ``content``,
``start_char``, ``end_char``, ``metadata`` with ``source_path`` and ``title``), as
``alias_study_common.read_chunk_exports`` reads them. Glossary attachment is off by default: the
glossary is its own field and changes no chunk id, span or content.

**gold-map.** The private label table (one JSON object per line, ASCII-escaped):

- one ``pair`` row per confirmatory pair: the stage-A pair id, record id, stage-A window id, arm
  group, the draw stratum and the **corpus arm** the question is run in for this pair, the max-overlap control chunk
  id with its coverage, the cover-any chunk ids, the control label, whether the pair was *lost*
  (control judged and not answers, the confirmatory report's rule) and, for a lost pair, its
  cover-any class (:data:`alias_control_chunks.COVER_SPLIT`);
- one ``label`` row per (record, chunk) judged in either registry: the final label (``answers`` /
  ``not`` / ``unjudged``) and the registries it came from.

The corpus arm: ``base`` and ``neighbour`` pairs are run in their own arm. A ``random`` pair is run
in the random draw whose stage-A leg retrieved its window (``--stage-a-runs``: the alias main run's
out dir, read only; the judged stage-A legs, ``alias_study_run.JUDGING_A_LEGS``). A window
retrieved in both draws (its paper is in both) takes the first draw in corpus-arm order, and is
counted. Every stage-A retrieval leg that had the window in its top 10 is kept on the pair row
(the stage-A reference the report prints beside each figure).

A base-paper window is always run in the **base** collection, even when stage A retrieved it only
in a larger arm's leg (neighbour or a random draw, whose collections hold the base papers too).
Its stage-1 query then meets less competition than the stage-A leg that found it, and its
same-arm stage-A reference (the window in the base legs' top 10) is 0. The summary counts these
pairs (``base_windows_only_in_larger_arm_legs``: base pairs with no base-arm stage-A leg).

The ``stratum`` kept on a pair row is the confirmatory **draw stratum** (the sample's
``stratum``; every confirmatory pair was drawn from the stage-A *answers* stratum), not the
stage-A judge's route or label source, which the gold-map inputs do not carry.

``--extra-final`` (repeatable; stage 1b): a further judge run's ``final.jsonl`` (rows with
``record_id``, ``chunk_id``, ``label``, ``source``). Only batch rows (``k`` starting ``batch|``)
are skipped (counted); any other row without the ids (a ``pair|`` row among them) is refused, and
so is a file that contributes no label. Its labels join the table as registry ``stage1b`` under the
same combine rule (answers if any registry says answers); disagreements are counted in the
summary's ``stage1b`` block (within the stage-1b files, and against the confirm registries).
Stage 1b judges only chunks with no label row, so a stage-1b label for a (record, chunk) that
already has one (``labels_already_judged``) is refused unless ``--allow-relabel`` is given; its use
is recorded (``stage1b.allow_relabel`` in the summary, ``allow_relabel`` in the table's meta row).
Without ``--extra-final``, table and summary are byte-identical to a run without stage 1b.

The judge files are read without repair (a torn tail is refused, never truncated). The labels
reuse ``alias_control_chunks``' helpers (``cover_labels``, ``cover_split``, ``label_of``,
``is_unjudged``) and ``alias_study_judge.pair_id``. Every path comes from the command line.

**stage1b-seed** (PLAN §4, stage 1b). For every lost pair, the top 10 of its question under one
retriever (``--retriever``, the rechunk run's condition name; ``base`` = the default retriever) in
its corpus arm's rows (``<runs>/<arm>/none.jsonl``, read and checked by ``rechunk_stage1.read_runs``,
summary checks included). Every (record, chunk) there with **no label row** in the table is a
seed; a chunk labelled ``unjudged`` is counted, not seeded. Deduplicated per (record, chunk).

- ``--out-seed``: one row per seed in the shape ``alias_study_judge.py build-control --seed-pairs``
  reads (``control_of``, ``record_id``, ``chunk_id``), with the cover-any seed rows' companions
  (``stage_a_chunk_id``, ``doc_id`` = the seed chunk's own document, ``stratum``) and
  ``window_arm`` (the arm group of the lost pair's stage-A window, *not* the chunk's arm, which
  ``build-control --any-paper`` derives from the chunk's own paper; ``build-control`` reads no
  arm from a seed row), ``pair_id``, ``corpus_arm``, ``best_rank``, ``lost_pairs_with_chunk``,
  ``same_paper_as_window``, ``stage`` = ``stage1b``. ``control_of`` and ``stage_a_chunk_id`` are
  those of the lost pair (of the same question) whose top 10 held the chunk: when several did, the
  first by pair id among those whose stage-A window lies in the chunk's paper, else the first by
  pair id.
  **build-control:** without ``--any-paper``, ``build_control_pairs`` refuses a seed whose chunk's
  paper (path root) is not its stage-A pair's ``c_paper`` (the window's paper). A top-10 chunk
  from another paper has no such stage-A pair among the lost pairs, so those rows
  (``same_paper_as_window`` false, counted as ``seeds_other_paper``) need ``alias_study_judge.py
  build-control --any-paper``, which takes the passage side (``c_paper``, ``arm``) from the chunk's
  own paper; every seed's paper then needs a chunk in a corpus arm's export.
- ``--out-export``: the export rows of exactly the seed chunks, from the ``--export`` files,
  unchanged (re-serialized ASCII-escaped only when the line is not pure ASCII); a seed chunk in no
  export, or seen with conflicting content, is refused.
- ``--summary``: counts only, input and output sha256s, provenance; no id.

Outputs are written once (an existing file with other bytes is refused) and never inside the
repository.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench", _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402

SCHEMA = {"structure": "rechunk-add-structure/1", "export": "rechunk-export/1", "gold_map": "rechunk-gold-map/1",
          "table": "rechunk-label-table/1", "stage1b_seed": "rechunk-stage1b-seed/1"}
METHOD = acc.CONTROL_METHOD
SIZE = acc.CONTROL_SIZE
OVERLAP = acc.CONTROL_OVERLAP
PARAMS: dict[str, Any] = dict(acc.CONTROL_PARAMS)
DESCRIPTOR = acc.CONTROL_DESCRIPTOR
#: the registry-like entry id the export resolves to (never a real collection; nothing is written)
SPEC_ID = "rechunk-offline-export"
#: an address that refuses every connection: the export never embeds, and nothing may reach a host
NO_HOST = "http://127.0.0.1:1"

LABELS = ("answers", "not", "unjudged")
REGISTRIES = ("control", "cover")
#: the registry name ``gold-map --extra-final`` gives the stage-1b judge runs' labels
EXTRA_REGISTRY = "stage1b"


# ------------------------------------------------------------------ small I/O helpers

def refuse_in_repo(path: Path, what: str) -> None:
    import alias_study_spot as asp

    asp.refuse_in_repo(path, what)


def read_rows(path: Path) -> list[dict[str, Any]]:
    """A JSONL file read as is: no repair, a torn line is a refusal (the private judge files are
    never rewritten here)."""
    out = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{Path(path).name}:{i}: not a JSON line ({e.msg}); nothing is repaired here") from None
    return out


def final_rows(path: Path) -> dict[str, dict[str, Any]]:
    """``pair_id -> final row`` of a judge's ``final.jsonl`` (its ``pair|`` rows), read without repair."""
    out: dict[str, dict[str, Any]] = {}
    for r in read_rows(path):
        if not str(r.get("k", "")).startswith("pair|"):
            continue
        pid = r.get("pair_id")
        if not pid:
            raise SystemExit(f"{Path(path).name}: a pair row without pair_id")
        if pid in out and out[pid] != r:
            raise SystemExit(f"{Path(path).name}: pair {pid[:6]}… has two different final rows")
        out[pid] = r
    return out


def write_streamed(path: Path, lines: Iterable[str], what: str) -> str:
    """Write ``lines`` (each ending in a newline) through a temporary file; returns the sha256."""
    refuse_in_repo(path, what)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    h = hashlib.sha256()
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line)
            h.update(line.encode("utf-8"))
    os.replace(tmp, path)
    return h.hexdigest()


def write_json(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def dump_line(obj: Mapping[str, Any], sort_keys: bool = False) -> str:
    """One ASCII-escaped JSON line (``alias_control_chunks.jsonl_bytes``: U+2028 and friends never
    break a row for a ``splitlines`` reader)."""
    return json.dumps(obj, ensure_ascii=True, sort_keys=sort_keys) + "\n"


def experiment_provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance as ep

    return ep()


# ------------------------------------------------------------------ add-structure

def build_structure(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """The ``markdown/1`` structure of ``text`` as a record's ``structure`` value, or
    ``(None, reason)``. The value is read back through ``structure_from_record`` (the loader's
    reader) and must give the same structure."""
    from ragstack.ingestion.structure import (
        INGEST_ADAPTERS,
        StructureInvalid,
        structure_from_record,
        validate_structure,
    )
    from ragstack.models import Document

    adapter = INGEST_ADAPTERS[".md"]
    try:
        built = adapter(Document(id="rechunk-structure", content=text, source="rechunk-structure.md"))
        if built is None:
            return None, "no_structure"
        validate_structure(built, text)
    except StructureInvalid as e:
        return None, f"invalid:{e.reason}"
    dumped = built.model_dump()
    back, reason = structure_from_record(json.loads(json.dumps({"structure": dumped})), text)
    if reason is not None or back is None:
        return None, f"unreadable:{reason}"
    if back != built:
        return None, "round_trip"
    return dumped, None


def structured_records(path: Path) -> tuple[list[str], dict[str, Any]]:
    """The output lines and counts; SystemExit on any record the adapter cannot structure."""
    lines: list[str] = []
    shas: list[str] = []
    problems: Counter[str] = Counter()
    sections = 0
    for i, rec in enumerate(read_rows(path), 1):
        if not isinstance(rec, dict):
            raise SystemExit(f"{path.name}:{i}: not a JSON object")
        if "structure" in rec:
            raise SystemExit(f"{path.name}:{i}: the record already carries a structure key (never replaced)")
        text = rec.get("text")
        if not isinstance(text, str):
            raise SystemExit(f"{path.name}:{i}: the record has no text")
        dumped, reason = build_structure(text)
        if reason is not None:
            problems[reason] += 1
            continue
        assert dumped is not None
        sections += len(dumped.get("sections") or [])
        shas.append(asc.sha_text(text))
        lines.append(dump_line({**rec, "structure": dumped}))
    if problems:
        raise SystemExit(f"REFUSED: {sum(problems.values())} record(s) get no valid markdown/1 structure "
                         f"({dict(problems)}); nothing written")
    return lines, {"records": len(lines), "sections": sections, "texts_sha256": asc.sha_text("\n".join(shas))}


def verify_structured(src: Path, dst: Path) -> dict[str, Any]:
    """Every output record is its input record plus ``structure``: same keys otherwise, same path,
    same text (sha compared). SystemExit otherwise."""
    a, b = read_rows(src), read_rows(dst)
    if len(a) != len(b):
        raise SystemExit(f"REFUSED: {len(a)} input records, {len(b)} output records")
    for i, (x, y) in enumerate(zip(a, b, strict=True), 1):
        if asc.sha_text(x.get("text") or "") != asc.sha_text(y.get("text") or ""):
            raise SystemExit(f"REFUSED: record {i}: the text sha changed")
        if {k: v for k, v in y.items() if k != "structure"} != x or "structure" not in y:
            raise SystemExit(f"REFUSED: record {i}: a key other than structure differs (path and doc id never change)")
    return {"records": len(b), "texts_identical": True}


def run_add_structure(src: Path, dst: Path, summary: Path | None = None) -> dict[str, Any]:
    env = acc.require_env()
    if src.resolve() == dst.resolve():
        raise SystemExit("--out is --in: the input is never rewritten")
    lines, counts = structured_records(src)
    refuse_in_repo(dst, "--out")
    tmp = dst.with_name(dst.name + ".unverified")
    write_streamed(tmp, lines, "--out")
    try:
        check = verify_structured(src, tmp)
    except SystemExit:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dst)
    from ragstack.ingestion import markdown_structure as md

    rec = {"schema": SCHEMA["structure"], "exploratory": True, "adapter": md.ADAPTER,
           "markdown_backend": env.get("markdown_backend"), **counts, "check": check,
           "in_sha256": asc.sha256_file(src), "out_sha256": asc.sha256_file(dst), "environment": env,
           "provenance": experiment_provenance()}
    write_json(summary or dst.with_name(dst.name + ".summary.json"), rec)
    return rec


# ------------------------------------------------------------------ export

def registry_spec() -> SimpleNamespace:
    """A registry-like entry: what ``embed_shard`` reads off a resolved spec (``chunk_params``,
    ``chunk_size``, ``id``)."""
    return SimpleNamespace(id=SPEC_ID, chunk_method=METHOD, chunk_size=SIZE, chunk_overlap=OVERLAP,
                           chunk_params=dict(PARAMS), embedding_model=acc.TOKENIZER)


def embed_args(model_window: int = acc.MODEL_WINDOW, symbol_glossary: str = "off",
               structure_metadata: str = "off") -> argparse.Namespace:
    """``embed_shard``'s own argument parser, with the chunk flags the registry entry carries and
    the model window given (no endpoint is asked for it)."""
    import embed_shard

    return embed_shard.parse_args(["(records)", "--chunk-method", METHOD, "--chunk-size", str(SIZE),
                                   "--chunk-overlap", str(OVERLAP), "--chunk-max-tokens", str(model_window),
                                   "--embedding-model", acc.TOKENIZER, "--embedding-url", NO_HOST,
                                   "--symbol-glossary", symbol_glossary,
                                   "--structure-metadata", structure_metadata])


def _refuse_request(request: Any) -> Any:
    raise RuntimeError(f"rechunk export: no request is ever sent (asked for {request.url})")


def build_pipeline(args: argparse.Namespace, spec: Any) -> tuple[Any, Any]:
    """``embed_shard._build_pipeline`` on a client that refuses every request. Returns
    ``(pipeline, client)``; the chunker must be the confirmatory one (target / ceiling / min piece)."""
    import embed_shard
    import httpx

    embed_shard.require_structure_backends_or_exit(args.structure_metadata,
                                                   embed_shard._structure_markers(args, spec))
    http = httpx.AsyncClient(transport=httpx.MockTransport(_refuse_request))
    pipe = embed_shard._build_pipeline(args, http, spec=spec)
    ch = pipe.chunker
    got = (getattr(ch, "target_tokens", None), getattr(ch, "ceiling_tokens", None),
           getattr(ch, "min_piece_tokens", None))
    if got != (acc.TARGET, acc.CEILING, acc.MIN_PIECE):
        raise SystemExit(f"REFUSED: the chunker's target/ceiling/min piece are {got}, not "
                         f"{(acc.TARGET, acc.CEILING, acc.MIN_PIECE)} (a model window under {acc.CEILING} tokens "
                         "clamps the tolerance)")
    if not pipe.structure_markers.needs_structure or pipe.structure_markers.structure_cuts is None:
        raise SystemExit("REFUSED: the entry's markers resolve to no structure_cuts")
    return pipe, http


def export_row(chunk: Any) -> dict[str, Any]:
    md = dict(chunk.metadata or {})
    md["chunk_descriptor"] = DESCRIPTOR
    return {"chunk_id": chunk.id, "doc_id": chunk.doc_id, "content": chunk.content,
            "start_char": chunk.start_char, "end_char": chunk.end_char, "metadata": md}


def require_structure_keys(path: Path) -> int:
    n = 0
    for i, rec in enumerate(read_rows(path), 1):
        if not isinstance(rec.get("structure"), dict):
            raise SystemExit(f"REFUSED: {path.name}:{i}: the record has no structure key (run add-structure first; "
                             "without it the section cuts never happen)")
        n += 1
    return n


async def _prepare(pipe: Any, files: Sequence[Path], tenant: str) -> list[tuple[Path, int, Any]]:
    out = []
    for f in files:
        n = require_structure_keys(f)
        prepared = await pipe.prepare_source(str(f), tenant_id=tenant)
        out.append((f, n, prepared))
    return out


def export_chunks(files: Sequence[Path], *, model_window: int = acc.MODEL_WINDOW, symbol_glossary: str = "off",
                  structure_metadata: str = "off", tenant: str = "public"
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The export rows of ``files`` (in file order, chunk order within) and the counts."""
    import embed_shard

    args = embed_args(model_window, symbol_glossary, structure_metadata)
    spec = registry_spec()
    pipe, http = build_pipeline(args, spec)

    async def go() -> list[tuple[Path, int, Any]]:
        try:
            return await _prepare(pipe, files, tenant)
        finally:
            await http.aclose()

    done = asyncio.run(go())
    dropped = getattr(pipe.loader, "dropped", None)
    invalid = dict(getattr(dropped, "structure_invalid", {}) or {})
    if invalid:
        raise SystemExit(f"REFUSED: the loader dropped {sum(invalid.values())} structure(s) ({invalid}); those "
                         "documents would be chunked without section cuts")
    if pipe.text_render_failures():
        raise SystemExit("REFUSED: text_render failed on some documents")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_file = []
    for f, n, prepared in done:
        k = 0
        for ch in prepared.chunks:
            if ch.id in seen:
                raise SystemExit(f"REFUSED: chunk {ch.id[:8]}… is produced twice (a record in two files?)")
            seen.add(ch.id)
            rows.append(export_row(ch))
            k += 1
        per_file.append({"sha256": asc.sha256_file(f), "records": n, "documents": len(prepared.doc_ids),
                         "chunks": k, "produced": prepared.produced})
    applied = embed_shard._applied_chunking(args, pipe.chunker, None, pipe.structure_markers)
    counts = {"files": per_file, "documents": sum(x["documents"] for x in per_file), "chunks": len(rows),
              "chunker": {"method": METHOD, "size": SIZE, "overlap": OVERLAP, "params": PARAMS,
                          "chunk_descriptor": DESCRIPTOR, "applied": applied, "model_window": model_window},
              "symbol_glossary": symbol_glossary, "structure_metadata": structure_metadata,
              "path": "embed_shard._build_pipeline(spec with params) -> IngestionPipeline.prepare_source"}
    return rows, counts


def run_export(files: Sequence[Path], dst: Path, summary: Path | None = None, *, expect_chunks: int | None = None,
               model_window: int = acc.MODEL_WINDOW, symbol_glossary: str = "off",
               structure_metadata: str = "off") -> dict[str, Any]:
    env = acc.require_env()
    refuse_in_repo(dst, "--out")
    if any(f.resolve() == dst.resolve() for f in files):
        raise SystemExit("--out is one of --records")
    rows, counts = export_chunks(files, model_window=model_window, symbol_glossary=symbol_glossary,
                                 structure_metadata=structure_metadata)
    if expect_chunks is not None and counts["chunks"] != expect_chunks:
        raise SystemExit(f"REFUSED: {counts['chunks']} chunks, --expect-chunks {expect_chunks}; nothing written")
    sha = write_streamed(dst, (dump_line(r, sort_keys=True) for r in rows), "--out")
    rec = {"schema": SCHEMA["export"], "exploratory": True, **counts, "expect_chunks": expect_chunks,
           "chunks_sha256": sha, "environment": env, "provenance": experiment_provenance()}
    write_json(summary or dst.with_name(dst.name + ".summary.json"), rec)
    return rec


# ------------------------------------------------------------------ gold-map

def label_word(f: Mapping[str, Any] | None) -> str | None:
    """A final row as ``answers`` / ``not`` / ``unjudged`` (``None``: no final row)."""
    if f is None:
        return None
    if acc.is_unjudged(f):
        return "unjudged"
    return "answers" if acc.label_of(f) == 1 else "not"


def combine(per_registry: Mapping[str, str]) -> str:
    """One (record, chunk)'s label over the registries that judged it: answers when any registry's
    final label is answers, not when one judged it and none answers, else unjudged."""
    vals = set(per_registry.values())
    if "answers" in vals:
        return "answers"
    return "not" if "not" in vals else "unjudged"


def cover_class(other: Sequence[int], primary: int | None) -> str:
    """The cover-any class of a lost pair, :func:`alias_control_chunks.cover_split`'s rule."""
    o = any(x == 1 for x in other or [])
    p = primary == 1
    return "both" if o and p else "other_only" if o else "primary_only" if p else "none"


def stage_a_hits(runs: Path, want: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], list[str]], dict[str, str]]:
    """``(record, window) -> [leg id, …]``: every stage-A retrieval leg whose top 10 for the record
    holds the window (read only), and the rows-file sha of each leg's file."""
    import alias_study_run as ar

    recs = {r for r, _ in want}
    hits: dict[tuple[str, str], list[str]] = defaultdict(list)
    shas: dict[str, str] = {}
    for c, g, r, rep in asc.all_legs():
        lid = asc.leg_id(c, g, r, rep)
        if lid not in ar.RETRIEVAL_A_LEGS:
            continue
        rows_path, _ = ar.leg_files(runs, c, g, r, rep)
        if not rows_path.is_file():
            raise SystemExit(f"--stage-a-runs: no rows for the stage-A leg {lid}")
        shas[f"{c}/{rows_path.name}"] = asc.sha256_file(rows_path)
        cond = asc.RETRIEVERS[r]
        for row in read_rows(rows_path):
            if row.get("condition") != cond or row.get("record_id") not in recs or row.get("failed"):
                continue
            for cid in (row.get("ranked_chunk_ids") or [])[:asc.FOUND_K]:
                key = (row["record_id"], cid)
                if key in want and lid not in hits[key]:
                    hits[key].append(lid)
    return dict(hits), shas


def corpus_arm_of(group: str, legs: Sequence[str]) -> tuple[str | None, bool]:
    """The corpus arm a pair's question is run in, and whether a random window was retrieved in
    both draws (then the first draw in corpus-arm order)."""
    import alias_study_run as ar

    if group in ("base", "neighbour"):
        return group, False
    if group != "random":
        raise SystemExit(f"unknown arm group {group!r}")
    draws = [c for c in asc.CORPUS_ARMS if asc.arm_group(c) == "random"
             and any(lg in ar.JUDGING_A_LEGS and lg.split("|")[0] == c for lg in legs)]
    if not draws:
        return None, False
    return draws[0], len(draws) > 1


def base_only_in_larger_arm(pair: Mapping[str, Any]) -> bool:
    """A base pair (run in the base collection) whose window no base-arm stage-A leg retrieved:
    stage A found it only in a larger arm's leg (module docstring)."""
    return pair["corpus_arm"] == "base" and not any(lg.split("|")[0] == "base" for lg in pair["stage_a_legs"])


def extra_labels(finals: Sequence[Sequence[Mapping[str, Any]]]) -> tuple[dict[tuple[str, str], str], dict[str, int]]:
    """``(record, chunk) -> label word`` of the additional registries' ``final.jsonl`` rows (the
    stage-1b judge runs; :data:`EXTRA_REGISTRY`), and their counts. Only a batch row (``k``
    starting ``batch|``) is skipped (counted); every other row must carry ``record_id`` and
    ``chunk_id`` (a ``pair|`` row without them, or any other row without them, is refused), and a
    ``pair_id`` that is not ``sha256(record|chunk)[:16]`` is refused. A file that contributes no
    label row is refused (a wrong or empty file never passes silently). One (record, chunk) seen in
    several rows (or files) takes :func:`combine` over them, and a disagreement between judged
    labels is counted."""
    import alias_study_judge as aj

    per: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    counts: Counter[str] = Counter()
    for fi, rows in enumerate(finals):
        used = 0
        for j, r in enumerate(rows):
            k = str(r.get("k", ""))
            if k.startswith("batch|"):
                counts["batch_rows_skipped"] += 1
                continue
            rid, cid = r.get("record_id"), r.get("chunk_id")
            if not rid or not cid:
                kind = "a pair| row" if k.startswith("pair|") else "a row that is not a batch| row"
                raise SystemExit(f"--extra-final #{fi + 1}: {kind} without record_id/chunk_id (row {j + 1})")
            if r.get("pair_id") is not None and r["pair_id"] != aj.pair_id(rid, cid):
                raise SystemExit(f"--extra-final #{fi + 1}: a row's pair_id is not sha256(record|chunk)[:16]")
            w = label_word(r)
            assert w is not None
            per[(rid, cid)][f"{fi}:{j}"] = w
            counts["rows"] += 1
            used += 1
        if not used:
            raise SystemExit(f"--extra-final #{fi + 1}: the file contributes no label (no pair row with ids)")
    out: dict[tuple[str, str], str] = {}
    for key, ws in per.items():
        judged = {v for v in ws.values() if v != "unjudged"}
        counts["conflicts_within"] += int(len(judged) > 1)
        out[key] = combine(ws)
    counts["labels"] = len(out)
    return out, {k: counts[k] for k in ("rows", "batch_rows_skipped", "labels", "conflicts_within")}


def gold_map(sample:Sequence[Mapping[str, Any]], seeds: Sequence[Mapping[str, Any]],
             cover_seeds: Sequence[Mapping[str, Any]], cfinal: Mapping[str, Mapping[str, Any]],
             vfinal: Mapping[str, Mapping[str, Any]], hits: Mapping[tuple[str, str], Sequence[str]],
             extra: Mapping[tuple[str, str], str] | None = None, *, allow_relabel: bool = False
             ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The label-table rows and their counts (a pure function; SystemExit on inconsistent inputs).
    ``extra`` (:func:`extra_labels`) adds the :data:`EXTRA_REGISTRY` registry's labels under the
    same combine rule; ``None`` leaves rows and counts exactly as without it. An ``extra`` label
    for a (record, chunk) that already has a label row (``labels_already_judged``; stage 1b seeds
    only chunks without one) is refused unless ``allow_relabel``, whose use the counts record."""
    import alias_study_judge as aj

    by_pid: dict[str, Mapping[str, Any]] = {}
    for s in sample:
        if aj.pair_id(s["record_id"], s["chunk_id"]) != s["pair_id"]:
            raise SystemExit("sample: a pair id is not sha256(record|window)[:16]")
        if s["pair_id"] in by_pid:
            raise SystemExit("sample: a pair is drawn twice")
        by_pid[s["pair_id"]] = s
    seed_of: dict[str, Mapping[str, Any]] = {}
    for p in seeds:
        sp = by_pid.get(p["control_of"])
        if sp is None:
            raise SystemExit("pairs-seed: a row controls a pair that is not in the sample")
        if p["record_id"] != sp["record_id"] or p.get("stage_a_chunk_id", sp["chunk_id"]) != sp["chunk_id"]:
            raise SystemExit("pairs-seed: a row's record or stage-A window is not its sample pair's")
        if p["control_of"] in seed_of:
            raise SystemExit("pairs-seed: two rows control one sample pair")
        seed_of[p["control_of"]] = p
    if set(seed_of) != set(by_pid):
        raise SystemExit(f"pairs-seed maps {len(seed_of)} of the {len(by_pid)} sample pairs")
    covers: dict[str, list[str]] = defaultdict(list)
    vreg = []
    for p in cover_seeds:
        sc = by_pid.get(p["control_of"])
        if sc is None or p["record_id"] != sc["record_id"]:
            raise SystemExit("pairs-seed-cover: a row's sample pair or record does not match")
        if p["chunk_id"] in covers[p["control_of"]]:
            raise SystemExit("pairs-seed-cover: one chunk twice for one sample pair")
        covers[p["control_of"]].append(p["chunk_id"])
        vreg.append({"pair_id": aj.pair_id(p["record_id"], p["chunk_id"]), "control_of": [p["control_of"]],
                     "chunk_id": p["chunk_id"]})
    primary = {sid: p["chunk_id"] for sid, p in seed_of.items()}
    _every, cprim, cother = acc.cover_labels(vreg, vfinal, primary)

    # (record, chunk) -> registry -> label word
    labels: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    missing: Counter[str] = Counter()
    for reg, rows, fin in (("control", [{"record_id": p["record_id"], "chunk_id": p["chunk_id"]}
                                        for p in seed_of.values()], cfinal),
                           ("cover", [{"record_id": p["record_id"], "chunk_id": p["chunk_id"]}
                                      for p in cover_seeds], vfinal)):
        for r in rows:
            w = label_word(fin.get(aj.pair_id(r["record_id"], r["chunk_id"])))
            if w is None:
                missing[reg] += 1
                w = "unjudged"
            prev = labels[(r["record_id"], r["chunk_id"])].get(reg)
            if prev is not None and prev != w:
                raise SystemExit(f"the {reg} registry gives one (record, chunk) two labels")
            labels[(r["record_id"], r["chunk_id"])][reg] = w

    pairs: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    unresolved = 0
    for s in sample:
        sid = s["pair_id"]
        seed = seed_of[sid]
        cf = cfinal.get(aj.pair_id(seed["record_id"], seed["chunk_id"]))
        legs = sorted(hits.get((s["record_id"], s["chunk_id"]), []))
        group = s["arm"]
        corpus, both = corpus_arm_of(group, legs)
        if corpus is None:
            unresolved += 1
            continue
        lost = cf is not None and not acc.is_unjudged(cf) and acc.label_of(cf) == 0
        cls = None
        if lost and sid in covers:
            cls = cover_class(cother.get(sid, []), cprim.get(sid))
            covered.append({"stage_a": None, "control": cf, "cover_primary": cprim.get(sid),
                            "cover_other": cother.get(sid, [])})
        counts["random_both_draws"] += int(both)
        cov = float(seed["coverage"])
        pairs.append({"kind": "pair", "pair_id": sid, "record_id": s["record_id"], "stage_a_chunk_id": s["chunk_id"],
                      "arm": group, "corpus_arm": corpus, "random_both_draws": both, "draw_stratum": s.get("stratum"),
                      "max_overlap_chunk_id": seed["chunk_id"], "coverage": cov,
                      "coverage_band": acc.band(cov, acc.COVERAGE_BANDS),
                      "cover_any_chunk_ids": list(covers.get(sid, [])),
                      "control_label": label_word(cf) or "no_final_row", "lost": lost, "cover_class": cls,
                      "stage_a_legs": legs})
    if unresolved:
        raise SystemExit(f"REFUSED: {unresolved} random pair(s) were retrieved by no judged stage-A leg of either "
                         "random draw (is --stage-a-runs the alias main run?)")
    split = acc.cover_split(covered, lambda r: "answers_to_not")["answers_to_not"]
    mine = Counter(p["cover_class"] for p in pairs if p["cover_class"] is not None)
    if any(split[k] != mine[k] for k in acc.COVER_SPLIT) or split["n"] != sum(mine.values()):
        raise SystemExit("the cover-any classes disagree with alias_control_chunks.cover_split")

    extra_counts: Counter[str] = Counter()
    if extra is not None:
        records = {s["record_id"] for s in sample}
        for (rid, cid), w in sorted(extra.items()):
            if rid not in records:
                raise SystemExit("--extra-final: a label for a record that is not in the sample")
            prior = labels.get((rid, cid))
            extra_counts["labels_new" if not prior else "labels_already_judged"] += 1
            judged_prior = {v for v in (prior or {}).values() if v != "unjudged"}
            extra_counts["conflicts_with_confirm_registries"] += int(
                w != "unjudged" and bool(judged_prior) and judged_prior != {w})
            labels[(rid, cid)][EXTRA_REGISTRY] = w
        if extra_counts["labels_already_judged"] and not allow_relabel:
            raise SystemExit(f"REFUSED: --extra-final labels {extra_counts['labels_already_judged']} (record, chunk) "
                             "key(s) that already have a label row in the confirm registries (stage 1b judges only "
                             "unlabelled chunks); pass --allow-relabel to merge them anyway; nothing written")

    label_rows = []
    conflicts = 0
    for (rid, cid), regs in sorted(labels.items()):
        judged = {v for v in regs.values() if v != "unjudged"}
        conflicts += int(len(judged) > 1)
        label_rows.append({"kind": "label", "record_id": rid, "chunk_id": cid, "label": combine(regs),
                           "registries": dict(sorted(regs.items())), "source": "+".join(sorted(regs))})
    out_counts = {
        "pairs": len(pairs), "records": len({p["record_id"] for p in pairs}),
        "by_arm_group": dict(Counter(p["arm"] for p in pairs)),
        "by_corpus_arm": dict(Counter(p["corpus_arm"] for p in pairs)),
        "records_by_corpus_arm": {c: len({p["record_id"] for p in pairs if p["corpus_arm"] == c})
                                  for c in asc.CORPUS_ARMS},
        "random_both_draws": counts["random_both_draws"],
        "base_windows_only_in_larger_arm_legs": sum(base_only_in_larger_arm(p) for p in pairs),
        "control_label": dict(Counter(p["control_label"] for p in pairs)),
        "lost": sum(p["lost"] for p in pairs), "lost_without_cover": sum(p["lost"] and p["cover_class"] is None
                                                                         for p in pairs),
        "cover_class": {k: mine[k] for k in acc.COVER_SPLIT}, "cover_split_check": "alias_control_chunks.cover_split",
        "coverage_band": dict(Counter(p["coverage_band"] for p in pairs)),
        "labels": len(label_rows), "label": dict(Counter(r["label"] for r in label_rows)),
        "label_source": dict(Counter(r["source"] for r in label_rows)), "registry_conflicts": conflicts,
        "no_final_row": dict(missing), "cover_any_chunks": sum(len(v) for v in covers.values())}
    if extra is not None:
        out_counts[EXTRA_REGISTRY] = {**{k: extra_counts[k] for k in ("labels_new", "labels_already_judged",
                                                                      "conflicts_with_confirm_registries")},
                                      "allow_relabel": allow_relabel}
    return [*pairs, *label_rows], out_counts


@dataclass
class LabelTable:
    """The gold-map output, read back: pairs, labels per (record, chunk), and the file's sha."""
    pairs: list[dict[str, Any]]
    labels: dict[tuple[str, str], dict[str, Any]]
    sha256: str
    meta: dict[str, Any] = field(default_factory=dict)

    def records(self, corpus_arm: str | None = None) -> set[str]:
        return {p["record_id"] for p in self.pairs if corpus_arm is None or p["corpus_arm"] == corpus_arm}

    def pairs_in(self, corpus_arm: str) -> list[dict[str, Any]]:
        return [p for p in self.pairs if p["corpus_arm"] == corpus_arm]

    def label(self, record_id: str, chunk_id: str) -> str | None:
        r = self.labels.get((record_id, chunk_id))
        return None if r is None else str(r["label"])

    def forbidden(self) -> set[str]:
        out: set[str] = set()
        for p in self.pairs:
            out |= {p["pair_id"], p["record_id"], p["stage_a_chunk_id"], p["max_overlap_chunk_id"],
                    *p["cover_any_chunk_ids"]}
        for rid, cid in self.labels:
            out |= {rid, cid}
        return out


def read_label_table(path: str | Path) -> LabelTable:
    """The table written by ``gold-map``; SystemExit when it is not one."""
    path = Path(path)
    rows = read_rows(path)
    if not rows or rows[0].get("kind") != "meta" or rows[0].get("schema") != SCHEMA["table"]:
        raise SystemExit(f"{path.name}: not a rechunk label table (its first row is the {SCHEMA['table']} meta row)")
    pairs = [r for r in rows if r.get("kind") == "pair"]
    labels = {(r["record_id"], r["chunk_id"]): r for r in rows if r.get("kind") == "label"}
    for p in pairs:
        if p.get("corpus_arm") not in asc.CORPUS_ARMS or not p.get("max_overlap_chunk_id"):
            raise SystemExit(f"{path.name}: a pair row without a corpus arm or a max-overlap chunk")
    if len(pairs) != rows[0].get("pairs") or len(labels) != rows[0].get("labels"):
        raise SystemExit(f"{path.name}: the row counts are not the meta row's")
    return LabelTable(pairs, labels, asc.sha256_file(path), rows[0])


def run_gold_map(sample: Path, seeds: Path, cover_seeds: Path, cfinal: Path, vfinal: Path, runs: Path, out: Path,
                 summary: Path | None = None, extra_final: Sequence[Path] | None = None,
                 allow_relabel: bool = False) -> dict[str, Any]:
    refuse_in_repo(out, "--out")
    if allow_relabel and not extra_final:
        raise SystemExit("--allow-relabel needs --extra-final")
    samp = read_rows(sample)
    hits, leg_shas = stage_a_hits(runs, {(s["record_id"], s["chunk_id"]) for s in samp})
    extra, extra_counts = (None, None) if not extra_final else extra_labels([read_rows(f) for f in extra_final])
    rows, counts = gold_map(samp, read_rows(seeds), read_rows(cover_seeds), final_rows(cfinal), final_rows(vfinal),
                            hits, extra, allow_relabel=allow_relabel)
    if extra_counts is not None:
        counts[EXTRA_REGISTRY] = {**extra_counts, **counts[EXTRA_REGISTRY], "files": len(extra_final or [])}
    meta = {"kind": "meta", "schema": SCHEMA["table"], "pairs": counts["pairs"], "labels": counts["labels"],
            "label_rule": "answers if any registry's final label is answers; not if judged and none answers; "
                          "else unjudged",
            "corpus_arm_rule": "base/neighbour: own arm; random: the draw whose judged stage-A leg retrieved the "
                               "window (both: the first in corpus-arm order)"}
    if extra_final:
        meta["extra_registries"] = [EXTRA_REGISTRY]
    if allow_relabel:
        meta["allow_relabel"] = True
    sha = acc.write_private(out, acc.jsonl_bytes([meta, *rows]), "the rechunk label table")
    inputs = {"sample": sample, "pairs_seed": seeds, "pairs_seed_cover": cover_seeds, "control_final": cfinal,
              "cover_final": vfinal}
    rec = {"schema": SCHEMA["gold_map"], "exploratory": True, **counts, "table_sha256": sha,
           "inputs_sha256": {k: asc.sha256_file(v) for k, v in inputs.items()},
           "stage_a_rows_sha256": dict(sorted(leg_shas.items())), "label_rule": meta["label_rule"],
           "corpus_arm_rule": meta["corpus_arm_rule"],
           "provenance": acc.public_provenance(experiment_provenance())}
    if extra_final:
        rec["inputs_sha256"]["extra_final"] = [asc.sha256_file(f) for f in extra_final]
    forbidden = read_label_table(out).forbidden()
    acc.write_summary(summary or out.with_name(out.name + ".summary.json"), rec, "the gold-map summary", forbidden)
    return rec


# ------------------------------------------------------------------ the rechunk run's presence check

def export_descriptors(exports: Sequence[Path]) -> Counter[str]:
    """``chunk_descriptor -> rows`` over the export rows' ``metadata.chunk_descriptor`` (the stamp
    :func:`export_row` writes on every row; a row without one counts as ``"<none>"``)."""
    import _glossbench_r2 as g

    out: Counter[str] = Counter()
    for path in exports:
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = g.parse_chunk(json.loads(line), f"{Path(path).name}:{i}")
                out[str(row.metadata.get("chunk_descriptor") or "<none>")] += 1
    return out


def receipt_descriptor(receipt: Mapping[str, Any]) -> str | None:
    """The chunk descriptor a load receipt records, if it records one (top-level
    ``chunk_descriptor`` or ``chunking.chunk_descriptor``). ``load_embeddings.py``'s receipts on
    the v1.6.24 tools image record none; the export rows' stamp is then the descriptor check."""
    d = receipt.get("chunk_descriptor")
    if d is None and isinstance(receipt.get("chunking"), Mapping):
        d = receipt["chunking"].get("chunk_descriptor")
    return None if d is None else str(d)


def check_rechunk_presence(table: LabelTable, exports: Sequence[Path], corpus_arm: str, *,
                           expected_chunks: int, load_receipt: Path | None = None) -> dict[str, Any]:
    """The arm's new export is the confirmatory chunking's, complete, and holds every chunk the
    table maps for this corpus arm's pairs. Raises ``found10.Refused`` when:

    - the export holds another number of distinct chunks than ``expected_chunks`` (the run
      config's ``corpora.<arm>.expected_chunks``, the offline count);
    - an export row's ``metadata.chunk_descriptor`` is missing or is not :data:`DESCRIPTOR`
      (``sentence/512/0`` and the control params), or the load receipt records a descriptor
      (:func:`receipt_descriptor`) other than :data:`DESCRIPTOR`;
    - a mapped chunk id (the max-overlap chunk and every cover-any chunk) is absent."""
    import found10 as f10

    pairs = table.pairs_in(corpus_arm)
    want = set()
    for p in pairs:
        want |= {p["max_overlap_chunk_id"], *p["cover_any_chunk_ids"]}
    chunks, sources = _check_export(exports, corpus_arm, expected_chunks, load_receipt)
    absent = sorted(c for c in want if c not in chunks)
    if absent:
        raise f10.Refused(f"rechunk presence: {len(absent)} of the {len(want)} mapped chunk id(s) of the "
                          f"{corpus_arm} pairs are absent from the arm's export (is it the rechunked export?)")
    return {"pairs": len(pairs), "records": len(table.records(corpus_arm)), "mapped_chunks": len(want),
            "present": len(want), "label_table_sha256": table.sha256,
            "export_sha256": [asc.sha256_file(x) for x in exports], "chunks_in_export": len(chunks),
            "expected_chunks": expected_chunks, "chunk_descriptor": DESCRIPTOR, "descriptor_checked_in": sources}


def check_rechunk_export(exports: Sequence[Path], corpus_arm: str, *, expected_chunks: int,
                         load_receipt: Path | None = None, records: int | None = None) -> dict[str, Any]:
    """The presence check of ``alias_study_run``'s ``rechunk: {all_records: true}`` mode (the reach
    comparison's S side, no label table): the export checks of :func:`check_rechunk_presence`
    (chunk count = ``expected_chunks``; every row's descriptor, and the load receipt's when it
    records one, :data:`DESCRIPTOR`), with the same refusals. ``records``: the records the run
    asks, recorded."""
    chunks, sources = _check_export(exports, corpus_arm, expected_chunks, load_receipt)
    return {"scope": "all_records", "records": records, "export_sha256": [asc.sha256_file(x) for x in exports],
            "chunks_in_export": len(chunks), "expected_chunks": expected_chunks, "chunk_descriptor": DESCRIPTOR,
            "descriptor_checked_in": sources}


def _check_export(exports: Sequence[Path], corpus_arm: str, expected_chunks: int, load_receipt: Path | None
                  ) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The export half of the presence checks: ``(chunks, descriptor sources)``; ``found10.Refused``
    on another chunk count or descriptor."""
    import found10 as f10

    chunks = asc.read_chunk_exports(exports)
    if len(chunks) != expected_chunks:
        raise f10.Refused(f"rechunk presence: the {corpus_arm} export holds {len(chunks)} chunks, "
                          f"corpora.{corpus_arm}.expected_chunks is {expected_chunks}")
    descs = export_descriptors(exports)
    other = {d: n for d, n in descs.items() if d != DESCRIPTOR}
    if other:
        raise f10.Refused(f"rechunk presence: {sum(other.values())} row(s) of the {corpus_arm} export carry "
                          f"another chunk_descriptor than the confirmatory {DESCRIPTOR!r} ({sorted(other)})")
    sources = ["export_rows"]
    if load_receipt is not None and Path(load_receipt).is_file():
        rd = receipt_descriptor(json.loads(Path(load_receipt).read_text(encoding="utf-8")))
        if rd is not None and rd != DESCRIPTOR:
            raise f10.Refused(f"rechunk presence: the {corpus_arm} load receipt records chunk_descriptor {rd!r}, "
                              f"not the confirmatory {DESCRIPTOR!r}")
        if rd is not None:
            sources.append("load_receipt")
    return chunks, sources


# ------------------------------------------------------------------ stage1b-seed

def stage1b_candidates(table: LabelTable, rows: Mapping[str, Mapping[tuple[str, str], Mapping[str, Any]]],
                       condition: str) -> tuple[dict[tuple[str, str], list[tuple[dict[str, Any], int]]], dict[str, Any]]:
    """``(record, chunk) -> [(lost pair, rank), …]``: every chunk with **no label row** for the
    record in the top 10 of a lost pair's question under ``condition``, in the pair's corpus arm's
    rows; and the counts. A missing row is refused (as in the report); a failed call leaves its
    pair out (counted: its found@10 stays inexact)."""
    cand: dict[tuple[str, str], list[tuple[dict[str, Any], int]]] = defaultdict(list)
    c: Counter[str] = Counter()
    lists: set[tuple[str, str]] = set()
    lost = [p for p in table.pairs if p["lost"]]
    for p in lost:
        row = rows.get(p["corpus_arm"], {}).get((p["record_id"], condition))
        if row is None:
            raise SystemExit(f"REFUSED: a lost pair's record has no {condition!r} row in its corpus arm's run")
        if row.get("failed") or row.get("ranked_chunk_ids") is None:
            c["lost_pairs_failed"] += 1
            continue
        c["lost_pairs_read"] += 1
        key = (p["record_id"], p["corpus_arm"])
        first = key not in lists
        lists.add(key)
        for rank, cid in enumerate(list(row["ranked_chunk_ids"])[:asc.FOUND_K], 1):
            lab = table.label(p["record_id"], cid)
            if first:
                c["top10_slots"] += 1
                c[f"top10_slots_{lab or 'no_label_row'}"] += 1
            if lab is None:
                cand[(p["record_id"], cid)].append((p, rank))
    counts = {"lost_pairs": len(lost), "lost_pairs_read": c["lost_pairs_read"],
              "lost_pairs_failed": c["lost_pairs_failed"], "questions": len({p["record_id"] for p in lost}),
              "top10_lists": len(lists), "top10_slots": c["top10_slots"],
              "top10_slots_by_label": {k: c[f"top10_slots_{k}"] for k in (*LABELS, "no_label_row")}}
    return dict(cand), counts


def scan_exports(exports: Sequence[Path], want: set[str]) -> tuple[dict[str, str], dict[str, Any], Counter[str]]:
    """``chunk_id -> the export line`` (unchanged when pure ASCII, else re-serialized ASCII-escaped,
    ``alias_control_chunks.jsonl_bytes``' reason) and ``chunk_id -> parsed row`` for the ids in
    ``want``. A chunk seen twice must carry the same content (the first line is kept); otherwise
    refused."""
    import _glossbench_r2 as g

    lines: dict[str, str] = {}
    parsed: dict[str, Any] = {}
    c: Counter[str] = Counter()
    for path in exports:
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                row = g.parse_chunk(obj, f"{Path(path).name}:{i}")
                if row.chunk_id not in want:
                    continue
                prev = parsed.get(row.chunk_id)
                if prev is not None:
                    if prev.content != row.content:
                        raise SystemExit(f"REFUSED: {Path(path).name}:{i}: a seed chunk appears with conflicting "
                                         "content in the exports; nothing written")
                    c["duplicate_rows_same_content"] += 1
                    continue
                text = line.rstrip("\r\n")
                if not text.isascii():
                    text = json.dumps(obj, ensure_ascii=True)
                    c["rows_reescaped"] += 1
                lines[row.chunk_id] = text + "\n"
                parsed[row.chunk_id] = row
    return lines, parsed, c


def stage1b_seeds(cand: Mapping[tuple[str, str], Sequence[tuple[Mapping[str, Any], int]]],
                  paper_of: Mapping[str, str], doc_of: Mapping[str, str]
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One seed row per unlabelled (record, chunk), in the shape ``alias_study_judge.py
    build-control --seed-pairs`` reads (``control_of``, ``record_id``, ``chunk_id``; the rest is
    carried as the cover-any seed rows carry it).

    ``control_of`` / ``stage_a_chunk_id`` are those of a lost pair of the same question whose top 10
    held the chunk: the first by pair id among those whose stage-A window lies in the chunk's
    paper (the window's paper is its max-overlap chunk's, ``paper_of``), else the first by pair id.
    Without ``--any-paper``, ``build_control_pairs`` refuses a seed whose chunk's paper (path
    root) is not the stage-A pair's ``c_paper`` (the window's paper): those rows carry
    ``same_paper_as_window`` false, are counted, never dropped, and go through ``build-control
    --any-paper``. ``window_arm`` is the stage-A window's arm group, named so that it is never read
    as the chunk's arm (``build-control`` reads no arm from a seed row)."""
    import alias_study_judge as aj

    seeds = []
    for (rid, cid), hits in sorted(cand.items()):
        pairs = {p["pair_id"]: p for p, _ in hits}
        same = sorted(pid for pid, p in pairs.items() if paper_of[p["max_overlap_chunk_id"]] == paper_of[cid])
        pick = pairs[(same or sorted(pairs))[0]]
        seeds.append({"control_of": pick["pair_id"], "record_id": rid, "chunk_id": cid,
                      "pair_id": aj.pair_id(rid, cid), "doc_id": doc_of[cid], "stage_a_chunk_id": pick["stage_a_chunk_id"],
                      "window_arm": pick["arm"], "corpus_arm": pick["corpus_arm"], "stratum": pick.get("draw_stratum"),
                      "best_rank": min(r for _, r in hits), "lost_pairs_with_chunk": sorted(pairs),
                      "same_paper_as_window": bool(same), "stage": EXTRA_REGISTRY})
    counts = {"seeds": len(seeds), "seed_records": len({s["record_id"] for s in seeds}),
              "seeds_same_paper_as_window": sum(s["same_paper_as_window"] for s in seeds),
              "seeds_other_paper": sum(not s["same_paper_as_window"] for s in seeds),
              "seeds_in_several_lost_pairs": sum(len(s["lost_pairs_with_chunk"]) > 1 for s in seeds),
              "seeds_by_corpus_arm": dict(Counter(s["corpus_arm"] for s in seeds)),
              "seeds_by_best_rank": rank_counts(s["best_rank"] for s in seeds)}
    return seeds, counts


def rank_counts(ranks: Iterable[int]) -> dict[str, int]:
    rs = list(ranks)
    return {name: sum(lo <= x <= hi for x in rs) for name, lo, hi in (("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10))}


def run_stage1b_seed(table_path: Path, runs: Path, exports: Sequence[Path], out_seed: Path, out_export: Path,
                     summary: Path, retriever: str = "base") -> dict[str, Any]:
    """``stage1b-seed`` (module docstring): the seed rows and the matching export rows of every
    unlabelled top-10 chunk of the lost pairs' questions under ``retriever`` (a condition name of
    the rechunk run, ``base`` = the default retriever), and a counts-only summary."""
    import rechunk_stage1 as rs

    if retriever not in asc.RETRIEVERS.values():
        raise SystemExit(f"--retriever {retriever!r} is not a condition of the run ({sorted(asc.RETRIEVERS.values())})")
    outs = {"--out-seed": out_seed, "--out-export": out_export, "--summary": summary}
    for what, path in outs.items():
        refuse_in_repo(path, what)
    ins = {Path(x).resolve() for x in (table_path, *exports)}
    if len({p.resolve() for p in outs.values()}) != 3 or any(p.resolve() in ins for p in outs.values()):
        raise SystemExit("REFUSED: the outputs must be three distinct files, none of them an input")
    table = read_label_table(table_path)
    rows, info = rs.read_runs(runs, table)
    cand, counts = stage1b_candidates(table, rows, retriever)
    windows = {p["max_overlap_chunk_id"] for p in table.pairs if p["lost"]}
    lines, parsed, dup = scan_exports(exports, {c for _, c in cand} | windows)
    missing = sorted({c for _, c in cand} - set(parsed))
    if missing:
        raise SystemExit(f"REFUSED: {len(missing)} seed chunk(s) are in no --export file; nothing written")
    if windows - set(parsed):
        raise SystemExit(f"REFUSED: {len(windows - set(parsed))} lost pair max-overlap chunk(s) are in no --export "
                         "file (their paper places the seeds); nothing written")
    import _glossbench_r2 as g

    paper_of = {cid: g.paper_of(row, "path_root") for cid, row in parsed.items()}
    seeds, scounts = stage1b_seeds(cand, paper_of, {cid: row.doc_id for cid, row in parsed.items()})
    seed_sha = acc.write_private(out_seed, acc.jsonl_bytes(seeds), "the stage-1b seed pairs")
    export_bytes = "".join(lines[c] for c in sorted({s["chunk_id"] for s in seeds})).encode("utf-8")
    export_sha = acc.write_private(out_export, export_bytes, "the stage-1b export rows")
    rec = {"schema": SCHEMA["stage1b_seed"], "exploratory": True, "retriever_condition": retriever,
           "top_k": asc.FOUND_K, **counts, **scounts, "export_rows": len({s["chunk_id"] for s in seeds}),
           "export_duplicate_rows_same_content": dup["duplicate_rows_same_content"],
           "export_rows_reescaped": dup["rows_reescaped"],
           "rule": "unlabelled = no label row for (record, chunk) in the table; a top-10 chunk labelled unjudged "
                   "is counted (top10_slots_by_label), not seeded",
           "control_of_rule": "a lost pair of the same question whose top 10 held the chunk: first by pair id among "
                              "those whose stage-A window is in the chunk's paper, else first by pair id",
           "build_control_note": "without --any-paper, alias_study_judge.build_control_pairs refuses a seed whose "
                                 "chunk's paper is not the stage-A pair's c_paper (seeds_other_paper counts those "
                                 "rows); run them through alias_study_judge.py build-control --any-paper, which takes "
                                 "c_paper and arm from the chunk's own paper",
           "inputs_sha256": {"label_table": table.sha256, "exports": [asc.sha256_file(x) for x in exports],
                             "runs": {c: {k: v[k] for k in ("rows_sha256", "summary_sha256")}
                                      for c, v in sorted(info.items())}},
           "outputs_sha256": {"seed": seed_sha, "export": export_sha},
           "provenance": acc.public_provenance(experiment_provenance())}
    forbidden = table.forbidden() | {x for s in seeds for x in (s["chunk_id"], s["pair_id"], s["doc_id"])}
    for arm_rows in rows.values():
        for r in arm_rows.values():
            forbidden |= set(r.get("ranked_chunk_ids") or [])
    acc.write_summary(summary, rec, "the stage-1b seed summary", forbidden)
    return rec


# ------------------------------------------------------------------ CLI

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-structure", help="copy records with a markdown/1 structure key")
    a.add_argument("--in", dest="src", required=True, type=Path)
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--summary", type=Path, default=None)
    e = sub.add_parser("export", help="the new chunks of structured records (no embedding)")
    e.add_argument("--records", required=True, nargs="+", type=Path)
    e.add_argument("--out", required=True, type=Path)
    e.add_argument("--summary", type=Path, default=None)
    e.add_argument("--expect-chunks", type=int, default=None)
    e.add_argument("--model-window", type=int, default=acc.MODEL_WINDOW)
    e.add_argument("--symbol-glossary", choices=["off", "rules"], default="off")
    e.add_argument("--structure-metadata", choices=["off", "on"], default="off")
    g = sub.add_parser("gold-map", help="the private label table of the confirmatory pairs")
    g.add_argument("--sample", required=True, type=Path)
    g.add_argument("--pairs-seed", required=True, type=Path)
    g.add_argument("--pairs-seed-cover", required=True, type=Path)
    g.add_argument("--control-final", required=True, type=Path)
    g.add_argument("--cover-final", required=True, type=Path)
    g.add_argument("--stage-a-runs", required=True, type=Path)
    g.add_argument("--out", required=True, type=Path)
    g.add_argument("--summary", type=Path, default=None)
    g.add_argument("--extra-final", action="append", type=Path, default=None,
                   help="a stage-1b judge run's final.jsonl (repeatable): its labels join as registry 'stage1b'")
    g.add_argument("--allow-relabel", action="store_true",
                   help="with --extra-final: merge labels for (record, chunk) keys that already have a label row "
                        "(refused otherwise; recorded in the summary and the table's meta row)")
    b = sub.add_parser("stage1b-seed", help="seed rows of the unlabelled top-10 chunks of the lost pairs")
    b.add_argument("--label-table", required=True, type=Path)
    b.add_argument("--runs", required=True, type=Path, help="the rechunk run's out dir (read only)")
    b.add_argument("--retriever", default="base", help="the run's condition name (base = the default retriever)")
    b.add_argument("--export", required=True, action="append", type=Path,
                   help="a rechunk chunk export (repeatable)")
    b.add_argument("--out-seed", required=True, type=Path)
    b.add_argument("--out-export", required=True, type=Path)
    b.add_argument("--summary", required=True, type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "stage1b-seed":
        rec = run_stage1b_seed(args.label_table, args.runs, args.export, args.out_seed, args.out_export,
                               args.summary, args.retriever)
        print(json.dumps({k: rec[k] for k in ("lost_pairs", "lost_pairs_failed", "seeds", "seeds_other_paper",
                                              "export_rows")}))
    elif args.cmd == "add-structure":
        rec = run_add_structure(args.src, args.out, args.summary)
        print(json.dumps({k: rec[k] for k in ("records", "sections", "out_sha256")}))
    elif args.cmd == "export":
        rec = run_export(args.records, args.out, args.summary, expect_chunks=args.expect_chunks,
                         model_window=args.model_window, symbol_glossary=args.symbol_glossary,
                         structure_metadata=args.structure_metadata)
        print(json.dumps({k: rec[k] for k in ("documents", "chunks", "chunks_sha256")}))
    else:
        rec = run_gold_map(args.sample, args.pairs_seed, args.pairs_seed_cover, args.control_final,
                           args.cover_final, args.stage_a_runs, args.out, args.summary, args.extra_final,
                           allow_relabel=args.allow_relabel)
        print(json.dumps({k: rec[k] for k in ("pairs", "records", "by_corpus_arm", "lost", "cover_class",
                                              "table_sha256")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
