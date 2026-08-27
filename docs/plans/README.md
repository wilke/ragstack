# Plans

Working plans, their status, and what is being worked on right now. Each plan records
what was decided **and why**, so a decision can be re-argued rather than re-discovered.

**Status vocabulary:** `DONE` shipped and merged · `DEPLOYED` also running on tenants ·
`IN PROGRESS` being built now · `OPEN` planned, not started · `BLOCKED` waiting on something
named · `ABANDONED` decided against, with the reason kept.

Last updated 2026-08-26. Fleet: **all four tenants on `v1.4.2`**.

---

## Currently working on

| | |
|---|---|
| **#427 W3 — per-stage query timings** | `IN PROGRESS` — the last load-bearing item of the observability work. It is what answers *why* a search exceeded its bound rather than only that it did. |

Immediately after: **W4** (latency rollup), then deploy the set and re-run the acceptance
check against the affected user's tenant. W3 + W4 are the remainder of #427's acceptance
criterion.

---

## Plans

| Plan | Status | Summary |
|---|---|---|
| [Personal collections (#201)](201-personal-collections.md) | `DEPLOYED`, partly proven | Six phases, all built. The user story works end to end on live infrastructure. Restore, the limits firing, and the graph leg are built but unexercised. |
| [Observability (#427)](427-observability.md) | `IN PROGRESS` — 6 of 9 items done | The API could name a failure but not explain it. W1/W2/W6/W7 and the runtime log-level endpoint are deployed; W3 is being built. |
| [Backlog](backlog.md) | `OPEN` | 25 open issues grouped by theme, with the ones that bite hardest called out. |

---

## Releases

| Tag | What it was |
|---|---|
| `v1.3.0` | The production baseline, **retro-tagged**. Three tenants had served this commit since 2026-08-14 as an untagged SHA — the revert position was a bare hash nothing identified as production. |
| `v1.4.0` | Personal collections, plus every defect the live validation found. |
| `v1.4.1` | A 401 with no credential means signed out, not "that credential was rejected". |
| `v1.4.2` | The API can now explain its own failures. Request ids, a working `LOG_LEVEL`, Elasticsearch error handling, runtime log control. |

Revert path is `git checkout <tag>` in the tenant worktree, then restart the API and the
Vite server by the pids recorded at launch. Never by process-name pattern (#402).

---

## How work gets done here

Recorded in [CLAUDE.md](../../CLAUDE.md), and worth restating because this session tested it:

- **Implementation goes to subagents**, one task per worktree under `~/Development/worktrees/`.
- **Fable plans and reviews.** The reviewer verifies independently — runs the suite, reproduces
  the claim, probes the failure mode — rather than reading the diff and agreeing.
- **Every PR gets a review before merge.** This session drifted off Fable onto another model
  after a transient model error and did not drift back for many PRs; when Fable returned it
  immediately found two surviving mutants, a false evidence claim, and a production default in
  the test harness that six prior reviews had walked past. The convention exists because the
  reviewer should not be the model that wrote the code.

### The failure mode this session kept finding

**Six times**, a test passed because something *ambient* satisfied its assertion rather than the
behaviour it named:

| what satisfied it | where |
|---|---|
| no root log handler, so nothing was captured | the redaction test |
| a constructor default equal to the asserted value | `kind == "error"` |
| the host timezone happening to be UTC-negative | the timestamp test |
| a dampened logger being restored anyway | the dropped-override test |
| no timer armed to begin with | the "422 changes nothing" test |
| a `ResponseRecorder`'s live header map | the Go stamping-point test |

Every one was found by **mutating the implementation and re-running**, never by reading. The
diagnostic that would have caught all six, from the W7 implementer:

> The test and the implementation were both reading the same in-memory object, so no round trip
> ever had to happen. Ask of any new test: **what is the narrowest mutation of the implementation
> that this would still pass?**

### Recurring defect class: a default that resolves to production

Six instances found so far — #363, #369, #392, #407, and both halves of #432. The newest are the
worst, because they are in the test harness itself: `pytest` imports production code unless
`PYTHONPATH` is pinned, and the Elasticsearch integration test defaults to the production cluster.
A suite that may import production code and may write to a production cluster cannot be the
evidence base for anything else.
