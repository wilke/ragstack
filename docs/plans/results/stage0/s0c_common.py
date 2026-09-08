"""Confirmation run, option (a) — the QUARANTINED half: shared plumbing and the guard.

This module is the root of the ``s0c_*`` harness, which does the parts of the confirmation
run (`../design/SPEC-confirmation-run-r3.md` §10 item 5 (a)) that must happen *before*
anyone can read anything, and which **reads nothing**:

* the 160 confirmation queries (80 topics × {summary, description}) and their embeddings;
* retrieval for 6 index arms × 3 modes at the served shape, reranked, pools frozen;
* packing at B ∈ {4,096 / 16,384 / 32,768} SFR tokens for all 11 scoring arms, contexts
  persisted in Stage 0b′'s reference form;
* pooling (r2 §6.3) into the labeling set, **counts only**;
* the 30-reading labeling pass over the pooled pairs.

Everything it writes goes under ``$STAGE0_BIG/work/conf/`` and is governed by the
``QUARANTINE.md`` at that directory's root.

**The guard.** ``QUARANTINE`` is true for the whole of this task. While it is true, every
endpoint function in this module — ``eret``, ``epack``, ``euc``, and the generic
``metric`` — **raises**. There is no code path in the ``s0c_*`` harness that returns an
endpoint value, a ranking, a similarity, a fusion score or a rerank score for a
confirmation topic; the labeler consumes only the pooled ``(topic, docno)`` id list.
The unblinding conditions are in ``QUARANTINE.md`` and in r2 §P.9 step 3→4: the two-reader
human read (r3 §5 step 3) and the label freeze. Neither is this task's.

**Reuse, not re-declaration.** Retrieval, the three modes, the packing walk and the context
persistence are Stage 0b′'s (`s0b_common`, `s0b_bm25`, `s0b_pack`, `s0b_contexts`, #520),
imported rather than copied. The labeling recipe is `s0_label_r31`'s (#507/#512),
imported rather than copied: the same prompt bytes, the same locator, the same
presentation seeding.

**Endpoints.** ``:9001-:9006`` (SFR, ≤ 2 in flight each, the 160 query embeddings only),
``:50052`` (crossencoder, ≤ 4 in flight), ``mango:8003`` and ``mango:8004`` (≤ 4 in flight
each). No store client is constructed anywhere in this harness. GPUs 6 and 7 are untouched.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import s0b_common as K          # noqa: E402  (Stage 0b′'s plumbing, reused verbatim)
import s0_common as C           # noqa: E402

# ---------------------------------------------------------------------- the guard
QUARANTINE = True
"""True for the whole of this task. See ``metric()``."""

_QUARANTINE_MSG = (
    "QUARANTINE: no aggregate metric over confirmation topics may be computed, printed, "
    "logged or committed by this task. The confirmation analysis is blocked by "
    "pre-registration until (i) the two-reader human read (r3 §5 step 3) has produced κ "
    "and (ii) the labels are frozen (r2 §P.9 step 3). Neither is this task's. "
    "See /rag/tmp/stage0-conf/work/conf/QUARANTINE.md.")


class QuarantineViolation(RuntimeError):
    """Raised by any endpoint function while ``QUARANTINE`` is true."""


def metric(name: str, *_a, **_kw):
    """The single choke point. Every endpoint function below funnels through it."""
    if QUARANTINE:
        raise QuarantineViolation(f"{name}: {_QUARANTINE_MSG}")
    raise NotImplementedError(
        f"{name} is not implemented in the s0c_* harness — the confirmation analysis is a "
        "separate, later task that runs after label freeze.")


def eret(*a, **kw):
    """`ERET@B`, evidence-document reach (r3 §3.1). Raises under quarantine."""
    return metric("ERET", *a, **kw)


def epack(*a, **kw):
    """`EPACK@B`, evidence containment given reach (r3 §3.1). Raises under quarantine."""
    return metric("EPACK", *a, **kw)


def euc(*a, **kw):
    """`EUC` = ERET × EPACK, revision 2's primary (r3 §3.1). Raises under quarantine."""
    return metric("EUC", *a, **kw)


def summarise(*a, **kw):
    """Any per-topic or per-arm aggregate. Raises under quarantine."""
    return metric("summarise", *a, **kw)


def forbid_metric(what: str = "aggregate") -> None:
    """Call at the top of anything that would aggregate a confirmation outcome."""
    metric(what)


# ---------------------------------------------------------------------- topics
def all_topics() -> list[str]:
    q = json.loads((C.WORK / "qrels_all.json").read_text())
    ids = sorted(q)
    assert len(ids) == 90, f"expected 90 CDS topics, found {len(ids)}"
    return ids


def conf_topics() -> list[str]:
    """The 80 confirmation topics = all 90 minus the 10 development topics (r2 §2.1)."""
    ids = [t for t in all_topics() if t not in set(C.DEV_TOPICS)]
    assert len(ids) == 80, f"expected 80 confirmation topics, found {len(ids)}"
    return ids


def assert_conf_only(topics) -> None:
    """The sequestration assertion, mirrored from ``s0b_common.assert_dev_only``."""
    conf = set(conf_topics())
    bad = sorted(set(topics) - conf)
    assert not bad, f"NON-CONFIRMATION TOPIC IN THE CONFIRMATION LIST: {bad}"


def assert_no_dev(topics) -> None:
    bad = sorted(set(topics) & set(C.DEV_TOPICS))
    assert not bad, f"DEVELOPMENT TOPIC IN THE CONFIRMATION LIST: {bad}"


# ---------------------------------------------------------------------- paths
CONF = C.WORK / "conf"
QUERIES = CONF / "queries"
POOLS = CONF / "pools"
CTX = CONF / "contexts"
POOL = CONF / "pool"
GENTOK = CONF / "gentok"
LABELS = CONF / "labels"
RUN = CONF / "run"
for _d in (CONF, QUERIES, POOLS, CTX, POOL, GENTOK, LABELS, RUN):
    _d.mkdir(parents=True, exist_ok=True)

ART = HERE / "artifacts" / "conf-a"
ART.mkdir(parents=True, exist_ok=True)

QUARANTINE_DOC = CONF / "QUARANTINE.md"

# ---------------------------------------------------------------------- pipeline pins
# Every one of these is Stage 0b′'s, imported so that a number that appears in both
# documents is the same constant.
VARIANTS = K.CDS_VARIANTS               # ("summary", "description")
INDEX_KEYS = K.INDEX_KEYS               # the six index arms
SCORING_ARMS = K.SCORING_ARMS           # + parent256 / nbr1_512 / nbr1_256 / nbr2_512 / multi
MODES = K.MODES                         # vector / bm25 / hybrid
RERANK_STATES = K.RERANK_STATES         # on / off
LEG_DEPTH = K.LEG_DEPTH                 # 100 per leg (r3 §3.4)
DEPTH = K.DEPTH                         # 50
RRF_K = K.RRF_K                         # 60
BUDGETS = K.BUDGETS                     # (4096, 16384, 32768) SFR tokens
PRIMARY_BUDGET = K.PRIMARY_BUDGET       # 16384

POOL_TOP_DOCS = 20                      # r2 §6.3 item 1
BIAS_BOUND_N = 10                       # r2 §6.3 item 2
SEED_BIASBOUND = C.SEED_BIASBOUND       # 20260916
SEED_GRADE0_CONF = C.SEED_GRADE0_CONF   # 20260912 (the corpus's own draw; recorded, not re-run)


def provenance(extra: dict | None = None) -> dict:
    """Stage 0b′'s provenance, plus this harness's quarantine attestation."""
    p = K.provenance()
    p.update({
        "harness": "s0c_* (confirmation run, option (a), quarantined half)",
        "quarantine": {
            "flag": QUARANTINE,
            "doc": str(QUARANTINE_DOC),
            "rule": ("no aggregate metric over confirmation topics is computed, printed, "
                     "logged or committed by this task; ERET/EPACK/EUC raise"),
            "unblocked_by": ("r3 §5 step 3 (two-reader human read → κ) and r2 §P.9 step 3 "
                             "(label freeze); neither is this task's"),
        },
        "populations": {"cds_confirmation_topics": 80, "variants": list(VARIANTS)},
        "endpoints_permitted": [
            "http://localhost:9001..9006 (SFR, <= 2 in flight each, queries only)",
            "http://localhost:50052 (crossencoder, <= 4 in flight)",
            "http://mango.cels.anl.gov:8003 (Llama-4-Scout, <= 4 in flight)",
            "http://mango.cels.anl.gov:8004 (Qwen3.6-35B-A3B, <= 4 in flight)"],
        "stores_contacted": "none — no Qdrant/Elasticsearch/Neo4j/tenant-API client is "
                            "constructed anywhere in the s0c_* harness",
        "embeddings": "REUSED from /rag/tmp/stage0-conf/emb/ (SFR-token chunk arms). This "
                      "is a stated deviation from r3 §3.3's confirmation-run path, which "
                      "re-chunks in the generator's tokenizer; recorded in the write-up.",
        **(extra or {})})
    return p


def sha256_file(p) -> str:
    return C.sha256_file(p)


def atomic_json(path, obj):
    return C.atomic_json(path, obj)


def git_head(repo: str = C.REPO) -> str:
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def env_report() -> dict:
    return {"HF_HOME": os.environ.get("HF_HOME"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "STAGE0_HELPERS": os.environ.get("STAGE0_HELPERS"),
            "STAGE0_BIG": str(C.BIG)}


def selftest() -> dict:
    """Offline. Asserts the guard actually raises and the topic split is the right one."""
    out: dict = {}
    for fn, name in ((eret, "ERET"), (epack, "EPACK"), (euc, "EUC"),
                     (summarise, "summarise"), (forbid_metric, "forbid_metric")):
        try:
            fn()
        except QuarantineViolation as e:
            assert name.lower() in str(e).lower() or "aggregate" in str(e).lower(), str(e)
        else:  # pragma: no cover
            raise AssertionError(f"{name} did NOT raise under quarantine")
    out["guard_raises"] = "ok (ERET, EPACK, EUC, summarise, forbid_metric)"

    ct = conf_topics()
    assert len(ct) == 80 and not (set(ct) & set(C.DEV_TOPICS))
    assert sorted(ct + C.DEV_TOPICS) == all_topics()
    out["topic_split"] = f"ok (80 confirmation + 10 development = {len(all_topics())})"

    assert_conf_only(ct[:3])
    assert_no_dev(ct)
    try:
        assert_conf_only(["2014_5"])           # a development topic
    except AssertionError:
        out["assert_conf_only"] = "ok (rejects a development topic)"
    else:  # pragma: no cover
        raise AssertionError("assert_conf_only accepted a development topic")

    assert QUARANTINE_DOC.exists(), f"missing {QUARANTINE_DOC}"
    out["quarantine_doc"] = str(QUARANTINE_DOC)
    out["dirs"] = [str(p) for p in (QUERIES, POOLS, CTX, POOL, GENTOK, LABELS, RUN)]
    return out


if __name__ == "__main__":
    print(json.dumps(selftest(), indent=1))
