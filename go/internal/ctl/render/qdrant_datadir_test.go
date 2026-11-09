package render

import (
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// stores.qdrant.data_dir moves the qdrant unit's binds, adds a second mount
// condition for the store root, and moves up.sh's binds through QDIR. The
// default-row goldens (units/*, up.sh) are untouched — TestUnitsGoldens and
// TestUpDownGoldens/parity_test.go hold them.
func TestQdrantDataDirUnitAndScripts(t *testing.T) {
	const qdir = "/data2/qdrant/sandbox"
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	tn.Stores.Qdrant.DataDir = qdir

	units, err := Units(tn, UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	q := units["ragstack-sandbox-qdrant.service"]
	golden(t, "units-qdrant-data-dir/ragstack-sandbox-qdrant.service", q)
	s := string(q)
	for _, w := range []string{
		"ConditionPathIsMountPoint=/rag\nConditionPathIsDirectory=" + qdir + "/storage\n",
		"--bind " + qdir + "/storage:/qdrant/storage",
		"--bind " + qdir + "/snapshots:/qdrant/snapshots",
		"StandardOutput=append:/rag/data/tenants/sandbox/logs/qdrant-sandbox.log",
	} {
		if !strings.Contains(s, w) {
			t.Errorf("qdrant unit lacks %q:\n%s", w, s)
		}
	}
	if strings.Contains(s, "/rag/data/tenants/sandbox/qdrant") {
		t.Errorf("qdrant unit still names the default qdrant dir:\n%s", s)
	}
	// Only the qdrant unit moves.
	def, err := Units(managedTenant("sandbox", 4, "enabled", registry.UIModeStatic), UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	for n, b := range units {
		if n != "ragstack-sandbox-qdrant.service" && string(b) != string(def[n]) {
			t.Errorf("%s changed with the qdrant data dir", n)
		}
	}
	argv, err := StoreArgv(tn, LegQdrant, UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if argv.Binds[0] != qdir+"/storage:/qdrant/storage" || argv.Binds[1] != qdir+"/snapshots:/qdrant/snapshots" {
		t.Errorf("StoreArgv binds = %v", argv.Binds)
	}

	up, err := UpSh(tn, StoreOptions{Images: "/rag/apptainer/images", ESHeap: "2g"})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "up.sh.qdrant-data-dir", up)
	for _, w := range []string{
		"TDIR=\"/rag/data/tenants/sandbox\"\nQDIR=\"" + qdir + "\"\n",
		`--bind "$QDIR/storage:/qdrant/storage"`,
		`--bind "$QDIR/snapshots:/qdrant/snapshots"`,
	} {
		if !strings.Contains(string(up), w) {
			t.Errorf("up.sh lacks %q", w)
		}
	}
	tn.Stores.Qdrant.DataDir = "/data2/$(id)"
	if _, err := UpSh(tn, StoreOptions{Images: "/rag/apptainer/images"}); err == nil {
		t.Error("a shell-expanding qdrant data_dir was rendered into up.sh")
	}
}

// PR1 emits no mount condition for the moved dir's filesystem: the store
// roots that would name it are not configured until PR2, and guessing one (the
// top-level directory) could condition a unit on a path that is not a mount
// point, so it would never start. ConditionPathIsDirectory on the moved
// storage/ is what refuses an unmounted store (doctor reports the mount:
// qdrant_data_dir_root_not_mounted).
func TestQdrantDataDirUnitHasOnlyTheRagMountCondition(t *testing.T) {
	for _, dir := range []string{"", "/rag/data/qdrant-stores/sandbox", "/data2/qdrant/sandbox", "/scratch/q"} {
		tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
		tn.Stores.Qdrant.DataDir = dir
		units, err := Units(tn, UnitConfig{})
		if err != nil {
			t.Fatal(err)
		}
		s := string(units["ragstack-sandbox-qdrant.service"])
		if n := strings.Count(s, "ConditionPathIsMountPoint="); n != 1 || !strings.Contains(s, "ConditionPathIsMountPoint=/rag\n") {
			t.Errorf("%q: %d mount conditions, want exactly ConditionPathIsMountPoint=/rag:\n%s", dir, n, s)
		}
	}
}
