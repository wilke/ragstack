#!/usr/bin/env python
"""Read title + abstract from the corpus JATS (clean/) for each fetched record.
Shard path is sha1(pmcid)[0:2]/[2:4]. Read-only on the corpus."""
import json, sys, hashlib, os
import xml.etree.ElementTree as ET

CLEAN = "/rag/oa/corpus/clean"

def path_for(pmcid):
    h = hashlib.sha1(pmcid.encode()).hexdigest()
    return os.path.join(CLEAN, h[:2], h[2:4], pmcid + ".xml")

def text_of(el):
    return " ".join(" ".join(el.itertext()).split())

src, out_path = sys.argv[1], sys.argv[2]
n = ok = no_file = no_abs = 0
with open(out_path, "w") as out:
    for line in open(src):
        r = json.loads(line)
        n += 1
        pmcid = r.get("pmcid")
        title = abstract = ""
        p = path_for(pmcid) if pmcid else None
        if p and os.path.exists(p):
            try:
                root = ET.parse(p).getroot()
                front = root.find("front")
                if front is not None:
                    t = front.find(".//title-group/article-title")
                    if t is not None:
                        title = text_of(t)
                    parts = []
                    for a in front.iter("abstract"):
                        if a.get("abstract-type") in ("graphical", "teaser"):
                            continue
                        parts.append(text_of(a))
                    abstract = " ".join(parts)
            except Exception:
                pass
        else:
            no_file += 1
        if not title:
            title = r.get("pm_title") or ""
        if not abstract:
            abstract = r.get("pm_abstract") or ""
            if not abstract:
                no_abs += 1
        if title or abstract:
            ok += 1
        r["title"] = title
        r["abstract"] = abstract
        r.pop("pm_title", None)
        r.pop("pm_abstract", None)
        out.write(json.dumps(r) + "\n")
        if n % 2000 == 0:
            print(n, flush=True)
print(f"n={n} with_text={ok} missing_xml={no_file} no_abstract={no_abs}")
