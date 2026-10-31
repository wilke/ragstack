# Alias study — abbreviation glossary and OA-1000 recount

Status: offline counts, 2026-10-30. No model, gateway, store or API call was made.
Script: `python/scripts/eval/alias_abbrev.py` (tests: `python/tests/eval/test_alias_abbrev.py`).
Run at commit `34fa58f` (clean tree, 0 untracked; `experiment_provenance()` embedded, `citable: true`).
Outputs are private (`/rag/tmp/alias-study-2026-11/`, mode 0600) because they hold paper text.
This page holds counts only: no paper text and no paper ids. Examples are synthetic.

| input | sha256 (prefix) |
|---|---|
| OA-1000 export `chunks-oa.jsonl` (32,765 chunks, 12,560 documents, 1,000 papers; paper = `source_path` root) | `ff16e611` |
| round-3 symbol glossary, arm `oa.qwen36p.mention.jsonl` (`llm` ∪ `rules` entries) | `1e1845dd` |
| word list `/usr/share/dict/american-english` (all-lowercase entries) | in `recount.json` |

| output | sha256 (prefix) |
|---|---|
| `glossary/abbrev_entries.jsonl` | `0f2c37e3` |
| `glossary/merged_glossary.jsonl` | `229519d7` |
| `glossary/recount.json` (counts, provenance, input hashes) | `1b666a60` |

## Definitions (owner)

A glossary entry maps an ALIAS to a PRIMARY TERM. **G-candidate:** a chunk of the paper that contains the alias as a whole token and does not contain the primary. Under the strict rule, that means neither the primary's word sequence nor any content word of the primary with 5 or more characters appears in the chunk. **C-candidate:** a chunk of the same paper that contains a primary term of the paper literally. The matchers and normalisation are the feasibility count's (`feas.py`, 2026-10-30), restated in the script. Re-running the symbol-only glossary reproduces that count exactly: 1,117 G entries, 255 papers with ≥2, 575 papers with a C chunk, 226 usable papers.

## Abbreviation rule

An abbreviation enters the glossary when it is **defined in the paper**:

* **Patterns.** P1 `long form (ABBR)`, where the closing paren may be replaced by `;` or `,` followed by a space, e.g. `fetal bovine serum (FBS; supplier)`. A space must precede the parenthesis. The long form is the shortest run of words immediately before the parenthesis that passes the alignment check. It never crosses `, ; : ! ? ( ) [ ]` or a sentence-ending period. P2 is `ABBR (long form)`. Its long form is the paren content up to the first `,` or `;`, with at least 2 words, and is skipped when it starts with `i.e.`/`e.g.`/`see`/`ref`/`fig`. The pattern `ABBR, long form,` is **not used**: it is too often an ordinary list.
* **Letter alignment.** All letters and digits of the abbreviation must occur in the long form in order. The first letter must start the long form, and the long form cannot start with a function word. **The initial of every content word must be one of the matched letters.** Function words (`a an and as at by for from in into its of on or per the to via with`) are exempt. Letters may also match inside words. So `deoxyribonucleic acid` passes for DNA and `messenger RNA` for mRNA, but `body mass index score` fails for BMI and `chain reaction` fails for PCR.
* **Long-form size.** At most 8 words. A one-word long form needs at least 6 characters and at least twice the abbreviation's length (e.g. `lipopolysaccharide` for LPS).
* **Abbreviation form.** The abbreviation is ASCII letters and digits only, 2 to 11 characters. Greek and barred symbols belong to the symbol glossary. A trailing plural `s` is stripped (`MSCs` becomes `MSC`), and the use match accepts the alias with or without that `s`.
* **Exclusions** (instances dropped on OA-1000 in brackets):
  * single letter (36);
  * pure number;
  * **casing**: it needs ≥2 capitals, or exactly 1 capital that is not the first character. This drops `Fig`, `Ref` and lowercase words, and keeps `mAb`-type forms (736);
  * a unit list (`mM`, `kDa`, `mmHg`, `pH`, `IU`, …) (19);
  * roman numerals made of I/V/X (7);
  * **common English word**: the lowercase form is an all-lowercase entry of the system word list (3+ letters), or a two-letter function word such as `OR`, `IT` or `US` (1,106).

  The alignment check removes gene- or species-like tokens that are not abbreviations, e.g. a formula after a chemical name. No separate gene list is used.
* **Conflicting definitions.** If a paper defines one abbreviation with unrelated long forms (no shared content word), the abbreviation is dropped for that paper (44 dropped). Otherwise the most frequent long form wins.
* **Use elsewhere is recorded, not required** (owner correction). Each entry carries `def_chunk_id`, every definition chunk, `use_chunk_ids` and `used_elsewhere`. `use_chunk_ids` lists the chunks of the paper that contain the alias but no definition of it. All counts are given both ways.

Merging keeps the symbol glossary unchanged and adds the abbreviation entries, keyed by (paper, alias, primary words). Each entry has a `kind`: `symbol`, `abbreviation`, or `both` when both glossaries hold the same alias→primary.

## Entries

| | all defined | used elsewhere |
|---|---|---|
| definition instances matched (P1 / P2) | 15,497 / 373 | |
| abbreviation entries | 10,152 | 6,577 |
| papers with ≥1 abbreviation entry | 974 | 951 |
| merged glossary (abbreviation / both / symbol) | 10,061 / 91 / 3,580 | 6,512 / 65 / 3,606 |

**Collision**: an alias that appears in ≥2 papers with long forms sharing no content word. Of 4,402 distinct aliases (all defined), 1,435 appear in ≥2 papers and 723 of those collide. That is 50.4% of shared aliases and 16.4% of all aliases, and 40.7% of entries carry a colliding alias. For the used-elsewhere subset: 472 of 976 shared aliases collide (48.4%), and 36.4% of entries carry a colliding alias.

**Precision spot check**: 40 entries were drawn with seed 20261030 into a private review file for the owner. The author found no obvious error. One entry is borderline: a family prefix whose long form is correct but which is mostly used before a number.

## Recount (strict rule; termlike primary — symbol ≤5 words, abbreviation ≤8 words, no digit-only word)

Paper counts use **distinct G entries** per paper. The kind split shows whether a paper's G entries come from symbol entries only, abbreviation entries only, or both.

| glossary | ≥1 G | ≥2 G | ≥3 G | with C | usable (≥2 G + C) |
|---|---|---|---|---|---|
| symbol only (round 3) | 452 | 255 | 143 | 575 | 226 |
| abbreviation, all defined | 918 | 799 | 677 | 952 | 778 |
| abbreviation, used elsewhere | 918 | 799 | 677 | 922 | 771 |
| **merged, all defined** | **939** (sym 21 / abbr 486 / both 432) | **847** (12 / 404 / 431) | **749** (9 / 326 / 414) | **932** | **800** (11 / 390 / 399) |
| merged, used elsewhere | 939 (21 / 486 / 432) | 847 (12 / 404 / 431) | 749 (9 / 326 / 414) | 911 | 791 (11 / 386 / 394) |

The two abbreviation variants produce the same G entries (5,078 in both). This is expected: a G chunk holds the alias without the long form, so the alias is always used outside a definition. The variants differ only in their C candidates, because unused entries still contribute long forms that C can match.

**Other chunks containing the alias**, per G-candidate (entry × chunk), merged glossary, given as median [p25, p75]:

| kind | corpus-wide | same paper |
|---|---|---|
| abbreviation | 60 [22, 181] | 14 |
| symbol | 132 [29, 450] | 35 |

Almost no G-candidate alias is unique in the corpus: the share with zero other chunks is 0.02%. Gold uniqueness has to come from the question, not from the alias.

## Yield and cost for 100 usable papers

Target: 100 papers, each with ≥2 accepted G and ≥1 accepted C. Each drafted job is accepted with probability p. G is drafted one entry at a time, stopping at 2 accepted, at most 6 jobs. C is drafted up to 2 times, stopping at the first accepted, at the same p. Papers are drawn at random from the usable pool, using pool means as in `feas.py`. Calls per job: 1.616. Tokens per call: 870 in / 2,224 out, the round-3 set-A average after the reasoning change.

| glossary | p | expected usable if the whole pool is drafted | papers to attempt | jobs | calls | tokens in / out |
|---|---|---|---|---|---|---|
| merged, all defined (pool 800) | 0.20 | 72.8 | **not reachable** | — | — | — |
| | 0.315 | 198.7 | 403 | 2,313 | 3,737 | 3.25 M / 8.31 M |
| | 0.386 | 292.1 | 274 | 1,478 | 2,389 | 2.08 M / 5.31 M |
| merged, used elsewhere (pool 791) | 0.20 | 71.9 | **not reachable** | — | — | — |
| | 0.315 | 196.3 | 403 | 2,313 | 3,737 | 3.25 M / 8.31 M |
| | 0.386 | 288.7 | 274 | 1,478 | 2,389 | 2.08 M / 5.31 M |
| symbol only (pool 226) | any | 10.4 / 31.1 / 48.8 | not reachable | | | |

The cost drops if C is drafted only once a paper has 2 accepted G. In that case the merged, all-defined glossary needs 3,154 calls at p = 0.315 and 2,093 at p = 0.386. At p = 0.20, drafting all 800 usable papers yields about 73.

## Limits

* The precision figure is the author's reading of 40 sampled entries. It is not the owner's verdict, which goes in the review file.
* p is assumed. The C acceptance rate is assumed equal to the G rate; the feasibility model used 0.5.
* The collision rule uses disjoint content words, so a synonym long form (same expansion, different words) counts as a collision. The 50% rate is therefore an upper bound on true ambiguity.

## Independent review (PR #887, 2026-10-30)

These are post-hoc checks by a reviewer on the same run's outputs. They did not change the builder or the counts above.

* **Reproduction.** A re-run from a clean checkout at `34fa58f` on the same inputs gives byte-identical `abbrev_entries.jsonl` and `merged_glossary.jsonl`. `recount.json` is identical apart from its time fields.
* **Precision.** The reviewer judged 60 entries drawn with a different seed (887). 57 are correct. The three errors:
  * one partial long form: the shortest aligned run took letters from inside words, giving `EMEM = essential medium`;
  * one boundary artifact: a leading `-` copied into the long form, with the expansion itself correct;
  * one place code that is not an abbreviation (a county name).
  
  The sample held no units, gene names or `IL`-type prefixes. Typos in the source are copied faithfully and were not counted as errors.
* **Partial long forms, corpus-wide.** About 50 of 9,922 P1 entries (≈0.5%) have a longer aligned run that covers more of the abbreviation's letters as word initials. Most have repeated letters, e.g. `PPI = pump inhibitors` or `MEM = medium`. The rule keeps the shortest run. Such a partial primary makes the strict G test looser for that entry.
* **Collision, normalised.** Lemmatised and case-folded words already absorb plural, case and hyphen variants. With a stronger normalisation (5-character prefixes of content words, plus `our`→`or`), 688 of 1,435 shared aliases still collide (47.9%). Sharing any non-stop 4-character prefix gives 666 (46.4%). A sample of 25 colliding aliases was almost all genuine homonyms (`AP`, `PA`, `NES`, `PIC`). The 50% figure is therefore not inflated by trivial variants.
* **G matcher and hyphens.** Tokens split at hyphens, so `IL-6` yields the alias `IL`, `RT-PCR` yields `RT`, and `SARS-CoV-2` yields `SARS`. In 2,320 of 26,518 abbreviation G pairs (8.7%), every alias occurrence in the chunk is hyphen-attached. Dropping those pairs gives merged ≥2 G 847 → 838 and usable 800 → 791. A form without a hyphen, such as `TNFα`, is one token and is not matched, which undercounts. Plural (`MSCs`) and possessive (`BMI's`) forms match. The not-primary test uses the same `words()` normalisation on the primary and on the chunk.
* **Yield.** A Monte Carlo of the stop rules reproduces the table (p = 0.315: 403 papers, ≈3,720 calls). Two caveats:
  * 115 of the 800 usable papers have only one C chunk, so they can draft C at most once. Capping C jobs at the C-chunk count raises the cost to 430 papers and 3,919 calls at p = 0.315, and to 291 papers and 2,495 calls at p = 0.386.
  * The paper count is set at the expected value, so reaching 100 usable papers has about a 50% chance.
