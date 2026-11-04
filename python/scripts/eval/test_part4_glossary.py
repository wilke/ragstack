"""``part4-glossary``: the retrieval-only re-score with one glossary file per condition.

Offline: synthetic pages (made-up words and symbols), glossary files written to
tmp_path, the SFR tokenizer from the local HF cache, a fake semantic-embedding
store, and injected fakes for page loading and retrieval at the
:class:`part4_glossary.GEnv` seam. No store or endpoint is contacted (every URL
is the dead port 127.0.0.1:1).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import backfill_symbol_glossary as bf  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402
import deployed_docs_trial as dt  # noqa: E402
import part4_glossary as p4g  # noqa: E402
import section_aware_trial as sat  # noqa: E402
from section_aware_trial import C  # noqa: E402

from ragstack.ingestion.glossary_file import (  # noqa: E402
    GLOSSARY_REFUSED_EXIT_CODE,
    GlossaryRow,
    build_row,
    file_sha256,
    read_glossary_file,
    render_chunks,
    write_glossary_file,
)
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    DEFAULT_CONFIG,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    SymbolGlossaryAttacher,
)
from ragstack.models import Chunk, Document  # noqa: E402

DEAD = "http://127.0.0.1:1"
PAD = " ".join(["plain filler words keep these chunks apart from each other"] * 9)

#: Made-up symbols with a definition ("where X is ...") and value statements, so
#: the rules extractor finds them and the value-only rendering attaches them.
TEXTS = {
    "d0": ("# Setup\n\nThe flow obeys R = Kv Pd, where Kv is the valve coefficient, and Pd is the "
           "pressure drop.\n" + PAD + "\n\nTable 1: Kv = 0.8 and Pd = 12 per unit.\n" + PAD
           + "\n\n# Later\n\nIn the second rig, where Kv is the leak factor of the seal. " + PAD
           + "\nTable 2: Kv = 4 here.\n" + PAD),
    "d1": ("# Model\n\nWe use the decay constant Zq, where Zq is the fade rate of the signal.\n" + PAD
           + "\n\nMeasured: Zq = 0.3 per hour, with Wm ≈ 2 where Wm is the window width.\n" + PAD),
    "d2": "# Notes\n\nNo symbols in this page, only words.\n\n" + PAD + "\n" + PAD,
}
N_ROWS = {"d0": 3, "d1": 2, "d2": 2}


def es_rows(texts: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Live-index rows that tile each page (cut at spaces), with a stored live field."""
    rows = []
    for did, text in (texts or TEXTS).items():
        n = N_ROWS[did]
        cut = [0] + [text.index(" ", len(text) * k // n) + 1 for k in range(1, n)] + [len(text)]
        for k in range(n):
            rows.append({"_id": f"{did}-{k}", "chunk_id": f"{did}-{k}", "doc_id": did,
                         "content": text[cut[k]:cut[k + 1]], "start_char": cut[k], "end_char": cut[k + 1],
                         "metadata": {"filename": f"{did}.md"}, "symbol_glossary": f"live field {did}-{k}"})
    return rows


def pages() -> dict[str, dict[str, Any]]:
    return dt.pages_from_index(es_rows())[0]


def rules_file(path: Path, texts: dict[str, str] | None = None) -> Path:
    att = SymbolGlossaryAttacher()
    write_glossary_file(path, [build_row(d, t, att) for d, t in sorted((texts or TEXTS).items())],
                        producer="synthetic rules", config=att.config)
    return path


def reworded_file(path: Path, word: str = "zebrine", config: GlossaryConfig | None = None) -> Path:
    """The same documents with an LLM half whose definitions are reworded: a
    second condition that differs only in what the glossary field says."""
    att = SymbolGlossaryAttacher(config) if config is not None else SymbolGlossaryAttacher()
    rows = []
    for d, t in sorted(TEXTS.items()):
        r = build_row(d, t, att)
        entries = tuple(GlossaryEntry(e.symbol, tuple(Definition(f"{word} {x.text}", x.offset) for x in e.definitions))
                        for e in r.rules.entries)
        llm = Glossary(entries, "llm/1:synthetic:abc:w80000x4:t2048") if entries else None
        rows.append(GlossaryRow(r.doc_id, r.text_sha256, r.text_chars, r.text_ws_sha256, r.rules, llm,
                                llm if llm else r.rules, {}, {"source": "synthetic"}, None))
    write_glossary_file(path, rows, producer="synthetic reworded", config=att.config)
    return path


class FakeEmbStore:
    """Deterministic unit vectors per text; nothing is ever missing (no embedding call)."""

    def __init__(self, d: Path) -> None:
        pass

    def missing(self, texts: list[str]) -> list[str]:
        return []

    def matrix(self, texts: list[str]) -> Any:
        import numpy as np

        out = np.array([[(int(dt.sha(t)[i:i + 4], 16) % 97) + 1.0 for i in range(0, 32, 4)] for t in texts],
                       dtype=np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture
def fake_sem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sat, "EmbStore", FakeEmbStore)


def dump(arms: dict[str, list[C]]) -> list[tuple[str, str, str, str, str, int]]:
    return [(a, c.id, c.doc_id, c.content, c.glossary, c.tokens) for a in sorted(arms) for c in arms[a]]


# ------------------------------------------------------------ the pre-change code, verbatim

def _build_arms_before(pg: dict[str, dict[str, Any]]) -> tuple[dict[str, list[C]], dict[str, Any]]:
    """``deployed_docs_trial.build_arms`` as it was before ``glossaries=`` (main 0e22c55)."""
    from collections import Counter as _Counter

    from ragstack.ingestion.chunkers import FixedTokenWindowChunker
    from ragstack.ingestion.tokenization import make_token_counter

    counter = make_token_counter("hf", model=sat.SFR_MODEL)
    chunkers = {(s, ov): FixedTokenWindowChunker(s, ov, token_counter=counter) for s in dt.SIZES for ov in (0, s // 8)}
    att = SymbolGlossaryAttacher()  # rules/3, no LLM cache
    arms: dict[str, list[C]] = {"L": [], **{a: [] for a in dt.ARMS}}
    st: _Counter[str] = _Counter()
    for did, p in sorted(pg.items()):
        for n, r in enumerate(p["rows"]):
            arms["L"].append(C(f"L|{did}|{n}", did, str(r["content"]), "", str(r.get("symbol_glossary") or "")))
        text = p["text"]
        g = att.glossary_for(text)
        secs = dt.md_sections(text)
        st["pages_with_heading"] += int(any(h for _, h, _ in secs))
        st["sections"] += len(secs)
        for (s, ov), ck in chunkers.items():
            a = dt.arm_name("T", s, ov)
            for n, ch in enumerate(ck.chunk(Document(id=did, content=text))):
                arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "",
                                 g.for_chunk(ch.content, att.config, start_char=ch.start_char)))
            a = dt.arm_name("X", s, ov)
            n = 0
            for off, head, seg in secs:
                for ch in ck.chunk(Document(id=did, content=seg)):
                    arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, head,
                                     g.for_chunk(ch.content, att.config, start_char=off + ch.start_char)))
                    n += 1
    tok = counter._tokenizer()
    for cs in arms.values():
        for i in range(0, len(cs), 512):
            enc = tok([c.content for c in cs[i:i + 512]], add_special_tokens=False)["input_ids"]
            for c, ids in zip(cs[i:i + 512], enc, strict=True):
                c.tokens = len(ids)
    page_tokens = {did: len(tok([p["text"]], add_special_tokens=False)["input_ids"][0]) for did, p in pg.items()}
    st["L_chunks_with_live_glossary"] = sum(1 for c in arms["L"] if c.glossary)
    st["T512o64_chunks_with_rules_glossary"] = sum(1 for c in arms["T512o64"] if c.glossary)
    return arms, {"stats": dict(st), "page_tokens": page_tokens}


def _sem_before(out: Path, pg: dict[str, dict[str, Any]]) -> dict[str, list[C]]:
    """``build_sem_arms``'s rendering before ``glossaries=``: the in-process rules/3 glossary."""
    from ragstack.ingestion.chunkers import make_chunker
    from ragstack.ingestion.tokenization import make_token_counter

    counter = make_token_counter("hf", model=sat.SFR_MODEL)
    store = sat.EmbStore(out / "emb")
    att = SymbolGlossaryAttacher()
    arms: dict[str, list[C]] = {a: [] for a in ("SEM512", "SEM256")}
    for a in arms:
        ck = make_chunker("semantic", embed_fn=lambda ts: store.matrix(list(ts)).tolist(),
                          **{**dt.S_COMMON, **dt.S_ARMS[a], "token_counter": counter})
        for did, p in sorted(pg.items()):
            g = att.glossary_for(p["text"])
            for n, ch in enumerate(ck.chunk(Document(id=did, content=p["text"]))):
                arms[a].append(C(f"{a}|{did}|{n}", did, ch.content, "",
                                 g.for_chunk(ch.content, att.config, start_char=ch.start_char)))
    return arms


# ------------------------------------------------------------ rendering


def test_without_glossaries_the_arms_are_byte_identical(tmp_path: Path, fake_sem: None) -> None:
    """No flag: the live field on L, the in-process rules/3 glossary on T/X/SEM, as before."""
    pg = pages()
    before, bmeta = _build_arms_before(pg)
    now, nmeta = dt.build_arms(pg)
    explicit, _ = dt.build_arms(pg, glossaries=None)
    assert dump(now) == dump(before) == dump(explicit)
    assert json.dumps(nmeta, sort_keys=True) == json.dumps(bmeta, sort_keys=True)
    assert all(c.glossary == f"live field {c.doc_id}-{c.id.rsplit('|', 1)[1]}" for c in now["L"])
    assert any(c.glossary for c in now["T128o0"]), "the fixture must exercise the rules rendering"
    sem_now, sem_before = dt.build_sem_arms(tmp_path, pg, []), _sem_before(tmp_path, pg)
    assert [x[:5] for x in dump(sem_now)] == [x[:5] for x in dump(sem_before)]  # (tokens: not set before)
    assert any(c.glossary for c in sem_now["SEM256"])


def test_with_a_file_the_arms_render_as_the_backfill_and_the_load(tmp_path: Path, fake_sem: None) -> None:
    assert_renders_as_backfill_and_load(tmp_path, reworded_file(tmp_path / "g.jsonl"))


#: Non-default on the two knobs the fixture can see: positional choice of
#: definition (Kv is defined twice) and the per-chunk entry cap (Kv and Pd share a chunk).
ODD_CONFIG = GlossaryConfig(positional=False, max_entries_per_chunk=1)


def test_the_files_header_config_renders_every_arm(tmp_path: Path, fake_sem: None) -> None:
    """The file's header config, not the default, is what the arms render under."""
    path = reworded_file(tmp_path / "odd.jsonl", config=ODD_CONFIG)
    assert read_glossary_file(path).config == ODD_CONFIG != DEFAULT_CONFIG
    assert_renders_as_backfill_and_load(tmp_path, path)
    pg = pages()
    gc = p4g.resolve_condition("odd", path, pg)
    odd, _ = dt.build_arms(pg, glossaries=gc.glossaries, glossary_config=gc.config)
    dflt, _ = dt.build_arms(pg, glossaries=gc.glossaries, glossary_config=DEFAULT_CONFIG)
    for name in ("L", "T128o0", "X128o16"):
        assert [c.glossary for c in odd[name]] != [c.glossary for c in dflt[name]], name
        assert all(c.glossary.count("\n") == 0 for c in odd[name]), name  # max_entries_per_chunk=1
    sem_odd = dt.build_sem_arms(tmp_path, pg, [], glossaries=gc.glossaries, glossary_config=gc.config)
    sem_dflt = dt.build_sem_arms(tmp_path, pg, [], glossaries=gc.glossaries, glossary_config=DEFAULT_CONFIG)
    assert [c.glossary for c in sem_odd["SEM256"]] != [c.glossary for c in sem_dflt["SEM256"]]


def assert_renders_as_backfill_and_load(tmp_path: Path, path: Path) -> None:
    pg = pages()
    gc = p4g.resolve_condition("x", path, pg)
    gfile = read_glossary_file(path)
    arms, _ = dt.build_arms(pg, glossaries=gc.glossaries, glossary_config=gc.config)
    # L: the live rows, exactly as backfill_symbol_glossary --glossary-file plans them
    want_l = []
    for did, p in sorted(pg.items()):
        planned, _ = bf.plan_updates(SymbolGlossaryAttacher(gfile.config), p["text"], p["rows"], gc.glossaries[did])
        assert [x[0] for x in planned] == [str(r["_id"]) for r in p["rows"]]
        want_l += [x[1] for x in planned]
        assert {x[2] for x in planned} == {gc.glossaries[did].version}
    assert [c.glossary for c in arms["L"]] == want_l
    assert any("zebrine" in g for g in want_l)
    # T/X/SEM: the chunks the arm cut, as the load's render_chunks renders them
    sem = dt.build_sem_arms(tmp_path, pg, [], glossaries=gc.glossaries, glossary_config=gc.config)
    for name, cs in [*arms.items(), *sem.items()]:
        if name == "L":
            continue
        starts = _starts(name, pg)
        chunks = [Chunk(id=c.id, doc_id=c.doc_id, content=c.content, start_char=s, end_char=s + len(c.content))
                  for c, s in zip(cs, starts, strict=True)]
        render_chunks(chunks, gc.glossaries, gfile.config)
        assert [c.glossary for c in cs] == [ch.symbol_glossary for ch in chunks], name
    assert any("zebrine" in c.glossary for c in arms["T512o0"])
    assert any("zebrine" in c.glossary for c in arms["X128o16"])


def _starts(arm: str, pg: dict[str, dict[str, Any]]) -> list[int]:
    """Each chunk's document start, re-derived the way the arm cuts (T/X: token windows; SEM: semantic)."""
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker, make_chunker
    from ragstack.ingestion.tokenization import make_token_counter

    counter = make_token_counter("hf", model=sat.SFR_MODEL)
    out: list[int] = []
    if arm.startswith("SEM"):
        store = FakeEmbStore(Path("."))
        ck = make_chunker("semantic", embed_fn=lambda ts: store.matrix(list(ts)).tolist(),
                          **{**dt.S_COMMON, **dt.S_ARMS[arm], "token_counter": counter})
        for did, p in sorted(pg.items()):
            out += [ch.start_char for ch in ck.chunk(Document(id=did, content=p["text"]))]
        return out
    fam, rest = arm[0], arm[1:]
    s, ov = (int(x) for x in rest.split("o"))
    ck = FixedTokenWindowChunker(s, ov, token_counter=counter)
    for did, p in sorted(pg.items()):
        if fam == "T":
            out += [ch.start_char for ch in ck.chunk(Document(id=did, content=p["text"]))]
        else:
            for off, _, seg in dt.md_sections(p["text"]):
                out += [off + ch.start_char for ch in ck.chunk(Document(id=did, content=seg))]
    return out


def test_two_files_differ_in_the_glossary_and_not_in_the_chunks(tmp_path: Path) -> None:
    pg = pages()
    a = p4g.resolve_condition("rules", rules_file(tmp_path / "a.jsonl"), pg)
    b = p4g.resolve_condition("reworded", reworded_file(tmp_path / "b.jsonl"), pg)
    arms_a, _ = dt.build_arms(pg, glossaries=a.glossaries, glossary_config=a.config)
    arms_b, _ = dt.build_arms(pg, glossaries=b.glossaries, glossary_config=b.config)
    for name in arms_a:
        assert [(c.id, c.content, c.tokens) for c in arms_a[name]] == [(c.id, c.content, c.tokens) for c in arms_b[name]]
    for name in ("L", "T128o0", "X512o64"):
        ga, gb = [c.glossary for c in arms_a[name]], [c.glossary for c in arms_b[name]]
        assert ga != gb, name
        assert all(x == y.replace("zebrine ", "") for x, y in zip(ga, gb, strict=True)), name


# ------------------------------------------------------------ the subcommand


def write_config(path: Path, expected: dict[str, int], conditions: dict[str, Any]) -> Path:
    cfg = {"es_url": DEAD, "index": "synthetic-index", "gen_url": DEAD, "judge_url": DEAD, "gateway_url": DEAD,
           "deny_ports": [], "deny_reason": "shared endpoint", "out_dir": "/nonexistent",
           "stratum_prefixes": [], "strata": [], "seed_stratum": "seed", "files": {},
           "seed_questions": {"P": {}, "N": {}, "W": {}},
           "prompts": {"GEN_SYSTEM": "s", "GEN_USER": "Rules:\n- Quote exactly " + dt._FIRST_RULE_END + "- x\n\n{passage}",
                       "JUDGE_USER": "j", "M_USER": "m", "JUDGE3_USER": "j", "READER_SYSTEM": "r",
                       "GRADER_USER": "g"},
           "part4": {"arms": sorted(expected), "conditions": conditions, "expected_chunks": expected,
                     "expected_m_pages": 0, "prior_question_files": [], "analysis": {"n_perm": 199, "n_boot": 50},
                     "files": {"prereg": "PREREG-synthetic.md", "manifest": "manifest.json",
                               "questions": "part4-questions.jsonl"}}}
    path.write_text(json.dumps(cfg))
    return path


#: One question per symbol-bearing value chunk; its words are in the reworded
#: definitions only, so the glossary decides which chunk ranks first.
QUESTIONS = [
    {"record_id": "q0", "doc_id": "d0", "status": "valid", "set": "S", "question": "zebrine valve",
     "gold": ["Table 1: Kv = 0.8 and Pd = 12 per unit."]},
    {"record_id": "q1", "doc_id": "d1", "status": "valid", "set": "S", "question": "zebrine fade",
     "gold": ["Zq = 0.3 per hour"]},
    {"record_id": "q2", "doc_id": "d2", "status": "invalid", "set": "S", "question": "x", "gold": ["y"]},
]


class World:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()


def make_env(w: World, rows: list[dict[str, Any]], tmp: Path) -> p4g.GEnv:
    build = p4g.make_build(tmp, [])

    def load() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        w.calls["load"] += 1
        return dt.pages_from_index(rows)

    def build_arms(pg: Any, arms: list[str], gl: Any, cfg: Any) -> dict[str, list[C]]:
        w.calls["build"] += 1
        return build(pg, arms, gl, cfg)

    def retrieve(arm: str, chunks: list[C], recs: list[dict[str, Any]]) -> dict[str, list[str]]:
        w.calls["retrieve"] += 1
        out = {}
        for r in recs:
            qw = set(r["question"].lower().split())
            score = [len(qw & set(f"{c.content} {c.glossary}".lower().split())) for c in chunks]
            out[r["record_id"]] = [chunks[i].id for i in sorted(range(len(chunks)), key=lambda i: (-score[i], i))]
        return out

    return p4g.GEnv(load, build_arms, lambda s: len(s.split()), retrieve,
                    lambda: {"commit": "f" * 40, "dirty": False, "untracked": 0, "recorded_at": "now"})


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "out"
    out.mkdir()
    (out / "PREREG-synthetic.md").write_text("synthetic\n")
    (out / "part4-questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in QUESTIONS))
    pg = pages()
    g = p4g.resolve_condition("r", rules_file(tmp_path / "count.jsonl"), pg)
    arms = p4g.make_build(tmp_path, [])(pg, ["L", "T512o0"], g.glossaries, g.config)
    cfg = write_config(tmp_path / "config.json", {a: len(cs) for a, cs in arms.items()}, {"k1": {"top": 1}, "B60": {"budget": 60}})
    return cfg, out


def test_rescore_records_the_files_in_the_results_and_the_provenance(setup: tuple[Path, Path], tmp_path: Path) -> None:
    cfg, out = setup
    fa, fb = rules_file(tmp_path / "rules.jsonl"), reworded_file(tmp_path / "reworded.jsonl")
    w = World()
    rc = p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), [("rules", fa), ("reworded", fb)])
    assert rc == p4g.EXIT_OK
    res = json.loads((out / p4g.DEFAULT_RESULTS).read_text())
    assert res["schema"] == p4g.RESULTS_SCHEMA and res["exploratory"] is True and res["citable"] is True
    assert res["reference_condition"] == "rules"
    for name, path, producer in (("rules", fa, "synthetic rules"), ("reworded", fb, "synthetic reworded")):
        gfile = read_glossary_file(path)
        for rec in (res["provenance"]["glossary_files"][name],
                    next(x for x in res["glossary_conditions"] if x["name"] == name)):
            assert rec["sha256"] == file_sha256(path)
            assert rec["producer"] == producer
            assert rec["counts"] == gfile.header["counts"]
            assert rec["config"] == gfile.header["config"]
            assert rec["glossary_versions"] == gfile.glossary_versions
            assert rec["documents"] == {"verified": 3, "verified_ws": 0, "unverified": 0}
    assert res["provenance"]["commit"] == "f" * 40
    assert res["questions"]["n_valid"] == 2
    # the glossary moved retrieval, and only the glossary: k1 finds both quotes under the reworded file
    assert res["found"]["reworded"]["L"]["k1"] == 1.0
    assert res["found"]["rules"]["L"]["k1"] < 1.0
    assert set(res["contrasts"]) == {"reworded-vs-rules"}
    assert res["contrasts"]["reworded-vs-rules"]["L"]["k1"]["gained"] >= 1
    assert res["chunks_with_glossary"]["rules"] == res["chunks_with_glossary"]["reworded"]
    # multiplicity and the power floor
    fam = res["design"]["family"]
    assert res["design"]["n_tests"] == 2 * 2 * 1 == fam["arms"] * fam["budget_conditions"] * fam["non_reference_conditions"]
    assert fam["alternative"] == "two-sided" and "power-table" in res["power"]
    for a, by_cond in res["contrasts"]["reworded-vs-rules"].items():
        for cond, t in by_cond.items():
            assert t["alternative"] == "two-sided", (a, cond)
            k, d = t["n_pages_nonzero"], t["gained"] + t["lost"]
            m = t["min_attainable_p"]
            assert (m["n_pages_nonzero"], m["discordant"]) == (k, d)
            assert m["regime"] == ("exact" if t["p_exact"] is not None else "monte_carlo")
            assert m["sign_flip"] <= t["p"] and m["mcnemar_two_sided"] <= t["p_mcnemar_two_sided"]
    assert res["contrasts"]["reworded-vs-rules"]["L"]["k1"]["min_attainable_p"]["mcnemar_two_sided"] == 1.0


def test_shuffle_rendered_permutes_each_arms_populated_fields(tmp_path: Path) -> None:
    """Round 2's shuffled control in the in-memory harness: per arm, the populated
    chunks and the multiset of strings are unchanged, the assignment is not, the
    chunks are untouched, the same seed gives the same arms."""
    pg = pages()
    g = p4g.resolve_condition("r", reworded_file(tmp_path / "r.jsonl"), pg)
    arms = p4g.make_build(tmp_path, [])(pg, ["L", "T128o0", "T512o0"], g.glossaries, g.config)
    before = {a: [(c.id, c.content, c.glossary) for c in cs] for a, cs in arms.items()}
    shuf, rec = p4g.shuffle_arms(arms, "261101|shuf")
    again, rec2 = p4g.shuffle_arms(arms, "261101|shuf")
    assert {a: [(c.id, c.content, c.glossary) for c in cs] for a, cs in arms.items()} == before  # not mutated
    assert rec == rec2 and [[c.glossary for c in cs] for cs in shuf.values()] == \
        [[c.glossary for c in cs] for cs in again.values()]
    moved_any = False
    for a, cs in arms.items():
        true = {c.id: c.glossary for c in cs}
        new = {c.id: c.glossary for c in shuf[a]}
        assert [(c.id, c.content) for c in shuf[a]] == [(c.id, c.content) for c in cs]
        assert {k for k, v in new.items() if v} == {k for k, v in true.items() if v}
        assert Counter(v for v in new.values() if v) == Counter(v for v in true.values() if v)
        moved = sum(1 for k in true if true[k] and new[k] != true[k])
        assert moved == rec[a]["populated"] - rec[a]["fixed_points"]
        moved_any |= moved > 0
        assert rec[a]["seed"] == "261101|shuf" and len(rec[a]["permutation_sha256"]) == 64
    assert moved_any, "the fixture must render at least two distinct fields in some arm"


def test_rescore_records_the_shuffled_condition(setup: tuple[Path, Path], tmp_path: Path) -> None:
    cfg, out = setup
    fb = reworded_file(tmp_path / "reworded.jsonl")
    w = World()
    rc = p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), [("true", fb), ("shuf", fb)],
                     shuffles={"shuf": "261101|shuf"})
    assert rc == p4g.EXIT_OK
    res = json.loads((out / p4g.DEFAULT_RESULTS).read_text())
    assert res["shuffled_conditions"] == {"shuf": "261101|shuf"}
    rec = next(x for x in res["glossary_conditions"] if x["name"] == "shuf")["shuffle"]
    assert rec["seed"] == "261101|shuf" and set(rec["arms"]) == {"L", "T512o0"}
    assert "shuffle" not in next(x for x in res["glossary_conditions"] if x["name"] == "true")
    assert res["provenance"]["glossary_files"]["shuf"]["shuffle"] == rec
    # field population per arm is the true condition's
    assert res["chunks_with_glossary"]["shuf"] == res["chunks_with_glossary"]["true"]
    for bad in (["nope=1"], ["shuf"], ["shuf=a:b"], ["shuf=1", "shuf=2"]):
        with pytest.raises(SystemExit):
            p4g.parse_shuffles(bad, ["true", "shuf"])
    assert p4g.parse_shuffles(None, ["true"]) == {}


def test_a_mismatch_refuses_before_anything_is_built_or_scored(setup: tuple[Path, Path], tmp_path: Path) -> None:
    cfg, out = setup
    other = dict(TEXTS, d1=TEXTS["d1"].replace("0.3", "0.4"))
    good, bad = rules_file(tmp_path / "good.jsonl"), rules_file(tmp_path / "bad.jsonl", other)
    w = World()
    rc = p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), [("good", good), ("bad", bad)])
    assert rc == GLOSSARY_REFUSED_EXIT_CODE
    assert w.calls["build"] == 0 and w.calls["retrieve"] == 0
    assert not (out / p4g.DEFAULT_RESULTS).exists()
    with pytest.raises(p4g.GlossaryRefused, match=r"mismatch: 1 \(e\.g\. 'd1'\)"):
        p4g.resolve_condition("bad", bad, pages())
    # a page with no row is refused as well (never a silent rules fallback)
    partial = rules_file(tmp_path / "partial.jsonl", {k: v for k, v in TEXTS.items() if k != "d2"})
    with pytest.raises(p4g.GlossaryRefused, match=r"missing: 1 \(e\.g\. 'd2'\)"):
        p4g.resolve_condition("partial", partial, pages())


def test_unverifiable_needs_the_allow_flag(setup: tuple[Path, Path], tmp_path: Path) -> None:
    """The live index lost d0's last chunk: its reassembly is shorter than the file's text."""
    cfg, out = setup
    rows = [r for r in es_rows() if r["_id"] != "d0-2"]
    path = rules_file(tmp_path / "g.jsonl")
    trimmed = dt.pages_from_index(rows)[0]
    with pytest.raises(p4g.GlossaryRefused, match="unverifiable"):
        p4g.resolve_condition("g", path, trimmed)
    w = World()
    assert p4g.rescore(cfg, out, make_env(w, rows, tmp_path), [("g", path)]) == GLOSSARY_REFUSED_EXIT_CODE
    assert w.calls["build"] == 0 and w.calls["retrieve"] == 0
    gc = p4g.resolve_condition("g", path, trimmed, allow_unverifiable=True)
    assert gc.record["documents"] == {"verified": 2, "verified_ws": 0, "unverified": 1}
    assert gc.record["allow_unverifiable"] is True


def test_cli_wires_the_subcommand_and_leaves_part4_alone(monkeypatch: pytest.MonkeyPatch, setup: tuple[Path, Path],
                                                       tmp_path: Path) -> None:
    cfg, out = setup
    seen: dict[str, Any] = {}

    def fake(args: Any) -> int:
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(p4g, "cmd_part4_glossary", fake)
    assert dt.main(["--private-config", str(cfg), "--out-dir", str(out), "part4-glossary",
                    "--glossary-file", "a=/x/a.jsonl", "--glossary-file", "b=/x/b.jsonl",
                    "--glossary-allow-unverifiable", "--shuffle-rendered", "b=261101|shuf"]) == 0
    assert seen["glossary_file"] == ["a=/x/a.jsonl", "b=/x/b.jsonl"] and seen["glossary_allow_unverifiable"]
    assert seen["shuffle_rendered"] == ["b=261101|shuf"]
    assert p4g.parse_specs(seen["glossary_file"]) == [("a", Path("/x/a.jsonl")), ("b", Path("/x/b.jsonl"))]
    for bad in (["noequals"], ["a=/x", "a=/y"], ["=/x"], []):
        with pytest.raises(SystemExit):
            p4g.parse_specs(bad)
    called: dict[str, Any] = {}
    monkeypatch.setattr(p4, "cmd_part4", lambda a: called.setdefault("part4", sorted(vars(a))) and 0)
    dt.main(["--private-config", str(cfg), "--out-dir", str(out), "part4"])
    assert "glossary_file" not in called["part4"]


def test_the_manipulation_check_hard_stops_before_retrieval(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "part4-questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in QUESTIONS))
    pg = pages()
    g = p4g.resolve_condition("r", rules_file(tmp_path / "count.jsonl"), pg)
    arms = p4g.make_build(tmp_path, [])(pg, ["L", "T512o0"], g.glossaries, g.config)
    wrong = {a: len(cs) + (a == "T512o0") for a, cs in arms.items()}
    cfg = write_config(tmp_path / "config.json", wrong, {"k1": {"top": 1}})
    w = World()
    assert p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), [("r", tmp_path / "count.jsonl")]) == p4g.EXIT_HARD_STOP
    assert w.calls["build"] == 1 and w.calls["retrieve"] == 0
    assert not (out / p4g.DEFAULT_RESULTS).exists()


def test_results_inside_the_repository_are_refused(setup: tuple[Path, Path], tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, out = setup
    fake_repo = tmp_path / "repo"
    (fake_repo / "python").mkdir(parents=True)
    # "the repository" is sat._REPO_PY.parent: point it at a scratch tree, so a
    # regression writes there and never into the real checkout
    monkeypatch.setattr(sat, "_REPO_PY", fake_repo / "python")
    inside = fake_repo / "part4-glossary-results.json"
    w = World()
    with pytest.raises(SystemExit, match="inside the repository"):
        p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), [("r", rules_file(tmp_path / "r.jsonl"))],
                    results=str(inside))
    assert not inside.exists() and w.calls["load"] == 0


@pytest.mark.parametrize("k", [10, 20, 21, 40])
def test_the_sign_flip_floor_is_what_the_test_can_return(k: int) -> None:
    """Every page gains: the most extreme outcome, so the test's own p IS the
    attainable minimum, in the exact regime (k <= 20) and the Monte-Carlo one."""
    import chunktrial_stats as cs

    an = {"n_perm": 999, "n_boot": 50, "ci_level": 0.9, "delta": 0.05, "alpha": 0.05}
    t = p4g.with_floor(p4.contrast_test([f"page{i}" for i in range(k)], [1] * k, [0] * k, "superiority", an, 7,
                                        alternative=p4g.ALTERNATIVE))
    real = cs.sign_flip_test([1.0] * k, n_flips=999, seed=7, alternative="two-sided")
    m = t["min_attainable_p"]
    assert m["regime"] == ("exact" if k <= cs.EXACT_MAX_PAGES else "monte_carlo")
    assert m["sign_flip"] == pytest.approx(real["p"]) == pytest.approx(t["p"])
    assert m["mcnemar_two_sided"] == pytest.approx(t["p_mcnemar_two_sided"]) == pytest.approx(2.0 ** (1 - k))


# ------------------------------------------------- --write-retrieval (identity reference)


def recording_env(w: World, tmp: Path) -> tuple[p4g.GEnv, list[tuple[str, dict[str, list[str]]]]]:
    """make_env, with every retrieve call's (arm, returned lists) recorded in call order."""
    env = make_env(w, es_rows(), tmp)
    seen: list[tuple[str, dict[str, list[str]]]] = []
    inner = env.retrieve

    def retrieve(arm: str, chunks: list[C], recs: list[dict[str, Any]]) -> dict[str, list[str]]:
        got = inner(arm, chunks, recs)
        seen.append((arm, {k: list(v) for k, v in got.items()}))
        return got

    return p4g.GEnv(env.load_pages, env.build_arms, env.ntok, retrieve, env.provenance), seen


def test_write_retrieval_leaves_the_results_byte_identical(setup: tuple[Path, Path], tmp_path: Path) -> None:
    cfg, out = setup
    specs = [("rules", rules_file(tmp_path / "rules.jsonl")), ("reworded", reworded_file(tmp_path / "rw.jsonl"))]
    assert p4g.rescore(cfg, out, make_env(World(), es_rows(), tmp_path), specs, results="without.json") == p4g.EXIT_OK
    store = tmp_path / "ref" / "identity.jsonl"
    store.parent.mkdir()
    assert p4g.rescore(cfg, out, make_env(World(), es_rows(), tmp_path), specs, results="with.json",
                       write_retrieval=str(store)) == p4g.EXIT_OK
    assert (out / "with.json").read_bytes() == (out / "without.json").read_bytes()
    assert store.is_file() and (store.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in store.parent.iterdir()] == ["identity.jsonl"]  # no temp file left
    # without the flag, nothing but the results is written
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["PREREG-synthetic.md", "part4-questions.jsonl", "without.json", "with.json"])


@pytest.mark.parametrize("which", [None, "reworded"])
def test_write_retrieval_round_trips_through_the_reranker_loader(setup: tuple[Path, Path], tmp_path: Path,
                                                                  which: str | None) -> None:
    """part4_glossary's writer -> reranker_test.load_identity_reference -> the very
    lists the re-score consumed for that condition, per arm."""
    import reranker_test as rt

    cfg, out = setup
    specs = [("rules", rules_file(tmp_path / "rules.jsonl")), ("reworded", reworded_file(tmp_path / "rw.jsonl"))]
    env, seen = recording_env(World(), tmp_path)
    store = tmp_path / "identity.jsonl"
    assert p4g.rescore(cfg, out, env, specs, write_retrieval=str(store),
                       write_retrieval_condition=which) == p4g.EXIT_OK
    arms = ["L", "T512o0"]
    assert [a for a, _ in seen] == arms * 2  # conditions in order, arms in order
    cond = which or "rules"
    mine = seen[:2] if cond == "rules" else seen[2:]
    other = seen[2:] if cond == "rules" else seen[:2]
    valid = ["q0", "q1"]
    for arm, lists in mine:
        assert rt.load_identity_reference(store, arm) == {q: lists[q] for q in valid}
    # the two conditions rank differently somewhere, so the selection is observable
    assert any(m[1] != o[1] for m, o in zip(mine, other, strict=True))
    rows = [json.loads(x) for x in store.read_text().splitlines()]
    head = rows[0]
    assert head["k"] == p4.ckey(p4g.RETRIEVAL_HEADER_STEP, "-") and head["schema"] == p4g.RETRIEVAL_SCHEMA
    assert head["glossary_condition"] == cond and head["reference_condition"] == "rules"
    assert head["glossary_file"]["sha256"] == file_sha256(dict(specs)[cond])
    assert head["provenance"]["commit"] == "f" * 40 and head["citable"] is True
    assert head["results"]["sha256"] == p4.file_sha(out / p4g.DEFAULT_RESULTS)
    assert head["arms"] == arms and head["n_questions"] == 2
    assert [r["k"] for r in rows[1:]] == [p4.ckey("retrieval", q, a) for a in arms for q in valid]
    with pytest.raises(SystemExit, match="no retrieval rows"):
        rt.load_identity_reference(store, "T1024o0")


def test_write_retrieval_refusals_come_before_any_work(setup: tuple[Path, Path], tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, out = setup
    specs = [("rules", rules_file(tmp_path / "rules.jsonl"))]
    fake_repo = tmp_path / "repo"
    (fake_repo / "python").mkdir(parents=True)
    monkeypatch.setattr(sat, "_REPO_PY", fake_repo / "python")
    (tmp_path / "taken.jsonl").write_text("")
    cases = [({"write_retrieval": str(fake_repo / "x.jsonl")}, "inside the repository"),
             ({"write_retrieval": str(tmp_path / "x.json")}, "must end .jsonl"),
             ({"write_retrieval": str(out / "part4-retrieval.jsonl")}, "part-4 retrieval store"),
             ({"write_retrieval": str(tmp_path / "nodir" / "x.jsonl")}, "does not exist"),
             ({"write_retrieval": str(tmp_path / "taken.jsonl")}, "written once"),
             ({"write_retrieval": str(tmp_path / "x.jsonl"), "write_retrieval_condition": "nope"},
              "not a --glossary-file condition"),
             ({"write_retrieval_condition": "rules"}, "needs --write-retrieval")]
    for kw, msg in cases:
        w = World()
        with pytest.raises(SystemExit, match=msg):
            p4g.rescore(cfg, out, make_env(w, es_rows(), tmp_path), specs, **kw)
        assert w.calls["load"] == 0, kw
    assert not list(fake_repo.glob("*.jsonl")) and not (out / "part4-retrieval.jsonl").exists()
    assert (tmp_path / "taken.jsonl").read_text() == ""  # the existing file is untouched


def test_write_retrieval_store_keeps_the_full_depth_behind_a_non_retrieval_header(tmp_path: Path) -> None:
    """The writer is the only place a list could be cut: the reranked 50-list goes
    out whole (the gate reads its top 10, the budget cells read deeper), and the
    header row is literally not a ``retrieval`` row, so the loader skips it by
    kind rather than by an arm that happens not to match."""
    import reranker_test as rt

    ids = [f"T512o0|d|{i}" for i in range(50)]
    lists = {"T512o0": {"q0": ids, "q1": ids[::-1]}, "L": {"q0": ids[:25], "q1": ids[25:]}}
    store = tmp_path / "ref.jsonl"
    assert p4g.write_retrieval_store(store, {"schema": p4g.RETRIEVAL_SCHEMA}, lists, ["q0", "q1"]) == 4
    rows = [json.loads(x) for x in store.read_text().splitlines()]
    assert rows[0]["k"] == "part4-glossary-retrieval|-|-|-|-" and rows[0]["schema"] == p4g.RETRIEVAL_SCHEMA
    assert [r["k"] for r in rows[1:]] == ["retrieval|q0|T512o0|-|-", "retrieval|q1|T512o0|-|-",
                                          "retrieval|q0|L|-|-", "retrieval|q1|L|-|-"]
    assert rt.load_identity_reference(store, "T512o0") == {"q0": ids, "q1": ids[::-1]}
    assert rt.load_identity_reference(store, "L") == {"q0": ids[:25], "q1": ids[25:]}
    assert (store.stat().st_mode & 0o777) == 0o600


def test_cli_wires_write_retrieval(monkeypatch: pytest.MonkeyPatch, setup: tuple[Path, Path]) -> None:
    cfg, out = setup
    seen: dict[str, Any] = {}
    monkeypatch.setattr(p4g, "rescore", lambda *a, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(p4g, "production_genv", lambda *a: None)
    base = ["--private-config", str(cfg), "--out-dir", str(out), "part4-glossary", "--glossary-file", "a=/x/a.jsonl"]
    assert dt.main(base) == 0
    assert seen["write_retrieval"] is None and seen["write_retrieval_condition"] is None
    assert dt.main([*base, "--write-retrieval", "/x/ref.jsonl", "--write-retrieval-condition", "a"]) == 0
    assert seen["write_retrieval"] == "/x/ref.jsonl" and seen["write_retrieval_condition"] == "a"
