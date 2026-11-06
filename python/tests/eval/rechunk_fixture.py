"""A synthetic rechunk stage-1 world for the tests: the confirmatory sample, its control and
cover-any registries with their final labels, the stage-A run files the random pairs are placed
by, and a label table written straight from pair and label rows. Made-up ids only; no store, no
network, no model."""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
for _p in (_EVAL, _EVAL / "glossbench", _EVAL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def cid(tag: str) -> str:
    """A chunk-id-looking string (long enough for ``public_problems``' substring check)."""
    return f"chunk-{tag}-0000"


def rid(tag: str) -> str:
    return f"alias-O-{tag}-g0"


def final(pid: str, label: int | None, source: str = "agree") -> dict[str, Any]:
    if label is None:
        source = "unjudged"
    return {"k": f"pair|{pid}", "pair_id": pid, "label": label, "source": source}


@dataclass
class Pair:
    """One confirmatory pair: the record, its stage-A window, its arm group, the corpus arms whose
    stage-A legs retrieved the window, the control (max-overlap) chunk and its label, and the
    cover-any chunks with their labels (the primary among them by its id)."""
    tag: str
    record: str
    group: str
    seen_in: tuple[str, ...]
    control_label: int | None
    coverage: float
    covers: dict[str, int | None] = field(default_factory=dict)
    #: the confirmatory draw stratum: every pair was drawn from the stage-A answers stratum
    stratum: str = "answers"

    @property
    def window(self) -> str:
        return cid(f"w{self.tag}")

    @property
    def control(self) -> str:
        return cid(f"m{self.tag}")

    @property
    def pair_id(self) -> str:
        return aj.pair_id(self.record, self.window)


def default_pairs() -> list[Pair]:
    """Every case the table must tell apart:

    1. base, answers;
    2. base, lost, a non-primary cover chunk answers (other_only);
    3. neighbour, lost, only the re-judged primary answers (primary_only; the registries disagree);
    4. random1 only, lost, nothing answers (none);
    5. random2 only, answers, low coverage;
    6. random in both draws, the control unjudged;
    7. the record of pair 1 again, in neighbour, answers;
    8. base, lost, no cover-any rows; stage A retrieved its window only in the neighbour arm.
    """
    r = rid
    return [
        Pair("1", r("P01"), "base", ("base",), 1, 0.92),
        Pair("2", r("P02"), "base", ("base",), 0, 0.71, {"m2": 0, "o2a": 1, "o2b": 0}),
        Pair("3", r("P03"), "neighbour", ("neighbour",), 0, 0.55, {"m3": 1, "o3a": 0}),
        Pair("4", r("P04"), "random", ("random1",), 0, 0.85, {"m4": 0, "o4a": None}),
        Pair("5", r("P05"), "random", ("random2",), 1, 0.31),
        Pair("6", r("P06"), "random", ("random1", "random2"), None, 0.66),
        Pair("7", r("P01"), "neighbour", ("neighbour",), 1, 0.81),
        Pair("8", r("P08"), "base", ("neighbour",), 0, 0.95),
    ]


class GoldWorld:
    """The gold-map inputs written to ``tmp`` (``paths``) from :func:`default_pairs`."""

    def __init__(self, tmp: Path, pairs: Sequence[Pair] | None = None) -> None:
        self.tmp = tmp
        self.pairs = list(pairs or default_pairs())
        self.sample = [{"pair_id": p.pair_id, "record_id": p.record, "chunk_id": p.window, "arm": p.group,
                        "stratum": p.stratum} for p in self.pairs]
        self.seeds = [{"pair_id": aj.pair_id(p.record, p.control), "record_id": p.record, "chunk_id": p.control,
                       "control_of": p.pair_id, "stage_a_chunk_id": p.window, "coverage": p.coverage}
                      for p in self.pairs]
        self.cover_seeds = [{"pair_id": aj.pair_id(p.record, cid(t)), "record_id": p.record, "chunk_id": cid(t),
                             "control_of": p.pair_id} for p in self.pairs for t in p.covers]
        self.cfinal = {aj.pair_id(p.record, p.control): final(aj.pair_id(p.record, p.control), p.control_label)
                       for p in self.pairs}
        self.vfinal = {aj.pair_id(p.record, cid(t)): final(aj.pair_id(p.record, cid(t)), lab)
                       for p in self.pairs for t, lab in p.covers.items()}
        self.runs = tmp / "stage-a-runs"
        self.paths = {
            "sample": write_jsonl(tmp / "sample.jsonl", self.sample),
            "pairs_seed": write_jsonl(tmp / "pairs-seed.jsonl", self.seeds),
            "pairs_seed_cover": write_jsonl(tmp / "pairs-seed-cover.jsonl", self.cover_seeds),
            "control_final": write_jsonl(tmp / "control-final.jsonl",
                                         [{"k": "batch|1", "n": len(self.cfinal)}, *self.cfinal.values()]),
            "cover_final": write_jsonl(tmp / "cover-final.jsonl",
                                       [{"k": "batch|1", "n": len(self.vfinal)}, *self.vfinal.values()]),
        }
        self.write_stage_a()

    def top10(self, corpus: str, record: str) -> list[str]:
        mine = [p.window for p in self.pairs if p.record == record and corpus in p.seen_in]
        return [*mine, *(cid(f"filler{i}") for i in range(10))][:10]

    def write_stage_a(self) -> None:
        """One file per attachment (``alias_study_run.leg_files``) with every retriever's rows;
        a failed bm25 row carries the window and must be skipped."""
        import alias_study_run as ar

        records = sorted({p.record for p in self.pairs})
        files: dict[Path, list[dict[str, Any]]] = {}
        for c, g, r, rep in asc.all_legs():
            if asc.leg_id(c, g, r, rep) not in ar.RETRIEVAL_A_LEGS:
                continue
            rows_path, _ = ar.leg_files(self.runs, c, g, r, rep)
            for rec in records:
                failed = r == "bm25" and rec == rid("P05")
                files.setdefault(rows_path, []).append({
                    "record_id": rec, "condition": asc.RETRIEVERS[r], "failed": failed,
                    "ranked_chunk_ids": None if failed else self.top10(c, rec)})
        for path, rows in files.items():
            write_jsonl(path, rows)

    def args(self, out: Path) -> list[str]:
        p = self.paths
        return ["gold-map", "--sample", str(p["sample"]), "--pairs-seed", str(p["pairs_seed"]),
                "--pairs-seed-cover", str(p["pairs_seed_cover"]), "--control-final", str(p["control_final"]),
                "--cover-final", str(p["cover_final"]), "--stage-a-runs", str(self.runs), "--out", str(out)]


def write_label_table(path: Path, pairs: Sequence[Mapping[str, Any]], labels: Sequence[Mapping[str, Any]]) -> Path:
    """A label table in ``rechunk_prep``'s format, written straight from rows (the runner and
    report tests need no gold-map run)."""
    import rechunk_prep as rp

    meta = {"kind": "meta", "schema": rp.SCHEMA["table"], "pairs": len(pairs), "labels": len(labels),
            "label_rule": "test", "corpus_arm_rule": "test"}
    return write_jsonl(path, [meta, *pairs, *labels])


def pair_row(record: str, corpus_arm: str, max_overlap: str, *, tag: str, covers: Sequence[str] = (),
             control_label: str = "answers", lost: bool = False, cover_class: str | None = None,
             coverage_band: str = ">=0.8", stage_a_legs: Sequence[str] = (), draw_stratum: str = "answers"
             ) -> dict[str, Any]:
    window = cid(f"w{tag}")
    return {"kind": "pair", "pair_id": aj.pair_id(record, window), "record_id": record, "stage_a_chunk_id": window,
            "arm": asc.arm_group(corpus_arm), "corpus_arm": corpus_arm, "random_both_draws": False,
            "draw_stratum": draw_stratum, "max_overlap_chunk_id": max_overlap, "coverage": 0.9, "coverage_band": coverage_band,
            "cover_any_chunk_ids": list(covers), "control_label": control_label, "lost": lost,
            "cover_class": cover_class, "stage_a_legs": list(stage_a_legs)}


def label_row(record: str, chunk: str, label: str, source: str = "control") -> dict[str, Any]:
    return {"kind": "label", "record_id": record, "chunk_id": chunk, "label": label,
            "registries": {source: label}, "source": source}
