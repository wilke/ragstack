"""A synthetic reach-comparison world for the tests (``reach_report.py``): accepted records with
quotes, a W export (fixed windows) and an S export (another chunking of the same documents), W and S
run directories in ``alias_study_run.py``'s layout, judge ``final.jsonl`` files, the main registry's
stage-A pairs and an S build summary. Made-up ids only; no store, no network, no model."""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
for _p in (_EVAL, _EVAL / "glossbench", _EVAL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402

CORPORA = asc.CORPUS_ARMS
STAMP = "alias-none/1+attach=mention@file/1"
N_PAPERS = 6


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def paper(j: int) -> str:
    return f"PMCF{j:04d}"


def rid(j: int, k: str) -> str:
    return f"alias-O-{paper(j)}-{k}0"


def quote(j: int, k: str) -> str:
    """Eight 8-hex-digit words: unique, no 20-character run shared with any other text."""
    h = hashlib.sha256(f"{j}|{k}".encode()).hexdigest()
    return " ".join(h[i:i + 8] for i in range(0, 64, 8))


def filler(tag: str) -> str:
    h = hashlib.sha256(f"filler|{tag}".encode()).hexdigest()
    return "Filler " + " ".join(h[i:i + 6] for i in range(0, 60, 6)) + "."


def wid(j: int, i: int) -> str:
    return f"wchunk-{paper(j)}-{i}-0000"


def sid(j: int, i: int) -> str:
    return f"schunk-{paper(j)}-{i}-0000"


def label_row(r: str, c: str, label: int | None, *, batch: int = 1, pid: str | None = None) -> dict[str, Any]:
    return {"k": f"pair|{pid or aj.pair_id(r, c)}", "pair_id": pid or aj.pair_id(r, c), "record_id": r,
            "chunk_id": c, "label": label, "source": "unjudged" if label is None else "agree", "batch": batch}


class World:
    """Paper j: records ``g`` (quote in W window 0) and ``c`` (quote in W window 1); W window 2 is
    filler. S chunks: 0 and 1 hold the two halves of the g quote (a split quote), 2 the whole c
    quote, 3 filler, 4 a byte-identical copy of W window 2 (the content-hash rule). Paper 0 also has
    a record ``x`` whose quote is not verbatim in its gold window (excluded from Q, kept in J)."""

    def __init__(self, tmp: Path, n_papers: int = N_PAPERS) -> None:
        self.tmp = tmp
        self.n = n_papers
        self.records: list[dict[str, Any]] = []
        for j in range(n_papers):
            for k, gold in (("g", 0), ("c", 1)):
                self.records.append({"record_id": rid(j, k), "paper": paper(j), "kind": "G" if k == "g" else "C",
                                     "question": f"What does paper number {j} report about item {k} in its results?",
                                     "quote": quote(j, k), "gold_chunks": [wid(j, gold)]})
        self.records.append({"record_id": rid(0, "x"), "paper": paper(0), "kind": "C",
                             "question": "Which unusual quantity did paper zero describe in the discussion?",
                             "quote": "this quote is not verbatim anywhere in the corpus at all, by design",
                             "gold_chunks": [wid(0, 2)]})
        self.by_id = {r["record_id"]: r for r in self.records}
        self.records_path = write_jsonl(tmp / "accepted.jsonl", self.records)
        self.w_rows, self.s_rows = [], []
        for j in range(n_papers):
            p = paper(j)
            texts = [f"Intro of {p}. {quote(j, 'g')} Then more.", f"Methods of {p}. {quote(j, 'c')} End.",
                     filler(f"w{j}")]
            pos = 0
            for i, t in enumerate(texts):
                self.w_rows.append(self.row(wid(j, i), p, t, pos))
                pos += len(t) + 1
            q = quote(j, "g")
            half = len(q) // 2
            cut = q.rfind(" ", 0, half)
            stexts = [f"Intro of {p}. {q[:cut]}", f"{q[cut + 1:]} Then more.", f"Methods of {p}. {quote(j, 'c')} End.",
                      filler(f"s{j}"), filler(f"w{j}")]
            pos = 0
            for i, t in enumerate(stexts):
                self.s_rows.append(self.row(sid(j, i), p, t, pos))
                pos += len(t) + 1
        self.w_export = write_jsonl(tmp / "w-export.jsonl", self.w_rows)
        self.s_export = write_jsonl(tmp / "s-export.jsonl", self.s_rows)
        self.w_runs, self.s_runs = tmp / "w-runs", tmp / "s-runs"
        #: (record, corpus, retriever) -> a top-10 list overriding the default
        self.w_over: dict[tuple[str, str, str], list[str] | None] = {}
        self.s_over: dict[tuple[str, str, str], list[str] | None] = {}

    @staticmethod
    def row(cid: str, p: str, text: str, pos: int) -> dict[str, Any]:
        return {"chunk_id": cid, "doc_id": f"doc-{p}", "content": text, "start_char": pos,
                "end_char": pos + len(text), "metadata": {"source_path": f"{p}#body", "title": f"T {p}"}}

    def j_of(self, r: str) -> int:
        return int(self.by_id[r]["paper"][4:])

    def w_default(self, r: str) -> list[str]:
        j = self.j_of(r)
        k = 0 if r.endswith("g0") else 1
        others = [wid((j + 1) % self.n, 2), wid((j + 2) % self.n, 2)]
        return [wid(j, k), wid(j, 2), *others]

    def s_default(self, r: str) -> list[str]:
        j = self.j_of(r)
        mine = [sid(j, 0), sid(j, 1)] if r.endswith("g0") else [sid(j, 2)]
        return [*mine, sid(j, 3), sid((j + 1) % self.n, 3)]

    def write_runs(self, side: str, *, summary_over: Mapping[str, Mapping[str, Any]] | None = None,
                   failed: Callable[[str, str, str], bool] | None = None) -> Path:
        d = self.w_runs if side == "W" else self.s_runs
        over = self.w_over if side == "W" else self.s_over
        default = self.w_default if side == "W" else self.s_default
        for c in CORPORA:
            rows = []
            for rec in self.records:
                for ret, cond in asc.RETRIEVERS.items():
                    key = (rec["record_id"], c, ret)
                    bad = bool(failed and failed(*key))
                    top = over.get(key, default(rec["record_id"]))
                    rows.append({"record_id": rec["record_id"], "condition": cond, "failed": bad,
                                 "ranked_chunk_ids": None if bad else top})
            write_jsonl(d / c / "none.jsonl", rows)
            s: dict[str, Any] = {"status": "complete", "conditions": {cond: {"gate": None} for cond in asc.RETRIEVERS.values()},
                                 "collection": f"bench-rechunk-{c}" if side == "S" else f"bench-alias-{c}-none"}
            if side == "S":
                live = {"total": 100, "stamps": {STAMP: 100}}
                s["census"] = {"live_before": live, "live_after": live}
                s["study"] = {"mode": "rechunk", "rechunk_scope": "all_records",
                              "collection_build": {"n_chunks": 100, "load_receipt_sha256": f"receipt-{c}"}}
            s.update((summary_over or {}).get(c, {}))
            (d / c / "none.summary.json").write_text(json.dumps(s))
        return d

    def build_summary(self) -> Path:
        cols = {c: {"collection": f"bench-rechunk-{c}", "chunks_loaded": 100, "load_receipt_sha256": f"receipt-{c}",
                    "census": {"total": 100, "stamps": {STAMP: 100}}} for c in CORPORA}
        p = self.tmp / "build-summary.json"
        p.write_text(json.dumps({"collections": cols}))
        return p

    def main_pairs(self, skip: Sequence[str] = ()) -> Path:
        """One stage-A pair per record on its paper's filler window (W window 2), one stage-B pair,
        none for the records in ``skip``."""
        rows = []
        for r in self.records:
            if r["record_id"] in skip:
                continue
            j = self.j_of(r["record_id"])
            side = {"question": r["question"], "term": "item", "record_kind": r["kind"], "need": "found10",
                    "record_id": r["record_id"], "q_paper": paper(j), "arm": "base"}
            rows.append({**side, "pair_id": aj.pair_id(r["record_id"], wid(j, 2)), "chunk_id": wid(j, 2),
                         "c_paper": paper(j), "stage": "A"})
            nxt = (j + 1) % self.n
            rows.append({**side, "pair_id": aj.pair_id(r["record_id"], wid(nxt, 2)), "chunk_id": wid(nxt, 2),
                         "c_paper": paper(nxt), "stage": "B"})
        return write_jsonl(self.tmp / "main-pairs.jsonl", rows)

    def args(self, cmd: str, *extra: str) -> list[str]:
        out = [cmd, "--records", str(self.records_path), "--w-runs", str(self.w_runs), "--s-runs", str(self.s_runs)]
        for c in CORPORA:
            out += ["--w-export", f"{c}={self.w_export}", "--s-export", f"{c}={self.s_export}"]
        return [*out, *extra]
