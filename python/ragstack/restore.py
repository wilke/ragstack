"""Restore a dormant collection from its Workspace archive (#358, phase 2 of #353).

A collection whose physical stores were evicted is ``dormant``: only its
archive — ``versions/<n>/`` under the OWNER's Workspace, one directory per
completed ingest or delete (:mod:`ragstack.ingestion.archive`) — still exists.
Restoring is a GoWe submission **as the user**: the caller's bearer token
authenticates the submission, the engine pre-stages every ``ws://`` version
directory with it (server-side staging — no task ever sees the token), and the
``restore-collection`` workflow runs the loader in replay mode
(``load_embeddings.py --replay``), which verifies every version (sha256 +
``spec_hash == registry row``) BEFORE writing anything, then replays them in
order — chunk versions upsert both legs, tombstones delete by doc id.

This module owns the API side of that: list the versions, submit, and watch
the submission to its terminal state, flipping the registry row
``restoring → active`` on COMPLETED, back to ``dormant`` (error recorded) on an
engine failure, and to ``lost`` (reason recorded) when the loader refused the
archive (``ArchiveCorrupt`` / ``SpecMismatch``). The state transitions are all
compare-and-swap from ``restoring`` so a watcher can never clobber a transition
made elsewhere (an operator, a sibling process's watchdog).

The token is held only in the submitting coroutine's frame and the GoWe
client's ``Authorization`` header — never on the registry row, never in a log
line, never in the reason strings written here (:func:`_scrub`).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragstack.collection_store import (
    ACTIVE,
    DORMANT,
    LOST,
    RESTORING,
    CollectionRecord,
    CollectionStore,
)
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.workspace import (
    WorkspaceAuthError,
    WorkspaceError,
    WorkspaceNotFound,
    collection_folder,
)

log = logging.getLogger(__name__)

#: Markers the loader prints (and raises) when it REFUSES an archive: a sha256 /
#: geometry failure or a build-spec hash that disagrees with the registry row.
#: A FAILED submission whose record carries one of these is a ``lost``
#: collection (the archive is bad), not a retryable engine failure.
FAILURE_MARKERS = ("ArchiveCorrupt", "SpecMismatch")

#: The workflow input that carries the version directories (restore-collection.cwl).
VERSIONS_INPUT = "versions"

#: Where the repo keeps the restore workflow (used when the setting is empty).
DEFAULT_CWL = Path(__file__).resolve().parents[2] / "cwl" / "restore-collection.cwl"


class RestoreError(RuntimeError):
    """A restore could not be submitted (or was refused). ``state`` is the
    lifecycle state the collection should be left in: ``lost`` when the archive
    itself is the problem (missing folder, no versions), ``dormant`` when the
    engine/Workspace call failed and a later attempt may succeed."""

    def __init__(self, message: str, *, state: str = DORMANT) -> None:
        super().__init__(message)
        self.state = state


def workspace_subject(owner: str) -> str:
    """The Workspace username behind a registry ``owner`` (``issuer:subject`` →
    ``subject``; a colon-free value is returned as-is). ``''`` when unknown."""
    if not owner:
        return ""
    _issuer, sep, subject = owner.partition(":")
    return subject if sep else owner


def classify_failure(record: dict[str, Any]) -> tuple[str, str]:
    """Decide what a non-COMPLETED terminal submission record means.

    The record is searched (as JSON text — GoWe's failure fields are not
    pinned here) for the loader's refusal markers; one present means the
    archive failed verification and the collection is ``lost`` with that
    reason. Anything else is an engine-side failure: ``dormant`` with the
    submission state recorded, so the next access tries again."""
    text = json.dumps(record, default=str)
    for marker in FAILURE_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            snippet = text[idx: idx + 200].split("\\n", 1)[0].rstrip('"}] ')
            return LOST, f"archive refused by the loader: {snippet}"
    state = record.get("state") or "FAILED"
    err = record.get("error") or record.get("message") or ""
    detail = f": {str(err)[:200]}" if err else ""
    return DORMANT, f"restore submission {record.get('id', '?')} ended {state}{detail}"


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "[token]") if token else text


class CollectionRestorer:
    """Submit and watch ``restore-collection`` runs.

    ``gowe_factory(token)`` builds a GoWe client authenticated as the caller
    (the app's shared http client underneath); ``workspace`` lists the owner's
    versions with the same token. ``on_change(cid)`` is the lifecycle gate's
    cache invalidation, called after every state write here.
    """

    def __init__(
        self,
        store: CollectionStore,
        *,
        workspace: Any,
        gowe_factory: Callable[[str], Any],
        cwl_path: str | Path = "",
        workflow_name: str = "ragstack-restore-collection",
        static_inputs: dict[str, Any] | None = None,
        worker_group: str = "",
        poll_interval: float = 5.0,
        timeout: float = 3600.0,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._gowe_factory = gowe_factory
        self._cwl_path = Path(cwl_path) if cwl_path else DEFAULT_CWL
        self._cwl_text: str | None = None
        self.workflow_name = workflow_name
        self.static_inputs = dict(static_inputs or {})
        self.worker_group = (worker_group or "").strip()
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._on_change = on_change
        # Strong refs to in-flight watchers (a bare fire-and-forget task is
        # GC-able mid-flight, and a dropped one leaves the row `restoring`).
        self._watchers: set[asyncio.Task] = set()
        self.submissions: list[dict[str, Any]] = []  # what was submitted (tests)

    # -- helpers ------------------------------------------------------------ #

    def _cwl(self) -> str:
        if self._cwl_text is None:
            try:
                self._cwl_text = self._cwl_path.read_text(encoding="utf-8")
            except OSError as e:
                raise RestoreError(
                    f"restore workflow {self._cwl_path} is not readable: {e}"
                ) from e
        return self._cwl_text

    async def _set(self, cid: str, state: str, reason: str) -> bool:
        """CAS ``restoring → state`` (never clobbers a transition made elsewhere)."""
        moved = await self._store.set_state(cid, state, expect=RESTORING, reason=reason)
        if self._on_change is not None:
            self._on_change(cid)
        if not moved:
            log.info("restore %r: row was no longer `restoring`; leaving it alone "
                     "(wanted %s: %s)", cid, state, reason)
        return moved

    # -- the public surface ------------------------------------------------- #

    def inputs_for(self, rec: CollectionRecord, versions: list[tuple[int, str]]) -> dict[str, Any]:
        """The workflow's inputs object: every version directory as a
        ``ws://`` Directory (pre-staged by the engine), the registry identity
        the loader verifies against, and the store settings."""
        return {
            **self.static_inputs,
            VERSIONS_INPUT: [{"class": "Directory", "location": uri} for _n, uri in versions],
            "collection_id": rec.spec.id,
            "spec_hash": rec.spec_hash,
        }

    async def submit(self, rec: CollectionRecord, token: str) -> str:
        """List the owner's versions and submit the restore AS THE CALLER.

        The row must already be ``restoring`` (the caller won the CAS). On any
        failure the row is moved to the state the failure implies
        (:class:`RestoreError.state`) with the reason recorded, and the error
        is re-raised for the caller to map (503/502). On success the watcher
        is started and the submission id returned."""
        cid = rec.spec.id
        try:
            sub_id = await self._submit(rec, token)
        except RestoreError as e:
            await self._set(cid, e.state, _scrub(str(e), token))
            raise
        except Exception as e:  # noqa: BLE001 — anything else is an engine-side failure
            reason = _scrub(f"restore submission failed: {type(e).__name__}: {e}", token)
            await self._set(cid, DORMANT, reason)
            raise RestoreError(reason) from e
        self.watch(cid, sub_id, token)
        return sub_id

    async def _submit(self, rec: CollectionRecord, token: str) -> str:
        cid = rec.spec.id
        subject = workspace_subject(rec.spec.owner)
        if not subject:
            raise RestoreError(
                f"collection {cid!r} records no owner subject; its Workspace "
                "archive cannot be located", state=DORMANT,
            )
        folder = collection_folder(subject, cid)
        try:
            versions = await self._workspace.list_versions(token, folder)
        except WorkspaceNotFound as e:
            raise RestoreError(
                f"archive folder missing in the owner's Workspace ({folder}/versions): {e}",
                state=LOST,
            ) from e
        except WorkspaceAuthError as e:
            raise RestoreError(
                f"the caller's token cannot read the owner's archive ({folder}): {e}",
                state=DORMANT,
            ) from e
        except WorkspaceError as e:
            raise RestoreError(f"Workspace listing failed: {e}", state=DORMANT) from e
        if not versions:
            raise RestoreError(
                f"archive has no versions in the owner's Workspace ({folder}/versions)",
                state=LOST,
            )
        # The registry's ordered list is what restore is documented to replay;
        # the Workspace is what actually exists. Replay what exists, in numeric
        # order, and say so when the two disagree (a user may delete a version).
        recorded = list(rec.versions)
        present = [n for n, _ in versions]
        if recorded and present != recorded:
            log.warning("restore %r: Workspace versions %s != registry versions %s; "
                        "replaying what the Workspace holds", cid, present, recorded)
        inputs = self.inputs_for(rec, versions)
        client = self._gowe_factory(token)
        try:
            wf_id = await client.register_workflow(self.workflow_name, self._cwl())
            labels = {"worker_group": self.worker_group} if self.worker_group else None
            sub = await client.submit(wf_id, inputs, labels=labels)
        except GoWeError as e:
            raise RestoreError(
                f"workflow engine refused the restore submission: {e}", state=DORMANT
            ) from e
        finally:
            await _close(client)
        sub_id = str(sub.get("id") or "")
        if not sub_id:
            raise RestoreError("workflow engine returned no submission id", state=DORMANT)
        self.submissions.append({"collection_id": cid, "submission_id": sub_id,
                                 "versions": present})
        log.info("restore %r: submitted %s over %d version(s) %s", cid, sub_id,
                 len(present), present)
        return sub_id

    def watch(self, cid: str, sub_id: str, token: str) -> asyncio.Task:
        """Poll ``sub_id`` to its terminal state in the background and flip the
        row. Strongly referenced until done; :meth:`drain` awaits them all."""
        task = asyncio.get_running_loop().create_task(self._watch(cid, sub_id, token))
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return task

    async def _watch(self, cid: str, sub_id: str, token: str) -> None:
        client = self._gowe_factory(token)
        try:
            final = await client.wait(
                sub_id, poll_interval=self.poll_interval, timeout=self.timeout
            )
        except GoWeError as e:
            await self._set(cid, DORMANT, _scrub(f"restore {sub_id}: {e}", token))
            return
        except Exception as e:  # noqa: BLE001 — a watcher must never die silently
            await self._set(cid, DORMANT,
                            _scrub(f"restore {sub_id}: watcher failed: {type(e).__name__}: {e}", token))
            return
        finally:
            await _close(client)
        if final.get("state") == "COMPLETED":
            if await self._set(cid, ACTIVE, ""):
                log.info("restore %r: %s COMPLETED; collection is active", cid, sub_id)
            return
        state, reason = classify_failure(final)
        await self._set(cid, state, _scrub(reason, token))
        log.warning("restore %r: %s -> %s (%s)", cid, sub_id, state, reason)

    @property
    def pending(self) -> int:
        return len(self._watchers)

    async def drain(self) -> None:
        """Await every in-flight watcher (shutdown, tests)."""
        while self._watchers:
            await asyncio.gather(*list(self._watchers), return_exceptions=True)


async def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001 — closing a per-call client is best-effort
        log.debug("restore: gowe client close failed", exc_info=True)
