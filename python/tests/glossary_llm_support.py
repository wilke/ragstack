"""A fake OpenAI-compatible model server for the glossary step's tests (#847 G2).

An ``httpx.MockTransport`` handler that speaks the contract the real servers
(vLLM, OpenAI) speak, at the HTTP boundary — so the tests exercise the real
``OpenAILLM`` client, not a collaborator's signature:

* ``GET /v1/models`` → ``{"object": "list", "data": [{"id", "object": "model",
  "owned_by", "root", "max_model_len"}]}``;
* ``POST /v1/chat/completions`` → ``{"id", "object": "chat.completion",
  "model", "choices": [{"index": 0, "message": {"role": "assistant",
  "content", "reasoning_content"}, "finish_reason": "stop"|"length"}],
  "usage": {"prompt_tokens", "completion_tokens", "total_tokens"}}``.
  A thinking model left thinking answers with ``content: null`` and the text
  in ``reasoning_content``; a cut-off answer has ``finish_reason: "length"``
  and a body that may end mid-JSON.

Outcomes are scripted per request: :class:`Answer`, :class:`Status` (429,
500, 503, 401, ...), :class:`Fail` (raise ``httpx.ConnectError`` /
``httpx.ReadTimeout`` — what the transport raises for a refused connection or
a server slower than the timeout). :func:`serve` puts the same fake behind a
real socket on 127.0.0.1 for a child process (the cwltool end-to-end run).
Every request is recorded with its JSON
body, its per-request timeout (``request.extensions["timeout"]``) and whether
it carried an ``Authorization`` header.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

ENDPOINT = "http://fake-llm.invalid:8000"
MODEL = "fake/glossary-model"


@dataclass
class Answer:
    entries: list[dict[str, str]] | str = field(default_factory=list)
    finish: str = "stop"
    content_null: bool = False
    reasoning: str | None = None

    def body(self) -> str:
        return self.entries if isinstance(self.entries, str) else json.dumps(self.entries)


@dataclass
class Status:
    code: int


@dataclass
class Fail:
    exc: type[Exception]


Outcome = Answer | Status | Fail


@dataclass
class Seen:
    method: str
    path: str
    body: dict[str, Any]
    timeout: dict[str, Any]
    authorized: bool

    @property
    def prompt(self) -> str:
        msgs = self.body.get("messages") or []
        return str(msgs[-1]["content"]) if msgs else ""


class FakeOpenAI:
    """``rules``: ``[(predicate(prompt) -> bool, [outcome, ...])]`` — the first
    rule whose predicate matches the request's user prompt answers it, its
    outcomes consumed in order, the last one repeating. ``default`` answers
    everything else. ``models``: the probe's outcome (an :class:`Answer` is
    ignored there — the listing names ``served``)."""

    def __init__(
        self,
        rules: list[tuple[Callable[[str], bool], list[Outcome]]] | None = None,
        *,
        default: Outcome | None = None,
        models: Outcome | None = None,
        served: tuple[str, ...] = (MODEL,),
    ) -> None:
        self.rules = [(pred, list(outs)) for pred, outs in (rules or [])]
        self.default = default or Answer([])
        self.models = models
        self.served = served
        self.seen: list[Seen] = []

    @property
    def chats(self) -> list[Seen]:
        return [s for s in self.seen if s.path.endswith("/chat/completions")]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self, timeout: float = 5.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport(), timeout=timeout, trust_env=False)

    def _pick(self, prompt: str) -> Outcome:
        for pred, outs in self.rules:
            if pred(prompt):
                return outs.pop(0) if len(outs) > 1 else outs[0]
        return self.default

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen = Seen(request.method, request.url.path, body,
                    dict(request.extensions.get("timeout") or {}),
                    "authorization" in request.headers)
        self.seen.append(seen)
        if request.url.path.endswith("/models"):
            out = self.models
            if isinstance(out, Status):
                return httpx.Response(out.code, json={"error": {"message": "nope"}})
            if isinstance(out, Fail):
                raise out.exc("fake transport failure", request=request)
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "vllm", "root": m,
                 "max_model_len": 131072} for m in self.served]})
        out = self._pick(seen.prompt)
        if isinstance(out, Status):
            return httpx.Response(out.code, json={"error": {"message": f"status {out.code}"}})
        if isinstance(out, Fail):
            raise out.exc("fake transport failure", request=request)
        text = out.body()
        message: dict[str, Any] = {"role": "assistant",
                                   "content": None if out.content_null else text}
        if out.reasoning is not None:
            message["reasoning_content"] = out.reasoning
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(self.seen)}", "object": "chat.completion",
            "model": body.get("model", ""),
            "choices": [{"index": 0, "message": message, "finish_reason": out.finish}],
            "usage": {"prompt_tokens": len(seen.prompt) // 4,
                      "completion_tokens": len(text) // 4,
                      "total_tokens": len(seen.prompt) // 4 + len(text) // 4},
        })


#: How long a ``Fail(ReadTimeout)`` holds the socket open under :func:`serve`:
#: longer than any per-call timeout the tests give the client.
SERVE_TIMEOUT_HOLD_S = 8.0


@contextmanager
def serve(fake: FakeOpenAI) -> Iterator[str]:
    """``fake`` on a real socket: a ``ThreadingHTTPServer`` on 127.0.0.1 and an
    ephemeral port, yielding its base URL. Each HTTP request becomes an
    ``httpx.Request`` handed to :meth:`FakeOpenAI.handle` (so ``seen`` records
    it as in the in-process tests); ``Fail(ConnectError)`` closes the socket
    without answering, ``Fail(ReadTimeout)`` holds it past the client's
    timeout. Shut down on exit."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            req = httpx.Request(self.command, f"http://fake{self.path}",
                                headers=dict(self.headers.items()), content=body)
            try:
                with lock:   # FakeOpenAI's scripted outcomes are not thread-safe
                    resp = asyncio.run(fake.handle(req))
            except httpx.ReadTimeout:
                time.sleep(SERVE_TIMEOUT_HOLD_S)
                self.close_connection = True
                return
            except httpx.TransportError:
                self.close_connection = True
                return
            data = resp.content
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _serve
        do_POST = _serve

        def log_message(self, *a: Any) -> None:  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
