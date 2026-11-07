"""``audit``: gold-label audit. Does a selected quote also occur, verbatim after whitespace
normalisation, in any OTHER extracted record (another eval or background article, a
duplicate article (#799), or a table/figure record of the same article)?

A retrieval of that other record could answer the question but would score as a miss
(the gold is one record path), so such items are flagged ``ambiguous_gold`` and scored
separately by ``score``.

The audit also counts the normalised quote's occurrences inside the gold record itself.
More than one (a short quote such as "in PBS buffer" or "P < 0.05" repeated in the
article) is flagged ``within_record_repeat``: which occurrence the writer meant is not
recoverable from the text, so these items are scored with the ambiguous set too (``score``
additionally takes the best coverage over every occurrence).

Search is exact. To keep it linear in corpus size it is anchored: every quote with at least
three whitespace tokens is indexed by its longest interior token (interior tokens are
whole tokens in any text containing the quote); a record is only substring-searched for
the quotes whose anchor occurs among its tokens. Quotes with fewer than three tokens are
searched in every record.

Writes ``audit.jsonl`` (question_id, ambiguous_gold, within_record_repeat, counts and the
other record paths, which are ids, not text) and ``audit.summary.json``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import article_of, log, norm_ws, read_jsonl, write_json, write_jsonl
from .config import out_path, require
from .passages import iter_records


def anchor(nquote: str) -> str | None:
    toks = nquote.split(" ")
    if len(toks) < 3:
        return None
    return max(toks[1:-1], key=lambda t: (len(t), t))


def count_occurrences(text: str, needle: str) -> int:
    """Occurrences of ``needle`` in ``text`` (overlapping ones counted)."""
    if not needle:
        return 0
    n, i = 0, text.find(needle)
    while i >= 0:
        n += 1
        i = text.find(needle, i + 1)
    return n


def find_occurrences(questions: list[dict[str, Any]], records) -> tuple[dict[str, list[str]], dict[str, int]]:
    """``(question_id -> other record paths containing the normalised quote,
    question_id -> occurrences of the normalised quote in its own gold record)``."""
    gold_counts: dict[str, int] = {}
    hits = _find(questions, records, gold_counts)
    return hits, gold_counts


def find_other_occurrences(questions: list[dict[str, Any]], records) -> dict[str, list[str]]:
    """question_id -> other record paths containing the normalised quote."""
    return _find(questions, records, {})


def _find(questions: list[dict[str, Any]], records, gold_counts: dict[str, int]) -> dict[str, list[str]]:
    nq = {q["question_id"]: norm_ws(q["answer_quote"]) for q in questions}
    gold = {q["question_id"]: q["record_path"] for q in questions}
    by_anchor: dict[str, list[str]] = defaultdict(list)
    unanchored: list[str] = []
    for qid, s in nq.items():
        a = anchor(s)
        (by_anchor[a].append(qid) if a else unanchored.append(qid))
    hits: dict[str, list[str]] = defaultdict(list)
    seen_paths: set[str] = set()
    for rec in records:
        path = rec["path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        text = norm_ws(rec["text"])
        toks = set(text.split(" "))
        cands = [qid for a in toks & by_anchor.keys() for qid in by_anchor[a]] + unanchored
        for qid in cands:
            if path == gold[qid]:
                gold_counts[qid] = count_occurrences(text, nq[qid])
            elif nq[qid] in text:
                hits[qid].append(path)
    return hits


def run(cfg: dict[str, Any]) -> int:
    require(cfg, "out_dir")
    rows_out = []
    summary: dict[str, Any] = {}
    sets = [("main", "questions.jsonl", "audit.jsonl")]
    if out_path(cfg, "questions.check.jsonl").exists():
        sets.append(("check", "questions.check.jsonl", "audit.check.jsonl"))
    for name, qfile, afile in sets:
        questions = read_jsonl(out_path(cfg, qfile))
        hits, gold_counts = find_occurrences(questions, iter_records(cfg))
        rows_out = []
        for q in questions:
            others = sorted(hits.get(q["question_id"], []))
            same = [p for p in others if article_of(p) == q["pmcid"]]
            n_gold = gold_counts.get(q["question_id"], 0)
            rows_out.append({"question_id": q["question_id"], "ambiguous_gold": bool(others),
                             "within_record_repeat": n_gold > 1, "n_in_gold_record": n_gold,
                             "n_other_records": len(others), "n_same_article_records": len(same),
                             "n_other_articles": len({article_of(p) for p in others} - {q["pmcid"]}),
                             "other_records": others[:50]})
        write_jsonl(out_path(cfg, afile), rows_out)
        summary[name] = {"questions": len(questions),
                         "ambiguous_gold": sum(r["ambiguous_gold"] for r in rows_out),
                         "within_record_repeat": sum(r["within_record_repeat"] for r in rows_out),
                         "gold_record_missing": sum(1 for r in rows_out if r["n_in_gold_record"] == 0),
                         "same_article_only": sum(1 for r in rows_out
                                                  if r["ambiguous_gold"] and r["n_other_articles"] == 0)}
        log(f"audit[{name}]: {summary[name]['ambiguous_gold']} of {len(questions)} quotes are ambiguous, "
            f"{summary[name]['within_record_repeat']} repeat inside their gold record")
    write_json(out_path(cfg, "audit.summary.json"), summary)
    return 0
