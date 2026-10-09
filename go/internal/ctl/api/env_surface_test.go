package api

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// CTL_ALLOWED_ENDPOINT_HOSTS (#714): the environment, else ctl.env (a
// --direct run through ctl-as-svc.sh does not load that file), else the
// default; a malformed value refuses. Registered with the host-tool keys, so
// the Ansible reconciliation knows it.
func TestAllowedEndpointHostsResolution(t *testing.T) {
	dir := t.TempDir()
	cfg := EngineConfig{}
	cfg.Roots.CtlConfigDir = dir
	t.Setenv(EnvAllowedEndpointHosts, "")
	if err := SetAllowedEndpointHostsFromEnv(&cfg); err != nil {
		t.Fatal(err)
	}
	host, _ := os.Hostname()
	if len(cfg.AllowedEndpointHosts) < 3 || cfg.AllowedEndpointHosts[0] != "127.0.0.1" {
		t.Errorf("default = %v (host %s)", cfg.AllowedEndpointHosts, host)
	}
	if err := os.WriteFile(filepath.Join(dir, "ctl.env"),
		[]byte("CTL_LISTEN=127.0.0.1:23990\nCTL_ALLOWED_ENDPOINT_HOSTS=mango.cels.anl.gov,p3.theseed.org\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := SetAllowedEndpointHostsFromEnv(&cfg); err != nil ||
		!reflect.DeepEqual(cfg.AllowedEndpointHosts, []string{"mango.cels.anl.gov", "p3.theseed.org"}) {
		t.Errorf("from ctl.env = %v, %v", cfg.AllowedEndpointHosts, err)
	}
	t.Setenv(EnvAllowedEndpointHosts, "localhost")
	if err := SetAllowedEndpointHostsFromEnv(&cfg); err != nil || !reflect.DeepEqual(cfg.AllowedEndpointHosts, []string{"localhost"}) {
		t.Errorf("the environment must win: %v, %v", cfg.AllowedEndpointHosts, err)
	}
	t.Setenv(EnvAllowedEndpointHosts, "http://mango:8003")
	if err := SetAllowedEndpointHostsFromEnv(&cfg); err == nil {
		t.Error("a malformed allowlist was accepted")
	}
	found := false
	for _, k := range HostToolEnvKeys() {
		found = found || k == EnvAllowedEndpointHosts
	}
	if !found {
		t.Error("CTL_ALLOWED_ENDPOINT_HOSTS is not registered in HostToolEnvKeys")
	}
}
