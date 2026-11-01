#!/usr/bin/env python
"""Alias study (OA-1000): the judge pipeline (DRAFT prereg §6). Built on the calibration
harness (``glossbench_alias_judge.py``, #894-#899): its prompt (``alias-judge/2``; the drift
reference on ``alias-judge/1``), its Qwen and gateway backends with the served-model check,
the size notice, PAUSE / INFLIGHT, the budget stop and the call caps.

    alias_study_judge.py --config CFG build-pairs
    alias_study_judge.py --config CFG status
    alias_study_judge.py --config CFG preflight --batch N --backend {C,E,argon,argon_r2,opus}
    alias_study_judge.py --config CFG label     --batch N --backend B --notice-acknowledged [--resume]
    alias_study_judge.py --config CFG finalize  --batch N

**Pairs (§6.2).** ``build-pairs`` reads every leg of the runner (all 28 must be ``complete``),
takes each record's top 10 (``ranked_chunk_ids[:10]``), drops the gold chunk (a hit by
definition) and deduplicates the (record, chunk) pairs across corpus arms, glossary arms,
retrievers and the repeats: one final label serves every cell. The registry
(``pairs.jsonl``) is frozen once written. A pair's **arm** (drift and spot-check strata only)
is the arm its passage's paper came from: the export the chunk is in (``chunk_exports``:
``base``; ``neighbour``; ``random1`` / ``random2`` -> ``random``). The registry row carries
the arm, but the prompt does not: :func:`glossbench_alias_judge.judge_prompt` is a function of
the question, its term, the passage, the passage paper's title and that paper's definitions
only, so no label can depend on which cell produced the pair. Pairs are labelled in the seeded
order ``sha256(261101|alias|judge|order|<pair>)`` and cut into batches of ``batch_size``.

**One batch (§6.3).** ``C`` (Qwen3.6-35B-A3B, n 5, thinking, T 0.7, 8192) and ``E``
(Qwen3.8-27B, n 1, the same) label every pair of the batch, local, at most 4 in flight, the
served model checked on every reply (a mismatch stops the run and stores nothing). When C's
majority of its valid samples and E's sample agree, that label is final; otherwise (they
disagree, C has no majority, E is invalid) the pair goes to ``argon`` (Gemini 4 Argon on the
gateway, ``alias-judge/2``, 4096, default temperature, ``fallback_model ""``); an empty reply is
re-issued once by the harness, and an unparseable or truncated one once more as ``argon_r2``;
still no label -> **unjudged** (not answering in the primary analysis). ``opus`` (Claude Opus
5.5, ``alias-judge/1``, 1 rep, 2048) re-labels the batch's **drift sample**: 5 % of the batch,
``round(0.05 n)``, allocated equally across the three arms (the remainder to base, then
neighbour), each stratum capped at its size and not re-allocated, the first pairs of each
stratum in the order ``sha256(261101|alias|drift|<batch>|<pair>)``. An Opus reply with no
verdict (empty after the re-issue, refused, unparseable) is **filtered**: not a drift failure,
excluded from the agreement and counted per arm.

**Finalize and the drift alarm.** ``finalize`` needs C and E on every pair of the batch, the
tiebreaker on every escalated pair and Opus on every sampled pair; it writes the batch's final
labels (``final.jsonl``) and checks the cumulative agreement between the final label (unjudged
= no) and Opus on the Opus-answered pairs: below **0.97 overall** or **0.95 in any arm**, each
scope with at least **100** Opus-answered pairs, writes ``PAUSE`` and ``drift-alarm.json`` and
exits 1. Nothing is labelled while ``PAUSE`` exists; the owner decides the remedy (a deviation).
A batch can be labelled only after every earlier batch is finalized.

**Gateway calls** carry a size notice per batch and backend (``preflight`` writes it; ``label``
refuses without ``--notice-acknowledged`` and a preflight of the same plan, which includes the
batch and the sha256 of its selection); the harness's budget stop (exit 76, PAUSE) and call cap
(``limits``) apply; and the study's caps (§14 O7, ``caps``): the cumulative calls of a backend
(its calls log) plus the planned selection may not exceed ``argon_share`` / ``opus_share`` of the
registry's pairs, nor ``argon_max_calls`` / ``opus_max_calls`` when set.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED, EXIT_BUDGET = jd.EXIT_OK, jd.EXIT_HARD_STOP, jd.EXIT_PAUSED, jd.EXIT_BUDGET
PAIRS_SCHEMA = "alias-study-pairs/1"
FINAL_SCHEMA = "alias-study-final/1"
BACKENDS = ("C", "E", "argon", "argon_r2", "opus")

#: prereg §6.3, fixed: what the config may not change (the endpoint, the cap and the timeout may)
FIXED: dict[str, dict[str, Any]] = {
    "C": {"model": "Qwen3.6-35B-A3B", "n": 5, "temperature": 0.7, "max_tokens": 8192, "enable_thinking": True,
          "judge_version": "alias-judge/2", "require_served_model": True, "variant": "C"},
    "E": {"model": "Qwen3.8-27B", "n": 1, "temperature": 0.7, "max_tokens": 8192, "enable_thinking": True,
          "judge_version": "alias-judge/2", "require_served_model": True, "variant": "E"},
    "argon": {"model": "Gemini 4 Argon", "judge_version": "alias-judge/2", "max_tokens": 4096, "temperature": None,
              "tag": "argon"},
    "argon_r2": {"model": "Gemini 4 Argon", "judge_version": "alias-judge/2", "max_tokens": 4096,
                 "temperature": None, "tag": "argon_r2"},
    "opus": {"model": "Claude Opus 5.5", "judge_version": "alias-judge/1", "max_tokens": 2048, "temperature": None,
             "tag": "opus"},
}
DEFAULTS: dict[str, Any] = {
    "batch_size": 2000,
    "drift": {"share": 0.05, "min_pairs": 100, "overall": 0.97, "per_arm": 0.95},
    "caps": {"argon_share": 0.04, "opus_share": 0.06, "argon_max_calls": None, "opus_max_calls": None},
    "unjudged_flag_share": 0.01,
}
GW_ROLE = {"argon": "tiebreak", "argon_r2": "tiebreak", "opus": "drift_ref"}


# ------------------------------------------------------------------ config

def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def validate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    cfg = {**DEFAULTS, **raw}
    for k in ("drift", "caps"):
        cfg[k] = {**DEFAULTS[k], **(raw.get(k) or {})}
    for k in ("out_dir", "runs_dir", "records", "chunk_exports", "glossary", "judge_c", "judge_e", "tiebreak",
              "drift_ref"):
        if not cfg.get(k):
            raise SystemExit(f"judge config has no {k!r}")
    if "base" not in cfg["chunk_exports"] or not set(cfg["chunk_exports"]) <= set(asc.CORPUS_ARMS):
        raise SystemExit(f"chunk_exports: keys from {list(asc.CORPUS_ARMS)}, base required")
    out = Path(cfg["out_dir"]).resolve()
    repo = _HERE.parents[2].resolve()
    if out == repo or repo in out.parents:
        raise SystemExit("out_dir is inside the repository; private outputs must live outside it")
    for role, block in (("C", "judge_c"), ("E", "judge_e"), ("argon", "tiebreak"), ("opus", "drift_ref")):
        b = cfg[block]
        for k, v in FIXED[role].items():
            if k in ("tag", "variant"):
                continue
            if b.get(k, v if k in ("temperature",) else object()) != v:
                raise SystemExit(f"{block}.{k} must be {v!r} (prereg §6.3), got {b.get(k)!r}")
        if not b.get("base_url"):
            raise SystemExit(f"{block}.base_url is required")
    if int(cfg["batch_size"]) < 1:
        raise SystemExit("batch_size: at least 1")
    return cfg


def harness_cfg(cfg: Mapping[str, Any], backend: str) -> dict[str, Any]:
    """The calibration harness's config for one backend (``glossbench_alias_judge.merged``)."""
    base: dict[str, Any] = {"out_dir": cfg["out_dir"], "chunks": [], "glossary": cfg["glossary"],
                            "paper_key": "path_root", "limits": dict(cfg.get("limits") or {})}
    if backend in ("C", "E"):
        blk = dict(cfg["judge_c" if backend == "C" else "judge_e"])
        q = {k: blk[k] for k in ("base_url", "model", "n", "temperature", "max_tokens", "enable_thinking",
                                 "judge_version", "require_served_model") if k in blk}
        q.update({"variant": backend, "inflight": int(blk.get("inflight", 4)),
                  "timeout_s": float(blk.get("timeout_s", 3600))})
        base["qwen"] = q
    else:
        blk = dict(cfg[GW_ROLE[backend]])
        gw = {k: blk[k] for k in ("base_url", "model", "served_models", "inflight", "empty_retry_s") if k in blk}
        gw.update({"tag": FIXED[backend]["tag"], "judge_version": FIXED[backend]["judge_version"],
                   "max_tokens": FIXED[backend]["max_tokens"], "temperature": None})
        base["judge_gw"] = gw
    merged = jd.merged(base)
    return {**base, **merged}


# ------------------------------------------------------------------ pairs

def leg_rows(runs_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    """``(leg id, row)`` for every row of every complete leg; refuses when any of the 28 legs is
    not complete (§6.2: pairs are built after retrieval)."""
    import alias_study_run as ar
    import found10 as f10

    st = ar.run_status(runs_dir)
    bad = {k: v for k, v in st.items() if v != "complete"}
    if bad:
        raise SystemExit(f"{len(bad)} leg(s) are not complete: {sorted(bad)[:6]}…; pairs are built after retrieval")
    out: list[tuple[str, dict[str, Any]]] = []
    for c, g, r, rep in asc.all_legs():
        label = f10.arm_label(g, rep if rep == 2 else None)
        cond = asc.RETRIEVERS[r]
        for row in f10.read_jsonl_repair_tail(runs_dir / c / f"{label}.jsonl"):
            if row["condition"] == cond:
                out.append((asc.leg_id(c, g, r, rep), row))
    return out


def chunk_arms(cfg: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """All chunks of the exports and each chunk's arm group (§6.2)."""
    chunks: dict[str, dict[str, Any]] = {}
    arm_of: dict[str, str] = {}
    for arm in asc.CORPUS_ARMS:
        path = cfg["chunk_exports"].get(arm)
        if not path:
            continue
        group = asc.arm_group(arm)
        for cid, row in asc.read_chunk_exports([path]).items():
            prev = arm_of.get(cid)
            if prev is not None and prev != group and prev != "base":
                raise SystemExit(f"chunk {cid[:8]}… is in the {prev} and the {group} exports")
            if prev is None:
                arm_of[cid] = group
            if cid in chunks and chunks[cid]["content"] != row["content"]:
                raise SystemExit(f"chunk {cid[:8]}… has other content in the {arm} export")
            chunks.setdefault(cid, row)
    return chunks, arm_of


def pair_id(record_id: str, chunk_id: str) -> str:
    return asc.sha_text(f"{record_id}|{chunk_id}")[:16]


def build_pairs(records: Sequence[Mapping[str, Any]], rows: Sequence[tuple[str, Mapping[str, Any]]],
                chunks: Mapping[str, Mapping[str, Any]], arm_of: Mapping[str, str]
                ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The deduplicated (record, chunk) registry and its counts."""
    by_id = {r["record_id"]: r for r in records}
    pairs: dict[str, dict[str, Any]] = {}
    instances = 0
    per_leg: Counter[str] = Counter()
    for leg, row in rows:
        if row.get("failed"):
            continue
        rec = by_id.get(row["record_id"])
        if rec is None:
            raise SystemExit("a run row names a record that is not in the record file")
        gold = rec["gold_chunks"][0]
        for cid in (row.get("ranked_chunk_ids") or [])[:asc.FOUND_K]:
            if cid == gold:
                continue
            instances += 1
            per_leg[leg] += 1
            pid = pair_id(rec["record_id"], cid)
            if pid in pairs:
                continue
            ch = chunks.get(cid)
            if ch is None:
                raise SystemExit(f"a retrieved chunk ({cid[:8]}…) is in no chunk export")
            pairs[pid] = {"pair_id": pid, "record_id": rec["record_id"], "chunk_id": cid,
                          "content_sha256": asc.sha_text(ch["content"]), "question": rec["question"],
                          "term": rec["primary"], "q_paper": rec["paper"], "c_paper": ch["paper"],
                          "record_kind": rec["kind"], "arm": arm_of[cid],
                          "order": asc.seeded_key(asc.SEEDS["judge_order"], pid)}
    out = sorted(pairs.values(), key=lambda p: p["order"])
    counts = {"pairs": len(out), "instances_before_dedup": instances, "legs": len(per_leg),
              "per_arm": dict(Counter(p["arm"] for p in out)),
              "same_paper": sum(p["c_paper"] == p["q_paper"] for p in out)}
    return out, counts


def batches(pairs: Sequence[Mapping[str, Any]], size: int) -> list[list[str]]:
    ids = [p["pair_id"] for p in pairs]
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def drift_sample(batch_no: int, pairs: Sequence[Mapping[str, Any]], share: float) -> list[str]:
    """§6.3: ``round(share x n)`` pairs, equally across the three arms (remainder to base, then
    neighbour), each stratum capped at its size, seeded per batch."""
    n_total = int(share * len(pairs) + 0.5)
    per = dict.fromkeys(asc.ARM_GROUPS, n_total // 3)
    for a in asc.ARM_GROUPS[: n_total % 3]:
        per[a] += 1
    seed = asc.SEEDS["drift"]
    out: list[str] = []
    for a in asc.ARM_GROUPS:
        stratum = sorted((p for p in pairs if p["arm"] == a),
                         key=lambda p: asc.seeded_key(seed, str(batch_no), p["pair_id"]))
        out += [p["pair_id"] for p in stratum[: per[a]]]
    return out


# ------------------------------------------------------------------ labels -> final

def final_label(c_row: Mapping[str, Any] | None, e_row: Mapping[str, Any] | None,
                tb: Mapping[str, Any] | None, tb2: Mapping[str, Any] | None) -> tuple[int | None, str]:
    """The composed rule (§6.3): C + E agreement, else the tiebreaker, else unjudged."""
    c = jd.majority((c_row or {}).get("verdicts") or [])
    e = jd.majority(((e_row or {}).get("verdicts") or [])[:1])
    if c is not None and c == e:
        return c, "agree"
    for row, src in ((tb, "tiebreak"), (tb2, "tiebreak_r2")):
        v = (row or {}).get("verdict")
        if v is not None:
            return int(bool(v)), src
    return None, "unjudged"


def escalated(c_row: Mapping[str, Any] | None, e_row: Mapping[str, Any] | None) -> bool:
    return final_label(c_row, e_row, None, None)[1] != "agree"


def needs_reissue(tb: Mapping[str, Any] | None) -> bool:
    """An Argon reply that came back but carries no verdict (unparseable, truncated). An empty
    reply was already re-issued once by the harness."""
    return tb is not None and tb.get("status") not in jd.EMPTY and tb.get("verdict") is None


def drift_stats(final: Mapping[str, Mapping[str, Any]], opus: Mapping[str, Mapping[str, Any]],
                sampled: Sequence[str], arm_of_pair: Mapping[str, str], dcfg: Mapping[str, Any]) -> dict[str, Any]:
    """Cumulative agreement and the alarm (§6.3), per arm false yes (§6.3, D2)."""
    scopes: dict[str, dict[str, int]] = {s: Counter() for s in ("overall", *asc.ARM_GROUPS)}
    for pid in sampled:
        arm = arm_of_pair[pid]
        o = (opus.get(pid) or {}).get("verdict")
        fl = (final.get(pid) or {}).get("label")
        for s in ("overall", arm):
            sc = scopes[s]
            sc["sampled"] += 1
            if o is None:
                sc["filtered"] += 1
                continue
            o = int(bool(o))
            f = int(fl or 0)
            sc["answered"] += 1
            sc["agree"] += int(f == o)
            if o == 0:
                sc["opus_no"] += 1
                sc["final_yes_on_opus_no"] += int(f == 1)
    out: dict[str, Any] = {"scopes": {}, "alarm": False, "alarm_reasons": []}
    for s, sc in scopes.items():
        thr = float(dcfg["overall"] if s == "overall" else dcfg["per_arm"])
        n = sc["answered"]
        agr = sc["agree"] / n if n else None
        fires = n >= int(dcfg["min_pairs"]) and agr is not None and agr < thr
        out["scopes"][s] = {**{k: int(v) for k, v in sc.items()}, "agreement": agr, "threshold": thr,
                            "evaluable": n >= int(dcfg["min_pairs"]), "fires": fires,
                            "filtered_share": (sc["filtered"] / sc["sampled"]) if sc["sampled"] else None,
                            "false_yes": {"k": int(sc["final_yes_on_opus_no"]), "n": int(sc["opus_no"]),
                                          "rate": (sc["final_yes_on_opus_no"] / sc["opus_no"]) if sc["opus_no"]
                                          else None, "wilson95": jd.wilson(sc["final_yes_on_opus_no"],
                                                                           sc["opus_no"])}}
        if fires:
            out["alarm"] = True
            out["alarm_reasons"].append(f"{s}: agreement {agr:.4f} < {thr} on {n} Opus-answered pairs")
    return out


# ------------------------------------------------------------------ the pipeline object

class StudyWorld(jd.World):
    """The harness's World over the study's registry: ``pairs()`` is the current selection."""

    def __init__(self, cfg: Mapping[str, Any], chunks: Mapping[str, Mapping[str, Any]],
                 glossary: Mapping[str, list[dict[str, Any]]], selection: Sequence[Mapping[str, Any]]) -> None:
        self.cfg = cfg
        self.out = Path(cfg["out_dir"])
        self.calib_dir = self.out
        self.chunks = {cid: SimpleNamespace(content=c["content"]) for cid, c in chunks.items()}  # type: ignore[misc]
        self.titles = {}
        for c in chunks.values():
            if c.get("title"):
                self.titles.setdefault(c["paper"], c["title"])
        self.glossary = dict(glossary)
        self._selection = list(selection)

    def pairs(self) -> list[dict[str, Any]]:
        for p in self._selection:
            c = self.chunks.get(p["chunk_id"])
            if c is None or asc.sha_text(c.content) != p["content_sha256"]:
                raise jd.p4.HardStop(f"pair {p['pair_id']}: chunk text differs from the registry")
        return [dict(p) for p in self._selection]


class StudyLabeler(jd.Labeler):
    """The harness's Labeler with the batch and the selection in the plan (so a size notice for
    one batch never admits another)."""

    def __init__(self, *a: Any, batch: int, role: str, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.batch, self.role = batch, role

    def plan(self, backend: str, reps: int) -> dict[str, Any]:
        p = super().plan(backend, reps)
        sel = sorted(x["pair_id"] for x in self.w.pairs())
        p.update({"study_batch": self.batch, "study_role": self.role,
                  "selection_sha256": asc.sha_text("".join(f"{x}\n" for x in sel))})
        return p


class Pipeline:
    def __init__(self, cfg: dict[str, Any], *, http_factory: Callable[[], Any] | None = None,
                 provenance: Mapping[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.out = Path(cfg["out_dir"])
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.http_factory = http_factory
        self.prov = dict(provenance or {})
        self._chunks: dict[str, dict[str, Any]] | None = None
        self._glossary: dict[str, list[dict[str, Any]]] | None = None

    # -- registry
    @property
    def pairs_path(self) -> Path:
        return self.out / "pairs.jsonl"

    def registry(self) -> list[dict[str, Any]]:
        if not self.pairs_path.is_file():
            raise SystemExit("pairs.jsonl missing: run build-pairs first")
        man = json.loads((self.out / "pairs-manifest.json").read_text())
        if asc.sha256_file(self.pairs_path) != man["pairs_sha256"]:
            raise jd.p4.HardStop("pairs.jsonl differs from its manifest")
        return asc.read_jsonl(self.pairs_path)

    def batch_list(self) -> list[list[str]]:
        return json.loads((self.out / "batches.json").read_text())["batches"]

    def chunks(self) -> dict[str, dict[str, Any]]:
        if self._chunks is None:
            self._chunks, _ = chunk_arms(self.cfg)
        return self._chunks

    def glossary(self) -> dict[str, list[dict[str, Any]]]:
        if self._glossary is None:
            self._glossary = jd.read_glossary(Path(self.cfg["glossary"]))
        return self._glossary

    def build(self) -> dict[str, Any]:
        if self.pairs_path.exists():
            raise SystemExit("pairs.jsonl exists; the registry is frozen once built")
        recs = asc.load_records(self.cfg["records"])
        rows = leg_rows(Path(self.cfg["runs_dir"]))
        chunks, arm_of = chunk_arms(self.cfg)
        self._chunks = chunks
        pairs, counts = build_pairs(recs, rows, chunks, arm_of)
        with self.pairs_path.open("w", encoding="utf-8") as fh:
            for p in pairs:
                fh.write(json.dumps(p, sort_keys=True) + "\n")
        bl = batches(pairs, int(self.cfg["batch_size"]))
        jd.g.write_json_atomic(self.out / "batches.json", {"batch_size": int(self.cfg["batch_size"]), "batches": bl,
                                                           "seed": asc.SEEDS["judge_order"]})
        man = {"schema": PAIRS_SCHEMA, "counts": counts, "batches": len(bl),
               "pairs_sha256": asc.sha256_file(self.pairs_path),
               "inputs_sha256": {"records": asc.sha256_file(self.cfg["records"]),
                                 **{f"chunks_{a}": asc.sha256_file(p) for a, p in self.cfg["chunk_exports"].items()}},
               "provenance": self.prov, "t": jd.g.now_iso()}
        jd.g.write_json_atomic(self.out / "pairs-manifest.json", man)
        return man

    # -- labels on disk
    def labels(self, backend: str) -> dict[str, dict[str, Any]]:
        name = {"C": "labels-qwen-C.jsonl", "E": "labels-qwen-E.jsonl", "argon": "labels-gw-argon.jsonl",
                "argon_r2": "labels-gw-argon_r2.jsonl", "opus": "labels-gw-opus.jsonl"}[backend]
        rows = jd.p4.read_jsonl_repair_tail(self.out / name)
        return {r["pair_id"]: r for r in rows}

    def finalized(self) -> set[int]:
        return {int(r["batch"]) for r in jd.p4.read_jsonl_repair_tail(self.out / "final.jsonl")
                if r.get("k", "").startswith("batch|")}

    # -- selection per backend
    def selection(self, batch_no: int, backend: str) -> list[dict[str, Any]]:
        bl = self.batch_list()
        if not 1 <= batch_no <= len(bl):
            raise SystemExit(f"--batch {batch_no}: 1..{len(bl)}")
        ids = set(bl[batch_no - 1])
        reg = [p for p in self.registry() if p["pair_id"] in ids]
        if backend in ("C", "E"):
            return reg
        if backend == "opus":
            want = set(drift_sample(batch_no, reg, float(self.cfg["drift"]["share"])))
            return [p for p in reg if p["pair_id"] in want]
        c, e = self.labels("C"), self.labels("E")
        missing = [p["pair_id"] for p in reg if p["pair_id"] not in c or p["pair_id"] not in e]
        if missing:
            raise SystemExit(f"batch {batch_no}: {len(missing)} pair(s) lack a C or E label; the tiebreaker "
                             "set is known only after both")
        esc = [p for p in reg if escalated(c[p["pair_id"]], e[p["pair_id"]])]
        if backend == "argon":
            return esc
        tb = self.labels("argon")
        lacking = [p["pair_id"] for p in esc if p["pair_id"] not in tb]
        if lacking:
            raise SystemExit(f"batch {batch_no}: {len(lacking)} escalated pair(s) lack the first Argon label")
        return [p for p in esc if needs_reissue(tb[p["pair_id"]])]

    def labeler(self, batch_no: int, backend: str) -> StudyLabeler:
        hc = harness_cfg(self.cfg, backend)
        world = StudyWorld(hc, self.chunks(), self.glossary(), self.selection(batch_no, backend))
        return StudyLabeler(hc, world, http_factory=self.http_factory, provenance=self.prov, batch=batch_no,
                            role=backend)

    def harness_backend(self, backend: str) -> str:
        return "qwen" if backend in ("C", "E") else "gateway"

    def check_order(self, batch_no: int) -> None:
        done = self.finalized()
        earlier = [b for b in range(1, batch_no) if b not in done]
        if earlier:
            raise SystemExit(f"batch {batch_no}: batch(es) {earlier} are not finalized")
        if batch_no in done:
            raise SystemExit(f"batch {batch_no} is finalized")

    def calls_so_far(self, backend: str) -> int:
        names = ["calls-gw-argon.jsonl", "calls-gw-argon_r2.jsonl"] if backend.startswith("argon") \
            else ["calls-gw-opus.jsonl"]
        n = 0
        for nm in names:
            p = self.out / nm
            if p.is_file():
                n += sum(1 for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
        return n

    def check_caps(self, backend: str, planned: int) -> None:
        """§14 O7: the cumulative calls plus the planned ones within the share and the ceiling."""
        if backend in ("C", "E"):
            return
        kind = "argon" if backend.startswith("argon") else "opus"
        total = len(self.registry())
        share = float(self.cfg["caps"][f"{kind}_share"])
        ceiling = self.cfg["caps"].get(f"{kind}_max_calls")
        used = self.calls_so_far(backend)
        lim = math.floor(share * total)
        if ceiling is not None:
            lim = min(lim, int(ceiling))
        if used + planned > lim:
            raise SystemExit(f"{kind} cap (§14 O7): {used} calls made + {planned} planned > {lim} "
                             f"({share:.0%} of {total} pairs{'' if ceiling is None else f', ceiling {ceiling}'}); "
                             "the owner raises the cap or the run stops here")

    def preflight(self, batch_no: int, backend: str) -> dict[str, Any]:
        self.check_order(batch_no)
        lab = self.labeler(batch_no, backend)
        self.check_caps(backend, len(lab.w.pairs()))
        return lab.preflight(self.harness_backend(backend), 1)

    def label(self, batch_no: int, backend: str, acknowledged: bool, resume: bool) -> int:
        if (self.out / "PAUSE").exists():
            raise SystemExit("PAUSE present: remove it (after the owner's decision) and run preflight again")
        self.check_order(batch_no)
        lab = self.labeler(batch_no, backend)
        hb = self.harness_backend(backend)
        st = lab.store(hb)
        sel = {p["pair_id"] for p in lab.w.pairs()}
        if any(r.get("pair_id") in sel for r in st.rows.values()) and not resume:
            raise SystemExit(f"{backend}: labels of this batch exist; pass --resume to continue")
        self.check_caps(backend, len(sel))
        lab.gate(hb, 1, acknowledged)
        if not sel:
            return EXIT_OK
        try:
            rc = lab.label_qwen() if hb == "qwen" else lab.label_gw()
        except jd.p4.HardStop as e:
            jd.g.log(f"HARD STOP: {e.reason}")
            return EXIT_HARD_STOP
        return self.served_model_stop(lab.key(hb), rc)

    def served_model_stop(self, key: str, rc: int) -> int:
        """§9.2: a served judge model id that is not the requested one is a hard stop. The
        harness stores nothing from such a reply and stops issuing; its Qwen path reports the
        stop as a pause, so the study turns it into a hard stop and writes PAUSE, which only
        the owner removes."""
        tp = self.out / f"totals-{key}.json"
        segs = json.loads(tp.read_text())["segments"] if tp.is_file() else []
        status = str(segs[-1]["status"]) if segs else ""
        if "served_model_mismatch" in status:
            (self.out / "PAUSE").write_text(f"hard stop: {key} {status} (§9.2); the owner decides\n",
                                            encoding="utf-8")
            return EXIT_HARD_STOP
        return rc

    # -- finalize
    def finalize(self, batch_no: int) -> int:
        self.check_order(batch_no)
        bl = self.batch_list()
        reg = {p["pair_id"]: p for p in self.registry()}
        ids = bl[batch_no - 1]
        c, e, tb, tb2, op = (self.labels(b) for b in BACKENDS)
        miss_ce = [p for p in ids if p not in c or p not in e]
        if miss_ce:
            raise SystemExit(f"batch {batch_no}: {len(miss_ce)} pair(s) lack a C or E label")
        esc = [p for p in ids if escalated(c[p], e[p])]
        miss_tb = [p for p in esc if p not in tb]
        miss_tb2 = [p for p in esc if p in tb and needs_reissue(tb[p]) and p not in tb2]
        sample = drift_sample(batch_no, [reg[p] for p in ids], float(self.cfg["drift"]["share"]))
        miss_op = [p for p in sample if p not in op]
        if miss_tb or miss_tb2 or miss_op:
            raise SystemExit(f"batch {batch_no}: tiebreaker missing on {len(miss_tb)}, its re-issue on "
                             f"{len(miss_tb2)}, Opus on {len(miss_op)} sampled pair(s)")
        path = self.out / "final.jsonl"
        for p in ids:
            lab, src = final_label(c[p], e[p], tb.get(p), tb2.get(p))
            jd.p4.append_fsync(path, {"k": f"pair|{p}", "schema": FINAL_SCHEMA, "batch": batch_no, "pair_id": p,
                                      "record_id": reg[p]["record_id"], "chunk_id": reg[p]["chunk_id"],
                                      "arm": reg[p]["arm"], "label": lab, "source": src,
                                      "c_label": jd.majority(c[p].get("verdicts") or []),
                                      "e_label": jd.majority((e[p].get("verdicts") or [])[:1]),
                                      "drift_sampled": p in set(sample),
                                      "opus": (op.get(p) or {}).get("verdict") if p in set(sample) else None})
        jd.p4.append_fsync(path, {"k": f"batch|{batch_no}", "schema": FINAL_SCHEMA, "batch": batch_no,
                                  "pairs": len(ids), "escalated": len(esc), "sampled": len(sample),
                                  "t": jd.g.now_iso(), "provenance": self.prov})
        summ = self.summary()
        jd.g.write_json_atomic(self.out / "judge-summary.json", summ)
        if summ["drift"]["alarm"]:
            jd.g.write_json_atomic(self.out / "drift-alarm.json", {"batch": batch_no, **summ["drift"],
                                                                     "t": jd.g.now_iso()})
            (self.out / "PAUSE").write_text(f"drift alarm after batch {batch_no}: "
                                            f"{'; '.join(summ['drift']['alarm_reasons'])}. The owner decides the "
                                            "remedy (a deviation entry).\n", encoding="utf-8")
            jd.notice_line("DRIFT", f"ALARM after batch {batch_no}: {summ['drift']['alarm_reasons']}",
                           self.out / "LOG")
            return EXIT_HARD_STOP
        return EXIT_OK

    def final_rows(self) -> dict[str, dict[str, Any]]:
        return {r["pair_id"]: r for r in jd.p4.read_jsonl_repair_tail(self.out / "final.jsonl")
                if r.get("k", "").startswith("pair|")}

    def summary(self) -> dict[str, Any]:
        fin = self.final_rows()
        sampled = [p for p, r in fin.items() if r.get("drift_sampled")]
        opus = {p: {"verdict": r.get("opus")} for p, r in fin.items() if r.get("drift_sampled")}
        arm_of = {p: r["arm"] for p, r in fin.items()}
        src = Counter(r["source"] for r in fin.values())
        n = len(fin)
        unj = src.get("unjudged", 0)
        return {"pairs_finalized": n, "batches_finalized": sorted(self.finalized()), "sources": dict(src),
                "unjudged": unj, "unjudged_share": unj / n if n else None,
                "judge_incomplete": bool(n and unj / n > float(self.cfg["unjudged_flag_share"])),
                "per_arm_pairs": dict(Counter(r["arm"] for r in fin.values())),
                "drift": drift_stats(fin, opus, sampled, arm_of, self.cfg["drift"]),
                "models": {k: FIXED[k]["model"] for k in BACKENDS}, "seeds": {
                    "judge_order": asc.SEEDS["judge_order"], "drift": asc.SEEDS["drift"]}}

    def status(self) -> dict[str, Any]:
        bl = self.batch_list()
        done = self.finalized()
        nxt = next((b for b in range(1, len(bl) + 1) if b not in done), None)
        st: dict[str, Any] = {"batches": len(bl), "finalized": sorted(done), "next": nxt,
                              "paused": (self.out / "PAUSE").exists()}
        if nxt is not None:
            have = {b: self.labels(b) for b in BACKENDS}
            todo: dict[str, Any] = {}
            for b in BACKENDS:
                try:
                    sel = self.selection(nxt, b)
                except SystemExit as e:
                    todo[b] = f"blocked: {e}"
                    continue
                todo[b] = sum(p["pair_id"] not in have[b] for p in sel)
            st["next_batch_left"] = todo
        return st


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance()
    except Exception:  # noqa: BLE001
        return jd.provenance()


def main(argv: Sequence[str] | None = None, *, http_factory: Callable[[], Any] | None = None,
         prov: Mapping[str, Any] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-pairs")
    sub.add_parser("status")
    for name in ("preflight", "label"):
        p = sub.add_parser(name)
        p.add_argument("--batch", type=int, required=True)
        p.add_argument("--backend", choices=BACKENDS, required=True)
        if name == "label":
            p.add_argument("--notice-acknowledged", action="store_true")
            p.add_argument("--resume", action="store_true")
    pf = sub.add_parser("finalize")
    pf.add_argument("--batch", type=int, required=True)
    args = ap.parse_args(argv)
    os.umask(0o077)
    cfg = load_config(args.config)
    pl = Pipeline(cfg, http_factory=http_factory, provenance=prov if prov is not None else provenance())
    if args.cmd == "build-pairs":
        man = pl.build()
        print(json.dumps(man["counts"], indent=1))
        return EXIT_OK
    if args.cmd == "status":
        print(json.dumps(pl.status(), indent=1))
        return EXIT_OK
    if args.cmd == "preflight":
        pl.preflight(args.batch, args.backend)
        return EXIT_OK
    if args.cmd == "label":
        return pl.label(args.batch, args.backend, args.notice_acknowledged, args.resume)
    return pl.finalize(args.batch)


if __name__ == "__main__":
    sys.exit(main())
