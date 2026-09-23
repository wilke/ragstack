"""Verify the corrections in BOTH stores. Env: SUBSET=<qid file> (dry-run mode), EXPECT=applied|reverted.
Full mode also checks the global ES counts and that writes have stopped (update_queue, three du samples)."""
import json,urllib.request,os,sys,random,pickle,subprocess,time
C="ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc"
QD="http://localhost:6333"; ES="http://localhost:9200"
BF="/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/oa-year-backfill"
D=f"/rag/data/qdrant/storage/collections/{C}"
def post(u,b,t=300):
    r=urllib.request.Request(u,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=t))
def get(u): return json.load(urllib.request.urlopen(u,timeout=60))
fails=[]
def chk(name,cond,detail=""):
    print(("  PASS  " if cond else "  FAIL  ")+name+("  "+detail if detail else ""),flush=True)
    if not cond: fails.append(name)
led=[json.loads(l) for l in open("corrections_ledger.jsonl")]
subset=bool(os.environ.get("SUBSET"))
if subset:
    sub={l.strip() for l in open(os.environ["SUBSET"]) if l.strip()}
    led=[e for e in led if e["qid"] in sub]
expect=os.environ.get("EXPECT","applied")
def want(e): return (e["new_year"],e["new_year"]*10000) if expect=="applied" else (e["prior_year"],None)
print(f"=== scope: {len(led)} rows, expect={expect}, subset={subset} ===")
r=get(f"{QD}/collections/{C}")["result"]
chk("qdrant points_count == 47,625,155", r["points_count"]==47625155, f"got {r['points_count']:,}")
chk("qdrant status green", r["status"]=="green", f"optimizer={r['optimizer_status']} segments={r['segments_count']}")
esc=post(f"{ES}/{C}/_count",{"query":{"match_all":{}}})["count"]
chk("ES doc count == 47,625,155", esc==47625155, f"got {esc:,}")
# per-point checks, both stores, ALL rows in scope
qbad=[]; qtype=[]; esbad=[]; sib=[]
for i in range(0,len(led),1000):
    ch=led[i:i+1000]
    got=post(f"{QD}/collections/{C}/points",{"ids":[e["qid"] for e in ch],"with_payload":["year","date","pmcid","chunk_id"],"with_vector":False})["result"]
    bid={g["id"]:g["payload"] for g in got}
    docs=post(f"{ES}/{C}/_mget",{"ids":["public:"+e["chunk_id"] for e in ch]})["docs"]
    for e,d in zip(ch,docs):
        wy,wd=want(e); p=bid.get(e["qid"]) or {}
        if p.get("year")!=wy or p.get("date")!=wd or p.get("pmcid")!=e["pmcid"] or p.get("chunk_id")!=e["chunk_id"]:
            qbad.append((e["qid"],p.get("year"),p.get("date"),wy,wd))
        if not isinstance(p.get("year"),int) or (wd is not None and not isinstance(p.get("date"),int)): qtype.append(e["qid"])
        m=(d.get("_source") or {}).get("metadata",{})
        if m.get("year")!=wy or m.get("date")!=wd or m.get("pmcid")!=e["pmcid"] or not isinstance(m.get("year"),int):
            esbad.append((e["qid"],m.get("year"),m.get("date"),wy,wd))
        miss=[k for k in ("pmcid","title","doi","source_path","tenant_id") if k not in m]
        if miss: sib.append((e["qid"],miss))
chk(f"{len(led)}/{len(led)} qdrant year,date == expected (pmcid,chunk_id intact)", not qbad, f"{len(qbad)} bad e.g.{qbad[:3]}")
chk("qdrant year/date are int", not qtype, f"{len(qtype)} bad")
chk(f"{len(led)}/{len(led)} ES metadata.year,date == expected and int (pmcid intact)", not esbad, f"{len(esbad)} bad e.g.{esbad[:3]}")
chk("ES sibling metadata intact", not sib, f"{len(sib)} e.g.{sib[:2]}")
# discovery agreement on a random 50 (both stores already proven == new_year above; this ties new_year to the source)
prim=pickle.load(open(f"{BF}/discovery.pkl","rb"))["prim"]
samp=random.Random(23).sample(led,min(50,len(led)))
dis=[(e["qid"],e["pmcid"],e["new_year"],prim.get(e["pmcid"])) for e in samp if prim.get(e["pmcid"])!=e["new_year"]]
chk(f"random {len(samp)}: new_year == discovery.jsonl year for pmcid", not dis, f"{len(dis)} e.g.{dis[:3]}")
alld=[e for e in led if prim.get(e["pmcid"])!=e["new_year"]]
chk(f"all {len(led)}: new_year == discovery year", not alld, f"{len(alld)}")
if not subset:
    fy=post(f"{ES}/{C}/_count",{"query":{"range":{"metadata.year":{"gt":2026}}}})["count"]
    exp_fy=0 if expect=="applied" else 8208
    chk(f"ES year>2026 == {exp_fy}", fy==exp_fy, f"got {fy}")
    hy=post(f"{ES}/{C}/_count",{"query":{"exists":{"field":"metadata.year"}}})["count"]
    chk("ES has-year == 7,294,845 (unchanged)", hy==7294845, f"got {hy:,}")
    hd=post(f"{ES}/{C}/_count",{"query":{"exists":{"field":"metadata.date"}}})["count"]
    exp_hd=253008+8208 if expect=="applied" else 253008
    chk(f"ES has-date == {exp_hd:,}", hd==exp_hd, f"got {hd:,}")
    # sanity on the subset filter by int term: ids restricted, year == new_year
    y=2014; ids=[e for e in led if e["new_year"]==y]
    qc=post(f"{QD}/collections/{C}/points/count",{"filter":{"must":[{"key":"year","match":{"value":y}},{"has_id":[e["qid"] for e in ids]}]},"exact":True})["result"]["count"]
    chk(f"qdrant int-match year={y} on corrected subset == {len(ids)}", qc==len(ids), f"got {qc}")
    ec=post(f"{ES}/{C}/_count",{"query":{"bool":{"filter":[{"term":{"metadata.year":y}},{"ids":{"values":["public:"+e["chunk_id"] for e in ids]}}]}}})["count"]
    chk(f"ES int-term year={y} on corrected subset == {len(ids)}", ec==len(ids), f"got {ec}")
    # writes stopped: update_queue 0 and three identical du samples
    def uq():
        t=get(f"{QD}/telemetry?details_level=3")["result"]["collections"]["collections"]
        c=[c for c in t if c.get("id")==C][0]
        return [(s["id"],s["local"]["update_queue"]) for s in c["shards"]]
    q=uq(); chk("qdrant update_queue length == 0", all(x[1]["length"]==0 for x in q), f"{q}")
    du=[]
    for k in range(3):
        du.append(int(subprocess.check_output(["du","-sb",D]).split()[0])); time.sleep(20)
    chk("three du samples byte-identical (writes stopped)", len(set(du))==1, f"{du}")
    r2=get(f"{QD}/collections/{C}")["result"]
    chk("qdrant optimizer_status ok after settle", r2["optimizer_status"]=="ok", f"{r2['optimizer_status']} segs={r2['segments_count']}")
print(); print("VERIFY:", "ALL PASS" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
