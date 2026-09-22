# Does PubTator3 supply the typed entity layer for the OA corpus?

*Run 2026-09-15. A bounded data experiment, not a scored evaluation: no pre-registration, no
hypothesis test, no ragstack code changed. Everything below is a measurement over the
2026-08-17 PubTator3 bulk release joined to `/rag/oa/corpus/discovery.jsonl` (1,441,791
records), plus a live-API verification sample and one CARD/ARO vocabulary check.*

> **Read this first.** Two of the headline numbers depend on **which** PubTator3 you ask.
> The FTP bulk files and the live PubTator3 API **disagree for 2024-and-later documents**, by
> a lot (§4.2). Every "coverage" number in §2 is measured against the bulk files, which is
> the artefact a pipeline would actually ingest. Where the API says something different, it
> is called out. Do not merge the two into one figure.

---

## 1. The answer

**Yes — except AMR genes, and except the last two and a half years.**

PubTator3 covers this corpus essentially completely for *species*, and the coverage is
**best exactly where MeSH is absent**: Frontiers in Microbiology, Microorganisms and
Antibiotics — the three journals that carry no MeSH at all — come back at **99.6–100.0%
species coverage** for every year through 2023, better than the corpus as a whole. The
gazetteer we were about to build for organisms → taxon id should not be built. It already
exists, it is applied at corpus scale by a published tagger, and 80.6% of its species
mentions carry an NCBI taxon id.

Two holes, both large enough to plan around:

1. **AMR gene and allele names.** Over 57,267 microbiology-journal full texts from
   2010–2023, a known AMR gene family appears in the text but **not** in PubTator's gene
   annotations in **68.7%** of (document, family) pairs (95% CI 68.1–69.2). For the core
   ESBL/carbapenemase/`mecA` families the miss rate is **60.2%** (59.2–61.2). `mecA` is
   missed in **99.8%** of the 820 documents that mention it; `mcr-*` in 98.7%; `optrA`,
   `poxtA`, `blaVEB`, `blaCARB`, `blaADC`, `blaIMI`, `blaSME` in **100%**. And where
   PubTator *does* resolve an allele, the identifier is a **per-genome NCBI Gene locus
   record, not an allele identity** — `blaCTX-M-15` resolves to at least five different gene
   ids across E. coli, K. pneumoniae and Salmonella, two of which occur **inside the same
   document** (§5.3). A separate AMR vocabulary is required. **Yes.**
2. **The recency cliff.** In the bulk release, documents from 2024 onward are largely
   **abstract-only**: 50.7 distinct concepts per document in 2023 falls to **9.2 in 2024 and
   8.5 in 2025**. That is 338,087 joinable corpus documents — 23.4% of the corpus — with roughly a
   sixth of the annotation they will eventually have. Part of that is a genuine PMC
   full-text lag (the live API has full text for only 64.9% of 2024 and 27.3% of 2026
   documents), but **most of it is bulk-file staleness**: for 2024–2025 documents the API
   *does* have full text for, the bulk file still carries only abstract-level annotations
   two thirds of the time (§4.2).

Neither hole touches the organism layer, which is the part the plan most needed.

---

## 2. Join coverage

`pmid` is present on **1,409,724 / 1,441,791 = 97.78%** of the corpus, all distinct, no
duplicates. `pmcid` is present on 100%, but the PubTator3 bulk tables are keyed on PMID
only, so the **32,067 PMID-less documents (2.22%) cannot be joined at all** by this route and
are excluded from every percentage below. (A separate job is recovering those identifiers;
nothing here duplicates it.)

| table | corpus docs present | % of joinable | % of all 1.44M |
|---|---:|---:|---:|
| `species2pubtator3` | 1,228,740 | **87.16%** | 85.22% |
| `gene2pubtator3` | 812,012 | 57.60% | 56.32% |
| `chemical2pubtator3` | 1,196,451 | 84.87% | 82.98% |
| `disease2pubtator3` | 1,204,687 | 85.46% | 83.55% |
| **any of the four** | **1,400,998** | **99.38%** | 97.17% |
| `relation2pubtator3` | 473,200 | 33.57% | 32.82% |

**Read the per-table rows as "documents that contain that entity type", not as
processing coverage.** The union row is the processing-coverage figure: PubTator3 has
*something* for 99.38% of every corpus document that carries a PMID. Only 8,726 joinable
documents (0.62%) have zero annotations of any of the four types.

### 2.1 The relation table is a different kind of thing

`relation2pubtator3` reaches only a third of the corpus and its predicate vocabulary is eight
coarse types (`associate`, `treat`, `cause`, `positive_correlate`, …) over
Chemical/Disease/Gene/Variant pairs. It is not a substitute for a designed KG edge model; it
is a candidate-edge source. Not investigated further here.

---

## 3. Coverage by journal — the finding that decides the approach

The three MeSH-less journals are the best-covered journals in the corpus, not the worst.

| journal | docs | joinable | any% | species% | gene% | chem% | disease% |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Frontiers in Microbiology** | 38,588 | 38,478 | 99.86 | **97.65** | 50.69 | 93.79 | 86.58 |
| **Microorganisms** | 17,512 | 17,047 | 99.85 | **96.49** | 41.00 | 87.59 | 82.50 |
| **Antibiotics** | 9,642 | 9,346 | 99.98 | **97.45** | 43.61 | 92.65 | 93.67 |
| Frontiers in Cell. Infect. Microbiol. | 11,019 | 10,986 | 99.94 | 97.49 | 67.25 | 85.45 | 97.34 |
| PLOS One | 322,311 | 322,054 | 99.56 | 94.40 | 53.38 | 85.56 | 90.86 |
| Scientific Reports | 282,800 | 282,608 | 98.64 | 74.36 | 43.20 | 83.26 | 78.06 |
| Nature Communications | 78,972 | 78,928 | 99.43 | 63.85 | 52.30 | 88.67 | 67.77 |

*Corpus journal names differ slightly from the brief: the corpus spells it `PLOS One`, and
its counts are Frontiers in Microbiology 38,588 (38,478 joinable — the brief's 38,478 is the
joinable count), Microorganisms 17,512 (17,047 joinable), Antibiotics 9,642 (9,346).*

The low species figures for Scientific Reports (74.4%) and Nature Communications (63.9%) are
**not** a coverage failure — those journals publish physics, materials and chemistry papers
that contain no organism. Their `any%` is 98.6 and 99.4.

Gene coverage is uniformly lower than species because most papers do not name a gene; it is
not evidence that gene tagging failed. Where genes matter, it is high: Frontiers in
Immunology 88.5%, PLoS Genetics 90.1%, PLoS Pathogens 86.1%, IJMS 80.3%.

### 3.1 The microbiology journals by year — where the cliff lands

| journal | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|
| Frontiers in Microbiology — species% | 99.6 | 99.6 | 90.8 | 89.8 | 87.2 |
| … gene% | 59.9 | 56.9 | **16.8** | **19.1** | 21.8 |
| … concepts/doc | 63.9 | 60.2 | **8.1** | **10.4** | 14.8 |
| Microorganisms — species% | 99.8 | 99.8 | 91.4 | 90.6 | 87.2 |
| … concepts/doc | 65.9 | 65.3 | **10.4** | **7.7** | 8.5 |
| Antibiotics — species% | 100.0 | 99.9 | 90.8 | 91.2 | 95.0 |
| … concepts/doc | 62.7 | 64.0 | **11.5** | **8.4** | 22.7 |

Species presence degrades gracefully (100% → ~90%) because organisms are named in the
abstract. Gene presence and concept density collapse, because genes are named in Methods and
Results. **Any typed entity layer built on the current bulk release is an abstract-level
layer for 2024+.**

---

## 4. Full text vs abstract-only

### 4.1 The live API

1,800 PMIDs, 300 per year, drawn with `random.seed(42)` from the joinable corpus, fetched
from `pubtator3-api/publications/export/biocjson?full=true`. A document counts as full-text
if it has any passage whose `section_type` is not `TITLE`/`ABSTRACT`. 1,793 returned (7
missing, all 2024).

| year | n | full text | 95% CI | median annotations, FT | median annotations, abstract-only |
|---|---:|---:|---|---:|---:|
| 2015 | 300 | **100.0%** | 98.7–100.0 | 283 | — |
| 2020 | 300 | **100.0%** | 98.7–100.0 | 287 | — |
| 2023 | 300 | 99.0% | 97.1–99.7 | 326 | 20 |
| 2024 | 293 | **64.8%** | 59.2–70.1 | 272 | 10 |
| 2025 | 300 | **65.3%** | 59.8–70.5 | 250 | 16 |
| 2026 | 300 | **27.3%** | 22.6–32.6 | 252 | 10 |

The brief's 100-PMID probe (90 full text / 10 abstract-only, the 10 all 2024–2026) is
confirmed and sharpened: the abstract-only fraction is not 10% spread evenly, it is ~0%
before 2024 and 35%/35%/73% in 2024/2025/2026. Median text length for an abstract-only
document is ~1,700 characters against ~50,000 for a full-text one — **a 30× difference in
what the entity layer has to work with.**

### 4.2 The bulk files are staler than the API, and that is the bigger effect

Same 1,793 documents, distinct-concept counts compared between the 2026-08-17 bulk release
and the live API:

| year | median API concepts | median BULK concepts | bulk < 25% of API |
|---|---:|---:|---:|
| 2015 | 36.5 | 36.5 | 0.0% |
| 2020 | 41.0 | 41.0 | 0.3% |
| 2023 | 48.0 | 47.5 | 1.0% |
| 2024 | 15.0 | 6.0 | 44.7% |
| 2025 | 15.5 | 6.0 | 43.0% |

Restricted to the documents the **API has full text for**, the gap is the whole story:

| year | n (API full-text docs) | median API concepts | median BULK concepts | bulk < 25% of API |
|---|---:|---:|---:|---:|
| 2024 | 190 | 35.0 | **5.5** | **68.9%** |
| 2025 | 196 | 31.5 | **5.0** | **65.8%** |
| 2026 | 82 | 32.0 | **6.5** | 45.1% |

2015–2023 match to within a concept, so this is not a units artefact. For 2024–2025, **two
thirds of the documents PubTator3 has already annotated at full text are carried in the bulk
release at abstract level only.** Operationally: ingesting the monthly bulk file gives you an
entity layer that is ~1 year behind on the newest ~24% of the corpus, and the fix is the API
(or a later bulk release), not more parsing.

---

## 5. Identifier density and resolution

### 5.1 Concepts per document (bulk, all joinable documents)

| table | covered docs | distinct concepts | mean per covered doc | rows carrying an id |
|---|---:|---:|---:|---:|
| species | 1,228,740 | 7,189,513 | 5.85 | 100.00% |
| gene | 812,012 | 14,257,528 | 17.56 | 100.00% |
| chemical | 1,196,451 | 20,134,169 | 16.83 | 94.96% |
| disease | 1,204,687 | 14,146,752 | 11.74 | 100.00% |

**Those "100%" figures are a property of the file format, not of the tagger.** A bulk row is
one *(pmid, type, concept id)* with the distinct surface forms pipe-joined, and the
species/gene/disease files simply **omit unresolved mentions**. Only the chemical file keeps
them, collapsed into a single `-` row per document. Mentions-per-document is therefore *not
measurable from the bulk files at all* — 5.85 is distinct taxa per document, not species
mentions per document.

### 5.2 The real resolution rate, measured at mention level from the API sample

1,793 documents, 472,000 individual annotations:

| type | mentions | with an identifier | % |
|---|---:|---:|---:|
| Gene | 114,519 | 110,580 | **96.56%** |
| CellLine | 9,585 | 9,122 | 95.17% |
| Disease | 104,091 | 96,575 | 92.78% |
| **Species** | 88,356 | 71,184 | **80.56%** |
| Chemical | 148,338 | 104,317 | 70.32% |

**The reported ~83% species-with-taxon-id figure checks out** (80.56% here). The unresolved
species mentions are mostly common names and abbreviations that the normaliser declines:
`wheat`, `HPV`, `S. pneumoniae`, `pneumococcal`, `S. aureus`, `piglets` — and `al.`, which is
a false positive from "et al.". Note that **a pipeline reading the bulk file never sees
these**, so a bulk-derived species layer silently drops ~1 in 5 species mentions rather than
carrying them as unresolved spans.

### 5.3 Where a resolved gene id points

This is the part that matters for a join out to BV-BRC. Resolved from NCBI Gene:

| gene id | symbol | organism | description |
|---|---|---|---|
| 9538104 | blaCTX-M-15 | *Escherichia coli* | beta-lactamase |
| 10228415 | blaCTX-M-15 | *Klebsiella pneumoniae* | CTX-M-15 beta-lactamase |
| 2716485 | CTX-M-15 | *Escherichia coli* | hypothetical protein |
| 18261918 | ctx-m-15 | *Klebsiella pneumoniae* | extended spectrum beta-lactamase CTX-M-1 |
| 24956176 | blaCTX-M-15 | *Salmonella enterica* | Beta-lactamase CTX-M-15 |

These are **per-genome locus records**. The allele `blaCTX-M-15` has no single id, and the
choice among them is driven by which organism GNorm2 thought the sentence was about. Across
the 57k-document microbiology slice:

| family | annotated rows | distinct NCBI Gene ids |
|---|---:|---:|
| blaTEM | 1,218 | 28 |
| blaOXA | 1,263 | 27 |
| blaCTX-M | 1,713 | 23 |
| blaNDM | 722 | 18 |
| blaKPC | 1,020 | 14 |
| blaCMY | 315 | 15 |
| mecA | **2** | 2 |

`blaNDM-1` alone spreads over 10 ids, `blaKPC-2` over 8. And the collision runs both ways:
gene id `9538104` is attached to four different surface strings (`CTX-M-15`, `blaCTX-M`,
`blaCTX-M-1`, `blaCTX-M-15`) — so the id is not even family-stable.

---

## 6. The AMR gap — measured

### 6.1 Method

Instrument: a 60-pattern list of AMR gene families (`amr_terms3.py`, committed alongside).
Every pattern is **case-sensitive and word-bounded**, requires an explicit hyphen before an
allele number, and is applied **identically** to the document text and to the PubTator gene
surface forms for the same document, so the comparison is symmetric. `blaACT` and `blaFOX`
were dropped because `ACT-1` and `FOX-1` collide with a transcription factor and with human
`RBFOX1`.

*Two earlier versions of this instrument were discarded and are named here because they
inflated the answer in the direction the hypothesis wanted.* v1 had no `\b` and was
case-insensitive, so `per 100` matched `blaPER` (5,842 documents) and `changes 1` matched
`blaGES`. v2 fixed the boundaries but allowed a space separator, so `KPC 69`, `FOX 30` and
`ACT 2601` matched. Headline miss rates across the three versions: 78.0% → 71.6% → **68.7%**.
Only v3 is reported.

Population: the 57,267 documents from Frontiers in Microbiology, Microorganisms, Antibiotics,
Frontiers in Cellular and Infection Microbiology and J. Antimicrob. Chemother. published
**2010–2023** — the window where §4.2 shows bulk and API agree, so a miss cannot be blamed on
the recency cliff. Text is the local JATS body with `<ref-list>` and `<back>` stripped.

### 6.2 Result

**6,388 documents (11.2%) name at least one AMR gene family in their text.** Of those:

| | docs | share |
|---|---:|---:|
| every family named in the text is also annotated | 583 | **9.1%** |
| some annotated, some missed | 2,563 | 40.1% |
| **none annotated** | **3,242** | **50.8%** |
| (of which: zero gene annotations of any kind) | 981 | 15.4% |

At (document, family) granularity: **27,888 text hits, 8,740 annotated, 19,148 missed —
68.7% (95% CI 68.1–69.2).** The rate is flat across years (63–75%, no trend), so it is a
property of the tagger, not of the release.

| family | docs naming it | also annotated | miss rate |
|---|---:|---:|---:|
| blaKPC | 729 | 473 | 35.1% |
| blaCTX-M | 1,359 | 752 | 44.7% |
| blaTEM | 1,037 | 550 | 47.0% |
| blaOXA | 1,791 | 774 | 56.8% |
| blaNDM | 984 | 423 | 57.0% |
| blaSHV | 704 | 181 | 74.3% |
| tet(A–Z) | 1,677 | 339 | 79.8% |
| blaIMP | 366 | 71 | 80.6% |
| aph(n') | 852 | 103 | 87.9% |
| ant(n') | 327 | 18 | 94.5% |
| gyrA | 1,386 | 55 | 96.0% |
| qacE / qacEΔ1 | 286 | 8 | 97.2% |
| blaGES | 203 | 5 | 97.5% |
| **mcr-\*** | **816** | **11** | **98.7%** |
| **mecA / mecC** | **820** | **2** | **99.8%** |
| optrA, poxtA, blaVEB, blaCARB, blaADC, blaIMI, blaSME, blaSPM, blaGIM, blaLEN, blaTLA, blaSFO, blaBEL, blaOKP, lsa (15 families) | 802 | **0** | **100%** |

Core ESBL/carbapenemase/`mecA` families only: **9,113 text hits, 3,623 annotated, 60.2% miss
(59.2–61.2)**; of the 3,727 documents naming a core family, only 653 (17.5%) have all of them
annotated and 1,868 (50.1%) have none.

### 6.3 The misses are real, not bulk staleness

400 (document, family) misses — 40 each for `mecA`, `mcr`, `optrA`, `gyrA`, `blaOXA`,
`blaCTX-M`, `blaVEB`, `tet`, `qacE`, `blaADC` — were re-checked against the **live API at
mention level**, searching every Gene annotation's surface text in the full-text response.
**390 / 400 = 97.5% (95% CI 95.5–98.6) are confirmed misses in the live API too.** `mecA`:
40/40. `blaCTX-M`, `blaVEB`, `tet`, `qacE`, `blaADC`: 40/40 each. Ten cases were bulk
staleness.

### 6.4 The named case: PMC7761672 / PMID 33287207

In the corpus (Antibiotics, 2020) and **annotated at full text** — 83 passages, 303
annotations (58 Gene, 77 Species, 133 Chemical, 22 Disease, 12 Variant, 1 CellLine).

The red team's specific prediction is **half wrong and half right**, and the right half is
worse than predicted. PubTator *does* find the two headline alleles, with ids:

```
blaOXA-48 / OXA-48 / BlaOXA-48   -> NCBIGene 15842812   (8+6+1 mentions)
blaCTX-M-15                      -> NCBIGene 10228415   (3 mentions)
CTX-M-15                         -> NCBIGene 18261918   (2 mentions)   <-- same allele, different id,
                                                                            same document
```

But of the **15** AMR gene families this paper's text names, **9 are not annotated at all**:
`blaKPC`, `blaNDM`, `blaSHV-11`, `fosA`, `oqxA`, `catB3`, `strA`, `gyrA`, `parC`. Worse,
`parC`, `ompK36`, `blaSHV-11`, `fosA` and `catB3::IS26` *are* tagged — as **Chemical**, in
the unresolved `-` bucket. So they are present in the annotation stream, typed wrongly, with
no identifier.

### 6.5 Verdict

**A separate AMR vocabulary is required. Yes.** Not because PubTator never finds AMR genes —
it finds the big ESBL and carbapenemase families 40–65% of the time — but because (a) the
recall is 30–40% on exactly the families a resistance-focused product is asked about, (b) it
is ~0% for `mecA`, `mcr-*`, oxazolidinone and several carbapenemase families, and (c) the
identifier it returns is a genome locus, not an allele, so it cannot serve as the join key to
BV-BRC that the plan needs.

---

## 7. CARD / ARO as the AMR vocabulary

**Licence — the two CARD downloads are not under the same terms, and only one is usable.**

| download | file | licence |
|---|---|---|
| `card.mcmaster.ca/latest/ontology` | `aro.obo` / `aro.tsv` / `aro.json` / `aro.owl` | **CC-BY 4.0** — free reuse with attribution |
| `card.mcmaster.ca/latest/data` | `card.json`, `aro_index.tsv`, FASTA | **"Use or reproduction … by any commercial organization … is prohibited, except with written permission of McMaster University."** |

Take the **ontology**, not the data package. Citation: Alcock et al. 2023, *NAR* 51:D690–D699.

**Size and shape.** `aro.obo` (2026-08-12) holds **9,051 terms** and **8,095 synonyms**
(6,889 terms carry at least one). Of these, **577 are AMR gene *families*** and **6,893 are
their descendants** — the allele-level terms. The AMR-gene subtree therefore yields
**8,680 distinct surface names** (names + synonyms) over 7,470 terms, and that is the
matchable vocabulary. The rest of the ontology is drugs (585), drug classes (75), adjuvants
(120) and mechanisms (12), which are separate assets — the drug terms are a better antibiotic
list than a MeSH slice.

**Are they the names in our papers?** All 57,267 microbiology full texts were scanned for
every match of the §6.1 patterns: **2,576 distinct AMR surface strings, 230,964 occurrences.**
Against the normalised ARO name set (lowercase, hyphens unified, parentheses dropped, a
leading `bla` stripped):

- **2,376 / 2,576 distinct strings = 92.2%** resolve to an ARO term.
- **186,007 / 230,964 occurrences = 80.5%** resolve.

The 200 unresolved strings are **naming-convention mismatches, not vocabulary gaps**, and
each has a mechanical fix:

| unresolved string | occurrences | why | in ARO as |
|---|---:|---|---|
| `AmpC` / `ampC` | 10,034 | ARO qualifies by organism | `Escherichia coli ampC beta-lactamase`, `Ecol_ampC_BLA` |
| `gyrA`, `parC` | 10,096 | ARO models the *mutation*, not the gene | `Acinetobacter baumannii gyrA conferring resistance to fluoroquinolones` |
| `aph(3`, `ant(3`, `ant(2` | 4,876 | **my** regex truncates at the open paren | `APH(3')-Ia` etc. are present |
| `aadA1`, `catA2` | 2,562 | ARO packs aliases into one composite name | `"aadA, aadA1, aad(3'')(9)"` |
| `qnrS`, `qnrB`, `qnrA`, `qnrD` | 4,795 | ARO is allele-level only | `QnrS1`…`QnrS15`, `QnrB1`… — no bare family term |
| `blaTEM`, `blaSHV`, `blaCTX-M`, `blaOXA` | 1,266 | same — family without allele | families exist as `is_a` parents, not as matchable names |
| `oqxA`, `oqxB` | 1,986 | ARO carries the operon | `oqxAB` |
| `mcr-3`, `mcr-5`, `mcr-7`, `mcr-8`, `mcr-10` | 2,013 | ARO is sub-allele-level | `MCR-3.1`…`MCR-3.14`, `mcr-10.1`… |
| `blaZ` | 2,181 | different surface form | `PC1 beta-lactamase (blaZ)`, `BlaZ beta-lactamase` |

**Fit for purpose: yes, with a normalisation layer.** ARO has the terms; what it does not
have is a flat index in the spelling papers use. A usable matcher needs: strip/optional `bla`
prefix, hyphen and parenthesis normalisation, split composite names on commas, roll
sub-alleles (`MCR-3.4`) up to alleles (`mcr-3`) and alleles up to families via `is_a`, and
add family-level terms (`qnrS`, `blaTEM`) synthesised from the `is_a` parents. That is a day
of work on a 9,051-term file, not a gazetteer-building project — and unlike a hand-built
gazetteer it comes with the family/drug-class/mechanism hierarchy already attached, which is
the part the typed entity layer actually wants.

Two things ARO does **not** give you: it is not a tagger (no spans, no disambiguation — CARD
ships RGI for sequences, not for text), and `aro.obo` carries no NCBI Gene or BV-BRC
cross-references, so the BV-BRC join still has to be built. `aro_index.tsv` has GenBank
accessions but is in the licence-restricted package.

---

## 8. What I could not determine

- **True mention counts from the bulk files.** The format is one row per *(pmid, concept
  id)*, so "mentions per document" is only obtainable from the API or from BioCXML (10 × 20 GB,
  not downloaded). Every bulk density figure here is distinct concepts per document.
- **Whether the 2024+ bulk lag is a policy or a backlog.** Measured, not explained. The
  release notes say monthly; the bulk file's full-text content for 2024–2025 is a year behind
  the API and I cannot say whether the next release closes it. Anything built on the bulk
  files should re-measure §4.2 on the release it actually ingests.
- **Precision of PubTator's AMR gene annotations.** §6 measures recall only (text-hit not
  annotated). I did not check whether annotated AMR genes are *correctly* typed and
  identified beyond the PMC7761672 case and the id-spread analysis in §5.3.
- **Whether ARO's terms would match at corpus scale**, as instructed. §7 establishes vocabulary
  fitness on 2,576 observed strings from 57k microbiology documents; it is not a corpus match.
- **The 32,067 PMID-less documents.** They were counted and excluded, nothing more. The
  NCBI ID converter would resolve most from PMCID, but that is the other job.
- **Whether `blaACT`/`blaFOX` are genuinely missed.** Dropped from the instrument as
  unresolvable by string match; under v2 they showed 78.9% and 97.6% miss, but those figures
  include false-positive text hits and should not be quoted.

---

## 9. What was downloaded, and where the code is

**Total downloaded: 5,595,664,788 bytes (5.21 GiB) of bulk files + 284,715,061 bytes
(0.27 GiB) of API responses = 5.88 GB.** One sequential `curl` per file, descriptive
User-Agent (`RAGStack-corpus-coverage-study/0.1 (contact: awilke1972@gmail.com; one-off
corpus coverage measurement)`), no parallel requests to the FTP host, 1 s between API calls.

Working directory: **`/rag/data/pubtator3/`** (writing there was permitted). It holds the
five bulk `.gz` files, the CARD downloads, the per-entity `*_bypmid.tsv` aggregates, the
57,267-row AMR scan, the 1,793-document API sample (`ft_raw/`) and every script. Source
files are the 2026-08-17 release (`Last-Modified: Mon, 17 Aug 2026 13:43:06 GMT`); sha256
prefixes are in `sha256.txt` and in the JSON report.

| file | what |
|---|---|
| `report-pubtator3.json` | every number above, machine-readable |
| `download.sh` | the sequential bulk download |
| `build_corpus.py` | `discovery.jsonl` → `corpus.tsv` |
| `agg.sh` | per-entity single-pass aggregation against the corpus PMID set |
| `coverage.py`, `density.py` | §2, §3, §5.1 |
| `ft_sample2.py` | §4 API sample |
| `amr_terms3.py`, `amr_scale3.py` | §6 instrument and scan |
| `amr_api_check.py` | §6.3 live-API confirmation |
| `parse_aro.py`, `aro_genes.py`, `aro_fit.py` | §7 |

The `.gz` sources, `ft_raw/` and the intermediate TSVs stay in the run directory; only the
scripts and `report-pubtator3.json` are copied here.
