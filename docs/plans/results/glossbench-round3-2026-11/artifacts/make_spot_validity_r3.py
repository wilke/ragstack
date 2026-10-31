#!/usr/bin/env python3
"""Build the counts-only artifact ``spot-validity-r3.json``: round 3's layer-3 owner spot-check.

Round 3 of the glossary benchmark (prereg ``../prereg/PREREG-glossbench-round3.md`` §3.2, which carries
round 2's §3.3 layer 3 unchanged) exported a seeded sample of 40 accepted records per set for the owner
to mark valid / invalid, blind to stratum and arm. ``RESULTS.md`` was committed before the labels were
imported and reports the check as absent; this builder serves the addendum
``../ADDENDUM-2026-10-30-spot-validity.md``. It reads the private files below and copies counts only:

* ``out/spot-<S>.json`` (the sheet the owner saw), ``spot-<S>.key.json`` (the blinding key),
  the owner's labels, and the harness's ``spot-import`` output ``spot-results-<S>.json``;
* ``out/accepted-O.jsonl`` / ``out/A-run2/accepted-A.jsonl`` (the population the sample came from),
  for each sampled record's stratum;
* set O's census files (DEVIATIONS.md D-5): ``O-partner-check/partner-verdicts-O-53.json``,
  ``partner-judging-notes-O-53.json`` and ``analysis/d5-exclusions-O.json``, for the overlap counts.

No question, quote, passage, answer, note text, record id, chunk id or paper id is copied. The one
record an owner decision overrides is named by its sheet item id and by ``sha256(record_id)[:12]``.

The as-typed figures are recomputed here from the labels and checked against the harness's import
(``n_valid``, ``labels_sha256``, the Wilson interval to 1e-12). The reported figure applies the owner's
decisions in ``DECISIONS``, each asserted against the labels it overrides.

    python3 -I make_spot_validity_r3.py --root T3 --out spot-validity-r3.json

``T3`` is the round's private output root (it holds ``out/``). The builder imports
``ragstack.provenance`` from the checkout it sits in, so run it from a clean tree for a citable record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "python"))
from ragstack.provenance import experiment_provenance  # noqa: E402

SCHEMA = "glossbench-r3-spot-validity/1"
WILSON_Z = 1.959963984540054  # glossbench_r2_draft.WILSON_Z; no continuity correction
THRESHOLD = 0.85  # round-2 prereg §3.3, carried by the round-3 prereg §3.2
CORPUS_ALIAS = {"O": "OA-1000", "A": "ASM-1000"}
PROVENANCE_DROP = {"host", "image", "installed_distribution"}
# Fields that would unblind an item: none may appear in the sheet the owner saw.
UNBLINDING_FIELDS = {"arm", "arms", "stratum", "condition", "attach", "glossary", "field", "record_id",
                     "source_chunk_id", "gold", "gold_chunks", "gold_differs", "reach", "paper", "doc_id",
                     "symbol_is_the_quantity", "verifier", "verifier_model", "model"}

FILES = {
    "O": {"sheet": "out/spot-O.json", "key": "out/spot-O.key.json", "labels": "out/spot-O.labels-2.json",
          "import": "out/spot-results-O.json", "accepted": "out/accepted-O.jsonl"},
    "A": {"sheet": "out/A-run2/spot-A.json", "key": "out/A-run2/spot-A.key.json",
          "labels": "out/A-run2/spot-A.labels.json", "import": "out/A-run2/spot-results-A.json",
          "accepted": "out/A-run2/accepted-A.jsonl"},
}
CENSUS = {"verdicts": "out/O-partner-check/partner-verdicts-O-53.json",
          "notes": "out/O-partner-check/partner-judging-notes-O-53.json",
          "exclusions": "out/analysis/d5-exclusions-O.json"}

# The owner's decisions (2026-10-30). Each names the sheet item, the record by sha256-prefix alias, and the
# label state it overrides, which the builder asserts before applying it.
DECISIONS = [
    {
        "set": "O",
        "item": "s010",
        "record_alias_sha256_12": "54b3ce7c4394",
        "typed": {"label": "valid", "denotes_quantity": "unsure", "note_present": True},
        "counted_as": "invalid",
        "reason_code": "OWNER_NOTE_QUESTION_AMBIGUOUS_OR_WRONG__DENOTES_QUANTITY_UNSURE",
        "reason": "typed 'valid', but the owner's own note on the item says the question is ambiguous or "
                  "wrong, and denotes_quantity is 'unsure'; the record is also census-flagged (D-5). The "
                  "owner decided to count it as not valid.",
        "decided_by": "owner",
        "decided_on": "2026-10-30",
        "decided_after": "the harness's as-typed import (40/40) and RESULTS.md; before this addendum",
    },
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alias(record_id: str) -> str:
    return hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:12]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def wilson(k: int, n: int, z: float = WILSON_Z) -> list[float]:
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def reading(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    v = k / n
    return {"n_valid": k, "n": n, "validity": v, "wilson95": [lo, hi],
            "wilson95_3dp": [round(lo, 3), round(hi, 3)], "below_threshold": v < THRESHOLD}


def public_provenance(prov: dict) -> dict:
    out = {k: v for k, v in prov.items() if k not in PROVENANCE_DROP}
    out["redacted"] = sorted(PROVENANCE_DROP)
    return out


def one_set(root: Path, s: str, census: dict | None) -> tuple[dict, dict]:
    f = {k: root / v for k, v in FILES[s].items()}
    sheet, key, labels, imp = load(f["sheet"]), load(f["key"]), load(f["labels"]), load(f["import"])
    acc = {}
    for line in f["accepted"].read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            acc[r["record_id"]] = r

    # The sheet, key, labels and import belong together.
    assert key["set"] == s and imp["set"] == s
    assert sheet["export_id"] == key["export_id"] == labels["export_id"]
    assert imp["labels_sha256"] == sha256_file(f["labels"])
    items = {it["item"]: it for it in labels["items"]}
    assert set(items) == set(key["key"]) == {it["item"] for it in sheet["items"]}
    rids = set(key["key"].values())
    assert rids <= set(acc), "a sampled record is not in the accepted file"

    # Blindness: the sheet carries no field that names arm, stratum, condition or record.
    sheet_fields = sorted(set().union(*[set(it) for it in sheet["items"]]))
    leaked = sorted(set(sheet_fields) & UNBLINDING_FIELDS)
    assert not leaked, leaked

    lab = Counter(it["label"] for it in items.values())
    dq = Counter(it.get("denotes_quantity") for it in items.values())
    joint = Counter(f"{it['label']}|{it.get('denotes_quantity')}" for it in items.values())
    n = len(items)
    k_typed = lab.get("valid", 0)
    assert set(lab) <= {"valid", "invalid"}
    assert imp["n_graded"] == n and imp["n_valid"] == k_typed and imp["threshold"] == THRESHOLD
    as_typed = reading(k_typed, n)
    assert all(abs(a - b) < 1e-12 for a, b in zip(as_typed["wilson95"], imp["wilson95"]))

    applied = []
    k_rep = k_typed
    for d in (d for d in DECISIONS if d["set"] == s):
        it = items[d["item"]]
        rid = key["key"][d["item"]]
        assert alias(rid) == d["record_alias_sha256_12"], "decision does not name the expected record"
        assert it["label"] == d["typed"]["label"]
        assert it.get("denotes_quantity") == d["typed"]["denotes_quantity"]
        assert bool(it.get("note")) == d["typed"]["note_present"]
        if d["typed"]["label"] == "valid" and d["counted_as"] == "invalid":
            k_rep -= 1
        entry = {x: d[x] for x in ("item", "record_alias_sha256_12", "typed", "counted_as", "reason_code",
                                   "reason", "decided_by", "decided_on", "decided_after")}
        if census is not None:
            entry["in_census_53"] = rid in census["ids53"]
            entry["census_flag_class"] = census["flag_class"].get(rid)
        applied.append(entry)

    out = {
        "corpus": CORPUS_ALIAS[s],
        "export_id": sheet["export_id"],
        "seed": key["seed"],
        "population_accepted": len(acc),
        "population_by_stratum": dict(sorted(Counter(r["stratum"] for r in acc.values()).items())),
        "n_items": n,
        "sample_by_stratum": dict(sorted(Counter(acc[r]["stratum"] for r in rids).items())),
        "blind": {"sheet_item_fields": sheet_fields, "unblinding_fields_present": leaked,
                  "note": "the passage is the raw chunk text, with no glossary field rendered; the sheet names no "
                          "arm, stratum, condition or record id; the key is a separate file"},
        "labels": {"label": dict(sorted(lab.items())), "denotes_quantity": dict(sorted(dq.items())),
                   "label_by_denotes_quantity": dict(sorted(joint.items())),
                   "items_with_a_note": sum(1 for it in items.values() if it.get("note"))},
        "as_typed": as_typed,
        "decisions": applied,
        "reported": reading(k_rep, n),
        "harness_import": {
            "n_graded": imp["n_graded"], "n_valid": imp["n_valid"], "validity": imp["validity"],
            "wilson95": imp["wilson95"], "threshold": imp["threshold"], "below_threshold": imp["below_threshold"],
            "rule": imp["rule"], "citable": imp["citable"],
            "provenance": public_provenance(imp["provenance"]),
        },
    }
    if census is not None:
        out["overlap_with_census"] = {
            "sample_in_census_53": len(rids & census["ids53"]),
            "sample_in_census_53_by_stratum": dict(sorted(Counter(acc[r]["stratum"]
                                                                  for r in rids & census["ids53"]).items())),
            "sample_in_census_53_census_verdict": dict(sorted(Counter(census["verdict"][r]
                                                                      for r in rids & census["ids53"]).items())),
            "sample_in_flagged_12": len(rids & census["flagged"]),
            "sample_in_flagged_12_by_class": dict(sorted(Counter(census["flag_class"][r]
                                                                 for r in rids & census["flagged"]).items())),
        }
    shas = {k: sha256_file(p) for k, p in f.items()}
    return out, shas


def census_sets(root: Path) -> tuple[dict, dict]:
    p = {k: root / v for k, v in CENSUS.items()}
    ver, notes, exc = load(p["verdicts"]), load(p["notes"]), load(p["exclusions"])
    verdict = {r["id"]: r["verdict"] for r in ver["records"]}
    ids53 = set(verdict)
    assert len(ids53) == 53
    # D-5's rule, re-derived: reject (with a reason -> wrong_quantity, without -> worded), or a note
    # containing 'NOT A QUANTITY'.
    flag_class = {}
    for rid, v in verdict.items():
        nt = notes.get(rid, {})
        if v == "reject":
            flag_class[rid] = "wrong_quantity" if nt.get("reason") else "worded"
        elif "NOT A QUANTITY" in str(nt.get("note", "")):
            flag_class[rid] = "not_a_quantity"
    flagged = set(exc["excluded_record_ids"])
    assert flagged == set(flag_class), "D-5 exclusions differ from the re-derived rule"
    assert len(flagged) == 12
    return ({"ids53": ids53, "verdict": verdict, "flagged": flagged, "flag_class": flag_class},
            {k: sha256_file(v) for k, v in p.items()})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    census, census_sha = census_sets(a.root)
    sets, shas = {}, {}
    for s in ("O", "A"):
        sets[s], sh = one_set(a.root, s, census if s == "O" else None)
        shas.update({f"{s}.{k}": v for k, v in sh.items()})
    shas.update({f"census.{k}": v for k, v in census_sha.items()})
    prov = experiment_provenance()
    art = {
        "schema": SCHEMA,
        "what": "round 3 layer-3 owner spot-check: 40 accepted records per set, labelled valid/invalid by the "
                "owner, blind to stratum and arm; counts only",
        "prereg_rule": {
            "source": "round-3 prereg §3.2 (round 2's §3.2-3.3 'apply unchanged'), which points at round-2 prereg "
                      "§3.3 layer 3 for the rule",
            "verbatim_round2_s3_3": "a seeded random sample of **40** accepted records per set (`261101|spot|<set>`), "
                                    "shown blind to stratum and arm; the owner marks valid / invalid. Reported: "
                                    "validity with a Wilson 95% CI. **Rule:** validity below **0.85** at the point "
                                    "estimate makes every result from that set carry *validity below threshold* in the "
                                    "sentence; the set is not rebuilt (that would be a forking path).",
            "verbatim_round3_s3_2": "owner spot-check of 40 per set with the 0.85 validity rule, seed "
                                    "`<spot.seed_base>|<set>` with `spot.seed_base` set to **`261103|spot`** in the "
                                    "private config",
            "threshold": THRESHOLD,
            "compared": "point estimate k/n, not an interval bound",
            "wilson_z": WILSON_Z,
            "label_if_below": "validity below threshold",
        },
        "sets": sets,
        "inputs_sha256": dict(sorted(shas.items())),
        "provenance": public_provenance(prov),
        "citable": bool(prov.get("citable")),
        "private_data": "the sheets, key, labels, notes, accepted records and census files stay private; no "
                        "record text or record id is copied",
    }
    a.out.write_text(json.dumps(art, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")
    for s in ("O", "A"):
        r, t = sets[s]["reported"], sets[s]["as_typed"]
        print(f"{s}: reported {r['n_valid']}/{r['n']} {r['wilson95_3dp']}  as typed {t['n_valid']}/{t['n']} "
              f"{t['wilson95_3dp']}  below={r['below_threshold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
