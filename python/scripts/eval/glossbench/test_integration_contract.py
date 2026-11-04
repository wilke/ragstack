"""The cross-PR contract of glossary benchmark round 2, pinned on the consumer side.

Three PRs were built in parallel and define the interfaces independently: the
glossary arms (``glossary_file.py rederive``, ``backfill --shuffle-rendered``,
``wording_proxy.py``), the drafter (``glossbench_r2_draft.py``) and this runner.
Their code cannot be imported here before the merge, so every fixture below is
**copied byte-exactly from the sibling PRs' own tests and writers** (named in each
comment) rather than built with their functions. If a sibling changes its format,
the copied literal here is what must change too — that is the point of the file.

Sources of the literals:

* the drafter's accepted record: ``glossbench_r2_draft.py`` ``Run.summary``
  (``json.dumps({record_id, set, paper, doc_id, source_chunk_id, stratum,
  **draft, gold_chunks, flags}, sort_keys=True)``), the record id format
  ``f"{set_}-{sha256(chunk_id)[:12]}-a{attempt}"`` and the fake drafter's answer in
  ``tests/eval/glossbench_r2_support.py`` (``fake_draft``) /
  ``test_glossbench_r2_draft.py`` (the fields its ``accepted-O.jsonl`` loop asserts);
* the arms' stamps: ``tests/unit/test_glossary_round2_arms.py`` — ``LLM_V``,
  ``f"{LLM_V}+merge=llm-only"`` / ``"none+merge=llm-only"`` for the pure file,
  ``stamp_version(EXTRACTOR_VERSION) + f"+shuf/1:261101|shuf:{perm[:12]}"`` and
  ``f"{LLM_V}@file/1+shuf/1:s:"`` for the shuffled index, the pinned permutation
  of ``test_pinned_permutation`` (seed ``pin`` → ``1aef9aca491b…``), and
  ``ShuffleResult.record()``'s keys from ``glossary_shuffle.py``;
* the backfill's apply result: ``backfill_symbol_glossary.run`` — ``{"index",
  "dry_run", **totals}`` (``docs, chunks, chunks_with_glossary, chunks_to_update,
  chunks_written, chunks_current, …``) plus ``glossary_file`` (``file_provenance``:
  ``path, sha256, schema, producer, …``) and ``shuffle`` (the record above);
* the proxy's results: ``wording_proxy.py`` ``run``/``score`` — ``{"schema":
  "wording-proxy/1", "per_record": {rid: {arm: {"pass", "match", …}}}, "totals", …}``
  and its ``read_records`` requirements (``record_id, gold_chunks, binding_terms,
  quantity``).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import found10 as f10  # noqa: E402

# ---- #861 literals (test_glossary_round2_arms.py / glossary_shuffle.py)
LLM_V = "llm/2:Model-X:0123456789ab:w80000x4:t4096:k0:r0"
EXTRACTOR_VERSION = "rules/3"
PURE_STAMP = f"{LLM_V}+merge=llm-only@file/1"             # test_every_mode_stamps_differently…: endswith
BARE_PURE_STAMP = "none+merge=llm-only@file/1"             # test_llm_only…: rows["bare"] == "none+merge=llm-only"
MERGED_STAMP = f"{LLM_V}+merge=merged@file/1"
RULES_ONLY_STAMP = f"{EXTRACTOR_VERSION}+merge=rules-only@file/1"
NONE_STAMP = "none+merge=none@file/1"
RULES_STAMP = f"{EXTRACTOR_VERSION}@file/1"                # stamp_version(EXTRACTOR_VERSION)
PIN_PERM = "1aef9aca491b3025740ecf294964a0a6c87132757234c74ba600884769da7ba3"  # test_pinned_permutation
SHUF_SEED = "261101|shuf"
SHUF_SUFFIX = f"+shuf/1:{SHUF_SEED}:{PIN_PERM[:12]}"       # ShuffleResult.stamp_suffix()
SHUFFLED_RULES_STAMP = RULES_STAMP + SHUF_SUFFIX           # test_backfill_shuffle_permutes…: `want`
SHUFFLED_LLM_PREFIX = f"{LLM_V}@file/1+shuf/1:s:"          # test_backfill_shuffle_keeps_the_llm_replace_guard
SHUFFLE_RECORD = {                                         # ShuffleResult.record()
    "scheme": "shuf/1", "seed": SHUF_SEED, "populated": 90, "empty": 10, "moved": 90, "fixed_points": 0,
    "fixed_points_before_repair": 2,
    "fixed_point_rule": ("a chunk is a fixed point when the string it receives EQUALS its own (value, not "
                         "position); repaired by seeded swaps; the residual is unavoidable for the greedy repair"),
    "permutation_sha256": PIN_PERM, "multiset_sha256": "7" * 64, "stamp_suffix": SHUF_SUFFIX,
}

# ---- #863 literals (glossbench_r2_draft.py Run.summary; glossbench_r2_support.py fake_draft)
CHUNK_ID = "d1-c2"
RECORD_ID = f"O-{hashlib.sha256(CHUNK_ID.encode('utf-8')).hexdigest()[:12]}-a1"
ACCEPTED_RECORD = json.dumps({
    "record_id": RECORD_ID, "set": "O", "paper": "PMC1", "doc_id": "d1", "source_chunk_id": CHUNK_ID,
    "stratum": "S1",
    # **r["draft"] — fake_draft's answer for a Qb passage
    "question": "How fast did the folded paper boats drift in the calm case?", "quote": "measured Qb = 0.15 m/s",
    "quantity": "boat drift speed", "value": "0.15 m/s", "binding_terms": ["boat drift speed"], "symbol": "Qb",
    "gold_chunks": [CHUNK_ID], "flags": [],
}, sort_keys=True, ensure_ascii=False)

# ---- backfill apply result (backfill_symbol_glossary.run + file_provenance), a pure file, shuffled
GSHA = "a" * 64
APPLY_SHUFFLED = {
    "index": "ragstack_lib_bench_synthetic", "dry_run": False, "chunks": 100, "chunks_current": 0,
    "chunks_to_update": 100, "chunks_with_glossary": 90, "chunks_written": 100, "docs": 6, "docs_llm": 6,
    "reassembly_gaps": 0, "reassembly_length_mismatches": 0,
    "glossary_file": {"path": "/private/pure.jsonl", "sha256": GSHA, "schema": "ragstack-glossary-file/1",
                      "producer": "scripts/glossary_file.py rederive --merge llm-only (source sha256 …)",
                      "created": "2026-11-01T00:00:00Z", "counts": {}, "degraded_reasons": {},
                      "glossary_versions": [f"{LLM_V}+merge=llm-only", "none+merge=llm-only"],
                      "file_stamp": "file/1", "mask": 1, "documents": {"verified": 6, "unverified": 0}},
    "shuffle": SHUFFLE_RECORD,
}

# ---- wording proxy results (wording_proxy.py run/score)
PROXY_RESULTS = {
    "schema": "wording-proxy/1",
    "rules": {"pass": "…"}, "arms": {"qwen36p": {"source": "glossary_file", "chunks": 100}},
    "inputs": {"records": {"path": "/private/accepted-O.jsonl", "sha256": "b" * 64, "n": 1}},
    "per_record": {RECORD_ID: {
        "qwen36p": {"pass": True, "match": {"chunk_id": CHUNK_ID, "symbol": "Qb", "term": "boat drift speed"},
                    "gold_fields_nonempty": 1, "malformed_lines": 0, "empty_terms": 0},
        "rules": {"pass": False, "match": None, "gold_fields_nonempty": 1, "malformed_lines": 0, "empty_terms": 0},
        "qwen36p-shuf": {"pass": False, "match": None, "gold_fields_nonempty": 1, "malformed_lines": 0,
                         "empty_terms": 0}}},
    "totals": {"qwen36p": {"n": 1, "pass": 1, "rate": 1.0}},
    "by_quantity": {}, "by_stratum": {}, "provenance": {}, "citable": False,
}


def _cfg(**arms: dict[str, Any]) -> dict[str, Any]:
    return f10.normalise_config({
        "api": {"base_url": "http://api.invalid"},
        "corpora": {"O": {"collection": "c", "index": "ragstack_lib_bench_synthetic", "expected_chunks": 100}},
        "arms": arms})


PURE_ARM = {"stamp_prefixes": ["llm/2:Model-X:"], "stamps": ["none"], "merge": "llm-only", "shuffle_seed": None,
            "glossary_file_sha256": {"O": GSHA}}


def test_the_drafters_accepted_record_is_the_runners_question(tmp_path: Path) -> None:
    q = tmp_path / "accepted-O.jsonl"
    q.write_text(ACCEPTED_RECORD + "\n", encoding="utf-8")
    recs = f10.load_questions([q])
    assert len(recs) == 1
    r = recs[0]
    # what the runner reads
    assert r["record_id"] == RECORD_ID and r["corpus"] == "O" and r["paper"] == "PMC1" and r["stratum"] == "S1"
    assert r["gold_chunks"] == [CHUNK_ID] and r["question"].startswith("How fast")
    # what the proxy reads from the SAME file is carried untouched (wording_proxy.read_records)
    assert isinstance(r["binding_terms"], list) and r["binding_terms"] and isinstance(r["quantity"], str)
    # the runner never required a field the drafter does not write
    written = set(json.loads(ACCEPTED_RECORD))
    assert set(f10.RECORD_FIELDS) <= written and "set" in written
    assert "gold_chunk_ids" not in written and "corpus" not in written


def test_the_proxys_results_file_is_the_runners_proxy_outcome(tmp_path: Path) -> None:
    px = tmp_path / "proxy.json"
    px.write_text(json.dumps(PROXY_RESULTS), encoding="utf-8")
    (tmp_path / "out").mkdir()
    rec = {**json.loads(ACCEPTED_RECORD), "corpus": "O"}
    per, _, _ = f10.collect(tmp_path / "out", [rec], px)
    assert per[RECORD_ID]["qwen36p"]["proxy"] == 1
    assert per[RECORD_ID]["rules"]["proxy"] == 0 and per[RECORD_ID]["qwen36p-shuf"]["proxy"] == 0


@pytest.mark.parametrize("stamp,arm,ok", [
    (PURE_STAMP, PURE_ARM, True),
    (BARE_PURE_STAMP, PURE_ARM, True),
    (f"{LLM_V}@file/1", PURE_ARM, False),                 # the round-1 file loaded as is: not pure
    (MERGED_STAMP, PURE_ARM, False),
    (NONE_STAMP, PURE_ARM, False),
    (PURE_STAMP + SHUF_SUFFIX, PURE_ARM, False),           # the shuffled index is not the true arm
    (PURE_STAMP + SHUF_SUFFIX, {**PURE_ARM, "shuffle_seed": SHUF_SEED}, True),
    (BARE_PURE_STAMP + SHUF_SUFFIX, {**PURE_ARM, "shuffle_seed": SHUF_SEED}, True),
    (PURE_STAMP, {**PURE_ARM, "shuffle_seed": SHUF_SEED}, False),
    (RULES_STAMP, {"stamps": ["rules/3"], "merge": None, "shuffle_seed": None}, True),
    (RULES_ONLY_STAMP, {"stamps": ["rules/3"], "merge": None, "shuffle_seed": None}, False),
    (RULES_ONLY_STAMP, {"stamps": ["rules/3"], "merge": "rules-only", "shuffle_seed": None}, True),
    (SHUFFLED_RULES_STAMP, {"stamps": ["rules/3"], "merge": None, "shuffle_seed": SHUF_SEED}, True),
    (SHUFFLED_RULES_STAMP, {"stamps": ["rules/3"], "merge": None, "shuffle_seed": None}, False),
    (SHUFFLED_LLM_PREFIX + "0123456789ab", {"stamp_prefixes": ["llm/2:Model-X:"], "shuffle_seed": "s"}, True),
    (NONE_STAMP, {"stamps": ["none"], "merge": "none", "shuffle_seed": None}, True),
    ("<none>", PURE_ARM, False),
])
def test_the_arms_stamps_are_read_structurally(stamp: str, arm: dict[str, Any], ok: bool) -> None:
    assert f10.stamp_allowed(stamp, arm) is ok


def test_the_backfills_apply_output_is_the_swap_records_apply() -> None:
    """swap.sh embeds the backfill's apply JSON as is; the runner reads its
    ``shuffle``, ``glossary_file.sha256``, ``chunks_written``/``chunks_to_update``."""
    cfg = _cfg(**{"qwen36p-shuf": {**PURE_ARM, "shuffle_seed": SHUF_SEED}, "qwen36p": PURE_ARM})
    record = {"schema": f10.SWAP_SCHEMA, "arm": "qwen36p-shuf", "index": "ragstack_lib_bench_synthetic",
              "glossary_file": {"path": "/private/pure.jsonl", "sha256": GSHA}, "shuffle_seed": SHUF_SEED,
              "backfill_extra_args": ["--shuffle-rendered", SHUF_SEED],
              "utc_start": "2026-11-01T00:00:00Z", "utc_end": "2026-11-01T00:02:00Z",
              "census_before": {"total": 100, "stamps": {PURE_STAMP: 90, BARE_PURE_STAMP: 10}},
              "dryrun": {**APPLY_SHUFFLED, "dry_run": True, "chunks_written": 0},
              "apply": APPLY_SHUFFLED,
              "census_after": {"total": 100, "stamps": {PURE_STAMP + SHUF_SUFFIX: 90,
                                                        BARE_PURE_STAMP + SHUF_SUFFIX: 10}}}
    v = f10.verify_swap_record(record, cfg, "O", "qwen36p-shuf")
    assert v["total"] == 100 and v["shuffle_suffix"] == SHUF_SUFFIX
    # the true arm's record is the same apply without `shuffle` and the unshuffled stamps
    true = {**record, "arm": "qwen36p", "shuffle_seed": None, "backfill_extra_args": [],
            "apply": {k: v for k, v in APPLY_SHUFFLED.items() if k != "shuffle"},
            "census_after": record["census_before"]}
    assert f10.verify_swap_record(true, cfg, "O", "qwen36p")["shuffle_suffix"] is None
    # cross-wired: the shuffled apply under the true arm's name, or the true census under the shuffled arm's
    with pytest.raises(f10.Refused):
        f10.verify_swap_record({**true, "apply": APPLY_SHUFFLED}, cfg, "O", "qwen36p")
    with pytest.raises(f10.Refused):
        f10.verify_swap_record({**record, "census_after": record["census_before"]}, cfg, "O", "qwen36p-shuf")
