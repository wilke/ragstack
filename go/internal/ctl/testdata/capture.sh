#!/usr/bin/env bash
# capture.sh — snapshot the live coconut host into testdata/live-<date>/ as
# golden fixtures for the ctl packages, with every secret stripped.
#
# Usage:  go/internal/ctl/testdata/capture.sh [--date YYYY-MM-DD]
#
# Reads (never writes) /rag/config/proxy, /rag/data/tenants/*, the gateway on
# :9000, `ss`, /proc. Writes ONLY under testdata/live-<date>/. Re-runnable.
#
# What is stripped (mirrors internal/ctl/settings.Classify):
#   * tenant.env: the API_KEYS / API_KEY_TENANTS / API_KEY_ROLES key strings
#     become same-shape placeholders <REDACTED:k<i>> (same count, same tenant
#     and role strings); every other secret-class value (*_DSN, *PASSWORD*,
#     *TOKEN*, *_API_KEY, *_KEY, *SECRET*, *AUTH*) becomes <REDACTED>;
#     ADMIN_SUBJECTS entries become bvbrc:admin<i>@example.org (same count).
#   * secrets.env: every value becomes <REDACTED>.
#   * procs.json: only the SAFE_ENV allowlist from ops/coconut/snapshot.sh,
#     minus any name matching its SECRET_HINT, minus any value that looks
#     like a token signature or a 64-hex string.
# The run FAILS if any canary shape (sig=…, postgresql://user:pass@, 64-hex)
# survives anywhere in the output tree; settings_canary_test.go re-checks the
# committed tree on every `go test`.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
DATE="2026-09-10"
while (( $# )); do
    case "$1" in
        --date) DATE="$2"; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done
OUT="$HERE/live-$DATE"
RAG="${RAG_ROOT:-/rag}"
PY="${PYTHON:-python3}"
GATEWAY="${GATEWAY_URL:-http://127.0.0.1:9000}"

say() { echo "[capture] $*"; }
die() { echo "[capture] ERROR: $*" >&2; exit 1; }

command -v "$PY" >/dev/null || die "python3 not found (set PYTHON=)"
[[ -d "$RAG/data/tenants" ]] || die "$RAG/data/tenants missing — is this the deploy host?"

mkdir -p "$OUT"/{proxy/conf.d,proxy/snippets,proxy/html,gateway,tenants,new-tenant-dryrun}

# --------------------------------------------------------------------------
# 1. proxy tree (nginx config is not secret; tls/ is deliberately NOT copied)
# --------------------------------------------------------------------------
for f in nginx.conf conf.d/00-maps.conf conf.d/10-gateway.conf \
         snippets/routes.conf snippets/proxy-common.conf snippets/cors.conf html/ragstack.html; do
    if [[ -f "$RAG/config/proxy/$f" ]]; then
        cp "$RAG/config/proxy/$f" "$OUT/proxy/$f"
    else
        say "proxy/$f absent — skipped"
    fi
done

# --------------------------------------------------------------------------
# 2. gateway bodies (skipped, with a marker, when :9000 is down)
# --------------------------------------------------------------------------
if curl -s --max-time 3 -o /dev/null "$GATEWAY/health"; then
    curl -s --max-time 5 "$GATEWAY/"                        > "$OUT/gateway/root.json"
    curl -s --max-time 5 "$GATEWAY/ragstack/tenants"        > "$OUT/gateway/tenants.json"
    curl -s --max-time 5 "$GATEWAY/ragstack/nope/api/health" > "$OUT/gateway/api-unknown-404.json"
    curl -s --max-time 5 "$GATEWAY/ragstack/x"              > "$OUT/gateway/catchall-404.json"
    rm -f "$OUT/gateway/SKIPPED.txt"
else
    say "gateway $GATEWAY down — bodies skipped"
    echo "gateway $GATEWAY was down at capture time $(date -Is); bodies not captured" > "$OUT/gateway/SKIPPED.txt"
fi

# --------------------------------------------------------------------------
# 3. tenants: manifest verbatim; env files redacted; bin + provision verbatim
# --------------------------------------------------------------------------
cp "$RAG/data/tenants/manifest.tsv" "$OUT/tenants/manifest.tsv"

redact_tenant_env() {  # $1 src  $2 dst  $3 tenant
    "$PY" - "$1" "$2" "$3" <<'PYEOF'
import json, re, sys
src, dst, tenant = sys.argv[1:4]
text = open(src, encoding="utf-8").read()
# The three keyed-by-secret values MUST each be rewritten, and counting them
# is the point: the old script fell through silently when the API_KEYS regex
# missed (a different quote style, a continuation, a value split over lines),
# and a miss there writes REAL KEYS into a committed fixture.
TRIPLE_SEEN = {"API_KEYS": 0, "API_KEY_TENANTS": 0, "API_KEY_ROLES": 0}

# Key strings of the API_KEYS triple -> same-shape placeholders, same order.
m = re.search(r"^API_KEYS='(\[.*\])'\s*$", text, re.M)
keys = json.loads(m.group(1)) if m else []
triple = ("API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES")
lines = text.split("\n")
for i, k in enumerate(keys, 1):
    ph = f"<REDACTED:k{i}>"
    if len(k) >= 16:
        lines = [l.replace(k, ph) for l in lines]
    else:  # a short key: only touch the triple, never free text
        lines = [l.replace(k, ph) if l.split("=", 1)[0] in triple else l for l in lines]

SECRET = re.compile(r"(API_KEY[A-Z_]*|_KEY$|SECRET|PASSWORD|TOKEN|DSN|AUTH)")
PUBLIC_DESPITE = {"CHUNK_MAX_TOKENS", "CHUNK_TOKEN_COUNTER", "EMBEDDING_MAX_BATCH_TOKENS",
                  "EMBEDDING_CHARS_PER_TOKEN", "GOWE_RECEIPTS_OUTPUT_KEY", "GOWE_SHARDS_INPUT_KEY"}
out = []
for line in lines:
    mm = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
    if mm:
        k, v = mm.groups()
        if k in triple:
            # Placeholders were substituted above — but only if the keys were
            # parsed at all. Any surviving real key text means the
            # substitution missed and this fixture would carry a live secret.
            TRIPLE_SEEN[k] += 1
            if any(len(x) >= 16 and x in v for x in keys):
                sys.exit("[capture] ERROR: %s: %s still holds a real key after substitution" % (src, k))
            if "<REDACTED:k" not in v and v.strip() not in ("", "''", '""', "'[]'", "'{}'"):
                sys.exit("[capture] ERROR: %s: %s was not rewritten (regex missed?)" % (src, k))
        elif SECRET.search(k) and k not in PUBLIC_DESPITE and v.strip():
            line = f"{k}=<REDACTED>"
        elif k == "ADMIN_SUBJECTS":
            body = v.split("#", 1)[0]
            n = len([e for e in body.split(",") if e.strip()])
            line = "ADMIN_SUBJECTS=" + ",".join(f"bvbrc:admin{i}@example.org" for i in range(1, n + 1))
    out.append(line)
for k, n in TRIPLE_SEEN.items():
    if n != 1:
        sys.exit("[capture] ERROR: %s: expected exactly one %s assignment, saw %d" % (src, k, n))
open(dst, "w", encoding="utf-8").write("\n".join(out))
PYEOF
}

for t in lucid asm dev demo; do
    tdir="$RAG/data/tenants/$t"
    [[ -d "$tdir" ]] || { say "tenant $t absent — skipped"; continue; }
    mkdir -p "$OUT/tenants/$t/config" "$OUT/tenants/$t/bin"
    redact_tenant_env "$tdir/config/tenant.env" "$OUT/tenants/$t/config/tenant.env" "$t"
    if [[ -f "$tdir/config/secrets.env" ]]; then
        sed -E 's/^([A-Za-z_][A-Za-z0-9_]*=).*$/\1<REDACTED>/' "$tdir/config/secrets.env" \
            > "$OUT/tenants/$t/config/secrets.env"
    fi
    [[ -f "$tdir/config/provision.env" ]] && cp "$tdir/config/provision.env" "$OUT/tenants/$t/config/provision.env"
    for b in up.sh down.sh up-es.sh down-es.sh; do
        [[ -f "$tdir/bin/$b" ]] && cp "$tdir/bin/$b" "$OUT/tenants/$t/bin/$b"
    done
    rmdir "$OUT/tenants/$t/bin" 2>/dev/null || true
    find "$tdir" -maxdepth 2 -printf '%y %m %P\n' | sort > "$OUT/tenants/$t/tree.txt"
done

# --------------------------------------------------------------------------
# 4. listeners + process facts (own processes only; others show pid null)
# --------------------------------------------------------------------------
ss -ltnH | awk '{print $4}' | grep -E ':(5[0-9]{3}|8090|9000|9443|6333|6343|9200|24[0-9]{3})$' | sort -t: -k2 -n -u \
    > "$OUT/listen.txt"

"$PY" - "$OUT/procs.json" <<'PYEOF'
import json, os, pwd, re, subprocess, sys
out = sys.argv[1]
SAFE_ENV = re.compile(r"^(CUDA_VISIBLE_DEVICES|HF_HOME|VLLM_CACHE_ROOT|PYTHONPATH|PORT|PYTHONUNBUFFERED|"
                      r"QDRANT__[A-Z_]+|ES_JAVA_OPTS|NEO4J_server_[a-z_]+|PGDATA|POSTGRES_USER|POSTGRES_DB|"
                      r"MODEL_NAME|DEVICE|VITE_[A-Z_]+|GF_SERVER_[A-Z_]+|GOWE_[A-Z_]+|HOME|VIRTUAL_ENV)=")
SECRET_HINT = re.compile(r"(PASS|SECRET|TOKEN|KEY|AUTH)", re.I)
VALUE_CANARY = re.compile(r"(sig=|[0-9a-f]{64})")
PORTS = re.compile(r"^(5\d{3}|8090|9000|9443|6333|6343|9200|24\d{3})$")

def rd(p, f):
    try:
        with open(f"/proc/{p}/{f}", "rb") as fh:
            return fh.read().decode(errors="replace")
    except Exception:
        return ""

rows = []
seen = set()
for line in subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True).stdout.splitlines():
    parts = line.split()
    if len(parts) < 4:
        continue
    port = parts[3].rsplit(":", 1)[1]
    if not PORTS.match(port):
        continue
    m = re.search(r"pid=(\d+)", line)
    pid = int(m.group(1)) if m else None
    key = (int(port), pid)
    if key in seen:
        continue
    seen.add(key)
    row = {"port": int(port), "pid": pid}
    if pid is None:
        row.update({"user": None, "cmdline": None, "cwd": None, "safe_env": [],
                    "note": "listener owned by another account (root or a service account)"})
    else:
        try:
            row["user"] = pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
        except Exception:
            row["user"] = None
        row["cmdline"] = rd(pid, "cmdline").replace("\0", " ").strip()
        try:
            row["cwd"] = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            row["cwd"] = None
        env = [e for e in rd(pid, "environ").split("\0") if e]
        safe = []
        for e in env:
            k = e.split("=", 1)[0]
            if not SAFE_ENV.match(e):
                continue
            if SECRET_HINT.search(k.replace("VISIBLE_DEVICES", "")):
                continue
            if VALUE_CANARY.search(e):
                continue
            safe.append(e)
        row["safe_env"] = sorted(safe)
    rows.append(row)
rows.sort(key=lambda r: (r["port"], r["pid"] or 0))
with open(out, "w") as fh:
    json.dump(rows, fh, indent=2, sort_keys=True)
    fh.write("\n")
PYEOF

# --------------------------------------------------------------------------
# 5. new-tenant.sh dry-run oracle (offline replay for render/parity_test.go)
# --------------------------------------------------------------------------
TMP="/tmp/ctl-capture-$$"
mkdir -p "$TMP/images"
RAG_DATA="$TMP" RAG_IMAGES="$TMP/images" TENANT_PORT_BASE=41000 \
    bash "$REPO/apptainer/new-tenant.sh" ctltest --dry-run > "$OUT/new-tenant-dryrun/ctltest.txt"
rm -rf "$TMP"

# --------------------------------------------------------------------------
# 6. canaries — fail loudly if any secret shape survived
# --------------------------------------------------------------------------
# This list MIRRORS settings/canary_test.go, which re-runs the same sweep over
# the committed tree on every `go test`. They drifted once: the script knew
# only lowercase hex and a postgresql:// DSN, so an uppercase sha256, a JWT, a
# PEM block and a `redis://user:pass@` URL walked straight past the gate that
# is supposed to stand between a live host and a committed fixture. Keep the
# two lists in step.
bad=0
CANARY_NAMES=(64-hex sig= url-creds jwt private-key long-base64)
CANARY_PATS=(
    '[0-9a-fA-F]{64}'
    'sig='
    '[a-z+]+://[^:/@[:space:]]+:[^@[:space:]]+@'
    'eyJ[A-Za-z0-9_-]{10,}\.eyJ'
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    '[A-Za-z0-9+/]{48,}={0,2}'
)
for i in "${!CANARY_NAMES[@]}"; do
    name="${CANARY_NAMES[$i]}"; pat="${CANARY_PATS[$i]}"
    if grep -rEn "$pat" "$OUT" >/dev/null; then
        echo "[capture] CANARY HIT for $name (/$pat/):" >&2
        grep -rEn "$pat" "$OUT" | sed -E 's/^([^:]+:[0-9]+:).{0,40}.*/\1 …/' >&2
        bad=1
    fi
done
(( bad == 0 )) || die "secret-shaped text found in $OUT — fix the redaction before committing"

# And the three keyed-by-secret values must be placeholders in EVERY captured
# tenant.env. The count is the gate: the redactor used to FAIL OPEN — if its
# API_KEYS regex did not match, no substitution happened and the file was
# written out as-is, with live keys in it.
for envfile in "$OUT"/tenants/*/config/tenant.env; do
    [[ -f "$envfile" ]] || continue
    total=$(grep -cE '^(API_KEYS|API_KEY_TENANTS|API_KEY_ROLES)=' "$envfile" || true)
    subbed=$(grep -cE '^(API_KEYS|API_KEY_TENANTS|API_KEY_ROLES)=.*<REDACTED:k' "$envfile" || true)
    (( total == 3 )) || die "$envfile: expected 3 API_KEY* assignments, found $total — the redactor did not see the file it thought it saw"
    (( subbed == 3 )) || die "$envfile: only $subbed of 3 API_KEY* values carry a <REDACTED:k…> placeholder — a real key would be committed"
done

say "wrote $OUT"
find "$OUT" -type f | sort | sed "s|^$HERE/||"
