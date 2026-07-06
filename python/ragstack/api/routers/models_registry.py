"""Admin: runtime model registry + hot-swappable task assignments (Phase 1).

Mounted at ``/v1/admin`` and gated by the admin role (see main.py). Registering a
model is config only; assigning one to a hot-swappable task (llm / reranker)
rebuilds and atomically swaps the live client (``deps.apply_assignment``). Build-
time tasks (embedding / chunking) are registerable but not assignable — changing
those means building a new collection, not mutating a running one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ragstack.api.deps import apply_assignment, get_model_registry
from ragstack.api.model_registry import ModelEntry, ModelRegistry, RegistryError
from ragstack.config import settings

router = APIRouter()


class ModelsRegistryResponse(BaseModel):
    models: list[ModelEntry]
    assignments: dict[str, str]


class AssignmentsPatch(BaseModel):
    # Only the hot-swappable tasks. extra="forbid" → naming a build-time task
    # (embedding/tokenizer) is a 422 rather than a silent no-op.
    model_config = {"extra": "forbid"}
    llm: str | None = None
    reranker: str | None = None


def _snapshot(reg: ModelRegistry) -> ModelsRegistryResponse:
    return ModelsRegistryResponse(models=reg.entries(), assignments=reg.assignments)


def _http_error(e: RegistryError) -> HTTPException:
    # RegistryError carries the status the registry chose (404/409/400); the
    # router stays thin and doesn't re-derive it from the message text.
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/models/registry", response_model=ModelsRegistryResponse)
async def list_models(reg: ModelRegistry = Depends(get_model_registry)) -> ModelsRegistryResponse:
    """The registered models and the current hot-swappable assignments."""
    return _snapshot(reg)


@router.post("/models/registry", response_model=ModelEntry, status_code=201)
async def register_model(
    entry: ModelEntry, reg: ModelRegistry = Depends(get_model_registry)
) -> ModelEntry:
    """Register a model. base_urls must pass the SSRF allowlist; a duplicate id is
    a 400 (use PUT to update)."""
    try:
        created = reg.create(entry)
    except RegistryError as e:
        raise _http_error(e) from None
    reg.save(settings.models_registry_file)
    return created


@router.put("/models/registry/{model_id}", response_model=ModelEntry)
async def update_model(
    model_id: str,
    entry: ModelEntry,
    request: Request,
    reg: ModelRegistry = Depends(get_model_registry),
) -> ModelEntry:
    """Replace a registered model (id is taken from the path, immutable). If the
    model is currently assigned to a live task, rebuild that task's app.state
    client from the new entry — otherwise the running server keeps serving the
    old endpoint/model behind an updated registry until the next restart."""
    try:
        updated = reg.update(model_id, entry)
    except RegistryError as e:
        raise _http_error(e) from None
    for task, assigned_id in reg.assignments.items():
        if assigned_id == model_id:
            apply_assignment(request.app, task, updated)
    reg.save(settings.models_registry_file)
    return updated


@router.delete("/models/registry/{model_id}", status_code=204)
async def delete_model(model_id: str, reg: ModelRegistry = Depends(get_model_registry)) -> None:
    """Remove a model. A model currently assigned to a task is a 409 (unassign first)."""
    try:
        reg.delete(model_id)
    except RegistryError as e:
        raise _http_error(e) from None
    reg.save(settings.models_registry_file)


@router.patch("/config/assignments", response_model=ModelsRegistryResponse)
async def patch_assignments(
    body: AssignmentsPatch,
    request: Request,
    reg: ModelRegistry = Depends(get_model_registry),
) -> ModelsRegistryResponse:
    """Assign registered models to hot-swappable tasks and apply live. Only the
    fields present in the body are changed; a field set to ``null`` reverts that
    task to its settings default. Each applied task rebuilds its app.state client.

    Every requested task is validated **before** any live swap, so an invalid
    task in a multi-field patch can't leave an earlier task half-applied and
    unpersisted."""
    requested = {task: getattr(body, task) for task in body.model_fields_set}
    try:
        for task, model_id in requested.items():
            reg.resolve_assignment(task, model_id)
    except RegistryError as e:
        raise _http_error(e) from None
    for task, model_id in requested.items():
        entry = reg.set_assignment(task, model_id)
        apply_assignment(request.app, task, entry)
    reg.save(settings.models_registry_file)
    return _snapshot(reg)
