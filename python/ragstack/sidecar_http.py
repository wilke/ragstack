"""Shared HTTP plumbing for sidecar clients.

Both :class:`~ragstack.embedders.SidecarEmbedder` and
:class:`~ragstack.scoring.scorers.SidecarReranker` talk to a model sidecar over
the same minimal contract: normalise the base URL, hold an
:class:`httpx.AsyncClient`, ``POST`` a JSON body to a path under that base, and
``raise_for_status()``. This module factors that out so the two classes share
one place to evolve the timeout, error handling, and request shape.
"""
from __future__ import annotations

from typing import Any

import httpx

#: Default per-request timeout (seconds) for sidecar calls. Sidecars do model
#: inference, so the budget is intentionally generous compared to httpx's 5s.
DEFAULT_TIMEOUT = 120.0


class SidecarClient:
    """Minimal JSON-over-HTTP client for a model sidecar.

    Holds the normalised ``base_url`` and the shared :class:`httpx.AsyncClient`,
    and exposes :meth:`post_json` for ``POST <base>/<path>`` calls that return
    decoded JSON after ``raise_for_status()``.
    """

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.timeout = timeout

    async def post_json(
        self,
        path: str,
        json: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """``POST <base_url>/<path>`` with ``json``; return the decoded JSON body.

        Raises :class:`httpx.HTTPStatusError` on a non-2xx response.
        """
        r = await self.http.post(
            f"{self.base_url}/{path.lstrip('/')}",
            json=json,
            headers=headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()
