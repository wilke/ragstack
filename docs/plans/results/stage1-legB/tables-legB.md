### Table 1 — the 24-config grid, Leg B rung ×0 (judged-only, 400 docs), n = 260 accepted queries, dense

Single relevant document per query; `nDCG@10 = 1/log2(rank+1)`. `fill` = median realised tokens / nominal size. **Descriptive — neighbouring rows differ by less than the noise floor and are not ordered claims.**

| rank | config | kind | size | ovl | c/doc | med tok | fill | nDCG@10 | R@10 | R@100 | MRR@10 | rr nDCG@10 |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov12_5pct` | token_window | 256 | 12.5% | 43.6 | 256 | 1.00 | **0.9838** | 0.9923 | 1.0000 | 0.9808 | 0.9705 |
| 2 | `fixed_tok256_ov0pct` | token_window | 256 | 0% | 38.3 | 256 | 1.00 | **0.9821** | 0.9923 | 1.0000 | 0.9788 | 0.9736 |
| 3 | `sentence_tok256_ov12_5pct` | sentence | 256 | 12.5% | 45.0 | 229 | 0.89 | **0.9820** | 0.9923 | 1.0000 | 0.9785 | 0.9814 |
| 4 | `words_tok256_ov12_5pct` | words | 256 | 12.5% | 67.3 | 164 | 0.64 | **0.9819** | 0.9962 | 1.0000 | 0.9771 | 0.9740 |
| 5 | `semantic_tok256_ov12_5pct` | semantic | 256 | 12.5% | 48.9 | 255 | 1.00 | **0.9806** | 0.9885 | 1.0000 | 0.9780 | 0.9837 |
| 6 | `fixed_tok256_ov25pct` | token_window | 256 | 25% | 50.6 | 256 | 1.00 | **0.9770** | 0.9923 | 1.0000 | 0.9721 | 0.9743 |
| 7 | `words_tok512_ov12_5pct` | words | 512 | 12.5% | 33.7 | 328 | 0.64 | **0.9737** | 0.9923 | 1.0000 | 0.9677 | 0.9798 |
| 8 | `semantic_tok2048_ov12_5pct` | semantic | 2048 | 12.5% | 22.3 | 351 | 0.17 | **0.9703** | 0.9885 | 1.0000 | 0.9646 | 0.9737 |
| 9 | `semantic_tok512_ov12_5pct` | semantic | 512 | 12.5% | 29.6 | 324 | 0.63 | **0.9700** | 0.9846 | 1.0000 | 0.9652 | 0.9724 |
| 10 | `fixed_tok512_ov0pct` | token_window | 512 | 0% | 19.4 | 512 | 1.00 | **0.9676** | 0.9885 | 0.9962 | 0.9610 | 0.9667 |
| 11 | `semantic_tok1024_ov12_5pct` | semantic | 1024 | 12.5% | 23.3 | 350 | 0.34 | **0.9674** | 0.9846 | 1.0000 | 0.9619 | 0.9737 |
| 12 | `fixed_tok512_ov25pct` | token_window | 512 | 25% | 25.3 | 512 | 1.00 | **0.9647** | 0.9846 | 1.0000 | 0.9583 | 0.9645 |
| 13 | `sentence_tok512_ov12_5pct` | sentence | 512 | 12.5% | 22.1 | 479 | 0.94 | **0.9645** | 0.9808 | 1.0000 | 0.9590 | 0.9720 |
| 14 | `fixed_tok512` | token_window | 512 | 12.5% | 21.9 | 512 | 1.00 | **0.9585** | 0.9846 | 1.0000 | 0.9498 | 0.9770 |
| 15 | `sentence_tok1024_ov12_5pct` | sentence | 1024 | 12.5% | 11.1 | 979 | 0.96 | **0.9575** | 0.9731 | 0.9962 | 0.9528 | 0.9699 |
| 16 | `words_tok1024_ov12_5pct` | words | 1024 | 12.5% | 17.0 | 657 | 0.64 | **0.9564** | 0.9808 | 0.9923 | 0.9483 | 0.9618 |
| 17 | `fixed_tok1024_ov25pct` | token_window | 1024 | 25% | 12.7 | 1024 | 1.00 | **0.9515** | 0.9731 | 0.9962 | 0.9449 | 0.9636 |
| 18 | `fixed_tok1024_ov12_5pct` | token_window | 1024 | 12.5% | 11.1 | 1024 | 1.00 | **0.9503** | 0.9654 | 0.9923 | 0.9452 | 0.9618 |
| 19 | `fixed_tok1024_ov0pct` | token_window | 1024 | 0% | 9.9 | 1024 | 1.00 | **0.9482** | 0.9692 | 1.0000 | 0.9414 | 0.9653 |
| 20 | `words_tok2048_ov12_5pct` | words | 2048 | 12.5% | 8.7 | 1312 | 0.64 | **0.9478** | 0.9769 | 0.9923 | 0.9386 | 0.9581 |
| 21 | `fixed_tok2048_ov0pct` | token_window | 2048 | 0% | 5.2 | 2048 | 1.00 | **0.9413** | 0.9615 | 0.9962 | 0.9345 | 0.9666 |
| 22 | `fixed_tok2048_ov25pct` | token_window | 2048 | 25% | 6.5 | 2048 | 1.00 | **0.9379** | 0.9654 | 0.9923 | 0.9293 | 0.9584 |
| 23 | `sentence_tok2048_ov12_5pct` | sentence | 2048 | 12.5% | 5.7 | 1979 | 0.97 | **0.9311** | 0.9654 | 0.9923 | 0.9202 | 0.9602 |
| 24 | `fixed_tok2048_ov12_5pct` | token_window | 2048 | 12.5% | 5.7 | 2048 | 1.00 | **0.9307** | 0.9577 | 0.9923 | 0.9221 | 0.9650 |

### Table 1b — the 12 `token_window` cells, rung ×11.5 (5,000 docs), dense

| rank | config | size | ovl | c/doc | chunks | nDCG@10 | R@10 | R@100 | MRR@10 | rr nDCG@10 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov0pct` | 256 | 0% | 38.3 | 191430 | **0.9595** | 0.9769 | 0.9923 | 0.9536 | 0.9390 |
| 2 | `fixed_tok256_ov12_5pct` | 256 | 12.5% | 43.6 | 217806 | **0.9582** | 0.9808 | 0.9923 | 0.9507 | 0.9291 |
| 3 | `fixed_tok256_ov25pct` | 256 | 25% | 50.6 | 252989 | **0.9518** | 0.9692 | 0.9923 | 0.9463 | 0.9431 |
| 4 | `fixed_tok512_ov25pct` | 512 | 25% | 25.3 | 126641 | **0.9339** | 0.9615 | 0.9846 | 0.9252 | 0.9340 |
| 5 | `fixed_tok512_ov0pct` | 512 | 0% | 19.4 | 96812 | **0.9291** | 0.9615 | 0.9846 | 0.9184 | 0.9364 |
| 6 | `fixed_tok512` | 512 | 12.5% | 21.9 | 109626 | **0.9214** | 0.9577 | 0.9846 | 0.9096 | 0.9424 |
| 7 | `fixed_tok1024_ov25pct` | 1024 | 25% | 12.7 | 63688 | **0.9145** | 0.9423 | 0.9692 | 0.9054 | 0.9279 |
| 8 | `fixed_tok1024_ov0pct` | 1024 | 0% | 9.9 | 49612 | **0.9094** | 0.9423 | 0.9692 | 0.8988 | 0.9310 |
| 9 | `fixed_tok1024_ov12_5pct` | 1024 | 12.5% | 11.1 | 55677 | **0.9060** | 0.9423 | 0.9654 | 0.8943 | 0.9399 |
| 10 | `fixed_tok2048_ov25pct` | 2048 | 25% | 6.5 | 32279 | **0.8895** | 0.9308 | 0.9577 | 0.8759 | 0.9207 |
| 11 | `fixed_tok2048_ov0pct` | 2048 | 0% | 5.2 | 26077 | **0.8863** | 0.9385 | 0.9577 | 0.8692 | 0.9341 |
| 12 | `fixed_tok2048_ov12_5pct` | 2048 | 12.5% | 5.7 | 28727 | **0.8675** | 0.9192 | 0.9500 | 0.8504 | 0.9155 |

### Table 2 — size × overlap panel, rung x0, ndcg@10

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9821 | 0.9838 | 0.9770 | -0.0051 | +0.0016 |
| 512 | 0.9676 | 0.9585 | 0.9647 | -0.0029 | -0.0092 |
| 1024 | 0.9482 | 0.9503 | 0.9515 | +0.0033 | +0.0020 |
| 2048 | 0.9413 | 0.9307 | 0.9379 | -0.0034 | -0.0107 |

### Table 2 — size × overlap panel, rung x0, recall@100

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 1.0000 | 1.0000 | 1.0000 | +0.0000 | +0.0000 |
| 512 | 0.9962 | 1.0000 | 1.0000 | +0.0038 | +0.0038 |
| 1024 | 1.0000 | 0.9923 | 0.9962 | -0.0038 | -0.0077 |
| 2048 | 0.9962 | 0.9923 | 0.9923 | -0.0038 | -0.0038 |

### Table 2 — size × overlap panel, rung x11_5, ndcg@10

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9595 | 0.9582 | 0.9518 | -0.0078 | -0.0014 |
| 512 | 0.9291 | 0.9214 | 0.9339 | +0.0049 | -0.0076 |
| 1024 | 0.9094 | 0.9060 | 0.9145 | +0.0050 | -0.0034 |
| 2048 | 0.8863 | 0.8675 | 0.8895 | +0.0032 | -0.0188 |

### Table 2 — size × overlap panel, rung x11_5, recall@100

| size | 0% | 12.5% | 25% | E(s) = 25%−0% | E12(s) = 12.5%−0% |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.9923 | 0.9923 | 0.9923 | +0.0000 | +0.0000 |
| 512 | 0.9846 | 0.9846 | 0.9846 | +0.0000 | +0.0000 |
| 1024 | 0.9692 | 0.9654 | 0.9692 | +0.0000 | -0.0038 |
| 2048 | 0.9577 | 0.9500 | 0.9577 | +0.0000 | -0.0077 |

### Table 3 — the pre-registered 11-contrast family (PREREG §3), nDCG@10

Bar **X_B = 0.01**. `resolved` requires |mean| ≥ X_B AND CI excludes 0 AND ≥60% of non-zero paired differences agree in sign AND Holm across the 11 AND `δ80 ≤ |mean|`. `δ80 = 2.802·sd_d/√n`.

| # | contrast | rung | mean | 95% CI | δ80 | sign (nonzero) | ties | p | Holm p | verdict |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 1 | interaction I 256 minus 2048 | x0 | **-0.0017** | [-0.0149, +0.0110] | 0.0187 | 50% of 18 | 242 | 0.7774 | 1.0000 | **UNRESOLVED** (declared unreachable, PREREG §4.1) |
| 2 | interaction slope per doubling | x0 | **+0.0011** | [-0.0030, +0.0055] | 0.0061 | 55% of 29 | 231 | 0.6088 | 1.0000 | **NULL at the bar** (δ80 < X_B) |
| 3 | overlap 25 minus 0 | x0 | **-0.0020** | [-0.0070, +0.0026] | 0.0069 | 57% of 30 | 230 | 0.4060 | 1.0000 | **NULL at the bar** (δ80 < X_B) |
| 4 | overlap 12 5 minus 0 | x0 | **-0.0040** | [-0.0098, +0.0015] | 0.0081 | 63% of 27 | 233 | 0.1666 | 0.6664 | **NULL at the bar** (δ80 < X_B) |
| 5 | size 2048 minus 256 | x11_5 | **-0.0754** | [-0.1015, -0.0519] | 0.0357 | 95% of 55 | 205 | 0.0001 | 0.0011 | **RESOLVED — effect** |
| 6 | size 256 minus 512 | x11_5 | **+0.0284** | [+0.0134, +0.0449] | 0.0225 | 79% of 38 | 222 | 0.0001 | 0.0011 | **RESOLVED — effect** |
| 7 | sentence512 minus fixed512 | x0 | **+0.0061** | [-0.0006, +0.0141] | 0.0103 | 86% of 7 | 253 | 0.0738 | 0.4428 | **UNRESOLVED** (δ80 ≥ X_B) |
| 8 | words512 minus fixed512 | x0 | **+0.0152** | [+0.0041, +0.0272] | 0.0167 | 82% of 17 | 243 | 0.0040 | 0.0280 | **UNRESOLVED** — CI excludes 0 but |effect| < δ80 |
| 9 | semantic512 minus fixed512 | x0 | **+0.0116** | [-0.0022, +0.0268] | 0.0210 | 62% of 16 | 244 | 0.1026 | 0.5130 | **UNRESOLVED** (δ80 ≥ X_B) |
| 10 | size 1024 minus 512 | x11_5 | **-0.0182** | [-0.0313, -0.0062] | 0.0180 | 68% of 37 | 223 | 0.0018 | 0.0144 | **RESOLVED — effect** |
| 11 | size 2048 minus 1024 | x11_5 | **-0.0289** | [-0.0426, -0.0165] | 0.0182 | 84% of 45 | 215 | 0.0001 | 0.0011 | **RESOLVED — effect** |

### Table 3b — the same contrasts on the other metrics (DESCRIPTIVE, no Holm)

recall@100 at rung ×0 is **degenerate by construction** (100 of 400 documents) and is pre-declared unresolvable — PREREG §4.2.

| contrast | metric | rung | mean | 95% CI | δ80 | reachable? |
|---|---|---|---:|---|---:|---|
| interaction I 256 minus 2048 | mrr@10 | x0 | -0.0015 | [-0.0169, +0.0140] | 0.0220 | no |
| interaction slope per doubling | mrr@10 | x0 | +0.0011 | [-0.0037, +0.0060] | 0.0070 | no |
| overlap 25 minus 0 | mrr@10 | x0 | -0.0028 | [-0.0087, +0.0028] | 0.0083 | no |
| overlap 12 5 minus 0 | mrr@10 | x0 | -0.0045 | [-0.0110, +0.0018] | 0.0092 | no |
| size 2048 minus 256 | mrr@10 | x0 | -0.0486 | [-0.0722, -0.0280] | 0.0321 | yes |
| size 256 minus 512 | mrr@10 | x0 | +0.0208 | [+0.0095, +0.0340] | 0.0176 | yes |
| sentence512 minus fixed512 | mrr@10 | x0 | +0.0092 | [+0.0014, +0.0191] | 0.0129 | no |
| words512 minus fixed512 | mrr@10 | x0 | +0.0178 | [+0.0040, +0.0330] | 0.0209 | no |
| semantic512 minus fixed512 | mrr@10 | x0 | +0.0154 | [-0.0012, +0.0337] | 0.0250 | no |
| size 1024 minus 512 | mrr@10 | x0 | -0.0126 | [-0.0263, -0.0003] | 0.0188 | no |
| size 2048 minus 1024 | mrr@10 | x0 | -0.0152 | [-0.0264, -0.0049] | 0.0155 | no |
| interaction I 256 minus 2048 | mrr@10 | x11_5 | -0.0141 | [-0.0384, +0.0102] | 0.0348 | no |
| interaction slope per doubling | mrr@10 | x11_5 | +0.0042 | [-0.0035, +0.0126] | 0.0116 | no |
| overlap 25 minus 0 | mrr@10 | x11_5 | +0.0032 | [-0.0061, +0.0129] | 0.0135 | no |
| overlap 12 5 minus 0 | mrr@10 | x11_5 | -0.0087 | [-0.0190, +0.0006] | 0.0140 | no |
| size 2048 minus 256 | mrr@10 | x11_5 | -0.0850 | [-0.1125, -0.0588] | 0.0389 | yes |
| size 256 minus 512 | mrr@10 | x11_5 | +0.0325 | [+0.0145, +0.0515] | 0.0263 | yes |
| size 1024 minus 512 | mrr@10 | x11_5 | -0.0183 | [-0.0326, -0.0050] | 0.0196 | no |
| size 2048 minus 1024 | mrr@10 | x11_5 | -0.0343 | [-0.0500, -0.0203] | 0.0216 | yes |
| interaction I 256 minus 2048 | recall@10 | x0 | -0.0038 | [-0.0192, +0.0077] | 0.0187 | **degenerate — unresolvable** |
| interaction slope per doubling | recall@10 | x0 | +0.0019 | [-0.0031, +0.0073] | 0.0074 | **degenerate — unresolvable** |
| overlap 25 minus 0 | recall@10 | x0 | +0.0010 | [-0.0058, +0.0087] | 0.0105 | **degenerate — unresolvable** |
| overlap 12 5 minus 0 | recall@10 | x0 | -0.0029 | [-0.0096, +0.0029] | 0.0089 | **degenerate — unresolvable** |
| size 2048 minus 256 | recall@10 | x0 | -0.0308 | [-0.0526, -0.0128] | 0.0278 | **degenerate — unresolvable** |
| size 256 minus 512 | recall@10 | x0 | +0.0064 | [-0.0026, +0.0192] | 0.0156 | **degenerate — unresolvable** |
| sentence512 minus fixed512 | recall@10 | x0 | -0.0038 | [-0.0115, +0.0000] | 0.0108 | **degenerate — unresolvable** |
| words512 minus fixed512 | recall@10 | x0 | +0.0077 | [+0.0000, +0.0192] | 0.0152 | **degenerate — unresolvable** |
| semantic512 minus fixed512 | recall@10 | x0 | +0.0000 | [-0.0115, +0.0115] | 0.0153 | **degenerate — unresolvable** |
| size 1024 minus 512 | recall@10 | x0 | -0.0167 | [-0.0321, -0.0026] | 0.0211 | **degenerate — unresolvable** |
| size 2048 minus 1024 | recall@10 | x0 | -0.0077 | [-0.0192, +0.0026] | 0.0152 | **degenerate — unresolvable** |
| interaction I 256 minus 2048 | recall@10 | x11_5 | +0.0000 | [-0.0192, +0.0192] | 0.0264 | no |
| interaction slope per doubling | recall@10 | x11_5 | +0.0000 | [-0.0065, +0.0065] | 0.0093 | no |
| overlap 25 minus 0 | recall@10 | x11_5 | -0.0038 | [-0.0106, +0.0019] | 0.0085 | no |
| overlap 12 5 minus 0 | recall@10 | x11_5 | -0.0048 | [-0.0125, +0.0029] | 0.0111 | no |
| size 2048 minus 256 | recall@10 | x11_5 | -0.0462 | [-0.0705, -0.0256] | 0.0328 | yes |
| size 256 minus 512 | recall@10 | x11_5 | +0.0154 | [+0.0038, +0.0295] | 0.0189 | no |
| size 1024 minus 512 | recall@10 | x11_5 | -0.0179 | [-0.0333, -0.0051] | 0.0201 | no |
| size 2048 minus 1024 | recall@10 | x11_5 | -0.0128 | [-0.0244, -0.0026] | 0.0151 | no |
| interaction I 256 minus 2048 | recall@100 | x0 | +0.0038 | [+0.0000, +0.0115] | 0.0108 | **degenerate — unresolvable** |
| interaction slope per doubling | recall@100 | x0 | -0.0019 | [-0.0054, +0.0000] | 0.0044 | **degenerate — unresolvable** |
| overlap 25 minus 0 | recall@100 | x0 | -0.0010 | [-0.0058, +0.0029] | 0.0060 | **degenerate — unresolvable** |
| overlap 12 5 minus 0 | recall@100 | x0 | -0.0019 | [-0.0058, +0.0000] | 0.0054 | **degenerate — unresolvable** |
| size 2048 minus 256 | recall@100 | x0 | -0.0064 | [-0.0167, +0.0000] | 0.0129 | **degenerate — unresolvable** |
| size 256 minus 512 | recall@100 | x0 | +0.0013 | [+0.0000, +0.0038] | 0.0036 | **degenerate — unresolvable** |
| sentence512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| words512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| semantic512 minus fixed512 | recall@100 | x0 | +0.0000 | [+0.0000, +0.0000] | 0.0000 | **degenerate — unresolvable** |
| size 1024 minus 512 | recall@100 | x0 | -0.0026 | [-0.0077, +0.0000] | 0.0072 | **degenerate — unresolvable** |
| size 2048 minus 1024 | recall@100 | x0 | -0.0026 | [-0.0077, +0.0000] | 0.0072 | **degenerate — unresolvable** |
| interaction I 256 minus 2048 | recall@100 | x11_5 | +0.0000 | [-0.0115, +0.0115] | 0.0153 | no |
| interaction slope per doubling | recall@100 | x11_5 | +0.0000 | [-0.0038, +0.0038] | 0.0055 | no |
| overlap 25 minus 0 | recall@100 | x11_5 | +0.0000 | [-0.0058, +0.0058] | 0.0085 | no |
| overlap 12 5 minus 0 | recall@100 | x11_5 | -0.0029 | [-0.0087, +0.0038] | 0.0089 | no |
| size 2048 minus 256 | recall@100 | x11_5 | -0.0372 | [-0.0603, -0.0179] | 0.0313 | yes |
| size 256 minus 512 | recall@100 | x11_5 | +0.0077 | [-0.0013, +0.0205] | 0.0160 | no |
| size 1024 minus 512 | recall@100 | x11_5 | -0.0167 | [-0.0295, -0.0051] | 0.0178 | no |
| size 2048 minus 1024 | recall@100 | x11_5 | -0.0128 | [-0.0256, -0.0026] | 0.0167 | no |

### Table 4 — per-size contrasts, Leg A vs Leg B side by side (nDCG@10)

Leg A recomputed from `../stage1/report1.json` with the identical bootstrap, so both columns are the same arithmetic. Leg A n = 10 topics; Leg B n = 260 queries. Leg A's δ80 here uses **this** file's convention (2.802·sd/√n), which is why it differs from the number published in `RESULTS-stage1-legA.md` (that used 0.995·sd, a small-n constant).

| contrast | Leg A mean [CI] | Leg A δ80 | Leg A res? | Leg B mean [CI] | Leg B δ80 | Leg B status | signs |
|---|---|---:|---|---|---:|---|---|
| size 512 − 256 | -0.0238 [-0.0895,+0.0417] | 0.0994 | unresolved | -0.0284 [-0.0449,-0.0134] | 0.0225 | effect | — (A unresolved) |
| size 1024 minus 512 | +0.1204 [+0.0523,+0.1865] | 0.1008 | effect | -0.0182 [-0.0313,-0.0062] | 0.0180 | effect | **OPPOSITE** |
| size 2048 minus 1024 | -0.0062 [-0.0443,+0.0339] | 0.0586 | unresolved | -0.0289 [-0.0426,-0.0165] | 0.0182 | effect | — (A unresolved) |
| size 2048 minus 256 | +0.0904 [-0.0266,+0.2229] | 0.1872 | unresolved | -0.0754 [-0.1015,-0.0519] | 0.0357 | effect | — (A unresolved) |
| overlap 12 5 minus 0 | -0.0210 [-0.0484,+0.0073] | 0.0413 | null@bar | -0.0040 [-0.0098,+0.0015] | 0.0081 | null@bar | **both null at their own bar** |
| overlap 25 minus 0 | -0.0273 [-0.0690,+0.0219] | 0.0682 | unresolved | -0.0020 [-0.0070,+0.0026] | 0.0069 | null@bar | — (A unresolved) |
| interaction slope per doubling | +0.0101 [-0.0228,+0.0435] | 0.0501 | unresolved | +0.0011 [-0.0030,+0.0055] | 0.0061 | null@bar | — (A unresolved) |
| interaction I 256 minus 2048 | -0.0409 [-0.1721,+0.0780] | 0.1894 | unresolved | -0.0017 [-0.0149,+0.0110] | 0.0187 | unresolved | — (A unresolved) |

Same table on **recall@100** — Leg B read at rung ×11.5 only (PREREG §4.2):

| contrast | Leg A mean [CI] | Leg A δ80 | Leg B ×11.5 mean [CI] | Leg B δ80 | signs |
|---|---|---:|---|---:|---|
| size 512 − 256 | -0.0081 [-0.0166,+0.0015] | 0.0139 | -0.0077 [-0.0205,+0.0013] | 0.0160 | — (A null@bar / B unresolved) |
| size 1024 minus 512 | +0.0302 [+0.0098,+0.0530] | 0.0333 | -0.0167 [-0.0295,-0.0051] | 0.0178 | — (A unresolved / B unresolved) |
| size 2048 minus 1024 | +0.0212 [+0.0030,+0.0386] | 0.0266 | -0.0128 [-0.0256,-0.0026] | 0.0167 | — (A unresolved / B unresolved) |
| size 2048 minus 256 | +0.0432 [+0.0150,+0.0763] | 0.0466 | -0.0372 [-0.0603,-0.0179] | 0.0313 | — (A unresolved / B effect) |
| overlap 12 5 minus 0 | +0.0022 [-0.0045,+0.0096] | 0.0106 | -0.0029 [-0.0087,+0.0038] | 0.0089 | — (A null@bar / B null@bar) |
| overlap 25 minus 0 | +0.0002 [-0.0103,+0.0096] | 0.0151 | +0.0000 [-0.0058,+0.0058] | 0.0085 | — (A null@bar / B null@bar) |

### Table 5 — Q3: realised median chunk tokens vs nominal size (DESCRIPTIVE)

Across the 24 config means at rung ×0. **Not pre-registered for inference**; the 24 means share the same queries and are not independent. PREREG §1 Q3 says the expected sign on Leg B is **negative** and that a large negative `r` is a replication of the mechanism, not a falsification.

| predictor | corr with nDCG@10 | corr with recall@10 | corr with MRR@10 |
|---|---:|---:|---:|
| log2 **nominal** size | -0.870 | -0.806 | -0.870 |
| log2 **realised** median tokens | -0.974 | -0.952 | -0.965 |

Fit across all 24 configs: `nDCG@10 ≈ 1.0896 -0.0140 × log2(realised median tokens)`.

| kind | n | mean raw nDCG@10 | mean residual about the fit |
|---|---:|---:|---:|
| `semantic` | 4 | 0.9721 | **-0.0016** |
| `sentence` | 4 | 0.9588 | **+0.0004** |
| `token_window` | 12 | 0.9578 | **+0.0007** |
| `words` | 4 | 0.9650 | **-0.0011** |

Spread of per-kind means: raw **0.0143** → residual **0.0023**.

### Table 6 — reranking, rung x0 (24 configs)

| config | dense nDCG@10 | reranked nDCG@10 | Δ |
|---|---:|---:|---:|
| `fixed_tok256_ov12_5pct` | 0.9838 | 0.9705 | -0.0133 |
| `fixed_tok256_ov0pct` | 0.9821 | 0.9736 | -0.0085 |
| `sentence_tok256_ov12_5pct` | 0.9820 | 0.9814 | -0.0006 |
| `words_tok256_ov12_5pct` | 0.9819 | 0.9740 | -0.0080 |
| `semantic_tok256_ov12_5pct` | 0.9806 | 0.9837 | +0.0031 |
| `fixed_tok256_ov25pct` | 0.9770 | 0.9743 | -0.0027 |
| `words_tok512_ov12_5pct` | 0.9737 | 0.9798 | +0.0061 |
| `semantic_tok2048_ov12_5pct` | 0.9703 | 0.9737 | +0.0034 |
| `semantic_tok512_ov12_5pct` | 0.9700 | 0.9724 | +0.0023 |
| `fixed_tok512_ov0pct` | 0.9676 | 0.9667 | -0.0009 |
| `semantic_tok1024_ov12_5pct` | 0.9674 | 0.9737 | +0.0062 |
| `fixed_tok512_ov25pct` | 0.9647 | 0.9645 | -0.0002 |
| `sentence_tok512_ov12_5pct` | 0.9645 | 0.9720 | +0.0075 |
| `fixed_tok512` | 0.9585 | 0.9770 | +0.0186 |
| `sentence_tok1024_ov12_5pct` | 0.9575 | 0.9699 | +0.0124 |
| `words_tok1024_ov12_5pct` | 0.9564 | 0.9618 | +0.0054 |
| `fixed_tok1024_ov25pct` | 0.9515 | 0.9636 | +0.0121 |
| `fixed_tok1024_ov12_5pct` | 0.9503 | 0.9618 | +0.0115 |
| `fixed_tok1024_ov0pct` | 0.9482 | 0.9653 | +0.0171 |
| `words_tok2048_ov12_5pct` | 0.9478 | 0.9581 | +0.0103 |
| `fixed_tok2048_ov0pct` | 0.9413 | 0.9666 | +0.0253 |
| `fixed_tok2048_ov25pct` | 0.9379 | 0.9584 | +0.0205 |
| `sentence_tok2048_ov12_5pct` | 0.9311 | 0.9602 | +0.0291 |
| `fixed_tok2048_ov12_5pct` | 0.9307 | 0.9650 | +0.0343 |

Dense↔reranked config-mean correlation **r = +0.767** (Leg A: +0.553). Spread 0.0531 → 0.0255.

### Table 6 — reranking, rung x11_5 (12 configs)

| config | dense nDCG@10 | reranked nDCG@10 | Δ |
|---|---:|---:|---:|
| `fixed_tok256_ov0pct` | 0.9595 | 0.9390 | -0.0206 |
| `fixed_tok256_ov12_5pct` | 0.9582 | 0.9291 | -0.0291 |
| `fixed_tok256_ov25pct` | 0.9518 | 0.9431 | -0.0087 |
| `fixed_tok512_ov25pct` | 0.9339 | 0.9340 | +0.0000 |
| `fixed_tok512_ov0pct` | 0.9291 | 0.9364 | +0.0073 |
| `fixed_tok512` | 0.9214 | 0.9424 | +0.0209 |
| `fixed_tok1024_ov25pct` | 0.9145 | 0.9279 | +0.0134 |
| `fixed_tok1024_ov0pct` | 0.9094 | 0.9310 | +0.0215 |
| `fixed_tok1024_ov12_5pct` | 0.9060 | 0.9399 | +0.0339 |
| `fixed_tok2048_ov25pct` | 0.8895 | 0.9207 | +0.0313 |
| `fixed_tok2048_ov0pct` | 0.8863 | 0.9341 | +0.0478 |
| `fixed_tok2048_ov12_5pct` | 0.8675 | 0.9155 | +0.0480 |

Dense↔reranked config-mean correlation **r = +0.612** (Leg A: +0.553). Spread 0.0920 → 0.0276.

### Table 7 — measured cost

| rung | configs | tokens (M) | embed min | tok/s | requests | retries |
|---|---:|---:|---:|---:|---:|---:|
| x0 | 24 | 103 | 8.6 | 198k | 17982 | 0 |
| x11_5 | 12 | 666 | 64.8 | 171k | 104822 | 0 |

Semantic breakpoint pass (x0): notional **101M** / actual **42M** tokens in 3.2 min (218k actual tok/s, 9885 requests, 0 retries). **0 of 400** documents exceeded `max_breakpoint_sentences = 3000` and fell back to fixed_token inside the semantic arm (median 212 sentences/doc).

### Table 8 — the pre-registered questions, scored

| # | question / prediction | outcome | verdict |
|---|---|---|---|
| **Q1** | does the overlap null replicate on Leg B? | 12.5%−0% = -0.0040, CI [-0.0098,+0.0015], δ80 0.0081 vs bar 0.01 | **REPLICATES** |
| **Q2** | is the size disagreement uniform or size-localised? | 1/3 adjacent steps resolve on **both** legs; point estimates disagree in sign at 1/3 steps (size 1024 minus 512) | **NOT ESTABLISHABLE — only 1 of 3 steps resolves on both legs (size 1024 minus 512, signs opposite)** |
| Q2 (descriptive) | point-estimate direction at each adjacent step | size 512 minus 256: A -0.0238 / B -0.0284* (same); size 1024 minus 512: A +0.1204* / B -0.0182* (OPPOSITE); size 2048 minus 1024: A -0.0062 / B -0.0289* (same) | `*` = resolvable on that leg |
| **PB2** | Leg B's size effect is monotone-negative at every step | size 512 minus 256 = -0.0284; size 1024 minus 512 = -0.0182; size 2048 minus 1024 = -0.0289 | **HOLDS** |
| **Q3 / PB4** | does realised-token size explain the grid, as on Leg A? | r(realised) = -0.974 vs r(nominal) = -0.870; per-kind spread 0.0143 → 0.0023 | **REPLICATES** (negative sign as pre-registered) |
| **PB5** | reranking reorders the grid (rung x0) | dense↔reranked r = +0.767 over 24 configs (Leg A +0.553) | **HOLDS** |
| **PB5** | reranking reorders the grid (rung x11_5) | dense↔reranked r = +0.612 over 12 configs (Leg A +0.553) | **HOLDS** |
| **PB7** | semantic >3000-sentence fallback rate exceeds Leg A's 0.30% | 0/400 = 0.00% | **FALSIFIED** |
