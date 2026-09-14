package render

// MIGRATE.md — the page an operator reads when the bundle is all they have.
//
// A backup bundle is self-describing JSON, which is exactly the wrong format
// for the situation it exists for: the host is gone, or the tenant is being
// rebuilt somewhere else, and somebody has to know what this directory is,
// which ports and stores it belonged to, and what command turns it back into a
// running tenant. manifest.json answers all of that to a program; MIGRATE.md
// answers it to a person, in the bundle, next to the data.
//
// It carries NO secret and no absolute host path except the ones the registry
// row already spells (data_dir, worktree): the bundle is relocatable, and a
// runbook that hard-coded /rag would be wrong on the host it is read on.

import (
	"bytes"
	"fmt"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// MigrateInfo is what MIGRATE.md says beyond the registry row itself: the
// bundle it belongs to and what that bundle holds.
type MigrateInfo struct {
	// BundleID is `<ts>-<kind>`, the bundle directory's basename.
	BundleID string
	// Kind is backup|pre-update|recovery.
	Kind string
	// CreatedAt is the RFC3339 stamp the manifest carries.
	CreatedAt string
	// CtlVersion is the binary that wrote the bundle.
	CtlVersion string
	// Fenced says whether the tenant was stopped for the duration. Only a
	// fenced bundle may be restored from.
	Fenced bool
	// ArtifactID is the prepared artifact the tenant ran, "" when unknown.
	ArtifactID string
	// Collections and Indices are the store inventory captured.
	Collections []string
	Indices     []string
	// SQLite lists the state files in the bundle, by base name.
	SQLite []string
	// Postgres names the dump inside the bundle ("" when the tenant keeps its
	// relational state in SQLite).
	Postgres string
	// SecretsIncluded says whether secrets.age is in the bundle. When it is
	// false the restore mints fresh credentials and the old ones are gone.
	SecretsIncluded bool
	// External lists what the bundle does NOT contain, as "<ref> (<reason>)".
	External []string
}

// MigrateMD renders the bundle's runbook.
func MigrateMD(t *registry.Tenant, info MigrateInfo) ([]byte, error) {
	if t == nil {
		return nil, fmt.Errorf("MIGRATE.md: no tenant row")
	}
	var b bytes.Buffer
	p := func(format string, a ...any) { fmt.Fprintf(&b, format+"\n", a...) }

	p("# %s — backup bundle %s", t.Name, info.BundleID)
	p("")
	p("Written by ragstack-ctl %s at %s. Kind: %s. Fenced: %v.", info.CtlVersion, info.CreatedAt, info.Kind, info.Fenced)
	p("")
	if !info.Fenced {
		p("> **best effort.** Nothing stopped this tenant writing while the bundle was taken, so it is")
		p("> `best_effort: true`: it can be inspected, it can never be restored from, and it satisfies no")
		p("> prerequisite of handover, migrate or decommission. Take a `--fence` bundle for that.")
		p("")
	}

	p("## What this is")
	p("")
	p("One tenant of the ragstack fleet: its stores' snapshots, its SQLite state, its public config and")
	p("its rendered units. Every path inside the bundle is relative to its own root, so the directory can")
	p("be copied anywhere; `manifest.json` is the machine-readable version of this page and `SHA256SUMS`")
	p("checksums every file in it.")
	p("")

	p("## The tenant")
	p("")
	p("| field | value |")
	p("|---|---|")
	p("| name | `%s` |", t.Name)
	p("| manifest name | `%s` |", t.ManifestName)
	p("| data dir | `%s` |", t.DataDir)
	p("| worktree | `%s` |", t.Worktree)
	p("| python env | `%s` |", t.PythonEnv)
	p("| code | `%s` (`%s`) |", t.Code.Tag, shortSHA(string(t.Code.SHA)))
	p("| artifact | `%s` |", orNone(info.ArtifactID))
	p("| ui mode | `%s` |", t.UI.Mode)
	p("")
	p("### Ports")
	p("")
	p("| leg | port |")
	p("|---|---|")
	p("| block base | %d (index %d) |", t.Ports.Base, t.Ports.Index)
	p("| api | %d |", t.Ports.API)
	p("| qdrant http / grpc | %d / %d |", t.Ports.QdrantHTTP, t.Ports.QdrantGRPC)
	p("| elasticsearch http / transport | %d / %d |", t.Ports.ESHTTP, t.Ports.ESTransport)
	p("| postgres | %d |", t.Ports.PG)
	p("")
	p("A restore into a FRESH tenant allocates a new block: these are the source's, recorded so that a")
	p("gateway row, a firewall rule or a bind that referred to them can be found again.")
	p("")

	p("## What the bundle holds")
	p("")
	p("| store | ownership | in the bundle |")
	p("|---|---|---|")
	p("| qdrant | %s | %s |", t.Stores.Qdrant.Ownership, listOrNone(info.Collections))
	p("| elasticsearch | %s | %s |", t.Stores.Elasticsearch.Ownership, listOrNone(info.Indices))
	p("| sqlite state | exclusive | %s |", listOrNone(info.SQLite))
	p("| postgres | %s | %s |", t.Stores.Postgres.Kind, orNone(info.Postgres))
	p("| neo4j | external | nothing — the graph store is not this tenant's |")
	p("")
	if info.SecretsIncluded {
		p("`secrets.age` holds this tenant's current AND historical secret files, encrypted to the backup")
		p("recipients. Decrypting it needs an age identity that is deliberately NOT kept on the host:")
		p("`age --decrypt -i <identity> secrets.age | tar -tv`.")
	} else {
		p("**No secrets are in this bundle.** No age recipient was configured when it was written, and the")
		p("ctl never writes credentials in the clear — so the API keys, the DSNs and the postgres password")
		p("of this tenant are NOT recoverable from here. A restore mints fresh credentials.")
	}
	p("")
	if len(info.External) > 0 {
		p("Not contained, and named so a recovery knows what is still missing:")
		p("")
		for _, e := range info.External {
			p("- %s", e)
		}
		p("")
	}

	p("## Restoring it")
	p("")
	p("```")
	p("ragstack-ctl backup verify %s %s        # checksums + manifest shape", t.Name, info.BundleID)
	p("ragstack-ctl tenant restore %s --from %s --as <fresh-name>", t.Name, info.BundleID)
	p("```")
	p("")
	p("`restore --as` is the only restore v1 performs: it builds a NEW tenant beside the old one from this")
	p("bundle's artifact, recovers every collection and index into it and verifies the counts against")
	p("`manifest.json`. It is also what proves the bundle — a bundle a restore has rebuilt is the only one")
	p("marked `verified`. There is no in-place restore in v1.")
	p("")
	p("On a host that does not have this bundle's tenant in its registry, copy the directory under")
	p("`<backups>/<tenant>/` first; the id is the directory name and `--from` takes the id, never a path.")
	return b.Bytes(), nil
}

func orNone(s string) string {
	if strings.TrimSpace(s) == "" {
		return "none"
	}
	return s
}

func listOrNone(items []string) string {
	if len(items) == 0 {
		return "nothing"
	}
	out := append([]string(nil), items...)
	sort.Strings(out)
	for i, v := range out {
		out[i] = "`" + v + "`"
	}
	return strings.Join(out, ", ")
}

func shortSHA(sha string) string {
	if len(sha) >= 12 {
		return sha[:12]
	}
	if sha == "" {
		return "no sha"
	}
	return sha
}
