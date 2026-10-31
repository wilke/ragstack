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
