#!/usr/bin/env python3
"""Build the alias-study (OA-1000) presentation deck from the committed public artifacts.

    python3 -I docs/plans/results/alias-study-2026-11/presentation/make_deck.py

Writes ONE self-contained HTML file, docs/presentations/alias-study-2026-11.html (inline CSS, JS
and SVG; the only external request is the Google Fonts stylesheet, with system fallbacks).

Every measured number on a slide is read here from a committed JSON artifact of the record:

  artifacts/results-stage-A2.public.json   Layer 1 (binding stage-A re-run, D-1)
  artifacts/results-stage-B.public.json    Layer 2, every cell, the placebo leg
  artifacts/h1b-gold.public.json           H1b's gold-only display (D-2), provenance
  artifacts/disagreement-tags.json         the owner's tags on the 19 spot-check disagreements
  artifacts/power-v4.json                  H2 power, added-paper count
  control/artifacts/control-judge.json     the exploratory chunk-boundary control
  artifacts/record-recount.json            corpus, pool, waves, record set, drafter and verifier counts
  control/artifacts/control-check.json     the stage-A windows re-cut from the record text (provenance slide)

The only typed values are pre-registration constants (thresholds, the judge line-up, rendering caps),
each cited to its prereg section, and the provenance pointers in RECORD below (git commits of the
merged record, which no JSON artifact carries). The prereg sha256 is computed from the file itself and
checked against the frozen value. Standard library only; safe under `python3 -I`.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                      # docs/plans/results/alias-study-2026-11
DOCS = STUDY.parents[2]                  # docs/
OUT = DOCS / "presentations" / "alias-study-2026-11.html"
STUDY_REL = "docs/plans/results/alias-study-2026-11"
REPO = "https://github.com/wilke/ragstack"

# Provenance pointers that live in git, not in a JSON artifact (merge commits of the record's PRs).
RECORD = dict(
    results_merge="a349629",       # PR #954, RESULTS.md + DEVIATIONS D-3 merged 2026-11-05
    control_merge="95cf2b9",       # PR #953, chunk-boundary control addendum merged 2026-11-05
    prereg_freeze_commit="e6ecfa6",  # prereg/README.md status line
    prereg_frozen_at="2026-11-04T05:39:34Z",  # prereg/README.md status line
    prereg_sha256="d54f4201a4d1281dd895adeca292162999b55109e518129c87f61af473e0822e",  # ditto
    prereg_tag="prereg-alias-study-2026-11",
)

# Pre-registration constants (not measurements), each with its section.
PREREG = dict(
    judge_validity_rule=0.90,       # §13.5: re-weighted and answers-stratum agreement below 0.90 fires the flag
    record_validity_rule=0.85,      # §13.4
    alpha=0.05,                     # §3.1, one-sided, per family
    glossary_line_cap=12,           # §5.3 / R27: 12 lines, one per alias
    glossary_char_cap=1200,         # §5.3 / R27: 1,200 characters
    judge_c="Qwen3.6-35B-A3B (n 5, T 0.7, thinking)",  # §6.3
    judge_e="Qwen3.8-27B (n 1)",                        # §6.3
    judge_tiebreak="Gemini 4 Argon",                     # §6.3
    judge_drift="Claude Opus 5.5 (5 % of pairs, never sets a label)",  # §6.3
)
# Corpus-build constants of the v4 build, typed from the prereg's §5.1 table (no public artifact carries them).
BUILD = dict(
    pdf_share={"neighbour": "8.34 %", "random1": "9.45 %", "random2": "9.41 %"},   # §5.1 v4 table
    art_rev={"neighbour": (1845, 578), "random1": (1930, 493), "random2": (1927, 496)},  # §5.1 v4 table
    added_chunks={"neighbour": 88765, "random1": 84762, "random2": 84552},         # §5.1 v4 table (checked below)
    draw_overlap=201,                # §5.1 v4 table, papers in both random draws
    rep_screen_excluded=9,           # §5.1 R18, papers of the v3 arms
    snapshot="2026-10-14",           # §5.1 build facts: OpenAlex snapshot the random pools come from
    total_points=811043,             # §15 item 5 (checked below against the artifact's chunk counts)
)
FLAG = "judge validity below threshold (§13.5)"


# --------------------------------------------------------------------------- #
# Load artifacts.
# --------------------------------------------------------------------------- #
def load(rel: str) -> dict:
    return json.loads((STUDY / rel).read_text(encoding="utf-8"))


A = load("artifacts/results-stage-A2.public.json")
B = load("artifacts/results-stage-B.public.json")
HG = load("artifacts/h1b-gold.public.json")
TAGS = load("artifacts/disagreement-tags.json")
POW = load("artifacts/power-v4.json")
CJ = load("control/artifacts/control-judge.json")
RC = load("artifacts/record-recount.json")
CK = load("control/artifacts/control-check.json")

prereg_sha = hashlib.sha256((STUDY / "prereg" / "PREREG-alias-study.md").read_bytes()).hexdigest()
if prereg_sha != RECORD["prereg_sha256"]:
    raise SystemExit(f"prereg sha256 {prereg_sha} != frozen {RECORD['prereg_sha256']}")
for doc in (A, B):
    if not doc["study"]["prereg_verified"]:
        raise SystemExit("a results file does not record prereg_verified")

L1, L2 = A["layer1"], B["layer2"]
H1, H1B, H2 = L1["H1"], L1["H1b"], L1["H2"]
CELLS = L2["cells"]
PLAC = L2["placebo"]
ORIGIN = A["fixed_sensitivities"]["origin"]
SPOT = A["spot_checks"]["judge_validity"]
CM = CJ["metrics"]

N_G = H1["n"]
N_C = L2["c_change"]["n"]
N_ALL = B["record_set"]["balanced"]
PAPERS = H1["papers"]
assert N_G + N_C == N_ALL and N_G == 2 * PAPERS and N_C == PAPERS, "balanced-set arithmetic"
for k in ("H1", "H1b", "H2"):
    assert L1[k] == B["layer1"][k] or k == "H2", f"stage-B layer1.{k} differs from A2"


# --------------------------------------------------------------------------- #
# Formatting.
# --------------------------------------------------------------------------- #
MINUS = "−"


def num(x, d=0) -> str:
    s = f"{abs(x):,.{d}f}"
    return (MINUS if x < 0 and float(s.replace(",", "")) != 0 else "") + s


def sgn(x, d=2) -> str:
    s = f"{abs(x):.{d}f}"
    if float(s) == 0:
        return f"{0:.{d}f}"
    return ("+" if x > 0 else MINUS) + s


def rec(points: float, n: int) -> float:
    """points of n records -> records (may be a half record on a two-draw average)."""
    return points * n / 100.0


def recs(points: float, n: int) -> str:
    r = rec(points, n)
    r = round(r * 2) / 2
    s = f"{abs(r):,.1f}".rstrip("0").rstrip(".")
    return (("+" if r > 0 else MINUS) if r != 0 else "") + s


def ci(c, d=2) -> str:
    return f"[{sgn(c[0], d)}, {sgn(c[1], d)}]"


def n_(x) -> str:
    return f'<span class="num">{x}</span>'


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def found(cell: str, key: str = "found10") -> int:
    return CELLS[cell][key]["found"]


def pp(rate: float, d=1) -> str:
    return f"{100 * rate:.{d}f} %"


# --------------------------------------------------------------------------- #
# Charts: horizontal grouped bars, zero baseline, drawn to scale.
# --------------------------------------------------------------------------- #
SERIES_CLASS = {1: "s1", 2: "s2", 3: "s3"}
_CHART_IDS: list[str] = []


def hbar(cats, series, xmax, xlabel, ticks, title, desc, label_w=190, row_h=None, fmt=None,
         whiskers=None, absent=None, width=760, refline=None, bar_h=30):
    """cats: category labels; series: [(name, values, slot, textured)]; values may be None (absent).
    whiskers: {(si, ci): (lo, hi)} in data units. absent: {(si, ci): text}."""
    fmt = fmt or (lambda v: f"{v:,.0f}")
    whiskers = whiskers or {}
    absent = absent or {}
    ns = len(series)
    gap = 8
    group_h = ns * bar_h + (ns - 1) * gap
    row_h = row_h or group_h + 30
    top, bottom, right = 8, 44, 120
    height = top + row_h * len(cats) + bottom
    plot_w = width - label_w - right
    sx = lambda v: label_w + plot_w * v / xmax
    _CHART_IDS.append(title)
    cid = f"chart{len(_CHART_IDS)}"
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{cid}t {cid}d">',
           f'<title id="{cid}t">{esc(title)}</title><desc id="{cid}d">{esc(desc)}</desc>']
    # grid + ticks
    for t in ticks:
        x = sx(t)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - bottom}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{height - bottom + 18}" text-anchor="middle">{fmt(t)}</text>')
    if refline:
        rv, rl = refline
        out.append(f'<line class="ref" x1="{sx(rv):.1f}" y1="{top}" x2="{sx(rv):.1f}" y2="{height - bottom}"/>'
                   f'<text class="reflab" x="{sx(rv) + 5:.1f}" y="{top + 12}">{esc(rl)}</text>')
    out.append(f'<line class="axis" x1="{label_w}" y1="{top}" x2="{label_w}" y2="{height - bottom}"/>')
    out.append(f'<text class="axlabel" x="{label_w + plot_w / 2:.1f}" y="{height - 6}" text-anchor="middle">{esc(xlabel)}</text>')
    for ci_, cat in enumerate(cats):
        y0 = top + ci_ * row_h + (row_h - group_h) / 2
        lines = cat.split("\n")
        cy = y0 + group_h / 2 - (len(lines) - 1) * 8
        for li, ln in enumerate(lines):
            cls = "cat" if li == 0 else "cat sub"
            out.append(f'<text class="{cls}" x="{label_w - 12}" y="{cy + li * 17 + 5:.1f}" text-anchor="end">{esc(ln)}</text>')
        for si, (name, vals, slot, tex) in enumerate(series):
            v = vals[ci_]
            y = y0 + si * (bar_h + gap)
            if v is None:
                txt = absent.get((si, ci_), "absent")
                out.append(f'<text class="val muted" x="{label_w + 8}" y="{y + bar_h / 2 + 5:.1f}">{esc(name)}: {esc(txt)}</text>')
                continue
            w = max(sx(v) - label_w, 1.5)
            fill = f"url(#hatch-{SERIES_CLASS[slot]})" if tex else ""
            style = f' style="fill:{fill}"' if tex else ""
            out.append(f'<rect class="bar {SERIES_CLASS[slot]}" x="{label_w}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" rx="3"{style}>'
                       f'<title>{esc(cat.splitlines()[0])}, {esc(name)}: {fmt(v)}</title></rect>')
            vx = sx(v)
            if (si, ci_) in whiskers:
                lo, hi = whiskers[(si, ci_)]
                yl = y + bar_h / 2
                out.append(f'<line class="whisk" x1="{sx(lo):.1f}" y1="{yl:.1f}" x2="{sx(hi):.1f}" y2="{yl:.1f}"/>'
                           f'<line class="whisk" x1="{sx(lo):.1f}" y1="{yl - 6:.1f}" x2="{sx(lo):.1f}" y2="{yl + 6:.1f}"/>'
                           f'<line class="whisk" x1="{sx(hi):.1f}" y1="{yl - 6:.1f}" x2="{sx(hi):.1f}" y2="{yl + 6:.1f}"/>')
                vx = max(vx, sx(hi))
            label = fmt(v) + (f"  {name}" if ci_ == 0 and ns > 1 else "")
            out.append(f'<text class="val" x="{vx + 7:.1f}" y="{y + bar_h / 2 + 5:.1f}">{esc(label)}</text>')
    out.append("</svg>")
    legend = '<div class="legend" aria-hidden="true">' + "".join(
        f'<span><i class="sw {SERIES_CLASS[slot]}{" tex" if tex else ""}"></i>{esc(name)}</span>'
        for name, _, slot, tex in series) + "</div>" if ns > 1 else ""
    return legend + "".join(out)


SVG_DEFS = """<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>
<pattern id="hatch-s1" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" class="pat-bg s1"/><line x1="0" y1="0" x2="0" y2="7" class="pat-line"/></pattern>
<pattern id="hatch-s2" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" class="pat-bg s2"/><line x1="0" y1="0" x2="0" y2="7" class="pat-line"/></pattern>
<pattern id="hatch-s3" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(135)"><rect width="7" height="7" class="pat-bg s3"/><line x1="0" y1="0" x2="0" y2="7" class="pat-line"/></pattern>
</defs></svg>"""


# --------------------------------------------------------------------------- #
# Slide scaffolding.
# --------------------------------------------------------------------------- #
SLIDES: list[str] = []


def slide(kicker: str, title: str, body: str, *, flag=False, exploratory=False, source="", cls=""):
    tags = []
    if exploratory:
        tags.append('<span class="tag explo">Exploratory</span>')
    foot = []
    if flag:
        foot.append(f'<p class="flag"><span class="flagmark" aria-hidden="true">!</span>Every result on this slide: {esc(FLAG)}.</p>')
    if source:
        foot.append(f'<p class="src">Source: {source}</p>')
    SLIDES.append(
        f'<section class="slide {cls}" aria-roledescription="slide">'
        f'<header class="sh"><p class="kicker">{esc(kicker)} {"".join(tags)}</p><h2>{title}</h2></header>'
        f'<div class="sb">{body}</div>'
        f'<footer class="sf">{"".join(foot)}<span class="pg"></span></footer></section>')


def tile(big: str, small: str, cls="") -> str:
    return f'<div class="tile {cls}"><p class="big num">{big}</p><p class="small">{small}</p></div>'


# --------------------------------------------------------------------------- #
# Derived figures (all from the artifacts above).
# --------------------------------------------------------------------------- #
G_found = {a: PLAC["G"]["found10"][f"base|{a}|default"]["found"] for a in ("none", "merged", "placebo")}
C_found = {a: PLAC["C"]["found10"][f"base|{a}|default"]["found"] for a in ("none", "merged", "placebo")}
G_gold = {a: ORIGIN["hits_by_origin"][f"base|{a}|default"]["gold"] for a in ("none", "merged")}
assert ORIGIN["hits_by_origin"]["base|none|default"]["records"] == "G"
G_miss = {a: N_G - v for a, v in G_found.items()}
gl = L2["g_lift"]
cc = L2["c_change"]
assert G_found["merged"] - G_found["none"] == gl["gained"] - gl["lost"]
dec = L2["decisions"]
d_gl, d_rr, d_gr = dec["attach_merged_glossary"], dec["keep_reranker_default"], dec["related_paper_growth_concern"]
nmr = L2["neighbour_minus_random"]
rr = L2["default_minus_bm25_neighbour"]
ARMS = [("base", "base (OA-1000)"), ("neighbour", "neighbour"), ("random1", "random-1"), ("random2", "random-2")]
judged = {a: found(f"{a}|none|default") for a, _ in ARMS}
gold = {a: found(f"{a}|none|default", "found10_gold") for a, _ in ARMS}
bm25 = {a: found(f"{a}|none|bm25") for a, _ in ARMS}
rand_j = (judged["random1"] + judged["random2"]) / 2
rand_g = (gold["random1"] + gold["random2"]) / 2
eq = H2["equivalence"]
pw = POW["simulation"]["H2"]["rho1"]
pw_a = pw["p0.85_q0.05"]["0.05"]["confirm_a0.7"]
pw_b = pw["p0.7_q0.1"]["0.05"]["confirm_a0.7"]
added = int(re.search(r"total (\d+)", POW["assumptions"]["neighbour_counts"]).group(1))
chunks = {a: B["runs"][f"{a}/merged"]["field_coverage"]["chunks"] for a, _ in ARMS}
fc_m = PLAC["field_coverage"]["merged"]
fc_p = PLAC["field_coverage"]["placebo"]
pmn = PLAC["G"]["placebo_minus_none"]
cgate = H1["c_gate"]
tags = TAGS["counts"]["tags"]
reads = TAGS["counts"]["readings"]
fl, rt = CM, CM["retest"]
cov = CM["cover_any"]["split"]["by_route"]["agree"]["answers_to_not"]["recovered_by_other_chunk"]
prov_stats = B["provenance"]["commit"][:7]
prov_ctrl = CJ["provenance"]["commit"][:7]
prov_tags = TAGS["provenance"]["commit"][:7]
prov_hg = HG["provenance"]["commit"][:7]
runsA = sorted({v["commit"][:7] for k, v in B["runs"].items() if v.get("stage") == "A"})
runsB = sorted({v["commit"][:7] for k, v in B["runs"].items() if v.get("stage") == "B"})

# --------------------------------------------------------------------------- #
# 1. Title
# --------------------------------------------------------------------------- #
SLIDES.append(f"""<section class="slide title" aria-roledescription="slide">
<div class="titlewrap">
<p class="kicker">ragstack · pre-registered study · OA-1000</p>
<h1>The alias study</h1>
<p class="lede">Does a glossary help a question find a passage that only uses the short form?<br>
And does growing a collection with a paper's citation neighbours cost more than random growth?</p>
<dl class="meta">
<div><dt>Pre-registration</dt><dd>frozen {esc(RECORD['prereg_frozen_at'][:10])}, sha256 <span class="num">{prereg_sha[:12]}…</span> (verified from the file), signed tag <span class="num">{esc(RECORD['prereg_tag'])}</span></dd></div>
<div><dt>Record</dt><dd>RESULTS.md at merge <span class="num">{RECORD['results_merge']}</span> (#954); control addendum <span class="num">{RECORD['control_merge']}</span> (#953)</dd></div>
<div><dt>Verdicts</dt><dd>H1 <b>{esc(H1['status'])}</b> · H1b <b>{esc(H1B['status'])}</b> · H2 <b>{esc(H2['status'])}</b> — {esc(FLAG)} on every result</dd></div>
</dl>
<p class="hint">Arrow keys, space or click to move · <span class="num">#n</span> links to a slide · print for one slide per page</p>
</div><footer class="sf"><span class="pg"></span></footer></section>""")

# --------------------------------------------------------------------------- #
# 2. Two questions
# --------------------------------------------------------------------------- #
slide("Prereg §1", "Two questions, two families",
      f"""<div class="cols2">
<div class="card"><p class="fam">Family G · glossary</p>
<p>A question names a concept by its <b>full term</b>; the passage that answers it uses only a <b>short form</b> — a symbol, or an abbreviation the paper defines once, elsewhere.</p>
<p class="q">Does attaching the paper's merged glossary to its chunks make that passage findable?</p>
<p class="small">H1: merged &gt; none on G records. H1b (only if H1 holds): the G gain exceeds the change on control (C) records.</p></div>
<div class="card"><p class="fam">Family N · growth</p>
<p>A real collection holds a paper <b>with</b> its references and citers, which reuse its vocabulary and short forms — often with different meanings.</p>
<p class="q">Does adding citation neighbours cost more than adding the same number of random same-field papers?</p>
<p class="small">H2: neighbour &lt; random average. Plus Layer 2: the reranker default vs BM25 in a related-paper collection.</p></div>
</div>
<p class="callout">The study <b>informs</b> two serving decisions — ship the merged glossary? keep the reranker default? — it does <b>not make</b> them. Each family is tested one-sided at α = {PREREG['alpha']} on its own; no level passes between them (prereg §3.1).</p>""",
      source="prereg §1, §3.1")

# --------------------------------------------------------------------------- #
# 3. How it was measured
# --------------------------------------------------------------------------- #
slide("Design", "How it was measured",
      f"""<div class="cols3">
<div class="card"><p class="fam">Records</p>
<p class="big num">{N_ALL:,}</p><p class="small">= {n_(PAPERS)} papers × (2 G + 1 C)</p>
<p><b>G</b> ({n_(N_G)}): the question uses the full term; the answering passage carries only the paper's short form.</p>
<p><b>C</b> ({n_(N_C)}): same machinery, but the passage already carries the term — tells a glossary effect apart from a general shift.</p></div>
<div class="card"><p class="fam">Outcome · found@10</p>
<p>A record is <b>found</b> when its gold chunk is in the top 10, <b>or</b> another top-10 chunk, read alone, is judged to state the core of the answer.</p>
<p>A measure of <b>reach</b>, not answer quality. <b>found@10-gold</b> (gold chunk only, no judge) is printed beside every figure.</p></div>
<div class="card"><p class="fam">The frozen judge (§6.3)</p>
<ul class="tight"><li>C: {esc(PREREG['judge_c'])}</li><li>E: {esc(PREREG['judge_e'])}</li><li>tiebreak when C and E disagree: {esc(PREREG['judge_tiebreak'])}</li><li>drift reference: {esc(PREREG['judge_drift'])}</li></ul>
<p class="small">Blind to arm, retriever and rank. {n_(f"{B['judge']['pairs_finalized']:,}")} pairs judged, {n_(f"{B['judge']['sources']['tiebreak']:,}")} by tiebreak, {n_(B['judge']['unjudged'])} unjudged.</p></div>
</div>
<p class="callout"><b>What a point is:</b> one percentage point of found@10. 1 of {N_G} G records = <span class="num">{100 / N_G:.2f}</span> pt · 1 of {N_C} C = <span class="num">{100 / N_C:.2f}</span> pt · 1 of {N_ALL} = <span class="num">{100 / N_ALL:.2f}</span> pt. Every point figure in this deck is also given in records.</p>""",
      source="<code>record_set.balanced</code>, <code>layer1.H1.n</code>, <code>layer2.c_change.n</code>, <code>judge.pairs_finalized</code> (results-stage-B); prereg §4, §6")

# --------------------------------------------------------------------------- #
# 3p. Data provenance: papers and text (owner request, 2026-11-05)
# --------------------------------------------------------------------------- #
COR, POOL, WAV, RSET = RC["corpus"], RC["pool"], RC["waves"], RC["record_set"]
VMOD = RC["verdict_source"]["all"]["verifier_model"]
assert RSET["accepted"]["records"] == B["record_set"]["records"] and RSET["balanced"]["records"] == N_ALL
assert RSET["balanced"]["layout"] == {"2G+1C": PAPERS}
COLL = {a: B["runs"][f"{a}/merged"]["field_coverage"] for a, _ in ARMS}
assert COLL["base"]["chunks"] == COR["chunks"] and COLL["base"]["glossary_file_counts"]["documents"] == COR["documents"]
ADDC = {a: COLL[a]["chunks"] - COLL["base"]["chunks"] for a, _ in ARMS[1:]}
assert ADDC == BUILD["added_chunks"], "artifact chunk counts differ from prereg §5.1"
NBC = re.search(r"realised mean ([\d.]+), min (\d+), max (\d+), total (\d+)", POW["assumptions"]["neighbour_counts"])
nb_mean, nb_min, nb_max = NBC.group(1), NBC.group(2), NBC.group(3)
DRAFT_MODEL = next(iter(RC["drafter"]["served_models_draft_rows"]))
arm_rows = "".join(
    f'<tr><td><b>{esc(lab)}</b></td><td class="num">{added:,}</td><td class="num">{ADDC[a]:,}</td>'
    f'<td class="num">{"—" if a == "neighbour" else sgn(100 * (ADDC[a] - ADDC["neighbour"]) / ADDC["neighbour"]) + " %"}</td>'
    f'<td class="num">{BUILD["pdf_share"][a]}</td><td class="num">{BUILD["art_rev"][a][0]:,} / {BUILD["art_rev"][a][1]:,}</td></tr>'
    for a, lab in ARMS[1:])
slide("Design · provenance", "Where the papers and the text come from",
      f"""<div class="cols3 dense">
<div class="card"><p class="fam">Base corpus · OA-1000</p>
<p>{COR['papers']:,} open-access articles, JATS XML from PubMed Central: {n_(f"{COR['documents']:,}")} documents (articles plus their table and figure units), {n_(f"{COR['chunks']:,}")} chunks.</p>
<p class="small"><i>How the {COR['papers']:,} were drawn is not stated in the study record.</i></p>
<p class="fam">Records</p>
<p>Seeded pool of {n_(POOL['eval_papers'])} eligible papers, {n_(WAV['activated_final'])} activated in waves; drafted by {esc(DRAFT_MODEL)}, checked mechanically, verified by Claude Opus 5.5 (Codex on {n_(VMOD['gpt-6-astra'])}): {n_(f"{RSET['accepted']['records']:,}")} accepted on {n_(RSET['accepted']['papers'])} papers. The {n_(PAPERS)} with exactly 2 G + 1 C give the {n_(N_ALL)} balanced records. Built blind to retrieval.</p></div>
<div class="card"><p class="fam">Text extraction</p>
<ul class="tight">
<li><b>JATS article:</b> abstract + body, titled sections as <code>## Title</code> lines (untitled ones leave none); no reference list.</li>
<li><b>Tables, figures:</b> lifted out as unit records (label, caption, grid; long tables split by row, header repeated).</li>
<li><b>Added papers:</b> PMC JATS where a copy exists, else the licensed open-access PDF: no headings, reference list kept (a known difference).</li>
<li><b>Screens</b> on added papers: control characters (PDF out above 0.5 % of chunks), repetition (distinct-chunk ratio &lt; 0.90; {BUILD['rep_screen_excluded']} out).</li>
</ul></div>
<div class="card"><p class="fam">Added papers · OpenAlex metadata only</p>
<p><b>Neighbours</b> per study paper: the 5 most-cited references and 5 most recent citers with open-access text, not in OA-1000; unfetchable ones walked past; articles and reviews only; shared ones added once. Realised {n_(nb_mean)} per paper ({nb_min}–{nb_max}).</p>
<p><b>Random, two draws</b> (snapshot of {BUILD['snapshot']}): same primary topic, year ±3, open access, no citation link to a study paper; matched per study paper on count, article type, chunk-length band and PDF share; ±5 % chunk volume a target, not a gate. {BUILD['draw_overlap']} papers in both draws.</p></div>
</div>
<table class="scen tight-t"><thead><tr><th>added arm</th><th>papers</th><th>chunks</th><th>vs neighbour</th><th>PDF share</th><th>article / review</th></tr></thead>
<tbody>{arm_rows}</tbody></table>""",
      source="<code>corpus</code>, <code>pool</code>, <code>waves</code>, <code>record_set</code>, <code>drafter</code>, <code>verdict_source</code> (record-recount); <code>runs.&lt;arm&gt;/merged.field_coverage</code> (results-stage-B); <code>assumptions.neighbour_counts</code> (power-v4); <code>jats.py</code> (extractor); prereg §4.1, §4.3, §5.1 (selection rules, screens, PDF share, article types)")

# --------------------------------------------------------------------------- #
# 3q. Data provenance: collections (owner request, 2026-11-05)
# --------------------------------------------------------------------------- #
COLL_KEYS = sorted({k.split("#")[0] for k in B["runs"]})
assert len(COLL_KEYS) == 9, COLL_KEYS
pts = sum(COLL[k.split("/")[0]]["chunks"] for k in COLL_KEYS)
assert pts == BUILD["total_points"], pts
assert all(v["census_source"] == "load_receipt+live" for v in B["runs"].values())
CKG = CK["gate"]
assert CK["gate_passes"] and CKG["v_stage_a_offsets_reproduce"]
ck_docs, ck_mis = CK["counts"]["docs"], CK["counts"]["content_mismatch"]
TOK = CK["chunker"]["tokenizer"]
assert CK["chunker"]["stage_a_descriptor"] == "fixed_token/512/0"
n_legs, n_rows = len(B["legs_read"]), B["record_set"]["records"]
assert set(B["legs_read"].values()) == {n_rows}
coll_rows = "".join(
    f'<tr><td><b>{esc(lab)}</b></td><td class="num">{COLL[a]["chunks"]:,}</td><td class="num">{COLL[a]["glossary_file_counts"]["documents"]:,}</td>'
    f'<td>{" · ".join(k.split("/")[1] for k in COLL_KEYS if k.split("/")[0] == a)}</td></tr>' for a, lab in ARMS)
slide("Design · provenance", "How the collections were built",
      f"""<div class="cols2 dense">
<div>
<div class="card"><p class="fam">Chunks and embeddings</p>
<ul class="tight">
<li><b>Chunker</b> <code>tok512o0</code> = <code>{esc(CK['chunker']['stage_a_descriptor'])}</code>: a sliding 512-token window, no overlap, blind to sentences and sections; tokens counted in the <code>{esc(TOK)}</code> tokenizer.</li>
<li><b>Embedding:</b> SFR dense; each corpus embedded once, base shared by all four arms with identical chunk ids and text; added papers through the same chunker, embedder and tools image <i>(its version: not stated in the public record)</i>.</li>
<li><b>Checked later</b> (control): on {n_(f"{ck_docs:,}")} sampled documents every stage-A chunk equals the record text at its offsets ({ck_mis} mismatches).</li>
</ul></div>
<div class="card"><p class="fam">Retrievers · top 10</p>
<ul class="tight">
<li><b>default</b> (served path): hybrid BM25 + SFR dense (RRF k 60), top 50 rescored by <code>BAAI/bge-reranker-v2-m3</code> on text + glossary field.</li>
<li><b>norerank:</b> the same hybrid. <b>BM25:</b> alone.</li>
<li>No graph, no rewriting; glossary boost on (declared). {n_legs} legs × {n_rows:,} records = {n_(f"{n_legs * n_rows:,}")} calls.</li>
</ul></div>
</div>
<div>
<div class="card"><p class="fam">{len(COLL_KEYS)} bench collections, each written once</p>
<table class="scen tight-t"><thead><tr><th>corpus arm</th><th>chunks</th><th>documents</th><th>glossary arms</th></tr></thead><tbody>{coll_rows}</tbody></table>
<p>{n_(f"{pts:,}")} points. Shards loaded once per glossary arm through the workflow engine; each load's receipt is pinned by the signed prereg tag, and every leg checked chunk count and glossary stamps against it.</p></div>
<div class="card"><p class="fam">Glossary field</p>
<p><b>merged:</b> the paper's symbols and defined abbreviations (plus plural entries), rendered on every chunk where the alias occurs as a token, at most {PREREG['glossary_line_cap']} lines / {PREREG['glossary_char_cap']:,} characters: {n_(f"{fc_m['chunks_with_field']:,}")} of {fc_m['chunks']:,} base chunks. <b>placebo:</b> another paper's entries, own entries removed: {n_(fc_p['chunks_with_field'])} chunks. <b>none:</b> an explicit empty file. Added papers' glossaries come from the same pipeline, run locally.</p></div>
</div>
</div>""",
      source="<code>runs.*.field_coverage</code>, <code>runs.*.census_source</code>, <code>legs_read</code>, <code>layer2.placebo.field_coverage</code> (results-stage-B); <code>chunker</code>, <code>gate</code>, <code>groups.*.counts</code> (control-check); prereg §5.1–§5.4, §9.3, §15 item 5")

# --------------------------------------------------------------------------- #
# 3o. Why judge at all (owner request, 2026-11-05)
# --------------------------------------------------------------------------- #
HBO = ORIGIN["hits_by_origin"]
assert HBO["base|none|default"]["records"] == "G" and HBO["neighbour|none|default"]["records"] == "all"


def origin_bar(cell: str, n: int, label: str) -> str:
    h = HBO[cell]
    tot = h["gold"] + h["same"] + h["other"]
    miss = n - tot
    return (f'<p class="small"><b>{esc(label)}</b>: {tot} of {n} found</p>'
            f'<div class="split" role="img" aria-label="{esc(label)}: gold chunk in top 10 {h["gold"]}, another passage of the same paper {h["same"]}, another paper {h["other"]}, not found {miss}">'
            f'<span class="seg s1" style="flex:{h["gold"]}">{h["gold"]} gold</span>'
            f'<span class="seg s2 tex" style="flex:{max(h["same"], 1)}">{h["same"]}</span>'
            f'<span class="seg s3" style="flex:{max(h["other"], 1)}">{h["other"]}</span>'
            f'<span class="seg miss" style="flex:{max(miss, 1)}">{miss}</span></div>')


base_g, nb = HBO["base|none|default"], HBO["neighbour|none|default"]
slide("Design · the outcome", "Why judge at all?",
      f"""<div class="cols2 dense">
<div>
<p class="lede">Each question asks for a <b>fact</b>, not a passage. It was written from one gold chunk, but the fact can be stated elsewhere too: in another passage of the same paper, or in another paper. A reader who retrieves any of those has the answer.</p>
<p><b>Counting only the gold chunk would score those as misses.</b> Without a glossary, {base_g['gold'] + base_g['same'] + base_g['other']} of {N_G} G questions were found: {base_g['gold']} had the gold chunk in the top 10; of the {N_G - base_g['gold']} where it was not, {base_g['same']} were answered by another passage of the same paper and {base_g['other']} by another paper (a lower bound on answers found elsewhere, since chunks beside a gold hit were not judged).</p>
<p><b>For H2 it changes what is asked.</b> A citation neighbour is more likely than a random paper to <i>answer</i>. Gold-only would count every gold chunk a neighbour pushes out as a loss, even when the neighbour supplies the answer. With neighbours added, {nb['other']} questions were answered by another paper, against {HBO['random1|none|default']['other']} and {HBO['random2|none|default']['other']} in the random draws: consistent with neighbours supplying answers where they displaced the gold (a per-cell reading; “another paper” is any other paper, and there is no per-record count). Judged H2 asks whether neighbours displace more than they add (prereg §3.1).</p>
<p class="small"><b>So judged found@10 is the outcome</b> (what a reader gets), with found@10-gold beside every figure as a judge-free check and as H2's direction gate. The price: the judge has to be right (next two slides).</p>
</div>
<div class="card"><p class="fam">Who answered: gold chunk in top 10 / same paper / another paper / not found</p>
<div class="legend-row small"><span class="li"><span class="key s1"></span> gold chunk in top 10</span><span class="li"><span class="key s2 tex"></span> another passage, same paper</span><span class="li"><span class="key s3"></span> another paper</span><span class="li"><span class="key miss"></span> not found</span></div>
{origin_bar("base|none|default", N_G, "G questions, base corpus, no glossary")}
{origin_bar("neighbour|none|default", N_ALL, "all questions, neighbours added")}
{origin_bar("random1|none|default", N_ALL, "all questions, random draw 1 added")}
{origin_bar("random2|none|default", N_ALL, "all questions, random draw 2 added")}
<p class="small">Counts are exact; small segments are widened to fit their labels, so widths are approximate.</p>
</div>
</div>""",
      flag=True,
      source="<code>fixed_sensitivities.origin.hits_by_origin</code> (stage-A2); <code>layer1.H1.n</code>, <code>record_set.balanced</code>; prereg §3.1, §6.1")

# --------------------------------------------------------------------------- #
# 3a. What "judged" means (owner request, 2026-11-05)
# --------------------------------------------------------------------------- #
DA, DB = A["judge"]["drift"], B["judge"]["drift"]
OA, OB = DA["scopes"]["overall"], DB["scopes"]["overall"]
NOREF = B["judge"]["opus_no_reference"]
slide("Design · the judge", "What \u201cjudged\u201d means",
      f"""<div class="cols2">
<div>
<p class="lede">An LLM judge decides whether a chunk answers the question. The alternative, found@10-gold, only checks whether the one known source chunk was retrieved.</p>
<p>For each question the system retrieves its top 10 chunks. For any chunk that matters, the frozen judge reads the question and that one chunk, with the paper title and the chunk's glossary entries, and answers one question: <b>does this passage, read on its own, state the core of what is asked?</b> Any chunk can count: the same paper, another paper, or a passage other than the gold one. A question is <b>found (judged)</b> if at least one top-10 chunk is labelled <i>answers</i>.</p>
<ul class="tight">
<li>Two Qwen models judge first: {esc(PREREG['judge_c'])}, and {esc(PREREG['judge_e'])}.</li>
<li>If they agree, that is the label; if they disagree (or either gives no valid label), {esc(PREREG['judge_tiebreak'])} breaks the tie ({n_(f"{B['judge']['sources']['tiebreak']:,}")} of {n_(f"{B['judge']['pairs_finalized']:,}")} pairs).</li>
<li>Claude Opus 5.5 re-labels a 5 % sample to watch for drift and never sets a label (next slide).</li>
<li>To save calls, a chunk is judged only where its label can change found@10 or RR@10: with the gold chunk in the top 10 the question already counts as found, and only the chunks ranked above it are judged (for RR@10).</li>
</ul>
</div>
<div>
<div class="card"><p class="fam">Gold vs judged</p>
<p><b>Gold:</b> is the record's own source passage in the top 10? A fixed fact, no judge involved.</p>
<p><b>Judged:</b> does <i>any</i> top-10 passage answer? Broader, because another passage can stand in for the gold one. That is why judged found@10 was already {PLAC['G']['found10']['base|none|default']['found']} of {N_G} G questions without a glossary, while the gold chunk was in the top 10 for {G_gold['none']} of them.</p></div>
<div class="card"><p class="fam">The cost of judging</p>
<p>It depends on the judge being right. On 100 spot-check pairs the owner's labels agreed with the judge's {SPOT['reweighted_agreement']:.3f} of the time (re-weighted), below the prereg's {PREREG['judge_validity_rule']:.2f} line. So every judged result carries <i>{esc(FLAG)}</i>, with the gold figure beside it. The owner's tags on the {TAGS['counts']['n']} disagreements (exploratory) put every one on the partial-versus-full line; none is a passage with no answer in it.</p></div>
</div>
</div>""",
      flag=True,
      source="prereg §6.2–§6.3; <code>judge.sources</code>, <code>judge.pairs_finalized</code>, <code>layer2.placebo.G.found10</code> (results-stage-B); <code>fixed_sensitivities.origin.hits_by_origin</code>, <code>spot_checks.judge_validity</code> (stage-A2); <code>counts</code> (disagreement-tags)")

# --------------------------------------------------------------------------- #
# 3b. The drift reference (owner request, 2026-11-05)
# --------------------------------------------------------------------------- #
slide("Design · the judge", "The drift reference: a monitor, not a judge",
      f"""<div class="cols2">
<div>
<p class="lede">It checks that the judging pipeline stays the same over a long run. Across two stages, {n_(f"{B['judge']['pairs_finalized']:,}")} pairs were judged on two Qwen endpoints and a gateway; an endpoint swapped to another model, a server setting or a gateway update could shift the judge without anyone noticing.</p>
<ul class="tight">
<li>A seeded 5 % sample of judged pairs, stratified equally across the three corpus arms ({n_(f"{OB['sampled']:,}")} of {n_(f"{B['judge']['pairs_finalized']:,}")}) is also sent to Claude Opus 5.5, on <code>alias-judge/1</code>, the prompt of the earlier judge calibration: a fixed external yardstick.</li>
<li>The run tracks how often the final label agrees with Opus, overall and per corpus arm, as batches accumulate.</li>
<li><b>It never sets a label.</b> If it could override labels it would be part of what it measures.</li>
</ul>
<p class="small"><b>Rules</b> (prereg §6.3, amended R29): reference lines {OB['threshold']:.2f} overall and {DB['scopes']['base']['threshold']:.2f} per arm, report-only; the one alarm that pauses the run is an <b>arm gap</b> above {DB['arm_gap']['threshold']:.2f} once every arm has {DB['arm_gap']['min_pairs']} Opus-answered pairs. An empty Opus answer (a known topic quirk) is re-issued once, then the pair is <i>no reference</i>: filtered, never a drift failure.</p>
</div>
<div>
<div class="tiles two">
{tile(f"{OA['agreement']:.3f}", f"agreement with Opus, stage A ({OA['agree']} of {OA['answered']} answered) · line {OA['threshold']:.2f}")}
{tile(f"{OB['agreement']:.3f}", f"cumulative, both stages ({OB['agree']:,} of {OB['answered']:,}) · line {OB['threshold']:.2f}")}
</div>
<p>Both sit below the {OB['threshold']:.2f} line, so they are reported, as R29 requires; it is not a deviation. The arm gap was {DB['arm_gap']['value']:.3f} (alarm above {DB['arm_gap']['threshold']:.2f}): <b>{'fired' if DB['arm_gap']['fires'] else 'never fired'}</b>, so the run never paused. Per arm, cumulative: {DB['scopes']['base']['agreement']:.3f} base, {DB['scopes']['neighbour']['agreement']:.3f} neighbour, {DB['scopes']['random']['agreement']:.3f} random against the {DB['scopes']['base']['threshold']:.2f} line; in stage A neighbour ({DA['scopes']['neighbour']['agreement']:.3f}) and random ({DA['scopes']['random']['agreement']:.3f}) sat below it, likewise report-only. {NOREF['total']} pairs ended as <i>no reference</i> ({NOREF['base']} base, {NOREF['neighbour']} neighbour, {NOREF['random']} random).</p>
<div class="card"><p class="fam">Drift reference vs the owner's spot check</p>
<p><b>Drift reference:</b> is the judge <i>consistent</i> over time and across arms? One model against another.</p>
<p><b>Spot check:</b> is the judge <i>right</i>? The owner against the judge. That one fell below its line ({SPOT['reweighted_agreement']:.3f} vs {PREREG['judge_validity_rule']:.2f}) and put the flag on every result.</p></div>
</div>
</div>""",
      source="<code>judge.drift</code>, <code>judge.opus_no_reference</code>, <code>judge.pairs_finalized</code> (results-stage-B; stage A from results-stage-A2); prereg §6.3, R29")

# --------------------------------------------------------------------------- #
# 4. What a point is
# --------------------------------------------------------------------------- #
slide("Reading the numbers", "What one point is, in records",
      f"""<p class="lede">A <b>point</b> is one percentage point of found@10. With these sample sizes a point is a handful of records, so every point figure in this deck is translated into records.</p>
<div class="tiles">
{tile(f"{100 / N_G:.2f} pt", f"= <b>1</b> of the {N_G} G records")}
{tile(f"{100 / N_C:.2f} pt", f"= <b>1</b> of the {N_C} C records")}
{tile(f"{100 / N_ALL:.2f} pt", f"= <b>1</b> of all {N_ALL} records")}
</div>
<div class="cols3 examples">
<p>H1's <span class="num">{sgn(H1['net_points'])}</span> pt = <b>{recs(H1['net_points'], N_G)}</b> G records</p>
<p>H1b's C change <span class="num">{sgn(cgate['delta_c_points'])}</span> pt = <b>{recs(cgate['delta_c_points'], N_C)}</b> C record</p>
<p>H2's <span class="num">{sgn(H2['net_points'])}</span> pt = <b>{recs(H2['net_points'], N_ALL)}</b> record (an average of two random draws)</p>
</div>""",
      flag=True,
      source="100 / n; <code>layer1.H1.net_points</code>, <code>layer1.H1.c_gate.delta_c_points</code>, <code>layer1.H2.net_points</code>")

# --------------------------------------------------------------------------- #
# 5. The alias problem, invented example
# --------------------------------------------------------------------------- #
diagram = f"""<svg class="diagram" viewBox="0 0 1120 430" role="img" aria-labelledby="pdt-t pdt-d">
<title id="pdt-t">Invented example of the alias problem</title>
<desc id="pdt-d">A paper defines PDT as photodynamic therapy once. Its results chunk says only PDT reduced tumour volume by 40 percent. The merged glossary renders the line PDT, photodynamic therapy, on that chunk because PDT occurs there. A G question using the full term reaches the results chunk only through that line. A C question reaches a methods chunk that already spells the term out.</desc>
<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ahead"/></marker></defs>
<text class="dlab" x="20" y="24">QUESTION</text><text class="dlab" x="420" y="24">CHUNKS OF THE PAPER</text><text class="dlab" x="850" y="24">GLOSSARY</text>
<!-- glossary -->
<rect class="box" x="850" y="40" width="250" height="96" rx="8"/>
<text class="dt" x="866" y="66">Introduction, once:</text>
<text class="dq" x="866" y="92">“… PDT (photodynamic</text><text class="dq" x="866" y="114">therapy) is …”</text>
<line class="flow" x1="975" y1="136" x2="975" y2="176" marker-end="url(#ah)"/>
<rect class="box acc" x="850" y="180" width="250" height="78" rx="8"/>
<text class="dt" x="866" y="204">merged glossary entry</text>
<text class="dmono" x="866" y="236">PDT — photodynamic therapy</text>
<text class="dsmall" x="850" y="284">mention rule: rendered only on</text>
<text class="dsmall" x="850" y="304">chunks where “PDT” occurs,</text>
<text class="dsmall" x="850" y="324">≤ {PREREG['glossary_line_cap']} lines / {PREREG['glossary_char_cap']:,} characters (§5.3)</text>
<!-- chunk A -->
<rect class="box" x="420" y="40" width="370" height="150" rx="8"/>
<text class="dt" x="436" y="66">Results chunk</text>
<text class="dq" x="436" y="96">“PDT reduced tumour volume</text><text class="dq" x="436" y="118">by 40%.”</text>
<rect class="field" x="430" y="138" width="350" height="40" rx="5"/>
<text class="dmono" x="442" y="164">field: PDT — photodynamic therapy</text>
<path class="flow dash" d="M850 219 C 820 219, 820 158, 784 158" marker-end="url(#ah)"/>
<!-- chunk B -->
<rect class="box" x="420" y="250" width="370" height="120" rx="8"/>
<text class="dt" x="436" y="276">Methods chunk</text>
<text class="dq" x="436" y="306">“Photodynamic therapy used</text><text class="dq" x="436" y="328">635 nm red light.”</text>
<text class="dsmall" x="436" y="356">no “PDT” here → no field rendered</text>
<!-- questions -->
<rect class="box g" x="20" y="60" width="330" height="110" rx="8"/>
<text class="dt" x="36" y="86">G question (full term)</text>
<text class="dq" x="36" y="114">“By how much did photodynamic</text><text class="dq" x="36" y="136">therapy reduce tumour volume?”</text>
<path class="flow" d="M350 115 L 414 115" marker-end="url(#ah)"/>
<text class="dsmall" x="20" y="194">the only lexical bridge is the field</text>
<rect class="box c" x="20" y="260" width="330" height="110" rx="8"/>
<text class="dt" x="36" y="286">C question (term in passage)</text>
<text class="dq" x="36" y="314">“Which light wavelength was used</text><text class="dq" x="36" y="336">for photodynamic therapy?”</text>
<path class="flow" d="M350 315 L 414 315" marker-end="url(#ah)"/>
<text class="dsmall" x="20" y="394">answerable with or without a glossary</text>
<text class="dwarn" x="1100" y="418" text-anchor="end">INVENTED EXAMPLE — not a study record</text>
</svg>"""
slide("The alias problem", "An invented example of what the glossary is for",
      f"""{diagram}
<p class="small center">This example is <b>invented</b> for illustration; no record, paper or passage of the study is shown. The rendered line follows the served format <span class="num">alias — term</span> (prereg §5.3, R27).</p>""",
      source="prereg §1, §5.3 (mention rule, caps, rendering conventions)")

# --------------------------------------------------------------------------- #
# 6. H1
# --------------------------------------------------------------------------- #
h1_chart = hbar(
    ["no glossary", "merged glossary", "placebo glossary\n(another paper's)"],
    [("missed (judged)", [G_miss["none"], G_miss["merged"], G_miss["placebo"]], 1, False),
     ("missed (gold chunk)", [N_G - G_gold["none"], N_G - G_gold["merged"], None], 2, True)],
    xmax=60, xlabel=f"G questions MISSED, of {N_G}: no top-10 chunk answers (judged) / gold chunk not in top 10 — fewer is better", ticks=[0, 10, 20, 30, 40, 50, 60],
    title="G records not found, by glossary arm",
    desc=f"Judged misses: none {G_miss['none']}, merged {G_miss['merged']}, placebo {G_miss['placebo']}. Gold-only misses: none {N_G - G_gold['none']}, merged {N_G - G_gold['merged']}; placebo gold-only not in the artifacts.",
    absent={(1, 2): "absent (no G-only gold count in the artifacts)"})
slide("Family G · H1 · confirmatory", f"H1 {esc(H1['status'])}: the merged glossary rescued {gl['gained']} of the {G_miss['none']} missed G records",
      f"""<div class="cols-chart"><div>{h1_chart}</div>
<div class="side">
<p>The bars count <b>misses</b>: questions where no top-10 chunk answers (judged), or where the record's own gold chunk is not in the top 10. Misses fell from <b>{G_miss['none']} to {G_miss['merged']}</b> judged and from <b>{N_G - G_gold['none']} to {N_G - G_gold['merged']}</b> by gold chunk.</p>
<p><b>{G_found['none']} → {G_found['merged']}</b> of {N_G} G records found (judged): <span class="num">{sgn(H1['net_points'])}</span> pt, 95 % CI <span class="num">{ci(H1['ci95'])}</span>; {H1['gained']} gained, {H1['lost']} lost, in {H1['n_nonzero']} papers; exact sign-flip p = <span class="num">{H1['p']}</span> (its minimum attainable p).</p>
<p>Gold-only: {G_gold['none']} → {G_gold['merged']} (<span class="num">{sgn(H1['gold_only_net_points'])}</span> pt = {recs(H1['gold_only_net_points'], N_G)} records; {gl['gold_only']['gained']} gained, {gl['gold_only']['lost']} lost).</p>
<p><b>Placebo</b> — another paper's glossary, same rule: {G_found['placebo']} of {N_G}, the same as none ({pmn['gained']} gained, {pmn['lost']} lost): placebo − none <span class="num">{sgn(pmn['net_points'])}</span> pt, {esc(pmn['equivalence']['reading'])}. That bounds foreign glossary text only at a far weaker dose: it renders on only {fc_p['chunks_with_field']:,} chunks vs {fc_m['chunks_with_field']:,} for merged, so it rules out little about extra text in general (prereg §5.3 reads it with field coverage).</p>
<p class="small">The gain is at the ceiling: {pp(G_found['none'] / N_G)} of G records were already found before any glossary.</p>
</div></div>""",
      flag=True,
      source="<code>layer2.placebo.G.found10</code>, <code>layer2.placebo.field_coverage</code>, <code>layer2.g_lift</code> (stage-B); <code>layer1.H1</code>, <code>fixed_sensitivities.origin.hits_by_origin</code> (stage-A2)")

# --------------------------------------------------------------------------- #
# 7. H1b
# --------------------------------------------------------------------------- #
dc = cgate["delta_c_points"]
slide("Family G · H1b · confirmatory", f"H1b {esc(H1B['status'])}: the gain is alias-specific — read with the C ceiling",
      f"""<div class="cols2 wide-left">
<div>
<div class="did">
<div><p class="small">G change (merged − none)</p><p class="big num">{sgn(cgate['delta_g_points'])} pt</p><p class="small">{recs(cgate['delta_g_points'], N_G)} of {N_G} G records</p></div>
<div class="op">−</div>
<div><p class="small">C change</p><p class="big num">{sgn(dc)} pt</p><p class="small">{recs(dc, N_C)} of {N_C} C records ({C_found['none']} → {C_found['merged']})</p></div>
<div class="op">=</div>
<div><p class="small">difference-in-differences</p><p class="big num">{sgn(H1B['net_points'])} pt</p><p class="small">95 % CI <span class="num">{ci(H1B['ci95'])}</span></p></div>
</div>
<p>{H1B['n_nonzero']} papers non-zero, all positive ({H1B['gained']} / {H1B['lost']}); label-randomization p = <span class="num">{H1B['p']:.4f}</span>, its minimum attainable p. Gold-only display (D-2): <span class="num">{sgn(HG['h1b_gold']['net_points'])}</span> pt <span class="num">{ci(HG['h1b_gold']['ci95'])}</span>.</p>
<table class="scen"><caption>How to read G against C (invented PDT example)</caption>
<thead><tr><th>scenario</th><th>G (PDT results question)</th><th>C (wavelength question)</th><th>DiD</th></tr></thead>
<tbody>
<tr class="hit"><td><b>alias bridging</b></td><td>rises: the field supplies “photodynamic therapy”</td><td>flat: term already there</td><td>positive</td></tr>
<tr><td><b>general boost</b></td><td>rises</td><td>rises as much: any extra text helps</td><td>≈ 0</td></tr>
<tr><td><b>clutter</b></td><td>flat or falls</td><td>falls: added lines crowd chunks</td><td>≤ 0 or C cost</td></tr>
</tbody></table>
<p class="small">Observed: G {recs(cgate['delta_g_points'], N_G)}, C {recs(dc, N_C)} — the alias-bridging pattern.</p>
</div>
<div class="side">
<p class="callout"><b>The C ceiling.</b> Baseline C was {cgate['baseline_c_found10']['found']} of {cgate['baseline_c_found10']['n']} found (headroom {cgate['headroom_records']} records), so C could only fall or stay. The C gate fires only if ΔC &gt; 0, |ΔC| ≥ ½|ΔG| and its CI excludes 0; the record reads <b>{esc(cgate['note'])}</b>: it could not have fired however much C “wanted” to rise.</p>
<p>So the evidence <b>against a general boost is one-sided</b>: a general upward shift was not observable on C. The <b>placebo</b> supports the reading: glossary text without the paper's own entries moved G by {sgn(pmn['net_points'])} pt ({pmn['gained']} / {pmn['lost']}).</p>
</div></div>""",
      flag=True,
      source="<code>layer1.H1b</code>, <code>layer1.H1.c_gate</code> (stage-A2); <code>layer2.placebo</code> (stage-B); <code>h1b_gold</code> (h1b-gold); prereg §3.1, §3.2")

# --------------------------------------------------------------------------- #
# 8. Family N problem
# --------------------------------------------------------------------------- #
slide("Family N · the problem", "Neighbours vs matched random papers",
      f"""<div class="cols2">
<div class="card"><p class="fam">Why neighbours might hurt</p>
<p>OA-1000 is a sample of unrelated papers. A real collection holds each paper beside its references and citers: the same vocabulary, the same short forms, often meaning something else — about half of the abbreviations shared between papers collide (prereg §1).</p>
<p>More look-alike chunks can push the gold chunk out of the top 10.</p></div>
<div class="card"><p class="fam">Why neighbours might not</p>
<p>A neighbour is also more likely than a random paper to <b>answer</b> the question, and an answer from another paper counts as found. Judged H2 asks whether neighbours displace more than they add; the gold-only gate checks the displacement itself (§3.1).</p></div>
</div>
<div class="arms">
<div><p class="small">base</p><p class="num big2">{chunks['base']:,}</p><p class="small">chunks</p></div>
<div><p class="small">+ neighbour arm</p><p class="num big2">{chunks['neighbour']:,}</p><p class="small">chunks · {added:,} added papers</p></div>
<div><p class="small">+ random-1</p><p class="num big2">{chunks['random1']:,}</p><p class="small">chunks · same count, same field</p></div>
<div><p class="small">+ random-2</p><p class="num big2">{chunks['random2']:,}</p><p class="small">chunks · a second draw</p></div>
</div>
<p class="small">H2 compares the neighbour arm with the average of the two random draws, default retriever, no glossary, on all {N_ALL} records. Every study paper's neighbours are added at once; the study makes no claim about one paper's neighbours alone (R11).</p>""",
      source="<code>runs.&lt;arm&gt;/merged.field_coverage.chunks</code> (stage-B); <code>assumptions.neighbour_counts</code> (power-v4); prereg §1, §3.1, §5.1")

# --------------------------------------------------------------------------- #
# 9. H2
# --------------------------------------------------------------------------- #
h2_chart = hbar(
    [lab for _, lab in ARMS],
    [("missed (judged)", [N_ALL - judged[a] for a, _ in ARMS], 1, False),
     ("missed (gold chunk)", [N_ALL - gold[a] for a, _ in ARMS], 2, True)],
    xmax=140, xlabel=f"questions MISSED, of {N_ALL}: no top-10 chunk answers (judged) / gold chunk not in top 10 — fewer is better", ticks=[0, 20, 40, 60, 80, 100, 120, 140],
    title="Records not found, by corpus arm",
    desc="; ".join(f"{lab}: judged {N_ALL - judged[a]}, gold-only {N_ALL - gold[a]}" for a, lab in ARMS))
slide("Family N · H2 · confirmatory", f"H2 {esc(H2['status'])}: neighbours displaced gold chunks; judged reach: no effect larger than {d_gr['equivalence']['delta_points']:.2f} pt",
      f"""<div class="cols-chart"><div>{h2_chart}</div>
<div class="side">
<p><b>Gold-only</b> (the direction gate, quoted first): neighbour {gold['neighbour']} vs random average {num(rand_g, 1)} of {N_ALL} — <span class="num">{sgn(H2['gold_only_net_points'])}</span> pt ({recs(H2['gold_only_net_points'], N_ALL)} records), CI <span class="num">{ci(nmr['gold_only']['ci95'])}</span>. Gate passes.</p>
<p><b>Judged</b>: {judged['neighbour']} vs {num(rand_j, 1)} — <span class="num">{sgn(H2['net_points'])}</span> pt ({recs(H2['net_points'], N_ALL)} record); {H2['gained']} gained, {H2['lost']} lost; p = <span class="num">{H2['p']:.3f}</span>. <b>Not confirmed</b>, with power <span class="num">{pw_a:.2f}</span> and <span class="num">{pw_b:.2f}</span> to confirm at the planning scenarios.</p>
<p><b>Equivalence</b> (two-sided, 90 % CI <span class="num">{ci(eq['ci90'])}</span> inside ±<span class="num">{eq['delta_points']:.2f}</span>): no effect larger than {eq['delta_points']:.2f} pt ≈ ±{rec(eq['delta_points'], N_ALL):.1f} of {N_ALL} records, in either direction.</p>
<p class="small"><b>A reading, not a measurement:</b> other-paper judged hits were {ORIGIN['hits_by_origin']['neighbour|none|default']['other']} in the neighbour arm vs {ORIGIN['hits_by_origin']['random1|none|default']['other']} and {ORIGIN['hits_by_origin']['random2|none|default']['other']} in the random draws — consistent with neighbours displacing gold chunks and answering in their place. It comes from per-cell totals; no per-record transition count exists.</p>
</div></div>""",
      flag=True,
      source="<code>layer2.cells.&lt;arm&gt;|none|default.found10 / .found10_gold</code>, <code>layer2.neighbour_minus_random</code> (stage-B); <code>layer1.H2</code>, <code>fixed_sensitivities.origin</code> (stage-A2); <code>simulation.H2.rho1</code> (power-v4)")

# --------------------------------------------------------------------------- #
# 10. Layer 2 serving reads
# --------------------------------------------------------------------------- #
thr = d_gl["thresholds"]
slide("Layer 2 · descriptive", "The three serving reads",
      f"""<div class="cols3">
<div class="card"><p class="fam">Attach the merged glossary?</p>
<p class="verdict">{esc(d_gl['reading'])}</p>
<p>Lift <span class="num">{sgn(d_gl['g_lift'])}</span> pt = <b>{recs(d_gl['g_lift'], N_G)} of {N_G}</b> G records, against an action bar of <span class="num">{thr['lift_points']:.0f}</span> pt = <b>{recs(thr['lift_points'], N_G).lstrip('+')} of {N_G}</b>.</p>
<p class="small">C cost rule did not fire: ΔC <span class="num">{sgn(d_gl['delta_c'])}</span>, C_lo <span class="num">{sgn(d_gl['delta_c_ci95'][0])}</span> vs limits <span class="num">{sgn(thr['c_point'])}</span> / <span class="num">{sgn(thr['c_lower'], 1)}</span>. Quoted beside H1 and H1b confirmed (D-3).</p></div>
<div class="card"><p class="fam">Keep the reranker default?</p>
<p class="verdict">{esc(d_rr['reading'])}</p>
<p>Default − BM25 on the neighbour arm: <span class="num">{sgn(d_rr['default_minus_bm25'])}</span> pt = <b>{recs(d_rr['default_minus_bm25'], N_ALL)} of {N_ALL}</b> records ({rr['gained']} gained, {rr['lost']} lost), CI <span class="num">{ci(d_rr['ci95'])}</span>.</p>
<p class="small">Non-inferiority rule: keep if CI low &gt; <span class="num">{sgn(d_rr['margin'], 0)}</span> pt. Not merely non-inferior — better.</p></div>
<div class="card"><p class="fam">Is related-paper growth a concern?</p>
<p class="verdict">{esc(d_gr['reading'].split(':')[0])}</p>
<p>Neighbour − random <span class="num">{sgn(d_gr['neighbour_minus_random'])}</span> pt; no effect larger than <span class="num">{d_gr['equivalence']['delta_points']:.2f}</span> pt (≈ {rec(d_gr['equivalence']['delta_points'], N_ALL):.1f} of {N_ALL}).</p>
<p class="small">A concern only at ≤ <span class="num">{sgn(d_gr['margin'], 0)}</span> pt with the CI excluding 0. Beside H2 not confirmed.</p></div>
</div>
<p class="small">Layer 2 has no p values and no gates; its thresholds are the owner's judgement of what is worth acting on, fixed before the data (prereg §3.3).</p>""",
      flag=True,
      source="<code>layer2.decisions</code>, <code>layer2.default_minus_bm25_neighbour</code>, <code>layer2.serving_statements</code> (stage-B); DEVIATIONS D-3")

# --------------------------------------------------------------------------- #
# 11. Reranker chart
# --------------------------------------------------------------------------- #
rr_chart = hbar(
    [lab for _, lab in ARMS],
    [("missed, served default (reranker)", [N_ALL - judged[a] for a, _ in ARMS], 1, False),
     ("missed, BM25 alone", [N_ALL - bm25[a] for a, _ in ARMS], 3, True)],
    xmax=80, xlabel=f"questions MISSED, of {N_ALL} (judged: no top-10 chunk answers) — fewer is better", ticks=[0, 20, 40, 60, 80],
    title="Records not found, default vs BM25, by corpus arm",
    desc="; ".join(f"{lab}: default {N_ALL - judged[a]}, BM25 {N_ALL - bm25[a]}" for a, lab in ARMS))
slide("Layer 2 · reranker", "The reranker default holds in every corpus arm",
      f"""<div class="cols-chart"><div>{rr_chart}</div>
<div class="side">
<p>On the neighbour arm (the decision's figure) the default missed {N_ALL - judged['neighbour']} records and BM25 {N_ALL - bm25['neighbour']}: a <b>{recs(d_rr['default_minus_bm25'], N_ALL).lstrip('+')}-record</b> margin, CI <span class="num">{recs(d_rr['ci95'][0], N_ALL).lstrip('+')}–{recs(d_rr['ci95'][1], N_ALL).lstrip('+')}</span> records.</p>
<p>Gold-only the gap is wider: <span class="num">{sgn(rr['gold_only']['net_points'])}</span> pt ({recs(rr['gold_only']['net_points'], N_ALL)} records).</p>
<p class="small">The papers-OC reading that motivated the question (BM25 ahead of the default, exploratory, 7 papers; prereg §2 item 2) does not repeat here. Glossary <i>none</i> throughout.</p>
</div></div>""",
      flag=True,
      source="<code>layer2.cells.&lt;arm&gt;|none|default</code> and <code>|none|bm25</code> <code>.found10</code>, <code>layer2.default_minus_bm25_neighbour</code> (stage-B)")

# --------------------------------------------------------------------------- #
# 12. Judge validity
# --------------------------------------------------------------------------- #
st = SPOT["strata"]
spot_chart = hbar(
    ["answers\n(C and E agreed)", "not\n(C and E agreed)", "tiebroken\n(Argon final)"],
    [("owner agrees with the final label", [100 * st[k]["rate"] for k in ("answers", "not", "tiebroken")], 1, False)],
    xmax=100, xlabel="owner–judge agreement, % of spot-check pairs (Wilson 95 % whiskers)", ticks=[0, 25, 50, 75, 100],
    fmt=lambda v: f"{v:.0f}%", whiskers={(0, i): tuple(100 * x for x in st[k]["wilson95"]) for i, k in enumerate(("answers", "not", "tiebroken"))},
    refline=(100 * PREREG["judge_validity_rule"], f"rule {PREREG['judge_validity_rule']:.2f}"), bar_h=26, width=700, label_w=170,
    title="Owner–judge agreement by spot-check stratum",
    desc="; ".join(f"{k}: {st[k]['agree']} of {st[k]['n']}" for k in ("answers", "not", "tiebroken")))
slide("Judge validity · §13.5", "The judge-validity flag fired — and what the disagreements were",
      f"""<div class="cols2">
<div>
<div class="tiles two">
{tile(f"{SPOT['reweighted_agreement']:.3f}", f"owner–judge agreement, re-weighted, on {SPOT['n']} pairs · rule ≥ {PREREG['judge_validity_rule']:.2f}")}
{tile(f"{st['answers']['agree']}/{st['answers']['n']}", f"agreement on the judge's <i>answers</i> stratum ({st['answers']['rate']:.2f}) · rule ≥ {PREREG['judge_validity_rule']:.2f}")}
</div>
<p>Both clauses fired, so every result carries the flag and found@10-gold is printed beside every figure. Nothing was re-labelled.</p>
{spot_chart}
</div>
<div class="card"><p class="fam">The {TAGS['counts']['n']} disagreements <span class="tag explo">Exploratory · post hoc</span></p>
<div class="split" role="img" aria-label="{tags['full']} full, {tags['partial']} partial, {tags['none']} none, {tags['other']} other answer">
<span class="seg s1" style="flex:{tags['full']}">{tags['full']} full</span><span class="seg s2 tex" style="flex:{tags['partial']}">{tags['partial']} partial</span></div>
<p>The owner tagged each: <b>{tags['full']}</b> the passage answers in full, <b>{tags['partial']}</b> in part, <b>{tags['none']}</b> no answer, <b>{tags['other']}</b> another answer. None is a misreading of a passage with no answer; all sit on the partial-versus-full line.</p>
<p class="small">Read <i>partial = not</i>: judge matches {reads['partial_is_not']['judge_matches']}, owner {reads['partial_is_not']['owner_matches']} of {reads['partial_is_not']['n']}; read <i>partial = answers</i>: judge {reads['partial_is_answers']['judge_matches']}, owner {reads['partial_is_answers']['owner_matches']}. The owner set every tag and discussed some items with an LLM before deciding; the tags are descriptive, not an independent annotation, and do not lift the flag.</p></div>
</div>""",
      flag=True,
      source="<code>spot_checks.judge_validity</code> (stage-A2); <code>counts</code> (disagreement-tags); prereg §13.5")

# --------------------------------------------------------------------------- #
# 13. Chunk-boundary control (exploratory)
# --------------------------------------------------------------------------- #
ra, rb = fl["by_route"]["agree"], fl["by_route"]["tiebreak_or_other"]
assert fl["by_stratum_direction"].get("answers|answers_to_not", 0) + fl["by_stratum_direction"].get("not|not_to_answers", 0) == ra["k"]
assert fl["by_stratum_direction"].get("not|not_to_answers", 0) == 0  # every agreed flip lost an answer
na, nb = rt["by_route"]["agree"], rt["by_route"]["tiebreak_or_other"]
ctl_chart = hbar(
    [f"C and E agreed\n(n {ra['n']})", f"tiebroken\n(n {rb['n']})"],
    [("control chunk vs stage-A window", [100 * ra["rate"], 100 * rb["rate"]], 1, False),
     ("null: same window re-judged", [100 * na["rate"], 100 * nb["rate"]], 2, True)],
    xmax=40, xlabel="judge label flips, % of pairs (Wilson 95 % whiskers)", ticks=[0, 10, 20, 30, 40],
    fmt=lambda v: f"{v:.0f}%" if float(v).is_integer() else f"{v:.1f}%",
    whiskers={(0, 0): tuple(100 * x for x in ra["wilson95"]), (0, 1): tuple(100 * x for x in rb["wilson95"]),
              (1, 0): tuple(100 * x for x in na["wilson95"]), (1, 1): tuple(100 * x for x in nb["wilson95"])},
    title="Flip rate by stage-A route, control vs test-retest null",
    desc=f"Agreed route: control {ra['k']} of {ra['n']}, null {na['k']} of {na['n']}. Tiebroken: control {rb['k']} of {rb['n']}, null {nb['k']} of {nb['n']}.")
slide("Addendum · chunk-boundary control", "Boundary-respecting chunks lost some answers — next door",
      f"""<div class="cols-chart"><div>{ctl_chart}</div>
<div class="side">
<p>{fl['n_judged_both']} stage-A pairs re-judged against a chunk that respects sentence and section boundaries and covers the same text; beside it, the same windows re-judged unchanged (the null).</p>
<p><b>Agreed pairs:</b> {ra['k']} of {ra['n']} flipped vs {na['k']} of {na['n']} in the null; every agreed flip lost an answer. <b>Tiebroken:</b> {rb['k']} of {rb['n']} vs {nb['k']} of {nb['n']} — indistinguishable from the judge's own noise.</p>
<p><b>{cov['k']} of {cov['n']}</b> lost answers were found in an overlapping control chunk: the evidence moved, it did not vanish.</p>
<p class="small">Exploratory, post-freeze, judge-only. The split by route and the null were added after the overall flip rate ({fl['flip']['k']}/{fl['flip']['n']} vs {rt['flip']['k']}/{rt['flip']['n']}) had been seen; no test was run. It changes no confirmatory figure.</p>
</div></div>""",
      flag=True, exploratory=True,
      source="<code>metrics.by_route</code>, <code>metrics.retest.by_route</code>, <code>metrics.cover_any.split.by_route</code> (control-judge.json); ADDENDUM-2026-11-05")

# --------------------------------------------------------------------------- #
# 14. Limitations
# --------------------------------------------------------------------------- #
slide("Limitations", "What this does not show",
      f"""<div class="cols2">
<ul class="lim">
<li><b>A judged reach metric.</b> found@10 counts one chunk that, read alone, states the answer — not answer quality, not rank. The judge is below its validity threshold; gold-only, which needs no judge, agrees in direction on H1/H1b and points the hypothesised way on H2.</li>
<li><b>A single-chunk binary rubric.</b> Two chunks that together answer, or one that answers in part, score as not found. On the owner's tags (exploratory, post hoc), all {TAGS['counts']['n']} owner–judge disagreements sit on that partial-versus-full line.</li>
<li><b>The ceiling.</b> {pp(G_found['none'] / N_G)} of G and {pp(C_found['none'] / N_C, 0)} of C records were found before any glossary. H1 rests on {H1['gained']} records in {H1['n_nonzero']} papers; a general upward shift on C was unobservable.</li>
</ul>
<ul class="lim">
<li><b>Selected splits in the control.</b> Route, direction and stratum were read after the overall rate; the null was added after seeing it. Confirming the agreed-pair pattern needs a fresh, pre-registered test.</li>
<li><b>One corpus, one population.</b> OA-1000 only; records LLM-drafted and LLM-verified, plus the owner's check of {A['spot_checks']['record_validity']['n']} ({A['spot_checks']['record_validity']['valid']} valid); questions use the glossary's primary term.</li>
<li><b>Two families, no joint error control.</b> No sentence here combines “the glossary helps” with a growth claim.</li>
</ul></div>""",
      flag=True,
      source="RESULTS.md § Limitations; ADDENDUM § Limitations; <code>layer2.placebo.*.found10</code>, <code>layer1.H1</code>")

# --------------------------------------------------------------------------- #
# 15. Sources
# --------------------------------------------------------------------------- #
blob = f"{REPO}/blob/main/{STUDY_REL}"
links = [
    ("Pre-registration (frozen)", "prereg/PREREG-alias-study.md"),
    ("The record: RESULTS.md", "RESULTS.md"),
    ("Deviations D-1 to D-3", "DEVIATIONS.md"),
    ("Chunk-boundary control addendum", "ADDENDUM-2026-11-05-chunk-boundary-control.md"),
    ("Lab book", "LABBOOK.md"),
    ("Layer 1 artifact", "artifacts/results-stage-A2.public.json"),
    ("Layer 2 artifact", "artifacts/results-stage-B.public.json"),
    ("Control artifact", "control/artifacts/control-judge.json"),
    ("This deck's generator", "presentation/make_deck.py"),
]
slide("Sources", "Where every number comes from",
      f"""<div class="cols2">
<ul class="srcs">{''.join(f'<li><a href="{blob}/{p}">{esc(t)}</a><br><code>{esc(STUDY_REL)}/{esc(p)}</code></li>' for t, p in links)}</ul>
<div class="card"><p class="fam">Commits</p>
<ul class="tight">
<li>prereg frozen at <span class="num">{RECORD['prereg_freeze_commit']}</span>, sha256 <span class="num">{prereg_sha[:16]}…</span></li>
<li>retrieval runs: stage A <span class="num">{', '.join(runsA)}</span>, stage B <span class="num">{', '.join(runsB)}</span></li>
<li>statistics <span class="num">{prov_stats}</span>; H1b-gold display <span class="num">{prov_hg}</span>; disagreement tags <span class="num">{prov_tags}</span>; control report <span class="num">{prov_ctrl}</span></li>
<li>record merged at <span class="num">{RECORD['results_merge']}</span> (#954); addendum <span class="num">{RECORD['control_merge']}</span> (#953)</li>
</ul>
<p class="small">The deck is generated by <code>make_deck.py</code> from the committed JSON; the only typed values are prereg constants and the commit pointers above. Public artifacts hold counts, rates and hashes only — no record, paper or passage text.</p></div>
</div>""",
      source="git history; artifact <code>provenance.commit</code> fields")

# --------------------------------------------------------------------------- #
# Page shell.
# --------------------------------------------------------------------------- #
CSS = r"""
:root{
  --page:#e6e9e3; --slide:#f4f5f1; --card:#fbfbf8; --ink:#17202a; --ink2:#46515e; --muted:#69737f;
  --line:#d3d8d0; --grid:#e1e4dd; --accent:#0e5a6e; --accent-soft:#dbe9ec;
  --s1:#1f6fb2; --s2:#c4661a; --s3:#1a9a7c; --patline:rgba(255,255,255,.55);
  --flag:#7a4510; --flag-bg:#f6e7d6; --explo:#2c5d3f; --explo-bg:#dcefe2;
  --display:"Bricolage Grotesque", "Avenir Next", "Segoe UI", system-ui, sans-serif;
  --body:"IBM Plex Sans", "Segoe UI", Roboto, system-ui, sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  color-scheme:light;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --page:#0b0f13; --slide:#141a20; --card:#1a2129; --ink:#e7eaee; --ink2:#b7bfc9; --muted:#8d97a3;
    --line:#2b343e; --grid:#232b34; --accent:#79bccd; --accent-soft:#1d3138;
    --s1:#3f86cc; --s2:#cc7530; --s3:#23977a; --patline:rgba(0,0,0,.35);
    --flag:#f0c08e; --flag-bg:#3a2a1a; --explo:#9fd6b1; --explo-bg:#1c3326; color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --page:#0b0f13; --slide:#141a20; --card:#1a2129; --ink:#e7eaee; --ink2:#b7bfc9; --muted:#8d97a3;
  --line:#2b343e; --grid:#232b34; --accent:#79bccd; --accent-soft:#1d3138;
  --s1:#3f86cc; --s2:#cc7530; --s3:#23977a; --patline:rgba(0,0,0,.35);
  --flag:#f0c08e; --flag-bg:#3a2a1a; --explo:#9fd6b1; --explo-bg:#1c3326; color-scheme:dark;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--page);color:var(--ink);font-family:var(--body);-webkit-font-smoothing:antialiased;overflow:hidden}
.num,code,.tick,.val{font-family:var(--mono);font-variant-numeric:tabular-nums}
code{font-size:.86em}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px;border-radius:4px}
.stage{height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:12px 16px}
.frame{container-type:inline-size;width:min(100%, calc((100vh - 80px) * 16 / 9));aspect-ratio:16/9;position:relative}
.slide{position:absolute;inset:0;display:none;flex-direction:column;background:var(--slide);border:1px solid var(--line);border-radius:14px;
  padding:2.6em 3.2em 1.4em;font-size:1.35cqw;line-height:1.4;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.06),0 8px 30px rgba(0,0,0,.06)}
.slide.on{display:flex;animation:fade .25s ease-out}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){.slide.on{animation:none}}
.sh{margin-bottom:1em}
.kicker{margin:0 0 .3em;font-size:.82em;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:600}
h1,h2{font-family:var(--display);font-weight:700;letter-spacing:-.01em;margin:0}
h2{font-size:2.05em;line-height:1.12;max-width:30em}
.sb{flex:1;min-height:0;display:flex;flex-direction:column;gap:.8em}
.sb p{margin:0 0 .55em}
.sf{display:flex;align-items:flex-end;gap:1.2em;border-top:1px solid var(--line);padding-top:.55em;margin-top:.6em;font-size:.74em;color:var(--muted)}
.sf p{margin:0}
.sf .src{flex:1}
.flag{display:flex;align-items:center;gap:.5em;background:var(--flag-bg);color:var(--flag);padding:.25em .7em .25em .3em;border-radius:999px;font-weight:600;white-space:nowrap}
.flagmark{display:inline-grid;place-items:center;width:1.45em;height:1.45em;border-radius:50%;background:var(--flag);color:var(--flag-bg);font-weight:800}
.pg{margin-left:auto;font-family:var(--mono);white-space:nowrap}
.tag{display:inline-block;font-size:.85em;letter-spacing:.04em;padding:.1em .6em;border-radius:999px;margin-left:.5em;vertical-align:.1em;text-transform:uppercase}
.tag.explo{background:var(--explo-bg);color:var(--explo);font-weight:700}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:1.6em}
.cols2.wide-left{grid-template-columns:1.45fr 1fr}
.cols3{display:grid;grid-template-columns:repeat(3,1fr);gap:1.2em}
.dense{font-size:.9em}
.dense .card{padding:.8em 1em}
.dense .card p{margin:0 0 .45em}
table.tight-t{font-size:.84em;margin:0 0 .3em}
table.tight-t td,table.tight-t th{padding:.22em .5em}
.cols-chart{display:grid;grid-template-columns:1.35fr 1fr;gap:1.8em;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1em 1.2em}
.fam{font-weight:700;color:var(--accent);margin:0 0 .5em!important;font-size:.92em;letter-spacing:.03em;text-transform:uppercase}
.q{font-family:var(--display);font-size:1.25em;line-height:1.25;font-weight:600}
.small{font-size:.86em;color:var(--ink2)}
.center{text-align:center}
.callout{border-left:4px solid var(--accent);background:var(--accent-soft);padding:.7em 1em;border-radius:0 8px 8px 0}
.lede{font-size:1.2em;max-width:44em;color:var(--ink2)}
.big{font-family:var(--mono);font-size:2.4em;font-weight:600;line-height:1.05;margin:0 0 .15em!important;color:var(--ink)}
.big2{font-size:1.6em;font-weight:600;margin:0!important}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:1.2em}
.tiles.two{grid-template-columns:1fr 1fr;margin-bottom:.8em}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1em 1.2em}
.tile .big{font-size:2.6em}
.examples p{background:var(--card);border:1px dashed var(--line);border-radius:8px;padding:.7em 1em}
ul.tight{margin:.2em 0 .5em;padding-left:1.1em}
ul.tight li{margin:.15em 0}
.side p{font-size:.98em}
.verdict{font-family:var(--display);font-size:1.35em;font-weight:700;line-height:1.2}
.did{display:flex;align-items:center;gap:1em;margin-bottom:.8em}
.did>div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.6em .9em;flex:1}
.did .big{font-size:1.8em}
.did .op{flex:none;background:none;border:0;font-size:2em;font-family:var(--mono);color:var(--muted);padding:0}
table.scen{border-collapse:collapse;width:100%;font-size:.86em;margin:.3em 0 .4em}
table.scen caption{text-align:left;font-weight:600;color:var(--ink2);padding-bottom:.3em}
.scen th,.scen td{border-bottom:1px solid var(--line);padding:.35em .5em;text-align:left;vertical-align:top}
.scen th{color:var(--muted);font-weight:600}
.scen tr.hit td{background:var(--accent-soft)}
.arms{display:grid;grid-template-columns:repeat(4,1fr);gap:1em}
.arms>div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7em 1em}
.arms .small{margin:0}
.split{display:flex;gap:3px;height:2.1em;margin:.3em 0 .8em;border-radius:6px;overflow:hidden;font-family:var(--mono);font-size:.9em}
.seg{display:flex;align-items:center;padding:0 .7em;color:#fff;font-weight:600;background:var(--s1)}
.seg.s2{background:var(--s2)}
.seg.s3{background:var(--s3)}
.seg.miss{background:var(--muted,#8a8f8c)}
.legend-row{display:flex;flex-wrap:wrap;align-items:center;gap:.35em .9em;margin:.2em 0 .6em}
.legend-row .li{white-space:nowrap;display:inline-flex;align-items:center;gap:.3em}
.key{display:inline-block;width:1.1em;height:.8em;border-radius:2px;background:var(--s1)}
.key.s2{background:var(--s2)}
.key.s3{background:var(--s3)}
.key.miss{background:var(--muted,#8a8f8c)}
.key.tex{background-image:repeating-linear-gradient(45deg,transparent 0 3px,var(--patline) 3px 4.5px)}
.seg.tex{background-image:repeating-linear-gradient(45deg,transparent 0 5px,var(--patline) 5px 6.5px)}
.lim{margin:0;padding-left:1.1em}
.lim li{margin:0 0 .8em}
.srcs{margin:0;padding-left:1.1em;font-size:.92em}
.srcs li{margin:0 0 .45em}
.srcs code{color:var(--muted);font-size:.8em}
/* charts */
svg.chart{width:100%;height:auto;display:block;overflow:visible}@media (max-width:760px){.cols-chart>div:first-child{overflow-x:auto}svg.chart{min-width:640px}}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .axis{stroke:var(--muted);stroke-width:1}
.chart .tick{fill:var(--muted);font-size:13px}
.chart .axlabel{fill:var(--ink2);font-size:14px;font-family:var(--body)}
.chart .cat{fill:var(--ink);font-size:15px;font-weight:600;font-family:var(--body)}
.chart .cat.sub{font-weight:400;fill:var(--ink2);font-size:13px}
.chart .val{fill:var(--ink);font-size:14px}
.chart .val.muted{fill:var(--muted);font-family:var(--body);font-style:italic}
.chart .ref{stroke:var(--flag);stroke-width:2;stroke-dasharray:5 4}
.chart .reflab{fill:var(--flag);font-size:13px;font-family:var(--body);font-weight:600}
.chart .whisk{stroke:var(--ink);stroke-width:1.5}
.bar.s1,.pat-bg.s1{fill:var(--s1)} .bar.s2,.pat-bg.s2{fill:var(--s2)} .bar.s3,.pat-bg.s3{fill:var(--s3)}
.pat-line{stroke:var(--patline);stroke-width:3}
.legend{display:flex;gap:1.4em;flex-wrap:wrap;font-size:.86em;color:var(--ink2);margin-bottom:.4em}
.legend span{display:inline-flex;align-items:center;gap:.45em}
.sw{display:inline-block;width:1.1em;height:.8em;border-radius:3px;background:var(--s1)}
.sw.s2{background:var(--s2)} .sw.s3{background:var(--s3)}
.sw.tex{background-image:repeating-linear-gradient(45deg,transparent 0 3px,var(--patline) 3px 5px)}
/* diagram */
svg.diagram{width:100%;max-height:31em;height:auto;display:block}
.diagram text{font-family:var(--body);fill:var(--ink)}
.diagram .dlab{font-size:13px;letter-spacing:.12em;fill:var(--muted);font-weight:600}
.diagram .box{fill:var(--card);stroke:var(--line);stroke-width:1.5}
.diagram .box.acc{stroke:var(--accent);stroke-width:2}
.diagram .box.g{stroke:var(--s1);stroke-width:2.5}
.diagram .box.c{stroke:var(--s3);stroke-width:2.5;stroke-dasharray:7 4}
.diagram .field{fill:var(--accent-soft);stroke:var(--accent);stroke-width:1.5}
.diagram .dt{font-size:14px;font-weight:700;fill:var(--ink2)}
.diagram .dq{font-size:17px}
.diagram .dmono{font-family:var(--mono);font-size:15px;fill:var(--ink)}
.diagram .dsmall{font-size:13.5px;fill:var(--ink2)}
.diagram .dwarn{font-size:13px;font-weight:700;letter-spacing:.08em;fill:var(--flag)}
.diagram .flow{stroke:var(--ink2);stroke-width:2;fill:none}
.diagram .dash{stroke-dasharray:6 5;stroke:var(--accent)}
.diagram .ahead{fill:var(--ink2)}
/* title */
.slide.title{justify-content:center;background:linear-gradient(160deg,var(--slide) 55%,var(--accent-soft))}
.titlewrap{flex:1;display:flex;flex-direction:column;justify-content:center}
.slide.title h1{font-size:4.6em;line-height:1;margin:.1em 0 .3em}
.meta{display:grid;gap:.5em;margin:1.4em 0 0;max-width:56em}
.meta div{display:grid;grid-template-columns:10em 1fr;gap:1em}
.meta dt{color:var(--muted);font-weight:600}
.meta dd{margin:0}
.hint{margin-top:2em;color:var(--muted);font-size:.85em}
/* controls */
.controls{display:flex;align-items:center;gap:.6em;font-family:var(--mono);font-size:14px;color:var(--ink2)}
.controls button{font:inherit;color:var(--ink);background:var(--slide);border:1px solid var(--line);border-radius:8px;padding:.35em .8em;cursor:pointer}
.controls button:hover{border-color:var(--accent)}
.counter{min-width:5.5em;text-align:center}
/* phone / narrow: stack, no 16:9 */
@media (max-width: 760px){
  body{overflow:auto}
  .stage{height:auto;display:block;padding:12px 16px}
  .frame{width:100%;aspect-ratio:auto;container-type:normal}
  .slide,.slide.on{position:relative;display:flex;font-size:15px;padding:1.3em 1.1em 1em;margin:0 0 16px;animation:none}
  h2{font-size:1.5em}
  .cols2,.cols2.wide-left,.cols3,.cols-chart,.tiles,.tiles.two,.arms{grid-template-columns:1fr}
  .did{flex-wrap:wrap}
  .sf{flex-wrap:wrap}
  .flag{white-space:normal}
  code,.num,.src{overflow-wrap:anywhere}
  .meta div{grid-template-columns:1fr;gap:0}
  .slide.title h1{font-size:2.6em}
  .controls{display:none}
}
@media print{
  @page{size:13.333in 7.5in;margin:0}
  :root{--page:#fff;--slide:#fff;--card:#fff}
  body{overflow:visible;background:#fff;print-color-adjust:exact;-webkit-print-color-adjust:exact}
  .stage{display:block;height:auto;padding:0}
  .frame{width:13.333in;aspect-ratio:auto}
  .slide,.slide.on{position:relative;display:flex;width:13.333in;height:7.5in;border:0;border-radius:0;box-shadow:none;break-after:page;page-break-after:always;animation:none;font-size:16px}
  .controls{display:none}
}
"""

JS = r"""
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var n=slides.length, i=0, counter=document.getElementById('counter');
  var stacked=window.matchMedia('(max-width: 760px)');
  slides.forEach(function(s,k){ s.id='s'+(k+1); s.setAttribute('aria-label',(k+1)+' of '+n);
    var pg=s.querySelector('.pg'); if(pg) pg.textContent=(k+1)+' / '+n; });
  function fromHash(){ var m=/^#(?:s)?(\d+)$/.exec(location.hash); return m?Math.min(Math.max(parseInt(m[1],10)-1,0),n-1):0; }
  function show(k,push){
    i=Math.min(Math.max(k,0),n-1);
    slides.forEach(function(s,j){ s.classList.toggle('on',j===i); s.setAttribute('aria-hidden', (!stacked.matches && j!==i)?'true':'false'); });
    counter.textContent=(i+1)+' / '+n;
    var h='#'+(i+1); if(push && location.hash!==h) history.replaceState(null,'',h);
    if(stacked.matches && push) slides[i].scrollIntoView({block:'start'});
  }
  document.addEventListener('keydown',function(e){
    if(e.altKey||e.ctrlKey||e.metaKey) return;
    var t=e.target; if(t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;
    var k=e.key;
    if(k==='ArrowRight'||k==='ArrowDown'||k==='PageDown'||(k===' '&&!e.shiftKey)){ e.preventDefault(); show(i+1,true); }
    else if(k==='ArrowLeft'||k==='ArrowUp'||k==='PageUp'||(k===' '&&e.shiftKey)){ e.preventDefault(); show(i-1,true); }
    else if(k==='Home'){ e.preventDefault(); show(0,true); }
    else if(k==='End'){ e.preventDefault(); show(n-1,true); }
  });
  document.getElementById('frame').addEventListener('click',function(e){
    if(stacked.matches) return;
    if(e.target.closest('a,button,summary,input')) return;
    var sel=window.getSelection&&String(window.getSelection()); if(sel) return;
    var r=this.getBoundingClientRect(); show(e.clientX-r.left<r.width/2?i-1:i+1,true);
  });
  document.getElementById('prev').onclick=function(){ show(i-1,true); };
  document.getElementById('next').onclick=function(){ show(i+1,true); };
  document.getElementById('theme').onclick=function(){
    var d=document.documentElement, cur=d.getAttribute('data-theme');
    var dark=cur?cur==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;
    d.setAttribute('data-theme',dark?'light':'dark');
  };
  window.addEventListener('hashchange',function(){ show(fromHash(),false); });
  show(fromHash(),false);
  if(stacked.matches && location.hash) slides[i].scrollIntoView({block:'start'});
})();
"""

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alias study deck</title>
<meta name="description" content="Pre-registered alias study (OA-1000): glossary attachment and citation-neighbour growth — the results, generated from the committed artifacts.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:ital,wght@0,400;0,600;0,700;1,400&display=swap">
<style>{CSS}</style>
</head>
<body>
{SVG_DEFS}
<main class="stage">
<div class="frame" id="frame" aria-live="polite">
{chr(10).join(SLIDES)}
</div>
<nav class="controls" aria-label="Slide navigation">
<button id="prev" type="button" aria-label="Previous slide">&larr;</button>
<span class="counter" id="counter" aria-live="polite">1 / {len(SLIDES)}</span>
<button id="next" type="button" aria-label="Next slide">&rarr;</button>
<button id="theme" type="button" aria-label="Toggle light or dark theme">theme</button>
</nav>
</main>
<script>{JS}</script>
</body>
</html>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page, encoding="utf-8")
print(f"wrote {OUT.relative_to(DOCS.parent)}: {len(SLIDES)} slides, {OUT.stat().st_size // 1024} KB")
