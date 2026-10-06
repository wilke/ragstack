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

The third part is **experiment provenance** (:func:`experiment_provenance`):
the block every experiment artifact — results JSON, receipt, run manifest —
embeds, so a number can be traced to the code that produced it. It replaces
the old "``chunkers.py`` is frozen by the chunking study" rule (owner
decision, 2026-10-06): code may move; every run records which code it was.
Printed by ``python -m ragstack.provenance --experiment``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import socket
import sys
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
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


# --------------------------------------------------------------------------- #
# Experiment provenance (owner decision 2026-10-06): which code produced a
# number. Replaces "chunkers.py is frozen by the chunking study".
# --------------------------------------------------------------------------- #

#: Bumped when a key changes meaning or disappears; adding a key does not bump it.
EXPERIMENT_PROVENANCE_SCHEMA = 1

#: Warning prefixes. Each warning starts with one of these, then says why.
WARN_DIRTY = "dirty tree"
WARN_NOT_IN_IMAGE = "not in an image"
WARN_NO_GIT = "no git checkout"

#: A few short texts whose sentence segmentation is fingerprinted when a run
#: does not pass its own documents: abbreviations, decimals, citations and a
#: no-punctuation run are where segmenters disagree. Changing this tuple changes
#: every canonical fingerprint, so append a new constant rather than edit it.
CANONICAL_SEGMENTATION_SAMPLE: tuple[str, ...] = (
    "Dr. Smith et al. reported a 2.5-fold increase (p < 0.05). Fig. 2 shows the "
    "effect in vivo, i.e. in mice. The U.S. cohort was smaller.",
    "Background: Salmonella enterica serovar Typhimurium causes gastroenteritis. "
    "Methods. We sequenced 1,024 isolates; 12 carried blaCTX-M-15! Results were "
    "consistent with ref. [3]. Conclusions? Resistance is spreading.",
    "Table 1 gene count length 12 34 56 78 90 abc def ghi jkl mno pqr\n"
    "row two 1 2 3 4 5 6 7 8 9 10\n\nA new paragraph starts here. It ends here.",
)


def _sha256_lines(lines: Sequence[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def span_fingerprint(
    spans_per_doc: Sequence[Sequence[tuple[int, int]]],
    *,
    kind: str,
    texts: Sequence[str] | None = None,
    producer: str | None = None,
) -> dict[str, Any]:
    """Fingerprint one coordinate system: per document, the ``(start, end)``
    character spans a segmenter produced. ``kind`` names what the spans are
    (``"sentences"`` from :func:`ragstack.ingestion.chunkers.sentence_spans`,
    ``"units"`` for structural units, ``"sections"`` …); ``producer`` names the
    function that made them.

    ``sha256`` hashes the offsets only, in document order — it is the
    coordinate system labels are keyed by. ``texts_sha256`` (when ``texts`` are
    given) hashes what was segmented, so two fingerprints are only comparable
    when that matches too. Equal ``sha256`` over equal texts means a label keyed
    by span index points at the same characters; unequal means it does not, and
    no translation between the two is defined.
    """
    lines = [f"{kind}\t{len(spans_per_doc)}"]
    n_spans = 0
    for spans in spans_per_doc:
        n_spans += len(spans)
        lines.append(",".join(f"{int(s)}:{int(e)}" for s, e in spans))
    out: dict[str, Any] = {
        "kind": kind,
        "producer": producer,
        "sha256": _sha256_lines(lines),
        "n_docs": len(spans_per_doc),
        "n_spans": n_spans,
        "texts_sha256": None if texts is None else _sha256_lines(
            [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]),
    }
    return out


def segmentation_fingerprint(
    texts: Sequence[str], *, sample: str = "run",
) -> dict[str, Any]:
    """:func:`span_fingerprint` of ``sentence_spans()`` over ``texts``, plus
    which backend segmented them (``punkt`` when ``nltk`` is importable, else
    the ``regex`` fallback — the two disagree, so an environment without
    ``nltk`` is a different segmentation even at the same commit) and the
    ``nltk`` version. ``sample`` says whose texts these are: ``"run"`` (the
    experiment's own documents) or ``"canonical"``
    (:data:`CANONICAL_SEGMENTATION_SAMPLE`)."""
    from ragstack.ingestion import chunkers

    spans = [chunkers.sentence_spans(t) for t in texts]
    fp = span_fingerprint(
        spans, kind="sentences", texts=texts,
        producer="ragstack.ingestion.chunkers.sentence_spans")
    try:
        import nltk  # noqa: F401 - the [chunking] extra; absent means regex
        nltk_version: str | None = str(getattr(nltk, "__version__", "")) or None
    except ImportError:
        nltk_version = None
    backend = "punkt" if chunkers._punkt_sentence_spans("A b. C d.") is not None else "regex"
    fp.update({"sample": sample, "backend": backend, "nltk": nltk_version})
    return fp


def _utc_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def experiment_provenance(
    *,
    texts: Sequence[str] | None = None,
    segmentations: Sequence[dict[str, Any]] | None = None,
    repo: str | os.PathLike[str] | None = None,
    release_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """The provenance block every experiment artifact embeds. JSON-serialisable;
    **never raises** — what cannot be determined is ``None`` and says why in
    ``warnings``.

    Keys:

    * ``version`` — the derived repo version (``v1.6.4`` / ``v1.6.4+a2be96f``,
      ADR-0010 decision 1), from the checkout (``source="git"``) or from the
      image's ``RELEASE`` (``source="image"``). ``None`` on a dirty tree (a
      dirty tree has no version) and when neither source exists.
    * ``describe`` — **provenance only**: the raw ``git describe --tags --match
      v* --long --dirty --always`` line, from
      :func:`ragstack.version.raw_describe_for_provenance`. Evidence of what
      git said; never a version, never parsed back into one.
    * ``commit`` — the full 40-hex sha (checkout ``HEAD``, or the image's).
    * ``dirty`` — tracked changes in the checkout (``None`` when unknown).
    * ``source`` — ``git`` | ``image`` | ``distribution``: where ``version``
      and ``commit`` came from. A checkout wins over an image when both exist.
    * ``in_image`` / ``image`` — whether ``/opt/ragstack/RELEASE`` exists, and
      its ``version``/``commit``/``build``/``build_date`` (``None`` outside a
      tools image).
    * ``distribution`` — the installed package's static version (says nothing
      about the commit; recorded so a ``distribution``-source record is not
      empty).
    * ``segmentation`` — span fingerprints: ``sentence_spans()`` over ``texts``
      when given, else over :data:`CANONICAL_SEGMENTATION_SAMPLE`, followed by
      any precomputed ``segmentations`` (e.g. a ``kind="units"``
      :func:`span_fingerprint` for a unit-bounded arm).
    * ``python``, ``host``, ``recorded_at`` (UTC, RFC 3339 ``Z``).
    * ``citable`` — the commit is known and the tree was clean. A run that is
      not citable may be reported, never cited as a result.
    * ``warnings`` — each starts with :data:`WARN_DIRTY`,
      :data:`WARN_NOT_IN_IMAGE`, :data:`WARN_NO_GIT` or ``provenance:``.

    ``repo`` defaults to the checkout this package was imported from and must be
    a working-tree top level; ``release_path`` defaults to :data:`RELEASE_PATH`.
    """
    warnings: list[str] = []
    rec: dict[str, Any] = {
        "schema": EXPERIMENT_PROVENANCE_SCHEMA,
        "version": None, "describe": None, "commit": None, "dirty": None,
        "source": "distribution", "in_image": False, "image": None,
        "distribution": None, "segmentation": [],
        "python": platform.python_version(), "host": None,
        "recorded_at": _utc_now(), "citable": False, "warnings": warnings,
    }
    try:
        rec["host"] = socket.gethostname() or None
    except Exception:  # noqa: BLE001 - never raise over metadata
        pass

    # The image, if this process runs in one.
    try:
        release = read_release(RELEASE_PATH if release_path is None else release_path)
        if release:
            rec["in_image"] = True
            rec["image"] = {k: _nullable(release.get(k))
                            for k in ("version", "commit", "build", "build_date")}
        else:
            warnings.append(f"{WARN_NOT_IN_IMAGE}: no {release_path or RELEASE_PATH}; "
                            "a versioned ragstack-tools image is the preferred way to run")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"provenance: reading RELEASE failed: {e}")

    # The checkout.
    try:
        from ragstack import version as v

        root = None if repo is None else Path(repo)
        raw = v.raw_describe_for_provenance(root)
        if raw is None:
            warnings.append(f"{WARN_NO_GIT}: git missing, or "
                            f"{root if root is not None else 'this package'} is not "
                            "the top level of a working tree")
        else:
            rec["describe"] = raw
            rec["source"] = "git"
            d = v.parse_describe(raw)
            try:
                rec["commit"] = v.commit_sha(root if root is not None else v._CHECKOUT)
            except v.VersionError as e:
                warnings.append(f"provenance: commit unknown: {e}")
            if d is not None:
                rec["dirty"] = d.dirty
                if d.dirty:
                    warnings.append(f"{WARN_DIRTY}: uncommitted tracked changes; the "
                                    "commit is a base, not the code that ran — not citable")
                else:
                    try:
                        rec["version"] = d.version
                    except v.VersionError as e:
                        warnings.append(f"provenance: no version: {e}")
            else:
                warnings.append(f"provenance: describe output not understood: {raw!r}")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"provenance: git lookup failed: {e}")

    # No checkout: the image answers.
    if rec["source"] != "git" and rec["image"] is not None:
        rec["source"] = "image"
        rec["version"] = rec["image"]["version"]
        rec["commit"] = rec["image"]["commit"]
        rec["dirty"] = False  # the build refuses a dirty tree and ships `git archive HEAD`
    elif (rec["source"] == "git" and rec["image"] is not None
          and rec["image"]["commit"] and rec["commit"]
          and rec["image"]["commit"] != rec["commit"]):
        warnings.append(f"provenance: checkout commit {rec['commit']} differs from the "
                        f"image's {rec['image']['commit']}; the checkout is recorded")

    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as dist_version

        try:
            rec["distribution"] = dist_version("ragstack")
        except PackageNotFoundError:
            pass
    except Exception:  # noqa: BLE001
        pass

    try:
        if texts is not None:
            rec["segmentation"].append(segmentation_fingerprint(list(texts), sample="run"))
        else:
            rec["segmentation"].append(segmentation_fingerprint(
                CANONICAL_SEGMENTATION_SAMPLE, sample="canonical"))
    except Exception as e:  # noqa: BLE001
        warnings.append(f"provenance: sentence fingerprint failed: {e}")
    for fp in segmentations or ():
        if isinstance(fp, dict):
            rec["segmentation"].append(dict(fp))

    rec["citable"] = bool(rec["commit"]) and rec["dirty"] is False
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m ragstack.provenance",
        description="Print a provenance record as JSON.",
    )
    ap.add_argument("--experiment", action="store_true", required=True,
                    help="the experiment-provenance block (the only record printed today)")
    ap.add_argument("--repo", type=Path, default=None,
                    help="working-tree top level (default: this package's checkout)")
    ap.add_argument("--release-path", default=None,
                    help=f"the image RELEASE file (default: {RELEASE_PATH})")
    ap.add_argument("--texts", nargs="*", type=Path, default=None, metavar="FILE",
                    help="fingerprint sentence_spans() over these files (one document "
                         "each) instead of the canonical sample")
    args = ap.parse_args(argv)
    texts = None
    if args.texts:
        texts = [p.read_text(encoding="utf-8") for p in args.texts]
    rec = experiment_provenance(texts=texts, repo=args.repo, release_path=args.release_path)
    json.dump(rec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess in tests
    sys.exit(main())
