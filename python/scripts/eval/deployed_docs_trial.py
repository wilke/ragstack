#!/usr/bin/env python
"""Exploratory offline trial: chunk size x overlap on a deployed documentation collection.

The pre-registrations are kept with the outputs, outside the repo; redacted
copies are in ``docs/plans/results/chunk-size-deployed-docs-2026-10-23/prereg/``.

Everything that identifies the collection or the private eval set (the store
URL and index name, endpoint hosts, the seed questions, the generation /
judge / reader / grader prompts, the stratum prefixes and the file names) is
read at startup from a PRIVATE JSON config (``--private-config`` or env
``CHUNKTRIAL_PRIVATE_CONFIG``); nothing private is in this file.

The collection is READ IN PLACE from the tenant's loopback Elasticsearch with
``_search`` only (no copy of it is written). Everything else runs in process,
reusing :mod:`section_aware_trial` (BM25, dense, reranker, quantized dense,
scoring, McNemar contrasts). Only derived artifacts are written: a manifest of
(doc_id, chunk count, sha256 of reassembled text), the generated questions,
embedding / rerank / judge caches and the results.

Subcommands:

* ``prepare``: read the index, reassemble pages, manifest and coverage checks,
  stratified page/passage sampling, question generation on a local
  Qwen3.8-27B endpoint (never a denied port), validation and the echo measure.
* ``run``: build L + the 16 T/X arms + the storage variants, retrieve for the
  generated questions and the seed set, judge the seed W group's pool with a
  local Llama-4-Scout endpoint, and write the part-1/2 results file.
* ``addendum-s``, ``part3``, ``k10``: see the redacted pre-registrations.
* ``part4``: the confirmatory part 4, in :mod:`deployed_docs_part4` (pause and
  resume; exit 75 = paused). Its config keys are documented there.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import section_aware_trial as sat  # noqa: E402
from section_aware_trial import (  # noqa: E402
    ArmSpec,
    C,
    Gold,
    Setup,
    contrast,
    found,
    log,
    norm,
    passage,
    read_jsonl,
    run_configs,
)

# Private values: placeholders here, filled by apply_private_config() from the private config.
ES_URL = ""
INDEX = ""
GEN_URL = ""
GEN_MODEL = "Qwen3.8-27B"
JUDGE_URL = ""
JUDGE_MODEL = "RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic"
#: Always refused, whatever the config says: :8004 is a shared endpoint another service depends on.
FORBIDDEN_PORTS_FLOOR = frozenset({8004})
FORBIDDEN_PORTS: set[int] = set(FORBIDDEN_PORTS_FLOOR)
REQUIRED_CONFIG_KEYS = (
    "es_url", "index", "gen_url", "judge_url", "gateway_url", "deny_ports", "deny_reason",
    "stratum_prefixes", "strata", "seed_stratum", "files",
)
FORBIDDEN_REASON = ""
MAX_LLM_INFLIGHT = 4
SEED = 261023
N_PAGES = 150
MIN_PAGE_CHARS = 400
PASSAGE_CHARS = 1200
MAX_QUOTE_CHARS = 300
ECHO_HIGH = 5
POOL = "all"
SIZES = (128, 256, 512, 1024)
FAMILIES = ("T", "X")
DENSE = ("int8", "int8+r", "bin", "bin+r")
STRATA: tuple[str, ...] = ()
STRATUM_PREFIXES: tuple[tuple[str, str], ...] = ()
SEED_STRATUM = ""
FILES: dict[str, str] = {}
LONG_PAGE_TOKENS = 2048

GEN_SYSTEM = ""
GEN_USER = ""
JUDGE_USER = ""

# ------------------------------------------------------------- seed questions (part 1)

#: id -> (question, [gold page filenames]) / id -> question. Private: from the config.
SEED_P: dict[str, tuple[str, list[str]]] = {}
SEED_N: dict[str, str] = {}
SEED_W: dict[str, str] = {}


# ------------------------------------------------------------- private config

def alt_results(name: str, key: str) -> str:
    """``results-x.json`` -> ``results-x.<key>.json`` (a non-primary judge's file)."""
    assert name.endswith(".json")
    return f"{name[:-5]}.{key}.json"


def apply_private_config(cfg: dict[str, Any]) -> None:
    """Set the private module globals from ``cfg`` (and the values derived from them)."""
    global ES_URL, INDEX, GEN_URL, JUDGE_URL, OPUS_URL, QWEN_URL, FORBIDDEN_PORTS, FORBIDDEN_REASON
    global STRATA, STRATUM_PREFIXES, SEED_STRATUM, FILES, SEED_P, SEED_N, SEED_W
    global GEN_SYSTEM, GEN_USER, JUDGE_USER, M_USER, D_USER, JUDGE3_USER, READER_SYSTEM, GRADER_USER, K10_READERS
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise SystemExit(f"private config is missing required keys: {', '.join(missing)}")
    ES_URL, INDEX = cfg["es_url"], cfg["index"]
    GEN_URL, JUDGE_URL, OPUS_URL = cfg["gen_url"], cfg["judge_url"], cfg["gateway_url"]
    QWEN_URL = GEN_URL
    FORBIDDEN_PORTS = set(FORBIDDEN_PORTS_FLOOR) | set(cfg["deny_ports"])
    FORBIDDEN_REASON = cfg["deny_reason"]
    STRATUM_PREFIXES = tuple((p, lab) for p, lab in cfg["stratum_prefixes"])
    STRATA = tuple(cfg["strata"])
    SEED_STRATUM = cfg["seed_stratum"]
    FILES = dict(cfg["files"])
    sq = cfg["seed_questions"]
    SEED_P = {k: (v[0], list(v[1])) for k, v in sq["P"].items()}
    SEED_N, SEED_W = dict(sq["N"]), dict(sq["W"])
    pr = cfg["prompts"]
    GEN_SYSTEM, GEN_USER, JUDGE_USER = pr["GEN_SYSTEM"], pr["GEN_USER"], pr["JUDGE_USER"]
    M_USER, JUDGE3_USER = pr["M_USER"], pr["JUDGE3_USER"]
    READER_SYSTEM, GRADER_USER = pr["READER_SYSTEM"], pr["GRADER_USER"]
    if GEN_USER.count(_FIRST_RULE_END) != 1:
        raise SystemExit("private config: GEN_USER must contain the first-rule marker exactly once")
    D_USER = GEN_USER.replace(_FIRST_RULE_END, _FIRST_RULE_END + D_RULE)
    K10_READERS = {"scout": (JUDGE_URL, JUDGE_MODEL, 60000, None),
                   "qwen": (GEN_URL, GEN_MODEL, 131072, {"chat_template_kwargs": {"enable_thinking": False}})}
    sat.apply_private_config(cfg.get("section_aware_trial", {}))


def load_private_config(path: str | None) -> dict[str, Any]:
    if not path:
        raise SystemExit("a private config is required: pass --private-config or set CHUNKTRIAL_PRIVATE_CONFIG")
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"private config not found: {p}")
    cfg = json.loads(p.read_text())
    apply_private_config(cfg)
    return cfg


# ------------------------------------------------------------------ helpers

def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stratum(filename: str) -> str:
    for pre, s in STRATUM_PREFIXES:
        if filename.startswith(pre):
            return s
    return "other"


def check_url(url: str) -> None:
    port = int(url.rstrip("/").rsplit(":", 1)[1])
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"refusing {url}: :{port} is denied ({FORBIDDEN_REASON})")


def read_index() -> list[dict[str, Any]]:
    """Every chunk of the live index via one read-only ``_search`` (1,265 rows)."""
    import httpx

    with httpx.Client(timeout=60) as http:
        n = http.get(f"{ES_URL}/{INDEX}/_count").json()["count"]
        r = http.post(f"{ES_URL}/{INDEX}/_search", json={
            "size": n, "track_total_hits": True, "sort": [{"_doc": "asc"}],
            "_source": ["chunk_id", "doc_id", "content", "start_char", "end_char", "metadata", "symbol_glossary"]})
        r.raise_for_status()
        hits = [h["_source"] for h in r.json()["hits"]["hits"]]
    if len(hits) != n:
        raise SystemExit(f"read {len(hits)} of {n} chunks")
    return hits


def pages_from_index(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    from ragstack.ingestion.reassembly import reassemble

    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_doc[str(r["doc_id"])].append(r)
    pages: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for did in sorted(by_doc):
        ra = reassemble(did, by_doc[did])
        fn = str((by_doc[did][0].get("metadata") or {}).get("filename") or "")
        if ra.gaps or ra.length_mismatches:
            failed.append(did)
            continue
        pages[did] = {"doc_id": did, "filename": fn, "stratum": stratum(fn), "text": ra.text,
                      "rows": sorted(by_doc[did], key=lambda x: (int(x["start_char"]), int(x["end_char"])))}
    meta = {"chunks": len(rows), "docs": len(by_doc), "reassembly_failed": len(failed),
            "reassembly_failed_ids": failed,
            "strata_pages": dict(Counter(p["stratum"] for p in pages.values()))}
    return pages, meta


def write_or_check_manifest(pages: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
    man = {d: {"chunks": len(p["rows"]), "sha256": sha(p["text"])} for d, p in sorted(pages.items())}
    if not path.exists():
        path.write_text(json.dumps(man, indent=0))
        return {"manifest": "written", "docs": len(man)}
    old = json.loads(path.read_text())
    drift = sorted(d for d in set(old) | set(man) if old.get(d) != man.get(d))
    return {"manifest": "checked", "docs": len(man), "drift_docs": len(drift), "drift_ids": drift}


_SENT_END = re.compile(r"(?:[.!?][\"')\]]*\s+|\n+)")


def snap_sentence(text: str, pos: int) -> int:
    """The next sentence start at or after ``pos``: the position after a
    sentence end ([.!?] + optional closer + whitespace) or a newline run."""
    if pos == 0:
        return 0
    m = _SENT_END.search(text, pos - 1 if pos > 0 else 0)
    return m.end() if m and m.end() < len(text) else pos


def sample(pages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    elig = {s: sorted(d for d, p in pages.items() if p["stratum"] == s and len(p["text"]) >= MIN_PAGE_CHARS)
            for s in STRATA}
    total = sum(len(v) for v in elig.values())
    quota = {s: N_PAGES * len(v) / total for s, v in elig.items()}
    base = {s: int(math.floor(q)) for s, q in quota.items()}
    rest = N_PAGES - sum(base.values())
    for s in sorted(STRATA, key=lambda s: (-(quota[s] - base[s]), STRATA.index(s)))[:rest]:
        base[s] += 1
    out = []
    for s in STRATA:
        for did in rng.sample(elig[s], base[s]):
            text = pages[did]["text"]
            if len(text) <= PASSAGE_CHARS:
                start = 0
            else:
                start = snap_sentence(text, rng.randint(0, len(text) - PASSAGE_CHARS))
            out.append({"doc_id": did, "stratum": s, "p_start": start, "p_end": min(len(text), start + PASSAGE_CHARS)})
    return out


def echo(question: str, passage_: str) -> int:
    a = re.findall(r"\w+", question.lower())
    b = re.findall(r"\w+", passage_.lower())
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def parse_json_answer(s: str) -> dict[str, Any] | None:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("question"), str) or not isinstance(d.get("answer_quote"), str):
        return None
    return d


def check_single(q: str, quote: str, text: str) -> str:
    """Part-2 validation of a single-passage question: the status
    (``empty_question`` / ``quote_too_long`` / ``quote_not_in_page`` / ``valid``)."""
    if not q:
        return "empty_question"
    if len(quote) > MAX_QUOTE_CHARS:
        return "quote_too_long"
    if not norm(quote) or norm(quote) not in norm(text):
        return "quote_not_in_page"
    return "valid"


def check_pair(q: str, qa: str, qb: str, text: str, a_span: tuple[int, int], b_span: tuple[int, int],
               nm: tuple[str, list[int]] | Any, gap: int) -> tuple[str, int | None]:
    """Part-3 validation of a two-passage (M) question: (status, quote gap or
    None). ``nm`` is :func:`norm_map` of ``text`` or a zero-argument callable
    returning it (so it is computed only when the quotes are found). The gap is
    returned whenever both quote positions resolve (``valid`` or
    ``quotes_too_close``)."""
    nt = norm(text)
    if not q:
        return "empty_question", None
    if len(qa) > MAX_QUOTE_CHARS or len(qb) > MAX_QUOTE_CHARS:
        return "quote_too_long", None
    if not norm(qa) or not norm(qb) or norm(qa) not in nt or norm(qb) not in nt:
        return "quote_not_in_page", None
    ntext, pos = nm() if callable(nm) else nm
    pa = quote_pos(norm(qa), ntext, pos, a_span)
    pb = quote_pos(norm(qb), ntext, pos, b_span)
    if pa is None or pb is None:
        return "quote_position_unresolved", None
    return ("valid" if abs(pb - pa) >= gap else "quotes_too_close"), abs(pb - pa)


def walk_budget(lst: list[str], bud: int, tokens: Any) -> list[str]:
    """Whole chunks of ``lst`` in order while the running token total stays
    <= ``bud`` (``tokens(cid)`` gives a chunk's tokens); the first chunk that
    would exceed it stops the walk."""
    tot, keep = 0, []
    for cid in lst:
        tot += tokens(cid)
        if tot > bud:
            break
        keep.append(cid)
    return keep


def reader_messages(question: str, passages: list[str], limit: int, ntok: Any, system: str,
                    reserve: int = 512) -> tuple[list[dict[str, str]], int, str]:
    """The k=10 test's reader prompt over ``passages`` (already rendered with
    their glossary): trailing excerpts are dropped while the prompt exceeds
    ``limit - reserve`` tokens. Returns (messages, excerpts dropped, excerpts body)."""
    ps = list(passages)
    dropped = 0
    while True:
        body = "\n\n".join(f"[{n}] {p}" for n, p in enumerate(ps, 1))
        user = f"Documentation excerpts:\n\n{body}\n\nQuestion: {question}"
        if ntok(system + user) <= limit - reserve or len(ps) <= 1:
            return [{"role": "system", "content": system}, {"role": "user", "content": user}], dropped, body
        ps.pop()
        dropped += 1


def grader_messages(question: str, gold: list[str], answer: str) -> list[dict[str, str]]:
    """The k=10 test's grader prompt (GRADER_USER from the private config)."""
    return [{"role": "user", "content": GRADER_USER.format(question=question, gold="\n".join(gold), answer=answer)}]


def load_cache(p: Path) -> dict[str, dict[str, Any]]:
    """A ``{"k": ...}`` JSONL cache as {k: row} (the last row of a key wins)."""
    return {x["k"]: x for x in read_jsonl(p)} if p.exists() else {}


async def chat_many(url: str, model: str, prompts: list[list[dict[str, str]]], max_tokens: int,
                    extra: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
    import httpx

    check_url(url)
    sem = asyncio.Semaphore(MAX_LLM_INFLIGHT)
    out: list[tuple[str, dict[str, Any]]] = [("", {})] * len(prompts)
    async with httpx.AsyncClient(timeout=600) as http:
        async def one(i: int, msgs: list[dict[str, str]]) -> None:
            body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens, **(extra or {})}
            async with sem:
                for attempt in range(4):
                    try:
                        r = await http.post(f"{url}/v1/chat/completions", json=body)
                        r.raise_for_status()
                        d = r.json()
                        break
                    except Exception:  # noqa: BLE001
                        if attempt == 3:
                            raise
                        await asyncio.sleep(3 * (attempt + 1))
            out[i] = (d["choices"][0]["message"].get("content") or "",
                      {"usage": d.get("usage") or {}, "finish": d["choices"][0].get("finish_reason")})

        await asyncio.gather(*(one(i, m) for i, m in enumerate(prompts)))
    return out


# ------------------------------------------------------------------ prepare

def cmd_prepare(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    rows = read_index()
    pages, meta = pages_from_index(rows)
    log(f"index: {meta['chunks']} chunks, {meta['docs']} docs; reassembly failed {meta['reassembly_failed']}; "
        f"strata {meta['strata_pages']}")
    man = write_or_check_manifest(pages, out / "manifest-261023.json")
    log(f"manifest: {json.dumps({k: v for k, v in man.items() if k != 'drift_ids'})}")
    if meta["reassembly_failed"] > 5:
        log("STOP: more than 5 pages fail reassembly")
        return 2
    qpath = out / "questions-261023.jsonl"
    if qpath.exists():
        log(f"{qpath} exists; not regenerating")
        return 0
    picks = sample(pages)
    log(f"sampled {len(picks)} pages: {dict(Counter(p['stratum'] for p in picks))}")
    log(f"generation: {len(picks)} calls on {GEN_URL} ({GEN_MODEL}), <= {MAX_LLM_INFLIGHT} in flight")
    extra = {"chat_template_kwargs": {"enable_thinking": False}}

    def prompt(p: dict[str, Any]) -> list[dict[str, str]]:
        psg = pages[p["doc_id"]]["text"][p["p_start"]:p["p_end"]]
        return [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": GEN_USER.format(passage=psg)}]

    t0 = time.time()
    res = asyncio.run(chat_many(GEN_URL, GEN_MODEL, [prompt(p) for p in picks], 512, extra))
    calls = len(picks)
    parsed = [parse_json_answer(a) for a, _ in res]
    retry = [i for i, d in enumerate(parsed) if d is None]
    if retry:
        log(f"retrying {len(retry)} unparseable answers once")
        res2 = asyncio.run(chat_many(GEN_URL, GEN_MODEL, [prompt(picks[i]) for i in retry], 512, extra))
        calls += len(retry)
        for i, (a, info) in zip(retry, res2, strict=True):
            parsed[i] = parse_json_answer(a)
            res[i] = (a, info)
    st: Counter[str] = Counter()
    usage = Counter()
    with qpath.open("w") as fh:
        for n, (p, d, (_, info)) in enumerate(zip(picks, parsed, res, strict=True)):
            for k in ("prompt_tokens", "completion_tokens"):
                usage[k] += int((info.get("usage") or {}).get(k) or 0)
            page = pages[p["doc_id"]]
            rec = {"record_id": f"g{n:03d}", **p, "filename": page["filename"], "finish": info.get("finish")}
            if d is None:
                rec["status"] = "unparseable"
            else:
                q, quote = d["question"].strip(), d["answer_quote"]
                rec.update(question=q, answer_quote=quote)
                rec["status"] = check_single(q, quote, page["text"])
                if rec["status"] == "valid":
                    rec["echo"] = echo(q, page["text"][p["p_start"]:p["p_end"]])
            st[rec["status"]] += 1
            fh.write(json.dumps(rec) + "\n")
    valid = [r for r in read_jsonl(qpath) if r["status"] == "valid"]
    gen = {"calls": calls, "seconds": round(time.time() - t0, 1), "usage": dict(usage), "status": dict(st),
           "valid": len(valid), "high_echo": sum(r["echo"] >= ECHO_HIGH for r in valid),
           "low_echo": sum(r["echo"] < ECHO_HIGH for r in valid),
           "echo_median": statistics.median(r["echo"] for r in valid) if valid else None}
    (out / "generation-261023.json").write_text(json.dumps({"index": dict(meta),
                                                            "manifest": man, "generation": gen}, indent=1))
    log(f"generation: {json.dumps(gen)}")
    return 0


# ---------------------------------------------------------------------- arms

_HEAD = re.compile(r"^#+", re.M)


def md_sections(text: str) -> list[tuple[int, str, str]]:
    """(offset, heading line, section text): a section starts at a line that
    begins with '#'; text before the first heading is a section."""
    starts = [m.start() for m in _HEAD.finditer(text)]
    bounds = sorted({0, *starts, len(text)})
    out = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        seg = text[a:b]
        if not seg.strip():
            continue
        head = seg.split("\n", 1)[0].strip() if seg.startswith("#") else ""
        out.append((a, head, seg))
    return out


def arm_name(fam: str, s: int, ov: int) -> str:
    return f"{fam}{s}o{ov}"


ARMS = tuple(arm_name(f, s, ov) for f in FAMILIES for s in SIZES for ov in (0, s // 8))


def build_arms(pages: dict[str, dict[str, Any]]) -> tuple[dict[str, list[C]], dict[str, Any]]:
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker
    from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    counter = make_token_counter("hf", model=sat.SFR_MODEL)
    chunkers = {(s, ov): FixedTokenWindowChunker(s, ov, token_counter=counter) for s in SIZES for ov in (0, s // 8)}
    att = SymbolGlossaryAttacher()  # rules/3, no LLM cache
    arms: dict[str, list[C]] = {"L": [], **{a: [] for a in ARMS}}
    st: Counter[str] = Counter()
    for did, p in sorted(pages.items()):
        for n, r in enumerate(p["rows"]):
            arms["L"].append(C(f"L|{did}|{n}", did, str(r["content"]), "", str(r.get("symbol_glossary") or "")))
        text = p["text"]
        g = att.glossary_for(text)
        secs = md_sections(text)
        st["pages_with_heading"] += int(any(h for _, h, _ in secs))
        st["sections"] += len(secs)
        for (s, ov), ck in chunkers.items():
            a = arm_name("T", s, ov)
            for n, ch in enumerate(ck.chunk(Document(id=did, content=text))):
                arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "",
                                 g.for_chunk(ch.content, att.config, start_char=ch.start_char)))
            a = arm_name("X", s, ov)
            n = 0
            for off, head, seg in secs:
                for ch in ck.chunk(Document(id=did, content=seg)):
                    arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, head,
                                     g.for_chunk(ch.content, att.config, start_char=off + ch.start_char)))
                    n += 1
    tok = counter._tokenizer()
    for cs in arms.values():
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)
    page_tokens = {did: len(tok([p["text"]], add_special_tokens=False)["input_ids"][0]) for did, p in pages.items()}
    st["L_chunks_with_live_glossary"] = sum(1 for c in arms["L"] if c.glossary)
    st["T512o64_chunks_with_rules_glossary"] = sum(1 for c in arms["T512o64"] if c.glossary)
    return arms, {"stats": dict(st), "page_tokens": page_tokens}


# ----------------------------------------------------------------------- run

_FULL_LISTS: dict[int, list[list[str]]] = {}
_orig_score = sat.score_config


def _score_with_lists(su: Setup, cs: list[C], bm_l, hy_l, rr_l) -> dict[str, Any]:
    per = _orig_score(su, cs, bm_l, hy_l, rr_l)
    for qi, rec in enumerate(su.records):
        per[rec["record_id"]]["rr_list"] = [cs[i].id for i in rr_l[qi]]
    return per


def split_classes(su: Setup, arm: str) -> dict[str, str]:
    big = "\x00".join(su.gold.text(c) for c in su.arms[arm])
    out = {}
    for r in su.records:
        q = su.gold.q[r["record_id"]]
        if not q:
            continue
        if q in big:
            out[r["record_id"]] = "contained"
            continue
        m = math.ceil(0.6 * len(q))
        out[r["record_id"]] = "partial" if (q[:m] in big or q[-m:] in big) else "split"
    return out


def counts(per: dict[str, Any], ids: list[str]) -> dict[str, int]:
    return {"n": len(ids), "b2048": sum(found(per[i], "b2048") for i in ids),
            "b5120": sum(found(per[i], "b5120") for i in ids), "r10": sum(found(per[i], "rerank") for i in ids),
            "r10_full": sum(found(per[i], "rerank_full") for i in ids),
            "bm25": sum(found(per[i], "bm25") for i in ids), "hybrid": sum(found(per[i], "hybrid") for i in ids)}


def strat_contrast(a: dict[str, Any], b: dict[str, Any], su: Setup, ids: list[str],
                   rec_stratum: dict[str, str]) -> dict[str, Any]:
    sub = Setup(arms={}, pools={}, records=[r for r in su.records if r["record_id"] in set(ids)], gold=su.gold,
                gold_fold=su.gold_fold, rec_paper=su.rec_paper, jats_docs=set())
    out = {}
    for leg in ("rerank", "b2048", "b5120"):
        c = contrast(a, b, sub, leg)
        c.pop("net_bazant", None)
        c.pop("net_other_papers", None)
        c["net_by_stratum"] = {s: sum(rec_stratum[x] == s for x in c["gained_ids"])
                               - sum(rec_stratum[x] == s for x in c["lost_ids"]) for s in STRATA}
        out[leg] = c
    return out


def rule(c: dict[str, Any], leg: str) -> dict[str, Any]:
    net = c[leg]["net"]
    strata_pos = sum(1 for v in c[leg]["net_by_stratum"].values() if v > 0)
    r1, r2, r3 = net >= 5, c["b5120"]["net"] >= 0, strata_pos >= 2
    return {"leg": leg, "net": net, "net_b5120": c["b5120"]["net"], "strata_with_net_gain": strata_pos,
            "rule1_net_ge_5": r1, "rule2_no_loss_b5120": r2, "rule3_gain_in_2_strata": r3, "passes": r1 and r2 and r3}


def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    w = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(w / (len(pos) * len(neg)), 4)


def cmd_run(args: argparse.Namespace) -> int:
    t0 = time.time()
    out = Path(args.out_dir)
    rows = read_index()
    pages, imeta = pages_from_index(rows)
    man = write_or_check_manifest(pages, out / "manifest-261023.json")
    log(f"manifest: {json.dumps({k: v for k, v in man.items() if k != 'drift_ids'})}")
    if man.get("drift_docs"):
        log("WARNING: manifest drift (logged as a deviation)")
    arms, ameta = build_arms(pages)
    log(f"arms built: {json.dumps({a: len(cs) for a, cs in arms.items()})}; {json.dumps(ameta['stats'])}")

    qs = [r for r in read_jsonl(out / "questions-261023.jsonl") if r["status"] == "valid"]
    records: list[dict[str, Any]] = []
    pairs: dict[str, dict[str, Any]] = {}
    rec_stratum: dict[str, str] = {}
    for r in qs:
        records.append({"record_id": r["record_id"], "pair": r["record_id"], "kind": "gen", "query": r["question"]})
        pairs[r["record_id"]] = {"expected": {"quote": r["answer_quote"]}}
        rec_stratum[r["record_id"]] = r["stratum"]
    ask: dict[str, str] = {}
    for rid, (q, _) in SEED_P.items():
        ask[rid] = "P"
        records.append({"record_id": rid, "pair": rid, "kind": "P", "query": q})
    for rid, q in SEED_W.items():
        ask[rid] = "W"
        records.append({"record_id": rid, "pair": rid, "kind": "W", "query": q})
    for rid, q in SEED_N.items():
        ask[rid] = "N"
        records.append({"record_id": rid, "pair": rid, "kind": "N", "query": q})
    for rid in ask:
        pairs[rid] = {"expected": {"quote": ""}}
        rec_stratum[rid] = SEED_STRATUM
    su = Setup(arms=arms, pools={POOL: set()}, records=records, gold=Gold(records, pairs),
               gold_fold=Gold(records, pairs, sat.fold), rec_paper={r["record_id"]: "" for r in records},
               jats_docs=set())

    specs = {"L": ArmSpec("L", False, False, False), **{a: ArmSpec(a, False, False, False) for a in ARMS}}
    t_arms = [a for a in ARMS if a.startswith("T")]
    for a in t_arms:
        for m in DENSE:
            specs[f"{a}~{m}"] = ArmSpec(a, False, False, False, dense=m)
    configs = [(a, g, 0, POOL, 1.0) for a in ("L", *ARMS) for g in ("rules", "off")]
    configs += [(f"{a}~{m}", "rules", 0, POOL, 1.0) for a in t_arms for m in DENSE]
    ns = argparse.Namespace(embed_urls=args.embed_urls, embed_model=sat.SFR_MODEL, rerank_url=args.rerank_url,
                            out_dir=str(out))
    sat.score_config = _score_with_lists
    try:
        res = asyncio.run(run_configs(su, configs, ns, specs))
    finally:
        sat.score_config = _orig_score
    t_retrieval = time.time() - t0
    log(f"retrieval done in {t_retrieval:.0f}s; configs {len(res)}")

    gen_ids = [r["record_id"] for r in qs]
    low_ids = [r["record_id"] for r in qs if r["echo"] < ECHO_HIGH]
    long_ids = [r["record_id"] for r in qs if ameta["page_tokens"][r["doc_id"]] > LONG_PAGE_TOKENS]
    cname = sat.config_name
    summary = {name: {"all": counts(per, gen_ids), "low_echo": counts(per, low_ids),
                      "long_pages": counts(per, long_ids)} for name, per in res.items()}

    def nm(a: str, g: str = "rules") -> str:
        return cname(a, g, 0, POOL, 1.0)

    contrasts: dict[str, dict[str, Any]] = defaultdict(dict)
    verdict: dict[str, Any] = {}
    for g in ("rules", "off"):
        for sub_name, ids in (("all", gen_ids), ("low_echo", low_ids), ("long_pages", long_ids)):
            for f in FAMILIES:
                for s in SIZES:
                    k = f"{g}|{sub_name}|{arm_name(f, s, s // 8)} - {arm_name(f, s, 0)}"
                    c = strat_contrast(res[nm(arm_name(f, s, s // 8), g)], res[nm(arm_name(f, s, 0), g)], su, ids,
                                       rec_stratum)
                    contrasts["1 overlap"][k] = c
                    if sub_name == "all":
                        verdict[f"overlap {k}"] = rule(c, "rerank")
                    for ov0 in (0, 1):
                        if s == 512:
                            continue
                        ov = s // 8 if ov0 else 0
                        ref = arm_name(f, 512, 64 if ov0 else 0)
                        contrasts["2 size"][f"{g}|{sub_name}|{arm_name(f, s, ov)} - {ref}"] = strat_contrast(
                            res[nm(arm_name(f, s, ov), g)], res[nm(ref, g)], su, ids, rec_stratum)
                    if f == "X":
                        for ov in (0, s // 8):
                            contrasts["3 sections X-T"][f"{g}|{sub_name}|X{s}o{ov} - T{s}o{ov}"] = strat_contrast(
                                res[nm(f"X{s}o{ov}", g)], res[nm(f"T{s}o{ov}", g)], su, ids, rec_stratum)
            for a in ARMS:
                c = strat_contrast(res[nm(a, g)], res[nm("L", g)], su, ids, rec_stratum)
                contrasts["4 vs live"][f"{g}|{sub_name}|{a} - L"] = c
                if sub_name == "all":
                    verdict[f"vs-live {g}|{a} - L"] = rule(c, "b5120")
    for a in t_arms:
        for m in DENSE:
            for sub_name, ids in (("all", gen_ids), ("low_echo", low_ids)):
                contrasts["5 storage"][f"{sub_name}|{a}~{m} - {a}"] = strat_contrast(
                    res[nm(f"{a}~{m}")], res[nm(a)], su, ids, rec_stratum)

    split = {}
    gsub = Setup(arms=arms, pools={}, records=[r for r in records if r["kind"] == "gen"], gold=su.gold,
                 gold_fold=su.gold_fold, rec_paper={}, jats_docs=set())
    for a in ("L", *ARMS):
        cl = split_classes(gsub, a)
        split[a] = dict(Counter(cl.values()))
        split[a]["_ids_not_contained"] = sorted(k for k, v in cl.items() if v != "contained")
    storage = {a: sat.d_storage(cs) for a, cs in arms.items()}

    # ---------------------------------------------------------- part 1
    by_id = {c.id: c for cs in arms.values() for c in cs}
    fname = {d: p["filename"] for d, p in pages.items()}
    rr = sat.Reranker(args.rerank_url, out / "rerank_cache.jsonl")
    queries = {r["record_id"]: r["query"] for r in records}

    def budget_walk(lst: list[str], bud: int) -> list[str]:
        return walk_budget(lst, bud, lambda cid: by_id[cid].tokens)

    # W judging pool: every arm's chunks within the b5120 walk (which contains the top 10
    # whenever the top 10 fits; the top 10 is added explicitly) -- deviation logged.
    pool_texts: dict[str, set[str]] = defaultdict(set)
    for per in res.values():
        for rid, k in ask.items():
            if k != "W":
                continue
            lst = per[rid]["rr_list"]
            for cid in set(budget_walk(lst, 5120)) | set(lst[:sat.TOP_K]):
                pool_texts[rid].add(by_id[cid].content)
    judge_path = out / "judge_cache-261023.jsonl"
    jc: dict[str, str] = {}
    if judge_path.exists():
        for x in read_jsonl(judge_path):
            jc[x["k"]] = x["v"]
    todo = [(rid, t) for rid, ts in sorted(pool_texts.items()) for t in sorted(ts)
            if sha(rid + "\x00" + t) not in jc]
    log(f"judge: pool {sum(len(v) for v in pool_texts.values())} (question, chunk) pairs; "
        f"{len(todo)} new calls on {JUDGE_URL}, <= {MAX_LLM_INFLIGHT} in flight")
    t_j = time.time()
    if todo:
        ans = asyncio.run(chat_many(JUDGE_URL, JUDGE_MODEL, [[{"role": "user", "content": JUDGE_USER.format(
            question=queries[rid], chunk=t)}] for rid, t in todo], 16))
        with judge_path.open("a") as fh:
            for (rid, t), (a, _) in zip(todo, ans, strict=True):
                w = re.findall(r"[a-z]+", a.lower())
                v = w[0] if w and w[0] in ("yes", "partial", "no") else "unparseable"
                jc[sha(rid + "\x00" + t)] = v
                fh.write(json.dumps({"k": sha(rid + "\x00" + t), "v": v}) + "\n")
    t_judge = time.time() - t_j

    def judged(rid: str, cid: str) -> str:
        v = jc.get(sha(rid + "\x00" + by_id[cid].content), "unjudged")
        return "no" if v == "unparseable" else v

    part1: dict[str, Any] = {"judge_calls": len(todo), "judge_unparseable": sum(v == "unparseable" for v in jc.values()),
                             "judge_pool": {k: len(v) for k, v in pool_texts.items()},
                             "judge_verdicts": dict(Counter(jc.values())), "per_config": {}}
    for name, per in res.items():
        row: dict[str, Any] = {"P": {}, "W": {}, "top1": {}}
        for rid, k in ask.items():
            lst = per[rid]["rr_list"]
            top = by_id[lst[0]] if lst else None
            if top is not None:
                gm = name.split("|")[1][1:]
                gm = {"on": "rules"}.get(gm, gm)
                row["top1"][rid] = round(rr.s[rr.key(queries[rid], passage(top, gm, False))], 5)
            if k == "P":
                gold = set(SEED_P[rid][1])
                row["P"][rid] = {leg: any(fname[by_id[c].doc_id] in gold for c in sel) for leg, sel in
                                 (("r10", lst[:sat.TOP_K]), ("b2048", budget_walk(lst, 2048)),
                                  ("b5120", budget_walk(lst, 5120)))}
            elif k == "W":
                row["W"][rid] = {}
                for leg, sel in (("r10", lst[:sat.TOP_K]), ("b2048", budget_walk(lst, 2048)),
                                 ("b5120", budget_walk(lst, 5120))):
                    vs = [judged(rid, c) for c in sel]
                    row["W"][rid][leg] = "yes" if "yes" in vs else ("partial" if "partial" in vs else "no")
                    if "unjudged" in vs:
                        row["W"][rid][f"{leg}_unjudged"] = vs.count("unjudged")
        pos = [row["top1"][r] for r, k in ask.items() if k in ("P", "W") and r in row["top1"]]
        neg = [row["top1"][r] for r, k in ask.items() if k == "N" and r in row["top1"]]
        neg_nom5 = [row["top1"][r] for r, k in ask.items() if k == "N" and r != "M5" and r in row["top1"]]
        lo, hi = (min(pos), max(pos)) if pos else (None, None)
        row["auroc_PW_vs_N"] = auroc(pos, neg)
        row["auroc_PW_vs_N_without_M5"] = auroc(pos, neg_nom5)
        row["negatives_in_positive_range"] = sorted(r for r, k in ask.items() if k == "N" and lo is not None
                                                    and lo <= row["top1"][r] <= hi)
        row["paired_w_minus_n"] = {w: round(row["top1"][w] - row["top1"][w[:-1]], 5) for w in SEED_W}
        row["P_found"] = {leg: sum(v[leg] for v in row["P"].values()) for leg in ("r10", "b2048", "b5120")}
        row["W_yes"] = {leg: sum(v[leg] == "yes" for v in row["W"].values()) for leg in ("r10", "b2048", "b5120")}
        row["W_partial"] = {leg: sum(v[leg] == "partial" for v in row["W"].values())
                            for leg in ("r10", "b2048", "b5120")}
        part1["per_config"][name] = row

    from ragstack.provenance import experiment_provenance

    gen = json.loads((out / "generation-261023.json").read_text())
    report = {
        "prereg": {"part1": FILES["prereg_part1"], "part2": FILES["prereg_part2"],
                   "sha256": {"part1": sha((out / FILES["prereg_part1"]).read_text()),
                              "part2": sha((out / FILES["prereg_part2"]).read_text())}},
        "provenance": experiment_provenance(repo=str(sat._REPO_PY.parent)),
        "index": INDEX, "index_meta": imeta | {"manifest": man}, "arms_meta": ameta["stats"],
        "generation": gen["generation"], "n_questions": len(gen_ids), "n_low_echo": len(low_ids),
        "n_long_pages": len(long_ids),
        "questions_by_stratum": dict(Counter(rec_stratum[i] for i in gen_ids)),
        "summary": summary, "contrasts": contrasts, "verdicts": verdict, "split_quotes": split,
        "storage": storage, "part1": part1,
        "load": {**dict(sat.EMBED_STATS), "retrieval_seconds": round(t_retrieval), "judge_seconds": round(t_judge),
                 "wall_seconds": round(time.time() - t0)},
        "per_record_ranks": {n: {r: {k: v for k, v in x.items() if k != "rr_list"} for r, x in per.items()}
                             for n, per in res.items()},
    }
    path = out / FILES["results_main"]
    path.write_text(json.dumps(report, indent=1))
    log(f"wrote {path} ({time.time() - t0:.0f}s)")
    return 0


# ------------------------------------------------------------- addendum S

S_ARMS = {
    "SEMlive": {"chunk_size": 512, "chunk_overlap": 64},
    "SEM512": {"chunk_size": 512, "chunk_overlap": 64, "max_tokens": 512},
    "SEM256": {"chunk_size": 256, "chunk_overlap": 32, "max_tokens": 256},
}
S_COMMON = {"buffer_size": 3, "breakpoint_percentile_threshold": 80.0, "min_chunk_length": 500}


def size_dist(cs: list[C]) -> dict[str, Any]:
    t = sorted(c.tokens for c in cs)
    n = len(t)
    return {"chunks": n, "median": statistics.median(t), "p90": t[min(n - 1, math.ceil(0.9 * n) - 1)],
            "max": t[-1], "over_512": sum(x > 512 for x in t), "over_256": sum(x > 256 for x in t)}


def cmd_addendum_s(args: argparse.Namespace) -> int:
    from ragstack.ingestion.chunkers import make_chunker
    from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    t0 = time.time()
    out = Path(args.out_dir)
    rows = read_index()
    pages, _ = pages_from_index(rows)
    man = write_or_check_manifest(pages, out / "manifest-261023.json")
    log(f"manifest: {json.dumps({k: v for k, v in man.items() if k != 'drift_ids'})}")
    urls = [u for u in args.embed_urls.split(",") if u]
    for u in urls:
        if not 9001 <= int(u.rstrip("/").rsplit(":", 1)[1]) <= 9006:
            raise SystemExit(f"refusing embedding endpoint {u}")
    counter = make_token_counter("hf", model=sat.SFR_MODEL)

    def kwargs(a: str) -> dict[str, Any]:
        k = {**S_COMMON, **S_ARMS[a]}
        if "max_tokens" in k:
            k["token_counter"] = counter
        return k

    # Two passes (Addendum C): record every buffer text with a stub vector, embed
    # the missing ones, then chunk for real from the cache.
    recorded: set[str] = set()

    def rec_fn(texts):  # noqa: ANN001, ANN202
        recorded.update(texts)
        return [[1.0] for _ in texts]

    for a in S_ARMS:
        ck = make_chunker("semantic", embed_fn=rec_fn, **kwargs(a))
        for did, p in sorted(pages.items()):
            ck.chunk(Document(id=did, content=p["text"]))
    store = sat.EmbStore(out / "emb")
    n_new = len(store.missing(sorted(recorded)))
    log(f"semantic buffers: {len(recorded)} distinct texts, {n_new} to embed")
    asyncio.run(sat.embed_missing(store, sorted(recorded), urls, sat.SFR_MODEL))

    def real_fn(texts):  # noqa: ANN001, ANN202
        return store.matrix(list(texts)).tolist()

    att = SymbolGlossaryAttacher()
    arms: dict[str, list[C]] = {a: [] for a in S_ARMS}
    spans: dict[str, dict[str, list[tuple[int, int, str]]]] = {a: {} for a in S_ARMS}
    for a in S_ARMS:
        ck = make_chunker("semantic", embed_fn=real_fn, **kwargs(a))
        for did, p in sorted(pages.items()):
            g = att.glossary_for(p["text"])
            chs = ck.chunk(Document(id=did, content=p["text"]))
            spans[a][did] = [(ch.start_char, ch.end_char, ch.content) for ch in chs]
            for n, ch in enumerate(chs):
                arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "",
                                 g.for_chunk(ch.content, att.config, start_char=ch.start_char)))
    live = [C(f"L|{did}|{n}", did, str(r["content"]), "", "") for did, p in sorted(pages.items())
            for n, r in enumerate(p["rows"])]
    tok = counter._tokenizer()
    for cs in (*arms.values(), live):
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)

    # Reproduction: SEMlive vs the live chunks, per page.
    same_bounds = same_text = 0
    differing: list[str] = []
    for did, p in pages.items():
        lv = [(int(r["start_char"]), int(r["end_char"]), str(r["content"])) for r in p["rows"]]
        mine = spans["SEMlive"][did]
        b = [(s, e) for s, e, _ in lv] == [(s, e) for s, e, _ in mine]
        t = [x for _, _, x in lv] == [x for _, _, x in mine]
        same_bounds += b
        same_text += t
        if not (b and t):
            differing.append(did)
    repro = {"pages": len(pages), "pages_identical_boundaries": same_bounds, "pages_identical_text": same_text,
             "chunks_live": sum(len(p["rows"]) for p in pages.values()), "chunks_SEMlive": len(arms["SEMlive"]),
             "pages_differing": len(differing), "pages_differing_ids": sorted(differing)}
    log(f"reproduction: {json.dumps({k: v for k, v in repro.items() if k != 'pages_differing_ids'})}")
    dists = {"L": size_dist(live), **{a: size_dist(cs) for a, cs in arms.items()}}
    log(f"size distributions: {json.dumps(dists)}")
    bug = {"SEMlive_over_512": dists["SEMlive"]["over_512"], "SEM512_over_512": dists["SEM512"]["over_512"],
           "missing_cap_confirmed": dists["SEMlive"]["over_512"] > 0 and dists["SEM512"]["over_512"] == 0}

    # Retrieval (generated questions only, rules/3 glossary, cap 0).
    qs = [r for r in read_jsonl(out / "questions-261023.jsonl") if r["status"] == "valid"]
    records = [{"record_id": r["record_id"], "pair": r["record_id"], "kind": "gen", "query": r["question"]}
               for r in qs]
    pairs = {r["record_id"]: {"expected": {"quote": r["answer_quote"]}} for r in qs}
    rec_stratum = {r["record_id"]: r["stratum"] for r in qs}
    su = Setup(arms=arms, pools={POOL: set()}, records=records, gold=Gold(records, pairs),
               gold_fold=Gold(records, pairs, sat.fold), rec_paper={r["record_id"]: "" for r in records},
               jats_docs=set())
    specs = {a: ArmSpec(a, False, False, False) for a in S_ARMS}
    ns = argparse.Namespace(embed_urls=args.embed_urls, embed_model=sat.SFR_MODEL, rerank_url=args.rerank_url,
                            out_dir=str(out))
    res = asyncio.run(run_configs(su, [(a, "rules", 0, POOL, 1.0) for a in S_ARMS], ns, specs))
    main_res = json.loads((out / FILES["results_main"]).read_text())["per_record_ranks"]

    def per(a: str) -> dict[str, Any]:
        return res[sat.config_name(a, "rules", 0, POOL, 1.0)] if a in S_ARMS else \
            main_res[sat.config_name(a, "rules", 0, POOL, 1.0)]

    gen_ids = [r["record_id"] for r in qs]
    low_ids = [r["record_id"] for r in qs if r["echo"] < ECHO_HIGH]
    summary = {a: {"all": counts(per(a), gen_ids), "low_echo": counts(per(a), low_ids)}
               for a in (*S_ARMS, "L", "T512o0", "T256o0")}
    pairs_c = [("SEM512", "SEMlive"), ("SEM256", "SEMlive"), ("SEMlive", "L"), ("SEM512", "T512o0"),
               ("SEM256", "T256o0")]
    contrasts, verdicts = {}, {}
    for x, y in pairs_c:
        for sub_name, ids in (("all", gen_ids), ("low_echo", low_ids)):
            c = strat_contrast(per(x), per(y), su, ids, rec_stratum)
            contrasts[f"{sub_name}|{x} - {y}"] = c
            if sub_name == "all":
                verdicts[f"{x} - {y}"] = rule(c, "b5120")
    split = {}
    for a in S_ARMS:
        cl = split_classes(su, a)
        split[a] = dict(Counter(cl.values()))
    from ragstack.provenance import experiment_provenance

    report = {"prereg": FILES["prereg_S"],
              "prereg_sha256": sha((out / FILES["prereg_S"]).read_text()),
              "provenance": experiment_provenance(repo=str(sat._REPO_PY.parent)),
              "manifest": man, "chunker_params": {a: kwargs(a) | {"token_counter": "SFR" if "max_tokens" in
                                                                   S_ARMS[a] else None} for a in S_ARMS},
              "semantic_buffer_texts": len(recorded), "semantic_buffers_embedded_new": n_new,
              "reproduction": repro, "size_distributions": dists, "bug_criterion": bug,
              "storage": {a: sat.d_storage(cs) for a, cs in arms.items()},
              "summary": summary, "contrasts": contrasts, "verdicts": verdicts, "split_quotes": split,
              "load": {**dict(sat.EMBED_STATS), "wall_seconds": round(time.time() - t0)},
              "per_record_ranks": {n: {r: {k: v for k, v in x.items() if k != "rr_list"} for r, x in pr.items()}
                                   for n, pr in res.items()}}
    path = out / FILES["results_S"]
    path.write_text(json.dumps(report, indent=1, default=str))
    log(f"wrote {path} ({time.time() - t0:.0f}s)")
    return 0


# ----------------------------------------------------------------- part 3

P3_SEED = 261024
P3_MIN_PAGE_TOKENS = 2048
P3_M_CHARS = 1000
P3_M_GAP = 1500
P3_MAX_CALLS = 450
P3_GEN_MAX_TOKENS = 600
OPUS_URL = ""  # the Claude gateway; from the private config
OPUS_MODEL = "Claude Opus 5"
QWEN_URL = ""  # = GEN_URL, set by apply_private_config()
QWEN_MODEL = GEN_MODEL

M_USER = ""

D_RULE = ("- Ask about a specific detail (a parameter, option, value, file name or step), "
          "not about the page's main topic.\n")
_FIRST_RULE_END = "except names of tools, commands, file formats or organisms.\n"
D_USER = ""  # GEN_USER plus D_RULE after its first rule; set by apply_private_config()

JUDGE3_USER = ""


def parse_m(s: str) -> dict[str, Any] | None:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("question", "quote_a", "quote_b")):
        return None
    return d


def norm_map(text: str) -> tuple[str, list[int]]:
    """Per-character gold normalisation with a map back to raw offsets."""
    import unicodedata

    out, pos = [], []
    for i, ch in enumerate(text):
        for x in unicodedata.normalize("NFKC", ch).casefold():
            if not x.isspace():
                out.append(x)
                pos.append(i)
    return "".join(out), pos


def quote_pos(nq: str, ntext: str, pos: list[int], span: tuple[int, int]) -> int | None:
    if not nq:
        return None
    hits, i = [], ntext.find(nq)
    while i >= 0:
        hits.append(pos[i])
        i = ntext.find(nq, i + 1)
    if not hits:
        return None
    inside = [h for h in hits if span[0] <= h < span[1]]
    return (inside or hits)[0]


def draw_b(rng: random.Random, text: str, a: int, chars: int, gap: int) -> tuple[int, bool] | None:
    """Part 3's B window start for an A window at ``a``: uniform over the
    positions at least ``gap`` before or after ``a`` (room for ``chars``),
    snapped to a sentence start; the raw position when the snap breaks the gap,
    overlaps A or runs off the page. Returns (b, unsnapped), or None when there
    is no room (no random draw is made then)."""
    n = len(text)
    lo_hi = [(0, a - gap), (a + gap, n - chars)]
    segs = [(x, y) for x, y in lo_hi if y >= x]
    if not segs:
        return None
    tot = sum(y - x + 1 for x, y in segs)
    r = rng.randint(0, tot - 1)
    for x, y in segs:
        if r <= y - x:
            b_raw = x + r
            break
        r -= y - x + 1
    b = snap_sentence(text, b_raw)
    if abs(b - a) < gap or (b < a + chars and a < b + chars) or b + 1 > n:
        return b_raw, True
    return b, False


def p3_sample(pages: dict[str, dict[str, Any]], elig: list[str]) -> tuple[list[dict[str, Any]], Counter]:
    rng = random.Random(P3_SEED)
    items: list[dict[str, Any]] = []
    st: Counter[str] = Counter()
    for did in elig:
        text = pages[did]["text"]
        n = len(text)
        for k in (1, 2):
            a = snap_sentence(text, rng.randint(0, max(0, n - P3_M_CHARS)))
            drawn = draw_b(rng, text, a, P3_M_CHARS, P3_M_GAP)
            if drawn is None:
                st["M_no_room"] += 1
                continue
            b, unsnapped = drawn
            if unsnapped:
                st["M_b_unsnapped"] += 1
            items.append({"type": "M", "doc_id": did, "k": k, "a": [a, min(n, a + P3_M_CHARS)],
                          "b": [b, min(n, b + P3_M_CHARS)]})
        lo = int(0.1 * n)
        hi = max(lo, int(0.9 * n) - PASSAGE_CHARS)
        s = snap_sentence(text, rng.randint(lo, hi))
        items.append({"type": "D", "doc_id": did, "p": [s, min(n, s + PASSAGE_CHARS)]})
    return items, st


def p3_generate(out: Path, pages: dict[str, dict[str, Any]], page_tokens: dict[str, int]) -> dict[str, Any]:
    qpath = out / "questions-part3-261023.jsonl"
    gpath = out / "generation-part3-261023.json"
    if qpath.exists() and gpath.exists():
        log(f"{qpath} exists; not regenerating")
        return json.loads(gpath.read_text())
    elig_all = [d for s in STRATA for d in sorted(pages) if pages[d]["stratum"] == s
                and page_tokens[d] >= P3_MIN_PAGE_TOKENS]
    elig = elig_all
    calls_planned = 3 * len(elig)
    if calls_planned > P3_MAX_CALLS:
        n_pages = P3_MAX_CALLS // 3
        rng = random.Random(P3_SEED)
        by = {s: [d for d in elig_all if pages[d]["stratum"] == s] for s in STRATA}
        quota = {s: n_pages * len(v) / len(elig_all) for s, v in by.items()}
        base = {s: int(math.floor(q)) for s, q in quota.items()}
        for s in sorted(STRATA, key=lambda s: (-(quota[s] - base[s]), STRATA.index(s)))[:n_pages - sum(base.values())]:
            base[s] += 1
        keep = {d for s in STRATA for d in rng.sample(by[s], base[s])}
        elig = [d for d in elig_all if d in keep]
        log(f"part3: {len(elig_all)} eligible pages exceed the call cap; sampled down to {len(elig)}")
    items, st = p3_sample(pages, elig)
    log(f"part3 generation: {len(elig_all)} eligible pages (>= {P3_MIN_PAGE_TOKENS} SFR tokens), "
        f"{len(elig)} used; {len(items)} calls planned on {GEN_URL} ({GEN_MODEL}), <= {MAX_LLM_INFLIGHT} in flight; "
        f"sampling {dict(st)}")
    extra = {"chat_template_kwargs": {"enable_thinking": False}}

    def prompt(it: dict[str, Any]) -> list[dict[str, str]]:
        t = pages[it["doc_id"]]["text"]
        if it["type"] == "M":
            u = M_USER.format(a=t[it["a"][0]:it["a"][1]], b=t[it["b"][0]:it["b"][1]])
        else:
            u = D_USER.format(passage=t[it["p"][0]:it["p"][1]])
        return [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": u}]

    def parse(it: dict[str, Any], a: str) -> dict[str, Any] | None:
        return parse_m(a) if it["type"] == "M" else parse_json_answer(a)

    t0 = time.time()
    res = asyncio.run(chat_many(GEN_URL, GEN_MODEL, [prompt(i) for i in items], P3_GEN_MAX_TOKENS, extra))
    calls = len(items)
    parsed = [parse(it, a) for it, (a, _) in zip(items, res, strict=True)]
    retry = [i for i, d in enumerate(parsed) if d is None]
    if retry:
        log(f"retrying {len(retry)} unparseable answers once")
        res2 = asyncio.run(chat_many(GEN_URL, GEN_MODEL, [prompt(items[i]) for i in retry], P3_GEN_MAX_TOKENS, extra))
        calls += len(retry)
        for i, (a, info) in zip(retry, res2, strict=True):
            parsed[i] = parse(items[i], a)
            res[i] = (a, info)
    stc: dict[str, Counter] = {"M": Counter(), "D": Counter()}
    usage: Counter[str] = Counter()
    nm_cache: dict[str, tuple[str, list[int]]] = {}
    nM = nD = 0
    with qpath.open("w") as fh:
        for it, d, (_, info) in zip(items, parsed, res, strict=True):
            for k in ("prompt_tokens", "completion_tokens"):
                usage[k] += int((info.get("usage") or {}).get(k) or 0)
            page = pages[it["doc_id"]]
            text = page["text"]
            if it["type"] == "M":
                rid = f"m{nM:03d}"
                nM += 1
            else:
                rid = f"d{nD:03d}"
                nD += 1
            rec = {"record_id": rid, **it, "stratum": page["stratum"], "filename": page["filename"],
                   "page_tokens": page_tokens[it["doc_id"]], "finish": info.get("finish")}
            if d is None:
                rec["status"] = "unparseable"
            elif it["type"] == "D":
                q, quote = d["question"].strip(), d["answer_quote"]
                rec.update(question=q, answer_quote=quote)
                status = check_single(q, quote, text)
                if status != "valid":
                    rec["status"] = status
                else:
                    rec["echo"] = echo(q, text[it["p"][0]:it["p"][1]])
                    rec["status"] = "echo_high" if rec["echo"] >= ECHO_HIGH else "valid"
            else:
                q, qa, qb = d["question"].strip(), d["quote_a"], d["quote_b"]
                rec.update(question=q, quote_a=qa, quote_b=qb)

                def nm_of(did: str = it["doc_id"], text: str = text) -> tuple[str, list[int]]:
                    if did not in nm_cache:
                        nm_cache[did] = norm_map(text)
                    return nm_cache[did]

                status, gap = check_pair(q, qa, qb, text, tuple(it["a"]), tuple(it["b"]), nm_of, P3_M_GAP)
                if gap is None:
                    rec["status"] = status
                else:
                    rec["quote_gap"] = gap
                    rec["status"] = status
                    rec["echo"] = max(echo(q, text[it["a"][0]:it["a"][1]]), echo(q, text[it["b"][0]:it["b"][1]]))
            stc[it["type"]][rec["status"]] += 1
            fh.write(json.dumps(rec) + "\n")
    gen = {"eligible_pages": len(elig_all), "pages_used": len(elig), "sampling": dict(st), "calls": calls,
           "retried": len(retry), "seconds": round(time.time() - t0, 1), "usage": dict(usage),
           "status": {k: dict(v) for k, v in stc.items()},
           "planned": {"M": sum(i["type"] == "M" for i in items), "D": sum(i["type"] == "D" for i in items)},
           "eligible_by_stratum": dict(Counter(pages[d]["stratum"] for d in elig_all))}
    gpath.write_text(json.dumps(gen, indent=1))
    log(f"part3 generation: {json.dumps(gen)}")
    return gen


def build_sem_arms(out: Path, pages: dict[str, dict[str, Any]], urls: list[str]) -> dict[str, list[C]]:
    from ragstack.ingestion.chunkers import make_chunker
    from ragstack.ingestion.symbol_glossary import SymbolGlossaryAttacher
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    counter = make_token_counter("hf", model=sat.SFR_MODEL)
    want = ("SEM512", "SEM256")

    def kwargs(a: str) -> dict[str, Any]:
        return {**S_COMMON, **S_ARMS[a], "token_counter": counter}

    recorded: set[str] = set()

    def rec_fn(texts):  # noqa: ANN001, ANN202
        recorded.update(texts)
        return [[1.0] for _ in texts]

    for a in want:
        ck = make_chunker("semantic", embed_fn=rec_fn, **kwargs(a))
        for did, p in sorted(pages.items()):
            ck.chunk(Document(id=did, content=p["text"]))
    store = sat.EmbStore(out / "emb")
    miss = len(store.missing(sorted(recorded)))
    log(f"SEM buffers: {len(recorded)} texts, {miss} to embed")
    if miss:
        asyncio.run(sat.embed_missing(store, sorted(recorded), urls, sat.SFR_MODEL))

    def real_fn(texts):  # noqa: ANN001, ANN202
        return store.matrix(list(texts)).tolist()

    att = SymbolGlossaryAttacher()
    arms: dict[str, list[C]] = {a: [] for a in want}
    for a in want:
        ck = make_chunker("semantic", embed_fn=real_fn, **kwargs(a))
        for did, p in sorted(pages.items()):
            g = att.glossary_for(p["text"])
            for n, ch in enumerate(ck.chunk(Document(id=did, content=p["text"]))):
                arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "",
                                 g.for_chunk(ch.content, att.config, start_char=ch.start_char)))
    tok = counter._tokenizer()
    for cs in arms.values():
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)
    return arms


def kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if not n:
        return None
    cats = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in cats) / (n * n)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else None


OPUS_MAX_FAIL = 20
OPUS_MAX_CONSEC_FAIL = 5
OPUS_STATS: Counter = Counter()
#: Output-token stop across BOTH Opus batches (W judge + k10 grader), owner go 2026-10-23.
#: The base is the completion tokens already logged by earlier processes since the go.
OPUS_OUTPUT_CAP = 3_000_000
OPUS_OUTPUT_BASE = 0
#: Gateway judge/grader models (owner, 2026-10-23: Opus 5 may filter; use Opus 4.8 or GPT-5.6 Sol).
#: tag -> (gateway model id, file tag). Opus 5 keeps the original "opus" file names.
JUDGE_MODELS = {"opus5": ("Claude Opus 5", "opus"), "opus48": ("Claude Opus 4.8", "opus48"),
                "sol": ("GPT-5.6 Sol", "sol")}
JUDGE: dict[str, Any] = {"key": "opus5", "model": "Claude Opus 5", "tag": "opus", "temperature": True}


def set_judge(key: str) -> None:
    JUDGE.update(key=key, model=JUDGE_MODELS[key][0], tag=JUDGE_MODELS[key][1], temperature=True)


def jfile(kind: str, tag: str | None = None) -> str:
    t = tag or JUDGE["tag"]
    return {"w_cache": f"judge3_cache-{t}-261023.jsonl", "w_log": f"{t}-calls-261023.jsonl",
            "k_cache": f"k10-grades-{t}-261023.jsonl", "k_log": f"k10-{t}-calls-261023.jsonl"}[kind]


def opus_output_base(out: Path, since: float) -> int:
    tot = 0
    for name in (jfile("w_log"), jfile("k_log")):
        p = out / name
        if p.exists():
            tot += sum(int(x.get("ct") or 0) for x in read_jsonl(p) if float(x.get("t") or 0) >= since)
    return tot


def opus_body(msgs: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
    b = {"model": JUDGE["model"], "messages": msgs, "max_tokens": max_tokens, "fallback_model": ""}
    if JUDGE["temperature"]:
        b["temperature"] = 0
    return b


OPUS_ZERO_RETRIES = 2
OPUS_RETRY_BACKOFF = 2.0
OPUS_BURST: dict[str, Any] = {"on": False, "zero_times": []}


async def _opus_call(http, msgs: list[dict[str, str]], max_tokens: int):  # noqa: ANN001, ANN202
    """One request. Returns (log row without text, answer text, usage, finish)."""
    OPUS_STATS["calls"] += 1
    row: dict[str, Any] = {}
    try:
        r = await http.post(f"{OPUS_URL}/v1/chat/completions", json=opus_body(msgs, max_tokens))
        if r.status_code == 400 and JUDGE["temperature"] and "temperature" in r.text.lower():
            # The model rejects temperature: drop the field for the rest of the run (logged).
            JUDGE["temperature"] = False
            OPUS_STATS["temperature_dropped"] = 1
            log(f"{JUDGE['model']}: HTTP 400 mentioning temperature; omitting temperature from now on")
            OPUS_STATS["calls"] += 1
            r = await http.post(f"{OPUS_URL}/v1/chat/completions", json=opus_body(msgs, max_tokens))
        r.raise_for_status()
        d = r.json()
        txt = d["choices"][0]["message"].get("content") or ""
    except Exception as e:  # noqa: BLE001
        row["status"] = f"error:{type(e).__name__}"
        return row, "", {}, None
    u = d.get("usage") or {}
    pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    OPUS_STATS["prompt_tokens"] += pt
    OPUS_STATS["completion_tokens"] += ct
    OPUS_STATS[f"model:{d.get('model')}"] += 1
    fin = d["choices"][0].get("finish_reason")
    row.update(pt=pt, ct=ct, finish=fin, model=d.get("model"),
               status="ok" if txt.strip() else "error:empty_answer")
    return row, txt, u, fin


async def opus_many(prompts: list[list[dict[str, str]]], max_tokens: int, inflight: int,
                    call_log: Path) -> tuple[list[tuple[str, dict[str, Any]] | None], bool]:
    """One attempt per prompt (no retry loop). A transport/HTTP error OR an
    empty / whitespace-only answer (whatever finish_reason says) is a failure:
    never returned as a label. Stops issuing calls after more than
    OPUS_MAX_FAIL failures in total or OPUS_MAX_CONSEC_FAIL in a row. Every
    call's usage is appended to ``call_log`` (no prompt or answer text)."""
    import httpx

    sem = asyncio.Semaphore(inflight)
    out: list[tuple[str, dict[str, Any]] | None] = [None] * len(prompts)
    stop = asyncio.Event()

    def fail(kind: str) -> None:
        OPUS_STATS["errors"] += 1
        OPUS_STATS["consecutive"] += 1
        OPUS_STATS[f"error:{kind}"] += 1
        if OPUS_STATS["consecutive"] >= OPUS_MAX_CONSEC_FAIL:
            OPUS_STATS["stop_reason_5_in_a_row"] = 1
            stop.set()
        elif OPUS_STATS["errors"] > OPUS_MAX_FAIL:
            OPUS_STATS["stop_reason_over_20"] = 1
            stop.set()

    async with httpx.AsyncClient(timeout=900) as http:
        async def one(i: int, msgs: list[dict[str, str]]) -> None:
            async with sem:
                if stop.is_set():
                    return
                if OPUS_OUTPUT_BASE + OPUS_STATS["completion_tokens"] > OPUS_OUTPUT_CAP:
                    OPUS_STATS["output_cap_reached"] = 1
                    stop.set()
                    return
                OPUS_STATS["pairs"] += 1
                # Gateway-owner rule (item-specific 0-token empties): an item that
                # returns a 0-token empty answer twice in a row is "no_label (empty)" and
                # skipped; skips do not count toward the failure stops (and do not reset the
                # in-a-row counter). An empty answer with tokens > 0, or an HTTP error, is
                # a failure at once. The 5-in-a-row stop thus counts new-item failures only.
                for attempt in range(2):
                    if attempt:
                        await asyncio.sleep(OPUS_RETRY_BACKOFF)
                        if stop.is_set():
                            return
                    row, txt, u, fin = await _opus_call(http, msgs, max_tokens)
                    row.update(n=OPUS_STATS["calls"], attempt=attempt, t=round(time.time(), 2))
                    with call_log.open("a") as fh:
                        fh.write(json.dumps(row) + "\n")
                    if row["status"] == "ok":
                        OPUS_STATS["consecutive"] = 0
                        OPUS_STATS["retries_used"] += attempt
                        out[i] = (txt, {"usage": u, "finish": fin, "attempts": attempt + 1})
                        return
                    if row["status"] == "error:empty_answer" and row.get("ct") == 0:
                        if attempt == 0:
                            continue
                        OPUS_STATS["retries_used"] += 1
                        OPUS_STATS["no_label_empty"] += 1
                        out[i] = ("", {"no_label": True, "usage": {}, "finish": fin, "attempts": 2})
                        return
                    OPUS_STATS["retries_used"] += attempt
                    fail(row["status"].split(":", 1)[1])
                    return

        await asyncio.gather(*(one(i, m) for i, m in enumerate(prompts)))
    return out, stop.is_set()


def strat_sample(keys_by_stratum: dict[str, list[str]], n: int, seed: int | str = 261023) -> set[str]:
    """Largest-remainder proportional allocation, then random.Random(seed).sample per stratum
    over the sorted keys, strata in sorted order."""
    tot = sum(len(v) for v in keys_by_stratum.values())
    if tot <= n:
        return {k for v in keys_by_stratum.values() for k in v}
    st = sorted(keys_by_stratum)
    quota = {s_: n * len(keys_by_stratum[s_]) / tot for s_ in st}
    base = {s_: int(math.floor(q)) for s_, q in quota.items()}
    for s_ in sorted(st, key=lambda x: (-(quota[x] - base[x]), x))[:n - sum(base.values())]:
        base[s_] += 1
    rng = random.Random(seed)
    return {k for s_ in st for k in rng.sample(sorted(keys_by_stratum[s_]), base[s_])}


def load_labels(p: Path) -> dict[str, str]:
    return {x["k"]: x["v"] for x in read_jsonl(p)} if p.exists() else {}


def cross_judge(out: Path, keys: list[str], kind: str, qwen_path: Path, sample: set[str],
                to_neg: str) -> dict[str, Any]:
    """Cross-model comparison over one pool (W pairs or k10 grades), from the caches.
    ``to_neg`` is the class unparseable maps to (no / incorrect); no_label is excluded from kappa."""
    caches = {m: load_labels(out / jfile(kind, JUDGE_MODELS[m][1])) for m in JUDGE_MODELS}
    qw = load_labels(qwen_path)
    o5 = caches["opus5"]
    skipped5 = [k for k in keys if k not in o5 or o5[k] == "no_label"]

    def norm_l(v: str) -> str:
        return to_neg if v == "unparseable" else v

    def kap(a: dict[str, str], b: dict[str, str], ks: list[str]) -> dict[str, Any]:
        ks = [k for k in ks if k in a and k in b and a[k] != "no_label" and b[k] != "no_label"]
        la, lb = [norm_l(a[k]) for k in ks], [norm_l(b[k]) for k in ks]
        pos = "yes" if kind == "w_cache" else "correct"
        return {"n": len(ks), "kappa_multiclass": kappa(la, lb),
                f"kappa_{pos}_vs_not": kappa([x == pos for x in la], [x == pos for x in lb])}

    per_model = {}
    for m, c in caches.items():
        lab = [c[k] for k in keys if k in c]
        per_model[m] = {"labelled": len(lab), "distribution": dict(Counter(lab)),
                        "on_opus5_skipped": {"items": len(skipped5),
                                             "answered": sum(1 for k in skipped5 if k in c and c[k] != "no_label"),
                                             "no_label": sum(1 for k in skipped5 if c.get(k) == "no_label"),
                                             "not_asked_or_failed": sum(1 for k in skipped5 if k not in c),
                                             "answer_distribution": dict(Counter(c[k] for k in skipped5
                                                                                 if k in c and c[k] != "no_label"))}}
    return {"pool": len(keys), "opus5_skipped_items": len(skipped5), "sol_sample_size": len(sample),
            "per_model": per_model,
            "kappa": {"opus48_vs_qwen": kap(caches["opus48"], qw, keys),
                      "opus48_vs_opus5": kap(caches["opus48"], o5, keys),
                      "sol_vs_opus48_on_sample": kap(caches["sol"], caches["opus48"], sorted(sample)),
                      "sol_vs_opus48_on_opus5_skipped": kap(caches["sol"], caches["opus48"], skipped5),
                      "opus5_vs_qwen": kap(o5, qw, keys)},
            "note": "kappa excludes no_label; unparseable mapped to " + to_neg}


def judge_word(a: str) -> str:
    w = re.findall(r"[a-z]+", a.lower())
    return w[0] if w and w[0] in ("yes", "partial", "no") else "unparseable"


def cmd_part3(args: argparse.Namespace) -> int:
    t0 = time.time()
    out = Path(args.out_dir)
    rows = read_index()
    pages, imeta = pages_from_index(rows)
    man = write_or_check_manifest(pages, out / "manifest-261023.json")
    log(f"manifest: {json.dumps({k: v for k, v in man.items() if k != 'drift_ids'})}")
    urls = [u for u in args.embed_urls.split(",") if u]
    arms, ameta = build_arms(pages)
    arms.update(build_sem_arms(out, pages, urls))
    log(f"arms: {json.dumps({a: len(cs) for a, cs in arms.items()})}")

    # ---- A. generation
    gen = p3_generate(out, pages, ameta["page_tokens"])
    if args.generate_only:
        return 0
    qs = [r for r in read_jsonl(out / "questions-part3-261023.jsonl") if r["status"] == "valid"]
    m_ids = [r["record_id"] for r in qs if r["type"] == "M"]
    d_ids = [r["record_id"] for r in qs if r["type"] == "D"]
    records = [{"record_id": r["record_id"], "pair": r["record_id"], "kind": r["type"], "query": r["question"]}
               for r in qs]
    pairs = {r["record_id"]: {"expected": {"quote": r["quote_a"] if r["type"] == "M" else r["answer_quote"]}}
             for r in qs}
    rec_stratum = {r["record_id"]: r["stratum"] for r in qs}
    su = Setup(arms=arms, pools={POOL: set()}, records=records, gold=Gold(records, pairs),
               gold_fold=Gold(records, pairs, sat.fold), rec_paper={r["record_id"]: "" for r in records},
               jats_docs=set())
    p3_arms = ("L", "SEM512", "SEM256", *ARMS)
    specs = {a: ArmSpec(a, False, False, False) for a in p3_arms}
    ns = argparse.Namespace(embed_urls=args.embed_urls, embed_model=sat.SFR_MODEL, rerank_url=args.rerank_url,
                            out_dir=str(out))
    sat.score_config = _score_with_lists
    try:
        res = asyncio.run(run_configs(su, [(a, "rules", 0, POOL, 1.0) for a in p3_arms], ns, specs))
    finally:
        sat.score_config = _orig_score
    t_ret = time.time() - t0
    log(f"part3 retrieval done ({t_ret:.0f}s)")

    by_id = {c.id: c for cs in arms.values() for c in cs}
    gb = Gold(records, {r["record_id"]: {"expected": {"quote": r.get("quote_b", "")}} for r in qs})

    def walk(lst: list[str], bud: int) -> list[str]:
        return walk_budget(lst, bud, lambda cid: by_id[cid].tokens)

    def a_name(a: str) -> str:
        return sat.config_name(a, "rules", 0, POOL, 1.0)

    # M rescoring: both quotes within the delivered set.
    perM: dict[str, dict[str, Any]] = {}
    both_one: dict[str, dict[str, int]] = {}
    for a in p3_arms:
        per = res[a_name(a)]
        pm: dict[str, Any] = {}
        bo = Counter()
        for rid in m_ids:
            lst = per[rid]["rr_list"]
            row: dict[str, Any] = {}
            for leg, sel in (("rerank", lst[:sat.TOP_K]), ("b2048", walk(lst, 2048)), ("b5120", walk(lst, 5120))):
                ha = [bool(su.gold.check(rid, by_id[c])) for c in sel]
                hb = [bool(gb.check(rid, by_id[c])) for c in sel]
                ok = any(ha) and any(hb)
                row[leg] = (1 if ok else None) if leg == "rerank" else ok
                row[f"{leg}_a"] = any(ha)
                row[f"{leg}_b"] = any(hb)
                one = any(x and y for x, y in zip(ha, hb, strict=True))
                row[f"{leg}_both_in_one"] = one
                bo[leg] += one
            pm[rid] = row
        perM[a] = pm
        both_one[a] = dict(bo)
    perD = {a: res[a_name(a)] for a in p3_arms}

    def cnt(per: dict[str, Any], ids: list[str]) -> dict[str, int]:
        return {"n": len(ids), "b2048": sum(found(per[i], "b2048") for i in ids),
                "b5120": sum(found(per[i], "b5120") for i in ids), "r10": sum(found(per[i], "rerank") for i in ids)}

    summary = {a: {"M": cnt(perM[a], m_ids) | {"both_in_one": both_one[a],
                                               "a_only_b5120": sum(perM[a][i]["b5120_a"] for i in m_ids),
                                               "b_only_b5120": sum(perM[a][i]["b5120_b"] for i in m_ids)},
                   "D": cnt(perD[a], d_ids)} for a in p3_arms}

    def rule3(c: dict[str, Any], leg: str) -> dict[str, Any]:
        r = rule(c, leg)
        disc = c[leg]["gained"] + c[leg]["lost"]
        r["discordant"] = disc
        r["unresolved"] = disc < 10
        return r

    cpairs: list[tuple[str, str, str]] = []
    for f in FAMILIES:
        for s in SIZES:
            if s != 512:
                for ov0 in (0, 1):
                    cpairs.append(("1 size", arm_name(f, s, s // 8 if ov0 else 0), arm_name(f, 512, 64 if ov0 else 0)))
    for a in (*ARMS, "SEM512", "SEM256"):
        cpairs.append(("2 vs live" if a in ARMS else "3 cap", a, "L"))
    for f in FAMILIES:
        for s in SIZES:
            cpairs.append(("4 overlap", arm_name(f, s, s // 8), arm_name(f, s, 0)))
    contrasts: dict[str, dict[str, Any]] = defaultdict(dict)
    verdicts: dict[str, Any] = {}
    for fam, x, y in cpairs:
        for typ, per, ids, leg in (("M", perM, m_ids, "b5120"), ("D", perD, d_ids, "b2048")):
            c = strat_contrast(per[x], per[y], su, ids, rec_stratum)
            contrasts[fam][f"{typ}|{x} - {y}"] = c
            verdicts[f"{fam}|{typ}|{x} - {y}"] = rule3(c, leg)

    # ---- B. W judges
    w_records = [{"record_id": rid, "pair": rid, "kind": "W", "query": q} for rid, q in SEED_W.items()]
    w_pairs = {rid: {"expected": {"quote": ""}} for rid in SEED_W}
    su_w = Setup(arms=arms, pools={POOL: set()}, records=w_records, gold=Gold(w_records, w_pairs),
                 gold_fold=Gold(w_records, w_pairs, sat.fold), rec_paper=dict.fromkeys(SEED_W, ""),
                 jats_docs=set())
    w_specs = {"L": ArmSpec("L", False, False, False), **{a: ArmSpec(a, False, False, False) for a in ARMS}}
    t_arms = [a for a in ARMS if a.startswith("T")]
    for a in t_arms:
        for m in DENSE:
            w_specs[f"{a}~{m}"] = ArmSpec(a, False, False, False, dense=m)
    w_configs = [(a, g, 0, POOL, 1.0) for a in ("L", *ARMS) for g in ("rules", "off")]
    w_configs += [(f"{a}~{m}", "rules", 0, POOL, 1.0) for a in t_arms for m in DENSE]
    rr0 = sat.EMBED_STATS.get("rerank_calls", 0)
    sat.score_config = _score_with_lists
    try:
        wres = asyncio.run(run_configs(su_w, w_configs, ns, w_specs))
    finally:
        sat.score_config = _orig_score
    pool: dict[str, set[str]] = defaultdict(set)
    for per in wres.values():
        for rid in SEED_W:
            lst = per[rid]["rr_list"]
            for cid in set(walk(lst, 5120)) | set(lst[:sat.TOP_K]):
                pool[rid].add(by_id[cid].content)
    pairs_w = [(rid, t) for rid in sorted(pool) for t in sorted(pool[rid])]
    scout = {x["k"]: x["v"] for x in read_jsonl(out / "judge_cache-261023.jsonl")}
    recon = {"pool_pairs": len(pairs_w), "expected": 3731,
             "pairs_in_scout_cache": sum(sha(r + "\x00" + t) in scout for r, t in pairs_w),
             "new_rerank_calls": sat.EMBED_STATS.get("rerank_calls", 0) - rr0}
    log(f"W pool reconstruction: {json.dumps(recon)}")
    if recon["pool_pairs"] != 3731 or recon["pairs_in_scout_cache"] != 3731:
        log("STOP: W pool reconstruction does not match the part-1 pool")
        return 3

    def run_judge(name: str, url: str, model: str, extra: dict[str, Any] | None, inflight: int) -> dict[str, Any]:
        path = out / f"judge3_cache-{name}-261023.jsonl"
        jc: dict[str, dict[str, Any]] = {}
        if path.exists():
            for x in read_jsonl(path):
                jc[x["k"]] = x
        todo = [(r, t) for r, t in pairs_w if sha(r + "\x00" + t) not in jc]
        if name == JUDGE["tag"] and args.judge_subset == "sol":
            todo = [(r, t) for r, t in todo if sha(r + "\x00" + t) in w_subset]
            log(f"judge {name}: subset sol = {len(w_skip5)} opus5-skipped + {len(w_sample)} sampled; {len(todo)} to ask")
        if name == JUDGE["tag"] and args.opus_cache_only:
            log(f"judge opus: cache only, {len(todo)} uncached pairs NOT asked (recorded as failed)")
            todo = []
        log(f"judge {name}: {len(pairs_w)} pairs, {len(todo)} new calls on {url} ({model}), <= {inflight} in flight")
        global MAX_LLM_INFLIGHT
        old = MAX_LLM_INFLIGHT
        MAX_LLM_INFLIGHT = inflight
        t1 = time.time()
        try:
            B = 200
            for i in range(0, len(todo), B):
                part = todo[i:i + B]
                msgs = [[{"role": "user", "content": JUDGE3_USER.format(question=SEED_W[r], chunk=t)}]
                        for r, t in part]
                stopped = False
                if name == JUDGE["tag"]:
                    ans, stopped = asyncio.run(opus_many(msgs, args.opus_max_tokens, inflight,
                                                         out / jfile("w_log")))
                else:
                    ans = asyncio.run(chat_many(url, model, msgs, 8, extra))
                with path.open("a") as fh:
                    for (r, t), got in zip(part, ans, strict=True):
                        if got is None:  # failed Opus call: left unjudged, counted in OPUS_STATS
                            continue
                        a, info = got
                        u = info.get("usage") or {}
                        x = {"k": sha(r + "\x00" + t), "v": "no_label" if info.get("no_label") else judge_word(a),
                             "raw": "<0-token empty x2>" if info.get("no_label") else a[:40],
                             "pt": int(u.get("prompt_tokens") or 0), "ct": int(u.get("completion_tokens") or 0)}
                        jc[x["k"]] = x
                        fh.write(json.dumps(x) + "\n")
                log(f"judge {name}: {min(i + B, len(todo))}/{len(todo)}"
                    + (f"; {name} {json.dumps(dict(OPUS_STATS))}" if name == JUDGE["tag"] else ""))
                if stopped:
                    log(f"STOP opus: error cap reached; {json.dumps(dict(OPUS_STATS))}")
                    raise SystemExit(4)
        finally:
            MAX_LLM_INFLIGHT = old
        missing = [(r, t) for r, t in pairs_w if sha(r + "\x00" + t) not in jc]
        for r, t in missing:  # failed calls count as "no" (prereg's unparseable rule) and are listed
            jc[sha(r + "\x00" + t)] = {"k": sha(r + "\x00" + t), "v": "unparseable", "raw": "<call failed>",
                                       "pt": 0, "ct": 0}
        labels = [jc[sha(r + "\x00" + t)]["v"] for r, t in pairs_w]
        dist = Counter(labels)
        yes_share = dist["yes"] / len(labels)
        n_ex = len(labels) - dist["no_label"]
        yes_share_ex = dist["yes"] / n_ex if n_ex else 0.0
        unp = sorted({jc[sha(r + "\x00" + t)]["raw"] for r, t in pairs_w
                      if jc[sha(r + "\x00" + t)]["v"] == "unparseable"})
        per_cfg = {}
        for cfg, per in wres.items():
            row = {}
            for leg in ("r10", "b2048", "b5120"):
                n = 0
                for rid in SEED_W:
                    lst = per[rid]["rr_list"]
                    sel = lst[:sat.TOP_K] if leg == "r10" else walk(lst, int(leg[1:]))
                    vs = [jc[sha(rid + "\x00" + by_id[c].content)]["v"] for c in sel]
                    n += "yes" in vs
                row[leg] = n
            per_cfg[cfg] = row
        return {"model": model, "url": url, "calls_this_run": len(todo), "seconds": round(time.time() - t1),
                "distribution": dict(dist), "yes_share": round(yes_share, 4),
                "degenerate": yes_share < 0.02 or yes_share > 0.90,
                "no_label_empty": dist["no_label"],
                "yes_share_no_label_excluded": round(yes_share_ex, 4),
                "degenerate_no_label_excluded": yes_share_ex < 0.02 or yes_share_ex > 0.90,
                "W_yes_hits_note": "a no_label chunk is never 'yes', so yes-hits are identical under both readings",
                "unparseable_raw": unp[:20],
                "usage": {"prompt_tokens": sum(jc[sha(r + "\x00" + t)]["pt"] for r, t in pairs_w),
                          "completion_tokens": sum(jc[sha(r + "\x00" + t)]["ct"] for r, t in pairs_w)},
                "labels": labels, "W_yes_hits": per_cfg}

    w_keys = [sha(r + "\x00" + t) for r, t in pairs_w]
    o5w = load_labels(out / jfile("w_cache", "opus"))
    w_skip5 = [k for k in w_keys if k not in o5w or o5w[k] == "no_label"]
    rid_of = {sha(r + "\x00" + t): r for r, t in pairs_w}
    w_sample = strat_sample({rid: [k for k in w_keys if rid_of[k] == rid and k not in set(w_skip5)]
                             for rid in SEED_W}, 400)
    w_subset = set(w_skip5) | w_sample
    if args.probe_keys:
        import httpx

        txt_of = {sha(r + "\x00" + t): (r, t) for r, t in pairs_w}
        for pre in [x for x in args.probe_keys.split(",") if x]:
            hits = [k for k in txt_of if k.startswith(pre)]
            if len(hits) != 1:
                log(f"probe {pre}: {len(hits)} matches in the W pool; skipped")
                continue
            r, t = txt_of[hits[0]]

            async def go(msgs):  # noqa: ANN001, ANN202
                async with httpx.AsyncClient(timeout=900) as http:
                    return await _opus_call(http, msgs, args.opus_max_tokens)

            row, a, _, fin = asyncio.run(go([{"role": "user", "content": JUDGE3_USER.format(
                question=SEED_W[r], chunk=t)}]))
            row.update(probe=pre, model_req=JUDGE["model"], temperature=JUDGE["temperature"], t=round(time.time(), 2),
                       label=judge_word(a) if a.strip() else None)
            with (out / jfile("w_log")).open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            log(f"PROBE W {pre} ({r}) {JUDGE['model']}: {json.dumps(row)}")
        return 0
    qwen = run_judge("qwen", QWEN_URL, QWEN_MODEL, {"chat_template_kwargs": {"enable_thinking": False}}, 4)
    if args.opus_dry_run:
        cache = out / jfile("w_cache")
        done = {x["k"] for x in read_jsonl(cache)} if cache.exists() else set()
        todo = [(r, t) for r, t in pairs_w if sha(r + "\x00" + t) not in done]
        if args.judge_subset == "sol":
            todo = [(r, t) for r, t in todo if sha(r + "\x00" + t) in w_subset]
        bodies = [opus_body([{"role": "user", "content": JUDGE3_USER.format(question=SEED_W[r], chunk=t)}],
                            args.opus_max_tokens) for r, t in todo]
        chars = [len(b["messages"][0]["content"]) for b in bodies]
        from ragstack.ingestion.tokenization import make_token_counter

        tok = make_token_counter("hf", model=sat.SFR_MODEL)._tokenizer()
        sfr = sum(len(x) for x in tok([b["messages"][0]["content"] for b in bodies],
                                      add_special_tokens=False)["input_ids"]) if bodies else 0
        dry = {"would_send": len(bodies), "pairs": len(pairs_w), "already_cached": len(pairs_w) - len(todo),
               "batches_of_200": math.ceil(len(bodies) / 200), "url": f"{OPUS_URL}/v1/chat/completions",
               "body_keys": sorted(bodies[0]) if bodies else [],
               "model": sorted({b["model"] for b in bodies}), "max_tokens": sorted({b["max_tokens"] for b in bodies}),
               "temperature": sorted({b["temperature"] for b in bodies}),
               "fallback_model": sorted({b["fallback_model"] for b in bodies}),
               "inflight": args.opus_inflight, "prompt_chars_total": sum(chars),
               "prompt_chars_median": statistics.median(chars) if chars else 0, "prompt_chars_max": max(chars, default=0),
               "prompt_tokens_SFR_total": sfr,
               "max_completion_tokens_total": len(bodies) * args.opus_max_tokens,
               "first_batch_actual_prompt_tokens_per_call": round(98003 / 200, 1)}
        if args.opus_test_one:
            msgs = [[{"role": "user", "content": JUDGE3_USER.format(question=SEED_W[r], chunk=t)}]
                    for r, t in todo[:1]]
            ans, _ = asyncio.run(opus_many(msgs, args.opus_max_tokens, 1, out / jfile("w_log")))
            got = ans[0]
            test = {"calls": OPUS_STATS["calls"], "errors": OPUS_STATS["errors"],
                    "prompt_tokens": OPUS_STATS["prompt_tokens"], "completion_tokens": OPUS_STATS["completion_tokens"],
                    "non_empty": got is not None, "label": judge_word(got[0]) if got else None,
                    "finish": got[1]["finish"] if got else None}
            (out / "opus-testone-261023.json").write_text(json.dumps(test, indent=1))
            log(f"OPUS TEST CALL (1 sent, not cached): {json.dumps(test)}")
            return 0 if got is not None and test["label"] != "unparseable" else 5
        (out / "opus-dryrun-261023.json").write_text(json.dumps(dry, indent=1))
        log(f"OPUS DRY RUN (no calls sent): {json.dumps(dry)}")
        return 0
    if args.opus:
        opus = run_judge(JUDGE["tag"], OPUS_URL, JUDGE["model"], {"fallback_model": ""}, args.opus_inflight)
    else:
        opus = {"status": "pending", "reason": "gateway hold: no gateway calls until the gateway owner confirms"}
    judges: dict[str, Any] = {"qwen": {k: v for k, v in qwen.items() if k != "labels"},
                              "opus": {k: v for k, v in opus.items() if k != "labels"},
                              "scout_record_only": dict(Counter(scout[sha(r + "\x00" + t)] for r, t in pairs_w))}
    if args.opus:
        judges["opus_audit"] = {k: v for k, v in OPUS_STATS.items() if k != "consecutive"} | {
            "limits": {"max_tokens": args.opus_max_tokens, "temperature": 0 if JUDGE["temperature"] else "omitted",
                       "inflight": args.opus_inflight, "model": JUDGE["model"],
                       "fallback_model": "", "base_url": OPUS_URL + "/v1", "max_fail": OPUS_MAX_FAIL,
                       "max_consecutive_fail": OPUS_MAX_CONSEC_FAIL,
                       "retries": "0-token empty: 1 re-ask, then no_label (skipped, not a failure)"}}
    if "labels" in opus:
        a0, b0 = opus["labels"], qwen["labels"]

        def agree(a: list[str], b: list[str]) -> dict[str, Any]:
            return {"n": len(a), "kappa_3class": kappa(a, b),
                    "kappa_yes_vs_not": kappa(["y" if x == "yes" else "n" for x in a],
                                              ["y" if x == "yes" else "n" for x in b])}

        as_no = [("no" if x in ("no_label", "unparseable") else x) for x in a0]
        b_no = [("no" if x == "unparseable" else x) for x in b0]
        keep = [i for i, x in enumerate(a0) if x != "no_label"]
        judges["agreement"] = {
            "no_label_as_no": agree(as_no, b_no),
            "no_label_excluded": agree([as_no[i] for i in keep], [b_no[i] for i in keep]),
            "note": "unparseable counted as 'no' (prereg); no_label = 0-token empty answer twice"}
        # Record of skipped (no_label) items: ids only, no text.
        skipped = []
        for (rid, t), v in zip(pairs_w, a0, strict=True):
            if v != "no_label":
                continue
            cids = [c.id for cs in arms.values() for c in cs if c.content == t]
            docs = sorted({by_id[c].doc_id for c in cids})
            in_cfgs = sorted(cfg for cfg, per in wres.items()
                             if any(by_id[c].content == t for c in
                                    set(walk(per[rid]["rr_list"], 5120)) | set(per[rid]["rr_list"][:sat.TOP_K])))
            skipped.append({"item": sha(rid + "\x00" + t)[:16], "set": "W", "question_id": rid,
                            "arms": sorted({cfg.split("|")[0] for cfg in in_cfgs}), "configs": in_cfgs,
                            "chunk_ids": sorted(cids), "doc_ids": docs,
                            "strata": sorted({pages[d]["stratum"] for d in docs})})
        judges["opus_no_label_items"] = skipped
        judges["opus_no_label_by_stratum"] = dict(Counter(s for x in skipped for s in x["strata"]))
        judges["opus_no_label_by_doc"] = dict(Counter(d for x in skipped for d in x["doc_ids"]))
        judges["opus_no_label_by_question"] = dict(Counter(x["question_id"] for x in skipped))
        # Rates per W question (pairs in its pool): no_label, and failures (uncached after the
        # runs = HTTP error / empty with tokens > 0, recorded as unparseable "<call failed>").
        ocache = {x["k"]: x for x in read_jsonl(out / jfile("w_cache"))}
        rates = {}
        for rid in SEED_W:
            ks = [sha(rid + "\x00" + t) for r, t in pairs_w if r == rid]
            nl = sum(ocache.get(k, {}).get("v") == "no_label" for k in ks)
            fl = sum(k not in ocache for k in ks)
            rates[rid] = {"pairs": len(ks), "no_label": nl, "failed": fl,
                          "no_label_rate": round(nl / len(ks), 4), "failed_rate": round(fl / len(ks), 4),
                          "not_evaluable": nl / len(ks) > 0.5}
        judges["opus_rates_by_question"] = rates
        ne = sorted(r for r, x in rates.items() if x["not_evaluable"])
        judges["opus_not_evaluable_questions"] = ne
        ev = [r for r in SEED_W if r not in ne]
        jo = dict(ocache)
        per_ev = {}
        for cfg, per in wres.items():
            row = {"questions_scored": len(ev)}
            for leg in ("r10", "b2048", "b5120"):
                n = 0
                for rid in ev:
                    lst = per[rid]["rr_list"]
                    sel = lst[:sat.TOP_K] if leg == "r10" else walk(lst, int(leg[1:]))
                    n += any(jo.get(sha(rid + "\x00" + by_id[c].content), {}).get("v") == "yes" for c in sel)
                row[leg] = n
            row["not_evaluable"] = ne
            per_ev[cfg] = row
        judges["opus_W_yes_hits_evaluable_only"] = per_ev

    from ragstack.provenance import experiment_provenance

    report = {"prereg": FILES["prereg_part3"],
              "prereg_sha256": sha((out / FILES["prereg_part3"]).read_text()),
              "provenance": experiment_provenance(repo=str(sat._REPO_PY.parent)),
              "index": INDEX, "manifest": man, "arms_chunks": {a: len(arms[a]) for a in p3_arms},
              "generation": gen, "n": {"M": len(m_ids), "D": len(d_ids)},
              "by_stratum": {"M": dict(Counter(rec_stratum[i] for i in m_ids)),
                             "D": dict(Counter(rec_stratum[i] for i in d_ids))},
              "summary": summary, "contrasts": contrasts, "verdicts": verdicts,
              "W_pool_reconstruction": recon, "judges": judges,
              "judge_model": JUDGE["model"], "judge_key": JUDGE["key"],
              "judge_subset": args.judge_subset,
              "cross_judge": cross_judge(out, w_keys, "w_cache", out / "judge3_cache-qwen-261023.jsonl",
                                         w_sample, "no"),
              "load": {**dict(sat.EMBED_STATS), "retrieval_seconds": round(t_ret),
                       "wall_seconds": round(time.time() - t0)},
              "per_record": {"M": perM, "D": {a: {r: {k: v for k, v in x.items() if k != "rr_list"}
                                                  for r, x in per.items() if r in set(d_ids)}
                                              for a, per in perD.items()}}}
    path = out / (FILES["results_part3"] if JUDGE["key"] == args.primary_judge
                  else alt_results(FILES["results_part3"], JUDGE["key"]))
    path.write_text(json.dumps(report, indent=1, default=str))
    log(f"wrote {path} ({time.time() - t0:.0f}s)")
    return 0


# --------------------------------------------------------------------- k10

K10_ARMS = ("L", "SEM512", "SEM256", "T128o0", "T256o0", "T512o0", "T1024o0", "X512o0")
K10_READERS: dict[str, tuple[str, str, int, dict[str, Any] | None]] = {}  # set by apply_private_config()
K10_SETS = ("M", "D", "P2")
K10_GRADES = ("correct", "partial", "incorrect", "abstained")
READER_SYSTEM = ""
GRADER_USER = ""


def grade_word(a: str) -> str:
    w = re.findall(r"[a-z]+", a.lower())
    return w[0] if w and w[0] in K10_GRADES else "unparseable"


async def chat_many_safe(url: str, model: str, prompts: list[list[dict[str, str]]], max_tokens: int,
                         extra: dict[str, Any] | None, inflight: int) -> list[tuple[str, dict[str, Any]]]:
    """Like chat_many (3 retries with backoff) but a call that still fails is
    returned as ("", {"error": ...}) instead of aborting the batch."""
    import httpx

    check_url(url)
    sem = asyncio.Semaphore(inflight)
    out: list[tuple[str, dict[str, Any]]] = [("", {})] * len(prompts)
    async with httpx.AsyncClient(timeout=900) as http:
        async def one(i: int, msgs: list[dict[str, str]]) -> None:
            body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens, **(extra or {})}
            async with sem:
                err = None
                for attempt in range(4):
                    try:
                        r = await http.post(f"{url}/v1/chat/completions", json=body)
                        r.raise_for_status()
                        d = r.json()
                        out[i] = (d["choices"][0]["message"].get("content") or "",
                                  {"usage": d.get("usage") or {}, "finish": d["choices"][0].get("finish_reason")})
                        return
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}:{str(e)[:120]}"
                        if attempt < 3:
                            await asyncio.sleep(3 * (attempt + 1))
                out[i] = ("", {"error": err})

        await asyncio.gather(*(one(i, m) for i, m in enumerate(prompts)))
    return out


def cmd_k10(args: argparse.Namespace) -> int:
    t0 = time.time()
    out = Path(args.out_dir)
    rows = read_index()
    pages, _ = pages_from_index(rows)
    man = write_or_check_manifest(pages, out / "manifest-261023.json")
    log(f"manifest: {json.dumps({k: v for k, v in man.items() if k != 'drift_ids'})}")
    urls = [u for u in args.embed_urls.split(",") if u]
    arms, _ = build_arms(pages)
    arms.update(build_sem_arms(out, pages, urls))
    arms = {a: arms[a] for a in K10_ARMS}
    by_id = {c.id: c for cs in arms.values() for c in cs}

    # ---- questions (validated; W excluded)
    q2 = [r for r in read_jsonl(out / "questions-261023.jsonl") if r["status"] == "valid"]
    q3 = [r for r in read_jsonl(out / "questions-part3-261023.jsonl") if r["status"] == "valid"]
    recs: dict[str, dict[str, Any]] = {}
    for r in q2:
        recs[r["record_id"]] = {"set": "P2", "q": r["question"], "gold": [r["answer_quote"]], "stratum": r["stratum"],
                                "doc_id": r["doc_id"]}
    for r in q3:
        gold = [r["quote_a"], r["quote_b"]] if r["type"] == "M" else [r["answer_quote"]]
        recs[r["record_id"]] = {"set": r["type"], "q": r["question"], "gold": gold, "stratum": r["stratum"],
                                "doc_id": r["doc_id"]}
    ids = {s: [i for i, x in recs.items() if x["set"] == s] for s in K10_SETS}
    log(f"k10 questions: {json.dumps({s: len(v) for s, v in ids.items()})}")

    # ---- top-10 lists, recomputed deterministically from the caches and verified
    records = [{"record_id": i, "pair": i, "kind": x["set"], "query": x["q"]} for i, x in recs.items()]
    pairs = {i: {"expected": {"quote": x["gold"][0]}} for i, x in recs.items()}
    su = Setup(arms=arms, pools={POOL: set()}, records=records, gold=Gold(records, pairs),
               gold_fold=Gold(records, pairs, sat.fold), rec_paper=dict.fromkeys(recs, ""), jats_docs=set())
    ns = argparse.Namespace(embed_urls=args.embed_urls, embed_model=sat.SFR_MODEL, rerank_url=args.rerank_url,
                            out_dir=str(out))
    rr0 = sat.EMBED_STATS.get("rerank_calls", 0)
    e0 = sat.EMBED_STATS.get("texts_embedded", 0)
    sat.score_config = _score_with_lists
    try:
        res = asyncio.run(run_configs(su, [(a, "rules", 0, POOL, 1.0) for a in K10_ARMS], ns,
                                      {a: ArmSpec(a, False, False, False) for a in K10_ARMS}))
    finally:
        sat.score_config = _orig_score
    cname = {a: sat.config_name(a, "rules", 0, POOL, 1.0) for a in K10_ARMS}
    top10 = {a: {i: res[cname[a]][i]["rr_list"][:sat.TOP_K] for i in recs} for a in K10_ARMS}
    ref2 = json.loads((out / FILES["results_main"]).read_text())["per_record_ranks"]
    refS = json.loads((out / FILES["results_S"]).read_text())["per_record_ranks"]
    ref3 = json.loads((out / FILES["results_part3"]).read_text())["per_record"]["D"]
    chk = Counter()
    for a in K10_ARMS:
        for i, x in recs.items():
            ref = None
            if x["set"] == "P2":
                src = refS if a.startswith("SEM") else ref2
                ref = (src.get(cname[a]) or {}).get(i, {}).get("top10")
            elif x["set"] == "D":
                ref = (ref3.get(a) or {}).get(i, {}).get("top10")
            if ref is None:
                chk["no_stored_list"] += 1
            elif ref == top10[a][i]:
                chk["identical"] += 1
            else:
                chk["different"] += 1
    lists_check = {**dict(chk), "new_rerank_calls": sat.EMBED_STATS.get("rerank_calls", 0) - rr0,
                   "new_texts_embedded": sat.EMBED_STATS.get("texts_embedded", 0) - e0}
    log(f"top-10 lists check: {json.dumps(lists_check)}")
    if chk["different"] or lists_check["new_rerank_calls"]:
        log("STOP: recomputed top-10 lists differ from the stored ones (or needed new reranker calls)")
        return 3

    # ---- gold presence in the top 10 and context tokens
    golds = {i: [norm(g) for g in x["gold"]] for i, x in recs.items()}

    def present(i: str, cids: list[str]) -> bool:
        txt = [norm(by_id[c].content) for c in cids]
        return all(any(sat.hit(g, t) for t in txt) for g in golds[i])

    found10 = {a: {i: present(i, top10[a][i]) for i in recs} for a in K10_ARMS}
    ctx = {a: {i: sum(by_id[c].tokens for c in top10[a][i]) for i in recs} for a in K10_ARMS}

    # ---- readers
    from ragstack.ingestion.tokenization import make_token_counter

    tok = make_token_counter("hf", model=sat.SFR_MODEL)._tokenizer()

    def ntok(s: str) -> int:
        return len(tok([s], add_special_tokens=False)["input_ids"][0])

    def reader_prompt(i: str, cids: list[str], limit: int) -> tuple[list[dict[str, str]], int]:
        msgs, dropped, _ = reader_messages(recs[i]["q"], [passage(by_id[c], "rules", False) for c in cids],
                                           limit, ntok, READER_SYSTEM)
        return msgs, dropped

    ans_path = out / "k10-answers-261023.jsonl"
    answers: dict[str, dict[str, Any]] = {}
    if ans_path.exists():
        for x in read_jsonl(ans_path):
            if x.get("ok"):
                answers[x["k"]] = x
    drops = Counter()
    reader_stats: dict[str, Any] = {}
    for rd, (url, model, window, extra) in K10_READERS.items():
        jobs = []
        for a in K10_ARMS:
            for i in recs:
                k = f"{rd}|{a}|{i}"
                if k in answers:
                    continue
                msgs, dropped = reader_prompt(i, top10[a][i], window)
                if dropped:
                    drops[f"{rd}|{a}"] += dropped
                jobs.append((k, msgs))
        log(f"k10 reader {rd}: {len(jobs)} calls on {url} ({model}), <= {MAX_LLM_INFLIGHT} in flight; "
            f"chunks dropped for the window so far {dict(drops)}")
        t1 = time.time()
        B = 200
        errs = 0
        for s in range(0, len(jobs), B):
            part = jobs[s:s + B]
            got = asyncio.run(chat_many_safe(url, model, [m for _, m in part], 400, extra, MAX_LLM_INFLIGHT))
            with ans_path.open("a") as fh:
                for (k, _), (a_txt, info) in zip(part, got, strict=True):
                    u = info.get("usage") or {}
                    x = {"k": k, "ok": "error" not in info, "answer": a_txt, "finish": info.get("finish"),
                         "error": info.get("error"), "pt": int(u.get("prompt_tokens") or 0),
                         "ct": int(u.get("completion_tokens") or 0)}
                    errs += not x["ok"]
                    if x["ok"]:
                        answers[k] = x
                    fh.write(json.dumps(x) + "\n")
            log(f"k10 reader {rd}: {min(s + B, len(jobs))}/{len(jobs)}; errors {errs}")
        mine = [x for k, x in answers.items() if k.startswith(rd + "|")]
        reader_stats[rd] = {"calls_this_run": len(jobs), "errors_this_run": errs, "seconds": round(time.time() - t1),
                            "answers": len(mine), "prompt_tokens": sum(x["pt"] for x in mine),
                            "completion_tokens": sum(x["ct"] for x in mine),
                            "finish_length": sum(x.get("finish") == "length" for x in mine),
                            "empty_answers": sum(not x["answer"].strip() for x in mine)}
    expected = len(K10_READERS) * len(K10_ARMS) * len(recs)
    if len(answers) != expected:
        log(f"STOP: {expected - len(answers)} reader answers missing after retries; rerun resumes from the cache")
        return 6

    # ---- graders: dedup by (question id, normalised answer)
    def gkey(i: str, a_txt: str) -> str:
        return sha(i + "\x00" + norm(a_txt))

    def grader_msgs(i: str, a_txt: str) -> list[dict[str, str]]:
        return grader_messages(recs[i]["q"], recs[i]["gold"], a_txt)

    uniq: dict[str, tuple[str, str]] = {}
    for k, x in answers.items():
        i = k.split("|", 2)[2]
        uniq.setdefault(gkey(i, x["answer"]), (i, x["answer"]))
    log(f"k10 grading: {len(answers)} answers, {len(uniq)} distinct (question, normalised answer)")
    qg_path = out / "k10-grades-qwen-261023.jsonl"
    qg = load_cache(qg_path)
    todo = [(gk, i, a_txt) for gk, (i, a_txt) in uniq.items() if gk not in qg]
    log(f"k10 grader qwen: {len(todo)} calls on {GEN_URL}, <= {MAX_LLM_INFLIGHT} in flight")
    t1 = time.time()
    for s in range(0, len(todo), 200):
        part = todo[s:s + 200]
        got = asyncio.run(chat_many_safe(GEN_URL, GEN_MODEL, [grader_msgs(i, a) for _, i, a in part], 16,
                                         {"chat_template_kwargs": {"enable_thinking": False}}, MAX_LLM_INFLIGHT))
        with qg_path.open("a") as fh:
            for (gk, _, _), (g_txt, info) in zip(part, got, strict=True):
                if "error" in info:
                    continue
                u = info.get("usage") or {}
                x = {"k": gk, "v": grade_word(g_txt), "raw": g_txt[:40], "pt": int(u.get("prompt_tokens") or 0),
                     "ct": int(u.get("completion_tokens") or 0)}
                qg[gk] = x
                fh.write(json.dumps(x) + "\n")
        log(f"k10 grader qwen: {min(s + 200, len(todo))}/{len(todo)}")
    qwen_secs = round(time.time() - t1)
    if any(gk not in qg for gk in uniq):
        log(f"STOP: {sum(gk not in qg for gk in uniq)} qwen grades missing after retries")
        return 6

    # ---- Opus grader (dry run / test / run)
    o5k = load_labels(out / jfile("k_cache", "opus"))
    k_keys = sorted(uniq)
    k_skip5 = [gk for gk in k_keys if gk not in o5k or o5k[gk] == "no_label"]
    k_strat = defaultdict(list)
    for gk in k_keys:
        if gk not in set(k_skip5):
            i = uniq[gk][0]
            k_strat[f"{recs[i]['set']}|{recs[i]['stratum']}"].append(gk)
    k_sample = strat_sample(dict(k_strat), 400)
    k_subset = set(k_skip5) | k_sample
    if args.probe_keys:
        import httpx

        for pre in [x for x in args.probe_keys.split(",") if x]:
            hits = [gk for gk in uniq if gk.startswith(pre)]
            if len(hits) != 1:
                log(f"probe {pre}: {len(hits)} matches in the k10 pool; skipped")
                continue
            i, a_txt = uniq[hits[0]]

            async def go(msgs):  # noqa: ANN001, ANN202
                async with httpx.AsyncClient(timeout=900) as http:
                    return await _opus_call(http, msgs, args.opus_max_tokens)

            row, g_txt, _, fin = asyncio.run(go(grader_msgs(i, a_txt)))
            row.update(probe=pre, model_req=JUDGE["model"], temperature=JUDGE["temperature"], t=round(time.time(), 2),
                       label=grade_word(g_txt) if g_txt.strip() else None)
            with (out / jfile("k_log")).open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            log(f"PROBE k10 {pre} ({i}) {JUDGE['model']}: {json.dumps(row)}")
        return 0
    og_path = out / jfile("k_cache")
    og = load_cache(og_path)
    otodo = [(gk, i, a_txt) for gk, (i, a_txt) in uniq.items() if gk not in og]
    if args.judge_subset == "sol":
        otodo = [x for x in otodo if x[0] in k_subset]
        log(f"k10 {JUDGE['key']}: subset sol = {len(k_skip5)} opus5-skipped + {len(k_sample)} sampled; {len(otodo)} to ask")
    if args.opus_cache_only and args.opus:
        log(f"k10 opus: cache only, {len(otodo)} uncached grades NOT asked")
        otodo_skipped = len(otodo)
        otodo = []
    else:
        otodo_skipped = 0
    bodies = [opus_body(grader_msgs(i, a), args.opus_max_tokens) for _, i, a in otodo]
    texts = [b["messages"][0]["content"] for b in bodies]
    sfr_in = sum(len(x) for x in tok(texts, add_special_tokens=False)["input_ids"]) if texts else 0
    dry = {"would_send": len(bodies), "distinct": len(uniq), "already_cached": len(uniq) - len(otodo),
           "url": f"{OPUS_URL}/v1/chat/completions", "model": JUDGE["model"], "max_tokens": args.opus_max_tokens,
           "temperature": 0, "fallback_model": "", "inflight": args.opus_inflight,
           "prompt_chars_total": sum(len(t) for t in texts), "est_input_tokens_chars_div4": sum(len(t) for t in texts) // 4,
           "est_input_tokens_SFR": sfr_in, "max_output_tokens_total": len(bodies) * args.opus_max_tokens}
    (out / "k10-opus-dryrun-261023.json").write_text(json.dumps(dry, indent=1))
    log(f"K10 OPUS DRY RUN (no calls sent): {json.dumps(dry)}")
    opus_audit: dict[str, Any] = {"status": "pending"}
    if args.opus_test_one and otodo:
        gk, i, a_txt = otodo[0]
        got, _ = asyncio.run(opus_many([grader_msgs(i, a_txt)], args.opus_max_tokens, 1,
                                       out / jfile("k_log")))
        test = {"calls": OPUS_STATS["calls"], "errors": OPUS_STATS["errors"], "prompt_tokens": OPUS_STATS["prompt_tokens"],
                "completion_tokens": OPUS_STATS["completion_tokens"], "non_empty": got[0] is not None,
                "label": grade_word(got[0][0]) if got[0] else None}
        log(f"K10 OPUS TEST CALL (1 sent, not cached): {json.dumps(test)}")
        (out / "k10-opus-testone-261023.json").write_text(json.dumps(test, indent=1))
        return 0 if got[0] is not None and test["label"] != "unparseable" else 5
    if args.opus and otodo:
        t1 = time.time()
        for s in range(0, len(otodo), 200):
            part = otodo[s:s + 200]
            got, stopped = asyncio.run(opus_many([grader_msgs(i, a) for _, i, a in part], args.opus_max_tokens,
                                                 args.opus_inflight, out / jfile("k_log")))
            with og_path.open("a") as fh:
                for (gk, _, _), r in zip(part, got, strict=True):
                    if r is None:
                        continue
                    u = r[1].get("usage") or {}
                    nl = bool(r[1].get("no_label"))
                    x = {"k": gk, "v": "no_label" if nl else grade_word(r[0]),
                         "raw": "<0-token empty x2>" if nl else r[0][:40], "pt": int(u.get("prompt_tokens") or 0),
                         "ct": int(u.get("completion_tokens") or 0)}
                    og[gk] = x
                    fh.write(json.dumps(x) + "\n")
            log(f"k10 grader opus: {min(s + 200, len(otodo))}/{len(otodo)}; opus {json.dumps(dict(OPUS_STATS))}")
            if stopped:
                log(f"STOP opus: error cap reached; {json.dumps(dict(OPUS_STATS))}")
                return 4
        opus_audit = {k: v for k, v in OPUS_STATS.items() if k != "consecutive"} | {"seconds": round(time.time() - t1)}
    still_empty = [gk for gk in uniq if gk not in og]
    if args.opus_final and still_empty:
        # After the sweep: still-empty pairs are unparseable (= incorrect, prereg), listed by count.
        for gk in still_empty:
            og[gk] = {"k": gk, "v": "unparseable", "raw": "<empty after sweep>", "pt": 0, "ct": 0}
        log(f"k10 opus: {len(still_empty)} pairs still empty after the sweep, recorded as unparseable")
    if isinstance(opus_audit, dict):
        opus_audit["still_empty_recorded_unparseable"] = len(still_empty) if args.opus_final else None
    opus_done = all(gk in og for gk in uniq)
    if opus_done and not args.opus:
        opus_audit = {"status": "complete from cache"}

    # ---- scoring
    # Two readings of Opus no_label (0-token empty twice): "opus" counts it as incorrect
    # (the prereg's unparseable rule); "opus_excl" excludes it (from per-arm counts, and
    # from a contrast whenever either arm's answer for that question is no_label).
    EXCL: dict[str, bool] = {"qwen": False, "opus": False, "opus_excl": True}

    def grade(g: dict[str, dict[str, Any]], rd: str, a: str, i: str, excl: bool = False) -> str:
        v = g[gkey(i, answers[f"{rd}|{a}|{i}"]["answer"])]["v"]
        if v == "no_label":
            return "no_label" if excl else "incorrect"
        return "incorrect" if v == "unparseable" else v

    graders = {"qwen": qg, **({"opus": og, "opus_excl": og} if opus_done else {})}
    summary: dict[str, Any] = {}
    for gname, g in graders.items():
        ex = EXCL[gname]
        for rd in K10_READERS:
            for st in K10_SETS:
                for a in K10_ARMS:
                    gl = {i: grade(g, rd, a, i, ex) for i in ids[st]}
                    gs = Counter(gl.values())
                    inc_found = sum(gl[i] == "incorrect" and found10[a][i] for i in ids[st])
                    summary[f"{gname}|{rd}|{st}|{a}"] = {
                        "n": len(ids[st]) - gs["no_label"], "no_label": gs["no_label"],
                        **{x: gs[x] for x in K10_GRADES},
                        "correct_or_partial": gs["correct"] + gs["partial"],
                        "incorrect_with_gold_in_top10": inc_found,
                        "found10": sum(found10[a][i] for i in ids[st])}
    context = {st: {a: {"mean": round(statistics.mean(ctx[a][i] for i in ids[st]), 1),
                        "median": statistics.median(ctx[a][i] for i in ids[st]),
                        "max": max(ctx[a][i] for i in ids[st])} for a in K10_ARMS} for st in K10_SETS}

    def corr(g: dict[str, dict[str, Any]], rd: str, a: str, ex: bool = False) -> dict[str, Any]:
        return {i: {"c": 1 if grade(g, rd, a, i, ex) == "correct" else None} for i in recs}

    pairs_c = [("1 vs live", a, "L") for a in K10_ARMS if a != "L"]
    pairs_c += [("2 size", a, "T512o0") for a in ("T128o0", "T256o0", "T1024o0")]
    pairs_c += [("3 cap", "SEM512", "L")]
    contrasts: dict[str, Any] = {}
    verdicts: dict[str, Any] = {}
    for gname, g in graders.items():
        ex = EXCL[gname]
        cs: dict[str, Any] = {}
        for fam, x, y in pairs_c:
            for st in K10_SETS:
                for rd in K10_READERS:
                    keep = {i for i in ids[st] if not ex or (grade(g, rd, x, i, True) != "no_label"
                                                              and grade(g, rd, y, i, True) != "no_label")}
                    sub = Setup(arms={}, pools={}, records=[r for r in records if r["record_id"] in keep],
                                gold=su.gold, gold_fold=su.gold_fold, rec_paper=su.rec_paper, jats_docs=set())
                    c = contrast(corr(g, rd, x, ex), corr(g, rd, y, ex), sub, "c")
                    c["n"] = len(keep)
                    c.pop("net_bazant", None)
                    c.pop("net_other_papers", None)
                    c["net_by_stratum"] = {s: sum(recs[z]["stratum"] == s for z in c["gained_ids"])
                                           - sum(recs[z]["stratum"] == s for z in c["lost_ids"]) for s in STRATA}
                    cs[f"{fam}|{st}|{rd}|{x} - {y}"] = c
        for fam, x, y in pairs_c:
            for st in K10_SETS:
                for rd in K10_READERS:
                    c = cs[f"{fam}|{st}|{rd}|{x} - {y}"]
                    other = cs[f"{fam}|{st}|{'qwen' if rd == 'scout' else 'scout'}|{x} - {y}"]
                    disc = c["gained"] + c["lost"]
                    r1 = c["net"] >= 5
                    r2 = (other["net"] > 0) == (c["net"] > 0) and other["net"] != 0
                    r3 = sum(v > 0 for v in c["net_by_stratum"].values()) >= 2
                    verdicts[f"{gname}|{fam}|{st}|{rd}|{x} - {y}"] = {
                        "net": c["net"], "other_reader_net": other["net"], "discordant": disc,
                        "strata_with_net_gain": sum(v > 0 for v in c["net_by_stratum"].values()),
                        "p": c["mcnemar_p"], "passes": r1 and r2 and r3, "unresolved": disc < 10}
        contrasts[gname] = cs

    agreement = None
    skipped: list[dict[str, Any]] = []
    if opus_done:
        keys = sorted(uniq)
        a_raw = [og[gk]["v"] for gk in keys]

        def fix(v: str) -> str:
            return "incorrect" if v in ("unparseable", "no_label") else v

        def agree(ks: list[int]) -> dict[str, Any]:
            a_l = [fix(a_raw[j]) for j in ks]
            b_l = [fix(qg[keys[j]]["v"]) for j in ks]
            return {"n": len(ks), "kappa_4class": kappa(a_l, b_l),
                    "kappa_correct_vs_not": kappa(["c" if x == "correct" else "n" for x in a_l],
                                                  ["c" if x == "correct" else "n" for x in b_l])}

        allj = list(range(len(keys)))
        agreement = {"no_label_as_incorrect": agree(allj),
                     "no_label_excluded": agree([j for j in allj if a_raw[j] != "no_label"]),
                     "opus_distribution": dict(Counter(a_raw)),
                     "qwen_distribution": dict(Counter(qg[gk]["v"] for gk in keys))}
        by_gk: dict[str, list[str]] = defaultdict(list)
        for k, x in answers.items():
            rd, a, i = k.split("|", 2)
            by_gk[gkey(i, x["answer"])].append(f"{rd}|{a}")
        for gk in keys:
            if og[gk]["v"] != "no_label":
                continue
            i = uniq[gk][0]
            skipped.append({"item": gk[:16], "record_id": i, "set": recs[i]["set"], "reader_arms": sorted(by_gk[gk]),
                            "stratum": recs[i]["stratum"], "doc_id": recs[i]["doc_id"]})

    rates_k10: dict[str, Any] = {}
    if og:
        for st in K10_SETS:
            for stratum in sorted({recs[i]["stratum"] for i in ids[st]}):
                ks = [gk for gk, (i, _) in uniq.items() if recs[i]["set"] == st and recs[i]["stratum"] == stratum]
                nl = sum(og.get(gk, {}).get("v") == "no_label" for gk in ks)
                fl = sum(og.get(gk, {}).get("raw") == "<empty after sweep>" or gk not in og for gk in ks)
                rates_k10[f"{st}|{stratum}"] = {"grades": len(ks), "no_label": nl, "failed": fl,
                                                "no_label_rate": round(nl / len(ks), 4) if ks else None,
                                                "failed_rate": round(fl / len(ks), 4) if ks else None}

    from ragstack.provenance import experiment_provenance

    report = {"prereg": FILES["prereg_k10"],
              "prereg_sha256": sha((out / FILES["prereg_k10"]).read_text()),
              "provenance": experiment_provenance(repo=str(sat._REPO_PY.parent)),
              "n": {s: len(v) for s, v in ids.items()}, "arms": list(K10_ARMS), "lists_check": lists_check,
              "window_drops": dict(drops), "readers": reader_stats,
              "graders": {"qwen": {"distinct_graded": len(uniq), "distribution": dict(Counter(qg[gk]["v"] for gk in uniq)),
                                   "seconds": qwen_secs,
                                   "prompt_tokens": sum(qg[gk]["pt"] for gk in uniq),
                                   "completion_tokens": sum(qg[gk]["ct"] for gk in uniq),
                                   "unparseable_raw": sorted({qg[gk]["raw"] for gk in uniq if qg[gk]["v"] == "unparseable"})[:20]},
                          "opus": ({"status": "pending"} if not opus_done else
                                   {"distribution": dict(Counter(og[gk]["v"] for gk in uniq)),
                                    "prompt_tokens": sum(og[gk]["pt"] for gk in uniq),
                                    "completion_tokens": sum(og[gk]["ct"] for gk in uniq),
                                    "unparseable_raw": sorted({og[gk]["raw"] for gk in uniq
                                                               if og[gk]["v"] == "unparseable"})[:20]}),
                          "opus_dry_run": dry, "opus_audit": opus_audit, "agreement": agreement,
                          "opus_no_label_items": skipped,
                          "opus_no_label_by_stratum": dict(Counter(x["stratum"] for x in skipped)),
                          "opus_no_label_by_doc": dict(Counter(x["doc_id"] for x in skipped)),
                          "opus_no_label_by_set": dict(Counter(x["set"] for x in skipped)),
                          "opus_rates_by_set_stratum": rates_k10, "opus_uncached_not_asked": otodo_skipped},
              "judge_model": JUDGE["model"], "judge_key": JUDGE["key"], "judge_subset": args.judge_subset,
              "cross_judge": cross_judge(out, k_keys, "k_cache", out / "k10-grades-qwen-261023.jsonl", k_sample,
                                         "incorrect"),
              "primary_grader": "opus" if opus_done else "pending (qwen secondary shown)",
              "summary": summary, "context_tokens_SFR": context, "contrasts": contrasts, "verdicts": verdicts,
              "load": {**dict(sat.EMBED_STATS), "wall_seconds": round(time.time() - t0)}}
    path = out / (FILES["results_k10"] if JUDGE["key"] == args.primary_judge
                  else alt_results(FILES["results_k10"], JUDGE["key"]))
    path.write_text(json.dumps(report, indent=1, default=str))
    log(f"wrote {path} ({time.time() - t0:.0f}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG"),
                    help="the private JSON config (or env CHUNKTRIAL_PRIVATE_CONFIG); required")
    ap.add_argument("--out-dir", default=None, help="default: the config's out_dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    for name in ("run", "addendum-s", "part3", "k10"):
        r = sub.add_parser(name)
        r.add_argument("--embed-urls", default=",".join(f"http://127.0.0.1:{p}" for p in range(9001, 9007)))
        r.add_argument("--rerank-url", default="http://127.0.0.1:50052")
        if name in ("part3", "k10"):
            r.add_argument("--opus", action="store_true", help="make gateway judge calls (only after the gateway owner's go)")
            r.add_argument("--opus-inflight", type=int, default=2)
            r.add_argument("--opus-max-tokens", type=int, default=8,
                           help="Opus max_tokens (default 8, the approved value; Opus 5 spends ~10 hidden tokens)")
            r.add_argument("--opus-dry-run", action="store_true",
                           help="build and count the Opus requests without sending any; then exit")
            r.add_argument("--opus-test-one", action="store_true",
                           help="with --opus-dry-run: send exactly ONE request (not cached) and check the label")
            r.add_argument("--generate-only", action="store_true")
            r.add_argument("--judge-model", choices=sorted(JUDGE_MODELS), default="opus5",
                           help="gateway judge/grader model (default opus5 = Claude Opus 5)")
            r.add_argument("--primary-judge", choices=sorted(JUDGE_MODELS), default="opus48",
                           help="the judge whose run writes the main results file; others write <file>.<key>.json")
            r.add_argument("--judge-subset", choices=("all", "sol"), default="all",
                           help="sol: Opus-5 no_label/failed items + a stratified sample of 400 (seed 261023)")
            r.add_argument("--probe-keys", default="",
                           help="comma-separated item-key prefixes: ONE call each, not cached; print and exit")
            r.add_argument("--opus-cache-only", action="store_true",
                           help="with --opus: score from the Opus cache only; make no gateway calls")
            r.add_argument("--opus-final", action="store_true",
                           help="k10: after the sweep, record still-missing Opus grades as unparseable")
            r.add_argument("--opus-output-cap", type=int, default=3_000_000,
                           help="stop once Opus completion tokens since --opus-cap-since (both batches) pass this")
            r.add_argument("--opus-cap-since", type=float, default=None,
                           help="epoch seconds of the go; earlier call-log rows don't count toward the cap")
    import deployed_docs_part4 as p4

    p4.add_part4_parser(sub)
    args = ap.parse_args(argv)
    cfg = load_private_config(args.private_config)
    if args.out_dir is None:
        args.out_dir = cfg["out_dir"]
    if getattr(args, "judge_model", None):
        set_judge(args.judge_model)
    if getattr(args, "opus_cap_since", None) is not None:
        global OPUS_OUTPUT_BASE, OPUS_OUTPUT_CAP
        OPUS_OUTPUT_CAP = args.opus_output_cap
        OPUS_OUTPUT_BASE = opus_output_base(Path(args.out_dir), args.opus_cap_since)
        log(f"opus output cap {OPUS_OUTPUT_CAP}; already used since go {OPUS_OUTPUT_BASE}")
    elif getattr(args, "opus", False) or getattr(args, "opus_test_one", False) or getattr(args, "probe_keys", ""):
        raise SystemExit("--opus/--opus-test-one need --opus-cap-since (the go time) for the output-token stop")
    return {"prepare": cmd_prepare, "run": cmd_run, "addendum-s": cmd_addendum_s,
            "part3": cmd_part3, "k10": cmd_k10, "part4": p4.cmd_part4}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
