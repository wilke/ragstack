"""A synthetic alias-study world for the tests: records, chunk exports, a glossary and the
runner's leg files, written to a temporary directory. No store, no network, no model."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
for _p in (_EVAL, _EVAL / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402

N_PAPERS = 12
CHUNKS_PER_PAPER = 4


def chunk_id(paper: str, i: int) -> str:
    return f"{paper}-c{i}"


def make_records(n_papers: int = N_PAPERS, set_: str = "O") -> list[dict[str, Any]]:
    recs = []
    for j in range(n_papers):
        p = f"P{j:02d}"
        for k, (alias, primary) in enumerate(((f"AB{j}", f"alpha beta {j}"), (f"CD{j}", f"charlie delta {j}"))):
            recs.append({"record_id": f"alias-{set_}-{p}-g{k}", "set": set_, "paper": p, "kind": "G", "stratum": "G",
                         "question": f"What does the study report about {primary} in the cohort number {j}?",
                         "primary": primary, "alias": alias, "alias_kind": "abbreviation" if k == 0 else "symbol",
                         "gold_chunks": [chunk_id(p, k)], "verifier": "claude" if j % 5 else "codex"})
        recs.append({"record_id": f"alias-{set_}-{p}-c0", "set": set_, "paper": p, "kind": "C", "stratum": "C",
                     "question": f"How was the gamma measure {j} defined in the methods of this paper?",
                     "primary": f"gamma measure {j}", "alias": f"GM{j}", "alias_kind": "abbreviation",
                     "gold_chunks": [chunk_id(p, 2)], "verifier": "claude"})
    # an unsatisfied paper: one G and one C (the descriptive leg)
    recs.append({"record_id": f"alias-{set_}-Q00-g0", "set": set_, "paper": "Q00", "kind": "G", "stratum": "G",
                 "question": "What does the study report about the epsilon zeta value?", "primary": "epsilon zeta",
                 "alias": "EZ", "alias_kind": "abbreviation", "gold_chunks": [chunk_id("Q00", 0)], "verifier": "claude"})
    recs.append({"record_id": f"alias-{set_}-Q00-c0", "set": set_, "paper": "Q00", "kind": "C", "stratum": "C",
                 "question": "How was the theta measured in this work overall?", "primary": "theta",
                 "alias": "TH", "alias_kind": "abbreviation", "gold_chunks": [chunk_id("Q00", 1)], "verifier": "claude"})
    return recs


def chunk_rows(papers: Sequence[str], prefix: str = "") -> list[dict[str, Any]]:
    rows = []
    for p in papers:
        for i in range(CHUNKS_PER_PAPER):
            text = f"{prefix}Paper {p} chunk {i}: the AB and CD values and the gamma measure were reported here."
            rows.append({"chunk_id": chunk_id(p, i), "doc_id": f"d-{p}", "content": text, "start_char": 0,
                         "end_char": len(text), "metadata": {"source_path": p, "title": f"Title of {p}"}})
    return rows


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


class World:
    """Paths of one synthetic world."""

    def __init__(self, tmp: Path, n_papers: int = N_PAPERS, set_: str = "O") -> None:
        self.tmp = tmp
        self.records = make_records(n_papers, set_)
        self.papers = sorted({r["paper"] for r in self.records})
        self.records_path = write_jsonl(tmp / "accepted.jsonl", self.records)
        self.base_export = write_jsonl(tmp / "chunks-base.jsonl", chunk_rows(self.papers))
        self.added = {"neighbour": ["N00", "N01"], "random1": ["R00", "R01"], "random2": ["S00"]}
        self.exports = {"base": self.base_export}
        for arm, papers in self.added.items():
            self.exports[arm] = write_jsonl(tmp / f"chunks-{arm}.jsonl", chunk_rows(papers, prefix=f"[{arm}] "))
        gl = []
        for r in self.records:
            gl.append({"alias": r["alias"], "primary": r["primary"], "kind": r["alias_kind"], "paper": r["paper"],
                       "doc_id": f"d-{r['paper']}"})
        self.glossary = write_jsonl(tmp / "glossary.jsonl", gl)
        self.runs = tmp / "runs"
        self.by_id = {r["record_id"]: r for r in self.records}

    def default_top10(self, leg: str, rec: Mapping[str, Any]) -> list[str]:
        """Gold at rank 1 plus same-paper and other-paper chunks."""
        p = rec["paper"]
        others = [chunk_id(p, i) for i in range(CHUNKS_PER_PAPER) if chunk_id(p, i) != rec["gold_chunks"][0]]
        corp = leg.split("|")[0]
        extra = [chunk_id(x, 0) for x in self.added.get(corp, [])]
        return [rec["gold_chunks"][0], *others, *extra, chunk_id("P00", 3), chunk_id("P01", 3)][:10]

    def write_runs(self, top10: Callable[[str, Mapping[str, Any]], list[str]] | None = None,
                   failed: Callable[[str, Mapping[str, Any]], bool] | None = None,
                   gates: Mapping[str, str] | None = None, status: Mapping[str, str] | None = None) -> None:
        """found10-shaped summaries and rows for all 28 legs."""
        top10 = top10 or self.default_top10
        gates = gates or {}
        status = status or {}
        files: dict[tuple[str, str], dict[str, Any]] = {}
        for c, g, r, rep in asc.all_legs():
            label = g if rep == 1 else f"{g}#2"
            key = (c, label)
            f = files.setdefault(key, {"rows": [], "conds": {}})
            cond = asc.RETRIEVERS[r]
            leg = asc.leg_id(c, g, r, rep)
            f["conds"][cond] = {"gate": gates.get(leg)}
            for rec in self.records:
                bad = bool(failed and failed(leg, rec))
                f["rows"].append({"record_id": rec["record_id"], "condition": cond, "failed": bad,
                                  "ranked_chunk_ids": None if bad else top10(leg, rec)})
        for (c, label), f in files.items():
            d = self.runs / c
            d.mkdir(parents=True, exist_ok=True)
            write_jsonl(d / f"{label}.jsonl", f["rows"])
            (d / f"{label}.summary.json").write_text(json.dumps({
                "status": status.get(f"{c}/{label}", "complete"), "conditions": f["conds"],
                "provenance": {"commit": "a" * 40, "dirty": False, "untracked": 0}, "citable": True}))
