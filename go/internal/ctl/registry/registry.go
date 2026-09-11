package registry

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// ManifestHeader is the first line of manifest.tsv, byte-identical to the
// one apptainer/new-tenant.sh writes (including the em dash).
const ManifestHeader = "# tenant\tindex\tbase_port  (owned by apptainer/new-tenant.sh — do not edit)"

// File modes from the host-layout table.
const (
	modeRegistry = 0o660
	modeManifest = 0o664 // world-readable today; the projection holds no secrets
	modeLock     = 0o660
)

// Error classes.
var (
	ErrSchema              = errors.New("registry: unsupported schema_version")
	ErrManifestUnknownRows = errors.New("manifest_has_unknown_rows")
	ErrManifestMismatch    = errors.New("manifest_row_mismatch")
	ErrManifestMissingRows = errors.New("manifest_missing_rows")

	// ErrProjectionStale is the NON-FATAL diagnostic a Load reports when
	// manifest.tsv on disk is not what the registry projects. A diagnostic
	// and not a repair, because the repair rewrites manifest.tsv FROM the
	// registry: with a registry that knows one tenant and a live four-row
	// manifest, any read — `GET /v1/tenants`, `doctor` — would truncate the
	// manifest to one row, and the next apptainer/new-tenant.sh would reissue
	// index 0 onto a live tenant's ports. Repairing is an explicit verb now
	// (Repair), and reading is read-only.
	ErrProjectionStale = errors.New("manifest_projection_stale")

	// ErrStaleGeneration is returned by Save when the registry on disk moved
	// since the caller loaded it: two writers that both loaded generation N
	// would both write N+1 and the second would silently discard the first.
	// The caller must reload and re-apply.
	ErrStaleGeneration = errors.New("registry_stale_generation")

	// ErrLockUnusable is returned when a lock file exists but this process
	// cannot open it read-write (wrong owner or mode). The message names the
	// file and the fix; a bare EACCES from inside Save is not actionable.
	ErrLockUnusable = errors.New("registry_lock_unusable")
)

// Generation is the <path>.generation sidecar: which registry generation the
// manifest projection on disk was rendered from, and its content hash.
type Generation struct {
	Generation     int64  `json:"generation"`
	ManifestSHA256 string `json:"manifest_sha256"`
}

// Paths derives the sibling files of a registry path.
type Paths struct {
	Registry, RegistryLock, Manifest, ManifestLock, Generation string
}

// PathsFor returns the sibling files for registry path p.
func PathsFor(p string) Paths {
	dir := filepath.Dir(p)
	return Paths{
		Registry:     p,
		RegistryLock: p + ".lock",
		Manifest:     filepath.Join(dir, "manifest.tsv"),
		ManifestLock: filepath.Join(dir, "manifest.tsv.lock"),
		Generation:   p + ".generation",
	}
}

// lockTrace, when set (tests only), records every lock path as it is taken.
var lockTrace func(path string)

// writeHook, when set (tests only), runs before each tmp→final rename and may
// return an error to simulate a crash mid-save.
var writeHook func(final string) error

// Diagnostics are a Load's non-fatal findings: conditions a caller should
// SURFACE (a doctor row, a banner on the fleet view) but that must never fail
// a read and must never be silently "fixed" by a reader.
type Diagnostics struct {
	// ProjectionStale wraps ErrProjectionStale when manifest.tsv on disk is
	// not what the registry projects, naming both sides. nil when current.
	ProjectionStale error
}

// Stale reports whether anything was found.
func (d Diagnostics) Stale() bool { return d.ProjectionStale != nil }

// Load reads and validates the registry at path. It NEVER writes — not the
// registry, not manifest.tsv, not the .generation record. A stale projection
// is reported through LoadWithDiagnostics and repaired only by Repair, an
// explicit verb: see ErrProjectionStale for the port-reissue bug that a
// repair-on-read caused.
func Load(path string) (*Fleet, error) {
	f, _, err := LoadWithDiagnostics(path)
	return f, err
}

// LoadWithDiagnostics is Load plus what it noticed about the projection. The
// fleet is returned whenever the registry itself is readable and valid; a
// stale manifest is in Diagnostics, not in err.
func LoadWithDiagnostics(path string) (*Fleet, Diagnostics, error) {
	f, err := LoadNoRepair(path)
	if err != nil {
		return nil, Diagnostics{}, err
	}
	var d Diagnostics
	p := PathsFor(path)
	switch ok, perr := ProjectionCurrent(path, f); {
	case perr != nil:
		d.ProjectionStale = fmt.Errorf("%w: cannot compare %s with the registry (generation %d): %v",
			ErrProjectionStale, p.Manifest, f.Generation, perr)
	case !ok:
		d.ProjectionStale = fmt.Errorf(
			"%w: %s is not the projection of %s at generation %d — nothing was rewritten "+
				"(a reader must not truncate a live manifest); decide which side is right, "+
				"then call registry.Repair (no CLI verb wires it yet) to rewrite the manifest "+
				"FROM the registry",
			ErrProjectionStale, p.Manifest, p.Registry, f.Generation)
	}
	return f, d, nil
}

// Repair is the explicit projection-repair verb: it rewrites manifest.tsv and
// the .generation record FROM the registry, without bumping the generation.
// Destructive by design — it is how a crash between the registry write and the
// projection write is cleaned up — and therefore never reached from a read.
func Repair(path string) (*Fleet, error) {
	f, err := LoadNoRepair(path)
	if err != nil {
		return nil, err
	}
	if err := RepairProjection(path, f); err != nil {
		return nil, fmt.Errorf("registry: repair projection: %w", err)
	}
	return f, nil
}

// LoadNoRepair reads and validates the registry without touching the projection.
func LoadNoRepair(path string) (*Fleet, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var f Fleet
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("registry: parse %s: %w", path, err)
	}
	if f.SchemaVersion != SchemaVersion {
		return nil, fmt.Errorf("%w: %s has schema_version %d, this binary reads %d", ErrSchema, path, f.SchemaVersion, SchemaVersion)
	}
	// Structural first (it names the offending JSON pointer), then the
	// cross-field invariants. A registry that does not match the published
	// contract is refused at load: that is how a bug that wrote a secret into
	// `settings`, or an enum nobody implements, stops at the file instead of
	// reaching a dashboard.
	if err := f.ValidateContract(); err != nil {
		return nil, fmt.Errorf("registry: %s: %w", path, err)
	}
	if err := f.Validate(); err != nil {
		return nil, fmt.Errorf("registry: %s: %w", path, err)
	}
	return &f, nil
}

// Validate checks the invariants Save relies on.
func (f *Fleet) Validate() error {
	if f.PortStride < 6 {
		return fmt.Errorf("port_stride %d < 6", f.PortStride)
	}
	if f.PortBase < 10000 {
		return fmt.Errorf("port_base %d < 10000", f.PortBase)
	}
	seenIdx := map[int]string{}
	seenMan := map[string]string{}
	for key, t := range f.Tenants {
		if t == nil {
			return fmt.Errorf("tenant %q is null", key)
		}
		if t.Name != key {
			return fmt.Errorf("tenant key %q != name %q", key, t.Name)
		}
		if err := paths.ValidateName(t.Name); err != nil {
			return err
		}
		if t.ManifestName == "" {
			return fmt.Errorf("tenant %q has no manifest_name", key)
		}
		if err := paths.ValidateName(t.ManifestName); err != nil {
			return fmt.Errorf("tenant %q manifest_name: %w", key, err)
		}
		if other, dup := seenMan[t.ManifestName]; dup {
			return fmt.Errorf("tenants %q and %q share manifest_name %q", other, key, t.ManifestName)
		}
		seenMan[t.ManifestName] = key
		if other, dup := seenIdx[t.Ports.Index]; dup {
			return fmt.Errorf("tenants %q and %q share index %d", other, key, t.Ports.Index)
		}
		seenIdx[t.Ports.Index] = key
		if want := f.PortBase + t.Ports.Index*f.PortStride; t.Ports.Base != want {
			return fmt.Errorf("tenant %q base %d != %d (index %d)", key, t.Ports.Base, want, t.Ports.Index)
		}
	}
	for _, tb := range f.Tombstones {
		if other, dup := seenIdx[tb.Index]; dup {
			return fmt.Errorf("tombstone %q and tenant %q share index %d", tb.ManifestName, other, tb.Index)
		}
		seenIdx[tb.Index] = "tombstone:" + tb.ManifestName
	}
	return nil
}

// Save writes f as the next generation: registry lock → manifest lock (fixed
// order), generation+1, registry.json (tmp+fsync+rename), manifest.tsv
// projection (tmp+fsync+rename), then the .generation record. updatedBy is
// recorded verbatim (principal or "local:<uid>").
func Save(path string, f *Fleet, updatedBy string) error {
	if err := f.Validate(); err != nil {
		return err
	}
	p := PathsFor(path)
	if err := os.MkdirAll(filepath.Dir(path), 0o770|os.ModeSetgid); err != nil {
		return err
	}
	unlockReg, err := lock(p.RegistryLock)
	if err != nil {
		return err
	}
	defer unlockReg()
	unlockMan, err := lock(p.ManifestLock)
	if err != nil {
		return err
	}
	defer unlockMan()

	// Re-read the generation UNDER THE LOCK. f was loaded before the lock was
	// taken, so another writer may have committed in between; blindly writing
	// f.Generation+1 makes that writer's generation vanish (last-write-wins on
	// a file whose whole point is being the single source of truth).
	if err := checkGeneration(p.Registry, f.Generation); err != nil {
		return err
	}

	next := *f
	next.SchemaVersion = SchemaVersion
	next.Generation = f.Generation + 1
	next.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	next.UpdatedBy = updatedBy

	reg, err := Marshal(&next)
	if err != nil {
		return err
	}
	if err := writeAtomic(p.Registry, reg, modeRegistry); err != nil {
		return err
	}
	if err := writeProjection(p, &next); err != nil {
		return err
	}
	*f = next
	return nil
}

// Marshal renders f as the canonical registry JSON (indented, keys sorted by
// encoding/json's struct order, trailing newline).
func Marshal(f *Fleet) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(f); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeProjection(p Paths, f *Fleet) error {
	man := ProjectManifest(f)
	if err := writeAtomic(p.Manifest, man, modeManifest); err != nil {
		return err
	}
	gen, err := json.Marshal(Generation{Generation: f.Generation, ManifestSHA256: sha256Hex(man)})
	if err != nil {
		return err
	}
	return writeAtomic(p.Generation, append(gen, '\n'), modeRegistry)
}

// ProjectManifest renders the manifest.tsv projection: the header, then one
// `manifest_name\tindex\tbase` row per tenant sorted by index, trailing
// newline. Tombstones are NOT emitted — they live in the registry.
func ProjectManifest(f *Fleet) []byte {
	rows := make([]*Tenant, 0, len(f.Tenants))
	for _, t := range f.Tenants {
		rows = append(rows, t)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Ports.Index < rows[j].Ports.Index })
	var b bytes.Buffer
	b.WriteString(ManifestHeader)
	b.WriteByte('\n')
	for _, t := range rows {
		fmt.Fprintf(&b, "%s\t%d\t%d\n", t.ManifestName, t.Ports.Index, t.Ports.Base)
	}
	return b.Bytes()
}

// Allocate returns the next free block: max(index over tenants ∪ tombstones)+1.
// Indexes are never reused.
func Allocate(f *Fleet) (index, base int) {
	max := -1
	for _, t := range f.Tenants {
		if t.Ports.Index > max {
			max = t.Ports.Index
		}
	}
	for _, tb := range f.Tombstones {
		if tb.Index > max {
			max = tb.Index
		}
	}
	index = max + 1
	return index, f.PortBase + index*f.PortStride
}

// ManifestRow is one parsed manifest.tsv row.
type ManifestRow struct {
	Name  string
	Index int
	Base  int
	Line  int
}

// ParseManifest parses manifest.tsv the way new-tenant.sh does: `#` lines
// and blanks skipped, rows are name\tindex\tbase, a final row without a
// newline still counts.
func ParseManifest(b []byte) ([]ManifestRow, error) {
	var rows []ManifestRow
	for i, line := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		if strings.TrimSpace(line) == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.Split(line, "\t")
		if len(parts) < 3 {
			return nil, fmt.Errorf("manifest line %d: expected name\\tindex\\tbase, got %q", i+1, line)
		}
		idx, err1 := strconv.Atoi(parts[1])
		base, err2 := strconv.Atoi(parts[2])
		if err1 != nil || err2 != nil {
			return nil, fmt.Errorf("manifest line %d: non-numeric index/base in %q", i+1, line)
		}
		rows = append(rows, ManifestRow{Name: parts[0], Index: idx, Base: base, Line: i + 1})
	}
	return rows, nil
}

// ReconcileManifest checks a live manifest.tsv against the registry. It fails
// with ErrManifestUnknownRows (rows the registry does not know), then
// ErrManifestMismatch (index/base differ), then ErrManifestMissingRows
// (registry tenants the manifest lacks). Adopt --commit requires nil.
func ReconcileManifest(f *Fleet, manifest []byte) error {
	rows, err := ParseManifest(manifest)
	if err != nil {
		return err
	}
	byMan := map[string]*Tenant{}
	for _, t := range f.Tenants {
		byMan[t.ManifestName] = t
	}
	var unknown, mismatch []string
	seen := map[string]bool{}
	for _, r := range rows {
		seen[r.Name] = true
		t, ok := byMan[r.Name]
		if !ok {
			unknown = append(unknown, fmt.Sprintf("%s\t%d\t%d", r.Name, r.Index, r.Base))
			continue
		}
		if t.Ports.Index != r.Index || t.Ports.Base != r.Base {
			mismatch = append(mismatch, fmt.Sprintf("%s: manifest %d/%d registry %d/%d", r.Name, r.Index, r.Base, t.Ports.Index, t.Ports.Base))
		}
	}
	if len(unknown) > 0 {
		return fmt.Errorf("%w: %s", ErrManifestUnknownRows, strings.Join(unknown, "; "))
	}
	if len(mismatch) > 0 {
		return fmt.Errorf("%w: %s", ErrManifestMismatch, strings.Join(mismatch, "; "))
	}
	var missing []string
	for name := range byMan {
		if !seen[name] {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("%w: %s", ErrManifestMissingRows, strings.Join(missing, ", "))
	}
	return nil
}

// ProjectionCurrent reports whether the .generation record matches both the
// registry generation and the manifest.tsv on disk.
func ProjectionCurrent(path string, f *Fleet) (bool, error) {
	p := PathsFor(path)
	gb, err := os.ReadFile(p.Generation)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, nil
		}
		return false, err
	}
	var g Generation
	if err := json.Unmarshal(gb, &g); err != nil {
		return false, nil // corrupt record: repair
	}
	if g.Generation != f.Generation {
		return false, nil
	}
	mb, err := os.ReadFile(p.Manifest)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, nil
		}
		return false, err
	}
	return sha256Hex(mb) == g.ManifestSHA256 && bytes.Equal(mb, ProjectManifest(f)), nil
}

// RepairProjection rewrites manifest.tsv and the .generation record from f
// without bumping the generation. Takes the same locks in the same order.
func RepairProjection(path string, f *Fleet) error {
	p := PathsFor(path)
	unlockReg, err := lock(p.RegistryLock)
	if err != nil {
		return err
	}
	defer unlockReg()
	unlockMan, err := lock(p.ManifestLock)
	if err != nil {
		return err
	}
	defer unlockMan()
	return writeProjection(p, f)
}

// checkGeneration refuses the write when registry at path is not at the
// generation the caller loaded. A missing file is generation 0 — the fresh
// registry adopt creates.
func checkGeneration(path string, loaded int64) error {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		if loaded != 0 {
			return fmt.Errorf("%w: %s does not exist, but this fleet was loaded at generation %d "+
				"(it was deleted or moved under us) — reload and re-apply", ErrStaleGeneration, path, loaded)
		}
		return nil
	}
	if err != nil {
		return err
	}
	var head struct {
		Generation int64 `json:"generation"`
	}
	if err := json.Unmarshal(b, &head); err != nil {
		return fmt.Errorf("registry: re-read %s under the lock: %w", path, err)
	}
	if head.Generation != loaded {
		return fmt.Errorf("%w: %s is at generation %d, this fleet was loaded at %d — another writer "+
			"committed in between; reload and re-apply", ErrStaleGeneration, path, head.Generation, loaded)
	}
	return nil
}

// lock takes an exclusive flock on path (created if missing) and returns the
// release function.
func lock(path string) (func(), error) {
	fh, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, modeLock)
	if err != nil {
		if errors.Is(err, os.ErrPermission) {
			return nil, lockPermissionError(path, err)
		}
		return nil, fmt.Errorf("open lock %s: %w", path, err)
	}
	// The mode passed to OpenFile is masked by the umask (0022 on this host
	// leaves 0640), and the lock files that already exist under
	// /rag/data/tenants were created by a different account at 0644. Either
	// way the next writer in the ragops group cannot open the lock and Save
	// dies with a bare EACCES. Chmod after create, and tolerate EPERM: not
	// owning the file is not fatal as long as we could open it.
	if err := fh.Chmod(modeLock); err != nil && !errors.Is(err, os.ErrPermission) {
		_ = fh.Close()
		return nil, fmt.Errorf("chmod lock %s to %#o: %w", path, modeLock, err)
	}
	if err := syscall.Flock(int(fh.Fd()), syscall.LOCK_EX); err != nil {
		_ = fh.Close()
		return nil, fmt.Errorf("flock %s: %w", path, err)
	}
	if lockTrace != nil {
		lockTrace(path)
	}
	return func() {
		_ = syscall.Flock(int(fh.Fd()), syscall.LOCK_UN)
		_ = fh.Close()
	}, nil
}

// writeAtomic writes data to a same-directory temp file, fsyncs, sets mode,
// renames over final and fsyncs the directory. On any failure the temp file
// is removed and final is untouched.
func writeAtomic(final string, data []byte, mode os.FileMode) (err error) {
	dir := filepath.Dir(final)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(final)+".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		if err != nil {
			_ = tmp.Close()
			_ = os.Remove(tmpName)
		}
	}()
	if _, err = tmp.Write(data); err != nil {
		return err
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if err = tmp.Chmod(mode); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if writeHook != nil {
		if err = writeHook(final); err != nil {
			return err
		}
	}
	if err = os.Rename(tmpName, final); err != nil {
		return err
	}
	if d, derr := os.Open(dir); derr == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}

// lockPermissionError turns EACCES on a lock file into something an operator
// can act on: which file, what it is now, and the two commands that fix it.
func lockPermissionError(path string, cause error) error {
	detail := "it does not exist and the directory is not writable"
	if st, serr := os.Stat(path); serr == nil {
		owner := ""
		if sys, ok := st.Sys().(*syscall.Stat_t); ok {
			owner = fmt.Sprintf(", owner uid %d gid %d", sys.Uid, sys.Gid)
		}
		detail = fmt.Sprintf("it exists with mode %#o%s", st.Mode().Perm(), owner)
	}
	return fmt.Errorf("%w: cannot open %s read-write (%v): %s. The control plane and every "+
		"operator account must share it: `sudo chgrp ragops %s && sudo chmod %#o %s`",
		ErrLockUnusable, path, cause, detail, path, modeLock, path)
}

func sha256Hex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}
