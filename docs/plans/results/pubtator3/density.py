import collections, json
ents=['species','gene','chemical','disease']
cov={}
for e in ents:
    d={}
    for line in open(f'{e}_bypmid.tsv'):
        p,c,ci=line.split('\t'); d[p]=int(c)
    cov[e]=d
rows=collections.defaultdict(lambda: collections.Counter())
jrows=collections.defaultdict(lambda: collections.Counter())
hist=collections.Counter()
peryear_hist=collections.defaultdict(collections.Counter)
for line in open('corpus.tsv'):
    pmid,pmcid,y,j=line.rstrip('\n').split('\t')
    if not pmid: continue
    tot=sum(cov[e].get(pmid,0) for e in ents)
    r=rows[y]; r['n']+=1; r['tot']+=tot
    for e in ents: r[e]+=cov[e].get(pmid,0)
    jr=jrows[j]; jr['n']+=1; jr['tot']+=tot
    for e in ents: jr[e]+=cov[e].get(pmid,0)
    hist[min(tot,200)]+=1
    peryear_hist[y][min(tot//10,20)]+=1
print(f"{'year':6s} {'n':>8s} {'concepts/doc':>13s} {'spec':>7s} {'gene':>7s} {'chem':>7s} {'dis':>7s} {'frac<=15concepts':>17s}")
out={}
for y in sorted(rows):
    if y and y>='2010':
        r=rows[y]; n=r['n']
        low=sum(c for b,c in peryear_hist[y].items() if b<=1)  # tot<=19
        print(f"{y:6s} {n:>8,} {r['tot']/n:13.2f} {r['species']/n:7.2f} {r['gene']/n:7.2f} {r['chemical']/n:7.2f} {r['disease']/n:7.2f} {100*low/n:16.1f}%")
        out[y]={'n':n,'concepts_per_doc':round(r['tot']/n,2),'species':round(r['species']/n,2),'gene':round(r['gene']/n,2),'chemical':round(r['chemical']/n,2),'disease':round(r['disease']/n,2),'frac_le19_concepts':round(100*low/n,2)}
print("\n=== TARGET JOURNALS density ===")
jo={}
for j in ["Frontiers in Microbiology","Microorganisms","Antibiotics","PLOS One","Scientific Reports","Nature Communications","Frontiers in Cellular and Infection Microbiology"]:
    r=jrows[j]; n=r['n']
    print(f"{j[:50]:50s} n={n:>7,} concepts/doc {r['tot']/n:7.2f}  spec {r['species']/n:6.2f} gene {r['gene']/n:6.2f} chem {r['chemical']/n:6.2f} dis {r['disease']/n:6.2f}")
    jo[j]={'n':n,'concepts_per_doc':round(r['tot']/n,2),'species':round(r['species']/n,2),'gene':round(r['gene']/n,2),'chemical':round(r['chemical']/n,2),'disease':round(r['disease']/n,2)}
print("\n=== total-concepts histogram (all joinable) ===")
cum=0; tot=sum(hist.values())
for b in [0,1,2,3,5,8,10,15,20,25,30,40,50,75,100,150,200]:
    c=sum(v for k,v in hist.items() if k<=b)
    print(f"  <= {b:4d} concepts: {c:>9,} ({100*c/tot:5.2f}%)")
json.dump({'by_year':out,'by_journal':jo}, open('density.json','w'), indent=1)
