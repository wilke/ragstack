"""Stage 0b' step 2 -- relabel the development set under the **revision-3** protocol.

`SPEC-confirmation-run-r3.md` SS3.7 changes the labeling protocol, not the sample size:

1. **Quote-primary anchoring.** The labeler returns verbatim quotes -- the first ten words
   and the last ten words of each span, plus the TITLE of the unit the span sits under --
   and **no numbers at all**. Spans are located by exact-then-normalised string match
   against the indexed text. A quote that does not locate anywhere is a hallucinated span
   (the existing P.5 gate, <= 0.05). This is the relocation `s0_label.py` had to implement
   as a *repair*; here it is the only path.
2. **Two judges.** Judge 1 is `mango:8003` (`Llama-4-Scout`, non-reasoning), judge 2 is
   `mango:8004` (`Qwen3.6-35B-A3B`, a reasoning model whose ``<think>`` block is stripped
   before parsing). Both label the SAME pairs with the SAME prompt, <= 4 concurrent each.
3. **Gates.** Self-consistency >= 0.90, hallucinated-span <= 0.05, and the new
   document-level whether-any-evidence agreement >= 0.90. Read by `s0_labelgates_r3.py`.

What is deliberately **kept** from Stage 0 so the two runs are comparable:

* the labeling set (`s0_label.labeling_set` over the pools already on disk -- nothing is
  re-retrieved and nothing is re-embedded);
* the 10 % self-consistency duplicate selection and its shuffle, seeded identically
  (``SEED_LABELDUP``), asserted against Stage 0's own ``label_meta.json``;
* D1 span semantics -- a located quote is snapped OUTWARD to whole-sentence boundaries;
* "no localizable evidence" (``evidence_sets: []``) as a legal verdict;
* the SS6.5 48,000-token windowing, counted in the same served-generator tokenizer, so both
  judges see identical windows;
* the sentence segmentation, `ragstack.ingestion.chunkers.sentence_spans`, unchanged
  between the pinned commit 55a0fc2 and repo HEAD (asserted at run time).

What **changes** relative to `s0_label.py`, deliberately:

* a quote whose interval crosses a unit boundary is **split into a multi-span set** rather
  than dropped as ``unresolvable`` -- under a quote-primary protocol the model was never
  asked to respect unit boundaries, so honouring the quote is the faithful reading of D1;
* there are no claimed indices, so ``index_agreement`` has no meaning and is not reported;
* the SS6.4 rule 3 minimality audit is **not run** (see RESULTS-stage0b-relabel.md
  "deviations": it was an instrument failure in Stage 0 and r3 SS3.7 does not gate on it).

**Zero store writes.** The only endpoints contacted are `mango:8003` and `mango:8004`.
No Qdrant / Elasticsearch / Neo4j / tenant-API client is constructed anywhere. GPUs 6 and
7 are untouched (mango is a remote host and nothing here selects a device).

Usage::

    python3 s0_label_r3.py --judge scout            # full development set
    python3 s0_label_r3.py --judge qwen  --limit 20 --tag smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# The Phase-0 helper modules (``stage1_common``, ``pilot_common``) live beside the working
# copy of the analysis tree, not in the repo; ``s0_common`` imports both at module load.
_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                              # noqa: E402
from s0_label import _find, _norm_map, labeling_set, segment       # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RUBRIC = HERE.parent / "design" / "RUBRIC-evidence.md"
# The merged TREC CDS topic file is a Phase-0 build artifact, not a repo file; ``s0_common``
# resolves it relative to its own tree, which in a repo checkout has no ``cds/``.
TOPICS = (C.CDS if (C.CDS / "topics_merged.json").exists()
          else _HELPERS / "cds") / "topics_merged.json"
R3 = C.WORK / "r3"
R3.mkdir(parents=True, exist_ok=True)

WINDOW_TOKENS = 48_000        # SS6.5, unchanged
CONC = 4                      # SS6.4 rule 5 -- non-negotiable, shared host
TEMPERATURE = 0.0

JUDGES = {
    "scout": {"base": C.MANGO,      "expect": C.SCOUT_EXPECT, "max_tokens": 3_000,
              "reasoning": False},
    "qwen":  {"base": C.MANGO_QWEN, "expect": C.QWEN_EXPECT,  "max_tokens": 12_000,
              "reasoning": True},
}

SYSTEM = (
    "You locate evidence in biomedical articles. You answer with strict JSON and nothing "
    "else. You never invent text."
)

# PROMPT REVISION 3 -- quote-primary. Derived from `s0_label.PROMPT` revision 2 by
# deleting every index and replacing the span identifier with three verbatim quotes. The
# clinical framing, the worked example's content, the enumeration rule and the
# "no localizable evidence" clause are carried over verbatim, so that a difference between
# the Stage 0 labels and these ones is attributable to the ANCHORING change and not to a
# rewritten task. The RUBRIC is unchanged and its sha256 is unmoved.
PROMPT = """You are given a clinical case (a TREC CDS topic) and one biomedical article \
that human assessors judged RELEVANT to it. Your task is NOT to re-judge relevance — the \
relevance is established. Your task is to say WHERE in this article the evidence for it \
lives. In most articles there IS such a place, and it is most often in the abstract.

## The clinical need
Type: {ntype}   (diagnosis = identify what the patient has; test = which investigation to \
order or how to read it; treatment = what to do for the patient)

Summary: {summary}

Description: {description}

## Definitions

SPAN: a contiguous run of WHOLE sentences inside EXACTLY ONE titled unit of the article. \
You identify a span by QUOTING it — never by numbering it. A span is given as three \
verbatim strings copied from the article below:

* `unit_title` — the title of the unit the span sits under, copied exactly as printed;
* `first_words` — the first ten words of the span, copied exactly;
* `last_words` — the last ten words of the span, copied exactly.

There are no unit numbers and no sentence numbers in this article, and you must not \
invent any. Prefer the SHORTEST run of sentences that carries the claim.

EVIDENCE SET: a MINIMAL collection of one or more spans that TOGETHER justify this \
article's relevance to the clinical need above. Minimal means no span can be deleted \
without losing sufficiency. A set may combine spans from different units when neither \
alone suffices (e.g. a Methods sentence naming the population plus a Results sentence \
carrying the effect).

## Worked example

Need: 56-year-old woman, shortness of breath 3 weeks after mastectomy, right calf \
tenderness, elevated D-dimer (type: diagnosis).
Article unit titled "Results" contains the sentence: "Pulmonary embolism occurred in 14 \
of 412 patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep \
venous thrombosis of the calf."
Correct answer: ONE set, ONE span —
`{{"unit_title": "Results", "first_words": "Pulmonary embolism occurred in 14 of 412 \
patients (3.4%) within", "last_words": "concurrent deep venous thrombosis of the calf."}}`
Not the whole paragraph; not the preceding sentence as well "for context".

## Rules
* If the article contains SEVERAL INDEPENDENT locations that each justify relevance, emit \
each as its OWN separate evidence set. One set per argument. Two locations are two sets, \
never one set with four spans.
* Never emit a set that is another set plus extra sentences.
* If the abstract already states the evidence, use the ABSTRACT span, not a later \
restatement of it.
* A case report, a cohort, a review or a trial about the same clinical situation as the \
case above almost always HAS a locatable span — the sentence that states the finding, the \
association, the test result or the management. Find it rather than declining.
* `{{"evidence_sets": []}}` — "no localizable evidence" — is a LEGAL verdict and you must \
use it when it is true: the article is relevant only by topic/aboutness and NO span of it \
justifies a {ntype} decision (for instance an editorial that asserts a problem matters but \
reports no finding and recommends nothing). It should be UNCOMMON. Never invent a span to \
avoid returning nothing, and never return nothing to avoid choosing.

## Output format — strict JSON, nothing else
{{"evidence_sets": [
  {{"spans": [{{"unit_title": "<the unit's title, VERBATIM>",
              "first_words": "<the first ten words of the span, VERBATIM>",
              "last_words": "<the last ten words of the span, VERBATIM>"}}]}}
]}}

Every quoted string must be copied EXACTLY from the article below, character for \
character; they are located automatically by string match, and a string that is not in \
the article is discarded and counted against you.

## The article
{body}
"""

REPROMPT = ("\n\nYour previous answer was rejected: every quoted string must appear "
            "VERBATIM in the article text above, copied character for character. Answer "
            "again, JSON only, with quotes you can see in the article.")


# ------------------------------------------------------------------ the judges
def served_model(base: str) -> str:
    with urllib.request.urlopen(base + "/v1/models", timeout=30) as r:
        return json.load(r)["data"][0]["id"]


class Judge:
    """Bounded, polite chat client for one mango endpoint. <= ``conc`` in flight.

    Modelled on ``s0_label.Scout`` (same retry ladder, same accounting, same politeness
    contract); parameterised by endpoint because r3 needs two of them, and because the
    reasoning judge needs a much larger ``max_tokens`` and a ``<think>`` stripper.
    """

    def __init__(self, name: str, conc: int = CONC):
        spec = JUDGES[name]
        self.name = name
        self.base = spec["base"]
        self.max_tokens = spec["max_tokens"]
        self.reasoning = spec["reasoning"]
        self.model = served_model(self.base)
        if self.model != spec["expect"]:
            raise SystemExit(
                f"{self.base} serves {self.model!r}, not {spec['expect']!r} — STOP")
        self.slots: queue.Queue = queue.Queue()
        for _ in range(conc):
            self.slots.put(1)
        self.pool = ThreadPoolExecutor(conc)
        self.lock = threading.Lock()
        self.conc = conc
        self.requests = self.retries = self.failures = self.truncated = 0
        self.prompt_tokens = self.completion_tokens = self.thinking_chars = 0
        self.seconds = 0.0

    def chat(self, prompt: str, *, timeout: int = 1800) -> dict:
        payload = {"model": self.model, "temperature": TEMPERATURE,
                   "max_tokens": self.max_tokens, "seed": C.SEED_LABELDUP,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}]}
        body = json.dumps(payload).encode()
        self.slots.get()
        try:
            for attempt in range(4):
                try:
                    t0 = time.time()
                    req = urllib.request.Request(
                        self.base + "/v1/chat/completions", body,
                        {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        out = json.load(r)
                    dt = time.time() - t0
                    ch = out["choices"][0]
                    msg = ch["message"]
                    content = (msg.get("content") or "").strip()
                    # vLLM exposes a reasoning model's thinking either inline in
                    # ``content`` as ``<think>…</think>``, or split out into a sibling
                    # field. mango:8004 uses ``reasoning``; other builds use
                    # ``reasoning_content``. Read both, strip both, never parse either.
                    reasoning = (msg.get("reasoning_content")
                                 or msg.get("reasoning") or "")
                    text, think_chars = strip_think(content, reasoning)
                    with self.lock:
                        self.requests += 1
                        self.prompt_tokens += out["usage"]["prompt_tokens"]
                        self.completion_tokens += out["usage"]["completion_tokens"]
                        self.thinking_chars += think_chars
                        self.seconds += dt
                        if ch.get("finish_reason") == "length":
                            self.truncated += 1
                    return {"text": text, "raw": content, "reasoning": reasoning,
                            "reasoning_chars": think_chars,
                            "finish": ch.get("finish_reason"), "ok": True,
                            "seconds": round(dt, 2)}
                except Exception as e:  # noqa: BLE001
                    with self.lock:
                        self.retries += 1
                    if attempt == 3:
                        with self.lock:
                            self.failures += 1
                        return {"text": "", "raw": "", "reasoning": "",
                                "reasoning_chars": 0, "ok": False,
                                "finish": f"{type(e).__name__}: {e}", "seconds": 0.0}
                    time.sleep(3 * (attempt + 1))
        finally:
            self.slots.put(1)

    def stats(self) -> dict:
        return {"judge": self.name, "endpoint": self.base, "served_model": self.model,
                "requests": self.requests, "retries": self.retries,
                "failures": self.failures, "truncated_responses": self.truncated,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "thinking_chars_stripped": self.thinking_chars,
                "llm_seconds": round(self.seconds, 1), "concurrency": self.conc,
                "max_tokens": self.max_tokens, "temperature": TEMPERATURE,
                "seed": C.SEED_LABELDUP}


_THINK_OPEN = re.compile(r"<think>", re.I)
_THINK_CLOSE = re.compile(r"</think>", re.I)


def strip_think(content: str, reasoning: str = "") -> tuple[str, int]:
    """Return (answer text, characters of thinking removed).

    Handles three shapes seen from vLLM reasoning models: an inline ``<think>…</think>``
    block; a separate ``reasoning_content`` field with the answer in ``content``; and a
    truncated response whose ``<think>`` never closes (everything is thinking, and the
    answer is empty -- which then fails to parse and earns the one re-prompt).
    """
    removed = len(reasoning or "")
    s = content or ""
    close = list(_THINK_CLOSE.finditer(s))
    if close:                              # keep only what follows the LAST close tag
        removed += close[-1].end()
        s = s[close[-1].end():]
    elif _THINK_OPEN.search(s):            # unclosed: the whole tail is thinking
        m = _THINK_OPEN.search(s)
        removed += len(s) - m.start()
        s = s[:m.start()]
    return s.strip(), removed


# ------------------------------------------------------------------ rendering
def _normtitle(t: str) -> str:
    return " ".join((t or "").split()).lower()


def render_r3(seg, order=None) -> str:
    """Quote-primary rendering: titled units, sentences one per line, NO numbers."""
    idx = list(range(len(seg))) if order is None else order
    parts = []
    for j in idx:
        _ui, title, sents = seg[j]
        parts.append(f'### UNIT TITLED "{title or "(untitled)"}"')
        parts.extend(s for _k, _a, _b, s in sents)
    return "\n".join(parts)


# ------------------------------------------------------------------ location
def _tables(seg):
    """(unit_bounds, sent_of, title->[(start,end)]) over the INDEXED text."""
    unit_bounds, sent_of = [], []
    title_ranges: dict[str, list[tuple[int, int]]] = {}
    for ui, ti, sents in seg:
        if not sents:
            continue
        us, ue = sents[0][1], sents[-1][2]
        unit_bounds.append((ui, us, ue, ti))
        title_ranges.setdefault(_normtitle(ti), []).append((us, ue))
        for k, s0, e0, _tx in sents:
            sent_of.append((ui, k, s0, e0))
    return unit_bounds, sent_of, title_ranges


def _hits(nd: str, q: str, cap: int = 200) -> list[int]:
    out, st = [], nd.find(q)
    while st != -1 and len(out) < cap:
        out.append(st)
        st = nd.find(q, st + 1)
    return out


def _find_preferring(nd: str, q: str, ranges) -> tuple[int, bool, bool]:
    """(position, ambiguous, landed_in_a_preferred_range). Pure string match."""
    hs = _hits(nd, q)
    if not hs:
        return -1, False, False
    for a, b in ranges or ():
        inside = [h for h in hs if a <= h < b]
        if inside:
            return inside[0], len(hs) > 1, True
    return hs[0], len(hs) > 1, False


def _snap(cs: int, ce: int, unit_bounds, sent_of) -> list[dict]:
    """D1: snap [cs, ce) OUTWARD to whole sentences, one entry per unit it touches.

    A quote confined to one unit yields one span. A quote that crosses a unit boundary
    yields one span per unit -- the multi-span set of D2, not a dropped span.
    """
    out = []
    for ui, us, ue, ti in unit_bounds:
        a, b = max(cs, us), min(ce, ue)
        if a >= b:
            continue
        inside = [x for x in sent_of if x[0] == ui]
        first = next((x for x in inside if x[2] <= a < x[3]), None)
        if first is None:
            first = next((x for x in inside if x[2] >= a), None)
        last = next((x for x in reversed(inside) if x[2] < b <= x[3]), None)
        if last is None:
            last = next((x for x in reversed(inside) if x[3] <= b), None)
        if first is None or last is None or last[1] < first[1]:
            continue
        out.append({"unit": ui, "unit_title": ti, "first_sentence": first[1],
                    "last_sentence": last[1], "start": first[2], "end": last[3]})
    return out


FAIL_PROBLEMS = ("quote_not_in_document", "last_quote_not_in_document", "no_first_words",
                 "span_unlocatable", "no_json")


def parse_and_verify_r3(raw: str, seg, text: str):
    """Quote-primary span recovery. Returns (sets, problems, stats).

    * **hallucinated** -- a quoted string is nowhere in the document (exact, then
      whitespace-collapsed case-folded match). The span is dropped and the P.5 gate's
      numerator is incremented. This is the ONLY failure mode the gate counts.
    * **unlocatable** -- the quote is in the document but the snapped interval covers no
      whole sentence of any unit (empty units, degenerate interval). Dropped, counted
      separately, not a hallucination.
    * **split_across_units** -- the quote's interval crosses a unit boundary; it becomes a
      multi-span set rather than being discarded.
    """
    stats = {"spans_seen": 0, "spans_emitted": 0, "hallucinated": 0, "unlocatable": 0,
             "ambiguous_quote": 0, "no_last_words": 0, "split_across_units": 0,
             "title_landed": 0, "title_elsewhere": 0, "title_unknown": 0}
    problems: list[str] = []
    txt = (raw or "").strip()
    if txt.startswith("```"):
        parts = txt.split("```")
        txt = parts[1] if len(parts) > 1 else txt
        if txt.lstrip().lower().startswith("json"):
            txt = txt.lstrip()[4:]
    a, b = txt.find("{"), txt.rfind("}")
    if a < 0 or b < a:
        return [], ["no_json"], stats
    try:
        obj = json.loads(txt[a:b + 1])
    except Exception as e:  # noqa: BLE001
        return [], [f"bad_json:{type(e).__name__}"], stats

    nd, imap = _norm_map(text)
    unit_bounds, sent_of, title_ranges = _tables(seg)
    # original char offset -> position in the normalised string (imap is increasing)
    import bisect

    def n_of(o: int) -> int:
        return bisect.bisect_left(imap, o)

    sets = []
    for es in obj.get("evidence_sets", []) or []:
        spans = []
        for sp in es.get("spans", []) or []:
            if not isinstance(sp, dict):
                continue
            stats["spans_seen"] += 1
            fw = " ".join(str(sp.get("first_words") or "").split()).lower()
            lw = " ".join(str(sp.get("last_words") or "").split()).lower()
            ti = _normtitle(str(sp.get("unit_title") or ""))
            if not fw:
                stats["hallucinated"] += 1
                problems.append("no_first_words")
                continue
            fw = fw[:120]
            ranges = [(n_of(x), n_of(y)) for x, y in title_ranges.get(ti, ())]
            if not ranges:
                stats["title_unknown"] += 1
            pf, amb1, in_title = _find_preferring(nd, fw, ranges)
            if pf < 0:
                stats["hallucinated"] += 1
                problems.append("quote_not_in_document")
                continue
            if ranges:
                stats["title_landed" if in_title else "title_elsewhere"] += 1
            if lw:
                lw = lw[-120:]
                pl, amb2 = _find(nd, lw, prefer_from=pf)
                if pl < 0:
                    stats["hallucinated"] += 1
                    problems.append("last_quote_not_in_document")
                    continue
                end_n = pl + len(lw)
            else:
                stats["no_last_words"] += 1
                amb2 = False
                end_n = pf + len(fw)
            if amb1 or amb2:
                stats["ambiguous_quote"] += 1
            if end_n <= pf:
                end_n = pf + len(fw)
            cs, ce_ = imap[pf], imap[min(end_n, len(imap)) - 1] + 1
            got = _snap(cs, ce_, unit_bounds, sent_of)
            if not got:
                stats["unlocatable"] += 1
                problems.append("span_unlocatable")
                continue
            if len(got) > 1:
                stats["split_across_units"] += 1
                problems.append("split_across_units")
            stats["spans_emitted"] += len(got)
            spans.extend(got)
        if spans:
            sets.append({"spans": spans})
    return sets, problems, stats


# ------------------------------------------------------------------ the run
def build_pairs():
    """The Stage 0 development labeling set, rebuilt in Stage 0's own order."""
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    pools = {a: json.loads((C.WORK / f"pool_{a}.json").read_text())
             for a in C.INDEX_KEYS if (C.WORK / f"pool_{a}.json").exists()}
    lset = labeling_set(pools, qrels)
    saved = C.WORK / "labeling_set.json"
    if saved.exists():
        assert json.loads(saved.read_text()) == lset, \
            "labeling_set no longer reproduces Stage 0's labeling_set.json — STOP"
    pairs = [(t, d, "pooled") for t in C.DEV_TOPICS for d in lset[t]["pooled"]]
    pairs += [(t, d, "sample") for t in C.DEV_TOPICS for d in lset[t]["sample"]]
    bad = {t for t, _d, _k in pairs} - set(C.DEV_TOPICS)
    assert not bad, f"non-development topic in the labeling set: {sorted(bad)}"
    return pairs, lset, qrels


def dup_indices(n_pairs: int) -> set[int]:
    """Stage 0's own 10 % duplicate draw, reproduced and asserted against its manifest."""
    rng = random.Random(C.SEED_LABELDUP)
    dup = set(rng.sample(range(n_pairs), max(1, round(0.10 * n_pairs))))
    meta = C.WORK / "label_meta.json"
    if meta.exists():
        m = json.loads(meta.read_text())
        if m.get("dup_indices") is not None and n_pairs == m["n_pairs"]:
            assert sorted(dup) == list(m["dup_indices"]), \
                "duplicate selection differs from Stage 0's — the runs are not comparable"
    return dup


def selftest() -> dict:
    """Exercise every branch of the quote-primary locator on a real indexed document.

    Run with ``--selftest``; contacts no endpoint. Checks, in order: an in-unit quote
    snaps to exactly the sentence it names; a quote that spans a unit boundary becomes a
    TWO-span set rather than a drop; a quote that is nowhere in the text is counted as
    hallucinated and dropped; and the ``<think>`` stripper handles the four response
    shapes a vLLM reasoning server can produce.
    """
    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
        if len(docs) > 3:
            break
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
        if len(units) > 3:
            break
    d = next(k for k in docs if k in units)
    text = docs[d]
    seg = segment(text, units[d])
    assert len(seg) >= 2, "selftest needs a document with >= 2 units"
    out: dict[str, object] = {"docno": d, "units": len(seg)}

    def quoted(u, first_of, last_of, title=None):
        ui, ti, sents = seg[u]
        return {"unit_title": title if title is not None else ti,
                "first_words": " ".join(sents[first_of][3].split()[:10]),
                "last_words": " ".join(sents[last_of][3].split()[-10:])}

    raw = json.dumps({"evidence_sets": [{"spans": [quoted(1, 0, 0)]}]})
    sets, probs, st = parse_and_verify_r3(raw, seg, text)
    sp = sets[0]["spans"][0]
    assert not probs and st["hallucinated"] == 0
    assert text[sp["start"]:sp["end"]].strip() == seg[1][2][0][3].strip(), \
        "in-unit quote did not snap to its own sentence"
    out["in_unit_snap"] = "ok"

    raw = json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": seg[0][1],
         "first_words": " ".join(seg[0][2][-1][3].split()[:10]),
         "last_words": " ".join(seg[1][2][0][3].split()[-10:])}]}]})
    sets, probs, st = parse_and_verify_r3(raw, seg, text)
    assert st["split_across_units"] == 1 and len(sets[0]["spans"]) == 2, \
        "cross-unit quote was not split into a multi-span set"
    out["cross_unit_split"] = "ok"

    raw = json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": "Results", "first_words": "zzq wubble frotz nine hundred",
         "last_words": "never appears anywhere here at all"}]}]})
    sets, probs, st = parse_and_verify_r3(raw, seg, text)
    assert sets == [] and st["hallucinated"] == 1 \
        and probs == ["quote_not_in_document"], "hallucination not caught"
    out["hallucination"] = "ok"

    body = '{"evidence_sets": []}'
    assert strip_think(f"<think>reasoning</think>\n{body}")[0] == body
    assert strip_think("<think>unclosed reasoning")[0] == ""
    assert strip_think(body, "reasoning in a sibling field")[0] == body
    assert strip_think(f"stray close</think>{body}")[0] == body
    out["think_stripper"] = "ok"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks of the locator; contacts no endpoint")
    ap.add_argument("--judge", choices=sorted(JUDGES))
    ap.add_argument("--limit", type=int, default=0, help="smoke: first N pairs only")
    ap.add_argument("--tag", default="", help="output suffix, e.g. 'smoke'")
    ap.add_argument("--conc", type=int, default=CONC)
    args = ap.parse_args()
    if args.selftest:
        print(json.dumps(selftest(), indent=1))
        return
    if not args.judge:
        raise SystemExit("--judge is required (or --selftest)")
    if args.conc > CONC:
        raise SystemExit(f"concurrency {args.conc} > {CONC} — mango is a shared host")

    assert RUBRIC.exists(), "P.5: no labeling call before the rubric exists"
    rub_hash = C.sha256_file(RUBRIC)

    # the sentence segmenter must be the one Stage 0 used (55a0fc2). It is unchanged at
    # HEAD; assert that rather than assert the commit, so the check is about the code.
    seg_diff = subprocess.run(
        ["git", "-C", C.REPO, "diff", "--stat", f"{C.EXPECT_COMMIT}..HEAD",
         "--", "python/ragstack/ingestion/chunkers.py"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert not seg_diff, f"chunkers.py moved since {C.EXPECT_COMMIT}: {seg_diff}"

    pairs, lset, qrels = build_pairs()
    dup = dup_indices(len(pairs))
    todo = list(enumerate(pairs))
    if args.limit:
        todo = todo[:args.limit]
    suffix = f"-{args.tag}" if args.tag else ""
    out_path = R3 / f"labels-r3-{args.judge}{suffix}.jsonl"
    raw_path = R3 / f"raw-r3-{args.judge}{suffix}.jsonl"
    man_path = R3 / f"label-manifest-{args.judge}{suffix}.json"

    done: dict[tuple[str, str], dict] = {}
    if out_path.exists():                                    # checkpointed / resumable
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["topic"], r["docno"])] = r
    todo = [x for x in todo if (x[1][0], x[1][1]) not in done]
    print(f"judge={args.judge} pairs_total={len(pairs)} to_run={len(todo)} "
          f"already_done={len(done)} dup={len(dup)}", flush=True)

    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
    tops = json.loads(TOPICS.read_text())
    gt = C.GenTokenizer()                       # served-generator tokenizer, SS7.2 / SS6.5

    judge = Judge(args.judge, conc=args.conc)
    prompt_sha = C.sha256_text(PROMPT)
    print(f"served_model={judge.model} prompt_sha256={prompt_sha}", flush=True)

    lock = threading.Lock()
    t0 = time.time()
    fout = open(out_path, "a")
    fraw = open(raw_path, "a")
    n_done = [0]

    def one(i_tdk):
        i, (t, d, kind) = i_tdk
        text, us = docs[d], units[d]
        seg = segment(text, us)
        f = tops[t]["fields"]
        groups, cur, curtok = [], [], 0                       # SS6.5 windowing
        for j, (_ui, _ti, _sents) in enumerate(seg):
            n = gt.count(text[us[j]["start_char"]:us[j]["end_char"]])
            if cur and curtok + n > WINDOW_TOKENS:
                groups.append(cur)
                cur, curtok = [], 0
            cur.append(j)
            curtok += n
        if cur:
            groups.append(cur)

        allsets, problems, raws, rawfull = [], [], [], []
        vstats: dict[str, int] = {}
        finishes = []
        for g in groups:
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render_r3(seg, g))
            r = judge.chat(p)
            raws.append(r["text"])
            rawfull.append({"content": r["raw"], "reasoning": r.get("reasoning", "")})
            finishes.append(r["finish"])
            sets, probs, stx = parse_and_verify_r3(r["text"], seg, text)
            probs_all = list(probs)
            failed = any(x in FAIL_PROBLEMS or x.startswith("bad_json") for x in probs)
            if failed:                                   # SS6.4 rule 2: exactly ONE retry
                r2 = judge.chat(p + REPROMPT)
                raws.append(r2["text"])
                rawfull.append({"content": r2["raw"],
                                "reasoning": r2.get("reasoning", "")})
                finishes.append(r2["finish"])
                sets2, probs2, stx2 = parse_and_verify_r3(r2["text"], seg, text)
                for k_, v_ in stx2.items():
                    stx[k_] = stx.get(k_, 0) + v_
                probs_all += list(probs2) + ["reprompted"]
                sets = sets2 if sets2 else sets
            allsets.extend(sets)
            problems.extend(probs_all)
            for k_, v_ in stx.items():
                vstats[k_] = vstats.get(k_, 0) + v_

        rec = {"topic": t, "docno": d, "kind": kind, "grade": qrels[t].get(d, 0),
               "sets": allsets, "problems": problems, "windowed": len(groups) > 1,
               "vstats": vstats, "raws": raws,
               "n_quote_fail": vstats.get("hallucinated", 0),
               "n_spans": sum(len(s["spans"]) for s in allsets),
               "n_units": len(seg), "doc_chars": len(text),
               "dropped": bool(problems) and not allsets,
               "judge": args.judge, "served_model": judge.model,
               "prompt_sha256": prompt_sha,
               "raw_response_sha256": hashlib.sha256(
                   "\n\x00\n".join(x["content"] for x in rawfull).encode()).hexdigest(),
               "finish_reasons": finishes, "pair_index": i}

        if i in dup:                                   # SS6.4 rule 4: self-consistency
            order = list(range(len(seg)))
            random.Random(C.SEED_LABELDUP + i).shuffle(order)
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render_r3(seg, order))
            r = judge.chat(p)
            s2, dprobs, dstx = parse_and_verify_r3(r["text"], seg, text)
            rec["dup_sets"] = s2
            rec["dup_problems"] = dprobs
            rec["dup_vstats"] = dstx
            rawfull.append({"content": r["raw"], "reasoning": r.get("reasoning", "")})

        with lock:
            fout.write(json.dumps(rec) + "\n")
            fout.flush()
            fraw.write(json.dumps({"topic": t, "docno": d, "raws": rawfull}) + "\n")
            fraw.flush()
            n_done[0] += 1
            if n_done[0] % 10 == 0:
                el = time.time() - t0
                print(f"  {n_done[0]}/{len(todo)}  {el:.0f}s  "
                      f"{el / n_done[0]:.1f}s/pair  "
                      f"{judge.prompt_tokens / 1e6:.1f}M prompt tok", flush=True)
        return rec

    list(judge.pool.map(one, todo))
    fout.close()
    fraw.close()
    wall = round(time.time() - t0, 1)
    C.atomic_json(man_path, {
        "judge": args.judge,
        "protocol": "SPEC-confirmation-run-r3.md SS3.7 — quote-primary, two judges",
        "rubric_sha256": rub_hash, "rubric_path": str(RUBRIC),
        "prompt_sha256": prompt_sha, "prompt_revision": 3,
        "system_sha256": C.sha256_text(SYSTEM),
        "reprompt_sha256": C.sha256_text(REPROMPT),
        "stats": judge.stats(), "n_pairs_total": len(pairs), "n_pairs_run": len(todo),
        "n_pairs_preexisting": len(done),
        "dup_indices": sorted(dup), "window_tokens": WINDOW_TOKENS,
        "dev_topics": C.DEV_TOPICS,
        "sentence_segmentation":
            f"ragstack.ingestion.chunkers.sentence_spans @ {C.EXPECT_COMMIT[:7]} "
            f"(unchanged at repo HEAD, asserted)",
        "labels_path": str(out_path), "raw_path": str(raw_path),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wall_seconds": wall,
        "seconds_per_pair": round(wall / max(len(todo), 1), 2)})
    print(f"labels written: {out_path} (+{len(todo)} this run)  wall={wall}s",
          json.dumps(judge.stats()), flush=True)


if __name__ == "__main__":
    main()
