<!-- Lab-book tier (dated status note), 2026-11-06. Not the record: every number below cites its source,
and where this note and the record (docs/plans/results/, registry/experiments.json) differ, the record wins.
Prepared for the CEPI Jam Session, 9-10 November 2026. Some results cited here (the OA chunking evaluation
main run, 4-5 Nov) exist so far only in the private ragstack-experiments repo and have no public record or
citable flag yet; treat them as provisional until they are registered. -->

# RAGStack chunking study and chunking experiments: briefing (as of 6 Nov 2026)

Sources read: public `wilke/ragstack` at `5d65bea` (v1.6.24, fetched today); private `ragstack-experiments` pulled today (`d8e5a2b`, 5 Nov, pull succeeded); registry `registry/experiments.json` schema 1.2, `updated: 2026-10-31`; the seven artifacts listed in Sources. Abbreviations used: CDS = TREC Clinical Decision Support corpus; ERET / EPACK = the study's reach and containment endpoints; NI = non-inferiority; OA = PMC Open Access; Scout / Qwen = the two local reader models (Llama-4-Scout, Qwen3.8-27B).

## Summary

The chunking work has two strands. (1) The **pre-registered TREC-CDS chunking study** (Phase 0 in early September, then Stage 0 calibration and confirmation run (a)) settled one axis, overlap, as a powered null on dense document-level metrics. It found that chunk size can only be read at matched realised size and matched delivered budget. Its confirmation labels have been quarantined since 9 to 14 Sep. It is still **blocked on the two-reader human read (HR-dev)**: nothing in either repository since the 10 Oct plan shows readers named, a rubric signed or a read started (`docs/plans/chunking-study-plan.md` status line; registry `confirmation-run-read-and-analysis` = blocked, `blocked_by` `hr-dev-two-reader-human-read` = planned). (2) A run of **applied, decision-oriented trials since 22 Oct**:
- section-aware trial (exploratory, no arm passed);
- deployed-docs parts 1–3 (exploratory, superseded);
- the **confirmatory part 4** (24–25 Oct, citable, PROVISIONAL): capped semantic 512 non-inferior to live; fixed 512 non-inferior to live and to 1024; 512 > 128 not confirmed. Its human grader validation is still waiting for two more graders as of the last record (29 Oct).
- **New since 31 Oct, the OA chunking evaluation main run** (frozen 5 Nov, run 4–5 Nov, n = 2,035 questions, six arms). Its pre-registered rule in the production setting (hybrid + reranker) **keeps `fixed_token` 512/64**. `sent512` is the only arm shown non-inferior. Overlap 0 failed non-inferiority (−2.06 points hit@5, CI [−3.49, −0.59]). Semantic arms help vector-only retrieval by about +8 points but hurt BM25. These results are only in the private repo, and the registry still lists the run as "planned".

The **alias study's 5 Nov chunk-boundary control** (exploratory) found that boundary-respecting chunks lost answers mostly to an overlapping neighbour chunk (24 of 28 agreed-route losses). The October release made `fixed_token` 512/64 the default "for now", capped semantic chunks at their size, enforced token budgets everywhere and seeded Punkt with biomedical abbreviations. The evidence now supports keeping 512/64, but it does not support the record's "cut overlap" conclusion under the served path.

## 1. The arc

### 1.0 Before the study: benchmarks (June 2026)
- **7-way known-item benchmark** (`chunking-compare-7way-2026-06-30`): 300 title queries. `fixed_tok256` was the narrow "quality winner" (reranked recall@5 0.917, hybrid MRR@10 0.901) and no config separated from `fixed_tok512`. The `fixed_token` default (#677) rests on this result (registry headline and `related`; `docs/CHUNKING.md` § *What the measurements say*). Citable: unknown.
- **SciFact** (`scifact-chunk-eval-2026-06-30`): no config differs from `fixed_tok512` after Holm. `fixed_tok256` scored nDCG@10 0.7214 vs 0.6984 (+0.023 [0.006, 0.040], Holm p 0.077). Abstracts are too short to test large chunks. Citable: unknown.
- The plan later classes both as superseded as evidence, because known-item queries are insensitive to chunking (registry gaps; `chunking-study-plan.md` §3 row "pre").

### 1.1 Phase 0 (4–5 Sep 2026, TREC CDS, n = 10 topics)
All Phase-0 records have citable = **unknown**: the harness ran from an uncommitted directory, pinned `d225cea` (registry reason field).
- **Step 3 dense contrast** (`phase0-step3-dense-contrast-2026-09-04`): `tok2048 − tok512` nDCG@10 +0.137, CI [+0.051, +0.225], 8/10 topics. This overturned the BM25 lead-ablation proxy (step 2, now superseded).
- **Stage 1 Leg A grid** (24 configs): overlap 12.5 % − 0 % on nDCG@10 = −0.0210, CI [−0.047, +0.007], δ80 0.046 (powered null). corr(log2 realised chunk tokens, nDCG@10) +0.811. Top config `sentence_tok2048` 0.6289. The run is PROVISIONAL and prunes nothing (registry).
- **Leg B grid**: overlap −0.0040, CI [−0.0098, +0.0015], δ80 0.0081 (powered null).
- **Size is contested**: the 512→1024 step resolves with opposite signs on the two legs (Leg A +0.1204, Leg B −0.0182), and no size contrast resolves behind the reranker (`chunking-study-plan.md` §2 SG1, citing `stage1-legB/RESULTS-stage1-legB.md` §7.2, §8.1).
- **Small-corpus re-score**: the gap between "found the paper" and "found the answering passage" at k = 1 is +0.28 to +0.45 (`phase0-rescore-small-corpora-2026-09-05`). Breadth × k: the primary interaction is not supported (`phase0-breadth-k-2026-09-05`).

### 1.2 Stage 0 calibration and confirmation run (a) (5–14 Sep)
- **Stage 0 rev 2**: GATE-NOT-EVALUABLE. Judge span self-consistency was 0.3226 against a 0.90 gate (`stage0-calibration-2026-09-05`).
- **Relabels r3, r3.1, r3.1ext**: span gates still fail; Scout/Qwen span self-consistency is 0.3831 / 0.5617 (`stage0b-relabel-r31-2026-09-06`). Graded support reaches Spearman–Brown reliability 0.9205 at 30 readings (`stage0b-relabel-r31ext-2026-09-07`, exploratory). This is the evidence base of Paper A, *Reliable but Not Canonical* (artifact dated 11 Oct).
- **Stage 0b′** (`stage0b-prime-calibration-2026-09-08`): "gate fails on power". Joint power at n = 80 is N1 0.560, N3 0.369, R1 0.513; R2 `header512` 0.948 is the only powered contrast. The pointed population sits at its ceiling (`chunking-study-plan.md` §2).
- **Pointed-at-scale** (`stage0-pointed-at-scale-2026-09-08`): reach falls 0.031–0.046 per decade of corpus size. This is a projection, not a measurement.
- **Confirmation run (a)** (`confirmation-run-a-2026-09-08`): 80 CDS topics, 3,738 pairs, labelled by Scout ×20 and Qwen ×10, quarantined. Status pre-registered; citable false; no outcome computed by design.

### 1.3 The 10 Oct consolidated plan
`docs/plans/chunking-study-plan.md`, 10 Oct; mirrored in the *Chunking Study Plan* doc, dated 10 Oct, built on main `008f2cb`.
- The goal is the chunk spec for the ~498k-article OA bacteria ∪ viruses load. The served path is hybrid + `bge-reranker-v2-m3`, the budget B = 16,384 tokens, and the rule one-sided NI with ε = 0.05 at α = 0.025, conjunctive on ERET and EPACK.
- **D4(i), owner 10 Oct**: CDS confirms on ERET only; containment moves to a pointed population, which currently fails guard 1.
- Six sub-goals (SG1–SG6) and four enablers (E1–E4). Overlap (SG2) is "decided by the record", but the record notes the 512/64 default was "inherited unchanged" (contradiction 8).
- Owner decision 10 Oct: settle one default spec only after re-runs (contradiction 11).
- Fifteen open owner decisions, D1–D17 with D16/D17 added in the repo.
- The *Chunking Study Run Plan* artifact (4 Oct) proposes roles, gates G1–G7 and an 8-week strawman with the read in W2–W4 and freeze/unblind in W5–W6. None of those dates was met per the repo record.

### 1.4 Applied trials (22–28 Oct)
- **Section-aware trial** (`section-aware-trial-2026-10-22`, exploratory, citable false): no arm passed the rule (net ≥ +5, no loss at 5,120 tokens). Reranked found/155: live 66, J1 65, S0 60, JS 56, SS 59, S0m 62. JS − J1 = −9 (p 0.035).
- **Addendum D**, a size × overlap sweep: one contrast held, overlap at 128 tokens inside sections with the Qwen3.6 reader (+26 at 5,120 tokens) (`section-aware-addendum-D-size-overlap-2026-10-23`). The public record sits only on the unmerged branch `docs/section-aware-trial-261022` (registry gaps; `ragstack-experiments/section-aware-2026-10/README.md`).
- **Deployed docs, parts 1–3** (23 Oct, `clark-chunk-size-parts1-3-2026-10-23`): superseded and not citable. The grader was changed after results were seen, 66 contrasts went uncorrected, and one model family generated, answered and graded (`docs/plans/results/chunk-size-deployed-docs-2026-10-23/README.md`).
- **Addendum S** (citable false) found the live semantic chunker ignored `chunk_size`: max 3,398 tokens, 235 chunks over 512. This led to library fixes #802 and #805.
- **Part 4, confirmatory** (frozen 2026-10-25T03:41:39Z, run 25 Oct; commit `c35d1ce`, clean; citable **true**; PROVISIONAL). 336 documentation pages, 1,265 live chunks. Set M: n = 372 questions on 71 pages. B4096 budget. δ = 7.5 points, Holm over 4 tests, both readers required (`docs/plans/results/chunk-size-deployed-docs-part4-2026-10-25/RESULTS.md`; artifact `part4_summary.json`).
  - H2 SEM512 − live: confirmed. Net +15 / +30 (Scout / Qwen).
  - H4 T512o0 − live: confirmed, +4 / +12.
  - H3 T512 − T1024: confirmed, −10 / +5. Scout's lower bound is −6.4 against the −7.5 margin.
  - H1 T512 − T128: **not confirmed**, +19 (p .053) / +1.
  - On the Opus 4.8 grader subset (25 %), only H2 survives.
  - Secondary, unadjusted: at B2048, large chunks lose (H2 +33 / +44, H3 +37 / +44, H4 +32 / +41, all "both"). At k10, T128 loses by 29–33 net with a quarter of the text.
- **Addendum Q** (`clark-part4-addendum-Q-2026-10-26`, exploratory; artifact citable true): 105 of 372 questions (28.2 %) have at least one non-answer reference quote. On clean questions H3 becomes unresolved and H4 changes under the Sol classifier.
- **Human validation** (`clark-part4-human-validation-2026-10-27`, running). The first extra grader, FNKM (29 Oct), is descriptive only: P_A 22/40 = 0.55, κ_B 0.467, per-grader verdict "not validated". The grader set was fixed at 2026-10-29T17:54:39Z as FNKM plus the next two complete files (`LABBOOK.md` 2026-10-29 entry).
- **OA eval pilot** (27–28 Oct, `oa-chunking-eval-pilot-2026-10-27`, citable false): question-generation pilot on 60 passages. The owner's blind rating is still pending (`oa-chunking-eval-2026-10/README.md`; RESULTS caveats 5 Nov).

### 1.5 New since 31 Oct: OA chunking evaluation, main run (4–5 Nov)
Source: `ragstack-experiments/oa-chunking-eval-2026-10/main-run-2026-11/RESULTS.md`, `settings/a-hybrid-rerank/decision.json`, `reports/PLAN-AND-PASS-RULE.md` Addenda A/B and Amendment B.1. Commits `6f71226` (freeze), `6f85793` (B.1), `afb2da7` / `d8e5a2b` (results, 5 Nov).

**Design**
- Question: which chunk config should be the default for the OA bacteria/virus re-ingest.
- Six arms, each its own dev collection, all from the same 241,930 records (2,600 eval and 20,000 background articles): `ft512o64` (baseline), `ft512o0`, `sent512`, `ft1024o0`, `sempool512`, `sem512`. SFR-Embedding-Mistral 4096-d embeddings, the same reranker, server v1.6.22, tools image v1.6.20-b1.
- **No section-bounded arm.** Those arms are still "waiting on #828".
- Questions: Qwen3.8 wrote 2,437 gold-quote questions, selected programmatically. Decision set n = **2,035**; sensitivity set n = 2,335.
- Metric: hit@5 by ≥ 90 % character coverage of the answer quote. Paired bootstrap, 10,000 resamples.
- Pass rule: cheap arms must be NI at −2.0; mid arms need a lower bound ≥ +2.0; expensive arms ≥ +4.0; Holm across 5. A context-ratio rule (> 1.5× baseline text moves an arm up a class), a Bonferroni stratum guard and a paired-ladder tie-break. Margins came from a 300-question noise run (half-widths 1.15–1.70, below the 2.0 floor).

**Results in (a) hybrid + reranker (DECISION)**
| arm | hit@5 | diff@5 vs baseline [95 % CI] | context tokens@5 (chars/4) | verdict |
|---|---|---|---|---|
| ft512o64 | 79.0 | — | 2,316 | **kept** |
| ft512o0 | 77.0 | −2.06 [−3.49, −0.59] | 2,300 | fails NI |
| sent512 | 79.6 | +0.54 [−0.74, +1.87] | 2,182 | passes NI; ladder does not switch |
| ft1024o0 | 76.7 | −2.36 [−4.03, −0.79] | 4,601 (1.99×) | fails, moved to the expensive class |
| sempool512 | 80.3 | +1.28 [−0.20, +2.75] | 1,843 | fails the mid bar |
| sem512 | 79.2 | +0.15 [−1.38, +1.67] | 1,762 | fails the expensive bar |

**Secondary settings** (the rule's pick in each)
- (d) hybrid without reranker: `sent512`. `sent512` scored +2.36 [+0.74, +3.98] and `sempool512` +3.39 [+1.62, +5.16].
- (b) vector only: `sempool512`. `sem512` +8.21 and `sempool512` +7.76; `ft1024o0` −4.42.
- (c) BM25 only: the baseline. `ft1024o0` +3.10, `sem512` −6.34, `sempool512` −3.93.
- The sensitivity set gives the same pick in all four settings. In (a) the top four arms lie within about 1.3 points and no pair is separated.

**Caveats** (as recorded in RESULTS)
- One writer model, and the owner's rating of the questions is still pending.
- No LLM answer grading yet.
- Context tokens are chars/4 estimates.
- **Registry and public record lag:** the registry row `oa-chunking-eval-main-2026-11` still says `planned`, `citable: n/a`. No provenance or citable block was found in the settings JSON. The public branch `exp/oa-chunking-eval` stops at `d2a8b9b` (Amendment B.1, verified with `git ls-remote`), so no public results record exists. **Citable status: OPEN.** It should be treated as a pre-registered decision run that has not yet been registered.

## 2. Results table

"Citable" is the registry `citable.value`. "Kind" is the registry `kind`. C = confirmatory, E = exploratory, P = pilot, B = benchmark.

| experiment (registry id) | kind | question (short) | n | headline result | citable? |
|---|---|---|---|---|---|
| chunking-compare-7way-2026-06-30 | B | 7 configs, known-item queries | 300 queries | fixed_tok256 narrowly best (recall@5 0.917); no config separates from fixed_tok512 | unknown |
| scifact-chunk-eval-2026-06-30 | B | SciFact, 7 configs | 300 claims | nothing differs from fixed_tok512 after Holm; tok256 +0.023 nDCG, Holm p 0.077 | unknown |
| phase0-step3-dense-contrast-2026-09-04 | C | do sizes separate with real embedder/reranker | 10 topics | tok2048 − tok512 +0.137 [+0.051, +0.225] nDCG@10 | unknown |
| phase0-stage1-legA-grid-2026-09-04 | C | size × overlap grid, Leg A | 10 topics | overlap −0.0210 [−0.047, +0.007], powered null; provisional | unknown |
| phase0-stage1-legB-grid-2026-09-05 | C | replicate on Leg B | Leg B queries | overlap −0.0040 [−0.0098, +0.0015], powered null | unknown |
| phase0-rescore-small-corpora-2026-09-05 | C | paper-found vs passage-found | 1–5,000 docs | passage gap +0.28 to +0.45 at k = 1 | unknown |
| stage0-calibration-2026-09-05 | P | can the confirmation gates be evaluated | 10 dev topics | GATE-NOT-EVALUABLE; span self-consistency 0.3226 vs 0.90 | unknown |
| stage0b-relabel-r31ext-2026-09-07 | E | graded-support reliability | 30 readings | Spearman–Brown 0.9205 | unknown |
| stage0b-prime-calibration-2026-09-08 | E | power of the confirmatory contrasts | n = 80 projected | fails on power (N1 0.560, N3 0.369, R1 0.513; R2 0.948) | unknown |
| confirmation-run-a-2026-09-08 | C | frozen chunk-size contrasts on 80 CDS topics | 3,738 pairs | quarantined, no outcome | false |
| semantic-vs-pooled / salmonella-amr (09-18, 09-24) | E | do semantic and semantic_pooled agree | 791 spans | 2 of 791 shared spans; token cost 6.78× | unknown |
| section-aware-trial-2026-10-22 | E | section-aware vs ft512/64 on openchia | 155 records | no arm passed; JS − J1 −9 (p 0.035) | false |
| section-aware-addendum-D-size-overlap-2026-10-23 | E | size × overlap × sections | 16 arms | only overlap @128 in-section, Qwen3.6 reader, passed (+26) | false |
| clark-chunk-size-parts1-3-2026-10-23 | E (superseded) | size / overlap / section on deployed docs | 116 + 170 questions | superseded by part 4 | false |
| clark-addendum-S-semantic-cap-2026-10-23 | E | does live semantic ignore chunk_size | 1,265 chunks | yes: max 3,398 tokens, 235 over 512, which led to #802/#805 | false |
| clark-k10-answers-2026-10-23 | E (superseded) | top-10 answers | 286 questions | 66 contrasts, 22 unresolved | false |
| **clark-part4-confirmatory-2026-10-25** | **C** | rebuild with smaller capped chunks? | **372 questions / 71 pages** | H2 +15/+30 confirmed; H4 +4/+12 confirmed; H3 −10/+5 confirmed; H1 +19/+1 not confirmed | **true (PROVISIONAL)** |
| clark-part4-addendum-Q-2026-10-26 | E | are reference quotes answer-bearing | 372 / 729 pairs | 105 of 372 (28.2 %) have a non-answer quote; weakens H3 and H4 | true (artifact); kind exploratory |
| clark-part4-human-validation-2026-10-27 | C | do humans agree with grader Sol | 1 of 3 graders | running; FNKM descriptive "not validated" | false (until complete) |
| oa-chunking-eval-pilot-2026-10-27 | P | can writers generate gold-quote questions | 60 passages | no write-up; owner rating pending | false |
| **oa-chunking-eval-main-2026-11** (registry: "planned") | pre-registered decision run | default chunk config for the OA re-ingest | **2,035 questions, 6 arms** | (a) keep ft512o64; sent512 NI; ft512o0 −2.06 [−3.49, −0.59] | **OPEN** (registry not updated; no provenance block found) |
| alias-study-2026-11 chunk-boundary control (not in registry) | E | do labels change on boundary-respecting chunks | 300 pairs | flip 13.3 % vs re-judge null 6.7 %; agreed-answers 18.7 % vs 4.0 % | artifact citable true; addendum exploratory |

Rows from the registry that have no outcome yet:
- `hr-dev-two-reader-human-read`, `stage2-serving-path-concordance`, `hard-pointed-set-hps`, `micro-pointed-set-2026-10-10`, `information-density-idens-2026-10-12`, `new-method-arms-c-r0-r5`, `reemb-generator-tokenizer`, `leg-c-citances-grid`, `g2-second-implementation-eret-epack`: all `planned`.
- `confirmation-run-read-and-analysis`: `blocked`.
- `openchia-survey-pilot-chunking-v1-2026-10-02`: `pre-registered`, blocked on external credentials.

**Citable vs exploratory, sharply:**
- **Citable = true:** only `clark-part4-confirmatory-2026-10-25` (confirmatory, but PROVISIONAL until human validation) and the addendum-Q artifact (exploratory by kind).
- **Citable = unknown:** every Phase-0 and Stage-0 number, a provenance problem rather than a negative verdict. The plan still uses them as the study record, and Paper A builds on Stage-0 claims.
- **Citable = false:** all section-aware, parts 1–3, Addendum S, k10 and pilot results.
- **Not yet in the registry:** the OA main run. It is the newest and largest decision run, and its citable status is OPEN.

## 3. What the October chunking release shipped, and whether the study supports it

Shipped on `main` 23 Oct and deployed on **dev only** from 24 Oct: tools v1.6.16 / server v1.6.17, then up to server v1.6.22 on 3 Nov. The other tenants stay on v1.6.14 until the owner says "Roll fleet" (`STATUS.md` header).
- **Default `fixed_token` 512/64** for new collections, decided by the owner on 6 Oct "for now" (`docs/CHUNKING.md` § *The default*).
- **Token budgets** (#805): one factory, `resolve_chunk_sizing`, so every chunker respects `max_tokens` on every entry point. The API answers 503 when no window is known.
- **`chunk_size` honoured** (#802): `size_unit="tokens"` is opt-in and part of collection identity. A **semantic size cap** stops semantic chunks above *size* (previously up to 3,398 tokens). The CHUNKING.md cap section cites part 4 H2 and calls it "provisional until its grader validation is in".
- **Sentence splitting** (#804): Punkt seeded with ~130 biomedical abbreviations, `SEGMENTER_VERSION = "punkt-abbrev-v1"`.
- #815/#816: before v1.6.16 the whole fleet segmented with the regex fallback, because no image had `nltk`.
- **Migration owed (#811):** every sentence, semantic and semantic_pooled collection needs a full re-ingest.
- Opt-in size tolerance and min-piece merge (#831) came later.
- `structure_cuts sections/1` was opened by #828 PR 7 (merged 4 Nov, `c3cb2d1`). `docs/CHUNKING.md` still describes it as refused until PR 7 (minor doc lag).

**Is the default supported?**
- **512/64 kept: now supported by a pre-registered decision run** (OA main run, setting (a)), but only in the sense that no challenger cleared its bar. It is not shown to be best: `sempool512` and `sent512` have higher point estimates.
- **Overlap 64: tension with the record.** Phase 0 calls overlap a powered null and "settled (cut it)" (`chunking-study-plan.md` SG2), on dense, document-level nDCG. The OA run measured passage-span coverage on the served hybrid + reranker path, and there `ft512o0` **failed** non-inferiority (−2.06, lower bound −3.49) and also lost under BM25 (−2.21 [−3.64, −0.79]). The two measure different things, so this is not a contradiction of the Phase-0 numbers. It does mean the "drop overlap" decision is not supported for the production path. This is my reading; neither record states it.
- **Semantic cap 512:** supported by part 4 H2, which is confirmed but provisional. In the OA run, capped semantic arms are not worse in (a) and are much better in vector-only retrieval, but they are worse with BM25 and cost more to ingest.
- **Sentence splitting:** `sent512` was the only arm non-inferior in (a), better in (d) and (b), and "never meaningfully worse than the baseline in a setting that uses vectors" (OA RESULTS, analysis). No experiment isolates the Punkt abbreviation change itself.
- **512 vs 1024:** part 4 H3 (512 non-inferior to 1024) and the OA run (`ft1024o0` −2.36 at twice the context) agree that 1024 does not pay in the served path.
- **512 vs 128 / 256:** H1 is not confirmed. The June benchmarks lean nominally to 256. This is **unresolved**.
- **The 10 Oct owner rule** ("settle one spec only after re-runs and verification") still applies. Three default specs coexist: 512/64 token, 512/64 char, and 256/32 on the bulk/CWL path (contradiction 11). Nothing found since says they were unified. OPEN.

## 4. Status today (6 Nov 2026)

**Concluded**
- Phase 0 and Stage 0 (Sep).
- Section-aware trial and addenda (22–23 Oct).
- Part 4 model-graded analysis and addendum Q (25–27 Oct).
- OA eval pilot (28 Oct).
- **OA eval main run (5 Nov, new)**.
- Alias-study chunk-boundary control (5 Nov, new).

**Running**
- Part 4 human grader validation. It needs two more graders after FNKM. No further grader file is in `ragstack-experiments/clark-part4-human-2026-10/human/` (last change 31 Oct), so the human-validation verdict is still pending per the repo. Whether graders have returned since 29 Oct is **OPEN**.

**Blocked**
- Confirmation run read and analysis (CR-read). It waits on:
  - **HR-dev** (owner names two readers, rubric sign-off G1, 100 CDS pairs plus about 50 pointed pairs);
  - **G2**, the second implementation of the analysis, "not built";
  - owner decisions D4 (amendment), D6, D13, D15, D17 before freeze, and D16 before unblinding (`chunking-study-plan.md` §4–§7; registry `blocked_by`).
- **Still blocked on the readers and on calibration: yes.** No commit since 10 Oct touches the plan's status, and the registry row is unchanged.
- The hard pointed set waits on the owner's go (D1).
- Section-bounded arms in the OA eval wait on #828. #828 PRs 4, 5b and 7 have now merged, so this may be unblocking: OPEN.

**Inconsistencies found**
- `ROADMAP.md` draws "chunking study (done)" (line 146) and says #319–#321 were closed 25 Oct because "the chunking study answered the question". `chunking-study-plan.md` says BLOCKED. The ROADMAP probably means the applied trials. OPEN.
- The *RAGStack Chunking Study* artifact is dated 5 Sep with status as of 9–10 Sep and contains nothing later. It is stale against the repo on every post-September point.
- The *Chunking Study Plan* doc (10 Oct) matches the repo plan. The *Run Plan* (4 Oct) dates are superseded. The *Chunking Evaluation Design* artifact (about 31 Aug) predates Phase 0. *Power and Paired Tests* (1 Nov) is about the alias/glossary H1 power ("300 papers … 0.96 / 0.84"), not chunking. The *Experiment Registry* artifact is a 30 Oct snapshot (registry `a4d6c40`), older than the 31 Oct registry and missing the OA main run. Where they differ, the repo wins.
- The 3 Nov context file says "section-aware chunking failed its pass rule on both trials". Addendum D did pass one contrast (overlap at 128 in-section, Qwen3.6, +26), per the registry. It also says "#828 is a design note with no code"; that is outdated (PRs 4, 5b and 7 merged 4–5 Nov). Its "0.89–0.93 at 500k" is flagged by `chunking-study-plan.md` contradiction 3 as a misquote: the record gives 0.885–0.915 linear and 0.721–0.838 logit.

## 5. Connection to the alias study

- **Shared corpus construction.** The alias study (OA-1000, prereg frozen 4 Nov, sha256 `d54f4201…`; status CONFIRMATORY, complete, with a judge-validity-below-threshold flag) indexes 32,765 chunks as **512-token windows, `tok512o0`** (`fixed_token/512/0`, no overlap) (`docs/plans/results/alias-study-2026-11/README.md`, RESULTS line 27). That is the `ft512o0` arm the OA eval just found **not** non-inferior to 512/64 in the served setting. The alias study's own conclusions do not depend on this, but its corpus is not the production default.
- **5 Nov chunk-boundary control** (`ADDENDUM-2026-11-05-chunk-boundary-control.md`; artifact `control/artifacts/control-judge.json` at `aeafaeb`, clean, citable true; addendum EXPLORATORY, post-freeze, judge-only, no significance test). It re-judged 300 stage-A pairs against `sentence/512/0` chunks with section cuts (`structure_cuts sections/1`, tolerance 0.25, ceiling 640, min_piece 128).
  - Flip rate 40 / 300 = 13.3 % (9.9–17.7), against a re-judge null on identical windows of 20 / 300 = 6.7 % (4.4–10.1). About half the flips are judge noise.
  - On pairs whose two Qwen judges agreed in stage A, the control only lost answers: 28 / 150 = 18.7 % vs null 4.0 %, and 0 / 99 gained.
  - **For 24 of the 28 lost answers (85.7 %, 68.5–94.3), an overlapping neighbour chunk answers.** The evidence moved next door; it did not disappear.
  - Flips concentrate where the control chunk covers less of the original window: 20.8 % at coverage 0.5–0.8, against 5.4 % at ≥ 0.8.
  - 94 % of control chunks end in sentence punctuation, against 21 % of the windows. Control chunks are shorter: mean 405 vs 449 SFR tokens.
- **What it means for chunking (interpretation, not a record claim).** Boundary-respecting chunks redistribute evidence across adjacent chunks. A single-chunk, read-alone measure (found@10 with a one-chunk judge) scores that as loss. This is consistent with three other findings: (i) part 4's B2048 secondary, where large chunks lose one of two passages; (ii) the OA run, where removing overlap costs about 2 points; (iii) the section-aware trial, where no section arm passed. It argues that any section-bounded arm (#828, C-R1) needs a **neighbour or containment endpoint** (SG4: `nbr1_512`, `parent256`) and a **random-boundary control** (C-R0 is already planned in the OA eval plan) before a single-chunk metric can be read. The addendum says confirming the agreed-pair pattern needs "a new, pre-registered test on a single named endpoint with fresh pairs. None has been run or approved."

## 6. Open questions and next steps

1. **Human validation of part 4**: two more graders. If humans agree with neither model grader, H1–H4 become "grader-unvalidated" (prereg §5). Owner and graders.
2. **HR-dev two-reader read and G2**: the critical path of the CDS study since September. It needs the owner to name readers and settle D4, D6, D13, D15, D16 and D17. Does the CDS confirmation still matter now that the OA eval has produced a production decision? Should D6 option (c), re-scope, be invoked? OPEN, owner.
3. **Register the OA main run**: update the registry row (status, kind, citable with provenance) and publish a redacted public record. Today it exists only privately.
4. **Overlap**: reconcile "overlap settled, cut it" (Phase 0, dense, document-level) with the OA result (overlap 0 fails NI on the served path, passage-level). A dated note in the plan seems due.
5. **Section-bounded and random-boundary arms** in the OA eval, now that #828 PRs 4, 5b and 7 are merged. Use a containment or neighbour endpoint (lesson of the alias control).
6. **OA eval follow-ups named in RESULTS**: LLM answer grading on Argo (grader and subset to be fixed before grading); the owner's blind rating of the question sheet; a second writer family.
7. **sent512 vs sempool512**: `sempool512` leads every hybrid setting by point estimate, but no CI separates it in (a). It costs more to ingest (cost_rank 5 vs 3) and loses under BM25 (−2.65 [−4.23, −1.08] head to head). This needs a decision on the cost term (D16).
8. **512 vs smaller (128 / 256)**: H1 is not confirmed, and the June benchmarks lean nominally to 256. Unresolved.
9. **One default spec** (owner rule 10 Oct; contradiction 11) and the **#811 re-ingest** of sentence and semantic collections before a fleet roll.
10. **Paper B** (chunking full-length articles): not started; waits on the confirmatory verdicts.

## Slide proposal

### Slide A: "Overlap: settled on paper, not on the served path"
- Phase 0, dense, document-level: overlap 12.5 % − 0 % = −0.021 nDCG [−0.047, +0.007], a powered null (10 topics; citable unknown)
- OA eval, served hybrid + reranker, passage-level: ft512o0 − ft512o64 = −2.06 points hit@5 [−3.49, −0.59], n = 2,035. Fails the −2.0 non-inferiority bar
- Same direction under BM25: −2.21 [−3.64, −0.79]
- Reading: overlap's cost is small, but the production path does not show it is free. 512/64 stays

### Slide B: "OA eval: no challenger beat 512/64 in production"
| arm | hit@5 | Δ vs 512/64 [95 % CI] | ctx@5 | rule |
|---|---|---|---|---|
| ft512o64 | 79.0 | — | 2,316 | kept |
| sent512 | 79.6 | +0.54 [−0.74, +1.87] | 2,182 | NI, no switch |
| sempool512 | 80.3 | +1.28 [−0.20, +2.75] | 1,843 | below +2 bar |
| sem512 | 79.2 | +0.15 [−1.38, +1.67] | 1,762 | below +4 bar |
| ft512o0 | 77.0 | −2.06 [−3.49, −0.59] | 2,300 | fails NI |
| ft1024o0 | 76.7 | −2.36 [−4.03, −0.79] | 4,601 | fails |
- Pre-registered rule, frozen 5 Nov; 6 arms × 241,930 records; Qwen3.8 gold-quote questions
- The reranker flattens differences. Semantic chunks gain +8 points in vector-only retrieval and lose 4–6 under BM25
- Not yet in the registry or the public record; no answer grading yet

### Slide C: "Capped semantic is safe on deployed docs (provisional)"
- Part 4, pre-registered, citable: 372 two-passage questions, 71 pages, 4,096-token budget, δ 7.5 points, both readers
- H2 SEM512 ≥ live: +15 / +30 net. H4 fixed512 ≥ live: +4 / +12. H3 512 ≥ 1024: −10 / +5
- H1 512 > 128: not confirmed (+19 / +1)
- At 2,048 tokens, large chunks lose by 7 to 13 points
- Human grader validation: 1 of 3 graders in, "not validated" (descriptive); verdict pending

### Slide D: "Boundary-aware chunks move answers next door"
- Alias-study control (exploratory, 5 Nov): 300 pairs re-judged on sentence/section chunks (≤ 640 tokens)
- Flips 13.3 % vs 6.7 % on a re-judge of identical windows. About half is judge noise
- Agreed "answers" pairs: 18.7 % lost vs 4.0 % null; 0 / 99 gained
- 24 of 28 lost answers sit in an overlapping neighbour chunk
- Implication: section arms need a neighbour or containment endpoint and a random-boundary control

### Slide E: "The CDS confirmation still waits on two readers"
- Confirmation run (a): 80 topics, 3,738 pairs, Scout ×20 + Qwen ×10, quarantined since 9–14 Sep
- Calibration: 80 topics cannot power size (joint power N1 0.56, N3 0.37); R2 headers (0.95) is the only powered contrast
- Critical path: owner names readers → rubric (G1) → 100-pair read → κ → freeze; G2 second implementation not built
- No movement on the record since the 10 Oct plan. ROADMAP's "chunking study (done)" conflicts with this

## Sources

**Public repo `/home/user/wilke/ragstack` @ `5d65bea`**
- `docs/plans/chunking-study-plan.md` (10 Oct; §1–§8, contradictions 1–11)
- `docs/CHUNKING.md` (§ The default, The semantic size cap, Sentence segmentation, What the measurements say, Token budget, Recommendations, Not built yet)
- `STATUS.md` header (dev deploy rows; unreleased chunking correctness block, 23 Oct)
- `ROADMAP.md` lines 64, 146, 169
- `MEMORY.md` lines 89–102
- `docs/plans/828-document-structure.md` (status block)
- `docs/plans/results/chunk-size-deployed-docs-2026-10-23/README.md`
- `docs/plans/results/chunk-size-deployed-docs-part4-2026-10-25/{README.md, RESULTS.md, LABBOOK.md}`
- `docs/plans/results/alias-study-2026-11/{README.md, RESULTS.md, ADDENDUM-2026-11-05-chunk-boundary-control.md}`
- git log since 31 Oct (#828 PRs #937, #938, #959; alias control commits `59b9ebd`, `6418e4d`, `3b61e9e`)
- `git ls-remote origin` (branches `exp/oa-chunking-eval` @ `d2a8b9b`, `docs/section-aware-trial-261022` @ `337248e`, `docs/clark-chunk-size-writeup`)

**Published presentations**
- Only one deck is in `docs/presentations/`: `alias-study-2026-11.html`, https://wilke.github.io/ragstack/presentations/alias-study-2026-11.html (17 slides). Slide "Boundary-respecting chunks lost some answers — next door" is its only chunking slide.
- **No dedicated chunking presentation exists** on main (also checked `/home/user/ragstack` @ `c32de85`, #966).

**Private repo `/home/user/ragstack-experiments` @ `d8e5a2b`** (pull succeeded)
- `registry/experiments.json` (schema 1.2, updated 2026-10-31): records cited by id above
- `oa-chunking-eval-2026-10/{README.md, freeze-2026-11-05/FREEZE.txt, main-run-2026-11/RESULTS.md, main-run-2026-11/settings/a-hybrid-rerank/decision.json, main-run-2026-11/reports/PLAN-AND-PASS-RULE.md}`
- `section-aware-2026-10/README.md`
- `clark-part4-human-2026-10/human/` (file listing only)
- `binding-fields-261029` checked: not chunking

**Artifacts** (dates as found)
- RAGStack Chunking Study, https://claude.ai/artifact/VN8Sfs3jcXjGM1qAjMDoHy: dated 5 Sep, latest status 9–10 Sep; stale
- Chunking Study Plan, https://claude.ai/artifact/QuaBtVhwvDxWsUDZfjeWtT: Claude Docs doc "RAGStack Study Plan", tab "Sub-study 1 · Chunking", dated 10 Oct; matches the repo plan
- Chunking Study Run Plan, https://claude.ai/artifact/4a5GKWaFPMJLxTvdQ8jxUd: dated 4 Oct; gates G1–G7, roles, 8-week strawman
- Chunking Evaluation Design, https://claude.ai/artifact/8GPpYftdqcGCQYJuP4npGt: about 31 Aug; pre-Phase-0 design
- Reliable but Not Canonical, https://claude.ai/artifact/P7ZEKhu2aqYQ4Tp9AqGxVx: Paper A draft, dated 11 Oct
- Power and Paired Tests, https://claude.ai/artifact/AE9ZuDA6a8MejMBtY9nr61: dated 1 Nov; alias/glossary power, not chunking
- RAGStack Experiment Registry, https://claude.ai/artifact/Xe4wjtqAoRqdPsobWJKQpg: dashboard, datasets updated 2026-10-30 from registry `a4d6c40`

**Prior context checked**
- `/tmp/claude-0/-home-user/ae7f1c57-f776-5d63-b6db-9b088b066117/scratchpad/ragstack-content.md` (3 Nov): slides 8–11 checked; three corrections noted in §4

**Not verified (OPEN)**
- Whether part-4 graders or HR-dev readers have returned work outside the repos
- The OA main run's provenance and citable status
- Whether the 256/32 bulk default was unified
- What ROADMAP's "done" refers to
