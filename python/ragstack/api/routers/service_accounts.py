"""Admin: service-account registration and revocation (issue #258 part 2).

``POST/GET /v1/admin/service-accounts`` + ``POST .../{subject}/disable|enable``.
A *service account* is a machine identity authenticated by an ``X-API-Key``
secret we mint, as opposed to a human identity authenticated by a token an
external issuer signed. Its ``subject`` IS the API-key tenant string and IS the
authorization subject (``authz.resolve_access`` takes ``principal.tenant``), so a
registered service account can own collections, receive shares and join groups
under that one identifier, with no change to the authorization seam.

Naming it as a share grantee or a group member takes the explicit
``@service:<subject>`` form: those surfaces qualify a bare, colon-free value with
the default issuer (``bvbrc:<value>``), which is a *federated* subject that a
service account — colon-free by the rule below — can never authenticate as. The
``@service:`` prefix is what keeps a colon-free grantee colon-free; see
``collections._resolve_grantee``.

**This surface manages the ACCOUNT RECORD, never the credential.**
``settings.api_keys`` / ``api_key_tenants`` / ``api_key_roles`` are
pydantic-settings fields read from the environment at import; nothing in the tree
writes them, and an in-process mutation would not reach a sibling uvicorn worker
and would vanish on restart. So:

* **Provisioning a key is an operator edit plus a restart**, and the key AND its
  tenant mapping must go in the SAME edit — ``_validate_production_settings``
  fails startup if ``API_KEY_TENANTS`` is set and any configured key is unmapped.
* **Disabling here is a soft, best-effort revoke.** It is what makes a leaked key
  stoppable *without* that restart, which is the operational gap #258 names; the
  auth-time check fails open and is cached, so revocation lands within the TTL
  and not at all while the store is unreachable
  (:func:`ragstack.api.security._service_account_disabled`). The authoritative
  revoke is removing the key from ``API_KEYS`` and restarting.
* Nobody should later "fix" this by mutating ``settings``.

The responses carry the subject and the record only — never key material, a key
prefix, or a count of how many keys map to an account. ``admin.py`` states as a
contract that ``api_keys``/``api_key_tenants``/``api_key_roles`` are never read
into any response, and this router must not partially undo that. It is a separate
module from ``admin.py`` for the same reason: that one is a read-only config
viewer over an explicit allowlist, with no store dependency.

Gating is at include time in ``main.py`` (``dependencies=_admin``, i.e.
``require_role(ROLE_ADMIN)``, which also performs authentication), matching the
``/v1/admin`` precedent set by ``models_registry.py`` — no route re-gates itself.

Rows live in the ONE store object the lifespan installs as the user/ACL/group
singletons, so a service account, the shares naming it and the group memberships
expanding it are the same rows in one database.

Python-only in v1: Go has no ``X-API-Key`` authentication path, so it serves no
service-account surface (the contract records this).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ragstack.api.security import Principal, clean_stored_text, resolve_principal
from ragstack.user_store import (
    RESERVED_SERVICE_SUBJECTS,
    UserInvariantError,
    UserNotFoundError,
    UserRecord,
    get_user_store,
)

log = logging.getLogger(__name__)

router = APIRouter()

#: Same clamp the auth path applies to caller-influenced profile claims
#: (``security._PROFILE_CLAIM_MAX``). An admin-supplied ``purpose`` is far less
#: hostile than an OIDC claim, but it lands in the same table and there is no
#: reason for a second, looser rule — so it goes through the SAME
#: ``clean_stored_text`` helper (control characters stripped, then truncated),
#: not just the length half of it.
_PURPOSE_MAX = 256

#: Upper bound on a subject. It becomes the ``users`` primary key and is echoed
#: into logs and every share/membership row that names it; an admin typo pasting
#: a whole file must be a 422, not a 200 000-character table key.
_SUBJECT_MAX = 128


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class ServiceAccountCreateRequest(BaseModel):
    """POST body. ``extra="forbid"`` so a typo'd field — or, more to the point,
    an attempt to pass a key here — is a 422 rather than a silent no-op."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(
        ...,
        min_length=1,
        max_length=_SUBJECT_MAX,
        description=(
            "The account's subject, which must equal the API-key tenant the "
            "operator maps its key to. Colon-free, and never the reserved "
            f"{sorted(RESERVED_SERVICE_SUBJECTS)} tenants."
        ),
    )
    purpose: str = Field(
        "", description="Free text: what this credential is for (audit aid)."
    )


class ServiceAccountInfo(BaseModel):
    """One service-account row as surfaced by the API.

    ``active`` is spelled out rather than left to ``UserRecord.enabled``: a
    computed property is not serialized. ``created_at`` is the record's
    ``first_seen_at`` (for a machine identity, creation *is* the first event).
    ``last_seen_at`` is deliberately not surfaced — the API-key path writes
    nothing back, so it is permanently empty and would only invite someone to
    build a "last used" feature on a field nobody fills.

    ``disabled_*`` and ``enabled_*`` are both surfaced and are *history*, not
    state: ``disabled_by``/``disabled_at`` survive a re-enable (they record the
    last revocation) and ``enabled_by``/``enabled_at`` record who reversed it.
    Read ``active`` — never ``disabled_at != ""`` — for the current state.
    """

    subject: str
    purpose: str
    created_by: str
    created_at: str
    disabled_by: str
    disabled_at: str
    enabled_by: str
    enabled_at: str
    active: bool


class ServiceAccountsResponse(BaseModel):
    service_accounts: list[ServiceAccountInfo]


def _info(rec: UserRecord) -> ServiceAccountInfo:
    return ServiceAccountInfo(
        subject=rec.subject,
        purpose=rec.purpose,
        created_by=rec.created_by,
        created_at=rec.first_seen_at,
        disabled_by=rec.disabled_by,
        disabled_at=rec.disabled_at,
        enabled_by=rec.enabled_by,
        enabled_at=rec.enabled_at,
        active=rec.enabled,
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="user store unavailable; refusing to serve (fail closed)",
    )


def _clean_subject(raw: str) -> str:
    """Normalize and validate a subject, or raise a 400.

    The colon rule is the whole partition between the two authentication
    namespaces: a bearer subject is always ``f"{issuer}:{sub}"``, so a colon-free
    subject cannot collide with — or be impersonated by — a federated identity.
    It is also what lets a service subject be an ``api_key_tenants`` value in
    production at all: ``security.validate_role_settings`` rejects a coloned
    API-key tenant outright whenever an identity provider is enabled.

    The reserved-tenant rule is the other partition, and it is the one that
    matters operationally: ``default`` is what every valid-but-unmapped API key
    resolves to (``security._principal_from_key``) and ``public`` is the shared
    corpus. Registering either as a service account and disabling it would 401
    *every* such caller at once — including the admin key that would have to
    call ``/enable``, so the lockout could only be undone with an env edit and a
    restart. That is a deployment-wide kill switch wearing the costume of a
    single-account revoke, and it is refused.

    ``_check_service_account`` in the store enforces both rules too (they are
    the data layer's invariants, not the router's), but checking here keeps a
    malformed *request* a 400 and leaves 409 to mean exactly one thing: a real
    collision with an existing human account.
    """
    subject = raw.strip()
    if not subject:
        raise HTTPException(400, "subject must not be empty or whitespace")
    if len(subject) > _SUBJECT_MAX:
        raise HTTPException(
            400, f"subject must be at most {_SUBJECT_MAX} characters"
        )
    if ":" in subject:
        raise HTTPException(
            400,
            f"service subject {subject!r} must be colon-free: ':' is reserved for "
            "federated 'issuer:sub' identities and the two namespaces must stay "
            "disjoint",
        )
    if subject in RESERVED_SERVICE_SUBJECTS:
        raise HTTPException(
            400,
            f"{subject!r} is a reserved tenant and cannot be a service account: "
            f"{sorted(RESERVED_SERVICE_SUBJECTS)} are shared fallback tenants that "
            "unmapped API keys and the keyless path resolve to, so disabling one "
            "would lock out every such caller — including the admin key needed to "
            "re-enable it",
        )
    return subject


# --------------------------------------------------------------------------- #
# /v1/admin/service-accounts
# --------------------------------------------------------------------------- #


@router.post("/service-accounts", response_model=ServiceAccountInfo, status_code=201)
async def create_service_account(
    req: ServiceAccountCreateRequest,
    principal: Principal = Depends(resolve_principal),
) -> ServiceAccountInfo:
    """Register a machine identity (admin only).

    Records the account; does NOT mint a credential — see the module docstring.
    A colon-bearing/blank subject is 400; a subject that already exists as a
    human account is 409 (converting a person's row into a machine credential is
    a privilege event and is refused); re-registering an existing service account
    returns the stored row unchanged, so a provisioning script is re-runnable.
    A store outage is 503.
    """
    subject = _clean_subject(req.subject)
    store = get_user_store()
    try:
        rec = await store.create_service_account(
            subject,
            created_by=principal.tenant,
            # Control characters stripped as well as truncated: this value is
            # echoed by GET and lands in logs and terminals, where a raw ESC or
            # NUL is an output-context injection, not free text.
            purpose=clean_stored_text(req.purpose, _PURPOSE_MAX),
        )
    except UserInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    log.info(
        "service account %r registered by %r", rec.subject, principal.tenant
    )
    return _info(rec)


@router.get("/service-accounts", response_model=ServiceAccountsResponse)
async def list_service_accounts(
    created_by: str = Query(
        "",
        description=(
            "Narrow to the accounts this admin subject minted. Empty (default) "
            "lists every registered service account."
        ),
    ),
    limit: int = Query(100, ge=1, le=1000),
    principal: Principal = Depends(resolve_principal),
) -> ServiceAccountsResponse:
    """Every registered service account, oldest first (admin only).

    Disabled accounts are included: disabling is soft state, not a deletion, and
    the audit trail is the point. Unregistered API-key tenants do not appear —
    registration is opt-in, so this enumerates the accounts an admin chose to
    register, NOT every key the server accepts (which the API deliberately
    cannot see).
    """
    store = get_user_store()
    try:
        recs = await store.list_service_accounts(created_by=created_by, limit=limit)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return ServiceAccountsResponse(service_accounts=[_info(r) for r in recs])


async def _set_disabled(subject: str, principal: Principal, disable: bool) -> Response:
    """Shared disable/enable body: 204 on success, 404 unknown, 409 for a human
    row or a self-disable, 503 on a store outage. Idempotent — the store returns
    the row unchanged when the requested state already holds."""
    resolved = _clean_subject(subject)
    if disable and resolved == principal.tenant:
        # Self-lockout guard. The disabled check runs on the API-key auth path,
        # so the very next request from this caller — including the ``/enable``
        # that would undo this — is a 401. There is no other API route back:
        # recovery would be an ``API_KEYS``/``API_KEY_TENANTS`` edit plus a
        # restart, or a direct write to the users table. Refuse instead, and say
        # what to do: revoking a machine account is somebody else's admin key.
        raise HTTPException(
            409,
            f"refusing to disable {resolved!r}: it is the account you are "
            "authenticating as, and the disabled check runs on this same "
            "API-key path — the next request, including the /enable that would "
            "undo it, would be 401 with no way back through the API. Use a "
            "different admin credential, or remove the key from API_KEYS and "
            "restart (the authoritative revoke).",
        )
    store = get_user_store()
    try:
        if disable:
            await store.disable_service_account(resolved, actor=principal.tenant)
        else:
            await store.enable_service_account(resolved, actor=principal.tenant)
    except UserNotFoundError:
        raise HTTPException(404, f"unknown service account {resolved!r}") from None
    except UserInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    # Drop the memoized auth-path answer for this subject so the change is not
    # waiting out a TTL in THIS process. It still is in every sibling worker —
    # the TTL is the revocation lag, and this flush narrows it, never removes it.
    from ragstack.api.security import reset_disabled_cache

    reset_disabled_cache()
    log.info(
        "service account %r %s by %r",
        resolved,
        "disabled" if disable else "enabled",
        principal.tenant,
    )
    return Response(status_code=204)


@router.post("/service-accounts/{subject}/disable", status_code=204, response_model=None)
async def disable_service_account(
    subject: str,
    principal: Principal = Depends(resolve_principal),
) -> Response:
    """Soft-disable a service account (admin only); its key is then rejected
    with 401 on the API-key path.

    **This is a soft revoke.** The auth-time check is cached (revocation lands
    within ``SERVICE_ACCOUNT_DISABLED_CACHE_TTL_SECONDS``, per process) and fails
    open when the user store cannot answer — an unreachable store means a
    disabled account keeps working rather than every API-key caller being locked
    out. The authoritative revoke stays "remove the key from ``API_KEYS`` and
    restart". Idempotent.

    Disabling **your own** subject is a 409: the check runs on the path you just
    authenticated on, so it would 401 you out of the ``/enable`` that undoes it.
    (Registering the reserved ``default``/``public`` tenants is likewise refused
    at creation — the same lockout, one step earlier.)"""
    return await _set_disabled(subject, principal, True)


@router.post("/service-accounts/{subject}/enable", status_code=204, response_model=None)
async def enable_service_account(
    subject: str,
    principal: Principal = Depends(resolve_principal),
) -> Response:
    """Re-enable a disabled service account (admin only). The key authenticates
    again within the disabled-check cache TTL. Idempotent."""
    return await _set_disabled(subject, principal, False)
