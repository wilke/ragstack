"""Figure — pointed reach against corpus size, measured and projected.

One panel, read straight from ``artifacts/pointed-scale/levels.json`` and ``fit.json``:
the six index arms' pointed ``ERET`` at the four measured corpus sizes, the linear-in-
log10(N) fit drawn **solid over the measured range and dashed beyond it**, and the
[0.15, 0.90] window r3 §11 guard 1 requires the population to sit inside. The logit fit is
drawn as a fainter dotted line so the reader can see how much of the projection is the
functional form rather than the data.

Run from this directory:  /rag/envs/ragstack/bin/python3 s0s_fig.py
"""
from __future__ import annotations

import json
import math

from figlib import Axes, Fig, MID, wrap

import s0s_common as SC

LV = json.loads((SC.ART / "levels.json").read_text())
FT = json.loads((SC.ART / "fit.json").read_text())
OUT = "figures/fig-pointed-reach-vs-scale.svg"

ARMS = [
    ("fixed_tok256_ov0pct", "256/0", "o", "#111111", None),
    ("fixed_tok512_ov0pct", "512/0", "s", "#111111", "6,3"),
    ("fixed_tok512", "512/64 (shipping)", "d", "#b03030", None),
    ("header512", "header512", "x", "#555555", "4,2,1,2"),
    ("fixed_tok1024_ov0pct", "1024/0", "^", "#111111", "2,3"),
    ("fixed_tok2048_ov0pct", "2048/0", "v", "#111111", "9,4"),
]

W, H = 1000, 620
f = Fig(W, H)
f.text(46, 40, "Reach falls with corpus size — far too slowly to matter",
       size=20, weight="600")
wrap(f, 46, 63,
     "Pointed ERET at B = 16,384 SFR tokens, hybrid retrieval with the reranker on, over "
     "nested seeded subsamples of the 32,663-document corpus (three draws at 4k / 8k / 16k, "
     "the full corpus once). Solid: the linear-in-log10(N) fit over the measured range. "
     "Dashed: the same fit extrapolated to the owner's 150k and 500k targets — a PROJECTION, "
     "not a measurement. Dotted: the logit fit, which is bounded by construction and is the "
     "more conservative reading of the same points. r3 §11 guard 1 needs the population "
     "INSIDE the shaded band; every arm is above it at every size, measured and projected.",
     W - 92, size=12.5)

AX0, AY0, AW, AH = 96, 210, 620, 320
ax = Axes(f, AX0, AY0, AW, AH, (3000, 700_000), (0.60, 1.0), xlog2=True)
ax.frame(grid_y=[0.7, 0.8, 0.9, 1.0],
         grid_x=[4000, 8000, 16000, 32663, 150_000, 500_000])

# the window ceiling
yw = ax.Y(0.90)
f.rect(AX0, yw, AW, AY0 + AH - yw, fill="#f3e6e6", stroke=None)
f.line(AX0, yw, AX0 + AW, yw, stroke="#b03030", w=1.6)
f.text(AX0 + 6, yw + 15, "0.90 — guard 1's ceiling; above this the population is at its "
       "ceiling and cannot separate arms", size=10.5, fill="#b03030", weight="600")

# the measured / projected divide
xd = ax.X(SC.FULL_N)
f.line(xd, AY0, xd, AY0 + AH, stroke="#999999", w=1.2, dash="3,3")
f.text(xd + 6, AY0 + 14, "measured ←  | →  projected", size=10.5, fill=MID)

ax.xticks([4000, 8000, 16000, 32663, 150_000, 500_000],
          ["4k", "8k", "16k", "32.7k", "150k", "500k"])
ax.yticks([0.6, 0.7, 0.8, 0.9, 1.0],
          ["0.60", "0.70", "0.80", "0.90", "1.00"])
ax.xlabel("corpus size — documents (log scale)")
ax.ylabel("pointed ERET")

rows = [r for r in LV["levels"]
        if r["mode"] == SC.PRIMARY_MODE and r["rerank"] == SC.PRIMARY_RERANK]

LEGY = AY0
for i, (arm, lab, shape, tone, dash) in enumerate(ARMS):
    fit = FT["fits"].get(arm)
    if not fit:
        continue
    a, b = fit["linear"]["a"], fit["linear"]["b"]
    az, bz = fit["logit"]["a"], fit["logit"]["b"]
    solid = [(n, a + b * math.log10(n)) for n in (3000, SC.FULL_N)]
    proj = [(n, a + b * math.log10(n)) for n in (SC.FULL_N, 700_000)]
    ax.clipline(solid, stroke=tone, w=1.6, dash=dash)
    ax.clipline(proj, stroke=tone, w=1.4, dash="5,4", opacity=0.75)
    lg = [(n, 1 / (1 + math.exp(-(az + bz * math.log10(n)))))
          for n in (SC.FULL_N, 700_000)]
    ax.clipline(lg, stroke=tone, w=1.1, dash="1,3", opacity=0.6)
    for r in rows:
        if r["arm"] == arm:
            f.marker(ax.X(r["size"]), ax.Y(r["ERET"]), shape=shape, size=4.6,
                     fill=tone, opacity=0.85)
    y = LEGY + 18 * i
    f.line(AX0 + AW + 26, y - 4, AX0 + AW + 54, y - 4, stroke=tone, w=1.6, dash=dash)
    f.marker(AX0 + AW + 40, y - 4, shape=shape, size=4.6, fill=tone)
    f.text(AX0 + AW + 62, y, lab, size=11.5, fill="#111111")

# projected values at 500k, printed
y0 = LEGY + 18 * len(ARMS) + 26
f.text(AX0 + AW + 26, y0, "projected ERET @500k", size=11, weight="600")
for i, (arm, lab, _s, tone, _d) in enumerate(ARMS):
    fit = FT["fits"].get(arm)
    if not fit:
        continue
    p = fit["projection"]["500000"]
    f.text(AX0 + AW + 26, y0 + 17 * (i + 1),
           f"{lab.split(' ')[0]}  {p['linear']:.3f} / {p['logit']:.3f}",
           size=10.5, fill=tone)
f.text(AX0 + AW + 26, y0 + 17 * (len(ARMS) + 1) + 6, "(linear / logit)", size=10,
       fill=MID, style="italic")

f.save(OUT)
print("wrote", OUT)
