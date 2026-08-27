"""Conformance: the P2 persona — a caller who cannot read the tenant default (#405).

Every conformance principal before this file could read the collection the
registry pointer names. That is why #419 ("omitting ``collection`` 404s for a
caller whose readable set excludes the pointer target") and #420 shipped: the
branch was unreachable from any suite, so no amount of green proved anything
about it. The matrix (``docs/testing/use-case-matrix.md``) marks rows **B6a**,
**F1**, **F6** ⚠️ with the note *"P2 is inexpressible in conformance"*. This
file is that note being retired.

The persona and its vacuity guard live in ``conftest.py`` /
``personas.py``; read those first — the assertions here are only meaningful
because the fixture proved, over HTTP, that the caller really is P2.

**Python-authoritative in phase 1.** The Go scaffold has no auth middleware, so
there is no ownership seam to assert against; the fixture skips there.

Still blocked, and deliberately not faked here:

* **A4** (job status is not readable across tenants) needs a second *tenant*,
  not a second principal in one tenant — tracked under #100.
* **C7** (an oversized/wrong-type upload is refused) needs C1: conformance
  still never uploads a file.
* **E1** (an evicted collection answers 503 + Retry-After) needs a real
  eviction; ``test_collections.py``'s only eviction call is ``dry_run``.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import skip_no_credential

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# B6a / F6 — the listing and the implicit query target agree, for a caller who
# cannot read the pointer
# --------------------------------------------------------------------------- #
async def test_p2_advertised_default_is_readable_and_is_not_the_pointer(
    caller_without_default_access,
) -> None:
    """``GET /v1/collections`` must advertise a ``default`` P2 can actually use.

    The #419 lie in one assertion: the field is contract-documented as *"the id
    served when a request omits ``collection``"*, so advertising the tenant
    pointer to a caller who gets a 404 when they use it is a conformance
    violation, not an open semantic question.
    """
    p2 = caller_without_default_access
    resp = await p2.client.get("/v1/collections", headers=p2.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [c["id"] for c in body["collections"]]

    assert body["default"] != p2.default_id, (
        f"P2's advertised default is the registry pointer {p2.default_id!r}, "
        "which P2 cannot read — the #419 defect exactly"
    )
    assert body["default"] in ids, (
        f"advertised default {body['default']!r} is not among the collections "
        f"P2 was shown ({ids}); the caller is being pointed at something they "
        "cannot select"
    )


#: A chunk id no corpus holds. ``GET /v1/chunks`` returns an empty list for an
#: EMPTY ``ids`` **without resolving a collection at all** (query.py's early
#: return), so a probe with no ids would 200 on any server and prove nothing —
#: the vacuity trap this issue is about, one endpoint down. A non-empty ids list
#: forces the resolution and keeps the response empty.
_PROBE_CHUNK_ID = "___conformance_p2_probe___"


#: The 404 detail a caller with an EMPTY readable set gets
#: (``api/default_collection.py::NO_ACCESSIBLE_COLLECTION``). P2's readable set
#: is not empty, so seeing this phrase means the resolution went down the wrong
#: branch — which is #419 exactly.
_NO_ACCESSIBLE = "no collection is accessible to this caller"


async def test_p2_naming_the_pointer_target_is_a_404(
    caller_without_default_access,
) -> None:
    """The control arm for everything below: P2 really cannot read ``D``.

    ``GET /v1/chunks`` with an explicit ``collection`` refuses at the ownership
    seam, before any store is touched, so this holds whether or not the run has
    a live vector backend. If it ever returned 200 the persona would be a P1 and
    every other assertion in this file would be passing for free.
    """
    p2 = caller_without_default_access
    resp = await p2.client.get(
        f"/v1/chunks?ids={_PROBE_CHUNK_ID}&collection={p2.default_id}",
        headers=p2.headers,
    )
    assert resp.status_code == 404, (
        f"P2 named the pointer target {p2.default_id!r} explicitly and got "
        f"{resp.status_code}, not 404 — so P2 CAN read it, and this file's "
        f"other assertions prove nothing: {resp.text}"
    )


async def test_p2_omitted_collection_is_not_the_419_404(
    caller_without_default_access,
) -> None:
    """#419's regression test at the C layer: ``POST /v1/query`` with no
    ``collection`` must not refuse a caller who *has* a readable collection.

    The defect was that the query path resolved the GLOBAL registry pointer and
    then 404'd on the ownership seam — telling the caller, by id, that a
    collection they had never been shown did not exist. The assertion is about
    the **resolution**, deliberately not about the answer: a self-booted
    in-memory run has a stub embedder and P2's own collection is served by a
    backend that may be unreachable, so a 5xx from the *store* is not a failure
    of this contract. What must never happen is a refusal that says P2 has
    nothing to read, or that names ``D``.
    """
    p2 = caller_without_default_access
    resp = await p2.client.post(
        "/v1/query", json={"query": "conformance p2 implicit-target probe"},
        headers=p2.headers,
    )
    assert resp.status_code != 404, (
        "POST /v1/query with `collection` omitted 404'd a caller who owns "
        f"{p2.readable_id!r} — the #419 defect: {resp.text}"
    )
    assert _NO_ACCESSIBLE not in resp.text, (
        f"the server answered {resp.status_code} with the empty-readable-set "
        f"refusal, but P2 can read {p2.readable_id!r}: {resp.text}"
    )
    assert p2.default_id not in resp.text, (
        f"the response names {p2.default_id!r}, an id P2 was never shown by "
        f"GET /v1/collections: {resp.text[:400]}"
    )


# --------------------------------------------------------------------------- #
# F1 — a collection I cannot read is refused, leak-safely
# --------------------------------------------------------------------------- #
async def test_p2_query_naming_the_pointer_target_is_refused(
    caller_without_default_access,
) -> None:
    """Create as A, call as B: the read denial is a **404**, never a 403.

    ``api/access.py`` makes this explicit — a 403 would be an existence oracle
    for private collections (403 = exists, 404 = does not), so a read the caller
    may not perform is indistinguishable from an unknown id.
    """
    p2 = caller_without_default_access
    resp = await p2.client.post(
        "/v1/query",
        json={"query": "conformance p2 probe", "collection": p2.default_id},
        headers=p2.headers,
    )
    assert resp.status_code == 404, (
        f"querying {p2.default_id!r} as a caller with no grant on it returned "
        f"{resp.status_code}, expected 404: {resp.text}"
    )


async def test_p2_private_collection_is_invisible_to_another_non_admin(
    caller_without_default_access, nonadmin_headers: dict[str, str],
) -> None:
    """P2's own collection must be neither listed nor queryable by another
    authenticated non-admin.

    The second half of the same seam: the first test proves P2 is kept out of
    somebody else's collection, this one proves somebody else is kept out of
    P2's. Both arms matter — a filter that is applied in one direction only
    reads as working right up until the day it does not.
    """
    p2 = caller_without_default_access
    if not nonadmin_headers:
        skip_no_credential(
            "needs a SECOND authenticated non-admin (RAGSTACK_API_KEY_NONADMIN) "
            "to prove P2's collection is private FROM somebody"
        )
    other_headers = nonadmin_headers
    assert other_headers != p2.headers, (
        "RAGSTACK_API_KEY_NONADMIN and RAGSTACK_API_KEY_P2 hold the SAME value; "
        "this test would be asking whether P2 can see P2's own collection. That "
        "collapse — two names for one principal — is the #405 defect itself."
    )

    listing = await p2.client.get("/v1/collections", headers=other_headers)
    assert listing.status_code == 200, listing.text
    assert p2.readable_id not in {c["id"] for c in listing.json()["collections"]}, (
        f"{p2.readable_id!r} is owned by P2 and shared with nobody, yet it "
        "appears in another non-admin's listing"
    )

    named = await p2.client.post(
        "/v1/query",
        json={"query": "conformance cross-principal probe", "collection": p2.readable_id},
        headers=other_headers,
    )
    assert named.status_code == 404, (
        f"a non-owner naming {p2.readable_id!r} got {named.status_code}, "
        f"expected 404: {named.text}"
    )


# --------------------------------------------------------------------------- #
# F6, the documents surface — a KNOWN drift, pinned so it cannot be re-lost
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN DEFECT (#450), found by this persona on its first run. "
        "`GET /v1/documents` resolves the GLOBAL registry pointer "
        "(routers/documents.py::list_documents) instead of the caller-aware "
        "default that `GET /v1/collections`, `/v1/query`, `/v1/retrieve` and "
        "`/v1/chunks` all share since #419 — so P2, who owns a perfectly "
        "readable collection, cannot list documents at all and is told about "
        "an id it was never shown. api/default_collection.py's module docstring "
        "already names documents.py as an unfixed drift site ('visibility: "
        "none'); this is the C-layer proof that it is a live defect and not "
        "just a note. strict=True so the day it is fixed, this file goes red "
        "and the xfail must be removed."
    ),
)
async def test_p2_can_list_documents(caller_without_default_access) -> None:
    """A caller with a readable collection can list its documents."""
    p2 = caller_without_default_access
    resp = await p2.client.get("/v1/documents?limit=1", headers=p2.headers)
    assert resp.status_code == 200, (
        f"P2 owns {p2.readable_id!r} but GET /v1/documents returned "
        f"{resp.status_code}: {resp.text}"
    )
    assert p2.default_id not in resp.text, (
        "the response names the registry pointer target, an id P2 was never "
        "shown by GET /v1/collections"
    )


# --------------------------------------------------------------------------- #
# The stats surface, as P2
# --------------------------------------------------------------------------- #
async def test_p2_tenant_scope_is_its_own(caller_without_default_access) -> None:
    """``GET /v1/stats/tenants`` reports P2's own scope, unconfined.

    ``restricted_to: null`` is the black-box observation the fixture uses to
    prove no ``TENANT_COLLECTIONS`` allowlist is what makes P2 look restricted
    (plan amendment A4). Asserted here as well as in the fixture so the
    *contract* — null means unconfined — is pinned by a named test and not only
    by a precondition somebody could relax.
    """
    p2 = caller_without_default_access
    resp = await p2.client.get("/v1/stats/tenants", headers=p2.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restricted_to"] is None, (
        f"restricted_to={body['restricted_to']!r}; P2 must be limited by "
        "OWNERSHIP, not by a TENANT_COLLECTIONS allowlist"
    )
    assert body["role"] != "admin", "P2 must not be admin"
    assert body["auth_enabled"] is True, "the server must be key-protected"


async def test_p2_is_refused_the_admin_surface(
    caller_without_default_access,
) -> None:
    """403, not 404 and not 200: P2 is authenticated, just not admin."""
    p2 = caller_without_default_access
    resp = await p2.client.get("/v1/config", headers=p2.headers)
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"


async def test_httpx_client_defaults_do_not_mask_the_persona(
    caller_without_default_access,
) -> None:
    """The persona's client must not be carrying the suite's default key.

    Not paranoia: the bug this whole issue is about was two env vars holding one
    value, and the near-miss while fixing it was httpx MERGING the shared
    ``client`` fixture's ``X-API-Key`` into requests that passed
    ``headers={}``. If the persona's client ever inherited the admin key, every
    assertion in this file would pass through the admin bypass and prove
    nothing.
    """
    p2 = caller_without_default_access
    assert "x-api-key" not in {h.lower() for h in p2.client.headers}, (
        "the persona client has a default X-API-Key; httpx merges client "
        "headers into every request, so the persona's own key may not be the "
        "one that authenticated"
    )
    resp = await p2.client.get("/v1/collections")
    assert resp.status_code == 401, (
        f"the persona client sent no key and got {resp.status_code}, not 401 — "
        "it is authenticating as somebody"
    )
    assert isinstance(p2.client, httpx.AsyncClient)
