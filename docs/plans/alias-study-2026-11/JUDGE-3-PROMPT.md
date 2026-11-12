# Alias study: the graded judge prompt `alias-judge/3` (draft for owner approval)

Status: **DRAFT, written 2026-11-11, for the owner to approve (JUDGE-VALIDATION-2-PLAN.md, Amendment 1
(4)). Not in code, no system sha yet, not run.** The plan is
[JUDGE-VALIDATION-2-PLAN.md](JUDGE-VALIDATION-2-PLAN.md) (v2); this prompt is its §3.2 and its §2.2 rubric.
The prompt is committed in the harness with its system sha **before any record of the second validation set
is drafted** (plan §3.2, §11). That rule is unchanged from v1.

**What this draft read.** Public repository text only: the pilot's description of the failure pattern (plan
§1), the spot check's counts and tag counts ([RESULTS.md](../results/alias-study-2026-11/RESULTS.md)), the
layer-2 verifier's criteria ([VERIFIER.md](VERIFIER.md)), and the specifications of `alias-judge/1` and `/2`
(`python/scripts/eval/glossbench_alias_judge.py`, [JUDGE-CALIBRATION.md](JUDGE-CALIBRATION.md)). It read no
pair, question or passage, public or private: not the pilot's 12 owner-labelled pairs (which plan v1 had
named as `/3`'s only data input) and not the 19 spot-check disagreements. Its examples are invented and
model no pair.

**What decision (2) of Amendment 1 changed.** Plan v1 said `/3` is "/2 with sharper *not* criteria" and
that `JUDGE_OUTPUT_FORMAT` stays verbatim. Under graded labels it cannot. `/3` differs from `/2` in three
things: the label set, the criteria and the output format. The alias rules stay verbatim (§1.2). Any
comparison of a `/3` judge with the binary candidates (K0, KA, KS, KY) or with the Opus reference (`/1`,
decision Q9) goes through the binary readings of §2.1 (plan §2.4).

## 1. The prompt

### 1.1 How /1 and /2 are specified (the structure mirrored)

| piece | `/1`, `/2` (code: `glossbench_alias_judge.py`) | `/3` (this draft) |
|---|---|---|
| system | `JUDGE_SYSTEM`; `/2` = `/1` + `JUDGE_RULE_V2`, inserted before the *yes* bullet | `JUDGE_SYSTEM_V3` (§1.2) |
| user message | `judge_prompt(question, term, passage, title, defs)`, the same for every backend | the same context block, with a new closing line (§1.3) |
| output | `{"reason": "at most 25 words", "answers": "yes" \| "no"}`, reason first | the same keys and order; values `full` / `partial` / `none` (§1.2, last block) |
| parser | `parse_judgement`: the last JSON object with a valid `answers` value | `parse_judgement_v3`, the same algorithm with the new value set (§2) |
| registry | `JUDGE_SYSTEMS["alias-judge/1" \| "/2"]` | `JUDGE_SYSTEMS["alias-judge/3"]` |

### 1.2 System message (`JUDGE_SYSTEM_V3`)

These parts are verbatim from `/1` and `/2`: the second sentence of the opening, the first two rule bullets
(from `/1`) and the third rule bullet (`JUDGE_RULE_V2`). A test asserts all three (§4); the check was also
run against the code on 2026-11-11 (all three hold).

```text
You judge how fully a PASSAGE from a scientific paper answers a QUESTION. Judge only from the text you are given, never from outside knowledge of any specific paper.

Rules:
- The QUESTION is phrased with a TERM. The passage may come from ANY paper, not necessarily the paper the question was written from. If it answers the question, it counts.
- The passage may use a short form (an abbreviation or a symbol) instead of the TERM. A short form counts as the TERM only if, in the passage's own paper, it stands for the TERM (the same concept). Use the passage paper's definitions given with the passage; when the passage or its definitions show that the short form stands for something else, it is NOT the TERM. The same short form often means different things in different papers.
- If the passage uses a short form (an abbreviation or a symbol) that its paper defines as the question's TERM, treat the short form as the TERM itself.

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

Examples (synthetic: invented terms, not from any paper):
- QUESTION: At what temperature are plates incubated for the Morvane test? PASSAGE: "Plates for the Morvane test were incubated at 30 °C for two days before colonies were counted." -> "full": the temperature is stated; the duration and the counting are extra.
- QUESTION: What is the purpose of the Morvane test? PASSAGE: "All isolates underwent the Morvane test and a growth assay." -> "none": the test is named, but its purpose is not stated.
- QUESTION: By how much does velorin lower the ZQ index in adult zebrafish? PASSAGE: "In adult zebrafish, velorin lowered the ZQ index." -> "partial": the direction is stated; the amount asked for is absent.
- QUESTION: Which solvent is used to extract tarsin, and why? PASSAGE: "Tarsin was extracted with cold ethanol." -> "partial": the solvent is stated; the reason is absent.
- QUESTION: What is the half-life of tarsin in seawater? PASSAGE (no medium in the passage or the title): "Tarsin had a half-life of 12 days." -> "partial": the value is stated; the seawater condition is unstated. Had the passage said "in fresh water", the condition would be contradicted: "none".
- TERM: Plinth ratio. QUESTION: Above what Plinth ratio is a slope classed as unstable? PASSAGE from another paper, whose definitions say "PR" stands for "Plinth ratio": "Slopes with PR above 1.4 were classed as unstable." -> "full": PR is the TERM in that paper, and the threshold is stated.

Answer with a JSON object only, exactly these keys, reason first; do not put double quotes inside the reason:
{"reason": "at most 25 words", "answers": "full" | "partial" | "none"}
```

The output block is a new constant, `JUDGE_OUTPUT_FORMAT_V3`. **Length, measured on this text:** the
system is 5,562 characters against 1,615 for `/2` (`/1` is 1,462), so `/3` adds 3,947 characters to every
call: about 0.93k input tokens for Argon at the harness's calibrated 4.26 characters per token, about 1.4k
for a Claude model at 2.81, and about 1.2k at the flat 3.4 used for every other model. The call count does
not change. The size notice computes the exact figure from the registry text: the gateway-judge path already
passes `JUDGE_SYSTEMS[judge_version]` to `gateway_est_input_tokens`, and the Opus path passes `JUDGE_SYSTEM`
because Opus runs `/1`; keep that split when adding the model-gateway backend.

### 1.3 User message (`judge_prompt_v3`)

This is `judge_prompt` byte for byte, except the last line (checked against the code on 2026-11-11). The
definitions block keeps its rules: the passage paper's merged-glossary entries whose alias occurs in the
passage, at most 25 (`MAX_DEFS`), sorted, one `- "<alias>" stands for "<primary>"` per line, or `(none
listed)`.

```text
PASSAGE'S PAPER TITLE: {title or '(unknown)'}
SHORT FORMS THE PASSAGE'S PAPER DEFINES (those occurring in the passage):
{definitions}

PASSAGE:
<<<
{passage}
>>>

TERM: {term}
QUESTION: {question}

How fully does the passage answer the question? Answer with the JSON object only.
```

The `/1`–`/2` closing line is *"Does the passage answer the question? Answer with the JSON object only."*
That is a yes/no question, and it would invite `"answers": "yes"`, which the `/3` parser rejects.
Implementation: `judge_prompt(...)` with that one line replaced, plus an assertion that the replacement
happened exactly once. **No reference answer is shown**, as in `/1` and `/2` and as on the spot-check sheet
(`alias_study_spot.py`, `PAIR_ITEM_FIELDS`): the task accepts correct answers from other papers that may
differ from the record's own answer, and a reference would anchor the judge to that one answer. Decision Q2
(the verifier quote standing in for a missing reference answer) concerns the `ans_cov` sampling feature
only (plan §5.3). It never enters a judge prompt or a rater sheet (plan §2).

## 2. Parsing contract (`parse_judgement_v3`)

```python
_GRADES = ("full", "partial", "none")

def parse_judgement_v3(text: str) -> tuple[str | None, str]:
    t = re.sub(r"<think>.*?</think>", "", (text or "").strip(), flags=re.S)
    cands = []
    try:
        cands.append(json.loads(t.strip().strip("`").removeprefix("json").strip()))
    except ValueError:
        pass
    for m in _JSON_OBJ.finditer(t):          # r"\{[^{}]*\}", re.S, as for /1
        try:
            cands.append(json.loads(m.group(0)))
        except ValueError:
            continue
    for obj in reversed(cands):
        if isinstance(obj, dict) and isinstance(obj.get("answers"), str):
            a = obj["answers"].strip().casefold()
            if a in _GRADES:
                return a, str(obj.get("reason") or "")[:300]
    return None, ""
```

* **Valid.** The **last** JSON object (whole-text parse first, then each flat `{…}` in text order) whose
  `answers` is a string equal to `full`, `partial` or `none` after stripping whitespace and case-folding.
  Tolerated, as for `/1`: code fences, prose around the object, an inline `<think>` block, extra keys, any
  key order, a missing reason (stored as `""`).
* **Invalid** (returns `None`). Anything else, with no synonym mapping: `yes`, `no`, `unsure`, `partially`,
  `full answer`, `Full.`, a list, a number, or no object at all. Mapping synonyms would be a judgement made
  at parse time.
* **It fails closed in both directions.** The key stays `answers`, but the value sets do not overlap. A `/2`
  parser reads a `/3` reply as invalid, and a `/3` parser reads a `/2` reply as invalid. A mixed-up config
  therefore produces missing labels, which are counted, never wrong labels.
* **Echoed examples cannot parse.** The prompt's examples use arrow notation, not JSON. A model that echoes
  one produces no object with an `answers` key.
* **An invalid reply, per role** (plan §3, §6.1). On a gateway it is re-issued once (prereg R4); if still
  invalid, the pair has no label from that judge, and the count is printed. For local Qwen, the invalid
  sample is left out of the vote and counted.
* **Stored per label.** `grade` (`full` / `partial` / `none` / null), `reason`, `judge_version`
  (`alias-judge/3`), `system_sha256`, `prompt_sha256`, the served model id, the serving backend where the
  gateway reports it (plan §4; a missing backend value is a mismatch there), the call date, and the
  thinking flag and reasoning tokens where the backend reports them.

### 2.1 Binary readings (derived, never asked)

| reading | *answers* when | used for |
|---|---|---|
| **strict** | `grade == "full"` | comparison with `/1` / `/2` labels; a found@10 that credits only complete passages |
| **lenient** | `grade in ("full", "partial")` | the same comparisons under the other reading of *partial* |

Both readings are always printed. The plan makes **strict** the primary reading for the §8.3 comparison
with K0 and with Opus `/1`, with lenient beside it (plan §8.3).

## 3. Rationale

1. **Why graded.** This is the owner's decision (2). The evidence: all 19 spot-check disagreements sit on
   the partial-versus-full line (12 tagged *partial*, 7 *full*, 0 *none*). The owner's own binary labels do
   not fit the owner's own tags under either reading of *partial*: they match on 10 of 19 with *partial =
   not* and on 6 of 19 with *partial = answers* (RESULTS.md, `counts.readings`). So the line was not drawn
   consistently by anyone, the owner included. `/1`'s sentence *"a passage that states the core of what is
   asked is 'yes' even if it adds or omits details"* puts that line inside the word "core". `/3` removes the
   sentence and makes the line an output.

2. **Parts and conditions make the line checkable.** "Every part stated, every condition met" can be
   checked against words in the passage. "The core" cannot. The breakdown follows what already failed in
   this pipeline:
   * the pilot's near-misses (the passage names the method but not its purpose, value, result, definition
     or condition);
   * `alias-verify/3`'s strict `supports` (an unstated purpose fails, and so does an unstated qualifier;
     VERIFIER.md).

   So a `/3` *full* means roughly what the verifier required of the record's own quote.

3. **Naming the topic is *none*, not *partial*. Owner, please confirm this rule (plan §13 Q4).** If
   mentioning the term or method counted as *partial*, the lenient reading would bring back exactly the
   false yeses the pilot found (9 of 9 disagreements were of this kind, plan §1). It would also inflate
   Hit@10 as plan §8.4 describes. The rule "point to the words or it is *none*" (step 2) keeps the
   *partial* / *none* line close to a yes/no fact. **Terminology clash:** the pilot table calls this pattern
   a "partial answer". The owner's 2026-11-04 binary reading made it *not*, confirming 5 of 7 pilot false
   yeses as *not*. This draft makes it *none*. If the owner's spot-check tag *partial* included naming-only
   passages, `/3` and the owner will disagree on those, and only the owner can say.

4. **When unsure between *full* and *partial*, choose *partial*.** This puts the owner's "partial allows
   for disagreement between graders" into the prompt: doubt about completeness lands in the middle class, so
   *full* becomes the class graders should agree on. The cost is a lower *full* rate under the strict
   reading. It is measured, not assumed, because both readings are printed. There is no matching rule
   between *partial* and *none*: step 2's point-to-the-words test decides that line.

5. **The judge has no *unsure* label and no confidence field.** The owner allows *unsure* for human raters
   (decision 1). A human cannot be resampled, and an honest abstention is better than a coin flip. The
   judges can be resampled:
   * C draws 5 samples, E 3 and Opus 3, and the calibration already used the vote share as the measured
     confidence;
   * a self-reported confidence was never calibrated here, and it would add a parsing failure mode;
   * an abstain label would be used most on near-misses, the very stratum the set exists to measure;
   * the doubt that matters for this set (how complete is it?) already has a home: *partial*.

   A reply of `"unsure"` is invalid by the parsing contract (§2).

6. **Only the closing line of the user message changes.** So K0 and K3 see the same pair context, title
   and definitions byte for byte, and the blinding of prereg §6.2 carries over. No arm, retriever, rank or
   cell is shown.

7. **The examples.** There are six. Each one maps to one rule: a full answer with extra detail; topic named
   only; direction without amount; one of two parts; a condition left unstated, with its contradicted
   variant; a short form defined as the TERM in another paper. All terms are invented (Morvane test,
   velorin, ZQ index, tarsin, Plinth ratio; "ZQ" replaced an earlier "KV", which names a real zebrafish
   structure). The alias rules get one example only, because the pilot found 0
   alias disagreements. **Known bias** (plan §3.2): the examples and rules encode one reading of
   completeness. So K3's agreement with the owner, who is rater r1, is partly by construction, and its
   agreement with Opus `/1` and with the other raters is not.

8. **The reason field.** It names the stated parts and, for *partial*, what is missing. That makes the
   full-versus-partial call auditable on the disagreement read (plan §7.3). The "no double quotes inside the
   reason" clause answers the one unparseable Argon reply in the tiebreaker run (unescaped quotes in the
   JSON, JUDGE-CALIBRATION.md).

## 4. Implementation checklist (before any validation-2 record)

* Add `JUDGE_SYSTEM_V3`, `JUDGE_OUTPUT_FORMAT_V3`, `judge_prompt_v3`, `parse_judgement_v3`, and
  `JUDGE_SYSTEMS["alias-judge/3"]` to `glossbench_alias_judge.py`, and store `grade` beside the binary
  `verdict`.
* Tests:
  * `/3` contains `/1`'s first two rule bullets and `JUDGE_RULE_V2` verbatim;
  * `judge_prompt_v3` differs from `judge_prompt` in the last line only;
  * the same user message goes to every backend;
  * the plan's §2.2 rubric equals the *Parts and conditions*, *Labels* and *Deciding* blocks of
    `JUDGE_SYSTEM_V3`, and the rater sheet writer's rubric **and examples** blocks equal the same blocks of
    `JUDGE_SYSTEM_V3` byte for byte (so the rater sheet and the judge rate one construct);
  * parser cases for every bullet of §2, including the cross-version fail-closed property.
* Self-check: a separate set of synthetic cases, not the prompt's examples, at least one per rule, with
  **expected labels set by the owner**. They follow the pattern of `VERIFY_CASES` in
  `test_glossbench_alias.py`. They are run live once on each `/3` judge before any validation-2 record is
  drafted, and the counts go into the plan amendment that records the system sha (plan §11, step 3). Ten
  of them, fixed before the freeze, are the canary cases that open and close every hosted candidate's batch
  (plan §4): always sent with `/3`, at temperature 0 and a fixed seed where the backend accepts them, with
  the plan's 3-of-10 tolerance otherwise.
* The system sha256 is recorded in that amendment in the same commit.

## 5. What the plan decided about this prompt (v2)

Plan v1 had left these to the plan; v2 settles them:

* **C's label over three classes** (plan §3, K3): the grade held by more than half of C's valid samples;
  otherwise no label. The escalation rate is reported.
* **The K3 rule** (plan §3): C and E agree when their grades are equal; otherwise Argon `/3` decides, read
  from the KA3 run on all pairs.
* **Primary binary reading** (plan §8.3): strict; lenient printed beside it.
* **Raters** (plan §2, §6.3): the rater sheet reproduces this prompt's *Parts and conditions*, *Labels*,
  *Deciding* and *Examples* blocks verbatim and shows the same fields as the judge, so that humans and
  judges rate one construct and no rater sees less than the owner, who approved the examples. Per decision (1), more raters with fewer pairs each; agreement by Krippendorff's α (plan §7).
* **The candidate judges** (plan §4): each runs both `/2` and `/3`; the panel and its per-call gate are in
  the plan, not here.
