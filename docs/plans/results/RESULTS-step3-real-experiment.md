# Phase-0 step 3 — the real experiment the step-2 proxy vetoed: **configs DO separate on Leg A**

Run 2026-09-04 as part of the Fable review of `RESULTS-step2-lead-ablation.md`, per the
pre-registration in `PREREG-step3.md` (written before any embedding call). Real pipeline:
SFR-Embedding-Mistral (:9001–:9006, ~200k tok/s aggregate observed, 0 retries),
bge-reranker-v2-m3 (:50052), repo `FixedTokenWindowChunker` + `jats.article_prose`, the
same 4,053-doc / 10-topic CDS pilot set, qrels and metrics as step 2. **No store was
written anywhere** — retrieval is exact brute-force cosine in numpy (fp16 storage, fp32
math); Qdrant :24041 and ES :24043 verified byte-identical before/after; :6333/:9200
never contacted. Fleet cost: ~108M embed tokens ≈ 11 min; ~10k rerank pairs ≈ seconds.

## Verdict against the pre-registered predictions

| prediction (what step 2's demotion logic implies) | outcome |
|---|---|
| **P1** — no pairwise config contrast reaches \|Δ\|≥0.05 on nDCG@10 or recall@100, no CI excludes zero | **FALSIFIED.** tok2048−tok512 nDCG@10 **+0.137** CI [+0.051,+0.225] (8/10 topics); tok2048−tok256 recall@100 **+0.043** CI [+0.015,+0.077] (9/10); grade≥2: tok2048−tok256 recall@100 **+0.077** CI [+0.038,+0.117] (8/10), tok2048−tok512 nDCG **+0.090** CI [+0.036,+0.142] |
| **P2** — plan §8 check 4 fails (<25% of queries change top-10 doc set between extremes) | **FALSIFIED.** **10/10** queries change (top-10 overlaps 2–5 of 10) |
| **P3** — the lead/full null transfers to dense (full−lead < 0.15 R@100 / 0.10 nDCG) | **HOLDS — and inverts.** Dense tok512-full − lead512 recall@100 = **−0.062** CI [−0.084,−0.040]: lead-only *beats* the full 512 index. But **reranked**, full recovers: tok512_rr−lead512_rr nDCG **+0.137** [−0.005,+0.294] (7/10); grade≥2 MRR@10 **+0.299** CI [+0.074,+0.542]. The reranker — the component that reads passages — reverses "lead suffices" at the top of the ranking |
| **P4** — reranking opens no separation | **PARTLY FALSIFIED.** tok256_rr−tok2048_rr nDCG −0.147 CI [−0.319,−0.006]; the full-vs-lead reversal above. (Secondary/descriptive at n=10, as registered) |
| ordering — coarser ≥ finer under max-rollup (step 2's whole>full mechanism) | **HOLDS.** tok2048 > tok512 ≳ tok256 everywhere; whole4096 ≈ tok2048 |

**The falsification bar X = 0.05 (CI excluding zero or ≥7/10 sign consistency) is met by
multiple contrasts.** The load-bearing one is tok2048−tok512 nDCG@10 (+0.137, CI
[+0.051,+0.225], 8/10, replicated at grade≥2 +0.090 CI [+0.036,+0.142]): it meets X on
point estimate, CI and sign simultaneously, and its CI lower bound survives an informal
Holm correction across the six named contrasts. tok256−tok2048 nDCG (−0.105) does *not*
individually clear the CI/sign criterion (CI spans zero, 3/7) — the recall@100 family
carries that pair instead (CIs exclude zero at both grades). The step-2 proxy's headline inference — "a chunking grid on Leg A
would be measuring differences smaller than the lead/full difference, indistinguishable
from zero" — is empirically false on the real pipeline: config contrasts (0.04–0.14) are
*larger* than the BM25 lead/full gap and statistically distinguishable even at n=10.

## Headline table (summary queries, grade≥1, means over 10 topics)

| metric | tok256/32 | tok512/64 (shipping) | tok2048/256 | whole4096 | lead512 |
|---|---|---|---|---|---|
| recall@100 | 0.3403 | 0.3349 | **0.3833** | 0.3811 | 0.3965 |
| nDCG@10 | 0.4952 | 0.4631 | **0.6000** | 0.5684 | 0.5703 |
| MRR@10 | 0.6111 | 0.6750 | **0.8033** | 0.7583 | 0.7417 |

Description-variant queries reproduce the pattern (tok2048 nDCG 0.603 vs tok256 0.431) —
the query-variant sensitivity does not change any call. Dense recall@100 (0.33–0.40) is
roughly double BM25's (0.19–0.26): the SFR retriever is far from the BM25 regime the
proxy measured.

Two honest labels: (i) **`whole4096` is really a head-4000-token arm** — the truncation
margin cuts roughly half the corpus (median doc 4,573 tokens), so it is *not* the dense
analog of step 2's true whole-document control; it is a coarse-granularity control.
(ii) **Reranked numbers rank arms, they do not grade the product** — bge-reranker
sometimes lowers absolute nDCG vs the SFR dense ordering on this clinical set
(tok2048_rr 0.578 < dense 0.600); the informative reranked read is the lead-suffices
*reversal*, which is the brief's "the reranker reads passages" hypothesis confirmed.

## What this means

1. **Document-level qrels demonstrably CAN separate chunking configs.** The step-2
   structural claim ("chunking changes which passage is retrieved; document-level
   judgments cannot see that") is true of passage *choice* but false as a veto: chunk
   granularity changes document *ranking* through score aggregation (max-rollup
   statistics, context per vector), and CDS's judgments see that clearly. Step 2's own
   whole−full = +0.064 was the warning sign.
2. **The direction of the effect is aboutness-shaped.** Coarse units win (tok2048 ≈
   whole ≈ lead > fine). On a topical, document-level set, more context per embedding
   beats precise passages — and lead-only dense (title+abstract+intro, one vector) is as
   good as anything until the reranker runs. Leg A's bias profile is now *measured*:
   it rewards aboutness-carrying configs. Legs B/C (deep-evidence by construction)
   plausibly push the other way — which is exactly the three-leg concordance question
   the plan was built to ask, and it needs Leg A alive to ask it.
3. **Check 3 (lead-chunk insufficiency) genuinely fails on CDS, in BM25 and dense
   retrieval alike** — that part of step 2 stands, and first-stage retrieval on this set
   really is solvable from the lead. But check 3 was the wrong gate for "can Leg A rank
   configs": the pre-registered instrument for that is check 4, which was never run in
   step 2 and passes 10/10 here.
4. **Effect sizes are within Leg A's power at full size.** 0.09–0.14 nDCG@10 contrasts
   are resolvable at 90 topics (plan §6); this is not a "significant at n=10, useless at
   n=90" artifact.

## Recommendation

**Reverse the demotion — don't defer it.** Step 2's *measurements* all stand (check 3
fails on CDS in BM25 and dense alike; the numbers reproduce exactly), but its *inference*
fails: check 3 was never the instrument for "can Leg A rank configs" — check 4 was, it
was never run in step 2, and it passes 10/10 here with contrasts inside 90-topic power.
Keep Leg A as a full leg with its bias profile stated (it rewards aboutness-carrying
coarse configs); do not anchor the study on Leg A *alone* — the coarse-wins direction is
plausibly the aboutness bias itself, and Legs B/C may contradict it, which is the
concordance question the three-leg design exists to ask. The strong structural claim
("document-level qrels cannot see chunking") is falsified, so its corollary — retiring
TREC-COVID as a fallback — is void too. Re-register check 3 as what it measures
(first-stage lead-chunk sufficiency, a *reranker-load* diagnostic) rather than a gate on
the leg.

## Files

```
step3/
  PREREG-step3.md          predictions + bar X, committed before the run
  chunk3.py                3 configs + whole4096 (repo chunker, SFR tokenizer)
  chunks_*.jsonl           126,302 / 63,806 / 17,046 / 4,053 chunks (~108M tokens)
  embed3.py, embed3.log    fleet embedding (0 retries)
  emb_*.npy, rows_*.jsonl  fp16 embeddings + row→(docno,chunk) maps
  score3.py, score3.log    scoring, rerank, metrics, check 4, bootstraps
  runs3.json, rerank3.json, report3.json
```

Deviations noted per pre-registration: 12.5%-overlap extremes instead of check 4's
literal `/0` overlap (second-order); the brief's `STAGE1_CONFIGS` does not exist in repo
main's `chunking_compare_7way.py` (config names here are local).
