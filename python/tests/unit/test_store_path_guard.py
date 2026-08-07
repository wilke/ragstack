"""Relative sqlite store paths are refused in production (#272).

Observed live: a demo server's config used a setting name that does not exist
(`COLLECTION_STORE_SQLITE_PATH`). The real name is `COLLECTION_STORE_PATH`, so
the wrong one silently fell back to the RELATIVE default and the registry landed
in the process working directory — where a second server started from the same
checkout read it and seeded another tenant's ACL database with a foreign
collection.
"""
from __future__ import annotations

import pytest

from ragstack.api import deps


def _prod(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", "/tmp")
    monkeypatch.setattr(deps.settings, "user_store_backend", "sqlite")
    monkeypatch.setattr(deps.settings, "user_store_path", "/abs/users.db")
    monkeypatch.setattr(deps.settings, "collection_store_backend", "json")
    # A json store needs somewhere to write, or the durable-registry guard
    # fires first and these path assertions never run (#286).
    monkeypatch.setattr(deps.settings, "collections_file", "/abs/collections.json")
    monkeypatch.setattr(deps.settings, "job_store_backend", "memory")


def test_absolute_paths_pass(monkeypatch):
    _prod(monkeypatch)
    deps._validate_production_settings()


@pytest.mark.parametrize(
    "backend_attr,path_attr,name",
    [
        ("user_store_backend", "user_store_path", "USER_STORE_PATH"),
        ("collection_store_backend", "collection_store_path", "COLLECTION_STORE_PATH"),
        ("job_store_backend", "job_store_path", "JOB_STORE_PATH"),
    ],
)
def test_a_relative_sqlite_path_is_refused(monkeypatch, backend_attr, path_attr, name):
    _prod(monkeypatch)
    monkeypatch.setattr(deps.settings, backend_attr, "sqlite")
    monkeypatch.setattr(deps.settings, path_attr, "ragstack_thing.db")  # the default shape
    with pytest.raises(RuntimeError) as exc:
        deps._validate_production_settings()
    msg = str(exc.value)
    assert name in msg
    assert "RELATIVE" in msg


def test_a_non_sqlite_backend_is_not_path_checked(monkeypatch):
    """postgres carries its location in the DSN; memory is refused elsewhere."""
    _prod(monkeypatch)
    monkeypatch.setattr(deps.settings, "collection_store_backend", "postgres")
    monkeypatch.setattr(deps.settings, "collection_store_path", "relative.db")
    deps._validate_production_settings()
