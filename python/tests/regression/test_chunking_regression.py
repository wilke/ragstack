"""Chunking regression: the CURRENT code against frozen 55a0fc2 goldens.

The goldens (``RAGSTACK_REGRESSION_DIR``, default ``/rag/snapshots/regression/v1``) are
50 stage0 dev-10 documents chunked at tag ``exp/stage0-55a0fc2``. They were proven
byte-equal to the stage0 run's own ``spans_*.jsonl`` rows. This test reruns the same
inputs through today's ``FixedTokenWindowChunker`` (the 6 stage0 index arms) and
``sentence_spans`` (the 50 docs plus ``canonical-1``), and compares the result
byte-for-byte. See ``README.md`` in this directory for what to do on a mismatch.

Skip/fail doctrine (root ``conftest.py``):
- the goldens directory is absent (CI, other hosts): **skip**;
- the tokenizer can't load offline: **skip**;
- the goldens' checksums or the tokenizer revision are wrong: **fail**, naming both sides.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.regression

DEFAULT_DIR = "/rag/snapshots/regression/v1"
REG_DIR = pathlib.Path(os.environ.get("RAGSTACK_REGRESSION_DIR", DEFAULT_DIR))
HERE = pathlib.Path(__file__).resolve().parent

if not (REG_DIR / "MANIFEST.json").is_file():
    pytest.skip(
        f"chunking regression goldens not found at {REG_DIR} "
        f"(set RAGSTACK_REGRESSION_DIR to a goldens directory to run this)",
        allow_module_level=True)

MANIFEST = json.loads((REG_DIR / "MANIFEST.json").read_text())
ARM_KEYS = [a["key"] for a in MANIFEST["arms"]]


def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _golden_lines(rel: str) -> list[str]:
    return (REG_DIR / rel).read_text(encoding="utf-8").splitlines()


def _env_note(env: dict) -> str:
    """Environment versus the manifest. A version difference is a lead, not a verdict."""
    pinned = MANIFEST["env"]
    rows = []
    for k in ("nltk", "transformers", "tokenizers", "sentence_backend"):
        mark = "" if env.get(k) == pinned.get(k) else "   <-- differs"
        rows.append(f"  {k}: golden={pinned.get(k)} current={env.get(k)}{mark}")
    return "environment (golden vs current):\n" + "\n".join(rows)


@pytest.fixture(scope="module")
def current(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run ``_chunk_regen.py`` once with the current checkout, offline tokenizer."""
    import ragstack

    py_root = pathlib.Path(ragstack.__file__).resolve().parents[1]
    out = tmp_path_factory.mktemp("chunk-regression") / "current.json"
    hf_home = os.environ.get("RAGSTACK_REGRESSION_HF_HOME") or str(
        REG_DIR / MANIFEST["tokenizer"]["hf_home"])
    env = {**os.environ, "PYTHONPATH": str(py_root), "HF_HOME": hf_home,
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    proc = subprocess.run(
        [sys.executable, str(HERE / "_chunk_regen.py"), str(REG_DIR), str(out)],
        env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode == 3:
        why = (proc.stderr.strip().splitlines() or [""])[-1]
        pytest.skip(f"tokenizer {MANIFEST['tokenizer']['model']} not available offline "
                    f"under HF_HOME={hf_home} (override with RAGSTACK_REGRESSION_HF_HOME): "
                    f"{why[:300]}")
    if proc.returncode != 0:
        pytest.fail(f"_chunk_regen.py crashed (exit {proc.returncode}) running the current "
                    f"code:\n{proc.stderr[-4000:]}")
    res = json.loads(out.read_text())
    got = res["env"]["ragstack_file"]
    if pathlib.Path(got).resolve() != pathlib.Path(ragstack.__file__).resolve():
        pytest.fail(f"subprocess imported ragstack from {got}, pytest from "
                    f"{ragstack.__file__}: they are not the same checkout")
    want_rev = MANIFEST["tokenizer"]["revision"]
    if res["env"]["tokenizer_revision"] != want_rev:
        pytest.fail(f"tokenizer revision under HF_HOME={hf_home} is "
                    f"{res['env']['tokenizer_revision']}, goldens were made with {want_rev}. "
                    f"This is an environment difference, not a code regression.")
    return res


def test_goldens_checksums() -> None:
    bad = []
    for rel, digest in MANIFEST["files"].items():
        p = REG_DIR / rel
        got = _sha256(p) if p.is_file() else "MISSING"
        if got != digest:
            bad.append(f"  {rel}: manifest={digest} actual={got}")
    assert not bad, f"goldens under {REG_DIR} do not match MANIFEST.json:\n" + "\n".join(bad)


def _first_span_diff(golden: dict, cur: dict) -> str:
    gs, cs = golden["spans"], cur["spans"]
    for i in range(max(len(gs), len(cs))):
        g = gs[i] if i < len(gs) else None
        c = cs[i] if i < len(cs) else None
        if g != c:
            return (f"{len(gs)} golden spans vs {len(cs)} current; first differing span "
                    f"#{i}: golden {g} current {c}")
    gh, ch = golden.get("hdr"), cur.get("hdr")
    if gh != ch:
        gh, ch = gh or [], ch or []
        for i in range(max(len(gh), len(ch))):
            g = gh[i] if i < len(gh) else None
            c = ch[i] if i < len(ch) else None
            if g != c:
                return f"spans equal; first differing hdr #{i}: golden {g!r} current {c!r}"
    return "rows parse equal but differ as bytes (serialisation or key order)"


@pytest.mark.parametrize("arm", ARM_KEYS)
def test_chunk_spans_match_goldens(arm: str, current: dict) -> None:
    got = current["spans"][arm]
    golden = {json.loads(line)["docno"]: line for line in
              _golden_lines(f"goldens/spans_{arm}.jsonl")}
    assert set(got) == set(golden), (
        f"arm {arm}: doc set differs: only golden {sorted(set(golden) - set(got))}, "
        f"only current {sorted(set(got) - set(golden))}")
    diffs = [f"  doc {d}: {_first_span_diff(json.loads(golden[d]), json.loads(got[d]))}"
             for d in golden if got[d] != golden[d]]
    assert not diffs, (
        f"arm {arm}: {len(diffs)}/{len(golden)} docs differ from the 55a0fc2 goldens "
        f"({REG_DIR}):\n" + "\n".join(diffs) + "\n" + _env_note(current["env"]))


def _sentence_preconditions(current: dict) -> None:
    if current["env"]["nltk"] is None:
        pytest.skip("nltk is not importable: sentence_spans would use the regex fallback; "
                    "the goldens are punkt (install the [chunking] extra)")
    assert current["env"]["sentence_backend"] == "punkt", (
        "nltk is importable but sentence_spans did not use the punkt backend\n"
        + _env_note(current["env"]))


def _sentence_diff(label: str, g: list, c: list) -> str:
    for i in range(max(len(g), len(c))):
        gi = g[i] if i < len(g) else None
        ci = c[i] if i < len(c) else None
        if gi != ci:
            return (f"  {label}: {len(g)} golden sentences vs {len(c)} current; "
                    f"first differing span #{i}: golden {gi} current {ci}")
    return f"  {label}: rows parse equal but differ as bytes"


def test_sentence_spans_docs_match_goldens(current: dict) -> None:
    _sentence_preconditions(current)
    got = current["sentences_docs"]
    golden = {json.loads(line)["docno"]: line for line in
              _golden_lines("goldens/sentence_spans_docs.jsonl")}
    assert set(got) == set(golden)
    diffs = [_sentence_diff(f"doc {d}", json.loads(golden[d])["spans"],
                            json.loads(got[d])["spans"])
             for d in golden if got[d] != golden[d]]
    assert not diffs, (f"sentence_spans: {len(diffs)}/{len(golden)} docs differ:\n"
                       + "\n".join(diffs) + "\n" + _env_note(current["env"]))


def test_sentence_spans_canonical_match_goldens(current: dict) -> None:
    _sentence_preconditions(current)
    got = current["sentences_canonical"]
    golden = _golden_lines("goldens/sentence_spans_canonical-1.jsonl")
    assert len(got) == len(golden)
    diffs = [_sentence_diff(f"canonical-1[{i}]", json.loads(g)["spans"],
                            json.loads(c)["spans"])
             for i, (g, c) in enumerate(zip(golden, got, strict=True)) if g != c]
    assert not diffs, (f"sentence_spans over canonical-1: {len(diffs)}/{len(golden)} differ:\n"
                       + "\n".join(diffs) + "\n" + _env_note(current["env"]))
