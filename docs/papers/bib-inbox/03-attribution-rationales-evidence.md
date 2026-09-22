# 03 — Locating and verifying evidence inside documents

Survey area: attribution evaluation, citation/grounding evaluation for RAG, rationale
extraction, fact verification with evidence selection, decomposed factuality.

**Note on format.** `docs/papers/bibliography.md` does not exist in the working tree at the
time of writing (`docs/papers/` contained only the empty `bib-inbox/`), so the reference entry
`allamraju-2025` could not be read. The format below follows the brief: short-key, exact title,
full author list, venue, year, URL, verification level, what the paper actually establishes, and
a one-line **Weight**. Adjust to house style when merging.

**Verification protocol used.** Every entry below was retrieved from ACL Anthology, arXiv
(`/abs/` page or the arXiv API), or an equivalent publisher page. Entries marked **read** were
additionally downloaded as PDF and read with `pymupdf` — the specific sections read are named.
Entries marked **verified** had title, full author list, venue, year and abstract confirmed on the
publisher page but the methods were not opened. Nothing in this file was written from memory.
Where two retrievals of the same paper disagreed, the disagreement is recorded rather than
silently resolved.

Counts: **41 entries verified**, of which **13 read** at the methods level. One secondary-source
number and two metadata details are quarantined in *Could not verify*.

---

## 1. Attribution evaluation (AIS and successors)

### `rashkin-2023-ais`
**Measuring Attribution in Natural Language Generation Models** — Hannah Rashkin, Vitaly
Nikolaev, Matthew Lamm, Lora Aroyo, Michael Collins, Dipanjan Das, Slav Petrov, Gaurav Singh
Tomar, Iulia Turc, David Reitter
*Computational Linguistics* 49(4):777–840, 2023 · https://aclanthology.org/2023.cl-4.2/ ·
**read** (§4.1.4 Limitations, §4.2 procedure, §5.5.1 IAA + Table 7)

- **Evidence model:** binary, and deliberately so. AIS asks whether "According to *P*, *s*"
  holds for a whole system output against a whole source *P*. It does **not** ask where in *P*
  the support lies.
- The limitations section states this outright: "we ask annotators to evaluate the entire output
  (rather than sentences or specific spans) under the reasoning that if even one span within the
  model output is not AIS, then the whole output is not AIS." They flag that "for some
  applications of AIS measures, it may be useful to have more fine-grained measures."
- **Repeated annotation:** yes, for the *whether* judgement — nine trained full-time annotators;
  IAA reported as Krippendorff's α, pairwise agreement and F1-to-consensus. AIS α is 0.69 on
  CNN/DM (the hardest, longest task) and higher on QReCC; agreement-with-expert is also
  reported. No repeated annotation of span location, because no span is annotated.
- **Granularity:** whole output vs. whole source.

**Weight:** The canonical framework for *whether*, and its own limitations section concedes the
*where* question is out of scope — the cleanest available statement that the field's flagship
attribution metric is a binary-support metric by construction.

### `bohnet-2022-attributed-qa`
**Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models**
— Bernd Bohnet, Vinh Q. Tran, Pat Verga, Roee Aharoni, Daniel Andor, Livio Baldini Soares,
Massimiliano Ciaramita, Jacob Eisenstein, Kuzman Ganchev, Jonathan Herzig, Kai Hui, Tom
Kwiatkowski, Ji Ma, Jianmo Ni, Lierni Sestorain Saralegui, Tal Schuster, William W. Cohen,
Michael Collins, Dipanjan Das, Donald Metzler, Slav Petrov, Kellie Webster
arXiv:2212.08037v2, 2022 (updated 2023-02-10) · https://arxiv.org/abs/2212.08037 · **verified**
(arXiv API, abstract verbatim, 22 authors confirmed)

- **Evidence model:** one *(answer, attributing passage)* pair per question, scored by AIS-style
  human rating. Correct-vs-incorrect attribution, not correct-vs-other-valid-passage.
- **Repeated annotation:** human annotations are the gold standard; the contribution is that an
  automatic metric correlates well enough for development. No span-stability measurement.
- **Granularity:** passage.

**Weight:** Establishes the AQA task shape — retrieve a passage, rate the pair — that everything
downstream inherits; useful as the point where "which passage" became a single-gold problem.

### `yue-2023-attrscore`
**Automatic Evaluation of Attribution by Large Language Models** — Xiang Yue, Boshi Wang, Ziru
Chen, Kai Zhang, Yu Su, Huan Sun
*Findings of EMNLP 2023*, pp. 4615–4635 · https://aclanthology.org/2023.findings-emnlp.307/ ·
**verified**

- **Evidence model:** three-way per *(claim, reference)* pair — attributable, extrapolatory,
  contradictory. Still a judgement about a given reference, never a search for the right one.
- Test set curated from 12 domains of real New Bing interactions.
- **Granularity:** claim against a cited reference.

**Weight:** The standard automatic stand-in for AIS; confirms that automatic attribution scoring
is defined over a *supplied* reference, so it cannot say anything about selection.

### `liu-2023-verifiability`
**Evaluating Verifiability in Generative Search Engines** — Nelson Liu, Tianyi Zhang, Percy Liang
*Findings of EMNLP 2023*, pp. 7001–7025 · https://aclanthology.org/2023.findings-emnlp.467/ ·
**verified**

- Human audit of Bing Chat, NeevaAI, perplexity.ai, YouChat: "on average, a mere 51.5% of
  generated sentences are fully supported by citations and only 74.5% of citations support their
  associated sentence."
- **Evidence model:** per-sentence citation precision/recall against the cited *page*. A citation
  is judged on whether the cited source supports the sentence — not on whether a better-suited
  passage existed elsewhere on the same page.
- **Granularity:** generated sentence → cited web page.

**Weight:** The most-cited empirical statement of citation quality in deployed systems, and a
clean demonstration that the field's operative unit is the *document*, not the span.

### `malaviya-2024-expertqa`
**ExpertQA: Expert-Curated Questions and Attributed Answers** — Chaitanya Malaviya, Subin Lee,
Sihao Chen, Elizabeth Sieber, Mark Yatskar, Dan Roth
*NAACL 2024*, pp. 3025–3045 · https://aclanthology.org/2024.naacl-long.167/ · **verified**

- 484 experts across 32 fields; 2177 questions with verified answers and attributions for claims.
- **Evidence model:** experts verify claim-level attributions produced by systems; the expert
  revises rather than independently re-selects, so this is verification of a proposed attribution,
  not independent re-location.
- **Granularity:** claim → cited source.

**Weight:** Best expert-grade attribution dataset; relevant mainly because even with domain
experts the protocol is *verify the offered citation*, never *find the evidence twice*.

### `honovich-2022-true`
**TRUE: Re-evaluating Factual Consistency Evaluation** — Or Honovich, Roee Aharoni, Jonathan
Herzig, Hagai Taitelbaum, Doron Kukliansy, Vered Cohen, Thomas Scialom, Idan Szpektor, Avinatan
Hassidim, Yossi Matias
*NAACL 2022*, pp. 3905–3920 · https://aclanthology.org/2022.naacl-main.287/ · **verified**

- Standardises 11 datasets to binary factual-consistency annotations; finds NLI-based and
  question-generation/answering metrics work best. The NLI model from this line (`T5-XXL TRUE`)
  is the scorer ALCE uses.
- **Granularity:** whole generated text against whole grounding text — binary.

**Weight:** The measurement instrument the citation literature runs on, and it is binary at the
text level; any span-level claim built on it inherits that coarseness.

### `zhang-2024-faithfulness-metrics-citation`
**A Comparative Analysis of Faithfulness Metrics and Humans in Citation Evaluation** — Weijia
Zhang, Mohammad Aliannejadi, Jiahuan Pei, Yifei Yuan, Jia-Hong Huang, Evangelos Kanoulas
arXiv:2408.12398, 2024; LLM4Eval workshop @ SIGIR 2024 ·
https://arxiv.org/abs/2408.12398 · **verified**

- Evaluates whether faithfulness metrics can separate **full / partial / no** support. Finding:
  "the best-performing metrics struggle to distinguish partial support from full or no support."
- **Evidence model:** graded (three-level) support, which is the nearest published move toward a
  graded rather than binary support judgement.

**Weight:** Prior art for *grading* support rather than binarising it — but graded over the
citation, not over every sentence of the document, and with no reliability analysis.

### `ding-2026-attribution-transfer`
**Do LLM Attribution Metrics Transfer? Auditing Retrieval-Augmented Generation Evaluation Across
Datasets and Constructs** — Tianyu Ding, Aditya Nannapaneni, Juan Pablo De la Cruz Weinstein
arXiv:2606.23915, submitted 2026-06-22, revised 2026-09-16; GroundLM workshop @ EMNLP 2026 ·
https://arxiv.org/abs/2606.23915 · **verified** (abs page)

- Audits eight automatic scorers across three constructs (provenance/topicality, generated-answer
  attribution, fact-check entailment). Reports a ranking inversion: an NLI scorer at 0.90 AUROC on
  short-claim attribution falls to 0.53 (chance) on long-form text, where BERTScore reaches 0.91.
- **Baseline check:** the headline is a *collapse*, not a gain, and the comparison is
  scorer-vs-scorer on the same data, so there is no zero-baseline inflation risk here. The 0.53
  figure is the load-bearing one and it is explicitly chance-level.

**Weight:** Independent evidence that attribution metrics do not transfer across granularity of
the thing being attributed — supports the position that a metric validated on short claims says
nothing about long-document span location.

---

## 2. Citation and grounding evaluation for RAG

### `gao-2023-alce`
**Enabling Large Language Models to Generate Text with Citations** — Tianyu Gao, Howard Yen,
Jiatong Yu, Danqi Chen
*EMNLP 2023*, pp. 6465–6488 · https://aclanthology.org/2023.emnlp-main.398/ · **read**
(§3.3 Citation Quality, Figure 3)

- **Evidence model — the crucial detail: the citation unit is the retrieved passage, and the
  metric never asks whether the cited span is the right one.** Citation recall for statement
  *s_i* is 1 iff `φ(concat(C_i), s_i) = 1`, i.e. the *concatenation of all cited passages* entails
  the statement under the TRUE NLI model. Citation precision marks a citation "irrelevant" only if
  it alone does not support the claim **and** removing it does not break the support of the rest.
- They state the design choice explicitly: precision "does not require citing a minimal set … because
  human writing often cites redundant sources to enhance credibility." So ALCE **tolerates**
  multiple valid citations by construction, and therefore cannot detect that two runs cited
  different-but-both-adequate passages.
- They tie the recall definition back to AIS: `φ(concat(C_i), s_i) = 1` implies *s_i* is true based
  solely on the concatenation.
- Headline result: "on the ELI5 dataset, even the best models lack complete citation support 50% of
  the time."
- **Granularity:** generated statement → set of passages (100-word Wikipedia passages / retrieved
  documents). Not spans.

**Weight:** The benchmark our citation-precision/recall vocabulary comes from, and the exact place
where "the right document" was substituted for "the right span" — with the substitution documented
as deliberate.

### `zhang-2025-longcite`
**LongCite: Enabling LLMs to Generate Fine-grained Citations in Long-Context QA** — Jiajie Zhang,
Yushi Bai, Xin Lv, Wanjun Gu, Danqing Liu, Minhao Zou, Shulin Cao, Lei Hou, Yuxiao Dong, Ling Feng,
Juanzi Li
*Findings of ACL 2025*, pp. 5098–5122 (also arXiv:2409.02897) ·
https://aclanthology.org/2025.findings-acl.264/ · **verified**

- Pushes the citation unit down to the **sentence** in long contexts (LongBench-Cite benchmark,
  CoF data pipeline, LongCite-8B/9B models).
- **Protocol point:** citations are emitted as *sentence indices into a pre-segmented context*,
  not as quoted text. This is a structural sidestep of the quote-fidelity problem rather than a
  study of it.
- **Granularity:** sentence, in contexts of tens of thousands of tokens.

**Weight:** The reference point for sentence-granular citation in long documents; its
index-not-quote interface is the closest thing in the literature to a "whole sentence anchor"
protocol, arrived at for engineering reasons and never evaluated against quoting.

### `wang-2026-granularity`
**Are Finer Citations Always Better? Rethinking Granularity for Attributed Generation** — Hexuan
Wang, Jingyu Zhang, Benjamin Van Durme, Daniel Khashabi
arXiv:2604.01432 (v1 2026-04-01, v2 2026-04-03), cs.CL · https://arxiv.org/abs/2604.01432 ·
**verified** (abs page, abstract quoted verbatim; v2 HTML also read)

- Abstract (v2, verbatim in part): "enforcing fine-grained citations degrades attribution quality
  by 16-276% compared to the best-performing granularity … attribution quality peaks at
  intermediate granularities (paragraph-level) … fine-grained (sentence-level) citations disrupt
  necessary semantic dependencies for attributing evidence to answer claims, while excessively
  coarse citations (multi-paragraph) introduce distracting noise … fine-grained constraints
  disproportionately penalize larger models."
- Setting: LongBench-Cite (585 English queries over HotpotQA, GovReport, MultiFieldQA,
  LongBench-Chat), four model scales 8B–120B, granularity swept as *k* sentences per citable unit
  (k = 1, 2 / 4, 8 / 16, 32). Metric: Citation F1.
- **Baseline check — do this before quoting the multiple.** The v1 abstract states the effect as
  "2–97% (median 40%)"; the v2 abstract states "16–276%". Same paper, two versions, incompatible
  ranges — so the headline multiple is unstable across a two-day revision and must not be quoted
  without saying which version. The one concrete pair surfaced from the v2 HTML is Llama-70B at
  16–31-sentence volume: Citation F1 0.398 at k=2 vs 0.677 at k=4 (+70%). That is a real, mid-range
  baseline, not a zero. But a 276% relative degradation implies some cell with a very low absolute
  F1, and those cells are where the top of the range comes from. **Re-derive from their tables
  before citing any multiple.**
- Answer correctness is reported as roughly flat (+0.7% to +9.2% in v2; −2.3% to +4.4% in v1).

**Weight:** The most direct challenge to "make the evidence unit a sentence" as a design rule —
but it measures a *generation* constraint's effect on citation F1, not the reliability of locating
evidence, so it argues against sentence-granular *citing*, not against sentence-granular *scoring*.
Must be cited and distinguished.

### `slobodkin-2024-attribute-first`
**Attribute First, then Generate: Locally-attributable Grounded Text Generation** — Aviv Slobodkin,
Eran Hirsch, Arie Cattan, Tal Schuster, Ido Dagan
*ACL 2024* (Vol. 1: Long Papers), pp. 3309–3344 (also arXiv:2403.17104) ·
https://aclanthology.org/2024.acl-long.182/ · **verified**

- Decomposes generation into content selection → sentence planning → sentence-by-sentence
  generation, so the *selected source segments themselves* become the attributions.
- **Evidence model:** segment-level ("locally-attributable") rather than passage-level; reports
  more concise citations at equal-or-better generation quality and attribution accuracy, and
  reduced human fact-verification time.
- **Granularity:** sub-sentence source segments.

**Weight:** The strongest existing argument that finer-than-passage attribution is achievable and
helps human verifiers — the counterweight to `wang-2026-granularity`, and evidence that the
community already believes span-level attribution is the goal.

### `sancheti-2024-posthoc-attribution`
**Post-Hoc Answer Attribution for Grounded and Trustworthy Long Document Comprehension: Task,
Insights, and Challenges** — Abhilasha Sancheti, Koustava Goswami, Balaji Vasan Srinivasan
arXiv:2406.06938, 2024-06-11; *SEM 2024 · https://arxiv.org/abs/2406.06938 · **verified**
(abs page; granularity and agreement details **not** confirmed — see *Could not verify*)

- Formulates post-hoc attribution of answer text to source in long-document comprehension;
  refactors existing datasets and benchmarks retrieval-based and entailment-based attributors;
  the contribution includes highlighting limitations of the repurposed datasets.

**Weight:** Names the exact task — attribute an already-written answer back into a long document —
and reports it as unsolved; a natural related-work anchor, low evidentiary weight until read.

---

## 3. Rationale and explanation extraction (deepest-read section)

### `deyoung-2020-eraser`
**ERASER: A Benchmark to Evaluate Rationalized NLP Models** — Jay DeYoung, Sarthak Jain, Nazneen
Fatema Rajani, Eric Lehman, Caiming Xiong, Richard Socher, Byron C. Wallace
*ACL 2020*, pp. 4443–4458 · https://aclanthology.org/2020.acl-main.408/ · **read**
(§3 datasets + Table 2 human agreement, §4.1 agreement metrics, §4.2 faithfulness metrics)

This is the closest prior art and it needs to be characterised precisely.

- **Multiple valid rationales: acknowledged, and handled by an "any-of" rule, not by a
  many-valid model.** The discrete metric is token-level IOU with a threshold: "We count a
  prediction as a match if it overlaps with **any of the ground truth rationales** by more than
  some threshold (here, 0.5)." Partial matches feed an F1; token-level P/R/F1 are also reported.
  For soft scorers they use AUPRC over token scores.
- **Non-exhaustiveness is stated, not measured.** "In general, the rationales we have for tasks
  are sufficient to make judgments, but **not necessarily comprehensive**." For some datasets they
  went back and collected *comprehensive* rationales (all supporting evidence marked) on test
  instances — Movie Reviews, Evidence Inference, BoolQ — precisely because the original
  annotations were known to be incomplete. That is the same phenomenon as a growing union, treated
  as a data-collection fix rather than as a finding.
- **Repeated annotation: yes, but shallow and against a majority, not pairwise-replicated.**
  Table 2 reports human agreement over rationales with 2–3 annotators per document on small
  subsets: Evidence Inference (3 annotators, 199 docs) κ 0.618, token-F1 0.617; BoolQ 0.617/…;
  Movie Reviews (2, 96 docs) κ 0.712, F1 0.799; FEVER (2, 24 docs) κ 0.854, F1 0.871; MultiRC
  (2, 99) κ 0.728, F1 0.749; CoS-E (2, 100) κ 0.619, F1 0.654; e-SNLI (3, 9807) κ 0.743, F1 0.799.
  They characterise all of this as "substantial or better agreement." Note the standard
  deviations are large (±0.13 to ±0.31) and for several datasets the "annotators" are members of
  the author team comparing against pre-existing rationales.
  Crucially: **"we have not collected redundant comprehensive annotations"** — i.e. the
  comprehensive rationales, the ones that would reveal whether the marked set keeps growing, were
  each collected exactly once.
- **Faithfulness metrics:** comprehensiveness = `m(x_i)_j − m(x_i / r_i)_j` (confidence drop when
  the rationale is erased) and sufficiency (prediction from the rationale alone), following Yu et
  al. (2019). These are *model-behaviour* metrics and are orthogonal to whether two readings pick
  the same span.
- **Granularity:** mixed by dataset and explicitly noted as a problem — Movie Reviews is span-level,
  "in contrast to most other datasets, the rationale annotations here are span level as opposed to
  sentence level"; they call for models that "adaptively provide rationales at a task-appropriate
  level of granularity."

**Weight:** Highest. ERASER established that human rationales disagree at the token level (κ
0.62–0.85) and that rationale sets are non-comprehensive — but it measured agreement between
*different annotators against a majority* on small subsets, scored with an overlap-tolerant
"any-of-the-gold" rule, and never repeated the comprehensive annotation. It establishes
disagreement; it does not establish (or refute) non-convergence of the union or the
reproducibility of a graded per-sentence score.

### `carton-2020-human-rationales`
**Evaluating and Characterizing Human Rationales** — Samuel Carton, Anirudh Rathore, Chenhao Tan
*EMNLP 2020*, pp. 9294–9307 · https://aclanthology.org/2020.emnlp-main.747/ · **read**
(abstract, §1 + Table 1 failure taxonomy, fidelity-curve framing)

- Finding: "human rationales do not necessarily perform well on these metrics" (sufficiency /
  comprehensiveness), and their fidelity "varies greatly from model to model and class to class."
- **Redundancy is named as the mechanism.** Human rationales are "non-comprehensive because they
  miss redundant-yet-relevant information (e.g. the second personal attack)" — i.e. when the
  document contains two spans that would each do, the annotator marks one. That is the
  many-valid-spans phenomenon, identified qualitatively, in 2020.
- Contributions: a normalisation of fidelity metrics against model-dependent baseline behaviour
  (they show an empty rationale can score perfectly sufficient when the model is class-biased —
  a baseline-artefact caution worth borrowing), plus retraining-based and "fidelity curve"
  characterisation revealing irrelevance and redundancy. Six datasets.

**Weight:** High. The clearest published statement that a human-marked rationale is one of several
adequate spans, and that this makes the *rationale-as-gold* assumption unsound. It stops short of
quantifying how many distinct spans exist or whether the set closes.

### `muscato-2026-disagreeing-rationales`
**Disagreeing Rationales: Rethinking Classification and Explainability Evaluation in Hate Speech
Detection** — Benedetta Muscato, Beiduo Chen, Gizem Gezici, Barbara Plank, Fosca Giannotti
arXiv:2605.31563v1, 2026-05-29 (16 pages; no venue stated) · https://arxiv.org/abs/2605.31563 ·
**verified** (arXiv API, abstract verbatim)

- Abstract, verbatim in part: "Human disagreement is ubiquitous and well-known in labeling.
  However, **variation in explanations, captured through token-level human rationales, remains far
  less explored**. At the same time, it is unclear how to best evaluate human labels and rationales
  — or even **how to best aggregate rationales beyond majority vote** — in light of this variation."
- They re-implement models/losses/metrics under one protocol across **hard, intermediate and soft
  rationale representation spaces**, and organise explainability metrics into plausibility,
  faithfulness, complexity. Result: "both hard and soft metrics favor softer representations,
  highlighting their effectiveness in capturing variation."
- **This is the single most dangerous entry for a novelty claim about graded-over-binary
  evidence representation**, and it is recent enough that a reviewer may not have it but an
  area chair might.

**Weight:** Highest for positioning. Independently argues that soft (graded) rationale
representations beat hard sets under variation — in hate-speech classification, over token-level
rationales, across annotators. It does **not** do test–retest of a single rater/model, does not
measure union growth, and reports no reliability coefficient; the claim is about metric preference,
not about reproducibility of a location.

### `xu-2023-rave`
**From Dissonance to Insights: Dissecting Disagreements in Rationale Construction for Case Outcome
Classification** — Shanshan Xu, T. Y. S. S. Santosh, Oana Ichim, Isabella Risini, Barbara Plank,
Matthias Grabmair
arXiv:2310.11878v5 (v1 2023-10-18, v5 2024-02-16); arXiv comment states "Accepted to EMNLP 2023" ·
https://arxiv.org/abs/2310.11878 · **verified** (arXiv API, abstract verbatim; anthology ID not
confirmed — see *Could not verify*)

- Introduces **RAVE: Rationale Variation in ECHR**, rationales from two international human-rights
  law experts, "for whom we observe **weak agreement**."
- Builds a two-level, task-independent taxonomy of *why* the rationales differ, plus
  COC-specific subcategories; finds disagreement "mainly stem[s] from underspecification of the
  legal context."
- Also reports "limited agreement between models and experts" on RAVE.
- **Repeated annotation:** two experts on the same items — a genuine replication of *which* text
  is the rationale, in a hard expert domain, with the result that they largely do not match.

**Weight:** High. Expert-level replication of span selection showing weak agreement, plus a
taxonomy of causes. Prior art for "people disagree about where"; does not scale the replication
(n = 2 experts, one pass each) and offers no reliability statistic or union analysis.

### `jakobsen-2023-whose-right-reasons`
**Being Right for Whose Right Reasons?** — Terne Sasha Thorn Jakobsen, Laura Cabello, Anders Søgaard
*ACL 2023* (anthology 2023.acl-long.59, confirmed via Semantic Scholar); arXiv:2306.00639v2 ·
https://arxiv.org/abs/2306.00639 · **verified** (arXiv API abstract verbatim + S2 venue/anthology id)

- A collection of **human rationale annotations augmented with annotator demographics**, three
  datasets (sentiment, common-sense reasoning), six demographic groups balanced on age and
  ethnicity.
- Finds "**systematic inter-group annotator disagreement**", and that 16 Transformer models "align
  better with rationales provided by certain demographic groups … biased towards aligning best with
  older and/or white annotators." Negative correlation between model size and rationale agreement.
- **Repeated annotation:** yes — the same items rationalised by many annotators, by design, to
  study *whose* span is picked.

**Weight:** High. Establishes that "which span" is not merely noisy but *systematically* varies by
annotator population — a strong prior-art statement that there is no unique correct rationale.
Frames the variation as subjectivity/bias, not as a reliability ceiling on the measurement.

### `sarumi-2026-annotator-rationales`
**Fine-Grained Perspectives: Modeling Explanations with Annotator-Specific Rationales** — Olufunke
O. Sarumi, Charles Welch, Daniel Braun
arXiv:2604.21667v1, 2026-04-23; accepted at the 5th NLPerspectives Workshop ·
https://arxiv.org/abs/2604.21667 · **verified** (arXiv API, abstract verbatim)

- Jointly models annotator-specific labels and their rationales on disaggregated NLI annotations,
  conditioning on annotator identity and demographics; argues "modeling explanations as expressions
  of fine-grained perspective provides a richer and more faithful representation of disagreement."

**Weight:** Perspectivist framing of rationale variation — treats multiple rationales as *valid
per-person*, which is a different explanation for non-convergence than ours and should be named as
the alternative hypothesis.

### `zaidan-2007-annotator-rationales`
**Using "Annotator Rationales" to Improve Machine Learning for Text Categorization** — Omar Zaidan,
Jason Eisner, Christine Piatko
*NAACL-HLT 2007*, pp. 260–267 · https://aclanthology.org/N07-1033/ · **verified**
(abstract not displayed on the landing page)

- The origin of annotator rationales: annotators highlight substrings supporting their label;
  contrast examples with the rationale removed regularise an SVM. ERASER's comprehensiveness metric
  descends directly from this contrast-example construction.

**Weight:** Provenance citation for the whole rationale line; establishes that rationales were
never intended as a unique gold location, only as extra supervision.

### `lei-2016-rationalizing`
**Rationalizing Neural Predictions** — Tao Lei, Regina Barzilay, Tommi Jaakkola
*EMNLP 2016*, pp. 107–117 · https://aclanthology.org/D16-1011/ · **verified**

- Generator–encoder architecture extracting short, coherent input fragments that suffice for the
  prediction; the model-side origin of extractive rationales.

**Weight:** Provenance citation; the objective is *sufficiency*, with no uniqueness requirement —
the model is free to pick any adequate fragment, which is exactly the degeneracy we observe.

### `jacovi-2020-faithfulness`
**Towards Faithfully Interpretable NLP Systems: How Should We Define and Evaluate Faithfulness?** —
Alon Jacovi, Yoav Goldberg
*ACL 2020*, pp. 4198–4205 · https://aclanthology.org/2020.acl-main.386/ · **verified**

- Separates *plausibility* (agrees with humans) from *faithfulness* (reflects the model's reasoning)
  and argues against "the current binary definition for faithfulness" in favour of "a more graded
  one, which we believe will be of greater practical utility."

**Weight:** The standard citation for "graded rather than binary" in the explanation literature —
useful framing, no measurement.

### `lehman-2019-evidence-inference`
**Inferring Which Medical Treatments Work from Reports of Clinical Trials** — Eric Lehman, Jay
DeYoung, Regina Barzilay, Byron C. Wallace
*NAACL 2019*, pp. 3705–3717 · https://aclanthology.org/N19-1371/ · **verified**

- 10,000+ prompts over full-text RCT reports; the model must both infer the comparative finding and
  identify the supporting evidence span. Source of ERASER's Evidence Inference task, and the one
  ERASER re-annotated for *comprehensive* rationales using medical doctors.

**Weight:** The long-document, expert-domain evidence-location task closest to ours in shape;
also the dataset where ERASER's own comprehensive re-annotation happened.

---

## 4. Fact verification with evidence selection

### `thorne-2018-fever`
**FEVER: a Large-scale Dataset for Fact Extraction and VERification** — James Thorne, Andreas
Vlachos, Christos Christodoulopoulos, Arpit Mittal
*NAACL-HLT 2018*, pp. 809–819 · https://aclanthology.org/N18-1074/ · **read**
(§3.4 data validation, §5.2 evaluation, §5.8 error analysis)

The nearest thing in the literature to "the union does not converge", and it is in a validation
subsection rather than a headline.

- **Multiple evidence sets: yes, and quantified.** "in 31.75% of the claims more than one sentence
  was considered appropriate evidence"; claims require *composition* of multiple sentences in
  16.82% of cases; evidence spans multiple pages in 12.15%.
- **Scoring:** "Some claims may be equally supported by different pieces of evidence; in this case
  one complete set of sentences should be predicted." Systems selecting sentences the annotators did
  not are penalised in precision.
- **The admission:** "We recognize that it is **not feasible to ensure that the evidence selection
  annotations are complete**, nevertheless we argue that they are useful for automatic evaluation
  during system development. For a more reliable evaluation we advocate crowd-sourcing annotations
  of false-positive predictions at a later date."
- **The super-annotator experiment — the key result.** 1% of data was re-annotated by expert
  "super-annotators" with no time limit, "instructed … to search over the whole Wikipedia for every
  possible sentence that could be used as evidence." Regular annotations scored **95.42% precision
  and 72.36% recall** against that exhaustive set. So ordinary annotation recovers barely
  three-quarters of the evidence that unlimited effort finds, while almost everything it does mark
  is defensible. §3.4.4: "all except two annotators achieved > 90% precision and all but 9 achieved
  recall > 70%. The majority of the low-recall cases are for claims such as *'Akshay Kumar is an
  actor.'* where **the super-annotator added 34 sentences as evidence**."
- **Confirmed downstream:** in the error analysis, "the pipeline retrieved new evidence that had not
  been identified by annotators in 21.85% (n = 210) of claims. This was in-line with our expectation
  given the measured recall rate of annotators."
- Label IAA (5-way on 4% of claims, n = 7506): Fleiss κ 0.6841 — for the *label*, not the location.
- **Granularity:** sentence.

**Weight:** Highest in this section, and the most quotable external precedent for the shape of our
result: high precision, mediocre recall, a set that grows sharply with more effort (34 sentences
for one claim), and an explicit statement that completeness is infeasible. It is one replication
(regular vs. super), not a growth curve, and it is about human annotators rather than repeated
model readings.

### `thorne-2018-fever-sharedtask`
**The Fact Extraction and VERification (FEVER) Shared Task** — James Thorne, Andreas Vlachos, Oana
Cocarascu, Christos Christodoulopoulos, Arpit Mittal
*Proceedings of the First Workshop on Fact Extraction and VERification (FEVER)*, 2018, pp. 1–9 ·
https://aclanthology.org/W18-5501/ · **read** (scoring section)

- The exact multi-set scoring rule, verbatim: "The training, development and test data splits
  contain **multiple sets of evidence for each claim**, each set being a **minimal set** of sentences
  that fully support or refute it. The primary scoring metric for the task is the label accuracy
  conditioned on providing **at least one complete set of evidence**, referred to as the FEVER score.
  … **Where multiple sets of evidence was annotated in the data, only one set was required** for the
  claim to be considered correct."
- Partial evidence scores zero. Recall is awarded "only by providing a complete set of evidence."
  Best system: FEVER score 64.21%.

**Weight:** Definitive answer to "how does FEVER score multiple valid evidence sets": disjunction
over annotated sets, each set required whole. This rewards matching *an* annotated set and is blind
to a correct set nobody annotated — the precise failure mode the super-annotator study exposed.

### `aly-2021-feverous`
**FEVEROUS: Fact Extraction and VERification Over Unstructured and Structured information** — Rami
Aly, Zhijiang Guo, Michael Schlichtkrull, James Thorne, Andreas Vlachos, Christos
Christodoulopoulos, Oana Cocarascu, Arpit Mittal
arXiv:2106.05707v3, 2021 (NeurIPS Datasets & Benchmarks) · https://arxiv.org/abs/2106.05707 ·
**verified** (arXiv API, abstract verbatim)

- 87,026 claims, evidence as sentences **and/or table cells**; explicit effort "to track and minimize
  the biases … e.g. being able to predict the label without using evidence." Baseline predicts both
  correct evidence and verdict for 18% of claims.

**Weight:** Extends the FEVER evidence-set machinery to mixed modalities; the 18% joint figure is a
useful reminder of how hard *correct evidence + correct verdict* remains.

### `wadden-2020-scifact`
**Fact or Fiction: Verifying Scientific Claims** — David Wadden, Shanchuan Lin, Kyle Lo, Lucy Lu
Wang, Madeleine van Zuylen, Arman Cohan, Hannaneh Hajishirzi
*EMNLP 2020*, pp. 7534–7550 · https://aclanthology.org/2020.emnlp-main.609/ · **read**
(§3 quality, §4 task formulation and evaluation)

- **Definition:** "A rationale is a **minimal collection of sentences** which, taken together as
  premises in the context of the abstract, can reasonably be judged by a domain expert as implying
  the claim." Each SUPPORTS/REFUTES relation "must be justified by **at least one rationale**" —
  multiple rationales per abstract are explicitly allowed.
- **Repeated annotation:** 232 claim-abstract pairs independently re-annotated. Label agreement
  Cohen's κ = 0.75. **Rationale agreement: "we treat each sentence as either classified as 'part of
  a rationale' or 'not part of a rationale' and compute sentence-level agreement. The resulting
  Cohen's κ is 0.71."** That is a per-sentence binary agreement, not a set-identity agreement — the
  same reduction that makes disagreement look smaller than it is.
- **Multi-set scoring:** abstract-level — correctly rationalized if "there exists some gold rationale
  `R_i(c,a) ⊆ Ŝ(c,a)`" (any one gold rationale is a subset of the prediction). Sentence-level — a
  predicted sentence is correct only if it is a member of *some* gold rationale **and** all other
  sentences of that same gold rationale are also predicted. Prediction capped at 3 sentences
  (FEVER caps at 5). The model predicts a single flat set even though gold may hold several
  rationales: "although the gold annotations may contain multiple separate rationales, to simplify
  the prediction task we only require the model to predict a single collection."
- They chose abstracts over full articles partly because "previous attempts at full-document
  annotation suffered from low annotator agreement."

**Weight:** High. The cleanest statement of the any-of-the-gold-sets scoring rule at two
granularities, plus a per-sentence κ (0.71) on rationale membership from genuine double annotation —
the most directly comparable published number to a per-sentence agreement figure. Note it is
inter-annotator, single replication, on abstracts (short documents).

### `kamoi-2023-wice`
**WiCE: Real-World Entailment for Claims in Wikipedia** — Ryo Kamoi, Tanya Goyal, Juan Diego
Rodriguez, Greg Durrett
*EMNLP 2023*, pp. 7561–7583 · https://aclanthology.org/2023.emnlp-main.470/ · **read**
(§2.3 annotation + IAA, §2.4 statistics, footnote 8, Appendix B aggregation)

**The most quantitatively dangerous entry in this file.**

- **Repeated annotation at scale:** "We collect annotations from **5 unique workers** for each
  example in the development and test set, and from 3 for the train set." Entailment-label IAA:
  Krippendorff's α = 0.62 on dev.
- **Exact-set agreement on *which* sentences, reported directly:** "We found that this subset of
  workers chose the **exact same set of supported sentences for 56.1% of SUPPORTED cases and 34.4%
  of PARTIALLY-SUPPORTED cases**, which shows high inter-annotator agreement." *Their* reading of
  56%/34% exact-set match is that agreement is high.
- **Multiple valid sets retained as gold.** Footnote 8: "There can be multiple sets of supporting
  sentences for each claim/subclaim because different annotators can annotate different sets of
  supporting sentences that include sufficient information to support (or partially support) the
  claim/subclaim." Appendix B explains they aggregate by taking the set from the majority-label
  workers rather than a union: "We prefer this over union as there can be **multiple different sets
  of sentences with identical information** (e.g., the date can sometimes be ascertained from several
  different sentences)." At claim level, however, they do take "the union of all combinations" of
  subclaim-level sets.
- **Granularity:** sub-sentence claim units (GPT-3.5 Claim-Split decomposition, 3.0 subclaims per
  claim) × supporting **sentences** (1.9 per subclaim, 3.1 per claim), plus token-level marking of
  *non-supported* tokens within partially-supported subclaims (aggregated by token-level union,
  with items dropped when any annotator differs by more than three tokens — 25.3% of
  partially-supported dev subclaims were dropped that way).
- Evidence documents are long: 119.5 evidence sentences per datapoint.

**Weight:** Highest for the "contradicts us" question. WiCE already ran five independent readings
per item over long evidence documents, already computed exact-set agreement on which sentences, and
already found it is roughly a coin flip (56.1% / 34.4%) — and interpreted that as *high* agreement.
Any claim that span-set instability is unmeasured is wrong. What WiCE does not do: repeat the same
*reader*, model union growth as a function of readings, or compute a reliability coefficient for a
graded per-sentence score.

### `yang-2018-hotpotqa`
**HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering** — Zhilin Yang, Peng
Qi, Saizheng Zhang, Yoshua Bengio, William Cohen, Ruslan Salakhutdinov, Christopher D. Manning
*EMNLP 2018*, pp. 2369–2380 · https://aclanthology.org/D18-1259/ · **verified**

- Provides "sentence-level supporting facts required for reasoning, allowing QA systems to reason
  with strong supervision and explain the predictions." Supporting-facts EM/F1 is scored against a
  **single** gold set per question.

**Weight:** The most-used sentence-level evidence benchmark with a single-gold assumption — the
contrast case to FEVER/SciFact/WiCE, and evidence that single-gold is still the default.

---

## 5. Repeated annotation and the many-valid problem (the reliability literature)

### `kwiatkowski-2019-nq`
**Natural Questions: a Benchmark for Question Answering Research** — Tom Kwiatkowski, Jennimaria
Palomaki, Olivia Redfield, Michael Collins, Ankur Parikh, Chris Alberti, Danielle Epstein, Illia
Polosukhin, Jacob Devlin, Kenton Lee, Kristina Toutanova, Llion Jones, Matthew Kelcey, Ming-Wei
Chang, Andrew M. Dai, Jakob Uszkoreit, Quoc Le, Slav Petrov
*TACL* 7:452–466, 2019 · https://aclanthology.org/Q19-1026/ · **read**
(abstract, §1 overview, §4.4 Variability of Annotations, §5 evaluation rationale)
*Author list above is from the TACL/anthology record for this paper; the PDF header was read for
§4.4 but the full author list was not re-transcribed character-by-character — re-check before
final typesetting.*

- **The repeated-annotation design:** 307,373 training examples with single annotations; 7,830 dev
  and 7,842 test examples with **5-way** annotations; plus "analysis of **25-way annotations on 302
  examples**, giving insights into human variability on the annotation task."
- **Disagreement about location, stated explicitly (§4.4):** "As well as disagreeing about whether
  (q, d) contains a valid answer, **annotators can disagree about the location of the best answer.
  In many cases there are multiple valid long answers in multiple distinct locations on the page.**"
  Worked example: for *"name the substance used to make the filament of bulb"* on the incandescent
  light bulb page, "annotators identify **7 passages**" that discuss tungsten filaments. For short
  answers the extreme case has **11 distinct but correct answers**, with 14 of 25 annotators marking
  substrings of "to the lungs" carved three different ways and 5 saying no adequate short answer
  exists.
- **How concentrated the distribution is:** "by just taking the most popular long answer, we could
  account for **83%** of the long answer [annotations]"; short answers are worse — most popular
  accounts for 64%, top three for 90%.
- **The protocol fix they used:** footnote 8 — "we did instruct annotators to **select the earliest
  instance of an answer when there are multiple answer instances on the page**. However, there are
  still cases where different annotators disagree on whether an answer earlier in the page is
  sufficient in comparison to a later answer."
- Human upper bound against the 5-way aggregate: long answers 90% precision / 85% recall; short
  answers 79% / 72%. Post-hoc precision of single annotations: long 90%, short 84%.

**Weight:** Highest for the repeated-annotation question. NQ is the existing 25-reading study of
*where the evidence is*, in long documents, with the finding that multiple distinct locations are
valid and that a tie-breaking instruction only partly fixes it. It reports the popularity
distribution rather than a reliability coefficient, and it never re-reads with the same annotator.
The "earliest instance" rule is prior art for *a* protocol fix to span ambiguity — a different one
from whole-sentence anchoring, aimed at a different failure.

### `nie-2020-chaosnli`
**What Can We Learn from Collective Human Opinions on Natural Language Inference Data?** — Yixin
Nie, Xiang Zhou, Mohit Bansal
*EMNLP 2020*, pp. 9131–9143 · https://aclanthology.org/2020.emnlp-main.734/ · **verified**

- **ChaosNLI: 464,500 annotations, 100 per example**, over 3,113 SNLI/MNLI and 1,532 αNLI dev items.
  Findings: substantial genuine disagreement; models cannot recover the human label distribution;
  models are near-perfect on high-agreement items and near-random on low-agreement ones.
- **Scope:** the *label*, not the location. No span is annotated.

**Weight:** The gold standard for "annotate the same item many times and look at the distribution" —
a direct methodological precedent for many-readings designs, applied to classification. Its absence
of a span analogue is exactly the gap.

### `pavlick-2019-inherent-disagreements`
**Inherent Disagreements in Human Textual Inferences** — Ellie Pavlick, Tom Kwiatkowski
*TACL* 7:677–694, 2019 · https://aclanthology.org/Q19-1043/ · **verified**

- Disagreements "persist as we collect more ratings and as we vary the amount of context provided to
  raters" — i.e. more annotation does not converge to a point; argues for evaluating against "the
  full distribution of plausible human judgments."

**Weight:** The canonical "more annotation does not converge" result, for NLI labels. The structural
analogue of a non-converging union, one modality away.

### `plank-2022-label-variation`
**The "Problem" of Human Label Variation: On Ground Truth in Data, Modeling and Evaluation** —
Barbara Plank
*EMNLP 2022*, pp. 10671–10682 · https://aclanthology.org/2022.emnlp-main.731/ · **verified**

- Position paper: variation is signal, not noise, and it "impacts all stages of the ML pipeline:
  data, modeling and evaluation."

**Weight:** The framing citation for treating disagreement as a property of the task; the natural
place to hang "evidence location is a variation-bearing annotation."

### `kiritchenko-2017-bws`
**Best-Worst Scaling More Reliable than Rating Scales: A Case Study on Sentiment Intensity
Annotation** — Svetlana Kiritchenko, Saif Mohammad
*ACL 2017* (Vol. 2: Short Papers), pp. 465–470 · https://aclanthology.org/P17-2074/ · **read**
(§5 Annotation Reliability, Table 3, Figure 2)

- Methodological ally for the reliability half of our result. "To assess the reliability of
  annotations produced by a method (BWS or rating scale), we calculate **average split-half
  reliability (SHR) over 100 trials**. … All annotations for a term or a tuple are randomly split
  into two halves. Two sets of scores are produced independently from the two halves. Then the
  **correlation between the two sets of scores** is calculated."
- They report Spearman ρ between half-sets as a function of annotation budget, holding total
  annotations equal. Table 3 example: adverb phrases, n = 724, SHR 0.97 (BWS) vs 0.92 (rating
  scale). Conclusion: "with the same total number of annotations, BWS produces significantly more
  reliable results than the rating scale."
- Companion: **Capturing Reliable Fine-Grained Sentiment Associations by Crowdsourcing and
  Best–Worst Scaling** — Svetlana Kiritchenko, Saif M. Mohammad, *NAACL 2016*, pp. 811–817,
  https://aclanthology.org/N16-1095/ (**verified**, metadata only — the landing page shows no
  abstract).

**Weight:** The precedent for judging an annotation *protocol* by split-half reliability of the
scores it yields rather than by accuracy — the same instrument family as a Spearman-Brown
coefficient, and the right citation for "we compared two elicitation formats on reliability."

---

## 6. Decomposed factuality and sub-claim support

### `min-2023-factscore`
**FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation** —
Sewon Min, Kalpesh Krishna, Xinxi Lyu, Mike Lewis, Wen-tau Yih, Pang Koh, Mohit Iyyer, Luke
Zettlemoyer, Hannaneh Hajishirzi
*EMNLP 2023*, pp. 12076–12100 · https://aclanthology.org/2023.emnlp-main.741/ · **verified**

- Decomposes a generation into atomic facts and scores the percentage supported by a knowledge
  source; motivated by the observation that "generations often contain a mixture of supported and
  unsupported pieces of information, making binary judgments of quality inadequate."
- **Evidence model:** per-atomic-fact binary support against a *retrieved* source; the estimator has
  "less than a 2% error rate" against human FActScores. Human numbers: ChatGPT 58%.
- **Granularity:** atomic fact (sub-sentence) on the generation side; passage on the source side.
  Which passage supported it is not scored.

**Weight:** The reference for decomposing the *claim*; note it decomposes the generation, never the
document — the asymmetry that leaves "where in the source" unmeasured.

### `gao-2023-rarr`
**RARR: Researching and Revising What Language Models Say, Using Language Models** — Luyu Gao,
Zhuyun Dai, Panupong Pasupat, Anthony Chen, Arun Tejasvi Chaganty, Yicheng Fan, Vincent Zhao, Ni
Lao, Hongrae Lee, Da-Cheng Juan, Kelvin Guu
*ACL 2023* (Vol. 1: Long Papers), pp. 16477–16508 · https://aclanthology.org/2023.acl-long.910/ ·
**verified**

- Post-hoc attribution + revision: find evidence for an existing generation, then minimally edit
  unsupported content. Measured with attribution (AIS-style) and preservation.
- **Evidence model:** retrieve-then-verify per sentence; success is "some evidence supports it",
  a disjunction over retrieved results.

**Weight:** The post-hoc attribution pipeline everyone cites; its success criterion is existential
("evidence was found"), which is precisely a *whether*-metric wearing a *where*-shaped interface.

---

## 7. Quoting, copying and verbatim fidelity (the protocol question)

### `signe-2026-chyd`
**Guaranteeing Faithful Evidence Extraction in Speculative Retrieval-Augmented Generation** —
Quentin Signé, Mohand Boughanem, Jose Moreno, Thiziri Belkacem
arXiv:2609.10046 (v1 2026-09-09, v2 2026-09-11), cs.IR · https://arxiv.org/abs/2609.10046 ·
**verified** (abs page, abstract quoted verbatim)

**The single most important entry for the transferable-protocol question.**

- Verbatim from the abstract: "RAG and recent hybrid or semi-extractive approaches … **do not
  guarantee that quoted or extracted spans are verbatim from the retrieved context**. … Results show
  that **existing hybrid methods frequently hallucinate quoted spans, with exact extraction accuracy
  dropping below 40% in technical domains**. In contrast, our approach achieves near-perfect
  extraction faithfulness regardless of the model used."
- Their fix is **Constrained Hybrid Decoding (CHyD)**: hard decoding constraints restricting
  generation to continuous spans present in the retrieved documents, repurposing speculative-decoding
  machinery. Guarantee: "any explicitly quoted span in the output appears verbatim in the provided
  context."
- Acknowledged trade-off: hard constraints cost fluency-oriented metrics while improving exact
  answer correctness.
- Evaluated on abstractive, extractive and semi-extractive QA including aircraft-maintenance
  technical data.
- **Baseline check:** the "<40%" is the *baseline* (existing hybrid methods) in the hardest domain,
  and the improvement is to near-perfect — so the dramatic figure is a floor on the prior art, not a
  ceiling on theirs. Still, "below 40%" is domain-specific and the abstract does not give the
  general-domain number; do not generalise it.

**Weight:** Highest for the protocol question. **The quote-hallucination failure mode is already
documented and named**, with a quantified failure rate and a published fix. Our contribution can no
longer be "models hallucinate quotes"; it must be the *comparison of elicitation formats*
(short quote anchor vs. whole-sentence anchor) at the prompt level, versus their decoder-level
constraint — a cheaper, model-agnostic, API-compatible alternative that they do not test.

### `zhang-2026-clinical-verbatim`
**Verifiable by Construction: Claim-Level Evaluation of Verbatim Citation in Clinical Question
Answering** — Jiashuo Zhang, Yuling Chen, Yvonne Commodore-Mensah, Michael Oberst
arXiv:2609.15964v2 (v1 2026-09-14, v2 2026-09-17), cs.CL · https://arxiv.org/abs/2609.15964 ·
**verified** (arXiv API, abstract verbatim)

- Abstract, verbatim in part: "we evaluate the ability of current models to perform this task
  end-to-end: from providing citations for every factual claim, to producing verbatim quotes, to
  ensuring that those quotes fully substantiate the claims. … We find that **most models can attach
  verbatim quotes to over 90% of their claims from prompting alone**, apart from some lightweight
  models such as claude-haiku-4.5. Yet these quotes often fail to substantiate every detail of the
  claims they accompany. For instance, **claude-opus-5 produces verbatim quotes for 98.0% of its
  claims, but fully substantiates only 37.1%.**"
- Harness: four clinical practice guidelines, twelve LLMs, 222 synthetic clinical questions, three
  stages scored separately.
- **Verbatim is defined in tiers** (read from the v2 HTML): *exact* character-for-character;
  *normalized* (Unicode normalisation, lowercasing, quote/dash conversion, whitespace collapsing,
  marker removal); *elided* (ellipsis permitted mid-quote, fragments in original order, each
  fragment ≥ 10 characters). No minimum or maximum quote length is imposed on the model.
- **Number discrepancy — flagged.** The abstract gives 98.0% verbatim / 37.1% substantiated for
  claude-opus-5; a separate read of the v2 HTML tables returned 100.0% VCR / 37.8% strict support
  for the same model, with a per-model VCR range of 66.2%–100.0% and strict support 33.8%–79.0%.
  These two readings are inconsistent. **Quote only the abstract figures unless the tables are
  re-read directly.**

**Weight:** High, and it **complicates** the quote-hallucination claim. With frontier models,
permissive normalisation and unconstrained quote length, verbatim compliance is already >90% for
most models — the residual problem they measure is *substantiation*, not fidelity. Our result must
therefore be stated with its conditions attached (which models, which quote length, which matching
rule); a blanket "models hallucinate quotes" is contradicted here for the strongest models.

### `weller-2024-according-to`
**"According to . . . ": Prompting Language Models Improves Quoting from Pre-Training Data** —
Orion Weller, Marc Marone, Nathaniel Weir, Dawn Lawrie, Daniel Khashabi, Benjamin Van Durme
*EACL 2024*, pp. 2288–2301 (also arXiv:2305.13252) · https://aclanthology.org/2024.eacl-long.140/ ·
**verified**

- Introduces **QUIP-Score**, measuring how much of a model's output is found verbatim (n-gram
  overlap) in an underlying corpus; shows *grounding prompts* ("according to Wikipedia…") raise it
  on Wikipedia, PubMed and the US tax code, and that anti-grounding prompts lower it.

**Weight:** Prior art that **a prompt-level instruction changes verbatim fidelity**, with a metric
for it. This is the closest published analogue to "the elicitation format determines whether the
model copies" — our whole-sentence anchor is a different lever pulling the same mechanism, and this
must be cited as the precedent for prompt-controlled quoting.

### `zhang-2024-quote-tuning`
**Verifiable by Design: Aligning Language Models to Quote from Pre-Training Data** — Jingyu Zhang,
Marc Marone, Tianjian Li, Benjamin Van Durme, Daniel Khashabi
arXiv:2404.03862v4 (v1 2024-04-05, updated 2025-02-22); NAACL 2025 ·
https://arxiv.org/abs/2404.03862 · **verified** (arXiv API, abstract verbatim)

- Quote-Tuning: a fast membership-inference function verifies text against trusted corpora and
  becomes a reward for preference learning. "Quote-Tuning significantly increases **verbatim quotes
  from high-quality documents by up to 130% relative to base models** while maintaining response
  quality."
- **Baseline check:** "up to 130% relative" — the absolute base rate is not in the abstract, so the
  multiple is uninterpretable as stated. Do not quote the 130% without the base numbers from the
  paper body.
- Philosophy, verbatim: "trivializing the verification process by developing models that quote
  verbatim statements from trusted sources."

**Weight:** Establishes training-time alignment toward quoting as a verifiability strategy — the
third rung (after prompting and constrained decoding) of the quote-fidelity ladder. Confirms the
community already treats "make it quote exactly" as the route to verifiability.

---

## Contradicts or complicates us

Ordered by how much damage each could do in review.

1. **`kamoi-2023-wice` already measured exact-set agreement on which sentences, at five readings
   per item, over long documents — and called ~50% "high."** SUPPORTED cases: 56.1% exact-set match;
   PARTIALLY-SUPPORTED: 34.4%. They also retain multiple gold sets *because* "different annotators
   can annotate different sets of supporting sentences that include sufficient information," and
   they explicitly *refuse the union* at subclaim level on the grounds that different sets can carry
   identical information. Any framing of "nobody has measured whether the span set reproduces" is
   false. What survives: they never repeat the *same* reader, never look at growth as a function of
   the number of readings, never compute a reliability coefficient, and never compare a graded
   per-sentence score against the set. We must cite WiCE up front, quote the 56.1/34.4, and say
   plainly that our contribution is the *reliability* framing (test–retest of one reader, growth of
   the union, graded vs. binary), not the discovery of disagreement.

2. **`thorne-2018-fever`'s super-annotator study is a union-growth result in all but name.**
   Regular annotators hit 95.42% precision but only 72.36% recall against unlimited-effort
   exhaustive annotation; one claim gained 34 sentences of evidence; the paper states outright that
   "it is not feasible to ensure that the evidence selection annotations are complete," and the
   error analysis found systems producing unannotated-but-valid evidence on 21.85% of claims. And
   `thorne-2018-fever-sharedtask` shows the field's response: score against *any one* annotated set
   and accept the incompleteness. So "the union does not converge" is, at minimum, strongly
   foreshadowed by 2018. Our differentiator is the *curve* — that the set keeps growing with
   readings rather than saturating — which FEVER's single regular-vs-super comparison cannot show.

3. **`kwiatkowski-2019-nq` is a 25-reading study of where the evidence is.** §4.4 states that
   annotators disagree "about the location of the best answer" and that "in many cases there are
   multiple valid long answers in multiple distinct locations on the page" (7 distinct passages for
   one question; 11 distinct correct short answers for another). Even with a tie-breaking
   instruction ("select the earliest instance") the disagreement persists. They quantify it as
   popularity mass (top long answer = 83% of annotations) rather than as reliability. A reviewer who
   knows NQ will ask what our design adds over 25-way annotation; the answer must be the
   graded-score reliability and the reader-identity control, and it must be ready.

4. **`muscato-2026-disagreeing-rationales` argues that soft rationale representations beat hard sets
   under variation** — the same directional claim as "a graded per-sentence score reproduces where
   the set does not," reached on token-level hate-speech rationales through a metric-sensitivity
   study. Recent (2026-05) and easy to miss. Must be cited; our distinction is that they compare
   *metrics* under variation while we compare *reproducibility* of two readouts from the same
   readings.

5. **`deyoung-2020-eraser` and `carton-2020-human-rationales` together establish non-uniqueness.**
   ERASER: token-level κ 0.62–0.85 between annotators, "not necessarily comprehensive" rationales,
   an any-of-the-gold IOU matching rule, and a deliberate re-collection of *comprehensive*
   rationales precisely because the first pass was known to be partial. Carton: human rationales
   fail fidelity metrics and are "non-comprehensive because they miss redundant-yet-relevant
   information." Neither closes the question we are asking, but between them "humans disagree about
   which span while agreeing a span exists" is established prior art and must be positioned against,
   not claimed.

6. **`wadden-2020-scifact` gives a directly comparable per-sentence agreement number** (Cohen's
   κ = 0.71 on sentence-level rationale membership, from genuine double annotation of 232 pairs) and
   a two-level any-of-the-gold-sets scoring rule. If our per-sentence agreement is in that
   neighbourhood, the interesting claim is about the *set*, not the sentence.

7. **`xu-2023-rave` and `jakobsen-2023-whose-right-reasons` supply the competing explanation.**
   Weak expert-to-expert agreement on legal rationales with a taxonomy attributing it to
   underspecification; systematic *demographic* structure in which span is chosen. Both frame
   span variation as legitimate perspectival difference rather than as measurement instability. We
   need an argument for why a single model re-reading a single document at fixed settings is a
   reliability phenomenon and not a perspectivist one — the two hypotheses predict different things
   about whether a majority-vote aggregate stabilises.

8. **`wang-2026-granularity` argues the opposite of "use whole sentences."** Enforcing
   sentence-level citation units degrades citation F1 relative to paragraph-level, worst for the
   largest models, because "fine-grained citations disrupt necessary semantic dependencies." This is
   about constraining *generation*, not about eliciting *anchors*, but the headline reads as
   "sentences are the wrong unit" and will be cited against us. Note also that its own headline
   range changed from "2–97% (median 40%)" in v1 to "16–276%" in v2 — grounds to treat the multiple
   sceptically and to engage with the mechanism rather than the number.

9. **`zhang-2026-clinical-verbatim` complicates the quote-hallucination premise.** With frontier
   models, tiered/normalised matching and no length constraint, over 90% of claims carry verbatim
   quotes; the failure they find is substantiation (claude-opus-5: 98.0% verbatim, 37.1% fully
   substantiating). Our quote-hallucination finding therefore needs its conditions stated
   explicitly — model tier, anchor length, and exact-match rule — or it will read as contradicted.

10. **`gao-2023-alce` deliberately tolerates redundant citations** ("does not require citing a
    minimal set"), so the field's most-used citation metric is by design insensitive to *which* of
    several adequate passages was cited. That is a point in our favour, but it also means "ALCE
    doesn't measure this" is a design choice they defend, not an oversight — argue against the
    choice, not the omission.

**Not found, and therefore still open:** no paper we retrieved repeats the *same* annotator or the
*same* model on the *same* item to measure test–retest stability of span selection; none reports a
split-half / Spearman-Brown reliability coefficient for evidence location; none models the union of
ever-marked sentences as a function of the number of readings.

---

## The transferable-protocol question

**Is the quote-anchor hallucination failure documented? Yes — squarely, and recently.**

- `signe-2026-chyd` states it as a headline: hybrid/semi-extractive RAG systems "frequently
  hallucinate quoted spans, with exact extraction accuracy dropping below 40% in technical
  domains." So the failure mode is named, quantified and published.
- Their fix is at the **decoder**: hard constraints restricting output to continuous spans of the
  retrieved context, with an absolute guarantee and a fluency cost.
- `zhang-2026-clinical-verbatim` measures the same property (verbatim compliance rate) at the
  **prompt** level with tiered matching, and finds it mostly solved for strong models on guideline
  text — so the failure is model- and domain-dependent, not universal.

**Is the whole-sentence fix documented? Not as a studied intervention, but adjacent work exists.**

- `weller-2024-according-to` is the precedent that **a prompt-level instruction moves verbatim
  fidelity**, with QUIP-Score as the measurement. It changes *whether* the model grounds, not the
  *unit* it is asked to reproduce.
- `zhang-2024-quote-tuning` moves the same dial at training time (+"up to 130%" verbatim quoting,
  base rate unstated).
- `zhang-2025-longcite` arrives at a functionally equivalent interface — cite **sentence indices**
  into a pre-segmented context instead of reproducing text — but presents it as a data/format choice
  for long-context QA and never compares it against quoting, so there is no measurement of what the
  format buys.
- Engineering practice has converged on the same idea without publishing it as a result: at least
  one tooling paper describes a "hallucination defence that discards any evidence quote it cannot
  locate verbatim" (ATIBA, arXiv:2609.04123 — surfaced in an arXiv full-text query, **not
  independently verified**, listed here only as evidence that the practice exists).

**Verdict.** The *failure* is known; the *unit-of-anchor* manipulation is not. No retrieved paper
varies the requested anchor granularity (short n-word quote vs. whole sentence vs. sentence index)
and measures the resulting fidelity rate. That controlled comparison — cheap, prompt-level,
model-agnostic, no decoder access — appears to be genuinely unreported, and it sits between
`weller-2024-according-to` (prompting changes grounding) and `signe-2026-chyd` (constrained decoding
guarantees it). Position the contribution there, cite both, and do not claim discovery of quote
hallucination.

---

## Could not verify

- **BioMedSumm inter-annotator agreement of 21.7%** on identifying spans in a cited document
  relevant to a citation (attributed by `wadden-2020-scifact` §7 to Cohen et al., 2014, 314
  citations over 20 papers). This is the lowest span-location agreement figure encountered anywhere
  and it would be valuable — but it is a **secondary-source number**; the primary paper was not
  retrieved. Queries: it surfaced only inside the SciFact PDF. Do not cite until Cohen et al. (2014)
  is fetched.
- **ACL Anthology identifier for `xu-2023-rave`.** The arXiv v5 comment says "Accepted to EMNLP
  2023"; Semantic Scholar returned HTTP 429 on two attempts and dblp's API returned an
  access-denied interstitial. Title, full author list and abstract are verified from the arXiv API.
  Cite as arXiv until the anthology ID is confirmed.
- **Per-model table numbers in `zhang-2026-clinical-verbatim`.** Two independent retrievals
  disagreed (abstract: 98.0% verbatim / 37.1% substantiated for claude-opus-5; v2 HTML tables read:
  100.0% VCR / 37.8% strict). Only the abstract figures are safe to quote.
- **Granularity and annotator-agreement details of `sancheti-2024-posthoc-attribution`.** The abs
  page does not state them and the PDF was not opened.
- **`kiritchenko-2016-bws` (N16-1095) abstract.** The anthology landing page renders metadata only;
  title, authors, venue, pages verified, abstract not.
- **`wang-2026-granularity` headline range.** v1 and v2 abstracts state incompatible ranges
  (2–97% vs 16–276%). Neither was cross-checked against the paper's own tables; the only concrete
  pair obtained (0.398 → 0.677) came from a single automated read of the v2 HTML.
- **`kwiatkowski-2019-nq` full author list** was taken from the anthology/TACL record rather than
  transcribed from the PDF header; re-check at typesetting.

---

## Searched and did not find

Queries run and what came back. Note: the session's WebSearch budget (200 calls, shared) was
exhausted partway through; later discovery used the **arXiv API** and **Semantic Scholar/dblp**
through WebFetch instead. Anything below marked *(arXiv API)* searched arXiv full text/abstracts
only and would not surface ACL-only work.

- *(arXiv API)* `abs:"evidence selection" AND abs:"stability"` — 7 hits, all irrelevant:
  financial-QA reranking, a mathematics paper on context localisation, bioinformatics agents, a
  nuclear-physics EOS paper, tabular prediction, fake-news retrieval, and vision attribution under
  geometric transforms. **Nothing on the stability of which evidence sentences get selected across
  repeated runs.**
- *(arXiv API)* `abs:"LLM" AND abs:"annotation" AND abs:"reproducibility"` — 20 hits, a substantial
  social-science literature on LLM-annotation reliability (SILICON, variance-aware protocols,
  inter-prompt reliability, ensemble/majority-vote stabilisation, "LLMs achieving comparable
  accuracy still disagree on 16–66% of items"). All of it concerns **labels**, none concerns
  **spans or rationales**. Potentially citable as the label-side analogue if we want it; none
  verified beyond the API listing, so none is entered above.
- *(arXiv API)* `abs:"rationale" AND abs:"disagreement" AND cat:cs.CL` — 14 hits; the four worth
  keeping are entered above (`muscato-2026`, `xu-2023-rave`, `jakobsen-2023`, `sarumi-2026`). The
  rest are perspectivist modelling, moral-judgement alignment, audit tooling and active learning.
  **None measures test–retest of a single annotator or model on the same item.**
- *(arXiv API)* `all:"hallucinated quotes" OR all:"quote hallucination" OR all:"verbatim quote"` —
  12 hits. Two are load-bearing and entered above (`signe-2026-chyd`, `zhang-2026-clinical-verbatim`).
  The rest are applications that *use* verbatim quoting as a grounding device (legal case-law quote
  extraction, chemistry literature synthesis, EU regulatory consultation analysis, SEC 8-K event
  extraction, paper-integrity checking, multi-document summarisation provenance, quote-search
  evaluation) or copyright work. **No paper manipulates requested quote length / anchor unit as an
  independent variable.**
- *(WebSearch, before budget exhaustion)* `LLM cite exact quote "not found in the document"
  fabricated quotation evaluation study verbatim span copy error rate` — blocked by budget; the
  earlier related query returned only blog-level material (a "Deterministic Quoting" engineering
  post) plus general hallucination surveys. **Worth re-running when budget resets.**
- **Never found:** a reliability coefficient (split-half, Spearman-Brown, test–retest, ICC) reported
  for *evidence span selection* by any annotator or model. `kiritchenko-2017-bws` supplies the
  method in a different domain; nobody appears to have applied it to evidence location.
- **Never found:** any study modelling the **growth of the union** of marked evidence sentences as a
  function of the number of independent readings. FEVER's super-annotator comparison
  (`thorne-2018-fever`) and ERASER's comprehensive re-annotation (`deyoung-2020-eraser`) are the two
  places where the phenomenon is acknowledged, and both treat it as a one-off data-collection
  correction rather than a curve to be measured.
