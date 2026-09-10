#!/usr/bin/env python3
"""Render INVENTORY.md from a snapshot.json produced by snapshot.sh.

    render_inventory.py <snapshot-dir>      writes <snapshot-dir>/INVENTORY.md

The generated part is everything under "Generated from the live system". The notes at the
top are maintained by hand in NOTES.md beside this script and are prepended verbatim, so a
re-run of snapshot.sh keeps the operator's annotations.
"""
import json, os, sys, re

d = sys.argv[1]
s = json.load(open(os.path.join(d, "snapshot.json")))
out = []
w = out.append
notes = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NOTES.md")
if os.path.exists(notes):
    w(open(notes).read().rstrip() + "\n")
w(f"\n---\n\n# Generated from the live system — {s['recorded_utc']} by {s['recorded_by']} on {s['system']['hostname']}\n")
w("## System\n")
w("| item | value |\n|---|---|")
for k, v in s["system"].items():
    w(f"| {k} | {v} |")

def short(cmd, n=110):
    cmd = re.sub(r"\s+", " ", cmd)
    return (cmd[:n] + "…") if len(cmd) > n else cmd

# classify listeners
def role(p):
    c, cwd, ports = p.get("cmdline", ""), p.get("cwd", ""), p.get("ports", [])
    if p.get("pid") is None:
        port = ports[0] if ports else 0
        if port in (9000, 9443, 8081, 8444): return "gateway nginx (svcbvbrc, apptainer nginx.sif)", "admin: coconut-proxy.service, or restore.sh --proxy"
        sysd = {22: "sshd", 25: "postfix (localhost)", 53: "systemd-resolved", 111: "rpcbind", 5666: "nagios nrpe", 6556: "check_mk agent", 6818: "slurmd"}
        return f"system daemon: {sysd.get(port, 'unknown, root-owned')}", "boot (systemd)"
    if "vllm serve Salesforce/SFR" in c: return "SFR embedding endpoint", "restore.sh sfr"
    if "VLLM::EngineCore" in c: return "vLLM engine core (child of an SFR endpoint)", "(follows its parent)"
    if "uvicorn ragstack.api.main:app" in c:
        t = cwd.split("/tenants/")[1].split("/")[0] if "/tenants/" in cwd else "legacy"
        return f"tenant API {t}", "restore.sh apis"
    if "uvicorn main:app" in c and cwd == "/app": return "sidecar (apptainer python.sif)", "restore.sh sidecars"
    if "vite" in c and "--base" in c: return "tenant UI (base-aware Vite)", "restore.sh uis"
    if "vite" in c: return "Vite dev server (legacy / personal)", "not restored (legacy-ui or personal)"
    if "gowe-server" in c: return "GoWe server", "restore.sh gowe"
    if "prometheus" in c or "grafana" in c: return "monitoring (apptainer docker://)", "restore.sh gowe (start-monitoring.sh)"
    if c.startswith("./qdrant"): return "Qdrant (apptainer instance)", "restore.sh stores"
    if "elasticsearch" in c: return "Elasticsearch (apptainer instance)", "restore.sh stores"
    if "neo4j" in c: return "Neo4j (apptainer instance)", "restore.sh stores"
    if c.startswith("postgres"): return "Postgres (apptainer instance)", "restore.sh stores"
    if c.startswith("redis-server"): return "Redis (apptainer instance)", "restore.sh stores"
    if "p3-web" in c: return "BV-BRC web (personal dev)", "not restored"
    if "http.server" in c: return "docs http.server in /tmp (lost on reboot)", "not restored"
    if c == "codex": return "codex CLI (interactive)", "not restored"
    return "?", "review"

w("\n## Listening services (one row per process; vLLM engine-core children omitted)\n")
w("| ports | role | pid | user | since | cwd | command | GPU | restored by |\n|---|---|---|---|---|---|---|---|---|")
gpu_of = {a["pid"]: a["gpu"] for a in s["gpu_apps"]}
rows = []
for p in s["listeners_by_process"]:
    r, how = role(p)
    if "engine core" in r: continue
    ports = ",".join(str(x) for x in p["ports"])
    cuda = next((e.split("=")[1] for e in p.get("env_safe", []) if e.startswith("CUDA_VISIBLE_DEVICES=")), "")
    gpu = gpu_of.get(p.get("pid"), cuda)
    rows.append((min(p["ports"]), f"| {ports} | {r} | {p.get('pid') or '-'} | {p.get('user','')} | {p.get('started','')[4:16]} | `{p.get('cwd','')}` | `{short(p.get('cmdline',''))}` | {gpu} | {how} |"))
for _, line in sorted(rows): w(line)

w("\n## Non-listening processes that must be restored\n")
w("| pid | since | role | command |\n|---|---|---|---|")
for p in sorted(s["non_listening_processes"], key=lambda x: x["cmdline"]):
    c = p["cmdline"]
    r = "GoWe worker" if "gowe-worker" in c else ("labeler supervisor" if "supervise" in c else ("labeler" if "s0c_label" in c else "?"))
    w(f"| {p['pid']} | {p['started'][4:16]} | {r} | `{short(c, 150)}` |")

w("\n## Apptainer instances and their writable binds (from /proc/<pid>/mountinfo)\n")
w("| instance | image | container path ← host path (relative to /rag) |\n|---|---|---|")
for i in s["apptainer_instances"]:
    binds = "<br>".join(f"`{b['container']}` ← `{b['host_rel_to_/rag']}`" for b in i["binds"]) or "(none beyond defaults)"
    w(f"| {i['name']} | {os.path.basename(i['image'])} | {binds} |")

w("\n## GPUs\n")
w("| gpu | memory used / total | processes |\n|---|---|---|")
by = {}
for a in s["gpu_apps"]: by.setdefault(a["gpu"], []).append(f"{a['pid']} ({a['used']})")
for g in s["gpus"]:
    w(f"| {g[0]} | {g[2]} / {g[3]} | {', '.join(by.get(g[0], ['— free']))} |")

w("\n## Stores\n")
for k, v in s["stores"].items():
    if isinstance(v, str): w(f"- **{k}**: {v}"); continue
    w(f"- **{k}** — {len(v)} entries")
    for name, cnt in sorted(v.items()):
        w(f"  - `{name}`: {cnt:,}" if isinstance(cnt, int) else f"  - `{name}`: {cnt}")

w("\n## Health at snapshot time (HTTP codes; 401 on a gateway path = alive)\n")
w("| check | code |\n|---|---|")
for k, v in s["health"].items(): w(f"| {k} | {v} |")

w("\n## mango (remote host — restored by its admin, verified from here)\n")
w("| endpoint | served model | max_model_len | vLLM cache config |\n|---|---|---|---|")
for k, v in s["mango"].items():
    w(f"| {k} | {v['models']} | {v.get('max_model_len')} | {v.get('cache_config')} |")

w("\n## Code and config identity\n")
w("| path | git HEAD |\n|---|---|")
for k, v in s["repos"].items(): w(f"| `{k}` | {v} |")
w("\n| config file | sha256[:16] |\n|---|---|")
for k, v in s["config_sha256_16"].items(): w(f"| `{k}` | {v} |")
w("\n| image | bytes |\n|---|---|")
for k, v in s["images"].items(): w(f"| {k} | {v:,} |")
w("\n## Labelers (quarantined confirmation run)\n")
for k, v in s["labelers"].items(): w(f"- {k}: `{json.dumps(v)}`")

open(os.path.join(d, "INVENTORY.md"), "w").write("\n".join(out) + "\n")
print(f"wrote {os.path.join(d, 'INVENTORY.md')}")
