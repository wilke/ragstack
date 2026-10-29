#!/usr/bin/env python
"""Glossary benchmark, gold labelling: build the judged sample (no model cost).

Committed form of round 1's scratch ``build_sample.py``, parameterised for round 2
(prereg §6). Every path comes in as an argument; corpus names are **aliases**
(e.g. ``O``, ``A``, ``P``), used in every document key ``<alias>-NN``.

    build_sample.py --out DIR \\
        --corpus O=SHARD.jsonl --corpus A=SHARD.jsonl \\
        --glossary O:rules=FILE --glossary O:qwen36p=FILE ... \\
        [--docs O=EVAL_DOC_IDS.txt ...] [--rules-arms rules] \\
        [--per-tercile 5] [--seed 261101|gold --seed-sep '|'] [--owner-items 40]

Writes, under ``--out`` (mode 0700, files 0600):

  sample.jsonl        one row per sampled document: key, corpus alias, tercile, the judged
                      text and the shuffled, arm-hidden item list (PRIVATE: document text)
  item_arms.json      item -> the arms that proposed it (PRIVATE: item text)
  owner_labels.csv    the owner's blind fill-in template (``--owner-items``; PRIVATE)
  manifest.json       seeds, rules, counts, sha256 of every input and output (counts only)

Rules (round 1's, unchanged):

* population per corpus = documents (restricted to ``--docs`` when given: the eval
  papers) where at least one arm proposed >= 1 entry; a ``--rules-arms`` arm
  contributes its rules half, every other arm its LLM half (a pure arm's file holds
  its proposals there);
* terciles = rank by the rules arm's rules-entry count, ties by a seeded key, cut at
  ``round(n/3)``, ``round(2n/3)``; ``--per-tercile`` per tercile, seeded; a short
  tercile gives what it has (shortfall recorded);
* items = distinct (canonical symbol, casefolded whitespace-collapsed definition);
  shown spelling = ``min()`` of the surface variants; order = a seeded shuffle;
* the judged text is what the LLM arms saw: the union of their windows
  (``windows(text, W, overlap, max)``); the rules arm saw it all;
* the owner's items: ``--owner-items`` items drawn uniformly over all sampled items,
  seeded (``<seed><sep>owner``), shown blind (no arm, no corpus alias beyond the key).

Seed streams: ``random.Random(sep.join([seed, purpose, alias, ...]))`` with purposes
``tie``, ``pick``, ``items``, ``owner``.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import gold_common as gc  # noqa: E402

DEFAULT_WINDOW_CHARS, DEFAULT_MAX_WINDOWS = 80_000, 4


def judged_span(text: str, window_chars: int, max_windows: int) -> tuple[str, bool]:
    from ragstack.ingestion.symbol_glossary_llm import WINDOW_OVERLAP, windows

    parts, truncated = windows(text, window_chars, WINDOW_OVERLAP, max_windows)
    if len(parts) == 1:
        return parts[0], truncated
    step = window_chars - WINDOW_OVERLAP
    end = step * (len(parts) - 1) + len(parts[-1])
    span = text[:end]
    assert all(span[i * step: i * step + window_chars] == p for i, p in enumerate(parts))
    return span, truncated


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values:
        k, sep, path = v.partition("=")
        if not sep or not k or not path:
            raise SystemExit(f"--{what} expects KEY=PATH, got {v!r}")
        out[k] = path
    return out


def owner_sample(rows: list[dict[str, Any]], n: int, seed: str, sep: str) -> list[tuple[str, str]]:
    """``n`` (doc key, item id) pairs drawn without replacement over every sampled item."""
    every = sorted((r["key"], it["id"]) for r in rows for it in r["items"])
    if n <= 0:
        return []
    return sorted(gc.seeded(seed, sep, "owner").sample(every, min(n, len(every))))


def main(argv: list[str] | None = None) -> int:
    from ragstack.ingestion.glossary_file import read_glossary_file
    from ragstack.ingestion.loaders import JsonlLoader
    from ragstack.ingestion.symbol_glossary import content_key

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus", action="append", required=True, help="ALIAS=SHARD.jsonl (repeat)")
    ap.add_argument("--glossary", action="append", required=True, help="ALIAS:ARM=GLOSSARY.jsonl (repeat)")
    ap.add_argument("--docs", action="append", default=[], help="ALIAS=FILE of eval doc ids, one per line")
    ap.add_argument("--rules-arms", default="rules", help="comma list of arms judged on their rules half")
    ap.add_argument("--tercile-arm", default="rules", help="the arm whose rules-entry count makes the terciles")
    ap.add_argument("--per-tercile", type=int, default=5)
    ap.add_argument("--seed", required=True, help="seed root, e.g. 261101|gold")
    ap.add_argument("--seed-sep", default="|")
    ap.add_argument("--owner-items", type=int, default=0)
    ap.add_argument("--window-chars", type=int, default=DEFAULT_WINDOW_CHARS)
    ap.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    shards = _pairs(args.corpus, "corpus")
    doc_lists = _pairs(args.docs, "docs")
    gl: dict[str, dict[str, str]] = {}
    for k, path in _pairs(args.glossary, "glossary").items():
        alias, sep, arm = k.partition(":")
        if not sep or alias not in shards:
            raise SystemExit(f"--glossary key must be ALIAS:ARM with a --corpus ALIAS, got {k!r}")
        gl.setdefault(alias, {})[arm] = path
    arms = sorted({a for v in gl.values() for a in v})
    if any(set(v) != set(arms) for v in gl.values()):
        raise SystemExit("every corpus needs a glossary file for every arm")
    if args.tercile_arm not in arms:
        raise SystemExit(f"--tercile-arm {args.tercile_arm!r} is not an arm")
    rules_arms = {a for a in args.rules_arms.split(",") if a}
    seed, sep = args.seed, args.seed_sep

    inputs: dict[str, str] = {}
    sample_rows: list[dict[str, Any]] = []
    item_arms: dict[str, dict[str, Any]] = {}
    counts: dict[str, dict[str, Any]] = {}
    for alias in sorted(shards):
        files = {}
        for arm in arms:
            p = Path(gl[alias][arm])
            inputs[f"{alias}:{arm}"] = gc.sha256_file(p)
            files[arm] = read_glossary_file(p)
        shard = Path(shards[alias])
        inputs[f"{alias}:shard"] = gc.sha256_file(shard)
        ids = list(files[args.tercile_arm].rows)
        if not all(set(f.rows) == set(ids) for f in files.values()):
            raise SystemExit(f"{alias}: the arms' files hold different documents")
        if alias in doc_lists:
            wanted = [x.strip() for x in Path(doc_lists[alias]).read_text(encoding="utf-8").splitlines() if x.strip()]
            inputs[f"{alias}:docs"] = gc.sha256_file(doc_lists[alias])
            missing = sorted(set(wanted) - set(ids))
            if missing:
                raise SystemExit(f"{alias}: {len(missing)} eval doc id(s) not in the glossary files")
            ids = [d for d in ids if d in set(wanted)]

        def proposals(doc_id: str, files: dict = files) -> dict[str, list[tuple[str, str, int]]]:
            res: dict[str, list[tuple[str, str, int]]] = {}
            for arm in arms:
                row = files[arm].rows[doc_id]
                g = row.rules if arm in rules_arms else row.llm
                res[arm] = [] if g is None else [(e.symbol, d.text, d.offset) for e in g.entries
                                                  for d in e.definitions]
            return res

        props = {d: proposals(d) for d in ids}
        pop = [d for d in ids if any(props[d][a] for a in arms)]
        size = {d: len(files[args.tercile_arm].rows[d].rules.entries) for d in pop}
        groups3 = gc.terciles(pop, size, gc.seeded(seed, sep, "tie", alias))
        chosen: list[tuple[int, str]] = []
        short = {}
        for ti, group in enumerate(groups3):
            pick = gc.seeded(seed, sep, "pick", alias, ti).sample(sorted(group), min(args.per_tercile, len(group)))
            if len(pick) < args.per_tercile:
                short[f"T{ti + 1}"] = args.per_tercile - len(pick)
            chosen += [(ti + 1, d) for d in pick]
        want = {d for _, d in chosen}
        docs = {d.id: d for d in JsonlLoader().load(str(shard)) if d.id in want}
        terc_info = {}
        for ti, group in enumerate(groups3):
            rc = [size[d] for d in group]
            terc_info[f"T{ti + 1}"] = {"population": len(group), "rules_entries_min": min(rc) if rc else None,
                                       "rules_entries_max": max(rc) if rc else None,
                                       "sampled": sum(1 for t, _ in chosen if t == ti + 1)}
        c_items, beyond = 0, 0
        arm_share: Counter[str] = Counter()
        for n_doc, (ti, doc_id) in enumerate(chosen):
            doc = docs[doc_id]
            key = content_key(doc.content)
            for arm in arms:
                if files[arm].rows[doc_id].text_sha256 != key:
                    raise SystemExit(f"{alias}: text identity failed for a sampled document ({arm})")
            span, truncated = judged_span(doc.content, args.window_chars, args.max_windows)
            groups = gc.group_items(props[doc_id])
            order = sorted(groups)
            gc.seeded(seed, sep, "items", alias, doc_id).shuffle(order)
            items, amap = [], {}
            for i, gk in enumerate(order):
                g = groups[gk]
                iid = f"i{i + 1:02d}"
                items.append({"id": iid, "symbol": gk[0], "definition": min(g["surface"])})
                past = all(o >= len(span) for o in g["offsets"])
                beyond += past
                amap[iid] = {"symbol": gk[0], "definition_norm": gk[1],
                             "arms": sorted(g["arms"], key=arms.index),
                             "rendered_by": [a for a in arms if gk in {
                                 (e.symbol, gc.norm_def(d.text))
                                 for e in files[a].rows[doc_id].merged.entries for d in e.definitions}],
                             "surface_variants": sorted(g["surface"]), "offsets": sorted(g["offsets"]),
                             "beyond_judged_text": past}
                arm_share.update(g["arms"])
            dkey = f"{alias}-{n_doc + 1:02d}"
            sample_rows.append({"key": dkey, "corpus": alias, "doc_id": doc_id, "tercile": ti,
                                "rules_entries": size[doc_id], "text_sha256": key,
                                "text_chars": len(doc.content), "judged_chars": len(span),
                                "truncated": truncated, "judged_text": span, "items": items})
            item_arms[dkey] = {"doc_id": doc_id, "items": amap}
            c_items += len(items)
        counts[alias] = {
            "documents": len(ids), "population_any_item": len(pop), "terciles": terc_info,
            "sampled": len(chosen), "shortfall": short, "items": c_items,
            "items_by_arm": {a: arm_share[a] for a in arms},
            "arm_share": {a: round(arm_share[a] / c_items, 4) if c_items else None for a in arms},
            "items_beyond_judged_text": beyond,
            "truncated_docs": sum(1 for r in sample_rows if r["corpus"] == alias and r["truncated"]),
        }

    owner = owner_sample(sample_rows, args.owner_items, seed, sep)
    owner_set = set(owner)
    gc.write_private(out / "sample.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample_rows))
    gc.write_private(out / "item_arms.json", json.dumps(item_arms, ensure_ascii=False, indent=1))
    written = ["sample.jsonl", "item_arms.json"]
    if owner:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["doc", "item", "symbol", "definition", "label (correct|partial|wrong|not-a-symbol)", "note"])
        for r in sample_rows:
            for it in r["items"]:
                if (r["key"], it["id"]) in owner_set:
                    w.writerow([r["key"], it["id"], it["symbol"], it["definition"], "", ""])
        gc.write_private(out / "owner_labels.csv", buf.getvalue())
        written.append("owner_labels.csv")
    tot_arm: Counter[str] = Counter()
    for c in counts.values():
        tot_arm.update(c["items_by_arm"])
    tot_items = sum(c["items"] for c in counts.values())
    manifest = {
        "what": "glossbench gold labelling: sample (no model cost)",
        "seed": seed, "seed_sep": sep,
        "seed_derivation": "random.Random(sep.join([seed, purpose, alias, ...])); purposes tie, pick, items, owner",
        "arms": arms, "rules_arms": sorted(rules_arms), "tercile_arm": args.tercile_arm,
        "per_tercile": args.per_tercile, "window_chars": args.window_chars, "max_windows": args.max_windows,
        "counts": counts,
        "totals": {"documents": len(sample_rows), "items": tot_items, "items_by_arm": dict(tot_arm),
                   "owner_items": len(owner)},
        "inputs_sha256": inputs,
        "outputs_sha256": {f: gc.sha256_file(out / f) for f in written},
        "code_sha256": {"build_sample.py": gc.sha256_file(Path(__file__)),
                        "gold_common.py": gc.sha256_file(_HERE / "gold_common.py")},
    }
    try:
        from ragstack.provenance import experiment_provenance

        manifest["provenance"] = experiment_provenance()
    except Exception as e:  # noqa: BLE001 - provenance never blocks the sample
        manifest["provenance"] = {"error": type(e).__name__}
    gc.write_private(out / "manifest.json", json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in ("counts", "totals")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
