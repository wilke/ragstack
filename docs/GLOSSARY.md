# RAGStack — Glossary

Four words carry most of the weight in this system, and three of them are
overloaded by something else in the stack — plus a fifth, **"default"**, which
this codebase overloads against *itself*. Every access-control bug found in
August 2026 lived exactly where these definitions blur, so they are written down
here rather than left to context.

---

## Tenant

**A physical deployment.** One API process plus the stateful services behind it:

| | |
|---|---|
| Qdrant | its own instance (vectors) |
| Elasticsearch | its own instance (BM25 text) |
| Postgres *or* sqlite | its own ACL / registry / job database |
| API | one uvicorn process |
| UI | optional — a Vite server, or none |

The boundary is **data at rest**, not a name. Two APIs pointing at one Qdrant are
not two tenants; they are two front doors on one tenant's data. Per
[ADR-0005](adr/0005-tenant-anatomy.md), provisioning a tenant is a *script*
(`apptainer/new-tenant.sh`), not an API call, because it allocates ports and
starts processes.

> Note what this means in practice today: of the deployments on the dev host,
> only `dev` meets this definition. `lucid-next` and `asm-next` are parallel APIs
> over **production** stores — new code, same bytes.

## Collection

**A logical registry entry.** An id — `default`, `lucid`, `open-access` — plus the
rows that describe and govern it:

- its **spec** (`collections` table / `collections_file`): the build spec
  (`model`, `dim`, chunker) that is immutable per [ADR-0002](adr/0002-collection-identity.md),
  and the recorded creator
- its **ACL rows** (`shares` table): one active owner, plus grants

A collection is what a request names in the `collection` field, what
`GET /v1/collections` lists, and what ownership and shares attach to. **It holds
no data.**

It also has a **lifecycle state** — `active`, `archiving`, `dormant`, `restoring`,
`lost` — and this is where `MAX_COLLECTIONS` gets misread. The cap counts the
rows that are **physically present**: `PHYSICAL = {active, archiving, restoring}`,
the states in which a Qdrant collection and an ES index actually exist
(`collection_store.py`). A `dormant` or `lost` row is still a collection in every
other sense — listed, owned, shareable, counted against the per-owner quota — but
it holds no store and **is not counted**. So "I have 100 collections and the cap
is 100" does not by itself mean the next create is refused; and at the bound a
create does not simply refuse, it **evicts** the least-recently-accessed active
collection whose archive is current and takes the freed slot
([ADR-0005](adr/0005-tenant-anatomy.md) decision 5).

## Store

**The physical data.** Not one object — two, and possibly a third:

```
collection 'lucid'
  ├── Qdrant collection   lucid_sfr_tok256      (vectors)
  ├── Elasticsearch index lucid_sfr_tok256      (BM25 text)
  └── Neo4j triples       scoped by (name, tenant_id, collection)
```

The two retrieval legs are separate objects that a hybrid query fuses. **They may
have different names, and they may live on different servers.** Both are true on
this deployment right now: the `dev` tenant's Qdrant collection is
`ragstack_..._928f8ebe` while its ES index is plain `ragstack`; Lucid
production's vector leg is on Qdrant `:6343` while its text leg is on the shared
ES `:9200`.

Hence the rule that any tooling must key a store by **`(backend_url, name)`**,
never by name alone — there are name collisions across instances today, and one
of each pair is production.

### Where the name comes from

**Derived** (the default): content-addressed from the build spec, so the same
spec always yields the same name.

```
Salesforce/SFR-Embedding-Mistral + 4096 + fixed/512/64
  → ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe
```

Because it is deterministic, **identical names on two different Qdrants are not
shared data** — they are two independent stores that happen to agree.

**Pinned**: `QDRANT_COLLECTION_EXPLICIT` names it outright. Used for corpora built
before the derivation scheme (`ragstack_sfr_tok256`, `lucid_sfr_tok256`).

---

## The invariant that ties collection to store

> **A physical index has exactly one registry entry.**
> — [ADR-0002](adr/0002-collection-identity.md) decision 5

Collection and store are **1:1 in the healthy state**. That is why it is tempting
to treat them as one thing — and why they must not be. Every serious bug found in
August 2026 was that mapping breaking, in one direction or the other:

| broken mapping | what it caused |
|---|---|
| **two** collections → one store | two independent ACLs over one dataset. Revoking a grant on one id left the same bytes readable through the other ([#275](https://github.com/wilke/ragstack/issues/275)) |
| **zero** collections → one store | data no entry claims and no ACL governs, invisible to the cap ([#285](https://github.com/wilke/ragstack/issues/285)) |

Access control is asserted at the **collection** ([ADR-0003](adr/0003-access-control.md)),
while the bytes live in the **store**. Merge the two words and you lose the
vocabulary needed to state either failure.

---

## Which database holds what

A frequent confusion, because "Postgres rows" sounds like it belongs to the store:

| object | lives in | keyed by |
|---|---|---|
| build spec | `collections` table | collection **id** |
| owner + shares | `shares` table | collection **id** |
| user profiles, roles | `users` table | subject (per **tenant**) |
| vectors | Qdrant | store name |
| text | Elasticsearch | store name |
| graph triples | Neo4j | `(name, tenant_id, collection)` |

So the relational rows are **collection-level, not store-level**. Deleting a store
does not remove them, and revoking ACLs does not remove data — which is precisely
why the two delete forms exist and why each is refused in the case that would
strand the other side.

---

## Three overloaded words to watch

**"Collection"** — Qdrant calls *its* physical containers "collections" too. In
this codebase, an unqualified "collection" means the RAGStack registry entry; the
Qdrant object is "the Qdrant collection" or "the store's vector leg". When the
docs say *two ids over one collection*, they mean one **Qdrant** collection.

**"Index"** — Elasticsearch's unit, and also the verb for writing to it. The ES
index is one leg of a store, never the store itself.

**"Default"** — two different things, and conflating them *is* bug
[#419](https://github.com/wilke/ragstack/issues/419):

| | **The registry pointer** | **The caller-effective target** |
|---|---|---|
| what it is | one deployment-wide setting (`DEFAULT_COLLECTION_ID`, else the settings-derived entry) | the id *this caller's* request lands in when it omits `collection` |
| on the wire | `CollectionInfo.is_default` (per item) | `CollectionsResponse.default` (top level) |
| computed from | config alone | allowlist ∩ readable, then: the pointer **if this caller can see it**, else the first visible entry in listing (insertion) order — and for ingest, narrowed again to what they may *write* |
| same for everyone? | yes | no |

The pointer is a **pointer, never an entry** (ADR-0002 decision 5): repointing it
moves no data, no ACL row and no exemption. The bug it caused: `/v1/query`,
`/v1/retrieve` and `/v1/chunks` resolved the *pointer* and then 404'd on the
ownership seam, so a caller whose readable set excluded the tenant default was
told, by id, that a collection they had never been shown did not exist. One
module — `api/default_collection.py` — is now the single answer to "which
collection does an omitted `collection` mean?", so the listing and the query
target cannot disagree again.

The distinction is also load-bearing for **authorization**, and in a way that is
easy to get backwards. The legacy shared surface's write exemption (a `read`
grant suffices to ingest there, because per-chunk `tenant_id` stamping is what
isolates writers) keys on the entry's `is_shared_surface` **flag** — never on
what `default` points at. Keyed on the pointer, aiming `default` at an owned
collection would let any *reader* of it ingest into somebody else's corpus just
by omitting `collection`.

---

## Known gap in this model

`DELETE /v1/collections/{id}?purge=true` removes the **registry entry, vectors,
text index, manifest and Neo4j triples**. The graph leg is
`GraphStore.delete_collection(tenant_id=None, collection)` — collection-wide, like
the two physical drops, deliberately *not* tenant-scoped, so a co-writer's edges
cannot be inherited by the next owner of a deterministic store name (#295/#380).

The residual gap is **eviction**, not purge. `api/eviction.py` passes
`graph_store=None`, so evicting a collection to `dormant` leaves its triples in
place. That is a rule, not an oversight: eviction may only destroy what exists
somewhere else, and the archive has no triples leg for a version until the
extract-graph step has run over it (`archive.py` writes `"graph": false`, and
replay has no extractor), so a dropped graph could not be rebuilt. The plumbing
(`ops/evict.py`'s `graph_store=` argument) is in place and unit-tested for when
per-version triples archiving lands (#350). Until then an evicted collection's
triples remain, and every read of them stays collection-scoped.
