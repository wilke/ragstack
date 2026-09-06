# RESULTS — the breadth × k interaction

Pre-registered in [`PREREG-breadth-k.md`](PREREG-breadth-k.md), frozen **before any new number
was computed** — including the CPU-only Leg B k-extension, which needs no GPU but would still
have tainted the predictions. `sha256(PREREG) = 3264cae8354fdc94…`, recorded in
`provenance-breadth-k.json` before the first embedding call.

**Cost: 6.75 GPU-minutes** against a 45-minute ceiling — 15 % of budget. 68.5 M tokens, 10 716
requests, **0 retries**. The Leg B half cost **zero GPU** (11 s of CPU) because it re-uses the
`rescore` embeddings unchanged. Scoring and analysis are CPU-only (~40 s).

---

## 0. Headline

> **The hypothesis is not supported, and the reason is more useful than the hypothesis.**
>
> With breadth manipulated cleanly — `m` = 1 → 16 gold (document, passage) pairs per topic,
> everything else (corpus, corpus size, query, model, chunker, gold provenance) held exactly
> fixed — **the marginal value of `k` does not increase with breadth.** The pre-registered primary
> interaction `I1` is **−0.014 to −0.032** across the four chunk sizes: two **powered NULLs** at
> the 0.05 bar, two UNRESOLVED. It is negative, not positive, on every arm.
>
> **The whole of the observed `m`-effect is identified, and it is competition between gold
> documents for the same top-k slots.** Because the query and the embedding are fixed, the ranking
> over a given corpus does not depend on `m` at all. `m` can reach the k-curve through exactly two
> channels: the `min(1, k/m)` ceiling — absorbed by the pre-registered random-ranking null — and
> inter-gold competition. **There is no third channel**, so a residual breadth effect here is
> *structurally* zero rather than empirically null. A post-hoc control measures the competition
> channel directly: **−0.03 to −0.06 at k=1, growing to −0.12 to −0.13 at k=20**.
>
> **`I2`, the one pre-registered contrast that resolved, is that same competition seen on the
> gap** (−0.081 and −0.097 observed; competition term −0.084 and −0.109). It is a real and
> practitioner-relevant fact — *for broad topics, relevant documents surface faster than the
> answering passages inside them, increasingly so with k* — but it is **not** evidence that
> breadth changes what `k` buys you. *No interaction beyond competition survives on any
> pre-registered contrast.*
>
> **What actually differs between the two legs is difficulty, not breadth — and it is enormous.**
> The lumped leg term is **−0.43 to −0.48 on PH@1** (Leg A 0.05–0.09 vs Leg B 0.48–0.56), roughly
> **15× any candidate breadth effect**. A naive Leg-A-vs-Leg-B comparison would have attributed
> all of it to breadth.
>
> **And the hypothesis's premise fails for a reason it did not anticipate.** "Narrow queries
> saturate — one gold passage, once found extra k adds nothing" is a property of **easy** queries,
> not narrow ones. Leg B's narrow queries saturate (`PH@k` 0.49 → 0.997 by k = 20, `Gap@k`
> collapses 0.492 → 0.000). Leg A's **equally narrow** `m=1` queries do not saturate at all —
> `k* = 20` at **every** rung of the ladder, and `PR@20` is still only 0.32. Leg B's queries were
> *written from* the gold passage; Leg A's gold is merely the best unit inside a topically
> relevant document.
>
> **Fixed-budget matching reverses the fixed-k ordering at every rung of the ladder** (best raw
> `PR@1` is `tok2048`; best budget-matched `PR_B@4096` is `tok256`, by 2.2×) — replicating
> `RESULTS-rescore` §4.2 on a different corpus, different query type, and across breadth.

---

## 1. Provenance, and what code actually ran

| item | value |
|---|---|
| repo commit | **`d225cea06b11278e0c1ff77c514239233a56aa35`** (asserted; harness aborts otherwise) |
| the brief's commit | The brief names **`55a0fc2`**. It is on `origin/main`, **one commit ahead of the local HEAD and not an ancestor of it**. This run executes at `d225cea` because every reused artifact pins it (`emb_*.npy`, `spans_*.json`, `queries.npy`, the §7a oracle output). |
| **the "unaffected by the fill change" claim, proven not assumed** | `FixedTokenWindowChunker` is **byte-identical** at the two commits — `sha256(class source) = 15309b8e0e3a53c0…` at both — and neither version mentions `budget_mode`. The `55a0fc2` diff touches only the sentence/word packers, their tests, and a doc. This run is `token_window` only. |
| `ragstack` resolved to | **`/home/wilke/Development/ragstack/python/ragstack/__init__.py`** |
| editable-finder defence | `pin_repo()` strips the meta-path finder pointing at `/rag/repos/ragstack`. Surviving `sys.meta_path`: `_distutils_hack.DistutilsMetaFinder` + the four builtin importers. **The `/rag/repos` checkout did not win.** |
| **served** model (live `GET /v1/models`, all six endpoints) | **`Salesforce/SFR-Embedding-Mistral`**, `max_model_len` 4096. Identical on `:9001`–`:9006`; the harness refuses to run if they differ. |
| reranker | **not used.** `:50052` — the crossencoder the §7a oracle used in an earlier round — is **outside this run's permitted endpoint range and was not contacted.** |
| Leg A corpus | 2 095 documents, `sha256` = `7439845ef6dbb315fa82555f…` (computed **before** embedding) |
| Leg B corpus | 400 documents, `sha256` = `d83c7fe1399b92ab394bd914…` — asserted equal to the `rescore` run's |
| seeds | `20260905` (distractors, gold subsampling, replicate chains); Leg B's and the §7a sample's (`20260904`) inherited unchanged |
| versions | Python 3.12.13, numpy 2.5.0, transformers 5.12.1, tokenizers 0.22.2, httpx 0.28.1 |
| **gold-recovery gate** | **2 161 / 2 161** pairs: re-derived `n_units`, the full `unit_start_tok` vector, `doc_chars` and `argmax_cls` all match the stored oracle record. **Zero failures.** |
| **coordinate gate** | **2 095 / 2 095** documents: `units_for_article`'s text is **byte-identical** to `stage1_common.doc_text`. Without this the gold spans and the chunk spans would be in different coordinate systems and every passage number would be meaningless. **Zero failures.** |
| **chunk-offset gate** | every chunk asserted `doc_text[start:end] == chunk.content`. Zero failures. |
| stores | **never contacted.** No Qdrant/Elasticsearch client is constructed in the harness or its imports. `:6333` / `:9200` / `:24041` / `:24043` were not touched at all. |
| GPUs | **6 and 7 at 0 MiB before and after.** No endpoint started. ≤ 2 in flight per endpoint on `:9001`–`:9006` only. |

### 1.1 Reproduction gate — Leg B comes back exactly

The k-extension is a separate scoring path over the same embeddings. It must recover the task
brief's Leg B table (N = 1, topical, n = 396):

| config | Gap@1 | Gap@5 | Gap@10 | PH@1 | PH@5 | PH@10 |
|---|---:|---:|---:|---:|---:|---:|
| tok256 | +0.505 | +0.104 | +0.030 | 0.495 | 0.896 | 0.970 |
| tok512 | +0.467 | +0.093 | +0.005 | 0.533 | 0.907 | 0.995 |
| tok1024 | +0.505 | +0.033 | +0.005 | 0.495 | 0.967 | 0.995 |
| tok2048 | +0.424 | +0.005 | +0.000 | 0.576 | 0.995 | 1.000 |

**PASS on all 28 gated rows** (24 above + the four n = 260 `Gap@1` values from
`RESULTS-rescore` §3.1), max |diff| **0.000465**, tolerance 0.0005.

### 1.2 The licensing identity holds exactly

PREREG §4 licenses the whole design on the claim that at `m = 1` the recall forms reduce
*exactly* to Leg B's hit forms. Asserted numerically and it holds to the last digit — e.g. Leg A
`tok512`, `m = 1`: `PH@k` and `PR@k` are the identical vector
`{1: 0.05233, 2: 0.09593, 3: 0.12500, 5: 0.16570, 10: 0.23692, 20: 0.31977}`, and `DH@k ≡ DR@k`.

---

## 2. How the confound was handled — the main methodological risk

Leg A and Leg B differ in corpus, query style, relevants-per-topic **and** gold provenance
(oracle-derived vs recorded-by-construction). PREREG §1.1 therefore refused to make breadth a
between-leg variable and split the problem into three named terms.

### 2.1 Term A — breadth, IDENTIFIED

Within Leg A only. For each of **86 topics** (those with ≥ 16 sampled golds; the four excluded are
`2014_9`, `2015_25`, `2016_22`, `2016_27`), corpus size is **fixed at N = 100** and `m` gold
(document, passage) pairs are drawn by **seeded nested subsampling of that topic's own qrels**,
`m ∈ {1, 2, 4, 8, 16}`, **R = 8** replicate chains, replicates averaged **within topic** before any
contrast. Distractors come from a **fixed per-topic ordered list** (nearest neighbours of the
centroid of *all* the topic's sampled golds — so the list does not move as `m` changes) drawn only
from documents TREC judged **non-relevant at any grade** in the full 2014/2015/2016 qrels.

Nothing varies across the ladder except `m`. **This is the only place a causal reading of breadth
is claimed.**

### 2.2 Term B — the leg difference, LUMPED and measured

Leg A `m=1, N=100` vs Leg B `m=1, N=100`, corpus-size matched. **This is not a breadth effect and
is never read as one.** It is a bound on how wrong the naive comparison would have been:

| metric | tok256 | tok512 | tok1024 | tok2048 |
|---|---:|---:|---:|---:|
| `PH@1` Leg A | 0.0581 | 0.0523 | 0.0538 | 0.0872 |
| `PH@1` Leg B | 0.4899 | 0.5151 | 0.4773 | 0.5631 |
| **difference** | **−0.432** | **−0.463** | **−0.424** | **−0.476** |
| `PH@10` difference | −0.726 | −0.740 | −0.723 | −0.704 |
| `DH@1` difference | −0.881 | −0.887 | −0.868 | −0.839 |

**Term B is −0.42 to −0.48 on `PH@1` and −0.70 to −0.78 on `PH@10`** — roughly **15×** the largest
candidate breadth effect measured anywhere in this run. Every CI excludes 0 by a wide margin. The
naive cross-leg reading was not merely confounded; it would have been almost entirely confound.

### 2.3 Term C — gold provenance, NOT IDENTIFIED

Leg A's gold is what `bge-reranker-v2-m3` ranked highest; Leg B's is the section the query was
written from. **This run cannot separate C from the rest of B.** A measured decomposition would
require running the §7a cross-encoder over Leg B's documents, and `:50052` is outside this run's
permitted endpoint range. The only handle is the pre-registered **S-margin** sensitivity (§9.1),
and it is a sensitivity, not an identification. **Anything visible only in a cross-leg comparison
is reported here as unattributable.**

I predicted (P8) that Term C would *flatter* Leg A — two correlated neural rankers agreeing. **P8
is wrong, and wrong in the opposite direction:** Leg A's passage numbers are 8–10× *worse*. The
cross-encoder's argmax inside a merely topically-relevant CDS document is a much weaker target
than a section a query was authored from.

---

## 3. The two Gap@k curves

`Gap@k = DH@k − PH@k` (Leg B, and Leg A at `m=1` where the forms coincide); `GapR@k = DR@k − PR@k`
at `m > 1`. `tok512`, topical, N = 100.

| k | 1 | 2 | 3 | 5 | 10 | 20 |
|---|---:|---:|---:|---:|---:|---:|
| **Leg B** (narrow, recorded gold), n = 396 | **+0.447** | +0.258 | +0.164 | +0.098 | +0.018 | **−0.003** |
| **Leg A `m=1`** (narrow, oracle gold), n = 86 | +0.023 | +0.068 | +0.077 | +0.109 | +0.157 | **+0.227** |
| **Leg A `m=16`** (broad, oracle gold), n = 86 | +0.016 | +0.033 | +0.047 | +0.079 | +0.152 | **+0.256** |

**The two legs' gaps move in opposite directions in k.** Leg B's collapses to zero — with ten
chunks in hand you nearly always touch the gold section, so the gap is a top-1 precision
phenomenon (this reproduces `RESULTS-rescore` §3.2). Leg A's **grows** with k: you recover
relevant *documents* far faster than the answering *passages* inside them. At `m=16, k=20`:
`DR@20 = 0.463` but `PR@20 = 0.207`.

**Half of Leg A's raw gap growth is arithmetic** and the lift form says so. `E_rand[DR@20] = 0.20`
against `E_rand[PR@20] = 0.065`, so the two nulls diverge with k on their own. In lift form
(`LGap = LD − LP`) the Leg A `m=1` curve is `+0.017 / +0.055 / +0.058 / +0.076 / +0.091 / +0.092`
— it still grows, but it **plateaus by k = 10** rather than climbing to +0.227. The qualitative
contrast with Leg B survives the correction; the magnitude does not. **Read the lift row, not the
raw row.**

---

## 4. I1 — the primary interaction. Not supported.

`I1 = [L_P@20 − L_P@5]_{m=16} − [L_P@20 − L_P@5]_{m=1}`, paired per topic, n = 86, topical.
`L_P@k = PR@k − E_rand[PR@k]` — the lift over the closed-form random-ranking null, pre-registered
as the inferential form precisely because raw `PR@k` carries a `min(1, k/m)` ceiling that would
manufacture a positive interaction (PREREG §5.1).

| config | slope at `m=16` | slope at `m=1` | **I1** | 95 % CI | δ80 | verdict |
|---|---:|---:|---:|---|---:|---|
| tok256 | +0.0897 | +0.1035 | **−0.0138** | [−0.050, +0.020] | 0.0499 | **NULL (powered)** |
| tok512 | +0.0856 | +0.1064 | **−0.0208** | [−0.053, +0.010] | 0.0451 | **NULL (powered)** |
| tok1024 | +0.0920 | +0.1243 | **−0.0323** | [−0.073, +0.006] | 0.0563 | UNRESOLVED |
| tok2048 | +0.1079 | +0.1308 | **−0.0228** | [−0.065, +0.018] | 0.0595 | UNRESOLVED |

**Two powered nulls, two unresolved.** On the two arms where the run has the power to say so,
there is no breadth × k interaction larger than the 0.05 bar. On the other two the run cannot
say — δ80 is 0.056 and 0.060 against a 0.05 bar. **These are reported as unresolved, not as
nulls.** And the sign is negative on all four arms: whatever `m` is doing, it is not making extra
`k` more valuable.

### 4.1 What the `m`-effect actually is — and why only two channels exist

**The design admits exactly two channels by which `m` can reach the k-curve.** The query text, the
embedding model and the chunking are fixed, so **the ranking over a given corpus does not depend
on `m` at all**; `m` only changes which documents are in the corpus and which passages count as
gold. Therefore:

1. **The `min(1, k/m)` ceiling** — `k` chunks can hit at most `k` distinct gold passages. This is
   what the pre-registered random-ranking null of PREREG §4 absorbs, and it is why `L_P` and not
   raw `PR` is the inferential form.
2. **Inter-gold competition** — the `m` gold documents compete with each other for the same top-k
   slots. The null treats each gold passage independently, so it does *not* absorb this.

**There is no third channel.** A "breadth effect" over and above these two is not merely
empirically absent here; it is structurally impossible in this design. That is a stronger
statement than a null, and it is the honest reading of `I1`.

### 4.2 The competition channel, measured (POST-HOC)

Holding the gold document fixed: each nested chain's **first** gold document `d0` is present at
every rung, so score only `d0`'s passage at `m=1` and at `m=16`. Query, gold document, gold
passage and corpus size are identical; only 15 distractors have been swapped for 15 other relevant
documents.

| focal_PR@k(m=16) − focal_PR@k(m=1) | k=1 | k=5 | k=10 | k=20 |
|---|---:|---:|---:|---:|
| tok256 | −0.047 | −0.083 | −0.092 | −0.121 |
| tok512 | −0.036 | −0.092 | −0.106 | −0.129 |
| tok1024 | −0.033 | −0.102 | −0.135 | −0.128 |
| tok2048 | −0.061 | −0.103 | −0.140 | −0.128 |

**Competition is large and grows with k** — a given relevant passage is 3–6 points less likely to
be in the top-1, and 12–13 points less likely to be in the top-20, once fifteen of its topic-mates
are in the corpus with it. **This is a kept measurement and the mechanism behind `I1`'s sign.**

> ### The correction is an identity, not evidence — disclosed
>
> It is tempting to subtract this term from `I1` and read the remainder as "the breadth effect
> with competition removed". **That number is ≈0 by construction and must not be read as a
> finding.** Each replicate chain is a fresh uniform permutation, so `d0` is a uniformly random
> member of the `m=16` gold set scored in the *same* corpus that `PR@k(m=16)` averages over. By
> exchangeability the focal estimate is an unbiased 1-of-16 sample of `PR@k(m=16)` itself, and at
> `m=1` it is *identically* `PR@k(m=1)`. So the "competition term" is not an isolated component —
> it **is** the entire `m`-effect on `PR`, and `I1 − term` reduces algebraically to the difference
> of the two null slopes, `−([E_P@20−E_P@5]_{m16} − [...]_{m1})`.
>
> **Verified on this run's own numbers**, which is the only thing the subtraction can be used for
> — a consistency check on the estimators:
>
> | | tok256 | tok512 | tok1024 | tok2048 |
> |---|---:|---:|---:|---:|
> | `−(EP slope difference)` — the algebraic expectation | −0.0013 | −0.0016 | −0.0016 | −0.0009 |
> | `I1 − term` as computed | +0.0240 | +0.0170 | −0.0061 | +0.0019 |
> | focal term vs actual `PR@20(m16) − PR@20(m1)` | −0.121 vs −0.116 | −0.129 vs −0.113 | −0.128 vs −0.134 | −0.128 vs −0.125 |
>
> The focal term tracks the *whole* `PR(m16) − PR(m1)` difference to within ±0.02 at every k — the
> signature of the identity — and the "corrected" values scatter around the ≈−0.001 expectation
> with 1-of-16 estimator noise. `posthoc-crowding.json` reports these rows with the house
> `read()` verdicts attached; **those verdicts are estimator diagnostics, not readings**, and the
> one that printed UNRESOLVED (`I2`, tok1024) carries no more meaning than the ones that printed
> NULL.

---

## 5. I2 — the one contrast that resolved, and what it is

`I2 = [GapR@5 − GapR@20]_{m=16} − [...]_{m=1}`. As pre-registered it **resolved on two arms**:

| config | I2 (raw GapR) | 95 % CI | δ80 | pre-registered verdict |
|---|---:|---|---:|---|
| tok256 | −0.0812 | [−0.138, −0.028] | 0.0810 | **RESOLVED** |
| tok512 | −0.0593 | [−0.114, −0.007] | 0.0765 | UNRESOLVED |
| tok1024 | −0.0968 | [−0.159, −0.039] | 0.0874 | **RESOLVED** |
| tok2048 | −0.0667 | [−0.128, −0.009] | 0.0876 | UNRESOLVED |

It says: **the document/passage gap widens faster with k for broad topics.** That is a real,
measured, practitioner-relevant fact and it is **kept**. What §4.1 settles is its *mechanism*, not
its existence. The same identity applies — the focal term is the whole `m`-effect on the gap, not
an isolated component:

| config | observed (raw `GapR`) | observed (lift `LGap`) | focal `m`-effect on the gap | `I2 −` term (≈0 by construction) |
|---|---:|---:|---:|---:|
| tok256 | −0.0812 | −0.0825 | −0.0843 | +0.0031 |
| tok512 | −0.0593 | −0.0609 | −0.0669 | +0.0075 |
| tok1024 | −0.0968 | −0.0983 | −0.1090 | +0.0123 |
| tok2048 | −0.0667 | −0.0676 | −0.0698 | +0.0031 |

**So `I2` is inter-gold competition, seen on the gap.** With sixteen relevant documents in a
100-document corpus, they crowd each other out of the top-k; the max-rollup document ranking
absorbs that crowding much better than the chunk ranking does, because a document only needs *one*
good chunk to rank while a passage needs *its own* chunk to rank. Hence `DR@k` pulls away from
`PR@k`, and the more relevant documents there are, the faster it pulls away.

**How to read this.** It is **not** evidence that breadth changes what extra `k` buys you — that
is `I1`, and `I1` is negative and null-to-unresolved. It **is** a description of what happens
downstream in a real corpus, where a broad topic genuinely does have many relevant documents
competing for the same slots. Competition is not an artifact to be subtracted away when the
question is "what should I set `top_k` to"; it is part of what breadth *is* at retrieval time. So
§10 reports it as a practitioner fact with its mechanism named, and §4 reports that no interaction
beyond it exists.

---

## 6. The saturation reframe — the premise fails, but not because of breadth

The hypothesis assumes narrow queries saturate. **`k*` — the smallest k reaching 90 % of the
k = 20 value — is 20 at every rung of the Leg A ladder, on both `PR` and `L_P`.** Nothing
saturates by k = 20 on Leg A, at any breadth. **P3 and P4 both fail**, and not marginally: the
`m=1` slope `L_P@20 − L_P@5` is **+0.104 to +0.131**, twice the 0.05 P3 predicted it would fall below.

Meanwhile Leg B's equally narrow (m = 1) queries saturate hard at the same corpus size:

| Leg B, N=100, tok512, n=396 | k=1 | k=2 | k=3 | k=5 | k=10 | k=20 |
|---|---:|---:|---:|---:|---:|---:|
| `PH@k` | 0.515 | 0.720 | 0.818 | 0.891 | 0.977 | **1.000** |
| `Gap@k` | +0.447 | +0.258 | +0.164 | +0.098 | +0.018 | **−0.003** |

vs Leg A `m=1`, same k, `PR@k` = 0.052 / 0.096 / 0.125 / 0.166 / 0.237 / **0.320**.

> **"Narrow queries saturate" is a statement about easy queries, not narrow ones.** Both legs have
> exactly one gold passage per query in these rows. Leg B's queries were *written from* the gold
> section, so the retriever is being asked to invert a generator. Leg A's gold is the
> cross-encoder's best unit inside a document TREC merely judged topically relevant to a clinical
> case narrative. Difficulty, not breadth, is what moves the shape of the k-curve here — and it
> moves it enormously.

---

## 7. Fixed-budget reading — mandatory, and it changes the chunk-size answer

At fixed k, ten 2048-token chunks is 8× the context of ten 256-token chunks, so any cross-chunk-size
k comparison is unfair. Realised budgets at `B = 4096` are matched to within 13 % (3 518–4 054
tokens; 16.0 / 8.0 / 4.1 / 2.3 chunks admitted), so this is the size-fair reading.

| Leg A, topical | tok256 | tok512 | tok1024 | tok2048 |
|---|---:|---:|---:|---:|
| raw `PR@1`, m=1 | 0.058 | 0.052 | 0.054 | **0.087** ← best |
| **budget-matched `PR_B@4096`, m=1** | **0.291** ← best | 0.208 | 0.156 | 0.132 |
| raw `PR@1`, m=16 | 0.018 | 0.017 | 0.023 | **0.023** ← best |
| **budget-matched `PR_B@4096`, m=16** | **0.181** ← best | 0.107 | 0.067 | 0.047 |

**P7 CONFIRMED at every rung of the ladder.** The fixed-k ordering favours the largest chunk; the
budget-matched ordering reverses it completely and favours the smallest, by **2.2× at m=1 and
3.8× at m=16**. This replicates `RESULTS-rescore` §4.2 on a different corpus, a different query
type, and now across breadth.

**Which reading supports which conclusion, as pre-committed:**

* **Cross-chunk-size conclusions rest on the fixed-budget family.** "Chunk small" is a
  budget-matched result and holds at every `m`.
* **The interaction conclusions (I1–I4) rest on fixed-k *within* one arm**, which is legitimate
  because the arm is held constant — and they are **corroborated by the budget form**: `I4`, the
  budget-ladder analogue of `I1`, is +0.008 / −0.009 / −0.041 / −0.031, one powered null and three
  unresolved. **The fixed-k and fixed-budget forms support the same conclusion**: no interaction.

---

## 8. Predictions, scored

| # | prediction | outcome |
|---|---|---|
| **P1** | `I1 > 0` and RESOLVED | **FAILED.** −0.014 to −0.032 — negative on every arm; 2 powered nulls, 2 unresolved. §4.1 shows a residual breadth effect is structurally impossible here, not merely unmeasured. |
| **P2** | ladder slope monotone increasing in `m` | **FAILED.** Pearson r vs log₂(m) is *negative* on all four arms (−0.29 to −0.63) — and that sign is inter-gold competition (§4.2), not breadth. |
| **P3** | at `m=1`, `L_P@20 − L_P@5 ≤ 0.05` | **FAILED**, by 2×: +0.104 to +0.131. Narrow Leg A queries do not saturate. |
| **P4** | `k*` rises with breadth (1–3 at m=1, ≥10 at m=16) | **FAILED.** `k* = 20` at every `m`. Nothing saturates by k = 20 on Leg A. |
| **P5** | Term B is large (\|diff\| on `Gap@1` > 0.05 on ≥ 3 of 4) | **CONFIRMED**, overwhelmingly: −0.36 to −0.45 on `Gap@1`, −0.42 to −0.48 on `PH@1`. |
| **P6** | `I2` does not resolve | **FAILED.** 2 of 4 resolved. The resolved effect is real and is kept (§5); what §4 establishes is that it is inter-gold competition rather than a change in what extra `k` buys. Recorded as a failed prediction. |
| **P7** | budget matching reverses the fixed-k size ordering at every `m` | **CONFIRMED** at all five rungs. |
| **P8** | Leg A `m=1` `PH@1` > Leg B `m=1` `PH@1` (oracle gold flatters Leg A) | **FAILED**, and in the opposite direction: Leg A is 8–10× *worse*. |

**Six of eight predictions failed.** They are reported, not dropped. P1–P4 failed because the
pre-registration inherited the task's premise that Leg B's saturation is a property of narrowness;
it is a property of easiness. P8 named a bias direction that turned out backwards.

---

## 9. Sensitivities

### 9.1 S-margin (the only handle on Term C)

Splitting Leg A's topics at the median oracle `margin_over_head` (+0.163): `I1` on the
high-confidence half is −0.004 to −0.030 (all UNRESOLVED), on the low half −0.016 to −0.061. The
interaction is absent in both halves; the high-margin half is, if anything, *closer to zero*. This
is consistent with the interaction being genuinely absent rather than being masked by gold noise —
but it is a sensitivity, **not** an identification of Term C.

### 9.2 S-policy — and a caveat that matters

Under `random` distractors `I1` is −0.063 / −0.032 / +0.023 / +0.093, versus −0.014 / −0.021 /
−0.032 / −0.023 under `topical`. **The estimate is not stable across distractor policy**, swinging
by up to 0.12 on `tok2048`. This reinforces the reading: at n = 86 topics the run can exclude an
interaction of ≈0.05 but cannot pin its sign or a smaller magnitude. Stated as a limitation, not
smoothed over.

### 9.3 Degeneracies, as pre-declared and as observed

PREREG §5.2.2 pre-enumerated that at Leg B `N = 1` the top-20 *is* the whole document for the
larger chunks. Measured fraction of queries with `C ≤ k`:

| Leg B, N=1 | k=3 | k=5 | k=10 | k=20 |
|---|---:|---:|---:|---:|
| tok256 | 0.000 | 0.000 | 0.015 | 0.088 |
| tok512 | 0.005 | 0.015 | 0.088 | **0.672** |
| tok1024 | 0.020 | 0.088 | **0.672** | **0.970** |
| tok2048 | **0.167** | **0.672** | **0.970** | **0.995** |

**Do not quote Leg B `N=1` `PH@20` or `Gap@20` for tok512/1024/2048** — they are 1.000 and 0.000 by
arithmetic for 67–99 % of queries. **At N = 100 the degenerate fraction is 0.000 at every k for
every arm**, which is why all curve-shape analysis above lives at N = 100. `PH@20 = 1.000` at
tok512/N=1 is exactly the proportion-at-1.0 hazard the brief flagged: it is read on its Wilson
interval and written **unresolvable by construction**; no δ80 is quoted from its (zero) Wald SE.

`whole4096` behaves as pre-declared (PREREG §5.2.4) — a **bound, not a comparison**. On Leg A its
`PR@1 = 0.129` at m=1 *beats* every chunked arm, but its `PR@k` and `DR@k` are nearly identical
(0.129 vs 0.138 at k=1), because with one unit per document there is no passage selection at all:
it is measuring document retrieval wearing a passage metric's label.

### 9.4 The power ceiling, as pre-registered

PREREG §6.2 declared **n = 86 topics** the binding constraint before any reading: δ80 =
`0.3022·σ_d`, resolvable at bar `b` only if `σ_d ≤ 3.310·b`, and a paired *binary* Leg A contrast
resolves at the 0.05 bar only if < 2.7 % of topics disagree. Borne out: the interaction δ80s land
at 0.045–0.060 against a 0.05 bar — **right at the edge**, which is why two arms give powered nulls
and two are unresolved. The run can exclude an interaction of ≈0.05; it cannot resolve one of 0.02.

---

## 10. What a practitioner should set `top_k` to

**The data cannot yet give a breadth-conditional `top_k` rule.** Within a single leg, with breadth
manipulated cleanly, breadth does not detectably change the marginal value of `k`. That is a
powered null on the two arms with the power to say so, and unresolved on the other two.

What the data *does* support:

1. **Set `k` by task difficulty, not by query breadth.** Difficulty moved the k-curve by 0.42–0.48
   on `PH@1`; breadth moved it by an amount indistinguishable from zero at a 0.05 bar. If you have
   to condition `top_k` on something, condition it on how far your queries are from the wording of
   your documents.
2. **On easy / paraphrase-shaped tasks (Leg B-like), `k = 10` is enough and `k = 20` is free.**
   `PH@10 ≈ 0.97–0.99`, `Gap@10 ≈ 0.02`, and by `k=20` the gap is 0.000. Going beyond 10 buys
   almost nothing.
3. **On hard, topical, real-qrel tasks (Leg A-like), no `k` in the tested range is enough.** At
   `k = 20` you have 21–32 % of the gold passages and the gap is still widening. Raise `k` or the
   token budget as far as the consumer tolerates — and expect **reranking**, not `k`, to be the
   lever. `RESULTS-rescore` §10.6 already identified reranking as the untested intervention aimed
   at exactly this axis; this run is dense-only by pre-registration and did not test it.
4. **Match budgets, not `k`, when comparing chunk sizes** — and then chunk small. `tok256` wins
   the budget-matched reading at every breadth by 2.2–3.8×, while the raw `@1` reading points the
   opposite way.
5. **If your consumer acts on the first hit, the document-level metric is lying to you** — by
   0.45 on Leg B at `k=1`, and on Leg A by a gap that *grows* with `k` instead of closing.
6. **On broad topics, expect your relevant documents to crowd each other** (§5). Sixteen relevant
   documents in a 100-document corpus make any *one* of their passages 12–13 points less likely to
   reach the top-20 than if it were alone. The document ranking hides this — a document needs only
   one good chunk to rank, while a passage needs *its own* chunk to rank — so `DR@k` pulls away
   from `PR@k` the more relevant material there is. If you report document recall on a broad-topic
   corpus, you are over-stating passage recall by a margin that *widens* as you raise `k`.

---

## 11. What remains unattributable

* **The ladder manipulates *qrel* breadth, not *query* breadth — and this is the sharpest
  limitation of the whole run.** The design deliberately holds the query text fixed at every rung;
  that is exactly what makes Term A clean. But the hypothesis describes a semantically **broader
  question** — "what are the treatment options for X" versus "what dose of Y was used" — and such
  a question would differ in its *wording*, its embedding, and therefore its whole ranking. This
  run changes only how many documents are *labelled* relevant to the same fixed question.
  **Query-breadth is structurally invisible here and remains untested.** A design that varied it
  would have to vary the query text, which reintroduces every confound §2 was built to exclude;
  that is a harder experiment, not a variant of this one.
* **Term C — oracle-derived vs recorded gold — is not identified.** It sits inside Term B and this
  run cannot separate it. `:50052` was outside the permitted endpoint range, so the cross-encoder
  could not be run over Leg B to decompose it. The S-margin split (§9.1) is a sensitivity only.
  **Any part of the −0.43 leg difference could be gold provenance rather than task difficulty.**
* **Breadth above `m = 16`.** The §7a sample capped relevants at 25 per topic, so the ladder tops
  out at 16 against a true CDS breadth of ~109. **Nothing here speaks to `m` in the hundreds.**
* **The competition measurement is post-hoc**, and the subtraction built on it is an algebraic
  identity rather than a correction (§4.2). The pre-registered readings are reported unmodified.

> ### Disclosure — a reading-rule fix made after the first reporter run
>
> The first run of `bk_report.py` evaluated the resolution rule in the wrong precedence: it tested
> `δ80 > |mean|` **before** testing `|mean| < bar`, which relabels every genuine powered null as
> UNRESOLVED, because a null has a small `|mean|` by definition. PREREG §6.3 states the rule as
> *RESOLVED iff `|mean| ≥ bar` ∧ CI excludes 0 ∧ `δ80 ≤ |mean|`; otherwise a powered NULL if
> `|mean| < bar` ∧ `δ80 ≤ bar`; otherwise UNRESOLVED.* The code was corrected to that literal
> order and the reporter re-run.
>
> **What moved:** two `I1` verdicts (tok256, tok512) went UNRESOLVED → **NULL (powered)**. No
> point estimate, CI or δ80 changed — only the label. Nothing that was RESOLVED became unresolved
> or vice versa.
>
> This is disclosed rather than silently edited, following `RESULTS-rescore` §4.4, which disclosed
> the same class of reading-rule error in the opposite direction. The diff is visible in
> `bk_report.py`; the first run's console output is not retained, so the two changed labels are
> stated here explicitly.
* **The `I1` sign is unstable across distractor policy** (§9.2) and should not be read.
* **Nothing about reranking, answer quality, non-biomedical corpora, or corpus sizes other than
  N = 100 for Leg A.**

---

## 12. Artifacts

All under `phase0/breadth-k/` (durable, at `~/Development/worktrees/phase0-rescue/`).

| file | what |
|---|---|
| `PREREG-breadth-k.md` | the pre-registration, frozen before any new number |
| `bk_common.py` | gold recovery, qrels, the closed-form null, provenance + the chunker-identity gate |
| `bk_legb_k.py` | Leg B k-extension — **zero GPU**, ranked lists, the 28-row reproduction gate |
| `bk_lega_embed.py` | Leg A pass 1 — gold-recovery + coordinate + chunk-offset gates, chunk, embed |
| `bk_score.py` | the breadth ladder — 400 cells, nested subsampling, 8 replicate chains |
| `bk_report.py` | δ80, topic-clustered bootstrap, Wilson, Holm, the pre-registered readings |
| `bk_posthoc.py` | **post-hoc** measurement of the inter-gold competition channel (§4.2) |
| `provenance-breadth-k.json` | commit, served model, all four gates, versions, GPU state |
| `runs-legb-k.json.gz`, `ranked-legb.json.gz` | Leg B per-query arrays (40 cells × 396 q) + **top-50 ranked lists** |
| `runs-lega.json.gz`, `ranked-lega.json.gz` | Leg A per-topic arrays (400 cells) + top-50 ranked lists |
| `lega_sims_<key>.npy` | **fp16 query × chunk similarity matrices** — any future k, m, replicate or corpus subset is a pure CPU re-analysis |
| `lega_spans_<key>.json`, `lega_emb_<key>.npy` | chunk `(doc, start, end, ntok)` + fp16 vectors, 5 configs |
| `lega_gold.json` | the 2 161 recovered passage-gold spans — Leg A's passage qrel, first use |
| `lega_corpora.json`, `lega_chains.json` | mini-corpus definitions and the nested gold chains |
| `report-breadth-k.json` | every reading with CI, δ80, discordance, Holm p |
| `posthoc-crowding.json` | the competition term per k, and the identity check of §4.2 |
| `gate-legb.json` | the 28-row reproduction gate |
| `estats-breadth-k.json`, `lega_embed.log` | cost: 6.75 GPU-minutes, 0 retries |

**Reusability note.** Item 1 of the brief asked for ranked lists so that any future `k` is a free
re-analysis. That is satisfied twice over: the top-50 ranked lists are persisted per cell, and the
full fp16 similarity matrices are persisted per config, from which **any** k, breadth, replicate,
budget or corpus subset can be recomputed with no GPU at all — as this run demonstrated by
re-scoring the whole Leg B k-extension in 11 seconds.
