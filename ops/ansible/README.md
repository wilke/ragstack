# ops/ansible — host bootstrap for ragstack-ctl

Host preparation only. These playbooks replace the two manual checklists (the
root items in `ops/coconut/NOTES.md` and the wilke items in plan v3 "Rollout")
and double as the installer for a migration target or a second site. They are
**not** called by `ragstack-ctl` (a static binary with no shell or Python
dependency) and never touch tenant lifecycle or gateway generation — those stay
in the ctl. `ragstack-ctl doctor` verifies the same facts, so playbook/reality
drift shows on the dashboard.

## Control node

Ansible is **not installed on coconut**. Either run from a laptop with
`ansible-core >= 2.15`, or make a dedicated venv on the box — not the ragstack
conda env:

```bash
/rag/envs/ragstack/bin/python -m venv /rag/tools/ansible-venv    # /usr/bin/python3 lacks ensurepip here
/rag/tools/ansible-venv/bin/pip install 'ansible-core>=2.15,<2.19'
export PATH=/rag/tools/ansible-venv/bin:$PATH
cd ~/Development/ragstack/ops/ansible          # ansible.cfg is only read from the cwd
```

No collections are needed (sysctl is a template + `sysctl --system`, systemd
via `ansible.builtin.systemd_service`). `inventory/coconut.yml` pins
`ansible_python_interpreter: /usr/bin/python3` because modules that become
`svcbvbrc` must run a Python that account can read — the control-node venv
under `$HOME` (NFS, mode 700) is not.

## The `requiretty` caveat

`sudo` on coconut has `requiretty`: `sudo -n` from a non-tty fails with "you
must have a tty". Two ways around it:

1. The admin adds `Defaults:<admin> !requiretty` to sudoers (documented
   prerequisite, not managed here); then `ansible-playbook … -K` just works.
2. Run local plays under a pseudo-tty:
   `script -qc 'ansible-playbook -i inventory/coconut.yml site.yml --tags root -K' /dev/null`
   from an interactive shell.

The `ctl` tag has the same problem for its `become_user: svcbvbrc` tasks
(`sudo -u svcbvbrc`, covered by wilke's `(svcbvbrc) NOPASSWD: ALL` rule — no
`-K`, but still a tty): use (2) for it, or `!requiretty`.

## Tag matrix

| tag | play | runs as | needs | what |
|---|---|---|---|---|
| `root` | coconut-host | root (`become`, `-K`) | tty (above) | `ragops` group + members, linger, `user@<uid>` drop-in, sysctl.d, proxy unit install/enable (+ optional cutover) |
| `hardening` | hardening | root (+ `become_user: p3` for `/rag`) | `hardening_enabled=true`; real run also `-e hardening_confirmed=true` | `g-w` on bin/config/proxy scripts/images/SIFs, `-R g-w,o-w` on `/rag/envs`, `/rag` to 0755 |
| `ctl` | ragstack-ctl | wilke; `become_user: svcbvbrc` for ctl-owned files | fresh login after `ragops` enrollment; tty for `sudo -u` | ctl dirs, node, bare mirror, ragops group pass, binary, `ctl.env`/secrets/unit, enable, doctor |
| `wilke` | subset of `ctl` | wilke, **no become at all** | fresh login with `ragops` | node toolchain, bare mirror, group pass on wilke-owned trees |
| `tenant-host` | `tenant-host.yml` | root then wilke | `-i inventory/<site>.yml` | coconut-host + ragstack-ctl minus adoption, plus apptainer/SIF/conda checks |

`ansible_become` is never global: each play opts in.

## Commands

Always preview first. Check mode never mutates; the few `check_mode: false`
tasks are read-only probes (`find … -print -quit`, `systemctl … is-*`,
`crontab -l`, `proxy.sh status`).

```bash
./check.sh                       # YAML parse + template render + --syntax-check (no host access)

# 1. root items (before PR-B) — as the admin, from a tty
ansible-playbook -i inventory/coconut.yml site.yml --check --diff --tags root -K
ansible-playbook -i inventory/coconut.yml site.yml --tags root -K
# proxy cutover (interactive nginx -> systemd unit), same play, when ready:
ansible-playbook -i inventory/coconut.yml site.yml --tags root -K -e proxy_switch_now=true

# 2. log out and back in (ragops is a new supplementary group), then as wilke:
ansible-playbook -i inventory/coconut.yml site.yml --check --diff --tags ctl
ansible-playbook -i inventory/coconut.yml site.yml --tags ctl \
    -e ctl_bin_src=$HOME/Development/ragstack/go/ragstack-ctl -e ctl_version=$(…/ragstack-ctl version) \
    -e @vault.yml --ask-vault-pass

# 3. hardening (open decision; before PR-D), root:
ansible-playbook -i inventory/coconut.yml site.yml --check --diff --tags hardening -K -e hardening_enabled=true
ansible-playbook -i inventory/coconut.yml site.yml --tags hardening -K -e hardening_enabled=true -e hardening_confirmed=true

# second site / migration target
ansible-playbook -i inventory/<site>.yml tenant-host.yml --check --diff -K
```

`check.sh <tag> [args]` wraps the preview: `./check.sh root -K`, `./check.sh ctl`.

### What each tag needs

- **root**: `become` (root). Prerequisites it only *documents* (final debug
  task): `Defaults:<admin> !requiretty`; wilke's `(svcbvbrc) NOPASSWD: ALL`
  rule (confirm with `sudo -l` from a tty); the coconut-proxy tree deployed to
  `/rag/config/proxy` (`deploy.sh`) so the unit can be copied.
- **wilke**: run as wilke with `ragops` in `id -Gn` (set at login — the role
  fails with a clear message otherwise; in check mode it only warns).
- **ctl**: everything in `wilke`, plus `sudo -u svcbvbrc` (tty), plus the
  `svcbvbrc` user manager running (linger + drop-in from `root`). Binary, unit
  enable and `doctor` are skipped while `ctl_bin_src` is undefined; secrets
  are skipped while the vault vars are undefined.
- **hardening**: `become`; the `/rag` chmod runs as `p3` (the operator is in
  `seed-admins-p3`). A real run needs `-e hardening_confirmed=true`.

### Order relative to the PRs

| when | what | tag |
|---|---|---|
| PR-A (this) | roles + inventory + `--check --diff` run recorded | — |
| before PR-B | linger, drop-in, proxy unit + crontab cutover, sysctl, sudoers confirmation | `root` (+ `proxy_switch_now=true`) |
| before PR-D | `ragops` group + members (also `root`), hardening (gated), ctl dirs, node, mirror, group pass, binary + unit | `root`, `hardening`, `ctl` |

The cutover in `root` is an **ordered task block**, not a handler chain:
handlers only fire on change, and the cutover must be re-runnable when the unit
was installed by an earlier run. Order: remove the `@reboot … start-proxy.sh`
line from svcbvbrc's crontab (a `shell` filter — the entry has no Ansible name,
so `cron: state=absent` cannot address it) → `proxy.sh stop` as the master's
owner (from `proxy.sh status`; `proxy.sh` refuses to signal another account's
pid) → `systemctl start coconut-proxy` → `GET :9000/health` with retries.

## Check-mode limitations (what the preview cannot diff)

- `template`/`copy` refuse a parent directory that only exists after a real
  run. Those tasks are skipped in check mode and the rendered content is
  printed by a `debug` task instead: the `user@<uid>` drop-in, `ctl.env`, the
  user unit. Same for `get_url`/`unarchive` (node) and the `current` symlink.
- `user: groups: ragops` cannot preview against a missing group; the preview
  prints the members it would add. `ctl` ends its preview early
  (`meta: end_host`) while `ragops` does not exist, because every later file
  task names that group.
- Recursive permission passes (`chgrp/chmod -R`) are `command`/`shell`
  tasks, skipped in check mode; each is preceded by a read-only
  `find … -print -quit` pre-check that drives `changed_when` and, in check
  mode, a `debug` line naming the tree and its first offender.

## Becoming `svcbvbrc` from wilke without root

`setfacl` is not on this host, so Ansible cannot hand its temp files to an
unprivileged `become_user` the usual way. The ctl play therefore sets
`ansible_remote_tmp` to `/rag/tmp/ansible-ctl` (2770 wilke:ragops, created by
its first task) and enables `ansible_shell_allow_world_readable_temp` — the
"world-readable" files are inside a directory only `ragops` can traverse.
Pipelining is on, so `command`/`stat`/`getent` tasks need no temp files at
all. The vault-rendered `ctl-secrets.env` passes through that directory for
the duration of one task and is written 0600.

## `ctl.env`

`roles/ragstack-ctl/templates/ctl.env.j2` emits exactly the non-secret keys
of `api.EnvKeys()` (`go/internal/ctl/api/env.go`) minus `api.SecretEnvKeys()`
minus `CTL_IDENTITY_KEY_FETCH_FILE` (a test-only override the conformance
runner sets directly; it must never appear in a template): `CTL_LISTEN`,
`CTL_RAG_ROOT`, `CTL_STATE_DIR`, `CTL_CONFIG_DIR`, `CTL_REGISTRY`,
`CTL_ADMIN_SUBJECTS`, `CTL_VIEWER_SUBJECTS`, `CTL_IDENTITY_ISSUER_ALLOWLIST`,
`CTL_LOG_LEVEL`, `CTL_LOG_FORMAT`, `CTL_RATE_LIMIT_PER_CREDENTIAL`,
`CTL_RATE_LIMIT_TARPIT_AT`, `CTL_EXTERNAL_STORE_PORTS`. It also carries three
process-environment variables the unit needs that are not part of the
`CTL_*` contract: `APPTAINER_CACHEDIR`/`APPTAINER_CONFIGDIR` (under
`/rag/data/ctl/apptainer`) and `XDG_RUNTIME_DIR=/run/user/<uid>`.
`CTL_GATEWAY_DENY_REMOTE_ADDRS` is deliberately omitted: the public mount is
required. `go/internal/ctl/api/env_template_test.go` reconciles this
template (and `ctl-secrets.env.j2`) against `env.go` directly — a key added
to one without the other fails that test. Secrets (`CTL_API_KEYS`,
`CTL_API_KEY_ROLES`, `CTL_API_KEY_NAMES` — exactly `api.SecretEnvKeys()`)
come from `vault.yml` (see `vault.example.yml`; `vault.yml` is gitignored)
and land in `ctl-secrets.env` 0600 with `no_log`. `CTL_ADMIN_SUBJECTS` lives
in `ctl.env`, not secrets: a bearer subject is a user name, not a
credential.

Two notes on the group pass: `chmod -R g+rwX` on `/rag/data/tenants` also
makes the legacy `tenant.env` files (which still carry secrets until `env
normalize`) group-readable — the group is `{wilke, svcbvbrc}` by design;
`secrets.env` files are set back to 0640. And `ctl_bin_owner` defaults to
`wilke` (`group_vars/all.yml`, single definition): the binary is installed
by the operator as `wilke`, mode 0755, executable by `svcbvbrc` via
`ragops`/world exec bits — no `become_user: svcbvbrc` for the copy or
symlink. This matters because hardening removes group-write from `/rag/bin`
(0755, owner-only write), so only `wilke` can write there; the symlink
`/rag/bin/ragstack-ctl` is installed the same way. `doctor` only requires
the binary not be group- or other-writable.

## What this replaces

| former checklist item | source | task (role) |
|---|---|---|
| `echo 'vm.max_map_count=262144' \| sudo tee /etc/sysctl.d/…` (+ overcommit) | NOTES.md admin item 1 | "Persist kernel settings in /etc/sysctl.d/90-ragstack.conf" + "Apply sysctl now if the live value is below the target" (coconut-host) |
| `sudo cp coconut-proxy.service /etc/systemd/system && daemon-reload && enable --now` | NOTES.md admin item 2, proxy README | "Install /etc/systemd/system/coconut-proxy.service", "Enable coconut-proxy — only when this run cuts over" (`enabled` is gated on `proxy_switch_now`, so the unit is never enabled while the `@reboot` crontab line still owns boot start), cutover block "Start coconut-proxy under systemd" (coconut-host) |
| remove the `@reboot … start-proxy.sh` crontab line; `proxy.sh stop` — don't leave two masters | BOOT.md / proxy README | "Remove the @reboot start-proxy.sh line from svcbvbrc's crontab", "Stop the interactively started master" (coconut-host, `proxy_switch_now`) |
| `loginctl enable-linger` | NOTES.md admin item 3 (svcbvbrc, not wilke) | "loginctl enable-linger svcbvbrc" (coconut-host) |
| `user@<uid>` drop-in: `RequiresMountsFor=/rag`, `SYSTEMD_UNIT_PATH` | plan "Supervision" | "Drop-in user@<uid>.service.d/ragstack.conf" + handler "systemd daemon-reload" + task "Restart user@<uid>.service (drop-in changed, groups changed, or SYSTEMD_UNIT_PATH missing)" (coconut-host) |
| `groupadd ragops; usermod -aG ragops wilke svcbvbrc` | plan "Host layout" | "Group ragops exists", "Enroll members in ragops" (coconut-host) |
| confirm `(svcbvbrc) NOPASSWD: ALL`, `Defaults !requiretty` | plan "Root items" | "Manual prerequisites (not managed here)" — debug only (coconut-host) |
| `chmod g-w` bin/config/proxy scripts/images/SIFs; `-R` on envs; `/rag` 0755 as p3 | plan "Group + hardening" | "chmod g-w on …", "chmod g-w on glob matches", "chmod -R g-w,o-w on the conda envs", "/rag to 0755 (as p3)" (hardening) |
| `mkdir /rag/data/ctl/{…} /rag/config/ctl/{units,templates}` with modes | plan "Host layout" | "/rag/data/ctl (2770) and subdirs", "/rag/config/ctl (2750) and subdirs", "backup-recipients.txt exists" (ragstack-ctl) |
| node tarball under `/rag/tools/node/<ver>` | plan "wilke items" | "Download node tarball (checksum verified)", "Unpack into …", "current -> <ver>" (ragstack-ctl) |
| `git clone --bare --mirror`, `core.sharedRepository=group` | plan "wilke items" | "git clone --bare --mirror …", "core.sharedRepository=group" (ragstack-ctl) |
| `chgrp -R ragops` + `g+rwX` + setgid on tenants/repos/backups/`data/ctl` (**not** `config/ctl` — `g+rwX` would widen `ctl-secrets.env` to 0660); `secrets.env` 0640 | plan "wilke items" | "Group pass — chgrp -Rh ragops, chmod -R g+rwX, setgid on dirs", "Group pass — secrets.env to 0640" (ragstack-ctl) |
| `install -m 0755 → /rag/bin/ragstack-ctl-<ver>; ln -sfn` | plan `install-ctl` | "/rag/bin/ragstack-ctl-<ver>", "/rag/bin/ragstack-ctl -> …" (ragstack-ctl) |
| `ctl.env`, `ctl-secrets.env` | plan "Host layout" | "ctl.env (0640)", "ctl-secrets.env (0600)" + "ctl-secrets.env is 0600 and ctl.env 0640 (nothing widened them)" assert (ragstack-ctl) |
| `systemctl --user enable --now ragstack-ctl.service` with `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS` | plan "Supervision" | "User unit …/units/ragstack-ctl.service", "systemctl --user enable --now ragstack-ctl.service", "…restart…", "ragstack-ctl.service is active" assert (an unmet `[Unit] Condition*` makes `enable --now` exit 0 without starting anything) (ragstack-ctl) |
| restart the user manager after enrollment | plan "Host layout" | task "Restart user@<uid>.service (drop-in changed, groups changed, or SYSTEMD_UNIT_PATH missing)" (root — a pre-checked task, not a handler, so a re-run repairs a manager whose environment is stale) + "systemctl --user daemon-reexec after unit/env changes" + "Root drop-in is effective" assert (ragstack-ctl) |
| `ragstack-ctl doctor` must not be red | plan "wilke items" | "ragstack-ctl doctor --json (fails on red)" (ragstack-ctl) |
| `apptainer/pull.sh` for qdrant/elasticsearch; conda env; sysctl | plan `tenant-host` | "Pull store images", "Conda env /rag/envs/ragstack exists" (tenant-host) + coconut-host sysctl |

## Files

```
ansible.cfg              inventory path, auto_silent interpreter, pipelining, no .retry, YAML result format
inventory/coconut.yml    coconut, ansible_connection=local, system python
group_vars/all.yml       rag_root, ctl_user, ragops, node_*, proxy_*, ctl_*, hardening_*, sysctl_settings, images
site.yml                 facts (getent -> ctl_uid) + the three tagged plays
tenant-host.yml          second-site playbook reusing the roles
vault.example.yml        shape of the (gitignored, ansible-vault encrypted) vault.yml
check.sh                 offline validation (+ syntax-check / check-mode when ansible is on PATH)
roles/coconut-host       root items; templates: user-dropin.conf.j2, sysctl.conf.j2
roles/hardening          gated permission tightening
roles/ragstack-ctl       wilke/svcbvbrc items; templates: ctl.env.j2, ctl-secrets.env.j2, ragstack-ctl.service.j2
roles/tenant-host        apptainer / SIF / conda checks
```
