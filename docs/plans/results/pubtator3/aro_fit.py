import re,sys,json,collections
from multiprocessing import Pool
sys.path.insert(0,'/rag/data/pubtator3')
from amr_terms3 import RX
TAG=re.compile(r'<[^>]+>'); REFLIST=re.compile(r'<ref-list\b.*?</ref-list>',re.S); BACK=re.compile(r'<back\b.*?</back>',re.S); WS=re.compile(r'\s+')
paths={}
for line in open('pmcid_paths.tsv'):
    f,p=line.rstrip('\n').split('\t'); paths[f[:-4]]=p
docs=[]
for line in open('micro_corpus.tsv'):
    pmid,pmcid,y,j=line.rstrip('\n').split('\t')
    if pmid and '2010'<=y<='2023' and pmcid in paths: docs.append(paths[pmcid])
def work(path):
    try: raw=open(path,encoding='utf-8',errors='replace').read()
    except Exception: return collections.Counter()
    txt=WS.sub(' ',TAG.sub(' ',BACK.sub(' ',REFLIST.sub(' ',raw))))
    c=collections.Counter()
    for k,r in RX.items():
        for m in r.finditer(txt): c[m.group(0)]+=1
    return c
tot=collections.Counter()
with Pool(48) as p:
    for c in p.imap_unordered(work, docs, chunksize=200): tot.update(c)
json.dump(dict(tot), open('amr_surface_forms.json','w'))
print("distinct AMR surface strings in micro corpus:",len(tot),"total occurrences:",sum(tot.values()))
