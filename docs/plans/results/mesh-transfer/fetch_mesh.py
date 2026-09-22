#!/usr/bin/env python
"""Fetch MeSH headings for a list of PMIDs via NCBI efetch. Polite: batched,
<=3 req/s, descriptive UA, hard stop on 429/5xx."""
import json, sys, time, urllib.request, urllib.parse, os
import xml.etree.ElementTree as ET

UA = "RAGStack-topic-label-transfer-experiment/0.1 (awilke1972@gmail.com; one-off research measurement)"
URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
BATCH = 100
SLEEP = 0.4  # ~2.5 req/s, under the 3/s no-key limit

src, out_path = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(src)]
done = set()
if os.path.exists(out_path):
    for l in open(out_path):
        try: done.add(json.loads(l)["pmid"])
        except Exception: pass
todo = [r for r in rows if r["pmid"] not in done]
print(f"{len(rows)} total, {len(done)} already fetched, {len(todo)} to go", flush=True)

byid = {r["pmid"]: r for r in rows}
out = open(out_path, "a")
n_req = 0
for i in range(0, len(todo), BATCH):
    chunk = [r["pmid"] for r in todo[i:i + BATCH]]
    data = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(chunk),
                                   "retmode": "xml"}).encode()
    req = urllib.request.Request(URL, data=data, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        print(f"STOP: HTTP {e.code} at batch {i//BATCH}; reporting and exiting", flush=True)
        break
    except Exception as e:
        print(f"STOP: {type(e).__name__}: {e} at batch {i//BATCH}", flush=True)
        break
    n_req += 1
    root = ET.fromstring(body)
    got = set()
    for art in root.iter("PubmedArticle"):
        pmid_el = art.find("./MedlineCitation/PMID")
        if pmid_el is None: continue
        pmid = pmid_el.text.strip()
        got.add(pmid)
        mh = []
        for h in art.iter("MeshHeading"):
            d = h.find("DescriptorName")
            if d is not None and d.text:
                mh.append({"d": d.text, "ui": d.get("UI"),
                           "major": d.get("MajorTopicYN") == "Y"})
        abst = " ".join(t.text or "" for t in art.iter("AbstractText")).strip()
        ttl_el = art.find(".//ArticleTitle")
        ttl = "".join(ttl_el.itertext()).strip() if ttl_el is not None else ""
        st = art.find("./MedlineCitation/MedlineJournalInfo/NlmUniqueID")
        rec = byid.get(pmid, {})
        out.write(json.dumps({"pmid": pmid, "pmcid": rec.get("pmcid"),
                              "journal": rec.get("journal"), "grp": rec.get("grp"),
                              "year": rec.get("year"), "mesh": mh,
                              "pm_title": ttl, "pm_abstract": abst,
                              "nlmid": st.text if st is not None else None}) + "\n")
    for pmid in chunk:
        if pmid not in got:
            out.write(json.dumps({"pmid": pmid, "pmcid": byid[pmid].get("pmcid"),
                                  "journal": byid[pmid].get("journal"),
                                  "grp": byid[pmid].get("grp"), "missing": True,
                                  "mesh": []}) + "\n")
    out.flush()
    if (i // BATCH) % 20 == 0:
        print(f"batch {i//BATCH}/{len(todo)//BATCH} ok", flush=True)
    time.sleep(SLEEP)
out.close()
print(f"done, {n_req} requests issued", flush=True)
