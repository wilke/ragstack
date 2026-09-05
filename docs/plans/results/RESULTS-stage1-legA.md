# Stage 1 — the 24-config chunking grid on the Leg A (TREC CDS) pilot set

*Run 2026-09-04 against [`PREREG-stage1.md`](PREREG-stage1.md), written before any embedding
call. Repo `/home/wilke/Development/ragstack`, main at `d225cea`. Grid = the committed
`chunking_compare_7way.STAGE1_CONFIGS`, imported rather than re-declared. All 24 configs
completed; 968M tokens actually embedded in 94 min of fleet time; zero retries; zero store
writes.*

> ## Read this first
>
> **This run is PROVISIONAL and prunes nothing.** Leg A has a *measured* bias
> ([`long-doc-judged-set.md`](../../../docs/plans/long-doc-judged-set.md) §13.3): CDS
> relevance is document-level and topical, so the leg rewards coarse, aboutness-carrying
> configs. **The ranking below may in part be that bias measuring itself.** Legs B and C are
> deep-evidence by construction, exist to confirm or contradict this direction, and **have
> not run**. No config may be dropped from the grid on this evidence alone — least of all on
> the chunk-size axis, where Leg A's bias points the same way as the cheaper option, the
> direction in which a biased instrument does the most damage.
>
> **n = 10 topics.** Adding grid cells adds contrasts, not power. Inference is confined to 9
> pre-registered contrasts with Holm correction; the 24-row ranking is descriptive and
> neighbouring rows are not ordered claims.

---

## 1. Answers, in one place

| question | answer |
|---|---|
| **Does overlap's effect depend on chunk size?** | **No detectable interaction.** `I = E(256) − E(2048) = −0.0409`, CI [−0.173, +0.079], 5/5 signs, Holm p = 1.00. The slope form is a tighter null: **+0.0101 per doubling**, CI [−0.022, +0.044]. |
| **Does overlap have *any* effect?** | **No measurable benefit — and this one is adequately powered.** Overlap 12.5% − 0% = **−0.0210**, CI [−0.047, +0.007], `delta80 = 0.046 < 0.05`; the point estimate is negative at every size and the interval is consistent with overlap *hurting* by up to ~0.047, not with it helping. On recall@100 the effect is **≤ 0.0033 in absolute value at every size**. |
| **Top configs (dense nDCG@10)** | `sentence_tok2048` 0.6289 · `fixed_tok1024_ov0pct` 0.6206 · `words_tok2048` 0.6081 · `fixed_tok1024_ov12_5pct` 0.6034 · `fixed_tok2048_ov12_5pct` 0.6000 |
| **What actually predicts the grid** | **Realised median chunk tokens.** `corr(log2 realised, nDCG@10) = +0.811`; `corr(log2 realised, recall@100) = +0.891`. Nominal size only manages +0.654, and *kind* adds almost nothing once realised size is controlled. |
| **Achieved throughput** | **171k tok/s overall** (968M actual tokens / 94.4 min), against the 164k model. Chunk-embed leg alone: **161k tok/s**, 98% of model. **0 retries in 186,647 requests.** |
| **Total cost** | **968M tokens actually embedded, 94.4 min = 1.57 h fleet wall-clock** (ceiling 3 h). **Notional 1,311M** — the number stage 2 must project from. |
| **Stores** | Qdrant `:24041` and ES `:24043` **byte-identical**, SHA-256 verified before/after. |

---

## 2. The reproduction gate (P1) — PASS, exactly

Three grid cells *are* step-3 configs. They were re-chunked and **re-embedded from scratch**
(step 3's `.npy` files were never read), then scored against step 3's own indexed set.

| | step 3 → stage 1 | result |
|---|---|---|
| chunk files | `tok256_ov32`→`fixed_tok256_ov12_5pct`, `tok512_ov64`→`fixed_tok512`, `tok2048_ov256`→`fixed_tok2048_ov12_5pct` | **byte-identical, SHA-256** (`aa16d33a015fb211`, `979fc92bf296b772`, `81f2a297da10814e`) |
| all 12 metric × config values | bar ±0.01 | **max \|diff\| = 0.0000** |

Two things this establishes beyond "the harness is correct":

1. **The code pinning worked.** `/rag/envs/ragstack` carries an editable-install *meta-path
   finder* pointing at `/rag/repos/ragstack` — a **different commit** (`6d6fcf6`) — and a
   meta-path finder runs before `sys.path`, so `PYTHONPATH` does not win. It was stripped in
   the parent and re-asserted in every worker. Byte-identical chunks prove the working copy
   at `d225cea` was the code that ran.
2. **The embedding fleet is reproducible enough not to be a confound.** An independent
   re-embedding, batched differently across six endpoints, produced an *identical document
   ranking* to four decimal places.

---

## 3. The 24-config grid

Summary queries, grade ≥ 1, dense (no rerank), means over 10 topics. `fill` = median realised
tokens ÷ nominal size. **Descriptive — neighbouring rows differ by less than the noise floor
(±0.12 at 80% power) and are not ordered claims.**

| rank | config | kind | size | ovl | c/doc | med tok | fill | nDCG@10 | R@100 | MRR@10 |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `sentence_tok2048_ov12_5pct` | sentence | 2048 | 12.5% | 4.2 | 1977 | 0.97 | **0.6289** | 0.3888 | 0.7833 |
| 2 | `fixed_tok1024_ov0pct` | token_window | 1024 | 0% | 7.2 | 1024 | 1.00 | **0.6206** | 0.3618 | 0.8125 |
| 3 | `words_tok2048_ov12_5pct` | words | 2048 | 12.5% | 6.3 | 1307 | 0.64 | **0.6081** | 0.3783 | 0.8500 |
| 4 | `fixed_tok1024_ov12_5pct` | token_window | 1024 | 12.5% | 8.0 | 1024 | 1.00 | **0.6034** | 0.3622 | 0.7743 |
| 5 | `fixed_tok2048_ov12_5pct` | token_window | 2048 | 12.5% | 4.2 | 2048 | 1.00 | **0.6000** | 0.3833 | 0.8033 |
| 6 | `fixed_tok2048_ov0pct` | token_window | 2048 | 0% | 3.9 | 2048 | 1.00 | 0.5942 | 0.3816 | 0.8033 |
| 7 | `fixed_tok2048_ov25pct` | token_window | 2048 | 25% | 4.7 | 2048 | 1.00 | 0.5905 | 0.3832 | 0.8000 |
| 8 | `fixed_tok1024_ov25pct` | token_window | 1024 | 25% | 9.1 | 1024 | 1.00 | 0.5794 | 0.3605 | 0.7643 |
| 9 | `sentence_tok1024_ov12_5pct` | sentence | 1024 | 12.5% | 8.0 | 979 | 0.96 | 0.5654 | 0.3639 | 0.7611 |
| 10 | `sentence_tok256_ov12_5pct` | sentence | 256 | 12.5% | 32.5 | 229 | 0.89 | 0.5389 | 0.3229 | 0.7750 |
| 11 | `words_tok1024_ov12_5pct` | words | 1024 | 12.5% | 12.3 | 656 | 0.64 | 0.5343 | 0.3302 | 0.6611 |
| 12 | `fixed_tok256_ov0pct` | token_window | 256 | 0% | 27.4 | 256 | 1.00 | 0.5315 | 0.3374 | 0.6750 |
| 13 | `sentence_tok512_ov12_5pct` | sentence | 512 | 12.5% | 15.8 | 479 | 0.94 | 0.5237 | 0.3349 | 0.7333 |
| 14 | `fixed_tok512_ov0pct` | token_window | 512 | 0% | 14.0 | 512 | 1.00 | 0.4994 | 0.3310 | 0.6893 |
| 15 | `fixed_tok256_ov12_5pct` | token_window | 256 | 12.5% | 31.2 | 256 | 1.00 | 0.4952 | 0.3403 | 0.6111 |
| 16 | `semantic_tok2048_ov12_5pct` | semantic | 2048 | 12.5% | 14.3 | 343 | 0.17 | 0.4912 | 0.3224 | 0.5810 |
| 17 | `fixed_tok256_ov25pct` | token_window | 256 | 25% | 36.1 | 256 | 1.00 | 0.4869 | 0.3407 | 0.7000 |
| 18 | `semantic_tok1024_ov12_5pct` | semantic | 1024 | 12.5% | 15.6 | 357 | 0.35 | 0.4847 | 0.3215 | 0.5786 |
| 19 | `fixed_tok512_ov25pct` | token_window | 512 | 25% | 18.1 | 512 | 1.00 | 0.4797 | 0.3281 | 0.6560 |
| 20 | `semantic_tok256_ov12_5pct` | semantic | 256 | 12.5% | 34.5 | 255 | 1.00 | 0.4711 | 0.3226 | 0.5708 |
| 21 | **`fixed_tok512`** (shipping) | token_window | 512 | 12.5% | 15.7 | 512 | 1.00 | 0.4631 | 0.3349 | 0.6750 |
| 22 | `words_tok256_ov12_5pct` | words | 256 | 12.5% | 48.3 | 164 | 0.64 | 0.4559 | 0.3295 | 0.5933 |
| 23 | `words_tok512_ov12_5pct` | words | 512 | 12.5% | 24.3 | 328 | 0.64 | 0.4499 | 0.3344 | 0.6087 |
| 24 | `semantic_tok512_ov12_5pct` | semantic | 512 | 12.5% | 20.4 | 359 | 0.70 | **0.4049** | 0.3156 | 0.5593 |

The shipping default `fixed_tok512` ranks **21 of 24**. That is a real observation and an
uncomfortable one, but it is *one leg's* observation and it is inside the noise floor of most
of the rows above it — see §6 before doing anything with it.

---

## 4. The primary question: no interaction, and the instrument could not have found one

### 4.1 The size × overlap panel (`token_window`, nDCG@10)

| size | 0% | 12.5% | 25% | `E(s)` = 25% − 0% | recall@100 `E(s)` |
|---:|---:|---:|---:|---:|---:|
| 256 | 0.5315 | 0.4952 | 0.4869 | **−0.0446** | +0.0033 |
| 512 | 0.4994 | 0.4631 | 0.4797 | **−0.0197** | −0.0030 |
| 1024 | 0.6206 | 0.6034 | 0.5794 | **−0.0412** | −0.0013 |
| 2048 | 0.5942 | 0.6000 | 0.5905 | **−0.0037** | +0.0016 |

### 4.2 The pre-registered family, Holm-corrected

nDCG@10, grade ≥ 1, summary, dense. Bar X: |mean| ≥ 0.05 **and** CI excludes 0 **and** ≥ 7/10
signs **and** survives Holm across these 9.

| contrast | mean | 95% CI | w/l | p | Holm p | resolved? |
|---|---:|---|---:|---:|---:|---|
| **I = E(256) − E(2048)** *(primary)* | −0.0409 | [−0.1729, +0.0793] | 5/5 | 0.559 | 1.000 | no |
| slope of E(s) per doubling | +0.0101 | [−0.0221, +0.0443] | 5/5 | 0.545 | 1.000 | no |
| overlap 25% − 0% | −0.0273 | [−0.0701, +0.0218] | 3/7 | 0.246 | 1.000 | no |
| overlap 12.5% − 0% | −0.0210 | [−0.0472, +0.0072] | 3/7 | 0.144 | 1.000 | no |
| size 2048 − 256 | +0.0904 | [−0.0277, +0.2199] | 7/3 | 0.140 | 1.000 | no |
| size 256 − 512 | +0.0238 | [−0.0406, +0.0899] | 5/4 | 0.495 | 1.000 | no |
| **sentence512 − fixed512** | **+0.0606** | **[+0.0138, +0.1072]** | **7/2** | **0.011** | 0.097 | **no — but only just** |
| words512 − fixed512 | −0.0132 | [−0.1170, +0.0958] | 4/5 | 0.800 | 1.000 | no |
| semantic512 − fixed512 | −0.0583 | [−0.1430, +0.0275] | 3/6 | 0.188 | 1.000 | no |

**Nothing is "resolved" under the pre-registered rule.** One contrast comes close and should
be flagged rather than buried: `sentence_tok512` beats `fixed_tok512` by **+0.0606** with a
**CI that excludes zero** and 7/10 topics agreeing — it clears every criterion except Holm
across the family of 9 (adjusted p = 0.097). Treat it as the grid's most promising signal and
the obvious thing for stage 2 to power properly, not as an established result.

### 4.3 The methodological finding that matters most

**The pre-registered primary contrast is structurally under-powered for its own threshold.**
`I` is a difference of two differences — four cells — so its per-topic variance compounds:

| contrast | measured `sd_d` | 80%-power detectable effect | bar |
|---|---:|---:|---:|
| **`I` (extremes DiD)** | 0.214 | **0.213** | 0.05 |
| slope of `E(s)` | 0.057 | 0.056 | 0.05 |
| overlap 12.5% − 0% | 0.047 | **0.046** | 0.05 |
| size 2048 − 256 | 0.211 | 0.210 | 0.05 |

At n = 10 the DiD **could not have** returned a positive answer at the registered 0.05 bar,
whatever the truth — its resolution floor is 4× the bar. That was knowable in advance from the
contrast's variance structure and we did not catch it when writing the prereg. It is the main
process lesson of this stage.

**But the null is not empty.** Two of the family's contrasts *are* adequately powered:

- the **slope form** of the interaction (`delta80 = 0.056`) returns **+0.0101, CI [−0.022,
  +0.044]** — a genuinely tight null;
- the **overlap main effect at 12.5%** (`delta80 = 0.046 < 0.05`) returns **−0.0210, CI
  [−0.047, +0.007]**.

So the honest statement is: *we cannot resolve the interaction as posed, but we can say the
overlap effect is small everywhere, and that its dependence on size is small.*

### 4.4 Overlap does not pay for itself

The prereg put the burden of proof on overlap, because it costs `1/(1−f)` vectors for the
whole lifetime of an index. Measured inflation at size 256: **1.000 / 1.135 / 1.317** chunks
(theory 1.000 / 1.143 / 1.333 — it engages essentially fully). What it buys:

- **nDCG@10: negative at every rung** (−0.045, −0.020, −0.041, −0.004).
- **recall@100: |Δ| ≤ 0.0033 at every rung.** Overlap moves recall essentially not at all.

On Leg A, overlap fails to discharge the burden. It is the one axis where this run is
adequately powered to say something, and it says: **0% overlap is not worse.**

### 4.5 Pre-registered predictions, scored

| # | prediction | outcome | verdict |
|---|---|---|---|
| **P5** | overlap engages at 2048 (chunks/doc ≫ 1) | 4.21 chunks/doc | **HOLDS** — the contrast is valid on this corpus (it would be void on scifact, where 5,182/5,183 docs are one chunk at 2048) |
| **P2** | size effect replicates: M(2048) − M(256) ≥ 0.05 with CI excluding 0 | +0.0904, CI [−0.028, +0.220] | **FALSIFIED on nDCG@10** — but **holds on recall@100** (+0.0432, CI [+0.0151, +0.0764]) |
| **P3** | the 256-vs-512 step resolves as **noise** | +0.0238, CI [−0.041, +0.090], 5/4 | **HOLDS** — exactly as predicted |
| **P4** | interaction `0 < I < 0.05` | −0.0409, CI spans 0 | **HOLDS in magnitude, WRONG SIGN** |

P3 is the one clean pre-registered success: step 3's non-monotone 256-vs-512 reversal, run at
a single overlap, **does** dissolve into noise once averaged over the overlap axis.

---

## 5. What actually predicts the grid: realised size, not nominal size and not kind

> **Exploratory — NOT pre-registered.** This section was not in the §3 family and gets no
> Holm protection. The correlations are across 24 *config means* that all share the same 10
> topics, so they are not independent observations and no inferential claim is made from
> them: read this as a description of the grid's shape and a hypothesis for stage 2 to
> pre-register, held to a lower standard than §4.

The prereg required `fill` because "nominal size is not comparable across kinds". That turns
out to understate it — realised size looks like *the* explanatory variable.

| predictor | corr with nDCG@10 | corr with recall@100 |
|---|---:|---:|
| log2 **nominal** size | +0.654 | — |
| log2 **realised median tokens** | **+0.811** | **+0.891** |

Fit across all 24 configs: `nDCG@10 ≈ 0.1185 + 0.0447 × log2(realised median tokens)`.
Residual by kind — how much each method beats or misses the realised-size trend:

| kind | n | mean residual |
|---|---:|---:|
| `sentence` | 4 | **+0.0251** |
| `token_window` | 12 | +0.0020 |
| `words` | 4 | −0.0025 |
| `semantic` | 4 | **−0.0287** |

**Once you know how many tokens land in a chunk, the chunking *method* adds almost nothing**
— the whole kind spread is 0.054, about the noise floor. `words_tok2048` ranks 3rd not because
word packing is clever but because its 0.64 fill makes it a ~1,307-token config; `semantic`
ranks last because it emits ~350-token blocks whatever its cap says.

Two caveats that must travel with any kind-vs-kind reading:

- **`sentence`/`words` rows labelled 12.5% carry ≈ 8.9% effective overlap.** Their packer
  takes overlap in *chars* at `OVERLAP_CHARS_PER_TOKEN = 2.5`, while production measures 3.50.
  Only the `token_window` rows are exact.
- **`semantic`'s `fill` is not a fill.** Its `size` is a *cap* that mostly does not bind; 0.17
  at 2048 means the ceiling was irrelevant, not that chunks were short of a target.

### 5.1 The plan's fill table does not transfer to a long-document corpus

`chunking-evaluation.md` measured fill on scifact (median document **354** tokens). Measured
here on the CDS pilot (median document **4,532** tokens):

| kind | 256 | 512 | 1024 | 2048 |
|---|---|---|---|---|
| scifact `sentence` | 82% | 64% | 35% | 17% |
| **CDS `sentence`** | **0.89** | **0.94** | **0.96** | **0.97** |
| scifact `words` | 62% | 56% | 34% | 17% |
| **CDS `words`** | **0.64** | **0.64** | **0.64** | **0.64** |

The scifact decay to 17% is **entirely a document-length artefact** — a 354-token document
cannot fill a 2048-token budget — and it vanishes here. `sentence` fill *rises* with size
(waste is one partial sentence per chunk, a shrinking fraction of a growing budget); `words`
is flat at 0.64 because its shortfall is proportional. **Fill is a property of corpus × kind,
not of kind.** The scifact table must not be reused to interpret long-document corpora.

---

## 6. Reranking reorders the grid

`bge-reranker-v2-m3` over the top-100 of each config (48,000 pairs total):

- spread barely shrinks (dense 0.224 → reranked 0.205; SD 0.063 → 0.055),
- but the **rank correlation across the 24 configs is only r = +0.553**.

The configs that gain most are the ones that did worst dense (`fixed_tok512` +0.088,
`fixed_tok256_ov25pct` +0.085, `semantic_tok256` +0.074); the best lose
(`fixed_tok1024_ov0pct` −0.096, `words_tok2048` −0.078). **A config chosen on dense nDCG is
not necessarily the config that wins after reranking**, and the production pipeline reranks.
Any stage-2 decision that will ship behind a reranker must be evaluated behind one.

Step 3's label is kept: **reranked numbers rank arms; they do not grade the product** — the
cross-encoder often *lowers* absolute nDCG against the SFR dense ordering on this clinical set.

---

## 7. Cost and throughput — the check on the model stage 2 depends on

| leg | tokens | wall | achieved | requests | retries |
|---|---:|---:|---:|---:|---:|
| chunk embedding, 24 configs | **744M** | 76.8 min | **161k tok/s** | 130,091 | **0** |
| semantic breakpoint pass | 225M actual (**567M notional**) | 17.7 min | 212k tok/s actual | 56,556 | **0** |
| **total fleet** | **968M actual** | **94.4 min = 1.57 h** | **171k tok/s** | 186,647 | **0** |
| **notional total** | **1,311M** | | | | |

**The 164k tok/s model holds**: the chunk-embed leg came in at 161k, 98% of model, with zero
retries across 130k requests. Per-config rate falls with chunk size because a request becomes
item-bound rather than token-bound (at 2048 a request carries 4 × 2048 = 8,192 tokens, the
token cap; at 256 it carries 16 × 256 = 4,096, the item cap — same request count, half the
tokens). The breakpoint pass's 212k is **not** comparable to the model: its inputs are
~272-token buffer texts (~4.4k tokens/request), so it is request-bound and runs faster per
token precisely because the sequences are short.

**Budget compliance (PREREG §8.2).** The group-boundary check was run explicitly at the 20/24
boundary: 628M tokens / 65.0 min elapsed + ~51 min projected = ~116 min vs the 180 min
ceiling → **PROCEED**. Final actual: **94.4 min, well inside**. (The automated chain launches
on config count alone and each invocation resets its own `--budget-hours` clock, so the
cumulative check is the explicit one, not the flag.)

### 7.1 The brief's semantic cost model was wrong by ~3.5×

The brief expected the semantic configs to "embed the text twice — roughly double cost".
`semantic` runs `pool_sentences=False`, so breakpoint detection embeds **one overlapping
7-sentence buffer TEXT per sentence**:

| config (run order) | notional | actual | cache hit | × corpus |
|---|---:|---:|---:|---:|
| `semantic_tok2048` | 148.6M | 148.6M | 0% (first) | **6.0×** |
| `semantic_tok1024` | 148.5M | 0.6M | **99.6%** | 6.0× |
| `semantic_tok512` | 146.3M | 7.9M | 94.6% | 5.9× |
| `semantic_tok256` | 124.0M | 67.5M | 45.6% | 5.0× |
| **all four** | **567M** | **225M** | **60.4% saved** | |

The cache is exactly equivalent (same text → same vector) and its hit pattern confirms the
mechanism: buffers are identical across sizes except where `_cap_tokens` truncates them, so
the cap never bites at 1024/2048, rarely at 512, often at 256.

**Project stage 2 from notional, not actual.** One semantic config costs ~6× the corpus in
breakpoint embedding *on top of* its chunk embedding — so **semantic is ~7× a `token_window`
config of the same nominal size**, not 2×. Combined with §3, semantic is simultaneously the
**worst-scoring kind** (mean 0.4630, ranks 16/18/20/24) and by far the **most expensive**. It
also produces 3.4× more chunks than `token_window` at the same nominal size (14.3 vs 4.2 per
doc at 2048), so its index is 3.4× larger. That is the worst cost/benefit position in the
grid — *and per §1 it still may not be pruned on Leg A alone.*

12 of 4,053 documents exceeded `max_breakpoint_sentences = 3000` and were chunked by the
`fixed_token` fallback **inside** the semantic arm (the largest: 3,972 spans, 656k chars), so
those documents are not semantically chunked in any semantic row.

---

## 8. Store-untouched proof

The harness constructs **no Qdrant or Elasticsearch client anywhere** — retrieval is exact
brute-force cosine over in-memory numpy. Production `:6333` / `:9200` were never contacted;
only the dev-tenant stores were even *read*, to snapshot them.

Snapshots (collection/index listings **plus exact per-collection document counts**) taken
before the run, mid-run, and after the reranking pass:

```
IDENTICAL: Qdrant :24041 and ES :24043 unchanged before/after
64a5698876f8a9fa5e2fc534ca6ba1a9bef0dcad81bd27c13601cb0956853976  stores_before.txt
64a5698876f8a9fa5e2fc534ca6ba1a9bef0dcad81bd27c13601cb0956853976  stores_after.txt
```

Contents (unchanged throughout): Qdrant `ragstack_lib_oa_dev_..._e788c5be` = 24,263 points,
`ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe` = 0; ES `ragstack` = 0,
`ragstack_lib_oa_dev_..._e788c5be` = 24,263 docs.

**GPU citizenship**: only `:9001`–`:9006` (GPUs 0–5) were used, ≤ 2 in-flight requests per
endpoint throughout (a token queue holding each endpoint twice). **GPUs 6 and 7 were never
touched and no new endpoint was started.** Zero retries in 186,647 requests indicates the
fleet was never pushed into backpressure.

---

## 9. What is and is not supported

**Supported:**

- Overlap's effect does not measurably depend on chunk size on this leg (slope +0.010, CI
  [−0.022, +0.044], adequately powered).
- Overlap has no measurable benefit at any size, and costs up to 1.32× the vectors
  (adequately powered on the 12.5% main effect; recall@100 |Δ| ≤ 0.0033 everywhere).
- Realised median chunk size, not nominal size and not method, is what tracks retrieval
  quality here (r = +0.81 nDCG, +0.89 recall@100).
- The step-3 results reproduce exactly, and the fleet is reproducible.
- The 164k tok/s cost model is sound; the brief's semantic cost model is not.

**Not supported — do not quote these as findings:**

- Any claim that a specific config is best. The top five span 0.629–0.600 against a
  0.12-nDCG noise floor. The ranking is a ranking, not a result.
- `sentence` beating `fixed` (+0.061). It clears X and its CI excludes zero, but not Holm.
  Promising; unproven.
- That `fixed_tok512` (rank 21) should be replaced. One leg, n = 10, inside the noise floor
  of most rows above it, and the leg is biased toward the coarse configs that beat it.
- Any interaction claim in either direction: the DiD instrument's floor is 0.213 vs a 0.05
  bar.
- That semantic should be dropped, however bad its cost/benefit looks here. §1 applies.

**Multiple-comparison handling, as pre-registered:** inference is confined to the 9 named
contrasts with Holm–Bonferroni at α = 0.05 on one primary metric and condition (nDCG@10,
grade ≥ 1, summary, dense). The 276 pairwise contrasts the grid admits were never tested. All
other metrics, both grade thresholds, the `description` variant and every reranked number are
descriptive and labelled as such in [`tables.md`](tables.md).

---

## 10. Recommendations for stage 2

1. **Make the slope form the primary interaction contrast, not the extremes DiD.** Same data,
   4× better resolution (`delta80` 0.056 vs 0.213). Pre-registering a contrast without
   computing its variance structure is what went wrong here.
2. **Parameterise the grid by realised tokens, or at minimum report against them.** Nominal
   size confounds the kind comparison so badly that "kind" nearly disappears when you control
   for it.
3. **Drop the burden-of-proof question on overlap only after Legs B/C.** If they agree, 0%
   overlap is a free `1/(1−f)` saving on every index for the product's lifetime — the largest
   actionable number in this run.
4. **Evaluate behind the reranker.** Dense and reranked rankings correlate only +0.55.
5. **Budget semantic at ~7× a token_window config**, and note it is the worst-scoring kind
   here. If GPU budget forces a cut in stage 2's *scope* (not the grid), semantic is where the
   cost is.
6. **Power.** At n = 10 the floor is ~0.12 nDCG@10. Leg A at its full 90 topics gives ~0.04 —
   which is where most of these contrasts live. **The full leg is worth running; this pilot
   was never going to resolve them.**

---

## 11. Files

```
stage1/
  PREREG-stage1.md        predictions, bar X, the 9-contrast family, budget — pre-committed
  NOTES-banked.md         findings recorded during the run, as they landed
  RESULTS-stage1-legA.md  this document
  tables.md               generated Tables 1-8 (full grid, panels, all metrics, verdicts)
  report1.json            every per-topic metric for every config and arm
  probe_cost.py           the CPU-only probe that caught the semantic 7x
  stage1_common.py        repo pinning, fleet client (<=2 in-flight/endpoint), cache
  stage1_chunk.py         20 non-semantic configs (CPU, 246 s)
  stage1_semantic.py      4 semantic configs (fleet breakpoint detection + per-doc cache)
  stage1_embed_score.py   embed -> exact cosine -> top-200, checkpointed per config
  stage1_report.py        rerank, metrics, bootstraps, Holm, auto-scored verdicts
  verify_step3.py / verify_step3_metrics.py    the P1 gates
  chunks_*.jsonl (24)     realised chunks + per-chunk SFR token counts
  runs_*.json (24)        checkpoints: top-200 docs x 20 queries per config
  cstats_*/estats_*.json  structure and cost per config
  semantic_cost.json      notional vs actual breakpoint accounting
  stores_{before,mid,after}.txt   the store-untouched proof
  chunk1.log / embed1.log / semantic1.log / embed_sem.log / finish.log
```

Embeddings were deliberately not persisted (24 configs of fp16 vectors exceed the free space
on this filesystem); the per-config run file is the checkpoint, so an interruption would have
lost at most the config in flight.
