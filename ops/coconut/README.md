# ops/coconut — reboot-safe operation of the coconut service stack

| script | does | touches the system? |
|---|---|---|
| `snapshot.sh [dir]` | captures every listener, apptainer instance (with real binds), GPU placement, store listings, health, mango, code/config identity into `dir/snapshot.json` and renders `dir/INVENTORY.md` (via `render_inventory.py`, prepending `NOTES.md`) | no — read-only |
| `pre-reboot.sh [--dry-run] [--all]` | graceful stop in reverse dependency order, by pid file / port / instance name with cwd+cmdline verification; never by name pattern | yes — stops services |
| `restore.sh [--dry-run] [--only G] [--skip G] [--proxy]` | starts everything in dependency order with health waits; idempotent (skips what already runs) | yes — starts services |
| `verify.sh [baseline-dir]` | fresh snapshot, diffed against the baseline; exit 1 on any regression | no — read-only |

## Which tenants these scripts know

`ragstack-ctl tenant list` — the control plane's registry — is the source of truth for which
tenants exist and on which ports. Until PR-E teaches these scripts to read that registry, each one
carries a **literal** list, and a tenant missing from it simply does not come back after a reboot.
Today that list is five: `lucid-next` :24000, `asm-next` :24020, `dev` :24040, `demo` :24060 and
`hackathon` :24080 (added 2026-09-15). `hackathon` also has three dedicated stores
(`qdrant-hackathon` :24081, `elasticsearch-hackathon` :24083, `postgres-hackathon` :24085, started
through the generated `/rag/data/tenants/hackathon/bin/up.sh`) and a **static** UI that nginx serves
from `ui/dist` — so it belongs in the stores and apis lists but never in the Vite UI loops.

Adding a tenant means editing `restore.sh` (stores + both api loops), `pre-reboot.sh` (the API stop
loop + the store list in step 7) and `snapshot.sh` (the store ports + the health loop).
`python/tests/unit/test_coconut_reboot_tenants.py` asserts those lists stay in step, so the next
addition cannot be half-done silently.

Read `NOTES.md` first: it lists what needs an admin (sysctl persistence, the gateway unit), what
was found to be already broken, and the order of operations for a planned reboot. The 2026-09-10
baseline and copies of these scripts are in `/rag/backups/reboot-2026-09-10/`.

Host bootstrap for `ragstack-ctl` (ragops group, linger, `user@<uid>` drop-in, sysctl, the gateway unit above, ctl dirs) is automated in [`ops/ansible/`](../ansible/README.md); NOTES.md admin items 1–3 map to its `root` tag.
