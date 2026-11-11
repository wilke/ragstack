"""``h2h_report.py report``: the §9.4 script of the adopted-spec head-to-head PREREG.

From the four settings' three-arm (decision) and five-arm (reporting) ``score`` outputs,
the decision config's ``decision.json`` and a private manifest of gate inputs it computes
G1, H1, H2 (D, D1, E; the ctx@5 rule; the stratum guard), H3, C1–C4 (Holm per setting),
the strata, the sensitivity set, the drift and chunk-id-overlap figures, the file-checkable
gates of §6, the cross-check against ``decision.json`` and both cost orders' decisions,
prints the §4.5 sentences and writes the public ``artifacts/h2h-report.json`` (aggregates
only: :func:`assert_public` refuses a question, quote, record id, host or path).

The manifest (private, never committed) names files by path::

    {"settings": {"a": {"decision_dir": ..., "reporting_dir": ..., "config": <the decision config.json>,
                        "reporting_config": <the reporting config.json>},
                  "d": ..., "b": ..., "c": ...},                  # all four settings required
     "arms": {"S": {"receipts": {"main-00": path, ..., "sentinel": path},
                    "stderr": {"main-00": path, ..., "sentinel": path},
                    "sentinel_logs_json": path, "qdrant_count": int, "es_count": int},
              "S-regex": {...}},                       # omit the arm when it was not run
     "reused_census_live": {"ft512o64": {"qdrant": int, "es": int}, "ft512o64s": ..., "sent512s": ...},
     "shard_files": {"main-00": path, ..., "sentinel": path},
     "reembed": {"cosines": path, "run_model_ids": {"embedding_model": ..., "reranker_model": ...}},
     "drift": {"ft512o64": {"old": path, "new": path}, ...}}      # optional

``frozen-inputs.json`` (public, beside the PREREG) supplies every pin the gates compare
against — the cost order, the images' build and commit, the sentinel chunk count, the shard
hashes, the question-set hashes, the reused-arm census, the model ids, the re-embed sample
hash and the per-setting request fields. Any gate input the manifest or the pins leave out
is reported as *absent* and the gate fails: absent is not passed. The harness's own
provenance (``ragstack.provenance.experiment_provenance`` on the checkout the report runs
from; the harness writes none itself) is embedded and gated on a clean tree.
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
OPTIONAL_ARMS = (R.SREGEX, R.SENT)
SETTINGS = ("a", "d", "b", "c")
STRATA_DIMS = ("journal_family", "length_band")
#: what a gate on each arm voids (§6)
VOIDS = {R.S: "decision", R.INC: "decision", R.BASE: "decision", R.SREGEX: "H3", R.SENT: "C1"}
#: the contrasts a voided arm takes with it (§6: a gate on sent512s voids C1; on S-regex, C3/H3)
CONTRAST_ARMS = {"C1": (R.S, R.SENT), "C2": (R.S, R.BASE), "C3": (R.SREGEX, R.S), "C4": (R.INC, R.BASE)}
PROVENANCE_KEYS = ("version", "commit", "dirty", "untracked", "citable", "source", "describe", "schema")


class ReportError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- public-file guard

_PATH = re.compile(r"(?<![\w.])/[\w.-]+/|(?<![\w.])~/")
_API_ROUTE = re.compile(r"^/(v1|api)/$")
_PRIVATE_PATTERNS = [re.compile(p) for p in (
    r"https?://", r"\bfile:", r"\bPMC\d{4,}", r"\b(sub|task|wf)_[0-9a-f]{8}",
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", r"\b\d{1,3}(\.\d{1,3}){3}\b",
    r"\blocalhost\b", r"\b[\w-]+:\d{2,5}\b", r"/[^\s'\"]*\.sif\b",
    r"\b[A-Za-z0-9_-]+\.(cels|anl|gov|edu|org|com|net|io|local|internal)\b")]
_PRIVATE_KEYS = {"question", "question_id", "answer_quote", "quote", "record_path", "pmcid", "path", "paths",
                 "collection", "physical", "url", "host", "api_key", "token", "record_id", "doc_id",
                 "source_path", "out_dir", "chunk_ids", "content", "secret", "password"}


def _scan(s: str, where: str) -> None:
    for p in _PRIVATE_PATTERNS:
        if p.search(s):
            raise ReportError(f"private-looking string at {where}: {s[:60]!r}")
    for m in _PATH.finditer(s):
        if not _API_ROUTE.match(m.group(0)):
            raise ReportError(f"path-like string at {where}: {s[:60]!r}")


def assert_public(obj: Any, where: str = "$") -> None:
    """Refuse to write a public file holding a question, quote, record id, host or path —
    in values and in keys, through dicts, lists and tuples."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _PRIVATE_KEYS:
                raise ReportError(f"private key {k!r} at {where}")
            _scan(str(k), f"{where}.{k}")
            assert_public(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_public(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        _scan(obj, where)
        if len(obj) > 1500:  # the §4.5 sentence is the longest legitimate string; gates are summarised in it
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


def load_setting(d: str | Path, required: tuple[str, ...], optional: tuple[str, ...], need_decision: bool) -> dict[str, Any]:
    """The score outputs of one ``out_dir``. ``required`` arms must be present; ``optional``
    arms (S-regex, sent512s) are recorded as present or missing; any other arm refuses."""
    d = Path(d)
    score = read_json(d / "score.json")
    rows = read_jsonl(d / "per_question.jsonl")
    present = list(score.get("arms") or [])
    missing = [a for a in required if a not in present]
    if missing:
        raise ReportError(f"{d.name}: arms missing from score.json: {missing}")
    extra = [a for a in present if a not in required + optional]
    if extra:
        raise ReportError(f"{d.name}: unexpected arms in score.json: {extra}")
    out = {"score": score, "rows": rows, "decision": None, "arms": tuple(a for a in required + optional if a in present),
           "arms_missing": [a for a in optional if a not in present]}
    if need_decision:
        out["decision"] = read_json(d / "decision.json")
    return out


# ------------------------------------------------------------------- numbers


def _public_iv(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items() if not k.startswith("_")}


def _raw_iv(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


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
    ``decide.stratum_guard`` computes them; the guard runs on the unrounded values, the
    report shows them rounded, with the 95 % bounds beside."""
    def fn(g):
        v = vectors(g, (arm, base))
        return paired_diff(v[arm], v[base], iters, seed)
    st = by_stratum(rows, list(dims), fn)
    tested, untested = [], []
    for dim, vals in st.items():
        for val, d in vals.items():
            row = {"dim": dim, "value": val, "n": d["n"], "point": d["point"], "lo95": d["lo"], "hi95": d["hi"]}
            if d["n"] is None or d["n"] < min_n or d["point"] is None:
                untested.append(_public_iv(row))
            else:
                tested.append((row, d))
    k = len(tested)
    alpha = R.STRATUM_ALPHA / k if k else R.STRATUM_ALPHA
    raw = []
    for row, d in tested:
        dist = d["_dist"]
        raw.append({**row, "lo": float(np.percentile(dist, 100 * alpha / 2)),
                    "hi": float(np.percentile(dist, 100 * (1 - alpha / 2)))})
    failed = R.stratum_failures(raw)
    return {"k": k, "level": round(1 - alpha, 6), "tested": [_public_iv(r) for r in raw], "not_tested": untested,
            "failed": failed, "_failed_raw": failed}


def two_sided_p(dist: np.ndarray) -> float:
    return min(1.0, 2 * min(p_greater(dist, 0.0), p_greater(-dist, 0.0)))


def contrasts(vec: dict[str, np.ndarray], setting: str, iters: int, seed: int,
              voided: dict[str, list[str]]) -> dict[str, Any]:
    """C1–C4 with Holm across the setting's family (§4.4). A contrast whose arm is missing is
    *absent*; one voided by a gate on its arm is computed, marked and taken out of the
    family."""
    out, pv = {}, {}
    for c, (x, y) in R.CONTRASTS.items():
        if x not in vec or y not in vec:
            out[c] = {"pair": f"{x} − {y}", "absent": True, "in_family": False}
            continue
        d = paired_diff(vec[x], vec[y], iters, seed)
        p = two_sided_p(d["_dist"])
        out[c] = {"pair": f"{x} − {y}", **_public_iv(d), "p_two_sided": round(p, 6), "in_family": False}
        if voided.get(c):
            out[c]["voided_by"] = sorted(set(voided[c]))
            continue
        if c in R.FAMILY[setting]:
            pv[c] = p
            out[c]["in_family"] = True
    for c, h in R.holm_family(setting, pv).items():
        out[c].update(p_holm=h["p_holm"], holm_sig=h["holm_sig"])
    return out


def cross_check(dec: dict[str, Any], vec: dict[str, np.ndarray], D1: dict[str, Any], iters: int, seed: int) -> tuple[list[str], dict[str, Any]]:
    """§4.1 / §6: ``decision.json`` ``arms.ft512o64s.{point, lo, hi}`` equals D1 to 4 decimals;
    every step present in ``ladder.steps`` equals the script's recomputation of that pair at
    the ladder's own level. The harness's choice is reported, not compared."""
    bad: list[str] = []
    info: dict[str, Any] = {"harness_winner": dec.get("winner"),
                            "harness_pass": {a: r.get("pass") for a, r in (dec.get("arms") or {}).items()},
                            "ladder_steps": [], "d1_match": None}
    arm = (dec.get("arms") or {}).get(R.INC)
    if not arm:
        bad.append("[cross_check] decision.json has no ft512o64s arm")
    else:
        want = {k: round(float(D1[k]), 4) for k in ("point", "lo", "hi")}
        got = {k: arm.get(k) for k in ("point", "lo", "hi")}
        info["d1_match"] = all(got[k] is not None and abs(got[k] - want[k]) < 5e-5 for k in want)
        info["d1_harness"] = got
        if not info["d1_match"]:
            bad.append(f"[cross_check] decision.json ft512o64s {got} != D1 {want}")
    ladder = dec.get("ladder") or {}
    a2 = ladder.get("alpha_two_sided")
    if a2 is None:
        bad.append("[cross_check] decision.json ladder has no alpha_two_sided (absent)")
        a2 = 0.05
    for st in ladder.get("steps") or []:
        cand, inc = st.get("candidate"), st.get("incumbent")
        if cand not in vec or inc not in vec:
            bad.append(f"[cross_check] ladder step {st.get('pair')} names an arm not in the three-arm rows")
            continue
        d = paired_diff(vec[cand], vec[inc], iters, seed, alpha=float(a2))
        mine = {k: round(float(d[k]), 4) for k in ("point", "lo", "hi")}
        theirs = {k: st.get(k) for k in ("point", "lo", "hi")}
        ok = all(theirs[k] is not None and abs(theirs[k] - mine[k]) < 5e-5 for k in mine)
        info["ladder_steps"].append({"pair": st.get("pair"), "harness": theirs, "recomputed": mine,
                                     "switched": st.get("switched"), "match": ok})
        if not ok:
            bad.append(f"[cross_check] ladder step {st.get('pair')} harness {theirs} != recomputed {mine}")
    info["steps_present"] = sorted(st.get("pair") for st in ladder.get("steps") or [])
    info["note"] = "a missing step is reported, not a gate; the harness's choice is reported, not compared"
    return bad, info


# ------------------------------------------------------------------- the file-checkable gates (manifest)


def _read_text(p: str | None) -> str | None:
    if not p:
        return None
    return Path(p).read_text(encoding="utf-8", errors="replace")


def arm_gates(name: str, spec: dict[str, Any] | None, frozen: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Receipts, worker stderr (staged file for every shard; the logs endpoint for the
    sentinel, which must agree) and store counts of one new arm. ``spec`` None = the arm
    was not run."""
    if not spec:
        return [f"[arm_missing] {name}: not run (no receipts, stderr or counts in the manifest)"], {"absent": True}
    bad: list[str] = []
    image = (frozen.get("images") or {}).get(name) or {}
    receipts = {label: read_json(p) for label, p in (spec.get("receipts") or {}).items()}
    rb, census = G.check_arm_receipts(receipts, name, expect_build=image.get("build"), expect_commit=image.get("commit"),
                                      expect_sentinel_chunks=(frozen.get("sentinel") or {}).get("n_chunks"))
    bad += rb
    texts = {label: _read_text(p) for label, p in (spec.get("stderr") or {}).items()}
    for label in receipts:
        texts.setdefault(label, None)
    sb, stderr_info = G.check_stderr(texts, name)
    bad += sb
    logs_info = None
    if spec.get("sentinel_logs_json"):
        data = read_json(spec["sentinel_logs_json"])
        tail = (data.get("data") or data).get("stderr")
        lb, logs_info = G.check_stderr({"sentinel": tail}, name, tail_limited=True)
        bad += lb
        file_n = stderr_info["warning_lines"].get("sentinel")
        if file_n is None or logs_info["warning_lines"].get("sentinel") != file_n:
            bad.append(f"[stderr_agree] {name}: sentinel warning lines differ between the staged file ({file_n}) and "
                       f"the logs endpoint ({logs_info['warning_lines'].get('sentinel')})")
    else:
        bad.append(f"[stderr_agree] {name}: no logs-endpoint capture of the sentinel task (absent)")
    bad += G.check_store_counts(name, census.get("expected_store_count"), spec.get("qdrant_count"), spec.get("es_count"))
    info = {"census": census, "stderr": stderr_info, "sentinel_logs_endpoint": logs_info,
            "qdrant": spec.get("qdrant_count"), "es": spec.get("es_count"),
            "_chunk_ids": [i for r in receipts.values() for i in (r.get("chunk_ids") or [])]}
    return bad, info


ALL_ARMS = DECISION_ARMS + OPTIONAL_ARMS


def manifest_gates(man: dict[str, Any], frozen: dict[str, Any], provenance: dict[str, Any] | None) -> tuple[dict[str, list[str]], dict[str, Any], dict[str, list[str]]]:
    """Every gate that reads something other than the score outputs, grouped by what it voids
    (``decision`` / ``H3`` / ``C1``) and, for the per-setting contrasts of §6, by the arm it
    names (a run-level gate — shards, re-embed, model ids, provenance — names every arm)."""
    voids: dict[str, list[str]] = defaultdict(list)
    by_arm: dict[str, list[str]] = defaultdict(list)
    info: dict[str, Any] = {}
    arms = man.get("arms") or {}
    per_arm: dict[str, dict[str, Any]] = {}
    for name in (R.S, R.SREGEX):
        bad, ai = arm_gates(name, arms.get(name), frozen)
        per_arm[name] = ai
        voids[VOIDS[name]] += bad
        by_arm[name] += bad
    if not per_arm[R.S].get("absent") and not per_arm[R.SREGEX].get("absent"):
        voids["H3"] += G.check_same_commit(per_arm[R.S]["census"], per_arm[R.SREGEX]["census"])
        info["chunk_id_overlap"] = G.chunk_id_overlap(per_arm[R.S]["_chunk_ids"], per_arm[R.SREGEX]["_chunk_ids"])
    else:
        info["chunk_id_overlap"] = None
    info["arms"] = {n: {k: v for k, v in ai.items() if not k.startswith("_")} for n, ai in per_arm.items()}
    live = man.get("reused_census_live") or {}
    frozen_census = frozen.get("reused_census") or {}
    for name in (R.BASE, R.INC, R.SENT):
        cb_ = G.check_reused_census(name, frozen_census.get(name), live.get(name))
        voids[VOIDS[name]] += cb_
        by_arm[name] += cb_
    info["reused_census"] = {"frozen": {a: frozen_census.get(a) for a in (R.BASE, R.INC, R.SENT)}, "live": live}
    files = man.get("shard_files") or {}
    actual = {label: G.sha256_file(p) for label, p in files.items()}
    voids["decision"] += G.check_shard_hashes(frozen.get("shards") or {}, actual)
    info["shards_hashed"] = sorted(actual)
    re_ = man.get("reembed") or {}
    fr = frozen.get("reembed") or {}
    if re_.get("cosines"):
        cb, cinfo = G.check_reembed(read_json(re_["cosines"]), float(fr.get("cosine_threshold", 0.999)),
                                    int(fr.get("n", 200)), fr.get("ids_sha256"))
        voids["decision"] += cb
        info["reembed"] = cinfo
    else:
        voids["decision"].append("[reembed] re-embed cosines absent")
    voids["decision"] += G.check_model_ids(frozen.get("model_ids") or {}, re_.get("run_model_ids") or {})
    voids["decision"] += G.check_provenance(provenance)
    info["harness_provenance"] = provenance
    drift = {}
    for arm, paths in (man.get("drift") or {}).items():
        drift[arm] = G.top5_drift(read_jsonl(paths["old"]), read_jsonl(paths["new"]))
    info["top5_drift"] = drift or None
    run_level = [g for g in voids["decision"] if g not in by_arm[R.S] and g not in by_arm[R.INC] and g not in by_arm[R.BASE]]
    for a in ALL_ARMS:
        by_arm[a] += run_level
    return dict(voids), info, dict(by_arm)


def config_pin_gates(setting: str, spec: dict[str, Any], frozen: dict[str, Any]) -> list[str]:
    """§9.1: the decision and reporting configs the run used are the pinned ones (sha256)."""
    pins = (frozen.get("configs") or {}).get(setting) or {}
    bad = []
    for kind, key in (("decision", "config"), ("reporting", "reporting_config")):
        path = spec.get(key)
        if not path:
            bad.append(f"[config_pin] ({setting}) no {kind} config in the manifest (absent)")
            continue
        want = pins.get(kind)
        if not want:
            bad.append(f"[config_pin] ({setting}) no pinned sha256 for the {kind} config (absent)")
            continue
        got = G.sha256_file(path)
        if got != want:
            bad.append(f"[config_pin] ({setting}) {kind} config sha256 {got[:12]} != pinned {want[:12]}")
    return bad


def out_inside_repo(out: str | Path, repo: str | Path) -> bool:
    """True when ``--out`` lies under the harness checkout: a rerun would then see the previous
    output as untracked (or the tree as dirty) and the provenance gate would fail by design."""
    try:
        Path(out).resolve().relative_to(Path(repo).resolve())
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------- per setting


def _tag(setting: str, msgs: list[str]) -> list[str]:
    """Insert the setting after the gate code: ``[sets] (a) …``."""
    out = []
    for m in msgs:
        head, _, rest = m.partition("] ")
        out.append(f"{head}] ({setting}) {rest}" if rest else f"({setting}) {m}")
    return out


def analyse_setting(setting: str, dec: dict[str, Any], rep: dict[str, Any], frozen: dict[str, Any],
                    config_api: dict[str, Any] | None, void_from_files: dict[str, list[str]],
                    frozen_order: str, overlap_share: float | None,
                    arm_voids_files: dict[str, list[str]] | None = None) -> dict[str, Any]:
    is_a = setting == "a"
    score, rows = dec["score"], dec["rows"]
    iters, seed = int(score["bootstrap"]["iters"]), int(score["bootstrap"]["seed"])
    primary = score.get("primary_set")
    if primary != "clean_excl_noise":
        raise ReportError(f"({setting}) three-arm primary set is {primary!r}, not clean_excl_noise")
    rs = set_rows(rows, primary)
    sens_name = score.get("sensitivity_set") or "clean"
    rs_sens = set_rows(rows, sens_name)
    out: dict[str, Any] = {"setting": setting, "is_decision_setting": is_a, "n_decision": len(rs),
                           "n_sensitivity": len(rs_sens), "bootstrap": {"iters": iters, "seed": seed},
                           "tokens": score.get("tokenizer"), "tokens_estimated": score.get("tokens_estimated"),
                           "arms_missing": sorted(set(rep["arms_missing"]))}
    voids: dict[str, list[str]] = defaultdict(list)
    for k, v in void_from_files.items():
        voids[k] += v
    by_arm: dict[str, list[str]] = defaultdict(list)
    for a, v in (arm_voids_files or {}).items():
        by_arm[a] += v

    def decision_level(msgs: list[str]) -> None:  # a gate on the decision names its three arms
        voids["decision"] += msgs
        for a in DECISION_ARMS:
            by_arm[a] += msgs

    for a in rep["arms_missing"]:
        msg = f"[arm_missing] ({setting}) {a}: not in the five-arm score output"
        voids[VOIDS[a]].append(msg)
        by_arm[a].append(msg)
    n_all = int((frozen.get("sets") or {}).get("all_rows") or 0)
    if not n_all:
        decision_level([f"[rows] ({setting}) frozen-inputs has no sets.all_rows (absent)"])
    decision_level(_tag(setting, G.check_sets([r["question_id"] for r in rs], [r["question_id"] for r in rs_sens],
                                              frozen.get("sets") or {})))
    for tag, sc, arms_ in ((setting, score, DECISION_ARMS), (f"{setting}, five-arm", rep["score"], rep["arms"])):
        rb, rinfo_ = G.check_retrieval(sc, arms_, n_all)
        for arm, msg in rb:
            tagged = _tag(tag, [msg])
            if arm is None:  # a run-level failure of this setting: every arm of this setting
                voids["decision"] += tagged
                for a in arms_:
                    by_arm[a] += tagged
            else:
                voids[VOIDS[arm]] += tagged
                by_arm[arm] += tagged
        if arms_ is DECISION_ARMS:
            rinfo = rinfo_
        else:
            rinfo5 = rinfo_
    out["server_versions"] = {"three_arm": rinfo["server_versions"], "five_arm": rinfo5["server_versions"]}
    out["missing_by_arm"] = {"three_arm": rinfo["missing_by_arm"], "five_arm": rinfo5["missing_by_arm"]}
    decision_level(_tag(setting, G.check_request_fields(config_api, (frozen.get("frozen_request") or {}).get(setting))))
    decision_level(_tag(setting, G.check_decision_integrity(dec["decision"], frozen_order, len(rs))))
    # the three contrasts on the three-arm decision rows (unrounded for the rule; rounded for the record)
    vec = vectors(rs, DECISION_ARMS)
    D = paired_diff(vec[R.S], vec[R.INC], iters, seed)
    E = paired_diff(vec[R.INC], vec[R.S], iters, seed)
    D1 = paired_diff(vec[R.INC], vec[R.BASE], iters, seed)
    out["G1"] = None
    if is_a:
        out["G1"] = R.g1_holds(D1)
        if not out["G1"]:
            decision_level([f"[G1] ({setting}) lower(ft512o64s − ft512o64) = {R._f(D1['lo'], 2, True)} ≤ 0"])
    strata = strata_test(rs, R.S, R.INC, iters, seed)
    ctx = {a: ctx_mean(rs, a) for a in DECISION_ARMS}
    ctx_ratio = (ctx[R.S] / ctx[R.BASE]) if ctx[R.S] is not None and ctx[R.BASE] else None
    hit = {a: float(vec[a].mean() * 100) for a in DECISION_ARMS}
    pi = discordance(vec[R.S], vec[R.INC])
    cb, cinfo = cross_check(dec["decision"], vec, D1, iters, seed)
    decision_level(_tag(setting, cb))
    out.update(hit5={a: round(h, 4) for a, h in hit.items()}, D=_public_iv(D), E=_public_iv(E), D1=_public_iv(D1),
               discordance_S_vs_ft512o64s=round(pi, 6) if pi is not None else None,
               ctx5={a: (round(c, 2) if c is not None else None) for a, c in ctx.items()},
               ctx_ratio_S_over_ft512o64=(round(ctx_ratio, 4) if ctx_ratio is not None else None),
               stratum_guard={k: v for k, v in strata.items() if not k.startswith("_")}, cross_check=cinfo)
    seq = R.decide_both_orders(frozen_order, gates=voids["decision"], D=_raw_iv(D), E=_raw_iv(E), D1=_raw_iv(D1),
                               ctx_ratio=ctx_ratio, strata_failed=strata["_failed_raw"], k_strata=strata["k"],
                               hit_S=hit[R.S], hit_inc=hit[R.INC], n=len(rs), pi=pi or 0.0, setting=setting)
    seq["result"] = {k: (_public_iv(v) if isinstance(v, dict) and "point" in v else v) for k, v in seq["result"].items()}
    seq["result"]["strata_failed"] = [_public_iv(s) for s in seq["result"]["strata_failed"]]
    out["sequence"] = seq
    vs = vectors(rs_sens, DECISION_ARMS)
    out["sensitivity"] = {"set": sens_name, "n": len(rs_sens), "label": "SENSITIVITY: noise subset included; not the decision",
                          "D": _public_iv(paired_diff(vs[R.S], vs[R.INC], iters, seed)),
                          "D1": _public_iv(paired_diff(vs[R.INC], vs[R.BASE], iters, seed))}
    # the reporting (five-arm) rows: H3, C1–C4, per-arm secondaries
    rows5 = rep["rows"]
    rs5 = set_rows(rows5, rep["score"].get("primary_set") or primary)
    out["n_five_arm"] = len(rs5)
    if len(rs5) != len(rs):
        out["five_arm_shortfall"] = len(rs) - len(rs5)
    arms5 = rep["arms"]
    vec5 = vectors(rs5, arms5)
    if R.SREGEX in vec5:
        D3 = paired_diff(vec5[R.SREGEX], vec5[R.S], iters, seed)
        strata3 = strata_test(rs5, R.SREGEX, R.S, iters, seed)
        out["D3"] = _public_iv(D3)
        out["stratum_guard_H3"] = {k: v for k, v in strata3.items() if not k.startswith("_")}
        if is_a:
            h3 = R.h3(gates=voids["H3"], D3=_raw_iv(D3), strata_failed=strata3["_failed_raw"], k_strata=strata3["k"],
                      overlap_share=overlap_share)
            h3["strata_failed"] = [_public_iv(s) for s in h3.get("strata_failed") or []]
            out["H3"] = h3
    else:
        out["D3"] = None
        if is_a:
            out["H3"] = R.h3(gates=voids["H3"], D3=None, strata_failed=[], k_strata=0, overlap_share=overlap_share)
    # §6: a gate on an arm voids every contrast of this setting that uses the arm
    voided_contrasts = {c: sorted(set(by_arm[x] + by_arm[y])) for c, (x, y) in CONTRAST_ARMS.items()
                        if by_arm.get(x) or by_arm.get(y)}
    out["contrasts"] = contrasts(vec5, setting, iters, seed, voided_contrasts)
    out["arm_gates"] = {a: sorted(set(v)) for a, v in by_arm.items() if v}
    desc: dict[str, Any] = {"hit5": {a: round(float(vec5[a].mean() * 100), 4) for a in arms5},
                            "ctx5": {a: (round(ctx_mean(rs5, a), 2) if rs5 else None) for a in arms5},
                            "discordance5": {f"{x}|{y}": round(discordance(vec5[x], vec5[y]) or 0.0, 6)
                                             for x, y in ((R.S, R.INC), (R.SREGEX, R.S), (R.S, R.SENT), (R.INC, R.BASE), (R.S, R.BASE))
                                             if x in vec5 and y in vec5}}
    if R.SREGEX in vec5:
        desc["S_regex_minus_ft512o64s"] = _public_iv(paired_diff(vec5[R.SREGEX], vec5[R.INC], iters, seed))
    for k in ("1", "10"):
        vk = vectors(rs5, arms5, "primary", k)
        desc[f"hit{k}"] = {a: round(float(vk[a].mean() * 100), 4) for a in arms5}
    vst = vectors(rs5, arms5, "strict")
    desc["strict5"] = {a: round(float(vst[a].mean() * 100), 4) for a in arms5}
    out["descriptive"] = desc
    out["gates"] = {k: sorted(set(v)) for k, v in voids.items() if v}
    out["gate_counts"] = {k: G.gate_counts(v) for k, v in out["gates"].items()}
    return out


# ------------------------------------------------------------------- driver


def check_prereg(path: str, sha: str) -> dict[str, Any]:
    got = G.sha256_file(path)
    if got != sha:
        raise ReportError(f"PREREG sha256 {got[:12]} != --prereg-sha256 {sha[:12]}: refusing (§9.2)")
    return {"prereg_sha256": got, "checked": True}


def harness_provenance(repo: str | Path | None = None) -> dict[str, Any]:
    """``experiment_provenance`` of the checkout the report runs from, reduced to the keys a
    public file may carry."""
    from ragstack.provenance import experiment_provenance

    rec = experiment_provenance(repo=repo or _EVAL.parents[2])
    return {k: rec.get(k) for k in PROVENANCE_KEYS}


def build_report(man: dict[str, Any], frozen: dict[str, Any], prereg: dict[str, Any],
                 provenance: dict[str, Any] | None) -> dict[str, Any]:
    frozen_order = frozen.get("cost_order")
    if frozen_order not in R.ORDERS:
        raise ReportError(f"frozen-inputs cost_order {frozen_order!r} is not A or B: refusing")
    if man.get("frozen_order") not in (None, frozen_order):
        raise ReportError(f"manifest frozen_order {man.get('frozen_order')!r} disagrees with the pinned {frozen_order!r}")
    file_voids, file_info, arm_voids = manifest_gates(man, frozen, provenance)
    settings = man.get("settings") or {}
    loaded: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = {}
    pin_voids: dict[str, list[str]] = {}
    for s in SETTINGS:
        spec = settings.get(s)
        if not spec:  # §6: one git_sha across ALL FOUR settings cannot be shown over fewer
            file_voids.setdefault("decision", []).append(f"[settings_missing] ({s}) setting not in the manifest")
            continue
        pin_voids[s] = config_pin_gates(s, spec, frozen)
        api = read_json(spec["config"]).get("api") if spec.get("config") else None
        loaded[s] = (load_setting(spec["decision_dir"], DECISION_ARMS, (), True),
                     load_setting(spec["reporting_dir"], DECISION_ARMS, OPTIONAL_ARMS, False), api)
    if "a" not in loaded:
        raise ReportError("setting (a) is required (the decision)")
    versions = {f"{s}/3": d["score"].get("server_versions") or {} for s, (d, _, _) in loaded.items()}
    versions.update({f"{s}/5": r["score"].get("server_versions") or {} for s, (_, r, _) in loaded.items()})
    vb, one_version = G.check_one_version(versions)
    overlap = (file_info.get("chunk_id_overlap") or {}).get("share_of_S")
    per: dict[str, Any] = {}
    for s, (d, r, api) in loaded.items():
        voids = {k: list(v) for k, v in file_voids.items()}
        voids.setdefault("decision", []).extend(vb + pin_voids[s])
        by_arm = {a: list(v) for a, v in arm_voids.items()}
        for a in DECISION_ARMS + OPTIONAL_ARMS:  # run-level: every arm
            by_arm.setdefault(a, []).extend(vb + pin_voids[s] + [g for g in file_voids.get("decision", []) if g.startswith("[settings_missing]")])
        per[s] = analyse_setting(s, d, r, frozen, api, voids, frozen_order, overlap, by_arm)
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
    ap.add_argument("--prereg", required=True, help="PREREG.md (§9.2: the script refuses on a sha256 mismatch)")
    ap.add_argument("--prereg-sha256", required=True)
    ap.add_argument("--repo", help="the harness checkout to record provenance for (default: this file's repo)")
    ap.add_argument("--out", required=True, help="public artifacts/h2h-report.json (aggregates only)")
    args = ap.parse_args(argv)
    prereg = check_prereg(args.prereg, args.prereg_sha256)
    man = read_json(args.manifest)
    frozen = read_json(args.frozen_inputs)
    repo = args.repo or _EVAL.parents[2]
    if out_inside_repo(args.out, repo):
        log(f"h2h_report: WARNING: --out {args.out} is inside the harness checkout; a rerun with it uncommitted will "
            "fail the provenance gate (write outside the tree and copy in, README)")
    rep = build_report(man, frozen, prereg, harness_provenance(repo))
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
