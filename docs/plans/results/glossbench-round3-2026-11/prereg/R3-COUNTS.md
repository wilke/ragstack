# Round-3 planning counts: attachment rules over the round-2 corpora (offline, 2026-10-29)

**Status: planning counts, not results.** Computed offline over the private round-2 inputs (chunk exports
`chunks-{oa,asm,openchia}.jsonl`, pure arm files `arms/*.jsonl`) with the committed library on `main` at
`d87a02e` (clean detached worktree, `dirty false, untracked 0`). Scripts and raw JSON beside this
file: `count_attachment.py` → `counts.json` (committed) and `per-paper-{O,A,P}.json` (per-paper counts keyed by paper
id, **private**); `sampling_power.py` → `sampling_power.json`; the stratified design pass →
`stratified_design.json`. No model, store, gateway or API was called. Counts only; no text was printed.

**Fidelity check.** The counting renderer replicates `Glossary.for_chunk` with one canonicalisation per
chunk; equality of rendered line counts with `for_chunk` itself was asserted on 400 seeded chunks per
corpus × 4 arms × 2 rules (0 mismatches). Value-only reach reproduces the round-2 manifests exactly:
O `qwen36p` 298 of 3,369 V chunks, A 7 of 341, eval-25 O 6 / A 2 (`pool-manifest-{O,A}.json`).

## 0. Definitions

| term | definition (all arm-blind unless stated) |
|---|---|
| **V** | value-statement chunk: `_glossbench_r2.value_statements` non-empty (a symbol-like token, not a unit, not a `STAT_TOKENS` entry, followed by `[=≈≃~∼]` and a number). Round 2's S1 ∪ S2. |
| **Σ(paper)** | the union of V tokens over the paper's chunks: the symbols the paper itself binds to a value somewhere. Uses no glossary. |
| **M_num** | chunk with no value statement that mentions a token of Σ(paper) as a whole token (`symbol_pattern`) and prints a number (`has_number`). The papers-OC owner's "quantity printed without `symbol = value`" blind spot, approximated without any glossary. |
| **M_nonum** | mentions a Σ token, no value statement, no number (negligible everywhere; dropped). |
| **R** | the rest. |
| **anysym** | chunk containing any token `is_specific_symbol` accepts (no glossary; the loosest symbol notion). |
| **value** | the served rule: `attach_only_with_value=true` — an entry renders where its symbol occurs with a value (`value_pattern`). |
| **mention** | `attach_only_with_value=false` — an entry renders where its symbol occurs at all (`symbol_pattern`). Every other cap unchanged (12 entries, 1,200 chars, positional). The **only** alternative attachment the code supports today. |
| **doc** | document-level attachment: every chunk of a document whose glossary has ≥ 1 entry carries the glossary. **Not supported by the code**; counted as "doc glossary non-empty" (rendering caps would still apply). |
| paper | O: `path_root` (article + its float units); A, P: the document. |

Attachment options `GlossaryConfig` supports (and nothing else): `attach_only_with_value` (bool; the
only attachment switch), `positional` (which definition, not whether), `max_entries_per_chunk` 12,
`max_chars_per_chunk` 1200 (render caps), `allow_single_letter` (extraction/validation, not attachment).
A file's header `config` is read strictly (`config_from_header` refuses unknown keys), so document-level
attachment needs a new key with a default equal to today's behaviour.

## 1. Corpus totals

| corpus | chunks | documents | papers | V | M_num | M_nonum | R | anysym | papers with Σ ≠ ∅ |
|---|---|---|---|---|---|---|---|---|---|
| O (OA-1000) | 32,765 | 12,560 | 1,000 | 3,369 (10.3%) | 8,285 (25.3%) | 14 | 21,097 | 23,069 (70%) | 673 |
| A (ASM-1000) | 27,418 | 1,000 | 1,000 | 341 (1.2%) | 1,104 (4.0%) | 0 | 25,973 | 23,011 (84%) | 140 |
| P (papers-OC) | 14,456 | 275 | 275 | 3,142 (21.7%) | 7,612 (52.7%) | 26 | 3,676 | 10,786 (75%) | 226 |

Σ(paper) size: O median 1 (p75 4, p90 9, max 43); A median 0 (p90 1, max 14); P median 4 (p75 11, p90 21,
max 109). One P document holds 2,453 M_num chunks (a book-length document); per-paper caps matter there.

## 2. Chunks with a non-empty glossary field, by arm × attachment rule (corpus-wide)

| corpus | arm | docs with entries | entries | **value** | **mention** | **doc** | mention/value | capped under mention |
|---|---|---|---|---|---|---|---|---|
| O | rules | 398 | 764 | 104 (0.32%) | 1,720 (5.2%) | 6,350 (19.4%) | 16.5× | 0 |
| O | scoutp | 576 | 1,772 | 218 (0.67%) | 4,327 (13.2%) | 11,825 (36.1%) | 19.8× | 6 |
| O | qwen38p | 707 | 2,641 | 323 (0.99%) | 4,878 (14.9%) | 14,909 (45.5%) | 15.1× | 2 |
| O | **qwen36p** | 777 | 3,209 | **300 (0.92%)** | **5,918 (18.1%)** | **15,695 (47.9%)** | 19.7× | 6 |
| A | rules | 143 | 199 | 3 (0.01%) | 850 (3.1%) | 5,302 (19.3%) | 283× | 0 |
| A | scoutp | 360 | 898 | 5 (0.02%) | 4,127 (15.1%) | 11,755 (42.9%) | 825× | 0 |
| A | qwen38p | 439 | 1,200 | 7 (0.03%) | 4,358 (15.9%) | 14,736 (53.8%) | 623× | 1 |
| A | **qwen36p** | 545 | 2,008 | **7 (0.03%)** | **6,246 (22.8%)** | **18,429 (67.2%)** | 892× | 2 |
| P | rules | 145 | 866 | 241 (1.7%) | 1,614 (11.2%) | 11,047 (76.4%) | 6.7× | 1 |
| P | scoutp | 162 | 1,060 | 392 (2.7%) | 1,945 (13.5%) | 11,536 (79.8%) | 5.0× | 2 |
| P | qwen38p | 185 | 1,841 | 498 (3.4%) | 2,151 (14.9%) | 12,420 (85.9%) | 4.3× | 4 |
| P | **qwen36p** | 185 | 1,857 | **509 (3.5%)** | **2,236 (15.5%)** | **12,303 (85.1%)** | 4.4× | 5 |

Lines per chunk under mention (qwen36p): O 1 line 3,550 / 2 lines 1,342 / 3: 517 / ≥ 4: 509; A 3,847 /
1,431 / 593 / 375; P 1,095 / 478 / 251 / 412. The 12-entry / 1,200-char caps bite on 0–6 chunks per
arm×corpus: **mention attachment is not cap-limited**. `value ⊂ mention` held on every chunk (0 exceptions).

Why "value" slightly exceeds the manifests' V reach (O qwen36p 300 vs 298): two chunks state a value for a
glossary symbol that `value_statements` stoplists (`STAT_TOKENS`, e.g. a two-letter lowercase token the
rules admit as a symbol). Negligible; noted so the two counts are not read as a discrepancy.

## 3. Where the field lands: by arm-blind population (qwen36p; other arms in `counts.json`)

| corpus | rule | in V | in M_num | in R | share of V reached | share of M_num reached |
|---|---|---|---|---|---|---|
| O | value | 298 | 1 | 1 | 8.8% | 0.0% |
| O | mention | 1,076 | 1,917 | 2,923 | 31.9% | 23.1% |
| O | doc | 2,164 | 4,786 | 8,732 | 64.2% | 57.8% |
| A | value | 7 | 0 | 0 | 2.1% | 0.0% |
| A | mention | 89 | 207 | 5,950 | 26.1% | 18.8% |
| A | doc | 235 | 802 | 17,392 | 68.9% | 72.6% |
| P | value | 509 | 0 | 0 | 16.2% | 0.0% |
| P | mention | 908 | 994 | 334 | 28.9% | 13.1% |
| P | doc | 2,257 | 7,174 | 2,848 | 71.8% | 94.2% |

Two readings. (i) **Value-only reaches M_num by construction never** (0–1 chunks): on that stratum the
value-vs-mention contrast is "a glossary on the gold chunk vs none", plus distractors. (ii) Under
**mention**, 49% (O), 95% (A) and 15% (P) of the chunks that gain a field are **R** chunks: no value
statement, no Σ-symbol mention with a number: that is the distractor load the #754 experiment saw.

Union over the four local arms (any arm renders): O value-in-V 369, mention-in-V 1,261, mention-in-M_num
2,292; A 8 / 100 / 241; P 577 / 1,031 / 1,340.

## 4. Per-paper distributions (qwen36p unless stated)

Quantiles over all papers of the corpus (O 1,000 / A 1,000 / P 275); "≥ k" = papers with at least k.

| corpus | quantity | median | p75 | p90 | max | ≥ 3 | ≥ 5 | ≥ 8 | ≥ 10 | ≥ 20 |
|---|---|---|---|---|---|---|---|---|---|---|
| O | V chunks | 2 | 4 | 9 | 118 | 384 | 240 | 130 | 83 | 18 |
| O | M_num chunks | 2 | 13 | 25 | 118 | 474 | 411 | 339 | 307 | 163 |
| O | value reach in V | 0 | 0 | 1 | 16 | 39 | 11 | 4 | 2 | 0 |
| O | mention reach in V | 0 | 1 | 4 | 25 | 135 | 71 | 28 | 18 | 2 |
| O | mention reach in M_num | 0 | 1 | 7 | 34 | 198 | 145 | 86 | 63 | 12 |
| O | mention chunks (all) | 3 | 9 | 17 | 62 | 515 | 413 | 291 | 239 | 66 |
| A | V chunks | 0 | 0 | 1 | 45 | 38 | 13 | 5 | 2 | 1 |
| A | M_num chunks | 0 | 0 | 1 | 61 | 78 | 62 | 48 | 37 | 13 |
| A | value reach in V | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| A | mention reach in V | 0 | 0 | 0 | 8 | 11 | 5 | 3 | 0 | 0 |
| A | mention reach in M_num | 0 | 0 | 0 | 41 | 18 | 14 | 6 | 5 | 2 |
| A | mention chunks (all) | 1 | 9 | 20 | 148 | 416 | 348 | 282 | 247 | 103 |
| P | V chunks | 4 | 9 | 31 | 153 | 165 | 124 | 80 | 68 | 44 |
| P | M_num chunks | 5 | 12 | 28 | 2,453 | 170 | 146 | 116 | 97 | 48 |
| P | value reach in V | 0 | 2 | 6 | 47 | 52 | 33 | 18 | 13 | 4 |
| P | mention reach in V | 1 | 3 | 11 | 62 | 84 | 52 | 38 | 32 | 9 |
| P | mention reach in M_num | 0 | 3 | 9 | 126 | 84 | 54 | 31 | 25 | 10 |
| P | mention chunks (all) | 3 | 10 | 20 | 143 | 147 | 115 | 84 | 72 | 28 |

Round 2's 25 eval papers under the same counts (reference): O V 144 / M_num 283, qwen36p value reach 6,
mention reach 98 chunks (33 in M_num); A V 119 / M_num 372, value 2, mention 291 (85 in M_num); P (fields
mode restricted the round-2 pool to prefilter-admitted chunks, so its manifest's 72 is a subset of) V 117 /
M_num 1,035, value 14, mention 169 (125 in M_num).

## 5. Eligibility and pool size per n_papers (the papers-OC owner's point 1: density as a selection criterion)

Eligibility rule proposed for round 3, **arm-blind with respect to the attachment arms** (the glossary is
a fixed factor shared by both arms): paper has V ≥ 2 **and** M_num ≥ 2 **and** V + M_num ≥ 10 **and** the
fixed Qwen3.6 glossary renders ≥ 1 chunk of the paper under mention (a paper whose glossary is empty is a
tie on every gold chunk under both arms and adds only noise).

| corpus | eligible papers (V≥2, M≥2, pop≥10, glossary≠∅) | with V≥3, M≥3 | with V≥1, M≥1 | eligible chunks | share of eligible-paper chunks with a field under mention |
|---|---|---|---|---|---|
| O | **288** | 236 | 317 | 13,050 | 21.9% |
| A | **28** | 22 | 36 | 1,479 | 27.0% |
| P | **112** | 107 | 116 | 10,470 | 18.2% |

Seeded random samples (1,000 draws) of n eligible papers, quotas qV from V and qM from M_num per paper
(all if fewer). "gold differs" = sampled chunks whose rendered field differs between the two arms (under
value the field is the value-only rendering; under mention it is a superset): the only records that can be
discordant through the gold chunk (the rest can differ only through distractors). Expected values from
per-paper reach shares under uniform within-paper sampling; p5/p95 over draws.

| design | V sampled | M sampled | V reached (value) | V reached (mention) | **V gold differs** | **M gold differs (= mention reach)** | total gold differs |
|---|---|---|---|---|---|---|---|
| O n=30, 6+6 | 141 [127,155] | 175 [169,180] | 14 | 56 | 42 [32,54] | 48 [37,61] | ≈ 90 |
| O n=40, 6+6 | 189 [174,204] | 234 [227,239] | 19 | 75 | 57 [44,70] | 64 [51,78] | ≈ 121 |
| O n=40, 8+8 | 224 [202,246] | 304 [291,315] | 23 | 89 | 66 [51,82] | 83 [64,100] | ≈ 149 |
| **O n=50, 6+6** | 236 [220,253] | 292 [285,298] | 24 | 94 | **70 [57,84]** | **80 [66,95]** | **≈ 150 [≈123, ≈179]** |
| A n=25, 4+4 | 84 [82,86] | 100 | 0 | 34 | 34 [29,38] | 28 [25,30] | ≈ 62 |
| A n=25, 2+6 | 50 | 150 | 0 | 19 | 19 [16,21] | 42 [37,45] | ≈ 61 |
| A n=30 | not enough eligible papers (28) | | | | | | |
| P n=25, 6+6 | 136 [127,143] | 147 [143,150] | 39 | 72 | 33 [25,41] | 52 [40,63] | ≈ 85 |
| P n=30, 6+6 | 162 [153,171] | 177 [173,180] | 46 | 86 | 40 [32,48] | 62 [49,76] | ≈ 102 |

Uniform (unstratified) sampling over V ∪ M_num, cap 12 per paper, for comparison (`sampling_power.json`):
O n=40: 475 chunks, gold differs 136 [111,163]; O n=50: 594 chunks, 170 [142,200]; A n=25: 295 chunks,
81 [68,94] (value reach 0); P n=25: 298 chunks, 86 [68,105].

**Round-2-style reach gate (≥ 150 chunks reached by at least one arm) under the two rules.** Under
value-only, no 25-paper sample of any corpus reaches it (O max per paper 16; A corpus-wide 8). Under
mention with the fixed Qwen3.6 glossary: O n=40 6+6 reaches ≈ 139 (75 + 64), n=50 ≈ 174; P n=25 ≈ 124,
n=30 ≈ 148; A n=25 ≈ 62 (and A's 28 eligible papers are the whole set: there is no sampling). **A
reach-based gate at 150 is reachable with margin only on O at n ≥ 50, and never on A.** A gate on "gold
differs" (what actually powers the contrast) is the better quantity; §6 of `PLAN-round3.md` sizes it.

**Can ASM-1000 qualify?** Not for a confirmatory leg. Value-only reaches 7 of 27,418 chunks (0 of them in
the 28 eligible papers' V stratum in expectation), M_num totals 1,104 corpus-wide of which Qwen3.6 reaches
207, and only 28 papers are eligible. A 25-paper A set yields ≈ 60 gold-differs records and power ≤ .5 at
d .17 / share .85 (§6). A can carry a **descriptive** leg only, with its floor quoted, and the contrast
there is "a glossary on the gold chunk vs none" because the served rule attaches nothing.

## 6. Power (committed `chunktrial_stats.simulate_power`, paper sign-flip, Holm worst case α/3)

Layout = the sampled per-paper quotas of one seeded draw shrunk by 1/1.3 (accepted ≈ drafted / 1.3);
between-paper SD of the gain share .15 (round-2 assumption); 1,500 sims × 1,500 flips, seed 20261030.
d = discordance (records whose found@10 differs), s = gain share among discordant. Not a measurement.

| stratum / design | accepted | one-sided d.10 s.85 | d.17 s.70 | d.17 s.85 | d.25 s.85 | two-sided d.17 s.85 | d.25 s.85 |
|---|---|---|---|---|---|---|---|
| O V, n=40 6+6 | 163 | .68 | .38 | .91 | .98 | .85 | .97 |
| O M_num, n=40 6+6 | 197 | .78 | .45 | .96 | .99 | .93 | .99 |
| **O V, n=50 6+6** | 186 | .74 | .43 | **.95** | 1.0 | **.92** | .99 |
| **O M_num, n=50 6+6** | 241 | .87 | .56 | **.99** | 1.0 | **.97** | 1.0 |
| O V, n=30 6+6 | 104 | .43 | .21 | .73 | .89 | .62 | .82 |
| O M_num, n=30 6+6 | 148 | .62 | .33 | .88 | .97 | .82 | .93 |
| A V, n=25 4+4 | 64 | .18 | .11 | .44 | .67 | .32 | .53 |
| A M_num, n=25 4+4 | 75 | .23 | .14 | .53 | .74 | .40 | .62 |
| A M_num, n=25 2+6 | 125 | .52 | .28 | .82 | .94 | .70 | .89 |
| P V, n=25 6+6 | 106 | .43 | .24 | .71 | .89 | .60 | .81 |
| P M_num, n=25 6+6 | 124 | .49 | .29 | .79 | .93 | .68 | .88 |
| P V, n=30 6+6 | 130 | .54 | .28 | .83 | .95 | .74 | .91 |
| P M_num, n=30 6+6 | 146 | .62 | .32 | .87 | .97 | .80 | .94 |

Pooled V ∪ M_num, uniform cap 12 (family of 4, `sampling_power.json`): O n=40 (356 accepted) one-sided
d.17 s.85 .997, d.10 s.85 .95, two-sided d.17 s.70 .57; O n=50 cap 10 (400) two-sided d.17 s.85 .997.

**What d to expect.** Round 1's model contrast had d .17 (26 of 155) with share .85. For the attachment
contrast, the gold field differs on ≈ 30% of sampled O chunks (§5: 150 of 528 at n=50); if found@10 flips
on a third to a half of those and distractor effects add a few percent, d ≈ .10–.17. The one-sided M_num
hypothesis is the one the theory predicts a direction for; the V-stratum contrast can go either way (the
#754 distractor penalty), so its power is quoted two-sided.

## 7. Cost of drafting at those sizes (round-2 drafter rates: 1.3 attempts/record, ≈ 1k input tokens/call, ≈ 250 output)

| set | papers × quotas | chunks drafted | drafter calls (× 1.3) | input tokens | output tokens | accepted (÷ 1.3) | verifier CLI calls |
|---|---|---|---|---|---|---|---|
| O | 50 × (6 V + 6 M) | ≈ 528 | ≈ 690 | ≈ 0.69 M | ≈ 0.17 M | ≈ 400 | ≈ 520 |
| P | 25 × (6 + 6) | ≈ 283 | ≈ 370 | ≈ 0.37 M | ≈ 0.09 M | ≈ 220 | ≈ 280 |
| P pilot (carried over, D-1) | 5 papers, 100 records | 100 | ≈ 130 | ≈ 0.13 M | ≈ 0.03 M | 100 | ≈ 130 |
| A (descriptive, optional) | 25 × (2 + 6) | ≈ 200 | ≈ 260 | ≈ 0.26 M | ≈ 0.07 M | ≈ 150 | ≈ 200 |
| **total with A** | | ≈ 1,110 | **≈ 1,450** | **≈ 1.45 M** | **≈ 0.36 M** | ≈ 870 | ≈ 1,130 (≈ $20, CLI dollars) |
| total without A | | ≈ 910 | ≈ 1,190 | ≈ 1.19 M | ≈ 0.29 M | ≈ 720 | ≈ 930 |

No glossary generation: both arms render the **existing** round-2 Qwen3.6 pure file (`oa/asm/openchia
.qwen36p.jsonl`, sha256s in `counts.json`). The frontier arm is dropped. Retrieval runs are local API
calls (no gateway): 6 arms × 3 corpora × 3 conditions in one run each, plus one `value` repeat per corpus.
The gateway total is ≈ 40% of round 2's planned 3.5 M input.

## 8. Two flags the owner asked for

**Re-indexing: not needed.** `backfill_symbol_glossary.py --glossary-file F` renders every chunk from the
file under the file's own header config and writes ES `_bulk` partial `update`s of exactly two fields,
`symbol_glossary` and `metadata.symbol_glossary_version` (`run()`, the `pending.append({"doc": {...}})`
body). `content` and the embedding are untouched; Qdrant is never written (its payload has no glossary;
`retrieval/glossary.py`). Swapping the attachment rule is therefore the same operation as swapping the
arm in round 2: a file whose header says `attach_only_with_value: false`, a dry run, apply, census. What
is **missing in code**: `--glossary-file` refuses `--config`, and a file carries one config, so the
mention arm needs a **re-configured copy of the file** with a version mark that the stamp grammar can
tell from the value arm (today's grammar `<base>[+merge=<mode>]@file/1[+shuf…]` has no attachment
slot; two arms that differ only in the header config would stamp identically and `stamp_allowed` could
not tell them apart). See `PLAN-round3.md` § code.

**Reranker input: yes, it changes.** `scoring.scorers.rerank_passage` scores `content + "\n" +
symbol_glossary` whenever `RERANK_INCLUDE_SYMBOL_GLOSSARY` is on (default on) and the chunk carries a
field; `retrieval/glossary.py` hydrates vector-only candidates from ES before the rerank. Under mention,
the cross-encoder sees glossary text on 15–23% of chunks instead of 0.03–3.5%, up to 12 lines / 1,200
chars more per passage. The cross-encoder sidecar runs at `MAX_LENGTH` 4096 tokens, so a 512-token chunk
plus the field is not truncated. The `norerank` and `bm25` conditions isolate the BM25-side effect from
the reranker-side effect; both are in the runner already. One more consequence: the boost probe
(`glossary_boost_probe.py`) wants a token present in **at most 10 glossaries**; under mention the same
definition is rendered on every chunk that mentions the symbol, so qualifying tokens are rarer and the
probe can come back `not_testable` (then `--boost-declared` records the declared value). Check before
freezing on the rendered fields offline.
