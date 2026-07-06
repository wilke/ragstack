"""Runtime model registry (Phase 1).

Holds registered models and the hot-swappable task assignments (llm / reranker),
with JSON persistence and URL-allowlist (SSRF) validation. This module is pure
config state — constructing the actual clients and swapping them into app.state
lives in ``ragstack.api.deps.apply_assignment`` (it needs the shared http client
and the concrete client classes).

Build-time tasks (embedding, tokenizer) can be *registered* here for later phases
but are not assignable at runtime — changing them means building a new collection,
not mutating a running one.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Every task a registered model may serve.
TASKS = {"embedding", "tokenizer", "llm", "reranker"}
# The subset that can be reassigned live (the client is an app.state singleton,
# independent of how any corpus was indexed).
HOT_SWAPPABLE = {"llm", "reranker"}
PROVIDERS = {"sidecar", "openai", "vllm"}


class ModelEntry(BaseModel):
    """A registered model: which task it serves and how to reach it."""

    id: str
    task: str
    provider: str = "openai"
    base_urls: list[str] = Field(default_factory=list)
    model: str = ""
    dim: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class RegistryError(ValueError):
    """Invalid registry operation (bad task/provider, unknown id, SSRF, dup, in-use)."""


class ModelRegistry:
    """In-memory registry with file persistence. Not thread-safe; the API serves
    it from a single event loop and writes are infrequent (admin actions)."""

    def __init__(
        self,
        models: list[ModelEntry] | None = None,
        assignments: dict[str, str] | None = None,
        *,
        allowlist: list[str] | None = None,
    ) -> None:
        self._models: dict[str, ModelEntry] = {m.id: m for m in (models or [])}
        self.assignments: dict[str, str] = dict(assignments or {})
        self._allowlist: list[str] = list(allowlist or [])

    # --- validation ------------------------------------------------------- #
    def _url_allowed(self, url: str) -> bool:
        # Fail closed: with no allowlist configured, nothing is permitted — the
        # server would otherwise call an operator-supplied URL unchecked (SSRF).
        #
        # Match on parsed (scheme, host[, port]) rather than a raw string prefix:
        # a prefix check lets ``http://localhost.evil.com`` slip past an allowed
        # ``http://localhost`` prefix. An allowlist entry with no explicit port
        # permits any port on that host (so ``http://localhost`` allows
        # ``http://localhost:9005``); an entry that pins a port requires a match.
        if not self._allowlist:
            return False
        try:
            u = urlsplit(url)
            if not u.scheme or not u.hostname:
                return False
            u_port = u.port
            for prefix in self._allowlist:
                p = urlsplit(prefix)
                if u.scheme != p.scheme or u.hostname != p.hostname:
                    continue
                if p.port is not None and u_port != p.port:
                    continue
                return True
        except ValueError:
            # Malformed URL / out-of-range port → reject.
            return False
        return False

    def _validate(self, entry: ModelEntry) -> None:
        if entry.task not in TASKS:
            raise RegistryError(f"unknown task {entry.task!r}; one of {sorted(TASKS)}")
        if entry.provider not in PROVIDERS:
            raise RegistryError(f"unknown provider {entry.provider!r}; one of {sorted(PROVIDERS)}")
        if not entry.base_urls:
            raise RegistryError("at least one base_url is required")
        for u in entry.base_urls:
            if not self._url_allowed(u):
                raise RegistryError(
                    f"url {u!r} is not permitted by model_url_allowlist "
                    f"(configured prefixes: {self._allowlist or '<none>'})"
                )
        if entry.task == "embedding" and not (entry.dim and entry.dim > 0):
            raise RegistryError("embedding models require a positive 'dim'")

    # --- CRUD ------------------------------------------------------------- #
    def entries(self) -> list[ModelEntry]:
        return list(self._models.values())

    def get(self, model_id: str) -> ModelEntry | None:
        return self._models.get(model_id)

    def create(self, entry: ModelEntry) -> ModelEntry:
        if entry.id in self._models:
            raise RegistryError(f"model {entry.id!r} already exists")
        self._validate(entry)
        self._models[entry.id] = entry
        return entry

    def update(self, model_id: str, entry: ModelEntry) -> ModelEntry:
        if model_id not in self._models:
            raise RegistryError(f"unknown model {model_id!r}")
        entry = entry.model_copy(update={"id": model_id})  # id is the path, immutable
        self._validate(entry)
        self._models[model_id] = entry
        return entry

    def delete(self, model_id: str) -> None:
        if model_id not in self._models:
            raise RegistryError(f"unknown model {model_id!r}")
        bound = [t for t, mid in self.assignments.items() if mid == model_id]
        if bound:
            raise RegistryError(f"model {model_id!r} is assigned to {bound}; unassign first")
        del self._models[model_id]

    # --- assignments (state only; applying is deps.apply_assignment) ------- #
    def resolve_assignment(self, task: str, model_id: str | None) -> ModelEntry | None:
        """Validate ``task -> model_id`` and return the entry to apply (None =
        revert to the settings default) **without mutating** the registry. Lets a
        caller check every field of a multi-task patch before applying any, so a
        later invalid task can't leave an earlier one half-applied."""
        if task not in HOT_SWAPPABLE:
            raise RegistryError(
                f"task {task!r} is not hot-swappable; embedding/chunking changes build a new collection"
            )
        if model_id is None:
            return None
        entry = self.get(model_id)
        if entry is None:
            raise RegistryError(f"unknown model {model_id!r}")
        if entry.task != task:
            raise RegistryError(f"model {model_id!r} serves task {entry.task!r}, not {task!r}")
        return entry

    def set_assignment(self, task: str, model_id: str | None) -> ModelEntry | None:
        """Record ``task -> model_id`` (or clear it with None) and return the entry
        to apply. Validates via :meth:`resolve_assignment`; the caller applies +
        persists."""
        entry = self.resolve_assignment(task, model_id)
        if model_id is None:
            self.assignments.pop(task, None)
        else:
            self.assignments[task] = model_id
        return entry

    # --- persistence ------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "models": [m.model_dump() for m in self._models.values()],
            "assignments": self.assignments,
        }

    def save(self, path: str) -> None:
        if not path:
            return  # in-memory only
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str, *, allowlist: list[str] | None = None) -> ModelRegistry:
        allow = list(allowlist or [])
        if path and Path(path).is_file():
            try:
                data = json.loads(Path(path).read_text())
                models = [ModelEntry(**m) for m in data.get("models", [])]
                return cls(models, data.get("assignments", {}), allowlist=allow)
            except Exception:
                log.warning("model registry file %s unreadable; starting empty", path, exc_info=True)
        return cls([], {}, allowlist=allow)
