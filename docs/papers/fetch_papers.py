#!/usr/bin/env python3
"""Fetch the bibliography's papers and build reflowable EPUBs for e-reader reading.

WHY THIS EXISTS
---------------
Two-column academic PDFs are miserable on a 6" e-ink screen: the layout is fixed, the
text does not reflow, and the reader ends up panning. An EPUB reflows. This builds one
per paper so the bibliography can actually be read away from a desk.

HOW IT PICKS A SOURCE, best first
---------------------------------
1. **arXiv HTML** (`arxiv.org/html/<id>`) — LaTeXML-rendered, structured, keeps headings,
   tables and math. Available for most arXiv papers since late 2023. Converts cleanly.
2. **PDF text** via PyMuPDF with block sorting, wrapped as Markdown. Loses figures and
   mangles some tables, but reflows and is readable. This is the ACL Anthology path,
   since the Anthology serves no HTML.

The original PDF is always kept beside the EPUB: it is the citable artifact, and it is the
fallback when a conversion reads badly.

SCOPE AND RIGHTS
----------------
Only open-access sources (arXiv, ACL Anthology). These are the user's own reading copies
of openly licensed papers, format-shifted for personal use. Nothing here republishes
anything. Paywalled venues are skipped and reported, not worked around.

USAGE
-----
    /rag/envs/ragstack/bin/python3 docs/papers/fetch_papers.py --list
    /rag/envs/ragstack/bin/python3 docs/papers/fetch_papers.py --out /tmp/papers
    /rag/envs/ragstack/bin/python3 docs/papers/fetch_papers.py --out /tmp/papers --only qu-2025

Needs `pandoc` on PATH and `pymupdf` in the interpreter. Writes only under --out.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Populated from docs/papers/bibliography.md. Keep the key identical to the bibliography's
# short-key so a reader can move between the two without a lookup table.
PAPERS = [
    {"key": "qu-2025",
         "title": "Is Semantic Chunking Worth the Computational Cost?",
         "authors": "Renyi Qu; Ruixuan Tu; Forrest Sheng Bao",
         "venue": "Findings of the ACL: NAACL 2025",
         "pdf": "https://aclanthology.org/2025.findings-naacl.114.pdf"},
    {"key": "wang-2025-pic",
         "title": "Document Segmentation Matters for Retrieval-Augmented Generation",
         "authors": "Zhitong Wang; Cheng Gao; Chaojun Xiao; Yufei Huang; Shuzheng Si; "
                 "Kangyang Luo; Yuzhuo Bai; Wenhao Li; Tangjian Duan; Chuancheng Lv; "
                 "Guoshan Lu; Gang Chen; Fanchao Qi; Maosong Sun",
         "venue": "Findings of the ACL 2025",
         "pdf": "https://aclanthology.org/2025.findings-acl.422.pdf"},
    {"key": "zhao-2025-moc",
         "title": "MoC: Mixtures of Text Chunking Learners for Retrieval-Augmented Generation "
                  "System",
         "authors": "Jihao Zhao; Zhiyuan Ji; Zhaoxin Fan; Hanyu Wang; Simin Niu; Bo Tang; "
                 "Feiyu Xiong; Zhiyu Li",
         "venue": "ACL 2025 (Long Papers)",
         "pdf": "https://aclanthology.org/2025.acl-long.258.pdf"},
    {"key": "kreileder-2026",
         "title": "Evaluating Chunking Strategies for Retrieval-Augmented Generation on "
                  "Academic Texts",
         "authors": "Valentin J. J. Kreileder; Johannes Reisinger; Andreas Fischer",
         "venue": "arXiv 2607.01852",
         "arxiv": "2607.01852"},
    {"key": "allamraju-2025",
         "title": "Breaking It Down: Domain-Aware Semantic Segmentation for Retrieval "
                  "Augmented Generation",
         "authors": "Aparajitha Allamraju; Maitreya Prafulla Chitale; Hiranmai Sri Adibhatla; "
                 "Rahul Mishra; Manish Shrivastava",
         "venue": "arXiv 2512.00367",
         "arxiv": "2512.00367"},
]


def get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pdf_to_markdown(pdf_path: pathlib.Path, title: str) -> str:
    """Extract reading-order text. `sort=True` orders blocks top-to-bottom, left-to-right,
    which is what recovers column order on a two-column paper."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    out = []
    for page in doc:
        text = page.get_text("text", sort=True)
        # Join lines the PDF broke mid-sentence; keep paragraph breaks.
        text = re.sub(r"-\n(\w)", r"\1", text)            # de-hyphenate across lines
        text = re.sub(r"(?<![.\n:;])\n(?=[a-z(])", " ", text)  # unwrap continuations
        out.append(text)
    body = "\n\n".join(out)
    # A bare page number on its own line is noise on an e-reader.
    body = re.sub(r"\n\s*\d{1,3}\s*\n", "\n\n", body)
    return body


def build_epub(meta: dict, source: pathlib.Path, kind: str, out: pathlib.Path) -> bool:
    cmd = ["pandoc", str(source), "-o", str(out),
           "--metadata", f"title={meta['title']}",
           "--metadata", f"author={meta['authors']}",
           "--metadata", f"publisher={meta['venue']}",
           "--metadata", "lang=en",
           "--toc", "--toc-depth=2"]
    if kind == "html":
        cmd += ["-f", "html"]
    else:
        cmd += ["-f", "markdown-smart"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"      pandoc failed: {r.stderr.strip()[:200]}")
        return False
    return out.exists() and out.stat().st_size > 2000


def process(meta: dict, outdir: pathlib.Path) -> dict:
    key = meta["key"]
    print(f"  [{key}]")
    res = {"key": key, "title": meta["title"], "pdf": None, "epub": None, "source": None}
    pdf_path = outdir / f"{key}.pdf"

    # --- the PDF, always ---------------------------------------------------------
    pdf_url = meta.get("pdf") or f"https://arxiv.org/pdf/{meta['arxiv']}"
    try:
        data = get(pdf_url)
        if not data.startswith(b"%PDF"):
            raise ValueError("not a PDF")
        pdf_path.write_bytes(data)
        res["pdf"] = pdf_path.name
        print(f"      pdf  {len(data)//1024} KB")
    except Exception as e:
        print(f"      pdf  FAILED: {e}")

    # --- the EPUB, arXiv HTML first ---------------------------------------------
    epub_path = outdir / f"{key}.epub"
    if meta.get("arxiv"):
        try:
            raw = get(f"https://arxiv.org/html/{meta['arxiv']}").decode("utf-8", "replace")
            if len(raw) > 20000 and "<section" in raw or "<h1" in raw:
                src = outdir / f".{key}.html"
                src.write_text(raw, encoding="utf-8")
                if build_epub(meta, src, "html", epub_path):
                    res.update(epub=epub_path.name, source="arxiv-html")
                    print(f"      epub {epub_path.stat().st_size//1024} KB  (arXiv HTML)")
                src.unlink(missing_ok=True)
        except Exception as e:
            print(f"      arXiv HTML unavailable ({e}); falling back to PDF text")

    if not res["epub"] and pdf_path.exists():
        try:
            md = pdf_to_markdown(pdf_path, meta["title"])
            src = outdir / f".{key}.md"
            src.write_text(f"# {meta['title']}\n\n*{meta['authors']}*\n\n"
                           f"*{meta['venue']}*\n\n---\n\n{md}", encoding="utf-8")
            if build_epub(meta, src, "md", epub_path):
                res.update(epub=epub_path.name, source="pdf-text")
                print(f"      epub {epub_path.stat().st_size//1024} KB  (PDF text)")
            src.unlink(missing_ok=True)
        except Exception as e:
            print(f"      epub FAILED: {e}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/papers")
    ap.add_argument("--only", action="append", help="one or more short-keys")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for p in PAPERS:
            print(f"{p['key']:<20} {p['title'][:76]}")
        return 0
    outdir = pathlib.Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in PAPERS if not a.only or p["key"] in a.only]
    print(f"{len(todo)} paper(s) -> {outdir}")
    results = [process(p, outdir) for p in todo]
    (outdir / "manifest.json").write_text(json.dumps(results, indent=1))
    ok = sum(1 for r in results if r["epub"])
    print(f"\n{ok}/{len(results)} EPUBs built; manifest at {outdir/'manifest.json'}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
