"""Stage 0 figure - why EUC@4096 is on a floor, and why no budget lifts it off.

Two panels, both read straight from artifacts/floor_diagnostic.json:

  A  the exact factorisation EUC = P(doc packed) x P(covered | packed), one point
     per index arm, against iso-EUC contours and the EUC = 0.15 floor SS8.5.7 row 9
     requires the endpoint to clear.
  B  the row-9 recalibration: EUC against the generator budget B, out to the
     ceiling where the whole frozen D = 50 pool is packed.

Run from this directory:  python3 fig_stage0.py
"""
import json
from figlib import Fig, Axes, INK, MID, GRID, wrap

D = json.load(open("artifacts/floor_diagnostic.json"))
OUT = "figures/fig-stage0-euc-floor.svg"

DEC = D["decomposition_at_B4096"]
CUR = D["budget_curve"]

# arm -> (panel-A label, panel-B label, marker, tone). Ordered fine -> coarse,
# then the two structural arms, so the size trade in panel A reads left to right.
ARMS = [
    ("fixed_tok256_ov0pct",  "256/0",             "256",     "o", "#111111"),
    ("fixed_tok512_ov0pct",  "512/0",             "512/0",   "s", "#111111"),
    ("fixed_tok512",         "512/64 (shipping)", "512/64",  "d", "#b03030"),
    ("fixed_tok1024_ov0pct", "1024/0",            "1024",    "^", "#111111"),
    ("fixed_tok2048_ov0pct", "2048/0",            "2048",    "v", "#111111"),
    ("header512",            "header512",         "hdr512",  "x", "#555555"),
    ("parent256",            "parent256",         "par256",  "x", "#555555"),
]
DASH = {"fixed_tok256_ov0pct": None, "fixed_tok512_ov0pct": "6,3",
        "fixed_tok512": None, "fixed_tok1024_ov0pct": "2,3",
        "fixed_tok2048_ov0pct": "9,4", "header512": "4,2,1,2",
        "parent256": "1,3"}

REQ_LO = 0.15
RED = "#b03030"

W, H = 1000, 700
f = Fig(W, H)

f.text(46, 40, "The endpoint never leaves the floor, and no budget lifts it off",
       size=20, weight="600")
wrap(f, 46, 63,
     "Evidence-unit coverage on the 10 development topics, summary variant. SS8.5.7 row 9 requires the development EUC to sit inside "
     "[0.15, 0.90]: below that the endpoint cannot register a difference, and every variance estimate taken from it is the standard deviation of a "
     "quantity pinned near zero. Measured: 0.017-0.069. Both panels are read from artifacts/floor_diagnostic.json.",
     W - 92, size=12.5)

# ---------------------------------------------------------------- panel A
AX0, AY0, AW, AH = 96, 182, 360, 348
ax = Axes(f, AX0, AY0, AW, AH, (0, 0.26), (0, 1.0))
ax.frame(grid_y=[0.2, 0.4, 0.6, 0.8], grid_x=[0.05, 0.10, 0.15, 0.20, 0.25])

# iso-EUC contours: P_cov = e / P_pack, labelled where each meets the top edge
for e, lab, dash, tone, wgt in [
        (0.02, "0.02",           "2,3", "#c0c0c0", "normal"),
        (0.05, "0.05",           "2,3", "#c0c0c0", "normal"),
        (REQ_LO, "0.15  ← floor", None,  RED,      "600")]:
    pts, x = [], e
    while x <= 0.26:
        y = e / x
        if y <= 1.0:
            pts.append((x, y))
        x += 0.002
    ax.clipline(pts, stroke=tone, w=1.6 if e == REQ_LO else 1.1, dash=dash)
    f.text(ax.X(e), AY0 + 15, lab, size=10.5, anchor="middle", fill=tone, weight=wgt)

# label offsets hand-placed: 512/0 and 512/64 sit 12 px apart and must not collide
OFF = {"fixed_tok256_ov0pct":  (0, -13, "middle"),
       "fixed_tok512_ov0pct":  (-10, -6, "end"),
       "fixed_tok512":         (10, 17, "start"),
       "fixed_tok1024_ov0pct": (11, 4, "start"),
       "fixed_tok2048_ov0pct": (11, 4, "start"),
       "header512":            (-10, 6, "end"),
       "parent256":            (11, 4, "start")}

for key, lab, _b, shape, tone in ARMS:
    d = DEC[key]
    px, py = ax.X(d["P_doc_packed"]), ax.Y(d["P_covered_given_packed"])
    f.marker(px, py, shape=shape, size=5.5, fill=tone, sw=1.4)
    dx, dy, anc = OFF[key]
    f.text(px + dx, py + dy, f"{lab} → {d['product']:.3f}", size=11, anchor=anc,
           fill=tone, weight="600" if key == "fixed_tok512" else "normal")

ax.xticks([0, 0.05, 0.10, 0.15, 0.20, 0.25],
          ["0", ".05", ".10", ".15", ".20", ".25"])
ax.yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0], ["0", ".2", ".4", ".6", ".8", "1.0"])
ax.xlabel("P(a unit's document is in the packed context)", dy=40, size=12)
ax.ylabel("P(unit fully contained | its document packed)", dx=46, size=12)
f.text(AX0, AY0 - 16, "A   the factorisation at B = 4,096", size=13.5, weight="600")

# ---------------------------------------------------------------- panel B
BX0, BY0, BW, BH = 596, 182, 296, 348
BUD = ["4096", "8192", "16384", "32768", "1000000000"]
bx = Axes(f, BX0, BY0, BW, BH, (-0.25, 4.25), (0, 0.30))
bx.f.rect(BX0, bx.Y(0.30), BW, bx.Y(REQ_LO) - bx.Y(0.30), fill="#eef3ee")
bx.frame(grid_y=[0.05, 0.10, 0.20, 0.25], grid_x=[0, 1, 2, 3, 4])
f.line(BX0, bx.Y(REQ_LO), BX0 + BW, bx.Y(REQ_LO), stroke=RED, w=1.6)
f.text(BX0 + 7, bx.Y(REQ_LO) - 7, "0.15  floor (row 9)", size=10.5, fill=RED, weight="600")
f.text(BX0 + 7, bx.Y(0.30) + 15, "required band, 0.15-0.90", size=10.5, fill="#4a6a4a")
f.text(BX0 + 7, bx.Y(0.30) + 29, "(y-axis truncated at 0.30)", size=10, fill="#4a6a4a")

# end labels, de-collided downward with a leader where the label had to move
placed = []
for key, _a, lab, shape, tone in ARMS:
    pts = [(i, CUR[key][b]["EUC"]) for i, b in enumerate(BUD)]
    bx.clipline(pts, stroke=tone, w=1.5, dash=DASH[key],
                opacity=1.0 if key == "fixed_tok512" else 0.85)
    for i, v in pts:
        f.marker(bx.X(i), bx.Y(v), shape=shape, size=4, fill=tone, sw=1.2)
    placed.append([bx.Y(pts[-1][1]), lab, tone, key])

placed.sort()
for i in range(1, len(placed)):
    if placed[i][0] - placed[i - 1][0] < 13:
        placed[i][0] = placed[i - 1][0] + 13
LX = BX0 + BW + 10
for ty, lab, tone, key in placed:
    y0 = bx.Y(CUR[key][BUD[-1]]["EUC"])
    if abs(ty - y0) > 2:
        f.line(BX0 + BW + 2, y0, LX - 3, ty - 4, stroke=tone, w=0.8, opacity=.55)
    f.text(LX, ty, lab, size=10.5, fill=tone,
           weight="600" if key == "fixed_tok512" else "normal")

bx.xticks([0, 1, 2, 3, 4], ["4k", "8k", "16k", "32k", "ceiling"])
bx.yticks([0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
          ["0", ".05", ".10", ".15", ".20", ".25", ".30"])
bx.xlabel("generator-token budget B   (ceiling = whole D = 50 pool)", dy=40, size=12)
bx.ylabel("EUC", dx=44, size=12)
f.text(BX0, BY0 - 16, "B   the row-9 recalibration", size=13.5, weight="600")

# ---------------------------------------------------------------- footnotes
y = 590
y = wrap(f, 46, y,
         "Panel A. Grey contours are iso-EUC. The two factors move in opposite directions with chunk size - fine chunks reach more documents, coarse "
         "chunks contain more of a span once reached - which is the trade the study exists to measure. It is invisible here because the first factor is "
         "catastrophically small for every arm: at B = 4,096 the packed context holds 1.1 of a topic's 10.1 unit-bearing documents.",
         W - 92, size=11.5)
y = wrap(f, 46, y + 5,
         "Panel B. Only 2048/0 crosses the floor, at B = 16,384, and it does so for a reason that discredits the measurement rather than saving it: at "
         "the ceiling EUC is very nearly proportional to chunk size, because 50 chunks of size S simply supply more text. D is frozen at 50 by P.4 "
         "(= production rerank_candidates), so the recalibration row 9 authorises cannot be performed inside the frozen design.",
         W - 92, size=11.5)

f.save(OUT)
print("wrote", OUT)
