package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"path/filepath"

	"github.com/ragstack/ragstack/internal/ctl/gowe"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// cmdGowe — `gowe render <tenant>`: what the tenant's API would register and
// whether the tools image each workflow names is the build its receipt
// describes (ADR-0010 decision 7, #655 step 4). Read-only: it reads the
// registry row, the tenant.env, the CWL files and the image store, and runs
// the tenant's own python against its own checkout. Exit 3 (refused) when
// any workflow's verdict has a problem — the same decision the boot makes —
// 1 when the check could not run, 2 on usage.
func cmdGowe(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 || args[0] != "render" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl gowe render <tenant> [--json] [--python PATH] [--worktree DIR] [--image-dirs A,B]")
		return exitUsage
	}
	fs := flag.NewFlagSet("gowe render", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	python := fs.String("python", "", "interpreter to run the check with (default <python_env>/bin/python from the row)")
	worktree := fs.String("worktree", "", "checkout to run the check from (default the row's worktree)")
	imageDirs := fs.String("image-dirs", "", "override GOWE_IMAGE_DIRS (comma-separated)")
	asJSON := fs.Bool("json", jsonOut, "the report as JSON")
	rest := args[1:]
	var name string
	if len(rest) > 0 && rest[0] != "" && rest[0][0] != '-' {
		name, rest = rest[0], rest[1:]
	}
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	if name == "" && fs.NArg() > 0 {
		name = fs.Arg(0)
	}
	if name == "" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl gowe render <tenant> [--json]")
		return exitUsage
	}
	if err := paths.ValidateName(name); err != nil {
		return fail(err)
	}
	f, err := registry.LoadNoRepair(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	t, ok := f.Tenants[name]
	if !ok {
		fmt.Fprintf(stderr, "ragstack-ctl: tenant %q is not in the registry\n", name)
		return exitError
	}
	// The row's data_dir is where this tenant's tenant.env lives; the roots
	// derivation is the fallback for a row adopted without one.
	tenantEnv := filepath.Join(t.DataDir, "config", "tenant.env")
	if t.DataDir == "" {
		roots := paths.NewRoots(*root, paths.Overrides{})
		tenantEnv = paths.TenantPaths(roots, t.Name, t.ManifestName).TenantEnv
	}
	if *worktree != "" {
		t.Worktree = *worktree
	}
	in, err := gowe.Resolve(t, tenantEnv)
	if err != nil {
		return fail(err)
	}
	if *python != "" {
		p, err := filepath.Abs(*python)
		if err != nil {
			return fail(err)
		}
		in.Python = p
	}
	if *imageDirs != "" {
		in.ImageDirs = *imageDirs
	}
	rep, err := gowe.Run(context.Background(), in, stderr)
	if err != nil {
		return fail(err)
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(rep); err != nil {
			return fail(err)
		}
	} else {
		gowe.Print(stdout, rep)
	}
	if !rep.OK {
		return exitRefused
	}
	return exitOK
}
