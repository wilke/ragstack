# Addendum S: semantic chunking, with and without a token cap (redacted public copy)

> **Redacted copy.** The private original is kept with the outputs, off GitHub. Its sha256 is
> `68e31254fc5501cf99ed1851c8530c9d082c538a4ebcbfc10b7c04c706a584bf` (recorded 2026-10-23T14:32:20Z).
> Only the collection's name and host were replaced.

Written 2026-10-23. The part-2 run had started, but no part-2 results had been seen. The only thing seen
was the live chunk sizes. Measured with the SFR-Embedding-Mistral tokenizer, the 1,265 live chunks have:
- median 311 tokens;
- p90 665;
- max 3,398;
- 0 overlapping adjacent pairs out of 929.

Owner: test semantic chunking in this setup and see whether it creates large chunks; "I would consider
that a potential bug." EXPLORATORY.

## Hypothesis (from code reading, origin/main 0b43ebb)
- `SemanticChunker` has no size cap unless `max_tokens` is passed.
- `api/deps.py` (around line 1480) sets `max_tokens` only when `chunk_max_tokens` is configured, or when
  the method is fixed_token.
- The live manifest records `chunk_method semantic, chunk_size 512, chunk_overlap 64, chunk_params
  {buffer_size 3, breakpoint_percentile_threshold 80, min_chunk_length 500}`.
- Prediction: rechunking the reassembled pages with those parameters and no `max_tokens` reproduces the
  live chunks, including chunks over 512 tokens.

## Arms (same pages, pipeline, gold and endpoints as part 2)

| arm | make_chunker("semantic", …) |
|---|---|
| **SEMlive** | buffer_size 3, breakpoint_percentile_threshold 80, min_chunk_length 500, chunk_size 512, chunk_overlap 64, **no max_tokens** |
| **SEM512** | as SEMlive, plus max_tokens 512 and token_counter SFR |
| **SEM256** | as SEMlive, plus max_tokens 256, chunk_size 256, chunk_overlap 32, token_counter SFR |

- Breakpoint embeddings: SFR-Embedding-Mistral on the embedding fleet, at most 2 in flight, cached by text
  hash, with the two-pass record-then-embed method of Addendum C.
- Glossary: rules/3, the same as the T and X arms.

## Checks and descriptives
- **Reproduction:** SEMlive against the live chunks L, per page. Report:
  - pages with identical chunk boundaries (start_char and end_char);
  - pages with identical chunk text;
  - the total chunk counts.
- **A reproduction failure is a result, not a deviation.** It means the live ingest used something else: a
  different embed endpoint, distance rounding, or another library version. It is reported as such.
- **Per arm, chunk-size distribution in SFR-Embedding-Mistral tokens:** median, p90, max, the count over
  512, and the count over 256.
- **Bug criterion, fixed now:** if SEMlive emits chunks over 512 tokens while SEM512 emits none, the
  missing cap is confirmed as the cause.

## Contrasts
Gained/lost/net, exact McNemar, the split by stratum, and the part-2 rule:
- SEM512 − SEMlive;
- SEM256 − SEMlive;
- SEMlive − L (expected about 0 if reproduced);
- SEM512 − T512o0;
- SEM256 − T256o0.

Endpoints are b2048, b5120 and reranked@10, on all questions and on the low-echo subset.

## Load
- SFR embeddings for the breakpoint buffers and the new chunks.
- Reranker, at most 4 in flight.
- No LLM, no store writes. The collection's store is read only.
