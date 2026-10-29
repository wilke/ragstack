# Pre-registration: the dedicated reranker test

**Status: DRAFT v1 (2026-10-29). NOT FROZEN.** No arm of §3 has been run on any set of §4; the docs-A
bm25-vs-rerank contrast has not been computed.

| file | tier | what |
|---|---|---|
| [PREREG-reranker-test.md](PREREG-reranker-test.md) | record (frozen on the owner's go) | the design: decomposition, hypotheses, arms, sets, measures, analysis, gates, code pin, decisions |
| [PLAN-reranker-test.md](PLAN-reranker-test.md) | **lab book** (dated, superseded-not-deleted) | ordered steps, owners, compute, risks — not a design constraint |
| [power-table-reranker.json](power-table-reranker.json) | planning aid | `reranker_test.py power-table --pages 208 --questions-n 735 --delta 0.05 --alpha 0.05 --n-sims 2000 --n-flips 2000 --seed 261029`, run at `9d78672` from a clean tree (`citable: true`); the run re-computes it on the realised layout (`--gold`) beside the results |

**Code still to merge before freezing** (prereg §9, second table): the inactive-reranker gate, the
glossary-boost behavioural probe, and Holm at p = 1.0 for gated members — one in-flight PR; its number
is written here on merge.

## Freeze procedure

The part-4 procedure (`../../chunk-size-deployed-docs-part4-2026-10-25/prereg/`), unchanged:

1. The owner says go. The prereg is frozen **as written** — its draft header included.
2. `sha256sum PREREG-reranker-test.md` and a UTC timestamp are appended to a **private** `PREREG.sha256`
   kept with the outputs, off GitHub, **before** `pools` runs on any set.
3. A one-line commit records the freeze here (this status line) and at the top of the prereg (`> Frozen
   <sha> at <UTC>`). The body is not touched; any later change is a `DEVIATIONS.md` entry.

Preconditions, met at `e3002ba`: the harness (#862) is on `main` with unit tests and a review; the
owner's decisions (prereg §12) are folded in.

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
