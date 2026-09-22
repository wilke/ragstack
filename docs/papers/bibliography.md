# Bibliography

Every entry here was fetched and read before it was listed. Each records what the paper
**actually establishes**, what it controlled for, and how much weight it can carry — not what
its abstract claims. See the `study-writeup` skill for the verification rule.

Status key: **verified** = title, authors, venue and year confirmed against the publisher or
arXiv page. **read** = the full text was retrieved and the methods checked, not just the
abstract.

---

## Chunking and segmentation for retrieval

### `qu-2025` — Is Semantic Chunking Worth the Computational Cost?
Renyi Qu, Ruixuan Tu, Forrest Sheng Bao. Findings of the ACL: NAACL 2025.
<https://aclanthology.org/2025.findings-naacl.114/> · **verified, read (full PDF)**

Compares fixed-size against breakpoint-based and clustering-based semantic chunkers on three
tasks (document retrieval, evidence retrieval, answer generation) over ten datasets from BEIR
and RAGBench. Concludes the computational cost of semantic chunking is not justified by
consistent gains.

**What makes it useful to us is not the headline.** Sweeping the semantic chunkers'
hyperparameters, they observe that the hyperparameter acts *through* chunk size — "Regardless
of the threshold type, it ultimately determines chunk size" — and that as their positional
weighting parameter approaches the fixed-size case the score rises, which they read as
positional information mattering more than semantic similarity. That is our realised-size
result reached from a different direction, and it is the right thing to cite for it.

**Their stated limitations name three gaps our work fills**, which is the strongest available
framing for our contribution:
- sentence embeddings are context-free, and "further exploration of contextual embeddings is
  necessary before definitively concluding the limitations of semantic chunking";
- "Lack of Chunk Quality Measures" — they had no ground-truth query-chunk relevance and fell
  back on document or evidence mapping, which is exactly our passage-level endpoint;
- "Lack of Suitable Datasets" — an ideal dataset "would include long documents", and they
  excluded corpora whose documents were too short. That is our BEIR finding.

**Weight: strong**, and complementary rather than duplicative. Note their chunk-size
hyperparameter is *number of chunks*, not tokens, and they do not report realised token
lengths, so they share our confound rather than having controlled it.

### `wang-2025-pic` — Document Segmentation Matters for Retrieval-Augmented Generation
Zhitong Wang, Cheng Gao, Chaojun Xiao, Yufei Huang, Shuzheng Si, Kangyang Luo, Yuzhuo Bai,
Wenhao Li, Tangjian Duan, Chuancheng Lv, Guoshan Lu, Gang Chen, Fanchao Qi, Maosong Sun.
Findings of the ACL 2025. <https://aclanthology.org/2025.findings-acl.422/> · **verified**

Proposes PIC (Pseudo-Instruction for document Chunking): use a document summary as a
pseudo-instruction, compute sentence-to-summary semantic similarity, group sentences
dynamically. Reports Hits@k and end-to-end exact-match gains on open-domain QA with no
additional training.

**Weight: genuine positive**, and the mechanism matters for how we cite it. PIC is *informed*
segmentation — guided by a document-level summary — not naive local-similarity breakpoint
detection. It is evidence for the second of the two ideas that share the name "semantic
chunking", not the first.

### `zhao-2025-moc` — MoC: Mixtures of Text Chunking Learners for Retrieval-Augmented Generation System
Jihao Zhao, Zhiyuan Ji, Zhaoxin Fan, Hanyu Wang, Simin Niu, Bo Tang, Feiyu Xiong, Zhiyu Li.
ACL 2025 (long). <https://aclanthology.org/2025.acl-long.258/> · **verified**

Introduces chunking-quality metrics and a granularity-aware Mixture-of-Chunkers framework.
Argues both traditional rule-based and simple semantic chunking are limited, and proposes
learned chunkers instead.

**Weight: positive, supporting the same distinction as `wang-2025-pic`** — the approaches that
win are learned or guided, not local-similarity thresholds.

### `kreileder-2026` — Evaluating Chunking Strategies for Retrieval-Augmented Generation on Academic Texts
Valentin J. J. Kreileder, Johannes Reisinger, Andreas Fischer. arXiv 2607.01852, 2 July 2026.
<https://arxiv.org/abs/2607.01852> · **verified**

Cluster-based semantic chunking against fixed-size and recursive chunking on academic theses,
evaluated with RAGAs. Finds cluster-based chunking did not outperform the simpler strategies.

**Weight: weak. Cite with its caveats or not at all.** The documents are theses rather than
journal articles; the evaluation framework is RAGAs, and the authors themselves report that
"RAGAs based faithfulness shows limited reliability in this setup"; and the result is a null
with no power analysis. It is consistent with `qu-2025`, not independent confirmation of it.

### `allamraju-2025` — Breaking It Down: Domain-Aware Semantic Segmentation for Retrieval Augmented Generation
Aparajitha Allamraju, Maitreya Prafulla Chitale, Hiranmai Sri Adibhatla, Rahul Mishra,
Manish Shrivastava. arXiv 2512.00367, 29 November 2025.
<https://arxiv.org/abs/2512.00367> · **verified, read (full PDF)**

Two trained semantic chunkers (Projected Similarity Chunking, Metric Fusion Chunking) trained
on PubMed Central full text, evaluated on PubMedQA augmented with full-text articles. Abstract
reports a 24× improvement in MRR with PSC and higher Hits@k.

**Weight: very weak as evidence for semantic chunking, despite being the closest paper to our
domain. Do not cite the 24× without the baseline.** Read the results table: the Character,
Sentence and Recursive baselines score **0.0** MRR and the semantic baseline scores **0.01**,
so the 24× multiple is measured against a baseline that essentially never retrieves the gold.
The reason is visible in the construction — the evaluation gold is the human-authored context
sentences from PubMedQA, and their chunkers are trained on positive pairs defined as sentences
the human author placed in the same section, so the method is trained to reproduce the
structure that defines the gold while fixed-size chunkers cut across it. The authors concede
the gold "is human-authored" for both sides. Retrieval is evaluated on **115 queries**, the
only PQA-L items with full-text matches. Their chunks average 471 tokens and the baselines'
realised sizes are not reported, so the realised-size confound is present and uncontrolled.

This entry is the project's worked example of why a citation gets read before it gets listed.

---

## Annotation agreement and evidence localisation

These three are cited by `paper-a-evidence-localisation/OUTLINE.md` and were, until
2026-09-22, in neither this file nor any `bib-inbox/` survey — i.e. recalled, not verified, which
is the failure the `study-writeup` skill exists to prevent. Each was fetched on 2026-09-22; the
status line says how far it was read.

### `hofstatter-2020-fira` — Fine-Grained Relevance Annotations for Multi-Task Document Ranking and Question Answering
Sebastian Hofstätter, Markus Zlabinger, Mete Sertkan, Michael Schröder, Allan Hanbury.
CIKM 2020. <https://doi.org/10.1145/3340531.3412878> · arXiv 2008.05363 · **verified, read (full
PDF, §5 "Annotation quality")**

Extends TREC-DL 2019 document ranking with passage- and word-level graded relevance for all
relevant documents (FiRA), and reports inter-annotator agreement as Cohen's κ **of each student
annotator against the majority-vote aggregate**, on the 10 query–document pairs every student
annotated. Their own numbers, verbatim: for 2-class relevance and for "the labeling of the relevant
word phrases, substantial Kappa agreements are reached, ranging from 0.5 to 0.8"; for 4-class
grading "a mediocre agreement ranging from 0.3 to 0.6".

**What it establishes for us:** a published measurement in which agreement on *where* the evidence
is (word selection) is **as high as** agreement on *whether* the document is relevant (2-class).
That is the counter-result to our headline and Paper A must engage it. **It is not** an "opposite
ordering" — location does not out-agree presence here; they tie — and an earlier draft of the
outline mis-stated this. Weight: **moderate, with stated blunting factors** — κ against an
aggregate that contains the rater being scored inflates agreement; n = 10 pairs; non-expert
student annotators; only documents already judged relevant were annotated, so the presence
condition is easier than ours. They themselves note agreement in line with Alonso & Mizzaro for
non-expert TREC labelling. Cite it as the published tie, then say why our design differs.

### `mathew-2021-hatexplain` — HateXplain: A Benchmark Dataset for Explainable Hate Speech Detection
Binny Mathew, Punyajoy Saha, Seid Muhie Yimam, Chris Biemann, Pawan Goyal, Animesh Mukherjee.
AAAI 2021. <https://doi.org/10.1609/aaai.v35i17.17745> · arXiv 2012.10289 · **verified (title,
authors, venue, year, abstract); not read past the abstract**

Cited only as prior art for a **soft per-token rationale** — each post annotated by several
raters with the token spans their label rests on, aggregated into a per-token score. That is the
representation the outline's "not novel" table needs it for, and the abstract establishes it.
Weight for that purpose: **mention, not support**. Do not cite it for any number.

### `warfield-2004-staple` — Simultaneous Truth and Performance Level Estimation (STAPLE): An Algorithm for the Validation of Image Segmentation
Simon K. Warfield, Kelly H. Zou, William M. Wells. IEEE Transactions on Medical Imaging 23(7),
2004. <https://doi.org/10.1109/tmi.2004.828354> · **verified (title, authors, venue, year,
abstract); not read past the abstract**

The canonical **probabilistic per-unit reference estimated by EM over multiple raters** — a
per-voxel truth estimate plus per-rater sensitivity/specificity, from segmentations that
individually disagree. Cited as the representational ancestor of a graded pooled per-sentence
support map, which is all the "not novel" table claims for it. ~2,100 citations (OpenAlex).
Weight for that purpose: **mention, not support**.

## How these fit together

The published record does **not** say semantic chunking fails. It says:

- naive local-similarity breakpoint detection has not earned its cost (`qu-2025`, weakly
  `kreileder-2026`), and
- *informed* segmentation — guided by a summary, or learned, or domain-trained — reports gains
  (`wang-2025-pic`, `zhao-2025-moc`, and `allamraju-2025` if one accepts its baselines).

Our chunker belongs to the first family, and our result is consistent with it. We have not
tested the second. **No paper in this set controls realised chunk size across methods**, which
is where our contribution sits.
