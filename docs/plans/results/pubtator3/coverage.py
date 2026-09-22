import collections, json
corpus=[]
for line in open('corpus.tsv'):
    pmid,pmcid,y,j=line.rstrip('\n').split('\t')
    corpus.append((pmid,pmcid,y,j))
ents=['species','gene','chemical','disease']
cov={}
for e in ents:
    d={}
    for line in open(f'{e}_bypmid.tsv'):
        p,c,ci=line.split('\t'); d[p]=(int(c),int(ci))
    cov[e]=d
    print(e,"pmids",len(d))
joinable=[r for r in corpus if r[0]]
print("corpus",len(corpus),"joinable",len(joinable))
union=set()
for e in ents: union|=set(cov[e])
print("union pmids in any table (global-restricted-to-corpus):",len(union))
res={'corpus_total':len(corpus),'joinable':len(joinable)}
for e in ents:
    n=len(cov[e]); res[e+'_docs']=n; res[e+'_pct_joinable']=round(100*n/len(joinable),2)
    print(f"{e:9s} {n:>9,} {100*n/len(joinable):6.2f}% of joinable  {100*n/len(corpus):6.2f}% of all")
res['any_docs']=len(union); res['any_pct_joinable']=round(100*len(union)/len(joinable),2)
print(f"{'ANY':9s} {len(union):>9,} {100*len(union)/len(joinable):6.2f}% of joinable  {100*len(union)/len(corpus):6.2f}% of all")

# by journal
def block(keyfn, title, order=None, minn=0):
    rows=collections.defaultdict(lambda: collections.Counter())
    for pmid,pmcid,y,j in corpus:
        k=keyfn(y,j)
        r=rows[k]; r['total']+=1
        if not pmid: continue
        r['joinable']+=1
        inany=False
        for e in ents:
            if pmid in cov[e]: r[e]+=1; inany=True
        if inany: r['any']+=1
    return rows
J=block(lambda y,j: j, 'journal')
Y=block(lambda y,j: y, 'year')
targets=["Frontiers in Microbiology","Microorganisms","Antibiotics","PLOS One","Scientific Reports","Nature Communications","Frontiers in Cellular and Infection Microbiology"]
print("\n=== TARGET JOURNALS ===")
print(f"{'journal':52s} {'docs':>8s} {'join':>8s} {'any%':>7s} {'spec%':>7s} {'gene%':>7s} {'chem%':>7s} {'dis%':>7s}")
jout={}
for j in targets:
    r=J[j]; jn=r['joinable'] or 1
    print(f"{j[:52]:52s} {r['total']:>8,} {r['joinable']:>8,} {100*r['any']/jn:7.2f} {100*r['species']/jn:7.2f} {100*r['gene']/jn:7.2f} {100*r['chemical']/jn:7.2f} {100*r['disease']/jn:7.2f}")
    jout[j]={k:r[k] for k in ['total','joinable','any','species','gene','chemical','disease']}
print("\n=== TOP 25 JOURNALS BY SIZE ===")
top=sorted(J.items(), key=lambda kv:-kv[1]['total'])[:25]
for j,r in top:
    jn=r['joinable'] or 1
    print(f"{j[:52]:52s} {r['total']:>8,} {r['joinable']:>8,} {100*r['any']/jn:7.2f} {100*r['species']/jn:7.2f} {100*r['gene']/jn:7.2f}")
print("\n=== BY YEAR (>=2005) ===")
print(f"{'year':6s} {'docs':>9s} {'join':>9s} {'any%':>7s} {'spec%':>7s} {'gene%':>7s} {'chem%':>7s} {'dis%':>7s}")
yout={}
for y in sorted(Y):
    if y and y>='2005':
        r=Y[y]; jn=r['joinable'] or 1
        print(f"{y:6s} {r['total']:>9,} {r['joinable']:>9,} {100*r['any']/jn:7.2f} {100*r['species']/jn:7.2f} {100*r['gene']/jn:7.2f} {100*r['chemical']/jn:7.2f} {100*r['disease']/jn:7.2f}")
        yout[y]={k:r[k] for k in ['total','joinable','any','species','gene','chemical','disease']}
res['by_journal']=jout; res['by_year']=yout
res['by_journal_top25']={j:{k:r[k] for k in ['total','joinable','any','species','gene','chemical','disease']} for j,r in top}
# density
print("\n=== DENSITY (distinct concepts per covered doc) ===")
for e in ents:
    d=cov[e]; tot=sum(v[0] for v in d.values()); wid=sum(v[1] for v in d.values())
    print(f"{e:9s} covered_docs {len(d):>9,} concepts {tot:>12,} mean/doc {tot/len(d):6.2f} with_id {wid:>12,} ({100*wid/tot:.2f}%)")
    res[e+'_concepts']=tot; res[e+'_concepts_with_id']=wid; res[e+'_mean_per_covered_doc']=round(tot/len(d),3)
json.dump(res, open('coverage.json','w'), indent=1)
