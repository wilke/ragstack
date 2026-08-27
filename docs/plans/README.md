# Plans

Working plans, their status, and what is being worked on right now. Each plan records
what was decided **and why**, so a decision can be re-argued rather than re-discovered.

**Status vocabulary:** `DONE` shipped and merged · `DEPLOYED` also running on tenants ·
`IN PROGRESS` being built now · `OPEN` planned, not started · `BLOCKED` waiting on something
named · `ABANDONED` decided against, with the reason kept.

Last updated 2026-08-27. Fleet: **`dev` on `v1.5.1`; `asm-next`, `lucid-next`, `demo` on `v1.5.0`**.
`dev` runs ahead on purpose — `v1.5.1` carries the #407 boot refusal, which needs a one-time
`tenant.env` edit ([runbook](../runbooks/upgrade-407-remove-gowe-store-urls.md)). Only `dev` was
affected: it is the only tenant setting `INGEST_BACKEND=gowe`, and the refusal lives on that path. The other three move once `dev` is accepted.

---

## Currently working on

| | |
|---|---|
| **#422 PR-2 / PR-3 — the remaining "caller's default collection" copies** | `IN PROGRESS`. The read paths (`GET`/`DELETE /v1/documents`) and the writable-set ingest picker. Until both land, the user from the #419 incident can ask questions but **still cannot list documents or upload**. |
| **#405 — a second principal in conformance** (#432 PR-2) | `IN PROGRESS`. Four distinct keys, `RAGSTACK_API_KEY_P2`, and a `client` fixture that actually authenticates — the persona axis P1–P4 exists in the matrix but cannot be expressed over HTTP today. |

Both are being built from Fable-reviewed plans, one worktree each. #422 is the user-facing one.

Immediately after: deploy whichever lands first, then move `asm-next` / `lucid-next` / `demo`
from `v1.5.0` to the current tag — they have been held only because `v1.5.1` carries nothing
user-facing.

---

## Plans

| Plan | Status | Summary |
|---|---|---|
| [Personal collections (#201)](201-personal-collections.md) | `DEPLOYED`, partly proven | Six phases, all built. The user story works end to end on live infrastructure. Restore, the limits firing, and the graph leg are built but unexercised. |
| [Observability (#427)](427-observability.md) | `COMPLETE` — 9 of 9 items (W5 deferred) | The API could name a failure but not explain it. W1/W2 and the log-level endpoint shipped in `v1.4.2`; W3/W4/W6/W7 in `v1.5.0`; W8/W9 in `v1.5.1`. |
| [Backlog](backlog.md) | `OPEN` | 25 open issues grouped by theme, with the ones that bite hardest called out. |

---

## Releases

| Tag | What it was |
|---|---|
| `v1.3.0` | The production baseline, **retro-tagged**. Three tenants had served this commit since 2026-08-14 as an untagged SHA — the revert position was a bare hash nothing identified as production. |
| `v1.4.0` | Personal collections, plus every defect the live validation found. |
| `v1.4.1` | A 401 with no credential means signed out, not "that credential was rejected". |
| `v1.4.2` | The API can now explain its own failures. Request ids, a working `LOG_LEVEL`, Elasticsearch error handling, runtime log control. |
| `v1.5.0` | A 503 you can explain. One greppable line per request with per-stage timings, a five-minute p50/p95 rollup in bucket upper bounds, and a 503 body that says whether retrying will help. |
| `v1.5.1` | The runbook for reading those lines, an opt-in Qdrant post-mortem probe (default off), the API seeding its own ingest store targets (#407), and a harness that proves which `ragstack` it imported (#432 PR-1). **Deploying it requires removing `qdrant_url`/`es_url` from `dev/config/tenant.env`'s `GOWE_WORKFLOW_INPUTS_JSON`** — the API refuses to boot otherwise, by design. Those were dev's only two keys, so the whole line went; the blob may stay for genuine per-deployment extras. |

Revert path is `git checkout <tag>` in the tenant worktree, then restart the API and the
Vite server by the pids recorded at launch. Never by process-name pattern (#402).

Reverting `dev` below `v1.5.1` is a checkout **plus a `tenant.env` edit** — three things that
must end mutually consistent, but only two actions. The CWL rides the checkout, because
`GOWE_WORKFLOW_CWL` points *inside* the worktree; the separate action is restoring
`GOWE_WORKFLOW_INPUTS_JSON` from `dev/config/tenant.env.pre-v1.5.1.bak` (mode 600). It is
load-bearing again below `v1.5.1`, because the pre-#407 CWL restores the production store
defaults — so a code revert without the config restore ingests into production. (If
`GOWE_WORKFLOW_CWL` is ever pointed outside the worktree, this becomes a genuine triple.)

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
