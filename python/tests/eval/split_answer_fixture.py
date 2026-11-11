"""A synthetic split-answer world for the tests: documents the control chunker cuts into known
chunks (a word-count token counter stands in for the SFR tokenizer), stage-A windows crafted on
them, the confirmatory run's ``chunk`` / ``map`` outputs (made by ``alias_control_chunks`` itself),
the retest export, a label table and a main judge config. Made-up ids and texts only; no store, no
network, no model."""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
for _p in (_EVAL, _EVAL / "glossbench", _EVAL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import rechunk_prep as rp  # noqa: E402
import split_answer_prep as sap  # noqa: E402

SEED = "test|split|negative|draw"


class WordCounter:
    """One token per whitespace-separated word: a deterministic stand-in for the SFR tokenizer."""

    def count(self, text: str) -> int:
        return len(text.split())


def sent(n: int, tag: str) -> str:
    return " ".join([tag.capitalize()] + [f"{tag}w{j}" for j in range(n - 1)]) + "."


def sents(k: int, n: int, tag: str) -> str:
    return " ".join(sent(n, f"{tag}{i}") for i in range(k))


#: four chunks [0,2371) [2371,5383) [5383,7304) [7304,9676); section edges at 2371 and 7304
DOC_A = ("## Intro\n\n" + sents(20, 20, "i") + "\n\n## Methods\n\n" + sents(40, 20, "m") + "\n\n## Results\n\n"
         + sents(20, 20, "r") + "\n")
#: three chunks [0,3000) [3000,6200) [6200,10040); no section edge
DOC_B = sents(80, 20, "u") + "\n"
#: one chunk
DOC_S = sents(5, 20, "s") + "\n"
DOC_S2 = sents(5, 20, "z") + "\n"
#: two chunks [0,1345) [1345,2689); a section edge at 1345
DOC_T = "## One\n\n" + sents(12, 20, "o") + "\n\n## Two\n\n" + sents(12, 20, "t") + "\n"
#: the negative draw's new documents: three chunks like B, and one chunk
DOC_C = sents(80, 20, "k") + "\n"
DOC_E = sents(5, 20, "e") + "\n"

DOCS = {"dA": ("PMC1", DOC_A), "dB": ("PMC2", DOC_B), "dS": ("PMC3", DOC_S), "dT": ("PMC4", DOC_T),
        "dS2": ("PMC5", DOC_S2), "dC": ("PMC6", DOC_C), "dE": ("PMC7", DOC_E)}


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def window(doc: str, s: int, e: int, tag: str) -> dict[str, Any]:
    paper, text = DOCS[doc]
    return {"chunk_id": f"win-{tag}-0000", "doc_id": doc, "content": text[s:e], "start_char": s, "end_char": e,
            "metadata": {"source_path": paper, "title": f"Title of {paper}"}}


@dataclass
class Pos:
    """One confirmatory pair: tag, document, window, the label-table group fields."""
    tag: str
    doc: str
    s: int
    e: int
    lost: bool
    cover_class: str | None
    record: str = ""

    @property
    def rid(self) -> str:
        return self.record or f"alias-O-{self.tag}-g0"


#: the confirmatory pairs (expected M index, group and edge in the comments)
POSITIVE = [
    Pos("a1", "dA", 2000, 3000, True, "none"),           # M=A1, split, crosses the wall at 2371
    Pos("a2", "dA", 0, 1000, True, "other_only"),        # M=A0, recovered, edge before
    Pos("a3", "dA", 5000, 9676, True, "none"),           # M=A3, split, M±1 does not contain: M±2; edge after
    Pos("a4", "dA", 5500, 7000, True, "primary_only"),   # M=A2, primary_only
    Pos("b1", "dB", 2600, 3500, False, None),            # M=B1, kept_changed
    Pos("b2", "dB", 7000, 9000, False, None),            # M=B2, kept_changed, edge after
    Pos("b3", "dB", 3100, 4000, False, None),            # M=B1 again, another record: the export row is shared
    Pos("b4", "dB", 3200, 3300, False, None, record="alias-O-b1-g0"),  # M=B1, b1's record: seed rows merge
    Pos("b5", "dB", 2950, 3100, True, "none"),           # M=B1, split, crosses no wall
    Pos("s1", "dS", 0, len(DOC_S), False, None),         # kept_identical (one-chunk document)
    Pos("s2", "dS2", 0, 300, False, None),               # kept_changed in a one-chunk document: skipped
    Pos("t1", "dT", 1000, 2000, True, "none"),           # M=T1, split, crosses the wall at 1345, edge after
]


def paths_of(tmp: Path) -> dict[str, Path]:
    return {"confirm": tmp / "confirm", "texts": tmp / "texts", "exports": tmp / "exports",
            "label_table": tmp / "labels.jsonl", "main_config": tmp / "judge-config.json",
            "judge_out": tmp / "judge"}


class World:
    """The confirmatory side: run/{sample,chunks-control,docs,pairs-seed}.jsonl from the real
    ``chunk`` and ``map``, retest/chunks-retest.jsonl, the label table and a main config."""

    def __init__(self, tmp: Path, positive: Sequence[Pos] = tuple(POSITIVE),
                 extra_windows: Sequence[dict[str, Any]] = ()) -> None:
        self.tmp = tmp
        self.p = paths_of(tmp)
        self.positive = list(positive)
        self.windows = {x.tag: window(x.doc, x.s, x.e, x.tag) for x in self.positive}
        texts = self.p["texts"]
        texts.mkdir(parents=True, exist_ok=True)
        for _d, (paper, text) in DOCS.items():
            write_jsonl(texts / f"{paper}.jsonl", [{"path": paper, "text": text}])
        # the stage-A exports: the positive windows in base, plus whatever the caller adds
        rows = list(self.windows.values()) + list(extra_windows)
        self.export = {"base": str(write_jsonl(self.p["exports"] / "chunks-base.jsonl", rows))}
        run = self.p["confirm"] / "run"
        sample = [{"pair_id": aj.pair_id(x.rid, self.windows[x.tag]["chunk_id"]), "record_id": x.rid,
                   "chunk_id": self.windows[x.tag]["chunk_id"], "stratum": "answers", "arm": "base",
                   "in_spot_sheet": False} for x in self.positive]
        self.sample = write_jsonl(run / "sample.jsonl", sample)
        acc.run_chunk(self.export, [texts], self.sample, run)
        acc.run_map(self.export, self.sample, run)
        write_jsonl(self.p["confirm"] / "retest" / "chunks-retest.jsonl", list(self.windows.values()))
        self.seeds = {s["stage_a_chunk_id"]: s for s in asc.read_jsonl(run / "pairs-seed.jsonl")}
        self.cdocs = acc.control_docs(run / "chunks-control.jsonl")
        pairs = []
        for x in self.positive:
            w = self.windows[x.tag]["chunk_id"]
            pairs.append({"kind": "pair", "pair_id": aj.pair_id(x.rid, w), "record_id": x.rid, "stage_a_chunk_id": w,
                          "max_overlap_chunk_id": self.seeds[w]["chunk_id"], "lost": x.lost,
                          "cover_class": x.cover_class, "arm": "base", "corpus_arm": "base"})
        meta = {"kind": "meta", "schema": rp.SCHEMA["table"], "pairs": len(pairs), "labels": 0}
        write_jsonl(self.p["label_table"], [meta, *pairs])
        self.p["judge_out"].mkdir(parents=True, exist_ok=True)
        self.p["main_config"].write_text(json.dumps({"out_dir": str(self.p["judge_out"]),
                                                     "chunk_exports": self.export}))

    def m_index(self, tag: str) -> int:
        w = self.windows[tag]
        s = self.seeds[w["chunk_id"]]
        return [r["chunk_id"] for r in self.cdocs[w["doc_id"]]].index(s["chunk_id"])

    def m_of(self, tag: str) -> str:
        return self.seeds[self.windows[tag]["chunk_id"]]["chunk_id"]

    def pinned(self) -> dict[str, str]:
        run = self.p["confirm"] / "run"
        return {"pairs_seed": asc.sha256_file(run / "pairs-seed.jsonl"),
                "chunks_control": asc.sha256_file(run / "chunks-control.jsonl"),
                "chunks_retest": asc.sha256_file(self.p["confirm"] / "retest" / "chunks-retest.jsonl"),
                "label_table": asc.sha256_file(self.p["label_table"]),
                "confirm_sample": asc.sha256_file(self.sample)}


def chunk_rows(doc: str) -> list[dict[str, Any]]:
    """The control chunker's rows for one fixture document (the chunks-control.jsonl shape)."""
    paper, text = DOCS[doc]
    cd = acc.chunk_document(doc, text, acc.control_chunker())
    return [{"chunk_id": c.id, "doc_id": doc, "content": c.content, "start_char": c.start_char,
             "end_char": c.end_char, "metadata": {"source_path": paper, "title": f"Title of {paper}"}}
            for c in cd.chunks]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def seed_repo(tmp: Path, seed: str = SEED, commit: bool = True) -> tuple[Path, Path]:
    """A git repository at ``tmp/repo`` with the seed file at ``split_answer_prep.SEED_RELPATH``,
    committed (or only written) on an otherwise clean tree."""
    repo = tmp / "repo"
    if (repo / ".git").exists():
        return repo, repo / sap.SEED_RELPATH
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "README").write_text("x\n")
    f = repo / sap.SEED_RELPATH
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"seed": seed}) + "\n")
    git(repo, "add", "README", *([sap.SEED_RELPATH] if commit else []))
    git(repo, "commit", "-q", "-m", "init")
    return repo, f


def negative_dir(tmp: Path, schema: str, picks: Sequence[tuple[str, str, int]]) -> Path:
    """A hand-made draw-negative directory: ``picks`` = (record, doc, M index) on the draw's own
    chunk run (documents dB and dC), all ``same:base``, with a summary as ``draw-negative`` writes
    it: the files' sha256s, the seed of :func:`seed_repo` (``tmp/repo``, made if missing), the
    stage-A commit and the pinned inputs as ``split_answer_prep`` holds them now, and quotas =
    drawn = the picks (the caller sets ``NEGATIVE_QUOTAS`` to ``{"same": len(picks), "other": 0}``)."""
    _repo, seed_file = seed_repo(tmp)
    d = tmp / "negative"
    rows = chunk_rows("dB") + chunk_rows("dC") + chunk_rows("dE")
    cp = write_jsonl(d / "chunks" / "chunks-control.jsonl", rows)
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(r)
    samp = []
    for i, (rec, doc, k) in enumerate(picks):
        m = by_doc[doc][k]["chunk_id"]
        w = f"negwin-{i}-0000"
        samp.append({"pair_id": aj.pair_id(rec, w), "record_id": rec, "stage_a_chunk_id": w,
                     "max_overlap_chunk_id": m, "doc_id": doc, "paper": "same", "arm": "base"})
    sp = write_jsonl(d / "sample-negative.jsonl", samp)
    drawn = Counter(sap.cell_key(r["paper"], r["arm"]) for r in samp)
    quotas = {sap.cell_key(p, a): drawn[sap.cell_key(p, a)] for p, a in sap.cells()}
    (d / "negative-draw.json").write_text(json.dumps({
        "schema": schema, "seed_file_sha256": asc.sha256_file(seed_file),
        "stage_a_commit_sha256": sap.STAGE_A_COMMIT_SHA256,
        "inputs_sha256": {k: sap.PINNED_INPUTS[k] for k in sap.DRAW_PINNED},
        "quotas": quotas, "drawn": dict(quotas), "n_drawn": len(samp),
        "outputs_sha256": {"sample_negative": asc.sha256_file(sp), "chunks_control": asc.sha256_file(cp)}}))
    return d


def rewrite_summary(d: Path, **changes: Any) -> None:
    """Edit a negative directory's ``negative-draw.json`` (a key set to ``None`` is removed)."""
    p = d / "negative-draw.json"
    summ = json.loads(p.read_text())
    for k, v in changes.items():
        if v is None:
            summ.pop(k, None)
        else:
            summ[k] = v
    p.write_text(json.dumps(summ))
