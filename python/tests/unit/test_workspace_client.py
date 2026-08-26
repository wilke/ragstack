"""Unit tests for ``ragstack.workspace`` against an ``httpx.MockTransport`` fake.

The fake — :mod:`tests.workspace_support`, shared with the API-level gowe
upload tests — models the BV-BRC Workspace JSON-RPC service and Shock closely
enough to pin the request shapes the real service expects, *including* its two
usermeta constraints: ``create`` silently stores nothing for a dotted field
name (#408) and ``update_metadata`` refuses one outright (#414). No live
service is ever contacted.
"""
from __future__ import annotations

import logging

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
from tests.workspace_support import (
    OTHER_TOKEN,
    SUBJECT,
    TOKEN,
    WS_URL,
    FakeWorkspace,
)


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


WANTED = {
    "ragstack_format": ARCHIVE_FORMAT, "ragstack_collection_id": "col-1",
    "ragstack_tenant": "t1", "ragstack_spec_hash": "h1",
}


def _meta_keys_written(fake: FakeWorkspace) -> set[str]:
    """Every user_metadata field name the client asked the service to store."""
    keys: set[str] = set()
    for method, params in fake.rpc_calls:
        if method == "Workspace.create":
            for _p, _t, metadata, _d in params["objects"]:
                keys |= set(metadata)
        elif method == "Workspace.update_metadata":
            for _p, metadata in params["objects"]:
                keys |= set(metadata)
    return keys


async def test_ensure_collection_folder_creates_layout_and_metadata(client, fake):
    uri = await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert uri == "ws://" + BASE
    assert set(fake.objects) >= {BASE, BASE + "/sources", BASE + "/versions"}
    assert fake.objects[BASE]["metadata"] == WANTED
    assert fake.objects[BASE + "/sources"]["metadata"] == {}
    # The create carried the metadata in the tuple's third slot (the confirmed shape).
    create = next(p for m, p in fake.rpc_calls if m == "Workspace.create")
    assert create["objects"][0][:2] == [BASE, "folder"]
    assert create["objects"][0][2]["ragstack_format"] == ARCHIVE_FORMAT
    assert create["objects"][0][3] is None


async def test_no_dotted_field_name_is_ever_sent(client, fake):
    """#414: a dot in a usermeta field name is unstorable — silently on
    ``create``, with a hard error on ``update_metadata``. Nothing this client
    writes may contain one, on any path."""
    fake._mkdir_p(BASE)  # forces the backfill path too
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    written = _meta_keys_written(fake)
    assert written and not any("." in key for key in written), written
    assert fake.objects[BASE]["metadata"] == WANTED


async def test_ensure_collection_folder_is_idempotent_and_writes_metadata_once(client, fake):
    for _ in range(3):
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.metadata_writes == [BASE]
    assert not any(m == "Workspace.update_metadata" for m, _ in fake.rpc_calls)
    # /u/home, .ragstack, collections (auto-created parents) + col-1, sources, versions
    assert len(fake.objects) == 6
    assert fake.objects[BASE]["metadata"]["ragstack_spec_hash"] == "h1"


async def test_ensure_collection_folder_backfills_missing_metadata_once(client, fake):
    fake._mkdir_p(BASE + "/sources")  # pre-existing, user-made, no ragstack_* keys
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.metadata_writes == [BASE]
    upd = [p for m, p in fake.rpc_calls if m == "Workspace.update_metadata"]
    assert len(upd) == 1 and upd[0]["append"] == 1 and upd[0]["objects"][0][0] == BASE
    assert BASE + "/versions" in fake.objects
    assert fake.objects[BASE]["metadata"] == WANTED


async def test_second_call_on_a_folder_created_before_the_fix_succeeds(client, fake):
    """The production shape of #414: a collection folder created by the old
    client exists with ``metadata == {}`` (its dotted keys were dropped). The
    NEXT ingest must heal it rather than blow up on ``update_metadata``."""
    fake._mkdir_p(BASE)
    fake.objects[BASE]["metadata"] = {}
    for _ in range(3):
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.objects[BASE]["metadata"] == WANTED
    assert len([p for m, p in fake.rpc_calls if m == "Workspace.update_metadata"]) == 1


async def test_ensure_collection_folder_converges_when_create_stores_no_metadata(client, fake):
    """#408's pessimistic reading — ``create`` persists no usermeta at all. The
    read-back then backfills through ``update_metadata``, and the second call
    (the one that used to 500) is a no-op."""
    fake.create_stores_metadata = False
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.objects[BASE]["metadata"] == WANTED  # not silently dropped
    before = len(fake.rpc_calls)
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    assert fake.objects[BASE]["metadata"] == WANTED
    # Second pass: stat + the subfolder create, and no further metadata write.
    assert [m for m, _ in fake.rpc_calls[before:]] == ["Workspace.get", "Workspace.create"]


async def test_metadata_that_will_not_persist_warns_instead_of_failing(client, fake, caplog):
    """A folder the service refuses to stamp must not close the collection to
    ingest — the keys are for discoverability (#353). It warns, loudly, once."""
    fake.create_stores_metadata = False
    fake.rpc_update_metadata = lambda params: []  # accepted, stores nothing
    with caplog.at_level(logging.WARNING, logger="ragstack.workspace"):
        uri = await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1",
                                                    spec_hash="h1", tenant="t1")
    assert uri == "ws://" + BASE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert BASE in warnings[0].getMessage() and "ragstack_spec_hash" in warnings[0].getMessage()
    assert TOKEN not in warnings[0].getMessage()


async def test_a_dotted_update_is_refused_by_the_service(client, fake):
    """Pins the constraint the fake encodes: had the client kept the dotted
    names, this is the error the second upload hit — a plain ``WorkspaceError``
    (it matches neither the auth nor the not-found pattern), i.e. an unhandled
    500 on the route."""
    fake._mkdir_p(BASE)
    with pytest.raises(WorkspaceError) as ei:
        await client._rpc(TOKEN, "Workspace.update_metadata",
                          {"objects": [[BASE, {"ragstack.spec_hash": "h1"}]], "append": 1})
    assert type(ei.value) is WorkspaceError
    assert "is not valid for storage" in str(ei.value)


async def test_ensure_collection_folder_tolerates_the_legacy_dotted_shape(client, fake):
    """No live folder carries the dotted keys (they never persisted), but a
    reader must not mistake one for a foreign collection build if it does."""
    fake._mkdir_p(BASE)
    fake.objects[BASE]["metadata"] = {
        "ragstack.format": ARCHIVE_FORMAT, "ragstack.collection_id": "col-1",
        "ragstack.tenant": "t1", "ragstack.spec_hash": "h1",
    }
    await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h1", tenant="t1")
    with pytest.raises(WorkspaceError, match="different collection build"):
        await client.ensure_collection_folder(TOKEN, SUBJECT, "col-1", spec_hash="h2", tenant="t1")


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
    assert st.exists and st.is_folder and st.metadata["ragstack_collection_id"] == "col-1"
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
