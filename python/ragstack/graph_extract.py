"""Submit and watch ``graph-extract`` runs (#350, phase 6 of #201) — the API
side of the extract-graph step, the :mod:`ragstack.restore` shape.

``POST /v1/collections/{id}/graph`` picks ONE archived chunk version of the
collection (the latest, or ``?version=n``), submits ``cwl/graph-extract.cwl``
AS THE USER (the caller's bearer token authenticates the submission; the
engine pre-stages the ``ws://`` version directory with it and post-stages the
workflow's only output — the version-named delta Directory — back onto
``versions/<n>/`` with it), and watches the submission to its terminal state:

* COMPLETED **and delivered** (``output_state == delivered`` — the engine
  finalizes and post-stages in one tick, so COMPLETED alone is not done) →
  the job completes with ``archive_ref`` and the registry row records the
  version in ``graph_archived_versions`` — the flag a later PR gates
  eviction's graph drop on (#380: eviction may only destroy what exists
  somewhere else);
* ``upload_failed`` → the job fails with ``OUTPUT_STAGING_FAILED`` (the
  triples were loaded, the leg is not archived: NOT recorded). The engine
  uploads a Directory's listing in filename order — ``manifest.json`` BEFORE
  ``triples.jsonl.gz`` — so a failure between the two leaves the Workspace
  with a manifest that says ``graph: true`` and no triples file (a half-
  applied delivery); and an engine crash mid-upload leaves the submission's
  ``output_state`` at ``uploading`` forever (the post-stager skips it), which
  the delivery-timeout turns into a failed job. Both recover the same way:
  :meth:`GraphExtractRunner.choose_version` never trusts ``graph: true`` on
  its own — it ``stat``s the triples file (present, size == the manifest's
  ``bytes``) before calling a version extracted, so the next ``POST`` simply
  resubmits and the extract tool overwrites the stale entries;
* FAILED with the load tool's exit 4 → ``graph_cap_exceeded`` (nothing loaded);
* any other terminal state → the job fails with the state named.

The token lives only in the submitting request's frame and the watcher's; it
is never on the job, the registry row, or a log line (:func:`_scrub`).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragstack.graph.budget import GRAPH_CAP_EXCEEDED, graph_cap_refusal_of
from ragstack.ingestion.gowe_backend import OUTPUT_STAGING_FAILED, _staging_failed
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.jobstore import COMPLETED, FAILED, RUNNING, JobStore
from ragstack.restore import workspace_subject
from ragstack.workspace import (
    WorkspaceAuthError,
    WorkspaceError,
    WorkspaceNotFound,
    collection_folder,
    ws_path,
    ws_uri,
)

log = logging.getLogger(__name__)

#: Where the repo keeps the workflow (used when the setting is empty).
DEFAULT_CWL = Path(__file__).resolve().parents[2] / "cwl" / "graph-extract.cwl"
#: The workflow input carrying the version Directory / its number.
VERSION_DIR_INPUT = "version_dir"
VERSION_INPUT = "version"


class GraphExtractError(RuntimeError):
    """The extraction could not be submitted. ``status`` is the HTTP status the
    endpoint maps it to (400 = the archive / the request; 502 = the engine or
    the Workspace)."""

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "[token]") if token else text


class ChosenVersion:
    """What :meth:`GraphExtractRunner.choose_version` found."""

    __slots__ = ("leg_delivered", "manifest", "number", "uri")

    def __init__(
        self, number: int, uri: str, manifest: dict[str, Any], *, leg_delivered: bool = False
    ) -> None:
        self.number = number
        self.uri = uri
        self.manifest = manifest
        #: The manifest claims ``graph: true`` AND the triples file is really
        #: there with the recorded size — the only state that counts as done.
        self.leg_delivered = leg_delivered

    @property
    def already_extracted(self) -> bool:
        return self.leg_delivered


class GraphExtractRunner:
    """Choose a version, submit ``graph-extract`` as the caller, watch it.

    ``gowe`` is a tokenless :class:`GoWeClient` over the app's shared http
    client; every engine call passes the caller's token per call.
    ``workspace`` lists the owner's versions and reads a manifest with the
    same token. ``on_change(cid)`` is the lifecycle gate's cache invalidation.
    """

    def __init__(
        self,
        job_store: JobStore,
        collection_store: Any,
        *,
        workspace: Any,
        gowe: Any,
        cwl_path: str | Path = "",
        workflow_name: str = "ragstack-graph-extract",
        static_inputs: dict[str, Any] | None = None,
        worker_group: str = "",
        poll_interval: float = 5.0,
        timeout: float = 7200.0,
        output_wait_timeout: float = 600.0,
        concurrency: int = 8,
        max_triples: int = 0,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._jobs = job_store
        self._store = collection_store
        self._workspace = workspace
        self._gowe = gowe
        self._cwl_path = Path(cwl_path) if cwl_path else DEFAULT_CWL
        self._cwl_text: str | None = None
        self.workflow_name = workflow_name
        self.static_inputs = dict(static_inputs or {})
        self.worker_group = (worker_group or "").strip()
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.output_wait_timeout = output_wait_timeout
        self.concurrency = max(1, int(concurrency))
        self.max_triples = max(0, int(max_triples))
        self._on_change = on_change
        # Strong refs to in-flight watchers, keyed by job id (a bare
        # fire-and-forget task is GC-able mid-flight).
        self._watchers: dict[str, asyncio.Task] = {}
        self.submissions: list[dict[str, Any]] = []  # what was submitted (tests)

    # -- helpers ------------------------------------------------------------ #

    def _cwl(self) -> str:
        if self._cwl_text is None:
            try:
                self._cwl_text = self._cwl_path.read_text(encoding="utf-8")
            except OSError as e:
                raise GraphExtractError(
                    f"graph-extract workflow {self._cwl_path} is not readable: {e}", status=503,
                ) from e
        return self._cwl_text

    def _changed(self, cid: str) -> None:
        if self._on_change is not None:
            self._on_change(cid)

    @staticmethod
    def folder_for(rec: Any) -> str:
        """The OWNER's collection folder (no ``ws://``) — the archive lives
        there whoever calls; an admin caller's token must be able to read it
        and write ``versions/<n>/`` in it, which the Workspace decides."""
        subject = workspace_subject(rec.spec.owner)
        if not subject:
            raise GraphExtractError(
                f"collection {rec.spec.id!r} records no owner subject; its Workspace "
                "archive cannot be located", status=400,
            )
        return collection_folder(subject, rec.spec.id)

    # -- the public surface ------------------------------------------------- #

    async def choose_version(
        self, rec: Any, token: str, *, version: int | None = None
    ) -> ChosenVersion:
        """List the owner's archived versions and pick the one to extract from:
        ``version`` when given (it must exist), else the LATEST chunk version
        (tombstones skipped). Reads that version's ``manifest.json`` — one
        Workspace read — to refuse a tombstone and to learn whether the leg is
        already there (``graph: true`` → the caller's no-op)."""
        folder = self.folder_for(rec)
        try:
            versions = await self._workspace.list_versions(token, folder)
        except WorkspaceNotFound as e:
            raise GraphExtractError(
                f"archive folder missing in the owner's Workspace ({folder}/versions): {e}",
                status=400,
            ) from e
        except WorkspaceAuthError as e:
            raise GraphExtractError(
                f"the caller's token cannot read the owner's archive ({folder}): {e}",
                status=403,
            ) from e
        except WorkspaceError as e:
            raise GraphExtractError(f"Workspace listing failed: {e}") from e
        if not versions:
            raise GraphExtractError(
                f"archive has no versions in the owner's Workspace ({folder}/versions)",
                status=400,
            )
        by_number = dict(versions)
        if version is not None:
            if version not in by_number:
                raise GraphExtractError(
                    f"version {version} is not in the archive (present: "
                    f"{sorted(by_number)})", status=400,
                )
            candidates = [(version, by_number[version])]
        else:
            candidates = sorted(versions, key=lambda t: t[0], reverse=True)
        for number, uri in candidates:
            manifest = await self._manifest(token, uri)
            if manifest.get("has_tombstone"):
                if version is not None:
                    raise GraphExtractError(
                        f"version {number} is a tombstone (a delete); it has no chunks to "
                        "extract a graph from", status=400,
                    )
                continue
            delivered = await self._leg_delivered(token, uri, manifest)
            return ChosenVersion(number, uri, manifest, leg_delivered=delivered)
        raise GraphExtractError(
            "the archive holds no chunk version (only tombstones)", status=400,
        )

    async def _leg_delivered(self, token: str, uri: str, manifest: dict[str, Any]) -> bool:
        """Is the graph leg REALLY there? ``graph: true`` in the manifest is
        necessary, not sufficient (module docstring: the manifest is uploaded
        first, so a half-applied delivery says true with no file). One
        ``stat`` of the triples file: present, and — when the manifest records
        its size — that size. Anything else is "not extracted": resubmit."""
        if not manifest.get("graph"):
            return False
        raw_files = manifest.get("files")
        files: dict[str, Any] = raw_files if isinstance(raw_files, dict) else {}
        name = str(files.get("triples") or "")
        if not name:
            return False
        path = f"{ws_path(uri)}/{name}"
        try:
            st = await self._workspace.stat(token, path)
        except WorkspaceError as e:
            raise GraphExtractError(f"could not stat {path}: {e}") from e
        if not st.exists or st.is_folder:
            log.warning("graph-extract: %s says graph: true but %s is missing — "
                        "a half-applied delivery; treating the version as not extracted",
                        ws_path(uri), name)
            return False
        raw_sizes = manifest.get("bytes")
        sizes: dict[str, Any] = raw_sizes if isinstance(raw_sizes, dict) else {}
        want = sizes.get(name)
        if want is not None and int(want) != int(st.size):
            log.warning("graph-extract: %s: %s is %d bytes, manifest says %s — treating "
                        "the version as not extracted", ws_path(uri), name, st.size, want)
            return False
        return True

    async def _manifest(self, token: str, uri: str) -> dict[str, Any]:
        import json

        path = f"{ws_path(uri)}/manifest.json"
        try:
            raw = await self._workspace.read_file(token, path)
        except WorkspaceError as e:
            raise GraphExtractError(f"could not read {path}: {e}") from e
        try:
            manifest = json.loads(raw)
        except ValueError as e:
            raise GraphExtractError(f"{path} is not valid JSON: {e}", status=400) from e
        if not isinstance(manifest, dict):
            raise GraphExtractError(f"{path} is not a manifest object", status=400)
        return manifest

    def inputs_for(self, rec: Any, chosen: ChosenVersion, *, job_id: str) -> dict[str, Any]:
        """The workflow's inputs object: the version Directory as ``ws://``
        (pre-staged by the engine), its number (the output basename), the
        registry identity the tools verify against, the budgets, and the
        static worker-side settings."""
        return {
            **self.static_inputs,
            VERSION_DIR_INPUT: {"class": "Directory", "location": chosen.uri},
            VERSION_INPUT: str(chosen.number),
            "collection_id": rec.spec.id,
            "tenant": str(chosen.manifest.get("tenant") or rec.spec.owner or ""),
            "spec_hash": rec.spec_hash,
            "concurrency": self.concurrency,
            "max_triples": self.max_triples,
            "job_id": job_id,
        }

    async def submit(
        self, rec: Any, token: str, *, job_id: str, chosen: ChosenVersion
    ) -> str:
        """Register + submit AS THE CALLER with the owner's ``versions/`` folder
        as the output destination; start the watcher; return the submission
        id. On any failure the job is failed with a caller-safe label and
        :class:`GraphExtractError` is raised for the endpoint to map."""
        cid = rec.spec.id
        destination = ws_uri(f"{self.folder_for(rec)}/versions") + "/"
        inputs = self.inputs_for(rec, chosen, job_id=job_id)
        try:
            wf_id = await self._gowe.register_workflow(self.workflow_name, self._cwl(), token=token)
            labels = {"worker_group": self.worker_group} if self.worker_group else None
            sub = await self._gowe.submit(
                wf_id, inputs, labels=labels, output_destination=destination, token=token,
            )
        except GoWeError as e:
            reason = _scrub(f"workflow engine refused the graph-extract submission: {e}", token)
            await self._jobs.update(job_id, status=FAILED, error=type(e).__name__)
            raise GraphExtractError(reason) from e
        sub_id = str(sub.get("id") or "")
        if not sub_id:
            await self._jobs.update(job_id, status=FAILED, error="GoWeContractError")
            raise GraphExtractError("workflow engine returned no submission id")
        self.submissions.append({"collection_id": cid, "job_id": job_id, "version": chosen.number,
                                 "submission_id": sub_id, "output_destination": destination})
        await self._jobs.update(job_id, status=RUNNING)
        log.info("graph-extract %r v%d: submitted %s (job %s)", cid, chosen.number, sub_id, job_id)
        self.watch(job_id, cid, chosen.number, sub_id, token, destination)
        return sub_id

    def watch(self, job_id: str, cid: str, version: int, sub_id: str, token: str,
              destination: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(
            self._watch(job_id, cid, version, sub_id, token, destination)
        )
        self._watchers[job_id] = task

        def _forget(done: asyncio.Task) -> None:
            if self._watchers.get(job_id) is done:
                del self._watchers[job_id]

        task.add_done_callback(_forget)
        return task

    async def _watch(self, job_id: str, cid: str, version: int, sub_id: str, token: str,
                     destination: str) -> None:
        try:
            final = await self._gowe.wait(
                sub_id, poll_interval=self.poll_interval, timeout=self.timeout, token=token,
                require_delivery=True, delivery_timeout=self.output_wait_timeout,
            )
        except GoWeError as e:
            log.warning("graph-extract %r v%d: %s: %s", cid, version, sub_id,
                        _scrub(str(e), token))
            await self._jobs.update(job_id, status=FAILED, error=type(e).__name__)
            return
        except Exception as e:  # noqa: BLE001 — a watcher must never die silently
            log.warning("graph-extract %r v%d: watcher failed: %s", cid, version,
                        type(e).__name__)
            await self._jobs.update(job_id, status=FAILED, error=type(e).__name__)
            return
        state = str(final.get("state") or "")
        if _staging_failed(final):
            # Loaded into the graph store, but the leg never reached the
            # Workspace: NOT recorded on the row — eviction must keep treating
            # this collection's graph as unarchived.
            log.error("graph-extract %r v%d: %s could not deliver the leg (%s)",
                      cid, version, sub_id, final.get("output_state"))
            await self._jobs.update(job_id, status=FAILED, error=OUTPUT_STAGING_FAILED)
            return
        if state != "COMPLETED":
            refusal = graph_cap_refusal_of(final)
            if refusal is not None:
                log.warning("graph-extract %r v%d: refused at the graph cap: %s", cid, version,
                            refusal)
                await self._jobs.update(job_id, status=FAILED, error=GRAPH_CAP_EXCEEDED)
                return
            log.warning("graph-extract %r v%d: %s ended %s", cid, version, sub_id, state or "?")
            await self._jobs.update(job_id, status=FAILED,
                                    error=f"gowe submission {state or 'FAILED'}")
            return
        archive_ref = f"{destination.rstrip('/')}/{version}"
        try:
            await self._store.append_graph_version(cid, version)
        except Exception:  # noqa: BLE001 — the leg exists; the list is repairable
            log.warning("graph-extract %r: could not record version %d on the registry row",
                        cid, version, exc_info=True)
        self._changed(cid)
        await self._jobs.update(job_id, status=COMPLETED, archive_ref=archive_ref)
        log.info("graph-extract %r v%d: %s COMPLETED and delivered (%s)", cid, version, sub_id,
                 archive_ref)

    @property
    def pending(self) -> int:
        return len(self._watchers)

    async def drain(self) -> None:
        """Await every in-flight watcher (shutdown, tests)."""
        while self._watchers:
            await asyncio.gather(*list(self._watchers.values()), return_exceptions=True)
