package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// Contract txn_state values (gateway_status.json).
const (
	TxnStateNone       = "none"
	TxnStateComplete   = "complete"
	TxnStateIncomplete = "incomplete"
	TxnStateRepaired   = "repaired"
)

// Status is GET /v1/gateway: what is published, whether the registry has
// moved past it, and what the nginx master looks like.
func Status(roots paths.Roots, f *registry.Fleet) (*model.GatewayStatus, error) {
	return StatusWith(roots, f, Options{Roots: roots, Sig: NewRealSignaller()})
}

// StatusWith is Status with the host seams supplied — the form the tests and
// the daemon (which builds its drivers once) use.
//
// It is a READ. It does not repair.
//
// It used to: every call ran State.Repair, which MOVES `current` — the symlink
// nginx resolves both generated includes through. So GET /v1/gateway, an
// operation the contract gives to the VIEWER role, changed what the gateway
// would serve at its next reload, unlocked, from a dashboard poll, possibly
// while an operator's publish was between its switch and its probe. An
// incomplete publication is reported here as `incomplete` and left exactly as
// it is; `ragstack-ctl gateway repair` (which takes the lock) is what puts the
// pointer back.
func StatusWith(roots paths.Roots, f *registry.Fleet, opts Options) (*model.GatewayStatus, error) {
	opts.Roots = roots
	opts.defaults()
	st := NewState(roots)

	txn, haveTxn, err := st.ReadTxn()
	if err != nil {
		return nil, err
	}

	out := &model.GatewayStatus{
		Generation:   st.CurrentGeneration(),
		TxnState:     TxnStateNone,
		PendingDiff:  true,
		Routes:       Routes(f),
		LegacyRoutes: LegacyRoutes(f),
	}
	if haveTxn {
		switch {
		case !txn.Complete():
			out.TxnState = TxnStateIncomplete
		case txn.Repaired:
			out.TxnState = TxnStateRepaired
		default:
			out.TxnState = TxnStateComplete
		}
		at := txn.FinishedAt
		if at == "" {
			at = txn.StartedAt
		}
		out.PublishedAt, out.PublishedBy = strPtr(at), strPtr(txn.PublishedBy)
		out.Nginx.ConfigOK = txn.ConfigOK
		out.Nginx.LastReloadAt = strPtr(txn.ReloadedAt)
	}

	// registry_generation is, per the schema, the generation the PUBLISHED
	// files were rendered from — not today's registry. The difference between
	// the two is exactly what pending_diff reports.
	if out.Generation > 0 {
		if m, err := readManifest(st, out.Generation); err == nil {
			out.RegistryGeneration = m.RegistryGeneration
		}
	}

	// nginx master identity, from the pidfile and /proc.
	if pid, err := readPID(opts.PIDFile); err == nil {
		if info, err := opts.Sig.Proc(pid); err == nil && info.Exists && looksLikeNginx(info) {
			p, u := pid, info.UID
			out.Nginx.MasterPID, out.Nginx.MasterUID = &p, &u
		}
	}

	d, err := DiffDetail(roots, f)
	if err != nil {
		return nil, err
	}
	out.PendingDiff = d.Response.Changed
	return out, nil
}

func strPtr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// Routes projects the registry's tenants onto the contract's Route rows — the
// same rows the generated maps carry.
func Routes(f *registry.Fleet) []model.GatewayRoute {
	names := make([]string, 0, len(f.Tenants))
	for n := range f.Tenants {
		names = append(names, n)
	}
	sort.Strings(names)
	// Display order first, then anything the display order does not mention.
	ordered := make([]string, 0, len(names))
	seen := map[string]bool{}
	for _, n := range f.DisplayOrder {
		if _, ok := f.Tenants[n]; ok && !seen[n] {
			ordered, seen[n] = append(ordered, n), true
		}
	}
	for _, n := range names {
		if !seen[n] {
			ordered = append(ordered, n)
		}
	}
	out := make([]model.GatewayRoute, 0, len(ordered))
	for _, n := range ordered {
		t := f.Tenants[n]
		out = append(out, model.GatewayRoute{
			Name: t.Name, API: t.Ports.API, UI: nullablePort(int(t.UI.Port)),
			UIMode: t.UI.Mode, Readonly: false, Status: RouteStatus(t.State),
		})
	}
	return out
}

// LegacyRoutes projects the registry's legacy rows. Their status is the
// registry's own (`active`/`retired`): a legacy row is a hand-run service the
// ctl does not manage, so reporting anything else would be inventing a fact.
func LegacyRoutes(f *registry.Fleet) []model.GatewayRoute {
	out := make([]model.GatewayRoute, 0, len(f.LegacyRoutes))
	for _, l := range f.LegacyRoutes {
		out = append(out, model.GatewayRoute{
			Name: l.Name, API: l.API, UI: nullablePort(l.UI),
			UIMode: registry.UIModeExternal, Readonly: l.Readonly, Status: l.Status,
		})
	}
	return out
}

// RouteStatus is what the gateway is told to expect of a tenant.
func RouteStatus(state string) string {
	switch state {
	case "active":
		return "active"
	case "decommissioned":
		return "retired"
	default:
		return "maintenance"
	}
}

func nullablePort(p int) *int {
	if p == 0 {
		return nil
	}
	return &p
}

// DiffResult is Diff plus the two things the contract's response has no room
// for: what the render was compared against, and whether a comparison against
// the hand-written maps came out semantically identical (PR-B's go/no-go).
type DiffResult struct {
	Response     model.GatewayRenderResponse
	ComparedTo   string
	SemanticNoop bool
	Notes        []string
}

// Diff renders the next generation and diffs it against what is published.
func Diff(roots paths.Roots, f *registry.Fleet) (*model.GatewayRenderResponse, error) {
	d, err := DiffDetail(roots, f)
	if err != nil {
		return nil, err
	}
	return &d.Response, nil
}

// DiffDetail is Diff with the semantic verdict.
//
// Before the first publication there is no generation to diff against, so the
// comparison is made against the LIVE hand-written maps: the three
// `map $tenant …` tables in conf.d/00-maps.conf, parsed and compared as data.
// That is the only comparison that can answer PR-B's question — "does moving
// the maps into a generated include change what the gateway routes?" — because
// the two files are formatted nothing alike and a textual diff between them is
// noise.
func DiffDetail(roots paths.Roots, f *registry.Fleet) (*DiffResult, error) {
	gen, err := Render(f, roots)
	if err != nil {
		return nil, err
	}
	st := NewState(roots)
	out := &DiffResult{Response: model.GatewayRenderResponse{
		Generation:         gen.N,
		RegistryGeneration: gen.RegistryGeneration,
		SHA256:             gen.SHA256(),
		Files:              []model.GatewayFile{},
	}}

	current, curN, published := st.ReadCurrent()
	if published {
		out.ComparedTo = fmt.Sprintf("gen-%d", curN)
		for _, rel := range RelPaths() {
			diff := unified(
				string(StripHeader(current[rel])), string(StripHeader(gen.Files[rel])),
				fmt.Sprintf("current/%s", rel), fmt.Sprintf("gen-%d/%s", gen.N, rel))
			out.Response.Files = append(out.Response.Files, model.GatewayFile{Path: rel, Diff: diff})
			if diff != "" {
				out.Response.Changed = true
			}
		}
		out.SemanticNoop = !out.Response.Changed
		return out, nil
	}

	// Nothing published yet: whole-file additions, plus the semantic verdict
	// against the live hand-written maps.
	for _, rel := range RelPaths() {
		out.Response.Files = append(out.Response.Files, model.GatewayFile{
			Path: rel,
			Diff: unified("", string(StripHeader(gen.Files[rel])), "/dev/null", fmt.Sprintf("gen-%d/%s", gen.N, rel)),
		})
	}
	out.Response.Changed = true

	// The live routing lives in whichever file the proxy tree actually carries
	// it: the generated include (conf.d/05-tenants.generated.conf) once
	// coconut-proxy's deploy has landed — a regular bootstrap file before the
	// first `gateway apply`, or a symlink into a published generation after
	// one — and the hand-written conf.d/00-maps.conf before that deploy.
	// doctor.GatewayMaps already tries them in that order and reads straight
	// through a symlink, so this one call is the same "vs published
	// generation" comparison whether or not the ctl itself did the publishing.
	liveMaps, ok, err := doctor.GatewayMaps(roots.ProxyDir)
	if err != nil {
		// The live routing is THERE and unreadable (EACCES, a dangling
		// symlink from a half-finished deploy). "No live maps found" would be
		// a lie, and a semantic-no-op verdict reached by comparing against
		// nothing is the dangerous half of that lie: it is what tells an
		// operator the first publish changes no route.
		out.ComparedTo = "the live tenant maps under " + roots.ProxyDir + " could not be read"
		out.SemanticNoop = false
		out.Notes = append(out.Notes, fmt.Sprintf(
			"%s: %v — the live routing could NOT be read, so nothing was compared and this is not a known no-op",
			roots.ProxyDir, err))
		return out, nil
	}
	if !ok {
		out.ComparedTo = "nothing published yet; no live tenant maps found under " + roots.ProxyDir
		return out, nil
	}
	out.ComparedTo = liveMaps.Source

	// The two tenant JSON lists are routing of their own, and they do not
	// necessarily live in the same file as the maps: pre-deploy they are
	// hand-written `return 200 '…'` bodies in routes.conf, post-deploy they
	// are the include's map values. doctor.LiveTenantLists applies that
	// precedence once, for this and for adopt's display_order alike, and
	// fills each list INDEPENDENTLY — a mixed tree really does carry one of
	// them in each file, and taking both from whichever file answered first
	// dropped the other.
	lists, lerr := doctor.LiveTenantLists(roots.ProxyDir)
	switch {
	case lerr != nil:
		out.Notes = append(out.Notes, fmt.Sprintf("%s: %v — the two tenant JSON lists were NOT compared", roots.ProxyDir, lerr))
	default:
		if lists.NamesJSON != "" {
			liveMaps.NamesJSON = lists.NamesJSON
		}
		if lists.TenantsJSON != "" {
			liveMaps.TenantsJSON = lists.TenantsJSON
		}
		if src := lists.Source(); src != "" && src != liveMaps.Source {
			out.ComparedTo = liveMaps.Source + " (tenant lists from " + src + ")"
		}
	}

	// A textual difference in the static-UI snippet is a routing difference
	// too — that file decides which alias block serves which tenant's
	// `vite build`, and it appears in NEITHER map. Comparing only the maps
	// called a first publish that adds, drops or repoints an alias block a
	// semantic no-op.
	staticDiff := staticNotes(liveStatic(roots.ProxyDir), gen.Files[FileStatic])
	out.Notes = append(out.Notes, staticDiff...)

	noop, notes := semanticEqual(liveMaps,
		doctor.ParseTenantMaps(gen.Files[FileTenants], fmt.Sprintf("gen-%d/%s", gen.N, FileTenants)))
	out.SemanticNoop, out.Notes = noop && len(staticDiff) == 0, append(out.Notes, notes...)
	return out, nil
}

// liveStatic reads the static-UI snippet the proxy tree carries today, through
// a symlink if that is what it is. An absent file is nil: "the tree serves no
// static UI", which is a comparable state, not an error.
func liveStatic(proxyDir string) []byte {
	b, err := os.ReadFile(filepath.Join(proxyDir, filepath.FromSlash(FileStatic)))
	if err != nil {
		return nil
	}
	return b
}

// staticAliasBlock captures one `location ^~ <prefix> { … }` and its body;
// aliasDirective picks the `alias <dir>;` out of that body. The generated
// snippet never nests a block inside one of these, which is what makes the
// non-greedy match exact rather than approximate.
var (
	staticAliasBlock = regexp.MustCompile(`(?s)location\s+\^~\s+(\S+)\s*\{(.*?)
\}`)
	aliasDirective = regexp.MustCompile(`alias\s+([^;]+);`)
)

func staticBlocks(b []byte) map[string]string {
	out := map[string]string{}
	for _, m := range staticAliasBlock.FindAllSubmatch(b, -1) {
		alias := ""
		if a := aliasDirective.FindSubmatch(m[2]); a != nil {
			alias = strings.TrimSpace(string(a[1]))
		}
		out[string(m[1])] = alias
	}
	return out
}

// staticNotes says, in words, how the live static-UI snippet differs from the
// render. It returns something for ANY textual difference once the provenance
// header is off: the per-prefix alias comparison names the ones it can, and a
// difference it cannot attribute to a block is still reported, because
// "different and unexplained" must not round down to "no-op".
func staticNotes(live, rendered []byte) []string {
	l, r := directives(StripHeader(live)), directives(StripHeader(rendered))
	if l == r {
		return nil
	}
	var notes []string
	a, b := staticBlocks([]byte(l)), staticBlocks([]byte(r))
	for _, p := range sortedKeys(a) {
		switch {
		case b[p] == "" && !hasKey(b, p):
			notes = append(notes, fmt.Sprintf("%s: %s is served from %s live and has no block in the render", FileStatic, p, a[p]))
		case b[p] != a[p]:
			notes = append(notes, fmt.Sprintf("%s: %s alias %s live -> %s rendered", FileStatic, p, a[p], b[p]))
		}
	}
	for _, p := range sortedKeys(b) {
		if !hasKey(a, p) {
			notes = append(notes, fmt.Sprintf("%s: %s is new in the render (alias %s)", FileStatic, p, b[p]))
		}
	}
	if notes == nil {
		notes = append(notes, fmt.Sprintf("%s: the live file and the render differ in text outside the alias blocks", FileStatic))
	}
	return notes
}

// directives drops comment and blank lines. nginx routes on directives, and
// the two files carry a paragraph of prose each — an ABSENT live snippet and a
// render whose only content is that prose serve exactly the same thing (no
// static UI at all), and calling that a difference would make every pre-deploy
// tree a non-no-op. Everything a comment is not still counts.
func directives(b []byte) string {
	var keep []string
	for _, line := range strings.Split(string(b), "\n") {
		t := strings.TrimSpace(line)
		if t == "" || strings.HasPrefix(t, "#") {
			continue
		}
		keep = append(keep, t)
	}
	return strings.Join(keep, "\n")
}

func hasKey(m map[string]string, k string) bool {
	_, ok := m[k]
	return ok
}

// semanticEqual compares two parsed map sets and reports every difference in
// words. Equal tables mean the first published generation routes exactly what
// the hand-written file routes today — a semantic no-op.
//
// "Equal" means the whole routing decision: the ADDRESS each name resolves to
// (host included — comparing ports alone called `10.0.0.5:24040` and
// `127.0.0.1:24040` the same route), the read-only flags, and the two tenant
// JSON lists that decide which tenants a client is told about at all.
func semanticEqual(live, rendered doctor.TenantMaps) (bool, []string) {
	var notes []string
	cmpAddr := func(kind string, a, b map[string]string) {
		for _, n := range sortedKeys(a) {
			switch {
			case b[n] == "":
				notes = append(notes, fmt.Sprintf("%s: %s is routed to %s live and absent from the render", kind, n, a[n]))
			case b[n] != a[n]:
				notes = append(notes, fmt.Sprintf("%s: %s %s live -> %s rendered", kind, n, a[n], b[n]))
			}
		}
		for _, n := range sortedKeys(b) {
			if a[n] == "" {
				notes = append(notes, fmt.Sprintf("%s: %s is new in the render (%s)", kind, n, b[n]))
			}
		}
	}
	cmpAddr("$tenant_api", live.APIAddr, rendered.APIAddr)
	cmpAddr("$tenant_ui", live.UIAddr, rendered.UIAddr)
	notes = append(notes, cmpJSONList("$tenants_names_json", live.NamesJSON, rendered.NamesJSON)...)
	notes = append(notes, cmpJSONList("$tenants_json", live.TenantsJSON, rendered.TenantsJSON)...)
	for _, n := range sortedKeys(live.Readonly) {
		if live.Readonly[n] != rendered.Readonly[n] {
			notes = append(notes, fmt.Sprintf("$tenant_readonly: %s %v live -> %v rendered", n, live.Readonly[n], rendered.Readonly[n]))
		}
	}
	for _, n := range sortedKeys(rendered.Readonly) {
		if _, ok := live.Readonly[n]; !ok && rendered.Readonly[n] {
			notes = append(notes, fmt.Sprintf("$tenant_readonly: %s is new in the render", n))
		}
	}
	return len(notes) == 0, notes
}

// cmpJSONList compares two JSON literals as DATA, not as text: the live one is
// hand-written inside an nginx `return` body and the rendered one comes out of
// encoding/json, so they differ in whitespace for reasons that route nothing.
// A literal missing on either side is reported as not compared rather than as
// a difference — the caller already said why it could not be read.
func cmpJSONList(kind, live, rendered string) []string {
	if live == "" || rendered == "" {
		if live == "" && rendered == "" {
			return nil
		}
		return []string{fmt.Sprintf("%s: present on only one side (live %q, rendered %q); not compared",
			kind, truncate(live), truncate(rendered))}
	}
	a, aerr := canonicalJSON(live)
	b, berr := canonicalJSON(rendered)
	if aerr != nil || berr != nil {
		if live != rendered {
			return []string{fmt.Sprintf("%s: differs and at least one side is not JSON (%v/%v)", kind, aerr, berr)}
		}
		return nil
	}
	if a == b {
		return nil
	}
	return []string{fmt.Sprintf("%s: live %s -> rendered %s", kind, truncate(a), truncate(b))}
}

func canonicalJSON(s string) (string, error) {
	var v any
	if err := json.Unmarshal([]byte(s), &v); err != nil {
		return "", err
	}
	b, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// truncate keeps a note readable: the tenant table literal is several hundred
// characters and the difference is never at the end of it.
func truncate(s string) string {
	const max = 160
	if len(s) <= max {
		return s
	}
	return s[:max] + "…"
}

// ---------------------------------------------------------------- unified diff

// unified renders a unified diff with three lines of context. The files are a
// few dozen lines each, so a plain LCS is both exact and instant, and it keeps
// the package on the standard library.
func unified(a, b, nameA, nameB string) string {
	if a == b {
		return ""
	}
	ops := lcsOps(splitLines(a), splitLines(b))
	const ctx = 3

	// Number every operation on both sides as it is produced, so a hunk
	// header can be read straight off the first operation it contains.
	aNo, bNo := make([]int, len(ops)), make([]int, len(ops))
	ai, bi := 0, 0
	for i, op := range ops {
		if op.kind != '+' {
			ai++
		}
		if op.kind != '-' {
			bi++
		}
		aNo[i], bNo[i] = ai, bi
	}

	// Group changed operations into hunks, padding each with ctx lines of
	// context and merging groups that would otherwise overlap.
	type group struct{ from, to int }
	var groups []group
	for i := 0; i < len(ops); i++ {
		if ops[i].kind == ' ' {
			continue
		}
		from, to := i-ctx, i+ctx
		if from < 0 {
			from = 0
		}
		if to > len(ops)-1 {
			to = len(ops) - 1
		}
		if n := len(groups); n > 0 && from <= groups[n-1].to+1 {
			if to > groups[n-1].to {
				groups[n-1].to = to
			}
			continue
		}
		groups = append(groups, group{from, to})
	}

	var out strings.Builder
	fmt.Fprintf(&out, "--- %s\n+++ %s\n", nameA, nameB)
	for _, g := range groups {
		var aCount, bCount int
		for i := g.from; i <= g.to; i++ {
			if ops[i].kind != '+' {
				aCount++
			}
			if ops[i].kind != '-' {
				bCount++
			}
		}
		// A hunk that opens with an insertion has no line of its own on the
		// `a` side, so its a-number is the line it comes AFTER — which is
		// what aNo already holds (`ai` did not advance for a '+').
		fmt.Fprintf(&out, "@@ -%d,%d +%d,%d @@\n", aNo[g.from], aCount, bNo[g.from], bCount)
		for i := g.from; i <= g.to; i++ {
			out.WriteString(string(ops[i].kind) + ops[i].line + "\n")
		}
	}
	return out.String()
}

func splitLines(s string) []string {
	if s == "" {
		return nil
	}
	return strings.Split(strings.TrimSuffix(s, "\n"), "\n")
}

type diffOp struct {
	kind byte // ' ', '-', '+'
	line string
}

// lcsOps is the classic longest-common-subsequence table. Config files are
// small; clarity beats cleverness here.
func lcsOps(a, b []string) []diffOp {
	n, m := len(a), len(b)
	table := make([][]int, n+1)
	for i := range table {
		table[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			if a[i] == b[j] {
				table[i][j] = table[i+1][j+1] + 1
			} else if table[i+1][j] >= table[i][j+1] {
				table[i][j] = table[i+1][j]
			} else {
				table[i][j] = table[i][j+1]
			}
		}
	}
	var ops []diffOp
	i, j := 0, 0
	for i < n && j < m {
		switch {
		case a[i] == b[j]:
			ops = append(ops, diffOp{' ', a[i]})
			i, j = i+1, j+1
		case table[i+1][j] >= table[i][j+1]:
			ops = append(ops, diffOp{'-', a[i]})
			i++
		default:
			ops = append(ops, diffOp{'+', b[j]})
			j++
		}
	}
	for ; i < n; i++ {
		ops = append(ops, diffOp{'-', a[i]})
	}
	for ; j < m; j++ {
		ops = append(ops, diffOp{'+', b[j]})
	}
	return ops
}
