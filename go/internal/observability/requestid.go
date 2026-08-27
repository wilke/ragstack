package observability

// Request-id generation and propagation — the Go half of #427's correlation id.
//
// # Why not chi's middleware.RequestID
//
// chi ships a RequestID middleware and the router used to install it. It cannot
// be the source of this header, for two independent reasons:
//
//  1. Its format is "<hostname>/<base64[:10]>-<counter>", which can never match
//     the `^[0-9a-f]{16}$` pattern the contract pins at
//     `components/headers/XRequestId`, nor the same pattern the conformance
//     suite asserts over the wire.
//  2. It honours an inbound X-Request-Id **verbatim**. That is the trust-the-
//     caller option the #427 plan explicitly rejected: a client could forge an
//     id, replay one, or make two concurrent requests indistinguishable in the
//     log — which is precisely the property the id exists to provide.
//
// Nothing in the tree ever read chi's id (`middleware.GetReqID` had zero call
// sites), so it was removed rather than left as a trap for the next reader.
//
// # The rule, identical to the Python implementation
//
// Always generate our own id. If an inbound X-Request-ID matches the charset
// and length rule below, record it separately as `upstream_rid` for gateway
// correlation — and never echo it. See
// `python/ragstack/observability/middleware.py` for the same three sentences on
// the other side of the contract.

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"net/http"
	"regexp"
	"sync/atomic"
	"time"
)

// HeaderRequestID is the header we read (case-insensitively, via the canonical
// form Go normalises to) and the header we write. Same name; never the same
// value — see upstreamRequestID.
const HeaderRequestID = "X-Request-Id"

type ctxKey int

const (
	ridKey ctxKey = iota
	upstreamRIDKey
)

// upstreamRE accepts an inbound id for RECORDING only. The charset cap is the
// log-injection guard: a newline in the header would otherwise let a caller
// forge whole log lines, and a multi-kilobyte header would let them flood the
// file. Length is bounded at 64 to match the documented schema.
//
// Anchored with \A…\z rather than ^…$ deliberately. Go's RE2 treats ^ and $ as
// text boundaries by default so the two are equivalent here, but the Python
// side needed `fullmatch` for exactly this reason (Python's `$` matches BEFORE a
// trailing newline, admitting the one character the guard exists to exclude),
// and spelling the intent out keeps the two implementations readable as the
// same rule.
var upstreamRE = regexp.MustCompile(`\A[A-Za-z0-9._-]{1,64}\z`)

// fallbackCounter backs the impossible-but-not-unhandled branch in NewRequestID.
var fallbackCounter atomic.Uint64

// NewRequestID returns a fresh id: 16 lowercase hex characters, 64 bits from
// crypto/rand.
//
// Short enough that a user can read it off a screenshot and an operator can
// retype it; wide enough that a collision inside one log-retention window is not
// a practical concern. The width and alphabet are fixed by the contract, so this
// must return 16 hex characters on every path, including the failure one.
func NewRequestID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		// crypto/rand failing is close to impossible, but returning "" or a
		// short value here would break the contract's pattern and — worse —
		// make two requests indistinguishable, which is the single property
		// this whole mechanism exists to provide. A clock-plus-counter id is
		// not unguessable, but it is still 16 hex characters and still unique.
		binary.BigEndian.PutUint64(b[:], uint64(time.Now().UnixNano())^fallbackCounter.Add(1))
	}
	return hex.EncodeToString(b[:])
}

// upstreamRequestID returns a caller-supplied X-Request-ID if it is safe to
// record, and "" otherwise. Never returned as the request id.
func upstreamRequestID(r *http.Request) string {
	v := r.Header.Get(HeaderRequestID)
	if v == "" || !upstreamRE.MatchString(v) {
		return ""
	}
	return v
}

// RequestIDMiddleware generates the request id, stamps it on the response and
// puts it (plus any validated upstream id) in the request context.
//
// The header is set BEFORE the rest of the chain runs. That one ordering choice
// is what makes "X-Request-Id on EVERY response" true in Go without the
// application-level exception handler the Python side needs: nothing downstream
// clears the header map, so the id survives onto chi's 404, onto Recoverer's
// panic-500, and onto every handler-written response alike.
//
// Install it FIRST (chi runs middlewares in registration order, outermost
// first), so everything below it — including the logging middleware — can read
// the id.
func RequestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rid := NewRequestID()
		w.Header().Set(HeaderRequestID, rid)

		ctx := context.WithValue(r.Context(), ridKey, rid)
		if upstream := upstreamRequestID(r); upstream != "" {
			ctx = context.WithValue(ctx, upstreamRIDKey, upstream)
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// RequestIDFromContext returns the server-generated id for the request being
// served, or "" outside one.
//
// "" is the normal state for a handler invoked directly in a unit test or from
// a CLI, so every caller must tolerate it rather than assume a request is in
// flight.
func RequestIDFromContext(ctx context.Context) string {
	rid, _ := ctx.Value(ridKey).(string)
	return rid
}

// UpstreamRequestIDFromContext returns the validated caller-supplied id, or ""
// when none was sent or it failed validation. It is for logging only and must
// never reach a response.
func UpstreamRequestIDFromContext(ctx context.Context) string {
	rid, _ := ctx.Value(upstreamRIDKey).(string)
	return rid
}
