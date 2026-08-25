"""Multi-collection registry.

Lets one API serve several corpora — different embedding models and/or chunk
strategies — selectable per request via the ``collection`` field on ``/query``
and ``/retrieve``. Each :class:`CollectionEntry` binds a Qdrant collection + ES
index + an embedder (matched to that collection's model/dim) into its own
``HybridRetriever``; the graph store, reranker, generator, and rewriters are
shared. An empty registry means single-collection mode: the pinned/derived
collection is the sole entry (under its physical name) and behaviour is
unchanged. ``default`` is never an entry — it is the pointer (see
:data:`RESERVED_COLLECTION_ID`).

The shared graph store is the reason ``CollectionEntry.collection`` (the physical
collection name) is also handed to the retriever and the ingest pipeline: since
one Neo4j holds every collection's triples, the collection boundary on the graph
axis lives in the triple data, not in the store instance (#209).

The registry is *built* in ``api/deps.py`` (which owns the embedder/store/
retriever construction helpers); this module holds the lookup container so both
the builder and the routers can import it without a cycle. The durable side —
``CollectionSpec`` itself and the backends that persist it — lives in
:mod:`ragstack.collection_store`; the three ``*_collection_spec`` helpers here
are the JSON-file façade over it, kept for the callers (and tests) that hold a
``settings`` object rather than a store.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ragstack.collection_store import (
    RESERVED_COLLECTION_ID,
    CollectionSpec,
    JsonFileCollectionStore,
    append_spec_to_file,
    remove_spec_from_file,
)
from ragstack.tenancy import allowed_collection_ids

log = logging.getLogger(__name__)

# Re-exported: `CollectionSpec` moved to ragstack.collection_store (which owns
# every backend that persists it) but is imported from here all over the API.
__all__ = [
    "RESERVED_COLLECTION_ID",
    "CollectionEntry",
    "CollectionRegistry",
    "CollectionSpec",
    "confined_collection_name",
    "forget_collection_spec",
    "is_reserved_collection_id",
    "load_collection_specs",
    "persist_collection_spec",
]


@dataclass
class CollectionEntry:
    """A built, ready-to-serve collection: metadata + its bound retriever/stores."""

    id: str
    label: str
    collection: str
    model: str
    dim: int
    chunk_method: str
    chunk_size: int | None
    chunk_overlap: int | None
    chunk_params: dict[str, Any]
    #: Is this the LEGACY SHARED SURFACE — the settings-derived collection that
    #: predates ownership, where per-chunk ``tenant_id`` is the isolation and the
    #: ``owner`` is only a backfill artifact?
    #:
    #: This is emphatically NOT "is the default collection". The two used to be
    #: the same flag, because the settings-derived entry was always the pointer
    #: target. Once ``default`` becomes a configurable POINTER (#276) they come
    #: apart, and conflating them would grant two exemptions to whatever the
    #: pointer names:
    #:
    #: * ``shared_scope`` (api/scope.py) refuses to widen scope here,
    #:   because widening would inject the backfill owner's tenant into every
    #:   caller's scope. Pointing ``default`` at a genuinely owned, shared
    #:   collection would silently DISABLE share-based widening for it.
    #: * the omitted-collection ingest branch (routers/documents.py) requires
    #:   only ``read`` rather than ``write``, for the same tenant-stamped reason.
    #:   Pointing ``default`` at an owned collection would turn that exemption
    #:   into a privilege escalation: any reader could ingest into it simply by
    #:   omitting ``collection``.
    #:
    #: "Is the pointer target" is ``entry.id == registry.default_id`` and is what
    #: the API's ``CollectionInfo.default`` / ``is_default`` report.
    #:
    #: (The task spec for #276 calls this flag ``is_legacy_shared``; the name
    #: here predates it and means exactly that. Kept, since renaming would be
    #: churn without a semantic change.)
    is_shared_surface: bool
    retriever: Any
    vector_store: Any
    text_index: Any
    embedder: Any = None  # the collection's embedder (matched to its model/dim); for ingest
    # Where that embedder points. The built ``embedder`` is bound to the app's
    # main-loop httpx client, so a *semantic* chunker — which embeds sentence
    # buffers synchronously from a background loop — cannot reuse it and must
    # build its own on its own loop. Retaining the api/endpoints here is what lets
    # ``deps._embed_bridge_for`` rebuild the SAME backend for this collection
    # instead of falling back to the server-default embedder (which would detect
    # boundaries with a different model than the one storing the vectors).
    embedding_api: str = ""
    embedding_endpoints: list[str] = field(default_factory=list)
    # The creator recorded on the durable spec (``CollectionSpec.owner``); ''
    # for legacy/hand-authored specs and the settings-derived default entry.
    # The startup ACL backfill reads this to tell "predates ownership → publish
    # world-readable" apart from "owner row lost → repair, stay private".
    owner: str = ""
    # The ES index name behind ``text_index`` (the field above holds the store
    # *object*, which doesn't advertise its name). Needed by the purge guard: two
    # registry entries may deliberately share one physical store (``CollectionSpec.
    # es_index``), and dropping the index out from under the other entry would
    # destroy data nobody asked to destroy. "" → same name as ``collection``.
    text_index_name: str = ""

    def es_index(self) -> str:
        """The physical ES index this entry reads/writes — mirrors
        ``CollectionSpec.es_index`` (it rides on ``collection`` by default)."""
        return self.text_index_name or self.collection


#: The id that names the POINTER — the collection a request resolves to when it
#: omits ``collection`` (``DEFAULT_COLLECTION_ID``). It is never a registry
#: entry: not creatable, not storable, not synthesised (#276, ADR-0002 decision
#: 5). A request may still SAY ``collection="default"`` — that is the same as
#: omitting it, and resolves to the pointer target's real id. Deliberately NOT
#: the same namespace as ``tenancy.DEFAULT_TENANT`` (also the string "default"),
#: which is the writer stamped on chunks. Conflating the two would be a security
#: bug. (Defined in :mod:`ragstack.collection_store`, which must drop a legacy
#: row under that id without importing the API layer; re-exported here.)


def is_reserved_collection_id(cid: str | None) -> bool:
    """Is ``cid`` the pointer name rather than a collection id?"""
    return cid == RESERVED_COLLECTION_ID


class CollectionRegistry:
    """Lookup over built collections with a designated default.

    ``default`` is a POINTER, not an entry: the registry refuses to hold an
    entry under the reserved id, and :meth:`resolve` / :meth:`canonical` map
    that name (like ``None``) to the entry ``default_id`` names. Resolution is
    one dict lookup — no store is consulted — so an omitted ``collection`` costs
    nothing on the request path."""

    def __init__(self, entries: list[CollectionEntry], default_id: str) -> None:
        if not entries:
            raise ValueError("CollectionRegistry requires at least one entry")
        for e in entries:
            self._refuse_reserved(e.id)
        self._entries: dict[str, CollectionEntry] = {e.id: e for e in entries}
        if default_id not in self._entries:
            # Fail here, not at the first no-collection request. `resolve(None)`
            # would raise KeyError deep in a router and surface as a misleading
            # "unknown collection None" 404 on every such request — an operator
            # typo in DEFAULT_COLLECTION_ID should stop the server instead.
            raise ValueError(
                f"default collection id {default_id!r} is not a registered "
                f"collection (have: {sorted(self._entries)})"
            )
        self._default_id = default_id

    @staticmethod
    def _refuse_reserved(cid: str) -> None:
        if is_reserved_collection_id(cid):
            raise ValueError(
                f"{RESERVED_COLLECTION_ID!r} is the pointer name, not a collection "
                "id: it names the collection a request resolves to when it omits "
                "'collection' and is never a registry entry of its own (#276)"
            )

    @property
    def default_id(self) -> str:
        return self._default_id

    def entries(self) -> list[CollectionEntry]:
        return list(self._entries.values())

    def has(self, cid: str) -> bool:
        """Is ``cid`` a registered ENTRY? The pointer name is not one — use
        :meth:`canonical` first when a caller-supplied id may say ``default``."""
        return cid in self._entries

    def add(self, entry: CollectionEntry) -> None:
        """Register a runtime-created collection (``POST /v1/collections``).
        Raises ``KeyError`` on a duplicate id so the router can 409, and
        ``ValueError`` for the reserved pointer name."""
        self._refuse_reserved(entry.id)
        if entry.id in self._entries:
            raise KeyError(entry.id)
        self._entries[entry.id] = entry

    def remove(self, cid: str) -> bool:
        """Drop a collection binding. Returns ``False`` if the id is unknown."""
        return self._entries.pop(cid, None) is not None

    def canonical(self, cid: str | None) -> str:
        """The REAL id a caller-supplied ``collection`` means: ``None`` and the
        reserved pointer name both mean the pointer target; anything else is
        returned unchanged (it may or may not be registered — that is the
        caller's 404 to raise). Authorization must run on this id, never on the
        literal ``"default"``: ACL rows left behind under that id by a
        pre-#276 registry must not grant anything."""
        if cid is None or is_reserved_collection_id(cid):
            return self._default_id
        return cid

    def permitted(self, allowed: set[str] | None) -> set[str] | None:
        """A ``TENANT_COLLECTIONS`` allowlist with the pointer name expanded to
        the id it currently points at, so an operator may confine a tenant to
        "the default" without knowing (or tracking) which real id that is.
        ``None`` (unrestricted) passes through."""
        if allowed is None or RESERVED_COLLECTION_ID not in allowed:
            return allowed
        return (allowed - {RESERVED_COLLECTION_ID}) | {self._default_id}

    def resolve(self, cid: str | None) -> CollectionEntry:
        """Entry for ``cid``, or the pointer target when ``cid`` is None or the
        reserved pointer name. Raises ``KeyError`` for an unknown other id so the
        router can 404 (explicit selection should fail loudly, not silently
        serve the wrong corpus)."""
        return self._entries[self.canonical(cid)]  # KeyError → 404 at the router


def confined_collection_name(
    registry: CollectionRegistry | None, tenant: str, mapping: dict[str, list[str]]
) -> str | None:
    """The physical collection name a *confined* tenant's graph reads must be
    scoped to, or ``None`` when the caller is unrestricted.

    The knowledge-graph endpoints take no ``collection`` argument — one graph
    store spans every collection — so a tenant confined by ``TENANT_COLLECTIONS``
    would otherwise inspect triples derived from collections it may not query
    (#209). This picks the same collection an unqualified ``/v1/query`` serves it:
    the registry default when permitted, else its first allowed collection present
    in the registry.

    ``None`` means "don't scope": the caller is unrestricted (operators/admins
    keep the cross-collection inspection view, which is also the only way to see
    pre-#209 triples that carry no collection stamp), or there is no registry, or
    the tenant's allowlist matches nothing in it — the last case being a caller
    with no readable collection at all, which the routers already handle by way of
    the tenant filter.
    """
    confined = allowed_collection_ids(tenant, mapping)
    if confined is None or registry is None:
        return None
    allowed: set[str] = registry.permitted(confined) or set()
    if registry.default_id in allowed:
        return registry.resolve(registry.default_id).collection
    present = sorted(e.id for e in registry.entries() if e.id in allowed)
    if not present:
        return None
    return registry.resolve(present[0]).collection


def load_collection_specs(settings: Any) -> list[CollectionSpec]:
    """Parse ``collections_file`` (preferred) or ``collections_json`` into specs.
    Returns [] (single-collection mode) when neither is set.

    Synchronous façade over :class:`JsonFileCollectionStore` — the file read runs
    under the same shared ``flock`` as the writers, so a reader can never observe
    a registry mid-update."""
    return JsonFileCollectionStore(settings).load_specs_sync()


def persist_collection_spec(settings: Any, spec: CollectionSpec) -> bool:
    """Write-through upsert a spec into ``collections_file`` so it survives
    restart (the lifespan re-reads that file). Returns ``False`` when no file is
    configured (in-memory only, lost on restart).

    Concurrency-safe as of the durable-registry work: the read-modify-write runs
    under an exclusive ``flock`` on ``{collections_file}.lock`` and writes through
    a per-writer unique temp path, so two API processes sharing one registry file
    can no longer lose each other's entry. The file format is unchanged."""
    path: str = getattr(settings, "collections_file", "") or ""
    return append_spec_to_file(path, spec)


def forget_collection_spec(settings: Any, cid: str) -> bool:
    """Write-through remove the spec with id ``cid`` from ``collections_file`` so a
    delete survives restart. Returns ``False`` when no file is configured or the id
    isn't present in the file (e.g. an in-memory-only or default entry). Same lock
    discipline as :func:`persist_collection_spec`."""
    path: str = getattr(settings, "collections_file", "") or ""
    return remove_spec_from_file(path, cid)
