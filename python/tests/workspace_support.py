"""The shared BV-BRC Workspace + Shock test double (``httpx.MockTransport``).

It models the real service closely enough to pin the request shapes it expects
— envelope ``version: "1.1"``, ``params: [<dict>]``, the
``[path, type, user_metadata, data]`` object tuple, the 12-element ObjectMeta
reply, an existing folder silently skipped by ``create``, ``Object not found!``
as an RPC error, and a Shock ``PUT`` of one ``upload`` multipart field with
``Authorization: OAuth <token>`` — and, critically, the two **usermeta
constraints observed live** in #408/#414:

* ``Workspace.create`` takes a user_metadata dict and stores **nothing** for a
  dotted field name (Mongo cannot hold a dot in a field name, and create does
  not complain); ``create_stores_metadata = False`` models the pessimistic
  reading of #408, where create persists no usermeta at all.
* ``Workspace.update_metadata`` **rejects** the whole call when any field name
  contains a dot: ``The dotted field 'x.y' in 'metadata.x.y' is not valid for
  storage.``

A fake that accepts dotted keys is a fake encoding the wrong contract — that is
exactly how #414 (every second upload into a collection a hard 500) shipped. No
live service is ever contacted.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=SECRETSIG"
OTHER_TOKEN = "un=bob@patricbrc.org|tokenid=t-2|expiry=9999999999|sig=OTHERSIG"
SUBJECT = "alice@patricbrc.org"
WS_URL = "http://workspace.test/services/Workspace"
SHOCK = "http://shock.test/services/shock_api"


class FakeWorkspace:
    """In-memory Workspace + Shock behind one MockTransport handler."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.objects: dict[str, dict[str, Any]] = {}  # path -> {type, metadata, size, shock}
        self.requests: list[httpx.Request] = []
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []
        self.metadata_writes: list[str] = []  # paths whose user metadata was written
        self.shock_uploads: list[tuple[str, int]] = []  # (node id, byte count)
        self.next_node = 0
        self.rpc_status: int | None = None  # force an HTTP status on every RPC
        self.contents: dict[str, bytes] = {}  # path -> bytes served by the download URL
        self.download_status: int | None = None  # force a status on every download
        #: #408 is ambiguous about how much of the create tuple's usermeta the
        #: live service dropped — all that was observed is an empty hash where
        #: four DOTTED keys were sent. True keeps the non-dotted keys (the
        #: optimistic reading); False stores no usermeta from ``create`` at all
        #: (the pessimistic one). Both must converge on a stamped folder.
        self.create_stores_metadata = True

    # -- transport entry point ------------------------------------------------
    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host == "shock.test":
            return self._shock(req)
        if req.url.host == "download.test":
            return self._download(req)
        return self._rpc(req)

    # -- Workspace download endpoint (what get_download_url points at) -------
    def _download(self, req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        if self.download_status is not None:
            return httpx.Response(self.download_status, text="nope")
        if req.headers.get("Authorization") != self.token:
            return httpx.Response(401, text="Authentication failed")
        path = req.url.path.removeprefix("/dl")
        if path not in self.contents:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=self.contents[path])

    def rpc_get_download_url(self, params: dict[str, Any]) -> list[Any]:
        urls = []
        for path in params["objects"]:
            if path not in self.objects and path not in self.contents:
                raise LookupError(f"Object not found! {path}")
            urls.append(f"http://download.test/dl{path}")
        return [urls]

    # -- Workspace JSON-RPC ---------------------------------------------------
    def _rpc(self, req: httpx.Request) -> httpx.Response:
        if self.rpc_status is not None:
            return httpx.Response(self.rpc_status, text="Authentication failed")
        assert req.method == "POST" and str(req.url) == WS_URL
        assert req.headers["Content-Type"] == "application/json"
        body = json.loads(req.content)
        assert body["version"] == "1.1" and body["id"]
        assert isinstance(body["params"], list) and len(body["params"]) == 1
        method, params = body["method"], body["params"][0]
        self.rpc_calls.append((method, params))
        if req.headers.get("Authorization") != self.token:
            return self._error("Token validation failed: bad signature")
        handler = getattr(self, "rpc_" + method.removeprefix("Workspace."), None)
        assert handler is not None, f"unexpected RPC {method}"
        try:
            return httpx.Response(200, json={"id": body["id"], "version": "1.1",
                                             "result": handler(params)})
        except LookupError as exc:
            return self._error(str(exc))

    @staticmethod
    def _error(message: str) -> httpx.Response:
        return httpx.Response(500, json={"version": "1.1", "error": {
            "name": "JSONRPCError", "code": -32603, "message": message}})

    def _meta(self, path: str) -> list[Any]:
        o = self.objects[path]
        parent, name = path.rsplit("/", 1)
        return [name, o["type"], parent + "/", "2026-08-24T00:00:00Z", "uuid-" + name,
                SUBJECT, o["size"], o["metadata"], {"is_folder": int(o["type"] == "folder")},
                "o", "n", o["shock"]]

    def _mkdir_p(self, path: str) -> None:
        parts = path.strip("/").split("/")
        for i in range(2, len(parts) + 1):  # /<user>/home always exists
            p = "/" + "/".join(parts[:i])
            self.objects.setdefault(p, {"type": "folder", "metadata": {}, "size": 0, "shock": ""})

    def _stored_usermeta(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """What ``create`` actually keeps of the tuple's user_metadata.

        A dotted field name is accepted by the call and stored for nobody —
        #408, observed live as an empty hash on a folder created with the four
        ``ragstack.*`` keys. There is no error, which is what made the gap
        invisible until the backfill path hit ``update_metadata`` (#414).
        """
        if not self.create_stores_metadata:
            return {}
        return {k: v for k, v in metadata.items() if "." not in k}

    def rpc_create(self, params: dict[str, Any]) -> list[Any]:
        out = []
        for obj in params["objects"]:
            path, typ, metadata, data = obj
            assert isinstance(metadata, dict) and isinstance(path, str) and path.startswith("/")
            if typ == "folder":
                if path in self.objects:
                    continue  # the real service ignores existing folders (and omits them)
                self._mkdir_p(path.rsplit("/", 1)[0])
                stored = self._stored_usermeta(metadata)
                self.objects[path] = {"type": "folder", "metadata": stored,
                                      "size": 0, "shock": ""}
                if stored:
                    self.metadata_writes.append(path)
            else:
                if path in self.objects and not params.get("overwrite"):
                    raise LookupError(f"Overwriting object {path} and overwrite flag is not set!")
                self._mkdir_p(path.rsplit("/", 1)[0])
                shock = ""
                if params.get("createUploadNodes"):
                    assert data is None
                    self.next_node += 1
                    shock = f"{SHOCK}/node/node-{self.next_node}"
                self.objects[path] = {"type": typ, "metadata": self._stored_usermeta(metadata),
                                      "size": len(data or ""), "shock": shock}
            out.append(self._meta(path))
        return [out]

    def rpc_get(self, params: dict[str, Any]) -> list[Any]:
        out = []
        for path in params["objects"]:
            if path not in self.objects:
                raise LookupError("Object not found!")
            out.append([self._meta(path)] if params.get("metadata_only") else
                       [self._meta(path), ""])
        return [out]

    def rpc_ls(self, params: dict[str, Any]) -> list[Any]:
        listing: dict[str, list[Any]] = {}
        for d in params["paths"]:
            prefix = d.rstrip("/") + "/"
            for p in self.objects:
                if p.startswith(prefix) and "/" not in p[len(prefix):]:
                    listing.setdefault(d, []).append(self._meta(p))
        return [listing]

    def rpc_update_metadata(self, params: dict[str, Any]) -> list[Any]:
        assert params.get("append") == 1, "must append — a bare update replaces the whole hash"
        # The storage layer refuses a dotted field name outright, before writing
        # anything (#414, observed live). Unlike ``create``, this one is loud.
        for _path, metadata in params["objects"]:
            for key in metadata:
                if "." in key:
                    raise LookupError(
                        f"The dotted field '{key}' in 'metadata.{key}' is not valid for storage."
                    )
        out = []
        for path, metadata in params["objects"]:
            if path not in self.objects:
                raise LookupError("Object not found!")
            self.objects[path]["metadata"].update(metadata)
            self.metadata_writes.append(path)
            out.append(self._meta(path))
        return [out]

    def rpc_delete(self, params: dict[str, Any]) -> list[Any]:
        out = []
        for path in params["objects"]:
            if path not in self.objects:
                raise LookupError("Object not found!")
            out.append(self._meta(path))
            del self.objects[path]
        return [out]

    # -- Shock ---------------------------------------------------------------
    def _shock(self, req: httpx.Request) -> httpx.Response:
        assert req.method == "PUT"
        if req.headers.get("Authorization") != f"OAuth {self.token}":
            return httpx.Response(401, json={"status": 401, "error": ["Unauthorized"], "data": None})
        ctype = req.headers["Content-Type"]
        m = re.match(r"multipart/form-data; boundary=(\S+)", ctype)
        assert m, ctype
        boundary = m.group(1).encode()
        body = req.content
        head, _, rest = body.partition(b"\r\n\r\n")
        assert head.startswith(b"--" + boundary + b"\r\n")
        assert b'name="upload"; filename="' in head
        payload = rest[: rest.rfind(b"\r\n--" + boundary + b"--")]
        node = req.url.path.rsplit("/", 1)[-1]
        self.shock_uploads.append((node, len(payload)))
        for o in self.objects.values():
            if o["shock"].endswith("/" + node):
                o["size"] = len(payload)
        return httpx.Response(200, json={"status": 200, "data": {"id": node}, "error": None})

