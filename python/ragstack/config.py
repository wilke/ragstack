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

    # Sharded (batch/directory) ingestion: how many documents process at once
    # and how many per shard. Bounds in-flight work for large directories.
    ingest_concurrency: int = 4
    ingest_shard_size: int = 64

    # Ingest distribution backend (ADR-0001 offline plane). "local" runs shards
    # in-process (LocalAsyncIORunner); "gowe" submits them to the GoWe CWL engine
    # as a scatter workflow (GoWeBackend). See make_ingest_backend.
    #
    # IMPORTANT — in "gowe" mode each manifest item's ``source`` is a **shard file**
    # the GoWe workers read (a JSONL shard fed to ingest_shard.py), NOT an arbitrary
    # document: the workflow, not the in-process pipeline, does the loading. Build
    # the manifest from pre-sharded JSONL files. (Transparently sharding arbitrary
    # /v1/ingest documents to GoWe is a separate follow-up.)
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

    # Per-tenant concurrency cap (fairness on the shared embedding fleet): the
    # max in-flight ingest items + queries one tenant may have at once. 0 =
    # unlimited. For real isolation set this below embedding_max_concurrency —
    # otherwise tenants still all contend on the embedder pool's global cap and
    # the per-tenant bound buys little.
    tenant_max_concurrency: int = 0

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
    # Roles: admin (superuser) | engineer | manager | researcher. Enforced
    # server-side per endpoint via ``require_role`` — never trusted from the client.
    api_key_roles: dict[str, str] = Field(default_factory=dict)
    # Role for an authenticated-but-unmapped key and the keyless dev path. Least
    # privilege by default: the admin/config/stats surface stays closed unless a
    # key is explicitly granted a higher role (or this is raised in dev).
    default_role: str = "researcher"

    # --- Identity (bearer credentials) ------------------------------------- #
    # OFF by default. "none" means the Authorization header is not an
    # authentication input at all and every existing deployment behaves exactly
    # as before. Turning this on makes `Authorization: Bearer <credential>` a
    # second way in, resolving to tenant f"{issuer}:{subject}" with the explicit
    # ROLE_RESEARCHER role — never default_role, which is `admin` on the demo box.
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

    # Per-tenant collection allowlist: tenant -> [collection ids] it may read/query
    # (and ingest into). A tenant ABSENT from the map is UNRESTRICTED — so an
    # operator/admin tenant sees every collection while specific orgs are confined
    # to theirs. An empty map (default) disables the feature entirely (all tenants
    # unrestricted), so single-collection and shared-explorer deployments are
    # unchanged. Lets one multi-collection API serve several orgs safely instead of
    # one server per org. env TENANT_COLLECTIONS='{"asm":["asm"],"lucid":["lucid"]}'
    tenant_collections: dict[str, list[str]] = Field(default_factory=dict)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Retrieval defaults
    top_k: int = 5
    # Fusion + per-leg depth (hybrid retrieval). Defaults preserve the prior
    # hardcoded behaviour; exposed so retrieval quality can be tuned/benchmarked
    # without editing code (see the phantom-knob fix + ablation-harness work).
    retrieval_candidate_multiplier: int = 2   # per-leg fetch = top_k * this, before RRF
    rrf_k: int = 60                            # Reciprocal Rank Fusion constant
    multiquery_n: int = 3                      # paraphrases per multi-query rewrite
    graph_context_score: float = 0.5           # RRF score for graph-triple pseudo-chunks
    graph_context_depth: int = 1               # graph neighbourhood hop depth
    # Answer generation
    llm_max_context_chars: int = 8000          # context budget packed into the LLM prompt
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # Cross-encoder reranking (final stage over the fused candidate pool).
    # Off by default; when enabled, the hybrid result is re-fetched to a pool of
    # `rerank_candidates`, rescored by the cross-encoder sidecar, then truncated
    # to top_k. A rerank failure degrades to the fused order (never a 500), so
    # this is not gated by require_durable_backends.
    rerank_enabled: bool = False
    rerank_candidates: int = 50
    crossencoder_sidecar_url: str = "http://localhost:50052"

    # Observability
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
