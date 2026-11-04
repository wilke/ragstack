# Alias study: a second judge validation on retrieval-like pairs (plan, frozen before data)

Status: **frozen**. This file is committed before any record is drafted, any pair is built or any
label is requested. Every output records the sha of the commit that added this file. A later change
is an amendment. It is dated and appended at the end, and it never edits the text above it.

Background: [JUDGE-VALIDATION-PLAN.md](JUDGE-VALIDATION-PLAN.md) (the first validation, frozen at
`e2713e1`), [JUDGE-CALIBRATION.md](JUDGE-CALIBRATION.md) (rounds 1–3, validation, tiebreaker, review
follow-ups), [TIEBREAKER-PLAN.md](TIEBREAKER-PLAN.md), and the pre-registration §6.1–6.6 and §13.5.

**This set is not a gate on the main run.** The owner decided that the main study proceeds with the
current judge unchanged. This set is for a later comparison and refinement round. §8 states what its
result can and cannot change.

## 1. Why a second validation

### What the pre-freeze pilot found (counts only)

The pilot ran the composed rule of prereg §6.3 on the 191 held-out records. Final label: C
(`Qwen3.6-35B-A3B`, majority of 5) and E (`Qwen3.8-27B`, n = 1) agree, otherwise the `Gemini 4 Argon`
tiebreaker decides.

| finding | value |
|---|---|
| final label vs the Opus drift reference | 157 / 166 (**0.946**), below the 0.97 alarm |
| per arm (base / neighbour / random) | 0.944 / 0.946 / 0.946 |
| the same rule on the first validation's constructed pairs | 0.99 |
| disagreements that are final *answers*, Opus *not* | 7 of 9; in 6 of the 7, C and E agreed (no escalation) |
| kind of passage in all 9 disagreements | partial answer: the passage names the method or topic but does not state the asked-for information (for example, "one-way ANOVA was used" for a question asking what kind of comparison ANOVA is for) |
| disagreements about alias resolution | 0 of 9 |
| escalation (tiebreak) rate | 6.0 % overall, 8.1 % on the stage-A legs; the validation had 2.5 % |
| Argon re-judging the 37 drift pairs whose final label is *answers* | rejects 5 of the 7 Opus-*not*; keeps 27 of the 30 Opus-*answers* |
| owner, blind, on 12 of these hard pairs | agrees with Opus 8 / 12, with the final label 5 / 12, with Argon 4 / 10 (the 10 Argon labelled) |
| owner on the 7 false yeses | confirms 5 of 7 as *not* |

### The weakness of the first validation set

The first set was **constructed**: gold, BM25 lexical neighbours and alias collisions. It tested alias
resolution well. It held few **topical near-misses**, which are what real retrieval mostly produces: a
chunk on the same topic or method that does not state the answer. Prereg §6.4 already said the
calibration pairs "are not the retriever outputs judged here". The pilot shows the cost: every error was
a near-miss and none was an alias error. This set takes its pairs from real retrieval over the study's
corpora and oversamples near-misses.

## 2. Candidates under comparison

| id | candidate | prompt | label |
|---|---|---|---|
| **K0** | current rule: C + E, Argon tiebreak (prereg §6.3, unchanged settings) | `alias-judge/2` | C and E agree → that label; else Argon |
| **K3** | the same rule with a new prompt | `alias-judge/3` for C, E and Argon | as K0 |
| **KA** | Argon alone | `alias-judge/2` | Argon's label |
| **KS** | `GPT-5.6 Sol` alone | `alias-judge/2` | Sol's label |
| **KY** | "yes needs a third yes" | `alias-judge/2` | *answers* only when C, E **and** Argon all say yes; otherwise *not* |
| **KO** | Opus, single call | `alias-judge/1` | repetition 1 of the reference (§4); scored on the owner subset only |

Settings are the prereg's: thinking on for C and E, `max_tokens` 8192 (local) and 4096 (Argon, Sol),
served-model check, `fallback_model: ""`. E takes n = 3 and the rule uses sample 0. KY reuses KA's labels;
a missing label in KY counts as *not*, and the count is printed.

**`alias-judge/3`** is `alias-judge/2` with sharper *not* criteria and worked partial-answer examples.

* It is committed, with its system sha, **before any record of this set is drafted**. Its only data input
  is the pilot's 12 owner-labelled pairs; nothing else from the pilot, this set or the main run is read.
* `JUDGE_RULE_V2` and `JUDGE_OUTPUT_FORMAT` stay verbatim, so K3 differs from K0 only in the yes / no
  criteria and the examples.
* The examples are **synthetic**. They show the patterns of the 12 pairs and never quote them, because
  the harness is public code.
* The added criterion says, in substance: naming the method, test, measure or topic the question is
  about, without stating what it asks (purpose, value, result, definition or condition), is *not*.

A known bias, stated now: K3 encodes the owner's reading of partial answers. Its agreement with the
owner subset (§4) is partly by construction. Its agreement with Opus is not.

## 3. The set

### 3.1 Records (fresh, out of sample)

* **Papers.** OA-1000 (set O's chunks and merged glossary, unchanged). A paper is **excluded** when it is
  in any of:
  1. the study's record set: every paper activated by the main run's seeded order (recomputed from the
     main pool with the drafter's own rule, `n_active` read from the main waves state), and every paper
     in the main draft, acceptance and accepted logs, the extension to 300 included;
  2. the pilots: every paper in the pilot-1 and pilot-2 draft logs and pilot 2's activated papers (the
     191 held-out records of the pre-freeze pilot are main-run records, so item 1 already covers them);
  3. the calibration set: every question paper of `calib/pairs.jsonl`;
  4. the first validation set: its exclusion list (`calib/validation/exclude-papers.txt`), its activated
     papers and every paper in its draft, acceptance and accepted logs.

  The union is written sorted, one paper per line, to `calib/validation-2/exclude-papers.txt`, and its
  sha256 goes into `provenance.json` and `LOG`. The pool builder drops these papers before its seeded
  draw (`alias_pool.exclude_papers`). Earlier outputs are only read. The count of eligible papers left
  is printed before drafting (open question 1).
* **Pipeline.** The study's own pipeline at ragstack `main`: pool builder, `run-waves` with `c_first`,
  drafter `alias-draft/2` (Gemini 2.5 Pro via the gateway, `max_tokens` 16384), layer 1, verifier
  `alias-verify/3` (Claude CLI with the Codex fallback). Settings are copied from the main config, and
  every difference besides paths, seeds, caps and the exclusion is listed in `provenance.json`.
* **Own run.** Out-dir `out/validation-2/`, config `cfg/alias-validation-2.json`, corpus set name `W`
  (record ids `alias-W-…`), pool seed base `261103|alias|validation-2`.
* **Size.** `waves.target_papers` = **30** satisfied papers. At the first validation's rates (83 records
  from 24 satisfied papers) this gives about 100–110 accepted records. The run stops at the target.
  **Accepted records only** are used.

### 3.2 Candidate pool (real retrieval)

Each record is retrieved, read-only, in the **15 cells** of the 5 Layer-1 attachments (base × none,
base × merged, neighbour × none, random-1 × none, random-2 × none) × 3 retrievers (default, norerank,
BM25), with the study's pinned retrieval config and collections. No collection is written. No main-run
result is read.

The pool is the main run's label demand (prereg §6.2), applied to these records: in a cell where the
gold is outside the top 10, every non-gold top-10 chunk; in a cell where the gold is in the top 10,
every non-gold chunk ranked **above** it. Pairs (record, chunk) are deduplicated across cells. Each
pair keeps its cells, best rank, arm (base / neighbour / random, as prereg §6.2) and a same-paper flag.

### 3.3 Strata (assigned without any judge)

Two features are computed per pool pair, from text only:

* `topic_hit`: the passage contains the TERM, or a short form that the passage paper's merged glossary
  defines as the TERM (the `alias_abbrev` matching rule);
* `ans_cov`: the share of the content words of the record's reference answer that occur in the passage
  (the stop list and normalisation of the harness BM25). Open question 2 covers records without one.

| stratum | rule | the failure mode it targets |
|---|---|---|
| **near-miss** | `topic_hit` and `ans_cov` below the median of the `topic_hit` pairs | topic or method named, answer absent |
| **partial** | `topic_hit` and `ans_cov` from the median to below the 85th percentile | answer partly stated or implied |
| **clear answer** | `topic_hit` and `ans_cov` at or above the 85th percentile | candidate true answers |
| **clear non-answer** | not `topic_hit` | off-topic or weakly related chunks |

The cut points are quantiles of the pool, computed before sampling, so they need no label. The strata are
sampling frames, not truth: the reference labels decide what a pair is, and the frame × reference table
is reported. Because every pair's inclusion probability is known, the weighted estimates of §5 stay
unbiased for the pool whatever the frames' quality. A poor frame only costs precision.

No LLM stratifier is used: every strong model at hand is a candidate or the reference. If the owner wants
one, Gemini 2.5 Pro (the drafter) is the only model not under test (open question 3).

### 3.4 The labelled pairs

Two components, both drawn with seed `261103|alias|validation-2|pairs`:

* **F (full lists).** Every pool pair of a seeded sample of **25 records**. It labels whole top-10 lists,
  which the Hit@10 re-scoring of §5.3 needs, and it shows the natural distribution of pairs. If F
  exceeds 600 pairs, records are dropped from the end of the seeded order until it fits.
* **S (stratified top-up).** From the other records' pool pairs, per stratum, the first pairs in the order
  sha256 of `261103|alias|validation-2|pairs|<stratum>|<pair>`, until the stratum's total (F + S) reaches:

| stratum | drawn (F + S) | expected Opus-unanimous |
|---|---|---|
| near-miss | **360** | about 300 |
| partial | 150 | about 125 |
| clear answer | 100 | about 85 |
| clear non-answer | 100 | about 85 |

The expected column assumes the first validation's loss: 83 no-reference and 17 split of 587 (17 %). A
stratum whose frame holds fewer pairs is taken whole and the power it reaches is reported. It is not
extended unless a dated amendment is written before any label. The pair file is frozen once built and
its sha256 is pinned in every later config (`pairs_sha256`). The total is at least 710 pairs. F's surplus
in over-filled strata can raise it, up to about 1,000. How many deduplicated pairs one record yields is
not known yet (open question 11). The cost table uses 850.

## 4. References

* **Opus ×3 (primary reference).** `Claude Opus 5.5` via the gateway, prompt `alias-judge/1` (the
  calibration and drift reference prompt), 3 repetitions, default temperature, `max_tokens` 2048. An
  empty or unparseable reply is re-issued **once** (prereg R4); after that the repetition stays empty.
  Classes as in the first plan: **unanimous** (3 valid, equal), **split**, **no reference**. Metrics
  against Opus use the unanimous pairs. Split and no-reference pairs are counted per stratum.
  **Caveat:** Opus is most likely to split on near-misses, so the unanimous subset is easier than the
  stratum. The split rate per stratum is reported, and the owner subset includes split pairs.
* **KO** (repetition 1) equals the reference on every unanimous pair, so it is scored on the owner subset only.
* **Owner subset (arbiter).** 100 pairs labelled blind by the owner:
  * **A, 80 pairs**, drawn before any model label (seed `261103|alias|validation-2|owner`): near-miss 40,
    partial 20, clear answer 10, clear non-answer 10. These carry the weighted owner estimates.
  * **B, 20 pairs**, drawn after the Opus and candidate labels exist: up to 10 Opus-split pairs and the rest
    from pairs where at least two candidates disagree, by the same seeded order. Reported separately and
    never weighted.
  * **Why 100.** The prereg puts 100 pairs at about 1.5 h of owner time (R15). At 80 to 100 pairs, a 95 %
    interval on an agreement near 0.85 has a half-width of about 0.07 to 0.08. That tells whether
    Opus-vs-owner agreement here sits near the pilot's 8 / 12 (0.67) or near the 0.95 assumed for the
    reference. The 40 near-miss pairs give ±0.11 to ±0.15 (at agreement 0.85 to 0.67). The subset cannot
    separate 0.95 from 0.98: it arbitrates direction, and Opus carries precision.
  * **The sheet** shows what the judges see (question, term, passage, title, definitions) and no stratum,
    arm, rank, cell or model label, in seeded order. Answers: *answers*, *not* or *unsure* (counted, left
    out of agreement). The labels are committed privately, with the ingest sha256, before the owner sees
    any model output of this set.
  * **Opus-vs-owner agreement** (accuracy, κ, AC1, per stratum and weighted) is a headline result.

## 5. Metrics

### 5.1 Per candidate

Against the Opus-unanimous reference (all candidates but KO) and against the owner (all candidates), each
as a 2 × 2 table of counts with:

* **accuracy** and **Cohen's κ**, with **Gwet's AC1** beside κ (prereg §6.6);
* **false-yes rate** = candidate *answers* / reference-*not* pairs;
* **false-no rate** = candidate *not* / reference-*answers* pairs;
* **escalation rate** for K0 and K3 (pairs sent to Argon / all pairs), and Argon's load for KY;
* a missing label counts as a disagreement, and the count is shown.

Each is reported per stratum (unweighted, Wilson 95 %), for F alone (unweighted, natural mix), and for the
pool (inverse-inclusion weights, with a record-cluster bootstrap interval, seed
`261103|alias|validation-2|boot`). Splits by arm, retriever, same-paper flag and record kind (G / C) are
descriptive.

### 5.2 Comparison with the current rule

The **primary comparison** is on the near-miss stratum, against Opus-unanimous: each of K3, KA, KS and KY
vs K0, paired, by a one-sided exact McNemar test with Holm adjustment over the four. A candidate is called **better
than K0** when (a) its near-miss accuracy is higher with adjusted p < 0.05, (b) its weighted false-no rate
is no more than 2 points above K0's at the point estimate, and (c) its owner-subset agreement (A) is not
lower than K0's at the point estimate. Where the Opus-based and owner-based rankings disagree, both are
printed, and the owner ranking is the one quoted (§4, arbiter). Everything else is descriptive.

### 5.3 Implied inflation of Hit@10

A record counts as a hit (the study's found@10) if any top-10 chunk is labelled *answers*. If a judge
gives a false yes with probability p on each non-answering chunk, a record with no true answer among
its 10 chunks becomes a hit with probability 1 − (1 − p)^10 under independence: about 0.10 at p = 1 %,
0.26 at 3 % and 0.40 at 5 %. A small per-pair rate is therefore a large per-record rate.

Independence does not hold. Errors cluster by question: a question with one near-miss chunk tends to have
several, and a judge that accepts one tends to accept the others. The data decide which way and how far
that moves the per-record rate. So the plan **re-scores** and does not assume independence. On every
record × cell list in F, Hit@10 is computed under each candidate's labels and under the reference.
Reported per cell and per arm, with a record-cluster bootstrap interval: the net difference and the gross
flips each way (hits gained by false yeses, hits lost by false noes). A list holding a split or
no-reference pair is excluded and counted. The per-arm difference matters because unequal inflation
bears on H2. This is descriptive. With 25 records the interval will be wide.

## 6. Size, power and cost

**Power.** The near-miss stratum is sized to tell a judge with true agreement 0.98 from one at 0.95.
One-sided α 0.05, power 0.80, normal approximation: n = 253 Opus-unanimous pairs (two-sided: 330). The
paired test of §5.2 (one-sided) depends on the discordance, which is a guess here (0.05). At that value a
3-point difference needs about 340 pairs at α 0.05 and about 525 at the strictest Holm level (0.0125); a
4-point difference needs about 295 at 0.0125. The 360 drawn (about 300 unanimous) meet the one-sample
target and the 4-point paired target, not the 3-point one. A committed script computes exact figures
before drafting (`artifacts/validation2_power.json`). If the frame is smaller, the reached power is printed.

**Gateway cost (estimate; approval needed, open question 5).** Per-call token figures are the measured ones:
Opus about 1.3 k prompt tokens, Argon and Sol about 0.9 k. `alias-judge/3` adds the length of its examples
to every K3 Argon call. Drafting is scaled from the first validation (203 calls, 172 k in, 425 k out for 83
records). Gemini 2.5 Pro reasons heavily, so drafting output dominates its cost.

| step | gateway calls | prompt tokens | completion tokens |
|---|---|---|---|
| drafting (about 110 records) | about 270 | about 0.23 M | about 0.56 M |
| Opus ×3 on 850 pairs, plus one re-issue per empty (about 15 %) | about 2,950 | about 3.8 M | about 0.15 M |
| Argon `/2` on all pairs (KA, KY, K0 tiebreaks) | about 850 | about 0.77 M | about 0.05 M |
| Argon `/3` on K3 escalations (assumed ≤ 10 %) | about 90 | open | small |
| Sol `/2` on all pairs | about 850 | about 0.77 M | about 0.05 M |
| **total** | **about 5,000** | **about 5.6 M** | **about 0.8 M** |

Off the gateway: about 160 verifier CLI calls, about 1,650 retrieval calls, and local C and E for `/2`
and `/3` (at 1.85 s and 0.74 s per pair, about 35 min per prompt for 850 pairs).

**Caps and notices.** At most **4 gateway calls in flight** in total; models run one after the other. Every
body carries `fallback_model: ""` and `max_tokens`. Before each batch a **size notice** (`GATEWAY
SIZE-NOTICE`, planned calls and tokens) goes to `LOG` and is acknowledged (`preflight`, then
`--notice-acknowledged`). The budget stop writes `PAUSE` and is not retried. Each batch is capped at 110 %
of its planned calls. A served-model mismatch stops the run and stores nothing.

## 7. Order of work, freezing and outputs

1. This plan (commit 1). No record is drafted before it is merged.
2. Code, tested, before drafting: exclusion builder, pool builder, stratum features, F / S sampler,
   `alias-judge/3` (with its system sha), KY, the candidate analysis, the power script.
3. Exclusion list and sha256, config, `run-waves` with its size notice.
4. Retrieval over the 15 cells, pool, strata, pair file (frozen, pinned), owner subset A.
5. Labels: Opus ×3; C (n = 5) and E (n = 3) with `/2` and `/3`; Argon and Sol `/2` on all; Argon `/3` on
   K3's escalations. The owner labels subset A meanwhile, seeing no model output. Then subset B.
6. Analysis as in §5. Anything not in §5 is labelled **exploratory**.

**Freezing.** Every output records this plan's commit sha in `provenance.json`. Later changes are dated
amendments appended below, and an amendment written after any label exists says which labels had been seen.

**Outputs.** Private: `calib/validation-2/` (exclusion list, config, pool, pairs, labels, owner ingest and
sheet, analysis, `provenance.json`, `SHA256SUMS`). Public, later: a counts-only section in
JUDGE-CALIBRATION.md. No paper text, record id, paper id or host enters the repository.

## 8. What the result can and cannot change

**Can:** motivate a judge change (prompt, rule or model) for a **future** study, under its own frozen plan;
motivate a **re-judging sensitivity** of this study's committed pairs, declared **exploratory**, run after
the main analysis is committed and printed beside the pre-registered results, never in their place; and
give a measured near-miss false-yes rate for the limitations (prereg §13.3).

**Cannot:** change the main run's judge, prompt, rule, tiebreaker, drift alarm or any final label; gate,
pause or delay the main run; or be read as a pass or fail of the current judge (there is no pass
criterion; §5.2 is a comparison).

This run reads no main-run label or result and writes no collection. It does share the gateway budget and
the local endpoints with the main run (open question 4).

## 9. Open questions for the owner

1. **Eligible papers.** Are enough OA-1000 papers left after the exclusion to satisfy 30? If not, may
   records come from neighbour or random-arm papers, which calibration never used?
2. **Reference answer.** Does every accepted record carry one for `ans_cov`? If not, what stands in (the
   verifier's evidence quote, or the gold chunk)?
3. **Strata.** Are the median and 85th-percentile cuts acceptable, or is a Gemini 2.5 Pro stratifier preferred?
4. **Schedule.** Should this run wait until the main run's judging ends, so the two never share the
   4-in-flight cap, the budget or the local endpoints?
5. **Budget.** Is about 5,000 gateway calls (5.6 M prompt, 0.8 M completion tokens) approved? Drop Sol to
   save about 850?
6. **`alias-judge/3`.** Does the owner write it, or approve a draft? Are synthetic examples acceptable?
7. **KY.** Is "C, E and Argon all say yes" the intended reading?
8. **"Better".** Are Holm over four, the 2-point false-no tolerance and the owner condition right (§5.2)?
9. **Opus.** Add a fourth Opus call per pair (about 850 calls) to score a single call on the full set? Keep
   the reference on `alias-judge/1`, or also run `/2` on a subset?
10. **Owner labels.** Is *unsure* allowed? Is a second rater available (prereg §14 O12)?
11. **F size.** Are 25 full-list records and the 600-pair cap right, given that pairs per record are
    unknown until retrieval?

## Amendments

(none)
