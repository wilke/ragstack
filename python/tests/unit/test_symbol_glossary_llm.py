"""The offline LLM glossary step (#754): prompt/version, windowing, parsing,
validation through a fake model, the reassembly helper, and the script's
cache-only, skip-if-cached behaviour. No network."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ragstack.ingestion import symbol_glossary_llm as sgl
from ragstack.ingestion.reassembly import reassemble
from ragstack.ingestion.symbol_glossary import (
    Definition,
    Glossary,
    GlossaryEntry,
    SymbolGlossaryCache,
    content_key,
)

DOC = (
    "The virions become deactivated at a rate λv that depends on humidity. We "
    "treat the deactivation rate as bounded. where Qb is the breathing flow rate."
)


class FakeLLM:
    model = "fake/model"

    def __init__(self, answers: list[str], finish: str = "stop") -> None:
        self.answers = list(answers)
        self.finish = finish
        self.calls: list[list[dict[str, str]]] = []

    async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):  # noqa: ANN001
        self.calls.append(messages)
        assert temperature == 0.0
        return self.answers.pop(0), self.finish


def test_prompt_requires_one_entry_per_symbol_and_resolves_respectively() -> None:
    assert "One JSON object per symbol" in sgl.USER_PROMPT
    assert "respectively" in sgl.USER_PROMPT


def test_version_names_model_and_prompt() -> None:
    v = sgl.llm_version("RedHatAI/Scout")
    assert v.startswith("llm/1:RedHatAI/Scout:") and len(v.split(":")[2]) == 12
    assert sgl.llm_version("RedHatAI/Scout") == v


def test_windows() -> None:
    assert sgl.windows("abc", 10, 2, 4) == (["abc"], False)
    parts, trunc = sgl.windows("a" * 25, 10, 2, 4)
    assert [len(p) for p in parts] == [10, 10, 9] and not trunc
    parts, trunc = sgl.windows("a" * 100, 10, 0, 3)
    assert len(parts) == 3 and trunc


def test_parse_response_tolerates_fences_and_prose() -> None:
    ans = 'Here you go:\n```json\n[{"symbol": "Qb", "definition": "breathing flow rate"}, {"x": 1}, 3]\n```'
    assert sgl.parse_response(ans) == [("Qb", "breathing flow rate")]
    assert sgl.parse_response("no array") == []
    assert sgl.parse_response("[not json") == []
    assert sgl.parse_response('{"symbol": "Qb"}') == []


def test_extract_with_llm_validates_against_document() -> None:
    llm = FakeLLM([json.dumps([
        {"symbol": "λv", "definition": "deactivation rate"},
        {"symbol": "λv", "definition": "ventilation rate"},  # lexicon: "ventilation" absent
        {"symbol": "Qb", "definition": "breathing flow rate"},
        {"symbol": "λq", "definition": "rate"},  # symbol absent
    ])])
    g, stats = asyncio.run(sgl.extract_with_llm(llm, DOC))
    assert g.as_dict() == {"Qb": ["breathing flow rate"], "λv": ["deactivation rate"]}
    assert stats.proposed == 4 and stats.rejected == 2 and stats.windows == 1
    assert g.version == sgl.llm_version("fake/model")
    assert DOC in llm.calls[0][1]["content"]


def test_extract_with_llm_windows_union_and_cutoff() -> None:
    text = DOC + " " + "pad " * 50 + "where Cq is the quanta concentration."
    llm = FakeLLM(['[{"symbol": "Qb", "definition": "breathing flow rate"}]',
                   '[{"symbol": "Cq", "definition": "quanta concentration"}]',
                   "[]", "[]"], finish="length")
    g, stats = asyncio.run(sgl.extract_with_llm(llm, text, window_chars=150, window_overlap=10))
    assert set(g.as_dict()) == {"Qb", "Cq"}
    assert stats.cut_off == stats.windows == len(llm.calls)


def test_merged_prefers_llm_order() -> None:
    rules = Glossary((GlossaryEntry("Qb", (Definition("breathing flow rate (exhaled volume)", 10),)),))
    llm = Glossary((GlossaryEntry("Qb", (Definition("breathing flow rate", 40), Definition("breathing flow rate (exhaled volume)", 5))),
                    GlossaryEntry("λv", (Definition("deactivation rate", 3),))), "llm/1:x:y")
    m = sgl.merged(rules, llm)
    assert m.as_dict() == {"Qb": ["breathing flow rate (exhaled volume)", "breathing flow rate"],
                           "λv": ["deactivation rate"]}
    assert [d.offset for d in m.entries[0].definitions] == [5, 40]
    assert m.version == "llm/1:x:y+rules/3"


def test_reassemble_overlapping_chunks_and_gaps() -> None:
    text = "0123456789abcdefghij"
    rows: list[dict[str, Any]] = [
        {"content": text[8:16], "start_char": 8, "end_char": 16},
        {"content": text[0:10], "start_char": 0, "end_char": 10},
        {"content": text[18:20], "start_char": 18, "end_char": 20},
    ]
    r = reassemble("d", rows)
    assert r.text == text[:16] + "  " + text[18:]
    assert r.gaps == 1 and r.length_mismatches == 0
    assert reassemble("d", [{"content": "abc", "start_char": 0, "end_char": 5}]).length_mismatches == 1


def test_script_writes_only_cache_and_skips_cached(tmp_path, monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "symbol_glossary_llm_script",
        Path(__file__).resolve().parents[2] / "scripts" / "symbol_glossary_llm.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    docs = tmp_path / "docs.jsonl"
    docs.write_text(json.dumps({"text": DOC, "path": "a.pdf", "metadata": {}}) + "\n", encoding="utf-8")
    cache = tmp_path / "cache.jsonl"
    calls: list[int] = []

    class _LLM(FakeLLM):
        def __init__(self, base_url, model, http, api_key=None):  # noqa: ANN001
            super().__init__(['[{"symbol": "Qb", "definition": "breathing flow rate"}]'])
            self.model = model
            calls.append(1)

    monkeypatch.setattr(mod, "OpenAILLM", _LLM)
    args = ["--jsonl", str(docs), "--cache", str(cache), "--llm-endpoint", "http://127.0.0.1:1",
            "--llm-model", "fake/model"]
    assert mod.main(args) == 0
    got = SymbolGlossaryCache(cache).get(content_key(DOC))
    assert got is not None and got[1] == [("Qb", (("breathing flow rate", DOC.index("breathing")),))]
    lines = cache.read_text(encoding="utf-8").splitlines()
    assert mod.main(args) == 0  # second run: cached, no new record
    assert cache.read_text(encoding="utf-8").splitlines() == lines
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cache.jsonl", "docs.jsonl"]


def test_parse_response_recovers_latex_backslashes_and_trailing_values() -> None:
    assert sgl.parse_response(r'[{"symbol": "\lambda_v", "definition": "decay rate"}]') == [
        ("\\lambda_v", "decay rate")]
    assert sgl.parse_response('[{"symbol": "Qb", "definition": "x"}]\n[]') == [("Qb", "x")]
    assert sgl.parse_response(
        '[{"symbol": "Qb", "definition": "x"} {"symbol": "Cq", "definition": "y"}]'
    ) == [("Qb", "x"), ("Cq", "y")]


def test_version_folds_generation_limits() -> None:
    a = sgl.llm_version("m")
    assert a.endswith(":w80000x4:t2048")
    assert sgl.llm_version("m", 40_000, 2, 1024) != a


def test_extract_with_llm_stamps_its_limits() -> None:
    llm = FakeLLM(['[{"symbol": "Qb", "definition": "breathing flow rate"}]'])
    g, _ = asyncio.run(sgl.extract_with_llm(llm, DOC, max_tokens=512))
    assert g.version == sgl.llm_version("fake/model", max_tokens=512)


def test_cut_off_records_are_retried_only_with_flag(tmp_path, monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "sgl_script_trunc", Path(__file__).resolve().parents[2] / "scripts" / "symbol_glossary_llm.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    docs = tmp_path / "docs.jsonl"
    docs.write_text(json.dumps({"text": DOC, "path": "a.pdf", "metadata": {}}) + "\n", encoding="utf-8")
    cache = tmp_path / "cache.jsonl"
    calls: list[int] = []

    class _LLM(FakeLLM):
        def __init__(self, base_url, model, http, api_key=None):  # noqa: ANN001
            super().__init__(['[{"symbol": "Qb", "definition": "breathing flow rate"}]'], finish="length")
            self.model = model
            calls.append(1)

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):  # noqa: ANN001
            calls.append(2)
            return await super().complete_detailed(messages, max_tokens, temperature)

    monkeypatch.setattr(mod, "OpenAILLM", _LLM)
    base = ["--jsonl", str(docs), "--cache", str(cache), "--llm-endpoint", "http://127.0.0.1:1",
            "--llm-model", "fake/model"]
    assert mod.main(base) == 0
    rec = json.loads(cache.read_text(encoding="utf-8").splitlines()[0])
    assert rec["m"]["cut_off"] == 1
    n = calls.count(2)
    assert mod.main(base) == 0 and calls.count(2) == n          # counted as cached
    assert mod.main([*base, "--retry-truncated"]) == 0 and calls.count(2) == n + 1
