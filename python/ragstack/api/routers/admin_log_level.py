"""Runtime log-level control (#427, follow-on to W1).

``GET`` / ``PUT`` / ``DELETE /v1/admin/log-level`` — read the level in effect in
this process, change it live, or drop back to what configuration says. The
owner's requirement was one sentence: *"make it set-able on demand via api call
so we don't have to reload the service."*

Admin-gated at include time (``api/main.py``) like every ``/v1/admin`` route, so
authorization is enforced by construction rather than by remembering to add a
dependency here. A non-admin gets 403 and a caller with no credential gets 401,
both before this module runs at all.

The mechanism, the bounds on logger names, the ordering rule between damping and
overrides, and why the audit line survives the change it audits all live in
:mod:`ragstack.observability.log_control`. This file is the HTTP shape and
nothing else.

**Process-local and reset on restart** — stated in the contract, and worth
repeating wherever someone reads about it: the way to make a level stick is
``LOG_LEVEL`` plus a restart. That is the feature, not a gap.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ragstack.api.security import Principal, resolve_principal
from ragstack.observability import log_control

router = APIRouter()


class LoggerLevel(BaseModel):
    """One logger this endpoint currently touches."""

    name: str
    #: The logger's OWN level. ``NOTSET`` means it inherits from root — not that
    #: it is silent.
    level: str
    #: ``override`` (set through this endpoint) or ``dampen`` (a member of the
    #: configured dampen set, at whatever the current root level implies).
    source: str


class LogLevelResponse(BaseModel):
    """The log level in effect in this process, and how it got there.

    The configured/effective split is the point. ``GET /v1/config`` echoes the
    **raw** ``LOG_LEVEL``, so a value the server rejected at start-up is reported
    there while INFO is what is actually in force — a gap W1's review flagged and
    this response closes: ``configured_level`` is that raw string,
    ``configured_level_resolved`` is what it resolves to, and ``effective_level``
    is the live root level.
    """

    pid: int
    configured_level: str
    configured_level_resolved: str
    effective_level: str
    runtime_override: bool
    changed_at: str
    changed_by: str
    dampening_active: bool
    dampen_loggers: list[str]
    loggers: list[LoggerLevel]
    logger_override_count: int
    max_logger_overrides: int
    #: The pending auto-revert. An operator must be able to see that the level
    #: will change under them — nothing else in this response would say so.
    auto_revert_pending: bool
    ttl_seconds: int | None
    #: Wall-clock, for a human; the countdown below is monotonic and authoritative.
    expires_at: str
    expires_in_seconds: int | None
    max_ttl_seconds: int


class LogLevelRequest(BaseModel):
    """``PUT`` body. At least one field; validation is atomic.

    Both fields are optional at the schema layer and the "one of them" rule is
    enforced in :mod:`~ragstack.observability.log_control`, alongside every other
    *semantic* refusal — so the rules about levels and logger names live in one
    place rather than being split between pydantic and the control module.

    **The 422 body is not one shape**, and an earlier version of this docstring
    claimed it was. A semantic refusal answers ``{"detail": "<sentence>"}``; a
    shape error pydantic catches first — a non-string ``level``, an unknown
    field — answers pydantic's list of error objects. Both are 422, and neither
    breaks the contract (the OpenAPI document defines no error body for this
    path), but do not write client code assuming ``detail`` is a string.

    ``level`` is deliberately not an ``Enum``: the server owns the vocabulary and
    answers a 4xx, and pinning an enum here would make that documented response
    unreachable from a conformant client. That reasoning is
    ``UserRoleRequest.role``'s — but note it answers **400** there and this
    answers 422. The status differs because the classification does:
    ``admin_users.py`` reserves 400 for a malformed request precisely to keep it
    distinct from the 409 its state change can raise. This endpoint has no such
    neighbour, so every refusal is a 422, as in ``groups.py``,
    ``collections.py`` and ``query.py``.
    """

    model_config = {"extra": "forbid"}

    level: str | None = Field(
        default=None,
        description=(
            "New root level. Case-insensitive; `warn` accepted; `NOTSET` rejected. "
            "Omit to leave the root level as it is."
        ),
    )
    loggers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Per-logger overrides, name->level. REPLACE semantics: what you send "
            "becomes the complete override set, `{}` clears them all, and omitting "
            "the field leaves them untouched."
        ),
    )
    #: Bounds are deliberately NOT `ge=`/`le=` here. Pydantic keeps the type
    #: check (a fractional or non-numeric value is a 422 before the handler
    #: runs), but the range belongs in `log_control` with every other semantic
    #: refusal: that is what keeps it atomic with the rest of the body and what
    #: makes the message a sentence naming the bound rather than a pydantic
    #: error object.
    #:
    #: `strict=True` because pydantic's LAX mode coerces `true` to `1` — caught
    #: in test: `{"ttl_seconds": true}` was accepted as a one-second TTL, which
    #: is not what anyone typing it means, and `"60"` was accepted as 60. The
    #: schema says `["integer", "null"]`; strict is what makes the field agree
    #: with the contract instead of quietly widening it.
    ttl_seconds: int | None = Field(
        default=None,
        strict=True,
        description=(
            "Auto-revert this change to the CONFIGURED defaults after N seconds "
            "(1..86400) — the same end state DELETE produces. Omit or send null "
            "for no expiry, which is the unchanged default. Every PUT supersedes "
            "the last one's expiry: a pending revert is cancelled first, and a new "
            "one armed only if this body carries `ttl_seconds`."
        ),
    )


@router.get("/log-level", response_model=LogLevelResponse)
async def get_log_level() -> dict[str, Any]:
    """The level this process is logging at right now, what configuration would
    return it to, and which loggers are overridden or damped. Read-only."""
    return log_control.describe()  # type: ignore[return-value]


@router.put("/log-level", response_model=LogLevelResponse)
async def set_log_level(
    body: LogLevelRequest,
    principal: Principal = Depends(resolve_principal),
) -> dict[str, Any]:
    """Change the level without restarting. Takes effect on the next log call.

    Refusals are 422 and change **nothing**: the whole body is validated before
    any logger is touched, so a caller who mistypes one logger name does not end
    up with the root level half-changed.

    ``principal.tenant`` — never a token, never an API key — goes on the WARNING
    audit line so that whoever finds DEBUG on in production can find out who
    turned it on and when.

    ``ttl_seconds`` arms an auto-revert to the configured defaults. Every PUT
    supersedes the previous one's expiry — cancelled before this change is
    applied, re-armed only if this body asked for one — so two TTLs can never
    overlap and an earlier timer can never fire later and undo this call.
    """
    try:
        return log_control.set_level(  # type: ignore[return-value]
            level=body.level,
            loggers=body.loggers,
            ttl_seconds=body.ttl_seconds,
            principal=principal.tenant,
        )
    except log_control.LogControlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/log-level", response_model=LogLevelResponse)
async def reset_log_level(
    principal: Principal = Depends(resolve_principal),
) -> dict[str, Any]:
    """Drop every runtime override and re-apply the configured defaults — the
    state a restart would produce, without the caller needing to know what
    ``LOG_LEVEL`` says. Idempotent, always 200, and audited like a change.

    Cancels any pending ``ttl_seconds`` auto-revert, because it produces that
    revert's end state right now. Audited as ``reset``, which is what tells it
    apart from the ``expired`` line an auto-revert writes."""
    return log_control.reset(principal=principal.tenant)  # type: ignore[return-value]
