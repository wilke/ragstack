"""Step 1/2 -- render every table of ``RESULTS-pointed-at-scale.md`` from the artifacts.

No number in the write-up is transcribed by hand: this module reads ``levels.json``,
``fit.json`` and ``difficulty.json`` (and, if step 2 ran, ``scale_levels.json``) and emits
``artifacts/pointed-scale/TABLES.md`` with the same ``<!-- TABLE: ... -->`` fences
``s0b_writeup.py`` splices on.
"""
from __future__ import annotations

import json
import statistics as st
from collections import defaultdict

import s0s_common as SC
import s0b_common as K

ARM_ORDER = ["fixed_tok256_ov0pct", "fixed_tok512_ov0pct", "fixed_tok512", "header512",
             "fixed_tok1024_ov0pct", "fixed_tok2048_ov0pct"]


def fence(title: str, lines: list[str]) -> str:
    return (f"<!-- TABLE: {title} -->\n\n**{title}**\n\n" + "\n".join(lines)
            + "\n\n<!-- /TABLE -->\n")


def fmt(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def main() -> None:
    lv = json.loads((SC.ART / "levels.json").read_text())
    fit = json.loads((SC.ART / "fit.json").read_text())
    dif = json.loads((SC.ART / "difficulty.json").read_text())
    rows = lv["levels"]

    sizes = sorted({r["size"] for r in rows})
    out: list[str] = []

    # -- 1. reach vs size ------------------------------------------------------
    def size_table(field: str, title: str) -> str:
        agg: dict = defaultdict(list)
        for r in rows:
            if r["mode"] != SC.PRIMARY_MODE or r["rerank"] != SC.PRIMARY_RERANK:
                continue
            if r[field] is not None:
                agg[(r["arm"], r["size"])].append(r[field])
        head = ("| arm | " + " | ".join(f"N = {s:,}" for s in sizes)
                + " | Δ 4k→32.7k |")
        sep = "|---|" + "---|" * (len(sizes) + 1)
        body = []
        for a in ARM_ORDER:
            cells = []
            for s in sizes:
                v = agg.get((a, s), [])
                if not v:
                    cells.append("—")
                elif len(v) == 1:
                    cells.append(f"{v[0]:.3f}")
                else:
                    cells.append(f"{st.mean(v):.3f} ±{(max(v)-min(v))/2:.3f}")
            first = agg.get((a, sizes[0]), [])
            last = agg.get((a, sizes[-1]), [])
            d = (f"{st.mean(last) - st.mean(first):+.3f}"
                 if first and last else "—")
            body.append(f"| `{a}` | " + " | ".join(cells) + f" | {d} |")
        return fence(title, [head, sep] + body)

    out.append(size_table(
        "ERET", "Pointed reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR "
                "(mean over draws ± half-range)"))
    out.append(size_table(
        "EPACK_given_reach",
        "Pointed EPACK | reach vs corpus size — `hybrid` + rerank, B = 16,384 SFR"))

    # -- 2. mode x size --------------------------------------------------------
    agg: dict = defaultdict(list)
    for r in rows:
        if r["rerank"] != "on":
            continue
        agg[(r["arm"], r["mode"], r["size"])].append(r["ERET"])
    head = "| arm | mode | " + " | ".join(f"N = {s:,}" for s in sizes) + " |"
    sep = "|---|---|" + "---|" * len(sizes)
    body = []
    for a in ARM_ORDER:
        for m in K.MODES:
            cells = [(f"{st.mean(agg[(a, m, s)]):.3f}" if agg.get((a, m, s)) else "—")
                     for s in sizes]
            body.append(f"| `{a}` | `{m}` | " + " | ".join(cells) + " |")
    out.append(fence("Pointed ERET by mode and corpus size — reranker on, B = 16,384 SFR",
                     [head, sep] + body))

    # -- 3. the fit and the projection ----------------------------------------
    head = ("| arm | slope / decade (linear) | slope 95 % CI | R² | "
            "reach @150k linear [95 %] | reach @150k logit [95 %] | "
            "reach @500k linear [95 %] | reach @500k logit [95 %] |")
    sep = "|---|---|---|---|---|---|---|---|"
    body = []
    for a in ARM_ORDER:
        f = fit["fits"].get(a)
        if not f:
            continue
        ci = f["slope_ci95"]["linear"]
        cells = [f"`{a}`", f"{f['linear']['b']:+.4f}",
                 f"[{ci[0]:+.4f}, {ci[1]:+.4f}]", f"{f['linear']['r2']:.3f}"]
        for t in SC.TARGETS:
            for link in ("linear", "logit"):
                p = f["projection"][str(t)][link]
                c = f["projection_ci95"][str(t)][link]
                cells.append(f"{p:.3f} [{c[0]:.3f}, {c[1]:.3f}]")
        body.append("| " + " | ".join(cells) + " |")
    out.append(fence("Reach vs log₁₀(corpus size) — fit and projection "
                     "(PROJECTION, not a measurement)", [head, sep] + body))

    # -- 4. guard 1 at each measured size -------------------------------------
    lo, hi = SC.WINDOW
    head = ("| N | arms with ERET in [0.15, 0.90] | arms with EPACK\\|reach in window | "
            "guard 1 verdict |")
    sep = "|---|---|---|---|"
    body = []
    for s in sizes:
        er = [st.mean([r["ERET"] for r in rows
                       if r["arm"] == a and r["size"] == s
                       and r["mode"] == SC.PRIMARY_MODE
                       and r["rerank"] == SC.PRIMARY_RERANK]) for a in ARM_ORDER]
        ep = [st.mean([r["EPACK_given_reach"] for r in rows
                       if r["arm"] == a and r["size"] == s
                       and r["mode"] == SC.PRIMARY_MODE
                       and r["rerank"] == SC.PRIMARY_RERANK
                       and r["EPACK_given_reach"] is not None]) for a in ARM_ORDER]
        ne = sum(1 for v in er if lo <= v <= hi)
        np_ = sum(1 for v in ep if lo <= v <= hi)
        verdict = "PASS" if ne == len(ARM_ORDER) and np_ == len(ARM_ORDER) else "FAIL"
        body.append(f"| {s:,} | {ne}/{len(ARM_ORDER)} | {np_}/{len(ARM_ORDER)} | "
                    f"{verdict} |")
    out.append(fence("r3 §11 guard 1 (the window half) at each measured corpus size",
                     [head, sep] + body))

    # -- 5. the step-2 gate ----------------------------------------------------
    head = "| arm | projected ERET @150k, linear | in window | logit | in window |"
    sep = "|---|---|---|---|---|"
    body = []
    for r in fit["gate"][str(SC.TARGETS[0])]["rows"]:
        body.append(f"| `{r['arm']}` | {r['linear']:.3f} | "
                    f"{'IN' if r['linear_in_window'] else 'OUT'} | {r['logit']:.3f} | "
                    f"{'IN' if r['logit_in_window'] else 'OUT'} |")
    g = fit["gate"][str(SC.TARGETS[0])]
    body.append(f"| **arms in window** | **{g['n_arms_in_window_linear']}/6** | | "
                f"**{g['n_arms_in_window_logit']}/6** | |")
    out.append(fence("The step-2 gate — ≥ 2 arms inside [0.15, 0.90] at 150,000 documents",
                     [head, sep] + body))

    # -- 6. difficulty ---------------------------------------------------------
    head = "| N | queries reached by every arm and every draw | fraction |"
    sep = "|---|---|---|"
    body = [f"| {int(k):,} | {v['queries_reached_by_every_arm_every_draw']} | "
            f"{v['fraction']:.3f} |"
            for k, v in sorted(dif["by_size"].items(), key=lambda kv: int(kv[0]))]
    body.append(f"| **every size** | **{dif['always_reached']['n']}** | "
                f"**{dif['always_reached']['fraction']:.3f}** |")
    out.append(fence("Per-query difficulty — the truly easy queries", [head, sep] + body))

    # -- 7. full-corpus reproduction ------------------------------------------
    head = ("| arm | ERET here (N = 32,663) | ERET Stage 0b′ | Δ | EPACK here | "
            "EPACK Stage 0b′ | Δ |")
    sep = "|---|---|---|---|---|---|---|"
    body = []
    for c in sorted(fit["full_corpus_reproduction_vs_stage0b_prime"],
                    key=lambda x: ARM_ORDER.index(x["arm"])
                    if x["arm"] in ARM_ORDER else 99):
        body.append(f"| `{c['arm']}` | {c['here']:.3f} | {c['stage0b_prime']:.3f} | "
                    f"{c['delta']:+.3f} | {c['EPACK_here']:.3f} | "
                    f"{c['EPACK_stage0b_prime']:.3f} | {c['EPACK_delta']:+.3f} |")
    out.append(fence("Full-corpus check — this harness against Stage 0b′'s published table",
                     [head, sep] + body))

    (SC.ART / "TABLES.md").write_text("\n".join(out))
    print((SC.ART / "TABLES.md").read_text())


if __name__ == "__main__":
    main()
