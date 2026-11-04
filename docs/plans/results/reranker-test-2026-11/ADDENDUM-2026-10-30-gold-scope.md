# Addendum, 2026-10-30: set P under four gold variants — gold looseness is not neutral

**Tier: record.** This file corrects nothing in [`RESULTS.md`](RESULTS.md), which stands as written. It
adds a sensitivity analysis of the set-P figures and is a new file, because the record is not edited.
**Everything here is exploratory.** Set P was exploratory by design (prereg Status, §4). H1–H3 on P are
**GATE-NOT-EVALUABLE** under every variant, for the reasons `RESULTS.md` lists: 7 clusters, and
`bm25@100` under-served on 19 records (D-5). No figure below is a test.

Source: [`artifacts/rr-P-gold-sensitivity.json`](artifacts/rr-P-gold-sensitivity.json) (counts only),
built by [`artifacts/make_rr_gold_sensitivity.py`](artifacts/make_rr_gold_sensitivity.py). The partner
that holds the papers-OC collection is "the papers-OC owner". Its commits cited below are in its own
repository. They are **external**: this repository holds none of them.

## What was done

Set P's gold was rebuilt offline four times from the held records, the pairs file and the bench-copy chunk
dump. The run's own lists were then re-scored with the committed harness's `stats` at `6e620c2` (clean
tree, `citable: true`), using the run's parameters (seed 261029, 10,000 flips, `--prefer live`,
`--gold-rule any`). Each `stats` record carries an `entry point outside checkout` warning, which the
artifact's `citable_reason` repeats. No retrieval, reranker or store call was made. Re-scoring the committed gold the same
way reproduces every committed found@10 (`committed_column_reproduced_by_harness_rerun: true`).

The four gold builders are **uncommitted offline scripts**. Their sha256 values are recorded in the
artifact, which is therefore `citable: false`. It is a reportable sensitivity, not a citable result.

| variant | gold rule | n included |
|---|---|---|
| committed | the papers-OC owner's quote rule as #871 implements it (D-3): containment, or the first/last ⌈0.6·len⌉ characters, with no minimum length | 153 |
| v1 | committed rule, with a **30-character floor** on the boundary fallback | 151 (2 lose all gold) |
| v2 | a short-anchor (< 30 characters) boundary match counts only if the chunk also carries every figure and every symbol token of the quote. This is the papers-OC owner's symbol-token rule (aad81fe) | 153 |
| v3 | short-anchor boundary matches under the papers-OC owner's **assignment rule** (6c71ef1). We implemented it from the owner's description. It passes all **14** of the owner's published match vectors (6fbd3f0; vectors-file sha256 `aea0c258…`). A second implementation built from the vectors file (v3b) gives identical figures and differs from v3's gold on 1 record | 153 |
| v4 | v3 plus **source-paper scoping**: gold is restricted to chunks of the record's own source paper | 151 |

**What v4 removes.** Under v3, **64 of 153** records had gold in a paper other than their source. For
50 of them the quote is long (≥ 50 normalised characters), so cross-paper gold is not only a short-quote
artefact. Scoping excludes 2 records, which have no gold in their source paper, leaving 151.

A document-identity check covered all the removed gold: 78 chunks in **2 documents**. Both are sha-named
documents, and neither is a copy of any source paper. The largest share of any source paper's long
sentences that either document holds is 3.6 %, and no source paper has a ≥ 50 % copy in the index. The
paper mapping used for scoping is the same as round 2's set-P exclusion list. The bench copy's document
kinds (238 PMCID-named, 21 sha-named, 15 DOI-named, 1 other) agree with the papers-OC owner's corrected
count at f5dec0b (external).

**Gold size** (chunks per included record): committed 87 × 1, 56 × 2, 3 × 4, 3 × 11, 2 × 32, 2 × 50;
v3 91 × 1, 57 × 2, 2 × 5, 3 × 8; v4 146 × 1, 5 × 3.

## The table

found@10, one run, records of n included (`found@10.<arm>`):

| arm | committed | v1 | v2 | v3 | v4 |
|---|---|---|---|---|---|
| A0 bm25 | 61 | 57 | 58 | 58 | 57 |
| A1 dense | 9 | 7 | 7 | 7 | 7 |
| A2 hybrid-shallow | 50 | 46 | 46 | 46 | 46 |
| A3 fused 50-pool top 10 | 20 | 17 | 18 | 18 | 17 |
| A4-10 | 50 | 46 | 46 | 46 | 46 |
| A4-20 | 54 | 51 | 52 | 52 | 51 |
| **A4-50 default** | **52** | **49** | **51** | **51** | **47** |
| A4-100 | 54 | 51 | 53 | 53 | 49 |
| A5-10 | 61 | 57 | 58 | 58 | 57 |
| A5-20 | 62 | 59 | 60 | 60 | 59 |
| A5-50 rerank over bm25 | 54 | 51 | 53 | 53 | 49 |
| A5-100 | 59 | 56 | 58 | 58 | 56 |
| A6 rerank over dense | 15 | 13 | 13 | 13 | 13 |
| A7-`content` | 43 | 39 | 41 | 41 | 36 |
| A7-`content+gloss` | 52 | 49 | 51 | 51 | 47 |
| A7-`gloss+content` | 40 | 37 | 38 | 38 | 35 |
| A7-`content+shuffled` | 41 | 38 | 40 | 40 | 35 |
| A7-`gloss-only` | 55 | 51 | 52 | 52 | 51 |
| A9-bm25 | 62 | 59 | 61 | 61 | 59 |
| A9-fused | 44 | 40 | 42 | 42 | 40 |

v3b equals v3 in every cell (`found@10.<arm>.v3b`).

**Ceilings** (`ceilings`): records with a gold chunk anywhere in the first D candidates of a list.

| ceiling | committed | v1 | v2 | v3 | v4 |
|---|---|---|---|---|---|
| bm25@10 / 20 / 50 / 100 | 61 / 75 / **82** / 94 | 57 / 71 / **78** / 91 | 58 / 72 / **80** / 92 | 58 / 72 / **80** / 92 | 57 / 69 / **76** / 91 |
| hybrid@10 / 20 / 50 / 100 | 50 / 65 / **83** / 93 | 46 / 59 / **78** / 88 | 46 / 60 / **80** / 90 | 46 / 60 / **80** / 90 | 46 / 59 / **74** / 86 |
| vector@10 / 20 / 50 / 100 | 9 / 12 / 19 / 34 | 7 / 8 / 14 / 29 | 7 / 8 / 14 / 30 | 7 / 8 / 14 / 30 | 7 / 8 / 14 / 22 |

**H1–H3** (`hypotheses`): gained / lost, exact p, and status. All three are exploratory and
GATE-NOT-EVALUABLE in every column.

| H | contrast | committed | v1 | v2 | v3 | v4 |
|---|---|---|---|---|---|---|
| H1 | A0 vs A4-50, found@10 | 61 vs 52; 20 / 11; p 0.5 | 57 vs 49; 19 / 11; 0.5 | 58 vs 51; 19 / 12; 0.5 | 58 vs 51; 19 / 12; 0.5 | **57 vs 47; 19 / 9; 0.5** |
| H2 | bm25@50 vs hybrid@50, ceiling | 82 vs 83; 2 / 3; p 0.875 | 78 vs 78; 2 / 2; 0.75 | 80 vs 80; 2 / 2; 0.75 | 80 vs 80; 2 / 2; 0.75 | **76 vs 74; 4 / 2; 0.375** |
| H3 | A5-50 vs A0, found@10 | 54 vs 61; 11 / 18; p 0.266 | 51 vs 57; 11 / 17; 0.117 | 53 vs 58; 12 / 17; 0.0078 | 53 vs 58; 12 / 17; 0.0078 | 49 vs 57; 9 / 17; 0.508 |

The p-values are the gated harness's exact sign-flip enumeration over 7 clusters. They are printed
because the harness prints them. They decide nothing.

## Findings (exploratory)

1. **Under v1–v3 no reported direction reverses.** The quote-rule variants lower every arm by 1–4 records
   (v1 by 2–4). The keyword lead over the reranked default (A0 − A4-50) narrows from 9 to 8 (v1) and then
   to 7 (v2, v3). H2's ceiling contrast becomes a tie, 2 / 2, under all three. The one exploratory
   contrast whose sign changes, A5-100 − A0 (net −2 → −1, 0, 0, −1), moves to a tie and does not
   reverse. The papers-OC owner's caution applies to v1: a uniform drop under a length floor is weak
   evidence, because the floor removes true and false matches alike. v2 and v3 discriminate, and they agree
   with v1 in direction.
2. **Under v4 the picture moves the other way.** The keyword lead **widens to 10** (57 vs 47). The
   ceiling@50 comparison **flips sign**: BM25's 50-pool holds gold for 76 records and the fused pool for
   74. The committed figures were 82 vs 83. Scoping removes 4 records from A4-50 and 6 from the hybrid@50
   ceiling, but only 1 from A0 and 4 from the bm25@50 ceiling. H2 is exploratory and GATE-NOT-EVALUABLE
   in every column. The flip is a description of these 151 records, not a reversal of anything tested.
3. **The 32-chunk pair is genuine gold ambiguity, not a rule artefact.** The committed rule gave one pair
   (2 records) 32 gold chunks. Under v3 it has 5:
   - 4 chunks contain the full quote, and they sit in 2 documents;
   - the fifth matches on its head anchor only. It is in a third document, does not straddle a chunk
     join, and assigns the same variable the same value.

   The same short assignment is printed in three documents. No quote rule can tell them apart. Only source
   scoping can, and v4 keeps the 3 chunks in the source paper. The 50-chunk pair falls to 1 gold chunk
   under v1–v3.
4. **The narrow conclusion: gold looseness is not neutral.** The direction of its effect depends on the
   kind of looseness and on what each arm retrieves:
   - the loose quote rule, short anchors matching in many places, **flattered keyword**. Tightening it
     costs A0 more than A4-50 (−3 vs −1 under v2 and v3);
   - cross-paper gold **flattered the reranked and fused arms**. Scoping it away costs A4-50 more than A0
     (−4 vs −1 from v3 to v4).

   Why each arm gains from each kind of looseness was not measured (absent). One plausible reading is
   that BM25 favours documents where a short string recurs, and the fused pool favours topical neighbours
   from other papers. Neither is tested here.

   The two corrections pull the BM25-vs-default gap in opposite directions, so an analysis that applies
   one and not the other is biased in a knowable direction. Measure gold looseness per set and per kind,
   and report the gold rule and its scope beside every figure. This agrees with the papers-OC owner's
   review notes on the same question at eccea19, 4a9030d, 7a10f05, 9564b31 and 6c71ef1 (external).

## What this does not change

- **`RESULTS.md` stands.** Its set-P figures are the committed column above, under the gold rule D-3
  names, and remain the record of the run as pre-registered.
- **Family A (docs-A) is untouched.** It uses its own frozen gold rule (`build_gold`, the parts 2–4
  normalisation). Neither short-quote nor cross-document sensitivity was measured on docs-A (absent).
- **Nothing on P becomes evaluable.** The gates are cluster count and depth read-back, and no gold variant
  changes either.
