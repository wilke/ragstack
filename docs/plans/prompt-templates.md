# Server-side prompt templates for `/v1/query` — plan

*2026-09-14. Status: `OPEN`. Raised by the BV-BRC literature demo (PR #544), which had to route
generation through a second service because RAGStack's generation prompt cannot be steered at
all. Owner framing, and the constraint this plan is built around: **RAGStack is text and
information retrieval, not answer interpretation.** That is an argument for making generation
*configurable*, not for making it *arbitrary* — the distinction §3 exists to hold.*

## 1. What it is for, in order

1. **Structured extraction over a corpus.** The literature demo asks a model to return a
   TSV table with a fixed column set (PPI, protein function, mutation) over retrieved
   passages. Today that prompt is built in the browser and sent to BV-BRC's Copilot, because
   `/v1/query` has nowhere to put it.
2. **Removing a whole service from the demo's path.** Today: browser → `/v1/retrieve`
   (RAGStack), then browser → Copilot `/chatbrc/chat-only` → `mango:8003`. But `dev` is
   *already* configured for that exact model and endpoint (`LLM_ENDPOINT`,
   `LLM_MODEL` in `tenant.env`). Copilot supplies no model we lack; it is a transport. See §7.
3. **Making generation comparable.** A named, versioned template is what lets Compare A/B two
   generation configurations and lets the grading harness attribute a verdict to one. A prompt
   assembled in a browser is not an experimental condition — it cannot be replayed.

## 2. The three levels, and which one this is

| | what | status |
|---|---|---|
| 1 | one fixed prompt, frozen in `llm.py:20` | **today** |
| 2 | **named server-side templates, caller selects by id and fills declared slots** | **this plan** |
| 3 | caller POSTs arbitrary prompt text | **refused — see §3** |

Level 1 reads as an oversight rather than a decision. Every adjacent knob on the generation
path *is* a `Setting` — `llm_endpoint`, `llm_model`, `llm_max_context_chars` — and STATUS.md
records a deliberate pass to make retrieval knobs real ("declared **and read** at
construction… the phantom `.env` no-ops are removed"). `_SYSTEM_PROMPT` never got that pass:
it is a module constant with one reference and no config surface, and there is no ADR
recording a choice to freeze it. This repo writes ADRs for decisions of consequence
(0003–0006); a deliberate "generation is unsteerable" would be written down.

## 3. Why level 2 and not level 3

Level 3 turns a multi-tenant, authorized, quota'd API into an **open LLM proxy scoped by
someone else's credential**. A caller who can post arbitrary prompt text can use another
tenant's model allocation for anything, and the request logs become an unbounded record of
user-authored text. It also destroys the property §1.3 wants: if the prompt is caller-authored,
two `/v1/query` results are never comparable.

Level 2 keeps three properties level 3 gives up:

* **Bounded output shape.** A template declares whether it produces prose or a table, and with
  which columns. The service is not a general text generator.
* **Reproducibility.** `(template id, template version, slot values, model id)` fully determines
  the prompt. That tuple is loggable, replayable, and is exactly what an ablation arm needs.
* **A small, reviewable prompt surface.** Prompts are deployment config, reviewed once, not
  per-request input.

**What this does NOT solve, stated plainly:** prompt *injection*. A slot value can still contain
"ignore the above instructions". Slots are values in a template the operator wrote, not a
sandbox. What level 2 bounds is **abuse and non-reproducibility**, not adversarial input — and a
template whose slots are pasted into a system message would be a bad template. Mitigations in
§5.3 reduce the surface; they do not close it. Anyone reading this plan as "injection handled"
has misread it.

## 4. What exists that this reuses

| piece | where | reuse |
|---|---|---|
| Per-request model selection | `query_request.json` `llm`, resolved in `deps.py` | composes with a template; no new model plumbing |
| Model registry + admin API | `/v1/admin/models/registry`, `models_registry_response.json` | the shape to imitate for a template registry, if one is ever needed |
| Context packing under a char budget | `llm.py` `_format_context`, `llm_max_context_chars` | renders `{{context}}`; unchanged |
| Contract-first + conformance | `contracts/`, `conformance/` | the template surface is contract-governed like everything else |
| A working reference implementation | `frontend/src/literature/extraction.ts` (PR #544) | the PPI/function/mutation templates and the table parser already exist, client-side, with 28 tests |

## 5. Design

### 5.1 The request

Two new fields on `/v1/query`. **Not** on `/v1/retrieve`, which does not generate.

```jsonc
{
  "query": "SARS-CoV-2 Spike ACE2 protein interaction",   // what gets EMBEDDED
  "collection": "oa-dev",
  "template": "ppi-extraction",                            // NEW
  "template_vars": {                                       // NEW
    "organism": "SARS-CoV-2",
    "genes": "Spike, ACE2"
  }
}
```

**`query` and the template are different strings, and this is the subtle part.** `query` is the
retrieval string — it is embedded and BM25'd. The template renders the *generation* prompt.
Conflating them is the bug waiting to happen: passing a 15,000-character extraction prompt as
`query` would embed the instructions rather than the question and return noise. The client
builds both from the same form fields; the server never derives one from the other.

`template` absent ⇒ exactly today's behaviour, byte for byte. That is the compatibility
guarantee conformance should assert.

### 5.2 The template

A YAML/JSON file loaded at startup from `PROMPT_TEMPLATES_FILE`, per tenant.

```yaml
- id: ppi-extraction
  version: 1
  label: Protein-Protein Interaction (PPI)
  output: table                     # table | text
  columns: [Pathogen, Protein A, Protein B, Interaction Type, Method, Assertion, Reference]
  slots:
    - {name: organism, required: true,  max_len: 120}
    - {name: genes,    required: false, max_len: 200}
  system: |
    You extract structured data from scientific literature. Use ONLY the provided passages.
  user: |
    Extract {{label}} for organism "{{organism}}"{{#genes}} involving: {{genes}}{{/genes}}.
    Return ONLY a TSV table with these columns:
    {{columns}}
    ...
    --- LITERATURE CONTEXT ---
    {{context}}
```

**A file, not a new store.** The repo already carries four `collection_store` backends and
issue #351 wants to collapse them; adding a fifth persistence surface for prompts would be
moving the wrong way. Templates are deployment config, they change at review speed, and a file
is reviewable in git. Admin CRUD is deferred to §6 phase 4 and may never be needed.

### 5.3 Slot substitution rules

These are the rules that keep §3's bound real. All of them are cheap; none is optional.

1. **Values, never source.** A slot value is substituted literally and the result is *not*
   re-rendered. `{{...}}` inside a slot value is inert text.
2. **Length caps, declared per slot.** An `organism` is a species name, not an essay. A missing
   cap is a template authoring error and fails at load, not at request time.
3. **Undeclared slots are a 422**, not silently ignored — a typo'd slot name that renders an
   empty clause produces a subtly wrong prompt and no error, which is the worst outcome.
4. **Slots may not reach the system message.** `system` is rendered with no slot substitution at
   all. The operator's framing instructions are not caller-influenced; only the user message
   carries caller text.
5. **Templates are validated at startup**, not lazily: an unparseable template file fails the
   boot loudly rather than 500ing the first caller who selects it.

### 5.4 The response

**Unchanged in phase 1.** `answer` carries the model's raw text; `sources` and
`rewritten_queries` as today. Clients parse the table.

This is deliberate restraint. `query_response.json` is `additionalProperties: false` and
`/v1/query` is the most conformance-covered endpoint in the repo; a `columns`/`rows` block is
the expensive half of this change and the easy half to get wrong. The parser already exists and
is tested (`extraction.ts`, PR #544), so nothing is blocked by deferring it. Add a `structured`
block in phase 4 **only if** a second client appears — one client is not a reason to move code
into a contract.

The response SHOULD, however, echo `template` and `template_version` so a result is
self-describing for Compare and grading. That is additive and cheap.

## 6. Phasing

| phase | what | gate |
|---|---|---|
| 0 | **ADR.** Records: level 2 not 3 (§3); templates are deployment config, not user content; slots never reach the system message; the response stays unchanged in phase 1. | owner sign-off |
| 1 | Contract: `template`/`template_vars` on `query_request.json`, `GET /v1/prompt-templates`, fixtures. Conformance including **"no `template` ⇒ byte-identical to today"**. | contract review |
| 2 | Python: loader, validator, renderer, wiring in `deps.py`/`llm.py`. Go stays a stub (ADR-0006 §4). | conformance green |
| 3 | Port the demo's three templates server-side; the app drops the Copilot leg and calls `/v1/query`. Removes a cross-origin dependency and a whole service. | demo still works |
| 4 | *Optional, on evidence:* `structured` response block; admin CRUD; per-template model pinning. | a second consumer exists |

Phases 0–2 are the real work; 3 is small and is what proves the design. **Not before the
hackathon** — the demo ships on the current two-leg path.

## 7. What this subsumes: the model-proxy question

The open question was whether to put the vLLM endpoints behind a proxy so browsers could reach
them. Owner's constraint: *it would have to sit behind a/the gated API.*

That constraint resolves it — **this plan is that gate, and no separate proxy is needed.**

* A browser cannot call `mango:8003` directly: it is plain HTTP on an internal host, and the
  demo page is HTTPS, so mixed content blocks it. That is the *only* structural reason Copilot
  is in the path.
* A standalone proxy would need authentication, and nginx cannot verify a BV-BRC token — it is
  an offline RSA signature check against a pinned key (`identity/bvbrc.py`). It would need an
  `auth_request` back into RAGStack, i.e. RAGStack gating it anyway. Unauthenticated it is free
  inference on ANL GPUs for anyone who finds the URL.
* RAGStack already reaches the model **server-side**, already authenticates, already scopes to a
  tenant and already enforces quota (`quota.py`, `ratelimit.py`).

So the gated API that a model proxy would have to sit behind already exists; it is `/v1/query`.
Give it a prompt seam and the browser never needs to reach a model host at all. Building a
second proxy would duplicate what Copilot's `chat-only` already does, and buy only independence
from a registry row that is not currently blocking anything.

## 8. Risks

| risk | mitigation |
|---|---|
| Scope creep into a prompt-management product (versioning UI, per-user prompts, A/B assignment) | phase 4 is gated on a second consumer existing; a file, not a store |
| Reproducibility claimed but not real — a template edited in place invalidates old results | `version` is required and MUST be bumped on any content change; the response echoes it. Consider refusing to load two templates with the same `(id, version)` and different bodies |
| The `query`-vs-template confusion of §5.1 reaching a client | conformance test that a template request with a long `query` is not silently truncated; document it at the top of the schema |
| Level 2 read as "injection is handled" | §3 says otherwise in the plan, and should say so in the ADR and the schema description too |
| Go divergence | Go is a frozen scaffold; contract + conformance keep the path open (ADR-0006 §4) |

## 9. Open questions for the owner

1. **Per-tenant or global templates?** A file per tenant is the obvious default and matches
   `tenant.env`. A shared set with per-tenant overrides is more machinery than the evidence
   justifies today.
2. **Should a template be able to pin a model?** A TSV-extraction template may want a different
   model than a summarization one. `llm` is already per-request, so this is a default, not a new
   capability. Cheap to add in phase 4; not needed for the demo.
3. **Does `/v1/query`'s rewrite stage apply when a template is used?** Rewriting operates on
   `query`, which is unchanged by this plan, so the honest default is "yes, unchanged" — but it
   is worth stating rather than discovering.
