"""Shared HTTP plumbing for identity providers.

One job: every network failure on the identity path becomes
:class:`IdentityUnavailable` (→ 503), never :class:`IdentityInvalid` (→ 401) and
never a silent allow. A key server that is down says nothing about the caller.
"""
from __future__ import annotations

from typing import Any

import httpx

from ragstack.identity.base import IdentityUnavailable


async def fetch_json(client: httpx.AsyncClient, url: str, *, what: str) -> Any:
    """GET ``url`` and parse JSON, mapping every failure to IdentityUnavailable."""
    resp = await fetch(client, url, what=what)
    try:
        return resp.json()
    except ValueError as exc:  # malformed body from the key server is our problem
        raise IdentityUnavailable(f"{what}: {url} returned non-JSON") from exc


async def fetch(client: httpx.AsyncClient, url: str, *, what: str) -> httpx.Response:
    """GET ``url``, raising IdentityUnavailable on transport or non-2xx status."""
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise IdentityUnavailable(f"{what}: {url} unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise IdentityUnavailable(f"{what}: {url} returned HTTP {resp.status_code}")
    return resp
