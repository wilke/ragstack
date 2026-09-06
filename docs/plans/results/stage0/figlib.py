"""Minimal dependency-free SVG plotting. matplotlib is not installed in any
interpreter on this host and nothing may be installed, so the figures are
emitted as hand-written SVG. Designed to stay legible in greyscale: series are
separated by marker shape and dash pattern first, tone second."""
from __future__ import annotations
import math, html

FONT = "'Helvetica Neue',Helvetica,Arial,'DejaVu Sans',sans-serif"
INK = "#111111"
MID = "#555555"
GRID = "#d8d8d8"
FAINT = "#f0f0f0"


def esc(s):
    return html.escape(str(s), quote=True)


class Fig:
    def __init__(self, w, h, title=None, subtitle=None):
        self.w, self.h = w, h
        self.parts = []
        self.title, self.subtitle = title, subtitle

    def add(self, s):
        self.parts.append(s)

    # ---- primitives -------------------------------------------------
    def text(self, x, y, s, size=12, anchor="start", fill=INK, weight="normal",
             style="normal", family=FONT, opacity=1.0, rotate=None):
        tr = f' transform="rotate({rotate},{x:.2f},{y:.2f})"' if rotate else ""
        self.add(f'<text x="{x:.2f}" y="{y:.2f}" font-family="{family}" font-size="{size}" '
                 f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}" '
                 f'font-style="{style}" opacity="{opacity}"{tr}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke=INK, w=1.0, dash=None, opacity=1.0, cap="butt"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                 f'stroke="{stroke}" stroke-width="{w}" stroke-linecap="{cap}" opacity="{opacity}"{d}/>')

    def path(self, pts, stroke=INK, w=1.5, dash=None, fill="none", opacity=1.0):
        if not pts:
            return
        d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{w}" '
                 f'stroke-linejoin="round" opacity="{opacity}"{da}/>')

    def rect(self, x, y, w, h, fill="none", stroke=None, sw=1.0, opacity=1.0, rx=0):
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.add(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w,0):.2f}" height="{max(h,0):.2f}" '
                 f'rx="{rx}" fill="{fill}" opacity="{opacity}"{st}/>')

    def marker(self, x, y, shape="o", size=5, fill=INK, stroke=None, sw=1.4, opacity=1.0):
        st = stroke or fill
        s = size
        if shape == "o":
            self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{s}" fill="{fill}" '
                     f'stroke="{st}" stroke-width="{sw}" opacity="{opacity}"/>')
        elif shape == "s":
            self.add(f'<rect x="{x-s:.2f}" y="{y-s:.2f}" width="{2*s}" height="{2*s}" '
                     f'fill="{fill}" stroke="{st}" stroke-width="{sw}" opacity="{opacity}"/>')
        elif shape == "^":
            p = f"{x:.2f},{y-s*1.15:.2f} {x-s:.2f},{y+s*0.8:.2f} {x+s:.2f},{y+s*0.8:.2f}"
            self.add(f'<polygon points="{p}" fill="{fill}" stroke="{st}" stroke-width="{sw}" opacity="{opacity}"/>')
        elif shape == "v":
            p = f"{x:.2f},{y+s*1.15:.2f} {x-s:.2f},{y-s*0.8:.2f} {x+s:.2f},{y-s*0.8:.2f}"
            self.add(f'<polygon points="{p}" fill="{fill}" stroke="{st}" stroke-width="{sw}" opacity="{opacity}"/>')
        elif shape == "d":
            p = f"{x:.2f},{y-s*1.25:.2f} {x+s*1.05:.2f},{y:.2f} {x:.2f},{y+s*1.25:.2f} {x-s*1.05:.2f},{y:.2f}"
            self.add(f'<polygon points="{p}" fill="{fill}" stroke="{st}" stroke-width="{sw}" opacity="{opacity}"/>')
        elif shape == "x":
            self.line(x-s, y-s, x+s, y+s, stroke=st, w=sw+0.4)
            self.line(x-s, y+s, x+s, y-s, stroke=st, w=sw+0.4)

    def svg(self):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">'
                f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>')
        return head + "".join(self.parts) + "</svg>"

    def save(self, path):
        with open(path, "w") as f:
            f.write(self.svg())
        return path


class Axes:
    """A rectangular data area with linear or log2 scales."""
    def __init__(self, fig, x0, y0, w, h, xlim, ylim, xlog2=False, ylog2=False):
        self.f, self.x0, self.y0, self.w, self.h = fig, x0, y0, w, h
        self.xlog2, self.ylog2 = xlog2, ylog2
        self.xlim = (math.log2(xlim[0]), math.log2(xlim[1])) if xlog2 else xlim
        self.ylim = (math.log2(ylim[0]), math.log2(ylim[1])) if ylog2 else ylim

    def X(self, v):
        v = math.log2(v) if self.xlog2 else v
        a, b = self.xlim
        return self.x0 + (v - a) / (b - a) * self.w

    def Y(self, v):
        v = math.log2(v) if self.ylog2 else v
        a, b = self.ylim
        return self.y0 + self.h - (v - a) / (b - a) * self.h

    def frame(self, grid_y=None, grid_x=None, box=True):
        f = self.f
        for gv in (grid_y or []):
            y = self.Y(gv)
            f.line(self.x0, y, self.x0 + self.w, y, stroke=GRID, w=1)
        for gv in (grid_x or []):
            x = self.X(gv)
            f.line(x, self.y0, x, self.y0 + self.h, stroke=GRID, w=1)
        if box:
            f.rect(self.x0, self.y0, self.w, self.h, fill="none", stroke="#999999", sw=1)

    def xticks(self, vals, labels=None, size=11, dy=17, rotate=None):
        labels = labels or [str(v) for v in vals]
        for v, lab in zip(vals, labels):
            x = self.X(v)
            self.f.line(x, self.y0 + self.h, x, self.y0 + self.h + 4, stroke="#777777", w=1)
            if rotate:
                self.f.text(x, self.y0 + self.h + dy, lab, size=size, anchor="end",
                            fill=MID, rotate=rotate)
            else:
                self.f.text(x, self.y0 + self.h + dy, lab, size=size, anchor="middle", fill=MID)

    def yticks(self, vals, labels=None, size=11, dx=8):
        labels = labels or [str(v) for v in vals]
        for v, lab in zip(vals, labels):
            y = self.Y(v)
            self.f.line(self.x0 - 4, y, self.x0, y, stroke="#777777", w=1)
            self.f.text(self.x0 - dx, y + 4, lab, size=size, anchor="end", fill=MID)

    def xlabel(self, s, dy=42, size=12.5):
        self.f.text(self.x0 + self.w / 2, self.y0 + self.h + dy, s, size=size, anchor="middle", fill=INK)

    def ylabel(self, s, dx=48, size=12.5):
        x = self.x0 - dx
        y = self.y0 + self.h / 2
        self.f.text(x, y, s, size=size, anchor="middle", fill=INK, rotate=-90)

    def clipline(self, pts, **kw):
        self.f.path([(self.X(a), self.Y(b)) for a, b in pts], **kw)


def wrap(fig, x, y, s, width_px, size=11, fill=MID, lh=1.45, anchor="start",
         weight="normal", style="normal"):
    """Greedy word wrap at an estimated 0.52*size px per character."""
    cpl = max(8, int(width_px / (0.56 * size)))
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > cpl:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    for i, ln in enumerate(lines):
        fig.text(x, y + i * size * lh, ln, size=size, fill=fill, anchor=anchor,
                 weight=weight, style=style)
    return y + len(lines) * size * lh
