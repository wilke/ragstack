### Table 1 — the 24-config grid, Leg B rung ×0 (judged-only, 400 docs), n = 396 all queries, dense

Single relevant document per query; `nDCG@10 = 1/log2(rank+1)`. `fill` = median realised tokens / nominal size. **Descriptive — neighbouring rows differ by less than the noise floor and are not ordered claims.**

| rank | config | kind | size | ovl | c/doc | med tok | fill | nDCG@10 | R@10 | R@100 | MRR@10 | rr nDCG@10 |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov12_5pct` | token_window | 256 | 12.5% | 43.6 | 256 | 1.00 | **0.9884** | 0.9949 | 1.0000 | 0.9861 | nan |
| 2 | `sentence_tok256_ov12_5pct` | sentence | 256 | 12.5% | 45.0 | 229 | 0.89 | **0.9882** | 0.9949 | 1.0000 | 0.9859 | nan |
| 3 | `words_tok256_ov12_5pct` | words | 256 | 12.5% | 67.3 | 164 | 0.64 | **0.9881** | 0.9975 | 1.0000 | 0.9849 | nan |
| 4 | `fixed_tok256_ov0pct` | token_window | 256 | 0% | 38.3 | 256 | 1.00 | **0.9873** | 0.9949 | 1.0000 | 0.9848 | nan |
| 5 | `semantic_tok256_ov12_5pct` | semantic | 256 | 12.5% | 48.9 | 255 | 1.00 | **0.9860** | 0.9924 | 1.0000 | 0.9839 | nan |
| 6 | `fixed_tok256_ov25pct` | token_window | 256 | 25% | 50.6 | 256 | 1.00 | **0.9836** | 0.9949 | 1.0000 | 0.9800 | nan |
| 7 | `words_tok512_ov12_5pct` | words | 512 | 12.5% | 33.7 | 328 | 0.64 | **0.9809** | 0.9949 | 1.0000 | 0.9762 | nan |
| 8 | `semantic_tok512_ov12_5pct` | semantic | 512 | 12.5% | 29.6 | 324 | 0.63 | **0.9803** | 0.9899 | 1.0000 | 0.9771 | nan |
| 9 | `semantic_tok2048_ov12_5pct` | semantic | 2048 | 12.5% | 22.3 | 351 | 0.17 | **0.9792** | 0.9924 | 1.0000 | 0.9751 | nan |
| 10 | `semantic_tok1024_ov12_5pct` | semantic | 1024 | 12.5% | 23.3 | 350 | 0.34 | **0.9773** | 0.9899 | 1.0000 | 0.9733 | nan |
| 11 | `fixed_tok512_ov0pct` | token_window | 512 | 0% | 19.4 | 512 | 1.00 | **0.9762** | 0.9899 | 0.9975 | 0.9719 | nan |
| 12 | `fixed_tok512_ov25pct` | token_window | 512 | 25% | 25.3 | 512 | 1.00 | **0.9743** | 0.9874 | 1.0000 | 0.9701 | nan |
| 13 | `sentence_tok512_ov12_5pct` | sentence | 512 | 12.5% | 22.1 | 479 | 0.94 | **0.9732** | 0.9848 | 1.0000 | 0.9693 | nan |
| 14 | `fixed_tok512` | token_window | 512 | 12.5% | 21.9 | 512 | 1.00 | **0.9702** | 0.9874 | 1.0000 | 0.9645 | nan |
| 15 | `sentence_tok1024_ov12_5pct` | sentence | 1024 | 12.5% | 11.1 | 979 | 0.96 | **0.9696** | 0.9798 | 0.9949 | 0.9665 | nan |
| 16 | `words_tok1024_ov12_5pct` | words | 1024 | 12.5% | 17.0 | 657 | 0.64 | **0.9679** | 0.9848 | 0.9949 | 0.9623 | nan |
| 17 | `fixed_tok1024_ov25pct` | token_window | 1024 | 25% | 12.7 | 1024 | 1.00 | **0.9656** | 0.9798 | 0.9975 | 0.9613 | nan |
| 18 | `fixed_tok1024_ov12_5pct` | token_window | 1024 | 12.5% | 11.1 | 1024 | 1.00 | **0.9648** | 0.9747 | 0.9949 | 0.9615 | nan |
| 19 | `fixed_tok1024_ov0pct` | token_window | 1024 | 0% | 9.9 | 1024 | 1.00 | **0.9635** | 0.9773 | 1.0000 | 0.9590 | nan |
| 20 | `words_tok2048_ov12_5pct` | words | 2048 | 12.5% | 8.7 | 1312 | 0.64 | **0.9623** | 0.9823 | 0.9949 | 0.9559 | nan |
| 21 | `fixed_tok2048_ov0pct` | token_window | 2048 | 0% | 5.2 | 2048 | 1.00 | **0.9589** | 0.9722 | 0.9975 | 0.9545 | nan |
| 22 | `fixed_tok2048_ov25pct` | token_window | 2048 | 25% | 6.5 | 2048 | 1.00 | **0.9558** | 0.9747 | 0.9949 | 0.9498 | nan |
| 23 | `sentence_tok2048_ov12_5pct` | sentence | 2048 | 12.5% | 5.7 | 1979 | 0.97 | **0.9522** | 0.9747 | 0.9949 | 0.9451 | nan |
| 24 | `fixed_tok2048_ov12_5pct` | token_window | 2048 | 12.5% | 5.7 | 2048 | 1.00 | **0.9519** | 0.9697 | 0.9949 | 0.9463 | nan |

### Table 1b — the 12 `token_window` cells, rung ×11.5 (5,000 docs), dense

| rank | config | size | ovl | c/doc | chunks | nDCG@10 | R@10 | R@100 | MRR@10 | rr nDCG@10 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov0pct` | 256 | 0% | 38.3 | 191430 | **0.9712** | 0.9848 | 0.9949 | 0.9666 | nan |
| 2 | `fixed_tok256_ov12_5pct` | 256 | 12.5% | 43.6 | 217806 | **0.9676** | 0.9874 | 0.9949 | 0.9611 | nan |
| 3 | `fixed_tok256_ov25pct` | 256 | 25% | 50.6 | 252989 | **0.9643** | 0.9773 | 0.9949 | 0.9602 | nan |
| 4 | `fixed_tok512_ov25pct` | 512 | 25% | 25.3 | 126641 | **0.9499** | 0.9722 | 0.9874 | 0.9429 | nan |
| 5 | `fixed_tok512_ov0pct` | 512 | 0% | 19.4 | 96812 | **0.9480** | 0.9722 | 0.9874 | 0.9401 | nan |
| 6 | `fixed_tok512` | 512 | 12.5% | 21.9 | 109626 | **0.9434** | 0.9697 | 0.9874 | 0.9348 | nan |
| 7 | `fixed_tok1024_ov25pct` | 1024 | 25% | 12.7 | 63688 | **0.9374** | 0.9596 | 0.9773 | 0.9302 | nan |
| 8 | `fixed_tok1024_ov0pct` | 1024 | 0% | 9.9 | 49612 | **0.9355** | 0.9596 | 0.9773 | 0.9277 | nan |
| 9 | `fixed_tok1024_ov12_5pct` | 1024 | 12.5% | 11.1 | 55677 | **0.9308** | 0.9596 | 0.9747 | 0.9215 | nan |
| 10 | `fixed_tok2048_ov25pct` | 2048 | 25% | 6.5 | 32279 | **0.9209** | 0.9520 | 0.9697 | 0.9107 | nan |
| 11 | `fixed_tok2048_ov0pct` | 2048 | 0% | 5.2 | 26077 | **0.9198** | 0.9571 | 0.9697 | 0.9077 | nan |
| 12 | `fixed_tok2048_ov12_5pct` | 2048 | 12.5% | 5.7 | 28727 | **0.9059** | 0.9444 | 0.9646 | 0.8932 | nan |

### Table 2 — size × overlap panel, rung x0, ndcg@10

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9873 | 0.9884 | 0.9836 | -0.0037 | +0.0011 |
| 512 | 0.9762 | 0.9702 | 0.9743 | -0.0019 | -0.0060 |
| 1024 | 0.9635 | 0.9648 | 0.9656 | +0.0021 | +0.0013 |
| 2048 | 0.9589 | 0.9519 | 0.9558 | -0.0032 | -0.0070 |

### Table 2 — size × overlap panel, rung x0, recall@100

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 1.0000 | 1.0000 | 1.0000 | +0.0000 | +0.0000 |
| 512 | 0.9975 | 1.0000 | 1.0000 | +0.0025 | +0.0025 |
| 1024 | 1.0000 | 0.9949 | 0.9975 | -0.0025 | -0.0051 |
| 2048 | 0.9975 | 0.9949 | 0.9949 | -0.0025 | -0.0025 |

### Table 2 — size × overlap panel, rung x11_5, ndcg@10

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9712 | 0.9676 | 0.9643 | -0.0070 | -0.0036 |
| 512 | 0.9480 | 0.9434 | 0.9499 | +0.0019 | -0.0046 |
| 1024 | 0.9355 | 0.9308 | 0.9374 | +0.0019 | -0.0047 |
| 2048 | 0.9198 | 0.9059 | 0.9209 | +0.0011 | -0.0139 |

### Table 2 — size × overlap panel, rung x11_5, recall@100

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9949 | 0.9949 | 0.9949 | +0.0000 | +0.0000 |
| 512 | 0.9874 | 0.9874 | 0.9874 | +0.0000 | +0.0000 |
| 1024 | 0.9773 | 0.9747 | 0.9773 | +0.0000 | -0.0025 |
| 2048 | 0.9697 | 0.9646 | 0.9697 | +0.0000 | -0.0051 |

### Table 3 — the pre-registered 11-contrast family (PREREG §3), nDCG@10

Bar **X_B = 0.01**. `resolved` requires |mean| ≥ X_B AND CI excludes 0 AND ≥60% of non-zero paired differences agree in sign AND Holm across the 11 AND `δ80 ≤ |mean|`. `δ80 = 2.802·sd_d/√n`.

| # | contrast | rung | mean | 95% CI | δ80 | sign (nonzero) | ties | p | Holm p | verdict |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 1 | interaction I 256 minus 2048 | x0 | **-0.0005** | [-0.0105, +0.0089] | 0.0138 | 50% of 20 | 376 | 0.9262 | 1.0000 | **UNRESOLVED** (declared unreachable, PREREG §4.1) |
| 2 | interaction slope per doubling | x0 | **+0.0006** | [-0.0026, +0.0037] | 0.0044 | 55% of 31 | 365 | 0.7496 | 1.0000 | **NULL at the bar** (δ80 < X_B) |
| 3 | overlap 25 minus 0 | x0 | **-0.0017** | [-0.0050, +0.0016] | 0.0046 | 58% of 31 | 365 | 0.3140 | 0.9420 | **NULL at the bar** (δ80 < X_B) |
| 4 | overlap 12 5 minus 0 | x0 | **-0.0026** | [-0.0065, +0.0010] | 0.0054 | 62% of 29 | 367 | 0.1582 | 0.7910 | **NULL at the bar** (δ80 < X_B) |
| 5 | size 2048 minus 256 | x11_5 | **-0.0522** | [-0.0701, -0.0360] | 0.0243 | 95% of 59 | 337 | 0.0001 | 0.0011 | **RESOLVED — effect** |
| 6 | size 256 minus 512 | x11_5 | **+0.0206** | [+0.0106, +0.0320] | 0.0154 | 79% of 42 | 354 | 0.0001 | 0.0011 | **RESOLVED — effect** |
| 7 | sentence512 minus fixed512 | x0 | **+0.0030** | [-0.0017, +0.0085] | 0.0073 | 75% of 8 | 388 | 0.2310 | 0.9240 | **NULL at the bar** (δ80 < X_B) |
| 8 | words512 minus fixed512 | x0 | **+0.0107** | [+0.0025, +0.0193] | 0.0122 | 79% of 19 | 377 | 0.0086 | 0.0602 | **UNRESOLVED** — CI excludes 0 but |effect| < δ80 |
| 9 | semantic512 minus fixed512 | x0 | **+0.0101** | [-0.0003, +0.0215] | 0.0155 | 65% of 17 | 379 | 0.0550 | 0.3300 | **UNRESOLVED** (δ80 ≥ X_B) |
| 10 | size 1024 minus 512 | x11_5 | **-0.0125** | [-0.0214, -0.0047] | 0.0119 | 68% of 41 | 355 | 0.0004 | 0.0032 | **RESOLVED — effect** |
| 11 | size 2048 minus 1024 | x11_5 | **-0.0191** | [-0.0279, -0.0109] | 0.0122 | 82% of 49 | 347 | 0.0001 | 0.0011 | **RESOLVED — effect** |

### Table 3b — the same contrasts on the other metrics (DESCRIPTIVE, no Holm)

recall@100 at rung ×0 is **degenerate by construction** (100 of 400 documents) and is pre-declared unresolvable — PREREG §4.2.

| contrast | metric | rung | mean | 95% CI | δ80 | reachable? |
|---|---|---|---:|---|---:|---|
| interaction I 256 minus 2048 | mrr@10 | x0 | -0.0001 | [-0.0117, +0.0114] | 0.0168 | no |
| interaction slope per doubling | mrr@10 | x0 | +0.0004 | [-0.0032, +0.0042] | 0.0053 | no |
| overlap 25 minus 0 | mrr@10 | x0 | -0.0022 | [-0.0062, +0.0015] | 0.0056 | no |
| overlap 12 5 minus 0 | mrr@10 | x0 | -0.0029 | [-0.0074, +0.0013] | 0.0062 | no |
| size 2048 minus 256 | mrr@10 | x0 | -0.0334 | [-0.0497, -0.0194] | 0.0217 | yes |
| size 256 minus 512 | mrr@10 | x0 | +0.0148 | [+0.0067, +0.0240] | 0.0125 | yes |
| sentence512 minus fixed512 | mrr@10 | x0 | +0.0047 | [-0.0012, +0.0117] | 0.0092 | no |
| words512 minus fixed512 | mrr@10 | x0 | +0.0117 | [+0.0017, +0.0223] | 0.0147 | no |
| semantic512 minus fixed512 | mrr@10 | x0 | +0.0126 | [+0.0008, +0.0257] | 0.0179 | no |
| size 1024 minus 512 | mrr@10 | x0 | -0.0082 | [-0.0174, -0.0003] | 0.0124 | no |
| size 2048 minus 1024 | mrr@10 | x0 | -0.0104 | [-0.0179, -0.0036] | 0.0103 | yes |
| interaction I 256 minus 2048 | mrr@10 | x11_5 | -0.0095 | [-0.0269, +0.0079] | 0.0250 | no |
| interaction slope per doubling | mrr@10 | x11_5 | +0.0028 | [-0.0025, +0.0086] | 0.0081 | no |
| overlap 25 minus 0 | mrr@10 | x11_5 | +0.0005 | [-0.0057, +0.0070] | 0.0091 | no |
| overlap 12 5 minus 0 | mrr@10 | x11_5 | -0.0079 | [-0.0152, -0.0013] | 0.0097 | no |
| size 2048 minus 256 | mrr@10 | x11_5 | -0.0588 | [-0.0778, -0.0412] | 0.0266 | yes |
| size 256 minus 512 | mrr@10 | x11_5 | +0.0233 | [+0.0114, +0.0361] | 0.0180 | yes |
| size 1024 minus 512 | mrr@10 | x11_5 | -0.0128 | [-0.0223, -0.0042] | 0.0130 | no |
| size 2048 minus 1024 | mrr@10 | x11_5 | -0.0226 | [-0.0329, -0.0131] | 0.0144 | yes |
| interaction I 256 minus 2048 | recall@10 | x0 | -0.0025 | [-0.0126, +0.0051] | 0.0123 | **degenerate — unresolvable** |
| interaction slope per doubling | recall@10 | x0 | +0.0013 | [-0.0023, +0.0048] | 0.0049 | **degenerate — unresolvable** |
| overlap 25 minus 0 | recall@10 | x0 | +0.0006 | [-0.0044, +0.0057] | 0.0069 | **degenerate — unresolvable** |
| overlap 12 5 minus 0 | recall@10 | x0 | -0.0019 | [-0.0063, +0.0019] | 0.0059 | **degenerate — unresolvable** |
| size 2048 minus 256 | recall@10 | x0 | -0.0227 | [-0.0379, -0.0101] | 0.0196 | **degenerate — unresolvable** |
| size 256 minus 512 | recall@10 | x0 | +0.0067 | [-0.0008, +0.0168] | 0.0125 | **degenerate — unresolvable** |
| sentence512 minus fixed512 | recall@10 | x0 | -0.0025 | [-0.0076, +0.0000] | 0.0071 | **degenerate — unresolvable** |
| words512 minus fixed512 | recall@10 | x0 | +0.0076 | [+0.0000, +0.0177] | 0.0122 | **degenerate — unresolvable** |
| semantic512 minus fixed512 | recall@10 | x0 | +0.0025 | [-0.0051, +0.0126] | 0.0123 | **degenerate — unresolvable** |
| size 1024 minus 512 | recall@10 | x0 | -0.0109 | [-0.0210, -0.0017] | 0.0139 | **degenerate — unresolvable** |
| size 2048 minus 1024 | recall@10 | x0 | -0.0051 | [-0.0126, +0.0017] | 0.0100 | **degenerate — unresolvable** |
| interaction I 256 minus 2048 | recall@10 | x11_5 | -0.0025 | [-0.0152, +0.0101] | 0.0187 | no |
| interaction slope per doubling | recall@10 | x11_5 | +0.0008 | [-0.0035, +0.0056] | 0.0065 | no |
| overlap 25 minus 0 | recall@10 | x11_5 | -0.0032 | [-0.0076, +0.0006] | 0.0059 | no |
| overlap 12 5 minus 0 | recall@10 | x11_5 | -0.0032 | [-0.0082, +0.0019] | 0.0073 | no |
| size 2048 minus 256 | recall@10 | x11_5 | -0.0320 | [-0.0480, -0.0177] | 0.0222 | yes |
| size 256 minus 512 | recall@10 | x11_5 | +0.0118 | [+0.0034, +0.0219] | 0.0133 | no |
| size 1024 minus 512 | recall@10 | x11_5 | -0.0118 | [-0.0219, -0.0034] | 0.0133 | no |
| size 2048 minus 1024 | recall@10 | x11_5 | -0.0084 | [-0.0160, -0.0025] | 0.0099 | no |
| interaction I 256 minus 2048 | recall@100 | x0 | +0.0025 | [+0.0000, +0.0076] | 0.0071 | **degenerate — unresolvable** |
| interaction slope per doubling | recall@100 | x0 | -0.0013 | [-0.0035, +0.0000] | 0.0029 | **degenerate — unresolvable** |
| overlap 25 minus 0 | recall@100 | x0 | -0.0006 | [-0.0038, +0.0019] | 0.0040 | **degenerate — unresolvable** |
| overlap 12 5 minus 0 | recall@100 | x0 | -0.0013 | [-0.0038, +0.0000] | 0.0035 | **degenerate — unresolvable** |
| size 2048 minus 256 | recall@100 | x0 | -0.0042 | [-0.0109, +0.0000] | 0.0085 | **degenerate — unresolvable** |
| size 256 minus 512 | recall@100 | x0 | +0.0008 | [+0.0000, +0.0025] | 0.0024 | **degenerate — unresolvable** |
| sentence512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| words512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| semantic512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| size 1024 minus 512 | recall@100 | x0 | -0.0017 | [-0.0051, +0.0000] | 0.0047 | **degenerate — unresolvable** |
| size 2048 minus 1024 | recall@100 | x0 | -0.0017 | [-0.0051, +0.0000] | 0.0047 | **degenerate — unresolvable** |
| interaction I 256 minus 2048 | recall@100 | x11_5 | +0.0000 | [-0.0076, +0.0076] | 0.0100 | no |
| interaction slope per doubling | recall@100 | x11_5 | +0.0000 | [-0.0025, +0.0025] | 0.0036 | no |
| overlap 25 minus 0 | recall@100 | x11_5 | +0.0000 | [-0.0038, +0.0038] | 0.0056 | no |
| overlap 12 5 minus 0 | recall@100 | x11_5 | -0.0019 | [-0.0057, +0.0025] | 0.0059 | no |
| size 2048 minus 256 | recall@100 | x11_5 | -0.0269 | [-0.0429, -0.0126] | 0.0218 | yes |
| size 256 minus 512 | recall@100 | x11_5 | +0.0076 | [+0.0000, +0.0177] | 0.0127 | no |
| size 1024 minus 512 | recall@100 | x11_5 | -0.0109 | [-0.0202, -0.0034] | 0.0117 | no |
| size 2048 minus 1024 | recall@100 | x11_5 | -0.0084 | [-0.0168, -0.0017] | 0.0110 | no |

### Table 4 — per-size contrasts, Leg A vs Leg B side by side (nDCG@10)

Leg A recomputed from `../stage1/report1.json` with the identical bootstrap, so both columns are the same arithmetic. Leg A n = 10 topics; Leg B n = 396 queries. Leg A's δ80 here uses **this** file's convention (2.802·sd/√n), which is why it differs from the number published in `RESULTS-stage1-legA.md` (that used 0.995·sd, a small-n constant).

| contrast | Leg A mean [CI] | Leg A δ80 | Leg A res? | Leg B mean [CI] | Leg B δ80 | Leg B status | signs |
|---|---|---:|---|---|---:|---|---|
| size 512 − 256 | -0.0238 [-0.0918,+0.0414] | 0.0994 | unresolved | -0.0206 [-0.0320,-0.0106] | 0.0154 | effect | — (A unresolved) |
| size 1024 minus 512 | +0.1204 [+0.0533,+0.1853] | 0.1008 | effect | -0.0125 [-0.0214,-0.0047] | 0.0119 | effect | **OPPOSITE** |
| size 2048 minus 1024 | -0.0062 [-0.0439,+0.0344] | 0.0586 | unresolved | -0.0191 [-0.0279,-0.0109] | 0.0122 | effect | — (A unresolved) |
| size 2048 minus 256 | +0.0904 [-0.0268,+0.2237] | 0.1872 | unresolved | -0.0522 [-0.0701,-0.0360] | 0.0243 | effect | — (A unresolved) |
| overlap 12 5 minus 0 | -0.0210 [-0.0473,+0.0070] | 0.0413 | null@bar | -0.0026 [-0.0065,+0.0010] | 0.0054 | null@bar | **both null at their own bar** |
| overlap 25 minus 0 | -0.0273 [-0.0691,+0.0208] | 0.0682 | unresolved | -0.0017 [-0.0050,+0.0016] | 0.0046 | null@bar | — (A unresolved) |
| interaction slope per doubling | +0.0101 [-0.0230,+0.0431] | 0.0501 | unresolved | +0.0006 [-0.0026,+0.0037] | 0.0044 | null@bar | — (A unresolved) |
| interaction I 256 minus 2048 | -0.0409 [-0.1773,+0.0760] | 0.1894 | unresolved | -0.0005 [-0.0105,+0.0089] | 0.0138 | unresolved | — (A unresolved) |

Same table on **recall@100** — Leg B read at rung ×11.5 only (PREREG §4.2):

| contrast | Leg A mean [CI] | Leg A δ80 | Leg B ×11.5 mean [CI] | Leg B δ80 | signs |
|---|---|---:|---|---:|---|
| size 512 − 256 | -0.0081 [-0.0168,+0.0015] | 0.0139 | -0.0076 [-0.0177,-0.0000] | 0.0127 | — (A null@bar / B unresolved) |
| size 1024 minus 512 | +0.0302 [+0.0096,+0.0531] | 0.0333 | -0.0109 [-0.0202,-0.0034] | 0.0117 | — (A unresolved / B unresolved) |
| size 2048 minus 1024 | +0.0212 [+0.0026,+0.0392] | 0.0266 | -0.0084 [-0.0168,-0.0017] | 0.0110 | — (A unresolved / B unresolved) |
| size 2048 minus 256 | +0.0432 [+0.0152,+0.0764] | 0.0466 | -0.0269 [-0.0429,-0.0126] | 0.0218 | — (A unresolved / B effect) |
| overlap 12 5 minus 0 | +0.0022 [-0.0046,+0.0095] | 0.0106 | -0.0019 [-0.0057,+0.0025] | 0.0059 | — (A null@bar / B null@bar) |
| overlap 25 minus 0 | +0.0002 [-0.0106,+0.0096] | 0.0151 | +0.0000 [-0.0038,+0.0038] | 0.0056 | — (A null@bar / B null@bar) |

### Table 5 — Q3: realised median chunk tokens vs nominal size (DESCRIPTIVE)

Across the 24 config means at rung ×0. **Not pre-registered for inference**; the 24 means share the same queries and are not independent. PREREG §1 Q3 says the expected sign on Leg B is **negative** and that a large negative `r` is a replication of the mechanism, not a falsification.

| predictor | corr with nDCG@10 | corr with recall@10 | corr with MRR@10 |
|---|---:|---:|---:|
| log2 **nominal** size | -0.868 | -0.802 | -0.870 |
| log2 **realised** median tokens | -0.977 | -0.965 | -0.968 |

Fit across all 24 configs: `nDCG@10 ≈ 1.0635 -0.0099 × log2(realised median tokens)`.

| kind | n | mean raw nDCG@10 | mean residual about the fit |
|---|---:|---:|---:|
| `semantic` | 4 | 0.9807 | **-0.0006** |
| `sentence` | 4 | 0.9708 | **+0.0003** |
| `token_window` | 12 | 0.9701 | **+0.0005** |
| `words` | 4 | 0.9748 | **-0.0011** |

Spread of per-kind means: raw **0.0106** → residual **0.0016**.

### Table 6 — reranking, rung x0 (24 configs)

| config | dense nDCG@10 | reranked nDCG@10 | Δ |
|---|---:|---:|---:|
| `fixed_tok256_ov12_5pct` | 0.9884 | nan | +nan |
| `sentence_tok256_ov12_5pct` | 0.9882 | nan | +nan |
| `words_tok256_ov12_5pct` | 0.9881 | nan | +nan |
| `fixed_tok256_ov0pct` | 0.9873 | nan | +nan |
| `semantic_tok256_ov12_5pct` | 0.9860 | nan | +nan |
| `fixed_tok256_ov25pct` | 0.9836 | nan | +nan |
| `words_tok512_ov12_5pct` | 0.9809 | nan | +nan |
| `semantic_tok512_ov12_5pct` | 0.9803 | nan | +nan |
| `semantic_tok2048_ov12_5pct` | 0.9792 | nan | +nan |
| `semantic_tok1024_ov12_5pct` | 0.9773 | nan | +nan |
| `fixed_tok512_ov0pct` | 0.9762 | nan | +nan |
| `fixed_tok512_ov25pct` | 0.9743 | nan | +nan |
| `sentence_tok512_ov12_5pct` | 0.9732 | nan | +nan |
| `fixed_tok512` | 0.9702 | nan | +nan |
| `sentence_tok1024_ov12_5pct` | 0.9696 | nan | +nan |
| `words_tok1024_ov12_5pct` | 0.9679 | nan | +nan |
| `fixed_tok1024_ov25pct` | 0.9656 | nan | +nan |
| `fixed_tok1024_ov12_5pct` | 0.9648 | nan | +nan |
| `fixed_tok1024_ov0pct` | 0.9635 | nan | +nan |
| `words_tok2048_ov12_5pct` | 0.9623 | nan | +nan |
| `fixed_tok2048_ov0pct` | 0.9589 | nan | +nan |
| `fixed_tok2048_ov25pct` | 0.9558 | nan | +nan |
| `sentence_tok2048_ov12_5pct` | 0.9522 | nan | +nan |
| `fixed_tok2048_ov12_5pct` | 0.9519 | nan | +nan |

Dense↔reranked config-mean correlation **r = +nan** (Leg A: +0.553). Spread 0.0365 → nan.

### Table 6 — reranking, rung x11_5 (12 configs)

| config | dense nDCG@10 | reranked nDCG@10 | Δ |
|---|---:|---:|---:|
| `fixed_tok256_ov0pct` | 0.9712 | nan | +nan |
| `fixed_tok256_ov12_5pct` | 0.9676 | nan | +nan |
| `fixed_tok256_ov25pct` | 0.9643 | nan | +nan |
| `fixed_tok512_ov25pct` | 0.9499 | nan | +nan |
| `fixed_tok512_ov0pct` | 0.9480 | nan | +nan |
| `fixed_tok512` | 0.9434 | nan | +nan |
| `fixed_tok1024_ov25pct` | 0.9374 | nan | +nan |
| `fixed_tok1024_ov0pct` | 0.9355 | nan | +nan |
| `fixed_tok1024_ov12_5pct` | 0.9308 | nan | +nan |
| `fixed_tok2048_ov25pct` | 0.9209 | nan | +nan |
| `fixed_tok2048_ov0pct` | 0.9198 | nan | +nan |
| `fixed_tok2048_ov12_5pct` | 0.9059 | nan | +nan |

Dense↔reranked config-mean correlation **r = +nan** (Leg A: +0.553). Spread 0.0653 → nan.

### Table 7 — measured cost

| rung | configs | tokens (M) | embed min | tok/s | requests | retries |
|---|---:|---:|---:|---:|---:|---:|
| x0 | 24 | 103 | 8.6 | 198k | 17982 | 0 |
| x11_5 | 12 | 666 | 64.8 | 171k | 104822 | 0 |

Semantic breakpoint pass (x0): notional **101M** / actual **42M** tokens in 3.2 min (218k actual tok/s, 9885 requests, 0 retries). **0 of 400** documents exceeded `max_breakpoint_sentences = 3000` and fell back to fixed_token inside the semantic arm (median 212 sentences/doc).

### Table 8 — the pre-registered questions, scored

| # | question / prediction | outcome | verdict |
|---|---|---|---|
| **Q1** | does the overlap null replicate on Leg B? | 12.5%−0% = -0.0026, CI [-0.0065,+0.0010], δ80 0.0054 vs bar 0.01 | **REPLICATES** |
| **Q2** | is the size disagreement uniform or size-localised? | 1/3 adjacent steps resolve on **both** legs; point estimates disagree in sign at 1/3 steps (size 1024 minus 512) | **NOT ESTABLISHABLE — only 1 of 3 steps resolves on both legs (size 1024 minus 512, signs opposite)** |
| Q2 (descriptive) | point-estimate direction at each adjacent step | size 512 minus 256: A -0.0238 / B -0.0206* (same); size 1024 minus 512: A +0.1204* / B -0.0125* (OPPOSITE); size 2048 minus 1024: A -0.0062 / B -0.0191* (same) | `*` = resolvable on that leg |
| **PB2** | Leg B's size effect is monotone-negative at every step | size 512 minus 256 = -0.0206; size 1024 minus 512 = -0.0125; size 2048 minus 1024 = -0.0191 | **HOLDS** |
| **Q3 / PB4** | does realised-token size explain the grid, as on Leg A? | r(realised) = -0.977 vs r(nominal) = -0.868; per-kind spread 0.0106 → 0.0016 | **REPLICATES** (negative sign as pre-registered) |
| **PB5** | reranking reorders the grid (rung x0) | dense↔reranked r = +nan over 24 configs (Leg A +0.553) | **FALSIFIED** |
| **PB5** | reranking reorders the grid (rung x11_5) | dense↔reranked r = +nan over 12 configs (Leg A +0.553) | **FALSIFIED** |
| **PB7** | semantic >3000-sentence fallback rate exceeds Leg A's 0.30% | 0/400 = 0.00% | **FALSIFIED** |
