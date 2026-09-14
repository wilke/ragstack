package ops

// create-sandbox — `create` onto a block the selftest owns.
//
// `ragstack-ctl selftest` has to make a real tenant: real directories, real
// units, a real qdrant and elasticsearch, a real ingest, a real backup and a
// real restore. What it must never do is take a production port block, advance
// the production allocator, or leave a row that looks to `decommission` like
// something worth protecting.
//
// The registry already knows how to keep the two apart — `registry.Allocate`
// skips the selftest range and its tombstones, `registry.AllocateSandbox`
// hands out the lowest free block in it, `paths.IsSelftestBlock` is the single
// question everything downstream asks. This file is the other half: a verb
// whose ONLY difference from `create` is which of those two allocators it
// calls, and a name grammar that makes the pairing unmistakable.
//
// Why a separate verb rather than `create --sandbox`: a flag is a value in a
// request, and a request comes from outside. The one thing standing between a
// selftest loop and a production block would then be a boolean somebody could
// pass by accident, in a retry, in a script, in a copied command line. With two
// verbs there is no such value: `create` refuses a `ctltest-` name,
// `create-sandbox` refuses every other one, and neither allocator can be
// reached by a request that meant the other.

import (
	"context"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// sandboxPrefix is the name every sandbox tenant carries. It is a convention
// for humans — the AUTHORITY is the port block — but it is a convention the
// two create verbs enforce on each other, so a row can never have one without
// the other.
const sandboxPrefix = "ctltest-"

// IsSandboxName reports whether name is spelled as a selftest sandbox. It is
// exported for the CLI, which asserts it before every op the selftest submits:
// the selftest may name no tenant but its own.
func IsSandboxName(name string) bool { return strings.HasPrefix(name, sandboxPrefix) }

func planCreateSandbox(_ context.Context, p *planner, args map[string]any) error {
	name := argStringOf(args, "name")
	// The args grammar (patSandboxName) has already refused anything else, so
	// this is unreachable from a validated request. It is stated anyway: the
	// grammar is one table away from this file, and the day somebody widens it
	// the allocator must still be the one thing that cannot be reached by the
	// wrong name.
	if !IsSandboxName(name) {
		return p.refuse("`create-sandbox` allocates out of the selftest range %d–%d and takes a `%s…` name only; "+
			"%q is a production tenant name — use `ragstack-ctl tenant create`",
			paths.SelftestBase, paths.SelftestEnd, sandboxPrefix, name)
	}
	return planCreateWith(p, args, registry.AllocateSandbox)
}
