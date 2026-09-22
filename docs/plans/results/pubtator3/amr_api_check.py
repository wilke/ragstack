import json, random, urllib.request, time, collections, sys, re
sys.path.insert(0,'/rag/data/pubtator3')
from amr_terms3 import RX
rows=[json.loads(l) for l in open('amr_scale_rows3.jsonl')]
random.seed(7)
# docs where a family is in text but NOT in bulk gene annotations
cand=collections.defaultdict(list)
for r in rows:
    for k in r[4]:
        if k not in r[5]: cand[k].append(r[0])
focus=['mecA/C','mcr','optrA','gyrA','blaOXA','blaCTX-M','blaVEB','tet','qacE','blaADC']
sel={k: random.sample(cand[k], min(40,len(cand[k]))) for k in focus if cand[k]}
pmids=sorted({p for v in sel.values() for p in v})
print("candidate docs to verify via API:",len(pmids), file=sys.stderr)
UA="RAGStack-corpus-coverage-study/0.1 (contact: awilke1972@gmail.com)"
docs={}
for i in range(0,len(pmids),100):
    url="https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson?pmids="+",".join(pmids[i:i+100])+"&full=true"
    req=urllib.request.Request(url, headers={'User-Agent':UA})
    body=None
    for a in range(4):
        try: body=urllib.request.urlopen(req, timeout=600).read().decode(); break
        except Exception as e: print("retry",e,file=sys.stderr); time.sleep(5*(a+1))
    if not body: continue
    for d in json.loads(body).get('PubTator3',[]):
        gene=set(); ft=set()
        for p in d.get('passages',[]):
            ft.add((p.get('infons') or {}).get('section_type') or '?')
            for an in p.get('annotations',[]):
                if (an.get('infons') or {}).get('type')=='Gene': gene.add(an.get('text',''))
        docs[str(d.get('pmid'))]={'gene':sorted(gene),'ft':bool(ft-{'TITLE','ABSTRACT','?'})}
    time.sleep(1.0)
print("fetched",len(docs),file=sys.stderr)
json.dump(docs, open('amr_api_docs.json','w'))
print(f"{'family':10s} {'checked':>8s} {'API also misses':>16s} {'API has it (bulk stale)':>24s} {'no full text in API':>20s}")
res={}
for k,v in sel.items():
    ok=[p for p in v if p in docs]
    hit=0; nft=0
    for p in ok:
        if not docs[p]['ft']: nft+=1
        joined=" | ".join(docs[p]['gene'])
        if RX[k].search(joined): hit+=1
    print(f"{k:10s} {len(ok):>8d} {len(ok)-hit:>16d} {hit:>24d} {nft:>20d}")
    res[k]={'checked':len(ok),'api_also_misses':len(ok)-hit,'api_has':hit,'api_no_fulltext':nft}
json.dump(res, open('amr_api_check.json','w'), indent=1)
