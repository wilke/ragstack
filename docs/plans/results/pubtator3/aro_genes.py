import json, collections
terms=json.load(open('aro_terms.json'))
byid={t['id']:t for t in terms}
children=collections.defaultdict(list)
for t in terms:
    for p in t['is_a']: children[p].append(t['id'])
famroots=[t['id'] for t in terms if 'classified_as_amr_gene_family' in t['pv']]
seen=set(); stack=list(famroots)
while stack:
    i=stack.pop()
    if i in seen: continue
    seen.add(i); stack.extend(children.get(i,[]))
genes=sorted(seen-set(famroots))
print("amr gene-family roots",len(famroots)," descendant gene/allele terms",len(genes)," union",len(seen))
allnames=set(); 
for i in seen:
    t=byid[i]; allnames.add(t['name'])
    for s in t['syn']: allnames.add(s)
print("distinct surface names (name+synonyms) in AMR-gene subtree:",len(allnames))
for probe in ["OXA-48","CTX-M-15","KPC-2","mecA","NDM-1","blaOXA-48","blaCTX-M-15","vanA","tetM","tet(M)","ermB","erm(B)","sul1","qnrS1"]:
    hits=[i for i in seen if byid[i]['name']==probe or probe in byid[i]['syn']]
    print(f"  probe {probe!r}: {'IN' if hits else 'ABSENT'} {hits[:2]}")
open('aro_amr_names.txt','w').write("\n".join(sorted(allnames)))
import random
random.seed(0); print("sample names:", random.sample(sorted(allnames), 25))
# length/char profile
import re
print("names containing a digit:", sum(1 for n in allnames if re.search(r'\d',n)))
print("names starting with 'bla':", sum(1 for n in allnames if n.startswith('bla')))
