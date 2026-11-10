"""``ops/coconut/qdrant-census.sh`` — the verification primitive of the qdrant
data-dir migration (docs/runbooks/qdrant-data-dir.md).

The script is run against canned qdrant answers: a stub ``curl`` first on PATH
serves a JSON table keyed by ``METHOD URL`` and logs every call, and the real
``jq`` does the shaping (the test is skipped where there is none). What is
asserted is what the runbook relies on: the exact count is the one reported,
the output is byte-stable for the same store whatever order qdrant lists it
in, every call is a read, and any HTTP error makes the exit status nonzero.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[3] / "ops" / "coconut"
SCRIPT = OPS / "qdrant-census.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash and jq",
)

STUB_CURL = r'''#!PYTHON
import json, os, sys
args = sys.argv[1:]
method, data, url = "GET", None, None
i = 0
while i < len(args):
    a = args[i]
    if a == "-X":
        method = args[i + 1]; i += 2; continue
    if a in ("--data", "-d"):
        data = args[i + 1]; i += 2; continue
    if a in ("-m", "-H", "-w"):
        i += 2; continue
    if a.startswith("-"):
        i += 1; continue
    url = a; i += 1
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(json.dumps({"method": method, "url": url, "data": data}) + "\n")
table = json.load(open(os.environ["CURL_TABLE"]))
key = method + " " + url
if key not in table:
    sys.stderr.write("curl: (7) Failed to connect\n")
    sys.exit(7)
code, body = table[key]
sys.stdout.write(json.dumps(body) + "\n" + str(code))
'''


def _info(points: int, segments: int, size: int) -> dict:
    return {"result": {"status": "green", "points_count": points, "segments_count": segments,
                       "config": {"params": {"vectors": {"size": size, "distance": "Cosine"}}}}}


def _table(base: str, order: tuple[str, ...] = ("chunks", "docs")) -> dict:
    return {
        f"GET {base}/collections": [200, {"result": {"collections": [{"name": n} for n in order]}}],
        f"GET {base}/aliases": [200, {"result": {"aliases": [
            {"alias_name": "live", "collection_name": "chunks"},
            {"alias_name": "a-old", "collection_name": "chunks"},
        ]}}],
        # The info estimate deliberately disagrees with the exact count.
        f"GET {base}/collections/chunks": [200, _info(87_990, 6, 768)],
        f"POST {base}/collections/chunks/points/count": [200, {"result": {"count": 88_000}}],
        f"GET {base}/collections/docs": [200, _info(1_200, 2, 4096)],
        f"POST {base}/collections/docs/points/count": [200, {"result": {"count": 1_200}}],
    }


def _run(tmp_path: Path, table: dict, *args: str) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    stub = tmp_path / "stub-bin"
    stub.mkdir(exist_ok=True)
    curl = stub / "curl"
    curl.write_text(STUB_CURL.replace("PYTHON", sys.executable, 1))
    curl.chmod(0o755)
    tab = tmp_path / "table.json"
    tab.write_text(json.dumps(table))
    log = tmp_path / "curl.log"
    log.write_text("")
    env = dict(os.environ, PATH=f"{stub}:{os.environ.get('PATH', '')}", CURL_TABLE=str(tab),
               CURL_LOG=str(log), JQ=shutil.which("jq") or "jq")
    env.pop("CURL", None)
    out = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
    calls = [json.loads(line) for line in log.read_text().splitlines() if line]
    return out, calls


def test_census_reports_exact_counts_vectors_and_aliases(tmp_path):
    base = "http://127.0.0.1:24041"
    out, calls = _run(tmp_path, _table(base), "24041")
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc == [{
        "endpoint": base,
        "collections": [
            {"name": "chunks", "status": "green", "points_count": 88_000, "segments_count": 6,
             "vectors": {"distance": "Cosine", "size": 768}, "sparse_vectors": None,
             "aliases": ["a-old", "live"]},
            {"name": "docs", "status": "green", "points_count": 1_200, "segments_count": 2,
             "vectors": {"distance": "Cosine", "size": 4096}, "sparse_vectors": None, "aliases": []},
        ],
    }]
    # Every call is a read; the only POST is the exact count.
    for c in calls:
        assert c["method"] in ("GET", "POST"), c
        if c["method"] == "POST":
            assert c["url"].endswith("/points/count") and json.loads(c["data"]) == {"exact": True}, c


def test_census_is_byte_stable_whatever_order_qdrant_lists(tmp_path):
    base = "http://127.0.0.1:24041"
    a, _ = _run(tmp_path, _table(base, ("chunks", "docs")), "24041")
    b, _ = _run(tmp_path, _table(base, ("docs", "chunks")), "24041")
    assert a.returncode == b.returncode == 0, a.stderr + b.stderr
    assert a.stdout == b.stdout
    # Keys are sorted (jq -S), so a diff of two censuses is a diff of facts.
    assert a.stdout.index('"aliases"') < a.stdout.index('"name"') < a.stdout.index('"points_count"')


def test_census_accepts_urls_and_several_endpoints(tmp_path):
    t = _table("http://127.0.0.1:6333")
    t.update(_table("http://127.0.0.1:24041"))
    out, _ = _run(tmp_path, t, "http://127.0.0.1:6333/", "24041")
    assert out.returncode == 0, out.stderr
    assert [d["endpoint"] for d in json.loads(out.stdout)] == ["http://127.0.0.1:6333", "http://127.0.0.1:24041"]


@pytest.mark.parametrize("broken", [
    "GET {b}/collections",
    "GET {b}/aliases",
    "GET {b}/collections/docs",
    "POST {b}/collections/docs/points/count",
])
def test_an_http_error_exits_nonzero(tmp_path, broken):
    base = "http://127.0.0.1:24041"
    t = _table(base)
    t[broken.format(b=base)] = [500, {"status": {"error": "boom"}}]
    # A second, healthy endpoint is still reported.
    t.update(_table("http://127.0.0.1:6333"))
    out, _ = _run(tmp_path, t, "24041", "6333")
    assert out.returncode == 1, out.stdout
    assert "HTTP 500" in out.stderr
    doc = json.loads(out.stdout)
    assert "error" in doc[0] and "collections" not in doc[0]
    assert doc[1]["collections"], doc


def test_a_connection_failure_exits_nonzero(tmp_path):
    out, _ = _run(tmp_path, {}, "24041")
    assert out.returncode == 1
    assert "curl failed" in out.stderr


@pytest.mark.parametrize("arg", ["", "0", "70000", "localhost:6333", "file:///etc/passwd", "http://h/;rm"])
def test_a_bad_argument_exits_2(tmp_path, arg):
    out, calls = _run(tmp_path, {}, arg)
    assert out.returncode == 2, out
    assert calls == []


def test_no_arguments_prints_usage(tmp_path):
    out, _ = _run(tmp_path, {})
    assert out.returncode == 2
    assert "qdrant-census.sh <port-or-url>" in out.stderr


def test_the_script_sources_nothing_and_sets_pipefail():
    text = SCRIPT.read_text()
    code = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    assert "set -uo pipefail" in code
    assert not [l for l in code if l.lstrip().startswith(("source ", ". "))]
