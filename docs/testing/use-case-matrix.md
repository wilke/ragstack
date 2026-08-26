# Use-case test matrix

**Status:** first draft, 2026-08-26. Written after the personal-collections live validation, in
which six defects reached live infrastructure that 2,485 unit/API tests and 73 conformance tests
did not catch.

## Why this exists

The suite is not small and it is not bad. It is aimed at the wrong axis.

| layer | tests | what it proves |
|---|---|---|
| `python/tests/{unit,api,ingestion}` | ~2,485 | a function or a route behaves, in isolation |
| `python/tests/integration` | 5 files | a CWL step runs |
| `python/tests/perf` | 23 files | a budget holds (#355) |
| `conformance/` | 73 | responses match the contract |

Every one of those asks *"does this call behave?"*. **None of them asks *"can a user get their
work done?"*** — a sequence of calls, in order, where each step's real side effects are the next
step's input. Every defect below was found by a person doing the sequence by hand:

| defect | what it was | why the suite missed it |
|---|---|---|
| #414 | the **second** upload into a collection is a hard 500 | nothing ever uploaded twice |
| #408 | folder metadata silently not persisted | the fake accepted what the real service discards |
| #415 | a failed upload wedges all ingest for 6 h | no test asserts recovery *after* a failure |
| #407 | a dev ingest wrote to **production** stores | the workflow's defaults were never exercised |
| GoWe#172 | every delivered archive was byte-corrupt | nothing read an artifact back and verified it |
| #404 | the graph driver was installed nowhere | tests import it; the deployed image never had it |

Three patterns repeat, and they are what this matrix is designed to break:

1. **Step two is never taken.** Create-then-create, upload-then-upload, fail-then-retry.
2. **A fake encodes a contract the real service does not honour.** A test double that accepts
   what the live service rejects proves nothing. Doubles must encode *observed* behaviour.
3. **The artifact is never read back.** "The writer reported success" is not evidence; the
   engine reported `delivered` while shipping corrupt bytes.

## How to read the matrix

**Layer** — where a case can honestly live:
`F` fake/unit · `C` conformance (HTTP, contract-shaped) · `L` live (real stores, engine, Workspace).
Prefer the cheapest layer that can actually fail. A case marked `L` genuinely cannot be proven
below it — that is a claim to defend, not a default.

**State** — ✅ covered · ⚠️ partial · ❌ absent.

---

## A. Identity and access

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| A1 | Sign in with a BV-BRC token; a user row appears on first auth | C·L | ✅ | `test_identity.py` |
| A2 | An expired/invalid token is refused everywhere, including mid-job | F·L | ⚠️ | mid-job expiry unproven |
| A3 | A read-only service account cannot create a collection | F·C | ✅ | #287 |
| A4 | Job status is not readable across tenants | F·C | ✅ | #130 |
| A5 | An API-key admin cannot perform a gowe ingest (needs a bearer) | F | ❌ | surfaced by #415; undocumented |

## B. Collection lifecycle

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| B1 | Create a collection with the server default build spec | C | ✅ | |
| B2 | Create a **second** collection; both usable | C | ⚠️ | "step two" |
| B3 | Create at the per-owner quota → refused (#384) | F·C | ⚠️ | not exercised at the boundary live |
| B4 | Create at the tenant's active bound → evict one or 507 (#379/#397) | F·L | ⚠️ | |
| B5 | Re-create an id that was purged → clean, no stale state | C·L | ❌ | **the archive folder outlives the purge** |
| B6 | `default` resolves as a pointer (#390) | F·C | ✅ | |
| B7 | Rename / transfer; shares follow correctly | F·C | ⚠️ | #281 open |

## C. Ingest — the sequence that broke

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| C1 | Upload a batch; job completes; chunks in both legs | C·L | ⚠️ | conformance **never uploads a file** |
| C2 | **Upload a second batch into the same collection** | F·C·L | ✅ | #414 — regression test added |
| C3 | Upload a third and fourth batch; versions 1..n all replayable | L | ❌ | only n=2 proven |
| C4 | A second upload while one is in flight → 429, not corruption | F·C | ✅ | #382 |
| C5 | **After a failed upload, the next upload is admitted** | F·C | ❌ | **#415 — the 6 h lockout** |
| C6 | Owner cancels their own stuck job | C | ❌ | endpoint does not exist (#415) |
| C7 | Oversized file / too many files / wrong content type → refused | F·C | ✅ | #377 |
| C8 | A scanned, text-free PDF fails its item without sinking the batch | F·L | ✅ | #382 |
| C9 | Ingest at the chunk cap → whole job refused, nothing partial | F·L | ⚠️ | never fired live |
| C10 | The submission always carries explicit store targets | F·C | ❌ | **#407 — the defaults still point at production** |
| C11 | Ingest survives an engine restart mid-job | L | ❌ | |

## D. The archive — read it back, always

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| D1 | An archive is written after every ingest and delete | L | ✅ | |
| D2 | **Every file's sha256 matches its manifest, on the downloaded copy** | L | ✅ | caught GoWe#172 |
| D3 | Folder metadata is readable back and names the format | L | ✅ | #408, fixed and verified |
| D4 | A corrupt or truncated archive is **refused** on restore, not warned | F·L | ⚠️ | fake-tested; never live |
| D5 | A manifest whose spec hash differs from the registry is refused | F | ✅ | |
| D6 | A user editing their own archive folder cannot corrupt a restore | F·L | ⚠️ | the folder is user-writable by design |

## E. Dormancy and restore

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| E1 | Evict → dormant; queries answer 503 + Retry-After | F·C | ✅ | #376 |
| E2 | **Cold restore rebuilds a dormant collection; counts match** | L | ❌ | built, never run live |
| E3 | Restore replays versions in order, tombstones included | F | ⚠️ | |
| E4 | Evict → restore → query returns the same results as before | L | ❌ | the round trip |
| E5 | Restore at the active bound evicts or 503s, never exceeds | F | ✅ | #397 |
| E6 | An evicted collection's triples survive (they are not archived) | F | ✅ | #380, deliberate |

## F. Using the collection

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| F1 | Query my own collection; results are mine only | C | ✅ | |
| F2 | Fused query across N readable collections, capped (#395) | C | ✅ | |
| F3 | A shared collection reports a correct count (#396) | C | ✅ | #274 |
| F4 | Prev/next expansion returns neighbours, ranking unchanged (#385) | C | ✅ | |
| F5 | Retrieval quality on a small library is not degraded | L | ⚠️ | #200 — gate inconclusive |

## G. Deletion

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| G1 | Purge removes registry, vectors, text, graph, manifest | C·L | ✅ | |
| G2 | Purge does **not** remove the owner's Workspace archive | L | ✅ | deliberate |
| G3 | Deleting documents writes a tombstone; restore replays it | F | ⚠️ | |
| G4 | Purge of a shared-store collection refuses (#228) | F | ✅ | |

## H. Deployment — where three of six defects lived

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| H1 | The deployed worker image can import every configured backend | L | ❌ | **#404 — tests import it, the image never had it** |
| H2 | Workers start with their env and secret files | L | ✅ | #412 |
| H3 | A tenant's config never resolves to another tenant's stores | F·L | ❌ | **the #363/#369/#392/#407 family** |
| H4 | An API restart leaves no job stranded | F | ⚠️ | `fail_interrupted` — scope unverified |

---

## What to build first

Ordered by evidence, not by ease. The top three each map to a defect that **already reached live
infrastructure**.

1. **C10 + H3 — "a default must never resolve to production."** Four occurrences (#363, #369,
   #392, #407). Make store targets required inputs with no default, and add a test asserting that
   a submission built for a tenant carries *that tenant's* URLs. This is the highest-value test in
   the repo and it does not exist.
2. **C5 + C6 — recovery after failure.** #415. Untestable today because the endpoint is missing;
   the plan is in flight.
3. **C1 — make conformance actually upload a file.** Conformance is the contract gate and it has
   never sent a multipart body. C2 is only covered because #414 forced it.
4. **E2 + E4 — the restore round trip.** The archive exists to be restored; that has never happened.
5. **B5 — re-create after purge.** The archive folder survives a purge by design, so a re-created
   collection meets a folder it did not make. Nothing tests that.
6. **H1 — image capability check.** A one-line import probe per configured backend, run against the
   *deployed* image, would have caught #404 before it blocked a phase.

## Rules this matrix encodes

- **A test double must encode observed behaviour of the real service, including its refusals.**
  The Workspace fake that accepted dotted keys is why #414 shipped.
- **Read the artifact back.** Never accept the writer's report as evidence.
- **Always take step two.** Most of these defects are invisible on the first call.
- **Prefer the cheapest layer that can fail.** `L` is a claim that a case cannot be proven below
  it — defend it or move the case down.
