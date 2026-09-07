# Stage 0b′ — the Claude judge family: gates and cross-judge agreement

One reading per pair (presentation k = 0, natural unit order), r3.1's prompt (sha256 `ba09e12255383321…`), r3.1's locator.

| statistic | requirement | **sonnet5** | **opus5** | **fable51** |
|---|---|---|---|---|
| model | — | `claude-sonnet-5` | `claude-opus-5` | `claude-fable-5-1` |
| pairs labeled / 308 | 308 | **308** | **308** | **251** ⚠ stopped |
| **hallucinated-span rate** | ≤ 0.05 | **0.0019** (1/525 spans; Wilson 95 % upper 0.01071) **PASS** | **0.00105** (1/955 spans; Wilson 95 % upper 0.00591) **PASS** | **0.0** (0/645 spans; Wilson 95 % upper 0.00592) **PASS** |
|   — split by anchor (first-sentence / last-sentence quote) | reported (#501, ten-word anchors: 4 / 54 for scout) | 1 / 0 | 1 / 0 | 0 / 0 |
|   — quotes rescued by the eight-word ladder (first / last) | descriptive | 0 / 0 | 1 / 0 | 0 / 0 |
|   — quotes located byte-exact (first anchor) | descriptive | 509/525 | 914/955 | 633/645 |
| served-model mismatches (a call `modelUsage` says another model answered) | 0 | **0**/360 calls | **0**/362 calls | **2**/251 calls |
| **self-consistency** | ≥ 0.90 | **ABSENT** — one reading per pair | **ABSENT** — one reading per pair | **ABSENT** — one reading per pair |
| **whether-agreement across presentations** | ≥ 0.90 | **ABSENT** — one presentation | **ABSENT** — one presentation | **ABSENT** — one presentation |
| “no localizable evidence” rate | descriptive | 0.1006 (31/308 pairs) | 0.0065 (2/308 pairs) | 0.012 (3/251 pairs) |
| pairs dropped (no verified span survived) | descriptive | 0/308 | 0/308 | 0/251 |
| pairs re-prompted (§6.4 rule 2) | descriptive | 1/308 | 3/308 | 0/251 |
| mean evidence **sets** per positive pair | descriptive | **1.8375** (n=277) | **3.0163** (n=306) | **2.4677** (n=248) |
| mean **spans** per positive pair | descriptive | **1.8845** | **3.1111** | **2.6008** |
| median span length (sentences / characters) | descriptive | 1.0 / 195.0 (n=522 spans) | 1.0 / 221.0 (n=952 spans) | 1 / 214 (n=645 spans) |
| spans split across a unit boundary | descriptive | 0 | 0 | 0 |
| quote landed inside the unit whose title it named | descriptive | 508/508 | 947/947 | 644/644 |
| pairs whose every span is in the abstract | descriptive | 77/277 | 36/306 | 28/248 |
| deep-section pairs (every span in unit ≥ 2) | descriptive (Stage 0: 3/308) | 105/277 | 52/306 | 44/248 |

## Cost and latency

| model | pairs | total cost | $/pair | median latency | mean latency | wall | input tok | output tok |
|---|---|---|---|---|---|---|---|---|
| `claude-sonnet-5` | 308 | **$40.8278** | $0.1326 | 12225.5 ms | 17732.7987 ms | 1986.2 s | 720 | 528795 |
| `claude-opus-5` | 308 | **$93.1172** | $0.3023 | 12211.0 ms | 15682.539 ms | 1771.1 s | 724 | 445755 |
| `claude-fable-5-1` | 251 | **$61.3743** | $0.2445 | 11417 ms | 12041.6454 ms | 401.4 s | 132 | 84003 |

Token counters are the CLI's: `input_tokens` is the uncached portion, and a prompt served from the prompt cache is billed under `cache_read_input_tokens` instead. Cost is `total_cost_usd`, which also carries the CLI's fixed ancillary `claude-haiku-4-5` call (≈ 900 input tokens per call, unchanged by the size of our prompt) — it is harness overhead, not a judge, and it is inside the cost but outside every isolation and agreement number below.

## Per-sentence support under the local judges' pooled 30-reading map

Support of a sentence = how many of the **30** local readings (qwen k=0..9 + scout k=0..19) placed evidence on it, 0…30. The baseline is the mean support of every sentence the local pool touched at all (support ≥ 1) on the same pairs. Source: `/rag/tmp/stage0-conf/work/r31ext`.

| model | pairs | sentences picked | mean support of picked | baseline (all locally touched) | lift | picked with support 0 | mean support excl. 0 |
|---|---|---|---|---|---|---|---|
| `claude-sonnet-5` | 277 | 626 | **10.7843** | 2.8634 | **+7.9209** | 79 (0.1262) | 12.3419 |
| `claude-opus-5` | 306 | 1327 | **9.1771** | 2.8634 | **+6.3137** | 288 (0.217) | 11.7209 |
| `claude-fable-5-1` | 248 | 825 | **10.8024** | 5.3469 | **+5.4555** | 137 (0.1661) | 12.9535 |

Sentence-level correlation between *this judge picked the sentence* (0/1) and the sentence's pooled local support, over the union of the locally-supported sentences and the judge's own picks:

| model | sentences | pooled ρ (Spearman) | pooled r (point-biserial) | per-pair mean ρ | pairs with a defined ρ |
|---|---|---|---|---|---|
| `claude-sonnet-5` | 40667 | **0.1353** | 0.2353 | 0.1481 | 274 (+33 with no variance) |
| `claude-opus-5` | 40876 | **0.1279** | 0.2758 | 0.1165 | 302 (+5 with no variance) |
| `claude-fable-5-1` | 15044 | **0.1238** | 0.2383 | 0.1204 | 246 (+4 with no variance) |

For scale, the same statistic computed on the local judges' own k = 0 readings (each of which is one of the 30 readings that *builds* the support map, so these are self-referential upper anchors, not comparables):

| local judge (k = 0) | sentences picked | mean support of picked | baseline | lift |
|---|---|---|---|---|
| scout | 3678 | 10.972 | 2.8634 | +8.1086 |
| qwen | 495 | 15.2808 | 2.8634 | +12.4174 |

## Per-sentence support under r3.1's own ten-reading pool

Support of a sentence = how many of the **10** local readings (scout k=0..4 + qwen k=0..4) placed evidence on it, 0…10. The baseline is the mean support of every sentence the local pool touched at all (support ≥ 1) on the same pairs. Source: `/rag/tmp/stage0-conf/work/r31`.

| model | pairs | sentences picked | mean support of picked | baseline (all locally touched) | lift | picked with support 0 | mean support excl. 0 |
|---|---|---|---|---|---|---|---|
| `claude-sonnet-5` | 277 | 626 | **3.4617** | 2.1476 | **+1.314** | 153 (0.2444) | 4.5814 |
| `claude-opus-5` | 306 | 1327 | **2.8011** | 2.1476 | **+0.6534** | 446 (0.3361) | 4.2191 |
| `claude-fable-5-1` | 248 | 825 | **3.3321** | 2.1742 | **+1.1579** | 212 (0.257) | 4.4845 |

Sentence-level correlation between *this judge picked the sentence* (0/1) and the sentence's pooled local support, over the union of the locally-supported sentences and the judge's own picks:

| model | sentences | pooled ρ (Spearman) | pooled r (point-biserial) | per-pair mean ρ | pairs with a defined ρ |
|---|---|---|---|---|---|
| `claude-sonnet-5` | 11696 | **0.0725** | 0.2012 | 0.1245 | 273 (+34 with no variance) |
| `claude-opus-5` | 11989 | **-0.0002** | 0.1616 | 0.0564 | 299 (+8 with no variance) |
| `claude-fable-5-1` | 9890 | **0.0786** | 0.2267 | 0.0766 | 243 (+7 with no variance) |

For scale, the same statistic computed on the local judges' own k = 0 readings (each of which is one of the 10 readings that *builds* the support map, so these are self-referential upper anchors, not comparables):

| local judge (k = 0) | sentences picked | mean support of picked | baseline | lift |
|---|---|---|---|---|
| scout | 3678 | 3.1852 | 2.1476 | +1.0375 |
| qwen | 495 | 5.3939 | 2.1476 | +3.2463 |

## Agreement with the local judges

| Claude judge | local side | co-labeled | κ(whether) | Jaccard where both + (mean / median / ≥ 0.5) | Claude chars inside local | local chars inside Claude |
|---|---|---|---|---|---|---|
| `sonnet5` | scout k=0 | 308 | **0.2466** (obs 0.8734) | 0.1588 / 0.0421 / 0.0885 (n=260) | 0.4209 | 0.1923 |
| `sonnet5` | scout k=5 union | 308 | **0.2751** (obs 0.9026) | 0.0988 / 0.0504 / 0.0185 (n=271) | 0.6799 | 0.1094 |
| `sonnet5` | qwen k=0 | 308 | **0.2456** (obs 0.9123) | 0.3785 / 0.2138 / 0.3551 (n=276) | 0.4066 | 0.5123 |
| `sonnet5` | qwen k=5 union | 308 | **0.0566** (obs 0.9026) | 0.3767 / 0.3167 / 0.278 (n=277) | 0.6213 | 0.4572 |
| `sonnet5` | scout ∪ qwen, ten-reading union | 308 | **0.0566** (obs 0.9026) | 0.115 / 0.0713 / 0.0253 (n=277) | 0.8255 | 0.1257 |
| `opus5` | scout k=0 | 308 | **0.1324** (obs 0.9221) | 0.1923 / 0.135 / 0.0745 (n=282) | 0.3937 | 0.3293 |
| `opus5` | scout k=5 union | 308 | **0.2583** (obs 0.9643) | 0.1559 / 0.1053 / 0.0475 (n=295) | 0.6686 | 0.1829 |
| `opus5` | qwen k=0 | 308 | **0.4951** (obs 0.987) | 0.2736 / 0.2293 / 0.1788 (n=302) | 0.2806 | 0.6733 |
| `opus5` | qwen k=5 union | 308 | **0.6652** (obs 0.9968) | 0.3568 / 0.3184 / 0.2582 (n=306) | 0.4688 | 0.5988 |
| `opus5` | scout ∪ qwen, ten-reading union | 308 | **0.6652** (obs 0.9968) | 0.1717 / 0.1168 / 0.0556 (n=306) | 0.732 | 0.2 |
| `fable51` | scout k=0 | 251 | **0.2855** (obs 0.9442) | 0.1871 / 0.1377 / 0.0769 (n=234) | 0.4035 | 0.2903 |
| `fable51` | scout k=5 union | 251 | **0.4515** (obs 0.9721) | 0.1322 / 0.0801 / 0.0332 (n=241) | 0.6655 | 0.1544 |
| `fable51` | qwen k=0 | 251 | **0.7462** (obs 0.992) | 0.2864 / 0.2711 / 0.1951 (n=246) | 0.3023 | 0.6473 |
| `fable51` | qwen k=5 union | 251 | **0.497** (obs 0.992) | 0.3639 / 0.3214 / 0.2702 (n=248) | 0.4872 | 0.5766 |
| `fable51` | scout ∪ qwen, ten-reading union | 251 | **0.497** (obs 0.992) | 0.1455 / 0.0971 / 0.0282 (n=248) | 0.749 | 0.1661 |

## Agreement among the three Claude judges

| pair | co-labeled | κ(whether) | observed agreement | Jaccard where both + (mean / median / ≥ 0.5) |
|---|---|---|---|---|
| sonnet5 vs opus5 | 308 | **0.1104** | 0.9058 | 0.4179 / 0.3931 / 0.3141 (n=277) |
| sonnet5 vs fable51 | 251 | **0.234** | 0.9283 | 0.4568 / 0.4199 / 0.4 (n=230) |
| opus5 vs fable51 | 251 | **0.7981** | 0.996 | 0.5041 / 0.4545 / 0.4435 (n=248) |

## What this run cannot say

**Self-consistency, five-presentation whether-agreement and union saturation are ABSENT for this family, not zero and not null.** They each need more than one reading of a pair and this run bought one. Every gate the r3.1 local judges failed on self-consistency is therefore untested here: nothing in this file says a Claude judge would have passed it.

*κ(human–human), κ(judge–human), the wrong-location / non-minimal / missed-evidence rates and enumeration recall against human-marked sets remain `PENDING-HUMAN` — they require the two-reader R-dev read of §6.6.2. No agent read was substituted and none was performed. The R-dev pairs were not read by any judge in this run.*
