package api

import (
	"context"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

// #716: the daemon's own report of a store it cannot open has to name the
// foreign-owned SQLite sidecars that are almost always the cause — the raw
// "attempt to write a readonly database (8)" does not.

// foreignProbe reports uid 4242 ("wilke") for every -shm/-wal, the daemon's
// own uid for everything else. A test cannot chown; this is the simulation.
func foreignProbe() doctor.SidecarProbe {
	return doctor.SidecarProbe{
		UID: func(fi fs.FileInfo) (int, bool) {
			if strings.HasSuffix(fi.Name(), "-shm") || strings.HasSuffix(fi.Name(), "-wal") {
				return 4242, true
			}
			return os.Geteuid(), true
		},
		User: func(uid int) string {
			if uid == 4242 {
				return "wilke"
			}
			return ""
		},
	}
}

// sidecarStore lays out the 2026-10-09 shape: jobs.db, a 32 KiB -shm and an
// empty -wal.
func sidecarStore(t *testing.T, walBytes int) string {
	t.Helper()
	store := filepath.Join(t.TempDir(), "jobs.db")
	for p, n := range map[string]int{store: 4096, store + "-shm": 32768, store + "-wal": walBytes} {
		if err := os.WriteFile(p, make([]byte, n), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return store
}

func TestEngineFailureNamesForeignSidecars(t *testing.T) {
	store := sidecarStore(t, 0)
	raw := errors.New("job store " + store + ": jobs: migrating: attempt to write a readonly database (8)")

	published, logDetail, healthDetail := explainEngineFailure(store, raw, foreignProbe())

	if !errors.Is(published, raw) {
		t.Error("the published error no longer wraps the SQLite error")
	}
	for _, want := range []string{
		store + "-shm owned by wilke (uid 4242, 32768 bytes)",
		store + "-wal owned by wilke (uid 4242, 0 bytes)",
		"the WAL is empty, so as wilke",
		"rm " + store + "-shm " + store + "-wal",
		"never open it with sqlite3/python",
		"without a restart",
		doctor.JobEngineUnavailable,
	} {
		if !strings.Contains(logDetail, want) {
			t.Errorf("log detail lacks %q:\n%s", want, logDetail)
		}
		if !strings.Contains(published.Error(), want) {
			t.Errorf("the published (refusal) error lacks %q", want)
		}
	}
	// /health is anonymous: base names, no directory.
	if strings.Contains(healthDetail, filepath.Dir(store)) {
		t.Errorf("health detail leaks the state dir:\n%s", healthDetail)
	}
	for _, want := range []string{"jobs.db-shm owned by wilke (uid 4242, 32768 bytes)", "rm jobs.db-shm jobs.db-wal"} {
		if !strings.Contains(healthDetail, want) {
			t.Errorf("health detail lacks %q:\n%s", want, healthDetail)
		}
	}
}

func TestEngineFailureNonEmptyWALSaysCopyAsideFirst(t *testing.T) {
	store := sidecarStore(t, 8272)
	_, logDetail, _ := explainEngineFailure(store, errors.New("readonly"), foreignProbe())
	if !strings.Contains(logDetail, "copy the sidecars aside first") {
		t.Errorf("a non-empty WAL must be copied aside before removal:\n%s", logDetail)
	}
}

// Sidecars the daemon owns are not the cause and are not reported: the error
// is published unchanged.
func TestEngineFailureOwnSidecarsAreSilent(t *testing.T) {
	store := sidecarStore(t, 0)
	raw := errors.New("disk I/O error")
	published, logDetail, healthDetail := explainEngineFailure(store, raw, doctor.SidecarProbe{})
	if published != raw || logDetail != "" || healthDetail != "" {
		t.Errorf("own sidecars produced a diagnosis: %v / %q / %q", published, logDetail, healthDetail)
	}
}

// The retry publishes the diagnosis where it is read: GET /health carries it
// as `engine_detail` while the engine is unavailable, and drops it once one
// is published.
func TestRetryPublishesTheSidecarDiagnosisOnHealth(t *testing.T) {
	cfg, repair := blockedStoreConfig(t)
	srv, h := engineStateServer(t, nil, errors.New("job store: not a directory"))

	// The blocked store cannot have real sidecars beside it (its parent is a
	// file), so the probe answers for them from a laid-out store elsewhere.
	laid := sidecarStore(t, 0)
	probe := foreignProbe()
	probe.Lstat = func(p string) (fs.FileInfo, error) {
		switch p {
		case cfg.StorePath + "-shm":
			return os.Lstat(laid + "-shm")
		case cfg.StorePath + "-wal":
			return os.Lstat(laid + "-wal")
		}
		return os.Lstat(p)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	seen := make(chan string, 1)
	backoff := func(n int) time.Duration {
		if n == 2 {
			// Attempt 1 has failed and published; read /health now.
			seen <- healthDetail(t, h)
			repair()
		}
		return time.Millisecond
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		retryEngine(ctx, srv, cfg, cfg.StorePath, discardLogger(), backoff, probe)
	}()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("retryEngine did not return")
	}
	got := <-seen
	if !strings.Contains(got, "jobs.db-shm owned by wilke") || strings.Contains(got, cfg.StorePath) {
		t.Errorf("engine_detail while unavailable = %q", got)
	}
	if d := healthDetail(t, h); d != "" {
		t.Errorf("engine_detail after recovery = %q, want absent", d)
	}
}

// With no published detail, an unavailable engine still says where to look.
func TestHealthEngineDetailGenericWhenNoDiagnosis(t *testing.T) {
	_, h := engineStateServer(t, nil, errors.New("job store: disk I/O error"))
	d := healthDetail(t, h)
	if !strings.Contains(d, doctor.JobEngineUnavailable) || !strings.Contains(d, "keeps retrying") {
		t.Errorf("generic engine_detail = %q", d)
	}
	_, ok := engineStateServer(t, &fakeEngine{}, nil)
	if d := healthDetail(t, ok); d != "" {
		t.Errorf("an available engine carries engine_detail %q", d)
	}
}

func healthDetail(t *testing.T, h http.Handler) string {
	t.Helper()
	w := do(t, h, http.MethodGet, "/health", nil, "")
	body := decode(t, w)
	if body["status"] != model.HealthOKStatus {
		t.Fatalf("status %v", body["status"])
	}
	d, _ := body["engine_detail"].(string)
	return d
}
