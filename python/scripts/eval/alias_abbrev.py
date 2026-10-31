#!/usr/bin/env python3
"""Alias study (2026-11): the ABBREVIATION glossary for OA-1000 and the recount.

A glossary entry maps an ALIAS to a PRIMARY TERM. The round-3 symbol glossary
(Qwen arm file, ``llm`` ∪ ``rules`` entries) is kept as it is; this script adds
abbreviation entries under the owner's rule, merges the two (each entry records
its ``kind``) and recounts the G / C candidates on the export.

Abbreviation rule (docs/plans/alias-study-2026-11/ABBREV.md has the full text):

* the abbreviation is DEFINED in the paper by one of two patterns —
  P1 ``long form (ABBR)`` (also ``(ABBR;`` / ``(ABBR,``) and P2 ``ABBR (long form)``;
* the long form plausibly expands the letters (:func:`aligns`: the first
  letter starts the long form, every letter is found in order, and the initial
  of EVERY content word of the long form is one of the matched letters);
* the long form has at most 8 words; a one-word long form needs >= 6 chars and
  >= 2x the abbreviation's length;
* the abbreviation is not a single letter, a number, a roman numeral, a unit,
  a common English word, or a capitalised word (casing rule) — :func:`sf_exclusion`;
* a paper that defines the same abbreviation with unrelated long forms
  (disjoint content words) drops it.

Use in a chunk other than the definition chunk is RECORDED (``used_elsewhere``,
``use_chunk_ids``) but does not gate entry; the counts are reported both ways.

G-candidate (strict): a chunk of the paper containing the alias as a whole
token and neither the primary's full word sequence nor any of its content words
(>= 5 chars). C-candidate: a chunk of the same paper, not a G chunk, containing
a termlike primary of the paper literally. Matchers and normalisation are the
feasibility count's (``feas.py``, 2026-10-30), restated here.

Inputs are read-only; outputs go to a private directory outside the repo.
No model, gateway, store or API call is made.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402

from ragstack.ingestion.symbol_glossary import _BARS, canonical_symbol, canonical_text  # noqa: E402

SEED = 20261030

# ---------------------------------------------------------------- normalisation (feas.py)
TOK = re.compile(rf"[\w{_BARS}]+")
WORD = re.compile(r"[^\W_]+")
STOP5 = frozenset("""about above after again against among because before being below between both could during
each every further having might other others their there these those through under until using where which
while would within without whose shall should value values number numbers based used means given total
first second third different specific""".split())


def lem(w: str) -> str:
    w = w.lower()
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def words(text: str) -> list[str]:
    t = canonical_text(text).replace("-", " ").replace("–", " ").replace("/", " ")
    return [lem(w) for w in WORD.findall(t)]


def contains_seq(hay: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    if n == 0:
        return False
    needle = list(needle)
    first = needle[0]
    return any(w == first and list(hay[i:i + n]) == needle for i, w in enumerate(hay))


def content5(ws: Iterable[str]) -> set[str]:
    return {w for w in ws if len(w) >= 5 and w not in STOP5 and not w.isdigit()}


# ---------------------------------------------------------------- abbreviation rules
#: function words that need not contribute a letter to the abbreviation
LF_STOP = frozenset("a an and as at by for from in into its of on or per the to via with".split())
#: two-letter common English words (the dictionary's two-letter entries are mostly abbreviations)
TWO_LETTER_WORDS = frozenset("am an as at be by do go he if in is it me my no of on or so to up us we".split())
#: unit spellings that pass the casing rule (case-sensitive)
UNITS = frozenset("""mM nM pM uM mL dL uL kDa Da kPa MPa GPa mmHg cmH2O kHz MHz GHz Hz mV kV mA kW MW kWh kJ MJ
pH pKa mOsm Gy cGy mGy kGy Sv mSv Bq kBq MBq IU mIU kb Mb Gb kbp Mbp Gbp dB kCal kcal OD""".split())
ROMAN = re.compile(r"^[IVX]+$")
SF_RE = r"[A-Za-z0-9]{2,11}"
P1 = re.compile(rf"\((?P<sf>{SF_RE})(?:\)|[;,]\s)")
P2 = re.compile(rf"(?<![\w\-/.])(?P<sf>{SF_RE})\s\((?P<inside>[^()]{{3,160}})\)")
#: the long form of P1 never crosses one of these (clause / sentence boundary)
BOUNDARY = re.compile(r"[;:!?()\[\],]|\.\s")
MAX_LF_WORDS = 8


def normalize_sf(sf: str) -> str:
    """Strip a plural ``s`` from an upper-case abbreviation (``MSCs`` -> ``MSC``)."""
    if len(sf) >= 3 and sf[-1] == "s" and sf[-2].isupper() and sum(c.isupper() for c in sf[:-1]) >= 2:
        return sf[:-1]
    return sf


def sf_exclusion(sf: str, common_words: frozenset[str]) -> str | None:
    """Why ``sf`` cannot be a glossary abbreviation, or ``None`` when it can."""
    if not sf.isascii() or not sf.isalnum():
        return "charset"
    if sf.isdigit():
        return "number"
    letters = [c for c in sf if c.isalpha()]
    if len(letters) < 2:
        return "single_letter"
    upper = sum(c.isupper() for c in sf)
    if not (upper >= 2 or (upper == 1 and not sf[0].isupper())):
        return "casing"            # Fig, Ref, lowercase words
    if sf in UNITS:
        return "unit"
    if ROMAN.match(sf):
        return "roman_numeral"
    low = sf.lower()
    if (len(low) == 2 and low in TWO_LETTER_WORDS) or (len(low) >= 3 and low in common_words):
        return "common_word"
    return None


def lf_words(lf: str) -> list[str]:
    t = canonical_text(lf).lower().replace("'s", "").replace("’s", "")
    t = t.replace("-", " ").replace("–", " ").replace("/", " ")
    return WORD.findall(t)


def aligns(sf: str, lf: str) -> bool:
    """Letter alignment: SF's first letter is the long form's first letter, all
    SF letters/digits occur in the long form in order, and the initial of every
    content word (not in :data:`LF_STOP`) is one of the matched characters."""
    s = [c for c in sf.lower() if c.isalnum()]
    ws = lf_words(lf)
    if not s or not ws or ws[0] in LF_STOP:
        return False
    text = " ".join(ws)
    req: list[int] = []
    pos = 0
    for w in ws:
        if w not in LF_STOP:
            req.append(pos)
        pos += len(w) + 1
    if text[0] != s[0]:
        return False

    @cache
    def f(si: int, li: int) -> bool:
        k = bisect.bisect_right(req, li)
        nxt = req[k] if k < len(req) else None
        if si == len(s):
            return nxt is None
        hi = nxt if nxt is not None else len(text) - 1
        return any(text[p] == s[si] and f(si + 1, p) for p in range(li + 1, hi + 1))

    return f(1, 0)


def lf_ok(sf: str, lf: str) -> bool:
    ws = lf_words(lf)
    if not ws or len(ws) > MAX_LF_WORDS:
        return False
    if " ".join(ws) == sf.lower():
        return False
    if len(ws) == 1 and len(ws[0]) < max(6, 2 * len(sf)):
        return False
    if all(w.isdigit() or w in LF_STOP for w in ws):
        return False
    return aligns(sf, lf)


@dataclass(frozen=True)
class Definition:
    sf: str          # as written
    alias: str       # normalised (plural stripped)
    lf: str
    pattern: str     # P1 | P2
    start: int       # span in the canonical text
    end: int


def find_definitions(canon: str) -> list[Definition]:
    """Definition instances in canonical text, before the SF exclusions."""
    out: list[Definition] = []
    for m in P1.finditer(canon):
        if m.start() == 0 or not canon[m.start() - 1].isspace():
            continue
        sf = m.group("sf")
        alias = normalize_sf(sf)
        base = max(0, m.start() - 200)
        pre = canon[base:m.start()]
        cuts = list(BOUNDARY.finditer(pre))
        if cuts:
            base += cuts[-1].end()
            pre = pre[cuts[-1].end():]
        spans = [t.start() for t in re.finditer(r"\S+", pre)]
        toks = pre.split()
        for k in range(1, min(len(toks), MAX_LF_WORDS + 2) + 1):
            lf = " ".join(toks[-k:]).strip(" \"'“”")
            if lf_ok(alias, lf):
                out.append(Definition(sf, alias, lf, "P1", base + spans[-k], m.end()))
                break
    for m in P2.finditer(canon):
        sf = m.group("sf")
        alias = normalize_sf(sf)
        inside = m.group("inside").strip()
        if re.match(r"(?i)(i\.?e|e\.?g|see|ref|fig)\b", inside):
            continue
        lf = re.split(r"[;,]", inside)[0].strip(" \"'“”.")
        if len(lf_words(lf)) >= 2 and lf_ok(alias, lf):
            out.append(Definition(sf, alias, lf, "P2", m.start(), m.end()))
    return out


def load_common_words(path: str | Path) -> frozenset[str]:
    """All-lowercase entries of a word list (``/usr/share/dict/american-english``)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return frozenset(w for w in (line.strip() for line in fh) if w.isalpha() and w.islower())


# ---------------------------------------------------------------- building entries
@dataclass
class Corpus:
    rows: list[Any]
    paper: dict[str, str]                # chunk -> paper
    doc: dict[str, str]                  # chunk -> doc
    paper_chunks: dict[str, list[str]]   # file order
    canon: dict[str, str]
    toks: dict[str, set[str]]
    cwords: dict[str, list[str]]
    cwset: dict[str, set[str]]
    tok_index: dict[str, set[str]]


def build_corpus(rows: Sequence[Any], paper_of: Callable[[Any], str]) -> Corpus:
    paper, doc, canon, toks, cwords, cwset = {}, {}, {}, {}, {}, {}
    paper_chunks: dict[str, list[str]] = defaultdict(list)
    tok_index: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        cid = r.chunk_id
        paper[cid] = paper_of(r)
        doc[cid] = r.doc_id
        c = canonical_text(r.content)
        canon[cid] = c
        ts = set(TOK.findall(c))
        toks[cid] = ts
        for t in ts:
            tok_index[t].add(cid)
        ws = words(r.content)
        cwords[cid] = ws
        cwset[cid] = set(ws)
        paper_chunks[paper[cid]].append(cid)
    return Corpus(list(rows), paper, doc, dict(paper_chunks), canon, toks, cwords, cwset, dict(tok_index))


def alias_tokens(alias: str, kind: str) -> set[str]:
    return {alias} if kind == "symbol" else {alias, alias + "s"}


def _disjoint(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    sa, sb = content5(a) or set(a), content5(b) or set(b)
    return not (sa & sb)


def build_abbrev_entries(corpus: Corpus, common_words: frozenset[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    defs: dict[str, dict[str, list[tuple[str, Definition]]]] = defaultdict(lambda: defaultdict(list))
    for r in corpus.rows:
        cid = r.chunk_id
        for d in find_definitions(corpus.canon[cid]):
            stats[f"instances_{d.pattern}"] += 1
            why = sf_exclusion(d.alias, common_words)
            if why:
                excluded[why] += 1
                continue
            defs[corpus.paper[cid]][d.alias].append((cid, d))
    entries: list[dict[str, Any]] = []
    for p in sorted(defs):
        pcs = corpus.paper_chunks[p]
        for alias in sorted(defs[p]):
            inst = defs[p][alias]
            variants: dict[tuple[str, ...], list[tuple[str, Definition]]] = {}
            for cid, d in inst:
                variants.setdefault(tuple(words(d.lf)), []).append((cid, d))
            keys = list(variants)
            if any(_disjoint(keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys))):
                stats["dropped_conflicting_long_forms"] += 1
                continue
            best = max(keys, key=lambda k: (len(variants[k]), -keys.index(k)))
            cid0, d0 = variants[best][0]
            def_chunks = list(dict.fromkeys(c for c, _ in inst))
            want = alias_tokens(alias, "abbreviation")
            uses = [c for c in pcs if c not in def_chunks and corpus.toks[c] & want]
            entries.append({
                "paper": p, "doc_id": corpus.doc[cid0], "alias": alias, "primary": d0.lf,
                "kind": "abbreviation", "pattern": d0.pattern,
                "def_chunk_id": cid0, "def_chunk_ids": def_chunks, "def_span": [d0.start, d0.end],
                "use_chunk_ids": uses, "used_elsewhere": bool(uses),
                "n_definitions": len(inst), "n_long_form_variants": len(keys),
            })
    stats["entries"] = len(entries)
    stats["entries_used_elsewhere"] = sum(e["used_elsewhere"] for e in entries)
    stats["papers_with_entry"] = len({e["paper"] for e in entries})
    stats["papers_with_used_elsewhere_entry"] = len({e["paper"] for e in entries if e["used_elsewhere"]})
    return entries, {**dict(stats), "excluded_instances": dict(excluded)}


def load_symbol_entries(arm: str | Path, doc_paper: Mapping[str, str]) -> list[dict[str, Any]]:
    """The round-3 symbol glossary: ``llm`` ∪ ``rules`` entries; per paper, one
    entry per (alias, primary word sequence), first seen wins (feas.py)."""
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    out: list[dict[str, Any]] = []
    with open(arm, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            r = json.loads(line)
            p = doc_paper.get(r["doc_id"])
            if p is None:
                continue
            for k in ("llm", "rules"):
                for e in (r.get(k) or {}).get("entries") or []:
                    key = canonical_symbol(e["symbol"])
                    for d in e["definitions"]:
                        t = d["text"].strip()
                        nk = (p, key, tuple(words(t)))
                        if t and nk not in seen:
                            seen.add(nk)
                            out.append({"paper": p, "doc_id": r["doc_id"], "alias": key, "primary": t,
                                        "kind": "symbol", "extractor": k})
    return out


def merge(symbol: Sequence[Mapping[str, Any]], abbrev: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Symbol ∪ abbreviation, keyed (paper, alias, primary words); an entry in both is ``kind: both``."""
    out: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for e in symbol:
        out[(e["paper"], e["alias"], tuple(words(e["primary"])))] = dict(e)
    for e in abbrev:
        k = (e["paper"], e["alias"], tuple(words(e["primary"])))
        if k in out:
            out[k] = {**e, "kind": "both", "extractor": out[k].get("extractor")}
        else:
            out[k] = dict(e)
    return list(out.values())


# ---------------------------------------------------------------- recount
def termlike(pw: Sequence[str], kind: str) -> bool:
    cap = 5 if kind == "symbol" else MAX_LF_WORDS
    return 1 <= len(pw) <= cap and not any(w.isdigit() for w in pw)


def quant(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(p: float) -> float:
        return s[int(round(p * (len(s) - 1)))]

    return {"n": len(s), "min": s[0], "p25": q(.25), "median": q(.5), "p75": q(.75), "p90": q(.9),
            "max": s[-1], "mean": round(sum(s) / len(s), 2), "share_zero": round(sum(1 for x in s if x == 0) / len(s), 4)}


def g_candidates(corpus: Corpus, entries: Sequence[Mapping[str, Any]]
                 ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
    """paper -> G entries (strict rule, termlike primary), each with its G chunks;
    and paper -> every chunk holding an alias of the paper without that entry's
    primary sequence (feas.py's default G chunks, which C candidates avoid)."""
    G: dict[str, list[dict[str, Any]]] = defaultdict(list)
    loose: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        pw = tuple(words(e["primary"]))
        if not pw:
            continue
        p = e["paper"]
        hits: set[str] = set()
        for t in alias_tokens(e["alias"], e["kind"]):
            hits |= corpus.tok_index.get(t, set())
        own = hits & set(corpus.paper_chunks.get(p, ()))
        no_seq = [c for c in own if not contains_seq(corpus.cwords[c], pw)]
        loose[p].update(no_seq)
        if not termlike(pw, e["kind"]):
            continue
        c5 = content5(pw)
        gch = [c for c in no_seq if not (c5 & corpus.cwset[c])]
        if gch:
            G[p].append({"alias": e["alias"], "pw": pw, "kind": e["kind"], "chunks": sorted(gch),
                         "other_corpus": len(hits) - 1, "other_paper": len(own) - 1})
    return dict(G), dict(loose)


def c_candidates(corpus: Corpus, entries: Sequence[Mapping[str, Any]],
                 avoid: Mapping[str, set[str]]) -> dict[str, int]:
    by_paper: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for e in entries:
        by_paper[e["paper"]].append(e)
    out: dict[str, int] = {}
    for p, lst in by_paper.items():
        gch = avoid.get(p, set())
        cands: set[str] = set()
        for e in lst:
            pw = tuple(words(e["primary"]))
            if not termlike(pw, e["kind"]) or not (len(pw) >= 2 or len("".join(pw)) >= 8):
                continue
            for c in corpus.paper_chunks[p]:
                if c not in gch and pw[0] in corpus.cwset[c] and contains_seq(corpus.cwords[c], pw):
                    cands.add(c)
        if cands:
            out[p] = len(cands)
    return out


def _kinds(gl: Iterable[Mapping[str, Any]]) -> str:
    ks = set()
    for x in gl:
        if x["kind"] in ("symbol", "both"):
            ks.add("symbol")
        if x["kind"] in ("abbreviation", "both"):
            ks.add("abbreviation")
    return "both" if len(ks) == 2 else next(iter(ks))


def recount(corpus: Corpus, entries: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[int]]:
    G, loose = g_candidates(corpus, entries)
    C = c_candidates(corpus, entries, loose)
    nG = {p: len(v) for p, v in G.items()}
    res: dict[str, Any] = {
        "entries": dict(Counter(e["kind"] for e in entries)),
        "papers_with_entry": len({e["paper"] for e in entries}),
        "G_entries": sum(nG.values()),
        "G_candidates_entry_chunk_pairs": sum(len(x["chunks"]) for v in G.values() for x in v),
    }
    for k in (1, 2, 3):
        ps = [p for p, n in nG.items() if n >= k]
        res[f"papers_G_ge{k}"] = {"total": len(ps), **Counter(_kinds(G[p]) for p in ps)}
    res["papers_with_C"] = len(C)
    usable = [p for p, n in nG.items() if n >= 2 and p in C]
    res["usable_papers"] = {"total": len(usable), **Counter(_kinds(G[p]) for p in usable)}
    res["papers_G_ge1_and_C"] = sum(1 for p in nG if p in C)
    pairs = [(x, c) for v in G.values() for x in v for c in x["chunks"]]
    res["G_candidate_other_chunks_with_alias"] = {
        kind: {"corpus": quant([x["other_corpus"] for x, _ in pairs if _kinds([x]) == kind or kind == "all"]),
               "same_paper": quant([x["other_paper"] for x, _ in pairs if _kinds([x]) == kind or kind == "all"])}
        for kind in ("all", "symbol", "abbreviation")}
    return res, sorted((nG[p] for p in usable), reverse=True)


def collision(abbrev: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Same alias, different long form (disjoint content words) across papers."""
    per: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for e in abbrev:
        pw = tuple(words(e["primary"]))
        per[e["alias"]].setdefault(e["paper"], set()).update(content5(pw) or set(pw))
    multi = {a: d for a, d in per.items() if len(d) >= 2}
    differ = {a for a, d in multi.items()
              if any(not (x & y) for i, x in enumerate(list(d.values())) for y in list(d.values())[i + 1:])}
    n_ent = len(abbrev)
    n_ent_coll = sum(1 for e in abbrev if e["alias"] in differ)
    return {"aliases": len(per), "aliases_in_ge2_papers": len(multi),
            "aliases_in_ge2_papers_with_different_long_form": len(differ),
            "rate_among_shared_aliases": round(len(differ) / len(multi), 4) if multi else None,
            "rate_among_all_aliases": round(len(differ) / len(per), 4) if per else None,
            "entries_whose_alias_collides": n_ent_coll,
            "entry_rate": round(n_ent_coll / n_ent, 4) if n_ent else None}


# ---------------------------------------------------------------- yield / cost
CALLS_PER_JOB = 1.616                  # round-3 average
TOKENS_PER_CALL = (870, 2224)          # round-3 set A, after the reasoning change (in, out)
MAX_G_JOBS, MAX_C_JOBS = 6, 2


def paper_yield(n_g: int, p: float) -> tuple[float, float]:
    """(P(>= 2 accepted G), E[G jobs]) drafting G entries one at a time, stop at 2
    accepted, at most min(n_g, 6) jobs (feas.py stop rule)."""
    m = min(n_g, MAX_G_JOBS)

    @cache
    def f(j: int, a: int) -> tuple[float, float]:
        if a >= 2:
            return 1.0, 0.0
        if j == m:
            return 0.0, 0.0
        s1, e1 = f(j + 1, a + 1)
        s0, e0 = f(j + 1, a)
        return p * s1 + (1 - p) * s0, 1 + p * e1 + (1 - p) * e0

    return f(0, 0)


def yield_cost(pool: Sequence[int], p: float, target: int = 100) -> dict[str, Any]:
    """Papers are drawn from the usable pool at random (pool means, as feas.py).
    A paper is usable when it reaches 2 accepted G and 1 accepted C; C is drafted
    up to 2 times, stop at the first accepted, at the same p."""
    if not pool:
        return {"reachable": False, "pool": 0}
    pc = 1 - (1 - p) ** MAX_C_JOBS
    ec = 1 + (1 - p)
    ys = [paper_yield(n, p) for n in pool]
    pu = [s * pc for s, _ in ys]
    expected_all = sum(pu)
    out: dict[str, Any] = {"pool": len(pool), "p_C_accepted": round(pc, 4),
                           "expected_usable_if_all_drafted": round(expected_all, 1)}
    if expected_all < target:
        out["reachable"] = False
        return out
    rate = expected_all / len(pool)
    n = math.ceil(target / rate)
    eg = sum(e for _, e in ys) / len(pool)
    jobs = n * (eg + ec)
    jobs_c_after = n * sum(e + s * ec for s, e in ys) / len(pool)
    calls = jobs * CALLS_PER_JOB
    out.update({"reachable": True, "papers_to_attempt": n, "usable_rate_per_paper": round(rate, 4),
                "jobs": round(jobs), "calls": round(calls),
                "tokens_in": round(calls * TOKENS_PER_CALL[0]), "tokens_out": round(calls * TOKENS_PER_CALL[1]),
                "jobs_if_C_only_after_2G": round(jobs_c_after),
                "calls_if_C_only_after_2G": round(jobs_c_after * CALLS_PER_JOB)})
    return out


# ---------------------------------------------------------------- outputs
def snippet(canon: str, span: Sequence[int], width: int = 200) -> str:
    a, b = max(0, span[0]), min(len(canon), span[1])
    if b - a >= width:
        return canon[a:a + width]
    pad = (width - (b - a)) // 2
    lo = max(0, a - pad)
    return canon[lo:lo + width]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--export", required=True, help="chunk export (chunks-oa.jsonl)")
    ap.add_argument("--arm", required=True, help="round-3 symbol glossary arm file")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--review-dir", required=True, type=Path)
    ap.add_argument("--paper-key", default="path_root", choices=("path_root", "doc_id"))
    ap.add_argument("--dict", default="/usr/share/dict/american-english")
    ap.add_argument("--spot-n", type=int, default=40)
    ap.add_argument("--allow-dirty", action="store_true", help="development only: outputs are not citable")
    a = ap.parse_args(argv)
    os.umask(0o077)
    for d in (a.out_dir, a.review_dir):
        g.refuse_in_repo(d, "alias-study outputs")
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    prov = g.experiment_provenance()
    if not g.citable(prov) and not a.allow_dirty:
        raise SystemExit("tree is dirty/untracked or commit unknown; commit first (or --allow-dirty)")

    rows = g.read_chunks([a.export])
    corpus = build_corpus(rows, lambda r: g.paper_of(r, a.paper_key))
    doc_paper = {r.doc_id: corpus.paper[r.chunk_id] for r in rows}
    common = load_common_words(a.dict)
    abbrev, astats = build_abbrev_entries(corpus, common)
    symbol = load_symbol_entries(a.arm, doc_paper)
    merged_all = merge(symbol, abbrev)
    used = [e for e in abbrev if e["used_elsewhere"]]
    merged_used = merge(symbol, used)

    variants = {"symbol_only": symbol, "abbrev_all_defined": abbrev, "abbrev_used_elsewhere": used,
                "merged_all_defined": merged_all, "merged_used_elsewhere": merged_used}
    counts, yields = {}, {}
    for name, ents in variants.items():
        counts[name], pool = recount(corpus, ents)
        yields[name] = {f"p={p}": yield_cost(pool, p) for p in (0.20, 0.315, 0.386)}
    papers = sorted(set(corpus.paper.values()))

    _write_jsonl(a.out_dir / "abbrev_entries.jsonl", abbrev)
    _write_jsonl(a.out_dir / "merged_glossary.jsonl",
                 ({**e, "used_elsewhere": e.get("used_elsewhere")} for e in merged_all))
    rng = random.Random(SEED)
    sample = rng.sample(abbrev, min(a.spot_n, len(abbrev)))
    review = [{"i": i + 1, "alias": e["alias"], "long_form": e["primary"], "pattern": e["pattern"],
               "used_elsewhere": e["used_elsewhere"],
               "definition_snippet": snippet(corpus.canon[e["def_chunk_id"]], e["def_span"]),
               "verdict": None} for i, e in enumerate(sample)]
    g.write_json_atomic(a.review_dir / f"abbrev_spotcheck_{len(review)}.json",
                        {"seed": SEED, "note": "owner review: set verdict to ok / wrong_long_form / not_abbreviation",
                         "rows": review})
    result = {
        "created": g.now_iso(), "provenance": prov, "citable": g.citable(prov),
        "inputs": {"export": a.export, "export_sha256": g.file_sha(a.export), "arm": a.arm,
                   "arm_sha256": g.file_sha(a.arm), "dict": a.dict, "dict_sha256": g.file_sha(a.dict),
                   "paper_key": a.paper_key},
        "corpus": {"chunks": len(rows), "documents": len(doc_paper), "papers": len(papers)},
        "abbreviation_build": astats,
        "collision": {"all_defined": collision(abbrev), "used_elsewhere": collision(used)},
        "recount": counts,
        "yield_100": {"assumptions": {"calls_per_job": CALLS_PER_JOB, "tokens_per_call_in_out": TOKENS_PER_CALL,
                                      "G_jobs_max": MAX_G_JOBS, "C_jobs_max": MAX_C_JOBS,
                                      "policy": "random draw from the usable pool; G sequential, stop at 2 accepted; "
                                                "C up to 2, stop at 1 accepted, same p; usable = 2 G and 1 C accepted"},
                      **yields},
        "spot_check": {"n": len(review), "seed": SEED},
    }
    g.write_json_atomic(a.out_dir / "recount.json", result)
    sums = [f"{g.file_sha(a.out_dir / f)}  {f}" for f in ("abbrev_entries.jsonl", "merged_glossary.jsonl", "recount.json")]
    (a.out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(json.dumps({"abbrev": astats, "citable": result["citable"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
