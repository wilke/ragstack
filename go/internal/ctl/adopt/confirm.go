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
//  2. that process has a directory under this tenant's OWN data dir MOUNTED
//     where a store of its kind keeps its data. The evidence is
//     /proc/<pid>/mountinfo, not the command line: the process holding the
//     port is the one inside the container (`./qdrant`, the elasticsearch JVM,
//     `postgres`) and carries no `--bind` at all, because the `apptainer
//     instance run` that set the mounts up exited long ago. A leg whose
//     storage is somewhere else is not this tenant's to stop however its URL
//     is spelled.
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
		bind, err := storageUnderDataDir(host, t, leg, l)
		if err != nil {
			return findings, err
		}
		if err := noOtherRowNamesIt(opts.Fleet, t, leg, port); err != nil {
			return findings, err
		}
		note := fmt.Sprintf("%s: pid %d on :%d, binding %s", leg, l.Pid, port, bind)
		findings = append(findings, model.Finding{
			Level: model.LevelInfo, Code: "stores_confirmed", Tenant: registry.NullString(t.Name),
			Detail: fmt.Sprintf("%s confirmed: exactly one process (pid %d, %s) serves :%d, it has %s — inside the "+
				"tenant's own data dir — and no other registry row names it. "+
				"capabilities.{stop,snapshot,restore} are set; purge stays false",
				leg, l.Pid, orDefault(l.User, "owner unreadable"), port, bind),
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

// dataPaths are the container paths each store kind keeps its DATA at, in the
// images this deployment runs. A bind of the tenant's own directory at one of
// them is the strongest statement available that the process on the port is
// this tenant's store: it is not merely mounting something of the tenant's, it
// is mounting it where its storage lives.
//
// They are the destinations the tenants' own launchers and `render.StoreArgv`
// both use. A leg whose image keeps data elsewhere fails this check and says
// what it found, which is the right outcome: the ctl must not confirm `stop`
// on a store whose storage it cannot point at.
var dataPaths = map[string][]string{
	"qdrant":        {"/qdrant/storage"},
	"elasticsearch": {"/usr/share/elasticsearch/data"},
	"postgres":      {"/var/lib/postgresql/data"},
}

// bindRE finds an apptainer `--bind <host>:<container>` in either spelling. It
// is the FALLBACK source (see storageUnderDataDir): a process that is itself an
// `apptainer instance run` carries its binds on the command line, and one that
// is not containerized at all carries whatever path it was given.
var bindRE = regexp.MustCompile(`^--bind(?:=(.*))?$`)

// storageUnderDataDir is fact (2): the process serving this port keeps its data
// inside the tenant's own data dir.
//
// The evidence is the process's MOUNTS, not its command line. Every store on
// this host runs inside an apptainer instance, and the process holding the port
// is the one INSIDE the container — `./qdrant`, the elasticsearch JVM,
// `postgres`. Its argv carries no `--bind`, because the `apptainer instance run
// --bind …` that set the mounts up is a different process that exited long ago.
// Reading the argv found nothing for every live leg, which made the whole
// confirmation unreachable on the only host it exists for. What survives is
// /proc/<pid>/mountinfo, and the account that runs this op (--direct, as the
// owner of the tree) is exactly the account allowed to read it.
//
// The argv scan is kept as a second source for the shapes mountinfo cannot
// describe: an `apptainer instance run` process itself, and a store running
// directly on the host with its path on the command line.
func storageUnderDataDir(host hostfacts.Host, t *registry.Tenant, leg string, l hostfacts.Listener) (string, error) {
	binds, err := host.StorageBinds(l.Pid)
	if err != nil {
		return "", fmt.Errorf("adopt %s: reading the mounts of pid %d (the %s process): %w", t.Name, l.Pid, leg, err)
	}
	var mine []hostfacts.StorageBind
	for _, b := range binds {
		if under(b.HostPath, t.DataDir) {
			mine = append(mine, b)
		}
	}
	// The strong form: the tenant's own directory mounted where this kind of
	// store keeps its data.
	for _, b := range mine {
		for _, want := range dataPaths[leg] {
			if b.ContainerPath == want {
				return fmt.Sprintf("%s at %s", b.HostPath, b.ContainerPath), nil
			}
		}
	}
	if len(mine) > 0 {
		// Something of the tenant's is mounted, but not its storage. That is an
		// anomaly worth seeing rather than a capability worth confirming: a
		// qdrant with only its snapshots directory bound is writing its
		// collections somewhere this tenant does not own.
		return "", fmt.Errorf("adopt %s: pid %d serves the %s port and mounts %s from this tenant's data dir, but "+
			"nothing at %s, where a %s keeps its data: its storage is somewhere else",
			t.Name, l.Pid, leg, describeBinds(mine), strings.Join(dataPaths[leg], " or "), leg)
	}
	// Fallback: the command line, for a process that IS the apptainer starter
	// or is not containerized at all.
	if path, ok := argvBindUnderDataDir(t, l); ok {
		return path + " (from the command line)", nil
	}
	if len(binds) == 0 {
		return "", fmt.Errorf("adopt %s: pid %d serves the %s port but has no mounts and no --bind on its command "+
			"line, so nothing says the files it is writing are this tenant's. A store the ctl may stop has to be "+
			"one it can see the storage of", t.Name, l.Pid, leg)
	}
	return "", fmt.Errorf("adopt %s: pid %d serves the %s port but mounts nothing under this tenant's data dir %s "+
		"(it mounts %s): it is not this tenant's store however its URL is spelled",
		t.Name, l.Pid, leg, t.DataDir, describeBinds(binds))
}

// argvBindUnderDataDir is the command-line source: `--bind <host>:<container>`.
func argvBindUnderDataDir(t *registry.Tenant, l hostfacts.Listener) (string, bool) {
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
		hostPath, _, _ := strings.Cut(spec, ":")
		if under(hostPath, t.DataDir) {
			return hostPath, true
		}
	}
	return "", false
}

// describeBinds renders a mount list for a refusal, capped so a container with
// thirty mounts does not produce a thirty-line error.
func describeBinds(binds []hostfacts.StorageBind) string {
	const max = 6
	out := make([]string, 0, max+1)
	for i, b := range binds {
		if i == max {
			out = append(out, fmt.Sprintf("… and %d more", len(binds)-max))
			break
		}
		out = append(out, b.HostPath+" at "+b.ContainerPath)
	}
	return strings.Join(out, ", ")
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
		// Each leg's port RESOLVED the same way this tenant's was: the URL
		// when it has one, the row's own port block when it does not. A row
		// that records a store only through its block — every tenant does,
		// and `stores.postgres.port` is null for a `local` leg adopted before
		// the port was written down — would otherwise claim nothing and let
		// the ctl confirm `stop` on a store two rows share.
		for _, claim := range []struct {
			what string
			port int
		}{
			{"qdrant", portOf(row.Stores.Qdrant.URL, row.Ports.QdrantHTTP)},
			{"elasticsearch", portOf(row.Stores.Elasticsearch.URL, row.Ports.ESHTTP)},
			{"postgres", pgPortOf(row)},
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

// pgPortOf is a row's relational-store port: the recorded one, else the port
// block's +5, and 0 for a tenant with no server of its own at all (a `sqlite`
// or `external` relational store claims no port on this host).
func pgPortOf(row *registry.Tenant) int {
	if row.Stores.Postgres.Kind != registry.PostgresKindLocal {
		return 0
	}
	if p := int(row.Stores.Postgres.Port); p != 0 {
		return p
	}
	return row.Ports.PG
}

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
