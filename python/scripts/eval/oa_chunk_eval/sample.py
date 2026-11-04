"""``sample``: manifest -> stratified eval sample + disjoint background set.

Filter the manifest to ``include_pmcids`` (the bacteria/virus definition), normalise
journal names into families, stratify by journal family x length band (year reported,
not crossed), allocate the eval articles proportionally across the cells (largest
remainder), and pick inside each cell by a seeded hash rank. The background set is drawn
by the same hash rank from what is left, so it is disjoint by construction. Everything is
a pure function of (manifest, include list, config); there is no ``random`` state.

Config, ``sample.strata``:

- ``journal_family_map``: ``{family: [pattern, ...]}``. A pattern is a case-insensitive
  regular expression searched in the normalised journal name (NFKD with combining marks
  removed, so "Microbiología" -> "microbiologia"; lowercase, punctuation to spaces,
  single spaces). First family in file order wins. Unmatched names go to
  ``default_family`` (default ``"other"``).
- ``length_bands`` / ``year_bands``: ``[{"name", "min", "max"}, ...]`` on ``body_chars`` /
  ``year``; ``min`` inclusive, ``max`` exclusive, either may be omitted. A value that is
  missing or falls in no band is ``"unknown"``.

``sample.background_pool``: ``"included"`` (default: background comes from the filtered
set) or ``"manifest"`` (any manifest article not in the eval sample).
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import corpus_rel_path, iter_jsonl_stream, log, seeded_rank, write_json, write_jsonl
from .config import out_path, require

_NONWORD = re.compile(r"[^0-9a-z]+")


def fold_accents(text: str) -> str:
    """NFKD, then drop combining marks: "Microbiología" -> "Microbiologia"."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def normalise_journal(name: str | None) -> str:
    s = fold_accents(name or "").lower().replace("&", " and ")
    return " ".join(_NONWORD.sub(" ", s).split())


def journal_family(name: str | None, family_map: dict[str, list[str]], default: str = "other") -> str:
    norm = normalise_journal(name)
    if not norm:
        return "unknown"
    for fam, patterns in family_map.items():
        for pat in patterns if isinstance(patterns, list) else [patterns]:
            if re.search(pat, norm, flags=re.IGNORECASE):
                return fam
    return default


def band(value: Any, bands: list[dict[str, Any]]) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    for b in bands:
        lo, hi = b.get("min"), b.get("max")
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return str(b["name"])
    return "unknown"


def strata_of(row: dict[str, Any], strata_cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "journal_family": journal_family(row.get("journal_xml") or row.get("journal"),
                                         strata_cfg.get("journal_family_map") or {},
                                         strata_cfg.get("default_family") or "other"),
        "length_band": band(row.get("body_chars"), strata_cfg.get("length_bands") or []),
        "year_band": band(row.get("year"), strata_cfg.get("year_bands") or []),
    }


def allocate(cell_sizes: dict[tuple[str, str], int], n: int) -> dict[tuple[str, str], int]:
    """Proportional allocation with largest remainder; ties broken by cell key. Never
    allocates more than a cell holds (the overflow goes to the cells with room)."""
    total = sum(cell_sizes.values())
    if n > total:
        raise ValueError(f"asked for {n} eval articles but only {total} are eligible")
    alloc = dict.fromkeys(cell_sizes, 0)
    remaining = n
    while remaining > 0:
        room = {c: cell_sizes[c] - alloc[c] for c in cell_sizes if cell_sizes[c] - alloc[c] > 0}
        cap = sum(room.values())
        quotas = {c: remaining * room[c] / cap for c in room}
        base = {c: min(room[c], int(q)) for c, q in quotas.items()}
        given = sum(base.values())
        order = sorted(room, key=lambda c: (-(quotas[c] - int(quotas[c])), c))
        for c in order:
            if given >= remaining:
                break
            if base[c] < room[c]:
                base[c] += 1
                given += 1
        for c, k in base.items():
            alloc[c] += k
        remaining -= given
    return alloc


def draw(rows: list[dict[str, Any]], include: set[str] | None, cfg_sample: dict[str, Any]
         ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The pure sampler. Returns (sample rows, summary)."""
    seed = cfg_sample["seed"]
    strata_cfg = cfg_sample.get("strata") or {}
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = r.get("pmcid")
        if pid and pid not in by_id:
            by_id[pid] = r
    eligible = sorted(p for p in by_id if include is None or p in include)
    info = {p: strata_of(by_id[p], strata_cfg) for p in eligible}
    cells: dict[tuple[str, str], list[str]] = defaultdict(list)
    for p in eligible:
        cells[(info[p]["journal_family"], info[p]["length_band"])].append(p)
    for c in cells:
        cells[c].sort(key=lambda p: seeded_rank(seed, "eval", p))
    n_eval = int(cfg_sample["n_eval_articles"])
    alloc = allocate({c: len(v) for c, v in cells.items()}, n_eval)
    eval_ids = {p for c, v in cells.items() for p in v[:alloc[c]]}

    pool_kind = cfg_sample.get("background_pool") or "included"
    pool_src = eligible if pool_kind == "included" else sorted(by_id)
    pool = sorted((p for p in pool_src if p not in eval_ids), key=lambda p: seeded_rank(seed, "background", p))
    n_bg = int(cfg_sample.get("n_background") or 0)
    if n_bg > len(pool):
        raise ValueError(f"asked for {n_bg} background articles but only {len(pool)} remain")
    bg_ids = set(pool[:n_bg])

    out = []
    for p in sorted(eval_ids | bg_ids):
        r = by_id[p]
        st = info.get(p) or strata_of(r, strata_cfg)
        out.append({"pmcid": p, "role": "eval" if p in eval_ids else "background",
                    "journal": r.get("journal_xml") or r.get("journal"), "year": r.get("year"),
                    "body_chars": r.get("body_chars"), **st})
    summary = {
        "seed": seed, "n_manifest": len(by_id), "n_eligible": len(eligible),
        "n_eval": len(eval_ids), "n_background": len(bg_ids), "background_pool": pool_kind,
        "cells": {f"{f}|{lb}": {"eligible": len(cells[(f, lb)]), "eval": alloc[(f, lb)]}
                  for (f, lb) in sorted(cells)},
    }
    return out, summary


def read_include(path: str | Path) -> set[str]:
    out = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def run(cfg: dict[str, Any]) -> int:
    require(cfg, "out_dir", "manifest", "include_pmcids", "sample.seed", "sample.n_eval_articles")
    include = read_include(cfg["include_pmcids"])
    rows = list(iter_jsonl_stream(cfg["manifest"]))
    sample_rows, summary = draw(rows, include, cfg["sample"])
    write_jsonl(out_path(cfg, "sample.jsonl"), sample_rows)
    shard = [{"pmcid": r["pmcid"], "xml_path": corpus_rel_path(r["pmcid"])} for r in sample_rows]
    write_jsonl(out_path(cfg, "shard.jsonl"), shard)
    if cfg.get("corpus_root"):
        root = Path(cfg["corpus_root"])
        missing = sum(1 for s in shard if not (root / s["xml_path"]).exists())
        summary["missing_xml"] = missing
        if missing:
            log(f"sample: {missing} XML files not found under corpus_root")
    write_json(out_path(cfg, "sample.summary.json"), summary)
    log(f"sample: {summary['n_eval']} eval + {summary['n_background']} background "
        f"from {summary['n_eligible']} eligible; shard.jsonl xml_path is relative to corpus_root "
        f"(jats_extract.py --shard shard.jsonl --corpus <corpus_root>)")
    return 0
