# Observability (#427)

**Status:** `IN PROGRESS` — 6 of 9 items done, all deployed as part of `v1.4.2`.

## Why this exists

A real user on a production tenant got a 503. The log named the immediate cause in one line —
a Qdrant `ReadTimeout` at 30s on a 47.6M-point, 192-segment collection. **The underlying cause
was unknowable from what we recorded.** A cold read on that collection measures ~14s and a warm
one 0.02s; the failing request exceeded 30s, and nothing we kept could distinguish a colder cache
from concurrent load from a query vector touching more segments.

**Acceptance criterion — the whole plan is designed against this, not against "add logging":**

> The next occurrence of this incident is explainable after the fact.

## What the plan found that the issue did not

Checking what existed rather than what the issue described turned up four things nobody had
reported:

1. **The root logger had no handler at all** and sat at WARNING — so every `log.info()` in the
   API was silently discarded, and warnings fell through to `logging.lastResort`: no timestamp,
   no level, no logger name. That is why the incident line had no timestamp of its own.
2. **`LOG_LEVEL` was honoured nowhere.** Defined in config, echoed from `GET /v1/config`, written
   by tenant provisioning — and `grep setLevel|basicConfig|dictConfig` hit only a CLI.
3. **Only Qdrant raised `StoreUnavailable`.** Elasticsearch had no error handling on any read, so
   an ES timeout on the *text* leg of a hybrid query was a bare 500 with a traceback while the
   identical failure on the vector leg produced a fully described 503. **Half of every hybrid
   query was uninstrumented and which half you got was chance.** The incident hit the
   instrumented one.
4. **`ELASTICSEARCH_TIMEOUT` did not exist**, so the text leg sat at the client default of 10s
   while `QDRANT_TIMEOUT` had been raised to 60s as the incident mitigation. That mitigation
   covered one leg of two.

## Items

| # | Item | Status | Notes |
|---|---|---|---|
| **W1** | Logging config, request context, request id | `DEPLOYED` | PR #428. Fixed (1) and (2) above on the way. A naive implementation would have **crashed dev and demo at startup** — both carry `LOG_LEVEL=info` and `setLevel('info')` raises. Now `.upper()` + fall back to INFO with a warning; a bad log level must never be fatal. |
| **W2a** | Structured store-failure fields | `DEPLOYED` | PR #429. `kind` + `elapsed_s` on `StoreUnavailable`, alongside the existing message (unchanged — it is what made the incident diagnosable in one grep). `ConnectTimeout` maps to `unreachable`, **not** `timeout`, so the UI never promises a warm read for a store never reached. |
| **W2b** | Elasticsearch parity | `DEPLOYED` | PR #429. Fixed (3) and (4). |
| **—** | Runtime log-level endpoint | `DEPLOYED` | PR #430. `GET`/`PUT`/`DELETE /v1/admin/log-level`, admin-gated on all verbs, audited on a WARNING-pinned logger so a change cannot hide itself. Not in the original plan; added because changing the level required a restart. |
| **—** | TTL / auto-revert | `DEPLOYED` | PR #431. `ttl_seconds` so DEBUG turns itself off. DEBUG costs ~15 log lines per outbound call and releases dampening: one `/v1/query` goes from ~3 lines to 75–210, and without a TTL it survives until restart. |
| **W6** | Error contract + UI 503 | `DONE` | PR #434. The repo's first error schema. A user now learns whether retrying will help. Found that the 503 is **overloaded** — store-unavailable, authz fail-closed, dormant, at-capacity — and that the contract never documented the store cause at all. |
| **W7** | Go request-id parity + first Go CI job | `DONE` | PR #433. Includes an **ADR-0006 amendment** — the scaffold freeze said "neither extended nor deleted", and this extends it. Recorded as a bounded exception with a falsifiable test. |
| **W3** | Per-stage query timings | `IN PROGRESS` | The load-bearing item. ~10 timing points across the retriever and query router. |
| **W4** | Latency rollup line | `OPEN` | p50/p95 per collection, no contract change. Closes the "is the bound creeping" question. |
| **W5** | `GET /v1/admin/stats/latency` | `DEFERRED` | W4's rollup meets the acceptance bullet without a contract change. Cheaper now that the log-level endpoint is a template, but not more necessary. |
| **W8** | Runbook + ADR amendment | `OPEN` | `docs/runbooks/tracing-a-503.md`; amend ADR-0006 to name the instrument that now exists. |
| **W9** | Qdrant post-mortem probe | `OPEN` | Opt-in, default off. Probes optimizer/indexing churn — a **different** candidate cause from the cold-cache one everyone assumes. Now near-trivial: `collection_health()` already exists. |

**Minimum remaining to meet the acceptance criterion: W3 + W4.**

## Decisions taken

| # | Decision | Chosen | Why |
|---|---|---|---|
| D1 | Flip `access_log_replaced` to `True` in W3 | yes | Line count is invariant — every request already produces one uvicorn access line and the summary line replaces it 1:1. Bytes/line grow ~3–4×. The flip and the promotion must land together or the count-invariance claim is false. |
| D2 | W7 vs the ADR-0006 scaffold freeze | proceed, recorded | The freeze's own rationale is that conformance keeps the port possible; a scaffold that cannot pass the contract's tests is a decaying option, not a preserved one. #419 is the counterweight to a literal reading. |
| D3 | `tenant_inflight` on the summary line | drop | `tenant_max_concurrency` defaults to 0 on every tenant, so it would render `-` forever. |
| D4 | `store` in `error.json` | no | Nothing consumes it; `detail` already names the store in prose. Additive later if wanted. |
| D7 | `client_disconnected` level | INFO | Not a fault, so never WARNING; but a disconnect during a 30s query is evidence, so not DEBUG. |
| — | Log format | logfmt, with a tested `json` switch | No shipper or aggregator is deployed; the consumer is a human with `grep`. The switch keeps it reversible. |
| — | Inbound `X-Request-ID` | generate ours, record theirs as `upstream_rid`, never echo | Removes the trust question entirely; the charset cap closes log injection. |
| — | OpenTelemetry / Prometheus / usage table | rejected for now | The SDK and instrumentation are already declared as extras and imported nowhere; no collector, no scraper. Spans exported to nothing explain nothing after the fact. |

## What this will still not do

Even complete, a repeat tells you: which leg, which collection, how long, which stages were fine,
how many requests were in flight, and that hour's p95. That **rules concurrency in or out** and
**rules the LLM/rerank/embed legs out** — both impossible today. It does **not** measure host
page-cache state, so "cold cache" versus "an unlucky query vector touching more segments" stays a
hypothesis. What changes is that it becomes a *testable* one: the retry's own line, seconds later
with `vector_ms=20`, is evidence for cache; `inflight=9` is evidence for load.

Closing that properly needs Qdrant-side telemetry on the store host, which is not this repo.
