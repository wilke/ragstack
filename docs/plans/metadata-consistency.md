# One metadata schema across every corpus — and how to get the existing ones onto it

**Status:** `PROPOSED`, 2026-09-15. Nothing here is built. No store was written. The ASM
metadata *audit* is running separately; every place a decision depends on its numbers says so
(§7) rather than guessing them.

Owner's framing: *"we need high level consistent metadata first."* Constraints taken as
settled (see [ADR-0005](../adr/0005-tenant-anatomy.md), [metadata-and-kg.md](metadata-and-kg.md) §2):
a corpus is a collection inside one tenant; the tenant's database is where a document record
lives; Qdrant and Elasticsearch may be updated, **dry-run and tested first**.

---

## 0. What was measured for this plan (2026-09-15) — and what was not

Everything in this section was read from live systems or the repo *today*. It is the ground
the rest stands on. The ASM *field-coverage* numbers are deliberately absent — that is the
audit's job.

### 0.1 The ASM tenant's stores and collections

Registry `/rag/data/tenants/asm/state/collections.db` (sqlite, read-only open), six rows:

| id | physical store | points (Qdrant) | ES docs / size | chunker | owner |
|---|---|---:|---:|---|---|
| `asm-tok256` (**default**) | `ragstack_sfr_tok256` | 24,830,600 (99 segments) | 24,830,600 / 25 GB | fixed_token 256/32 | asm-ops |
| `asm-tok512` | `ragstack_sfr_tok512` | 12,587,981 (51) | 12,587,981 / 20.3 GB | fixed_token 512/64 | asm-ops |
| `asm-semantic` | `ragstack_sfr_semantic` | 2,982,219 (13) | 2,982,219 / 8.4 GB | semantic | asm-ops |
| `open-access` | `ragstack_lib_open_access_…_cd24acfc` | 47,625,155 | 47,625,155 / 82.3 GB | fixed_token 512/64 | asm-ops |
| `QSOX1` | `ragstack_lib_qsox1_…` | 3,809 | 3,809 | fixed 512/64 | bvbrc user |
| `Catlle_50Genes` | `ragstack_lib_catlle_50genes_…` | — | 0 | fixed 512/64 | bvbrc user |

Stores are the **production** Qdrant `:6333` (1.18.0) and Elasticsearch `:9200` (8.13.4) —
adopted per ADR-0005 amendment 7, still shared with `demo` (its `tenant.env` names the same
two URLs). Every Qdrant collection has `on_disk_payload: true` and payload indexes on
**`doc_id` and `tenant_id` only**. ES indexes are 1 shard, `_source` retained, and
`metadata.*` strings dynamically mapped to `keyword` (`year`, `chunk_index`, `n_citations`
mapped `long`).

**No snapshot repository is registered on `:9200`** (`GET /_snapshot/_all` → `{}`). Qdrant has
a `snapshots/` bind. `ragstack-ctl tenant backup --fence` exists but is an outage for the
tenant and was **not** exercised against the adopted stores in this session.

### 0.2 What an ASM chunk actually carries (two points, not the audit)

Scrolled two points of `ragstack_sfr_tok256` with `content` excluded. Keys, flat at the payload
top level:

```
chunk_id, chunk_index, doc_id, doc_type, doi, doi_source, end_char, filename, n_citations,
next_chunk_id, prev_chunk_id, source_path, start_char, tenant_id, year, [title]
```

with `year` an **int**, `doi` lowercase, `tenant_id: "public"`, one of the two carrying
`title`. The ES mapping additionally knows `authors` and `keywords`, so some documents carry
those.

**This is exactly `enrich.index_metadata()`** (`python/ragstack/ingestion/enrich.py`) — the
`EnrichedDoc` fields minus `citations`/`abstract`, empties dropped. It is the same producer
that stamps a personal-collection PDF (`source_path, filename, doc_type, doi, doi_source,
year, n_citations`, per the hackathon shape in the brief). **Regimes 2 and 3 in the brief are
one producer with two inputs** (a JSONL of extracted PDFs vs. an uploaded PDF). Regime 1 is
the other producer, `jats.py`, whose `JATS_METADATA_KEYS` are
`doi, pmid, pmcid, title, authors, keywords, journal, publisher, year, licence, sha256,
source_url, content_type, section_title, graphic, abstract, n_tables, n_figures` — and reach a
chunk only through `JsonlLoader(passthrough_keys=…)`, which is **off by default** so the
ASM/PDF path stays byte-identical.

There is no declared schema in `contracts/`: `source.json`, `document_info.json` and
`chunks_response.json` all type `metadata` as a free `object`. That is the drift
[metadata-and-kg.md](metadata-and-kg.md) §1 observed, seen from the contract side.

### 0.3 Two things called "section"

| key | producer | meaning | present on |
|---|---|---|---|
| `section` | `ingestion/boilerplate.py` (`classify_chunk(chunk.content)`) | boilerplate verdict: `references` / `license` / `acknowledgements`; **absent means body** | any chunk ingested with detection on (default on in the API since the feature landed) |
| `section_title` | `ingestion/jats.py` | the JATS `<sec><title>` the unit came from | JATS-derived chunks, only with passthrough |

The brief's "ASM has no `section`" is compatible with three states — detection was off when
ASM was built, ASM predates the feature, or every chunk classified as body — and the audit
distinguishes them (count of `is_boilerplate` keys). §3 depends on which.

### 0.4 The build-spec guard and the manifests (the constraint the brief asked to check)

Read `provenance.py` and `api/deps.py::check_ingest_build_spec`:

- `spec_hash = sha1(f"{model}|{dim}|{chunk_descriptor}")[:8]`. It covers **model, dim and
  chunker only**. A payload write changes none of them. `_differs()` compares those six
  fields concrete-vs-concrete; metadata is not among them.
- `CollectionManifest` has no content hash, no payload hash, no chunk-count check on read
  (`chunk_count` is recorded, never compared).
- The **archive** format (`ingestion/archive.py`) *does* hash `chunks.jsonl.gz`, which
  includes metadata — so a payload update makes any Workspace archive of that collection
  stale, and a later evict→restore would **revert the enrichment**. Checked: all six ASM rows
  have `archive_version 0`, `versions []`, `archive_pending 0`. **No archive exists to go
  stale.** The two personal libraries (`QSOX1`, `Catlle_50Genes`) are in the same state.

**Conclusion: a payload-only update trips neither ADR-0002's guard nor any manifest or
archive today.** The condition to carry forward: a collection that *has* archive versions
must have its archive re-cut (a new version) after a metadata backfill, or the restore path
will silently roll it back. That is a rule for the backfill tool, not a blocker.

### 0.5 The recovery services, as they behave today

- **PMC ID Converter** — moved to `https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`
  (the old URL 301s). Accepts 200 ids/request. **Bursts of ~20–40 requests, then HTTP 429,
  independent of pacing between 0.4 s and 3 s** (five attempts, 122 requests, measured in
  [`results/oa-idfix-pmc-idconv-2026-09-15.md`](results/oa-idfix-pmc-idconv-2026-09-15.md)).
  A 300-record spot check found **0 disagreements** with existing `doi`/`pmid` values.
- **Crossref** — `ingestion/doi_metadata.py` already implements it: polite pool
  (`mailto`), bounded concurrency 4, 10 s timeout, one `Retry-After`-honouring retry, on-disk
  cache (`/rag/cache/doi-cache` on `demo`), DataCite fallback, *never overwrites an existing
  field*. Yields `title, authors, journal, year, publisher, publication_type, url`.
  `DOI_ENRICHMENT_ENABLED=true` on **`demo` only** — not on `hackathon`, `dev`, `asm`. So a
  hackathon upload today carries no Crossref title even when it has a DOI.
- **PubTator3** — see §6; its docs live inside an Angular bundle, not on the page WebFetch
  sees. Endpoints, the ≤3 req/s request, and the raw-text service are quoted there verbatim.

### 0.6 The dev tenant (test target candidate)

Qdrant `:24041`, ES `:24043`, port 24040, manifests dir, sqlite registry. One real collection:
`oa-dev` (`ragstack_lib_oa_dev_…_e788c5be`, **24,263 chunks**, JATS-shaped). **There is no
ASM-shaped collection on dev.** Hackathon (`:24081`/`:24083`) holds one 513-chunk scratch
library.

### 0.7 Not verified in this session

- Any ASM field-coverage number (title/pmcid/section/year presence, document count, the
  number of distinct `doi`s). All deferred to the audit (§7).
- Whether the PDFs behind `source_path` on ASM chunks still exist on disk. The brief says no
  source corpus exists; the payload carries paths; the audit can `stat` a sample.
- The end-to-end behaviour of NCBI's raw-text annotator (§6: submit works, retrieve did not
  within two minutes).
- Availability, licence and runnability of AIONER / GNorm2 / TaggerOne on this host (§6). No
  images under `/rag/apptainer/images/` are taggers.
- Throughput of `set_payload` and `update_by_query` on these stores. §4 says how to measure
  it before it matters.
- Whether `ragstack-ctl tenant backup` can fence the adopted `:6333`/`:9200` pair at all.

---

## 1. The target schema

### 1.1 The rule that makes it universal

**Absent, never null; typed, never coerced; provenance on anything derived.** A field that a
source cannot have is *omitted* on that source (the ES dynamic mapping and Qdrant filters
both treat absence correctly; `""`/`null` already caused #471-class divergence). Every field
has one type, pinned in `contracts/`, and every writer coerces to it at ingest.

Three tiers, three obligations:

| tier | meaning |
|---|---|
| **required** | every document from every producer carries it; a document without it is an ingest error |
| **best-effort** | filled when a recovery path exists; absence is legitimate and *counted* |
| **source-specific** | exists only where the source format has it; absence on other sources is not a gap and must not be reported as one |

### 1.2 The fields

**Identity.** `doc_id` is the only universal identifier. Everything else is a *claim* about
what the document is, and each claim says where it came from.

| field | type | tier | notes |
|---|---|---|---|
| `doc_id` | string | required | internal, deterministic (MEMORY.md: uuid5 of resolved path/content) |
| `doi` | string, lowercase, `10.\d{4,9}/…` | best-effort | `normalize_doi()` is the one canonicaliser |
| `doi_source` | keyword: `metadata` \| `filename` \| `text` \| `pdf-metadata` \| `jats` \| `pmc-idconv` \| `crossref` | required **iff** `doi` present | already exists on the enrich producer; JATS must stamp `jats` |
| `pmid` | string of digits | best-effort | not int — leading zeros are impossible today but the ES mapping should never coerce |
| `pmcid` | string `PMC\d+` | best-effort | **structurally impossible for preprints, grey literature and most PDFs**; that is not a gap |
| `sha256` | hex string | best-effort now, required for new ingests | the content identity; JATS has it, the PDF loader should compute it |
| `ids_source` | keyword list | required iff any of pmid/pmcid were *recovered* rather than read | e.g. `["pmc-idconv:2026-09-15"]` — the enrichment stamp, filterable |

**Bibliographic.** Descriptive; nothing filters on these except `year` and `doc_type`.

| field | type | tier | notes |
|---|---|---|---|
| `title` | string | **required-present** | the *rule* is required, not the truth: a document with no recoverable title carries `title = filename` **and `title_source = "filename"`**. Today the UI already falls through `title → filename → source_path → doi → doc_id`; making that fallback explicit and stamped is what lets "half of ASM has no title" become a measurable number instead of a rendering accident |
| `title_source` | keyword: `source` \| `crossref` \| `jats` \| `filename` | required | |
| `year` | **int** | best-effort | 1800 ≤ year ≤ current+1, else dropped and counted (8,408 future-dated chunks on `open-access`, and **16,176 more across ASM's three indices**, per [date-filtering.md](date-filtering.md)). **Derived as `date // 10000` wherever `date` is present** — never captured independently, or the two drift |
| `date` | **int, `yyyymmdd`** | best-effort | Unknown components are **zero-padded**: `2020-03-15` → `20200315`, `2020-03` → `20200300`, `2020` → `20200000`. Self-describing about precision (`% 100 == 0` = no day, `% 10000 == 0` = no month), so no separate `date_precision` field. Decided 2026-09-15; the argument and the rejected alternatives are in [date-filtering.md](date-filtering.md) § *"When finer precision is wanted"*. Writes nothing today — no range operator exists — but the backfill must store it in this form or a re-fetch is needed later |
| `doc_type` | keyword: `article` \| `supplement` \| `front-matter` \| `short` \| `empty` | required | `enrich.classify`'s vocabulary; JATS emits `article` |
| `journal` | string | best-effort | |
| `authors` | list[string] | best-effort | never a joined string |
| `publisher`, `licence`, `keywords`, `abstract` | | best-effort | `abstract` is document-record only, never on a chunk |
| `enriched_from` | keyword list | required iff any bibliographic field was recovered | `["crossref:2026-09-16"]`; replaces the boolean `doi_enriched_from` stamp with something dated |

**Structural.** Chunk-level; the only tier that is per chunk rather than per document.

| field | type | tier | notes |
|---|---|---|---|
| `chunk_index`, `prev_chunk_id`, `next_chunk_id`, `start_char`, `end_char` | | required | every producer already writes these |
| `content_type` | keyword: `article` \| `table` \| `figure` | required, default `article` | JATS writes it; the PDF path must default it so "filter to prose" is answerable everywhere |
| `section` + `is_boilerplate` | keyword / bool | best-effort | the boilerplate verdict; **recoverable from `content` alone** (§3) |
| `section_title` | string | **source-specific (JATS)** | not recoverable for PDF-derived corpora (§3) |
| `tenant_id` | keyword | required | fail-closed scope, unchanged |

What is deliberately **not** in the core: MeSH headings, funding, references, PubTator
entities (§6), and an `extras` namespace. They belong in the document record when it exists
([metadata-and-kg.md](metadata-and-kg.md) §2), not on 47 million chunk payloads.

### 1.3 Where it lives

The honest current answer is *on the chunk, twice* (Qdrant payload, ES `metadata.*`), and this
plan does not change that — the document record is the right home and is the later step
metadata-and-kg §2 already sequences after the on-chunk backfills. What this plan adds is the
bridge: every backfill and every ingest produces a **per-collection catalog** — one JSONL line
per document in the §1.2 shape, the same thing `ingest_jsonl.py --catalog-out` writes today.
The catalog is (a) the input to the store update, (b) the rollback ledger once it also holds
the prior values, and (c) the seed for the document table when that table is built. It costs
nothing extra and means the document record is populated from a file that already exists
rather than by a second 47M-point scan.

Contract change: add `contracts/schemas/document_record.json` (the §1.2 fields, typed,
`additionalProperties: false` over the core, plus one open `extras` object) and reference it
from `document_info.json`'s `metadata`. Chunk responses keep a free `metadata` object for now
— tightening them is a breaking change for every stored chunk that predates the schema.

---

## 2. Recovery paths per source

### 2.1 OA corpus (JATS on disk; the enrich→JATS mapping is the lever)

Everything is **local**. `discovery.jsonl` has `pmcid` 100 %, `doi` 98.0 %, `pmid` 97.8 %;
`discovery-idfix.jsonl` recovers 22,901 of the 28,220 that had neither; 250-file JATS sample
carries `pub-date/year` 100 %, `article-title` 100 %, `contrib` 98–99 %, `abstract` 96 %,
`kwd` 53 % ([date-filtering.md](date-filtering.md)).

| field | from | cost |
|---|---|---|
| doi, pmid, pmcid | `discovery.jsonl` ⊕ `discovery-idfix.jsonl` (doi/pmid by pmcid), then JATS `article-id` as the tiebreak | file join |
| title, authors, journal, year, publisher, licence, abstract, keywords, sha256 | `jats.py`, already extracted | CPU, an afternoon over 1.44M files, parallel |
| section_title, content_type | `jats.py` | same pass |
| the remaining 5,220 unattempted + 3,847 doi-only + 88 pmid-only | ID Converter, resumable script in the idfix report | ~50 requests in bursts of ≤20 with cooldowns; an hour of wall clock, minutes of work |
| the 99 confirmed-unresolvable (95 = *IJMS* 2007) | nothing — PMC itself has neither id | **do not chase**; count them |

Two defects stand in the way and both are producer fixes, not backfills:

1. **`jats.py` emits `year` as `str`; production carries `int`.** Fix before any JATS ingest
   ([date-filtering.md](date-filtering.md) B0 asks *which path built the deployed corpus*; this
   plan does not need the answer, only that the next path is typed).
2. **`JsonlLoader` passthrough is off by default**, so a JATS ingest through the standard
   path silently drops `pmid, pmcid, journal, section_title, content_type, …`. The OA
   ingest must pass the JATS key list, and the loader should refuse a record whose
   `metadata` carries keys it will drop *without* an explicit passthrough decision — a
   dropped key is exactly how the 47.6M-chunk `open-access` ended up with `year` on 14.8 %.

**Scope call.** The existing `open-access` collection (47.6M points, in ASM's registry) is the
*already-ingested* OA corpus, with the year gap and (unaudited) id gaps. Backfilling it is the
47.6M-point operation date-filtering Part B sizes. **If a re-chunk of `open-access` is coming
within the horizon — and the chunking study's budget-matched result (`tok256` beats
`tok2048` by 2.2–3.8×) makes that plausible — do not backfill it; fix the producer and let the
re-ingest carry the schema.** That is a decision the owner takes with one number: is
`open-access` going to be rebuilt in the next quarter? If yes, this plan's OA work is
§2.1's two producer fixes and nothing else. If no, `open-access` joins §4 as the largest
target, and its recovery is local, not network.

### 2.2 ASM collections (no source on disk; the DOI is the key)

The brief reports a DOI on (nearly) every document, no PMCID, no `section`, half without a
title. The audit will replace "nearly" and "half" with numbers; the paths are the same either
way.

**The fetch is DONE.** `/rag/data/asm-metadata-cache/` (2026-09-15, 1.3 GB, checksummed,
`README.md`): `crossref.jsonl` + `ncbi-idconv.jsonl` (277,682 DOIs each) + `doc-index.jsonl`
(440,049 documents). The backfill is now a **local join**, not a fetch. Coverage it supplies:
title 40.5% → **94.4%**, journal/publisher 0% → **94.4%**, pmcid/pmid 0% → **93.5%/92.5%**;
for the 86% that are body articles, title → **99.2%** and pmcid → **98.7%**.

**Dates are cached unflattened** in Crossref's native `date-parts` — 23.2% year-month-day,
76.6% year-month — so §1.2's `date` (`yyyymmdd`, zero-padded) is derivable with no re-fetch,
and so is `year` (`date // 10000`). Store both from the same parse; never capture them
separately.

| field | from | key | cost, per *document* |
|---|---|---|---|
| title, authors, journal, publisher, `date`→`year` | **the local cache** (above) | doi | local join; no network |
| pmid, pmcid | **the local cache** (`ncbi-idconv.jsonl`) | doi | local join; no network |
| title where Crossref has none | nothing external | — | `title = filename`, `title_source = filename` (§1.2) |

**Throughput, corrected — both earlier numbers were wrong, and the diagnosis was too.**
Crossref serves two pools and names the active one in a response header: `/works/{doi}` is
`polite-single` at **10 req/s**; `/works?filter=doi:a,b,…` is `polite-array` at **3 req/s but
200 DOIs per request**. The audit's 429 storm was reproduced *inside* the polite pool —
concurrency 4 unpaced on the per-DOI endpoint attempts ~49 req/s and takes 83% 429s — so the
cause was exceeding 10 req/s, **not** anonymity. And this table's original "10–20 docs/s at
concurrency 4" was unreachable under *any* pacing: one DOI per request against a 10 req/s cap
caps you at 10 docs/s. The array endpoint sustains **373–547 DOIs/s with zero 429s**, which is
why the whole population took minutes.

OpenAlex was measured and **rejected**: its free tier is now a metered daily credit budget
(1,000 credits, $0.10/day, 1 credit per 50-DOI batch → 50k DOIs/day → 6 days), and
`ids.pmcid` is **0/189** on ASM DOIs — it carries no PMCID at all.

**Two findings that change this section's rules:**

1. **The never-clobber rule in §4.3 needs an exception.** 5.7% of documents that production
   counts as *having* a title carry a PDF-internal id instead — `jv089906257p`,
   `Microsoft Word - SI-Chrysogine_AEM.docx`. Filling only *empty* titles leaves **10,196
   wrong ones in place**. The dry run must classify "existing value is not a title" with a
   stated test, and that class is the only permitted overwrite.
2. **The no-DOI population does not need `first_page` parsing.** It is 74% supplements and
   11% mastheads — files that contain no article title, so a parser correctly returns
   `"Supplemental Material"`. The **parent DOI is in `source_path`**: applying the ASM
   filename regex to *every* path segment derives one for **45,613 of 45,918 (99.3%)**, and
   80.5% of those were already independently present from other documents' filenames — a
   clean cross-check. Already joined in the cache; §3's `first_page` route is not needed.
| section (boilerplate) | local classifier over stored `content` | — | §3 |
| section_title | **not recoverable** | — | §3 |

**Expectations to test on a 500-document sample before sizing anything** (§5 step 1):
ASM journals are `10.1128/…`; PMC holds ASM content after an embargo and not uniformly, so
`pmcid` for ASM will be **best-effort with a real ceiling** — the sample measures the ceiling.
Crossref's coverage of ASM DOIs should be ~100 %; the sample confirms it and measures the
title-recovery rate against the audit's "no title" count.

**Documents with no DOI at all** (the audit's residual): the only local signal is the first
chunk's text. Do **not** build a title extractor for them; stamp `title = filename` and count
them. If the count is large enough to matter, the right tool is a re-extraction with a
structure-aware PDF parser — which changes chunk boundaries and is therefore a new collection
under ADR-0002, not a backfill.

### 2.3 Personal collections (PDF upload; small; private)

| field | from | governance |
|---|---|---|
| doi | text scan / PDF metadata (exists) | local |
| title, authors, journal, year | Crossref, **only if the tenant opts in** (`DOI_ENRICHMENT_ENABLED`) | sends the *DOI*, not the document. A DOI is a public identifier, but the set of DOIs a user uploads is a reading list; per-tenant opt-in is the right granularity and it already exists as a setting. `demo` is on; `hackathon`/`dev` are off. **Recommend on for `hackathon`** (a hackathon corpus is not confidential) and leave `dev` to its owner |
| pmid, pmcid | ID Converter by doi, same opt-in | same argument |
| everything else | `title_source = filename`; `doc_type`; structural fields | local, already written |

No backfill is worth writing for these: the hackathon scratch library is 513 chunks and a
re-ingest is seconds. The producer fix (explicit `title_source`, `content_type` default,
`sha256`) is what matters, and it lands once in `enrich.py`/`loaders.py` for ASM-shaped
input too.

**Preprints and grey literature never get a PMCID, and often no DOI.** The schema handles
that by *absence* (§1.1); the only thing that must not happen is a UI or filter that treats a
missing PMCID as a broken document.

---

## 3. `section` specifically

Two answers, because the word names two fields (§0.3).

**`section` / `is_boilerplate` — recoverable, cheaply, for ASM.** `classify_chunk` takes
only `chunk.content` and a config. The content is in every Qdrant payload. A backfill is:
scroll `content` per collection → classify → `set_payload({section, is_boilerplate})` on the
non-body chunks only (body chunks stay byte-identical, exactly as at ingest). It rides the
same tool as §4 and is the same shape of write. Two cautions: the classifier was calibrated on
scholarly PDF chunks of the API path's size, and ASM's default is **256-token** chunks — a
reference list split into 256-token pieces may classify differently from 512-token pieces, so
the rehearsal (§4) must report the flagged fraction per collection and a human must eyeball
50 flagged chunks per section label before production. And do it only if the audit says the
keys are absent; if ASM was built with detection on and every chunk is body, there is nothing
to write and the interesting finding is *why*.

**`section_title` — not recoverable for ASM, and it should not be tried.** The JATS heading
comes from `<sec><title>`; the ASM chunks came from PDFs flattened to text at extraction, and
the chunker crossed section boundaries freely (`fixed_token` has no notion of them). Even if
the PDFs were still on disk, re-extracting with structure changes chunk boundaries and that is
a new collection under ADR-0002, not an update. A heading-shaped first line (*"Materials and
Methods"*) could be stamped as a guess, but a guessed structural field on one corpus and a
true one on another is worse than an absent one — the filter would silently mean two things.

**So `section_title` is `source-specific (JATS)` in §1.2.** That is the schema decision the
brief anticipated: present where the source has structure, absent where it does not, and
every consumer written to tolerate absence. It will exist on OA-derived documents going
forward and never on ASM's current corpora.

---

## 4. Dry-run and test — what "test first" means on production stores

### 4.1 The mechanics, and what they imply

- **Qdrant.** `set_payload(collection, payload, points|filter)` merges keys into existing
  payloads and **does not touch the vector**; `delete_payload(keys, …)` removes keys. Selecting
  by `filter: {doc_id: …}` hits the keyword index, so the per-document write is cheap. Re-
  embedding is not involved: the vector is the embedding of `content`, and `content` is not
  changed. Cost is a payload rewrite (on-disk payload storage) and, after many writes, the
  optimizer re-indexing payload segments in the background — watch collection `status` and
  pause on `yellow`.
- **Elasticsearch.** `_update_by_query` with a `terms` filter on `doc_id` and a painless
  script whose `params` map `doc_id → fields` for a batch of documents. It rewrites each hit
  as a new document version and marks the old one deleted; BM25 is unaffected because
  `content` is unchanged, and the index has no vectors. Cost is a reindex of the touched
  documents plus transient disk growth until segments merge — **check free space on the ES
  data volume against the largest index before the full run** (25 GB for tok256; budget 2×).
  Use `conflicts=proceed`, `requests_per_second` throttling, and leave `refresh_interval`
  alone unless the rehearsal shows it matters.
- **Both, per document, or neither.** The UI and `/v1/documents` read metadata from **ES**
  (`_source` includes `doc_id, metadata`); vector-leg filters read **Qdrant**. Updating one
  store recreates #471 at data level. The unit of work is one document in both stores, in a
  fixed order (Qdrant then ES), checkpointed by `doc_id`.
- **Idempotent by construction.** Every write is a merge of the same values; re-applying a
  document is a no-op. That, plus the checkpoint, is the whole answer to *"what if it stops
  half way"* — resume, and the verification aggregation (below) proves both stores converged.
- **Rollback is a ledger, not a snapshot.** A Qdrant snapshot of a 24.8M × 4096-dim
  collection is ~400 GB and no ES repository exists. Instead the job records, per document,
  the **prior values of every key it will touch** (including "absent") *before* the first
  write, in the catalog line (§1.3). Rollback is the inverse pass: `set_payload` the prior
  values, `delete_payload` the keys that were absent; the same on ES. Rollback is rehearsed
  (§4.3), not assumed.

### 4.2 The test target

**A cloned, bounded subset of the real ASM collection on the dev tenant.** `oa-dev` is the
wrong shape (JATS keys, `tenant_id`, no `doi_source`), so it cannot rehearse the ASM producer.
The clone: pick ~1,000 `doc_id`s from `ragstack_sfr_tok256` stratified by the audit's classes
(has title / no title; has pmid / none; …), scroll their points **with vectors** (~40k points ×
~16.5 KB ≈ 0.7 GB), upsert into dev Qdrant `:24041` and ES `:24043` under a registry id with an
unmistakable prefix (`scratch-metaclone-tok256-…`), through the API so the row, manifest and
cap come from the normal path. Store URLs are **required arguments** of every script here
(#454); the constructor guard from MEMORY.md (`QdrantClient(url=None)` resolves to production)
applies.

`demo` and `asm` are not test targets: both write to `:6333`/`:9200`.

### 4.3 The procedure

| phase | what | passes when |
|---|---|---|
| **A. dry run** (any store, read-only) | the tool reads current payloads for the target collection, resolves the recovery sources (§2), and writes the catalog with `before` and `proposed` per document plus a summary: per field, documents that would gain it / change it / keep it; type violations it would refuse | the summary matches the audit's coverage numbers to within the sample noise, and **zero** documents would have an *existing* non-empty value overwritten (the never-clobber rule) |
| **B. rehearsal on the clone** | apply on the dev clone; verify; **roll back**; verify | after apply: per-field coverage equals `before + gain`; `points_count` and `indexed_vectors_count` unchanged; ES doc count unchanged; 200 random documents read from *both* stores agree on every field; **retrieval invariance** — top-50 of 20 fixed queries (vector, bm25, hybrid) byte-identical before and after except in `metadata`. After rollback: every payload hash (sha256 over the sorted payload, `content` included) equals its pre-apply hash, for every point |
| **C. production canary** | 1,000 documents on the **smallest** ASM corpus (`asm-semantic`, 3M points), ledger on | the same checks as B on that subset, plus `/v1/documents` and one `/v1/query` against `asm-next` showing the recovered titles, plus the operator watching Qdrant `status` and ES disk during the run |
| **D. full run** | per collection, smallest first (`asm-semantic` → `asm-tok512` → `asm-tok256`), off-peak, SIGINT-safe (the bulk-load runbook's rule: SIGINT, never SIGTERM), Fable plan on record before start | the aggregation before/after per collection, published in `results/` like every other measurement |

Half-applied at any phase means: the checkpoint names the last completed `doc_id`, the
aggregation says how many documents disagree between stores (should be ≤ 1 — the one in
flight), and either *resume* (forward, idempotent) or *ledger rollback* (backward, rehearsed
in B) restores a consistent state. There is no third state.

### 4.4 Re-measure coverage after every write phase — including type-only conversions

**Coverage measured before a write is not a valid baseline for after it.** Every phase above
re-runs the §0 coverage aggregation on the affected collection and publishes both numbers.
Two independent reasons, and the first is the one that bites:

**A type conversion has a failure population, and re-measurement is how you find it.** The
`lucid` fix is a string→int parse over 1,554,790 points. `'2023'` parses; `'2019-2020'`,
`'2023 Mar'`, `'n.d.'` and `'In press'` do not. Whatever that fraction is, the conversion must
do *something* with it, and every option moves coverage:

| what the tool does with an unparseable value | consequence |
|---|---|
| leave it as a string | mixed types inside one collection — the exact defect being removed, now harder to detect because the collection *looks* fixed |
| drop the field | coverage falls; the document silently leaves every year filter, with no error anywhere |
| write a sentinel (`0`, `-1`, `9999`) | worse than either — it *matches* range filters and returns wrong documents |

The tool must therefore report the unparseable population **before** phase C, and the owner
decides the policy. The default is to leave such documents untouched and count them, because
an unconverted document is visibly broken in the same way it already was, while a dropped or
sentinelled one is invisibly broken in a new way. A type conversion that reports "100%
converted" without naming its failure population has not been verified.

**Second reason: the pre-fix numbers were measured with instruments that were wrong at least
once.** An ES coverage query during this work reported 100% of ASM lacking a title because it
used `title` rather than `metadata.title` — ES nests under `metadata.*` while Qdrant's payload
is flat, so the *same* field needs a different path per store. Any before/after pair must use
a field path proven against a real sampled document in **each** store, and the report states
which path was used. Numbers from the two stores that disagree are a finding, not noise.

What the rehearsal must **measure**, because nothing here is sized yet: documents/s for the
Qdrant leg, documents/s for the ES leg, ES disk delta per 1,000 documents, and whether the
Qdrant optimizer goes `yellow` during sustained payload writes on a 99-segment collection.
Those four numbers turn "N documents" from the audit into a wall-clock estimate for D.

---

## 5. Order of work — cheapest decisive step first

1. **The 500-document sample** (a morning, no writes). 500 ASM `doi`s drawn from the audit's
   strata → Crossref (through `doi_metadata.py`, cache on) and the ID Converter (3 requests).
   Decides: the title-recovery ceiling against the audit's "no title" count, and the PMCID
   ceiling for ASM journals. If Crossref recovers titles for >90 % of the untitled documents,
   §2.2 is worth doing; if PMCID resolves for <30 %, `pmcid` on ASM is documented as
   best-effort-with-a-low-ceiling and not treated as a gap.
2. **The schema, in `contracts/`** (§1.2, `document_record.json`) and the **producer fixes**
   (`jats.py` year → int; `JsonlLoader` refuses silent key drops; `title_source`,
   `content_type` default, `sha256` in `enrich.py`/`loaders.py`). Both implementations, per
   the repo rule. This is the step that stops the drift *recurring*; every later step is
   cleaning up the past.
3. **The owner's one decision on `open-access`** (§2.1): re-chunk coming, or backfill 47.6M?
   Everything about the OA regime forks on it.
4. **The backfill tool** — one script, `--dry-run` default, store URLs required, catalog +
   ledger output, checkpoint by `doc_id`, `--rollback <ledger>` mode. Then §4 A → B → C → D on
   the three ASM corpora. The `section` pass (§3) is a `--fields section` invocation of the
   same tool after the audit says the keys are absent.
5. **Personal collections**: flip `DOI_ENRICHMENT_ENABLED` on `hackathon`; nothing else.
6. **Later, not now**: the document table seeded from the catalogs (metadata-and-kg §2); the
   OA ingest under the fixed mapping; `extras`.

**Deliberately not done, or not yet:** no `section_title` on ASM (§3); no title extractor
for DOI-less PDFs (§2.2); no chasing the 99 *IJMS* 2007 records; no `open-access` backfill
until step 3 is answered; no tagger deployment (§6); no KG.

---

## 6. The capability gap: typed entities for documents PubTator3 cannot see

### 6.1 What NCBI actually offers — read from the API documentation, then probed

The page at `https://www.ncbi.nlm.nih.gov/research/pubtator3/api` is an Angular application;
its text is in `main.<hash>.js`, which is what was read. The `…/pubtator3-api/` root is a bare
Django-REST index and documents nothing. Verbatim from the bundle:

| endpoint | purpose |
|---|---|
| `GET …/pubtator3-api/search/?text=…` | search; returns `pmid, pmcid, title, journal, authors, date, doi` per hit (probed: 200) |
| `GET …/pubtator3-api/publications/export/{pubtator\|biocxml\|biocjson}?pmids=…[&full=true]` | annotations for known articles; `full=true` for full text (probed: 200) |
| `GET …/pubtator3-api/publications/pmc_export/biocxml?pmcids=…` | the same by PMCID |
| `GET …/pubtator3-api/entity/autocomplete/?query=…` | free-text *concept lookup* — an entity id for a string, not annotation of a document |
| `GET …/pubtator3-api/relations?…` | BioREx relations, twelve types |
| **"Process Raw Text"**: `POST https://www.ncbi.nlm.nih.gov/CBBresearch/Lu/Demo/RESTful/request.cgi` with `text=…&bioconcept=…` (form-encoded) → a session number; `POST …/RESTful/retrieve.cgi` with `id=<session>` → the result, *"with a 404 (Not Found) HTTP status code before the result is ready"* | **the only NCBI service that annotates arbitrary text.** It is the PubTator 2-era CGI, kept and linked from the PubTator3 docs |

Stated policy, verbatim: *"we ask that users post no more than three requests per second"*;
*"If you anticipate heavy or large-scale use of the PubTator API, please contact us in advance
at chih-hsuan.wei@nih.gov."* Entity types and namespaces (their Table 1): Gene → NCBI Gene,
Disease → MeSH, Chemical → MeSH, Variant → dbSNP else HGVS, Species → NCBI Taxonomy, Cell
Line → Cellosaurus.

Probed 2026-09-15 with a public test sentence (no user data):

- `POST request.cgi` → **HTTP 200, `{"id":"980BB1ECA8EC2C4280B0"}`** (twice; ids are
  20-hex). The submit path exists and accepts text.
- `retrieve.cgi` with that id → **HTTP 400 (an NCBI HTML error page), on POST and on GET,
  across ~2 minutes and four attempts.** Not the documented "404 until ready". So the service
  is **not verified end to end**; it may need a longer wait, a different parameter shape, or
  it may be degraded. Someone with an hour should finish this probe before the option is
  counted on.
- Every guessed `pubtator3-api/annotations/annotate/…` path → 404 `"This resource is not
  available"`. The brief was right that a 404 there establishes nothing; the real path is the
  CGI above.

### 6.2 The options, weighed

| option | covers | governance | cost at 50 docs | cost at 10M docs | maturity here |
|---|---|---|---|---|---|
| **A. PubTator3 export by PMID/PMCID** (bulk FTP + API for 2024+) | PMC OA subset, with the recency cliff measured in [`results/pubtator3/`](results/pubtator3/RESULTS-pubtator3-coverage.md) | public documents only; nothing sent but ids | n/a | bulk: 5.2 GiB, a join | measured: 99.38 % of PMID-bearing OA docs have *something* |
| **B. NCBI raw-text CGI** | anything, in principle | **sends the document text off-host.** Disqualifying for personal collections by default; acceptable only under a tenant-level opt-in the owner records | trivial, async; 3 req/s; "contact us" above some volume | not an option | submit verified; retrieve **not** |
| **C. Local taggers** — AIONER (NER, all six types), GNorm2 (gene normalisation, species assignment), an NLM-Chem-style chemical normaliser, TaggerOne (disease/chemical) — the components the PubTator3 paper names | anything | on-host; nothing leaves | GPU-minutes per document on one H200 | the whole 1.44M OA corpus in GPU-days; not needed because A exists | **unverified**: none present on this host; licences, Docker/Apptainer availability, and whether GNorm2's Java + BERT stack runs rootless are all open |
| **D. Dictionary matching** — NCBI Taxonomy `names.dmp`, MeSH descriptors + supplementary concepts, CARD ARO for AMR | anything | on-host | seconds | hours | the ARO fit is measured (92.2 % of AMR surface strings); species/chemical dictionaries are standard; **genes by dictionary are poor** (ambiguity), which is the one type C exists for |

The measured AMR result is what decides the shape: PubTator3 misses **68.7 %** of
(document, AMR-family) pairs and resolves alleles to per-genome loci, so **an ARO layer is
needed for every producer, including the PMC one.** That makes D's ARO component mandatory
regardless of what A/B/C do, and it makes "one identifier schema, two producers" the *minimum*
— it is really one schema, one PMC producer, one local producer, and one ARO pass over both.

### 6.3 The design to reserve now (and not build)

One record shape, wherever the annotations come from:

```
entity: {type, id, namespace, text, chunk_id?, start?, end?, source, source_date}
  type      ∈ {gene, disease, chemical, variant, species, cellline, amr}
  namespace ∈ {ncbigene, mesh, dbsnp, hgvs, ncbitaxon, cellosaurus, aro}
  source    ∈ {pubtator3-bulk, pubtator3-api, ncbi-rawtext, local-aioner, local-dict}
```

It lives with the document record (metadata-and-kg §2, §3.5 — MeSH/taxon as the KG's
resolved-node backbone), with at most a small `taxon_id[]` / `mesh_id[]` facet on chunks if
topical filtering is wanted. `source` is the field that makes the two producers one product:
a consumer never asks *which tagger*, it asks *what confidence class*, and the per-source
precision is something to measure once, on a held-out PMC slice where both producers can run.

**Recommendation for the gap itself:** default to **C or D on-host** for personal
collections, with B available only as a tenant-level opt-in the owner records in writing —
sending a private PDF's text to an external CGI is a data-governance decision and the default
must be *no*. Because personal collections are small, C's per-document cost is irrelevant
and its only real cost is standing the taggers up once; that is a bounded engineering task to
scope *after* someone has verified the four "unverified" items in the table. Until then, D
(taxonomy + MeSH + ARO dictionaries) gives species, chemicals and AMR genes locally with
known precision characteristics, and covers the part of the microbiology use case the
PubTator study showed matters most.

---

## 7. What hinges on the audit

| audit number | decides |
|---|---|
| documents per ASM collection (distinct `doc_id`) | wall clock for §4 D via the rehearsal's docs/s; the ID Converter burst count |
| fraction with `doi` | whether §2.2 is a near-complete recovery or leaves a residual worth a separate decision |
| fraction with `title` (per collection) | the value of §2.2 at all, against step 1's Crossref ceiling |
| fraction with `pmid`/`pmcid` | whether an ID Converter pass is worth its burst budget, against step 1's PMCID ceiling |
| presence of `section`/`is_boilerplate` keys | whether §3's classifier pass has anything to write, or whether ASM was built with detection on |
| whether `source_path` files exist on disk | whether *any* structure-aware re-extraction is even possible (still a new collection, not a backfill) |
| `year` type and range | whether the 8,408-style future-dated defect exists on ASM too |
| Qdrant vs ES agreement on a sample | whether ASM already carries a #471-class divergence that the backfill must not preserve |
| the same, for `open-access` | the size of the alternative in §2.1 if the owner says "no re-chunk" |

Nothing above is guessed here. Where the plan needed a number to be *shaped* (the clone size,
the canary size), it chose a round one and says so.

---

## 8. Files this plan touches when it is executed

- `contracts/schemas/document_record.json` (new), `contracts/openapi.yaml`,
  `contracts/schemas/document_info.json`
- `python/ragstack/ingestion/jats.py` (year type), `loaders.py` (passthrough refusal,
  `sha256`), `enrich.py` (`title_source`, `content_type` default), `doi_metadata.py`
  (dated `enriched_from`), and the Go equivalents
- `python/scripts/backfill_metadata.py` (new: dry-run / apply / rollback, store URLs required)
- `docs/plans/results/metadata-backfill/` for the rehearsal and canary measurements
- the four tenant `tenant.env` files, only for `DOI_ENRICHMENT_*` on `hackathon`

Related: [metadata-and-kg.md](metadata-and-kg.md) (the document record and KG shape this
feeds), [date-filtering.md](date-filtering.md) (the `year` backfill this generalises),
[oa-full-ingest.md](oa-full-ingest.md) (the ingest whose mapping §2.1 fixes),
[`results/pubtator3/`](results/pubtator3/), [`results/mesh-transfer/`](results/mesh-transfer/),
[`results/oa-idfix-pmc-idconv-2026-09-15.md`](results/oa-idfix-pmc-idconv-2026-09-15.md).
