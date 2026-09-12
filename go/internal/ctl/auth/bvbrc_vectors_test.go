package auth

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// The Go half of the shared BV-BRC replay table. Python replays the same file
// (python/tests/unit/test_identity_bvbrc_vectors.py); if the two verifiers
// ever disagree about a vector, one of the two suites goes red rather than a
// tenant and the control plane quietly disagreeing about who a caller is.
//
// `reason_substring` in the fixture is the PYTHON error text and the README
// calls it advisory: the assertion that must hold is `error_kind`. This port
// keeps the reason strings identical anyway, so the substring is asserted too
// — a free drift alarm on the wording both implementations use in their logs.

const fixtureDir = "../../../../contracts/fixtures/identity/bvbrc"

type vectorFile struct {
	SigningSubjectURL string   `json:"signing_subject_url"`
	Allowlist         []string `json:"allowlist"`
	Now               int64    `json:"now"`
	Vectors           []struct {
		Name      string   `json:"name"`
		Token     string   `json:"token"`
		Allowlist []string `json:"allowlist"`
		Now       *int64   `json:"now"`
		Expect    struct {
			OK              bool   `json:"ok"`
			Subject         string `json:"subject"`
			TokenID         string `json:"token_id"`
			ExpiresAt       *int64 `json:"expires_at"`
			ErrorKind       string `json:"error_kind"`
			ReasonSubstring string `json:"reason_substring"`
		} `json:"expect"`
	} `json:"vectors"`
	KeyServerVariants []struct {
		Name          string `json:"name"`
		Body          string `json:"body"`
		ExpectParseOK bool   `json:"expect_parse_ok"`
	} `json:"key_server_variants"`
}

func loadVectors(t *testing.T) vectorFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixtureDir, "vectors.json"))
	if err != nil {
		t.Fatalf("read vectors.json: %v", err)
	}
	var vf vectorFile
	if err := json.Unmarshal(raw, &vf); err != nil {
		t.Fatalf("parse vectors.json: %v", err)
	}
	if len(vf.Vectors) == 0 {
		t.Fatal("vectors.json carries no vectors")
	}
	return vf
}

func publicKeyBody(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixtureDir, "public_key.json"))
	if err != nil {
		t.Fatalf("read public_key.json: %v", err)
	}
	return raw
}

// fakeFetch serves one body at every allowlisted URL and counts the calls —
// the cache, rotation and refetch-rate-limit tests are all assertions about
// that count.
type fakeFetch struct {
	mu    sync.Mutex
	body  []byte
	err   error
	calls int
	urls  []string
}

func (f *fakeFetch) fetch(_ context.Context, url string) ([]byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	f.urls = append(f.urls, url)
	if f.err != nil {
		return nil, f.err
	}
	return f.body, nil
}

func (f *fakeFetch) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

func TestVectorsReplay(t *testing.T) {
	vf := loadVectors(t)
	body := publicKeyBody(t)

	for _, vec := range vf.Vectors {
		vec := vec
		t.Run(vec.Name, func(t *testing.T) {
			allowlist := vf.Allowlist
			if vec.Allowlist != nil {
				allowlist = vec.Allowlist
			}
			nowUnix := vf.Now
			if vec.Now != nil {
				nowUnix = *vec.Now
			}
			ff := &fakeFetch{body: body}
			v, err := New(Options{
				Allowlist: allowlist,
				Now:       func() time.Time { return time.Unix(nowUnix, 0).UTC() },
				Fetch:     ff.fetch,
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			id, err := v.Authenticate(context.Background(), vec.Token)
			if vec.Expect.OK {
				if err != nil {
					t.Fatalf("expected success, got %v", err)
				}
				if id.Subject != vec.Expect.Subject {
					t.Errorf("subject = %q, want %q", id.Subject, vec.Expect.Subject)
				}
				if id.TokenID != vec.Expect.TokenID {
					t.Errorf("token_id = %q, want %q", id.TokenID, vec.Expect.TokenID)
				}
				if id.Issuer != Issuer {
					t.Errorf("issuer = %q, want %q", id.Issuer, Issuer)
				}
				if id.DisplayName != vec.Expect.Subject {
					t.Errorf("display_name = %q, want %q", id.DisplayName, vec.Expect.Subject)
				}
				switch {
				case vec.Expect.ExpiresAt == nil && id.ExpiresAt != nil:
					t.Errorf("expires_at = %v, want nil", id.ExpiresAt)
				case vec.Expect.ExpiresAt != nil && id.ExpiresAt == nil:
					t.Errorf("expires_at = nil, want %d", *vec.Expect.ExpiresAt)
				case vec.Expect.ExpiresAt != nil && id.ExpiresAt.Unix() != *vec.Expect.ExpiresAt:
					t.Errorf("expires_at = %d, want %d", id.ExpiresAt.Unix(), *vec.Expect.ExpiresAt)
				}
				return
			}

			if err == nil {
				t.Fatalf("expected %s, got identity %+v", vec.Expect.ErrorKind, id)
			}
			wantKind := ErrInvalid
			if vec.Expect.ErrorKind == "unavailable" {
				wantKind = ErrUnavailable
			}
			if !errors.Is(err, wantKind) {
				t.Errorf("error kind = %v, want %v (%v)", err, wantKind, vec.Expect.ErrorKind)
			}
			if sub := vec.Expect.ReasonSubstring; sub != "" && !strings.Contains(err.Error(), sub) {
				t.Errorf("reason %q does not contain %q", err.Error(), sub)
			}
		})
	}
}

// TestUnpinnedIssuerIsRefusedBeforeAnyFetch is the property the Python module's
// docstring spends a paragraph on: a token naming an unlisted SigningSubject
// must not cause a network call, or the refusal itself becomes an SSRF.
func TestUnpinnedIssuerIsRefusedBeforeAnyFetch(t *testing.T) {
	vf := loadVectors(t)
	ff := &fakeFetch{body: publicKeyBody(t)}
	v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
	if err != nil {
		t.Fatal(err)
	}
	token := "un=eve|tokenid=x|expiry=4102444800|SigningSubject=https://attacker.example/key|sig=abcd"
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("an unpinned SigningSubject was accepted")
	}
	if ff.count() != 0 {
		t.Fatalf("fetched %d times for an unpinned issuer; want 0 (%v)", ff.count(), ff.urls)
	}
}

func TestEmptyAllowlistIsRefused(t *testing.T) {
	if _, err := New(Options{Allowlist: []string{}}); err == nil {
		t.Fatal("an explicitly empty allowlist was accepted; it would authenticate nobody, or — if the check were skipped — everybody")
	}
	v, err := New(Options{})
	if err != nil {
		t.Fatalf("nil allowlist should default to the canonical four: %v", err)
	}
	if len(v.Allowlist()) != len(DefaultSigningSubjects) {
		t.Fatalf("default allowlist = %v", v.Allowlist())
	}
}

// TestKeyIsCachedAcrossAuthentications: three verifications, one fetch.
func TestKeyIsCachedAcrossAuthentications(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: publicKeyBody(t)}
	v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := v.Authenticate(context.Background(), token); err != nil {
			t.Fatalf("auth %d: %v", i, err)
		}
	}
	if ff.count() != 1 {
		t.Fatalf("fetched %d times for 3 authentications; want 1", ff.count())
	}
}

// TestKeyTTLExpiryRefetches: past the TTL the key is fetched again, without any
// signature having failed.
func TestKeyTTLExpiryRefetches(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: publicKeyBody(t)}
	now := time.Unix(vf.Now, 0).UTC()
	v, err := New(Options{
		Allowlist: vf.Allowlist,
		KeyTTL:    time.Hour,
		Now:       func() time.Time { return now },
		Fetch:     ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatal(err)
	}
	now = now.Add(2 * time.Hour)
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatal(err)
	}
	if ff.count() != 2 {
		t.Fatalf("fetched %d times across a TTL boundary; want 2", ff.count())
	}
}

// TestRotationRefetchesOnBadSignature: the key server rolled its key while we
// held the old one. A signature that fails against the CACHED key triggers a
// refetch, and the token then verifies against the new one.
//
// Note the first authentication makes exactly ONE fetch, not two: the cold
// fetch stamps the cache, so the rotation refetch that immediately follows is
// rate-limited by MinRefetch. That is the Python provider's behaviour too, and
// it is the right one — a cold key that does not verify is not evidence of a
// rotation.
func TestRotationRefetchesOnBadSignature(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	// Start with a key that is valid PEM but the wrong one: any signature made
	// with the fixture key fails against it.
	ff := &fakeFetch{body: []byte(`{"pubkey": "` + otherPEM + `"}`)}
	now := time.Unix(vf.Now, 0).UTC()
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		MinRefetch: time.Minute,
		Now:        func() time.Time { return now },
		Fetch:      ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("the wrong key verified a token")
	}
	if ff.count() != 1 {
		t.Fatalf("cold fetches = %d; want 1 (the rotation refetch is rate-limited by the cold one)", ff.count())
	}

	// The issuer's rotation completes. Past MinRefetch the failing signature
	// buys one refetch, which picks up the real key, and the same token — the
	// one that just failed — verifies.
	ff.mu.Lock()
	ff.body = publicKeyBody(t)
	ff.mu.Unlock()
	now = now.Add(2 * time.Minute)
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("after rotation: %v", err)
	}
	if ff.count() != 2 {
		t.Fatalf("fetches after rotation = %d; want 2", ff.count())
	}
}

// TestRefetchIsRateLimited: once a refetch has happened, a SECOND bad
// signature inside MinRefetch must not produce another one. Without this, a
// stream of garbage signatures is a free amplification lever against the
// BV-BRC key server.
func TestRefetchIsRateLimited(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: []byte(`{"pubkey": "` + otherPEM + `"}`)}
	now := time.Unix(vf.Now, 0).UTC()
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		MinRefetch: time.Minute,
		Now:        func() time.Time { return now },
		Fetch:      ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	// Cold fetch stamps the cache (1 fetch).
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("the wrong key verified a token")
	}
	// Past MinRefetch, the bad signature buys one refetch (2 fetches).
	now = now.Add(2 * time.Minute)
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("the wrong key verified a token")
	}
	if got := ff.count(); got != 2 {
		t.Fatalf("fetches = %d; want 2 (cold + one rotation refetch)", got)
	}
	// Immediately after, with the clock unmoved: no further fetch.
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("the wrong key verified a token")
	}
	if got := ff.count(); got != 2 {
		t.Fatalf("a second bad signature inside MinRefetch caused %d extra fetches; want 0", got-2)
	}
}

// TestUnavailableKeyServerIsErrUnavailable: we could not decide, which is a
// different fact from "the credential is bad" even though both are 401.
func TestUnavailableKeyServerIsErrUnavailable(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{err: errors.New("connection refused")}
	v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
	if err != nil {
		t.Fatal(err)
	}
	_, err = v.Authenticate(context.Background(), token)
	if !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v; want ErrUnavailable", err)
	}
}

// TestKeyServerVariants replays key_server_variants[]: the body shapes a key
// server is known to answer with (JSON `pubkey`, JSON `public_key`, a bare
// PEM, each also with literal backslash-n) and the ones that must fail.
func TestKeyServerVariants(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	if len(vf.KeyServerVariants) == 0 {
		t.Fatal("vectors.json carries no key_server_variants")
	}
	for _, variant := range vf.KeyServerVariants {
		variant := variant
		t.Run(variant.Name, func(t *testing.T) {
			ff := &fakeFetch{body: []byte(variant.Body)}
			v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
			if err != nil {
				t.Fatal(err)
			}
			_, err = v.Authenticate(context.Background(), token)
			if variant.ExpectParseOK {
				if err != nil {
					t.Fatalf("a usable key body was refused: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatal("an unusable key body produced an identity")
			}
			if !errors.Is(err, ErrUnavailable) {
				t.Fatalf("err = %v; want ErrUnavailable (the key server, not the token, is the problem)", err)
			}
		})
	}
}

// TestSignedRegionBoundary restates in Go what the fixture's
// `fields_after_sig_are_ignored` vector pins: appending fields after the
// signature cannot change who the caller is.
func TestSignedRegionBoundary(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: publicKeyBody(t)}
	v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
	if err != nil {
		t.Fatal(err)
	}
	base, err := v.Authenticate(context.Background(), token)
	if err != nil {
		t.Fatal(err)
	}
	tampered, err := v.Authenticate(context.Background(), token+"|un=eve@example.org")
	if err != nil {
		t.Fatalf("appending an unsigned field made the token unverifiable: %v", err)
	}
	if tampered.Subject != base.Subject {
		t.Fatalf("appended field changed the subject: %q → %q", base.Subject, tampered.Subject)
	}
}

func fixedNow(unix int64) func() time.Time {
	t := time.Unix(unix, 0).UTC()
	return func() time.Time { return t }
}

// validToken returns the fixture's plain "valid" token.
func validToken(t *testing.T, vf vectorFile) string {
	t.Helper()
	for _, v := range vf.Vectors {
		if v.Name == "valid" {
			return v.Token
		}
	}
	t.Fatal(`vectors.json has no vector named "valid"`)
	return ""
}

// otherPEM is a syntactically valid RSA public key that is NOT the fixture's —
// the "the issuer rotated its key" stand-in. Generated once, never used to
// sign anything.
const otherPEM = `-----BEGIN PUBLIC KEY-----\nMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC27sjYAHv74JPiM+IMpKZzhrHi\nS0qTt09GTvyblNn1gVhvf1S4UVuLybkfGjGU7CjaBeF7eT8GCZIkjznJ/k5FUQ44\nMXwgHmLcwGPpqAMSaWgQo80sWbkqUcvw9Wq3eM5vtSusSaWVwqzqd+MYhG4mKWSf\n4U70G0JN3G2Chb/r8wIDAQAB\n-----END PUBLIC KEY-----`
