"""Collection provenance manifests.

One JSON file per collection under ``collection_manifest_dir`` recording how the
corpus was built — the *verified* lineage, as opposed to the registry's
operator-asserted ``chunk_method`` labels. Written at ingest (``source="ingest"``)
and, for collections that predate manifests, materialized from the registry spec
at startup (``source="config"``). Read by ``GET /v1/collections``.

Disabled when ``collection_manifest_dir`` is empty: every function no-ops /
returns ``None``, so behaviour is unchanged.

The second half of the module is the **tool provenance** of an archive version
(ADR-0010 decision 8, #655 step 2): the ``provenance`` object the worker's
pack/ingest/extract steps write into ``manifest.json``, the shard receipt and
the graph leg — which GoWe workflow and which tools image (name, digest, and
the image's own ``RELEASE`` identity) built it. Additive: a record without the
key reads as unknown (:func:`read_provenance`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


def chunk_descriptor(
    method: str, size: int | None, overlap: int | None, params: dict[str, Any] | None = None
) -> str:
    """A canonical, stable string identifying a chunking configuration — the
    content-address input for :func:`ragstack.stores.qdrant.collection_name` and
    the manifest's ``spec_hash``. Deterministic: sorted params, no whitespace."""
    parts = [method or "", str(size if size is not None else ""), str(overlap if overlap is not None else "")]
    if params:
        parts.append(json.dumps(params, sort_keys=True, separators=(",", ":")))
    return "/".join(parts)


def ragstack_version() -> str:
    """The installed package version, stamped into manifests so a corpus records
    which build wrote it. Empty when running from a source tree with no install
    metadata — provenance must never fail on a missing distribution."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ragstack")
    except PackageNotFoundError:
        return ""


def spec_hash(model: str, dim: int, chunk: str) -> str:
    """Short content-address of the full build spec (matches the hash embedded in
    a content-addressed collection name)."""
    return hashlib.sha1(f"{model}|{dim}|{chunk}".encode()).hexdigest()[:8]


class CollectionManifest(BaseModel):
    """The build spec + ingest metadata for one collection."""

    collection: str
    model: str
    dim: int
    embedding_api: str = ""
    embedding_endpoints: list[str] = Field(default_factory=list)
    chunk_method: str = ""
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = Field(default_factory=dict)
    spec_hash: str = ""
    corpus: str = ""  # a source hint (e.g. last ingest source path)
    chunk_count: int | None = None
    ingested_at: str = ""  # ISO 8601; caller stamps (Date.now is unavailable here)
    ragstack_version: str = ""
    source: str = "ingest"  # "ingest" (verified) | "config" (materialized from registry)


def make_ingest_manifest(
    *,
    collection: str,
    model: str,
    dim: int,
    embedding_api: str = "",
    embedding_endpoints: list[str] | None = None,
    chunk_method: str = "",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    chunk_params: dict[str, Any] | None = None,
    corpus: str = "",
    chunk_count: int | None = None,
    ragstack_version: str = "",
    source: str = "ingest",
) -> CollectionManifest:
    """Build a verified manifest for a just-ingested collection, stamped with the
    current time and the build spec's content hash. The single constructor shared
    by the API ingest hook and the CLI ingest scripts, so both record provenance
    identically."""
    from datetime import datetime

    desc = chunk_descriptor(chunk_method, chunk_size, chunk_overlap, chunk_params)
    return CollectionManifest(
        collection=collection,
        model=model or "",
        dim=dim,
        embedding_api=embedding_api,
        embedding_endpoints=list(embedding_endpoints or []),
        chunk_method=chunk_method or "",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_params=dict(chunk_params or {}),
        spec_hash=spec_hash(model or "", dim, desc),
        corpus=corpus,
        chunk_count=chunk_count,
        ingested_at=datetime.now(UTC).isoformat(),
        ragstack_version=ragstack_version,
        source=source,
    )


def _safe_name(collection: str) -> str:
    """A filesystem-safe basename for a collection (defensive — collection names
    are already slug-like, but never let one escape the manifest dir)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", collection)


def _path(manifest_dir: str, collection: str) -> str:
    return os.path.join(manifest_dir, f"{_safe_name(collection)}.json")


def read_manifest(manifest_dir: str, collection: str) -> CollectionManifest | None:
    """Load a collection's manifest, or ``None`` (disabled dir / missing / corrupt)."""
    if not manifest_dir:
        return None
    path = _path(manifest_dir, collection)
    try:
        with open(path, encoding="utf-8") as f:
            return CollectionManifest.model_validate_json(f.read())
    except FileNotFoundError:
        return None
    except Exception as e:  # corrupt/partial file — don't take down the reader
        log.warning("provenance: manifest %s unreadable: %s", path, e)
        return None


def write_manifest(manifest_dir: str, manifest: CollectionManifest) -> None:
    """Persist a manifest (atomic replace). No-op when the dir is unset."""
    if not manifest_dir:
        return
    os.makedirs(manifest_dir, exist_ok=True)
    path = _path(manifest_dir, manifest.collection)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))
    os.replace(tmp, path)  # atomic — a reader never sees a half-written file


def delete_manifest(manifest_dir: str, collection: str) -> bool:
    """Remove a collection's manifest file. Returns whether one was there.

    Used by the collection purge (``DELETE /v1/collections/{id}?purge=true``):
    once the physical store is gone the manifest describes nothing, and leaving
    it behind is exactly the orphan the purge exists to prevent. No-op (``False``)
    when manifests are disabled or the file is already absent — purging twice
    must not be an error. An unlink that fails for any *other* reason (e.g. a
    read-only manifest dir) raises, so the purge reports it instead of claiming
    a deletion that didn't happen."""
    if not manifest_dir:
        return False
    try:
        os.remove(_path(manifest_dir, collection))
    except FileNotFoundError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Tool provenance of an archive version / ingest receipt (ADR-0010 decision 8,
# #655 step 2): which workflow and which tools image built it.
# --------------------------------------------------------------------------- #

#: The in-image release file the tools-image build writes
#: (``apptainer/ragstack-tools.def``): ``key=value`` lines for ``version``,
#: ``commit``, ``build``, ``build_date`` — the same values as the image labels.
RELEASE_PATH = "/opt/ragstack/RELEASE"

#: The keys of a ``provenance`` object, in manifests (``manifest.json``) and
#: receipts. Every value is a string or ``null``; an object with every value
#: ``null`` — and a manifest with no ``provenance`` key at all — reads as
#: "unknown" (:func:`read_provenance`).
PROVENANCE_KEYS = (
    "workflow_id",         # the GoWe `wf_` id the API registered (binds text + image name)
    "tool_image",          # the image name the registered CWL's dockerPull carried
    "tool_image_digest",   # its sha256 from the committed receipt; null when unstamped
    "image_version",       # org.ragstack.version of the image the worker ran in (RELEASE)
    "image_commit",        # org.ragstack.commit
    "image_build",         # org.ragstack.build
)


def read_release(path: str | os.PathLike[str] = RELEASE_PATH) -> dict[str, str]:
    """Parse the in-image ``RELEASE`` file (``key=value`` per line, ``#``
    comments ignored). ``{}`` when absent or unreadable — outside the image
    (a checkout, a test) there is none, and provenance must never fail a pack
    step over it."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip():
            out[key.strip()] = value.strip()
    return out


def _nullable(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def tool_provenance(
    workflow_id: str | None = None,
    tool_image: str | None = None,
    tool_image_digest: str | None = None,
    *,
    release_path: str | os.PathLike[str] | None = None,
) -> dict[str, str | None]:
    """The ``provenance`` object a worker writes: the three submission inputs
    the API seeded (what it *was told*) plus what its own image's ``RELEASE``
    file says (what it *is*). Outside an image the last three are ``null``.
    Empty strings are ``null`` — a CWL ``["null", string]`` input omitted by
    a hand-run arrives as nothing, and nothing is not a value. ``release_path``
    defaults to :data:`RELEASE_PATH`, resolved at call time."""
    release = read_release(RELEASE_PATH if release_path is None else release_path)
    return {
        "workflow_id": _nullable(workflow_id),
        "tool_image": _nullable(tool_image),
        "tool_image_digest": _nullable(tool_image_digest),
        "image_version": _nullable(release.get("version")),
        "image_commit": _nullable(release.get("commit")),
        "image_build": _nullable(release.get("build")),
    }


def unknown_provenance() -> dict[str, str | None]:
    """What a record written before #655 step 2 says: every field ``null``."""
    return dict.fromkeys(PROVENANCE_KEYS)


def read_provenance(record: dict[str, Any] | None) -> dict[str, str | None]:
    """The ``provenance`` object of a manifest / receipt / summary dict,
    normalised to exactly :data:`PROVENANCE_KEYS`. A record without the key
    (pre-step-2), with ``null``, or with something that is not an object reads
    as :func:`unknown_provenance`; unknown sub-keys are dropped and missing
    ones are ``null``, so every reader sees one shape."""
    raw = (record or {}).get("provenance") if isinstance(record, dict) else None
    if not isinstance(raw, dict):
        return unknown_provenance()
    return {k: _nullable(raw.get(k)) for k in PROVENANCE_KEYS}


def add_provenance_arguments(parser: Any) -> None:
    """The three CLI flags every worker-side tool takes for its provenance
    record (``--workflow-id``, ``--tool-image``, ``--tool-image-digest``),
    bound from the CWL inputs of the same names. All optional: a hand-run
    tool records nulls and is told nothing it cannot verify."""
    g = parser.add_argument_group(
        "provenance (ADR-0010 decision 8)",
        "seeded per submission by the API; recorded, never verified, here")
    g.add_argument("--workflow-id", default="", metavar="WF_ID",
                   help="the GoWe workflow id the submission was pinned to")
    g.add_argument("--tool-image", default="", metavar="NAME",
                   help="the tools image name the registered CWL's dockerPull carried")
    g.add_argument("--tool-image-digest", default="", metavar="SHA256",
                   help="its sha256 from the committed cwl/tool-image.receipt.json")


def provenance_from_args(args: Any) -> dict[str, str | None]:
    """:func:`tool_provenance` from the flags :func:`add_provenance_arguments`
    declared (``RELEASE`` is read from its in-image path)."""
    return tool_provenance(
        getattr(args, "workflow_id", ""),
        getattr(args, "tool_image", ""),
        getattr(args, "tool_image_digest", ""),
    )
