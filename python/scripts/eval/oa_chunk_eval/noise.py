"""``noise``: the noise run of Addendum A.3, which sets the margins.

Subset: ``noise.n_questions`` (~300) questions chosen by a seeded hash rank from
``questions.jsonl`` (ambiguous-gold and within-record-repeat items excluded when
``audit.jsonl`` exists). Arms:
``noise.arms`` ([baseline, cheap arm]) or, by default, the baseline and the first cheap
arm in the config. Retrieve them first with ``retrieve --subset noise``.

Computed on primary hit@``decide.k``:

- split-half spread of the baseline: ``split_reps`` seeded random halvings of the subset;
  per halving the difference of the baseline hit rate between the halves (points);
  reported as SD and 2.5/97.5 percentiles;
- discordance d between the two arms (share of questions where exactly one hits);
- paired diff (arm - baseline) with the paired bootstrap 95% CI on the subset;
- CI half-width at the planned N: the subset's bootstrap half-width scaled by
  sqrt(n_subset / planned_n) (primary), and the normal approximation
  1.96 x SD(diff) / sqrt(planned_n) (reported);
- margins per Addendum A: M_ni = max(floor, half-width), M_mid = max(floor, half-width),
  M_high = 2 x M_mid, floor = 2.0 points;
- when ``noise.target_half_width`` H is set: the N at which the half-width is <= H.

Writes ``noise.json`` (aggregates only), with ``noise_seed``, ``n_questions`` and
``selected_ids_sha256`` (:func:`ids_sha256` of the selected ids) so ``decide`` can check that
``score`` excluded the same subset. The margins are NOT applied automatically: the
owner records them in the plan addendum and copies them into the config's ``margins``.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from typing import Any

import numpy as np

from .common import log, read_jsonl, seeded_rank, write_json
from .config import arms as cfg_arms
from .config import baseline_arm, out_path, require
from .stats import discordance, half_width_at, paired_diff, strip_private


def noise_arms(cfg: dict[str, Any]) -> list[str]:
    names = cfg["noise"].get("arms")
    if names:
        if len(names) != 2:
            raise ValueError("noise.arms must name exactly two arms (baseline first)")
        return list(names)
    base = baseline_arm(cfg)["name"]
    cheap = [a["name"] for a in cfg_arms(cfg) if a["cost_class"] == "cheap" and a["name"] != base]
    if not cheap:
        raise ValueError("noise needs a cheap arm besides the baseline (or noise.arms)")
    return [base, cheap[0]]


def ids_sha256(ids: Iterable[str]) -> str:
    """sha256 of the sorted question ids joined by newlines: ties a noise subset to its use."""
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def noise_questions(cfg: dict[str, Any], questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    apath = out_path(cfg, "audit.jsonl")
    if apath.exists():  # the same exclusion as score's clean set
        amb = {r["question_id"] for r in read_jsonl(apath)
               if r.get("ambiguous_gold") or r.get("within_record_repeat")}
        questions = [q for q in questions if q["question_id"] not in amb]
    seed = cfg["noise"]["seed"]
    ranked = sorted(questions, key=lambda q: seeded_rank(seed, "noise", q["question_id"]))
    return ranked[:int(cfg["noise"]["n_questions"])]


def split_half(values: np.ndarray, reps: int, seed: int) -> dict[str, Any]:
    """Seeded random halvings: hit(half A) - hit(half B), in points."""
    n = len(values)
    if n < 2:
        return {"reps": 0, "sd": None, "lo": None, "hi": None}
    rng = np.random.default_rng(seed)
    h = n // 2
    diffs = np.empty(reps)
    for i in range(reps):
        perm = rng.permutation(n)
        diffs[i] = (values[perm[:h]].mean() - values[perm[h:2 * h]].mean()) * 100.0
    return {"reps": reps, "half_size": h, "sd": float(diffs.std(ddof=1)), "mean": float(diffs.mean()),
            "lo": float(np.percentile(diffs, 2.5)), "hi": float(np.percentile(diffs, 97.5))}


def compute(base_vec: list[int], arm_vec: list[int], ncfg: dict[str, Any], iters: int, seed: int) -> dict[str, Any]:
    b = np.asarray(base_vec, dtype=float)
    a = np.asarray(arm_vec, dtype=float)
    n = len(b)
    planned = int(ncfg["planned_n"])
    floor = float(ncfg["floor"])
    pd_ = paired_diff(a, b, iters, seed)
    hw_sub = (pd_["hi"] - pd_["lo"]) / 2 if n else None
    hw_boot = hw_sub * math.sqrt(n / planned) if n and hw_sub is not None else None
    hw_norm = half_width_at((a - b) * 100.0, planned)
    hw = hw_boot if hw_boot is not None else hw_norm
    m_ni = max(floor, hw) if hw is not None else None
    m_mid = max(floor, hw) if hw is not None else None
    out = {
        "n_subset": n, "planned_n": planned,
        "baseline_hit": round(float(b.mean()) * 100, 4) if n else None,
        "arm_hit": round(float(a.mean()) * 100, 4) if n else None,
        "split_half_baseline": strip_private(split_half(b, int(ncfg["split_reps"]), int(ncfg["seed"]))),
        "discordance": discordance(a, b),
        "paired_diff": strip_private(pd_),
        "half_width_subset": None if hw_sub is None else round(hw_sub, 4),
        "half_width_at_planned_n": None if hw is None else round(hw, 4),
        "half_width_at_planned_n_normal": None if hw_norm is None else round(hw_norm, 4),
        "margins": None if m_ni is None else {"M_ni": round(m_ni, 4), "M_mid": round(m_mid, 4),
                                              "M_high": round(2 * m_mid, 4)},
        "floor": floor,
    }
    H = ncfg.get("target_half_width")
    if H and hw_sub:
        out["target_half_width"] = H
        out["n_for_target"] = int(math.ceil(n * (hw_sub / float(H)) ** 2))
    return out


def run(cfg: dict[str, Any]) -> int:
    require(cfg, "out_dir")
    from .score import build_rows
    names = noise_arms(cfg)
    qs = noise_questions(cfg, read_jsonl(out_path(cfg, "questions.jsonl")))
    rows, meta = build_rows(cfg, arm_names=names, questions=qs)
    if not rows:
        log("noise: no subset question has an ok retrieval in both arms (run retrieve --subset noise)")
        return 2
    k = str(int(cfg["decide"]["k"]))
    base, arm = names
    sc = cfg["score"]
    res = compute([r["arms"][base][k]["primary"] for r in rows], [r["arms"][arm][k]["primary"] for r in rows],
                  cfg["noise"], int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"]))
    res.update({"arms": names, "k": int(k), "metric": "primary", "n_selected": len(qs),
                "noise_seed": cfg["noise"]["seed"], "n_questions": int(cfg["noise"]["n_questions"]),
                "selected_ids_sha256": ids_sha256(q["question_id"] for q in qs),
                "missing_by_arm": meta["missing_by_arm"], "hit_quality": meta["hit_quality"],
                "refusals": meta["refusals"]})
    write_json(out_path(cfg, "noise.json"), res)
    if meta["refusals"]:  # margins measured on broken scoring must not be used
        for r in meta["refusals"]:
            log(f"noise: REFUSED: {r}")
        return 2
    log(f"noise: n={res['n_subset']} d={res['discordance']} half-width@{res['planned_n']}="
        f"{res['half_width_at_planned_n']} margins={res['margins']}")
    return 0
