#!/usr/bin/env python
"""Alias study (OA-1000): H1b on found@10-gold, a display (DEVIATIONS D-2).

    alias_study_h1b_gold.py --config CFG --out private.json --public public.json [--reference results-B.public.json]

**Why.** Prereg §6.1 and §7 print found@10-gold beside every found@10 figure. The statistics
script (``alias_study_stats.py``) prints a gold-only net beside H1 and H2 but none beside H1b.
This script computes it afterwards. **It is a display only and decides nothing**: no verdict,
gate or family procedure reads it, and the stage-B results stay the reported ones (D-1's
binding rule; D-2).

**What.** H1b's own estimator, on the same records and cells, with the gold-only hit vector
(``hit_gold``) in place of the judged one (``hit``): per paper, the mean over its G records of
D minus its C record's D, D = merged - none (base x default), through
:func:`alias_study_stats.h1b_label_supports`, which takes the hit key as an argument. The
95 % paper-bootstrap CI and the label-randomization p come from the module's own
:func:`alias_study_stats.member_randomization`, with the module's seeds (``261101|alias|boot``,
``261101|alias|perm``) and counts (10,000 each). The p is printed as a figure of the display,
not as a test: H1b's tested p is the judged one.

**Inputs and checks.** The stage-B statistics config, read through
:class:`alias_study_stats.Data` (so :func:`alias_study_stats.study_preflight`, the analysis
gate and the record-set verification run first), then the label coverage and the frozen
stage-A cross-check, exactly as :func:`alias_study_stats.analyse` runs them. The main run's
studies must carry ``prereg_verified`` (every run summary verified the frozen prereg's sha).

**Cross-check (a hard stop).** The judged H1b is recomputed by the same wrapper and must equal
both :func:`alias_study_stats.layer1`'s H1b on the same outcomes and the committed stage-B
public results (``--reference``): net, p, CI and counts, exactly; the H1 and H2 gold-only nets
must equal the reference's; and the input hashes must equal the reference's. Any difference
writes nothing and exits 2.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_stats as st  # noqa: E402

SCHEMA = "alias-study-h1b-gold/1"
LABEL = "display only, decides nothing; computed post-hoc (DEVIATIONS D-2)"
REFERENCE = _HERE.parents[2] / "docs" / "plans" / "results" / "alias-study-2026-11" / "artifacts" / \
    "results-stage-B.public.json"
#: the H1b fields the cross-check compares, exactly, with the committed stage-B results
H1B_FIELDS = ("n", "papers", "net_points", "ci95", "ci90", "gained", "lost", "n_nonzero", "statistic", "p",
              "p_exact", "p_method", "min_attainable_p", "missing_or_dropped", "alternative", "test")
#: the fields :func:`h1b_member` produces that layer1's H1b must carry unchanged
MEMBER_FIELDS = ("n", "papers", "net_points", "ci95", "ci90", "ci90_exact", "gained", "lost", "alternative",
                 "test", "n_nonzero", "statistic", "p", "p_exact", "p_perm", "p_mid", "p_method",
                 "min_attainable_p", "missing_or_dropped")


def h1b_member(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
               an: Mapping[str, Any], key: str = "hit", missing: str = "exclude") -> dict[str, Any]:
    """H1b's estimate, CI and label-randomization p on the hit vector ``key`` (``hit`` judged,
    ``hit_gold`` gold-only): the two lines of :func:`alias_study_stats.layer1` that build H1b,
    with the key as a parameter. No gate, no family procedure."""
    clb, vb, supb, missb = st.h1b_label_supports(bal, outcomes, key, missing)
    return {**st.member_randomization(clb, vb, supb, "greater", "paper", an), "missing_or_dropped": missb}


def analysis_params(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The module's counts and seeds, as :func:`alias_study_stats.analyse` sets them."""
    an = {"n_perm": int(cfg.get("n_perm", st.cs.N_FLIPS)), "n_boot": int(cfg.get("n_boot", st.cs.N_BOOT)),
          "perm_seed": asc.seed_int(asc.SEEDS["perm"]), "boot_seed": asc.seed_int(asc.SEEDS["boot"])}
    if not cfg.get("dry_run") and (an["n_perm"] != 10_000 or an["n_boot"] != 10_000):
        raise SystemExit("n_perm and n_boot are 10,000 (prereg §7); other values only in a dry run")
    return an


def compute(bal: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]],
            gates: Mapping[str, str | None], reach: Mapping[str, Any] | None, an: Mapping[str, Any],
            power_artifact: str | Path | None = None) -> dict[str, Any]:
    """The judged H1b by the wrapper and by :func:`alias_study_stats.layer1` (which must agree on
    every wrapper field), the gold-only H1b by the wrapper, and layer1's H1 and H2 gold-only nets."""
    l1 = st.layer1(bal, outcomes, gates, reach, an, power_artifact=power_artifact)
    judged = h1b_member(bal, outcomes, an, "hit")
    gold = h1b_member(bal, outcomes, an, "hit_gold")
    diff = sorted(k for k in MEMBER_FIELDS if k in l1["H1b"] and l1["H1b"][k] != judged.get(k))
    return {"judged": judged, "gold": gold, "layer1_h1b": l1["H1b"], "wrapper_equals_layer1": not diff,
            "wrapper_vs_layer1_differs": diff,
            "h1_gold_only_net_points": l1["H1"].get("gold_only_net_points"),
            "h2_gold_only_net_points": l1["H2"].get("gold_only_net_points")}


def crosscheck(comp: Mapping[str, Any], reference: Mapping[str, Any],
               inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The judged H1b (wrapper and layer1), H1's and H2's gold-only nets and, when given, the
    input hashes against the committed stage-B results. ``agree`` is True only when all match."""
    ref1 = reference["layer1"]
    ref = ref1["H1b"]
    mism = [f"H1b.{k}" for k in H1B_FIELDS if k in ref and comp["judged"].get(k) != ref[k]]
    if not comp["wrapper_equals_layer1"]:
        mism += [f"wrapper vs layer1: {k}" for k in comp["wrapper_vs_layer1_differs"]]
    for h, k in (("H1", "h1_gold_only_net_points"), ("H2", "h2_gold_only_net_points")):
        if comp[k] != ref1[h].get("gold_only_net_points"):
            mism.append(f"{h}.gold_only_net_points")
    if inputs is not None and dict(inputs) != reference.get("inputs"):
        mism.append("inputs")
    return {"agree": not mism, "mismatches": mism,
            "judged_h1b_recomputed": {k: comp["judged"].get(k) for k in ("net_points", "p", "ci95", "n", "papers")},
            "judged_h1b_reference": {k: ref.get(k) for k in ("net_points", "p", "ci95", "n", "papers")},
            "h1_gold_only_net_points": comp["h1_gold_only_net_points"],
            "h2_gold_only_net_points": comp["h2_gold_only_net_points"],
            "inputs_compared": inputs is not None}


def result(comp: Mapping[str, Any], check: Mapping[str, Any], an: Mapping[str, Any]) -> dict[str, Any]:
    """The output body (provenance, study and inputs are added by :func:`main`)."""
    g = comp["gold"]
    return {"schema": SCHEMA, "label": LABEL,
            "h1b_gold": {**{k: g.get(k) for k in MEMBER_FIELDS if k in g}, "key": "hit_gold",
                         "note": "H1b's estimator on found@10-gold; the p is a display figure of the same "
                                 "label randomization, not a test, and enters no family"},
            "h1b_judged_crosscheck": dict(check),
            "seeds": {"perm": asc.SEEDS["perm"], "boot": asc.SEEDS["boot"], "rule": asc.SEED_RULE},
            "n_perm": an["n_perm"], "n_boot": an["n_boot"]}


def public_output(res: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                  final_keys: Any = ()) -> tuple[Any, list[str]]:
    """The public copy (:func:`alias_study_stats.public_copy`) and its
    :func:`alias_study_common.public_problems` against the same forbidden ids and texts as the
    statistics' own ``--public``."""
    forbidden = {r["record_id"] for r in records} | {str(r["paper"]) for r in records} | \
                {r["gold_chunks"][0] for r in records} | {c for _, c in final_keys}
    texts = [r["question"] for r in records]
    pub = st.public_copy(res)
    return pub, asc.public_problems(pub, forbidden, texts)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--public", required=True, type=Path)
    ap.add_argument("--reference", type=Path, default=REFERENCE)
    args = ap.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    reference = json.loads(args.reference.read_text())
    d = st.Data(cfg)                                   # preflight, gate, record-set check first
    an = analysis_params(cfg)
    legs, needs = st.coverage_scope(d.stage)
    cov = st.label_coverage(d.rows, d.by_id, d.final, legs, needs)
    if cov["pairs_without_final_label"] and not cfg.get("dry_run"):
        raise SystemExit(f"{cov['pairs_without_final_label']} demanded pair(s) without a final label; nothing computed")
    prim = d.outcomes("primary")
    frozen = st.check_frozen_stage_a(Path(cfg["judge_dir"]), Path(cfg["runs_dir"]), prim, d.by_id)
    study = st.study_block(d)
    if not cfg.get("dry_run") and not study.get("prereg_verified"):
        raise SystemExit("a run summary did not verify the frozen prereg's sha256 (§9.2)")
    comp = compute(d.bal, prim, d.gates, cfg.get("reach"), an, power_artifact=cfg.get("power_artifact"))
    inputs = st.input_hashes(cfg, d)
    check = crosscheck(comp, reference, inputs)
    print(json.dumps({"judged_crosscheck": check}, indent=1, sort_keys=True))
    if not check["agree"]:
        print(f"STOP: the judged H1b recomputed here does not match the stage-B results: {check['mismatches']}",
              file=sys.stderr)
        return 2
    res = result(comp, check, an)
    try:
        from ragstack.provenance import experiment_provenance
        prov = experiment_provenance()
    except Exception:  # noqa: BLE001
        prov = {}
    res.update(study=study, label_coverage=cov, frozen_stage_a=frozen, record_set=d.identity,
               inputs={**inputs, "reference_results_sha256": asc.sha256_file(args.reference)},
               provenance=prov, dry_run=bool(cfg.get("dry_run")),
               citable=st.is_citable(study, prov, bool(cfg.get("dry_run"))))
    pub, bad = public_output(res, d.records, d.final)
    if bad:
        print(f"REFUSED: the public copy would carry {len(bad)} private string(s) ({sorted(set(bad))})", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    args.public.write_text(json.dumps(pub, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"H1b_gold": {k: res["h1b_gold"].get(k) for k in ("net_points", "ci95", "p", "n", "papers",
                                                                      "gained", "lost")},
                      "label": LABEL}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
