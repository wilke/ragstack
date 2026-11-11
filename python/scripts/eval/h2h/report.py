"""``h2h_report.py report``: the §9.4 script of the adopted-spec head-to-head PREREG.

From the four settings' three-arm (decision) and five-arm (reporting) ``score`` outputs,
the decision config's ``decision.json`` and a private manifest of gate inputs it computes
G1, H1, H2 (D, D1, E; the ctx@5 rule; the stratum guard), H3, C1–C4 (Holm per setting),
the strata, the sensitivity set, the drift and chunk-id-overlap figures, the file-checkable
gates of §6, the cross-check against ``decision.json`` and both cost orders' decisions,
prints the §4.5 sentences and writes the public ``artifacts/h2h-report.json`` (aggregates
only: :func:`assert_public` refuses a question, quote, record id, host or path).

The manifest (private, never committed) names files by path::

    {"frozen_order": "B",
     "settings": {"a": {"decision_dir": ..., "reporting_dir": ...,
                        "frozen_request": {"rerank": true, "use_graph": false,
                                           "retrieval_mode": "hybrid", "top_k": 10}}, "d": ..., "b": ..., "c": ...},
     "arms": {"S": {"receipts": {"main-00": path, ..., "sentinel": path},
                    "stderr": {"main-00": path, ..., "sentinel": path},
                    "sentinel_logs_json": path | null, "qdrant_count": int, "es_count": int, "expect_build": "1"},
              "S-regex": {... "expect_build": "2"}},
     "reused_census_live": {"ft512o64": {"qdrant": int, "es": int}, "ft512o64s": ..., "sent512s": ...},
     "shard_files": {"main-00": path, ..., "sentinel": path},
     "reembed": {"cosines": path, "run_model_ids": {"embedding_model": ..., "reranker_model": ...}},
     "drift": {"ft512o64": {"old": path, "new": path}, ...}}      # optional

``frozen-inputs.json`` (public, beside the PREREG) supplies the pins the gates compare
against. Any gate input the manifest leaves out is reported as *absent* and the gate fails:
absent is not passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

_EVAL = Path(__file__).resolve().parents[1]
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from oa_chunk_eval.score import by_stratum, set_rows  # noqa: E402
from oa_chunk_eval.stats import discordance, p_greater, paired_diff  # noqa: E402

from h2h import gates as G  # noqa: E402
from h2h import rules as R  # noqa: E402

K = "5"
DECISION_ARMS = (R.BASE, R.INC, R.S)
REPORT_ARMS = (R.BASE, R.INC, R.S, R.SREGEX, R.SENT)
SETTINGS = ("a", "d", "b", "c")
STRATA_DIMS = ("journal_family", "length_band")
#: what a gate on each arm voids (§6)
VOIDS = {R.S: "decision", R.INC: "decision", R.BASE: "decision", R.SREGEX: "H3", R.SENT: "C1"}


class ReportError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- public-file guard

_PRIVATE_PATTERNS = [re.compile(p) for p in (
    r"https?://", r"(^|[\s\"'(])/(rag|home|scout|tmp|mnt|data)/", r"\bPMC\d{4,}", r"\b(sub|task|wf)_[0-9a-f]{8}",
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", r"\b\d{1,3}(\.\d{1,3}){3}\b",
    r"localhost", r"/[^\s'\"]*\.sif\b", r"[A-Za-z0-9_-]+\.(cels|anl|gov|edu|org|com)\b")]
_PRIVATE_KEYS = {"question", "question_id", "answer_quote", "quote", "record_path", "pmcid", "path", "paths",
                 "collection", "physical", "url", "host", "api_key", "token"}


def assert_public(obj: Any, where: str = "$") -> None:
    """Refuse to write a public file holding a question, quote, record id, host or path."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _PRIVATE_KEYS:
                raise ReportError(f"private key {k!r} at {where}")
            assert_public(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_public(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        for p in _PRIVATE_PATTERNS:
            if p.search(obj):
                raise ReportError(f"private-looking string at {where}: {obj[:60]!r}")
        if len(obj) > 1500:  # a §4.5 sentence with every gate listed is the longest legitimate string
            raise ReportError(f"over-long string at {where} ({len(obj)} chars): not an aggregate")


# ------------------------------------------------------------------- loading


def read_json(p: str | Path) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_jsonl(p: str | Path) -> list[dict[str, Any]]:
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_setting(d: str | Path, arms: tuple[str, ...], need_decision: bool) -> dict[str, Any]:
    d = Path(d)
    score = read_json(d / "score.json")
    rows = read_jsonl(d / "per_question.jsonl")
    missing = [a for a in arms if a not in (score.get("arms") or [])]
    if missing:
        raise ReportError(f"{d}: arms missing from score.json: {missing}")
    extra = [a for a in (score.get("arms") or []) if a not in arms]
    if extra:
        raise ReportError(f"{d}: unexpected arms in score.json: {extra}")
    out = {"score": score, "rows": rows, "decision": None}
    if need_decision:
        out["decision"] = read_json(d / "decision.json")
    return out


# ------------------------------------------------------------------- numbers


def _strip(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items() if not k.startswith("_")}


def vectors(rows: list[dict[str, Any]], arms: tuple[str, ...], metric: str = "primary", k: str = K) -> dict[str, np.ndarray]:
    return {a: np.array([r["arms"][a][k][metric] for r in rows], dtype=float) for a in arms}


def ctx_mean(rows: list[dict[str, Any]], arm: str, k: str = K) -> float | None:
    if not rows:
        return None
    return float(np.mean([r["arms"][arm][k]["ctx_tokens"] for r in rows]))


def strata_test(rows: list[dict[str, Any]], arm: str, base: str, iters: int, seed: int,
                dims: tuple[str, ...] = STRATA_DIMS, min_n: int = R.MIN_STRATUM_N) -> dict[str, Any]:
    """Per stratum of ``dims`` the paired difference arm − base with the two-sided
    (1 − 0.05/k) percentile bounds, k = strata with n ≥ min_n (the guard's own level), as
    ``decide.stratum_guard`` computes them; plus the 95 % bounds for reporting."""
    def fn(g):
        v = vectors(g, (arm, base))
        return paired_diff(v[arm], v[base], iters, seed)
    st = by_stratum(rows, list(dims), fn)
    tested, untested, all_rows = [], [], []
    for dim, vals in st.items():
        for val, d in vals.items():
            row = {"dim": dim, "value": val, "n": d["n"],
                   "point": None if d["point"] is None else round(d["point"], 4),
                   "lo95": None if d["lo"] is None else round(d["lo"], 4),
                   "hi95": None if d["hi"] is None else round(d["hi"], 4)}
            all_rows.append((row, d))
            if d["n"] is None or d["n"] < min_n or d["point"] is None:
                untested.append(row)
            else:
                tested.append((row, d))
    k = len(tested)
    alpha = R.STRATUM_ALPHA / k if k else R.STRATUM_ALPHA
    out_tested = []
    for row, d in tested:
        dist = d["_dist"]
        lo = float(np.percentile(dist, 100 * alpha / 2))
        hi = float(np.percentile(dist, 100 * (1 - alpha / 2)))
        out_tested.append({**row, "lo": round(lo, 4), "hi": round(hi, 4)})
    failed = R.stratum_failures(out_tested)
    return {"k": k, "level": round(1 - alpha, 6), "tested": out_tested, "not_tested": untested,
            "failed": failed}


def two_sided_p(dist: np.ndarray) -> float:
    return min(1.0, 2 * min(p_greater(dist, 0.0), p_greater(-dist, 0.0)))


def contrasts(vec: dict[str, np.ndarray], setting: str, iters: int, seed: int) -> dict[str, Any]:
    """C1–C4 with Holm across the setting's family (§4.4)."""
    out, pv = {}, {}
    for c, (x, y) in R.CONTRASTS.items():
        if x not in vec or y not in vec:
            out[c] = {"pair": f"{x} − {y}", "absent": True}
            continue
        d = paired_diff(vec[x], vec[y], iters, seed)
        pv[c] = two_sided_p(d["_dist"])
        out[c] = {"pair": f"{x} − {y}", **_strip(d), "p_two_sided": round(pv[c], 6),
                  "in_family": c in R.FAMILY[setting]}
    fam = R.holm_family(setting, {c: p for c, p in pv.items() if c in R.FAMILY[setting]})
    for c, h in fam.items():
        out[c].update(p_holm=h["p_holm"], holm_sig=h["holm_sig"])
    return out


def cross_check(dec: dict[str, Any], vec: dict[str, np.ndarray], D1: dict[str, Any], iters: int, seed: int) -> tuple[list[str], dict[str, Any]]:
    """§4.1 / §6: ``decision.json`` ``arms.ft512o64s.{point, lo, hi}`` equals D1 to 4 decimals;
    every step present in ``ladder.steps`` equals the script's recomputation of that pair.
    The harness's choice is reported, not compared."""
    bad, info = [], {"harness_winner": dec.get("winner"), "harness_pass": {a: r.get("pass") for a, r in (dec.get("arms") or {}).items()},
                     "ladder_steps": [], "d1_match": None}
    arm = (dec.get("arms") or {}).get(R.INC)
    if not arm:
        bad.append("cross-check: decision.json has no ft512o64s arm")
    else:
        want = {k: round(float(D1[k]), 4) for k in ("point", "lo", "hi")}
        got = {k: arm.get(k) for k in ("point", "lo", "hi")}
        info["d1_match"] = all(got[k] is not None and abs(got[k] - want[k]) < 5e-5 for k in want)
        info["d1_harness"] = got
        if not info["d1_match"]:
            bad.append(f"cross-check: decision.json ft512o64s {got} != D1 {want}")
    ladder = dec.get("ladder") or {}
    a2 = float(ladder.get("alpha_two_sided") or 0.05)
    for st in ladder.get("steps") or []:
        cand, inc = st.get("candidate"), st.get("incumbent")
        if cand not in vec or inc not in vec:
            bad.append(f"cross-check: ladder step {st.get('pair')} names an arm not in the three-arm rows")
            continue
        d = paired_diff(vec[cand], vec[inc], iters, seed, alpha=a2)
        mine = {k: round(float(d[k]), 4) for k in ("point", "lo", "hi")}
        theirs = {k: st.get(k) for k in ("point", "lo", "hi")}
        ok = all(theirs[k] is not None and abs(theirs[k] - mine[k]) < 5e-5 for k in mine)
        info["ladder_steps"].append({"pair": st.get("pair"), "harness": theirs, "recomputed": mine,
                                     "switched": st.get("switched"), "match": ok})
        if not ok:
            bad.append(f"cross-check: ladder step {st.get('pair')} harness {theirs} != recomputed {mine}")
    present = {st.get("pair") for st in ladder.get("steps") or []}
    info["steps_present"] = sorted(present)
    info["note"] = "a missing step is reported, not a gate; the harness's choice is reported, not compared"
    return bad, info


# ------------------------------------------------------------------- the file-checkable gates (manifest)


def _read_text(p: str | None) -> str | None:
    if not p:
        return None
    return Path(p).read_text(encoding="utf-8", errors="replace")


def arm_gates(name: str, spec: dict[str, Any] | None, frozen: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Receipts, worker stderr and store counts of one new arm."""
    if not spec:
        return [f"{name}: no receipts / stderr / counts in the manifest (absent)"], {"absent": True}
    bad: list[str] = []
    receipts = {label: read_json(p) for label, p in (spec.get("receipts") or {}).items()}
    sent = (frozen.get("sentinel") or {}).get("n_chunks")
    rb, census = G.check_arm_receipts(receipts, name, expect_build=spec.get("expect_build"),
                                      expect_sentinel_chunks=sent)
    bad += rb
    texts = {label: _read_text(p) for label, p in (spec.get("stderr") or {}).items()}
    for label in receipts:
        texts.setdefault(label, None)
    sb, stderr_info = G.check_stderr(texts, name)
    bad += sb
    logs_info = None
    if spec.get("sentinel_logs_json"):  # the logs-endpoint capture of the sentinel task (tail-limited)
        data = read_json(spec["sentinel_logs_json"])
        tail = (data.get("data") or data).get("stderr")
        lb, logs_info = G.check_stderr({"sentinel": tail}, name, tail_limited=True)
        bad += lb
        file_n = stderr_info["warning_lines"].get("sentinel")
        if file_n is not None and logs_info["warning_lines"].get("sentinel") != file_n:
            bad.append(f"{name}: sentinel warning lines differ between the staged file ({file_n}) and the logs "
                       f"endpoint ({logs_info['warning_lines'].get('sentinel')})")
    bad += G.check_store_counts(name, census.get("expected_store_count"), spec.get("qdrant_count"), spec.get("es_count"))
    info = {"census": census, "stderr": stderr_info, "sentinel_logs_endpoint": logs_info,
            "qdrant": spec.get("qdrant_count"), "es": spec.get("es_count"),
            "chunk_ids": [i for r in receipts.values() for i in (r.get("chunk_ids") or [])]}
    return bad, info


def manifest_gates(man: dict[str, Any], frozen: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Every gate that reads something other than the score outputs, grouped by what it voids."""
    voids: dict[str, list[str]] = defaultdict(list)
    info: dict[str, Any] = {}
    arms = man.get("arms") or {}
    per_arm: dict[str, dict[str, Any]] = {}
    for name in (R.S, R.SREGEX):
        bad, ai = arm_gates(name, arms.get(name), frozen)
        per_arm[name] = ai
        voids[VOIDS[name]] += bad
    if not per_arm[R.S].get("absent") and not per_arm[R.SREGEX].get("absent"):
        voids["H3"] += G.check_same_commit(per_arm[R.S]["census"], per_arm[R.SREGEX]["census"])
        info["chunk_id_overlap"] = G.chunk_id_overlap(per_arm[R.S]["chunk_ids"], per_arm[R.SREGEX]["chunk_ids"])
    info["arms"] = {n: {k: v for k, v in ai.items() if k != "chunk_ids"} for n, ai in per_arm.items()}
    # reused arms: live census == frozen census
    live = man.get("reused_census_live") or {}
    frozen_census = frozen.get("reused_census") or {}
    for name in (R.BASE, R.INC, R.SENT):
        voids[VOIDS[name]] += G.check_reused_census(name, frozen_census.get(name), live.get(name))
    info["reused_census"] = {"frozen": frozen_census, "live": live}
    # same input: shard hashes
    files = man.get("shard_files") or {}
    actual = {label: G.sha256_file(p) for label, p in files.items()}
    voids["decision"] += G.check_shard_hashes(frozen.get("shards") or {}, actual)
    info["shards_hashed"] = sorted(actual)
    # embedding drift
    re_ = man.get("reembed") or {}
    if re_.get("cosines"):
        cb, cinfo = G.check_reembed(read_json(re_["cosines"]), float((frozen.get("reembed") or {}).get("cosine_threshold", 0.999)),
                                    int((frozen.get("reembed") or {}).get("n", 200)))
        voids["decision"] += cb
        info["reembed"] = cinfo
    else:
        voids["decision"].append("re-embed cosines absent")
    voids["decision"] += G.check_model_ids(frozen.get("model_ids") or {}, re_.get("run_model_ids") or {})
    # drift (descriptive)
    drift = {}
    for arm, paths in (man.get("drift") or {}).items():
        drift[arm] = G.top5_drift(read_jsonl(paths["old"]), read_jsonl(paths["new"]))
    info["top5_drift"] = drift or None
    return dict(voids), info


# ------------------------------------------------------------------- per setting


def analyse_setting(setting: str, dec: dict[str, Any], rep: dict[str, Any], frozen: dict[str, Any],
                    frozen_request: dict[str, Any] | None, void_from_files: dict[str, list[str]],
                    is_decision_setting: bool, frozen_order: str) -> dict[str, Any]:
    score, rows = dec["score"], dec["rows"]
    iters, seed = int(score["bootstrap"]["iters"]), int(score["bootstrap"]["seed"])
    primary = score.get("primary_set")
    if primary != "clean_excl_noise":
        raise ReportError(f"({setting}) three-arm primary set is {primary!r}, not clean_excl_noise")
    rs = set_rows(rows, primary)
    sens_name = score.get("sensitivity_set") or "clean"
    rs_sens = set_rows(rows, sens_name)
    out: dict[str, Any] = {"setting": setting, "is_decision_setting": is_decision_setting,
                           "n_decision": len(rs), "n_sensitivity": len(rs_sens),
                           "bootstrap": {"iters": iters, "seed": seed}, "tokens": score.get("tokenizer"),
                           "tokens_estimated": score.get("tokens_estimated")}
    voids: dict[str, list[str]] = defaultdict(list)
    for k, v in void_from_files.items():
        voids[k] += v
    # sets (three-arm output)
    voids["decision"] += [f"({setting}) " + b for b in G.check_sets([r["question_id"] for r in rs], [r["question_id"] for r in rs_sens],
                                                                   frozen.get("sets") or {})]
    # retrieval, three-arm and five-arm
    n_all = int((frozen.get("sets") or {}).get("all_rows") or 2437)
    rb, rinfo = G.check_retrieval(score, DECISION_ARMS, n_all)
    voids["decision"] += [f"({setting}) " + b for b in rb]
    rb5, rinfo5 = G.check_retrieval(rep["score"], REPORT_ARMS, n_all)
    for b in rb5:
        arm = b.split(":")[0]
        voids[VOIDS.get(arm, "decision")].append(f"({setting}, five-arm) " + b)
    out["server_versions"] = {"three_arm": rinfo["server_versions"], "five_arm": rinfo5["server_versions"]}
    out["missing_by_arm"] = {"three_arm": rinfo["missing_by_arm"], "five_arm": rinfo5["missing_by_arm"]}
    sent_req = ((score.get("request") or {}) if isinstance(score.get("request"), dict) else None)
    if frozen_request is not None and sent_req is not None:
        voids["decision"] += [f"({setting}) " + b for b in G.check_request_fields(sent_req, frozen_request)]
    # the three contrasts on the three-arm decision rows
    vec = vectors(rs, DECISION_ARMS)
    D = paired_diff(vec[R.S], vec[R.INC], iters, seed)
    E = paired_diff(vec[R.INC], vec[R.S], iters, seed)
    D1 = paired_diff(vec[R.INC], vec[R.BASE], iters, seed)
    if D1["lo"] is None or D1["lo"] <= 0:
        voids["decision"].append(f"({setting}) G1: lower(ft512o64s − ft512o64) = {R._f(D1['lo'], 2, True)} ≤ 0")
    strata = strata_test(rs, R.S, R.INC, iters, seed)
    ctx = {a: ctx_mean(rs, a) for a in DECISION_ARMS}
    ctx_ratio = (ctx[R.S] / ctx[R.BASE]) if ctx[R.S] is not None and ctx[R.BASE] else None
    hit = {a: round(float(vec[a].mean() * 100), 4) for a in DECISION_ARMS}
    pi = discordance(vec[R.S], vec[R.INC])
    cb, cinfo = cross_check(dec["decision"], vec, D1, iters, seed)
    voids["decision"] += [f"({setting}) " + b for b in cb]
    out.update(hit5=hit, D=_strip(D), E=_strip(E), D1=_strip(D1), discordance_S_vs_ft512o64s=round(pi, 6) if pi is not None else None,
               ctx5={a: (round(c, 2) if c is not None else None) for a, c in ctx.items()},
               ctx_ratio_S_over_ft512o64=(round(ctx_ratio, 4) if ctx_ratio is not None else None),
               stratum_guard=strata, cross_check=cinfo)
    out["sequence"] = R.decide_both_orders(frozen_order, gates=voids["decision"], D=_strip(D), E=_strip(E), D1=_strip(D1),
                                           ctx_ratio=ctx_ratio, strata_failed=strata["failed"], k_strata=strata["k"],
                                           hit_S=hit[R.S], hit_inc=hit[R.INC], n=len(rs), pi=pi or 0.0, setting=setting)
    # sensitivity set, descriptive
    vs = vectors(rs_sens, DECISION_ARMS)
    out["sensitivity"] = {"set": sens_name, "n": len(rs_sens), "label": "SENSITIVITY: noise subset included; not the decision",
                          "D": _strip(paired_diff(vs[R.S], vs[R.INC], iters, seed)),
                          "D1": _strip(paired_diff(vs[R.INC], vs[R.BASE], iters, seed))}
    # five-arm reporting rows: H3, C1–C4, per-arm secondaries
    rows5 = rep["rows"]
    rs5 = set_rows(rows5, rep["score"].get("primary_set") or primary)
    out["n_five_arm"] = len(rs5)
    if len(rs5) != len(rs):
        out["five_arm_shortfall"] = len(rs) - len(rs5)
    vec5 = vectors(rs5, REPORT_ARMS)
    D3 = paired_diff(vec5[R.SREGEX], vec5[R.S], iters, seed)
    strata3 = strata_test(rs5, R.SREGEX, R.S, iters, seed)
    out["D3"] = _strip(D3)
    out["stratum_guard_H3"] = strata3
    out["H3"] = R.h3(gates=voids["H3"], D3=_strip(D3), strata_failed=strata3["failed"], k_strata=strata3["k"])
    out["contrasts"] = contrasts(vec5, setting, iters, seed)
    if voids["C1"]:
        out["contrasts"]["C1"]["voided_by"] = voids["C1"]
    out["descriptive"] = {"S_regex_minus_ft512o64s": _strip(paired_diff(vec5[R.SREGEX], vec5[R.INC], iters, seed)),
                          "hit5": {a: round(float(vec5[a].mean() * 100), 4) for a in REPORT_ARMS},
                          "ctx5": {a: (round(ctx_mean(rs5, a), 2) if rs5 else None) for a in REPORT_ARMS},
                          "discordance5": {f"{x}|{y}": round(discordance(vec5[x], vec5[y]) or 0.0, 6)
                                           for x, y in ((R.S, R.INC), (R.SREGEX, R.S), (R.S, R.SENT), (R.INC, R.BASE), (R.S, R.BASE))}}
    for k in ("1", "10"):
        vk = vectors(rs5, REPORT_ARMS, "primary", k)
        out["descriptive"][f"hit{k}"] = {a: round(float(vk[a].mean() * 100), 4) for a in REPORT_ARMS}
    vst = vectors(rs5, REPORT_ARMS, "strict")
    out["descriptive"]["strict5"] = {a: round(float(vst[a].mean() * 100), 4) for a in REPORT_ARMS}
    out["gates"] = {k: sorted(set(v)) for k, v in voids.items() if v}
    return out


# ------------------------------------------------------------------- driver


def check_prereg(path: str | None, sha: str | None) -> dict[str, Any]:
    if not path:
        return {"prereg_sha256": None, "checked": False}
    got = G.sha256_file(path)
    if sha and got != sha:
        raise ReportError(f"PREREG sha256 {got[:12]} != --prereg-sha256 {sha[:12]}: refusing (§9.2)")
    return {"prereg_sha256": got, "checked": bool(sha)}


def build_report(man: dict[str, Any], frozen: dict[str, Any], prereg: dict[str, Any]) -> dict[str, Any]:
    frozen_order = man.get("frozen_order") or frozen.get("cost_order") or "B"
    if frozen_order not in R.ORDERS:
        raise ReportError(f"frozen_order {frozen_order!r}")
    file_voids, file_info = manifest_gates(man, frozen)
    settings = man.get("settings") or {}
    loaded: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for s in SETTINGS:
        spec = settings.get(s)
        if not spec:
            continue
        loaded[s] = (load_setting(spec["decision_dir"], DECISION_ARMS, True),
                     load_setting(spec["reporting_dir"], REPORT_ARMS, False))
    if "a" not in loaded:
        raise ReportError("setting (a) is required (the decision)")
    versions = {f"{s}/3": d["score"].get("server_versions") or {} for s, (d, _) in loaded.items()}
    versions.update({f"{s}/5": r["score"].get("server_versions") or {} for s, (_, r) in loaded.items()})
    vb, one_version = G.check_one_version(versions)
    per: dict[str, Any] = {}
    for s, (d, r) in loaded.items():
        voids = {k: list(v) for k, v in file_voids.items()}
        voids.setdefault("decision", []).extend(vb)
        per[s] = analyse_setting(s, d, r, frozen, (settings[s] or {}).get("frozen_request"), voids, s == "a", frozen_order)
    a = per["a"]
    sentences = [a["sequence"]["sentence"], a["H3"]["sentence"]]
    disagreements = [s for s, p in per.items() if s != "a" and p["sequence"]["decision"] != a["sequence"]["decision"]]
    return {"schema": "adopted-spec-h2h-report/1", "prereg": prereg, "frozen_order": frozen_order,
            "server_version": one_version, "decision": a["sequence"]["decision"], "row": a["sequence"]["row"],
            "row_label": a["sequence"]["row_label"], "other_order": a["sequence"]["other_order"],
            "H3_any_worker": a["H3"]["any_worker"], "sentences": sentences,
            "secondary_settings_disagreeing_with_a": disagreements,
            "settings": per, "files": file_info}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="private JSON naming the inputs (see the module doc)")
    ap.add_argument("--frozen-inputs", required=True, help="the public frozen-inputs.json")
    ap.add_argument("--prereg", help="PREREG.md; with --prereg-sha256 the script refuses on a mismatch")
    ap.add_argument("--prereg-sha256")
    ap.add_argument("--out", required=True, help="public artifacts/h2h-report.json (aggregates only)")
    args = ap.parse_args(argv)
    prereg = check_prereg(args.prereg, args.prereg_sha256)
    man = read_json(args.manifest)
    frozen = read_json(args.frozen_inputs)
    rep = build_report(man, frozen, prereg)
    assert_public(rep)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for s in rep["sentences"]:
        print(s)
    log(f"h2h_report: wrote {args.out}; decision {rep['decision']} (row {rep['row']}); "
        f"under order {rep['other_order']['order']}: {rep['other_order']['decision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
