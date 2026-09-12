// Package logs tails a tenant's logs for the CLI and the operator-only HTTP
// endpoint. Three properties matter and each is enforced here rather than at
// the call site:
//
//   - BOUNDED. At most MaxLines lines and MaxLineBytes per line, and at most
//     lines×MaxLineBytes BYTES scanned back from the end of the file: a
//     40 GiB api.log — or a 64 MiB line without a newline in it — costs the
//     same as a 40 KiB one.
//   - REDACTED. Every line goes through a settings.Redactor seeded from the
//     tenant's own secret files, so a key a process printed does not reach an
//     HTTP response. The contract types `redacted` as the constant true for
//     exactly this reason.
//   - PATHLESS. The response never carries the file's on-disk path.
package logs

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// Bounds.
const (
	// MaxLines is the ctl's own cap. The contract allows 5000; the CLI and
	// the API both refuse more than this.
	MaxLines = 2000
	// MaxLineBytes truncates a single monstrous line (a stack trace with an
	// embedded payload) instead of streaming it.
	MaxLineBytes = 16 << 10
	// readChunk is how much is read per backwards seek.
	readChunk = 64 << 10
	// TruncationMarker ends a line that was longer than MaxLineBytes, so an
	// operator can tell a cut line from a short one.
	TruncationMarker = "…[truncated]"
)

// ErrNoLog is returned when the tenant has no such log — a ui log for a
// static-UI tenant, a store log for a shared store, a file that was never
// created. The HTTP layer answers 404 with it.
var ErrNoLog = errors.New("no such tenant log")

// Tail returns the last n lines of path, redacted, and whether anything was
// cut (more lines existed, or a line was longer than MaxLineBytes).
//
// The read is bounded in BYTES as well as in lines: n lines of at most
// MaxLineBytes each is everything that can survive the truncation below, so
// the scan never looks further back than n×MaxLineBytes. It walks backwards
// only to COUNT newlines (nothing is accumulated — the earlier
// `append(part, buf...)` made a 64 MiB line quadratic: 32 s and 32 GiB of
// allocation), then reads the resulting window forward exactly once.
func Tail(path string, n int, red *settings.Redactor) ([]string, bool, error) {
	if n <= 0 {
		n = 200
	}
	if n > MaxLines {
		n = MaxLines
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, false, err
	}
	defer func() { _ = f.Close() }()
	size, err := f.Seek(0, io.SeekEnd)
	if err != nil {
		return nil, false, err
	}

	// floor is as far back as the scan may ever look.
	floor := size - int64(n)*MaxLineBytes
	if floor < 0 {
		floor = 0
	}
	start, found, err := lineStart(f, size, floor, n)
	if err != nil {
		return nil, false, err
	}
	truncated := start > 0
	if !found && floor > 0 {
		// The window filled up before n lines were found: what is left at
		// its front is the tail of a longer line, kept and marked below.
		truncated = true
	}

	window := make([]byte, size-start)
	if len(window) > 0 {
		if _, err := f.ReadAt(window, start); err != nil && !errors.Is(err, io.EOF) {
			return nil, false, err
		}
	}
	var lines []string
	if len(window) > 0 {
		lines = strings.Split(strings.TrimSuffix(string(window), "\n"), "\n")
	}
	if len(lines) > n {
		lines = lines[len(lines)-n:]
		truncated = true
	}
	out := make([]string, 0, len(lines))
	for _, l := range lines {
		// Redact BEFORE truncating: a secret straddling the cut would not
		// match any seed once its tail is gone.
		if red != nil {
			l = red.Redact(l)
		}
		if len(l) > MaxLineBytes {
			l = l[:MaxLineBytes] + TruncationMarker
			truncated = true
		}
		out = append(out, l)
	}
	return out, truncated, nil
}

// lineStart walks backwards from size to floor and returns the offset of the
// first byte of the last n lines, and whether it actually found n line
// breaks. Only the newline COUNT crosses the loop, so the cost is one linear
// pass over at most size-floor bytes with a single reusable buffer.
func lineStart(f *os.File, size, floor int64, n int) (int64, bool, error) {
	buf := make([]byte, readChunk)
	pos := size
	breaks := 0
	for pos > floor {
		chunk := int64(readChunk)
		if pos-floor < chunk {
			chunk = pos - floor
		}
		pos -= chunk
		b := buf[:chunk]
		if _, err := f.ReadAt(b, pos); err != nil && !errors.Is(err, io.EOF) {
			return 0, false, err
		}
		for i := len(b) - 1; i >= 0; i-- {
			if b[i] != '\n' {
				continue
			}
			// A newline at EOF terminates the last line, it does not open
			// another one.
			if pos+int64(i) == size-1 {
				continue
			}
			breaks++
			if breaks >= n {
				return pos + int64(i) + 1, true, nil
			}
		}
	}
	return floor, false, nil
}

// Read resolves the tenant's log for file, tails it and returns the contract
// body. The redactor is seeded from the tenant's own secret material.
func Read(roots paths.Roots, t *registry.Tenant, file model.LogFile, lines int) (*model.LogsResponse, error) {
	path, err := Resolve(roots, t, file)
	if err != nil {
		return nil, err
	}
	if lines <= 0 {
		lines = 200
	}
	requested := lines
	if requested > MaxLines {
		requested = MaxLines
	}
	// Seeding first, and failing when it fails: `redacted: true` is a
	// constant in the contract, so an answer built from an unseeded redactor
	// would be a lie. The caller turns this into a 500.
	red, err := NewRedactor(t)
	if err != nil {
		return nil, err
	}
	out, truncated, err := Tail(path, requested, red)
	if err != nil {
		return nil, err
	}
	return &model.LogsResponse{
		Tenant: t.Name, File: file, Lines: out,
		Requested: requested, Returned: len(out), Truncated: truncated, Redacted: true,
	}, nil
}

// NewRedactor seeds a redactor with every value in the tenant's secrets.env,
// every secret-class value in its tenant.env and provision.env (the legacy
// layout keeps secrets there), and the same from every historical copy beside
// them (*.bak-<stamp>, *.orig, *.recovered) — a process started before a
// rotation still prints the OLD key. Values shorter than the redactor's floor
// are ignored by it, so a port or a boolean never shreds a log line.
//
// It fails rather than returning a half-seeded redactor: see
// envfile.SeedRedactor.
func NewRedactor(t *registry.Tenant) (*settings.Redactor, error) {
	r := settings.NewRedactor()
	if err := envfile.SeedRedactor(filepath.Join(t.DataDir, "config"), r); err != nil {
		return nil, err
	}
	return r, nil
}

// Resolve maps a file selector onto a path under the tenant's tree. Every
// candidate is tried in order and the first readable one wins; nothing
// outside the tenant's data dir (or the apptainer instance log directory) is
// ever considered.
func Resolve(roots paths.Roots, t *registry.Tenant, file model.LogFile) (string, error) {
	tp := paths.TenantPaths(roots, t.Name, t.ManifestName)
	var candidates []string
	switch file {
	case model.LogAPI:
		candidates = []string{string(t.API.Log), tp.APILog, filepath.Join(t.DataDir, "api.log")}
	case model.LogUI:
		if t.UI.Mode == registry.UIModeStatic {
			return "", fmt.Errorf("%w: %s serves a built UI, which has no log", ErrNoLog, t.Name)
		}
		candidates = []string{
			filepath.Join(t.DataDir, "logs", "ui-"+t.Name+".log"),
			filepath.Join(t.DataDir, "ui.log"),
		}
	case model.LogQdrant:
		if t.Stores.Qdrant.Ownership != registry.OwnershipExclusive {
			return "", fmt.Errorf("%w: %s runs on a shared qdrant the ctl only probes", ErrNoLog, t.Name)
		}
		candidates = append(storeCandidates(t, tp, "qdrant"), instanceLog(string(t.Stores.Qdrant.Instance))...)
	case model.LogES:
		if t.Stores.Elasticsearch.Ownership != registry.OwnershipExclusive {
			return "", fmt.Errorf("%w: %s runs on a shared elasticsearch the ctl only probes", ErrNoLog, t.Name)
		}
		candidates = append(storeCandidates(t, tp, "elasticsearch"), instanceLog(string(t.Stores.Elasticsearch.Instance))...)
		if newest := newestLog(tp.ESLogs); newest != "" {
			candidates = append(candidates, newest)
		}
	default:
		return "", fmt.Errorf("%w: unknown selector %q", ErrNoLog, file)
	}
	for _, c := range candidates {
		if c == "" {
			continue
		}
		if fi, err := os.Stat(c); err == nil && !fi.IsDir() {
			return c, nil
		}
	}
	return "", fmt.Errorf("%w: %s has no readable %s log", ErrNoLog, t.Name, file)
}

// storeCandidates are the unit-written log (StandardOutput=append:) and its
// pre-ctl equivalents.
func storeCandidates(t *registry.Tenant, tp paths.Tenant, svc string) []string {
	short := svc
	if svc == "elasticsearch" {
		short = "es"
	}
	return []string{
		filepath.Join(t.DataDir, "logs", svc+"-"+t.ManifestName+".log"),
		filepath.Join(t.DataDir, "logs", short+"-"+t.ManifestName+".log"),
		filepath.Join(tp.LogsDir, svc+"-"+t.ManifestName+".log"),
	}
}

// instanceLog is the apptainer instance's stdout capture, the only place a
// hand-started store's output goes today. It lives under the operator's home,
// so it is offered only when this account can actually read it.
func instanceLog(instance string) []string {
	if instance == "" {
		return nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	base := filepath.Join(home, ".apptainer", "instances", "logs")
	matches, err := filepath.Glob(filepath.Join(base, "*", "*", instance+".out"))
	if err != nil {
		return nil
	}
	sort.Strings(matches)
	return matches
}

// newestLog is the most recently modified *.log in dir ("" when there is
// none) — Elasticsearch rotates its own files and names them after the
// cluster, not the tenant.
func newestLog(dir string) string {
	matches, err := filepath.Glob(filepath.Join(dir, "*.log"))
	if err != nil || len(matches) == 0 {
		return ""
	}
	type entry struct {
		path string
		mod  int64
	}
	var entries []entry
	for _, m := range matches {
		fi, err := os.Stat(m)
		if err != nil {
			continue
		}
		entries = append(entries, entry{m, fi.ModTime().UnixNano()})
	}
	if len(entries) == 0 {
		return ""
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].mod > entries[j].mod })
	return entries[0].path
}
