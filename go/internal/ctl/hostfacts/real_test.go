package hostfacts

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDeepestMount(t *testing.T) {
	mounts := map[string]string{"/": "ext4", "/rag": "xfs", "/data2": "xfs", "/data2/inner": "nfs"}
	for path, want := range map[string]string{
		"/data2/qdrant/dev":    "/data2",
		"/data2":               "/data2",
		"/data2x/qdrant":       "/",
		"/data2/inner/q":       "/data2/inner",
		"/rag/data/tenants/x":  "/rag",
		"/srv/qdrant":          "/",
		"/data2/../srv/qdrant": "/",
	} {
		got, _, err := deepestMount(path, mounts)
		if err != nil || got != want {
			t.Errorf("deepestMount(%q) = %q, %v; want %q", path, got, err, want)
		}
	}
	if _, _, err := deepestMount("rel/path", mounts); err == nil {
		t.Error("a relative path was accepted")
	}
}

// The real probe reads this process's own mountinfo; "/" always contains "/".
func TestRealMountOfRoot(t *testing.T) {
	if _, err := os.Stat("/proc/self/mountinfo"); err != nil {
		t.Skip("no /proc/self/mountinfo")
	}
	mp, _, err := (&Real{}).MountOf("/")
	if err != nil || mp != "/" {
		t.Fatalf("MountOf(/) = %q, %v", mp, err)
	}
}

// Review F3: a store root that is a symlink to the mounted volume answers the
// volume's mount, not "/" — the lexical answer would refuse every start of a
// qdrant moved there.
func TestMountOfResolvesASymlinkedPath(t *testing.T) {
	base := t.TempDir()
	vol := filepath.Join(base, "mnt", "vol")
	if err := os.MkdirAll(filepath.Join(vol, "tenants"), 0o755); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(base, "qdrant")
	if err := os.Symlink(vol, alias); err != nil {
		t.Fatal(err)
	}
	f := &Fake{Mounts: map[string]string{"/": "ext4", vol: "xfs"}}
	for _, p := range []string{filepath.Join(alias, "tenants", "dev"), filepath.Join(alias, "tenants")} {
		mp, fs, err := f.MountOf(p)
		if err != nil || mp != vol || fs != "xfs" {
			t.Errorf("MountOf(%s) = %s, %s, %v; want %s, xfs", p, mp, fs, err, vol)
		}
	}
	// A path nothing of which exists below / is answered lexically, as before.
	if mp, _, _ := f.MountOf("/no/such/qdrant/dev"); mp != "/" {
		t.Errorf("MountOf of a missing path = %s, want /", mp)
	}
}

// Second review item 1: the REAL MountOf resolves too. An alias in a temp dir
// pointing at /proc (a mount on every Linux host, and never the temp dir's)
// answers exactly what /proc answers — the lexical answer would be the temp
// dir's own mount.
func TestRealMountOfResolvesASymlinkedPath(t *testing.T) {
	r := &Real{ProcRoot: "/proc"}
	want, wantFS, err := r.MountOf("/proc/sys")
	if err != nil {
		t.Skipf("no readable mountinfo: %v", err)
	}
	if want != "/proc" {
		t.Skipf("/proc is not a mount point here (deepest mount of /proc/sys is %s)", want)
	}
	alias := filepath.Join(t.TempDir(), "proc-alias")
	if err := os.Symlink("/proc", alias); err != nil {
		t.Fatal(err)
	}
	got, gotFS, err := r.MountOf(filepath.Join(alias, "sys"))
	if err != nil || got != want || gotFS != wantFS {
		t.Errorf("MountOf(%s/sys) = %s, %s, %v; want %s, %s (what the resolved path answers)", alias, got, gotFS, err, want, wantFS)
	}
}
