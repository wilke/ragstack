# Plan: why the reranker orders papers-OC worse than BM25 (exploratory, post-hoc)

**Tier: record.** Written and committed **before** any of the analyses below was computed. It was written
**after** [`../RESULTS.md`](../RESULTS.md), the [gold-scope addendum](../ADDENDUM-2026-10-30-gold-scope.md)
and the 2026-10-30 lab-book entries were known, so everything here is **post-hoc and EXPLORATORY**. No
analysis is confirmatory, no p-value decides anything, and nothing here edits `RESULTS.md`, the addendum or
the prereg. Exact McNemar values, where printed, are descriptive only: set P has 7 papers, 138 of its 155
records sit in one paper, and 155 records carry only 73 distinct queries (records sharing a query share
every list), so no test on P is valid.

## Inputs (all existing; nothing is retrieved, reranked or re-scored)

Read from the run's private output directory and the papers-OC record files; each input's sha256 goes into
`why-results.json`.

| input | used for |
|---|---|
| set-P pools file (`bm25@100`, `hybrid@50`, `live:*` lists) | A0 (prefix of `bm25@100`), A4-50 and A5-50 (the API's live top-10 captures, as the harness's `arm_map --prefer live` reads them), the `hybrid@50` pool |
| set-P rerank file (`rr:*` lists) | the full 50-long reranked orders (`rr:hybrid@50:<rendering>`), needed for ranks beyond 10; their top 10 equals the live capture on 155 / 155 (`identity_all`) |
| set-P rerank score cache (hash-keyed, order + scores, no text) | Q4. Scores are looked up, never recomputed: the cache key is the harness's own `RerankCache.key(query, rendered passages)`; a record whose key is missing, or whose cached order does not reproduce the committed list, is counted as *not measurable* |
| set-P gold, the run's own (D-3) | primary gold |
| set-P gold v4 (addendum) | sensitivity (Q6), rebuilt from the cross-check's uncommitted builder chain (`variant4.py`, sha256 `1bfb7ae4…`); the rebuilt file must be byte-identical to the cross-check's, else Q6 is reported *not measurable* |
| the bench copy's chunk dump (sha `cf40b916…`) | chunk text, glossary, document id, source file (source-paper mapping as in v4: pair `pmcid` → bench documents whose `source_path` stem is that pmcid) |
| held records (sha `c720261e…`) + pairs file (sha `f8d3b7f1…`) | queries and source papers, via the harness's `load_partner_held` |
| docs-A pools, rerank and gold files; docs-A question file (status = valid) | Q1 and Q3 contrast. docs-A chunk text is **not** read (it lives only in the live index, which is out of scope), so Q2, Q4 and Q5 are not run on docs-A; a docs-A chunk id carries its page (`<arm>|<page>|<n>`) |

Arms: **A0** = BM25 alone (top 10 of `bm25@100` on P, of `bm25@200` on docs-A); **A4-50** = the default
(rerank of `hybrid@50`, `content+gloss`); **A5-50** = rerank over `bm25@50`. On docs-A A4-50 and A5-50 are
the offline reranks (no live capture exists there), as in `RESULTS.md`.

Record outcome classes, per record with a gold chunk in the `hybrid@50` pool where stated:
**lost** = gold in A0's top 10 and not in A4-50's; **won** = in A4-50's top 10 and not in A0's; **kept** =
in both; **neither** = in neither. Rank of gold = rank of the first gold chunk (gold rule `any`; every P
record carries exactly one quote, so `any` = `all` there).

## Fixed definitions

**Text normalisation and tokens** (P and docs-A queries): the harness's `partner_norm` (NFKC, whitespace
runs → one space, strip, casefold), then the addendum's v2 tokenisation: combining marks re-attached
(`\s+(?=[U+0300–U+036F])` removed), word tokens = `[\ẁ-ͯ]+`.

**Query classes (Q1), not exclusive:**

- **symbol**: the raw query contains a Unicode sub- or superscript character (U+00B2, U+00B3, U+00B9,
  U+2070–U+209F) or a LaTeX command (`\[A-Za-z]+`), **or** a normalised word token contains a non-ASCII
  letter or a combining mark (Greek and the like), **or** a token has the subscript form
  `^[a-z][a-z0-9]*_[a-z0-9]+$` (the v2 symbol rule);
- **digit**: the normalised query contains `[0-9]`;
- **unit**: the normalised query contains `%` or `°`, or a number followed (optionally after one space) by a
  unit token from the fixed list below as a whole word, or a multi-letter unit from the list anywhere as a
  whole word. List: `k, kev, mev, gev, tev, ev, nm, µm, μm, mm, cm, m, km, s, ms, ns, µs, μs, ps, fs, hz, khz,
  mhz, ghz, thz, t, g, mg, kg, µg, μg, mol, mmol, l, ml, µl, μl, pa, kpa, mpa, gpa, bar, mbar, atm, torr, w,
  kw, mw, j, kj, v, kv, mv, a, ma, ω, ohm, ppm, ppb, da, kda, gy, sv, bq, ci, °c, k, kb, mb, gb, tb, kib, mib,
  gib, rpm, dpi, px, min, h, hr, d, yr`; "multi-letter anywhere" excludes `min, hr, yr, bar, pa, ms, ns, ps,
  fs, mb, gb, kb, tb, mw, kw, mv, ma, ml, mm, cm, km, ev, da` (English words or common abbreviations);
- **none**: none of the three.

Report, per class: records, distinct queries, found@10 for A0, A4-50, A5-50, and A0-vs-A4-50 gained / lost
with exact two-sided McNemar (descriptive). Same table on docs-A (n = 732).

**Rare token (Q2):** a normalised query word token whose **document frequency over the 14,456 chunks of
the bench dump** (token present in the chunk's `content+gloss` text, tokenised the same way) is **≤ 1 % of
chunks (≤ 144)**. Presence in a chunk = token-set membership in that chunk's `content+gloss` text (the text
the default reranker reads; BM25 also indexes the glossary field).

**Source paper (Q3):** the pair's `pmcid` mapped to bench documents as v4 maps it. **Same-page (docs-A):**
the page of any gold chunk of the record.

**Symbol density (Q4):** in the gold chunk's raw `content`, the count of characters that are non-ASCII
letters, combining marks, Unicode math symbols (category `Sm`), or the sub/superscript characters above,
per 100 characters of content.

## Questions and what each reports

**Q1 Lexical signal.** The class table above, P and docs-A.

**Q2 Exact-token advantage.** Population: P records with a gold chunk in the `hybrid@50` pool. In the full
`rr:hybrid@50:content+gloss` order: *gold* = the first gold chunk, *top non-gold* = the highest-ranked
non-gold chunk. Per record: number of the query's rare tokens in gold vs in the top non-gold → share of
records where the top non-gold shares **fewer** rare tokens than gold (also equal, more), and a secondary:
share where gold carries strictly more rare tokens than **every** chunk ranked above it (records with gold at
rank 1 have none above and are counted apart). Split by lost / kept / won / neither. Records whose query has
no rare token are counted as not measurable for Q2.

**Q3 Same-paper distractors.** For each record and each list (A4-50's full 50 order; A0's `bm25@100` order),
the chunks ranked above the first gold: count and share from the source paper. Report, split by lost / kept
/ won / neither: records, chunks above gold (pooled), same-paper share (pooled) and the per-record median
share. Also, all included records: same-paper share of the top 10 in A0 vs A4-50 (pooled). docs-A: the same
with same-page in place of same-paper (A4-50 = offline `rr:hybrid@50:content+gloss`, A0 = `bm25@200`).

**Q4 Score margins** (only if the cache reproduces the lists). For in-pool P records: margin = score of the
top non-gold − score of the first gold, in the `content+gloss` rerank of `hybrid@50` → median and IQR by lost /
kept / won / neither. Spearman ρ (average ranks for ties) between gold symbol density and the gold's
reranked rank (1–50), over in-pool records, and the same against the gold's BM25 rank where gold is in
`bm25@100`; also by query class symbol vs not. Descriptive only; records that share a query and gold chunk
are not independent.

**Q5 Glossary.** For in-pool P records, compare the first gold's rank in `rr:hybrid@50:content` (no glossary)
vs `content+gloss` vs `content+shuffled`: records where adding the glossary moves gold up / down / not, and
found@10 under each rendering. Split by whether any of the record's gold chunks carries a glossary, and by
Q1 class (symbol / not).

**Q6 Gold-rule sensitivity.** Repeat Q1's P table and Q3's headline (lost-record same-paper share, A4-50 and
A0, and the all-record top-10 shares) under v4 gold.

## The candidate explanations being examined

Named when this analysis was commissioned, from the lab-book reading of `RESULTS.md`. Each gets one of
*supported*, *not supported*, *mixed* or *not measurable* in `WHY.md`, with counts:

1. **Exact-token task.** papers-OC queries ask for exact values and symbols, which BM25 matches and a
   cross-encoder does not weigh. Expect: lost records concentrated in symbol / digit / unit queries (Q1);
   gold sharing more rare query tokens than the chunks placed above it, more so in lost records (Q2).
2. **Same-paper distractors.** The reranker promotes other chunks of the gold's own paper. Expect: in lost
   records, most chunks above gold in A4-50 are same-paper, more than in won / kept records and more than in
   BM25's lists (Q3).
3. **Symbol tokenisation.** The cross-encoder's subword tokenisation fragments symbols and equations.
   Expect: symbol queries lose more (Q1); symbol-dense gold ranks worse after reranking (Q4, ρ > 0 between
   density and rank) and not under BM25; the glossary (which spells symbols out) helps symbol queries more (Q5).
4. **Gold definition.** The loss is partly an artefact of the gold rule. Expect: the Q1 and Q3 patterns
   change materially under v4 (Q6).

## Output

`analyse_why.py` (committed before it is run) writes `why-results.json` — counts, ranks summaries and
correlation values only; input sha256s; `ragstack.provenance.experiment_provenance()` — and is run from the
clean commit that contains it, so the JSON is citable. `WHY.md` reports it. Hygiene: aliases only (docs-A,
papers-OC), the partner is "the papers-OC owner"; no query, quote or chunk text, no record ids, no paths or
host names in any committed file.
