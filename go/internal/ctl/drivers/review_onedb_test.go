package drivers

// From the adversarial re-review of the handover fix, and KEPT: the
// one-database boundary used to be STATED and not DETECTED.
//
// `pg_dump -Fc -d <tenant>` moves one database. A second database in the same
// cluster, and any role created outside the tenant's own, stays in the
// pre-handover cluster and is not in the dump. addPGDump says so in a step
// warning and the runbook repeats it — but nothing asks postgres, so a tenant
// whose cluster someone did touch by hand hands over silently short, passes the
// row-count proof (which only ever looks at current_database()), soaks, commits,
// and then doctor invites the operator to `rm -rf` the only copy of the rest.
//
// It is now two more rows of the census, read in the same quiet moment it
// already reads pg_database_size, and the release REFUSES over anything beyond
// the tenant's own database and role unless `accept_extra_databases` says
// otherwise — which records what was left behind in the row.

import (
	"strings"
	"testing"
)

// TestTheCensusCanSeeASecondDatabaseInTheCluster. The release's census is the
// only thing that looks at the source cluster at all, and it is scoped to one
// database — so "there is nothing else here" is an assumption the handover
// makes and never checks.
func TestTheCensusCanSeeASecondDatabaseInTheCluster(t *testing.T) {
	if !strings.Contains(censusSQL, "pg_database") || !strings.Contains(censusSQL, "datname") {
		t.Errorf("the census never enumerates pg_database, so a handover cannot tell a cluster holding only the "+
			"tenant's database from one holding two. The dump carries one of them and the row-count proof, which "+
			"is scoped to current_database(), reports success either way.\ncensusSQL = %s", censusSQL)
	}
	if !strings.Contains(censusSQL, "pg_roles") {
		t.Errorf("the census never enumerates pg_roles: a login role created outside the tenant's own is in the " +
			"pre-handover cluster, is not in a single-database dump, and nothing reports it")
	}
}
