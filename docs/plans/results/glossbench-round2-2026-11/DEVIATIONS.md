# Deviations from the frozen pre-registration: glossary-model benchmark, round 2

The pre-registration is frozen at commit 9e109c3 (PREREG-glossbench-round2.md, sha256
`39c579e1bc3aa7d80bffc6a93ef36157ce4c41c28ac3f37c939b6a5953cd4778`, frozen 2026-10-30T02:19:33Z; merge
dedc990). The file is never edited. Every change after the freeze is recorded here, dated, with what was
known when it was made.

## D-1, 2026-10-30T02:27:02Z: the set-P pilot is 100 records, not about 50

**What changes.** §3.4–§3.5: the set-P pilot batch is drafted at **100 records** (over 4–5 papers) instead
of about 50 (over 2–3 papers), and the papers-OC owner judges **all 100**. The promotion condition is
unchanged: the Wilson 95% upper bound of the judged error rate must be ≤ 0.15, with at least 30 judged
records. The pilot's records remain data for no hypothesis.

**Why.** After the freeze, the papers-OC owner pointed out that the bar and the sample size had not been
checked against each other. With the frozen rule:

| judged | rejects allowed | Wilson 95% upper bound at that count | first failing count |
|---|---|---|---|
| 30 | 0 | 0 → 0.114 | 1 → 0.167 |
| 50 | 2 | 2 → 0.135 | 3 → 0.162 |
| 100 | 8 | 8 → 0.150 | 9 → 0.162 |

At 50 judged records, an observed error rate of 6% fails. The outcome would then depend mostly on chance in
a small sample, not on the set's quality. At 100 records the same threshold tests the set rather than the
sample. The papers-OC owner offered to judge 100. The threshold itself, the 30-record floor, the hypotheses
added on promotion, and the "fail → replication, no further option" rule are all unchanged.

**What was known.** Nothing from round 2 had been run. No paper had been sampled, no record drafted, no
gateway call made, and no partner verdict existed. The owner chose this option on 2026-10-30. The other
options were keeping 50 records, and changing to a point-estimate rule; the latter was rejected because it
loosens the bar after the freeze.

**Effect on the design.** Drafting cost: about 100 pilot drafts instead of about 50, roughly +0.1 M gateway
input tokens. Judging cost: the partner's. The pilot is still excluded from every hypothesis set.
