#!/usr/bin/env python3
"""Render selected repo docs into a small self-contained GitHub-Pages site:
an index landing page + one styled HTML page per doc.

- Mermaid fenced blocks render client-side (pinned mermaid@11 CDN — the only
  external dep, and only loaded on pages that have diagrams).
- Heading ids use GitHub's slugify so in-doc anchors keep working.
- Each page gets a sticky two-level sidebar TOC + scroll-spy.

Regenerate:  pip install markdown && python docs/build_docs.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
REPO = "https://github.com/wilke/ragstack"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"

# The docs to publish, in nav order. `card`/`blurb` drive the index landing page.
PAGES = [
    dict(
        src="ARCHITECTURE.md", out="architecture.html", label="Overview",
        card="Architecture Overview",
        blurb="A high-level map — capabilities, components, data-flow diagrams, and the full API + service-script surface.",
    ),
    dict(
        src="ARCHITECTURE-DEEP-DIVE.md", out="architecture-deep-dive.html", label="Deep-Dive",
        card="Architecture Deep-Dive",
        blurb="Per-capability internals: algorithms, tools & models, scalability, single vs bulk, 33 diagrams, and a duplication audit.",
    ),
    dict(
        src="cookbook-new-org-ingest.md", out="cookbook-new-org-ingest.html", label="Cookbook",
        card="Cookbook — New-Org Ingest",
        blurb="Stand up an API server for a new organization and bulk-ingest ~40k documents via GoWe, step by step.",
    ),
    dict(
        src="LOCAL-DEMO.md", out="local-demo.html", label="Runbook",
        card="Local Demo Runbook",
        blurb="Spin up the UI + API locally against SciFact data — the fastest way to see RAGStack running.",
    ),
]


def gh_slug(text: str) -> str:
    """GitHub's heading slugify: lowercase, drop punctuation (keep word/space/-),
    then every whitespace char becomes a hyphen (not collapsed)."""
    s = re.sub(r"[^\w\s-]", "", text.strip().lower(), flags=re.UNICODE)
    return re.sub(r"\s", "-", s)


def render_page(page: dict) -> None:
    raw = (HERE / page["src"]).read_text(encoding="utf-8")

    m = re.match(r"#\s+(.+)\n", raw)
    title = m.group(1).strip() if m else page["card"]
    body_md = raw[m.end():] if m else raw
    body_md = re.sub(r"\n## Contents\n.*?\n---\n", "\n", body_md, count=1, flags=re.DOTALL)

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

    for i, code in enumerate(diagrams):
        node = f'<div class="diagram"><pre class="mermaid">{html.escape(code)}</pre></div>'
        body_html = body_html.replace(f"<p>MERMAIDPLACEHOLDER{i}</p>", node)

    body_html = body_html.replace("<table>", '<div class="tablewrap"><table>')
    body_html = body_html.replace("</table>", "</table></div>")

    chips = [f'<span class="chip">{page["label"]}</span>']
    if diagrams:
        chips.append(f'<span class="chip">{len(diagrams)} diagrams</span>')
    chips.append(f'<span class="chip"><a href="{REPO}">github.com/wilke/ragstack &rarr;</a></span>')

    mermaid_script = _MERMAID_SCRIPT.replace("__MERMAID_CDN__", MERMAID_CDN) if diagrams else ""

    out = (
        _PAGE.replace("__TITLE__", html.escape(title))
        .replace("__TAGLINE__", html.escape(page["blurb"]))
        .replace("__CHIPS__", "\n      ".join(chips))
        .replace("__NAV__", _build_nav(md.toc_tokens))
        .replace("__BODY__", body_html)
        .replace("__MERMAID__", mermaid_script)
        .replace("__REPO__", REPO)
    )
    (HERE / page["out"]).write_text(out, encoding="utf-8")
    print(f"  {page['out']:32} {len(diagrams)} diagrams, {(HERE / page['out']).stat().st_size // 1024} KB")


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


def build_index() -> None:
    cards = []
    for p in PAGES:
        cards.append(
            f'<a class="doc-card" href="{p["out"]}">'
            f'<span class="card-tag">{p["label"]}</span>'
            f'<h2>{html.escape(p["card"])}</h2>'
            f'<p>{html.escape(p["blurb"])}</p>'
            f'<span class="card-go">Read &rarr;</span></a>'
        )
    out = _INDEX.replace("__CARDS__", "\n      ".join(cards)).replace("__REPO__", REPO)
    (HERE / "index.html").write_text(out, encoding="utf-8")
    print(f"  {'index.html':32} landing ({len(PAGES)} docs)")


def main() -> None:
    print("building docs site:")
    for p in PAGES:
        render_page(p)
    build_index()


# --------------------------------------------------------------------------- #
# Shared styling (used by both templates).
# --------------------------------------------------------------------------- #
_CSS = r"""
  :root {
    --paper:#fbfcfe; --panel:#fff; --ink:#141a22; --muted:#586374; --faint:#8a93a3;
    --line:#e4e9f0; --accent:#1668b0; --accent-2:#0f4f88; --accent-soft:#e9f1fb;
    --code-bg:#eef2f8; --code-ink:#274156; --sidebar:#f6f8fb; --panel-dark:#0f1622;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace;
    --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Inter, system-ui, sans-serif;
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; scroll-padding-top:1.5rem; }
  body { margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
    font-size:16px; line-height:1.65; -webkit-font-smoothing:antialiased; }
  a { color:var(--accent); text-underline-offset:2px; }

  header.hero { border-bottom:1px solid var(--line);
    background:
      linear-gradient(180deg, rgba(22,104,176,.05), rgba(22,104,176,0) 70%),
      repeating-linear-gradient(0deg, transparent, transparent 31px, rgba(22,104,176,.06) 32px),
      repeating-linear-gradient(90deg, transparent, transparent 31px, rgba(22,104,176,.06) 32px),
      var(--paper);
  }
  .hero-inner { max-width:1200px; margin:0 auto; padding:2.4rem 2rem 2rem; }
  .home { display:inline-block; font-family:var(--mono); font-size:.75rem; letter-spacing:.06em;
    text-decoration:none; color:var(--accent-2); margin-bottom:1rem; }
  .home:hover { text-decoration:underline; }
  .kicker { font-family:var(--mono); font-size:.72rem; letter-spacing:.18em; text-transform:uppercase;
    color:var(--accent-2); margin:0 0 .7rem; }
  h1.title { font-size:clamp(1.7rem,3.4vw,2.6rem); line-height:1.12; margin:0 0 .6rem;
    letter-spacing:-.02em; text-wrap:balance; max-width:24ch; }
  .tagline { color:var(--muted); font-size:1.02rem; max-width:60ch; margin:0 0 1.1rem; }
  .chips { display:flex; flex-wrap:wrap; gap:.5rem; }
  .chip { font-size:.74rem; font-family:var(--mono); padding:.28rem .6rem; border-radius:999px;
    border:1px solid var(--line); background:var(--panel); color:var(--muted); }
  .chip a { color:var(--accent); text-decoration:none; }

  footer { border-top:1px solid var(--line); margin-top:3rem; }
  .foot-inner { max-width:1200px; margin:0 auto; padding:1.6rem 2rem 3rem; color:var(--muted);
    font-size:.84rem; }
  code { font-family:var(--mono); background:var(--code-bg); color:var(--code-ink);
    padding:.12em .4em; border-radius:4px; overflow-wrap:break-word; }
"""

_PAGE_CSS = r"""
  .wrap { max-width:1200px; margin:0 auto; padding:0 2rem;
    display:grid; grid-template-columns:280px minmax(0,1fr); gap:3rem; align-items:start; }
  nav.side { position:sticky; top:0; max-height:100vh; overflow-y:auto; padding:1.6rem 0 3rem; font-size:.86rem; }
  nav.side .side-title { font-family:var(--mono); font-size:.7rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--faint); padding:0 0 .6rem; }
  ul.toc, ul.toc-sub { list-style:none; margin:0; padding:0; }
  ul.toc > li { margin:.1rem 0; }
  ul.toc > li > a { display:block; padding:.28rem .6rem; border-radius:6px; color:var(--ink);
    text-decoration:none; font-weight:550; border-left:2px solid transparent; }
  ul.toc-sub { margin:.1rem 0 .5rem; }
  ul.toc-sub > li > a { display:block; padding:.2rem .6rem .2rem 1.1rem; color:var(--muted);
    text-decoration:none; font-size:.82rem; border-left:2px solid transparent; line-height:1.4; }
  nav.side a:hover { color:var(--accent); background:var(--accent-soft); }
  nav.side a.active { color:var(--accent-2); border-left-color:var(--accent); background:var(--accent-soft); }
  main { padding:1.6rem 0 5rem; min-width:0; }
  .content { max-width:60rem; }
  .content code { font-size:.85em; }
  h2 { font-size:1.5rem; letter-spacing:-.01em; margin:2.6rem 0 1rem; padding-top:.4rem;
    line-height:1.2; scroll-margin-top:1rem; }
  h2::before { content:""; display:block; width:44px; height:3px; background:var(--accent);
    border-radius:2px; margin-bottom:.9rem; }
  h3 { font-size:1.16rem; margin:2rem 0 .6rem; letter-spacing:-.01em; scroll-margin-top:1rem; }
  h4 { font-size:1rem; margin:1.4rem 0 .5rem; color:var(--accent-2); }
  p { margin:.7rem 0; }
  strong { font-weight:650; color:#0d1620; }
  hr { border:0; border-top:1px solid var(--line); margin:2.4rem 0; }
  .headanchor { color:var(--faint); text-decoration:none; font-weight:400; margin-left:.4rem;
    opacity:0; font-size:.8em; }
  h2:hover .headanchor, h3:hover .headanchor { opacity:1; }
  a code { color:var(--accent); background:var(--accent-soft); }
  ul, ol { padding-left:1.3rem; margin:.6rem 0; }
  li { margin:.28rem 0; }
  li::marker { color:var(--faint); }
  blockquote { margin:1.1rem 0; padding:.7rem 1.1rem; border-left:3px solid var(--accent);
    background:var(--accent-soft); border-radius:0 8px 8px 0; color:#233; }
  blockquote p { margin:.3rem 0; }
  /* Fenced code blocks (bash etc.) — dark panel against the light page. */
  pre { background:var(--panel-dark); color:#dce3ee; padding:1rem 1.1rem; border-radius:8px;
    overflow-x:auto; font-size:.82rem; line-height:1.55; margin:1.1rem 0; border:1px solid #1c2740; }
  pre code { background:none; color:inherit; padding:0; font-size:inherit; white-space:pre; }
  .tablewrap { overflow-x:auto; margin:1.2rem 0; border-radius:8px; -webkit-overflow-scrolling:touch; }
  .tablewrap table { border-collapse:collapse; min-width:100%; font-size:.9rem; }
  th, td { border:1px solid var(--line); padding:.5rem .7rem; text-align:left; vertical-align:top; }
  td code, th code { white-space:nowrap; }
  thead th { background:var(--sidebar); font-family:var(--mono); font-size:.78rem;
    letter-spacing:.02em; text-transform:uppercase; color:var(--muted); white-space:nowrap; }
  tbody tr:nth-child(even) { background:#fafbfd; }
  .diagram { margin:1.3rem 0; padding:1.1rem; border:1px solid var(--line); border-radius:10px;
    background:var(--panel); overflow-x:auto; box-shadow:0 1px 2px rgba(20,30,50,.03); }
  .diagram pre.mermaid { margin:0; text-align:center; }
  .diagram pre.mermaid:not([data-processed]) { color:var(--faint); font-family:var(--mono); font-size:.8rem; }
  .foot-inner code { font-size:.8rem; }
  @media (max-width:920px) {
    .wrap { grid-template-columns:1fr; gap:0; }
    nav.side { position:static; max-height:none; border-bottom:1px solid var(--line); padding:1rem 0; }
    main { padding-top:1rem; }
  }
"""

_MERMAID_SCRIPT = """<script type="module">
  import mermaid from "__MERMAID_CDN__";
  mermaid.initialize({
    startOnLoad: true, securityLevel: "strict", theme: "base",
    themeVariables: {
      fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, sans-serif", fontSize: "14px",
      primaryColor: "#e9f1fb", primaryBorderColor: "#1668b0", primaryTextColor: "#141a22",
      lineColor: "#7d8aa0", secondaryColor: "#f2f5fa", tertiaryColor: "#fafbfd",
    },
    flowchart: { curve: "basis", htmlLabels: true },
  });
</script>"""

_SPY_SCRIPT = """<script>
  (function () {
    var content = document.getElementById("content");
    content.querySelectorAll("h2[id], h3[id]").forEach(function (h) {
      var a = document.createElement("a");
      a.href = "#" + h.id; a.className = "headanchor"; a.textContent = "#";
      a.setAttribute("aria-label", "Link to this section"); h.appendChild(a);
    });
    var links = {};
    document.querySelectorAll("nav.side a[href^='#']").forEach(function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    var current = null;
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        var a = links[e.target.id]; if (!a || a === current) return;
        if (current) current.classList.remove("active");
        a.classList.add("active"); current = a; a.scrollIntoView({ block: "nearest" });
      });
    }, { rootMargin: "0px 0px -80% 0px", threshold: 0 });
    content.querySelectorAll("h2[id], h3[id]").forEach(function (h) { obs.observe(h); });
  })();
</script>"""

_PAGE = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>__TITLE__ · RAGStack</title>\n<style>" + _CSS + _PAGE_CSS + "</style>\n</head>\n<body>\n"
    '<header class="hero"><div class="hero-inner">\n'
    '  <a class="home" href="index.html">&larr; RAGStack docs</a>\n'
    '  <p class="kicker">RAGStack · Documentation</p>\n'
    '  <h1 class="title">__TITLE__</h1>\n'
    '  <p class="tagline">__TAGLINE__</p>\n'
    '  <div class="chips">\n      __CHIPS__\n  </div>\n'
    "</div></header>\n\n"
    '<div class="wrap">\n'
    '  <nav class="side" id="side"><div class="side-title">Contents</div>\n__NAV__\n  </nav>\n'
    '  <main><article class="content" id="content">\n__BODY__\n  </article></main>\n'
    "</div>\n\n"
    '<footer><div class="foot-inner">Generated from the repo markdown via '
    '<code>docs/build_docs.py</code> · <a href="__REPO__">__REPO__</a></div></footer>\n'
    "__MERMAID__\n" + _SPY_SCRIPT + "\n</body>\n</html>\n"
)

_INDEX_CSS = r"""
  .index-wrap { max-width:1000px; margin:0 auto; padding:2.4rem 2rem 4rem; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:1.2rem;
    margin-top:.5rem; }
  a.doc-card { display:flex; flex-direction:column; text-decoration:none; color:inherit;
    background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:1.4rem 1.5rem;
    transition:border-color .15s, box-shadow .15s, transform .15s; }
  a.doc-card:hover { border-color:var(--accent); box-shadow:0 6px 20px rgba(22,104,176,.10);
    transform:translateY(-2px); }
  .card-tag { align-self:flex-start; font-family:var(--mono); font-size:.68rem; letter-spacing:.1em;
    text-transform:uppercase; color:var(--accent-2); background:var(--accent-soft);
    padding:.2rem .55rem; border-radius:999px; margin-bottom:.9rem; }
  a.doc-card h2 { font-size:1.18rem; margin:0 0 .5rem; letter-spacing:-.01em; line-height:1.25; }
  a.doc-card p { color:var(--muted); font-size:.92rem; margin:0 0 1rem; flex:1; }
  .card-go { font-weight:600; color:var(--accent); font-size:.9rem; }
"""

_INDEX = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>RAGStack — Documentation</title>\n"
    '<meta name="description" content="RAGStack documentation: architecture overview, deep-dive, and a local-demo cookbook.">\n'
    "<style>" + _CSS + _INDEX_CSS + "</style>\n</head>\n<body>\n"
    '<header class="hero"><div class="hero-inner">\n'
    '  <p class="kicker">RAGStack</p>\n'
    '  <h1 class="title">RAGStack Documentation</h1>\n'
    '  <p class="tagline">A multi-tenant Retrieval-Augmented Generation platform — one HTTP API, '
    "two implementations (Python/FastAPI, Go/Chi), conforming to a single OpenAPI 3.1 contract.</p>\n"
    '  <div class="chips"><span class="chip"><a href="__REPO__">github.com/wilke/ragstack &rarr;</a></span></div>\n'
    "</div></header>\n\n"
    '<div class="index-wrap"><div class="cards">\n      __CARDS__\n</div></div>\n\n'
    '<footer><div class="foot-inner">Generated from the repo markdown via '
    '<code>docs/build_docs.py</code> · <a href="__REPO__">__REPO__</a></div></footer>\n'
    "</body>\n</html>\n"
)


if __name__ == "__main__":
    main()
