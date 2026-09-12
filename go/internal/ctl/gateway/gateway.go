// Package gateway publishes nginx configuration GENERATIONS.
//
// The proxy tree (/rag/config/proxy) belongs to the coconut-proxy repo, whose
// deploy.sh refuses drifted tracked files. So the ctl never edits a tracked
// file: it writes whole generations of the two generated includes under
//
//	<CtlStateDir>/gateway/gen-<N>/conf.d/05-tenants.generated.conf
//	<CtlStateDir>/gateway/gen-<N>/snippets/tenants-ui-static.generated.conf
//
// and publishes a generation by moving ONE pointer — the `current` symlink —
// while the two paths inside the proxy tree are stable symlinks into
// `current`. Publishing is therefore a single atomic rename, and rolling back
// is the same rename in the other direction.
//
// txn.json beside the generations is the durable record of the last switch.
// It is rewritten at every transition (staging → switched → reloaded →
// verified, or reverted/failed), so a crash anywhere leaves a file that says
// exactly how far the publication got; Repair reads it and puts `current`
// back on the last generation that was actually verified.
package gateway

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/version"
)

// The two generated includes, as relative paths inside a generation dir and
// inside the proxy tree. They are the same two strings in both places: that is
// what makes `<proxy>/conf.d/05-tenants.generated.conf` a symlink to
// `<state>/gateway/current/conf.d/05-tenants.generated.conf`.
const (
	FileTenants = "conf.d/05-tenants.generated.conf"
	FileStatic  = "snippets/tenants-ui-static.generated.conf"
	// ManifestName records the sha256 of each file of the generation.
	ManifestName = "manifest.json"
	// TxnName is the durable record of the last publication attempt.
	TxnName = "txn.json"
	// CurrentName is the pointer every published include resolves through.
	CurrentName = "current"
)

// RelPaths is the generation's file list, in the order everything (the
// manifest, the diff, the digest) iterates it.
func RelPaths() []string { return []string{FileTenants, FileStatic} }

// Generation is a rendered, not necessarily published, gateway generation.
type Generation struct {
	N                  int               `json:"generation"`
	RegistryGeneration int64             `json:"registry_generation"`
	RegistrySHA256     string            `json:"registry_sha256"`
	CreatedAt          string            `json:"created_at"`
	Generator          string            `json:"generator"`
	Files              map[string][]byte `json:"-"`
}

// Manifest is gen-<N>/manifest.json: what was rendered, from which registry,
// and the digest of every file as written.
type Manifest struct {
	Generation         int               `json:"generation"`
	RegistryGeneration int64             `json:"registry_generation"`
	RegistrySHA256     string            `json:"registry_sha256"`
	Generator          string            `json:"generator"`
	CreatedAt          string            `json:"created_at"`
	Files              map[string]string `json:"files"` // relpath -> "sha256:<hex>"
}

// header markers. Everything between them is metadata ABOUT the render (when
// it ran, which binary ran it) rather than configuration, so comparisons —
// pending_diff, the unified diff, "is the published generation still what the
// registry says" — strip it first. Without that, the timestamp alone would
// make every render differ from every publication and `pending_diff` could
// never be false.
const (
	headerBegin = "# ragstack-ctl gateway generation header — begin"
	headerEnd   = "# ragstack-ctl gateway generation header — end"
)

// now is the clock, indirected for tests.
var now = time.Now

// State is the on-disk gateway state directory.
type State struct{ Dir string }

// NewState locates the state dir for a deployment: <CtlStateDir>/gateway.
func NewState(roots paths.Roots) State {
	return State{Dir: filepath.Join(roots.CtlStateDir, "gateway")}
}

// GenDir is the directory of generation n.
func (s State) GenDir(n int) string { return filepath.Join(s.Dir, "gen-"+strconv.Itoa(n)) }

// CurrentLink is the symlink every published include resolves through.
func (s State) CurrentLink() string { return filepath.Join(s.Dir, CurrentName) }

// TxnPath is the durable record of the last publication attempt.
func (s State) TxnPath() string { return filepath.Join(s.Dir, TxnName) }

// Generations lists the generation numbers present on disk, ascending.
func (s State) Generations() []int {
	ents, err := os.ReadDir(s.Dir)
	if err != nil {
		return nil
	}
	var out []int
	for _, e := range ents {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), "gen-") {
			continue
		}
		n, err := strconv.Atoi(strings.TrimPrefix(e.Name(), "gen-"))
		if err != nil || n < 1 {
			continue
		}
		out = append(out, n)
	}
	sort.Ints(out)
	return out
}

// CurrentGeneration is the generation `current` points at, or 0 when nothing
// has been published. A dangling or foreign symlink reads as 0 — "nothing this
// package published" — never as a guess.
func (s State) CurrentGeneration() int {
	target, err := os.Readlink(s.CurrentLink())
	if err != nil {
		return 0
	}
	base := filepath.Base(target)
	if !strings.HasPrefix(base, "gen-") {
		return 0
	}
	n, err := strconv.Atoi(strings.TrimPrefix(base, "gen-"))
	if err != nil || n < 1 {
		return 0
	}
	if st, err := os.Stat(s.GenDir(n)); err != nil || !st.IsDir() {
		return 0
	}
	return n
}

// NextGeneration is the number the next render would be published as: one
// past the highest generation on disk, so a pruned or reverted generation
// number is never reused.
func (s State) NextGeneration() int {
	gens := s.Generations()
	if len(gens) == 0 {
		return 1
	}
	return gens[len(gens)-1] + 1
}

// ReadGeneration reads the files of generation n off disk.
func (s State) ReadGeneration(n int) (map[string][]byte, error) {
	out := map[string][]byte{}
	for _, rel := range RelPaths() {
		b, err := os.ReadFile(filepath.Join(s.GenDir(n), rel))
		if err != nil {
			return nil, err
		}
		out[rel] = b
	}
	return out, nil
}

// ReadCurrent reads the files the `current` pointer resolves to. ok is false
// when nothing has been published yet — a fact, not an error.
func (s State) ReadCurrent() (map[string][]byte, int, bool) {
	n := s.CurrentGeneration()
	if n == 0 {
		return nil, 0, false
	}
	files, err := s.ReadGeneration(n)
	if err != nil {
		return nil, n, false
	}
	return files, n, true
}

// RegistrySHA256 is the digest of the registry a generation was rendered from:
// taken over registry.Marshal, the same canonical bytes registry.Save writes,
// so an in-memory fleet and the file it came from hash identically.
func RegistrySHA256(f *registry.Fleet) (string, error) {
	b, err := registry.Marshal(f)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	return "sha256:" + hex.EncodeToString(sum[:]), nil
}

// Render renders the next gateway generation from the fleet. It writes
// nothing: the returned Generation carries the exact bytes a publish would
// put in gen-<N>.
func Render(f *registry.Fleet, roots paths.Roots) (*Generation, error) {
	if f == nil {
		return nil, fmt.Errorf("gateway: nil fleet")
	}
	sum, err := RegistrySHA256(f)
	if err != nil {
		return nil, fmt.Errorf("gateway: hashing the registry: %w", err)
	}
	cfg := render.NginxConfig{ProxyDir: roots.ProxyDir}
	tenants, err := render.NginxTenants(f, cfg)
	if err != nil {
		return nil, fmt.Errorf("gateway: rendering %s: %w", FileTenants, err)
	}
	static, err := render.NginxStatic(f, cfg)
	if err != nil {
		return nil, fmt.Errorf("gateway: rendering %s: %w", FileStatic, err)
	}
	g := &Generation{
		N:                  NewState(roots).NextGeneration(),
		RegistryGeneration: f.Generation,
		RegistrySHA256:     sum,
		CreatedAt:          now().UTC().Format(time.RFC3339),
		Generator:          "ragstack-ctl " + version.Version,
		Files:              map[string][]byte{},
	}
	for rel, body := range map[string][]byte{FileTenants: tenants, FileStatic: static} {
		g.Files[rel] = append(g.header(rel), body...)
	}
	return g, nil
}

// header is the provenance block every generated file opens with.
func (g *Generation) header(rel string) []byte {
	var b strings.Builder
	b.WriteString(headerBegin + "\n")
	fmt.Fprintf(&b, "# file:                %s\n", rel)
	fmt.Fprintf(&b, "# generator:           %s\n", g.Generator)
	fmt.Fprintf(&b, "# registry generation: %d\n", g.RegistryGeneration)
	fmt.Fprintf(&b, "# registry sha256:     %s\n", g.RegistrySHA256)
	fmt.Fprintf(&b, "# generated at:        %s\n", g.CreatedAt)
	b.WriteString(headerEnd + "\n")
	return []byte(b.String())
}

// bodyProvenance is the renderer's OWN first line — `# 05-tenants.generated.conf
// — generated by ragstack-ctl from registry generation 14. DO NOT EDIT.` — which
// sits below the header block and carries the registry generation number.
//
// It has to come off with the header, and for the same reason. It is metadata
// about the render, not routing, and leaving it in meant every comparison saw
// a difference whenever the registry generation had moved for ANY reason: a
// tenant renamed elsewhere, a fleet field edited, a second `registry save`. The
// visible damage was at the switch, where the coconut-proxy bootstrap copy —
// rendered from generation N, byte-identical in routing to the generation being
// published from N+1 — was refused as a hand edit; and in `pending_diff`, which
// was then true forever with nothing to publish.
var bodyProvenance = regexp.MustCompile(`\A#[^\n]*\bfrom registry generation \d+[^\n]*\n`)

// StripHeader removes everything that describes the RENDER rather than the
// configuration: the provenance block and the generated file's own
// `from registry generation N` line. Two renders of one routing table differ
// only in those, so this is what every comparison — pending_diff, the unified
// diff, the bootstrap adoption check, the published digest — uses.
func StripHeader(b []byte) []byte {
	s := string(b)
	if strings.HasPrefix(s, headerBegin) {
		if i := strings.Index(s, headerEnd); i >= 0 {
			s = strings.TrimPrefix(s[i+len(headerEnd):], "\n")
		}
	}
	return bodyProvenance.ReplaceAll([]byte(s), nil)
}

// Manifest is the record written beside the generation's files.
func (g *Generation) Manifest() Manifest {
	m := Manifest{
		Generation:         g.N,
		RegistryGeneration: g.RegistryGeneration,
		RegistrySHA256:     g.RegistrySHA256,
		Generator:          g.Generator,
		CreatedAt:          g.CreatedAt,
		Files:              map[string]string{},
	}
	for _, rel := range RelPaths() {
		sum := sha256.Sum256(g.Files[rel])
		m.Files[rel] = "sha256:" + hex.EncodeToString(sum[:])
	}
	return m
}

// SHA256 is the digest the render response publishes and `gateway apply`
// pins: taken over the header-stripped bytes in path order, so it identifies
// the CONFIGURATION, not the minute it was rendered.
func (g *Generation) SHA256() string {
	sum := sha256.New()
	for _, rel := range RelPaths() {
		sum.Write([]byte(rel))
		sum.Write([]byte{0})
		sum.Write(StripHeader(g.Files[rel]))
	}
	return "sha256:" + hex.EncodeToString(sum.Sum(nil))
}

// Write materialises the generation as <state>/gen-<N>/, files and manifest.
// Existing content is overwritten: a half-written generation from a crashed
// publish is not a reason to refuse, and every byte is derived from the
// registry rather than from what is already there.
func (g *Generation) Write(s State) error {
	dir := s.GenDir(g.N)
	for _, rel := range RelPaths() {
		p := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o2770); err != nil {
			return err
		}
		if err := writeFileAtomic(p, g.Files[rel], 0o660); err != nil {
			return err
		}
	}
	b, err := json.MarshalIndent(g.Manifest(), "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(dir, ManifestName), append(b, '\n'), 0o660)
}

// writeFileAtomic writes via a sibling temp file + rename, with the mode set
// BEFORE the rename so the file is never briefly world-readable.
func writeFileAtomic(path string, b []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o2770); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(tmp.Name()) }()
	if _, err := tmp.Write(b); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
