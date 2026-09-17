#!/usr/bin/env python3
"""Give each shared API key its own subject, without reissuing any key.

The tenant maps many keys onto ONE subject, so every key holder is the same
principal: they share a per-owner quota, they co-own each other's collections,
and nothing is attributable. The mapping is key -> subject, so re-pointing the
keys at distinct subjects fixes all three WITHOUT changing any key value.
Nobody needs a new credential.

Dry-run by default. Prints only subjects and counts — never a key value.

    python3 split_key_subjects.py /rag/data/tenants/hackathon/config/secrets.env
    python3 split_key_subjects.py ... --apply          # writes, after a backup

Leaves alone: admin-role keys, and any key whose subject is not --from.
"""
from __future__ import annotations
import argparse, json, pathlib, re, shutil, sys
from datetime import datetime, timezone

VAR = re.compile(r"^(?P<k>API_KEY_TENANTS|API_KEY_ROLES)=(?P<q>['\"]?)(?P<v>.*)(?P=q)$", re.M)


def load(text):
    out = {}
    for m in VAR.finditer(text):
        try:
            out[m.group("k")] = json.loads(m.group("v"))
        except json.JSONDecodeError as e:
            sys.exit(f"{m.group('k')} is not JSON: {e}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("secrets", type=pathlib.Path)
    ap.add_argument("--from", dest="src", default=None,
                    help="subject to split (default: the subject holding the most keys)")
    ap.add_argument("--prefix", default="attendee", help="new subject prefix")
    ap.add_argument("--role", default="user", help="only split keys with this role")
    ap.add_argument("--tenant-env", type=pathlib.Path, default=None,
                    help="tenant.env, to warn about a stale TENANT_COLLECTIONS confinement")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    text = a.secrets.read_text()
    v = load(text)
    tenants, roles = v.get("API_KEY_TENANTS"), v.get("API_KEY_ROLES")
    if tenants is None or roles is None:
        sys.exit("API_KEY_TENANTS / API_KEY_ROLES not found")

    # Show the distribution BEFORE choosing. An earlier version defaulted to a
    # guessed subject and would have split one key instead of thirty, because
    # "which subjects exist" and "how the keys divide between them" are
    # different questions and only the second one matters here.
    import collections as _c
    dist = _c.Counter((sub, roles.get(k)) for k, sub in tenants.items())
    print("key distribution (counts only, no key values):")
    for (sub, role), n in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  subject={sub:<18} role={str(role):<6} keys={n}")
    print()

    if a.src is None:
        (a.src, chosen_role), _ = max(
            ((sr, n) for sr, n in dist.items() if sr[1] == a.role),
            key=lambda kv: kv[1], default=((None, None), 0))
        if a.src is None:
            sys.exit(f"no keys with role={a.role!r}")
        print(f"--from not given; choosing the largest group: {a.src!r}\n")

    targets = [k for k, s in tenants.items() if s == a.src and roles.get(k) == a.role]
    if not targets:
        sys.exit(f"no keys with subject={a.src!r} and role={a.role!r} — nothing to do")

    # Deterministic and stable: sorted by key hash so a re-run maps identically.
    import hashlib
    targets.sort(key=lambda k: hashlib.sha256(k.encode()).hexdigest())
    width = max(2, len(str(len(targets))))
    assign = {k: f"{a.prefix}-{i:0{width}d}" for i, k in enumerate(targets, 1)}

    new_tenants = dict(tenants)
    new_tenants.update(assign)  # 3.8-compatible: dict|dict is 3.9+
    untouched = {s for k, s in tenants.items() if k not in assign}

    print(f"keys total                 {len(tenants)}")
    print(f"splitting (subject={a.src}, role={a.role})  {len(targets)}")
    print(f"left untouched             {len(tenants) - len(targets)}  subjects: {sorted(untouched)}")
    print(f"new subjects               {assign[targets[0]]} … {assign[targets[-1]]}")
    print()
    if a.tenant_env and a.tenant_env.exists():
        te = a.tenant_env.read_text(errors="replace")
        if re.search(rf'TENANT_COLLECTIONS=.*"{re.escape(a.src)}"', te):
            print()
            print(f"  !! {a.src} is confined by TENANT_COLLECTIONS in {a.tenant_env.name}.")
            print("     Confinement applies to READS, so a collection created under a")
            print("     confined subject is invisible even to its creator. Remove that")
            print("     entry in the same edit, or the new subjects inherit nothing but")
            print("     the old subject keeps hiding anything already created under it.")
    print()
    print("register these as service accounts BEFORE restarting:")
    for s in sorted(set(assign.values())):
        print(f"  {s}")

    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.secrets.with_name(f"{a.secrets.name}.bak-split-subjects-{stamp}")
    shutil.copy2(a.secrets, backup)

    def replace(t, key, obj):
        return re.sub(rf"^{key}=.*$", f"{key}='{json.dumps(obj, sort_keys=True)}'", t, count=1, flags=re.M)

    out = replace(text, "API_KEY_TENANTS", new_tenants)
    a.secrets.write_text(out)
    print(f"\nwrote {a.secrets}")
    print(f"backup {backup}")
    print("roles unchanged. Restart the API, then verify.")


if __name__ == "__main__":
    main()
