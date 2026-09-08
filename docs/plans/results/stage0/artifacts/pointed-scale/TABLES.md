### Pointed reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR (mean over draws ± half-range)

| arm | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 | Δ 4k→32.7k |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.981 ±0.003 | 0.976 ±0.003 | 0.961 ±0.000 | 0.955 | -0.026 |
| `fixed_tok512_ov0pct` | 0.983 ±0.006 | 0.966 ±0.000 | 0.962 ±0.003 | 0.955 | -0.028 |
| `fixed_tok512` | 0.981 ±0.003 | 0.966 ±0.000 | 0.955 ±0.000 | 0.938 | -0.043 |
| `header512` | 0.981 ±0.006 | 0.966 ±0.000 | 0.959 ±0.003 | 0.955 | -0.026 |
| `fixed_tok1024_ov0pct` | 0.981 ±0.003 | 0.970 ±0.003 | 0.964 ±0.006 | 0.938 | -0.043 |
| `fixed_tok2048_ov0pct` | 0.974 ±0.003 | 0.966 ±0.006 | 0.953 ±0.003 | 0.944 | -0.030 |

### Pointed EPACK | reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR

| arm | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 | Δ 4k→32.7k |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.935 ±0.003 | 0.923 ±0.003 | 0.900 ±0.000 | 0.870 | -0.065 |
| `fixed_tok512_ov0pct` | 0.977 ±0.009 | 0.949 ±0.003 | 0.951 ±0.003 | 0.923 | -0.054 |
| `fixed_tok512` | 0.983 ±0.006 | 0.965 ±0.006 | 0.963 ±0.003 | 0.952 | -0.031 |
| `header512` | 0.981 ±0.003 | 0.961 ±0.003 | 0.955 ±0.003 | 0.935 | -0.046 |
| `fixed_tok1024_ov0pct` | 1.000 ±0.000 | 0.994 ±0.000 | 0.981 ±0.003 | 0.982 | -0.018 |
| `fixed_tok2048_ov0pct` | 0.990 ±0.003 | 0.988 ±0.000 | 0.988 ±0.000 | 0.988 | -0.002 |

### Pointed ERET by mode and corpus size — reranker on, B = 16,384 SFR

| arm | mode | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | `vector` | 0.976 | 0.961 | 0.947 | 0.904 |
| `fixed_tok256_ov0pct` | `bm25` | 0.976 | 0.964 | 0.944 | 0.921 |
| `fixed_tok256_ov0pct` | `hybrid` | 0.981 | 0.976 | 0.961 | 0.955 |
| `fixed_tok512_ov0pct` | `vector` | 0.959 | 0.945 | 0.906 | 0.887 |
| `fixed_tok512_ov0pct` | `bm25` | 0.983 | 0.968 | 0.944 | 0.938 |
| `fixed_tok512_ov0pct` | `hybrid` | 0.983 | 0.966 | 0.962 | 0.955 |
| `fixed_tok512` | `vector` | 0.947 | 0.932 | 0.923 | 0.887 |
| `fixed_tok512` | `bm25` | 0.987 | 0.970 | 0.940 | 0.927 |
| `fixed_tok512` | `hybrid` | 0.981 | 0.966 | 0.955 | 0.938 |
| `header512` | `vector` | 0.949 | 0.936 | 0.902 | 0.876 |
| `header512` | `bm25` | 0.976 | 0.964 | 0.944 | 0.938 |
| `header512` | `hybrid` | 0.981 | 0.966 | 0.959 | 0.955 |
| `fixed_tok1024_ov0pct` | `vector` | 0.949 | 0.921 | 0.913 | 0.870 |
| `fixed_tok1024_ov0pct` | `bm25` | 0.974 | 0.964 | 0.953 | 0.927 |
| `fixed_tok1024_ov0pct` | `hybrid` | 0.981 | 0.970 | 0.964 | 0.938 |
| `fixed_tok2048_ov0pct` | `vector` | 0.945 | 0.910 | 0.881 | 0.842 |
| `fixed_tok2048_ov0pct` | `bm25` | 0.972 | 0.964 | 0.945 | 0.927 |
| `fixed_tok2048_ov0pct` | `hybrid` | 0.974 | 0.966 | 0.953 | 0.944 |

### Reach vs log₁₀(corpus size) — fit and projection (PROJECTION, not a measurement)

| arm | slope / decade (linear) | slope 95 % CI | R² | reach @150k linear [95 %] | reach @150k logit [95 %] | reach @500k linear [95 %] | reach @500k logit [95 %] |
|---|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | -0.0323 | [-0.0618, -0.0082] | 0.908 | 0.932 [0.878, 0.977] | 0.897 [0.677, 0.970] | 0.915 [0.847, 0.971] | 0.828 [0.344, 0.961] |
| `fixed_tok512_ov0pct` | -0.0311 | [-0.0594, -0.0089] | 0.819 | 0.931 [0.879, 0.974] | 0.887 [0.668, 0.966] | 0.915 [0.849, 0.969] | 0.807 [0.362, 0.956] |
| `fixed_tok512` | -0.0455 | [-0.0805, -0.0176] | 0.982 | 0.909 [0.847, 0.959] | 0.843 [0.601, 0.941] | 0.885 [0.805, 0.949] | 0.721 [0.262, 0.913] |
| `header512` | -0.0318 | [-0.0610, -0.0089] | 0.831 | 0.929 [0.874, 0.973] | 0.886 [0.670, 0.964] | 0.912 [0.846, 0.967] | 0.809 [0.361, 0.955] |
| `fixed_tok1024_ov0pct` | -0.0386 | [-0.0651, -0.0171] | 0.814 | 0.922 [0.873, 0.961] | 0.883 [0.727, 0.951] | 0.901 [0.840, 0.952] | 0.800 [0.454, 0.923] |
| `fixed_tok2048_ov0pct` | -0.0343 | [-0.0673, -0.0086] | 0.894 | 0.921 [0.865, 0.969] | 0.894 [0.737, 0.961] | 0.903 [0.831, 0.962] | 0.838 [0.481, 0.951] |

### r3 §11 guard 1 (the window half) at each measured corpus size

| N | arms with ERET in [0.15, 0.90] | arms with EPACK\|reach in window | guard 1 verdict |
|---|---|---|---|
| 4,000 | 0/6 | 0/6 | FAIL |
| 8,000 | 0/6 | 0/6 | FAIL |
| 16,000 | 0/6 | 1/6 | FAIL |
| 32,663 | 0/6 | 1/6 | FAIL |

### The step-2 gate — ≥ 2 arms inside [0.15, 0.90] at 150,000 documents

| arm | projected ERET @150k, linear | in window | logit | in window |
|---|---|---|---|---|
| `fixed_tok1024_ov0pct` | 0.922 | OUT | 0.883 | IN |
| `fixed_tok2048_ov0pct` | 0.921 | OUT | 0.894 | IN |
| `fixed_tok256_ov0pct` | 0.932 | OUT | 0.897 | IN |
| `fixed_tok512` | 0.909 | OUT | 0.843 | IN |
| `fixed_tok512_ov0pct` | 0.931 | OUT | 0.887 | IN |
| `header512` | 0.929 | OUT | 0.886 | IN |
| **arms in window** | **0/6** | | **6/6** | |

### Do the arms separate? — between-arm spread at each corpus size

| N | lowest arm's ERET | highest arm's ERET | between-arm spread | between-arm spread, EPACK unconditional |
|---|---|---|---|---|
| 4,000 | 0.974 | 0.983 | **0.009** | 0.064 |
| 8,000 | 0.966 | 0.976 | **0.009** | 0.064 |
| 16,000 | 0.953 | 0.964 | **0.011** | 0.081 |
| 32,663 | 0.938 | 0.955 | **0.017** | 0.102 |

### The paired size contrasts at each corpus size — d, σ_d, and the query count 80 % power at ε = 0.05 would need

| contrast | N = 4,000 | N = 8,000 | N = 16,000 | N = 32,663 |
|---|---|---|---|---|
| **N1** `fixed_tok512` → `fixed_tok1024_ov0pct` | d -0.0000<br>σ_d 0.101<br>n₈₀ 32 | d +0.0038<br>σ_d 0.079<br>n₈₀ 20 | d +0.0094<br>σ_d 0.097<br>n₈₀ 30 | d +0.0000<br>σ_d 0.185<br>n₈₀ 108 |
| **N3** `fixed_tok512` → `fixed_tok2048_ov0pct` | d -0.0075<br>σ_d 0.100<br>n₈₀ 32 | d +0.0000<br>σ_d 0.094<br>n₈₀ 28 | d -0.0019<br>σ_d 0.186<br>n₈₀ 110 | d +0.0056<br>σ_d 0.199<br>n₈₀ 125 |
| **R1** `fixed_tok256_ov0pct` → `fixed_tok2048_ov0pct` | d -0.0075<br>σ_d 0.142<br>n₈₀ 64 | d -0.0094<br>σ_d 0.130<br>n₈₀ 54 | d -0.0075<br>σ_d 0.170<br>n₈₀ 91 | d -0.0113<br>σ_d 0.184<br>n₈₀ 107 |
| **R2** `header512` → `fixed_tok512_ov0pct` | d +0.0019<br>σ_d 0.025<br>n₈₀ 2 | d +0.0000<br>σ_d 0.000<br>n₈₀ — | d +0.0038<br>σ_d 0.050<br>n₈₀ 8 | d +0.0000<br>σ_d 0.107<br>n₈₀ 36 |

### Per-query difficulty — the truly easy queries

| N | queries reached by every arm and every draw | fraction |
|---|---|---|
| 4,000 | 170 | 0.961 |
| 8,000 | 168 | 0.949 |
| 16,000 | 163 | 0.921 |
| 32,663 | 161 | 0.910 |
| **every size** | **161** | **0.910** |

### Full-corpus check — this harness against Stage 0b′'s published table

| arm | ERET here (N = 32,663) | ERET Stage 0b′ | Δ | EPACK here | EPACK Stage 0b′ | Δ |
|---|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.955 | 0.955 | +0.000 | 0.870 | 0.870 | +0.000 |
| `fixed_tok512_ov0pct` | 0.955 | 0.955 | +0.000 | 0.923 | 0.923 | +0.000 |
| `fixed_tok512` | 0.938 | 0.938 | +0.000 | 0.952 | 0.952 | +0.000 |
| `header512` | 0.955 | 0.955 | +0.000 | 0.935 | 0.935 | +0.000 |
| `fixed_tok1024_ov0pct` | 0.938 | 0.938 | +0.000 | 0.982 | 0.982 | +0.000 |
| `fixed_tok2048_ov0pct` | 0.944 | 0.944 | +0.000 | 0.988 | 0.988 | +0.000 |
