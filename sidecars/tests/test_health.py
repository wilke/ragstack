"""Sidecar liveness/readiness (#751 PR-1, #748).

The readiness probe must exercise the model: a reranker whose predict() raises
(the cuDNN-workspace OOM of #748) has to turn /ready — and its /health alias —
into a 503 naming the exception class, while /live stays 200. A stub model is
injected through each module's ``_model``, so no torch / sentence-transformers
is needed and nothing touches a GPU or a port.

Run: ``make test-sidecars`` (or ``pytest sidecars/tests``).
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SIDECARS = Path(__file__).resolve().parents[1]


def _load(name: str):
    # Every sidecar is a top-level ``main.py`` (bind-mounted as /app), so load
    # each under a distinct module name instead of importing ``main``.
    modname = f"_sidecar_{name}_main"
    spec = importlib.util.spec_from_file_location(modname, SIDECARS / name / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


class StubModel:
    """Counts calls; raises or sleeps on demand. Both predict and encode."""

    device = "cuda:0"

    def __init__(self, *, raises: BaseException | None = None, sleep: float = 0.0):
        self.raises = raises
        self.sleep = sleep
        self.calls = 0
        self.release = threading.Event()

    def _run(self, *args, **kwargs):
        self.calls += 1
        if self.sleep:
            # Wait on an event (released at teardown) rather than a bare sleep,
            # so a timed-out probe thread does not outlive the test by long.
            self.release.wait(self.sleep)
        if self.raises is not None:
            raise self.raises
        return [0.5, 0.25]

    predict = _run
    encode = _run

    def get_sentence_embedding_dimension(self) -> int:
        return 768


MODEL_SIDECARS = ["crossencoder", "embedding"]


@pytest.fixture(params=MODEL_SIDECARS)
def sidecar(request, monkeypatch):
    mod = _load(request.param)
    monkeypatch.setattr(mod, "HEALTH_TIMEOUT_S", 0.3)
    monkeypatch.setattr(mod, "HEALTH_CACHE_S", 10.0)
    stubs: list[StubModel] = []

    def install(stub: StubModel):
        stubs.append(stub)
        monkeypatch.setattr(mod, "_model", stub)
        return stub

    mod.install = install
    yield mod
    for s in stubs:
        if s is not None:
            s.release.set()


def _client(mod) -> TestClient:
    # Entering the client runs the lifespan; with _model already injected the
    # warm-up returns the stub instead of loading a real model.
    return TestClient(mod.app)


def test_ready_ok_runs_real_probe(sidecar):
    stub = sidecar.install(StubModel())
    with _client(sidecar) as c:
        r = c.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["loaded"] is True
    assert body["model"] == sidecar.MODEL_NAME
    assert body["device"] == "cuda:0"
    assert isinstance(body["probe_ms"], float)
    assert body["checked_at"].endswith("Z")
    assert stub.calls == 1


def test_probe_exception_is_503_with_class(sidecar):
    sidecar.install(StubModel(raises=RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED\nmore")))
    with _client(sidecar) as c:
        r = c.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "fail"
    assert body["error"] == "RuntimeError"
    assert body["detail"] == "cuDNN error: CUDNN_STATUS_NOT_INITIALIZED"  # first line only
    assert body["loaded"] is True


def test_probe_timeout_is_503_timeout_error(sidecar):
    sidecar.install(StubModel(sleep=5.0))
    with _client(sidecar) as c:
        t0 = time.monotonic()
        r = c.get("/ready")
        elapsed = time.monotonic() - t0
    assert r.status_code == 503
    assert r.json()["error"] == "TimeoutError"
    assert elapsed < 3.0  # bounded by HEALTH_TIMEOUT_S, not by the hung call


def test_timed_out_probe_is_not_restarted_while_running(sidecar, monkeypatch):
    stub = sidecar.install(StubModel(sleep=5.0))
    with _client(sidecar) as c:
        assert c.get("/ready").status_code == 503
        monkeypatch.setattr(sidecar, "_health_cache", None)  # force a re-check
        r = c.get("/ready")
    assert r.status_code == 503
    assert r.json()["error"] == "TimeoutError"
    assert "still running" in r.json()["detail"]
    assert stub.calls == 1  # no second call stacked onto the hung device


def test_cache_window_runs_probe_once(sidecar):
    stub = sidecar.install(StubModel())
    with _client(sidecar) as c:
        assert c.get("/ready").status_code == 200
        assert c.get("/ready").status_code == 200
        assert c.get("/health").status_code == 200
    assert stub.calls == 1


def test_cache_expiry_reprobes(sidecar, monkeypatch):
    stub = sidecar.install(StubModel())
    monkeypatch.setattr(sidecar, "HEALTH_CACHE_S", 0.0)
    with _client(sidecar) as c:
        c.get("/ready")
        c.get("/ready")
    assert stub.calls == 2


def test_failure_is_cached_too(sidecar):
    stub = sidecar.install(StubModel(raises=RuntimeError("boom")))
    with _client(sidecar) as c:
        assert c.get("/ready").status_code == 503
        assert c.get("/ready").status_code == 503
    assert stub.calls == 1


def test_live_200_while_ready_503(sidecar):
    sidecar.install(StubModel(raises=RuntimeError("boom")))
    with _client(sidecar) as c:
        assert c.get("/ready").status_code == 503
        live = c.get("/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_health_is_alias_of_ready(sidecar):
    sidecar.install(StubModel(raises=ValueError("bad")))
    with _client(sidecar) as c:
        r = c.get("/health")
    assert r.status_code == 503
    assert r.json()["error"] == "ValueError"
    # The API's startup reranker check reads "model" from /health; keep it.
    assert r.json()["model"] == sidecar.MODEL_NAME


def test_model_not_loaded_is_loading(sidecar):
    # No lifespan (client not entered) and no model: /ready must not load one.
    sidecar.install(None)  # type: ignore[arg-type]
    c = TestClient(sidecar.app)
    r = c.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "loading"
    assert r.json()["loaded"] is False
    assert sidecar._model is None


# ---------------------------------------------------------------- faiss


def test_faiss_ready_and_live(tmp_path, monkeypatch):
    mod = _load("faiss")
    monkeypatch.setattr(mod, "DATA_DIR", str(tmp_path))
    (tmp_path / "a.faiss").write_bytes(b"")
    fake = type(sys)("faiss")
    monkeypatch.setitem(sys.modules, "faiss", fake)
    c = TestClient(mod.app)
    assert c.get("/live").json() == {"status": "ok"}
    r = c.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["available"] == 1
    assert c.get("/health").status_code == 200


def test_faiss_not_ready_without_faiss(monkeypatch):
    mod = _load("faiss")
    monkeypatch.setitem(sys.modules, "faiss", None)  # import faiss -> ImportError
    c = TestClient(mod.app)
    r = c.get("/ready")
    assert r.status_code == 503
    assert r.json()["error"] in {"ImportError", "ModuleNotFoundError"}
    assert c.get("/live").status_code == 200
