# ops/coconut — reboot-safe operation of the coconut service stack

| script | does | touches the system? |
|---|---|---|
| `snapshot.sh [dir]` | captures every listener, apptainer instance (with real binds), GPU placement, store listings, health, mango, code/config identity into `dir/snapshot.json` and renders `dir/INVENTORY.md` (via `render_inventory.py`, prepending `NOTES.md`) | no — read-only |
| `pre-reboot.sh [--dry-run] [--all]` | graceful stop in reverse dependency order, by pid file / port / instance name with cwd+cmdline verification; never by name pattern | yes — stops services |
| `restore.sh [--dry-run] [--only G] [--skip G] [--proxy]` | starts everything in dependency order with health waits; idempotent (skips what already runs) | yes — starts services |
| `verify.sh [baseline-dir]` | fresh snapshot, diffed against the baseline; exit 1 on any regression | no — read-only |

Read `NOTES.md` first: it lists what needs an admin (sysctl persistence, the gateway unit), what
was found to be already broken, and the order of operations for a planned reboot. The 2026-09-10
baseline and copies of these scripts are in `/rag/backups/reboot-2026-09-10/`.

Host bootstrap for `ragstack-ctl` (ragops group, linger, `user@<uid>` drop-in, sysctl, the gateway unit above, ctl dirs) is automated in [`ops/ansible/`](../ansible/README.md); NOTES.md admin items 1–3 map to its `root` tag.
