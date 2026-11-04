#!/usr/bin/env python3
"""Round-3 planning counts (offline; counts only, never text).

For each corpus (O = oa, A = asm, P = openchia) and each pure arm file of round 2
(rules, qwen36p, qwen38p, scoutp), count per chunk:

  vs        arm-blind: the chunk states a value for a symbol-like token
            (_glossbench_r2.value_statements, STAT_TOKENS stoplist)        -> population V
  msig      arm-blind: no value statement, but the chunk mentions a token of
            SIGMA(paper) = union of the paper's value-statement tokens      -> population M
  msig_num  M and the chunk prints a number                                -> population M_num
  value[a]  arm a renders a non-empty field under the served value-only rule
  ment[a]   arm a renders a non-empty field under mention attachment
            (attach_only_with_value=False, every other cap as the file's)
  doc[a]    arm a's document glossary has >= 1 entry (document-level attachment)
  ment_cap[a] mention rendering hit the 12-entry or 1200-char cap

Rendering replicates Glossary.for_chunk line by line but canonicalises each chunk
once (for_chunk re-canonicalises per call); equality with for_chunk is asserted on
a seeded sample of chunks per corpus.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "python")
sys.path.insert(0, "python/scripts/eval")

import _glossbench_r2 as g  # noqa: E402
from ragstack.ingestion.glossary_file import read_glossary_file  # noqa: E402
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    Glossary, GlossaryConfig, canonical_text, display_form, symbol_pattern, value_pattern,
    _SYMBOL, is_specific_symbol,
)

R2 = Path(os.environ.get("GLOSSBENCH_R2_INPUTS", "glossbench-round2-inputs"))  # private round-2 inputs (chunk exports, arm files)
import re  # noqa: E402

_SYMBOL_RE = re.compile(_SYMBOL)

CORPORA = {
    "O": ("oa", "path_root", 32765),
    "A": ("asm", "doc_id", 27418),
    "P": ("openchia", "doc_id", 14456),
}
ARMS = ("rules", "qwen36p", "qwen38p", "scoutp")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("counts.json")


def render(gl: Glossary, canon: str, cfg: GlossaryConfig, start_char: int | None) -> tuple[int, bool]:
    """(lines rendered, capped?) exactly as Glossary.for_chunk decides them."""
    if not gl.entries or not canon:
        return 0, False
    where = start_char if cfg.positional else None
    n = 0
    size = 0
    capped = False
    for entry in gl.entries:
        if n >= cfg.max_entries_per_chunk:
            capped = True
            break
        pattern = value_pattern(entry.symbol) if cfg.attach_only_with_value else symbol_pattern(entry.symbol)
        if pattern.search(canon) is None:
            continue
        defs = "; ".join(d.text for d in entry.definition_for(where))
        line = display_form(f"{entry.symbol} — {defs}")
        extra = len(line) + (1 if n else 0)
        if size + extra > cfg.max_chars_per_chunk:
            capped = True
            break
        n += 1
        size += extra
    return n, capped


def quantiles(xs: list[int]) -> dict[str, float]:
    if not xs:
        return {}
    s = sorted(xs)
    def q(p: float) -> float:
        i = int(round(p * (len(s) - 1)))
        return float(s[i])
    return {"n": len(s), "min": float(s[0]), "p10": q(.1), "p25": q(.25), "median": q(.5),
            "p75": q(.75), "p90": q(.9), "max": float(s[-1]), "mean": sum(s) / len(s), "sum": float(sum(s))}


def ge_table(xs: list[int], ks=(1, 2, 3, 5, 8, 10, 15, 20, 30)) -> dict[str, int]:
    return {f">={k}": sum(1 for x in xs if x >= k) for k in ks}


def main() -> None:
    result: dict = {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "corpora": {}}
    for set_, (alias, paper_rule, expected) in CORPORA.items():
        t0 = time.time()
        rows = g.read_chunks([R2 / "exports" / f"chunks-{alias}.jsonl"])
        assert len(rows) == expected, (set_, len(rows))
        eval_docs = set((R2 / "cfg" / f"eval-docs-{alias}.txt").read_text().split())
        paper = {c.chunk_id: g.paper_of(c, paper_rule) for c in rows}
        papers = sorted(set(paper.values()))
        eval_papers = {paper[c.chunk_id] for c in rows if c.doc_id in eval_docs}
        canon = {c.chunk_id: canonical_text(c.content) for c in rows}
        vs_tokens = {c.chunk_id: g.value_statements(c.content) for c in rows}
        has_num = {c.chunk_id: g.has_number(c.content) for c in rows}
        sigma: dict[str, set[str]] = defaultdict(set)
        for c in rows:
            sigma[paper[c.chunk_id]].update(vs_tokens[c.chunk_id])
        # arm-blind populations
        is_vs = {cid: bool(t) for cid, t in vs_tokens.items()}
        msig = {}
        anysym = {}
        for c in rows:
            cid = c.chunk_id
            cn = canon[cid]
            if is_vs[cid]:
                msig[cid] = False
            else:
                msig[cid] = any(symbol_pattern(t).search(cn) for t in sigma[paper[cid]])
            anysym[cid] = any(is_specific_symbol(m.group("sym")) for m in _SYMBOL_RE.finditer(cn))
        pop = {cid: ("V" if is_vs[cid] else "M_num" if (msig[cid] and has_num[cid])
                     else "M_nonum" if msig[cid] else "R") for cid in canon}
        # per arm
        arm_block = {}
        per_paper_reach: dict[str, dict[str, Counter]] = {}
        rng = random.Random(f"r3plan|{set_}")
        sample = rng.sample(rows, 400)
        for arm in ARMS:
            gfile = read_glossary_file(R2 / "arms" / f"{alias}.{arm}.jsonl")
            assert gfile.config.attach_only_with_value
            cfg_v = gfile.config
            cfg_m = replace(cfg_v, attach_only_with_value=False)
            cfg_m_big = replace(cfg_m, max_entries_per_chunk=100000, max_chars_per_chunk=10**9)
            gl = {d: r.merged for d, r in gfile.rows.items()}
            counts = Counter()
            by_pop = {m: Counter() for m in ("value", "mention", "doc")}
            pp = {m: Counter() for m in ("value", "mention", "doc", "value_V", "mention_V", "mention_Mnum")}
            lines_hist = {"value": Counter(), "mention": Counter()}
            uncapped_lines = Counter()
            for c in rows:
                cid = c.chunk_id
                glo = gl.get(c.doc_id)
                if glo is None:
                    counts["doc_missing"] += 1
                    continue
                nv, _ = render(glo, canon[cid], cfg_v, c.start_char)
                nm, capped = render(glo, canon[cid], cfg_m, c.start_char)
                nmb, _ = render(glo, canon[cid], cfg_m_big, c.start_char)
                dl = bool(glo.entries)
                p = paper[cid]
                lines_hist["value"][min(nv, 13)] += 1
                lines_hist["mention"][min(nm, 13)] += 1
                uncapped_lines[min(nmb, 40)] += 1
                if nv:
                    counts["value"] += 1; by_pop["value"][pop[cid]] += 1; pp["value"][p] += 1
                    if pop[cid] == "V": pp["value_V"][p] += 1
                if nm:
                    counts["mention"] += 1; by_pop["mention"][pop[cid]] += 1; pp["mention"][p] += 1
                    if pop[cid] == "V": pp["mention_V"][p] += 1
                    if pop[cid] == "M_num": pp["mention_Mnum"][p] += 1
                if capped:
                    counts["mention_capped"] += 1
                if dl:
                    counts["doc"] += 1; by_pop["doc"][pop[cid]] += 1; pp["doc"][p] += 1
                if nv and not nm:
                    counts["value_not_mention_BUG"] += 1
                if c.doc_id in eval_docs:
                    counts["eval_chunks"] += 1
                    if nv: counts["eval_value"] += 1
                    if nm: counts["eval_mention"] += 1
                    if nv and pop[cid] == "V": counts["eval_value_V"] += 1
                    if nm and pop[cid] == "M_num": counts["eval_mention_Mnum"] += 1
            # fidelity check against the library's own renderer
            for c in sample:
                glo = gl.get(c.doc_id)
                if glo is None:
                    continue
                for cfg in (cfg_v, cfg_m):
                    ref = glo.for_chunk(c.content, cfg, start_char=c.start_char)
                    n_ref = len(ref.split("\n")) if ref else 0
                    n_me, _ = render(glo, canon[c.chunk_id], cfg, c.start_char)
                    assert n_ref == n_me, (set_, arm, c.chunk_id, n_ref, n_me)
            per_paper_reach[arm] = pp
            docs_with_entries = sum(1 for r in gfile.rows.values() if r.merged.entries)
            arm_block[arm] = {
                "file_sha256": g.file_sha(R2 / "arms" / f"{alias}.{arm}.jsonl"),
                "docs": len(gfile.rows), "docs_with_entries": docs_with_entries,
                "entries_total": sum(len(r.merged.entries) for r in gfile.rows.values()),
                "chunks_with_field": {m: counts[m] for m in ("value", "mention", "doc")},
                "chunks_with_field_share": {m: counts[m] / len(rows) for m in ("value", "mention", "doc")},
                "chunks_with_field_by_population": {m: dict(by_pop[m]) for m in by_pop},
                "mention_capped_chunks": counts["mention_capped"],
                "value_not_mention": counts["value_not_mention_BUG"],
                "lines_per_chunk_hist": {m: dict(sorted(lines_hist[m].items())) for m in lines_hist},
                "mention_uncapped_lines_hist": dict(sorted(uncapped_lines.items())),
                "eval25": {k: counts[k] for k in ("eval_chunks", "eval_value", "eval_mention",
                                                  "eval_value_V", "eval_mention_Mnum")},
                "per_paper": {
                    m: {"quantiles": quantiles([pp[m][p] for p in papers]),
                        "papers_ge": ge_table([pp[m][p] for p in papers])} for m in pp},
            }
        # arm-blind per-paper densities
        dens = {k: Counter() for k in ("V", "M_num", "M_nonum", "R", "chunks", "anysym")}
        for c in rows:
            p = paper[c.chunk_id]
            dens[pop[c.chunk_id]][p] += 1
            dens["chunks"][p] += 1
            if anysym[c.chunk_id]:
                dens["anysym"][p] += 1
        union_reach = {}
        for mode in ("value", "mention"):
            # S1-style: reached by >= 1 local arm, within V (value) or within V u M_num (mention)
            pass
        # union across arms per chunk (needs a second pass; cheap): recompute booleans
        union = {"value_V": Counter(), "mention_V": Counter(), "mention_Mnum": Counter(),
                 "any_arm_value": 0, "any_arm_mention": 0, "any_arm_mention_Mnum": 0, "any_arm_value_V": 0}
        gls = {arm: {d: r.merged for d, r in read_glossary_file(R2 / "arms" / f"{alias}.{arm}.jsonl").rows.items()}
               for arm in ARMS}
        cfgs = {arm: read_glossary_file(R2 / "arms" / f"{alias}.{arm}.jsonl").config for arm in ARMS}
        for c in rows:
            cid = c.chunk_id
            v = any(render(gls[a][c.doc_id], canon[cid], cfgs[a], c.start_char)[0] for a in ARMS if c.doc_id in gls[a])
            m = any(render(gls[a][c.doc_id], canon[cid], replace(cfgs[a], attach_only_with_value=False), c.start_char)[0]
                    for a in ARMS if c.doc_id in gls[a])
            p = paper[cid]
            if v:
                union["any_arm_value"] += 1
                if pop[cid] == "V":
                    union["any_arm_value_V"] += 1; union["value_V"][p] += 1
            if m:
                union["any_arm_mention"] += 1
                if pop[cid] == "V":
                    union["mention_V"][p] += 1
                if pop[cid] == "M_num":
                    union["any_arm_mention_Mnum"] += 1; union["mention_Mnum"][p] += 1
        per_paper_table = {
            p: {"chunks": dens["chunks"][p], "V": dens["V"][p], "M_num": dens["M_num"][p],
                "M_nonum": dens["M_nonum"][p], "anysym": dens["anysym"][p],
                "S1_value_union": union["value_V"][p], "S1_mention_V_union": union["mention_V"][p],
                "S1_mention_Mnum_union": union["mention_Mnum"][p],
                **{f"{a}_{m}": per_paper_reach[a][m][p] for a in ARMS
                   for m in ("value_V", "mention_V", "mention_Mnum", "mention")},
                "eval25": p in eval_papers}
            for p in papers}
        result["corpora"][set_] = {
            "alias": alias, "paper_key": paper_rule, "chunks": len(rows), "documents": len({c.doc_id for c in rows}),
            "papers": len(papers), "eval_papers": len(eval_papers),
            "population": dict(Counter(pop.values())),
            "population_share": {k: v / len(rows) for k, v in Counter(pop.values()).items()},
            "chunks_mentioning_any_specific_symbol": sum(anysym.values()),
            "papers_with_sigma": sum(1 for p in papers if sigma[p]),
            "sigma_size_quantiles": quantiles([len(sigma[p]) for p in papers]),
            "per_paper_density": {
                k: {"quantiles": quantiles([dens[k][p] for p in papers]), "papers_ge": ge_table([dens[k][p] for p in papers])}
                for k in ("V", "M_num", "M_nonum", "anysym")},
            "union_reach": {k: (v if not isinstance(v, Counter) else None) for k, v in union.items() if not isinstance(v, Counter)},
            "union_reach_per_paper": {
                k: {"quantiles": quantiles([union[k][p] for p in papers]), "papers_ge": ge_table([union[k][p] for p in papers])}
                for k in ("value_V", "mention_V", "mention_Mnum")},
            "eval25_density": {k: sum(dens[k][p] for p in eval_papers) for k in ("V", "M_num", "M_nonum")},
            "arms": arm_block,
            "seconds": round(time.time() - t0, 1),
        }
        # private per-paper table (ids are opaque doc ids / PMC ids: keep it in scratch, not in the record)
        (OUT.parent / f"per-paper-{set_}.json").write_text(json.dumps(per_paper_table, indent=0, sort_keys=True))
        OUT.write_text(json.dumps(result, indent=1, sort_keys=True))
        print(set_, "done", round(time.time() - t0, 1), "s", flush=True)


if __name__ == "__main__":
    main()
