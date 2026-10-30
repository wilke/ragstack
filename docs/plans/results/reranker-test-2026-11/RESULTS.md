<!-- experiment: the dedicated reranker test | 2026-10-30 | COMPLETE — family A tested on docs-A; set P GATE-NOT-EVALUABLE -->
# Results: the dedicated reranker test, pre-registered

**Status: COMPLETE.** Family A (H1–H3) was tested on **docs-A / T512o0** as pre-registered: **H3 confirmed**
(the reranker fed BM25's own candidates is non-inferior to BM25 alone), **H1 and H2 not confirmed** — and on
both the observed direction is the reverse of the hypothesis (the default beats BM25 alone; the fused pool
has the higher first-stage recall). On the papers-OC owner's 155-record set (**set P**, exploratory by
design) every hypothesis is **GATE-NOT-EVALUABLE**, for the three reasons the harness lists; what is reported
for P is **descriptive**. Family B (the round-2 sets) is **absent**: round 2 produced no set
([`../glossbench-round2-2026-11/RESULTS.md`](../glossbench-round2-2026-11/RESULTS.md)). Every number in this
file is in [`artifacts/reranker-docsA-r2.json`](artifacts/reranker-docsA-r2.json) or
[`artifacts/reranker-P-r2.json`](artifacts/reranker-P-r2.json), or in `DEVIATIONS.md`, and the sentence says
which. The labels *confirmed*, *not confirmed* and *GATE-NOT-EVALUABLE* are the harness's own
(`hypotheses.<H>.status`).

## Provenance

| | |
|---|---|
| pre-registration | [prereg/PREREG-reranker-test.md](prereg/PREREG-reranker-test.md), sha256 `5dfe9e65c96add94a550a7a040d8a425c99d949194b4e5b851f26483f15ad230`, frozen 2026-10-30T02:19:33Z on the owner's go (commit 9e109c3, merge dedc990); never edited; the sha verified by hand against the README and the private `PREREG.sha256` before the first `pools` run |
| deviations | [DEVIATIONS.md](DEVIATIONS.md): **D-1** the pre-committed re-score yields A4-50 only (A0, A2 absent); **D-2** input-reader fixes (#871) and the pin move; **D-3** set P's gold uses the papers-OC owner's actual quote rule; **D-4** the docs-A identity reference is rebuilt on the bench copy (#873); **D-5** set P's `bm25@100` lists short on 19 records, verified as exhaustion, run with `--allow-short`; **D-6** the glossary-boost probe structurally `not_testable` on P, continued with `--boost-declared 1.0`; **D-7** the L replication (D2) was not run |
| code pin | every output: **`f408b7a`** (`v1.6.21+f408b7a`, merge of #873), clean detached worktree, `dirty false, untracked 0`, `citable true`, not in a tools image (`in_image false`, the *not in an image* warning kept). The D-3 re-score reads a step-7 result built at `db97c28` (clean) |
| harness | `python/scripts/eval/reranker_test.py` (`pools`, `rerank`, `fuse`, `census`, `stats`, `power-table`, `part4-rescore`); analysis machinery imported from `deployed_docs_part4` / `chunktrial_stats` (prereg §6) |
| reranker | `BAAI/bge-reranker-v2-m3`, HF snapshot `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` (read from the sidecar's cache; equals the expected snapshot), fp16 on one GPU; `MAX_LENGTH` 4096 and `RERANK_BATCH_SIZE` 32 **declared** from the sidecar's launch environment (`/ready` does not expose them); installed `sentence-transformers` **5.6.0** (outside the `requirements.txt` pin `>=3.0,<4`, `pin_drift` recorded), `transformers` 5.12.1, `torch` 2.12.1 |
| embedder | docs-A dense leg: SFR-Embedding-Mistral in-process (the part-4 embeddings); set P: the API's own |
| sets | **docs-A / T512o0**: 735 valid questions (question-file sha `b2fbbd56…`), 208 clusters (`doc_id`), bench index of 1,113 chunks @512 over 336 documents, **1** chunk with a glossary; in-process arms. **papers-OC (set P)**: the owner's 155 held records (sha `c720261e…`) joined to its pairs file (sha `f8d3b7f1…`; 57 of 65 pairs used, 73 distinct queries, 7 papers), on the bench copy's chunk dump (sha `cf40b916…`; 14,456 chunks @512, 275 documents, **241** with a glossary, census `rules/3@file/1` on all 14,456 **before and after every pass**); API arms, reranked arms offline |
| gold rules | docs-A: the parts 2–4 rule (NFKC, casefold, whitespace removed; `build_gold`), 732 included / 3 excluded `no_gold_chunk_in_index`. Set P: the papers-OC owner's rule (D-3: NFKC, whitespace runs to one space, strip, casefold, containment or first/last ⌈0.6·len⌉), 153 included / 2 excluded |
| tokenizers | chunk sizes in SFR-Embedding-Mistral tokens (@512); reranker input lengths in XLM-R tokens (bge-reranker-v2-m3's tokenizer, `census`) |
| settings (prereg §3) | docs-A: all seven `PREREG_SETTINGS` observed in-process = expected. Set P: all seven **declared** via `--server-settings` (the harness's own `/v1/config` read returned 403, `config_readable false`); `TEXT_SYMBOL_GLOSSARY_BOOST` 1.0 **declared** (D-6). The admin `/v1/config` read by the coordinator (`rerank_enabled true`, `rerank_candidates 50`, model as above) was identical at all **eight** reads around the three set-P passes and the docs-A pass (full-config hash prefix `f85cf2e2f73c36ab`) |
| artifacts | [`artifacts/reranker-docsA-r2.json`](artifacts/reranker-docsA-r2.json), [`artifacts/reranker-P-r2.json`](artifacts/reranker-P-r2.json): counts-only copies of the stats, pools, rerank, fuse, census, gold, power and re-score outputs made by [`artifacts/make_rr_artifacts.py`](artifacts/make_rr_artifacts.py); source sha256s inside; `artifact_provenance.citable true` (inherited from the outputs) |
| what the partner saw | nothing: no call left this host; the papers-OC owner's gateway was not used (prereg §4) |

## Contents

- [The answer](#the-answer)
- [Gates, per set](#gates-per-set)
- [Family A: docs-A / T512o0 (confirmatory)](#family-a-docs-a--t512o0-confirmatory)
- [Set P: papers-OC (exploratory, GATE-NOT-EVALUABLE)](#set-p-papers-oc-exploratory-gate-not-evaluable)
- [What this shows](#what-this-shows)
- [What is not reported](#what-is-not-reported)
- [Threats to the reading](#threats-to-the-reading)
- [Reproducing](#reproducing)

## The answer

The test asked why, on papers-OC, BM25 alone at top 10 had found more records than the shipped
hybrid-fuse-rerank path, and decomposed every reranked figure into *not in the pool* + *ordering loss* +
*found* (prereg §1). On the confirmatory set the premise does not hold:

| set | H1: BM25 alone > default at top 10 | H2: fused pool recall < BM25 pool recall at depth 50 | H3: rerank over BM25's pool non-inferior to BM25 (δ = 0.05) |
|---|---|---|---|
| **docs-A / T512o0** (n = 732 of 735, 208 clusters) | **not confirmed** — 629 vs **707**; 6 gained / 84 lost; p = 1.0, Holm 1.0 | **not confirmed** — 703 vs **727**; 1 / 25; p = 1.0, Holm 1.0 | **confirmed** — 688 vs 629; 65 / 6; p = 1.0 × 10⁻⁴, Holm 3.0 × 10⁻⁴; 90 % CI of the net share [+0.060, +0.102], lower bound above −δ |
| **papers-OC (set P)** (n = 153 of 155, 7 clusters) | **GATE-NOT-EVALUABLE** — 61 vs 52; 20 / 11; p = 0.5 | **GATE-NOT-EVALUABLE** — 82 vs 83; 2 / 3; p = 0.875 | **GATE-NOT-EVALUABLE** — 54 vs 61; 11 / 18; p = 0.266 |

On docs-A the default finds 78 more records than BM25 alone, and reranking BM25's own 50 candidates
finds 59 more than BM25's own order. On papers-OC, descriptively and from one run over seven papers, the
two pools hold a gold chunk for almost the same number of records at depth 50 (82 BM25, 83 fused), and
both reranked arms place fewer of them in the top 10 than BM25's own order does. The gates on P are
structural (7 clusters; `bm25@100` lists short on 19 records, verified as index exhaustion) and nothing on P
is a test.

## Gates, per set

Prereg §3, §5, §8. Source: `stats.identity_gate`, `stats.depth_read_back`, `stats.inactive_reranker`,
`stats.manipulation_check`, `stats.repeat`, `pools.manipulation` in each artifact.

| gate | docs-A / T512o0 | papers-OC (set P) |
|---|---|---|
| identity gate (offline `content+gloss` top 10 = the reference list and order, 3 seeded probes) | **pass**, 3 / 3; reference: part 4's committed retrieval code run on the bench copy (D-4; 735 rows, 50-long lists, `rules` condition, built at `f408b7a` clean; its hybrid@50 pools equal this run's on 735 / 735 questions) | **pass**, 3 / 3 against the API's own reranked capture (`live:hybrid@50`); over all records the offline list equals the live list on **155 / 155** (`identity_all`) |
| depth read-back | every list served in full (depths 10–200; under-served 0, exhausted 0) | **`bm25@100` under-served on 19 of 155 records** (served min 23; 5 distinct queries): **GATE-NOT-EVALUABLE** under `--allow-short`, inherited by every arm built on it (A0, A5-D, A9-bm25) and by H1–H3. All other lists full; 0 degraded responses; the API does not echo `top_k`. D-5 verifies the 19 as exhaustion, not a clamp |
| inactive-reranker gate (reranked top 10 = its pool's order on every record) | **not fired** on any arm: `rr:hybrid@50:content+gloss` identical on 1 / 732, `rr:bm25@50` on 0 / 732; `gloss-only` identical on 715 / 732 (expected: 36,717 of 36,750 passages empty) | **not fired**: every live and offline arm identical on 0 / 153; `gloss-only` on 22 / 153 (6,400 of 7,750 passages empty) |
| glossary-boost probe | `in-process` (the in-process BM25 applies `--glossary-boost 1.0` itself; probe not applicable, recorded) | **`not_testable`** — 0 qualifying chunks (every ≥ 5-character whole-word glossary token also occurs in some chunk's content); continued as **`declared`** (`--boost-declared 1.0`, D-6). Not a pass. |
| fixed settings (`PREREG_SETTINGS`, 7 values) | `ok`, observed in-process | `ok`, every value **declared** (`--server-settings`), not read back |
| admin `/v1/config` | identical at start and end (hash prefix `f85cf2e2f73c36ab`) | identical at start and end of all three passes (same hash) |
| census of the bench copy | 1,113 chunks, 1 glossaried (part-4 arm) | 14,456 chunks at `rules/3@file/1` before and after every pass |
| noise-floor repeat (D12: A0 and A4-50 once) | second `pools` + `rerank` pass (its own sidecar scores): **0** records differ in top 10 on all 8 lists, found@10 differs **0** | second `pools` pass: found@10 differs **0** on all 9 lists; top 10 differs on **3** records for `hybrid@50` and `hybrid@100` (fused pools), **0** for the live reranked lists and `bm25@100` |
| reranker snapshot | `953dc6f6…` = expected | same |
| clusters ≥ 8 | 208 | **7 → GATE-NOT-EVALUABLE** for the corpus-level claim (prereg §8) |

## Family A: docs-A / T512o0 (confirmatory)

Source: `artifacts/reranker-docsA-r2.json`, `stats.*`. n = **732** included of 735 (3 excluded,
`no_gold_chunk_in_index`), 208 clusters (pages), gold rule `any`, `--prefer live` (no live capture exists
on docs-A, so A4-50 and A5-50 are the offline rerank behind the passed identity gate; `family.offline_arms`).
Test: cluster sign-flip permutation, 10,000 flips, seed 261029, one-sided for H1/H2, H3 shifted by δ = 0.05;
Holm over three (a gated or absent member would enter at p = 1.0; none did); 90 % two-sided cluster-bootstrap
percentile CI of the net share (= the one-sided 95 % bound for H3). Exact McNemar is descriptive only.

### Confirmatory table

| H | statement (`hypotheses.<H>.statement`) | arms | found@10 / ceiling@50 | gained / lost (net) | non-zero clusters | p | mid-p | Holm | 90 % CI (net share) | McNemar (two-sided, descriptive) | **status** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | found@10(A0) − found@10(A4-50) > 0 | A0 `bm25@200` prefix; A4-50 `rr:hybrid@50:content+gloss` | 629 vs 707 | 6 / 84 (**−78**) | 66 | 1.0 | 1.0 | 1.0 | [−0.130, −0.084] | 1.1 × 10⁻¹⁸ | **not confirmed** |
| H2 | ceiling@50(hybrid) − ceiling@50(bm25) < 0, tested as bm25 − hybrid > 0 | pools `bm25@50`, `hybrid@50` | 703 vs 727 | 1 / 25 (**−24**) | 22 | 1.0 | 1.0 | 1.0 | [−0.047, −0.021] | 8.0 × 10⁻⁷ | **not confirmed** |
| H3 | found@10(A5-50) − found@10(A0) > −δ | A5-50 `rr:bm25@50:content+gloss`; A0 | 688 vs 629 | 65 / 6 (**+59**) | 208 | 1.0 × 10⁻⁴ | 0.0 | 3.0 × 10⁻⁴ | [+0.060, +0.102] | 1.3 × 10⁻¹³ | **confirmed** |

p = 1.0 × 10⁻⁴ is the permutation minimum (1 / 10,001). `holm_drop_gated` (the labelled sensitivity,
D14) gives the same three verdicts, as it must with no gated member. The H3 non-inferiority test's
shift-to-null construction is approximate for d ∈ {−1, 0, +1} (prereg §6), so its level is nominal; the
CI's lower bound (+0.060) lies above −δ independently.

**Reading the pattern.** H1 and H2 are *not confirmed*, and in both the discordant records run almost
entirely against the hypothesis: the default finds 84 records BM25 alone misses and misses 6 BM25 finds;
the fused 50-pool holds gold for 25 records the BM25 50-pool lacks and lacks it for 1. The two-sided
exploratory contrasts (`exploratory."A4-50 - A0"`, uncorrected, Bonferroni count 42 beside it: p = 1.0 × 10⁻⁴)
show the same reversal. These are not nulls: the design saw an effect, in the direction opposite to the
pre-registered one. H3 is confirmed with the CI's lower bound a full δ above the margin: on docs-A the
reranker, given BM25's own candidates, is not merely non-inferior to BM25's order but ahead of it.

### Power floor beside the verdicts

Prereg §7, re-computed on the realised layout (208 clusters, 732 questions; `power_realised_layout`,
2,000 simulations × 2,000 flips, Holm worst case α/3; ICC-0 rows, the ICC-0.1 rows are within 0.05):

| discordance | H1/H2 power at gain share .65 / .70 / .75 | H3 power at true net 0 / −.01 / −.02 |
|---|---|---|
| .05 | .31 / .55 / .78 | 1.00 / .99 / .91 |
| .10 | .62 / .88 / .98 | .98 / .88 / .64 |
| .15 | .82 / .97 / 1.00 | .90 / .72 / .47 |
| .20 | .92 / 1.00 / 1.00 | .80 / .61 / .37 |
| .25 | .96 / 1.00 / 1.00 | .70 / .51 / .31 |

Observed discordance: H1 **90 / 732 = .123** (power .62–.82 at gain share .65 had the effect been in the
hypothesised direction); H2 **26 / 732 = .036**, below the table's first row — at that discordance H2 could
only have been resolved by a lopsided split, which it had (1 : 25), in the reverse direction; H3 **71 / 732 =
.097**, where the test has power .98 against a true net of 0 and .88 against −0.01. Neither *not confirmed*
is an underpowered null; both are resolved reversals.

### Sensitivities

- **`--gold-rule all`** (a record counts only when every gold chunk is placed; `stats_gold_rule_all`):
  found@10 A0 **494**, A4-50 **622**, A5-50 **598**; ceilings@50 bm25 653, hybrid 705. H1 not confirmed
  (14 / 142), H2 not confirmed (2 / 54), H3 confirmed (118 / 14; p = 1.0 × 10⁻⁴, Holm 3.0 × 10⁻⁴; CI
  [+0.115, +0.169]). The gap between the two rules is the gold's shape: 372 of the 732 questions carry two
  quotes and the median question has **2** gold chunks (238 have one; max 56; `gold.sizes`), so *all* is
  materially stricter here.
- **D3 pre-committed re-score** (`part4_rescore_d3`; D-1): the step-7 result (built at `db97c28`,
  clean, condition `rules`) yields **A4-50 found@10 = 622 / 735** under part 4's own *all-quotes text-hit*
  rule (cell `k10`); A0 and A2 are **absent** from that file, so H1 and H3 are *not computed* from it
  (`d3_complete false`). The fresh run's A4-50 under `--gold-rule all` is **622 / 732**. The counts agree;
  the rules differ (text hit over all quotes vs gold-chunk-id over all quotes; n 735 vs 732), so the
  agreement is of totals, and a per-record agreement was **not computed** (absent).
- **mid-p** (reported, never decides): H1 1.0, H2 1.0, H3 0.0.

### Exploratory (E1–E4, E6; uncorrected p, Bonferroni count 42 beside each; none is a finding)

Source: `stats.arms.<arm>.summary`, `stats.decomposition`, `stats.ceilings`, `stats.exploratory`.

**Decomposition** n = 732 = not in pool + ordering loss + found@10 (`decomposition`):

| arm | pool | ceiling@D | not in pool | ordering loss | **found@10** | found@20 | found@50 | median rank [IQR] | share at rank 11–50 | MRR@50 |
|---|---|---|---|---|---|---|---|---|---|---|
| A0 bm25 (prefix) | `bm25@200` | — | — | — | **629** | — | — | 1 [1, 3] | 0 | .587 |
| A1 dense (prefix) | `vector@200` | — | — | — | 655 | — | — | 2 [1, 3] | 0 | .575 |
| A2 hybrid-shallow | `hybrid@10` | 689 | — | — | 689 | — | — | 1 [1, 3] | 0 | .668 |
| A3 fused 50-pool's own top 10 | `hybrid@50` | 727 | — | — | 683 | — | — | 1 [1, 2.5] | 0 | .666 |
| A4-10 | `hybrid@10` | 689 | 43 | 0 | 689 | — | — | 1 [1, 2] | 0 | .769 |
| A4-20 | `hybrid@20` | 712 | 20 | 12 | 700 | 712 | — | 1 [1, 2] | .016 | .774 |
| **A4-50 default** | `hybrid@50` | **727** | **5** | **20** | **707** | 719 | 727 | 1 [1, 2] | .027 | .780 |
| A4-100 | `hybrid@100` | 729 | 3 | 23 | 706 | 719 | 726 | 1 [1, 2] | .027 | .779 |
| A4-200 | `hybrid@200` | 731 | 1 | 25 | 706 | 720 | 726 | 1 [1, 2] | .027 | .779 |
| A5-10 | `bm25@10` | 629 | 103 | 0 | 629 | — | — | 1 [1, 2] | 0 | .719 |
| A5-20 | `bm25@20` | 673 | 59 | 6 | 667 | 673 | — | 1 [1, 2] | .008 | .750 |
| **A5-50** | `bm25@50` | **703** | **29** | **15** | **688** | 697 | 703 | 1 [1, 2] | .020 | .763 |
| A5-100 | `bm25@100` | 717 | 15 | 21 | 696 | 707 | 716 | 1 [1, 2] | .027 | .769 |
| A5-200 | `bm25@200` | 727 | 5 | 21 | 706 | 718 | 725 | 1 [1, 2] | .026 | .779 |
| A6 rerank over dense | `vector@50` | 709 | 23 | 15 | 694 | 701 | 709 | 1 [1, 2] | .020 | .771 |
| A9-bm25 (RRF k 60 of A4-50 rank with `bm25@50` rank) | — | — | — | — | 682 | 696 | 725 | 1 [1, 3] | .059 | .717 |
| A9-fused (with `hybrid@50` rank) | — | — | — | — | 701 | 716 | 727 | 1 [1, 2] | .036 | .745 |

Ceilings (`ceilings`, records with a gold chunk in the first D of the leg): bm25 629 / 673 / 703 / 717 / 727;
hybrid 689 / 712 / 727 / 729 / 731; vector 655 / 687 / 709 / 721 / 730, for D = 10 / 20 / 50 / 100 / 200.

Selected contrasts (gained / lost, two-sided uncorrected p; m = 42): A4-50 − A0 **84 / 6**, 1.0 × 10⁻⁴;
A5-50 − A4-50 **1 / 20**, 3.8 × 10⁻⁵; A6 − A4-50 4 / 17, 0.0037; A4-50 − A2 28 / 10, 0.0050; A4-50 − A3
33 / 9, 3.0 × 10⁻⁴; A4-100 − A4-50 1 / 2, 1.0; A4-200 − A4-50 3 / 4, 1.0; A5-200 − A0 83 / 6, 1.0 × 10⁻⁴;
A9-fused − A4-50 7 / 13, 0.33; A9-bm25 − A4-50 6 / 31, 1.0 × 10⁻⁴; A1 − A0 75 / 49, 0.049.

**E2, reranker input (A7, over the same `hybrid@50` pool).** `content` **707**, `content+gloss` **707**,
`gloss+content` **707**, `content+shuffled` **707**, `gloss-only` **682** (exploratory only: 36,717 of 36,750
passages empty; ordering loss 45). The four non-empty renderings are identical on every record (0 / 0
against A4-50): the docs-A bench index carries a glossary on **1 of 1,113** chunks, which is why this set
isolates fusion and the reranker from the glossary (prereg §4). The shuffle had **1 bearer and 1 fixed point**
(`shuffle.unchanged 1`), so `content+shuffled` equals `content+gloss` by construction here; the shuffled
figure says nothing about the glossary on this set.

**E6, neighbour hits** (`census.neighbour_hits`: a top-10 chunk that is `prev`/`next` of a gold chunk):
A4-50 **488** of 732 records, of which **8** not found@10; A0 (`bm25@200` prefix) 374, of which 30; A5-50 460,
of which 14. **Length census** (XLM-R tokens, `MAX_LENGTH` 4096 declared): passages `content+gloss` median
483, p95 545, max 643; query + passage pairs `hybrid@50|content+gloss` median 526, p95 580, max **692**;
**0** pairs over `MAX_LENGTH` in any rendering. Truncation (C3) is ruled out on docs-A.

## Set P: papers-OC (exploratory, GATE-NOT-EVALUABLE)

Source: `artifacts/reranker-P-r2.json`. **Exploratory throughout** (prereg Status, §4). n = **153** included of
155 (2 excluded, `no_gold_chunk_in_index`), **7** clusters (papers; 138 of the 155 records are in one paper,
lab book Part 3), 73 distinct queries, gold under the papers-OC owner's rule (D-3), glossary condition
`rules/3@file/1`, boost **declared** (D-6), `--prefer live`: A4-50, A5-50 and A6 are the API's own reranked
captures (`route: api`); the depth sweep and A7 are offline (`route: offline`) behind the passed identity gate.

### Why H1–H3 are GATE-NOT-EVALUABLE (every reason, as the harness lists them)

| H | `gate` | p (exact enumeration) | Holm (entered at 1.0) |
|---|---|---|---|
| H1 | `depth read-back: bm25@100 under-served`; `fewer than 8 clusters (7)`; `2 non-zero clusters: alpha/3 not attainable` | 0.5 | 1.0 |
| H2 | `depth read-back: bm25@100 under-served`; `fewer than 8 clusters (7)`; `3 non-zero clusters: alpha/3 not attainable` | 0.875 | 1.0 |
| H3 | `depth read-back: bm25@100 under-served` (inherited by both A5-50 and A0); `fewer than 8 clusters (7)` | 0.266 | 1.0 |

With 7 clusters the minimum attainable two-sided p is 2 / 128 (prereg §7); with 2 or 3 non-zero clusters,
α/3 is unreachable. The `bm25@100` lists were short on **19 records** (served 23 on 8 records, 36 on 10,
79 on 1; 5 distinct queries, records sharing a query share a list): D-5 shows each served length equals the
index's own count of documents matching the server's `most_fields` clause, i.e. the index was exhausted, not
clamped. The API route cannot tell the two apart, so the harness mark stands as pre-registered and the
verification is reported descriptively. The A0 top 10 itself is full on every record (served ≥ 23); the
mark is inherited from the list. For the API's own A5-50 (`rerank_candidates 50`), those 19 records had
23–36 candidates to rerank, not 50.

### Descriptive results

**found@10 per arm** (`arms.<arm>.summary`; one run; `gate` column from `arms.<arm>.gate`):

| arm | route / list | found@10 | found@20 | found@50 | median rank [IQR] | share at rank 11–50 | MRR@50 | gate |
|---|---|---|---|---|---|---|---|---|
| A0 bm25 (prefix of `bm25@100`) | api | **61** | — | — | 2 [1, 5] | 0 | .197 | `bm25@100` under-served |
| A1 dense (prefix of `vector@100`) | api | 9 | — | — | 2 [1, 8] | 0 | .034 | — |
| A2 hybrid-shallow (`hybrid@10`) | api | 50 | — | — | 5 [3, 7] | 0 | .098 | — |
| A3 fused 50-pool's own top 10 | api | 20 | — | — | 3 [2, 6] | 0 | .058 | — |
| A4-10 | offline | 50 | — | — | 2 [1, 4.75] | 0 | .180 | — |
| A4-20 | offline | 54 | 65 | — | 4 [1, 8] | .072 | .181 | — |
| **A4-50 default** | **api (live)** | **52** | — | — | 2.5 [1, 6.25] | 0 | .177 | — |
| A4-100 | offline | 54 | 65 | 81 | 9 [2, 33] | .176 | .181 | — |
| A5-10 | offline | 61 | — | — | 3 [2, 5] | 0 | .180 | under-served |
| A5-20 | offline | 62 | 75 | — | 6 [2, 9] | .085 | .178 | under-served |
| **A5-50 rerank over bm25** | **api (live)** | **54** | — | — | 3 [1, 7] | 0 | .166 | under-served |
| A5-100 | offline | 59 | 67 | 86 | 9 [2, 28] | .176 | .194 | under-served |
| A6 rerank over dense | api (live) | 15 | — | — | 1 [1, 3.5] | 0 | .066 | — |
| A7-`content` | offline | **43** | 56 | 83 | 10 [2, 29] | .261 | .176 | — |
| A7-`content+gloss` (= A4-50's rendering) | offline | **52** | 64 | 83 | 7 [2, 17] | .203 | .187 | — |
| A7-`gloss+content` | offline | **40** | 59 | 83 | 11 [2, 21.5] | .281 | .174 | — |
| A7-`content+shuffled` | offline | **41** | 57 | 83 | 13 [2, 30] | .275 | .168 | — |
| A7-`gloss-only` (exploratory only) | offline | 55 | 61 | 83 | 5 [2, 24] | .183 | .187 | — |
| A9-bm25 (RRF of A4-50 rank with `bm25@50` rank) | offline fuse | 62 | 80 | 83 | 5 [2, 11] | .137 | .196 | an input under-served |
| A9-fused (with `hybrid@50` rank) | offline fuse | 44 | 65 | 83 | 9 [4, 18] | .255 | .136 | — |

The API's live lists are top-10 captures, so found@20 / found@50 are *absent* for them (written as —).
The offline `content+gloss` rerank of `hybrid@50` reproduces the live A4-50 list on **155 / 155** records
(`identity_all`), so the A7 rows are on the same footing as the live default.

**Ceilings** (`ceilings`, D = 10 / 20 / 50 / 100): bm25 **61 / 75 / 82 / 94** (gated); hybrid **50 / 65 / 83 /
93**; vector 9 / 12 / 19 / 34.

**Decomposition** n = 153 = not in pool + ordering loss + found@10 (`decomposition`):

| arm | pool | ceiling | not in pool | **ordering loss** | found@10 |
|---|---|---|---|---|---|
| A4-50 default | `hybrid@50` | 83 | 70 | **31** | 52 |
| A5-50 rerank over bm25 | `bm25@50` | 82 | 71 | **28** | 54 |
| A4-100 | `hybrid@100` | 93 | 60 | 39 | 54 |
| A5-100 | `bm25@100` | 94 | 59 | 35 | 59 |
| A4-20 | `hybrid@20` | 65 | 88 | 11 | 54 |
| A5-20 | `bm25@20` | 75 | 78 | 13 | 62 |
| A6 | `vector@50` | 19 | 134 | 4 | 15 |
| A7-`content` | `hybrid@50` | 83 | 70 | 40 | 43 |
| A7-`content+shuffled` | `hybrid@50` | 83 | 70 | 42 | 41 |
| A7-`gloss+content` | `hybrid@50` | 83 | 70 | 43 | 40 |
| A7-`gloss-only` | `hybrid@50` | 83 | 70 | 28 | 55 |

**Pairwise, descriptive** (gained / lost against the second arm; the paper-clustered sign-flip p is 1.0 or
0.5 on every exploratory contrast here because at most 3 clusters are non-zero, so only exact McNemar —
which ignores clustering, with 138 of 155 records in one paper — is quoted, two-sided, Bonferroni count 38
beside it): A0 − A4-50 **20 / 11** (McNemar 0.15); A5-50 − A0 11 / 18 (0.26); A5-50 − A4-50 4 / 2 (0.69);
A7-`content` − A4-50 **2 / 11** (0.022); A7-`content+shuffled` − A4-50 **3 / 14** (0.013); A7-`gloss+content` −
A4-50 1 / 13 (0.0018); A7-`gloss-only` − A4-50 21 / 18 (0.75); A9-bm25 − A4-50 16 / 6 (0.052); A9-fused − A4-50
4 / 12 (0.077); A4-100 − A4-50 3 / 1; A5-100 − A0 18 / 20 (0.87).

**The shuffled control differs from the papers-OC owner's** (prereg §3, D9): A7-`content+shuffled` is a
seeded permutation of whole glossary strings over **all 241** glossary-bearing chunks of the dump with fixed
points allowed (**4 unchanged**; seed 261029, `--shuffle-unit string`). The owner's 57 / 155 control of
2026-10-22 was a derangement over the glossaried chunks present in the 50-pools, against a different gold
(Test 3's) and pool. The two shuffled figures are not the same measurement.

**Gold-size distribution** (`gold.sizes`; D-3): gold chunks per included record median **1** (87 records have
one gold chunk, 56 have two, 3 have four, 3 have eleven, **2 have 32 and 2 have 50**; p90 = 2, max 50);
documents per record median 1 (87 / 56 / 3 / 3 / 2 at 18 / 2 at 27). Every record carries exactly one
quote, so **`--gold-rule all` gives identical figures to `any`** on every arm and hypothesis
(`stats_gold_rule_all`).

**Length census** (XLM-R tokens, `MAX_LENGTH` 4096 declared): passages median 483, p95 546, max 681;
pairs `hybrid@50|content+gloss` median 523, p95 616, max 733; the longest pair in any rendering **745**
(`bm25@50|content+shuffled`); **0** over `MAX_LENGTH` in any rendering. Truncation (C3) is ruled out on P as well. **Neighbour hits**: live A4-50 21
records with a gold neighbour in the top 10 (7 of them not found@10); A0 27 (7).

**Glossary boost, declared** (D-6): the probe found **0** chunks whose glossary holds a ≥ 5-character ASCII
token absent from every chunk's content (241 glossaried chunks, `max_glossary_df` 10), so `not_testable`
is structural for this condition and the run continued with the boost **declared** at 1.0 (the server
release's management declaration). Behavioural support, from D-5's read-only counts: on **2 of the 5**
exhaustion queries the server served more documents than match on content alone (23 vs 22; 23 vs 17) —
glossary-only matches were returned, so the glossary field is queried (boost > 0). Its magnitude is not
verified.

## What this shows

In the record's own voice; descriptive where the set is exploratory.

1. **On docs-A, the reranker helps, strongly.** The shipped default places a gold chunk in the top 10 for
   707 of 732 questions against 629 for BM25 alone (84 gained, 6 lost); the fused 50-pool's recall is the
   higher one (727 vs 703); and the reranker given BM25's own 50 candidates beats BM25's own order by 59
   (H3 confirmed, CI lower bound +0.060 above −δ). H1 and H2 are not confirmed because their premise is
   reversed on this set, not because the design lacked power. Of the 732 questions, the default's whole
   shortfall is 5 not in the pool plus 20 ordering losses.
2. **On papers-OC, descriptively, the loss against BM25 is ordering, not the pool ceiling.** At depth 50
   the two pools hold gold for 82 (BM25) and 83 (fused) records — the C1 mechanism (fusion diluting the
   pool) is not what the counts show. Both reranked arms then place fewer gold chunks in the top 10 than
   BM25's own order: the default 52 (31 ordering losses of 83), rerank-over-BM25 54 (28 of 82), BM25 alone
   61. Deeper BM25 pools do not recover it (A5-100: 59). This is one run, 153 records, 7 papers, 138 of them
   from one paper, under gates that make it untestable; it is the pattern C2 of prereg §2 as a description,
   not a confirmed cause.
3. **On papers-OC, what the reranker reads matters, descriptively.** Over the same 50 candidates,
   `content` alone finds 43, `content+gloss` 52, `gloss+content` 40, and `content+shuffled` 41. The
   shuffled control falling to the content-only level is consistent with the glossary's *content*, not its
   presence or length, carrying the gain; the counts are small (2 / 11, 3 / 14 discordant records) and
   paper-clustered inference is unavailable. The same renderings are identical on docs-A, where 1 chunk in
   1,113 carries a glossary, so docs-A says nothing about this.
4. **Reranker input length is not a cause on either set.** No query–passage pair exceeded the declared
   `MAX_LENGTH` of 4,096 XLM-R tokens (max 692 on docs-A, 745 on P); C3 (truncation) is excluded.
5. **The confirmatory set and the exploratory set point in opposite directions.** docs-A and papers-OC
   differ in corpus (1,113 documentation chunks over 336 pages vs 14,456 paper chunks over 275 papers),
   question origin, gold rule and glossary reach. The prereg's own rule applies: a result that holds on one
   set and not another is *corpus-dependent*, stated as such. Nothing here licenses a claim about the
   reranker on papers-OC beyond the counts above.

## What is not reported

*Absent* means never measured; nothing here is a null.

| item | status | why |
|---|---|---|
| **Family B** (H1–H3 on the round-2 sets O, A, R2-P) | **absent** | round 2 produced no frozen set (its `RESULTS.md`); nothing to admit |
| **L replication** of family A (prereg §4, D2) | **absent** | not run (D-7); family A rests on T512o0 alone |
| E8: the four glossary conditions × reranker arms on papers-OC | absent | needs round 2's store swaps; round 2 is closed |
| A8: alternative cross-encoder (D7) | absent | none deployed |
| E7: judged answer-presence on discordant records (D8) | absent | no judge run |
| depth-200 arms on P (D10) | absent | the API route caps at 100; the in-process BM25 is not byte-identical to the store on papers-OC |
| found@20 / found@50 for the API's live arms (A4-50, A5-50, A6 on P) | absent | the live capture is a top-10 list |
| per-record agreement between the D3 re-score and the fresh A4-50 | absent | not computed; totals agree (622 / 735 vs 622 / 732 under different rules) |
| the magnitude of `TEXT_SYMBOL_GLOSSARY_BOOST` on the set-P server | declared, not read back | D-6; `/v1/config` does not expose it, the probe is structurally `not_testable`, the behavioural evidence shows only boost > 0 |
| multiplier and RRF k on the set-P server | declared (`--server-settings`) | not exposed by `/v1/config` (prereg §3) |

## Threats to the reading

- **The gates on P are real.** 7 clusters and 19 exhausted lists make every set-P hypothesis
  GATE-NOT-EVALUABLE; no sentence above about papers-OC is a test result. The McNemar values quoted beside
  set-P contrasts ignore clustering with 138 of 155 records from one paper.
- **Declared settings on P.** The seven fixed settings and the glossary boost on the set-P server are
  declared, not read back; the admin config was read and was stable across the passes, but it does not
  expose them. A misdeclared multiplier or RRF k would change what "the default" was.
- **Two gold rules.** docs-A uses the parts 2–4 normalisation (frozen), set P the papers-OC owner's rule
  (D-3). Cross-set comparisons of found@10 are not like for like; `--gold-rule all` moves docs-A by 85–135
  records and P by none.
- **The gold's shape on P.** Four records have 32 or 50 gold chunks across 18 or 27 documents (a short
  quote under the owner's 60 % clause): any list that touches those documents "finds" them. This is the
  owner's rule reproduced faithfully (D-3), and it inflates every arm alike.
- **docs-A's reranked arm is near ceiling** (707 of 727 reachable at depth 50), as the prereg anticipated
  (§7); the confirmed H3 and the reversed H1/H2 are large enough that the ceiling did not hide them, but the
  depth sweep's small differences (A4-50 vs A4-100 vs A4-200: 707 / 706 / 706) are within 1–3 records.
- **The identity gate on docs-A compares our harness with part 4's code on the bench copy** (D-4), not
  with the deployed index's lists; the deployed reference's ids were disjoint from the bench copy's. It
  proves the harness reproduces part 4's pipeline, which is what §8 asks.
- **Sidecar drift.** `sentence-transformers` 5.6.0 is outside the sidecar's own pin; the record names the
  version that ran. Not re-run under the pinned version.
- **Not from a versioned tools image.** Every output carries the *not in an image* warning; the pin is a
  clean git commit, which is citable under the current rule but is not ADR-0010's preferred form.

## Reproducing

At `f408b7a` in a clean tree, `PYTHONPATH=python`, with the private inputs (the part-4 private config and
question file, the set-P held records and pairs file, the bench-copy chunk dump, the sidecar's cache and
deps directories) supplied by their owner. Every `--base-url`, `--rerank-url` and `--embed-urls` is local;
no default resolves anywhere. Set P needs the API running against the bench copy with the run's key in
`RAGSTACK_API_KEY`.

```
# docs-A: the identity reference (D-4), then pools → rerank → fuse → census → power → re-score → stats
scripts/eval/deployed_docs_trial.py --private-config CFG7 --out-dir OUT/docsA-part4 part4-glossary \
  --glossary-file rules=RULES.jsonl --write-retrieval-condition rules --write-retrieval OUT/identity-T512o0.jsonl \
  --embed-urls … --rerank-url …
scripts/eval/reranker_test.py pools --backend inprocess --part4-arm T512o0 --private-config CFG8 --questions Q.jsonl \
  --set-field status --set-value valid --depths 10,20,50,100,200 --legs bm25,vector,hybrid --embed-urls … \
  --emb-dir OUT/emb --multiplier 2 --glossary-boost 1.0 --out OUT/docsA-pools-r2.json --gold-out OUT/docsA-gold-r2.json
scripts/eval/reranker_test.py rerank --part4-arm T512o0 --private-config CFG8 --questions Q.jsonl --set-field status \
  --set-value valid --pools OUT/docsA-pools-r2.json --rerank-url … --cache OUT/rerank-cache-r2.jsonl \
  --identity-reference OUT/identity-T512o0.jsonl --identity-arm T512o0 --inflight 4 --reranker-cache … --sidecar-deps … \
  --sidecar-max-length 4096 --sidecar-batch-size 32 --shuffle-seed 261029 --shuffle-unit string --out OUT/docsA-rerank-r2.json
scripts/eval/reranker_test.py fuse   --lists OUT/docsA-rerank-r2.json OUT/docsA-pools-r2.json --rrf-k 60 --out OUT/docsA-fuse-r2.json
scripts/eval/reranker_test.py census --part4-arm T512o0 --private-config CFG8 --questions Q.jsonl --set-field status --set-value valid \
  --lists OUT/docsA-rerank-r2.json OUT/docsA-pools-r2.json --gold OUT/docsA-gold-r2.json --max-length 4096 --out OUT/docsA-census-r2.json
scripts/eval/reranker_test.py power-table --gold OUT/docsA-gold-r2.json --delta 0.05 --alpha 0.05 --n-sims 2000 --n-flips 2000 \
  --seed 261029 --out OUT/docsA-power-r2.json
scripts/eval/reranker_test.py part4-rescore --part4-result STEP7.json --part4-arm T512o0 --map A4-50=k10 --questions Q.jsonl \
  --set-field status --set-value valid --set-label docs-A/T512o0 --allow-partial --out OUT/docsA-part4-rescore-r2.json
scripts/eval/reranker_test.py stats --lists OUT/docsA-pools-r2.json OUT/docsA-rerank-r2.json OUT/docsA-fuse-r2.json \
  --gold OUT/docsA-gold-r2.json --set-label docs-A/T512o0 --prefer live --gold-rule any --delta 0.05 --alpha 0.05 \
  --n-perm 10000 --n-boot 10000 --ci-level 0.90 --seed 261029 --repeat OUT/docsA-pools-r2rep.json OUT/docsA-rerank-r2rep.json \
  --out OUT/docsA-stats-r2.json            # and once more with --gold-rule all

# set P: pools (API route) → rerank → fuse → census → stats
scripts/eval/reranker_test.py pools --backend api --base-url … --api-key-env RAGSTACK_API_KEY --auth-header authorization \
  --collection BENCH --live hybrid@50,bm25@50,vector@50 --depths 10,20,50,100 --legs bm25,vector,hybrid \
  --server-settings SERVER.json --boost-declared 1.0 --allow-short --chunks DUMP.jsonl --questions-format partner-held \
  --pairs PAIRS.jsonl --questions HELD.jsonl --inflight 4 --out OUT/P-pools-r2.json --gold-out OUT/P-gold-r2.json
scripts/eval/reranker_test.py rerank --chunks DUMP.jsonl --questions-format partner-held --pairs PAIRS.jsonl --questions HELD.jsonl \
  --pools OUT/P-pools-r2.json --rerank-url … --cache OUT/P-rerank-cache.jsonl --inflight 4 --reranker-cache … --sidecar-deps … \
  --sidecar-max-length 4096 --sidecar-batch-size 32 --shuffle-seed 261029 --shuffle-unit string --out OUT/P-rerank-r2.json
scripts/eval/reranker_test.py fuse / census / stats as above with the P files, --set-label papers-OC, --depths 10,20,50,100,
  --repeat OUT/P-pools-r2rep.json

# the public artifacts
python3 -I docs/plans/results/reranker-test-2026-11/artifacts/make_rr_artifacts.py --out-dir OUT --dump DUMP.jsonl --dest docs/plans/results/reranker-test-2026-11/artifacts
```

The `census` subcommand needs the bge-reranker-v2-m3 tokenizer and the SFR tokenizer in an offline HF
cache (`HF_HUB_CACHE`); the run pointed it at a scratch cache linking the sidecar's snapshot and the
embedding model's tokenizer read-only.
