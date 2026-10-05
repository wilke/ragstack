"""The tools-image identity check (ADR-0010 decision 7; #655 step 4).

GoWe resolves a ``dockerPull`` as ``<image-dir>/<name>`` and never checks
``dockerImageId``, so ``ragstack.tool_image.verify_named_image`` is the only
enforcement of image identity on the whole path. Covered:

* the check itself over a temp store: ok; wrong sha256 in the receipt;
  receipt missing; file missing; two dirs with the hit in the second (the
  path is reported); the committed receipt disagreeing; labels through a fake
  ``apptainer`` on PATH (ok, mismatching, and absent → ``labels_checked``
  False with a warning, not a failure); the bare default name → ``unstamped``;
* the sha256 cache (one hash per (path, size, mtime));
* the boot gate in ``api.deps``: stamped CWL + good store → boots; wrong-sha
  receipt → refuses, naming the image, the path and the problem; dirs unset →
  warns and boots; unstamped CWL → boots with no check; local backend → no
  check at all;
* the CLI ``python -m ragstack.tool_image verify`` the ctl shells to.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ragstack.api import deps
from ragstack.tool_image import (
    _SHA256_CACHE,
    DEFAULT_TOOL_IMAGE,
    RECEIPT_BASENAME,
    ImageVerdict,
    file_sha256,
    parse_image_dirs,
    stamp_tool_image,
    verify_cwl_file,
    verify_named_image,
)

NAME = "ragstack-tools-v9.9.9-b1.sif"
COMMIT = "c" * 40
IMAGE_BYTES = b"not a real SIF, but 3 MB of it\n" * 100_000

CWL = """\
cwlVersion: v1.2
class: Workflow
inputs:
  pdfs: File[]
steps:
  a:
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
"""


def _receipt(sha: str, **over: object) -> dict[str, object]:
    r: dict[str, object] = {"name": NAME, "version": "v9.9.9", "commit": COMMIT, "build": "1",
                            "build_date": "2026-10-05T00:00:00Z", "sha256": sha}
    r.update(over)
    return r


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A store dir with a fake image and a receipt that matches it."""
    d = tmp_path / "store"
    d.mkdir()
    (d / NAME).write_bytes(IMAGE_BYTES)
    sha = hashlib.sha256(IMAGE_BYTES).hexdigest()
    (d / (NAME + ".receipt.json")).write_text(json.dumps(_receipt(sha)))
    return d


@pytest.fixture
def no_apptainer(monkeypatch, tmp_path: Path):
    """PATH with no apptainer on it (and no real one reachable)."""
    empty = tmp_path / "nobin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _fake_apptainer(dir_: Path, labels: dict[str, str], *, rc: int = 0) -> Path:
    """An ``apptainer`` on PATH that answers ``inspect --json --labels``."""
    d = dir_ / "bin"
    d.mkdir(exist_ok=True)
    exe = d / "apptainer"
    doc = json.dumps({"data": {"attributes": {"labels": labels}}})
    exe.write_text("#!/bin/sh\n" + (f"printf '%s' '{doc}'\n" if rc == 0 else "echo 'FATAL: not a SIF' >&2\n")
                   + f"exit {rc}\n")
    exe.chmod(0o755)
    return d


@pytest.fixture
def good_apptainer(monkeypatch, tmp_path: Path):
    d = _fake_apptainer(tmp_path, {"org.ragstack.version": "v9.9.9", "org.ragstack.commit": COMMIT,
                                   "org.ragstack.build": "1", "org.ragstack.build-date": "x"})
    monkeypatch.setenv("PATH", str(d))


# --- the check ------------------------------------------------------------- #

def test_ok_with_labels(store, good_apptainer):
    v = verify_named_image(NAME, [store])
    assert v.ok and v.state == "ok"
    assert v.exists and v.path == str(store / NAME) and v.found_in == [str(store)]
    assert v.receipt_found and v.sha256_ok is True
    assert v.labels_checked and v.labels_ok is True
    assert v.problems == [] and v.warnings == []
    d = v.to_dict()
    assert d["name"] == NAME and d["state"] == "ok" and d["sha256"] == hashlib.sha256(IMAGE_BYTES).hexdigest()


def test_wrong_sha256_in_receipt_is_a_problem(store, no_apptainer):
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("0" * 64)))
    v = verify_named_image(NAME, [store])
    assert not v.ok and v.state == "problem"
    assert v.sha256_ok is False
    assert any("sha256 mismatch" in p and str(store / NAME) in p for p in v.problems)


def test_receipt_missing_is_a_problem(store, no_apptainer):
    (store / (NAME + ".receipt.json")).unlink()
    v = verify_named_image(NAME, [store])
    assert not v.ok and v.exists and not v.receipt_found
    assert any("no receipt beside the image" in p for p in v.problems)
    assert v.sha256_ok is None  # nothing to compare against — but still a failure


def test_receipt_naming_another_image_is_a_problem(store, no_apptainer):
    sha = hashlib.sha256(IMAGE_BYTES).hexdigest()
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt(sha, name="ragstack-tools-v1.0.0-b1.sif")))
    v = verify_named_image(NAME, [store])
    assert any("names 'ragstack-tools-v1.0.0-b1.sif', not " + NAME in p for p in v.problems)


def test_file_missing_is_a_problem_naming_the_dirs(tmp_path, no_apptainer):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    v = verify_named_image(NAME, [a, b])
    assert not v.ok and not v.exists and v.path is None
    assert v.problems == [f"{NAME} not found in: {a}, {b}"]


def test_second_dir_hit_reports_that_path(store, tmp_path, no_apptainer):
    empty = tmp_path / "empty"
    empty.mkdir()
    v = verify_named_image(NAME, [empty, store])
    assert v.ok and v.path == str(store / NAME) and v.found_in == [str(store)]
    assert v.dirs == [str(empty), str(store)]


def test_first_hit_wins_and_a_second_copy_is_a_warning(store, tmp_path, no_apptainer):
    other = tmp_path / "other"
    other.mkdir()
    (other / NAME).write_bytes(b"different bytes")
    (other / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("1" * 64)))
    v = verify_named_image(NAME, [store, other])
    assert v.ok and v.path == str(store / NAME) and v.found_in == [str(store), str(other)]
    assert any("2 dirs" in w for w in v.warnings)
    # The other order verifies the other copy — which does not match ITS receipt.
    v2 = verify_named_image(NAME, [other, store])
    assert not v2.ok and v2.path == str(other / NAME)


def test_committed_receipt_disagreeing_is_a_problem(store, no_apptainer):
    sha = hashlib.sha256(IMAGE_BYTES).hexdigest()
    committed = _receipt(sha)
    assert verify_named_image(NAME, [store], committed_receipt=committed).ok
    v = verify_named_image(NAME, [store], committed_receipt=_receipt("f" * 64))
    assert not v.ok and v.committed_receipt_ok is False
    assert any(RECEIPT_BASENAME in p and "sha256=" in p and "different build" in p for p in v.problems)
    v = verify_named_image(NAME, [store], committed_receipt=_receipt(sha, commit="d" * 40))
    assert not v.ok and any("commit=" in p for p in v.problems)


def test_labels_mismatch_is_a_problem(store, tmp_path, monkeypatch):
    d = _fake_apptainer(tmp_path, {"org.ragstack.version": "v9.9.9", "org.ragstack.commit": "e" * 40,
                                   "org.ragstack.build": "2"})
    monkeypatch.setenv("PATH", str(d))
    v = verify_named_image(NAME, [store])
    assert not v.ok and v.labels_checked and v.labels_ok is False and v.sha256_ok is True
    msgs = "\n".join(v.problems)
    assert "label org.ragstack.commit" in msgs and "label org.ragstack.build" in msgs
    assert "label org.ragstack.version" not in msgs


def test_apptainer_failing_on_the_file_is_a_problem(store, tmp_path, monkeypatch):
    d = _fake_apptainer(tmp_path, {}, rc=255)
    monkeypatch.setenv("PATH", str(d))
    v = verify_named_image(NAME, [store])
    assert not v.ok and v.labels_checked and v.labels_ok is False
    assert any("labels unreadable" in p and "exited 255" in p and "not a SIF" in p for p in v.problems)


def test_apptainer_absent_is_a_warning_not_a_failure(store, no_apptainer):
    v = verify_named_image(NAME, [store])
    assert v.ok and v.state == "ok"
    assert v.labels_checked is False and v.labels_ok is None and v.labels is None
    assert any("labels not verified" in w and "apptainer" in w for w in v.warnings)
    assert v.sha256_ok is True


def test_unstamped_name_is_unstamped_not_a_failure(tmp_path, no_apptainer):
    v = verify_named_image(DEFAULT_TOOL_IMAGE, [tmp_path])
    assert v.state == "unstamped" and v.ok and not v.exists and v.problems == []
    # ... whatever the dirs say, even none.
    assert verify_named_image(DEFAULT_TOOL_IMAGE, []).state == "unstamped"


def test_a_name_that_is_neither_is_a_problem(tmp_path, no_apptainer):
    v = verify_named_image("other.sif", [tmp_path])
    assert v.state == "problem" and any("neither" in p for p in v.problems)


def test_no_dirs_is_unchecked_with_a_warning(no_apptainer):
    v = verify_named_image(NAME, [])
    assert v.state == "unchecked" and v.ok and v.problems == []
    assert any("GOWE_IMAGE_DIRS" in w for w in v.warnings)


def test_summary_names_the_path_and_each_problem(store, no_apptainer):
    (store / (NAME + ".receipt.json")).unlink()
    s = verify_named_image(NAME, [store]).summary()
    assert s.startswith(f"{NAME}: problem ({store / NAME})")
    assert "problem: no receipt beside the image" in s
    assert "warning: labels not verified" in s


def test_parse_image_dirs():
    assert parse_image_dirs("") == [] and parse_image_dirs(None) == []
    assert parse_image_dirs(" /a , /b,, ") == [Path("/a"), Path("/b")]


def test_sha256_is_streamed_and_cached(tmp_path):
    p = tmp_path / "x.sif"
    p.write_bytes(IMAGE_BYTES)
    _SHA256_CACHE.clear()
    assert file_sha256(p) == hashlib.sha256(IMAGE_BYTES).hexdigest()
    st = p.stat()
    assert (str(p), st.st_size, st.st_mtime_ns) in _SHA256_CACHE
    # A rewrite with a new mtime/size is hashed afresh; the old key stays harmless.
    p.write_bytes(b"other")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert file_sha256(p) == hashlib.sha256(b"other").hexdigest()


# --- the boot gate -------------------------------------------------------- #

def _stamped_cwl(dir_: Path, store: Path | None, *, name: str = NAME, committed: bool = True) -> Path:
    """A stamped CWL in ``dir_`` and, by default, a committed receipt beside
    it that agrees with the store's."""
    dir_.mkdir(parents=True, exist_ok=True)
    cwl = dir_ / "pdf-ingest-scatter.cwl"
    cwl.write_text(stamp_tool_image(CWL, name))
    if committed and store is not None:
        receipt = json.loads((store / (name + ".receipt.json")).read_text())
        (dir_ / RECEIPT_BASENAME).write_text(json.dumps(receipt))
    return cwl


@pytest.fixture
def gowe_boot(monkeypatch, tmp_path):
    """The boot gate's preconditions, with every registered CWL pointed at a
    temp file so no repo copy is read."""
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    monkeypatch.setattr(deps.settings, "gowe_tool_image", "")
    monkeypatch.setattr(deps.settings, "ingest_backend", "gowe")
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", "")
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", "")
    monkeypatch.setattr(deps.settings, "graph_extract_cwl", str(tmp_path / "absent-graph.cwl"))
    monkeypatch.setattr(deps.settings, "collection_restore_cwl", str(tmp_path / "absent-restore.cwl"))
    import ragstack.tool_image as ti

    _SHA256_CACHE.clear()
    ti._LABELS_CACHE.clear()


def test_boot_passes_on_a_stamped_tree_with_a_matching_store(gowe_boot, store, tmp_path, no_apptainer, monkeypatch, caplog):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with caplog.at_level(logging.INFO, logger="ragstack.api.deps"):
        deps._validate_production_settings()
    assert any("verified at " + str(store / NAME) in r.message for r in caplog.records)


def test_boot_refuses_on_a_wrong_sha_receipt(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("0" * 64)))
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError) as info:
        deps._validate_production_settings()
    msg = str(info.value)
    assert "identity check FAILED" in msg and "ADR-0010" in msg and "Refusing to boot" in msg
    assert f"GOWE_WORKFLOW_CWL={cwl}" in msg
    assert NAME in msg and str(store / NAME) in msg
    assert "problem: sha256 mismatch" in msg
    # The committed receipt was written from the GOOD receipt, so it disagrees
    # with the store's now too — both findings are in the one message.
    assert "committed receipt" in msg


def test_boot_refuses_on_a_missing_image(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    (store / NAME).unlink()
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError, match="not found in: " + str(store)):
        deps._validate_production_settings()


def test_boot_refuses_on_a_committed_receipt_that_disagrees(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    (tmp_path / "cwl" / RECEIPT_BASENAME).write_text(json.dumps(_receipt("a" * 64)))
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError, match="committed receipt"):
        deps._validate_production_settings()


def test_boot_warns_and_boots_with_dirs_unset(gowe_boot, store, tmp_path, no_apptainer, monkeypatch, caplog):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("0" * 64)))  # would refuse if seen
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    with caplog.at_level(logging.WARNING, logger="ragstack.api.deps"):
        deps._validate_production_settings()
    warned = [r for r in caplog.records if "GOWE_IMAGE_DIRS is unset" in r.message]
    assert warned and warned[0].levelno == logging.WARNING and NAME in warned[0].message


def test_boot_skips_an_unstamped_cwl(gowe_boot, store, tmp_path, no_apptainer, monkeypatch, caplog):
    cwl = tmp_path / "cwl" / "pdf-ingest-scatter.cwl"
    cwl.parent.mkdir()
    cwl.write_text(CWL)  # dockerPull: ragstack-worker.sif
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(tmp_path / "no-such-store"))
    with caplog.at_level(logging.INFO, logger="ragstack.api.deps"):
        deps._validate_production_settings()
    assert any("unstamped tree, identity check skipped" in r.message for r in caplog.records)


def test_boot_runs_no_check_on_the_local_backend(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("0" * 64)))
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    monkeypatch.setattr(deps.settings, "ingest_backend", "local")
    deps._validate_production_settings()


def test_boot_checks_the_graph_and_restore_defaults_too(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    """The two registrars with a repo-copy default are read from their
    configured path; a stamped graph CWL over a bad store refuses the boot
    even when the ingest CWL is unstamped."""
    ingest = tmp_path / "cwl" / "pdf-ingest-scatter.cwl"
    ingest.parent.mkdir()
    ingest.write_text(CWL)
    graph = _stamped_cwl(tmp_path / "cwl2", store)
    (store / NAME).write_bytes(b"swapped")
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(ingest))
    monkeypatch.setattr(deps.settings, "graph_extract_cwl", str(graph))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError, match=r"(?s)GRAPH_EXTRACT_CWL=.*sha256 mismatch"):
        deps._validate_production_settings()


def test_boot_hashes_a_shared_image_once(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    """Three registrars naming one image: a verdict per CWL (each has its own
    committed receipt to compare), but the 250 MB file is hashed once."""
    import ragstack.tool_image as ti

    a = _stamped_cwl(tmp_path / "a", store)
    b = _stamped_cwl(tmp_path / "b", store)
    c = _stamped_cwl(tmp_path / "c", store)
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(a))
    monkeypatch.setattr(deps.settings, "graph_extract_cwl", str(b))
    monkeypatch.setattr(deps.settings, "collection_restore_cwl", str(c))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    calls = []
    real = ti.verify_named_image

    def counting(*args, **kw):
        calls.append(args[0])
        return real(*args, **kw)

    monkeypatch.setattr(ti, "verify_named_image", counting)
    opened = []
    real_open = Path.open

    def counting_open(self, *a, **kw):
        if self.name == NAME and (a and "b" in a[0]):
            opened.append(str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", counting_open)
    deps._validate_production_settings()
    assert calls == [NAME] * 3
    assert len(opened) == 1


# --- the CLI the ctl shells to --------------------------------------------- #

def _cli(*args: str, env_path: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": env_path,
           "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    return subprocess.run([sys.executable, "-m", "ragstack.tool_image", "verify", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def test_cli_verify_name_and_cwl(store, tmp_path, no_apptainer):
    path = os.environ["PATH"]
    r = _cli("--name", NAME, "--dirs", f"{tmp_path / 'nope'},{store}", "--json", env_path=path)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["ok"] is True and doc["records"][0]["verdict"]["path"] == str(store / NAME)
    assert doc["records"][0]["verdict"]["labels_checked"] is False

    cwl = _stamped_cwl(tmp_path / "cwl", store)
    r = _cli("--cwl", str(cwl), "--dirs", str(store), "--json", env_path=path)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)["records"][0]
    assert rec["cwl"] == str(cwl) and rec["tool_image"] == NAME
    assert rec["text_sha256"] == hashlib.sha256(cwl.read_bytes()).hexdigest()
    assert rec["verdict"]["committed_receipt_ok"] is True

    # A problem: exit 1, still JSON (the ctl reads the records).
    (store / (NAME + ".receipt.json")).write_text(json.dumps(_receipt("0" * 64)))
    r = _cli("--cwl", str(cwl), "--dirs", str(store), "--json", env_path=path)
    assert r.returncode == 1
    doc = json.loads(r.stdout)
    assert doc["ok"] is False and any("sha256 mismatch" in p for p in doc["records"][0]["verdict"]["problems"])

    # Text mode names what GoWe hashes; usage is 2.
    r = _cli("--cwl", str(cwl), "--dirs", str(store), env_path=path)
    assert r.returncode == 1 and "GoWe would content-hash this" in r.stdout and "problem: sha256" in r.stdout
    assert _cli("--dirs", str(store), env_path=path).returncode == 2


def test_verify_cwl_file_on_a_document_without_an_image(tmp_path, no_apptainer):
    p = tmp_path / "x.cwl"
    p.write_text("cwlVersion: v1.2\nclass: Workflow\ninputs: {}\nsteps: {}\n")
    rec = verify_cwl_file(p, [tmp_path])
    assert rec["tool_image"] is None and rec["verdict"]["state"] == "unstamped"
    assert rec["verdict"]["problems"] == []
    assert isinstance(ImageVerdict("", []).to_dict()["problems"], list)


# --- review of #672: one implementation, per-CWL committed receipt, dedupe --- #

def test_boot_refuses_a_mixed_stamped_document(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    """A stamped CWL with ONE site changed to a different stamped name: the
    document names two images. `tool_image_of` returns None, which the first
    cut of the boot logged as "skipped" and BOOTED — while `verify --cwl` (the
    ctl) said `problem`. Decision 7 says refuse, and the boot now goes through
    the same `verify_cwl_file` the ctl does."""
    cwl = _stamped_cwl(tmp_path / "cwl", store)
    other = "ragstack-tools-v9.9.9-b2.sif"  # a stamped name nothing in the store answers to
    cwl.write_text(cwl.read_text() + f"""\
  b:
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: {other}
          dockerImageId: {other}
""")
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError, match=r"(?s)GOWE_WORKFLOW_CWL=.*names 2 distinct images") as info:
        deps._validate_production_settings()
    assert "Refusing to boot" in str(info.value)


def test_boot_reaches_the_unstamped_branch_of_the_check(gowe_boot, tmp_path, no_apptainer, monkeypatch):
    """The unstamped boot path is `verify_named_image`'s own unstamped branch,
    not a short-circuit in deps: break that branch and the boot refuses."""
    import ragstack.tool_image as ti

    cwl = tmp_path / "cwl" / "pdf-ingest-scatter.cwl"
    cwl.parent.mkdir()
    cwl.write_text(CWL)
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(cwl))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(tmp_path))
    seen = []
    real = ti.verify_named_image

    def spy(name, dirs, **kw):
        seen.append(name)
        return real(name, dirs, **kw)

    monkeypatch.setattr(ti, "verify_named_image", spy)
    deps._validate_production_settings()
    assert seen == [DEFAULT_TOOL_IMAGE]
    # Mutation in-process: an unstamped name treated as a stamped one is a
    # missing file, and the boot must refuse it.
    monkeypatch.setattr(ti, "DEFAULT_TOOL_IMAGE", "something-else.sif")
    with pytest.raises(RuntimeError, match="neither"):
        deps._validate_production_settings()


def test_boot_compares_the_committed_receipt_per_cwl(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    """Two registered CWLs naming ONE image from different dirs: the graph
    CWL's committed receipt is tampered, the ingest CWL's is fine. A verdict
    cached by image name alone would compare only the first dir's receipt and
    boot; the ctl says `problem`. Both must refuse."""
    a = _stamped_cwl(tmp_path / "a", store)
    b = _stamped_cwl(tmp_path / "b", store)
    (tmp_path / "b" / RECEIPT_BASENAME).write_text(json.dumps(_receipt("f" * 64)))
    monkeypatch.setattr(deps.settings, "gowe_workflow_cwl", str(a))
    monkeypatch.setattr(deps.settings, "graph_extract_cwl", str(b))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError) as info:
        deps._validate_production_settings()
    msg = str(info.value)
    assert f"GRAPH_EXTRACT_CWL={b}" in msg and "committed receipt" in msg
    assert f"GOWE_WORKFLOW_CWL={a}" not in msg  # the good one is not blamed


def test_boot_refusal_names_each_registrar_once_per_finding(gowe_boot, store, tmp_path, no_apptainer, monkeypatch):
    """Three registrars, one broken image: the finding is printed once, with
    all three settings in front of it, not three times."""
    a = _stamped_cwl(tmp_path / "a", store)
    b = _stamped_cwl(tmp_path / "b", store)
    c = _stamped_cwl(tmp_path / "c", store)
    (store / NAME).write_bytes(b"swapped")
    for k, p in (("gowe_workflow_cwl", a), ("graph_extract_cwl", b), ("collection_restore_cwl", c)):
        monkeypatch.setattr(deps.settings, k, str(p))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    with pytest.raises(RuntimeError) as info:
        deps._validate_production_settings()
    msg = str(info.value)
    assert msg.count("problem: sha256 mismatch") == 1
    assert f"GOWE_WORKFLOW_CWL={a}, GRAPH_EXTRACT_CWL={b}, COLLECTION_RESTORE_CWL={c}" in msg


def test_boot_inspects_a_shared_image_once(gowe_boot, store, tmp_path, good_apptainer, monkeypatch):
    """Labels are cached per image file like the sha256: three CWLs, one
    `apptainer inspect`."""
    import ragstack.tool_image as ti

    ti._LABELS_CACHE.clear()
    calls = []
    real = ti._read_image_labels

    def counting(path, apptainer="apptainer"):
        calls.append(str(path))
        return real(path, apptainer)

    monkeypatch.setattr(ti, "_read_image_labels", counting)
    exe = tmp_path / "bin" / "apptainer"
    exe.write_text(exe.read_text().replace("exit 0", "echo ran >> \"${0%/*}/calls\"\nexit 0"))
    for k, d in (("gowe_workflow_cwl", "a"), ("graph_extract_cwl", "b"), ("collection_restore_cwl", "c")):
        monkeypatch.setattr(deps.settings, k, str(_stamped_cwl(tmp_path / d, store)))
    monkeypatch.setattr(deps.settings, "gowe_image_dirs", str(store))
    deps._validate_production_settings()
    assert len(calls) == 3  # asked three times ...
    assert (tmp_path / "bin" / "calls").read_text().count("ran") == 1  # ... inspected once
