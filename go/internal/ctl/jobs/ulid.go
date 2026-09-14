package jobs

import (
	"crypto/rand"
	"sync"
	"time"
)

// Job ids are ULIDs (job.json#/$defs/JobId: `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`):
// 48 bits of millisecond timestamp then 80 bits of entropy, rendered in
// Crockford base32. Two properties earn it over a UUID here: the id sorts by
// creation time (so `ORDER BY id` is `ORDER BY created_at` and a listing is
// stable without a second column), and it is 26 URL-safe characters an
// operator can read back over the phone.
//
// This is ~40 lines, so it is spelled out rather than pulled in as a
// dependency — the ctl binary's module graph is a thing we keep small on
// purpose.

// crockford is the base32 alphabet with I, L, O and U removed (they are the
// characters a human confuses with 1, 1, 0 and V).
const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// ulidGen mints ids. Within one millisecond it increments the entropy of the
// previous id instead of drawing fresh bytes, so ids minted in a tight loop
// (a test, a burst of submissions) still sort in submission order.
type ulidGen struct {
	mu   sync.Mutex
	last [16]byte
	ms   uint64
}

func (g *ulidGen) new(t time.Time) string {
	ms := uint64(t.UTC().UnixMilli())
	g.mu.Lock()
	defer g.mu.Unlock()
	var b [16]byte
	if ms == g.ms && g.ms != 0 {
		b = g.last
		// Increment the 80-bit entropy, big-endian.
		for i := 15; i >= 6; i-- {
			b[i]++
			if b[i] != 0 {
				break
			}
		}
	} else {
		for i := 0; i < 6; i++ {
			b[i] = byte(ms >> (40 - 8*uint(i)))
		}
		if _, err := rand.Read(b[6:]); err != nil {
			// crypto/rand does not fail on Linux; if it ever does, a
			// timestamp-only id is still unique enough to be recorded
			// against, and the caller gets no error path it cannot use.
			for i := 6; i < 16; i++ {
				b[i] = byte(ms >> (8 * uint(i%8)))
			}
		}
		g.ms = ms
	}
	g.last = b
	return encodeULID(b)
}

// encodeULID renders 128 bits as 26 Crockford characters. The 26 characters
// carry 130 bits, so the value is read as if prefixed by two zero bits —
// which is exactly why the first character is always 0-7.
func encodeULID(b [16]byte) string {
	bit := func(i int) byte {
		if i < 2 {
			return 0
		}
		j := i - 2
		return (b[j/8] >> (7 - uint(j%8))) & 1
	}
	out := make([]byte, 26)
	for c := 0; c < 26; c++ {
		var v byte
		for k := 0; k < 5; k++ {
			v = v<<1 | bit(c*5+k)
		}
		out[c] = crockford[v]
	}
	return string(out)
}
