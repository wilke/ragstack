"""#775: the API logs its effective symbol-glossary config once at startup.

Before it, the boost was logged nowhere and extraction only when on, so an
operator could not tell from a tenant's log whether an `env set` had been
applied by a restart. Drives the real lifespan on in-memory backends, with
every store URL at a dead port."""
from __future__ import annotations

import json
import logging

import pytest

from ragstack.api import deps

DEAD = "http://127.0.0.1:1"
LINE = "symbol glossary config:"


def _in_memory_settings(monkeypatch) -> None:
    s = deps.settings
    monkeypatch.setattr(s, "vector_backend", "memory")
    monkeypatch.setattr(s, "text_backend", "memory")
    monkeypatch.setattr(s, "graph_backend", "memory")
    monkeypatch.setattr(s, "rerank_enabled", False)
    monkeypatch.setattr(s, "require_durable_backends", False)
    monkeypatch.setattr(s, "qdrant_url", DEAD)
    monkeypatch.setattr(s, "elasticsearch_url", DEAD)


async def _boot(caplog) -> list[str]:
    from fastapi import FastAPI

    caplog.set_level(logging.INFO, logger=deps.log.name)
    try:
        async with deps.lifespan(FastAPI()):
            pass
    except Exception:
        # Later startup steps may fail without infra; the line comes first.
        pass
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(LINE)]


@pytest.mark.parametrize(
    ("mode", "boost", "cache", "want"),
    [
        ("off", 0.0, "", "extraction=off boost=0 llm_cache=none config_json=default"),
        ("rules", 1.0, "/rag/x/c.jsonl", "extraction=rules boost=1 llm_cache=/rag/x/c.jsonl"),
        ("rules", 2.5, "", "extraction=rules boost=2.5 llm_cache=none"),
        ("bogus", 1.0, "", "extraction=off (unknown mode 'bogus') boost=1"),
    ],
)
async def test_startup_logs_the_effective_glossary_config(
    monkeypatch, caplog, mode, boost, cache, want
) -> None:
    _in_memory_settings(monkeypatch)
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", mode)
    monkeypatch.setattr(deps.settings, "text_symbol_glossary_boost", boost)
    monkeypatch.setattr(deps.settings, "symbol_glossary_cache_path", cache)
    lines = await _boot(caplog)
    assert len(lines) == 1, lines
    assert want in lines[0], lines[0]


async def test_the_line_carries_no_secret(monkeypatch, caplog) -> None:
    _in_memory_settings(monkeypatch)
    canary = "sk-canary-0123456789abcdef"
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "rules")
    monkeypatch.setattr(deps.settings, "symbol_glossary_config_json", json.dumps({"note": canary}))
    monkeypatch.setattr(deps.settings, "api_keys", [canary])
    lines = await _boot(caplog)
    assert len(lines) == 1 and "config_json=set" in lines[0]
    assert canary not in lines[0]
