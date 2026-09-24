"""Distribution backends for sharded ingestion.

The ``IngestBackend`` seam decouples *what* runs (a shard of work items) from
*where* it runs. ``LocalAsyncIORunner`` is the single-host implementation —
bounded asyncio concurrency, no broker. A Parsl / GoWe / k8s runner can
implement the same protocol later (one task = one shard) without touching the
pipeline. This is the seam the "single host now, cluster later" decision rests on.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ragstack.ingestion.manifest import ItemResult, WorkItem
from ragstack.jobstore import FAILED

if TYPE_CHECKING:
    import httpx

    from ragstack.config import Settings

log = logging.getLogger(__name__)

# A shard processor: given a shard (list of work items), return one result each.
ShardFn = Callable[[list[WorkItem]], Awaitable[list[ItemResult]]]


def partition(items: list[WorkItem], shard_size: int) -> list[list[WorkItem]]:
    """Split items into shards of at most ``shard_size``."""
    if shard_size < 1:
        raise ValueError("shard_size must be >= 1")
    return [items[i : i + shard_size] for i in range(0, len(items), shard_size)]


@runtime_checkable
class IngestBackend(Protocol):
    """Run shards of work, returning a flat list of per-item results."""

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn: ShardFn
    ) -> list[ItemResult]: ...


class LocalAsyncIORunner:
    """Single-host backend: run shards concurrently under a semaphore.

    Concurrency is bounded so a large run can't open unbounded in-flight work
    (e.g. thousands of simultaneous embed requests). A shard whose processor
    raises wholesale is not fatal: its items are recorded as failed and the run
    continues.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        self._max = max(1, max_concurrency)

    async def run_shards(
        self, shards: list[list[WorkItem]], shard_fn: ShardFn
    ) -> list[ItemResult]:
        sem = asyncio.Semaphore(self._max)

        async def _one(shard: list[WorkItem]) -> list[ItemResult]:
            async with sem:
                return await shard_fn(shard)

        gathered = await asyncio.gather(
            *(_one(s) for s in shards), return_exceptions=True
        )
        out: list[ItemResult] = []
        for shard, res in zip(shards, gathered, strict=True):
            if isinstance(res, BaseException):
                out.extend(
                    ItemResult(
                        item_id=i.item_id,
                        source=i.source,
                        status=FAILED,
                        error=type(res).__name__,
                    )
                    for i in shard
                )
            else:
                out.extend(res)
        return out


def ingest_backend_name(settings: Settings) -> str:
    """The configured ingest backend, normalised — ``"local"`` when unset.

    The one normalisation. It was inlined here and again in the documents router,
    and #609 needed a third caller (the collections router's create-time guard);
    three copies of ``or "local"`` / ``strip().lower()`` is how a deployment with
    ``INGEST_BACKEND=" GoWe "`` ends up guarded on one path and not the others.
    """
    return (getattr(settings, "ingest_backend", None) or "local").strip().lower()


def make_ingest_backend(
    settings: Settings, *, http: httpx.AsyncClient | None = None
) -> IngestBackend:
    """Build the configured ``IngestBackend`` (the composition-root seam).

    ``ingest_backend="local"`` → :class:`LocalAsyncIORunner` (in-process, the
    default). ``"gowe"`` → a :class:`~ragstack.ingestion.gowe_backend.GoWeBackend`
    that submits each run's shards to the GoWe CWL engine. The GoWe stack is
    imported lazily so the default local path never pulls in the httpx workflow
    client. Raises ``ValueError`` with an actionable message on an unknown backend
    or missing/invalid GoWe config, so a misconfiguration fails fast at startup
    rather than on the first ingest.
    """
    backend = ingest_backend_name(settings)
    if backend == "local":
        return LocalAsyncIORunner(max_concurrency=settings.ingest_concurrency)
    if backend == "gowe":
        return _make_gowe_backend(settings, http)
    raise ValueError(
        f"unknown ingest_backend {settings.ingest_backend!r} (use 'local' or 'gowe')"
    )


# --- per-tenant tool image (#614) ------------------------------------------- #

#: The tool image every shipped CWL names in its ``DockerRequirement``. GoWe joins
#: the bare name onto the worker's ``--image-dir``, so without substitution the
#: image is a property of the worker GROUP, not of the tenant or the submission.
DEFAULT_TOOL_IMAGE = "ragstack-worker.sif"

# A value of `DockerRequirement`: the key at the START of a (non-comment) line,
# the default name as the whole value — optionally quoted, optionally followed by
# a trailing comment. Anchored on the key so a comment or prose that merely
# MENTIONS the name is left alone. `dockerImageId` is rewritten alongside
# `dockerPull` (GoWe reads only the latter, cwltool --singularity only the
# former) so the registered document never names two different images.
_TOOL_IMAGE_RE = re.compile(
    r"^(?P<key>[ \t]*(?P<field>dockerPull|dockerImageId):[ \t]*(?P<q>[\"']?))"
    + re.escape(DEFAULT_TOOL_IMAGE)
    + r"(?=(?P=q)[ \t]*(?:#.*)?\r?$)",
    re.MULTILINE,
)

# A bare image FILENAME: GoWe joins it onto `--image-dir`, so a separator (or a
# leading dot, for `..`) would resolve outside that directory.
_TOOL_IMAGE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.sif")


def validate_tool_image(name: str) -> str:
    """Return ``name`` stripped, or raise ``ValueError`` naming ``GOWE_TOOL_IMAGE``.

    Empty is valid (= no substitution). Anything else must be a bare filename
    ending in ``.sif``: no ``/`` or ``\\``, no leading dot — a path would escape
    the worker's ``--image-dir``, and a non-``.sif`` name is not what the
    apptainer runtime resolves.
    """
    value = (name or "").strip()
    if value and (
        not _TOOL_IMAGE_NAME_RE.fullmatch(value)
        # NAME_MAX: a longer name cannot exist as a file in any image dir.
        or len(value.encode("utf-8")) > 255
    ):
        raise ValueError(
            f"GOWE_TOOL_IMAGE={value!r} is not a bare image filename: it must end in "
            "'.sif' and contain no path separator or leading dot (the engine joins it "
            "onto the worker's --image-dir, so a path would escape it) and be at most "
            "255 bytes. "
            f"Example: {DEFAULT_TOOL_IMAGE.replace('.sif', '-v1.6.3.sif')}"
        )
    return value


class ToolImageError(ValueError):
    """The pin could not be applied to every image site of a CWL document."""


# The default name as a whole token (not a prefix of `ragstack-worker.sif.bak`).
_DEFAULT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9._-])" + re.escape(DEFAULT_TOOL_IMAGE) + r"(?![A-Za-z0-9._-])"
)


def _residual_image_sites(cwl: str) -> list[int]:
    """1-based line numbers where the default image is still named as an image.

    A site the substitution regex cannot see — a flow mapping
    (``{dockerPull: ragstack-worker.sif}``), a list item (``- dockerPull: …``),
    ``dockerPull :``, a differently-cased key, or the value on the next line —
    would run the unpinned image on that one step. Comment lines are skipped, and
    so is prose that merely mentions the name: a line counts only if it also
    names a docker key or is the bare value alone.
    """
    sites = []
    for n, line in enumerate(cwl.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        code = re.split(r"[ \t]#", line, maxsplit=1)[0]
        if not _DEFAULT_TOKEN_RE.search(code):
            continue
        bare = code.strip().lstrip("-").strip().strip("\"'")
        if "docker" in code.lower() or bare == DEFAULT_TOOL_IMAGE:
            sites.append(n)
    return sites


def substitute_tool_image(cwl: str, tool_image: str, *, source: str = "workflow") -> str:
    """Replace the default tool image in a CWL document's ``DockerRequirement``.

    Empty ``tool_image`` (or the default name itself) returns ``cwl`` unchanged,
    byte for byte. Otherwise every ``dockerPull: ragstack-worker.sif`` (and its
    ``dockerImageId`` twin) is rewritten; nothing else in the text is touched.

    Two failure shapes, handled differently:

    * **Nothing to substitute** (no site names the default at all) logs a
      WARNING: the pin does nothing, but the document is self-consistent — every
      step runs what the CWL names.
    * **A half-substituted document** — some sites rewritten, one the regex
      cannot see left naming the default — RAISES :class:`ToolImageError` naming
      each residual line. A pinned tenant would otherwise run one step on the
      wrong image with nothing in the logs a reader would connect to it; a warn
      scrolls past at boot, while a refusal is fixed once, before any traffic.
      The ingest backend is built at boot, so it fails the boot there.
    """
    image = (tool_image or "").strip()
    if not image or image == DEFAULT_TOOL_IMAGE:
        return cwl
    pulls = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal pulls
        if m.group("field") == "dockerPull":
            pulls += 1
        return m.group("key") + image

    out = _TOOL_IMAGE_RE.sub(_sub, cwl)
    residual = _residual_image_sites(out)
    if residual:
        raise ToolImageError(
            f"GOWE_TOOL_IMAGE={image} could not be applied to all of {source}: "
            f"line(s) {', '.join(map(str, residual))} still name {DEFAULT_TOOL_IMAGE} "
            "in a form the substitution does not rewrite (flow mapping, list item, "
            "'dockerPull :', key case, or value on the next line). Those steps would "
            f"run the unpinned image. Write them as 'dockerPull: {DEFAULT_TOOL_IMAGE}' "
            "on one line, or unset GOWE_TOOL_IMAGE."
        )
    if pulls == 0:
        log.warning(
            "GOWE_TOOL_IMAGE=%s substituted nothing: %s has no 'dockerPull: %s' line, "
            "so its steps run whatever image that CWL names, not the pinned one",
            image, source, DEFAULT_TOOL_IMAGE,
        )
    else:
        log.info("GOWE_TOOL_IMAGE: %s pinned to %s (%d dockerPull site(s))",
                 source, image, pulls)
    return out


# Workflow inputs the API owns and seeds per run (#407). An operator setting
# these in GOWE_WORKFLOW_INPUTS_JSON is refused at boot — see _make_gowe_backend.
_RESERVED_STATIC_INPUTS = frozenset({"qdrant_url", "es_url"})


def _make_gowe_backend(
    settings: Settings, http: httpx.AsyncClient | None
) -> IngestBackend:
    import json
    from pathlib import Path

    # Lazy: keep the httpx/workflow client out of the default local path.
    from ragstack.ingestion.gowe_backend import GoWeBackend
    from ragstack.ingestion.gowe_client import GoWeClient
    from ragstack.workspace import WorkspaceClient

    if not settings.gowe_workflow_cwl:
        raise ValueError(
            "ingest_backend=gowe requires gowe_workflow_cwl (path to the scatter CWL)"
        )
    # Resolve to an absolute path so a relative value doesn't silently depend on
    # the process CWD (which differs between `make run-python` and the deployed
    # unit); the error names the resolved path so a miss is diagnosable.
    cwl_path = Path(settings.gowe_workflow_cwl).expanduser().resolve()
    try:
        cwl = cwl_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"gowe_workflow_cwl {str(cwl_path)!r} is unreadable: {e}") from e
    # #614: pin this tenant's tool image. The text is POSTed verbatim at every
    # submission and every tool is inlined (no external `run:` file), so this one
    # substitution covers every dockerPull that reaches the engine.
    # Validated here too, so a caller that builds the backend without the boot
    # check still cannot register a path-shaped image name.
    tool_image = validate_tool_image(getattr(settings, "gowe_tool_image", "") or "")
    cwl = substitute_tool_image(cwl, tool_image, source=str(cwl_path))
    try:
        static_inputs = json.loads(settings.gowe_workflow_inputs_json or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"gowe_workflow_inputs_json is not valid JSON: {e}") from e
    if not isinstance(static_inputs, dict):
        raise ValueError("gowe_workflow_inputs_json must be a JSON object")
    # #407: store targets are seeded per run by the ingest router, from this
    # API's own settings, and a per-run input wins the merge in GoWeBackend.run
    # — so these keys here would be silently INERT. Refuse at boot rather than
    # let an operator believe a blob that does nothing is steering their writes:
    # inert config believed live is exactly the defect this issue is.
    reserved = sorted(k for k in _RESERVED_STATIC_INPUTS if k in static_inputs)
    if reserved:
        raise ValueError(
            f"gowe_workflow_inputs_json may not set {', '.join(reserved)}: ingest store "
            "targets are seeded per run from the QDRANT_URL / ELASTICSEARCH_URL settings "
            "(and QDRANT_COLLECTION_ROUTES / ES_COLLECTION_ROUTES) and would override "
            "these keys silently. "
            "Remove them from GOWE_WORKFLOW_INPUTS_JSON and set those settings instead; "
            "the blob remains for genuine per-deployment extras."
        )

    client = GoWeClient(
        base_url=settings.gowe_url, token=settings.gowe_token or None, http=http
    )
    # The user path reads each run's receipts from the archive in the caller's
    # Workspace; share the engine client's HTTP pool. Holds no token.
    workspace = WorkspaceClient(
        getattr(settings, "workspace_url", "") or "https://p3.theseed.org/services/Workspace",
        client.http,
        timeout=float(getattr(settings, "workspace_timeout", 60.0) or 60.0),
    )
    # The scattered input / receipts output names come from settings (#203
    # blocker b): the PDF workflow scatters over ``pdfs``, the JSONL bulk one over
    # ``shards``. Blank → the GoWeBackend defaults, so an older settings object
    # (tests build a SimpleNamespace) keeps the bulk-workflow behaviour.
    shards_key = (getattr(settings, "gowe_shards_input_key", "") or "").strip()
    receipts_key = (getattr(settings, "gowe_receipts_output_key", "") or "").strip()
    return GoWeBackend(
        client,
        cwl,
        workflow_name=settings.gowe_workflow_name,
        static_inputs=static_inputs,
        shards_input_key=shards_key or "shards",
        receipts_output_key=receipts_key or "receipts",
        worker_group=settings.gowe_worker_group or None,
        poll_interval=settings.gowe_poll_interval,
        timeout=settings.gowe_timeout,
        output_wait_timeout=float(getattr(settings, "gowe_output_wait_timeout", 600.0) or 600.0),
        workspace=workspace,
    )
