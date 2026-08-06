"""Group management endpoints (issue #245 part 2, ADR-0004 decision 3).

``POST/GET /v1/groups`` and ``GET/DELETE /v1/groups/{id}`` +
``POST/DELETE /v1/groups/{id}/members`` — RAGStack-native named groups of user
subjects. A group is a share target: ``GRANT read TO @group:<id>`` (via the
share API) reaches every active member through the ONE authorization seam
(``grants_for_subject`` unions real-group shares in :mod:`ragstack.group_store`),
so a group grant is honoured at read time with no per-router SQL.

Authorization here is the group-ownership analogue of the collection-owner seam:
group create is open to any authenticated caller (they own what they create);
view is owner-or-member (a non-member gets a leak-safe 404, so a private group's
existence and membership never leak); manage (delete, add/remove members) is
owner-or-admin. Group grants stay READ-only — that guarantee is the share API's
(``create_share``), and write/owner resolution is owner-only regardless
(:mod:`ragstack.authz`). The built-in ``public`` group is viewable but never
member-editable and never deletable (the store enforces both; the API surfaces
the refusal as 409).

Store access goes through :func:`ragstack.group_store.get_group_store` — in a
running server the lifespan installs ONE store object as the user/acl/group
singletons, so the group a share names and the membership authz expands are the
same rows in the same database.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ragstack.acl_store import GRANTEE_USER
from ragstack.api.routers.collections import _DEFAULT_ISSUER, _resolve_grantee
from ragstack.api.security import ROLE_ADMIN, Principal, resolve_principal
from ragstack.group_store import (
    GroupInvariantError,
    GroupMemberRecord,
    GroupNotFoundError,
    GroupRecord,
    get_group_store,
)

log = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class GroupCreateRequest(BaseModel):
    """POST body for creating a group. The caller becomes ``owner_subject``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description="Non-empty group name, unique per owner among active groups.",
    )


class MemberAddRequest(BaseModel):
    """POST body for adding a member. ``subject`` is resolved exactly like a
    share grantee (a full ``issuer:subject`` kept verbatim, a bare BV-BRC
    username prefixed to ``bvbrc:<username>``, or ``@service:<subject>`` for a
    service account, whose subject stays colon-free)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(
        ...,
        min_length=1,
        description=(
            "A user to add to the group: a full 'issuer:subject', a bare BV-BRC "
            "username (qualified with `issuer`), or '@service:<subject>' for a "
            "service account. The resolved subject is echoed back."
        ),
    )
    issuer: str = Field(
        "bvbrc", description="Issuer used to qualify a bare username (default 'bvbrc')."
    )


class GroupInfo(BaseModel):
    """One group row, as surfaced by the API. ``active`` is derived from
    ``deleted_at`` (a computed property is not serialized, so it is explicit)."""

    id: str
    name: str
    owner_subject: str
    built_in: bool
    created_at: str
    deleted_by: str
    deleted_at: str
    active: bool


class MemberInfo(BaseModel):
    """One membership row, as surfaced by the API."""

    id: str
    group_id: str
    subject: str
    added_by: str
    added_at: str
    removed_by: str
    removed_at: str
    active: bool


class GroupsResponse(BaseModel):
    groups: list[GroupInfo]


class GroupDetailResponse(BaseModel):
    group: GroupInfo
    members: list[MemberInfo]


def _group_info(rec: GroupRecord) -> GroupInfo:
    return GroupInfo(
        id=rec.id,
        name=rec.name,
        owner_subject=rec.owner_subject,
        built_in=rec.built_in,
        created_at=rec.created_at,
        deleted_by=rec.deleted_by,
        deleted_at=rec.deleted_at,
        active=rec.active,
    )


def _member_info(rec: GroupMemberRecord) -> MemberInfo:
    return MemberInfo(
        id=rec.id,
        group_id=rec.group_id,
        subject=rec.subject,
        added_by=rec.added_by,
        added_at=rec.added_at,
        removed_by=rec.removed_by,
        removed_at=rec.removed_at,
        active=rec.active,
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="authorization store unavailable; refusing to serve (fail closed)",
    )


async def _authorize_group(
    store: object, group_id: str, principal: Principal, *, manage: bool
) -> GroupRecord:
    """Load ``group_id`` and gate access for ``principal`` (the group-ownership
    seam). Raises leak-safe :class:`HTTPException`:

    - unknown/soft-deleted group, or a caller who may not view it → **404**
      (existence is not leaked to a non-member);
    - ``manage`` requested by an active member who is not the owner → **403**;
    - store outage → **503** (fail closed).

    Returns the active :class:`GroupRecord` on allow. ``manage`` gates the
    mutating routes (delete, add/remove member) to owner-or-admin; ``view`` (the
    default) additionally admits an active member. The built-in ``public`` group
    is viewable by everyone (``is_member`` is constant-true) but only an admin
    passes the manage gate — and the store then refuses the mutation itself."""
    try:
        g = await store.get_group(group_id)  # type: ignore[attr-defined]
        is_member = False
        if g is not None and g.active:
            is_member = await store.is_member(principal.tenant, group_id)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001 — fail closed on any store outage
        raise _unavailable() from e
    if g is None or not g.active:
        raise HTTPException(404, f"unknown group {group_id!r}")
    is_admin = principal.role == ROLE_ADMIN
    is_owner = bool(g.owner_subject) and g.owner_subject == principal.tenant
    if is_admin or is_owner:
        return g
    if manage:
        # A member may see the group but not manage it — that is an honest 403 and
        # leaks nothing new. A non-member gets the same 404 as an unknown id.
        if is_member:
            raise HTTPException(
                403, f"only the owner or an admin may manage group {group_id!r}"
            )
        raise HTTPException(404, f"unknown group {group_id!r}")
    if is_member:
        return g
    raise HTTPException(404, f"unknown group {group_id!r}")


# --------------------------------------------------------------------------- #
# /v1/groups
# --------------------------------------------------------------------------- #


@router.post("/groups", response_model=GroupInfo, status_code=201)
async def create_group(
    req: GroupCreateRequest,
    principal: Principal = Depends(resolve_principal),
) -> GroupInfo:
    """Create a group owned by the caller (any authenticated principal).

    An empty/whitespace name is 422; the reserved ``public`` name or an active
    name collision for this owner is 409; a store outage is 503."""
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "group name must not be empty or whitespace")
    store = get_group_store()
    try:
        rec = await store.create_group(name, owner_subject=principal.tenant)
    except GroupInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return _group_info(rec)


@router.get("/groups", response_model=GroupsResponse)
async def list_groups(
    principal: Principal = Depends(resolve_principal),
) -> GroupsResponse:
    """The active groups the caller owns or is an active member of (oldest
    first). The implicit built-in ``public`` group is not listed."""
    store = get_group_store()
    try:
        owned = await store.list_groups_owned_by(principal.tenant)
        member = await store.list_groups_for_member(principal.tenant)
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    by_id: dict[str, GroupRecord] = {g.id: g for g in owned}
    for g in member:
        by_id.setdefault(g.id, g)
    groups = sorted(by_id.values(), key=lambda g: (g.created_at, g.id))
    return GroupsResponse(groups=[_group_info(g) for g in groups])


@router.get("/groups/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: str,
    principal: Principal = Depends(resolve_principal),
) -> GroupDetailResponse:
    """A group and its active membership (owner-or-member-or-admin). A
    non-member gets a leak-safe 404."""
    store = get_group_store()
    g = await _authorize_group(store, group_id, principal, manage=False)
    try:
        members = await store.list_members(group_id)
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return GroupDetailResponse(
        group=_group_info(g), members=[_member_info(m) for m in members]
    )


@router.delete("/groups/{group_id}", status_code=204, response_model=None)
async def delete_group(
    group_id: str,
    principal: Principal = Depends(resolve_principal),
) -> Response:
    """Soft-delete a group (owner-or-admin). The built-in ``public`` group is
    refused (409). Shares granted to the group become inert immediately."""
    store = get_group_store()
    await _authorize_group(store, group_id, principal, manage=True)
    try:
        await store.delete_group(group_id, actor=principal.tenant)
    except GroupInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except GroupNotFoundError:
        raise HTTPException(404, f"unknown group {group_id!r}") from None
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# /v1/groups/{id}/members
# --------------------------------------------------------------------------- #


@router.post(
    "/groups/{group_id}/members", response_model=MemberInfo, status_code=201
)
async def add_member(
    group_id: str,
    req: MemberAddRequest,
    principal: Principal = Depends(resolve_principal),
) -> MemberInfo:
    """Add a user to a group (owner-or-admin). The subject is resolved like a
    share grantee and echoed back; a never-logged-in user is pre-provisioned. A
    group-target form (``@public`` / ``@group:``) is rejected (no nesting, 422);
    a duplicate active membership or the built-in ``public`` group is 409.

    A **service account** goes in as ``@service:<subject>`` — a bare subject
    would be qualified to ``bvbrc:<subject>``, adding a federated identity that
    will never authenticate rather than the machine account (the echoed
    ``subject`` is how you check)."""
    store = get_group_store()
    await _authorize_group(store, group_id, principal, manage=True)
    grantee_type, subject = _resolve_grantee(req.subject, req.issuer)
    if grantee_type != GRANTEE_USER:
        raise HTTPException(
            422, "a group member must be a user, not a group (no nesting)"
        )
    try:
        rec = await store.add_member(group_id, subject, added_by=principal.tenant)
    except GroupInvariantError as e:
        raise HTTPException(409, str(e)) from e
    except GroupNotFoundError:
        raise HTTPException(404, f"unknown group {group_id!r}") from None
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return _member_info(rec)


@router.delete(
    "/groups/{group_id}/members/{subject}", status_code=204, response_model=None
)
async def remove_member(
    group_id: str,
    subject: str,
    issuer: str = Query(
        _DEFAULT_ISSUER,
        description=(
            "Issuer used to qualify a bare username, exactly as on add "
            "(default 'bvbrc'). Ignored when ``subject`` is already a full "
            "'issuer:sub' string or an '@service:<subject>' form."
        ),
    ),
    principal: Principal = Depends(resolve_principal),
) -> Response:
    """Soft-remove a member (owner-or-admin). Instant access change: a
    collection shared to the group is no longer readable by the removed user on
    the next request. ``subject`` is resolved the SAME way as on add — a bare
    username is qualified to ``issuer:sub``, ``@service:<subject>`` stays
    colon-free — so removing with the identifier that was used to add reliably
    matches. A group-target form (``@public`` / ``@group:``) is rejected (422).
    Removing a non-member is a 204 no-op."""
    store = get_group_store()
    await _authorize_group(store, group_id, principal, manage=True)
    grantee_type, resolved = _resolve_grantee(subject, issuer)
    if grantee_type != GRANTEE_USER:
        raise HTTPException(
            422, "a group member must be a user, not a group (no nesting)"
        )
    try:
        await store.remove_member(group_id, resolved, removed_by=principal.tenant)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed
        raise _unavailable() from e
    return Response(status_code=204)
