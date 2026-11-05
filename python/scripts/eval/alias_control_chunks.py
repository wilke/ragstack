#!/usr/bin/env python
"""Alias study (OA-1000): the exploratory chunk-boundary control (judge-only, option (a)).

    PYTHONPATH=python alias_control_chunks.py check  --judge-config MAIN.json --texts T [--texts T…] \\
                                                     [--sample SAMPLE.jsonl] [--per-arm 1000] --out CHECK.json
    PYTHONPATH=python alias_control_chunks.py draw   --judge-config MAIN.json --spot-key KEY.json \\
                                                     --spot-sheet SHEET.json --out-dir DIR
    PYTHONPATH=python alias_control_chunks.py chunk  --judge-config MAIN.json --texts T [--texts T…] \\
                                                     --sample DIR/sample.jsonl --out-dir DIR
    PYTHONPATH=python alias_control_chunks.py map    --judge-config MAIN.json --sample DIR/sample.jsonl \\
                                                     --control-dir DIR [--cover-any --pairs FLIPPED]
    PYTHONPATH=python alias_control_chunks.py report --judge-config MAIN.json --control-config CONTROL.json \\
                                                     --control-dir DIR [--cover-config COVER.json] \\
                                                     [--retest-config RETEST.json --retest-dir RDIR] --out REPORT.json
    PYTHONPATH=python alias_control_chunks.py retest-seed --judge-config MAIN.json --control-dir DIR --out-dir RDIR

**Record texts for the main run** (read-only; every source is needed, or documents are excluded
as ``no_text``): the base arm's extractor output (the round-1 ``oa.jsonl`` record file), and for the
added arms all four per-paper text directories of the study corpus, ``text``, ``text-v2`` (it holds
the random arms), ``text-v3`` and ``text-v4``: pass each with its own ``--texts``.
``check`` and ``chunk`` print the resolution counts; a non-zero ``no_text`` means a directory is
missing from the list.

**The question.** Do the judges' labels change when the same question is judged against a
boundary-respecting chunk covering the same text, instead of the ``tok512o0`` window
(``fixed_token/512/0``) it was judged against in stage A? Exploratory, post-freeze: nothing here
changes a confirmatory number, cell, record, judge, seed or gate of the frozen prereg.

**The control chunking is product code, not a harness copy.** Every chunk comes from
``ragstack.ingestion`` (#828 PR 1/2/4/7, #831): :func:`~ragstack.ingestion.structure.attach_structure`
with the Markdown adapter ``markdown/1`` in raw mode (each ``Document`` gets
``source="<doc_id>.md"`` so the adapter is selected; ``content`` is the record text, unchanged,
uncleaned, LaTeX included), then the chunker :func:`~ragstack.ingestion.chunker_config.build_chunker`
builds from the collection markers :data:`CONTROL_PARAMS` as THE readers resolve them
(``structure_from_params``, ``size_unit_from_params``, ``semantic_cap_from_params``):
``sentence/512/0`` + ``structure_cuts=sections/1`` + ``size_tolerance=0.25`` + ``min_piece=128``,
tokens counted with the study embedder's tokenizer (``Salesforce/SFR-Embedding-Mistral``,
``HFTokenCounter``), the model window 4096 (so the #831 ceiling is min(floor(512 x 1.25), 4080) =
640; a window below 640 would clamp the tolerance away and is refused), then
:func:`~ragstack.ingestion.structure.stamp_documents` with ``structure_cuts=True``. No segmenter,
regex or chunk rule of this file's own: the checks below only *measure* the chunks against the
library's own segments (``doc_cut_segments``) and sentence spans (``sentence_spans``).

**The environment is refused, never degraded.** Every subcommand first checks
(:func:`require_env`) that ``ragstack`` is imported from the checkout this script lives in (the
env's installed ragstack is a stale editable tree: run with ``PYTHONPATH=<checkout>/python``),
that the Punkt segmenter runs (``segmenter_version() == "punkt-abbrev-v1"``), and that the ``.md``
adapter is ``markdown/1`` with its ``[markdown]`` parser installed
(``require_structure_backends`` raises ``StructureBackendUnavailable`` otherwise).

**Record text.** A stage-A document is the record text its chunks were cut from. ``--texts``
names record JSONL files or directories of them (``{text, path, metadata}`` rows, the extractor's
output); a document's candidates are the records whose ``path`` root (before ``#``) is the
chunk export's ``source_path`` root, and the document resolves to the one candidate text that
reproduces **every** stage-A chunk of it (``text[start_char:end_char] == content``). A document
with no candidate, no reproducing candidate or two different reproducing texts is excluded and
counted (``no_text`` / ``no_match`` / ``ambiguous``); doc ids are never re-derived from paths.

**check (P2).** Offline, counts only, on every document of the sampled pairs (``--sample``) and a
seeded draw of ``--per-arm`` documents per corpus arm of the judge config's ``chunk_exports``
(the first in the order ``sha256(261101|alias|control|check|<arm>|<doc id>)``): (i) no chunk
holds a fragment of two cut segments (a union of whole segments, ``section_crossing``, is
counted); (ii) every chunk boundary is a sentence-span boundary (spans split at the segment
edges) or a segment boundary, except boundaries inside a single sentence over the 640 ceiling
(counted with the sentence count); (iii) every chunk at or under 640 SFR tokens, the share above
512 reported; (iv) the chunks tile each document; (v) every stage-A chunk of a resolved document
reproduces its text (by construction; unresolved documents counted); (vi) the environment. The
output is public (:func:`write_public`).

**draw (§4).** The spot check's rule (``alias_study_spot.draw_pairs_from``, reused, never copied)
with its nominal sizes tripled: *answers* / *not* / *tiebroken* = 150 / 99 / 51 = 300, arms equal
within a stratum (remainder to base, then neighbour), the §13.5 shortfall chain, cells filled in
the order ``sha256(261101|alias|spot|judge|<stratum>|<arm>|<pair id>)``, over stage A's
**committed** final labels (``alias_study_spot.committed_stage_a``). Because the order is the spot
check's, the committed 100-pair spot sheet is the head of every cell: the draw re-derives the
spot sheet from its sources (``alias_study_spot.judge_sheet``), requires its sha256 to be the spot
key's and the sheet file's, requires the key's pairs to be the x1 draw's cell by cell, and requires
each x1 cell to be a prefix of the x3 cell. Writes ``sample.jsonl`` (PRIVATE: pair id, record id,
stage-A chunk id, stratum, arm, whether on the spot sheet) and ``draw.json`` (counts and sha256s).

**chunk.** The control chunks of every document of the sampled pairs: ``chunks-control.jsonl``
(PRIVATE, the chunk-export row shape the judge reads, with the stamp, the SFR token count and
whether the chunk is a piece of one over-ceiling sentence in ``metadata``), ``docs.jsonl``
(PRIVATE, per document: arm, resolution, LaTeX preamble, sections) and ``chunk.json`` (counts,
the chunker as applied, P2 counts on these documents, provenance with span fingerprints).

**map (§3).** Each sampled stage-A window ``W = [start_char, end_char)`` against the control
chunks of the same document, whose text is the control chunks' concatenation (they tile it):
first ``text[start:end] == content`` for the window (a failure is excluded and counted), then
*primary* ``max-overlap``, the control chunk with the most characters of ``W``, ties to the
earlier chunk, recording coverage ``|W ∩ K| / |W|`` and purity ``|W ∩ K| / |K|``. Writes
``pairs-seed.jsonl`` (PRIVATE: ``control_of`` = the stage-A pair id, the control chunk id and the
overlap statistics; the input of ``alias_study_judge.py build-control``) and ``map.json``.
``--cover-any --pairs FILE`` (the flipped stage-A pair ids, one per line or ``{pair_id}`` JSONL):
every control chunk overlapping the window, the :data:`COVER_ANY_CAP` largest overlaps when there
are more (ties to the earlier; capped windows counted), one seed row each, in
``pairs-seed-cover.jsonl`` / ``map-cover.json``.

**report (§5, exploratory).** Flip rate (control final label != stage-A final label, unjudged =
*not*, as in the primary analysis) overall, by direction, stratum, arm, stratum x direction, LaTeX
preamble stratum, window-coverage band and control-chunk token band, with Wilson 95 % intervals;
the 2 x 2 table with kappa and Gwet's AC1 (``alias_study_stats.agreement_metrics``); C alone, E
alone and the escalation share under each chunking; ``cover-any`` attribution of the flips; the
boundary diagnostics of the windows and the control chunks. With ``--cover-config``,
``cover_any.split`` divides the flipped pairs of each direction four ways by whether a NON-primary
overlapping chunk answers and whether the re-judged primary chunk answers (``other_only`` /
``both`` / ``primary_only`` / ``none``; the primary is the ``pairs-seed.jsonl`` chunk of that
``control_of``), with ``recovered_by_other_chunk`` (answers -> not only) and
``primary_rejudge_agreement_selected``: the cover run re-judged the primary chunk itself, the same
bytes, so its label against the control run's label is a judge-repeatability count, on selected
(flipped, hence borderline) pairs only. Owner agreement (§5.4) is not
computed (owner, 2026-11-05: dropped). The output is public: counts, rates, versions and shas
only, checked by ``alias_study_common.public_problems`` (no id, no text, no host) before it is
written; the provenance's ``host`` and ``warnings`` are dropped from every public file.

**retest (the test-retest null, exploratory).** How often does the frozen pipeline change a label
when the identical stage-A window is judged again? ``retest-seed`` reads the control's
``pairs-seed.jsonl``, requires each row's ``stage_a_chunk_id`` and ``record_id`` to be its stage-A
pair's (the main registry, read-only), and writes into ``RDIR``: ``chunks-retest.jsonl`` (PRIVATE:
the stage-A export rows of those windows, one per distinct window, each original line kept byte for
byte when pure ASCII, else re-serialized ASCII-escaped), ``pairs-seed-retest.jsonl`` (PRIVATE:
``control_of``, ``record_id``, ``chunk_id`` = the stage-A window, ``stratum``, ``arm``) and
``retest-seed.json`` (counts and sha256s). A missing window is refused. A control config whose
``control`` export is ``chunks-retest.jsonl`` then runs ``alias_study_judge.py build-control`` /
``preflight`` / ``label`` / ``finalize`` unchanged in its own out dir (duplicate chunk ids with
identical content are accepted, and the retest pair ids are the stage-A pair ids).
(``metrics.by_route`` gives the control flip by the same stage-A route, to be read beside the
retest's; ``metrics.cover_any.split.by_route`` repeats the four-way split and
``recovered_by_other_chunk`` per stage-A route and direction; for ``not_to_answers`` that count is
a non-primary chunk that also answers, never a recovery.) ``report --retest-config RETEST.json --retest-dir RDIR`` adds ``metrics.retest`` (flip of the
retest final label against the committed stage-A label, unjudged = *not*, by direction, stratum,
arm, stage-A route ``agree`` / ``tiebreak_or_other`` and the retest's own route, and the 2 x 2
agreement table; ``escalation_overlap``: over the pairs with both final rows, how many left the
``agree`` route in stage A, in the retest, in both, in one only, and the Jaccard of the two
escalated sets; ``tiebreak_replicate``: over the retest registry's pairs with an Argon label row in
both the main judge dir and the retest judge dir, how many share the prompt and the system sha256,
and the Wilson agreement of the two Argon verdicts where both are non-null, with the null-verdict
pairs counted apart) and ``metrics.flip_minus_retest`` (the control flip rate minus the retest flip
rate, for display; no test is made of it).

Every artifact embeds ``experiment_provenance()`` with span fingerprints: sentences over the
documents' texts, and the adapter's sections and the control chunks where the documents are known.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402

REPO = _HERE.parents[2]

SCHEMA = {"check": "alias-control-check/1", "draw": "alias-control-draw/1", "chunk": "alias-control-chunk/1",
          "map": "alias-control-map/1", "report": "alias-control-report/1",
          "retest": "alias-control-retest-seed/1"}
#: §2: the control chunking as collection markers (``chunk_params``), read by THE readers
CONTROL_METHOD = "sentence"
CONTROL_SIZE = 512
CONTROL_OVERLAP = 0
CONTROL_PARAMS: dict[str, Any] = {"size_unit": "tokens", "pack": "318", "structure_cuts": "sections/1",
                                  "size_tolerance": 0.25, "min_piece": 128}
#: §2: the identity string the markers give (``ragstack.provenance.chunk_descriptor``)
CONTROL_DESCRIPTOR = ('sentence/512/0/{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens",'
                      '"structure_cuts":"sections/1"}')
STAGE_A_DESCRIPTOR = "fixed_token/512/0"
TOKENIZER = "Salesforce/SFR-Embedding-Mistral"
#: the study collections' model window (their index names carry ``_4096_``); minus the specials
#: reserve it is the chunker's window budget, which must stay >= the 640 ceiling
MODEL_WINDOW = 4096
TARGET = 512
CEILING = 640
MIN_PIECE = 128
REQUIRED_SEGMENTER = "punkt-abbrev-v1"
REQUIRED_ADAPTER = "markdown/1"
#: §4: the spot check's nominal sizes x 3 (150 / 99 / 51)
NOMINAL_FACTOR = 3
#: §3: ``cover-any`` judges at most this many control chunks per window
COVER_ANY_CAP = 6
CHECK_SEED = f"{asc.SEED_BASE}|control|check"
CHECK_PER_ARM = 1000
COVERAGE_BANDS: tuple[tuple[float, float, str], ...] = ((0.0, 0.5, "<0.5"), (0.5, 0.8, "0.5-0.8"),
                                                        (0.8, math.inf, ">=0.8"))
TOKEN_BANDS: tuple[tuple[float, float, str], ...] = ((0, MIN_PIECE, f"<{MIN_PIECE}"),
                                                     (MIN_PIECE, TARGET, f"{MIN_PIECE}-{TARGET - 1}"),
                                                     (TARGET, CEILING + 1, f"{TARGET}-{CEILING}"),
                                                     (CEILING + 1, math.inf, f">{CEILING}"))
#: #940: a document holding a full LaTeX preamble is its own report stratum (the text is NOT cleaned)
LATEX_MARKERS = ("\\documentclass", "\\begin{document}")


# ------------------------------------------------------------------ environment

def require_env() -> dict[str, Any]:
    """The three refusals of the module docstring. Raises SystemExit; returns what was checked."""
    import ragstack

    where = Path(ragstack.__file__).resolve()
    checkout = REPO.resolve()
    if not where.is_relative_to(checkout):
        raise SystemExit(f"REFUSED: ragstack is imported from {where.parent}, outside this script's checkout "
                         f"{checkout}; run with PYTHONPATH={checkout / 'python'} so the chunker is this commit's")
    from ragstack.ingestion import chunkers
    from ragstack.ingestion import structure as st

    sv = chunkers.segmenter_version()
    if sv != REQUIRED_SEGMENTER:
        raise SystemExit(f"REFUSED: segmenter_version() is {sv!r}, not {REQUIRED_SEGMENTER!r}: install the [chunking] "
                         "extra (nltk with its Punkt data); the regex fallback is a different segmentation (#815)")
    adapter = st.INGEST_ADAPTERS.get(".md")
    if getattr(adapter, "name", None) != REQUIRED_ADAPTER:
        raise SystemExit(f"REFUSED: the .md ingest adapter is {getattr(adapter, 'name', None)!r}, not "
                         f"{REQUIRED_ADAPTER!r}")
    try:
        st.require_structure_backends(markers=control_markers())
    except st.StructureBackendUnavailable as e:
        raise SystemExit(f"REFUSED: {e.client_detail}") from None
    from ragstack.ingestion import markdown_structure as md

    return {"ragstack_in_checkout": True, "segmenter_version": sv, "adapter": REQUIRED_ADAPTER,
            "markdown_backend": md.backend()}


# ------------------------------------------------------------------ the control chunker (ragstack only)

def control_markers() -> Any:
    from ragstack.ingestion.chunker_config import structure_from_params

    return structure_from_params(CONTROL_METHOD, CONTROL_PARAMS)


def control_descriptor() -> str:
    from ragstack.provenance import chunk_descriptor

    return chunk_descriptor(CONTROL_METHOD, CONTROL_SIZE, CONTROL_OVERLAP, CONTROL_PARAMS)


@dataclass
class ControlChunker:
    chunker: Any
    counter: Any
    budget: int

    @property
    def ceiling(self) -> int:
        return int(self.chunker.ceiling_tokens)

    def applied(self) -> dict[str, Any]:
        from ragstack.ingestion.chunkers import sentence_tolerance_provenance

        return {"chunk_descriptor": control_descriptor(), "chunk_params": dict(CONTROL_PARAMS),
                "markers": control_markers().applied(), "tokenizer": TOKENIZER, "model_window": MODEL_WINDOW,
                "window_budget": self.budget, "budget_mode": self.chunker.budget_mode,
                "tolerance": sentence_tolerance_provenance(self.chunker), "stage_a_descriptor": STAGE_A_DESCRIPTOR}


def control_chunker(model_window: int = MODEL_WINDOW) -> ControlChunker:
    """The chunker the markers resolve to (module docstring), through ``build_chunker``."""
    from ragstack.ingestion.chunker_config import (
        build_chunker,
        semantic_cap_from_params,
        size_unit_from_params,
    )

    markers = control_markers()
    if markers.structure_cuts is None or not markers.needs_structure:
        raise SystemExit("the control markers resolve to no structure_cuts")
    tol = semantic_cap_from_params(CONTROL_METHOD, CONTROL_PARAMS, recorded_size=CONTROL_SIZE)
    chunker: Any
    chunker, counter, budget = build_chunker(
        CONTROL_METHOD, chunk_size=CONTROL_SIZE, chunk_overlap=CONTROL_OVERLAP, model=TOKENIZER,
        token_backend="hf", max_tokens=model_window, size_unit=size_unit_from_params(CONTROL_METHOD, CONTROL_PARAMS),
        structure_cuts=markers.structure_cuts, **tol)
    if getattr(chunker, "ceiling_tokens", None) != CEILING or getattr(chunker, "target_tokens", None) != TARGET:
        raise SystemExit(f"REFUSED: the chunker's target/ceiling are {getattr(chunker, 'target_tokens', None)}/"
                         f"{getattr(chunker, 'ceiling_tokens', None)}, not {TARGET}/{CEILING}: a window budget of "
                         f"{budget} tokens clamps the #831 tolerance (it must be >= {CEILING})")
    if chunker.min_piece_tokens != MIN_PIECE or chunker.budget_mode != "joined":
        raise SystemExit("REFUSED: the chunker is not the §2 one (min_piece / budget_mode)")
    return ControlChunker(chunker, counter, budget)


@dataclass
class ChunkedDoc:
    doc: Any
    chunks: list[Any]
    counts: Counter[str] = field(default_factory=Counter)


def chunk_document(doc_id: str, text: str, cc: ControlChunker) -> ChunkedDoc:
    """attach_structure -> chunk -> stamp_documents, as the ingest loop runs them (§2)."""
    from ragstack.ingestion.chunkers import take_cut_stats
    from ragstack.ingestion.structure import attach_structure, stamp_documents
    from ragstack.models import Document

    doc = Document(id=doc_id, content=text, source=f"{doc_id}.md")
    counts: Counter[str] = Counter()
    attach_structure([doc], None, counts)
    chunks = cc.chunker.chunk(doc)
    counts.update(take_cut_stats(cc.chunker, doc.id))
    if chunks:
        stamp_documents([doc], chunks, counts, structure_cuts=True)
    return ChunkedDoc(doc, chunks, counts)


def doc_segments(doc: Any) -> list[Any]:
    """The cut segments the chunker used (``doc_cut_segments``); one whole-text segment when the
    document has none (the tolerant chunker's own rule for an unstructured document)."""
    from ragstack.ingestion.chunkers import doc_cut_segments
    from ragstack.ingestion.structure import Segment

    return doc_cut_segments(doc) or [Segment(0, len(doc.content), -1, -1)]


def has_latex_preamble(text: str) -> bool:
    return all(m in text for m in LATEX_MARKERS)


def section_info(doc: Any) -> dict[str, Any]:
    secs = doc.structure.sections if doc.structure is not None else []
    return {"structure": doc.structure.adapter if doc.structure is not None else None, "n_sections": len(secs),
            "untitled_only": all(s.title is None for s in secs)}


# ------------------------------------------------------------------ P2 measurements

def measure_document(cd: ChunkedDoc, cc: ControlChunker) -> tuple[Counter[str], list[int], list[bool]]:
    """P2 (i)-(iv) on one chunked document: counts, each chunk's SFR tokens, and whether each chunk
    is a piece of one sentence over the ceiling. Measures against the library's segments and
    sentence spans; changes nothing."""
    from ragstack.ingestion import chunkers

    doc, text = cd.doc, cd.doc.content
    c: Counter[str] = Counter(docs=1)
    spans = sorted((ch.start_char, ch.end_char) for ch in cd.chunks)
    if not spans or spans[0][0] != 0 or spans[-1][1] != len(text) or any(
            a[1] != b[0] for a, b in zip(spans, spans[1:], strict=False)):
        c["tiling_violation_docs"] += 1
    c["content_mismatch"] += sum(text[ch.start_char:ch.end_char] != ch.content for ch in cd.chunks)
    segs = doc_segments(doc)
    starts = [g.start for g in segs]
    seg_bounds = {g.start for g in segs} | {g.end for g in segs}
    for s, e in spans:
        lo, hi = bisect.bisect_right(starts, s) - 1, bisect.bisect_right(starts, e - 1) - 1
        if lo != hi:
            whole = s == segs[lo].start and e == segs[hi].end
            c["whole_segment_unions" if whole else "fragment_violations"] += 1
    units = [u for part in chunkers._units_by_segment(chunkers.sentence_spans(text), segs) for u in part]
    bounds = seg_bounds | {u[0] for u in units} | {u[1] for u in units}
    ustarts = [u[0] for u in units]
    split_units: set[int] = set()
    over: dict[int, bool] = {}
    for b in sorted({s for s, _ in spans} | {e for _, e in spans}):
        if b in bounds:
            continue
        k = bisect.bisect_right(ustarts, b) - 1
        if k not in over:
            u = units[k]
            over[k] = u[0] < b < u[1] and cc.counter.count(text[u[0]:u[1]]) > cc.ceiling
        if over[k]:
            c["split_sentence_boundaries"] += 1
            split_units.add(k)
        else:
            c["boundary_violations"] += 1
    c["split_sentences"] += len(split_units)
    toks = [int(cc.counter.count(ch.content)) for ch in cd.chunks]
    piece = []
    for ch in cd.chunks:
        k = bisect.bisect_right(ustarts, ch.start_char) - 1
        piece.append(k in split_units and units[k][0] <= ch.start_char and ch.end_char <= units[k][1])
    c["chunks"] += len(cd.chunks)
    c["over_ceiling"] += sum(t > cc.ceiling for t in toks)
    c["over_target"] += sum(t > TARGET for t in toks)
    c["under_min_piece"] += sum(t < MIN_PIECE for t in toks)
    c["split_sentence_pieces"] += sum(piece)
    c["section_crossing"] += sum(bool(ch.metadata.get("section_crossing")) for ch in cd.chunks)
    c["stamped"] += sum("structure_version" in ch.metadata for ch in cd.chunks)
    info = section_info(doc)
    c["structured_docs"] += int(info["structure"] is not None)
    one = info["n_sections"] <= 1 and info["untitled_only"]
    c["one_untitled_section_docs"] += int(one)
    c["one_untitled_section_chunks"] += len(cd.chunks) if one else 0
    c["latex_preamble_docs"] += int(has_latex_preamble(text))
    for name, v in cd.counts.items():
        c[f"ingest:{name}"] += v
    return c, toks, piece


def p2_gate(c: Mapping[str, int], env_ok: bool) -> dict[str, bool]:
    """(i)-(vi) with the exceptions counted beside them (module docstring)."""
    return {"i_no_fragment_of_two_segments": c.get("fragment_violations", 0) == 0,
            "ii_boundaries_are_sentence_or_segment": c.get("boundary_violations", 0) == 0,
            "iii_at_or_under_ceiling": c.get("over_ceiling", 0) == 0,
            "iv_chunks_tile_documents": c.get("tiling_violation_docs", 0) == 0 and c.get("content_mismatch", 0) == 0,
            "v_stage_a_offsets_reproduce": True,
            "vi_environment": env_ok}


# ------------------------------------------------------------------ inputs

def judge_cfg(path: Path) -> dict[str, Any]:
    import alias_study_judge as aj

    return aj.load_config(path)


@dataclass
class Exports:
    by_id: dict[str, Any]
    by_doc: dict[str, list[Any]]
    arm_of_doc: dict[str, str]


def read_exports(chunk_exports: Mapping[str, str]) -> Exports:
    """The stage-A chunk exports (``_glossbench_r2.parse_chunk`` rows, offsets kept), by chunk and
    by document; a document's arm is the first export (corpus-arm order) that holds it."""
    import _glossbench_r2 as g

    by_id: dict[str, Any] = {}
    arm_of: dict[str, str] = {}
    for arm in asc.CORPUS_ARMS:
        path = chunk_exports.get(arm)
        if not path:
            continue
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = g.parse_chunk(json.loads(line), f"{Path(path).name}:{i}")
                prev = by_id.get(row.chunk_id)
                if prev is not None:
                    if prev.content != row.content or (prev.start_char, prev.end_char) != (row.start_char, row.end_char):
                        raise SystemExit(f"{Path(path).name}:{i}: chunk {row.chunk_id[:8]}… seen with other content")
                    continue
                by_id[row.chunk_id] = row
                arm_of.setdefault(row.doc_id, arm)
    by_doc: dict[str, list[Any]] = defaultdict(list)
    for row in by_id.values():
        by_doc[row.doc_id].append(row)
    for rows in by_doc.values():
        rows.sort(key=lambda r: (r.start_char, r.end_char))
    return Exports(by_id, dict(by_doc), arm_of)


def path_root(p: str) -> str:
    return str(p).split("#", 1)[0]


def _text_files(paths: Iterable[Path], roots: set[str]) -> list[Path]:
    """A file is read whole; a directory is the per-paper layout (``<path root>.jsonl``), of which
    only the files of the wanted roots are read."""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        out += sorted(f for f in p.glob("*.jsonl") if f.stem in roots) if p.is_dir() else [p]
    return out


def load_texts(paths: Iterable[Path], roots: set[str]) -> dict[str, list[str]]:
    """root -> the distinct record texts under it, from the record JSONL files (module docstring)."""
    out: dict[str, list[str]] = defaultdict(list)
    for f in _text_files(paths, roots):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                root = path_root(r.get("path") or "")
                if root in roots:
                    t = r.get("text") or ""
                    if t not in out[root]:
                        out[root].append(t)
    return dict(out)


def resolve_doc(chunks: Sequence[Any], candidates: Sequence[str]) -> tuple[str, str | None]:
    """``(status, text)``: the one candidate reproducing every stage-A chunk (module docstring)."""
    if not candidates:
        return "no_text", None
    ok = {t for t in candidates if all(t[c.start_char:c.end_char] == c.content for c in chunks)}
    if len(ok) == 1:
        return "ok", next(iter(ok))
    return ("no_match", None) if not ok else ("ambiguous", None)


def resolve_docs(doc_ids: Iterable[str], ex: Exports, texts: Sequence[Path]) -> tuple[dict[str, str], Counter[str]]:
    doc_ids = sorted(set(doc_ids))
    roots = {path_root(ex.by_doc[d][0].source_path) for d in doc_ids if d in ex.by_doc}
    cand = load_texts(texts, roots)
    got: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for d in doc_ids:
        rows = ex.by_doc.get(d)
        if not rows:
            counts["not_in_exports"] += 1
            continue
        status, text = resolve_doc(rows, cand.get(path_root(rows[0].source_path), []))
        counts[status] += 1
        if text is not None:
            got[d] = text
    return got, counts


# ------------------------------------------------------------------ provenance and outputs

def provenance(texts: Sequence[str] | None = None, docs: Sequence[Any] | None = None,
               chunk_spans: Sequence[Sequence[tuple[int, int]]] | None = None) -> dict[str, Any]:
    """``experiment_provenance()`` with the span fingerprints (module docstring)."""
    from ragstack.provenance import experiment_provenance, span_fingerprint

    segs: list[dict[str, Any]] = []
    if docs is not None and texts is not None:
        segs.append(span_fingerprint(
            [[(s.start, s.end) for s in d.structure.sections] if d.structure is not None else [] for d in docs],
            kind="sections", texts=texts,
            producer=f"ragstack.ingestion.structure.attach_structure ({REQUIRED_ADAPTER}, raw mode)"))
    if chunk_spans is not None and texts is not None:
        segs.append(span_fingerprint(chunk_spans, kind="chunks", texts=texts, producer=control_descriptor()))
    return experiment_provenance(texts=list(texts) if texts is not None else None, segmentations=segs)


def public_provenance(p: Mapping[str, Any]) -> dict[str, Any]:
    """A provenance block fit for a public file: no ``host``, no ``warnings`` (their count kept)."""
    out = {k: v for k, v in p.items() if k not in ("host", "warnings")}
    out["warnings_n"] = len(p.get("warnings") or [])
    return out


def write_public(path: Path, obj: Mapping[str, Any], forbidden: Iterable[str] = (),
                 texts: Iterable[str] = ()) -> dict[str, Any]:
    """Counts only: ``alias_study_common.public_problems`` must find no id, no private text and no
    host field, or nothing is written."""
    bad = asc.public_problems(obj, forbidden, texts)
    if bad:
        raise SystemExit(f"REFUSED: the public file would carry {len(bad)} private string(s) ({sorted(set(bad))}); "
                         "nothing written")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return dict(obj)


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """One JSON object per line, **ASCII-escaped**: the readers (``alias_study_common.read_jsonl`` and
    the judge's) split on ``str.splitlines``, which also breaks at U+2028/U+2029, U+0085 and
    U+001C-U+001E. Record text carries such characters (two U+2029 in the main run's control
    chunks), so an unescaped row would be read as two broken lines."""
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in rows).encode("utf-8")


def write_private(path: Path, data: bytes, what: str) -> str:
    import alias_study_spot as asp

    asp.write_private(path, data, what)
    return asc.sha256_file(path)


def write_summary(path: Path, obj: Mapping[str, Any], what: str, forbidden: Iterable[str] = (),
                  texts: Iterable[str] = ()) -> None:
    """A run's counts beside its private files: public-clean, kept when only the provenance differs."""
    import alias_study_spot as asp

    bad = asc.public_problems(obj, forbidden, texts)
    if bad:
        raise SystemExit(f"REFUSED: {what} would carry {len(bad)} private string(s); nothing written")
    asp.write_key(path, obj, what)


# ------------------------------------------------------------------ check (P2)

def check_docs(ex: Exports, sample_docs: Iterable[str], per_arm: int) -> dict[str, list[str]]:
    """group -> documents: ``sampled`` and, per corpus arm, the first ``per_arm`` documents in the
    order ``sha256(<CHECK_SEED>|<arm>|<doc id>)``."""
    out: dict[str, list[str]] = {"sampled": sorted(set(sample_docs))}
    by_arm: dict[str, list[str]] = defaultdict(list)
    for d, a in ex.arm_of_doc.items():
        by_arm[a].append(d)
    for a in asc.CORPUS_ARMS:
        if a in by_arm:
            out[a] = sorted(by_arm[a], key=lambda d: (asc.seeded_key(CHECK_SEED, a, d), d))[:per_arm]
    return out


def sample_doc_ids(sample: Sequence[Mapping[str, Any]], ex: Exports) -> list[str]:
    miss = [r["chunk_id"] for r in sample if r["chunk_id"] not in ex.by_id]
    if miss:
        raise SystemExit(f"{len(miss)} sampled stage-A chunk(s) are in no chunk export of the judge config")
    return sorted({ex.by_id[r["chunk_id"]].doc_id for r in sample})


def run_check(chunk_exports: Mapping[str, str], texts: Sequence[Path], out: Path, sample: Path | None = None,
              per_arm: int = CHECK_PER_ARM) -> dict[str, Any]:
    env = require_env()
    ex = read_exports(chunk_exports)
    samp = asc.read_jsonl(sample) if sample else []
    groups = check_docs(ex, sample_doc_ids(samp, ex), per_arm)
    every = sorted({d for v in groups.values() for d in v})
    got, res = resolve_docs(every, ex, texts)
    cc = control_chunker()
    per_doc: dict[str, Counter[str]] = {}
    toks_of: dict[str, list[int]] = {}
    docs, spans = [], []
    for d in every:
        if d not in got:
            continue
        cd = chunk_document(d, got[d], cc)
        per_doc[d], toks_of[d], _ = measure_document(cd, cc)
        docs.append(cd.doc)
        spans.append([(c.start_char, c.end_char) for c in cd.chunks])
    groups_out: dict[str, Any] = {}
    total: Counter[str] = Counter()
    for gname, ds in groups.items():
        c: Counter[str] = Counter()
        toks: list[int] = []
        for d in ds:
            if d in per_doc:
                c.update(per_doc[d])
                toks += toks_of[d]
        groups_out[gname] = {"documents": len(ds), "resolved": sum(d in got for d in ds), "counts": dict(c),
                             "tokens": token_summary(toks)}
    for d in every:
        if d in per_doc:
            total.update(per_doc[d])
    all_toks = [t for d in every for t in toks_of.get(d, [])]
    gate = p2_gate(total, True)
    rec = {"schema": SCHEMA["check"], "exploratory": True, "chunker": cc.applied(), "environment": env,
           "seed": CHECK_SEED, "per_arm": per_arm, "resolution": {"documents": len(every), **dict(res)},
           "counts": dict(total), "tokens": token_summary(all_toks), "groups": groups_out,
           "gate": gate, "gate_passes": all(gate.values()),
           "provenance": public_provenance(provenance([got[d] for d in every if d in got], docs, spans))}
    forbidden = set(every) | {r["pair_id"] for r in samp} | {r["chunk_id"] for r in samp}
    return write_public(out, rec, forbidden)


def token_summary(toks: Sequence[int]) -> dict[str, Any]:
    if not toks:
        return {"n": 0}
    s = sorted(toks)

    def rank(q: float) -> int:
        return s[max(0, math.ceil(q * len(s)) - 1)]

    return {"n": len(s), "mean": round(statistics.fmean(s), 2), "p10": rank(0.10), "p50": rank(0.50),
            "p90": rank(0.90), "max": s[-1], "min": s[0], "share_over_target": round(sum(t > TARGET for t in s) / len(s), 4),
            "share_over_ceiling": round(sum(t > CEILING for t in s) / len(s), 4),
            "share_under_min_piece": round(sum(t < MIN_PIECE for t in s) / len(s), 4)}


# ------------------------------------------------------------------ draw (§4)

def control_nominal(factor: int = NOMINAL_FACTOR) -> dict[str, int]:
    import alias_study_spot as asp

    return {s: factor * int(v) for s, v in asp.NOMINAL.items()}


def draw(final: Sequence[Mapping[str, Any]], factor: int = NOMINAL_FACTOR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The spot check's draw (``alias_study_spot.draw_pairs_from``) at ``factor`` x its nominal sizes."""
    import alias_study_spot as asp

    return asp.draw_pairs_from(final, nominal=control_nominal(factor))


def cells(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        out[(r["stratum"], r["arm"])].append(r["pair_id"])
    return dict(out)


def verify_spot_prefix(final: Sequence[Mapping[str, Any]], picked: Sequence[Mapping[str, Any]],
                       key: Mapping[str, Any]) -> dict[str, Any]:
    """The spot key's pairs are the x1 draw's, cell by cell, and every x1 cell is a prefix of the
    control draw's cell (module docstring): same pairs, same order, at the head. The control cell
    must also be larger than the spot cell unless the cell has no more pairs to give (a cell the x1
    draw already exhausted). Raises SystemExit; returns the per-cell counts."""
    one, one_counts = draw(final, 1)
    avail = one_counts["available"]
    c1, c3 = cells(one), cells(picked)
    kcells: dict[tuple[str, str], set[str]] = defaultdict(set)
    for v in (key.get("items") or {}).values():
        kcells[(v["stratum"], v["arm"])].add(v["pair_id"])
    if {k: set(v) for k, v in c1.items()} != dict(kcells):
        raise SystemExit("REFUSED: the spot key's pairs are not the x1 draw's (stratum, arm) cells: another draw, "
                         "other stage-A labels or an edited key")
    out = {}
    for k, ids in sorted(c1.items()):
        have = c3.get(k, [])
        if len(have) < len(ids) or have[:len(ids)] != ids:
            raise SystemExit(f"REFUSED: the spot sheet's {k[0]}/{k[1]} cell is not the head of the control draw's")
        if len(have) == len(ids) and int(avail[k[0]][k[1]]) > len(ids):
            raise SystemExit(f"REFUSED: the control draw's {k[0]}/{k[1]} cell is no larger than the spot sheet's, "
                             "though the cell has more pairs: not a x3 draw")
        out[f"{k[0]}|{k[1]}"] = {"spot": len(ids), "control": len(have)}
    for k, ids in c3.items():
        out.setdefault(f"{k[0]}|{k[1]}", {"spot": 0, "control": len(ids)})
    return out


def run_draw(judge_config: Path, spot_key: Path, spot_sheet: Path, out_dir: Path) -> dict[str, Any]:
    import alias_study_spot as asp

    env = require_env()
    asp.refuse_in_repo(out_dir, "--out-dir")
    cfg = judge_cfg(judge_config)
    key = json.loads(spot_key.read_text(encoding="utf-8"))
    if key.get("schema") != asp.KEY_SCHEMA or key.get("kind") != "judge":
        raise SystemExit("--spot-key is not the judged-pair spot key")
    sheet_sha = asc.sha256_file(spot_sheet)
    if sheet_sha != key.get("sheet_sha256"):
        raise SystemExit("REFUSED: the spot sheet's sha256 is not the spot key's")
    rebuilt, ordered, sa, extra = asp.judge_sheet(cfg)
    if asc.sha_text(asp.canonical(rebuilt).decode("utf-8")) != key["sheet_sha256"]:
        raise SystemExit("REFUSED: the spot sheet re-derived from its sources is not the key's sheet")
    if sa["commit_sha256"] != key.get("stage_a_commit_sha256"):
        raise SystemExit("REFUSED: the stage-A commit is not the one the spot sheet was drawn from")
    asp.check_key_ids(key, rebuilt["items"], [f["pair_id"] for f in ordered])
    picked, counts = draw(sa["final"], NOMINAL_FACTOR)
    prefix = verify_spot_prefix(sa["final"], picked, key)
    spot_ids = {v["pair_id"] for v in key["items"].values()}
    rows = [{"pair_id": f["pair_id"], "record_id": f["record_id"], "chunk_id": f["chunk_id"],
             "stratum": f["stratum"], "arm": f["arm"], "in_spot_sheet": f["pair_id"] in spot_ids} for f in picked]
    sha = write_private(out_dir / "sample.jsonl", jsonl_bytes(rows), "the control sample")
    rec = {"schema": SCHEMA["draw"], "exploratory": True, "seed": asc.SEEDS["spot_judge"],
           "nominal": control_nominal(), "factor": NOMINAL_FACTOR,
           "draw_rule": "alias_study_spot.draw_pairs_from at 3 x the spot check's nominal sizes, over stage A's "
                        "committed final labels",
           "counts": counts, "spot_prefix": prefix, "spot_pairs_in_sample": sum(r["in_spot_sheet"] for r in rows),
           "sample_sha256": sha, "spot_sheet_sha256": key["sheet_sha256"],
           "stage_a_commit_sha256": sa["commit_sha256"], "judge_inputs": extra["inputs"], "environment": env,
           "provenance": public_provenance(provenance())}
    forbidden = {r["pair_id"] for r in rows} | {r["record_id"] for r in rows} | {r["chunk_id"] for r in rows}
    write_summary(out_dir / "draw.json", rec, "draw.json", forbidden)
    return {"n": len(rows), "counts": counts, "spot_prefix": prefix, "sample_sha256": sha}


# ------------------------------------------------------------------ chunk

def run_chunk(chunk_exports: Mapping[str, str], texts: Sequence[Path], sample: Path, out_dir: Path
              ) -> dict[str, Any]:
    import alias_study_spot as asp

    env = require_env()
    asp.refuse_in_repo(out_dir, "--out-dir")
    ex = read_exports(chunk_exports)
    samp = asc.read_jsonl(sample)
    doc_ids = sample_doc_ids(samp, ex)
    got, res = resolve_docs(doc_ids, ex, texts)
    cc = control_chunker()
    rows, drows, docs, spans, ok_texts = [], [], [], [], []
    p2: Counter[str] = Counter()
    toks_all: list[int] = []
    for d in doc_ids:
        first = ex.by_doc[d][0]
        base = {"doc_id": d, "arm": ex.arm_of_doc[d], "stage_a_chunks": len(ex.by_doc[d])}
        if d not in got:
            drows.append({**base, "status": "excluded"})
            continue
        cd = chunk_document(d, got[d], cc)
        c, toks, piece = measure_document(cd, cc)
        p2.update(c)
        toks_all += toks
        docs.append(cd.doc)
        ok_texts.append(got[d])
        spans.append([(ch.start_char, ch.end_char) for ch in cd.chunks])
        drows.append({**base, "status": "ok", "text_sha256": asc.sha_text(got[d]), "chars": len(got[d]),
                      "latex_preamble": has_latex_preamble(got[d]), "control_chunks": len(cd.chunks),
                      **section_info(cd.doc)})
        for ch, t, pc in zip(cd.chunks, toks, piece, strict=True):
            md = {k: ch.metadata[k] for k in sorted(ch.metadata) if k in _stamp_keys()}
            rows.append({"chunk_id": ch.id, "doc_id": d, "content": ch.content, "start_char": ch.start_char,
                         "end_char": ch.end_char,
                         "metadata": {"source_path": first.source_path, "title": first.title, **md,
                                      "chunk_descriptor": CONTROL_DESCRIPTOR, "tokens": t, "split_sentence": pc}})
    csha = write_private(out_dir / "chunks-control.jsonl", jsonl_bytes(rows), "the control chunks")
    dsha = write_private(out_dir / "docs.jsonl", jsonl_bytes(drows), "the control documents")
    rec = {"schema": SCHEMA["chunk"], "exploratory": True, "chunker": cc.applied(), "environment": env,
           "documents": len(doc_ids), "resolution": dict(res), "chunks": len(rows), "p2_counts": dict(p2),
           "p2_gate": p2_gate(p2, True), "tokens": token_summary(toks_all),
           "chunks_sha256": csha, "docs_sha256": dsha, "sample_sha256": asc.sha256_file(sample),
           "provenance": public_provenance(provenance(ok_texts, docs, spans))}
    forbidden = set(doc_ids) | {r["chunk_id"] for r in rows} | {r["pair_id"] for r in samp}
    write_summary(out_dir / "chunk.json", rec, "chunk.json", forbidden)
    return {"documents": len(doc_ids), "resolution": dict(res), "chunks": len(rows), "chunks_sha256": csha}


def _stamp_keys() -> tuple[str, ...]:
    from ragstack.ingestion.structure import STAMP_KEYS

    return STAMP_KEYS


# ------------------------------------------------------------------ map (§3)

def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def primary_map(ws: int, we: int, spans: Sequence[tuple[int, int, str]]) -> dict[str, Any] | None:
    """``max-overlap``: the span with the most characters of ``[ws, we)``, ties to the earlier
    (``spans`` in document order); ``None`` when nothing overlaps."""
    best: tuple[int, int, int, str] | None = None
    n = 0
    for s, e, cid in sorted(spans, key=lambda x: (x[0], x[1])):
        ov = overlap(ws, we, s, e)
        if ov > 0:
            n += 1
            if best is None or ov > best[0]:
                best = (ov, s, e, cid)
    if best is None or we <= ws:
        return None
    ov, s, e, cid = best
    return {"chunk_id": cid, "overlap_chars": ov, "coverage": round(ov / (we - ws), 6),
            "purity": round(ov / (e - s), 6), "n_overlapping": n}


def cover_any(ws: int, we: int, spans: Sequence[tuple[int, int, str]], cap: int = COVER_ANY_CAP
              ) -> tuple[list[dict[str, Any]], bool]:
    """Every span overlapping ``[ws, we)``, the ``cap`` largest overlaps when there are more (ties
    to the earlier), in document order; and whether the cap applied."""
    hits = [(overlap(ws, we, s, e), s, e, cid) for s, e, cid in sorted(spans, key=lambda x: (x[0], x[1]))]
    hits = [h for h in hits if h[0] > 0]
    kept = sorted(sorted(hits, key=lambda h: (-h[0], h[1]))[:cap], key=lambda h: h[1])
    return ([{"chunk_id": cid, "overlap_chars": ov, "coverage": round(ov / (we - ws), 6),
              "purity": round(ov / (e - s), 6)} for ov, s, e, cid in kept], len(hits) > cap)


def control_docs(chunks_path: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in asc.read_jsonl(chunks_path):
        out[r["doc_id"]].append(r)
    for rows in out.values():
        rows.sort(key=lambda r: (r["start_char"], r["end_char"]))
    return dict(out)


def doc_text(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The record text the control chunks tile, or ``None`` when they do not tile from 0."""
    pos, parts = 0, []
    for r in rows:
        if r["start_char"] != pos or len(r["content"]) != r["end_char"] - r["start_char"]:
            return None
        parts.append(r["content"])
        pos = r["end_char"]
    return "".join(parts)


def read_pair_ids(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.add(json.loads(line)["pair_id"] if line.startswith("{") else line)
    return out


def run_map(chunk_exports: Mapping[str, str], sample: Path, control_dir: Path, *, cover: bool = False,
            pairs: Path | None = None) -> dict[str, Any]:
    import alias_study_spot as asp

    env = require_env()
    asp.refuse_in_repo(control_dir, "--control-dir")
    ex = read_exports(chunk_exports)
    samp = asc.read_jsonl(sample)
    if cover:
        if pairs is None:
            raise SystemExit("--cover-any needs --pairs (the flipped stage-A pair ids)")
        want = read_pair_ids(pairs)
        unknown = want - {r["pair_id"] for r in samp}
        if unknown:
            raise SystemExit(f"{len(unknown)} --pairs id(s) are not in the control sample")
        samp = [r for r in samp if r["pair_id"] in want]
    cdocs = control_docs(control_dir / "chunks-control.jsonl")
    dinfo = {r["doc_id"]: r for r in asc.read_jsonl(control_dir / "docs.jsonl")}
    texts: dict[str, str] = {}
    excluded: Counter[str] = Counter()
    seeds: list[dict[str, Any]] = []
    capped = 0
    for r in samp:
        w = ex.by_id.get(r["chunk_id"])
        if w is None:
            excluded["chunk_not_in_exports"] += 1
            continue
        rows = cdocs.get(w.doc_id)
        if not rows or (dinfo.get(w.doc_id) or {}).get("status") != "ok":
            excluded["document_not_chunked"] += 1
            continue
        if w.doc_id not in texts:
            t = doc_text(rows)
            if t is None:
                excluded["control_chunks_do_not_tile"] += 1
                continue
            texts[w.doc_id] = t
        text = texts[w.doc_id]
        if text[w.start_char:w.end_char] != w.content:
            excluded["stage_a_offsets"] += 1
            continue
        spans = [(c["start_char"], c["end_char"], c["chunk_id"]) for c in rows]
        tok = {c["chunk_id"]: c["metadata"].get("tokens") for c in rows}
        base = {"control_of": r["pair_id"], "record_id": r["record_id"], "stage_a_chunk_id": r["chunk_id"],
                "doc_id": w.doc_id, "stratum": r["stratum"], "arm": r["arm"], "in_spot_sheet": r["in_spot_sheet"],
                "window_chars": w.end_char - w.start_char,
                "latex_preamble": bool(dinfo[w.doc_id].get("latex_preamble"))}
        if cover:
            hits, was_capped = cover_any(w.start_char, w.end_char, spans)
            capped += was_capped
            seeds += [{**base, **h, "control_tokens": tok[h["chunk_id"]], "cover_capped": was_capped} for h in hits]
        else:
            m = primary_map(w.start_char, w.end_char, spans)
            if m is None:
                excluded["no_overlap"] += 1
                continue
            seeds.append({**base, **m, "control_tokens": tok[m["chunk_id"]]})
    name = "pairs-seed-cover" if cover else "pairs-seed"
    sha = write_private(control_dir / f"{name}.jsonl", jsonl_bytes(seeds), f"the {name} file")
    docs = sorted(texts)
    structs = [chunk_structure(d, texts[d]) for d in docs]
    spans_all = [[(c["start_char"], c["end_char"]) for c in cdocs[d]] for d in docs]
    rec: dict[str, Any] = {
        "schema": SCHEMA["map"], "exploratory": True, "rule": "cover-any" if cover else "max-overlap",
        "tie_rule": "the earlier control chunk", "pairs_in": len(samp),
        "pairs_mapped": len({s["control_of"] for s in seeds}), "seed_rows": len(seeds), "excluded": dict(excluded),
        "per_stratum": dict(Counter(s["stratum"] for s in seeds if cover is False)),
        "coverage_bands": dict(Counter(band(s["coverage"], COVERAGE_BANDS) for s in seeds)),
        "token_bands": dict(Counter(band(s["control_tokens"] or 0, TOKEN_BANDS) for s in seeds)),
        "n_overlapping": dict(Counter(str(s.get("n_overlapping")) for s in seeds)) if not cover else None,
        "seed_sha256": sha, "sample_sha256": asc.sha256_file(sample), "environment": env,
        "chunks_sha256": asc.sha256_file(control_dir / "chunks-control.jsonl"),
        "provenance": public_provenance(provenance([texts[d] for d in docs], structs, spans_all))}
    if cover:
        rec["cover_any_cap"] = COVER_ANY_CAP
        rec["windows_capped"] = capped
    forbidden = set(docs) | {s["control_of"] for s in seeds} | {s["record_id"] for s in seeds} | \
        {s["chunk_id"] for s in seeds} | {s["stage_a_chunk_id"] for s in seeds}
    write_summary(control_dir / ("map-cover.json" if cover else "map.json"), rec, "the map summary", forbidden)
    return {k: rec[k] for k in ("rule", "pairs_in", "pairs_mapped", "seed_rows", "excluded", "seed_sha256")}


def chunk_structure(doc_id: str, text: str) -> Any:
    """The document with its adapter structure (for the sections fingerprint), no chunking."""
    from ragstack.ingestion.structure import attach_structure
    from ragstack.models import Document

    doc = Document(id=doc_id, content=text, source=f"{doc_id}.md")
    attach_structure([doc], None, Counter())
    return doc


def band(x: float, bands: Sequence[tuple[float, float, str]]) -> str:
    for lo, hi, name in bands:
        if lo <= x < hi:
            return name
    return "none"


# ------------------------------------------------------------------ retest-seed (the test-retest null)

def stage_a_window_rows(chunk_exports: Mapping[str, str], want: set[str]) -> dict[str, tuple[str, dict[str, Any]]]:
    """``chunk_id -> (line, row)``: the stage-A export rows of the wanted chunk ids, verbatim (the
    original line without its newline, and the parsed object), first export in corpus-arm order;
    a chunk id seen again with other content is refused."""
    import _glossbench_r2 as g

    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for arm in asc.CORPUS_ARMS:
        path = chunk_exports.get(arm)
        if not path:
            continue
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                row = g.parse_chunk(obj, f"{Path(path).name}:{i}")
                if row.chunk_id not in want:
                    continue
                prev = out.get(row.chunk_id)
                if prev is not None:
                    p = g.parse_chunk(prev[1], "prev")
                    if (p.content, p.start_char, p.end_char) != (row.content, row.start_char, row.end_char):
                        raise SystemExit(f"{Path(path).name}:{i}: chunk {row.chunk_id[:8]}… seen with other content")
                    continue
                out[row.chunk_id] = (line.rstrip("\r\n"), obj)
    return out


def retest_export_bytes(rows: Iterable[tuple[str, Mapping[str, Any]]]) -> tuple[bytes, int]:
    """The retest export: each original line kept byte for byte when it is pure ASCII (so no
    ``str.splitlines`` separator can hide in it), else the same object re-serialized ASCII-escaped
    with its key order kept (:func:`jsonl_bytes`' reason). Returns the bytes and the re-escaped count."""
    parts, escaped = [], 0
    for line, obj in rows:
        if line.isascii():
            parts.append(line + "\n")
        else:
            parts.append(json.dumps(obj, ensure_ascii=True) + "\n")
            escaped += 1
    return "".join(parts).encode("utf-8"), escaped


def run_retest_seed(judge_config: Path, control_dir: Path, out_dir: Path) -> dict[str, Any]:
    """``retest-seed`` (module docstring, *retest*): the control's seed pairs re-pointed at their own
    stage-A windows, and those windows as a control-shaped export."""
    import _glossbench_r2 as g
    import alias_study_judge as aj
    import alias_study_spot as asp

    env = require_env()
    asp.refuse_in_repo(out_dir, "--out-dir")
    cfg = judge_cfg(judge_config)
    seeds = asc.read_jsonl(control_dir / "pairs-seed.jsonl")
    mout = Path(cfg["out_dir"])
    main_pairs = {p["pair_id"]: p for p in asc.read_jsonl(mout / "pairs.jsonl")}
    for i, s in enumerate(seeds, 1):
        a = main_pairs.get(s["control_of"])
        if a is None or a.get("stage", "A") != "A":
            raise SystemExit(f"REFUSED: seed row {i}: control_of is not a stage-A pair of the main registry")
        if a["record_id"] != s["record_id"] or a["chunk_id"] != s["stage_a_chunk_id"]:
            raise SystemExit(f"REFUSED: seed row {i}: its stage_a_chunk_id/record_id is not that stage-A pair's")
        if aj.pair_id(s["record_id"], s["stage_a_chunk_id"]) != s["control_of"]:
            raise SystemExit(f"REFUSED: seed row {i}: (record, stage-A chunk) does not give the stage-A pair id")
    want = {s["stage_a_chunk_id"] for s in seeds}
    found = stage_a_window_rows(cfg["chunk_exports"], want)
    missing = want - set(found)
    if missing:
        raise SystemExit(f"REFUSED: {len(missing)} stage-A window(s) are in no chunk export of the judge config")
    order = sorted(want)
    data, escaped = retest_export_bytes(found[c] for c in order)
    csha = write_private(out_dir / "chunks-retest.jsonl", data, "the retest windows")
    rseeds = [{"control_of": s["control_of"], "record_id": s["record_id"], "chunk_id": s["stage_a_chunk_id"],
               "stage_a_chunk_id": s["stage_a_chunk_id"], "stratum": s["stratum"], "arm": s["arm"],
               "in_spot_sheet": s.get("in_spot_sheet")} for s in seeds]
    ssha = write_private(out_dir / "pairs-seed-retest.jsonl", jsonl_bytes(rseeds), "the retest seed pairs")
    rec = {"schema": SCHEMA["retest"], "exploratory": True, "seed_rows": len(rseeds),
           "stage_a_pairs": len({s["control_of"] for s in rseeds}), "windows": len(order),
           "windows_reescaped": escaped, "per_stratum": dict(Counter(s["stratum"] for s in rseeds)),
           "per_arm": dict(Counter(s["arm"] for s in rseeds)),
           "chunks_sha256": csha, "seed_sha256": ssha,
           "control_seed_sha256": asc.sha256_file(control_dir / "pairs-seed.jsonl"),
           "chunk_exports_sha256": {a: asc.sha256_file(p) for a, p in sorted(cfg["chunk_exports"].items())},
           "environment": env, "provenance": public_provenance(provenance())}
    doc_ids = {g.parse_chunk(found[c][1], c).doc_id for c in order}
    forbidden = {s["control_of"] for s in rseeds} | {s["record_id"] for s in rseeds} | want | doc_ids
    write_summary(out_dir / "retest-seed.json", rec, "retest-seed.json", forbidden)
    return {k: rec[k] for k in ("seed_rows", "stage_a_pairs", "windows", "windows_reescaped", "chunks_sha256",
                                "seed_sha256")}


# ------------------------------------------------------------------ report (§5)

def label_of(f: Mapping[str, Any] | None) -> int | None:
    """A final row's label, unjudged = *not* (0) as in the primary analysis; ``None`` = no final row."""
    if f is None:
        return None
    return int(bool(f.get("label")))


def wilson(k: int, n: int) -> dict[str, Any]:
    import alias_study_stats as ast

    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson95": ast._wilson(k, n)}


def ends_sentence(text: str) -> bool:
    return text.rstrip().endswith((".", "!", "?"))


def starts_capital_or_digit(text: str) -> bool:
    t = text.lstrip()
    return bool(t) and (t[0].isupper() or t[0].isdigit())


def holds_heading_line(text: str) -> bool:
    return text.startswith("## ") or "\n## " in text


COVER_SPLIT = ("other_only", "both", "primary_only", "none")


def cover_split(covered: Sequence[Mapping[str, Any]], direction: Any) -> dict[str, Any]:
    """The ``cover-any`` split of the flipped pairs (module docstring, *report*): per direction, whether
    a NON-primary overlapping chunk answers and whether the re-judged primary chunk answers (a primary
    without a cover label counts as not answering, and is counted); and the same-bytes re-judge of
    the primary chunk against the control run's label, on these selected (flipped) pairs only."""
    def one(pool: Sequence[Mapping[str, Any]], recovery: bool) -> dict[str, Any]:
        c: Counter[str] = Counter()
        for r in pool:
            o = any(x == 1 for x in r.get("cover_other") or [])
            p = r.get("cover_primary") == 1
            c["both" if o and p else "other_only" if o else "primary_only" if p else "none"] += 1
        out: dict[str, Any] = {"n": len(pool), **{k: c[k] for k in COVER_SPLIT},
                               "primary_without_cover_label": sum(r.get("cover_primary") is None for r in pool),
                               "primary_rejudge_agreement_selected": rejudge(pool)}
        if recovery:
            out["recovered_by_other_chunk"] = wilson(c["other_only"] + c["both"], len(pool))
        return out

    def rejudge(pool: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        have = [r for r in pool if r.get("cover_primary") is not None]
        return wilson(sum(r["cover_primary"] == label_of(r["control"]) for r in have), len(have))

    pool = [r for r in covered if "cover_other" in r]
    # by stage-A route, per direction; recovered_by_other_chunk is shown for both directions here
    # (for not_to_answers it only counts a non-primary chunk that answers too, never a recovery)
    by_route = {g: {d: one([r for r in pool if route(r["stage_a"]) == g and direction(r) == d], True)
                    for d in ("answers_to_not", "not_to_answers")}
                for g in ("agree", "tiebreak_or_other")}
    return {"answers_to_not": one([r for r in pool if direction(r) == "answers_to_not"], True),
            "not_to_answers": one([r for r in pool if direction(r) == "not_to_answers"], False),
            "primary_rejudge_agreement_selected": rejudge(pool), "by_route": by_route}


def route(f: Mapping[str, Any] | None) -> str:
    """A final row's route: ``agree`` (both Qwen stages agreed) or ``tiebreak_or_other``."""
    return "agree" if (f or {}).get("source") == "agree" else "tiebreak_or_other"


def compute_retest(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The test-retest null (module docstring, *retest*) from one row per retest seed row:
    ``stage_a`` / ``retest`` (the final rows, or None), ``stratum``, ``arm``. Counts and rates only."""
    import alias_study_stats as ast

    judged = [r for r in rows if r["retest"] is not None and r["stage_a"] is not None]

    def flipped(r: Mapping[str, Any]) -> bool:
        return label_of(r["retest"]) != label_of(r["stage_a"])

    def by(key: Any) -> dict[str, Any]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in judged:
            groups[str(key(r))].append(r)
        return {g: wilson(sum(map(flipped, v)), len(v)) for g, v in sorted(groups.items())}

    flips = [r for r in judged if flipped(r)]
    c = Counter((label_of(r["retest"]), label_of(r["stage_a"])) for r in judged)
    esc_a = {i for i, r in enumerate(judged) if route(r["stage_a"]) != "agree"}
    esc_r = {i for i, r in enumerate(judged) if route(r["retest"]) != "agree"}
    union = esc_a | esc_r
    return {
        "n_seed": len(rows), "n_judged_both": len(judged),
        "n_without_retest_label": sum(r["retest"] is None for r in rows),
        "flip": wilson(len(flips), len(judged)),
        "by_direction": {"answers_to_not": sum(label_of(r["stage_a"]) == 1 for r in flips),
                         "not_to_answers": sum(label_of(r["stage_a"]) == 0 for r in flips)},
        "direction_rates": {
            "answers_to_not": wilson(sum(label_of(r["stage_a"]) == 1 for r in flips),
                                     sum(label_of(r["stage_a"]) == 1 for r in judged)),
            "not_to_answers": wilson(sum(label_of(r["stage_a"]) == 0 for r in flips),
                                     sum(label_of(r["stage_a"]) == 0 for r in judged))},
        "by_stratum": by(lambda r: r["stratum"]), "by_arm": by(lambda r: r["arm"]),
        "by_route": by(lambda r: route(r["stage_a"])),
        "by_route_retest": by(lambda r: route(r["retest"])),
        "sources": {"stage_a": dict(Counter(str(r["stage_a"].get("source")) for r in judged)),
                    "retest": dict(Counter(str(r["retest"].get("source")) for r in judged))},
        "agreement_retest_vs_stage_a": ast.agreement_metrics(c[(1, 1)], c[(1, 0)], c[(0, 1)], c[(0, 0)]),
        "escalation_overlap": {"stage_a": len(esc_a), "retest": len(esc_r), "both": len(esc_a & esc_r),
                               "stage_a_only": len(esc_a - esc_r), "retest_only": len(esc_r - esc_a),
                               "jaccard": round(len(esc_a & esc_r) / len(union), 4) if union else None},
    }


def tiebreak_replicate(pair_ids: Iterable[str], stage_a_argon: Mapping[str, Mapping[str, Any]],
                       retest_argon: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The Argon tiebreak replicate (module docstring, *retest*): over the given (retest registry)
    pair ids with an Argon label row in both the stage-A and the retest judge dir, how many share
    the prompt and system sha256, and how often the two Argon verdicts agree (rows where both
    verdicts are non-null; the rest are counted in ``n_null_verdict``). Counts and rates only."""
    both = [(stage_a_argon[p], retest_argon[p]) for p in sorted(set(pair_ids))
            if p in stage_a_argon and p in retest_argon]
    have = [(a, b) for a, b in both if a.get("verdict") is not None and b.get("verdict") is not None]
    return {"n": len(both),
            "same_prompt_sha256": sum(a.get("prompt_sha256") == b.get("prompt_sha256") for a, b in both),
            "same_system_sha256": sum(a.get("system_sha256") == b.get("system_sha256") for a, b in both),
            "n_null_verdict": len(both) - len(have),
            "verdict_agreement": wilson(sum(a["verdict"] == b["verdict"] for a, b in have), len(have))}


def argon_labels(out_dir: Path) -> dict[str, dict[str, Any]]:
    """The Argon label rows of a judge dir by pair id (as ``alias_study_judge`` reads them)."""
    import alias_study_judge as aj
    import glossbench_alias_judge as jd

    return {r["pair_id"]: r for r in jd.p4.read_jsonl_repair_tail(out_dir / aj.LABEL_FILES["argon"])}


def compute_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """§5 from one row per mapped stage-A pair: ``stage_a`` / ``control`` (the final rows, or None),
    ``stratum``, ``arm``, ``coverage``, ``control_tokens``, ``latex_preamble``, ``cover`` (the
    cover-any final labels of the pair, or None), ``window`` / ``control_text`` (the passages, for
    the boundary diagnostics only), ``section_crossing``, ``split_sentence``, ``window_tokens``.
    Counts and rates only."""
    import alias_study_stats as ast

    judged = [r for r in rows if r["control"] is not None and r["stage_a"] is not None]
    flips = [r for r in judged if label_of(r["control"]) != label_of(r["stage_a"])]

    def direction(r: Mapping[str, Any]) -> str:
        return "answers_to_not" if label_of(r["stage_a"]) == 1 else "not_to_answers"

    def by(key: Any, pool: Sequence[Mapping[str, Any]] = judged) -> dict[str, Any]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in pool:
            groups[str(key(r))].append(r)
        return {g: wilson(sum(label_of(r["control"]) != label_of(r["stage_a"]) for r in v), len(v))
                for g, v in sorted(groups.items())}

    c = Counter((label_of(r["control"]), label_of(r["stage_a"])) for r in judged)
    table = ast.agreement_metrics(c[(1, 1)], c[(1, 0)], c[(0, 1)], c[(0, 0)])

    def stage_detail(side: str) -> dict[str, Any]:
        fs = [r[side] for r in judged]
        n = len(fs)
        return {"n": n, "c_answers": wilson(sum(f.get("c_label") == 1 for f in fs), n),
                "e_answers": wilson(sum(f.get("e_label") == 1 for f in fs), n),
                "escalated": wilson(sum(f.get("source") != "agree" for f in fs), n),
                "unjudged": sum(f.get("source") == "unjudged" for f in fs),
                "sources": dict(Counter(str(f.get("source")) for f in fs))}

    covered = [r for r in flips if r.get("cover") is not None]
    # answers -> not: the evidence may lie outside the primary chunk; recovered when any cover-any
    # chunk answers. not -> answers: the primary chunk answered and is in the cover set, so
    # cover-any answers by construction; reported as a consistency count, never as a recovery.
    a2n = [r for r in covered if direction(r) == "answers_to_not"]
    n2a = [r for r in covered if direction(r) == "not_to_answers"]
    shared = Counter(r["control_pair"] for r in rows if r.get("control_pair"))

    def diag(texts: Sequence[str]) -> dict[str, Any]:
        n = len(texts)
        return {"n": n, "ends_in_sentence_punctuation": wilson(sum(map(ends_sentence, texts)), n),
                "starts_capital_or_digit": wilson(sum(map(starts_capital_or_digit, texts)), n),
                "holds_a_heading_line": wilson(sum(map(holds_heading_line, texts)), n)}

    windows = [r["window"] for r in rows]
    controls = [r["control_text"] for r in rows]
    cover_any_block: dict[str, Any] = {
        "flipped": len(flips), "flipped_with_cover_any": len(covered),
        "answers_to_not": {"with_cover_any": len(a2n), "recovered": wilson(
            sum(any(x == 1 for x in r["cover"]) for r in a2n), len(a2n))},
        "not_to_answers": {"with_cover_any": len(n2a), "cover_any_answers": wilson(
            sum(any(x == 1 for x in r["cover"]) for r in n2a), len(n2a))}}
    if any("cover_other" in r for r in covered):
        cover_any_block["split"] = cover_split(covered, direction)
    return {
        "n_mapped": len(rows), "n_judged_both": len(judged),
        "n_without_control_label": sum(r["control"] is None for r in rows),
        "flip": wilson(len(flips), len(judged)),
        "by_direction": dict(Counter(direction(r) for r in flips)),
        "direction_rates": {"answers_to_not": wilson(sum(direction(r) == "answers_to_not" for r in flips),
                                                     sum(label_of(r["stage_a"]) == 1 for r in judged)),
                            "not_to_answers": wilson(sum(direction(r) == "not_to_answers" for r in flips),
                                                     sum(label_of(r["stage_a"]) == 0 for r in judged))},
        "by_stratum": by(lambda r: r["stratum"]), "by_arm": by(lambda r: r["arm"]),
        "by_route": by(lambda r: route(r["stage_a"])),
        "by_stratum_direction": dict(Counter(f"{r['stratum']}|{direction(r)}" for r in flips)),
        "by_latex_preamble": by(lambda r: "latex_preamble" if r["latex_preamble"] else "no_latex_preamble"),
        "by_coverage_band": by(lambda r: band(r["coverage"], COVERAGE_BANDS)),
        "by_control_token_band": by(lambda r: band(r["control_tokens"] or 0, TOKEN_BANDS)),
        "by_spot_sheet": by(lambda r: "spot_pair" if r.get("in_spot_sheet") else "added_pair"),
        "by_section_structure": by(lambda r: "one_untitled_section" if r.get("one_untitled_section")
                                   else "sectioned"),
        "control_pairs_shared": {"control_pairs": len(shared),
                                 "shared_by_two_or_more_windows": sum(v > 1 for v in shared.values()),
                                 "windows_on_shared_control_pairs": sum(v for v in shared.values() if v > 1)},
        "agreement_control_vs_stage_a": table,
        "judge_stage": {"stage_a": stage_detail("stage_a"), "control": stage_detail("control")},
        "cover_any": cover_any_block,
        "boundary": {
            "windows": diag(windows), "control": diag(controls),
            "control_section_crossing": wilson(sum(bool(r.get("section_crossing")) for r in rows), len(rows)),
            "control_split_sentence_pieces": wilson(sum(bool(r.get("split_sentence")) for r in rows), len(rows)),
            "window_tokens": token_summary([int(r["window_tokens"]) for r in rows if r.get("window_tokens") is not None]),
            "control_tokens": token_summary([int(r["control_tokens"]) for r in rows if r.get("control_tokens") is not None]),
            "one_untitled_section_pairs": wilson(sum(bool(r.get("one_untitled_section")) for r in rows), len(rows)),
        },
    }


def final_pairs(out_dir: Path) -> dict[str, dict[str, Any]]:
    import glossbench_alias_judge as jd

    return {r["pair_id"]: r for r in jd.p4.read_jsonl_repair_tail(out_dir / "final.jsonl")
            if str(r.get("k", "")).startswith("pair|")}


def judge_files(out_dir: Path) -> dict[str, Any]:
    """Unjudged / filtered counts per backend and gateway calls (§5.6), from the judge dir."""
    import alias_study_judge as aj

    out: dict[str, Any] = {}
    for b, name in aj.LABEL_FILES.items():
        p = out_dir / name
        out[f"label_rows_{b}"] = sum(1 for x in p.read_text(encoding="utf-8").splitlines() if x.strip()) \
            if p.is_file() else 0
    for b in ("argon", "opus"):
        out[f"gateway_calls_{b}"] = aj.gateway_calls_so_far(out_dir, b)
    return out


def cover_labels(vreg: Iterable[Mapping[str, Any]], vfin: Mapping[str, Mapping[str, Any]],
                 primary: Mapping[str, str]) -> tuple[dict[str, list[int]], dict[str, int], dict[str, list[int]]]:
    """The cover-any final labels per stage-A pair id: all of them, the primary chunk's (the
    ``pairs-seed.jsonl`` chunk of that ``control_of``) and the others'. Unjudged = *not*."""
    every: dict[str, list[int]] = defaultdict(list)
    prim: dict[str, int] = {}
    other: dict[str, list[int]] = defaultdict(list)
    for p in vreg:
        if p["pair_id"] not in vfin:
            continue
        lab = int(bool(vfin[p["pair_id"]].get("label")))
        for sid in p["control_of"]:
            every[sid].append(lab)
            if p["chunk_id"] == primary.get(sid):
                prim[sid] = lab
            else:
                other[sid].append(lab)
    return dict(every), prim, dict(other)


def retest_rows(rseeds: Sequence[Mapping[str, Any]], stage_a: Mapping[str, Mapping[str, Any]],
                rfinal: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One :func:`compute_retest` row per retest seed row. The retest pair id must be the stage-A
    pair id (same record, same window); anything else is refused."""
    import alias_study_judge as aj

    rows = []
    for i, s in enumerate(rseeds, 1):
        if aj.pair_id(s["record_id"], s["chunk_id"]) != s["control_of"]:
            raise SystemExit(f"retest seed row {i}: its (record, chunk) pair is not the stage-A pair it retests")
        rows.append({"stage_a": stage_a.get(s["control_of"]), "retest": rfinal.get(s["control_of"]),
                     "stratum": s["stratum"], "arm": s["arm"]})
    return rows


def run_report(judge_config: Path, control_config: Path, control_dir: Path, out: Path,
               cover_config: Path | None = None, retest_config: Path | None = None,
               retest_dir: Path | None = None) -> dict[str, Any]:
    import alias_study_judge as aj
    import alias_study_spot as asp

    if (retest_config is None) != (retest_dir is None):
        raise SystemExit("--retest-config and --retest-dir go together")
    env = require_env()
    cfg = judge_cfg(judge_config)
    ccfg = aj.load_config(control_config)
    sa = asp.committed_stage_a(cfg)
    stage_a = {f["pair_id"]: f for f in sa["final"]}
    ex = read_exports(cfg["chunk_exports"])
    seeds = asc.read_jsonl(control_dir / "pairs-seed.jsonl")
    cdocs = control_docs(control_dir / "chunks-control.jsonl")
    dinfo = {r["doc_id"]: r for r in asc.read_jsonl(control_dir / "docs.jsonl")}
    cchunk = {c["chunk_id"]: c for rows in cdocs.values() for c in rows}
    cfinal = final_pairs(Path(ccfg["out_dir"]))
    cover: dict[str, list[int]] = {}
    cover_prim: dict[str, int] = {}
    cover_other: dict[str, list[int]] = {}
    if cover_config is not None:
        vcfg = aj.load_config(cover_config)
        cover, cover_prim, cover_other = cover_labels(
            asc.read_jsonl(Path(vcfg["out_dir"]) / "pairs.jsonl"), final_pairs(Path(vcfg["out_dir"])),
            {s["control_of"]: s["chunk_id"] for s in seeds})
    cc = control_chunker()
    rows = []
    excluded: Counter[str] = Counter()
    for s in seeds:
        w = ex.by_id.get(s["stage_a_chunk_id"])
        k = cchunk.get(s["chunk_id"])
        if w is None or k is None:
            excluded["stage_a_chunk_unknown" if w is None else "control_chunk_unknown"] += 1
            continue
        di = dinfo.get(s["doc_id"]) or {}
        cpid = aj.pair_id(s["record_id"], s["chunk_id"])
        row = {"stage_a": stage_a.get(s["control_of"]), "control": cfinal.get(cpid), "control_pair": cpid,
               "stratum": s["stratum"], "arm": s["arm"], "coverage": s["coverage"],
               "control_tokens": s["control_tokens"], "latex_preamble": s["latex_preamble"],
               "in_spot_sheet": s["in_spot_sheet"], "cover": cover.get(s["control_of"]) if cover_config else None,
               "window": w.content, "control_text": k["content"],
               "window_tokens": cc.counter.count(w.content),
               "section_crossing": k["metadata"].get("section_crossing"),
               "split_sentence": k["metadata"].get("split_sentence"),
               "one_untitled_section": di.get("n_sections", 0) <= 1 and di.get("untitled_only", False)}
        if cover_config is not None and s["control_of"] in cover:
            row["cover_primary"] = cover_prim.get(s["control_of"])
            row["cover_other"] = cover_other.get(s["control_of"], [])
        rows.append(row)
    docs = sorted({s["doc_id"] for s in seeds if s["doc_id"] in cdocs})
    texts = [doc_text(cdocs[d]) or "" for d in docs]
    rec: dict[str, Any] = {"schema": SCHEMA["report"], "exploratory": True,
           "note": "EXPLORATORY, post-freeze: judge-only chunk-boundary control; changes no confirmatory figure. "
                   "Owner agreement (§5.4) dropped (owner, 2026-11-05).",
           "chunker": cc.applied(), "environment": env, "metrics": compute_report(rows),
           "excluded": dict(excluded),
           "judge_files": {"control": judge_files(Path(ccfg["out_dir"]))},
           "inputs": {"seed_sha256": asc.sha256_file(control_dir / "pairs-seed.jsonl"),
                      "chunks_sha256": asc.sha256_file(control_dir / "chunks-control.jsonl"),
                      "stage_a_commit_sha256": sa["commit_sha256"]},
           "provenance": public_provenance(provenance(texts, [chunk_structure(d, t) for d, t in zip(docs, texts,
                                                                                                    strict=True)],
                                                      [[(c["start_char"], c["end_char"]) for c in cdocs[d]]
                                                       for d in docs]))}
    if cover_config is not None:
        rec["judge_files"]["cover_any"] = judge_files(Path(aj.load_config(cover_config)["out_dir"]))
    forbidden = set(docs) | set(stage_a) | set(cfinal) | {s["record_id"] for s in seeds} | \
        {s["chunk_id"] for s in seeds} | {s["stage_a_chunk_id"] for s in seeds}
    if retest_config is not None and retest_dir is not None:
        rcfg = aj.load_config(retest_config)
        rexp = rcfg["chunk_exports"].get(aj.CONTROL_EXPORT)
        rchunks = retest_dir / "chunks-retest.jsonl"
        if not rexp or asc.sha256_file(rexp) != asc.sha256_file(rchunks):
            raise SystemExit(f"REFUSED: the retest config's {aj.CONTROL_EXPORT!r} export is not "
                             f"{rchunks} (sha256 differs)")
        rseeds = asc.read_jsonl(retest_dir / "pairs-seed-retest.jsonl")
        rfinal = final_pairs(Path(rcfg["out_dir"]))
        rmetrics = compute_retest(retest_rows(rseeds, stage_a, rfinal))
        rmetrics["tiebreak_replicate"] = tiebreak_replicate(
            (p["pair_id"] for p in asc.read_jsonl(Path(rcfg["out_dir"]) / "pairs.jsonl")), argon_labels(Path(cfg["out_dir"])), argon_labels(Path(rcfg["out_dir"])))
        rec["metrics"]["retest"] = rmetrics
        cflip, rflip = rec["metrics"]["flip"]["rate"], rmetrics["flip"]["rate"]
        rec["metrics"]["flip_minus_retest"] = {
            "control_flip_rate": cflip, "retest_flip_rate": rflip,
            "difference": round(cflip - rflip, 4) if cflip is not None and rflip is not None else None,
            "note": "display only, exploratory: no test; the retest re-judges the identical stage-A window"}
        rec["judge_files"]["retest"] = judge_files(Path(rcfg["out_dir"]))
        rec["inputs"]["retest_seed_sha256"] = asc.sha256_file(retest_dir / "pairs-seed-retest.jsonl")
        rec["inputs"]["retest_chunks_sha256"] = asc.sha256_file(rchunks)
        forbidden |= set(rfinal) | {s["record_id"] for s in rseeds} | {s["chunk_id"] for s in rseeds}
    passages = [r["window"] for r in rows] + [r["control_text"] for r in rows]
    return write_public(out, rec, forbidden, passages)


# ------------------------------------------------------------------ main

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("--judge-config", required=True, type=Path)
    ck.add_argument("--texts", required=True, type=Path, action="append")
    ck.add_argument("--sample", type=Path, default=None)
    ck.add_argument("--per-arm", type=int, default=CHECK_PER_ARM)
    ck.add_argument("--out", required=True, type=Path)
    dr = sub.add_parser("draw")
    dr.add_argument("--judge-config", required=True, type=Path)
    dr.add_argument("--spot-key", required=True, type=Path)
    dr.add_argument("--spot-sheet", required=True, type=Path)
    dr.add_argument("--out-dir", required=True, type=Path)
    ch = sub.add_parser("chunk")
    ch.add_argument("--judge-config", required=True, type=Path)
    ch.add_argument("--texts", required=True, type=Path, action="append")
    ch.add_argument("--sample", required=True, type=Path)
    ch.add_argument("--out-dir", required=True, type=Path)
    mp = sub.add_parser("map")
    mp.add_argument("--judge-config", required=True, type=Path)
    mp.add_argument("--sample", required=True, type=Path)
    mp.add_argument("--control-dir", required=True, type=Path)
    mp.add_argument("--cover-any", action="store_true")
    mp.add_argument("--pairs", type=Path, default=None)
    rp = sub.add_parser("report")
    rp.add_argument("--judge-config", required=True, type=Path)
    rp.add_argument("--control-config", required=True, type=Path)
    rp.add_argument("--control-dir", required=True, type=Path)
    rp.add_argument("--cover-config", type=Path, default=None)
    rp.add_argument("--retest-config", type=Path, default=None)
    rp.add_argument("--retest-dir", type=Path, default=None)
    rp.add_argument("--out", required=True, type=Path)
    rt = sub.add_parser("retest-seed")
    rt.add_argument("--judge-config", required=True, type=Path)
    rt.add_argument("--control-dir", required=True, type=Path)
    rt.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args(argv)
    os.umask(0o077)
    if a.cmd == "check":
        res = run_check(judge_cfg(a.judge_config)["chunk_exports"], a.texts, a.out, a.sample, a.per_arm)
        res = {k: res[k] for k in ("resolution", "counts", "tokens", "gate", "gate_passes")}
    elif a.cmd == "draw":
        res = run_draw(a.judge_config, a.spot_key, a.spot_sheet, a.out_dir)
    elif a.cmd == "chunk":
        res = run_chunk(judge_cfg(a.judge_config)["chunk_exports"], a.texts, a.sample, a.out_dir)
    elif a.cmd == "map":
        res = run_map(judge_cfg(a.judge_config)["chunk_exports"], a.sample, a.control_dir, cover=a.cover_any,
                      pairs=a.pairs)
    elif a.cmd == "retest-seed":
        res = run_retest_seed(a.judge_config, a.control_dir, a.out_dir)
    else:
        res = run_report(a.judge_config, a.control_config, a.control_dir, a.out, a.cover_config,
                         a.retest_config, a.retest_dir)
        res = {"n_mapped": res["metrics"]["n_mapped"], "flip": res["metrics"]["flip"],
               **({"retest_flip": res["metrics"]["retest"]["flip"]} if "retest" in res["metrics"] else {})}
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
