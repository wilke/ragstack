#!/usr/bin/env python
"""Rechunk stage 1 (exploratory): the offline preparation of the alias corpora under the chunker of
the confirmatory boundary test, and the private label table the stage-1 report reads.

    rechunk_prep.py add-structure --in RECORDS.jsonl --out RECORDS.structured.jsonl [--summary S.json]
    rechunk_prep.py export --records R1.jsonl [R2.jsonl ...] --out CHUNKS.jsonl [--summary S.json] \\
                           [--expect-chunks N]
    rechunk_prep.py gold-map --sample sample.jsonl --pairs-seed pairs-seed.jsonl \\
                             --pairs-seed-cover pairs-seed-cover.jsonl --control-final judge-control/final.jsonl \\
                             --cover-final judge-cover/final.jsonl --stage-a-runs RUNS_DIR --out TABLE.jsonl \\
                             [--summary S.json]

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

The judge files are read without repair (a torn tail is refused, never truncated). The labels
reuse ``alias_control_chunks``' helpers (``cover_labels``, ``cover_split``, ``label_of``,
``is_unjudged``) and ``alias_study_judge.pair_id``. Every path comes from the command line.
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
          "table": "rechunk-label-table/1"}
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


def gold_map(sample:Sequence[Mapping[str, Any]], seeds: Sequence[Mapping[str, Any]],
             cover_seeds: Sequence[Mapping[str, Any]], cfinal: Mapping[str, Mapping[str, Any]],
             vfinal: Mapping[str, Mapping[str, Any]], hits: Mapping[tuple[str, str], Sequence[str]]
             ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The label-table rows and their counts (a pure function; SystemExit on inconsistent inputs)."""
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
                 summary: Path | None = None) -> dict[str, Any]:
    refuse_in_repo(out, "--out")
    samp = read_rows(sample)
    hits, leg_shas = stage_a_hits(runs, {(s["record_id"], s["chunk_id"]) for s in samp})
    rows, counts = gold_map(samp, read_rows(seeds), read_rows(cover_seeds), final_rows(cfinal), final_rows(vfinal),
                            hits)
    meta = {"kind": "meta", "schema": SCHEMA["table"], "pairs": counts["pairs"], "labels": counts["labels"],
            "label_rule": "answers if any registry's final label is answers; not if judged and none answers; "
                          "else unjudged",
            "corpus_arm_rule": "base/neighbour: own arm; random: the draw whose judged stage-A leg retrieved the "
                               "window (both: the first in corpus-arm order)"}
    sha = acc.write_private(out, acc.jsonl_bytes([meta, *rows]), "the rechunk label table")
    inputs = {"sample": sample, "pairs_seed": seeds, "pairs_seed_cover": cover_seeds, "control_final": cfinal,
              "cover_final": vfinal}
    rec = {"schema": SCHEMA["gold_map"], "exploratory": True, **counts, "table_sha256": sha,
           "inputs_sha256": {k: asc.sha256_file(v) for k, v in inputs.items()},
           "stage_a_rows_sha256": dict(sorted(leg_shas.items())), "label_rule": meta["label_rule"],
           "corpus_arm_rule": meta["corpus_arm_rule"],
           "provenance": acc.public_provenance(experiment_provenance())}
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
    absent = sorted(c for c in want if c not in chunks)
    if absent:
        raise f10.Refused(f"rechunk presence: {len(absent)} of the {len(want)} mapped chunk id(s) of the "
                          f"{corpus_arm} pairs are absent from the arm's export (is it the rechunked export?)")
    return {"pairs": len(pairs), "records": len(table.records(corpus_arm)), "mapped_chunks": len(want),
            "present": len(want), "label_table_sha256": table.sha256,
            "export_sha256": [asc.sha256_file(x) for x in exports], "chunks_in_export": len(chunks),
            "expected_chunks": expected_chunks, "chunk_descriptor": DESCRIPTOR, "descriptor_checked_in": sources}


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
    args = ap.parse_args(argv)
    if args.cmd == "add-structure":
        rec = run_add_structure(args.src, args.out, args.summary)
        print(json.dumps({k: rec[k] for k in ("records", "sections", "out_sha256")}))
    elif args.cmd == "export":
        rec = run_export(args.records, args.out, args.summary, expect_chunks=args.expect_chunks,
                         model_window=args.model_window, symbol_glossary=args.symbol_glossary,
                         structure_metadata=args.structure_metadata)
        print(json.dumps({k: rec[k] for k in ("documents", "chunks", "chunks_sha256")}))
    else:
        rec = run_gold_map(args.sample, args.pairs_seed, args.pairs_seed_cover, args.control_final,
                           args.cover_final, args.stage_a_runs, args.out, args.summary)
        print(json.dumps({k: rec[k] for k in ("pairs", "records", "by_corpus_arm", "lost", "cover_class",
                                              "table_sha256")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
