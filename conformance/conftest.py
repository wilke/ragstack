"""Shared fixtures for RAGStack conformance tests.

All tests run as pure HTTP black-box tests against a running
RAGStack-compatible API server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the RAGStack API server under test."""
    return os.environ.get("RAGSTACK_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def impl() -> str:
    """Implementation identifier (e.g. 'python', 'go', 'rust')."""
    return os.environ.get("RAGSTACK_IMPL", "unknown")


@pytest_asyncio.fixture(scope="session")
async def client(base_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client pre-configured with the server base URL."""
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def schemas() -> dict[str, dict]:
    """Load all JSON schemas from ``contracts/schemas/`` and return them
    as a mapping of schema name (without extension) to parsed dict.
    """
    schemas_dir = Path(__file__).resolve().parent.parent / "contracts" / "schemas"
    result: dict[str, dict] = {}
    if schemas_dir.is_dir():
        for schema_file in sorted(schemas_dir.glob("*.json")):
            with open(schema_file, encoding="utf-8") as fh:
                result[schema_file.stem] = json.load(fh)
    return result
