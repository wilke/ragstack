import re, json, collections
terms=[]; cur=None
for line in open('card/aro.obo'):
    line=line.rstrip('\n')
    if line=='[Term]':
        cur={'syn':[],'pv':[],'is_a':[],'xref':[]}; terms.append(cur); continue
    if line.startswith('[') and line!='[Term]': cur=None; continue
    if cur is None: continue
    if line.startswith('id: '): cur['id']=line[4:]
    elif line.startswith('name: '): cur['name']=line[6:]
    elif line.startswith('synonym: '):
        m=re.match(r'synonym: "(.*?)" (\w+)', line)
        if m: cur['syn'].append(m.group(1))
    elif line.startswith('property_value: '): cur['pv'].append(line[16:].strip())
    elif line.startswith('is_a: '): cur['is_a'].append(line[6:].split(' ! ')[0])
    elif line.startswith('xref: '): cur['xref'].append(line[6:])
    elif line.startswith('is_obsolete: true'): cur['obsolete']=True
terms=[t for t in terms if t.get('id')]
print("total terms", len(terms), "obsolete", sum(1 for t in terms if t.get('obsolete')))
pvc=collections.Counter()
for t in terms:
    for p in t['pv']: pvc[p]+=1
for p,c in pvc.most_common(20): print("  pv", c, p)
nsyn=sum(len(t['syn']) for t in terms)
print("synonyms total", nsyn, "terms with >=1 synonym", sum(1 for t in terms if t['syn']))
# AMR-gene-ish terms
gene_like=[t for t in terms if any(p.startswith('classified_as_amr_gene') or p=='classified_as_amr_gene_family' for p in t['pv'])]
print("classified_as_amr_gene_family terms", len(gene_like))
json.dump(terms, open('aro_terms.json','w'))
