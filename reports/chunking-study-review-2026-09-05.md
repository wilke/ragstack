# Review of the September 5 RAGStack chunking study

Reviewed September 5, 2026. Verdict: a useful exploratory engineering study with several good diagnostics, but the current headline conclusions exceed the evidence. It is not yet a confirmatory demonstration of the best production chunking configuration.

I read the [interactive study](https://claude.ai/code/artifact/54a6dc7f-9998-4306-8350-82da9a843e2b), updated this checkout to `55a0fc2`, inspected the current implementation, and inspected the additional evidence on `origin/docs/stage1-and-pilot-findings` at `0a753abe9beb5b4f76fa19794069777e56365f73`. The working-tree README addition and pre-existing untracked files were preserved.

The September grid was run at `d225cea`, not the latest implementation. The evidence branch contains preregistrations and narrative result tables, but its [evidence README](https://github.com/wilke/ragstack/blob/0a753abe9beb5b4f76fa19794069777e56365f73/docs/plans/results/README.md) explicitly says the run scripts and JSON/JSONL artifacts were left outside the repository. I could audit reported arithmetic and code behavior, but could not independently recompute the September confidence intervals or the latest passage and Leg B grid results. No GPU experiments were rerun.

**What the study does well**

- It checks whether the corpus can actually exercise the treatment. Short abstracts provide little information about 1024-versus-2048-token chunking. Moving to full text is justified; this does not imply that every BEIR dataset is unsuitable for every chunking question.
- It measures realized chunk lengths, overlap inflation, and computational cost. Those are necessary manipulation checks, not optional diagnostics.
- It varies overlap as a fraction within the fixed-token family, includes zero overlap and the shipping baseline, and compares configurations on the same queries.
- It uses distinct query constructions: clinical topical retrieval, generated fact lookup, and citation contexts. Disagreement between them exposes the dependence of results on the retrieval task.
- It compares dense and reranked outcomes, probes truncation, and corrects an invalid inference from the BM25 lead-only proxy.
- It distinguishes document retrieval from passage localization and begins comparing fixed context budgets. This is the most useful direction for a RAG evaluation.
- The evidence record includes a named comparison family, paired intervals, some multiplicity correction, code-import assertions, and a repeat run reproducing earlier metrics. These are real strengths even though they do not complete the reproducibility story.

**What the results currently support**

| Claim | Assessment |
|---|---|
| Long-document data are needed for the large-size comparison | Supported for this grid and target workload. |
| Realized size is more informative than the nominal label | Strong descriptive finding; not proof that boundary method has no effect. |
| Zero overlap is a promising cheaper option | Supported as a candidate. Universal equivalence across sizes, tasks, and budgets is not established. |
| The reranker changes the size contrast | Plausible and supported by reported comparisons; residual equivalence and end-to-end benefit require separate tests. |
| Document success can conceal passage failure | Demonstrated as a diagnostic. The one-document experiment cannot estimate the production failure rate. |
| 256 is the best budget-matched configuration | Preliminary; the exact budget, metric, evidence labels, and per-query results must be inspectable. |
| Semantic chunking is generally inferior | Not established. This implementation and parameterization have poor observed cost/quality, but the family was not compared at matched realized sizes. |

**1. Correct the identifiable reporting errors first**

The lead-only-versus-full-index headline attaches the wrong interval to its `+0.137` nDCG effect. The [source record](/Users/me/Development/ragstack/docs/plans/long-doc-judged-set.md:692) explicitly separates two effects that happen to round to the same value:

- Dense **2048 versus 512**: `+0.137`, CI `[+0.051, +0.225]`.
- Reranked **full-512 versus lead-only-512**: `+0.137`, CI `[-0.005, +0.294]`.

The latter interval crosses zero. A different endpoint—grade ≥2 MRR@10—supports the reversal with `+0.299 [0.074, 0.542]`. The report should identify the endpoint rather than transfer an interval between experiments.

The passage table gives top-1 success of **49.5%, 53.3%, 49.5%, and 57.6%**, implying gaps of **50.5, 46.7, 50.5, and 42.4 percentage points**. Its surrounding prose instead says 55–65% success and 28–45-point gaps. If these summarize different populations, name the populations; otherwise update the prose.

The statement that the whole problem is at rank 1 also needs qualification: the harder set reportedly reaches only 21–32% gold-passage recall at rank 20. An easy set's recovery by rank 10 does not explain that result.

The store-check claim is overstated. The source report hashes files containing collection/index listings and counts; equal hashes of those snapshots establish unchanged listings/counts, not byte-identical vector and document contents. Avoid turning an operational check into a stronger content-integrity claim.

**2. Replace “powered null” decision rules with bounded-effect tests**

Detectable-effect calculations are useful for planning. They do not make a nonsignificant result evidence of exact equality. An 80% power target is probabilistic, not a boundary below which detection is impossible. Statements that an experiment could not find an effect “whatever the truth” are incorrect.

There is nevertheless useful bounded evidence in the reported overlap results. For the **overlap-averaged 12.5% versus zero** dense Leg A contrast, the unadjusted 95% interval is `[-0.0472, +0.0072]` nDCG. On that specific aggregate, it bounds any benefit of overlap to below 0.01. That is stronger and more precise than merely saying the test was nonsignificant. It does not establish the same bound separately at every size, behind the reranker, on passage recall, or across query populations.

For removing overlap, predefine an acceptable loss `epsilon`, define `D = quality(no_overlap) - quality(overlap)`, and test whether the appropriate lower confidence bound exceeds `-epsilon`. This is a noninferiority question. For symmetric equivalence, require an interval contained within `[-epsilon, +epsilon]`; the usual TOST procedure at alpha .05 corresponds to a 90% interval before any multiplicity adjustment. Choose the margin from product consequences, not the sample size that happens to be affordable. [Equivalence-testing methodology](https://lakens.github.io/statistical_inferences/09-equivalencetest.html).

The claimed fourfold interaction-power improvement also compares different units. The endpoint interaction spans **256 to 2048, three doublings**; the slope is **per doubling**. Under a linear interaction model, its reported 0.056 detectable effect becomes `3 × 0.056 = 0.168` across the full range, versus 0.213 for the endpoint contrast—about **1.27×**, not 4×. A slope additionally imposes a functional form and can miss nonmonotonic interactions. It is a reasonable alternative estimand, not a free fourfold power gain.

Keep the inferential family consistent with the claims. The [stage-1 preregistration](https://github.com/wilke/ragstack/blob/0a753abe9beb5b4f76fa19794069777e56365f73/docs/plans/results/PREREG-stage1.md) confines formal inference to nine dense nDCG comparisons and labels reranked and other-metric results descriptive. Promoting those later results into decision gates needs an explicit amendment and independent confirmation. Seven agreeing topics out of ten are directional evidence, not a conventional significance test: the two-sided exact sign-test p-value is **0.34375**.

**3. Treat the one-document test as conditional localization**

When the only searchable document is already known to be relevant, document Hit@1 is one by construction. The experiment asks whether retrieval selects the answering part of a known document. It does not measure document discovery in a competitive corpus, nor establish that the deployed system has a 42–51-point document-to-passage gap.

Keep this diagnostic, then repeat passage scoring over the actual multi-document search space. Report separately:

- relevant-document coverage;
- gold-evidence availability in the candidate pool;
- evidence selected after reranking and context packing;
- correctness and citation support of the final answer.

Gold labels should identify minimal sufficient evidence spans or evidence sets, independently of tested chunks. A chunk merely intersecting a large “correct section” may not contain the answer. Include multiple valid locations, overlapping evidence, and questions requiring more than one passage.

A reranker can improve ordering only among the candidates it receives. Low gold-evidence availability in that pool calls for a retrieval or candidate-depth change. Also, retrieving useful evidence somewhere in ten chunks is not proof that an LLM will use it successfully; that requires downstream evaluation. [Controlled evidence-position experiments](https://aclanthology.org/2024.tacl-1.9/) illustrate this distinction.

**4. Define budget matching completely**

Fixed k gives a 2048-token configuration up to eight times the context of a 256-token configuration. Correcting this is essential, but a headline ratio is not enough. Publish exact budgets, packing rules, actual supplied tokens, overlap deduplication, partial final-chunk handling, and the metric numerator/denominator. Count the context budget using the generator's tokenizer; report embedding and reranker token counts separately.

A useful arithmetic check: if the published 396-query table is the relevant population and chunks have their nominal lengths, at a 2048-token budget compare 256-token Hit@8 with 2048-token Hit@1. Monotonicity places the former between Hit@5 = 0.896 and Hit@10 = 0.970; the latter is 0.576. The ratio would therefore be **1.56–1.68**, not 2.2–3.8, at that budget and for that metric. This does not disprove a ratio measured on another budget or metric; it shows why the headline needs its precise definition and raw table.

Use evidence recall versus supplied tokens, answer correctness versus supplied tokens, and latency/storage frontiers. A random-chunk baseline is useful context, but normalized lift alone is not a budget-matched superiority test.

**5. Separate independent evidence from model agreement**

Using structural sections instead of candidate chunk boundaries avoids one kind of circularity. It does not make the production cross-encoder an independent evidence judge. If that model chooses the gold section or decides which examples enter the benchmark, and then reranks the evaluated candidates, part of the result can measure agreement with its own preferences.

Make the source-generated and oracle-selected labels explicit. Audit accepted **and rejected** examples with blinded domain raters; record sufficient answer spans and alternative valid evidence. Report rater identities or roles, whether “hand-read” actually means a human expert or an agent audit, disagreements, adjudication, and uncertainty on label error. The reports' “my read” descriptions do not establish an independent expert annotation process.

The depth filter and rare-entity requirement define a useful stress test but intentionally narrow the query population. Do not call short, topical, or review-oriented needs defective simply because they violate that construction. Citation contexts supply independently authored text, but a citation is not itself an exhaustive relevance judgment: co-citations, background mentions, and critical citations require adjudication.

**6. The production-path comparison remains missing**

The source preregistration uses **exact cosine retrieval over all chunks, max-rollup to documents, then reranks only the dense-winning chunk of each of the top 100 documents**. This is a sensible controlled document-retrieval experiment. It is different from ragstack's [chunk retrieval/fusion](/Users/me/Development/ragstack/python/ragstack/retrieval/retriever.py:173) and [candidate-pool reranking](/Users/me/Development/ragstack/python/ragstack/api/routers/query.py:325), with possible BM25, approximate search, per-document shaping, and context expansion.

Selecting one chunk per document before reranking can hide a document's true answering chunk from the cross-encoder. Conversely, global max-rollup gives documents with many chunks more opportunities to obtain a high similarity. These mechanisms can interact with size and overlap.

Validate the shortlisted configurations through the actual intended serving path. Record dense/BM25/hybrid mode, candidate depth, per-document limits, reranker depth, neighbor expansion, quantization, ANN parameters, and final context budget. Compare ANN with exact search as a diagnostic. Freeze candidate pools when estimating the effect of reranking; the API can otherwise change retrieval depth when reranking is toggled.

The preregistration explicitly embeds queries without an instruction prefix, matching the local production convention. That is a valid as-deployed baseline, but [SFR's model card](https://huggingface.co/Salesforce/SFR-Embedding-Mistral) specifies an instruction for retrieval queries. Include raw versus task-instructed queries as a bounded sensitivity experiment before attributing the dense/reranked difference entirely to chunking.

The 4096-token truncation probe is useful, but “2048 SFR tokens is safe” should be verified on **actual query–chunk pairs under the reranker's tokenizer**, including special tokens. One padded example does not establish the tail behavior of clinical queries and biomedical text. Lower absolute scores below the cutoff show score sensitivity; ranking degradation requires rank or quality evidence.

**7. Method effects are confounded with implementation and length**

Today's commit `55a0fc2` changes word/sentence packing from isolated-unit token sums to joined-text counting. The [harness comment](/Users/me/Development/ragstack/python/scripts/eval/chunking_compare_7way.py:765) explicitly preserves `budget_mode="summed"` to reproduce the earlier grids, while the default is now `"joined"`. The study's constant 0.64 word fill is historical behavior, not a current invariant. Pin this parameter and distinguish configuration identities; rerunning the same old labels on the new default changes the intervention.

The other-method rows also do not share the fixed-window family's effective overlap: their nominal 12.5% is converted to characters and was estimated at about 8.9% actual token overlap. Semantic has no ordinary overlap at all; that parameter applies to its fallback. Consequently the grid is only factorial within the fixed-token family.

Similar semantic medians do not make its four configurations identical. The [source table](https://github.com/wilke/ragstack/blob/0a753abe9beb5b4f76fa19794069777e56365f73/docs/plans/results/tables-stage1-legA.md) reports **34.5 chunks/document at cap 256 versus 14.3 at cap 2048**, with p95 lengths 256 and 2048. The tail and splitting behavior differ substantially even when medians flatten. Report full length distributions and hashes of spans, not just medians.

To isolate boundary placement, tune each method on development data to comparable realized length distributions and overlap. Then compare on held-out queries. Include a source-structure-aware JATS packer and small retrieval chunks with parent/neighbor expansion. The latter separates retrieval granularity from the context supplied to the generator. The existing semantic-pooled and cheaper breakpoint-model implementations are also distinct cost baselines; the measured sevenfold overhead is not a law of semantic chunking.

**8. Strengthen sampling, scaling, and reproducibility**

Ten clinical topics are ten query-level sampling units, regardless of the number of documents or grid cells. Averaging over overlap levels does not create more independent topics. Report per-topic differences and use independent topics for confirmation. For generated or citation data, account for repeated source articles and related queries; bootstrap or model the appropriate clusters. Plan power for the actual primary paired contrast and its margin, rather than the median standard deviation across many unrelated comparisons.

Holdout data must stay outside prompt revision, filtering-rule revision, chunker tuning, and configuration selection. More queries generated by the same biased procedure mainly buy precision for that procedure. Include independently authored needs covering fact lookup, topical browsing, comparison, multi-passage synthesis, unanswerable questions, and cross-boundary evidence.

The distractor ladder is a good design, but unjudged is not synonymous with irrelevant. Pool and judge newly retrieved candidates, especially hard in-domain negatives, and deduplicate papers, versions, source passages, and citations. Repeat nested distractor samples with several seeds. Keep source document identities and text constant across chunking configurations. Explicitly scope recall to the available judgments when exhaustive recall is unknowable. [IR evaluation with incomplete judgments](https://www.nist.gov/publications/retrieval-evaluation-incomplete-information).

Check the cost model using repeated configurations and observed lengths. A run landing 2% from a forecast does not establish 2% forecasting accuracy. Distinguish cold and warm caches, chunking, embedding, indexing, reranking, and answer latency. Report fleet wall time and allocated GPU-hours separately, plus storage including indexes, payloads, and replicas rather than vectors alone.

Archive runnable harnesses, immutable code/model/tokenizer revisions, corpus and query manifests, generation/filter prompts, frozen qrels, chunk spans, candidate rankings, rerank scores, per-query metrics, seeds, environment and hardware manifests, and analysis scripts. Preserve failed/rejected examples and the dated preregistration/amendment history. Generated figures should consume these artifacts directly so their captions cannot drift from the underlying contrast.

**A concrete next experiment**

1. Freeze a development set and an untouched confirmation set, separated by query/topic and source document. Specify whether the deployment optimizes evidence lookup, topical retrieval, or a weighted mixture; also report each population separately.
2. Predefine one primary evidence/answer metric at a realistic token budget and one acceptable quality-loss margin for cost reductions. Retain document nDCG as a secondary endpoint. Define the comparison family before analyzing the holdout.
3. Carry fixed-token 256/512/1024/2048 with zero overlap, plus the shipping 512/64 control. Add matched-size sentence/structure and small-chunk-plus-parent baselines. Keep a focused overlap comparison in the long-document, boundary-straddling, hard-distractor regime rather than declaring that axis universally settled.
4. Run dense and the intended production retrieval mode. Vary candidate depth and reranking using shared first-stage pools where appropriate, and score after actual context packing. Judge evidence independently of the reranker.
5. Confirm on unused clinical topics and a powered independent query set. Recalculate the proposed ~1,500-query budget using the actual paired endpoint, clustering, noninferiority margin, and multiplicity rule. Carry configurations to operational scale only after this decision is defined.
6. Select the cheapest configuration whose held-out interval satisfies the prespecified quality requirement and has no unacceptable failure in critical query strata. If none does, report the unresolved tradeoff rather than infer a universal winner.

I would retain zero overlap as a strong candidate, keep the size question open under matched budgets, prioritize independent passage labels and the serving-path evaluation, and rerun the corrected word/sentence arms before making a corpus-wide rebuild decision.
