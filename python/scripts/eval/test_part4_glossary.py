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
    Definition,
    Glossary,
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


def reworded_file(path: Path, word: str = "zebrine") -> Path:
    """The same documents with an LLM half whose definitions are reworded: a
    second condition that differs only in what the glossary field says."""
    att = SymbolGlossaryAttacher()
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
    pg = pages()
    path = reworded_file(tmp_path / "g.jsonl")
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
                    "--glossary-allow-unverifiable"]) == 0
    assert seen["glossary_file"] == ["a=/x/a.jsonl", "b=/x/b.jsonl"] and seen["glossary_allow_unverifiable"]
    assert p4g.parse_specs(seen["glossary_file"]) == [("a", Path("/x/a.jsonl")), ("b", Path("/x/b.jsonl"))]
    for bad in (["noequals"], ["a=/x", "a=/y"], ["=/x"], []):
        with pytest.raises(SystemExit):
            p4g.parse_specs(bad)
    called: dict[str, Any] = {}
    monkeypatch.setattr(p4, "cmd_part4", lambda a: called.setdefault("part4", sorted(vars(a))) and 0)
    dt.main(["--private-config", str(cfg), "--out-dir", str(out), "part4"])
    assert "glossary_file" not in called["part4"]
