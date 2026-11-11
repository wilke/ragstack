"""The file-checkable gates of PREREG §6, as pure functions on loaded data.

Each function returns a list of failure strings (empty = the gate holds) and, where useful,
a descriptive dict beside it. The caller decides what each failure voids (§6: a gate on S,
ft512o64s or ft512o64 voids the decision; on S-regex, H3; on sent512s, C1).

Receipts are the per-shard ``<shard>.receipt.json`` of ``ingest-bulk-h2h.cwl`` (same shape
as ``ingest-bulk.cwl``'s); worker stderr is the staged-out ``<shard>.ingest-stderr.txt``
(the full stream) or, for the sentinel, the GoWe logs endpoint's ``data.stderr`` (a 256 KB
tail). The hashes follow the harness: a question-id set is the sha256 of the sorted ids, one
per line, LF-terminated, UTF-8.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

#: the receipt `chunking` object every S / S-regex receipt must carry in full (§6)
EXPECTED_CHUNKING: dict[str, Any] = {"v": 1, "method": "sentence", "chunk_size": 512, "size_tolerance": 0.25,
                                     "min_piece": 128, "ceiling": 640, "structure_cuts": "sections/1"}
#: the `chunk_params` the registry entry shows (`GET /v1/collections`) for both new arms
EXPECTED_CHUNK_PARAMS: dict[str, Any] = {"size_unit": "tokens", "size_tolerance": 0.25, "min_piece": 128,
                                         "structure_cuts": "sections/1"}
SEGMENTER = {"S": "punkt-abbrev-v1", "S-regex": "regex-abbrev-v1"}
PUNKT_WARNING = "NLTK Punkt is unavailable"
N_MAIN_DOCS = 241_930
N_SENTINEL_DOCS = 40
#: GoWe keeps only the last 256 KB of a stream in its DB (`maxLogCapture`); a tail of that
#: size may have lost the once-per-process warning, so it is not evidence either way
GOWE_TAIL_BYTES = 256 * 1024


def ids_sha256(ids: Iterable[str]) -> str:
    """sha256 of the sorted ids, one per line, LF-terminated, UTF-8 (``frozen-inputs.json``'s
    canonical form; equals the harness's ``noise.ids_sha256`` up to the trailing LF)."""
    body = "".join(f"{i}\n" for i in sorted(set(ids)))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------- receipts


def check_receipt(r: dict[str, Any], arm: str, label: str, *, expect_build: str | None,
                  expect_image_version: str | None = None) -> list[str]:
    """One shard receipt of ``arm`` (``S`` or ``S-regex``): complete, no failed document, the
    full ``chunking`` object, the arm's segmenter, the stamped build."""
    bad = []
    if r.get("status") != "completed":
        bad.append(f"{arm}/{label}: status {r.get('status')!r}")
    if r.get("n_docs_failed", 0) != 0:
        bad.append(f"{arm}/{label}: n_docs_failed {r.get('n_docs_failed')}")
    if r.get("error"):
        bad.append(f"{arm}/{label}: error {str(r.get('error'))[:80]!r}")
    ch = r.get("chunking")
    if not isinstance(ch, dict):
        bad.append(f"{arm}/{label}: no chunking object on the receipt")
    else:
        for k, v in EXPECTED_CHUNKING.items():
            if k not in ch:
                bad.append(f"{arm}/{label}: chunking.{k} missing")
            elif ch[k] != v:
                bad.append(f"{arm}/{label}: chunking.{k} = {ch[k]!r}, expected {v!r}")
    prov = r.get("provenance") or {}
    seg = prov.get("segmenter_version")
    if seg != SEGMENTER[arm]:
        bad.append(f"{arm}/{label}: segmenter_version {seg!r}, expected {SEGMENTER[arm]!r}")
    if not prov.get("image_commit"):
        bad.append(f"{arm}/{label}: image_commit is null (not run from a stamped image)")
    if expect_build is not None and str(prov.get("image_build")) != str(expect_build):
        bad.append(f"{arm}/{label}: image_build {prov.get('image_build')!r}, expected {expect_build!r}")
    if expect_image_version is not None and prov.get("image_version") != expect_image_version:
        bad.append(f"{arm}/{label}: image_version {prov.get('image_version')!r}, expected {expect_image_version!r}")
    ids = r.get("chunk_ids")
    if isinstance(ids, list) and len(set(ids)) != r.get("n_chunks"):
        bad.append(f"{arm}/{label}: n_chunks {r.get('n_chunks')} != distinct chunk_ids {len(set(ids))}")
    return bad


def check_arm_receipts(receipts: dict[str, dict[str, Any]], arm: str, *, expect_build: str | None,
                       sentinel_label: str = "sentinel", n_main_docs: int = N_MAIN_DOCS,
                       n_sentinel_docs: int = N_SENTINEL_DOCS, n_main_shards: int = 16,
                       expect_sentinel_chunks: int | None = None) -> tuple[list[str], dict[str, Any]]:
    """All of an arm's receipts, keyed by shard label (``main-00`` … ``main-15``, ``sentinel``).
    Returns the failures and the arm's census: expected store count (distinct chunk ids
    across every receipt where they are carried, else the sum of ``n_chunks``), documents,
    image commit."""
    bad: list[str] = []
    main = {k: v for k, v in receipts.items() if k != sentinel_label}
    if len(main) != n_main_shards:
        bad.append(f"{arm}: {len(main)} main-shard receipts, expected {n_main_shards}")
    if sentinel_label not in receipts:
        bad.append(f"{arm}: no sentinel receipt")
    for label, r in receipts.items():
        bad += check_receipt(r, arm, label, expect_build=expect_build)
    n_docs_main = sum(int(r.get("n_docs") or 0) for r in main.values())
    if n_docs_main != n_main_docs:
        bad.append(f"{arm}: {n_docs_main:,} documents in the main shards, expected {n_main_docs:,}")
    sent = receipts.get(sentinel_label) or {}
    if sent and sent.get("n_docs") != n_sentinel_docs:
        bad.append(f"{arm}: sentinel n_docs {sent.get('n_docs')}, expected {n_sentinel_docs}")
    if expect_sentinel_chunks is not None and sent and sent.get("n_chunks") != expect_sentinel_chunks:
        bad.append(f"{arm}: sentinel n_chunks {sent.get('n_chunks')}, expected {expect_sentinel_chunks} (dry run)")
    commits = {(r.get("provenance") or {}).get("image_commit") for r in receipts.values()}
    if len(commits) != 1:
        bad.append(f"{arm}: {len(commits)} distinct image_commit values across receipts")
    all_ids: set[str] = set()
    carried = True
    total = 0
    for r in receipts.values():
        ids = r.get("chunk_ids")
        total += int(r.get("n_chunks") or 0)
        if isinstance(ids, list) and ids:
            all_ids.update(ids)
        else:
            carried = False
    expected = len(all_ids) if carried and all_ids else total
    census = {"n_receipts": len(receipts), "n_docs_main": n_docs_main, "n_docs_sentinel": sent.get("n_docs"),
              "sentinel_n_chunks": sent.get("n_chunks"), "sum_n_chunks": total, "chunk_ids_carried": carried,
              "distinct_chunk_ids": len(all_ids) if carried else None, "expected_store_count": expected,
              "image_commit": next(iter(commits)) if len(commits) == 1 else None,
              "image_build": {str((r.get("provenance") or {}).get("image_build")) for r in receipts.values()},
              "segmenter_versions": sorted({str((r.get("provenance") or {}).get("segmenter_version"))
                                            for r in receipts.values()})}
    census["image_build"] = sorted(census["image_build"])
    return bad, census


def check_same_commit(census_s: dict[str, Any], census_r: dict[str, Any]) -> list[str]:
    """§2/§6: every S and S-regex receipt carries the same ``image_commit``."""
    a, b = census_s.get("image_commit"), census_r.get("image_commit")
    if not a or not b:
        return ["image_commit missing on one arm"]
    if a != b:
        return [f"image_commit differs between S ({a[:12]}) and S-regex ({b[:12]})"]
    return []


# ------------------------------------------------------------------- worker stderr


def punkt_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if PUNKT_WARNING in line)


def check_stderr(texts: dict[str, str | None], arm: str, *, tail_limited: bool = False) -> tuple[list[str], dict[str, Any]]:
    """Per shard label → the captured stderr (None = not captured). S: zero warning lines on
    every shard; S-regex: at least one on every shard. A missing capture fails. With
    ``tail_limited`` (the GoWe logs endpoint) a capture at or over 256 KB is not evidence
    either way and fails the gate."""
    bad: list[str] = []
    counts: dict[str, int | None] = {}
    sizes: dict[str, int | None] = {}
    for label, text in texts.items():
        if text is None:
            bad.append(f"{arm}/{label}: worker stderr not captured")
            counts[label] = None
            sizes[label] = None
            continue
        n = punkt_lines(text)
        size = len(text.encode("utf-8"))
        counts[label] = n
        sizes[label] = size
        if tail_limited and size >= GOWE_TAIL_BYTES:
            bad.append(f"{arm}/{label}: stderr tail at the 256 KB limit ({size} bytes): the once-per-process "
                       "warning may have scrolled off; the staged-out file is required")
            continue
        if arm == "S" and n != 0:
            bad.append(f"{arm}/{label}: {n} '{PUNKT_WARNING}' line(s) in worker stderr (expected 0)")
        if arm == "S-regex" and n < 1:
            bad.append(f"{arm}/{label}: no '{PUNKT_WARNING}' line in worker stderr (expected ≥ 1)")
    return bad, {"warning_lines": counts, "bytes": sizes, "total_warning_lines": sum(c or 0 for c in counts.values())}


# ------------------------------------------------------------------- store counts


def check_store_counts(arm: str, expected: int | None, qdrant: int | None, es: int | None) -> list[str]:
    """Qdrant = Elasticsearch = the receipts' expected count, exactly."""
    if expected is None or qdrant is None or es is None:
        return [f"{arm}: store counts incomplete (expected {expected}, qdrant {qdrant}, es {es})"]
    bad = []
    if qdrant != es:
        bad.append(f"{arm}: qdrant {qdrant:,} != elasticsearch {es:,}")
    if qdrant != expected:
        bad.append(f"{arm}: qdrant {qdrant:,} != receipts {expected:,}")
    if es != expected:
        bad.append(f"{arm}: elasticsearch {es:,} != receipts {expected:,}")
    return bad


def check_reused_census(arm: str, frozen: dict[str, Any] | None, live: dict[str, Any] | None) -> list[str]:
    """Reused arms: the live census equals the census recorded at the freeze (qdrant and es)."""
    if not frozen or frozen.get("qdrant") is None or frozen.get("es") is None:
        return [f"{arm}: no frozen census"]
    if not live or live.get("qdrant") is None or live.get("es") is None:
        return [f"{arm}: no live census"]
    bad = []
    for store in ("qdrant", "es"):
        if int(live[store]) != int(frozen[store]):
            bad.append(f"{arm}: live {store} {int(live[store]):,} != frozen {int(frozen[store]):,}")
    if int(frozen["qdrant"]) != int(frozen["es"]):
        bad.append(f"{arm}: frozen census qdrant {frozen['qdrant']} != es {frozen['es']}")
    return bad


# ------------------------------------------------------------------- inputs, sets, retrieval


def check_shard_hashes(pinned: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Same input: the shard files' sha256 equal the §9.1 pins (label → sha256)."""
    bad = []
    for label, want in pinned.items():
        got = actual.get(label)
        if got is None:
            bad.append(f"shard {label}: not hashed")
        elif got != want:
            bad.append(f"shard {label}: sha256 {got[:12]} != pinned {want[:12]}")
    return bad


def check_sets(decision_ids: Iterable[str], sensitivity_ids: Iterable[str], frozen: dict[str, Any]) -> list[str]:
    """Decision set: n and the id-list sha256 equal the freeze (three-arm output)."""
    bad = []
    for name, ids in (("decision", decision_ids), ("sensitivity", sensitivity_ids)):
        ids = list(ids)
        want = frozen.get(name) or {}
        if len(ids) != want.get("n"):
            bad.append(f"{name} set: n = {len(ids)}, frozen {want.get('n')}")
        got = ids_sha256(ids)
        if got != want.get("sha256"):
            bad.append(f"{name} set: ids sha256 {got[:12]} != frozen {str(want.get('sha256'))[:12]}")
    return bad


def check_retrieval(score: dict[str, Any], arms: Iterable[str], n_questions: int = 2437) -> tuple[list[str], dict[str, Any]]:
    """Per setting, from ``score.json``: no missing row per arm, no refusal, one server
    version label per arm (the cross-setting check is :func:`check_one_version`)."""
    bad: list[str] = []
    missing = score.get("missing_by_arm") or {}
    for a in arms:
        if missing.get(a):
            bad.append(f"{a}: {missing[a]} question(s) without an ok retrieval row")
    if score.get("n_questions") != n_questions:
        bad.append(f"{score.get('n_questions')} questions scored, expected {n_questions}")
    for r in score.get("refusals") or []:
        bad.append(f"score refusal: {r}")
    versions = score.get("server_versions") or {}
    for a, vs in versions.items():
        if len(vs) != 1:
            bad.append(f"{a}: {len(vs)} server versions in its rows")
    return bad, {"server_versions": versions, "missing_by_arm": missing}


def check_one_version(versions_by_setting: dict[str, dict[str, list[str]]]) -> tuple[list[str], str | None]:
    """One ``version+git_sha`` label across every arm of every setting."""
    labels = {v for per in versions_by_setting.values() for vs in per.values() for v in vs}
    if len(labels) == 1:
        return [], next(iter(labels))
    if not labels:
        return ["no server version recorded on any row"], None
    return [f"{len(labels)} server versions across the run: " + ", ".join(sorted(labels))], None


def check_request_fields(sent: dict[str, Any], frozen: dict[str, Any]) -> list[str]:
    """The request fields (`rerank`, `use_graph`, `retrieval_mode`, `top_k`) equal the frozen
    Addendum C config of the setting."""
    bad = []
    for k in ("rerank", "use_graph", "retrieval_mode", "top_k"):
        if k not in sent:
            bad.append(f"request field {k} not recorded")
        elif sent[k] != frozen.get(k):
            bad.append(f"request field {k} = {sent[k]!r}, frozen {frozen.get(k)!r}")
    return bad


# ------------------------------------------------------------------- embedding drift


def check_reembed(cosines: list[dict[str, Any]], threshold: float = 0.999, n_expected: int = 200) -> tuple[list[str], dict[str, Any]]:
    """Every re-embedded chunk's cosine to its stored vector ≥ threshold."""
    bad = []
    vals = [float(c["cosine"]) for c in cosines if c.get("cosine") is not None]
    if len(vals) != n_expected:
        bad.append(f"re-embed: {len(vals)} cosines, expected {n_expected}")
    below = [c for c in cosines if c.get("cosine") is not None and float(c["cosine"]) < threshold]
    if below:
        bad.append(f"re-embed: {len(below)} chunk(s) below cosine {threshold} (min {min(vals):.6f})")
    desc = {"n": len(vals), "min": min(vals) if vals else None, "threshold": threshold, "n_below": len(below)}
    return bad, desc


def check_model_ids(freeze: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """The embedding and reranker model ids recorded at the run equal those at the freeze."""
    bad = []
    for k in ("embedding_model", "reranker_model"):
        if not freeze.get(k) or not run.get(k):
            bad.append(f"{k}: not recorded at {'the freeze' if not freeze.get(k) else 'the run'}")
        elif freeze[k] != run[k]:
            bad.append(f"{k}: run {run[k]!r} != freeze {freeze[k]!r}")
    return bad


# ------------------------------------------------------------------- descriptives


def chunk_id_overlap(ids_s: Iterable[str], ids_r: Iterable[str]) -> dict[str, Any]:
    """§4.3: the share of S chunk ids (uuid5 of doc_id:start:end) also present in S-regex."""
    a, b = set(ids_s), set(ids_r)
    inter = len(a & b)
    return {"n_S": len(a), "n_S_regex": len(b), "shared": inter,
            "share_of_S": round(inter / len(a), 6) if a else None,
            "share_of_S_regex": round(inter / len(b), 6) if b else None}


def top5_drift(old_rows: Iterable[dict[str, Any]], new_rows: Iterable[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """Share of questions whose top-k chunk-id set equals the old run's (reused arms; §2).
    Rows carry ``question_id`` and ``hits`` (each with ``chunk_id``); only questions present
    in both are compared."""
    def sets(rows):
        out = {}
        for r in rows:
            if r.get("status", "ok") != "ok":
                continue
            out[r["question_id"]] = frozenset(str(h.get("chunk_id")) for h in (r.get("hits") or [])[:k])
        return out
    o, n = sets(old_rows), sets(new_rows)
    common = sorted(set(o) & set(n))
    same = sum(1 for q in common if o[q] == n[q])
    return {"k": k, "n_compared": len(common), "n_same": same,
            "share_same": round(same / len(common), 6) if common else None}
