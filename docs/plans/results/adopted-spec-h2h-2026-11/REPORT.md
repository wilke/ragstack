# Report: does the adopted chunking beat the OA eval's pick?

Confirmatory head-to-head, run 2026-11-11/12: the chunking the project adopted as its production default (**S**)
against the OA chunking eval's own pick (**ft512o64s**) and the server default (**ft512o64**), on the OA eval's
2,035 decision-set questions, plus the same spec built with the regex sentence segmenter (**S-regex**). This is
the reader's version: hypothesis, short results summary, how it was tested, results, conclusion. The record it
reads from is [RESULTS.md](RESULTS.md) (the decision sentences verbatim, every table, the gates, the ingest record)
and [`artifacts/h2h-report.json`](artifacts/h2h-report.json). How the test data was made is in
[TEST-DATA.md](TEST-DATA.md). Deviations are in [DEVIATIONS.md](DEVIATIONS.md). Unlike those record files, this
report is revised in place, as a paper is, and must read as finished whenever it is read.

## 1. Hypothesis

**The question.** The project adopted a production chunking, S: `sentence` chunks of 512 tokens with no overlap, a
size tolerance of 0.25 (so a chunk may run to 640 tokens before it is split), small pieces under 128 tokens merged
into a neighbour, and section boundaries used as cut points ([`docs/CHUNKING.md`](../../../CHUNKING.md) § *The
adopted default*). It was adopted on two pieces of evidence that never compared it with the strongest alternative:
the OA chunking eval, whose frozen rule picked ft512o64s (512-token windows, 64-token overlap, section cuts) at
+3.5 hit@5 points over the server default, and the reach study, which compared S with plain 512-token windows on
another question set. S's two extra knobs, the tolerance and the merge, had no retrieval evidence of their own. And
every tenant but `dev` runs images without `nltk`, where `sentence` falls back to a regex segmenter, while every
study ran Punkt. So: on the OA eval's own questions, corpus, embedder and reranker, is S at least as good as
ft512o64s, is it better, and does the regex segmenter change the answer?

**The hypotheses as tested** (PREREG §1, §4; setting (a) = hybrid retrieval plus the reranker, the production
setting; hit@5 on the decision set, n = 2,035; paired bootstrap over questions, 10,000 resamples, 95 % two-sided):
- **Gate G1 (drift, not a hypothesis):** the lower bound of ft512o64s − ft512o64 is above 0. The same collections
  and questions as the OA eval's Addendum C, so this re-finds a known +3.49; a failure would mean the server or the
  embeddings had drifted and would void the run, not count against ft512o64s.
- **H1 (non-inferiority):** the lower bound of S − ft512o64s is at or above −2.0 points, the margin the OA eval
  froze, and no stratum (8 journal families, 3 length bands) fails the guard.
- **H2 (superiority):** the lower bound of S − ft512o64s is above 0, tested only if H1 holds.
- **H3 (segmenter):** the lower bound of S-regex − S is at or above −2.0 points and the stratum guard holds; if
  so S may be deployed on any worker, otherwise only where the Punkt backend is asserted at ingest.

**The decision rule, fixed before any S number existed.** H1 fails → deploy ft512o64s. H1 and H2 hold → deploy S.
H1 holds and H2 fails (a tie) → the cheaper arm, and the owner ruled before the freeze that under the cost order
in force (**Order B**: cost by stored chunks and ingest compute, where S ranks before ft512o64s) a tie deploys S,
unless ft512o64s is shown superior to S on the frozen paired ladder. The sentence always states what the other
order (A, where a tie goes to ft512o64s) would have decided, because the analyst had seen every reused arm's
per-question result and a tie was the known prior (PREREG §1, *Declared exposure*).

The test was frozen before the collections were built: PREREG sha256 `31687ee4…6f82`, signed tag
`prereg-adopted-spec-h2h-2026-11` at `f4d4ef9a`, owner's go of 2026-11-11 (cost order B, margin 2.0, server
v1.6.25, tools v1.6.24 with S on build 1 and S-regex on build 2).

## 2. Results summary

**Decision: deploy S.** A tie at H2 under cost order B; no validity gate fired; H3 passes, so S may be deployed on
any worker.
- **Rates.** In the production setting, hit@5 was **82.85 %** under S against **82.51 %** under ft512o64s and
  79.02 % under the server default ft512o64 (`settings.a.hit5`).
- **What one point means here.** The decision set has 2,035 questions, one per article, so one hit@5 point is
  about **20 questions**. S reaches the quote on 1,686 questions and ft512o64s on 1,679. They differ on 107
  questions (5.3 %): S alone on 57, ft512o64s alone on 50. The net 7 questions are **+0.34 points**, with a 95 %
  interval of [−0.64, +1.33], about −13 to +27 questions.
- **Tests.** Non-inferiority at 2.0 points holds (lower bound −0.64 ≥ −2.0; no stratum fails). Superiority does
  not (lower bound below 0), and ft512o64s is not shown superior to S either (ft512o64s − S = −0.34 [−1.38,
  +0.64]). That is a tie; under cost order B it goes to S. **Under cost order A the decision would have been
  ft512o64s.**
- **Gate G1** re-found ft512o64s − ft512o64 = +3.49 [+2.11, +4.86], Addendum C's figure to the printed precision.
- **H3.** S-regex − S = −0.15 [−0.49, +0.20]. The two segmenters produced the same chunk ids for 99.2 % of chunks
  on this corpus, and only 13 of the 2,035 questions change hit status between them. The margin could not have
  failed at that discordance; the overlap carries the finding.
- **Beside the decision.** S stores 10.6 % fewer chunks than ft512o64s (767,984 against 859,280) and returns about
  the same context at k = 5 (chars/4 estimate: 2,016 against 2,000 tokens; the server default 2,316). S − sent512s,
  which isolates what the tolerance and the merge add, is +0.79 [+0.10, +1.47] (Holm-significant, secondary).
  In the three secondary retrieval settings S's advantage over ft512o64s is larger (+1.67, +2.06 and +0.93 points)
  and in two of them superiority holds; all four settings decide S.

The result is about **hit@5 on short, single-section quotes under the OA eval's retrievers**. It does not resolve
whether S is better than ft512o64s in production; it shows S is not worse by 2 points or more, on a powered test.

## 3. How it was tested

- **Questions.** The OA chunking eval's decision set: 2,035 questions, each an LLM-written question with an exact
  answer quote (median 119 characters) from one 1,200-character passage of one article, selected by programmatic
  checks and a gold audit; the clean set minus the 300-question noise subset the eval used to set its margins.
  One question per article. Full chain: [TEST-DATA.md](TEST-DATA.md) §2. The sensitivity set (2,335, noise
  subset included) is reported beside it.
- **Corpus.** 22,593 PMC open-access articles on bacteria and viruses, extracted into 241,930 article, table and
  figure records (16 shards), the same text in every arm; about 20,000 articles hold no question.
- **Arms.** Five collections on the dev server, all with SFR-Embedding-Mistral 4096-d embeddings and the same
  reranker:
  - **ft512o64** (server default) and **ft512o64s** (the OA eval's pick) and **sent512s** (sentence 512 with
    section cuts, no tolerance or merge): reused from the OA eval and its Addendum C, re-retrieved afresh here;
  - **S** and **S-regex**: built for this study from the pinned shards through one study workflow text, on two
    stamped builds of the tools tag v1.6.24 that differ only in whether `nltk` is installed. Every receipt
    carries the image commit, the build number, the segmenter actually probed and the full chunking object;
    every worker's full stderr was staged out and read for the Punkt-fallback warning (0 lines on every S
    shard, ≥ 1 on every S-regex shard). A 40-document sentinel shard went first on both arms and had to
    reproduce the pre-freeze dry run exactly (168 chunks, identical ids); it did.
- **Retrieval.** 48,740 calls: 5 arms × 4 settings × 2,437 questions, `top_k` 10, on one server version
  (v1.6.25, checked on every row). Settings: (a) hybrid + reranker, the decision; (d) hybrid without the reranker;
  (b) vector only; (c) BM25 only.
- **Metric and test.** hit@5: the top-5 chunks from the gold article cover ≥ 90 % of the quote. Paired bootstrap
  over questions (10,000 resamples, seed 0), 95 % two-sided intervals; non-inferiority then superiority on one
  contrast, which needs no multiplicity correction; a Bonferroni stratum guard over 11 strata. Secondary contrasts
  (S − sent512s, S − ft512o64, S-regex − S, ft512o64s − ft512o64) carry a Holm family per setting and decide
  nothing. ctx@5 is chars/4, labelled estimated.
- **Gates.** G1; receipts complete with 0 failed documents and the sentinel at 168; the chunking object, segmenter,
  image commit and build on every receipt; distinct chunk ids = Qdrant = Elasticsearch per new arm; the reused
  arms' census unchanged since the freeze; the 17 shard hashes; worker stderr per shard, with the sentinel's file
  and logs-endpoint tail agreeing; a 200-chunk re-embed of ft512o64s with every cosine ≥ 0.999; the embedding and
  reranker model ids; one server version; the frozen request fields; the decision set by count and id hash; the
  harness's `decision.json` as a cross-check of the estimates (not of the choice); a clean tree at the frozen
  commit. **None fired.**
- **Power.** At the planned discordance of 0.05 (observed 0.053), non-inferiority at 2.0 has power 0.98 if S is
  truly equal to ft512o64s and 0.52 if it is truly 1 point worse; the smallest superiority detectable with power
  0.80 is 1.4 points (`power.json`). A true loss of 2 points would have failed H1 with probability 0.975.
- **Exposure.** The analyst had seen every reused arm's per-question result; the rule and the cost order were fixed
  knowing that prior, and the other order's decision is always printed. No S or S-regex per-question file was read
  before the frozen script wrote its output.

## 4. Results

**The decision cell** (tested; setting (a), n = 2,035; `settings.a`):

| | ft512o64s reaches the quote | ft512o64s does not |
|---|---|---|
| **S reaches the quote** | 1,629 | **57** |
| **S does not** | **50** | 299 |

| quantity | value |
|---|---|
| hit@5, S / ft512o64s / ft512o64 | 82.85 % / 82.51 % / 79.02 % |
| S − ft512o64s | **+0.34 points** [−0.64, +1.33]; discordance 0.053 (107 questions) |
| H1, non-inferiority at 2.0 | holds (lower bound −0.64); stratum guard: 11 strata, none failed |
| H2, superiority | fails (lower bound below 0) |
| ft512o64s − S (the Order B tie step) | −0.34 [−1.38, +0.64]: ft512o64s not shown superior |
| decision | **S** (tie, cost order B); under order A: ft512o64s |
| gate G1, ft512o64s − ft512o64 | +3.49 [+2.11, +4.86] (140 questions for ft512o64s alone, 69 for ft512o64 alone) |
| ctx@5 rule | S / ft512o64 = 0.87, below 1.5; the threshold was not raised |
| gates | none fired |

**H3, the segmenter** (tested; setting (a)):

| quantity | value |
|---|---|
| hit@5, S-regex / S | 82.70 % / 82.85 % |
| S-regex − S | −0.15 points [−0.49, +0.20]; 13 questions differ (5 for S-regex alone, 8 for S alone) |
| margin and stratum guard | hold (none of 11 strata failed) |
| chunk-id overlap | 761,915 ids shared: 99.2 % of S's 767,984 and of S-regex's 767,956 |
| sentence | S may be deployed on any worker (on this corpus) |

The overlap, not the margin, is the finding: at a discordance of 0.0064 the two arms could not differ by more than
0.64 points, so the 2.0-point margin could not fail (PREREG §2, §5 anticipated this collapse). What H3 shows is
that on these 241,930 records, under this spec, with the 640-token ceiling, the merge and the section cuts, the
regex segmenter moves a chunk boundary in under 1 % of chunks and changes the hit status of 13 questions.

**Secondary contrasts in the decision setting** (Holm over the two that are not G1 or H3; `settings.a.contrasts`):

| contrast | point | 95 % interval | Holm p |
|---|---|---|---|
| S − sent512s (what tolerance + merge add) | +0.79 | [+0.10, +1.47] | 0.027, significant |
| S − ft512o64 (S against the server default) | +3.83 | [+2.46, +5.26] | 0.0004, significant |

**The other retrieval settings** (secondary; the decision is setting (a) alone; `settings.d`, `.b`, `.c`):

| setting | S | ft512o64s | ft512o64 | S − ft512o64s | H1 / H2 | decision under B (under A) |
|---|---|---|---|---|---|---|
| (a) hybrid + reranker, the decision | 82.85 | 82.51 | 79.02 | +0.34 [−0.64, +1.33] | holds / fails | S (ft512o64s) |
| (d) hybrid, no reranker | 67.08 | 65.41 | 61.28 | +1.67 [+0.44, +2.95] | holds / holds | S (S) |
| (b) vector only | 59.16 | 57.10 | 49.14 | +2.06 [+0.59, +3.49] | holds / holds | S (S) |
| (c) BM25 only | 56.31 | 55.38 | 55.68 | +0.93 [−0.10, +1.97] | holds / fails | S (ft512o64s) |

hit@5 in %. Without the reranker, S's lead over ft512o64s grows and clears superiority in (d) and (b); the
reranker in (a) narrows every contrast between the vector-using arms (ft512o64s − ft512o64 is +7.96 in (b) and
+3.49 in (a)). In BM25 alone the section cuts do nothing for ft512o64s (−0.29 against ft512o64) while S still
edges ahead. S-regex − S is 0.00, −0.05 and −0.05 points in (d), (b) and (c).

**Other depths and strict containment** (descriptive, setting (a)): hit@1 S 60.5 %, ft512o64s 60.9 %, ft512o64
56.6 %; hit@10 85.7 / 85.5 / 82.7 %; strict single-chunk hit@5 82.7 / 82.5 / 78.8 %. ft512o64s edges S at k = 1 and
S edges ft512o64s at k = 5 and 10; none of these is tested.

**Sensitivity set** (descriptive; n = 2,335, noise subset included): S − ft512o64s = +0.51 [−0.43, +1.41]; G1 +3.04
[+1.76, +4.33]. Same reading as the decision set.

**Cost quantities** (descriptive; the Order B rationale): stored chunks S 767,984, S-regex 767,956, sent512s
850,312, ft512o64s 859,280, ft512o64 705,204. ctx@5 (chars/4, estimated) S 2,016, ft512o64s 2,000, ft512o64 2,316,
sent512s 1,906 tokens. S stores 10.6 % fewer chunks than ft512o64s at the same context per query; the cost
ranking the owner chose before the run is borne out on stored chunks and is not what the decision rests on.

**The harness's own verdict** (a cross-check of estimates, not of the choice): its ft512o64s − ft512o64 interval
equals G1 to 4 decimals in every setting, and every ladder step it took was recomputed and matched. Its paired
ladder chose S in (a), (d) and (b) and kept the baseline in (c), where its ladder tests each challenger against
ft512o64 rather than against ft512o64s; reported, not compared.

## 5. Conclusion

- **What is shown.** On the OA eval's 2,035 questions, under the production retriever, the adopted chunking S is
  not worse than the eval's pick ft512o64s by 2 hit@5 points or more: the test was pre-registered, powered
  (0.98 at equality), and no validity gate fired. S is also not shown better: +0.34 points, about 7 questions of
  2,035, with an interval that includes zero. Under the cost order the owner fixed before the run, that tie
  deploys S; under the other order it would have deployed ft512o64s, and the record says so. The regex
  segmenter does not change the answer on this corpus: 99.2 % of chunk ids are identical and 13 questions move.
- **What is not shown.**
  - **Superiority.** A true advantage of 1.4 points or more would have been seen with power 0.80; the observed
    +0.34 is below that and the test does not resolve it. In the three secondary settings S's lead is larger and
    twice clears superiority, but those settings are not the decision and were not tested for it.
  - **Cross-section or long answers.** Every quote sits inside one section and most inside one sentence; the
    640-token ceiling, the merge and the section cuts were tested only on such quotes (PREREG §3, §8; the
    cross-section supplementary set is a separate pre-registration, owner Q4).
  - **Which knob does what.** S − sent512s (+0.79, Holm-significant, secondary) measures the tolerance and the
    merge jointly.
  - **Answer quality.** hit@5 credits the quote's text in the top 5 from the gold article; no reader or model was
    asked whether the answer is usable.
- **Which way the known biases point.** The passages were cut by the Punkt segmenter, so quote edges follow
  Punkt's sentence boundaries, which favours S over S-regex in H3 (declared; H3 passed anyway and the overlap
  figure does not depend on it). The question set had been used twice and every reused arm's result was known;
  the rule, the margin and the cost order were fixed in writing with that prior declared, and the other order's
  decision is printed. The reused arms were embedded a week earlier; the re-embed gate (minimum cosine
  0.9999995) and the one-version gate held. The ingest path differs between the new and reused arms (the study
  text captures stderr; the server default ran on an older tools image); the sentinel chunk ids reproduced across
  the texts, and the tools-version difference to ft512o64 is not otherwise controlled.
- **What it means in practice.** The adopted default can go to the re-ingest (#811) as adopted: it does not cost
  hit@5 against the eval's pick, it stores about 10 % fewer chunks, and it may run on workers without `nltk`
  (receipts still record the backend). The choice between S and ft512o64s on these questions is a cost
  judgement, not a retrieval one, and the record carries both orders' decisions so that judgement can be
  revisited without re-running the study.
