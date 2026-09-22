import json, sys, collections
out = open('/rag/data/pubtator3/corpus.tsv','w')
n=0; npmid=0; npmcid=0
jc=collections.Counter(); yc=collections.Counter()
dupe=collections.Counter()
for line in open('/rag/oa/corpus/discovery.jsonl'):
    r=json.loads(line); n+=1
    pmid=(r.get('pmid') or '').strip()
    pmcid=(r.get('pmcid') or '').strip()
    j=(r.get('journal') or '').strip()
    y=(r.get('year') or '').strip()
    if pmid: npmid+=1; dupe[pmid]+=1
    if pmcid: npmcid+=1
    jc[j]+=1; yc[y]+=1
    out.write(f"{pmid}\t{pmcid}\t{y}\t{j}\n")
out.close()
print("records",n,"with_pmid",npmid,round(100*npmid/n,2),"with_pmcid",npmcid,round(100*npmcid/n,2))
print("distinct_pmid",len(dupe),"pmids_appearing_more_than_once",sum(1 for v in dupe.values() if v>1))
print("TOP JOURNALS"); 
for j,c in jc.most_common(30): print(f"  {c}\t{j}")
print("YEARS")
for y,c in sorted(yc.items()): print(f"  {y}\t{c}")
