"""Retrieval-only re-score of part 4's questions under glossary FILES: ``part4-glossary``.

EXPLORATORY. Not part of the confirmatory part 4 (:mod:`deployed_docs_part4`),
whose code path this module does not change: it is a separate subcommand of
``deployed_docs_trial.py`` that reads the part-4 private config and the part-4
questions file (valid rows), builds the arms in memory from the live index
exactly as part 4 does, and asks one question of each glossary condition: does
every gold quote reach the delivered context (part 4's reader-independent
``found``)? No reader, grader or gateway is called, and no index is written.

A **glossary condition** is ``--glossary-file NAME=PATH`` (repeatable; the first
is the reference the others are contrasted with). Part 4 already uses "arm"
for the chunker and "condition" for the context budget (B4096, k10); a
glossary is a third axis, and it is per DOCUMENT, so one file renders every
arm. For each file:

1. it is read in full and validated by :func:`read_glossary_file`;
2. every page is checked against its row (joined by ``doc_id``) with
   :func:`resolve_documents` on the text reassembled from the live chunks, as
   ``backfill_symbol_glossary --glossary-file`` checks: a ``mismatch`` or a
   ``missing`` row refuses the run, and so does an ``unverifiable`` page unless
   ``--glossary-allow-unverifiable`` is given (the name the loader and the
   backfill use);
3. only when EVERY file resolved, the arms are built with every chunk's
   ``symbol_glossary`` rendered from that file's stamped rows and header config
   (:func:`deployed_docs_trial.build_arms` with ``glossaries=``): L at each live
   row's ``start_char`` (the backfill's rendering), T/X/SEM at each chunk's
   ``start_char`` (the load's ``render_chunks``).

Chunk ids and text are the same under every condition (checked); only the
glossary field, and so the BM25 glossary leg and the reranker passage, differ.
The BM25 glossary leg uses part 4's harness constant
(``section_aware_trial.GLOSSARY_BOOST`` = 1), not the library default
``TEXT_SYMBOL_GLOSSARY_BOOST`` = 0.0: the re-score models a boost-1
deployment.

With any ``--glossary-file``, L is re-rendered from the file too, so "the live
field as served" is not one of the conditions. To compare against what is
served, pass a rules file built from the live text (``glossary_file.py build``
over the reassembled pages) as a condition.

Caches: like part 4, retrieval appends to the output directory's ``emb/`` and
``rerank_cache.jsonl``. Embeddings key on the chunk text (the same under every
condition); rerank entries key on sha256(question + passage), and the passage
includes the glossary, so no condition reads another's scores. Nothing else
in the part-4 output directory is written.

Multiplicity: ``design.n_tests`` = arms x budget conditions x non-reference
conditions, every test two-sided and uncorrected (exploratory). Each contrast
carries ``min_attainable_p``: the sign-flip floor in the regime its p was
computed in (``exact`` or ``monte_carlo``) and the exact McNemar floor.

**The shuffled control** (``--shuffle-rendered NAME=SEED``, repeatable; glossary
benchmark round 2): condition NAME's rendered fields are permuted, per arm (each
arm is its own index), among the chunks of that arm that carry a non-empty one
(:func:`ragstack.ingestion.glossary_shuffle.shuffle_rendered`, keyed by chunk
id, seeded, deterministic): the populated chunks and the multiset of strings
are unchanged, only which chunk carries which string. To contrast true with
shuffled, pass the same file under two names (``qwen36p=F``,
``qwen36p-shuf=F``) and shuffle the second. The backfill's flag takes a bare
SEED (one index per run); here several conditions share a run, so the flag
names its condition. Each shuffled condition's record carries ``shuffle``:
per arm, the seed, ``permutation_sha256``, populated/empty counts, residual
fixed points and the multiset hash.

Exit codes: 0 done; 1 the manipulation check or the same-chunks check failed;
6 (``GLOSSARY_REFUSED_EXIT_CODE``) a glossary file was refused. Nothing is
built or retrieved before every file has resolved.

The results file records, per condition, the file's sha256, header producer,
created, counts and config, ``glossary_versions`` and degraded reasons, and the
verification counts; the same records are in the ``provenance`` block, under
``glossary_files``, beside :func:`ragstack.provenance.experiment_provenance`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import deployed_docs_part4 as p4  # noqa: E402
import deployed_docs_trial as dt  # noqa: E402
import section_aware_trial as sat  # noqa: E402
from section_aware_trial import C, log, norm  # noqa: E402

RESULTS_SCHEMA = "part4-glossary/1"
DEFAULT_RESULTS = "part4-glossary-results.json"
EXIT_OK, EXIT_HARD_STOP = p4.EXIT_OK, p4.EXIT_HARD_STOP
#: A page whose text differs from the reassembly of its own live rows (the T/X
#: arms are cut from the page text, the L arm is the rows): never rendered.
PAGE_TEXT_DIFFERS = "page_text_differs"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class GlossaryRefused(Exception):  # noqa: N818 - a refusal, reported and exited on
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class GlossaryCondition:
    name: str
    path: Path
    #: doc_id -> the stamped glossary to render (``GlossaryRow.stamped()``).
    glossaries: dict[str, Any]
    #: the file's header config: what every chunk is rendered under.
    config: Any
    #: what the results and the provenance block record about the file.
    record: dict[str, Any]


@dataclass
class GEnv:
    """The seams (production: :func:`production_genv`; tests inject fakes)."""
    load_pages: Callable[[], tuple[dict[str, dict[str, Any]], dict[str, Any]]]
    #: (pages, arm names, doc_id -> stamped glossary, config) -> {arm: chunks}
    build_arms: Callable[[dict[str, dict[str, Any]], list[str], Mapping[str, Any], Any], dict[str, list[C]]]
    ntok: Callable[[str], int]
    retrieve: Callable[[str, list[C], list[dict[str, Any]]], dict[str, list[str]]]
    provenance: Callable[[], dict[str, Any]]


def parse_specs(values: list[str] | None) -> list[tuple[str, Path]]:
    """``NAME=PATH`` per ``--glossary-file``, in order; names unique."""
    out: list[tuple[str, Path]] = []
    for v in values or []:
        name, sep, path = v.partition("=")
        if not sep or not path or not _NAME.fullmatch(name):
            raise SystemExit(f"--glossary-file wants NAME=PATH (NAME: letters, digits, '_.-'), got {v!r}")
        if any(n == name for n, _ in out):
            raise SystemExit(f"--glossary-file: condition name {name!r} given twice")
        out.append((name, Path(path)))
    if not out:
        raise SystemExit("part4-glossary needs at least one --glossary-file NAME=PATH")
    return out


def parse_shuffles(values: list[str] | None, names: list[str]) -> dict[str, str]:
    """``NAME=SEED`` per ``--shuffle-rendered``; NAME must be a declared condition."""
    from ragstack.ingestion.glossary_shuffle import ShuffleError, check_seed

    out: dict[str, str] = {}
    for v in values or []:
        name, sep, seed = v.partition("=")
        if not sep or name not in names:
            raise SystemExit(f"--shuffle-rendered wants NAME=SEED with NAME one of the "
                             f"--glossary-file conditions {names}, got {v!r}")
        if name in out:
            raise SystemExit(f"--shuffle-rendered: condition {name!r} given twice")
        try:
            out[name] = check_seed(seed)
        except ShuffleError as e:
            raise SystemExit(f"--shuffle-rendered {name}: {e}") from e
    return out


def shuffle_arms(arms: dict[str, list[C]], seed: str) -> tuple[dict[str, list[C]], dict[str, Any]]:
    """Every arm's rendered glossary fields permuted among its populated chunks
    (new :class:`C` objects; the input is not mutated). Returns the arms and the
    per-arm :meth:`ShuffleResult.record`."""
    from ragstack.ingestion.glossary_shuffle import shuffle_rendered

    out: dict[str, list[C]] = {}
    records: dict[str, Any] = {}
    for a, chunks in arms.items():
        fields = {c.id: c.glossary for c in chunks}
        if len(fields) != len(chunks):
            raise SystemExit(f"part4-glossary: arm {a} has duplicate chunk ids; cannot shuffle")
        res = shuffle_rendered(fields, seed)
        out[a] = [replace(c, glossary=res.rendered(c.id, c.glossary)) for c in chunks]
        records[a] = res.record()
    return out, records


def resolve_condition(name: str, path: Path, pages: dict[str, dict[str, Any]],
                      allow_unverifiable: bool = False) -> GlossaryCondition:
    """Read ``path`` and check every page against it; :class:`GlossaryRefused`
    on any problem. Pure apart from reading the file."""
    from ragstack.ingestion.glossary_file import (
        GlossaryFileError,
        file_provenance,
        read_glossary_file,
        resolve_documents,
    )
    from ragstack.ingestion.reassembly import reassemble
    from ragstack.ingestion.symbol_glossary import config_dict

    try:
        gfile = read_glossary_file(path, doc_ids=pages)
    except (GlossaryFileError, OSError) as e:
        raise GlossaryRefused(f"refusing: glossary file {str(path)!r} ({name}): {e}") from e
    docs, differs = [], []
    for did, p in sorted(pages.items()):
        ra = reassemble(did, p["rows"])
        if ra.text != p["text"]:
            differs.append((did, PAGE_TEXT_DIFFERS))
            continue
        docs.append(ra)
    res = resolve_documents(gfile, docs, allow_unverifiable=allow_unverifiable)
    res.problems = differs + res.problems
    if res.problems:
        raise GlossaryRefused(f"{res.refusal(repr(str(path)))} [condition {name}]")
    record = {"name": name, **file_provenance(gfile, res), "config": config_dict(gfile.config),
              "rows_outside_pages": int(gfile.header["counts"]["documents"]) - len(gfile.rows),
              "allow_unverifiable": allow_unverifiable}
    return GlossaryCondition(name, path, dict(res.glossaries), gfile.config, record)


ALTERNATIVE = "two-sided"
POWER_NOTE = ("no power computation for this re-score. The part-4 power table (`part4 --power-table`, prereg §6) "
              "is the reference for the design's sensitivity. A null here is readable only where "
              "min_attainable_p is below the threshold and the discordant count could carry an effect "
              "the reader cares about; with few non-zero pages or few discordants, no outcome can be "
              "significant and a null says nothing.")


def min_attainable_p(n_pages_nonzero: int, discordant: int, *, exact: bool, n_flips: int) -> dict[str, Any]:
    """The smallest p each two-sided test can return here.

    * ``sign_flip``: the page sign-flip test's ``p`` as :func:`chunktrial_stats.sign_flip_test`
      computes it. In the ``exact`` regime (k <= ``EXACT_MAX_PAGES`` non-zero pages,
      enumerated) the floor is ``2^(1-k)`` (the observed vector and its mirror; 1 at
      k = 0). In the ``monte_carlo`` regime p = (1 + #) / (1 + n_flips), so the floor
      is ``1 / (1 + n_flips)`` whatever k is.
    * ``mcnemar_two_sided``: the exact McNemar test over D discordant questions,
      ``2^(1-D)``; 1 when D is 0."""
    def floor2(n: int) -> float:
        return 1.0 if n <= 0 else min(1.0, 2.0 ** (1 - n))

    return {"sign_flip": floor2(n_pages_nonzero) if exact else 1.0 / (1 + n_flips),
            "regime": "exact" if exact else "monte_carlo",
            "mcnemar_two_sided": floor2(discordant),
            "n_pages_nonzero": n_pages_nonzero, "discordant": discordant}


def with_floor(test: dict[str, Any]) -> dict[str, Any]:
    if "gained" in test:
        test["min_attainable_p"] = min_attainable_p(
            int(test["n_pages_nonzero"]), int(test["gained"]) + int(test["lost"]),
            exact=test.get("p_exact") is not None, n_flips=int(test["n_flips"]))
    return test


def manifest_check(pages: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
    """Part 4's manifest, checked and never written (the part-4 outputs are read-only here)."""
    man = {d: {"chunks": len(p["rows"]), "sha256": dt.sha(p["text"])} for d, p in sorted(pages.items())}
    if not path.exists():
        return {"manifest": "absent", "docs": len(man)}
    old = json.loads(path.read_text())
    drift = sorted(d for d in set(old) | set(man) if old.get(d) != man.get(d))
    return {"manifest": "checked", "docs": len(man), "drift_docs": len(drift)}


def rescore(cfg_path: Path, out: Path, env: GEnv, specs: list[tuple[str, Path]], *,
            allow_unverifiable: bool = False, results: str = DEFAULT_RESULTS,
            shuffles: Mapping[str, str] | None = None) -> int:
    from ragstack.ingestion.glossary_file import GLOSSARY_REFUSED_EXIT_CODE

    cfg = dt.load_private_config(str(cfg_path))
    p4c = p4.part4_config(cfg)
    files = p4c["files"]
    rpath = Path(results) if Path(results).is_absolute() else out / results
    repo = sat._REPO_PY.parent.resolve()
    rp = rpath.resolve()
    if rp == repo or repo in rp.parents:
        raise SystemExit("part4-glossary: refusing to write the results inside the repository")
    qpath = out / files["questions"]
    if not qpath.is_file():
        raise SystemExit(f"part4-glossary: the part-4 questions file is missing: {files['questions']}")
    valid = [r for r in sat.read_jsonl(qpath) if r.get("status") == "valid"]
    pages, imeta = env.load_pages()
    man = manifest_check(pages, out / files["manifest"])
    # ---- every file resolves before anything is built, retrieved or scored
    conds: list[GlossaryCondition] = []
    for name, path in specs:
        try:
            conds.append(resolve_condition(name, path, pages, allow_unverifiable))
        except GlossaryRefused as e:
            log(e.message)
            return GLOSSARY_REFUSED_EXIT_CODE
    for gc in conds:
        log(f"part4-glossary: {gc.name}: {json.dumps(gc.record['documents'])} "
            f"versions {gc.record['glossary_versions']}")
    arm_names, conditions = list(p4c["arms"]), p4c["conditions"]
    built = {gc.name: env.build_arms(pages, arm_names, gc.glossaries, gc.config) for gc in conds}
    for gc in conds:
        if gc.name in (shuffles or {}):
            seed = (shuffles or {})[gc.name]
            built[gc.name], per_arm = shuffle_arms(built[gc.name], seed)
            gc.record["shuffle"] = {"seed": seed, "arms": per_arm}
            log(f"part4-glossary: {gc.name}: shuffled rendered fields, seed {seed!r}, "
                f"{ {a: r['permutation_sha256'][:12] for a, r in per_arm.items()} }")
    ref = conds[0].name
    exp = p4c["expected_chunks"]
    manip = {a: {"chunks": len(built[ref].get(a, [])), "expected": exp.get(a),
                 "ok": len(built[ref].get(a, [])) == exp.get(a)} for a in arm_names}
    if not all(v["ok"] for v in manip.values()):
        log(f"part4-glossary: manipulation check failed: {json.dumps(manip)}")
        return EXIT_HARD_STOP
    for g, arms in built.items():
        for a in arm_names:
            if [(c.id, c.content) for c in arms[a]] != [(c.id, c.content) for c in built[ref][a]]:
                log(f"part4-glossary: internal: arm {a} has different chunks under {g} than under {ref}")
                return EXIT_HARD_STOP
    # ---- retrieval and the delivered contexts, per glossary condition
    per_item: dict[str, Any] = {r["record_id"]: {} for r in valid}
    for gc in conds:
        g = gc.name
        by_id = {c.id: c for cs in built[g].values() for c in cs}
        for a in arm_names:
            ranked = env.retrieve(a, built[g][a], valid)
            for r in valid:
                q = r["record_id"]
                golds = [norm(x) for x in r["gold"]]
                cell = per_item[q].setdefault(g, {}).setdefault(a, {})
                for cond, spec in conditions.items():
                    lst = ranked[q]
                    cx = (p4.budget_context(lst, int(spec["budget"]), by_id.__getitem__, env.ntok)
                          if "budget" in spec else p4.top_context(lst, int(spec["top"]), by_id.__getitem__))
                    texts = [norm(c.content) for c in cx.chunks]
                    cell[cond] = {"found": all(any(sat.hit(x, t) for t in texts) for x in golds),
                                  "chunks": len(cx.ids), "ctx_tokens": cx.ctx_tokens,
                                  "chunks_with_glossary": sum(1 for c in cx.chunks if c.glossary)}
    # ---- summary: rates, and each condition against the reference (two-sided, exploratory)
    qids = [r["record_id"] for r in valid]
    doc_of = {r["record_id"]: r["doc_id"] for r in valid}
    an, seed = p4c["analysis"], int(p4c["seed"])
    rate = {gc.name: {a: {cond: (round(sum(bool(per_item[q][gc.name][a][cond]["found"]) for q in qids) / len(qids), 4)
                                 if qids else None) for cond in conditions} for a in arm_names} for gc in conds}
    contrasts: dict[str, Any] = {}
    for gc in conds[1:]:
        contrasts[f"{gc.name}-vs-{ref}"] = {
            a: {cond: with_floor(p4.contrast_test([doc_of[q] for q in qids],
                                                  [int(bool(per_item[q][gc.name][a][cond]["found"])) for q in qids],
                                                  [int(bool(per_item[q][ref][a][cond]["found"])) for q in qids],
                                                  "superiority", an, seed, alternative=ALTERNATIVE))
                for cond in conditions} for a in arm_names}
    n_tests = len(arm_names) * len(conditions) * (len(conds) - 1)
    records = {gc.name: gc.record for gc in conds}
    prov = {**env.provenance(), "glossary_files": records}
    citable = bool(prov.get("commit")) and prov.get("dirty") is False and prov.get("untracked") == 0
    report = {
        "schema": RESULTS_SCHEMA,
        "exploratory": True,
        "note": ("retrieval-only: found = every gold quote in the delivered context (part 4's reader-independent "
                 "metric); one glossary file per condition; contrasts are two-sided against the reference"),
        "config_sha256": p4.file_sha(cfg_path),
        "questions": {"file": files["questions"], "sha256": p4.file_sha(qpath), "n_valid": len(valid)},
        "provenance": prov, "citable": citable,
        "glossary_conditions": [gc.record for gc in conds],
        "reference_condition": ref,
        "shuffled_conditions": dict(shuffles or {}),
        "design": {"arms": arm_names, "conditions": conditions, "seed": seed,
                   "analysis": {k: an[k] for k in ("n_perm", "n_boot", "ci_level")},
                   "allow_unverifiable": allow_unverifiable,
                   "n_tests": n_tests,
                   "family": {"definition": "arms x budget conditions x non-reference glossary conditions, "
                                            "each contrasted with the reference",
                              "arms": len(arm_names), "budget_conditions": len(conditions),
                              "non_reference_conditions": len(conds) - 1, "alternative": ALTERNATIVE,
                              "correction": "none (exploratory): read every p against n_tests"}},
        "power": POWER_NOTE,
        "index": {k: v for k, v in imeta.items() if k != "reassembly_failed_ids"},
        "manifest": man,
        "manipulation_check": manip,
        "chunks_with_glossary": {g: {a: sum(1 for c in arms[a] if c.glossary) for a in arm_names}
                                 for g, arms in built.items()},
        "found": rate,
        "contrasts": contrasts,
        "per_item": per_item,
    }
    p4.write_json_atomic(rpath, report)
    log(f"part4-glossary: wrote {rpath}")
    return EXIT_OK


def make_build(out: Path, urls: list[str]) -> Callable[[dict[str, dict[str, Any]], list[str], Mapping[str, Any], Any],
                                                       dict[str, list[C]]]:
    """Part 4's arm building (:func:`deployed_docs_trial.build_arms` and
    ``build_sem_arms``) with every chunk rendered from one file's glossaries."""
    def build(pages: dict[str, dict[str, Any]], arms: list[str], glossaries: Mapping[str, Any],
              config: Any) -> dict[str, list[C]]:
        built, _ = dt.build_arms(pages, glossaries=glossaries, glossary_config=config)
        if any(a.startswith("SEM") for a in arms):
            built.update(dt.build_sem_arms(out, pages, urls, glossaries=glossaries, glossary_config=config))
        return {a: built[a] for a in arms}

    return build


def production_genv(out: Path, embed_urls: str, rerank_url: str) -> GEnv:
    """Part 4's production seams (:func:`deployed_docs_part4.production_env`); arms from :func:`make_build`."""
    base = p4.production_env(out, embed_urls, rerank_url)
    return GEnv(base.load_pages, make_build(out, [u for u in embed_urls.split(",") if u]), base.ntok,
                base.retrieve, base.provenance)


def cmd_part4_glossary(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    specs = parse_specs(args.glossary_file)
    shuffles = parse_shuffles(getattr(args, "shuffle_rendered", None), [n for n, _ in specs])
    env = production_genv(out, args.embed_urls, args.rerank_url)
    return rescore(Path(args.private_config), out, env, specs,
                   allow_unverifiable=args.glossary_allow_unverifiable, results=args.results,
                   shuffles=shuffles)


def add_parser(sub: Any) -> None:
    r = sub.add_parser("part4-glossary",
                       help="exploratory retrieval-only re-score of part 4's questions, one glossary file per condition")
    r.add_argument("--embed-urls", default=",".join(f"http://127.0.0.1:{p}" for p in range(9001, 9007)))
    r.add_argument("--rerank-url", default="http://127.0.0.1:50052")
    r.add_argument("--glossary-file", action="append", metavar="NAME=PATH", required=True,
                   help="a glossary condition: NAME and its glossary.jsonl (repeatable; the first is the reference)")
    r.add_argument("--glossary-allow-unverifiable", action="store_true",
                   help="render pages whose live chunks do not reassemble their full text (else refused)")
    r.add_argument("--shuffle-rendered", action="append", metavar="NAME=SEED", default=None,
                   help="the shuffled control: permute condition NAME's rendered glossary fields "
                        "among each arm's populated chunks, seeded (repeatable)")
    r.add_argument("--results", default=DEFAULT_RESULTS,
                   help=f"results file (relative to the output directory; default {DEFAULT_RESULTS})")
