"""API tests for health, query, retrieve, and async ingestion endpoints."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_query_endpoint_returns_200(client):
    response = await client.post("/v1/query", json={"query": "What is RAG?"})
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert "sources" in body
    assert "rewritten_queries" in body


@pytest.mark.asyncio
async def test_retrieve_endpoint_returns_200(client):
    response = await client.post("/v1/retrieve", json={"query": "vector databases"})
    assert response.status_code == 200
    assert "sources" in response.json()


@pytest.mark.asyncio
async def test_ingest_endpoint_returns_accepted(client):
    response = await client.post("/v1/ingest", json={"source": "/tmp/test.txt"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert "job_id" in body


@pytest.mark.asyncio
async def test_ingest_async_flow_completes(client, tmp_path):
    """Ingest a real file, poll the job, and confirm it reaches completed."""
    f = tmp_path / "doc.txt"
    f.write_text("hello world " * 50, encoding="utf-8")

    resp = await client.post("/v1/ingest", json={"source": str(f)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    job_id = resp.json()["job_id"]

    body = {}
    for _ in range(50):
        body = (await client.get(f"/v1/ingest/{job_id}")).json()
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)

    assert body["status"] == "completed"
    assert len(body["chunk_ids"]) > 0


@pytest.mark.asyncio
async def test_ingest_status_unknown_job(client):
    resp = await client.get("/v1/ingest/no-such-job")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"


@pytest.mark.asyncio
async def test_list_documents_returns_empty_list(client):
    response = await client.get("/v1/documents")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_graph_entities_returns_empty_list(client):
    response = await client.get("/v1/graph/entities")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_graph_neighbors_returns_empty_list(client):
    response = await client.get("/v1/graph/neighbors/Alice")
    assert response.status_code == 200
    assert response.json() == []
