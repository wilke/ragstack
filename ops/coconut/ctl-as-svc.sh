#!/usr/bin/env bash
# ops/coconut/ctl-as-svc.sh — run ragstack-ctl AS THE SERVICE ACCOUNT.
#
#   ops/coconut/ctl-as-svc.sh gateway status
#   ops/coconut/ctl-as-svc.sh gateway apply --expect-bodies <dir>
#
# The control plane owns files under /rag/data/ctl, /rag/config/ctl and the two
# gateway include symlinks, all of which belong to svcbvbrc. A human operator
# reaches them through this wrapper and nowhere else: the ctl itself has NO
# sudo in any code path (plan v3, "no sudo in any code path"), so the privilege
# boundary is this one script, auditable in one screen.
#
# Why `script -qec`: sudo on coconut is configured with `requiretty`, so
# `sudo -n -u svcbvbrc …` from a non-tty context (a pipeline, a CI step, an
# editor terminal that is not a real pty) fails with "sorry, you must have a
# tty to run sudo". `script` allocates one. -q keeps its own chatter out and
# /dev/null discards the typescript.
#
# -e (--return) is LOAD-BEARING. Without it `script` exits with its own status
# and the child's is thrown away, so every failure of the ctl reached the
# caller as SUCCESS. Verified on this host (util-linux 2.37.2):
#
#   script -qc  'exit 7' /dev/null; echo $?   ->  0
#   script -qec 'exit 7' /dev/null; echo $?   ->  7
#
# That silently broke every exit-code contract the ctl has: `gateway apply`
# returning 3 (refused) or 1 (failed) came out of this wrapper as 0, so a
# runbook step, a CI job or a `&&` chain carried on as if the publish had
# worked.
#
# What `script` DOES still change is the output: it runs the child on a pty, so
# every line comes back with a CR before its LF and any progress output is
# interleaved as a terminal would show it. Anything parsing this wrapper's
# stdout has to strip CR (`tr -d '\r'`) — `ragstack-ctl … --json` piped through
# here is still valid JSON, because a JSON parser treats CR as whitespace, but
# line-oriented `grep -x` / `read` comparisons will not match.
#
# Why the explicit env: a `sudo -u` session does NOT set up the target
# account's user-manager environment, and `systemctl --user` (units, linger,
# daemon-reload) needs XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS to find the
# per-user manager. Without them systemctl reports "Failed to connect to bus",
# which reads like a broken systemd rather than a missing variable.
#
# Arguments are passed through verbatim: each is wrapped in POSIX single quotes
# (see shquote), so a path with a space, a JSON argument with quotes, or a `;`
# in a value reaches the ctl as ONE argument and is never re-parsed as shell
# syntax.
set -euo pipefail

CTL_USER=${CTL_USER:-svcbvbrc}
CTL_BIN=${CTL_BIN:-/rag/bin/ragstack-ctl}

if [[ $# -eq 0 ]]; then
    cat >&2 <<EOF
usage: $0 <ragstack-ctl arguments…>

  $0 doctor
  $0 gateway status
  $0 gateway diff
  $0 gateway apply --dry-run
  $0 gateway apply --expect-bodies /rag/repos/ragstack/go/internal/ctl/testdata/live-2026-09-10/gateway

env: CTL_USER (default svcbvbrc), CTL_BIN (default /rag/bin/ragstack-ctl)
EOF
    exit 2
fi

uid=$(id -u "$CTL_USER" 2>/dev/null) || { echo "$0: no such account: $CTL_USER" >&2; exit 1; }

# shquote renders one argument as a POSIX single-quoted word.
#
# NOT `printf %q`: that emits bash's own `$'…'` form for anything with a
# control character or a non-ASCII byte, and the string built here is handed to
# `$SHELL -c` by `script`. On an account whose shell is dash (the default for a
# service account on many hosts) `$'…'` is not a quoting form at all — it reads
# as a literal `$` followed by a quoted string — so a tenant name with a
# non-ASCII character, or a --note with a tab in it, would arrive mangled.
# Single quotes with `'\''` for an embedded quote are POSIX, and mean the same
# thing in every shell.
shquote() { local s=${1//\'/\'\\\'\'}; printf "'%s'" "$s"; }

# Build the argument vector as a single properly quoted string. `sudo … env …
# ctl "$@"` cannot be used directly because the whole command runs inside
# `script -c`, which takes ONE string and hands it to a shell.
argv=""
for a in "$@"; do
    argv+=" $(shquote "$a")"
done

# -n: never prompt. A wrapper that can block on a password prompt inside a
# pseudo-tty is a wrapper that hangs a runbook step with no output.
cmd="sudo -n -u $(shquote "$CTL_USER") env \
XDG_RUNTIME_DIR=/run/user/${uid} \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus \
$(shquote "$CTL_BIN")${argv}"

# `bash -c` inside: `script` runs its argument with $SHELL, and $SHELL is the
# INVOKING account's. Naming the interpreter makes this wrapper behave the same
# whether it is run from bash, dash, zsh or a cron shell.
exec script -qec "bash -c $(shquote "$cmd")" /dev/null
