"""Resolve a bulk ingest's target through the collection registry (#263).

The bulk writers exist because routing millions of chunks through the API would
defeat the point. But they have been choosing their *physical* store name
themselves, and that is a different thing entirely — it produces a Qdrant
collection and an ES index that:

* no registry entry names, so they are invisible to ``GET /v1/collections``, to
  the per-tenant cap, and to :mod:`ragstack.ops.store_inventory`;
* no owner row governs, so they can never be listed, shared or revoked
  (ADR-0004); and
* worst, **have no provenance manifest** — and
  ``check_ingest_build_spec`` early-returns when ``read_manifest()`` is ``None``
  ("no manifest yet — nothing to contradict"). So the ADR-0002 409 guard is
  permanently disarmed for every subsequent API ingest into that store, which is
  the exact failure ADR-0002 was written to prevent: same model, 256/32 vs
  512/64, silent interleave, slow quality decay.

The decision (ADR-0005 decision 6, recorded on #263) is that **the bulk data path
stays direct to the stores; only the registration moves**. A bulk writer takes a
``--collection-id`` that already exists in the registry and refuses an
unregistered one, optionally creating it through the API first so the spec, the
cap and the owner row all come from the normal path.

Everything physical then comes from the registry entry rather than the command
line: the vector collection, its Qdrant instance (a routed collection lives
elsewhere), and the ES index. The CLI's own build parameters are *checked*
against the entry, never used to name anything.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class TargetError(RuntimeError):
    """An ingest target that must not be written to. Message is operator-facing."""


@dataclass(frozen=True)
class IngestTarget:
    """Where a bulk ingest may write, resolved from one registry entry."""

    collection_id: str
    spec: Any  # CollectionSpec
    qdrant_url: str
    collection: str  # physical vector store name — from the spec, not the CLI
    es_index: str

    @property
    def model(self) -> str:
        return self.spec.embedding_model or ""

    @property
    def dim(self) -> int:
        return int(self.spec.embedding_model_dim)

    def check_build(
        self,
        *,
        model: str | None = None,
        dim: int | None = None,
        chunk_method: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_params: dict[str, Any] | None = None,
    ) -> None:
        """Refuse a write whose build spec differs from the registry entry's.

        This is ADR-0002's 409 guard, applied where the bulk path actually is.
        The API's version compares against the provenance manifest and gives up
        when there is none; here the registry entry is the record, and there is
        always one — that is the point of resolving through it.

        ``None`` means "the caller did not say", which is not the same as a
        mismatch: a script that never learns the chunker (it ingests
        pre-chunked JSON) must not be blocked, only prevented from
        contradicting. A value that IS supplied and differs is fatal.
        """
        from ragstack.provenance import chunk_descriptor

        diffs: list[str] = []

        def cmp(field: str, want: Any, got: Any) -> None:
            if got is None:
                return
            if str(want or "") != str(got or ""):
                diffs.append(f"{field}: registry={want!r} ingest={got!r}")

        cmp("embedding_model", self.spec.embedding_model, model)
        cmp("embedding_dim", self.spec.embedding_model_dim, dim)
        if chunk_method is not None or chunk_size is not None or chunk_overlap is not None:
            want = self.spec.chunk_descriptor()
            got = chunk_descriptor(chunk_method or "", chunk_size, chunk_overlap, chunk_params)
            cmp("chunk", want, got)
        if diffs:
            raise TargetError(
                f"refusing to ingest into collection {self.collection_id!r}: this "
                "ingest's build spec differs from the registry entry's, and mixing "
                "them inside one index produces retrievable, plausible-looking, "
                "wrong results (ADR-0002).\n  "
                + "\n  ".join(diffs)
                + f"\nEither match the entry, or create a new collection for this "
                f"spec instead of writing into {self.collection_id!r}."
            )

    def write_manifest(
        self,
        manifest_dir: str,
        *,
        embedding_api: str = "",
        embedding_endpoints: list[str] | None = None,
        corpus: str = "",
        chunk_count: int | None = None,
    ) -> str:
        """Record verified provenance for this store, arming ADR-0002's guard.

        Written from the **registry entry**, so a store built by the bulk path
        and one built by the API are described identically — and so a later API
        ingest has something to be refused by.
        """
        if not manifest_dir:
            return ""
        from ragstack.provenance import make_ingest_manifest, write_manifest

        manifest = make_ingest_manifest(
            collection=self.collection,
            model=self.spec.embedding_model or "",
            dim=self.dim,
            embedding_api=embedding_api or self.spec.embedding_api,
            embedding_endpoints=list(
                embedding_endpoints or self.spec.embedding_endpoints
            ),
            chunk_method=self.spec.chunk_method,
            chunk_size=self.spec.chunk_size,
            chunk_overlap=self.spec.chunk_overlap,
            chunk_params=self.spec.chunk_params,
            corpus=corpus,
            chunk_count=chunk_count,
        )
        write_manifest(manifest_dir, manifest)
        return manifest.spec_hash


# ---------------------------------------------------------------------------
# registry access
# ---------------------------------------------------------------------------


def _settings() -> Any:
    from ragstack.config import settings

    return settings


def load_specs(settings: Any | None = None) -> list[Any]:
    """Every spec in the configured collection registry.

    Reads the durable store the API reads (``COLLECTION_STORE_BACKEND``), not a
    running API — a bulk load routinely runs while the API is down.

    Synchronous, and therefore **must not be called from inside a running event
    loop**. That is deliberate: resolving the target belongs in ``main()``,
    before any embedding or connecting happens, so a refusal costs nothing.
    """
    from ragstack.collection_store import make_collection_store

    store = make_collection_store(settings or _settings())

    async def _run() -> list[Any]:
        try:
            return await store.list_specs()
        finally:
            await store.close()

    return asyncio.run(_run())


def _qdrant_url_for(collection: str, settings: Any, override: str = "") -> str:
    """The Qdrant instance serving ``collection``.

    A **routed** collection lives on its own instance and that routing wins over
    any command-line URL: writing it to the default instance would silently build
    a second, invisible copy of a store that already exists elsewhere. For
    everything else an explicit ``--qdrant-url`` beats the ambient setting, since
    the operator naming an instance is the more specific statement.
    """
    routes = getattr(settings, "qdrant_collection_routes", None) or {}
    if collection in routes:
        return routes[collection]
    return override or settings.qdrant_url


def target_from_spec(
    spec: Any, settings: Any | None = None, *, qdrant_url: str = ""
) -> IngestTarget:
    s = settings or _settings()
    return IngestTarget(
        collection_id=spec.id,
        spec=spec,
        qdrant_url=_qdrant_url_for(spec.collection, s, qdrant_url),
        collection=spec.collection,
        es_index=spec.es_index(),
    )


def _registry_description(settings: Any) -> str:
    backend = (getattr(settings, "collection_store_backend", "json") or "json").lower()
    if backend == "sqlite":
        return f"sqlite:{settings.collection_store_path}"
    if backend == "postgres":
        return "postgres (COLLECTION_STORE_DSN / POSTGRES_DSN)"
    if backend == "json":
        return f"json:{settings.collections_file or '<COLLECTIONS_JSON inline>'}"
    return backend


def resolve(
    collection_id: str,
    *,
    settings: Any | None = None,
    specs: list[Any] | None = None,
    qdrant_url: str = "",
) -> IngestTarget:
    """Resolve an id to its registry entry, or refuse.

    The refusal names the registry that was consulted and both ways to create the
    entry. An unhelpful "not found" here is what tempts an operator into reaching
    for the old ``--collection <name>`` behaviour, which is the hole this closes.
    """
    s = settings or _settings()
    entries = load_specs(s) if specs is None else specs
    for spec in entries:
        if spec.id == collection_id:
            return target_from_spec(spec, s, qdrant_url=qdrant_url)
    known = ", ".join(sorted(e.id for e in entries)) or "<registry is empty>"
    raise TargetError(
        f"collection {collection_id!r} is not in the registry "
        f"({_registry_description(s)}).\n"
        f"Known ids: {known}\n"
        "A bulk load may not mint a store the registry has never seen: it would "
        "be invisible to GET /v1/collections and to the collection cap, governed "
        "by no owner row, and — with no provenance manifest — it would disarm "
        "ADR-0002's build-spec guard for every later API ingest into it.\n"
        "Create it first, either through the API (POST /v1/collections, which is "
        "also what --create-via-api does) so the cap and the owner row come from "
        "the normal path, or by adding the entry to the registry above."
    )


def resolve_by_store_name(
    name: str,
    *,
    settings: Any | None = None,
    specs: list[Any] | None = None,
    qdrant_url: str = "",
) -> IngestTarget:
    """Resolve a *physical* store name to the registry entry that claims it.

    The migration path for the existing ``--collection <name>`` flag. An
    invocation that already targets a registered store keeps working (and now
    gets its manifest written); one that would have minted an invisible store is
    refused. That split is deliberate: a flag day would strand running pipelines,
    while a warning would be ignored by exactly the callers that matter.
    """
    s = settings or _settings()
    entries = load_specs(s) if specs is None else specs
    matches = [e for e in entries if e.collection == name]
    if len(matches) == 1:
        return target_from_spec(matches[0], s, qdrant_url=qdrant_url)
    if len(matches) > 1:
        # ADR-0002 decision 5 broken the other way; the API refuses to start in
        # this state, so do not guess which entry's ACLs govern the write.
        raise TargetError(
            f"physical store {name!r} is claimed by {len(matches)} registry "
            f"entries ({', '.join(sorted(e.id for e in matches))}), which "
            "violates ADR-0002 decision 5. Pass --collection-id to say which one "
            "this ingest belongs to, and fix the registry."
        )
    raise TargetError(
        f"physical store {name!r} is claimed by no registry entry "
        f"({_registry_description(s)}).\n"
        "Writing to it would create a store that GET /v1/collections cannot see, "
        "the collection cap does not count, and no owner row governs — and with "
        "no provenance manifest it would disarm ADR-0002's build-spec guard for "
        "every later API ingest into it (#263).\n"
        "Pass --collection-id <id> for an existing entry, or "
        "--collection-id <id> --create-via-api http://<api> to create one."
    )


def create_via_api(
    api_url: str,
    collection_id: str,
    *,
    api_key: str = "",
    bearer: str = "",
    label: str = "",
    embedding: str | None = None,
    chunk: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> None:
    """Create the collection through ``POST /v1/collections``.

    Deliberately the API and not a direct registry write: the cap, the owner row
    and the server-default build spec are all applied there. The CLI does not
    get to choose the spec — it verifies afterwards, via
    :meth:`IngestTarget.check_build`, that what the server created is what this
    ingest is about to produce, and refuses if not.
    """
    import httpx

    body: dict[str, Any] = {"id": collection_id}
    if label:
        body["label"] = label
    if embedding is not None:
        body["embedding"] = embedding
    if chunk is not None:
        body["chunk"] = chunk
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    r = httpx.post(f"{api_url.rstrip('/')}/v1/collections", json=body,
                   headers=headers, timeout=timeout)
    if r.status_code == 201:
        log.info("created collection %r via %s", collection_id, api_url)
        return
    if r.status_code == 409:
        # Someone else created it, or it already existed. Either way the entry
        # now exists, which is all this call was for.
        log.info("collection %r already exists", collection_id)
        return
    detail = ""
    try:
        detail = json.dumps(r.json())
    except Exception:  # noqa: BLE001
        detail = r.text[:500]
    raise TargetError(
        f"could not create collection {collection_id!r} via {api_url}: "
        f"HTTP {r.status_code} {detail}"
    )


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def add_arguments(parser: Any) -> None:
    """Add the registry-target flags to a bulk writer's parser."""
    g = parser.add_argument_group(
        "collection target (#263)",
        "A bulk load writes into a store named by a REGISTRY ENTRY. The physical "
        "Qdrant collection and ES index come from that entry, not from the "
        "command line.",
    )
    g.add_argument("--collection-id", default=os.getenv("RAGSTACK_COLLECTION_ID", ""),
                   help="registry id to ingest into (env RAGSTACK_COLLECTION_ID). "
                        "Must already exist unless --create-via-api is given.")
    g.add_argument("--create-via-api", default="", metavar="URL",
                   help="create the id first via POST <URL>/v1/collections, so the "
                        "cap, the owner row and the build spec come from the normal "
                        "path. Uses --api-key / --api-bearer.")
    g.add_argument("--api-key", default=os.getenv("RAGSTACK_API_KEY", ""),
                   help="X-API-Key for --create-via-api (env RAGSTACK_API_KEY)")
    g.add_argument("--api-bearer", default=os.getenv("RAGSTACK_BEARER", ""),
                   help="bearer token for --create-via-api (env RAGSTACK_BEARER)")


def resolve_from_args(args: Any, *, settings: Any | None = None) -> IngestTarget:
    """Resolve :func:`add_arguments`' flags into a target, creating it first if
    ``--create-via-api`` was given and the id is absent."""
    cid = getattr(args, "collection_id", "") or ""
    physical = getattr(args, "collection", "") or ""
    url = getattr(args, "qdrant_url", "") or ""
    s = settings or _settings()

    if not cid:
        if physical:
            return resolve_by_store_name(physical, settings=s, qdrant_url=url)
        raise TargetError(
            "--collection-id is required: a bulk load writes into a store named "
            "by a registry entry (#263). Pass the id of an existing collection, "
            "or --collection-id NEW --create-via-api http://<api> to create it "
            "through the normal path first."
        )

    try:
        target = resolve(cid, settings=s, qdrant_url=url)
    except TargetError:
        if not getattr(args, "create_via_api", ""):
            raise
        target = None  # type: ignore[assignment]
    if target is not None:
        return _checked(target, physical, cid)

    create_via_api(
        args.create_via_api, cid,
        api_key=getattr(args, "api_key", "") or "",
        bearer=getattr(args, "api_bearer", "") or "",
    )
    # Re-resolve from the registry rather than trusting the response body: the
    # durable entry is what every later reader sees, and if the API wrote to a
    # different registry than this CLI reads, that is exactly the misconfiguration
    # worth failing on here instead of after a 500k-row load.
    return _checked(resolve(cid, settings=s, qdrant_url=url), physical, cid)


def resolve_or_exit(args: Any, *, settings: Any | None = None, **build: Any) -> IngestTarget:
    """Resolve, check the build spec, and exit 2 with a readable message on
    refusal — the entry point every bulk writer calls from ``main()``.

    A traceback is the wrong output here: every :class:`TargetError` is a
    complete, actionable sentence aimed at an operator who is about to load a
    corpus, and burying it under a stack trace is how it gets ignored.
    """
    import sys

    try:
        target = resolve_from_args(args, settings=settings)
        if build:
            target.check_build(**build)
    except TargetError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    return target


def _checked(target: IngestTarget, physical: str, cid: str) -> IngestTarget:
    """Refuse a ``--collection`` that contradicts the resolved entry.

    Silently preferring the entry would be worse than failing: the operator
    named a store, and writing to a different one is the failure mode this whole
    module exists to remove.
    """
    if physical and physical != target.collection:
        raise TargetError(
            f"--collection {physical!r} contradicts collection {cid!r}, whose "
            f"registry entry names the store {target.collection!r}. The physical "
            "name comes from the entry; drop --collection."
        )
    return target
