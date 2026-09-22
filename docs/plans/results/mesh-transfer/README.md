# `mesh-transfer/` — does a topic label transfer to an unseen microbiology journal?

**One run, 2026-09-15, exploratory.** Not part of the Phase-0 chunking series that the rest
of [`results/`](../README.md) records — it answers a question from the metadata / knowledge-graph
track, and it is filed here because this is where measurements live.

[`RESULTS-mesh-label-transfer.md`](RESULTS-mesh-label-transfer.md) is the write-up. Its
harness and logs ship with it, so every number is reproducible from the corpus in about six
minutes on one idle GPU.

## The question

RAGStack wants one consistent topic label space over a 1.44M-document open-access corpus.
~79% of documents carry curated MeSH headings and the rest do not — and **the gap is
journal-structured, not random**: the microbiology/AMR journals the owner most cares about
(*Frontiers in Microbiology*, *Microorganisms*, *Antibiotics*) carry **no MeSH at all**.
Those are precisely where a label would have to be predicted, and precisely where it can
never be scored.

So the experiment uses *Frontiers in Cellular and Infection Microbiology* — MEDLINE-indexed,
therefore has ground truth — as a proxy: hold the journal out of training entirely, predict
it, and **measure the gap against a random in-distribution holdout**. The gap is the finding.

## The answer

**Yes, it transfers.** A linear probe on 768-dim `bge-base-en-v1.5` embeddings of
title+abstract loses **0.006 micro-F1** (0.596 → 0.591, 95% CI [−0.018, +0.006]) going from
a random holdout to the held-out journal. Handing it 1,479 labelled documents *from that
journal* recovers only +0.032, so the journal boundary is not what limits quality. kNN label
propagation is worse on every metric and 10⁴× more expensive at inference. Macro-F1 on a
matched label set degrades −0.037, and the labels that break are MeSH *check tags* about study
population (`Male`, `Aged`, `Adult`) rather than microbiology subject matter.

Embedding the whole 1.44M corpus costs **9.3 minutes** on one H200; 10M costs ~1 GPU-hour.

## Read §8 before quoting the "yes"

The proxy is **not** the target. *Frontiers in Microbiology* is not MEDLINE-indexed and this
experiment cannot say whether that reflects a content difference or an indexing-policy
difference. It is also **n = 1 held-out journal**, the training set is not
microbiology-*free*, and the 121-label vocabulary is an artifact of the sample size — the
macro trend suggests the gap widens for rarer labels, which a production-size vocabulary
would have many more of. §8 lists ten things the run could not determine.

Separately: absolute quality is moderate and is a **different question** from transfer.
Micro-F1 ≈ 0.6 inside a 121-label space, and ~0.25 recall against a document's real MeSH
headings. Whether that is good enough for anything downstream is untested here.

## Files

| file | what it is |
|---|---|
| [`RESULTS-mesh-label-transfer.md`](RESULTS-mesh-label-transfer.md) | the write-up |
| [`report.json`](report.json) | machine-readable headline metrics, chosen hyper-parameters, the 121-label vocabulary |
| [`run.log`](run.log) | main run — every sweep cell on dev **and** on both test sets |
| [`run2.log`](run2.log) | the seen/unseen control, bootstrap CIs, cost timings |
| [`fetch_mesh.py`](fetch_mesh.py) [`extract_text.py`](extract_text.py) [`embed.py`](embed.py) [`experiment.py`](experiment.py) [`experiment2.py`](experiment2.py) | the harness, in run order |

Intermediates (`sample.jsonl`, `mesh.jsonl`, `fetched.jsonl`, `labelled.jsonl`, `emb.npy`)
are not committed; the first three scripts regenerate them from `/rag/oa/corpus/` and NCBI.
