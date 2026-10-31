# Alias study: answer-judge calibration (alias-judge/1)

Status: harness in `python/scripts/eval/glossbench_alias_judge.py`, tests in
`python/tests/eval/test_glossbench_alias_judge.py`. This note covers the design, how `n` is
chosen and the counts. It holds no pair text. The calibration set, the labels and the private
report are in the study's private output directory (`calib/`).

## The question

The study's answer judge decides, for a QUESTION (asked with a primary TERM) and a retrieved
PASSAGE from any paper, whether the passage answers the question. Opus is the reference judge,
but it costs gateway budget. Can a local Qwen judge (mango `:8004`, Qwen3.6-35B-A3B), sampled
`n` times with a majority vote, replace it? And if not everywhere, can Qwen's vote share decide
which pairs it may label alone and which should go to Opus?

## The judge task and the single prompt

`JUDGE_VERSION = "alias-judge/1"`: `JUDGE_SYSTEM` plus `judge_prompt()`. Every backend gets the
same system and user message, byte for byte. `test_same_prompt_for_every_backend` checks this.

* A passage from another paper counts if it answers the question.
* A short form in the passage counts as the TERM only if it stands for the TERM in the
  passage's **own** paper. Collisions are common: about 47 % of abbreviations that appear in
  more than one paper differ in meaning between papers (ABBREV.md).
* **Context given with the passage:** the passage paper's title and the short forms that paper
  defines, meaning its merged-glossary entries whose alias occurs in the passage (at most 25,
  sorted). Nothing is keyed on the question. The judge never sees the question paper's
  glossary, so it can spot a collision only through the passage paper's own definition, just
  as a reader of that paper would. Without this context the task cannot be decided: a G
  passage contains only the short form, by construction.
* Output: `{"reason": "<= 25 words", "answers": "yes" | "no"}`. The reason comes first so
  that a model without thinking still reasons before it commits.

## Calibration set (offline; pilot records only)

`build-calib` reads only the two pilots' draft and acceptance logs, and takes records with a
layer-1 pass. It refuses any path under a `main` directory. It makes no model, API or index
calls. Each question gets candidate passages from the OA-1000 chunk export:

| stratum | how it is chosen |
|---|---|
| `gold` | the record's source chunk |
| `lex_same` | the top BM25 chunk of the same paper that is not gold (a local Okapi BM25 over the export, with the question as the query) |
| `lex_other` | the top BM25 chunks of two other papers, one chunk per paper |
| `coll_diff` | chunks from other papers that contain the record's alias, where that paper's glossary gives the alias a primary sharing **no** content word with the TERM (the rule of `alias_abbrev.collision`). Ranked by BM25, one per paper |
| `coll_same` | the same, but the primaries share a content word |

A paper with no glossary entry for the alias is not used, because its meaning is unknown.
The quota per question is gold 1, lex_same 1, lex_other 2 and up to 6 collisions (half of
each meaning where possible). Tie-breaks are a seeded hash, so the set does not depend on the
interpreter's hash seed (a test checks this). The file is frozen once built.

Built set (sha256 `983b8d7c…`, commit `c6983f3`): **410 pairs from 65 questions** (36 from
pilot 1, 29 from pilot 2; 31 accepted, 34 layer-1-only). Pairs by stratum: gold 65,
lex_same 64, lex_other 130, coll_same 82, coll_diff 69. Pairs by record kind: G 229, C 181.
The aliases of most questions have fewer than 6 glossed collisions; 239 collision slots
stayed empty.

## Backends

* **Opus via the partner gateway.** `AliasGateway`, the drafter's client, is reused unchanged.
  Every body carries `fallback_model: ""` and `max_tokens`. Temperature is omitted, so the
  model's default applies. The budget stop is the drafter's classifier: `provider_notice`,
  429, an untyped notice body, or three untyped 503s. It exits 76 and writes `PAUSE` and
  `budget-stop-judge.json`. The cumulative caps and the 5-in-a-row / 20-total failure pauses
  also apply. Three repetitions per pair measure self-consistency. At most 4 calls are in
  flight; the operator can lower that mid-run through an `INFLIGHT` file.
* **Claude CLI.** `Run.call_cli` is reused, so the flags and the empty cwd are the
  verifier's. The #891 safeguard-fallback detection marks an answer from another model
  `refused`, and such a pair has no verdict. Every call is isolation-checked.
* **Qwen.** One request per pair with `n = 100` samples at temperature 0.7, thinking off
  (`chat_template_kwargs.enable_thinking = false`), `max_tokens` 200. Every sample's verdict
  is stored, and the served model id is recorded.

Every backend has a notice gate (`preflight`, then `--notice-acknowledged` for the same
plan), a `PAUSE` file and `--resume` without re-issuing a stored key.

## How `n` is chosen

Qwen is sampled **once**, at n = 100. Smaller `n` is simulated without re-running. For every
odd `n`, each of 1,000 bootstrap draws takes an `n`-subsample of each pair's valid samples
**without replacement**. That is a hypergeometric draw of the yes count, so it is exact rather
than a resample of a resample. The draw's majority vote is then scored against the Opus
reference, which is the majority of its three labels. Scores are accuracy, Cohen's κ and
per-stratum accuracy. Pairs where Opus is not unanimous are scored separately, because the
reference itself is uncertain there.

The **plateau** is the smallest `n` whose mean accuracy is within 0.005 of the n = 99 value.
The deployable `n` is the plateau, or a smaller `n` if the accuracy given up is negligible
for the study.

The vote share is the confidence score. The analysis reports a reliability table (Opus yes
rate per vote-share decile) and a **cascade**: Qwen's label is accepted when its share is
≥ τ or ≤ 1 − τ, and the remaining pairs escalate to Opus. The cascade is evaluated for
τ ∈ {0.6, 0.7, 0.8, 0.9} at the full 100 samples and at n = 11 and n = 21 (bootstrapped).
It reports the escalation rate, the accuracy of the accepted subset and the overall accuracy.

## Results

Run of 2026-10-31. The labels came from commits c6983f3 → 4f020dc, as each totals file's
provenance records. The two fixes between those commits did not change a stored label: one
re-issues empty answers, the other scales the CLI isolation budget on a safeguard retry.
Counts only below.

**Opus (gateway model `Claude Opus 5.5`, the strongest Opus the gateway lists).** Each pair
was labelled 3 times at the default temperature. 33 of the 410 pairs (8 %) got an empty
answer (0 tokens) on every attempt, which looks like a provider-side filter. The Claude CLI
refused 21 of those same 33. Those 33 pairs have no reference. Of the 377 labelled pairs,
372 are unanimous (98.7 %), and pairwise agreement between repetitions is 0.996. Gateway use
was 1,500 calls (1,230 in the batch plus re-issues of empty answers), 1.99 M tokens in and
93 k out.

**Claude CLI (`claude-opus-5-5`, once).** Agreement with the Opus majority is 0.992
(κ 0.980, n = 370). The CLI refused 28 pairs; 23 of those were #891 safeguard fallbacks.

**Qwen (`Qwen3.6-35B-A3B`, n = 100, T 0.7, thinking off).** 41,000 samples, 4 unparseable,
at 45 samples/s with 4 requests in flight.

Majority-vote agreement with the Opus reference, on the pairs where Opus was unanimous
(372 pairs, 1,000 hypergeometric draws per n):

| n | accuracy | κ |
|---|---|---|
| 1 | 0.956 | 0.887 |
| 3 | 0.955 | 0.887 |
| 5 | 0.955 | 0.886 |
| 11 | 0.954 | 0.884 |
| 21 | 0.953 | 0.881 |
| 51 | 0.952 | 0.879 |
| 99 | 0.952 | 0.877 |

**Majority voting buys nothing; the plateau is at n = 1.** Qwen's disagreements with Opus are
systematic, not sampling noise. The vote share is bimodal, and the wrong labels sit in the
confident bins. 15 of the 18 disagreements are on G records, and 13 of the 18 are false "no"s:
Qwen rejects a short form that the passage's paper defines as the term.

Per-stratum accuracy at n = 99: coll_diff 1.00, lex_other 0.97, gold 0.97, coll_same 0.93,
lex_same 0.86.

The coll_diff stratum is trivial once the passage paper's definition is shown: every judge
says "no". It is decidable from the definitions block alone: in all 69 coll_diff pairs the
alias is listed with the passage paper's OWN different primary and never with the TERM, and
the TERM string occurs in only 1 of the 69 passages. Opus said "no" on 68/68 labelled and
Qwen scores 1.00 there, so the stratum measures the prompt's definition block, not judgement. This set therefore does not test a collision the judge must infer without a
definition.

**Cascade** (vote share as confidence; pairs with an uncertain share go to Opus):

| τ | escalated (n = 100) | cascade accuracy | escalated (n = 11) | cascade accuracy |
|---|---|---|---|---|
| 0.6 | 0.8 % | 0.957 | 0.8 % | 0.959 |
| 0.7 | 1.6 % | 0.965 | 1.7 % | 0.964 |
| 0.8 | 2.7 % | 0.973 | 2.8 % | 0.971 |
| 0.9 | 5.4 % | 0.984 | 4.1 % | 0.977 |

**What this means for choosing n.** With thinking off, Qwen cannot replace Opus by sampling.
n = 1 is as good as n = 99, about 4 points below the reference. A cascade at τ = 0.9 with
n ≈ 11 sends about 4 % of pairs to Opus and closes about half the gap. Two levers are
untested: thinking on, and a prompt rule that a short form defined as the term IS the term.
Either one would be a new judge version, re-run on this set.

## Round 2: the two untested levers (alias-judge/2, thinking on)

Run of 2026-10-31, harness commit `65a58e1` (clean). The set and the Opus reference are round
1's, read in place (`calib_dir`; `pairs_sha256` pins `983b8d7c…`). Outputs are in `calib/round2/`
(`analysis-variants.json`, `SHA256SUMS`). Local Qwen only (mango `:8004`, Qwen3.6-35B-A3B, at most
4 requests in flight). There were no gateway or CLI calls.

* **A**: `alias-judge/2` with thinking off. The prompt is /1 plus one rule (`JUDGE_RULE_V2`): *"If the passage uses a
  short form (an abbreviation or a symbol) that its paper defines as the question's TERM, treat
  the short form as the TERM itself."* The user message is unchanged.
* **B**: `alias-judge/1` with thinking on. **C**: `alias-judge/2` with thinking on.
* Thinking switch: `chat_template_kwargs: {"enable_thinking": true}`, vLLM's pass-through to
  the Qwen3 chat template. The thinking comes back in `message.reasoning` and is counted in
  `usage.completion_tokens_details.reasoning_tokens`. `max_tokens` was 8192 with thinking on and 200 with it off.
* Each configuration took n = 21 samples per pair at T 0.7 on all 410 pairs. Every row records
  its prompt version, system sha, thinking flag and reasoning tokens. The baseline is round 1's
  n = 100 file, subsampled to the same n values.

**Accuracy on the 372 Opus-unanimous pairs** (majority vote, 1,000 hypergeometric draws; FP is
a false yes and FN a false no, mean counts):

| config | n=1 acc | κ | FP | FN | n=21 acc | κ | FP | FN |
|---|---|---|---|---|---|---|---|---|
| baseline (/1, off) | 0.956 | 0.887 | 5.1 | 11.4 | 0.953 | 0.881 | 5.0 | 12.5 |
| A (/2, off) | 0.956 | 0.888 | 5.7 | 10.9 | 0.952 | 0.878 | 6 | 12 |
| B (/1, on) | 0.977 | 0.943 | 6.9 | 1.7 | 0.976 | 0.941 | 7 | 2 |
| C (/2, on) | 0.976 | 0.941 | 7.5 | 1.5 | 0.981 | 0.954 | 6 | 1 |

n = 3 / 11: baseline 0.955 / 0.954, A 0.956 / 0.955, B 0.978 / 0.978, C 0.978 / 0.980.

**Subsets.** The review counted 5 questions and 30 pairs overlapping the study. The main draft log
has grown since then. At this run (`overlap.json`, which records the log's sha and row count),
7 calibration records and 50 pairs share a `record_id` with the study.

| n = 1 → 21 | all 372 | without overlap (327) | without coll_diff (304) | without both (262) |
|---|---|---|---|---|
| baseline | 0.956 → 0.953 | 0.966 → 0.964 | 0.946 → 0.943 | 0.957 → 0.956 |
| A | 0.956 → 0.952 | 0.966 → 0.966 | 0.946 → 0.941 | 0.958 → 0.958 |
| B | 0.977 → 0.976 | 0.985 → 0.982 | 0.972 → 0.970 | 0.982 → 0.977 |
| C | 0.976 → 0.981 | 0.985 → 0.988 | 0.971 → 0.977 | 0.981 → 0.985 |

coll_diff is 1.00 for every configuration. As in round 1, it is reported but is not evidence of judgement.

**Per-stratum accuracy at n = 21** (all 372; G / C is the record kind):

| config | gold | lex_same | lex_other | coll_same | coll_diff | G | C |
|---|---|---|---|---|---|---|---|
| baseline | 0.97 | 0.86 | 0.97 | 0.94 | 1.00 | 0.930 | 0.982 |
| A | 0.97 | 0.84 | 0.97 | 0.95 | 1.00 | 0.932 | 0.976 |
| B | 1.00 | 0.88 | 0.99 | 0.99 | 1.00 | 0.976 | 0.976 |
| C | 1.00 | 0.89 | 0.99 | 1.00 | 1.00 | 0.985 | 0.976 |

**Majority errors over all stored samples.** The baseline makes 18: 13 FN, all but one on G
records, and 5 FP. A makes 18, of which 17 are the baseline's. B makes 9 (2 FN, 7 FP): it fixes 12
baseline errors and adds 3. C makes 7 (1 FN, 6 FP): it fixes 13 and adds 2. All of C's errors
are in lex_same (6) or lex_other (1). It has no collision error and no false no on a G record.
These are the n = 21 FP / FN of the table above, which the round-2 working notes first
undercounted by one for B and C: they dropped the 2 unanimous pairs whose Opus log holds a
re-issued fourth row.

**Over-correction check.** The rule did not turn collisions or lexical non-answers into yeses.
coll_diff has 0 FP in A and C, and coll_same has 0 FP in A and C (B has 1). C's false yeses (6; the
baseline has 5) are in lex_same and lex_other, and 4 of them are baseline errors. The
residue is Qwen accepting a neighbouring chunk of the question's own paper that Opus rejects.

**Cascade at n = 21** (accept Qwen when the share is ≥ τ or ≤ 1 − τ; Opus labels the rest):

| config | τ 0.8 escalated | cascade acc | τ 0.9 escalated | cascade acc |
|---|---|---|---|---|
| baseline | 3.0 % | 0.972 | 4.7 % | 0.980 |
| A | 3.2 % | 0.973 | 4.3 % | 0.979 |
| B | 1.6 % | 0.987 | 2.2 % | 0.987 |
| C | 1.6 % | 0.984 | 2.4 % | 0.984 |

Without the overlap records, τ 0.9 at n = 21 escalates 1.5 % for both B (0.994) and C (0.991).
With thinking on, the remaining errors sit in confident bins, so escalation adds only 0.3–1 point.

**Throughput** (410 requests × 21 samples, 4 in flight): A 177 s at 48.6 samples/s, 42
completion tokens per sample, 2 invalid (length). B 1,524 s at 5.65 samples/s, 668 completion
tokens per sample of which 631 reasoning, 0 invalid. C 1,556 s at 5.53 samples/s, 675 tokens per
sample (638 reasoning), 1 invalid (length). Thinking costs about 16× the tokens and about 9× the
time per sample.

**Reading.** The prompt rule alone does nothing; Qwen's false noes survive an explicit rule.
Thinking on removes them: FN falls from about 12 to 1–2, and gold, coll_same and lex_other reach
about 1.00. With thinking on, the rule adds a little (C has no collision error and is best at
n = 21), but at n = 1, B and C are within noise. Majority voting still buys little: C gains
0.5 points from n = 1 to 21, and B gains none. The remaining gap to Opus, about 2 points (1.2
without the overlap records), is false yeses on same-paper neighbouring chunks.

## Round 3: a second local model (Qwen3.8-27B)

Run of 2026-10-31, harness commit `9dabeb4` (clean). The set, the Opus reference and the overlap
list (7 records, 50 pairs, copied from round 2 so the subsets match) are unchanged. Outputs are in
`calib/round3/` (`analysis-variants.json`, `provenance.json`, `SHA256SUMS`). Local endpoint only:
mango `:8008`, served model id `Qwen3.8-27B` (`Qwen/Qwen3.8-27B-FP8`, vLLM 0.31.0). There were no
gateway or CLI calls.

* **Harness change.** `qwen.require_served_model` puts `base_url` in the plan, so a preflight for one
  endpoint does not admit a run on another. It also stops the run, storing nothing, if a reply's
  served model id is not `qwen.model`. Each row records its endpoint. Round-2 plans are unchanged.
* **Thinking switch.** It is the same as on `:8004`, `chat_template_kwargs.enable_thinking`. The
  model's default is thinking **on**, so D must send `false` explicitly. The thinking comes back in
  `message.reasoning`.
* **Concurrency** (alias-judge/2 prompts, n = 21 per request; first 1, 2, 4 and 8 requests in
  flight). With thinking off, samples/s were 14.2, 15.8, 17.6 and 18.1. With thinking on they were
  7.8, 8.3, 7.5 and 12.4. The server held at most 128 sequences running, and the rest queued. The
  runs used 4 in flight (the harness cap), because 8 gains little and the endpoint is shared.
* **D** is `alias-judge/2` with thinking off (`max_tokens` 200). **E** is `alias-judge/2` with thinking
  on (`max_tokens` 8192). Both took n = 21 at T 0.7 on all 410 pairs. **C** is round 2's
  Qwen3.6-35B-A3B with /2 and thinking on.

**Accuracy on the 372 Opus-unanimous pairs** (majority vote, 1,000 hypergeometric draws; FP and FN
are mean counts):

| config | n=1 acc | κ | FP | FN | n=21 acc | κ | FP | FN |
|---|---|---|---|---|---|---|---|---|
| C (3.6, /2, on) | 0.976 | 0.941 | 7.5 | 1.5 | 0.981 | 0.954 | 6 | 1 |
| D (3.8, /2, off) | 0.959 | 0.896 | 2.0 | 13.1 | 0.960 | 0.897 | 2 | 13 |
| E (3.8, /2, on) | 0.980 | 0.950 | 4.3 | 3.3 | 0.981 | 0.953 | 4 | 3 |

n = 3 / 11: C 0.978 / 0.980, D 0.959 / 0.959, E 0.981 / 0.982.

| n = 1 → 21 | all 372 | without overlap (327) | without coll_diff (304) | without both (262) |
|---|---|---|---|---|
| C | 0.976 → 0.981 | 0.985 → 0.988 | 0.971 → 0.977 | 0.981 → 0.985 |
| D | 0.959 → 0.960 | 0.964 → 0.963 | 0.950 → 0.951 | 0.955 → 0.954 |
| E | 0.980 → 0.981 | 0.986 → 0.988 | 0.975 → 0.977 | 0.982 → 0.985 |

**Per-stratum accuracy at n = 21** (all 372):

| config | gold | lex_same | lex_other | coll_same | coll_diff | G | C |
|---|---|---|---|---|---|---|---|
| C | 1.00 | 0.89 | 0.99 | 1.00 | 1.00 | 0.985 | 0.976 |
| D | 0.98 | 0.93 | 0.95 | 0.93 | 1.00 | 0.946 | 0.976 |
| E | 1.00 | 0.96 | 0.97 | 0.97 | 1.00 | 0.976 | 0.988 |

**Majority errors over all stored samples.** D makes 15: 13 FN and 2 FP. Five of the FN are coll_same
G pairs, which is the round-1 pattern of rejecting a short form the passage's paper defines as the
term. E makes 7: 3 FN and 4 FP. They are 2 in lex_same, 2 in lex_other and 3 in coll_same (1 FP and
2 FN). C also makes 7, but only 3 of E's 7 are C's: E fixes 4 of C's errors and adds 4. Three of E's
errors (and three of C's) are on overlap records.

**Cascade at n = 21** (τ 0.8 / 0.9): C escalates 1.6 % / 2.4 % with accuracy 0.984 / 0.984. D
escalates 2.4 % / 4.3 % with 0.976 / 0.981. E escalates 0.8 % / 1.3 % with 0.984 / 0.984. E's
remaining errors are confident, so a cascade adds about 0.3 points.

**Throughput** (410 requests × 21 samples, 4 in flight): D took 491 s at 17.5 samples/s, with 34
completion tokens per sample and 0 invalid. E took 1,000 s at 8.6 samples/s, with 195 completion
tokens per sample (158 of them reasoning) and 0 invalid. C took 1,556 s at 5.5 samples/s with 675
tokens per sample. E reasons about a quarter as long as C and finishes 1.6× faster. With thinking
off, Qwen3.8-27B (dense) is about 2.8× slower than Qwen3.6-35B-A3B (MoE).

**Reading.** With thinking off, the new model fails the same way as the old one: false noes on G
records, near round 1's baseline. With thinking on, E matches C at n = 21 (0.981 both, 0.988
without overlap) and is 0.4 points better at n = 1. Its errors are balanced (FP 4 / FN 3, against
C's 6 / 1), and it gives up C's perfect coll_same for fewer lex_same false yeses. Majority voting
buys E almost nothing (0.980 → 0.981), so n = 1 to 3 is deployable. The two thinking judges are
interchangeable on accuracy. E is cheaper per sample and needs no voting. The two judges share
only 3 errors, which leaves an untested option: a C + E agreement gate.
