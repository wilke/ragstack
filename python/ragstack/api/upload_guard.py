"""Content-Length guard in front of the multipart parser — ``POST /v1/ingest/upload`` (#202).

Why a middleware and not a dependency: FastAPI parses a multipart body
(``request.form()``) BEFORE any route dependency runs, and Starlette's parser
drains the whole request stream into one ``SpooledTemporaryFile`` per part
(rolling to disk past 1 MiB). So every gate inside the handler — the
allowlist, the size caps, the in-flight and hourly 429s — decides only after
the full body has been received and spooled. That protects the Workspace, the
Python heap and INGEST_ROOT; it does not protect ingress or the spool
directory. This middleware is the one check that runs before ``receive()`` is
ever called: an upload whose ``Content-Length`` exceeds
``max_upload_bytes_per_request`` plus a per-file multipart-framing allowance
is refused with 413 and ``Connection: close`` without reading a byte, and one
with no ``Content-Length`` at all (chunked transfer) is refused with 411 —
the parser has no way to bound what it has not been told the size of.

What it cannot do: a client that LIES about ``Content-Length`` (declares a
small body, sends a large one — or declares 10 GB, sends 64 KB and idles) is
only stopped by the deployment gateway's body cap and read timeout. Deploy
this API behind a gateway that enforces a body cap of about
``MAX_UPLOAD_BYTES_PER_REQUEST`` — see docs/DEPLOYMENT.md.

Pure ASGI (same shape as ``api/root_path.py``): matching is on the ROUTE path
(``get_route_path``, so a mounted-under-a-prefix deployment matches too) and
on the method + ``multipart/form-data`` content type; everything else passes
straight through. ``max_upload_bytes_per_request <= 0`` disables the guard.
"""
from __future__ import annotations

import json

from starlette.datastructures import Headers
from starlette.routing import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from ragstack.config import settings

UPLOAD_ROUTE = "/v1/ingest/upload"
# Per-file allowance for the multipart framing (boundary lines, the
# Content-Disposition / Content-Type part headers) on top of the payload cap.
FRAMING_PER_FILE = 1024


def content_length_limit() -> int:
    """The largest ``Content-Length`` an upload request may declare; 0 = no guard."""
    cap = settings.max_upload_bytes_per_request
    if cap <= 0:
        return 0
    return cap + max(settings.max_upload_files, 0) * FRAMING_PER_FILE


def _is_multipart_upload(scope: Scope, headers: Headers) -> bool:
    if scope.get("method") != "POST" or get_route_path(scope) != UPLOAD_ROUTE:
        return False
    ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return ctype == "multipart/form-data"


class UploadContentLengthMiddleware:
    """413 / 411 an upload from its request headers alone — before the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if _is_multipart_upload(scope, headers):
                limit = content_length_limit()
                if limit > 0:
                    raw = (headers.get("content-length") or "").strip()
                    if not raw.isdigit():
                        await _refuse(
                            send, 411,
                            "Content-Length is required for uploads; chunked transfer "
                            "encoding is not accepted",
                        )
                        return
                    if int(raw) > limit:
                        await _refuse(
                            send, 413,
                            f"request body of {raw} bytes exceeds the upload limit of {limit} "
                            f"bytes (max_upload_bytes_per_request plus multipart framing)",
                        )
                        return
        await self.app(scope, receive, send)


async def _refuse(send: Send, status: int, detail: str) -> None:
    """A complete JSON error response, ``Connection: close`` so the server does
    not try to drain the unread body to reuse the connection."""
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})
