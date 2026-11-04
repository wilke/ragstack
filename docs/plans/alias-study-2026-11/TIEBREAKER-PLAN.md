# Alias study: a non-Claude tiebreaker judge (plan, frozen before labelling)

Status: **frozen**. This file is committed before any tiebreaker label is requested. Every output
records the sha of the commit that added it. A later change is a dated amendment at the end.

Background: [JUDGE-VALIDATION-PLAN.md](JUDGE-VALIDATION-PLAN.md) and the validation outputs (merged
#898; private `calib/validation/`). The validation set has 587 pairs. Opus (`Claude Opus 5.5`, 3
repetitions) is unanimous on 487. On **83 pairs Opus returned an empty answer on every attempt**
(a provider filter keyed on about 20 vaccine/virus questions), and the Claude CLI refused 62 of
those 83. Those pairs have no Claude reference. We want a judge that (a) agrees with Opus where
Opus answers and (b) answers the filtered pairs.

## 1. Candidates

The partner gateway (`127.0.0.1:18766`) allow-list (`GET /v1/models`, 2026-10-31) lists **no GPT-6
model** (GPT-6 Astra / Sol / Luna, GPT-6.1 Sol are in Argo's catalogue but not on the gateway's
allow-list; the partner who owns it is unreachable, so it is not changed). The candidates are:

| model (requested name) | served id accepted | note |
|---|---|---|
| `GPT-5.6 Sol` | `GPT-5.6 Sol`, `gpt-5.6-sol-2026-07-09` | the gateway's primary; the reply's `model` is the versioned id (probe) |
| `Gemini 4 Argon` | `Gemini 4 Argon` | the newest Gemini on the allow-list |

A reply whose `model` is not in the accepted set stops the run and is not stored (a fallback to
another model would show up here; every body carries `fallback_model: ""`).

## 2. Settings (both models, identical)

* Harness: `glossbench_alias_judge.py`, backend `gateway` (this PR), prompt **`alias-judge/2`**
  (the system prompt the C and E judges use), the same user message as every judge.
* `max_tokens` 4096, temperature omitted (the model's default), `fallback_model: ""`, at most 4 in
  flight in total: the models run **one after the other**.
* An empty answer is re-issued once; if the second answer is empty too, it is **stored** as empty
  (an empty answer is part of what is measured here). The budget stop (`provider_notice` at any
  status, HTTP 429, an untyped notice) pauses the run; it is not retried.

## 3. Labels

* Every model × all 587 pairs × 1 (rep 1).
* Reps 2 and 3 (self-consistency) on a fixed subset: the **83 filtered pairs** plus **100
  Opus-unanimous pairs** sampled with seed `20261031|tiebreaker|selfcons` (`random.Random(seed)
  .sample(sorted(unanimous ids), 100)`). The subset file is written before labelling and its sha256
  is pinned in the config.
* Calls: 2 × (587 + 2 × 183) = 1,906.

## 4. Metrics (rep 1 unless stated)

* **Agreement with Opus** on the 487 unanimous pairs: (pairs whose label equals the Opus label) /
  487. **A pair without a valid label counts as a disagreement.** Reported beside it: Cohen's κ on
  the pairs with a valid label, false yes (judge yes, Opus no) and false no, and the same without
  `coll_diff`.
* **Refusal / empty rate**: pairs without a valid label (empty, truncated, unparseable, an HTTP
  error left after the retry pass) / pairs, on all 587 and on the filtered 83.
* **Self-consistency** on the subset: the share of pairs whose three labels are valid and equal,
  separately for the 83 and the 100.
* Cost: prompt, completion and reasoning tokens per call, and latency (median, p90). The gateway
  reports no price, so **cost = mean total tokens per call**.
* On the filtered 83: each model's labels against the C + E gate's final labels (75 no, 8 yes,
  0 escalated in the validation), and against each other. Descriptive only: there is no reference.

## 5. Selection rule

Among the models whose refusal / empty rate on the filtered 83 is **≤ 5 %** (at most 4 of 83),
select the one with the **highest agreement on the 487**. A tie (the same number of agreeing pairs)
goes to the lower cost (§4). If no model qualifies, nothing is selected and that is the result.

## 6. Outputs

Private: `/rag/tmp/alias-study-2026-11/calib/tiebreaker/` (0700 / 0600): config, subset file,
preflights, `labels-gw-<model>.jsonl`, `calls-gw-<model>.jsonl`, totals, `analysis-tiebreaker.json`,
`LOG` (GATEWAY SIZE-NOTICE and totals lines), `provenance.json`, `SHA256SUMS`.

## Amendments

(none)
