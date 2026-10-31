<!-- experiment: why the reranker orders papers-OC worse than BM25 | 2026-10-30 | EXPLORATORY, post-hoc, offline -->
# Why the reranker orders papers-OC worse than BM25: an exploratory, post-hoc reading

**Tier: record. EXPLORATORY throughout, and post-hoc.** The plan, [`PLAN.md`](PLAN.md), was committed
(`d53e791`) **after** [`../RESULTS.md`](../RESULTS.md), the
[gold-scope addendum](../ADDENDUM-2026-10-30-gold-scope.md) and the 2026-10-30 lab-book entries were known,
and **before** any analysis here was computed. The script, [`analyse_why.py`](analyse_why.py), was committed
(`134654b`) before it was run. One block of descriptive counts, labelled **POST-PLAN**, was added after
the planned outputs had been seen (`9094c5a`); this file says where it uses that block. Nothing in
`RESULTS.md`, the addendum or the prereg is changed. Nothing here is a test. Set P has 7 papers, 138 of its
155 records sit in one paper, and its 155 records carry 73 distinct queries. Exact McNemar values are
printed because the plan asked for them. They describe; they decide nothing.

Source: [`why-results.json`](why-results.json) (counts only), run at `ff82cd4` from a clean tree
(`dirty false, untracked 0, citable true`, not in a tools image). No retrieval, reranker, store, gateway or
model call was made. Q4's scores were **looked up** in the run's hash-keyed score cache, never recomputed;
all 83 in-pool records' cached orders reproduce the committed reranked lists (missing 0, mismatched 0). The
v4 gold was rebuilt from the cross-check's builder chain (`variant4.py`, sha256 `1bfb7ae4…`), and the rebuilt
gold is byte-identical to the cross-check's (sha256 `3624a466…`).

Arms: **A0** is BM25 alone (top 10). **A4-50** is the default: rerank of the fused 50-pool, `content+gloss`.
**A5-50** is the rerank of BM25's own 50. Outcome classes compare A0 with A4-50 at top 10: **lost** (A0 has
the gold, A4-50 does not), **won**, **kept**, **neither**. Under the run's gold (D-3), the 153 included
records split into 20 lost, 11 won, 41 kept and 81 neither. All 20 lost records have their gold in the fused
50-pool, so every loss is an ordering loss.

## The headline, for a reader in a hurry

The four explanations the lab-book reading offered do not describe this data well. The data point
somewhere else:

- **The reranker loses on plain-word queries, not on exact-token ones.** 16 of the 20 lost records have
  no symbol, digit or unit in the query. On symbol queries the reranker gains 2 records and loses 0. For 13
  of the 20 lost records, the query has no rare token at all.
- **The chunks the reranker puts above gold come from other papers, not from the gold's own paper.** In the
  lost records, 46 of the 469 chunks the reranker ranks above gold (9.8 %) come from the source paper.
  BM25 ranks 66 chunks above gold in those same records, and 41 of them (62 %) come from the source paper.

So on these records BM25 stays inside the source paper and finds the gold among its neighbours. The
cross-encoder spreads the top of the list over about 50 other documents.

**Caveat (POST-PLAN):** the 20 lost records carry only **8 distinct queries**, and all 20 are in the
dominant paper. This may be one paper's property, not a corpus property.

## Per explanation

### 1. Exact-token task: **not supported** (the data run the other way)

The prediction was that losses concentrate in symbol, digit or unit queries, and that gold carries more of
the query's rare tokens than the chunks placed above it.

**Q1, query class** (`P.Q1`; found@10 out of n; gained / lost is A4-50 against A0):

| class | records (queries) | A0 | A4-50 | A5-50 | gained / lost | McNemar (descriptive) |
|---|---|---|---|---|---|---|
| symbol | 31 (18) | 16 | 18 | 18 | 2 / **0** | 0.5 |
| digit | 26 (20) | 10 | 8 | 10 | 2 / 4 | 0.69 |
| unit | 2 (2) | 0 | 0 | 0 | 0 / 0 | — |
| none of the three | 109 (44) | 37 | 30 | 30 | 9 / **16** | 0.23 |
| all | 153 (73) | 61 | 52 | 54 | 11 / 20 | 0.15 |

The classes overlap.

On docs-A (`docsA.Q1`, 732 questions) symbol queries are rare (21) and nothing is lost on them (1 / 0). The
digit class goes 6 / 0, and the plain class 77 / 6.

**Q2, rare tokens** (`P.Q2`). A rare token appears in at most 1 % of the bench copy's chunks (≤ 144 of
14,456). The population is the 83 records with gold in the fused 50-pool.

| outcome | records | query has no rare token | top non-gold carries fewer / equal / more rare tokens than gold | gold carries more than every chunk above it |
|---|---|---|---|---|
| lost | 20 | **13** | 1 / 4 / 2 | **0 of 7** |
| kept | 41 | 1 | 2 / 32 / 6 | 0 (15 at rank 1) |
| won | 11 | 3 | 0 / 5 / 3 | 0 (3 at rank 1) |
| neither | 11 | 2 | 2 / 5 / 2 | 1 |

Gold rarely out-matches the chunks the reranker prefers on rare tokens: 5 of 64 measurable records. Where
the reranker loses, the query usually has no distinctive term for an exact-match ranker to exploit. Among
the kept records, 40 of 41 have a query with a rare token.

### 2. Same-paper distractors: **not supported** (reversed)

The prediction was that, in lost records, the chunks above gold come mostly from the gold's own paper, more
than in won or kept records and more than in BM25's lists.

**Q3, same-paper share of the chunks ranked above the first gold** (`P.Q3`, pooled; per-record median in
brackets). The source paper is mapped as v4 maps it, and 155 of 155 records map to exactly one document.

| outcome | A4-50 (50-long reranked list) | A0 (BM25 list) |
|---|---|---|
| lost (20) | **46 / 469 = 9.8 %** [4.7 %] | **41 / 66 = 62 %** [67 %] |
| kept (41) | 40 / 115 = 35 % [21 %] | 45 / 84 = 54 % [73 %] |
| won (11) | 3 / 25 = 12 % [0] | 17 / 362 = 4.7 % [1.5 %] |

The pattern is symmetric. When the reranker loses, BM25's ranking stayed inside the source paper and the
reranker's did not. When the reranker wins, it is BM25 that wandered out of the paper. Over all records,
the source paper's share of the top 10 is 22 % for A0 (342 / 1,530) and 18 % for A4-50 (279 / 1,530).

**POST-PLAN** (`P.post_plan.lost`): the 423 cross-paper chunks the reranker puts above gold in the lost
records spread over **50 documents**. The most frequent document holds 14 % of them, and the top two hold
26 %. 273 of the 423 are in PMCID-named papers, 91 in DOI-named ones and 59 in sha-named ones. So the
distractors are not the two sha-named documents that held the addendum's removed cross-paper gold. 27 of
the 423 come from another record's source paper.

On docs-A, same-page in place of same-paper (`docsA.Q3_same_page`): in its 6 lost records, 6 / 88 (6.8 %)
of the reranker's chunks above gold share a gold page, against 2 / 31 (6.5 %) for BM25. Over all records the
reranker's top 10 is more page-local than BM25's: 38 % against 29 % (2,776 against 2,087 of 7,320).

### 3. Symbol tokenisation: **mixed, mostly not supported**

The prediction was that symbol queries lose more, that symbol-dense gold ranks worse after reranking but
not under BM25, and that the glossary helps symbol queries more.

- Symbol queries do **not** lose: 2 gained and 0 lost (Q1). Their gold's reranked rank has median **2**
  [1, 7] (19 records), against **9** [2.75, 22.25] for the other in-pool records (64; `P.Q4`).
- **The one supportive signal.** Gold symbol density (non-ASCII letters, combining marks, math symbols, and
  sub- and superscripts per 100 characters) correlates with a worse reranked rank: Spearman ρ = **+0.27**
  (n = 83). Against the BM25 rank it is ρ = −0.05 (n = 82). The densities are compressed (median 0.86, IQR
  0.73–0.88). **POST-PLAN:** the 83 records hold only **21 distinct gold chunks** and 45 distinct queries,
  so ρ rests on far fewer independent points than 83.
- **Q5, glossary** (`P.Q5`; only records whose in-pool gold carries a glossary). Adding the glossary moves
  gold up more often on **non-symbol** queries: up 36, down 6, same 8 of 50, and found@10 20 → 27 (shuffled:
  19). On symbol queries it is up 7, down 5, same 4 of 16, and found@10 11 → 15 (shuffled: 10). Where gold
  carries no glossary (17 records), adding glossaries moves gold **down** in 6 and up in 0. Only the
  distractors' glossaries change there.

### 4. Gold definition: **not supported as the explanation** (the pattern survives v4)

Q6 (`P_v4`, 151 included): 19 lost, 9 won, 38 kept and 85 neither.

| | committed gold (D-3) | v4 |
|---|---|---|
| A0 / A4-50 / A5-50 found@10 | 61 / 52 / 54 | 57 / 47 / 49 |
| class "none": gained / lost | 9 / 16 | 7 / 15 |
| class symbol: gained / lost | 2 / 0 | 2 / 0 |
| lost records: same-paper share above gold, A4-50 against A0 | 9.8 % against 62 % | 10.2 % (46 / 450) against 60 % (38 / 63) |
| top-10 same-paper share, A0 against A4-50 | 22 % against 18 % | 22 % against 18 % |

Gold looseness moves the totals, as the addendum records. It does not move the shape. The losses stay on
plain-word queries, and the reranker's distractors stay cross-paper.

## Score margins (Q4)

From the run's own cached scores, with the margin = top non-gold score − first-gold score (`P.Q4`):

| outcome | n | median | IQR | min |
|---|---|---|---|---|
| lost | 20 | **0.20** | 0.10–0.27 | **0.07** |
| kept | 41 | 0.01 | −0.04–0.09 | −0.30 |
| won | 11 | 0.06 | −0.03–0.15 | −0.28 |
| neither | 11 | 0.31 | 0.17–0.51 | 0.08 |

The losses are not near-ties: in every lost record the reranker scores some non-gold chunk at least 0.07
above the gold. It prefers the cross-paper chunks with confidence. (The run's noise-floor repeat found 0
records differing for the live reranked lists, `RESULTS.md`.)

## What contradicts the lab-book reading

1. **Not exact tokens.** The reranker's papers-OC losses are on queries without symbols, digits, units or
   rare tokens (16 / 20 in the plain class; 13 / 20 with no rare token). On symbol queries it does not lose
   at all.
2. **Not same-paper distractors: the reverse.** BM25 wins by staying inside the source paper (62 % of its
   chunks above gold are same-paper in the lost records). The reranker loses by promoting chunks from about
   50 other documents (9.8 % same-paper). The addendum suggested that the fused pool favours topical
   neighbours from other papers, and left it untested. This data places that tendency in the **reranker's
   ordering**, with the pool held fixed.
3. **The glossary's reranker gain is mostly on non-symbol queries** (36 / 50 records up), and glossaries on
   distractors only push gold down.

## Not measurable here

- **docs-A Q2, Q4 and Q5.** docs-A's chunk text exists only in the live index, which was out of scope, so
  only Q1 and Q3 were run on docs-A.
- **Why** plain-word queries favour the source paper under BM25. A candidate is the dominant paper's
  vocabulary or length; it needs the BM25 term statistics, which were not read.
- **Whether the cross-paper chunks answer the question** (C4 / E7: judged answer-presence). No judge was run.
- **Any corpus-level statement.** 7 papers, 138 of 155 records in one paper, 8 distinct queries behind the
  20 lost records, and one run.

## Limitations

7 papers, with 138 of 155 records in one paper; 73 distinct queries for 155 records; one run of lists.
Every class and outcome count is in records, not independent observations. The query classes, the rare-token
threshold and the density definition were fixed in `PLAN.md` before computing, but they were written
after `RESULTS.md` was known. Q4's ρ rests on 21 distinct gold chunks. The POST-PLAN block was added after
the planned outputs had been seen.

## Reproducing

At `ff82cd4` in a clean tree, with the run's private outputs, the papers-OC records and the rebuilt v4 gold
supplied by their owner:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python python3 -I docs/plans/results/reranker-test-2026-11/why/analyse_why.py \
  --root RUN_DIR --dump DUMP.jsonl --records-dir RECORDS_DIR --v4-gold P-gold-v4.json --v4-builder variant4.py \
  --out docs/plans/results/reranker-test-2026-11/why/why-results.json
```

The input sha256 values are in `why-results.json` (`inputs_sha256`).
