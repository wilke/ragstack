# Alias study: a second judge validation on retrieval-like pairs, with graded labels (DRAFT v2, not frozen, not run)

Status: **DRAFT v2**, written 2026-11-11. It supersedes DRAFT v1 (2026-11-04), which is kept verbatim as
the appendix at the end of this file so that nothing it specified is lost; where v2 says "as in v1", the
text is in that appendix. Nothing in this plan has been run. No label, pair file or rater sheet exists yet.
v1 said the plan would freeze once the owner answered its questions. That has changed. v2 freezes at the
commit that records four things: (a) the owner approves this text and the prompt in
[JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md), (b) the owner answers §13, (c) §4's panel is confirmed
(§13 Qb), and (d) the two pinned configuration fingerprints and the hosted partial sets are re-read from
the gateway's configuration endpoint and confirmed against the expected values of §4 (the gateway's
configuration record and fingerprint went live on 2026-11-12; §13 Qe answered, Amendment 5). It also
records the pre-freeze results that §11 step 4 lists (the `/3` system sha, the self-check counts, the
canaries). Every output records the sha of that commit. Any change after it is a dated amendment at the end of the v2 text
(before the appendix).

Owner rule (Amendment 1 (6)): plans are made or checked with Fable and always reviewed. This draft was
written by an Argo Opus agent and checked, corrected and finished by Fable on 2026-11-11; the list of
corrections is in the PR that merged it.

## What v2 changes, and why

| topic | v1 | v2 | why |
|---|---|---|---|
| label scale | binary *answers* / *not* | graded **full / partial / none** for one (question, passage) pair (§2) | Amendment 1 (2); the spot check could not give credit to partial evidence |
| human reference | owner alone, 100 pairs | a panel of R raters (recommended 5, minimum 2; the owner is one of them). Each rater grades at most 80 items: a shared core plus overlapping blocks (§6.3) | Amendment 1 (1) |
| "unsure" | not allowed | allowed, with a reason code, treated as missing (§2.3) | Amendment 1 (1) |
| inter-rater agreement | none (one rater) | Krippendorff's α (ordinal) is primary; weighted κ and AC2 beside it (§7) | more than one rater |
| disagreement | n/a | no forced adjudication; the distribution of panel grades is reported (§7.3) | "partial allows for disagreement between graders" |
| judge vs human | accuracy against the owner | agreement with the panel compared with agreement between humans, plus confusion matrices and collapses (§8.1) | a reference made of several raters |
| validity reading | the 0.90 binary rule (alias-study prereg §13.5) | a bar relative to human agreement, per collapse scheme, with a complete outcome map (§9). The §13.5 rule itself is untouched | §9 |
| what paper-b may cite | not addressed | a stated licence (§9.2) | owner request |
| alias-judge/3 | "/2 with sharper *not* criteria", drafted from 12 pilot pairs | a graded prompt drafted by the agent from public repository text only; owner approves it ([JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md)) | Amendment 1 (4) |
| candidates | K0, K3, KA, KS, KY, KO | adds graded KA3 and KS3, and a candidate panel from the model gateway (§4) | Amendment 1 (3), (4); Amendment 2 |
| frames | near-miss / partial / clear answer / clear non-answer | near-miss / mid-coverage / high-coverage / off-topic. Only the names change | "partial" is now a label |
| budget | about 5,000 calls | about 7,600 calls on the study gateway plus about 6,600–8,800 on the model gateway at 850 pairs; the ceiling is 20,000 in all, C and E excluded (§10, §13 Q5), held by a pre-specified drop order (§10) | Amendment 1 (3) |
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

Rows quoting the 37 Argon re-judged pairs and the per-pair owner counts are private counts
(`judge-summary.json`, the owner's blind sheet); the public record carries the 8/12, 5/12 and 4/10 figures
and the three Argon-rejected pairs (prereg §5.6).

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
*Labels* and *Deciding* blocks of `alias-judge/3` (JUDGE-3-PROMPT.md §1.2), reproduced on the rater sheet
**together with the prompt's six synthetic examples**, so that no rater sees less of the construct than the
judges do and than the owner (r1), who approved the examples, already has. If the two texts ever differ,
the committed prompt wins and this section is corrected by amendment.

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
  missing). `/3` replaces `/2`'s *yes* and *no* bullets with the blocks above; that phrase, inside the *yes*
  bullet, is the line the owner and the judges drew in different places (CONTROL-PLAN §9), and `/3` has no
  sentence like it.
* The owner's tag *other answer* (RESULTS.md) has no label of its own: a passage that answers under the
  question's conditions is *full* whatever its answer; one whose conditions contradict the question's is
  *none*.

### 2.3 Unsure (raters only)

* A rater uses *unsure* only when the pair cannot be graded. A reason code is required: *meaning* (the
  question is ambiguous as written), *text* (the passage is garbled or cut off), *question* (the question
  looks malformed or unanswerable as written), or *expertise* (outside the rater's competence).
* Doubt between two adjacent grades is not *unsure*. Step 4 sends doubt about completeness to *partial*,
  and disagreement there is expected ("partial allows for disagreement between graders"). Doubt between
  *partial* and *none* goes back to step 2: if the rater cannot point to words in the passage that state or
  let a reader work out a part, the grade is *none*.
* Because *partial* then holds both "known incomplete" and "not sure it is complete", the sheet has an
  optional **doubt (full / partial)** tick box beside the grade. It changes no grade; its rate is reported
  per rater and per frame, and α is also reported with ticked items removed.
* *Unsure* counts as missing. Items that end up short of grades are topped up (§6.3). *Unsure* counts are
  reported per rater and per reason code. A rater with more than 10 % *unsure* is flagged (no grade is
  changed), and α is also reported on the items that no rater marked *unsure*, so that skipped hard items
  cannot raise α unseen. An item that two or more raters mark *meaning* or *question* is excluded from all
  judge scoring (§8) and counted.
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
| KX1–KX4 | the §4 panel | `/2` and `/3` | no (outside the §8.3 test); decision judges under §9 |

* The confirmatory family is K3, KA, KS and KY against K0, with Holm over four (Q8 accepted). **Only
  these four can be called "better than K0"** (§8.3); the family was fixed by v1 before any candidate from
  the model gateway existed, and it is not widened.
* **Decision judge** means: a judge configuration that receives the §9.1 verdict, can carry the §9.2
  licence, and can be recommended as a future judge (§12). **Every judge in this table is a decision
  judge**, the §4 panel included (Amendment 2). "Descriptive" in this table refers only to the §8.3 test.
* **Judge C's configuration is not permanently served.** The inventory (§4) records C's model as not
  permanently served, and a local id that runs the same model ends on 2026-11-30 (that id is not C,
  Amendment 3). C's availability is confirmed by the model-gateway maintainer before the freeze and
  recorded in the freeze commit (§13 Qd). K0, K3, KY and KC3 all need C. Therefore C's `/2` and `/3`
  labelling of the frozen pair file runs **first**, before any other judge (§11, step 7), and the schedule
  is set so that it completes while C's configuration is available. **C is a configuration, not a
  location** (Amendment 4): its pin is the configuration fingerprint of §4 (model repository or id,
  weights snapshot or revision, quantisation, serving engine and version, and the serving flags). If C's
  serving stops before its labelling completes: a re-served C with the **identical configuration**
  (the same fingerprint, wherever and whenever it runs) counts as C, with a provenance note; any other
  configuration (a different snapshot, quantisation, engine version or flag) is a different judge (KC′),
  and then K0 cannot be claimed as the main run's rule. Without K0 the §8.3 comparison is **not run** and
  is reported as such; the §9.1 verdicts for every judge that did label (Opus, KA, KA3, KS, KS3, KE3, the
  panel) still stand. Nothing is re-labelled to fit.
* **Every judge is reached by model id** (Amendment 4). C, E, KX1 and the hosted candidates are called
  through the model gateway by their model ids; Opus, Argon and Sol are called through the study gateway
  by their model names, as in v1 and the main run (that path is unchanged; it never used a direct
  address). No direct address or port of any model appears in this plan, in the prompt, or in any
  study config: the two gateways are the only addresses the harness knows, and the private config holds
  them.
* Settings are v1's (appendix §2): thinking on for C and E; `max_tokens` 8192 (C, E, KX1) and 4096
  (Argon, Sol, the hosted candidates); temperature omitted on the study gateway, T 0.7 for C and E (as
  the main run); E requested with n = 3 and read at sample 0 (samples 1–2 reported for information);
  `fallback_model: ""` on the study gateway and fallback off on the model gateway; a served-model mismatch
  on the study gateway stops the run, and a configuration mismatch on the model gateway is handled by the
  §4 table.
* **Sampling is pinned for every local judge and sent on every request, never left to defaults** (the
  §4 fingerprint does not cover sampling defaults or the chat template). For C, E and KX1 the harness
  sends, on every request: thinking on, `max_tokens` 8192, temperature 0.7 (KX1 runs as E), `top_p` and
  `top_k`, and `n` (5 for C, 3 for E, 1 for KX1). The main run sent temperature only and took `top_p`
  and `top_k` from the engine's defaults, so the values pinned here are **the engine's effective defaults
  for the two pinned configurations as the maintainer reads them before the freeze**, written into the
  config and the freeze commit; K0 then runs under the same effective settings as the main run. If they
  cannot be established, the harness sends the model's published thinking-mode values and the freeze
  commit declares that K0's sampling may differ from the main run's. Every stored label carries the
  values as sent (JUDGE-3-PROMPT.md §2). The hosted candidates get `max_tokens` 4096 and no temperature
  (§4), because their defaults are not pinnable and the canaries are their change detector.
* **Changed from v1:** K3's tiebreak reads Argon `/3` from the KA3 run, which labels every pair, so no
  separate escalation calls are made. K3's escalation rate (C and E not equal, or either without a label)
  is expected to exceed K0's with three classes. It is reported.

### 3.2 alias-judge/3 (Amendment 1 (4))

The prompt, its parsing contract and its rationale are in [JUDGE-3-PROMPT.md](JUDGE-3-PROMPT.md). What
this plan fixes about it:

* It is committed in the harness (`JUDGE_SYSTEMS["alias-judge/3"]`), with its system-prompt sha recorded in
  the freeze commit (§11 step 4), **before the freeze and before any record of set W is drafted**.
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
* Before the freeze (§11 step 3), the live self-check of JUDGE-3-PROMPT.md §4 (synthetic cases with
  owner-set expected labels) runs once per `/3` judge (5 times per case on each hosted candidate, for the
  canary selection), and its counts go into the freeze commit beside the system sha.

## 4. Candidate judges (from the model gateway inventory, 2026-11-11)

Source: the inventory of the **model gateway** made by the vllm session on 2026-11-11 and verified by it
from the gateway's status and model listings, read with the study's service key. **No model call was
made.** The operational details of that inventory (account limits, observed availability, catalogue ids
not adopted here, instance names, the host it was read from) live in the private inventory note beside
the config, not in this file. This gateway is distinct from the study gateway that serves Opus, Argon and
Sol (§6, §10); the two have separate budgets and separate caps.

**The gateway.** OpenAI-compatible, with a service key labelled for this study. It is the only path to
C, E, KX1 and the hosted candidates (Amendment 4): every call names a model id, and the gateway decides
where it is served. A request option switches fallback off, after which an unavailable model returns an
error rather than another model. It logs labels, model ids and timing, and no text.

**Identity = configuration, not location** (Amendment 4). A judge is a configuration: the model
repository or id, the weights snapshot or revision, the quantisation, the serving engine and its version,
and the serving flags that change outputs (context length, the default chat-template arguments including
the default thinking setting, the reasoning parser). Where that configuration runs, under which instance
name, and when it was started are not part of its identity. **A move or restart with an identical
configuration is not a mismatch.** The gateway is the source of this information (live since 2026-11-12,
Amendment 5): it keeps a **configuration record** per model, of two kinds held apart, *declared* (its own
record of the repository, revision, quantisation, engine, context and settings) and, for a local backend,
*observed* (read from the running engine without a model call: served repository, context length, engine
version, KV-cache dtype, start time), lists every field on which the engine contradicts the record, and
derives a **configuration fingerprint**: a hash over the declared and observed fields that excludes the
backend's name, its address and the start time, so that a move or restart with the same setup keeps it.
The fingerprint is **full** when the engine could be read and **partial** otherwise (always for a hosted
id, where it covers the route, the serving backend's name and the model id only; for a local id, a
partial value means the engine could not be read at that moment); the two kinds are distinguishable by
their prefix. The same value comes back as the per-response configuration value on every forwarded
answer; a gateway-written error carries none. It is read from the gateway's configuration endpoint with
the study's key; the concrete names of the endpoint and the value live in the private config. **Stated
limits:** the fingerprint does not cover the chat template, the reasoning parser or the sampling defaults
(speed-only options are deliberately excluded too). Sampling is therefore pinned by the harness and sent
on every request (thinking, `max_tokens`, temperature, `top_p`, `top_k`, `n`; §3) and stored per label as
sent (JUDGE-3-PROMPT.md §2). The chat template and the reasoning parser cannot be sent per request; for
the local judges they rest on the maintainer's promise not to change C's or KX1's configuration without
the owner's go (§13 Qd), **and the canary cases run on the local judges too** (below), so that a template
or parser change, or a drift in a default the harness does not send, shows up as a canary flip even when
the fingerprint still matches. The snapshot hash cannot be observed through the engine, so it is
declared, and is only as good as the gateway's record. A non-empty contradiction list is a mismatch here.
For hosted (ALCF) ids anything beyond the partial value must come from ALCF support.

**Pinned configurations and expected fingerprints** (read from the gateway's configuration endpoint on
2026-11-12 with the study's service key, no model call, no contradictions listed; a private copy of the
reading is kept):

| judge | model id | declared | observed | expected full fingerprint |
|---|---|---|---|---|
| C | `Qwen3.6-35B-A3B` | repo `Qwen/Qwen3.6-35B-A3B`, revision `995ad96e…`, vLLM, context 131072 | the same repo, context 131072, engine 0.31.0, cache fp8 | `v1:c94d715990c85f58` |
| E and KX1 | `Qwen3.8-27B` | repo `Qwen/Qwen3.8-27B-FP8`, revision `017b9c7a…`, fp8, vLLM, context 131072 | the same repo, engine 0.31.0, fp8 | `v1:76874a40425f4e01` |

These are the **expected values**; the freeze commit re-reads both from the endpoint and confirms them
(§11 step 4). Each pin is that full fingerprint: the model id is the handle, the fingerprint is the
identity. Today each is served by one local backend and fallback off keeps a request on the first route
of the model's route order (there is no per-request route preference); that is recorded in the private
inventory note and plays no part in the gate. Hosted partial values read the same day: Mistral-Large-3
`v1-partial:1b3b761acf47e93c`, Llama-3.1-70B `v1-partial:806c83f705876ae0`, gpt-oss-120b
`v1-partial:354bfb398ce854ba` and `v1-partial:32ef49512f6c83ac` (two clusters). They are the expected
hosted sets; the sets actually used are the ones seen in the pre-freeze self-check (§11 step 3), and
gpt-oss-120b's set may hold both clusters if both are seen then.

**Mismatch rules, one per judge class** (code item, §11). A *mismatch* is: the response `model` field not
in the accepted set for that judge, or the per-response configuration value absent or wrong for that
judge: for a local judge (C, E, KX1) the answer must carry **exactly its pinned full fingerprint**, and
any **different full value** is a mismatch at once; for a hosted judge the partial value must be **in the
set recorded for that candidate in the freeze commit**. **A missing value is a mismatch** on any stored
reply. **A serving outage is not a mismatch**: an error with fallback off returns no reply (and a
gateway-written error carries no configuration value), nothing is stored, and labelling resumes when the
configuration is served again, under the §3 re-serve rule (the same fingerprint continues the batch;
anything else is a different judge). **A transient partial value on a local judge is an outage, not an
identity failure**: a partial value means the gateway could not read the engine at that moment (§4
above). The reply that carried it is not stored, the harness pauses the batch and re-reads the
fingerprint from the gateway's configuration endpoint **up to 5 times, one minute apart**; if a read
returns the pinned full value the batch resumes and the paused pair is re-issued (a re-read, not a
re-issue of a stored row, so the no-re-issue rule is untouched); if all 5 reads return a partial value,
or any read returns a different full value, it is a mismatch and the batch stops. The same rule applies
to the batch start and end reads below. Every pause, its reads and its outcome are logged and counted. The interim gate that §13 Qe once offered (instance name in the fingerprint's place) is
not needed and is not used; its text is kept in Amendment 4 as history only.

| judge class | judges | accepted `model` | identity gate | on a mismatch |
|---|---|---|---|---|
| study gateway | Opus, Argon, Sol | the requested name and its versioned id (TIEBREAKER-PLAN.md §1) | the served-model check only (no configuration record exists there) | the run **stops** and the reply is not stored (prereg §6.3, v1 §6) |
| model gateway, pinned configuration | C, E, KX1 (and any local candidate) | the pinned id | the full configuration fingerprint equals the pin | the reply is not stored and the **batch stops**; the only remedy is a dated amendment with its own budget line and the owner's approval (no re-issue within this plan, see below); the void is counted |
| model gateway, hosted (ALCF) | KX2–KX4 | the requested id (no revision pin exists) | the partial fingerprint (route, serving backend's name, model id) is in the **set recorded for that candidate in the freeze commit** from the pre-freeze self-check (§11 step 3); a model served on two clusters (gpt-oss-120b today) has both values in the set only if both were seen then, and the cluster is recorded per row | the reply is not stored and **that candidate stops** (a cluster outside the set is a mismatch); its rows up to the last passing check are kept and dated; the other judges continue |

**Per call, every row records:** the configuration fingerprint the gateway reports (full or partial), the
response `model` field, the call date, and (private, never in a public file) the instance or backend that
served it. The private record is for tracing; the public record carries fingerprints and dates.

**Configuration and canary checks.** A *batch* is one model × one prompt over the frozen pair file (or
over H for repetitions). The fingerprint of a pinned configuration is read from the gateway at the start
and end of every batch of that model and compared with the pin, besides the per-call comparison above
(a partial value at either read follows the transient rule above: up to 5 re-reads a minute apart before
it counts); a failed end check **voids that batch's rows since the last passing check** (the void is
counted; nothing is re-issued within this plan, see the last bullet below). A restart or move between
the two reads with the same fingerprint is reported (the start time changes) and voids nothing. Hosted
models have no revision pin, so neither the `model` field nor the partial fingerprint can catch a weight
change; and for the local judges the fingerprint does not cover the chat template, the reasoning parser
or any default the harness does not send. **Every model-gateway judge's batch** (C, E, KX1 and the
hosted candidates) therefore starts and ends with **10 fixed synthetic canary cases**: the change detector
for hosted ids, and the template-and-defaults detector for the local judges, run as follows:

* the cases are a subset of the JUDGE-3-PROMPT.md §4 self-check cases with owner-set labels, committed
  before the freeze; they are always sent with **`/3`**, whatever prompt the batch runs, so one graded
  construct is tracked across every batch;
* **selection rule**: before the freeze, the live self-check runs every case **5 times** on each
  model-gateway judge (hosted and local) under that judge's batch settings (JUDGE-3-PROMPT.md §4; costed
  in §10). The 10 canaries are the cases whose label was identical in all 5 runs on every model-gateway
  judge, taken in the committed case order; if fewer than 10 qualify, the remaining places go to the cases
  with the most consistent runs, and each chosen case's measured flip rate is recorded in the freeze
  commit;
* they run at **temperature 0 with a fixed seed** where the serving configuration accepts those parameters
  (recorded per call); where it does not, its default sampling is used and the tolerance below applies;
* **void rule**: a batch is voided when its end canary differs from its start canary on **3 or more of the
  10 cases**, or when the end canary's agreement with the owner-set labels is **3 or more cases lower** than
  the start canary's (the second clause is implied by the first and is kept for readability). **Stated
  false-void rate under default sampling**: if each case flips between two runs with probability q, the
  chance that 3 or more of 10 differ is about 1 % per batch at q = 0.05, about 7 % at q = 0.10 (about 48 %
  over nine hosted batches) and about 32 % at q = 0.20; the selection rule above is what keeps q low, and
  the measured q is reported beside every void. Under deterministic settings a single differing case is
  reported and does not void. On a local judge, a canary void with a matching fingerprint is reported as
  **template, parser or default drift** and goes to the maintainer and the owner (§13 Qd) before any later
  batch of that judge runs; the void itself follows the same rules as a hosted void;
* **chaining**: each batch's start canary is also compared with the previous batch's end canary for the
  same candidate; a difference on 3 or more cases marks a **canary epoch boundary**, which voids nothing
  (no row was mid-batch) but tags every later row with a new epoch, and the candidate is reported per epoch
  if more than one exists;
* **no re-issue**: a voided batch is **not** re-issued within this plan (a batch is 850–1,000 calls, and
  neither the 110 % cap nor the 20,000 ceiling has room for a second one). The candidate is **reported up
  to its last passing batch**: the batches before the void stand, the voided batch's rows are counted and
  excluded, and any later batch of that candidate is not run. A re-run is a new dated amendment with its
  own budget line and the owner's approval. The same rule applies to a pinned-configuration batch voided
  by the fingerprint check.

Canary calls are in the budget (§10). **Open items:** the two pinned fingerprints are re-read from the
gateway's configuration endpoint and confirmed against the expected values above in the freeze commit
(§13 Qe is answered: the record and fingerprint are live, Amendment 5); a gateway-side route lock on the
two pinned configurations (§13 Qc) is optional and, under Amendment 4, no longer carries identity.

**Hosted (ALCF) models**: a catalogue of hosted ids with no revision pins, reached through a shared
rate-limited account; a model that is not running needs a long cold start at a cost, so only models
reported *running* at the time of the read are proposed. The catalogue size, the account limits, the
availability observed during the inventory and the other running chat models it listed are in the private
inventory note. The four proposed judges below were all listed and reported *running*.

**Not recommended**: a local id whose name does not match the model it serves (it serves Qwen3.6, judge
C's model, and ends 2026-11-30, §3); small models; domain fine-tunes; ids whose provenance the inventory
could not establish; anything needing a cold start, as a main judge. The ids concerned are in the private
note.

**Hosted models as decision judges.** Owner decision, 2026-11-11 (Amendment 2): "all models are acceptable
even from ALCF". Under §3's definition the hosted candidates are **decision judges**: they receive the §9.1
verdict, can carry the §9.2 licence and can be recommended as a future judge. Conditions this plan
attaches: every row records the partial configuration fingerprint the gateway reports (route, backend
name, model id; Amendment 5), the response `model` field and the call date, and privately the serving
instance (Amendment 4); a revision request goes to ALCF support before the freeze, and its answer (a
revision, or "none available") is recorded in the freeze commit **beside** the partial fingerprint (the
gateway computes that value; the study cannot add to it); the mismatch and canary rules above apply. They are outside the §8.3 test only because that family was
fixed by v1.

**Proposed panel** (the agent's suggestion; **OWNER DECISION PENDING**, §13 Qb):

| id | model | reached as | role | thinking |
|---|---|---|---|---|
| KX1 | `Qwen3.8-27B` FP8, pinned configuration | model gateway, by id; full fingerprint | anchor: judge E's configuration behind the gateway's identity gate; measures serving and configuration stability, adds no family | on, as E; `max_tokens` 8192, as E |
| — | `Claude Opus 5.5` | the study gateway, by name | reference family; already in the design as Opus ×3 `/1` and KO (§6). No `/3` call: Q9 declined a fourth Opus call | — |
| KX2 | `Mistral-Large-3-675B-Instruct-2512` | model gateway, by id; hosted (ALCF), partial fingerprint | family diversity (tested live) | recorded if the gateway reports it |
| KX3 | `Meta-Llama-3.1-70B-Instruct` | model gateway, by id; hosted (ALCF), partial fingerprint | family diversity | — |
| KX4 (optional) | `gpt-oss-120b` | model gateway, by id; hosted (ALCF), partial fingerprint | a fourth family, funded only if §10's ceiling allows | recorded |

Per candidate: `/2` and `/3` on all pairs, plus `/3` repetitions 2–3 on H (§6.3; A's after A is drawn, B's
after B is drawn), for self-consistency, and the canary cases above. Settings: `max_tokens` 4096 for the
hosted candidates, with temperature omitted (their defaults are not pinnable; the canaries are their
detector); KX1 runs **as E**, with E's full sampling pin sent on every request (thinking on, `max_tokens`
8192 so that truncation does not masquerade as missing labels, temperature 0.7, `top_p`, `top_k`, n = 1;
§3); fallback off, at most 4 in flight, the model gateway's own size notice and 110 % cap.
The first 20 calls of each candidate are measured for tokens (the size notice), since none of their
tokenizers is calibrated (JUDGE-CALIBRATION.md gives Argon and Opus only). Each candidate is scored under
§8.1, §8.2, §8.4 and §9. Family correlation (each candidate's errors against Opus-unanimous, cross-tabulated
with C, E, Argon and Sol's) is reported.

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

**Raters.** R raters (recommended **5**, fallback **4**, minimum **2**; Amendment 1 (1)). r1 is the owner.
Raters are pseudonymous (r1…rR) and no name is committed.

**H, the items the panel grades:**

* **A**: **150** pairs at the recommended R = 5 (120 at R = 4; the table below gives |A| per R), drawn before
  any model label exists, seed `261103|alias|validation-2|panel`, by frame in the proportions 1/2, 1/4, 1/8,
  1/8 (150: near-miss 75, mid-coverage 37, high-coverage 19, off-topic 19; 120: 60 / 30 / 15 / 15; v1's 80
  at 40/20/10/10). A carries the weighted estimates. |A| is chosen so that the §7.2 precision target is met
  with every rater grading fewer items than v1's single rater (80 against 100): more raters, fewer items
  each (Amendment 1 (1)).
* **B**: 20 pairs, drawn **from the pairs not in A** after the model labels exist (seed `…|panel|B`): up
  to 10 Opus-split pairs, the rest pairs on which at least two of K0, K3 (strict), KA, KS and KY disagree
  under either collapse, in the seeded order. B is reported separately and never weighted.

**Core.** The first 20 A pairs in the order sha256 of `…|panel|core|<frame>|<pair>`, 8 / 6 / 3 / 3 by frame.
**All raters grade the core.** It gives every rater pair the same items for severity profiles and for the
thinned-α check (§7.1).

**Blocks.** The other |A| − 20 A pairs are split into R(R−1)/2 rater-pair blocks, round-robin within each
frame in the seeded order, so that **each non-core item is graded by exactly two raters** and every rater
pair shares one block. B's 20 pairs are split the same way when B is drawn. Each rater's load is
**L = 20 + 2·(|A| − 20 + |B|) / R**, and each rater pair shares 20 + (|A| − 20 + |B|) / (R(R−1)/2) items.

| R | A (by frame) | items per rater | shared by each rater pair (core + block) | grades in total | expected α half-width (ordinal, A) |
|---|---|---|---|---|---|
| 2 | **80** (40 / 20 / 10 / 10) | 100: both raters grade everything (no block structure) | 100 | 200 | about ±0.14 |
| 3 | **100** (50 / 25 / 13 / 12) | about 87 | about 53 | 260 | about ±0.12 |
| 4 (fallback) | 120 (60 / 30 / 15 / 15) | 80 | 40 | 320 | about ±0.11 |
| **5 (recommended)** | **150** (75 / 37 / 19 / 19) | **80** | **35** | **400** | **about ±0.10** |
| 6 | 150 | 70 | 30 | 420 | about ±0.10 |

Block sizes are not whole numbers at every R, so loads vary by one or two items. The half-width column is
the rough figure of §7.2 scaled by 1/√items; the power script replaces it before drafting. **R = 5 with
|A| = 150 is the recommendation**: it meets the §7.2 target (≤ 0.10) at the same 80-item load as R = 4,
and every rater grades fewer items than v1's single rater (80 against 100), which is how Amendment 1 (1) is
honoured (more raters, fewer items each than v1; the load does not fall further from R = 4 to R = 5 because
|A| grows with R to meet the target). If only three raters besides
the owner are available, R = 4 with |A| = 120 is run and its expected ±0.11 is **declared now** as a
limitation of the α band (§7.2); it is not an escape hatch found after the fact. R = 2 is the floor the
owner confirmed: it keeps the rater load at v1's 100 items by shrinking A to v1's 80, and gives no
leave-one-rater-out or without-owner figure (§7.1); those rows are then marked *not computable*. R = 3
shrinks A to 100 so that each rater has fewer items than v1's single rater. The configuration run is stated
in the freeze commit, and the §10 figures are recomputed for it.

**Time and sessions.** The binary spot check took about 1.5 h for 100 pairs (prereg R15). A graded rubric
with parts and conditions is slower: plan on 1–1.5 minutes per item, so about 1.5–2 h per rater at R = 4 or
5, and about 2–2.5 h at R = 2. Each rater's items are split into **sessions of at most 40 items**, in the
rater's seeded order, with a break between sessions; grade shares, *unsure* and the doubt tick are reported
by position tertile within each rater's order, as a fatigue check.

**Sheet.**

* The §2.2 rubric and the six synthetic examples (§2) are printed at the top, then the §2 fields for each
  item.
* The sheet does not show the frame, arm, rank, cell, any model label, any other rater's grade, or whether
  an item is in the core or in B.
* Each rater sees their own order, sha256 of `…|panel|sheet|<rater>|<pair>`.
* Choices: *full* / *partial* / *none* / *unsure* (with a reason code), the optional *doubt (full /
  partial)* tick (§2.3), and an optional private note.

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

**Top-up.** A non-core item of A or B with fewer than 2 valid grades (an *unsure* or an unfinished item)
goes to one more rater, chosen by seeded rotation among the raters who have not seen it. There is one round
per set (A, then B). Items still short are counted as single-rated or unrated, reported, and **excluded from
Δ** (§8.1), since a_HH needs two grades.

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

| α_S (point estimate) | 95 % lower bound | reading |
|---|---|---|
| ≥ 0.800 | ≥ 0.667 | reliable |
| ≥ 0.800 | < 0.667 | **tentative** (the interval reaches the unreliable band) |
| 0.667 to < 0.800 | any | tentative |
| < 0.667 | any | not reliable: the scheme is **not evaluable** (§9.1) |

* The cut-offs are Krippendorff's conventional ones; the source is verified before freeze. The lower-bound
  clause exists because at R = 4 with |A| = 120 A holds about 150 two-rater-equivalent items, which at
  α ≈ 0.70 gives an interval of roughly ±0.11 (ordinal) to ±0.14 (per collapse), wider on a frame or without
  the owner; a point estimate alone cannot place α in a band.
* **Precision target: a 95 % half-width of α_ordinal on A ≤ 0.10 at α = 0.70.** It is met by the
  recommended R = 5 with |A| = 150 (§6.3) and missed by about 0.01 at the fallback R = 4 with |A| = 120; that
  miss is declared here, before the freeze, as a limitation of the α band if the fallback is run. The power
  script (§10) reports the expected half-width for the configuration actually chosen before any record is
  drafted; |A| is not raised after the freeze.
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
* **Δ = a_JH − a_HH**, averaged over items, paired by item, with a bootstrap CI (§7.1's bootstrap). Items
  with fewer than 2 valid grades have no a_HH and are **excluded from Δ**; their count is reported. Items
  excluded under §2.3 (two or more *meaning* / *question* marks) are excluded from every judge metric.
* **Label use**: J's share of each grade (or collapsed class) on A, beside the panel's shares. A judge that
  always gives the same label can match a lopsided panel by chance (at a *full* rate of 10 % and α 0.667,
  humans agree about 0.94 under strict and an "always not-full" judge scores 0.90, Δ ≈ −0.04); §9.1 (iv)
  guards against it.
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
  owner subset) and the without-owner figures are printed beside every panel figure, so v1's reading stays
  available and the rubric-author confound (the owner approved the rubric and the examples) is visible.

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
holding a split or no-reference pair is excluded and counted. The per-arm difference matters because
unequal inflation bears on the alias study's H2 (v1 §5.3). Added for graded candidates: full@10 (a *full*
chunk in the top 10), support@10 (a *full* or *partial* chunk), and the number of records with support
only, per cell and per arm. Descriptive; with 25 records the intervals will be wide.

## 9. Validity verdict and what paper-b may cite

### 9.1 What replaces the 0.90 binary rule for this study's reading

The alias-study prereg §13.5 rule and its flag stay exactly as they are (§12). The verdict below applies
**per judge J and per scheme S** (strict and lenient for a binary judge; ordinal, strict and lenient for a
graded judge), and every (J, S) lands in exactly one of four outcomes:

* **not evaluable**: α_S on A is below 0.667 at the point estimate (§7.2). Nothing can be said about J
  under S.
* Otherwise J **meets the human bar** under S when all four hold:
  * (i) pool-weighted Δ ≥ −0.05 at the point estimate;
  * (ii) Δ on the near-miss and mid-coverage frames of A together ≥ −0.05 at the point estimate;
  * (iii) the one-sided 95 % lower bound of the pool-weighted Δ ≥ −0.10 (or of the unweighted Δ, if §10's
    design-effect rule selects it before the freeze);
  * (iv) **constant-labeller guard**: α_S with J added as a rater is at least α_S(humans) − 0.05, and J
    gives every grade (or collapsed class) that the panel gives in at least 5 % of its valid grades on A.
* **below** the bar: (i), (ii) or (iv) fails.
* **unresolved**: (i), (ii) and (iv) hold but (iii) fails (too few items to tell).

Reported beside the verdict: α_S with its CI and band (§7.2), Δ with its CI, n, J's label shares, consensus
accuracy, false-support and false-full rates, the pattern table, and **the same verdict computed without
the owner's grades**. Rationale: the two point-estimate clauses mirror §13.5's structure (overall, plus the
hard stratum); the bar is relative to how well humans agree, because with graded labels there is no single
right answer; (iv) stops a judge from passing by matching the majority class; for a binary judge the
verdict says which collapse its labels measure. v1 §8 said this set has "no pass criterion"; v2 replaces
that sentence with this verdict, which is a validity reading per judge and scheme, not a pass or fail of
the main run (§12).

### 9.2 Paper-b licence

**What the panel does not do.** The papers README lists paper-b as blocked on the two-reader human read;
that read (HR-dev, the TREC-CDS confirmation run, `docs/plans/chunking-briefing-2026-11-06.md`) uses a
different corpus and task. This panel does not replace it and does not unblock the CDS confirmatory
verdict.

**What it can license.** Judged figures on OA-1000 produced by a judge configuration this study scores:
the alias study, and the rechunk, boundary and reach studies that reused the frozen judge. All four must
hold:

1. the scheme is evaluable and the judge **meets the human bar** under S (§9.1);
2. α_S ≥ 0.667 (the scheme is evaluable); whenever α_S's band is *tentative* under §7.2 (point estimate
   below 0.800, or lower bound below 0.667), the citing sentence says "panel reliability tentative";
3. the population matches: tok512o0 windows over the study's collections. Figures on other chunkings (for
   example reach-rechunk's adopted chunking S) carry a population-shift caveat unless those collections are
   added to this study as extra read-only cells (§13 Q6);
4. the figures come from a clean-commit `citable: true` artifact under `docs/plans/results/`, pass through
   `claims.json` / `check_claims.py`, and quote α_S, Δ and n beside the first figure.

**Outcome map** (per judge configuration). Only the strict and lenient verdicts feed the map; the
**ordinal verdict of a graded judge is report-only** and licenses nothing by itself. Each judge gets
**exactly one wording** per figure:

| strict outcome | lenient outcome | binary judge (one Hit@10 figure) | graded judge (full@10 and support@10) |
|---|---|---|---|
| meets | meets | cited under the strict wording, "full answer in the top 10", with the note "also meets lenient" | full@10 cited as "full answer"; support@10 cited as "any support" |
| meets | below, unresolved or not evaluable | "full answer in the top 10" | full@10 cited as "full answer"; support@10 only with "judge validity not established" |
| below, unresolved or not evaluable | meets | "any support in the top 10"; never as "answers" without that wording | support@10 cited as "any support"; full@10 only with "judge validity not established" |
| below, unresolved or not evaluable | below, unresolved or not evaluable | only with "judge validity not established", next to the judge-free figure (found@10-gold or quote@10) | the same, for both figures |

Conditions 2–4 apply to every row of the map, and condition 2's "tentative" clause attaches to whichever
scheme is cited. **Rubric-author robustness:** §9.1 computes every verdict with and without the owner's
grades; when the verdict without the owner is lower than the verdict with the owner (a lower row of this
map, or *not evaluable*), the citing sentence adds "not robust to excluding the rubric author". Paper-c
keeps the §13.5 flag on every judged figure; it may quote the panel result beside the flag as later
evidence, under the same map.

## 10. Size, power, cost

**Opus power** (v1 §6, kept; frames renamed): one-sided α 0.05, power 0.80, agreement 0.98 vs 0.95: 253
unanimous pairs (two-sided 330). Paired test at discordance 0.05: a 3-point difference needs about 340 pairs
at α 0.05 and about 525 at the strictest Holm level (0.0125); a 4-point difference about 295 at 0.0125. The
360 drawn (about 300 unanimous) meet the one-sample target and the 4-point paired target.

**Panel precision** (rough normal approximations):

* At agreement about 0.85 under a collapse, the Wilson half-width is about 0.065 on 120 items (0.058 on
  150) and about 0.09 on the 60 near-miss items (0.08 on 75).
* Per block item under a collapse, the paired difference d = a_JH − a_HH takes values in {−1, 0, +½}. With
  an SD of about 0.35 the **unweighted** SE of Δ is about 0.03 on 120 items and 0.029 on 150. Weighting
  inflates it by the Kish design effect of the frame weights, which is not known until the pool exists.
* At SE 0.03, clause (iii) (lower bound ≥ −0.10) binds only when Δ̂ is just below −0.05, so it adds almost
  nothing beyond (i); it starts to bind at SE above about 0.030, and holds for a judge at true Δ = 0 with
  probability Φ(0.10 / SE − 1.645): 0.95 at SE 0.03, 0.72 at 0.045, 0.51 at 0.06. **Rule, decided before
  the freeze from the design effect:** if the weighted SE exceeds 0.04, clause (iii) is applied to the
  unweighted Δ on A (the weighted Δ still carries (i)), or A is enlarged by amendment; the choice is
  recorded in the freeze commit.
* Chance of meeting the bar at SE 0.03 (clauses (i)–(iii), with (iv) assumed met): about 0.9 for a judge at
  true Δ = 0, about 0.5 at Δ = −0.05, under 0.05 at Δ = −0.10; lower at a larger SE.
* The power script simulates the α interval (§7.2's precision target), the design effect, and these chances
  into `artifacts/validation2_power.json` before drafting.
* Sizes do not change without a dated amendment made before any label exists.

**Per-call tokens.** Measured: Opus `/1` about 1.3k prompt tokens; Argon `/2` about 0.93k; Sol `/2` about
0.9k (GPT-5.6 Sol; GPT-6.1 Sol unmeasured); completion about 50 per call (JUDGE-CALIBRATION.md). The `/3`
system is 5,562 characters against 1,615 for `/2`, so `/3` adds 3,947 characters per call: about 0.93k tokens
for Argon (4.26 chars/token, the harness's calibration) and about 1.2k at the flat 3.4 for Sol and the
candidates. Argon `/3` is therefore about 1.86k per call and Sol `/3` about 2.1k. (v2's first draft put the
difference at 0.4k; the measured text says 0.93k.)

**Study-gateway budget at 850 pairs** (Amendment 1 (3): up to 4 × the ~5,000 drafted = **20,000 calls**).
H is costed at its recommended size, 170 items (|A| 150 + |B| 20); the fallback R = 4 saves 120 study-gateway
calls and 60 per candidate:

| item | calls | prompt / completion tokens |
|---|---|---|
| drafting (about 110 records; scaled from the first validation's 203 calls, 172k in, 425k out for 83 records) | ~270 | 0.23M / 0.56M |
| Opus ×3 `/1`, plus one re-issue per empty (about 15 %) | ~2,950 | 3.8M / 0.16M |
| Argon `/2` (KA, KY, K0's tiebreaks) | ~850 | 0.79M / 0.04M |
| Sol `/2` (KS) | ~850 | 0.76M / 0.04M |
| Argon `/3` (KA3, K3's tiebreaks) | ~850 | 1.58M / 0.04M |
| Sol `/3` (KS3) | ~850 | 1.79M / 0.04M |
| Argon `/3` + Sol `/3` repetitions 2–3 on H (170 items × 2 reps × 2 models; A's and B's items run separately) | 680 | 1.35M / 0.03M |
| re-issue allowance (single invalid replies, prereg R4; never a batch) | ~80 | 0.1M / — |
| pre-freeze `/3` live self-check, Argon and Sol (about 20 cases, once each, JUDGE-3-PROMPT.md §4; the 5-run selection rule applies to hosted candidates only) | ~40 | 0.08M / — |
| **study gateway** | **~7,580** | **~10.8M / ~0.92M** |

**Model-gateway budget** (the §4 panel; its own key and cap). Per candidate: `/2` on all pairs (850), `/3` on
all pairs (850), `/3` repetitions 2–3 on H (340), the canary cases (10 at the start and end of each of the
three batches, 60, all on `/3`) and the pre-freeze self-check (about 20 cases × 5 runs, 100) = **2,200
calls**, about 3.6M prompt tokens at the flat 3.4 (0.9k × 850 + 2.1k × 1,350); completion unknown until
measured.

| panel | calls at 850 pairs | prompt tokens |
|---|---|---|
| KX1–KX3 | ~6,600 | ~10.8M |
| KX1–KX4 (with the optional gpt-oss-120b) | ~8,800 | ~14.4M |

**Totals and the ceiling.** Counted together against the 20,000 ceiling, whichever budget pays, C and E
excluded (see below and §13 Q5) (study gateway at 1,000 pairs: 270 + 3,470 + 4,000 + 680 + 80 + 200 = 8,700; per candidate 2,500):

| pairs | study gateway | + 3 candidates | + 4 candidates | with the 110 % caps (4 candidates) |
|---|---|---|---|---|
| 850 | ~7,580 | ~14,180 | ~16,380 | ~18,020 |
| 1,000 | ~8,700 | ~16,200 | ~18,700 | ~20,570 → **drop order applies** |

**Pre-specified drop order**, applied once the pair file is frozen (so the pair count is known) and before
any candidate call, whenever the capped total would exceed 20,000: first the optional candidate's `/2` run
(850–1,000 calls; at 1,000 pairs with four candidates this brings the capped total to about 19,470), then
the optional candidate, then all candidates' repetitions 2–3. The step taken is recorded. The Opus
reference, the Holm-family runs, the self-check and the canary cases are never cut. **There is no re-issue
reserve for a voided batch**: a void ends that candidate's labelling at its last passing batch (§4), which
can only lower the total.

* Sol (GPT-6.1) and the candidates have no measured tokens; the first 20 calls of each are measured for the
  size notice.
* The study gateway's budget is at least 10M tokens a day (prereg §14 O7), so the Opus and `/3` batches are
  spread over several days.

**Off the two paid budgets:** about 160 verifier CLI calls; about 1,650 retrieval calls (110 records × 15
cells); C and E on `/2` and `/3`, now called through the model gateway by model id (Amendment 4) at no
token cost (at the first validation's 1.85 s and 0.74 s per pair: about 37 min per prompt for 850 pairs,
about 75 min in all, more for `/3`'s longer input). C's and E's calls (about 1,700 requests each at 850
pairs, one request per pair and prompt, C at n = 5 and E at n = 3) are logged and gated by the gateway
like every other call. C and E also run the canary cases (10 at the start and end of each of their two
batches, 40 each) and the 5-run pre-freeze self-check (about 20 cases × 5, 100 each), all on `/3`, at no
token cost; KX1's canaries and self-check are already in the per-candidate count above. This plan's **recommendation** is that they are **not counted against the 20,000
ceiling**: Amendment 1 (3) set that ceiling at 4 × v1's ~5,000 drafted gateway calls, and v1's table
never included C and E, which it listed off the gateway; only the path changed, not the count. Amendment
1 (3) said "gateway budget" when there was one gateway, and there are now two, so this is an
interpretation of an owner decision, put to the owner as §13 Q5. If the owner counts them in, the capped
total at 850 pairs with four candidates rises by about 3,400 to about 21,400 and the §10 drop order
applies.

**Human time:** 400 grades at R = 5 (320 at R = 4), about 1.5–2 h per rater, plus at most 10 % in top-ups
(§6.3).

**Caps and notices** (kept from v1): at most 4 calls in flight per gateway, models one after the other;
`fallback_model: ""` on the study gateway and the fallback-off request header on the model gateway; the
`GATEWAY SIZE-NOTICE` with `preflight` and `--notice-acknowledged` before each batch; the budget stop writes
`PAUSE` and is not retried; each batch capped at 110 % of its planned calls; a served-model mismatch (study
gateway) or a configuration-fingerprint mismatch (model gateway) is handled by the §4 table (on the study
gateway it stops the run and stores nothing, as in v1).

## 11. Order of work, freezing, outputs

1. This text and JUDGE-3-PROMPT.md approved by the owner, §13 answered, the §4 panel confirmed. The
   gateway's configuration record and fingerprint are live (2026-11-12, §13 Qe answered). Nothing is
   frozen yet: steps 2 and 3 run first, because the freeze commit records their results.
2. Code, with tests: exclusion builder (as in the first validation); pool builder with the added-arm
   fallback (Q1); frame features and cuts; F/S sampler; `alias-judge/3` and `parse_judgement_v3`
   (JUDGE-3-PROMPT.md §2 and §4); C's graded majority and the K3 rule; KY; the model-gateway client, the
   one path for C, E, KX1 and the hosted candidates, with the per-class mismatch rules, the configuration
   fingerprint read at batch start and end and compared per call, the per-call fingerprint record, and the
   canary cases (§4); panel assignment (core, blocks, top-up, sessions);
   per-rater sheet writer and ingest; agreement statistics (α ordinal and nominal, weighted κ, AC2/AC1, the
   bootstrap, the published worked example as a test); a test that the sheet writer's rubric and examples
   blocks equal the prompt's (§2); judge-vs-panel scoring (§8.1); the candidate analysis (§8.2–8.4); the
   power script with the panel simulation.
3. **Before the freeze:** the `/3` live self-check (JUDGE-3-PROMPT.md §4) on every `/3` judge, 5 runs per
   case on each model-gateway judge (C, E, KX1 and the hosted candidates) for the canary selection (§4);
   the partial fingerprint of every hosted candidate observed during those runs, which becomes that
   candidate's recorded set (§4); the two pinned fingerprints confirmed with the maintainer, and the
   engine's effective sampling defaults for the two pinned configurations read by the maintainer and
   written into the config as the `top_p` / `top_k` pins (§3); the ALCF revision request filed (§4). Raters confirmed,
   rubric read, familiarisation done.
4. **The freeze commit.** It records the owner's approvals, the §13 answers, the panel, the `/3` system
   sha, the self-check counts, the chosen canaries with their measured flip rates, the two pinned
   fingerprints **re-read from the gateway's configuration endpoint that day and confirmed equal to the
   expected values of §4** (a different value is not frozen over; it goes back to the maintainer and the
   owner), and each hosted candidate's partial-fingerprint set as seen in step 3. Every output records
   this commit's sha.
5. Exclusions, config and `run-waves` with the size notice.
6. Retrieval, pool, frames; the pair file frozen; A drawn, assigned and the sheets written.
7. Labelling, in this order: **C on `/2` and `/3` first** (§3, the end-date rule), then E; Opus ×3; Argon
   and Sol on `/2` and `/3`; the panel candidates on `/2` and `/3`; repetitions 2–3 on A's items of H. The
   raters grade A blind meanwhile and their ingests are committed; top-up. Then B is drawn (it needs the
   model labels), assigned, graded and ingested; the repetitions on B's items run after B is drawn.
8. Analysis. Anything outside §7–9 is labelled exploratory.

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

**Cannot:** change the main run's judge, prompt, rule, tiebreaker, drift reference lines, arm-gap pause or
any final label (the main run is complete; there is nothing to gate, pause or delay); remove the §13.5 flag
from any alias-study result or paper-c figure; validate labels beyond the judge configurations and the
population it tested. v1 added "or be read as a pass or fail of the current judge (there is no pass
criterion)"; v2 drops that sentence deliberately: §9.1 is a validity verdict per judge and scheme, and §9.2
says what it licenses. It is still not a pass or fail of the main run, whose flag stands.

It reads no main-run label and writes no collection. v1 Q4 (schedule) was answered by events: the main
run's judging ended on 2026-11-05 and nothing else runs on the judge configurations.

## 13. Open questions for the owner (v2)

| # | question | recommendation |
|---|---|---|
| Q1 | How many raters (R), and who? | R = 5 with A = 150: confirm four raters besides the owner (80 items each, the §7.2 target met). R = 4 with A = 120 is the fallback at the same load with a declared ±0.11; R = 2 reduces A to 80 |
| Q2 | Are the thresholds and margins right? | α cut-offs 0.800 / 0.667; Δ margins −0.05 (point) and −0.10 (lower bound) |
| Q3 | Does §8.3 (c), the panel in the owner's place, fit the accepted Q8 "owner condition"? | yes; the owner-alone figure is printed beside it either way |
| Q4 | Confirm `/3`'s six synthetic examples and its rule that naming the topic is *none*, not *partial* (JUDGE-3-PROMPT.md §3, item 3) | yes to both; the lenient collapse would otherwise readmit the pilot's false yeses |
| Q5 | Which budget pays for the panel candidates? And, now that C's and E's calls pass through the model gateway too (Amendment 4): are they inside or outside the 20,000 ceiling? Amendment 1 (3) said "gateway budget" when there was one gateway | the model gateway's own key; both paid budgets counted against the 20,000 ceiling in §10 either way; **C and E outside the ceiling** (v1's ~5,000, which the ceiling is 4× of, never included them; only their path changed). **Owner to confirm**; if they count, the capped total at 850 pairs with four candidates is about 21,400 and the drop order applies (§10) |
| Q6 | Add reach-rechunk's adopted-chunking (S) collections as extra read-only cells? | yes, if still loaded; sizes amended before freeze |
| Q7 | What is paper-b's scope under §9.2? | the panel licenses OA-1000 judged figures only; HR-dev remains the CDS blocker; re-scoping paper-b is the owner's decision |
| Q8 | Set-level sufficiency? | a later plan |
| Qa | May unpinned ALCF models be decision judges? | **answered** (Amendment 2): yes, with the partial configuration fingerprint, the response `model` field and the date recorded per call (the instance privately; Amendment 4), and a revision request to ALCF support |
| Qb | Confirm the §4 panel (KX1 Qwen3.8-27B anchor; Opus as the existing reference; KX2 Mistral-Large-3-675B; KX3 Llama-3.1-70B; optional KX4 gpt-oss-120b) | **answered** (Amendment 3): confirmed as proposed |
| Qc | A gateway-side route lock on the two pinned configurations? | optional; under Amendment 4 the configuration fingerprint carries identity and a route lock adds none; left to the maintainer |
| Qd | Confirm that C's and KX1's configurations stay available under their model ids for the study window (§3), and the schedule that puts C's labelling first | **answered in part** (Amendment 3, reworded under Amendment 4): the maintainer changes neither configuration without the owner's go; the owner gives the window guarantee at the freeze. The chat template and the reasoning parser are outside the §4 fingerprint, so for the local judges they rest on this promise, backed by the local canaries (§4) |
| Qe | Did the model gateway expose the configuration record and fingerprint that the §4 identity gate reads? (As of 2026-11-11 it reported the serving instance only; the interim gate of Amendment 4 was the alternative) | **answered** (Amendment 5): the record and fingerprint went live on 2026-11-12. The gate is the per-response configuration value (full for C, E and KX1; partial, in the freeze-commit set, for the hosted candidates). The interim gate is not needed and not used; its text stays in Amendment 4 as history. The freeze commit re-reads and confirms the two expected full fingerprints of §4 |

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

It answers §13 Qa: unpinned ALCF-hosted models may be decision judges (§3's definition), with the
per-call backend header value, the response `model` field and the date recorded, and a revision request to
ALCF support. The proposed panel of §4 stays the recommendation; the owner confirms it as §13 Qb.
(Amendment 4 replaces the per-call backend value with the configuration fingerprint the gateway reports;
the backend or instance is still recorded, privately, for tracing.)

### Amendment 3 (2026-11-11): owner answers to §13

Owner decision, recorded verbatim:

> I agree with your recommendations. I would use [four named raters] as raters.. Depends on the type of
> question and how easy it is to rate and find the information.

The raters' names are held in the private study record, not in this public file. What this settles:

- **Q1:** R = 5 (the owner plus the four named raters), |A| = 150, 80 items per rater; R = 4 remains the
  declared fallback if a rater drops out before the freeze.
- **Q2–Q8, Qb–Qd:** the recommendations in §13 are accepted as written, including the panel (KX1
  Qwen3.8-27B anchor, Opus as the existing reference, KX2 Mistral-Large-3-675B, KX3 Llama-3.1-70B,
  optional KX4 gpt-oss-120b), the optional route lock left to the owner and the model gateway, and C's end
  date to be supplied by the model-gateway maintainer before the freeze.
- **The owner's caveat** ("depends on the type of question and how easy it is to rate and find the
  information") is recorded as an open design point to settle before the freeze: the rater
  familiarisation step (§11 step 3) will report per-rater time and *unsure* rate on the core items by
  question type, and if a rater cannot rate a type reliably, the owner may re-balance block assignment by
  amendment before any set-A grading starts. No change to the frozen block design is made here.
- **Budget slip corrected:** the study-gateway self-check runs once per case (as JUDGE-3-PROMPT.md §4 and §11
  step 3 say), about 40 calls, not 200; every budget total above therefore overstates by about 160 calls,
  left as a conservative margin.

- **Qd, judge C (facts from the model-gateway maintainer, 2026-11-11; reworded in configuration terms
  under Amendment 4).** C is the configuration Qwen3.6-35B-A3B, snapshot `995ad96e…`, served by vLLM
  0.31.0 with the main run's flags, called with `alias-judge/2` and thinking on, as in
  JUDGE-CALIBRATION.md. It is not the Scout-named alias of the same weights, so that alias's 2026-11-30
  end does not bind C. C's configuration has no end date but is marked not permanent, and an open GPU
  proposal could stop its serving. The promise that matters is about configurations, not servers: the
  maintainer changes neither C's configuration nor KX1's (their fingerprints, §4) without the owner's
  explicit go-ahead, notifies this study first, and keeps both available under their model ids; where and
  on what they run may change. A guarantee for the study window is the owner's to give at the freeze. The
  §3 fallback (C labelled first; a re-served identical configuration counts as C; anything else is KC′
  and K0 cannot be claimed) stands.

### Amendment 4 (2026-11-11): judges are configurations reached through the model gateway

Owner decision, recorded verbatim:

> We should not use the ports but reach the models through the frontdoor/model-gateway This allows us to
> move the models or restart if necessary. The config is important and not the port or direct url. The
> frontdoor should return model/setup information.

What this changes, and where:

- **Path.** C, E, KX1 and the hosted candidates are reached only through the model gateway, by model id
  (§3, §4, §10, §11 step 2). No direct address or port of any model appears in this plan, in
  JUDGE-3-PROMPT.md or in any study config; the private config holds the two gateway addresses and
  nothing else. Opus, Argon and Sol keep their existing path through the study gateway, by model name,
  with v1's served-model check; the plan never routed them through the model gateway and does not now.
- **Identity = configuration, not location** (§4). The backend-name gates (the per-class table keyed on
  the expected backend, "a missing header is a mismatch", the start-and-end model-listing check) are
  replaced by a configuration-fingerprint gate: the fingerprint covers the model repository or id, the
  snapshot or revision, the quantisation, the serving engine and version, and the serving flags (context
  length, default chat-template arguments including the default thinking setting, the reasoning parser),
  and is read from the gateway. A call served by a different configuration is void under the existing
  per-class rule (pinned configuration: the batch stops; hosted: the candidate is reported to its last
  passing batch). A move or restart with an identical configuration is not a mismatch. Every row records
  the fingerprint the gateway reports, the response `model` field and the date; the backend or instance is
  recorded privately.
- **Pins** (§3, §4). C's and KX1's pins are configuration pins: snapshot plus flags, as one fingerprint
  each, recorded in the freeze commit. The §3 fallback for C stays: a re-served identical configuration
  counts as C; anything else is KC′.
- **Hosted ids** (§4). The gateway reports a partial fingerprint (id, cluster, framework; no revision).
  The canary rule stays the change detector for hosted ids, and the partial fingerprint used is stated
  per row and in the freeze commit.
- **Dependency** (§13 Qe, blocks the freeze). The gateway today reports the serving instance, not the
  weights. The maintainer has proposed a configuration record (declared from the gateway's own record,
  and observed from the running engine, kept apart and compared) and a fingerprint over both that
  excludes start time and instance, on an authenticated endpoint and on every forwarded response, partial
  for hosted ids; about a day of work after the owner's go and one brief gateway restart. The snapshot
  hash cannot be observed through the engine and is declared only. Until it exists the freeze cannot
  happen, or the owner accepts the interim gate (instance name + response `model` field + canaries) as a
  declared limitation, under which a same-configuration move to another instance is a mismatch unless the
  maintainer confirms it beforehand and it is recorded per row (§4). Recommendation: wait for it.
- **Order of work** (§11). The live self-check, the canary selection and the recording of the pinned and
  hosted fingerprints now run before the freeze commit (step 3), and the freeze commit (step 4) records
  them; the `/3` system sha goes into the freeze commit rather than a later amendment (§3.2). A serving
  outage is not a mismatch; the pinned-configuration remedy is a dated amendment only (§4).
- **Housekeeping.** §13 Qc (route lock) is now optional without bearing on identity; Qd and Amendment
  3's Qd bullet are reworded in configuration terms; the freeze conditions gain item (d). C's and E's
  calls now pass through the model gateway; whether they count against the 20,000 ceiling is put to the
  owner as §13 Q5, with the recommendation that they do not (§10).

### Amendment 5 (2026-11-12): the gateway's configuration record and fingerprint are live; §13 Qe answered

The model-gateway maintainer built and deployed the configuration record and fingerprint on 2026-11-12,
after a brief front-door restart. It was read the same day with the study's service key, with no model
call; a private copy of the reading is kept beside the config. Facts, stated generically (the concrete
names of the endpoint and the per-response value are in the private config, not here):

- Per model, a *declared* record (repository, revision, quantisation, engine, context, settings); for a
  local backend an *observed* block (served repository, context, engine version, KV-cache dtype, start
  time); a list of the fields on which the engine contradicts the record; and a fingerprint. The
  fingerprint is **full** when the engine could be read and **partial** when it could not (always for an
  ALCF id, where it covers the route, the serving backend's name and the model id only; for a local id a
  partial value means the engine was unreadable at that moment). The two kinds carry different prefixes.
- The same value comes back on every forwarded answer; a gateway-written error carries none.
- It excludes the backend's name, its address and the start time (so a move or restart with the same
  setup keeps it). It does not cover the chat template or the sampling defaults, and speed-only options
  are deliberately excluded. The snapshot hash is declared, not observed.
- Read on 2026-11-12, no contradictions listed: C (`Qwen3.6-35B-A3B`) `v1:c94d715990c85f58`; E and KX1
  (`Qwen3.8-27B`) `v1:76874a40425f4e01`; hosted, all partial: Mistral-Large-3 `v1-partial:1b3b761acf47e93c`,
  Llama-3.1-70B `v1-partial:806c83f705876ae0`, gpt-oss-120b `v1-partial:354bfb398ce854ba` and
  `v1-partial:32ef49512f6c83ac` (two clusters). The declared and observed fields behind the two full values
  are tabulated in §4.
- Fallback off keeps a request on the first route of the model's route order; there is no per-request
  route preference. The two Qwen ids have a local backend only.

What this settles:

- **§13 Qe is answered.** The freeze no longer waits on the gateway. The interim gate (instance name in
  the fingerprint's place, with the maintainer-confirmed-move allowance) is not needed and is not used;
  Amendment 4's text about it is kept as history.
- **Expected values** (§4). The two full fingerprints above are the pins. The freeze commit re-reads both
  from the gateway's configuration endpoint and confirms them (§11 step 4, status paragraph item (d)); a
  different value is not frozen over. The hosted partial values above are the expected sets; the sets
  actually recorded are those seen in the pre-freeze self-check (§11 step 3), and gpt-oss-120b's may hold
  both clusters.
- **The gate** (§4). A local judge's answer must carry exactly its pinned full fingerprint; a partial or
  different value voids it. A hosted judge's partial value must be in its freeze-commit set. Canaries stay
  the change detector for hosted judges. A missing value on a stored reply is a mismatch; a gateway error
  is an outage, not a mismatch (§4).
- **Stated limits** (§4). The chat template and the sampling defaults are outside the fingerprint, so the
  harness pins and records them per call (thinking on, `max_tokens`, temperature, §3); the snapshot is
  declared only. The partial fingerprint for ALCF ids covers route, backend name and model id, not
  weights; the earlier "id, cluster, framework" wording in Amendment 4 described the proposal and is
  superseded by this.
- **Status and §11** updated accordingly; §4's "Open items" now reads re-read and confirm.

**Review follow-up (2026-11-12, after an independent check of the first Amendment 5 text):**

- **Sampling pinned for every local judge** (§3, §4). C, E and KX1 get thinking, `max_tokens`,
  temperature 0.7 (KX1 as E), `top_p`, `top_k` and `n` sent on every request, never defaults. The
  `top_p` / `top_k` values are the engine's effective defaults for the two pinned configurations, read by
  the maintainer before the freeze and written into the config and the freeze commit, so that K0 keeps
  the main run's effective settings (the main run sent temperature only); if they cannot be established,
  the model's published thinking-mode values are sent and the freeze commit declares the possible
  difference from the main run. The hosted candidates keep `max_tokens` 4096 and no temperature.
- **Compensating record** (JUDGE-3-PROMPT.md §2). Every stored label carries `max_tokens`, temperature,
  `top_p`, `top_k`, `n` and the thinking flag **as sent**, beside whatever the gateway reports.
- **Chat template and reasoning parser** (§4, §13 Qd). Chosen: **both** — the plan states that for the
  local judges these rest on the maintainer's no-change promise (Qd), **and** the canary cases now run on
  the local judges too (C, E, KX1: 10 cases at the start and end of every batch, 5-run self-check
  before the freeze, at no token cost, §10). A local canary void with a matching fingerprint is reported
  as template, parser or default drift and goes to the maintainer and the owner before any later batch
  of that judge.
- **Transient partial fingerprint on a local judge = outage, not identity failure** (§4). The reply is
  not stored, the batch pauses, the fingerprint is re-read up to 5 times a minute apart; the pinned full
  value resumes the batch and re-issues the paused pair; 5 partial reads, or any different full value,
  is a mismatch. The same rule applies to the batch start and end reads, so a momentary unreadable engine
  neither voids a batch nor ends C's labelling (K0's dependency).
- **Stale text** (§4). The hosted partial fingerprint is "route, backend name, model id" everywhere
  outside quoted history; an ALCF revision, if one is given, is recorded in the freeze commit beside the
  partial fingerprint, not added to it (the gateway computes that value).

The plan is not frozen; it freezes only on the owner's word "freeze".

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
