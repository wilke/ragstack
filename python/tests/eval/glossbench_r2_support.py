"""Synthetic fixtures for the round-2 harness tests (items 2c/2d).

Every paper, symbol, value and sentence here is made up. Nothing is read from a
corpus, an index or an evaluation set, and no endpoint is called.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from ragstack.ingestion.glossary_file import (  # noqa: E402
    GlossaryRow,
    build_row,
    masked_key,  # noqa: E402
    write_glossary_file,
)
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    DEFAULT_CONFIG,
    Glossary,
    SymbolGlossaryAttacher,
    content_key,
)

FILLER = ("The pond survey ran over three summers and the boats were counted by hand. "
          "Observers wrote every count into a notebook kept in a dry box. ")


def paper_text(i: int) -> str:
    """One synthetic article: a defined symbol with a value (S1 material), an
    undefined symbol with a value (S2; ``Nb``, not the stoplisted ``n``), a
    stoplisted statistic with a value (``p``: no value statement) and a
    words-only quantity (S3)."""
    return (
        f"Drift of paper boats, study {i}. "
        "where Qb is the boat drift speed of a paper boat on still water. "
        + FILLER
        + f"In the calm case we measured Qb = 0.{i}5 m/s for the folded boats. "
        + FILLER
        + f"The number of samples Nb = 1{i} was small in that season (p = 0.0{i}). "
        + FILLER + FILLER
        + f"The pond keeper reported a wind gust of eleven knots near jetty {i} on the last day. "
        + FILLER
    )


def float_text(i: int) -> str:
    return f"Table {i}. Summary of boats. The sail angle Sa = {i}2 degrees was fixed for all boats. "


def chunk_rows(doc_id: str, text: str, path: str, size: int = 160, step: int = 120) -> list[dict[str, Any]]:
    out = []
    s = 0
    n = 0
    while True:
        e = min(len(text), s + size)
        out.append({"id": f"{doc_id}-c{n}", "doc_id": doc_id, "content": text[s:e], "start_char": s,
                    "end_char": e, "metadata": {"source_path": path, "title": f"Boat paper {doc_id}"}})
        n += 1
        if e >= len(text):
            break
        s += step
    return out


def corpus(n_papers: int = 6, floats: bool = True) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(chunk rows, doc_id -> text). Paper k = article ``PMCk`` (+ float ``PMCk#table-1``)."""
    rows: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for i in range(1, n_papers + 1):
        doc, text = f"d{i}", paper_text(i)
        texts[doc] = text
        rows += chunk_rows(doc, text, f"PMC{i}")
        if floats:
            fdoc, ftext = f"d{i}t", float_text(i)
            texts[fdoc] = ftext
            rows += chunk_rows(fdoc, ftext, f"PMC{i}#table-1")
    return rows, texts


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def write_arm(path: Path, texts: dict[str, str], *, empty: bool = False, only: set[str] | None = None) -> Path:
    """A rules-built glossary file; ``empty`` gives every document an empty merged glossary
    (the pure condition of a model that proposed nothing)."""
    att = SymbolGlossaryAttacher(DEFAULT_CONFIG)
    rows: list[GlossaryRow] = []
    for doc_id in sorted(texts):
        if only is not None and doc_id not in only:
            continue
        text = texts[doc_id]
        if empty:
            g = Glossary((), "llm/2:synthetic:x")
            rows.append(GlossaryRow(doc_id, content_key(text), len(text), masked_key(text), g, g, g))
        else:
            rows.append(build_row(doc_id, text, att))
    write_glossary_file(path, rows, producer="synthetic test fixture")
    return path


def make_config(tmp: Path, *, n_papers: int = 6, arms_empty: bool = False, with_frontier: bool = True,
                pool: dict[str, Any] | None = None, **blocks: Any) -> tuple[Path, dict[str, Any], dict[str, str]]:
    rows, texts = corpus(n_papers)
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    chunks = write_jsonl(data / "chunks.jsonl", rows)
    arms = {a: str(write_arm(data / f"{a}.jsonl", texts, empty=arms_empty))
            for a in ("rules", "scoutp", "qwen38p", "qwen36p")}
    if with_frontier:
        arms["frontierp"] = str(write_arm(data / "frontierp.jsonl", texts, empty=True))
    out = tmp / "out"
    cfg = {"out_dir": str(out),
           "corpora": {"O": {"chunks": [str(chunks)], "expected_chunks": len(rows), "paper_key": "path_root",
                             "arms": arms}},
           "pool": {"n_papers": 4, "min_vs_chunks": 2, "s1_per_paper": 2, "s2_per_paper": 1, "s3_per_paper": 1,
                    "reach_gate": 3, **(pool or {})},
           **blocks}
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg))
    return path, cfg, texts


# ------------------------------------------------------------------ a fake drafter

QUANTITY = {"Qb": "boat drift speed", "Nb": "number of samples", "Sa": "sail angle"}
_VS = re.compile(r"\b(Qb|Nb|Sa)\s*=\s*(\d[\d.]*\s*(?:m/s|degrees)?)")


def fake_draft(passage: str) -> dict[str, Any]:
    """What a cooperative drafter answers for a passage (deterministic)."""
    if "WITHOUT a symbol" in passage:
        m = re.search(r"wind gust of eleven knots near jetty \d+", passage)
        if not m:
            return {"none": True}
        return {"question": "How strong was the wind gust the pond keeper reported?", "quote": m.group(0),
                "quantity": "wind gust", "value": "eleven knots", "binding_terms": ["wind gust"], "symbol": ""}
    body = passage.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    m = _VS.search(body)
    if not m:
        return {"none": True}
    sym, val = m.group(1), m.group(2).strip()
    lo = max(0, m.start() - 12)
    quote = body[lo:m.end()]
    q = {"Qb": "How fast did the folded paper boats drift in the calm case?",
         "Nb": "How many samples were taken in that season?",
         "Sa": "At what sail angle were the boats fixed?"}[sym]
    return {"question": q, "quote": quote, "quantity": QUANTITY[sym], "value": val,
            "binding_terms": [QUANTITY[sym]], "symbol": sym}
