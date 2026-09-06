"""Stage 0b' step 2, **second attempt** -- the "r3.1" relabel of the development set.

`RESULTS-stage0b-relabel.md` (PR #501) ran `SPEC-confirmation-run-r3.md` SS3.7 and stopped:
neither judge passed self-consistency >= 0.90, and 54 of Scout's 58 hallucinated spans were
the *closing* ten-word anchor. r3 SS10 items 2 and 3 record the owner's two decisions; this
module implements both.

1. **Whole-sentence anchors** (decision C, r3 SS10 item 3). The model quotes the **whole
   first sentence** and the **whole last sentence** of each span, verbatim -- not their
   first/last ten words. A quote is located by exact match, then by normalised
   (whitespace-collapsed, case-folded) match, then -- if the full sentence still does not
   locate -- by its **first eight and last eight words**, which must BOTH land inside ONE
   sentence of the segmented document; that sentence is the located sentence. A quote that
   fails all three is a hallucinated span (the existing P.5 gate, <= 0.05).
2. **Five presentations per pair** (r3 SS3.7 item 6 / SS10 item 2 (a)). Every pair of the
   development labeling set is presented **five times at temperature 0**, each with the
   document's units in a different seeded order -- seed ``SEED_LABELDUP + 100*k + i`` for
   presentation ``k`` and pair index ``i``; presentation 0 is the natural order. This is
   Stage 0's own 10 %-duplicate mechanism (SS6.4 rule 4) applied to every pair, so the
   union of a judge's five readings can be built and its **saturation** measured.
   Temperature is **0** throughout: the variation is the presentation, not the sampler.

Everything else is r3's, imported from `s0_label_r3.py` rather than re-declared: the two
judges and their politeness contract, the ``<think>`` stripper, the quote-primary rendering,
the D1 outward snap (and its cross-unit multi-span reading), the SS6.5 48,000-token
windowing, the labeling set, "no localizable evidence" as a legal verdict, and the assertion
that the sentence segmenter is byte-identical to Stage 0's.

**Zero store writes.** The only endpoints contacted are `mango:8003` and `mango:8004`.
No Qdrant / Elasticsearch / Neo4j / tenant-API / SFR / reranker client is constructed
anywhere in this file or in `s0_label_r3.py`. GPUs 6 and 7 are untouched (mango is a remote
host and nothing here selects a device). Outputs go to ``$STAGE0_BIG/work/r31/`` only.

Usage::

    python3 s0_label_r31.py --selftest                       # offline; no endpoint
    python3 s0_label_r31.py --judge scout --limit 20 --presentations 2 --tag smoke
    python3 s0_label_r31.py --judge scout                    # 308 pairs x 5 presentations
    python3 s0_label_r31.py --merge-manifest                 # label-manifest-r31.json
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import pathlib
import random
import subprocess
import sys
import threading
import time

# The Phase-0 helper modules (``stage1_common``, ``pilot_common``) live beside the working
# copy of the analysis tree, not in the repo; ``s0_common`` imports both at module load.
_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                                    # noqa: E402
from s0_label import _find, _norm_map, segment                           # noqa: E402
from s0_label_r3 import (CONC, JUDGES, SYSTEM, TEMPERATURE, WINDOW_TOKENS,  # noqa: E402
                         Judge, _find_preferring, _normtitle, _snap, _tables,
                         build_pairs, dup_indices, render_r3)
import s0_label_r3 as R3M                                                # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RUBRIC = HERE.parent / "design" / "RUBRIC-evidence.md"
TOPICS = R3M.TOPICS
R31 = C.WORK / "r31"
R31.mkdir(parents=True, exist_ok=True)

N_PRESENTATIONS = 5
PRESENTATION_SEED_STRIDE = 100      # seed = SEED_LABELDUP + 100*k + pair_index

# ---------------------------------------------------------------------------- prompt
# PROMPT REVISION 3.1 -- whole-sentence anchors. Derived from `s0_label_r3.PROMPT`
# (revision 3) by changing ONLY the span identifier: the two ten-word anchors become two
# whole-sentence quotes. The clinical framing, the definitions of SPAN and EVIDENCE SET,
# the worked example's content, the enumeration rule and the "no localizable evidence"
# clause are carried over from revision 3 unchanged, so that a difference between the r3
# labels and these ones is attributable to the ANCHOR change and not to a rewritten task.
# The RUBRIC is unchanged and its sha256 is unmoved (the rubric's SS6 output block is
# superseded by this one; the amendment text is proposed in the write-up, not applied --
# the rubric is frozen and hashed).
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
You identify a span by QUOTING it — never by numbering it. Every sentence of the article \
is printed below on its own line. A span is given as three verbatim strings:

* `unit_title` — the title of the unit the span sits under, copied exactly as printed;
* `first_sentence` — the span's FIRST sentence, quoted IN FULL: copy the whole printed \
line, from its first character to its final punctuation;
* `last_sentence` — the span's LAST sentence, quoted IN FULL, the same way. If the span is \
a SINGLE sentence, repeat that same sentence here.

Copy each sentence whole. Do not shorten it, do not stop in the middle, do not join two \
lines, do not paraphrase and do not re-punctuate. There are no unit numbers and no \
sentence numbers in this article, and you must not invent any. Prefer the SHORTEST run of \
sentences that carries the claim — most spans are ONE sentence, and then `first_sentence` \
and `last_sentence` are that one sentence.

EVIDENCE SET: a MINIMAL collection of one or more spans that TOGETHER justify this \
article's relevance to the clinical need above. Minimal means no span can be deleted \
without losing sufficiency. A set may combine spans from different units when neither \
alone suffices (e.g. a Methods sentence naming the population plus a Results sentence \
carrying the effect).

## Worked example

Need: 56-year-old woman, shortness of breath 3 weeks after mastectomy, right calf \
tenderness, elevated D-dimer (type: diagnosis).
Article unit titled "Results" contains the printed line: "Pulmonary embolism occurred in \
14 of 412 patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent \
deep venous thrombosis of the calf."
Correct answer: ONE set, ONE span, the sentence quoted in full on both anchors —
`{{"unit_title": "Results", "first_sentence": "Pulmonary embolism occurred in 14 of 412 \
patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep venous \
thrombosis of the calf.", "last_sentence": "Pulmonary embolism occurred in 14 of 412 \
patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep venous \
thrombosis of the calf."}}`
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
              "first_sentence": "<the span's first sentence, IN FULL, VERBATIM>",
              "last_sentence": "<the span's last sentence, IN FULL, VERBATIM>"}}]}}
]}}

Every quoted sentence must be copied EXACTLY from the article below, character for \
character and whole; they are located automatically by string match, and a sentence that \
is not in the article is discarded and counted against you.

## The article
{body}
"""

REPROMPT = ("\n\nYour previous answer was rejected: every quoted sentence must appear "
            "VERBATIM in the article text above — the WHOLE printed line, copied character "
            "for character from its first character to its final punctuation. Answer "
            "again, JSON only, with sentences you can see printed in the article.")

FAIL_PROBLEMS = ("quote_not_in_document", "last_quote_not_in_document", "no_first_words",
                 "span_unlocatable", "no_json")


# ---------------------------------------------------------------------------- location
def _sent_at(sent_starts: list[int], sent_of: list[tuple], off: int):
    """The sentence (ui, k, start, end) containing original char offset ``off``, else None.

    ``sent_of`` is in document order and ``sent_starts`` is its list of start offsets, so a
    bisect finds the candidate and one containment test confirms it. Offsets that fall in
    the gap between two sentences (inter-sentence whitespace, a unit's untokenised tail)
    belong to no sentence and return ``None``.
    """
    j = bisect.bisect_right(sent_starts, off) - 1
    if j < 0:
        return None
    s = sent_of[j]
    return s if s[2] <= off < s[3] else None


class _Locator:
    """Locate a whole-sentence quote in one indexed document.

    Three ladders, in order, exactly as r3 SS10 item 3 states them:

    1. **exact** -- the quoted string appears in the document byte for byte;
    2. **normalised** -- it appears in the whitespace-collapsed, case-folded map
       (`s0_label._norm_map`), which is the path r3 already used;
    3. **eight-word** -- the full sentence does not locate, but its first eight words and
       its last eight words both do, and both land inside ONE sentence of the segmented
       document. That sentence is the located sentence, whole.

    A quote that survives none of the three is a hallucinated span. The unit title is used
    only to *disambiguate* a quote that occurs more than once; it never overrides a match.
    """

    def __init__(self, text: str, seg):
        self.text = text
        self.nd, self.imap = _norm_map(text)
        self.unit_bounds, self.sent_of, self.title_ranges = _tables(seg)
        self.sent_starts = [s[2] for s in self.sent_of]

    def n_of(self, o: int) -> int:
        return bisect.bisect_left(self.imap, o)

    def o_of(self, n: int) -> int:
        return self.imap[min(n, len(self.imap) - 1)]

    def ranges_norm(self, title: str):
        return [(self.n_of(a), self.n_of(b)) for a, b in self.title_ranges.get(title, ())]

    def _exact(self, q_raw: str, orig_ranges):
        """Byte-for-byte hits in the ORIGINAL text, preferring the named unit's range."""
        hits, st = [], self.text.find(q_raw)
        while st != -1 and len(hits) < 200:
            hits.append(st)
            st = self.text.find(q_raw, st + 1)
        if not hits:
            return None
        for a, b in orig_ranges or ():
            inside = [h for h in hits if a <= h < b]
            if inside:
                return inside[0], len(hits) > 1
        return hits[0], len(hits) > 1

    def locate(self, q_raw: str, title: str, prefer_from_norm: int = -1) -> dict | None:
        """Return {start, end, norm_pos, mode, ambiguous} in ORIGINAL char offsets."""
        qn = " ".join((q_raw or "").split()).lower()
        if not qn:
            return None
        orig_ranges = self.title_ranges.get(title, ())
        rn = self.ranges_norm(title)

        got = self._exact(q_raw, orig_ranges)
        if got is not None:
            s, amb = got
            return {"start": s, "end": s + len(q_raw), "norm_pos": self.n_of(s),
                    "mode": "exact", "ambiguous": amb}

        if prefer_from_norm >= 0:
            p, amb = _find(self.nd, qn, prefer_from=prefer_from_norm)
            in_title = False
        else:
            p, amb, in_title = _find_preferring(self.nd, qn, rn)
        if p >= 0:
            return {"start": self.o_of(p), "end": self.o_of(p + len(qn) - 1) + 1,
                    "norm_pos": p, "mode": "normalised", "ambiguous": amb,
                    "in_title": in_title}

        # ladder 3: first eight and last eight words, both inside ONE sentence
        w = qn.split()
        if len(w) <= 8:
            return None                     # first8 == last8 == the quote, already failed
        f8, l8 = " ".join(w[:8]), " ".join(w[-8:])
        if prefer_from_norm >= 0:
            p1, amb1 = _find(self.nd, f8, prefer_from=prefer_from_norm)
        else:
            p1, amb1, _ = _find_preferring(self.nd, f8, rn)
        if p1 < 0:
            return None
        p2, amb2 = _find(self.nd, l8, prefer_from=p1)
        if p2 < 0:
            return None
        a_off = self.o_of(p1)
        b_off = self.o_of(p2 + len(l8) - 1)
        sa = _sent_at(self.sent_starts, self.sent_of, a_off)
        sb = _sent_at(self.sent_starts, self.sent_of, b_off)
        if sa is None or sb is None or (sa[0], sa[1]) != (sb[0], sb[1]):
            return None                     # the two halves are not in one sentence
        return {"start": sa[2], "end": sa[3], "norm_pos": p1, "mode": "eight_word",
                "ambiguous": bool(amb1 or amb2)}


def _blank_stats() -> dict:
    return {"spans_seen": 0, "spans_emitted": 0, "hallucinated": 0, "unlocatable": 0,
            "ambiguous_quote": 0, "no_last_words": 0, "last_same_as_first": 0,
            "split_across_units": 0, "title_landed": 0, "title_elsewhere": 0,
            "title_unknown": 0, "legacy_field_names": 0,
            "first_exact": 0, "first_normalised": 0, "first_eight_word": 0,
            "last_exact": 0, "last_normalised": 0, "last_eight_word": 0}


def parse_and_verify_r31(raw: str, seg, text: str):
    """Whole-sentence-anchor span recovery. Returns (sets, problems, stats).

    * **hallucinated** -- a quoted sentence locates nowhere under any of the three ladders.
      The span is dropped and the P.5 gate's numerator is incremented; ``problems`` records
      WHICH anchor failed (``quote_not_in_document`` = the first-sentence quote,
      ``last_quote_not_in_document`` = the last-sentence quote), because that split is the
      direct test of the whole-sentence decision against #501's 54/58.
    * **unlocatable** -- the quotes located but the snapped interval covers no whole
      sentence of any unit. Dropped, counted separately, NOT a hallucination.
    * **split_across_units** -- the span's interval crosses a unit boundary; it becomes a
      multi-span set rather than being discarded (r3's reading of D1/D2, kept).
    """
    stats = _blank_stats()
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

    L = _Locator(text, seg)
    sets = []
    for es in obj.get("evidence_sets", []) or []:
        spans = []
        for sp in es.get("spans", []) or []:
            if not isinstance(sp, dict):
                continue
            stats["spans_seen"] += 1
            legacy = "first_sentence" not in sp and "first_words" in sp
            if legacy:
                stats["legacy_field_names"] += 1
            fq = str(sp.get("first_sentence") or sp.get("first_words") or "")
            lq = str(sp.get("last_sentence") or sp.get("last_words") or "")
            ti = _normtitle(str(sp.get("unit_title") or ""))
            if not fq.strip():
                stats["hallucinated"] += 1
                problems.append("no_first_words")
                continue
            if not L.title_ranges.get(ti):
                stats["title_unknown"] += 1

            f = L.locate(fq, ti)
            if f is None:
                stats["hallucinated"] += 1
                problems.append("quote_not_in_document")
                continue
            stats["first_" + f["mode"]] += 1
            if L.title_ranges.get(ti):
                inside = any(a0 <= f["start"] < b0 for a0, b0 in L.title_ranges[ti])
                stats["title_landed" if inside else "title_elsewhere"] += 1

            same = " ".join(lq.split()).lower() == " ".join(fq.split()).lower()
            if not lq.strip():
                stats["no_last_words"] += 1
                lo = f
            elif same:
                stats["last_same_as_first"] += 1
                lo = f
            else:
                lo = L.locate(lq, ti, prefer_from_norm=f["norm_pos"])
                if lo is None:
                    stats["hallucinated"] += 1
                    problems.append("last_quote_not_in_document")
                    continue
                stats["last_" + lo["mode"]] += 1
            if f["ambiguous"] or lo["ambiguous"]:
                stats["ambiguous_quote"] += 1

            cs, ce = f["start"], lo["end"]
            if ce <= cs:                    # the model gave the anchors out of order
                ce = max(f["end"], lo["end"])
            got = _snap(cs, ce, L.unit_bounds, L.sent_of)
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


# ---------------------------------------------------------------------------- the run
def order_for(k: int, pair_index: int, n_units: int) -> tuple[list[int], int, str]:
    """Presentation k's unit order. k = 0 is the natural order; k >= 1 is a seeded shuffle.

    The seed is ``SEED_LABELDUP + 100*k + pair_index`` (r3 SS10 item 2 (a)), which is Stage
    0's own duplicate mechanism with a per-presentation stride. Note that this makes
    presentation 1 a DIFFERENT shuffle from #501's single duplicate, which used
    ``SEED_LABELDUP + pair_index``; both are "a different seeded order", and the write-up
    records the difference.
    """
    seed = C.SEED_LABELDUP + PRESENTATION_SEED_STRIDE * k + pair_index
    o = list(range(n_units))
    if k == 0:
        return o, seed, "natural (seed recorded, not applied)"
    random.Random(seed).shuffle(o)
    return o, seed, "seeded_shuffle"


def selftest() -> dict:
    """Offline checks of the whole-sentence locator. Contacts no endpoint.

    Exercises, in order: a whole-sentence quote snaps to exactly its own sentence; a
    two-sentence span built from two whole sentences snaps to both; a quote that crosses a
    unit boundary becomes a TWO-span set; a paraphrased sentence whose first eight and last
    eight words survive is rescued by ladder 3 and lands on the right sentence; a quote
    whose first eight words are real but whose last eight come from a DIFFERENT sentence is
    NOT rescued; a quote that is nowhere is hallucinated, with the failing anchor named;
    the one-sentence conventions (``last_sentence`` omitted / equal) both work; and the
    presentation seeding is deterministic and differs between presentations.
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

    def sent(u, k):
        return seg[u][2][k][3]

    def span(u, k0, k1, title=None):
        return {"unit_title": seg[u][1] if title is None else title,
                "first_sentence": sent(u, k0), "last_sentence": sent(u, k1)}

    sets, probs, st = parse_and_verify_r31(
        json.dumps({"evidence_sets": [{"spans": [span(1, 0, 0)]}]}), seg, text)
    sp = sets[0]["spans"][0]
    assert not probs and st["hallucinated"] == 0
    assert text[sp["start"]:sp["end"]].strip() == sent(1, 0).strip(), \
        "whole-sentence quote did not snap to its own sentence"
    assert st["first_exact"] == 1 and st["last_same_as_first"] == 1
    out["one_sentence_span"] = "ok"

    u = next((i for i in range(len(seg)) if len(seg[i][2]) >= 3), None)
    if u is not None:
        sets, probs, st = parse_and_verify_r31(
            json.dumps({"evidence_sets": [{"spans": [span(u, 0, 2)]}]}), seg, text)
        sp = sets[0]["spans"][0]
        assert sp["first_sentence"] == 0 and sp["last_sentence"] == 2, \
            "multi-sentence span did not cover first..last sentence"
        out["multi_sentence_span"] = "ok"

    sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": seg[0][1], "first_sentence": seg[0][2][-1][3],
         "last_sentence": seg[1][2][0][3]}]}]}), seg, text)
    assert st["split_across_units"] == 1 and len(sets[0]["spans"]) == 2, \
        "cross-unit quote was not split into a multi-span set"
    out["cross_unit_split"] = "ok"

    # ladder 3: a sentence the model mangled in the MIDDLE but copied at both ends.
    src = next((sent(1, k) for k in range(len(seg[1][2]))
                if len(sent(1, k).split()) >= 20), None)
    if src:
        w = src.split()
        mangled = " ".join(w[:8] + ["QQQ", "ZZZ", "WWW"] + w[-8:])
        sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
            {"unit_title": seg[1][1], "first_sentence": mangled,
             "last_sentence": mangled}]}]}), seg, text)
        assert st["hallucinated"] == 0 and st["first_eight_word"] == 1, \
            "eight-word ladder did not rescue a mangled middle"
        assert text[sets[0]["spans"][0]["start"]:sets[0]["spans"][0]["end"]].strip() \
            == src.strip(), "eight-word ladder landed on the wrong sentence"
        out["eight_word_rescue"] = "ok"

        # ...but only when both halves are in the SAME sentence.
        other = next((sent(1, k) for k in range(len(seg[1][2]))
                      if sent(1, k) != src and len(sent(1, k).split()) >= 10), None)
        if other:
            crossed = " ".join(w[:8] + ["QQQ"] + other.split()[-8:])
            sets2, probs2, st2 = parse_and_verify_r31(
                json.dumps({"evidence_sets": [{"spans": [
                    {"unit_title": seg[1][1], "first_sentence": crossed,
                     "last_sentence": crossed}]}]}), seg, text)
            assert st2["hallucinated"] == 1 and sets2 == [] \
                and probs2 == ["quote_not_in_document"], \
                "eight-word ladder rescued a quote whose halves span two sentences"
            out["eight_word_rejects_cross_sentence"] = "ok"

    sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": seg[1][1], "first_sentence": sent(1, 0)}]}]}), seg, text)
    assert sets and st["no_last_words"] == 1 and not probs, \
        "an omitted last_sentence was not accepted for a one-sentence span"
    out["last_sentence_omitted"] = "ok"

    sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": "Results",
         "first_sentence": "Zzq wubble frotz nine hundred and never appears at all here.",
         "last_sentence": "Nor does this one, anywhere in the article, ever."}]}]}),
        seg, text)
    assert sets == [] and st["hallucinated"] == 1 \
        and probs == ["quote_not_in_document"], "hallucination not caught (first anchor)"
    out["hallucination_first_anchor"] = "ok"

    sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": seg[1][1], "first_sentence": sent(1, 0),
         "last_sentence": "Zzq wubble frotz nine hundred and never appears at all here."}
    ]}]}), seg, text)
    assert sets == [] and st["hallucinated"] == 1 \
        and probs == ["last_quote_not_in_document"], \
        "hallucination not caught (last anchor), or attributed to the wrong anchor"
    out["hallucination_last_anchor"] = "ok"

    # the legacy field names still parse, and are counted rather than silently accepted
    sets, probs, st = parse_and_verify_r31(json.dumps({"evidence_sets": [{"spans": [
        {"unit_title": seg[1][1], "first_words": sent(1, 0)}]}]}), seg, text)
    assert sets and st["legacy_field_names"] == 1
    out["legacy_field_names"] = "ok"

    o0, s0_, k0 = order_for(0, 7, 9)
    o1, s1_, k1 = order_for(1, 7, 9)
    o1b, _, _ = order_for(1, 7, 9)
    assert o0 == list(range(9)) and k0.startswith("natural")
    assert o1 == o1b and k1 == "seeded_shuffle" and sorted(o1) == list(range(9))
    assert s1_ - s0_ == PRESENTATION_SEED_STRIDE
    assert order_for(1, 7, 9)[0] != order_for(2, 7, 9)[0] or 9 <= 2
    out["presentation_seeding"] = "ok"

    assert R3M.strip_think('<think>x</think>\n{"evidence_sets": []}')[0] \
        == '{"evidence_sets": []}'
    out["think_stripper"] = "ok (r3's, imported unchanged)"
    return out


def merge_manifest() -> pathlib.Path:
    """Fold the per-judge manifests into the single ``label-manifest-r31.json``."""
    per = {}
    for j in sorted(JUDGES):
        p = R31 / f"label-manifest-r31-{j}.json"
        if p.exists():
            per[j] = json.loads(p.read_text())
    smoke = {}
    for j in sorted(JUDGES):
        p = R31 / f"label-manifest-r31-{j}-smoke.json"
        if p.exists():
            smoke[j] = json.loads(p.read_text())
    tot = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "retries": 0,
           "failures": 0, "truncated_responses": 0, "llm_seconds": 0.0}
    for m in list(per.values()) + list(smoke.values()):
        for k in tot:
            tot[k] += (m.get("stats") or {}).get(k, 0)
    out = {
        "protocol": ("SPEC-confirmation-run-r3.md SS3.7 item 1 (whole-sentence anchors, "
                     "SS10 item 3) + item 6 / SS10 item 2 (a) (five presentations per pair)"),
        "prompt_revision": "3.1",
        "prompt_sha256": C.sha256_text(PROMPT),
        "system_sha256": C.sha256_text(SYSTEM),
        "reprompt_sha256": C.sha256_text(REPROMPT),
        "rubric_sha256": C.sha256_file(RUBRIC), "rubric_path": str(RUBRIC),
        "n_presentations": N_PRESENTATIONS,
        "presentation_seed": "SEED_LABELDUP + 100*k + pair_index; k=0 is the natural order",
        "temperature": TEMPERATURE, "concurrency_per_endpoint": CONC,
        "window_tokens": WINDOW_TOKENS, "dev_topics": C.DEV_TOPICS,
        "judges": {j: {"endpoint": JUDGES[j]["base"],
                       "served_model": (m.get("stats") or {}).get("served_model"),
                       "expected_model": JUDGES[j]["expect"],
                       "max_tokens": JUDGES[j]["max_tokens"],
                       "reasoning": JUDGES[j]["reasoning"],
                       "records": m.get("n_records_total"),
                       "pairs": m.get("n_pairs_total"),
                       "stats": m.get("stats"), "wall_seconds": m.get("wall_seconds"),
                       "started_utc": m.get("started_utc"),
                       "finished_utc": m.get("finished_utc")}
                   for j, m in per.items()},
        "smoke": {j: {"stats": m.get("stats"), "wall_seconds": m.get("wall_seconds"),
                      "n_records_total": m.get("n_records_total")}
                  for j, m in smoke.items()},
        "totals_including_smoke": tot,
        "endpoints_contacted": [JUDGES[j]["base"] for j in sorted(JUDGES)],
        "stores_contacted": ("none — no Qdrant/Elasticsearch/Neo4j/tenant-API client is "
                             "constructed in s0_label_r31.py, s0_label_r3.py or "
                             "s0_labelgates_r31.py"),
    }
    C.atomic_json(R31 / "label-manifest-r31.json", out)
    return R31 / "label-manifest-r31.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks of the locator; contacts no endpoint")
    ap.add_argument("--merge-manifest", action="store_true")
    ap.add_argument("--judge", choices=sorted(JUDGES))
    ap.add_argument("--limit", type=int, default=0, help="smoke: first N pairs only")
    ap.add_argument("--presentations", type=int, default=N_PRESENTATIONS)
    ap.add_argument("--tag", default="", help="output suffix, e.g. 'smoke'")
    ap.add_argument("--conc", type=int, default=CONC)
    args = ap.parse_args()
    if args.selftest:
        print(json.dumps(selftest(), indent=1))
        return
    if args.merge_manifest:
        print(merge_manifest())
        return
    if not args.judge:
        raise SystemExit("--judge is required (or --selftest / --merge-manifest)")
    if args.conc > CONC:
        raise SystemExit(f"concurrency {args.conc} > {CONC} — mango is a shared host")

    assert RUBRIC.exists(), "P.5: no labeling call before the rubric exists"
    rub_hash = C.sha256_file(RUBRIC)
    seg_diff = subprocess.run(
        ["git", "-C", C.REPO, "diff", "--stat", f"{C.EXPECT_COMMIT}..HEAD",
         "--", "python/ragstack/ingestion/chunkers.py"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert not seg_diff, f"chunkers.py moved since {C.EXPECT_COMMIT}: {seg_diff}"

    pairs, lset, qrels = build_pairs()
    dup = dup_indices(len(pairs))               # #501's 31 pairs, for the comparable read
    idx = list(range(len(pairs)))
    if args.limit:
        idx = idx[:args.limit]
    # presentation-major: a run stopped early still has COMPLETE presentations 0..j-1 for
    # every pair, which is what the saturation curve needs.
    todo = [(i, k) for k in range(args.presentations) for i in idx]

    suffix = f"-{args.tag}" if args.tag else ""
    out_path = R31 / f"labels-r31-{args.judge}{suffix}.jsonl"
    raw_path = R31 / f"raw-r31-{args.judge}{suffix}.jsonl"
    man_path = R31 / f"label-manifest-r31-{args.judge}{suffix}.json"

    done: set[tuple[str, str, int]] = set()
    if out_path.exists():                                    # checkpointed / resumable
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["topic"], r["docno"], r["presentation"]))
    todo = [(i, k) for i, k in todo
            if (pairs[i][0], pairs[i][1], k) not in done]
    print(f"judge={args.judge} pairs={len(idx)} presentations={args.presentations} "
          f"records_to_run={len(todo)} already_done={len(done)}", flush=True)

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
    tok_lock = threading.Lock()
    t0 = time.time()
    fout = open(out_path, "a")
    fraw = open(raw_path, "a")
    n_done = [0]

    def one(ik):
        i, k = ik
        t, d, kind = pairs[i]
        text, us = docs[d], units[d]
        seg = segment(text, us)
        f = tops[t]["fields"]
        order, seed, order_kind = order_for(k, i, len(seg))

        groups, cur, curtok = [], [], 0                       # SS6.5 windowing, in ORDER
        for j in order:
            with tok_lock:
                n = gt.count(text[us[j]["start_char"]:us[j]["end_char"]])
            if cur and curtok + n > WINDOW_TOKENS:
                groups.append(cur)
                cur, curtok = [], 0
            cur.append(j)
            curtok += n
        if cur:
            groups.append(cur)

        allsets, problems, raws, rawfull, finishes = [], [], [], [], []
        vstats: dict[str, int] = {}
        for g in groups:
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render_r3(seg, g))
            r = judge.chat(p)
            raws.append(r["text"])
            rawfull.append({"content": r["raw"], "reasoning": r.get("reasoning", "")})
            finishes.append(r["finish"])
            sets, probs, stx = parse_and_verify_r31(r["text"], seg, text)
            probs_all = list(probs)
            failed = any(x in FAIL_PROBLEMS or x.startswith("bad_json") for x in probs)
            if failed:                                   # SS6.4 rule 2: exactly ONE retry
                r2 = judge.chat(p + REPROMPT)
                raws.append(r2["text"])
                rawfull.append({"content": r2["raw"],
                                "reasoning": r2.get("reasoning", "")})
                finishes.append(r2["finish"])
                sets2, probs2, stx2 = parse_and_verify_r31(r2["text"], seg, text)
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
               "finish_reasons": finishes, "pair_index": i,
               "presentation": k, "unit_order_seed": seed, "unit_order": order_kind,
               "in_501_duplicate_31": i in dup}

        with lock:
            fout.write(json.dumps(rec) + "\n")
            fout.flush()
            fraw.write(json.dumps({"topic": t, "docno": d, "presentation": k,
                                   "raws": rawfull}) + "\n")
            fraw.flush()
            n_done[0] += 1
            if n_done[0] % 25 == 0:
                el = time.time() - t0
                print(f"  {n_done[0]}/{len(todo)}  {el:.0f}s  "
                      f"{el / n_done[0]:.2f}s/record  "
                      f"{judge.prompt_tokens / 1e6:.1f}M prompt tok", flush=True)
        return rec

    list(judge.pool.map(one, todo))
    fout.close()
    fraw.close()
    wall = round(time.time() - t0, 1)
    n_recs = sum(1 for line in out_path.read_text().splitlines() if line.strip())
    C.atomic_json(man_path, {
        "judge": args.judge,
        "protocol": ("SPEC-confirmation-run-r3.md SS3.7 item 1 (whole-sentence anchors) + "
                     "item 6 (five presentations per pair)"),
        "rubric_sha256": rub_hash, "rubric_path": str(RUBRIC),
        "prompt_sha256": prompt_sha, "prompt_revision": "3.1",
        "system_sha256": C.sha256_text(SYSTEM),
        "reprompt_sha256": C.sha256_text(REPROMPT),
        "stats": judge.stats(), "n_pairs_total": len(idx),
        "n_presentations": args.presentations,
        "n_records_total": n_recs, "n_records_run": len(todo),
        "n_records_preexisting": len(done),
        "presentation_seed_formula": "SEED_LABELDUP + 100*k + pair_index",
        "dup_indices_501": sorted(dup), "window_tokens": WINDOW_TOKENS,
        "dev_topics": C.DEV_TOPICS,
        "sentence_segmentation":
            f"ragstack.ingestion.chunkers.sentence_spans @ {C.EXPECT_COMMIT[:7]} "
            f"(unchanged at repo HEAD, asserted)",
        "labels_path": str(out_path), "raw_path": str(raw_path),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wall_seconds": wall,
        "seconds_per_record": round(wall / max(len(todo), 1), 3)})
    print(f"labels written: {out_path} (+{len(todo)} this run)  wall={wall}s",
          json.dumps(judge.stats()), flush=True)


if __name__ == "__main__":
    main()
