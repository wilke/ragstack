"""Knowledge-graph endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class EntityInfo(BaseModel):
    name: str
    triple_count: int = 0


class TripleResponse(BaseModel):
    subject: str
    predicate: str
    object: str


@router.get("/entities", response_model=list[EntityInfo])
async def list_entities() -> list[EntityInfo]:
    """List all entities in the knowledge graph."""
    return []


@router.get("/neighbors/{entity}", response_model=list[TripleResponse])
async def get_neighbors(entity: str, depth: int = 1) -> list[TripleResponse]:
    """Return triples in the neighbourhood of an entity."""
    return []
