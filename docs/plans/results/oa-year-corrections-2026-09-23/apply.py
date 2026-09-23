"""Apply corrections from corrections_ledger.jsonl. Qdrant first, then ES mirror.
Replicates canary_write.py (Qdrant set_payload year+date, ?wait=true) and canary_write_es.py /
reconcile_es.py (ES _bulk partial doc on _id="public:"+chunk_id, chunk_id fetched from Qdrant by point id).
Idempotent, resumable via done-files. Env: SUBSET=<file of qids>, SUFFIX=<done-file suffix>, BATCH, PAUSE."""
import json,urllib.request,time,os,subprocess,sys,collections
C="ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc"
QD="http://localhost:6333"; ES="http://localhost:9200"
D=f"/rag/data/qdrant/storage/collections/{C}"
BATCH=int(os.environ.get("BATCH","1000")); PAUSE=float(os.environ.get("PAUSE","0.25"))
SUF=os.environ.get("SUFFIX",""); MIN_FREE_GB=400; POINTS=47625155
QDONE=f"corrections_done{SUF}.txt"; EDONE=f"corrections_es_done{SUF}.txt"
def post(u,b,t=300,ndjson=False):
    data=b if ndjson else json.dumps(b).encode()
    r=urllib.request.Request(u,data=data,headers={'Content-Type':'application/x-ndjson' if ndjson else 'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=t))
def disk():
    b=int(subprocess.check_output(["du","-sb",D]).split()[0])
    av=int(subprocess.check_output(["df","-BG","/rag"]).decode().split()[-3].rstrip("G"))
    r=json.load(urllib.request.urlopen(f"{QD}/collections/{C}",timeout=60))["result"]
    return {"bytes":b,"avail_gb":av,"status":r["status"],"segments":r["segments_count"],"points":r["points_count"],"optimizer":r["optimizer_status"]}
def gate(s):
    if s["avail_gb"]<MIN_FREE_GB: print("!!! ABORT: /rag below 400GB"); sys.exit(2)
    if s["points"]!=POINTS: print(f"!!! ABORT: points_count moved {s['points']}"); sys.exit(3)
led=[json.loads(l) for l in open("corrections_ledger.jsonl")]
if os.environ.get("SUBSET"):
    sub={l.strip() for l in open(os.environ["SUBSET"]) if l.strip()}
    led=[e for e in led if e["qid"] in sub]
    assert len(led)==len(sub), (len(led),len(sub))
print(f"ledger rows in scope={len(led)} SUFFIX='{SUF}'",flush=True)
b0=disk(); print("BEFORE:",json.dumps(b0),flush=True); gate(b0)
# ---- Qdrant ----
qdone={l.strip() for l in open(QDONE)} if os.path.exists(QDONE) else set()
todo=[e for e in led if e["qid"] not in qdone]
byyear=collections.defaultdict(list)
for e in todo: byyear[e["new_year"]].append(e["qid"])
print(f"qdrant todo={len(todo)} already={len(qdone)} years={len(byyear)}",flush=True)
df=open(QDONE,"a"); n=0; t0=time.time()
for y in sorted(byyear):
    ids=byyear[y]
    for i in range(0,len(ids),BATCH):
        ch=ids[i:i+BATCH]
        r=post(f"{QD}/collections/{C}/points/payload?wait=true",{"payload":{"year":int(y),"date":int(y)*10000},"points":ch})
        assert r.get("status")=="ok", r
        for q in ch: df.write(q+"\n")
        df.flush(); n+=len(ch)
        if n%2000<BATCH:
            s=disk(); print(f"  [q {n:,}] {time.time()-t0:.1f}s status={s['status']} segs={s['segments']} d={(s['bytes']-b0['bytes'])/2**30:+.2f}GiB avail={s['avail_gb']}G pts={s['points']:,}",flush=True); gate(s)
        time.sleep(PAUSE)
print(f"QDRANT DONE {n:,} in {time.time()-t0:.1f}s",flush=True)
# ---- ES mirror: chunk_id fetched from Qdrant by point id (as reconcile_es.py), asserted == ledger ----
edone={l.strip() for l in open(EDONE)} if os.path.exists(EDONE) else set()
todo=[e for e in led if e["qid"] not in edone]
print(f"es todo={len(todo)} already={len(edone)}",flush=True)
ef=open(EDONE,"a"); n=0; errs=0; t0=time.time()
for i in range(0,len(todo),BATCH):
    ch=todo[i:i+BATCH]
    got=post(f"{QD}/collections/{C}/points",{"ids":[e["qid"] for e in ch],"with_payload":["chunk_id","year"],"with_vector":False})["result"]
    cid={g["id"]:g["payload"] for g in got}
    lines=[]
    for e in ch:
        p=cid.get(e["qid"])
        assert p and p["chunk_id"]==e["chunk_id"], (e["qid"],p)
        assert p.get("year")==e["new_year"], ("qdrant not yet corrected", e["qid"], p)
        lines.append(json.dumps({"update":{"_index":C,"_id":"public:"+e["chunk_id"]}})+"\n")
        lines.append(json.dumps({"doc":{"metadata":{"year":int(e["new_year"]),"date":int(e["new_year"])*10000}}})+"\n")
    r=post(f"{ES}/_bulk","".join(lines).encode(),ndjson=True)
    if r.get("errors"):
        for it in r["items"]:
            if it["update"].get("error"):
                errs+=1
                if errs<=3: print("  ES ERR:",json.dumps(it["update"]["error"])[:220],flush=True)
    if errs: print("!!! ABORT: ES bulk errors"); sys.exit(4)
    for e in ch: ef.write(e["qid"]+"\n")
    ef.flush(); n+=len(ch); time.sleep(0.1)
print(f"ES DONE {n:,} in {time.time()-t0:.1f}s errors={errs}",flush=True)
s=disk(); print("AFTER:",json.dumps(s),flush=True)
