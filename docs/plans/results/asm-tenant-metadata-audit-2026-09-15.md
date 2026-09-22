# ASM tenant: metadata state of the six registered collections

**Date:** 2026-09-15
**Scope:** Measurement only. **Read-only throughout** — no writes, no deletes, no upserts, no
index or collection changes, no service restarts, no conformance suite. Every number below comes
from `GET`/`_count`/`_search` against the production Elasticsearch (`http://localhost:9200`),
`GET /collections/...` against the production Qdrant (`http://localhost:6333`), a read-only
`sqlite3 -readonly` open of the registry, and reads of files under `/rag`. Two external
read-only APIs were queried with a bounded DOI sample (NCBI PMC ID Converter, Crossref).

**Tenant:** `asm` — `/rag/data/tenants/asm/config/tenant.env`, registry
`/rag/data/tenants/asm/state/collections.db`.

---

## Headline

Three findings, in the order they should be acted on.

1. **`asm-semantic` is not the same corpus as `asm-tok256`/`asm-tok512`.** It holds **48.6%** of
   their documents (95% CI 44.2–53.0, n=500 random documents). `asm-tok256` and `asm-tok512`
   cover **identical** document sets (500/500). The three are registered and labelled as one
   source under three chunkings; they are not. Any A/B of chunking strategy across them, and any
   query fanned across them, is comparing different corpora. **This is more urgent than the
   metadata gap.**
2. **The payload schema varies per collection, silently.** `open-access` carries 12 keys that no
   `asm-*` collection has (`pmcid`, `pmid`, `journal`, `publisher`, `licence`, `source_url`,
   `sha256`, `content_type`, `section_title`, `graphic`, …). `QSOX1` is a third schema again. A
   filter or a citation renderer written against one collection returns nothing, or renders
   blank, against another.
3. **All three owner claims are confirmed, exactly.** DOI but no PMCID: yes. No `section`: yes,
   and it was **never available** for this source, not dropped. About half have no title: **53.5%
   of `asm-tok256` chunks have no title** — and the gap is almost entirely pre-1990 scanned PDFs.

A fourth, not asked about but larger in absolute terms: **`open-access` is missing `year` on
85.2% of its 47.6M chunks** — and that one is **100% recoverable offline** from a file already on
disk.

**The good news, measured:** the gap is almost entirely recoverable. A DOI is enough to recover a
title for **100%** of the sampled title-less passages (Crossref, n=200), and enough to recover a
PMCID+PMID for **98.8%** (NCBI, n=400). Crossref also returns `journal` and `authors`, which no
ASM collection has at all. Nothing below required or performed a write.

---

## 1. Field × collection completeness

Counts are **exact over the full population**, not sampled. Method: the Elasticsearch mirror of
each Qdrant collection is dynamically mapped, so its `metadata.*` subfield list is the exact union
of every field ever indexed, and an `exists` query counts the field over every document. ES and
Qdrant document counts agree exactly for all six collections (column 2 vs. the Qdrant
`points_count` in §3), so the ES counts stand for the Qdrant payloads. Ingest drops empty values
before writing (`python/ragstack/ingestion/enrich.py:354`), so "exists" means "present and
non-empty".

Percentages are of that collection's chunks.

| field | asm-tok256 | asm-tok512 | asm-semantic | open-access | QSOX1 | Catlle_50Genes |
|---|---|---|---|---|---|---|
| **chunks (n)** | **24,830,600** | **12,587,981** | **2,982,219** | **47,625,155** | **3,809** | **0** |
| `doi` | 94.2% | 93.8% | 93.8% | 100.0% | 25.0% | — |
| `doi_source` | 94.2% | 93.8% | 93.8% | 100.0% | 25.0% | — |
| `pmcid` | **0.0%** | **0.0%** | **0.0%** | 100.0% | 0.0% | — |
| `pmid` | **0.0%** | **0.0%** | **0.0%** | 99.9% | 0.0% | — |
| `title` | **46.5%** | **46.5%** | **44.4%** | 100.0% | **0.0%** | — |
| `section` | **0.0%** | **0.0%** | **0.0%** | 0.2% | 20.4% | — |
| `section_title` | 0.0% | 0.0% | 0.0% | 33.5% | 0.0% | — |
| `year` | 99.5% | 99.5% | 99.4% | **14.8%** | 0.0% | — |
| `journal` | **0.0%** | **0.0%** | **0.0%** | 100.0% | 0.0% | — |
| `authors` | **7.4%** | **7.9%** | **7.4%** | 99.9% | 0.0% | — |
| `keywords` | 4.4% | 4.4% | 3.9% | 63.7% | 0.0% | — |
| `tenant_id` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | — |
| `doc_type` | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | — |
| `content_type` | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% | — |
| `filename` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | — |
| `source_path` | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | — |
| `source_url` | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% | — |
| `publisher` | 0.0% | 0.0% | 0.0% | 94.6% | 0.0% | — |
| `licence` | 0.0% | 0.0% | 0.0% | 99.8% | 0.0% | — |
| `sha256` | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% | — |
| `n_citations` | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | — |
| `chunk_index` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | — |
| `prev_chunk_id` | 98.2% | 96.5% | 93.1% | 65.0% | 99.2% | — |
| `next_chunk_id` | 98.2% | 96.5% | 93.1% | 65.0% | 99.2% | — |
| `graphic` | 0.0% | 0.0% | 0.0% | 16.8% | 0.0% | — |
| `is_boilerplate` | 0.0% | 0.0% | 0.0% | 0.2% | 20.4% | — |
| `pages` | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% | — |

A `0.0%` in this table is a **hard zero**, not a rounding: the field is absent from the index
mapping entirely, meaning no document in that collection has ever carried it.

`Catlle_50Genes` is **empty** — 0 points in Qdrant, 0 docs in ES, and its ES index holds only the
249-byte mapping. It is registered and queryable and returns nothing.

---

## 2. The three owner claims

### Claim 1 — "ASM passages carry a DOI but no PMCID." **Confirmed, precisely.**

| | asm-tok256 | asm-tok512 | asm-semantic |
|---|---|---|---|
| has `doi` | 23,396,822 (94.2%) | 11,802,691 (93.8%) | 2,798,040 (93.8%) |
| has `pmcid` | **0** | **0** | **0** |
| has `pmid` | **0** | **0** | **0** |
| has `journal` | **0** | **0** | **0** |

Exact, whole-population. The ~6% without a DOI are concentrated in `doc_type: supplement`
(36.5% DOI coverage) and `front-matter` (0.5%) — covers, mastheads, advertising pages, editorial
boards. Body articles are at **97.8%**.

Where the DOI comes from, whole-population, `asm-tok256`:

| `doi_source` | chunks |
|---|---|
| `filename` | 22,698,207 |
| `text` | 698,582 |
| `metadata` | 33 |

Essentially every ASM DOI is **reconstructed from the PDF filename**, by an ASM-specific rule
(`python/ragstack/ingestion/enrich.py:46,96-107,170-203`: filename stem matching
`^([a-z]{2,6}\.[0-9][-.0-9a-z]+)$`, prefixed `10.1128`). The source JSONL carries `doi` on
**0.0%** of records (verified independently, 4,800 records across 6 shards), so there was no
upstream DOI to copy. The DOIs are therefore synthesised, not asserted — which matters for
§5: they nonetheless resolve.

### Claim 2 — "They carry no `section`." **Confirmed — and it was never available.**

`metadata.section` exists on **0** chunks in all three ASM collections. This is not a dropped
field:

- `section` is emitted from exactly one place in the codebase,
  `python/ragstack/ingestion/boilerplate.py:52-54,502-507`, and it is **not a document-structure
  field**. Its whole vocabulary is `body` / `references` / `license` / `acknowledgements`, and
  `body` is never written. It would never say "Methods" or "Results".
- That `BoilerplateFilter` landed **2026-08-03** (commit `2aa97d5`, #233). The three ASM
  collections were built **2026-06-29 → 2026-07-04**. The code did not exist yet.
- Real structural section labels exist only on the **JATS/PMC-XML path**, under a different key,
  `section_title` (`python/ragstack/ingestion/jats.py:133,524`), and they ride onto chunks only
  via an explicit per-corpus `JsonlLoader(passthrough_keys=…)` opt-in. `scripts/ingest_jsonl.py`,
  the tool that built ASM, never passes `passthrough_keys` at all.
- The ASM source is **PDF text**, not JATS. There are no section headings to parse — the upstream
  extractor emitted a flat `text` blob plus `{title, authors, creationdate, producer, format,
  first_page, abstract}`.

**Verdict: never available.** No PDF-sourced code path, past or present, emits a document-structure
`section` for this corpus. The same reasoning covers the missing `pmcid`, `pmid`, `journal`,
`publisher`, `licence`, `source_url` — all of them are JATS passthrough fields.

Note the 20.4% `section` on **QSOX1** is the boilerplate classifier, not structure:
`references` 663, `acknowledgements` 58, `license` 57. And the 33.5% `section_title` on
`open-access` is **not** section structure either — it is present on 100% of `figure` and `table`
units and **0.0%** of `article` units, i.e. it is a caption/label field. `open-access` has no
usable section structure on body text.

### Claim 3 — "About half have no title." **Confirmed: 53.5% have none.**

| collection | has title | **no title** |
|---|---|---|
| asm-tok256 | 11,555,955 (46.5%) | **13,274,645 (53.5%)** |
| asm-tok512 | 5,859,445 (46.5%) | **6,728,536 (53.5%)** |
| asm-semantic | 1,324,896 (44.4%) | **1,657,323 (55.6%)** |

The owner's "about half" is accurate to within four points.

**The gap is a date cliff, not a random loss.** `asm-tok256`, `doc_type: article`, exact:

| publication year | chunks | has title |
|---|---|---|
| before 1980 | 1,477,963 | **0.4%** |
| 1980–1989 | 2,307,416 | **0.1%** |
| 1990–1999 | 4,388,815 | 8.8% |
| 2000–2009 | 6,201,833 | 34.4% |
| 2010–2019 | 6,126,906 | 92.3% |
| 2020–2024 | 2,867,791 | **99.5%** |

This is the scanned-PDF / born-digital boundary. Pre-1990 ASM PDFs are page images with OCR'd or
re-typeset text and no embedded document metadata; post-2010 PDFs carry a real title field. The
title is not "missing at random" — it is missing for **the older half of the archive**, which is
also the half a user is least able to identify from the passage text alone.

By `doc_type` (asm-tok256): `article` 47.0% titled, `supplement` 43.6%, `front-matter` **5.5%**,
`short` 46.2%.

---

## 3. Point counts

Qdrant `points_count`, live, and the ES mirror. They agree exactly everywhere.

| registry id | Qdrant collection | points | segments | ES docs | ES size |
|---|---|---|---|---|---|
| `open-access` | `ragstack_lib_open_access_…_cd24acfc` | 47,625,155 | 192 | 47,625,155 | 82.3 GB |
| `asm-tok256` | `ragstack_sfr_tok256` | 24,830,600 | 99 | 24,830,600 | 25 GB |
| `asm-tok512` | `ragstack_sfr_tok512` | 12,587,981 | 51 | 12,587,981 | 20.3 GB |
| `asm-semantic` | `ragstack_sfr_semantic` | 2,982,219 | 13 | 2,982,219 | 8.4 GB |
| `QSOX1` | `ragstack_lib_qsox1_…_8870376b` | 3,809 | 8 | 3,809 | 3.5 MB |
| `Catlle_50Genes` | `ragstack_lib_catlle_50genes_…_915eb6bf` | **0** | 8 | **0** | 249 B |

All six report `status: green`. Mean chunk length (`end_char − start_char`): asm-tok256 698 chars,
asm-tok512 1,383, asm-semantic 2,422, open-access 1,425, QSOX1 510.

Document-level, distinct `doc_id` (ES cardinality, precision 40k, approximate ±~1%):

| collection | distinct `doc_id` | distinct `doi` | chunks/doc |
|---|---|---|---|
| asm-tok256 | ~438,834 | ~273,188 | 56.6 |
| asm-tok512 | ~439,162 | ~273,219 | 28.7 |
| asm-semantic | **~206,044** | **~154,268** | 14.5 |

---

## 4. The corpus divergence — read this one first

`doc_id` is stable across the three ASM collections (a document has the same id in each). Sampling
**500 random documents** from `asm-tok512` (one chunk each, `chunk_index: 0`, `random_score`) and
testing membership:

| sampled from asm-tok512 | also in asm-tok256 | also in asm-semantic |
|---|---|---|
| 500 documents | **500 (100.0%)** | **243 (48.6%)** |

95% Wilson CI on the 48.6%: **44.2% – 53.0%**.

Corroborated independently by a DOI-level test (300 random DOIs): `asm-tok256` DOIs found in
`asm-semantic` 59.0%; `asm-semantic` DOIs found in `asm-tok512` 99.7%; `asm-tok256` DOIs found in
`asm-tok512` 100.0%. Both methods agree that **`asm-semantic` ⊂ `asm-tok512` ≡ `asm-tok256`**, at
roughly half the documents.

The build record `/rag/documents/HANDOFF-2026-07-04.md` flags semantic as *"⚠️ PARTIAL — only
~8.6% of docs"*. **That figure is stale and too pessimistic**: two independent live measurements
put document coverage near 49%, not 8.6%. (The 8.6% was plausibly a mid-run snapshot; the chunk
ratio, 2.98M/12.59M = 23.7%, is a third number again, and differs from the document ratio because
semantic chunks average 2,422 chars against tok512's 1,383.) Whichever is quoted, the collection
is **incomplete and registered as if it were not** — its registry label reads simply
"ASM papers — semantic chunking", with no partial marker, and its `state` is `active`.

**Why this is the urgent item.** A query fanned across `asm-tok256` + `asm-semantic` searches two
different corpora; roughly half the archive can be retrieved from one and not the other, and
nothing in the API surface or the registry says so. A chunking A/B across the three is
confounded by corpus composition, not just chunk size.

### Other cross-collection hazards

- **Schema divergence (§1).** A metadata filter on `journal`, `pmcid`, `pmid`, `publisher`,
  `licence`, `source_url` or `content_type` matches **100% of `open-access` and 0% of every ASM
  collection**. A filter on `year` matches 99.5% of ASM and **14.8%** of `open-access` — so the
  *same* year filter silently drops ~85% of the OA corpus while looking like it worked. These are
  the two directions in which a cross-collection query breaks today, and neither raises an error.
- **`section` vs `section_title`.** Two different keys with related names, different meanings
  (boilerplate class vs. figure/table caption), and zero overlap in the collections that carry
  them. Neither is a body-text section.
- **`Catlle_50Genes` is empty** and active. It contributes nothing and reports no error.
- **Journal-name variants in `open-access`:** `PLoS ONE` (6,928,778), `PLOS ONE` (987,425) and
  `PLOS One` (700,236) are three distinct keyword values for one journal, out of ~340 distinct
  journal strings. Any `journal` facet or filter is split across them.
- **Implausible `year` values in ASM:** ~0.04% of chunks carry a year outside 1900–2026
  (asm-tok256 9,916; asm-tok512 4,980; asm-semantic 1,280) — histogram buckets at 2030, 2035,
  2040, 2045. Small, but a `year <= 2026` range filter is not a no-op.

**Not a hazard, checked and cleared:** the registry row for `asm-tok256` has an empty
`text_index`. That is benign — `python/ragstack/collection_store.py:147` resolves
`self.text_index or self.collection`, and `ragstack_sfr_tok256` is a real 24.8M-doc ES index.

---

## 5. What `open-access` in this tenant actually is

**It is the OA corpus, essentially complete — 97.7% of it.**

| | value |
|---|---|
| chunks | 47,625,155 |
| distinct `pmcid` | ~1,408,194 |
| distinct `doi` | ~1,407,415 |
| distinct `doc_id` | ~16,704,590 |
| distinct `journal` | ~340 |
| `/rag/oa/corpus/discovery.jsonl` records | 1,441,791 |
| **coverage** | **~97.7% of discovery** |

`doc_id` ≫ `pmcid` because each article is split into multiple indexed units — `content_type` is
`article` (31,677,222 chunks), `figure` (8,012,840) and `table` (7,935,093). Top journals:
Scientific Reports 11.7M chunks, PLoS ONE 6.9M, Int. J. Mol. Sci. 3.8M, Nature Communications
3.2M — i.e. the broad PMC OA subset, not a microbiology slice.

**Against the `discovery.jsonl` record shape** (`doi, issn, journal, pmcid, pmid, title, year`):

| discovery field | in the OA collection |
|---|---|
| `pmcid` | 100.0% ✅ |
| `doi` | 100.0% ✅ |
| `pmid` | 99.9% ✅ |
| `title` | 100.0% ✅ |
| `journal` | 100.0% ✅ |
| `year` | **14.8% ❌** |
| `issn` | **absent from the payload entirely** ❌ |

So the OA payload matches discovery on six of seven fields and **loses `year` on 85.2% of chunks**
(40,583,118 chunks) and `issn` on all of them. The loss happened at ingest, not at discovery:

> Sampled 300 distinct PMCIDs from OA chunks that have **no** `year`. **300/300 (100.0%)** are
> present in `/rag/oa/corpus/discovery.jsonl`, and **300/300 have a `year` there** (and a title).

**The entire OA `year` gap is recoverable offline, from a file already on disk, keyed by a field
already in the payload, with no network calls.** (Recommendation only — nothing was written.)

---

## 6. Distance from the known-good shape

The hackathon-tenant chunk shape given in the brief is `source_path, filename, doc_type, doi,
doi_source, year, n_citations, tenant_id, chunk_index, prev_chunk_id, next_chunk_id, start_char,
end_char`.

**The ASM collections match that shape exactly — all 13 keys, at the completeness in §1.** That
"known-good" shape *is* the ASM shape; both come from the same `enrich()` →
`index_metadata()` path (`python/ragstack/ingestion/enrich.py:328-354`). ASM adds `title`
(46.5%), `authors` (7.4%) and `keywords` (4.4%) on top of it.

So ASM is **not** behind a richer internal standard. The gap is between that shape and the
**OA/JATS record**, which is the only ingest path in this codebase that produces bibliographic
metadata at all:

| | hackathon / ASM shape | OA discovery record | ASM actual |
|---|---|---|---|
| chunk mechanics (`chunk_index`, `prev`/`next`, `start`/`end_char`) | ✅ | n/a | ✅ 93–100% |
| provenance (`source_path`, `filename`, `tenant_id`) | ✅ | n/a | ✅ 100% |
| `doi` | ✅ | ✅ | ✅ 94.2% (synthesised from filename) |
| `year` | ✅ | ✅ | ✅ 99.5% |
| `title` | ❌ not in the shape | ✅ | ⚠️ 46.5% |
| `authors` | ❌ | — | ⚠️ 7.4% |
| `journal` / `issn` | ❌ | ✅ | ❌ 0% |
| `pmcid` / `pmid` | ❌ | ✅ | ❌ 0% |
| `section` (structural) | ❌ | ❌ *(OA has none either)* | ❌ 0% |

The practical reading: **ASM is one identifier join away from the OA record**, and nothing more
exotic is needed. §7 measures that join.

---

## 7. Recoverability from the DOI

Sample construction: 400 documents drawn at random (`random_score`, seed fixed) from
`asm-tok256`, restricted to `doc_type: article` with a `doi` and **no `title`** — i.e. exactly the
population the owner's third claim is about — one chunk per document (`chunk_index: 0`), 400
distinct DOIs. A 200-DOI control group **with** a title was drawn the same way. All 400 no-title
DOIs have `doi_source: filename`; journal mix jvi 116, jcm 112, jb 71, aem 41, aac 28, iai 27,
cmr 5. No malformed or journal-level DOIs in the doc-level sample.

### NCBI PMC ID Converter

Batched at 200 ids, 3s between batches, descriptive `User-Agent` with `mailto`. Three batches,
600 ids, **no 429 at all** — the burst quota noted in the brief was not hit at this pacing.

| sample | n | returned | **PMCID** | **PMID** | not in PMC |
|---|---|---|---|---|---|
| **no title** | 400 | 400 | **395 (98.8%)** | 395 (98.8%) | 5 |
| has title (control) | 200 | 200 | 199 (99.5%) | 195 (97.5%) | 1 |

**98.8%** of title-less ASM DOIs resolve to both a PMCID and a PMID (95% Wilson CI **96.9–99.5**).
The five failures are front-matter-like artefacts (`10.1128/jcm.24.4.i6-i6.1986`,
`10.1128/jcm.18.3.751-751.1983`, …), not real articles.

This also validates the synthesised DOIs: a filename-derived DOI that NCBI resolves is a correct
DOI. **The filename reconstruction rule is sound at ~99%.**

### Crossref

Sequential, **1 request/second**, `mailto` in the `User-Agent`. 280 DOIs (200 no-title + 80
control). **Zero 429s at this pacing, 100% of requests answered.**

| sample | n attempted | **title** | authors | journal | year | errors |
|---|---|---|---|---|---|---|
| **no title** | 200 | **200 (100.0%)** | 200 (100.0%) | 200 (100.0%) | 200 (100.0%) | 0 |
| has title (control) | 80 | 80 (100.0%) | 78 (97.5%) | 80 (100.0%) | 80 (100.0%) | 0 |

Examples from the title-less population, all 1973–2001 scanned articles:

| DOI | recovered title | authors | journal | year |
|---|---|---|---|---|
| `10.1128/jvi.12.2.275-283.1973` | Replication of Dengue Virus Type 2 in *Aedes albopictus*… | 2 | Journal of Virology | 1973 |
| `10.1128/jcm.39.5.1871-1876.2001` | Detection of Salmonellae in Chicken Feces by a Combination of Tetrathi… | 4 | Journal of Clinical Microbiology | 2001 |
| `10.1128/jvi.75.21.10488-10492.2001` | Epstein-Barr Virus and the Somatic Hypermutation of Immunoglobulin Gen… | 4 | Journal of Virology | 2001 |

> **A caution on pacing, since it changes the answer.** A first attempt at 4 concurrent workers
> was rate-limited into uselessness — 558/600 requests returned HTTP 429 and the apparent
> resolution rate collapsed to 9.8%. That number is an artefact of the client, not a property of
> the data. Re-run sequentially at 1 req/s, the same DOIs resolved at 100%. Any future backfill
> should pace at ≤1 req/s and treat a 429 as a stop, not a retry.

### The answer to "is a DOI enough to recover a title?"

**Yes, unambiguously.** For the ~half of ASM passages that carry a DOI but no title:

| route | resolution on the no-title sample |
|---|---|
| Crossref → `title` | **100.0%** (200/200; 95% Wilson CI 98.1–100) |
| Crossref → `authors` + `journal` + `year` | 100.0% |
| PMC ID Converter → `pmcid` + `pmid` | **98.8%** (395/400; CI 96.9–99.5) |
| either route yields title **or** PMCID | 98.8% (395/400) |

Crossref additionally supplies `journal` and `authors`, which **no ASM collection has at all**
(0% and 7.4% respectively) — so one join closes four gaps at once, not just the title.

Scale of the job: ~273k distinct DOIs in `asm-tok256`/`asm-tok512` (~154k in `asm-semantic`). At
1 req/s that is roughly 76 hours of Crossref calls, or a few hours against a Crossref metadata
dump; the ID Converter batches 200/request and would take ~1,400 requests.



### A local route that needs no network at all

The titles are **already inside the indexed text**. The upstream source JSONL
(`/rag/ingest/docs/asm/`, 18 GB, 41 shards, still on disk) carries `first_page` on **98.2%** of
records, and `first_page` is the literal head of the `text` field that was chunked. For a 1998
article with no `title`, `first_page` reads:

```
JOURNAL OF VIROLOGY,
0022-538X/98/$04.0010
July 1998, p. 5680–5698                                    Vol. 72, No. 7
Copyright © 1998, American Society for Microbiology. All Rights Reserved.
A Comprehensive Panel of Near-Full-Length Clones and
Reference Sequences for Non-Subtype B Isolates of
Human Immunodeficiency Virus Type 1
FENG GAO, DAVID L. ROBERTSON, CATHERINE D. CARRUTHERS, …
```

The title is there, below the masthead, above the author list — and it is already in
`chunk_index: 0` of every one of these documents, in production, today. Extracting it needs a
parse or a model pass, not a fetch.

### What is *not* recoverable at source

The `/rag/ingest/docs/asm/` JSONL is the surviving source, and it is **poorer than the index**:

| field, measured on 4,800 records across 6 shards | present |
|---|---|
| `title` | **39.7%** |
| `authors` | 10.4% |
| `keywords` | 2.1% |
| `first_page` | 98.2% |
| `doi` | **0.0%** |
| `abstract` | **0.0%** |

So **re-deriving metadata "at source" cannot close the title gap** — the source has *less* title
coverage than the index (39.7% of documents vs. 46.5% of chunks; the difference is length
weighting, since titled modern PDFs are longer and contribute more chunks). Recovery must go
through identifiers, or through the `first_page` text.

**The original ASM PDFs are gone.** `source_path` points at `/local/scratch/<uuid>/…`;
`/local/scratch` and `/local` do not exist on this host, and no ASM PDF or XML survives anywhere
readable. The upstream extractor that produced the JSONL in Sep 2024 (its metadata keys
`creationdate`, `producer: "Apex PDFWriter"`, `format: "PDF 1.3"`, `first_page` match no code in
this repo) could not be found either — not in the repo, not under `/rag`. **Re-extraction from
PDFs is not an option.**

Also checked and **negative**: none of the 395 recovered ASM PMCIDs (0/395) appear in
`/rag/oa/corpus/discovery.jsonl`. The local OA discovery file cannot supply ASM titles — its
1.44M records are the recent CC-licensed OA subset, and legacy ASM articles are not in it. (It
*can* supply OA's own missing years — §5.)

---

## 8. How ASM was ingested

For the record, since it determines what is fixable and where.

- **Source:** `/rag/ingest/docs/asm/` — 18 GB, 41 `<uuid>.jsonl` shards. The UUID filenames are
  exactly the `/local/scratch/<uuid>/…` prefixes in `source_path`. Record shape
  `{text, path, metadata:{title, authors, creationdate, keywords, doi, producer, format,
  first_page, abstract}}`. Produced Sep 2024 by an external pipeline; **extractor not found**.
- **Ingest tool:** `python/scripts/ingest_jsonl.py` (`JsonlLoader` → `enrich()` → chunk → embed →
  Qdrant + ES), driven by `/rag/cache/load3corpus/build_continuous_12gpu.sh`, lines 51-53:
  - `tok256)   --chunk-method fixed_token --chunk-size 256 --chunk-overlap 32`
  - `tok512)   --chunk-method fixed_token --chunk-size 512 --chunk-overlap 64`
  - `semantic) --chunk-method semantic_pooled --chunk-buffer-size 3 --chunk-breakpoint-percentile 80 --chunk-min-length 500`
- **Built:** 2026-06-29 → 2026-07-04, CLI-direct to Qdrant/ES/vLLM, never through the API.
  Record: `/rag/documents/semantic-chunking-experiment-2026-07-01.md` §9.4,
  `/rag/documents/HANDOFF-2026-07-0{1,2,3,4}.md`.
- **Metadata:** `enrich()` (`enrich.py:328-341`) → `index_metadata()` (`enrich.py:344-354`), which
  drops `citations`/`abstract` and every empty value. `title` is copied verbatim with **no
  fallback** (`enrich.py:334`: `title=(meta.get("title") or "").strip()`). `enrich.py:4-7` already
  says the quiet part: *"In practice those fields are sparse: `doi` and `abstract` are never
  populated and `authors`/`title` only sometimes."*
- **The current chunking study did not build these and does not use them.** `docs/plans/` mentions
  ASM only at `date-filtering.md:156` (a warning not to rehearse against it, because it writes to
  the production stores) and `metadata-and-kg.md:32`, which already records this audit's §1
  finding in one line: *"`asm-tok256` carries no `journal`, `publisher`, `licence`, `pmcid`,
  `source_url` — field presence varies by collection, silently."*
- **No document-level manifest** exists for ASM. `/rag/data/tenants/asm/manifests/*.json` are
  chunking-spec only, with `chunk_count: null`. Nearest substitutes:
  `/rag/cache/load3corpus/catalog/*.catalog.jsonl`, `/rag/cache/sfr.catalog.jsonl`.

---

## 9. What I could not determine

- **Why `asm-semantic` stopped at ~49% of documents.** The checkpoints
  (`/rag/cache/load3corpus/ckpt/semantic_s*.ckpt`, `/rag/cache/load12c/ckpt/`) and the handoff
  notes show the run was interrupted, but I did not reconstruct which shards completed. Deciding
  whether to finish it or retire it needs that, and it is a read of the checkpoint files, not a
  measurement of the stores.
- **Whether the 8.6% in `HANDOFF-2026-07-04.md` was ever true.** I measured ~49% today; I cannot
  say whether the handoff was wrong, was a mid-run snapshot, or was measured on a different
  quantity.
- **The upstream PDF extractor.** Not in the repo, not under `/rag`. So I cannot say *why*
  `title` was captured for some PDFs and not others beyond the strong year correlation, nor
  whether a better extractor would have done better on the pre-1990 scans.
- **Whether Crossref holds at 100% across the whole ~273k-DOI population.** The 100% is measured
  on n=200 (CI lower bound 98.1%), drawn only from `doc_type: article` with a DOI. It says nothing
  about the ~6% of ASM chunks that have **no** DOI at all — supplements and front-matter — for
  which neither route has a key to join on. Those are unrecoverable by identifier, full stop.
- **Whether `open-access`'s missing 2.3% of PMCIDs (vs. discovery) is a deliberate filter or an
  ingest shortfall.** I measured the delta but did not diff the id sets.
- **Anything requiring a write.** Every recovery route in §5 and §7 is stated as a measurement of
  feasibility. No backfill, no reindex, no payload update was performed or attempted.

---

## 10. Recommendations (not performed — all require writes)

In the order the measurements support:

1. **Mark `asm-semantic` as partial**, or finish it. Today it is `state: active` with a label that
   claims parity with the other two. Anything comparing the three is comparing corpora.
2. **Backfill `open-access.year`** from `/rag/oa/corpus/discovery.jsonl`, keyed on `pmcid`. 100%
   recoverable on the sample, entirely offline, 40.6M chunks affected. Cheapest large win here.
3. **Backfill ASM `pmcid`/`pmid`** via the PMC ID Converter, keyed on the existing `doi`. 98.8%
   resolution, ~273k distinct DOIs to convert. This also makes ASM joinable to the OA corpus and
   to PubTator/MeSH work.
4. **Recover ASM `title`** for the 53.5% that lack one — from `chunk_index: 0` text (local, free,
   needs a parser or a model pass) or from Crossref/PMC via the DOI. Prefer the identifier route
   where it resolves; it is authoritative, whereas the masthead parse is a heuristic.
5. **Do not** plan to recover `section` for ASM. It never existed, the PDFs are gone, and the only
   `section` in the codebase is a 4-value boilerplate class.
6. **Normalise `open-access.journal`** (`PLoS ONE` / `PLOS ONE` / `PLOS One`) before exposing a
   journal facet.
7. **Delete or hide `Catlle_50Genes`**, which is empty and active.

---

## Method notes / reproducibility

- Registry: `sqlite3 -readonly /rag/data/tenants/asm/state/collections.db`.
- Point counts: `GET http://localhost:6333/collections/<name>`.
- Completeness: `POST http://localhost:9200/<index>/_count` with
  `{"query":{"exists":{"field":"metadata.<f>"}}}` — exact, whole-population. Field *lists* from
  `GET /<index>/_mapping`, whose dynamic `metadata.properties` is the exact union of fields ever
  indexed, which is what licenses the hard-zero reading.
- Sampling: `function_score` + `random_score` with a fixed seed over `metadata.chunk_index: 0`
  (one chunk per document). An early attempt using a `terms` aggregation was **discarded** — it
  returns the *most frequent* DOIs, which biased toward long documents and surfaced journal-level
  DOI artefacts (`10.1128/mSphere`) that do not occur in an unbiased draw.
- CIs are Wilson 95%.
- Sample sizes are stated inline at every sampled figure; every unqualified count in §1, §2 and §3
  is exact over the full population.
- Scratch artifacts (samples, raw API responses) are in the session scratchpad, not committed.
