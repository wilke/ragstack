"""The driver must let the two legs settle before calling them disagreed.

An immediate read races the stores. Two changes that were individually correct
made that race likely: the legs are now written concurrently (neither finishes
last by construction), and the text index has its refresh parked during a bulk
load so its count only becomes visible at the end. The vector store also
acknowledges upserts before applying them.

On batch 01024-01087 that produced a 16,950 "disagreement" which converged to
zero in ~60 seconds — but the driver had already written `failed` and triggered
a full re-run of a healthy batch, at a cost of ~3 hours.
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_driver", Path(__file__).resolve().parents[2] / "scripts" / "gowe_batch_ingest.py"
)
drv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drv)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(drv.__dict__.setdefault("time", __import__("time")),
                        "sleep", lambda s: None, raising=False)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)


def _counts(monkeypatch, sequence):
    """Feed store_counts a scripted sequence of (qdrant, es) readings."""
    calls = {"n": 0}

    def fake(q, e, s):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]

    monkeypatch.setattr(drv, "store_counts", fake)
    return calls


def test_returns_immediately_when_legs_already_agree(monkeypatch):
    calls = _counts(monkeypatch, [(100, 100)])
    assert drv.settled_store_counts("q", "e", "s") == (100, 100)
    assert calls["n"] == 1, "polled despite the legs already agreeing"


def test_transient_disagreement_converges(monkeypatch):
    """The real-world shape: short on one leg, then equal."""
    _counts(monkeypatch, [(83_050, 100_000), (95_000, 100_000), (100_000, 100_000)])
    assert drv.settled_store_counts("q", "e", "s") == (100_000, 100_000)


def test_real_disagreement_still_reported(monkeypatch):
    """A genuine gap must not be masked — it never converges, so the poll
    returns the last reading and the caller fails the batch."""
    _counts(monkeypatch, [(90_000, 100_000)])
    q, e = drv.settled_store_counts("q", "e", "s", max_wait=1, interval=0)
    assert (q, e) == (90_000, 100_000)
    assert q != e


def test_static_gap_fails_fast_without_burning_the_whole_window(monkeypatch):
    """A real gap is STATIC. Waiting the full window for it is wrong twice over:
    it delays a true failure, and it made the test suite take 20 minutes."""
    calls = _counts(monkeypatch, [(90_000, 100_000)])
    q, e = drv.settled_store_counts("q", "e", "s", max_wait=10_000,
                                    interval=0, stable_rounds=3)
    assert (q, e) == (90_000, 100_000)
    # 1 initial + 3 identical reads to establish staleness, not hundreds.
    assert calls["n"] <= 5, f"polled {calls['n']} times on a static gap"


def test_a_moving_leg_is_not_mistaken_for_a_static_gap(monkeypatch):
    """Counts that keep changing must keep the poll alive even if they are slow
    to converge — otherwise a slow apply gets failed as a real gap."""
    _counts(monkeypatch, [(10, 100), (40, 100), (70, 100), (100, 100)])
    assert drv.settled_store_counts("q", "e", "s", interval=0) == (100, 100)


def test_settle_window_defaults_long_enough_to_cover_an_async_apply():
    import inspect
    default = inspect.signature(drv.settled_store_counts).parameters["max_wait"].default
    # The observed convergence was ~60 s; the default needs real headroom over it
    # because a bigger collection applies more slowly.
    assert default >= 300, f"settle window {default}s too short"


def test_verification_uses_the_settling_read_not_the_raw_one():
    """Guards the wiring: the fix is worthless if the caller still reads once."""
    src = Path(__file__).resolve().parents[2] / "scripts" / "gowe_batch_ingest.py"
    text = src.read_text()
    verify = text.split("legs disagree")[0][-1500:]
    assert "settled_store_counts(" in verify, (
        "the leg check no longer calls settled_store_counts — an immediate "
        "store_counts read reintroduces the race")


def test_settle_timeout_flag_is_exposed():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "gowe_batch_ingest.py")
    assert '"--settle-timeout"' in src.read_text()
