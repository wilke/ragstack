package main

// `fleet image prepare|list` — admitting a built API SERVER image into the
// ctl's image store (PR-F), and listing what is there.
//
// prepare is artifact prepare's twin: a job like any other, CLI-only (the
// daemon has no route for it, so --direct is implied and --server refused),
// because its argument is the PATH of a file the ctl will later run.

import (
	"flag"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func imageUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl fleet image prepare|list …

  prepare --sif PATH [--mirror DIR]
                  Admit a built server image (ragstack-server-<version>-b<N>.sif,
                  its receipt beside it as PATH.receipt.json) into the ctl's
                  image store: sha256(file) == receipt, labels == receipt
                  (role server), receipt.commit resolves in the mirror; then
                  copy both into <state>/images/server/ and record
                  server_images[<name>] in the registry.

                  CLI-only: the daemon exposes no route for it (--direct is
                  implied). The FIRST prepare is a one-way door — an older
                  ragstack-ctl cannot load a registry that carries
                  server_images; install and selftest this binary first.

  list [--json]   The prepared server images, newest first, and the tenants
                  running each.

%s
`, opFlagSummary)
	return exitUsage
}

func cmdFleetImage(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return imageUsage()
	}
	switch args[0] {
	case "prepare":
		return cmdImagePrepare(args[1:], registryPath, ragRoot, jsonOut)
	case "list":
		return cmdImageList(args[1:], registryPath, ragRoot, jsonOut)
	case "help", "-h", "--help":
		imageUsage()
		return exitOK
	default:
		return usageErr("fleet image: unknown verb %q (prepare|list)", args[0])
	}
}

func cmdImagePrepare(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet image prepare", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	sif := fs.String("sif", "", "the built server image (absolute path; its receipt beside it) (required)")
	mirror := fs.String("mirror", "", "the bare mirror the image's commit must resolve in (default $"+envMirror+" or <rag-root>/repos/ragstack.git)")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *sif == "" {
		return imageUsage()
	}
	if setFlags(fs)["server"] {
		fmt.Fprintf(stderr, "ragstack-ctl: `fleet image prepare` has no daemon route and --server cannot reach it: "+
			"it takes the path of an image the ctl will later run, which is a trusted-operator, CLI-only input. "+
			"Run it on the host without --server.\n")
		return exitUsage
	}
	*o.direct = true
	path := *sif
	if !filepath.IsAbs(path) {
		abs, err := filepath.Abs(path)
		if err != nil {
			return usageErr("--sif %q: %v", path, err)
		}
		path = abs
	}
	opArgs := map[string]any{"sif": filepath.Clean(path)}
	if *mirror != "" {
		opArgs["mirror"] = *mirror
	} else {
		opArgs["mirror"] = mirrorPath(*o.ragRoot)
	}
	return submitOp(o, opTarget{path: "/v1/fleet/server-images", op: "image-prepare"}, opArgs)
}

func cmdImageList(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet image list", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	f, err := loadForRead(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	names := make([]string, 0, len(f.ServerImages))
	for n := range f.ServerImages {
		names = append(names, n)
	}
	sort.Slice(names, func(i, j int) bool {
		a, b := f.ServerImages[names[i]], f.ServerImages[names[j]]
		if a.PreparedAt != b.PreparedAt {
			return a.PreparedAt > b.PreparedAt
		}
		return names[i] < names[j]
	})
	if *asJSON {
		out := make([]map[string]any, 0, len(names))
		for _, n := range names {
			im := f.ServerImages[n]
			out = append(out, map[string]any{
				"name": n, "version": im.Version, "commit": im.Commit, "build": im.Build, "sha256": im.SHA256,
				"path": im.Path, "prepared_at": im.PreparedAt, "prepared_by": im.PreparedBy,
				"tenants": imageTenants(f, n),
			})
		}
		return encode(out)
	}
	if len(names) == 0 {
		fmt.Fprintln(stdout, "no server images are prepared — `ragstack-ctl fleet image prepare --sif <path>`")
		return exitOK
	}
	fmt.Fprintf(stdout, "%-40s %-12s %-12s %-20s %s\n", "IMAGE", "VERSION", "COMMIT", "PREPARED", "TENANTS")
	for _, n := range names {
		im := f.ServerImages[n]
		commit := im.Commit
		if len(commit) > 12 {
			commit = commit[:12]
		}
		fmt.Fprintf(stdout, "%-40s %-12s %-12s %-20s %s\n", n, im.Version, commit, im.PreparedAt,
			orNone(strings.Join(imageTenants(f, n), ",")))
	}
	return exitOK
}

// imageTenants are the tenants whose API runs from the named image.
func imageTenants(f *registry.Fleet, name string) []string {
	var out []string
	for tn, t := range f.Tenants {
		if t.ServerImage != nil && t.ServerImage.Name == name {
			out = append(out, tn)
		}
	}
	sort.Strings(out)
	return out
}
