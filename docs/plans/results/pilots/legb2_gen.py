"""Leg B re-run, stages A and B — paraphrase, then write the query, on the real LLM.

Two passes, never one, because the one-pass form is the T1b contamination the plan
forbids: a query written from the chunk it must retrieve shares that chunk's vocabulary
by construction. Pass A sees the section and nothing else; pass B sees pass A's summary
and nothing else — not the section, not the title, not the abstract.

**One item per call.** The previous round batched 14 items per LLM call and could not rule
out cross-item bleed. mango's 131k window makes batching unnecessary at this scale (a
section is 250-2,200 tokens), so isolation is by construction here rather than by
instruction. What the big window actually bought: nothing was truncated, at any stage.

The stage-B prompt is where fixes 1 and 3 are *asked for*; ``legb2_screen.py`` is where
they are *enforced*. Asking is not the same as checking, and the previous round's failure
was trusting a prompt clause.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import time

import mango

HERE = pathlib.Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Stage A — abstractive summary of the section (thinking off; mechanical)
# --------------------------------------------------------------------------- #
# Changed from the committed g1 PARAPHRASE_PROMPT in exactly one way: specific NAMES must
# survive the paraphrase verbatim. Fix 1 requires the query to name an entity that the
# source section also names; a summariser that generalises "quercetin" to "a flavonoid
# compound" makes that unachievable downstream. That generalisation is the documented
# mechanism behind six of the previous round's ten bad accepts.
PARAPHRASE_PROMPT = """\
You are helping build an evaluation set for a scientific search system.

Read the passage below and write a 2-3 sentence ABSTRACTIVE summary of what it
establishes. Rules:
- Use your own words for the sentences. Do not copy phrases of more than three
  consecutive words.
- Carry the passage's SPECIFIC NAMES over verbatim: genes, proteins, organisms,
  strains, compounds, drugs, cell types, diseases, cohorts, instruments, and
  software or tool names. Never replace a name with a generic description.
- Keep at least one concrete measurement, condition or result if the passage reports one.
- Do not mention "the passage", "the text", "the authors", or any document.
- Output only the summary.

PASSAGE:
{chunk}
"""

# --------------------------------------------------------------------------- #
# Stage B — the query (thinking on; this is where the three fixes are asked for)
# --------------------------------------------------------------------------- #
QUERY_PROMPT = """\
You are writing a realistic question that a working researcher might type into a
literature search tool.

Below is a short summary of one finding. Write ONE question that this finding answers.

HARD RULES. A question that breaks any of them is thrown away:
1. AT MOST 20 words. Aim for 10 to 16. Real search queries are short.
2. ONE clause, asking ONE thing. Never join two questions with "and", "or", "while",
   "whereas", or a comma. If the summary contains two findings, pick one.
3. NAME AT LEAST ONE SPECIFIC ENTITY from the summary — a gene, protein, organism,
   strain, compound, drug, cell type, disease, cohort, instrument, or software/tool
   name — spelled exactly as the summary spells it. Never write a generic stand-in
   such as "a flavonoid compound", "a web-based tool", or "a certain transcription
   factor". If the summary truly names nothing specific, return an empty query.
4. Never refer to "this study", "the authors", a figure, a table, or any document.
5. Do not presuppose the design decisions of the work being summarised. Someone who
   has never read it must be able to ask this question.

Output ONLY a JSON object on a single line, with no code fence and no commentary:
{{"entity": "<the exact entity name you used, or \\"\\" if none>", "query": "<the question, or \\"\\" if rule 3 cannot be met>"}}

SUMMARY:
{summary}
"""


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def parse_json_line(text: str) -> dict:
    """The model sometimes wraps the object in a fence or prose. Take the outermost {...}."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        o = json.loads(t[i:j + 1])
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


def run_stage(m, items, fn, label, workers=mango.MAX_INFLIGHT):
    t0 = time.time()
    done = [0]
    lock = __import__("threading").Lock()

    def wrap(it):
        r = fn(it)
        with lock:
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {label}: {done[0]}/{len(items)} "
                      f"({time.time() - t0:.0f}s)", flush=True)
        return r

    with cf.ThreadPoolExecutor(workers) as ex:
        out = list(ex.map(wrap, items))
    print(f"  {label}: {len(out)} done in {time.time() - t0:.0f}s", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    secs = json.loads((HERE / "legb2_sections.json").read_text())["rows"]
    if args.limit:
        secs = secs[:args.limit]
    print(f"{len(secs)} sections; model {mango.MODEL}", flush=True)

    m = mango.Mango()

    # ---- Stage A: section text ONLY ---------------------------------------- #
    def para(s):
        r = m.chat(PARAPHRASE_PROMPT.format(chunk=s["sec_text"]),
                   max_tokens=900, think=False, temperature=0.3)
        return {"qid": s["qid"], "summary": r["text"], "ok": r["ok"],
                "finish": r["finish"], "elapsed": r["elapsed"], "note": r["note"]}

    pa = run_stage(m, secs, para, "stage A paraphrase")
    (HERE / "legb2_summaries.json").write_text(json.dumps(pa, indent=1))
    stats_a = m.stats()
    print("  after A:", stats_a, flush=True)

    # ---- Stage B: the SUMMARY ONLY ----------------------------------------- #
    summaries = {r["qid"]: r["summary"] for r in pa if r["ok"]}

    def qgen(qid):
        r = m.chat(QUERY_PROMPT.format(summary=summaries[qid]),
                   max_tokens=4000, think=True, temperature=0.5)
        o = parse_json_line(r["text"])
        return {"qid": qid, "raw": r["text"][:2000],
                "query": (o.get("query") or "").strip(),
                "entity": (o.get("entity") or "").strip(),
                "parsed": bool(o), "ok": r["ok"], "finish": r["finish"],
                "elapsed": r["elapsed"], "note": r["note"]}

    qb = run_stage(m, sorted(summaries), qgen, "stage B query")
    (HERE / "legb2_queries.json").write_text(json.dumps(qb, indent=1))

    stats = m.stats()
    print("  after B:", stats, flush=True)
    (HERE / "legb2_gen_manifest.json").write_text(json.dumps({
        "model": mango.MODEL,
        "endpoint": mango.URL,
        "max_inflight": mango.MAX_INFLIGHT,
        "one_item_per_call": True,
        "paraphrase_prompt_sha256": sha(PARAPHRASE_PROMPT),
        "query_prompt_sha256": sha(QUERY_PROMPT),
        "paraphrase_thinking": False,
        "query_thinking": True,
        "stats_after_A": stats_a,
        "stats_after_B": stats,
        "n_sections": len(secs),
        "n_summaries_ok": len(summaries),
        "n_queries_parsed": sum(1 for r in qb if r["parsed"]),
        "n_queries_nonempty": sum(1 for r in qb if r["query"]),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
