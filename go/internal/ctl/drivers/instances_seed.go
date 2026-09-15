package drivers

// RealInstances.SeedConfigDir, alone in its own file ON PURPOSE.
//
// It is the one jobs.Instances method the ops branch of PR-D2 added and the
// driver branch has not implemented, and keeping it here rather than beside
// the other RealInstances methods is a MERGE decision, not a design one: the
// two branches were written in parallel, and a stub in the middle of the file
// the driver agent rewrote is a conflict for everyone. A file nobody else
// touches is deleted in one move the day the real implementation lands.
//
// What it must do, when it is implemented (in instances.go, beside List, Run
// and Stop, and with this file removed):
//
//	<Bin> exec --bind <hostDir>:/__seed <sif> cp -R <containerDir>/. /__seed/
//
// — the same argv `ragstack-ctl es-seed-config` runs from the ES unit's
// ExecStartPre (cmd/ragstack-ctl/main.go, cmdESSeedConfig), which the instance
// supervisor has to run itself because it has no ExecStartPre. hostDir is
// under the driver's approved roots like every other bind it makes; the argv
// is fixed, so nothing a tenant's configuration can reach ends up on it.
//
// It is UNCONDITIONAL. "Seed only when the host directory is empty" is policy
// and lives in ops (ops/supervisor.go's seedESConfig, through Files.ReadDir),
// which is what keeps an operator's own elasticsearch.yml from being
// overwritten.

// The refusal is spelled out rather than built with this package's `pending`
// helper: whether that helper still exists depends on whether anything else is
// unwired, and this stub must not be the reason the package stops compiling.
import (
	"context"
	"fmt"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

func (i *RealInstances) SeedConfigDir(context.Context, string, string, string) error {
	return fmt.Errorf("%w: instances.SeedConfigDir lands in PR-D2 — until it does, an instance-supervised "+
		"tenant whose elasticsearch config bind is EMPTY cannot be started; seed it once by hand with "+
		"`ragstack-ctl es-seed-config <name>`", jobs.ErrRefused)
}
