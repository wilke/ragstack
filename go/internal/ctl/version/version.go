// Package version carries the build identity of ragstack-ctl. The three
// variables are set at link time by the Makefile's build-ctl target
// (-X github.com/ragstack/ragstack/internal/ctl/version.Version=…); a plain
// `go build` reports "dev".
package version

import "runtime"

// Set via -ldflags -X; see Makefile build-ctl.
var (
	Version = "dev"
	Commit  = ""
	BuiltAt = ""
)

// SchemaVersion is the registry.json schema this binary reads and writes.
// Kept here (not in registry) so `ragstack-ctl version` can report it without
// importing the registry package.
const SchemaVersion = 1

// BuildInfo is the payload of `ragstack-ctl version` and GET /v1/version.
type BuildInfo struct {
	Version       string `json:"version"`
	Commit        string `json:"commit"`
	BuiltAt       string `json:"built_at"`
	Go            string `json:"go"`
	SchemaVersion int    `json:"schema_version"`
}

// Info returns the build identity of the running binary.
func Info() BuildInfo {
	return BuildInfo{
		Version:       Version,
		Commit:        Commit,
		BuiltAt:       BuiltAt,
		Go:            runtime.Version(),
		SchemaVersion: SchemaVersion,
	}
}
