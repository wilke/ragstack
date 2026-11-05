# Bib inbox 14 — alias glossaries, vocabulary mismatch, index-time expansion and corpus growth

> **Provenance.** Produced for `paper-c-alias-glossary` (the alias study, `docs/plans/results/alias-study-2026-11/`).
> Metadata was verified per entry as the status tag says. Tooling: the OpenAlex works API
> (`api.openalex.org`, search and DOI lookup with abstracts), the arXiv export API
> (`export.arxiv.org/api/query`), Crossref for one author-list check, and the ACL Anthology PDFs for
> the two entries marked *read* (text extracted with pymupdf). No web search was used. The Elsevier
> full text of `alier-2026` was not fetched (publisher wall), and that entry is marked accordingly.

**Status key.** `verified` = title, authors, venue and year confirmed on the publisher page, OpenAlex
record or arXiv abstract; `abstract` = the abstract was read as fetched; `read` = the full text was
retrieved and the methods, results and conclusions checked. No entry rests on recall.

**The question asked of every paper.** Does it (a) test attaching a document's *own* definitions to its
retrieval units, as opposed to expanding the query or prefixing a document summary; (b) use a
within-document control passage that already carries the term; (c) report a judge-free and a judged
measure side by side; (d) vary corpus *relatedness* at matched size, as opposed to size alone; (e)
pre-register anything. The answer for every entry below is given in its *what it supports* line.

**Keys reused from earlier surveys, not re-described here:** `cuconasu-2024-power-of-noise`,
`webber-2008-statistical-power`, `lakens-2017-equivalence-tests`, `fuhr-2017-common-mistakes`,
`sakai-2020-on-fuhrs-guideline` (file 05); `gunther-2024`, `conti-2025`, `anthropic-2024`,
`chen-2024-densex` (file 04); `faggioli-2023`, `thomas-2024`, `clarke-dietz-2024`, `soboroff-2025`,
`dietz-2025-tropes`, `alaofi-2024-cafe`, `norman-2026-reliability-without-validity`,
`haldar-2025-rating-roulette` (file 01).

---

## 1. The vocabulary problem and aliasing

### `furnas-1987` — The vocabulary problem in human-system communication
George W. Furnas, Thomas K. Landauer, Louis M. Gomez, Susan Dumais. Communications of the ACM 30(11),
964–971, 1987. <https://doi.org/10.1145/32206.32212> · **verified, abstract** (OpenAlex; 1,503 citations
there)

Spontaneous word choice for the same object in five domains; two people favour the same term with
probability below 0.20; a single designer-chosen access word fails 80–90 % of the time; the optimal
strategy derived is **unlimited aliasing**, many names per object, with several-fold improvement.

**What it supports.** The framing sentence of the Problem section: one object, many names, and the
system must carry the aliases. **Established name:** the field already calls the remedy *aliasing*; our
"alias" vocabulary is theirs and should be cited as such rather than presented as a coinage. (a)–(e): no;
it is a human-factors study, not a retrieval experiment.

### `carpineto-romano-2012` — A Survey of Automatic Query Expansion in Information Retrieval
Claudio Carpineto, Giovanni Romano. ACM Computing Surveys 44(1), 1–50, 2012.
<https://doi.org/10.1145/2071389.2071390> · **verified, abstract** (OpenAlex; 1,117 citations)

The standard survey of query-side expansion: why a few-keyword query mis-models the need, the steps of
an AQE component, the data sources and techniques, and the open issues before AQE becomes standard.

**What it supports.** The classical remedy is on the *query* side; our glossary is on the *document* side
and per paper. Cite for the contrast, not for a number. (a)–(e): no.

## 2. Abbreviations and symbols in scientific text

### `yu-2002` — Mapping Abbreviations to Full Forms in Biomedical Articles
Hong Yu, George Hripcsak, Carol Friedman. Journal of the American Medical Informatics Association 9(3),
262–272, 2002 (May–Jun). <https://doi.org/10.1197/jamia.m0913> · PubMed PMID 11971887 · **verified,
abstract** (author list from the PubMed esummary record, "Yu H, Hripcsak G, Friedman C"; OpenAlex and
Crossref both return a single author for this DOI, so the PubMed record is the source for the authors)

Pattern-matching rules (AbbRE) map defined abbreviations to full forms with 0.70 recall and 0.95
precision on ten articles from the ten most-cited medical and biological journals; **an average of 25 %
of abbreviations were defined in the article**; of undefined ones, 68 % could be mapped to one of four
public databases; "many abbreviations are ambiguous (i.e., they map to more than one full form)".

**What it supports.** Two premises of the design: that a paper defines its short forms (so a per-paper
glossary can be built from the paper itself) and that short forms collide across papers (so a
collection grown with related papers is the setting where an alias bridge can mislead). Mention-level
support for the premise; not a retrieval result. (a)–(e): no.

### `schwartz-hearst-2003` — A simple algorithm for identifying abbreviation definitions in biomedical text
Ariel S. Schwartz, Marti A. Hearst. Pacific Symposium on Biocomputing 2003, 451–462.
<https://doi.org/10.1142/9789812776303_0042> · **verified, abstract** (OpenAlex lists the year as 2002;
the proceedings volume is PSB 2003; 521 citations)

An untrained algorithm for pairing an abbreviation with its definition, 96 % precision and 82 % recall
on a standard test collection and 95 % / 82 % on a larger one.

**What it supports.** The task our glossary builder's abbreviation layer belongs to; cite as the
canonical reference for definition extraction, not for our builder's accuracy (which is not measured
against theirs). (a)–(e): no.

### `veyseh-2020-sciad` — What Does This Acronym Mean? Introducing a New Dataset for Acronym Identification and Disambiguation
Amir Pouran Ben Veyseh, Franck Dernoncourt, Quan Hung Tran, Thien Huu Nguyen. COLING 2020, 3285–3301.
<https://doi.org/10.18653/v1/2020.coling-main.292> · arXiv 2010.14678 · **verified, abstract** (OpenAlex +
arXiv agree on venue)

A manually annotated acronym-identification dataset of 17,506 sentences and a disambiguation dataset of
62,441 samples in the scientific domain; existing models "fall far behind human-level performance";
earlier datasets were "mostly limited to the medical domain".

**What it supports.** That acronym identification and disambiguation are studied as standalone NLP tasks
on scientific text, with no downstream retrieval measurement. That is the gap `alier-2026` also names.
(a)–(e): no.

### `head-2021-scholarphi` — Augmenting Scientific Papers with Just-in-Time, Position-Sensitive Definitions of Terms and Symbols
Andrew Head, Kyle Lo, Dongyeop Kang, Raymond Fok, Sam Skjonsberg, Daniel S. Weld, Marti A. Hearst. CHI
2021. <https://doi.org/10.1145/3411764.3445648> · **verified (title, full author list, venue, year via
the OpenAlex DOI record); abstract not read**

**What it supports.** Mention only: the reading-side version of what our glossary does at index time
(showing a reader the definition of a symbol or term where it is used). It establishes that the
"definitions are elsewhere in the paper" problem is recognised for human readers. Do not cite for a
number.

## 3. Index-time expansion and context for chunks

### `nogueira-2019-doc2query` — Document Expansion by Query Prediction
Rodrigo Nogueira, Wei Yang, Jimmy Lin, Kyunghyun Cho. arXiv 1904.08375, 2019.
<https://arxiv.org/abs/1904.08375> · **verified, abstract** (arXiv API; no journal reference on the record;
OpenAlex counts 381 citations)

Predicts the queries a passage could answer with a sequence-to-sequence model and appends them to the
passage before indexing; with a reranker, state of the art on two retrieval tasks; without one,
approaches neural-reranker effectiveness at much lower latency.

**What it supports.** The nearest mechanism family: expansion on the document side, applied at index
time, with the reranker's presence changing how much it matters. Our glossary is a *deterministic,
paper-sourced* expansion rather than a learned one, and our lift likewise shrinks under the cross-encoder.
(a) partly (document-side expansion, but not the document's own definitions); (b)–(e): no.

(For a document-level context prefix per chunk see `anthropic-2024`, non-peer-reviewed; for
contextualised chunk embeddings `gunther-2024` and `conti-2025`; for decontextualised propositions
`chen-2024-densex`. None attaches the document's own definitions, and none has a within-document
control passage.)

## 4. The acronym gap in RAG retrieval — the nearest prior work

### `alier-2026` — Closing the acronym gap in educational retrieval with ontology-guided augmentation
Marc Alier, Francisco José García-Peñalvo, María José Casañ, Juanan Pereira, Arnau Granados. Information
Sciences 740, 123181, 2026. <https://doi.org/10.1016/j.ins.2026.123181> · **verified (OpenAlex record),
abstract only — the full text is behind the publisher wall and was not fetched; NO baseline check has
been done.**

From the abstract: in acronym-dense course repositories "locally defined short forms often sit far from
their expansions in embedding space, yielding systematic retrieval failures even when relevant passages
exist"; prior acronym work "rarely quantifies downstream retrieval impact in RAG pipelines". They build
a corpus-scale acronym ontology (candidate mining, LLM-assisted definition extraction with evidence
spans, corpus-level disambiguation, light expert curation), then apply **Query-Time Augmentation** (append
likely short/long forms to the query) and **Ingestion-Time Enrichment** (inject complementary forms at
indexing), with optional embedding fine-tuning. On a ~10,000-document Catalan university corpus, with
nDCG@10, Recall@10/100 and Success@100 and paired-bootstrap tests over queries: QTA gives a 45.5 % average
overall improvement across models and "more than doubles Recall@10 for acronym-to-expansion queries";
QTA + ITE up to 64.5 % over baseline.

**What it supports, and what it contradicts.** It is the paper that names our problem ("the acronym
gap") and the one whose magnitudes a reviewer will hold against our one-point lift. Engage it directly:

- **Direction.** Their headline queries are *acronym → expansion*; our G records are *full term in the
  question → short form only in the passage*. These are different failure modes and the glossary
  bridges only ours.
- **Stack.** The abstract names embedding models and optional fine-tuning; it does not name a
  cross-encoder reranker. Our lift is 1.00 point under the cross-encoder and 2.50 / 4.17 points without
  it and under BM25, so the stack explains part of the gap in magnitude.
- **Corpus and ceiling.** Course documents against full-length research articles; our baseline already
  found 591 of 600 alias records, so there was one point of headroom to win. Their baseline headroom is
  unknown from the abstract.
- **Controls.** Nothing in the abstract corresponds to a within-document control passage, a placebo
  expansion, a judge-free sensitivity or a pre-registration.
- **Baseline check: absent.** A 45.5 % relative improvement is a statement about the baseline as much as
  the method, and the baseline numbers are not in the abstract. Until the full text is read, cite the
  direction of the result and the gap statement, not the multiples.

(a) partly (ITE injects forms at indexing, from a corpus-level ontology rather than the document's own
definitions); (b) no; (c) no (no judged measure; standard qrels); (d) no; (e) no.

## 5. Corpus growth, distractors and rare surface forms

### `reimers-gurevych-2021` — The Curse of Dense Low-Dimensional Information Retrieval for Large Index Sizes
Nils Reimers, Iryna Gurevych. ACL 2021 (short), 605–611. <https://aclanthology.org/2021.acl-short.77/> ·
arXiv 2012.14210 · **verified, read (full PDF)**

Proves and measures that the probability of a false positive in dense retrieval grows with index size
and shrinks with dimensionality, faster than for BM25, with a tipping point at which sparse overtakes
dense. Index built from the 7,433 relevant MS MARCO passages plus randomly added passages up to 8.8 M.
Table 1 (MRR@10 × 100, 10 k → 100 k → 1 M → 8.8 M): BM25 79.93 → 63.88 → 40.14 → 17.56; dense
768-dim with hard negatives 91.48 → 79.42 → 56.05 → 28.55; dense without hard negatives falls below BM25
at 8.8 M. Table 3: the share of queries for which a randomly generated string outranks the relevant
passage rises with index size for every dense model and is 0.00 % for BM25. Conclusion: "Dense
approaches work better for smaller, clean indexes"; with increasing index size the difference shrinks
and can invert.

**What it supports.** The expectation, and our descriptive finding, that growth costs gold-chunk reach
(`growth-vs-base`: −7.22 and −5.06 gold points for neighbour and random growth). **What it does not
speak to:** relatedness. Their added passages are random MS MARCO passages, so the paper varies size
alone; the neighbour-versus-random contrast at matched size is not in it. (d) no, size only; (a)–(c),
(e) no.

### `sciavolino-2021` — Simple Entity-Centric Questions Challenge Dense Retrievers
Christopher Sciavolino, Zexuan Zhong, Jinhyuk Lee, Danqi Chen. EMNLP 2021, 6138–6148.
<https://aclanthology.org/2021.emnlp-main.496/> · arXiv 2109.08535 · **verified, read (full PDF)**

EntityQuestions, simple questions from Wikidata facts. Top-20 retrieval accuracy (Table 1 and the full
Table 5 "Average" row): DPR (NQ) 49.7, DPR (multi) 56.7, BM25 71.2 on average; the authors write that
the gap reaches "60% absolute" on some patterns, and the largest per-pattern gap in Table 5 is 63.7
points ("Who is [E]'s child?": DPR-NQ 19.2 vs BM25 82.9). (A review suggested 72.0 and 65.8; those are
single cells of other tables, not the averages or the largest gap; the figures above were re-read from
the full text.) Dense
retrievers generalise to common entities and degrade on rarer ones unless the question pattern was seen
in training; data augmentation does not fix it; REALM is also below BM25 (19.6 points on average).

**What it supports.** Why a lexical bridge matters on rare surface forms, which a paper-specific
abbreviation or symbol is, and why the glossary lift is largest under BM25 and the un-reranked hybrid.
**Caveat:** their mismatch is entity rarity, not short-form/long-form mismatch, and their dense models
are 2021 DPR-class; our served embedder is stronger and the hybrid already carries BM25. Cite as
background for the mechanism, not as a prediction of our effect size. (a)–(e): no.

(For the generator-side result that *topically related* distractors harm a RAG reader more than random
ones, see `cuconasu-2024-power-of-noise` in file 05 and cite it precisely; it is a different endpoint
from retrieval reach, and our H2 null on judged reach does not contradict it.)

---

## Contradicting and complicating work

- **`alier-2026`** reports gains an order of magnitude larger than ours on an acronym problem. The entry
  above lists the differences (direction, stack, corpus, controls) and the missing baseline check; the
  paper must engage it as the nearest prior work and not around it.
- **`reimers-gurevych-2021`** implies that on a larger base corpus than 1,000 papers the gold-only cost
  of growth would be larger and the dense leg weaker; our growth figures are specific to a 32,765-chunk
  base, and the paper should say so.
- **`cuconasu-2024-power-of-noise`** would predict that the neighbour arm's *judged* answers, which come
  from related papers, are worse inputs to a generator than random-arm answers even when found@10 is
  equal. found@10 is reach, not answer quality, and the paper must not read H2's null as "neighbours are
  harmless downstream".
- **The LLM-judge negatives** (`soboroff-2025`, `clarke-dietz-2024`, `alaofi-2024-cafe`) apply with
  force: our judge agreed with the human reference on 0.872 re-weighted, and the paper's answer is the
  pre-specified flag plus the judge-free sensitivity, not a claim that the judge is adequate.

## Searched and did not find

OpenAlex works search, the query as typed and the total hits it reported; none of the top hits was a
controlled test of the thing named.

| query | hits | what came back |
|---|---|---|
| glossary attachment chunk retrieval abbreviation full term query | 27 | a thesis on structuring RAG content; unrelated reports |
| abbreviation expansion retrieval-augmented generation retrieval evaluation | 5,225 | RAG surveys and healthcare reviews; nothing testing abbreviation expansion in a RAG retrieval evaluation |
| citation neighbors corpus expansion retrieval distractors related documents | 124 | test-time corpus feedback, DAPR, citation-closure retrieval for compliance QA; nothing contrasting related against random corpus growth at matched size |
| pre-registered retrieval experiment information retrieval preregistration | 1,000 | preregistration literature in psychology, toxicology and cognitive modelling; no IR or RAG preregistration |
| symbol definitions scientific papers retrieval notation mathematical symbols question answering | 2,749 | `head-2021-scholarphi` (reading aid), mathematical IR surveys, notation-semantics modelling; no retrieval test of symbol definitions attached to chunks |
| document expansion with acronym definitions index time enrichment | 5,122 | `alier-2026` (the one hit that is on point), multimodal enrichment indexing, thesaurus enrichment for audiovisual archives |

Not searched (coverage limits, state if asked): the ACM Digital Library and Springer (publisher access
fails from this host), OpenReview, Semantic Scholar; forward citations of `alier-2026` (too recent to have
any in OpenAlex beyond 2).

## Established names for what we might have coined

| we were going to call it | the field already calls it | source |
|---|---|---|
| alias / aliasing (many names for one object as the remedy to vocabulary mismatch) | **unlimited aliasing** | `furnas-1987` — adopt; the term is theirs |
| the acronym problem in RAG retrieval | **the acronym gap** | `alier-2026` — adopt when describing the phenomenon, with the direction difference stated |
| expansion applied at indexing time | **document expansion** (`nogueira-2019-doc2query`); **Ingestion-Time Enrichment** (`alier-2026`) | use "document expansion" as the family name; our variant is paper-sourced definitions |
| correct paper retrieved, answering chunk missed | **within-document retrieval failure** | already adopted by the roster (`kobeissi-2026`, `docs/papers/README.md`) |
| a null read with its power floor | **the powered-null rule** (Webber, Moffat & Zobel 2008) | `webber-2008-statistical-power`, already in file 05 |

## COULD NOT VERIFY

- The full text of `alier-2026` (Elsevier). Only the OpenAlex abstract was read; no baseline numbers.
- The abstract of `head-2021-scholarphi` (metadata only; a mention-level entry).
