package hostfacts

import (
	"os"
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
