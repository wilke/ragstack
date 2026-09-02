# Ingesting the PMC Open Access subset

**Status:** `PROPOSED`. Goal: every OA article about **bacteria** or **viruses**, reached by
downloading the whole OA subset as JATS XML and filtering at parse time.

---

## The shape

```
PMC Cloud (AWS Open Data)  ──►  compressed JATS archives on disk  ──►  parse-time filter  ──►  embed
        ~223 GB gzip                    keep compressed              PMCID allowlist        ~16M chunks
```

Filtering at parse rather than at download is the right call: source is cheap relative to
indexing, and holding the whole subset means a later topic can be added without
re-downloading.

---

## Storage — measured, not estimated

From our own 1,439,753-article corpus: **178 GB uncompressed, 130 KB/article**, and on a
400-file sample **4.5× under gzip, 6.7× under zstd-19**.

| | 8,186,181 articles |
|---|---|
| uncompressed on disk | **1,012 GB** |
| gzip | **~223 GB** |
| zstd-19 | **~150 GB** |

**Keep it compressed.** Expanding it the way `/rag/oa/corpus/clean/` is laid out today costs
1 TB on a volume that is already 66% full (1.3 TB free of 3.9 TB). The archives are the
storage format; decompression happens in the parser.

**What actually gets embedded is far smaller.** The target is the topic union, not the whole
subset:

| | articles | chunks | fp32 4096-dim | 1024-dim + int8 |
|---|---|---|---|---|
| all OA | 8.19M | 271M | 6.1 TB | 2.3 TB |
| **bacteria ∪ viruses** | **~498k** | ~16M | **~0.5 TB** | ~0.2 TB |
| broadened (+ infections, + virus diseases) | ~650k | ~21M | ~0.7 TB | ~0.25 TB |

At ~500k articles the embedding-model decision stops being a blocker and becomes an
optimization — 0.5 TB fits the free space today.

---

## Where to get it — the FTP path is being retired

`https://ftp.ncbi.nlm.nih.gov/pub/pmc/readme.txt`, updated **2026-08-25**:

> All legacy files for the PMC Article Datasets are in the process of being removed from the
> FTP Service. […] All files are now available via the updated PMC Cloud Service.

Probing from this host confirms it: `oa_bulk/` and `oa_file_list.csv` both **404**, and the
directory listing returns only `PMC-ids.csv.gz` and `readme.txt`.

**Use the AWS Open Data channel — which is already what we use.** Our existing corpus'
`source_url` values are `https://pmc-oa-opendata.s3.amazonaws.com/<PMCID>.<ver>/…`, so the
current pipeline is already on the supported path. No login, HTTPS or S3.

> **Prerequisite, not yet done:** confirm the exact bucket layout, the licence-tier split
> (`comm` / `noncomm` / `other`) and the incremental-update mechanism against
> `https://pmc.ncbi.nlm.nih.gov/tools/cloud/`. I could not enumerate it from this host — the
> FTP listing is filtered here — and **no path in this plan should be treated as verified.**

---

## The topic filter, without needing MeSH locally

The obvious approach — filter on MeSH while parsing — **does not work**: MeSH is a PubMed
field and is **not in the JATS XML**. Our own 250-file sample found no MeSH anywhere.

Instead, resolve the topic to a **PMCID allowlist once, up front**, and apply it while
streaming the archives:

```
esearch db=pmc term='(bacteria[MeSH] OR viruses[MeSH]) AND "open access"[filter]'
   → ~497,942 PMCIDs   (paged, or via the history server)
   → an on-disk id set
   → parse an article only if its PMCID is in the set
```

Verified counts (esearch, 2026-08-30):

| query | all PMC | open access |
|---|---|---|
| `bacteria[MeSH]` | 479,846 | **225,939** |
| `viruses[MeSH]` | 464,837 | **283,779** |
| union | — | **497,942** |
| union + `bacterial infections` + `virus diseases` | — | **649,903** |

**The MeSH tree choice moves the target by 30%** — `bacteria[MeSH]` is the organism tree, so
a paper about *treating* an infection may be indexed under `bacterial infections` and not
appear. Decide this deliberately.

This also means the filter is **re-runnable**: a new topic is a new esearch and a re-parse of
archives already on disk, with no re-download.

---

## Compliance: articles withdrawn from the OA subset

The same readme records that COVID-era articles were removed from the OA subset when
publisher licence terms expired, with a list of removed PMCIDs published, and that
**"downstream users should update their datasets accordingly."**

This is an obligation, not a nicety, and nothing in RAGStack currently handles it:

1. Fetch the removed-PMCID list and check it against the 1,439,753 we already hold.
2. Delete any match from the vector store, the text index and the archive.
3. Make it recurring — the same check runs on every incremental update.

There is currently **no mechanism to withdraw a document** from a collection on licence
grounds. That is a gap worth its own issue.

---

## What must land before the load

Doing these after the load means doing 16M+ chunks twice.

1. **Fix #471** — a filter that works on `bm25`, returns nothing on `vector` and looks fine
   on `hybrid`. Every new field inherits it.
2. **Declare the metadata schema with types** ([metadata-and-kg.md](metadata-and-kg.md)).
3. **Extract the fields we currently discard**, all present in the source:
   - `article-type` — JATS `<article @article-type>`, a clean controlled vocabulary
     (`research-article`, `review-article`, `case-report`, `brief-report`, `letter`)
   - `license_code` — from `ali:license_ref`, instead of 400-char truncated prose
   - a **real date**, not just `year` — `pub-date` has day/month/year
   - `section` — `<sec><title>`, present in 96% of sampled files
4. **Payload indexes** in Qdrant for every filterable field. Only `doc_id` and `tenant_id`
   are indexed today; an unindexed filter scans every point.
5. **Decide the embedding model and quantization** — build-time, and unchangeable without
   re-embedding everything.

---

## Chunking: the memory lever is size, not strategy

Measured by the repo's own eval harness (`python/scripts/eval/chunking_compare.py`):

| mode | #chunks | /doc | median chars | ingest s | **chunking s** | recall@5 | nDCG@10 | rerank |
|---|---|---|---|---|---|---|---|---|
| fixed | 394,353 | 103.3 | 512 | 1541.5 | **4.3** | 0.896 | 0.890 | **8.330** |
| sentence | 415,443 | 108.8 | 447 | 1603.1 | 30.3 | 0.898 | 0.884 | 8.279 |
| semantic | **89,556** | 23.5 | **1602** | 3802.8 | **3,214.6** | 0.899 | 0.889 | **7.107** |

Semantic does produce **4.4× fewer chunks** — but its median chunk is **3.1× larger**, and
chunk count is total tokens ÷ chunk size. The boundary algorithm decides *where* the cuts
fall, not how many there are.

**The control in the same repo settles it.** From the 7-way run, with no semantics involved:
`fixed_char512` → 28,112 chunks; `fixed_char2048` → **7,111**. A **4.0× reduction from
changing one number.**

Semantic's cost is not small: the chunking step is **748× more expensive** (4.3 s →
3,214.6 s) because it embeds the text to find boundaries — you pay for embeddings twice —
and total ingest is 2.5× slower. Retrieval quality is a wash (recall@5 +0.003, nDCG −0.001)
except for **mean rerank score, which drops 8.33 → 7.11**: the cross-encoder judging the
retrieved passages a worse match, which is the expected cost of larger chunks. Two
operational hazards come with it: 9,365 chunks hit the token cap, and a prior benchmark
recorded in `reports/chunking-comparison.md:129` found **12% of semantic chunks would
overflow the 4096-token window**.

This matches PR #44's conclusion, already in `STATUS.md`: *"the uniformly-sized modes —
semantic costs most with no quality upside."*

### For the OA target

| chunk size | chunks (~498k articles) | fp32 4096-dim |
|---|---|---|
| 512 tokens | ~16M | ~0.5 TB |
| 1024 tokens | ~8M | ~0.25 TB |
| + drop the 64-token overlap | −12.5% again | |

Bigger chunks buy memory and cost precision — the rerank drop is that cost, measured. Tune
**size** as the lever with recall and rerank score as the guardrail; leave semantic chunking
alone unless something other than storage motivates it.

## Chunking: respect the structure the source already has

### The token counter can silently resize every chunk

`make_token_counter` defaults to **`chars_per_token = 2.5`** for the `estimate` backend, and
`chunker_config` **falls back to `estimate` when a model is unavailable** — it logs, it does
not refuse.

Measured against production: the open-access chunks are 512-token windows whose char lengths
run 1610–2044, i.e. **3.14–3.99 chars/token, mean 3.50**. A 512-token budget evaluated at
2.5 c/t cuts at 1280 chars, which is **366 real tokens — 29% under-filled, ~1.4× more chunks
than intended**.

So a corpus built on a host without the tokenizer gets 40% more vectors than one built with
it, from the same command. **For a corpus-scale build the estimator should be an explicit
opt-in, not a fallback** — the same "make it required rather than defaulted" rule as #454.

### Sentence and paragraph boundaries

Today's production chunker, `FixedTokenWindowChunker`, slides a token window and **cuts
wherever the window ends** — mid-sentence, mid-word. It is exact on token counts and offsets,
which is why it is the baseline, but it has no notion of structure.

The pieces to do better already exist and have never been combined:

| have | gives |
|---|---|
| `SentenceChunker` | *"no chunk ever splits a sentence"*, and packs to a **token** budget when given `max_tokens` + a counter |
| `RecursiveCharacterChunker` | a separator hierarchy — paragraph/line breaks first, then weaker separators |
| `FixedTokenWindowChunker` | exact token accounting and offset mapping |

**And for JATS we should not be inferring paragraphs at all.** The source is marked up:
`<sec>`, `<title>`, `<p>` are real structural units, and `jats.py` already reads section
titles. Chunking *within* structural units — never crossing a section, preferring not to
cross a paragraph — is strictly better than detecting boundaries from newlines in flattened
text.

### Proposed chunker

A **structure-aware token packer**:

1. Split on the source's own structure (JATS `<sec>`/`<p>`; blank-line paragraphs for
   plain text and Markdown).
2. Within a unit, split to sentences (Punkt, as `SentenceChunker` already does).
3. Pack whole sentences to a **real token budget** — never `estimate` for a corpus build.
4. Never cross a section boundary; avoid crossing a paragraph unless a chunk would otherwise
   be under some floor.
5. Keep exact `start_char`/`end_char`, which every existing chunker already maintains and
   which the offset model in [metadata-and-kg.md](metadata-and-kg.md) depends on.

**It must be evaluated, not assumed.** `chunking_compare.py` already produces the numbers
that would decide it — recall@k, nDCG, rerank score, chunks/doc, ingest time — so the new
mode gets added to the 7-way run and stands or falls on the same table. The prediction worth
testing is that respecting boundaries improves the **rerank score** (less irrelevant text per
chunk) at similar chunk counts, which is precisely where semantic chunking lost.

---

## Idempotency and updates

- **Articles are versioned** (`PMC9297083.1`). Re-ingesting must replace, not duplicate — a
  duplicated article reads as corroboration it has not earned.
- **The load is a one-off; staying current is the job.** Plan the incremental path at the
  same time, or the corpus is stale the week after it lands.
- **Retraction status** is not in the JATS. It comes from PubMed's
  `CommentsCorrectionsList` or the OA metadata; serving retracted science as evidence is a
  correctness problem, not a data-quality one.

---

## Open questions

- **Decompress-per-parse cost.** zstd is fast, but a re-chunk re-pays it across 8.19M
  articles. Measure parse+decompress throughput on one shard; it may decide gzip vs zstd on
  decode speed rather than ratio.
- **Archive granularity vs. random access.** Package-level archives are simple but make
  single-article access awkward. If the offset model in
  [metadata-and-kg.md](metadata-and-kg.md) lands, "download all OA" and "store chunk text
  once" become the same operation.
- **How much of the target we already hold.** Unknown: we have no MeSH locally, and the
  full-text counts (422k mentioning "bacteria", 226k "viruses") measure mention, not topic.
  A MeSH pass over our 1.44M PMIDs would say whether this is a ~500k fetch or a top-up —
  and it is cheap enough to do before committing to the download.
