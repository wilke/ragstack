"""#847 G2a: the glossary workflow step (``scripts/glossary_step.py``), its
bounded extractor, the client fixes it needed, and the file's consumers on
the coupled path (``ingest_shard --glossary-file``, ``archive_version
--glossary-file``, ``glossary_file.py merge``).

Offline throughout. The model is :class:`tests.glossary_llm_support.FakeOpenAI`,
an ``httpx.MockTransport`` speaking the OpenAI-compatible contract at the HTTP
boundary; the real ``OpenAILLM`` client posts to it. Store URLs are the dead
port ``127.0.0.1:1``. Documents are synthetic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from ragstack.ingestion import symbol_glossary_llm as sgl
from ragstack.ingestion.archive import (
    GLOSSARY_NAME,
    ROLE_GLOSSARY,
    ArchiveCorrupt,
    verify_version,
)
from ragstack.ingestion.embedding_file import read_embedding_file
from ragstack.ingestion.glossary_file import (
    GLOSSARY_REFUSED_EXIT_CODE,
    GlossaryFileError,
    merge_files,
    read_glossary_file,
    row_to_obj,
    stamp_version,
)
from ragstack.ingestion.glossary_step import StepSettings, endpoint_name, run_step
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.symbol_glossary import (
    ANOTHER_SYMBOL_BETWEEN,
    DEFAULT_CONFIG,
    EXTRACTOR_VERSION,
    SYMBOL_ABSENT,
    VERSION_KEY,
    WORD_NOT_IN_DOCUMENT,
    validate_entries,
    validate_entries_detailed,
)
from ragstack.llm import OpenAILLM
from tests.glossary_llm_support import ENDPOINT, MODEL, Answer, Fail, FakeOpenAI, Status
from tests.unit.test_glossary_file import FakeES, _bf_args, _es_rows, bf, corpus  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import archive_version as archive_cli  # noqa: E402
import glossary_file as gf_cli  # noqa: E402
import glossary_step as step_cli  # noqa: E402
import ingest_shard  # noqa: E402

DEAD = "http://127.0.0.1:1"
PAD = " ".join(["filler words keep the windows apart"] * 4)


def _doc(tag: str) -> str:
    return (f"{tag} study. The virions become deactivated at a rate λv that depends on "
            f"humidity. We treat the deactivation rate as bounded. {PAD} where Qb is the "
            f"breathing flow rate. Measured: λv = 0.3 per hour and Qb = 0.5 m3/h. {PAD}")


DOCS = {"alpha": _doc("Alpha"), "beta": _doc("Beta"), "gamma": _doc("Gamma")}
GOOD = [{"symbol": "λv", "definition": "deactivation rate"},
        {"symbol": "Qb", "definition": "breathing flow rate"}]
BAD = [{"symbol": "λv", "definition": "ventilation rate"},   # word_not_in_document
       {"symbol": "λq", "definition": "rate"}]               # symbol_absent


def _about(tag: str):
    return lambda prompt: f"{tag} study" in prompt


def _shard(tmp_path: Path, docs: dict[str, str] = DOCS, *, name: str = "shard.jsonl",
           bulk: bool = False) -> Path:
    """The upload shape (``pdf_extract``: text + path + metadata) or, with
    ``bulk``, the JSONL bulk shape (richer metadata, a source URL)."""
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as fh:
        for doc_id, text in docs.items():
            rec: dict[str, Any] = {"text": text, "path": f"/corpus/{doc_id}.pdf",
                                   "metadata": {}}
            if bulk:
                rec = {"text": text, "path": f"PMC/{doc_id}.xml",
                       "metadata": {"pmcid": f"PMC{len(doc_id)}", "journal": "J",
                                    "content_type": "body", "source_url": "https://x.invalid/"}}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def _limits(**kw: Any) -> sgl.StepLimits:
    base: dict[str, Any] = {"window_chars": 80_000, "max_windows": 4, "max_tokens": 4096,
                                "timeout_s": 30.0, "thinking": False, "repair_rounds": 0,
                                "cutoff_retry": "off", "backoff": (0.0, 0.0)}
    base.update(kw)
    return sgl.StepLimits(**base)


def _settings(**kw: Any) -> StepSettings:
    lim = kw.pop("limits", None) or _limits()
    base: dict[str, Any] = {"llm": True, "endpoint": ENDPOINT, "model": MODEL,
                                "api_key_env": "FAKE_KEY", "concurrency": 2, "limits": lim}
    base.update(kw)
    return StepSettings(**base)


def _run(fake: FakeOpenAI, docs: dict[str, str] = DOCS, **kw: Any) -> tuple[list, dict]:
    settings = kw.pop("settings", None) or _settings(**kw.pop("settings_kw", {}))

    async def go():
        async with fake.client() as http:
            llm = OpenAILLM(ENDPOINT, MODEL, http,
                            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            loaded = JsonlLoader().load(str(kw.pop("shard")))
            return await run_step(loaded, settings, DEFAULT_CONFIG, llm=llm, http=http, **kw)

    return asyncio.run(go())


@pytest.fixture
def fake_cli(monkeypatch):
    """Route the script's own HTTP client to a fake, and drop the backoff."""
    holder: dict[str, FakeOpenAI] = {}

    def client(timeout_s: float, concurrency: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=holder["fake"].transport(),
                                 timeout=httpx.Timeout(timeout_s), trust_env=False)

    monkeypatch.setattr(step_cli, "_http_client", client)
    monkeypatch.setattr(step_cli, "BACKOFF", (0.0, 0.0))

    def use(fake: FakeOpenAI) -> FakeOpenAI:
        holder["fake"] = fake
        return fake

    return use


def _llm_argv(shard: Path, out: Path, *extra: str) -> list[str]:
    return [str(shard), "--out", str(out), "--summary", str(out.with_suffix(".summary.json")),
            "--llm", "on", "--llm-endpoint", ENDPOINT, "--llm-model", MODEL,
            "--llm-api-key-env", "FAKE_GLOSSARY_KEY", "--llm-timeout-s", "30",
            "--llm-max-tokens", "4096", "--llm-window-chars", "80000",
            "--llm-max-windows", "4", "--llm-concurrency", "2", *extra]


# --------------------------------------------------------------------------- #
# the degrade path (the one the issue's design rests on)
# --------------------------------------------------------------------------- #


def test_degrade_path_endpoint_down_yields_rules_only_rows_and_exit_0(
    tmp_path: Path, fake_cli, caplog
) -> None:
    """Endpoint unreachable: the probe fails after its retries, EVERY document
    gets the rules glossary with ``degraded == "endpoint_unavailable"``, the
    file is written, the step exits 0 and says so in a WARNING. No chat call
    is made — the outage costs one probe, not N timeouts."""
    fake = fake_cli(FakeOpenAI(models=Fail(httpx.ConnectError), default=Fail(httpx.ConnectError)))
    shard, out = _shard(tmp_path), tmp_path / "glossary.jsonl"
    with caplog.at_level(logging.WARNING):
        rc = step_cli.main(_llm_argv(shard, out))
    assert rc == 0
    gfile = read_glossary_file(out)
    assert gfile.header["counts"]["degraded"] == 3 and gfile.header["counts"]["with_llm"] == 0
    for row in gfile.rows.values():
        assert row.degraded == "endpoint_unavailable"
        assert row.llm is None and row.merged == row.rules
        assert row.glossary_version == EXTRACTOR_VERSION and row.rules.entries
    assert fake.chats == [] and len(fake.seen) == 3          # 1 probe + 2 retries
    summary = json.loads(out.with_suffix(".summary.json").read_text())
    assert summary["degraded"] == 3
    assert summary["degraded_reasons"] == {"endpoint_unavailable": 3}
    assert summary["probe"]["degrades_all"] == "endpoint_unavailable"
    assert any("DEGRADED" in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_a_5xx_mid_run_degrades_the_document_after_the_retries(tmp_path: Path) -> None:
    fake = FakeOpenAI([(_about("Alpha"), [Status(500)])], default=Answer(GOOD))
    rows, summary = _run(fake, shard=_shard(tmp_path))
    by = {r.doc_id: r for r in rows}
    degraded = [r for r in rows if r.degraded]
    assert [r.degraded for r in degraded] == ["endpoint_unavailable"]
    calls = degraded[0].provenance["llm"]["calls"]
    assert calls == [{**calls[0], "status": "endpoint_unavailable", "retries": 2}]
    assert sum(1 for s in fake.chats if "Alpha study" in s.prompt) == 3
    assert summary["with_llm"] == 2 and len(by) == 3


def test_a_429_then_200_is_retried_and_succeeds(tmp_path: Path) -> None:
    fake = FakeOpenAI([(_about("Alpha"), [Status(429), Answer(GOOD)])], default=Answer(GOOD))
    rows, summary = _run(fake, shard=_shard(tmp_path))
    assert all(r.degraded is None and r.llm is not None for r in rows)
    assert summary["retries"] == 1


def test_a_4xx_degrades_without_retry(tmp_path: Path) -> None:
    fake = FakeOpenAI([(_about("Beta"), [Status(400)])], default=Answer(GOOD))
    rows, _ = _run(fake, shard=_shard(tmp_path))
    assert sorted(str(r.degraded) for r in rows) == ["None", "None", "http_400"]
    assert sum(1 for s in fake.chats if "Beta study" in s.prompt) == 1


def test_a_timeout_degrades_one_document_not_the_batch(tmp_path: Path) -> None:
    fake = FakeOpenAI([(_about("Gamma"), [Fail(httpx.ReadTimeout)])], default=Answer(GOOD))
    rows, summary = _run(fake, shard=_shard(tmp_path))
    gamma = [r for r in rows if r.degraded]
    assert len(gamma) == 1 and gamma[0].degraded == "timeout"
    assert gamma[0].llm is None and gamma[0].merged == gamma[0].rules
    assert summary["with_llm"] == 2 and summary["degraded_reasons"] == {"timeout": 1}
    # a timeout already cost the full timeout: not retried
    assert sum(1 for s in fake.chats if "Gamma study" in s.prompt) == 1


def test_the_step_deadline_degrades_what_is_left(tmp_path: Path) -> None:
    ticks = iter(range(0, 10_000, 50))
    fake = FakeOpenAI(default=Answer(GOOD))
    rows, summary = _run(fake, shard=_shard(tmp_path),
                         settings=_settings(deadline_s=1.0, concurrency=1),
                         clock=lambda: float(next(ticks)))
    assert {r.degraded for r in rows} == {"deadline"} and fake.chats == []
    assert summary["degraded_reasons"] == {"deadline": 3}


def test_a_model_the_server_does_not_list_degrades_everything(tmp_path: Path) -> None:
    fake = FakeOpenAI(default=Answer(GOOD), served=("some/other-model",))
    rows, summary = _run(fake, shard=_shard(tmp_path))
    assert {r.degraded for r in rows} == {"model_not_served"} and fake.chats == []
    assert summary["probe"]["served"] == {"listed": ["some/other-model"]}


def test_the_served_model_is_recorded(tmp_path: Path) -> None:
    rows, _ = _run(FakeOpenAI(default=Answer(GOOD)), shard=_shard(tmp_path))
    llm = rows[0].provenance["llm"]
    assert llm["served"] == {"id": MODEL, "root": MODEL, "owned_by": "vllm",
                             "max_model_len": 131072}
    assert llm["endpoint_name"] == "fake-llm.invalid:8000"


# --------------------------------------------------------------------------- #
# the client fixes (#847 throughput comment)
# --------------------------------------------------------------------------- #


async def test_per_request_timeout_reaches_httpx() -> None:
    """Regression for the hard-coded ``timeout=120.0`` in ``complete_detailed``:
    an explicit per-request timeout is what httpx sees; none means the
    CLIENT's timeout, never 120."""
    fake = FakeOpenAI(default=Answer(GOOD))
    async with fake.client(timeout=7.0) as http:
        llm = OpenAILLM("http://llm.invalid", "m", http)
        await llm.complete_detailed([{"role": "user", "content": "x"}], timeout=300.0)
        await llm.complete_detailed([{"role": "user", "content": "x"}])
    assert fake.chats[0].timeout["read"] == 300.0
    assert fake.chats[1].timeout["read"] == 7.0


async def test_complete_result_reports_reasoning_and_usage_and_keeps_empty_content() -> None:
    fake = FakeOpenAI([(lambda p: "think" in p, [Answer(GOOD, content_null=True,
                                                         reasoning="hmm")])],
                      default=Answer(GOOD, finish="length"))
    async with fake.client() as http:
        llm = OpenAILLM("http://llm.invalid", "m", http)
        r = await llm.complete_result([{"role": "user", "content": "think"}])
        assert r.content == "" and r.reasoning and r.finish_reason == "stop"
        with pytest.raises(ValueError, match="empty answer"):
            await llm.complete_detailed([{"role": "user", "content": "think"}])
        r2 = await llm.complete_result([{"role": "user", "content": "x"}], extra={"seed": 7})
        assert r2.finish_reason == "length" and r2.usage["completion_tokens"] > 0
    assert fake.chats[-1].body["seed"] == 7


def test_thinking_off_is_sent_as_extra_body_and_reasoning_content_is_ignored(
    tmp_path: Path, fake_cli
) -> None:
    """``--llm-thinking off`` reaches the request as
    ``chat_template_kwargs.enable_thinking=false``; an answer that ALSO carries
    ``reasoning_content`` is parsed from ``content`` only (its presence is
    counted). A server that ignores the flag and answers into
    ``reasoning_content`` with ``content: null`` degrades that document
    ``bad_response`` — visibly, not as an empty glossary."""
    fake = fake_cli(FakeOpenAI(
        [(_about("Alpha"), [Answer(GOOD, reasoning=json.dumps(BAD))]),
         (_about("Beta"), [Answer(GOOD, content_null=True, reasoning=json.dumps(GOOD))])],
        default=Answer(GOOD)))
    out = tmp_path / "g.jsonl"
    assert step_cli.main(_llm_argv(_shard(tmp_path), out, "--llm-thinking", "off")) == 0
    assert {json.dumps(s.body["chat_template_kwargs"]) for s in fake.chats} == {
        '{"enable_thinking": false}'}
    assert all(s.body["temperature"] == 0.0 and s.body["max_tokens"] == 4096 for s in fake.chats)
    assert all(s.timeout["read"] == 30.0 for s in fake.chats)
    rows = {r.doc_id: r for r in read_glossary_file(out).rows.values()}
    by_text = {r.doc_id: r for r in rows.values()}
    degraded = [r for r in by_text.values() if r.degraded]
    assert [r.degraded for r in degraded] == ["bad_response"]
    ok = [r for r in by_text.values() if not r.degraded]
    assert all(r.llm is not None and r.llm.as_dict() == {"Qb": ["breathing flow rate"],
                                                        "λv": ["deactivation rate"]} for r in ok)
    summary = json.loads(out.with_suffix(".summary.json").read_text())
    assert summary["reasoning_seen"] == 2
    # on: the flag flips
    fake_cli(fake := FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(_shard(tmp_path), out, "--llm-thinking", "on")) == 0
    assert all(s.body["chat_template_kwargs"] == {"enable_thinking": True} for s in fake.chats)


# --------------------------------------------------------------------------- #
# bounded: cut-off split, repair, the call bound
# --------------------------------------------------------------------------- #


def test_cut_off_split_retries_once_and_records_it(tmp_path: Path) -> None:
    cut = json.dumps(GOOD[:1])[:-1] + ', {"symbol": "Q'     # a truncated JSON array
    fake = FakeOpenAI([(lambda p: "Alpha study" in p and p.count("filler") > 5,
                        [Answer(cut, finish="length")])],
                      default=Answer(GOOD[1:]))
    rows, summary = _run(fake, {"alpha": DOCS["alpha"]}, shard=_shard(tmp_path, {"alpha": DOCS["alpha"]}),
                         settings=_settings(limits=_limits(cutoff_retry="split")))
    llm = rows[0].provenance["llm"]
    assert [c["kind"] for c in llm["calls"]] == ["first", "split", "split"]
    assert llm["calls"][0]["finish"] == "length"
    assert llm["cut_off"] == 1 and llm["cut_off_unresolved"] == 0
    # the cut-off answer's salvaged object and the halves' answers are unioned
    assert rows[0].llm is not None and set(rows[0].llm.as_dict()) == {"λv", "Qb"}
    assert summary["cut_off"] == 1


def test_a_never_ending_cut_off_stays_bounded(tmp_path: Path) -> None:
    fake = FakeOpenAI(default=Answer("[", finish="length"))
    rows, _ = _run(fake, {"alpha": DOCS["alpha"]}, shard=_shard(tmp_path, {"alpha": DOCS["alpha"]}),
                   settings=_settings(limits=_limits(cutoff_retry="split")))
    llm = rows[0].provenance["llm"]
    assert len(fake.chats) == 3 and [c["kind"] for c in llm["calls"]] == ["first", "split", "split"]
    assert llm["cut_off"] == 1 and llm["cut_off_unresolved"] == 2
    # never an answer: degraded cut_off (#850 review), the rules stand in
    assert rows[0].degraded == "cut_off" and rows[0].llm is None
    assert rows[0].merged == rows[0].rules


def test_without_split_a_cut_off_is_recorded_not_retried(tmp_path: Path) -> None:
    fake = FakeOpenAI(default=Answer(GOOD, finish="length"))
    rows, _ = _run(fake, {"alpha": DOCS["alpha"]}, shard=_shard(tmp_path, {"alpha": DOCS["alpha"]}))
    assert len(fake.chats) == 1
    assert rows[0].provenance["llm"]["cut_off"] == 1
    assert rows[0].provenance["llm"]["cut_off_unresolved"] == 1


def test_calls_per_document_never_exceed_the_bound(tmp_path: Path) -> None:
    """An adversarial server: every call is 429'd twice, then cut off with
    entries the gate rejects (so every repair round fires). The requests per
    document never exceed ``max_calls x (1 + retries)``."""
    long_doc = {"alpha": DOCS["alpha"] * 10}
    counter = {"n": 0}

    class Adversary(FakeOpenAI):
        def _pick(self, prompt: str):
            counter["n"] += 1
            return Status(429) if counter["n"] % 3 else Answer(BAD, finish="length")

    # 2500 > WINDOW_OVERLAP: the PR's 1000 was a degenerate window that the
    # tool now refuses (#850 review)
    lim = _limits(window_chars=2500, max_windows=3, cutoff_retry="split", repair_rounds=2)
    fake = Adversary()
    rows, _ = _run(fake, long_doc, shard=_shard(tmp_path, long_doc),
                   settings=_settings(limits=lim))
    assert lim.max_calls() == 3 * (1 + 2 + 2) and lim.max_attempts() == 45
    assert 0 < len(fake.chats) <= lim.max_attempts()
    llm = rows[0].provenance["llm"]
    assert len(llm["calls"]) <= lim.max_calls() and llm["windows"] == 3 and llm["truncated"]
    assert {c["kind"] for c in llm["calls"]} == {"first", "split", "repair"}


def test_the_repair_round_sends_the_rejections_with_their_reasons(tmp_path: Path) -> None:
    fake = FakeOpenAI([(lambda p: "REJECTED:" in p, [Answer(GOOD)])], default=Answer(BAD))
    one = {"alpha": DOCS["alpha"]}
    rows, _ = _run(fake, one, shard=_shard(tmp_path, one),
                   settings=_settings(limits=_limits(repair_rounds=1)))
    repair = [s for s in fake.chats if "REJECTED:" in s.prompt]
    assert len(repair) == 1
    assert "— word_not_in_document" in repair[0].prompt and "— symbol_absent" in repair[0].prompt
    assert "Alpha study" in repair[0].prompt                 # the window goes along
    row = rows[0]
    assert row.llm is not None and row.llm.as_dict() == {"Qb": ["breathing flow rate"],
                                                         "λv": ["deactivation rate"]}
    llm = row.provenance["llm"]
    assert [c["kind"] for c in llm["calls"]] == ["first", "repair"]
    assert llm["proposed"] == 4 and llm["kept"] == 2 and llm["repair_proposed"] == 2
    assert row.drops == {"symbol_absent": 1, "word_not_in_document": 1}


def test_drops_are_keyed_by_gate_reason() -> None:
    text = DOCS["alpha"] + " the air change rate λa and the viral deactivation rate λv."
    raw = [("λv", "deactivation rate"), ("λv", "ventilation rate"), ("λq", "rate"),
           ("λv", "air change rate"), ("λv", "= 0.3 per hour"), ("rate", "breathing flow")]
    g, rejected = validate_entries_detailed(raw, text, "v")
    reasons = [why for _, _, why in rejected]
    assert reasons == [WORD_NOT_IN_DOCUMENT, SYMBOL_ABSENT, ANOTHER_SYMBOL_BETWEEN,
                       "implausible", "not_specific"]
    assert [(s, d) for s, d, _ in rejected] == raw[1:]          # as proposed, in order
    # the count-only gate is the same gate
    assert validate_entries(raw, text, "v") == (g, len(rejected))


def test_version_string_changes_with_thinking_and_repair() -> None:
    base = sgl.llm_version("m", 80_000, 4, 4096)
    assert base.startswith("llm/2:m:") and base.endswith(":w80000x4:t4096:k0:r0")
    assert sgl.llm_version("m", 80_000, 4, 4096, thinking=True).endswith(":k1:r0")
    assert sgl.llm_version("m", 80_000, 4, 4096, repair_rounds=1).endswith(":k0:r1")
    assert sgl.llm_version("m", 80_000, 4, 4096, thinking=None).endswith(":k" + "u:r0")
    assert _limits(thinking=True, repair_rounds=1).version("m").endswith(
        ":w80000x4:t4096:k1:r1")
    # the prompt hash covers the repair template
    h = sgl.prompt_hash()
    sgl_repair = sgl.REPAIR_PROMPT
    try:
        sgl.REPAIR_PROMPT = sgl_repair + " "
        assert sgl.prompt_hash() != h
    finally:
        sgl.REPAIR_PROMPT = sgl_repair


# --------------------------------------------------------------------------- #
# reuse, rules-only parity, configuration refusals, the environment
# --------------------------------------------------------------------------- #


def test_reuse_copies_matching_rows_and_calls_only_for_degraded(tmp_path: Path, fake_cli) -> None:
    shard = _shard(tmp_path)
    first = tmp_path / "first.jsonl"
    fake_cli(FakeOpenAI([(_about("Beta"), [Fail(httpx.ReadTimeout)])], default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, first)) == 0
    assert read_glossary_file(first).header["counts"]["degraded"] == 1
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    second = tmp_path / "second.jsonl"
    assert step_cli.main(_llm_argv(shard, second, "--reuse", str(first))) == 0
    assert [s for s in fake.chats if "Beta study" not in s.prompt] == []
    assert len(fake.chats) == 1                               # beta only
    g2 = read_glossary_file(second)
    assert g2.header["counts"]["degraded"] == 0 and g2.header["counts"]["with_llm"] == 3
    prior = read_glossary_file(first).rows
    from ragstack.ingestion.glossary_file import file_sha256

    for doc_id, row in g2.rows.items():
        if prior[doc_id].degraded is None:
            assert row.llm == prior[doc_id].llm
            assert row.provenance["reused_from"] == file_sha256(first)
        else:
            assert row.provenance["reused_from"] is None
    # a different version (here: repair rounds) reuses nothing
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, tmp_path / "third.jsonl", "--reuse", str(second),
                                   "--llm-repair-rounds", "1")) == 0
    assert {s.prompt.split(" study")[0][-5:] for s in fake.chats} >= {"Alpha", "Gamma"}


def test_rules_only_step_equals_glossary_file_build(tmp_path: Path) -> None:
    shard = _shard(tmp_path)
    a, b = tmp_path / "step.jsonl", tmp_path / "build.jsonl"
    assert step_cli.main([str(shard), "--out", str(a)]) == 0
    assert gf_cli.main(["build", "--jsonl", str(shard), "--out", str(b)]) == 0
    ga, gb = read_glossary_file(a), read_glossary_file(b)
    assert ga.header["counts"] == gb.header["counts"] and ga.header["config"] == gb.header["config"]
    assert list(ga.rows) == list(gb.rows)

    def strip(row):
        obj = row_to_obj(row)
        obj.pop("provenance")
        return obj

    assert [strip(r) for r in ga.rows.values()] == [strip(r) for r in gb.rows.values()]
    assert all(r.provenance["llm"] is None and r.provenance["rules"] == {"version": "rules/3"}
               for r in ga.rows.values())
    # rules rows are byte-identical across runs (the header's `created` aside)
    again = tmp_path / "again.jsonl"
    assert step_cli.main([str(shard), "--out", str(again)]) == 0
    assert a.read_text().splitlines()[1:] == again.read_text().splitlines()[1:]


def test_an_empty_shard_writes_an_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    out = tmp_path / "g.jsonl"
    assert step_cli.main([str(empty), "--out", str(out)]) == 0
    assert read_glossary_file(out).header["counts"]["documents"] == 0
    assert step_cli.main([str(tmp_path / "missing.jsonl"), "--out", str(out)]) == 3


@pytest.mark.parametrize("drop", ["--llm-timeout-s", "--llm-max-tokens", "--llm-window-chars",
                                  "--llm-max-windows", "--llm-endpoint", "--llm-model"])
def test_llm_on_without_a_limit_refuses_before_any_call(tmp_path: Path, fake_cli, drop,
                                                        capsys) -> None:
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    argv = _llm_argv(_shard(tmp_path), tmp_path / "g.jsonl")
    i = argv.index(drop)
    del argv[i:i + 2]
    assert step_cli.main(argv) == 2
    assert fake.seen == [] and not (tmp_path / "g.jsonl").exists()
    assert drop in capsys.readouterr().err
    # a zero is not a value either
    argv = _llm_argv(_shard(tmp_path), tmp_path / "g.jsonl")
    if drop not in ("--llm-endpoint", "--llm-model"):
        argv[argv.index(drop) + 1] = "0"
        assert step_cli.main(argv) == 2 and fake.seen == []


def test_other_configuration_refusals(tmp_path: Path, fake_cli) -> None:
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    shard, out = _shard(tmp_path), tmp_path / "g.jsonl"
    base = _llm_argv(shard, out)
    swap = base.index("--llm-endpoint") + 1
    for endpoint in ("http://user:secret@h.invalid:1", "http://h.invalid:1/?key=secret", "h:1"):
        argv = list(base)
        argv[swap] = endpoint
        assert step_cli.main(argv) == 2
    assert step_cli.main([*base, "--llm-api-key-env", "sk-not-a-name!"]) == 2
    assert step_cli.main([*base, "--config", '{"no_such_key": 1}']) == 2
    assert step_cli.main([str(shard), "--out", str(out), "--reuse", str(out)]) == 2
    assert fake.seen == []
    # off needs none of the LLM knobs and touches no network
    assert step_cli.main([str(shard), "--out", str(out)]) == 0 and fake.seen == []


def test_the_key_comes_from_the_named_variable_and_is_never_recorded(
    tmp_path: Path, fake_cli, monkeypatch, capsys, caplog
) -> None:
    secret = "sk-test-0123456789-SECRET"
    monkeypatch.setenv("FAKE_GLOSSARY_KEY", secret)
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    out = tmp_path / "g.jsonl"
    with caplog.at_level(logging.DEBUG):
        assert step_cli.main(_llm_argv(_shard(tmp_path), out)) == 0
    assert fake.seen and all(s.authorized for s in fake.seen)
    blobs = [out.read_text(), out.with_suffix(".summary.json").read_text(),
             capsys.readouterr().out, caplog.text]
    assert not any(secret in b for b in blobs)
    assert "FAKE_GLOSSARY_KEY" in blobs[1]                    # the NAME is recorded
    # unset: no Authorization header at all
    monkeypatch.delenv("FAKE_GLOSSARY_KEY")
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(_shard(tmp_path), out)) == 0
    assert fake.seen and not any(s.authorized for s in fake.seen)


def test_the_step_reads_no_environment_but_the_named_key(tmp_path: Path, fake_cli,
                                                          monkeypatch) -> None:
    """#849 tool rule: every knob is a flag. The only environment variable the
    step reads is the one ``--llm-api-key-env`` names (the RELEASE file is a
    file, not the environment)."""
    fake_cli(FakeOpenAI(default=Answer(GOOD)))
    read: list[str] = []

    class Recording(os._Environ):  # type: ignore[name-defined,misc]
        def __getitem__(self, key):
            # Who asked: the first frame outside os.py, the frozen Mapping
            # helpers and this class. Only reads by THIS REPO's code count:
            # the interpreter (gettext's locale variables, asyncio's debug
            # switch) and third-party libraries (nltk's data path, probed by
            # the shared tool provenance) are not knobs of the step.
            frame = sys._getframe(1)
            while frame is not None and (frame.f_code.co_filename in (os.__file__, __file__)
                                         or frame.f_code.co_filename.startswith("<frozen")):
                frame = frame.f_back
            caller = frame.f_code.co_filename if frame is not None else ""
            if caller.startswith((str(ROOT / "ragstack"), str(ROOT / "scripts"))):
                read.append(key)
            return super().__getitem__(key)

    env = Recording(os.environ._data, os.environ.encodekey, os.environ.decodekey,  # type: ignore[attr-defined]
                    os.environ.encodevalue, os.environ.decodevalue)  # type: ignore[attr-defined]
    monkeypatch.setattr(os, "environ", env)
    assert step_cli.main(_llm_argv(_shard(tmp_path), tmp_path / "g.jsonl")) == 0
    assert read == ["FAKE_GLOSSARY_KEY"], read
    # ...and importing it does not build the settings object (which reads the
    # whole environment). In a fresh interpreter: other tests import settings.
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util, sys; "
         f"spec = importlib.util.spec_from_file_location('gs', {str(ROOT / 'scripts' / 'glossary_step.py')!r}); "
         "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
         "print('ragstack.config' in sys.modules)"],
        capture_output=True, text=True, env={**dict(os.environ), "PYTHONPATH": str(ROOT)},
        check=True)
    assert out.stdout.strip() == "False", "the step imports the settings object (reads env)"


def test_endpoint_name_is_host_and_port_only() -> None:
    assert endpoint_name("http://u:p@h.example:8004/v1?key=s") == "h.example:8004"
    assert endpoint_name("https://h.example") == "h.example:443"
    assert endpoint_name("http://h.example/") == "h.example:80"


# --------------------------------------------------------------------------- #
# the file's consumers: ingest_shard, merge, pack
# --------------------------------------------------------------------------- #


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _ingest_argv(shard: Path, tmp_path: Path, *extra: str) -> list[str]:
    return [str(shard), "--out", str(tmp_path / "receipt.json"),
            "--embedding-file", str(tmp_path / "batch.emb.jsonl"), "--shard-id", "batch-0",
            "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", DEAD, "--es-url", DEAD,
            "--chunk-method", "fixed", "--chunk-size", "60", "--chunk-overlap", "0",
            "--chunk-token-counter", "estimate", *extra]


def _llm_file(tmp_path: Path, shard: Path, name: str = "g.jsonl") -> Path:
    out = tmp_path / name
    rows, _ = _run(FakeOpenAI([(_about("Beta"), [Status(400)])], default=Answer(GOOD)),
                   shard=shard)
    from ragstack.ingestion.glossary_file import write_glossary_file

    write_glossary_file(out, rows, producer="test")
    return out


@pytest.mark.parametrize("bulk", [False, True], ids=["upload", "bulk-jsonl"])
def test_ingest_shard_renders_from_the_glossary_file(tmp_path: Path, monkeypatch,
                                                     bulk: bool) -> None:
    emb = _CountingEmbedder()
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: emb)
    shard = _shard(tmp_path, bulk=bulk)
    gpath = _llm_file(tmp_path, shard)
    gfile = read_glossary_file(gpath)
    assert ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(gpath))) == 0
    chunks, _ = read_embedding_file(tmp_path / "batch.emb.jsonl")
    assert chunks and emb.calls
    stamps = {c.doc_id: c.metadata[VERSION_KEY] for c in chunks}
    for doc_id, row in gfile.rows.items():
        assert stamps[doc_id] == stamp_version(row.glossary_version)
    assert sorted(set(stamps.values())) == sorted({
        stamp_version(EXTRACTOR_VERSION),
        stamp_version(_limits().version(MODEL))})
    assert any(c.symbol_glossary for c in chunks)
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    rec = receipt["enrichment"]["symbol_glossary"]
    assert rec["source"] == "glossary_file" and rec["counts"] == gfile.header["counts"]
    assert rec["applied_batches"] == 1


@pytest.mark.parametrize("bulk", [False, True], ids=["upload", "bulk-jsonl"])
def test_ingest_shard_refuses_a_mismatched_glossary_file_before_embedding(
    tmp_path: Path, monkeypatch, capsys, bulk: bool
) -> None:
    emb = _CountingEmbedder()
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: emb)
    shard = _shard(tmp_path, bulk=bulk)
    good = _llm_file(tmp_path, shard)
    lines = good.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[2])
    row["text_sha256"] = "0" * 64
    lines[2] = json.dumps(row, ensure_ascii=False, sort_keys=True)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(bad)))
    assert rc == GLOSSARY_REFUSED_EXIT_CODE == 6
    assert emb.calls == 0 and not (tmp_path / "batch.emb.jsonl").exists()
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["error"].startswith("glossary_file_mismatch: mismatch=1")
    assert "glossary_file_mismatch" in capsys.readouterr().err
    # a file over another document set: missing rows, same refusal
    other = _shard(tmp_path, {"delta": _doc("Delta")}, name="other.jsonl", bulk=bulk)
    elsewhere = _llm_file(tmp_path, other, name="elsewhere.jsonl")
    assert ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file",
                                          str(elsewhere))) == 6
    assert "missing=3" in json.loads((tmp_path / "receipt.json").read_text())["error"]
    assert emb.calls == 0 and not (tmp_path / "batch.emb.jsonl").exists()


def test_ingest_shard_glossary_file_flag_rules(tmp_path: Path, monkeypatch) -> None:
    emb = _CountingEmbedder()
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: emb)
    shard = _shard(tmp_path)
    gpath = _llm_file(tmp_path, shard)
    with pytest.raises(SystemExit) as e:
        ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(gpath),
                                       "--symbol-glossary-config", '{"positional": false}'))
    assert e.value.code == 2
    broken = tmp_path / "broken.jsonl"
    broken.write_text("{}\n")
    with pytest.raises(SystemExit) as e:
        ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(broken)))
    assert e.value.code == 2 and emb.calls == 0
    # off is off: the file is ignored and no chunk carries a glossary
    assert ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(gpath),
                                          "--symbol-glossary", "off")) == 0
    chunks, _ = read_embedding_file(tmp_path / "batch.emb.jsonl")
    assert not any(c.symbol_glossary for c in chunks)
    assert not any(VERSION_KEY in c.metadata for c in chunks)


def test_merge_refuses_duplicate_doc_ids_and_differing_configs(tmp_path: Path) -> None:
    s1 = _shard(tmp_path, {"alpha": DOCS["alpha"]}, name="s1.jsonl")
    s2 = _shard(tmp_path, {"beta": DOCS["beta"], "gamma": DOCS["gamma"]}, name="s2.jsonl")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    assert step_cli.main([str(s1), "--out", str(a)]) == 0
    assert step_cli.main([str(s2), "--out", str(b)]) == 0
    out = tmp_path / "m.jsonl"
    assert gf_cli.main(["merge", "--out", str(out), str(a), str(b)]) == 0
    merged = read_glossary_file(out)
    assert merged.header["counts"]["documents"] == 3
    assert merged.header["producer"].startswith("merge(scripts/glossary_step.py ")
    first = out.read_bytes()
    assert gf_cli.main(["merge", "--out", str(out), str(a), str(b)]) == 0
    assert out.read_bytes() == first                          # deterministic
    with pytest.raises(GlossaryFileError, match="in both"):
        merge_files([a, b, a], tmp_path / "dup.jsonl")
    c = tmp_path / "c.jsonl"
    assert step_cli.main([str(s1), "--out", str(c), "--config", '{"positional": false}']) == 0
    with pytest.raises(GlossaryFileError, match="different config"):
        merge_files([b, c], tmp_path / "cfg.jsonl")
    assert gf_cli.main(["merge", "--out", str(tmp_path / "x.jsonl"), str(b), str(c)]) == 2


def test_pack_puts_the_glossary_in_the_version_and_the_manifest_role_verifies(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _CountingEmbedder())
    shard = _shard(tmp_path)
    gpath = _llm_file(tmp_path, shard)
    assert ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(gpath))) == 0
    out = tmp_path / "versions"
    argv = ["--version", "3", "--collection-id", "scratch-g2a", "--chunks",
            str(tmp_path / "batch.emb.jsonl"), "--receipt", str(tmp_path / "receipt.json"),
            "--out", str(out), "--workers", "1", "--glossary-file", str(gpath)]
    assert archive_cli.main(argv) == 0
    vdir = out / "3"
    manifest = verify_version(vdir)
    assert manifest["files"][ROLE_GLOSSARY] == GLOSSARY_NAME
    assert manifest["glossary"]["counts"] == read_glossary_file(gpath).header["counts"]
    assert read_glossary_file(vdir / GLOSSARY_NAME).rows.keys() == read_glossary_file(gpath).rows.keys()
    first = (vdir / "manifest.json").read_bytes()
    assert archive_cli.main(argv) == 0
    assert (vdir / "manifest.json").read_bytes() == first     # a retry is byte-identical
    # the role is verified like any file
    with (vdir / GLOSSARY_NAME).open("a", encoding="utf-8") as fh:
        fh.write("\n")
    with pytest.raises(ArchiveCorrupt):
        verify_version(vdir)
    # without the flag: no role, the manifest as before
    argv_plain = argv[:-2]
    assert archive_cli.main(argv_plain) == 0
    plain = verify_version(vdir)
    assert ROLE_GLOSSARY not in plain["files"] and "glossary" not in plain
    # a doc_id in two files refuses the pack
    assert archive_cli.main([*argv, str(gpath)]) == 1


# --------------------------------------------------------------------------- #
# #850 review follow-ups
# --------------------------------------------------------------------------- #

# A document that also names a TERM a model may offer as a symbol.
TERM_DOC = {"alpha": DOCS["alpha"] + " the air change rate λa and the viral deactivation rate λv."}
# What the gate rejects, by reason, for TERM_DOC.
MIXED = [{"symbol": "λv", "definition": "ventilation rate"},       # word_not_in_document
         {"symbol": "λq", "definition": "rate"},                   # symbol_absent, one token
         {"symbol": "air change rate", "definition": "air change"},  # not_a_symbol
         {"symbol": "rate", "definition": "breathing flow"},       # not_specific
         {"symbol": "ventilation speed", "definition": "air"}]     # symbol_absent, a phrase
UNREPAIRABLE = [MIXED[2], MIXED[3], MIXED[4]]


def test_empty_llm_half_falls_back_to_rules(tmp_path: Path) -> None:
    """Finding 1: a model answer the gate keeps nothing of is recorded as an
    empty LLM half, but ``merged`` is the rules (stamp ``rules/3``), not an
    empty glossary that drops the rules' entries."""
    one = {"alpha": DOCS["alpha"]}
    rows, summary = _run(FakeOpenAI(default=Answer([])), one, shard=_shard(tmp_path, one))
    r = rows[0]
    assert r.rules.entries, "the synthetic document has rules entries"
    assert r.degraded is None
    assert r.llm is not None and r.llm.entries == () and r.llm.version.startswith("llm/2:")
    assert r.provenance["llm"]["kept"] == 0                   # the LLM half is recorded as is
    assert r.merged == r.rules and r.glossary_version == EXTRACTOR_VERSION
    assert stamp_version(r.glossary_version) == "rules/3@file/1"
    assert summary["merged_entries"] == len(r.rules.entries) and summary["with_llm"] == 1


@pytest.mark.parametrize("policy", ["off", "split"])
def test_all_windows_cut_off_unresolved_is_degraded_cut_off(tmp_path: Path, policy: str) -> None:
    """Finding 1: every window cut off with nothing kept is a model that never
    answered — degraded ``cut_off``, the rules stand in, not a success with an
    empty glossary. A cut-off whose salvaged objects the gate keeps is not."""
    one = {"alpha": DOCS["alpha"]}
    rows, summary = _run(FakeOpenAI(default=Answer("[", finish="length")), one,
                         shard=_shard(tmp_path, one),
                         settings=_settings(limits=_limits(cutoff_retry=policy)))
    r = rows[0]
    assert r.degraded == "cut_off" and r.llm is None and r.merged == r.rules
    assert r.provenance["llm"]["degraded_reason"] == "cut_off"
    assert summary["degraded_reasons"] == {"cut_off": 1}
    # salvaged objects survive the gate: not degraded
    rows, _ = _run(FakeOpenAI(default=Answer(GOOD, finish="length")), one,
                   shard=_shard(tmp_path, one),
                   settings=_settings(limits=_limits(cutoff_retry=policy)))
    assert rows[0].degraded is None and rows[0].llm is not None and rows[0].llm.entries


def test_backfill_of_an_empty_llm_half_over_an_llm_stamped_index_needs_replace_llm(
    corpus: dict[str, Path],  # noqa: F811 — the fixture imported above
) -> None:
    """Finding 1, the consequence: a row whose LLM half is empty now renders
    the rules (``rules/…``), so a backfill over an index stamped ``llm/…``
    would replace an LLM stamp — the guard refuses without ``--replace-llm``."""
    docs = {d.id: d.content for d in JsonlLoader().load(str(corpus["shard"]))}
    rows, _ = _run(FakeOpenAI(default=Answer([])), docs, shard=corpus["shard"])
    assert all(r.llm is not None and r.glossary_version == EXTRACTOR_VERSION for r in rows)
    from ragstack.ingestion.glossary_file import write_glossary_file

    gpath = corpus["dir"] / "empty-llm.jsonl"
    write_glossary_file(gpath, rows, producer="test")
    es_rows = _es_rows(corpus["emb"])
    for r in es_rows:
        r["metadata"][VERSION_KEY] = "llm/1:m:abc"
    es = FakeES(es_rows)
    with pytest.raises(SystemExit, match="--replace-llm"):
        asyncio.run(bf.run(_bf_args(gpath, apply=True), es=es))
    assert es.bulks == []
    out = asyncio.run(bf.run(_bf_args(gpath, apply=True, replace_llm=True), es=es))
    assert out["chunks_written"] == len(es_rows)


def test_a_term_in_the_document_is_not_a_symbol_not_symbol_absent() -> None:
    """Finding 2: the label. A phrase the document contains is ``not_a_symbol``;
    ``symbol_absent`` stays for what the document does not contain."""
    text = TERM_DOC["alpha"]
    raw = [(e["symbol"], e["definition"]) for e in MIXED] + [("Humidity", "x")]
    _, rejected = validate_entries_detailed(raw, text, "v")
    assert {s: why for s, _, why in rejected} == {
        "λv": WORD_NOT_IN_DOCUMENT, "λq": SYMBOL_ABSENT, "air change rate": "not_a_symbol",
        "rate": "not_specific", "ventilation speed": SYMBOL_ABSENT,
        "Humidity": "not_a_symbol"}


def test_the_not_a_symbol_label_changes_no_decision(monkeypatch) -> None:
    """Finding 2: relabelling only. With the new label switched off (the
    9de253c gate), the kept glossary and the rejected entries are identical,
    and every reason is the same except symbol_absent -> not_a_symbol."""
    import ragstack.ingestion.symbol_glossary as sg

    text = TERM_DOC["alpha"] + " Qb(t) and Q b are the same; lambda v too."
    raw = ([(e["symbol"], e["definition"]) for e in MIXED + GOOD + BAD]
           + [("Qb(t)", "breathing flow rate"), ("Q b", "breathing flow rate"),
              ("the virions", "deactivated"), ("", "rate"), ("λv", "")])
    new_g, new_rej = validate_entries_detailed(raw, text, "v")
    with monkeypatch.context() as m:
        m.setattr(sg, "_occurs_but_not_a_symbol", lambda *a: False)
        old_g, old_rej = validate_entries_detailed(raw, text, "v")
    assert new_g == old_g and new_g.entries
    assert [(s, d) for s, d, _ in new_rej] == [(s, d) for s, d, _ in old_rej]
    changed = {(o, n) for (_, _, o), (_, _, n) in zip(old_rej, new_rej, strict=True) if o != n}
    assert changed == {(SYMBOL_ABSENT, "not_a_symbol")}
    assert validate_entries(raw, text, "v") == (new_g, len(new_rej))


def test_the_repair_payload_sends_only_repairable_reasons(tmp_path: Path) -> None:
    """Finding 2: the repair prompt carries word_not_in_document and a
    single-token symbol_absent, never not_specific, not_a_symbol or a phrase
    the document does not contain."""
    fake = FakeOpenAI([(lambda p: "REJECTED:" in p, [Answer(GOOD)])], default=Answer(MIXED))
    rows, _ = _run(fake, TERM_DOC, shard=_shard(tmp_path, TERM_DOC),
                   settings=_settings(limits=_limits(repair_rounds=1)))
    repair = [s for s in fake.chats if "REJECTED:" in s.prompt]
    assert len(repair) == 1
    sent = repair[0].prompt.split("REJECTED:")[1].split("Correct each entry")[0]
    assert "— word_not_in_document" in sent and "— symbol_absent" in sent
    assert '"λq"' in sent and '"λv"' in sent
    for absent in ("not_specific", "not_a_symbol", "air change rate", "ventilation speed",
                   '"symbol": "rate"'):
        assert absent not in sent, absent
    assert "not_specific" not in sgl.REPAIR_PROMPT
    assert rows[0].drops["not_a_symbol"] == 1 and rows[0].drops["not_specific"] == 1


def test_no_repair_call_when_nothing_is_repairable(tmp_path: Path) -> None:
    fake = FakeOpenAI(default=Answer(UNREPAIRABLE + GOOD))
    rows, _ = _run(fake, TERM_DOC, shard=_shard(tmp_path, TERM_DOC),
                   settings=_settings(limits=_limits(repair_rounds=2)))
    assert [s for s in fake.chats if "REJECTED:" in s.prompt] == []
    assert [c["kind"] for c in rows[0].provenance["llm"]["calls"]] == ["first"]
    assert rows[0].llm is not None and set(rows[0].llm.as_dict()) == {"λv", "Qb"}


def test_reuse_respects_cutoff_policy_and_seed(tmp_path: Path, fake_cli) -> None:
    """Finding 3 (the review's probe, inverted): a prior file built with cut-off
    ``off`` and no seed is NOT reused by a run with ``split`` or with a seed —
    neither is in the version string, both change the answer."""
    shard = _shard(tmp_path)
    first = tmp_path / "first.jsonl"
    fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, first, "--llm-cutoff-retry", "off")) == 0
    for extra in (["--llm-cutoff-retry", "split"], ["--llm-seed", "7"],
                  ["--llm-cutoff-retry", "split", "--llm-seed", "7"]):
        fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
        out = tmp_path / "second.jsonl"
        assert step_cli.main(_llm_argv(shard, out, "--reuse", str(first), *extra)) == 0
        assert len(fake.chats) == 3, extra                     # every document recomputed
        assert not any(r.provenance["reused_from"] for r in read_glossary_file(out).rows.values())
    # the same policy and seed still reuse
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, tmp_path / "third.jsonl", "--reuse", str(first),
                                   "--llm-cutoff-retry", "off")) == 0
    assert fake.chats == []


@pytest.mark.parametrize("chars,rc", [("1000", 2), ("2000", 2), ("2001", 0)])
def test_a_window_no_longer_than_the_overlap_refuses(tmp_path: Path, fake_cli, chars: str,
                                                     rc: int, capsys) -> None:
    """Finding 5: a window <= WINDOW_OVERLAP advances one character per window."""
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    argv = _llm_argv(_shard(tmp_path), tmp_path / "g.jsonl")
    argv[argv.index("--llm-window-chars") + 1] = chars
    assert step_cli.main(argv) == rc
    if rc == 2:
        assert fake.seen == [] and "--llm-window-chars must be > 2000" in capsys.readouterr().err


def test_summary_drops_exclude_reused_rows(tmp_path: Path, fake_cli) -> None:
    """Finding 11: like proposed/kept/calls, ``drops`` counts this run's work only."""
    shard = _shard(tmp_path)
    first = tmp_path / "first.jsonl"
    fake_cli(FakeOpenAI([(_about("Beta"), [Fail(httpx.ReadTimeout)])],
                        default=Answer(GOOD + BAD)))
    assert step_cli.main(_llm_argv(shard, first)) == 0
    fake_cli(FakeOpenAI(default=Answer(GOOD + BAD)))
    second = tmp_path / "second.jsonl"
    assert step_cli.main(_llm_argv(shard, second, "--reuse", str(first))) == 0
    summary = json.loads(second.with_suffix(".summary.json").read_text())
    assert summary["reused"] == 2 and summary["proposed"] == 4 and summary["llm_calls"] == 1
    assert summary["drops"] == {"symbol_absent": 1, "word_not_in_document": 1}


def test_a_malformed_shard_is_zero_rows_and_exit_0(tmp_path: Path) -> None:
    """Finding 12: kept as is, and documented on the exit-code line."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n{\n", encoding="utf-8")
    out = tmp_path / "g.jsonl"
    assert step_cli.main([str(bad), "--out", str(out)]) == 0
    assert read_glossary_file(out).header["counts"]["documents"] == 0
    assert "malformed throughout" in (step_cli.__doc__ or "")


@pytest.mark.parametrize("flag,body,k", [
    ("off", {"enable_thinking": False}, "k0"),
    ("on", {"enable_thinking": True}, "k1"),
    ("unset", None, "ku"),
])
def test_llm_thinking_tri_state_request_body_and_version(tmp_path: Path, fake_cli, flag: str,
                                                         body: Any, k: str) -> None:
    """Finding 13: ``unset`` sends no ``chat_template_kwargs`` at all (a strict
    OpenAI-compatible server is not sent an unknown field); the version's
    ``k`` field records which."""
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    out = tmp_path / "g.jsonl"
    assert step_cli.main(_llm_argv(_shard(tmp_path), out, "--llm-thinking", flag)) == 0
    assert fake.chats
    for s in fake.chats:
        if body is None:
            assert "chat_template_kwargs" not in s.body
        else:
            assert s.body["chat_template_kwargs"] == body
    versions = {r.llm.version for r in read_glossary_file(out).rows.values() if r.llm}
    assert len(versions) == 1 and f":{k}:r0" in versions.pop()


def test_repairable_reasons_are_gate_reasons_and_exclude_the_unrepairable() -> None:
    from ragstack.ingestion.symbol_glossary import GATE_REASONS, NOT_A_SYMBOL, NOT_SPECIFIC
    from ragstack.ingestion.symbol_glossary_llm import REPAIRABLE_REASONS

    assert REPAIRABLE_REASONS <= set(GATE_REASONS)
    assert set(GATE_REASONS) - REPAIRABLE_REASONS == {NOT_SPECIFIC, NOT_A_SYMBOL}


# --------------------------------------------------------------------------- #
# v1.6.20 Gap A: the receipt carries the degraded reasons
# --------------------------------------------------------------------------- #


def test_receipt_from_a_glossary_file_carries_degraded_reasons(
    tmp_path: Path, monkeypatch
) -> None:
    """On the upload path the archive is the only output, so the receipt's
    ``enrichment.symbol_glossary`` is where the API reads the counts: it must
    carry the per-reason tally ``glossary-summary.json`` has, and the
    distinct glossary versions."""
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _CountingEmbedder())
    shard = _shard(tmp_path)
    out = tmp_path / "g.jsonl"
    fake = FakeOpenAI([(_about("Beta"), [Status(400)])], default=Answer(GOOD))
    rows, summ = _run(fake, shard=shard)
    from ragstack.ingestion.glossary_file import write_glossary_file

    write_glossary_file(out, rows, producer="test")
    assert ingest_shard.main(_ingest_argv(shard, tmp_path, "--glossary-file", str(out))) == 0
    rec = json.loads((tmp_path / "receipt.json").read_text())["enrichment"]["symbol_glossary"]
    assert rec["degraded_reasons"] == summ["degraded_reasons"]
    assert sum(rec["degraded_reasons"].values()) == rec["counts"]["degraded"] == 1
    assert rec["glossary_versions"] == sorted({r.glossary_version for r in rows})
    assert len(rec["glossary_versions"]) == 2       # rules (the degraded doc) + llm


# --------------------------------------------------------------------------- #
# v1.6.20: --llm-extra-body
# --------------------------------------------------------------------------- #

EXTRA = {"reasoning_effort": "low", "chat_template_kwargs": {"custom": 1}}


def test_llm_extra_body_is_sent_and_recorded(tmp_path: Path, fake_cli) -> None:
    """Sent with every call, merged with --llm-thinking's key; recorded in
    every row's provenance.llm and the summary settings; its hash is in the
    version's ``k`` field (``k0+e<8 hex>``)."""
    from ragstack.ingestion.symbol_glossary_llm import extra_body_hash

    shard = _shard(tmp_path)
    out = tmp_path / "g.jsonl"
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, out, "--llm-thinking", "off",
                                   "--llm-extra-body", json.dumps(EXTRA))) == 0
    assert fake.chats
    for seen in fake.chats:
        assert seen.body["reasoning_effort"] == "low"
        assert seen.body["chat_template_kwargs"] == {"custom": 1, "enable_thinking": False}
        assert seen.body["model"] == MODEL                     # core fields untouched
    eh = extra_body_hash(EXTRA)
    assert eh is not None and len(eh) == 8
    version = _limits(thinking=False, extra_body=EXTRA).version(MODEL)
    assert f":k0+e{eh}:r0" in version
    rows = read_glossary_file(out).rows.values()
    for row in rows:
        assert row.provenance["llm"]["extra_body"] == EXTRA
        assert row.llm is not None and row.llm.version == version
    summary = json.loads(out.with_suffix(".summary.json").read_text())
    assert summary["settings"]["extra_body"] == EXTRA
    assert summary["settings"]["version"] == version
    # without the flag: nothing extra sent, nothing recorded, the old version string
    plain = tmp_path / "plain.jsonl"
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, plain)) == 0
    assert all("reasoning_effort" not in s.body for s in fake.chats)
    assert all(r.provenance["llm"]["extra_body"] is None
               for r in read_glossary_file(plain).rows.values())
    assert _limits().version(MODEL) == sgl.llm_version(MODEL, 80_000, 4, 4096,
                                                       thinking=False, repair_rounds=0)
    assert "+e" not in _limits().version(MODEL)


def test_llm_extra_body_with_thinking_unset_may_set_enable_thinking(
        tmp_path: Path, fake_cli) -> None:
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    body = {"chat_template_kwargs": {"enable_thinking": True}}
    assert step_cli.main(_llm_argv(_shard(tmp_path), tmp_path / "g.jsonl", "--llm-thinking",
                                   "unset", "--llm-extra-body", json.dumps(body))) == 0
    assert all(s.body["chat_template_kwargs"] == {"enable_thinking": True} for s in fake.chats)
    assert ":ku+e" in json.loads((tmp_path / "g.summary.json").read_text())["settings"]["version"]


@pytest.mark.parametrize("raw,needle", [
    ("[1, 2]", "must be a JSON object"),
    ('"low"', "must be a JSON object"),
    ("{not json", "bad JSON"),
    ('{"model": "other"}', "may not set ['model']"),
    ('{"max_tokens": 9, "temperature": 1}', "may not set ['max_tokens', 'temperature']"),
    ('{"seed": 1}', "may not set ['seed']"),
    ('{"messages": []}', "may not set ['messages']"),
    ('{"api_key": "x"}', "credential-like keys ['api_key']"),
    ('{"extra_headers": {"Authorization": "Bearer x"}}', "extra_headers.Authorization"),
    ('{"opts": [{"access_token": "x"}]}', "opts[0].access_token"),
    ('{"token": "x"}', "credential-like keys ['token']"),
    ('{"chat_template_kwargs": {"enable_thinking": true}}', "--llm-thinking owns"),
])
def test_llm_extra_body_refusals(tmp_path: Path, fake_cli, capsys, raw: str,
                                 needle: str) -> None:
    """Refused with exit 2 before any call (the probe included)."""
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    rc = step_cli.main(_llm_argv(_shard(tmp_path), tmp_path / "g.jsonl",
                                 "--llm-extra-body", raw))
    assert rc == 2 and fake.seen == []
    assert needle in capsys.readouterr().err
    assert not (tmp_path / "g.jsonl").exists()


def test_llm_extra_body_needs_llm_on(tmp_path: Path, capsys) -> None:
    rc = step_cli.main([str(_shard(tmp_path)), "--out", str(tmp_path / "g.jsonl"),
                        "--llm-extra-body", '{"reasoning_effort": "low"}'])
    assert rc == 2 and "--llm-extra-body needs --llm on" in capsys.readouterr().err


def test_reuse_does_not_reuse_rows_with_a_different_extra_body(tmp_path: Path,
                                                               fake_cli) -> None:
    shard = _shard(tmp_path)
    first = tmp_path / "first.jsonl"
    fake_cli(FakeOpenAI(default=Answer(GOOD)))
    assert step_cli.main(_llm_argv(shard, first, "--llm-extra-body", json.dumps(EXTRA))) == 0
    for extra in ([], ["--llm-extra-body", '{"reasoning_effort": "high"}']):
        fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
        out = tmp_path / "second.jsonl"
        assert step_cli.main(_llm_argv(shard, out, "--reuse", str(first), *extra)) == 0
        assert len(fake.chats) == 3, extra                     # every document recomputed
        assert not any(r.provenance["reused_from"] for r in read_glossary_file(out).rows.values())
    # the same body (keys in another order: same canonical hash) still reuses
    fake = fake_cli(FakeOpenAI(default=Answer(GOOD)))
    same = json.dumps(dict(reversed(list(EXTRA.items()))))
    assert step_cli.main(_llm_argv(shard, tmp_path / "third.jsonl", "--reuse", str(first),
                                   "--llm-extra-body", same)) == 0
    assert fake.chats == []
