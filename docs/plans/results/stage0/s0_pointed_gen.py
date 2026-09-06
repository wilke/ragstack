"""Stage 0b' — the POINTED-QUESTION population, generated on the development topics
(``design/SPEC-confirmation-run-r3.md`` §11).

r3 §1 declares the population this study optimises for: **pointed, evidence-seeking
questions**. The run's own queries are Leg A's TREC CDS clinical narratives, so §11 adds a
second population of the declared shape, generated on the same 32,663-document Stage 0
corpus. This module builds §11's Stage 0b' half: **≥ 150 queries on the development
topics' documents, with construction gold**. It measures nothing about retrieval — §11's
guard 1 (discrimination) and guard 3 (sizing from σ_d) are a later, separate pass.

Construction follows the Leg B re-run (``../pilots/RESULTS-legB-rerun.md``,
``legb2_gen.py``), with the deviations named in ``RESULTS-stage0b-pointed-gen.md`` §7:

* **two passes, never one** (the re-run's T1b defence). Pass A sees the deep section and
  nothing else and writes an abstractive summary; pass B **never sees the section** — a
  question written *from* the passage it must retrieve shares that passage's vocabulary by
  construction, which is the contamination the re-run's two-pass form exists to exclude.
  Pass B does see the article's **front matter**, as a negative constraint: r3 §11 requires
  the entity to be absent from the title + abstract and the query to sit below the 0.80
  `title_answerable` bar against them, and a generator blind to the front matter cannot
  satisfy a constraint stated over it. The 20-candidate smoke measured that directly (§7).
* pass C then locates the **construction gold**: shown the question and the section, it
  copies out the verbatim sentence(s) that answer it. This is the pass §11 guard 2 needs
  and the one the re-run did not have; the quotes are located by exact-then-normalised
  match and a quote that does not locate **rejects the query** (r3 §10 item 3, decision C:
  whole sentences).
* pass D is the re-run's abstract-answerability verifier, prompt file **verbatim**, so the
  yield here is comparable with the re-run's 65 %.

**One item per LLM call** at every stage, so cross-item bleed is excluded by construction.

Endpoint policy, non-negotiable: the only endpoint contacted is ``mango:8003``
(``Llama-4-Scout``, served id asserted live before the first call), **≤ 4 in flight**.
``:50052`` is NOT called — the crossencoder construction cross-check the re-run ran in its
§5.2 is deferred to the Stage 0b' retrieval pass. No SFR endpoint, no store client, no
tenant API. GPUs 6 and 7 are untouched: nothing here selects a device.

Reproduce::

    HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python \\
    STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0 \\
    /rag/envs/ragstack/bin/python3 s0_pointed_gen.py --smoke        # 20 candidates
    ... s0_pointed_gen.py --target 150 --max-candidates 600         # the full run
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import queue
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# The Phase-0 helper modules (``stage1_common``, ``pilot_common``, ``legb2_rules``) live
# beside the working copy of the analysis tree, not in the repo; ``s0_common`` imports the
# first two at module load, and the Leg B re-run's rule module is imported from the same
# tree so the three §2.6 fixes are the SAME CODE, not a copy of it.
_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                      # noqa: E402
import s0_math as M                                        # noqa: E402
from s0_label import _norm_map, segment                    # noqa: E402

import legb2_rules as R                                    # noqa: E402  (the re-run's fixes)

sys.path.insert(0, "/home/wilke/Development/ragstack/python/scripts/eval")
import _g1_rating as g1r                                   # noqa: E402
from g1_make_queries import (                              # noqa: E402
    MAX_WORDS, MIN_CONTENT_TERMS, TITLE_ANSWERABLE_THRESHOLD, _DOC_REFERENCE_RE,
    _INTERROGATIVE_RE, normalize,
)

HERE = pathlib.Path(__file__).resolve().parent
LEGB2 = _HELPERS / "pilots" / "legb2_gen.py"
VERIFIER_PROMPT_FILE = _HELPERS / "pilots" / "verifier_prompt.txt"
IDF_FILE = _HELPERS / "pilots" / "idf_oa10k.json"
STEP2_FETCHLIST = _HELPERS / "step2" / "fetchlist.txt"

OUT = C.WORK / "pointed"
OUT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ constants
SEED_POINTED = 20260918        # NEW seed: the candidate draw and its round-robin order
CONC = 4                       # <= 4 in flight on the shared host. Non-negotiable.
MAX_QUERY_WORDS = 15           # r3 §11: "held to ~12 words"; the re-run's bar was 20
TARGET_WORDS_LO, TARGET_WORDS_HI = 10, 13
MIN_SEC_WORDS, MAX_SEC_WORDS = 200, 1600   # see §7: the re-run's 250-2,200 SFR tokens
MAX_GOLD_SENTENCES = 5         # reported, and gated: a "minimal" set is not a paragraph
BLOCK = 100                    # candidates screened per block before the stop is re-read

# Stage sampling parameters, pinned and recorded in the manifest.
STAGES = {
    "paraphrase": {"max_tokens": 900, "temperature": 0.3},
    "query":      {"max_tokens": 1200, "temperature": 0.5},
    "gold":       {"max_tokens": 2000, "temperature": 0.0},
    "verify":     {"max_tokens": 200, "temperature": 0.0},
}

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
# Stage A is ``legb2_gen.PARAPHRASE_PROMPT`` **verbatim**. It cannot be imported (that
# module imports ``mango``, which pins :8004/Qwen and raises at import), so it is copied
# and the copy is ASSERTED against the source file at startup — see ``assert_verbatim``.
PARAPHRASE_PROMPT = """\
You are helping build an evaluation set for a scientific search system.

Read the passage below and write a 2-3 sentence ABSTRACTIVE summary of what it
establishes. Rules:
- Use your own words for the sentences. Do not copy phrases of more than three
  consecutive words.
- Carry the passage's SPECIFIC NAMES over verbatim: genes, proteins, organisms,
  strains, compounds, drugs, cell types, diseases, cohorts, instruments, and
  software or tool names. Never replace a name with a generic description.
- Keep at least one concrete measurement, condition or result if the passage reports one.
- Do not mention "the passage", "the text", "the authors", or any document.
- Output only the summary.

PASSAGE:
{chunk}
"""

# Stage B is ``legb2_gen.QUERY_PROMPT`` with THREE changes, each recorded in the manifest
# by ``assert_verbatim`` and argued in the write-up's §7:
#   1. rule 1's word budget, 20/"aim 10-16" -> 15/"aim 10-13"   (r3 §11's ~12-word bar);
#   2. rule 3 gains the front-matter clause and rule 6 is added — r3 §11 requires the
#      entity to be ABSENT from the title + abstract and the query to sit below the 0.80
#      `title_answerable` bar against them, and the smoke measured that a generator which
#      cannot see the front matter cannot satisfy a constraint stated over it: 10 of 20
#      smoke candidates died on `title_answerable_ta` and 2 more on `entity_in_front_matter`;
#   3. the FRONT MATTER block that rule 6 refers to.
# The two-pass isolation that the re-run exists to provide is UNCHANGED: stage B still
# never sees the source section, so the query cannot copy the passage it must retrieve.
# What it now sees is the front matter it must *avoid*, which is a negative constraint.
QUERY_PROMPT = """\
You are writing a realistic question that a working researcher might type into a
literature search tool.

Below is a short summary of one finding. Write ONE question that this finding answers.

HARD RULES. A question that breaks any of them is thrown away:
1. AT MOST 15 words. Aim for 10 to 13. Real search queries are short.
2. ONE clause, asking ONE thing. Never join two questions with "and", "or", "while",
   "whereas", or a comma. If the summary contains two findings, pick one.
3. NAME AT LEAST ONE SPECIFIC ENTITY from the summary — a gene, protein, organism,
   strain, compound, drug, cell type, disease, cohort, instrument, or software/tool
   name — spelled exactly as the summary spells it, AND one that does NOT appear
   anywhere in the FRONT MATTER below. Never write a generic stand-in
   such as "a flavonoid compound", "a web-based tool", or "a certain transcription
   factor". If the summary truly names nothing specific, return an empty query.
4. Never refer to "this study", "the authors", a figure, a table, or any document.
5. Do not presuppose the design decisions of the work being summarised. Someone who
   has never read it must be able to ask this question.
6. The FRONT MATTER below is the article's own title and abstract — what a reader
   already knows before opening the paper. Your question must ask for something the
   SUMMARY supplies that the FRONT MATTER does NOT state: a specific quantity,
   population, mechanism, reagent, instrument or sub-analysis. If the FRONT MATTER
   already answers your question, it is the wrong question — ask a narrower one.
   Reuse as few of the FRONT MATTER's words as you can.

Output ONLY a JSON object on a single line, with no code fence and no commentary:
{{"entity": "<the exact entity name you used, or \\"\\" if none>", "query": "<the question, or \\"\\" if rule 3 cannot be met>"}}

FRONT MATTER:
{front}

SUMMARY:
{summary}
"""

# Stage C is NEW — the re-run had no construction gold. It is what r3 §11 guard 2 and r3
# §10 item 3 (decision C, whole-sentence quotes) require: the model must produce a receipt
# that can be located character-for-character, and a receipt that does not locate throws
# the query away rather than being repaired.
GOLD_PROMPT = """\
You are marking the exact evidence for one question inside one section of a scientific
article.

QUESTION: {query}

Below is the SECTION. Copy out the sentence or sentences of the SECTION that answer the
question.

HARD RULES:
1. Copy WHOLE sentences, character for character, exactly as they appear below. Never
   paraphrase, never shorten a sentence, never join two sentences with an ellipsis,
   never correct a typo or change punctuation.
2. Copy the FEWEST sentences that fully answer the question. Usually one. Never more
   than three.
3. Every sentence you copy must appear in the SECTION verbatim. If no sentence of the
   SECTION answers the question, return an empty list.
4. No commentary, no explanation, no numbering.

Output ONLY a JSON object on a single line, with no code fence:
{{"sentences": ["<a verbatim sentence from the SECTION>"]}}

SECTION:
{section}
"""

VERIFIER_PROMPT = VERIFIER_PROMPT_FILE.read_text()      # the re-run's file, verbatim


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def assert_verbatim() -> dict:
    """Prove the copied prompts against the Leg B re-run's own source file.

    ``legb2_gen`` cannot be imported (it imports ``mango``, which pins :8004/Qwen and
    raises at import), so its prompts are copied here. A copy that drifts is a silent
    change of generator, so the copy is CHECKED rather than trusted:

    * stage A must appear in ``legb2_gen.py`` byte-for-byte;
    * stage B's carried-over rules — 2, 4 and 5, the JSON output contract, and the
      opening instruction — must each appear in ``legb2_gen.py`` verbatim, so that the
      three changes r3 §11 needs (§7 of the write-up) are the ONLY changes;
    * the full line-level diff against the re-run's prompt is returned and recorded.
    """
    src = LEGB2.read_text()
    if PARAPHRASE_PROMPT not in src:
        raise SystemExit("PARAPHRASE_PROMPT is not verbatim in legb2_gen.py — STOP")
    # ``\\"`` in the source FILE is ``\"`` in the string it defines; re-escape before
    # comparing text to text.
    esc = QUERY_PROMPT.replace("\\", "\\\\")
    carried = [
        "You are writing a realistic question that a working researcher might type into a",
        "Below is a short summary of one finding. Write ONE question that this finding answers.",
        "HARD RULES. A question that breaks any of them is thrown away:",
        '2. ONE clause, asking ONE thing. Never join two questions with "and", "or", "while",',
        '   "whereas", or a comma. If the summary contains two findings, pick one.',
        "4. Never refer to \"this study\", \"the authors\", a figure, a table, or any document.",
        "5. Do not presuppose the design decisions of the work being summarised. Someone who",
        "   has never read it must be able to ask this question.",
        "Output ONLY a JSON object on a single line, with no code fence and no commentary:",
        '{{"entity": "<the exact entity name you used, or \\\\"\\\\" if none>", '
        '"query": "<the question, or \\\\"\\\\" if rule 3 cannot be met>"}}',
    ]
    missing = [c for c in carried if c not in src or c not in esc]
    if missing:
        raise SystemExit(
            "stage-B rules carried from the Leg B re-run no longer match legb2_gen.py: "
            f"{missing} — STOP")
    # The full diff, for the record.
    theirs = src.splitlines()
    i = theirs.index("You are writing a realistic question that a working researcher might type into a")
    theirs = theirs[i:i + 28]
    mine = esc.splitlines()
    import difflib
    diff = [ln for ln in difflib.unified_diff(theirs, mine, "legb2_gen.QUERY_PROMPT",
                                              "s0_pointed_gen.QUERY_PROMPT", n=0)]
    return {"legb2_gen_sha256": sha(src),
            "paraphrase_verbatim_in_legb2_gen": True,
            "query_prompt_carried_lines_verified": len(carried),
            "query_prompt_unified_diff": diff}


# --------------------------------------------------------------------------- #
# The client — mango:8003 only, <= 4 in flight
# --------------------------------------------------------------------------- #
class Scout:
    """Bounded, polite client for ``mango:8003``.

    Structure and failure policy carried from ``pilots/mango.py`` (the Leg B re-run's
    client) and ``s0_label_r3.Judge``: the served id is read from ``/v1/models`` and
    asserted before the first call, concurrency is capped by a slot queue, every failure
    path backs off rather than retrying immediately, an empty ``content`` with
    ``finish_reason == "length"`` doubles the budget once rather than being read as a
    refusal, and tokens/elapsed are accumulated so the run reports its own cost.
    """

    def __init__(self, conc: int = CONC):
        self.base = C.MANGO
        self.model = json.load(urllib.request.urlopen(
            self.base + "/v1/models", timeout=60))["data"][0]["id"]
        if self.model != C.SCOUT_EXPECT:
            raise SystemExit(
                f"{self.base} serves {self.model!r}, not {C.SCOUT_EXPECT!r} this run was "
                f"calibrated against. Refusing to run: the generator identity is part of "
                f"the result.")
        self.slots: queue.Queue = queue.Queue()
        for _ in range(conc):
            self.slots.put(1)
        self.conc = conc
        self.lock = threading.Lock()
        self.per_stage: dict = collections.defaultdict(
            lambda: {"requests": 0, "retries": 0, "failures": 0, "truncated": 0,
                     "prompt_tokens": 0, "completion_tokens": 0, "llm_seconds": 0.0})
        self.t0 = time.time()

    def chat(self, prompt: str, stage: str, *, timeout: int = 900) -> dict:
        cfg = STAGES[stage]
        payload = {"model": self.model, "temperature": cfg["temperature"],
                   "max_tokens": cfg["max_tokens"], "seed": SEED_POINTED,
                   "messages": [{"role": "user", "content": prompt}]}
        s = self.per_stage[stage]
        self.slots.get()
        try:
            budget = cfg["max_tokens"]
            note = ""
            for attempt in range(5):
                payload["max_tokens"] = budget
                body = json.dumps(payload).encode()
                try:
                    t = time.time()
                    req = urllib.request.Request(
                        self.base + "/v1/chat/completions", body,
                        {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        o = json.load(r)
                    dt = time.time() - t
                    ch = o["choices"][0]
                    msg = ch["message"]
                    text = (msg.get("content") or "").strip()
                    if not text:
                        text = (msg.get("reasoning_content") or "").strip()
                    with self.lock:
                        s["requests"] += 1
                        s["prompt_tokens"] += o["usage"]["prompt_tokens"]
                        s["completion_tokens"] += o["usage"]["completion_tokens"]
                        s["llm_seconds"] += dt
                        if ch.get("finish_reason") == "length":
                            s["truncated"] += 1
                    if not text and ch.get("finish_reason") == "length" and attempt < 3:
                        budget *= 2
                        note = f"budget doubled to {budget}"
                        with self.lock:
                            s["retries"] += 1
                        continue
                    return {"text": text, "ok": bool(text),
                            "finish": ch.get("finish_reason"),
                            "elapsed": round(dt, 2), "note": note}
                except Exception as e:                                  # noqa: BLE001
                    with self.lock:
                        s["retries"] += 1
                    if attempt == 4:
                        with self.lock:
                            s["failures"] += 1
                        return {"text": "", "ok": False, "finish": "error",
                                "elapsed": 0.0, "note": f"{type(e).__name__}: {e}"}
                    time.sleep(3 * (attempt + 1))
            with self.lock:
                s["failures"] += 1
            return {"text": "", "ok": False, "finish": "budget", "elapsed": 0.0,
                    "note": note}
        finally:
            self.slots.put(1)

    def stats(self) -> dict:
        tot = collections.Counter()
        for v in self.per_stage.values():
            for k, x in v.items():
                tot[k] += x
        return {"endpoint": self.base, "served_model": self.model,
                "concurrency": self.conc, "seed": SEED_POINTED,
                "per_stage": {k: dict(v, llm_seconds=round(v["llm_seconds"], 1))
                              for k, v in sorted(self.per_stage.items())},
                "total": {**{k: (round(v, 1) if isinstance(v, float) else v)
                             for k, v in tot.items()},
                          "wall_seconds": round(time.time() - self.t0, 1)}}


def run_stage(cl: Scout, items, fn, label: str):
    t0 = time.time()
    done = [0]
    lk = threading.Lock()

    def wrap(it):
        r = fn(it)
        with lk:
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {label}: {done[0]}/{len(items)} ({time.time()-t0:.0f}s)",
                      flush=True)
        return r

    with ThreadPoolExecutor(cl.conc) as ex:
        out = list(ex.map(wrap, items))
    print(f"  {label}: {len(out)} done in {time.time()-t0:.0f}s", flush=True)
    return out


def parse_json_obj(text: str) -> dict:
    """The model sometimes wraps the object in a fence or prose. Take the outermost {...}.

    ``legb2_gen.parse_json_line``, carried over.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        o = json.loads(t[i:j + 1])
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------- #
# Source documents — the ten development topics only
# --------------------------------------------------------------------------- #
def dev_documents() -> tuple[dict, dict, dict]:
    """(membership, excluded_counts, reproduction_check).

    ``membership`` is ``{docno: [{"topic", "grade_class"}]}`` over the dev topics'
    grade >= 1 relevants and the 300-per-topic grade-0 negatives drawn for dev. The draw is
    ``s0_corpus.build``'s, replayed byte-for-byte: ONE ``random.Random(20260904)`` over
    ``sorted(dev_topics)``, sampling from the SORTED negative list — and the result is
    asserted identical to ``step2/fetchlist.txt``, which is what that seed is for.

    A document that is ALSO a confirmation topic's grade >= 1 relevant is EXCLUDED, to keep
    the exposure ledger clean: nothing in this run may read, or be written from, a
    confirmation topic's judged-relevant document.
    """
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    plan = json.loads((C.WORK / "corpus_plan.json").read_text())
    dev, conf = plan["dev"], plan["conf"]
    if sorted(dev) != sorted(C.DEV_TOPICS):
        raise SystemExit(f"dev topics {sorted(dev)} != {sorted(C.DEV_TOPICS)} — STOP")
    if set(dev) & set(conf):
        raise SystemExit("dev and confirmation topic sets intersect — STOP")

    rng = random.Random(C.SEED_GRADE0_DEV)
    memb: dict[str, list] = collections.defaultdict(list)
    dev_only: set[str] = set()
    for tid in sorted(dev):
        pos = sorted([d for d, g in qrels[tid].items() if g >= 1])
        neg = sorted([d for d, g in qrels[tid].items() if g == 0])
        negs = rng.sample(neg, min(300, len(neg)))
        for d in pos:
            memb[d].append({"topic": tid, "grade_class": "relevant"})
        for d in negs:
            memb[d].append({"topic": tid, "grade_class": "grade0"})
        dev_only |= set(pos) | set(negs)

    old = sorted({x.strip() for x in STEP2_FETCHLIST.read_text().split() if x.strip()},
                 key=int)
    repro = {"step2_fetchlist_n": len(old), "dev_slice_n": len(dev_only),
             "identical": old == sorted(dev_only, key=int)}
    if not repro["identical"]:
        raise SystemExit("the dev slice no longer reproduces step2/fetchlist.txt — STOP")

    conf_rel: set[str] = set()
    for tid in conf:
        conf_rel |= {d for d, g in qrels[tid].items() if g >= 1}
    also_conf = dev_only & conf_rel
    for d in also_conf:
        memb.pop(d, None)

    excl = {"dev_slice": len(dev_only),
            "excluded_also_confirmation_relevant": len(also_conf),
            "after_confirmation_exclusion": len(dev_only) - len(also_conf)}
    return dict(memb), excl, repro


def load_corpus(memb: dict) -> tuple[dict, dict, dict]:
    """docs / units for the dev documents, from the Stage 0a artifacts."""
    units = {}
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        if r["docno"] in memb:
            units[r["docno"]] = r["units"]
    cache = OUT / "dev_docs.jsonl"
    if not cache.exists():
        with open(cache, "w") as w:
            for line in open(C.WORK / "docs.jsonl"):
                if json.loads(line)["docno"] in units:
                    w.write(line)
    docs = {}
    for line in open(cache):
        r = json.loads(line)
        if r["docno"] in units:
            docs[r["docno"]] = r
    for d, us in units.items():
        if d in docs and [u["i"] for u in us] != list(range(len(us))):
            raise SystemExit(f"{d}: unit indices are not 0..n-1 — STOP")
    stats = {"in_membership": len(memb), "in_units_jsonl": len(units),
             "in_docs_jsonl": len(docs),
             "dropped_not_parsed": len(memb) - len(docs)}
    return docs, units, stats


def candidate_units(docs: dict, units: dict) -> tuple[list, dict]:
    """Deep sections with enough prose, one candidate list, ordered for topic coverage.

    "Deep" is r3 §11's rule as briefed: a unit that is neither the abstract nor the first
    body unit — i.e. ``i >= 2``, with ``cls != "abstract"`` for the handful of articles
    carrying a second abstract-classed unit (a "Summary") further down.

    "Enough prose" is the Leg B re-run's positive rule (``legb2_rules.section_signals``):
    the section must carry >= 1 numeric result or method noun, and be long enough to
    summarise but short enough to pass whole. The re-run's 250-2,200 SFR-token band is
    restated in WORDS here (200-1,600) — see the write-up's deviations: the SFR fleet is
    not contactable under this task's endpoint policy, and Scout's tokenizer is not SFR's.
    """
    clause = collections.Counter()
    per_doc: dict[str, list] = {}
    n_deep = 0
    for d, rec in docs.items():
        us = units[d]
        keep = []
        for u in us[2:]:
            if u["cls"] == "abstract":
                continue
            n_deep += 1
            seg = rec["text"][u["start_char"]:u["end_char"]]
            w = len(seg.split())
            sig = R.section_signals(seg)
            cl = {"min_words": w >= MIN_SEC_WORDS, "max_words": w <= MAX_SEC_WORDS,
                  "has_result_or_method": sig["numeric_result"] or sig["method_noun"]}
            for k, v in cl.items():
                clause[k] += (not v)
            if all(cl.values()):
                keep.append({"unit": u["i"], "unit_title": u["title"] or "",
                             "unit_cls": u["cls"], "unit_words": w,
                             "start_char": u["start_char"], "end_char": u["end_char"],
                             "n_units_in_doc": len(us)})
        if keep:
            per_doc[d] = keep

    # Round-robin over the ten dev topics so coverage is by construction, not by luck.
    rng = random.Random(SEED_POINTED)
    by_topic: dict[str, list] = collections.defaultdict(list)
    for d in per_doc:
        for m in MEMBERSHIP[d]:
            by_topic[m["topic"]].append(d)
    for t in by_topic:
        by_topic[t] = sorted(set(by_topic[t]), key=int)
        rng.shuffle(by_topic[t])
    order, taken = [], set()
    topics = sorted(by_topic)
    cursor = {t: 0 for t in topics}
    while True:
        progressed = False
        for t in topics:
            while cursor[t] < len(by_topic[t]) and by_topic[t][cursor[t]] in taken:
                cursor[t] += 1
            if cursor[t] < len(by_topic[t]):
                d = by_topic[t][cursor[t]]
                cursor[t] += 1
                taken.add(d)
                order.append((d, t))
                progressed = True
        if not progressed:
            break

    cands = []
    for d, t in order:
        # ONE candidate unit per document. r3 §11 point 4 caps ACCEPTED queries at 2 per
        # document; drawing one keeps that cap satisfied by construction and, more
        # importantly, keeps the queries independent. The smoke drew two adjacent units per
        # document and produced near-duplicate pairs from the same article (two TomoBreast
        # questions, two "idiopathic inflammatory myopathies" questions) — correlated
        # queries would deflate the σ_d that §11 guard 3 sizes the population from, which is
        # the one quantity this set exists to measure.
        us = per_doc[d][:]
        rng.shuffle(us)
        cands.append({"docno": d, "draw_topic": t, **us[0]})
    stats = {"docs_with_a_candidate_unit": len(per_doc),
             "deep_units_examined": n_deep,
             "eligible_units": sum(len(v) for v in per_doc.values()),
             "candidate_units": len(cands),
             "clause_rejections_independent": dict(clause)}
    return cands, stats


# --------------------------------------------------------------------------- #
# The gold locator — exact, then normalised; a quote that does not locate rejects
# --------------------------------------------------------------------------- #
def locate_gold(quotes: list[str], seg_text: str, seg_base: int, sents: list,
                unit_i: int) -> dict:
    """Locate each quoted sentence in the section and snap it to whole sentences.

    ``sents`` is ``[(k, abs_start, abs_end, text)]`` from ``s0_label.segment``, whose spans
    tile the unit exactly, so a located character interval maps to a
    ``(unit, first_sentence, last_sentence)`` triple with no gaps (D1). ``seg_base`` is the
    section's absolute ``start_char``, which turns a section-relative hit into the document
    offsets every other Stage 0 artifact speaks in.

    Returns ``{"ok", "spans", "per_quote"}``. ``ok`` is False as soon as ONE quote fails to
    locate: r3 §10 item 3 makes a non-locating receipt a hallucinated span, not a repairable
    one.
    """
    nd, idx = _norm_map(seg_text)
    per, hit_sents = [], set()
    for q in quotes:
        q = (q or "").strip()
        if not q:
            per.append({"quote": q, "how": "empty"})
            return {"ok": False, "spans": [], "per_quote": per}
        p = seg_text.find(q)
        how = "exact"
        if p >= 0:
            a, b = p, p + len(q)
        else:
            nq, _ = _norm_map(q)
            p = nd.find(nq) if nq else -1
            if p < 0:
                per.append({"quote": q[:160], "how": "not_located"})
                return {"ok": False, "spans": [], "per_quote": per}
            how = "normalised"
            a, b = idx[p], idx[p + len(nq) - 1] + 1
        abs_a, abs_b = seg_base + a, seg_base + b
        first = next((k for k, sa, sb, _ in sents if sa <= abs_a < sb), None)
        last = next((k for k, sa, sb, _ in sents if sa <= abs_b - 1 < sb), None)
        if first is None or last is None or last < first:
            per.append({"quote": q[:160], "how": how, "snap": "no_sentence"})
            return {"ok": False, "spans": [], "per_quote": per}
        # Did the model copy WHOLE sentences, or a fragment that had to be snapped out to
        # sentence boundaries? ``sentence_spans`` spans carry the inter-sentence whitespace,
        # so comparing raw offsets would call every quote a fragment; compare the located
        # text against the sentences it covers, whitespace-collapsed.
        whole = _norm(seg_text[a:b]) == _norm(
            " ".join(s[3] for s in sents[first:last + 1]))
        per.append({"quote": q[:160], "how": how, "first_sentence": first,
                    "last_sentence": last, "whole_sentences": whole})
        hit_sents.update(range(first, last + 1))

    # contiguous runs of whole sentences -> D1 spans
    spans = []
    for k in sorted(hit_sents):
        if spans and k == spans[-1]["last_sentence"] + 1:
            spans[-1]["last_sentence"] = k
        else:
            spans.append({"unit": unit_i, "first_sentence": k, "last_sentence": k})
    for s in spans:
        s["start"] = sents[s["first_sentence"]][1]
        s["end"] = sents[s["last_sentence"]][2]
        s["text"] = " ".join(sents[k][3] for k in
                             range(s["first_sentence"], s["last_sentence"] + 1))
    return {"ok": True, "spans": spans, "per_quote": per}


MEMBERSHIP: dict = {}
MAX_WORDS_LEGB = R.MAX_QUERY_WORDS   # 20 — the re-run's bar, reported for comparison


# --------------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------------- #
# The gating set. Every gate is computed from the QUERY, the SECTION and the FRONT MATTER
# — never from what the generator declares about itself. That is the Leg B re-run's rule
# ("a gate must not depend on the thing it is auditing"), and it is why r3 §11's entity
# clause is enforced here as ``no_deep_rare_term`` on the query's own terms rather than on
# the declared entity string. The declared entity is checked too, and reported, never gated.
GATES = ("empty", "not_a_question", "names_document", "too_short", "too_long_15",
         "compound", "title_answerable_ta", "title_answerable_title",
         "not_specific", "no_deep_rare_term",
         "gold_not_located", "gold_too_many_sentences", "duplicate",
         "abstract_answerable")
REPORTED = ("anchor_fail", "entity_no_rare_term", "entity_rare_absent_from_section",
            "entity_rare_all_in_front_matter", "entity_substring_absent_from_section",
            "entity_substring_in_front_matter", "too_long_20",
            "quote_snapped_to_sentence")


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def screen(cand: dict, gen: dict, gold: dict, verdict: dict, idf: dict,
           seen: set) -> dict:
    q, ent = gen["query"], gen["entity"]
    sec, front, title = cand["_sec"], cand["_front"], cand["_title"]
    shape = R.query_shape(q)
    spec = R.specificity(q, sec, idf, g1r.tokenize, entity=ent)
    ov_t = g1r.idf_overlap(q, title, idf)
    ov_ta = g1r.idf_overlap(q, front, idf)
    ov_sec = g1r.idf_overlap(q, sec, idf)
    ne, nsec, nfront = _norm(ent), _norm(sec), _norm(front)

    # r3 §11's entity clause, enforced on the query's OWN terms: at least one term of the
    # query must be rare in the corpus (IDF >= 5.60 over the 10k OA title+abstract table),
    # occur in the source section, and NOT occur in the article's title + abstract. A query
    # without such a term asks only for things the front matter already names, whatever
    # entity the generator says it used.
    q_terms = set(g1r.tokenize(q))
    sec_terms = set(g1r.tokenize(sec))
    front_terms = set(g1r.tokenize(front))
    deep_rare = sorted(t for t in q_terms & sec_terms
                       if idf.get(t, 99.0) >= R.IDF_SPECIFIC and t not in front_terms)

    # The declared entity, checked three ways and reported, never gated.
    e_terms = set(g1r.tokenize(ent))
    e_rare = {t for t in e_terms if idf.get(t, 99.0) >= R.IDF_SPECIFIC}

    f = {
        "empty": not q or not ent,
        "not_a_question": bool(q) and not q.endswith("?")
                          and not _INTERROGATIVE_RE.match(q),
        "names_document": bool(q) and bool(_DOC_REFERENCE_RE.search(q)),
        "too_short": bool(q) and len(g1r.tokenize(q)) < MIN_CONTENT_TERMS,
        "too_long_15": bool(q) and shape["n_words"] > MAX_QUERY_WORDS,
        "compound": bool(q) and shape["compound"],
        "title_answerable_ta": bool(q)
                               and ov_ta["idf_overlap"] >= TITLE_ANSWERABLE_THRESHOLD,
        "title_answerable_title": bool(q)
                                  and ov_t["idf_overlap"] >= TITLE_ANSWERABLE_THRESHOLD,
        "not_specific": bool(q) and not spec["specific"],
        "no_deep_rare_term": bool(q) and not deep_rare,
        "gold_not_located": not gold["ok"] or not gold["spans"],
        "gold_too_many_sentences": gold["ok"] and sum(
            s["last_sentence"] - s["first_sentence"] + 1
            for s in gold["spans"]) > MAX_GOLD_SENTENCES,
        "duplicate": bool(q) and normalize(q) in seen,
        "abstract_answerable": bool(verdict.get("abstract_answerable")),
        # reported, not gated
        "anchor_fail": bool(q) and not spec["anchor_ok"],
        "entity_no_rare_term": bool(ent) and not e_rare,
        "entity_rare_absent_from_section": bool(e_rare) and not (e_rare & sec_terms),
        "entity_rare_all_in_front_matter": bool(e_rare) and e_rare <= front_terms,
        "entity_substring_absent_from_section": bool(ent) and ne not in nsec,
        "entity_substring_in_front_matter": bool(ent) and ne in nfront,
        "too_long_20": bool(q) and shape["n_words"] > MAX_WORDS_LEGB,
        "quote_snapped_to_sentence": gold["ok"] and any(
            not p.get("whole_sentences", True) for p in gold["per_quote"]),
    }
    first = next((k for k in GATES if f[k]), None)
    return {
        "filters": f, "first_reason": first, "accepted": first is None,
        "n_words": shape["n_words"],
        "idf_overlap_title": ov_t["idf_overlap"],
        "idf_overlap_title_abstract": ov_ta["idf_overlap"],
        "idf_overlap_source_section": ov_sec["idf_overlap"],
        "jaccard_title_abstract": ov_ta["jaccard"],
        "n_query_terms": ov_ta["n_query_terms"],
        "n_rare_terms_absent_from_ta": len(
            [t for t in q_terms if idf.get(t, 99.0) >= R.IDF_SPECIFIC
             and t not in front_terms]),
        "deep_rare_terms": deep_rare[:8],
        "n_deep_rare_terms": len(deep_rare),
        "spec": spec,
    }


# --------------------------------------------------------------------------- #
def provenance() -> dict:
    def head(p):
        return subprocess.run(["git", "-C", p, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    def dirty(p):
        return subprocess.run(["git", "-C", p, "status", "--porcelain"],
                              capture_output=True, text=True,
                              check=True).stdout.strip().splitlines()
    import ragstack
    return {
        "code_repo": C.REPO, "code_commit": head(C.REPO),
        "code_commit_expected_by_s0_common": C.EXPECT_COMMIT,
        "worktree": str(HERE.parents[3]), "worktree_commit": head(str(HERE.parents[3])),
        "worktree_status_porcelain": dirty(str(HERE.parents[3])),
        "interpreter": sys.executable, "python": sys.version.split()[0],
        "HF_HOME": os.environ.get("HF_HOME"),
        "ragstack_file": ragstack.__file__,
        "helpers": str(_HELPERS),
        "gpu_before": C.gpu_snapshot(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=150)
    ap.add_argument("--max-candidates", type=int, default=600)
    ap.add_argument("--block", type=int, default=BLOCK)
    ap.add_argument("--tag", default="full")
    ap.add_argument("--smoke", action="store_true",
                    help="20 candidates, one block, tag 'smoke'")
    a = ap.parse_args()
    if a.smoke:
        a.target, a.max_candidates, a.block, a.tag = 10**9, 20, 20, "smoke"

    t_wall = time.time()
    prov = provenance()
    verb = assert_verbatim()
    print(json.dumps({"provenance": prov, "prompt_provenance": verb}, indent=1),
          flush=True)

    global MEMBERSHIP
    MEMBERSHIP, excl, repro = dev_documents()
    docs, units, cstats = load_corpus(MEMBERSHIP)
    print(f"dev slice {excl['dev_slice']} docs; "
          f"{excl['excluded_also_confirmation_relevant']} excluded as a confirmation "
          f"topic's relevant; {cstats['in_docs_jsonl']} parsed and usable", flush=True)
    cands, sel = candidate_units(docs, units)
    print(json.dumps(sel, indent=1), flush=True)

    idf = json.loads(IDF_FILE.read_text())
    idf_sha = sha(json.dumps(idf, sort_keys=True))

    cl = Scout()
    print(f"served model {cl.model}", flush=True)

    accepted, rejected, raws = [], [], []
    seen: set = set()
    hits = collections.Counter()
    funnel = collections.Counter()
    per_doc_accepted: collections.Counter = collections.Counter()
    n_screened = 0
    qn = 0

    for b0 in range(0, min(len(cands), a.max_candidates), a.block):
        if len(accepted) >= a.target or n_screened >= a.max_candidates:
            break
        blk = cands[b0:b0 + a.block]
        blk = blk[:a.max_candidates - n_screened]
        print(f"\n--- block {b0//a.block + 1}: {len(blk)} candidates "
              f"(screened {n_screened}, accepted {len(accepted)})", flush=True)
        for c in blk:
            rec = docs[c["docno"]]
            c["_sec"] = rec["text"][c["start_char"]:c["end_char"]]
            c["_title"] = rec["title"] or ""
            u0 = units[c["docno"]][0]
            c["_abstract"] = rec["text"][u0["start_char"]:u0["end_char"]]
            c["_front"] = (c["_title"] + " " + c["_abstract"]).strip()

        # ---- stage A: the SECTION only ------------------------------------- #
        pa = run_stage(cl, blk, lambda c: cl.chat(
            PARAPHRASE_PROMPT.format(chunk=c["_sec"]), "paraphrase"),
            "A paraphrase")
        # ---- stage B: the SUMMARY only ------------------------------------- #
        pb = run_stage(cl, list(zip(blk, pa)), lambda z: cl.chat(
            QUERY_PROMPT.format(summary=z[1]["text"], front=z[0]["_front"]), "query")
            if z[1]["ok"]
            else {"text": "", "ok": False, "finish": "skipped_no_summary",
                  "elapsed": 0.0, "note": ""},
            "B query")
        gens = []
        for c, ra, rb in zip(blk, pa, pb):
            o = parse_json_obj(rb["text"])
            gens.append({"query": (o.get("query") or "").strip(),
                         "entity": (o.get("entity") or "").strip(),
                         "parsed": bool(o), "summary": ra["text"]})
        # ---- stage C: the QUERY + the SECTION (gold) ----------------------- #
        pc = run_stage(cl, list(zip(blk, gens)), lambda z: cl.chat(
            GOLD_PROMPT.format(query=z[1]["query"], section=z[0]["_sec"]), "gold")
            if z[1]["query"] else {"text": "", "ok": False, "finish": "skipped_no_query",
                                   "elapsed": 0.0, "note": ""},
            "C gold")
        # ---- stage D: the QUERY + TITLE + ABSTRACT (verifier) -------------- #
        pd = run_stage(cl, list(zip(blk, gens)), lambda z: cl.chat(
            VERIFIER_PROMPT.format(question=z[1]["query"], title=z[0]["_title"],
                                   abstract=z[0]["_abstract"]), "verify")
            if z[1]["query"] else {"text": "", "ok": False, "finish": "skipped_no_query",
                                   "elapsed": 0.0, "note": ""},
            "D verify")

        for i, c in enumerate(blk):
            n_screened += 1
            gen = gens[i]
            ra, rb, rc, rd = pa[i], pb[i], pc[i], pd[i]
            seg = segment(docs[c["docno"]]["text"], units[c["docno"]])
            sents = next(s for ui, _t, s in seg if ui == c["unit"])
            og = parse_json_obj(rc["text"])
            quotes = [x for x in (og.get("sentences") or []) if isinstance(x, str)]
            gold = (locate_gold(quotes, c["_sec"], c["start_char"], sents, c["unit"])
                    if quotes else {"ok": False, "spans": [], "per_quote": []})
            vtoks = re.findall(r"\b(YES|NO)\b", (rd["text"] or "").upper())
            verdict = {"verdict": vtoks[-1] if vtoks else "",
                       "abstract_answerable": bool(vtoks) and vtoks[-1] == "YES",
                       "undecided": not vtoks}
            sc = screen(c, gen, gold, verdict, idf, seen)
            for k, v in sc["filters"].items():
                hits[k] += bool(v)
            funnel[sc["first_reason"] or "accepted"] += 1

            qn += 1
            row = {
                "qid": f"pq_{qn:04d}",
                "query": gen["query"], "entity": gen["entity"],
                "n_words": sc["n_words"],
                "docno": c["docno"], "draw_topic": c["draw_topic"],
                "dev_topics": MEMBERSHIP[c["docno"]],
                "unit": c["unit"], "unit_title": c["unit_title"],
                "unit_cls": c["unit_cls"], "unit_words": c["unit_words"],
                "unit_start_char": c["start_char"], "unit_end_char": c["end_char"],
                "n_units_in_doc": c["n_units_in_doc"],
                "gold_spans": gold["spans"],
                "gold_quotes": quotes,
                "gold_locate": gold["per_quote"],
                "verifier": verdict,
                "filters": sc["filters"], "first_reason": sc["first_reason"],
                "idf_overlap_title": sc["idf_overlap_title"],
                "idf_overlap_title_abstract": sc["idf_overlap_title_abstract"],
                "idf_overlap_source_section": sc["idf_overlap_source_section"],
                "jaccard_title_abstract": sc["jaccard_title_abstract"],
                "n_query_terms": sc["n_query_terms"],
                "n_rare_terms_absent_from_ta": sc["n_rare_terms_absent_from_ta"],
                "deep_rare_terms": sc["deep_rare_terms"],
                "n_deep_rare_terms": sc["n_deep_rare_terms"],
                "spec": sc["spec"],
                "summary": gen["summary"],
                "raw_sha256": {"paraphrase": sha(ra["text"]), "query": sha(rb["text"]),
                               "gold": sha(rc["text"]), "verify": sha(rd["text"])},
                "finish": {"paraphrase": ra["finish"], "query": rb["finish"],
                           "gold": rc["finish"], "verify": rd["finish"]},
            }
            raws.append({"qid": row["qid"], "docno": c["docno"], "unit": c["unit"],
                         "paraphrase": ra["text"], "query": rb["text"],
                         "gold": rc["text"], "verify": rd["text"]})
            if sc["accepted"] and per_doc_accepted[c["docno"]] < 2:
                seen.add(normalize(gen["query"]))
                per_doc_accepted[c["docno"]] += 1
                accepted.append(row)
            else:
                if sc["accepted"]:
                    row["first_reason"] = "per_document_cap"
                    funnel["accepted"] -= 1
                    funnel["per_document_cap"] += 1
                rejected.append(row)

        print(f"  block done: screened {n_screened}, accepted {len(accepted)}",
              flush=True)

    # ------------------------------------------------------------------ out
    tag = "" if a.tag == "full" else f"-{a.tag}"
    acc_p = OUT / f"pointed-dev{tag}.jsonl"
    rej_p = OUT / f"pointed-dev-rejected{tag}.jsonl"
    raw_p = OUT / f"pointed-dev-raw{tag}.jsonl"
    for p, rows in ((acc_p, accepted), (rej_p, rejected), (raw_p, raws)):
        with open(p, "w") as w:
            for r in rows:
                w.write(json.dumps(r) + "\n")

    topics_covered = collections.Counter()
    for r in accepted:
        topics_covered[r["draw_topic"]] += 1
    unit_idx = collections.Counter(r["unit"] for r in accepted)
    words = [r["n_words"] for r in accepted]
    n_rules = sum(1 for r in rejected + accepted
                  if r["first_reason"] in (None, "abstract_answerable",
                                           "per_document_cap"))
    man = {
        "spec": "design/SPEC-confirmation-run-r3.md §11 (Stage 0b' pointed-question set)",
        "tag": a.tag,
        "provenance": prov,
        "prompt_provenance": verb,
        "endpoint": cl.stats(),
        "prompts_sha256": {
            "paraphrase": sha(PARAPHRASE_PROMPT), "query": sha(QUERY_PROMPT),
            "gold": sha(GOLD_PROMPT), "verify": sha(VERIFIER_PROMPT),
            "verifier_prompt_file": str(VERIFIER_PROMPT_FILE),
        },
        "stage_params": STAGES,
        "seeds": {"grade0_dev": C.SEED_GRADE0_DEV, "pointed_draw": SEED_POINTED},
        "idf": {"file": str(IDF_FILE), "n_terms": len(idf), "sha256": idf_sha,
                "specific_threshold": R.IDF_SPECIFIC,
                "title_answerable_threshold": TITLE_ANSWERABLE_THRESHOLD},
        "source_documents": {**excl, **cstats,
                             "dev_reproduction_check": repro,
                             "membership_docs": len(MEMBERSHIP)},
        "section_selection": sel,
        "thresholds": {"max_query_words": MAX_QUERY_WORDS,
                       "target_words": [TARGET_WORDS_LO, TARGET_WORDS_HI],
                       "section_words": [MIN_SEC_WORDS, MAX_SEC_WORDS],
                       "max_gold_sentences": MAX_GOLD_SENTENCES,
                       "legb_rerun_max_query_words": MAX_WORDS_LEGB,
                       "legacy_max_words": MAX_WORDS},
        "counts": {
            "candidates_screened": n_screened,
            "accepted": len(accepted),
            "rejected": len(rejected),
            "target": a.target, "max_candidates": a.max_candidates,
            "yield": round(len(accepted) / n_screened, 4) if n_screened else None,
            "yield_wilson95": M.wilson(len(accepted), n_screened) if n_screened else None,
            "passed_rule_gates": n_rules,
            "yield_rules_only": round(n_rules / n_screened, 4) if n_screened else None,
            "independent_filter_hits": dict(hits),
            "first_match_funnel": dict(funnel),
            "gates": list(GATES), "reported_not_gated": list(REPORTED),
            "topics_covered": dict(sorted(topics_covered.items())),
            "n_topics_covered": len(topics_covered),
            "docs_used": len(per_doc_accepted),
            "accepted_per_doc_max": max(per_doc_accepted.values(), default=0),
            "unit_index_distribution": dict(sorted(unit_idx.items())),
            "query_words": {
                "median": statistics.median(words) if words else None,
                "mean": round(statistics.mean(words), 2) if words else None,
                "min": min(words) if words else None, "max": max(words) if words else None,
                "distribution": dict(sorted(collections.Counter(words).items())),
            },
        },
        "gpu_after": C.gpu_snapshot(),
        "wall_seconds": round(time.time() - t_wall, 1),
        "outputs": {"accepted": str(acc_p), "rejected": str(rej_p), "raw": str(raw_p)},
    }
    C.atomic_json(OUT / f"pointed-manifest{tag}.json", man)
    print(json.dumps({k: v for k, v in man.items()
                      if k in ("counts", "endpoint", "section_selection")}, indent=1),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
