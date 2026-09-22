# hackathon `asm-semantic` — supplement second pass (parent DOI from `source_path`)

**Date:** 2026-09-16
**Target:** `ragstack_lib_asm_semantic_salesforce_sfr_embedding_4096_semantic_f59de32c`
on Qdrant `http://localhost:24081` + Elasticsearch `http://localhost:24083` (hackathon tenant).
**Status:** COMPLETE — plan exhausted, 151/151 documents, 138,532 points, both stores.
**Working dir:** `/rag/data/asm-supplement-pass/` (`plan.jsonl`, `ledger.jsonl`, `checkpoint.json`).
**Record committed 2026-09-22 (#624); the run's working directory under `/rag/data/…` is not in git.**

---

## 1. What this pass reached that the first recovery pass could not

The first recovery pass (`plan_recovered.py`, 16,263 documents) keyed on the **synthetic
supplement DOI** — `10.1128/mbio.03591-22-s0004` — stripped the suffix, and joined the parent.
That only works for documents that *have* a `doi` payload.

Most of the remaining supplements have **no `doi` field at all**. But the parent DOI is in the
`source_path`, twice over:

```
/local/scratch/<uuid>/AEMv79i3_10_1128_AEM_02521_12-…/aem.2013.79.issue-3/aem.02521-12/suppl/zam999104079so4.pdf
                      └──────────────────────────┘                        └──────────┘
```

`/<journal>.<yyyy>.<vol>.issue-<n>/<parent-suffix>/` → `10.1128/aem.02521-12`, which is already
in the local Crossref cache. **No network.**

## 2. Result

| | before | after |
|---|---:|---:|
| chunks with `metadata.title` | 6,524,727 — 97.12% | **6,663,259 — 99.18%** |

Both stores measured at rest, green:

```
qdrant points_count  6,718,269   (was 6,718,269 — exactly unchanged)
es     docs.count    6,718,269   (was 6,718,269 — exactly unchanged)
```

Other fields, after: `journal` 97.35%, `publisher` 97.37%, `authors` 98.01%, `date` 97.35%,
`year` 99.96%.

### Verification

1,500 randomly sampled points, every written field compared in **both** stores:

```
qdrant exact 1500/1500    es exact 1500/1500    mismatches: none
(date, year) types       qdrant int/int 400/400    es int/int 400/400
date // 10000 == year    0 violations
date precision           253 yyyymmdd · 147 yyyymm
```

## 3. The join was checked, not just performed

Before any write, the parent article's publication year was compared against the year **already
stored** on each supplement's chunks:

```
year: agree=151   disagree=0   no-stored-year=0
```

151 of 151. A wrong parent would almost certainly have produced year disagreements, so this is
evidence the join is *correct*, not merely that it *resolved*. Consequently **`year` was never
written** where one already existed — the stored value stands, and `date` supplies the finer
precision alongside it.

## 4. Provenance — the two recovery paths stay distinguishable

| stamp | documents |
|---|---:|
| `crossref:2026-09-16:parent-doi` (first pass, synthetic DOI) | 16,263 |
| `crossref:2026-09-16:parent-doi-from-path` (this pass) | 151 |

Both carry `title_source: "crossref-parent"`, so a consumer can tell a supplement's inherited
title from a document's own, and an operator can tell *which* derivation produced it.

**A supplement now carries its parent article's title.** That is deliberate and is what makes the
supplement findable, but it is not the supplement's own title, and the two fields above are how
anyone downstream can tell.

## 5. What remains without a title — 55,010 chunks (0.82%), and why

| doc_type | chunks | recoverable? |
|---|---:|---|
| **front-matter** | **53,196** | **No — and correctly so.** These are journal issue front matter (`admin.pdf`). Their path yields an *issue-level* id (`10.1128/jcm.1989.27.issue-12`) that Crossref does not register, because there is no article and therefore no article title. |
| article | 1,741 | Not in the local cache — 47 old articles (1998, 2009). A network Crossref fetch could resolve some. |
| supplement | 62 | 2 documents whose parent is not in the cache. |
| short | 11 | 11 documents, same. |

Only ~1,814 chunks (0.027%) are a genuine *gap*. The 53,196 front-matter chunks are a *correct
absence*: inventing a title for them would be the failure mode this repo keeps hitting, where a
wrong value is indistinguishable from a right one.

## 6. Two field-path errors caught in the dry run, both by cross-checking

Consistent with the standing rule that a field path must be proven in each store and each source:

* `to_date()` was reading `rec["issued"]`; the cache nests dates under **`rec["dates"]`**. Caught
  because the year-agreement check printed `agree=0 disagree=0 no-stored-year=0` — all three zero
  is arithmetically impossible for 151 documents. Had only `date` coverage been checked afterwards,
  this would have shipped as a silent 0% fill.
* `journal` was reading `container-title` (the Crossref API spelling); the cache stores
  **`container_title`**.

Also: `metadata.doc_id` does not exist in Elasticsearch on this index — `doc_id` is **top-level**,
beside `metadata`. A cardinality aggregation on `metadata.doc_id` returned **0**, which reads as
"no documents" rather than "no such field".

## 7. Operational

Gate: collection green + optimizer ok + `points_count` unmoved + disk floor, checked every 10
documents, with a green precondition at start. **It fired once** — a restart found the collection
yellow at segments 28 (the optimizer merging after the previous segment's writes) and the applier
refused to start rather than write into a merge. It resumed from its checkpoint 10 s after green.

Throughput 88–159 pt/s. Chunk counts per supplement are extremely skewed — the largest single
supplementary PDF is **7,631 chunks** — so per-document progress is a poor progress signal; points
written is the honest one.
