# R0 — calibration of OpenChia's Episode stopping statistic on known truth

**Date:** 2026-10-05 · **Pre-registration:** `PREREG-R0-stopping-statistic-calibration.json` (frozen before the run)
· **Code:** `r0_stopping_statistic_calibration.py` (stdlib only; independent reimplementation) · **Raw results:**
`RESULTS-R0-stopping-statistic-calibration.json` · **Seed:** `20261005-{K}-{condition}` · **Replicates:** 300 per condition

**Verdict:** the shipped stopping statistic is reproduced exactly and is **calibrated under even sampling**; under
**heavy heterogeneity it does not terminate** and its remaining-richness band **misses the truth from below** 11–37 %
of the time. Two of four pre-registered predictions held; two were falsified as stated, both in the same direction:
the failure mode under heterogeneity is non-termination with an underestimating band, not premature stopping.

## 0. The statistic is pinned, not inferred

Reimplementing the documented chain at `chian/OpenChia@98d74fb` (`numeric_control_library/{rarefaction,
credit_assignment,continuation}.py`) for one result channel and evaluating it at the acceptance record's state
(two observed units, one accepted identity seen twice: n = 2, count = 1, f₁ = 0, f₂ = 1, no failures):

| | projected marginal credit upper bound |
|---|---|
| recorded in `docs/openchia/epistemic_yield_acceptance.md` (2026-10-02) | 0.749286267 |
| recomputed here | 0.7492862673640707 |
| absolute difference | 3.6 × 10⁻¹⁰ |

So the simulation below exercises the statistic OpenChia actually stops on:
`min(1, (count + next.upper) / max(1, total.lower, count+1)) − count / max(1, total.upper, count+1) ≤ θ`,
with `next` = Jeffreys-gamma conditional next-yield × Wilson usability, `total` = count + bias-corrected Chao1
remaining richness, Chebyshev radii `sqrt(var / component_alpha)`, `component_alpha = α/3`, α = 0.05.

## 1. Design (as pre-registered)

Finite population of K distinct identities; each unit draws a Poisson(5)-sized set with detection weights that are
equal (**even**), log-normal σ = 1 (**mild**) or log-normal σ = 2 (**heavy** — a few sources easy to hit, many nearly
unreachable: the web-search regime). True remaining richness is known after every unit. Cap 50·K/5 units.

## 2. Results at the default tolerance θ = 0.01

| condition | band coverage | miss above / below | never stopped | stop unit (median) | undiscovered at stop (median / p90) |
|---|---:|---:|---:|---:|---:|
| K=50 even | 0.953 | 0.047 / 0.000 | 0 % | 50 | 0.000 / 0.040 |
| K=50 mild | 0.942 | 0.058 / 0.000 | 5 % | 157 | 0.020 / 0.060 |
| K=50 heavy | 0.819 | 0.181 / 0.000 | **85 %** | 320 | **0.140 / 0.260** |
| K=200 even | 0.987 | 0.013 / 0.000 | 0 % | 230 | 0.000 / 0.010 |
| K=200 mild | 0.979 | 0.021 / 0.000 | 12 % | 1,182 | 0.005 / 0.020 |
| K=200 heavy | 0.889 | 0.111 / 0.000 | **100 %** | — | — |
| K=1000 even | 0.996 | 0.004 / 0.000 | 0 % | 1,190 | 0.002 / 0.005 |
| K=1000 mild | 0.988 | 0.012 / 0.000 | 17 % | 7,379 | 0.004 / 0.009 |
| K=1000 heavy | **0.627** | **0.373** / 0.000 | **100 %** | — | — |

Stops at n ≤ 2 under θ = 0.01: **0 of 2,700 trajectories** in every condition.

At θ = 0.05: even and mild conditions always terminate (K=1000 mild: median 3,308 units, 2.2 % undiscovered);
K=200 heavy terminated in ≤ 1 of 300 replicates; K=1000 heavy in none.

## 3. Predictions scored

| | pre-registered | observed | status |
|---|---|---|---|
| **P1** even sampling calibrated: coverage ≥ 0.90 at every K; median undiscovered at stop ≤ 0.02 | coverage 0.953 / 0.987 / 0.996; undiscovered 0.000 / 0.000 / 0.002 | **held** |
| **P2** σ = 2, K = 1000: premature-stop rate ≥ 0.25 and coverage ≤ 0.80 | coverage 0.627 (✓) but **no replicate stopped**, so the premature rate is undefined; at K = 50 the 15 % that stopped were premature 86 % of the time | **falsified as stated** — the mechanism was wrong (non-termination, not early firing); the direction (unsafe) was right |
| **P3** undiscovered(θ=0.05) ≥ 2 × undiscovered(θ=0.01) in every condition | ratios 2.0 · ∞ · 4.0 · 9.5 · 5.6 where defined; **1.29 at K=50 heavy**; undefined in 3 conditions | **falsified as stated** — holds in every well-sampled condition |
| **P4** the default cannot fire after two units | 0 / 2,700 | **held** |

## 4. What this means

1. **Where sampling is even, the rule is sound and conservative.** Coverage runs above nominal (Chebyshev radii), and
   the stop lands at essentially complete discovery (≤ 0.2 % missed), at roughly K·ln K / 5 units — the coupon-collector
   horizon. This is the regime of **candidate generation over a known entity table**, where we control the sampling.
2. **Where sampling is heavily uneven, the rule does not give an answer.** It neither stops (the singleton stream from
   rare items keeps the conditional upper high) nor bounds the truth (37 % of bands at K = 1000 exclude it, all from
   below). A run in this regime ends by `bound_hit`, which OpenChia correctly refuses to call convergence — but the
   remaining-richness band it reports on the way is not a number we can quote.
3. **Therefore the 2 Oct claim that the controller "gives *searched and did not find* a measured number" is
   withdrawn.** Literature search is the uneven regime by construction. The survey pilot's P5 ("stop behaviour,
   measured not predicted") now has a prior: expect `bound_hit`.
4. **The one upstream acceptance is uninformative about the default.** It stopped at θ = 0.9 after two units; R0 shows
   the default cannot fire there, matching the record's own caveat. R1 should run the same problem at θ = 0.01.
5. **Mild heterogeneity is safe but slow:** 6× the sampling of the even case at K = 1000, and 12–17 % of runs hit a
   generous cap. Budgets, not the stop, will end most real runs.

## 5. Limits of R0

- Method-level, not implementation-level: R0b must push the same incidence states through OpenChia's own
  `numeric_control_library` (executing external code — the owner performs this) and compare bands to the digit.
- One channel; multi-channel conjunction and the hypervolume over several columns were not exercised.
- Log-normal detection is one heterogeneity model; a power-law or two-class mixture is a natural follow-up.
- nano-graphrag's larger `preferential_incidence_estimator_v2` is a different estimator and was not tested.

## 6. The ladder from here

R0 (done) → **R0b** same states through OpenChia's code → **R1** reproduce the scheduling acceptance at the default θ →
**R2** our deterministic results inside their structure (span-filter: 0.9205 / Δ −0.0013, Scout 1,050 / Qwen 9 filtered;
then a seeded dev-subset labeling Episode) → **R3** the survey pilot → only then an Architecture for a confirmatory stage.
