"""The P2 persona's preconditions, as data plus one pure function (#405).

``docs/testing/use-case-matrix.md`` names four personas. Conformance could only
ever express **role** (admin / not admin), so P2 — *a caller whose readable set
excludes the registry pointer's target* — was inexpressible, and every row that
needs it stayed ⚠️. #419 and #420 shipped because of exactly that: every caller
in every suite could read the tenant default, so the "cannot read the pointer"
branch was unreachable code as far as the tests were concerned.

P2's four "not"s are load-bearing, and each one has a *different* way of making
a P2 test silently vacuous:

* **auth must be configured** — with no keys and no identity provider,
  ``api/access.py::filter_readable`` and ``enforce_access`` are both no-ops, so
  every caller reads everything and nothing is being filtered;
* **P2 must not be admin** — ``authz.resolve_access``'s admin bypass returns
  allow for every action before any ownership rule runs;
* **the pointer target must not be readable by P2** — if it is (a share, or the
  settings-derived shared surface's ``public`` read grant), P2's default *is*
  the pointer and the branch under test never executes;
* **no ``TENANT_COLLECTIONS`` allowlist** — the allowlist takes a different code
  path, so a green test would be proving the allowlist rather than ownership.

This module is deliberately **pure and importable without a server**: the
conformance fixture gathers the facts over HTTP and hands them here, and
``test_persona_meta.py`` feeds the same function fabricated facts to prove each
precondition actually fails when violated. A guard nobody has watched fail is
not a guard.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Every collection this suite creates as P2 is named with this prefix, so a
#: stray one on a real server is unmistakably the conformance suite's litter and
#: not somebody's corpus.
P2_COLLECTION_PREFIX = "conf-p2-"


@dataclass(frozen=True)
class PersonaFacts:
    """What the fixture observed over HTTP, before judging it.

    Keeping the observation and the judgement apart is what makes the judgement
    testable: :func:`assert_persona_preconditions` needs no server.
    """

    #: status of ``GET /v1/collections`` with **no** credential. 401 proves the
    #: deployment authenticates callers.
    anonymous_status: int
    #: status of ``GET /v1/config`` as P2. 403 proves P2 is not admin.
    p2_config_status: int
    #: collection ids ``GET /v1/collections`` lists for P2.
    p2_ids: list[str]
    #: the id the **admin** listing reports as ``default`` — the registry
    #: pointer's target, ``D``.
    pointer_target: str
    #: ``restricted_to`` from ``GET /v1/stats/tenants`` as P2. ``None`` means no
    #: ``TENANT_COLLECTIONS`` allowlist is in play (A4).
    restricted_to: object
    #: status of the fixture's ``POST /v1/collections`` as P2, or ``None`` when
    #: P2 already owned something and no create was attempted.
    create_status: int | None = None


def assert_persona_preconditions(facts: PersonaFacts) -> None:
    """Raise ``AssertionError`` naming the first precondition that does not hold.

    Every message says which precondition failed, both sides of the comparison,
    and *why that makes the persona vacuous* — because the reader of a red build
    is usually not the person who wrote this.
    """
    assert facts.anonymous_status == 401, (
        "PRECONDITION auth_configured: an unauthenticated GET /v1/collections "
        f"returned {facts.anonymous_status}, expected 401. The server is "
        "KEYLESS, so filter_readable and enforce_access are both no-ops and "
        "every P2 assertion would pass while asserting nothing. Boot the server "
        "with API_KEYS set (see conformance/run_authz_keyed.sh)."
    )

    assert facts.p2_config_status == 403, (
        "PRECONDITION p2_is_not_admin: GET /v1/config as RAGSTACK_API_KEY_P2 "
        f"returned {facts.p2_config_status}, expected 403. 200 means the key is "
        "ADMIN — resolve_access's admin bypass allows every action before any "
        "ownership rule runs, so the branch under test is unreachable. 401 means "
        "the key is not valid on this server."
    )

    assert facts.restricted_to is None, (
        "PRECONDITION no_tenant_allowlist: GET /v1/stats/tenants reports "
        f"restricted_to={facts.restricted_to!r}, expected null. A "
        "TENANT_COLLECTIONS allowlist is in force, so what limits P2's listing "
        "is CONFINEMENT, not ownership — a different code path from the one "
        "#419 lived in, and a green test here would be proving the wrong thing."
    )

    assert facts.pointer_target, (
        "PRECONDITION pointer_exists: the admin listing reports default='' — "
        "the server has no registry pointer target for P2 to be excluded from, "
        "so 'P2 cannot read D' is trivially true and proves nothing. The server "
        "is misprovisioned for this persona."
    )

    assert facts.pointer_target not in facts.p2_ids, (
        "PRECONDITION pointer_not_readable: P2's listing CONTAINS the registry "
        f"pointer target {facts.pointer_target!r} (listing: {facts.p2_ids}). P2 "
        "can read the tenant default, so its effective default IS the pointer "
        "and the '#419 caller' branch never executes. Check for a share to P2, "
        "or for a public read grant on the settings-derived shared surface."
    )

    owned = [cid for cid in facts.p2_ids if cid != facts.pointer_target]
    assert owned, (
        "PRECONDITION p2_has_one_collection: P2's listing is empty"
        + (
            f" and POST /v1/collections as P2 returned {facts.create_status}"
            if facts.create_status is not None
            else ""
        )
        + (
            ". 403 on create means ALLOW_USER_COLLECTION_CREATE is off for "
            "non-admins (#287), so this deployment cannot provision the persona "
            "black-box; create a collection owned by the P2 subject out of band."
            if facts.create_status == 403
            else ". P2 with an EMPTY readable set is persona P3 "
            "(caller_with_nothing), a different row: it exercises the "
            "default:'' path, not the 'my default is not the tenant's' path."
        )
    )
