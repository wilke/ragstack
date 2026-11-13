"""#341: ``load_embeddings`` restores the parked ES refresh interval on SIGTERM.

``--bulk-refresh`` parks ``index.refresh_interval`` at ``-1`` for the load and
restores it in a ``finally``. SIGTERM's default action kills the process
without unwinding, so a plain ``kill``, a supervisor/container teardown or an
engine cancellation used to leave the index refreshing never. These tests run
the real ``main()`` in a subprocess — both call sites, the embedding-file load
and ``--replay`` — with a fake text index that logs park/restore to a file,
deliver SIGTERM while the load is running, and assert the restore ran and the
process exited 143.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[2]
SCRIPTS = PYTHON_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name != "posix",
                                reason="POSIX signals")

# The driver swaps the in-memory text index for one that HAS the ES parking
# methods (logging each call), and replaces the load work with a coroutine
# that announces itself and then blocks, so the test can signal mid-load.
DRIVER = textwrap.dedent('''
    import asyncio, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import load_embeddings as le
    from ragstack.stores.memory import InMemoryTextIndex

    log_path, ready, mode, slow_restore = (Path(sys.argv[2]), Path(sys.argv[3]),
                                           sys.argv[4], sys.argv[5] == "1")

    def note(line):
        with log_path.open("a") as fh:
            fh.write(line + "\\n")

    class ParkingIndex(InMemoryTextIndex):
        async def bulk_load_refresh(self, disable):
            note("parked")
            return "30s"
        async def restore_refresh(self, prior):
            note(f"restoring:{prior}")
            if slow_restore:
                ready.with_suffix(".restoring").write_text("x")
                await asyncio.sleep(60)
            note(f"restored:{prior}")
        async def refresh(self):
            note("refreshed")

    async def block(*a, **k):
        ready.write_text("x")
        await asyncio.sleep(60)
        note("load-finished")  # never reached when SIGTERM works

    le.InMemoryTextIndex = ParkingIndex
    le.run_load_file = block
    le.run_replay = block
    le.verify_replay = lambda *a, **k: []
    common = ["--vector-backend", "memory", "--text-backend", "memory",
              "--collection-id", "sigterm-test", "--bulk-refresh",
              "--out", str(log_path.with_suffix(".summary.json"))]
    if mode == "replay":
        argv = ["--replay", "unused-version-dir", *common]
    else:
        argv = ["unused.emb.jsonl", *common]
    sys.exit(le.main(argv))
''')


def _start(tmp_path: Path, mode: str, *, slow_restore: bool = False):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    log = tmp_path / "calls.log"
    ready = tmp_path / "ready"
    env = {**os.environ, "PYTHONPATH": str(PYTHON_DIR),
           # A leak to a real store must fail loudly, never reach one.
           "QDRANT_URL": "http://127.0.0.1:1", "ELASTICSEARCH_URL": "http://127.0.0.1:1"}
    proc = subprocess.Popen(
        [sys.executable, str(driver), str(SCRIPTS), str(log), str(ready), mode,
         "1" if slow_restore else "0"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc, log, ready


def _wait_for(path: Path, proc, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if proc.poll() is not None:
            out, err = proc.communicate()
            pytest.fail(f"driver exited early ({proc.returncode}):\n{out}\n{err}")
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"timed out waiting for {path.name}")
        time.sleep(0.05)


@pytest.mark.parametrize("mode", ["load", "replay"])
def test_sigterm_mid_load_restores_refresh_and_exits_143(tmp_path, mode):
    proc, log, ready = _start(tmp_path, mode)
    try:
        _wait_for(ready, proc)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    calls = log.read_text().splitlines()
    assert calls == ["parked", "restoring:30s", "restored:30s", "refreshed"], (calls, out, err)
    assert proc.returncode == 143, (proc.returncode, out, err)
    assert "terminated by SIGTERM" in err
    assert "refresh_interval restored" in out


def test_second_sigterm_still_kills(tmp_path):
    """The handler is one-shot: a SIGTERM during a hung restore gets the
    default action, so an operator is never left unable to stop the load."""
    proc, log, ready = _start(tmp_path, "load", slow_restore=True)
    try:
        _wait_for(ready, proc)
        proc.send_signal(signal.SIGTERM)
        _wait_for(ready.with_suffix(".restoring"), proc)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == -signal.SIGTERM
    assert "restored:30s" not in log.read_text()


class _Idx:
    def __init__(self):
        self.calls: list[str] = []

    async def bulk_load_refresh(self, disable):
        self.calls.append("parked")
        return None

    async def restore_refresh(self, prior):
        self.calls.append(f"restored:{prior}")

    async def refresh(self):
        self.calls.append("refreshed")


def test_handler_removed_after_the_parked_window():
    import load_embeddings as le

    before = signal.getsignal(signal.SIGTERM)
    idx = _Idx()

    async def run():
        async with le.parked_refresh(idx, True) as prior:
            assert prior is None
            assert signal.getsignal(signal.SIGTERM) is not before  # armed while parked
        return signal.getsignal(signal.SIGTERM)

    after = asyncio.run(run())
    assert idx.calls == ["parked", "restored:None", "refreshed"]
    assert after in (before, signal.SIG_DFL)


def test_nothing_parked_or_installed_when_disabled_or_unsupported():
    import load_embeddings as le

    before = signal.getsignal(signal.SIGTERM)
    idx = _Idx()

    async def run():
        async with le.parked_refresh(idx, False) as p1:
            assert signal.getsignal(signal.SIGTERM) is before
        async with le.parked_refresh(object(), True) as p2:
            assert signal.getsignal(signal.SIGTERM) is before
        return p1, p2

    assert asyncio.run(run()) == (None, None)
    assert idx.calls == []
