"""Application configuration loaded from environment variables."""
from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


def _split_list_env(value: object) -> object:
    """Parse a ``list[str]`` env var from either a JSON array or a bare
    comma-separated string (pydantic-settings parses list envs as JSON, so a bare
    comma list would otherwise raise). Non-strings pass through unchanged."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            return json.loads(value)
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    # LLM / Embeddings
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"  # must name the model the llm_endpoint serves
    # OpenAI-compatible chat endpoint for answer generation (e.g. vLLM serving
    # Llama). Empty → /v1/query keeps its retrieval-only placeholder. When set to
    # a vLLM/self-hosted endpoint, also set llm_model to that server's model name
    # (the gpt-4o-mini default will be rejected as unknown).
    llm_endpoint: str = ""

    # Vector store
    vector_backend: str = "qdrant"          # qdrant | memory
    # When true (production), refuse to start on a non-durable / unreachable
    # backend instead of silently degrading to in-memory and losing data.
    require_durable_backends: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "ragstack"
    # Per-request bound (seconds) on every Qdrant call the API makes. Unset, the
    # client fell back to httpx's 5 s default — and a 4096-d search over a
    # 25M-point collection takes longer than that whenever the host is under
    # page-cache pressure, so healthy-but-slow searches surfaced as bare 500s
    # (ReadTimeout → ResponseHandlingException). 30 s lets a slow search finish;
    # what still times out is reported as 503 StoreUnavailable, not 500.
    qdrant_timeout: int = 30
    # Opt-in post-mortem probe (#427 W9). When a Qdrant SEARCH times out, fire ONE
    # bounded (2 s) read of that collection's optimizer state and log the raw
    # counters, so the log can say whether the store was mid-optimize when it
    # failed to answer — the one candidate cause `elapsed_s`/`reason` cannot see.
    # Default OFF on purpose: it sends another request to a store that has just
    # failed to answer one. Rate-limited to once per collection per 60 s. See
    # QdrantVectorStore._postmortem_probe for what it does and does not buy.
    qdrant_postmortem_probe: bool = False
    # Upsert batching: chunk each upsert so one request never carries an oversized
    # payload (a single large-shard upsert makes the client raise
    # ResponseHandlingException). qdrant_upsert_concurrency > 1 pipelines the
    # batches for throughput; 1 (default, API serving) is serial and safest under
    # a capped/optimizing collection. The bulk load path raises concurrency itself.
    qdrant_upsert_batch_size: int = 256
    qdrant_upsert_concurrency: int = 1
    # Explicit Qdrant collection override. When set (env QDRANT_COLLECTION_EXPLICIT),
    # the API serves this literal collection name verbatim instead of deriving one
    # from (qdrant_collection, embedding_model, embedding_model_dim) via
    # collection_name(). This lets the serving API point at a pre-built collection
    # whose name the derivation can't reproduce (e.g. ragstack_sfr_tok256). Empty
    # (default) keeps the derived behaviour byte-for-byte unchanged.
    # The Elasticsearch (BM25) index follows this name too when elasticsearch_index
    # is left at its default, so hybrid retrieval's two legs read the same corpus;
    # set elasticsearch_index explicitly to override that.
    qdrant_collection_explicit: str = ""
    # Multi-instance Qdrant routing: map a collection name to an alternate Qdrant
    # base URL, so that collection is served from a SEPARATE Qdrant process with its
    # own ``vm.max_map_count`` (VMA) budget. The VMA limit is per-process, and Qdrant
    # memory-maps every indexed segment's vectors — so a large collection
    # (e.g. ragstack_sfr_semantic, ~38k VMAs) can't always coexist with others under
    # one process's 65,530 ceiling. Routing it to a second instance sidesteps that.
    # A collection not listed here uses ``qdrant_url`` (single-instance, unchanged).
    # JSON env, e.g.
    #   QDRANT_COLLECTION_ROUTES='{"ragstack_sfr_semantic":"http://localhost:6343"}'
    # This is also the migration seam onto a sharded cluster: point a collection's
    # route at the cluster URL to cut it over independently of the others.
    qdrant_collection_routes: dict[str, str] = {}
    # Multi-collection registry. Each entry is a self-contained corpus the query
    # API can serve — its own Qdrant collection + ES index, its own embedding
    # model/dim/endpoints, and a chunk-strategy label — selectable per request via
    # the `collection` field on /query and /retrieve. Empty (default) = single-
    # collection mode: the pinned/derived collection is the sole "default" entry
    # and behaviour is byte-for-byte unchanged.
    #
    # ``collections_file`` points at a JSON file (a list of specs); ``collections_json``
    # is the same content inline. The file wins if both are set. Each spec:
    #   {"id": "sfr-512", "label": "SFR · fixed_token/512",
    #    "collection": "ragstack_sfr_tok512", "text_index": "",
    #    "embedding_api": "openai", "embedding_model": "Salesforce/SFR-Embedding-Mistral",
    #    "embedding_model_dim": 4096, "embedding_endpoints": ["http://localhost:9001"],
    #    "embedding_sidecar_url": "", "chunk_method": "fixed_token", "chunk_size": 512}
    collections_file: str = ""
    collections_json: str = ""
    # Which collection a request that omits `collection` resolves to — a POINTER
    # at an existing entry, never a collection that gets created to satisfy it.
    #
    # Empty means "the settings-derived entry", which is the historical
    # behaviour. It exists because `default` used to be SYNTHESISED as a second
    # registry entry over a physical store that often already had one — two ids
    # over one dataset, and therefore two independent ACLs, so revoking a grant
    # on one left the same bytes readable through the other (#275).
    #
    # Naming a nonexistent id is fatal at startup: it is an operator typo, and
    # every no-collection request would otherwise 404 or serve the wrong corpus.
    # (A *user's* stored preference pointing somewhere they lost access to is
    # different — that falls back, because stale preferences are guaranteed once
    # sharing exists.)
    default_collection_id: str = ""
    # Where that registry actually LIVES. A collection's identity is its build
    # spec (model + dim + chunker), so the mapping id -> {index, model, dim,
    # chunker} has to be durable and authoritative — an ingest that used a
    # different spec would corrupt the index silently.
    #   json     (default) the shipped `collections_file` / `collections_json`
    #            path, byte-compatible with what is deployed. The read-modify-
    #            write is now flock'd, so instances sharing one file (prod runs
    #            three) can no longer lose an entry.
    #   memory   process-local; nothing persists (dev/tests).
    #   sqlite   a durable `collections` table in `collection_store_path`.
    #   postgres the same table in `collection_store_dsn` (falls back to
    #            `postgres_dsn`) — the multi-process source of truth.
    # Switching to sqlite/postgres seeds the empty table ONCE from
    # `collections_file`, so migrating is a backend flip plus a restart; the JSON
    # file is left untouched, so rolling back is flipping it back.
    collection_store_backend: str = "json"  # json | memory | sqlite | postgres
    collection_store_path: str = "ragstack_collections.db"
    collection_store_dsn: str = ""
    # Hard cap on registered collections, enforced at POST /v1/collections.
    # ADR-0003: the collection count is the binding constraint — each collection
    # costs a physical Qdrant collection + ES index (budget ~100-150 per
    # instance, thread exhaustion near ~1000, crash-on-create ~2000) — and
    # creation is open to any authenticated principal, so without enforcement a
    # single caller looping the endpoint is an instance-wide denial of service.
    # Applies to admins too (the limit is physical, not an authorization tier).
    # 0 disables the cap; the default matches the ADR's "alert at 100" line.
    #
    # Since #359 this bounds PHYSICALLY PRESENT collections (`state` in
    # collection_store.PHYSICAL: active, plus archiving/restoring, which hold
    # or are rebuilding their stores), not registered ones. A `dormant`
    # collection — evicted to its Workspace archive, restored on first access
    # (#353/#358) — holds no Qdrant/ES slot and is not counted. Only `active`
    # rows are evictable, so the bound is met by them. At the bound, POST /v1/collections
    # evicts exactly one least-recently-accessed collection whose archive is
    # current (never one with an in-flight ingest job, never one over the
    # legacy shared surface's stores — ops/evict.py) and proceeds; when nothing
    # is evictable it answers 507 naming why. Set it from the tenant's measured
    # ceilings (memory mappings, threads, RAM at 60 %) — the derivation lives
    # in docs/runbooks/active-collection-bound.md.
    max_collections: int = 100
    # Per-collection chunk cap (#291, phase 3 of #201): the most chunks ONE
    # user-created collection may hold. Derivation, under the 2026-08-24 sizing
    # assumptions (~1,000 documents and 2-5 collections per user): 1,000
    # documents x the measured ~34 chunks per article = 34,000, plus headroom
    # -> 50,000. The per-user ceiling is therefore 5 x 50k = 250k chunks (about
    # 4 GB of 4096-d vectors), i.e. a tenant is bounded by `max_collections`
    # long before by bytes.
    #
    # Applies to USER-CREATED collections only — ones with an active owner row
    # whose owner is not an admin (api/access.py::is_user_created); curated
    # corpora (no owner row, the backfill owner, or an admin creator) are exempt
    # unless the registry entry sets an explicit `max_chunks` override
    # (CollectionSpec.max_chunks: None = derive from this default, 0 = exempt,
    # N = cap at N). Enforced ONCE per ingest job, before the first write: one
    # live `VectorStore.count()` round-trip per job (never per chunk); when
    # `live + incoming > cap` the WHOLE job is refused with the job error label
    # `chunk_cap_exceeded` and a report of `live`, `incoming`, `cap` and how
    # many would have fit — never a partial write. A replay (restore) is never
    # capped: it re-admits what was already admitted. 0 disables the default.
    max_chunks_per_collection: int = 50_000
    # Capability gate on POST /v1/collections (issue #287): whether a non-admin
    # principal may create a collection at all. ADR-0003 opened creation to any
    # authenticated caller — this switch is for the deployment that must NOT do
    # that, e.g. a read-only service account handed to an integration partner:
    # every other write already 403s a non-owner, but creation is object-less
    # (there is nothing yet to check an ACL against), so it was the one write
    # that always succeeded regardless of intent. False makes creation
    # admin-only; true (default) is the historical ADR-0003 behaviour, byte-for-
    # byte unchanged. This is a blunt env-wide capability switch, not a role —
    # see #287 for why a `reader` role was rejected (it would invert the
    # fail-closed floor `_bearer_role` relies on). A `creators` group is the
    # planned per-person-granularity follow-up (#245 already has what it needs);
    # add one only when that granularity is actually asked for.
    allow_user_collection_create: bool = True
    # Per-OWNER collection quota (issue #290), distinct from the per-tenant
    # MAX_COLLECTIONS above: that cap protects the tenant's physical stores and
    # applies to every collection regardless of who owns it; this one bounds how
    # many collections any single principal may OWN at once, counting active
    # `owner` rows in the ACL store (`AclStore.count_owned`). Measured/assumed
    # usage today is 2-5 collections per person (issue #290's 2026-08-24 sizing
    # comment); 5 constrains nobody legitimate. Enforced on ACQUISITION — both
    # `POST /v1/collections` (create) and `POST /v1/collections/{id}/owner`
    # (transfer) — not just creation, which is trivially evadable (create at the
    # limit, transfer one away, create again) and weaponisable (transfer junk
    # collections onto a colleague to fill their quota). Admins are EXEMPT from
    # this quota (an explicit, logged branch) — unlike MAX_COLLECTIONS, which is
    # physical protection ADR-0005 decision 5 applies to admins too; this one is
    # an authorization-style limit on acquisition, not a physical bound.
    # `backfill_collection_owners` never refuses regardless of this setting — it
    # repairs existing state at boot and logs a WARNING when a repair pushes an
    # owner over quota instead. 0 disables the cap (mirrors MAX_COLLECTIONS).
    max_collections_per_owner: int = 5
    # Refuse an ingest whose build spec differs from the target collection's
    # recorded provenance (spec_hash over model|dim|chunk descriptor). Writing
    # vectors from a different embedder, or chunks from a different chunker, into
    # an existing index produces an incoherent corpus with no error — this turns
    # that into a 409. Only fires when a manifest exists to compare against
    # (`collection_manifest_dir` set) and both sides state the field concretely,
    # so a pre-manifest corpus is never blocked. Set false to override (e.g. a
    # deliberate in-place rebuild of a collection).
    collection_spec_guard: bool = True
    # Runtime model registry (Phase 1): a JSON file persisting registered models
    # and hot-swappable task assignments (llm / reranker). Empty → in-memory only
    # (CRUD works, but nothing is persisted across restarts).
    models_registry_file: str = ""
    # SSRF gate for model registration: a registered model's base_urls must each
    # start with one of these prefixes (the server *calls* those URLs). Defaults
    # to loopback only; widen explicitly for real backends. Accepts a comma list
    # or a JSON array from the environment (like embedding_endpoints).
    model_url_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost", "http://127.0.0.1"]
    )

    @field_validator("model_url_allowlist", mode="before")
    @classmethod
    def _split_model_url_allowlist(cls, value: object) -> object:
        return _split_list_env(value)
    # Content-address DERIVED collection names over the full build spec (model +
    # dim + chunk descriptor) instead of (model, dim) only. Off by default so
    # existing derived names are byte-for-byte unchanged; turn on so that
    # re-ingesting the same model with a different chunker routes to a NEW
    # collection instead of silently overwriting the old one. Explicit/registry
    # names bypass derivation and are unaffected either way.
    collection_name_include_chunk: bool = False
    # Provenance manifests: one JSON file per collection recording its full build
    # spec (model, dim, embedding endpoints, chunk method/params, ingest time,
    # count). Empty (default) disables them (no read/write — unchanged). When set,
    # the ingest path writes a verified manifest and GET /v1/collections reports
    # it instead of trusting the registry's operator-asserted labels.
    collection_manifest_dir: str = ""

    # Embedding backend (used at both ingest and query time)
    embedding_api: str = "sidecar"          # sidecar | openai
    embedding_sidecar_url: str = "http://localhost:50053"
    # Optional fan-out: multiple embedding endpoints (e.g. vLLM replicas across
    # the H200s). When set, requests are load-balanced across them with failover;
    # empty falls back to the single embedding_sidecar_url. max_concurrency bounds
    # total in-flight embedding requests (backpressure).
    # Accepts either a comma-separated string or a JSON array, e.g.
    #   EMBEDDING_ENDPOINTS=http://h1:8000,http://h2:8000
    #   EMBEDDING_ENDPOINTS=["http://h1:8000","http://h2:8000"]
    # NoDecode: skip pydantic-settings' default JSON decode so the validator below
    # receives the raw env string and can accept comma-separated input too.
    embedding_endpoints: Annotated[list[str], NoDecode] = Field(default_factory=list)
    embedding_max_concurrency: int = 8
    # Probe path appended to each fan-out endpoint for health checks. The default
    # suits the sidecar and vLLM's OpenAI server; override for backends that
    # expose readiness elsewhere (a backend with no /health would otherwise read
    # as permanently unhealthy and degrade pool routing).
    embedding_health_path: str = "/health"

    @field_validator("embedding_endpoints", mode="before")
    @classmethod
    def _split_embedding_endpoints(cls, value: object) -> object:
        return _split_list_env(value)
    # embedding_model_dim is the vector dimension; defaults to BGE-base.
    # When embedding_api == "openai", set embedding_model to the OpenAI/vLLM model name.
    embedding_model_dim: int = 768

    # Ingestion: publisher profile driving scholarly-metadata enrichment (DOI
    # prefix, filename->DOI rule, front-matter name set). "asm" is the default,
    # built-in profile (10.1128 + the jvi.02415-06 filename rule) and keeps
    # behaviour identical to before profiles existed. See
    # ``ragstack.ingestion.enrich.PROFILES`` for the registry; resolve a profile
    # with ``ragstack.ingestion.enrich.resolve_profile(settings.publisher_profile)``.
    publisher_profile: str = "asm"

    # DOI metadata enrichment (ragstack.ingestion.doi_metadata). Resolves each
    # ingested document's DOI against Crossref (DataCite as fallback) and fills
    # *missing* bibliographic fields — title, authors, journal, year, publisher,
    # publication_type, url — so PDFs that carry no usable metadata stop showing
    # up as bare filenames in citations.
    #
    # OFF by default and network-touching: leaving it off keeps ingest behaviour
    # byte-for-byte unchanged and offline/air-gapped deployments unaffected.
    # Precedence is always "existing explicit metadata wins, enrichment fills
    # gaps" — see ``doi_metadata.merge_enrichment``.
    doi_enrichment_enabled: bool = False
    # Contact address for Crossref's polite pool. Not required, but strongly
    # recommended: it routes requests to better-behaved infrastructure and lets
    # Crossref reach an operator instead of blocking the deployment outright.
    doi_enrichment_mailto: str = ""
    # Directory for the on-disk resolution cache (one JSON per DOI, negatives
    # included). Empty = in-process memory only, so a restart re-fetches; set it
    # on any real deployment so re-ingests never re-hit the API.
    doi_enrichment_cache_dir: str = ""
    doi_enrichment_timeout: float = 10.0
    # Max concurrent lookups. Keep small — this is the politeness contract, and
    # a 3000-document shard would otherwise open 3000 connections to Crossref.
    doi_enrichment_concurrency: int = 4
    # Try DataCite when Crossref authoritatively has no record (datasets,
    # preprints, repository deposits). Costs one extra request only on a
    # confirmed Crossref 404.
    doi_enrichment_datacite_fallback: bool = True
    # Override the User-Agent entirely. Empty builds a descriptive default from
    # the package version and doi_enrichment_mailto.
    doi_enrichment_user_agent: str = ""

    # Chunker defaults. fixed_token is a sliding TOKEN window (chunk_size/overlap in
    # tokens) and needs embedding_model (its HF tokenizer). See CHUNK_METHODS.
    chunk_method: str = "fixed"   # fixed | fixed_token | sentence | words | semantic
    chunk_size: int = 512
    chunk_overlap: int = 64
    # Semantic chunker (chunk_method=semantic) tunables. Embeds sentence buffers
    # to find topic breakpoints; needs the [chunking] extra (NLTK punkt) for
    # high-quality sentence splitting (a regex fallback is used otherwise).
    chunk_buffer_size: int = 3
    chunk_breakpoint_percentile: float = 80.0
    chunk_min_length: int = 500
    # Token-based chunk sizing (opt-in; default None = OFF — char-budget behaviour
    # unchanged and no tokenizer is loaded or endpoint probed at startup). When set
    # to an int, that int is the embedding model's context window: every chunk is
    # capped so its token count stays a small `reserve` below it (see
    # resolve_max_tokens), so /v1/ingest can't emit a chunk that overflows the
    # window. chunk_token_counter selects how tokens are counted: "hf" loads the
    # model's HF tokenizer (exact, needs the [chunking] extra), "endpoint" asks the
    # serving endpoint, "estimate" uses a chars-per-token heuristic.
    #
    # This setting is the ONLY way to get an inexact counter. "hf" that cannot load
    # its tokenizer refuses — at boot, since the chunker is built in lifespan —
    # rather than quietly demoting to "estimate", which sizes chunks ~1.4x off and
    # would build a differently-chunked index under an unchanged configuration.
    chunk_max_tokens: int | None = None
    chunk_token_counter: str = "hf"          # hf | endpoint | estimate

    @field_validator("chunk_method")
    @classmethod
    def _validate_chunk_method(cls, value: str) -> str:
        # Fail fast with a clear message at config load rather than deep inside
        # make_chunker. Lazy import keeps config free of an ingestion dependency.
        from ragstack.ingestion.chunkers import CHUNK_METHODS

        if value not in CHUNK_METHODS:
            raise ValueError(
                f"chunk_method {value!r} not in {CHUNK_METHODS}"
            )
        return value

    # --- Chunk-level boilerplate (ragstack.ingestion.boilerplate) ------------ #
    # Scholarly PDFs contribute chunks that are not content: the Creative Commons
    # licence footer, the "© The Author(s)" line, the acknowledgements/funding/
    # competing-interests block, and the reference list. They are lexically about
    # everything and semantically about nothing, so they match every query weakly
    # and dominate the top-k whenever nothing scores strongly (observed live: a
    # "role of bees" query returned a CC footer, a copyright line and two
    # bibliography entries in its top 5, and the LLM then cited a paper it had
    # only seen inside the retrieved bibliography).
    #
    # DETECTION is ON by default and purely additive: non-body chunks get
    # metadata["section"] and metadata["is_boilerplate"], nothing is removed, and
    # a body chunk's payload is byte-for-byte what it was. That makes the problem
    # measurable and filterable without a behaviour change.
    boilerplate_detection_enabled: bool = True
    # DROPPING is OFF by default: a false positive is permanent for that ingest,
    # so removing content is an explicit operator decision. Turn it on for
    # scholarly-PDF corpora (it also saves the embed cost of those chunks); drops
    # are counted and logged per source, never silent. A document whose chunks are
    # ALL boilerplate is never emptied — see BoilerplateFilter's guard.
    boilerplate_drop: bool = False
    # Threshold overrides as a JSON object, e.g.
    #   BOILERPLATE_CONFIG_JSON='{"reference_density":15,"boilerplate_sections":["references"]}'
    # One string rather than a dozen settings: the thresholds are a calibration
    # set tuned together per corpus. See BoilerplateConfig for every key and the
    # reasoning behind its default. Malformed JSON degrades to the defaults.
    boilerplate_config_json: str = ""

    # Embedding request batching (bounds request size so large documents don't
    # overflow the backend's max batch / context window).
    embedding_max_batch_items: int = 64
    embedding_max_batch_tokens: int = 8192
    embedding_chars_per_token: int = 4

    # Ingestion safety. When ingest_root is set, ingest sources must resolve
    # within it (defeats path-traversal / arbitrary-file-read). max_document_bytes
    # caps input size as a DoS guard; 0 disables the cap.
    ingest_root: str = ""
    max_document_bytes: int = 50_000_000
    # POST /v1/ingest/upload bounds (#202 phase 2c). Together with the per-file
    # max_document_bytes these cap the SHAPE of one upload request:
    #   max_upload_files              ≤ N files per request            → 413
    #   max_upload_bytes_per_request  sum of the files' bytes           → 413
    #                                 (checked against the declared sizes before
    #                                 anything is staged or written, and again as
    #                                 a running total while streaming; 0 disables)
    #   upload_content_types          content-type allowlist            → 415
    #                                 (PDFs are also %PDF-sniffed). Accepts a
    #                                 comma list or a JSON array from the env.
    # One running ingest job per principal (429 + Retry-After while a job of the
    # caller's is accepted/running) is api/deps.py::single_inflight_ingest — not
    # a setting; an admin principal is exempt (logged), like the rate bucket.
    max_upload_files: int = 50
    max_upload_bytes_per_request: int = 500_000_000
    upload_content_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "application/pdf", "text/plain", "text/markdown",
            "application/xml", "text/xml",  # JATS
        ]
    )

    @field_validator("upload_content_types", mode="before")
    @classmethod
    def _split_upload_content_types(cls, value: object) -> object:
        return _split_list_env(value)

    # Sharded (batch/directory) ingestion: how many documents process at once
    # and how many per shard. Bounds in-flight work for large directories.
    ingest_concurrency: int = 4
    ingest_shard_size: int = 64

    # Ingest distribution backend (ADR-0001 offline plane). "local" runs shards
    # in-process (LocalAsyncIORunner); "gowe" submits them to the GoWe CWL engine
    # as a scatter workflow (GoWeBackend). See make_ingest_backend.
    #
    # In "gowe" mode the API submits AS THE CALLER (#203/#353): POST /v1/ingest
    # takes a Workspace reference (``ws://…`` or ``/<user>/home/…``) and
    # POST /v1/ingest/upload writes the PDFs into the caller's Workspace first;
    # both then submit gowe_workflow_cwl (the scatter-per-PDF workflow) with
    # ``ws://`` File inputs, the caller's BV-BRC token in the Authorization
    # header, and output_destination = the collection's ``versions/`` folder.
    # The engine pre-stages the inputs and post-stages the archive with that
    # token; nothing is staged on the API host. Needs a bearer (BV-BRC) caller
    # and a registered collection — an API-key principal gets 401.
    ingest_backend: str = "local"           # local | gowe
    # GoWe engine connection + workflow (used only when ingest_backend=gowe).
    gowe_url: str = "http://localhost:8091"
    gowe_token: str = ""                     # empty → GoWeClient loads a BV-BRC token file
    gowe_workflow_cwl: str = ""              # ABSOLUTE path to the scatter CWL (a relative
    #                                          path is resolved against the process CWD)
    gowe_workflow_name: str = "ragstack-bulk-ingest"
    # Static (non-shards) CWL inputs as a JSON object — collection, embedding
    # endpoints, chunk config, … matching the workflow's inputs. The collection
    # MUST match the served collection or ingest writes where the API can't read.
    gowe_workflow_inputs_json: str = "{}"
    gowe_worker_group: str = ""              # route to a GoWe worker group (submission label)
    gowe_poll_interval: float = 5.0
    gowe_timeout: float = 7200.0
    # The workflow's scattered File[] input and its per-item receipts output
    # (#203 blocker b). Defaults match cwl/pdf-ingest-scatter.cwl — the workflow
    # the API drives; the JSONL bulk workflow (ingest-bulk.cwl) names its input
    # "shards", so set GOWE_SHARDS_INPUT_KEY=shards to drive that one. Safe to
    # default to "pdfs": no INGEST_BACKEND=gowe deployment could exist before
    # this — both ingest endpoints refused every non-local backend with 501.
    gowe_shards_input_key: str = "pdfs"
    gowe_receipts_output_key: str = "receipts"
    # How long to wait, after the engine reports COMPLETED, for it to post-stage
    # the archive to output_destination (output_state → delivered/upload_failed).
    # Finalize and post-stage happen in one scheduler tick, so COMPLETED can be
    # observed before delivery is decided; an engine with no Workspace stager
    # never delivers, and this bound turns that into a loud failure.
    gowe_output_wait_timeout: float = 600.0

    # BV-BRC Workspace (ragstack/workspace.py, #356): the JSON-RPC endpoint the
    # API writes a user's collection folder / sources to — always with the
    # caller's own token, never a server identity. Bytes go to Shock at the URL
    # the Workspace returns per upload node, so there is no separate Shock setting.
    workspace_url: str = "https://p3.theseed.org/services/Workspace"
    workspace_timeout: float = 60.0            # per-request bound in seconds

    # Per-tenant concurrency cap (fairness on the shared embedding fleet): the
    # max in-flight ingest items + queries one tenant may have at once. 0 =
    # unlimited. For real isolation set this below embedding_max_concurrency —
    # otherwise tenants still all contend on the embedder pool's global cap and
    # the per-tenant bound buys little.
    tenant_max_concurrency: int = 0

    # Per-principal request-RATE limits (issue #87), enforced by
    # ragstack.ratelimit.TokenBucketLimiter via api/deps.py::rate_limited and keyed
    # on principal.tenant — a per-process, in-memory token bucket (see the module
    # docstring for the multi-process caveat: N replicas give N times the rate).
    # <= 0 disables the corresponding bucket. One bucket covers both
    # POST /v1/ingest and POST /v1/ingest/upload (they're the same write path by
    # a different transport). An admin principal is exempt from the bucket
    # (logged) but NOT from the request bounds below.
    rate_limit_ingest_per_hour: int = 10
    rate_limit_collections_create_per_hour: int = 5
    rate_limit_shares_per_hour: int = 60

    # Request bounds (issue #87) — validation, not rate limiting: these cap the
    # SHAPE of a single request regardless of how often it's sent. <= 0 disables
    # a bound. Out-of-bound params are a 422; an oversized JSON body is a 413.
    #
    # 1 MB cap on the JSON body of POST /v1/ingest, POST /v1/collections and
    # POST /v1/collections/{id}/shares (ragstack.api.deps.bound_json_body).
    # POST /v1/ingest/upload is multipart and bounded per-file by
    # max_document_bytes instead — this setting does not apply to it.
    max_json_body_bytes: int = 1_000_000
    # QueryRequest.top_k / RetrieveRequest.top_k ceiling.
    max_top_k: int = 100
    # GET /v1/chunks: max entries in the comma-separated `ids` query param.
    max_chunk_ids: int = 200
    # Ceiling applied to `limit` query params on list endpoints (GET /v1/documents,
    # GET /v1/admin/service-accounts). GET /v1/jobs's own limit (<=100) is already
    # under this and is left as-is.
    max_list_limit: int = 500

    # Ingestion job tracking. "memory" is process-local (lost on restart);
    # "sqlite" is durable single-process; "postgres" is the multi-process
    # checkpoint of record for the 500k path (uses postgres_dsn).
    job_store_backend: str = "memory"       # memory | sqlite | postgres
    job_store_path: str = "ragstack_jobs.db"

    # Text index (BM25). "memory" is the dev Jaccard placeholder; "elasticsearch"
    # is the real durable BM25 backend used for hybrid retrieval.
    text_backend: str = "memory"            # memory | elasticsearch
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "ragstack"
    elasticsearch_api_key: str = ""
    # Per-request timeout (seconds) for the BM25 leg, the counterpart to
    # qdrant_timeout. None = leave the elasticsearch client on its own default,
    # which is 10s — a THIRD of the Qdrant default, on the other half of the same
    # hybrid query. Before #427 this knob did not exist: when the vector leg's
    # bound was raised to 60s as the incident's interim mitigation, there was no
    # way to give the text leg the same headroom.
    elasticsearch_timeout: float | None = None

    # Neo4j (knowledge graph). "memory" is the in-process dev graph (lost on
    # restart); "neo4j" is the durable property-graph backend (M4). Neo4j 5
    # rejects the literal password "neo4j", so the default here is "ragstack"
    # (matches config/rag.env).
    graph_backend: str = "memory"           # memory | neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ragstack"
    neo4j_database: str = ""                 # empty → driver default ("neo4j")

    # Knowledge-graph triple extraction (M4 Phase 2). Off by default so ingest
    # behaviour is unchanged. When enabled AND an LLM is configured
    # (``llm_endpoint`` set), the ingestion pipeline runs an LLM extractor over
    # each document's chunks and stores the resulting triples in the graph store.
    # An extraction failure degrades gracefully (skips the chunk) and never fails
    # the ingest. The two bounds cap LLM cost — 0 means unbounded:
    #   * kg_extraction_max_chunks         — process at most N chunks per document
    #   * kg_extraction_max_triples_per_chunk — keep at most N triples per chunk
    kg_extraction_enabled: bool = False
    kg_extraction_max_chunks: int = 0
    kg_extraction_max_triples_per_chunk: int = 0

    # Graph extraction as a collection-lifecycle step (#350, phase 6 of #201).
    # OFF the ingest critical path and off by default: nothing runs until the
    # owner (or an admin) calls POST /v1/collections/{id}/graph, which submits
    # cwl/graph-extract.cwl AS THE USER over one archived chunk version
    # (`versions/<n>/` in the owner's Workspace): extract (one LLM call per
    # chunk, `graph_extract_concurrency` in flight) → the graph leg
    # `triples.jsonl.gz` beside the version → load into the graph store scoped
    # by (tenant, collection). Budgets (#291's sibling):
    #   * graph_max_triples_per_collection — one collection's graph may hold at
    #     most this many triples (default 200,000: the 50k chunk cap x ~4
    #     triples per prose chunk, with headroom). Checked ONCE per job by the
    #     load tool with one live count; a load that would cross it is refused
    #     whole (job error `graph_cap_exceeded`, nothing loaded). 0 disables.
    #   * graph_extraction_jobs_per_owner — in-flight extraction jobs one owner
    #     may have at once (429 + Retry-After beyond it; admins exempt). A
    #     COLLECTION never has more than one in flight regardless of caller
    #     (two deltas post-staged onto the same versions/<n>/ would interleave).
    #   * graph_extraction_max_failed_fraction — the share of a version's
    #     attempted chunks whose LLM call may fail before the extract tool
    #     refuses the run as an outage (exit 1, retryable, nothing archived);
    #     every attempted chunk failing always refuses. A delivered empty leg
    #     would be permanent (idempotent per version), so an outage must not
    #     become one.
    # The workflow's LLM endpoint/model default to llm_endpoint/llm_model as
    # the WORKER sees them and the graph store URI to neo4j_uri; override any
    # of those (and add worker-side settings) through graph_extract_inputs_json,
    # a JSON object merged over the static workflow inputs. The Neo4j
    # credentials are never workflow inputs: the worker reads NEO4J_USER /
    # NEO4J_PASSWORD from its own environment.
    graph_max_triples_per_collection: int = 200_000
    graph_extraction_jobs_per_owner: int = 1
    graph_extraction_max_failed_fraction: float = 0.5
    graph_extract_concurrency: int = 8
    graph_extract_cwl: str = ""              # ABSOLUTE path; empty = the repo copy
    graph_extract_workflow_name: str = "ragstack-graph-extract"
    graph_extract_inputs_json: str = "{}"

    # PostgreSQL (metadata / job queue)
    postgres_dsn: str = "postgresql+asyncpg://ragstack:ragstack@localhost/ragstack"

    # Redis (cache / rate limiting)
    redis_url: str = "redis://localhost:6379"

    # API
    api_keys: list[str] = Field(default_factory=list)
    # Maps an API key to its tenant_id (data isolation). Keys absent from the map
    # resolve to the "default" tenant. A key mapped to "public" writes into the
    # shared public corpus everyone can read.
    api_key_tenants: dict[str, str] = Field(default_factory=dict)
    # Maps an API key to its RBAC role (authz for the dashboard/admin surface).
    # Keys absent from the map (and the keyless dev path) get ``default_role``.
    # Roles: admin (superuser) | user. "researcher" is a deprecated alias for
    # "user" (normalized with a warning); engineer/manager were removed per
    # ADR-0003 and are rejected at startup. Enforced server-side per endpoint
    # via ``require_role`` — never trusted from the client.
    api_key_roles: dict[str, str] = Field(default_factory=dict)
    # Role for an authenticated-but-unmapped key and the keyless dev path. Least
    # privilege by default: the admin/config/stats surface stays closed unless a
    # key is explicitly granted a higher role (or this is raised in dev).
    default_role: str = "user"
    # How long the API-key auth path memoizes "is this tenant a DISABLED service
    # account?" (issue #258). The check is a user-store read on a path that was
    # otherwise pure CPU, so it is cached per subject — and, exactly as with
    # ``identity_cache_ttl_seconds`` below, THIS TTL IS THE REVOCATION LAG:
    # disabling a service account takes effect within this many seconds, per
    # process. 0 disables the cache (a store read on every API-key request:
    # instant revocation, worst hot-path cost). Negative values are clamped to 0,
    # and values above 300 FAIL STARTUP (security.validate_service_account_settings)
    # — the same hard cap identity_cache_ttl_seconds gets, for the same reason:
    # this is the only revoke that does not need a restart, so an unbounded TTL
    # would silently keep a leaked key working for hours.
    # Small by default because the lookup is cheap and the lag is the point.
    service_account_disabled_cache_ttl_seconds: int = 30

    # --- Bearer admins ------------------------------------------------------ #
    # BEARER subjects the operator grants the admin role, verbatim. Entries are
    # federated 'issuer:subject' strings and MUST contain a ':' — the exact
    # inverse of the colon-free service-account rule, because a colon-free entry
    # would name an API-key tenant and silently make a machine credential admin
    # through the bearer door (use API_KEY_ROLES for those). Startup refuses
    # blank, padded, colon-free, reserved, control-bearing or over-long entries
    # (security.validate_admin_subjects_settings), and logs only the COUNT.
    #
    # This is the BREAK-GLASS path and it is checked FIRST on the auth path,
    # with no store read: it works on an empty users table (which is how a
    # deployment bootstraps its first admin — the grant endpoint is itself
    # admin-gated), it survives a user-store outage, and no database write can
    # revoke it. Removing an entry is an env edit plus a restart.
    #
    # It never leaks onto the API-key path: _principal_from_key does not consult
    # it, so the two subject namespaces stay disjoint (#243).
    admin_subjects: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("admin_subjects", mode="before")
    @classmethod
    def _split_admin_subjects(cls, value: object) -> object:
        return _split_list_env(value)
    # How long the bearer auth path memoizes "does this subject's users row say
    # admin?". Same shape as the disabled-check cache above, with the danger
    # pointing the OTHER way: THIS TTL IS THE DEMOTION LAG. A revoked admin
    # keeps admin for up to this long (per process), so it is small by default,
    # values above 300 FAIL STARTUP, and the grant/revoke route flushes this
    # process's cache immediately. 0 disables the cache (a store read on every
    # bearer request). The env allowlist above is never cached — it is a
    # frozenset membership test with no I/O.
    admin_role_cache_ttl_seconds: int = 30

    # --- Identity (bearer credentials) ------------------------------------- #
    # OFF by default. "none" means the Authorization header is not an
    # authentication input at all and every existing deployment behaves exactly
    # as before. Turning this on makes `Authorization: Bearer <credential>` a
    # second way in, resolving to tenant f"{issuer}:{subject}" with the explicit
    # ROLE_USER role — unless ADMIN_SUBJECTS or the subject's users row names it
    # an admin, and never default_role, which is `admin` on the demo box.
    identity_provider: str = "none"          # none | bvbrc | oidc
    # HARD PIN for BV-BRC tokens: the only SigningSubject URLs whose keys may
    # verify a token. BV-BRC's own validateToken.js fetches the key from whatever
    # URL the token embeds (its allowlist guard builds an Error it never throws),
    # so without this pin anyone who can serve a URL can forge any username.
    # Default is the canonical set from BV-BRC's P3AuthConstants.pm.
    identity_issuer_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://user.patricbrc.org/public_key",
            "https://user.bv-brc.org/public_key",
            "https://user.alpha.patricbrc.org/public_key",
            "https://user.beta.patricbrc.org/public_key",
        ]
    )

    @field_validator("identity_issuer_allowlist", mode="before")
    @classmethod
    def _split_identity_issuer_allowlist(cls, value: object) -> object:
        return _split_list_env(value)
    # Successful authentications are memoized for this long (bounded LRU, per
    # process). This is also the revocation lag — and BV-BRC tokens cannot be
    # revoked before expiry at all — so the startup check refuses > 300 s.
    identity_cache_ttl_seconds: int = 300
    # Public-key / JWKS cache lifetime, and the HTTP timeout for fetching them.
    identity_key_cache_ttl_seconds: int = 86400
    identity_http_timeout_seconds: float = 5.0
    # Clock skew tolerated on ID-token exp/nbf/iat, capped at 300 s.
    identity_clock_skew_seconds: int = 300
    # OIDC. The issuer is used for discovery ({issuer}/.well-known/...); the
    # client ids pin `aud` — WITHOUT them an ID token minted for any other
    # application on the same IdP would be accepted, so startup refuses an empty
    # list. allowed_issuers overrides the accepted `iss` spellings; left empty it
    # is [issuer], except for Google, which mints both accounts.google.com and
    # https://accounts.google.com (enumerated, never prefix-matched).
    identity_oidc_issuer: str = ""
    identity_oidc_client_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    identity_oidc_allowed_issuers: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    # Short label that becomes Identity.issuer and the tenant prefix ("google:123…").
    identity_oidc_issuer_label: str = "oidc"

    @field_validator(
        "identity_oidc_client_ids", "identity_oidc_allowed_issuers", mode="before"
    )
    @classmethod
    def _split_identity_oidc_lists(cls, value: object) -> object:
        return _split_list_env(value)

    # --- User profile store (ADR-0004 decision 1) --------------------------- #
    # The first verified bearer authentication upserts a profile row
    # users(subject, issuer, email, display_name, first_seen_at, last_seen_at),
    # keyed on the tenant string f"{issuer}:{sub}" — never an email. The write
    # is fire-and-forget on the auth path: auth never fails because it did.
    #   memory   (default) process-local; nothing persists (dev/tests).
    #   sqlite   a durable `users` table in `user_store_path`.
    #   postgres the same table in `user_store_dsn` (falls back to
    #            `postgres_dsn`) — the multi-process source of truth, and the
    #            backend ADR-0004's groups/shares will FK onto.
    user_store_backend: str = "memory"  # memory | sqlite | postgres
    user_store_path: str = "ragstack_users.db"
    user_store_dsn: str = ""
    # Subject that OWNS every LEGACY collection at startup (issue #243 backfill,
    # ADR-0004 decision 4). On each boot, a registry collection whose durable
    # spec records NO creator (it predates ownership / was hand-authored) and
    # whose owner row was never written is granted `owner` to this subject AND
    # `read` to the built-in `public` group — preserving the world-readable
    # behaviour those corpora always had (un-publishing later is a single
    # revoke, never resurrected). A collection whose spec DOES record a creator
    # is never published: a missing owner row is repaired to that creator and it
    # stays private. Reassignable: transfer ownership once real owners are known.
    acl_backfill_owner: str = "legacy:admin"

    # Per-tenant collection allowlist: tenant -> [collection ids] it may read/query
    # (and ingest into). A tenant ABSENT from the map is UNRESTRICTED — so an
    # operator/admin tenant sees every collection while specific orgs are confined
    # to theirs. An empty map (default) disables the feature entirely (all tenants
    # unrestricted), so single-collection and shared-explorer deployments are
    # unchanged. Lets one multi-collection API serve several orgs safely instead of
    # one server per org. env TENANT_COLLECTIONS='{"asm":["asm"],"lucid":["lucid"]}'
    tenant_collections: dict[str, list[str]] = Field(default_factory=dict)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])
    # Path prefix this API is mounted under by a reverse proxy that STRIPS it
    # (the gateway serves each deployment at /ragstack/<tenant>/api/...). It is
    # what /docs builds its openapi URL from, what /redoc builds its `spec-url`
    # from, and what the served schema advertises as its `servers` entry. Empty
    # (default) = mounted at the root, which is also direct-port access — the
    # proxy's `X-Forwarded-Prefix` then supplies it per request
    # (ragstack/api/root_path.py). Set this only where the proxy cannot be made
    # to send that header: it PINS the prefix, so a deployment reachable both
    # ways would then advertise it on the direct port too.
    #
    # It must NOT be a prefix this app is itself served under, because pinning it
    # also makes Starlette strip it before routing: ROOT_PATH=/v1 would 404 the
    # whole /v1 surface (GET /v1/stats/tenants would route as /stats/tenants).
    # A value that fails validation is rejected at import with a warning and
    # means "no proxy" — it does NOT fall back to the caller-supplied header.
    # env ROOT_PATH=/ragstack/asm/api
    root_path: str = ""

    # Retrieval defaults
    top_k: int = 5
    # Fusion + per-leg depth (hybrid retrieval). Defaults preserve the prior
    # hardcoded behaviour; exposed so retrieval quality can be tuned/benchmarked
    # without editing code (see the phantom-knob fix + ablation-harness work).
    retrieval_candidate_multiplier: int = 2   # per-leg fetch = top_k * this, before RRF
    rrf_k: int = 60                            # Reciprocal Rank Fusion constant
    # Per-document diversity: at most N chunks from any one doc_id in the final
    # top_k. 0 (default) = OFF, so existing deployments are unchanged. Without it
    # one paper can monopolize the answer — observed live, 3 of the top 5 chunks
    # came from a single document. Overflow chunks are demoted to the tail of the
    # candidate pool rather than discarded, so the result count never shrinks:
    # the cap only takes effect when there are enough other documents to fill it.
    # 2-3 is the recommended setting for a multi-paper library.
    retrieval_max_per_doc: int = 0
    # Query-time boilerplate demotion: re-classify each fused candidate (using the
    # ingest-time metadata["is_boilerplate"] stamp when present, else the same
    # pure classifier over the chunk text) and sort licence/reference/
    # acknowledgement chunks to the BACK of the candidate pool. Demotion, not
    # deletion — a demoted chunk still surfaces when nothing else is available.
    # OFF by default. Its purpose is corpora already indexed WITHOUT the flag:
    # unlike boilerplate_drop it needs no re-ingest. Costs one regex pass over
    # top_k*candidate_multiplier chunks per query.
    retrieval_demote_boilerplate: bool = False
    multiquery_n: int = 3                      # paraphrases per multi-query rewrite
    # Score stamped on graph-triple pseudo-chunks BEFORE fusion. Note it does not
    # reach the ranking: RRF fuses on rank position only and discards the
    # incoming score (scorers.RRFScorer.fuse), so today this value is inert —
    # the graph leg's weight is its position in its own list. Kept for the
    # pseudo-chunk shape; ranking the neighbourhood is a separate issue (#347).
    graph_context_score: float = 0.5
    graph_context_depth: int = 1               # graph neighbourhood hop depth
    # Minimum Triple.confidence (0–3) for a triple to enter the graph leg. 0 =
    # no filtering, i.e. today's behaviour exactly; an unstamped triple has
    # confidence 0 and passes at the default floor (fail-OPEN by design — see
    # retriever._graph_context and #347). 2 keeps only tool-corroborated triples.
    graph_min_confidence: int = 0
    # Query-side entity extraction for the graph leg (#349). The query's
    # 1..graph_query_ngram_max-grams are matched exactly (case-folded) against
    # the entity names in the caller's (tenant, collection) scope; up to
    # graph_query_entity_max matched entities — longest first, then query
    # order — each get one neighbourhood query. No model call.
    graph_query_entity_max: int = 5
    graph_query_ngram_max: int = 3
    # Answer generation
    llm_max_context_chars: int = 8000          # context budget packed into the LLM prompt
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # Cross-encoder reranking (final stage over the fused candidate pool).
    # ON by default: the hybrid result is re-fetched to a pool of
    # `rerank_candidates`, rescored by the cross-encoder sidecar, then truncated
    # to top_k. A rerank failure degrades to the fused order (never a 500), so
    # this is not gated by require_durable_backends — and an unreachable sidecar
    # costs latency, not availability, which is why it is safe to default on.
    # Set rerank_enabled=false to opt a deployment back out.
    rerank_enabled: bool = True
    rerank_candidates: int = 50
    crossencoder_sidecar_url: str = "http://localhost:50052"

    # Observability
    # Honoured since #427 — before that this was echoed by GET /v1/config and
    # written by tenant provisioning while configuring nothing at all. Parsed
    # case-insensitively and NEVER fatally: the deployed dev/demo tenants carry
    # `LOG_LEVEL=info`, which `logging.setLevel` rejects outright, and
    # .env.example has long documented a `warn` that is not a stdlib level name.
    # An unrecognised value falls back to INFO with a warning
    # (observability/logging_config.py:resolve_log_level).
    log_level: str = "INFO"
    # `logfmt` (default) | `json`. logfmt because the only consumer today is a
    # human on the host with grep — no log shipper or aggregator is deployed.
    # The json branch is built and tested, so switching is a config change.
    # NOT echoed by GET /v1/config: config_response.json is
    # `additionalProperties: false`, so adding a field there is a contract
    # change, and this setting does not warrant one.
    log_format: str = "logfmt"
    # Disable uvicorn's access log because our own per-request summary line is a
    # strict superset of it: method, path and status, plus the request id, the
    # tenant, the wall time, the per-stage breakdown and the in-flight count.
    #
    # Default TRUE since #427 W3 landed that line. The two must flip together or
    # the volume argument is false. That argument, stated correctly: the line
    # count is invariant GIVEN dampening plus this access-log replacement —
    # every request already produced one uvicorn access line and ours replaces
    # it 1:1, while W1's `log_dampen_loggers` removes the 5-14 transport lines
    # per query that root-at-INFO would otherwise have added. Bytes per line
    # grow roughly 3-4x (~100 -> ~350 B). Set FALSE to keep uvicorn's access log
    # as well, at the cost of two lines per request.
    access_log_replaced: bool = True
    # Loggers pinned to WARNING while the root level is INFO or higher, and left
    # alone at DEBUG. A setting rather than a hardcoded list so an operator can
    # change it without a code change.
    #
    # Why these four: every one is NOTSET, so before #427 they inherited a root
    # that sat at WARNING with no handler — silent by accident. Raising root to
    # INFO un-mutes them all at once, and they are HTTP transports on a path
    # this API takes several times per request. A single /v1/query makes 5
    # outbound calls minimum (embed, Qdrant, ES, rerank, LLM), 6 with query
    # rewriting and up to ~14 on the multi-collection path — one httpx INFO
    # line each. The one summary line #427 exists to produce would arrive at a
    # signal-to-noise of 1:5, worst case 1:14.
    #
    # neo4j and qdrant_client are deliberately NOT here: they sit closer to our
    # own data path and are far less chatty. The noise problem is the transports.
    # See observability/logging_config.py for what damping costs and the one
    # thing it had to carry forward — which endpoint served the embed call. W3
    # discharged that: embed_pool records it and it reaches the summary line as
    # `embed_ep=`.
    log_dampen_loggers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["httpx", "httpcore", "elastic_transport", "urllib3"]
    )
    # How often the in-process latency histogram writes its rollup line (#427
    # W4). `0` DISABLES it — no task is created at all, which is what an off
    # switch has to mean if it is not to be a busy loop.
    #
    # 300 s because the line is cumulative-since-start rather than windowed: its
    # job is to make "is p95 creeping toward QDRANT_TIMEOUT?" answerable by
    # diffing two lines out of a log, and five minutes is fine-grained enough
    # for a bound that crept over weeks — while costing at most a handful of
    # lines an hour, and none at all on a process that has served no query.
    #
    # NOT echoed by GET /v1/config, for the same reason `log_format` is not:
    # config_response.json is `additionalProperties: false`, so adding a field
    # there is a contract change and W4 deliberately makes none.
    latency_rollup_seconds: float = 300.0
    otel_exporter_otlp_endpoint: str = ""

    @field_validator("log_dampen_loggers", mode="before")
    @classmethod
    def _split_log_dampen_loggers(cls, value: object) -> object:
        return _split_list_env(value)

    # --- Collection lifecycle / restore (#358, phase 2 of #353) ------------ #
    # Kept together at the end of the class to stay clear of the ingest
    # (#203) settings above.
    #
    # `last_accessed_at` (eviction's LRU key, #359) is NEVER written per
    # request: the API keeps an in-process dirty set of touched collection ids
    # and flushes it to the registry in ONE write every this-many seconds (and
    # at shutdown).
    collection_access_flush_seconds: float = 60.0
    # How long the resolution path memoizes a collection's registry row
    # (state) — the lifecycle check on every read/ingest is one cached read.
    # This is also the cross-process lag for a state change made elsewhere
    # (an eviction by a sibling instance); transitions made by THIS process
    # invalidate the entry immediately.
    collection_state_cache_seconds: float = 5.0
    # `Retry-After` (seconds) sent with the 503 while a collection is
    # dormant/restoring.
    collection_restore_retry_after: int = 30
    # A `restoring` row older than this (its `state_changed_at`) is presumed
    # orphaned — the API process that submitted it died before flipping the
    # state — and is moved back to `dormant` so the next access restores again
    # instead of 503ing forever. Also the watcher's poll timeout.
    collection_restore_timeout: float = 3600.0
    collection_restore_poll_interval: float = 5.0
    # ABSOLUTE path to cwl/restore-collection.cwl. Empty = the repo's copy next
    # to this package (a source checkout); set it explicitly in a container.
    collection_restore_cwl: str = ""
    collection_restore_workflow_name: str = "ragstack-restore-collection"
    # Extra/overriding static inputs for the restore workflow as a JSON object
    # — typically `qdrant_url` / `es_url` AS SEEN FROM THE WORKER, when those
    # differ from this API's own `qdrant_url` / `elasticsearch_url` (the
    # defaults). The worker group comes from `gowe_worker_group`.
    collection_restore_inputs_json: str = "{}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
