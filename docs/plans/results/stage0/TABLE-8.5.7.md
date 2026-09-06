### Row 1 — σ_d(EUC@4096) per confirmatory contrast

| contrast | control − candidate | n used | mean d | **σ̂_d** | χ² 80% | χ² 90% | χ² 95% | bootstrap 80% | **governing bound** |
|---|---|---|---|---|---|---|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | 10 | -0.0524 | **0.0600** | 0.0777 | 0.0882 | 0.0988 | 0.0660 | **0.0777** (chi2) |
| **N2** | `fixed_tok512` − `fixed_tok512_ov0pct` | 10 | 0.0000 | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | **0.0000** (bootstrap) |
| **R1** | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | 10 | -0.0024 | **0.0597** | 0.0772 | 0.0877 | 0.0982 | 0.0659 | **0.0772** (chi2) |
| **R2** | `header512` − `fixed_tok512_ov0pct` | 10 | 0.0083 | **0.0263** | 0.0341 | 0.0387 | 0.0434 | 0.0351 | **0.0351** (bootstrap) |
| **R3** | `parent256` − `fixed_tok512` | 10 | 0.0083 | **0.0615** | 0.0795 | 0.0904 | 0.1012 | 0.0766 | **0.0795** (chi2) |

### Row 2 — unit-level `p_flip` and ρ, model-based vs direct σ_d

| contrast | units | `p_flip` | **ρ (one-way ICC)** | ρ 95% cluster-boot | model σ_d | direct σ_d | **governing point σ_d** |
|---|---|---|---|---|---|---|---|
| **N1** | 110 | 0.0546 | **-0.0223** | [-0.0707, 0.0322] | 0.0704 | 0.0600 | **0.0704** (model) |
| **N2** | 110 | 0.0000 | **—** | [—, —] | 0.0000 | 0.0000 | **0.0000** (model) |
| **R1** | 110 | 0.0727 | **-0.0458** | [-0.0905, 0.0118] | 0.0813 | 0.0597 | **0.0813** (model) |
| **R2** | 110 | 0.0091 | **-0.0092** | [-0.0381, -0.0008] | 0.0288 | 0.0263 | **0.0288** (model) |
| **R3** | 110 | 0.0273 | **0.0643** | [-0.0319, 0.0875] | 0.0638 | 0.0615 | **0.0638** (model) |

### Row 3 — units per topic after D3, and the cap

* **m̄ = 11**, median 12.0, min 5, max 12, cap 12
* cap-hit topics: **7** of 10 (rate 0.7)
* per topic: `{"2014_11": 12, "2014_29": 12, "2014_5": 11, "2015_18": 10, "2015_23": 12, "2015_8": 12, "2016_1": 12, "2016_13": 12, "2016_26": 5, "2016_9": 12}`

**D3 pipeline, counted at every step:**

| step | count |
|---|---|
| raw_sets | 391 |
| after_merge | 388 |
| after_containment | 388 |
| after_cap | 110 |
| no_localizable_evidence_pairs | 38 |
| dropped_pairs | 11 |
| pairs | 308 |
| windowed_pairs | 2 |
| cap_hit_topics | 7 |
| none_by_grade | {'1': 19, '2': 19} |

### Row 4 — measured per-topic binary discordance (`ES-Hit@4096`)

| contrast | discordant / n | **d** | Wilson 95% | d ≤ 0.025 at point? | at Wilson upper? | implied binary σ_d = √d |
|---|---|---|---|---|---|---|
| **N1** | 4 / 10 | **0.4000** | [0.1682, 0.6873] | NO | NO | 0.6325 |
| **N2** | 0 / 10 | **0.0000** | [0.0000, 0.2775] | YES | NO | 0.0000 |
| **R1** | 4 / 10 | **0.4000** | [0.1682, 0.6873] | NO | NO | 0.6325 |
| **R2** | 1 / 10 | **0.1000** | [0.0179, 0.4042] | NO | NO | 0.3162 |
| **R3** | 2 / 10 | **0.2000** | [0.0567, 0.5098] | NO | NO | 0.4472 |

### Row 5 — measured ρ_variant and the real variant-averaging divisor

| contrast | topics paired | **ρ_variant** | **measured divisor √(2/(1+ρ))** | assumed in rev. 1 | adaptation applies (≥ 1.15)? |
|---|---|---|---|---|---|
| **N1** | 10 | **-0.0944** | **1.4861** | 1.3 | YES |
| **N2** | 10 | **—** | **—** | 1.3 | NO |
| **R1** | 10 | **-0.7569** | **2.8682** | 1.3 | YES |
| **R2** | 10 | **1.0000** | **1.0000** | 1.3 | NO |
| **R3** | 10 | **-0.0476** | **1.4491** | 1.3 | YES |

### Row 6 — projected `n_retained` under the §8.5.6 exclusions

| criterion | applied when | measured |
|---|---|---|
| < 5 fetchable grade ≥ 1 documents | corpus assembly — **non-outcome data, computed EXACTLY on all 80** | **0 topics**  |
| < 3 evidence units | label freeze — projected from the dev rate | dev rate 0.0 (none) |
| > 1/3 of pairs failed quote verification | label freeze | dev rate 0.0 |
| majority windowed AND windowed union inconsistent | label freeze | dev rate 0.0 (majority-windowed: none) |

**Projected n_retained = 80** (nominal 80). n_retained < 60 gate: **not tripped**.

σ_d required for 80% power at ε = 0.05 (exact non-central t):

| n | σ_d for 80% power |
|---|---|
| 80 | 0.1577 |
| 76 | 0.1536 |
| 72 | 0.1494 |
| 68 | 0.145 |
| 64 | 0.1406 |
| 60 | 0.136 |

**The confirmation set's `n_rel` distribution** (non-outcome data, §2.3): min 8, median 96.5, max 854; **13 topics below the dev window's 40** and **12 above its 250** — 25 of 80 lie in strata the dev sample does not contain at all.

Dev `n_rel`: `{"2014_5": 133, "2014_11": 100, "2014_29": 85, "2015_8": 128, "2015_18": 135, "2015_23": 109, "2016_1": 128, "2016_9": 123, "2016_13": 152, "2016_26": 113}`

### Row 7 — power against Δ ∈ {0, 0.01, 0.02}, per contrast

| contrast | σ_d used | Δ = 0 | Δ = 0.01 | Δ = 0.02 | at n |
|---|---|---|---|---|---|
| **N1** (point estimate) | 0.0704 | 100.0% | 99.9% | 96.4% | 80 |
| **N1** (80% upper bound) | 0.0911 | 99.8% | 97.3% | 82.9% | 80 |
| **N2** (point estimate) | 0.0000 | 100.0% | 100.0% | 100.0% | 80 |
| **N2** (80% upper bound) | 0.0000 | 100.0% | 100.0% | 100.0% | 80 |
| **R1** (point estimate) | 0.0813 | 100.0% | 99.1% | 90.3% | 80 |
| **R1** (80% upper bound) | 0.1052 | 98.7% | 91.9% | 71.2% | 80 |
| **R2** (point estimate) | 0.0288 | 100.0% | 100.0% | 100.0% | 80 |
| **R2** (80% upper bound) | 0.0372 | 100.0% | 100.0% | 100.0% | 80 |
| **R3** (point estimate) | 0.0638 | 100.0% | 100.0% | 98.6% | 80 |
| **R3** (80% upper bound) | 0.0825 | 100.0% | 99.0% | 89.4% | 80 |

### The gate, per contrast (§8.5.5 three-outcome rule)

| contrast | σ_d point | σ_d 80% bound | requirement at n_retained | holds at point? | holds at bound? | **verdict** |
|---|---|---|---|---|---|---|
| **N1** | 0.0704 | 0.0911 | 0.1577 | yes | yes | **POWER GATE PASSES** |
| **N2** | 0.0000 | 0.0000 | 0.1577 | yes | yes | **POWER GATE PASSES** |
| **R1** | 0.0813 | 0.1052 | 0.1577 | yes | yes | **POWER GATE PASSES** |
| **R2** | 0.0288 | 0.0372 | 0.1577 | yes | yes | **POWER GATE PASSES** |
| **R3** | 0.0638 | 0.0825 | 0.1577 | yes | yes | **POWER GATE PASSES** |

### Row 8 — label validation

**Machine gates (P.5 / §6.4), computed:**

* hallucinated-span rate **0.0504** (24 / 476 spans, Wilson 95% upper 0.0739) — gate ≤ 0.05: **FAIL**
* self-consistency **0.3226** (10 / 31 duplicated pairs) — gate ≥ 0.90: **FAIL**
* minimality shrinkage 1.0000 (29 / 29 audited) — descriptive
* "no localizable evidence" verdicts: 38 / 308 pairs (rate 0.1234); by grade `{"1": {"pairs": 131, "none": 19}, "2": {"pairs": 177, "none": 19}}`
* dropped pairs: 11; windowed pairs: 2

**Human half — `PENDING-HUMAN`.** κ(human–human), κ(Scout–human), positive-class agreement and the `wrong-location` / `non-minimal` / `missed-evidence` rates all require the two-reader R-dev read of §6.6.2. **They were not computed and no agent read was substituted.** The ≥100-pair stratified draw is rendered and ready:

* draw: **100 pairs**, seed `20260915`, strata `{"model_positive": 48, "model_negative": 27, "deep_section": 3, "long_document": 22}` (shortfalls: `{"deep_section": {"wanted": 20, "available": 3}}`)
* artifacts: `work/RDEV-readsheet-A.html`, `work/RDEV-readsheet-B.html` (independently shuffled), `work/rdev_verdicts_{A,B}.csv`, `work/rdev_sample.json`

### Row 9 — manipulation checks (§7.6) and the dev EUC level

| check | requirement | measured | verdict |
|---|---|---|---|
| 1. GOLD packing control | EUC ≥ 0.95 | mean 1.0000, min 1.0000 | **PASS** |
| 2. NEGATIVE control (grade-0 only) | EUC ≤ 0.05 | `{"fixed_tok1024_ov0pct": 0.0, "fixed_tok2048_ov0pct": 0.0, "fixed_tok256_ov0pct": 0.0, "fixed_tok512": 0.0, "fixed_tok512_ov0pct": 0.0, "header512": 0.0, "parent256": 0.0}` | **PASS** |
| 3. discrimination | doc Hit@1 < 1.0; top-10 differ across size extremes for ≥ 25% of topics | Hit@1 `{"fixed_tok256_ov0pct": 0.5, "fixed_tok512_ov0pct": 0.5, "fixed_tok1024_ov0pct": 0.6, "fixed_tok2048_ov0pct": 0.5, "fixed_tok512": 0.3, "header512": 0.4}`; differ 10/10 | **PASS** |
| 4. budget bind | realised ∈ [0.85B, B] except rank-1 overshoot | see below | **FAIL** |
| 5. dev EUC in [0.15, 0.90] | floor/ceiling would destroy the variance estimate | `{"fixed_tok1024_ov0pct": 0.0691, "fixed_tok2048_ov0pct": 0.0358, "fixed_tok256_ov0pct": 0.0333, "fixed_tok512": 0.0167, "fixed_tok512_ov0pct": 0.0167, "header512": 0.025, "parent256": 0.025}` | **FAIL** |

**Check 4 detail (realised generator tokens at B = 4,096, `summary`):**

| arm | mean realised | in band | rank-1 overshoot | n |
|---|---|---|---|---|
| `fixed_tok1024_ov0pct` | 3504 | 3 | 0 | 10 |
| `fixed_tok2048_ov0pct` | 3269 | 0 | 0 | 10 |
| `fixed_tok256_ov0pct` | 3994.3 | 10 | 0 | 10 |
| `fixed_tok512` | 3918.8 | 10 | 0 | 10 |
| `fixed_tok512_ov0pct` | 3862.3 | 10 | 0 | 10 |
| `header512` | 3970.5 | 10 | 0 | 10 |
| `parent256` | 3712.6 | 8 | 0 | 10 |
