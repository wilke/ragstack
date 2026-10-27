#!/usr/bin/env python
"""Export the public, redacted artifacts of the deployed-docs chunk-size trial.

Reads the PRIVATE results, caches and logs from the trial's out-dir (named in
the private config, ``--private-config`` or env ``CHUNKTRIAL_PRIVATE_CONFIG``)
and writes counts, contrasts, label distributions, kappas, context-token
statistics, gateway usage totals and per-record rows reduced to ids, strata,
ranks, found flags and grades.

Redaction is by allow-list, twice over:

1. only named top-level sections of each results file are exported, and named
   sub-keys known to carry text or locations (chunk lists, raw model output,
   URLs, index names) are not descended into;
2. every string that survives, key or value, must be a short token
   (``SAFE``), must not contain a private term, and must not be a substring
   match of any question, quote or seed-question text. A document id is
   replaced by a salted hash (salt in the private config). Anything else
   stops the export with the offending paths: it never writes a partial set.

Run: ``PYTHONPATH=python python python/scripts/eval/chunktrial_export_public.py``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT_DEST = REPO / "docs/plans/results/chunk-size-deployed-docs-2026-10-23/artifacts"

SAFE = re.compile(r"^[A-Za-z0-9_.+|~:=<>()%/,*# -]{0,80}$")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Model ids that may appear verbatim even though they contain a vendor name.
ALLOWED_VERBATIM = {"Salesforce/SFR-Embedding-Mistral"}

#: Sub-keys that are never exported (text, locations, or per-chunk lists).
SKIP_KEYS = {"top10", "rr_list", "judge_pool", "unparseable_raw", "url", "base_url", "index", "provenance",
             "prereg", "prereg_sha256", "note", "W_yes_hits_note", "_ids_not_contained", "drift_ids",
             "reassembly_failed_ids", "pages_differing_ids", "opus_no_label_by_doc", "est_input_tokens_chars_div4",
             "rescore_variants", "retries", "legend"}

SECTIONS = {
    "part12": ("results_main", ["n_questions", "n_low_echo", "n_long_pages", "questions_by_stratum", "index_meta",
                                "arms_meta", "generation", "summary", "contrasts", "verdicts", "split_quotes",
                                "storage", "part1", "load"]),
    "addendum_S": ("results_S", ["manifest", "chunker_params", "semantic_buffer_texts", "reproduction",
                                 "size_distributions", "bug_criterion", "storage", "summary", "contrasts",
                                 "verdicts", "split_quotes", "load"]),
    "part3": ("results_part3", ["manifest", "arms_chunks", "generation", "n", "by_stratum", "summary", "contrasts",
                                "verdicts", "W_pool_reconstruction", "judges", "judge_model", "judge_key",
                                "judge_subset", "cross_judge", "load"]),
    "k10": ("results_k10", ["n", "arms", "lists_check", "window_drops", "readers", "graders", "judge_model",
                            "judge_key", "judge_subset", "cross_judge", "primary_grader", "summary",
                            "context_tokens_SFR", "contrasts", "verdicts", "load"]),
}
#: Cross-judge copies (same sections as their primary file); suffix -> public suffix.
ALT_JUDGES = {"opus5": "opus5", "sol": "sol"}

CALL_LOGS = {"W judge, Claude Opus 5": "opus-calls-261023.jsonl",
             "k10 grader, Claude Opus 5": "k10-opus-calls-261023.jsonl",
             "W judge, Claude Opus 4.8": "opus48-calls-261023.jsonl",
             "k10 grader, Claude Opus 4.8": "k10-opus48-calls-261023.jsonl",
             "W judge, GPT-5.6 Sol (subset)": "sol-calls-261023.jsonl",
             "k10 grader, GPT-5.6 Sol (subset)": "k10-sol-calls-261023.jsonl"}
K10_GRADE_FILES = {"opus48": "k10-grades-opus48-261023.jsonl", "qwen": "k10-grades-qwen-261023.jsonl",
                   "opus5": "k10-grades-opus-261023.jsonl", "sol": "k10-grades-sol-261023.jsonl"}


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def norm(text: str) -> str:
    """The trial's gold normalisation (NFKC, casefold, whitespace removed) — the grade-cache key uses it."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


GRAM_WORDS = 6


def word_grams(text: str, n: int = GRAM_WORDS) -> set[tuple[str, ...]]:
    """All runs of ``n`` consecutive words (NFKC, casefolded, ``\\w+`` tokens)."""
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


class Redactor:
    def __init__(self, cfg: dict[str, Any], doc_ids: set[str], filenames: set[str], texts: list[str],
                 skip_keys: set[str] | frozenset[str] | None = None):
        self.salt = cfg["export_salt"]
        self.skip_keys = SKIP_KEYS if skip_keys is None else skip_keys
        self.labels = cfg["public_stratum_labels"]
        self.doc_ids = doc_ids
        self.filenames = filenames
        # The private terms themselves are private: they come from the config, never from this file.
        terms = [t.lower() for t in cfg["export_private_terms"]] + [cfg["index"].lower()]
        terms += [p.lower() for p, _ in cfg["stratum_prefixes"]]
        terms += [u.split("//", 1)[-1].split(":")[0].lower() for u in (cfg["gen_url"], cfg["judge_url"])]
        self.terms = sorted({t for t in terms if t and t not in ("127.0.0.1", "localhost")})
        self.texts = [norm(t) for t in texts if len(norm(t)) >= 12]
        # Every run of six consecutive words from a private text: a blob containing one is refused.
        self.grams = {g for t in texts for g in word_grams(t)}
        self.errors: list[str] = []

    def h(self, did: str) -> str:
        return "d:" + hashlib.sha256((self.salt + did).encode()).hexdigest()[:12]

    def relabel(self, s: str) -> str:
        if s in self.doc_ids or s in self.filenames:
            return self.h(s)
        s = UUID.sub(lambda m: self.h(m.group()), s)
        return "|".join(self.labels.get(p, p) for p in s.split("|"))

    def check(self, s: str, path: str) -> None:
        if s in ALLOWED_VERBATIM:
            return
        low = s.lower()
        if not SAFE.match(s) or s.count(" ") > 6:
            self.errors.append(f"{path}: unsafe string {s[:40]!r}")
        elif any(t in low for t in self.terms):
            self.errors.append(f"{path}: private term in {s[:40]!r}")

    def clean(self, o: Any, path: str = "") -> Any:
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k in self.skip_keys:
                    continue
                k2 = self.relabel(str(k))
                self.check(k2, path + "/" + k2)
                out[k2] = self.clean(v, path + "/" + k2)
            return out
        if isinstance(o, (list, tuple)):
            return [self.clean(v, f"{path}[{i}]") for i, v in enumerate(o)]
        if isinstance(o, str):
            s = self.relabel(o)
            self.check(s, path)
            return s
        if o is None or isinstance(o, (bool, int, float)):
            return o
        self.errors.append(f"{path}: unexpected type {type(o).__name__}")
        return None


def blob_leak(name: str, blob: str, red: Redactor) -> str | None:
    """Why ``blob`` (an output file's text) must not be written, or None: a private text
    (normalised substring) or a run of :data:`GRAM_WORDS` words from one."""
    low = norm(blob)
    leaks = [t for t in red.texts if t in low]
    if leaks:
        return f"{name} contains {len(leaks)} private text(s)"
    runs = word_grams(blob) & red.grams
    if runs:
        return f"{name} contains {len(runs)} {GRAM_WORDS}-word run(s) from private text"
    return None


def usage_totals(src: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, name in CALL_LOGS.items():
        rows = [r for r in read_jsonl(src / name) if not r.get("probe")]
        st = Counter(str(r.get("status")) for r in rows)
        out[label] = {"requests": len(rows), "prompt_tokens": sum(int(r.get("pt") or 0) for r in rows),
                      "completion_tokens": sum(int(r.get("ct") or 0) for r in rows),
                      "zero_token_completions": sum(1 for r in rows if not int(r.get("ct") or 0)),
                      "status": dict(st), "probes_excluded": sum(1 for r in read_jsonl(src / name) if r.get("probe"))}
    return out


def per_record(src: Path, cfg: dict[str, Any], res: dict[str, Any], red: Redactor) -> dict[str, Any]:
    out: dict[str, Any] = {"legend": {
        "part2 / addendum_S": "config -> question id -> [reranked rank (null = not in top 10), found at b2048, "
                              "found at b5120]",
        "part3": "set -> arm -> question id -> [rank, b2048, b5120, b5120 quote a, b5120 quote b] (a/b only for M)",
        "k10": "grader -> reader|arm -> question id -> grade",
        "strata": "question id -> public stratum label"}}
    for key, name in (("part2", "results_main"), ("addendum_S", "results_S")):
        pr = res[name]["per_record_ranks"]
        out[key] = {c: {i: [x.get("rerank"), x.get("b2048"), x.get("b5120")] for i, x in per.items()}
                    for c, per in pr.items()}
    p3 = res["results_part3"]["per_record"]
    out["part3"] = {s: {a: {i: [x.get("rerank"), x.get("b2048"), x.get("b5120")]
                            + ([x.get("b5120_a"), x.get("b5120_b")] if s == "M" else [])
                            for i, x in per.items()} for a, per in p3[s].items()} for s in p3}
    answers = {r["k"]: r for r in read_jsonl(src / "k10-answers-261023.jsonl")}
    k10: dict[str, Any] = {}
    for g, name in K10_GRADE_FILES.items():
        grades = {r["k"]: r["v"] for r in read_jsonl(src / name)}
        rows: dict[str, dict[str, str]] = defaultdict(dict)
        for k, a in answers.items():
            reader, arm, qid = k.split("|", 2)
            gk = hashlib.sha256((qid + "\x00" + norm(a.get("answer") or "")).encode("utf-8")).hexdigest()
            v = grades.get(gk)
            if v is not None:
                rows[f"{reader}|{arm}"][qid] = v
        k10[g] = dict(rows)
    out["k10"] = k10
    strata = {}
    for name in ("questions-261023.jsonl", "questions-part3-261023.jsonl"):
        for r in read_jsonl(src / name):
            strata[r["record_id"]] = red.labels.get(r["stratum"], r["stratum"])
    for grp in ("P", "N", "W"):
        for qid in cfg["seed_questions"][grp]:
            strata[qid] = f"seed-{grp}"
    out["strata"] = strata
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG"))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    args = ap.parse_args(argv)
    if not args.private_config:
        raise SystemExit("a private config is required: pass --private-config or set CHUNKTRIAL_PRIVATE_CONFIG")
    cfg = json.loads(Path(args.private_config).read_text())
    src, dest, files = Path(cfg["out_dir"]), Path(args.dest), cfg["files"]

    res = {k: json.loads((src / v).read_text()) for k, v in files.items() if k.startswith("results_")}
    alt = {}
    for k in ("results_part3", "results_k10"):
        for j, pub in ALT_JUDGES.items():
            p = src / f"{files[k][:-5]}.{j}.json"
            if p.exists():
                alt[(k, pub)] = (p, json.loads(p.read_text()))

    doc_ids = set(json.loads((src / "manifest-261023.json").read_text()))
    qrows = read_jsonl(src / "questions-261023.jsonl") + read_jsonl(src / "questions-part3-261023.jsonl")
    filenames = {r["filename"] for r in qrows if r.get("filename")}
    filenames |= {f for v in cfg["seed_questions"]["P"].values() for f in v[1]}
    texts = [r.get(k) or "" for r in qrows for k in ("question", "answer_quote", "quote_a", "quote_b")]
    texts += [v[0] for v in cfg["seed_questions"]["P"].values()]
    texts += list(cfg["seed_questions"]["N"].values()) + list(cfg["seed_questions"]["W"].values())
    red = Redactor(cfg, doc_ids, filenames, texts)

    outputs: dict[str, Any] = {}
    for pub, (fkey, keys) in SECTIONS.items():
        outputs[f"{pub}.json"] = red.clean({k: res[fkey][k] for k in keys if k in res[fkey]}, pub)
    for (fkey, j), (_, data) in sorted(alt.items()):
        pub = {"results_part3": "part3", "results_k10": "k10"}[fkey]
        keys = SECTIONS[pub][1]
        outputs[f"{pub}.{j}.json"] = red.clean({k: data[k] for k in keys if k in data}, f"{pub}.{j}")
    pr = per_record(src, cfg, res, red)
    outputs["per_record.json"] = red.clean(pr, "per_record") | {"legend": pr["legend"]}
    outputs["gateway_usage.json"] = red.clean(usage_totals(src), "usage")

    from ragstack.provenance import experiment_provenance

    harness = (src / "HARNESS-as-run.sha256").read_text().split()
    # PREREG.sha256 is append-only: these five are its first five entries; later parts append more.
    prereg_sha = dict(zip(re.findall(r"^(\w{64})\s+PREREG", (src / "PREREG.sha256").read_text(), re.M)[:5],
                          ["part1", "part2", "addendum_S", "part3", "k10"], strict=True))

    def src_entry(p: Path, data: dict[str, Any]) -> dict[str, Any]:
        pv = data.get("provenance") or {}
        return {"sha256_prefix": sha_file(p)[:16],
                "run_provenance": {k: pv.get(k) for k in ("version", "describe", "commit", "dirty", "untracked",
                                                      "in_image", "recorded_at")},
                "citable": False}

    sources = {pub: src_entry(src / files[fk], res[fk]) for pub, (fk, _) in SECTIONS.items()}
    for (fk, j), (p, data) in sorted(alt.items()):
        sources[f"{ {'results_part3': 'part3', 'results_k10': 'k10'}[fk] }.{j}"] = src_entry(p, data)
    manifest = {
        "schema": "chunktrial-public-export/1",
        "status": "EXPLORATORY",
        "citable": False,
        "why_not_citable": ["the results were produced by an uncommitted harness (dirty tree, untracked files)",
                            "the primary grader was changed after results were seen"],
        # Host and warnings are context, not identity (comparison_key), and a host name can be private.
        "export_provenance": {k: v for k, v in experiment_provenance(repo=str(REPO)).items()
                              if k not in ("host", "warnings")},
        "as_run_harness_sha256": {"trial": harness[0], "section_aware_trial": harness[2], "parsing_test": harness[4]},
        "prereg_sha256_private_originals": {v: k for k, v in prereg_sha.items()},
        "sources": sources,
        "files": {},
        "redaction": {"string_rule": SAFE.pattern, "skipped_keys": sorted(SKIP_KEYS),
                      "doc_ids": "salted sha256, 12 hex (salt private)",
                      "stratum_labels": sorted(set(cfg["public_stratum_labels"].values()))},
    }
    verbatim = {k: manifest.pop(k) for k in ("export_provenance", "redaction", "why_not_citable")}
    manifest = red.clean(manifest, "manifest") | verbatim

    if red.errors:
        print(f"REFUSING to write: {len(red.errors)} redaction errors", file=sys.stderr)
        for e in red.errors[:60]:
            print("  " + e, file=sys.stderr)
        return 2
    blobs = {n: json.dumps(o, indent=1, sort_keys=True, ensure_ascii=True) + "\n" for n, o in outputs.items()}
    for n, b in blobs.items():
        why = blob_leak(n, b, red)
        if why:
            print(f"REFUSING to write: {why}", file=sys.stderr)
            return 3
    dest.mkdir(parents=True, exist_ok=True)
    for n, b in blobs.items():
        (dest / n).write_text(b)
        manifest["files"][n] = hashlib.sha256(b.encode()).hexdigest()[:16]
    (dest / "export_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    shown = dest.relative_to(REPO) if dest.resolve().is_relative_to(REPO) else dest
    print(json.dumps({"wrote": sorted([*blobs, "export_manifest.json"]), "dest": str(shown)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
