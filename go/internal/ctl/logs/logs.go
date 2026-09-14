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
	"io/fs"
	"os"
	"os/user"
	"path/filepath"
	"sort"
	"strconv"
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

// SecretsUnreadableError is "this account cannot read the file whose values
// seed the redactor" — which on a pre-handover host is the EXPECTED state and
// not a fault of the control plane.
//
// Until PR-E hands a tenant over, its tenant.env and secrets.env are 0600
// files owned by the operator who provisioned it, while the daemon runs as
// its own service account. Seeding then fails with EACCES — and the ctl must
// still refuse to answer, because `redacted` is the constant true in the
// contract and an answer built from an unseeded redactor would be a lie.
// What was wrong was the CLASSIFICATION: this reached the router as an
// anonymous error, so it was logged as an internal fault and the operator was
// told "the control plane had a problem, please retry", which is both untrue
// and unactionable — no retry changes a file mode. The HTTP layer answers 409
// `refused` with Detail() instead, naming the account, the file and the owner.
//
// It is NOT a 403: the CALLER's credential is fine. `forbidden` is about the
// ctl's principal list, and reusing it here would tell an operator to fix
// their own key.
type SecretsUnreadableError struct {
	Tenant string // the tenant whose logs were asked for
	Path   string // the file that could not be read
	Owner  string // registry `owner` of the tenant
	Err    error  // the underlying EACCES
}

func (e *SecretsUnreadableError) Error() string { return e.Detail() }

func (e *SecretsUnreadableError) Unwrap() error { return e.Err }

// Detail is the operator-facing sentence: the account, the path, WHY that
// path is needed, and who owns the tenant — the four facts an operator needs
// to choose between waiting for the handover and fixing a mode.
func (e *SecretsUnreadableError) Detail() string {
	d := fmt.Sprintf(
		"logs for %s are unavailable: the ctl runs as %s and cannot read %s, whose values seed the log redactor",
		e.Tenant, CtlAccount(), e.Path)
	if e.PreHandover() {
		return d + fmt.Sprintf("; the tenant is owned by %s until its handover (PR-E)", e.Owner)
	}
	// The same refusal for a different cause: the tenant is already this
	// account's, so nothing is pending and the file's mode is simply wrong.
	return d + fmt.Sprintf("; the tenant is owned by %s, which is this account — the file's mode or ownership is wrong", e.Owner)
}

// PreHandover reports whether the tenant is still owned by another account,
// which is the expected state this refusal exists for.
func (e *SecretsUnreadableError) PreHandover() bool { return e.Owner != "" && e.Owner != CtlUser() }

// Extra is the typed payload of the contract error body.
func (e *SecretsUnreadableError) Extra() map[string]any {
	return map[string]any{
		"path":         e.Path,
		"owner":        e.Owner,
		"tenant":       e.Tenant,
		"ctl_user":     CtlUser(),
		"pre_handover": e.PreHandover(),
	}
}

// ctlIdentity resolves the account this process runs as. It is a variable so
// a test can pin it, and it reads the EFFECTIVE uid rather than $USER — a
// daemon under systemd has no reliable one.
var ctlIdentity = func() (name string, uid int) {
	uid = os.Geteuid()
	if u, err := user.LookupId(strconv.Itoa(uid)); err == nil && u.Username != "" {
		return u.Username, uid
	}
	return "", uid
}

// CtlAccount is the account for prose: "svcbvbrc (uid 1002)", or "uid 1002"
// when the name cannot be resolved.
func CtlAccount() string {
	name, uid := ctlIdentity()
	if name == "" {
		return fmt.Sprintf("uid %d", uid)
	}
	return fmt.Sprintf("%s (uid %d)", name, uid)
}

// CtlUser is the bare account name, for comparison against the registry's
// `owner` enum (svcbvbrc|wilke). Empty when it cannot be resolved — it then
// compares unequal to every owner, which only ever changes the tail of the
// sentence, never whether the request is refused.
func CtlUser() string {
	name, _ := ctlIdentity()
	return name
}

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
	// would be a lie. The caller turns a permission failure into the 409
	// `refused` (SecretsUnreadableError) and anything else into a 500.
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
// envfile.SeedRedactor. A file this account cannot READ is returned as a
// *SecretsUnreadableError — still a failure, still no log line, but a
// classified one the HTTP layer can explain instead of a 500.
func NewRedactor(t *registry.Tenant) (*settings.Redactor, error) {
	r := settings.NewRedactor()
	if err := envfile.SeedRedactor(filepath.Join(t.DataDir, "config"), r); err != nil {
		var se *envfile.SeedError
		if errors.As(err, &se) && errors.Is(err, fs.ErrPermission) {
			return nil, &SecretsUnreadableError{Tenant: t.Name, Path: se.Path, Owner: t.Owner, Err: err}
		}
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
