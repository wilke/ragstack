#!/usr/bin/env bash
# ops/coconut/ctl-daemon.sh — start/stop the ragstack-ctl daemon WITHOUT systemd.
#
#   ops/coconut/ctl-daemon.sh start|stop|status|restart
#
# The supervised deployment is the user unit (ops/systemd/ragstack-ctl.service,
# rendered by the ansible ragstack-ctl role). That unit needs the root items
# from the coconut-host role — linger for the service account and the
# `user@<uid>` drop-in that sets SYSTEMD_UNIT_PATH — and those are NOT done
# yet. Until they are, this script runs the same binary in the same environment
# the unit would, so PR-B has a daemon to serve the dashboard from.
#
# It is deliberately the unit's shape, not a second design. Each line of
# ragstack-ctl.service and what stands in for it here:
#
#   EnvironmentFile=…/ctl.env + -ctl-secrets.env  ->  load_env (PARSED, not
#                                                     sourced — see below)
#   Environment=HOME=/rag/data/ctl/home           ->  HOME=…, exported
#   WorkingDirectory=/rag/data/ctl                ->  cd "$STATE_DIR"
#   UMask=0002                                    ->  umask 0002
#   StandardOutput=append:…/ctl.log               ->  >> "$LOG", created 0640
#   ExecStart=/rag/bin/ragstack-ctl serve         ->  setsid --fork … serve
#   Restart=always                                ->  NOT reproduced; this is a
#                                                     detached foreground
#                                                     process, and a crash is
#                                                     meant to be noticed.
#
# Those five settings are not decoration. A daemon started here with the
# invoking user's umask, HOME and working directory writes state the unit-run
# daemon then cannot read: 0002 vs 0022 decides whether the ctl group can write
# /rag/data/ctl, and HOME decides where apptainer puts its cache. "It works
# under the script and fails under the unit" is the bug this section prevents.
#
# Nothing here uses pkill: `stop` reads the pidfile and verifies the process is
# THIS binary running `serve` before signalling. A pid file is a file — the pid
# in it can have been reused, and "kill everything whose name looks like mine"
# is how an unrelated process gets killed on a shared host.
set -euo pipefail

RAG_ROOT=${RAG_ROOT:-/rag}
CTL_BIN=${CTL_BIN:-$RAG_ROOT/bin/ragstack-ctl}
STATE_DIR=${CTL_STATE_DIR:-$RAG_ROOT/data/ctl}
CONFIG_DIR=${CTL_CONFIG_DIR:-$RAG_ROOT/config/ctl}
PIDFILE=${CTL_PIDFILE:-$STATE_DIR/ctl.pid}
LOG=${CTL_LOG:-$STATE_DIR/ctl.log}
LISTEN_DEFAULT=127.0.0.1:23990

# The unit's UMask=0002: group-writable state, which is what makes
# /rag/data/ctl usable by the ctl group rather than by one account.
umask 0002

# load_env reads ctl.env and, when present, ctl-secrets.env.
#
# It PARSES them; it does not source them. systemd's EnvironmentFile is not a
# shell: it reads KEY=VALUE lines, strips one matching pair of quotes, and
# never expands anything. `set -a; . file` is a shell, so `KEY=$(curl …)` in a
# file this script reads would EXECUTE — from a file whose whole point is to be
# edited by an operator, and which this script reads before dropping any
# privilege it has. Worse, the two would disagree: a value with a `$` in it
# (an API key can contain one) arrives intact under systemd and mangled or
# empty under a sourcing script, so the daemon behaves differently depending on
# how it was started.
#
# NOTHING from these files is ever echoed: ctl-secrets.env holds the API keys,
# and a script that printed its environment would put them in a terminal, a log
# and a scrollback buffer at once. A malformed line is reported by NUMBER only.
load_env() {
    local file line key val n
    for file in "$CONFIG_DIR/ctl.env" "$CONFIG_DIR/ctl-secrets.env"; do
        [[ -r $file ]] || continue
        n=0
        while IFS= read -r line || [[ -n $line ]]; do
            n=$((n + 1))
            # Blank lines and comments, as systemd accepts them.
            [[ -z ${line//[[:space:]]/} ]] && continue
            [[ ${line#"${line%%[![:space:]]*}"} == \#* ]] && continue
            if [[ ! $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
                echo "$0: $file line $n is not KEY=VALUE; refusing to start" >&2
                exit 1
            fi
            key=${line%%=*}
            val=${line#*=}
            # One matching pair of surrounding quotes comes off, the way
            # systemd does it. Everything inside stays literal.
            if [[ ${#val} -ge 2 && ${val:0:1} == "'" && ${val: -1} == "'" ]]; then
                val=${val:1:${#val}-2}
            elif [[ ${#val} -ge 2 && ${val:0:1} == '"' && ${val: -1} == '"' ]]; then
                val=${val:1:${#val}-2}
            fi
            export "$key=$val"
        done <"$file"
    done
}

listen() { echo "${CTL_LISTEN:-$LISTEN_DEFAULT}"; }

# running: true iff the pidfile names a live process that IS this daemon.
#
# Two facts, both required:
#
#   argv[1] == "serve"   — the ctl is one binary with many verbs, and an
#                          in-flight `ragstack-ctl gateway apply` (run through
#                          sudo, through `script`, from a runbook) has
#                          "ragstack-ctl" in its command line too. The old test
#                          was `grep -q ragstack-ctl` over the whole cmdline,
#                          so `stop` would have SIGTERMed a publish mid-switch
#                          if the pidfile happened to name it.
#   the executable        — the strong half. /proc/<pid>/exe needs the same uid
#                          (or ptrace privilege), so for a daemon owned by
#                          another account it is unreadable; there the check
#                          falls back to argv[0]'s basename, which is still far
#                          tighter than the old substring match.
running() {
    local pid exe want argv0 argv1
    local -a args
    [[ -f $PIDFILE ]] || return 1
    pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [[ -n $pid && $pid =~ ^[0-9]+$ && -d /proc/$pid ]] || return 1
    mapfile -d '' -t args <"/proc/$pid/cmdline" 2>/dev/null || return 1
    argv0=${args[0]-}
    argv1=${args[1]-}
    [[ $argv1 == serve ]] || return 1
    exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
    want=$(readlink -f "$CTL_BIN" 2>/dev/null || true)
    if [[ -n $exe && -n $want ]]; then
        [[ $exe == "$want" ]]
    else
        [[ $(basename -- "$argv0") == "$(basename -- "$CTL_BIN")" ]]
    fi
}

pid_of() { cat "$PIDFILE" 2>/dev/null || echo ""; }
owner_of() { stat -c %U "/proc/$(pid_of)" 2>/dev/null || echo "?"; }

case "${1:-status}" in
  start)
    if running; then echo "already running (pid $(pid_of))"; exit 0; fi
    [[ -x $CTL_BIN ]] || { echo "$0: $CTL_BIN is not executable" >&2; exit 1; }
    mkdir -p "$STATE_DIR"
    load_env
    # The unit's Environment=HOME and WorkingDirectory. HOME decides where
    # apptainer puts its cache; the working directory decides what a relative
    # path in a config file resolves against.
    export HOME=${CTL_HOME:-$STATE_DIR/home}
    mkdir -p "$HOME"
    cd "$STATE_DIR"
    # The log is created with an explicit mode before the first append, so it
    # is not left at whatever the umask produced on the very first start (and
    # the daemon's log lines can name a tenant, a path and a subject).
    [[ -e $LOG ]] || install -m 0640 /dev/null "$LOG"

    # A STALE pidfile must not be mistaken for this start's: the daemon writes
    # the file itself once it is listening (`serve --pidfile`), so its absence
    # is the "not up yet" state and its presence is the "up" one.
    rm -f "$PIDFILE"

    # setsid --fork, and the daemon writes its own pid.
    #
    # The old form was `setsid nohup … & echo $! >"$PIDFILE"`. Under job
    # control `$!` is the setsid PARENT, which exits immediately after forking
    # the daemon — so the pidfile named a process that was already gone,
    # `running` said false, start reported FAILED, deleted the pidfile, and
    # left a healthy daemon nobody could stop with this script. Only the
    # process itself knows its pid; --pidfile makes it write it, after the bind.
    setsid --fork "$CTL_BIN" serve --pidfile "$PIDFILE" </dev/null >>"$LOG" 2>&1

    # Up to 10s: bind, load the registry, answer /health.
    for _ in $(seq 40); do
        if running && curl -fsS --max-time 1 "http://$(listen)/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
    if running; then
        echo "started (pid $(pid_of)), listening on $(listen), log $LOG"
    else
        echo "FAILED to start — see $LOG" >&2
        tail -20 "$LOG" 2>/dev/null || true
        rm -f "$PIDFILE"
        exit 1
    fi
    ;;

  stop)
    if ! running; then
        echo "not running"
        rm -f "$PIDFILE"
        exit 0
    fi
    pid=$(pid_of)
    if ! kill -TERM "$pid" 2>/dev/null; then
        echo "cannot signal pid $pid — it belongs to $(owner_of); run this as that account" >&2
        exit 1
    fi
    for _ in $(seq 40); do running || break; sleep 0.25; done
    if running; then
        echo "pid $pid did not stop on SIGTERM; leaving it alone rather than SIGKILLing a daemon mid-write" >&2
        exit 1
    fi
    # `serve` removes its own pidfile on an orderly exit; this covers the case
    # where it was killed harder than SIGTERM at some earlier point.
    rm -f "$PIDFILE"
    echo "stopped"
    ;;

  restart)
    "$0" stop
    "$0" start
    ;;

  status)
    if running; then
        echo "running (pid $(pid_of), owner $(owner_of), up $(ps -o etime= -p "$(pid_of)" 2>/dev/null | tr -d ' '))"
    else
        echo "not running"
    fi
    # The health endpoint is anonymous by contract ({status, version}), so this
    # needs no credential and prints nothing sensitive.
    load_env
    echo -n "health: "
    curl -s --max-time 2 "http://$(listen)/health" || echo "(no answer on $(listen))"
    echo
    ;;

  *)
    echo "usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
