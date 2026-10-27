"""Tests for the part-4 public export on synthetic inputs (no private data, no network)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import part4_export_public as px  # noqa: E402

QUESTION = "how do I reset the widget password from the command line"
QUOTE_A = "run widgetctl reset with the admin flag to clear the stored password"
QUOTE_B = "the command asks for confirmation before it writes the new credentials"
DOC = "0a1b2c3d-1111-4222-8333-444455556666"
PRIV_STRATUM, PUB_STRATUM = "acmeguides", "topic"
TEST_DESC = ("page sign-flip permutation (standard p; exact when <= 20 non-zero pages), "
             "page-cluster bootstrap percentile CI, McNemar descriptive")


def config(src: Path) -> dict[str, Any]:
    return {"export_salt": "synthetic-salt", "out_dir": str(src),
            "public_stratum_labels": {PRIV_STRATUM: PUB_STRATUM, "cli": "cli"},
            "export_private_terms": ["acmecorp"], "index": "idx-synthetic-secret",
            "stratum_prefixes": [["acme-docs/", PRIV_STRATUM]],
            "gen_url": "http://gen.synthetic.example:8001/v1", "judge_url": "http://judge.synthetic.example/v1",
            "seed_questions": {"P": {"P1": ["what is the seed question about widgets here", ["seed.md"]]},
                               "N": {}, "W": {}},
            "prompts": {"ADDQ_USER": "You are classifying a quote about the product. {question} {quote}"},
            "part4": {"files": {"questions": "part4-questions.jsonl"}, "prior_question_files": []}}


def readers(grade: str) -> dict[str, Any]:
    return {rd: {"ok": True, "finish": "stop", "dropped": 0, "reader_prompt_tokens": 900, "sol": grade,
                 "opus48": None} for rd in ("qwen", "scout")}


def part4_results() -> dict[str, Any]:
    cell = {"found": True, "chunks": 3, "ctx_tokens": 4000, "shortfall": 96, "truncated": False,
            "truncated_empty": False, "prompt_tokens_sfr": 4100, "readers": readers("correct")}
    return {
        "schema": "part4-results/1", "prereg": {"file": "PREREG-acmecorp-part4.md", "sha256": "ab" * 32},
        "config_sha256": "cd" * 32, "citable": False,
        "provenance": {"commit": "f" * 40, "describe": "v1-1-gfff", "dirty": False, "untracked": 0,
                       "citable": False, "recorded_at": "2026-10-25T00:00:00Z", "version": "v1+fff",
                       "host": "synthhost42", "warnings": ["a long free-form warning that names a path /home/x"]},
        "run_segments": [{"step": "generation", "end": "paused", "reason": "pause_file", "resumed": False,
                          "totals_since_resume": {"gateway_calls": 10, "gateway_prompt_tokens": 1000,
                                                  "gateway_completion_tokens": 100}},
                         {"step": "results", "end": "complete", "resumed": True,
                          "totals_since_resume": {"gateway_calls": 5, "gateway_prompt_tokens": 500,
                                                  "gateway_completion_tokens": 50, "local_calls": 7}}],
        "design": {"arms": ["T512o0", "L"], "readers": ["qwen", "scout"], "seed": 7,
                   "generator": {"model": "Synth Model", "tag": "synth", "host": "gen-node-7"},
                   "analysis": {"delta": 0.075, "alpha": 0.05, "procedure": "holm4"}},
        "n": {"M": 1, "S": 0}, "by_stratum": {"M": {PRIV_STRATUM: 1}, "S": {}},
        "index": {"chunks": 10, "docs": 2, "strata_pages": {PRIV_STRATUM: 2}},
        "generation": {"usage": {"prompt_tokens": 800, "completion_tokens": 80}},
        "grader_usage": {"sol": {"calls_recorded": 4, "prompt_tokens": 600, "completion_tokens": 60}},
        "gen_fallback": {"items": [{"record_id": "M000k0", "set": "M", "stratum": PRIV_STRATUM, "doc_id": DOC,
                                    "page_tokens": 3000, "gen": "sol"}],
                         "by_stratum": {PRIV_STRATUM: 1}, "page_tokens_median": 3000.0},
        "analysis": {"status": "complete", "test": TEST_DESC,
                     "secondary": {"per_stratum": {PRIV_STRATUM: {"n_questions": 1, "p": 0.5}}}},
        "subsets": {"retest": ["01d18ccedbc60f7a"]},
        "questions": {"M000k0": {"set": "M", "stratum": PRIV_STRATUM, "doc_id": DOC, "page_tokens": 3000,
                                 "gen": "sol"}},
        "per_item": {"M000k0": {"T512o0": {"B4096": cell}, "L": {"B4096": copy.deepcopy(cell)}}},
    }


def addq_results() -> dict[str, Any]:
    return {"schema": "part4-addq-results/1", "status": "EXPLORATORY",
            "note": "Nothing here changes a part-4 confirmatory verdict.",
            "prereg": {"file": "PREREG-acmecorp-addq.md", "sha256": "12" * 32}, "config_sha256": "34" * 32,
            "inputs_sha256": {"questions": "56" * 32}, "citable": False,
            "provenance": {"commit": "e" * 40, "host": "synthhost42"},
            "run_segments": [{"end": "complete", "totals_since_resume": {"gateway_calls": 4,
                                                                         "gateway_prompt_tokens": 40,
                                                                         "gateway_completion_tokens": 4}}],
            "design": {"primary": {"model": "Synth A", "tag": "a"}, "prompt_sha256": "78" * 32,
                       "positions": {"A": "gold[0]", "B": "gold[1]"}, "prompt": "should never be exported"},
            "analysis": {"clean_sets": {"primary": {"n": 1, "ids": ["M000k0"]}},
                         "prevalence": {"a": {"by_stratum": {PRIV_STRATUM: {"n": 1}}}},
                         "sensitivity": {"clean": {"primary": {"summary": "not robust: H2 unresolved on the "
                                                                         "clean set"}}},
                         "usage": {"a": {"calls_recorded": 2}}}}


def make_src(tmp_path: Path, part4: dict[str, Any] | None = None, addq: dict[str, Any] | None = None) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "results-part4.json").write_text(json.dumps(part4 or part4_results()))
    (src / "results-part4-addendum-q.json").write_text(json.dumps(addq or addq_results()))
    (src / "part4-questions.jsonl").write_text(json.dumps(
        {"record_id": "M000k0", "set": "M", "stratum": PRIV_STRATUM, "doc_id": DOC, "question": QUESTION,
         "quote_a": QUOTE_A, "quote_b": QUOTE_B, "gold": [QUOTE_A, QUOTE_B], "filename": "acme-docs/x.md"}) + "\n")
    return src


@pytest.fixture(autouse=True)
def _no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    import ragstack.provenance as rp

    monkeypatch.setattr(rp, "experiment_provenance", lambda repo=None: {"commit": "c" * 40, "host": "h"})


def run(tmp_path: Path, part4: dict[str, Any] | None = None, addq: dict[str, Any] | None = None) -> tuple[int, Path]:
    src = make_src(tmp_path, part4, addq)
    dest = tmp_path / "out"
    return px.export(config(src), src, dest), dest


def all_text(dest: Path) -> str:
    return "".join(p.read_text() for p in sorted(dest.glob("*.json")))


def test_clean_export_writes_every_file(tmp_path: Path) -> None:
    rc, dest = run(tmp_path)
    assert rc == 0
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        ["part4_summary.json", "part4_per_record.json", "addq_summary.json", "gateway_usage_part4.json",
         "export_manifest.json"])
    usage = json.loads((dest / "gateway_usage_part4.json").read_text())
    assert usage["part4"]["calls"] == 15 and usage["part4"]["input_tokens"] == 1500
    assert usage["addendum_Q"]["output_tokens"] == 4 and usage["total"]["calls"] == 19
    text = all_text(dest)
    for leak in ("should never be exported", "PREREG-acmecorp", "acme-docs", DOC, "01d18ccedbc60f7a"):
        assert leak not in text
    pr = json.loads((dest / "part4_per_record.json").read_text())["records"]["M000k0"]
    assert pr["doc"].startswith("d:") and len(pr["doc"]) == 14
    assert pr["arms"]["L"]["B4096"][0] is True
    assert pr["arms"]["L"]["B4096"][-1]["scout"] == [True, "stop", 0, {"opus48": None, "sol": "correct"}]
    man = json.loads((dest / "export_manifest.json").read_text())
    assert set(man["files"]) == {"part4_summary.json", "part4_per_record.json", "addq_summary.json",
                                 "gateway_usage_part4.json"}


def test_strata_are_relabelled(tmp_path: Path) -> None:
    rc, dest = run(tmp_path)
    assert rc == 0
    text = all_text(dest)
    assert PRIV_STRATUM not in text
    s = json.loads((dest / "part4_summary.json").read_text())
    assert s["by_stratum"]["M"] == {PUB_STRATUM: 1}
    assert set(s["analysis"]["secondary"]["per_stratum"]) == {PUB_STRATUM}
    assert s["gen_fallback"]["items"][0]["stratum"] == PUB_STRATUM
    pr = json.loads((dest / "part4_per_record.json").read_text())["records"]["M000k0"]
    assert pr["stratum"] == PUB_STRATUM
    a = json.loads((dest / "addq_summary.json").read_text())
    assert set(a["analysis"]["prevalence"]["a"]["by_stratum"]) == {PUB_STRATUM}


def test_host_is_dropped(tmp_path: Path) -> None:
    rc, dest = run(tmp_path)
    assert rc == 0
    assert "synthhost42" not in all_text(dest)
    data = "".join(p.read_text() for p in sorted(dest.glob("*.json")) if p.name != "export_manifest.json")
    assert "gen-node-7" not in data and '"host"' not in data   # the manifest lists "host" as a skipped key
    s = json.loads((dest / "part4_summary.json").read_text())
    assert set(s["provenance"]) == set(px.PROVENANCE_KEYS)


def test_unsafe_string_refuses(tmp_path: Path) -> None:
    p4 = part4_results()
    p4["analysis"]["remark"] = "a free-form sentence with far too many words to be a label, says the run"
    rc, dest = run(tmp_path, part4=p4)
    assert rc == 2 and not dest.exists()


def test_private_term_refuses(tmp_path: Path) -> None:
    p4 = part4_results()
    p4["analysis"]["remark"] = "acmecorp-arm"
    rc, dest = run(tmp_path, part4=p4)
    assert rc == 2 and not dest.exists()


def test_six_word_run_from_a_question_refuses(tmp_path: Path) -> None:
    p4 = part4_results()
    p4["design"]["label"] = "reset the widget password from the"   # six words of QUESTION, SAFE as a string
    rc, dest = run(tmp_path, part4=p4)
    assert rc == 3 and not dest.exists()


def test_raw_uuid_refuses(tmp_path: Path) -> None:
    p4 = part4_results()
    p4["design"]["ref"] = DOC.upper()    # not caught by the lower-case hashing rule; SAFE as a string
    rc, dest = run(tmp_path, part4=p4)
    assert rc == 3 and not dest.exists()


def test_lowercase_uuid_is_hashed_not_exported(tmp_path: Path) -> None:
    p4 = part4_results()
    p4["design"]["ref"] = DOC
    rc, dest = run(tmp_path, part4=p4)
    assert rc == 0 and DOC not in all_text(dest)
    assert json.loads((dest / "part4_summary.json").read_text())["design"]["ref"].startswith("d:")


def test_numeric_term_inside_another_number_is_not_a_leak() -> None:
    assert not px.term_in("18766", '{"x": -0.15718766176}')
    assert px.term_in("18766", '{"port": 18766}')
    assert px.term_in("acme", '{"x": "ACMEcorp"}')
