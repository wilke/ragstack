#!/usr/bin/env bash
# ops/coconut/snapshot.sh — capture the live service state of coconut (READ-ONLY).
#
#   ./snapshot.sh [outdir]        default: /rag/backups/reboot-$(date +%F)
#
# Writes <outdir>/snapshot.json (machine-readable, what verify.sh diffs against)
# and <outdir>/INVENTORY.md (human-readable). Reads /proc, ss, apptainer instance
# list, nvidia-smi, and the stores' read-only listing endpoints. Contacts nothing
# that writes. Never prints environment VALUES that look like secrets — only the
# variable names, plus a whitelist of operational settings (ports, heap, budgets).
set -euo pipefail
OUT=${1:-/rag/backups/reboot-$(date +%F)}
mkdir -p "$OUT"
export OUT
python3 - <<'PY'
import json, os, re, subprocess, hashlib, time, urllib.request, glob, pwd
OUT = os.environ["OUT"]
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
me = pwd.getpwuid(os.getuid()).pw_name

def sh(cmd, timeout=30):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as e:
        return ""

def rd(p, f):
    try:
        with open(f"/proc/{p}/{f}", "rb") as fh:
            return fh.read().decode(errors="replace")
    except Exception:
        return ""

SAFE_ENV = re.compile(r"^(CUDA_VISIBLE_DEVICES|HF_HOME|VLLM_CACHE_ROOT|PYTHONPATH|PORT|PYTHONUNBUFFERED|"
                      r"QDRANT__[A-Z_]+|ES_JAVA_OPTS|NEO4J_server_[a-z_]+|PGDATA|POSTGRES_USER|POSTGRES_DB|"
                      r"MODEL_NAME|DEVICE|VITE_[A-Z_]+|GF_SERVER_[A-Z_]+|GOWE_[A-Z_]+|HOME|VIRTUAL_ENV)=")
SECRET_HINT = re.compile(r"(PASS|SECRET|TOKEN|KEY|AUTH)", re.I)

# --- listening sockets → pids -------------------------------------------------
listen = {}
for line in sh("ss -ltnpH").splitlines():
    parts = line.split()
    if len(parts) < 4: continue
    addr = parts[3]; port = int(addr.rsplit(":", 1)[1])
    m = re.search(r"pid=(\d+)", line)
    listen.setdefault(port, {"addr": addr, "pid": int(m.group(1)) if m else None})

procs = {}
for port, info in sorted(listen.items()):
    p = info["pid"]
    if p is None:
        procs.setdefault(f"port:{port}", {"pid": None, "ports": [port], "owner": "not this user",
                                          "note": "listener owned by another account (root or a service account)"})
        continue
    if p in procs: procs[p]["ports"].append(port); continue
    cmd = rd(p, "cmdline").replace("\0", " ").strip()
    env = [e for e in rd(p, "environ").split("\0") if e]
    env_names = sorted({e.split("=", 1)[0] for e in env})
    env_safe = sorted(e for e in env if SAFE_ENV.match(e) and not SECRET_HINT.search(e.split("=",1)[0].replace("VISIBLE_DEVICES","")))
    try: cwd = os.readlink(f"/proc/{p}/cwd")
    except Exception: cwd = ""
    st = sh(f"ps -o lstart= -p {p}").strip()
    user = sh(f"ps -o user= -p {p}").strip()
    ppid = sh(f"ps -o ppid= -p {p}").strip()
    procs[p] = {"pid": p, "user": user, "ppid": int(ppid) if ppid else None, "ports": [port], "cwd": cwd,
                "started": st, "cmdline": cmd, "env_safe": env_safe, "env_names": env_names,
                "apptainer": next((e.split("=",1)[1] for e in env if e.startswith("APPTAINER_CONTAINER=")), None)}
for p in procs.values():
    if "ports" in p: p["ports"].sort()

# --- non-listening processes we must restore (GoWe workers, labelers, supervisors) --
extra = []
for line in sh("ps -eo pid,user,lstart,args --no-headers").splitlines():
    if not re.search(r"gowe-worker|s0c_label\.py|s0c_supervise\.sh __supervise|squashfuse_ll", line): continue
    if "squashfuse_ll" in line: continue
    pid = int(line.split()[0])
    try: cwd = os.readlink(f"/proc/{pid}/cwd")
    except Exception: cwd = ""
    extra.append({"pid": pid, "user": line.split()[1], "started": " ".join(line.split()[2:7]),
                  "cmdline": " ".join(line.split()[7:]), "cwd": cwd})

# --- apptainer instances + their real bind mounts --------------------------------
inst = []
for line in sh("apptainer instance list").splitlines()[1:]:
    parts = line.split()
    if len(parts) < 3: continue
    name, pid, image = parts[0], int(parts[1]), parts[-1]
    binds = []
    for ml in rd(pid, "mountinfo").splitlines():
        f = ml.split()
        if len(f) < 5: continue
        src, dst = f[3], f[4]
        if dst.startswith(("/proc", "/sys", "/dev", "/etc/", "/usr/bin/nvidia", "/usr/share/egl", "/usr/share/glvnd", "/usr/share/nvidia", "/usr/lib")): continue
        if src == "/" and dst in ("/home/" + me, "/tmp", "/var/tmp"): continue
        if dst == "/tmp" or dst == "/var/tmp" or dst == "/home/" + me: continue
        binds.append({"container": dst, "host_rel_to_/rag": src})
    inst.append({"name": name, "pid": pid, "image": image, "binds": binds})

# --- GPUs ------------------------------------------------------------------------
gpus = [l.strip().split(", ") for l in sh("nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv,noheader").splitlines() if l.strip()]
apps = [l.strip().split(", ") for l in sh("nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader").splitlines() if l.strip()]
uuid2idx = {g[1]: g[0] for g in gpus}
gpu_apps = [{"gpu": uuid2idx.get(a[0], a[0]), "pid": int(a[1]), "used": a[2]} for a in apps]

# --- stores (read-only listings) -------------------------------------------------
def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r: return r.read().decode()
    except Exception as e:
        return None
stores = {}
for port in (6333, 6343, 24041):
    j = get(f"http://127.0.0.1:{port}/collections")
    cols = {}
    if j:
        for c in json.loads(j)["result"]["collections"]:
            cj = get(f"http://127.0.0.1:{port}/collections/{c['name']}")
            try: cols[c["name"]] = json.loads(cj)["result"].get("points_count")
            except Exception: cols[c["name"]] = None
    stores[f"qdrant:{port}"] = cols if j else "DOWN"
for port in (9200, 24003, 24043):
    t = get(f"http://127.0.0.1:{port}/_cat/indices?h=index,docs.count&s=index")
    stores[f"elasticsearch:{port}"] = ({l.split()[0]: int(l.split()[1]) for l in t.splitlines() if l.strip() and not l.startswith(".")} if t is not None else "DOWN")
stores["neo4j-dev:24046"] = "UP" if get("http://127.0.0.1:24046/") is not None else "DOWN"
stores["neo4j:7474"] = "UP" if get("http://127.0.0.1:7474/") is not None else "DOWN"

# --- HTTP health -----------------------------------------------------------------
def code(url):
    try:
        req = urllib.request.Request(url); 
        with urllib.request.urlopen(req, timeout=8) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return None
health = {}
for t, port in (("lucid-next", 24000), ("asm-next", 24020), ("dev", 24040), ("demo", 24060)):
    health[f"api:{t}:{port}"] = code(f"http://127.0.0.1:{port}/health")
    health[f"gateway:{t}"] = code(f"http://127.0.0.1:9000/ragstack/{t}/api/v1/collections?counts=false")
for t in ("asm", "lucid"):
    health[f"gateway:{t} (legacy)"] = code(f"http://127.0.0.1:9000/ragstack/{t}/api/v1/collections?counts=false")
for port in range(9001, 9007):
    health[f"sfr:{port}"] = code(f"http://127.0.0.1:{port}/v1/models")
health["crossencoder:50052"] = code("http://127.0.0.1:50052/health") or code("http://127.0.0.1:50052/docs")
health["embedding:50053"] = code("http://127.0.0.1:50053/health") or code("http://127.0.0.1:50053/docs")
health["gowe:8091"] = code("http://127.0.0.1:8091/api/v1/health") or code("http://127.0.0.1:8091/")
health["prometheus:9090"] = code("http://127.0.0.1:9090/-/ready")
health["grafana:3001"] = code("http://127.0.0.1:3001/api/health")
for name, port in (("demo", 5210), ("lucid-next", 5211), ("asm-next", 5212), ("dev", 8090), ("asm-legacy", 5173), ("lucid-legacy", 5175)):
    health[f"ui:{name}:{port}"] = code(f"http://127.0.0.1:{port}/")

# --- mango (remote; cannot be restored from here) --------------------------------
mango = {}
for port in (8000, 8003, 8004):
    j = get(f"http://mango:{port}/v1/models", timeout=8)
    m = get(f"http://mango:{port}/metrics", timeout=8)
    cfg = {}
    if m:
        for l in m.splitlines():
            if l.startswith("vllm:cache_config_info"):
                cfg = {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', l) if k in ("cache_dtype", "gpu_memory_utilization", "num_gpu_blocks", "block_size")}
                break
    mango[f"mango:{port}"] = {"models": [x["id"] for x in json.loads(j)["data"]] if j else "DOWN",
                             "max_model_len": [x.get("max_model_len") for x in json.loads(j)["data"]] if j else None,
                             "cache_config": cfg}

# --- code + config identity ------------------------------------------------------
def gitsha(path):
    return sh(f"git -C {path} rev-parse --short HEAD 2>/dev/null").strip() or None
repos = {p: gitsha(p) for p in ["/rag/repos/ragstack", "/rag/repos/GoWe", os.path.expanduser("~/Development/ragstack"),
                                os.path.expanduser("~/Development/worktrees/confirmation-run")] + sorted(glob.glob("/rag/repos/tenants/*"))}
def sha(path):
    try: return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    except Exception: return None
configs = {p: sha(p) for p in sorted(glob.glob("/rag/data/tenants/*/config/tenant.env")) +
           ["/rag/config/proxy/nginx.conf", "/rag/config/proxy/conf.d/00-maps.conf", "/rag/config/proxy/conf.d/10-gateway.conf",
            "/rag/config/proxy/conf.d/20-gowe.conf", "/rag/config/unified.models.json", "/scout/wf/gowe/worker-env.env",
            "/scout/wf/gowe/ragstack-worker-env.env"]}
images = {os.path.basename(p): os.path.getsize(p) for p in sorted(glob.glob("/rag/apptainer/images/*.sif"))}
system = {"hostname": sh("hostname").strip(), "kernel": sh("uname -r").strip(),
          "vm.max_map_count": sh("sysctl -n vm.max_map_count").strip(),
          "vm.overcommit_memory": sh("sysctl -n vm.overcommit_memory").strip(),
          "max_map_count_persisted": bool(sh("grep -rs max_map_count /etc/sysctl.conf /etc/sysctl.d/").strip()),
          "fstab_rag_scout": sh("grep -E '/rag|/scout' /etc/fstab").strip().splitlines(),
          "linger": sh("loginctl show-user $USER -p Linger 2>/dev/null").strip(),
          "coconut-proxy.service installed": bool(sh("systemctl cat coconut-proxy 2>/dev/null").strip()),
          "uptime": sh("uptime -s").strip()}
labelers = {}
for f in glob.glob("/rag/tmp/stage0-conf/work/conf/run/heartbeat-*.json"):
    try: labelers[os.path.basename(f)] = json.load(open(f))
    except Exception: pass

snap = {"recorded_utc": now, "recorded_by": me, "system": system, "listeners_by_process": list(procs.values()),
        "non_listening_processes": extra, "apptainer_instances": inst, "gpus": gpus, "gpu_apps": gpu_apps,
        "stores": stores, "health": health, "mango": mango, "repos": repos, "config_sha256_16": configs,
        "images": images, "labelers": labelers}
json.dump(snap, open(f"{OUT}/snapshot.json", "w"), indent=1, default=str)
print(f"wrote {OUT}/snapshot.json")
PY
echo "$OUT"
python3 "$(dirname "$0")/render_inventory.py" "$OUT"
