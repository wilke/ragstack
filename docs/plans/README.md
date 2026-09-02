# Plans

Working plans, their status, and what is being worked on right now. Each plan records
what was decided **and why**, so a decision can be re-argued rather than re-discovered.

**Status vocabulary:** `DONE` shipped and merged · `DEPLOYED` also running on tenants ·
`IN PROGRESS` being built now · `OPEN` planned, not started · `BLOCKED` waiting on something
named · `ABANDONED` decided against, with the reason kept.

Last updated 2026-08-27. Fleet: **all four tenants on `v1.5.2`** — `dev`, `demo`, `lucid-next`,
`asm-next`, upgraded in that order. Revert point is `v1.5.1` for `dev` and `v1.5.0` for the other
three; for those three a plain `git checkout` + restart is sufficient (no config coupling — the
`GOWE_WORKFLOW_INPUTS_JSON` dependency is gowe-only, and `dev` is the only gowe tenant).

---

## Currently working on

| | |
|---|---|
| **#422 PR-4 (W12–W14)** | `NOT STARTED`. |
| **#415 W4–W10** | `NOT STARTED`. A wedged job row still has no self-service recovery — no cancel endpoint, and the admin exemption cannot work on the gowe path (it needs a BV-BRC bearer). |
| **#454** | `OPEN`. Five write-path CLI scripts still default their store URLs to production. |

**Shipped in `v1.5.2` and live on all four tenants:** #452 (the P2 conformance persona), #447 and
#453 (the documents read paths and the writable-set ingest picker).

**What the deploy did and did not prove.** Every tenant restarted clean, the registry line is
byte-identical to its own baseline on all four, and `/v1/collections` + `/v1/documents` are
byte-identical before and after for every key tested. That is a no-regression result.

It is **not** an acceptance test of #422, and no tenant can be one:

- The API-key callers on `dev`, `demo` and `lucid` can all already read their tenant's pointer
  target, so the caller-aware pick resolves to the same collection it did before. The divergence
  #447 fixes only appears for a caller *without* that grant.
- On `asm-next` those callers exist (the #419 cohort), but they authenticate with BV-BRC bearer
  tokens, not API keys, so the journey cannot be driven from a shell. **The user-facing claim —
  that they can now list documents — is still unconfirmed against a real session.**
- **The #453 ingest picker has had no live exercise on any tenant.** The only way to test it is to
  write, and `demo`/`asm` write to the *production* Qdrant and Elasticsearch. The C-layer proof
  (the review's ASGI experiments, including a mutation of the echo line) was judged sufficient
  rather than buying one echoed id with a production write.

Also unre-proven live: the multi-batch upload run that #414/#415 blocked. Those are fixed in code
and never re-run.

---

## Plans

| Plan | Status | Summary |
|---|---|---|
| [Personal collections (#201)](201-personal-collections.md) | `DEPLOYED`, partly proven | Six phases, all built. The user story works end to end on live infrastructure. Restore, the limits firing, and the graph leg are built but unexercised. |
| [Observability (#427)](427-observability.md) | `COMPLETE` — 9 of 9 items (W5 deferred) | The API could name a failure but not explain it. W1/W2 and the log-level endpoint shipped in `v1.4.2`; W3/W4/W6/W7 in `v1.5.0`; W8/W9 in `v1.5.1`. |
| [Chunking evaluation](chunking-evaluation.md) | `PROPOSED` | Redo the comparison: the ground truth is known-item-by-title and flatters lead chunks, the baseline may not be the shipping chunker, and the token counter can silently resize every arm by 40%. Distractor ladder for corpus size — subsampling destroys the qrels, as the G1 pilot found. |
| [Date filtering](date-filtering.md) | `SCOPED`, not started | A range operator in the filter grammar, and a `year` backfill on `open-access`. `year` is the only temporal field and covers **14.8%** of that corpus; 8,408 chunks are dated in the future. |
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
| `v1.5.2` | The caller's own collection, on every surface. `/v1/documents` lists what the *caller* can read (#447) and an omitted `collection` on ingest targets what they can *write* (#453); conformance can finally express a second principal (#452). No config change, no UI redeploy. |
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

**Eight instances**, by the counting rule below — #363, #369, #392, #407, both halves of #432,
`conformance/conftest.py`'s `RAGSTACK_BASE_URL` default (#405), and the write-path CLI scripts
(#454). The harness ones are the worst, because they undermine every other claim: `pytest`
imports production code unless `PYTHONPATH` is pinned, the Elasticsearch integration test
defaulted to the production cluster, and the conformance suite — which *creates and deletes
collections* — defaulted to `http://localhost:8000`, the legacy production API's address on this
host. A suite that may import production code, write to a production cluster and provision
collections on a production API cannot be the evidence base for anything else.

**Counting rule** (so the number stops drifting every time someone adds a line): one entry per
*distinct defect* — issue-tracked, in mainline code or the test harness, whose default resolves to
a live production store or API on this host — counted **once however often it is rediscovered**.
#451 was #392 found a second time from a different direction and is closed as a duplicate rather
than counted twice. Deliberately excluded: example inputs and `.env.example` (documentation, not a
firing default), and runbook prose.

The shape is the same in all eight: the default **is** the documented convention (port 8000 is
Python, `localhost:9200` is Elasticsearch), and the convention is right — on a laptop. It is a
defect here because the deployment host is also the development host, so the convention and
production name the same address. The fix is never to change the convention; it is to make the
value *required*, and to keep the convention in the invocation (a Make target), where it cannot
fire by accident.

One caution about the evidence: whether a given address is *listening right now* is not what makes
these defects. `:8000` was down when #405's fix was reviewed. The defect is that the value names
production, and the fleet's uptime is not a safety property of the test suite.
