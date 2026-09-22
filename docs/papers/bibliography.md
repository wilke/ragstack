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

## How these fit together

The published record does **not** say semantic chunking fails. It says:

- naive local-similarity breakpoint detection has not earned its cost (`qu-2025`, weakly
  `kreileder-2026`), and
- *informed* segmentation — guided by a summary, or learned, or domain-trained — reports gains
  (`wang-2025-pic`, `zhao-2025-moc`, and `allamraju-2025` if one accepts its baselines).

Our chunker belongs to the first family, and our result is consistent with it. We have not
tested the second. **No paper in this set controls realised chunk size across methods**, which
is where our contribution sits.
