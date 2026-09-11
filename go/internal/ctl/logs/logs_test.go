package logs

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// tenantTree builds a temporary tenant with the config files the redactor is
// seeded from and the log files the selector resolves.
func tenantTree(t *testing.T) (paths.Roots, *registry.Tenant) {
	t.Helper()
	roots := paths.NewRoots(t.TempDir(), paths.Overrides{})
	tp := paths.TenantPaths(roots, "dev", "dev")
	for _, d := range []string{tp.ConfigDir, tp.LogsDir, tp.ESLogs} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write(t, tp.TenantEnv, strings.Join([]string{
		"LOG_LEVEL=info",
		`API_KEYS='["k-aaaaaaaaaaaaaaaaaaaa","k-bbbbbbbbbbbbbbbbbbbb"]'`,
		`API_KEY_ROLES='{"k-aaaaaaaaaaaaaaaaaaaa":"admin"}'`,
	}, "\n")+"\n")
	write(t, tp.SecretsEnv, "TENANT_PG_PASSWORD=pg-super-secret-value\n")

	tenant := registry.NewTenant("dev", "dev")
	tenant.DataDir = tp.DataDir
	tenant.API = registry.API{Bind: "0.0.0.0", PidFile: tp.PidFile, Log: tp.APILog}
	tenant.UI = registry.UI{Mode: registry.UIModeDev, Port: 8090, Base: "/ragstack/dev/ui/"}
	tenant.Stores.Qdrant.Ownership = registry.OwnershipExclusive
	tenant.Stores.Qdrant.Instance = "qdrant-dev"
	tenant.Stores.Elasticsearch.Ownership = registry.OwnershipShared
	return roots, tenant
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestTailReadsFromTheEnd: the last n lines, in order, with truncated set
// only when something was actually cut.
func TestTailReadsFromTheEnd(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "api.log")
	var b strings.Builder
	for i := 1; i <= 5000; i++ {
		fmt.Fprintf(&b, "line %d\n", i)
	}
	write(t, path, b.String())

	lines, truncated, err := Tail(path, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(lines) != 10 || lines[0] != "line 4991" || lines[9] != "line 5000" {
		t.Fatalf("tail = %v", lines)
	}
	if !truncated {
		t.Error("a 5000-line file tailed at 10 is truncated")
	}

	short := filepath.Join(dir, "short.log")
	write(t, short, "one\ntwo\n")
	lines, truncated, err = Tail(short, 10, nil)
	if err != nil || len(lines) != 2 || truncated {
		t.Errorf("short file: %v %v %v", lines, truncated, err)
	}

	empty := filepath.Join(dir, "empty.log")
	write(t, empty, "")
	lines, truncated, err = Tail(empty, 10, nil)
	if err != nil || len(lines) != 0 || truncated {
		t.Errorf("empty file: %v %v %v", lines, truncated, err)
	}

	// A file with no trailing newline still yields its last line.
	noNL := filepath.Join(dir, "nonl.log")
	write(t, noNL, "a\nb")
	if lines, _, _ := Tail(noNL, 5, nil); len(lines) != 2 || lines[1] != "b" {
		t.Errorf("no trailing newline: %v", lines)
	}
}

// TestTailCapsRequestAndLineLength: the ctl's own bounds, not the caller's.
func TestTailCapsRequestAndLineLength(t *testing.T) {
	path := filepath.Join(t.TempDir(), "api.log")
	write(t, path, strings.Repeat("x", MaxLineBytes+500)+"\n")
	lines, truncated, err := Tail(path, 1_000_000, nil)
	if err != nil {
		t.Fatal(err)
	}
	// The cut line keeps its marker, so the bound is the cap plus that.
	if len(lines) != 1 || len(lines[0]) != MaxLineBytes+len(TruncationMarker) {
		t.Fatalf("line length = %d, want the %d cap plus the marker", len(lines[0]), MaxLineBytes)
	}
	if !truncated {
		t.Error("a cut line sets truncated")
	}
}

// TestRedactionSeedsFromTheTenantsOwnSecrets: a key the API printed is gone
// from the response, and so is a secret-shaped assignment.
func TestRedactionSeedsFromTheTenantsOwnSecrets(t *testing.T) {
	roots, tenant := tenantTree(t)
	write(t, tenant.API.Log, strings.Join([]string{
		"INFO  started on :24040",
		"DEBUG authenticated with k-aaaaaaaaaaaaaaaaaaaa",
		"ERROR  connect failed: TENANT_PG_PASSWORD=pg-super-secret-value",
		"DEBUG token |sig=" + strings.Repeat("ab", 40),
	}, "\n")+"\n")

	resp, err := Read(roots, tenant, model.LogAPI, 100)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(resp.Lines, "\n")
	for _, secret := range []string{"k-aaaaaaaaaaaaaaaaaaaa", "pg-super-secret-value", strings.Repeat("ab", 40)} {
		if strings.Contains(joined, secret) {
			t.Errorf("a secret survived redaction: %q", secret)
		}
	}
	if !strings.Contains(joined, "started on :24040") {
		t.Error("redaction ate an ordinary line")
	}
	if !resp.Redacted || resp.Returned != 4 || resp.Requested != 100 {
		t.Errorf("response = %+v", resp)
	}
	if resp.Tenant != "dev" || resp.File != model.LogAPI {
		t.Errorf("response identity = %+v", resp)
	}
}

// TestResolveSelectors covers every file selector, including the two that
// must answer ErrNoLog rather than a path.
func TestResolveSelectors(t *testing.T) {
	roots, tenant := tenantTree(t)
	tp := paths.TenantPaths(roots, "dev", "dev")
	write(t, tenant.API.Log, "api\n")
	write(t, filepath.Join(tenant.DataDir, "ui.log"), "vite\n")
	write(t, filepath.Join(tp.LogsDir, "qdrant-dev.log"), "qdrant\n")

	for _, c := range []struct {
		file model.LogFile
		want string
	}{
		{model.LogAPI, tenant.API.Log},
		{model.LogUI, filepath.Join(tenant.DataDir, "ui.log")},
		{model.LogQdrant, filepath.Join(tp.LogsDir, "qdrant-dev.log")},
	} {
		got, err := Resolve(roots, tenant, c.file)
		if err != nil || got != c.want {
			t.Errorf("resolve %s = %q, %v; want %q", c.file, got, err, c.want)
		}
	}
	// A shared store has no log of this tenant's.
	if _, err := Resolve(roots, tenant, model.LogES); !errors.Is(err, ErrNoLog) {
		t.Errorf("shared ES log = %v, want ErrNoLog", err)
	}
	// A built UI has no log at all.
	static := *tenant
	static.UI.Mode = registry.UIModeStatic
	if _, err := Resolve(roots, &static, model.LogUI); !errors.Is(err, ErrNoLog) {
		t.Errorf("static UI log = %v, want ErrNoLog", err)
	}
	if _, err := Resolve(roots, tenant, model.LogFile("nope")); !errors.Is(err, ErrNoLog) {
		t.Errorf("unknown selector = %v, want ErrNoLog", err)
	}
}

// TestESFallsBackToTheNewestRotatedFile: Elasticsearch names its own logs
// after the cluster, so the selector takes the newest *.log in its log dir.
func TestESFallsBackToTheNewestRotatedFile(t *testing.T) {
	roots, tenant := tenantTree(t)
	tenant.Stores.Elasticsearch.Ownership = registry.OwnershipExclusive
	tp := paths.TenantPaths(roots, "dev", "dev")
	old := filepath.Join(tp.ESLogs, "old-cluster.log")
	recent := filepath.Join(tp.ESLogs, "ragstack-dev.log")
	write(t, old, "old\n")
	write(t, recent, "recent\n")
	past := time.Now().Add(-48 * time.Hour)
	if err := os.Chtimes(old, past, past); err != nil {
		t.Fatal(err)
	}
	got, err := Resolve(roots, tenant, model.LogES)
	if err != nil {
		t.Fatal(err)
	}
	if got != recent {
		t.Errorf("resolve es = %q, want the newest file %q", got, recent)
	}
}

// TestReadRefusesAnAbsentFile: a missing log is an error the HTTP layer maps
// to 404, never an empty 200.
func TestReadRefusesAnAbsentFile(t *testing.T) {
	roots, tenant := tenantTree(t)
	if _, err := Read(roots, tenant, model.LogAPI, 10); !errors.Is(err, ErrNoLog) {
		t.Errorf("read of an absent api log = %v, want ErrNoLog", err)
	}
}

// TestNewRedactorSeedsEachKeySeparately: the JSON triple is unpacked so a
// single key printed on its own is caught, not only the whole blob.
func TestNewRedactorSeedsEachKeySeparately(t *testing.T) {
	_, tenant := tenantTree(t)
	r, err := NewRedactor(tenant)
	if err != nil {
		t.Fatal(err)
	}
	if r.Seeds() < 3 {
		t.Errorf("redactor has %d seeds, want the two keys plus the password", r.Seeds())
	}
	out := r.Redact("used k-bbbbbbbbbbbbbbbbbbbb today")
	if strings.Contains(out, "k-bbbbbbbbbbbbbbbbbbbb") {
		t.Errorf("second key not seeded: %q", out)
	}
	if !strings.Contains(out, settings.Redacted) {
		t.Errorf("redaction marker missing: %q", out)
	}
}

// TestTailBoundsAMonstrousLine is the 64 MiB-without-a-newline case. The
// backwards scan used to do `buf = append(part, buf...)` per 64 KiB chunk,
// which is quadratic: this file took 32 s and allocated 32 GiB. The scan is
// now capped at lines×MaxLineBytes and the window is read forward once, so
// the whole call is a few milliseconds — the assertion below is deliberately
// two orders of magnitude looser than that, to fail only on a real
// regression.
func TestTailBoundsAMonstrousLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "api.log")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	chunk := bytes.Repeat([]byte("x"), 1<<20)
	for i := 0; i < 64; i++ { // 64 MiB, not one newline in it
		if _, err := f.Write(chunk); err != nil {
			t.Fatal(err)
		}
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	start := time.Now()
	lines, truncated, err := Tail(path, 200, nil)
	elapsed := time.Since(start)
	if err != nil {
		t.Fatal(err)
	}
	if elapsed > 200*time.Millisecond {
		t.Errorf("Tail of a 64 MiB single-line file took %s, want < 200ms", elapsed)
	}
	if !truncated {
		t.Error("truncated = false; a 64 MiB line that was cut to 16 KiB is truncated")
	}
	if len(lines) != 1 {
		t.Fatalf("got %d lines, want 1", len(lines))
	}
	if !strings.HasSuffix(lines[0], TruncationMarker) {
		t.Error("a cut line must carry the truncation marker")
	}
	if got, want := len(lines[0]), MaxLineBytes+len(TruncationMarker); got != want {
		t.Errorf("line length %d, want %d", got, want)
	}
}

// TestTailStillReturnsTheLastLines guards the rewrite: a file with more lines
// than asked for, spanning more than one read chunk, still answers with the
// LAST n in order.
func TestTailStillReturnsTheLastLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "api.log")
	var b strings.Builder
	for i := 0; i < 5000; i++ {
		fmt.Fprintf(&b, "line %d %s\n", i, strings.Repeat("p", 40))
	}
	write(t, path, b.String())
	lines, truncated, err := Tail(path, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !truncated {
		t.Error("truncated = false, but 4990 lines were dropped")
	}
	if len(lines) != 10 {
		t.Fatalf("got %d lines, want 10", len(lines))
	}
	if !strings.HasPrefix(lines[0], "line 4990 ") || !strings.HasPrefix(lines[9], "line 4999 ") {
		t.Errorf("wrong window: %q … %q", lines[0], lines[9])
	}
}

// TestRedactorKeepsTenantNamesOutOfTheSeeds is the API_KEY_TENANTS bug: the
// map is keyed BY the API key and its VALUES are tenant names, so seeding the
// values struck `lucid-next` — eight characters, over the seed floor — out of
// lucid-next's own log lines, on the one page an operator opens when that
// tenant is broken.
func TestRedactorKeepsTenantNamesOutOfTheSeeds(t *testing.T) {
	roots := paths.NewRoots(t.TempDir(), paths.Overrides{})
	tp := paths.TenantPaths(roots, "lucid-next", "lucid")
	write(t, tp.TenantEnv, strings.Join([]string{
		`API_KEYS='["k-cccccccccccccccccccc"]'`,
		`API_KEY_TENANTS='{"k-cccccccccccccccccccc":"lucid-next"}'`,
		`API_KEY_ROLES='{"k-cccccccccccccccccccc":"admin"}'`,
	}, "\n")+"\n")
	tenant := registry.NewTenant("lucid-next", "lucid")
	tenant.DataDir = tp.DataDir

	r, err := NewRedactor(tenant)
	if err != nil {
		t.Fatal(err)
	}
	out := r.Redact("tenant lucid-next served a request with k-cccccccccccccccccccc")
	if !strings.Contains(out, "lucid-next") {
		t.Errorf("the tenant's own name was redacted from its log: %q", out)
	}
	if strings.Contains(out, "k-cccccccccccccccccccc") {
		t.Errorf("the API key survived: %q", out)
	}
}

// TestNewRedactorSeedsRotatedKeys: a key rotated OUT of secrets.env is still
// printed by the process that was started before the rotation, so the
// historical copies beside the live file are mined too.
func TestNewRedactorSeedsRotatedKeys(t *testing.T) {
	roots, tenant := tenantTree(t)
	tp := paths.TenantPaths(roots, "dev", "dev")
	write(t, filepath.Join(tp.ConfigDir, "secrets.env.bak-20260901"),
		"TENANT_PG_PASSWORD=pg-previous-secret-value\n")
	write(t, filepath.Join(tp.ConfigDir, "tenant.env.orig"),
		`API_KEYS='["k-dddddddddddddddddddd"]'`+"\n")

	r, err := NewRedactor(tenant)
	if err != nil {
		t.Fatal(err)
	}
	out := r.Redact("old pg-previous-secret-value and old k-dddddddddddddddddddd")
	if strings.Contains(out, "pg-previous-secret-value") || strings.Contains(out, "k-dddddddddddddddddddd") {
		t.Errorf("a rotated-out secret survived: %q", out)
	}
}

// TestReadFailsClosedWhenSeedingFails: the contract types `redacted` as the
// constant true, so an answer built on a redactor that could not be seeded is
// a lie. Read must fail (the HTTP layer answers 500) rather than return log
// lines with an empty seed set.
func TestReadFailsClosedWhenSeedingFails(t *testing.T) {
	roots, tenant := tenantTree(t)
	tp := paths.TenantPaths(roots, "dev", "dev")
	write(t, tp.APILog, "hello\n")
	// A tenant.env the strict grammar refuses AND that holds no KEY=value
	// line for the tolerant scan to mine: nothing can be seeded from it.
	write(t, tp.TenantEnv, "this file is a note, not an env file\n")

	if _, err := Read(roots, tenant, model.LogAPI, 10); err == nil {
		t.Fatal("Read succeeded with an unseedable tenant.env; it must fail closed")
	}
}
