#!/usr/bin/env python3
"""Render docs/ARCHITECTURE-DEEP-DIVE.md into a self-contained, GitHub-Pages-ready
HTML page (docs/architecture-deep-dive.html).

- Mermaid fenced blocks are extracted and re-emitted as <pre class="mermaid"> so
  mermaid.js renders them client-side (the only external dependency; pinned CDN).
- Heading ids use GitHub's slugify so the in-doc anchors keep working.
- A sticky two-level sidebar TOC + scroll-spy is generated from the H2/H3 tree.

Regenerate:  python docs/build_arch_html.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
SRC = HERE / "ARCHITECTURE-DEEP-DIVE.md"
OUT = HERE / "architecture-deep-dive.html"
REPO = "https://github.com/wilke/ragstack"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"


def gh_slug(text: str) -> str:
    """GitHub's heading slugify: lowercase, drop punctuation (keep word/space/-),
    then every whitespace char becomes a hyphen (not collapsed — so '— ' yields a
    double hyphen, matching GitHub)."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s", "-", s)
    return s


def main() -> None:
    raw = SRC.read_text(encoding="utf-8")

    # Title = the single H1; keep the rest as the body.
    m = re.match(r"#\s+(.+)\n", raw)
    title = m.group(1).strip() if m else "RAGStack Architecture"
    body_md = raw[m.end():] if m else raw

    # Drop the in-body "## Contents" list — the sidebar replaces it (up to its ---).
    body_md = re.sub(r"\n## Contents\n.*?\n---\n", "\n", body_md, count=1, flags=re.DOTALL)

    # Pull mermaid blocks out before markdown sees them; leave a lone placeholder.
    diagrams: list[str] = []

    def _stash(mo: re.Match) -> str:
        diagrams.append(mo.group(1))
        return f"\n\nMERMAIDPLACEHOLDER{len(diagrams) - 1}\n\n"

    body_md = re.sub(r"```mermaid\n(.*?)\n```", _stash, body_md, flags=re.DOTALL)

    md = markdown.Markdown(
        extensions=["extra", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": lambda v, s: gh_slug(v), "toc_depth": "2-3"}},
    )
    body_html = md.convert(body_md)

    # Re-insert diagrams as mermaid nodes (markdown wrapped each placeholder in <p>).
    for i, code in enumerate(diagrams):
        node = f'<div class="diagram"><pre class="mermaid">{html.escape(code)}</pre></div>'
        body_html = body_html.replace(f"<p>MERMAIDPLACEHOLDER{i}</p>", node)

    # Wrap tables so wide ones scroll horizontally instead of cramming.
    body_html = re.sub(r"<table>", '<div class="tablewrap"><table>', body_html)
    body_html = body_html.replace("</table>", "</table></div>")

    # Sidebar from the H2/H3 tree (skip any leftover H1).
    nav = _build_nav(md.toc_tokens)

    OUT.write_text(_PAGE.format(
        title=html.escape(title), body=body_html, nav=nav, repo=REPO, mermaid_cdn=MERMAID_CDN,
    ), encoding="utf-8")
    print(f"wrote {OUT} ({len(diagrams)} diagrams, {OUT.stat().st_size // 1024} KB)")


def _build_nav(tokens: list[dict]) -> str:
    # toc_tokens "name" is already HTML-escaped by markdown — don't escape again.
    out = ['<ul class="toc">']
    for t in tokens:
        if t["level"] != 2:
            continue
        kids = [c for c in t.get("children", []) if c["level"] == 3]
        out.append(f'<li><a href="#{t["id"]}">{t["name"]}</a>')
        if kids:
            out.append('<ul class="toc-sub">')
            for c in kids:
                out.append(f'<li><a href="#{c["id"]}">{c["name"]}</a></li>')
            out.append("</ul>")
        out.append("</li>")
    out.append("</ul>")
    return "\n".join(out)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="RAGStack architecture deep-dive: algorithms, scalability, and duplication — grounded in file:line references.">
<style>
  :root {{
    --paper:#fbfcfe; --panel:#fff; --ink:#141a22; --muted:#586374; --faint:#8a93a3;
    --line:#e4e9f0; --accent:#1668b0; --accent-2:#0f4f88; --accent-soft:#e9f1fb;
    --code-bg:#eef2f8; --code-ink:#274156; --sidebar:#f6f8fb;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace;
    --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Inter, system-ui, sans-serif;
  }}
  * {{ box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; scroll-padding-top:1.5rem; }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
    font-size:16px; line-height:1.65; -webkit-font-smoothing:antialiased; }}

  /* Header / hero with a faint blueprint grid */
  header.hero {{ border-bottom:1px solid var(--line);
    background:
      linear-gradient(180deg, rgba(22,104,176,.05), rgba(22,104,176,0) 70%),
      repeating-linear-gradient(0deg, transparent, transparent 31px, rgba(22,104,176,.06) 32px),
      repeating-linear-gradient(90deg, transparent, transparent 31px, rgba(22,104,176,.06) 32px),
      var(--paper);
  }}
  .hero-inner {{ max-width:1200px; margin:0 auto; padding:3rem 2rem 2.2rem; }}
  .kicker {{ font-family:var(--mono); font-size:.72rem; letter-spacing:.18em; text-transform:uppercase;
    color:var(--accent-2); margin:0 0 .7rem; }}
  h1.title {{ font-size:clamp(1.7rem,3.4vw,2.6rem); line-height:1.12; margin:0 0 .6rem;
    letter-spacing:-.02em; text-wrap:balance; max-width:22ch; }}
  .tagline {{ color:var(--muted); font-size:1.02rem; max-width:60ch; margin:0 0 1.1rem; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:.5rem; }}
  .chip {{ font-size:.74rem; font-family:var(--mono); padding:.28rem .6rem; border-radius:999px;
    border:1px solid var(--line); background:var(--panel); color:var(--muted); }}
  .chip a {{ color:var(--accent); text-decoration:none; }}

  /* Layout: sticky sidebar + reading column */
  .wrap {{ max-width:1200px; margin:0 auto; padding:0 2rem;
    display:grid; grid-template-columns:280px minmax(0,1fr); gap:3rem; align-items:start; }}
  nav.side {{ position:sticky; top:0; max-height:100vh; overflow-y:auto; padding:1.6rem 0 3rem;
    font-size:.86rem; }}
  nav.side .side-title {{ font-family:var(--mono); font-size:.7rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--faint); padding:0 0 .6rem; }}
  ul.toc, ul.toc-sub {{ list-style:none; margin:0; padding:0; }}
  ul.toc > li {{ margin:.1rem 0; }}
  ul.toc > li > a {{ display:block; padding:.28rem .6rem; border-radius:6px; color:var(--ink);
    text-decoration:none; font-weight:550; border-left:2px solid transparent; }}
  ul.toc-sub {{ margin:.1rem 0 .5rem; }}
  ul.toc-sub > li > a {{ display:block; padding:.2rem .6rem .2rem 1.1rem; color:var(--muted);
    text-decoration:none; font-size:.82rem; border-left:2px solid transparent; line-height:1.4; }}
  nav.side a:hover {{ color:var(--accent); background:var(--accent-soft); }}
  nav.side a.active {{ color:var(--accent-2); border-left-color:var(--accent);
    background:var(--accent-soft); }}

  main {{ padding:1.6rem 0 5rem; min-width:0; }}
  .content {{ max-width:60rem; }}

  /* Typography */
  h2 {{ font-size:1.5rem; letter-spacing:-.01em; margin:2.6rem 0 1rem; padding-top:.4rem;
    line-height:1.2; scroll-margin-top:1rem; }}
  h2::before {{ content:""; display:block; width:44px; height:3px; background:var(--accent);
    border-radius:2px; margin-bottom:.9rem; }}
  h3 {{ font-size:1.16rem; margin:2rem 0 .6rem; letter-spacing:-.01em; color:var(--ink);
    scroll-margin-top:1rem; }}
  h4 {{ font-size:1rem; margin:1.4rem 0 .5rem; color:var(--accent-2); }}
  p {{ margin:.7rem 0; }}
  a {{ color:var(--accent); text-underline-offset:2px; }}
  strong {{ font-weight:650; color:#0d1620; }}
  hr {{ border:0; border-top:1px solid var(--line); margin:2.4rem 0; }}
  .headanchor {{ color:var(--faint); text-decoration:none; font-weight:400; margin-left:.4rem;
    opacity:0; font-size:.8em; }}
  h2:hover .headanchor, h3:hover .headanchor {{ opacity:1; }}

  /* Inline code + the pervasive file:line refs */
  code {{ font-family:var(--mono); font-size:.85em; background:var(--code-bg); color:var(--code-ink);
    padding:.12em .4em; border-radius:4px; overflow-wrap:break-word; }}
  a code {{ color:var(--accent); background:var(--accent-soft); }}

  /* Lists */
  ul, ol {{ padding-left:1.3rem; margin:.6rem 0; }}
  li {{ margin:.28rem 0; }}
  li::marker {{ color:var(--faint); }}

  /* Blockquote / callouts */
  blockquote {{ margin:1.1rem 0; padding:.7rem 1.1rem; border-left:3px solid var(--accent);
    background:var(--accent-soft); border-radius:0 8px 8px 0; color:#233; }}
  blockquote p {{ margin:.3rem 0; }}

  /* Tables — wrapped in .tablewrap so wide ones scroll instead of cramming. */
  .tablewrap {{ overflow-x:auto; margin:1.2rem 0; border-radius:8px;
    -webkit-overflow-scrolling:touch; }}
  .tablewrap table {{ border-collapse:collapse; min-width:100%; font-size:.9rem; }}
  th, td {{ border:1px solid var(--line); padding:.5rem .7rem; text-align:left; vertical-align:top; }}
  /* File:line refs stay on one line (the table scrolls); prose in cells still wraps. */
  td code, th code {{ white-space:nowrap; }}
  thead th {{ background:var(--sidebar); font-family:var(--mono); font-size:.78rem;
    letter-spacing:.02em; text-transform:uppercase; color:var(--muted); white-space:nowrap; }}
  tbody tr:nth-child(even) {{ background:#fafbfd; }}

  /* Mermaid diagram cards */
  .diagram {{ margin:1.3rem 0; padding:1.1rem; border:1px solid var(--line); border-radius:10px;
    background:var(--panel); overflow-x:auto; box-shadow:0 1px 2px rgba(20,30,50,.03); }}
  .diagram pre.mermaid {{ margin:0; text-align:center; }}
  .diagram pre.mermaid:not([data-processed]) {{ color:var(--faint); font-family:var(--mono);
    font-size:.8rem; }}

  footer {{ border-top:1px solid var(--line); margin-top:3rem; }}
  .foot-inner {{ max-width:1200px; margin:0 auto; padding:1.6rem 2rem 3rem; color:var(--muted);
    font-size:.84rem; }}
  .foot-inner code {{ font-size:.8rem; }}

  @media (max-width:920px) {{
    .wrap {{ grid-template-columns:1fr; gap:0; }}
    nav.side {{ position:static; max-height:none; border-bottom:1px solid var(--line);
      padding:1rem 0; }}
    nav.side details > summary {{ cursor:pointer; font-family:var(--mono); font-size:.75rem;
      letter-spacing:.12em; text-transform:uppercase; color:var(--accent-2); }}
    main {{ padding-top:1rem; }}
  }}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <p class="kicker">RAGStack · Architecture</p>
    <h1 class="title">{title}</h1>
    <p class="tagline">A per-capability deep dive — for each capability: the algorithm, the tools &amp; models, inputs&nbsp;&rarr;&nbsp;outputs, whether it scales &amp; parallelizes, single vs bulk, and a diagram. Closes with a cross-cutting duplication audit.</p>
    <div class="chips">
      <span class="chip">Python implementation</span>
      <span class="chip">grounded in <code>file:line</code></span>
      <span class="chip">33 diagrams</span>
      <span class="chip"><a href="{repo}">github.com/wilke/ragstack &rarr;</a></span>
    </div>
  </div>
</header>

<div class="wrap">
  <nav class="side" id="side">
    <div class="side-title">Contents</div>
    {nav}
  </nav>
  <main>
    <article class="content" id="content">
      {body}
    </article>
  </main>
</div>

<footer>
  <div class="foot-inner">
    Generated from <code>docs/ARCHITECTURE-DEEP-DIVE.md</code> via <code>docs/build_arch_html.py</code> ·
    <a href="{repo}">{repo}</a>
  </div>
</footer>

<script type="module">
  import mermaid from "{mermaid_cdn}";
  mermaid.initialize({{
    startOnLoad: true,
    securityLevel: "strict",
    theme: "base",
    themeVariables: {{
      fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, sans-serif",
      fontSize: "14px",
      primaryColor: "#e9f1fb", primaryBorderColor: "#1668b0", primaryTextColor: "#141a22",
      lineColor: "#7d8aa0", secondaryColor: "#f2f5fa", tertiaryColor: "#fafbfd",
    }},
    flowchart: {{ curve: "basis", htmlLabels: true }},
  }});
</script>
<script>
  // Heading anchor links + scroll-spy over the sidebar.
  (function () {{
    var content = document.getElementById("content");
    content.querySelectorAll("h2[id], h3[id]").forEach(function (h) {{
      var a = document.createElement("a");
      a.href = "#" + h.id; a.className = "headanchor"; a.textContent = "#";
      a.setAttribute("aria-label", "Link to this section");
      h.appendChild(a);
    }});
    var links = {{}};
    document.querySelectorAll("nav.side a[href^='#']").forEach(function (a) {{
      links[a.getAttribute("href").slice(1)] = a;
    }});
    var current = null;
    var obs = new IntersectionObserver(function (entries) {{
      entries.forEach(function (e) {{
        if (!e.isIntersecting) return;
        var a = links[e.target.id];
        if (!a || a === current) return;
        if (current) current.classList.remove("active");
        a.classList.add("active"); current = a;
        a.scrollIntoView({{ block: "nearest" }});
      }});
    }}, {{ rootMargin: "0px 0px -80% 0px", threshold: 0 }});
    content.querySelectorAll("h2[id], h3[id]").forEach(function (h) {{ obs.observe(h); }});
  }})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
