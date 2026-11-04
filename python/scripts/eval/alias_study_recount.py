#!/usr/bin/env python
"""Alias study (OA-1000): the committed recount of the record set (DRAFT prereg §4.1, §4.3; §15 items 2, 3).

    alias_study_recount.py --study-dir /rag/tmp/alias-study-2026-11 \\
                           [--chunk-export chunks-oa.jsonl] --out record-recount.json

Everything is recomputed from the private build logs with committed functions only:
:func:`alias_study_common.load_records`, :func:`~alias_study_common.verify_record_set` and
:func:`~alias_study_common.split_records` (which apply ``glossbench_alias_waves.satisfied`` and
``glossbench_alias_checks.alias_key``, the prereg's balanced-set rule), and for the activation
order the drafter's own rule (``glossbench_alias_draft.AliasRun.paper_states`` /
``wave_order``: ``wv.PaperState`` over the pool's jobs in ``(paper, stratum, rank)`` order,
``wv.reachable``, ``random.Random('<pool seed>|waves').sample``), re-stated in
:func:`activation_order` because the drafter's class needs a live run directory.

What it reports (counts and shas only):

* the record-set identity (§4.1): the accepted-record file's sha256 and the balanced-set id
  sha, 1,091 / 300 papers / 900 balanced / 191 held out, the layouts per paper, the alias kinds;
* the **verdict source** of the 1,091 and of the 900 (§4.1 table): the record's
  ``verifier_model`` (``claude-opus-5-5`` or the Codex fallback ``gpt-6-astra``), and for a Codex
  verdict the reason the CLI was not used, read from the stored ``refusal_text`` of the record's
  verify row (:func:`refusal_reason`): a CLI safeguard fallback (another model answered) or a
  safeguard API error;
* the build overlaps of §4.3 items 4–6: pilot papers, calibration record ids and question
  papers, and the judge-validation records, pairs and paper exclusion list against the study's
  papers, with how much of the extension from 200 to 300 satisfied papers the exclusion list
  covers (§15 item 3);
* every other §4.1 / §4.3 figure the stored logs can reproduce (gateway calls and tokens, the
  verifier's verdicts, pool and corpus counts, the wave activation).

Each recomputed figure is compared with the prereg's (:data:`PREREG`) under ``checks``; a
mismatch is reported, never raised. ``verify_record_set`` does raise when the record set is
not the frozen one. The output is a public artifact (§11): no record, paper, chunk or pair id
and no question text (:func:`alias_study_common.public_problems`; ``host`` and ``warnings``
are dropped from the embedded provenance, as ``alias_study_stats.public_copy`` does). A run
refuses to write an output that fails the check.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import alias_study_common as asc  # noqa: E402

SCHEMA = "alias-study-record-recount/1"

#: the private build layout under ``--study-dir`` (optional entries are sha'd and checked when present)
LAYOUT: dict[str, tuple[str, bool]] = {
    "records": ("out/main/accepted-alias-O.jsonl", True),
    "acceptance_summary": ("out/main/acceptance-summary-alias-O.json", True),
    "acceptance_log": ("out/main/acceptance-alias-O.jsonl", True),
    "verify_log": ("out/main/verify-alias-O.jsonl", True),
    "main_drafts": ("out/main/draft-alias-O.jsonl", True),
    "main_draft_calls": ("out/main/draft-calls-alias-O.jsonl", True),
    "pool": ("out/main/pool-alias-O.jsonl", True),
    "pool_manifest": ("out/main/pool-manifest-alias-O.json", False),
    "waves_state": ("out/main/waves-state-alias-O.json", True),
    "waves_totals": ("out/main/waves-totals-alias-O.json", False),
    "pilot1_drafts": ("out/draft-alias-O-pilot.jsonl", True),
    "pilot2_drafts": ("out/pilot2/draft-alias-O.jsonl", True),
    "calib_pairs": ("calib/pairs.jsonl", True),
    "validation_pairs": ("calib/validation/pairs.jsonl", True),
    "validation_exclude": ("calib/validation/exclude-papers.txt", True),
    "validation_drafts": ("out/validation/draft-alias-V.jsonl", True),
    "validation_acceptance": ("out/validation/acceptance-alias-V.jsonl", True),
    "validation_accepted": ("out/validation/accepted-alias-V.jsonl", True),
    "merged_glossary": ("glossary/merged_glossary.jsonl", False),
    "abbrev_entries": ("glossary/abbrev_entries.jsonl", False),
}

#: the figures the prereg states (DRAFT v3 §4.1 table, §4.3, §6.4), by check name. A prefix-only
#: sha in the prereg (``ff16e611…``) is compared as a prefix.
PREREG: dict[str, Any] = {
    "accepted_sha256": asc.ACCEPTED_SHA256,
    "accepted_sha256_equals_summary": True,
    "balanced_ids_sha256": asc.BALANCED_SHA256,
    "accepted.records": 1091, "accepted.G": 683, "accepted.C": 408, "accepted.papers": 408,
    "balanced.papers": 300, "balanced.papers_exactly_2G_1C": 300, "balanced.records": 900,
    "balanced.G": 600, "balanced.C": 300,
    "held_out.records": 191, "held_out.G": 83, "held_out.C": 108, "held_out.papers": 108,
    "held_out.papers_1G_1C": 83, "held_out.papers_1C_only": 25,
    "balanced.alias_kind.abbreviation.G": 508, "balanced.alias_kind.abbreviation.C": 257,
    "balanced.alias_kind.symbol.G": 88, "balanced.alias_kind.symbol.C": 38,
    "balanced.alias_kind.both.G": 4, "balanced.alias_kind.both.C": 5,
    "gold_chunks_length_1": 1091,
    "pilot_false": 1091,
    "drafter.prompt_alias_draft_2": 1091, "drafter.served_gemini_25_pro_draft_rows": 2904,
    "verifier.prompt_alias_verify_3": 1091,
    "verifier.claude.pass": 1037, "verifier.claude.reject": 504,
    "verifier.codex.pass": 54, "verifier.codex.reject": 36,
    "verdict_source.all.claude-opus-5-5": 1037, "verdict_source.all.gpt-6-astra": 54,
    "verdict_source.all.codex_after_cli_safeguard_fallback": 47,
    "verdict_source.all.codex_after_safeguard_api_error": 7,
    "verdict_source.balanced.claude-opus-5-5": 853, "verdict_source.balanced.gpt-6-astra": 47,
    "verdict_source.balanced.codex_after_cli_safeguard_fallback": 40,
    "verdict_source.balanced.codex_after_safeguard_api_error": 7,
    "verify_log.cli_refusals": 90, "verify_log.cli_safeguard_fallbacks": 80, "verify_log.codex_calls": 90,
    "gateway.calls": 2912, "gateway.answered": 2904, "gateway.failures": 8,
    "gateway.failures_http_502": 5, "gateway.failures_http_503": 3,
    "gateway.prompt_tokens": 2544684, "gateway.completion_tokens": 6027230,
    "pool.seed": "261031|alias|main|O", "pool.sha256": "b49b2daf", "pool.eligible_papers": 857,
    "pool.eval_papers_sha256": "4849699c", "pool.schema": "glossbench-alias-pool-manifest/1",
    "waves.activated": 532, "waves.waves": 20, "waves.target": 300, "waves.stop": "target_met",
    "waves.accepted_first_segment_close": 445, "waves.accepted_at_200": 734, "waves.activated_at_200": 355,
    "corpus.chunks": 32765, "corpus.documents": 12560, "corpus.papers": 1000, "corpus.sha256": "ff16e611",
    "glossary.merged_sha256": "229519d7", "glossary.abbrev_sha256": "0f2c37e3",
    "pilot.papers_with_accepted": 18, "pilot.satisfied_papers": 13,
    "calibration.record_ids_in_main_drafts": 8, "calibration.record_ids_in_accepted": 4,
    "calibration.record_ids_in_accepted.G": 1, "calibration.record_ids_in_accepted.C": 3,
    "calibration.record_ids_in_balanced": 2, "calibration.record_ids_in_balanced.C": 2,
    "validation.exclude_papers": 377,
    "validation.satisfied_outside_exclusion": 91,
    "validation.satisfied_with_validation_drafts": 8, "validation.drafts_on_those_papers": 44,
    "validation.l1_passed_on_those_papers": 25, "validation.accepted_on_those_papers": 18,
    "validation.balanced_records_on_those_papers": 24, "validation.balanced_records_on_those_papers.G": 16,
    "validation.balanced_records_on_those_papers.C": 8,
    "validation.pairs": 587, "validation.question_papers_in_satisfied": 6,
    "validation.pairs_with_question_paper_in_satisfied": 121,
    "validation.pairs_with_balanced_gold_passage": 39, "validation.distinct_balanced_gold_passages": 35,
    "validation.pairs_with_balanced_gold_passage_and_question_paper_in_satisfied": 21,
    "validation.pairs_with_passage_paper_in_satisfied": 190,
    "validation.pairs_touching_satisfied": 243,
    "validation.exclusion_covers_extension": False,
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return asc.read_jsonl(path)


def read_exclude(path: Path) -> set[str]:
    """One paper id per line; blank lines and ``#`` comments ignored (as
    ``glossbench_alias_pool.read_exclude_papers``)."""
    return {x.split("#", 1)[0].strip() for x in path.read_text(encoding="utf-8").splitlines()} - {""}


# ------------------------------------------------------------------ verdict source

def refusal_reason(text: str | None) -> str:
    """Why the CLI verdict was replaced by Codex, from a verify row's ``refusal_text``: the
    CLI's safeguard fallback (``glossbench_r2_draft``: ``safety_stop_fallback: answered by
    <model> …``; #891), the API's safeguard error, or anything else."""
    t = str(text or "")
    if t.startswith("safety_stop_fallback:"):
        return "cli_safeguard_fallback"
    if "safeguards flagged" in t:
        return "safeguard_api_error"
    return "other"


def fallback_answered_by(text: str | None) -> str | None:
    t = str(text or "")
    if not t.startswith("safety_stop_fallback: answered by "):
        return None
    return t.split("answered by ", 1)[1].split(" ", 1)[0]


def verdict_sources(records: Sequence[Mapping[str, Any]], verify_rows: Sequence[Mapping[str, Any]]
                    ) -> dict[str, Any]:
    """§4.1 *verdict source*: counts of ``verifier_model`` and, for a Codex verdict, the refusal
    reason of the record's Codex verify row (the last row of a record wins)."""
    vrow = {str(v["record_id"]): v for v in verify_rows}
    models: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    answered_by: Counter[str] = Counter()
    for r in records:
        m = str(r.get("verifier_model"))
        models[m] += 1
        if str(r.get("verifier")) == "codex":
            v = vrow.get(str(r["record_id"])) or {}
            if str(v.get("verifier")) != "codex":
                reasons["no_codex_verify_row"] += 1
                continue
            reasons[refusal_reason(v.get("refusal_text"))] += 1
            if (ab := fallback_answered_by(v.get("refusal_text"))) is not None:
                answered_by[ab] += 1
    return {"records": len(records), "verifier_model": dict(sorted(models.items())),
            "codex_reason": dict(sorted(reasons.items())),
            "cli_safeguard_fallback_answered_by": dict(sorted(answered_by.items()))}


# ------------------------------------------------------------------ activation order

def activation_order(pool_rows: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    """The drafter's seeded activation order (``AliasRun.paper_states`` + ``wave_order``) from
    the pool file (header row first, then the ``job`` rows): the reachable papers, shuffled by
    ``random.Random('<pool seed>|waves')``."""
    import glossbench_alias_checks as ak
    import glossbench_alias_waves as wv

    header = pool_rows[0]
    goal = {k: int(v) for k, v in header["design"]["goal"].items()}
    states: dict[str, Any] = {}
    jobs = [r for r in pool_rows[1:] if r.get("kind") == "job"]
    for r in sorted(jobs, key=lambda x: (x["paper"], x["stratum"], x["rank"])):
        ps = states.setdefault(r["paper"], wv.PaperState(r["paper"]))
        if r["stratum"] == "G":
            ps.g.append((r["job_id"], ak.alias_key(r["alias"], r.get("alias_kind"))))
        else:
            ps.c.append(r["job_id"])
    ok = sorted(p for p, ps in states.items() if wv.reachable(ps, goal))
    return random.Random(f"{header['seed']}|waves").sample(ok, len(ok)), {"papers": len(states), "reachable": len(ok)}


def waves_at_extension(state: Mapping[str, Any], target_before: int = 200, target_after: int = 300
                       ) -> dict[str, Any]:
    """From the waves state: the activated count and accepted records when the target was raised
    from ``target_before`` to ``target_after`` (the reseal's time), and when the first run closed."""
    w = state["waves"]
    hist = sorted(w["history"], key=lambda h: int(h["wave"]))
    reseal = next((r for r in state.get("reseals") or []
                   if (r.get("diff") or {}).get("waves.target_papers") == {"new": target_after, "old": target_before}),
                  None)
    before = [h for h in hist if reseal is not None and str(h["t"]) < str(reseal["t"])]
    # the first run to close (the 100-paper target): the first segment that ended ``complete``,
    # a hard stop and its cleared resume being one run (prereg §4.3 item 2, "445 accepted")
    seg0 = next((s for s in state.get("segments") or [] if s.get("end") == "complete"), {})
    first = [h for h in hist if seg0.get("ended") and str(h["t"]) <= str(seg0["ended"])]
    return {"activated_final": int(w["n_active"]), "waves": int(w["wave"]), "stop": w.get("stop"),
            "activated_at_extension": int(before[-1]["papers_active"]) if before else None,
            "satisfied_at_extension": int(before[-1]["papers_satisfied"]) if before else None,
            "accepted_at_extension": sum(int(h["accepted"]) for h in before) if before else None,
            "accepted_first_segment_close": sum(int(h["accepted"]) for h in first) if first else None,
            "accepted_history_total": sum(int(h["accepted"]) for h in hist)}


# ------------------------------------------------------------------ overlaps

def paper_layout(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """``"<n>G+<m>C" -> papers`` over the records' papers."""
    per: dict[str, Counter[str]] = defaultdict(Counter)
    for r in records:
        per[str(r["paper"])][str(r["kind"])] += 1
    return dict(sorted(Counter(f"{c['G']}G+{c['C']}C" for c in per.values()).items()))


def validation_overlap(satisfied: set[str], balanced: Sequence[Mapping[str, Any]],
                       v_drafts: Sequence[Mapping[str, Any]], v_acceptance: Sequence[Mapping[str, Any]],
                       v_accepted: Sequence[Mapping[str, Any]], v_pairs: Sequence[Mapping[str, Any]]
                       ) -> dict[str, Any]:
    """§4.3 item 6: the judge-validation records and pairs against the satisfied papers. A pair's
    question paper is ``q_paper``, its passage paper ``c_paper``, its passage ``chunk_id``."""
    vpapers = {str(r["paper"]) for r in v_drafts}
    on = vpapers & satisfied
    bal_on = [r for r in balanced if str(r["paper"]) in on]
    gold = {str(r["gold_chunks"][0]) for r in balanced}
    gp = [p for p in v_pairs if str(p["chunk_id"]) in gold]
    q_in = [p for p in v_pairs if str(p["q_paper"]) in satisfied]
    return {
        "validation_draft_papers": len(vpapers),
        "validation_accepted_papers": len({str(r["paper"]) for r in v_accepted}),
        "satisfied_with_validation_drafts": len(on),
        "satisfied_with_validation_accepted": len({str(r["paper"]) for r in v_accepted} & satisfied),
        "drafts_on_those_papers": sum(str(r["paper"]) in on for r in v_drafts),
        "l1_passed_on_those_papers": sum(str(r["paper"]) in on and r.get("layer") == "l1" and r.get("verdict") == "pass"
                                         for r in v_acceptance),
        "accepted_on_those_papers": sum(str(r["paper"]) in on for r in v_accepted),
        "balanced_records_on_those_papers": len(bal_on),
        "balanced_records_on_those_papers_by_kind": dict(sorted(Counter(str(r["kind"]) for r in bal_on).items())),
        "pairs": len(v_pairs),
        "question_papers_in_satisfied": len({str(p["q_paper"]) for p in q_in}),
        "pairs_with_question_paper_in_satisfied": len(q_in),
        "pairs_with_balanced_gold_passage": len(gp),
        "distinct_balanced_gold_passages": len({str(p["chunk_id"]) for p in gp}),
        "pairs_with_balanced_gold_passage_and_question_paper_in_satisfied":
            sum(str(p["q_paper"]) in satisfied for p in gp),
        "pairs_with_passage_paper_in_satisfied": sum(str(p["c_paper"]) in satisfied for p in v_pairs),
        "pairs_touching_satisfied": sum(str(p["q_paper"]) in satisfied or str(p["c_paper"]) in satisfied
                                        for p in v_pairs),
    }


def extension_coverage(order: Sequence[str], n_before: int, n_after: int, satisfied: set[str],
                       accepted_papers: set[str], exclude: set[str]) -> dict[str, Any]:
    """§15 item 3: does the validation exclusion list cover the papers the extension activated
    (positions ``n_before`` .. ``n_after`` of the activation order)?"""
    first, ext = set(order[:n_before]), set(order[n_before:n_after])
    return {"activated_before_extension": len(first), "activated_before_extension_outside_exclusion": len(first - exclude),
            "activated_by_extension": len(ext), "activated_by_extension_in_exclusion": len(ext & exclude),
            "activated_by_extension_outside_exclusion": len(ext - exclude),
            "satisfied_activated_by_extension": len(satisfied & ext),
            "satisfied_activated_by_extension_outside_exclusion": len((satisfied & ext) - exclude),
            "satisfied_outside_activation": len(satisfied - set(order[:n_after])),
            "satisfied_outside_exclusion": len(satisfied - exclude),
            "accepted_papers_outside_exclusion": len(accepted_papers - exclude),
            "exclusion_covers_extension": not (ext - exclude)}


def calibration_overlap(cal_pairs: Sequence[Mapping[str, Any]], main_draft_ids: set[str],
                        by_id: Mapping[str, Mapping[str, Any]], balanced_ids: set[str],
                        satisfied: set[str], accepted_papers: set[str]) -> dict[str, Any]:
    """§4.3 item 5: calibration record ids that the main run also drafted, and the calibration
    question papers among the study's papers."""
    crec = {str(p["record_id"]) for p in cal_pairs}
    ov = crec & main_draft_ids
    acc = sorted(ov & set(by_id))
    bal = [i for i in acc if i in balanced_ids]
    cq = {str(p["q_paper"]) for p in cal_pairs}
    return {"record_ids": len(crec), "record_ids_in_main_drafts": len(ov), "record_ids_in_accepted": len(acc),
            "record_ids_in_accepted_by_kind": dict(sorted(Counter(str(by_id[i]["kind"]) for i in acc).items())),
            "record_ids_in_balanced": len(bal),
            "record_ids_in_balanced_by_kind": dict(sorted(Counter(str(by_id[i]["kind"]) for i in bal).items())),
            "question_papers": len(cq), "question_papers_in_accepted": len(cq & accepted_papers),
            "question_papers_in_satisfied": len(cq & satisfied)}


def pilot_overlap(p1: set[str], p2: set[str], satisfied: set[str], accepted_papers: set[str]) -> dict[str, Any]:
    return {"pilot1_draft_papers": len(p1), "pilot2_draft_papers": len(p2),
            "papers_with_accepted": len((p1 | p2) & accepted_papers), "satisfied_papers": len((p1 | p2) & satisfied),
            "pilot1_in_satisfied": len(p1 & satisfied), "pilot2_in_satisfied": len(p2 & satisfied),
            "pilot1_in_accepted": len(p1 & accepted_papers), "pilot2_in_accepted": len(p2 & accepted_papers)}


# ------------------------------------------------------------------ checks

def compare(found: Mapping[str, Any], prereg: Mapping[str, Any] = PREREG) -> dict[str, Any]:
    """Each prereg figure beside the recomputed one: ``match`` true / false, or ``not_checked``
    when the input was not given. A string prereg value shorter than 64 characters that looks
    like a sha prefix is compared as a prefix."""
    out: dict[str, Any] = {}
    for k, want in prereg.items():
        if k not in found or found[k] is None:
            out[k] = {"prereg": want, "recomputed": None, "status": "not_checked"}
            continue
        got = found[k]
        if isinstance(want, str) and isinstance(got, str) and len(want) == 8 and len(got) == 64:
            ok = got.startswith(want)
        else:
            ok = got == want
        out[k] = {"prereg": want, "recomputed": got, "status": "match" if ok else "MISMATCH"}
    return out


def public_copy(obj: Any, _in_prov: bool = False) -> Any:
    """``host`` and ``warnings`` dropped from every ``provenance`` mapping (as
    ``alias_study_stats.public_copy``)."""
    if isinstance(obj, Mapping):
        return {k: public_copy(v, k == "provenance") for k, v in obj.items()
                if not (_in_prov and k in ("host", "warnings"))}
    if isinstance(obj, list):
        return [public_copy(v) for v in obj]
    return obj


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance()
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ compute

def resolve(study_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for key, (rel, required) in LAYOUT.items():
        p = study_dir / rel
        if p.is_file():
            paths[key] = p
        elif required:
            raise SystemExit(f"recount: {rel} is missing under --study-dir")
    return paths


def compute(paths: Mapping[str, Path], chunk_export: Path | None = None) -> tuple[dict[str, Any], set[str], list[str]]:
    """The recount. Returns ``(result, forbidden ids, private texts)`` for the public check."""
    records = asc.load_records(paths["records"])
    ident = asc.verify_record_set(paths["records"], records)
    bal, desc = asc.split_records(records)
    by_id = {str(r["record_id"]): r for r in records}
    bal_ids = {str(r["record_id"]) for r in bal}
    satisfied = {str(r["paper"]) for r in bal}
    acc_papers = {str(r["paper"]) for r in records}
    summary = json.loads(paths["acceptance_summary"].read_text(encoding="utf-8"))
    verify = _jsonl(paths["verify_log"])
    acceptance = _jsonl(paths["acceptance_log"])
    drafts = _jsonl(paths["main_drafts"])
    calls = _jsonl(paths["main_draft_calls"])
    pool = _jsonl(paths["pool"])
    state = json.loads(paths["waves_state"].read_text(encoding="utf-8"))
    p1 = {str(r["paper"]) for r in _jsonl(paths["pilot1_drafts"]) if r.get("paper")}
    p2 = {str(r["paper"]) for r in _jsonl(paths["pilot2_drafts"]) if r.get("paper")}
    cal_pairs = _jsonl(paths["calib_pairs"])
    v_pairs = _jsonl(paths["validation_pairs"])
    v_drafts = _jsonl(paths["validation_drafts"])
    v_acc_log = _jsonl(paths["validation_acceptance"])
    v_accepted = _jsonl(paths["validation_accepted"])
    exclude = read_exclude(paths["validation_exclude"])

    def kinds(rs: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        return dict(sorted(Counter(str(r["kind"]) for r in rs).items()))

    order, order_info = activation_order(pool)
    waves = waves_at_extension(state)
    n_before = waves["activated_at_extension"]
    ext = extension_coverage(order, int(n_before or 0), waves["activated_final"], satisfied, acc_papers, exclude) \
        if n_before is not None else None
    vs_all, vs_bal = verdict_sources(records, verify), verdict_sources(bal, verify)
    l2 = [r for r in acceptance if r.get("layer") == "l2"]
    l2_counts = Counter((str(r.get("verifier")), str(r.get("verdict"))) for r in l2)
    codex_rows = [v for v in verify if str(v.get("verifier")) == "codex"]
    call_status = Counter(str(c.get("status")) for c in calls)
    alias_kind = Counter((str(r["alias_kind"]), str(r["kind"])) for r in bal)
    val = validation_overlap(satisfied, bal, v_drafts, v_acc_log, v_accepted, v_pairs)
    cal = calibration_overlap(cal_pairs, {str(r["record_id"]) for r in drafts}, by_id, bal_ids, satisfied, acc_papers)
    pil = pilot_overlap(p1, p2, satisfied, acc_papers)

    res: dict[str, Any] = {
        "record_set": {"identity": ident, "accepted_sha256_equals_summary": ident["accepted_sha256"] == summary.get("accepted_sha256"),
                       "accepted": {"records": len(records), "papers": len(acc_papers), "by_kind": kinds(records)},
                       "balanced": {"records": len(bal), "papers": len(satisfied), "by_kind": kinds(bal),
                                    "layout": paper_layout(bal),
                                    "alias_kind": {f"{a}.{k}": n for (a, k), n in sorted(alias_kind.items())}},
                       "held_out": {"records": len(desc), "papers": len({str(r["paper"]) for r in desc}),
                                    "by_kind": kinds(desc), "layout": paper_layout(desc)},
                       "gold_chunks_length_1": sum(len(r["gold_chunks"]) == 1 for r in records),
                       "pilot_false": sum(r.get("pilot") is False for r in records)},
        "verdict_source": {"all": vs_all, "balanced": vs_bal,
                           "rule": "verifier_model of the record; for a Codex verdict, refusal_reason() of the "
                                   "record's verify row's refusal_text (safety_stop_fallback: ... -> "
                                   "cli_safeguard_fallback; 'safeguards flagged' -> safeguard_api_error)"},
        "verifier": {"l2_verdicts": {f"{v}.{d}": n for (v, d), n in sorted(l2_counts.items())},
                     "verify_rows": len(verify), "codex_rows": len(codex_rows),
                     "codex_rows_by_reason": dict(sorted(Counter(refusal_reason(v.get("refusal_text"))
                                                                 for v in codex_rows).items())),
                     "prompt_versions_accepted": dict(Counter(str(r.get("verifier_prompt_version")) for r in records))},
        "drafter": {"prompt_versions_accepted": dict(Counter(str(r.get("prompt_version")) for r in records)),
                    "draft_rows": len(drafts), "served_models_draft_rows": dict(Counter(str(r.get("model")) for r in drafts)),
                    "gateway_calls": len(calls), "gateway_call_status": dict(sorted(call_status.items())),
                    "prompt_tokens": sum(int(c.get("pt") or 0) for c in calls),
                    "completion_tokens": sum(int(c.get("ct") or 0) for c in calls)},
        "pool": {"seed": pool[0].get("seed"), "sha256": asc.sha256_file(paths["pool"]),
                 "eval_papers": len(pool[0].get("eval_papers") or []),
                 "eval_papers_sha256": asc.sha_text("\n".join(pool[0].get("eval_papers") or [])),
                 "activation": order_info},
        "waves": waves,
        "pilot_overlap": pil,
        "calibration_overlap": cal,
        "validation_overlap": {**val, "exclude_papers": len(exclude)},
        "validation_exclusion_vs_extension": ext,
    }
    if "pool_manifest" in paths:
        res["pool"]["manifest_schema"] = json.loads(paths["pool_manifest"].read_text(encoding="utf-8")).get("schema")
    if "waves_totals" in paths:
        tot = json.loads(paths["waves_totals"].read_text(encoding="utf-8"))
        res["waves"]["stored_totals_since_start"] = (tot.get("totals") or {}).get("since_start")
        res["waves"]["stored_cumulative_with_prior"] = tot.get("cumulative_with_prior")
    res["waves"]["target"] = next((r["diff"]["waves.target_papers"]["new"] for r in reversed(state.get("reseals") or [])
                                   if "waves.target_papers" in (r.get("diff") or {})), None)
    glossary = {k: asc.sha256_file(paths[k]) for k in ("merged_glossary", "abbrev_entries") if k in paths}
    res["glossary"] = glossary
    corpus = None
    if chunk_export is not None:
        ch = asc.read_chunk_exports([chunk_export])
        corpus = {"sha256": asc.sha256_file(chunk_export), "chunks": len(ch),
                  "documents": len({c["doc_id"] for c in ch.values()}), "papers": len({c["paper"] for c in ch.values()})}
    res["corpus"] = corpus

    vsa, vsb = vs_all, vs_bal
    found: dict[str, Any] = {
        "accepted_sha256": ident["accepted_sha256"],
        "accepted_sha256_equals_summary": res["record_set"]["accepted_sha256_equals_summary"],
        "balanced_ids_sha256": ident["balanced_sha256"],
        "accepted.records": len(records), "accepted.G": kinds(records).get("G", 0),
        "accepted.C": kinds(records).get("C", 0), "accepted.papers": len(acc_papers),
        "balanced.papers": len(satisfied), "balanced.papers_exactly_2G_1C": paper_layout(bal).get("2G+1C", 0),
        "balanced.records": len(bal), "balanced.G": kinds(bal).get("G", 0), "balanced.C": kinds(bal).get("C", 0),
        "held_out.records": len(desc), "held_out.G": kinds(desc).get("G", 0), "held_out.C": kinds(desc).get("C", 0),
        "held_out.papers": res["record_set"]["held_out"]["papers"],
        "held_out.papers_1G_1C": paper_layout(desc).get("1G+1C", 0),
        "held_out.papers_1C_only": paper_layout(desc).get("0G+1C", 0),
        **{f"balanced.alias_kind.{a}.{k}": alias_kind.get((a, k), 0)
           for a in ("abbreviation", "symbol", "both") for k in ("G", "C")},
        "gold_chunks_length_1": res["record_set"]["gold_chunks_length_1"],
        "pilot_false": res["record_set"]["pilot_false"],
        "drafter.prompt_alias_draft_2": sum(r.get("prompt_version") == "alias-draft/2" for r in records),
        "drafter.served_gemini_25_pro_draft_rows": sum(r.get("model") == "Gemini 2.5 Pro" for r in drafts),
        "verifier.prompt_alias_verify_3": sum(r.get("verifier_prompt_version") == "alias-verify/3" for r in records),
        **{f"verifier.{v}.{d}": l2_counts.get((v, d), 0) for v in ("claude", "codex") for d in ("pass", "reject")},
        "verdict_source.all.claude-opus-5-5": vsa["verifier_model"].get("claude-opus-5-5", 0),
        "verdict_source.all.gpt-6-astra": vsa["verifier_model"].get("gpt-6-astra", 0),
        "verdict_source.all.codex_after_cli_safeguard_fallback": vsa["codex_reason"].get("cli_safeguard_fallback", 0),
        "verdict_source.all.codex_after_safeguard_api_error": vsa["codex_reason"].get("safeguard_api_error", 0),
        "verdict_source.balanced.claude-opus-5-5": vsb["verifier_model"].get("claude-opus-5-5", 0),
        "verdict_source.balanced.gpt-6-astra": vsb["verifier_model"].get("gpt-6-astra", 0),
        "verdict_source.balanced.codex_after_cli_safeguard_fallback": vsb["codex_reason"].get("cli_safeguard_fallback", 0),
        "verdict_source.balanced.codex_after_safeguard_api_error": vsb["codex_reason"].get("safeguard_api_error", 0),
        "verify_log.cli_refusals": len(codex_rows),
        "verify_log.cli_safeguard_fallbacks": res["verifier"]["codex_rows_by_reason"].get("cli_safeguard_fallback", 0),
        "verify_log.codex_calls": len(codex_rows),
        "gateway.calls": len(calls), "gateway.answered": call_status.get("ok", 0),
        "gateway.failures": len(calls) - call_status.get("ok", 0),
        "gateway.failures_http_502": call_status.get("error:http_502", 0),
        "gateway.failures_http_503": call_status.get("error:http_503", 0),
        "gateway.prompt_tokens": res["drafter"]["prompt_tokens"],
        "gateway.completion_tokens": res["drafter"]["completion_tokens"],
        "pool.seed": res["pool"]["seed"], "pool.sha256": res["pool"]["sha256"],
        "pool.eligible_papers": res["pool"]["eval_papers"], "pool.eval_papers_sha256": res["pool"]["eval_papers_sha256"],
        "pool.schema": res["pool"].get("manifest_schema"),
        "waves.activated": waves["activated_final"], "waves.waves": waves["waves"], "waves.target": res["waves"]["target"],
        "waves.stop": waves["stop"], "waves.accepted_first_segment_close": waves["accepted_first_segment_close"],
        "waves.accepted_at_200": waves["accepted_at_extension"], "waves.activated_at_200": waves["activated_at_extension"],
        "corpus.chunks": (corpus or {}).get("chunks"), "corpus.documents": (corpus or {}).get("documents"),
        "corpus.papers": (corpus or {}).get("papers"), "corpus.sha256": (corpus or {}).get("sha256"),
        "glossary.merged_sha256": glossary.get("merged_glossary"), "glossary.abbrev_sha256": glossary.get("abbrev_entries"),
        "pilot.papers_with_accepted": pil["papers_with_accepted"], "pilot.satisfied_papers": pil["satisfied_papers"],
        "calibration.record_ids_in_main_drafts": cal["record_ids_in_main_drafts"],
        "calibration.record_ids_in_accepted": cal["record_ids_in_accepted"],
        "calibration.record_ids_in_accepted.G": cal["record_ids_in_accepted_by_kind"].get("G", 0),
        "calibration.record_ids_in_accepted.C": cal["record_ids_in_accepted_by_kind"].get("C", 0),
        "calibration.record_ids_in_balanced": cal["record_ids_in_balanced"],
        "calibration.record_ids_in_balanced.C": cal["record_ids_in_balanced_by_kind"].get("C", 0),
        "validation.exclude_papers": len(exclude),
        "validation.satisfied_outside_exclusion": len(satisfied - exclude),
        "validation.satisfied_with_validation_drafts": val["satisfied_with_validation_drafts"],
        "validation.drafts_on_those_papers": val["drafts_on_those_papers"],
        "validation.l1_passed_on_those_papers": val["l1_passed_on_those_papers"],
        "validation.accepted_on_those_papers": val["accepted_on_those_papers"],
        "validation.balanced_records_on_those_papers": val["balanced_records_on_those_papers"],
        "validation.balanced_records_on_those_papers.G": val["balanced_records_on_those_papers_by_kind"].get("G", 0),
        "validation.balanced_records_on_those_papers.C": val["balanced_records_on_those_papers_by_kind"].get("C", 0),
        "validation.pairs": val["pairs"],
        "validation.question_papers_in_satisfied": val["question_papers_in_satisfied"],
        "validation.pairs_with_question_paper_in_satisfied": val["pairs_with_question_paper_in_satisfied"],
        "validation.pairs_with_balanced_gold_passage": val["pairs_with_balanced_gold_passage"],
        "validation.distinct_balanced_gold_passages": val["distinct_balanced_gold_passages"],
        "validation.pairs_with_balanced_gold_passage_and_question_paper_in_satisfied":
            val["pairs_with_balanced_gold_passage_and_question_paper_in_satisfied"],
        "validation.pairs_with_passage_paper_in_satisfied": val["pairs_with_passage_paper_in_satisfied"],
        "validation.pairs_touching_satisfied": val["pairs_touching_satisfied"],
        "validation.exclusion_covers_extension": (ext or {}).get("exclusion_covers_extension"),
    }
    res["checks"] = compare(found)
    st = Counter(v["status"] for v in res["checks"].values())
    res["checks_summary"] = dict(sorted(st.items()))
    forbidden = (set(by_id) | acc_papers | {str(r["gold_chunks"][0]) for r in records}
                 | {str(r["record_id"]) for r in drafts} | {str(r["paper"]) for r in drafts}
                 | {str(p[k]) for p in v_pairs + cal_pairs for k in ("pair_id", "record_id", "q_paper", "c_paper", "chunk_id")}
                 | {str(r["paper"]) for r in v_drafts} | exclude | p1 | p2)
    texts = [str(r["question"]) for r in records] + [str(p["question"]) for p in v_pairs]
    return res, forbidden, texts


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--study-dir", required=True, type=Path, help="the private alias-study directory (read only)")
    ap.add_argument("--chunk-export", type=Path, default=None, help="the OA-1000 chunk export (corpus counts, §4.1)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    paths = resolve(args.study_dir)
    res, forbidden, texts = compute(paths, args.chunk_export)
    inputs = {k: {"path": LAYOUT[k][0], "sha256": asc.sha256_file(p)} for k, p in sorted(paths.items())}
    if args.chunk_export is not None:
        inputs["chunk_export"] = {"path": args.chunk_export.name, "sha256": asc.sha256_file(args.chunk_export)}
    prov = provenance()
    out = public_copy({"schema": SCHEMA, **res, "inputs": inputs, "provenance": prov,
                       "citable": bool(prov.get("dirty") is False and prov.get("untracked") == 0)})
    bad = asc.public_problems(out, forbidden, texts)
    if bad:
        print(f"REFUSED: the output would carry {len(bad)} private string(s) ({sorted(set(bad))})", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    mism = {k: v for k, v in res["checks"].items() if v["status"] != "match"}
    print(json.dumps({"checks": res["checks_summary"], "not_matching": mism, "citable": out["citable"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
