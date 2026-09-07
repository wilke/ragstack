# Pre-registration — r3.1 extension (Scout ×20, Qwen ×10)

**Written and committed before the first new label was generated.** Every number below is
computed from the **committed r3.1 labels** (`artifacts/r31/labels-r31-{scout,qwen}.jsonl`,
#507) by `s0_labelgates_r31ext.py --prefit`, which is committed in the same change, so the
projections are reproducible and were not chosen after seeing the answer. The machine-readable
copy the scorer reads is `predictions.json` beside this file; it is not edited after this
commit.

**What the run is.** The same 308 development pairs (208 pooled + 100 bias-bound), the same
two judges on `mango:8003` / `mango:8004`, the same prompt revision 3.1 (sha256
`ba09e122553833219a999f8a99c496fd2926b2b341f77026dfe7c6ab2f5131c7`, asserted equal to r3.1's
manifest before the first call), the same seed formula `SEED_LABELDUP + 100·k + pair_index`,
temperature 0, ≤ 4 in flight per endpoint. Presentations **k = 5..19** for Scout and
**k = 5..9** for Qwen; r3.1's k = 0..4 records are **read, not regenerated**.

**What it measures.** r3 §10 item 4 puts three options on the table for what "where" means on
the CDS population; the owner is pursuing all three. This run supplies the data for
option (a) — *graded* per-sentence support as the gold — and tests option (c)'s implicit
claim that more presentations converge on a canonical span set.

---

## P-ext-1 — graded support becomes reliable at 30 readings

> The split-half reliability of **graded per-sentence support** — for each sentence, the
> fraction of readings that include it, both judges pooled — Spearman–Brown-corrected to the
> full instrument, is **≥ 0.85 at 30 readings**.

**Estimator, fixed here.** Per pair: take the first *n* readings in the pooled interleaved
order (s0, q0, s1, q1, …, then the remaining Scout readings); the universe is the sentences
selected by at least one of those *n* readings; split the *n* readings at random into two
halves of *n*/2; score each sentence in each half by the fraction of that half's readings that
include it; Pearson *r* across the pair's sentences; **mean over pairs**; 20 seeded random
splits; Spearman–Brown doubling gives the full-*n* reliability. A sentence is identified as
`(unit index, sentence index)` straight off the label record.

**Basis.** The reviewer measured split-half *r* = **0.556** at n = 10 ⇒ full-10 reliability
**0.714** ⇒ projected **0.88** at 30. This module reproduces that measurement on the committed
labels at *r* = **0.5444**, SB full-10 **0.7050**, which projects to **0.877** at 30
(m = 3: 3·0.705/(1+2·0.705)). Either way the prediction is a genuine bet: 0.85 sits below the
projection but above the 0.71 that ten readings buy today.

**Scored** against the observed Spearman–Brown full-30 value. Threshold **0.85**.

---

## P-ext-2 — Scout's sentence union at k = 20 is within 10 % of the asymptote

> The Scout-only union of **distinct evidence sentences** at k = 20 is within 10 % of the
> asymptote of a Michaelis–Menten fit to Scout's existing k = 1..5 curve.

**The fit, computed now.** U(k) = V<sub>max</sub>·k/(K<sub>m</sub>+k), least squares on
Scout's committed k = 1..5 curve (mean distinct sentences per pair, over the 295 pairs positive
somewhere in the five):

| k | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Scout, distinct sentences | 12.4678 | 22.7424 | 27.5051 | 34.1559 | 37.4441 |

**V<sub>max</sub> = 72.3277**, **K<sub>m</sub> = 4.6179**, r² = **0.9942**.

**The number written down before generating anything: projected U(20) = 58.7603**, which is
**81.2 %** of the asymptote. Within 10 % of the asymptote therefore requires an observed
**U(20) ≥ 65.0949**.

**Stated plainly, because pre-registration is for this:** the fit's own arithmetic says k = 20
reaches only 81 % of V<sub>max</sub>, so **the prediction as written is expected to FAIL**
unless the real curve flattens faster than Michaelis–Menten. It is registered as written and
will be scored as written. The informative secondary — registered here, scored beside it — is
the **accuracy of the projection**: |U(20)<sub>observed</sub> − 58.7603| / 58.7603 ≤ 0.10. A
pass there says the five-point fit extrapolated four-fold correctly; a miss says the shape is
wrong, and the direction of the miss says which way.

For the record, the same fit on Qwen's k = 1..5 sentence curve is V<sub>max</sub> = 6.1704,
K<sub>m</sub> = 3.0750 (r² = 0.9932), projecting U(10) = 4.72.

---

## P-ext-3 — decision C holds under reshuffled unit orders

> The hallucinated-span rate at the **new** presentations (Scout k = 5..19, Qwen k = 5..9)
> stays **≤ 0.05** for both judges.

**Basis.** r3.1's all-presentation rates were 0.01957 (Scout, 85/4,344 spans) and 0.00888
(Qwen, 16/1,802). Whole-sentence anchors (r3 §10 item 3, decision C) fixed the copy gate; the
only thing that changes here is the unit order, which the model does not quote from. A rate
above 0.05 on the new presentations would mean the copy behaviour is order-dependent, which
nothing in r3.1 suggests.

**Scored** on the new presentations only, per judge, with Wilson 95 % intervals reported.

---

## Stop rules, also pre-registered

Evaluated on the smoke (20 pairs × 1 new presentation per judge) before the full run is
launched: **projected wall > 6 h** ⇒ stop; **served model id ≠** `s0_common.SCOUT_EXPECT` /
`QWEN_EXPECT` ⇒ stop; **smoke hallucinated-span rate > 0.5** ⇒ stop (the prompt is broken).
The prompt sha256 is asserted equal to r3.1's manifest value before the first call; a mismatch
is a stop, not a deviation.

## What this run does *not* do

No human read, no κ against a human, no enumeration recall — those are `PENDING-HUMAN` and
stay so. The R-dev pairs are not read. No confirmation topic is touched. No store client is
constructed and no endpoint other than `mango:8003` / `mango:8004` is contacted.
