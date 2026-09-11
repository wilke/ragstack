#!/usr/bin/env bash
# ops/ansible/check.sh [tag] [extra ansible-playbook args...]
#
# Validation that never touches the host:
#   1. every *.yml parses (python yaml);
#   2. every roles/*/templates/*.j2 renders with StrictUndefined against
#      group_vars/all.yml + role defaults (+ ctl_uid from getent);
#   3. if ansible-playbook is on PATH: --syntax-check for both playbooks and,
#      when a tag is given, `--check --diff --tags <tag>` (pass -K for root).
#
#   ./check.sh                 # 1 + 2 (+ syntax-check if ansible is present)
#   ./check.sh ctl             # + check-mode dry run of the ctl tag
#   ./check.sh root -K         # + check-mode dry run of the root tag (sudo)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
tag="${1:-}"; [[ $# -gt 0 ]] && shift

# A python with yaml + jinja2 (the conda base on PATH may lack jinja2).
PY=""
for cand in "${PYTHON:-}" python3 /usr/bin/python3 /rag/envs/ragstack/bin/python; do
    [[ -n "$cand" ]] || continue
    if "$cand" -c 'import yaml, jinja2' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [[ -z "$PY" ]]; then
    echo "check.sh: no python with yaml+jinja2 found (set PYTHON=...)" >&2
    exit 2
fi

echo "== 1. YAML parse ($PY)"
mapfile -t yml < <(find . -name '*.yml' -not -path './.ansible/*' | sort)
"$PY" - "${yml[@]}" <<'PYEOF'
import sys, yaml
bad = 0
for f in sys.argv[1:]:
    try:
        with open(f) as fh:
            yaml.safe_load(fh)
        print(f"  ok   {f}")
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  FAIL {f}: {e}")
sys.exit(1 if bad else 0)
PYEOF

echo "== 2. Template render (StrictUndefined)"
CTL_UID="$(id -u "$(awk '$1=="ctl_user:"{print $2}' group_vars/all.yml)" 2>/dev/null || echo 10078)"
CTL_UID="$CTL_UID" "$PY" - <<'PYEOF'
import glob, os, sys, yaml
from jinja2 import Environment, StrictUndefined

def load(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or {}

env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
base = load("group_vars/all.yml")
# Values that site.yml sets at run time or that come from the vault.
base.update({
    "ansible_managed": "Ansible managed",
    "ctl_uid": os.environ.get("CTL_UID", "10078"),
    "ctl_api_keys": '["deadbeef"]',
    "ctl_api_key_roles": '{"deadbeef":"operator"}',
    "ctl_admin_subjects": "bvbrc:someone",
    # group_vars uses lookup('env', 'HOME') for ctl_mirror_src; stub it.
    "lookup": lambda kind, key, *a, **k: os.environ.get(key, "") if kind == "env" else "",
})

def resolve(vars_):
    # Render string values that reference other vars (one pass per level, as
    # ansible would lazily) so templates see fully expanded paths.
    for _ in range(5):
        changed = False
        for k, v in list(vars_.items()):
            if isinstance(v, str) and "{{" in v:
                new = env.from_string(v).render(**vars_)
                if new != v:
                    vars_[k] = new; changed = True
        if not changed:
            break
    return vars_

bad = 0
for tpl in sorted(glob.glob("roles/*/templates/*.j2")):
    role = tpl.split("/")[1]
    vars_ = dict(load(f"roles/{role}/defaults/main.yml")) if os.path.exists(f"roles/{role}/defaults/main.yml") else {}
    vars_.update(base)
    vars_ = resolve(vars_)
    try:
        with open(tpl) as fh:
            out = env.from_string(fh.read()).render(**vars_)
        print(f"  ok   {tpl} ({len(out.splitlines())} lines)")
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  FAIL {tpl}: {e}")
sys.exit(1 if bad else 0)
PYEOF

if ! command -v ansible-playbook >/dev/null 2>&1; then
    echo "== 3. ansible-playbook not on PATH — skipping syntax-check/check-mode (see README: control-node setup)"
    exit 0
fi

echo "== 3. ansible-playbook --syntax-check"
ansible-playbook -i inventory/coconut.yml site.yml --syntax-check
ansible-playbook -i inventory/coconut.yml tenant-host.yml --syntax-check

if [[ -n "$tag" ]]; then
    echo "== 4. check mode: --tags $tag (never mutates; sudo needed for root/hardening: pass -K)"
    ansible-playbook -i inventory/coconut.yml site.yml --check --diff --tags "$tag" "$@"
fi
