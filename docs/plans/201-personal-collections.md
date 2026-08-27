# Personal collections (#201)

**Status:** `DEPLOYED` and partly proven. All six phases built; the user story works end to end
on live infrastructure. Three legs of the lifecycle are built but unexercised.

## The story

A BV-BRC researcher signs in, makes a few collections of their own documents, and asks questions
grounded in them. The authorization layer shipped three weeks before this work; **the way to get a
document *in* did not.**

Operating assumptions: **~1,000 documents and 2–5 collections per user, development tenant only.**

## Phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 0 | De-risk the value proposition (#200) | `DONE`, gate inconclusive | PR #362. The gate as written could not be evaluated — the baseline kept aggregates only, and small libraries score 0.95 because small corpora are easy, not because the config is good. Nothing argued for a small-N branch, so phase 2 was cleared; "inconclusive" was **not** written up as "equivalent". |
| 1 | Close the safety gaps before a surface exists | `DONE` | #130 IDOR, #197 filter drop, #87 rate limits, #287 creation gate. All landed **before** the ingest surface opened, which was the point of ordering them first. |
| 2 | The ingest path (#203 → #202) | `DONE` | User ingest submits to GoWe as the user; **the Workspace is the only source**, so no task ever holds the token and the API keeps no staging directory. Option A was skipped — batch-per-task shipped directly. |
| 3 | Limits that match the assumptions | `DONE`, never fired live | Per-collection chunk cap, per-owner quota of 5, `default` as a pointer. |
| 4 | Using what you own | `PARTIAL` | Fused search, the shared-collection count bug, prev/next expansion all shipped. **#86 catalog and #281 rename remain open.** |
| 5 | Scale-out by policy | `DONE` | A runbook, not code. #289 stays open by design. |
| 6 | Graph on personal collections | `BUILT, OFF BY DEFAULT` | Evidence fields, query-side entity extraction, `extract-graph`. Default-on is decided by the ablation number (#122), not by the code existing. |

## What live validation proved

Run on the dev tenant against real infrastructure:

- **The user story works**: BV-BRC token sign-in → create → upload 3 PDFs → job completed 3/3,
  241 chunks → archive delivered to the caller's own Workspace.
- **The archive is byte-exact.** Downloaded and re-verified: every sha256 matches the manifest,
  241 float32 rows at dim 4096, zero replacement characters.
- **Throughput**, from one complete 50-PDF job (5,915 chunks): 127.6s submit→completed, of which
  **84.5s is Workspace byte movement and only 38.4s is compute**. Per-task fixed cost 1.34s extract
  + 6.05s ingest; marginal 44.6 ms/doc and 11.5 ms/chunk (R²=0.999). Dispatch gaps ~0.15s — the
  engine scheduler is not the bottleneck. Only 3 of 4 workers used: the 50-file request cap × batch
  20 is exactly 3 shards.

## What live validation broke

Every one of these was invisible to 2,311 unit/API tests and 104 conformance tests:

| defect | what it was |
|---|---|
| #414 / #408 | **The second upload into any collection was a hard 500.** Dot-named Workspace metadata keys: accepted-and-discarded on create, rejected on update. Since a request is capped at 50 files, every library over 50 documents was unreachable — the core use case. |
| #415 | A failed upload left its job row non-terminal, and the in-flight guard then refused **every** later ingest for 6 h with no self-service recovery. |
| #407 | A dev-tenant ingest **wrote to the production stores** — the API never seeded the store URLs and the CWL defaults point at production. |
| GoWe#172 | **Every archive the engine delivered was silently corrupt** — file bytes marshalled through a JSON string. The engine reported `delivered` both times. Our own per-file sha256 is what caught it. |
| #404 | The graph driver was pinned nowhere and resolved to a major version that cannot boot the API. |

## The sizing is wrong by 3.4×

The plan is built on ~34 chunks per document, taken from the open-access bulk build. Real
full-text research PDFs produced **118.3 chunks per document**. So 1,000 documents is ~118,000
chunks, and a user hits the 50,000-chunk cap at about **423 documents** — less than half the
stated target. The cap and the "1,000 documents per user" assumption are inconsistent as written.

Cheapest resolution: restate the assumption (5 collections × ~400 documents is self-consistent
today) and express the limit in **documents**, because "400 of 1,000 documents" is actionable and
"50,000 chunks" is not. Raising the cap instead triples per-collection RAM and moves the active
bound `n`. Recorded on #291.

## The active bound, measured

Ten real 35k-chunk collections built and torn down. **The prediction was wrong in an instructive
way** — mappings and threads do not bind; threads are essentially nil per collection because both
stores size their runtimes once from the host's core count. **RAM binds: 751 MiB per collection.**
So `n` is a function of the memory share this tenant is granted, which is a decision nobody has
made. Recommendation: keep 100, ~150 defensible, 250 only once the share is stated.

## Not yet proven live

- **Restore from a cold archive**, and the evict→restore round trip. Built (#376, #379, #397).
- **The limits firing at the cap.** Built (#383, #384), never exercised against a full collection.
- **The graph leg.** Built and off by default; needs a worker image carrying the graph extra (#404).
- **A full 250-document run.** Stopped at 50 by #414; now possible.
