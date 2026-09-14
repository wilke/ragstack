# ADR 0008 — Generation is configurable, not arbitrary: named server-side prompt templates

- **Status:** Proposed
- **Date:** 2026-09-14
- **Deciders:** @wilke
- **Related:** [#544](https://github.com/wilke/ragstack/pull/544) (the literature demo that
  forced the question), [#545](https://github.com/wilke/ragstack/pull/545)
  ([docs/plans/prompt-templates.md](../plans/prompt-templates.md), the implementation plan),
  [ADR-0005](0005-tenant-anatomy.md) (a tenant is self-contained),
  [ADR-0006](0006-execution-topology-revised.md) §4 (Go stays a scaffold),
  [#122](https://github.com/wilke/ragstack/issues/122) (the ablation harness this protects)

## Context

`/v1/query` generates an answer with a prompt that cannot be changed by anything: the
system message is `_SYSTEM_PROMPT`, a module constant at `python/ragstack/llm.py:20` with
exactly one reference and no configuration surface. Every *adjacent* knob on the same code
path is a `Setting` — `llm_endpoint`, `llm_model`, `llm_max_context_chars` — and the
retrieval knobs went through a deliberate pass to make them real (STATUS.md: "declared
**and read** at construction… the phantom `.env` no-ops are removed"). The prompt never got
that pass, and no ADR records a decision to freeze it. It reads as an unfilled gap.

The gap became concrete while building a BV-BRC literature console (#544). That app asks a
model to return a TSV table with a fixed column set (protein–protein interactions, protein
function, mutations) over retrieved passages. RAGStack had nowhere to put that instruction,
so the app builds the prompt **in the browser** and posts it to BV-BRC's Copilot service —
even though the `dev` tenant is already configured for the *same model at the same
endpoint* Copilot forwards to (`LLM_ENDPOINT=http://mango.cels.anl.gov:8003`,
`LLM_MODEL=…Llama-4-Scout…FP8-dynamic`). Copilot supplies no model we lack. It is in the
path purely as a transport, because we had no seam.

That arrangement has already cost us. The model list the browser must choose from is
Copilot's, not ours; one of its two advertised models is registered against an endpoint
that now serves a different model entirely, so selecting it fails 100% of the time. We
cannot curate a catalog we do not own.

A prompt assembled in a browser is also not an experimental condition. It cannot be
replayed, so Compare cannot A/B two generation configurations and the grading harness
cannot attribute a verdict to one.

## Decision drivers

1. **RAGStack is text and information retrieval, not answer interpretation.** Generation is
   a convenience the service offers, not its purpose. Whatever we add must not quietly turn
   it into a general text-generation product.
2. **Multi-tenancy is real.** Tenants share GPUs. Anything that lets one caller spend
   another tenant's model allocation is a defect, not a feature.
3. **Results must be comparable.** #122, Compare and the grading harness all depend on a
   generation configuration being a nameable, replayable thing.
4. **The contract is the product.** A new capability is a schema change first, implemented
   in Python, kept open for Go by conformance (ADR-0006 §4).

## Decision

Three levels were on the table. We take the middle one.

| | | |
|---|---|---|
| 1 | one fixed prompt, frozen in code | today |
| 2 | **named server-side templates; the caller selects one by id and fills declared slots** | **decided** |
| 3 | the caller posts arbitrary prompt text | **refused** |

### 1. Templates are deployment configuration, per tenant

A template is a record in a file named by `PROMPT_TEMPLATES_FILE`, loaded and validated at
startup, exposed read-only at `GET /v1/prompt-templates`. It declares an `id`, a `version`,
a `label`, an output shape (`text` or `table` with `columns`), a set of named `slots` with
length caps, and the `system` and `user` message bodies.

Per tenant, not global — consistent with [ADR-0005](0005-tenant-anatomy.md), which already
makes a tenant self-contained down to its own Elasticsearch. Copy a template between tenants
when you want it in two places. Federation remains reserved for its own future ADR.

A file rather than a new store: the repo already carries four `collection_store` backends
and [#351](https://github.com/wilke/ragstack/issues/351) exists to collapse them. Prompts
change at review speed, and a file is reviewable in git. Admin CRUD is not part of this
decision.

### 2. The caller selects and fills; it never authors

`/v1/query` gains exactly two fields: `template` (an id) and `template_vars` (values for
the declared slots). `/v1/retrieve` gains nothing — it does not generate.

**`query` and the template are different strings.** `query` is what gets embedded and
BM25'd; the template renders the generation prompt. The server never derives one from the
other. Conflating them would embed a multi-thousand-character instruction block and return
noise.

**Absent `template` ⇒ byte-identical behaviour to today.** This is a conformance assertion,
not an aspiration.

### 3. Slot substitution rules, which are what make level 2 hold

1. **Values, never source.** A slot value is substituted literally and the result is not
   re-rendered. `{{…}}` inside a slot value is inert text.
2. **Declared length caps**, per slot. A missing cap is a template authoring error and
   fails at load, not at request time.
3. **Undeclared slots are a 422.** A typo'd slot name that silently renders an empty clause
   produces a subtly wrong prompt and no error — the worst available outcome.
4. **Slots never reach the system message.** `system` is rendered with no substitution at
   all. The operator's framing is not caller-influenced.
5. **Templates validate at startup**, so a malformed file fails the boot loudly rather than
   500ing the first caller who selects it.

### 4. Identity of a template is `(id, version, content hash)`

`version` must be bumped on any content change, and a content hash is computed at load and
echoed in the response. Because templates are per-tenant and copied by hand, two tenants
can hold `ppi-extraction` v1 with bodies that have drifted apart — both claiming the same
identity while being mutually incomparable. The hash makes that detectable instead of
invisible. Precedent: the ragstack-ctl gateway generation header already stamps a
`registry sha256` for the same reason.

### 5. A template does not pin a model

Considered and rejected. Compare exists to vary the model while holding the prompt
constant, so a pinned model obstructs #122 directly. It also couples configuration that
changes at different rates (a retired model breaks every template naming it, discovered at
request time), and it entangles two stages — `apply_assignment` rebuilds
`app.state.rewriters` from the *same* LLM as the generator, so a template-level model choice
would silently also change query rewriting.

The worry it would address — a tuned template degrading when the global default is swapped
— is answered by **recording rather than constraining**: the response echoes the resolved
model id. A genuine hard requirement (structured-output mode, say) would be a
`requires_capability` flag: a capability, not an identity. Not built until something needs it.

### 6. The rewrite stage is unchanged

Rewriting operates on `query`, which this decision does not touch. The stages are
orthogonal and stay so.

## What this decision explicitly does NOT do

**It does not solve prompt injection.** A slot value is caller-supplied text; a template
author who interpolates one into an instruction position has written a bad template, and
rule 4 above only keeps that out of the *system* message. What level 2 bounds is **abuse and
non-reproducibility**, not adversarial input. Anyone citing this ADR as "injection handled"
has misread it.

**It does not add a model proxy, and it removes the reason to want one.** A proxy fronting
the vLLM endpoints so browsers could reach them would have to sit behind an authenticating
gate; nginx cannot verify a BV-BRC token (an offline RSA check against a pinned key,
`identity/bvbrc.py`), so the gate would be RAGStack anyway. Meanwhile RAGStack already
reaches the model server-side with authentication and tenant scoping. Once `/v1/query` can
carry a prompt, no browser needs to reach a model host at all.

Note the limit of that gate as it stands: `ratelimit.py` is scoped to **write** endpoints
(ingest, collection creation, share grants) and `quota.py`'s per-tenant concurrency limit is
**disabled by default** (`limit <= 0`). Authentication and tenant scoping are real; metering
of generation spend is not. Extending the rate limiter to cover generation is a prerequisite
for treating this as a spend control, and is out of scope here.

## Consequences

- The literature demo can drop its second service and its cross-origin call; the browser
  stops choosing from a catalog we do not own.
- Generation becomes an ablation dimension: `(template, version, hash, model)` names a
  condition that #122 and the grading harness can hold fixed or vary.
- Operators gain a reviewable prompt surface and the obligation to review it. A bad template
  is now a deployment artifact, with the blast radius that implies.
- `query_response.json` grows echo fields. It does **not** grow a structured `rows` block in
  this decision — that endpoint is the most conformance-covered in the repo, the parser
  already exists and is tested client-side, and one consumer is not a reason to move code
  into a contract.
- Go diverges further; the path stays open by contract and conformance (ADR-0006 §4).

## Alternatives considered

**Level 3 — the caller posts the prompt.** Rejected. It makes a multi-tenant, authorized
API an open LLM proxy scoped by someone else's credential, turns request logs into an
unbounded record of user-authored text, and destroys comparability: two `/v1/query` results
are never the same condition. It is also what the browser does today, which is precisely the
arrangement this ADR exists to end.

**A single configurable system prompt (`LLM_SYSTEM_PROMPT`).** Rejected as insufficient and
faintly dangerous: one prompt per deployment cannot express "answer the question" and
"extract PPIs as TSV" simultaneously, and shipping it would create the impression the gap
was closed.

**Per-collection default prompts.** Rejected. A collection is a corpus, not a task; the same
corpus supports PPI extraction, mutation extraction and prose summary.

**Templates in a database with admin CRUD.** Deferred, not rejected. It is strictly more
machinery than the evidence justifies, and it points the wrong way while #351 is trying to
reduce the number of persistence surfaces.
