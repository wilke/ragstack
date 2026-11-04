"""``passages``: one ~1,200-char passage per eval article, cut at sentence boundaries
inside a target section region of the extracted article record.

Input: the jats_extract JSONL (config ``extracted``: a path, a glob or a list; default
``<out_dir>/extracted.jsonl``). Only records with ``metadata.content_type == "article"``
whose ``path`` is an eval article of ``sample.jsonl`` are used.

Regions: the article record text carries section titles as markdown headings
(``## Results``, ``### Growth curves``; ragstack/ingestion/jats.py). A region is the
text between one heading line and the next heading; the text before the first heading
is the abstract. A region's section type is the first ``section_targets`` keyword found
in its own heading or, failing that, in its parent top-level (``##``) heading; the
abstract region is ``abstract``. Regions with no target type are not used.

Cut: the passage is a run of whole sentences (``ragstack.ingestion.chunkers.sentence_spans``,
the segmenter the sentence chunker uses) inside one region, grown from a seeded start
sentence until it reaches ``chars``; if that overshoots ``max_frac x chars`` the last
sentence is dropped when the rest still reaches ``min_frac x chars``. The type is chosen
by a seeded draw over the target types present in the article, then the region and start
inside it, so section types spread across the sample (the type is reported as the
``section_type`` stratum). Articles with no usable region are listed in
``passages.skipped.jsonl`` with the reason.

Fallback (config ``passages.fallback_body``, default off): when the target regions give no
passage (``no_target_region`` or ``no_fitting_passage``), the cut is retried over every
region of the text, headed or not, with type ``body``; such rows carry ``fallback: true``.
Short letters and dispatches without section headings (and without an abstract) would
otherwise be lost. Articles that fail the fallback too keep their original skip reason.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any

from .common import (
    iter_jsonl_stream,
    log,
    read_jsonl,
    seeded_rank,
    seeded_unit,
    sha256_text,
    write_json,
    write_jsonl,
)
from .config import out_path, require

_HEADING = re.compile(r"^(#{2,6}) +(.+?)\s*$", re.MULTILINE)


def extracted_paths(cfg: dict[str, Any]) -> list[Path]:
    specs = cfg.get("extracted") or [str(out_path(cfg, "extracted.jsonl"))]
    out: list[Path] = []
    for s in specs:
        hits = sorted(glob.glob(s)) if any(ch in s for ch in "*?[") else [s]
        out.extend(Path(h) for h in hits)
    if not out:
        raise FileNotFoundError("no extracted JSONL matched the config 'extracted'")
    return out


def iter_records(cfg: dict[str, Any]):
    for p in extracted_paths(cfg):
        for rec in iter_jsonl_stream(p):
            if isinstance(rec, dict) and isinstance(rec.get("text"), str) and rec.get("path"):
                yield rec


def classify(title: str, targets: list[str]) -> str | None:
    t = title.lower()
    for target in targets:
        if target.lower() in t:
            return target
    return None


def regions(text: str, targets: list[str], has_abstract: bool = True) -> list[dict[str, Any]]:
    """Section regions of an article record text: ``{start, end, type, title}``."""
    heads = list(_HEADING.finditer(text))
    out: list[dict[str, Any]] = []
    first = heads[0].start() if heads else len(text)
    if has_abstract and first > 0 and "abstract" in [t.lower() for t in targets]:
        out.append({"start": 0, "end": first, "type": "abstract", "title": ""})
    parent_title = ""
    for i, h in enumerate(heads):
        level, title = len(h.group(1)), h.group(2)
        if level == 2:
            parent_title = title
        start = h.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        typ = classify(title, targets) or (classify(parent_title, targets) if level > 2 else None)
        if typ and end > start:
            out.append({"start": start, "end": end, "type": typ, "title": title})
    return out


def body_regions(text: str) -> list[dict[str, Any]]:
    """Every region of the text, headed or not, typed ``body`` (the fallback)."""
    heads = list(_HEADING.finditer(text))
    first = heads[0].start() if heads else len(text)
    out: list[dict[str, Any]] = []
    if first > 0:
        out.append({"start": 0, "end": first, "type": "body", "title": ""})
    for i, h in enumerate(heads):
        start = h.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        if end > start:
            out.append({"start": start, "end": end, "type": "body", "title": h.group(2)})
    return out


def _spans(text: str) -> list[tuple[int, int]]:
    from ragstack.ingestion.chunkers import sentence_spans
    return sentence_spans(text)


def cut_in_region(text: str, region: dict[str, Any], chars: int, min_frac: float, max_frac: float,
                  seed: Any, key: str) -> tuple[int, int] | None:
    """A sentence-aligned ``(start, end)`` in ``text`` inside ``region``, or None."""
    rs, re_ = region["start"], region["end"]
    sub = text[rs:re_]
    sents = []
    for a, b in _spans(sub):
        seg = sub[a:b]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg.rstrip())
        if trail > lead:
            sents.append((rs + a + lead, rs + a + trail))
    if not sents:
        return None
    lo, hi = chars * min_frac, chars * max_frac
    candidates = []
    for i in range(len(sents)):
        j = i
        while j < len(sents) and sents[j][1] - sents[i][0] < chars:
            j += 1
        if j == len(sents):
            j -= 1
        end = sents[j][1]
        if end - sents[i][0] > hi and j > i and sents[j - 1][1] - sents[i][0] >= lo:
            end = sents[j - 1][1]
        if lo <= end - sents[i][0] <= hi:
            candidates.append((sents[i][0], end))
    if not candidates:
        return None
    return candidates[int(seeded_unit(seed, "start", key) * len(candidates))]


def pick_passages(rec: dict[str, Any], pcfg: dict[str, Any], seed: Any) -> tuple[list[dict[str, Any]], str | None]:
    text = rec["text"]
    pmcid = rec["path"]
    meta = rec.get("metadata") or {}
    targets = list(pcfg["section_targets"])
    regs = regions(text, targets, has_abstract=bool(meta.get("abstract", True)))
    out = _pick_in(text, pmcid, regs, pcfg, seed) if regs else []
    why = None if out else ("no_fitting_passage" if regs else "no_target_region")
    if out or not pcfg.get("fallback_body"):
        for p in out:
            p["fallback"] = False
        return out, why
    fb = _pick_in(text, pmcid, body_regions(text), pcfg, seed)
    for p in fb:
        p["fallback"] = True
    return (fb, None) if fb else ([], why)


def _pick_in(text: str, pmcid: str, regs: list[dict[str, Any]], pcfg: dict[str, Any],
             seed: Any) -> list[dict[str, Any]]:
    types = sorted({r["type"] for r in regs}, key=lambda t: seeded_rank(seed, "type", pmcid, t))
    out: list[dict[str, Any]] = []
    used: list[tuple[int, int]] = []
    for want in range(int(pcfg["per_article"])):
        found = None
        for typ in types[want:] + types[:want]:
            regs_t = sorted([r for r in regs if r["type"] == typ],
                            key=lambda r: seeded_rank(seed, "region", pmcid, str(r["start"])))
            for r in regs_t:
                se = cut_in_region(text, r, int(pcfg["chars"]), float(pcfg["min_frac"]),
                                   float(pcfg["max_frac"]), seed, f"{pmcid}:{r['start']}:{want}")
                if se and not any(se[0] < b and a < se[1] for a, b in used):
                    found = (se, r)
                    break
            if found:
                break
        if not found:
            break
        (s, e), r = found
        used.append((s, e))
        passage = text[s:e]
        out.append({"passage_id": f"{pmcid}#p{want}", "pmcid": pmcid, "record_path": pmcid,
                    "start": s, "end": e, "sha256": sha256_text(passage), "text": passage,
                    "section_type": r["type"], "section_title": r["title"]})
    return out


def run(cfg: dict[str, Any]) -> int:
    require(cfg, "out_dir", "sample.seed")
    sample = read_jsonl(out_path(cfg, "sample.jsonl"))
    evals = {r["pmcid"]: r for r in sample if r["role"] == "eval"}
    seed = cfg["sample"]["seed"]
    rows, skipped, seen = [], [], set()
    for rec in iter_records(cfg):
        meta = rec.get("metadata") or {}
        pid = rec["path"]
        if meta.get("content_type") != "article" or pid not in evals or pid in seen:
            continue
        seen.add(pid)
        ps, why = pick_passages(rec, cfg["passages"], seed)
        st = {k: evals[pid].get(k) for k in ("journal_family", "length_band", "year_band")}
        for p in ps:
            rows.append({**p, **st})
        if why:
            skipped.append({"pmcid": pid, "reason": why, **st})
    for pid in sorted(set(evals) - seen):
        st = {k: evals[pid].get(k) for k in ("journal_family", "length_band", "year_band")}
        skipped.append({"pmcid": pid, "reason": "no_article_record", **st})
    rows.sort(key=lambda r: r["passage_id"])
    write_jsonl(out_path(cfg, "passages.jsonl"), rows)
    write_jsonl(out_path(cfg, "passages.skipped.jsonl"), skipped)
    from ragstack.ingestion.chunkers import segmenter_version
    write_json(out_path(cfg, "passages.summary.json"),
               {"n_eval_articles": len(evals), "n_passages": len(rows), "n_skipped_articles": len(skipped),
                "segmenter": segmenter_version(), "chars": cfg["passages"]["chars"]})
    log(f"passages: {len(rows)} passages, {len(skipped)} articles skipped")
    return 0
