package api

import "testing"

// TestSetHostToolsFromEnvIsTheOneSourceBothEntryPointsRead covers the helper
// `serve` and `--direct` share. Two entry points that read the CTL_* paths
// separately would build two different driver sets on the same host, and the
// operator debugging that would have no reason to suspect which process read
// which variable.
func TestSetHostToolsFromEnvIsTheOneSourceBothEntryPointsRead(t *testing.T) {
	t.Setenv(EnvSystemctlBin, "/bin/systemctl")
	t.Setenv(EnvGitBin, "/opt/git/bin/git")
	t.Setenv(EnvNodeBin, "/home/wilke/.local/bin/node")
	t.Setenv(EnvNpmBin, "  /home/wilke/.local/bin/npm  ") // whitespace is trimmed
	t.Setenv(EnvApptainerBin, "/usr/local/bin/apptainer")
	t.Setenv(EnvMirror, "/rag/repos/ragstack.git")
	t.Setenv(EnvNpmCache, "   ") // blank: says nothing

	var cfg EngineConfig
	cfg.NpmCache = "/already/set"
	SetHostToolsFromEnv(&cfg)

	if cfg.Systemctl != "/bin/systemctl" || cfg.Git != "/opt/git/bin/git" ||
		cfg.Node != "/home/wilke/.local/bin/node" || cfg.Npm != "/home/wilke/.local/bin/npm" ||
		cfg.Apptainer != "/usr/local/bin/apptainer" || cfg.Mirror != "/rag/repos/ragstack.git" {
		t.Errorf("the environment did not reach the config: %+v", cfg)
	}
	// A blank variable says nothing, so it must not erase a value the caller
	// already had.
	if cfg.NpmCache != "/already/set" {
		t.Errorf("a blank variable overwrote the configured value: %q", cfg.NpmCache)
	}
}
