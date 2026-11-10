#!/usr/bin/env bash
# ops/coconut/qdrant-census.sh — what a qdrant holds, as stable JSON (READ-ONLY).
#
#   ./qdrant-census.sh <port-or-url>...
#
#   ./qdrant-census.sh 24041 > before.json      # a tenant's qdrant, by port
#   ./qdrant-census.sh http://127.0.0.1:6333    # the shared qdrant, by URL
#   diff before.json after.json                 # nothing printed = same store
#
# It is the verification primitive of docs/runbooks/qdrant-data-dir.md: run it
# before a store is moved and after it is started from the new directory, and
# diff the two. For every endpoint it prints, per collection: name, status,
# points_count (POST /collections/{c}/points/count {"exact":true} — the exact
# count, not the estimate GET /collections/{c} reports), segments_count, the
# vector config (dense and sparse) and the aliases pointing at it. Collections
# are sorted by name, aliases by name, object keys sorted (jq -S), so two
# censuses of the same store are byte-identical.
#
# Calls only GET /collections, GET /aliases, GET /collections/{c} and POST
# /collections/{c}/points/count, none of which writes. A bare number is
# http://127.0.0.1:<port>. Any HTTP error, timeout or unparsable answer is
# reported on stderr, recorded as "error" for that endpoint, and makes the exit
# status 1; a bad argument is 2. Sources nothing; needs curl and jq (CURL / JQ
# override the binaries). No API key is sent: the tenant and shared qdrants on
# coconut are unauthenticated on loopback.
set -uo pipefail

CURL=${CURL:-curl}
JQ=${JQ:-jq}
TIMEOUT=${CENSUS_TIMEOUT:-60}

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
case $1 in -h|--help) usage ;; esac

# base_of turns an argument into a base URL without a trailing slash.
base_of() {
  local a=$1
  if [[ $a =~ ^[0-9]{1,5}$ ]] && (( a >= 1 && a <= 65535 )); then
    printf 'http://127.0.0.1:%s' "$a"
  elif [[ $a =~ ^https?://[A-Za-z0-9._:-]+(/[A-Za-z0-9._/-]*)?$ ]]; then
    printf '%s' "${a%/}"
  else
    return 1
  fi
}

# req METHOD URL [BODY] — prints the response body; nonzero unless HTTP 200.
req() {
  local method=$1 url=$2 body=${3:-} out code
  local args=(-sS -m "$TIMEOUT" -X "$method" -w '\n%{http_code}')
  if [[ -n $body ]]; then
    args+=(-H 'Content-Type: application/json' --data "$body")
  fi
  if ! out=$("$CURL" "${args[@]}" "$url"); then
    echo "qdrant-census: $method $url: curl failed" >&2
    return 1
  fi
  code=${out##*$'\n'}
  out=${out%$'\n'*}
  if [[ $code != 200 ]]; then
    echo "qdrant-census: $method $url: HTTP $code" >&2
    return 1
  fi
  printf '%s' "$out"
}

# census BASE — prints one endpoint's JSON object; nonzero on any error.
census() {
  local base=$1 cols aliases names name enc info count row rows=()
  cols=$(req GET "$base/collections") || return 1
  names=$(printf '%s' "$cols" | "$JQ" -r '.result.collections[].name' 2>/dev/null) || {
    echo "qdrant-census: GET $base/collections: not a collection list" >&2; return 1; }
  aliases=$(req GET "$base/aliases") || return 1
  printf '%s' "$aliases" | "$JQ" -e '.result.aliases | type == "array"' >/dev/null 2>&1 || {
    echo "qdrant-census: GET $base/aliases: not an alias list" >&2; return 1; }
  while IFS= read -r name; do
    [[ -n $name ]] || continue
    enc=$("$JQ" -rn --arg n "$name" '$n | @uri')
    info=$(req GET "$base/collections/$enc") || return 1
    count=$(req POST "$base/collections/$enc/points/count" '{"exact":true}') || return 1
    row=$("$JQ" -cn --arg name "$name" --argjson info "$info" --argjson count "$count" \
      --argjson aliases "$aliases" '{
        name: $name,
        status: $info.result.status,
        points_count: $count.result.count,
        segments_count: $info.result.segments_count,
        vectors: ($info.result.config.params.vectors // null),
        sparse_vectors: ($info.result.config.params.sparse_vectors // null),
        aliases: ([$aliases.result.aliases[] | select(.collection_name == $name) | .alias_name] | sort)
      }' 2>/dev/null)
    if [[ $? -ne 0 || -z $row ]] || ! printf '%s' "$row" | "$JQ" -e '.points_count | type == "number"' >/dev/null 2>&1; then
      echo "qdrant-census: $base/collections/$name: unparsable answer" >&2
      return 1
    fi
    rows+=("$row")
  done <<< "$names"
  printf '%s\n' "${rows[@]}" | "$JQ" -cs --arg endpoint "$base" \
    '{endpoint: $endpoint, collections: (map(select(. != null)) | sort_by(.name))}'
}

rc=0
docs=()
for arg in "$@"; do
  if ! base=$(base_of "$arg"); then
    echo "qdrant-census: $arg is neither a port nor an http(s) URL" >&2
    exit 2
  fi
  if doc=$(census "$base"); then
    docs+=("$doc")
  else
    rc=1
    docs+=("$("$JQ" -cn --arg endpoint "$base" '{endpoint: $endpoint, error: "census failed (see stderr)"}')")
  fi
done
printf '%s\n' "${docs[@]}" | "$JQ" -S -s '.' || rc=1
exit "$rc"
