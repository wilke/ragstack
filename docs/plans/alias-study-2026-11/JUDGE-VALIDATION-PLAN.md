# Alias study: out-of-sample validation of the answer judge (plan, frozen before data)

Status: **frozen**. This file is committed before any validation question is drafted, any pair is
built or any label is requested. Every output of the validation records the sha of the commit that
added this file. A change to this file after that commit is an amendment, and it is dated and
listed at the end. It never edits the text above it.

Calibration background: [JUDGE-CALIBRATION.md](JUDGE-CALIBRATION.md) (rounds 1–3, merged #894,
#896, #897). The private calibration outputs are under `calib/`, `calib/round2/` and `calib/round3/`.

## 1. The frozen rule under test

The rule was selected on the round-1–3 calibration set (410 pairs, 372 with a unanimous Opus
reference). In sample it scored **0.992** accuracy with **2.7 %** of pairs escalated.

| judge | endpoint | model | prompt | thinking | samples | its label |
|---|---|---|---|---|---|---|
| **C** | mango `:8004` | `Qwen3.6-35B-A3B` | `alias-judge/2` | on | n = 5, T 0.7 | majority of the valid samples |
| **E** | mango `:8008` | `Qwen3.8-27B` | `alias-judge/2` | on | n = 1, T 0.7 | the one sample |

**Rule.** When C and E both give a label and the labels agree, that label is final. Otherwise the
pair is **escalated** to the Opus reference. "Otherwise" covers three cases: the labels disagree,
C has no majority (no valid sample, or a tie among the valid ones), or E's sample is invalid.

Operational details, fixed now:

* Both judges use the merged harness (`glossbench_alias_judge.py`) with `max_tokens` 8192,
  `qwen.require_served_model: true` and `chat_template_kwargs.enable_thinking: true`.
* C is requested once per pair with `n = 5`. E is requested once per pair with `n = 3`. **The rule
  uses E's first stored sample (index 0)**, which is an n = 1 draw because the samples of one
  request are independent. The majority of E's three samples is reported for information only.
* The in-sample figure above is reproduced with exactly this reading (C = the first 5 stored
  samples, E = the first stored sample) from `calib/round2/labels-qwen-C.jsonl` and
  `calib/round3/labels-qwen-E.jsonl`: 0.992, with 10 of 372 pairs (2.7 %) escalated.
* An escalated pair takes the Opus reference label. On the primary pairs (unanimous reference) it
  is therefore correct by construction. In deployment one Opus call would replace the majority of
  three, and round 1 measured Opus's agreement between repetitions at 0.996.

## 2. The validation set

### Questions (fresh, out of sample)

* **Papers.** These come from the OA-1000 export (set O's chunks and merged glossary, unchanged). A
  paper is **excluded** when it is in any of the following:
  1. the study's activated papers: the first `n_active` papers of the main run's seeded activation
     order, recomputed from `out/main/pool-alias-O.jsonl` with the drafter's own rule
     (`<pool seed>|waves` over the reachable papers), and `n_active` read from
     `out/main/waves-state-alias-O.json`. This set contains every satisfied paper;
  2. every paper in `out/main/draft-alias-O.jsonl`, `out/main/acceptance-alias-O.jsonl` and
     `out/main/accepted-alias-O.jsonl`;
  3. every paper in the pilot-1 and pilot-2 draft logs, and pilot 2's activated papers;
  4. every question paper of the calibration set (`calib/pairs.jsonl`, `q_paper`).

  The union is written to `calib/validation/exclude-papers.txt`, and its sha256 is recorded. The
  pool builder drops these papers before it samples (`alias_pool.exclude_papers`). `out/main` is
  only read.
* **Pipeline.** The study's own pipeline at ragstack `main` (a986a19 or later): pool builder,
  `run-waves` with `c_first`, drafter prompt `alias-draft/2` (Gemini 2.5 Pro via the partner gateway,
  `max_tokens` 16384, at most 4 in flight, the budget stop, `PAUSE`), layer 1, and verifier
  `alias-verify/3` (Claude CLI, with the Codex fallback). The settings are copied from the main config.
* **Own run.** Out-dir `/rag/tmp/alias-study-2026-11/out/validation/`, config
  `cfg/alias-validation.json`, corpus set name `V` (the same export and glossary as set O; record ids
  read `alias-V-…`), pool seed base **`261031|alias|validation`** (pool seed
  `261031|alias|validation|V`).
* **Size.** `waves.target_papers` = **22** satisfied papers (2 accepted G on distinct aliases and
  1 accepted C each). At the main run's realised rates (731 accepted records from 355 activated
  papers for 200 satisfied), this gives about 80 accepted records (about 50 G and 30 C) from about
  40 activated papers. The run stops at the target. It is not extended to hit a count.
* **Which records.** **Accepted records only** (a layer-1 and a layer-2 pass). Round 1 also took
  layer-1-only records. The validation uses what the study uses.

### Pairs (offline, the round-1 construction)

`build-calib` from the harness, unchanged except for reading the validation logs and keeping only
accepted records (`calib.records: "accepted"`). The quotas per question are round 1's: gold 1,
lex_same 1, lex_other 2 and collisions 6 (half of each meaning where possible). BM25 k1 1.2, b 0.75,
top 200. Calibration seed **`20261031|judge-validation`**. The expected size is about 400–500 pairs
(round 1 averaged 6.3 pairs per question). The file is frozen once it is built, and its sha256 is
pinned in every later config (`pairs_sha256`).

Strata: `gold`, `lex_same`, `lex_other`, `coll_same`, `coll_diff`. **`coll_diff` is decidable from
the definitions block.** The passage paper's own definition of the alias, which is a different
primary, is shown to the judge, so the stratum measures the prompt, not judgement. Round 1 found
every judge at 1.00 there. It is included in the totals. Every metric is also reported without it.

## 3. Labels

* **Opus reference.** The partner gateway, `Claude Opus 5.5` (the strongest Claude Opus the gateway
  lists, as in round 1), with prompt **`alias-judge/1`** (the prompt the calibration reference used),
  3 repetitions per pair, the default temperature, `max_tokens` 2048, at most 4 in flight, the budget
  stop and `PAUSE`. An empty answer is re-issued, as in round 1.
* **C** and **E** as in §1.
* **Claude CLI** (`claude-opus-5-5`, once per pair, off the gateway) as a second reference, if time
  allows. It is exploratory only.

## 4. Metric, pass criterion, and how pairs without a reference are handled

The reference classes are fixed per pair from the Opus labels:

| class | definition | use |
|---|---|---|
| **unanimous** | 3 valid labels, all equal | the primary metric's pairs |
| **split** | at least 1 valid label, but not unanimous (a 2–1 split or fewer than 3 valid) | reported separately: the gate's escalations, and its agreement with the Opus majority where one exists |
| **no reference** | no valid Opus label on any repetition (Opus-filtered: empty on every attempt) | reported separately as "no reference": count, strata, and what the gate would do (accepted / escalated). Never scored |

**Primary metric** (on the unanimous pairs, all strata):

* **accuracy** = (pairs where the gate's final label equals the reference) / (unanimous pairs), with
  escalated pairs taking the reference label;
* **escalation share** = escalated pairs / unanimous pairs.

**Pass criterion: accuracy ≥ 0.98 AND escalation share ≤ 5 %.** Both are point estimates, with
Wilson 95 % intervals reported beside them. The verdict is PASS or FAIL on the point estimates. The
same two numbers **without `coll_diff`** are reported as a declared secondary, with their own
PASS/FAIL against the same thresholds. The primary verdict is the all-strata one.

Reported with the verdict, all on the unanimous pairs:

* the accuracy of the gate on the pairs it accepts (non-escalated), with FP (false yes) and FN
  (false no) counts;
* **each judge alone**: C (majority of 5), E (n = 1, sample 0) and E (majority of 3). A pair where
  the judge has no label counts as an error, and the count is shown. Accuracy, κ, FP and FN;
* per stratum and per record kind (G / C): accuracy, escalations;
* escalation share over all pairs (including split and no-reference pairs).

The evaluation is deterministic: the samples as stored, no bootstrap. It is computed by
`analyse-gate` in the harness.

## 5. Order of work and outputs

1. This plan (commit 1).
2. Code (small, tested): `alias_pool.exclude_papers`, `calib.records`, `analyse-gate`.
3. Exclusion list, then the validation config, then `run-waves` with the size notice in `LOG`.
4. `build-calib`, frozen and pinned.
5. Labels: Opus ×3, C (n = 5), E (n = 3), optionally CLI.
6. `analyse-gate`, then the verdict. Anything else is labelled **exploratory**.

Private outputs: `/rag/tmp/alias-study-2026-11/calib/validation/` (pairs, labels, analysis,
`provenance.json` with this plan's commit sha, `SHA256SUMS`). The repository gets this plan, the
code and a counts-only validation section in JUDGE-CALIBRATION.md.

## Amendments

(none)
