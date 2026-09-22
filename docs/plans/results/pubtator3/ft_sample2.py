import random, collections, json, urllib.request, time, sys, os
sample=json.load(open('ft_sample.json'))
UA="RAGStack-corpus-coverage-study/0.1 (contact: awilke1972@gmail.com)"
os.makedirs('ft_raw',exist_ok=True)
out=open('ft_sample_result.jsonl','w')
seen=set()
for y,v in sorted(sample.items()):
    for i in range(0,len(v),100):
        chunk=v[i:i+100]
        pmids=",".join(p for p,_,_ in chunk)
        url=f"https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson?pmids={pmids}&full=true"
        req=urllib.request.Request(url, headers={'User-Agent':UA})
        body=None
        for attempt in range(4):
            try:
                body=urllib.request.urlopen(req, timeout=600).read().decode('utf-8'); break
            except Exception as ex:
                print("retry",y,i,ex, file=sys.stderr); time.sleep(5*(attempt+1))
        if body is None: continue
        open(f'ft_raw/{y}_{i}.json','w').write(body)
        d=json.loads(body); docs=d.get('PubTator3',[])
        for doc in docs:
            secs=collections.Counter(); nann=0; bytype=collections.Counter(); ntext=0
            for p in doc.get('passages',[]):
                st=(p.get('infons') or {}).get('section_type') or '?'
                secs[st]+=1; ntext+=len(p.get('text',''))
                for a in p.get('annotations',[]):
                    nann+=1; bytype[(a.get('infons') or {}).get('type','?')]+=1
            rec={'year':y,'pmid':str(doc.get('pmid') or ''),'pmcid':str(doc.get('pmcid') or ''),
                 'npassages':len(doc.get('passages',[])),'sections':dict(secs),'nann':nann,
                 'ann_by_type':dict(bytype),'textlen':ntext}
            out.write(json.dumps(rec)+"\n")
        print("fetched",y,i,"docs",len(docs), file=sys.stderr)
        time.sleep(1.0)
out.close()
