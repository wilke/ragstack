"""The alias drafting mode: matchers, the standalone check, layer 1 for G and C jobs, the
pool builder (caps, seeded order, waves, yield estimate), the drafter chain with the stop
rule, the pilot, the observed-calls counter and the budget stop.

Fake gateway and fake CLI; a synthetic corpus; no store, no network, no model.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import alias_abbrev  # noqa: E402
import glossbench_alias_checks as ak  # noqa: E402
import glossbench_alias_draft as ad  # noqa: E402
import glossbench_alias_pool as apool  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402

PROV = {"commit": "a" * 40, "dirty": False, "untracked": 0}
SERVED = "served-drafter-alias"

# ------------------------------------------------------------------ a synthetic corpus

SENTENCES = [
    "The air change rate (ACH) of ward {i} was measured with tracer gas. ",           # c0 def ACH
    "The ACH fell to 2.{i} per hour when the windows of ward {i} were closed. ",       # c1 G
    "With the fans on, ACH rose to {i}6 per hour in every room. ",                     # c2 G
    "Doubling the ACH halved the aerosol concentration within {i} minutes. ",          # c3 G
    "The air change rate depended strongly on the wind direction near ward {i}. ",    # c4 C (ACH)
    "Here Qv denotes the outdoor airflow supplied by unit {i}. ",                      # c5 def Qv
    "Raising Qv lowered the carbon dioxide level below 8{i}0 ppm. ",                   # c6 G
    "Qv was kept constant during the night shifts of block {i}. ",                     # c7 G
    "Measuring outdoor airflow required a calibrated hood in block {i}. ",             # c8 C (Qv)
    "Staff change shifts at seven in block {i}. ",                                     # c9 (no alias)
    "The ACH changed little in winter in block {i}. ",                                 # c10 G
]


def world(tmp: Path, n_papers: int = 3, **pool_over: Any) -> tuple[Path, dict[str, Any]]:
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows, entries = [], []
    for i in range(1, n_papers + 1):
        doc, s = f"d{i}", 0
        for n, t in enumerate(SENTENCES):
            p = t.format(i=i)
            rows.append({"id": f"{doc}-c{n}", "doc_id": doc, "content": p, "start_char": s, "end_char": s + len(p),
                         "metadata": {"title": f"Ward study {i}"}})
            s += len(p)
        entries.append({"doc_id": doc, "alias": "ACH", "primary": "air change rate", "kind": "abbreviation",
                        "def_chunk_id": f"{doc}-c0",
                        "use_chunk_ids": [f"{doc}-c{k}" for k in (0, 1, 2, 3, 4, 9, 10)]})
        entries.append({"doc_id": doc, "alias": "Qv", "primary": "outdoor airflow", "kind": "symbol",
                        "def_chunk_id": f"{doc}-c5", "use_chunk_ids": [f"{doc}-c{k}" for k in (5, 6, 7)]})
    chunks = data / "chunks.jsonl"
    chunks.write_text("".join(json.dumps(r) + "\n" for r in rows))
    gl = data / "alias-glossary.jsonl"
    gl.write_text(json.dumps({"schema": "alias-glossary/1"}) + "\n" + "".join(json.dumps(e) + "\n" for e in entries))
    cfg = {"out_dir": str(tmp / "out"),
           "corpora": {"T": {"chunks": [str(chunks)], "paper_key": "doc_id", "alias_glossary": str(gl),
                             "expected_chunks": len(rows)}},
           "alias_pool": {"seed_base": "test", **pool_over},
           "drafter": {"base_url": "http://127.0.0.1:18999/v1", "empty_retry_s": 0.0, "inflight": 1},
           "verifier": {"cwd": str(tmp / "vcwd")}, "limits": {"rate_backoffs_s": [0.0, 0.0, 0.0]}}
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg))
    apool.build(cfg, "T", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg


def jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def corpus_idx(tmp: Path) -> tuple[g.Corpus, ck.PaperIndex]:
    _, cfg = world(tmp)
    c = g.build_corpus(g.read_chunks(cfg["corpora"]["T"]["chunks"]), "doc_id")
    return c, ck.PaperIndex(c, 200)


# ------------------------------------------------------------------ matchers

def test_alias_matchers() -> None:
    assert ak.alias_in_text("ACH", "The ACH fell.") and not ak.alias_in_text("ACH", "The ACHE fell.")
    assert not ak.alias_in_text("ACH", "the ach fell")                     # the chunk matcher is case-sensitive
    assert ak.alias_in_text("Qv", "raising Qv") and ak.alias_in_text("Qv", "raising Q_v")
    # the glossary builder's matcher: hyphen / apostrophe split, an abbreviation's plural
    for t in ("MSC-derived cells", "the MSC's niche", "MSCs grew", "MSC/MSCs"):
        assert ak.alias_in_text("MSC", t, "abbreviation") and ak.alias_in_text("MSC", t, "both"), t
    assert not ak.alias_in_text("MSC", "MSCs grew", "symbol")
    assert ak.alias_in_text("ACH", "The ACH fell.") == alias_abbrev.alias_occurs("ACH", "symbol", "The ACH fell.")
    for q in ("What is ACH?", "what is ach?", "What is A.C.H.?", "What is A C H?", "Are ACHs high?",
              "What is the A-C-H?"):
        assert ak.alias_variant_in("ACH", q, "abbreviation"), q
    for q in ("raising Qv", "raising qv", "raising Q_v", "raising Q v", "raising Q_{v}", "raising Qᵥ"):
        assert ak.alias_variant_in("Qv", q, "symbol"), q
    assert not ak.alias_variant_in("ACH", "Does it ache?", "abbreviation")
    assert not ak.alias_variant_in("Qv", "raising Qvs", "symbol")          # no plural for a symbol
    assert ak.alias_variant_in("AS", "the AS level", "abbreviation")
    assert not ak.alias_variant_in("AS", "as high as the level", "abbreviation")   # stop word: case-sensitive
    assert ak.alias_variant_in("a", "Is a fan used?", "symbol")            # strict: the pool drops such aliases
    # the question guard is never weaker than the chunk matcher, and catches case variants and plurals
    for q in ("Is MSC-based therapy safe?", "What is the MSC's role?", "Do Mscs grow?", "do mscs grow?",
              "What do MSC's do?", "What is msc?"):
        for kind in ("abbreviation", "both"):
            assert ak.alias_in_question("MSC", q, kind), (q, kind)
    assert ak.alias_in_question("Qv", "raising Q_v", "symbol") and not ak.alias_in_question("Qv", "Qvs", "symbol")
    assert not ak.alias_in_question("AS", "as high as the level", "abbreviation")

def test_primary_rules() -> None:
    assert ak.chunk_has_primary("air change rate", "Staff change shifts.")            # strict: one content word
    assert not ak.chunk_has_primary("air change rate", "The ACH changed little.")     # exact token
    assert ak.chunk_has_primary("air flow", "the air flow was low")                   # no >=5 word: literal
    assert not ak.chunk_has_primary("air flow", "the air was low, the flow high")
    assert ak.primary_literal_in("air change rate", "The air-change rate was high")
    assert ak.primary_literal_in("air change rate", "air change rates")            # lemmatised (alias_abbrev)
    assert not ak.primary_literal_in("air change rate", "air and change rate")
    with pytest.raises(ValueError):
        ak.chunk_has_primary("air change rate", "x", 4)                                # one strict rule
    assert ak.primary_in_question("air change rate", "How did air change rates differ between wards?")
    assert ak.primary_in_question("outdoor airflow", "What lowered carbon dioxide when outdoor airflows rose?")
    assert not ak.primary_in_question("air change rate", "How did the ventilation rate differ?")
    assert not ak.primary_in_question("of the", "what of it")                         # no content word, not literal


@pytest.mark.parametrize("q,bad", [
    ("What did this study find about the air change rate?", True),
    ("In the present study, how was the air change rate measured?", True),
    ("How did the authors measure the air change rate?", True),
    ("What does the proposed method assume about airflow?", True),
    ("How did we measure the air change rate?", True),
    ("What did our wards show?", True),
    ("In this work, what was the air change rate?", True),
    ("What does Figure 3 show about the air change rate?", True),
    ("Which value in Table 2 is the air change rate?", True),
    ("What is defined in Eq. (4)?", True),
    ("What is described in Section 2.1 about air change rates?", True),
    ("What does the table show about the air change rate?", True),
    ("Which air change rate was described above?", True),
    ("What does the study report for the air change rate?", True),
    ("What does the supplementary material list?", True),
    ("Let us know: what was the air change rate?", True),
    ("What is the air change rate in US hospital wards?", False),
    ("What does the study of indoor air say about the air change rate?", False),
    ("How does the air change rate depend on wind direction?", False),
    ("Which tables of values are used for air change rates in hospitals?", False),
])
def test_standalone(q: str, bad: bool) -> None:
    assert bool(ak.self_references(q)) is bad, ak.self_references(q)


# ------------------------------------------------------------------ layer 1

G_ENTRY = {"alias": "ACH", "primary": "air change rate", "alias_kind": "abbreviation"}
C_ENTRY = G_ENTRY
GOOD_G = {"question": "How low did the air change rate fall once the windows were shut?",
          "answer": "2.1 per hour", "quote": "The ACH fell to 2.1 per hour"}
GOOD_C = {"question": "Which outside factor did the air change rate depend on strongly?",
          "answer": "the wind direction", "quote": "The air change rate depended strongly on the wind direction"}


@pytest.mark.parametrize("kind,chunk,change,code", [
    ("G", "d1-c1", {}, None),
    ("C", "d1-c4", {}, None),
    ("G", "d1-c1", {"question": "How low did ACH fall once the windows were shut?"}, "alias_in_question"),
    ("G", "d1-c1", {"question": "How low did the ach fall once the windows were shut, air change rate?"},
     "alias_in_question"),
    ("G", "d1-c1", {"question": "How low did the ventilation fall once the windows were shut?"},
     "primary_not_in_question"),
    ("G", "d1-c1", {"quote": "fell to 2.1 per hour when"}, "quote_lacks_alias"),
    ("G", "d1-c9", {"quote": "Staff change shifts at seven", "answer": "seven"}, "gold_has_primary"),
    ("G", "d1-c1", {"quote": "The ACH fell to 9.9 per hour"}, "quote_not_in_paper"),
    ("G", "d1-c1", {"quote": "The ACH rose to 16 per hour", "answer": "16 per hour"}, "quote_not_in_paper"),
    ("G", "d1-c1", {"quote": "in block 1"}, "quote_not_unique"),
    ("G", "d1-c2", {}, "quote_not_in_source"),
    ("G", "d1-c1", {"answer": "eleven cubic metres"}, "answer_not_in_quote"),
    ("G", "d1-c1", {"answer": ""}, "answer_missing"),
    ("G", "d1-c1", {"question": ""}, "question_missing"),
    ("G", "d1-c1", {"question": "Air change rate?"}, "question_too_short"),
    ("G", "d1-c1", {"question": "How low " + "very " * 40 + "did the air change rate fall?"}, "question_too_long"),
    ("G", "d1-c1", {"question": "How low did the air change rate fall? And when did it rise?"},
     "multiple_questions"),
    ("G", "d1-c1", {"question": "In this study, how low did the air change rate fall with windows shut?"},
     "not_standalone"),
    ("G", "d1-c1", {"quote": "The ACH fell to 2.1 per hour " * 12}, "quote_too_long"),
    ("C", "d1-c4", {"question": "Which outside factor did the ventilation depend on strongly?"},
     "term_not_in_question"),
    ("C", "d1-c4", {"quote": "depended strongly on the wind direction"}, "term_not_in_quote"),
    ("C", "d1-c1", {"quote": "The ACH fell to 2.1 per hour", "answer": "2.1 per hour"}, "term_not_in_chunk"),
])
def test_layer1_codes(tmp_path: Path, kind: str, chunk: str, change: dict[str, Any], code: str | None) -> None:
    c, idx = corpus_idx(tmp_path)
    draft = {**(GOOD_G if kind == "G" else GOOD_C), **change}
    out = ak.layer1(draft, None, kind=kind, entry=G_ENTRY, paper="d1", source_chunk=c.chunks[chunk], idx=idx,
                    cfg={})
    if code is None:
        assert out["verdict"] == "pass", out["reasons"]
    else:
        assert out["verdict"] == "reject" and code in out["reasons"], out["reasons"]
    assert out["record_stratum"] == kind


def test_layer1_parse_and_leak(tmp_path: Path) -> None:
    c, idx = corpus_idx(tmp_path)
    kw = {"kind": "G", "entry": G_ENTRY, "paper": "d1", "source_chunk": c.chunks["d1-c1"], "idx": idx}
    assert ak.layer1(None, "json", cfg={}, **kw)["reasons"] == ["unparseable"]
    assert ak.layer1({"none": True}, None, cfg={}, **kw)["reasons"] == ["no_answer"]
    assert "no_answer" in ak.REDRAW_EXEMPT
    small = ck.PaperIndex(c, 0)                                             # nothing is "frequent"
    leak = {**GOOD_G, "question": "Why the air change rate fell to 2.1 per hour?"}
    out = ak.layer1(leak, None, cfg={}, **{**kw, "idx": small})
    assert "lexical_leak" in out["reasons"] and out["leak_overlap"] > 0.5
    ok = ak.layer1(GOOD_G, None, cfg={}, **{**kw, "idx": small})          # term words are excused
    assert "lexical_leak" not in ok["reasons"], ok
    for raw, want in (('{"none": true}', {"none": True}), ('{"question": 1, "answer": "a", "quote": "q"}', None),
                      ('[{"question": "a"}]', None), ('```json\n{"question": "a", "answer": "b", "quote": "c"}\n```',
                                                         {"question": "a", "answer": "b", "quote": "c"})):
        assert ak.parse_draft(raw)[0] == want


# ------------------------------------------------------------------ pool

def test_pool_caps_waves_and_yield(tmp_path: Path) -> None:
    _, cfg = world(tmp_path, n_papers=3, g_per_paper=5, c_per_paper=2)
    out = Path(cfg["out_dir"])
    rows = jsonl(out / "pool-alias-T.jsonl")
    header, jobs = rows[0], rows[1:]
    assert header["schema"] == apool.POOL_SCHEMA and header["eval_papers"] == ["d1", "d2", "d3"]
    for p in ("d1", "d2", "d3"):
        gj = [j for j in jobs if j["paper"] == p and j["stratum"] == "G"]
        cj = [j for j in jobs if j["paper"] == p and j["stratum"] == "C"]
        assert len(gj) == 5 and len(cj) == 2
        assert [j["rank"] for j in gj] == list(range(5)) and [j["wave"] for j in gj] == [0, 0, 1, 1, 2]
        assert [j["wave"] for j in cj] == [0, 1]
        cids = {j["chunk_id"] for j in gj}
        assert len(cids) == 5 and not cids & {f"{p}-c0", f"{p}-c5", f"{p}-c4", f"{p}-c9"}  # def / recheck
        # C: the term literally (alias_abbrev's C rule; the definition chunk is not excluded)
        assert {j["chunk_id"] for j in cj} <= {f"{p}-c0", f"{p}-c4", f"{p}-c5", f"{p}-c8"}
        # the first two G jobs spread over both aliases (round-robin over entries)
        assert {j["alias"] for j in gj[:2]} == {"ACH", "Qv"}
    m = json.loads((out / "pool-manifest-alias-T.json").read_text())
    assert m["counts"]["G_candidates"] == 3 * 6 and m["counts"]["G_def_chunk_excluded"] == 0
    assert m["counts"]["C_candidates"] == 3 * 4
    y = m["yield"]
    exp = 3 * (apool.binom_expected_min(5, 0.35, 2) + apool.binom_expected_min(2, 0.35, 1))
    assert y["expected_yield"] == round(exp / 9, 4) and y["status"] == "ADEQUATE" and y["calibrated"] is False
    assert y["by_kind"]["G"]["papers_reachable"] == 3 and y["by_kind"]["G"]["required_rate_max"] == 2 / 5
    # seeded: a rebuild is byte-identical in its jobs
    apool.build(cfg, "T", out, provenance=lambda: PROV)
    assert jsonl(out / "pool-alias-T.jsonl")[1:] == jobs


def test_pool_yield_gate_and_eligibility(tmp_path: Path) -> None:
    _, cfg = world(tmp_path, n_papers=2, yield_calibration={"G": 0.05, "C": 0.05}, min_c_candidates=5)
    m = json.loads((Path(cfg["out_dir"]) / "pool-manifest-alias-T.json").read_text())
    assert m["counts"]["papers_eligible"] == 0 and m["yield"]["status"] == "YIELD-NOT-ADEQUATE"
    _, cfg = world(tmp_path / "b", n_papers=2, yield_calibration={"G": 0.05, "C": 0.05})
    m = json.loads((Path(cfg["out_dir"]) / "pool-manifest-alias-T.json").read_text())
    assert m["yield"]["calibrated"] is True and m["yield"]["status"] == "YIELD-NOT-ADEQUATE"
    assert apool.binom_expected_min(6, 1.0, 2) == pytest.approx(2.0)
    assert apool.binom_expected_min(2, 0.5, 1) == pytest.approx(0.75)
    with pytest.raises(SystemExit):
        apool.pool_config({"alias_pool": {"yield_gate": 2}})
    with pytest.raises(SystemExit):
        apool.corpus_config({"corpora": {}}, "../x")


# ------------------------------------------------------------------ the drafter chain

class Resp:
    def __init__(self, status: int, payload: dict[str, Any] | None = None, text: str | None = None) -> None:
        self.status_code, self._p = status, payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self) -> dict[str, Any]:
        if self._p is None:
            raise ValueError("not json")
        return self._p


def ok_resp(content: str) -> Resp:
    return Resp(200, {"model": SERVED, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


def fake_draft(messages: list[dict[str, str]], bad_jobs: set[str] = frozenset()) -> dict[str, Any]:  # type: ignore[assignment]
    user = messages[-1]["content"]
    body = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0].strip()
    term = user.split("TERM: ", 1)[1].split("\n", 1)[0]
    if any(b in body for b in bad_jobs):
        return {"question": "What did this study find?", "answer": "x", "quote": body}
    where = "hospital ventilation" if "SHORT FORM" in user else "hospital wards"   # G and C differ
    return {"question": f"What is stated about the {term} in {where}?",
            "answer": " ".join(body.rstrip(". ").split()[-4:]), "quote": body}


class Gateway:
    def __init__(self, respond: Any = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.respond = respond or (lambda body, n: ok_resp(json.dumps(fake_draft(body["messages"]))))

    async def post(self, url: str, json: dict[str, Any]) -> Resp:  # noqa: A002
        self.bodies.append(json)
        await asyncio.sleep(0)
        return self.respond(json, len(self.bodies))

    async def aclose(self) -> None:
        pass


class AliasCLI:
    def __init__(self, reject_if: str | None = None) -> None:
        self.reject_if, self.calls = reject_if, []

    def __call__(self, cmd: list[str], *, input: str, capture_output: bool, text: bool, cwd: str,  # noqa: A002
                 timeout: int) -> Any:
        self.calls.append({"cmd": cmd, "input": input})
        v = dict.fromkeys(ak.VERIFY_KEYS, True)
        if self.reject_if and self.reject_if in input:
            v["standalone"] = False
        out = {"result": json.dumps({**v, "reason": "ok"}), "is_error": False, "total_cost_usd": 0.0,
               "modelUsage": {"claude-opus-5-5": {"inputTokens": 400, "outputTokens": 20}}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")


def run(path: Path, *args: str, gw: Any = None, cli: Any = None) -> int:
    return ad.main(["--private-config", str(path), *args], provenance=lambda: PROV,
                   http_factory=(lambda: gw) if gw else None, cli_runner=cli)


def chain(path: Path, gw: Gateway, cli: Any = None, *extra: str) -> None:
    assert run(path, "preflight", "--stage", "draft", "--set", "T", *extra) == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", *extra, gw=gw) == 0
    assert run(path, "check", "--set", "T", *extra) == 0
    assert run(path, "preflight", "--stage", "verify", "--set", "T", *extra) == 0
    assert run(path, "verify", "--set", "T", "--notice-acknowledged", *extra, cli=cli or AliasCLI()) == 0
    assert run(path, "summary", "--set", "T", *extra) == 0


def test_chain_stop_rule_skips_later_waves(tmp_path: Path) -> None:
    path, cfg = world(tmp_path)
    out = Path(cfg["out_dir"])
    gw = Gateway()
    cli = AliasCLI()
    chain(path, gw, cli)
    # wave 0 = 2 G + 1 C per paper, all pass; waves 1-2 are skipped by the stop rule
    assert len(gw.bodies) == 3 * 3
    for b in gw.bodies:
        assert b["fallback_model"] == "" and b["max_tokens"] > 0
    g_bodies = [b for b in gw.bodies if "SHORT FORM" in b["messages"][-1]["content"]]
    assert len(g_bodies) == 6 and all(b["messages"][0]["content"] == ak.DRAFT_SYSTEM_G for b in g_bodies)
    acc = jsonl(out / "accepted-alias-T.jsonl")
    assert len(acc) == 9 and {a["kind"] for a in acc} == {"G", "C"}
    for a in acc:
        assert a["binding_terms"] == [a["primary"]] and a["pilot"] is False and a["verifier"] == "claude"
        assert a["gold_chunks"] == [a["source_chunk_id"]]
        if a["kind"] == "G":
            assert ak.alias_in_text(a["alias"], a["quote"]) and not ak.alias_variant_in(a["alias"], a["question"])
    s = json.loads((out / "acceptance-summary-alias-T.json").read_text())
    assert s["alias"]["jobs_skipped_by_stop_rule"] == {"G": 3 * 4, "C": 3}
    assert s["alias"]["papers_satisfied_accepted"] == {"G": 3, "C": 3}
    # the verifier gets the alias contract, the definition passage for G
    assert all(c["cmd"][-1] == ak.VERIFY_SYSTEM for c in cli.calls)
    assert any("DEFINITION PASSAGE" in c["input"] for c in cli.calls)
    st = json.loads((out / "draft-state-alias-T.json").read_text())
    assert st["status"] == "complete" and st["yield_watch"]["status"] == "not_reached"


def test_chain_later_wave_runs_for_short_papers_and_l2_reject(tmp_path: Path) -> None:
    path, cfg = world(tmp_path, n_papers=1)
    out = Path(cfg["out_dir"])
    pool = jsonl(out / "pool-alias-T.jsonl")[1:]
    first_g = [j for j in pool if j["stratum"] == "G" and j["wave"] == 0]
    bad = Path(cfg["corpora"]["T"]["chunks"][0])
    texts = {json.loads(x)["id"]: json.loads(x)["content"] for x in bad.read_text().splitlines()}
    bad_text = {texts[first_g[0]["chunk_id"]].strip()}
    gw = Gateway(lambda body, n: ok_resp(json.dumps(fake_draft(body["messages"], bad_text))))
    chain(path, gw, AliasCLI(reject_if="windows"))
    drafts = jsonl(out / "draft-alias-T.jsonl")
    # the bad G job is redrawn once (not_standalone) and fails; wave 1 drafts one more G for the paper
    assert sum(d["job_id"] == first_g[0]["job_id"] for d in drafts) == 2
    assert {d["wave"] for d in drafts if d["stratum"] == "G"} == {0, 1}
    log = jsonl(out / "acceptance-alias-T.jsonl")
    assert any("not_standalone" in r["reasons"] for r in log if r["layer"] == "l1")
    s = json.loads((out / "acceptance-summary-alias-T.json").read_text())
    # wave 0 left the paper one G pass short: wave 1 drafted both its G jobs, wave 2 none
    assert s["alias"]["jobs_drafted"]["G"] == 4 and s["alias"]["jobs_skipped_by_stop_rule"]["G"] == 2


def test_pilot_own_files(tmp_path: Path) -> None:
    path, cfg = world(tmp_path, n_papers=3)
    out = Path(cfg["out_dir"])
    chain(path, Gateway(), None, "--pilot", "4", "--papers", "2")
    drafts = jsonl(out / "draft-alias-T-pilot.jsonl")
    assert 0 < len({d["job_id"] for d in drafts}) <= 4 and len({d["paper"] for d in drafts}) <= 2
    acc = jsonl(out / "accepted-alias-T-pilot.jsonl")
    assert acc and all(a["pilot"] is True for a in acc)
    assert not (out / "accepted-alias-T.jsonl").exists()


def test_yield_gate_refuses_low_pool(tmp_path: Path) -> None:
    path, cfg = world(tmp_path, yield_calibration={"G": 0.01, "C": 0.01})
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", gw=Gateway()) == dr_hard()
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", "--allow-low-yield", gw=Gateway()) == 0


def dr_hard() -> int:
    return ad.EXIT_HARD_STOP


# ------------------------------------------------------------------ the observed-calls counter

def _with(path: Path, **blocks: Any) -> None:
    cfg = json.loads(path.read_text())
    for k, v in blocks.items():
        cfg[k] = {**(cfg.get(k) or {}), **v}
    path.write_text(json.dumps(cfg))


def test_max_calls_pauses_mid_batch(tmp_path: Path) -> None:
    path, cfg = world(tmp_path)
    _with(path, limits={"max_calls": 4})
    gw = Gateway()
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", gw=gw) == ad.EXIT_PAUSED
    assert len(gw.bodies) == 4
    st = json.loads((Path(cfg["out_dir"]) / "draft-state-alias-T.json").read_text())
    assert st["status"] == "paused" and st["reason"].startswith("max_calls")
    # the counter is per segment: a resume after a fresh preflight drafts on
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", "--resume", gw=gw) == ad.EXIT_PAUSED
    assert len(gw.bodies) == 8


def test_notice_overrun_caps_at_notice_plus_share(tmp_path: Path) -> None:
    path, cfg = world(tmp_path)
    _with(path, limits={"notice_overrun": 0.10}, drafter={"attempts_factor": 0.1})
    gw = Gateway()
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    pf = json.loads((Path(cfg["out_dir"]) / "preflight-draft-alias-T.json").read_text())
    import math
    cap = math.ceil(pf["plan"]["calls"] * 1.1)
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", gw=gw) == ad.EXIT_PAUSED
    assert cap < 9 and len(gw.bodies) == cap


# ------------------------------------------------------------------ the budget stop

def _budget_run(tmp: Path, respond: Any) -> tuple[Path, dict[str, Any], Gateway, int]:
    path, cfg = world(tmp)
    gw = Gateway(respond)
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    rc = run(path, "draft", "--set", "T", "--notice-acknowledged", gw=gw)
    return path, cfg, gw, rc


def test_budget_stop_terse_notice_then_resume(tmp_path: Path) -> None:
    notice = "Usage notice: budget reached. key=sk-abcdef1234567890 Bearer tok123 " + "x" * 300

    def respond(body: dict[str, Any], n: int) -> Resp:
        return Resp(503, None, notice) if n == 2 else ok_resp(json.dumps(fake_draft(body["messages"])))
    path, cfg, gw, rc = _budget_run(tmp_path, respond)
    out = Path(cfg["out_dir"])
    assert rc == ad.EXIT_BUDGET == 76
    assert len(gw.bodies) == 2                                      # not retried, nothing issued after
    assert (out / "PAUSE").exists()
    ev = json.loads((out / "budget-stop-alias-T.json").read_text())
    assert ev["http_status"] == 503 and ev["why"] == "notice" and len(ev["body"]) <= 200
    assert "sk-abcdef" not in ev["body"] and "tok123" not in ev["body"] and "[redacted]" in ev["body"]
    st = json.loads((out / "draft-state-alias-T.json").read_text())
    assert st["status"] == "paused" and st["reason"].startswith("budget_stop")
    drafts = jsonl(out / "draft-alias-T.jsonl")
    assert len(drafts) == 1 and not any(d["status"] == "empty_twice" for d in drafts)
    calls = jsonl(out / "draft-calls-alias-T.jsonl")
    assert any(c["status"] == "budget_stop:503" for c in calls)
    # resume: PAUSE still there -> pauses at once; removed + fresh preflight -> completes
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", "--resume", gw=Gateway()) == ad.EXIT_PAUSED
    (out / "PAUSE").unlink()
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    gw2 = Gateway()
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", "--resume", gw=gw2) == 0
    assert len(gw2.bodies) == 8 and len(jsonl(out / "draft-alias-T.jsonl")) == 9


NOTICE = json.dumps({"error": {"type": "provider_notice", "message": "usage notice"}})
TRANSIENT = json.dumps({"error": {"type": "provider_error", "message": "upstream unavailable"}})


def test_budget_stop_503_provider_notice(tmp_path: Path) -> None:
    _, cfg, gw, rc = _budget_run(tmp_path, lambda body, n: Resp(503, None, NOTICE))
    assert rc == ad.EXIT_BUDGET and len(gw.bodies) == 1
    ev = json.loads((Path(cfg["out_dir"]) / "budget-stop-alias-T.json").read_text())
    assert ev["why"] == "provider_notice" and ev["http_status"] == 503


def test_budget_stop_three_untyped_503s(tmp_path: Path) -> None:
    _, cfg, gw, rc = _budget_run(tmp_path, lambda body, n: Resp(503, None, "<html>Service Unavailable"))
    assert rc == ad.EXIT_BUDGET and len(gw.bodies) == 3
    ev = json.loads((Path(cfg["out_dir"]) / "budget-stop-alias-T.json").read_text())
    assert ev["why"] == "503_x3_untyped"


@pytest.mark.parametrize("first", [Resp(503, None, ""), Resp(503, None, TRANSIENT),
                                   Resp(502, None, TRANSIENT), Resp(500, None, TRANSIENT)])
def test_transient_errors_take_the_failed_call_path(tmp_path: Path, first: Resp) -> None:
    def respond(body: dict[str, Any], n: int) -> Resp:
        return first if n <= 2 else ok_resp(json.dumps(fake_draft(body["messages"])))
    _, cfg, gw, rc = _budget_run(tmp_path, respond)
    out = Path(cfg["out_dir"])
    # two failed calls (two untyped 503s in a row are below the stop), re-issued on the next pass
    assert rc == 0 and len(gw.bodies) == 11 and not (out / "budget-stop-alias-T.json").exists()
    calls = jsonl(out / "draft-calls-alias-T.jsonl")
    assert sum(c["status"].startswith("error:http_") for c in calls) == 2


@pytest.mark.parametrize("status", [503, 502, 500])
def test_typed_provider_error_mentioning_quota_is_not_a_budget_stop(tmp_path: Path, status: int) -> None:
    body = json.dumps({"error": {"type": "provider_error", "message": "upstream quota / rate limit hiccup"}})
    _, cfg, gw, rc = _budget_run(tmp_path, lambda b, n: Resp(status, None, body))
    out = Path(cfg["out_dir"])
    assert rc == ad.EXIT_PAUSED and not (out / "budget-stop-alias-T.json").exists() and len(gw.bodies) == 5


def test_provider_error_503_never_stops_for_budget(tmp_path: Path) -> None:
    _, cfg, gw, rc = _budget_run(tmp_path, lambda body, n: Resp(503, None, TRANSIENT))
    out = Path(cfg["out_dir"])
    assert rc == ad.EXIT_PAUSED and not (out / "budget-stop-alias-T.json").exists() and not (out / "PAUSE").exists()
    assert json.loads((out / "draft-state-alias-T.json").read_text())["reason"].startswith("failures_5_in_a_row")


@pytest.mark.parametrize("resp,why", [
    (Resp(429, None, ""), "429"),
    (Resp(429, None, TRANSIENT), "429"),
    (Resp(502, None, NOTICE), "provider_notice"),
    (Resp(400, None, '{"error": "quota exceeded for this key"}'), "notice"),
    (ok_resp("Your usage limit has been reached. Try again later."), "notice_in_reply"),
])
def test_budget_stop_other_shapes(tmp_path: Path, resp: Resp, why: str) -> None:
    _, cfg, gw, rc = _budget_run(tmp_path, lambda body, n: resp)
    assert rc == ad.EXIT_BUDGET and len(gw.bodies) == 1
    assert json.loads((Path(cfg["out_dir"]) / "budget-stop-alias-T.json").read_text())["why"] == why


def test_502_keeps_the_failed_call_behaviour(tmp_path: Path) -> None:
    _, cfg, gw, rc = _budget_run(tmp_path, lambda body, n: Resp(502, None, "Bad Gateway"))
    out = Path(cfg["out_dir"])
    assert rc == ad.EXIT_PAUSED and not (out / "budget-stop-alias-T.json").exists() and not (out / "PAUSE").exists()
    st = json.loads((out / "draft-state-alias-T.json").read_text())
    assert st["reason"].startswith("failures_5_in_a_row")
    assert len(gw.bodies) == 5                                      # one request per failed call, no in-place retry


def test_error_type_parser() -> None:
    assert ad.error_type(NOTICE) == "provider_notice" and ad.error_type(TRANSIENT) == "provider_error"
    for t in ("", "<html>", '{"error": "x"}', '{"error": {"type": 3}}', "[]"):
        assert ad.error_type(t) is None


def test_redact() -> None:
    assert ad.redact("token: abc123 ok", 200) == "token: [redacted] ok"
    assert len(ad.redact("y " * 500)) == 200


def test_pool_drops_one_letter_word_alias(tmp_path: Path) -> None:
    _, cfg = world(tmp_path, n_papers=1)
    gl = Path(cfg["corpora"]["T"]["alias_glossary"])
    gl.write_text(gl.read_text() + json.dumps({"doc_id": "d1", "alias": "a", "primary": "absorptivity",
                                               "kind": "symbol", "def_chunk_id": None,
                                               "use_chunk_ids": ["d1-c1"]}) + "\n")
    m = apool.build(cfg, "T", Path(cfg["out_dir"]), provenance=lambda: PROV)
    assert m["counts"]["entry_alias_is_a_word"] == 1 and m["counts"]["entries"] == 3


def test_config_template_is_accepted() -> None:
    import glossbench_r2_draft as dr

    tpl = json.loads((_EVAL / "examples" / "glossbench-alias-config.example.json").read_text())
    m = dr.merged_config(tpl)
    assert m["drafter"]["max_tokens"] == 16384 and m["verifier"]["inflight"] <= 3 and m["verifier"]["codex_fallback"]
    assert m["limits"]["notice_overrun"] == 0.10
    apool.pool_config(tpl)
    assert set(tpl["alias_checks"]) <= set(ak.CHECK_DEFAULTS)
    import glossbench_alias_waves as wv
    assert wv.waves_config(tpl["waves"]) == wv.WAVE_DEFAULTS


# ------------------------------------------------------------------ one matcher (review of #888)

TRICKY = [
    "Mesenchymal stromal cells (MSCs) were isolated from marrow of donor {i}. ",        # def MSC
    "MSC-derived vesicles reduced the lesion size in mouse {i}. ",                      # G (hyphen)
    "The MSC's secretome was profiled by mass spectrometry in batch {i}. ",            # G (possessive)
    "Passaged MSCs lost their potency after {i}0 days. ",                               # G (plural)
    "Stromal markers were absent in culture {i}. ",                                     # no G: 'stromal' (>= 5)
    "The msc gene is unrelated to anything in sample {i}. ",                            # no G: case
    "Mesenchymal stromal cell expansion took {i} weeks in the bioreactor. ",           # C
    "Raising Qv lowered the carbon dioxide level in hall {i}. ",                        # G (symbol, no def)
    "The outdoor airflows were measured in hall {i}. ",                                 # C (lemmatised plural)
]


def tricky_world(tmp: Path, n_papers: int = 3) -> tuple[dict[str, Any], g.Corpus]:
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows, entries = [], []
    for i in range(1, n_papers + 1):
        doc, s = f"m{i}", 0
        for n, t in enumerate(TRICKY):
            p = t.format(i=i)
            rows.append({"id": f"{doc}-c{n}", "doc_id": doc, "content": p, "start_char": s, "end_char": s + len(p),
                         "metadata": {"title": f"Stromal study {i}"}})
            s += len(p)
        entries.append({"doc_id": doc, "alias": "MSC", "primary": "Mesenchymal stromal cells", "kind": "abbreviation",
                        "def_chunk_id": f"{doc}-c0", "def_chunk_ids": [f"{doc}-c0"], "use_chunk_ids": []})
        entries.append({"doc_id": doc, "alias": "Qv", "primary": "outdoor airflow", "kind": "both"})
    chunks = data / "chunks.jsonl"
    chunks.write_text("".join(json.dumps(r) + "\n" for r in rows))
    gl = data / "merged_glossary.jsonl"
    gl.write_text("".join(json.dumps(e) + "\n" for e in entries))
    cfg = {"out_dir": str(tmp / "out"),
           "corpora": {"T": {"chunks": [str(chunks)], "paper_key": "doc_id", "alias_glossary": str(gl)}},
           "alias_pool": {"seed_base": "test", "min_c_candidates": 1}}
    apool.build(cfg, "T", g.out_dir(cfg), provenance=lambda: PROV)
    return cfg, g.build_corpus(g.read_chunks([str(chunks)]), "doc_id")


@pytest.mark.parametrize("which", ["simple", "tricky"])
def test_every_pool_job_passes_the_alias_and_primary_checks_on_its_own_chunk(tmp_path: Path, which: str) -> None:
    if which == "simple":
        _, cfg = world(tmp_path)
        c = g.build_corpus(g.read_chunks(cfg["corpora"]["T"]["chunks"]), "doc_id")
    else:
        cfg, c = tricky_world(tmp_path)
    idx = ck.PaperIndex(c, 0)
    jobs = jsonl(Path(cfg["out_dir"]) / "pool-alias-T.jsonl")[1:]
    kinds = {j["stratum"] for j in jobs}
    assert kinds == {"G", "C"}
    if which == "tricky":   # the hyphen, possessive, plural and the symbol without a definition all made it
        assert {j["chunk_id"].split("-")[1] for j in jobs if j["stratum"] == "G"} == {"c1", "c2", "c3", "c7"}
    for j in jobs:
        ch = c.chunks[j["chunk_id"]]
        # a draft whose quote is the whole chunk and whose question names the term (never the alias)
        draft = {"question": f"What is reported about {j['primary']} in hospital settings?",
                 "answer": ch.content.split()[-2], "quote": ch.content.strip()}
        out = ak.layer1(draft, None, kind=j["stratum"], entry=j, paper=j["paper"], source_chunk=ch, idx=idx, cfg={})
        bad = {"quote_lacks_alias", "alias_in_question", "gold_has_primary", "primary_not_in_question",
               "term_not_in_question", "term_not_in_quote", "term_not_in_chunk"} & set(out["reasons"])
        assert not bad, (j["chunk_id"], j["alias"], out["reasons"])


def test_pool_g_candidates_are_the_matchers_definition(tmp_path: Path) -> None:
    """The pool's G candidates = exactly the chunks the layer-1 matchers accept (no second definition)."""
    cfg, c = tricky_world(tmp_path)
    jobs = jsonl(Path(cfg["out_dir"]) / "pool-alias-T.jsonl")[1:]
    gl = [json.loads(x) for x in Path(cfg["corpora"]["T"]["alias_glossary"]).read_text().splitlines()]
    for e in gl:
        want = {cid for cid, ch in c.chunks.items() if ch.doc_id == e["doc_id"]
                and ak.alias_in_text(e["alias"], ch.content, e["kind"])
                and not ak.chunk_has_primary(e["primary"], ch.content)}
        got = {j["chunk_id"] for j in jobs if j["stratum"] == "G" and j["alias"] == e["alias"]
               and j["doc_id"] == e["doc_id"]}
        assert got <= want and want, (e, got, want)
    m = json.loads((Path(cfg["out_dir"]) / "pool-manifest-alias-T.json").read_text())
    assert m["counts"]["G_candidates"] == 3 * 4


def test_expected_drafted_and_preflight_reflect_the_stop_rule(tmp_path: Path) -> None:
    assert apool.expected_drafted([2, 2, 2], 2, 1.0) == pytest.approx(2.0)
    assert apool.expected_drafted([2, 2, 2], 2, 0.0) == pytest.approx(6.0)
    assert apool.expected_drafted([1, 1], 1, 0.5) == pytest.approx(1.5)
    assert apool.expected_drafted([2, 2], 2, 0.3, have=2) == 0.0
    path, cfg = world(tmp_path)
    _with(path, alias={"l1_pass_rate": {"G": 0.5, "C": 0.5}})
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    plan = json.loads((Path(cfg["out_dir"]) / "preflight-draft-alias-T.json").read_text())["plan"]
    exp = 3 * (apool.expected_drafted([2, 2, 2], 2, 0.5) + apool.expected_drafted([1, 1], 1, 0.5))
    import math
    assert plan["jobs_planned"] == 24 and plan["jobs_expected"] == round(exp, 2)
    assert plan["calls"] == math.ceil(exp * 1.3) < plan["calls_if_no_stop_rule"] == math.ceil(24 * 1.3)
    assert plan["calls_upper_bound"] == 24 * 2 * 2 + 20 and plan["stop_rule"]["rate_source"] == "alias.l1_pass_rate"
    assert plan["max_output_tokens"] == plan["calls_upper_bound"] * plan_max_tokens(cfg)


def plan_max_tokens(cfg: dict[str, Any]) -> int:
    import glossbench_r2_draft as dr
    return int(dr.merged_config(cfg)["drafter"]["max_tokens"])


def test_pilot_spreads_over_papers_with_caps_and_reports_realised_calls(tmp_path: Path) -> None:
    path, cfg = world(tmp_path, n_papers=14)
    out = Path(cfg["out_dir"])
    assert run(path, "preflight", "--stage", "draft", "--set", "T", "--pilot", "50", "--papers", "12") == 0
    pf = json.loads((out / "preflight-draft-alias-T-pilot.json").read_text())["plan"]
    pp = pf["pilot"]["plan"]
    assert pp["papers"] == 12 and pp["papers_with_jobs"] == 12 and pp["jobs_per_paper"] == [4, 5]
    assert sum(pp["jobs_planned"].values()) == 50 == pf["jobs_planned"] and set(pp["jobs_planned"]) == {"G", "C"}
    gw = Gateway()
    chain(path, gw, None, "--pilot", "50", "--papers", "12")
    s = json.loads((out / "acceptance-summary-alias-T-pilot.json").read_text())
    r = s["alias"]["realised"]
    assert r["gateway_calls"] == len(gw.bodies) == 36 and s["alias"]["papers"] == 12    # wave 0 only: all pass
    assert r["by_kind"]["G"]["l1_pass_rate"] == 1.0 and r["latest_notice"]["calls"] == pf["calls"]


def test_resume_does_not_double_count_calls(tmp_path: Path) -> None:
    def respond(body: dict[str, Any], n: int) -> Resp:
        return Resp(503, None, NOTICE) if n == 3 else ok_resp(json.dumps(fake_draft(body["messages"])))
    path, cfg, gw, rc = _budget_run(tmp_path, respond)
    out = Path(cfg["out_dir"])
    assert rc == ad.EXIT_BUDGET and (out / "PAUSE").exists()
    st = json.loads((out / "draft-state-alias-T.json").read_text())
    assert st["totals"]["since_start"]["gateway_calls"] == 3
    (out / "PAUSE").unlink()
    with pytest.raises(SystemExit, match="fresh notice"):                   # the notice predates the pause
        run(path, "draft", "--set", "T", "--notice-acknowledged", "--resume", gw=Gateway())
    assert run(path, "preflight", "--stage", "draft", "--set", "T") == 0
    gw2 = Gateway()
    assert run(path, "draft", "--set", "T", "--notice-acknowledged", "--resume", gw=gw2) == 0
    st = json.loads((out / "draft-state-alias-T.json").read_text())
    assert st["totals"]["since_start"]["gateway_calls"] == 3 + len(gw2.bodies) == 3 + 7
    assert st["totals"]["since_resume"]["gateway_calls"] == 7
