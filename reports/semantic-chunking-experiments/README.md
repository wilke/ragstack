# Semantic-chunking experiment reproducers

The four scripts here produced the numbers in
[`../semantic-chunking-experiments.md`](../semantic-chunking-experiments.md). They live
beside that write-up rather than in `python/scripts/eval/` because they are **archived
reproducers, not part of the eval harness**: nothing imports them, no CWL step runs them,
and they answer questions that are already answered.

Keeping them is deliberate. The write-up records *conclusions* — that the semantic
chunker's cost is CPU-bound in sentence segmentation, that a cheap breakpoint model
tracks an expensive one closely enough on boundary agreement. A conclusion is only as
durable as the hardware and models it was measured on, and re-deriving one on new GPUs or
a new candidate model needs the measurement, not the number.

| Script | What it measures |
|---|---|
| `bge_gpu_bench.py` | Embedding GPU throughput, no HTTP in the path |
| `embed_speed.py` | Breakpoint-model candidates' throughput over real sentence buffers |
| `breakpoint_model_compare.py` | Cheap vs expensive breakpoint model — chunk-span Jaccard, boundary F1, Spearman |
| `profile_semantic_cpu.py` | Semantic-chunker CPU cost, isolated with a mock embedder |

## Running one

Three of them import `ragstack`, so the package has to be importable — `make
install-python` (an editable install) is enough, and then the working directory does not
matter:

```bash
python reports/semantic-chunking-experiments/profile_semantic_cpu.py
```

`bge_gpu_bench.py` additionally needs `torch` + `transformers` and a GPU; it is the only
one with dependencies outside the project's own.

The original measurements were taken on `coconut` (8× H200 NVL) on 2026-07-01 — see the
provenance table at the top of the write-up for the exact commit and model versions.
