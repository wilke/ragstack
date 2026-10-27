#!/usr/bin/env python
"""Addendum Q to part 4 (EXPLORATORY): are the two-passage gold quotes answer-bearing?

The pre-registration is kept with the outputs, outside the repo. Nothing here
changes a part-4 confirmatory verdict. Nothing private is in this file: the
gateway, the prompt (with the product name), the file names and the frozen
prereg sha256 come from the private config (``--private-config`` or env
``CHUNKTRIAL_PRIVATE_CONFIG``), as for :mod:`deployed_docs_part4`.

Each (question, quote) pair of the valid set-M part-4 questions is classified on
its own as ``answer`` / ``setup`` / ``neither`` by a primary and a secondary
classifier (one gateway call per pair and classifier). Then, from the stored
labels only (no answer is graded again):

* prevalence of ``setup`` / ``neither`` per quote and per question, overall, by
  stratum and by quote position (A = ``gold[0]``, B = ``gold[1]``);
* Cohen's kappa between the classifiers (3-class and answer-vs-not, item-bootstrap CI);
* the clean set (both quotes ``answer``) under the primary, the secondary and both;
* the sensitivity: part 4's H1-H4 family (its set, condition, primary grader,
  readers, procedure, delta and seed, read from ``results-part4.json``'s design
  block) recomputed on the clean set with part 4's own helpers
  (:func:`deployed_docs_part4.hypothesis_tests` + :func:`deployed_docs_part4.decide`),
  by dropping questions from ``per_item``. Reading rule: a verdict unchanged ->
  ``unchanged`` (all unchanged = robust); a weaker verdict on the clean set whose
  point estimate (net share, every reader) keeps its sign -> ``unresolved on the
  clean set``; anything else -> ``changed`` (reported beside the confirmatory one).

Subcommands::

    part4_addendum_q.py run [--resume] [--batch-calls N] [--batch-input-tokens N] [--batch-output-tokens N]
    part4_addendum_q.py analyse
    part4_addendum_q.py export-human OUT        # blinded sample, key in OUT's sibling <stem>.key.json
    part4_addendum_q.py import-human GRADES --key KEY

The gateway client, pause triggers, state/resume checks and the append-only
stores are part 4's (:class:`deployed_docs_part4.Gateway`, ``Control``,
``Store``); exit codes 0 complete, 75 paused (resumable), 1 hard stop or crash.

``addendum_q`` config block (every key optional unless marked; defaults in :data:`DEFAULTS`)::

    primary            {model, tag, max_tokens, temperature (null = not sent)}
    secondary          {model, tag, max_tokens, temperature}
    gateway            {inflight (<= 4), passes}
    limits             {output_token_cap, batch_calls, batch_input_tokens, batch_output_tokens,
                        max_fail_consecutive, max_fail_total, empty_retries, empty_backoff_s,
                        rate_backoffs_s}   (as part 4's limits)
    set                the question set classified ("M")
    expected_questions int | null: the valid question count; a gate before any call when set
    kappa              {n_boot, level, seed}
    human              {n, seed}: the optional blinded human sample
    files              {prereg (REQUIRED), prereg_sha256 (the frozen sha; checked when set),
                        questions, part4_results, labels ("{tag}" placeholder), call_log,
                        state, results, human_results, pause_file}

and one prompt in the base ``prompts`` block: ``ADDQ_USER``, with the
placeholders ``{question}`` and ``{quote}`` (the product name written into it).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import os
import random
import signal
import string
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import chunktrial_stats as cs  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402
import deployed_docs_trial as dt  # noqa: E402
import section_aware_trial as sat  # noqa: E402
from section_aware_trial import log, read_jsonl  # noqa: E402

EXIT_OK, EXIT_HARD_STOP, EXIT_PAUSED = p4.EXIT_OK, p4.EXIT_HARD_STOP, p4.EXIT_PAUSED
LABELS = ("answer", "setup", "neither")
POSITIONS = ("A", "B")
STEP = "addq"
STATE_SCHEMA = "part4-addq-state/1"
RESULTS_SCHEMA = "part4-addq-results/1"
HUMAN_SCHEMA = "part4-addq-human/1"
MAX_INFLIGHT = 4
PRODUCT_PLACEHOLDER = "<PRODUCT>"
#: rule 1's marker words for this task (part 4's is ``Grade:``): ``Label: setup`` / ``Answer: setup``
MARKER = "(?:label|answer)"
parse_label = p4.LabelParser({lab: lab for lab in LABELS}, MARKER)
#: a quote whose label is one of these is not ``answer``: its question is not clean
UNLABELLED = frozenset({"unparseable", "no_label"})
REPO = sat._REPO_PY.parent.resolve()

DEFAULTS: dict[str, Any] = {
    "primary": {"model": "Claude Opus 5.5", "tag": "opus55", "max_tokens": 1024, "temperature": 0},
    "secondary": {"model": "GPT-5.6 Sol", "tag": "sol", "max_tokens": 4096, "temperature": None},
    "gateway": {"inflight": 4, "passes": 2},
    "limits": {"output_token_cap": 1_000_000, "batch_calls": None, "batch_input_tokens": None,
               "batch_output_tokens": None, "max_fail_consecutive": 5, "max_fail_total": 20,
               "empty_retries": 2, "empty_backoff_s": 30.0, "rate_backoffs_s": [30.0, 120.0, 600.0]},
    "set": "M",
    "expected_questions": 372,
    "kappa": {"n_boot": 10000, "level": 0.95, "seed": 261026},
    "human": {"n": 40, "seed": "261026|Q"},
    "files": {"prereg": None, "prereg_sha256": None, "questions": "part4-questions.jsonl",
              "part4_results": "results-part4.json", "labels": "addq-labels-{tag}.jsonl",
              "call_log": "addq-calls.jsonl", "state": "addq-state.json",
              "results": "results-part4-addendum-q.json", "human_results": "addq-human.json",
              "pause_file": "PAUSE"},
}


def addq_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """The ``addendum_q`` block merged over :data:`DEFAULTS` (one level of nesting)."""
    user = cfg.get("addendum_q")
    if not isinstance(user, dict):
        raise SystemExit("private config: the 'addendum_q' block is missing")
    out = copy.deepcopy(DEFAULTS)
    for k, v in user.items():
        if k not in DEFAULTS:
            raise SystemExit(f"private config: unknown addendum_q key {k!r}")
        if isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
            unknown = sorted(set(v) - set(DEFAULTS[k]))
            if unknown:
                raise SystemExit(f"private config: unknown addendum_q.{k} keys {unknown}")
            out[k].update(v)
        else:
            out[k] = v
    if not out["files"]["prereg"]:
        raise SystemExit("private config: addendum_q.files.prereg is required")
    n = out["gateway"]["inflight"]
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_INFLIGHT:
        raise SystemExit(f"private config: addendum_q.gateway.inflight must be an int in 1..{MAX_INFLIGHT}")
    ps = out["gateway"]["passes"]
    if not isinstance(ps, int) or isinstance(ps, bool) or ps < 1:
        raise SystemExit("private config: addendum_q.gateway.passes must be an int >= 1")
    if out["primary"]["tag"] == out["secondary"]["tag"]:
        raise SystemExit("private config: addendum_q primary and secondary need distinct tags")
    if "{tag}" not in out["files"]["labels"]:
        raise SystemExit("private config: addendum_q.files.labels needs a {tag} placeholder")
    return out


def prompt_template(cfg: dict[str, Any]) -> str:
    """``prompts.ADDQ_USER``: exactly the fields ``{question}`` and ``{quote}``, once each,
    and no unfilled ``<PRODUCT>`` placeholder (the synthetic example's)."""
    tpl = (cfg.get("prompts") or {}).get("ADDQ_USER")
    if not isinstance(tpl, str) or not tpl.strip():
        raise SystemExit("private config: prompts.ADDQ_USER is required")
    fields = [f for _, f, _, _ in string.Formatter().parse(tpl) if f is not None]
    if sorted(fields) != ["question", "quote"]:
        raise SystemExit("private config: prompts.ADDQ_USER needs {question} and {quote} exactly once each "
                         f"(found {fields})")
    if PRODUCT_PLACEHOLDER in tpl:
        raise SystemExit(f"private config: prompts.ADDQ_USER still contains the {PRODUCT_PLACEHOLDER} placeholder")
    return tpl


def messages(tpl: str, question: str, quote: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": tpl.format(question=question, quote=quote)}]


def refuse_in_repo(path: Path, what: str) -> None:
    rp = path.resolve()
    if rp == REPO or REPO in rp.parents:
        raise SystemExit(f"addq: refusing to write {what} inside the repository")


def notice(msg: str) -> None:
    """A one-line notice for the gateway owner (printed only; nothing is sent anywhere)."""
    print(f"GATEWAY-NOTICE part4-addq: {msg}", flush=True)


def label_row(k: str, res: dict[str, Any]) -> dict[str, Any]:
    """A stored classifier result: part 4's grade row with this task's label parser."""
    if res["status"] == "no_label":
        v, rule = "no_label", res.get("reason") or "empty"
    else:
        v, rule = parse_label(res["text"])
    return {"k": k, "v": v, "rule": rule, "raw": res.get("text", ""), "pt": int(res.get("pt") or 0),
            "ct": int(res.get("ct") or 0), "finish": res.get("finish"), "model": res.get("model"),
            "attempts": res.get("attempts")}


def pair_key(qid: str, pos: str, tag: str) -> str:
    return p4.ckey(STEP, qid, pos, "-", tag)


# -------------------------------------------------------------------- inputs

def load_questions(path: Path, set_: str) -> list[dict[str, Any]]:
    """The valid questions of ``set_`` (sorted by record id), each with two gold quotes."""
    if not path.is_file():
        raise SystemExit(f"addq: questions file not found: {path.name}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in read_jsonl(path):
        if r.get("set") != set_ or r.get("status") != "valid":
            continue
        rid = r.get("record_id")
        gold = r.get("gold")
        if not isinstance(rid, str) or rid in seen:
            raise SystemExit(f"addq: {path.name}: missing or duplicate record_id {rid!r}")
        if not (isinstance(gold, list) and len(gold) == 2 and all(isinstance(g, str) and g.strip() for g in gold)):
            raise SystemExit(f"addq: {path.name}: {rid} needs a gold list of two quotes")
        if not str(r.get("question") or "").strip():
            raise SystemExit(f"addq: {path.name}: {rid} has no question")
        seen.add(rid)
        out.append({"record_id": rid, "question": r["question"], "gold": list(gold), "doc_id": r.get("doc_id"),
                    "stratum": r.get("stratum"), "gen": r.get("gen")})
    return sorted(out, key=lambda r: r["record_id"])


def pairs_of(recs: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(r["record_id"], pos) for r in recs for pos in POSITIONS]


def quote_of(rec: dict[str, Any], pos: str) -> str:
    return rec["gold"][POSITIONS.index(pos)]


def check_inputs(recs: list[dict[str, Any]], part4: dict[str, Any], set_: str, expected: int | None) -> None:
    """The questions file and part 4's results must describe the same question set."""
    qs = part4.get("questions") or {}
    in_results = sorted(q for q, v in qs.items() if v.get("set") == set_)
    mine = [r["record_id"] for r in recs]
    if in_results != mine:
        raise p4.HardStop(f"inputs_mismatch:{len(mine)} valid {set_} questions vs {len(in_results)} in part-4 results")
    if expected is not None and len(mine) != expected:
        raise p4.HardStop(f"expected_questions_mismatch:{len(mine)}!={expected}")


# ------------------------------------------------------------------ analysis

def _share(k: int, n: int) -> float | None:
    return round(k / n, 4) if n else None


def prevalence(recs: list[dict[str, Any]], lab: dict[tuple[str, str], str]) -> dict[str, Any]:
    """Per quote and per question, overall, by stratum and by quote position."""
    def block(rs: list[dict[str, Any]]) -> dict[str, Any]:
        vals = [lab[(r["record_id"], p)] for r in rs for p in POSITIONS]
        cnt = Counter(vals)
        n_lab = sum(cnt[x] for x in LABELS)
        per_q: Counter[str] = Counter(dict.fromkeys(
            ("any_setup", "any_neither", "any_not_answer", "any_unlabelled", "clean"), 0))
        for r in rs:
            two = [lab[(r["record_id"], p)] for p in POSITIONS]
            per_q["any_setup"] += "setup" in two
            per_q["any_neither"] += "neither" in two
            per_q["any_not_answer"] += any(x in ("setup", "neither") for x in two)
            per_q["any_unlabelled"] += any(x in UNLABELLED for x in two)
            per_q["clean"] += all(x == "answer" for x in two)
        nq = len(rs)
        return {"per_quote": {"n": len(vals), "n_labelled": n_lab,
                              "counts": {x: cnt[x] for x in (*LABELS, *sorted(UNLABELLED))},
                              "share": {x: _share(cnt[x], len(vals)) for x in ("setup", "neither")},
                              "share_of_labelled": {x: _share(cnt[x], n_lab) for x in ("setup", "neither")}},
                "per_question": {"n": nq, "counts": dict(sorted(per_q.items())),
                                 "share": {k: _share(v, nq) for k, v in sorted(per_q.items())}}}

    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        strata[str(r["stratum"])].append(r)
    by_pos = {}
    for p in POSITIONS:
        cnt = Counter(lab[(r["record_id"], p)] for r in recs)
        by_pos[p] = {"n": len(recs), "counts": {x: cnt[x] for x in (*LABELS, *sorted(UNLABELLED))},
                     "share": {x: _share(cnt[x], len(recs)) for x in ("setup", "neither")}}
    return {"overall": block(recs), "by_stratum": {s: block(rs) for s, rs in sorted(strata.items())},
            "by_position": by_pos}


def length_cuts(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The share of stored rows cut off by ``max_tokens`` (``no_label`` with rule ``truncated``)."""
    n = len(rows)
    k = sum(1 for r in rows.values() if r["v"] == "no_label" and r["rule"] == "truncated")
    return {"n": n, "truncated": k, "share": _share(k, n)}


def kappa_block(a: list[str], b: list[str], kc: dict[str, Any]) -> dict[str, Any]:
    """Cohen's kappa over the pairs both raters labelled: 3-class and answer-vs-not."""
    both = [(x, y) for x, y in zip(a, b, strict=True) if x in LABELS and y in LABELS]
    out: dict[str, Any] = {"n_pairs": len(a), "n_labelled_both": len(both), "n_excluded": len(a) - len(both)}
    if not both:
        return {**out, "three_class": None, "answer_vs_not": None}
    xa, xb = [x for x, _ in both], [y for _, y in both]
    nb, seed, level = int(kc["n_boot"]), int(kc["seed"]), float(kc["level"])
    out["agreement"] = _share(sum(x == y for x, y in both), len(both))
    out["three_class"] = cs.cohen_kappa_ci(xa, xb, categories=list(LABELS), n_boot=nb, seed=seed, level=level)
    out["answer_vs_not"] = cs.cohen_kappa_ci([x == "answer" for x in xa], [y == "answer" for y in xb],
                                             categories=[True, False], n_boot=nb, seed=seed, level=level)
    return out


def clean_ids(recs: list[dict[str, Any]], lab: dict[tuple[str, str], str]) -> list[str]:
    """A question is clean when both of its quotes are labelled ``answer``."""
    return [r["record_id"] for r in recs if all(lab[(r["record_id"], p)] == "answer" for p in POSITIONS)]


#: verdict strength: a lower rank on the clean set is a non-confirmation there
RANK = {"confirmed": 2, "reader-dependent": 1, "not confirmed": 0, "not shown non-inferior": 0, "not reached": 0}


def _sign(x: Any) -> int:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0
    return (x > 0) - (x < 0)


def reading(full: dict[str, Any], clean: dict[str, Any], readers: list[str]) -> str:
    """The prereg's reading rule for one hypothesis (full -> clean)."""
    if clean["status"] == full["status"]:
        return "unchanged"
    flipped = any(_sign(full["per_reader"][rd].get("net_share")) * _sign(clean["per_reader"][rd].get("net_share")) < 0
                  for rd in readers)
    if RANK[clean["status"]] < RANK[full["status"]] and not flipped:
        return "unresolved on the clean set"
    return "changed"


def family(per_item: dict[str, Any], questions: dict[str, Any], qids: list[str], design: dict[str, Any]) -> dict[str, Any]:
    """Part 4's confirmatory family on ``qids`` (exactly as ``analyse_part4``'s ``family``)."""
    an, readers = design["analysis"], list(design["readers"])
    hyps = p4.hypothesis_tests(per_item, questions, qids, an["family_condition"], design["grader_primary"]["tag"],
                               readers, an, int(design["seed"]))
    return {"n_questions": len(qids), "n_pages": len({questions[q]["doc_id"] for q in qids}),
            "hypotheses": p4.decide(hyps, readers, an)}


def _brief(h: dict[str, Any], readers: list[str]) -> dict[str, Any]:
    keep = ("n", "net_share", "p", "p_holm", "significant", "ci")
    return {"status": h["status"], "per_reader": {rd: {k: h["per_reader"][rd].get(k) for k in keep} for rd in readers}}


def _same_as_stored(st: dict[str, Any], new: dict[str, Any], readers: list[str]) -> bool:
    """Status, and per reader ``p``, ``p_holm`` and ``net_share``, equal to part 4's stored family."""
    if st.get("status") != new["status"]:
        return False
    for rd in readers:
        a, b = (st.get("per_reader") or {}).get(rd) or {}, new["per_reader"][rd]
        for k in ("p", "p_holm", "net_share"):
            x, y = a.get(k), b.get(k)
            if x is None or y is None:
                if x is not y:
                    return False
            elif not math.isclose(float(x), float(y), rel_tol=1e-9, abs_tol=1e-12):
                return False
    return True


def sensitivity(part4: dict[str, Any], clean_sets: dict[str, list[str]]) -> dict[str, Any]:
    """H1-H4 on the full family and on each clean set, with the reading per hypothesis.

    The full family is recomputed with the same code and compared with the statuses
    stored in part 4's results (``reproduces_part4``): a mismatch means the inputs or
    the code differ from what part 4 ran, and the sensitivity must not be read."""
    design, per_item, questions = part4["design"], part4["per_item"], part4["questions"]
    an, readers = design["analysis"], list(design["readers"])
    fam_q = sorted(q for q in questions if questions[q]["set"] == an["family_set"])
    full = family(per_item, questions, fam_q, design)
    stored = ((part4.get("analysis") or {}).get("family") or {}).get("hypotheses") or {}
    repro = {h: _same_as_stored(stored.get(h) or {}, full["hypotheses"][h], readers) for h in an["order"]}
    out: dict[str, Any] = {
        "family": {"set": an["family_set"], "condition": an["family_condition"],
                   "grader": design["grader_primary"]["tag"], "readers": readers, "procedure": an["procedure"],
                   "delta": an["delta"], "seed": design["seed"]},
        "reproduces_part4": all(repro.values()), "reproduces_part4_by_hypothesis": repro,
        "full": {"n_questions": full["n_questions"], "n_pages": full["n_pages"],
                 "hypotheses": {h: _brief(full["hypotheses"][h], readers) for h in an["order"]}},
        "clean": {},
    }
    for name, ids in clean_sets.items():
        keep = set(ids)
        qids = [q for q in fam_q if q in keep]
        cl = family(per_item, questions, qids, design)
        hyps = {}
        for h in an["order"]:
            f, c = full["hypotheses"][h], cl["hypotheses"][h]
            hyps[h] = {"full": f["status"], "clean": c["status"], "reading": reading(f, c, readers),
                       "clean_detail": _brief(c, readers)}
        readings = [v["reading"] for v in hyps.values()]
        out["clean"][name] = {
            "n_questions": cl["n_questions"], "n_pages": cl["n_pages"], "dropped": len(fam_q) - len(qids),
            "hypotheses": hyps,
            "summary": ("robust: every hypothesis keeps its confirmatory verdict" if all(r == "unchanged" for r in readings)
                        else "not robust: " + ", ".join(f"{h} {hyps[h]['reading']}" for h in an["order"]
                                                         if hyps[h]["reading"] != "unchanged")),
        }
    if not out["reproduces_part4"]:
        for blk in out["clean"].values():
            blk["summary"] = "not read: full set does not reproduce part 4"
    return out


def human_agreement(items: dict[str, str], key: dict[str, dict[str, str]],
                    labels: dict[str, dict[tuple[str, str], str]], kc: dict[str, Any]) -> dict[str, Any]:
    """Human vs each classifier on the blinded sample (``items``: item id -> human label)."""
    unknown = sorted(set(items) - set(key))
    if unknown:
        raise SystemExit(f"addq import-human: items not in the key: {unknown[:5]}")
    bad = sorted(i for i, v in items.items() if v not in LABELS)
    if bad:
        raise SystemExit(f"addq import-human: labels must be one of {list(LABELS)} (items {bad[:5]})")
    ids = sorted(items)
    out: dict[str, Any] = {"n_items_in_key": len(key), "n_graded": len(ids),
                           "missing": sorted(set(key) - set(items)),
                           "human_counts": dict(Counter(items[i] for i in ids))}
    for tag, lab in labels.items():
        model = [lab.get((key[i]["record_id"], key[i]["position"]), "no_label") for i in ids]
        out[tag] = kappa_block([items[i] for i in ids], model, kc)
    return out


# ----------------------------------------------------------------------- run

class AddQ:
    """One addendum-Q run directory: config, inputs, stores, state."""

    def __init__(self, cfg_path: Path, out: Path, provenance: Callable[[], dict[str, Any]] | None = None,
                 batch: dict[str, int | None] | None = None) -> None:
        self.cfg = dt.load_private_config(str(cfg_path))
        self.aq = addq_config(self.cfg)
        for k, v in (batch or {}).items():
            if v is not None:
                self.aq["limits"][k] = v
        self.out = out
        refuse_in_repo(out, "the addendum-Q outputs")
        self.files = self.aq["files"]
        self.tpl = prompt_template(self.cfg)
        self.config_sha = p4.file_sha(cfg_path)
        prereg = out / self.files["prereg"]
        if not prereg.is_file():
            raise SystemExit(f"addq: prereg not found in the output directory: {self.files['prereg']}")
        self.prereg_sha = p4.file_sha(prereg)
        self.provenance = provenance or (lambda: _experiment_provenance())
        self.specs = {self.aq["primary"]["tag"]: self.aq["primary"],
                      self.aq["secondary"]["tag"]: self.aq["secondary"]}
        self.state_path = out / self.files["state"]

    def path(self, key: str, tag: str | None = None) -> Path:
        name = self.files[key]
        return self.out / (name.format(tag=tag) if tag else name)

    def load_inputs(self) -> None:
        qp, rp = self.path("questions"), self.path("part4_results")
        if not rp.is_file():
            raise SystemExit(f"addq: part-4 results not found: {rp.name}")
        self.recs = load_questions(qp, self.aq["set"])
        self.rec = {r["record_id"]: r for r in self.recs}
        self.part4 = json.loads(rp.read_text())
        self.inputs_sha = {"questions": p4.file_sha(qp), "part4_results": p4.file_sha(rp)}
        self.pairs = pairs_of(self.recs)

    def stores(self) -> dict[str, p4.Store]:
        return {t: p4.Store(self.path("labels", t)) for t in self.specs}

    def missing(self) -> list[tuple[str, str, str]]:
        return [(t, q, pos) for t in self.specs for q, pos in self.pairs if pair_key(q, pos, t) not in self.st[t]]

    # ------------------------------------------------------------ state
    def write_state(self, status: str, reason: str | None) -> None:
        segs = list(self.prev_state.get("segments", [])) if self.prev_state else []
        segs.append(self.segment(status, reason))
        done = sum(len(s.rows) for s in self.st.values()) if hasattr(self, "st") else None
        p4.write_json_atomic(self.state_path, {
            "schema": STATE_SCHEMA, "status": status, "reason": reason,
            "counts": {"planned": len(self.pairs) * len(self.specs) if hasattr(self, "pairs") else None, "done": done},
            "totals": self.control.totals(), "commit": self.commit, "prereg_sha256": self.prereg_sha,
            "config_sha256": self.config_sha, "inputs_sha256": getattr(self, "inputs_sha", None),
            "segments": segs, "updated": p4.now_iso()})

    def segment(self, status: str, reason: str | None) -> dict[str, Any]:
        return {"started": self.started, "ended": p4.now_iso(), "end": status, "reason": reason,
                "totals_since_resume": dict(self.control.seg), "resumed": self.resume,
                "limits": {k: self.aq["limits"][k] for k in ("output_token_cap", "batch_calls",
                                                             "batch_input_tokens", "batch_output_tokens")}}

    # ------------------------------------------------------------ run
    def execute(self, resume: bool) -> int:
        self.resume = resume
        self.started = p4.now_iso()
        self.prev_state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        self.prov = self.provenance()
        self.commit = self.prov.get("commit")
        self.load_inputs()
        prev_tot = None
        if resume:
            st = self.prev_state
            if st is None:
                log("addq --resume: no state file")
                return EXIT_HARD_STOP
            if st.get("status") == "hard_stop":
                log(f"addq --resume refused: the run hard-stopped ({st.get('reason')}); a new decision is needed")
                return EXIT_HARD_STOP
            bad = [k for k, v in (("commit", self.commit), ("prereg_sha256", self.prereg_sha),
                                  ("config_sha256", self.config_sha), ("inputs_sha256", self.inputs_sha))
                   if st.get(k) != v]
            if bad:
                log(f"addq --resume refused (hard stop): {', '.join(bad)} differ from {self.files['state']}")
                return EXIT_HARD_STOP
            prev_tot = st.get("totals", {}).get("since_start")
        elif self.prev_state is not None:
            log(f"addq: {self.files['state']} exists; continue with --resume (or move the outputs away)")
            return EXIT_HARD_STOP
        self.control = p4.Control(self.aq["limits"], self.out / self.files["pause_file"], prev_tot)
        frozen = self.files.get("prereg_sha256")
        if frozen and frozen != self.prereg_sha:
            self.write_state("hard_stop", "prereg_sha_mismatch")
            log("addq: the prereg file does not match the frozen sha256 (hard stop)")
            return EXIT_HARD_STOP
        old = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: self.control.stop("sigterm"))
        try:
            self.write_state("running", None)
            check_inputs(self.recs, self.part4, self.aq["set"], self.aq["expected_questions"])
            self.st = self.stores()
            self.classify()
            self.write_results()
        except p4.Pause as p:
            self.write_state("paused", p.reason)
            t = self.control.totals()
            notice(f"PAUSED ({p.reason}); totals since start {json.dumps(t['since_start'])}; "
                   f"since last resume {json.dumps(t['since_resume'])}; resume with: run --resume")
            return EXIT_PAUSED
        except p4.HardStop as h:
            self.write_state("hard_stop", h.reason)
            log(f"addq HARD STOP: {h.reason}")
            return EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001 - recorded, exit 1, resumable
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            self.write_state("crashed", reason)
            log(f"addq CRASHED: {reason}")
            notice(f"CRASHED ({type(e).__name__}); after fixing the cause, resume with: run --resume")
            return EXIT_HARD_STOP
        finally:
            signal.signal(signal.SIGTERM, old)
        self.write_state("complete", None)
        notice(f"complete; totals since start {json.dumps(self.control.totals()['since_start'])}")
        return EXIT_OK

    def classify(self) -> None:
        gw = p4.Gateway(dt.OPUS_URL, self.control, self.aq["limits"], self.path("call_log"))
        self.gateway = gw
        todo = self.missing()
        if not todo:
            return
        notice(f"batch: {len(todo)} classifier calls to issue (<= {self.aq['gateway']['inflight']} in flight)")

        def job(x: tuple[str, str, str]) -> Callable[[Any], Any]:
            t, q, pos = x
            k = pair_key(q, pos, t)
            msgs = messages(self.tpl, self.rec[q]["question"], quote_of(self.rec[q], pos))

            async def fn(http: Any) -> None:
                res = await gw.standard(http, self.specs[t], msgs, STEP, k)
                if res is not None:
                    self.st[t].put(label_row(k, res))
            return fn

        for _ in range(int(self.aq["gateway"]["passes"])):
            asyncio.run(p4.run_jobs([("gateway", job(x)) for x in todo], int(self.aq["gateway"]["inflight"]),
                                    self.control))
            if self.control.stopped():
                raise p4.Pause(self.control.reason or "stopped")
            todo = self.missing()
            if not todo:
                break
        if todo:
            raise p4.Pause(f"calls_failed:{len(todo)}")
        notice(f"classification done; totals since start {json.dumps(self.control.totals()['since_start'])}")

    # ---------------------------------------------------------- results
    def labels(self) -> dict[str, dict[tuple[str, str], str]]:
        return {t: {(q, pos): self.st[t].rows[pair_key(q, pos, t)]["v"] for q, pos in self.pairs
                    if pair_key(q, pos, t) in self.st[t]} for t in self.specs}

    def analysis(self) -> dict[str, Any]:
        lab = self.labels()
        pt, stag = self.aq["primary"]["tag"], self.aq["secondary"]["tag"]
        rules = {t: dict(sorted(Counter(r["rule"] for r in self.st[t].rows.values()).items())) for t in self.specs}
        usage = {t: {"prompt_tokens": sum(int(x.get("pt") or 0) for x in s.rows.values()),
                     "completion_tokens": sum(int(x.get("ct") or 0) for x in s.rows.values()),
                     "calls_recorded": len(s.rows)} for t, s in self.st.items()}
        cp, cs_ = clean_ids(self.recs, lab[pt]), clean_ids(self.recs, lab[stag])
        both = sorted(set(cp) & set(cs_))
        clean_sets = {"primary": cp, "secondary": cs_, "intersection": both}
        hp = self.path("human_results")
        return {
            "n_questions": len(self.recs), "n_pairs": len(self.pairs),
            "rules": rules, "usage": usage,
            "length_cuts": {"primary": {"tag": pt, **length_cuts(self.st[pt].rows)},
                            "secondary": {"tag": stag, **length_cuts(self.st[stag].rows)},
                            "truncated_share_over_1pct": (length_cuts(self.st[pt].rows)["share"] or 0) > 0.01},
            "prevalence": {t: prevalence(self.recs, lab[t]) for t in self.specs},
            "kappa": kappa_block([lab[pt][x] for x in self.pairs], [lab[stag][x] for x in self.pairs],
                                 self.aq["kappa"]),
            "clean_sets": {k: {"n": len(v), "ids": v} for k, v in clean_sets.items()},
            "sensitivity": sensitivity(self.part4, clean_sets),
            "human": json.loads(hp.read_text()) if hp.is_file() else None,
        }

    def write_results(self) -> None:
        if self.missing():
            raise SystemExit(f"addq: {len(self.missing())} classifier labels missing; finish the run first")
        prov = self.prov
        citable = bool(prov.get("commit")) and prov.get("dirty") is False and prov.get("untracked") == 0
        segs = list(self.prev_state.get("segments", [])) if self.prev_state else []
        if hasattr(self, "control"):
            segs.append(self.segment("complete", None))
        report = {
            "schema": RESULTS_SCHEMA, "status": "EXPLORATORY",
            "note": "Nothing here changes a part-4 confirmatory verdict.",
            "prereg": {"file": self.files["prereg"], "sha256": self.prereg_sha},
            "config_sha256": self.config_sha, "inputs_sha256": self.inputs_sha,
            "provenance": prov, "citable": citable, "run_segments": segs,
            "design": {"primary": self.aq["primary"], "secondary": self.aq["secondary"], "set": self.aq["set"],
                       "labels": list(LABELS), "positions": {"A": "gold[0]", "B": "gold[1]"},
                       "prompt_sha256": dt.sha(self.tpl), "kappa": self.aq["kappa"],
                       "temperature_dropped": sorted(getattr(getattr(self, "gateway", None), "temperature_dropped",
                                                             set()))},
            "analysis": self.analysis(),
        }
        p4.write_json_atomic(self.path("results"), report)
        log(f"addq: wrote {self.files['results']}")

    def analyse_only(self) -> int:
        """Recompute the results file from the stored labels (no calls)."""
        self.prev_state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        self.prov = self.provenance()
        frozen = self.files.get("prereg_sha256")
        if frozen and frozen != self.prereg_sha:
            log("addq analyse: the prereg file does not match the frozen sha256 (hard stop)")
            return EXIT_HARD_STOP
        self.load_inputs()
        try:
            check_inputs(self.recs, self.part4, self.aq["set"], self.aq["expected_questions"])
        except p4.HardStop as h:
            log(f"addq analyse HARD STOP: {h.reason}")
            return EXIT_HARD_STOP
        self.st = self.stores()
        if self.missing():
            log(f"addq analyse: {len(self.missing())} labels missing; finish the run first")
            return EXIT_HARD_STOP
        self.write_results()
        return EXIT_OK

    # ------------------------------------------------------- human sample
    def export_human(self, path: Path) -> int:
        """40 (question, quote) pairs (seed ``human.seed``), blinded: no model label, no
        record id, no position in the items file; the key is ``<stem>.key.json``."""
        refuse_in_repo(path, "the human-sample export")
        self.load_inputs()
        h = self.aq["human"]
        pick = random.Random(str(h["seed"])).sample(sorted(self.pairs), min(int(h["n"]), len(self.pairs)))
        items, key = [], {}
        for n, (q, pos) in enumerate(pick):
            iid = f"q{n:03d}"
            items.append({"item": iid, "question": self.rec[q]["question"], "quote": quote_of(self.rec[q], pos)})
            key[iid] = {"record_id": q, "position": pos}
        kpath = path.with_name(path.stem + ".key.json")
        p4.write_json_atomic(path, {"labels": list(LABELS), "items": items,
                                    "note": "PRIVATE. Fill in 'label' per item; the key is a separate file."})
        p4.write_json_atomic(kpath, {"key": key, "seed": str(h["seed"]),
                                     "note": "PRIVATE blinding key: never shown to the grader."})
        log(f"addq: human sample: {len(items)} pairs -> {path.name} (+ {kpath.name})")
        return EXIT_OK

    def import_human(self, grades: Path, key_path: Path) -> int:
        """Human labels (the export with ``label`` filled in, or ``{"grades": {item: label}}``)
        against both classifiers; written to ``files.human_results`` and folded into the
        results file by ``analyse``."""
        self.load_inputs()
        self.st = self.stores()
        g = json.loads(grades.read_text())
        items = ({str(k): v for k, v in g["grades"].items()} if "grades" in g
                 else {it["item"]: it["label"] for it in g["items"] if it.get("label") not in (None, "")})
        key = json.loads(key_path.read_text())["key"]
        res = {"schema": HUMAN_SCHEMA, **human_agreement(items, key, self.labels(), self.aq["kappa"])}
        p4.write_json_atomic(self.path("human_results"), res)
        log(f"addq: human agreement on {res['n_graded']} items -> {self.files['human_results']}")
        return EXIT_OK


def _experiment_provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance

    return experiment_provenance(repo=str(REPO))


# ----------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None, provenance: Callable[[], dict[str, Any]] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG"),
                    help="the private JSON config (or env CHUNKTRIAL_PRIVATE_CONFIG); required")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="classify every (question, quote) pair (pause/resume; exit 75 = paused)")
    r.add_argument("--resume", action="store_true", help="continue a paused run (commit/prereg/config sha must match)")
    r.add_argument("--batch-calls", type=int, default=None, help="pause after this many gateway calls this segment")
    r.add_argument("--batch-input-tokens", type=int, default=None)
    r.add_argument("--batch-output-tokens", type=int, default=None)
    sub.add_parser("analyse", help="recompute the results file from the stored labels (no calls)")
    e = sub.add_parser("export-human", help="write the blinded human sample (PRIVATE; outside the repo)")
    e.add_argument("out")
    i = sub.add_parser("import-human", help="human labels vs the classifiers (JSON in, JSON out)")
    i.add_argument("grades")
    i.add_argument("--key", required=True, help="the export's <stem>.key.json")
    args = ap.parse_args(argv)
    if not args.private_config:
        raise SystemExit("a private config is required: pass --private-config or set CHUNKTRIAL_PRIVATE_CONFIG")
    cfg = json.loads(Path(args.private_config).read_text())
    out = Path(args.out_dir or cfg["out_dir"])
    batch = ({"batch_calls": args.batch_calls, "batch_input_tokens": args.batch_input_tokens,
              "batch_output_tokens": args.batch_output_tokens} if args.cmd == "run" else None)
    aq = AddQ(Path(args.private_config), out, provenance, batch)
    if args.cmd == "run":
        return aq.execute(args.resume)
    if args.cmd == "analyse":
        return aq.analyse_only()
    if args.cmd == "export-human":
        return aq.export_human(Path(args.out))
    return aq.import_human(Path(args.grades), Path(args.key))


if __name__ == "__main__":
    sys.exit(main())
