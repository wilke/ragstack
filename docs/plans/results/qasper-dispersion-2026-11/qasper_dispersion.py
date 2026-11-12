"""EXPLORATORY (not pre-registered): section-level evidence dispersion in QASPER v0.3.

Question: how often does an answer's evidence span more than one SECTION of the paper
(not just more than one paragraph), and how does that share vary by answer type?
Descriptive only. Answer type is not question composition; nothing here codes question
complexity.

Reads the official AllenAI release (``qasper-train-v0.3.json`` and ``qasper-dev-v0.3.json``,
extracted from ``qasper-train-dev-v0.3.tgz``) from DATA_DIR and writes aggregates only (no
question, answer or paper text) to OUT_JSON. Optionally also the public test split
(``--test-dir``, holding ``qasper-test-v0.3.json`` from ``qasper-test-and-evaluator-v0.3.tgz``)
and, for the 55.5% reproduction check only, any other release directory
(``--repro-dir LABEL=DIR[,URL]``, every ``qasper-*.json`` in it).

Usage (from the repo root, clean tree, so the provenance block carries a version):

    PYTHONPATH=python python3 -I docs/plans/results/qasper-dispersion-2026-11/qasper_dispersion.py \
        DATA_DIR OUT_JSON [--test-dir DIR] [--repro-dir LABEL=DIR[,URL] ...]

``-I`` (isolated mode, because DATA_DIR is downloaded data) ignores PYTHONPATH, so
``_import_provenance`` falls back to inserting this repo's ``python/`` explicitly.

Deterministic: no randomness except a paper-cluster bootstrap with a fixed seed.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "qasper-dispersion/1"
DATASET_URL = "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz"
TEST_URL = "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz"
SPLITS = ("train", "dev")
SEP = " ::: "
FLOAT = "FLOAT SELECTED"
TYPES = ("extractive", "abstractive", "yes_no")
BOOT_REPS = 2000
BOOT_SEED = 20261111

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


# --------------------------------------------------------------------------- mapping


def section_index(full_text: list[dict]) -> dict[str, Any]:
    """Per paper: section keys at three levels and lookup tables for evidence mapping.

    Levels: ``entry`` = the full_text entry index; ``full`` = the full section path
    (``section_name`` as given, e.g. ``Experiments ::: Setup``); ``top`` = its first
    ``:::`` component. A missing name becomes ``<unnamed:i>`` so it never merges with
    another section.
    """
    full, top = [], []
    exact: dict[str, list[int]] = {}
    normed: dict[str, list[int]] = {}
    heading: dict[str, list[int]] = {}
    for i, sec in enumerate(full_text):
        name = sec.get("section_name")
        fname = norm(name) if name and norm(name) else f"<unnamed:{i}>"
        full.append(fname)
        top.append(fname.split(SEP)[0].strip() if not fname.startswith("<unnamed") else fname)
        if name and norm(name):
            heading.setdefault(norm(name), []).append(i)
        for para in sec.get("paragraphs") or []:
            exact.setdefault(para, []).append(i)
            normed.setdefault(norm(para), []).append(i)
    return {"full": full, "top": top, "exact": exact, "normed": normed, "heading": heading}


def map_evidence(ev: str, idx: dict[str, Any]) -> tuple[str, list[int]]:
    """Map one text evidence string to candidate section entry indices.

    Rules, in order: exact paragraph match; whitespace-normalised paragraph match; the
    string is a section heading (normalised). Returns (rule, sorted distinct entry indices);
    rule ``unmatched`` with [] when nothing matches.
    """
    if ev in idx["exact"]:
        return "exact", sorted(set(idx["exact"][ev]))
    n = norm(ev)
    if n in idx["normed"]:
        return "normalised", sorted(set(idx["normed"][n]))
    if n in idx["heading"]:
        return "heading", sorted(set(idx["heading"][n]))
    return "unmatched", []


def answer_type(a: dict) -> str | None:
    if a.get("unanswerable"):
        return "unanswerable"
    if a.get("extractive_spans"):
        return "extractive"
    if a.get("free_form_answer"):
        return "abstractive"
    if a.get("yes_no") is not None:
        return "yes_no"
    return None


def contiguous(sorted_ints: list[int]) -> bool:
    return bool(sorted_ints) and sorted_ints[-1] - sorted_ints[0] + 1 == len(sorted_ints)


def analyse_answer(a: dict, idx: dict[str, Any]) -> dict[str, Any]:
    """Per-answer record (no text). Ambiguous evidence (paragraph text present in several
    sections) resolves to the earliest entry, mirroring QASPER's own prefer-earlier rule;
    ``ambiguous_cross_section`` flags it for a sensitivity cut."""
    ev = list(dict.fromkeys(a.get("evidence") or []))  # distinct, order kept
    floats = [e for e in ev if e.startswith(FLOAT)]
    texts = [e for e in ev if not e.startswith(FLOAT)]
    rules: Counter = Counter()
    entries: list[int] = []
    para_keys: set[tuple[str, Any]] = set()
    ambiguous = False
    for e in texts:
        rule, cands = map_evidence(e, idx)
        rules[rule] += 1
        if not cands:
            continue
        if len(cands) > 1:
            ambiguous = True
        entries.append(cands[0])
        para_keys.add(("h", cands[0]) if rule == "heading" else ("p", norm(e)))
    ent = sorted(set(entries))
    full = sorted({idx["full"][i] for i in ent})
    top_first: dict[str, int] = {}
    for i, t in enumerate(idx["top"]):
        top_first.setdefault(t, i)
    tops = sorted({idx["top"][i] for i in ent}, key=lambda t: top_first[t])
    top_order = {t: k for k, t in enumerate(sorted(top_first, key=lambda t: top_first[t]))}
    return {
        "type": answer_type(a),
        "n_ev": len(ev),
        "n_float": len(floats),
        "n_text": len(texts),
        "rules": dict(rules),
        "all_mapped": rules["unmatched"] == 0,
        "all_exact": rules["exact"] == len(texts),
        "ambiguous_cross_section": ambiguous,
        "n_para": len(para_keys),
        "n_entry": len(ent),
        "n_full": len(full),
        "n_top": len(tops),
        "entries": ent,
        "entry_contiguous": contiguous(ent),
        "top_contiguous": contiguous(sorted(top_order[t] for t in tops)),
        "entry_span": (ent[-1] - ent[0]) if ent else None,
        "in_first_section": 0 in ent,
        "n_top_excl_first": len({idx["top"][i] for i in ent if i != 0}),
    }


# --------------------------------------------------------------------------- stats


def wilson(k: int, n: int, z: float = 1.959963984540054) -> dict[str, Any]:
    if n == 0:
        return {"k": 0, "n": 0, "p": None, "lo": None, "hi": None}
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "p": round(p, 4), "lo": round(c - h, 4), "hi": round(c + h, 4)}


def cluster_boot(pairs_by_paper: list[tuple[int, int]], reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> dict[str, Any]:
    """Percentile CI for a pooled share, resampling papers (answers within a paper are not
    independent: same paper, often same question)."""
    pairs = [p for p in pairs_by_paper if p[1] > 0]
    if not pairs:
        return {"lo": None, "hi": None, "papers": 0}
    rng = random.Random(seed)
    m = len(pairs)
    est = []
    for _ in range(reps):
        k = n = 0
        for _ in range(m):
            a, b = pairs[rng.randrange(m)]
            k += a
            n += b
        est.append(k / n)
    est.sort()
    return {"lo": round(est[int(0.025 * reps)], 4), "hi": round(est[int(0.975 * reps) - 1], 4),
            "papers": m, "reps": reps, "seed": seed}


def fleiss_variable(items: list[list[int]]) -> dict[str, Any]:
    """Fleiss' kappa for binary labels with a variable number of raters per item (the
    pairwise-agreement form). Raters are not the same people across items, so Cohen's
    kappa does not apply; with exactly two raters per item this equals Scott's pi."""
    items = [it for it in items if len(it) >= 2]
    if not items:
        return {"items": 0, "kappa": None}
    tot = sum(len(it) for it in items)
    p1 = sum(sum(it) for it in items) / tot
    pe = p1 * p1 + (1 - p1) * (1 - p1)
    po = 0.0
    for it in items:
        n = len(it)
        k = sum(it)
        po += (k * (k - 1) + (n - k) * (n - k - 1)) / (n * (n - 1))
    po /= len(items)
    agree = sum(1 for it in items if len(set(it)) == 1)
    return {"items": len(items), "all_agree": agree, "all_agree_share": round(agree / len(items), 4),
            "p_observed": round(po, 4), "p_expected": round(pe, 4), "positive_rate": round(p1, 4),
            "kappa": round((po - pe) / (1 - pe), 4) if pe < 1 else None}


# --------------------------------------------------------------------------- driver


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_files(data_dir: Path, test_dir: Path | None) -> dict[str, Path]:
    files = {sp: data_dir / f"qasper-{sp}-v0.3.json" for sp in SPLITS}
    if test_dir is not None:
        files["test"] = test_dir / "qasper-test-v0.3.json"
    return files


def collect(data: dict[str, dict]) -> tuple[list[dict], dict[str, Any]]:
    """Flatten to per-answer records tagged with split / paper / question (ids only)."""
    recs: list[dict] = []
    struct: dict[str, Any] = {}
    for sp in data:
        c: Counter = Counter()
        for pid in sorted(data[sp]):
            p = data[sp][pid]
            idx = section_index(p["full_text"])
            c["papers"] += 1
            c["sections"] += len(idx["full"])
            c["sections_hierarchical"] += sum(1 for f in idx["full"] if SEP in f)
            c["papers_with_hierarchical_names"] += int(any(SEP in f for f in idx["full"]))
            c["papers_with_duplicate_full_names"] += int(len(set(idx["full"])) < len(idx["full"]))
            c["papers_with_paragraph_text_in_2plus_sections"] += int(any(len(set(v)) > 1 for v in idx["exact"].values()))
            c["distinct_top_sections"] += len(set(idx["top"]))
            first = idx["full"][0] if idx["full"] else ""
            c["first_section_is_intro_like"] += int(bool(re.search(r"intro", first, re.IGNORECASE)))
            for q in p["qas"]:
                c["questions"] += 1
                for ans in q["answers"]:
                    c["answers"] += 1
                    r = analyse_answer(ans["answer"], idx)
                    r.update(split=sp, paper=pid, qid=q["question_id"], n_sections_paper=len(idx["full"]))
                    recs.append(r)
        struct[sp] = dict(sorted(c.items()))
    return recs, struct


def eligible_text(r: dict) -> bool:
    """Primary denominator: answerable, typed, non-empty evidence, no FLOAT SELECTED item,
    every text evidence string mapped."""
    return r["type"] in TYPES and r["n_ev"] > 0 and r["n_float"] == 0 and r["all_mapped"]


def share(rs: list[dict], pred) -> dict[str, Any]:
    return wilson(sum(1 for r in rs if pred(r)), len(rs))


def shares_block(rs: list[dict], with_boot: bool = False) -> dict[str, Any]:
    preds = {
        "multi_paragraph": lambda r: r["n_para"] > 1,
        "multi_evidence_string": lambda r: r["n_text"] > 1,
        "multi_section_full_path": lambda r: r["n_full"] > 1,
        "multi_section_top_level": lambda r: r["n_top"] > 1,
    }
    out: dict[str, Any] = {"n": len(rs)}
    for name, pr in preds.items():
        out[name] = share(rs, pr)
        if with_boot:
            per: dict[str, list[int]] = {}
            for r in rs:
                v = per.setdefault(r["paper"], [0, 0])
                v[0] += int(pr(r))
                v[1] += 1
            out[name]["paper_cluster_boot95"] = cluster_boot([tuple(per[k]) for k in sorted(per)])
    mp = [r for r in rs if r["n_para"] > 1]
    out["given_multi_paragraph"] = {
        "multi_section_full_path": share(mp, lambda r: r["n_full"] > 1),
        "multi_section_top_level": share(mp, lambda r: r["n_top"] > 1),
    }
    out["section_count_full_path"] = dict(sorted(Counter(min(r["n_full"], 4) for r in rs).items()))
    out["section_count_top_level"] = dict(sorted(Counter(min(r["n_top"], 4) for r in rs).items()))
    return out


def by_type(rs: list[dict], with_boot: bool = False) -> dict[str, Any]:
    return {t: shares_block([r for r in rs if r["type"] == t], with_boot) for t in TYPES}


def floats_block(rs: list[dict]) -> dict[str, Any]:
    """With-figures/tables variant: answerable, typed, non-empty evidence, all text mapped
    (floats allowed). Floats have no section in full_text, so section counts are given two
    ways: floats ignored (answers with only floats drop out of that share), and all floats
    together counted as one extra pseudo-section."""
    base = [r for r in rs if r["type"] in TYPES and r["n_ev"] > 0 and r["all_mapped"]]
    with_text = [r for r in base if r["n_entry"] > 0]

    def blk(sub: list[dict]) -> dict[str, Any]:
        tx = [r for r in sub if r["n_entry"] > 0]
        return {
            "n": len(sub),
            "with_any_float": sum(1 for r in sub if r["n_float"] > 0),
            "float_only": sum(1 for r in sub if r["n_entry"] == 0),
            "multi_location_paragraphs_plus_floats": share(sub, lambda r: r["n_para"] + r["n_float"] > 1),
            "multi_section_full_floats_ignored": share(tx, lambda r: r["n_full"] > 1),
            "multi_section_top_floats_ignored": share(tx, lambda r: r["n_top"] > 1),
            "multi_section_full_floats_as_one_section": share(sub, lambda r: r["n_full"] + (r["n_float"] > 0) > 1),
            "multi_section_top_floats_as_one_section": share(sub, lambda r: r["n_top"] + (r["n_float"] > 0) > 1),
        }

    return {"all": blk(base), "by_type": {t: blk([r for r in base if r["type"] == t]) for t in TYPES},
            "n_with_text": len(with_text)}


def agreement(rs: list[dict]) -> dict[str, Any]:
    """Per question, across that question's eligible answers (primary denominator)."""
    byq: dict[str, list[dict]] = {}
    for r in rs:
        byq.setdefault(r["qid"], []).append(r)
    labs = {
        "multi_paragraph": lambda r: int(r["n_para"] > 1),
        "multi_section_full_path": lambda r: int(r["n_full"] > 1),
        "multi_section_top_level": lambda r: int(r["n_top"] > 1),
    }
    out: dict[str, Any] = {}
    for name, f in labs.items():
        items = [[f(r) for r in byq[q]] for q in sorted(byq)]
        two = [it for it in items if len(it) == 2]
        out[name] = {"all_multi_annotated": fleiss_variable(items), "exactly_two": fleiss_variable(two)}
    # exact same set of section entries, among multi-annotated questions
    multi = [byq[q] for q in sorted(byq) if len(byq[q]) >= 2]
    same = sum(1 for g in multi if len({tuple(r["entries"]) for r in g}) == 1)
    out["identical_section_entry_set"] = {"items": len(multi), "k": same,
                                          "share": round(same / len(multi), 4) if multi else None}
    return out


def multi_annotation_counts(recs: list[dict], elig: list[dict]) -> dict[str, Any]:
    allq = Counter(r["qid"] for r in recs)
    eq = Counter(r["qid"] for r in elig)
    return {"questions_with_2plus_answers": sum(1 for v in allq.values() if v >= 2),
            "questions_with_2plus_eligible_answers": sum(1 for v in eq.values() if v >= 2)}


def distance(rs: list[dict]) -> dict[str, Any]:
    """Among multi-section answers: adjacency, span, and a first-section proxy."""
    mf = [r for r in rs if r["n_full"] > 1]
    mt = [r for r in rs if r["n_top"] > 1]
    single_f = [r for r in rs if r["n_full"] == 1]
    spans = sorted(r["entry_span"] for r in mf)
    return {
        "full_path_multi_n": len(mf),
        "full_path_all_sections_contiguous": share(mf, lambda r: r["entry_contiguous"]),
        "full_path_within_one_top_level": share(mf, lambda r: r["n_top"] == 1),
        "full_path_entry_span_median": spans[len(spans) // 2] if spans else None,
        "full_path_entry_span_quartiles": [spans[len(spans) // 4], spans[(3 * len(spans)) // 4]] if spans else None,
        "top_level_multi_n": len(mt),
        "top_level_all_sections_contiguous": share(mt, lambda r: r["top_contiguous"]),
        "proxy_first_section": {
            "note": "PROXY ONLY for 'same finding restated': share with >=1 evidence paragraph in the "
                    "first full_text section (usually the Introduction; the abstract is a separate field "
                    "and no evidence string matched it).",
            "top_level_multi_with_first_section": share(mt, lambda r: r["in_first_section"]),
            "full_path_multi_with_first_section": share(mf, lambda r: r["in_first_section"]),
            "single_section_in_first_section": share(single_f, lambda r: r["in_first_section"]),
            "top_level_multi_still_multi_without_first_section": share(mt, lambda r: r["n_top_excl_first"] > 1),
        },
    }


def mapping_report(recs: list[dict]) -> dict[str, Any]:
    ans = [r for r in recs if r["type"] in TYPES]
    rules: Counter = Counter()
    for r in ans:
        rules.update(r["rules"])
    tot = sum(rules.values())
    return {
        "text_evidence_strings_answerable": tot,
        "by_rule": dict(sorted(rules.items())),
        "unmatched_exact_share": round(1 - rules["exact"] / tot, 4) if tot else None,
        "unmatched_after_all_rules_share": round(rules["unmatched"] / tot, 4) if tot else None,
        "answers_with_cross_section_ambiguous_evidence": sum(1 for r in ans if r["ambiguous_cross_section"]),
    }


def denominators(recs: list[dict]) -> dict[str, Any]:
    c: Counter = Counter()
    for r in recs:
        c["answers"] += 1
        t = r["type"]
        if t == "unanswerable":
            c["unanswerable"] += 1
            continue
        if t is None:
            c["untyped"] += 1
            continue
        c["answerable"] += 1
        if r["n_ev"] == 0:
            c["answerable_empty_evidence"] += 1
            continue
        if r["n_float"] > 0:
            c["answerable_with_float"] += 1
            continue
        c["answerable_text_only"] += 1
        if not r["all_mapped"]:
            c["text_only_unmapped"] += 1
            continue
        c["eligible_primary"] += 1
        if r["all_exact"]:
            c["eligible_all_exact"] += 1
    return dict(sorted(c.items()))


def repro_simple(data: dict) -> dict[str, Any]:
    """The QASPER paper's 55.5% definition read literally, straight off the JSON with no
    mapping: answerable answers, non-empty evidence, no FLOAT SELECTED item; share with >1
    distinct evidence string. Plus two looser readings tried while hunting the gap."""
    c: Counter = Counter()
    for pid in sorted(data):
        for q in data[pid]["qas"]:
            union: set[str] = set()
            q_ok = False
            for ans in q["answers"]:
                a = ans["answer"]
                if a.get("unanswerable") or not a.get("evidence"):
                    continue
                ev = a["evidence"]
                c["answerable_with_evidence"] += 1
                c["answerable_with_evidence_multi_item"] += int(len(set(ev)) > 1)
                if any(e.startswith(FLOAT) for e in ev):
                    continue
                c["text_only"] += 1
                c["text_only_multi_paragraph"] += int(len(set(ev)) > 1)
                hl = [h for h in a.get("highlighted_evidence") or [] if not h.startswith(FLOAT)]
                c["text_only_multi_highlight"] += int(len(set(hl)) > 1)
                union |= set(ev)
                q_ok = True
            if q_ok:
                c["questions_text_only"] += 1
                c["questions_union_over_annotators_multi_paragraph"] += int(len(union) > 1)
    out: dict[str, Any] = dict(sorted(c.items()))
    out["share_text_only_multi_paragraph"] = wilson(c["text_only_multi_paragraph"], c["text_only"])
    out["share_text_only_multi_highlight"] = wilson(c["text_only_multi_highlight"], c["text_only"])
    out["share_with_floats_multi_item"] = wilson(c["answerable_with_evidence_multi_item"], c["answerable_with_evidence"])
    out["share_question_union_multi_paragraph"] = wilson(
        c["questions_union_over_annotators_multi_paragraph"], c["questions_text_only"])
    return out


def build(data_dir: Path, test_dir: Path | None = None,
          repro: dict[str, tuple[Path, str | None]] | None = None) -> dict[str, Any]:
    files = split_files(data_dir, test_dir)
    data = {sp: json.loads(f.read_text()) for sp, f in files.items()}
    recs, struct = collect(data)
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "exploratory": True,
        "pre_registered": False,
        "label": "EXPLORATORY, descriptive, not pre-registered",
        "dataset": {
            "name": "QASPER v0.3 (Dasigi et al., NAACL 2021)",
            "license": "CC BY 4.0",
            "url": DATASET_URL,
            "archive": "qasper-train-dev-v0.3.tgz",
            "files": {f.name: sha256(f) for f in files.values()},
            "test_url": TEST_URL if test_dir is not None else None,
            "test_archive": "qasper-test-and-evaluator-v0.3.tgz" if test_dir is not None else None,
        },
        "definitions": {
            "primary_denominator": "answers that are answerable (extractive/abstractive/yes-no), have non-empty "
                                   "evidence, contain no FLOAT SELECTED item, and whose every text evidence "
                                   "string maps to the paper (exact paragraph, whitespace-normalised paragraph, "
                                   "or section heading)",
            "paragraph": "distinct mapped evidence paragraph (a heading-evidence string counts as one location)",
            "section_full_path": "full_text section_name as given, e.g. 'Experiments ::: Setup'",
            "section_top_level": "first ' ::: ' component of section_name",
            "ambiguity": "evidence text found in >1 section resolves to the earliest one",
            "unit": "answer (annotation); a question can have several",
        },
        "structure": struct,
        "splits": {},
    }
    groups = {sp: [r for r in recs if r["split"] == sp] for sp in data}
    groups["pooled"] = [r for r in recs if r["split"] in SPLITS]
    if "test" in data:
        groups["pooled_with_test"] = recs
    out["groups_note"] = ("'pooled' = train+dev (the primary pool); 'pooled_with_test' adds the public "
                          "test split, which carries most of the multi-annotator questions")
    out["reproduction_55_5"] = {
        "paper_claim": "Among the answerable questions with text-only evidence, 55.5% of the answers have "
                       "multi-paragraph evidence (Dasigi et al. 2021, sec. 3); computed over all 5,049 questions "
                       "of the release the paper describes",
        "v0.3": {sp: repro_simple(data[sp]) for sp in data},
    }
    for label, (d, url) in sorted((repro or {}).items()):
        blk: dict[str, Any] = {"url": url}
        for f in sorted(d.glob("qasper-*.json")):
            blk[f.name] = {"sha256": sha256(f), **repro_simple(json.loads(f.read_text()))}
        out["reproduction_55_5"][label] = blk
    for g, rs in groups.items():
        elig = [r for r in rs if eligible_text(r)]
        boot = g in ("pooled", "pooled_with_test")
        out["splits"][g] = {
            "denominators": denominators(rs),
            "mapping": mapping_report(rs),
            "text_only": {"all": shares_block(elig, boot), "by_type": by_type(elig, boot)},
            "text_only_exact_match_only": {
                "all": shares_block([r for r in elig if r["all_exact"]]),
                "by_type": by_type([r for r in elig if r["all_exact"]])},
            "text_only_excluding_ambiguous": {
                "all": shares_block([r for r in elig if not r["ambiguous_cross_section"]])},
            "with_figures_tables": floats_block(rs),
            "agreement": agreement(elig),
            "multi_annotation": multi_annotation_counts(rs, elig),
            "distance": distance(elig),
            "question_level_any_answer_multi_paragraph": _question_level(elig),
        }
    return out


def _question_level(elig: list[dict]) -> dict[str, Any]:
    """Question-level variants for the 55.5% reproduction check."""
    byq: dict[str, list[dict]] = {}
    for r in elig:
        byq.setdefault(r["qid"], []).append(r)
    qs = [byq[q] for q in sorted(byq)]
    return {
        "questions": len(qs),
        "any_answer_multi_paragraph": wilson(sum(1 for g in qs if any(r["n_para"] > 1 for r in g)), len(qs)),
        "first_answer_multi_paragraph": wilson(sum(1 for g in qs if g[0]["n_para"] > 1), len(qs)),
        "any_answer_multi_section_top": wilson(sum(1 for g in qs if any(r["n_top"] > 1 for r in g)), len(qs)),
    }


def _import_provenance():
    try:
        from ragstack.provenance import experiment_provenance
    except ImportError:
        root = Path(__file__).resolve().parents[4]
        sys.path.insert(0, str(root / "python"))
        from ragstack.provenance import experiment_provenance
    return experiment_provenance


def main(argv: list[str]) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="EXPLORATORY QASPER section-dispersion measurement")
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("out_json", type=Path)
    ap.add_argument("--test-dir", type=Path, default=None)
    ap.add_argument("--repro-dir", action="append", default=[], metavar="LABEL=DIR[,URL]")
    a = ap.parse_args(argv[1:])
    repro = {}
    for item in a.repro_dir:
        label, _, rest = item.partition("=")
        d, _, url = rest.partition(",")
        repro[label] = (Path(d), url or None)
    out = a.out_json
    prov = _import_provenance()()
    prov.pop("host", None)
    w = prov.pop("warnings", None)
    prov["warnings_n"] = len(w or [])
    res = build(a.data_dir, a.test_dir, repro)
    res["provenance"] = prov
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    p = res["splits"]["pooled"]["text_only"]["all"]
    print(json.dumps({k: p[k]["p"] for k in ("multi_paragraph", "multi_section_full_path", "multi_section_top_level")}))


if __name__ == "__main__":
    main(sys.argv)
