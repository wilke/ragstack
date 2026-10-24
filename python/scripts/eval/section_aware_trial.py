#!/usr/bin/env python
"""Exploratory offline trial: section-aware chunking vs fixed_token 512/64.

The design is pre-registered (``PREREG-section-aware-261022.md``, kept with the
outputs, outside the repo). Everything runs IN PROCESS -- no Elasticsearch, no
Qdrant, no store writes of any kind:

* BM25 is Lucene BM25 (k1 1.2, b 0.75, Lucene's one-byte length norms) over the
  arm's whole pool, on a standard-analyzer approximation (UAX#29-style word
  tokens, lowercased, no stop words). ``content`` is always queried;
  ``symbol_glossary`` (glossary on, boost 1, ``norms: false`` as the store maps
  it) and ``heading`` (heading arms) are summed ``most_fields``-style.
* Dense is exact cosine in numpy over SFR-Embedding-Mistral vectors from the
  OpenAI-compatible fleet (stateless use, <= 2 requests in flight/endpoint).
  Queries are embedded raw, as ragstack embeds queries.
* The reranker is the live cross-encoder sidecar (stateless use, <= 4 in flight),
  request shape ``{"query", "documents", "top_k"}`` as in
  ``glossary_rerank_experiment.py``; passage = :func:`rerank_passage` (+ heading).

Pipeline (mirrors ``glossary_rerank_experiment.py`` and v1.6.14):

* reranked: BM25 top 100 + dense top 100 -> RRF k=60 (dense list first, stable)
  -> per-document cap -> cut to 50 -> cross-encoder -> cap -> top 10;
* hybrid (no reranker) and BM25-only: as the live API runs them with
  ``rerank=false``, ``top_k=10``: each leg fetches top_k x 2 = 20, fuse, cap,
  top 10.

Subcommands: ``fetch`` (Europe PMC JATS, <= 1 req/s, cached), ``build`` (arms +
descriptives), ``calibrate`` (arm L, both caps, P-live), ``run`` (everything).

The eval set is PRIVATE: nothing here prints or writes queries, quotes or chunk
text. Result files hold counts, ranks and ids only. The downloaded XML is
untrusted data: it is parsed with the repo's stdlib-ElementTree JATS parser
(expat, which resolves no external entities); a document declaring entities is
refused; nothing in it is executed.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

# Run with ``python -I`` (the inputs are untrusted): -I drops PYTHONPATH and the
# script directory, so the repo's own package is put on the path explicitly.
_REPO_PY = Path(__file__).resolve().parents[2]
if str(_REPO_PY) not in sys.path:
    sys.path.insert(0, str(_REPO_PY))

import numpy as np  # noqa: E402

# --------------------------------------------------------------------- constants

EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"
CHUNK_SIZE, CHUNK_OVERLAP = 512, 64       # live collection spec fixed_token_512_64
UNIT_MAX_TOKENS = 480                      # jats_extract --unit-token-budget default
DEEP = 100                                 # rerank path: top_k 50 x multiplier 2
SHALLOW = 20                               # rerank=false path: top_k 10 x multiplier 2
RERANK_POOL = 50
TOP_K = 10
RRF_K = 60
K1, B = 1.2, 0.75
GLOSSARY_BOOST = 1.0                       # v1.6.14 TEXT_SYMBOL_GLOSSARY_BOOST
MAX_EMBED_INFLIGHT_PER_ENDPOINT = 2
MAX_RERANK_INFLIGHT = 4
BUDGETS = (5120, 2048)
BAZANT_PMCID = "PMC8092463"
POOLS = ("P-live", "P-owner", "P-owner+")
ARMS = ("L", "J0", "J1", "J1h", "S0", "Sa", "Sb", "Sc")
HEADING_ARMS = ("J1h", "Sa", "Sb", "Sc")


@dataclass(frozen=True)
class ArmSpec:
    base: str            # chunk set: L / J0 / J1 / S0
    h_bm25: bool         # heading as a BM25 field
    h_rerank: bool       # heading in the reranker passage
    h_embed: bool        # heading in the embedded text
    h_boost: float = 1.0  # heading BM25 field boost (x the run's heading-boost factor)
    prepend: bool = False  # heading prepended to the BM25 content text (addendum Sp/Spe)
    dense: str = "f32"     # addendum D dense storage variant: f32 / int8 / int8+r / bin / bin+r


ARM_SPEC = {
    "L": ArmSpec("L", False, False, False),
    "J0": ArmSpec("J0", False, False, False),
    "J1": ArmSpec("J1", False, False, False),
    "J1h": ArmSpec("J1", True, True, False),
    "S0": ArmSpec("S0", False, False, False),
    "Sa": ArmSpec("S0", True, False, False),
    "Sb": ArmSpec("S0", True, True, False),
    "Sc": ArmSpec("S0", True, True, True),
}
CONTRASTS = {  # name -> (arm, reference)
    "primary Sb-J1": ("Sb", "J1"),
    "Sc-J1": ("Sc", "J1"),
    "boundaries S0-J1": ("S0", "J1"),
    "floats J1-J0": ("J1", "J0"),
    "heading-fixed J1h-J1": ("J1h", "J1"),
    "heading-section Sb-S0": ("Sb", "S0"),
    "embedding Sc-Sb": ("Sc", "Sb"),
}
LIVE_REFERENCE = {"bm25": 96, "hybrid": 85, "rerank": 81, "rerank_top3": 44, "rerank_B": 21}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def doc_pmcid(meta: dict[str, Any]) -> str | None:
    pid = (meta or {}).get("pmcid")
    if pid:
        return str(pid)
    m = re.fullmatch(r"(PMC\d+)\.md", str((meta or {}).get("filename") or ""))
    return m.group(1) if m else None


# ------------------------------------------------------------------------ fetch

def cmd_fetch(args: argparse.Namespace) -> int:
    import httpx

    rows = read_jsonl(Path(args.dump))
    pmcids = sorted({p for r in rows if (p := doc_pmcid(r.get("metadata") or {}))})
    out = Path(args.jats_dir)
    out.mkdir(parents=True, exist_ok=True)
    flog = out / "fetch_log.jsonl"
    done = {json.loads(x)["pmcid"] for x in flog.read_text().splitlines()} if flog.exists() else set()
    log(f"{len(pmcids)} PMC ids; {len(done)} already attempted")
    last = 0.0
    with httpx.Client(timeout=60, follow_redirects=True) as http, flog.open("a") as fl:
        for pid in pmcids:
            dest = out / f"{pid}.xml"
            if dest.exists() or pid in done:
                continue
            wait = 1.0 - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            rec: dict[str, Any] = {"pmcid": pid, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            try:
                r = http.get(EPMC_URL.format(pmcid=pid))
                rec["status"] = r.status_code
                if r.status_code == 200 and r.content.lstrip().startswith(b"<"):
                    dest.write_bytes(r.content)
                    rec.update(ok=True, bytes=len(r.content))
                else:
                    rec["ok"] = False
            except Exception as e:  # noqa: BLE001 - a failure moves the doc to non-JATS
                rec.update(ok=False, error=type(e).__name__)
            fl.write(json.dumps(rec) + "\n")
            fl.flush()
    log(f"cached XML files: {len(list(out.glob('PMC*.xml')))} of {len(pmcids)}")
    return 0


# ------------------------------------------------------------------ JATS units

def parse_jats(path: Path):
    """Parse one untrusted JATS file, or raise ValueError."""
    from xml.etree import ElementTree as ET

    raw = path.read_bytes()
    if b"<!ENTITY" in raw:
        raise ValueError("entity declarations refused")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"parse error: {e}") from e


def section_units(root) -> list[tuple[str, str]]:
    """``(section path, text)`` units whose ``"\\n\\n"``-join reproduces
    :func:`jats.article_prose` exactly (checked by the caller).

    Each section's own text (its title line, as ``section_text`` renders it, plus
    its non-section children) is one unit; subsections are their own units. A
    section with no own text carries its title line forward onto the next unit,
    so no text is lost and no title-only chunk is made. Abstract -> "Abstract";
    body-level text outside any ``<sec>`` -> "" (no heading).
    """
    from ragstack.ingestion.jats import LIFT, article_prose, clean_itext, itext

    abstract, _ = article_prose(root)
    units: list[tuple[str, str]] = []
    if abstract:
        units.append(("Abstract", abstract))
    body = root.find(".//body")
    if body is None:
        return units
    carry: list[str] = []

    def flush(buf: list[str], path: list[str], *, only_titles_carry: bool) -> None:
        if not buf:
            return
        if only_titles_carry:
            carry.extend(buf)
        else:
            units.append((" > ".join(path), "\n\n".join(carry + buf)))
            carry.clear()
        buf.clear()

    def walk(sec, depth: int, path: list[str]) -> None:
        title_el = sec.find("title")
        t = itext(title_el) if title_el is not None else ""
        p = path + [t] if t else path
        buf: list[str] = []
        has_text = False
        if t:
            buf.append(("#" * min(depth + 2, 6)) + " " + t)
        for child in sec:
            if child.tag == "title" or child.tag in LIFT:
                continue
            if child.tag == "sec":
                flush(buf, p, only_titles_carry=not has_text)
                has_text = False
                walk(child, depth + 1, p)
            else:
                x = clean_itext(child)
                if x:
                    buf.append(x)
                    has_text = True
        flush(buf, p, only_titles_carry=not has_text)

    top: list[str] = []
    for child in body:
        if child.tag in LIFT:
            continue
        if child.tag == "sec":
            if top:
                units.append(("", "\n\n".join(carry + top)))
                carry.clear()
                top = []
            walk(child, 0, [])
        else:
            x = clean_itext(child)
            if x:
                top.append(x)
    if top:
        units.append(("", "\n\n".join(carry + top)))
        carry.clear()
    if carry:  # trailing empty sections: keep their titles rather than drop text
        units.append(("", "\n\n".join(carry)))
    return units


def float_label(root, suffix: str) -> str:
    from ragstack.ingestion.jats import collect_floats, itext

    m = re.match(r"(table|figure)-(\d+)", suffix)
    if not m:
        return ""
    tws, figs = collect_floats(root)
    els = tws if m.group(1) == "table" else figs
    i = int(m.group(2)) - 1
    lab = itext(els[i].find("label")) if 0 <= i < len(els) else ""
    return lab or f"{m.group(1).capitalize()} {i + 1}"


def jats_doc_units(root, pmcid: str, count) -> dict[str, Any]:
    """The three JATS chunkings' units for one article (texts identical across
    J1 and S0; J0 differs only in where the floats sit and that they are whole).
    """
    from ragstack.ingestion.jats import (
        DEFAULT_MIN_UNIT_CHARS,
        article_records,
        fig_units,
        table_units,
    )

    records, skipped = article_records(root, pmcid, count_tokens=count, max_tokens=UNIT_MAX_TOKENS)
    prose_recs = [r for r in records if r["metadata"]["content_type"] == "article"]
    if not prose_recs:
        raise ValueError("no prose")
    prose = prose_recs[0]["text"]
    floats = [(float_label(root, r["metadata"]["section_title"]), r["text"])
              for r in records if r["metadata"]["content_type"] != "article"]
    secs = section_units(root)
    # J0: whole floats (no row split), XML document order, same min-length floor.
    whole: list[str] = []
    ti = fi = 0
    for el in root.iter():
        if el.tag == "table-wrap":
            ti += 1
            whole += [t for _, t in table_units(el, ti, max_chars=10**9)]
        elif el.tag == "fig":
            fi += 1
            whole += [t for _, t, _ in fig_units(el, fi, max_chars=10**9)]
    whole = [t for t in whole if len(t.strip()) >= DEFAULT_MIN_UNIT_CHARS]
    j0 = "\n\n".join([prose, *whole])
    return {
        "J0": [("", j0)],
        "J1": [(None, prose)] + floats,   # None: J1h heading resolved by offset
        "S0": secs + floats,
        "sections_join_ok": "\n\n".join(t for _, t in secs) == prose,
        "n_sections": len(secs), "n_floats": len(floats),
        "skipped_units": sum(1 for s in skipped if s.get("kind") == "unit"),
    }


# ----------------------------------------------------------------- arm building

@dataclass
class C:
    """One chunk of one arm's pool."""
    id: str
    doc_id: str
    content: str
    heading: str
    glossary: str
    tokens: int = 0
    jats: bool = False
    #: Addendum A "+" glossary: rules/3 + the JATS LLM cache on JATS chunks; the
    #: live glossary (same as ``glossary``) on L and non-JATS chunks.
    glossary_plus: str = ""
    #: Addendum B: per-extractor glossary (rules/3 + that extractor's cache;
    #: live chunks re-attached from its live-text cache).
    gx: dict[str, str] = dc_field(default_factory=dict)


SEMANTIC_PARAMS = {"buffer_size": 3, "breakpoint_percentile_threshold": 80.0, "min_chunk_length": 500,
                   "max_tokens": 512, "chunk_size": 512, "chunk_overlap": 64}


def merge_siblings(secs: list[tuple[str, str]], count, limit: int = CHUNK_SIZE) -> list[tuple[str, str]]:
    """Addendum C S0m: greedily merge adjacent sibling section units (same parent
    path; never the Abstract or no-heading units) while the blank-line-joined
    text stays <= ``limit`` SFR tokens. The joined text is S0's, so the
    document text is unchanged."""
    out: list[tuple[str, str]] = []
    cur: list[Any] | None = None   # [path, text, parent, mergeable]
    for path, text in secs:
        mergeable = path not in ("", "Abstract")
        parent = path.rsplit(" > ", 1)[0] if " > " in path else ""
        if (cur is not None and cur[3] and mergeable and cur[2] == parent
                and count(cur[1] + "\n\n" + text) <= limit):
            cur[1] = cur[1] + "\n\n" + text
            continue
        if cur is not None:
            out.append((cur[0], cur[1]))
        cur = [path, text, parent, mergeable]
    if cur is not None:
        out.append((cur[0], cur[1]))
    return out


def build_arms(args: argparse.Namespace) -> tuple[dict[str, list[C]], dict[str, Any]]:
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker
    from ragstack.ingestion.symbol_glossary import (
        EXTRACTOR_VERSION,
        SymbolGlossaryAttacher,
        SymbolGlossaryCache,
        content_key,
    )
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    counter = make_token_counter("hf", model=SFR_MODEL)
    chunker = FixedTokenWindowChunker(CHUNK_SIZE, CHUNK_OVERLAP, token_counter=counter)
    attacher = SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(args.llm_cache)))
    jcache = getattr(args, "jats_llm_cache", "") or ""
    plus = SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(jcache))) if jcache else None
    gdocs_path = getattr(args, "write_glossary_docs", "") or ""
    gdocs: dict[str, str] = {}   # content_key -> text: every distinct text the attacher keys
    from ragstack.ingestion.reassembly import reassemble

    extractors: dict[str, tuple[Any, Any]] = {}
    for spec_ in getattr(args, "extractor", None) or []:
        name, caches = spec_.split("=", 1)
        live_c, jats_c = caches.split(",", 1)
        extractors[name] = (SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(live_c))),
                            SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(jats_c))))
    removed = ({x.strip() for x in Path(args.powner_plus).read_text().splitlines() if x.strip()}
               if extractors else set())

    rows = read_jsonl(Path(args.dump))
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_doc[str(r["doc_id"])].append(r)
    jdir = Path(args.jats_dir)
    st: Counter[str] = Counter()
    sem_on = bool(getattr(args, "semantic", False))
    jats_arms = ("J0", "J1", "S0") + (("JS", "SS", "S0m") if sem_on else ())
    arms: dict[str, list[C]] = {"L": [], **{a: [] for a in jats_arms}}
    units_cache: dict[str, Any] = {}
    sem_chunker = None
    if sem_on:
        # Addendum C: two passes. Record every buffer text with a stub vector
        # (buffer texts depend only on the sentence split), embed the missing
        # ones in one batch, then chunk for real from the cache.
        from ragstack.ingestion.chunkers import make_chunker

        recorded: set[str] = set()

        def rec_fn(texts):  # noqa: ANN001, ANN202
            recorded.update(texts)
            return [[1.0] for _ in texts]

        sp = SEMANTIC_PARAMS
        mk: dict[str, Any] = {**sp, "token_counter": counter}
        rec = make_chunker("semantic", embed_fn=rec_fn, **mk)
        jd = Path(args.jats_dir)
        for did in sorted(by_doc):
            pmcid = doc_pmcid(by_doc[did][0].get("metadata") or {})
            if not pmcid or not (jd / f"{pmcid}.xml").exists():
                continue
            try:
                u = jats_doc_units(parse_jats(jd / f"{pmcid}.xml"), pmcid, counter.count)
            except Exception:  # noqa: BLE001 - recorded as a failure in the main pass
                continue
            units_cache[did] = u
            rec.chunk(Document(id=did, content=u["J1"][0][1]))
            for _, t in u["S0"][: u["n_sections"]]:
                rec.chunk(Document(id=did, content=t))
        store = EmbStore(Path(args.out_dir) / "emb")
        urls = [x for x in args.embed_urls.split(",") if x]
        for u_ in urls:
            if not 9001 <= int(u_.rstrip("/").rsplit(":", 1)[1]) <= 9006:
                raise SystemExit(f"refusing embedding endpoint {u_}")
        st["semantic_buffer_texts"] = len(recorded)
        asyncio.run(embed_missing(store, sorted(recorded), urls, args.embed_model))
        st["semantic_embed_http_calls"] = EMBED_STATS["http_calls"]
        st["semantic_texts_embedded_new"] = EMBED_STATS["texts_embedded"]

        def real_fn(texts):  # noqa: ANN001, ANN202
            return store.matrix(list(texts)).tolist()

        sem_chunker = make_chunker("semantic", embed_fn=real_fn, **mk)
    failures: list[dict[str, str]] = []
    jats_docs: list[str] = []
    for did in sorted(by_doc):
        drows = sorted(by_doc[did], key=lambda r: int(r["start_char"]))
        live = [C(str(r["chunk_id"]), did, str(r["content"]), "", g := str(r.get("symbol_glossary") or ""),
                  glossary_plus=g) for r in drows]
        arms["L"].extend(live)
        if extractors:
            ltext = reassemble(did, drows).text
            kept = did not in removed
            for name, (att_l, _) in extractors.items():
                gl_ = att_l.glossary_for(ltext)
                hit_ = att_l.cache.get(content_key(ltext)) is not None
                if kept:
                    st[f"x_{name}_live_cache_hit"] += int(hit_)
                    st[f"x_{name}_live_cache_miss"] += int(not hit_)
                    st[f"x_{name}_live_entries"] += len(gl_.entries)
                    st[f"x_{name}_live_docs_with_entries"] += int(bool(gl_.entries))
                for c, r in zip(live, drows, strict=True):
                    c.gx[name] = gl_.for_chunk(c.content, att_l.config, start_char=int(r["start_char"]))
        pmcid = doc_pmcid(drows[0].get("metadata") or {})
        units = None
        if pmcid:
            xml = jdir / f"{pmcid}.xml"
            if not xml.exists():
                failures.append({"doc_id": did, "pmcid": pmcid, "stage": "fetch"})
            else:
                try:
                    units = units_cache.get(did) or jats_doc_units(parse_jats(xml), pmcid, counter.count)
                except Exception as e:  # noqa: BLE001 - doc goes to the non-JATS stratum
                    failures.append({"doc_id": did, "pmcid": pmcid, "stage": "parse",
                                     "reason": type(e).__name__})
        if units is None:
            for a in jats_arms:  # non-JATS: live chunks verbatim
                arms[a].extend(C(c.id, c.doc_id, c.content, "", c.glossary, glossary_plus=c.glossary,
                                 gx=dict(c.gx)) for c in live)
            continue
        jats_docs.append(did)
        st["sections_join_ok"] += int(units["sections_join_ok"])
        st["sections"] += units["n_sections"]
        st["float_units"] += units["n_floats"]
        st["float_units_skipped_short"] += units["skipped_units"]
        sec_starts: list[int] = []
        sec_paths: list[str] = []
        off = 0
        for path, text in units["S0"][: units["n_sections"]]:
            sec_starts.append(off)
            sec_paths.append(path)
            off += len(text) + 2
        sem_units: dict[str, set[int]] = {}
        if sem_on:
            nsec = units["n_sections"]
            units["JS"] = units["J1"]
            units["SS"] = units["S0"]
            merged = merge_siblings(units["S0"][:nsec], counter.count)
            units["S0m"] = merged + units["S0"][nsec:]
            sem_units = {"JS": {0}, "SS": set(range(nsec))}
            st["S0m_section_units"] += len(merged)
        for a in jats_arms:
            doc_text = "\n\n".join(t for _, t in units[a])
            g = attacher.glossary_for(doc_text)
            st[f"glossary_llm_cache_hit_{a}"] += int(g.version != EXTRACTOR_VERSION)
            gdocs[content_key(doc_text)] = doc_text
            gp = plus.glossary_for(doc_text) if plus is not None else g
            gxs = {}
            for name, (_, att_j) in extractors.items():
                gxs[name] = att_j.glossary_for(doc_text)
                hit_ = att_j.cache.get(content_key(doc_text)) is not None
                st[f"x_{name}_jats_cache_hit_{a}"] += int(hit_)
                st[f"x_{name}_jats_cache_miss_{a}"] += int(not hit_)
                st[f"x_{name}_jats_entries_{a}"] += len(gxs[name].entries)
                st[f"x_{name}_jats_docs_with_entries_{a}"] += int(bool(gxs[name].entries))
            if plus is not None:
                hit_ = plus.cache.get(content_key(doc_text)) is not None  # type: ignore[union-attr]
                st[f"plus_cache_hit_{a}"] += int(hit_)
                st[f"plus_cache_miss_{a}"] += int(not hit_)
                st[f"plus_llm_version_used_{a}"] += int(gp.version != EXTRACTOR_VERSION)
                st[f"plus_entries_{a}"] += len(gp.entries)
                st[f"rules_entries_{a}"] += len(g.entries)
                st[f"plus_docs_with_entries_{a}"] += int(bool(gp.entries))
                st[f"rules_docs_with_entries_{a}"] += int(bool(g.entries))
            off = 0
            n = 0
            for ui, (head, text) in enumerate(units[a]):
                ck = sem_chunker if ui in sem_units.get(a, ()) else chunker
                for ch in ck.chunk(Document(id=did, content=text)):  # type: ignore[union-attr]
                    gstart = off + ch.start_char
                    if head is None:  # J1 prose: section path at the chunk's start
                        k = bisect.bisect_right(sec_starts, ch.start_char) - 1
                        h = sec_paths[k] if k >= 0 and units["sections_join_ok"] else ""
                    else:
                        h = head
                    arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, h,
                                     g.for_chunk(ch.content, attacher.config, start_char=gstart),
                                     jats=True,
                                     glossary_plus=gp.for_chunk(ch.content, attacher.config,
                                                                start_char=gstart),
                                     gx={nm_: gg.for_chunk(ch.content, attacher.config, start_char=gstart)
                                         for nm_, gg in gxs.items()}))
                    n += 1
                off += len(text) + 2
    st["jats_docs"] = len(jats_docs)
    st["pmc_docs"] = sum(1 for d in by_doc if doc_pmcid(by_doc[d][0].get("metadata") or {}))
    st["docs"] = len(by_doc)
    # SFR token counts, batched through the fast tokenizer.
    tok = counter._tokenizer()
    for cs in arms.values():
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)
    jset = set(jats_docs)
    for c in arms["L"]:
        c.jats = c.doc_id in jset
    st["llm_cache_records"] = len(attacher.cache) if attacher.cache is not None else 0
    if gdocs_path:
        # JsonlLoader shape; Document.content == text, so the LLM script's
        # content_key equals the attacher's. PRIVATE: document text.
        with open(gdocs_path, "w", encoding="utf-8") as fh:
            for k, t in sorted(gdocs.items()):
                fh.write(json.dumps({"text": t, "path": f"jats-glossary-doc-{k[:16]}", "metadata": {}},
                                    ensure_ascii=False) + "\n")
        os.chmod(gdocs_path, 0o640)
        st["glossary_docs_written"] = len(gdocs)
    return arms, {"stats": dict(st), "failures": failures, "jats_docs": jats_docs}


def save_arms(arms: dict[str, list[C]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for a, cs in arms.items():
            for c in cs:
                fh.write(json.dumps({"arm": a, **c.__dict__}, ensure_ascii=False) + "\n")
    os.chmod(path, 0o640)


def load_arms(path: Path) -> dict[str, list[C]]:
    arms: dict[str, list[C]] = defaultdict(list)
    for x in read_jsonl(path):
        a = x.pop("arm")
        arms[a].append(C(**x))
    return dict(arms)


# ----------------------------------------------------------------------- gold

def norm(text: str) -> str:
    """The gold normalisation rule: NFKC, casefold, every whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def fold(text: str) -> str:
    """Sensitivity only (not the prereg rule): the JATS parser's own lookalike
    folding (``jats.norm``: dashes, quotes, micro sign, zero-width) applied
    before :func:`norm`, so a quote copied with an en dash still matches JATS
    text whose dash the parser folded to '-'."""
    from ragstack.ingestion.jats import _TRANS, _ZERO_WIDTH

    return norm(text.translate(_ZERO_WIDTH).translate(_TRANS))


def fuzzy(text: str) -> str:
    """dedup_quote_note variant: letters, digits and '=' only."""
    return "".join(ch for ch in norm(text) if ch.isalnum() or ch == "=")


def hit(q: str, t: str) -> str | None:
    """'full' / 'partial' (longest quote prefix or suffix in the chunk >= 60%) / None."""
    if not q:
        return None
    if q in t:
        return "full"
    m = math.ceil(0.6 * len(q))
    if q[:m] in t or q[-m:] in t:
        return "partial"
    return None


class Gold:
    def __init__(self, records: list[dict[str, Any]], pairs: dict[str, dict[str, Any]],
                 normalise=norm) -> None:
        self.records = records
        self.normalise = normalise
        self.q = {r["record_id"]: normalise(str(pairs[r["pair"]]["expected"]["quote"])) for r in records}
        self.qf = {r["record_id"]: fuzzy(str(pairs[r["pair"]]["expected"]["quote"])) for r in records}
        self._t: dict[str, str] = {}
        self._tf: dict[str, str] = {}
        self._memo: dict[tuple[str, str, bool], str | None] = {}

    def text(self, c: C) -> str:
        t = self._t.get(c.id)
        if t is None:
            t = self._t[c.id] = self.normalise(c.content)
        return t

    def check(self, rid: str, c: C, fuzzy_: bool = False) -> str | None:
        key = (rid, c.id, fuzzy_)
        if key not in self._memo:
            if fuzzy_:
                t = self._tf.get(c.id) or self._tf.setdefault(c.id, fuzzy(c.content))
                self._memo[key] = hit(self.qf[rid], t)
            else:
                self._memo[key] = hit(self.q[rid], self.text(c))
        return self._memo[key]


# ----------------------------------------------------------------------- BM25

_WORD = re.compile(r"[\ẁ-ͯ⃐-⃿]+")
_MIDLETTER = set(".'’:·")
_MIDNUM = set(".,;")


def analyze(text: str) -> list[str]:
    """Standard-analyzer approximation: UAX#29 word runs (letters/digits/_ and
    combining marks; '.', apostrophe, ':' join letters; '.', ',', ';' join
    digits), lowercased, no stop words."""
    out: list[str] = []
    prev_end = -2
    for m in _WORD.finditer(text):
        s, e = m.span()
        tok = m.group()
        if out and s == prev_end + 1:
            sep = text[prev_end]
            left, right = out[-1][-1], tok[0]
            if (sep in _MIDLETTER and left.isalpha() and right.isalpha()) or \
               (sep in _MIDNUM and left.isdigit() and right.isdigit()):
                out[-1] = out[-1] + sep + tok.lower()
                prev_end = e
                continue
        out.append(tok.lower())
        prev_end = e
    return out


def _byte4(i: int) -> int:
    """Lucene SmallFloat.intToByte4 then byte4ToInt: the lossy length norm."""
    if i < 24:
        return i
    v = i - 24
    nbits = v.bit_length()
    if nbits >= 4:
        shift = nbits - 4
        enc = ((v >> shift) & 0x07) | ((shift + 1) << 3)
    else:
        enc = v
    bits, sh = enc & 0x07, (enc >> 3) - 1
    return 24 + (bits if sh == -1 else (bits | 0x08) << sh)


class Field:
    def __init__(self, texts: list[str], norms: bool) -> None:
        post: dict[str, dict[int, int]] = defaultdict(dict)
        dl = np.zeros(len(texts), dtype=np.float64)
        total = 0
        ndocs = 0
        for i, t in enumerate(texts):
            toks = analyze(t) if t else []
            if not toks:
                continue
            ndocs += 1
            total += len(toks)
            dl[i] = _byte4(len(toks)) if norms else 1.0
            for w in toks:
                post[w][i] = post[w].get(i, 0) + 1
        self.n = ndocs
        avgdl = total / ndocs if ndocs else 1.0
        self.denom_norm = K1 * (1 - B + B * dl / avgdl)
        self.post = {w: (np.fromiter(d.keys(), dtype=np.int64), np.fromiter(d.values(), dtype=np.float64))
                     for w, d in post.items()}

    def score(self, terms: list[str], out: np.ndarray, boost: float) -> None:
        for w in terms:  # duplicates add, as Lucene's per-term SHOULD clauses do
            p = self.post.get(w)
            if p is None:
                continue
            idx, tf = p
            idf = math.log(1 + (self.n - len(idx) + 0.5) / (len(idx) + 0.5))
            out[idx] += boost * idf * tf / (tf + self.denom_norm[idx])


def gmode(g: bool | str) -> str:
    """Glossary state: 'off', 'rules' (the first trial's "on": live glossary on L
    and non-JATS chunks, rules/3 on JATS chunks) or 'plus' (addendum A)."""
    if isinstance(g, str):
        return g
    return "rules" if g else "off"


def glossary_text(c: C, gm: str) -> str:
    if gm.startswith("x:"):
        return c.gx.get(gm[2:], "")
    return "" if gm == "off" else (c.glossary_plus if gm == "plus" else c.glossary)


def bm25_content(c: C, prepend: bool) -> str:
    return f"{c.heading}\n{c.content}" if prepend and c.heading else c.content


class BM25:
    """One pool's BM25 fields, built lazily and shared across configs:
    ``content`` (optionally heading-prepended), ``symbol_glossary`` per glossary
    state (norms:false, as the store maps it), ``heading``."""

    def __init__(self, chunks: list[C], idx: list[int]) -> None:
        self.chunks, self.map = chunks, idx
        self._f: dict[tuple[str, Any], Field] = {}

    def field(self, kind: str, arg: Any) -> Field:
        if (kind, arg) not in self._f:
            if kind == "content":
                self._f[(kind, arg)] = Field([bm25_content(c, arg) for c in self.chunks], norms=True)
            elif kind == "gloss":
                self._f[(kind, arg)] = Field([glossary_text(c, arg) for c in self.chunks], norms=False)
            else:
                self._f[(kind, arg)] = Field([c.heading for c in self.chunks], norms=True)
        return self._f[(kind, arg)]

    def top(self, query: str, gm: str, heading_boost: float, prepend: bool, depth: int) -> list[int]:
        terms = analyze(query)
        s = np.zeros(len(self.chunks))
        self.field("content", prepend).score(terms, s, 1.0)
        if gm != "off":
            self.field("gloss", gm).score(terms, s, GLOSSARY_BOOST)
        if heading_boost:
            self.field("head", None).score(terms, s, heading_boost)
        nz = np.flatnonzero(s > 0)
        order = nz[np.lexsort((nz, -s[nz]))]   # score desc, index order on ties
        return [self.map[i] for i in order[:depth]]


# ---------------------------------------------------------------------- dense

class EmbStore:
    """Append-only float32 vector cache keyed by sha256(text) (no text stored)."""

    def __init__(self, d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
        self.vec_path, self.idx_path = d / "vectors.f32", d / "index.jsonl"
        self.row: dict[str, int] = {}
        self.dim = 0
        if self.idx_path.exists():
            for x in read_jsonl(self.idx_path):
                self.row[x["k"]] = x["r"]
                self.dim = x["d"]
        self._mm: np.ndarray | None = None

    def missing(self, texts: list[str]) -> list[str]:
        seen: set[str] = set()
        out = []
        for t in texts:
            k = sha(t)
            if k not in self.row and k not in seen:
                seen.add(k)
                out.append(t)
        return out

    def add(self, texts: list[str], vecs: list[list[float]]) -> None:
        arr = np.asarray(vecs, dtype=np.float32)
        self.dim = arr.shape[1]
        with self.vec_path.open("ab") as fv, self.idx_path.open("a") as fi:
            base = fv.tell() // (4 * self.dim)
            fv.write(arr.tobytes())
            for j, t in enumerate(texts):
                self.row[sha(t)] = base + j
                fi.write(json.dumps({"k": sha(t), "r": base + j, "d": self.dim}) + "\n")
        self._mm = None

    def matrix(self, texts: list[str]) -> np.ndarray:
        if self._mm is None:
            self._mm = np.fromfile(self.vec_path, dtype=np.float32).reshape(-1, self.dim)
        m = self._mm[[self.row[sha(t)] for t in texts]].astype(np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
        return m


EMBED_STATS: Counter[str] = Counter()


async def embed_missing(store: EmbStore, texts: list[str], urls: list[str], model: str,
                        batch: int = 32) -> None:
    import httpx

    todo = store.missing(texts)
    if not todo:
        return
    log(f"embedding {len(todo)} texts on {len(urls)} endpoints")
    sem = {u: asyncio.Semaphore(MAX_EMBED_INFLIGHT_PER_ENDPOINT) for u in urls}
    lock = asyncio.Lock()
    done = 0
    async with httpx.AsyncClient(timeout=600) as http:
        async def one(i0: int, url: str) -> None:
            nonlocal done
            part = todo[i0:i0 + batch]
            async with sem[url]:
                for attempt in range(4):
                    try:
                        r = await http.post(f"{url.rstrip('/')}/v1/embeddings",
                                            json={"model": model, "input": part})
                        r.raise_for_status()
                        EMBED_STATS["http_calls"] += 1
                        data = sorted(r.json()["data"], key=lambda d: d["index"])
                        break
                    except Exception:  # noqa: BLE001
                        if attempt == 3:
                            raise
                        await asyncio.sleep(3 * (attempt + 1))
            async with lock:
                EMBED_STATS["texts_embedded"] += len(part)
                store.add(part, [d["embedding"] for d in data])
                done += len(part)
                if done % 2048 < batch:
                    log(f"  embedded {done}/{len(todo)}")

        await asyncio.gather(*(one(i, urls[(i // batch) % len(urls)]) for i in range(0, len(todo), batch)))


def dense_top(mat: np.ndarray, qv: np.ndarray, allowed: np.ndarray, depth: int) -> list[int]:
    s = mat @ qv
    s = np.where(allowed, s, -np.inf)
    cand = np.argpartition(-s, depth)[: depth * 2] if len(s) > depth * 2 else np.arange(len(s))
    cand = cand[np.isfinite(s[cand])]
    order = cand[np.lexsort((cand, -s[cand]))]
    return order[:depth].tolist()


class QuantIndex:
    """Addendum D dense storage variants over L2-normalised SFR vectors.

    * ``int8``: per-dimension scalar quantization to [-127, 127], clipped at the
      pool's 0.5% / 99.5% quantiles per dimension; score = dot product of the
      dequantised pool and query vectors.
    * ``bin``: one sign bit per dimension (1 if > 0); score = agreeing bits,
      i.e. 4,096 - Hamming distance (computed as a +-1 dot product, rank-equal).
    * ``+r``: quantized top ``3 x depth`` (oversampling 3), rescored by exact
      f32 cosine, keep ``depth``.

    Ties break by pool index (the f32 leg's rule), over a full sort of the pool.
    """

    def __init__(self, mat: np.ndarray, allowed: np.ndarray, mode: str) -> None:
        self.mat, self.allowed = mat, allowed
        self.kind, self.rescore = mode.split("+")[0], mode.endswith("+r")
        if self.kind not in ("int8", "bin"):
            raise ValueError(f"unknown dense mode {mode}")
        self.cand = np.flatnonzero(allowed)
        if self.kind == "int8":
            sub = mat[self.cand]
            self.lo = np.quantile(sub, 0.005, axis=0).astype(np.float32)
            self.hi = np.quantile(sub, 0.995, axis=0).astype(np.float32)
            self.span = np.maximum(self.hi - self.lo, 1e-12)
            self.qc = self._deq(sub)
        else:
            self.qc = np.where(mat[self.cand] > 0, 1.0, -1.0).astype(np.float32)

    def _deq(self, x: np.ndarray) -> np.ndarray:
        code = np.rint((np.clip(x, self.lo, self.hi) - self.lo) / self.span * 254.0) - 127.0
        return (self.lo + (code + 127.0) * self.span / 254.0).astype(np.float32)

    def top(self, q: np.ndarray, depth: int) -> list[int]:
        qv = self._deq(q[None, :])[0] if self.kind == "int8" else np.where(q > 0, 1.0, -1.0).astype(np.float32)
        s = self.qc @ qv
        order = self.cand[np.lexsort((self.cand, -s))]
        if not self.rescore:
            return order[:depth].tolist()
        top = order[: depth * 3]
        s2 = self.mat[top] @ q
        return top[np.lexsort((top, -s2))][:depth].tolist()


# --------------------------------------------------------------------- rerank

class Reranker:
    """Per-(query, passage) score cache over the sidecar. A cross-encoder score
    is pointwise, so only uncached passages are sent; order = score desc,
    stable on the pool order (the sidecar's own sort)."""

    def __init__(self, url: str, cache_path: Path) -> None:
        self.url = url.rstrip("/")
        self.path = cache_path
        self.s: dict[str, float] = {}
        if cache_path.exists():
            for x in read_jsonl(cache_path):
                self.s[x["k"]] = x["s"]
        self.calls = 0

    @staticmethod
    def key(q: str, p: str) -> str:
        return sha(q + "\x00" + p)

    async def fill(self, jobs: list[tuple[str, list[str]]]) -> None:
        import httpx

        need: dict[str, list[str]] = defaultdict(list)
        for q, ps in jobs:
            for p in ps:
                k = self.key(q, p)
                if k not in self.s and p not in need[q]:
                    need[q].append(p)
        work = [(q, ps[i:i + 64]) for q, ps in need.items() for i in range(0, len(ps), 64)]
        if not work:
            return
        log(f"reranking {sum(len(p) for _, p in work)} new (query, passage) pairs in {len(work)} calls")
        sem = asyncio.Semaphore(MAX_RERANK_INFLIGHT)
        async with httpx.AsyncClient(timeout=300) as http:
            with self.path.open("a") as fh:
                async def one(q: str, ps: list[str]) -> None:
                    async with sem:
                        for attempt in range(4):
                            try:
                                r = await http.post(f"{self.url}/rerank",
                                                    json={"query": q, "documents": ps, "top_k": len(ps)})
                                r.raise_for_status()
                                d = r.json()
                                break
                            except Exception:  # noqa: BLE001
                                if attempt == 3:
                                    raise
                                await asyncio.sleep(3 * (attempt + 1))
                    self.calls += 1
                    for i, sc in zip(d["indices"], d["scores"], strict=True):
                        k = self.key(q, ps[i])
                        self.s[k] = float(sc)
                        fh.write(json.dumps({"k": k, "s": float(sc)}) + "\n")

                await asyncio.gather(*(one(q, ps) for q, ps in work))

    def order(self, q: str, ps: list[str]) -> list[int]:
        sc = [self.s[self.key(q, p)] for p in ps]
        return sorted(range(len(ps)), key=lambda i: -sc[i])


# ------------------------------------------------------------------ pipeline

def rrf(dense: list[int], bm: list[int]) -> list[int]:
    score: dict[int, float] = {}
    for lst in (dense, bm):   # the live retriever's order: vector leg first
        for r, i in enumerate(lst):
            score[i] = score.get(i, 0.0) + 1.0 / (RRF_K + r + 1)
    return sorted(score, key=lambda i: -score[i])   # stable on insertion order


def cap(lst: list[int], docs: list[str], k: int) -> list[int]:
    if k <= 0:
        return lst
    seen: dict[str, int] = defaultdict(int)
    keep, over = [], []
    for i in lst:
        seen[docs[i]] += 1
        (keep if seen[docs[i]] <= k else over).append(i)
    return keep + over


def passage(c: C, gm: str, heading: bool) -> str:
    p = c.content
    if heading and c.heading:
        p = f"{c.heading}\n{p}"
    g = glossary_text(c, gm)
    if g:
        p = f"{p}\n{g}"
    return p


def embed_text(c: C, heading: bool) -> str:
    return f"{c.heading}\n{c.content}" if heading and c.heading else c.content


@dataclass
class Setup:
    arms: dict[str, list[C]]
    pools: dict[str, set[str]]           # pool -> removed doc_ids
    records: list[dict[str, Any]]
    gold: Gold
    gold_fold: Gold
    rec_paper: dict[str, str]
    jats_docs: set[str]


async def run_configs(su: Setup, configs: list[tuple[str, Any, int, str, float]],
                      args: argparse.Namespace,
                      specs: dict[str, ArmSpec] = ARM_SPEC) -> dict[str, dict[str, Any]]:
    """configs: (arm, glossary state, cap, pool, heading-boost factor). Returns
    per-config per-record ranks."""
    configs = [(a, gmode(g), k, p, hb) for a, g, k, p, hb in configs]
    ARM = specs
    urls = [u for u in args.embed_urls.split(",") if u]
    for u in urls:
        port = int(u.rstrip("/").rsplit(":", 1)[1])
        if not 9001 <= port <= 9006:
            raise SystemExit(f"refusing embedding endpoint {u}: only :9001-:9006 are allowed")
    out_dir = Path(args.out_dir)
    store = EmbStore(out_dir / "emb")
    rr = Reranker(args.rerank_url, out_dir / "rerank_cache.jsonl")
    queries = [r["query"] for r in su.records]

    # Embeddings: queries + every embedding text the configs need.
    need_sets = {(ARM[a].base, ARM[a].h_embed) for a, *_ in configs}
    texts = list(queries)
    for base, he in need_sets:
        texts += [embed_text(c, he) for c in su.arms[base]]
    await embed_missing(store, texts, urls, args.embed_model)
    qmat = store.matrix(queries)
    mats = {(b, he): store.matrix([embed_text(c, he) for c in su.arms[b]]) for b, he in need_sets}

    bm25: dict[tuple[str, str], BM25] = {}
    quant: dict[tuple[str, bool, str, str], QuantIndex] = {}
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, tuple, list[list[int]], list[list[int]], list[list[int]]]] = []
    for arm, gm, k, pool, hb in configs:
        spec = ARM[arm]
        cs = su.arms[spec.base]
        docs = [c.doc_id for c in cs]
        allowed = np.array([c.doc_id not in su.pools[pool] for c in cs])
        if (spec.base, pool) not in bm25:
            idx = [i for i in range(len(cs)) if allowed[i]]
            bm25[(spec.base, pool)] = BM25([cs[i] for i in idx], idx)
        index = bm25[(spec.base, pool)]
        hboost = spec.h_boost * hb if spec.h_bm25 else 0.0
        mat = mats[(spec.base, spec.h_embed)]
        qz = None
        if spec.dense != "f32":
            qk = (spec.base, spec.h_embed, pool, spec.dense)
            if qk not in quant:
                quant[qk] = QuantIndex(mat, allowed, spec.dense)
            qz = quant[qk]
        bm_l, hy_l, pre = [], [], []
        for qi, q in enumerate(queries):
            b = index.top(q, gm, hboost, spec.prepend, DEEP)
            d = dense_top(mat, qmat[qi], allowed, DEEP) if qz is None else qz.top(qmat[qi], DEEP)
            bm_l.append(cap(b[:SHALLOW], docs, k)[:TOP_K])
            hy_l.append(cap(rrf(d[:SHALLOW], b[:SHALLOW]), docs, k)[:TOP_K])
            pre.append(cap(rrf(d, b), docs, k)[:RERANK_POOL])
        pending.append((arm, (arm, gm, k, pool, hb), bm_l, hy_l, pre))

    jobs = []
    for arm, key, _, _, pre in pending:
        spec = ARM[arm]
        cs = su.arms[spec.base]
        jobs += [(queries[qi], [passage(cs[i], key[1], spec.h_rerank) for i in p]) for qi, p in enumerate(pre)]
    await rr.fill(jobs)

    for arm, key, bm_l, hy_l, pre in pending:
        spec = ARM[arm]
        cs = su.arms[spec.base]
        docs = [c.doc_id for c in cs]
        rr_l = []
        for qi, p in enumerate(pre):
            o = rr.order(queries[qi], [passage(cs[i], key[1], spec.h_rerank) for i in p])
            rr_l.append(cap([p[j] for j in o], docs, key[2]))
        results[config_name(*key)] = score_config(su, cs, bm_l, hy_l, rr_l)
    log(f"reranker calls this run: {rr.calls}")
    EMBED_STATS["rerank_calls"] += rr.calls
    return results


def config_name(arm: str, gloss: Any, k: int, pool: str, hb: float) -> str:
    gm = gmode(gloss)
    g = {"rules": "on", "off": "off", "plus": "plus"}.get(gm, gm)
    return f"{arm}|g{g}|cap{k}|{pool}" + (f"|hb{hb:g}" if hb != 1.0 else "")


def score_config(su: Setup, cs: list[C], bm_l, hy_l, rr_l) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    for qi, rec in enumerate(su.records):
        rid = rec["record_id"]

        def first(lst: list[int], fuzzy_: bool = False, g: Gold = su.gold) -> tuple[int | None, int | None]:
            any_r = full_r = None
            for r, i in enumerate(lst, 1):
                h = g.check(rid, cs[i], fuzzy_)  # noqa: B023 - called within the iteration
                if h and any_r is None:
                    any_r = r
                if h == "full" and full_r is None:
                    full_r = r
            return any_r, full_r

        rr10 = rr_l[qi][:TOP_K]
        a, f = first(rr10)
        b_a, b_f = first(bm_l[qi])
        h_a, h_f = first(hy_l[qi])
        fz, _ = first(rr10, True)
        fo, _ = first(rr10, g=su.gold_fold)
        gid = set(rec.get("gold_chunk_ids") or [])

        def by_id(lst: list[int]) -> int | None:
            # Fidelity only: the live reference counts the record's gold_chunk_ids.
            return next((r for r, i in enumerate(lst, 1) if cs[i].id in gid), None)  # noqa: B023

        budget = {}
        for bud in BUDGETS:
            tot, got = 0, False
            for i in rr_l[qi]:
                tot += cs[i].tokens
                if tot > bud:
                    break
                if su.gold.check(rid, cs[i]):
                    got = True
                    break
            budget[bud] = got
        per[rid] = {"rerank": a, "rerank_full": f, "bm25": b_a, "bm25_full": b_f,
                    "hybrid": h_a, "hybrid_full": h_f, "rerank_fuzzy": fz, "rerank_fold": fo,
                    "id_rerank": by_id(rr10), "id_bm25": by_id(bm_l[qi]), "id_hybrid": by_id(hy_l[qi]),
                    "b5120": budget[5120], "b2048": budget[2048],
                    "docs10": len({cs[i].doc_id for i in rr10}),
                    "top10": [cs[i].id for i in rr10]}
    return per


# -------------------------------------------------------------------- summary

def found(r: dict[str, Any], leg: str, top: int = TOP_K) -> bool:
    v = r.get(leg)
    if leg.startswith("b") and leg[1:].isdigit():
        return bool(v)
    return v is not None and v <= top


def summarize(per: dict[str, dict[str, Any]], su: Setup) -> dict[str, Any]:
    recs = su.records
    kinds = sorted({r["kind"] for r in recs})

    def cnt(leg: str, sel=None, top: int = TOP_K) -> int:
        return sum(1 for r in recs if (sel is None or sel(r)) and found(per[r["record_id"]], leg, top))

    out = {
        "rerank": cnt("rerank"), "rerank_top3": cnt("rerank", top=3),
        "rerank_full": cnt("rerank_full"), "bm25": cnt("bm25"), "bm25_full": cnt("bm25_full"),
        "hybrid": cnt("hybrid"), "hybrid_full": cnt("hybrid_full"),
        "budget5120": cnt("b5120"), "budget2048": cnt("b2048"),
        "rerank_fold_sensitivity": cnt("rerank_fold"),
        "fidelity_gold_ids": {"rerank": cnt("id_rerank"), "rerank_top3": cnt("id_rerank", top=3),
                              "rerank_B": cnt("id_rerank", lambda r: r["kind"] == "B"),
                              "bm25": cnt("id_bm25"), "hybrid": cnt("id_hybrid")},
        "rerank_kind": {k: cnt("rerank", lambda r, k=k: r["kind"] == k) for k in kinds},
        "rerank_bazant": cnt("rerank", lambda r: su.rec_paper[r["record_id"]] == BAZANT_PMCID),
        "rerank_other_papers": cnt("rerank", lambda r: su.rec_paper[r["record_id"]] != BAZANT_PMCID),
        "mean_distinct_docs_top10": round(statistics.mean(per[r["record_id"]]["docs10"] for r in recs), 3),
    }
    out["rerank_B"] = out["rerank_kind"].get("B", 0)
    return out


def mcnemar_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def contrast(arm_per, ref_per, su: Setup, leg: str = "rerank") -> dict[str, Any]:
    gained, lost = [], []
    for r in su.records:
        rid = r["record_id"]
        a, b = found(arm_per[rid], leg), found(ref_per[rid], leg)
        if a and not b:
            gained.append(rid)
        elif b and not a:
            lost.append(rid)

    def net(sel) -> int:
        return sum(1 for x in gained if sel(x)) - sum(1 for x in lost if sel(x))

    return {"gained": len(gained), "lost": len(lost), "net": len(gained) - len(lost),
            "mcnemar_p": round(mcnemar_p(len(gained), len(lost)), 4),
            "net_bazant": net(lambda x: su.rec_paper[x] == BAZANT_PMCID),
            "net_other_papers": net(lambda x: su.rec_paper[x] != BAZANT_PMCID),
            "gained_ids": gained, "lost_ids": lost}


# ------------------------------------------------------------------- loading

def load_setup(args: argparse.Namespace) -> Setup:
    d = Path(args.out_dir)
    meta = json.loads((d / "arms_meta.json").read_text())
    arms = load_arms(d / "arms_chunks.jsonl")
    records = read_jsonl(Path(args.records))
    pairs = {p["id"]: p for p in read_jsonl(Path(args.pairs))}
    pools = {"P-live": set()}
    for name, f in (("P-owner", args.powner), ("P-owner+", args.powner_plus)):
        ids = {x.strip() for x in Path(f).read_text().splitlines() if x.strip() and not x.startswith("#")}
        known = {c.doc_id for c in arms["L"]}
        if not ids <= known:
            raise SystemExit(f"{name}: {len(ids - known)} doc_id(s) not in the pool")
        pools[name] = ids
    return Setup(arms=arms, pools=pools, records=records, gold=Gold(records, pairs),
                 gold_fold=Gold(records, pairs, fold),
                 rec_paper={r["record_id"]: str((pairs[r["pair"]].get("paper") or {}).get("pmcid") or "")
                            for r in records},
                 jats_docs=set(meta["jats_docs"]))


# ---------------------------------------------------------------- subcommands

def cmd_build(args: argparse.Namespace) -> int:
    d = Path(args.out_dir)
    arms, meta = build_arms(args)
    save_arms(arms, d / "arms_chunks.jsonl")
    (d / "arms_meta.json").write_text(json.dumps(meta, indent=1))
    su = load_setup(args)
    desc: dict[str, Any] = {"build": meta["stats"], "failures": meta["failures"], "arms": {}}
    for a, cs in su.arms.items():
        def dstats(sel: list[C]) -> dict[str, Any]:
            t = sorted(c.tokens for c in sel)
            if not t:
                return {"chunks": 0}
            return {"chunks": len(t), "median_tokens": statistics.median(t),
                    "p90_tokens": t[min(len(t) - 1, int(math.ceil(0.9 * len(t))) - 1)],
                    "share_under_128": round(sum(x < 128 for x in t) / len(t), 4),
                    "with_glossary": sum(1 for c in sel if c.glossary),
                    "with_heading": sum(1 for c in sel if c.heading)}
        jsel = [c for c in cs if c.doc_id in su.jats_docs]
        # Attainability: records whose gold is in ANY chunk of the arm's pool.
        att_full = att_any = 0
        nonjats_only = []
        for r in su.records:
            q = su.gold.q[r["record_id"]]
            hits = [(c, hit(q, su.gold.text(c))) for c in cs]
            hs = [(c, h) for c, h in hits if h]
            att_any += bool(hs)
            att_full += any(h == "full" for _, h in hs)
            if a == "L" and hs and all(c.doc_id not in su.jats_docs for c, _ in hs):
                nonjats_only.append(r["record_id"])
        att_fold = sum(1 for r in su.records
                       if any(hit(su.gold_fold.q[r["record_id"]], su.gold_fold.text(c)) for c in cs))
        desc["arms"][a] = {"all": dstats(cs), "jats_docs": dstats(jsel),
                           "records_attainable_any": att_any, "records_attainable_full": att_full,
                           "records_attainable_any_fold_sensitivity": att_fold}
        if a == "L":
            desc["records_gold_only_in_nonjats_docs"] = nonjats_only
    (d / "descriptives-261022.json").write_text(json.dumps(desc, indent=1))
    for a, x in desc["arms"].items():
        log(f"{a}: {x['all']} jats={x['jats_docs'].get('chunks')} attain any/full "
            f"{x['records_attainable_any']}/{x['records_attainable_full']}")
    log(f"build stats: {meta['stats']}; failures: {len(meta['failures'])}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    su = load_setup(args)
    configs = [("L", g, k, "P-live", 1.0) for g in (True, False) for k in (0, 2)]
    res = asyncio.run(run_configs(su, configs, args))
    out = {"live_reference": LIVE_REFERENCE,
           "L": {n: summarize(p, su) for n, p in res.items()},
           "per_record": {n: {rid: {k: v for k, v in x.items() if k != "top10"} for rid, x in p.items()}
                          for n, p in res.items()}}
    Path(args.out_dir, "calibration-L-261022.json").write_text(json.dumps(out, indent=1))
    for n, s in out["L"].items():
        log(f"{n}: bm25 {s['bm25']} hybrid {s['hybrid']} rerank {s['rerank']} "
            f"top3 {s['rerank_top3']} B {s['rerank_B']} | by gold ids: {s['fidelity_gold_ids']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    su = load_setup(args)
    configs = [(a, g, k, p, 1.0) for a in ARMS for g in (True, False) for k in (0, 2) for p in POOLS]
    configs += [(a, g, k, p, 2.0) for a in HEADING_ARMS for g in (True, False) for k in (0, 2) for p in POOLS]
    res = asyncio.run(run_configs(su, configs, args))
    pc, primary_pool = args.primary_cap, args.primary_pool
    summary = {n: summarize(p, su) for n, p in res.items()}

    def nm(arm: str, pool: str, gloss: bool = True, k: int = pc, hb: float = 1.0) -> str:
        return config_name(arm, gloss, k, pool, hb)

    contrasts: dict[str, Any] = {}
    for pool in POOLS:
        for gloss in (True, False):
            for k in (0, 2):
                tag = f"{pool}|g{'on' if gloss else 'off'}|cap{k}"
                block: dict[str, Any] = {}
                for cname, (a, ref) in CONTRASTS.items():
                    block[cname] = {leg: contrast(res[nm(a, pool, gloss, k)], res[nm(ref, pool, gloss, k)], su, leg)
                                    for leg in ("rerank", "b5120", "b2048", "bm25", "hybrid")}
                for a in ARMS:
                    for ref in ("J1", "L"):
                        if a != ref:
                            block[f"{a} vs {ref}"] = {"rerank": contrast(res[nm(a, pool, gloss, k)],
                                                                         res[nm(ref, pool, gloss, k)], su)}
                contrasts[tag] = block

    def verdict(pool: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for a in ("Sb", "Sc"):
            c = contrasts[f"{pool}|gon|cap{pc}"][f"{a}-J1" if a == "Sc" else "primary Sb-J1"]
            r1 = c["rerank"]["net"] >= 5
            r2 = c["b5120"]["net"] >= 0
            r3 = c["rerank"]["net_other_papers"] >= 1
            out[a] = {"net_primary": c["rerank"]["net"], "net_budget5120": c["b5120"]["net"],
                      "net_other_papers": c["rerank"]["net_other_papers"],
                      "rule1_net_ge_5": r1, "rule2_no_loss_at_5120": r2, "rule3_gain_outside_bazant": r3,
                      "passes": r1 and r2 and r3}
        out["section_aware_looks_worth_building"] = any(v["passes"] for v in out.values())
        return out

    # Strata: dedup_quote_note pairs, kinds, per paper, non-JATS-only gold.
    pairs = {p["id"]: p for p in read_jsonl(Path(args.pairs))}
    dq = [r["record_id"] for r in su.records if pairs[r["pair"]].get("dedup_quote_note")]
    strata: dict[str, Any] = {"dedup_quote_note_records": len(dq), "per_config": {}}
    for n, per in res.items():
        strata["per_config"][n] = {
            "dedup_note_found": sum(1 for x in dq if found(per[x], "rerank")),
            "dedup_note_fuzzy_found": sum(1 for x in dq if found(per[x], "rerank_fuzzy")),
        }
    report = {
        "prereg_sha256": args.prereg_sha,
        "primary": {"cap": pc, "pool": primary_pool, "glossary": "on", "endpoint": "rerank recall@10 (found)"},
        "records": len(su.records),
        "pools_removed": {p: sorted(x) for p, x in su.pools.items()},
        "summary": summary,
        "contrasts": contrasts,
        "verdict": {p: verdict(p) for p in POOLS},
        "strata": strata,
        "per_record_ranks": {n: {rid: {k: v for k, v in x.items() if k != "top10"} for rid, x in per.items()}
                             for n, per in res.items()},
    }
    path = Path(args.out_dir, "results-261022.json")
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    for a in ARMS:
        s = summary[nm(a, primary_pool)]
        log(f"{a}: rerank {s['rerank']} bm25 {s['bm25']} hybrid {s['hybrid']} top3 {s['rerank_top3']} "
            f"B {s['rerank_B']} b5120 {s['budget5120']} b2048 {s['budget2048']} full {s['rerank_full']}")
    log(f"verdict ({primary_pool}): {report['verdict'][primary_pool]['section_aware_looks_worth_building']}")
    return 0


# ------------------------------------------------- addendum B: LLM extractor run

QWEN_VARIANTS = {
    # name -> (extra request fields, max_tokens)
    "qwen-nothink": ({"chat_template_kwargs": {"enable_thinking": False}}, 2048),
    "qwen-think": ({}, 8192),
    "coder-next": ({}, 2048),   # Qwen3-Coder-Next on :8004, not a thinking model
    "qwen36-nothink": ({"chat_template_kwargs": {"enable_thinking": False}}, 2048),  # Qwen3.6-35B-A3B
}
#: The only endpoint each variant may use (endpoint politeness is per server).
#: Private (the hosts): set by apply_private_config() from the private config's
#: ``section_aware_trial.variant_endpoint``.
VARIANT_ENDPOINT: dict[str, str] = {}


def apply_private_config(cfg: dict[str, Any]) -> None:
    """Set this module's private values (only the LLM-glossary endpoints)."""
    VARIANT_ENDPOINT.clear()
    VARIANT_ENDPOINT.update(cfg.get("variant_endpoint", {}))

_THINK = re.compile(r"<think>.*?</think>", re.S)


def cmd_llm_glossary(args: argparse.Namespace) -> int:
    """Run ``scripts/symbol_glossary_llm.py`` unmodified, through a thin client
    wrapper that (a) adds a variant's request fields (e.g. Qwen's
    ``chat_template_kwargs``), (b) reads ONLY ``message.content`` (a reasoning
    field is ignored; a ``<think>`` block left in content is stripped), (c) logs
    per-call usage, finish reason and latency, and (d) records each document's
    raw proposals so the gates can be broken down afterwards. No library code
    is edited; the wrapper is installed by rebinding names at runtime.
    Logs hold hashes, counts and (private, 640) proposals -- never prompts."""
    import contextvars
    import importlib.util

    import ragstack.ingestion.symbol_glossary_llm as sgl
    from ragstack.ingestion.symbol_glossary import content_key
    from ragstack.llm import OpenAILLM

    extra, max_tokens = QWEN_VARIANTS[args.variant]
    out = Path(args.stats_dir)
    out.mkdir(parents=True, exist_ok=True)
    calls_fh = (out / f"calls-{args.variant}-{args.text}.jsonl").open("a")
    props_fh = (out / f"proposals-{args.variant}-{args.text}.jsonl").open("a")
    os.chmod(out / f"proposals-{args.variant}-{args.text}.jsonl", 0o640)
    cur_doc: contextvars.ContextVar[str] = contextvars.ContextVar("cur_doc", default="")

    class WrappedLLM(OpenAILLM):
        def __init__(self, base_url, model, http, api_key=None, extra_body=None):  # noqa: ANN001
            super().__init__(base_url, model, http, api_key=api_key, extra_body={**(extra_body or {}), **extra})

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):  # noqa: ANN001
            t0 = time.monotonic()
            r = await self.http.post(
                f"{self.base_url}/v1/chat/completions",
                json={"model": self.model, "messages": messages, "max_tokens": max_tokens,
                      "temperature": temperature, **self.extra_body},
                headers={"Content-Type": "application/json"}, timeout=args.request_timeout)
            r.raise_for_status()
            data = r.json()
            ch = (data.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            finish = ch.get("finish_reason") or ""
            content = msg.get("content") or ""
            had_think_tag = "<think>" in content
            fenced = content.lstrip().startswith("```")
            content = _THINK.sub("", content).strip()
            if "<think>" in content:   # unterminated reasoning: no final answer
                content = ""
            u = data.get("usage") or {}
            calls_fh.write(json.dumps({
                "doc": cur_doc.get()[:16], "finish": finish, "prompt_tokens": u.get("prompt_tokens"),
                "completion_tokens": u.get("completion_tokens"),
                "reasoning_tokens": ((u.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                "reasoning_chars": len(msg.get("reasoning_content") or msg.get("reasoning") or ""),
                "content_chars": len(content), "think_tag_in_content": had_think_tag, "fenced": fenced,
                "seconds": round(time.monotonic() - t0, 2)}) + "\n")
            calls_fh.flush()
            if not content:
                raise ValueError(f"LLM returned an empty answer (finish_reason={finish or 'unset'})")
            return content, finish

    orig_parse = sgl.parse_response

    def parse_and_record(answer: str):  # noqa: ANN202
        got = orig_parse(answer)
        props_fh.write(json.dumps({"doc": cur_doc.get(), "proposals": got}, ensure_ascii=False) + "\n")
        props_fh.flush()
        return got

    sgl.parse_response = parse_and_record  # extract_with_llm resolves it at call time

    spec = importlib.util.spec_from_file_location("symbol_glossary_llm_script",
                                                  _REPO_PY / "scripts" / "symbol_glossary_llm.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    orig_extract = mod.extract_with_llm

    async def extract_tagged(llm, text, **kw):  # noqa: ANN001, ANN202
        cur_doc.set(content_key(text))
        return await orig_extract(llm, text, **kw)

    mod.extract_with_llm = extract_tagged
    mod.OpenAILLM = WrappedLLM
    t0 = time.time()
    rc = mod.main(["--jsonl", args.docs, "--cache", args.cache, "--llm-endpoint", args.llm_endpoint,
                   "--llm-model", args.llm_model, "--concurrency", str(args.concurrency),
                   "--max-tokens", str(max_tokens), "--timeout", str(args.request_timeout)])
    (out / f"wall-{args.variant}-{args.text}.json").write_text(json.dumps(
        {"start": t0, "end": time.time(), "seconds": round(time.time() - t0, 1), "rc": rc}))
    return rc


def gate_breakdown(proposals_path: Path, docs_path: Path) -> dict[str, Any]:
    """First failing gate per proposal, replicating ``validate_entries`` (the
    library's own helpers, in its order) on the union of each document's
    proposals. Counts only."""
    from ragstack.ingestion import symbol_glossary as sg

    cfg = sg.DEFAULT_CONFIG
    texts = {sg.content_key(x["text"]): x["text"] for x in read_jsonl(docs_path)}
    per_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for x in read_jsonl(proposals_path):
        per_doc[x["doc"]].extend((a, b) for a, b in x["proposals"])
    cnt: Counter[str] = Counter()
    for key, props in per_doc.items():
        text = texts.get(key)
        if text is None:
            cnt["doc_not_found"] += len(props)
            continue
        canon, _ = sg.canonical_map(text)
        doc_words = {w.casefold() for w in sg._WORDS.findall(canon)}
        for sym0, raw_def in props:
            sym = sg.canonical_symbol(sg._TRAILING_ARGS.sub("", (sym0 or "").strip()))
            defn = sg._clean_definition(raw_def or "", cfg.max_definition_words)
            occ = sg.symbol_pattern(sym).search(canon) if sym else None
            if occ is None:
                cnt["rejected_symbol_not_in_document"] += 1
                continue
            if not sg.is_specific_symbol(sym, allow_single_letter=cfg.allow_single_letter):
                cnt["rejected_symbol_not_specific"] += 1
                continue
            if not sg._plausible_definition(defn, sym):
                cnt["rejected_implausible_definition"] += 1
                continue
            if not sg._norm_words(defn) <= doc_words:
                cnt["rejected_document_only"] += 1
                continue
            occs = [m.start() for m in sg.symbol_pattern(sym).finditer(canon)]
            if cfg.definition_window > 0:
                if not sg._near_symbol(defn, canon, occs, cfg.definition_window):
                    cnt["rejected_locality"] += 1
                    continue
                pairs = sg._verbatim_pairs(defn, canon, occs, cfg.definition_window)
                if pairs and not any(
                        not sg._another_symbol_between(canon, d1 if d1 <= o else o + len(sym),
                                                       o if d1 <= o else d0, sym)
                        for _, d0, d1, o in pairs):
                    cnt["rejected_another_symbol_between"] += 1
                    continue
            cnt["accepted"] += 1
    cnt["proposals"] = sum(len(v) for v in per_doc.values())
    cnt["documents_with_proposals"] = sum(1 for v in per_doc.values() if v)
    return dict(cnt)


def call_stats(calls_path: Path) -> dict[str, Any]:
    xs = read_jsonl(calls_path) if calls_path.exists() else []
    f = Counter(x["finish"] for x in xs)
    return {"calls": len(xs), "finish_reasons": dict(f), "cut_off_calls": f.get("length", 0),
            "prompt_tokens": sum(x["prompt_tokens"] or 0 for x in xs),
            "completion_tokens": sum(x["completion_tokens"] or 0 for x in xs),
            "reasoning_tokens_reported": sum(x["reasoning_tokens"] or 0 for x in xs),
            "calls_with_reasoning_field": sum(1 for x in xs if x["reasoning_chars"]),
            "calls_with_think_tag_in_content": sum(1 for x in xs if x["think_tag_in_content"]),
            "empty_answers": sum(1 for x in xs if not x["content_chars"]),
            "fenced_answers": sum(1 for x in xs if x.get("fenced")),
            "call_seconds_sum": round(sum(x["seconds"] for x in xs), 1)}


# ------------------------------------------------------------------ addendum A

ADD_SPEC = {
    "L": ArmSpec("L", False, False, False),
    "J0+": ArmSpec("J0", False, False, False),
    "J1+": ArmSpec("J1", False, False, False),
    "S0+": ArmSpec("S0", False, False, False),
    "Sb+": ArmSpec("S0", True, True, False),
    "Sf3+": ArmSpec("S0", True, False, False, h_boost=0.3),
    "Sf5+": ArmSpec("S0", True, False, False, h_boost=0.5),
    "Sp+": ArmSpec("S0", False, True, False, prepend=True),
    "Spe+": ArmSpec("S0", False, True, True, prepend=True),
}
ADD_HEADING = ("Sb+", "Sf3+", "Sf5+", "Sp+", "Spe+")
ADD_POOL = "P-owner+"


def cmd_addendum(args: argparse.Namespace) -> int:
    su = load_setup(args)
    plus_arms = [a for a in ADD_SPEC if a != "L"]
    configs = [("L", "plus", 0, ADD_POOL, 1.0)]
    configs += [(a, g, 0, ADD_POOL, 1.0) for a in plus_arms for g in ("plus", "rules")]
    res = asyncio.run(run_configs(su, configs, args, ADD_SPEC))
    summary = {n: summarize(p, su) for n, p in res.items()}

    def nm(a: str, g: str = "plus") -> str:
        return config_name(a, g, 0, ADD_POOL, 1.0)

    def block(a: str, ref: str, g: str, gref: str) -> dict[str, Any]:
        return {leg: contrast(res[nm(a, g)], res[nm(ref, gref)], su, leg)
                for leg in ("rerank", "b5120", "b2048", "bm25", "hybrid")}

    contrasts: dict[str, Any] = {"plus": {}, "rules": {}}
    for g in ("plus", "rules"):
        gl = "plus"  # L always carries its live glossary (rules == plus on L)
        contrasts[g]["1 boundaries S0+ - J1+"] = block("S0+", "J1+", g, g)
        contrasts[g]["2 J1+ - L"] = block("J1+", "L", g, gl)
        contrasts[g]["2 J0+ - L"] = block("J0+", "L", g, gl)
        for a in ADD_HEADING:
            contrasts[g][f"3 {a} - S0+"] = block(a, "S0+", g, g)

    def rule(c: dict[str, Any]) -> dict[str, Any]:
        r1, r2, r3 = c["rerank"]["net"] >= 5, c["b5120"]["net"] >= 0, c["rerank"]["net_other_papers"] >= 1
        return {"net": c["rerank"]["net"], "net_b5120": c["b5120"]["net"],
                "net_other_papers": c["rerank"]["net_other_papers"],
                "rule1_net_ge_5": r1, "rule2_no_loss_at_5120": r2, "rule3_gain_outside_bazant": r3,
                "passes": r1 and r2 and r3}

    verdict = {k: rule(v) for k, v in contrasts["plus"].items() if k.startswith(("1 ", "3 "))}

    # Glossary coverage per arm (whole pool and JATS documents only).
    meta = json.loads((Path(args.out_dir) / "arms_meta.json").read_text())
    coverage: dict[str, Any] = {"build_stats": {k: v for k, v in meta["stats"].items()
                                                if "plus" in k or "rules" in k or "cache" in k}}
    for base in ("L", "J0", "J1", "S0"):
        cs = su.arms[base]
        js = [c for c in cs if c.doc_id in su.jats_docs]
        coverage[base] = {
            "chunks_with_glossary_rules": sum(1 for c in cs if c.glossary),
            "chunks_with_glossary_plus": sum(1 for c in cs if c.glossary_plus),
            "jats_docs_chunks_with_glossary_rules": sum(1 for c in js if c.glossary),
            "jats_docs_chunks_with_glossary_plus": sum(1 for c in js if c.glossary_plus),
            "jats_docs_with_any_glossaried_chunk_plus": len({c.doc_id for c in js if c.glossary_plus}),
            "jats_docs_with_any_glossaried_chunk_rules": len({c.doc_id for c in js if c.glossary}),
            "glossary_lines_plus": sum(c.glossary_plus.count("\n") + 1 for c in cs if c.glossary_plus),
            "glossary_lines_rules": sum(c.glossary.count("\n") + 1 for c in cs if c.glossary),
        }

    # Cross-check against the earlier live run's corrected numbers: L scored by the
    # requoted records' gold_chunk_ids (pair mapping identical), P-live and P-owner+.
    crosscheck: dict[str, Any] = {}
    if args.crosscheck_records:
        rq = {r["record_id"]: r for r in read_jsonl(Path(args.crosscheck_records))}
        assert all(rq[r["record_id"]]["pair"] == r["pair"] for r in su.records)
        su2 = Setup(**{**su.__dict__, "records": [rq[r["record_id"]] for r in su.records]})
        res2 = asyncio.run(run_configs(su2, [("L", "plus", 0, p, 1.0) for p in ("P-live", ADD_POOL)],
                                       args, ADD_SPEC))
        for n, p in res2.items():
            s2 = summarize(p, su2)
            crosscheck[n] = {"by_requote_gold_ids": s2["fidelity_gold_ids"],
                             "by_prereg_text_rule": {k: s2[k] for k in ("rerank", "bm25", "hybrid")}}
        crosscheck["openchia_corrected_live_pre_dedup2"] = {"rerank": 62, "bm25": 72, "hybrid": 64}

    report = {"prereg_addendum": "PREREG-addendum-A-261022.md", "prereg_addendum_sha256": args.prereg_sha,
              "pool": ADD_POOL, "cap": 0, "summary": summary, "contrasts": contrasts,
              "verdict_contrasts_1_and_3": verdict, "glossary_coverage": coverage, "crosscheck_L": crosscheck,
              "per_record_ranks": {n: {rid: {k: v for k, v in x.items() if k != "top10"}
                                       for rid, x in per.items()} for n, per in res.items()}}
    path = Path(args.out_dir, "results-addendumA-261022.json")
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    for n, s_ in summary.items():
        log(f"{n}: rerank {s_['rerank']} bm25 {s_['bm25']} hybrid {s_['hybrid']} top3 {s_['rerank_top3']} "
            f"B {s_['rerank_B']} b5120 {s_['budget5120']} b2048 {s_['budget2048']}")
    return 0


# ------------------------------------------------------------------ addendum B

B_SPEC = {"L": ArmSpec("L", False, False, False), "J1": ArmSpec("J1", False, False, False),
          "S0": ArmSpec("S0", False, False, False)}


def cmd_addendum_b(args: argparse.Namespace) -> int:
    su = load_setup(args)
    exts = [e.split("=", 1)[0] for e in args.extractor]
    configs = [("L", "rules", 0, ADD_POOL, 1.0)]              # the live glossary field, as served
    configs += [("J1", "plus", 0, ADD_POOL, 1.0), ("S0", "plus", 0, ADD_POOL, 1.0)]  # A's J1+/S0+
    configs += [(a, f"x:{e}", 0, ADD_POOL, 1.0) for e in exts for a in B_SPEC]
    res = asyncio.run(run_configs(su, configs, args, B_SPEC))
    summary = {n: summarize(p, su) for n, p in res.items()}

    def nm(a: str, g: str) -> str:
        return config_name(a, g, 0, ADD_POOL, 1.0)

    def block(x: str, y: str) -> dict[str, Any]:
        return {leg: contrast(res[x], res[y], su, leg) for leg in ("rerank", "b5120", "b2048", "bm25", "hybrid")}

    contrasts: dict[str, Any] = {}
    for i, e1 in enumerate(exts):
        for e2 in exts[i + 1:]:
            for a in B_SPEC:
                contrasts[f"extractor {a}-{e2} - {a}-{e1}"] = block(nm(a, f"x:{e2}"), nm(a, f"x:{e1}"))
    for e in exts:
        for a in ("J1", "S0"):
            contrasts[f"text {a}-{e} - L-{e}"] = block(nm(a, f"x:{e}"), nm("L", f"x:{e}"))
    if "scout" in exts:  # the re-attached Scout arms against A's / live's as-served glossary
        contrasts["check L-scout - L(live field)"] = block(nm("L", "x:scout"), nm("L", "rules"))
        for a in ("J1", "S0"):
            contrasts[f"check {a}-scout - {a}+ (A)"] = block(nm(a, "x:scout"), nm(a, "plus"))

    meta = json.loads((Path(args.out_dir) / "arms_meta.json").read_text())
    coverage: dict[str, Any] = {"build_stats": {k: v for k, v in meta["stats"].items() if k.startswith("x_")}}
    for e in exts:
        for base in ("L", "J1", "S0"):
            cs = [c for c in su.arms[base] if c.doc_id not in su.pools[ADD_POOL]]
            js = [c for c in cs if c.doc_id in su.jats_docs]
            coverage[f"{base}-{e}"] = {
                "glossaried_chunks": sum(1 for c in cs if c.gx.get(e)),
                "glossaried_chunks_jats_docs": sum(1 for c in js if c.gx.get(e)),
                "docs_with_glossaried_chunk": len({c.doc_id for c in cs if c.gx.get(e)}),
                "glossary_lines": sum(c.gx[e].count("\n") + 1 for c in cs if c.gx.get(e))}
    live_l = [c for c in su.arms["L"] if c.doc_id not in su.pools[ADD_POOL]]
    coverage["L-live-field"] = {"glossaried_chunks": sum(1 for c in live_l if c.glossary)}
    if "scout" in exts:
        coverage["L-scout_reattached_equals_live_field_chunks"] = sum(
            1 for c in live_l if c.gx.get("scout", "") == c.glossary)
        coverage["L_chunks_in_pool"] = len(live_l)
    sd = Path(args.stats_dir) if args.stats_dir else None
    extraction: dict[str, Any] = {}
    if sd is not None:
        for e in exts:
            for t in ("live", "jats"):
                calls = sd / f"calls-{e}-{t}.jsonl"
                if not calls.exists():
                    continue
                docs = (Path(args.out_dir) / "glossary-docs-live.jsonl" if t == "live"
                        else Path(args.jats_docs))
                wall = sd / f"wall-{e}-{t}.json"
                extraction[f"{e}-{t}"] = {
                    "calls": call_stats(calls),
                    "gates": gate_breakdown(sd / f"proposals-{e}-{t}.jsonl", docs),
                    "wall": json.loads(wall.read_text()) if wall.exists() else None}

    report = {"prereg_addendum": "PREREG-addendum-B-261022.md", "prereg_addendum_sha256": args.prereg_sha,
              "pool": ADD_POOL, "cap": 0, "extractors": exts, "summary": summary, "contrasts": contrasts,
              "glossary_coverage": coverage, "extraction": extraction,
              "per_record_ranks": {n: {rid: {k: v for k, v in x.items() if k != "top10"}
                                       for rid, x in per.items()} for n, per in res.items()}}
    path = Path(args.out_dir, args.out_name)
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    for n, s_ in summary.items():
        log(f"{n}: rerank {s_['rerank']} bm25 {s_['bm25']} hybrid {s_['hybrid']} top3 {s_['rerank_top3']} "
            f"B {s_['rerank_B']} b5120 {s_['budget5120']} b2048 {s_['budget2048']}")
    return 0


# ------------------------------------------------------------------ addendum C

C_ARMS = ("L", "J1", "S0", "JS", "SS", "S0m")
C_SPEC = {a: ArmSpec(a, False, False, False) for a in C_ARMS}
C_GLOSS = ("scout", "qwen36-nothink")


def cmd_addendum_c(args: argparse.Namespace) -> int:
    su = load_setup(args)
    configs = [(a, f"x:{g}", 0, ADD_POOL, 1.0) for g in C_GLOSS for a in C_ARMS]
    res = asyncio.run(run_configs(su, configs, args, C_SPEC))
    summary = {n: summarize(p, su) for n, p in res.items()}

    def nm(a: str, g: str) -> str:
        return config_name(a, f"x:{g}", 0, ADD_POOL, 1.0)

    def block(x: str, y: str) -> dict[str, Any]:
        return {leg: contrast(res[x], res[y], su, leg) for leg in ("rerank", "b5120", "b2048", "bm25", "hybrid")}

    pairs_ = [("1 SS - S0", "SS", "S0"), ("2 JS - J1", "JS", "J1"), ("3 SS - JS", "SS", "JS"),
              ("4 S0m - S0", "S0m", "S0")] + [(f"5 {a} - L", a, "L") for a in ("JS", "SS", "S0m")]
    contrasts = {g: {n: block(nm(a, g), nm(b, g)) for n, a, b in pairs_} for g in C_GLOSS}

    def rule(c: dict[str, Any]) -> dict[str, Any]:
        r1, r2, r3 = c["rerank"]["net"] >= 5, c["b5120"]["net"] >= 0, c["rerank"]["net_other_papers"] >= 1
        return {"net": c["rerank"]["net"], "net_b5120": c["b5120"]["net"],
                "net_other_papers": c["rerank"]["net_other_papers"],
                "rule1_net_ge_5": r1, "rule2_no_loss_at_5120": r2, "rule3_gain_outside_bazant": r3,
                "passes": r1 and r2 and r3}

    verdict = {g: {k: rule(v) for k, v in contrasts[g].items() if k.startswith(("1 ", "3 "))} for g in C_GLOSS}
    desc: dict[str, Any] = {}
    for a in C_ARMS:
        cs = [c for c in su.arms[a] if c.doc_id not in su.pools[ADD_POOL]]
        js = [c for c in cs if c.doc_id in su.jats_docs]

        def d(sel: list[C]) -> dict[str, Any]:
            t = sorted(c.tokens for c in sel)
            return {"chunks": len(t), "median_tokens": statistics.median(t),
                    "p90_tokens": t[min(len(t) - 1, int(math.ceil(0.9 * len(t))) - 1)],
                    "share_under_128": round(sum(x < 128 for x in t) / len(t), 4),
                    "max_tokens": t[-1]}
        desc[a] = {"pool": d(cs), "jats_docs": d(js),
                   "mean_docs_top10": {g: summary[nm(a, g)]["mean_distinct_docs_top10"] for g in C_GLOSS}}
    meta = json.loads((Path(args.out_dir) / "arms_meta.json").read_text())
    st = meta["stats"]
    coverage = {k: v for k, v in st.items()
                if k.startswith("semantic_") or k == "S0m_section_units"
                or any(k.startswith(f"x_{g}_") for g in C_GLOSS)}
    report = {"prereg_addendum": "PREREG-addendum-C-261022.md", "prereg_addendum_sha256": args.prereg_sha,
              "pool": ADD_POOL, "cap": 0, "semantic_params": SEMANTIC_PARAMS, "summary": summary,
              "contrasts": contrasts, "verdict_contrasts_1_and_3": verdict, "descriptives": desc,
              "build_coverage": coverage,
              "per_record_ranks": {n: {rid: {k: v for k, v in x.items() if k != "top10"}
                                       for rid, x in per.items()} for n, per in res.items()}}
    path = Path(args.out_dir, "results-addendumC-261022.json")
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    for n, s_ in summary.items():
        log(f"{n}: rerank {s_['rerank']} bm25 {s_['bm25']} hybrid {s_['hybrid']} top3 {s_['rerank_top3']} "
            f"B {s_['rerank_B']} b5120 {s_['budget5120']} b2048 {s_['budget2048']}")
    return 0


# Part 2: where the gold sits. The class map and its order are fixed in the prereg.
SECTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("other", ("acknowledg", "reference", "funding", "conflict", "competing", "author contribution",
               "data availability", "availability of data", "supplementary", "supporting information",
               "appendix", "abbreviation", "ethics")),
    ("abstract", ("abstract",)),
    ("introduction-background", ("introduction", "background", "motivation", "overview")),
    ("results", ("result", "finding", "outcome", "application", "case stud", "example")),
    ("methods", ("method", "material", "model", "theory", "approach", "experimental", "experiment",
                 "procedure", "study design", "design", "setting", "participant", "formulation",
                 "derivation", "statistical analysis", "data", "parameter", "assumption")),
    ("discussion", ("discussion", "limitation", "implication", "interpretation")),
    ("conclusion", ("conclusion", "concluding", "summary", "outlook", "perspective")),
)
BODY_CLASSES = {"methods", "results", "float"}
_MINUS = str.maketrans(dict.fromkeys("−–‐‑‒—", "-"))
_SCI = re.compile(r"(?:×|x|·|\*)10\^?(-?\d+)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")


def classify_title(title: str) -> str | None:
    t = title.casefold()
    for cls, keys in SECTION_RULES:
        if any(k in t for k in keys):
            return cls
    return None


def classify_unit(path: str, is_float: bool, unmapped: Counter[str]) -> str:
    if is_float:
        return "float"
    if path == "Abstract":
        return "abstract"
    if not path:
        return "other"
    for title in path.split(" > "):
        c = classify_title(title)
        if c:
            return c
    unmapped[sha(path)[:12]] += 1   # titles are document text: counted by hash, never written
    return "other"


def numbers(text: str) -> list[float]:
    t = "".join(unicodedata.normalize("NFKC", text).split()).translate(_MINUS)
    t = _SCI.sub(lambda m: f"e{m.group(1)}", t)
    t = _THOUSANDS.sub("", t)
    out = []
    for m in _NUM.finditer(t):
        try:
            out.append(float(m.group()))
        except ValueError:
            continue
    return out


def _has_num(nums: list[float], v: float) -> bool:
    return any(abs(x - v) <= 1e-6 * max(abs(v), 1e-12) for x in nums)


def cmd_section_location(args: argparse.Namespace) -> int:
    from ragstack.ingestion.tokenization import make_token_counter

    su = load_setup(args)
    pairs = {p["id"]: p for p in read_jsonl(Path(args.pairs))}
    counter = make_token_counter("hf", model=SFR_MODEL)
    rows = read_jsonl(Path(args.dump))
    pm = {}
    for r in rows:
        pm.setdefault(str(r["doc_id"]), doc_pmcid(r.get("metadata") or {}))
    docs = sorted(d for d in su.jats_docs if d not in su.pools[ADD_POOL])
    unmapped: Counter[str] = Counter()
    units: dict[str, list[tuple[str, str, str]]] = {}   # doc -> (class, path-hash, text)
    for d in docs:
        u = jats_doc_units(parse_jats(Path(args.jats_dir) / f"{pm[d]}.xml"), pm[d], counter.count)
        n = u["n_sections"]
        units[d] = [(classify_unit(p, i >= n, unmapped), sha(p)[:12], t) for i, (p, t) in enumerate(u["S0"])]
    class_counts = Counter(c for us in units.values() for c, _, _ in us)
    per: dict[str, Any] = {}
    for r in su.records:
        rid, pair = r["record_id"], pairs[r["pair"]]
        quote_raw = str(pair["expected"]["quote"])
        vals = [float(v["value"]) for v in pair["expected"].get("values") or []]
        qnums = numbers(quote_raw)
        rec: dict[str, Any] = {"paper": su.rec_paper[rid], "kind": r["kind"], "classes": [], "fold": False,
                               "docs": 0}
        for gold, is_fold in ((su.gold, False), (su.gold_fold, True)):
            q = gold.q[rid]
            hits = [(d, c) for d in docs for c, _, t in units[d] if hit(q, gold.normalise(t))]
            if hits:
                rec["fold"] = is_fold
                break
        rec["classes"] = sorted({c for _, c in hits})
        rec["docs"] = len({d for d, _ in hits})
        # Value check, per gold-holding document: abstract vs body (methods/results/float).
        crit: dict[str, dict[str, bool]] = {k: {"abstract": False, "body": False} for k in ("q", "v", "n", "any")}
        nq = gold.q[rid]
        for d in {d for d, _ in hits}:
            for c, _, t in units[d]:
                where = "abstract" if c == "abstract" else ("body" if c in BODY_CLASSES else None)
                if where is None:
                    continue
                nums = numbers(t)
                f_q = bool(hit(nq, gold.normalise(t)))
                f_v = any(_has_num(nums, v) for v in vals)
                f_n = bool(qnums) and all(_has_num(nums, x) for x in qnums)
                for k, f in (("q", f_q), ("v", f_v), ("n", f_n), ("any", f_q or f_v or f_n)):
                    crit[k][where] = crit[k][where] or f

        def cat(x: dict[str, bool]) -> str:
            return {(True, False): "abstract_only", (True, True): "abstract_plus_body",
                    (False, True): "body_only", (False, False): "neither"}[(x["abstract"], x["body"])]
        rec["value_check"] = {k: cat(v) for k, v in crit.items()}
        rec["gold_in_jats"] = bool(hits)
        per[rid] = rec

    def tally(sel) -> dict[str, Any]:
        ids = [rid for rid in per if sel(per[rid])]
        out: dict[str, Any] = {"records": len(ids)}
        out["class_any"] = dict(Counter(c for rid in ids for c in per[rid]["classes"]))
        out["class_set"] = dict(Counter("+".join(per[rid]["classes"]) or "none" for rid in ids))
        for k in ("q", "v", "n", "any"):
            out[f"value_check_{k}"] = dict(Counter(per[rid]["value_check"][k] for rid in ids))
        out["record_ids_by_class_set"] = {cs: sorted(rid for rid in ids
                                                     if ("+".join(per[rid]["classes"]) or "none") == cs)
                                          for cs in out["class_set"]}
        return out

    nonjats = sorted(rid for rid, x in per.items() if not x["gold_in_jats"])
    report = {"prereg_addendum": "PREREG-addendum-C-261022.md", "pool": ADD_POOL,
              "jats_docs_in_pool": len(docs), "unit_class_counts": dict(class_counts),
              "unmapped_titles": {"distinct": len(unmapped), "units": sum(unmapped.values())},
              "records_gold_only_outside_jats": {"count": len(nonjats), "ids": nonjats},
              "records_matched_only_with_fold": sorted(rid for rid, x in per.items() if x["fold"]),
              "all": tally(lambda x: True),
              "bazant": tally(lambda x: x["paper"] == BAZANT_PMCID),
              "other_papers": tally(lambda x: x["paper"] != BAZANT_PMCID),
              "per_kind": {k: tally(lambda x, k=k: x["kind"] == k) for k in sorted({x["kind"] for x in per.values()})},
              "per_record": {rid: {k: v for k, v in x.items() if k != "paper"} for rid, x in per.items()}}
    path = Path(args.out_dir, "section-location-261022.json")
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    log(f"section location: {report['all']['class_set']}; value(any): {report['all']['value_check_any']}")
    return 0


# ------------------------------------------------------------------ addendum D

D_SIZES = (128, 256, 512, 1024)
D_FAMILIES = ("T", "X")              # T: J1 structure; X: S0 structure (token within sections)
D_GLOSS = ("scout", "qwen36-nothink")
D_DENSE = ("int8", "int8+r", "bin", "bin+r")
D_EMBED_LIMIT = 1_000_000


def d_arm(fam: str, s: int, ov: int) -> str:
    return f"{fam}{s}o{ov}"


D_ARMS = tuple(d_arm(f, s, ov) for f in D_FAMILIES for s in D_SIZES for ov in (0, s // 8))


def build_arms_d(args: argparse.Namespace) -> tuple[dict[str, list[C]], dict[str, Any]]:
    """The 16 T/X arms over EVERY document: JATS documents by family, non-JATS
    documents plain-token over their live text reassembled exactly as the live
    attacher hashes it. Glossaries gx:<ext> from the existing caches (read only)."""
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker
    from ragstack.ingestion.reassembly import reassemble
    from ragstack.ingestion.symbol_glossary import (
        SymbolGlossaryAttacher,
        SymbolGlossaryCache,
        content_key,
    )
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    counter = make_token_counter("hf", model=SFR_MODEL)
    chunkers = {(s, ov): FixedTokenWindowChunker(s, ov, token_counter=counter)
                for s in D_SIZES for ov in (0, s // 8)}
    extractors: dict[str, tuple[Any, Any]] = {}
    for spec_ in args.extractor:
        name, caches = spec_.split("=", 1)
        live_c, jats_c = caches.split(",", 1)
        extractors[name] = (SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(live_c))),
                            SymbolGlossaryAttacher(cache=SymbolGlossaryCache(Path(jats_c))))
    if tuple(extractors) != D_GLOSS:
        raise SystemExit(f"addendum D needs --extractor for exactly {D_GLOSS}")
    removed = {x.strip() for x in Path(args.powner_plus).read_text().splitlines() if x.strip()}
    rows = read_jsonl(Path(args.dump))
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_doc[str(r["doc_id"])].append(r)
    jdir = Path(args.jats_dir)
    st: Counter[str] = Counter()
    arms: dict[str, list[C]] = {a: [] for a in D_ARMS}
    failures: list[dict[str, str]] = []
    jats_docs: list[str] = []
    for did in sorted(by_doc):
        drows = sorted(by_doc[did], key=lambda r: int(r["start_char"]))
        kept = did not in removed
        pmcid = doc_pmcid(drows[0].get("metadata") or {})
        units = None
        if pmcid:
            xml = jdir / f"{pmcid}.xml"
            if not xml.exists():
                failures.append({"doc_id": did, "pmcid": pmcid, "stage": "fetch"})
            else:
                try:
                    units = jats_doc_units(parse_jats(xml), pmcid, counter.count)
                except Exception as e:  # noqa: BLE001 - doc goes to the non-JATS stratum
                    failures.append({"doc_id": did, "pmcid": pmcid, "stage": "parse", "reason": type(e).__name__})
        if units is None:
            ltext = reassemble(did, drows).text
            gls = {}
            for name, (att_l, _) in extractors.items():
                gls[name] = att_l.glossary_for(ltext)
                if kept:
                    hit_ = att_l.cache.get(content_key(ltext)) is not None
                    st[f"nonjats_{name}_cache_hit"] += int(hit_)
                    st[f"nonjats_{name}_cache_miss"] += int(not hit_)
            if kept:
                st["nonjats_docs_kept"] += 1
            for (s, ov), ck in chunkers.items():
                chs = ck.chunk(Document(id=did, content=ltext))
                for fam in D_FAMILIES:
                    a = d_arm(fam, s, ov)
                    for n, ch in enumerate(chs):
                        arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "", "", jats=False,
                                         gx={nm_: gl.for_chunk(ch.content, extractors[nm_][0].config,
                                                               start_char=ch.start_char)
                                             for nm_, gl in gls.items()}))
            continue
        jats_docs.append(did)
        for fam, base in (("T", "J1"), ("X", "S0")):
            us = units[base]
            doc_text = "\n\n".join(t for _, t in us)
            gxs = {}
            for name, (_, att_j) in extractors.items():
                gxs[name] = att_j.glossary_for(doc_text)
                if kept:
                    hit_ = att_j.cache.get(content_key(doc_text)) is not None
                    st[f"jats_{fam}_{name}_cache_hit"] += int(hit_)
                    st[f"jats_{fam}_{name}_cache_miss"] += int(not hit_)
            for (s, ov), ck in chunkers.items():
                a = d_arm(fam, s, ov)
                off = n = 0
                for head, text in us:
                    for ch in ck.chunk(Document(id=did, content=text)):
                        gstart = off + ch.start_char
                        arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, head or "", "", jats=True,
                                         gx={nm_: gg.for_chunk(ch.content, extractors[nm_][1].config,
                                                               start_char=gstart)
                                             for nm_, gg in gxs.items()}))
                        n += 1
                    off += len(text) + 2
    tok = counter._tokenizer()
    for cs in arms.values():
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)
    st["jats_docs"] = len(jats_docs)
    st["jats_docs_kept"] = sum(1 for d in jats_docs if d not in removed)
    st["docs"] = len(by_doc)
    return arms, {"stats": dict(st), "failures": failures, "jats_docs": jats_docs}


def d_consistency(arms: dict[str, list[C]], ref_path: Path, jats_docs: set[str]) -> dict[str, Any]:
    """T512o64 must equal J1 and X512o64 must equal S0 on JATS documents (content
    and both glossaries, chunk for chunk); non-JATS T512o64 vs live L is reported."""
    ref = load_arms(ref_path)
    out: dict[str, Any] = {}

    def by_doc(cs: list[C], jats: bool) -> dict[str, list[tuple[str, ...]]]:
        d: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        for c in cs:
            if (c.doc_id in jats_docs) == jats:
                d[c.doc_id].append((c.content, *(c.gx.get(g, "") for g in D_GLOSS)))
        return d

    for mine, theirs in (("T512o64", "J1"), ("X512o64", "S0")):
        a, b = by_doc(arms[mine], True), by_doc(ref[theirs], True)
        diff_docs = sorted(d for d in set(a) | set(b) if a.get(d) != b.get(d))
        content_diff = sorted(d for d in set(a) | set(b)
                              if [x[0] for x in a.get(d, [])] != [x[0] for x in b.get(d, [])])
        out[f"{mine}=={theirs}"] = {"jats_docs": len(set(a) | set(b)), "docs_differing": len(diff_docs),
                                   "docs_content_differing": len(content_diff),
                                   "chunks_mine": sum(map(len, a.values())), "chunks_ref": sum(map(len, b.values())),
                                   "equal": not diff_docs}
    a = {d: [x[0] for x in v] for d, v in by_doc(arms["T512o64"], False).items()}
    b = {d: [x[0] for x in v] for d, v in by_doc(ref["L"], False).items()}
    out["nonjats T512o64 vs live L (reported)"] = {
        "docs": len(set(a) | set(b)), "docs_identical": sum(1 for d in set(a) | set(b) if a.get(d) == b.get(d)),
        "chunks_mine": sum(map(len, a.values())), "chunks_live": sum(map(len, b.values()))}
    return out


def d_setup(args: argparse.Namespace, arms: dict[str, list[C]], jats_docs: set[str]) -> Setup:
    records = read_jsonl(Path(args.records))
    pairs = {p["id"]: p for p in read_jsonl(Path(args.pairs))}
    ids = {x.strip() for x in Path(args.powner_plus).read_text().splitlines() if x.strip() and not x.startswith("#")}
    return Setup(arms=arms, pools={ADD_POOL: ids}, records=records, gold=Gold(records, pairs),
                 gold_fold=Gold(records, pairs, fold),
                 rec_paper={r["record_id"]: str((pairs[r["pair"]].get("paper") or {}).get("pmcid") or "")
                            for r in records},
                 jats_docs=jats_docs)


def d_split_classes(su: Setup, arm: str) -> dict[str, str]:
    """Pool-level: 'contained' (some chunk holds a full hit), 'partial' (only a
    >= 60% prefix/suffix hit), 'split' (neither). The pool's normalised chunk
    texts are joined with NUL, which no normalised quote contains, so a
    substring hit in the join is a hit inside one chunk."""
    big = "\x00".join(su.gold.text(c) for c in su.arms[arm] if c.doc_id not in su.pools[ADD_POOL])
    out = {}
    for r in su.records:
        q = su.gold.q[r["record_id"]]
        if q and q in big:
            out[r["record_id"]] = "contained"
            continue
        m = math.ceil(0.6 * len(q))
        out[r["record_id"]] = "partial" if q and (q[:m] in big or q[-m:] in big) else "split"
    return out


def d_storage(cs: list[C]) -> dict[str, Any]:
    n = len(cs)
    t = sorted(c.tokens for c in cs)
    gb = 1e9
    return {"chunks": n, "tokens_total": sum(t), "tokens_median": statistics.median(t),
            "tokens_p90": t[min(n - 1, int(math.ceil(0.9 * n)) - 1)], "tokens_max": t[-1],
            "share_under_128": round(sum(x < 128 for x in t) / n, 4),
            "content_chars_total": sum(len(c.content) for c in cs),
            "estimate": {"note": "arithmetic, not measured: N x 4096 x bytes/dim; HNSW ~ N x 32 links x 4 B",
                         "vectors_gb_f32": round(n * 4096 * 4 / gb, 3),
                         "vectors_gb_int8": round(n * 4096 / gb, 3),
                         "vectors_gb_bin": round(n * 4096 / 8 / gb, 4),
                         "hnsw_links_gb": round(n * 32 * 4 / gb, 4),
                         "rescore_variants": "RAM holds the quantized vectors; disk also holds f32"}}


def cmd_addendum_d(args: argparse.Namespace) -> int:
    t_start = time.time()
    out = Path(args.out_dir)
    arms_path = out / "arms-addendumD-261023.jsonl"
    meta_path = out / "arms-addendumD-meta-261023.json"
    if args.reuse_arms and arms_path.exists():
        arms = load_arms(arms_path)
        meta = json.loads(meta_path.read_text())
        log(f"reused arms: {sum(map(len, arms.values()))} chunks")
    else:
        arms, meta = build_arms_d(args)
        save_arms(arms, arms_path)
        meta["consistency"] = d_consistency(arms, Path(args.reference_arms), set(meta["jats_docs"]))
        meta_path.write_text(json.dumps(meta, indent=1))
        os.chmod(meta_path, 0o640)
        log(f"build stats: {meta['stats']}; failures: {len(meta['failures'])}")
        log(f"consistency: {json.dumps(meta['consistency'])}")
    jats_docs = set(meta["jats_docs"])
    ok_cons = all(v["equal"] for k, v in meta["consistency"].items() if "==" in k)
    misses = {k: v for k, v in meta["stats"].items() if k.endswith("_cache_miss") and v}
    if not ok_cons or misses:
        log(f"STOP: consistency ok={ok_cons}; cache misses={misses}")
        return 3
    # J1 from the reference arms, for contrast 5 (embeddings and reranks cached).
    ref = load_arms(Path(args.reference_arms))
    arms["J1"] = ref["J1"]
    su = d_setup(args, arms, jats_docs)
    store = EmbStore(out / "emb")
    queries = [r["query"] for r in su.records]
    texts = list(queries) + [c.content for a in (*D_ARMS, "J1") for c in arms[a]]
    todo = store.missing(texts)
    vec_bytes = store.vec_path.stat().st_size if store.vec_path.exists() else 0
    log(f"embedding plan: {len(todo)} new texts; cache {len(store.row)} vectors, {vec_bytes / 1e9:.2f} GB")
    meta["embedding_plan"] = {"new_texts": len(todo), "cached_vectors": len(store.row),
                              "cache_gb": round(vec_bytes / 1e9, 2)}
    if args.build_only:
        meta_path.write_text(json.dumps(meta, indent=1))
        return 0
    if len(todo) > D_EMBED_LIMIT:
        log(f"STOP: {len(todo)} new texts exceed {D_EMBED_LIMIT}")
        return 4

    spec = {a: ArmSpec(a, False, False, False) for a in (*D_ARMS, "J1")}
    t_arms = [a for a in D_ARMS if a.startswith("T")]
    for a in t_arms:
        for m in D_DENSE:
            spec[f"{a}~{m}"] = ArmSpec(a, False, False, False, dense=m)
    configs = [(a, f"x:{g}", 0, ADD_POOL, 1.0) for g in D_GLOSS for a in (*D_ARMS, "J1")]
    configs += [(f"{a}~{m}", "x:scout", 0, ADD_POOL, 1.0) for a in t_arms for m in D_DENSE]
    t0 = time.time()
    res = asyncio.run(run_configs(su, configs, args, spec))
    t_run = time.time() - t0
    summary = {n: summarize(p, su) for n, p in res.items()}

    def nm(a: str, g: str) -> str:
        return config_name(a, f"x:{g}", 0, ADD_POOL, 1.0)

    def block(x: str, y: str) -> dict[str, Any]:
        return {leg: contrast(res[x], res[y], su, leg) for leg in ("rerank", "b5120", "b2048", "bm25", "hybrid")}

    def rule(c: dict[str, Any]) -> dict[str, Any]:
        r1, r2, r3 = c["rerank"]["net"] >= 5, c["b5120"]["net"] >= 0, c["rerank"]["net_other_papers"] >= 1
        return {"net": c["rerank"]["net"], "net_b5120": c["b5120"]["net"],
                "net_other_papers": c["rerank"]["net_other_papers"],
                "rule1_net_ge_5": r1, "rule2_no_loss_at_5120": r2, "rule3_gain_outside_bazant": r3,
                "passes": r1 and r2 and r3}

    contrasts: dict[str, Any] = {"1 overlap": {}, "2 size": {}, "3 sections X-T": {}, "4 storage": {},
                                 "5 T512o64-J1": {}}
    verdict: dict[str, Any] = {}
    for g in D_GLOSS:
        for fam in D_FAMILIES:
            for s in D_SIZES:
                k = f"{g}|{d_arm(fam, s, s // 8)} - {d_arm(fam, s, 0)}"
                contrasts["1 overlap"][k] = block(nm(d_arm(fam, s, s // 8), g), nm(d_arm(fam, s, 0), g))
                verdict[k] = rule(contrasts["1 overlap"][k])
                for ovf in ("0", "s/8"):
                    ov, ov512 = (0, 0) if ovf == "0" else (s // 8, 64)
                    if s != 512:
                        contrasts["2 size"][f"{g}|{d_arm(fam, s, ov)} - {d_arm(fam, 512, ov512)}"] = block(
                            nm(d_arm(fam, s, ov), g), nm(d_arm(fam, 512, ov512), g))
                    if fam == "X":
                        contrasts["3 sections X-T"][f"{g}|{d_arm('X', s, ov)} - {d_arm('T', s, ov)}"] = block(
                            nm(d_arm("X", s, ov), g), nm(d_arm("T", s, ov), g))
        contrasts["5 T512o64-J1"][g] = block(nm("T512o64", g), nm("J1", g))
    for a in t_arms:
        for m in D_DENSE:
            contrasts["4 storage"][f"scout|{a}~{m} - {a}"] = block(nm(f"{a}~{m}", "scout"), nm(a, "scout"))

    # Split quotes (pool level) and quote lengths in SFR tokens (raw quote text).
    from ragstack.ingestion.tokenization import make_token_counter

    counter = make_token_counter("hf", model=SFR_MODEL)
    pairs = {p["id"]: p for p in read_jsonl(Path(args.pairs))}
    qlen = {r["record_id"]: counter.count(str(pairs[r["pair"]]["expected"]["quote"])) for r in su.records}
    split = {a: d_split_classes(su, a) for a in D_ARMS}
    split_report: dict[str, Any] = {"classes": {a: dict(Counter(v.values())) for a, v in split.items()},
                                    "quote_sfr_tokens": {"median": statistics.median(qlen.values()),
                                                         "max": max(qlen.values())},
                                    "quote_length": {}, "overlap_moves": {}}
    for fam in D_FAMILIES:
        for s in D_SIZES:
            ov = s // 8
            split_report["quote_length"][f"{s}o{ov}"] = {
                "longer_than_s_minus_ov": sum(1 for x in qlen.values() if x > s - ov),
                "longer_than_s_never_fits": sum(1 for x in qlen.values() if x > s),
                "at_most_ov_plus_1_always_fits": sum(1 for x in qlen.values() if x <= ov + 1)}
            a0, a1 = split[d_arm(fam, s, 0)], split[d_arm(fam, s, ov)]
            split_report["overlap_moves"][f"{d_arm(fam, s, ov)} vs {d_arm(fam, s, 0)}"] = {
                "gained_contained": sorted(r for r in a1 if a1[r] == "contained" and a0[r] != "contained"),
                "lost_contained": sorted(r for r in a1 if a0[r] == "contained" and a1[r] != "contained")}
    for a, v in split.items():
        split_report["classes"][a]["bazant_contained"] = sum(
            1 for r, x in v.items() if x == "contained" and su.rec_paper[r] == BAZANT_PMCID)

    storage = {a: d_storage([c for c in arms[a] if c.doc_id not in su.pools[ADD_POOL]]) for a in D_ARMS}
    from ragstack.provenance import experiment_provenance

    report = {"prereg_addendum": "PREREG-addendum-D-261023.md", "prereg_addendum_sha256": args.prereg_sha,
              "provenance": experiment_provenance(repo=str(_REPO_PY.parent)),
              "pool": ADD_POOL, "cap": 0, "arms": list(D_ARMS), "glossaries": list(D_GLOSS),
              "dense_variants": list(D_DENSE), "build": meta, "summary": summary, "contrasts": contrasts,
              "verdict_overlap_rule": verdict, "split_quotes": split_report, "storage": storage,
              "load": {"embed_http_calls": EMBED_STATS["http_calls"], "texts_embedded": EMBED_STATS["texts_embedded"],
                       "rerank_calls": EMBED_STATS["rerank_calls"],
                       "seconds_run_configs": round(t_run, 1), "seconds_total": round(time.time() - t_start, 1)},
              "per_record_ranks": {n: {rid: {k: v for k, v in x.items() if k != "top10"}
                                       for rid, x in per.items()} for n, per in res.items()}}
    path = out / "results-addendumD-261023.json"
    path.write_text(json.dumps(report, indent=1))
    os.chmod(path, 0o640)
    for n, s_ in summary.items():
        log(f"{n}: rerank {s_['rerank']} b2048 {s_['budget2048']} b5120 {s_['budget5120']} "
            f"bm25 {s_['bm25']} hybrid {s_['hybrid']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--dump", required=True)
    f.add_argument("--jats-dir", required=True)
    lg = sub.add_parser("llm-glossary", help="addendum B: run the LLM glossary script via a thin wrapper")
    lg.add_argument("--variant", required=True, choices=sorted(QWEN_VARIANTS))
    lg.add_argument("--text", required=True, choices=("live", "jats"))
    lg.add_argument("--docs", required=True)
    lg.add_argument("--cache", required=True)
    lg.add_argument("--llm-endpoint", required=True)
    lg.add_argument("--llm-model", required=True)
    lg.add_argument("--concurrency", type=int, default=4)
    lg.add_argument("--request-timeout", type=float, default=900.0)
    lg.add_argument("--stats-dir", required=True)
    for name in ("build", "calibrate", "run", "addendum", "addendum-b", "addendum-c", "addendum-d",
                 "section-location"):
        s = sub.add_parser(name)
        s.add_argument("--dump", required=True)
        s.add_argument("--records", required=True)
        s.add_argument("--pairs", required=True)
        s.add_argument("--jats-dir", required=True)
        s.add_argument("--llm-cache", required=True)
        s.add_argument("--powner", required=True, help="doc_ids removed for P-owner")
        s.add_argument("--powner-plus", required=True, help="doc_ids removed for P-owner+")
        s.add_argument("--out-dir", required=True)
        s.add_argument("--embed-urls", default="", help="comma-separated :9001-:9006 URLs; no default")
        s.add_argument("--embed-model", default=SFR_MODEL)
        s.add_argument("--rerank-url", default="", help="cross-encoder sidecar; no default")
        s.add_argument("--primary-cap", type=int, default=-1)
        s.add_argument("--primary-pool", default="P-owner+", choices=POOLS)
        s.add_argument("--prereg-sha", default="")
        s.add_argument("--jats-llm-cache", default="", help="addendum A: JATS LLM glossary cache (read)")
        s.add_argument("--write-glossary-docs", default="", help="build: write the attacher's doc texts (JSONL)")
        s.add_argument("--crosscheck-records", default="", help="addendum A: requoted records, L cross-check")
        s.add_argument("--stats-dir", default="", help="addendum B: extraction call/proposal logs")
        s.add_argument("--semantic", action="store_true", help="build: addendum C arms JS / SS / S0m")
        s.add_argument("--jats-docs", default="", help="addendum B: the JATS glossary documents (JSONL)")
        s.add_argument("--out-name", default="results-addendumB-261022.json")
        s.add_argument("--extractor", action="append", default=[],
                       help="addendum B: NAME=LIVE_CACHE,JATS_CACHE (repeatable; read-only)")
        s.add_argument("--reference-arms", default="", help="addendum D: Addendum C arms_chunks.jsonl (J1/S0/L)")
        s.add_argument("--reuse-arms", action="store_true", help="addendum D: reuse the saved D arms")
        s.add_argument("--build-only", action="store_true", help="addendum D: build, check, plan; no embedding")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        return cmd_fetch(args)
    if args.cmd == "llm-glossary":
        if not VARIANT_ENDPOINT:
            cp = os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG")
            if not cp:
                p.error("llm-glossary needs the private config: set CHUNKTRIAL_PRIVATE_CONFIG")
            apply_private_config(json.loads(Path(cp).read_text()).get("section_aware_trial", {}))
        if args.llm_endpoint.rstrip("/") != VARIANT_ENDPOINT[args.variant]:
            p.error(f"variant {args.variant} must use {VARIANT_ENDPOINT[args.variant]} only")
        if not 1 <= args.concurrency <= 4:
            p.error("at most 4 in flight on the generator endpoint")
        if not Path(args.cache).name.startswith(f"llm-cache-{args.variant}-"):
            p.error("the cache must be a new per-variant file (llm-cache-<variant>-{live,jats}-261022.jsonl)")
        return cmd_llm_glossary(args)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "section-location":
        return cmd_section_location(args)
    if not (args.embed_urls and args.rerank_url):
        p.error("--embed-urls and --rerank-url are required (no defaults)")
    if args.cmd == "calibrate":
        return cmd_calibrate(args)
    if args.cmd == "addendum-c":
        return cmd_addendum_c(args)
    if args.cmd == "addendum-d":
        if not args.reference_arms:
            p.error("addendum-d needs --reference-arms")
        return cmd_addendum_d(args)
    if args.cmd == "addendum-b":
        return cmd_addendum_b(args)
    if args.cmd == "addendum":
        if not args.jats_llm_cache:
            p.error("addendum needs --jats-llm-cache")
        return cmd_addendum(args)
    if args.primary_cap not in (0, 2):
        p.error("run needs --primary-cap 0|2, named from the L calibration first")
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
