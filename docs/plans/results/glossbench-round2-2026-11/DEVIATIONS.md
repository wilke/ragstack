# Deviations from the frozen pre-registration: glossary-model benchmark, round 2

The pre-registration is frozen at commit 9e109c3 (PREREG-glossbench-round2.md, sha256
`39c579e1bc3aa7d80bffc6a93ef36157ce4c41c28ac3f37c939b6a5953cd4778`, frozen 2026-10-30T02:19:33Z; merge
dedc990). The file is never edited. Every change after the freeze is recorded here, dated, with what was
known when it was made.

## D-1, 2026-10-30T02:27:02Z: the set-P pilot is 100 records, not about 50

**What changes.** §3.4 (c) and the worked example in §3.5. The frozen text reads: *"About **50** records
from **3** papers (`--pilot 50 --papers 3`, papers seeded `<seed>|pilot`) are drafted, checked and sent
through the adapter before full set-P drafting"* (§3.4 c) and *"At n = 50 judged pilot records the rule
admits at most **2** rejects (upper bound .135; 3 of 50 gives .162)"* (§3.5). Under this entry the pilot
batch is drafted at **100 records** over 4–5 papers (`--pilot 100 --papers K`, K fixed in the drafting
command and recorded in `preflight-draft-P-pilot.json` under `plan.pilot`), and the papers-OC owner
judges **all 100** (every verdict `basis: sampled`, `sample.n` 100). The promotion condition is
unchanged: the Wilson 95% upper bound of the judged error rate must be ≤ 0.15, with at least 30 judged
records. The pilot's records remain data for no hypothesis. §3.4 (b), the judged sample of 50 for the
full set P, is not touched.

**The decision rule, stated so it cannot be read two ways.** 8 of 100 sits at the threshold (upper bound
0.149981), so the rule is fixed here before any verdict exists:

- the bound is computed **from the raw counts** (`n`, rejects) as the partner's verdict file gives them,
  with the two-sided z = **1.959963984540054** (`glossbench_r2_draft.wilson`, no continuity correction,
  no rounding) — never from a bound the partner reports (the partner quotes it to 4 decimals, 0.1500,
  beside the raw count; the 4-decimal figure is not the input);
- the condition is met **iff bound ≤ 0.15 at full float precision**, and n ≥ 30 (z = 1.96 gives
  0.149983 at 8 of 100, the same verdict, so the choice of z does not decide any count);
- the bound is recorded **to 6 decimals** in the §3.5 addendum, beside n, rejects and seed.

`import-partner-verdicts` on a pilot writes this as `promotion` in `partner-verdicts-P-pilot.json`
(`z`, `threshold`, `min_judged`, `upper_bound`, `upper_bound_6dp`, `met`; `promotion_condition`, with
the boundary test at exactly 8/100 → met and 9/100 → not met). The addendum quotes that block; it is not
re-derived by hand.

**Why.** After the freeze, the papers-OC owner pointed out that the bar and the sample size had not been
checked against each other. With the frozen rule:

| judged | rejects allowed | Wilson 95% upper bound at that count | first failing count |
|---|---|---|---|
| 30 | 0 | 0 → 0.113513 | 1 → 0.166704 |
| 50 | 2 | 2 → 0.134601 | 3 → 0.162171 |
| 100 | 8 | 8 → 0.149981 | 9 → 0.162262 |

(z = 1.959963984540054, as `glossbench_r2_draft.wilson` computes it; the same rows are asserted in
`test_promotion_condition_at_the_boundary`.)

At 50 judged records, an observed error rate of 6% fails. The outcome would then depend mostly on chance in
a small sample, not on the set's quality. At 100 records the same threshold tests the set rather than the
sample. The papers-OC owner offered to judge 100. The threshold itself, the 30-record floor, the hypotheses
added on promotion, and the "fail → replication, no further option" rule are all unchanged.

**What was known.** Nothing from round 2 had been run. No paper had been sampled, no record drafted, no
gateway call made, and no partner verdict existed. The owner chose this option on 2026-10-30. The other
options were keeping 50 records, and changing to a point-estimate rule; the latter was rejected because it
loosens the bar after the freeze.

**Effect on the design.** Drafting cost: about 100 pilot drafts instead of about 50, roughly +0.1 M gateway
input tokens (the plan's step 8b budgets ~0.1 M for 50). Judging cost: the partner's. The pilot is still
excluded from every hypothesis set. Code: the `promotion` block above is the one addition to
`import-partner-verdicts`; it computes the frozen rule and adds no choice. Every P run is at or after
the commit that carries it (§3.4 pins P runs at or after `b5251db`; this is later).
