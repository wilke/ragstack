"""API tests for health and query endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from ragstack.api.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_query_endpoint_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/query",
            json={"query": "What is RAG?"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert "sources" in body
    assert "rewritten_queries" in body


@pytest.mark.asyncio
async def test_retrieve_endpoint_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/retrieve",
            json={"query": "vector databases"},
        )
    assert response.status_code == 200
    assert "sources" in response.json()


@pytest.mark.asyncio
async def test_ingest_endpoint_returns_accepted():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ingest",
            json={"source": "/tmp/test.txt"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert "job_id" in body


@pytest.mark.asyncio
async def test_list_documents_returns_empty_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/documents")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_graph_entities_returns_empty_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/graph/entities")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_graph_neighbors_returns_empty_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/graph/neighbors/Alice")
    assert response.status_code == 200
    assert response.json() == []
