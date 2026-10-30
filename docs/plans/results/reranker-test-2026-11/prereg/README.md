# Pre-registration: the dedicated reranker test

**Status: FROZEN `5dfe9e65c96add94a550a7a040d8a425c99d949194b4e5b851f26483f15ad230` at 2026-10-30T02:19:33Z on the owner's go (commit 9e109c3).** The pre-registration file is unchanged since that commit and is never edited; later changes go to `DEVIATIONS.md`. At the freeze, no arm of §3 had been run on any set of §4 and the docs-A bm25-vs-rerank contrast had not been computed.

| file | tier | what |
|---|---|---|
| [PREREG-reranker-test.md](PREREG-reranker-test.md) | record (frozen on the owner's go) | the design: decomposition, hypotheses, arms, sets, measures, analysis, gates, code pin, decisions |
| [PLAN-reranker-test.md](PLAN-reranker-test.md) | **lab book** (dated, superseded-not-deleted) | ordered steps, owners, compute, risks — not a design constraint |
| [power-table-reranker.json](power-table-reranker.json) | planning aid | `reranker_test.py power-table --pages 208 --questions-n 735 --delta 0.05 --alpha 0.05 --n-sims 2000 --n-flips 2000 --seed 261029`, run at `9d78672` from a clean tree (`citable: true`); the run re-computes it on the realised layout (`--gold`) beside the results |

**Code of prereg §9's second table, merged** (2026-10-29): **#867** (`exp/r2-manip-checks`, merge
**`48d5c73`**) — the inactive-reranker gate, the glossary-boost behavioural probe
(`glossary_boost_probe.py`, shared with round 2) and Holm at p = 1.0 for gated or absent members as the
default with `holm_drop_gated` as the labelled sensitivity. R2-P's admission to family B (prereg §4, D15)
depends in addition on round 2's **#868** (`exp/r2-setp-fields`: set-P field mode against the partner's
binding export v2, pilot, partner adapter), merge **`b5251db`**, which round 2 requires before its own
freeze. Every run is at or after `48d5c73` (`b5251db` when it touches R2-P).

## Freeze procedure

The part-4 procedure (`../../chunk-size-deployed-docs-part4-2026-10-25/prereg/`), with one difference
stated in step 3:

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written** — its draft header included.
2. In a clean checkout of that commit, `sha256sum PREREG-reranker-test.md` and a UTC timestamp
   (`date -u +%FT%TZ`) are appended to a **private** `PREREG.sha256` kept with the outputs, off GitHub,
   **before** `pools` runs on any set and before the docs-A bm25-vs-rerank contrast is computed from the
   step-7 file (prereg §11, D3).
3. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha of the frozen text>)`. **The prereg file is not touched, not even for a
   "frozen" note**: the public file is the frozen text, so its sha256 on `main` must stay equal to the
   recorded sha (part 4 annotated its public copy because the hashed original was a private file). Any
   later change is a `DEVIATIONS.md` entry. (`reranker_test.py` has no prereg-sha check of its own; the
   sha is verified by hand against this README before the first `pools` run and recorded in the run's
   notes.)
4. The PR carrying the freeze commit is merged; its merge commit is the freeze record's commit. The first
   `pools` run happens after that merge.

Preconditions, **all met** on `main` at `b5251db` (2026-10-29; checked by re-reading every subcommand,
flag, constant and reason code the prereg names against that tree, re-running `power-table` with the §7
command, which reproduced every non-provenance key of `power-table-reranker.json`, and running the #867
unit tests): the harness (#862) at `e3002ba`; **#867** at `48d5c73` (D13, D14); **#868** at `b5251db`
(behind D15); the owner's decisions D1–D15 folded in. For the R2 sets of family B, round 2's own freeze
precedes their use.

## What changed from the v0 draft (2026-10-29, same day)

Substance, not wording:

- **§0 corrections are made**, not pending: the lab-book addendum of 2026-10-29 (#860) is cited, with
  Tests 2–3 of `docs/plans/symbol-glossary.md` as the source of 85, 65, 57, 105 and 33.
- **The 57/155 control was a seeded derangement over the glossaried chunks of the 50-pools**; the harness
  default (`--shuffle-unit string`) permutes over all glossary bearers and allows fixed points (counted).
  The difference is stated beside every shuffled figure.
- **`use_graph=false`** pinned on every API request.
- **Multiplier and RRF k are not exposed by `/v1/config`**: declared via `--server-settings`, recorded as
  server-assumed, not read back.
- **A4-50 is GATE-NOT-EVALUABLE when identical to A3 on every record** (a wired-but-inactive reranker);
  noted that the round-2 runner enforces this in code and this harness does not automate it.
- **An ABSENT identity gate makes H1–H3 GATE-NOT-EVALUABLE** unless `--identity-reference` is supplied;
  on docs-A the reference is part 4's own reranked lists (`part4-retrieval.jsonl`, `--identity-arm
  T512o0`).
- **`gloss-only` is exploratory only** (a glossary-less chunk is scored on an empty passage).
- **Set P runs on our host, the 155 held privately**; the partner's gateway clamps `top_k` to 10, so no
  leg goes through it; the API route caps depth at 100; the in-process BM25 is a numpy approximation and
  not byte-identical on papers-OC, so depth 200 is docs-A in-process only.
- **Private paths removed** from §10 (the sidecar's cache and deps directories are now the harness's
  `--reranker-cache` / `--sidecar-deps` arguments); the tenant name and the gateway's product name no
  longer appear.
- **Decisions section** replaces the separate decisions file; D6 records that the host question is moot
  under the lifted copy rule.

## v1.1 — review of 2026-10-29 (same day)

- **Two manipulation checks are required code, not analysis rules**: the inactive-reranker gate (A4-50
  vs A3, A5-50 vs A0; fires → the arm's hypotheses GNE at p = 1.0) and a behavioural probe that
  `TEXT_SYMBOL_GLOSSARY_BOOST` is active (ships at 0.0, not in `/v1/config`; fails → hard stop). Both
  are an in-flight PR and precede the freeze (D13).
- **Holm is always over three**; a gated or absent member enters at p = 1.0 (D14), the same rule as
  round 2. The code's evaluable-members Holm is a sensitivity until the PR lands.
- **The power table is committed here** from the committed code at `9d78672` (clean tree) instead of
  deferred to freeze time.
- **`--prefer live` pinned**: the API's own reranked capture is the arm wherever it exists.
- **R2-P** carries round 2's status label and is exploratory unless round 2's addendum promotes P.

## v1.2 — final pre-freeze edits (owner decisions of 2026-10-29)

- **The in-flight PR is #867**: named in the header, §3, §6, §8, §9, D13 and D14, with the probe's five
  verdicts (`inactive` / `mixed` / `error` stop; `not_testable` gates unless `--boost-declared`;
  `declared`) and the gate's reach to fuse arms built on a gated list.
- **D14: Holm at p = 1.0 is the code's default from #867**; `holm_drop_gated` is a labelled sensitivity
  only, never the decision.
- **D15 (new): R2-P follows round 2's bounded promotion addendum** (round 2 §3.5 / D17: fixed hypotheses
  H1-P, H3-P, H4-P; Holm over 7; Wilson 95% upper bound ≤ 0.15 on the partner's pilot error rate; dated
  before any non-pilot P record is drafted). This file adds no decision point of its own; if round 2
  does not promote, R2-P is exploratory here with no further option.
- **#868** named as the dependency behind R2-P (set-P field mode against the partner's binding export
  v2, pilot, adapter); the partner is "the partner" or "the papers-OC owner" throughout.

## v1.3 — freeze-readiness check against `main` at `b5251db` (2026-10-29)

Text only; no hypothesis, arm, measure, test, threshold or decision changed:

- **#867 merged at `48d5c73`, #868 at `b5251db`**: every "required before freezing" clause (prereg
  header, §6, §8, §9 second table, D13, D14) now states the merge commit; the citable pin moves from
  `e3002ba` to `48d5c73` (`b5251db` for R2-P). The gate and Holm functions are named as the code has them
  (`inactive_reranker`, `holm_family`, `api_boost_probe`).
- **Freeze record**: the sha is recorded in this README only, never in the prereg file (procedure step 3).
