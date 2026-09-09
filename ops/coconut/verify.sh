#!/usr/bin/env bash
# ops/coconut/verify.sh — compare the live system against a baseline snapshot.
#
#   ./verify.sh [baseline-dir]     default: /rag/backups/reboot-2026-09-10
#
# Takes a fresh snapshot (read-only) into <baseline>/verify-<utc>/ and reports, per item:
# health endpoints (baseline code vs now), apptainer instances (present/missing), Qdrant
# collections and point counts, Elasticsearch indices and doc counts, the six SFR endpoints,
# the tenant gateway paths, GoWe, monitoring, mango's three endpoints, GPU placement.
# Exit 1 if anything that was UP in the baseline is DOWN now or a store count changed.
set -uo pipefail
BASE=${1:-/rag/backups/reboot-2026-09-10}
[[ -f $BASE/snapshot.json ]] || { echo "no baseline at $BASE/snapshot.json — run snapshot.sh first" >&2; exit 2; }
NOW=$BASE/verify-$(date -u +%Y%m%dT%H%M%SZ)
"$(dirname "$0")/snapshot.sh" "$NOW" >/dev/null
BASE=$BASE NOW=$NOW python3 - <<'PY'
import json, os, sys
b = json.load(open(os.path.join(os.environ["BASE"], "snapshot.json")))
n = json.load(open(os.path.join(os.environ["NOW"], "snapshot.json")))
bad = 0
def row(ok, label, before, after):
    global bad
    mark = "✓" if ok else "✗"
    if not ok: bad += 1
    print(f"  {mark} {label:42s} {str(before):>14s} → {str(after):<14s}")

print(f"baseline {b['recorded_utc']}  vs  now {n['recorded_utc']}\n")
print("health (HTTP code; 401 on a gateway path means the API is alive)")
for k, v in b["health"].items():
    w = n["health"].get(k)
    up_before = v in (200, 401, 404); up_now = w in (200, 401, 404)
    row(up_now or not up_before, k, v, w)

print("\napptainer instances")
bi = {i["name"] for i in b["apptainer_instances"]}; ni = {i["name"] for i in n["apptainer_instances"]}
for name in sorted(bi | ni):
    row(name in ni or name not in bi, name, "present" if name in bi else "-", "present" if name in ni else "MISSING")

print("\nstores (collections / indices: count of entries, and any count that changed)")
for k, v in b["stores"].items():
    w = n["stores"].get(k)
    if isinstance(v, str) or isinstance(w, str):
        row(w == v or v == "DOWN", k, v if isinstance(v, str) else f"{len(v)} entries", w if isinstance(w, str) else f"{len(w)} entries")
        continue
    missing = sorted(set(v) - set(w)); changed = sorted(c for c in v if c in w and v[c] != w[c])
    row(not missing and not changed, k, f"{len(v)} entries", f"{len(w)} entries" + (f" missing={missing}" if missing else "") + (f" changed={changed}" if changed else ""))
    for c in changed: print(f"      {c}: {v[c]} → {w[c]}")

print("\nmango (remote — not restored by these scripts)")
for k, v in b["mango"].items():
    w = n["mango"].get(k, {})
    row(w.get("models") == v["models"], k, v["models"], w.get("models"))
    if w.get("cache_config") != v["cache_config"]:
        print(f"      cache_config changed: {v['cache_config']} → {w.get('cache_config')}")

print("\nGPU placement (gpu → used memory per pid, baseline vs now)")
def gm(s):
    d = {}
    for a in s["gpu_apps"]: d.setdefault(a["gpu"], []).append(a["used"])
    return {k: sorted(v) for k, v in d.items()}
bg, ng = gm(b), gm(n)
for g in sorted(set(bg) | set(ng), key=lambda x: int(x) if str(x).isdigit() else 99):
    row(g in ng or g not in bg, f"gpu {g}", bg.get(g, "-"), ng.get(g, "MISSING"))

print("\nsystem")
for k in ("vm.max_map_count", "coconut-proxy.service installed", "max_map_count_persisted", "uptime"):
    row(True, k, b["system"].get(k), n["system"].get(k))
print("\nlabelers")
for k, v in n.get("labelers", {}).items():
    print(f"  {k}: {v.get('state')} records={v.get('records')} at {v.get('utc')}")

print(f"\n{'ALL GOOD' if not bad else str(bad) + ' regression(s)'} — details in {os.environ['NOW']}/snapshot.json")
sys.exit(1 if bad else 0)
PY
