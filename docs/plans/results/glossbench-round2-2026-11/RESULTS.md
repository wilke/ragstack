<!-- experiment: Glossary-model benchmark, round 2 | 2026-10-30 | COMPLETE — GATE-NOT-EVALUABLE -->
# Round 2 results: glossary-model benchmark, pre-registered

**Status: COMPLETE — retrieval legs GATE-NOT-EVALUABLE (reach gate unattainable); no confirmatory
hypothesis tested.** The round stopped at the pre-registered reach gate (prereg §3.1 step 4) on every set,
by owner decision on 2026-10-30 ([DEVIATIONS.md](DEVIATIONS.md) D-5). Nothing below compares glossary
conditions on retrieval, wording or precision. What is reported is **descriptive**: the counts the
arm-blind pool build produced, and the outcome of the one model batch that ran. Every number in this file
is in [`artifacts/reach-r2.json`](artifacts/reach-r2.json) or in `DEVIATIONS.md` D-4, and the sentence
says which.

## Provenance

| | |
|---|---|
| pre-registration | [prereg/PREREG-glossbench-round2.md](prereg/PREREG-glossbench-round2.md), sha256 `39c579e1bc3aa7d80bffc6a93ef36157ce4c41c28ac3f37c939b6a5953cd4778`, frozen 2026-10-30T02:19:33Z on the owner's go (commit 9e109c3, merge dedc990); never edited |
| deviations | [DEVIATIONS.md](DEVIATIONS.md): **D-1** pilot 100 records over 5 papers (before any run); **D-2** code pin `0a7a7f3` → `d87a02e` for post-freeze tooling (#870), two pins across steps; **D-3** set P's `in_set` from the partner's pairs file, R1 exclusion mapped by PMC id (7 / 7 titles agree, 138 / 155 quotes verbatim); **D-4** frontier batch on set O only, truncated at `max_tokens`, stopped A and P; **D-5** gate settled by an upper bound; round stopped |
| code pins | pool builds, chunk exports, arm files: **`0a7a7f3`** (merge of #869), clean detached worktree, `dirty false, untracked 0`, `citable true`, not in a tools image (`in_image false`, warning kept). Frontier batch: **`d87a02e`** (merge of #870), clean. Prereg §9 asked for one commit; see D-2 |
| artifact | [`artifacts/reach-r2.json`](artifacts/reach-r2.json), a counts-only copy of the three private pool manifests made by [`artifacts/make_reach_r2.py`](artifacts/make_reach_r2.py); `artifact_provenance.citable` **true** (it inherits the manifests' verdict: clean tree, pinned commit, mechanical copy — no analysis), with the honest qualifier that the run was from a git checkout, not from a versioned `ragstack-tools` image |
| reading | **`dry`** on every set — the pool was built without the `frontierp` file (prereg §3.1 step 4). The `final` reading was never taken; §*The reach gate* shows it could not have changed the verdict |
| token counts | the gateway's own (D-4); no SFR token count appears in this file |
| what the partner saw | nothing of round 2 reached the papers-OC owner except the gateway traffic of D-4, which it audited (D-4) |

## Contents

- [The answer](#the-answer)
- [The reach gate, per set](#the-reach-gate-per-set)
- [Per-arm reach](#per-arm-reach)
- [The frontier batch](#the-frontier-batch)
- [What is not reported](#what-is-not-reported)
- [What this says](#what-this-says)
- [Threats to the descriptive reading](#threats-to-the-descriptive-reading)
- [Closure](#closure)
- [Reproducing](#reproducing)

## The answer

Round 2 asked whether the model that writes the symbol glossary changes what retrieval finds (H1–H3) and
whether its definitions carry the binding words (H4), on records from 25 papers per set with no rules
fallback (prereg §1–2). **It did not get to ask.** The confirmatory population S1 — value-statement chunks
that at least one candidate glossary reaches — had to hold ≥ 150 chunks per set before any question was
generated. The 25 eval papers of each set hold **144** (OA-1000), **119** (ASM-1000) and **72**
(papers-OC) value-statement chunks in total, so S1 is below 150 on every set **under any arm**, the
frontier included. Every set's retrieval leg is **GATE-NOT-EVALUABLE by arithmetic**, and the owner
stopped the round at the gate rather than run the secondary legs (D-5).

The gate was unattainable **by construction of the sample**: the frozen design fixed `n_papers` 25 and a
gate of 150 without counting the value-statement chunks 25 papers hold. That count needs no glossary and
no model call, and was not run before freezing. It is a design error in the frozen text, recorded as such.

The one model batch that ran — GPT-5.5 on the 26 set-O documents that reach the model — returned output
cut at `max_tokens` 4,096 on 30 of 58 calls. **Its glossaries are truncated outputs, not a measure of
GPT-5.5 glossary quality** (D-4).

## The reach gate, per set

Source: `artifacts/reach-r2.json`, `sets.<S>.counts` and `sets.<S>.gate`. Reading `dry` (reach arms
present: `rules`, `scoutp`, `qwen38p`, `qwen36p`; `frontierp` configured, absent). A *value-statement
chunk* is defined in prereg §3.1 step 1; S1 / S2 / S3 in step 3. `sampled` is what the seeded chunk
sample (step 5: 12 S1, 2 S2, 2 S3 per paper; 6 S3 on P) would have handed the drafter; on P the
arm-blind lexical prefilter of the field mode (prereg §3.4) further reduces it.

| set | corpus | papers / documents / chunks @512 | papers with ≥ 1 value-statement chunk | eligible (≥ 3) | eval papers | value-statement chunks, corpus | **value-statement chunks, eval papers** | S1 (dry) | S2 | S3 | sampled S1 / S2 / S3 | gate 150 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **O** | OA-1000 | 1,000 (`path_root`) / 12,560 units / 32,765 | 673 | 384 | 25 | 3,369 | **144** | **12** | 132 | 539 | 12 / 49 / 50 | GATE-NOT-EVALUABLE |
| **A** | ASM-1000 | 1,000 / 1,000 / 27,418 | 140 | **38** | 25 | 341 | **119** | **3** | 116 | 920 | 3 / 50 / 50 | GATE-NOT-EVALUABLE |
| **P** | papers-OC | 275 / 275 / 14,456 | 226 | 253 † (7 excluded, D-3) | 25 | 3,142 | **72** | **12** | 60 | 786 | 10 / 31 / 126 | GATE-NOT-EVALUABLE |

† On P the manifest's `eligible_papers` (253) exceeds `papers_with_vs` (226), so it is the field mode's
eligibility (the arm-blind lexical prefilter of prereg §3.4: papers with ≥ 3 chunks the prefilter admits
for a planned field, `min_field_chunks`), not the "≥ 3 value-statement chunks" rule that O and A report.
It is copied, not re-derived. For the same reason P's pool holds only the eval-paper chunks the prefilter
admits, so P's **72** is the value-statement chunks of the eval papers *that the prefilter admits*; the
prefilter is arm-blind and S1 is drawn from that pool, so the bound below holds on P as on O and A.

**The upper bound** (`sets.<S>.s1_upper_bound`). S1 ∪ S2 is exactly the value-statement chunks of the
eval papers' pool (12 + 132 = 144; 3 + 116 = 119; 12 + 60 = 72; on P, see †): the build assigns S1 or S2
only to a chunk with a value statement, S3 otherwise. An arm can only move a chunk from S2 to S1.
The `final` reading with `frontierp` is therefore bounded by 144, 119 and 72, all below 150:
`attainable_under_any_arm: false` on every set. No frontier file was needed to settle the gate.

**Manipulation checks** (prereg §8, `sets.<S>.manipulation`): chunk counts equal round 1's (32,765 /
27,418 / 14,456, `match true`); every arm file resolves every eval-paper document (`verified` 266 / 25 /
25, `unverified 0`, no `verified_ws`). Set P's field mode: binding export v2 (sha `1c377891…`), 26 fields,
`in_set` **13 / 13** as asserted, 65 records, 0 without the key, 0 outside the list; 4 constants, cap 2;
226 (chunk, field) jobs planned against a 500-record target (S1 realised 31 of 300, S2 47 of 50, S3 148
of 150 — the S1 shortfall is the gate from the other side).

## Per-arm reach

Source: `sets.<S>.reach`. Reach = the share of value-statement chunks on which the arm's file renders a
non-empty glossary field under the **served value-only attachment** (`for_chunk`; an entry attaches only
where its symbol occurs with a value). Pure conditions (`rederive --merge llm-only`, no rules fallback;
prereg §4). Shares to one decimal. Corpus-wide reach was computed for O and A (`--corpus-reach`); for P
it is **absent** (not computed on that build).

| arm | O eval papers (of 144) | O corpus (of 3,369) | A eval papers (of 119) | A corpus (of 341) | P eval papers (of 72) | P corpus |
|---|---|---|---|---|---|---|
| `rules` | 2 (1.4 %) | 104 (3.1 %) | 2 (1.7 %) | 3 (0.9 %) | 5 (6.9 %) | absent |
| `scoutp` | 7 (4.9 %) | 215 (6.4 %) | 2 (1.7 %) | 5 (1.5 %) | 8 (11.1 %) | absent |
| `qwen38p` | 12 (8.3 %) | 317 (9.4 %) | 3 (2.5 %) | 7 (2.1 %) | 12 (16.7 %) | absent |
| `qwen36p` | 6 (4.2 %) | 298 (8.8 %) | 2 (1.7 %) | 7 (2.1 %) | 11 (15.3 %) | absent |
| **any of the four** (= S1) | **12 (8.3 %)** | — | **3 (2.5 %)** | — | **12 (16.7 %)** | — |
| `frontierp` | absent | absent | absent | absent | absent | absent |

Arm files (`sets.<S>.arms`): `rules` is round 1's `rules/3` file; the three pure files carry
`llm/2:<model>:dcb924271b1e:w80000x4:t4096:k0:r0+merge=llm-only`. On OA-1000 the pure files have an LLM
half for 1,593–1,595 of 12,560 units (float units below 1,500 characters have none; prereg §4), so O's
reach is article-unit-dominated as the prereg states; `qwen36p` carries 2 degraded documents on O and
on A (round 1's cut-offs), 0 on P.

## The frontier batch

Source: `DEVIATIONS.md` D-4 (our step's summary and the gateway owner's audit; the audit is **external
and not re-measured here**). Not in `reach-r2.json`: no frontier count entered a pool.

| | |
|---|---|
| model, settings | GPT-5.5, pure, round 1's prompt and step settings as frozen (thinking **unset**, `max_tokens` 4,096, cut-off retry `split`, windows 80,000 × ≤ 4, seed 20261028, concurrency 4, `fallback_model ""`) |
| scope run | set **O** eval papers only: 266 units, 240 skipped below 1,500 characters, **26** documents to the model. Sets **A and P: not run** |
| window | 2026-10-30 03:13:23Z → 03:23:17Z (our step); 03:13:45Z → 03:22:50Z (audit) |
| calls | **58**, every outcome `ok` (audit); served `gpt-5.5-2026-04-24` on all 58, no substitution (audit) |
| tokens (gateway's count) | **330,236 in / 181,266 out** (audit) |
| output cap | **30 of 58 calls returned exactly 4,096 output tokens**; median 4,096, minimum 421 (audit). The cap reached the provider on every call |
| our step | 9 of 26 documents **degraded** (`cut_off`, unresolved after the split retry), 16 cut-off windows, `with_llm` 17, 26 kept entries, no visible reasoning content; round 1's Qwen3.6 proposed 634 entries on the same 26 documents with no cut-off |
| stop | the stop rule the coordinator set in the batch brief before the batch started (> 2 degraded documents on a set) fired at 9; no rerun, no merge, no `final` reading |
| budget | 0.33 M in / 0.18 M out / 58 calls against the pause at 5.0 M / 0.75 M / 4,000 (prereg §8) |

**Reading.** The GPT-5.5 glossaries are **truncated outputs**. They say nothing about GPT-5.5 glossary
quality, and no entry from them is in any arm file, index or artifact. Whether hidden reasoning occupies
part of the 4,096 is **unverified**: the provider reports `usage.completion_tokens_details.reasoning_tokens`
and our step did not record it.

## What is not reported

Prereg §2, §5–§7. *Absent* means never measured; nothing here is a null.

| item | status | why |
|---|---|---|
| **H1, H2, H3, H4** (family F, Holm over 4) | **GATE-NOT-EVALUABLE on O, on A and on O ∪ A** | S1 < 150 on every set by the upper bound; no record drafted, no retrieval run. H2 additionally has no `frontierp` arm (D-4) |
| secondary tables (`bm25`, `norerank`, per corpus, `qwen38p`, `scoutp`, `none`, `frontierp` − `rules`) | absent | no retrieval run |
| found@10 (M1), discordant counts, noise-floor repeat, glossary-boost probe, inactive-reranker rule | absent | no run; no swap was applied to any bench index (every index stayed at its round-1 census) |
| **wording proxy** (M2, H4's outcome) | **absent** | needs drafted records; none was drafted |
| **precision** (M3, §6: harm / strict / lenient per arm, three judges, owner's 40 blind items, κ) | **absent** | no gold sample built, no judge run; the frontier arm would have been truncated output |
| S3 (words-only stratum), on P the stratum of interest | absent | no record drafted |
| **set P** (drafting, partner's scripted checks, judged sample of 50) and its status label | **absent** | prereg §8: the papers-OC leg rests on R1 (one paper) only — and R1 was not re-run either |
| set-P pilot (D-1: 100 records, 5 papers) and the §3.5 promotion addendum | absent; no addendum appended | not drafted; the promotion condition was never evaluated, so P is not promoted and the family never grew |
| R1 replication (the partner's 155 on the bench copy) | absent | no run |
| layer-3 owner spot-check, validity CIs | absent | no accepted records |
| realised power table (`found10_analysis.power_table`) | absent | no realised layout; the planning aid `prereg/power_sim.json` stands as a design assumption |
| `frontierp` reach, `final` gate reading, the stated mixture file | absent | D-4 |

## What this says

Descriptive, from the counts above; no contrast was tested and none is implied.

1. **The served value-only attachment reaches a small share of value-statement chunks.** Corpus-wide,
   `qwen36p` renders a field on **8.8 %** of OA-1000's value-statement chunks (298 of 3,369) and
   **2.1 %** of ASM-1000's (7 of 341); `rules` on **3.1 %** and **0.9 %**. On the 25 eval papers the
   union of all four arms reaches **8.3 %** (O), **2.5 %** (A) and **16.7 %** (P). Nine in ten
   quantity-bearing chunks carry no glossary under any candidate, on every corpus. The attachment rule
   (an entry attaches only where its symbol occurs *with a value*) is the mechanism the partner's review
   named in round 1 (lab book, review point 3); these are the first counts of it per value-statement
   chunk.
2. **Round 1's papers-OC retrieval gain was measured on a question set concentrated on glossaried
   chunks.** Round 1 found `qwen36` over `rules` at 70 vs 52 of 155 under `base`, 22 gained / 4 lost
   (lab book Part 3; 138 of the 155 from one paper). A glossary can move a record only through a chunk
   that carries one. At the reach shares above — 15 % of value-statement chunks on papers-OC eval
   papers, 509 of 14,456 chunks of any kind under `qwen36` (lab book Part 1) — a 26-of-155 discordance
   is consistent only with a record set whose gold chunks are glossaried far more often than the
   corpus's. The partner's own proxy says the same from its side (the glossary names the asked
   quantity for 65–68 % of its pairs; prereg §3.4, external). **The per-record attachment rate of the R1
   set was not measured in this round (absent)**, so this is an inference from counts, stated as one.
   What round 1 showed is that the glossary model matters *where a glossary attaches*; round 2's counts
   say where that is: rarely.
3. **ASM-1000 cannot carry a value-statement design at this attachment.** 341 value-statement chunks in
   27,418; 38 of 1,000 articles have three or more. Round 1 saw it (3–7 glossaried chunks of 27,418) and
   the prereg cited that figure as the reason the gate was "real" — without turning it into the count of
   S1 that 25 papers could yield.
4. **The gate of 150 was set without the count that decides it.** Value-statement chunks per paper need
   no arm file; `glossbench_r2_pool.py build` prints them. Had it run before the freeze, the sample size,
   the gate or the population would have been set differently. That is the lesson the record carries
   forward, not a result about glossaries.
5. **A frontier model at the round-1 cap is not a usable arm without recording reasoning tokens.** 30 of
   58 calls at exactly 4,096 output tokens, with the hidden-reasoning share unrecorded, is a configuration
   failure to be fixed in the step (record `reasoning_tokens`, set the cap from a measured output length),
   not a property of the model to be reported.

## Threats to the descriptive reading

- **The value-statement definition is the code's** (`value_pattern` on the chunk's own tokens, 20-token
  statistical stoplist). A quantity printed in words, or as "X of 12", is not a value-statement chunk
  here; S3 was the stratum for those and was never drafted. The shares are shares of *symbol-bound*
  quantities only.
- **Reach is attachment, not extraction.** A glossary may define the symbol and still render nothing on a
  chunk where the symbol appears without a value. The counts measure the glossary as served, which is the
  estimand the prereg chose (§3.1), not the glossary as written.
- **`dry` reading.** `frontierp` is absent from the union; the upper bound makes this immaterial for the
  gate, and material for any statement about the frontier's reach (none is made).
- **O is unit-level.** OA-1000's paper is the article with its float units (`path_root`); 1,000 papers
  but 12,560 units, and the pure LLM files cover 1,593–1,595 units. Corpus-wide O reach counts float
  units' value-statement chunks that no LLM arm can reach by design (prereg §4), so the O corpus shares
  are lower bounds on article-body reach.
- **One build per set, seeded.** Different seeds would draw different 25 papers; the eligible-paper
  counts (384 of 1,000 on O, 38 of 1,000 on A; P's 253 is field-mode eligibility, see †) and the
  per-paper mean (5.8 / 4.8 / 2.9 value-statement chunks) say the gate fails for any draw of 25 — the maximum a draw could reach is bounded by the richest 25 papers, which
  was not computed (absent) and is not claimed to exceed 150 either.
- **Not from a tools image.** Every provenance block carries `in_image false`; the commit, clean tree and
  segmentation fingerprint are recorded instead.
- **The gateway audit is external.** Call, token and cap counts of the frontier batch are the gateway
  owner's; our step recorded the served id per call and 58 calls, and recorded no token counts.

## Closure

Round 2 is closed as GATE-NOT-EVALUABLE; it is not re-opened, re-sampled or re-gated (a changed gate
after seeing the counts would be a forking path). Any later round is its own pre-registration in its own
directory; nothing of it is written here. What follows this round is lab-book matter
([`../glossbench-2026-10/LABBOOK.md`](../glossbench-2026-10/LABBOOK.md), addendum 2026-10-30).

## Reproducing

- The pool build, from a clean checkout at `0a7a7f3` with a private config naming the chunk exports and
  the arm files: `PYTHONPATH=python python/scripts/eval/glossbench_r2_pool.py --private-config CFG build
  --set {O,A,P} --corpus-reach` (P in `fields` mode, `--in-set-records` the pairs file of D-3). The
  manifests are the private outputs; their sha256s are in `artifacts/reach-r2.json`
  (`artifact_provenance.source_sha256`).
- The public artifact, from the manifests: `python3 -I artifacts/make_reach_r2.py --manifests DIR --out
  artifacts/reach-r2.json` — byte-deterministic.
- The arm files: `python/scripts/glossary_file.py rederive --merge llm-only` on round 1's files (prereg
  §4); their sha256s are in the artifact.
- The frontier batch command, settings and its stop are in `DEVIATIONS.md` D-4; the raw output is private
  (sha256 `839ff301…`) and is not an input to anything.
