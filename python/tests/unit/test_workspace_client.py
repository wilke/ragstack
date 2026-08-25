"""Unit tests for ``ragstack.workspace`` against an ``httpx.MockTransport`` fake.

The fake models the BV-BRC Workspace JSON-RPC service and Shock closely enough
to pin the request shapes the real service expects (envelope ``version: "1.1"``,
``params: [<dict>]``, the ``[path, type, metadata, data]`` object tuple, the
12-element ObjectMeta reply, an existing folder being silently skipped by
``create``, ``Object not found!`` as an RPC error, and a Shock ``PUT`` of one
``upload`` multipart field with ``Authorization: OAuth <token>``). No live
service is ever contacted.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import pytest

from ragstack.workspace import (
    ARCHIVE_FORMAT,
    WorkspaceAuthError,
    WorkspaceClient,
    WorkspaceError,
    WorkspaceExists,
    WorkspaceNotFound,
    WorkspaceTooLarge,
    _multipart_frame,
)

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

    def rpc_create(self, params: dict[str, Any]) -> list[Any]:
        out = []
        for obj in params["objects"]:
            path, typ, metadata, data = obj
            assert isinstance(metadata, dict) and isinstance(path, str) and path.startswith("/")
            if typ == "folder":
                if path in self.objects:
                    continue  # the real service ignores existing folders (and omits them)
                self._mkdir_p(path.rsplit("/", 1)[0])
                self.objects[path] = {"type": "folder", "metadata": dict(metadata),
                                      "size": 0, "shock": ""}
                if metadata:
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
                self.objects[path] = {"type": typ, "metadata": dict(metadata),
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


@pytest.fixture
def fake() -> FakeWorkspace:
    return FakeWorkspace()


@pytest.fixture
async def client(fake: FakeWorkspace):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        yield WorkspaceClient(WS_URL, http, timeout=5.0)


async def _chunks(data: bytes, size: int = 7):
    for i in range(0, len(data), size):
        yield data[i:i + size]


BASE = f"/{SUBJECT}/home/.ragstack/collections/col-1"


# ---------------------------------------------------------------------------
# ensure_collection_folder
# ---------------------------------------------------------------------------


async def test_ensure_collection_folder_creates_layout_and_metadata(client, fake):
    uri = await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert uri == "ws://" + BASE
    assert set(fake.objects) >= {BASE, BASE + "/sources", BASE + "/versions"}
    assert fake.objects[BASE]["metadata"] == {
        "ragstack.format": ARCHIVE_FORMAT, "ragstack.collection_id": "col-1",
        "ragstack.tenant": "t1", "ragstack.spec_hash": "h1",
    }
    assert fake.objects[BASE + "/sources"]["metadata"] == {}
    # The create carried the metadata in the tuple's third slot (the confirmed shape).
    create = next(p for m, p in fake.rpc_calls if m == "Workspace.create")
    assert create["objects"][0][:2] == [BASE, "folder"]
    assert create["objects"][0][2]["ragstack.format"] == ARCHIVE_FORMAT
    assert create["objects"][0][3] is None


async def test_ensure_collection_folder_is_idempotent_and_writes_metadata_once(client, fake):
    for _ in range(3):
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.metadata_writes == [BASE]
    assert not any(m == "Workspace.update_metadata" for m, _ in fake.rpc_calls)
    # /u/home, .ragstack, collections (auto-created parents) + col-1, sources, versions
    assert len(fake.objects) == 6
    assert fake.objects[BASE]["metadata"]["ragstack.spec_hash"] == "h1"


async def test_ensure_collection_folder_backfills_missing_metadata_once(client, fake):
    fake._mkdir_p(BASE + "/sources")  # pre-existing, user-made, no ragstack.* keys
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.metadata_writes == [BASE]
    upd = [p for m, p in fake.rpc_calls if m == "Workspace.update_metadata"]
    assert len(upd) == 1 and upd[0]["append"] == 1 and upd[0]["objects"][0][0] == BASE
    assert BASE + "/versions" in fake.objects


async def test_ensure_collection_folder_refuses_foreign_metadata(client, fake):
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    with pytest.raises(WorkspaceError, match="different collection build"):
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h2", tenant="t1")
    assert fake.metadata_writes == [BASE]


@pytest.mark.parametrize("subject,cid", [("a/b", "c"), ("", "c"), (SUBJECT, ".."), (SUBJECT, "x/y")])
async def test_ensure_collection_folder_rejects_unsafe_segments(client, fake, subject, cid):
    with pytest.raises(WorkspaceError, match="invalid"):
        await client.ensure_collection_folder(TOKEN, subject, cid, spec_hash="h", tenant="t")
    assert fake.requests == []


# ---------------------------------------------------------------------------
# upload_source
# ---------------------------------------------------------------------------


async def test_upload_source_streams_to_shock_and_returns_ws_path(client, fake):
    data = b"%PDF-1.4 " + b"x" * 1000
    uri = await client.upload_source(TOKEN, "ws://" + BASE + "/sources", "paper.pdf",
                                     _chunks(data), max_bytes=len(data))
    assert uri == f"ws://{BASE}/sources/paper.pdf"
    create = [p for m, p in fake.rpc_calls if m == "Workspace.create"]
    assert create == [{"objects": [[BASE + "/sources/paper.pdf", "pdf", {}, None]],
                       "createUploadNodes": 1}]
    assert fake.shock_uploads == [("node-1", len(data))]
    assert fake.objects[BASE + "/sources/paper.pdf"]["size"] == len(data)
    shock_req = next(r for r in fake.requests if r.url.host == "shock.test")
    assert shock_req.headers["Authorization"] == f"OAuth {TOKEN}"
    assert "content-length" not in shock_req.headers  # streamed, not buffered


async def test_upload_source_accepts_sync_and_async_readers(client, fake):
    import io

    data = b"y" * 3000
    await client.upload_source(TOKEN, BASE + "/sources", "a.txt", io.BytesIO(data), max_bytes=3000)

    class AsyncReader:
        def __init__(self) -> None:
            self.buf = io.BytesIO(data)

        async def read(self, n: int) -> bytes:
            return self.buf.read(n)

    await client.upload_source(TOKEN, BASE + "/sources", "b.txt", AsyncReader(), max_bytes=3000)
    assert [n for _, n in fake.shock_uploads] == [3000, 3000]
    assert fake.objects[BASE + "/sources/a.txt"]["type"] == "txt"


async def test_upload_source_refuses_at_max_bytes_plus_one_before_finishing(client, fake):
    data = b"z" * 101
    with pytest.raises(WorkspaceTooLarge) as ei:
        await client.upload_source(TOKEN, BASE + "/sources", "big.bin", _chunks(data), max_bytes=100)
    assert ei.value.max_bytes == 100 and ei.value.filename == "big.bin"
    assert fake.shock_uploads == []  # the Shock handler never completed a body
    # The empty placeholder the create call made was removed again.
    assert BASE + "/sources/big.bin" not in fake.objects
    assert [m for m, _ in fake.rpc_calls] == ["Workspace.create", "Workspace.delete"]


async def test_upload_source_exactly_max_bytes_succeeds(client, fake):
    data = b"z" * 100
    uri = await client.upload_source(TOKEN, BASE + "/sources", "ok.bin", _chunks(data), max_bytes=100)
    assert uri.endswith("/sources/ok.bin") and fake.shock_uploads == [("node-1", 100)]


async def test_upload_source_never_overwrites(client, fake):
    await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"1"), max_bytes=10)
    with pytest.raises(WorkspaceError, match="overwrite"):
        await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"2"), max_bytes=10)
    assert fake.objects[BASE + "/sources/a.txt"]["size"] == 1


async def test_upload_source_rejects_bad_filename(client, fake):
    with pytest.raises(WorkspaceError, match="invalid filename"):
        await client.upload_source(TOKEN, BASE + "/sources", "../x", _chunks(b"1"), max_bytes=10)
    assert fake.requests == []


async def test_upload_source_shock_error_is_typed_and_placeholder_removed(client, fake):
    original = fake._shock

    def failing(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": 500, "error": ["disk full"], "data": None})

    fake._shock = failing  # type: ignore[method-assign]
    try:
        with pytest.raises(WorkspaceError, match="disk full"):
            await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"1"), max_bytes=10)
    finally:
        fake._shock = original  # type: ignore[method-assign]
    assert BASE + "/sources/a.txt" not in fake.objects
    assert [m for m, _ in fake.rpc_calls] == ["Workspace.create", "Workspace.delete"]
    # A retry of the same name is not blocked by a leftover empty object.
    uri = await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"12"), max_bytes=10)
    assert uri.endswith("/sources/a.txt") and fake.objects[BASE + "/sources/a.txt"]["size"] == 2


async def test_upload_source_cleans_up_when_no_upload_node_or_stream_fails(client, fake):
    fake.rpc_create_orig = fake.rpc_create  # type: ignore[attr-defined]

    def no_node(params):
        out = fake.rpc_create_orig(params)  # type: ignore[attr-defined]
        out[0][0][11] = ""
        return out

    fake.rpc_create = no_node  # type: ignore[method-assign]
    with pytest.raises(WorkspaceError, match="no upload node"):
        await client.upload_source(TOKEN, BASE + "/sources", "n.txt", _chunks(b"1"), max_bytes=10)
    fake.rpc_create = fake.rpc_create_orig  # type: ignore[method-assign]
    assert BASE + "/sources/n.txt" not in fake.objects

    async def broken():
        yield b"ab"
        raise OSError("disk read failed")

    with pytest.raises(OSError):
        await client.upload_source(TOKEN, BASE + "/sources", "s.txt", broken(), max_bytes=10)
    assert BASE + "/sources/s.txt" not in fake.objects
    assert fake.shock_uploads == []


async def test_upload_source_sized_sends_content_length(client, fake):
    data = b"q" * 5000
    await client.upload_source(TOKEN, BASE + "/sources", "s.pdf", _chunks(data, 999),
                               max_bytes=5000, size=5000)
    shock_req = next(r for r in fake.requests if r.url.host == "shock.test")
    head, tail = _multipart_frame("s.pdf")
    assert int(shock_req.headers["Content-Length"]) == len(head) + 5000 + len(tail)
    assert "transfer-encoding" not in shock_req.headers
    assert fake.shock_uploads == [("node-1", 5000)]


async def test_upload_source_sized_refuses_before_any_rpc(client, fake):
    with pytest.raises(WorkspaceTooLarge):
        await client.upload_source(TOKEN, BASE + "/sources", "big.pdf", _chunks(b"x" * 11),
                                   max_bytes=10, size=11)
    assert fake.requests == [] and fake.objects == {}


@pytest.mark.parametrize("actual", [4, 9])
async def test_upload_source_sized_stream_must_match_declared_size(client, fake, actual):
    with pytest.raises(WorkspaceError, match="declared"):
        await client.upload_source(TOKEN, BASE + "/sources", "m.txt", _chunks(b"x" * actual, 3),
                                   max_bytes=100, size=6)
    assert BASE + "/sources/m.txt" not in fake.objects
    assert [m for m, _ in fake.rpc_calls] == ["Workspace.create", "Workspace.delete"]


# ---------------------------------------------------------------------------
# list_versions / stat
# ---------------------------------------------------------------------------


async def test_list_versions_orders_numerically(client, fake):
    fake._mkdir_p(BASE + "/versions")
    for name in ("10", "9", "2", "1", "notes", "07", "7", "\u00b2", "\u0663", ""):
        if name:
            fake._mkdir_p(f"{BASE}/versions/{name}")
    fake.objects[BASE + "/versions/manifest.json"] = {"type": "json", "metadata": {}, "size": 1,
                                                       "shock": ""}
    got = await client.list_versions(TOKEN, "ws://" + BASE)
    # "07" (non-canonical), "²" and "٣" (str.isdigit() says yes) are skipped.
    assert [n for n, _ in got] == [1, 2, 7, 9, 10]
    assert got[2] == (7, f"ws://{BASE}/versions/7")
    assert got[-1] == (10, f"ws://{BASE}/versions/10")
    assert fake.rpc_calls == [("Workspace.ls", {"paths": [BASE + "/versions"]})]


async def test_list_versions_empty_folder(client, fake):
    fake._mkdir_p(BASE + "/versions")
    assert await client.list_versions(TOKEN, BASE) == []


async def test_list_versions_missing_folder_is_not_found(client, fake):
    with pytest.raises(WorkspaceNotFound):
        await client.list_versions(TOKEN, BASE)


async def test_stat_existing_and_missing(client, fake):
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    st = await client.stat(TOKEN, "ws://" + BASE + "/")
    assert st.exists and st.is_folder and st.metadata["ragstack.collection_id"] == "col-1"
    assert st.path == BASE and st.size == 0
    missing = await client.stat(TOKEN, BASE + "/nope")
    assert not missing.exists and missing.metadata == {}
    get = [p for m, p in fake.rpc_calls if m == "Workspace.get"]
    assert get[-1] == {"objects": [BASE + "/nope"], "metadata_only": 1}


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


async def test_every_request_carries_the_callers_token_and_nothing_else(fake):
    fakes = {TOKEN: fake, OTHER_TOKEN: FakeWorkspace(OTHER_TOKEN)}

    def route(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        return fakes[auth.removeprefix("OAuth ")](req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        client = WorkspaceClient(WS_URL, http)
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h", tenant="t")
        await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"abc"), max_bytes=3)
        await client.list_versions(TOKEN, BASE)
        await client.ensure_collection_folder(OTHER_TOKEN, "bob@patricbrc.org", "col-2",
                                              spec_hash="h", tenant="t")
        await client.stat(OTHER_TOKEN, "/bob@patricbrc.org/home")

    assert fake.requests and fakes[OTHER_TOKEN].requests
    for tok, f in fakes.items():
        for req in f.requests:
            expected = f"OAuth {tok}" if req.url.host == "shock.test" else tok
            assert req.headers["Authorization"] == expected
            # No other credential-bearing header, cookie or query parameter.
            assert "cookie" not in req.headers and "x-api-key" not in req.headers
            assert req.url.query == b""
            assert tok not in str(req.url)
            assert tok.encode() not in req.content
    # The client itself holds no token.
    assert not any(TOKEN in str(v) for v in vars(client).values())


async def test_http_401_and_403_surface_as_auth_error(client, fake):
    for status in (401, 403):
        fake.rpc_status = status
        with pytest.raises(WorkspaceAuthError, match=str(status)):
            await client.stat(TOKEN, BASE)


async def test_rpc_token_validation_failure_is_auth_error(client, fake):
    with pytest.raises(WorkspaceAuthError, match="Token validation failed"):
        await client.stat(OTHER_TOKEN, BASE)


async def test_token_never_appears_in_logs_or_exception_text(client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    errors: list[BaseException] = []
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h", tenant="t")
    await client.upload_source(TOKEN, BASE + "/sources", "a.txt", _chunks(b"abc"), max_bytes=3)
    for coro in (
        client.upload_source(TOKEN, BASE + "/sources", "b.txt", _chunks(b"abcd"), max_bytes=3),
        client.stat(OTHER_TOKEN, BASE),                      # RPC-level auth error
        client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="x", tenant="t"),
    ):
        with pytest.raises(WorkspaceError) as ei:
            await coro
        errors.append(ei.value)
    fake.rpc_status = 401
    with pytest.raises(WorkspaceAuthError) as ei:
        await client.stat(TOKEN, BASE)
    errors.append(ei.value)
    # A server that echoes the token back must still not leak it through us.
    fake.rpc_status = None
    fake.rpc_get = lambda params: (_ for _ in ()).throw(LookupError(f"bad token {TOKEN}"))  # type: ignore[method-assign]
    with pytest.raises(WorkspaceError) as ei:
        await client.stat(TOKEN, BASE)
    errors.append(ei.value)

    assert len(errors) == 5
    for exc in errors:
        assert TOKEN not in str(exc) and TOKEN not in repr(exc)
        assert "SECRETSIG" not in str(exc) and "OTHERSIG" not in str(exc)
    assert caplog.text  # something was logged at DEBUG
    assert TOKEN not in caplog.text and OTHER_TOKEN not in caplog.text
    assert "SECRETSIG" not in caplog.text and "OTHERSIG" not in caplog.text


# --- read_file (#203: the archive's receipt.json is read back as the caller) --


async def test_read_file_uses_get_download_url_then_get_with_token(client, fake):
    path = BASE + "/versions/3/receipt.json"
    fake.contents[path] = b'[{"shard_id": "a"}]'
    data = await client.read_file(TOKEN, "ws://" + path)
    assert data == b'[{"shard_id": "a"}]'
    assert [m for m, _ in fake.rpc_calls] == ["Workspace.get_download_url"]
    assert fake.rpc_calls[0][1] == {"objects": [path]}
    dl = [r for r in fake.requests if r.url.host == "download.test"]
    assert len(dl) == 1 and dl[0].headers["Authorization"] == TOKEN


async def test_read_file_missing_is_not_found(client, fake):
    with pytest.raises(WorkspaceNotFound):
        await client.read_file(TOKEN, BASE + "/versions/9/receipt.json")


async def test_read_file_download_errors_are_typed(client, fake):
    path = BASE + "/versions/3/receipt.json"
    fake.contents[path] = b"{}"
    fake.download_status = 403
    with pytest.raises(WorkspaceAuthError):
        await client.read_file(TOKEN, path)
    fake.download_status = 500
    with pytest.raises(WorkspaceError, match="HTTP 500"):
        await client.read_file(TOKEN, path)


async def test_read_file_refuses_a_non_http_download_url(client, fake):
    path = BASE + "/versions/3/receipt.json"
    fake.contents[path] = b"{}"
    fake.rpc_get_download_url = lambda params: [["ftp://nope/x"]]  # type: ignore[method-assign]
    with pytest.raises(WorkspaceError, match="no download URL"):
        await client.read_file(TOKEN, path)


async def test_upload_source_existing_object_is_refused_and_never_deleted(client, fake):
    """The service silently skips (and omits from the reply) an object that
    already exists. That object is the user's: refuse with WorkspaceExists and
    make NO delete RPC — the pre-existing object must survive."""
    dest = BASE + "/sources/keep.txt"
    fake.objects[dest] = {"type": "txt", "metadata": {}, "size": 7, "shock": ""}
    fake.rpc_create = lambda params: [[]]  # type: ignore[method-assign]  # skipped → omitted
    with pytest.raises(WorkspaceExists) as ei:
        await client.upload_source(TOKEN, BASE + "/sources", "keep.txt", _chunks(b"1"), max_bytes=10)
    assert ei.value.path == dest
    assert [m for m, _ in fake.rpc_calls] == ["Workspace.create"]  # no Workspace.delete
    assert fake.objects[dest]["size"] == 7 and fake.shock_uploads == []
