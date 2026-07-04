"""Async client for the GoWe workflow engine's REST API (ADR-0001 step 2b).

GoWe (a CWL v1.2 engine) runs the offline-plane workflows: register a CWL, submit
it with inputs, poll to completion, download the output files. This wraps that
flow so ``GoWeBackend`` (and operators) can drive it from Python.

Auth: BV-BRC token (anonymous submission is disabled on the deployed server). The
token is a ``un=…|tokenid=…|expiry=…|sig=…`` string sent verbatim in the
``Authorization`` header; it is loaded from ``$GOWE_TOKEN``/``$BVBRC_TOKEN`` or the
usual token files (``~/.gowe/credentials.json``, ``~/.patric_token``, …).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("GOWE_URL", "http://localhost:8091")
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}

# Token lookup order: explicit env first, then the files GoWe's own CLI reads.
_TOKEN_ENV = ("GOWE_TOKEN", "BVBRC_TOKEN")
_TOKEN_FILES = ("~/.gowe/credentials.json", "~/.bvbrc_token", "~/.patric_token",
                "~/.p3_token")


class GoWeError(RuntimeError):
    """A GoWe API call failed (non-2xx, or a submission ended non-COMPLETED)."""


def load_bvbrc_token() -> str | None:
    """Find a BV-BRC token from the environment or the standard token files.

    ``~/.gowe/credentials.json`` is JSON (``{"token": "un=…"}``); the others are the
    bare token string. Returns None if none is found (callers surface a clear error
    rather than sending an unauthenticated request that would 401)."""
    for env in _TOKEN_ENV:
        if v := os.environ.get(env):
            return v.strip()
    for f in _TOKEN_FILES:
        p = Path(f).expanduser()
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8").strip()
        if p.suffix == ".json" or text.startswith("{"):
            try:
                tok = json.loads(text).get("token")
            except json.JSONDecodeError:
                continue
            if tok:
                return str(tok).strip()
        elif "un=" in text:
            return text
    return None


class GoWeClient:
    """Minimal async REST client for GoWe (register → submit → poll → download)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        token: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token if token is not None else load_bvbrc_token()
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        # GoWe accepts the raw token (it strips an optional "Bearer " prefix).
        return {"Authorization": self._token} if self._token else {}

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        resp = await self._http.request(
            method, f"{self._base}{path}", headers=self._headers(), **kw
        )
        if resp.status_code >= 400:
            raise GoWeError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp

    async def _json(self, method: str, path: str, **kw) -> Any:
        return (await self._request(method, path, **kw)).json()

    # --- workflow / submission lifecycle ---------------------------------- #
    async def register_workflow(
        self, name: str, cwl: str, labels: dict[str, str] | None = None
    ) -> str:
        """Register (or dedup-match) a CWL document; returns its ``wf_…`` id."""
        body = {"name": name, "cwl": cwl, "labels": labels or {}}
        data = (await self._json("POST", "/api/v1/workflows", json=body))["data"]
        return data["id"]

    async def submit(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        *,
        labels: dict[str, str] | None = None,
        output_destination: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Submit a registered workflow with an inputs (job) object. Returns the
        submission record (``id``, ``state``, …); ``dry_run`` validates only."""
        body: dict[str, Any] = {"workflow_id": workflow_id, "inputs": inputs}
        if labels:
            body["labels"] = labels
        if output_destination:
            body["output_destination"] = output_destination
        path = "/api/v1/submissions" + ("?dry_run=true" if dry_run else "")
        return (await self._json("POST", path, json=body))["data"]

    async def get_submission(self, sub_id: str) -> dict[str, Any]:
        return (await self._json("GET", f"/api/v1/submissions/{sub_id}"))["data"]

    async def wait(
        self, sub_id: str, *, poll_interval: float = 3.0, timeout: float = 3600.0
    ) -> dict[str, Any]:
        """Poll a submission until it reaches a terminal state. Returns the final
        record. Raises ``GoWeError`` on timeout (the submission keeps running)."""
        deadline = time.monotonic() + timeout
        while True:
            sub = await self.get_submission(sub_id)
            if sub.get("state") in TERMINAL_STATES:
                return sub
            if time.monotonic() >= deadline:
                raise GoWeError(
                    f"submission {sub_id} not terminal after {timeout}s "
                    f"(state={sub.get('state')})"
                )
            await asyncio.sleep(poll_interval)

    async def download(self, location: str) -> bytes:
        """Download an output file by its ``file://…`` location (must be under the
        server's allowed download dirs). Returns the response bytes verbatim — an
        empty body yields ``b""`` (we never read a server-side path off the local
        filesystem, which would be a footgun on a remote/containerized worker)."""
        resp = await self._request(
            "GET", "/api/v1/files/download", params={"location": location}
        )
        return resp.content

    async def upload(self, path: str | Path) -> str:
        """Upload a local file; returns its server ``location`` for use in inputs."""
        p = Path(path)
        files = {"file": (p.name, p.read_bytes())}
        data = (await self._json("POST", "/api/v1/files", files=files))["data"]
        return data["location"]
