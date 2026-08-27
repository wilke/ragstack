"""A dependency-free stand-in for the embedding sidecar, for self-booted runs.

``run_authz_keyed.sh`` pins every store and sidecar URL at ``127.0.0.1:1``
(#432) because on the dev host the real ports carry live services. That is the
right default — but ``/v1/query``, ``/v1/retrieve``, ``/v1/chunks?query=`` and
the context-window surface all need *an* embedder, and a dead one turns every
one of those conformance assertions into a 500. Skipping them instead would
recreate the exact vacuity #405 exists to remove.

So the runner boots this: ``POST /embed`` speaking the sidecar's wire contract
(:class:`ragstack.embedders.SidecarEmbedder`), backed by a SHA-256 of the text
rather than a model. It is emphatically **not** a model: it says nothing about
retrieval quality, only that the plumbing carries a vector end to end. Retrieval
quality is an L-layer claim (use-case matrix F5), never a conformance one.

Determinism (same text → same vector) is defensive rather than load-bearing
today: the self-booted collections are empty, so no conformance assertion
currently compares two rankings over real content. It costs nothing and it is
what a ranking-stability assertion would need the day one exists — making it
random was tried, and nothing went red, which is the honest statement of how far
this stub is exercised.

Binds 127.0.0.1 only, on a port the caller chooses (the runner picks an
ephemeral high one). Stdlib only — the conformance suite must not grow a
dependency on the implementation it tests.

Usage: ``python stub_embedding_sidecar.py <port> <dim>``
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _vector(text: str, dim: int) -> list[float]:
    """A deterministic unit-ish vector for *text*.

    SHA-256 is extended by counter until it covers ``dim`` floats, each mapped
    into [-1, 1). Same text → same vector, different text → different vector
    with overwhelming probability; that is the entire contract this stub owes
    the API.
    """
    raw = b""
    counter = 0
    seed = text.encode("utf-8")
    while len(raw) < dim * 4:
        raw += hashlib.sha256(seed + struct.pack("<I", counter)).digest()
        counter += 1
    ints = struct.unpack_from(f"<{dim}I", raw, 0)
    return [(i / 2**31) - 1.0 for i in ints]


class _Handler(BaseHTTPRequestHandler):
    dim = 8

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
        if self.path.rstrip("/") in ("/health", "/healthz", ""):
            self._send(200, {"status": "ok", "stub": True, "dim": self.dim})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
        if self.path.rstrip("/") != "/embed":
            self._send(404, {"detail": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            texts = json.loads(self.rfile.read(length) or b"{}").get("texts") or []
        except json.JSONDecodeError:
            self._send(400, {"detail": "body is not JSON"})
            return
        self._send(200, {"embeddings": [_vector(str(t), self.dim) for t in texts]})

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence per-request logging — the runner's log is for the API."""


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 0
    _Handler.dim = int(argv[2]) if len(argv) > 2 else 8
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"stub-embedding: listening on 127.0.0.1:{server.server_port} dim={_Handler.dim}",
          flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
