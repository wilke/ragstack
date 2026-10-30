<!-- experiment: Glossary attachment benchmark, round 3 | 2026-10-30 | COMPLETE — H1 not confirmed; H2, H3 GATE-NOT-EVALUABLE; set P stopped at its pilot -->
# Round 3 results: glossary attachment benchmark, pre-registered

**Status: COMPLETE.** Family F (H1–H3) was tested on **set O (OA-1000)** as pre-registered: **H1 not
confirmed**, **H2 and H3 GATE-NOT-EVALUABLE**. Set A (ASM-1000) ran as the descriptive leg; its records were
drafted after an upstream change in the drafter model ([DEVIATIONS.md](DEVIATIONS.md) D-6). **Set P
(papers-OC) was stopped at its pilot** (D-1): it was not promoted, and no non-pilot P record was drafted.
The labels *not confirmed* and *GATE-NOT-EVALUABLE* are the harness's own (`family.hypotheses.<H>.status`).
Every number in this file is in [`artifacts/results-r3.json`](artifacts/results-r3.json) or in
`DEVIATIONS.md`, unless the sentence names another source. Two other sources appear: the private
operations log, for checks the harness does not write, and the papers-OC owner's gateway audit, which is
external.

## Provenance

| | |
|---|---|
| pre-registration | [prereg/PREREG-glossbench-round3.md](prereg/PREREG-glossbench-round3.md), sha256 `43637555d234922323655ca6f51216ca08c39596422f53d2d5437419f10d3765`, frozen 2026-10-30T15:34:08Z on the owner's go (frozen text a27b581, freeze merge 6e620c2, #875); never edited. The drafter checked the sha at every stage and resume (`prereg_sha256` in every drafting total) |
| deviations | [DEVIATIONS.md](DEVIATIONS.md): **D-1** set P stopped at its pilot (7 of 100 jobs passed layer 1); not promoted. **D-2** drafting-pipeline changes after the freeze (#878: content-eligible field assignment in fields mode, yield estimate and gate, yield watch, Codex fallback on a usage-policy refusal). **D-3** O and A proceed under the yield watch. **D-4** the pilot's verification rerun, the partner's format check, and layer-1 `value_is_bound` (#880). **D-5** set O's 53 discriminating records judged in full by the papers-OC owner; per-hypothesis clean-subset sensitivities declared before any retrieval. **D-6** the drafter model's upstream behaviour changed between sets O and A; A resumed with `max_tokens` 16,384 |
| code pins, in order | **`6e620c2`** (freeze merge): configs, chunk dumps, `rederive --attach mention` arm files, pool builds for O / A / P, the set-P pilot's drafting, layer 1 and first verification (paused on a refusal, D-1). **`b501ec7`** (#878): the pilot's verification rerun in a new output directory and the partner export (D-4). **`1627f9d`** (#880): the layer-1 and export changes of D-4; no run used this commit directly. **`4c0bbb8`** (= `1627f9d` plus docs-only #879): the O and A pool rebuilds (rows byte-identical to the `6e620c2` builds; only the header's yield block differs), set O's drafting, layer 1, verification and summary, and both segments of set A. **`6b434e8`** (= `4c0bbb8` plus `DEVIATIONS.md` only): the wording proxy, every retrieval leg, and both analyses. Each was a clean detached worktree (`dirty false`, `untracked 0`). None ran from a tools image (`in_image false`, warning kept) |
| harness | `found10.py run` per arm × set, `found10.py analyse` with `analysis.round: 3` (`found10_analysis.analyse_r3`), `wording_proxy.py`, `glossbench_r2_draft.py` (draft, check, verify, summary), `glossbench_r3_pool.py build` |
| artifact | [`artifacts/results-r3.json`](artifacts/results-r3.json), a counts-only copy made by [`artifacts/make_results_r3.py`](artifacts/make_results_r3.py) from the harness outputs. Source sha256s are inside. `artifact_provenance.citable` is **true**: every retrieval, proxy and analysis output records `citable true` at `6b434e8`. The builder recomputes no test |
| glossary | fixed: round 2's pure Qwen3.6 files (`llm/2:Qwen3.6-35B-A3B:dcb924271b1e:w80000x4:t4096:k0:r0+merge=llm-only`) and round 1's `rules/3`, each under `value` (served) and `mention` attachment (`+attach=mention`); `mention-shuf` permutes `mention`'s rendered fields with seed `261103|shuf` (fixed points 0) |
| retrieval | bench copies only: OA-1000 32,765 chunks @512, ASM-1000 27,418; papers-OC 14,456, never swapped. Conditions `base` (hybrid + rerank), `bm25`, `norerank`; top 50 requested; found@10. 2026-10-30T22:33:47Z–22:51:51Z |
| drafter / verifier | Gemini 2.5 Pro through the papers-OC owner's gateway (`max_tokens` 4,096, raised to 16,384 for set A's second segment, D-6); Claude Opus 5.5 through the CLI verifier, off-gateway, with the Codex CLI as the fallback on a usage-policy refusal (D-2 item 4) |
| what the partner saw | the gateway traffic, which it audited; the pilot's 5 records as a format check (D-4); set O's 53 discriminating records, judged as a census (D-5). It saw no arm outcome |

## Contents

- [The answer](#the-answer)
- [Confirmatory table (set O, `base`, Holm over 3)](#confirmatory-table-set-o-base-holm-over-3)
- [Sensitivities (EXPLORATORY, D-5)](#sensitivities-exploratory-d-5)
- [Secondary results (unadjusted, outside F)](#secondary-results-unadjusted-outside-f)
- [Set A (descriptive; D-6 caveat)](#set-a-descriptive-d-6-caveat)
- [Set P: the pilot (D-1)](#set-p-the-pilot-d-1)
- [Manipulation checks](#manipulation-checks)
- [The proxy, and the distractor load](#the-proxy-and-the-distractor-load)
- [Drafting: yields and costs](#drafting-yields-and-costs)
- [What this shows](#what-this-shows)
- [What is not reported](#what-is-not-reported)
- [Limitations](#limitations)
- [Reproducing](#reproducing)

## The answer

Round 3 held the glossary fixed and changed only where its entries attach. Under `value`, the served rule,
an entry attaches where its symbol states a value. Under `mention`, it attaches on every chunk that
mentions the symbol. The question was whether `mention` finds more records on the chunks the served rule
cannot reach (stratum **M_num**: a symbol and a number, but no `symbol = value`), without losing on the
chunks it can (stratum **V**) (prereg §1–2).

| | H1: `mention` > `value` on M_num (one-sided) | H2: `mention` ≠ `value` on V (two-sided) | H3: `mention` > `mention-shuf` on V ∪ M_num (one-sided) |
|---|---|---|---|
| **label** | **not confirmed** | **GATE-NOT-EVALUABLE** | **GATE-NOT-EVALUABLE** |
| found@10 | 134 vs **142** of 149 | 51 vs **56** of 57 | 185 vs 186 of 206 |
| gained / lost | 1 / 9 | 0 / 5 | 1 / 2 |

Under `mention`, the gold chunk carried the asked quantity's words in its glossary field for more records
than under `value` (the proxy: 12 against 7 of 206). Retrieval did not follow. On M_num, `mention` found 8
fewer records than `value`, and the hypothesised gain was not confirmed. The test is one-sided, so the
adverse direction is descriptive. The design had power **.90** at the planned effect (d .17, gain share
.85, α/3, on the realised layout), so for the hypothesised gain this is a **powered null**. On V and in H3
the discordant records sit in too few papers for any split to reach α (5 and 3 papers).

## Confirmatory table (set O, `base`, Holm over 3)

Source: `confirmatory.family.hypotheses.<H>`, `confirmatory.power_realised.members.<H>`,
`confirmatory.census_breakdown_beside.<H>`. Test: paper-clustered sign-flip, exact enumeration (≤ 20
non-zero papers), seed 261103; 90 % paper-bootstrap percentile CI of the net share (10,000 resamples);
Holm over the family with gated members at p = 1.0. The census breakdown is the papers-OC owner's
judgement of the **discriminating records** in the hypothesis's stratum: those whose gold chunk renders
differently under the two attachment rules (D-5). Only those records were judged.

| | n (papers; non-zero) | statistic: net share [90 % CI] | gained / lost | p | Holm | min attainable p | realised power, d .17 / s .85 (d .10 / s .85) | MDE split (α; Holm worst) | label | census of the discriminating records beside it |
|---|---|---|---|---|---|---|---|---|---|---|
| **H1** M_num, `mention` − `value`, one-sided | 149 (46; 9) | −0.054 [−0.090, −0.020] | 1 / 9 | 0.998 | 1.0 | 0.00195 | **.90** (.66) | 9 / 1; 9 / 1 | **not confirmed** | 36: clean 27, not a quantity 6, worded 2, wrong quantity 1 |
| **H2** V, `mention` − `value`, two-sided | 57 (30; 5) | −0.088 [−0.149, −0.034] | 0 / 5 | 0.0625 | 1.0 | 0.0625 | .25 (.06), two-sided | absent (gated) | **GATE-NOT-EVALUABLE**: min attainable p 0.0625 > α 0.05 (5 non-zero papers) | 17: clean 14, not a quantity 2, wrong quantity 1 |
| **H3** V ∪ M_num, `mention` − `mention-shuf`, one-sided | 206 (48; 3) | −0.005 [−0.019, +0.009] | 1 / 2 | 0.875 | 1.0 | 0.125 | .97 (.81) | absent (gated) | **GATE-NOT-EVALUABLE**: min attainable p 0.125 > α 0.05 (3 non-zero papers) | 53: clean 41, not a quantity 8, worded 2, wrong quantity 2 |

- **MDE split** is `found10_analysis.mde_split`: the gained / lost split of the observed discordant records
  that a one-sided exact McNemar test would have needed. For H1 it would have needed 9 gained and 1 lost,
  at α and at the Holm worst case α/3. The observed split was 1 gained and 9 lost.
- **Power** is `power_table_r3` on the realised layout, at α/3, one- or two-sided as the member is (2,000
  simulations, between-paper SD of the gain share .15). The planning table (prereg §7) expected about 241
  accepted M_num records and power .99 for H1. The realised n is **149**. For H2 the planning table
  expected about 186 V records and two-sided power .92; the realised n is **57** and the power **.25**. So
  H2 was underpowered as well as gated.
- **Missing calls:** none. `family_missing_as_not_found` equals `family` on every member, with 0 scored
  not found.
- The harness counted 0 failed calls on every leg, so no record was excluded (`excluded_missing` 0).

## Sensitivities (EXPLORATORY, D-5)

**EXPLORATORY.** These sensitivities were declared at 2026-10-30T21:58:27Z, before any retrieval on any
round-3 set. Each one recomputes a hypothesis with the census-flagged records of its own stratum removed.
A record is flagged if the papers-OC owner rejected it or noted it as not a quantity. They never replace
H1–H3. Source: `exploratory_d5_sensitivity`.

| | records (flagged removed) | net share [90 % CI] | gained / lost | p | Holm | min attainable p | power d .17 / s .85 | MDE split (α; Holm worst) | label |
|---|---|---|---|---|---|---|---|---|---|
| H1, M_num minus 9 flagged | 140 (44 papers; 9 non-zero) | −0.050 [−0.085, −0.018] | 1 / 8 | 0.998 | 1.0 | 0.00195 | .87 | 8 / 1; 9 / 0 | not confirmed (exploratory) |
| H2, V minus 3 flagged | 54 (29; 5) | −0.093 [−0.160, −0.036] | 0 / 5 | 0.0625 | 1.0 | 0.0625 | .21 | absent (gated) | GATE-NOT-EVALUABLE (exploratory) |
| H3, both minus 12 flagged | 194 (48; 2) | −0.010 [−0.024, 0.000] | 0 / 2 | 1.0 | 1.0 | 0.25 | .95 | absent (gated) | GATE-NOT-EVALUABLE (exploratory) |

On this exploratory reading, removing the flagged records changes no label and no direction. One of H1's
nine losses was on a flagged record.

## Secondary results (unadjusted, outside F)

Pre-registered as secondary (prereg §2). Each p below is unadjusted and none is a finding of the family.
Source: `secondary`.

**H1–H3 under the other two conditions (set O):**

| condition | H1 M_num gained / lost, net share, p | H2 V gained / lost, net share, p (two-sided) | H3 gained / lost, net share, p |
|---|---|---|---|
| `bm25` | 0 / 34, −0.228, p 1.0 | 1 / 12, −0.193, p 0.0098 | 3 / 11, −0.039, p 0.999 |
| `norerank` | 6 / 7, −0.007, p 0.73 | 2 / 5, −0.053, p 0.45 | 5 / 4, +0.005, p 0.50 |

**Other contrasts on set O, `base`, V ∪ M_num (n 206):**

| contrast | gained / lost | net share [90 % CI] | p | reading |
|---|---|---|---|---|
| pooled `mention` − `value`, two-sided | 1 / 14 | −0.063 [−0.095, −0.034] | **0.0017**, sign `mention` < `value` | the only pooled test of the attachment rule; unadjusted |
| `mention` − `none`, one-sided | 1 / 14 | −0.063 [−0.095, −0.034] | 0.9999 | — |
| `value` − `none`, one-sided | 0 / 0 | 0.000 | 1.0 (GATE-NOT-EVALUABLE, 0 non-zero papers) | **`value` and `none` found the same records** |
| `rules-mention` − `rules-value`, two-sided | 0 / 5 | −0.024 | 0.0625 (GATE-NOT-EVALUABLE, 5 papers) | the rules glossary moves the same way |
| `mention` − `rules-mention`, two-sided | 1 / 9 | −0.039 | 0.021 | — |
| `value` − `rules-value`, two-sided | 0 / 0 | 0.000 | 1.0 (GATE-NOT-EVALUABLE) | — |

**M_num split by the drafter's `symbol_is_the_quantity`** (`secondary.m_num_split`): on the *incidental*
records (n 128), 1 gained and 8 lost, net −0.055, p 0.996. On the *symbol-bound* records (n 21), 0 gained
and 1 lost, GATE-NOT-EVALUABLE (1 non-zero paper). No record was unrecorded.

**found@10 per arm, set O** (`descriptives.found_at_10.O`; the `value#2` repeat equals `value` on every cell):

| stratum, condition | `value` | `mention` | `mention-shuf` | `none` | `rules-value` | `rules-mention` |
|---|---|---|---|---|---|---|
| M_num (149), `base` | 142 | 134 | 134 | 142 | 142 | 139 |
| M_num, `bm25` | 137 | 103 | 109 | 139 | 138 | 126 |
| M_num, `norerank` | 122 | 121 | 120 | 122 | 124 | 123 |
| V (57), `base` | 56 | 51 | 52 | 56 | 56 | 54 |
| V, `bm25` | 51 | 40 | 42 | 51 | 51 | 46 |
| V, `norerank` | 50 | 47 | 47 | 49 | 49 | 49 |

## Set A (descriptive; D-6 caveat)

**Descriptive only** (prereg §3). No hypothesis depends on set A, and its figures are secondary and
unadjusted. **D-6 caveat:** set O was drafted entirely before the drafter model's upstream behaviour
changed, and set A was drafted entirely after it. Differences in yield or record character between O and A
therefore cannot be attributed to the corpora alone. Retrieval on A ran with the same code and server as on
O. The served rule renders a field on **0** of A's gold chunks in either stratum, so the A contrast reads
*a glossary on the gold chunk against none* (prereg §3).

| | n | `value` | `mention` | `mention-shuf` | `none` | `rules-value` | `rules-mention` |
|---|---|---|---|---|---|---|---|
| M_num, `base` | 57 | 48 | 42 | 42 | 48 | 48 | 45 |
| M_num, `bm25` | 57 | 46 | 35 | 36 | 46 | 46 | 42 |
| M_num, `norerank` | 57 | 40 | 32 | 35 | 40 | 40 | 38 |
| V, every condition | 6 | 6 | 6 (5 under `bm25`) | 6 (5 under `bm25`) | 6 | 6 | 6 |

H1–H3 on A, `base` (`secondary.other_sets.A.base`): H1 0 gained / 6 lost, net −0.105, p 1.0 (one-sided;
min attainable p 0.031). H2 0 / 0 and H3 0 / 0, both GATE-NOT-EVALUABLE (0 non-zero papers). Under `bm25`,
H1 is 0 / 11; under `norerank`, 0 / 8. Set A has 63 accepted records over 22 papers (V 6, M_num 57),
against a plan of 200 jobs.

## Set P: the pilot (D-1)

P's recorded result is its pilot. That was 100 jobs over 5 papers in fields mode at `6e620c2`, with 105
drafter calls (94,614 prompt / 2,185 completion tokens, by the gateway's count). **88 of the 100 first
attempts answered "no such quantity"**, and **7 jobs passed layer 1**. At most 7 records could have been
judged, fewer than the 30 that §3.5 requires, so **P is not promoted** by the frozen rule. F stays at three
members with Holm over 3. In the verification rerun at `b501ec7`, 5 of the 7 passed: 4 by the Claude CLI
and 1 by the Codex fallback after a usage-policy refusal. 2 were rejected for single reading. The 5 went to
the papers-OC owner as a format check of the adapter only (D-4). They are data for no hypothesis and are
never pooled. The cause was field assignment by quota, not by content: in 41 of the 88 "no such quantity"
jobs, the chunk names none of the assigned field's terms (D-1). #878 changed the assignment rule (D-2).
**No round-3 P sample was drawn**, and no P retrieval leg ran. The R1 replication (the papers-OC owner's
155-record set) is **absent**.

## Manipulation checks

Source: `runs.<set>/<arm>`, `restore`, `descriptives.noise_floor`. Where a row says so, the source is the
private operations log, and the figure is not in the artifact.

| check | result |
|---|---|
| census of each leg | every one of the 14 legs (7 arms × O, A) found its arm's stamp on **every** chunk before and after the run (32,765 / 32,765 on O, 27,418 / 27,418 on A), source `swap_record+live`. papers-OC was never swapped: its census was `rules/3@file/1` on 14,456 chunks before and after, and its after-dump is byte-identical to the before-dump (ops log) |
| depth served | every condition of every leg served **50 of 50** on every record (`depth_short` 0, `depth_over` 0) |
| BM25 exhaustion verifier | **never fired**: no list was short, so `exhausted_verified` was 0 and the verdict set was empty on every cell |
| gates | no arm × condition was gated (`gated_arms` empty). `base` was never identical to `norerank` (0 of 206 on O, 0 of 63 on A), so the inactive-reranker rule did not fire. Failed calls were 0 on every leg (618 calls per O leg, 189 per A leg) |
| glossary-boost probe | **active** on A × `mention` and A × `mention-shuf`: 18 qualifying chunks, 4 of 4 probes passed. **declared** (`--boost-declared 1.0`) on every other cell, where 0 or 2 chunks qualified, below the minimum of 3. This matches the pre-declared cells of prereg §5 note 2 exactly. No short list occurred, so the content-only `_count` comparison of note 2(d) is **absent** |
| admin `/v1/config` | identical at **all 32 reads** (start and end of 14 legs, one refused attempt, the pre-check and the end check), full-config sha256 prefix `f85cf2e2f73c36ab`, the same as the reranker test's (ops log) |
| noise floor (`value` repeated as `value#2`) | found@10 discordant **0** on `base`, `bm25` and `norerank`, on both sets (0 of 206, 0 of 63). Rankings differed below the found@10 line on 8 / 20 / 29 O records and on 3 / 0 / 1 A records (ops log) |
| restore | O and A were restored to `rules/3` on every chunk (`restore.<set>.census_after`; 32,765 and 27,418 written, 0 reassembly gaps). The after-dumps differ from the pre-round dumps only in row order: diff count **0** on chunk id, glossary field, glossary version and neighbours (ops log) |
| one refused leg attempt | the first O × `value#2` run was refused before any call: the swap record named arm `value#2` and the runner requires `value`. Nothing was measured. The repeat ran after an idempotent re-swap recorded under `value` (ops log) |
| arm files | `mention` ⊇ `value` on every record: the proxy counted **0** records where `value` passes and `mention` fails, on O and on A, and 0 for `rules-mention` against `rules-value`. The shuffled arm has 0 fixed points |

## The proxy, and the distractor load

**The wording proxy (M2) is a manipulation check, not a hypothesis** (prereg §5). It ran at 22:30Z on O
and 22:31Z on A, before the first swap. A record passes if some entry on its gold chunk's rendered field
has a definition whose words contain one of the record's binding terms. Source:
`proxy_manipulation_check.<set>`.

| set O (n 206; V 57, M_num 149) | `value` | `mention` | `mention-shuf` | `none` | `rules-value` | `rules-mention` |
|---|---|---|---|---|---|---|
| records whose gold chunk carries any field | 12 (V 12, M_num 0) | 63 (V 27, M_num 36) | 63 | 0 | 4 | 36 |
| proxy pass | 7 (V 7, M_num 0) | **12** (V 10, M_num 2) | 0 (chance level) | 0 | 1 | 4 |

On set A, `mention` puts a field on 14 of 63 gold chunks (V 3, M_num 11) and `value` on 0. The proxy pass
is **0** under every arm.

**Distractor load** (`proxy_manipulation_check.<set>.arms.<arm>.chunks_with_field`): the share of all chunks
in the index that carry a glossary field.

| arm | OA-1000 (of 32,765) | ASM-1000 (of 27,418) |
|---|---|---|
| `value` (served) | 300 (0.9 %) | 7 (0.03 %) |
| `mention` | **5,918 (18.1 %)** | **6,246 (22.8 %)** |
| `rules-value` | 104 (0.3 %) | 3 (0.01 %) |
| `rules-mention` | 1,720 (5.2 %) | 850 (3.1 %) |

So `mention` raised the number of O records whose gold chunk carries the right definition from 7 to 12 of
206. It did this by putting a field on about 20 times as many O chunks, and about 900 times as many A chunks.

## Drafting: yields and costs

Gateway counts are the drafter's totals (`drafting.<set>`), built from the usage the gateway returns
with each call. No gateway cost in USD was recorded (`cost_usd` null). Verifier costs are the CLI's own.

| batch | pin | gateway calls | prompt / completion tokens | jobs | layer-1 passes | layer 2 (Claude pass / reject; Codex pass) | accepted |
|---|---|---|---|---|---|---|---|
| P pilot | `6e620c2` (verify rerun `b501ec7`) | 105 | 94,614 / 2,185 | 100 | 7 | 4 / 2; 1 | 5 (format check only) |
| O | `4c0bbb8` | 863 (859 answered, 4 HTTP failures) | 687,111 / 113,145 | 534 | 254 | 205 / 48; 1 | **206** (V 57, M_num 149), 48 papers |
| A, segment 1 (`max_tokens` 4,096) | `4c0bbb8` | 170 (169 answered, 1 HTTP 502; 2 truncated) | 147,996 / 368,458 | 104 | 29 | — | — |
| A, segment 2 (`max_tokens` 16,384, D-6) | `4c0bbb8` | 156 | 135,499 / 356,582 | 96 | 48 | — | — |
| A, both segments | | 326 | 283,495 / 725,040 | 200 | 77 | 58 / 14; 5 | **63** (V 6, M_num 57), 22 papers |
| **ledger** | | **1,294** | **1,065,220 / 840,370** | | | | |

- **Ledger.** The ledger total is `cumulative_with_prior` of set A's second segment. The token diagnostic of
  D-6 adds 8 calls (6,790 / 13,645 tokens), which sit outside the ledger (ops log).
- **Caps.** Our pause was 2,500 calls / 2.5 M input / 0.5 M output. The output cap was raised to 1.0 M for A's
  second segment (D-6): output stood at 483,788 when set A paused, and 840,370 at the end. The papers-OC
  owner's user approved about 1.45 M input tokens; 1.07 M were used.
- **Over the size notice.** Set O used 863 calls against its notice of 695 (+24 %): it needed 1.61 attempts
  per job against the assumed 1.3 (D-5).
- **Attempts per accepted record**: O 4.17 (V 7.30, M_num 2.97); A 5.16 (V 15.8, M_num 4.04); P pilot 21.0.
- **Layer 1, the `value_is_bound` rule:** it rejected 28 of O's 859 drafts and 8 of A's 325. A hand check of
  10 of O's 28, drawn with seed `261103|vbcheck|O`, found 10 bounds as written. 4 of them are arguable (D-5).
- **Layer 2 (Claude CLI):** O used 254 CLI calls (1 usage-policy refusal, passed to Codex), 637,879 context /
  39,983 output tokens, **$6.14**. A used 77 CLI calls (5 refusals, all passed by Codex), $1.89. The P pilot
  used $0.15 in its rerun ($0.15 more in its first attempt, the 4 refused calls not tallied). No record was
  `verifier_unavailable`.
- **Gold differs among accepted records:** O **53** (V 17, M_num 36) of 206, against a design that planned
  about 150. A had 14 (V 3, M_num 11) of 63 (D-5; ops log for A).

**The upstream change (D-6).** Set A's per-call completion tokens jumped at its first call (median 2,070
against set O's 136). Set O's 859 answered calls each cost at most 304 completion tokens (ops log), so by
its own counts set O was drafted under the earlier behaviour. The papers-OC owner's gateway audit is
external and not re-measured here. It finds the change on **identical requests** (its request hashes
`549cf9ad…` and `ac22ff02…`), within **19:00:09Z–20:04:43Z**. Our probe's request hashes were computed over
a different byte scope, so they are not comparable with the audit's hashes.

## What this shows

In the record's own voice, from the figures above.

1. **The mention rule did what it was built to do to the input, and retrieval did not follow.** On set O it
   raised the number of records whose gold chunk carries the asked quantity's words in its glossary from 7
   to 12 of 206 (proxy, a manipulation check). On M_num, where the served rule attaches nothing to the gold
   chunk, the count went from 0 to 2. Over the same records, found@10 on M_num went from 142 to 134 of 149.
   **H1 is not confirmed.** The test is one-sided, so the adverse direction is descriptive: the net share's
   90 % CI is [−0.090, −0.020]. With power .90 at the planned effect, this is a **powered null for the
   hypothesised gain**. It is not a null for any effect smaller than the planned one: at d .10 / s .85 the
   power was .66.
2. **Where the loss sits is descriptive, from unadjusted secondaries.** The losses are largest under `bm25`
   (H1 0 / 34) and nearly absent under `norerank` (H1 6 / 7). `mention` puts a field on 18 % of OA-1000
   chunks instead of 0.9 %. That pattern is consistent with glossaried distractors winning BM25 matches,
   the distractor penalty that H2's framing named, but this round did not test that mechanism.
3. **H2 and H3 cannot be read.** On V, `mention` lost 5 records and gained none, but the 5 sit in 5 papers.
   No split could reach α, and the two-sided power was .25 on the realised 57 records. H3 (right entries
   against shuffled ones under `mention`) differs on 3 records in 3 papers. Neither is a null.
4. **On this evidence the served value-only rule stays.** The pooled secondary (`mention` − `value`,
   two-sided, unadjusted) is 1 gained to 14 lost, p 0.0017. The `rules` glossary moves the same way
   (`rules-mention` − `rules-value` 0 / 5, gated). One fact belongs beside that: under `base`, on these 206
   records, `value` found exactly the records that `none` found (0 discordant). Under `bm25` and
   `norerank` the totals differ by at most 2 records (table above; descriptive). This round shows `value`
   ahead of `mention`. It does not show `value` ahead of no glossary.
5. **The census changes nothing.** Removing the 12 records the papers-OC owner flagged (EXPLORATORY, D-5)
   leaves every label and direction as it was.

## What is not reported

*Absent* means never measured. Nothing here is a null.

| item | status | why |
|---|---|---|
| set P beyond the pilot (H1-P–H3-P, the judged sample of 50, the status label), and the §3.5 promotion addendum | **absent** | D-1: P stopped at its pilot and was not promoted. The outcome is recorded in D-1, not appended to `prereg/README.md` |
| R1 replication (the papers-OC owner's 155 on the bench copy) | **absent** | not run |
| layer-3 owner spot-check, and validity per set | **absent** | 40 items were exported per set (O and A) and their labels have not been imported. Set O's status label stays *our three-layer check (layers 1–2)* |
| the content-only `_count` beside a short `bm25` list (prereg §5 note 2(d)) | absent | no list was short |
| precision of entries | not measured by design | prereg §6: the glossary is unchanged from rounds 1–2 |
| set A's records under the earlier drafter behaviour | absent | D-6: A was drafted only after the change |

## Limitations

- **53 discriminating records.** The two arms differ on the gold chunk itself only on these 53 (V 17,
  M_num 36), against a design that planned about 150. On the rest, an arm can move a record only through
  distractors. H1's n of 149 counts every M_num record, as frozen.
- **8 of the 53 are not a quantity** in the papers-OC owner's census: an equation number, a supplementary
  reference, an accession, a definitional constant, or a number read out of a name. A further 4 were
  rejected (D-5). The sensitivities remove all 12 per stratum (exploratory). The layer-1 rules that would
  catch these classes are for later rounds.
- **Set A was drafted after the upstream change** (D-6), so O against A is not a corpus comparison. Set A
  is also small (63 records, 6 in V), and its V stratum cannot carry any contrast.
- **Set P was not run** beyond its pilot. Nothing here speaks for papers-OC, the corpus behind round 1's
  gain.
- **Spot-check validity is absent** until the owner's layer-3 labels are imported. Layers 1 and 2 are a
  machine check: an LLM drafter checked by an LLM verifier, with a Codex fallback on 7 records in the round.
- **One run, one corpus for the confirmatory family.** Clustering is by paper, and 46–48 papers contribute.
  Different seeds would draw different papers.
- **Not from a tools image.** Every provenance block records `in_image false`. Commit, clean tree and
  segmentation fingerprint are recorded instead.
- **External figures** are the papers-OC owner's: the census judgements, which we hold as counts and
  verdict files with private sha256s `f5183e80…` and `ea3cfa5c…`, and the gateway audit. Neither is
  re-measured here.

## Reproducing

- Retrieval, from a clean checkout at `6b434e8`, with a private config naming the bench indices, arm files
  and `bm25_scope`. For each set and arm, swap the arm onto the bench copy (`glossbench/swap.sh`), then run
  `PYTHONPATH=python python/scripts/eval/glossbench/found10.py run`, with `--boost-declared 1.0` except on
  A × `mention` and A × `mention-shuf`. Finally run `found10.py analyse` with `analysis.round: 3`, once on
  the accepted records and once on the D-5 clean subset.
- Drafting, at `4c0bbb8`: `glossbench_r2_draft.py --private-config CFG {preflight,draft,check,verify,summary}
  --set {O,A}`, with A's second segment in a new `--out-dir` under the D-6 config.
- The public artifact, from the private output root: `python3 -I artifacts/make_results_r3.py --root T3 --out
  artifacts/results-r3.json`. The output is byte-deterministic. Its inputs are listed by sha256 in
  `artifact_provenance.source_sha256`.
