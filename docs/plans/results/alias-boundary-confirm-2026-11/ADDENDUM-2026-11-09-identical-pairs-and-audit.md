# Addendum (post hoc): identical control chunks, and the gateway audit's independent checks, 2026-11-09

**Post hoc. Not pre-registered. It changes no frozen result.** [RESULTS.md](RESULTS.md) and its decision
sentence stand as committed. This file adds a sensitivity analysis, plus checks made from outside the study
by the gateway coordinator's audit.

## 1. Pairs where the chunking changed nothing

**How it was found.** The coordinator's audit noted request bodies sent twice, once in each arm. That points
to pairs whose control chunk is byte-identical to the stage-A window.

**Counts** (from [`artifacts/posthoc-identical-pairs.json`](artifacts/posthoc-identical-pairs.json), written
by [`posthoc_identical.py`](posthoc_identical.py) at `71e1055`, clean tree, `citable: true`):
- **56 of 351 pairs** have a control chunk whose text equals the window. All 56 have coverage 1.0: the
  window coincided with a section-bounded chunk.
- PREREG §4 does not exclude them, so the frozen primary counted them.

| | control *answers* | control *not* |
|---|---|---|
| **identical pairs (56)**, retest *answers* | 55 | 0 |
| **identical pairs**, retest *not* | 1 | 0 |

The identical pairs contribute **no b** and **one c**: one pair whose identical text the judge labelled
differently on the two reads. That is judge noise, and it can only push the test towards the null.

**Excluding them** (sensitivity only):

| | n | b | c | d | exact one-sided McNemar p |
|---|---|---|---|---|---|
| frozen primary, all pairs | 351 | 68 | 9 | 16.8 points | 1.22 × 10⁻¹² |
| changed pairs only | 295 | 68 | 8 | **20.3 points** | 2.8 × 10⁻¹³ |

The frozen primary is therefore conservative. Among pairs where the chunking actually changed the text, the
excess is about 20 points rather than 17.

**What this does not change.**
- The pre-registered estimand is defined over the drawn pairs, identical ones included. The confirmed
  sentence and its figures remain the result of record.
- A future pre-registration of a chunking contrast should define whether no-op pairs count.

## 2. Independent checks from the gateway audit

The gateway coordinator reconciled the study's gateway traffic against its own audit. It did this without
access to the study's registries.

- **Exact reconciliation.** The 110 calls of 2026-11-06 match the audit exactly: calls 1768–1877,
  contiguous, all `ok`, no empty outputs.

  | UTC hour | Argon calls | Opus calls |
  |---|---|---|
  | 14Z | 49 | 36 |
  | 15Z | 16 | 9 |

  The study's gateway total since stage A comes to about $9.2 at list prices.
- **Freshness.** None of the 110 request bodies appeared in any chat call of 2026-11-05. The condition that
  the pairs were fresh held.
- **No interim analysis.** The audit holds no chat (judge) call between the exploratory null's last call
  (2026-11-05T20:03:29Z) and the confirmatory batch (2026-11-06T14:29:30Z): 233 calls, none on the judge
  endpoint. The order was:
  1. freeze (frozen commit `016fb61` at 01:56:56Z; freeze recorded 02:47:50Z);
  2. draw (04:52Z);
  3. first judge call (14:29:30Z);
  4. `confirm-report` (14:34Z);
  5. cover-any batch (15:07Z).

  The frozen primary was computed before the cover-any calls existed. Cover-any is the descriptive step 7
  ([addendum](ADDENDUM-2026-11-06-cover-any.md)).
- **Planned n.** `n_draw = 351` is fixed in the frozen PREREG §3.3, and `draw-confirm` refuses any other
  value. The draw ran once.
- **Consistency of n with the traffic.** The Opus drift sample is 18 calls per arm, which is 5.13 % of 351:
  the pre-specified 5 %.
- **Direction of the clustering correction.** The record-clustered primary (p = 1.17 × 10⁻¹¹) is less
  significant than the unclustered exact binomial (P(X ≥ 68 | 77, ½) = 1.22 × 10⁻¹²). That is the right
  direction for 351 pairs over 218 questions.
- **Escalation rate.** On this agreed-*answers* stratum, 49 of 702 judgments (7.0 %) went to the tiebreak.
  Sizing should use a stratum-specific escalation rate.
