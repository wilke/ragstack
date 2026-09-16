package adopt

// `adopt <t> --readopt --confirm-stores qdrant,elasticsearch,postgres` — the
// operator act that says "the ctl may stop, snapshot and restore this store".
//
// Every capability on every adopted store starts false, and every op that
// would touch a store refuses on that (doctor's capabilities_unconfirmed and,
// since PR-E, stores_unconfirmed). Flipping them is not a formality: a `stop`
// on the wrong leg takes down a store other tenants are using, and a `restore`
// on the wrong leg overwrites their data. So the flip is EARNED, here, from
// three facts read at the moment of confirmation rather than from the row that
// was written at adoption time:
//
//  1. exactly ONE process serves the leg's port. Two pids on one port is two
//     servers — or a port being handed over — and neither is a thing to
//     confirm a capability over.
//  2. that process's argv binds a path under this tenant's OWN data dir. An
//     apptainer instance's `--bind <host>:<container>` is where its storage
//     really is, and a leg whose storage is somewhere else is not this
//     tenant's to stop however the URL is spelled.
//  3. no other registry row names the same store. Two rows pointing at one
//     port is the definition of shared, whatever either row's `ownership`
//     says.
//
// A leg that is not `ownership: exclusive` is refused outright, before any of
// that: a shared store's `stop` capability would be permission to take down
// somebody else's tenant, and no amount of process evidence makes that safe.
//
// `purge` is never set. It is the capability that destroys data, nothing in v1
// asks for it, and a confirmation flow that granted it by default would make
// the one irreversible capability the easiest to acquire.

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// ConfirmableLegs are the store legs `--confirm-stores` accepts, in the order
// a confirmation reports them.
var ConfirmableLegs = []string{"qdrant", "elasticsearch", "postgres"}

// ConfirmOptions are the seams and the surroundings a confirmation needs.
type ConfirmOptions struct {
	// Host is the read-only host surface; nil ⇒ the live host.
	Host hostfacts.Host
	// Fleet is the registry as it stands, for the "no other row names this
	// store" check. Nil means there is no registry yet, which is a legitimate
	// state (the first adoption) and not a reason to skip the check — there is
	// simply no other row to collide with.
	Fleet *registry.Fleet
	// Roots is the deployment layout the live host probe is built from. It is
	// only read when Host is nil.
	Roots paths.Roots
}

// ValidateLegs judges a `--confirm-stores` list without reading the host, so a
// typo is a usage error rather than a failed probe.
func ValidateLegs(legs []string) error {
	if len(legs) == 0 {
		return fmt.Errorf("--confirm-stores takes at least one of %s", strings.Join(ConfirmableLegs, ","))
	}
	seen := map[string]bool{}
	for _, leg := range legs {
		if !containsLeg(ConfirmableLegs, leg) {
			return fmt.Errorf("--confirm-stores: %q is not a store leg (%s)", leg, strings.Join(ConfirmableLegs, ","))
		}
		if seen[leg] {
			return fmt.Errorf("--confirm-stores: %q appears twice", leg)
		}
		seen[leg] = true
	}
	return nil
}

// ConfirmStores re-verifies each named leg of t and, when all of them pass,
// sets {stop, snapshot, restore} on every one. It MUTATES t — the row a commit
// is about to write — and it is all-or-nothing: a run in which any leg fails
// leaves every capability exactly as it found it, because a half-confirmed
// tenant is one whose `fleet start` would bring up some of its stores.
//
// The findings it returns are the evidence, at info level, so an operator who
// asked for a confirmation can see what was actually read.
func ConfirmStores(t *registry.Tenant, legs []string, opts ConfirmOptions) ([]model.Finding, error) {
	if err := ValidateLegs(legs); err != nil {
		return nil, err
	}
	host := opts.Host
	if host == nil {
		host = hostfacts.NewReal(opts.Roots)
	}
	listeners, err := host.Listeners()
	if err != nil {
		return nil, fmt.Errorf("adopt %s: reading the listening sockets: %w", t.Name, err)
	}
	byPort := map[int][]hostfacts.Listener{}
	for _, l := range listeners {
		byPort[l.Port] = append(byPort[l.Port], l)
	}

	var findings []model.Finding
	type pass struct {
		leg  string
		set  func(registry.Capabilities)
		note string
	}
	var passed []pass
	for _, leg := range legs {
		port, owner, err := legPort(t, leg)
		if err != nil {
			return findings, err
		}
		if err := checkExclusive(t, leg, owner); err != nil {
			return findings, err
		}
		l, err := soleListener(t, leg, port, byPort[port])
		if err != nil {
			return findings, err
		}
		bind, err := bindUnderDataDir(t, leg, l)
		if err != nil {
			return findings, err
		}
		if err := noOtherRowNamesIt(opts.Fleet, t, leg, port); err != nil {
			return findings, err
		}
		note := fmt.Sprintf("%s: pid %d on :%d, binding %s", leg, l.Pid, port, bind)
		findings = append(findings, model.Finding{
			Level: model.LevelInfo, Code: "stores_confirmed", Tenant: registry.NullString(t.Name),
			Detail: fmt.Sprintf("%s confirmed: exactly one process (pid %d, %s) serves :%d, its argv binds %s under "+
				"the tenant's data dir, and no other registry row names it. capabilities.{stop,snapshot,restore} "+
				"are set; purge stays false", leg, l.Pid, orDefault(l.User, "owner unreadable"), port, bind),
		})
		passed = append(passed, pass{leg: leg, set: setterFor(t, leg), note: note})
	}

	// Every leg passed: only now is anything written.
	caps := registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
	for _, p := range passed {
		p.set(caps)
	}
	sort.Slice(findings, func(i, j int) bool { return findings[i].Detail < findings[j].Detail })
	return findings, nil
}

// legPort is the port and the recorded ownership of one leg.
func legPort(t *registry.Tenant, leg string) (int, string, error) {
	switch leg {
	case "qdrant":
		return portOf(t.Stores.Qdrant.URL, t.Ports.QdrantHTTP), t.Stores.Qdrant.Ownership, nil
	case "elasticsearch":
		return portOf(t.Stores.Elasticsearch.URL, t.Ports.ESHTTP), t.Stores.Elasticsearch.Ownership, nil
	case "postgres":
		pg := t.Stores.Postgres
		if pg.Kind != registry.PostgresKindLocal {
			return 0, "", fmt.Errorf("adopt %s: the postgres leg is `%s`, not `local`: there is no server of this "+
				"tenant's to confirm (sqlite is files under <data_dir>/state, and an external server belongs to "+
				"somebody else)", t.Name, pg.Kind)
		}
		port := int(pg.Port)
		if port == 0 {
			port = t.Ports.PG
		}
		return port, pg.Ownership, nil
	default:
		return 0, "", fmt.Errorf("adopt %s: %q is not a store leg", t.Name, leg)
	}
}

// setterFor is where a confirmed leg's capabilities land.
func setterFor(t *registry.Tenant, leg string) func(registry.Capabilities) {
	switch leg {
	case "qdrant":
		return func(c registry.Capabilities) { t.Stores.Qdrant.Capabilities = c }
	case "elasticsearch":
		return func(c registry.Capabilities) { t.Stores.Elasticsearch.Capabilities = c }
	default:
		return func(c registry.Capabilities) { t.Stores.Postgres.Capabilities = c }
	}
}

func checkExclusive(t *registry.Tenant, leg, ownership string) error {
	if ownership == registry.OwnershipExclusive {
		return nil
	}
	return fmt.Errorf("adopt %s: the %s leg is `%s`, not exclusive: confirming `stop` on it would be confirming "+
		"that the ctl may take down a store other tenants are using. A shared leg keeps stop:false, and the tenant "+
		"is handed over without it", t.Name, leg, orDefault(ownership, "unknown"))
}

// soleListener is fact (1): exactly one process, and this account can see it.
func soleListener(t *registry.Tenant, leg string, port int, ls []hostfacts.Listener) (hostfacts.Listener, error) {
	if len(ls) == 0 {
		return hostfacts.Listener{}, fmt.Errorf("adopt %s: nothing is listening on :%d, so there is no %s process to "+
			"confirm. Start the store and run this again", t.Name, port, leg)
	}
	pids := map[int]bool{}
	for _, l := range ls {
		pids[l.Pid] = true
	}
	if pids[0] {
		return hostfacts.Listener{}, fmt.Errorf("adopt %s: :%d is held by a process this account cannot attribute "+
			"(/proc/<pid>/fd is not readable across accounts), so nothing about it can be verified. Run the "+
			"confirmation as the account that started %s", t.Name, port, leg)
	}
	if len(pids) != 1 {
		return hostfacts.Listener{}, fmt.Errorf("adopt %s: :%d is served by %d different processes (%s): that is two "+
			"servers on one port, or a handover in progress, and neither is something to confirm a capability over",
			t.Name, port, len(pids), joinPids(pids))
	}
	// Several SOCKETS of one process (IPv4 and IPv6) are one server; the first
	// carries the argv either way.
	return ls[0], nil
}

// bindRE finds an apptainer `--bind <host>:<container>` in either spelling.
// The host side is everything up to the first colon; apptainer's own grammar
// allows a third `:ro` field, which is why the split is on the FIRST colon and
// the rest is ignored.
var bindRE = regexp.MustCompile(`^--bind(?:=(.*))?$`)

// bindUnderDataDir is fact (2): the process's argv binds a path under this
// tenant's data dir.
//
// The argv, not the URL: a URL is what the tenant was CONFIGURED to dial, and
// the question here is where the process on the other end actually keeps its
// files. An instance started against another tenant's storage answers the same
// URL and is not this tenant's to stop.
func bindUnderDataDir(t *registry.Tenant, leg string, l hostfacts.Listener) (string, error) {
	var binds []string
	for i, arg := range l.Cmdline {
		m := bindRE.FindStringSubmatch(arg)
		if m == nil {
			continue
		}
		spec := m[1]
		if spec == "" {
			if i+1 >= len(l.Cmdline) {
				continue
			}
			spec = l.Cmdline[i+1]
		}
		host, _, _ := strings.Cut(spec, ":")
		binds = append(binds, host)
		if under(host, t.DataDir) {
			return host, nil
		}
	}
	if len(binds) == 0 {
		return "", fmt.Errorf("adopt %s: pid %d serves the %s port but its command line has no --bind at all, so "+
			"nothing says the files it is writing are this tenant's. A store the ctl may stop has to be one it can "+
			"see the storage of", t.Name, l.Pid, leg)
	}
	return "", fmt.Errorf("adopt %s: pid %d serves the %s port but binds %s, none of which is under this tenant's "+
		"data dir %s: it is not this tenant's store however its URL is spelled",
		t.Name, l.Pid, leg, strings.Join(binds, ", "), t.DataDir)
}

// noOtherRowNamesIt is fact (3): the registry has one claimant for this port.
func noOtherRowNamesIt(f *registry.Fleet, t *registry.Tenant, leg string, port int) error {
	if f == nil {
		return nil
	}
	var others []string
	for name, row := range f.Tenants {
		if name == t.Name || row == nil || row.State == "decommissioned" {
			continue
		}
		for _, claim := range []struct {
			what string
			port int
		}{
			{"qdrant", portOf(row.Stores.Qdrant.URL, 0)},
			{"elasticsearch", portOf(row.Stores.Elasticsearch.URL, 0)},
			{"postgres", int(row.Stores.Postgres.Port)},
		} {
			if claim.port != 0 && claim.port == port {
				others = append(others, fmt.Sprintf("%s (%s)", name, claim.what))
			}
		}
	}
	if len(others) == 0 {
		return nil
	}
	sort.Strings(others)
	return fmt.Errorf("adopt %s: :%d is also named by %s, so the %s leg is SHARED whatever this row's ownership "+
		"says. Confirming it would be confirming the ctl may stop a store those tenants are using",
		t.Name, port, strings.Join(others, ", "), leg)
}

// ---------------------------------------------------------------- helpers

// portOf is the port of a store URL, or fallback when it has none.
func portOf(raw string, fallback int) int {
	if raw == "" {
		return fallback
	}
	i := strings.LastIndex(raw, ":")
	if i < 0 {
		return fallback
	}
	port, err := strconv.Atoi(strings.TrimSuffix(strings.SplitN(raw[i+1:], "/", 2)[0], "/"))
	if err != nil || port == 0 {
		return fallback
	}
	return port
}

// containsLeg is the small membership test ValidateLegs needs. Local to this
// file: the adopt package has no general "contains" and one leg list does not
// justify introducing one.
func containsLeg(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}

func joinPids(pids map[int]bool) string {
	out := make([]string, 0, len(pids))
	for pid := range pids {
		out = append(out, strconv.Itoa(pid))
	}
	sort.Strings(out)
	return strings.Join(out, ", ")
}
