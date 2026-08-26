"""Shared fixtures for in-process API tests.

The app's lifespan builds its singletons against real infra (Qdrant, an
embedding endpoint) and is not triggered by httpx's ASGITransport. This fixture
populates ``app.state`` with in-memory, network-free doubles so the API can be
exercised without standing up any services.
"""
from __future__ import annotations

import tempfile
import uuid

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.deps import build_rate_limiters
from ragstack.api.main import app
from ragstack.api.model_registry import ModelRegistry
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import InMemoryJobStore
from ragstack.quota import TenantQuota
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.rewriting.rewriters import PassthroughRewriter
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore

#: The id of the conftest's settings-derived (legacy shared-surface) entry —
#: its physical collection name. `default` is the POINTER at it, never an entry
#: (#276); tests that used to grant/query the collection `default` name this.
SHARED_ID = "ragstack"


class _FakeEmbedder:
    """Deterministic constant-dimension embedder — no network."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _StateRetriever:
    """The default collection entry's retriever, proxied to the CURRENT
    ``app.state.retriever``. The query path resolves through the registry
    (``registry.resolve(...).retriever``), so tests that swap ``app.state.retriever``
    (e.g. the reranking tests) keep working without also rebuilding the registry."""

    async def retrieve(self, *args: object, **kwargs: object) -> object:
        return await app.state.retriever.retrieve(*args, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_qdrant(monkeypatch):
    """Never touch a real Qdrant from a test. ``POST /v1/collections`` builds a
    live ``QdrantVectorStore`` and calls ``ensure_collection`` — with the default
    ``QDRANT_URL`` (:6333) that would create stray collections on whatever Qdrant
    is reachable (e.g. a prod instance on the dev host, or CI). Pin it to a dead
    port so the ensure step fails fast and is swallowed (best-effort), leaving the
    registry/response assertions intact and nothing created."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "qdrant_url", "http://localhost:6399")


@pytest.fixture(autouse=True)
def _acl_store():
    """A fresh in-memory ACL store per test, seeded exactly like the startup
    backfill would for the conftest's pre-existing shared collection (id
    ``SHARED_ID`` — its physical name; ``default`` is the pointer, #276): owned
    by ``legacy:admin`` and ``read``-granted to the ``public`` group (so it stays
    world-readable, the pre-ownership behaviour). ASGITransport skips the lifespan,
    so nothing else installs or backfills the store.

    Seeded synchronously (writing the rows directly) so this stays a plain sync
    fixture usable by both async and sync tests without touching an event loop."""
    from ragstack.acl_store import (
        GRANTEE_GROUP,
        GRANTEE_USER,
        PERM_OWNER,
        PERM_READ,
        PUBLIC_GROUP,
        InMemoryAclStore,
        ShareRecord,
        reset_acl_store,
        set_acl_store,
    )

    store = InMemoryAclStore()

    def _seed(grantee_type: str, grantee_id: str, permission: str) -> None:
        rec = ShareRecord(
            id=uuid.uuid4().hex,
            collection_id=SHARED_ID,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by="system:backfill",
            granted_at="2020-01-01T00:00:00+00:00",
        )
        store._shares[rec.id] = rec

    _seed(GRANTEE_USER, "legacy:admin", PERM_OWNER)
    _seed(GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ)
    set_acl_store(store)
    yield store
    reset_acl_store()


@pytest.fixture(autouse=True)
def _clear_auth_caches():
    """The auth path memoizes two per-subject verdicts in process-wide module
    state, each with a TTL: "is this tenant a disabled service account?" (the
    API-key path, issue #258) and "does this subject's users row say admin?"
    (the bearer path). Tests reuse subjects like ``default``/``owner`` across
    modules and swap the user-store singleton underneath them, so a cached
    answer from one test would silently decide the next one — and for the role
    cache that means one test's admin leaking into another's assertions. Clear
    both on setup and teardown."""
    from ragstack.api.security import reset_disabled_cache, reset_role_cache

    reset_disabled_cache()
    reset_role_cache()
    yield
    reset_disabled_cache()
    reset_role_cache()


@pytest.fixture(autouse=True)
def _enable_ingest(monkeypatch):
    """``POST /v1/ingest`` fails closed with 503 when ``ingest_root`` is unset — an
    unconfined ``source`` is an arbitrary server-side file read. Unset is the
    default, so point the root at the temp dir that ``tmp_path`` lives under;
    otherwise every ingest test would be asserting against the gate rather than
    the behaviour it is about. Tests that exercise the gate set it back to ``""``
    themselves (their monkeypatch is applied after this one, so it wins)."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", tempfile.gettempdir())


@pytest_asyncio.fixture
async def client():
    embedder = _FakeEmbedder()
    vector_store = InMemoryVectorStore()
    text_index = InMemoryTextIndex()
    job_store = InMemoryJobStore()
    pipeline = IngestionPipeline(
        loader=default_loader_registry(),
        chunker=RecursiveCharacterChunker(),
        embedder=embedder,
        vector_store=vector_store,
        text_index=text_index,
    )

    tenant_quota = TenantQuota(0)
    ingestor = ShardedIngestor(
        pipeline,
        LocalAsyncIORunner(max_concurrency=4),
        shard_size=64,
        job_store=job_store,
        quota=tenant_quota,
    )

    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.text_index = text_index
    app.state.graph_store = InMemoryGraphStore()
    app.state.job_store = job_store
    app.state.pipeline = pipeline
    app.state.ingestor = ingestor
    app.state.generator = None  # no LLM by default → placeholder answer
    app.state.tenant_quota = tenant_quota
    # Fresh per test, built from CURRENT settings — so a test's
    # monkeypatch.setattr(settings, "rate_limit_...", ...) takes effect, and no
    # test starts with a bucket already drawn down by a previous one.
    app.state.rate_limiters = build_rate_limiters()
    app.state.retriever = HybridRetriever(vector_store, text_index, embedder)
    app.state.rewriters = {"passthrough": PassthroughRewriter()}  # no LLM in tests
    app.state.reranker = None  # rerank off by default → fused order
    # The lifespan builds app.state.collections (the multi-collection registry the
    # query/retrieve/collections routers resolve through); ASGITransport skips the
    # lifespan, so build a one-entry registry over the in-memory doubles here — the
    # single-collection path. The entry carries its PHYSICAL name as its id, as
    # the real registry build does (#276); `default` is the pointer at it.
    app.state.collections = CollectionRegistry(
        [
            CollectionEntry(
                id=SHARED_ID, label=SHARED_ID, collection=SHARED_ID,
                model="test-model", dim=4, chunk_method="fixed", chunk_size=None,
                chunk_overlap=None, chunk_params={},
                is_shared_surface=True, retriever=_StateRetriever(),
                vector_store=vector_store, text_index=text_index,
            )
        ],
        default_id=SHARED_ID,
    )
    # Model registry (Phase 1) + a real http client for apply_assignment to hand
    # to any swapped OpenAILLM/SidecarReranker (construction only — no network).
    state_http = httpx.AsyncClient()
    app.state.http_client = state_http
    app.state.model_registry = ModelRegistry(
        [], {}, allowlist=["http://localhost", "http://127.0.0.1"]
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await state_http.aclose()


# --------------------------------------------------------------------------- #
# The `caller_without_default_access` persona (#419)
# --------------------------------------------------------------------------- #
#
# The reason #419 shipped is not a missing assertion: it is that EVERY test
# caller could read the tenant's registry default, so the "cannot read the
# pointer" branch was unreachable in every suite. This is that caller, as a
# named, reusable fixture rather than inline setup — authenticated, non-admin,
# read on exactly one collection which is NOT the registry pointer target, and
# no ``TENANT_COLLECTIONS`` allowlist (so the allowlist branch is not what
# saves it).
#
# Insertion order is deliberate: ``C_readable`` is registered FIRST and the
# pointer names ``C_default``, so "the pointer when visible, else the first
# visible entry" is discriminating for both arms — the sharee's answer is the
# pointer (NOT entries[0]) and the persona's is entries[0] (NOT the pointer).

#: ``C_readable`` — the persona's only readable collection. Registered first.
PERSONA_READABLE_ID = "personal-mine"
#: ``C_default`` — the registry pointer target. Private; the persona holds
#: nothing on it. Registered second, so it is never ``entries[0]``.
PERSONA_DEFAULT_ID = "curated-corpus"

#: Distinct seeded content per collection. The singular query path stamps no
#: ``collection`` on its sources (#253 — ``conformance/test_multi_collection.py``
#: pins the absence), so "which collection served this?" is only observable
#: through the content that comes back.
PERSONA_READABLE_TEXT = "marmalade recipes from the personal corpus"
PERSONA_DEFAULT_TEXT = "curated tokenizer benchmarks from the tenant corpus"

#: API keys → subjects. ``persona`` is the affected user; ``sharee`` is the
#: control arm (a NON-ADMIN who can read the pointer target — an admin would
#: pass vacuously through the authz bypass); ``curator`` owns the pointer
#: target; ``outsider`` can read NOTHING (a #201 user in the seconds before
#: their personal collection is provisioned).
PERSONA_KEYS = {
    "persona": "k-persona",
    "sharee": "k-sharee",
    "curator": "k-curator",
    "outsider": "k-outsider",
}


@pytest_asyncio.fixture
async def caller_without_default_access(client, monkeypatch):
    """A caller whose readable set EXCLUDES the registry pointer target.

    Depends on ``client`` so it runs after the app state (and its one-entry
    registry) is built, then replaces ``app.state.collections`` with the
    persona's two-collection registry.

    Asserts its own preconditions — see :attr:`Persona`. A persona fixture that
    cannot prove it is the persona is worse than none: with auth unconfigured
    both ``filter_readable`` and ``enforce_access`` no-op, and every test built
    on this would pass while asserting nothing (R6)."""
    from types import SimpleNamespace

    from ragstack.acl_store import GRANTEE_USER, PERM_OWNER, PERM_READ, get_acl_store
    from ragstack.api import deps, security
    from ragstack.api.access import auth_configured
    from ragstack.api.security import (
        ROLE_ADMIN,
        ROLE_USER,
        admin_subject_allowlist,
        normalize_role,
    )
    from ragstack.config import settings as cfg
    from ragstack.models import Chunk
    from ragstack.retrieval.retriever import HybridRetriever

    monkeypatch.setattr(security.settings, "api_keys", list(PERSONA_KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {v: k for k, v in PERSONA_KEYS.items()},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)
    # No allowlist: the `allowed is not None` branch must not be what saves it.
    monkeypatch.setattr(cfg, "tenant_collections", {})
    monkeypatch.setattr(deps.settings, "default_collection_id", "")

    def _entry(cid: str) -> CollectionEntry:
        """Its OWN vector store + text index + retriever, so which entry a
        request landed on is observable from what comes back."""
        vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
        return CollectionEntry(
            id=cid, label=cid, collection=cid, model="test-model", dim=4,
            chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
            is_shared_surface=False, owner="",
            retriever=HybridRetriever(vs, ti, app.state.embedder),
            vector_store=vs, text_index=ti, embedder=app.state.embedder,
        )

    async def _seed(entry: CollectionEntry, chunk_id: str, text: str, tenant: str) -> None:
        chunk = Chunk(
            id=chunk_id, doc_id=f"doc-{chunk_id}", content=text,
            embedding=[0.1, 0.2, 0.3, 0.4], metadata={"tenant_id": tenant},
        )
        await entry.vector_store.upsert([chunk])
        await entry.text_index.index([chunk])

    readable, default = _entry(PERSONA_READABLE_ID), _entry(PERSONA_DEFAULT_ID)
    app.state.kg_extractor = None
    app.state.doi_enricher = None
    app.state.collections = CollectionRegistry(
        [readable, default], default_id=PERSONA_DEFAULT_ID
    )
    await _seed(readable, "mine-c1", PERSONA_READABLE_TEXT, "persona")
    await _seed(default, "curated-c1", PERSONA_DEFAULT_TEXT, "curator")

    acl = get_acl_store()
    await acl.grant(
        PERSONA_READABLE_ID, GRANTEE_USER, "persona", PERM_OWNER, granted_by="persona"
    )
    await acl.grant(
        PERSONA_DEFAULT_ID, GRANTEE_USER, "curator", PERM_OWNER, granted_by="curator"
    )
    # The control arm: a non-admin who CAN read the pointer target (and the
    # persona's collection too, so "pointer wins over entries[0]" is a real
    # choice for this caller rather than the only option).
    for cid in (PERSONA_DEFAULT_ID, PERSONA_READABLE_ID):
        await acl.grant(cid, GRANTEE_USER, "sharee", PERM_READ, granted_by="curator")

    async def _make_entry(cid: str, text: str, tenant: str) -> CollectionEntry:
        """Build + seed one more collection for a test that needs a third axis
        (e.g. allowlist ∩ readable). Not registered and not granted — the test
        decides both."""
        e = _entry(cid)
        await _seed(e, f"{cid}-c1", text, tenant)
        return e

    persona = SimpleNamespace(
        client=client,
        make_entry=_make_entry,
        acl=acl,
        headers={"X-API-Key": PERSONA_KEYS["persona"]},
        sharee_headers={"X-API-Key": PERSONA_KEYS["sharee"]},
        curator_headers={"X-API-Key": PERSONA_KEYS["curator"]},
        outsider_headers={"X-API-Key": PERSONA_KEYS["outsider"]},
        readable_id=PERSONA_READABLE_ID,
        default_id=PERSONA_DEFAULT_ID,
        readable_text=PERSONA_READABLE_TEXT,
        default_text=PERSONA_DEFAULT_TEXT,
        readable_chunk_id="mine-c1",
        default_chunk_id="curated-c1",
        entry_ids=[PERSONA_READABLE_ID, PERSONA_DEFAULT_ID],  # insertion order
    )

    # --- the fixture's own vacuity guard (R6) ------------------------------- #
    assert persona.default_id != persona.readable_id, "C_default must differ from C_readable"
    assert app.state.collections.default_id == persona.default_id
    assert auth_configured() is True, (
        "auth must be CONFIGURED: filter_readable and enforce_access are both "
        "no-ops otherwise, and every test built on this persona would pass "
        "while asserting nothing"
    )
    assert normalize_role(security.settings.default_role) != ROLE_ADMIN
    assert not security.settings.api_key_roles, "no persona key may carry a role override"
    assert "persona" not in admin_subject_allowlist(), (
        "the persona must not be admin — resolve_access's admin bypass would "
        "hide the whole defect"
    )
    assert "sharee" not in admin_subject_allowlist()
    # ...and no TENANT_COLLECTIONS allowlist is in play.
    assert cfg.tenant_collections == {}
    yield persona
