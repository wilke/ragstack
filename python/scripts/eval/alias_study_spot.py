#!/usr/bin/env python
"""Alias study (OA-1000): the owner's two spot-check sheets (DRAFT prereg v3 §13.4, §13.5, §5.5).

    alias_study_spot.py draw-records --records ACCEPTED.jsonl --chunk-export BASE.jsonl --out-dir DIR
    alias_study_spot.py draw-pairs   --judge-config JUDGE.json --out-dir DIR
    alias_study_spot.py ingest       --key KEY.json --sheet FILLED.json --out OUT \\
                                     (--records ACCEPTED.jsonl --chunk-export BASE.jsonl | --judge-config JUDGE.json)

**draw-records (§13.4).** 40 of the 900 balanced records (the record file must be the frozen one,
:func:`alias_study_common.verify_record_set`), the first 40 in the order
``sha256(261101|alias|spot|records|<record id>)`` ascending (§5.5; the record id is the named
part). The draw reads record ids only, never the kind. Each sheet item carries what the owner
needs to mark the record *valid* / *invalid* and nothing that names its kind (G / C), arm, paper
or id: the gold passage's paper title, the glossary entry the record was drafted for (every G
and C record has one), the question, the drafted answer and quote, and the gold passage.

**draw-pairs (§13.5, R15).** 100 judged pairs from the labels **committed in stage A** (§5.7):
the commit must verify (:func:`alias_study_judge.verify_marker`; in the main run its public
record must be tracked at HEAD), and only the committed bytes of ``final.jsonl`` are read, so a
stage-B label can never enter. A pilot judge directory is refused (its labels never reach the
main run, §5.6). Pairs are stratified by the final label (:func:`alias_study_stats.spot_stratum`): *answers*
where C and E agreed (50: base 17 / neighbour 17 / random 16), *not* where they agreed (33: 11 /
11 / 11), *tiebroken* where the Argon label is final (17: 6 / 6 / 5); an unjudged pair is in no
stratum. The arm split is equal with the remainder to base, then neighbour; a cell with fewer
pairs than its share is taken whole and its shortfall goes to the other arms of the stratum
(:func:`alias_study_judge.drift_allocation`, the same rule), a stratum's shortfall to *answers*,
then to *not* (:func:`stratum_totals`). Within a cell the first pairs in the order
``sha256(261101|alias|spot|judge|<stratum>|<arm>|<pair id>)`` are taken. An item shows exactly
what the judge was shown (``alias-judge/2``): the passage paper's title, the short forms that
paper defines that occur in the passage, the passage, the term and the question; never the
label, stratum, arm, leg, retriever, glossary arm or any id. The sheet is ordered by
``sha256(261101|alias|spot|judge|sheet|<pair id>)``, so its order says nothing about the stratum.
The glossary files and chunk exports the judge config names must be the ones the stage-A
registry was built from (the committed ``pairs-manifest.json``, ``inputs_sha256``), so the
definitions on the sheet are the judge's; the check is recorded in the key.

**The sheet and its key.** Each draw writes ``spot-<kind>.sheet.json`` (PRIVATE, the only file the
owner sees: items with an empty ``label`` and ``note``) and ``spot-<kind>.key.json`` (PRIVATE,
never shown to the rater: item -> record id or pair id, the draw's counts, the seed, the sheet's
sha256 and the input sha256s). The sheet's sha256 is that of its bytes, which are canonical JSON
(:func:`canonical`). Both go outside the repository.

**ingest re-derives the draw; it never trusts the key.** The sheet is rebuilt from its sources,
the frozen record file and the chunk exports (*records*) or the verified stage-A commit and the
judge config's inputs (*judge*), by the same functions the draw used, and its sha256 must equal
the key's. The filled sheet is accepted only when, with every ``label`` and ``note`` blanked
again, its canonical JSON has that sha256 too (same items, same text, same order; only ``label``
and ``note`` may change), and every item carries a label. Each label is attributed to the record
or pair the re-derived draw puts at that item, and the key must name the same ones: a key whose
ids were swapped, or a key and sheet edited together, is refused. Output, exactly what
``alias_study_stats.py`` reads under ``spot_checks``: *records* a JSON ``{valid, n,
sheet_sha256}`` with n = 40 (counts only, so it may be written where §13.4 commits it, in the
repository too); *judge* a JSONL of ``{pair_id, owner_label}`` (1 *answers*, 0 *not*), private
and refused inside the repository.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_stats as ast  # noqa: E402

REPO = _HERE.parents[2]
SHEET_SCHEMA = {"records": "alias-study-spot-records-sheet/1", "judge": "alias-study-spot-judge-sheet/1"}
KEY_SCHEMA = "alias-study-spot-key/1"
LABELS = {"records": {"valid": 1, "invalid": 0}, "judge": {"answers": 1, "not": 0}}
FILL_FIELDS = ("label", "note")
#: §13.4 / §13.5: the sizes and strata the statistics read (alias_study_stats)
SPOT_RECORDS_N = ast.SPOT_RECORDS_N
SPOT_JUDGE_N = ast.SPOT_JUDGE_N
SPOT_STRATA = ast.SPOT_STRATA
#: §13.5 (R15): the nominal stratum sizes; the arm split is alias_study_judge.drift_allocation's
NOMINAL = {"answers": 50, "not": 33, "tiebroken": 17}
RECORD_ITEM_FIELDS = ("item", "title", "glossary_entry", "question", "answer", "quote", "passage", *FILL_FIELDS)
PAIR_ITEM_FIELDS = ("item", "title", "definitions", "passage", "term", "question", *FILL_FIELDS)
INSTRUCTIONS = {
    "records": "PRIVATE. For each item set label to 'valid' or 'invalid' (round 3's layer-3 reading: the question "
               "asks with the glossary entry's term, stands alone, has one reading, and the quote from the passage "
               "states the answer). Use note for anything else. Change nothing but label and note.",
    "judge": "PRIVATE. For each item set label to 'answers' if the passage answers the question, else 'not' (the "
             "judge's question, alias-judge/2: the short forms listed are the passage paper's own definitions). "
             "Use note for anything else. Change nothing but label and note.",
}


# ------------------------------------------------------------------ files

def canonical(obj: Any) -> bytes:
    """The bytes of a sheet: its sha256 is the sheet's identity (module docstring)."""
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def refuse_in_repo(path: Path, what: str) -> None:
    p = path.resolve()
    repo = REPO.resolve()
    if p == repo or repo in p.parents:
        raise SystemExit(f"{what} is inside the repository; the spot-check files are private and live outside it")


def write_private(path: Path, data: bytes, what: str) -> None:
    """Write once: an existing file with other bytes is refused (a sheet is drawn once)."""
    refuse_in_repo(path, what)
    if path.exists() and path.read_bytes() != data:
        raise SystemExit(f"{path.name} exists with other content; a sheet is drawn once (remove it deliberately)")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def write_key(path: Path, key: Mapping[str, Any], what: str) -> None:
    """The key, written with its sheet. A re-draw (the same sheet) keeps an existing key whose
    content is the same but for ``provenance``; any other difference is refused."""
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "provenance"} == {k: v for k, v in key.items() if k != "provenance"}:
            return
    write_private(path, canonical(key), what)


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance()
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ records (§13.4)

def draw_record_ids(balanced_ids: Sequence[str], n: int = SPOT_RECORDS_N) -> list[str]:
    """The first ``n`` record ids in the order ``sha256(261101|alias|spot|records|<id>)``."""
    ids = sorted(set(balanced_ids), key=lambda i: (asc.seeded_key(asc.SEEDS["spot_records"], i), i))
    if len(ids) < n:
        raise SystemExit(f"{len(ids)} balanced records; the record spot check draws {n} (§13.4)")
    return ids[:n]


def record_item(n: int, rec: Mapping[str, Any], chunks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """One blind item: no kind, arm, paper or id (module docstring)."""
    import glossbench_alias_checks as ak

    gold = rec["gold_chunks"][0]
    ch = chunks.get(gold)
    if ch is None:
        raise SystemExit(f"sheet item {n}: the gold chunk is in no chunk export given")
    for k in ("answer", "quote"):
        if not str(rec.get(k) or "").strip():
            raise SystemExit(f"sheet item {n}: the record has no {k!r}; the owner reads it to judge validity")
    entry = {"alias": rec["alias"], "primary": rec["primary"], "alias_kind": rec.get("alias_kind")}
    return {"item": f"r{n:02d}", "title": ch.get("title") or "", "glossary_entry": ak.glossary_line(entry),
            "question": rec["question"], "answer": rec["answer"], "quote": rec["quote"], "passage": ch["content"],
            "label": None, "note": ""}


def records_sheet(records: Path, exports: Sequence[Path]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """The records sheet from its sources (module docstring): ``(sheet, record ids in item order,
    the record-set identity)``. The draw and the ingest both call it."""
    recs = asc.load_records(records)
    ident = asc.verify_record_set(records, recs)
    bal, _ = asc.split_records(recs)
    by_id = {r["record_id"]: r for r in bal}
    picked = draw_record_ids(list(by_id))
    chunks = asc.read_chunk_exports(exports)
    items = [record_item(i, by_id[rid], chunks) for i, rid in enumerate(picked, 1)]
    sheet = {"schema": SHEET_SCHEMA["records"], "kind": "records", "labels": list(LABELS["records"]),
             "instructions": INSTRUCTIONS["records"], "n": len(items), "items": items}
    return sheet, picked, ident


def draw_records(records: Path, exports: Sequence[Path], out_dir: Path) -> dict[str, Any]:
    refuse_in_repo(out_dir, "--out-dir")
    sheet, picked, ident = records_sheet(records, exports)
    items = sheet["items"]
    data = canonical(sheet)
    sha = asc.sha_text(data.decode("utf-8"))
    key = {"schema": KEY_SCHEMA, "kind": "records", "sheet_sha256": sha, "n": len(items),
           "seed": asc.SEEDS["spot_records"],
           "draw_rule": "the first 40 balanced record ids in the order sha256('<seed>|<record id>') ascending",
           "items": {it["item"]: rid for it, rid in zip(items, picked, strict=True)},
           "record_set": {k: ident[k] for k in ("accepted_sha256", "balanced_sha256", "balanced")},
           "inputs": {"records": asc.sha256_file(records),
                      "chunk_exports": [asc.sha256_file(p) for p in exports]},
           "provenance": provenance(),
           "note": "PRIVATE blinding key: never shown to the rater."}
    sp, kp = out_dir / "spot-records.sheet.json", out_dir / "spot-records.key.json"
    write_private(sp, data, "the records sheet")
    write_key(kp, key, "the records key")
    return {"sheet": str(sp), "key": str(kp), "sheet_sha256": sha, "n": len(items)}


# ------------------------------------------------------------------ judged pairs (§13.5)

#: §13.5: the stratum of a final label, as the statistics compute it
spot_stratum = ast.spot_stratum


def stratum_totals(sizes: Mapping[str, int]) -> dict[str, int]:
    """§13.5: the pairs each stratum gives, from the pairs it has. A stratum short of its nominal
    size gives what it has; *tiebroken*'s shortfall goes to *answers*, then to *not*;
    *not*'s to *answers*; *answers*'s to *not*. *tiebroken* never receives a shortfall: it gives
    at most its 17, so the sheet is smaller than 100 whenever *answers* and *not* together cannot
    make up the rest, even while *tiebroken* still holds pairs it does not give."""
    s_a, s_n, s_t = (int(sizes.get(k, 0)) for k in SPOT_STRATA)
    t = min(NOMINAL["tiebroken"], s_t)
    short_t = NOMINAL["tiebroken"] - t
    short_n = max(0, NOMINAL["not"] - s_n)
    a = min(s_a, NOMINAL["answers"] + short_t + short_n)
    n = min(s_n, SPOT_JUDGE_N - a - t)
    return {"answers": a, "not": n, "tiebroken": t}


def allocate(sizes: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, int]]:
    """stratum -> arm -> pairs drawn (module docstring): :func:`stratum_totals`, then each
    stratum's count split across the arms by :func:`alias_study_judge.drift_allocation` (equal,
    the remainder to base then neighbour, a short cell's shortfall to the stratum's other arms)."""
    import alias_study_judge as aj

    tot = stratum_totals({s: sum(int(v) for v in (sizes.get(s) or {}).values()) for s in SPOT_STRATA})
    return {s: aj.drift_allocation(tot[s], dict(sizes.get(s) or {})) for s in SPOT_STRATA}


def draw_pairs_from(final: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The drawn final rows (stratum and arm attached) and the counts. Every row must be a
    stage-A row; an unjudged pair is in no stratum."""
    seed = asc.SEEDS["spot_judge"]
    cells: dict[str, dict[str, list[Mapping[str, Any]]]] = {s: {a: [] for a in asc.ARM_GROUPS} for s in SPOT_STRATA}
    unjudged = 0
    for f in final:
        if f.get("stage", "A") != "A":
            raise SystemExit("a final label that is not from stage A reached the draw (§13.5 draws stage A's labels)")
        st = spot_stratum(f)
        if st is None:
            unjudged += 1
            continue
        if f.get("arm") not in asc.ARM_GROUPS:
            raise SystemExit(f"a final label carries arm {f.get('arm')!r}")
        cells[st][f["arm"]].append(f)
    sizes = {s: {a: len(v) for a, v in arms.items()} for s, arms in cells.items()}
    alloc = allocate(sizes)
    picked: list[dict[str, Any]] = []
    for s in SPOT_STRATA:
        for a in asc.ARM_GROUPS:
            keyed = sorted((asc.seeded_key(seed, s, a, f["pair_id"]), f["pair_id"], i)
                           for i, f in enumerate(cells[s][a]))
            picked += [{**cells[s][a][i], "stratum": s} for _, _, i in keyed[: alloc[s][a]]]
    return picked, {"available": sizes, "drawn": alloc, "unjudged_excluded": unjudged, "n": len(picked)}


def sheet_order(pair_ids: Sequence[str]) -> list[str]:
    """The sheet's item order, ``sha256(261101|alias|spot|judge|sheet|<pair id>)``: blind to the
    stratum the pair was drawn from."""
    return sorted(pair_ids, key=lambda p: (asc.seeded_key(asc.SEEDS["spot_judge"], "sheet", p), p))


def pair_items(pairs: Sequence[Mapping[str, Any]], chunks: Mapping[str, Mapping[str, Any]],
               glossary: Mapping[str, Sequence[Any]], out_dir: str) -> list[dict[str, Any]]:
    """Items in :func:`sheet_order`: what the judge's prompt carries, from the registry rows
    ``pairs`` (question, term, passage paper), and nothing else. The chunk text is checked
    against the registry's sha256 (:class:`alias_study_judge.StudyWorld`)."""
    import alias_study_judge as aj
    import glossbench_alias_judge as jd

    world = aj.StudyWorld({"out_dir": out_dir}, chunks, {k: list(v) for k, v in glossary.items()}, pairs)
    by_id = {p["pair_id"]: p for p in world.pairs()}
    items = []
    for n, pid in enumerate(sheet_order(list(by_id)), 1):
        p = by_id[pid]
        content = world.chunks[p["chunk_id"]].content
        defs, _ = jd.definitions_for(content, world.glossary.get(p["c_paper"], ()))
        items.append({"item": f"p{n:03d}", "title": world.titles.get(p["c_paper"], ""),
                      "definitions": [list(d) for d in defs], "passage": content, "term": p["term"],
                      "question": p["question"], "label": None, "note": ""})
    return items


def committed_stage_a(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The verified stage-A commit and its committed bytes of ``final.jsonl`` and
    ``pairs.jsonl`` (module docstring). Raises SystemExit."""
    import alias_study_judge as aj
    import alias_study_run as ar

    mode = ar.config_mode(cfg)
    if mode == "pilot":
        raise SystemExit("a pilot judge directory: the spot check is drawn from the main run's stage-A labels, "
                         "and the pilot's labels never reach the main run (§5.6, §13.5)")
    runs, jdir = Path(cfg["runs_dir"]), Path(cfg["out_dir"]).resolve()
    if ar.read_commit_marker(runs) is None:
        raise SystemExit("stage A is not committed: the judged-pair spot check is drawn from stage A's committed "
                         "labels (§5.7, §13.5)")
    v = aj.verify_marker(runs)
    if v.get("mode") != mode:
        raise SystemExit(f"stage A was committed in mode {v.get('mode')!r}, the judge config is {mode!r}")
    commit = json.loads(Path((ar.read_commit_marker(runs) or {})["commit"]).read_text(encoding="utf-8"))
    files = {f["name"]: f for f in commit["files"]}
    out: dict[str, Any] = {"commit_sha256": v["commit_sha256"], "mode": mode}
    for name in ("final", "pairs"):
        f = files.get(name)
        if f is None or Path(f["path"]).resolve().parent != jdir:
            raise SystemExit(f"the stage-A commit's {name!r} file is not in the judge config's out_dir")
        with Path(f["path"]).open("rb") as fh:
            data = fh.read(int(f["bytes"]))
        out[name] = [json.loads(x) for x in data.split(b"\n") if x.strip()]
    out["final"] = [x for x in out["final"] if str(x.get("k", "")).startswith("pair|")]
    mf = files.get("pairs_manifest")
    if mf is None:
        raise SystemExit("the stage-A commit holds no pairs_manifest (the registry's inputs)")
    out["pairs_manifest"] = json.loads(Path(mf["path"]).read_text(encoding="utf-8"))
    return out


def check_judge_inputs(cfg: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The glossary files and chunk exports of the judge config are the ones the stage-A registry
    was built from (the committed manifest's ``inputs_sha256``): the sheet's definitions and
    passages are then the judge's. Raises SystemExit; returns the shas checked."""
    import alias_study_judge as aj

    want = manifest.get("inputs_sha256") or {}
    have = {"glossaries": [asc.sha256_file(x) for x in aj.glossary_paths(cfg)],
            **{f"chunks_{a}": asc.sha256_file(p) for a, p in cfg["chunk_exports"].items()}}
    bad = sorted(k for k in set(have) | {k for k in want if k.startswith("chunks_") or k == "glossaries"}
                 if have.get(k) != want.get(k))
    if bad:
        raise SystemExit(f"the judge config's {bad} differ from the inputs the stage-A registry was built from (the "
                         "committed pairs-manifest.json): the sheet would not show what the judge saw")
    return {"matches_stage_a_pairs_manifest": True, **have}


def judge_sheet(cfg: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """The judge sheet from its sources (module docstring): ``(sheet, the drawn final rows in item
    order, the stage-A commit read, the draw's counts and input check)``. The draw and the ingest
    both call it."""
    import alias_study_judge as aj

    sa = committed_stage_a(cfg)
    inputs = check_judge_inputs(cfg, sa["pairs_manifest"])
    picked, counts = draw_pairs_from(sa["final"])
    reg = {p["pair_id"]: p for p in sa["pairs"]}
    missing = [f["pair_id"] for f in picked if f["pair_id"] not in reg]
    if missing:
        raise SystemExit(f"{len(missing)} drawn pair(s) are not in the committed stage-A registry")
    chunks, _ = aj.chunk_arms(cfg)
    glossary = aj.read_glossaries(aj.glossary_paths(cfg))
    items = pair_items([reg[f["pair_id"]] for f in picked], chunks, glossary, str(cfg["out_dir"]))
    by_pid = {f["pair_id"]: f for f in picked}
    ordered = [by_pid[p] for p in sheet_order(list(by_pid))]
    sheet = {"schema": SHEET_SCHEMA["judge"], "kind": "judge", "labels": list(LABELS["judge"]),
             "instructions": INSTRUCTIONS["judge"], "n": len(items), "items": items}
    return sheet, ordered, sa, {"counts": counts, "inputs": inputs}


def draw_pairs(judge_config: Path, out_dir: Path) -> dict[str, Any]:
    import alias_study_judge as aj

    refuse_in_repo(out_dir, "--out-dir")
    cfg = aj.load_config(judge_config)
    sheet, ordered, sa, extra = judge_sheet(cfg)
    items = sheet["items"]
    data = canonical(sheet)
    sha = asc.sha_text(data.decode("utf-8"))
    counts = extra["counts"]
    key = {"schema": KEY_SCHEMA, "kind": "judge", "sheet_sha256": sha, "n": len(items), "mode": sa["mode"],
           "seed": asc.SEEDS["spot_judge"],
           "draw_rule": "stratified by the final label (answers 50, not 33, tiebroken 17; arms equal, remainder to "
                        "base then neighbour; shortfall to the stratum's other arms, then answers, then not); within "
                        "a cell sha256('<seed>|<stratum>|<arm>|<pair>') ascending; sheet order "
                        "sha256('<seed>|sheet|<pair>') ascending",
           "stage_a_commit_sha256": sa["commit_sha256"], "counts": counts,
           "judge_inputs": extra["inputs"],
           "items": {it["item"]: {"pair_id": f["pair_id"], "stratum": f["stratum"], "arm": f["arm"]}
                     for it, f in zip(items, ordered, strict=True)},
           "inputs": {"judge_config": asc.sha256_file(judge_config)}, "provenance": provenance(),
           "note": "PRIVATE blinding key: never shown to the rater."}
    sp, kp = out_dir / "spot-judge.sheet.json", out_dir / "spot-judge.key.json"
    write_private(sp, data, "the judge sheet")
    write_key(kp, key, "the judge key")
    return {"sheet": str(sp), "key": str(kp), "sheet_sha256": sha, "n": len(items), "counts": counts}


# ------------------------------------------------------------------ ingest

def check_filled(sheet: Mapping[str, Any], key: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> list[int]:
    """The filled sheet is the re-derived one (``rebuilt``, whose sha256 must also be the key's)
    with only ``label`` and ``note`` changed, and every item labelled: returns the labels (1 / 0)
    in item order. Raises SystemExit."""
    kind = key.get("kind")
    if kind not in LABELS or key.get("schema") != KEY_SCHEMA:
        raise SystemExit("not a spot-check key")
    want = asc.sha_text(canonical(rebuilt).decode("utf-8"))
    if want != key.get("sheet_sha256"):
        raise SystemExit("the key's sheet sha256 is not the sheet re-derived from its sources: another draw, or an "
                         "edited key")
    if sheet.get("schema") != SHEET_SCHEMA[kind]:
        raise SystemExit(f"the sheet is not a {kind} sheet")
    items = sheet.get("items")
    if not isinstance(items, list):
        raise SystemExit("the sheet has no items")
    allowed = set(RECORD_ITEM_FIELDS if kind == "records" else PAIR_ITEM_FIELDS)
    extra = sorted({k for it in items for k in it} - allowed)
    if extra:
        raise SystemExit(f"the filled sheet carries fields {extra}: only label and note may be filled in")
    blank = {**sheet, "items": [{**it, "label": None, "note": ""} for it in items]}
    if asc.sha_text(canonical(blank).decode("utf-8")) != want:
        raise SystemExit("the filled sheet is not the drawn sheet (with label and note blanked its sha256 differs "
                         "from the re-derived sheet's): items, text or order changed, or another sheet")
    vals = LABELS[kind]
    bad = [it["item"] for it in items if it.get("label") not in vals]
    if bad:
        raise SystemExit(f"{len(bad)} item(s) carry no label of {list(vals)}: {bad[:6]}")
    return [vals[it["label"]] for it in items]


def check_key_ids(key: Mapping[str, Any], items: Sequence[Mapping[str, Any]], ids: Sequence[str]) -> None:
    """The key names, item by item, the records or pairs the re-derived draw puts there."""
    have = [(v["pair_id"] if isinstance(v, Mapping) else v) for v in (key.get("items") or {}).values()]
    if list(key.get("items") or {}) != [it["item"] for it in items] or have != list(ids):
        raise SystemExit("the key's items are not the re-derived draw's (an id swapped or reordered): refused")


def write_counts(path: Path, data: bytes) -> None:
    """The records result (counts and the sheet's sha256 only): may go where §13.4 commits it,
    in the repository too; written once."""
    if path.exists() and path.read_bytes() != data:
        raise SystemExit(f"{path.name} exists with other content")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def ingest(key_path: Path, sheet_path: Path, out: Path, judge_config: Path | None = None,
           records: Path | None = None, exports: Sequence[Path] = ()) -> dict[str, Any]:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    if key.get("kind") == "records":
        if records is None or not exports:
            raise SystemExit("ingest of the records sheet needs --records and --chunk-export: the draw is re-derived")
        rebuilt, picked, _ = records_sheet(records, exports)
        labels = check_filled(sheet, key, rebuilt)
        check_key_ids(key, rebuilt["items"], picked)
        if len(labels) != SPOT_RECORDS_N:
            raise SystemExit(f"{len(labels)} labelled records; the record spot check has n = {SPOT_RECORDS_N} (§13.4)")
        res = {"valid": sum(labels), "n": len(labels), "sheet_sha256": key["sheet_sha256"]}
        write_counts(out, (json.dumps(res, sort_keys=True) + "\n").encode("utf-8"))
        return res
    import alias_study_judge as aj

    if key.get("kind") != "judge":
        raise SystemExit("not a spot-check key")
    if judge_config is None:
        raise SystemExit("ingest of the judge sheet needs --judge-config: the draw is re-derived from the stage-A "
                         "commit")
    rebuilt, ordered, sa, _ = judge_sheet(aj.load_config(judge_config))
    if sa["commit_sha256"] != key.get("stage_a_commit_sha256"):
        raise SystemExit("the stage-A commit is not the one the sheet was drawn from")
    labels = check_filled(sheet, key, rebuilt)
    pids = [f["pair_id"] for f in ordered]
    check_key_ids(key, rebuilt["items"], pids)
    rows = [{"pair_id": p, "owner_label": lab} for p, lab in zip(pids, labels, strict=True)]
    write_private(out, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8"), "--out")
    return {"n": len(rows), "n_nominal": SPOT_JUDGE_N, "owner_answers": sum(labels),
            "per_stratum": dict(Counter(f["stratum"] for f in ordered))}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    dr = sub.add_parser("draw-records")
    dr.add_argument("--records", required=True, type=Path)
    dr.add_argument("--chunk-export", required=True, type=Path, action="append")
    dr.add_argument("--out-dir", required=True, type=Path)
    dp = sub.add_parser("draw-pairs")
    dp.add_argument("--judge-config", required=True, type=Path)
    dp.add_argument("--out-dir", required=True, type=Path)
    ig = sub.add_parser("ingest")
    ig.add_argument("--key", required=True, type=Path)
    ig.add_argument("--sheet", required=True, type=Path)
    ig.add_argument("--out", required=True, type=Path)
    ig.add_argument("--judge-config", type=Path, default=None)
    ig.add_argument("--records", type=Path, default=None)
    ig.add_argument("--chunk-export", type=Path, action="append", default=[])
    a = ap.parse_args(argv)
    os.umask(0o077)
    if a.cmd == "draw-records":
        res = draw_records(a.records, a.chunk_export, a.out_dir)
    elif a.cmd == "draw-pairs":
        res = draw_pairs(a.judge_config, a.out_dir)
    else:
        res = ingest(a.key, a.sheet, a.out, a.judge_config, a.records, a.chunk_export)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
