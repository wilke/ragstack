import re, sys, os, json, collections
from multiprocessing import Pool
sys.path.insert(0,'/rag/data/pubtator3')
from amr_terms3 import RX

TAG=re.compile(r'<[^>]+>')
REFLIST=re.compile(r'<ref-list\b.*?</ref-list>', re.S)
BACK=re.compile(r'<back\b.*?</back>', re.S)
WS=re.compile(r'\s+')

paths={}
for line in open('pmcid_paths.tsv'):
    f,p=line.rstrip('\n').split('\t'); paths[f[:-4]]=p

# gene annotations per pmid (surface forms joined)
ann=collections.defaultdict(list)
for line in open('micro_gene_rows.tsv'):
    pm,gid,ment=line.rstrip('\n').split('\t')
    ann[pm].append(ment)

docs=[]
for line in open('micro_corpus.tsv'):
    pmid,pmcid,y,j=line.rstrip('\n').split('\t')
    if pmid and '2010'<=y<='2023' and pmcid in paths:
        docs.append((pmid,pmcid,y,j,paths[pmcid]))
print("docs to scan",len(docs), file=sys.stderr)

def work(d):
    pmid,pmcid,y,j,path=d
    try:
        raw=open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return None
    raw=BACK.sub(' ', REFLIST.sub(' ', raw))
    txt=WS.sub(' ', TAG.sub(' ', raw))
    annstr=" | ".join(ann.get(pmid,()))
    thit={}; ahit=set()
    for k,r in RX.items():
        m=r.search(txt)
        if m: thit[k]=m.group(0)
        if annstr and r.search(annstr): ahit.add(k)
    return (pmid,pmcid,y,j,thit,sorted(ahit),len(ann.get(pmid,())),len(txt))

with Pool(48) as p:
    res=[r for r in p.imap_unordered(work, docs, chunksize=200) if r]
print("scanned",len(res), file=sys.stderr)
with open('amr_scale_rows3.jsonl','w') as f:
    for r in res: f.write(json.dumps(r)+"\n")
