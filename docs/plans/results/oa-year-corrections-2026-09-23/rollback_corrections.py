#!/usr/bin/env python3
"""ROLLBACK for the 2026-09-23 corrections run. Same logic as ../oa-year-backfill/rollback.py's corrections
branch, reading corrections_ledger.jsonl instead: Qdrant set year=prior_year then delete key `date`;
ES script metadata.year=prior_year, remove metadata.date. Both stores. Resumable via rollback_corrections_done<SUFFIX>.txt.
  python3 rollback_corrections.py --dry-run
  python3 rollback_corrections.py --execute          (env SUBSET=<qid file>, SUFFIX=<done-file suffix>)"""
import json,urllib.request,os,sys,time,collections
from pathlib import Path
C="ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc"
QD="http://localhost:6333"; ES="http://localhost:9200"
SUF=os.environ.get("SUFFIX",""); DONE=f"rollback_corrections_done{SUF}.txt"
def post(u,b,t=300,ndjson=False):
    data=b if ndjson else json.dumps(b).encode()
    r=urllib.request.Request(u,data=data,headers={'Content-Type':'application/x-ndjson' if ndjson else 'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=t))
# The ledger is resolved next to this script, never from cwd.
LEDGER=Path(__file__).resolve().parent/"corrections_ledger.jsonl"
if not LEDGER.is_file():
    sys.exit(f"refusing: ledger not found at {LEDGER} (it must sit next to rollback_corrections.py)")
led=[json.loads(l) for l in open(LEDGER)]
if os.environ.get("SUBSET"):
    sub={l.strip() for l in open(os.environ["SUBSET"]) if l.strip()}
    led=[e for e in led if e["qid"] in sub]; assert len(led)==len(sub)
corrs=[(e["qid"],e["prior_year"],e["chunk_id"]) for e in led]
print(f"to revert: {len(corrs):,} corrections (SUFFIX='{SUF}')")
if "--dry-run" in sys.argv or "--execute" not in sys.argv:
    print("dry run - nothing written. re-run with --execute"); sys.exit(0)
done={l.strip() for l in open(DONE)} if os.path.exists(DONE) else set()
df=open(DONE,"a")
def chunks(xs,n):
    for i in range(0,len(xs),n): yield xs[i:i+n]
byprior=collections.defaultdict(list)
for q,p,c in corrs:
    if q not in done: byprior[p].append((q,c))
n=0
for prior,items in byprior.items():
    for batch in chunks(items,1000):
        qids=[q for q,_ in batch]
        got=post(f"{QD}/collections/{C}/points",{"ids":qids,"with_payload":["chunk_id"],"with_vector":False})["result"]
        live={g["id"]:g["payload"]["chunk_id"] for g in got}
        for q,c in batch: assert live.get(q)==c, (q,c,live.get(q))
        post(f"{QD}/collections/{C}/points/payload?wait=true",{"payload":{"year":int(prior)},"points":qids})
        post(f"{QD}/collections/{C}/points/payload/delete?wait=true",{"keys":["date"],"points":qids})
        lines=[]
        for q,c in batch:
            lines.append(json.dumps({"update":{"_index":C,"_id":"public:"+c}})+"\n")
            lines.append(json.dumps({"script":{"source":"ctx._source.metadata.year=params.y;ctx._source.metadata.remove('date')","params":{"y":int(prior)}}})+"\n")
        r=post(f"{ES}/_bulk","".join(lines).encode(),ndjson=True)
        assert not r.get("errors"), [it for it in r["items"] if it["update"].get("error")][:3]
        for q in qids: df.write(q+"\n")
        df.flush(); n+=len(qids); time.sleep(0.2)
print(f"ROLLBACK COMPLETE: {n:,} reverted. Verify: ES year>2026 should return to 8,208 (or the subset size).")
