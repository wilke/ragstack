# Bib inbox 01 — LLM-as-judge for IR relevance assessment

Survey for Paper A (evidence localisation). Same rule as `../bibliography.md`: **every entry
here was fetched before it was listed.** Nothing is from recall. Where a paper reports a large
multiple, a dramatic gain or a very high correlation, this file states what the baseline was.

**Status key.** `verified` = exact title, full author list, venue and year confirmed on the
publisher or arXiv abstract page. `verified, read` = the full text (HTML or PDF, extracted
locally) was retrieved and the methods checked, not just the abstract. Entries that could not
be confirmed against a publisher page are quarantined in the final section and must not be
cited until someone re-fetches them.

**Four fields recorded for every entry**, because they are what Paper A turns on:

- **Granularity** — document/passage-level *judgement* of a candidate that is handed to the
  judge, versus *localisation*: the judge must find where inside a document the evidence sits.
- **Self-reproducibility** — did anyone run the same model on the same item twice and measure
  agreement with itself, as opposed to agreement with humans?
- **Readings / temperature** — how many readings per item, at what temperature.
- **Human agreement** — kappa, correlation, or rank correlation of system orderings.

**Headline finding of the survey.** The prior in the brief is confirmed, with two partial
exceptions. 56 papers are listed below; 53 had title, authors, venue and year confirmed on a
publisher, arXiv or ACL Anthology page, and 3 (`alaofi-2026-tois`, `xue-2026-scoring-consistency`,
`krishna-2024-radiology`) only via a DOI record or a primary source's reference list because the
publisher page refuses automated fetch — each is flagged in place and again at the end.

Every LLM-as-relevance-judge paper in the IR strand works on a candidate that has already been
selected for it — a document or a passage — and asks only *whether*. Two papers ask an LLM
*where*: `saha-2026` in IR (four months old, one reading per item, no self-consistency
measurement) and `kasner-2026-spans` in general NLP (one reading per item, inter-annotator
agreement only). **Not one paper in this file measures an LLM judge's agreement with itself at
span level.** Several measure it at whole-response or whole-item level — `haldar-2025-rating-roulette`,
`schroeder-2024`, `reiss-2023`, `shi-2025-position-bias`, `shyr-2025-repeatability`,
`xue-2026-scoring-consistency`, `krishna-2024-radiology`, `norman-2026-reliability-without-validity` — and they disagree with
each other about whether it is good, which is itself worth saying.

---

## 1. The founding line — LLMs as relevance assessors

### `faggioli-2023` — Perspectives on Large Language Models for Relevance Judgment
Guglielmo Faggioli, Laura Dietz, Charles Clarke, Gianluca Demartini, Matthias Hagen, Claudia
Hauff, Noriko Kando, Evangelos Kanoulas, Martin Potthast, Benno Stein, Henning Wachsmuth.
ICTIR 2023. <https://arxiv.org/abs/2304.09161> · doi:10.1145/3578337.3605136 ·
**verified, read**

The paper that opened the debate. Proposes a human–machine collaboration spectrum and runs a
pilot: GPT-3.5 (`text-davinci-003`) and YouChat over 1,000 topic-document pairs from TREC-8 and
100 question-passage pairs per grade from TREC-DL 2021. Deliberately minimal prompts.

**Granularity:** documents (TREC-8), passages (TREC-DL 2021). No localisation.
**Self-reproducibility:** not measured.
**Readings / temperature:** one reading, temperature 0.
**Human agreement:** Cohen's κ = 0.38 (GPT-3.5, TREC-8), 0.07 (YouChat, TREC-8), 0.40 (GPT-3.5,
DL 2021), 0.49 (YouChat, DL 2021). Leaderboards "similar" but LLM judgments less sensitive —
65% of system pairs distinguished under MAP versus 72% for human judgments.

The dissenting section is the citable part: *"Relevance is subjective, and changes over time for
the same person… it is a big leap of faith to fully trust the model's ability to make correct
assessments"*; *"Currently, there is no proof that the judgments made by LLMs are grounded in
reality."*

**Weight: strong as the framing citation, weak as evidence.** The pilot is small and the prompts
are admittedly unoptimised; Thomas et al. beat it mostly by prompting harder. Cite it for the
spectrum and for the dissent, not for a number.

### `thomas-2024` — Large Language Models can Accurately Predict Searcher Preferences
Paul Thomas, Seth Spielman, Nick Craswell, Bhaskar Mitra. SIGIR 2024, pp. 1930–1940.
<https://arxiv.org/abs/2309.10621> · doi:10.1145/3626772.3657707 · **verified, read (full PDF)**

The Bing paper everything downstream reproduces. In-house GPT-4 over 3,000 query:document pairs
stratified from TREC-Robust, plus deployment data from Bing measured against first-party
searcher preferences rather than third-party labels.

**Granularity:** whole documents. *"Given a query and a web page…"*. No localisation anywhere in
the paper.
**Self-reproducibility:** not measured. One prompt variant asks the model to *"simulate several
judges, generating the output of five simulated judges from one LLM call"* — and the authors
say plainly why this is not repeated reading: *"Since the outputs are generated in sequence they
are not really independent labellers."* It also **hurt**: the `M` feature alone scores κ = 0.22
against the bare baseline's 0.38.
**Readings / temperature:** one reading. Temperature 0, top-p 1, frequency penalty 0.5, presence
penalty 0. These exact settings propagate into UMBRELA and onward.
**Human agreement:** Cohen's κ from 0.20 to 0.64 across 32 prompt-feature variants (Table 1; the
best, `-DNA-`, is κ = 0.64 ± 0.02, document-preference AUC 0.85 ± 0.01 — the running text says
the range is 0.20–0.62, a minor internal inconsistency). System-ranking Kendall τ 0.77
(MAP@100) to 0.86 (P@10) over 110 runs.

**Check the baseline, and the paper does it for you.** It cites Damessie et al. 2017 (κ 0.24–0.52
for crowd workers re-judging 120 TREC-Robust documents, 0.58 in a controlled lab) and computes
κ = 0.52 between two groups of trained human assessors from Cormack et al. 1998. So "as good as
human labellers" means matching a ceiling around 0.52–0.58, not matching truth.

**The most useful result for us is section 4.3.** They generate 42 paraphrases of the single best
prompt — same design, same task, same data, only rewording — and mean κ ranges from 0.50 to
0.72, empirical 95% CI 0.50–0.71 over all bootstraps and paraphrases. Their conclusion:
*"the measured performance of any prompt … should be taken as a single sample from a wider range
of potential performance."* That is an instability result about the instrument, measured across
prompts because nobody measured it across readings.

**Weight: very strong.** The anchor citation for "LLM judges agree with humans on *whether* about
as well as humans agree with each other," and simultaneously the first evidence that the
instrument is less stable than a single number suggests.

### `upadhyay-2024-umbrela` — UMBRELA: UMbrela is the (Open-Source Reproduction of the) Bing RELevance Assessor
Shivani Upadhyay, Ronak Pradeep, Nandan Thakur, Nick Craswell, Jimmy Lin. arXiv 2406.06519,
10 June 2024. <https://arxiv.org/abs/2406.06519> · **verified, read**

Open-source GPT-4o reproduction of `thomas-2024`, run over TREC Deep Learning 2019–2023. The tool
that TREC 2024 RAG then used for its judgments.

**Granularity:** query–passage pairs, 0–3 graded. *"Given a query and a passage, you must provide
a score on an integer scale of 0 to 3."* No localisation.
**Self-reproducibility:** not measured.
**Readings / temperature:** one reading per pair; temperature 0, top-p 1, frequency penalty 0.5,
presence penalty 0 — inherited verbatim from `thomas-2024`.
**Human agreement:** Cohen's κ against NIST on the four-point scale is only **0.3081–0.3730**
across DL 2019–2023. Per-label accuracy: non-relevant ≈ 75%, related ≈ 50%, highly relevant
≈ 30%, perfectly relevant ≈ 45%. Yet system-ranking Kendall τ = 0.8728–0.9435 and Spearman
ρ = 0.9729–0.9923.

**Weight: strong, and the entry that teaches the metric lesson.** The gap between κ ≈ 0.35 and
τ ≈ 0.9 recurs in every paper in this file. High rank correlation is compatible with genuinely
weak per-item agreement, because rank correlation is an aggregate over hundreds of pooled
judgments and errors cancel. Any sentence in our paper that leans on τ must say which it means.

### `upadhyay-2024-initial-look` — A Large-Scale Study of Relevance Assessments with Large Language Models: An Initial Look
Shivani Upadhyay, Ronak Pradeep, Nandan Thakur, Daniel Campos, Nick Craswell, Ian Soboroff,
Hoa Trang Dang, Jimmy Lin. arXiv 2411.08275, 13 Nov 2024; extended as "…Using UMBRELA",
ICTIR 2025, doi:10.1145/3731120.3744605. <https://arxiv.org/abs/2411.08275> · **verified, read**

Four assessment conditions deployed *in situ* on the TREC 2024 RAG Track — fully manual NIST,
UMBRELA alone, and two human-in-the-loop variants — across 77 runs from 19 teams. The claim that
Clarke and Dietz then attacked.

**Granularity:** passages, 0–3. No localisation.
**Self-reproducibility:** not measured. One LLM judgment per pair.
**Readings / temperature:** one reading; no temperature stated in the paper.
**Human agreement:** Kendall τ between UMBRELA-induced and fully-manual system rankings = 0.890
(nDCG@20), 0.944 (nDCG@100), 0.929 (Recall@100). No item-level kappa is reported at all.

**Their own limitations section is the best gap-quote in the whole IR strand:** *"Our analysis is
missing a comparison between two human assessors, since it is well known that humans disagree
with each other in making relevance judgments. Due to budget and time limitations, we were not
able to have the same topics annotated by multiple human assessors independently."* And:
*"Without this, we are missing an important point of reference, as the divergence between human
and LLM-assisted processes needs to be compared to human–human inter-annotator agreement."*

**Weight: strong for the claim, and strong for us.** The flagship large-scale study of LLM
relevance judgment has no repeatability measurement on either side — not human-human, not
model-self. Also note the surprise finding that human-in-the-loop did *not* improve correlation
over the pure-LLM condition.

### `upadhyay-2026-trec2025` — Overview of the TREC 2025 Retrieval Augmented Generation (RAG) Track
Shivani Upadhyay, Nandan Thakur, Ronak Pradeep, Nick Craswell, Daniel Campos, Jimmy Lin.
arXiv 2603.09891, 10 March 2026. <https://arxiv.org/abs/2603.09891> · **verified (abstract)**

Second edition: long multi-sentence narrative queries, MS MARCO V2.1, over 150 submissions,
"a multi-layered evaluation framework encompassing relevance assessment, response completeness,
attribution verification, and agreement analysis."

**Weight: cite for currency only, and re-read before quoting any number.** The abstract is
confirmed; the tables are not extracted here. The phrase "agreement analysis" is promising and
someone should check whether it includes any repeated-reading condition before we claim nobody
does this in 2026.

### `rahmani-2024-llmjudge` — LLMJudge: LLMs for Relevance Judgments
Hossein A. Rahmani, Emine Yilmaz, Nick Craswell, Bhaskar Mitra, Paul Thomas, Charles L. A.
Clarke, Mohammad Aliannejadi, Clemencia Siro, Guglielmo Faggioli. arXiv 2408.08896, 9 Aug 2024
(LLMJudge challenge overview, LLM4Eval @ SIGIR 2024). <https://arxiv.org/abs/2408.08896> ·
**verified**

Task-definition paper for the shared task: can LLMs replace human labellers on TREC 2023 Deep
Learning queries? Passage-level, 0–3. No empirical agreement numbers of its own; no mention of
self-consistency or of span-level judgment.

**Weight: cite as the venue anchor.** The numbers live in `rahmani-2025-judging`.

### `rahmani-2025-judging` — Judging the Judges: A Collection of LLM-Generated Relevance Judgements
Hossein A. Rahmani, Clemencia Siro, Mohammad Aliannejadi, Nick Craswell, Charles L. A. Clarke,
Guglielmo Faggioli, Bhaskar Mitra, Paul Thomas, Emine Yilmaz. arXiv 2502.13908, 19 Feb 2025.
<https://arxiv.org/abs/2502.13908> · **verified**

Releases 42 sets of LLM-generated labels for TREC 2023 DL from eight teams, benchmarked against
NIST qrels.

**Granularity:** passages, 0–3.
**Self-reproducibility:** not measured — this studies variance *across methods and teams*, not
within a model across readings.
**Readings / temperature:** varies by team, not disclosed in aggregate.
**Human agreement:** across the 42 submissions, Kendall τ clusters in 0.85–0.95 with low variance
while Cohen's κ spans roughly 0.06–0.29 with high variance. (Figures from a delegated read of
the tables; the metadata above is confirmed directly. Re-check the exact κ range before quoting
it in the paper.)

**Weight: moderate, and the single best illustration of the κ/τ divergence** — 42 independent
attempts at the same task, all of which look interchangeable by rank correlation and are not
interchangeable per item.

### `rahmani-2024-llm4eval-report` — Report on the 1st Workshop on Large Language Model for Evaluation in Information Retrieval (LLM4Eval 2024) at SIGIR 2024
Hossein A. Rahmani, Clemencia Siro, Mohammad Aliannejadi, Nick Craswell, Charles L. A. Clarke,
Guglielmo Faggioli, Bhaskar Mitra, Paul Thomas, Emine Yilmaz. arXiv 2408.05388, 9 Aug 2024;
ACM SIGIR Forum, doi:10.1145/3722449.3722461. <https://arxiv.org/abs/2408.05388> · **verified**

Workshop report, not a study. **Weight: venue anchor only.**

### `dewan-2026-true` — TRUE: A Reproducible Framework for LLM-Driven Relevance Judgment in Information Retrieval
Mouly Dewan, Jiqun Liu, Chirag Shah. arXiv 2509.25602 (v1 29 Sep 2025, v2 11 Jan 2026); WSDM
2026, doi:10.1145/3773966.3779397. <https://arxiv.org/abs/2509.25602> · **verified**

Rubric-based multi-factor judgment (intent, coverage, specificity, accuracy, usefulness) with
iterative sampling, on TREC DL 2019/2020 and LLMJudge.

**Weight: moderate — and read the title carefully.** "Reproducible" here means *a standardised,
re-runnable workflow*, not *the judge reproduces itself*. Nothing in the abstract measures
repeated readings. This is the closest any IR paper comes to claiming our territory by name
while not measuring it; worth a footnote precisely so a reviewer does not raise it.

### `alaofi-2026-tois` — On the Use of LLMs for Relevance Labelling
Marwah Alaofi, Paul Thomas, Falk Scholer, Mark Sanderson. ACM TOIS 44(4), Article 77, 2026.
doi:10.1145/3788872 · **verified (metadata only — see caveat)**

Passage-level labelling across multiple LLMs; finds high rank correlation with human orderings
but reduced discriminative power, and possible systematic advantage to non-neural systems.

**Caveat on verification:** `dl.acm.org` returns HTTP 403 to automated fetch. The title, authors,
venue and DOI were confirmed from the reference list of `saha-2026`, whose PDF was extracted and
read locally (entry [3] there), and cross-checked against a secondary index. The full text was
not read. **Do not cite a number from this paper without a library-authenticated read.**

**Weight: hold until read.** Promising for the "reduced discriminative power" point that
`otero-2025` makes independently.

### `thakur-2025-freshstack` — FreshStack: Building Realistic Benchmarks for Evaluating Retrieval on Technical Documents
Nandan Thakur, Jimmy Lin, Sam Havens, Michael Carbin, Omar Khattab, Andrew Drozdov.
arXiv 2504.13128 (v1 17 Apr 2025, v2 13 Jun 2025). <https://arxiv.org/abs/2504.13128> ·
**verified**

Automatic benchmark construction over code and technical documentation, using nugget generation
and nugget-level support in place of direct question-document relevance judgment.

**Granularity:** nugget-level support against retrieved documents. The motivation is ours — long,
heterogeneous documents make direct document relevance judgment unreliable — but the nugget is
supplied, not located.
**Weight: moderate, cite for the motivation.** It is independent evidence that practitioners
retreat from document-level judgment when documents get long, which is the premise of Paper A.

---

## 2. TREC RAG — nuggets and support, the nearest neighbours

### `pradeep-2024-autonuggetizer` — Initial Nugget Evaluation Results for the TREC 2024 RAG Track with the AutoNuggetizer Framework
Ronak Pradeep, Nandan Thakur, Shivani Upadhyay, Daniel Campos, Nick Craswell, Jimmy Lin.
arXiv 2411.09607, 14 Nov 2024. <https://arxiv.org/abs/2411.09607> · **verified**

GPT-4o refactor of the 2003 TREC-QA nugget methodology: automatic nugget creation from the
judged pool, then automatic assignment of nuggets to a system's free-text answer. 21 topics,
45 runs.

**Granularity:** the judge must decide whether a nugget is present *anywhere* in an unsegmented
answer — a search over a text, so closer to localisation than anything else in section 1. But
the output is a three-way label per nugget, not a location, and the text searched is the
system's answer, not a source document.
**Self-reproducibility:** not measured.
**Readings / temperature:** single pass, batched; no temperature stated.
**Human agreement:** run-level Kendall τ = 0.783, but per-topic-averaged τ = 0.518 and over all
topic/run pairs τ = 0.324. No human–human agreement on nugget assignment is reported.

**Weight: moderate, and note the degradation.** The same aggregate-versus-item pattern again: the
headline 0.78 becomes 0.32 when you stop averaging away the variance.

### `pradeep-2025-nugget-recall` — The Great Nugget Recall: Automating Fact Extraction and RAG Evaluation with Large Language Models
Ronak Pradeep, Nandan Thakur, Shivani Upadhyay, Daniel Campos, Nick Craswell, Jimmy Lin.
arXiv 2504.15068, 21 Apr 2025; SIGIR 2025, doi:10.1145/3726302.3730090.
<https://arxiv.org/abs/2504.15068> · **verified**

The full version of the above, superseding it. Run-level τ = 0.887 between the most-human and
most-automatic conditions; per-topic τ = 0.490; all topic/run pairs τ = 0.328.

Their stated caution, from a delegated read of the discussion: *"Another gap in our current
understanding is the relationship between inter-annotator agreement and LLM–human differences."*
(Verify this sentence against the PDF before quoting it in the paper.)

**Weight: strong for the nugget line; cite this one rather than the initial report.**

### `thakur-2025-support` — Support Evaluation for the TREC 2024 RAG Track: Comparing Human versus LLM Judges
Nandan Thakur, Ronak Pradeep, Shivani Upadhyay, Daniel Campos, Nick Craswell, Jimmy Lin.
arXiv 2504.15205, 21 Apr 2025; SIGIR 2025 (short). <https://arxiv.org/abs/2504.15205> ·
**verified, read**

**The most important paper in this file after `saha-2026`.** 45 runs, 36 topics: does the cited
passage support the answer sentence? GPT-4o against NIST humans, in a from-scratch condition and
a post-editing condition.

**Granularity:** a *sentence-citation pair*. *"You will be provided with a statement and its
corresponding passage which the statement cites."* Three-way label (full / partial / no
support). The judge is **not** asked to locate the evidence inside the passage. And only the
first cited passage per sentence was judged, by budget-driven design.
**Self-reproducibility:** not measured — explicitly deferred: *"We keep as future work
explorations of consistency: why LLM judges labeled only a few sentences as 'no support', and
similarly, why human judges labeled a majority of sentences as 'no support'."*
**Readings / temperature:** one reading per pair, one passage at a time; temperature not stated.
**Human agreement — the numbers that matter:** exact three-way match between GPT-4o and humans
56% (from scratch), 72% (post-editing). In the unbiased disagreement study: GPT-4o vs human
κ = 0.29 / 0.27; **independent human vs human κ = −0.03 / 0.07**. Run-level τ > 0.79.

**Check the baseline — this is the case study.** The headline is that *"an independent human judge
correlates better with GPT-4o than a human judge."* True, and it sounds like a vindication of
LLM judges. It is at least as much an indictment of the task: two trained humans given the same
sentence and the same passage agree at or below chance. The LLM looks good because the
comparator has collapsed.

**Weight: very strong, and double-edged for us.** It is the strongest published evidence that
fine-grained support judgment is intrinsically unstable — which supports our *where* result —
and simultaneously the strongest argument that the instability is a property of the task rather
than of language models, which is the objection we have to answer.

### `pradeep-2024-ragnarok` — Ragnarök: A Reusable RAG Framework and Baselines for TREC 2024 Retrieval-Augmented Generation Track
Ronak Pradeep, Nandan Thakur, Sahel Sharifymoghaddam, Eric Zhang, Ryan Nguyen, Daniel Campos,
Nick Craswell, Jimmy Lin. arXiv 2406.16828, 24 Jun 2024; ECIR 2025,
doi:10.1007/978-3-031-88708-6_9. <https://arxiv.org/abs/2406.16828> · **verified (metadata,
via delegated fetch)**

Track infrastructure and baselines. Not a judgment-quality study. **Weight: cite only if we need
to describe the track setup.**

---

## 3. Critical, negative and bias work

### `soboroff-2025` — Don't Use LLMs to Make Relevance Judgments
Ian Soboroff. *Information Retrieval Research* 1(1), March 2025, pp. 29–46;
arXiv 2409.15133 (v1 23 Sep 2024, v2 26 Mar 2025). <https://arxiv.org/abs/2409.15133> ·
doi:10.54195/irrj.19625 · **verified**

Position piece from the SIGIR 2024 LLM4Eval keynote. The argument is about what a test collection
is *for*, not about accuracy: *"No system can outperform the evaluation's answer key. When the
answer key is created by a machine learning model or some other mechanical process, we are
saying that the model represents the idea we are aiming for, and we can't measure something
better than the performance of that model."*

**Granularity:** document-level, classic TREC pooling. Not empirical.
**Weight: strong as the ceiling argument, and directly relevant to our framing.** It is the
sharpest statement of why reliability is not validity, and Paper A must answer it: a reproducible
judge is still only a reproducible model.

### `clarke-dietz-2024` — LLM-based relevance assessment still can't replace human relevance assessment
Charles L. A. Clarke, Laura Dietz. arXiv 2412.17156 (v1 22 Dec 2024, v2 6 Jun 2025, v3 19 Jan
2026); EVIA 2025 (NTCIR-18 satellite), doi:10.20736/0002002105.
<https://arxiv.org/abs/2412.17156> · **verified, read**

The direct rebuttal of `upadhyay-2024-initial-look`. Three moves: question whether the evidence
supports the claim; submit a run deliberately built to exploit the automatic metric; simulate
circularity.

**Granularity:** passage-level (TREC RAG). No localisation.
**Self-reproducibility:** not measured.
**Human agreement / the numbers:** the WaterlooClarke `uwc1` run — pooled from 15 preliminary runs
and reranked with GPT-4o judgments — ranked **5th under LLM evaluation and 28th under manual
evaluation**. In the circularity simulation, where every system uses UMBRELA as a final-stage
reranker and UMBRELA then evaluates, Kendall τ against human rankings falls to 0.63 (top 60),
0.44 (top 20), 0.49 (top 15), 0.38 (top 10) and **−0.40 among the top 5** — against 0.84 (top
60) and 0.51 (top 20) for unmodified systems.

**Weight: very strong.** The 5th-vs-28th result is the single most quotable number in the
critical literature. Note the scope: it is an argument about *using* an LLM judge as the
reusable gold for a shared task. Our result is about reliability of an instrument, not about
enthroning one, but a reviewer will cite this at us and the paper should name it first.

### `dietz-2025-tropes` — LLM-Evaluation Tropes: Perspectives on the Validity of LLM-Evaluations
Laura Dietz, Oleg Zendel, Peter Bailey, Charles Clarke, Ellese Cotterill, Jeff Dalton, Faegheh
Hasibi, Mark Sanderson, Nick Craswell. ICTIR 2025, pp. 218–229; arXiv 2504.19076, 27 Apr 2025.
<https://arxiv.org/abs/2504.19076> · doi:10.1145/3731120.3744588 · **verified**

*(The ICTIR camera-ready circulates under the title "Principles and Guidelines for the Use of LLM
Judges"; same nine authors, same venue. Cite the arXiv title with the DOI.)*

A taxonomy of fourteen failure modes — Circularity, LLM-as-Ranker, LLM Narcissism, Loss of
Variety, Ignored Label Correlation, Old Systems, LLM Evolution, Test Set Leak, Self-Training
Collapse, Goodhart-style Overfitting, Adversarial Threats, Rubber-Stamp Effect, Black-box
Labeling, Predictable Secrets — each with a protocol for quantifying it. Reproduces and extends
the `clarke-dietz-2024` circularity numbers as its case study.

**Weight: strong as the checklist.** Paper A should walk its design against this list explicitly;
several tropes (LLM Evolution, Ignored Label Correlation, Rubber-Stamp Effect) are live for a
multi-family reproducibility study and naming them pre-empts the obvious review.

### `otero-2025` — Limitations of Automatic Relevance Assessments with Large Language Models for Fair and Reliable Retrieval Evaluation
David Otero, Javier Parapar, Álvaro Barreiro. SIGIR 2025; arXiv 2411.13212 (v1 20 Nov 2024, v3
15 Apr 2025). <https://arxiv.org/abs/2411.13212> · **verified**

Asks what the high rank correlations conceal. Finds LLM judgments "unfair at ranking
top-performing systems" and producing "an exceedingly high rate of false positives" on pairwise
significance tests relative to human judgments.

**Granularity:** document/passage-level test-collection judgments.
**Self-reproducibility:** not a repeated-judgment study.
**Weight: strong, and the right companion to `upadhyay-2024-umbrela`.** Together they say: τ ≈ 0.9
overall, and the evaluation still fails exactly where a shared task needs it to work.

### `balog-2025` — Rankers, Judges, and Assistants: Towards Understanding the Interplay of LLMs in Information Retrieval Evaluation
Krisztian Balog, Donald Metzler, Zhen Qin. SIGIR 2025; arXiv 2503.19092 (v1 24 Mar 2025, v2
9 Jul 2025). <https://arxiv.org/abs/2503.19092> · **verified, read**

First direct empirical evidence that an LLM judge prefers passages surfaced by an LLM reranker.
BM25 top-100 reranked and judged, TREC-DL 2019/2020, Gemini 1/1.5 judges with the UMBRELA prompt.

**Granularity:** passages. No localisation.
**Self-reproducibility:** not measured. Temperature 0, top-p 1, one reading.
**Human agreement:** graded Cohen's κ 0.139–0.268 (DL19), 0.144–0.230 (DL20); binarised κ
0.337–0.462 (DL19), 0.273–0.370 (DL20). System-ranking Kendall τ across all systems is
essentially noise — 0.033–0.077 (DL19), 0.121–0.143 (DL20) — rising to 0.600 (DL19) and 0.867
(DL20) when restricted to the oracle rankers.
The bias result: human assessment puts the oracle systems above all LLM-based rankers; the LLM
judges *"completely invert this order, ranking all LLM-based rankers as superior."*

**Weight: strong for the self-preference claim; treat the τ numbers carefully.** These are much
lower than the UMBRELA/TREC figures because the system set is constructed (reranked variants of
one first stage), not a diverse TREC pool. That is a *feature* of the experiment, not a
contradiction of UMBRELA — say so if we cite both.

### `alaofi-2024-cafe` — LLMs can be Fooled into Labelling a Document as Relevant (best café near me; this paper is perfectly relevant)
Marwah Alaofi, Paul Thomas, Falk Scholer, Mark Sanderson. SIGIR-AP 2024, pp. 32–41;
arXiv 2501.17969, 29 Jan 2025. <https://arxiv.org/abs/2501.17969> ·
doi:10.1145/3673791.3698431 · **verified**

Adversarial probe: inject query terms and explicit relevance assertions into irrelevant passages
and count mislabels. Proposes gullibility tests as a diagnostic beyond agreement metrics.

**Granularity:** passages, 0–3 and binarised. No localisation.
**Self-reproducibility:** not measured; temperature 0, one reading.
**Human agreement:** binary Cohen's κ ≈ 0.70 (GPT-4o), 0.68 (GPT-4), 0.66 (Llama-3 70B);
Krippendorff α ≈ 0.65/0.64 on the four-point ordinal scale — within the human-human range, while
the same judges are trivially fooled.

**Weight: strong.** The cleanest demonstration that agreement with humans and trustworthiness are
different properties. Relevant to us because the same holds for reproducibility: a judge can be
perfectly self-consistent about a wrong answer.

### `yu-2026-overrating` — When LLM Judges Inflate Scores: Exploring Overrating in Relevance Assessment
Chuting Yu, Hang Li, Guido Zuccon, Joel Mackenzie, Teerapong Leelanupab. SIGIR 2026 (accepted);
arXiv 2602.17170, 19 Feb 2026. <https://arxiv.org/abs/2602.17170> · **verified**

Shows systematic upward bias in relevance scores on TREC-DL, correlated with surface features,
and delivered with high model confidence.

**Granularity:** passages, binary and graded.
**Self-reproducibility:** not measured as resampling. They measure *order sensitivity* instead —
judge the same pair in both presentation orders — and report tie/contradiction rates from ~24%
to over 45% across models.
**Human agreement:** graded κ 0.107–0.235 (DL2019), binary κ 0.163–0.421; pairwise accuracy
without ties up to 93.08%.
**Limitations, verbatim:** *"The experimental setup relies only on TREC DL datasets, which
primarily consist of short queries paired with relatively short passages. Future work will
extend our analysis to investigate how LLM-based relevance judges behave in collections, such as
TREC-8 and TREC Robust04."*

**Weight: strong, and that limitations quote is ours.** They name short passages as the boundary
of what they have tested. Full-length articles are the other side of that boundary.

### `meng-2026-rerankers` — Re-Rankers as Relevance Judges
Chuan Meng, Jiqun Liu, Mohammad Aliannejadi, Fengran Mo, Jeff Dalton, Maarten de Rijke.
arXiv 2601.04455, 8 Jan 2026. <https://arxiv.org/abs/2601.04455> · **verified**

Repurposes eight rerankers as judges on TREC-DL 2019–2023, matching or beating UMBRELA in roughly
40–50% of configurations while showing strong same-family self-preference.

**Granularity:** binary only — *"We focus on generating binary relevance judgments in this work."*
**Self-reproducibility:** not measured; default decoding from original implementations.
**Human agreement:** Cohen's κ e.g. Rank1-7B 0.450 (DL20), monoT5-3B 0.386 (DL19), UMBRELA 0.499
(DL19); Kendall τ e.g. 0.937 (Rank1-14B, MAP@100, DL22) against UMBRELA's 0.905.
*(Numbers from a delegated read; metadata confirmed directly.)*

**Weight: moderate.** Its real contribution to us is Clarke and Dietz's thesis made concrete —
there is no principled line between a reranker and a judge.

### `panickssery-2024` — LLM Evaluators Recognize and Favor Their Own Generations
Arjun Panickssery, Samuel R. Bowman, Shi Feng. NeurIPS 2024; arXiv 2404.13076, 15 Apr 2024.
<https://arxiv.org/abs/2404.13076> · **verified**

Not IR. Summarisation. Included because every circularity paper above cites it for "narcissism".
Shows above-chance self-recognition, a linear relation between self-recognition and
self-preference, and a causal link via fine-tuning.

**Self-reproducibility:** not measured as resampling. They measure *order reversal*: prompting
twice with swapped order flips the preference for GPT-4, GPT-3.5 and Llama at 25%, 58% and 89%
respectively.
**Limitations, verbatim:** *"Despite the use of a diverse set of control tasks, our experiments
can only provide evidence towards the causal hypothesis without fully validating it."*

**Weight: moderate; cite it as the source of the narcissism claim, not as IR evidence.** The
89% order-reversal figure is a useful reminder of how large presentation effects can be — which
is why Paper A seeded presentation order.

### `rahmani-2025-synthetic` — Towards Understanding Bias in Synthetic Data for Evaluation
Hossein A. Rahmani, Varsha Ramineni, Emine Yilmaz, Nick Craswell, Bhaskar Mitra. CIKM 2025;
arXiv 2506.10301, 12 Jun 2025. <https://arxiv.org/abs/2506.10301> · **verified**

Bland-Altman method-comparison analysis of leniency bias in LLM judgments on the 0–3 scale, over
roughly 22,000 judgments. Bias matters for absolute system scores, less for relative comparisons.
**Limitations, verbatim:** *"While encouraging, these findings are based on a single test
collection, and more extensive experiments are needed to confirm whether these trends hold more
generally, especially in different domains or under alternative prompting strategies."*

**Weight: moderate.** The methodological import is the Bland-Altman framing: agreement between
two measurement methods is not a correlation question. Worth borrowing.

### `yang-2026-judge-changes` — When the Judge Changes, So Does the Measurement: Auditing LLM-as-Judge Reliability
Zongyou Yang, Yinghan Hou, Xiaokun Yang. arXiv 2607.08535, 9 Jul 2026.
<https://arxiv.org/abs/2607.08535> · **verified (abstract)**

Judge-replacement as a measurement-validity problem across four datasets, Qwen3 1.7B–32B and
MiniMax releases. Finds judge upgrades are not interchangeable, that stronger judges reduce but
do not remove position and verbosity bias, and — directly relevant — that *"Repeated-sample
juries add little when errors are correlated."*

**Weight: moderate, and that one clause is load-bearing.** It is the strongest published caution
against the assumption that pooling readings buys independent evidence. Paper A pools readings;
the correlated-error objection must be addressed.

---

## 4. Span-level localisation — the thin strand

### `saha-2026` ★ — LLMs as Assessors: Right for the Right Reason?
Sourav Saha, Mandar Mitra, Aditya Dutta. arXiv 2601.08919 (v1 13 Jan 2026, v2 24 Apr 2026).
<https://arxiv.org/abs/2601.08919> · **verified, read (full PDF)**

**The only paper found that asks an LLM to locate evidence inside a document and scores it
against human locations.** Uses INEX 2009 (68 topics, 4,858 relevant documents) and INEX 2010
(52 topics, 5,471 relevant documents) over a 2008 Wikipedia dump, where the *human* assessors
were also instructed to highlight all responsive passages. Models: Llama-3.1-8B-Instruct,
GPT-4.1-mini, GPT-4o-mini. Generated text is mapped back to document substrings by longest
common subsequence, then scored with character-level precision/recall.

**Granularity: localisation.** Explicitly so, and explicitly framed against the document-level
literature.
**Self-reproducibility: not measured.** No repeated reading of any item.
**Readings / temperature:** one reading per item. Llama-3.1-8B at temperature 3.0 for
document-level prediction and 1.0 for explanation extraction (top-k 50, top-p 1.0); GPT models at
temperature 0, top-p 1. Note the asymmetry — the open model runs *hot* and is still read once.
**Human agreement:** micro-level character F1 for the best configuration is 0.6004 / 0.5605
(Llama, INEX 2009 / 2010) and 0.6638 / 0.6736 (GPT-4.1-mini). Macro precision for the weakest
exemplar set is 0.2520 (2009) and 0.2238 (2010), rising to 0.3415 / 0.2853 with their best
exemplar-selection strategy. Recall runs far ahead of precision because the models over-highlight:
Llama marks more than half the document in 80.7% of INEX 2009 cases and 69.6% of INEX 2010 cases.

**Check the baseline — the document-level accuracy figures are not what they look like.** Table 2
reports Llama-3.1-8B at 0.9035 / 0.9375 accuracy, better than both GPT models. Table 3 then
reports the same model at **0.3005** on non-relevant documents ranked 1–100. The two tables are
computed on disjoint, single-class sets, so a model that answers "relevant" to everything scores
near 1.0 on Table 2 by construction. The 0.90 is a yes-bias, and the authors say as much —
*"Llama-3.1-8B may struggle to correctly identify non-relevant documents"*. Do not quote the 0.90.

**Their own limitations, verbatim:** *"All reported results should be interpreted in the context
of inter-annotator agreement; however, only limited agreement information is available for the
INEX collections."* And: *"our preliminary analysis indicates that detailed step-by-step
prompting encourages paraphrasing rather than faithful extraction, an issue we plan to
investigate more systematically in future work."*

**Weight: very strong, and the entry Paper A must engage with directly.** It establishes that
LLM localisation is substantially worse than LLM document judgment, which agrees with our
direction. It does **not** establish anything about the judge against itself: one reading per
item, no repeats, and by their own statement no human-agreement baseline to calibrate against.
Their diagnosis is *accuracy* — "needles in haystacks", over-highlighting; ours is *stability*.
The two are complementary and the paper should say so in the related-work section rather than
let a reviewer discover it.

### `kasner-2026-spans` — LLMs as Span Annotators: A Comparative Study of LLMs and Humans
Zdeněk Kasner, Vilém Zouhar, Patrícia Schmidtová, Ivan Kartáč, Kristýna Onderková, Ondřej
Plátek, Dimitra Gkatzia, Saad Mahamood, Ondřej Dušek, Simone Balloccu. MME workshop @ EACL 2026;
arXiv 2504.08697 (v1 11 Apr 2025, v4 2 Feb 2026). <https://arxiv.org/abs/2504.08697> ·
**verified, read**

The general-NLP counterpart: LLMs versus skilled crowdworkers on three span-annotation tasks
(data-to-text error spotting, translation error identification, propaganda technique detection),
releasing 40k+ span annotations.

**Granularity: span-level**, character offsets with partial credit and γ scores.
**Self-reproducibility: not measured.** Confirmed by direct read — no repeated run of the same
model on the same input. Seed 42, temperature 0 for local models; proprietary models take
neither parameter.
**Human agreement — the numbers are bleak and they are the point.** Human-to-human IAA on
D2T-Eval sits around F1 0.20–0.25 and γ 0.18–0.22. The best LLM (o3-mini) reaches F1 0.23 / γ
0.19. On propaganda, the best LLM manages γ 0.16 against a human IAA of γ 0.31. Manual review
marked 45.3% of crowdworker annotations correct against 49.5% for the LLM. And:
*"Concerningly, the LLM annotations that were marked as correct have only 24% hard
character-level overlap (51% soft) with human annotations."*
**Limitations, verbatim:** *"Our estimates of the upper-bound IAA for each task are difficult to
establish and depend on many factors, such as the chosen annotation categories, their ambiguity,
the annotation guidelines, or the qualification level of human annotators."*

**Weight: very strong.** The 24%-overlap-among-*correct*-annotations sentence is the single best
external statement that "the same judgement" and "the same span" are different objects. This is
the closest published analogue of our *whether*/*where* split, arrived at from inter-annotator
agreement rather than self-agreement.

### `deyoung-2020-eraser` — ERASER: A Benchmark to Evaluate Rationalized NLP Models
Jay DeYoung, Sarthak Jain, Nazneen Fatema Rajani, Eric Lehman, Caiming Xiong, Richard Socher,
Byron C. Wallace. ACL 2020, pp. 4443–4458. <https://aclanthology.org/2020.acl-main.408/> ·
**verified**

Seven datasets with human rationale spans, standardised metrics (IOU-F1 at threshold 0.5,
token-level P/R/F1) plus comprehensiveness and sufficiency.
**Granularity:** span/token sets inside a document — localisation.
**Self-reproducibility:** not applicable (pre-LLM human annotation).
**Human agreement:** token-level κ against majority vote, per dataset: e-SNLI 0.743 ± 0.162,
FEVER 0.854 ± 0.196, Movie Reviews 0.712 ± 0.135, MultiRC 0.728 ± 0.268, BoolQ 0.618 ± 0.194,
CoS-E 0.619 ± 0.308. Evidence Inference — the expert biomedical set — has **no number**:
*"we would expect agreement to be high, but have not collected redundant comprehensive
annotations."* *(Figures from a delegated read; metadata confirmed directly.)*
**Weight: strong for the metric vocabulary.** IOU-F1 at a threshold is the established way to
score a span set against a reference, and Paper A should say how its span-set agreement relates
to it. Note the missing number for the one expert-annotated biomedical dataset — the gap is old.

### `wadden-2020-scifact` — Fact or Fiction: Verifying Scientific Claims
David Wadden, Shanchuan Lin, Kyle Lo, Lucy Lu Wang, Madeleine van Zuylen, Arman Cohan,
Hannaneh Hajishirzi. EMNLP 2020, pp. 7534–7550.
<https://aclanthology.org/2020.emnlp-main.609/> · **verified**

1,409 expert biomedical claims against 5,183 abstracts, with sentence-level rationale annotation.
Best baseline sentence-selection+label F1 = 60.6 with the oracle abstract given, 39.5 in open
retrieval.
**Granularity:** sentence selection within an abstract — localisation, in our domain.
**Human agreement:** label κ = 0.75, evidence-sentence κ = 0.71. *(Delegated read.)*
**Weight: moderate-strong as the biomedical precedent.** The closest existing dataset to our
setting; note it works on abstracts, not full text, which is precisely the limitation Paper A
targets.

### `yang-2018-hotpotqa` — HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering
Zhilin Yang, Peng Qi, Saizheng Zhang, Yoshua Bengio, William W. Cohen, Ruslan Salakhutdinov,
Christopher D. Manning. EMNLP 2018, pp. 2369–2380. <https://arxiv.org/abs/1809.09600> ·
<https://aclanthology.org/D18-1259/> · **verified, read (full PDF)**

**The cleanest pre-existing *whether*-versus-*where* number in the literature, and it is human.**
1,000 dev/test items re-answered by additional crowd workers, scoring the original worker as a
prediction. Table 8: Human answer EM **83.60** / F1 91.40 against supporting-fact EM **61.50** /
F1 90.04. Human upper bound (best-matching worker): answer EM 96.80 against supporting-fact EM
87.40.

Their reading, verbatim: *"We also note that crowd workers agree less on supporting facts, which
could reflect that this task is inherently more subjective than answering the question."*

**Weight: very strong — and it complicates us as much as it helps.** It is independent
confirmation that agreement on the answer exceeds agreement on the evidence location, in humans,
seven years before our result. It also shows the structure of the gap: EM collapses (83.6 → 61.5)
while F1 barely moves (91.4 → 90.0), i.e. the disagreement is about *set membership at the
margins*, not about being in the wrong part of the document. Paper A should report the graded
analogue alongside the set analogue for exactly this reason, and cite this when it does.

### `thorne-2018-fever` — FEVER: a Large-scale Dataset for Fact Extraction and VERification
James Thorne, Andreas Vlachos, Christos Christodoulopoulos, Arpit Mittal. NAACL-HLT 2018,
pp. 809–819. <https://aclanthology.org/N18-1074/> · **verified**

185,445 claims over ~50,000 Wikipedia pages with sentence-level evidence; 31.75% of claims need
more than one evidence sentence, 16.82% need composition across sentences, 12.15% across pages.
**Human agreement:** claim-label five-way Fleiss κ = 0.6841. Against exhaustive "super-annotator"
re-annotation of 1% of the data, regular annotators achieve evidence precision 95.42% and
**recall 72.36%**. *(Delegated read; metadata confirmed directly.)*
**Weight: strong.** The precision/recall asymmetry is the same shape as our result and as
`saha-2026`'s: what annotators mark is usually right, what they fail to mark is the problem.
The reported observation that *"most of the examples that were annotated incorrectly were cases
where the label was correct, but the evidence selected was not sufficient"* is worth verifying
against the PDF and quoting if it holds.

### `zaidan-2007-rationales` — Using "Annotator Rationales" to Improve Machine Learning for Text Categorization
Omar F. Zaidan, Jason Eisner, Christine D. Piatko. NAACL-HLT 2007, pp. 260–267.
<https://aclanthology.org/N07-1033/> · **verified (delegated read)**

The original rationale-annotation study: 1,800 movie reviews, 8.55 free-form rationale spans per
document, four annotators on a 150-document agreement subset. Pairwise span-overlap recall runs
63.0–80.1% between annotator pairs; class-label four-way agreement 89% of documents.
**Weight: moderate.** Same shape again — near-total agreement on the label, two-thirds to
four-fifths on the spans — from 2007, which is useful for arguing this is a property of evidence
annotation rather than of language models.

---

## 5. Attribution and support — where the judge is handed the passage

*This whole subsection exists to make one point: the attribution literature, which looks at first
glance like it is about locating evidence, is not. In every case below the candidate passage is
supplied and the judge returns a label.*

### `rashkin-2023-ais` — Measuring Attribution in Natural Language Generation Models
Hannah Rashkin, Vitaly Nikolaev, Matthew Lamm, Lora Aroyo, Michael Collins, Dipanjan Das,
Slav Petrov, Gaurav Singh Tomar, Iulia Turc, David Reitter. *Computational Linguistics* 49(4),
2023, pp. 777–840. <https://aclanthology.org/2023.cl-4.2/> · **verified**

Defines the AIS protocol: two binary questions (Interpretability, then Attribution) over a
supplied source passage. Validated over conversational QA, summarisation and table-to-text with
thousands of judgments per task.
**Granularity: judge, not locate.** The passage *"has already been retrieved."*
**Human agreement:** crowd-crowd Krippendorff α — CNN/DM interpretability .46 / AIS .69; QReCC
interpretability .91 / AIS .76; WoW AIS .79; ToTTo AIS .74. Crowd-versus-expert on CNN/DM
interpretability is α = −.04. *(Delegated read.)*
**Weight: strong as the definition of the task we are *not* doing**, and the crowd/expert α of
−0.04 is a second instance of the Thakur collapse.

### `bohnet-2023-attributed-qa` — Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models
Bernd Bohnet, Vinh Q. Tran, Pat Verga, Roee Aharoni, Daniel Andor, Livio Baldini Soares,
Massimiliano Ciaramita, Jacob Eisenstein, Kuzman Ganchev, Jonathan Herzig, Kai Hui,
Tom Kwiatkowski, Ji Ma, Jianmo Ni, Lierni Sestorain Saralegui, Tal Schuster, William W. Cohen,
Michael Collins, Dipanjan Das, Donald Metzler, Slav Petrov, Kellie Webster. arXiv 2212.08037
(v1 15 Dec 2022, v2 10 Feb 2023). <https://arxiv.org/abs/2212.08037> · **verified**

Formalises (answer, cited-paragraph) attribution over Natural Questions, and introduces AutoAIS
(an 11B T5 NLI model) as an automatic proxy.
**Granularity: judge, not locate.**
**Check the baseline:** system-level AIS↔AutoAIS Pearson r = 0.96, described as "remarkably
strong" — but the paper itself flags instance-level correlation as much lower and more variable.
Another aggregate-versus-item artefact. *(Delegated read.)*
**Weight: moderate.** Cite the r = 0.96 only with its instance-level caveat attached.

### `gao-2023-alce` — Enabling Large Language Models to Generate Text with Citations
Tianyu Gao, Howard Yen, Jiatong Yu, Danqi Chen. EMNLP 2023, pp. 6465–6488.
<https://aclanthology.org/2023.emnlp-main.398/> · **verified**

ALCE: citation recall and precision computed by an NLI model over the passages the system itself
cited.
**Granularity: judge, not locate.**
**Human agreement:** Cohen's κ 0.698 (recall), 0.525 (precision); metric-versus-human accuracy
85.1% / 77.6%. Their own limitation: *"the NLI model cannot detect the case of 'partially
support'."* *(Delegated read.)*
**Weight: moderate.** The partial-support blind spot is the same axis our graded score sits on.

### `yue-2023-attrscore` — Automatic Evaluation of Attribution by Large Language Models
Xiang Yue, Boshi Wang, Ziru Chen, Kai Zhang, Yu Su, Huan Sun. Findings of EMNLP 2023,
pp. 4615–4635. <https://aclanthology.org/2023.findings-emnlp.307/> · **verified**

Three-way attribution judgment (attributable / extrapolatory / contradictory) on simulated and
real New Bing outputs. Zero-shot GPT-4 F1 = 55.6 (simulation) / 85.1 (GenSearch).
**Granularity: judge, not locate** — *"our task setting prioritizes one reference per statement."*
**Readings / temperature:** temperature 0, averaged over four prompt phrasings — prompt variation
again standing in for reading variation. *(Delegated read.)*
**Weight: moderate.** Note no formal inter-annotator agreement on GenSearch: single-annotator
gold, spot-checked at 84% on 50 examples.

### `min-2023-factscore` — FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation
Sewon Min, Kalpesh Krishna, Xinxi Lyu, Mike Lewis, Wen-tau Yih, Pang Koh, Mohit Iyyer,
Luke Zettlemoyer, Hannaneh Hajishirzi. EMNLP 2023, pp. 12076–12100.
<https://aclanthology.org/2023.emnlp-main.741/> · **verified**

Decomposes generations into atomic facts and scores support against the subject's whole Wikipedia
page — so the annotator, and the automatic estimator, must *search*.
**Granularity: search, then judge.** Closer to us than the rest of this subsection.
**Human agreement:** two annotators on 10% of data, 91% agreement. Error taxonomy: different
interpretations 21%, subjective 21%, annotation mistakes 21%, inferred-not-stated 16%, depends
on strictness 11%, Wikipedia inconsistent 5%.
**The quote that belongs in our introduction:** *"70% of the errors are due to retrieved passages
not providing direct evidence… validating facts often requires reading the entire page rather
than a single passage."* *(Delegated read; metadata confirmed directly.)*
**Weight: strong.** Independent evidence that the retrieval-a-passage framing loses most of the
evidence in a long document — the motivation for Paper A's setting.

### `gao-2023-rarr` — RARR: Researching and Revising What Language Models Say, Using Language Models
Luyu Gao, Zhuyun Dai, Panupong Pasupat, Anthony Chen, Arun Tejasvi Chaganty, Yicheng Fan,
Vincent Y. Zhao, Ni Lao, Hongrae Lee, Da-Cheng Juan, Kelvin Guu. ACL 2023, pp. 16477–16508.
<https://aclanthology.org/2023.acl-long.910/> · **verified (delegated read)**

Post-hoc research-and-revise with a sentence-level AIS metric. Reports three-run standard
deviations at the *system* level (Attr_auto 1.2, PresLev 0.5, F1_AP 1.0) — the closest thing in
the attribution literature to a repeated-run measurement, and it is aggregate, not per item.
Describes a three-annotator IAA pilot but reports no coefficient.
**Weight: weak-moderate.** Cite only for the three-run system-level variance as a contrast with
per-item variance.

---

## 6. Human assessors disagreeing about *where* — the pre-LLM baseline

### `voorhees-2000` — Variations in Relevance Judgments and the Measurement of Retrieval Effectiveness
Ellen M. Voorhees. *Information Processing and Management* 36(5), 2000 (pp. 697–716 per dblp).
<https://www.nist.gov/publications/variations-relevance-judgments-and-measurement-retrieval-effectiveness>
· **verified (NIST record; dblp page returned an access-denied interstitial, so treat the page
range as secondary)**

The founding result of Cranfield practice: assessors disagree substantially on individual
relevance decisions and system rankings barely move. `thomas-2024` quotes τ = 0.94 across runs
from this line as the human-human upper bound.
**Weight: very strong as framing.** It is the reason the field tolerates κ ≈ 0.35 judges, and the
reason a *where* result needs to be argued on different grounds than a *whether* result: nobody
has shown that span-level disagreement washes out the same way.

### `parry-2025-shelf-life` — Variations in Relevance Judgments and the Shelf Life of Test Collections
Andrew Parry, Maik Fröbe, Harrisen Scells, Ferdinand Schlatt, Guglielmo Faggioli, Saber Zerhoudi,
Sean MacAvaney, Eugene Yang. SIGIR 2025; arXiv 2502.20937 (v1 28 Feb 2025, v2 21 May 2025).
<https://arxiv.org/abs/2502.20937> · **verified**

Re-annotates TREC Deep Learning 2019 and reproduces Voorhees in the neural setting: assessor
disagreement still does not affect system rankings. But some models degrade substantially under
the new judgments and some have reached human ranker effectiveness, so *"test collections can
expire."*
**Weight: strong.** The modern citation for "disagreement doesn't move rankings", which our paper
needs in order to explain why it is asking a different question.

### `soboroff-2003-novelty` — Overview of the TREC 2003 Novelty Track
Ian Soboroff, Donna Harman. TREC 2003, NIST SP 500-255.
<https://trec.nist.gov/pubs/trec12/papers/NOVELTY.OVERVIEW.pdf> · **verified, read (full PDF)**

Fifty new topics, 25 relevant documents each, split into sentences; systems return relevant and
novel sentences. Two assessors per topic.
**The number:** scoring the secondary assessor as if it were a system against the primary gives
relevant-sentence **F = 0.58** (P = 0.69, R = 0.67) and novel-sentence **F = 0.46** (P = 0.56,
R = 0.54).
And a distinction worth borrowing: the paper notes there was *"not a large difference between the
primary and secondary assessor in terms of the number of relevant and novel sentences selected"*
— they picked similar *amounts*, and still overlapped at F = 0.58. Volume agreement is not
location agreement.
**Weight: very strong, and it is old.** Two trained NIST assessors, the same topic, the same
documents, sentence-level: 0.58. This is the human floor Paper A's 0.47 span-set reproduction
should be read against, and the paper is weaker if it does not report it.

### `allan-2003-hard` — HARD Track Overview in TREC 2003: High Accuracy Retrieval from Documents
James Allan. TREC 2003, NIST SP 500-255.
<https://trec.nist.gov/pubs/trec12/papers/HARD.OVERVIEW.pdf> · **verified, read (full PDF)**

Ad-hoc retrieval with passage-level judging (byte offset plus length); LDC assessors judged
42,016 documents over 50 topics. No numeric passage-level agreement is reported.
**The quote:** *"Passage-level judging was a terrifically difficult task for the LDC and needs to
be revisited."*
**Weight: moderate as evidence, strong as rhetoric.** Twenty-three years before our result, the
organisers of the only large TREC effort at sub-document gold said the task defeated professional
annotators and left it there.

### `lee-sun-2019-pico` — A Study on Agreement in PICO Span Annotations
Grace E. Lee, Aixin Sun. SIGIR 2019 (short). <https://arxiv.org/abs/1904.09557> ·
**verified, read (full PDF)**

EBM-PICO: 5,000 abstracts annotated by MTurk workers, 200 of them additionally by medical
experts. Pairwise F1 within each group, under exact-boundary agreement and two relaxed variants
(one-side boundary, token overlap).
**The numbers, expert group, 200 documents:** exact span F1 — Participants 0.395, Interventions
0.576, Outcomes 0.357. One-side boundary — 0.680 / 0.732 / 0.654. Token overlap — 0.737 / 0.758 /
0.713. MTurk workers are far worse: exact F1 0.187 / 0.093 / 0.064 on the full 5,000.
**Their two observations, verbatim in substance:** *"Boundaries of PICO span annotations by
individual human annotators are very diverse"* and *"Despite the disagreement in span boundaries,
general areas of the span annotations are broadly agreed by annotators."*
**Weight: very strong, and it is the closest human analogue of our own two numbers.** Domain
experts, biomedical text, the same passages: exact span sets reproduce at 0.36–0.58, relaxed
overlap at 0.65–0.76. That is the same shape as a set measure collapsing while a graded or
relaxed measure holds. Paper A is stronger if it presents this as the human precedent rather than
letting a reviewer supply it.

---

## 7. Self-consistency and test-retest of LLM judges

### `haldar-2025-rating-roulette` ★ — Rating Roulette: Self-Inconsistency in LLM-As-A-Judge Frameworks
Rajarshi Haldar, Julia Hockenmaier. EMNLP 2025; arXiv 2510.27106, 31 Oct 2025.
<https://arxiv.org/abs/2510.27106> · **verified, read**

**The one paper that defines our construct by name.** Verbatim: *"We define self-reliability as
the agreement of a judge with itself over multiple runs with the same settings (prompt and
hyperparameters in case of LLMs)."*

Three benchmarks (SummaC binary factuality, SummEval Likert, MT-Bench three-way ranking), three
judges (Llama-3.1, DeepSeek-R1, Qwen-3), Krippendorff α across runs.

**Granularity: whole response.** Confirmed by direct read — no span or sub-response analysis
anywhere.
**Self-reproducibility — the numbers:** SummaC binary α = 0.3263 (Llama-3.1), 0.6278 (DeepSeek-R1),
0.7883 (Qwen-3). MT-Bench three-way ranking α = 0.265 / 0.507 / 0.563. SummEval "fluency" is
very low for every model regardless of scale. Their summary: judgments are *"almost arbitrary in
the worst case."*
**Readings / temperature:** three runs per item in the main results, up to ten tested with no
significant difference; temperature 0.6, top-p 0.9 (0.95 for Qwen-3). Greedy decoding was tested
separately and *degraded* performance.
**Recommendations:** account for self-reliability explicitly; take a majority vote across runs;
and — pointed, for us — *"Collect Data on Self-Reliability of Human Judges."*

**Weight: very strong, and it is the paper our contribution sits next to.** Same construct, same
motivation, one level of granularity coarser and outside IR. Paper A should cite it as prior art
for the *method* and claim novelty only for span-level localisation and for the IR relevance
setting. Anything vaguer than that will read as an overclaim.

### `schroeder-2024-trust` — Can You Trust LLM Judgments? Reliability of LLM-as-a-Judge
Kayla Schroeder, Zach Wood-Doughty. arXiv 2412.12509 (v1 17 Dec 2024, v2 18 Feb 2025).
<https://arxiv.org/abs/2412.12509> · **verified**

McDonald's ω reliability framework applied to LLM judges picking the best of five candidate
responses.
**Self-reproducibility:** ω ranges 0.421–0.803 across Starling-LM-7B-beta, Llama-3-8B-Instruct and
Gemma-1.1-7b-it — well below the ≥0.9 psychometric convention — rising to 0.989–0.990 only in an
easier binary condition with ground truth supplied. At temperature 0, ω = 1.0 trivially.
**Readings / temperature:** 100 repeated judgments per item; temperatures 1.0, 0.75, 0.5, 0.25
and ~0. *(Delegated read; metadata confirmed directly.)*
**Weight: strong, and the methodological precedent for the reliability-coefficient framing.**
Note the trivial-ω-at-zero point: Paper A runs at temperature 0 and does *not* get 1.0, which is
a result in itself and needs stating against this paper.

### `norman-2026-reliability-without-validity` ★ — Reliability without Validity: A Systematic, Large-Scale Evaluation of LLM-as-a-Judge Models Across Agreement, Consistency, and Bias
Justin D. Norman, Michael U. Rivera, D. Alex Hughes. arXiv 2606.19544, 17 Jun 2026.
<https://arxiv.org/abs/2606.19544> · **verified (abstract)**

21 judges from nine providers across MT-Bench, JudgeBench and RewardBench; 118 runs, ~541,000
judgments, three protocols (agreement, consistency, bias audit). Four findings, verbatim:
*"kappa deflation between exact match and Cohen's kappa is universal (33–41 pp on MT-Bench),
judge rankings shift by up to 14 positions across benchmarks, high test–retest reliability
(>0.95) coexists with severe position bias (>0.10) in two production-deployed judges
(instantiating a consistency–bias paradox), and verbosity bias is small (<0.011)."*

**Weight: very strong, and the sharpest threat to our framing.** The title is the objection. A
judge can be near-perfectly reproducible and still systematically wrong, and they demonstrate it
at scale. Paper A already disclaims validity; after this paper the disclaimer has to be in the
abstract, not the limitations. Get the full text and read the consistency protocol before
drafting section 5.

### `reiss-2023` — Testing the Reliability of ChatGPT for Text Annotation and Classification: A Cautionary Remark
Michael V. Reiss. arXiv 2304.11085, 17 Apr 2023. <https://arxiv.org/abs/2304.11085> ·
**verified**

Binary news/not-news classification of 234 website texts, repeated.
**Self-reproducibility:** Krippendorff α > 0.9 for identical repeats at temperature 0.25; at
temperature 1.0, α = 0.75 unpooled, 0.88 pooling three repeats, 0.91 pooling ten. Prompt-wording
changes are far more damaging: α < 0.6 unpooled, mean α = 0.43 across 45 instruction pairs.
**Readings / temperature:** ten identical repeats per condition; T = 0.25 and 1.0. *(Delegated
read; metadata confirmed directly.)*
**Weight: strong.** The clearest demonstration that pooling repeats recovers reliability — an
empirical precedent for the pooling Paper A does — and, again, that prompt wording dominates
sampling noise.

### `barrie-2024-pss` — Prompt Stability Scoring for Text Annotation with Large Language Models
Christopher Barrie, Elli Palaiologou, Petter Törnberg. arXiv 2407.02039, 2 Jul 2024.
<https://arxiv.org/abs/2407.02039> · **verified**

Adapts intra- and inter-coder reliability to prompt-paraphrase variation; ~3.1M annotations across
six datasets. Intra-PSS (same prompt repeated) generally above 0.8; inter-PSS (paraphrased
prompts) frequently below it. *(Delegated read.)*
**Weight: moderate.** Useful as the formalisation of what `thomas-2024` observed informally, and
as the name for the axis Paper A holds fixed.

### `shi-2025-position-bias` — Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge
Lin Shi, Chiyu Ma, Wenhua Liang, Xingjian Diao, Weicheng Ma, Soroush Vosoughi. AACL-IJCNLP 2025;
arXiv 2406.07791, 12 Jun 2024. <https://arxiv.org/abs/2406.07791> · **verified**

Defines repetition stability, position consistency and preference fairness over >150,000
instances and 15 judges.
**Self-reproducibility:** repetition stability 1.00 ± 0.02 (GPT-4o), 0.97 ± 0.05 (GPT-4),
0.96 ± 0.07 (Claude-3.5-Sonnet), 0.89 ± 0.18 (Claude-3-Haiku); DevBench range 0.79–0.99.
Three trials per pair. *(Delegated read; metadata confirmed directly.)*
**Weight: moderate, and it cuts against us.** For strong judges on a pairwise task, repetition
stability is near 1.0. Their reason for measuring it is telling: self-consistency is treated as a
*precondition check* before bias analysis, not as a finding. That is the received view Paper A
is arguing against, so quote it.

### `abercrombie-2025-consistency` — Consistency is Key: Disentangling Label Variation in Natural Language Processing with Intra-Annotator Agreement
Gavin Abercrombie, Tanvi Dinkar, Amanda Cercas Curry, Verena Rieser, Dirk Hovy. 4th Workshop on
Perspectivist Approaches to NLP, 2025, pp. 63–74.
<https://aclanthology.org/2025.nlperspectives-1.6/> · **verified, read (full PDF)**

Human annotators only, no LLMs — and it is the source of the field-level gap claim, verbatim:
*"While inter-annotator agreement is frequently used in NLP to determine the reliability of labels
or the processes used to produce them, intra-annotator agreement is rarely, if ever, reported."*
And: *"reporting intra-annotator agreement is so far extremely uncommon in NLP, as we show in a
systematic review."*
**The number:** annotators *"provide inconsistent responses around 25% of the time across four
different NLP tasks."*
**One sentence that we contradict, and must therefore quote:** *"We do not foresee many situations
where reliability is high yet stability/consistency is low."*
**Weight: very strong.** The systematic-review claim is the cleanest external evidence that
nobody measures the thing, and the last quote is a named prediction our result speaks to.

### `shyr-2025-repeatability` — A statistical framework for evaluating the repeatability and reproducibility of large language models
Cathy Shyr, Boyu Ren, Chih-Yuan Hsu, Rory J. Tinker, Thomas A. Cassini, Rizwan Hamid,
Adam Wright, Lisa Bastarache, Josh F. Peterson, Bradley A. Malin, Hua Xu. medRxiv
2025.08.06.25333170, 2025; PMC12637745. <https://pmc.ncbi.nlm.nih.gov/articles/PMC12637745/> ·
**verified (delegated read)**

Separates repeatability (same conditions) from reproducibility (different conditions), each
measured semantically (embedding cosine) and internally (token-probability entropy). 518 USMLE
questions and 90 rare-disease cases, 912,000 generations, R = 100 repeats per combination at
temperature 0.5, top-k 30.
**The finding that matters to us:** repeatability and reproducibility did **not** correlate with
diagnostic accuracy, and the more ambiguous real-world cases were *less* variable than the
structured exam questions.
**Weight: strong.** Second independent statement that stability and correctness are orthogonal —
reinforcing `norman-2026-reliability-without-validity`. Also the largest repeat count in this file, which makes it a useful
precedent for our reading budget.

### `xue-2026-scoring-consistency` — On the Consistency of Automatic Scoring with Large Language Models
Mingfeng Xue, Xingyao Xiao, Yunting Liu, Mark Wilson. *Educational and Psychological
Measurement*, 2026. doi:10.1177/00131644261418138 · **verified (metadata via OpenAlex DOI record;
publisher page and PubMed both cookie-gated to automated fetch — re-confirm before citing)**

Intra- and inter-LLM consistency scoring constructed responses (ASAP) across five model families
and several temperatures. Reports "almost perfect" intra-LLM consistency regardless of
temperature, with intra-LLM consistency **not** correlating with accuracy, while inter-LLM
consistency was only moderate and did correlate.
**Weight: moderate, and it contradicts us if taken at face value.** The strongest published claim
of high LLM self-consistency. Note the task: short constructed responses against a rubric —
whole-item scoring, no localisation, short texts. Read it before the paper goes out.

### `krishna-2024-radiology` — Evaluation of Reliability, Repeatability, Robustness, and Confidence of GPT-3.5 and GPT-4 on a Radiology Board–style Examination
Satheesh Krishna, Nishaant Bhambra, Robert Bleakney, Rajesh Bhayana. *Radiology* 311(2),
e232715, May 2024. doi:10.1148/radiol.232715 · **verified (metadata via Semantic Scholar and
OpenAlex DOI records, in agreement; RSNA and PubMed pages return 403/cookie-gate)**

150 board-style questions, three attempts spread over weeks. Repeatability κ = 0.6–0.77 (GPT-3.5)
and 0.76–0.82 (GPT-4), against the study's own pre-specified "almost perfect" threshold of
κ ≥ 0.91, which neither reached. Accuracy drifted down across attempts.
**Weight: weak-moderate, and flag the design.** Attempts separated by weeks conflate model drift
with sampling stochasticity, so this is not a clean test-retest. Cite only as evidence that the
medical literature takes κ ≥ 0.91 as the bar — a harsher standard than IR uses, and one worth
naming when we report a Spearman-Brown figure.

### `zheng-2023-mtbench` — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
Lianmin Zheng, Wei-Lin Chiang, Ying Sheng, Siyuan Zhuang, Zhanghao Wu, Yonghao Zhuang, Zi Lin,
Zhuohan Li, Dacheng Li, Eric P. Xing, Hao Zhang, Joseph E. Gonzalez, Ion Stoica. NeurIPS 2023
(Datasets and Benchmarks); arXiv 2306.05685, 9 Jun 2023. <https://arxiv.org/abs/2306.05685> ·
**verified**

The origin of the phrase. GPT-4 matches human preference at >80% agreement — *"the same level of
agreement between humans"*, so once again the comparator is human-human, not truth. Documents
position, verbosity and self-enhancement bias.
**Self-reproducibility:** not measured.
**Weight: moderate; cite as origin only.** Everything specific we need is downstream of it.

### `nowak-2025-self-consistency` — Estimating the Self-Consistency of LLMs
Robert Nowak. arXiv 2509.19489, 2025. <https://arxiv.org/abs/2509.19489> ·
**verified (delegated fetch)**

Short theoretical note: under a fixed budget B = mn (m prompts × n repeats), the variance-optimal
split is roughly m, n ∝ √B. No empirical judge task.
**Weight: weak, but cite it if we justify our readings-per-item budget.** It is the only formal
treatment of the repeats-versus-items trade-off found.

---

## Contradicts or complicates us

Ordered by how much trouble each one causes.

1. **`norman-2026-reliability-without-validity` — "Reliability without Validity" is our title in the negative.** 541,000
   judgments showing test–retest reliability above 0.95 coexisting with severe position bias.
   High self-agreement is demonstrably compatible with a systematically wrong instrument. Our
   0.92 is not evidence the graded score is right, and a reviewer with this paper in hand will
   say so unless we say it first, in the abstract.

2. **`thakur-2025-support` — the human comparator collapses before the model does.** Two trained
   NIST assessors, given the same answer sentence and the same cited passage, agree at
   κ = −0.03 (from scratch) and 0.07 (post-editing). Two LLMs agree with each other at κ = 0.46–0.60.
   The obvious counter-reading of our result: span disagreement is a property of the *task*, not
   of language models, and our 0.47 might be *better* than humans would manage. We have no human
   read yet. This is the most dangerous single entry in the file for us.

3. **`lee-sun-2019-pico` — humans in our own domain already show the same two numbers.** Medical
   experts on biomedical abstracts: exact span-boundary F1 0.357–0.576, relaxed token-overlap F1
   0.713–0.758. *"Boundaries … are very diverse"* while *"general areas … are broadly agreed."* A
   graded or relaxed view being stable while the exact set is not is a twenty-year-old finding
   about human annotation. Our contribution is that it holds for a machine *against itself*, at
   temperature zero, where there is no obvious source of the variation — but the phenomenon is
   not new and the paper must not present it as if it were.

4. **`yang-2018-hotpotqa` — and the shape of the gap is instructive.** Human answer EM 83.60
   versus supporting-fact EM 61.50, and the authors say crowd workers *"agree less on supporting
   facts."* But supporting-fact **F1** is 90.04 against answer F1 91.40 — the gap is almost
   entirely in exact set match. If our 0.47 is an exact-set-match statistic and our 0.92 is a
   graded one, part of the contrast is a metric artefact that HotpotQA already exhibits. Paper A
   must report a partial-credit span measure alongside the exact-set measure or this is the first
   review comment.

5. **`shi-2025-position-bias` — strong judges are already known to repeat themselves.** Repetition
   stability 0.96–1.00 for GPT-4o, GPT-4 and Claude-3.5-Sonnet on pairwise MT-Bench. If a
   reviewer's prior is "modern judges are stable", it is an evidenced prior. Our answer has to be
   that the task differs (localisation, not pairwise preference) and that we measure the span set,
   not the label — not that stability was previously unknown.

6. **`xue-2026-scoring-consistency` — "almost perfect" intra-LLM consistency, regardless of
   temperature.** The strongest direct counterclaim available. Short constructed responses, whole-
   item rubric scoring, no localisation — so the scope defence is available, but only if we have
   actually read the paper. Its publisher page is gated; read it before drafting.

7. **`abercrombie-2025-consistency` — a named prediction we falsify.** *"We do not foresee many
   situations where reliability is high yet stability/consistency is low."* Our result is
   adjacent to that: high reliability on one view of the same readings, low reproducibility on
   another view of the *same* readings. Worth engaging explicitly; it is a friendly paper and the
   engagement makes our result look like a contribution to their framework rather than an
   exception to it.

8. **`yang-2026-judge-changes` — pooling may not buy independence.** *"Repeated-sample juries add
   little when errors are correlated."* Paper A pools readings into a graded score and gets 0.92.
   If the errors across readings are correlated — which temperature-zero decoding makes likely —
   the Spearman-Brown projection overstates what pooling achieved. We should report the observed
   pairwise correlation and let the reader check the projection, not just the projected figure.

9. **`schroeder-2024-trust` — ω = 1.0 at temperature zero, trivially.** Their framework says a
   deterministic decoder is perfectly reliable by construction. We run at temperature zero and do
   *not* get perfect reproduction. That is interesting, but it means the paper owes the reader an
   explanation of where the variation comes from (non-determinism in serving, batching,
   presentation seeding, prompt-position effects) or the result reads as an artefact of an
   uncontrolled pipeline rather than a property of the task.

10. **`clarke-dietz-2024` and `soboroff-2025` — the ceiling argument.** Neither is about
    reproducibility, but both will be cited at any paper that makes LLM-generated gold look more
    usable. *"No system can outperform the evaluation's answer key."* A stable judge is a stable
    ceiling.

11. **`saha-2026` — someone got there first on localisation, four months ago.** They do not
    measure self-consistency and their framing is accuracy rather than stability, so the
    contribution survives. But "nobody has asked an LLM where the evidence is" is now false and
    must be struck from any draft that contains it.

---

## The gap we fill

The claim that survives all of the above is narrow and should be stated narrowly:
**no published work measures an LLM judge's agreement with itself at span level.** Not in IR, not
in NLG evaluation, not in annotation methodology. The supporting evidence, in descending order of
strength:

**The one paper that defines the construct says it is whole-response only.**
`haldar-2025-rating-roulette` gives the definition we need — *"We define self-reliability as the
agreement of a judge with itself over multiple runs with the same settings"* — and applies it to
binary factuality, Likert scoring and three-way ranking. Confirmed by direct read: no span or
sub-response analysis anywhere in the paper. Its own closing recommendation is for *more*
reliability data, including from humans.

**The one paper that does LLM span annotation does not repeat a reading.**
`kasner-2026-spans` compares LLM spans against human spans across three tasks and 40k+
annotations, with seed 42 and temperature 0 — one run per input. It measures inter-annotator
agreement only. Its limitations section concedes it cannot even establish the human ceiling:
*"Our estimates of the upper-bound IAA for each task are difficult to establish and depend on many
factors, such as the chosen annotation categories, their ambiguity, the annotation guidelines, or
the qualification level of human annotators."* And the sentence that most directly names the
phenomenon we measure: *"Concerningly, the LLM annotations that were marked as correct have only
24% hard character-level overlap (51% soft) with human annotations."* Correct, and in a different
place.

**The one paper that does LLM evidence localisation in IR does not repeat a reading either.**
`saha-2026` runs each query-document pair once — Llama at temperature 1.0 to 3.0, GPT at 0 — and
says outright it has no agreement baseline to interpret against: *"All reported results should be
interpreted in the context of inter-annotator agreement; however, only limited agreement
information is available for the INEX collections."*

**The flagship IR study has no repeatability measurement on either side.**
`upadhyay-2024-initial-look`: *"Our analysis is missing a comparison between two human assessors,
since it is well known that humans disagree with each other in making relevance judgments. Due to
budget and time limitations, we were not able to have the same topics annotated by multiple human
assessors independently."* One LLM judgment per pair, no kappa at the item level at all.

**The TREC RAG support study defers consistency by name.**
`thakur-2025-support`: *"We keep as future work explorations of consistency: why LLM judges labeled
only a few sentences as 'no support', and similarly, why human judges labeled a majority of
sentences as 'no support'."* And on the localisation half: *"We save for future work how to best
evaluate multiple passage citations for every sentence in the answer, as it would require a much
larger annotation budget."*

**The nugget line says the same thing more generally.**
`pradeep-2025-nugget-recall`: *"Another gap in our current understanding is the relationship between
inter-annotator agreement and LLM–human differences."*

**The annotation-methodology literature says the field does not do this at all.**
`abercrombie-2025-consistency`, from a systematic review: *"While inter-annotator agreement is
frequently used in NLP to determine the reliability of labels or the processes used to produce
them, intra-annotator agreement is rarely, if ever, reported"*; *"reporting intra-annotator
agreement is so far extremely uncommon in NLP."*

**And the field's default decoding setting makes the measurement structurally unavailable.**
Every IR relevance-judgment paper in section 1 that states a temperature states 0 —
`thomas-2024`, `upadhyay-2024-umbrela`, `balog-2025`, `alaofi-2024-cafe`, `faggioli-2023` — and
every one of them reads each item exactly once. Greedy decoding is treated as making the question
moot. `schroeder-2024-trust` makes that assumption explicit by reporting ω = 1.0 at temperature 0.
Our result is that it is not moot, which is why the paper should report the temperature-zero
setting prominently rather than as a footnote.

**One honest caveat on the gap.** `upadhyay-2026-trec2025` advertises "agreement analysis" in its
evaluation framework and its tables were not extracted for this survey. Before the preprint goes
out, someone should read that section and confirm it contains no repeated-reading condition.

---

## Searched and did not find

Queries run that returned nothing relevant, so the space is covered rather than missed. (This
session exhausted its 200-call web-search budget; searches below were run by this agent or by the
four delegated sweeps, all of which reported their own dead ends.)

- *"relevance judgment" LLM "multiple runs" OR "repeated" stability "same model" variability
  qrels nondeterminism study* — surfaced only general-purpose LLM determinism work
  (`Non-Determinism of "Deterministic" LLM Settings`, programming-task repeatability), nothing in
  IR relevance judgment.
- *LLM "highlight" OR "extract" supporting span RAG citation self-consistency across runs
  different spans same verdict* — returned citation-extraction engineering blogs and
  highlighted-chain-of-thought work; the search engine itself noted the specific combination
  "self-consistency across multiple runs with different spans supporting the same verdict"
  appears undocumented.
- *"Spearman-Brown" OR "generalizability theory" LLM judge reliability number of judgments
  needed* — found Spearman-Brown used to project *ensembles of different judges* (`yang-2026`,
  and a capability-transfer paper), never to project repeated readings of one judge at span level.
  No paper was found applying generalizability theory with rater facets to LLM judges.
- *evidence sentence selection LLM agreement "where" versus "whether" localisation reliability
  biomedical* — returned clinical-annotation and fact-checking work; no paper posing the
  whether/where contrast as such.
- *"test-retest" reliability large language model annotation intra-rater agreement repeated
  readings* — returned the general IRR methodology literature plus the clinical papers now in
  section 7; nothing at sub-document granularity.
- *TREC Deep Learning track LLM-qrel agreement NIST assessors* — no standalone DL-track LLM-qrel
  study beyond `faggioli-2023`, `thomas-2024` and `upadhyay-2024-umbrela`.
- **No single "Overview of the TREC 2024 RAG Track" paper exists.** The expected NIST path
  (`trec.nist.gov/pubs/trec33/papers/Overview_rag.pdf`) returns 404; only the TREC 2025 (trec34)
  overview exists. 2024 coverage is split across `pradeep-2024-autonuggetizer`,
  `thakur-2025-support`, `upadhyay-2024-initial-look` and `pradeep-2024-ragnarok`. Do not cite a
  2024 RAG Track overview paper.
- **No numeric passage-level human-human agreement in the TREC HARD track overviews** (2003 or
  2004), despite both discussing passage judging at length — only the qualitative
  "terrifically difficult" statement.
- **No inter-annotator agreement coefficient in `gao-2023-rarr`**, despite the paper describing a
  three-annotator agreement pilot. Searched the abstract and full text twice.
- **No human-human nugget-assignment agreement anywhere in the AutoNuggetizer line.**
- *generalizability theory applied to LLM raters*, *batch-invariance / floating-point
  nondeterminism in LLM serving (primary peer-reviewed source)* — no citable primary source
  located; the batch-invariance material exists only as vendor/blog write-ups.

---

## COULD NOT VERIFY — do not cite

- **"Same Input, Different Scores: A Multi Model Study on the Inconsistency of LLM Judge"** —
  appears only on ResearchGate (publication 401599267), dated February 2026. No arXiv id, no
  publisher page, no author list confirmable. Content sounded directly relevant (identical inputs
  scoring differently at temperature 0 across Gemini, Claude Haiku and Claude Sonnet on enterprise
  RAG pairs). Searched: *"Same Input, Different Scores" inconsistency LLM judge arXiv multi model
  study*. **Excluded.**
- **"LLM4Eval@WSDM 2025: Large Language Model for Evaluation in Information Retrieval"** —
  `dl.acm.org/doi/10.1145/3701551.3705706` returns HTTP 403 to automated fetch; title and authors
  not confirmable from a publisher page. Try the Microsoft Research mirror or the
  `llm4eval/WSDM2025` GitHub. **Excluded pending re-fetch.**
- **`alaofi-2026-tois`** is listed in section 1 but its publisher page is 403 and its full text was
  not read. Metadata came from the reference list of a PDF this survey extracted and read
  (`saha-2026`, entry [3]) plus a secondary index. Safe to cite as existing; **not** safe to cite
  a number from.
- **`xue-2026-scoring-consistency`** and **`krishna-2024-radiology`** — metadata confirmed only via
  DOI records (OpenAlex, Semantic Scholar), publisher and PubMed pages cookie-gated. Both are
  listed above with that caveat. Read before quoting.
- **`voorhees-2000`** page range 697–716 comes from dblp, whose page returned an access-denied
  interstitial. Title, author, journal, volume, issue and year are confirmed from the NIST record.
- **arXiv 2601.01862 "Judging with Personality and Confidence"** — surfaced in a search snippet
  during the critical-literature sweep, never fetched. **Excluded.**
- **Numbers marked *(delegated read)* above** were extracted by a sub-agent that fetched the same
  primary source this survey lists. Their bibliographic metadata was independently re-fetched and
  confirmed here; the *figures* were not re-derived. Any such number that becomes load-bearing in
  the manuscript must be re-read from the PDF before it goes into `claims.json`.
