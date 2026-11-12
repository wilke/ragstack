# Alias study: a second judge validation on retrieval-like pairs, with graded labels (DRAFT v2, not frozen, not run)

Status: **DRAFT v2**, written 2026-11-11. It supersedes DRAFT v1 (2026-11-04), which is kept verbatim as
the appendix at the end of this file so that nothing it specified is lost; where v2 says "as in v1", the
text is in that appendix. Nothing in this plan has been run. No label, pair file or rater sheet exists yet.
v1 said the plan would freeze once the owner answered its questions. That has changed. v2 freezes at the
commit that records three things: (a) the owner approves this text and the prompt in
[JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md), (b) the owner answers §13, and (c) §4's panel is confirmed
(§13 Qb). Every output records the sha of that commit. Any change after it is a dated amendment at the
end of the v2 text (before the appendix).

Owner rule (Amendment 1 (6)): plans are made or checked with Fable and always reviewed. This draft was
written by an Argo Opus agent and checked, corrected and finished by Fable on 2026-11-11; the list of
corrections is in the PR that merged it.

## What v2 changes, and why

| topic | v1 | v2 | why |
|---|---|---|---|
| label scale | binary *answers* / *not* | graded **full / partial / none** for one (question, passage) pair (§2) | Amendment 1 (2); the spot check could not give credit to partial evidence |
| human reference | owner alone, 100 pairs | a panel of R raters (default 4, minimum 2; the owner is one of them). Each rater grades fewer items: a shared core plus overlapping blocks (§6.3) | Amendment 1 (1) |
| "unsure" | not allowed | allowed, with a reason code, treated as missing (§2.3) | Amendment 1 (1) |
| inter-rater agreement | none (one rater) | Krippendorff's α (ordinal) is primary; weighted κ and AC2 beside it (§7) | more than one rater |
| disagreement | n/a | no forced adjudication; the distribution of panel grades is reported (§7.3) | "partial allows for disagreement between graders" |
| judge vs human | accuracy against the owner | agreement with the panel compared with agreement between humans, plus confusion matrices and collapses (§8.1) | a reference made of several raters |
| validity reading | the 0.90 binary rule (alias-study prereg §13.5) | a bar relative to human agreement, per collapse scheme, with a complete outcome map (§9). The §13.5 rule itself is untouched | §9 |
| what paper-b may cite | not addressed | a stated licence (§9.2) | owner request |
| alias-judge/3 | "/2 with sharper *not* criteria", drafted from 12 pilot pairs | a graded prompt drafted by the agent from public repository text only; owner approves it ([JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md)) | Amendment 1 (4) |
| candidates | K0, K3, KA, KS, KY, KO | adds graded KA3 and KS3, and a candidate panel from the model gateway (§4) | Amendment 1 (3), (4); Amendment 2 |
| frames | near-miss / partial / clear answer / clear non-answer | near-miss / mid-coverage / high-coverage / off-topic. Only the names change | "partial" is now a label |
| budget | about 5,000 calls | about 7,300 calls on the study gateway plus about 5,900–7,900 on the model gateway at 850 pairs; the ceiling is 20,000 in all (§10) | Amendment 1 (3) |
| v1 questions Q1–Q11 | open | answered and folded in (§14) | Amendment 1 (5) |

Background: [JUDGE-VALIDATION-PLAN.md](JUDGE-VALIDATION-PLAN.md) (the first validation),
[TIEBREAKER-PLAN.md](TIEBREAKER-PLAN.md), [JUDGE-CALIBRATION.md](JUDGE-CALIBRATION.md),
[VERIFIER.md](VERIFIER.md), the alias-study [RESULTS.md](../results/alias-study-2026-11/RESULTS.md)
(the spot check), the [pre-registration](../results/alias-study-2026-11/prereg/PREREG-alias-study.md)
(§6.2–6.6, §13.5, §14 O8 and O12) and
[CONTROL-PLAN.md §9](../results/alias-study-2026-11/control/CONTROL-PLAN.md) (graded labels, set-level
sufficiency). Also the [papers README](../../papers/README.md) (the paper-b blocker) and the
`study-writeup` skill.

**This is not a gate.** The main run is finished, and so are its judge, labels and reported lines. Nothing
here changes them (§12).

## 1. Why a second validation (v1 §1, kept)

* The first validation scored the judge on constructed pairs. The composed rule scored 0.992 there
  (JUDGE-CALIBRATION.md, validation). On the 166 retrieval-like pilot pairs with an Opus reference it was
  157/166 = 0.946, and about the same in each arm (0.944 / 0.946 / 0.946). The constructed set overstated
  agreement.
* There were 9 disagreements. In 7 of them the final label said *answers* and Opus said *not*. C and E
  agreed with each other in 6 of those 7. All 9 were the partial-answer kind: the passage names the method,
  test or topic the question is about but does not state the asked-for information. None involved an alias.
* Escalation rose to 6.0 % overall and 8.1 % on the stage-A legs, against 2.5 % on constructed pairs. Argon,
  re-judging the 37 drift pairs whose final label was *answers*, rejected 5 of the 7 Opus-*not* and kept 27
  of the 30 Opus-*answers*.
* The owner, blind, on 12 of these hard pairs agreed with Opus 8/12, with the final label 5/12 and with
  Argon 4/10; on the 7 false yeses the owner confirmed 5 as *not*.
* The alias-study spot check (RESULTS.md) found 41/50 = 0.82 [0.692, 0.903] on the *answers* stratum and
  81/100 unweighted (0.872 re-weighted to the stage-A label shares), with κ 0.615 and AC1 0.626. The 0.90
  rule fired on both clauses. Of the 19 disagreements, the owner later tagged 12 *partial* and 7 *full*, 0
  *none* (exploratory, post hoc).
* The first set was weak for this purpose: few near-miss pairs, a single rater, and a binary rubric.

**Why graded labels (added in v2).** Most of the disagreements are partial evidence. Whether partial
evidence counts depends on how the label is read. With *partial* read as *not answering*, the judge's
label matched the tag-implied label on 9 of 19 and the owner's on 10 of 19; with *partial* read as
*answers*, the judge matched on 13 of 19 and the owner on 6 of 19 (RESULTS.md, `counts.readings`). A
binary label hides this. A graded label measures it, and each binary judge can then be scored against the
collapse that its labels actually track (§2.4).

## 2. Labels

Raters and every judge see **the same fields**: the passage paper's title, the definitions block (the
short forms the passage paper defines, those occurring in the passage), the passage, the TERM and the
question. That is what `/1` and `/2` show and what the alias-study spot-check sheet showed
(`alias_study_spot.py`, `PAIR_ITEM_FIELDS`). **No reference answer is shown** to anyone: a correct answer
from another paper counts (VERIFIER.md; the first rule of `/1`), and a reference would anchor the grader
to one answer. The record's reference answer (or the verifier's evidence quote, Q2) is used only to compute
the `ans_cov` sampling feature of §5.3.

Raters and judges also get the **same rubric, word for word**: §2.2 below is the *Parts and conditions*,
*Labels* and *Deciding* blocks of `alias-judge/3` (JUDGE-3-PROMPT.md §1.2), reproduced on the rater sheet.
If the two texts ever differ, the committed prompt wins and this section is corrected by amendment.

Literature to verify before freeze (`study-writeup` skill: a source is adopted only after it has been
read): graded relevance assessment; nugget-based evaluation; sufficient-context labelling;
Krippendorff's α and its conventional cut-offs; weighted κ; Gwet's AC2.

### 2.1 What is graded

The grade covers **what the passage states, read alone**, about the question. It does not cover whether
the passage is from the question's paper: a passage from another paper can be **full** (VERIFIER.md). No
outside knowledge and no other passage is used. The alias rules of `/2` apply unchanged: a short form that
the passage paper defines as the TERM is the TERM.

### 2.2 Rubric (verbatim from `alias-judge/3`)

```text
Parts and conditions:
- A PART is a piece of information the question asks for: for example a value or amount, a direction of change, a result, a purpose or reason, a cause, a definition, a method, a name, or a number of items. A question may ask for one part or several.
- A CONDITION is an organism, population, material, method or setting that the question names. Conditions are not parts.
- A part is STATED when the passage says it about the TERM, in any words. A part is WORKABLE when a reader can work it out from what the passage says, by combining or calculating from its statements, without outside knowledge. Otherwise the part is ABSENT. A pointer elsewhere ("see Table 2", "as described before") states nothing.
- A condition is MET when the passage, its paper's title or its definitions state it, CONTRADICTED when they state a different one, and UNSTATED otherwise.

Labels:
- "full": every part is STATED, as precisely as the question asks, and every condition is MET. Extra information does not matter, and details the question does not ask for may be missing. When a part can have several valid answers (a purpose, a cause, an example), one stated answer is enough; all of them are needed only when the question asks for a number of items or for all of them. A passage from another paper is "full" if it states the parts under the question's conditions, even when its answer differs from the one in the question's own paper.
- "partial": at least one part is STATED or WORKABLE, no condition is CONTRADICTED, and "full" does not hold. Typical cases: some parts are stated and others are absent; a part is stated less precisely than asked (a direction where an amount is asked, a general class where a specific item is asked, fewer items than the number asked); a part is only workable; a condition is unstated, or is met only for a narrower or broader group or setting than the question names.
- "none": no part is STATED or WORKABLE, or a condition is CONTRADICTED. This includes a passage that names the term, method, test, measure or topic the question is about but does not state what is asked; a passage with other facts about the term (for example its result when the question asks for its purpose); and a passage about a different concept that shares a short form.

Deciding:
1. List the question's parts and conditions.
2. For each part, find the words in the passage that state it or let a reader work it out. If no part has such words, the label is "none". Naming what the question is about is never a part.
3. Check the conditions. One contradicted condition makes the label "none".
4. Between "full" and "partial", ask: could a reader who sees only this passage, its title and its definitions write the complete answer without guessing or working anything out? If yes, "full"; if not, "partial". When unsure between "full" and "partial", choose "partial".
5. In the reason, name the stated parts; for "partial", also name what is missing.
```

The three labels are exhaustive and exclusive: a contradicted condition is *none*; otherwise no stated or
workable part is *none*; otherwise every part stated precisely with every condition met is *full*;
everything else is *partial*.

Notes:

* v1's near-miss criterion ("naming the method, test, measure or topic … without stating what it asks, is
  *not*") is the first "This includes" clause of the *none* label and step 2's last sentence.
* The phrase in `alias-judge/1` and `/2`, "states the core of what is asked … even if it adds or omits
  details", is closest to **full** (extra information does not matter; details not asked for may be
  missing). It is the only sentence of `/2` that `/3` drops, because it is the line the owner and the judges
  drew in different places (CONTROL-PLAN §9).
* The owner's tag *other answer* (RESULTS.md) has no label of its own: a passage that answers under the
  question's conditions is *full* whatever its answer; one whose conditions contradict the question's is
  *none*.

### 2.3 Unsure (raters only)

* A rater uses *unsure* only when the pair cannot be graded. A reason code is required: *meaning* (the
  question is ambiguous as written), *text* (the passage is garbled or cut off), *question* (the question
  looks malformed or unanswerable as written), or *expertise* (outside the rater's competence).
* Doubt between two adjacent grades is not *unsure*. Step 4 sends doubt about completeness to *partial*,
  and disagreement there is expected ("partial allows for disagreement between graders").
* *Unsure* counts as missing. Items that end up short of grades are topped up (§6.3). *Unsure* counts are
  reported per rater and per reason code.
* Judges have no *unsure* option (JUDGE-3-PROMPT.md §3, item 5). A missing judge label counts as a
  disagreement (§8).

### 2.4 Collapses (binary readings of a grade)

* **Strict**: *full* vs (*partial* + *none*).
* **Lenient**: (*full* + *partial*) vs *none*.

Every binary judge is scored against both collapses of the panel's grades. The verdict tells which one its
labels measure (§9).

### 2.5 Out of scope

Set-level sufficiency (whether the top-10 together answer the question, CONTROL-PLAN §9) is deferred to a
later plan (§13 Q8).

## 3. Candidates

| id | rule | prompt | in the Holm family |
|---|---|---|---|
| K0 | the composed rule as run: C (majority of n = 5), E (sample 0 of n = 3), Argon on disagreement or a missing label | `/2` binary | baseline |
| K3 | composed and graded: C's grade is the one held by more than half of its valid samples (otherwise no label); E's grade is sample 0. If they are equal, that grade stands; otherwise Argon `/3` decides | `/3` | yes |
| KA | Gemini 4 Argon alone | `/2` | yes |
| KA3 | Argon alone (new) | `/3` | descriptive |
| KS | GPT-6.1 Sol alone (prereg §14 O8; its allow-list entry and served id are confirmed before any call, as TIEBREAKER-PLAN.md §1 did) | `/2` | yes |
| KS3 | Sol alone (new) | `/3` | descriptive |
| KY | *answers* only when C, E and Argon all say yes; otherwise *not* (Q7); a missing label counts as *not* and is counted | `/2` | yes |
| KO | Opus repetition 1 | `/1` | panel comparison only (§6.2) |
| KC3, KE3 | C or E alone, graded | `/3` | descriptive |
| KX1–KX4 | the §4 panel | `/2` and `/3` | descriptive |

* The confirmatory family is K3, KA, KS and KY against K0, with Holm over four (Q8 accepted).
* Settings are v1's (appendix §2): thinking on for C and E; `max_tokens` 8192 (local) and 4096 (Argon,
  Sol); temperature omitted on the gateway, T 0.7 locally; E requested with n = 3 and read at sample 0
  (samples 1–2 reported for information); `fallback_model: ""`; the served-model check stops the run on a
  mismatch.
* **Changed from v1:** K3's tiebreak reads Argon `/3` from the KA3 run, which labels every pair, so no
  separate escalation calls are made. K3's escalation rate (C and E not equal, or either without a label)
  is expected to exceed K0's with three classes. It is reported.

### 3.2 alias-judge/3 (Amendment 1 (4))

The prompt, its parsing contract and its rationale are in [JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md). What
this plan fixes about it:

* It is committed in the harness (`JUDGE_SYSTEMS["alias-judge/3"]`), with its system-prompt sha recorded in
  an amendment here, **before any record of set W is drafted**.
* **Changed from v1:** its inputs were public repository text only (the pilot's description, the spot
  check's counts and tags, the `/1` and `/2` specifications). It read no pair, question or passage, not the
  pilot's 12 owner-labelled pairs and not the 19 spot-check disagreements. v1 had named the 12 pairs as its
  only data input.
* The alias rules and the context framing of `/2` are kept verbatim (a test asserts it); the user message
  differs from `/2` in its closing line only, so K0 and K3 see the same context byte for byte.
* Output: `{"reason": "at most 25 words", "answers": "full" | "partial" | "none"}`, reason first. The key
  stays `answers` and the value sets of `/2` and `/3` do not overlap, so a mixed-up configuration yields
  missing labels, never wrong ones. The stored field is `grade`.
* It carries **six synthetic examples** with invented terms (v1 Q6: synthetic examples accepted). They
  model no pair.
* Known bias: the rubric and the examples encode one reading of completeness, written by the agent and
  approved by the owner, who is also rater r1. Figures without the owner (§7.1) show how much this matters.
* `/3` gets no paraphrase check (the `/2` check is in JUDGE-CALIBRATION.md). That is a limitation.
* Before any set-W record is drafted, the live self-check of JUDGE-3-PROMPT.md §4 (synthetic cases with
  owner-set expected labels) runs once per `/3` judge, and its counts go into the amendment that records
  the system sha.

## 4. Candidate judges (from the model gateway inventory, 2026-11-11)

Source: the inventory of the **model gateway** made by the vllm session on 2026-11-11 and verified by it;
the gateway's `/healthz` was reachable from the development host; its `/status` and `/v1/models` were read
with the service key. **No model call was made.** This gateway is distinct from the study gateway that
serves Opus, Argon and Sol (§6, §10); the two have separate budgets and separate caps.

**The gateway.** OpenAI-compatible, with a service key labelled for this study. Per-call response headers
record the serving backend (`X-Model-Router-Backend`); a request header switches fallback off
(`X-Model-Router-Fallback: off`), after which an unavailable backend returns an error rather than another
backend. It logs labels, model ids and timing, and no text.

**Pinned local models** (vLLM 0.31.0): `Qwen3.8-27B` FP8 (weights snapshot `017b9c7a…`, 131k context,
thinking switchable; judge E's model) and `Qwen3.6-35B-A3B` (judge C's model; not permanent). Routing, from
`/status`: a model's `route_order` is a preference list and a route is used only where the model has an
instance. `Qwen3.8-27B` has one instance, the local vLLM backend; the ALCF catalogue lists no such id; so
today every call by that id is served locally. No per-request route override exists.

**Gate on every call to a pinned local model** (code item, §11): the `X-Model-Router-Backend` value must
equal the local backend name recorded for that model in the private config, and the response `model` field
must equal the pinned id; any other value voids the row (not stored, re-issued once, then counted as
missing). This is the served-model check of `/2` (TIEBREAKER-PLAN.md §1) extended by the backend header; it
also catches the unlikely case of the ALCF catalogue adding the exact id later. The weights pin holds only
while the local server is not restarted: `/v1/models` is read at the start and end of every labelling wave
and compared with the pinned snapshot, and a change stops the run. **Open items:** the vllm session confirms
the backend names and the snapshot before the freeze; an optional owner decision is a gateway-side lock
(`route_order: [local]` on the two Qwen records), §13 Qc.

**ALCF-hosted models**: 52 ids, no revision pins; a shared account at 20 requests/s; cold starts of more
than 30 minutes and node cost for models not running; two brief outages observed during the inventory.
Running chat models at the time of the read include `Mistral-Large-3-675B-Instruct-2512` (tested live),
`Meta-Llama-3.1-70B-Instruct`, `Llama-4-Scout-17B-16E-Instruct` (the real Scout), `gpt-oss-120b` (on two
clusters), `gemma-4-31B-it` and `mistral-large-4`. The four proposed judges below were all listed and
reported *running*.

**Not recommended**: the local id named `Llama-4-Scout` (it is Qwen3.6 and ends 2026-11-30); small models;
domain fine-tunes; unknown-provenance ids (`beam`, `muse-spark`, `inkling`); anything needing a cold start,
as a main judge.

**ALCF models as decision judges.** Owner decision, 2026-11-11 (Amendment 2): "all models are acceptable
even from ALCF". Conditions this plan attaches: every row records the `X-Model-Router-Backend` value, the
response `model` field and the call date; a revision request goes to ALCF support before the freeze, and
its answer (a revision, or "none available") is recorded in the amendment; a candidate whose response
`model` field changes during the run stops that candidate's labelling, and its rows up to then are kept and
dated. These models are **descriptive** (outside the Holm family, §3) either way.

**Proposed panel** (the agent's suggestion; **OWNER DECISION PENDING**, §13 Qb):

| id | model | served from | role | thinking |
|---|---|---|---|---|
| KX1 | `Qwen3.8-27B` FP8, pinned | model gateway, local | anchor: judge E's model behind the gateway's pin and gate; measures serving and pin stability, adds no family | on, as E |
| — | `Claude Opus 5.5` | the study gateway | reference family; already in the design as Opus ×3 `/1` and KO (§6). No `/3` call: Q9 declined a fourth Opus call | — |
| KX2 | `Mistral-Large-3-675B-Instruct-2512` | ALCF | family diversity (tested live) | recorded if the backend reports it |
| KX3 | `Meta-Llama-3.1-70B-Instruct` | ALCF | family diversity | — |
| KX4 (optional) | `gpt-oss-120b` | ALCF | a fourth family, funded only if §10's ceiling allows | recorded |

Per candidate: `/2` and `/3` on all pairs, plus `/3` repetitions 2–3 on H (§6.3), for self-consistency.
Settings: `max_tokens` 4096, temperature omitted, fallback off, at most 4 in flight, the model gateway's
own size notice and 110 % cap. The first 20 calls of each candidate are measured for tokens (the size
notice), since none of their tokenizers is calibrated (JUDGE-CALIBRATION.md gives Argon and Opus only).
Each candidate is scored under §8.1, §8.2 and §8.4 and is descriptive. Family correlation (each candidate's
errors against Opus-unanimous, cross-tabulated with C, E, Argon and Sol's) is reported.

## 5. The set (v1 §3, kept unless marked)

### 5.1 Records

* **Papers.** OA-1000 (set O's chunks and merged glossary, unchanged). The exclusions are v1 §3.1's items
  1–4: the main run's record set (activated papers and every paper in the draft, acceptance and accepted
  logs, the extension to 300 included); the pilots; the calibration set's question papers; the first
  validation set (its exclusion list, activated papers and logs). The union is written sorted to
  `calib/validation-2/exclude-papers.txt`; its sha256 goes into `provenance.json` and `LOG`. The count of
  eligible papers is printed before drafting.
* **Pipeline and run.** The study's pipeline at ragstack `main`, run separately: out-dir
  `out/validation-2/`, config `cfg/alias-validation-2.json`, corpus set name `W` (record ids `alias-W-…`),
  pool seed base `261103|alias|validation-2`. Settings copied from the main config; every difference besides
  paths, seeds, caps and the exclusion is listed in `provenance.json`.
* **Size.** `waves.target_papers` = **30 satisfied papers**, which at the first validation's rates (83
  records from 24 satisfied papers) gives about 100–110 accepted records. Accepted records only.
* **Q1 (accepted).** If the eligible OA-1000 papers cannot satisfy 30, more papers are drawn from the
  neighbour and random arms with seed base `261103|alias|validation-2|added`. The record count by source
  is reported, and every metric is also shown for OA-1000-paper records alone.

### 5.2 Pool

v1 §3.2 unchanged: each record retrieved read-only in the **15 cells** (5 Layer-1 attachments × 3
retrievers) with the study's pinned retrieval config and collections; the label demand of prereg §6.2;
pairs deduplicated across cells, keeping cells, best rank, arm and a same-paper flag. No collection is
written; no main-run result is read.

### 5.3 Frames (renamed only)

v1 §3.3's two text features, `topic_hit` and `ans_cov`, cut at the median and the 85th percentile of the
`topic_hit` pairs (Q3 accepted; no LLM stratifier):

| frame (v1 name) | rule |
|---|---|
| **near-miss** | `topic_hit` and `ans_cov` below the median |
| **mid-coverage** ("partial") | `topic_hit` and `ans_cov` from the median to below the 85th percentile |
| **high-coverage** ("clear answer") | `topic_hit` and `ans_cov` at or above the 85th percentile |
| **off-topic** ("clear non-answer") | not `topic_hit` |

The frames are sampling frames, not truth; the frame × panel-grade table is reported. **Q2 (accepted):**
when a record has no reference answer, `ans_cov` is computed from the verifier's evidence quote. That
quote enters no sheet and no prompt (§2).

### 5.4 Sampling

Both components are drawn with seed `261103|alias|validation-2|pairs` (v1 §3.4):

* **F** (full lists): every pool pair of 25 seeded records, capped at 600 pairs by dropping records from
  the end of the seeded order (Q11 accepted).
* **S** (per frame, from the other records' pairs, in the order sha256 of `…|pairs|<frame>|<pair>`), until
  each frame's total (F + S) reaches:

| frame | drawn (F + S) | expected Opus-unanimous |
|---|---|---|
| near-miss | **360** | about 300 |
| mid-coverage | 150 | about 125 |
| high-coverage | 100 | about 83 |
| off-topic | 100 | about 83 |

* The expected column assumes the first validation's 17 % loss (no-reference and split). A frame holding
  fewer pairs is taken whole and the power reached is reported; it is not extended without a dated
  amendment written before any label.
* The pair file is frozen once built; its sha256 is pinned in every later config (`pairs_sha256`).
* The total is at least 710 pairs and up to about 1,000 (F's surplus). Costs are computed for 850 and
  checked at 1,000 (§10).

## 6. References

### 6.1 Opus ×3 on alias-judge/1 (kept; Q9: no fourth call)

As in v1 §4: `Claude Opus 5.5` via the study gateway, `alias-judge/1`, 3 repetitions, default temperature,
`max_tokens` 2048; an empty or unparseable reply re-issued once (prereg R4); classes **unanimous** / **split**
/ **no reference** (the provider filter clustered on vaccine and virus questions). Metrics against Opus use
the unanimous pairs; split and no-reference counts are reported per frame. The split rate is expected to be
higher on near-miss pairs; it is reported. Opus remains the reference for the binary comparison (§8.2) and
is itself scored against the panel (§8.1).

### 6.2 KO

Opus repetition 1 is scored against the panel (§8.1). This replaces v1's Opus-vs-owner headline.

### 6.3 Rater panel

**Raters.** R raters (default **4**, minimum **2**; Amendment 1 (1)). r1 is the owner. Raters are
pseudonymous (r1…rR) and no name is committed.

**H, the items the panel grades:**

* **A**: 120 pairs, drawn before any model label exists, seed `261103|alias|validation-2|panel`, by frame:
  near-miss 60, mid-coverage 30, high-coverage 15, off-topic 15 (v1's 80 at 40/20/10/10, scaled by 1.5 for
  the panel). A carries the weighted estimates.
* **B**: 20 pairs, drawn after the model labels exist (seed `…|panel|B`): up to 10 Opus-split pairs, the
  rest pairs on which at least two candidates disagree under either collapse. B is reported separately and
  never weighted.

**Core.** The first 20 A pairs in the order sha256 of `…|panel|core|<frame>|<pair>`, 8 / 6 / 3 / 3 by frame.
**All raters grade the core.** It gives every rater pair the same items for severity profiles and for the
thinned-α check (§7.1).

**Blocks.** The other 100 A pairs are split into R(R−1)/2 rater-pair blocks, round-robin within each frame
in the seeded order, so that **each non-core item is graded by exactly two raters** and every rater pair
shares one block. B's 20 pairs are split the same way when B is drawn. Each rater's load is
L = 20 + 2·100/R + 2·20/R = 20 + 240/R.

| R | items per rater | shared by each rater pair (core + block) | grades in total |
|---|---|---|---|
| 2 | 100: **H is reduced** to A 80 (40 / 20 / 10 / 10) + B 20 and both raters grade everything (no block structure) | 100 | 200 |
| 3 | 100 | 60 | 300 |
| **4 (default)** | **80** | **40** | **320** |
| 5 | 68 | 32 | 340 |
| 6 | 60 | 28 | 360 |

R = 2 is the floor the owner confirmed. It keeps the rater load at v1's 100 items by shrinking A to v1's 80,
and gives no leave-one-rater-out or without-owner figure (§7.1); those rows are then marked *not
computable*. The reduction, if used, is stated in the freeze commit.

**Time.** The binary spot check took about 1.5 h for 100 pairs (prereg R15). A graded rubric with parts
and conditions is slower: plan on 1–1.5 minutes per item, so about 1.5–2 h per rater at R = 4, and about
2–2.5 h at R = 2.

**Sheet.**

* The §2.2 rubric is printed at the top, then the §2 fields for each item.
* The sheet does not show the frame, arm, rank, cell, any model label, any other rater's grade, or whether
  an item is in the core or in B.
* Each rater sees their own order, sha256 of `…|panel|sheet|<rater>|<pair>`.
* Choices: *full* / *partial* / *none* / *unsure* (with a reason code), and an optional private note.

**Independence.**

* Raters work alone and use no LLM. (Some of the spot-check *tags* were discussed with an LLM before the
  owner decided, RESULTS.md; grades here are not.)
* No discussion of any item until A and B are committed. No reading of the full paper.
* Familiarisation: 6 pairs from the first validation set (private, constructed pairs from papers that set
  W excludes), graded on the sheet format. There is no answer key, and these grades are discarded.

**Commit.**

* The sha256 of each rater's ingest is committed privately before that rater sees any model output or any
  other rater's grades.
* The owner opens no label file until the owner's own A and B are committed.
* Only counts and shas are public.

**Top-up.** A non-core A item with fewer than 2 valid grades (an *unsure* or an unfinished item) goes to one
more rater, chosen by seeded rotation among the raters who have not seen it. There is one round. Items
still short are counted as single-rated or unrated and reported.

**Completion.** A rater who completes less than 90 % of their items has the unfinished items sent to
top-up. No rater is excluded for disagreeing.

## 7. Inter-rater agreement

### 7.1 Statistics

* **Primary:** Krippendorff's α, ordinal metric, on A, using all grades (core items carry R grades, block
  items 2) and treating missing grades as missing.
* α nominal for the strict and lenient collapses of the grades (α_strict, α_lenient).
* Pairwise quadratic-weighted κ (primary κ) and linear-weighted κ; unweighted κ per collapse; Gwet's AC2
  (ordinal weights) and AC1 (per collapse) beside them.
* Also reported: per frame; A ∪ B (secondary); leave one rater out; without the owner (r1); the core
  thinned to two seeded grades per item (to show that the core's extra grades do not drive α); rater
  profiles (grade shares) and severity on the core.
* Intervals: a record-cluster bootstrap stratified by frame, 10,000 resamples, seed
  `261103|alias|validation-2|boot`.
* The α code is tested against a published worked example, verified before freeze (§2).

### 7.2 Acceptability (per scheme S ∈ {ordinal, strict, lenient}, at the point estimate; the CI is printed)

| α_S | reading |
|---|---|
| ≥ 0.800 | reliable |
| 0.667 to < 0.800 | tentative |
| < 0.667 | not reliable: the scheme is **not evaluable** (§9.1) |

* The cut-offs are Krippendorff's conventional ones; the source is verified before freeze.
* A pairwise κ below 0.40 is flagged and has no other effect.
* If strict turns out less reliable than lenient (or the reverse), that is an expected and informative
  result, not a failure.

### 7.3 Disagreement: no forced adjudication

* No discussion, no tie-break, no adjudicated label. Every grade vector is kept as graded.
* Reported per frame, weighted and unweighted: the distribution of panel patterns, which are unanimous
  *full* / *partial* / *none*; *full–partial*, *partial–none* and *full–none* splits; single-rated.
* A **display label**, for presentation only: all valid grades equal → that grade; 3 or more valid grades
  with a strict majority → that grade; otherwise a split label naming the range. Block items, with two
  grades, are never majority-labelled.
* Wide (*full–none*) splits get a descriptive read of their reason codes and notes after A and B are
  committed. No grade is changed.

### 7.4 Per-item forms

Each item carries its grade vector, its pattern, its mean score (*none* 0, *partial* 1, *full* 2) and a
consensus flag (all valid grades equal).

## 8. Metrics

### 8.1 Judges vs the panel (per judge J and scheme S; binary judges under strict and lenient; graded judges under ordinal and both collapses)

* **a_JH**: per item, the share of the item's valid human grades that J matches. Under a collapse a match
  is 0 or 1; under ordinal, a match is scored 1 − |x − y| / 2 on the 0 / 1 / 2 scale.
* **a_HH**: per item, agreement between the item's raters, on the same scale (the mean over rater pairs).
* **Δ = a_JH − a_HH**, averaged over items, paired by item, with a bootstrap CI (§7.1's bootstrap).
* Consensus accuracy: J against the consensus items (§7.4), Wilson interval.
* Confusion matrices: J against each individual rater's grade (each item's weight of 1 split across its
  graders), and J against the panel pattern.
* α with J added as a rater, compared with α for humans only.
* For graded judges: Kendall's τ-b against the panel mean, and the mean signed difference.
* **False-support rate** (J says *full* or *answers* where the panel consensus is *none*) and
  **false-full rate** (J says *full* where the consensus is *partial* or *none*).
* A missing judge label scores 0 in a_JH.
* Results are pool-weighted on A (inverse-inclusion weights of the frame draw) and unweighted per frame. B
  is reported separately and unweighted.
* Headline: the Opus majority and KO against the panel. The owner-alone agreement (r1's grades, v1's
  owner subset) is printed beside every panel figure so v1's reading stays available.

### 8.2 Judges vs Opus-unanimous (v1 §5.1, kept)

Accuracy, Cohen's κ, Gwet's AC1, false-yes and false-no rates, escalation (K0, K3) and Argon's load (KY); a
missing label counts as a disagreement and is counted. Per frame (unweighted, Wilson 95 %), F alone, and
pool-weighted with the record-cluster bootstrap. Splits by arm, retriever, same-paper flag and record kind
(G / C) are descriptive. Graded judges are scored through both collapses.

### 8.3 Primary comparison (v1 §5.2, kept; Q8 accepted)

* Near-miss frame, against Opus-unanimous: K3, KA, KS and KY each against K0, paired, by a one-sided exact
  McNemar test with Holm adjustment over the four.
* A candidate is **better than K0** when all three hold: (a) adjusted p < 0.05; (b) its weighted false-no
  rate is at most 2 points above K0's at the point estimate; (c) its panel Δ on A under **strict** is at
  least K0's at the point estimate. (c) is v1's owner condition with the panel (which includes the owner)
  in the owner's place; the owner-alone version is printed beside it (§8.1), and §13 Q3 asks the owner to
  confirm the substitution.
* **K3 enters via the strict collapse** (*full* = *answers*), with lenient shown beside it. Three reasons:
  `/2`'s "core … omits details" is closest to *full* (§2.2); v1 built K3 so that a partial answer is *not*;
  the false yeses so far were on partial evidence.
* If the Opus-based and the panel-based rankings of the candidates differ, both are printed and the
  panel's is quoted.

### 8.4 Hit@10 re-scoring on F (v1 §5.3, kept)

Under independence a false-yes rate p inflates a record with no true answer into a hit with probability
1 − (1 − p)^10: 0.10, 0.26 and 0.40 at 1 %, 3 % and 5 %. Independence does not hold, so on every record ×
cell list in F, Hit@10 is re-scored under each candidate's labels and under the Opus reference, per cell and
per arm, with the net difference and the gross flips each way and a record-cluster bootstrap interval; a list
holding a split or no-reference pair is excluded and counted. Added for graded candidates: full@10 (a
*full* chunk in the top 10), support@10 (a *full* or *partial* chunk), and the number of records with
support only, per cell and per arm. Descriptive; with 25 records the intervals will be wide.

## 9. Validity verdict and what paper-b may cite

### 9.1 What replaces the 0.90 binary rule for this study's reading

The alias-study prereg §13.5 rule and its flag stay exactly as they are (§12). The verdict below applies
**per judge J and per scheme S** (strict and lenient for a binary judge; ordinal, strict and lenient for a
graded judge), and every (J, S) lands in exactly one of four outcomes:

* **not evaluable**: α_S on A is below 0.667 (§7.2). Nothing can be said about J under S.
* Otherwise J **meets the human bar** under S when all three hold:
  * (i) pool-weighted Δ ≥ −0.05 at the point estimate;
  * (ii) Δ on the near-miss and mid-coverage frames of A together ≥ −0.05 at the point estimate;
  * (iii) the one-sided 95 % lower bound of the pool-weighted Δ ≥ −0.10.
* **below** the bar: (i) or (ii) fails.
* **unresolved**: (i) and (ii) hold but (iii) fails (too few items to tell).

Reported beside the verdict: α_S with its CI, Δ with its CI, n, consensus accuracy, false-support and
false-full rates, and the pattern table. Rationale: the two point-estimate clauses mirror §13.5's structure
(overall, plus the hard stratum); the bar is relative to how well humans agree, because with graded labels
there is no single right answer; for a binary judge the verdict says which collapse its labels measure.

### 9.2 Paper-b licence

**What the panel does not do.** The papers README lists paper-b as blocked on the two-reader human read;
that read (HR-dev, the TREC-CDS confirmation run, `docs/plans/chunking-briefing-2026-11-06.md`) uses a
different corpus and task. This panel does not replace it and does not unblock the CDS confirmatory
verdict.

**What it can license.** Judged figures on OA-1000 produced by a judge configuration this study scores:
the alias study, and the rechunk, boundary and reach studies that reused the frozen judge. All four must
hold:

1. the scheme is evaluable and the judge **meets the human bar** under S (§9.1);
2. α_S ≥ 0.800; if 0.667 ≤ α_S < 0.800 the citing sentence says "panel reliability tentative";
3. the population matches: tok512o0 windows over the study's collections. Figures on other chunkings (for
   example reach-rechunk's adopted chunking S) carry a population-shift caveat unless those collections are
   added to this study as extra read-only cells (§13 Q6);
4. the figures come from a clean-commit `citable: true` artifact under `docs/plans/results/`, pass through
   `claims.json` / `check_claims.py`, and quote α_S, Δ and n beside the first figure.

**Outcome map** (per judge configuration; S = strict, L = lenient):

| strict outcome | lenient outcome | how its judged figures may be cited in paper-b |
|---|---|---|
| meets | any | as a "full answer in the top 10" figure (and, if lenient also meets, as "any support" too) |
| below, unresolved or not evaluable | meets | as an "any support in the top 10" figure only; never as "answers" without that wording |
| below, unresolved or not evaluable | below, unresolved or not evaluable | only with "judge validity not established", next to the judge-free figure (found@10-gold or quote@10) |

Condition 2's "tentative" clause attaches to whichever S is cited. Paper-c keeps the §13.5 flag on every
judged figure; it may quote the panel result beside the flag as later evidence, under the same map.

## 10. Size, power, cost

**Opus power** (v1 §6, kept; frames renamed): one-sided α 0.05, power 0.80, agreement 0.98 vs 0.95: 253
unanimous pairs (two-sided 330). Paired test at discordance 0.05: a 3-point difference needs about 340 pairs
at α 0.05 and about 525 at the strictest Holm level (0.0125); a 4-point difference about 295 at 0.0125. The
360 drawn (about 300 unanimous) meet the one-sample target and the 4-point paired target.

**Panel precision** (rough normal approximations):

* At agreement about 0.85 under a collapse, the Wilson half-width is about 0.065 on 120 items and about
  0.09 on the 60 near-miss items.
* Per block item under a collapse, the paired difference d = a_JH − a_HH takes values in {−1, 0, +½}. With
  an SD of about 0.35 the SE of Δ is about 0.03 on 120 items, and more after weighting.
* Chance of meeting the bar: about 0.85 for a judge at true Δ = 0, about 0.4 at Δ = −0.05, under 0.1 at
  Δ = −0.10 (clause (iii) is what bites).
* The power script simulates the α interval and these chances into `artifacts/validation2_power.json`
  before drafting.
* Sizes do not change without a dated amendment made before any label exists.

**Per-call tokens.** Measured: Opus `/1` about 1.3k prompt tokens; Argon `/2` about 0.93k; Sol `/2` about
0.9k (GPT-5.6 Sol; GPT-6.1 Sol unmeasured); completion about 50 per call (JUDGE-CALIBRATION.md). The `/3`
system is 5,562 characters against 1,615 for `/2`, so `/3` adds 3,947 characters per call: about 0.93k tokens
for Argon (4.26 chars/token, the harness's calibration) and about 1.2k at the flat 3.4 for Sol and the
candidates. Argon `/3` is therefore about 1.86k per call and Sol `/3` about 2.1k. (v2's first draft put the
difference at 0.4k; the measured text says 0.93k.)

**Study-gateway budget at 850 pairs** (Amendment 1 (3): up to 4 × the ~5,000 drafted = **20,000 calls**):

| item | calls | prompt / completion tokens |
|---|---|---|
| drafting (about 110 records; scaled from the first validation's 203 calls, 172k in, 425k out for 83 records) | ~270 | 0.23M / 0.56M |
| Opus ×3 `/1`, plus one re-issue per empty (about 15 %) | ~2,950 | 3.8M / 0.16M |
| Argon `/2` (KA, KY, K0's tiebreaks) | ~850 | 0.79M / 0.04M |
| Sol `/2` (KS) | ~850 | 0.76M / 0.04M |
| Argon `/3` (KA3, K3's tiebreaks) | ~850 | 1.58M / 0.04M |
| Sol `/3` (KS3) | ~850 | 1.75M / 0.04M |
| Argon `/3` + Sol `/3` repetitions 2–3 on H (140 items × 2 reps × 2 models) | 560 | 1.1M / 0.03M |
| re-issue allowance | ~80 | 0.1M / — |
| **study gateway** | **~7,260** | **~10.1M / ~0.95M** |

**Model-gateway budget** (the §4 panel; its own key and cap). Per candidate: `/2` on all pairs (850), `/3` on
all pairs (850), `/3` repetitions 2–3 on H (280) = **1,980 calls**, about 3.0M prompt tokens at the flat 3.4
(0.9k × 850 + 2.1k × 1,130); completion unknown until measured.

| panel | calls at 850 pairs | prompt tokens |
|---|---|---|
| KX1–KX3 | ~5,940 | ~9M |
| KX1–KX4 (with the optional gpt-oss-120b) | ~7,920 | ~12M |

**Totals and the ceiling.** Counted together against the 20,000 ceiling, whichever budget pays:

| pairs | study gateway | + 3 candidates | + 4 candidates | with the 110 % caps (4 candidates) |
|---|---|---|---|---|
| 850 | ~7,260 | ~13,200 | ~15,180 | ~16,700 |
| 1,000 | ~8,380 | ~15,220 | ~17,500 | ~19,250 |

Every cell is under 20,000. **Pre-specified drop order** if the realised pair count would push the capped
total over 20,000: first the optional candidate's `/2` run (850–1,000 calls), then the optional candidate,
then all candidates' repetitions 2–3. The order is applied before any candidate call and recorded. The
Opus reference and the Holm-family runs are never cut.

* Sol (GPT-6.1) and the candidates have no measured tokens; the first 20 calls of each are measured for the
  size notice.
* The study gateway's budget is at least 10M tokens a day (prereg §14 O7), so the Opus and `/3` batches are
  spread over several days.

**Off-gateway:** about 160 verifier CLI calls; about 1,650 retrieval calls (110 records × 15 cells); local C
and E on `/2` and `/3` (at the first validation's 1.85 s and 0.74 s per pair: about 37 min per prompt for 850
pairs, about 75 min in all, more for `/3`'s longer input).

**Human time:** 320 grades at R = 4, about 1.5–2 h per rater, plus at most 10 % in top-ups (§6.3).

**Caps and notices** (kept from v1): at most 4 calls in flight per gateway, models one after the other;
`fallback_model: ""` on the study gateway and `X-Model-Router-Fallback: off` on the model gateway; the
`GATEWAY SIZE-NOTICE` with `preflight` and `--notice-acknowledged` before each batch; the budget stop writes
`PAUSE` and is not retried; each batch capped at 110 % of its planned calls; a served-model (or backend)
mismatch stops the run and stores nothing.

## 11. Order of work, freezing, outputs

1. This text and JUDGE-3-PROMPT.md approved, §13 answered, the §4 panel confirmed. The freeze commit.
2. Code, with tests: exclusion builder (as in the first validation); pool builder with the added-arm
   fallback (Q1); frame features and cuts; F/S sampler; `alias-judge/3` and `parse_judgement_v3`
   (JUDGE-3-PROMPT.md §2 and §4); C's graded majority and the K3 rule; KY; the model-gateway backend with
   the backend-header gate and `/v1/models` snapshot check (§4); panel assignment (core, blocks, top-up);
   per-rater sheet writer and ingest; agreement statistics (α ordinal and nominal, weighted κ, AC2/AC1, the
   bootstrap, the published worked example as a test); judge-vs-panel scoring (§8.1); the candidate
   analysis (§8.2–8.4); the power script with the panel simulation.
3. The `/3` live self-check (JUDGE-3-PROMPT.md §4) on every `/3` judge; the system sha and the counts go into
   an amendment here. Raters confirmed, rubric read, familiarisation done.
4. Exclusions, config and `run-waves` with the size notice; the ALCF revision request filed (§4).
5. Retrieval, pool, frames; the pair file frozen; A drawn, assigned and the sheets written.
6. Labelling: model labels (Opus ×3; C and E on `/2` and `/3`; Argon and Sol on `/2` and `/3`; repetitions
   on H; the panel candidates), while the raters grade A blind and their ingests are committed; top-up;
   then B is drawn, assigned, graded and ingested.
7. Analysis. Anything outside §7–9 is labelled exploratory.

**Freezing.** Every output records this plan's commit sha in `provenance.json`. An amendment written after
any label exists states which labels had been seen.

**Private outputs:** `calib/validation-2/` (exclusion list, config, pool, pairs, labels, `panel/` with
assignment, sheets, ingests and top-ups, analysis, `provenance.json`, `SHA256SUMS`).

**Public outputs:** a counts-only section in JUDGE-CALIBRATION.md, and a record directory under
`docs/plans/results/` with artifacts (counts, agreement statistics, verdicts, shas, provenance marked
citable), RESULTS, TEST-DATA.md and REPORT.md. Nothing public contains paper text, record or paper ids,
question text, rater identities or hosts.

## 12. What this can and cannot do

**Can** (v1 §8, kept): motivate a judge change for a future study under its own frozen plan; serve as an
exploratory re-judging sensitivity, printed beside pre-registered results and never in their place; give a
measured false-yes rate on near-miss pairs for the limitations.

**Can** (new in v2): give a validity verdict per judge and scheme (§9.1); license paper-b figures under
§9.2.

**Cannot:** change the main run's judge, prompt, rule, tiebreaker, drift lines or any final label (the
main run is complete; there is nothing to gate); remove the §13.5 flag from any alias-study result or
paper-c figure; validate labels beyond the judge configurations and the population it tested.

It reads no main-run label and writes no collection. v1 Q4 (schedule) was answered by events: the main
run's judging ended on 2026-11-05 and nothing else runs on the judge endpoints.

## 13. Open questions for the owner (v2)

| # | question | recommendation |
|---|---|---|
| Q1 | How many raters (R), and who? | R = 4: confirm three raters besides the owner. If fewer, the §6.3 table applies; R = 2 reduces A to 80 |
| Q2 | Are the thresholds and margins right? | α cut-offs 0.800 / 0.667; Δ margins −0.05 (point) and −0.10 (lower bound) |
| Q3 | Does §8.3 (c), the panel in the owner's place, fit the accepted Q8 "owner condition"? | yes; the owner-alone figure is printed beside it either way |
| Q4 | Confirm `/3`'s six synthetic examples and its rule that naming the topic is *none*, not *partial* (JUDGE-3-PROMPT.md §3, item 3) | yes to both; the lenient collapse would otherwise readmit the pilot's false yeses |
| Q5 | Which budget pays for the panel candidates? | the model gateway's own key; both counted against the 20,000 ceiling in §10 either way |
| Q6 | Add reach-rechunk's adopted-chunking (S) collections as extra read-only cells? | yes, if still loaded; sizes amended before freeze |
| Q7 | What is paper-b's scope under §9.2? | the panel licenses OA-1000 judged figures only; HR-dev remains the CDS blocker; re-scoping paper-b is the owner's decision |
| Q8 | Set-level sufficiency? | a later plan |
| Qa | May unpinned ALCF models be decision judges? | **answered** (Amendment 2): yes, with the backend header, the response `model` field and the date recorded per call, and a revision request to ALCF support |
| Qb | Confirm the §4 panel (KX1 Qwen3.8-27B anchor; Opus as the existing reference; KX2 Mistral-Large-3-675B; KX3 Llama-3.1-70B; optional KX4 gpt-oss-120b) | confirm as proposed |
| Qc | A gateway-side route lock (`route_order: [local]`) on the two pinned Qwen records? | optional; the per-call gate of §4 holds without it |

## 14. v1 questions: answers and where they are folded in

| v1 Q | answer (Amendment 1 (5) unless stated) | where |
|---|---|---|
| Q1 | draw from neighbour / random-arm papers if fewer than 30 satisfied | §5.1 |
| Q2 | the verifier's evidence quote stands in for a missing reference answer, for `ans_cov` only | §5.3, §2 |
| Q3 | keep the median / 85th-percentile cuts | §5.3 |
| Q4 | answered by events | §12 |
| Q5 | budget: Amendment 1 (3) | §10 |
| Q6 | `/3` drafted by the agent, synthetic examples: Amendment 1 (4) | §3.2, JUDGE-3-PROMPT.md |
| Q7 | KY = C, E and Argon all say yes | §3 |
| Q8 | Holm over four, 2-point false-no tolerance, owner condition (as the panel condition, §13 Q3) | §8.3 |
| Q9 | no fourth Opus call; the reference stays on `alias-judge/1` | §6.1 |
| Q10 | a second rater and *unsure*: Amendment 1 (1) | §2.3, §6.3 |
| Q11 | 25 full-list records, 600-pair cap | §5.4 |

## Amendments

### Amendment 1 (2026-11-11): owner decisions

Recorded verbatim:

> OWNER DECISIONS 2026-11-11: (1) a second rater is available; more raters if each has fewer questions to
> rate; 'unsure' is allowed. (2) graded labels full / partial / none — "partial allows for disagreement
> between graders". (3) gateway budget approved up to 4x the drafted ~5,000 calls. (4) owner approves a
> draft of alias-judge/3 by the agent. (5) agent defaults accepted for Q1-Q3, Q7-Q9, Q11 (Q1
> neighbour/random-arm papers if <30 eligible; Q2 verifier evidence quote stands in for a missing reference
> answer; Q3 median/85th cuts; Q4 answered by events; Q7 KY = C, E, Argon all yes; Q8 Holm over four,
> 2-point false-no tolerance, owner condition; Q9 no fourth Opus call, reference on alias-judge/1; Q11 25
> full-list records, 600-pair cap). (6) Owner rule: plan with Fable, always review.

Also noted the same day: additional judge models from a separate model gateway were being identified; the
inventory is §4.

### Amendment 2 (2026-11-11): ALCF-hosted models as judges

Owner decision, recorded verbatim:

> all models are acceptable even from ALCF

It answers §13 Qa: unpinned ALCF-hosted models may be decision judges, with the per-call
`X-Model-Router-Backend` value, the response `model` field and the date recorded, and a revision request
to ALCF support. The proposed panel of §4 stays the recommendation; the owner confirms it as §13 Qb.

---

# Appendix: DRAFT v1 (2026-11-04), superseded by v2 above

Kept verbatim (headings demoted one level, text otherwise as committed on `main` at `4146aee9`) so that every v1 specification v2 refers to ("as in v1 §3.1") stays in this file. v1 is superseded in full; where it conflicts with v2, v2 applies.

## Alias study: a second judge validation on retrieval-like pairs (draft plan, not approved, not run)

Status: **DRAFT, written 2026-11-04; not approved, not frozen, not run** (as of 2026-11-11). No record
has been drafted, no pair built, no label requested, `calib/validation-2/` does not exist and
`alias-judge/3` is not written. The eleven open questions of §9 are unanswered. If the owner takes the
plan up, the answers are recorded as a dated amendment below, and only the commit that records them
freezes the plan; every output would then record that commit's sha. Until then the text below is kept
as drafted on 2026-11-04, except where a dated note says otherwise.

**What changed since 2026-11-04 (status note, 2026-11-11).** The main run completed on 2026-11-05
([RESULTS.md](../results/alias-study-2026-11/RESULTS.md)) with the judge of prereg §6.3 unchanged. Its
owner spot check (prereg §13.5, 100 pairs) agreed with the final label on 81 of 100: 0.872 re-weighted
and 0.82 on the *answers* stratum, below the 0.90 rule, so every result carries *judge validity below
threshold (§13.5)*. The owner's post-hoc tags put all 19 disagreements on the partial-versus-full line
(12 partial, 7 full; 13 of the 19 judge-*answers* / owner-*not*). The Opus drift reference ended at 0.948
(386 / 407) in stage A and 0.960 (1,122 / 1,169) cumulative against the 0.97 line, report-only under R29;
the arm gap never fired. The same frozen judge was then reused, unchanged, by the confirmatory boundary
test ([alias-boundary-confirm-2026-11](../results/alias-boundary-confirm-2026-11/RESULTS.md): Opus on
18 pairs per arm, 16 / 18 and 15 / 18), rechunk stages 1 and 1b
([rechunk-found10-2026-11](../results/rechunk-found10-2026-11/README.md)), the reach comparison
([reach-rechunk-2026-11](../results/reach-rechunk-2026-11/RESULTS.md): re-judged drift sample of 555
pairs, agreement 0.9712 overall and 0.9327 on *answers* labels) and split-answer step 1
([split-answer-2026-11](../results/split-answer-2026-11/RESULTS.md): 22 Opus pairs, drift not
evaluable; 35 of 150 stage-A *not* pairs became *answers* when neighbours were added). None of these
ran a second validation or changed the judge. The spot-check result, not the pilot's 12-pair owner read
of §1, is now the motivating evidence; §1 is kept as written. The control plan's design note
([CONTROL-PLAN.md §9](../results/alias-study-2026-11/control/CONTROL-PLAN.md)) proposes graded
*full / partial / none* labels and set-level sufficiency for the next pre-registration; K3's binary
"a partial answer is *not*" sharpening (§2) is one option beside that, not the owner's stated
direction. Open question 4 (schedule) is answered by events: the main run's judging has ended.

Background: [JUDGE-VALIDATION-PLAN.md](JUDGE-VALIDATION-PLAN.md) (the first validation, frozen at
`e2713e1`), [JUDGE-CALIBRATION.md](JUDGE-CALIBRATION.md) (rounds 1–3, validation, tiebreaker, review
follow-ups), [TIEBREAKER-PLAN.md](TIEBREAKER-PLAN.md), and [the pre-registration](../results/alias-study-2026-11/prereg/PREREG-alias-study.md) §6.1–6.6, §13.5 and §14 (O8, O12).

**This set is not a gate on the main run.** The owner decided (2026-11-04) that the main study proceeds
with the current judge unchanged; it did, and completed on 2026-11-05. This set is for a later comparison
and refinement round. §8 states what its
result can and cannot change.

### 1. Why a second validation

#### What the pre-freeze pilot found (counts only)

The pilot ran the composed rule of prereg §6.3 on the 191 held-out records. Final label: C
(`Qwen3.6-35B-A3B`, majority of 5) and E (`Qwen3.8-27B`, n = 1) agree, otherwise the `Gemini 4 Argon`
tiebreaker decides.

| finding | value |
|---|---|
| final label vs the Opus drift reference | 157 / 166 (**0.946**), below the 0.97 line (an alarm when the pilot ran; a reported reference line since R29, owner 2026-11-04) |
| per arm (base / neighbour / random) | 0.944 / 0.946 / 0.946 |
| the same rule on the first validation's constructed pairs | 0.99 |
| disagreements that are final *answers*, Opus *not* | 7 of 9; in 6 of the 7, C and E agreed (no escalation) |
| kind of passage in all 9 disagreements | partial answer: the passage names the method, test or topic the question is about but does not state the asked-for information (its purpose, value, result, definition or condition) |
| disagreements about alias resolution | 0 of 9 |
| escalation (tiebreak) rate | 6.0 % overall, 8.1 % on the stage-A legs; the validation had 2.5 % |
| Argon re-judging the 37 drift pairs whose final label is *answers* | rejects 5 of the 7 Opus-*not*; keeps 27 of the 30 Opus-*answers* |
| owner, blind, on 12 of these hard pairs | agrees with Opus 8 / 12, with the final label 5 / 12, with Argon 4 / 10 (the 10 Argon labelled) |
| owner on the 7 false yeses | confirms 5 of 7 as *not* |

Rows quoting the 37 Argon re-judged pairs and the per-pair owner counts are private counts (`judge-summary.json`, the owner's blind sheet); the public record carries the 8 / 12, 5 / 12 and 4 / 10 figures and the three Argon-rejected pairs (prereg §5.6).

#### The weakness of the first validation set

The first set was **constructed**: gold, BM25 lexical neighbours and alias collisions. It tested alias
resolution well. It held few **topical near-misses**, which are what real retrieval mostly produces: a
chunk on the same topic or method that does not state the answer. Prereg §6.4 already said the
calibration pairs "are not the retriever outputs judged here". The pilot shows the cost: every error was
a near-miss and none was an alias error. This set takes its pairs from real retrieval over the study's
corpora and oversamples near-misses.

### 2. Candidates under comparison

| id | candidate | prompt | label |
|---|---|---|---|
| **K0** | current rule: C + E, Argon tiebreak (prereg §6.3, unchanged settings) | `alias-judge/2` | C and E agree → that label; else Argon |
| **K3** | the same rule with a new prompt | `alias-judge/3` for C, E and Argon | as K0 |
| **KA** | Argon alone | `alias-judge/2` | Argon's label |
| **KS** | `GPT-6.1 Sol` alone (prereg §14 O8, owner 2026-11-04: the candidate for this round; its allow-list entry and served id are confirmed before any call, as TIEBREAKER-PLAN.md §1 did for `GPT-5.6 Sol`, which scored 0.990 on the first validation set) | `alias-judge/2` | Sol's label |
| **KY** | "yes needs a third yes" | `alias-judge/2` | *answers* only when C, E **and** Argon all say yes; otherwise *not* |
| **KO** | Opus, single call | `alias-judge/1` | repetition 1 of the reference (§4); scored on the owner subset only |

Settings are the prereg's: thinking on for C and E, `max_tokens` 8192 (local) and 4096 (Argon, Sol),
served-model check, `fallback_model: ""`. E is requested with n = 3, as the first validation did; the rule reads sample 0, an n = 1 draw, which is the prereg's setting, and samples 1–2 are reported for information only. KY reuses KA's labels;
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

### 3. The set

#### 3.1 Records (fresh, out of sample)

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

#### 3.2 Candidate pool (real retrieval)

Each record is retrieved, read-only, in the **15 cells** of the 5 Layer-1 attachments (base × none,
base × merged, neighbour × none, random-1 × none, random-2 × none) × 3 retrievers (default, norerank,
BM25), with the study's pinned retrieval config and collections. No collection is written. No main-run
result is read.

The pool is the main run's label demand (prereg §6.2), applied to these records: in a cell where the
gold is outside the top 10, every non-gold top-10 chunk; in a cell where the gold is in the top 10,
every non-gold chunk ranked **above** it. Pairs (record, chunk) are deduplicated across cells. Each
pair keeps its cells, best rank, arm (base / neighbour / random, as prereg §6.2) and a same-paper flag.

#### 3.3 Strata (assigned without any judge)

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

#### 3.4 The labelled pairs

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
| clear answer | 100 | about 83 |
| clear non-answer | 100 | about 83 |

The expected column assumes the first validation's loss: 83 no-reference and 17 split of 587 (17 %). A
stratum whose frame holds fewer pairs is taken whole and the power it reaches is reported. It is not
extended unless a dated amendment is written before any label. The pair file is frozen once built and
its sha256 is pinned in every later config (`pairs_sha256`). The total is at least 710 pairs. F's surplus
in over-filled strata can raise it, up to about 1,000. How many deduplicated pairs one record yields is
not known yet (open question 11). The cost table uses 850.

### 4. References

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

### 5. Metrics

#### 5.1 Per candidate

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

#### 5.2 Comparison with the current rule

The **primary comparison** is on the near-miss stratum, against Opus-unanimous: each of K3, KA, KS and KY
vs K0, paired, by a one-sided exact McNemar test with Holm adjustment over the four. A candidate is called **better
than K0** when (a) its near-miss accuracy is higher with adjusted p < 0.05, (b) its weighted false-no rate
is no more than 2 points above K0's at the point estimate, and (c) its owner-subset agreement (A) is not
lower than K0's at the point estimate. Where the Opus-based and owner-based rankings disagree, both are
printed, and the owner ranking is the one quoted (§4, arbiter). Everything else is descriptive.

#### 5.3 Implied inflation of Hit@10

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

### 6. Size, power and cost

**Power.** The near-miss stratum is sized to tell a judge with true agreement 0.98 from one at 0.95.
One-sided α 0.05, power 0.80, normal approximation: n = 253 Opus-unanimous pairs (two-sided: 330). The
paired test of §5.2 (one-sided) depends on the discordance, which is a guess here (0.05). At that value a
3-point difference needs about 340 pairs at α 0.05 and about 525 at the strictest Holm level (0.0125); a
4-point difference needs about 295 at 0.0125. The 360 drawn (about 300 unanimous) meet the one-sample
target and the 4-point paired target, not the 3-point one. A committed script computes exact figures
before drafting (`artifacts/validation2_power.json`). If the frame is smaller, the reached power is printed.

**Gateway cost (estimate; approval needed, open question 5).** Per-call token figures are the measured ones:
Opus about 1.3 k prompt tokens, Argon about 0.9 k, Sol about 0.9 k as measured for GPT-5.6 Sol (GPT-6.1 Sol not yet measured). `alias-judge/3` adds the length of its examples
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

### 7. Order of work, freezing and outputs

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

### 8. What the result can and cannot change

**Can:** motivate a judge change (prompt, rule or model) for a **future** study, under its own frozen plan;
motivate a **re-judging sensitivity** of this study's committed pairs, declared **exploratory**, run after
the main analysis is committed and printed beside the pre-registered results, never in their place; and
give a measured near-miss false-yes rate for the limitations (prereg §13.3).

**Cannot:** change the main run's judge, prompt, rule, tiebreaker, drift reference lines, arm-gap pause or
any final label (the main run is complete, so there is nothing to gate, pause or delay); or be read as a
pass or fail of the current judge (there is no pass criterion; §5.2 is a comparison).

This run reads no main-run label or result and writes no collection. It would share the gateway budget
and the local endpoints with whatever study is running at the time (open question 4).

### 9. Open questions for the owner

1. **Eligible papers.** Are enough OA-1000 papers left after the exclusion to satisfy 30? If not, may
   records come from neighbour or random-arm papers, which calibration never used?
2. **Reference answer.** Does every accepted record carry one for `ans_cov`? If not, what stands in (the
   verifier's evidence quote, or the gold chunk)?
3. **Strata.** Are the median and 85th-percentile cuts acceptable, or is a Gemini 2.5 Pro stratifier preferred?
4. **Schedule.** The main run's judging ended 2026-11-05. Is any other study running whose 4-in-flight
   cap, budget or local endpoints this run must not share?
5. **Budget.** Is about 5,000 gateway calls (5.6 M prompt, 0.8 M completion tokens) approved? Drop Sol to
   save about 850?
6. **`alias-judge/3`.** Does the owner write it, or approve a draft? Are synthetic examples acceptable?
7. **KY.** Is "C, E and Argon all say yes" the intended reading?
8. **"Better".** Are Holm over four, the 2-point false-no tolerance and the owner condition right (§5.2)?
9. **Opus.** Add a fourth Opus call per pair (about 850 calls) to score a single call on the full set? Keep
   the reference on `alias-judge/1`, or also run `/2` on a subset?
10. **Owner labels.** Is *unsure* allowed? Is a second rater available for this round? (Prereg §14 O12,
    owner 2026-11-04: a single rater for the main study; further raters planned for a second wave.)
11. **F size.** Are 25 full-list records and the 600-pair cap right, given that pairs per record are
    unknown until retrieval?

### Amendments

(none)
