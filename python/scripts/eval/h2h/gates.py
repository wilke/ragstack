"""The file-checkable gates of PREREG §6, as pure functions on loaded data.

Each function returns a list of failure strings (empty = the gate holds) and, where useful,
a descriptive dict beside it. Every failure string starts with a bracketed gate code
(``[segmenter] S/main-03: …``) so the §4.5 sentence can list *each gate with its count*
(:func:`gate_counts`) while the full list stays in the report's ``gates`` block. The caller
decides what each failure voids (§6: a gate on S, ft512o64s or ft512o64 voids the decision;
on S-regex, H3; on sent512s, C1). **Absent is not passed**: a gate whose input is missing
fails with its own code and the word "absent".

Receipts are the per-shard ``<shard>.receipt.json`` of ``ingest-bulk-h2h.cwl`` (same shape
as ``ingest-bulk.cwl``'s); worker stderr is the staged-out ``<shard>.ingest-stderr.txt``
(the full stream) or, for the sentinel, the GoWe logs endpoint's ``data.stderr`` (a 256 KB
tail). The hashes follow the harness: a question-id set is the sha256 of the sorted ids, one
per line, LF-terminated, UTF-8.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
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
N_SHARDS = 17
#: GoWe keeps only the last 256 KB of a stream in its DB (`maxLogCapture`); a tail of that
#: size may have lost the once-per-process warning, so it is not evidence either way
GOWE_TAIL_BYTES = 256 * 1024
VERSION_LABEL = re.compile(r"^\d+\.\d+\.\d+\+[0-9a-f]{7,40}$")
_CODE = re.compile(r"^\[([A-Za-z0-9_-]+)\]")


def gate_counts(gates: Iterable[str]) -> dict[str, int]:
    """``{gate code: count}`` over failure strings (``[code] …``); uncoded strings count under
    ``other``."""
    c: Counter[str] = Counter()
    for g in gates:
        m = _CODE.match(g)
        c[m.group(1) if m else "other"] += 1
    return dict(sorted(c.items()))


def ids_sha256(ids: Iterable[str]) -> str:
    """sha256 of the sorted ids, one per line, LF-terminated, UTF-8 (``frozen-inputs.json``'s
    canonical form)."""
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
                  expect_commit: str | None, expect_image_version: str | None = None) -> list[str]:
    """One shard receipt of ``arm`` (``S`` or ``S-regex``): complete, no failed document, the
    full ``chunking`` object, the arm's segmenter, the stamped build and commit. A missing
    expectation (``None``) is itself a failure: absent is not passed."""
    bad = []
    w = f"{arm}/{label}"
    if r.get("status") != "completed":
        bad.append(f"[receipt_status] {w}: status {r.get('status')!r}")
    if r.get("n_docs_failed", 0) != 0:
        bad.append(f"[docs_failed] {w}: n_docs_failed {r.get('n_docs_failed')}")
    if r.get("error"):
        bad.append(f"[receipt_error] {w}: error {str(r.get('error'))[:80]!r}")
    ch = r.get("chunking")
    if not isinstance(ch, dict):
        bad.append(f"[chunking] {w}: no chunking object on the receipt")
    else:
        for k, v in EXPECTED_CHUNKING.items():
            if k not in ch:
                bad.append(f"[chunking] {w}: chunking.{k} missing")
            elif ch[k] != v:
                bad.append(f"[chunking] {w}: chunking.{k} = {ch[k]!r}, expected {v!r}")
    prov = r.get("provenance") or {}
    seg = prov.get("segmenter_version")
    if seg != SEGMENTER[arm]:
        bad.append(f"[segmenter] {w}: segmenter_version {seg!r}, expected {SEGMENTER[arm]!r}")
    commit = prov.get("image_commit")
    if not commit:
        bad.append(f"[image_commit] {w}: image_commit is null (not run from a stamped image)")
    elif expect_commit is None:
        bad.append(f"[image_commit] {w}: no pinned image commit to compare against (absent)")
    elif commit != expect_commit:
        bad.append(f"[image_commit] {w}: image_commit {commit[:12]} != pinned {expect_commit[:12]}")
    if expect_build is None:
        bad.append(f"[image_build] {w}: no pinned build number to compare against (absent)")
    elif str(prov.get("image_build")) != str(expect_build):
        bad.append(f"[image_build] {w}: image_build {prov.get('image_build')!r}, expected {expect_build!r}")
    if expect_image_version is not None and prov.get("image_version") != expect_image_version:
        bad.append(f"[image_version] {w}: image_version {prov.get('image_version')!r}, expected {expect_image_version!r}")
    ids = r.get("chunk_ids")
    if isinstance(ids, list) and len(set(ids)) != r.get("n_chunks"):
        bad.append(f"[chunk_ids] {w}: n_chunks {r.get('n_chunks')} != distinct chunk_ids {len(set(ids))}")
    return bad


def check_arm_receipts(receipts: dict[str, dict[str, Any]], arm: str, *, expect_build: str | None,
                       expect_commit: str | None, expect_sentinel_chunks: int | None,
                       sentinel_label: str = "sentinel", n_main_docs: int = N_MAIN_DOCS,
                       n_sentinel_docs: int = N_SENTINEL_DOCS, n_main_shards: int = N_SHARDS - 1) -> tuple[list[str], dict[str, Any]]:
    """All of an arm's receipts, keyed by shard label (``main-00`` … ``main-15``, ``sentinel``).
    Returns the failures and the arm's census: expected store count (distinct chunk ids
    across every receipt where they are carried, else the sum of ``n_chunks``), documents,
    image commit."""
    bad: list[str] = []
    main = {k: v for k, v in receipts.items() if k != sentinel_label}
    if len(main) != n_main_shards:
        bad.append(f"[receipts] {arm}: {len(main)} main-shard receipts, expected {n_main_shards}")
    if sentinel_label not in receipts:
        bad.append(f"[receipts] {arm}: no sentinel receipt")
    for label, r in receipts.items():
        bad += check_receipt(r, arm, label, expect_build=expect_build, expect_commit=expect_commit)
    n_docs_main = sum(int(r.get("n_docs") or 0) for r in main.values())
    if n_docs_main != n_main_docs:
        bad.append(f"[docs] {arm}: {n_docs_main:,} documents in the main shards, expected {n_main_docs:,}")
    sent = receipts.get(sentinel_label) or {}
    if sent and sent.get("n_docs") != n_sentinel_docs:
        bad.append(f"[docs] {arm}: sentinel n_docs {sent.get('n_docs')}, expected {n_sentinel_docs}")
    if expect_sentinel_chunks is None:
        bad.append(f"[sentinel_chunks] {arm}: no pinned sentinel chunk count (absent)")
    elif sent and sent.get("n_chunks") != expect_sentinel_chunks:
        bad.append(f"[sentinel_chunks] {arm}: sentinel n_chunks {sent.get('n_chunks')}, expected {expect_sentinel_chunks} (dry run)")
    commits = {(r.get("provenance") or {}).get("image_commit") for r in receipts.values()}
    if len(commits) != 1:
        bad.append(f"[image_commit] {arm}: {len(commits)} distinct image_commit values across receipts")
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
              "image_build": sorted({str((r.get("provenance") or {}).get("image_build")) for r in receipts.values()}),
              "segmenter_versions": sorted({str((r.get("provenance") or {}).get("segmenter_version"))
                                            for r in receipts.values()})}
    return bad, census


def check_same_commit(census_s: dict[str, Any], census_r: dict[str, Any]) -> list[str]:
    """§2/§6: every S and S-regex receipt carries the same ``image_commit``."""
    a, b = census_s.get("image_commit"), census_r.get("image_commit")
    if not a or not b:
        return ["[image_commit] image_commit missing on one arm"]
    if a != b:
        return [f"[image_commit] image_commit differs between S ({a[:12]}) and S-regex ({b[:12]})"]
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
        w = f"{arm}/{label}"
        if text is None:
            bad.append(f"[stderr_missing] {w}: worker stderr not captured")
            counts[label] = None
            sizes[label] = None
            continue
        n = punkt_lines(text)
        size = len(text.encode("utf-8"))
        counts[label] = n
        sizes[label] = size
        if tail_limited and size >= GOWE_TAIL_BYTES:
            bad.append(f"[stderr_tail] {w}: stderr tail at the 256 KB limit ({size} bytes): the once-per-process "
                       "warning may have scrolled off; the staged-out file is required")
            continue
        if arm == "S" and n != 0:
            bad.append(f"[stderr_lines] {w}: {n} '{PUNKT_WARNING}' line(s) in worker stderr (expected 0)")
        if arm == "S-regex" and n < 1:
            bad.append(f"[stderr_lines] {w}: no '{PUNKT_WARNING}' line in worker stderr (expected ≥ 1)")
    return bad, {"warning_lines": counts, "bytes": sizes, "total_warning_lines": sum(c or 0 for c in counts.values())}


# ------------------------------------------------------------------- store counts


def check_store_counts(arm: str, expected: int | None, qdrant: int | None, es: int | None) -> list[str]:
    """Qdrant = Elasticsearch = the receipts' expected count, exactly."""
    if expected is None or qdrant is None or es is None:
        return [f"[counts] {arm}: store counts incomplete (expected {expected}, qdrant {qdrant}, es {es})"]
    bad = []
    if qdrant != es:
        bad.append(f"[counts] {arm}: qdrant {qdrant:,} != elasticsearch {es:,}")
    if qdrant != expected:
        bad.append(f"[counts] {arm}: qdrant {qdrant:,} != receipts {expected:,}")
    if es != expected:
        bad.append(f"[counts] {arm}: elasticsearch {es:,} != receipts {expected:,}")
    return bad


def check_reused_census(arm: str, frozen: dict[str, Any] | None, live: dict[str, Any] | None) -> list[str]:
    """Reused arms: the live census equals the census recorded at the freeze (qdrant and es)."""
    if not frozen or frozen.get("qdrant") is None or frozen.get("es") is None:
        return [f"[census] {arm}: no frozen census"]
    if not live or live.get("qdrant") is None or live.get("es") is None:
        return [f"[census] {arm}: no live census"]
    bad = []
    for store in ("qdrant", "es"):
        if int(live[store]) != int(frozen[store]):
            bad.append(f"[census] {arm}: live {store} {int(live[store]):,} != frozen {int(frozen[store]):,}")
    if int(frozen["qdrant"]) != int(frozen["es"]):
        bad.append(f"[census] {arm}: frozen census qdrant {frozen['qdrant']} != es {frozen['es']}")
    return bad


# ------------------------------------------------------------------- inputs, sets, retrieval


def check_shard_hashes(pinned: dict[str, str], actual: dict[str, str], n_expected: int = N_SHARDS) -> list[str]:
    """Same input: exactly ``n_expected`` pinned shard files, every one hashed and equal; no
    unpinned file in the run."""
    bad = []
    if len(pinned) != n_expected:
        bad.append(f"[shards] {len(pinned)} pinned shard files, expected {n_expected}")
    for label, want in pinned.items():
        got = actual.get(label)
        if got is None:
            bad.append(f"[shards] shard {label}: not hashed")
        elif got != want:
            bad.append(f"[shards] shard {label}: sha256 {got[:12]} != pinned {want[:12]}")
    for label in sorted(set(actual) - set(pinned)):
        bad.append(f"[shards] shard {label}: in the run but not pinned")
    return bad


def check_sets(decision_ids: Iterable[str], sensitivity_ids: Iterable[str], frozen: dict[str, Any]) -> list[str]:
    """Decision set: n and the id-list sha256 equal the freeze (three-arm output)."""
    bad = []
    for name, ids in (("decision", decision_ids), ("sensitivity", sensitivity_ids)):
        ids = list(ids)
        want = frozen.get(name) or {}
        if not want.get("n") or not want.get("sha256"):
            bad.append(f"[sets] {name} set: no frozen n / sha256 (absent)")
            continue
        if len(ids) != want.get("n"):
            bad.append(f"[sets] {name} set: n = {len(ids)}, frozen {want.get('n')}")
        got = ids_sha256(ids)
        if got != want.get("sha256"):
            bad.append(f"[sets] {name} set: ids sha256 {got[:12]} != frozen {str(want.get('sha256'))[:12]}")
    return bad


def check_retrieval(score: dict[str, Any], arms: Iterable[str], n_questions: int) -> tuple[list[tuple[str | None, str]], dict[str, Any]]:
    """Per setting, from ``score.json``: no missing row per arm, no refusal, no request
    mismatch or unverified request, one well-formed server version label per arm. Returns
    ``(arm or None, failure)`` pairs so the caller can void by the arm named."""
    bad: list[tuple[str | None, str]] = []
    arms = list(arms)
    missing = score.get("missing_by_arm") or {}
    for a in arms:
        if missing.get(a):
            bad.append((a, f"[rows] {a}: {missing[a]} question(s) without an ok retrieval row"))
    if score.get("n_questions") != n_questions:
        bad.append((None, f"[rows] {score.get('n_questions')} questions scored, expected {n_questions}"))
    for r in score.get("refusals") or []:
        head = str(r).split(":", 1)[0].strip()
        bad.append((head if head in arms else None, f"[score_refusal] {r}"))
    for key in ("request_mismatch_by_arm", "request_unverified_by_arm"):
        for a, n in (score.get(key) or {}).items():
            if n:
                what = key.replace("_by_arm", "").replace("_", " ")
                bad.append((a if a in arms else None, f"[request] {a}: {n} row(s) {what}"))
    versions = score.get("server_versions")
    if not isinstance(versions, dict) or not versions:
        bad.append((None, "[version] score.json records no server_versions (absent)"))
        versions = {}
    for a in arms:
        vs = versions.get(a)
        if not vs:
            bad.append((a, f"[version] {a}: no server version recorded on its rows"))
        elif len(vs) != 1:
            bad.append((a, f"[version] {a}: {len(vs)} server versions in its rows"))
        elif not VERSION_LABEL.match(str(vs[0])):
            bad.append((a, f"[version] {a}: server version label {vs[0]!r} is not version+git_sha"))
    return bad, {"server_versions": versions, "missing_by_arm": missing}


def check_one_version(versions_by_setting: dict[str, dict[str, list[str]]]) -> tuple[list[str], str | None]:
    """One well-formed ``version+git_sha`` label across every arm of every setting."""
    labels = {v for per in versions_by_setting.values() for vs in per.values() for v in vs}
    if not labels:
        return ["[version] no server version recorded on any row"], None
    if len(labels) != 1:
        return [f"[version] {len(labels)} server versions across the run: " + ", ".join(sorted(labels))], None
    label = next(iter(labels))
    if not VERSION_LABEL.match(str(label)):
        return [f"[version] server version label {label!r} is not version+git_sha"], None
    return [], label


def check_request_fields(api: dict[str, Any] | None, frozen: dict[str, Any] | None) -> list[str]:
    """The request the config sends (``api.request_extra`` + ``api.top_k``, what every
    retrieval row records) equals the frozen Addendum C request of the setting."""
    if not api:
        return ["[request] no config api block to check (absent)"]
    if not frozen:
        return ["[request] no frozen request fields for this setting (absent)"]
    sent = dict(api.get("request_extra") or {})
    sent["top_k"] = api.get("top_k")
    bad = []
    for k in ("rerank", "use_graph", "retrieval_mode", "top_k"):
        if k not in sent or sent[k] is None:
            bad.append(f"[request] request field {k} not set in the config")
        elif k not in frozen:
            bad.append(f"[request] request field {k} not frozen")
        elif sent[k] != frozen.get(k):
            bad.append(f"[request] request field {k} = {sent[k]!r}, frozen {frozen.get(k)!r}")
    return bad


# ------------------------------------------------------------------- decision.json integrity


def check_decision_integrity(dec: dict[str, Any], cost_order: str, n_decision: int,
                             margins: dict[str, float] | None = None) -> list[str]:
    """``decision.json`` was produced by the frozen rule: nothing forced, the paired ladder,
    the Bonferroni guard, the frozen margins, the decision set and n, and a ladder order that
    respects the frozen cost order."""
    margins = margins or {"M_ni": 2.0, "M_mid": 2.0}
    bad = []
    if dec.get("forced_unchecked"):
        bad.append(f"[decision_json] decide ran with --force-unchecked: {'; '.join(map(str, dec['forced_unchecked']))[:120]}")
    if dec.get("tie_break") != "paired_ladder":
        bad.append(f"[decision_json] tie_break {dec.get('tie_break')!r}, expected 'paired_ladder'")
    if dec.get("stratum_correction") != "bonferroni":
        bad.append(f"[decision_json] stratum_correction {dec.get('stratum_correction')!r}, expected 'bonferroni'")
    m = dec.get("margins") or {}
    for k, v in margins.items():
        if m.get(k) is None or abs(float(m[k]) - v) > 1e-9:
            bad.append(f"[decision_json] margins.{k} = {m.get(k)!r}, frozen {v}")
    if dec.get("margins_match_noise") is False:
        bad.append("[decision_json] margins_match_noise is false")
    inputs = dec.get("inputs") or {}
    if inputs.get("set") != "clean_excl_noise":
        bad.append(f"[decision_json] decided on set {inputs.get('set')!r}, expected 'clean_excl_noise'")
    if inputs.get("n") != n_decision:
        bad.append(f"[decision_json] decided on n = {inputs.get('n')}, the three-arm decision set has {n_decision}")
    order = (dec.get("ladder") or {}).get("order") or []
    if order and order[0] != "ft512o64":
        bad.append(f"[decision_json] ladder order starts with {order[0]!r}, not the baseline")
    if "S" in order and "ft512o64s" in order:
        s_first = order.index("S") < order.index("ft512o64s")
        if (cost_order == "B") != s_first:
            bad.append(f"[decision_json] ladder order {order} does not follow cost order {cost_order}")
    return bad


# ------------------------------------------------------------------- embedding drift


def check_reembed(cosines: list[dict[str, Any]], threshold: float, n_expected: int,
                  ids_sha256_pinned: str | None) -> tuple[list[str], dict[str, Any]]:
    """Every re-embedded chunk's cosine to its stored vector ≥ threshold, over exactly the
    pinned sample (its id-list sha256)."""
    bad = []
    vals = [float(c["cosine"]) for c in cosines if c.get("cosine") is not None]
    if len(vals) != n_expected:
        bad.append(f"[reembed] {len(vals)} cosines, expected {n_expected}")
    if not ids_sha256_pinned:
        bad.append("[reembed] no pinned sample ids sha256 (absent)")
    else:
        got = ids_sha256(str(c.get("chunk_id")) for c in cosines)
        if got != ids_sha256_pinned:
            bad.append(f"[reembed] cosines cover ids sha256 {got[:12]}, pinned sample {ids_sha256_pinned[:12]}")
    below = [c for c in cosines if c.get("cosine") is not None and float(c["cosine"]) < threshold]
    if below:
        bad.append(f"[reembed] {len(below)} chunk(s) below cosine {threshold} (min {min(vals):.6f})")
    desc = {"n": len(vals), "min": min(vals) if vals else None, "threshold": threshold, "n_below": len(below)}
    return bad, desc


def check_model_ids(freeze: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """The embedding and reranker model ids recorded at the run equal those at the freeze."""
    bad = []
    for k in ("embedding_model", "reranker_model"):
        if not freeze.get(k) or not run.get(k):
            bad.append(f"[model_ids] {k}: not recorded at {'the freeze' if not freeze.get(k) else 'the run'}")
        elif freeze[k] != run[k]:
            bad.append(f"[model_ids] {k}: run {run[k]!r} != freeze {freeze[k]!r}")
    return bad


def check_provenance(prov: dict[str, Any] | None) -> list[str]:
    """§6: the harness ran from a clean tree (``experiment_provenance``: ``dirty`` false,
    ``untracked`` 0, a commit known)."""
    if not prov:
        return ["[provenance] no harness provenance recorded (absent)"]
    bad = []
    if prov.get("dirty") is not False:
        bad.append(f"[provenance] harness tree dirty = {prov.get('dirty')!r} (clean tree required)")
    if prov.get("untracked") is None or int(prov.get("untracked") or 0) != 0:
        bad.append(f"[provenance] harness tree untracked = {prov.get('untracked')!r} (0 required)")
    if not prov.get("commit"):
        bad.append("[provenance] harness commit unknown")
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
