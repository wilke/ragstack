# HA RAG Design — Consolidated Review & Contrast with RAGStack

**Date:** 2026-07-02
Companion to [ha-rag-reference-design.md](ha-rag-reference-design.md). This document
consolidates the three independent reviews of that design (security, UX, software
engineering) and contrasts the design — and the reviews' findings — against RAGStack's
**plan** ([SPEC.md](../../SPEC.md), [STATUS.md](../../STATUS.md), [ADR 0001](../adr/0001-execution-topology.md))
and its **current implementation**.

---

## Part A — Consolidated review

Three reviewers critiqued the reference design independently. **42 findings** total; the
striking result is how little they overlap — each lens found a different class of gap,
and together they show the design is strong on *reliability mechanics* and weak on
*trust boundaries* (both security and human).

### Cross-cutting theme: the design engineers the machine, not the trust
- **Security:** the design shares physical substrate across tenants and defers security to "expanded by the review" (§6.2) — so the RAG-specific attack surface (prompt injection, corpus poisoning, cache/graph cross-tenant leakage) has *zero* controls.
- **UX:** the design's best reliability move — "degrade, don't fail" — is **silent**. A degraded 200 is indistinguishable from a healthy one, so the reliability work is invisible at the moment of reading.
- **SWE:** the design's load-bearing claims are *asserted, not specified* — "atomic" cutover across 4 stores, read-replica reads with no consistency contract, a composite key that handles insert but not delete.

### Top findings per lens

**Security (16 findings; 4 critical) — grounded in OWASP LLM Top 10 (2025)**
1. **Cross-tenant isolation has no defense-in-depth** — store-level filtering is the *only* boundary; no post-retrieval re-check that every returned chunk's `tenant_id` matches the caller. *(Crit)*
2. **Graph traversal is unscoped** — multi-hop can walk into another tenant's subgraph. *(Crit)*
3. **Semantic cache is a cross-tenant leak + poisoning vector** — not tenant-namespaced. *(Crit)*
4. **No indirect prompt-injection defense (LLM01)** — ingested doc text flows untrusted into the LLM prompt. *(Crit)*
5. Corpus/embedding poisoning (LLM04/08), answer-side exfiltration (LLM02), ingest SSRF + malicious files, unbounded per-tenant compute (LLM10), and **no erasure/deletion fan-out across the 6 stores + caches + backups** (a deleted doc resurrects on the next re-index). *(High)*

**Software engineering (12 findings)**
1. **No read-your-writes / consistency contract** — "reads replicas, writes primary" with no lag bound → "I just ingested, why can't I find it?" *(High)*
2. **Blue-green cutover is not atomic** and BM25/graph aren't versioned by embedding model — RRF fuses across a v1/v2 discontinuity. *(High)*
3. **Composite key handles insert, not delete** — edited docs orphan old chunks; no prune/delete-propagation spec. *(High)*
4. DLQ-replay idempotency, checkpoint frontier must be a **gap set not a high-water mark**, retry amplification across layers. *(Med-High)*
5. **~12 moving parts with no phasing** — needs a minimal-viable subset; testing/eval-in-CI nearly absent. *(Med-High)*

**UX (14 findings)**
1. **No streaming / TTFT as a product deliverable** — a 6 s silence vs a 6 s streaming answer. *(High)*
2. **No progressive source disclosure** — retrieval finishes ~3 s before generation; stream sources first. *(High)*
3. **Citations are a two-word promise** — no per-source schema, no grounding/attribution check. *(High)*
4. **No confidence signal / calibrated "I don't know"** — a confidently-wrong answer isn't even in the failure matrix. *(High)*
5. **Degradation is silent** — every rung of §4.4 needs a machine-readable descriptor + client copy. *(High)*

---

## Part B — Design vs RAGStack: capability contrast

Legend: ✅ implemented · 🟡 partial · 📋 planned (SPEC/ADR) · ❌ absent.

| Capability (design) | Our plan | Current impl | Verdict |
|---|---|---|---|
| **Ingest/retrieval as separate concerns** | 📋 ADR 0001 (offline/online planes) | 🟡 separate `ingestion/`+`retrieval/` packages & a separate bulk CLI, but **same process/deploy**; no physical plane split | Direction matches; separation is logical, not physical |
| **Durable queue + KEDA ingest workers** | 📋 ADR 0001 uses **GoWe/CWL** (workflow DAG) instead of a streaming queue | ❌ `/v1/ingest` runs in-process background tasks; bulk = `ingest_jsonl.py` producer-worker | Different mechanism, same goal (see Part E) |
| **Separate ingest vs query embedding pools (bulkhead)** | 📋 ADR 0001 Go embedding-router sidecar | 🟡 one `PooledEmbedder` (least-loaded, failover, health) **shared** by both paths | Pool exists; not split — a real bulkhead gap |
| **Idempotent upsert on stable key** | — | ✅ deterministic `uuid5(doc_id:start:end)`; re-ingest overwrites in place | **We're solid here** |
| **Delete/prune orphans on re-ingest** (SWE #3) | — | ✅ `delete_except` / replace-on-reingest (`pipeline.py`), upsert-then-prune in bulk | **We already solve the design's gap** |
| **Checkpoint frontier as gap set** (SWE #4b) | — | ✅ `done_ranges` out-of-order tracking (#65/#70) | **We're ahead of the design's spec** |
| **Poison isolation** | — | ✅ `BatchingEmbedder` bisection quarantine; 🟡 no durable DLQ | Partial |
| **Embedding-model migration (blue-green, per-query version)** | 🟡 STATUS notes re-ingest on model change | 🟡 `(model,dim)` collection scoping isolates models; ❌ no dual-serve/cutover/version-pinning | Foundation present; orchestration absent |
| **Hybrid dense+BM25+RRF** | ✅ SPEC M5 | ✅ `HybridRetriever` + `RRFScorer` (verify `c` vs the design's ≈60) | **Match** |
| **Cross-encoder rerank, degrade-first** | ✅ SPEC M5 | ✅ `SidecarReranker`, degrades to fused order | **Match** |
| **Query rewriting** | ✅ SPEC M5 | ✅ passthrough/multiquery/hyde (step-back/entity planned) | **Match** |
| **Graph retrieval, tenant-scoped traversal** (Sec #2) | ✅ SPEC M4 | ✅ Neo4j, traversal scoped `all(rel … IN tenants)`, depth-capped | **We're ahead of the design's gap** |
| **Multi-tenancy: server-derived id, fail-closed** | ✅ | ✅ metadata filter, own+public, fail-closed on missing filter | Direction matches |
| **Defense-in-depth: post-retrieval tenant re-check** (Sec #1) | ❌ | ❌ relies solely on Qdrant/ES filter | **Shared gap** |
| **Caches (semantic/result), tenant-namespaced** (Sec #3) | 📋 | ❌ Redis provisioned, not load-bearing; no caches yet | Not built → fix *before* adding caches |
| **Prompt-injection / output DLP** (Sec #4/#6) | ❌ | ❌ none | **Gap (both)** |
| **Ingest SSRF / malicious-file hardening** (Sec #7) | — | 🟡 `INGEST_ROOT` LFI confinement (local files only; no URL fetch yet) | Smaller surface today; harden before adding URL/CDC sources |
| **Per-tenant compute/token quota + fan-out cap** (Sec #8) | 📋 SPEC M6 rate limiting | 🟡 `TenantQuota` = concurrency slots (not token/compute) | Partial |
| **Erasure/deletion fan-out + at-rest encryption** (Sec #9/#13) | ❌ | 🟡 per-doc delete across vector/text/graph; ❌ caches/backups/erasure flow | Partial |
| **Consistency contract / read-your-writes** (SWE #1) | ❌ | ✅ *trivially* (single-node Qdrant, no replicas → RYW holds) | Not a problem *yet*; becomes one at replica scale |
| **Streaming + TTFT + progressive sources** (UX #1/#2) | 📋 SPEC M6 (streaming) | ❌ `stream` is a request field only; no SSE endpoint | **Gap; on roadmap** |
| **Citation schema + grounding check** (UX #3) | — | 🟡 structured `Source{doc_id,chunk_id,score,metadata}`; ❌ no span/grounding/`ingested_at` | Better than design's promise; incomplete |
| **Confidence / calibrated "I don't know"** (UX #4) | ❌ | ❌ degrades to sources-with-note, no abstention threshold | **Gap (both)** |
| **Visible degradation descriptor** (UX #5) | ❌ | ❌ degrades **silently** (200 + sources) | **Gap (both)** |
| **Structured errors (problem+json) + feedback loop** (UX #8/#9) | — | 🟡 typed errors in `platform/`; ❌ no `/feedback`, no explain mode | Gap |
| **Observability (OTel, RED/USE, burn-rate SLOs)** | 📋 SPEC M7 | ❌ logging only; metrics/tracing not built | **Gap; on roadmap** |
| **Contract-first API + conformance gate** | ✅ | ✅ OpenAPI + `conformance/` over Python+Go | **Match (a real strength)** |
| **Idempotency-Key on ingest POST** (design §6.4) | — | ❌ every POST mints a new job_id (STATUS #6) | Gap (we use deterministic ids instead) |
| **HA deployment: multi-AZ, HPA/KEDA, blue-green/canary** | 📋 SPEC M8 (Helm, scaling, load test) | ❌ Docker Compose + Apptainer, single-node | **Largest gap; entirely on roadmap** |

---

## Part C — Where RAGStack already aligns or leads

The design is a *naive-clean-room* target; in several **correctness** areas the actual
codebase is **ahead of it**, because those lessons were already paid for:

1. **Delete/prune correctness (SWE #3).** The design's composite key "handles insert, not delete." RAGStack already has `delete_except` / replace-on-reingest and upsert-then-prune — the exact version-superseding sweep the SWE reviewer asks the design to add.
2. **Gap-tracking checkpoint (SWE #4b).** The design says "advance the frontier"; RAGStack's `done_ranges` (#65/#70) already tracks out-of-order completions as a gap set — more correct than the design's text.
3. **Tenant-scoped graph traversal (Sec #2).** The design left graph isolation as a critical gap; RAGStack scopes every hop (`all(rel … IN $tenants)`) and depth-caps it.
4. **Contract-first + conformance.** Both the design and all three reviewers call this the right model — RAGStack already lives it (OpenAPI + `conformance/` over two implementations).
5. **Graceful degradation ordering.** RAGStack already degrades rerank→fused and LLM→sources; the design and UX reviewer agree the *ordering* is right (RAGStack just needs to make it *visible* — UX #5).
6. **Idempotent-upsert-over-at-least-once** and **model/dim collection scoping** are already in place.

Net: RAGStack is **not behind a naive production design on ingest correctness** — it's ahead on exactly the parts that are hardest to retrofit.

---

## Part D — Real gaps worth adopting (mapped to milestones)

Ordered by leverage; each is a genuine gap the design/reviews expose that our plan should absorb.

| # | Gap | Source | Where it fits |
|---|---|---|---|
| 1 | **Defense-in-depth tenant isolation** — post-retrieval `tenant_id` re-check on every vector/BM25 result; tenant-namespace *any* future cache **before** caching ships | Sec #1/#3 | Now (cheap) + gate on M6 |
| 2 | **RAG threat controls** — prompt-injection isolation (delimit retrieved context), output DLP, ingest content validation/provenance | Sec #4/#5/#6 | New security workstream (pre-M6) |
| 3 | **Make degradation visible** — a `degradation{}` descriptor in responses + client copy | UX #5 | M6 (API) — low effort, high trust |
| 4 | **Streaming + progressive sources + TTFT SLO** | UX #1/#2 | SPEC **M6** (already lists streaming) |
| 5 | **Confidence branch + calibrated "I don't know"** + add "confidently wrong" to the failure matrix | UX #4 | M5/M6 |
| 6 | **Citation schema + grounding check + `ingested_at`/`as_of`** | UX #3/#6 | M6 |
| 7 | **Per-query `model_version` pinning through the funnel** + real blue-green cutover for model migration | SWE #2 | M8 (or when first model swap lands) |
| 8 | **Consistency contract** (read-your-writes via the ingest receipt watermark) — spec now, enforce when read replicas arrive | SWE #1 | M8 |
| 9 | **Erasure/deletion fan-out** across stores + caches + backups; at-rest encryption | Sec #9/#13 | M6/M8 |
| 10 | **Per-tenant compute/token quotas + rewrite fan-out cap** (today's `TenantQuota` is slots only) | Sec #8, SWE #5 | M6 |
| 11 | **Observability** — OTel traces across both planes, RED/USE, burn-rate SLO alerts | SWE #10 | SPEC **M7** |
| 12 | **Separate query vs ingest embedding pools** (bulkhead) | SWE, design §5.4 | ADR 0001 embedding-router sidecar |
| 13 | **Feedback endpoint + explain/debug mode** feeding the eval set | UX #9/#10 | M6/M7 |

---

## Part E — Reconciliation with ADR 0001

The independent design **validates ADR 0001's central bet**: both arrive at a hard
split between an offline/throughput ingest plane and an online/latency retrieval plane,
with a **separate embedding pool as a bulkhead** so bulk re-embed can't starve live
queries. That convergence — reached independently from web research — is a strong signal
the ADR's direction is right.

**The one substantive divergence is the ingest-plane substrate:**
- **The design** uses a *streaming durable queue + KEDA workers* — optimized for continuous/CDC ingestion and freshness SLOs.
- **ADR 0001** uses *GoWe/CWL workflows* — optimized for batch scatter-gather over a corpus (which is RAGStack's actual workload: large JSONL dumps, eval sweeps).

These aren't in conflict — they fit different ingest shapes. **Recommendation:** keep GoWe/CWL for batch corpus ingest and eval (ADR 0001), and adopt the *queue + KEDA* pattern only if/when a **continuous/streaming/CDC** ingestion requirement appears. Note both in ADR 0001 as the two ingest modalities, with the workload trigger that selects each.

---

## Part F — Prioritized recommendations

**Do now (cheap, high-trust, no new infra):**
1. Post-retrieval tenant re-check (Sec #1) — a few lines, closes a defense-in-depth gap.
2. Response `degradation{}` descriptor (UX #5) — turns silent degradation into visible, and it's already happening under the hood.
3. Spec the consistency contract + confirm the delete/prune sweep already covers version-superseding (SWE #1/#3 — we largely do).
4. Prompt-injection prompt-isolation for retrieved context (Sec #4) — a prompt-template change.

**Fold into M6 (API & Auth):** streaming + TTFT, citation/confidence/abstention, structured errors + `degradation{}`, per-tenant compute quotas + fan-out caps, Idempotency-Key, tenant-namespaced caching guardrails (before any cache ships).

**Fold into M7 (Observability):** OTel across both planes, RED/USE, burn-rate SLO alerts, per-tenant cost/quality attribution and a tenant-facing view.

**Fold into M8 (Production):** multi-AZ stores + read replicas + the consistency enforcement, HPA/KEDA autoscaling, blue-green/canary with per-query `model_version` pinning, erasure/deletion fan-out + at-rest encryption, load/soak/chaos + eval-in-CI as the model-swap quality gate.

**Update ADR 0001:** record the two ingest modalities (batch workflow vs streaming queue) and the query/ingest embedding-pool bulkhead as an explicit consequence.

**Guiding correction to the design itself:** adopt the SWE reviewer's *minimal-viable subset* framing — RAGStack should not chase the full 12-component target; it already has the hard-to-retrofit correctness core, and should add HA components only on measured triggers (replicas when a single node saturates; second embedding pool when bulk contends; semantic cache when LLM cost/tail justifies the false-positive risk; blue-green at the first real model migration).
