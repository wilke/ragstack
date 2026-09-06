*Hand-added preamble, 2026-09-06: every table in this file is computed over **n = 10 topics**
(the CDS pilot). Only Table 1's caption says so; the others inherit it.*

### Table 1 — the 24-config grid (summary queries, grade >= 1, dense, means over 10 topics)

`fill` = median realised tokens / nominal size. `c/doc` = chunks per document. Ordered by nDCG@10. **Descriptive: neighbouring rows differ by less than the noise floor and are not ordered claims.**

| rank | config | kind | size | ovl | c/doc | med tok | p95 | fill | nDCG@10 | R@10 | R@100 | MRR@10 |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `sentence_tok2048_ov12_5pct` | sentence | 2048 | 12.5% | 4.2 | 1977 | 2012 | 0.97 | **0.6289** | 0.0520 | 0.3888 | 0.7833 |
| 2 | `fixed_tok1024_ov0pct` | token_window | 1024 | 0% | 7.2 | 1024 | 1024 | 1.00 | **0.6206** | 0.0520 | 0.3618 | 0.8125 |
| 3 | `words_tok2048_ov12_5pct` | words | 2048 | 12.5% | 6.3 | 1307 | 1439 | 0.64 | **0.6081** | 0.0493 | 0.3783 | 0.8500 |
| 4 | `fixed_tok1024_ov12_5pct` | token_window | 1024 | 12.5% | 8.0 | 1024 | 1024 | 1.00 | **0.6034** | 0.0503 | 0.3622 | 0.7743 |
| 5 | `fixed_tok2048_ov12_5pct` | token_window | 2048 | 12.5% | 4.2 | 2048 | 2048 | 1.00 | **0.6000** | 0.0496 | 0.3833 | 0.8033 |
| 6 | `fixed_tok2048_ov0pct` | token_window | 2048 | 0% | 3.9 | 2048 | 2048 | 1.00 | **0.5942** | 0.0472 | 0.3816 | 0.8033 |
| 7 | `fixed_tok2048_ov25pct` | token_window | 2048 | 25% | 4.7 | 2048 | 2048 | 1.00 | **0.5905** | 0.0472 | 0.3832 | 0.8000 |
| 8 | `fixed_tok1024_ov25pct` | token_window | 1024 | 25% | 9.1 | 1024 | 1024 | 1.00 | **0.5794** | 0.0471 | 0.3605 | 0.7643 |
| 9 | `sentence_tok1024_ov12_5pct` | sentence | 1024 | 12.5% | 8.0 | 979 | 1005 | 0.96 | **0.5654** | 0.0464 | 0.3639 | 0.7611 |
| 10 | `sentence_tok256_ov12_5pct` | sentence | 256 | 12.5% | 32.5 | 229 | 251 | 0.89 | **0.5389** | 0.0461 | 0.3229 | 0.7750 |
| 11 | `words_tok1024_ov12_5pct` | words | 1024 | 12.5% | 12.3 | 656 | 726 | 0.64 | **0.5343** | 0.0447 | 0.3302 | 0.6611 |
| 12 | `fixed_tok256_ov0pct` | token_window | 256 | 0% | 27.4 | 256 | 256 | 1.00 | **0.5315** | 0.0453 | 0.3374 | 0.6750 |
| 13 | `sentence_tok512_ov12_5pct` | sentence | 512 | 12.5% | 15.8 | 479 | 502 | 0.94 | **0.5237** | 0.0433 | 0.3349 | 0.7333 |
| 14 | `fixed_tok512_ov0pct` | token_window | 512 | 0% | 14.0 | 512 | 512 | 1.00 | **0.4994** | 0.0410 | 0.3310 | 0.6893 |
| 15 | `fixed_tok256_ov12_5pct` | token_window | 256 | 12.5% | 31.2 | 256 | 256 | 1.00 | **0.4952** | 0.0438 | 0.3403 | 0.6111 |
| 16 | `semantic_tok2048_ov12_5pct` | semantic | 2048 | 12.5% | 14.3 | 343 | 2048 | 0.17 | **0.4912** | 0.0449 | 0.3224 | 0.5810 |
| 17 | `fixed_tok256_ov25pct` | token_window | 256 | 25% | 36.1 | 256 | 256 | 1.00 | **0.4869** | 0.0388 | 0.3407 | 0.7000 |
| 18 | `semantic_tok1024_ov12_5pct` | semantic | 1024 | 12.5% | 15.6 | 357 | 1024 | 0.35 | **0.4847** | 0.0439 | 0.3215 | 0.5786 |
| 19 | `fixed_tok512_ov25pct` | token_window | 512 | 25% | 18.1 | 512 | 512 | 1.00 | **0.4797** | 0.0401 | 0.3281 | 0.6560 |
| 20 | `semantic_tok256_ov12_5pct` | semantic | 256 | 12.5% | 34.5 | 255 | 256 | 1.00 | **0.4711** | 0.0420 | 0.3226 | 0.5708 |
| 21 | `fixed_tok512` | token_window | 512 | 12.5% | 15.7 | 512 | 512 | 1.00 | **0.4631** | 0.0371 | 0.3349 | 0.6750 |
| 22 | `words_tok256_ov12_5pct` | words | 256 | 12.5% | 48.3 | 164 | 188 | 0.64 | **0.4559** | 0.0408 | 0.3295 | 0.5933 |
| 23 | `words_tok512_ov12_5pct` | words | 512 | 12.5% | 24.3 | 328 | 369 | 0.64 | **0.4499** | 0.0383 | 0.3344 | 0.6087 |
| 24 | `semantic_tok512_ov12_5pct` | semantic | 512 | 12.5% | 20.4 | 359 | 512 | 0.70 | **0.4049** | 0.0373 | 0.3156 | 0.5593 |

### Table 2a — token_window size x overlap, ndcg@10 (summary, grade>=1, dense)

| size | 0% | 12.5% | 25% | E(s) = 25%-0% | chunks/doc @12.5% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.5315 | 0.4952 | 0.4869 | -0.0446 | 31.2 |
| 512 | 0.4994 | 0.4631 | 0.4797 | -0.0197 | 15.7 |
| 1024 | 0.6206 | 0.6034 | 0.5794 | -0.0412 | 8.0 |
| 2048 | 0.5942 | 0.6000 | 0.5905 | -0.0037 | 4.2 |

### Table 2b — token_window size x overlap, recall@100 (summary, grade>=1, dense)

| size | 0% | 12.5% | 25% | E(s) = 25%-0% | chunks/doc @12.5% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.3374 | 0.3403 | 0.3407 | +0.0033 | 31.2 |
| 512 | 0.3310 | 0.3349 | 0.3281 | -0.0030 | 15.7 |
| 1024 | 0.3618 | 0.3622 | 0.3605 | -0.0013 | 8.0 |
| 2048 | 0.3816 | 0.3833 | 0.3832 | +0.0016 | 4.2 |

### Table 3 — the pre-registered family (PREREG §3), primary condition

nDCG@10, grade >= 1, summary queries, dense. Bar X: |mean| >= 0.05 AND CI excludes 0 AND >= 7/10 signs AND survives Holm across these 9.

| contrast | mean | 95% CI | w/l | p | Holm p | resolved? |
|---|---:|---|---:|---:|---:|---|
| interaction I 256 minus 2048 | **-0.0409** | [-0.1729, +0.0793] | 5/5 | 0.5592 | 1.0000 | no |
| interaction slope per doubling | **+0.0101** | [-0.0221, +0.0443] | 5/5 | 0.5452 | 1.0000 | no |
| overlap 25 minus 0 | **-0.0273** | [-0.0701, +0.0218] | 3/7 | 0.2456 | 1.0000 | no |
| overlap 12 5 minus 0 | **-0.0210** | [-0.0472, +0.0072] | 3/7 | 0.1438 | 1.0000 | no |
| size 2048 minus 256 | **+0.0904** | [-0.0277, +0.2199] | 7/3 | 0.1400 | 1.0000 | no |
| size 256 minus 512 | **+0.0238** | [-0.0406, +0.0899] | 5/4 | 0.4948 | 1.0000 | no |
| sentence512 minus fixed512 | **+0.0606** | [+0.0138, +0.1072] | 7/2 | 0.0108 | 0.0972 | no |
| words512 minus fixed512 | **-0.0132** | [-0.1170, +0.0958] | 4/5 | 0.8004 | 1.0000 | no |
| semantic512 minus fixed512 | **-0.0583** | [-0.1430, +0.0275] | 3/6 | 0.1884 | 1.0000 | no |

Mean per-topic SD of the paired deltas across the family: **0.125**. At n = 10 and 80% power the smallest detectable effect is ~0.995 x SD = **0.124** nDCG@10 — the resolution floor of this design, and the reason a null here is not evidence of absence.

### Table 4 — the same family on the other metrics (DESCRIPTIVE, no Holm)

| contrast | recall@100 mean [CI] | recall@10 mean [CI] | mrr@10 mean [CI] |
|---|---|---|---|
| interaction I 256 minus 2048 | +0.0016 [-0.0174,+0.0185] | -0.0065 [-0.0192,+0.0043] | +0.0283 [-0.1583,+0.2033] |
| interaction slope per doubling | -0.0003 [-0.0060,+0.0058] | +0.0015 [-0.0013,+0.0047] | -0.0100 [-0.0623,+0.0423] |
| overlap 25 minus 0 | +0.0002 [-0.0105,+0.0095] | -0.0031 [-0.0071,+0.0007] | -0.0150 [-0.1050,+0.0921] |
| overlap 12 5 minus 0 | +0.0022 [-0.0046,+0.0095] | -0.0012 [-0.0036,+0.0012] | -0.0291 [-0.1207,+0.0806] |
| size 2048 minus 256 | +0.0432 [+0.0151,+0.0764] | +0.0054 [-0.0057,+0.0160] | +0.1402 [+0.0000,+0.3170] |
| size 256 minus 512 | +0.0081 [-0.0019,+0.0166] | +0.0032 [-0.0026,+0.0096] | -0.0114 [-0.1472,+0.0881] |
| sentence512 minus fixed512 | +0.0000 [-0.0064,+0.0064] | +0.0061 [-0.0004,+0.0141] | +0.0583 [-0.1167,+0.2333] |
| words512 minus fixed512 | -0.0005 [-0.0104,+0.0101] | +0.0012 [-0.0068,+0.0085] | -0.0663 [-0.2940,+0.1119] |
| semantic512 minus fixed512 | -0.0193 [-0.0344,-0.0034] | +0.0001 [-0.0060,+0.0066] | -0.1157 [-0.3550,+0.1286] |

### Table 5 — primary contrast under the other conditions (DESCRIPTIVE)

| condition | I = E(256) - E(2048) | 95% CI | w/l |
|---|---:|---|---:|
| summary, grade>=1 | -0.0409 | [-0.1731, +0.0754] | 5/5 |
| summary, grade>=2 | +0.0068 | [-0.0791, +0.0922] | 5/5 |
| description, grade>=1 | -0.0786 | [-0.1510, -0.0088] | 3/7 |
| description, grade>=2 | -0.0081 | [-0.0893, +0.0818] | 5/5 |

### Table 6 — reranked nDCG@10 (summary, grade>=1) — DESCRIPTIVE

Reranked numbers **rank arms; they do not grade the product** (step-3's label, kept): bge-reranker-v2-m3 sometimes lowers absolute nDCG against the SFR dense ordering on this clinical set.

| config | dense nDCG@10 | reranked nDCG@10 | delta |
|---|---:|---:|---:|
| `sentence_tok2048_ov12_5pct` | 0.6289 | 0.5906 | -0.0383 |
| `fixed_tok1024_ov0pct` | 0.6206 | 0.5243 | -0.0963 |
| `words_tok2048_ov12_5pct` | 0.6081 | 0.5300 | -0.0781 |
| `fixed_tok1024_ov12_5pct` | 0.6034 | 0.5553 | -0.0481 |
| `fixed_tok2048_ov12_5pct` | 0.6000 | 0.5780 | -0.0221 |
| `fixed_tok2048_ov0pct` | 0.5942 | 0.5995 | +0.0053 |
| `fixed_tok2048_ov25pct` | 0.5905 | 0.6267 | +0.0362 |
| `fixed_tok1024_ov25pct` | 0.5794 | 0.5499 | -0.0294 |
| `sentence_tok1024_ov12_5pct` | 0.5654 | 0.5846 | +0.0193 |
| `sentence_tok256_ov12_5pct` | 0.5389 | 0.4214 | -0.1176 |
| `words_tok1024_ov12_5pct` | 0.5343 | 0.5029 | -0.0314 |
| `fixed_tok256_ov0pct` | 0.5315 | 0.5102 | -0.0213 |
| `sentence_tok512_ov12_5pct` | 0.5237 | 0.4945 | -0.0292 |
| `fixed_tok512_ov0pct` | 0.4994 | 0.5446 | +0.0452 |
| `fixed_tok256_ov12_5pct` | 0.4952 | 0.4315 | -0.0637 |
| `semantic_tok2048_ov12_5pct` | 0.4912 | 0.5349 | +0.0436 |
| `fixed_tok256_ov25pct` | 0.4869 | 0.5714 | +0.0845 |
| `semantic_tok1024_ov12_5pct` | 0.4847 | 0.5314 | +0.0467 |
| `fixed_tok512_ov25pct` | 0.4797 | 0.4983 | +0.0186 |
| `semantic_tok256_ov12_5pct` | 0.4711 | 0.5450 | +0.0739 |
| `fixed_tok512` | 0.4631 | 0.5508 | +0.0877 |
| `words_tok256_ov12_5pct` | 0.4559 | 0.4475 | -0.0083 |
| `words_tok512_ov12_5pct` | 0.4499 | 0.4527 | +0.0028 |
| `semantic_tok512_ov12_5pct` | 0.4049 | 0.4634 | +0.0585 |

### Table 7 — measured embedding cost

| config | chunks | tokens (M) | embed s | tok/s | requests | retries |
|---|---:|---:|---:|---:|---:|---:|
| `sentence_tok2048_ov12_5pct` | 16916 | 29.7 | 182 | 164k | 3977 | 0 |
| `fixed_tok1024_ov0pct` | 29280 | 27.9 | 168 | 166k | 3554 | 0 |
| `words_tok2048_ov12_5pct` | 25730 | 31.7 | 193 | 164k | 4232 | 0 |
| `fixed_tok1024_ov12_5pct` | 32582 | 31.6 | 193 | 163k | 4000 | 0 |
| `fixed_tok2048_ov12_5pct` | 17046 | 31.2 | 194 | 161k | 4095 | 0 |
| `fixed_tok2048_ov0pct` | 15663 | 27.9 | 145 | 192k | 3701 | 0 |
| `fixed_tok2048_ov25pct` | 18858 | 35.5 | 220 | 161k | 4614 | 0 |
| `fixed_tok1024_ov25pct` | 37036 | 36.4 | 222 | 163k | 4585 | 0 |
| `sentence_tok1024_ov12_5pct` | 32268 | 29.6 | 179 | 165k | 3828 | 0 |
| `sentence_tok256_ov12_5pct` | 131893 | 28.4 | 190 | 149k | 8244 | 0 |
| `words_tok1024_ov12_5pct` | 49976 | 31.9 | 193 | 166k | 4061 | 0 |
| `fixed_tok256_ov0pct` | 111249 | 28.0 | 176 | 158k | 6954 | 0 |
| `sentence_tok512_ov12_5pct` | 63866 | 29.0 | 174 | 167k | 3992 | 0 |
| `fixed_tok512_ov0pct` | 56561 | 27.9 | 170 | 164k | 3536 | 0 |
| `fixed_tok256_ov12_5pct` | 126302 | 31.9 | 196 | 163k | 7894 | 0 |
| `semantic_tok2048_ov12_5pct` | 58040 | 28.8 | 162 | 178k | 4347 | 0 |
| `fixed_tok256_ov25pct` | 146445 | 37.1 | 229 | 162k | 9153 | 0 |
| `semantic_tok1024_ov12_5pct` | 63221 | 28.8 | 173 | 167k | 4416 | 0 |
| `fixed_tok512_ov25pct` | 73394 | 36.8 | 224 | 164k | 4588 | 0 |
| `semantic_tok256_ov12_5pct` | 139999 | 28.9 | 193 | 150k | 8750 | 0 |
| `fixed_tok512` | 63806 | 31.8 | 194 | 164k | 3988 | 0 |
| `words_tok256_ov12_5pct` | 195939 | 32.1 | 268 | 120k | 12247 | 0 |
| `words_tok512_ov12_5pct` | 98655 | 32.0 | 193 | 166k | 6166 | 0 |
| `semantic_tok512_ov12_5pct` | 82700 | 28.9 | 173 | 167k | 5169 | 0 |
| **total (chunk embed)** | | **744** | **4606** | **161k** | | |

### Table 7b — the semantic BREAKPOINT pass (the cost the brief's model missed)

`semantic` embeds one overlapping 7-sentence buffer per sentence for boundary detection, on top of the chunk embed in Table 7. **notional** = what a production ingest of that one config would pay (use this for stage-2 projections); **actual** = what was sent after the per-document cache shared across the four sizes.

| config | notional tok (M) | actual tok (M) | notional items | actual items | x corpus (notional) |
|---|---:|---:|---:|---:|---:|
| `semantic_tok2048_ov12_5pct` | 148.6 | 148.6 | 546209 | 546126 | 6.0x |
| `semantic_tok1024_ov12_5pct` | 148.5 | 0.6 | 546209 | 558 | 6.0x |
| `semantic_tok512_ov12_5pct` | 146.3 | 7.9 | 546209 | 15471 | 5.9x |
| `semantic_tok256_ov12_5pct` | 124.0 | 67.5 | 546209 | 264760 | 5.0x |
| **all 4** | **567** | **225** | 2184836 | 826915 | |

Breakpoint pass wall-clock **17.7 min**, 212k actual tok/s (56556 requests, 0 retries). That rate is **not** comparable to the 164k tok/s chunk-embed model: the request shape is different (~271-token buffer texts, 16 per request, so a request carries ~4.3k tokens instead of ~8.2k) and this pass is request-bound, not token-bound. 12 of 4053 documents exceeded `max_breakpoint_sentences=3000` and were chunked by the fixed_token fallback inside the semantic arm (median 115 sentences/doc).

### Table 8 — the pre-registered predictions, scored automatically

| # | prediction (PREREG §9) | outcome | verdict |
|---|---|---|---|
| P5 | overlap engages at 2048 (chunks/doc >> 1) | 4.21 chunks/doc at 2048/12.5% | **HOLDS** — the primary contrast is valid |
| P2 | size effect replicates: M(2048)-M(256) >= 0.05, CI excl. 0 | +0.0904, CI [-0.0277,+0.2199], 7/3 signs, Holm p 1.000 | **FALSIFIED** |
| P3 | the 256-vs-512 step resolves as NOISE | +0.0238, CI [-0.0406,+0.0899], 5/4 signs, Holm p 1.000 | **HOLDS** — resolved as noise |
| P4 | interaction I > 0 but < 0.05 (sub-threshold) | -0.0409, CI [-0.1729,+0.0793], 5/5 signs, Holm p 1.000 | **HOLDS in magnitude, WRONG SIGN** |

**Answer to the stage-1 question:** **NO detectable size x overlap interaction at this resolution.**
