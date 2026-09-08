"""Stage 0b' -- render the write-up's tables from the committed JSON.

The write-up quotes no number that is not printed here from ``stats.json`` /
``checks.json`` / ``es_concordance.json``, so re-running this reproduces every table in
``RESULTS-stage0b-prime.md``.
"""
from __future__ import annotations

import json
import sys

import s0b_common as K
import s0_common as C  # noqa: F401

B = str(K.PRIMARY_BUDGET)
ARM_ORDER = ["fixed_tok256_ov0pct", "fixed_tok512_ov0pct", "fixed_tok512",
             "header512", "fixed_tok1024_ov0pct", "fixed_tok2048_ov0pct",
             "parent256", "nbr1_512", "nbr1_256", "nbr2_512", "multi256+1024"]


def f(x, n=3):
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    return f"{x:.{n}f}" if isinstance(x, float) else str(x)


def ci(d, n=3):
    if not d or d.get("mean") is None:
        return "—"
    return f"{d['mean']:+.{n}f} [{d['lo']:+.{n}f}, {d['hi']:+.{n}f}]"


def main() -> None:
    S = json.loads((K.OUT / "stats.json").read_text())
    CH = json.loads((K.OUT / "checks.json").read_text())
    try:
        ES = json.loads((K.OUT / "es_concordance.json").read_text())
    except Exception:  # noqa: BLE001
        ES = None
    L = []

    # ---- gate table, CDS -------------------------------------------------------
    L.append("### CDS development topics — B = 16,384 SFR, `hybrid`, reranker on, "
             "`summary` queries\n")
    L.append("| arm | ERET | EPACK (a) support | EPACK (b) core | EPACK (c) unit | "
             "ERET×EPACK(a) | SFR realised | gen est | docs packed | window |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    lv = S["levels"]["cds"][B]
    for a in ARM_ORDER:
        k = f"{a}|hybrid|on"
        if k not in lv:
            k = f"{a}|vector|on"
            if k not in lv:
                continue
        v = lv[k]
        w = f"ERET {v['window']['ERET']}, EPACK(a) {v['window']['EPACK_a']}"
        L.append(f"| `{a}`{' *(vector only)*' if 'vector' in k else ''} | "
                 f"{f(v['ERET'])} | {f(v['EPACK_a'])} | {f(v['EPACK_b'])} | "
                 f"{f(v['EPACK_c'])} | {f(v['product_ERETxEPACKa'])} | "
                 f"{v['mean_sfr_realised']} | {v['mean_gen_tokens_est']} | "
                 f"{v['mean_docs_packed']} | {w} |")

    # ---- gate table, pointed ---------------------------------------------------
    L.append("\n### Pointed development set (177 queries) — same configuration\n")
    L.append("| arm | ERET | ERET Wilson 95 % | EPACK given reach | EPACK Wilson 95 % | "
             "EPACK unconditional | SFR realised | docs packed | window |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    lp = S["levels"]["pointed"][B]
    for a in ARM_ORDER:
        k = f"{a}|hybrid|on"
        if k not in lp:
            k = f"{a}|vector|on"
            if k not in lp:
                continue
        v = lp[k]
        wl = v["ERET_wilson95"]
        pl = v["EPACK_given_reach_wilson95"]
        L.append(f"| `{a}`{' *(vector only)*' if 'vector' in k else ''} | "
                 f"{f(v['ERET'])} | [{f(wl[0])}, {f(wl[1])}] | "
                 f"{f(v['EPACK_given_reach'])} | "
                 f"{('[' + f(pl[0]) + ', ' + f(pl[1]) + ']') if pl else '—'} | "
                 f"{f(v['EPACK_unconditional'])} | {v['mean_sfr_realised']} | "
                 f"{v['mean_docs_packed']} | ERET {v['window']['ERET']}, "
                 f"EPACK {v['window']['EPACK_given_reach']} |")

    # ---- contrasts -------------------------------------------------------------
    for pop in ("cds", "pointed"):
        L.append(f"\n### Contrasts — {pop}, `hybrid` + rerank, B = 16,384 SFR\n")
        L.append("| id | control − candidate | d ERET [95 % CI] | σ_d(ERET) bound₈₀ | "
                 "NI ERET | d EPACK∩ [95 % CI] | σ_d(EPACK) bound₈₀ | NI EPACK | "
                 "n_ret ERET / EPACK | dropped |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for cid, c in S["contrasts"][pop].items():
            if c.get("status") == "ABSENT":
                L.append(f"| {cid} | — | ABSENT | | | | | | | |")
                continue
            e, p = c["ERET"], c["EPACK_confirmatory_intersection"]
            L.append(
                f"| **{cid}** | `{c['control']}` − `{c['candidate']}` | {ci(e['ci'])} | "
                f"{f(e['sigma']['governing_bound_80'])} | "
                f"{f(e['ni'].get('non_inferior'))} | {ci(p['ci'])} | "
                f"{f(p['sigma']['governing_bound_80'])} | "
                f"{f(p['ni'].get('non_inferior'))} | "
                f"{e['n_retained']} / {p['n_retained']} | "
                f"{p.get('n_dropped', 0)} |")

    # ---- the three EPACK readings ---------------------------------------------
    L.append("\n### The three EPACK readings, side by side (CDS, confirmatory "
             "intersection)\n")
    L.append("| id | (a) support-weighted | (b) core ≥ 0.5 | (c) unit-based |")
    L.append("|---|---|---|---|")
    for cid, r in S["epack_readings"].items():
        L.append(f"| **{cid}** | {ci(r['a'])} | {ci(r['b'])} | {ci(r['c'])} |")

    # ---- estimand + sensitivity -----------------------------------------------
    L.append("\n### Estimand agreement and the `EPACK := 0` sensitivity (CDS)\n")
    L.append("| id | confirmatory ∩ | reached-set | `EPACK := 0` | agreement |")
    L.append("|---|---|---|---|---|")
    for cid, c in S["contrasts"]["cds"].items():
        L.append(f"| **{cid}** | {ci(c['EPACK_confirmatory_intersection']['ci'])} | "
                 f"{ci(c['EPACK_reached_set']['ci'])} | "
                 f"{ci(c['EPACK_zero_imputation_sensitivity']['ci'])} | "
                 f"{c['estimand_agreement']} |")

    # ---- joint power -----------------------------------------------------------
    L.append("\n### Joint bootstrap power (both endpoints resampled together, "
             "10,000 draws, seed 20260913)\n")
    L.append("| id | population | n | Δ = 0 | Δ = 0.01 | Δ = 0.02 | marginals at Δ = 0 "
             "(ERET / EPACK) |")
    L.append("|---|---|---|---|---|---|---|")
    for cid, c in S["contrasts"]["cds"].items():
        jp = c.get("joint_power", {})
        bd = jp.get("by_delta", {})
        if not bd:
            continue
        L.append(f"| **{cid}** | CDS | {jp['n']} | "
                 + " | ".join(f(bd[str(x)]["joint_power"]) for x in (0.0, 0.01, 0.02))
                 + f" | {f(bd['0.0']['power_ERET'])} / {f(bd['0.0']['power_EPACK'])} |")
    for cid, c in S["contrasts"]["pointed"].items():
        jp = c.get("joint_power_at_n_dev", {})
        bd = jp.get("by_delta", {})
        if not bd:
            continue
        L.append(f"| **{cid}** | pointed | {jp['n']} | "
                 + " | ".join(f(bd[str(x)]["joint_power"]) for x in (0.0, 0.01, 0.02))
                 + f" | {f(bd['0.0']['power_ERET'])} / {f(bd['0.0']['power_EPACK'])} |")

    # ---- mode x size -----------------------------------------------------------
    for pop in ("cds", "pointed"):
        L.append(f"\n### Mode × size — {pop}, B = 16,384 SFR, reranker **on**\n")
        L.append("| id | vector: d ERET / d EPACK∩ | bm25: d ERET / d EPACK∩ | "
                 "hybrid: d ERET / d EPACK∩ |")
        L.append("|---|---|---|---|")
        for cid, _f2, _c, _d in [(x[0], x[1], x[2], x[3]) for x in _contrasts()]:
            cells = []
            for m in K.MODES:
                k = f"{cid}|{m}|rerank_on"
                v = S["mode_x_size"][pop].get(k)
                cells.append("—" if not v else
                             f"{ci(v['d_ERET'])} / {ci(v['d_EPACK_int'])}")
            L.append(f"| **{cid}** | " + " | ".join(cells) + " |")
        L.append(f"\n### Mode × size — {pop}, reranker **off** (same frozen pools)\n")
        L.append("| id | vector: d ERET / d EPACK∩ | bm25: d ERET / d EPACK∩ | "
                 "hybrid: d ERET / d EPACK∩ |")
        L.append("|---|---|---|---|")
        for cid, _f2, _c, _d in [(x[0], x[1], x[2], x[3]) for x in _contrasts()]:
            cells = []
            for m in K.MODES:
                k = f"{cid}|{m}|rerank_off"
                v = S["mode_x_size"][pop].get(k)
                cells.append("—" if not v else
                             f"{ci(v['d_ERET'])} / {ci(v['d_EPACK_int'])}")
            L.append(f"| **{cid}** | " + " | ".join(cells) + " |")

    # ---- budget curve ----------------------------------------------------------
    for pop, key in (("CDS", "cds"), ("pointed", "pointed")):
        L.append(f"\n### Budget curve — {pop}, `hybrid` + rerank\n")
        L.append("| arm | ERET @4k | ERET @16k | ERET @32k | EPACK @4k | EPACK @16k | "
                 "EPACK @32k |")
        L.append("|---|---|---|---|---|---|---|")
        ek = "EPACK_a" if key == "cds" else "EPACK_given_reach"
        for a in ARM_ORDER:
            row = []
            ok = True
            for metric in ("ERET", ek):
                for b in K.BUDGETS:
                    lvb = S["levels"][key][str(b)]
                    k = f"{a}|hybrid|on"
                    if k not in lvb:
                        k = f"{a}|vector|on"
                    if k not in lvb:
                        ok = False
                        break
                    row.append(f(lvb[k][metric]))
            if ok:
                L.append(f"| `{a}` | " + " | ".join(row) + " |")

    # ---- guards ----------------------------------------------------------------
    L.append("\n### Pointed guard 1 — discrimination and the window\n")
    L.append("| check | value | bar | verdict |")
    L.append("|---|---|---|---|")
    for k, v in S["guard1_discrimination"]["top10_doc_sets_differ"].items():
        L.append(f"| top-10 document sets differ, {k} | {f(v['rate'])} "
                 f"({v['n_queries']} queries) | ≥ 0.25 | "
                 f"{'PASS' if v['rate'] >= 0.25 else 'FAIL'} |")
    L.append(f"| confirmatory `EPACK` inside [0.15, 0.90] for every arm | "
             f"{sum(1 for v in S['guard1_discrimination']['EPACK_window_by_arm_hybrid_rerank_on'].values() if v == 'IN')}"
             f"/{len(S['guard1_discrimination']['EPACK_window_by_arm_hybrid_rerank_on'])}"
             f" arms IN | all arms | "
             f"{S['guard1_discrimination']['status']} |")

    L.append("\n### Pointed guard 3 — sizing from the measured σ_d "
             "(α = 0.025 one-sided, ε = 0.05, 80 % power)\n")
    L.append("| id | σ_d(ERET) | σ_d(EPACK∩) | n for 80 % ERET | n for 80 % EPACK | "
             "**n for 80 % JOINT** | joint power @177 | @600 | within the 600 cap |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for cid, r in S["guard3_sizing"]["by_contrast"].items():
        L.append(f"| **{cid}** | {f(r['sigma_d_ERET_bound80'])} | "
                 f"{f(r['sigma_d_EPACK_bound80'])} | {f(r['n_for_80pct_ERET'])} | "
                 f"{f(r['n_for_80pct_EPACK'])} | **{f(r['n_for_80pct_JOINT_delta0'])}** | "
                 f"{f(r['joint_power_at_177']['0.0']['joint_power'])} | "
                 f"{f(r['joint_power_at_600']['0.0']['joint_power'])} | "
                 f"{f(r['within_600_cap'])} |")

    # ---- plumbing --------------------------------------------------------------
    L.append("\n### Plumbing reproduction\n")
    L.append("| arm | Stage 0 `P(doc packed)` @4,096 gen | this harness @5,146 SFR | "
             "|Δ| | Stage 0 unions re-scored here |")
    L.append("|---|---|---|---|---|")
    cc = CH["plumbing_C_end_to_end_reach"]
    cb = CH["plumbing_B_scoring_identity_on_stage0_unions"]
    for a, v in cc.items():
        if a.startswith("_"):
            continue
        L.append(f"| `{a}` | {f(v['stage0_at_4096_gen'], 4)} | "
                 f"{f(v['reach_at_5145_sfr'], 4)} | {f(v['abs_diff'], 4)} | "
                 f"{f(cb[a]['recomputed'], 4)} (Δ {f(cb[a]['abs_diff'], 4)}) |")

    # ---- concordance -----------------------------------------------------------
    if ES:
        L.append("\n### BM25 concordance against the dev tenant's Elasticsearch\n")
        s = ES.get("summary")
        if s:
            L.append("| statistic | value |")
            L.append("|---|---|")
            L.append(f"| queries | {s['queries']} |")
            L.append(f"| **mean overlap@50** | **{f(s['mean_overlap_at_50'], 4)}** "
                     f"(bar ≥ 0.90) |")
            L.append(f"| median overlap@50 | {f(s['median_overlap_at_50'], 4)} |")
            L.append(f"| min overlap@50 | {f(s['min_overlap_at_50'], 4)} |")
            L.append(f"| mean Spearman on the intersection | "
                     f"{f(s['mean_spearman_on_intersection'], 4)} |")
            L.append(f"| same rank-1 | {f(s['same_rank1_rate'], 4)} |")
            for pop, v in s["by_population"].items():
                L.append(f"| overlap@50, {pop} ({v['queries']} queries) | "
                         f"{f(v['mean_overlap_at_50'], 4)} |")
            L.append(f"| verdict | **{ES['status']}** |")
        else:
            L.append(f"Concordance check did not produce a summary: `{ES.get('status')}`")

    out = "\n".join(L) + "\n"
    (K.ART / "TABLES.md").write_text(out)
    print(out)


def _contrasts():
    from s0b_stats import CONTRASTS
    return CONTRASTS


if __name__ == "__main__":
    sys.exit(main())
