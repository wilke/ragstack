# RAGStack — Glossary

Four words carry most of the weight in this system, and three of them are
overloaded by something else in the stack. Every access-control bug found in
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
`GET /v1/collections` lists, what ownership and shares attach to, and what
`MAX_COLLECTIONS` counts. **It holds no data.**

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

## Two overloaded words to watch

**"Collection"** — Qdrant calls *its* physical containers "collections" too. In
this codebase, an unqualified "collection" means the RAGStack registry entry; the
Qdrant object is "the Qdrant collection" or "the store's vector leg". When the
docs say *two ids over one collection*, they mean one **Qdrant** collection.

**"Index"** — Elasticsearch's unit, and also the verb for writing to it. The ES
index is one leg of a store, never the store itself.

---

## Known gap in this model

`DELETE /v1/collections/{id}?purge=true` removes the **registry entry, vectors,
text index and manifest** — but **not Neo4j triples**, which have no
per-collection deletion (`stores/neo4j.py` offers `delete_by_doc` only). Because
store names are deterministic, a later collection built from the same spec
inherits the previous one's graph edges. Graph is `tbd` in the store definition
above for exactly this reason.
