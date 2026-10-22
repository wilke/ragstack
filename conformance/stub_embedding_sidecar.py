"""A dependency-free stand-in for the embedding AND crossencoder sidecars, for
self-booted runs.

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

Since #751 PR-3 it also speaks the crossencoder sidecar's contract — ``POST
/rerank`` returning ``{scores, indices}`` in the ORIGINAL order (score = 1/(i+1),
so a rerank is a visible no-op), ``GET /ready``/``/live`` as #760 defines them —
so the keyed boot can run with ``RERANK_ENABLED=true`` and the ``degraded``
surface (#748) is reachable from a black-box test: ``POST /__conformance/rerank
{"mode": "fail"}`` makes ``/rerank`` answer 500 and ``/ready`` answer 503
``{"status": "fail"}`` until ``{"mode": "ok"}`` restores it. That control route
is test furniture; the real sidecars have nothing like it.

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

MODEL_NAME = "conformance-stub"


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
    #: "ok" | "fail" — flipped by POST /__conformance/rerank. Class-level so
    #: every handler thread sees the same mode.
    rerank_mode = "ok"

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"detail": "body is not JSON"})
            return None
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
        path = self.path.rstrip("/")
        if path == "/live":
            self._send(200, {"status": "ok"})
        elif path in ("/health", "/healthz", "/ready", ""):
            # #760 semantics: 200 ok / 503 {"status": "fail", …}. /health is an
            # alias of /ready on the real sidecars, so it is one here too.
            if _Handler.rerank_mode == "fail":
                self._send(503, {"status": "fail", "model": MODEL_NAME, "loaded": True,
                                 "error": "RuntimeError",
                                 "detail": "conformance stub forced to fail"})
            else:
                self._send(200, {"status": "ok", "model": MODEL_NAME, "loaded": True,
                                 "stub": True, "dim": self.dim})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
        path = self.path.rstrip("/")
        if path == "/embed":
            body = self._body()
            if body is None:
                return
            texts = body.get("texts") or []
            self._send(200, {"embeddings": [_vector(str(t), self.dim) for t in texts]})
        elif path == "/rerank":
            body = self._body()
            if body is None:
                return
            if _Handler.rerank_mode == "fail":
                self._send(500, {"detail": "conformance stub forced to fail"})
                return
            docs = body.get("documents") or []
            top_k = body.get("top_k")
            n = len(docs) if top_k is None else min(int(top_k), len(docs))
            # Original order, descending scores: a rerank that changes nothing,
            # which is exactly as much as a stub may claim.
            self._send(200, {"scores": [1.0 / (i + 1) for i in range(n)],
                             "indices": list(range(n))})
        elif path == "/__conformance/rerank":
            body = self._body()
            if body is None:
                return
            mode = body.get("mode")
            if mode not in ("ok", "fail"):
                self._send(400, {"detail": "mode must be ok or fail"})
                return
            _Handler.rerank_mode = mode
            self._send(200, {"mode": mode})
        else:
            self._send(404, {"detail": "not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence per-request logging — the runner's log is for the API."""


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 0
    _Handler.dim = int(argv[2]) if len(argv) > 2 else 8
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"stub-sidecar: listening on 127.0.0.1:{server.server_port} dim={_Handler.dim} "
          f"(embed + rerank)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
