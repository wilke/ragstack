"""Step 1+2: compute the remaining correction set and write corrections_ledger.jsonl BEFORE any write.
Read-only against both stores. Same row format as canary_ledger.jsonl."""
import json,urllib.request,glob,os,sys
C="ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc"
QD="http://localhost:6333"; ES="http://localhost:9200"
BF="/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/oa-year-backfill"
def post(u,b,t=300):
    r=urllib.request.Request(u,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=t))
plan={}   # qid -> (prior_from_plan, new_year)
for f in sorted(glob.glob(f"{BF}/plan/correct_*.txt")):
    y=int(os.path.basename(f)[8:-4])
    for line in open(f):
        qid,prior=line.rstrip("\n").split("\t")
        assert qid not in plan, qid
        plan[qid]=(int(prior),y)
print(f"plan ids={len(plan)}")
canary_done={l.strip() for l in open(f"{BF}/canary_done.txt")}
canary_corr={json.loads(l)["qid"] for l in open(f"{BF}/canary_ledger.jsonl") if json.loads(l)["kind"]=="correct"}
assert canary_corr<=canary_done and canary_corr<=set(plan), "canary corrections not consistent"
remaining=sorted(q for q in plan if q not in canary_done)
print(f"canary corrections already done={len(canary_corr)}  remaining={len(remaining)}")
if len(plan)!=8408 or len(canary_corr)!=200 or len(remaining)!=8208:
    print("!!! GATE 1 FAIL — expected 8408/200/8208"); sys.exit(2)
rows=[]; missing=[]
for i in range(0,len(remaining),1000):
    ids=remaining[i:i+1000]
    got=post(f"{QD}/collections/{C}/points",{"ids":ids,"with_payload":["chunk_id","pmcid","year","date"],"with_vector":False})["result"]
    bid={g["id"]:g["payload"] for g in got}
    for q in ids:
        pl=bid.get(q)
        if pl is None: missing.append(q); continue
        rows.append({"qid":q,"chunk_id":pl["chunk_id"],"pmcid":pl["pmcid"],"prior_year":pl.get("year"),
                     "prior_date":pl.get("date"),"new_year":plan[q][1],"kind":"correct"})
print(f"fetched={len(rows)} missing={len(missing)}")
bad_prior=[r for r in rows if not isinstance(r["prior_year"],int) or r["prior_year"]<=2026]
plan_mismatch=[r for r in rows if r["prior_year"]!=plan[r["qid"]][0]]
has_date=[r for r in rows if r["prior_date"] is not None]
print(f"prior_year<=2026 or non-int: {len(bad_prior)}   prior!=plan: {len(plan_mismatch)}   prior_date present: {len(has_date)}")
# ES cross-check: every chunk's metadata.year must equal the Qdrant prior (stores in lockstep before we start)
es_mis=[]; es_missing=[]
for i in range(0,len(rows),1000):
    ch=rows[i:i+1000]
    docs=post(f"{ES}/{C}/_mget?_source_includes=metadata.year,metadata.date,metadata.pmcid",{"ids":["public:"+r["chunk_id"] for r in ch]})["docs"]
    for r,d in zip(ch,docs):
        if not d.get("found"): es_missing.append(r["qid"]); continue
        m=d["_source"].get("metadata",{})
        if m.get("year")!=r["prior_year"] or m.get("date") is not None or m.get("pmcid")!=r["pmcid"]:
            es_mis.append((r["qid"],r["prior_year"],m.get("year"),m.get("date")))
print(f"ES cross-check: missing={len(es_missing)} disagree={len(es_mis)} e.g.{es_mis[:3]}")
ok = (len(rows)==8208 and not missing and not bad_prior and not plan_mismatch and not has_date and not es_missing and not es_mis)
if not ok:
    print("!!! GATE 2 FAIL — ledger NOT written"); sys.exit(3)
with open("corrections_ledger.jsonl","w") as f:
    for r in rows: f.write(json.dumps(r)+"\n")
import collections
print("LEDGER WRITTEN corrections_ledger.jsonl rows=",len(rows))
print("prior years:",sorted(collections.Counter(r["prior_year"] for r in rows).items())[:12],"...")
print("new years:",sorted(collections.Counter(r["new_year"] for r in rows).items()))
print("distinct pmcids:",len({r["pmcid"] for r in rows}))
