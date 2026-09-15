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

WHICH registry (#563). All of the above presumes the process knows where the
registry is, and on the GoWe ingest plane it did not: the worker read
``COLLECTION_STORE_*`` from its own environment, which ``gowe-worker`` is given
once per worker GROUP — so one group served exactly one tenant, and a shared
group pointed at the wrong tenant's registry failed every task after the extract
stage had already run. :func:`registry_settings` adds the missing piece as a
NAME (``--registry hackathon``) resolved against per-registry environment
variables, so one worker can serve many tenants. A name and never coordinates:
a GoWe submission's ``submitted_inputs`` is an immutable, UI-rendered,
plaintext-stored snapshot, so a DSN placed there would be permanent. See
``docs/adr/0009-registry-selection-for-bulk-workers.md``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    ) -> None:
        """Refuse a write whose build spec differs from the registry entry's.

        This is ADR-0002's 409 guard, applied where the bulk path actually is.
        The API's version compares against the provenance manifest and gives up
        when there is none; here the registry entry is the record, and there is
        always one — that is the point of resolving through it.

        Unset on **either** side is not a mismatch, and the comparison is
        field-by-field rather than over the whole chunk descriptor. Both rules
        are load-bearing:

        * A script that never learns the chunker (it ingests pre-chunked JSON)
          must be prevented from contradicting the entry, not blocked by silence.
        * A registry entry that records nothing for a field cannot be
          contradicted by one — the same "nothing to contradict" rule
          ``check_ingest_build_spec`` applies to a missing manifest, at field
          granularity. This is what keeps a semantic corpus re-ingestable:
          ``asm-semantic`` records ``chunk_size=None`` because the semantic
          chunker does not use one, while argparse still hands us its ``512``
          default. Comparing whole descriptors made ``semantic//`` vs
          ``semantic/512/64`` a fatal difference and refused a legitimate
          rebuild of a production corpus.

        A value that both sides state, and state differently, is fatal.
        """
        diffs: list[str] = []

        def cmp(field: str, want: Any, got: Any) -> None:
            if got is None or got == "" or want is None or want == "":
                return
            if str(want) != str(got):
                diffs.append(f"{field}: registry={want!r} ingest={got!r}")

        cmp("embedding_model", self.spec.embedding_model, model)
        cmp("embedding_dim", self.spec.embedding_model_dim, dim)
        cmp("chunk_method", self.spec.chunk_method, chunk_method)
        cmp("chunk_size", self.spec.chunk_size, chunk_size)
        cmp("chunk_overlap", self.spec.chunk_overlap, chunk_overlap)
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


#: A registry NAME, as it travels on a workflow input (``registry: hackathon``).
#: Deliberately the same shape as a collection id: it arrives from a submission
#: and is used to BUILD AN ENVIRONMENT VARIABLE NAME, so it is validated before
#: any lookup rather than after. No path separator, no ``=``, no shell
#: metacharacter, no leading punctuation, nothing empty.
_REGISTRY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

#: ``registry=hackathon`` -> ``COLLECTION_STORE_BACKEND_HACKATHON``. Uppercase,
#: and anything outside ``[A-Z0-9_]`` becomes ``_`` (so ``prod.eu`` and
#: ``prod-eu`` both reach ``..._PROD_EU`` — collapsing two names onto one
#: registry is visible in the refusal, unlike an env name that cannot be set).
def registry_env_suffix(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", name.strip().upper())


def validate_registry_name(value: str) -> str:
    """Return a clean registry name, or raise ``ValueError`` saying why not.

    Split out from :func:`registry_settings` so ``Settings`` can reject a typo at
    config load (a ``ValueError`` is what pydantic turns into a readable
    ``ValidationError``) instead of letting it surface as a refused ingest after
    the extract stage has already run. An empty value is valid and means "no
    registry named".
    """
    name = (value or "").strip()
    if not name:
        return ""
    if not _REGISTRY_NAME_RE.match(name):
        raise ValueError(
            f"registry name {name!r} is invalid. A registry name selects an "
            f"environment variable, so it must match {_REGISTRY_NAME_RE.pattern} "
            "(letters, digits, '_', '.', '-'; at most 64 characters). It names a "
            "REGISTRY, never a path, a DSN or any other coordinate."
        )
    return name


def _registry_env_names(name: str) -> dict[str, str]:
    s = registry_env_suffix(name)
    return {
        "backend": f"COLLECTION_STORE_BACKEND_{s}",
        "path": f"COLLECTION_STORE_PATH_{s}",
        "dsn": f"COLLECTION_STORE_DSN_{s}",
        "file": f"COLLECTIONS_FILE_{s}",
        "inline": f"COLLECTIONS_JSON_{s}",
    }


class _RegistryView:
    """``settings`` as seen by one NAMED registry.

    Everything unrelated to the registry (``qdrant_url``, the routes table)
    delegates to the real settings object; every coordinate of *which registry*
    comes from the per-name environment and from nowhere else. That "nowhere
    else" is the whole point — see :func:`registry_settings`.
    """

    __slots__ = ("_base", "_over", "registry_name")

    def __init__(self, base: Any, name: str, over: dict[str, Any]) -> None:
        self._base = base
        self._over = over
        self.registry_name = name

    def __getattr__(self, item: str) -> Any:
        try:
            return self._over[item]
        except KeyError:
            return getattr(self._base, item)


def registry_settings(name: str, settings: Any | None = None) -> Any:
    """Resolve a registry NAME to the settings that read that registry.

    The bug this exists for (#563). A GoWe ingest submission carries nearly all
    of a tenant's physical state as visible workflow inputs — ``qdrant_url``,
    ``es_url``, ``collection``, ``embedding_url``, ``tenant`` — seeded per job by
    that tenant's API. The one piece that did not travel was the COLLECTION
    REGISTRY: the worker read it from its own process environment, set once per
    worker GROUP. So a group served exactly one tenant, and pointing the shared
    group at the dev tenant's sqlite registry made every ``hackathon`` ingest
    resolve against the wrong database and exit 2 *after* extract had already
    succeeded — every job dead half-done.

    A name, not coordinates, and emphatically not a credential: ``inputs`` and
    ``submitted_inputs`` on ``GET /api/v1/submissions/{id}`` are an immutable
    snapshot the UI renders and SQLite stores in plaintext, so a DSN placed there
    is permanent. The name selects a per-registry suffix
    (``COLLECTION_STORE_BACKEND_HACKATHON`` and friends); the DSN reaches the
    container through the worker's ``--secret-file``, which GoWe injects as
    container env and redacts from captured task output. When GoWe#260 (named
    secret references) lands, this env convention is swapped for a ``secret://``
    input and nothing else here changes.

    **An unconfigured name is fatal, and never falls back.** Falling back to the
    unsuffixed ``COLLECTION_STORE_*`` is precisely the outage: it is how a
    hackathon ingest silently consulted the dev tenant's registry. An empty name
    is the *other* contract — "no registry was named" — and gives back today's
    behaviour, the unsuffixed vars, byte for byte.
    """
    base = settings or _settings()
    # Validated BEFORE any lookup: the name builds an environment variable name
    # and arrives from a submission.
    try:
        name = validate_registry_name(name)
    except ValueError as e:
        raise TargetError(f"--registry: {e}") from None
    if not name:
        return base

    env = _registry_env_names(name)
    backend = (os.getenv(env["backend"], "") or "").strip().lower()
    over: dict[str, Any] = {
        "collection_store_backend": backend,
        # Blank every unsuffixed coordinate: a named registry inherits NOTHING.
        # `make_collection_store` falls back from `collection_store_dsn` to
        # `postgres_dsn`, and that fallback across tenants is the bug.
        "collection_store_path": "",
        "collection_store_dsn": "",
        "postgres_dsn": "",
        "collections_file": "",
        "collections_json": "",
    }

    def _refuse(why: str, looked: list[str]) -> TargetError:
        # Names only — never a value. A DSN read from a worker secret must not
        # be reachable through an error message.
        return TargetError(
            f"registry {name!r} {why}.\n"
            f"Looked for: {', '.join(looked)}\n"
            "Refusing to fall back to the unsuffixed COLLECTION_STORE_* "
            "variables: those name whichever tenant's registry this worker group "
            "was configured for, and resolving one tenant's collection against "
            "another tenant's registry is the failure this input exists to "
            "prevent (#563). Configure the registry on the worker (the DSN "
            "belongs in --secret-file, never in a workflow input), or drop "
            "--registry to use this process's own COLLECTION_STORE_* settings."
        )

    if not backend:
        raise _refuse(
            "is not configured on this worker", [env["backend"]]
        )
    if backend == "sqlite":
        path = (os.getenv(env["path"], "") or "").strip()
        if not path:
            raise _refuse(
                f"is configured as {backend!r} but names no database file",
                [env["backend"], env["path"]],
            )
        over["collection_store_path"] = path
    elif backend == "postgres":
        dsn = (os.getenv(env["dsn"], "") or "").strip()
        if not dsn:
            raise _refuse(
                f"is configured as {backend!r} but names no DSN",
                [env["backend"], env["dsn"]],
            )
        over["collection_store_dsn"] = dsn
        over["postgres_dsn"] = dsn
    elif backend == "json":
        file = (os.getenv(env["file"], "") or "").strip()
        inline = os.getenv(env["inline"], "") or ""
        if not file and not inline.strip():
            raise _refuse(
                f"is configured as {backend!r} but names no registry file",
                [env["backend"], env["file"], env["inline"]],
            )
        over["collections_file"] = file
        over["collections_json"] = inline
    elif backend != "memory":
        # `make_collection_store` warns and falls back to `json` for an unknown
        # backend. Here that would quietly read an unconfigured json registry,
        # so say what was set instead.
        raise _refuse(
            f"names an unknown backend {backend!r} "
            "(use json, sqlite, postgres or memory)",
            [env["backend"]],
        )
    return _RegistryView(base, name, over)


#: ``scheme://user:PASSWORD@host`` — the shape of every DSN this module can be
#: pointed at, matched structurally so a value the driver rewrote on its way to
#: the exception (asyncpg normalises the URL it failed on) is still caught.
_DSN_PASSWORD_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s/@:]+:)[^\s/@]*(@)")


def _secret_values(settings: Any) -> list[str]:
    """Values that must never appear in an operator-facing message.

    The registry DSN reaches a worker container as a secret (#563); a driver
    exception that embeds it — asyncpg and SQLAlchemy both quote the URL they
    failed on — would otherwise print it to task stderr under the eyes of
    whoever is reading the job that failed.
    """
    out = []
    for field in ("collection_store_dsn", "postgres_dsn"):
        value = (getattr(settings, field, "") or "").strip()
        if value:
            out.append(value)
    return out


def _redact(text: str, settings: Any) -> str:
    """Scrub credentials out of third-party error text.

    Two passes, because neither alone is enough: the exact configured value
    (which catches a DSN with no password in it, e.g. a unix-socket URL), and
    the structural ``user:password@`` form (which catches the same DSN after a
    driver normalised it — ``postgresql+asyncpg://`` becomes ``postgresql://``
    before asyncpg ever quotes it).
    """
    for secret in _secret_values(settings):
        text = text.replace(secret, "<redacted DSN>")
    return _DSN_PASSWORD_RE.sub(r"\1<redacted>\2", text)


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


def _specs_or_raise(settings: Any, specs: list[Any] | None) -> list[Any]:
    """Load the registry, turning any failure into an operator-facing refusal.

    A corrupt or unreachable registry must never read as an empty one: "no entry
    claims this" and "we could not look" are different answers, and only the
    first is a reason to stop. Both stop the load here, but the message has to
    say which."""
    if specs is not None:
        return specs
    try:
        return load_specs(settings)
    except Exception as e:  # noqa: BLE001 — every failure is the same refusal
        # The driver's message quotes the URL it failed on, and for a named
        # registry that URL is a worker secret (#563) — redact it before it
        # reaches task stderr.
        raise TargetError(
            f"could not read the collection registry ({_registry_description(settings)}): "
            f"{type(e).__name__}: {_redact(str(e), settings)}\n"
            "Refusing to ingest: an unreadable registry is not an empty one, and "
            "guessing would write into a store nothing claims (#263)."
        ) from e


def _registry_description(settings: Any) -> str:
    """Which registry was consulted, in one line, with no credential in it.

    This string ends up in every refusal, so it names the registry by NAME when
    one is in play (#563) — "sqlite:/rag/data/tenants/dev/state/..." was a true
    but useless answer to "why did the hackathon tenant's ingest fail?". The
    postgres branch names the *variables* rather than the DSN for the same
    reason the DSN is not a workflow input: this text is read by people who are
    not entitled to the credential.
    """
    name = (getattr(settings, "registry_name", "") or "").strip()
    env = _registry_env_names(name) if name else {}
    backend = (getattr(settings, "collection_store_backend", "json") or "json").lower()
    if backend == "sqlite":
        where = f"sqlite:{settings.collection_store_path}"
    elif backend == "postgres":
        where = (
            f"postgres ({env['dsn']})" if name
            else "postgres (COLLECTION_STORE_DSN / POSTGRES_DSN)"
        )
    elif backend == "json":
        where = f"json:{settings.collections_file or '<COLLECTIONS_JSON inline>'}"
    else:
        where = backend
    return f"registry {name!r}: {where}" if name else where


def resolve(
    collection_id: str,
    *,
    settings: Any | None = None,
    specs: list[Any] | None = None,
    qdrant_url: str = "",
    registry: str = "",
) -> IngestTarget:
    """Resolve an id to its registry entry, or refuse.

    The refusal names the registry that was consulted and both ways to create the
    entry. An unhelpful "not found" here is what tempts an operator into reaching
    for the old ``--collection <name>`` behaviour, which is the hole this closes.

    ``registry`` names WHICH registry to consult (#563); empty is today's
    behaviour, the process's own ``COLLECTION_STORE_*`` settings. See
    :func:`registry_settings`.
    """
    s = registry_settings(registry, settings)
    entries = _specs_or_raise(s, specs)
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
    registry: str = "",
) -> IngestTarget:
    """Resolve a *physical* store name to the registry entry that claims it.

    The migration path for the existing ``--collection <name>`` flag. An
    invocation that already targets a registered store keeps working (and now
    gets its manifest written); one that would have minted an invisible store is
    refused. That split is deliberate: a flag day would strand running pipelines,
    while a warning would be ignored by exactly the callers that matter.

    ``registry`` selects which registry claims it (#563), exactly as in
    :func:`resolve`.
    """
    s = registry_settings(registry, settings)
    entries = _specs_or_raise(s, specs)
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
    g.add_argument("--registry",
                   default=os.getenv("RAGSTACK_COLLECTION_REGISTRY", ""),
                   metavar="NAME",
                   help="WHICH collection registry to resolve --collection-id "
                        "against, by NAME (e.g. 'hackathon'); read from "
                        "COLLECTION_STORE_BACKEND_<NAME> and "
                        "COLLECTION_STORE_{PATH,DSN}_<NAME> in THIS process's "
                        "environment (env RAGSTACK_COLLECTION_REGISTRY). A name, "
                        "never coordinates and never a credential, so it is safe "
                        "as a visible workflow input (#563). Omitted = the "
                        "unsuffixed COLLECTION_STORE_* settings. A name nothing "
                        "is configured for is refused, never silently fallen "
                        "back from.")


def resolve_from_args(args: Any, *, settings: Any | None = None) -> IngestTarget:
    """Resolve :func:`add_arguments`' flags into a target, creating it first if
    ``--create-via-api`` was given and the id is absent."""
    cid = getattr(args, "collection_id", "") or ""
    physical = getattr(args, "collection", "") or ""
    url = getattr(args, "qdrant_url", "") or ""
    # Resolve the NAMED registry once, here, so every path below — including the
    # re-resolve after --create-via-api — consults the same one, and so an
    # unconfigured name is refused before the registry is read rather than after
    # (#563).
    s = registry_settings(getattr(args, "registry", "") or "", settings)

    if not cid:
        if physical:
            return _checked(
                resolve_by_store_name(physical, settings=s, qdrant_url=url), args
            )
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
        return _checked(target, args)

    create_via_api(
        args.create_via_api, cid,
        api_key=getattr(args, "api_key", "") or "",
        bearer=getattr(args, "api_bearer", "") or "",
    )
    # Re-resolve from the registry rather than trusting the response body: the
    # durable entry is what every later reader sees, and if the API wrote to a
    # different registry than this CLI reads, that is exactly the misconfiguration
    # worth failing on here instead of after a 500k-row load.
    return _checked(resolve(cid, settings=s, qdrant_url=url), args)


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


def _checked(target: IngestTarget, args: Any) -> IngestTarget:
    """Refuse a physical name on the command line that contradicts the entry.

    Both legs, and refusing rather than ignoring. Silently preferring the entry
    would be worse than failing — the operator named a store, and writing to a
    different one is the failure mode this module exists to remove. And silently
    preferring the *flag* is worse still: ``--es-index`` used to let a bulk load
    put the text leg in an index no registry entry claims, which is the same hole
    as ``--collection``, one leg over.
    """
    for flag, given, resolved in (
        ("--collection", getattr(args, "collection", "") or "", target.collection),
        ("--es-index", getattr(args, "es_index", "") or "", target.es_index),
    ):
        if given and given != resolved:
            raise TargetError(
                f"{flag} {given!r} contradicts collection "
                f"{target.collection_id!r}, whose registry entry names "
                f"{resolved!r}. Physical names come from the entry; drop {flag}."
            )
    return target
