# Use-case test matrix

**Status:** second draft, 2026-08-26. Written after the personal-collections live validation, in
which six defects reached live infrastructure that the whole test suite did not catch — seven, with
#419/#420, found days after the first draft and by the same method. Revised
after #416, #418, #421, #423 and #424 landed — rows they closed are marked, rows they only
half-closed are demoted rather than ticked. Counts re-measured on `main` at `059666b`.

## Why this exists

The suite is not small and it is not bad. It is aimed at the wrong axis.

| layer | test functions | collected | what it proves |
|---|---|---|---|
| `python/tests/{unit,api,ingestion}` | 2,311 | 2,726 | a function or a route behaves, in isolation |
| `python/tests/eval` | 176 | 202 | a retrieval/eval helper behaves |
| `python/tests/integration` | 9 (5 files) | 9 | a CWL step runs |
| `python/tests/perf` | 35 (20 files) | 29 under `-m perf` | a budget holds (#355) |
| `conformance/` | 73 | 104 | responses match the contract |

(The Python rows sum to 2,531 of the tree's 2,539 functions; the other 8 are
`python/tests/test_document_registry.py`, which sits at the top level.)

*How counted:* "test functions" is `def test_` / `async def test_`; "collected" is
`pytest --collect-only -q`, which is larger wherever a test is parametrised. The first draft's
"2,485 unit/API tests" was the function count for the **entire** `python/tests/` tree at the time
(`2778813`) — not for `{unit,api,ingestion}`, which was 2,257 — and it double-counted the
`integration` and `perf` rows listed beside it. "73 conformance" was the function count; the
collected count was 104 then and is 104 now. Re-measure both before quoting them.

Every one of those asks *"does this call behave?"*. **None of them asks *"can a user get their
work done?"*** — a sequence of calls, in order, where each step's real side effects are the next
step's input. Every defect below was found by a person doing the sequence by hand:

| defect | what it was | why the suite missed it | now |
|---|---|---|---|
| #414 | the **second** upload into a collection is a hard 500 | nothing ever uploaded twice | fixed (#416) |
| #408 | folder metadata silently not persisted | the fake accepted what the real service discards | fixed (#416) |
| #415 | a failed upload wedges all ingest for 6 h | no test asserts recovery *after* a failure | part-fixed (#418) |
| #407 | a dev ingest wrote to **production** stores | the workflow's defaults were never exercised | open |
| GoWe#172 | every delivered archive was byte-corrupt | nothing read an artifact back and verified it | fixed |
| #404 | the graph driver was installed nowhere | tests import it; the deployed image never had it | open |
| #419/#420 | a user's every question 404'd, and the UI named a collection it was not querying | **every test caller could read the tenant default** — the branch was unreachable, not unasserted | fixed (#421/#423/#424) |

Four patterns repeat, and they are what this matrix is designed to break:

1. **Step two is never taken.** Create-then-create, upload-then-upload, fail-then-retry.
2. **A fake encodes a contract the real service does not honour.** A test double that accepts
   what the live service rejects proves nothing. Doubles must encode *observed* behaviour.
3. **The artifact is never read back.** "The writer reported success" is not evidence; the
   engine reported `delivered` while shipping corrupt bytes.
4. **There is only ever one kind of caller.** #419 was not a missing assertion — the branch was
   *unreachable* in every suite, because no test principal existed who could not read the tenant
   default. See **Persona** below.

## How to read the matrix

**Layer** — where a case can honestly live:
`F` fake/unit (Python or frontend) · `C` conformance (HTTP, contract-shaped) · `L` live (real
stores, engine, Workspace). Dot-joined means the case needs **each** listed layer; a row missing
one of its own layers is at best ⚠️. Prefer the cheapest layer that can actually fail. A case
marked `L` genuinely cannot be proven below it — that is a claim to defend, not a default.

**State** — ✅ covered · ⚠️ partial · ❌ absent.

**Bold** — on the use case or in its note — marks a case that reached live infrastructure as a
defect.

**Persona** — a journey is only covered once it is proven for **each persona that will take it**.
Where more than one does, the row's State is the **weakest** of them: a ✅ that holds only for P1
is a ⚠️. This is not a licence to demote every row — most rows have exactly one persona, and only
rows whose behaviour actually *branches* on the caller are audited against the list.

| | Persona | Readable set | Not | Why it exists |
|---|---|---|---|---|
| **P1** | `owner` | owns what it uses; **can** read the collection the registry pointer names | — | the caller every test was written for |
| **P2** | `caller_without_default_access` | **exactly one** collection, which is **not** the registry pointer's target; no shares | not admin; target not `public`; no `TENANT_COLLECTIONS` allowlist; auth configured | **the #201 default new-user state**, not an edge case. #419/#420 were invisible until a live user landed in it |
| **P3** | `caller_with_nothing` | empty | as P2 | a P2 in the seconds before provisioning; the `default: ""` case (#421) |
| **P4** | `admin` | everything, via the authz bypass | — | must never be the *only* persona a row is proven with — the bypass hides exactly what P2 and P3 find |

P2's four "not"s are load-bearing: an admin bypasses `authz.resolve_access` entirely, a
`public` target is readable by everyone, a `TENANT_COLLECTIONS` allowlist takes a different
branch, and with auth unconfigured `api/access.py::filter_readable` is a no-op — any one of them makes a P2 test vacuous. Reference
implementations: `python/tests/api/conftest.py:215` (pytest fixture, which asserts its own
preconditions) and `frontend/src/lib/collectionFixtures.ts` (`LISTING_WITHOUT_DEFAULT`).
P2 **cannot be expressed in `conformance/` today** — the suite's only principal axis is role, and
`run_authz_keyed.sh` wires its "non-admin" key to the same value as the primary. That gap is why
several rows below are ⚠️ rather than ✅.

---

## A. Identity and access

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| A1 | Sign in with a BV-BRC token; a user row appears on first auth | C·L | ✅ | `conformance/test_identity.py` |
| A2 | An expired/invalid token is refused everywhere, including mid-job | F·L | ⚠️ | mid-job expiry unproven |
| A3 | A read-only service account cannot create a collection | F·C | ⚠️ | #287; already principal-aware. F only — conformance's create round-trip **skips** a non-admin caller instead of asserting the refusal (`test_collections.py:118`) |
| A4 | Job status is not readable across tenants | F·C | ⚠️ | #130; already principal-aware. F only — conformance explicitly excludes `GET /v1/ingest/{job_id}`: *"a real assertion here still needs a two-tenant fixture this single-tenant probe harness doesn't have"* (`test_authz.py:122-128`) |
| A5 | An API-key admin cannot perform a gowe ingest (needs a bearer) | F | ❌ | surfaced by #415; undocumented |

## B. Collection lifecycle

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| B1 | Create a collection with the server default build spec | C | ✅ | |
| B2 | Create a **second** collection; both usable | C | ⚠️ | "step two" |
| B3 | Create at the per-owner quota → refused (#384) | F·C | ⚠️ | not exercised at the boundary live |
| B4 | Create at the tenant's active bound → evict one or 507 (#379/#397) | F·L | ⚠️ | |
| B5 | Re-create an id that was purged → clean, no stale state | C·L | ❌ | **the archive folder outlives the purge** |
| B6 | `default` resolves as a pointer (#390), for a caller who **can** read the target (P1) | F·C | ✅ | `test_default_pointer.py`; conformance asserts `default ∈ collections[]` |
| B6a | **…and for one who cannot (P2): listing and query agree on the target** | F·C·L | ⚠️ | #419 fixed (#421/#423), live-verified; F-layer only — P2 is inexpressible in conformance |
| B7 | Rename / transfer; shares follow correctly | F·C | ⚠️ | #281 open |

## C. Ingest — the sequence that broke

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| C1 | Upload a batch; job completes; chunks in both legs | C·L | ⚠️ | conformance **never uploads a file** |
| C2 | **Upload a second batch into the same collection** | F·C·L | ⚠️ | #414 fixed (#416), F-tested and live-proven; the C leg is blocked on C1 |
| C3 | Upload a third and fourth batch; versions 1..n all replayable | L | ❌ | only n=2 proven |
| C4 | A second upload while one is in flight → 429, not corruption | F·C | ⚠️ | #377 (`single_inflight_ingest`); `test_upload_hardening.py` — conformance sends no 429 |
| C5 | **After a failed upload, the next upload is admitted** | F·C | ⚠️ | #418 landed W1–W3 (`test_ingest_job_terminalization.py`); #415 open for W4–W10; C leg blocked on C1 |
| C6 | Owner cancels their own stuck job | C | ❌ | endpoint still does not exist (#415) |
| C7 | Oversized file / too many files / wrong content type → refused | F·C | ⚠️ | #377, `test_upload_hardening.py`; C leg blocked on C1 — conformance sends no upload to refuse |
| C8 | A scanned, text-free PDF fails its item without sinking the batch | F·L | ✅ | #382 |
| C9 | Ingest at the chunk cap → whole job refused, nothing partial | F·L | ⚠️ | never fired live |
| C10 | The submission always carries explicit store targets | F·C | ❌ | **#407 — the defaults still point at production** |
| C11 | Ingest survives an engine restart mid-job | L | ❌ | |

## D. The archive — read it back, always

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| D1 | An archive is written after every ingest and delete | L | ✅ | |
| D2 | **Every file's sha256 matches its manifest, on the downloaded copy** | L | ✅ | caught GoWe#172 |
| D3 | Folder metadata is readable back and names the format | L | ✅ | #408 fixed (#416), verified live |
| D4 | A corrupt or truncated archive is **refused** on restore, not warned | F·L | ⚠️ | fake-tested; never live |
| D5 | A manifest whose spec hash differs from the registry is refused | F | ✅ | |
| D6 | A user editing their own archive folder cannot corrupt a restore | F·L | ⚠️ | the folder is user-writable by design |

## E. Dormancy and restore

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| E1 | Evict → dormant; queries answer 503 + Retry-After | F·C | ⚠️ | #376; F only — conformance's only eviction call is `dry_run`, so no collection is ever dormant there |
| E2 | **Cold restore rebuilds a dormant collection; counts match** | L | ❌ | built, never run live |
| E3 | Restore replays versions in order, tombstones included | F | ⚠️ | |
| E4 | Evict → restore → query returns the same results as before | L | ❌ | the round trip |
| E5 | Restore at the active bound evicts or 503s, never exceeds | F | ✅ | #397 |
| E6 | An evicted collection's triples survive (they are not archived) | F | ✅ | deliberate interim — `ops/evict.py` does not pass a graph store because the archive has no triples leg. #380 (open) tracks the delete, gated on #350 |

## F. Using the collection

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| F1 | Query a named collection I can read; results are mine only | C | ⚠️ | proven only as P1 — the conformance credential also owns the registry default |
| F2 | Fused query across N readable collections, capped (#395) | C | ⚠️ | #395; the readable-set filter is untested — the conformance caller can read everything |
| F3 | A shared collection reports a correct count (#396) | C | ✅ | #274 |
| F4 | Prev/next expansion returns neighbours, ranking unchanged (#385) | C | ✅ | |
| F5 | Retrieval quality on a small library is not degraded | L | ⚠️ | #200 — gate inconclusive |
| F6 | **Omitting `collection` serves the id `GET /v1/collections` advertised as `default`** | F·C·L | ⚠️ | #419 fixed (#423), live-verified against the affected user; `test_default_collection_resolution.py`. C leg needs the P2 persona |
| F7 | **The UI's collection chip names the collection the request actually targets** | F | ✅ | #420 fixed (#424) — `collectionTarget.ts`, one function feeds both label and request body |

## G. Deletion

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| G1 | Purge removes registry, vectors, text, graph, manifest | C·L | ✅ | C proves the registry leg (create→`?purge=true`→gone from listing, admin-gated); the physical legs are the L claim |
| G2 | Purge does **not** remove the owner's Workspace archive | L | ✅ | deliberate |
| G3 | Deleting documents writes a tombstone; restore replays it | F | ⚠️ | |
| G4 | Purge of a shared-store collection refuses (#228) | F | ✅ | |

## H. Deployment — where three of six defects lived

| # | Use case | Layer | State | Note |
|---|---|---|---|---|
| H1 | The deployed worker image can import every configured backend | L | ❌ | **#404 — tests import it, the image never had it** |
| H2 | Workers start with their env and secret files | L | ✅ | #412 |
| H3 | A tenant's config never resolves to another tenant's stores | F·L | ❌ | **the #363/#369/#392/#407 family** |
| H4 | An API restart leaves no job stranded | F | ⚠️ | `jobstore.fail_interrupted` — scope unverified |

---

## What to build first

Ordered by evidence, not by ease.

1. **C10 + H3 — "a default must never resolve to production."** Four occurrences (#363, #369,
   #392, #407), **all four still open**. Make store targets required inputs with no default, and
   add a test asserting that a submission built for a tenant carries *that tenant's* URLs. This is
   the highest-value test in the repo and it does not exist.
2. **C1 — make conformance actually upload a file.** Conformance is the contract gate and it has
   never sent a multipart body. This is now the single blocker on the `C` leg of C2, C4 *and* C5:
   three rows sit at ⚠️ waiting on it, and every one of them is a defect that already shipped.
3. **A second principal in `conformance/` — P2, and a second tenant.** The suite has one credential
   and one tenant, so **B6a, F6, F1, F2, A3 and A4** are all held below ✅ by the same missing
   fixture. `test_authz.py:122-128` already admits it in a comment ("needs a two-tenant fixture
   this single-tenant probe harness doesn't have"), and `test_collections.py:118` skips rather than
   asserts for the same reason. #419 was found by a user and not by a test for exactly this reason.
4. **C6 — the cancel endpoint, and the rest of #415.** #418 closed the lockout (W1–W3); W4–W10 and
   the owner-facing cancel are still open, so C5 has no recovery path a user can drive.
5. **E2 + E4 — the restore round trip.** The archive exists to be restored; that has never happened.
6. **B5 — re-create after purge.** The archive folder survives a purge by design, so a re-created
   collection meets a folder it did not make. Nothing tests that.
7. **H1 — image capability check.** A one-line import probe per configured backend, run against the
   *deployed* image, would have caught #404 before it blocked a phase.

**Off this list since the first draft:** the second-upload defect (#414/#408, closed by #416) and
the ingest lockout's terminalisation half (#415 W1–W3, closed by #418). Neither was on the list
because it was ranked — both were fixed under their own issues. That is the honest reading of this
ranking's limits: it ranks *journeys*, so it cannot see a hole that lives on the persona axis.
#419/#420 were the largest such hole and this list did not contain them at any position.

## Rules this matrix encodes

- **A test double must encode observed behaviour of the real service, including its refusals.**
  The Workspace fake that accepted dotted keys is why #414 shipped.
- **Read the artifact back.** Never accept the writer's report as evidence.
- **Always take step two.** Most of these defects are invisible on the first call.
- **A row proven only as P1 or P4 is not proven.** Where behaviour branches on who is calling, the
  second caller is a step in the journey exactly as much as the second upload is. #419 sat behind
  that branch in every layer at once, which is why nothing below a live user could see it.
- **Prefer the cheapest layer that can fail.** `L` is a claim that a case cannot be proven below
  it — defend it or move the case down.
- **Re-measure the counts before quoting them.** The first draft's did not survive contact with
  `pytest --collect-only`.
