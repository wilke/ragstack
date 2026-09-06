"""Stage 0 step 8 -- render the SS8.5.7 table to markdown from the JSON artifacts.

Nothing here recomputes anything: every number is read from ``work/*.json`` so the
RESULTS document and the machine record cannot drift apart.
"""
from __future__ import annotations

import json

import s0_common as C

ORDER = ["N1", "N2", "R1", "R2", "R3"]


def f(x, n=4):
    return "—" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))


def main() -> None:
    T = json.loads((C.WORK / "stage0_table.json").read_text())
    G = json.loads((C.WORK / "label_gates.json").read_text())
    K = json.loads((C.WORK / "checks.json").read_text())
    R = json.loads((C.WORK / "rdev_sample.json").read_text())
    U = json.loads((C.WORK / "units.json").read_text())
    L = []

    P = L.append
    P("### Row 1 — σ_d(EUC@4096) per confirmatory contrast\n")
    P("| contrast | control − candidate | n used | mean d | **σ̂_d** | χ² 80% | χ² 90% | "
      "χ² 95% | bootstrap 80% | **governing bound** |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "row1_sigma_d" not in r:
            P(f"| **{cid}** | {r['control']} − {r['candidate']} | — | — | — | — | — | — | "
              f"— | **{r['status']}** |")
            continue
        a = r["row1_sigma_d"]
        P(f"| **{cid}** | `{r['control']}` − `{r['candidate']}` | {a['n_used']} | "
          f"{f(a['mean_diff'])} | **{f(a['point_estimate'])}** | {f(a['chi2_upper']['80%'])} | "
          f"{f(a['chi2_upper']['90%'])} | {f(a['chi2_upper']['95%'])} | "
          f"{f(a['bootstrap80_upper'])} | **{f(a['governing_bound_80'])}** "
          f"({a['governing_bound_source']}) |")

    P("\n### Row 2 — unit-level `p_flip` and ρ, model-based vs direct σ_d\n")
    P("| contrast | units | `p_flip` | **ρ (one-way ICC)** | ρ 95% cluster-boot | "
      "model σ_d | direct σ_d | **governing point σ_d** |")
    P("|---|---|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "row2_pflip_rho" not in r:
            P(f"| **{cid}** | — | — | — | — | — | — | {r['status']} |")
            continue
        a = r["row2_pflip_rho"]
        ci = a["rho_boot95"]
        P(f"| **{cid}** | {a['n_units']} | {f(a['p_flip'])} | **{f(a['rho_icc'])}** | "
          f"[{f(ci[0]) if ci else '—'}, {f(ci[1]) if ci else '—'}] | "
          f"{f(a['model_sigma_d'])} | {f(a['direct_sigma_d'])} | "
          f"**{f(a['governing_point_sigma_d'])}** ({a['governing_source']}) |")

    P("\n### Row 3 — units per topic after D3, and the cap\n")
    a = T["row3_units_per_topic"]
    P(f"* **m̄ = {a['m_bar']}**, median {a['m_median']}, min {a['m_min']}, "
      f"max {a['m_max']}, cap {a['cap']}")
    P(f"* cap-hit topics: **{a['cap_hit_topics']}** of {len(a['m_per_topic'])} "
      f"(rate {a['cap_hit_rate']})")
    P(f"* per topic: `{json.dumps(a['m_per_topic'])}`")
    P("\n**D3 pipeline, counted at every step:**\n")
    P("| step | count |")
    P("|---|---|")
    for k, v in a["d3_steps"].items():
        P(f"| {k} | {v} |")

    P("\n### Row 4 — measured per-topic binary discordance (`ES-Hit@4096`)\n")
    P("| contrast | discordant / n | **d** | Wilson 95% | d ≤ 0.025 at point? | "
      "at Wilson upper? | implied binary σ_d = √d |")
    P("|---|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "row4_binary_discordance" not in r:
            P(f"| **{cid}** | — | — | — | — | — | — |")
            continue
        a = r["row4_binary_discordance"]
        P(f"| **{cid}** | {a['discordant_topics']} / {a['n']} | **{f(a['d'])}** | "
          f"[{f(a['wilson95'][0])}, {f(a['wilson95'][1])}] | "
          f"{'YES' if a['resolvable_at_point'] else 'NO'} | "
          f"{'YES' if a['resolvable_at_wilson_upper'] else 'NO'} | "
          f"{f(a['sigma_d_binary_implied'])} |")

    P("\n### Row 5 — measured ρ_variant and the real variant-averaging divisor\n")
    P("| contrast | topics paired | **ρ_variant** | **measured divisor √(2/(1+ρ))** | "
      "assumed in rev. 1 | adaptation applies (≥ 1.15)? |")
    P("|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "row5_rho_variant" not in r:
            P(f"| **{cid}** | — | — | — | 1.3 | — |")
            continue
        a = r["row5_rho_variant"]
        P(f"| **{cid}** | {a.get('n_topics_paired', '—')} | **{f(a['rho_variant'])}** | "
          f"**{f(a['measured_divisor'])}** | 1.3 | "
          f"{'YES' if a['applies'] else 'NO'} |")

    P("\n### Row 6 — projected `n_retained` under the §8.5.6 exclusions\n")
    a = T["row6_n_retained"]
    P("| criterion | applied when | measured |")
    P("|---|---|---|")
    c3 = a["criterion_lt5_fetchable_relevants_EXACT"]
    P(f"| < 5 fetchable grade ≥ 1 documents | corpus assembly — **non-outcome data, "
      f"computed EXACTLY on all 80** | **{c3['n']} topics** {c3['topics'] or ''} |")
    b = a["criterion_lt3_units_dev_rate"]
    P(f"| < 3 evidence units | label freeze — projected from the dev rate | "
      f"dev rate {b['rate']} ({b['dev_topics_below_3'] or 'none'}) |")
    b = a["criterion_label_failure_dev_rate"]
    P(f"| > 1/3 of pairs failed quote verification | label freeze | dev rate {b['rate']} |")
    b = a["criterion_windowing_failure_dev_rate"]
    P(f"| majority windowed AND windowed union inconsistent | label freeze | "
      f"dev rate {b['rate']} (majority-windowed: {b['dev_topics_majority_windowed'] or 'none'}) |")
    P(f"\n**Projected n_retained = {a['projected_n_retained']}** "
      f"(nominal 80). n_retained < 60 gate: "
      f"**{'TRIPPED' if a['gate_n_retained_lt_60'] else 'not tripped'}**.\n")
    P("σ_d required for 80% power at ε = 0.05 (exact non-central t):\n")
    P("| n | σ_d for 80% power |")
    P("|---|---|")
    for n, v in sorted(a["sigma_requirement_at_n"].items(), key=lambda x: -int(x[0])):
        P(f"| {n} | {v} |")
    d = a["conf_n_rel_distribution"]
    P(f"\n**The confirmation set's `n_rel` distribution** (non-outcome data, §2.3): "
      f"min {d['min']}, median {d['median']}, max {d['max']}; "
      f"**{d['n_below_dev_window_40']} topics below the dev window's 40** and "
      f"**{d['n_above_dev_window_250']} above its 250** — "
      f"{d['n_below_dev_window_40'] + d['n_above_dev_window_250']} of 80 lie in strata the "
      f"dev sample does not contain at all.")
    P(f"\nDev `n_rel`: `{json.dumps(a['dev_n_rel_distribution'])}`")

    P("\n### Row 7 — power against Δ ∈ {0, 0.01, 0.02}, per contrast\n")
    P("| contrast | σ_d used | Δ = 0 | Δ = 0.01 | Δ = 0.02 | at n |")
    P("|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "row7_power" not in r:
            continue
        for lbl, a in r["row7_power"].items():
            nice = "point estimate" if lbl == "at_point_estimate" else "80% upper bound"
            for nk, v in a.items():
                if nk == "sigma_d":
                    continue
                P(f"| **{cid}** ({nice}) | {f(a['sigma_d'])} | {v['0.00']}% | "
                  f"{v['0.01']}% | {v['0.02']}% | {nk[1:]} |")

    P("\n### The gate, per contrast (§8.5.5 three-outcome rule)\n")
    P("| contrast | σ_d point | σ_d 80% bound | requirement at n_retained | "
      "holds at point? | holds at bound? | **verdict** |")
    P("|---|---|---|---|---|---|---|")
    for cid in ORDER:
        r = T["contrasts"][cid]
        if "gate" not in r:
            P(f"| **{cid}** | — | — | — | — | — | **{r['status']}** |")
            continue
        g = r["gate"]
        P(f"| **{cid}** | {f(r['governing_sigma_d_point'])} | "
          f"{f(r['governing_sigma_d_bound80'])} | "
          f"{f(g['sigma_requirement_at_n_retained'])} | "
          f"{'yes' if g['holds_at_point_estimate'] else 'no'} | "
          f"{'yes' if g['holds_at_80pct_upper_bound'] else 'no'} | "
          f"**{g['verdict']}** |")

    P("\n### Row 8 — label validation\n")
    P("**Machine gates (P.5 / §6.4), computed:**\n")
    h = G["hallucinated_span_rate"]
    P(f"* hallucinated-span rate **{f(h['rate'])}** "
      f"({h['failed_spans']} / {h['attempted_spans']} spans, Wilson 95% upper "
      f"{f(h['wilson95_upper'])}) — gate ≤ 0.05: **{'PASS' if h['PASS'] else 'FAIL'}**")
    s = G["self_consistency"]
    P(f"* self-consistency **{f(s['rate'])}** ({s['consistent']} / {s['n_duplicated']} "
      f"duplicated pairs) — gate ≥ 0.90: **{'PASS' if s['PASS'] else 'FAIL'}**")
    m = G["minimality_shrinkage"]
    P(f"* minimality shrinkage {f(m['rate'])} ({m['shrank']} / {m['n_audited']} audited) "
      f"— descriptive")
    P(f"* \"no localizable evidence\" verdicts: {G['no_localizable_evidence_pairs']} / "
      f"{G['pairs']} pairs (rate {G['no_localizable_evidence_rate']}); by grade "
      f"`{json.dumps(G['by_grade'])}`")
    P(f"* dropped pairs: {G['dropped_pairs']}; windowed pairs: {G['windowed_pairs']}")
    P("\n**Human half — `PENDING-HUMAN`.** κ(human–human), κ(Scout–human), positive-class "
      "agreement and the `wrong-location` / `non-minimal` / `missed-evidence` rates all "
      "require the two-reader R-dev read of §6.6.2. **They were not computed and no agent "
      "read was substituted.** The ≥100-pair stratified draw is rendered and ready:\n")
    P(f"* draw: **{R['drawn']} pairs**, seed `{R['seed']}`, "
      f"strata `{json.dumps(R['drawn_by_stratum'])}` "
      f"(shortfalls: `{json.dumps(R['shortfalls'])}`)")
    P("* artifacts: `work/RDEV-readsheet-A.html`, `work/RDEV-readsheet-B.html` "
      "(independently shuffled), `work/rdev_verdicts_{A,B}.csv`, `work/rdev_sample.json`")

    P("\n### Row 9 — manipulation checks (§7.6) and the dev EUC level\n")
    P("| check | requirement | measured | verdict |")
    P("|---|---|---|---|")
    k = K["check1_gold_packing"]
    P(f"| 1. GOLD packing control | EUC ≥ 0.95 | mean {f(k['mean'])}, min {f(k['min'])} | "
      f"**{'PASS' if k['PASS'] else 'FAIL'}** |")
    k = K["check2_negative_control"]
    P(f"| 2. NEGATIVE control (grade-0 only) | EUC ≤ 0.05 | "
      f"`{json.dumps(k['per_arm_mean_EUC_grade0_only'])}` | "
      f"**{'PASS' if k['PASS'] else 'FAIL'}** |")
    k = K["check3_discrimination"]
    P(f"| 3. discrimination | doc Hit@1 < 1.0; top-10 differ across size extremes for "
      f"≥ 25% of topics | Hit@1 `{json.dumps(k['doc_hit_at_1_per_arm'])}`; "
      f"differ {k['topics_with_different_top10_across_size_extremes']}/{k['of_topics']} | "
      f"**{'PASS' if k['PASS'] else 'FAIL'}** |")
    k = K["check4_budget_bind"]
    P(f"| 4. budget bind | realised ∈ [0.85B, B] except rank-1 overshoot | "
      f"see below | **{'PASS' if k['PASS'] else 'FAIL'}** |")
    k = K["check5_dev_EUC_level"]
    P(f"| 5. dev EUC in [0.15, 0.90] | floor/ceiling would destroy the variance estimate | "
      f"`{json.dumps(k['per_arm_mean_EUC@4096_summary'])}` | "
      f"**{'PASS' if k['PASS'] else 'FAIL'}** |")
    P("\n**Check 4 detail (realised generator tokens at B = 4,096, `summary`):**\n")
    P("| arm | mean realised | in band | rank-1 overshoot | n |")
    P("|---|---|---|---|---|")
    for arm, v in K["check4_budget_bind"]["per_arm"].items():
        P(f"| `{arm}` | {v['mean_realised']} | {v['in_band']} | "
          f"{v['rank1_overshoot_cases']} | {v['n']} |")

    (C.HERE / "TABLE-8.5.7.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    _ = U


if __name__ == "__main__":
    main()
