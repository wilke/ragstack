"""Hermetic tests for judge.py (gateway terms, stops, resume) and swap.sh (guards, record).
No subprocess judge, no network: the gateway's urlopen is intercepted; swap.sh runs with a
fake ``curl`` and a fake backfill on PATH."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import found10 as f10  # noqa: E402
import judge as jd  # noqa: E402

HERE = Path(__file__).resolve().parent
SEED = "261101|shuf"
PERM = "1aef9aca491b3025740ecf294964a0a6c87132757234c74ba600884769da7ba3"
SUFFIX = f"+shuf/1:{SEED}:{PERM[:12]}"


# ------------------------------------------------------------- rubric / parsing
def test_rubric_v3_is_frozen() -> None:
    # round 1's rubric-v3 system prompt, byte for byte
    assert jd.SYSTEM_SHA256 == "3739db896815df1d35d4c1f5a55d2d597612418eefed898aa322aaa32e89ade6"
    assert jd.LABELS == ("correct", "partial", "wrong", "not-a-symbol") and jd.RUBRIC == "v3"


def test_parse_labels() -> None:
    items = [{"id": "i01"}, {"id": "i02"}]
    ok = jd.parse_labels('```json\n{"labels":[{"id":"i01","label":"partial","reason":"r"},'
                         '{"id":"i02","label":"wrong","reason":""}]}\n```', items)
    assert ok["valid"] and [x["label"] for x in ok["labels"]] == ["partial", "wrong"]
    assert not jd.parse_labels('{"labels":[{"id":"i01","label":"correct"}]}', items)["valid"]  # missing i02
    assert not jd.parse_labels('{"labels":[{"id":"i01","label":"maybe"},{"id":"i02","label":"wrong"}]}',
                               items)["valid"]
    assert not jd.parse_labels("not json", items)["valid"]


# ------------------------------------------------------------- gateway terms
class _Resp(io.BytesIO):
    status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


def test_gateway_body_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    sent: list[tuple[dict[str, str], dict[str, Any], str]] = []

    def fake(req: Any, timeout: float | None = None) -> _Resp:
        sent.append((dict(req.header_items()), json.loads(req.data), req.full_url))
        return _Resp(json.dumps({"model": "Echoed", "choices": [{"message": {"content": "{}"},
                     "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    for m in ("GPT-5.5", "Gemini 2.5 Pro", "GPT-5.6 Sol"):
        r = jd.call_gateway("x", m, 1500, "http://gw.invalid/v1/")
        hdr, body, url = sent[-1]
        assert url == "http://gw.invalid/v1/chat/completions"
        assert body["fallback_model"] == "" and body["max_tokens"] == 1500
        assert ("temperature" in body) == (m not in jd.GATEWAY_NO_TEMPERATURE)
        assert not any(k.lower() == "authorization" for k in hdr)
        assert r["served_model"] == "Echoed" and r["context_tokens"] == 3
    with pytest.raises(ValueError):
        jd.gateway_body("x", "M", 0)


def _args(tmp: Path, **over: Any) -> argparse.Namespace:
    a = {"judge": "gateway", "out": str(tmp / "labels.jsonl"), "pause_file": None, "inflight": 99,
         "max_tokens": 100, "effort": None, "budget_input_tokens": 5_000_000, "budget_output_tokens": 750_000,
         "codex_overhead_file": None}
    a.update(over)
    return argparse.Namespace(**a)


ROWS = [{"key": f"O-{k:02d}", "corpus": "O", "judged_text": "t", "items": [{"id": "i01", "symbol": "x",
                                                                            "definition": "d"}]}
        for k in range(1, 8)]
GOOD = json.dumps({"labels": [{"id": "i01", "label": "correct", "reason": ""}]})


def _fake_call(answers: dict[str, list[str]], log: list[str]) -> Any:
    def call(prompt: str) -> dict[str, Any]:
        key = log_keys(prompt)[0]
        log.append(key)
        text = answers.get(key, [GOOD]).pop(0) if answers.get(key) else GOOD
        return {"text": text, "served_model": "Served-X", "context_tokens": 1000, "output_tokens": 50}
    return call


def log_keys(prompt: str) -> list[str]:
    return [r["key"] for r in ROWS if f"marker {r['key']} " in prompt]


@pytest.fixture(autouse=True)
def _marked_rows() -> None:
    for r in ROWS:
        r["judged_text"] = f"marker {r['key']} text"


def test_judge_run_empty_retry_then_skip_and_resume(tmp_path: Path) -> None:
    log: list[str] = []
    answers = {"O-02": ["", GOOD], "O-03": ["", ""]}  # O-02 recovers on the retry, O-03 is skipped
    run = jd.JudgeRun(_args(tmp_path), _fake_call(answers, log), "M", ROWS)
    assert run.inflight == jd.MAX_INFLIGHT  # hard cap
    assert run.execute() == jd.EXIT_OK
    rows = [json.loads(x) for x in Path(tmp_path / "labels.jsonl").read_text().splitlines()]
    by = {r["key"]: r for r in rows}
    assert by["O-02"]["ok"] and by["O-02"]["empties"] == 1
    assert not by["O-03"]["ok"] and by["O-03"]["skipped"] and by["O-03"]["attempts"] == 2
    tot = json.loads((tmp_path / "labels.totals.json").read_text())
    assert tot["cumulative"]["calls"] == 9 and tot["cumulative"]["empty"] == 3
    assert tot["served_models"] == {"Served-X": 9}
    # resume: only the skipped document runs again
    log2: list[str] = []
    assert jd.JudgeRun(_args(tmp_path), _fake_call({}, log2), "M", ROWS).execute() == jd.EXIT_OK
    assert log2 == ["O-03"]
    tot = json.loads((tmp_path / "labels.totals.json").read_text())
    assert tot["cumulative"]["calls"] == 10  # carried across the resume


def test_judge_pause_file_and_budget(tmp_path: Path) -> None:
    pause = tmp_path / "PAUSE"
    pause.write_text("")
    log: list[str] = []
    assert jd.JudgeRun(_args(tmp_path, pause_file=str(pause)), _fake_call({}, log), "M", ROWS).execute() \
        == jd.EXIT_PAUSED
    assert log == []
    pause.unlink()
    # budget: 1000 input tokens per call, budget 2500 -> stops after the third call
    log = []
    run = jd.JudgeRun(_args(tmp_path, budget_input_tokens=2500, inflight=1), _fake_call({}, log), "M", ROWS)
    assert run.execute() == jd.EXIT_PAUSED
    assert len(log) == 3 and "token budget" in run.reason[0]


def test_judge_failure_cluster_pauses(tmp_path: Path) -> None:
    log: list[str] = []
    answers = {r["key"]: ["", ""] for r in ROWS}
    run = jd.JudgeRun(_args(tmp_path, inflight=1), _fake_call(answers, log), "M", ROWS)
    assert run.execute() == jd.EXIT_PAUSED
    assert "in a row" in run.reason[0] and len(set(log)) == jd.FAIL_CONSECUTIVE


def test_gateway_run_needs_notice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = tmp_path / "s.jsonl"
    s.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"gateway": {"base_url": "http://127.0.0.1:1/v1"}}))
    rc = jd.main(["run", "--judge", "gateway", "--model", "M", "--sample", str(s), "--out", str(tmp_path / "o.jsonl"),
                  "--private-config", str(cfg)])
    assert rc == jd.EXIT_FAIL and "notice-acknowledged" in capsys.readouterr().err
    assert jd.main(["preflight", "--judge", "gateway", "--model", "M", "--sample", str(s)]) == 0
    n = json.loads(capsys.readouterr().out.split("NOTICE ", 1)[1])
    assert n["documents"] == 7 and n["calls_max_with_retry"] == 14 and n["concurrency"] == 3
    assert n["max_tokens_per_call"] == 8000 and n["est_input_tokens"] > 0


# ------------------------------------------------------------- swap.sh
def _exe(p: Path, body: str) -> None:
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _swap(tmp: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", **(env or {})}
    return subprocess.run(["bash", str(HERE / "swap.sh"), *args], capture_output=True, text=True, env=env)


@pytest.fixture
def fakes(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    # a fake ES census (curl) and a fake backfill that records its argv
    # the census shows the shuffled control's stamps; the fake backfill answers as the real one does
    # (its `shuffle` record names the seed it was given) unless FAKE_SEED overrides the seed it reports
    _exe(tmp_path / "bin/curl", 'echo \'{"hits":{"total":{"value":100}},"aggregations":{"v":{"buckets":'
                                f'[{{"key":"llm/2:M:x+merge=llm-only@file/1{SUFFIX}","doc_count":100}}]}}}}}}\'\n')
    _exe(tmp_path / "bin/fakebackfill", f'echo "$@" >> {tmp_path}/argv.log\n'
         'args=" $* "; seed=; gf=; while [ $# -gt 0 ]; do [ "$1" = --shuffle-rendered ] && seed=$2; '
         '[ "$1" = --glossary-file ] && gf=$2; shift; done\n'
         'sha=$(sha256sum "$gf" | cut -d" " -f1)\n'
         'seed=${FAKE_SEED-$seed}\n'
         'shuf=; [ -n "$seed" ] && shuf=", \\"shuffle\\": {\\"scheme\\": \\"shuf/1\\", \\"seed\\": \\"$seed\\", '
         f'\\"permutation_sha256\\": \\"{PERM}\\", \\"stamp_suffix\\": \\"+shuf/1:$seed:{PERM[:12]}\\"}}"\n'
         'if [[ "$args" == *" --apply "* ]]; then echo "{\\"dry_run\\": false, \\"chunks\\": 100, \\"chunks_to_update\\": 100, '
         '\\"chunks_written\\": ${FAKE_WRITTEN-100}, \\"glossary_file\\": {\\"sha256\\": \\"${FAKE_SHA-$sha}\\"}$shuf}"; '
         'else echo "{\\"dry_run\\": true, \\"chunks\\": 100, \\"chunks_to_update\\": 100$shuf}"; fi\n')
    (tmp_path / "g.jsonl").write_text('{"x": 1}\n')
    return tmp_path


def test_swap_refuses_a_non_bench_index(fakes: Path) -> None:
    r = _swap(fakes, "--arm", "a", "--glossary-file", str(fakes / "g.jsonl"), "--index", "ragstack_lib_prod_x",
              "--es-url", "http://es.invalid", "--backfill", "fakebackfill", "--record-dir", str(fakes / "rec"))
    assert r.returncode == 9 and "REFUSE" in r.stderr
    assert not (fakes / "argv.log").exists()


def test_swap_requires_every_argument(fakes: Path) -> None:
    r = _swap(fakes, "--arm", "a", "--index", "ragstack_lib_bench_x")
    assert r.returncode == 2 and "missing" in r.stderr


def test_swap_writes_a_record_the_runner_accepts(fakes: Path) -> None:
    idx = "ragstack_lib_bench_synthetic"
    r = _swap(fakes, "--arm", "armx", "--glossary-file", str(fakes / "g.jsonl"), "--index", idx,
              "--es-url", "http://es.invalid", "--backfill", "fakebackfill", "--record-dir", str(fakes / "rec"),
              "--", "--replace-llm", "--shuffle-rendered", "261101|shuf")
    assert r.returncode == 0, r.stderr
    argv = (fakes / "argv.log").read_text().splitlines()
    assert len(argv) == 2 and "--apply" not in argv[0]
    assert f"--apply --confirm-index {idx}" in argv[1] and "--shuffle-rendered 261101|shuf" in argv[1]
    rec = json.loads((fakes / "rec/armx.swap.json").read_text())
    assert rec["shuffle_seed"] == SEED and rec["census_after"]["total"] == 100
    gsha = hashlib.sha256((fakes / "g.jsonl").read_bytes()).hexdigest()
    assert rec["apply"]["shuffle"]["seed"] == SEED and rec["glossary_file"]["sha256"] == gsha
    assert rec["apply"]["glossary_file"]["sha256"] == gsha            # the real backfill hashes the same file
    assert oct((fakes / "rec/armx.swap.json").stat().st_mode & 0o777) == "0o600"
    cfg = f10.normalise_config({
        "api": {"base_url": "http://api.invalid"},
        "corpora": {"O": {"collection": "c", "index": idx, "expected_chunks": 100}},
        "arms": {"armx": {"stamp_prefixes": ["llm/2:M:"], "merge": "llm-only", "glossary_file_sha256": {"O": gsha},
                          "shuffle_seed": SEED}}})
    assert f10.verify_swap_record(rec, cfg, "O", "armx") == {
        "total": 100, "stamps": {f"llm/2:M:x+merge=llm-only@file/1{SUFFIX}": 100}, "shuffle_suffix": SUFFIX}


def test_swap_refuses_when_the_backfill_disagrees(fakes: Path) -> None:
    idx = "ragstack_lib_bench_synthetic"
    common = ["--arm", "armx", "--glossary-file", str(fakes / "g.jsonl"), "--index", idx, "--es-url",
              "http://es.invalid", "--backfill", "fakebackfill", "--record-dir", str(fakes / "rec")]
    # the backfill applied another seed than the one on the command line
    r = _swap(fakes, *common, "--", "--shuffle-rendered", SEED, env={"FAKE_SEED": "261101|other"})
    assert r.returncode == 1 and "applied shuffle seed" in r.stderr and not (fakes / "rec/armx.swap.json").exists()
    # a shuffle applied although none was asked for (a true arm)
    r = _swap(fakes, *common, env={"FAKE_SEED": SEED})
    assert r.returncode == 1 and "applied shuffle seed" in r.stderr
    # fewer chunks written than planned
    r = _swap(fakes, *common, "--", "--shuffle-rendered", SEED, env={"FAKE_WRITTEN": "99"})
    assert r.returncode == 1 and "wrote 99 of 100" in r.stderr and not (fakes / "rec/armx.swap.json").exists()
    # the backfill rendered another file than the one swap.sh hashed: swap.sh records it, the runner refuses
    r = _swap(fakes, *common, "--", "--shuffle-rendered", SEED, env={"FAKE_SHA": "0" * 64})
    assert r.returncode == 0, r.stderr
    rec = json.loads((fakes / "rec/armx.swap.json").read_text())
    cfg = f10.normalise_config({
        "api": {"base_url": "http://api.invalid"},
        "corpora": {"O": {"collection": "c", "index": idx, "expected_chunks": 100}},
        "arms": {"armx": {"stamp_prefixes": ["llm/2:M:"], "merge": "llm-only", "shuffle_seed": SEED,
                          "glossary_file_sha256": {"O": rec["glossary_file"]["sha256"]}}}})
    with pytest.raises(f10.Refused, match="other than the one swap.sh hashed"):
        f10.verify_swap_record(rec, cfg, "O", "armx")
