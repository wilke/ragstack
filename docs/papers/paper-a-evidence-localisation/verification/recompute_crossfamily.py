#!/usr/bin/env python3
"""Third-family (Claude) support lift, point-biserial r / Spearman rho, costs and n's.

Definitions (DRAFT §5.5, §4; RESULTS-stage0b-claude-judges.md):
* support of a sentence = number of the 30 local readings (Scout k = 0..19, Qwen k = 0..9)
  that select it (a reading counts once however many of its spans cover it);
* lift = mean support of the sentences a Claude judge picked (support 0 included) minus the
  baseline, the mean support of every sentence the local pool touched (support >= 1);
* point-biserial r = Pearson r, over every sentence the pool touched OR the judge picked,
  between picked (0/1) and support; Spearman rho = the same on average ranks; pooled over
  all pairs;
* cost = the sum of each label record's `cost_usd`; per pair = its mean.

The text does not say over WHICH pairs the baseline is taken. Two readings are computed:
(a) every pair the judge labeled that has local support (the harness's reading), and
(b) only the pairs the judge called positive (DRAFT §5.5 says Fable's baseline is 'computed
over its own 248 positive pairs'). A third, (c), drops the one locator blow-up pair, which
contributes 22,366 support-1 sentences to the pool.
"""
from __future__ import annotations

import vcommon as V

BLOW = ("2016_1", "4212306")


def main() -> None:
    prov = V.provenance()
    ext = {j: V.load("r31ext", j) for j in ("scout", "qwen")}
    depth = {"scout": 20, "qwen": 10}
    support = {}
    for k in set(ext["scout"]) | set(ext["qwen"]):
        sup: dict = {}
        for j in ext:
            for p in range(depth[j]):
                for s in V.sentence_set(ext[j][k][p]):
                    sup[s] = sup.get(s, 0) + 1
        support[k] = sup

    judges = {j: {k: v[0] for k, v in V.load("claude", j).items()}
              for j in ("sonnet5", "opus5", "fable51")}
    res: dict = {}

    def lift(labs, pair_filter):
        picked, base, zero, pairs = [], [], 0, 0
        for k in sorted(labs):
            sup = support.get(k) or {}
            if not sup or not pair_filter(k):
                continue
            base += list(sup.values())
            mine = V.sentence_set(labs[k])
            if not mine:
                continue
            pairs += 1
            vals = [sup.get(s, 0) for s in mine]
            picked += vals
            zero += sum(v == 0 for v in vals)
        return {"mean_support_picked": V.fmean(picked), "baseline": V.fmean(base),
                "lift": V.fmean(picked) - V.fmean(base), "n_sentences_picked": len(picked),
                "n_pairs_with_local_support_and_picks": pairs,
                "n_locally_touched_sentences": len(base),
                "frac_picked_support_0": zero / len(picked)}

    for j, labs in judges.items():
        a = lift(labs, lambda k: True)
        b = lift(labs, lambda k, labs=labs: V.positive(labs[k]))
        c = lift(labs, lambda k: k != BLOW)
        px, sx, px_nb, sx_nb = [], [], [], []
        for k in sorted(labs):
            sup = support.get(k) or {}
            if not sup:
                continue
            mine = V.sentence_set(labs[k])
            uni = sorted(set(sup) | mine)
            px += [1.0 if s in mine else 0.0 for s in uni]
            sx += [float(sup.get(s, 0)) for s in uni]
            if k != BLOW:
                px_nb += [1.0 if s in mine else 0.0 for s in uni]
                sx_nb += [float(sup.get(s, 0)) for s in uni]
        costs = [r["cost_usd"] for r in labs.values() if r.get("cost_usd") is not None]
        res[j] = {"pairs_labeled": len(labs),
                  "pairs_positive": sum(V.positive(r) for r in labs.values()),
                  "pairs_with_local_support": sum(1 for k in labs if support.get(k)),
                  "lift_a_all_labeled_pairs": a, "lift_b_positive_pairs_only": b,
                  "lift_c_excluding_blowup_pair": c,
                  "point_biserial_r": V.pearson(px, sx),
                  "spearman_rho": V.pearson(V.avg_ranks(px), V.avg_ranks(sx)),
                  "n_sentences_pooled": len(px),
                  "point_biserial_r_excluding_blowup_pair": V.pearson(px_nb, sx_nb),
                  "spearman_rho_excluding_blowup_pair": V.pearson(V.avg_ranks(px_nb),
                                                                  V.avg_ranks(sx_nb)),
                  "n_sentences_pooled_excluding_blowup_pair": len(px_nb),
                  "cost_total_usd": sum(costs), "cost_mean_per_pair": V.fmean(costs),
                  "n_pairs_costed": len(costs)}
    res["total_cost_usd"] = sum(res[j]["cost_total_usd"] for j in judges)
    res["total_pair_readings"] = sum(res[j]["n_pairs_costed"] for j in judges)
    for lj in ("scout", "qwen"):
        res[f"self_reference.{lj}"] = lift({k: v[0] for k, v in ext[lj].items()},
                                           lambda k: True)
    V.write("recompute_crossfamily", prov, {"results": res})


if __name__ == "__main__":
    main()
