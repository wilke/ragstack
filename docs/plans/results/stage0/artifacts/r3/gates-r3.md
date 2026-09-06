# Stage 0b' — machine label gates under the revision-3 protocol

| gate | requirement | **scout** | **qwen** |
|---|---|---|---|
| **self-consistency** (span-union Jaccard, 10 % re-presented) | ≥ 0.90 | **0.6452** (20/31) **FAIL** | **0.4194** (13/31) **FAIL** |
|   — reading: first set only | reported | 0.7742 | 0.4194 |
|   — reading: best-matching set pair | reported | 0.9032 | 0.4194 |
| **hallucinated-span rate** | ≤ 0.05 | **0.08204** (58/707 spans; Wilson 95 % upper 0.10459) **FAIL** | **0.02528** (9/356 spans; Wilson 95 % upper 0.04734) **PASS** |
|   — of which the *last*-ten-words anchor | reported | 54 (first-words anchor: 4) | 7 (first-words anchor: 2) |
| **document-level whether-agreement** (new, r3 §3.7) | ≥ 0.90 | **0.9677** (30/31) **PASS** | **0.9677** (30/31) **PASS** |
| “no localizable evidence” rate | descriptive | 0.0812 (25/308 pairs) | 0.013 (4/308 pairs) |
| spans emitted / attempted | descriptive | 689 / 707 | 347 / 356 |
| spans split across a unit boundary | descriptive | 14 | 0 |
| quotes ambiguous (>1 occurrence) | descriptive | 104 | 30 |
| quote landed inside the unit whose title it named | descriptive | 647/679 | 335/335 |
| pairs dropped (no verified span survived) | descriptive | 1/308 | 3/308 |
| mean evidence sets / spans per positive pair | descriptive | 1.809 / 2.096 (n=282) | 1.153 / 1.153 (n=301) |
| pairs whose every span is in the abstract | descriptive | 168/282 | 183/301 |
| deep-section pairs (every span outside abstract + first body unit) | descriptive (Stage 0: 3/308) | 6/282 | 80/301 |
| **ALL THREE GATES** | conjunctive | **FAIL** | **FAIL** |

## Cross-judge agreement (scout vs qwen)

| statistic | value |
|---|---|
| co-labeled pairs | 308 |
| κ(scout–qwen), pair-level binary evidence/none | **0.34** |
| observed / expected agreement | 0.9318 / 0.8967 |
| confusion (both+ / both− / scout-only+ / qwen-only+) | 281 / 6 / 1 / 20 |
| span-union Jaccard where both positive | mean **0.1605**, median 0.0793, ≥ 0.5 on 0.0676 of 281 pairs |
| asymmetric coverage (scout's chars inside qwen's / qwen's inside scout's) | **0.1622** / **0.6592** (n=281) |

## Decision — r3 §5 step 2

**NEITHER JUDGE PASSES — stop per r3 §5 step 2.** The labeler is wrong, not unstable, and the protocol needs redesign, not another run.

*κ(human–human) and κ(judge–human) are `PENDING-HUMAN`: they require the two-reader R-dev read of §6.6.2. No agent read was substituted and none was performed.*
