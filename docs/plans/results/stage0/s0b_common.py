"""Stage 0b' (prime) -- shared plumbing for the revision-3 calibration re-run.

Revision 3 (`../design/SPEC-confirmation-run-r3.md`) changes four things Stage 0b baked in,
and this module is where each change is pinned:

* **budgets and chunk sizes are counted in ONE tokenizer, the embedder's** (r3 SS3.3, the
  Stage 0b' path). The chunk arms already carry their SFR token counts in
  ``emb/rows_<arm>.json``; arbitrary slices (``parent256``) are counted with the SFR
  tokenizer loaded from the local HF cache. **The generator's tokenizer is ABSENT from
  ``/rag/cache``** -- ``Llama-4-Scout`` is not cached and r3's harness rules forbid
  contacting ``mango`` -- so generator tokens are reported as a *descriptive estimate*
  ``gen = sfr / 1.2564`` (r3 SS3.3's own ratio 2048/1630), never as a budget.
* **three retrieval modes** (``vector``/``bm25``/``hybrid``) at the served shape (r3 SS3.4).
* **two populations**: the ten CDS development topics x {summary, description}, and the
  177-query pointed development set (r3 SS11).
* **budgets** 4,096 / **16,384** / 32,768 SFR tokens (r3 SS3.2).

**Zero store writes, and only four endpoints.** ``:9001-:9006`` (SFR embeddings, <= 2 in
flight each, for the <= 200 query embeddings only), ``:50052`` (crossencoder, <= 4 in
flight), and -- for the SS3.4 concordance check alone, in ``s0b_es.py`` -- the **dev
tenant's** Elasticsearch at the URL in ``/rag/data/tenants/dev/config/tenant.env``. Qdrant,
``:6333``, ``:9200``, ``mango`` and every other tenant are never contacted. GPUs 6 and 7 are
untouched: no endpoint is started and no device is selected.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The Phase-0 helper modules (``stage1_common``, ``pilot_common``) and the CDS topic files
# live beside the working copy of the analysis tree, not in the repo (the same shim
# ``s0_label_r31.py`` uses).
HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (HELPERS / "stage1", HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C  # noqa: E402  (paths, seeds, arms, fleet constants)

for _name in ("CDS", "STAGE1", "PILOTS", "STEP2"):
    if not getattr(C, _name).exists():
        setattr(C, _name, HELPERS / _name.lower())

BIG = C.BIG
WORK = C.WORK
EMB = C.EMB
CHUNKS = C.CHUNKS
OUT = WORK / "stage0b-prime"          # the ONLY directory this run writes under BIG
CTX = OUT / "contexts"
for _d in (OUT, CTX):
    _d.mkdir(parents=True, exist_ok=True)

ART = HERE / "artifacts" / "stage0b-prime"
ART.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ populations
DEV_TOPICS = C.DEV_TOPICS
CDS_VARIANTS = ("summary", "description")
POINTED = WORK / "pointed" / "pointed-dev.jsonl"

# ------------------------------------------------------------------ arms / modes
INDEX_KEYS = C.INDEX_KEYS
SHIPPING = "fixed_tok512"
MODES = ("vector", "bm25", "hybrid")
RERANK_STATES = ("on", "off")

# scoring arms = the six index arms plus the r3 SS3.5 delivery arms
DELIVERY_ARMS = {
    # name              : (source index arm, kind, parameter)
    "parent256":        ("fixed_tok256_ov0pct", "parent", None),
    "nbr1_512":         ("fixed_tok512", "neighbour", 1),
    "nbr1_256":         ("fixed_tok256_ov0pct", "neighbour", 1),
    "nbr2_512":         ("fixed_tok512", "neighbour", 2),
}
MULTI_ARM = "multi256+1024"
MULTI_SOURCES = ("fixed_tok256_ov0pct", "fixed_tok1024_ov0pct")
SCORING_ARMS = INDEX_KEYS + list(DELIVERY_ARMS) + [MULTI_ARM]

# ------------------------------------------------------------------ pipeline (r3)
LEG_DEPTH = 100          # per-leg fetch: top_k(50) x candidate_multiplier(2) -- r3 SS3.4
DEPTH = 50               # pool after shaping/truncation = rerank_candidates
RRF_K = 60
BUDGETS = (4096, 16384, 32768)
PRIMARY_BUDGET = 16384
EPS = 0.05
WINDOW = (0.15, 0.90)
PARENT_MAX_SFR = 1024    # r3 keeps SS5.2's parent cap; Stage 0b' reads it in SFR tokens
UNIT_CAP_PER_DOC = 4     # r3 SS3.1: D3 rule 4 replaced by <= 4 units per DOCUMENT
SEED_UNITCAP_R3 = 20260918
SEED_BOOT = C.SEED_BOOT  # 20260913
CORE_SUPPORT = 0.5       # EPACK reading (b): "core" sentences
LONG_DOC_CHARS = 120_000 # #513: the compendium documents excluded from the pointed set

# BM25, pinned (r3 SS3.4)
BM25_K1 = 1.2
BM25_B = 0.75
BM25_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
BM25_PIN = {
    "k1": BM25_K1, "b": BM25_B,
    "tokenizer": r"lowercase; Unicode word boundaries via Python re '\w+' (re.UNICODE); "
                 "no stemming; no stopwords",
    "idf": "Lucene BM25: ln(1 + (N - df + 0.5)/(df + 0.5))",
    "query_semantics": "plain OR over query token OCCURRENCES (a repeated query token "
                       "scores twice, as a Lucene BooleanQuery with duplicate SHOULD "
                       "clauses does)",
    "avgdl": "exact mean chunk length in tokens over the arm's chunks",
    "known_departures_from_ES_standard": [
        "decimals: UAX#29 keeps '0.05' as one token; '\\w+' splits it into '0' and '05'",
        "apostrophes: UAX#29 keeps \"patient's\" as one token; '\\w+' splits it",
        "hyphens: both split, but UAX#29's handling of intra-word '-' differs on "
        "letter-digit boundaries",
        "length norm: Lucene stores a LOSSY 1-byte encoded norm; this BM25 uses the exact "
        "chunk length",
        "no character folding: ES 'standard' does not fold either, so this matches",
    ],
}

# ------------------------------------------------------------------ tokenizer
SFR_TOKENIZER_ID = "Salesforce/SFR-Embedding-Mistral"
SFR_PER_GEN = 2048 / 1630.0     # r3 SS3.3: a 2,048-generator-token chunk is ~2,570 SFR
GEN_TOKENIZER_STATUS = (
    "ABSENT: RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic is not in /rag/cache, and "
    "the harness rules for this run forbid contacting mango:8003/tokenize. Generator-token "
    f"columns are the ESTIMATE gen = sfr / {SFR_PER_GEN:.4f} (r3 SS3.3's own ratio "
    "2048/1630, measured at Stage 0 check 4), reported as descriptive and never used as a "
    "budget.")


def gen_tokens(sfr: float) -> float:
    """Descriptive generator-token estimate. NEVER a budget (r3 SS3.3)."""
    return sfr / SFR_PER_GEN


_SFR_TOK = None


def sfr_tokenizer():
    """The embedder's tokenizer, from the local HF cache. No network."""
    global _SFR_TOK
    if _SFR_TOK is None:
        os.environ.setdefault("HF_HOME", "/rag/cache")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from transformers import AutoTokenizer
        _SFR_TOK = AutoTokenizer.from_pretrained(SFR_TOKENIZER_ID, use_fast=True)
    return _SFR_TOK


class SfrCount:
    """Cached SFR token counts for arbitrary text slices (``parent256`` needs them).

    ``add_special_tokens=False``: budgets count each admitted slice's own supplied text,
    the same pin Stage 0b made for the generator tokenizer.
    """

    def __init__(self) -> None:
        self.tok = sfr_tokenizer()
        self.cache: dict[str, int] = {}
        self.calls = 0

    def count(self, text: str) -> int:
        h = hashlib.blake2b(text.encode(), digest_size=16).hexdigest()
        v = self.cache.get(h)
        if v is None:
            self.calls += 1
            v = len(self.tok(text, add_special_tokens=False)["input_ids"])
            self.cache[h] = v
        return v

    def counts(self, texts: list[str]) -> list[int]:
        todo = [t for t in texts
                if hashlib.blake2b(t.encode(), digest_size=16).hexdigest() not in self.cache]
        if todo:
            enc = self.tok(todo, add_special_tokens=False)["input_ids"]
            for t, ids in zip(todo, enc):
                self.cache[hashlib.blake2b(t.encode(), digest_size=16).hexdigest()] = len(ids)
            self.calls += len(todo)
        return [self.count(t) for t in texts]


# ------------------------------------------------------------------ corpus loaders
def load_docs() -> dict[str, str]:
    docs = {}
    with open(WORK / "docs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            docs[r["docno"]] = r["text"]
    return docs


def load_units() -> dict[str, list[dict]]:
    units = {}
    with open(WORK / "units.jsonl") as f:
        for line in f:
            r = json.loads(line)
            units[r["docno"]] = r["units"]
    return units


def load_rows(arm: str) -> list[list]:
    """``[[docno, start_char, end_char, sfr_tokens], ...]`` in embedding-row order."""
    return json.loads((EMB / f"rows_{arm}.json").read_text())


def load_headers(arm: str = "header512") -> dict[tuple[str, int], str]:
    hdr = {}
    with open(CHUNKS / f"spans_{arm}.jsonl") as f:
        for line in f:
            r = json.loads(line)
            for i, (s, _e, _n) in enumerate(r["spans"]):
                hdr[(r["docno"], s)] = r["hdr"][i]
    return hdr


def neighbour_table(rows: list[list]) -> tuple[dict, dict]:
    """Row-index -> previous / next row index within the same document.

    The chunk records carry no ``prev_chunk_id`` / ``next_chunk_id`` field: they are
    ``[docno, start, end, ntok]`` in document order inside each document's block, so a
    chunk's neighbours are its adjacent rows in the same document. This is asserted
    (each document's rows are contiguous and non-decreasing in ``start``) rather than
    assumed.
    """
    prev: dict[int, int] = {}
    nxt: dict[int, int] = {}
    i = 0
    n = len(rows)
    while i < n:
        d = rows[i][0]
        j = i
        while j + 1 < n and rows[j + 1][0] == d:
            j += 1
        for k in range(i, j):
            assert rows[k][1] <= rows[k + 1][1], (d, k)
            nxt[k] = k + 1
            prev[k + 1] = k
        i = j + 1
    return prev, nxt


# ------------------------------------------------------------------ misc
def sha256_file(p) -> str:
    return C.sha256_file(p)


def atomic_json(path, obj):
    return C.atomic_json(path, obj)


def git_head(repo: str = C.REPO) -> str:
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def provenance(extra: dict | None = None) -> dict:
    """Like ``s0_common.provenance`` but does NOT abort on a moved HEAD.

    Stage 0's helper asserts the repo is at ``55a0fc2``; this worktree is branched from a
    later ``main``. Both hashes are recorded and the diff of the one file that matters --
    the sentence segmenter, which defines the gold's sentence identity -- is asserted
    empty against ``55a0fc2`` instead.
    """
    import numpy
    head = git_head()
    wt = git_head(str(HERE))
    seg_diff = subprocess.run(
        ["git", "-C", C.REPO, "diff", f"{C.EXPECT_COMMIT}..HEAD", "--",
         "python/ragstack/ingestion/chunkers.py"],
        capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", C.REPO, "status", "--porcelain"],
                           capture_output=True, text=True, check=True).stdout.strip()
    pkgs = {}
    for name in ("numpy", "httpx", "transformers", "tokenizers", "scipy"):
        try:
            mod = __import__(name)
            pkgs[name] = getattr(mod, "__version__", "?")
        except Exception:  # noqa: BLE001
            pkgs[name] = "ABSENT"
    return {
        "repo_head": head,
        "stage0_pinned_commit": C.EXPECT_COMMIT,
        "worktree_head": wt,
        "segmenter_diff_vs_stage0_pin": seg_diff or "EMPTY (identical)",
        "git_status_porcelain": dirty.splitlines(),
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "HF_HOME": os.environ.get("HF_HOME"),
        "numpy": numpy.__version__,
        "packages": pkgs,
        "tokenizer_for_budgets": SFR_TOKENIZER_ID,
        "generator_tokenizer": GEN_TOKENIZER_STATUS,
        "budgets_sfr_tokens": list(BUDGETS),
        "primary_budget_sfr": PRIMARY_BUDGET,
        "modes": list(MODES),
        "leg_depth": LEG_DEPTH, "depth": DEPTH, "rrf_k": RRF_K,
        "bm25": BM25_PIN,
        "seeds": {"bootstrap": SEED_BOOT, "unit_cap_per_doc": SEED_UNITCAP_R3},
        **(extra or {}),
    }


def assert_dev_only(topics) -> None:
    bad = sorted(set(topics) - set(DEV_TOPICS))
    assert not bad, f"CONFIRMATION TOPIC IN THE QUERY LIST: {bad}"
