"""Thin async client for the BV-BRC Workspace (JSON-RPC 1.1) plus Shock for bytes.

Phase 2 of #201 / building block of the archive design in #353: the API keeps a
per-collection folder in the **owner's** Workspace —
``/<subject>/home/.ragstack/collections/<id>/`` with ``sources/`` and
``versions/`` beneath it — and every call here is made with the **caller's**
token. There is no service identity: the token is passed per call, placed only
in the ``Authorization`` header, and never logged or echoed in an exception.

Call shapes (copied, not the code) come from the Workspace service's own
typespec (``Workspace.spec`` / ``WorkspaceImpl.pm``) and GoWe's clients:

* every RPC is ``POST <workspace_url>`` with
  ``{"id", "method": "Workspace.<m>", "version": "1.1", "params": [<dict>]}``
  and the raw token in ``Authorization``; the reply is ``{"result": …}`` or
  ``{"error": {"code", "message", …}}``.
* ``Workspace.create`` ``{objects: [[path, type, user_metadata, data]], …}``;
  type ``"folder"`` makes a directory, parents are created on demand and an
  existing folder is silently skipped (and omitted from the result), which is
  what makes :meth:`WorkspaceClient.ensure_collection_folder` idempotent.
  ``createUploadNodes: 1`` returns a Shock node URL instead of storing data.
* **usermeta field names may not contain a dot** — the service stores them in
  Mongo, which forbids it. ``Workspace.create`` accepts a dotted key in the
  object tuple and stores *nothing* for it, with no error (#408);
  ``Workspace.update_metadata`` refuses the whole call (``The dotted field
  'a.b' in 'metadata.a.b' is not valid for storage``, #414). Hence the flat
  ``ragstack_*`` names, and the read-back in
  :meth:`WorkspaceClient._stamp` — a metadata write here is verified, not
  assumed. Values are plain strings: GoWe's reader types usermeta as
  ``map[string]string`` and drops anything else.
* ``Workspace.get`` ``{objects: [path], metadata_only: 1}`` → ``[[meta], …]``;
  ``Workspace.ls`` ``{paths: [dir]}`` → ``{dir: [meta, …]}``;
  ``Workspace.update_metadata`` ``{objects: [[path, user_metadata]], append: 1}``;
  ``Workspace.get_download_url`` ``{objects: [path]}`` → ``[[url, …]]``, then a
  plain ``GET`` of the URL with the token (:meth:`WorkspaceClient.read_file`).
* an object's ``meta`` is the tuple ``[name, type, parent_path, creation_time,
  id, owner, size, user_metadata, auto_metadata, user_perm, global_perm,
  shock_url]`` (``parent_path`` is the containing folder as the service reports it;
  nothing here depends on it).
* bytes go to Shock as ``PUT <shock_url>`` (the URL the create call returned),
  ``multipart/form-data`` with one ``upload`` file field and
  ``Authorization: OAuth <token>``; the body is streamed, never buffered.

Nothing here talks to a live service in tests — see
``tests/unit/test_workspace_client.py`` for the ``httpx.MockTransport`` fake.
"""
from __future__ import annotations

import inspect
import itertools
import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: Value of ``ragstack_format`` on every collection folder this client creates.
ARCHIVE_FORMAT = "ragstack-archive/1"
#: Where collections live under a user's home (issue #353 layout).
COLLECTIONS_ROOT = ".ragstack/collections"
#: Read granularity for streamed uploads (bytes in flight at once).
STREAM_CHUNK = 1 << 20

#: The usermeta stamped on a collection folder. Underscore-separated, NOT dotted
#: (#414): the Workspace stores usermeta in Mongo, which cannot hold a dot in a
#: field name — ``create`` accepts a dotted key and stores nothing for it, and
#: ``update_metadata`` refuses the call outright. Flat string values, because
#: that is all a reader of this metadata is guaranteed to see (GoWe's client
#: types usermeta as ``map[string]string`` and drops non-string values).
_META_KEYS = ("ragstack_format", "ragstack_collection_id", "ragstack_tenant", "ragstack_spec_hash")
#: Pre-#414 dotted spelling → its canonical name. No folder in any live
#: Workspace carries these (they never persisted), but reading them keeps a
#: folder stamped by an older client — or by a store without the constraint —
#: legible instead of looking like a foreign collection build.
_LEGACY_META_KEYS = {
    "ragstack.format": "ragstack_format",
    "ragstack.collection_id": "ragstack_collection_id",
    "ragstack.tenant": "ragstack_tenant",
    "ragstack.spec_hash": "ragstack_spec_hash",
}
_AUTH_RE = re.compile(r"authentication required|token validation failed|insufficient permissions"
                      r"|permission denied|not authorized", re.I)
_NOT_FOUND_RE = re.compile(r"not found|does not exist", re.I)
_FOLDER_TYPES = frozenset({"folder", "modelfolder"})
_VERSION_RE = re.compile(r"[0-9]+")
_EXT_TYPES = {"pdf": "pdf", "txt": "txt"}
_ids = itertools.count(1)


# ---------------------------------------------------------------------------
# Errors — none of them ever carries the token in its text.
# ---------------------------------------------------------------------------


class WorkspaceError(Exception):
    """Any Workspace/Shock failure that is not more specifically typed."""


class WorkspaceAuthError(WorkspaceError):
    """HTTP 401/403, or an RPC error saying the token was missing/invalid/insufficient."""


class WorkspaceNotFound(WorkspaceError):
    """The Workspace reported the path does not exist."""


class WorkspaceExists(WorkspaceError):
    """``upload_source`` found an object already at the destination path. Nothing
    was written and — critically — nothing is deleted: the object is the user's."""

    def __init__(self, path: str) -> None:
        super().__init__(f"{path} already exists (never overwritten)")
        self.path = path


class WorkspaceTooLarge(WorkspaceError):
    """The upload stream exceeded ``max_bytes``; refused before the upload finished."""

    def __init__(self, filename: str, max_bytes: int) -> None:
        super().__init__(f"upload of {filename!r} exceeds max_bytes={max_bytes}")
        self.filename = filename
        self.max_bytes = max_bytes


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceObject:
    """One parsed ObjectMeta tuple (see the module docstring for the layout).

    ``path`` is ``meta[2]`` joined with ``name``; the spec describes ``[2]`` as the
    object's path and the service reports the containing folder there, so treat
    ``path`` as informational — no method here relies on it.
    """

    name: str
    type: str
    path: str  # full path: parent + name
    created: str
    id: str
    owner: str
    size: int
    metadata: dict[str, str] = field(default_factory=dict)
    auto_metadata: dict[str, Any] = field(default_factory=dict)
    shock_url: str = ""

    @property
    def is_folder(self) -> bool:
        return self.type.lower() in _FOLDER_TYPES

    @classmethod
    def from_meta(cls, meta: Any) -> WorkspaceObject:
        if not isinstance(meta, list) or len(meta) < 9:
            raise WorkspaceError("malformed object metadata tuple from Workspace")
        name = str(meta[0])
        parent = str(meta[2] or "")
        full = parent + name if parent.endswith("/") else f"{parent}/{name}"
        return cls(
            name=name,
            type=str(meta[1] or ""),
            path=full,
            created=str(meta[3] or ""),
            id=str(meta[4] or ""),
            owner=str(meta[5] or ""),
            size=int(meta[6] or 0),
            metadata=dict(meta[7] or {}),
            auto_metadata=dict(meta[8] or {}),
            shock_url=str(meta[11] or "") if len(meta) > 11 and meta[11] else "",
        )


@dataclass(frozen=True)
class WorkspaceStat:
    """Result of :meth:`WorkspaceClient.stat` — ``exists`` is False for a missing path."""

    path: str
    exists: bool
    type: str = ""
    size: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_folder(self) -> bool:
        return self.type.lower() in _FOLDER_TYPES


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def ws_path(path: str) -> str:
    """Normalise ``ws:///u/home/x``, ``ws://u/home/x`` or ``/u/home/x`` to ``/u/home/x``."""
    p = path.strip()
    if p.startswith("ws://"):
        p = p[len("ws://"):]
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    if not p:
        raise WorkspaceError("empty workspace path")
    return p


def ws_uri(path: str) -> str:
    """``/u/home/x`` → ``ws:///u/home/x`` (the form GoWe accepts as a File location)."""
    return "ws://" + ws_path(path)


def collection_folder(subject: str, collection_id: str) -> str:
    """``/<subject>/home/.ragstack/collections/<collection_id>`` (no ``ws://`` prefix)."""
    return f"/{_segment(subject, 'subject')}/home/{COLLECTIONS_ROOT}/{_segment(collection_id, 'collection_id')}"


def ragstack_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """The ragstack keys carried by a folder's usermeta, under their canonical
    (underscore) names — the pre-#414 dotted spelling reads as its equivalent."""
    found = {canonical: metadata[legacy]
             for legacy, canonical in _LEGACY_META_KEYS.items() if legacy in metadata}
    found.update({k: metadata[k] for k in _META_KEYS if k in metadata})
    return found


def _segment(value: str, what: str) -> str:
    """A single path component: non-empty, no separators, not ``.``/``..``."""
    if not value or "/" in value or "\\" in value or value in (".", "..") or "\x00" in value:
        raise WorkspaceError(f"invalid {what} for a workspace path: {value!r}")
    return value


def _object_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _EXT_TYPES.get(ext, "unspecified")


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class WorkspaceClient:
    """Async Workspace client. Holds no token — every method takes the caller's.

    ``http`` is the app's shared :class:`httpx.AsyncClient` (as for the sidecar
    and GoWe clients); the caller owns its lifecycle.
    """

    def __init__(self, base_url: str, http: httpx.AsyncClient, *, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.timeout = timeout

    # -- public API -------------------------------------------------------

    async def ensure_collection_folder(
        self,
        token: str,
        subject: str,
        collection_id: str,
        *,
        spec_hash: str,
        tenant: str,
    ) -> str:
        """Create ``/<subject>/home/.ragstack/collections/<id>/`` + ``sources/`` +
        ``versions/`` and stamp the four ``ragstack_*`` keys on the collection folder.

        Idempotent, and — since #414 — safe to call on every ingest into the same
        collection, which is what the upload route does. The metadata is written
        at most once per folder: carried on the create and, if the service did
        not keep it (#408) or the folder predates the stamp, backfilled with
        ``Workspace.update_metadata``; a folder that already carries the wanted
        values costs one ``stat``. A folder carrying *different* ``ragstack_*``
        values belongs to another collection build and is refused rather than
        overwritten. Two concurrent first calls with different ``spec_hash``
        values can race on the create (last write wins server-side); that is
        accepted because collection creation is a single-writer path.

        Metadata that will not persist is a WARNING, never a failure: the keys
        exist for discoverability (#353), and failing an ingest over them is
        precisely what made #414 a hard 500 on every upload after the first.
        Returns the ``ws://`` URI of the collection folder.
        """
        base = collection_folder(subject, collection_id)
        wanted = {
            "ragstack_format": ARCHIVE_FORMAT,
            "ragstack_collection_id": collection_id,
            "ragstack_tenant": tenant,
            "ragstack_spec_hash": spec_hash,
        }
        subfolders: list[list[Any]] = [
            [f"{base}/sources", "folder", {}, None],
            [f"{base}/versions", "folder", {}, None],
        ]

        st = await self.stat(token, base)
        if not st.exists:
            await self._rpc(token, "Workspace.create",
                            {"objects": [[base, "folder", wanted, None], *subfolders]})
            log.debug("workspace: created collection folder %s", base)
            await self._stamp(token, base, wanted)
            return ws_uri(base)

        if not st.is_folder:
            raise WorkspaceError(f"{base} exists but is not a folder")
        present = ragstack_metadata(st.metadata)
        if not present:
            await self._stamp(token, base, wanted, known_missing=True)
        elif present != wanted:
            raise WorkspaceError(f"{base} carries ragstack metadata for a different collection build")
        # Existing folders are skipped server-side, so this is a no-op when both exist.
        await self._rpc(token, "Workspace.create", {"objects": subfolders})
        return ws_uri(base)

    async def _stamp(
        self, token: str, base: str, wanted: dict[str, str], *, known_missing: bool = False
    ) -> None:
        """Make the ``ragstack_*`` keys actually land on ``base``, or say why not.

        ``Workspace.create`` takes a user_metadata dict in the object tuple and
        was seen to store nothing of it (#408) with no error of any kind — so the
        create is treated as best-effort and read back here, with
        ``Workspace.update_metadata`` as the authoritative write. If the keys are
        still not there afterwards the folder is left as it is and a warning
        names the folder and the keys: this is discoverability metadata, and a
        collection that cannot be stamped must still accept documents.
        """
        if not known_missing:
            st = await self.stat(token, base)
            if ragstack_metadata(st.metadata) == wanted:
                return
            log.debug("workspace: create did not store the metadata on %s; backfilling", base)
        try:
            await self._rpc(token, "Workspace.update_metadata",
                            {"objects": [[base, wanted]], "append": 1})
        except WorkspaceError as exc:
            log.warning("workspace: could not stamp %s on %s: %s — the folder is usable, "
                        "but it is not self-describing", ", ".join(sorted(wanted)), base, exc)
            return
        log.debug("workspace: stamped metadata on %s", base)
        st = await self.stat(token, base)
        stored = ragstack_metadata(st.metadata)
        if stored != wanted:
            log.warning("workspace: the metadata write on %s was accepted but did not persist "
                        "(stored %s, wanted %s) — the folder is usable, but it is not "
                        "self-describing", base, sorted(stored), ", ".join(sorted(wanted)))

    async def upload_source(
        self,
        token: str,
        folder: str,
        filename: str,
        stream: Any,
        *,
        max_bytes: int,
        size: int | None = None,
    ) -> str:
        """Stream ``stream`` into ``<folder>/<filename>`` via Shock; return its ``ws://`` URI.

        ``stream`` may be an async iterable of ``bytes``, an object with an async
        ``read(n)`` (e.g. Starlette's ``UploadFile``) or a sync binary file-like.
        Bytes are forwarded in :data:`STREAM_CHUNK` pieces — the file is never
        held in memory. The byte count is checked as it flows, so an oversized
        stream raises :class:`WorkspaceTooLarge` *before* the upload completes.

        ``size`` (when the caller knows it) does two things: a ``size > max_bytes``
        stream is refused before any RPC is made, and the Shock ``PUT`` carries a
        ``Content-Length`` (what both reference clients send) instead of chunked
        transfer encoding; a stream that does not match the declared size is a
        :class:`WorkspaceError`. Whatever fails after the upload node was created
        — no node in the reply, a transport error, a stream error, a Shock
        non-2xx, too large — the empty placeholder object is removed again
        (best-effort) so a retry of the same name is not blocked. An existing
        object of the same name is never overwritten.
        """
        if max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        if size is not None and size < 0:
            raise ValueError("size must be >= 0")
        dest = f"{ws_path(folder)}/{_segment(filename, 'filename')}"
        if size is not None and size > max_bytes:
            raise WorkspaceTooLarge(filename, max_bytes)
        result = await self._rpc(
            token, "Workspace.create",
            {"objects": [[dest, _object_type(filename), {}, None]], "createUploadNodes": 1},
        )
        created = _objects(result)
        if not created:
            # The service silently skips (and omits from the result) an object
            # that already exists. That object is the user's and was NOT created
            # by this call, so it must not be discarded below — refuse instead.
            raise WorkspaceExists(dest)
        # From here on the placeholder listed in the reply is ours to remove on
        # any failure.
        try:
            if not created[0].shock_url:
                raise WorkspaceError(f"Workspace returned no upload node for {dest}")
            shock_url = created[0].shock_url
            if not shock_url.startswith(("http://", "https://")):
                raise WorkspaceError(f"Workspace returned a non-HTTP upload URL for {dest}")
            head, tail = _multipart_frame(filename)
            headers = {
                "Authorization": f"OAuth {token}",
                "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
            }
            if size is not None:
                headers["Content-Length"] = str(len(head) + size + len(tail))
            try:
                resp = await self.http.put(
                    shock_url,
                    content=_multipart(head, tail, _bounded(stream, filename, max_bytes, size)),
                    headers=headers,
                    timeout=self.timeout,
                )
            except WorkspaceError:
                raise
            except httpx.HTTPError as exc:
                raise WorkspaceError(
                    f"Shock upload of {dest} failed: {_scrub(str(exc), token)}"
                ) from None
            self._check_shock(resp, dest, token)
        except Exception:  # not BaseException: never await an RPC during cancellation
            await self._discard_placeholder(token, dest)
            raise
        log.debug("workspace: uploaded %s", dest)
        return ws_uri(dest)

    async def list_versions(self, token: str, collection_folder: str) -> list[tuple[int, str]]:
        """``[(n, ws_uri)]`` for the numeric subfolders of ``<collection_folder>/versions``,
        ordered numerically (``10`` after ``9``). Only canonical ASCII decimal names
        count (``7``, not ``07`` or a Unicode digit); anything else is skipped.
        Raises :class:`WorkspaceNotFound` when the ``versions/`` folder is missing.
        """
        versions = f"{ws_path(collection_folder)}/versions"
        result = await self._rpc(token, "Workspace.ls", {"paths": [versions]})
        listing = result[0] if isinstance(result, list) and result else result
        if not isinstance(listing, dict):
            raise WorkspaceError("malformed ls result from Workspace")
        entries = listing.get(versions)
        if entries is None:
            entries = listing.get(versions + "/")
        if entries is None:
            # ls of a missing folder comes back empty rather than as an error.
            st = await self.stat(token, versions)
            if not st.exists:
                raise WorkspaceNotFound(f"{versions} does not exist")
            entries = []
        found: list[tuple[int, str]] = []
        for meta in entries:
            obj = WorkspaceObject.from_meta(meta)
            if not obj.is_folder:
                continue
            if _VERSION_RE.fullmatch(obj.name) and str(int(obj.name)) == obj.name:
                found.append((int(obj.name), ws_uri(f"{versions}/{obj.name}")))
            else:
                log.debug("workspace: skipping non-version entry %r in %s", obj.name, versions)
        found.sort(key=lambda t: t[0])
        return found

    async def read_file(self, token: str, path: str) -> bytes:
        """Fetch the bytes of ``path`` as the caller: ``Workspace.get_download_url``
        for the object, then a ``GET`` of that URL with the token — the same two
        steps GoWe's stager takes. Sized for small control files (an archive's
        ``receipt.json`` / ``manifest.json``); the whole body is returned.
        """
        p = ws_path(path)
        result = await self._rpc(token, "Workspace.get_download_url", {"objects": [p]})
        urls = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
        url = urls[0] if isinstance(urls, list) and urls else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise WorkspaceError(f"Workspace returned no download URL for {p}")
        try:
            resp = await self.http.get(url, headers={"Authorization": token}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise WorkspaceError(f"download of {p} failed: {_scrub(str(exc), token)}") from None
        if resp.status_code in (401, 403):
            raise WorkspaceAuthError(f"Workspace rejected the token for {p} (HTTP {resp.status_code})")
        if resp.status_code == 404:
            raise WorkspaceNotFound(f"{p} does not exist")
        if resp.status_code >= 400:
            raise WorkspaceError(f"download of {p} failed: HTTP {resp.status_code}")
        return resp.content

    async def stat(self, token: str, path: str) -> WorkspaceStat:
        """Existence, type, size and user metadata of ``path`` (``Workspace.get``,
        ``metadata_only``). A missing path yields ``exists=False``, not an error.
        """
        p = ws_path(path)
        try:
            result = await self._rpc(token, "Workspace.get", {"objects": [p], "metadata_only": 1})
        except WorkspaceNotFound:
            return WorkspaceStat(path=p, exists=False)
        # get → [[meta] | [meta, data], …]
        entries = result[0] if isinstance(result, list) and result else []
        if not entries:
            return WorkspaceStat(path=p, exists=False)
        first = entries[0]
        meta = first[0] if isinstance(first, list) and first and isinstance(first[0], list) else first
        obj = WorkspaceObject.from_meta(meta)
        return WorkspaceStat(path=p, exists=True, type=obj.type, size=obj.size,
                             metadata=obj.metadata)

    # -- plumbing ---------------------------------------------------------

    async def _rpc(self, token: str, method: str, params: dict[str, Any]) -> Any:
        """One JSON-RPC 1.1 call. Only the ``Authorization`` header carries the token."""
        body = {"id": str(next(_ids)), "method": method, "version": "1.1", "params": [params]}
        try:
            resp = await self.http.post(
                self.base_url,
                json=body,
                headers={"Authorization": token, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise WorkspaceError(f"{method}: transport error: {_scrub(str(exc), token)}") from None
        if resp.status_code in (401, 403):
            raise WorkspaceAuthError(f"{method}: Workspace rejected the token (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError:
            data = None
        err = data.get("error") if isinstance(data, dict) else None
        if err:
            msg = _scrub(str(err.get("message") if isinstance(err, dict) else err), token)
            log.debug("workspace: %s failed: %s", method, msg)
            if _AUTH_RE.search(msg):
                raise WorkspaceAuthError(f"{method}: {msg}")
            if _NOT_FOUND_RE.search(msg):
                raise WorkspaceNotFound(f"{method}: {msg}")
            raise WorkspaceError(f"{method}: {msg}")
        if resp.status_code >= 400 or not isinstance(data, dict):
            raise WorkspaceError(f"{method}: unexpected HTTP {resp.status_code} from Workspace")
        return data.get("result")

    @staticmethod
    def _check_shock(resp: httpx.Response, dest: str, token: str) -> None:
        if resp.status_code in (401, 403):
            raise WorkspaceAuthError(f"Shock rejected the token for {dest} (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError:
            data = None
        errors = data.get("error") if isinstance(data, dict) else None
        if resp.status_code >= 400 or errors:
            detail = _scrub(str(errors or resp.status_code), token)
            raise WorkspaceError(f"Shock upload of {dest} failed: {detail}")

    async def _discard_placeholder(self, token: str, dest: str) -> None:
        """Remove the empty upload node a refused upload left behind. Best-effort."""
        try:
            await self._rpc(token, "Workspace.delete", {"objects": [dest]})
        except WorkspaceError as exc:
            log.debug("workspace: could not remove placeholder %s: %s", dest, exc)


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

_BOUNDARY = "ragstack-" + uuid.uuid4().hex


def _scrub(text: str, token: str) -> str:
    """Defence in depth: never let the token through in any message we build."""
    return text.replace(token, "[token]") if token else text


def _objects(result: Any) -> list[WorkspaceObject]:
    """Parse a ``list<ObjectMeta>`` RPC result (``[[meta, …]]``)."""
    metas = result[0] if isinstance(result, list) and result else []
    return [WorkspaceObject.from_meta(m) for m in metas or []]


async def _iter_stream(stream: Any) -> AsyncIterator[bytes]:
    """Yield ``bytes`` chunks from any of the accepted stream kinds."""
    if hasattr(stream, "__aiter__"):
        async for chunk in stream:
            if chunk:
                yield bytes(chunk)
        return
    read = getattr(stream, "read", None)
    if read is None:
        raise TypeError("stream must be an async iterable of bytes or a file-like with read()")
    if inspect.iscoroutinefunction(read):
        while chunk := await read(STREAM_CHUNK):
            yield chunk
    else:
        while chunk := read(STREAM_CHUNK):
            yield chunk


async def _bounded(
    stream: Any, filename: str, max_bytes: int, size: int | None = None
) -> AsyncIterator[bytes]:
    """Forward chunks, raising :class:`WorkspaceTooLarge` the moment the running
    total exceeds ``max_bytes`` — mid-body, so the upload never completes. With a
    declared ``size`` the stream must also match it exactly (the ``Content-Length``
    already went out), else :class:`WorkspaceError`."""
    total = 0
    async for chunk in _iter_stream(stream):
        total += len(chunk)
        if total > max_bytes:
            raise WorkspaceTooLarge(filename, max_bytes)
        if size is not None and total > size:
            raise WorkspaceError(f"stream for {filename!r} is longer than the declared size {size}")
        yield chunk
    if size is not None and total != size:
        raise WorkspaceError(f"stream for {filename!r} was {total} bytes, declared {size}")


def _multipart_frame(filename: str) -> tuple[bytes, bytes]:
    """The fixed bytes around a single ``upload`` file field (what Shock expects)."""
    safe = filename.replace('"', "%22").replace("\r", "").replace("\n", "")
    head = (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="upload"; filename="{safe}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{_BOUNDARY}--\r\n".encode()
    return head, tail


async def _multipart(head: bytes, tail: bytes, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Frame ``chunks`` between the precomputed multipart ``head`` and ``tail``."""
    yield head
    async for chunk in chunks:
        yield chunk
    yield tail
