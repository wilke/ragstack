# Meaningful text sections — a taxonomy for section-aware chunking

**Status:** input for the UI/ingest session, written 2026-09-22 by the chunking-study session.
Not a decision. The evidence cited is from this project's own measurements; the taxonomy is a
proposal to be argued with.

**Why sections before embedding.** Every chunker in the deployment slides a window over flat
text (`fixed`, `fixed_token`, `sentence`, `words`, `semantic`, `semantic_pooled`); none is
section-aware. `CHUNK_SECTION_AWARE` exists only as a planned Phase-5 config in
[`plan-c5.md`](plan-c5.md). So a boundary that matters semantically is crossed arbitrarily today,
and the fix belongs **before** the embedder, not inside it.

---

## What our own data says, before any taxonomy is proposed

Three measurements should constrain the design, because they contradict the obvious answer.

**1. The largest evidence-bearing class is the one that does not fit IMRaD.** A section-level
oracle over 2,161 judged (topic, document) pairs asked which structural section actually carries
the answer. Result, as a share of wins:

| section class | share of wins | present in | win rate where present |
|---|---|---|---|
| **other** (untitled body leads, case presentations) | **39.1 %** | 78 % | 49.8 % |
| discussion | 28.9 % | 83 % | 34.9 % |
| methods | 12.9 % | 40 % | 32.3 % |
| intro | 8.9 % | 76 % | 11.7 % |
| results | 7.2 % | 38 % | 19.0 % |
| abstract | 3.1 % | 100 % | 3.1 % |

A rigid IMRaD taxonomy would put the biggest class in a bucket called "other". **Untitled and
non-canonical sections must be first-class, not a fallback.**

**2. The evidence is not at the top.** 55.4 % of judged pairs have their best-supporting section
*starting* past token 1,024 (95 % CI 53.0–58.4); only 11.9 % falls in abstract plus introduction,
and 3.0 % in the lead unit. Under a looser midpoint definition the past-1,024 share is 78.7 %.
Any scheme that privileges head matter is optimising for 12 % of the evidence.

**3. Overlap buys nothing, so section boundaries are cheap to respect.** On two independent legs,
four sizes and two metrics, overlap returned a powered null — 61,559 extra vectors on 5,000
documents bought a recall@100 change of exactly 0.0000. Cutting *at* a section boundary instead
of *through* it costs nothing that overlap was paying for.

---

## The proposed taxonomy

Three document families in this corpus, and they do not share a structure.

### A. Scientific articles (JATS XML, PDF)

`jats.py` already extracts section-aware body text for XML, so for JATS the structure is
available and only needs to be carried through to the chunker rather than flattened.

| section | keep? | why it is its own unit |
|---|---|---|
| title, authors, affiliations | metadata, not a chunk | identity, not evidence |
| **abstract** (and its structured sub-parts) | yes, tagged | high precision, low recall — 3.1 % of wins |
| introduction / background | yes | states the problem; rarely answers a pointed question |
| related work | yes, tagged | the section a positioning query wants, and nothing else does |
| **methods / materials / experimental setup** | yes | "how was X measured" is a different query from "what was found" |
| data / datasets / corpus | yes, tagged | corpus facts are asked for directly and live nowhere else |
| **results / findings / evaluation** | yes | — |
| **discussion / analysis** | yes | 28.9 % of wins, second only to "other" |
| **limitations** | yes, **tagged, high value** | the single most useful section for positioning our own work; two of our strongest framings came from other authors' limitations sections |
| conclusion / future work | yes | — |
| ethics / broader impact | yes, tagged | — |
| **table and figure captions** | yes, each its own unit | self-contained claims, dense, and destroyed by a window that splits them |
| footnotes | attach to parent | fragmentary alone; meaningful in place |
| **references / bibliography** | **flag, exclude by default** | pure noise in retrieval; there is precedent in the codebase for flagging reference-list passages (#593) |
| acknowledgements / funding | flag, exclude by default | — |
| appendix / supplementary | yes, tagged | often where the real numbers are |
| **untitled body section** | **yes, first-class** | the largest winning class; must not be a fallback bucket |

### B. This project's own study documents (markdown)

These have a house structure that is more regular than IMRaD and more useful to retrieve on.

| section | why it is its own unit |
|---|---|
| **verdict block** (`**Verdict: …**`) | the answer, stated once, at the top |
| **scope note** ("stated first because it bounds everything below") | the caveat that makes the verdict readable; must never be separated from it |
| **provenance table** (§0 — inputs, hashes, seeds, endpoints, commit pins) | where every number's source lives; the unit a reproducibility question wants |
| **gate table** (requirement / measured / PASS-FAIL) | the decision, in one block |
| counts, results tables | — |
| **deviations, stated** | what was not done as specified |
| attestations | — |
| **what remains blocked** | the forward-looking unit |
| **dated addenda** | append-only; each is its own unit and must keep its date |
| **decision records / owner decisions** | numbered items with options and a recommendation |
| pre-registration / predictions | written before the data; must be retrievable separately from results |

### C. Literature survey files

| section | why |
|---|---|
| **one entry per paper** (key, title, authors, venue, URL, status, what it establishes, **Weight**) | the atomic unit; a query about a paper should return exactly one |
| **"contradicts or complicates us"** | the highest-value section in the whole corpus for finding a story |
| **"searched and did not find"** | the coverage evidence; a gap claim needs it |
| **"established names for what we invented"** | naming decisions |

---

## Rules the taxonomy implies

1. **Never split a section across chunks silently.** Cut inside a section; if a section exceeds
   the budget, split it at paragraph boundaries and mark the pieces as continuations.
2. **Never merge two sections into one chunk** without recording that it happened.
3. **Carry the heading path**, not just the leaf — "Results > 5.3 Reliability" locates a chunk
   that "Reliability" alone does not.
4. **Order is load-bearing.** Every unit carries `section_index` and `sections_in_document` so
   reading order is recoverable and a retrieved section can be expanded to its neighbours. That
   neighbour expansion is a delivery arm we have already measured (`nbr1_512`), so the ordering
   is not hypothetical.
5. **Flag rather than delete** reference lists, acknowledgements and front matter. A retrieval
   default can exclude them; a later question may still want them.
6. **Tag, do not rename.** Keep the author's own heading text verbatim *and* attach a normalised
   class. Normalising away "Supporting information" loses the thing that made it findable.
7. **Untitled is a class, not a failure.** See the oracle table.

---

## What is staged already

`docs/papers/build_corpus.py` implements family B and C client-side as an interim measure: 25
source documents to 595 section files, each carrying `source_file`, `kind`, `area`,
`heading_path`, `section_title`, `section_index` and `sections_in_document` in the text, so a
`fixed_token` 512 / overlap 0 window picks them up. It is a workaround, not the design — the
design belongs in ingest, where family A can use what `jats.py` already knows.
