# `semantic` and `semantic_pooled` are two chunkers, not one made cheaper

**Date:** 2026-09-18 · **Tenant:** dev · **Status:** measured, reproducible, small-n

`make_chunker`'s docstring describes `semantic_pooled` as *"cheaper, GPU-friendly"*,
which reads as the same algorithm made efficient. It is not. On identical input
the two place **different boundaries**, and each is reproducible against itself,
so the difference is algorithmic rather than noise.

Anything that treats them as a cheap/expensive pair of the same chunker — a study
arm, a grid row, a recommendation to "use pooled, it's the same but faster" —
is mislabelled.

## What differs

One boolean in `make_chunker` sets two things:

| | `semantic` | `semantic_pooled` |
|---|---|---|
| embedded input | each overlapping **buffer text** (`2*buffer_size+1` sentences, re-embedded at every position) | each **sentence once**, mean-pooled over the same window |
| `distance_round` | `None` — raw cosine | `6` decimals before the percentile |

Everything downstream is the same code: same `sentence_spans`, same window, same
cosine → percentile → breakpoint → merge-short.

The vectors necessarily differ — a transformer is not linear in its input, and
cross-sentence attention within the window is exactly what pooling discards. What
was **not** obvious is whether the *boundaries* differ, because the breakpoint rule
consumes only the **percentile rank** of consecutive distances. Equal boundaries
need rank-order preservation, not equal vectors. That is the hypothesis this
measures.

## Method

3 synthetic documents, 1,624 chars each, built from three clearly distinct topic
blocks (antimicrobial resistance / photosynthesis / seismology) in three different
orders, so a topic boundary exists at a known offset.

Both arms: `buffer_size=2`, percentile 80.0, `min_chunk_length=500`,
`Salesforce/SFR-Embedding-Mistral` over dev's two endpoints, run inside
`ragstack-worker-v1.6.4.sif` through the tool's own embedder builder. Identical
shard bytes (`md5 972bf35258f82a01382403a2d0d22f1d`).

Distances taken from the real `SemanticChunker._buffer_embeddings`, with pooled's
`distance_round` applied as the chunker applies it.

## Result

```
                    semantic    semantic_pooled
texts embedded           108                108
TOKENS embedded       10,032              2,244        ratio 4.47x

overall Spearman (rank correlation of the two distance series)   0.4254
overall span Jaccard                                             0.111

  doc0   1 vs 1 chunks   Jaccard 1.000   rho 0.2402
  doc1   2 vs 2 chunks   Jaccard 0.000   rho 0.4559
  doc2   2 vs 2 chunks   Jaccard 0.000   rho 0.5784
```

**Read the Jaccard, not the chunk count.** The arms agree on the *number* of
chunks (1, 2, 2) and on both documents that split they share **no boundary at
all** — legacy cut at 694 and 892, pooled at 965 and 1076. A summary that counted
chunks would have reported agreement.

## The control, which is what makes this a finding

Each arm run twice against the same fleet:

```
semantic         run1 vs run2   Spearman 0.9984   spans IDENTICAL   18/51 distances bit-identical
semantic_pooled  run1 vs run2   Spearman 0.9993   spans IDENTICAL   31/51 distances bit-identical
```

Both arms are reproducible with themselves, so the 0.4254 between them is a real
method difference and not fleet nondeterminism.

Note the nuance: float nondeterminism **is** present — only 18/51 and 31/51
distances are bit-identical across runs — but on this input it was never large
enough to move a boundary. `semantic_pooled` rounds distances precisely to make
that guarantee; `semantic` does not, so legacy's reproducibility here is observed,
not guaranteed.

## Cost

4.47x measured at `buffer_size=2`. The arithmetic is `2*buffer_size+1`, so ~5x
expected and 4.47x observed (edge buffers are shorter than a full window). **At
the server default `buffer_size=3` it is ~7x.** An earlier "~7x" figure quoted
during this work was that default, not these collections.

## What this does NOT establish

- **Which is better.** Boundary *quality* is a retrieval question needing real
  documents and an evaluation, not a span diff. This says only that they differ.
- **Anything at corpus scale.** 3 synthetic documents, 51 distance pairs, one
  fleet state.
- **That legacy is reproducible in general.** Observed here; unrounded distances
  make it not guaranteed.

## Consequences

- **A cheap/expensive framing is wrong.** Choosing pooled to save tokens also
  changes where the chunks are.
- **Chunk method is collection identity**, so a collection declared `semantic`
  cannot be moved to pooled in place — it needs recreating.
- It supports the decision **not** to repoint the `semantic` keyword at pooled
  (docs/plans/chunking-one-factory.md §7d): `asm-semantic` holds 6,718,269 points
  recorded as `semantic`, and an alias would have silently changed what they
  claim to be.
