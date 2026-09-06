"""Shared plumbing for Stage 0 of the confirmation run (SPEC-confirmation-run.md).

Reuses, never re-declares:

* ``../stage1/stage1_common.py``  -- ``pin_repo``, ``Fleet``, ``atomic_json``, ``doc_text``
* ``../pilots/pilot_common.py``   -- ``units_for_article``: the structural-unit split (D1's
  basis) over exactly the indexed ``doc_text``.

**Zero store writes.** No Qdrant/Elasticsearch/Neo4j client is constructed here or in
anything this imports. ``:6333`` / ``:9200`` / ``:24041`` / ``:24043`` are never contacted.
Retrieval is exact brute-force cosine over in-memory embeddings; the only HTTP callers are
the SFR embedding fleet on ``:9001-:9006`` (<=2 in flight per endpoint), the crossencoder
on ``:50052`` (pipeline reranker only -- it contributes NOTHING to gold), and
``mango:8003`` (Llama-4-Scout, the labeler, <=4 concurrent).

**GPUs 6 and 7 are RESERVED**: no endpoint is started, and nothing here selects a device.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
PHASE0 = HERE.parent
DESIGN = PHASE0.parent / "design"
STAGE1 = PHASE0 / "stage1"
PILOTS = PHASE0 / "pilots"
CDS = PHASE0 / "cds"
STEP2 = PHASE0 / "step2"

# Large artifacts live off the NFS home (MEMORY: /home is space-constrained).
BIG = pathlib.Path(os.environ.get("STAGE0_BIG", "/rag/tmp/stage0-conf"))
XML = BIG / "xml"
CHUNKS = BIG / "chunks"
EMB = BIG / "emb"
WORK = BIG / "work"
for _d in (BIG, XML, CHUNKS, EMB, WORK):
    _d.mkdir(parents=True, exist_ok=True)

for _p in (str(STAGE1), str(PILOTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stage1_common as S  # noqa: E402

S.pin_repo()

import pilot_common as P  # noqa: E402  (imported for units_for_article)

REPO = "/home/wilke/Development/ragstack"
# P.1: run at the brief's commit. d225cea (which every Phase-0 artifact pins) is its
# direct parent, verified with `git merge-base --is-ancestor`.
EXPECT_COMMIT = "55a0fc2f6bf64e592c2c65d8825524216c423e2b"

# ---------------------------------------------------------------- seeds (P.1)
SEED_GRADE0_DEV = 20260904      # reproduces the step-2 pilot corpus byte-for-byte
SEED_GRADE0_CONF = 20260912     # the 80 confirmation topics
SEED_UNITCAP = 20260912         # D3 rule 4 stratified subsample
SEED_BOOT = 20260913            # bootstrap / permutation
SEED_LABELDUP = 20260914        # 10% self-consistency duplicates
SEED_RDEV = 20260915            # the R-dev stratified human-read draw
SEED_BIASBOUND = 20260916       # the 10-per-topic out-of-pool sample

DEV_TOPICS = ["2014_5", "2014_11", "2014_29", "2015_8", "2015_18", "2015_23",
              "2016_1", "2016_9", "2016_13", "2016_26"]

# ---------------------------------------------------------------- arms (P.3)
# (key, kind, tokens, overlap_tokens, header?)  -- budget_mode pinned "joined" (P.1)
INDEX_ARMS = [
    ("fixed_tok256_ov0pct", 256, 0, False),
    ("fixed_tok512_ov0pct", 512, 0, False),
    ("fixed_tok1024_ov0pct", 1024, 0, False),
    ("fixed_tok2048_ov0pct", 2048, 0, False),
    ("fixed_tok512", 512, 64, False),          # shipping control, 12.5% overlap
    ("header512", 512, 0, True),               # defect 7: contextual chunk headers
]
INDEX_KEYS = [a[0] for a in INDEX_ARMS]
SCORING_KEYS = INDEX_KEYS + ["parent256"]
BUDGET_MODE = "joined"                          # P.1: pinned, inert for token_window

# ---------------------------------------------------------------- endpoints
SFR_PORTS = list(range(9001, 9007))             # GPUs 6/7 reserved: never 9007+
SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"
RERANK_URL = "http://localhost:50052"
RERANK_EXPECT = "BAAI/bge-reranker-v2-m3"
MANGO = "http://mango.cels.anl.gov:8003"
SCOUT_EXPECT = "RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic"
MANGO_QWEN = "http://mango.cels.anl.gov:8004"
QWEN_EXPECT = "Qwen/Qwen3.6-35B-A3B"

# ---------------------------------------------------------------- pipeline (P.4)
DEPTH = 50                 # = rerank_candidates; full-pool rerank, never per-doc
BUDGETS = (2048, 4096, 8192)
PRIMARY_BUDGET = 4096
UNIT_CAP = 12              # D3 rule 4
JACCARD_MERGE = 0.5        # D3 rule 1 / SS6.4 rule 4 -- one number governs both
EPS = 0.05                 # SS8.2 margin -- never moves


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def atomic_json(path, obj):
    return S.atomic_json(pathlib.Path(path), obj)


def _get_json(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def served_ids() -> dict:
    """Probe every model identity LIVE (P.1). Aborts on any mismatch."""
    out = {}
    for p in SFR_PORTS:
        mid = _get_json(f"http://localhost:{p}/v1/models")["data"][0]["id"]
        if mid != SFR_MODEL:
            raise SystemExit(f":{p} serves {mid!r}, not {SFR_MODEL!r}")
        out[f"sfr:{p}"] = mid
    mid = _get_json(MANGO + "/v1/models")["data"][0]["id"]
    if mid != SCOUT_EXPECT:
        raise SystemExit(f"mango:8003 serves {mid!r}, not {SCOUT_EXPECT!r}")
    out["labeler:mango8003"] = mid
    try:
        out["judge2:mango8004"] = _get_json(MANGO_QWEN + "/v1/models")["data"][0]["id"]
    except Exception as e:  # noqa: BLE001
        out["judge2:mango8004"] = f"UNAVAILABLE {type(e).__name__}"
    for path in ("/health", "/healthz", "/v1/models", "/info"):
        try:
            out["reranker:50052"] = {"path": path, "body": _get_json(RERANK_URL + path)}
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        out["reranker:50052"] = "NO IDENTITY ENDPOINT FOUND"
    return out


def gpu_snapshot() -> list[dict]:
    q = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,memory.used,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True)
    rows = []
    for line in q.stdout.strip().splitlines():
        i, m, u = (x.strip() for x in line.split(","))
        rows.append({"gpu": int(i), "mem_used_mib": int(m), "util_pct": int(u)})
    return rows


def provenance(extra: dict | None = None) -> dict:
    import numpy
    import ragstack
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != EXPECT_COMMIT:
        raise SystemExit(f"repo HEAD {head} != expected {EXPECT_COMMIT}")
    dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                           capture_output=True, text=True, check=True).stdout.strip()
    pkgs = {}
    for name in ("numpy", "httpx", "transformers", "tokenizers", "scipy"):
        try:
            mod = __import__(name)
            pkgs[name] = getattr(mod, "__version__", "?")
        except Exception:  # noqa: BLE001
            pkgs[name] = "ABSENT"
    return {
        "commit": head,
        "commit_expected": EXPECT_COMMIT,
        "git_status_porcelain": dirty.splitlines(),
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "HF_HOME": os.environ.get("HF_HOME"),
        "ragstack_file": ragstack.__file__,
        "meta_path": [type(f).__module__ + "." + type(f).__name__ for f in sys.meta_path],
        "numpy": numpy.__version__,
        "packages": pkgs,
        "budget_mode_pinned": BUDGET_MODE,
        "seeds": {"grade0_dev": SEED_GRADE0_DEV, "grade0_conf": SEED_GRADE0_CONF,
                  "unitcap": SEED_UNITCAP, "bootstrap": SEED_BOOT,
                  "label_dup": SEED_LABELDUP, "rdev_draw": SEED_RDEV,
                  "bias_bound": SEED_BIASBOUND},
        **(extra or {}),
    }


# ---------------------------------------------------------------- generator tokenizer
class GenTokenizer:
    """Budget tokens = the SERVED generator's tokenizer, probed live (SS7.2, P.1).

    vLLM's ``/tokenize`` is the authority; no local tokenizer file is trusted. The
    ``add_special_tokens`` choice is pinned here and recorded in the manifest: budgets
    count the CHUNK's own supplied text, so no BOS is charged per chunk.
    """

    ADD_SPECIAL = False

    def __init__(self, base: str = MANGO, model: str = SCOUT_EXPECT):
        import httpx
        self.base, self.model = base, model
        self.client = httpx.Client(timeout=300)
        self.cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        h = hashlib.blake2b(text.encode(), digest_size=16).hexdigest()
        v = self.cache.get(h)
        if v is None:
            r = self.client.post(self.base + "/tokenize", json={
                "model": self.model, "prompt": text,
                "add_special_tokens": self.ADD_SPECIAL})
            r.raise_for_status()
            v = int(r.json()["count"])
            self.cache[h] = v
        return v

    def counts(self, texts):
        return [self.count(t) for t in texts]
