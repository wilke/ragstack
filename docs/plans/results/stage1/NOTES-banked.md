# Banked findings (written during the run, before results were known)

## P1 chunk half — PASS (SHA-256, whole file)

| step-3 key | stage-1 grid key | sha256 (first 16) | bytes |
|---|---|---|---|
| `tok256_ov32` | `fixed_tok256_ov12_5pct` | `aa16d33a015fb211` | 128,024,931 |
| `tok512_ov64` | `fixed_tok512` | `979fc92bf296b772` | 124,207,880 |
| `tok2048_ov256` | `fixed_tok2048_ov12_5pct` | `81f2a297da10814e` | 119,653,370 |

Identical on both sides. This also validates the `sys.meta_path` pinning: the run resolved
`ragstack` to the working copy at `d225cea`, and the chunkers there differ from
`/rag/repos/ragstack` (6d6fcf6) only in comments — verified by diff.

## P1 metric half — PASS, exactly (max |diff| = 0.0000)

Re-chunked and **re-embedded from scratch** on the fleet (step 3's `.npy` files were not
read), scored against step 3's own indexed set of 4,053 docs so the denominators match:

| metric | step 3 | stage 1 | \|diff\| |
|---|---|---|---|
| `fixed_tok256_ov12_5pct` nDCG@10 | 0.4952 | 0.4952 | 0.0000 |
| `fixed_tok512` nDCG@10 | 0.4631 | 0.4631 | 0.0000 |
| `fixed_tok2048_ov12_5pct` nDCG@10 | 0.6000 | 0.6000 | 0.0000 |

All 12 metric x config combinations agree to 4 decimal places against a ±0.01 bar. Note
what this shows beyond the harness being correct: **the vLLM SFR endpoints are
reproducible enough that an independent re-embedding two hours later, batched differently
across six endpoints, produced an identical document ranking.** Non-determinism in the
embedding fleet is not a confound for this study.

## P5 precondition — SATISFIED

Realised chunks/doc at size 2048: **3.86 (0%) / 4.21 (12.5%) / 4.65 (25%)**. Overlap
engages at every rung of the ladder on this corpus, so the primary interaction contrast is
measurable. On scifact (`chunking-evaluation.md`) it would not be: 5,182 of 5,183 documents
are a single chunk at 2048 and all three overlap fractions give the same chunk count.

Overlap inflation at size 256 measured 1.000 / 1.135 / 1.317 against the theoretical
1/(1-f) = 1.000 / 1.143 / 1.333 — it engages essentially fully.

## The plan's fill table does NOT transfer to a long-document corpus

`chunking-evaluation.md` § "Nominal size is not realised size" measured fill on scifact
(median document 354 tokens):

| kind | 256 | 512 | 1024 | 2048 |
|---|---|---|---|---|
| scifact `token_window` | 100% | 65% | 35% | 17% |
| scifact `sentence` | 82% | 64% | 35% | 17% |
| scifact `words` | 62% | 56% | 34% | 17% |

Measured here (CDS pilot, median document 4,532 tokens):

| kind | 256 | 512 | 1024 | 2048 |
|---|---|---|---|---|
| `token_window` | 1.00 | 1.00 | 1.00 | 1.00 |
| `sentence` | 0.89 | 0.94 | 0.96 | **0.97** |
| `words` | 0.64 | 0.64 | 0.64 | 0.64 |

Two things change. (i) The scifact decay to 17% at 2048 is **entirely a document-length
artefact** — a 354-token document cannot fill a 2048-token budget — and vanishes here.
(ii) `sentence` fill *rises* with size here (0.89 -> 0.97) because the packer's waste is
one partial sentence per chunk, a shrinking fraction of a growing budget; `words` is flat
at 0.64 because the word packer's shortfall is proportional, not a fixed tail.

**Consequence for stage 2**: fill is a property of *corpus x kind*, not of kind. The
scifact-measured table must not be reused to interpret a long-document corpus, and the
"nominal size is not comparable across kinds" caveat is *stronger* here for `words`
(0.64 flat: `words_tok2048` is really a ~1,307-token config) and *weaker* for `sentence`.

## PRIMARY RESULT (12 token_window cells, all complete)

**No detectable size x overlap interaction — and, more usefully, overlap has no
detectable effect at any size.**

| contrast | mean | 95% CI | w/l | delta80 | bar X |
|---|---|---|---|---|---|
| **I = E(256) − E(2048)** (primary) | **−0.0409** | [−0.1729, +0.0793] | 5/5 | **0.213** | 0.05 |
| slope of E(s) per doubling | +0.0101 | [−0.0221, +0.0443] | 5/5 | 0.056 | 0.05 |
| overlap 25% − 0% (mean over sizes) | −0.0273 | [−0.0701, +0.0218] | 3/7 | 0.077 | 0.05 |
| overlap 12.5% − 0% (mean over sizes) | −0.0210 | [−0.0472, +0.0072] | 3/7 | **0.046** | 0.05 |
| size 2048 − 256 (mean over fracs) | +0.0904 | [−0.0277, +0.2199] | 7/3 | 0.210 | 0.05 |
| size 256 − 512 (mean over fracs) | +0.0238 | [−0.0406, +0.0899] | 5/4 | 0.112 | 0.05 |

### The methodological finding that matters most

**The pre-registered primary contrast is structurally under-powered for its own
threshold.** `I` is a difference of two differences — four cells — so its per-topic
variance compounds: measured `sd_d = 0.214`, giving an 80%-power detectable effect of
**0.213 nDCG@10, more than 4x the 0.05 bar we set**. At n = 10 this design *could not
have* returned a positive answer at the registered threshold, whatever the truth.

That is not a null result; it is an instrument that cannot resolve the question as posed,
and it was knowable in advance from the DiD's variance structure. **Stage 2 should make
the slope form primary, not the extremes DiD**: the slope uses all four sizes, and its
`sd_d = 0.057` gives `delta80 = 0.056` — an order of magnitude better and nearly adequate
for a 0.05 bar. It returns **+0.0101, CI [−0.022, +0.044]** — a genuinely tight null.

### What IS adequately powered, and what it says

The **overlap main effect at 12.5%** has `delta80 = 0.046 < 0.05`: this design could have
detected an overlap effect at the bar, and did not. Measured **−0.0210, CI [−0.047,
+0.007]**, 7 of 10 topics negative. So on Leg A:

> **Overlap does not pay for itself, at any size on the ladder.** It costs `1/(1−f)`
> vectors for the whole lifetime of an index and returns a point estimate that is
> *negative* on nDCG@10 and indistinguishable from zero on recall@100.

E(s) = 25% − 0%, per size — negative at every rung on nDCG, and ~0 at every rung on recall:

| size | nDCG@10 | recall@100 |
|---|---|---|
| 256 | −0.0446 | +0.0033 |
| 512 | −0.0197 | −0.0030 |
| 1024 | −0.0412 | −0.0013 |
| 2048 | −0.0037 | +0.0016 |

The recall@100 column is the striking one: **|Δ| ≤ 0.0033 at every size**. Overlap moves
recall essentially not at all, while multiplying the vector count by up to 1.32x
(measured). The prereg said "the burden of proof sits on overlap"; on Leg A it does not
discharge it.

### Pre-registered predictions, scored

- **P3 HOLDS exactly as predicted.** The non-monotone 256-vs-512 step from step 3 resolves
  as **noise**: averaged over the three overlap fractions, 256 − 512 = **+0.0238, CI
  [−0.041, +0.090]**, 5/4 signs — |Δ| < 0.05 with a CI spanning zero, which is the
  registered form of "resolved as noise". The fuller grid did what it was predicted to do.
- **P4 holds in magnitude, wrong in sign.** Predicted `0 < I < 0.05`; observed
  I = −0.041 — sub-threshold as predicted, but negative (overlap helps *less* at 256 than
  at 2048), and far inside the noise floor either way.
- **P2 not confirmed at the full bar.** Size 2048 − 256 = +0.090 clears the 0.05 point
  estimate and reaches 7/10 signs, but its CI spans zero — consistent with step 3, which
  found this same pair CI-unresolvable (step 3's load-bearing contrast was 2048−512).

### The surprise: the size ladder peaks at 1024, and the two metrics disagree

nDCG@10 ranks `1024_ov0` (0.6206) and `1024_ov12.5` (0.6034) **above every 2048 cell**
(0.5942–0.6000), while recall@100 puts 2048 clearly on top (0.3816–0.3833 vs 0.3605–0.3622
at 1024). So "coarse wins" is **not monotone**: precision at the top of the ranking peaks
around 1024, breadth keeps improving to 2048. And 512 is the *worst* size at every overlap
(0.4631–0.4994), below even 256 — reproducing step 3's local reversal rather than
dissolving it, though neither gap is CI-resolvable.

## The semantic cost finding — the brief's model was off by ~7x, and it is measured

The brief expected "the 4 semantic configs embed the text twice — roughly double cost".
`semantic` runs `pool_sentences=False`, so breakpoint detection embeds **one overlapping
7-sentence buffer TEXT per sentence**, not one sentence each. Measured on the real corpus:

| config (run order) | breakpoint **notional** | breakpoint **actual** | cache hit | x corpus (notional) |
|---|---|---|---|---|
| `semantic_tok2048_ov12_5pct` | 148.6M | 148.6M | 0% (first) | **6.0x** |
| `semantic_tok1024_ov12_5pct` | 148.5M | **0.6M** | **99.6%** | 6.0x |
| `semantic_tok512_ov12_5pct` | 146.3M | 7.9M | 94.6% | 5.9x |
| `semantic_tok256_ov12_5pct` | 124.0M | 67.5M | 45.6% | 5.0x |
| **all four** | **567M** | **225M** | **60.4% saved** | |

Wall clock 17.7 min, **212k tok/s actual**, 56,556 requests, **0 retries**, 546,209
breakpoint items per config (689,243 sentences in the corpus, median 115/doc).

The cache-hit column is the mechanism, confirmed: buffers are the *same text* at every size
except where `_cap_tokens` truncates one to the config's budget. At 1024 and 2048 the cap
essentially never bites (99.6% hit), at 512 rarely (94.6%), at 256 often (45.6%).

**For stage 2, project from notional, not actual.** A production ingest of *one* semantic
config pays ~6x the corpus in breakpoint embedding **on top of** its chunk embedding — so
semantic is ~7x the cost of a `token_window` config of the same nominal size, not 2x. That
is the single largest cost number in this study and the brief's model understated it ~3.5x.

Note also that the breakpoint pass's 212k tok/s is **not** comparable to the 164k chunk-embed
model: the request shape differs (buffer texts average ~272 tokens, 16 per request ≈ 4.4k
tokens/request vs the chunk path's ~8.2k), and this pass is request-bound rather than
token-bound. It ran *faster* per token precisely because the sequences are short.

## Semantic's `size` is a cap that mostly does not bind — its "fill" is not a fill

| config | chunks | chunks/doc | median realised tok | "fill" |
|---|---|---|---|---|
| `semantic_tok2048` | 58,040 | 14.32 | 343 | 0.17 |
| `semantic_tok1024` | 63,221 | 15.60 | 357 | 0.35 |
| `semantic_tok512` | 82,700 | 20.40 | 359 | 0.70 |
| `semantic_tok256` | 139,999 | 34.54 | 255 | **1.00** |

Semantic is adaptive: it emits ~350-token blocks whatever the ceiling, so the cap only binds
at 256 (median 255 = exactly capped). **The `fill` column for semantic must not be read as
the other kinds' fill** — 0.17 at 2048 does not mean "underfilled", it means the ceiling was
irrelevant. Two consequences worth carrying:

1. `semantic_tok2048` and `semantic_tok1024` are nearly the same chunking (14.32 vs 15.60
   chunks/doc); the size axis barely moves semantic until it starts truncating.
2. At the same nominal size, semantic produces **3.4x more chunks** than `token_window`
   (14.32 vs 4.21 per doc at 2048), so its index is 3.4x larger. Nominal size hides that.

## PREREG §8.2 group-boundary budget check — PROCEED (run at the 20/24 boundary)

| quantity | value |
|---|---|
| chunk-embed tokens, 20 non-semantic configs | **628M** |
| fleet-busy time for them | **65.0 min** (sum of per-config embed seconds) |
| achieved | **161k tok/s** vs the 164k model — **98% of it** |
| requests / retries | **107,409 / 0** |
| semantic group, projected | ~40 min breakpoint (request-bound) + ~11 min chunk embed |
| **elapsed + projected** | **~116 min vs the 180 min ceiling → PROCEED** |

(The check is run at the group boundary because the prereg promised it there. The
automated chain launches on config count alone, and each `stage1_embed_score.py`
invocation resets its own `--budget-hours` clock, so the cumulative check is this
explicit one rather than the flag.)

## Cost-model check, non-semantic group (COMPLETE)

`token_window` + `sentence` + `words` = 628.4M chunk-embed tokens, measured from the chunk
files before any embedding call. Achieved: 158-192k tok/s per config, **169k tok/s
cumulative** at the 4-config mark, **0 retries** — the 164k tok/s model holds.

Per-config rate falls with chunk size because the request becomes item-bound rather than
token-bound: at 2048 a request carries 4 chunks x 2048 = 8,192 tokens (the token cap), at
256 it carries 16 x 256 = 4,096 (the item cap). Same request count, half the tokens.
