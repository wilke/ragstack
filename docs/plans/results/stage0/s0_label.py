"""Stage 0b step 3 -- the evidence labels (SS6, P.5).

Labeler: **Llama-4-Scout on ``mango:8003``**, served id asserted live before the first
call. It is cross-family from ``bge-reranker-v2-m3``; **``:50052`` contributes nothing to
gold** -- no reranker score, ranking, or artifact reaches this module.

Protocol rules implemented (SS6.4):

1. the labeler sees topic (summary + description + type) and the document segmented into
   numbered structural units with numbered sentences -- never a ranking, a chunk boundary
   or any arm artifact;
2. **quote or it did not happen** -- first/last-10-word anchors are substring-verified
   against the span's own text; failure -> ONE re-prompt -> drop the pair and increment the
   hallucinated-span rate (gate <= 0.05);
3. minimality is demanded prompt-side and audited on a 10% sample ("remove any span not
   strictly needed"); shrinkage reported;
4. self-consistency on 10% duplicates presented at a different unit order; consistent iff
   primary-set char-span Jaccard >= 0.5 (gate >= 0.90). temperature 0 + seed recorded --
   vLLM continuous batching means temp-0 is not deterministic, so it is MEASURED;
5. prompt sha256s, served id, sampling params and concurrency (<= 4) recorded.

Sentence segmentation is ``ragstack.ingestion.chunkers.sentence_spans`` at the pinned
commit -- the repo's own segmentation, whose spans tile the text exactly, so a
``(unit, first_sentence, last_sentence)`` triple maps to a half-open ``[start, end)``
interval into the indexed text with no gaps (D1).
"""
from __future__ import annotations

import hashlib
import json
import queue
import random
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import s0_common as C

WINDOW_TOKENS = 48_000        # SS6.5
CONC = 4                      # SS6.4 rule 5 -- non-negotiable, shared host
TEMPERATURE = 0.0
MAX_TOKENS = 3000

SYSTEM = (
    "You locate evidence in biomedical articles. You answer with strict JSON and nothing "
    "else. You never invent text."
)

# PROMPT REVISION 2, made at smoke-test time BEFORE any label was retained, and
# recorded in ``label_meta.json`` with both sha256s. Revision 1 returned "no localizable
# evidence" on two dev pairs whose ABSTRACTS plainly carry the evidence (PMC3600284,
# PMC3596662 vs topic 2014_5): it foregrounded the decline verdict and the minimality
# demand and so did not faithfully express RUBRIC SS1/D2, whose lead instruction is "say
# WHERE the evidence lives". The RUBRIC is unchanged and still governs (its sha256 is
# frozen); only the prompt's rendering of it was corrected, before any label existed.
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

SPAN: a contiguous run of WHOLE sentences inside EXACTLY ONE numbered unit, given as \
(unit, first_sentence, last_sentence). A span never crosses a unit boundary. Prefer the \
SHORTEST run of sentences that carries the claim.

EVIDENCE SET: a MINIMAL collection of one or more spans that TOGETHER justify this \
article's relevance to the clinical need above. Minimal means no span can be deleted \
without losing sufficiency. A set may combine spans from different units when neither \
alone suffices (e.g. a Methods sentence naming the population plus a Results sentence \
carrying the effect).

## Worked example

Need: 56-year-old woman, shortness of breath 3 weeks after mastectomy, right calf \
tenderness, elevated D-dimer (type: diagnosis).
Article unit 3 (Results), sentence 7: "Pulmonary embolism occurred in 14 of 412 patients \
(3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep venous \
thrombosis of the calf."
Correct answer: ONE set, ONE span — unit 3, sentences 7 to 7. Not the whole paragraph; \
not sentence 6 as well "for context".

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
  {{"spans": [{{"unit": 3, "first_sentence": 7, "last_sentence": 7,
              "first_words": "<the first ten words of the span, VERBATIM>",
              "last_words": "<the last ten words of the span, VERBATIM>"}}]}}
]}}

The quoted words must be copied EXACTLY from the sentences shown below; they are checked \
automatically.

## The article
{body}
"""

AUDIT = """Below is a clinical need, an article, and a set of evidence spans a previous \
reader selected. Remove any span that is NOT strictly needed: a set must be MINIMAL, so if \
deleting a span still leaves enough to justify relevance, delete it. Do not add spans. Do \
not change indices. Return the same strict JSON format.

{original}

## The clinical need
Type: {ntype}
Summary: {summary}
Description: {description}

## The article
{body}
"""


def served_model(base: str = C.MANGO) -> str:
    with urllib.request.urlopen(base + "/v1/models", timeout=30) as r:
        return json.load(r)["data"][0]["id"]


class Scout:
    """Bounded, polite client for mango:8003. Non-reasoning: no thinking-token tax."""

    def __init__(self, conc: int = CONC):
        self.model = served_model()
        if self.model != C.SCOUT_EXPECT:
            raise SystemExit(f"mango:8003 serves {self.model!r}, not {C.SCOUT_EXPECT!r}")
        self.slots: queue.Queue = queue.Queue()
        for _ in range(conc):
            self.slots.put(1)
        self.pool = ThreadPoolExecutor(conc)
        self.lock = threading.Lock()
        self.requests = self.retries = self.failures = 0
        self.prompt_tokens = self.completion_tokens = 0
        self.seconds = 0.0

    def chat(self, prompt: str, *, max_tokens: int = MAX_TOKENS, timeout: int = 900):
        payload = {"model": self.model, "temperature": TEMPERATURE,
                   "max_tokens": max_tokens, "seed": C.SEED_LABELDUP,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}]}
        body = json.dumps(payload).encode()
        self.slots.get()
        try:
            for attempt in range(4):
                try:
                    t0 = time.time()
                    req = urllib.request.Request(
                        C.MANGO + "/v1/chat/completions", body,
                        {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        out = json.load(r)
                    dt = time.time() - t0
                    ch = out["choices"][0]
                    with self.lock:
                        self.requests += 1
                        self.prompt_tokens += out["usage"]["prompt_tokens"]
                        self.completion_tokens += out["usage"]["completion_tokens"]
                        self.seconds += dt
                    return {"text": (ch["message"].get("content") or "").strip(),
                            "finish": ch.get("finish_reason"), "ok": True}
                except Exception as e:  # noqa: BLE001
                    with self.lock:
                        self.retries += 1
                    if attempt == 3:
                        with self.lock:
                            self.failures += 1
                        return {"text": "", "ok": False,
                                "finish": f"{type(e).__name__}: {e}"}
                    time.sleep(3 * (attempt + 1))
        finally:
            self.slots.put(1)

    def stats(self):
        return {"model": self.model, "requests": self.requests, "retries": self.retries,
                "failures": self.failures, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "llm_seconds": round(self.seconds, 1), "concurrency": CONC,
                "temperature": TEMPERATURE, "seed": C.SEED_LABELDUP}


# ------------------------------------------------------------------ segmentation
def segment(text: str, units: list[dict]):
    """[(unit_i, unit_title, [(sent_idx, start, end, sent_text)])] over the INDEXED text."""
    from ragstack.ingestion.chunkers import sentence_spans
    out = []
    for u in units:
        seg = text[u["start_char"]:u["end_char"]]
        sp = sentence_spans(seg)
        sents = [(k, u["start_char"] + a, u["start_char"] + b, seg[a:b].strip())
                 for k, (a, b) in enumerate(sp) if seg[a:b].strip()]
        out.append((u["i"], u["title"] or u["cls"], sents))
    return out


def render(seg, order=None) -> str:
    idx = list(range(len(seg))) if order is None else order
    parts = []
    for j in idx:
        ui, title, sents = seg[j]
        parts.append(f"### UNIT {ui}: {title or '(untitled)'}")
        parts.extend(f"[{k}] {s}" for k, _a, _b, s in sents)
    return "\n".join(parts)


def _words(s: str, n: int, last: bool = False) -> str:
    w = s.split()
    return " ".join(w[-n:] if last else w[:n])


def _norm_map(text: str):
    """Whitespace-collapsed lowercase text plus a map back to original char offsets."""
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def _find(nd: str, q: str, prefer_from: int = -1) -> tuple[int, bool]:
    """Return (position, ambiguous). Prefers an occurrence at/after ``prefer_from``."""
    if not q:
        return -1, False
    hits = []
    st = nd.find(q)
    while st != -1 and len(hits) < 50:
        hits.append(st)
        st = nd.find(q, st + 1)
    if not hits:
        return -1, False
    if prefer_from >= 0:
        near = [h for h in hits if h >= prefer_from]
        if near:
            return min(near, key=lambda h: h - prefer_from), len(hits) > 1
    return hits[0], len(hits) > 1


def parse_and_verify(raw: str, seg, text: str):
    """Quote-anchored span recovery. Returns (sets, problems, stats).

    SS6.4 rule 2 says the checker "verifies substrings **against the document**". SS6.6.1
    separately names the case *quote verifies, location wrong* as ``wrong-location`` -- a
    LABEL ERROR, explicitly **not** a hallucination. This implementation therefore splits
    the two, where a first implementation conflated them:

    * **hallucinated** -- the quoted words are nowhere in the document. This is the P.5
      gate's numerator, and the span is dropped.
    * **misindexed** -- the quote IS in the document but not at the claimed
      ``(unit, first_sentence, last_sentence)``. **PINNED DECISION:** the quote is the
      thing the checker can verify, so the span is RELOCATED to the quote's actual
      position and snapped outward to whole sentences inside the one unit that contains
      it (D1). The claimed indices are treated as derived, not authoritative. The
      index-agreement rate is reported separately as a finding about the labeler.
    * **unresolvable** -- the quote is in the document but its interval crosses a unit
      boundary, or the unit cannot be identified. The span is dropped and counted.
    """
    stats = {"spans_seen": 0, "hallucinated": 0, "misindexed": 0, "index_ok": 0,
             "unresolvable": 0, "ambiguous_quote": 0, "no_last_words": 0}
    problems: list[str] = []
    txt = raw.strip()
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
    # unit and sentence tables over the SAME indexed text
    unit_bounds = []          # (unit_i, start_char, end_char)
    sent_of = []              # (unit_i, sent_i, start_char, end_char)
    for ui, _ti, sents in seg:
        if not sents:
            continue
        unit_bounds.append((ui, sents[0][1], sents[-1][2]))
        for k, s0, e0, _tx in sents:
            sent_of.append((ui, k, s0, e0))

    def locate(ch_start: int, ch_end: int):
        for ui, us, ue in unit_bounds:
            if us <= ch_start < ue:
                if ch_end > ue:
                    return None
                inside = [x for x in sent_of if x[0] == ui]
                first = next((x for x in inside if x[2] <= ch_start < x[3]), None)
                last = next((x for x in reversed(inside)
                             if x[2] < ch_end <= x[3] or x[2] <= ch_end - 1 < x[3]), None)
                if first is None or last is None or last[1] < first[1]:
                    return None
                return ui, first[1], last[1], first[2], last[3]
        return None

    sets = []
    for es in obj.get("evidence_sets", []) or []:
        spans = []
        for sp in es.get("spans", []) or []:
            stats["spans_seen"] += 1
            fw = " ".join(str(sp.get("first_words") or "").split()).lower()
            lw = " ".join(str(sp.get("last_words") or "").split()).lower()
            if not fw:
                stats["hallucinated"] += 1
                problems.append("no_first_words")
                continue
            fw = fw[:120]
            pf, amb1 = _find(nd, fw)
            if pf < 0:
                stats["hallucinated"] += 1
                problems.append("quote_not_in_document")
                continue
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
            loc = locate(cs, ce_)
            if loc is None:
                stats["unresolvable"] += 1
                problems.append("span_crosses_unit_or_unlocatable")
                continue
            ui, fi, li, s0, e0 = loc
            try:
                claimed = (int(sp["unit"]), int(sp["first_sentence"]),
                           int(sp["last_sentence"]))
            except Exception:  # noqa: BLE001
                claimed = None
            if claimed == (ui, fi, li):
                stats["index_ok"] += 1
            else:
                stats["misindexed"] += 1
                problems.append("misindexed_relocated")
            spans.append({"unit": ui, "first_sentence": fi, "last_sentence": li,
                          "start": s0, "end": e0,
                          "claimed": list(claimed) if claimed else None})
        if spans:
            sets.append({"spans": spans})
    return sets, problems, stats


# ------------------------------------------------------------------ labeling set
def labeling_set(pools: dict, qrels: dict) -> dict:
    """SS6.3: pooled (top-20 DOCUMENTS of any arm x variant, grade >= 1) + 10 seeded
    out-of-pool grade >= 1 documents per topic (the bias-bound sample)."""
    out = {}
    for t in C.DEV_TOPICS:
        rel = {d for d, g in qrels[t].items() if g >= 1}
        pooled: set[str] = set()
        for arm, pv in pools.items():
            for v in ("summary", "description"):
                seen: list[str] = []
                for d, _s, _e, _sc, _ri in pv[v][t]["reranked"]:
                    if d not in seen:
                        seen.append(d)
                    if len(seen) >= 20:
                        break
                pooled |= {d for d in seen if d in rel}
        rng = random.Random(f"{C.SEED_BIASBOUND}:{t}")   # seeded per topic; string seed so a non-numeric id cannot crash it
        rest = sorted(rel - pooled)
        sample = rng.sample(rest, min(10, len(rest)))
        out[t] = {"pooled": sorted(pooled), "sample": sorted(sample),
                  "n_rel_total": len(rel)}
    return out


def main() -> None:
    import stage1_common as S
    S.pin_repo()

    rub = C.DESIGN / "RUBRIC-evidence.md"
    assert rub.exists(), "P.5: no labeling call may be issued before the rubric exists"
    rub_hash = C.sha256_file(rub)
    print("rubric sha256:", rub_hash, flush=True)

    qrels = json.loads((C.WORK / "qrels_all.json").read_text())
    pools = {a: json.loads((C.WORK / f"pool_{a}.json").read_text())
             for a in C.INDEX_KEYS if (C.WORK / f"pool_{a}.json").exists()}
    lset = labeling_set(pools, qrels)
    C.atomic_json(C.WORK / "labeling_set.json", lset)
    pairs = [(t, d, "pooled") for t in C.DEV_TOPICS for d in lset[t]["pooled"]]
    pairs += [(t, d, "sample") for t in C.DEV_TOPICS for d in lset[t]["sample"]]
    print("pairs to label:", len(pairs),
          {t: len(lset[t]["pooled"]) for t in C.DEV_TOPICS}, flush=True)

    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
    tops = json.loads((C.CDS / "topics_merged.json").read_text())
    gt = C.GenTokenizer()

    scout = Scout()
    rng = random.Random(C.SEED_LABELDUP)
    dup_pairs = set(rng.sample(range(len(pairs)), max(1, round(0.10 * len(pairs)))))
    rng2 = random.Random(C.SEED_LABELDUP + 1)
    audit_pairs = set(rng2.sample(range(len(pairs)), max(1, round(0.10 * len(pairs)))))

    results: list[dict] = []
    lock = threading.Lock()
    t0 = time.time()

    def one(i_tdk):
        i, (t, d, kind) = i_tdk
        text, us = docs[d], units[d]
        seg = segment(text, us)
        f = tops[t]["fields"]
        # SS6.5 windowing: split into unit groups of <= 48k generator tokens
        groups, cur, curtok = [], [], 0
        for j, (_ui, _ti, sents) in enumerate(seg):
            n = gt.count(text[us[j]["start_char"]:us[j]["end_char"]])
            if cur and curtok + n > WINDOW_TOKENS:
                groups.append(cur)
                cur, curtok = [], 0
            cur.append(j)
            curtok += n
        if cur:
            groups.append(cur)
        windowed = len(groups) > 1

        allsets, problems, raws = [], [], []
        vstats: dict[str, int] = {}
        for g in groups:
            body = render(seg, g)
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=body)
            r = scout.chat(p)
            raws.append(r["text"])
            sets, probs, stx = parse_and_verify(r["text"], seg, text)
            # SS6.4 rule 2, PINNED reading: a re-prompt is issued whenever ANY span failed
            # verification -- not only when every set died -- so a partial quote failure
            # cannot be silently absorbed. A failed SPAN is discarded and never becomes a
            # unit; the PAIR is dropped only when no verified span survives the one
            # re-prompt. The hallucinated-span rate counts every failed span across BOTH
            # attempts, so the gate cannot be flattered by a successful retry.
            probs_all = list(probs)
            # SS6.4 rule 2, PINNED: the ONE re-prompt is triggered by a verification
            # FAILURE -- a quote that is not in the document, or one that cannot be snapped
            # to a single unit. A merely MISINDEXED span is repaired by relocation (see
            # parse_and_verify) and is not a failure, so it does not trigger a re-prompt.
            failed = any(x in ("quote_not_in_document", "last_quote_not_in_document",
                               "no_first_words", "span_crosses_unit_or_unlocatable",
                               "no_json") or x.startswith("bad_json") for x in probs)
            if failed:
                r2 = scout.chat(p + "\n\nYour previous answer was rejected: the quoted "
                                    "words must appear VERBATIM in the numbered sentences "
                                    "and the indices must exist. Answer again, JSON only.")
                raws.append(r2["text"])
                sets2, probs, stx2 = parse_and_verify(r2["text"], seg, text)
                for k_, v_ in stx2.items():
                    stx[k_] = stx.get(k_, 0) + v_
                probs_all += list(probs) + ["reprompted"]
                # A span that verified against the document on attempt 1 is not made
                # invalid by a worse retry; the retry replaces attempt 1 only when it
                # actually recovered something.
                sets = sets2 if sets2 else sets
            allsets.extend(sets)
            problems.extend(probs_all)
            for k_, v_ in stx.items():
                vstats[k_] = vstats.get(k_, 0) + v_

        rec = {"topic": t, "docno": d, "kind": kind, "grade": qrels[t].get(d, 0),
               "sets": allsets, "problems": problems, "windowed": windowed,
               "vstats": vstats, "raws": raws,
               "n_quote_fail": vstats.get("hallucinated", 0),
               "n_spans": sum(len(s["spans"]) for s in allsets),
               "n_units": len(seg), "doc_chars": len(text),
               "dropped": bool(problems) and not allsets}

        if i in dup_pairs:                               # SS6.4 rule 4: self-consistency
            order = list(range(len(seg)))
            random.Random(C.SEED_LABELDUP + i).shuffle(order)
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render(seg, order))
            r = scout.chat(p)
            s2, _, _s = parse_and_verify(r["text"], seg, text)
            rec["dup_sets"] = s2
        if i in audit_pairs and allsets:                 # SS6.4 rule 3: minimality audit
            p = AUDIT.format(original=json.dumps({"evidence_sets": allsets}),
                             ntype=tops[t]["type"], summary=f["summary"],
                             description=f["description"], body=render(seg))
            r = scout.chat(p, max_tokens=2000)
            s3, _, _s = parse_and_verify(r["text"], seg, text)
            rec["audit_sets"] = s3
        with lock:
            results.append(rec)
            if len(results) % 25 == 0:
                print(f"  {len(results)}/{len(pairs)}  {time.time()-t0:.0f}s "
                      f"{scout.prompt_tokens/1e6:.1f}M prompt tok", flush=True)
        return rec

    list(scout.pool.map(one, list(enumerate(pairs))))
    with open(C.WORK / "labels.jsonl", "w") as f:
        for r in sorted(results, key=lambda x: (x["topic"], x["docno"])):
            f.write(json.dumps(r) + "\n")
    C.atomic_json(C.WORK / "label_meta.json", {
        "rubric_sha256": rub_hash,
        "rubric_path": str(rub),
        "prompt_sha256": C.sha256_text(PROMPT),
        "prompt_revision": 2,
        "prompt_revision_reason":
            "revision 1 returned 'no localizable evidence' on two dev pairs whose "
            "abstracts plainly carry the evidence (PMC3600284, PMC3596662 vs 2014_5); it "
            "did not faithfully express RUBRIC SS1/D2. Revised at smoke-test time, BEFORE "
            "any label was retained. The RUBRIC is unchanged and its sha256 is unmoved.",
        "audit_prompt_sha256": C.sha256_text(AUDIT),
        "system_sha256": C.sha256_text(SYSTEM),
        "scout": scout.stats(), "n_pairs": len(pairs),
        "dup_indices": sorted(dup_pairs), "audit_indices": sorted(audit_pairs),
        "window_tokens": WINDOW_TOKENS,
        "sentence_segmentation": "ragstack.ingestion.chunkers.sentence_spans @ 55a0fc2",
        "wall_seconds": round(time.time() - t0, 1)})
    print("labels written:", len(results), json.dumps(scout.stats()), flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(C.STAGE1))
    main()
